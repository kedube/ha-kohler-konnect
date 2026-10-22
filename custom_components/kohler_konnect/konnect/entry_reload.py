"""Decide whether a config-entry update needs the integration reloaded.

Home Assistant fires an entry's update listener on every ``async_update_entry`` call, and
the running integration writes to its own entry: the refresh token (rotated on every
refresh, see ``konnect/auth.py``) and per-valve bookkeeping such as the remembered warm-up
mode. Reloading on those would rebuild every platform, flap entities to ``unavailable``,
drop the MQTT connection and reset the zone clocks. So the listener compares a signature
taken at setup against the live entry.

⚠️ The signature must be a frozen snapshot. The coordinator holds a reference to the same
``ConfigEntry`` Home Assistant mutates in place, so comparing live ``entry.data`` against
``coordinator.entry.data`` compares an object with itself and never reloads.

No Home Assistant imports, so the offline tests exercise this exact decision.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

__all__ = ["reload_signature"]


def _frozen(value: Any) -> Any:
    """Return a deep, immutable, comparable copy of a config value.

    Deep so that no later in-place edit of the entry can change the snapshot.
    """
    if isinstance(value, Mapping):
        # Sorted so insertion order doesn't matter. Keys are unique, so values are never
        # compared by the sort.
        return tuple(sorted((str(key), _frozen(val)) for key, val in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_frozen(item) for item in value))
    return value


def reload_signature(
    data: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    ignore_data: Iterable[str] = (),
    ignore_options: Iterable[str] = (),
) -> tuple[Any, ...]:
    """Fingerprint everything about an entry that, if changed, means "reload".

    ``ignore_data`` holds the keys the running integration writes to its own entry, and
    ``ignore_options`` the options it reads live rather than caching. Every other key
    counts, including keys added or removed, so a new setting reloads by default.
    Suppressing a reload must be an explicit entry in ``RELOAD_IGNORED_DATA_KEYS`` or
    ``RELOAD_IGNORED_OPTION_KEYS``.
    """
    skip_data = frozenset(ignore_data)
    skip_options = frozenset(ignore_options)
    return (
        tuple(
            sorted(
                (key, _frozen(value))
                for key, value in data.items()
                if key not in skip_data
            )
        ),
        tuple(
            sorted(
                (key, _frozen(value))
                for key, value in options.items()
                if key not in skip_options
            )
        ),
    )
