"""An Anthem valve's touch-panel settings, as the app's Settings → Control Panel shows them
(`docs/protocol/gcs_valve.md` §6).

Each control changes every panel at once, from a fresh read, as the app's Save does
(`Valve.async_write_ui_config`); all are untested, since no write has been seen on a valve.
Flow Control, which the app's current screens hide, starts off. Water-saving mode is a
reading: the app never changes it.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import Entity

from .coordinator import KohlerKonnectCoordinator, Valve
from .entity import KohlerValveEntity
from .konnect.ui_config import (
    BRIGHTNESS,
    PANEL_LANGUAGES,
    PANEL_UNITS,
    WAKE_SCREENS,
    brightness,
    flow_control,
    panel_language,
    panel_units,
    switch_on,
    wake_screen,
)
from .product_base import UntestedControl

__all__ = ["panel_entities"]

# Added to the wake screen when the second valve has outlets, as the app writes it.
_SECOND_VALVE = 192


class _PanelEntity(KohlerValveEntity):
    """One of the touch panels' settings, read from the first panel's record, which is
    the one the app's Control Panel shows.

    Named in English, as every valve entity is; the translation key labels its states.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, valve: Valve, key: str, name: str
    ) -> None:
        super().__init__(coordinator, valve)
        self._attr_name = name
        self._attr_translation_key = key
        self._attr_unique_id = f"{self._device_id}_{key}"

    @property
    def _record(self) -> dict[str, Any]:
        records = self._valve.gcs_state.ui_config
        return records[0] if records else {}

    @property
    def available(self) -> bool:
        return super().available and bool(self._valve.gcs_state.ui_config)


class PanelLanguageSelect(UntestedControl, _PanelEntity, SelectEntity):
    """ "Panel Language": a language with the region it is written with."""

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve, "valve_panel_language", "Panel Language")
        self._attr_options = list(PANEL_LANGUAGES)

    @property
    def current_option(self) -> str | None:
        return panel_language(self._record)

    async def async_select_option(self, option: str) -> None:
        language, region = PANEL_LANGUAGES[option]
        await self._valve.async_write_ui_config(
            {"language": language, "languageRegion": region}
        )


class PanelUnitsSelect(UntestedControl, _PanelEntity, SelectEntity):
    """ "Panel Units": temperature and flow units together."""

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve, "panel_units", "Panel Units")
        self._attr_options = list(PANEL_UNITS)

    @property
    def current_option(self) -> str | None:
        return panel_units(self._record)

    async def async_select_option(self, option: str) -> None:
        temperature, flow = PANEL_UNITS[option]
        await self._valve.async_write_ui_config(
            {"temperatureUnits": temperature, "flowUnits": flow}
        )


class WakeScreenSelect(UntestedControl, _PanelEntity, SelectEntity):
    """ "Wake Screen": what a panel shows when it wakes."""

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve, "wake_screen", "Wake Screen")
        self._attr_options = list(WAKE_SCREENS)

    @property
    def current_option(self) -> str | None:
        return wake_screen(self._record) if self._record else None

    async def async_select_option(self, option: str) -> None:
        second = _SECOND_VALVE if self._valve.model.outlets_valve2 > 0 else 0
        await self._valve.async_write_ui_config(
            {"defaultHomeScreen": str(WAKE_SCREENS[option] + second)}
        )


class PanelSwitch(UntestedControl, _PanelEntity, SwitchEntity):
    """ "Haptic Feedback" or "Accessible Display"."""

    def __init__(
        self,
        coordinator: KohlerKonnectCoordinator,
        valve: Valve,
        key: str,
        name: str,
        field: str,
    ) -> None:
        super().__init__(coordinator, valve, key, name)
        self._field = field

    @property
    def is_on(self) -> bool | None:
        return switch_on(self._record, self._field)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._valve.async_write_ui_config({self._field: "1"})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._valve.async_write_ui_config({self._field: "0"})


class FlowControlSwitch(UntestedControl, _PanelEntity, SwitchEntity):
    """ "Flow Control": whether the panel offers the flow on its main screen. The app's
    current screens hide this switch, so it starts off."""

    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve, "flow_control", "Flow Control")

    @property
    def is_on(self) -> bool | None:
        return flow_control(self._record)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._valve.async_write_ui_config({}, flow_control_on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._valve.async_write_ui_config({}, flow_control_on=False)


class PanelBrightnessNumber(UntestedControl, _PanelEntity, NumberEntity):
    """ "Display Brightness", 1 to 10 as the app's slider sets it."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value, _attr_native_max_value = BRIGHTNESS
    _attr_native_step = 1

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve, "display_brightness", "Display Brightness")

    @property
    def native_value(self) -> int | None:
        return brightness(self._record)

    async def async_set_native_value(self, value: float) -> None:
        await self._valve.async_write_ui_config({"backLight": str(round(value))})


class WaterSavingModeSensor(_PanelEntity, BinarySensorEntity):
    """The panels' low-flow mode, set at setup: one outlet at a time, and warm-up through
    the selected outlets. The app shows and keeps it, and never changes it."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve, "water_saving_mode", "Water-Saving Mode")

    @property
    def is_on(self) -> bool | None:
        return switch_on(self._record, "waterSavingMode")


def panel_entities(
    coordinator: KohlerKonnectCoordinator, valve: Valve, platform: str
) -> list[Entity]:
    """One valve's touch-panel entities for one platform."""
    if platform == "select":
        return [
            PanelLanguageSelect(coordinator, valve),
            PanelUnitsSelect(coordinator, valve),
            WakeScreenSelect(coordinator, valve),
        ]
    if platform == "switch":
        return [
            PanelSwitch(
                coordinator,
                valve,
                "haptic_feedback",
                "Haptic Feedback",
                "hapticFeedback",
            ),
            PanelSwitch(
                coordinator,
                valve,
                "accessible_display",
                "Accessible Display",
                "accessibilityMode",
            ),
            FlowControlSwitch(coordinator, valve),
        ]
    if platform == "number":
        return [PanelBrightnessNumber(coordinator, valve)]
    if platform == "binary_sensor":
        return [WaterSavingModeSensor(coordinator, valve)]
    return []
