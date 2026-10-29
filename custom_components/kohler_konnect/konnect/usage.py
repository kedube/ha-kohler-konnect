"""Water usage history, as Kohler's usage endpoints report it, for every product.

Valves read `gcs-usage`, faucets `faucet-usage`, toilets `numi-usage` or `blade-usage` and
EvoCycles `evo-usage` (see `KohlerClient.async_get_usage`). Each returns the same kind of
bucket list under its own name and field names, so this reads any of them into one shape:
`intervalKey`, `volume` in litres and `onDuration` in seconds. See
`docs/protocol/platform.md` §6.
"""

from __future__ import annotations

import math
from typing import Any

#: Litres to US gallons: the Konnect app's own constant for `Standard` accounts, so values
#: match its charts exactly. Both endpoints report litres whatever the account's units.
GALLONS_PER_LITRE = 0.264172

_VALVE_SERIES = "gcsUsageDataDetailsList"
_FAUCET_SERIES = "faucetUsageDataDetailsList"
# Both toilets' list; its buckets carry the faucet's field names (`numi_purewash.md` §6.2).
TOILET_SERIES = "numiUsageDataDetailsList"
_EVO_SERIES = "evoUsageDataDetailsList"


def is_metric(water_units: str | None) -> bool:
    """Whether an account's `waterUnits` shows litres rather than US gallons.

    The app shows gallons for `Standard` and litres otherwise; the other value is `Metric`.
    """
    return (water_units or "Standard").strip().casefold() != "standard"


def usage_volume_gallons(litres: float) -> float:
    """One usage volume in US gallons."""
    return litres * GALLONS_PER_LITRE


def _number(value: Any) -> float | None:
    # Finite only: Python's JSON parser accepts a bare `NaN` or `Infinity`, and one such
    # bucket would make every total built on it a value Home Assistant refuses.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _faucet_entry(entry: dict[str, Any], *, quantity: bool = True) -> dict[str, Any]:
    """A `faucet-usage` bucket in the valve's field names.

    Litres come from `waterUsage`, what the app charts, else `quantity`; the bucket's own
    `volume` is not read. `usageDuration` is seconds. A negative figure counts as none used.
    See `docs/protocol/sensate_faucet.md` §2.2, R6.
    """
    litres = _number(entry.get("waterUsage"))
    if litres is None and quantity:
        litres = _number(entry.get("quantity"))
    normalized: dict[str, Any] = {"intervalKey": entry.get("intervalKey")}
    if litres is not None:
        normalized["volume"] = max(0.0, litres)
    duration = _number(entry.get("usageDuration"))
    if duration is not None:
        normalized["onDuration"] = max(0.0, duration)
    return normalized


def _toilet_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """A toilet's bucket in the valve's field names.

    Litres come from `waterUsage` alone, the one figure the app charts for a toilet.
    `usageDuration` is read as the faucet's seconds, which no toilet screen shows to
    confirm (`numi_purewash.md` §6.2).
    """
    return _faucet_entry(entry, quantity=False)


def _evo_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """An `evo-usage` bucket in the valve's field names.

    Litres come from `waterUsage`, the fresh water the app's Water Usage screen charts, not
    `waterSave` or `totalWaterConsumption`; `duration` is seconds (`evocycle.md` §6.1, §6.2).
    """
    normalized: dict[str, Any] = {"intervalKey": entry.get("intervalKey")}
    litres = _number(entry.get("waterUsage"))
    if litres is not None:
        normalized["volume"] = max(0.0, litres)
    duration = _number(entry.get("duration"))
    if duration is not None:
        normalized["onDuration"] = max(0.0, duration)
    return normalized


def usage_series(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The per-period entries from a usage response, oldest first.

    Empty for a malformed or failed read, so "no data" and "the call did not work" read
    the same. A valve's `averageBlendTemperature` has no known unit
    (`docs/protocol/platform.md` §6), so nothing surfaces it.
    """
    if not isinstance(payload, dict):
        return []
    entries = payload.get(_VALVE_SERIES)
    if isinstance(entries, list):
        return [entry for entry in entries if isinstance(entry, dict)]
    for name, read in (
        (_FAUCET_SERIES, _faucet_entry),
        (TOILET_SERIES, _toilet_entry),
        (_EVO_SERIES, _evo_entry),
    ):
        entries = payload.get(name)
        if isinstance(entries, list):
            return [read(entry) for entry in entries if isinstance(entry, dict)]
    return []
