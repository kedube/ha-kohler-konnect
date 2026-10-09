"""The device-registry calls Home Assistant 2026.10 changed.

2026.10 stopped treating a device's identifier as unique across config entries, and
deprecated the two calls this integration made that assume it: looking a device up by
identifier with `async_get_device`, and naming a device's parent by identifier
(`via_device`). Both stop working in 2027.8. Their replacements are not in 2026.3, the
oldest version this integration supports, so each is used where Home Assistant has it and
the old call is kept only for the versions that do not.
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

    ``identifier`` is the parent's own identifier and ``registry_id`` its id in the device
    registry, once known. Where Home Assistant takes a registry id but this one is not
    known, the link is left out rather than made the deprecated way: the device still
    works, only its page loses the "connected via" line, and `via_device` will stop working.
    """
    if not VIA_DEVICE_ID:
        return DeviceInfo(via_device=identifier)
    if registry_id is None:
        return DeviceInfo()
    return DeviceInfo(via_device_id=registry_id)
