"""Device-registry calls that differ across supported Home Assistant versions.

From 2026.10 a device identifier is no longer unique across config entries, so looking a
device up by identifier (`async_get_device`) and naming a parent by identifier
(`via_device`) are deprecated, and stop working in 2027.8. A device also belongs to a single
config entry, so reading the set it belonged to (`config_entries`) is deprecated, until
2027.10. Their replacements are missing from 2026.3, the oldest supported version, so each
helper uses the new call where it exists and falls back to the old one otherwise.
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

__all__ = ["VIA_DEVICE_ID", "device_by_identifier", "device_entry_ids", "parent_link"]

#: 2026.10 and later name a device's parent by its registry id. Earlier versions take only
#: the parent's identifier, and refuse a device-info key they do not know. `DeviceInfo` is a
#: dataclass from 2026.11 and a TypedDict before, so its fields are read whichever it is.
VIA_DEVICE_ID = "via_device_id" in (
    getattr(DeviceInfo, "__dataclass_fields__", None) or DeviceInfo.__optional_keys__
)


def device_by_identifier(
    registry: dr.DeviceRegistry, identifier: tuple[str, str], config_entry_id: str
) -> dr.DeviceEntry | None:
    """The config entry's own device with ``identifier``, or None if it has none."""
    if hasattr(registry, "async_get_device_by_identifier"):
        return registry.async_get_device_by_identifier(identifier, config_entry_id)
    return registry.async_get_device(identifiers={identifier})


def device_entry_ids(device: dr.DeviceEntry) -> tuple[str, ...]:
    """The ids of the config entries ``device`` belongs to: none if it is orphaned."""
    if hasattr(device, "config_entry_id"):
        return () if device.config_entry_id is None else (device.config_entry_id,)
    return tuple(device.config_entries)


def parent_link(identifier: tuple[str, str], registry_id: str | None) -> dict[str, Any]:
    """The `DeviceInfo` keyword arguments that name a device's parent device.

    ``identifier`` is the parent's identifier and ``registry_id`` its device-registry id,
    if known. Where Home Assistant takes a registry id but none is known yet, the link is
    left out rather than made the deprecated way; the device only loses its "connected via"
    line. A plain dict, so it spreads into `DeviceInfo(...)` whether that is a TypedDict or
    a dataclass.
    """
    if not VIA_DEVICE_ID:
        return {"via_device": identifier}
    if registry_id is None:
        return {}
    return {"via_device_id": registry_id}
