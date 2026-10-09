"""Faucet switch: the water, on and off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity

from .coordinator import FaucetCoordinator
from .entity import KohlerFaucetEntity


def faucet_switches(coordinator: FaucetCoordinator) -> list[SwitchEntity]:
    return [FaucetWaterSwitch(coordinator)]


class FaucetWaterSwitch(KohlerFaucetEntity, SwitchEntity):
    """On/off control for the faucet's water.

    Water turned on here is turned off again after the safety limit set in the integration
    options, unless something turns it off first.
    """

    _attr_translation_key = "water"

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "water")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.water_running

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_water(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_water(False)
