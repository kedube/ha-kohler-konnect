"""Detect how many outlets sit on each valve, without asking the user.

Two sources, both verified live, in preference order:

1. **The valve itself** — ``gcs-state/gcsadvancestate/{deviceId}`` returns a
   ``valveSettings[]`` array with ``noOfOutlets`` per valve. Authoritative, and needs no
   controller. Only available when the account has a GCS device.
2. **The controller** — ``hub-configuration/{deviceId}`` reports ``configuredoutlets`` per
   zone. The only option on a HUB-only account, which by definition has no valve id to
   query.

Either yields the split directly, so nothing has to be inferred from a flat outlet count or
from gaps in outlet ids. (Ids are not the dense ``0..N-1`` this once said: each zone's
valve reserves three ``outLetId`` slots, 0-2 and 3-5, whatever is fitted, so a K-28211
reports ``0, 1, 3, 4`` — see ``ValveModel.outlet_id`` and ``docs/protocol/gcs_valve.md``
§1.1.)

Detection can still fail — an unreachable device, an unexpected payload — so callers must
keep a manual fallback. **A count outside 0-3 is such a failure**, not something to clamp:
each zone's valve word has three outlet bits, so ``4`` or ``-1`` means the payload is not
what this reads it as, and any split built from it would be a guess. Rejecting it falls
back to the model the owner confirmed (or to asking them), which is the safer of the two
for a device that opens water. A clamped ``4 -> 3`` would build entities for a layout
nobody reported; taken as read, ``4`` made every write raise in ``encode_pair`` and ``-1``
cut a 3-outlet valve's outlet total to 2.
"""

from __future__ import annotations

import logging

from .valve_hex import OUTLETS_PER_VALVE

_LOGGER = logging.getLogger(__name__)

# valveSettings entries are named "Valve1", "Valve2", … in order.
_VALVE_PREFIX = "valve"


def _as_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _split(first: int, second: int) -> tuple[int, int] | None:
    """The detected split, or None when it is not one a valve can have."""
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

    ``setting`` is the ``setting`` object of a ``gcsadvancestate`` response. Its
    ``valveSettings`` array carries all eight valve slots; unpopulated ones report
    ``noOfOutlets`` of 0.

    The sibling ``outlets`` total is deliberately ignored — it was ``null`` on the tested
    system while the per-valve counts were correct.

    Anything that is not an object reads as no answer, like a missing array: the config
    flow and setup catch only the client's own errors, so an `AttributeError` here failed
    them both.
    """
    entries = setting.get("valveSettings") if isinstance(setting, dict) else None
    if not isinstance(entries, list):
        return None

    counts: dict[int, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("valve") or "").strip().lower()
        if not name.startswith(_VALVE_PREFIX):
            continue
        index = _as_int(name[len(_VALVE_PREFIX) :])
        if index in (1, 2):
            counts[index] = _as_int(entry.get("noOfOutlets"))

    return _split(counts.get(1, 0), counts.get(2, 0))


def topology_from_hub_configuration(configuration: dict) -> tuple[int, int] | None:
    """Read ``(zone1, zone2)`` outlet counts from a ``hub-configuration`` response.

    Used for HUB-only accounts, which have no valve to query directly.

    Reads ``configuredoutlets``, **not** ``parts.valve1``/``valve2``. Those count physical
    valve units, while the GCS API counts zones — so a single 6-outlet unit reports
    ``parts.valve2: NotConnected`` while genuinely having a populated zone 2.

    Type-checked the same way as the valve's read: a zone that is not an object counts as
    absent, and a configuration that is not one yields nothing.
    """
    config = configuration if isinstance(configuration, dict) else {}

    def configured(key: str) -> int:
        zone = config.get(key)
        return _as_int(zone.get("configuredoutlets")) if isinstance(zone, dict) else 0

    return _split(configured("zoneone"), configured("zonetwo"))


def describe(topology: tuple[int, int]) -> str:
    """A short human summary, e.g. ``"2 zones, 3 + 3 outlets"``."""
    first, second = topology
    if not second:
        return f"1 zone, {first} outlet{'s' if first != 1 else ''}"
    return f"2 zones, {first} + {second} outlets"
