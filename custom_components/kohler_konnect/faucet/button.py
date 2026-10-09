"""Faucet buttons: quick-dispense amounts, the set amount, the chosen preset, clear leak."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from ..const import DISPENSE_MAX_ML, DISPENSE_MIN_ML, DOMAIN
from .coordinator import FaucetCoordinator
from .entity import KohlerFaucetEntity, add_with_presets
from .units import ALL_QUICK_KEYS, QuickAmount


def faucet_buttons(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: FaucetCoordinator,
    add: Callable[[list[ButtonEntity]], None],
) -> list[ButtonEntity]:
    """Every button for one faucet; the preset button is added once a preset exists."""
    quick = coordinator.profile.quick_amounts
    # Drop the other unit system's quick buttons, left over from a change of the account's
    # units.
    keep = {f"{coordinator.device_id}_dispense_{q.key}" for q in quick}
    stale = {f"{coordinator.device_id}_dispense_{key}" for key in ALL_QUICK_KEYS} - keep
    ent_reg = er.async_get(hass)
    for row in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if row.domain == "button" and row.unique_id in stale:
            ent_reg.async_remove(row.entity_id)

    add_with_presets(
        coordinator, entry, lambda: add([FaucetDispensePresetButton(coordinator)])
    )
    return [
        *(FaucetQuickDispenseButton(coordinator, amount) for amount in quick),
        FaucetDispenseSetAmountButton(coordinator),
        FaucetClearLeakButton(coordinator),
    ]


class FaucetQuickDispenseButton(KohlerFaucetEntity, ButtonEntity):
    """Dispense a fixed amount with one tap."""

    def __init__(self, coordinator: FaucetCoordinator, amount: QuickAmount) -> None:
        super().__init__(coordinator, f"dispense_{amount.key}")
        self._ml = amount.ml
        self._attr_translation_key = amount.translation_key
        self._attr_translation_placeholders = amount.placeholders

    async def async_press(self) -> None:
        await self.coordinator.async_dispense(self._ml / 1000)


class FaucetDispenseSetAmountButton(KohlerFaucetEntity, ButtonEntity):
    """Dispense whatever amount the "Dispense amount" number is set to."""

    _attr_translation_key = "dispense_set_amount"

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "dispense_set_amount")

    async def async_press(self) -> None:
        await self.coordinator.async_dispense(
            self.coordinator.dispense_amount_ml / 1000
        )


class FaucetClearLeakButton(KohlerFaucetEntity, ButtonEntity):
    """Acknowledge the leak events Kohler currently reports."""

    _requires_online = False
    _attr_translation_key = "clear_leak"

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "clear_leak")

    async def async_press(self) -> None:
        await self.coordinator.async_clear_leaks()


class FaucetDispensePresetButton(KohlerFaucetEntity, ButtonEntity):
    """Dispense the preset chosen in the Preset select.

    Sends the verified dispense command with the preset's amount, not Kohler's preset
    command, which is only app-confirmed.
    """

    _attr_translation_key = "dispense_preset"

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "dispense_preset")

    @property
    def available(self) -> bool:
        return bool(self.coordinator.presets) and super().available

    async def async_press(self) -> None:
        if (preset := self.coordinator.chosen_preset) is None:
            return
        if not DISPENSE_MIN_ML <= preset.liters * 1000 <= DISPENSE_MAX_ML:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="preset_out_of_range",
                translation_placeholders={"title": preset.title},
            )
        await self.coordinator.async_dispense(preset.liters, preset.title)
