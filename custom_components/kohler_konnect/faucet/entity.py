"""The base for every faucet entity."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from ..konnect.const import SKU_SENSATE
from .coordinator import FaucetCoordinator

#: The model shown on the device page. `SET` is almost certainly the Setra, but nothing in
#: Kohler's API or app says so outright, so it is not named as one.
_MODELS = {SKU_SENSATE: "Sensate"}


def as_text(value: Any, nested_key: str = "version") -> str | None:
    """``value`` as text; dicts like {"version": "16.0"} are unwrapped."""
    if isinstance(value, dict):
        value = value.get(nested_key)
    if value is None or value == "":
        return None
    return str(value)


def add_with_presets(
    coordinator: FaucetCoordinator, entry: ConfigEntry, add: Callable[[], None]
) -> None:
    """Call ``add`` once the faucet has a Konnect preset, now or later.

    Faucets without presets get no preset entities.
    """
    added = False

    @callback
    def _check() -> None:
        nonlocal added
        if not added and coordinator.presets:
            added = True
            add()

    _check()
    if not added:
        entry.async_on_unload(coordinator.async_add_listener(_check))


class KohlerFaucetEntity(CoordinatorEntity[FaucetCoordinator]):
    """Base entity: attaches every entity to its faucet's device."""

    _attr_has_entity_name = True
    # Live faucet data and commands are meaningless while Kohler reports the faucet
    # offline; cloud-side data (leak history, usage) and local settings are not.
    _requires_online = True

    def __init__(self, coordinator: FaucetCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_{key}"
        about = coordinator.about
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.device_id)},
            manufacturer="Kohler",
            model=_MODELS.get(coordinator.sku, "Konnect faucet"),
            model_id=as_text(about.get("model")) or coordinator.sku,
            name=coordinator.device_name,
            sw_version=as_text(about.get("firmware")),
            hw_version=as_text(about.get("hardware")),
            serial_number=as_text(about.get("serialNumber") or about.get("serial"))
            or coordinator.serial_number,
        )

    @property
    def available(self) -> bool:
        if self._requires_online and not self.coordinator.faucet_online:
            return False
        return super().available
