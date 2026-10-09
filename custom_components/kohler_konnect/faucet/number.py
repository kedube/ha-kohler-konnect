"""Faucet number: the free-choice dispense amount."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import EntityCategory

from ..const import DISPENSE_MAX_ML, DISPENSE_MIN_ML
from .coordinator import FaucetCoordinator
from .entity import KohlerFaucetEntity
from .units import HA_UNIT_KEYS, from_ml, to_ml


def faucet_numbers(coordinator: FaucetCoordinator) -> list[NumberEntity]:
    return [FaucetDispenseAmount(coordinator)]


class FaucetDispenseAmount(KohlerFaucetEntity, RestoreNumber):
    """How much the "Dispense set amount" button pours.

    Shown in mL or US fl oz, following the account's units; kept on the coordinator in mL,
    so a change of units keeps the same volume.
    """

    _requires_online = False
    _attr_translation_key = "dispense_amount"
    # A setting for the button, listed under Configuration on the device page.
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.VOLUME
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "dispense_amount")
        profile = coordinator.profile
        self._unit = profile.number_unit
        self._attr_native_unit_of_measurement = profile.number_native_unit
        self._attr_native_min_value = profile.number_min
        self._attr_native_max_value = profile.number_max
        self._attr_native_step = profile.number_step

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        data = await self.async_get_last_number_data()
        if data is None or data.native_value is None:
            return
        # The value may be in the other unit system's unit.
        unit = HA_UNIT_KEYS.get(str(data.native_unit_of_measurement), "ml")
        ml = to_ml(float(data.native_value), unit)
        self.coordinator.dispense_amount_ml = min(
            max(ml, DISPENSE_MIN_ML), DISPENSE_MAX_ML
        )

    @property
    def native_value(self) -> float:
        return round(from_ml(self.coordinator.dispense_amount_ml, self._unit), 2)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.dispense_amount_ml = to_ml(value, self._unit)
        self.async_write_ha_state()
