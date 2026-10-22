"""Detect how many outlets sit on each valve, without asking the user.

Two sources, in preference order:

1. The valve: ``gcs-state/gcsadvancestate/{deviceId}``, ``valveSettings[].noOfOutlets``.
   Needs no controller, only a GCS device on the account.
2. The controller: ``hub-configuration/{deviceId}``, ``configuredoutlets`` per zone.
   The only source on a HUB-only account.

Both give the split directly. It is never inferred from outlet ids, which are not dense
(``ValveModel.outlet_id``, ``docs/protocol/gcs_valve.md`` §1.1).

Detection can fail, so callers keep a manual fallback. Two answers are failures, never
clamped or defaulted, because a guessed split builds entities for a layout nobody
reported on a device that opens water:

* a count outside 0-3 (each zone's valve word has three outlet bits);
* a zone-2 count that is missing or not a whole number. Zone 2 is the one place 0 is a
  real answer (single zone), so reading garbage as 0 would turn a two-zone valve into a
  single-zone one: zone 2 could no longer be driven or stopped, and the reload would
  delete its sub-devices. A single-zone valve reports ``Valve2`` as 0 explicitly
  (``docs/protocol/gcs_valve.md`` §1); for the controller see
  ``docs/protocol/hub_controller.md`` §1.
"""

from __future__ import annotations

import logging

from .valve_hex import OUTLETS_PER_VALVE

_LOGGER = logging.getLogger(__name__)

# valveSettings entries are named "Valve1", "Valve2", … in order.
_VALVE_PREFIX = "valve"


def _count(value: object) -> int | None:
    """A reported outlet count, or None when the value is not one.

    Any whole-number spelling counts (``3``, ``"3"``, ``" 3 "``, ``"3.0"``). Anything
    else is None, never 0, which for zone 2 is a real answer.
    """
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return None
    # False for a fraction, and for infinity and NaN as well.
    return int(number) if number.is_integer() else None


def _split(first: int | None, second: int | None) -> tuple[int, int] | None:
    """The detected split, or None when it is not one a valve can have.

    A None count for either zone fails the whole detection.
    """
    if first is None or second is None:
        _LOGGER.debug(
            "Ignoring a detected split of %s + %s outlets: a zone's count is missing "
            "or not a whole number",
            first,
            second,
        )
        return None
    if first <= 0:
        return None
    if not (first <= OUTLETS_PER_VALVE and 0 <= second <= OUTLETS_PER_VALVE):
        _LOGGER.debug(
            "Ignoring a detected split of %s + %s outlets: each valve has 0-%s",
            first,
            second,
            OUTLETS_PER_VALVE,
        )
        return None
    return first, second


def topology_from_valve_settings(setting: dict) -> tuple[int, int] | None:
    """Read ``(outlets_valve1, outlets_valve2)`` from a valve settings block.

    ``setting`` is the ``setting`` object of a ``gcsadvancestate`` response. A missing
    or unreadable ``Valve2`` count is a failed read, not a single-zone valve. The
    sibling ``outlets`` total is ignored: it can be null while the per-valve counts are
    right.

    Never raises on a malformed payload (callers catch only the client's own errors);
    anything unexpected returns None.
    """
    entries = setting.get("valveSettings") if isinstance(setting, dict) else None
    if not isinstance(entries, list):
        return None

    counts: dict[int, int | None] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("valve") or "").strip().lower()
        if not name.startswith(_VALVE_PREFIX):
            continue
        try:
            index = int(name[len(_VALVE_PREFIX) :])
        except ValueError:
            continue
        if index in (1, 2):
            counts[index] = _count(entry.get("noOfOutlets"))

    return _split(counts.get(1), counts.get(2))


def topology_from_hub_configuration(configuration: dict) -> tuple[int, int] | None:
    """Read ``(zone1, zone2)`` outlet counts from a ``hub-configuration`` response.

    Used for HUB-only accounts, which have no valve to query directly. Reads
    ``configuredoutlets``, not ``parts.valve1``/``valve2``, which count valve bodies
    rather than zones (``docs/protocol/hub_controller.md`` §1). An absent or unreadable
    ``zonetwo`` returns None, like any malformed payload.
    """
    config = configuration if isinstance(configuration, dict) else {}

    def configured(key: str) -> int | None:
        zone = config.get(key)
        return _count(zone.get("configuredoutlets")) if isinstance(zone, dict) else None

    return _split(configured("zoneone"), configured("zonetwo"))


def describe(topology: tuple[int, int]) -> str:
    """A short human summary, e.g. ``"2 zones, 3 + 3 outlets"``."""
    first, second = topology
    if not second:
        return f"1 zone, {first} outlet{'s' if first != 1 else ''}"
    return f"2 zones, {first} + {second} outlets"
