"""Faucet select: the Konnect preset that "Dispense preset" pours."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from ..konnect import FaucetPreset
from .coordinator import FaucetCoordinator
from .entity import KohlerFaucetEntity, add_with_presets
from .units import from_ml


def add_faucet_selects(
    entry: ConfigEntry,
    coordinator: FaucetCoordinator,
    add: Callable[[list[SelectEntity]], None],
) -> None:
    """Add the Preset select once the faucet has a preset, now or later."""
    add_with_presets(coordinator, entry, lambda: add([FaucetPresetSelect(coordinator)]))


class FaucetPresetSelect(KohlerFaucetEntity, SelectEntity, RestoreEntity):
    """Which Konnect preset the "Dispense preset" button pours.

    One dropdown, in the app's order, however many presets there are. The choice is kept
    by preset rather than by name, so renaming a preset in the app keeps it chosen.
    """

    _attr_translation_key = "preset"
    # A setting for the button, listed under Configuration on the device page.
    _attr_entity_category = EntityCategory.CONFIG
    # A local choice from Kohler's cloud list; the faucet need not be online.
    _requires_online = False

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "preset")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.preset_choice is not None:
            return
        state = await self.async_get_last_state()
        if state is not None and (preset := self._labels.get(state.state)):
            self.coordinator.preset_choice = preset.preset_id

    @property
    def _labels(self) -> dict[str, FaucetPreset]:
        return self.coordinator.preset_options

    @property
    def available(self) -> bool:
        return bool(self.coordinator.presets) and super().available

    @property
    def options(self) -> list[str]:
        return list(self._labels)

    @property
    def current_option(self) -> str | None:
        chosen = self.coordinator.chosen_preset
        if chosen is None:
            return None
        return next(
            label
            for label, preset in self._labels.items()
            if preset.preset_id == chosen.preset_id
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if (preset := self.coordinator.chosen_preset) is None:
            return {}
        profile = self.coordinator.profile
        return {
            "amount": round(from_ml(preset.liters * 1000, profile.number_unit), 2),
            "unit": profile.number_native_unit,
        }

    async def async_select_option(self, option: str) -> None:
        self.coordinator.preset_choice = self._labels[option].preset_id
        self.async_write_ha_state()
