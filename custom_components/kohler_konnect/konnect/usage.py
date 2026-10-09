"""Water usage history, as Kohler's usage endpoints report it — for every product.

Valves read `gcs-usage` and faucets `faucet-usage`. The two take the same query (see
`KohlerClient.async_get_usage`) and return the same kind of bucket list under different
names and field names, so this reads either into one shape: `intervalKey`, `volume` in
litres and `onDuration` in seconds. The water sensors then work the same for a shower and a
faucet.
"""

from __future__ import annotations

from typing import Any

#: Litres to US gallons. Both endpoints report litres regardless of the account's unit
#: setting; the Konnect app multiplies by this exact constant when `waterUnits` is
#: `Standard`. Verified from the app's own bytecode, so it matches what the chart shows to
#: the last digit rather than being a rounder conversion of our own choosing.
GALLONS_PER_LITRE = 0.264172

_VALVE_SERIES = "gcsUsageDataDetailsList"
_FAUCET_SERIES = "faucetUsageDataDetailsList"


def is_metric(water_units: str | None) -> bool:
    """Whether an account's `waterUnits` shows litres rather than US gallons.

    The app converts to gallons when the value is `Standard` and shows litres otherwise.
    The other value is `Metric` — seen live on a Sensate account. Until the faucets merged
    in, the valve sensors tested for `Liters` instead, which no account sends, so a metric
    account was shown gallons.
    """
    return (water_units or "Standard").strip().casefold() != "standard"


def usage_volume_gallons(litres: float) -> float:
    """One usage volume in US gallons."""
    return litres * GALLONS_PER_LITRE


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _faucet_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """A `faucet-usage` bucket in the valve's field names.

    Litres are in `waterUsage` — what the app charts — with `quantity` as a stand-in; the
    bucket's own `volume` field is not something the app reads. `usageDuration` is seconds,
    which the app divides by 60 for its chart. A negative figure is read as none used.
    """
    litres = _number(entry.get("waterUsage"))
    if litres is None:
        litres = _number(entry.get("quantity"))
    normalized: dict[str, Any] = {"intervalKey": entry.get("intervalKey")}
    if litres is not None:
        normalized["volume"] = max(0.0, litres)
    duration = _number(entry.get("usageDuration"))
    if duration is not None:
        normalized["onDuration"] = duration
    return normalized


def usage_series(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The per-period entries from a usage response, oldest first.

    Empty for a malformed or failed read, so a caller can treat "no data" and "the call did
    not work" the same way — which is right for a diagnostic sensor.

    **For a valve, only `volume`, `onDuration` and `timestamp` are trustworthy.** The app
    never reads `averageBlendTemperature` or `numberOfTimesValveSwitchedOn` — they have no
    call sites in its bytecode at all — and the observed temperature values (~78 against real
    setpoints of 104.9 °F) do not correspond to any plausible unit, so nothing here surfaces
    them.
    """
    if not isinstance(payload, dict):
        return []
    entries = payload.get(_VALVE_SERIES)
    if isinstance(entries, list):
        return [entry for entry in entries if isinstance(entry, dict)]
    entries = payload.get(_FAUCET_SERIES)
    if isinstance(entries, list):
        return [_faucet_entry(entry) for entry in entries if isinstance(entry, dict)]
    return []
