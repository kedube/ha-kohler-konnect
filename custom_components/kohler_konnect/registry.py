"""Device-registry calls that differ across supported Home Assistant versions.

From 2026.10 a device identifier is no longer unique across config entries, so looking a
device up by identifier (`async_get_device`) and naming a parent by identifier
(`via_device`) are deprecated, and stop working in 2027.8. Their replacements are missing
from 2026.3, the oldest supported version, so each helper uses the new call where it exists
and falls back to the old one otherwise.
"""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

__all__ = ["VIA_DEVICE_ID", "device_by_identifier", "parent_link"]

#: 2026.10 and later name a device's parent by its registry id. Earlier versions take only
#: the parent's identifier, and refuse a device-info key they do not know.
VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__optional_keys__


def device_by_identifier(
    registry: dr.DeviceRegistry, identifier: tuple[str, str], config_entry_id: str
) -> dr.DeviceEntry | None:
    """The config entry's own device with ``identifier``, or None if it has none."""
    if hasattr(registry, "async_get_device_by_identifier"):
        return registry.async_get_device_by_identifier(identifier, config_entry_id)
    return registry.async_get_device(identifiers={identifier})


def parent_link(identifier: tuple[str, str], registry_id: str | None) -> DeviceInfo:
    """The part of a device's `DeviceInfo` that names its parent device.

    ``identifier`` is the parent's identifier and ``registry_id`` its device-registry id,
    if known. Where Home Assistant takes a registry id but none is known yet, the link is
    left out rather than made the deprecated way; the device only loses its "connected via"
    line.
    """
    if not VIA_DEVICE_ID:
        return DeviceInfo(via_device=identifier)
    if registry_id is None:
        return DeviceInfo()
    return DeviceInfo(via_device_id=registry_id)
