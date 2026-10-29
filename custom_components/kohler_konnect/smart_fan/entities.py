"""A Smart Fan's entities, platform by platform (`product_platforms.py` lists them).

Readings: whether the fan and light run, at what speed, the air, the connection and the
firmware. Every control is an `UntestedControl`: the fan and light themselves, Stop all,
and the stored defaults the app's Settings change. A default can't change while the part
it sets up runs, as in the app.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.update import UpdateEntity
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.helpers.entity import Entity

from ..konnect.smart_fan import (
    COLORS,
    DISABLE,
    ENABLE,
    KIND_FAN,
    KIND_LIGHT,
    REPEATS,
    SPEEDS,
    clock_time,
    schedule_days,
)
from ..product_base import ProductEntity, UntestedControl
from .coordinator import SmartFanCoordinator

__all__ = ["smart_fan_entities"]


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _level(value: Any) -> int | None:
    """A brightness or minutes figure, sent as a string; None for none or ``"0"``."""
    try:
        number = int(str(value).strip())
    except ValueError:
        return None
    return number or None


class _SmartFanEntity(ProductEntity):
    coordinator: SmartFanCoordinator

    def __init__(self, coordinator: SmartFanCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key


class _Control(UntestedControl, _SmartFanEntity):
    """A control: unavailable while Kohler says the fan is offline."""

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.online is not False


# --- readings -------------------------------------------------------------------------


class SmartFanFanState(_SmartFanEntity, SensorEntity):
    """Whether the fan runs, and at what speed: `off`, `low`, `medium`, `high`, or `on`."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: SmartFanCoordinator) -> None:
        super().__init__(coordinator, "fan_state")
        self._attr_options = ["off", *(speed.lower() for speed in SPEEDS), "on"]

    @property
    def native_value(self) -> str:
        if not self.coordinator.fan_running:
            return "off"
        speed = (_text(self.coordinator.state.get("fanSpeed")) or "").upper()
        return speed.lower() if speed in SPEEDS else "on"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        running = self.coordinator.running(KIND_FAN)
        return {"favorite": None if running is None else running.title}


class SmartFanLightState(_SmartFanEntity, BinarySensorEntity):
    """Whether the main light is on, with its brightness and color."""

    def __init__(self, coordinator: SmartFanCoordinator) -> None:
        super().__init__(coordinator, "light_state")

    @property
    def is_on(self) -> bool:
        return self.coordinator.light_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.coordinator.state
        color = (_text(state.get("lightColor")) or "").upper()
        running = self.coordinator.running(KIND_LIGHT)
        return {
            "brightness": _level(state.get("lightBrightnessLevel")),
            "color": color.lower() if color in COLORS else None,
            "favorite": None if running is None else running.title,
        }


class SmartFanConnected(_SmartFanEntity, BinarySensorEntity):
    """Whether Kohler's cloud can reach the fan."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SmartFanCoordinator) -> None:
        super().__init__(coordinator, "connected")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.online


class SmartFanFirmware(_SmartFanEntity, SensorEntity):
    """One part's firmware, as the app's About screen lists it."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SmartFanCoordinator, key: str, part: str) -> None:
        super().__init__(coordinator, key)
        self._part = part

    @property
    def native_value(self) -> str | None:
        block = self.coordinator.about.get(self._part)
        return _text(block.get("firmware")) if isinstance(block, dict) else None


class SmartFanLastUpdate(_SmartFanEntity, SensorEntity):
    """When the fan last reported something new."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SmartFanCoordinator) -> None:
        super().__init__(coordinator, "last_update")

    @property
    def native_value(self) -> datetime | None:
        reported = self.coordinator.last_report
        return None if reported is None else datetime.fromtimestamp(reported, tz=UTC)


class SmartFanAir(_SmartFanEntity, SensorEntity):
    """A reading the fan sends and the app never shows: its unit is not known yet, so it
    is off until an owner turns it on."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SmartFanCoordinator, key: str, field: str) -> None:
        super().__init__(coordinator, key)
        self._field = field

    @property
    def native_value(self) -> str | None:
        return _text(self.coordinator.state.get(self._field))


class SmartFanFirmwareUpdate(_SmartFanEntity, UpdateEntity):
    """Installed and latest firmware, from Kohler's check. Installing stays with the app."""

    def __init__(self, coordinator: SmartFanCoordinator) -> None:
        super().__init__(coordinator, "firmware_status")

    @property
    def entity_picture(self) -> str | None:
        return None

    @property
    def installed_version(self) -> str | None:
        firmware = self.coordinator.about.get("firmware")
        installed = (
            _text(firmware.get("version")) if isinstance(firmware, dict) else None
        )
        check = self.coordinator.firmware
        return installed or (check.current if check else None)

    @property
    def latest_version(self) -> str | None:
        check = self.coordinator.firmware
        if check is not None and check.available and check.latest:
            return check.latest
        return self.installed_version


# --- controls -------------------------------------------------------------------------


class SmartFanFan(_Control, FanEntity):
    """The fan: on as its stored defaults, or as one of its favorites (preset modes)."""

    def __init__(self, coordinator: SmartFanCoordinator) -> None:
        super().__init__(coordinator, "fan")

    @property
    def supported_features(self) -> FanEntityFeature:
        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if self.coordinator.favorites(KIND_FAN):
            features |= FanEntityFeature.PRESET_MODE
        return features

    @property
    def is_on(self) -> bool:
        return self.coordinator.fan_running

    @property
    def preset_modes(self) -> list[str] | None:
        return [f.title for f in self.coordinator.favorites(KIND_FAN)] or None

    @property
    def preset_mode(self) -> str | None:
        running = self.coordinator.running(KIND_FAN)
        return None if running is None else running.title

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        favorite = (
            None
            if preset_mode is None
            else self.coordinator.find_favorite(KIND_FAN, preset_mode)
        )
        await self.coordinator.async_turn_on(KIND_FAN, favorite)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.async_turn_on(preset_mode=preset_mode)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_off(KIND_FAN)


class SmartFanLight(_Control, LightEntity):
    """The main light: on as its stored defaults, or as one of its favorites (effects)."""

    _attr_color_mode = ColorMode.ONOFF

    def __init__(self, coordinator: SmartFanCoordinator) -> None:
        super().__init__(coordinator, "light")
        self._attr_supported_color_modes = {ColorMode.ONOFF}

    @property
    def supported_features(self) -> LightEntityFeature:
        if self.coordinator.favorites(KIND_LIGHT):
            return LightEntityFeature.EFFECT
        return LightEntityFeature(0)

    @property
    def is_on(self) -> bool:
        return self.coordinator.light_on

    @property
    def effect_list(self) -> list[str] | None:
        return [f.title for f in self.coordinator.favorites(KIND_LIGHT)] or None

    @property
    def effect(self) -> str | None:
        running = self.coordinator.running(KIND_LIGHT)
        return None if running is None else running.title

    async def async_turn_on(self, **kwargs: Any) -> None:
        effect = kwargs.get("effect")
        favorite = (
            None
            if effect is None
            else self.coordinator.find_favorite(KIND_LIGHT, effect)
        )
        await self.coordinator.async_turn_on(KIND_LIGHT, favorite)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_off(KIND_LIGHT)


class SmartFanNightLight(_Control, LightEntity):
    """The night light, on at its stored brightness.

    The app has the controls but never shows them, so a fan may well have no night light
    (§10 Q7): off until an owner turns it on. A configuration entity, so that turning on an
    area's lights doesn't send it.
    """

    _attr_color_mode = ColorMode.ONOFF
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SmartFanCoordinator) -> None:
        super().__init__(coordinator, "night_light")
        self._attr_supported_color_modes = {ColorMode.ONOFF}

    @property
    def is_on(self) -> bool:
        return self.coordinator.night_light_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "level": _level(self.coordinator.state.get("nightLightBrightnessLevel"))
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_night_light(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_night_light(False)


class SmartFanSyncClock(_Control, ButtonEntity):
    """Gives the fan Home Assistant's time zone, which the app sets only at setup."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SmartFanCoordinator) -> None:
        super().__init__(coordinator, "sync_clock")

    async def async_press(self) -> None:
        await self.coordinator.async_sync_clock()


class SmartFanStopAll(_Control, ButtonEntity):
    """Stops the fan and its lights, as the app's Stop all does."""

    def __init__(self, coordinator: SmartFanCoordinator) -> None:
        super().__init__(coordinator, "stop_all")

    async def async_press(self) -> None:
        await self.coordinator.async_stop_all()


class _Default(_Control):
    """One stored default, written with `updatedefaultsettings`.

    ``shown_with`` names the switch it belongs to: the app shows it only while that is
    on, so it is unavailable otherwise. ``hidden_with`` is the opposite. ``also`` is
    written with it, as the app's screen writes it.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: SmartFanCoordinator,
        key: str,
        field: str,
        blocked_by: str,
        *,
        shown_with: str | None = None,
        hidden_with: str | None = None,
        also: dict[str, str] | None = None,
    ) -> None:
        super().__init__(coordinator, key)
        self._field = field
        self._blocked_by = blocked_by
        self._shown_with = shown_with
        self._hidden_with = hidden_with
        self._also = also or {}

    def _enabled(self, field: str) -> bool:
        return str(self.coordinator.settings.get(field) or "").upper() == ENABLE

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self._shown_with is not None and not self._enabled(self._shown_with):
            return False
        return self._hidden_with is None or not self._enabled(self._hidden_with)

    @property
    def _stored(self) -> str | None:
        return _text(self.coordinator.settings.get(self._field))

    async def _async_write(self, value: str) -> None:
        await self.coordinator.async_set_defaults(
            {self._field: value, **self._also}, blocked_by=self._blocked_by
        )


class SmartFanDefaultSwitch(_Default, SwitchEntity):
    """A default that is `ENABLE` or `DISABLE`."""

    @property
    def is_on(self) -> bool | None:
        stored = (self._stored or "").upper()
        return None if stored not in (ENABLE, DISABLE) else stored == ENABLE

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(ENABLE)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(DISABLE)


class SmartFanSchedule(SmartFanDefaultSwitch):
    """The fan schedule, on or off, once one exists (§3.4): set one up with the
    `set_fan_schedule` action or in the app. What it is shows in its attributes."""

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.settings.get("fanScheduleCreated") is True
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        settings = self.coordinator.settings

        def clock(prefix: str) -> str | None:
            moment = clock_time(
                settings.get(f"{prefix}Hr"),
                settings.get(f"{prefix}Min"),
                settings.get(f"{prefix}AmPm"),
            )
            return None if moment is None else moment.strftime("%H:%M")

        setting = str(settings.get("fsSetting") or "").upper()
        repeat = next((name for name, sent in REPEATS.items() if sent == setting), None)
        speed = _text(settings.get("fsFanSpeed"))
        return {
            "start": clock("fsStartTime"),
            "end": clock("fsEndTime"),
            "repeat": repeat,
            "days": schedule_days(settings.get("fsDays"))
            if repeat == "custom"
            else None,
            "speed": None if speed is None else speed.lower(),
        }


class SmartFanDefaultSelect(_Default, SelectEntity):
    """A default chosen from a fixed list, sent upper-case as the app sends it."""

    def __init__(
        self,
        coordinator: SmartFanCoordinator,
        key: str,
        field: str,
        blocked_by: str,
        choices: tuple[str, ...],
        **dependence: str,
    ) -> None:
        super().__init__(coordinator, key, field, blocked_by, **dependence)
        self._attr_options = [choice.lower() for choice in choices]

    @property
    def current_option(self) -> str | None:
        stored = (self._stored or "").lower()
        return stored if stored in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        await self._async_write(option.upper())


class SmartFanDefaultNumber(_Default, NumberEntity):
    """A default set in steps of 10, as the app's pickers offer: a value between steps
    goes to the nearest."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 10

    def __init__(
        self,
        coordinator: SmartFanCoordinator,
        key: str,
        field: str,
        blocked_by: str,
        bounds: tuple[int, int],
        unit: str,
        *,
        enabled: bool = True,
        **dependence: str,
    ) -> None:
        super().__init__(coordinator, key, field, blocked_by, **dependence)
        self._attr_native_min_value, self._attr_native_max_value = bounds
        self._attr_native_unit_of_measurement = unit
        self._attr_entity_registry_enabled_default = enabled

    @property
    def native_value(self) -> int | None:
        return _level(self._stored)

    async def async_set_native_value(self, value: float) -> None:
        step = math.floor(value / 10 + 0.5) * 10
        low, high = self._attr_native_min_value, self._attr_native_max_value
        await self._async_write(str(int(min(max(step, low), high))))


def _numbers(fan: SmartFanCoordinator, light: bool) -> list[Entity]:
    numbers: list[Entity] = [
        SmartFanDefaultNumber(
            fan,
            "default_fan_run_time",
            "fanTime",
            "fan",
            (10, 60),
            UnitOfTime.MINUTES,
            shown_with="fanTimer",
        )
    ]
    if light:
        numbers += [
            SmartFanDefaultNumber(
                fan,
                "default_light_brightness",
                "lightBrightnessLevel",
                "light",
                (10, 100),
                PERCENTAGE,
            ),
            SmartFanDefaultNumber(
                fan,
                "default_night_light_brightness",
                "nightLightBrightness",
                "night_light",
                (10, 100),
                PERCENTAGE,
                # Off with the night light it sets up.
                enabled=False,
            ),
        ]
    return numbers


def _selects(fan: SmartFanCoordinator, light: bool) -> list[Entity]:
    selects: list[Entity] = [
        SmartFanDefaultSelect(fan, "default_fan_speed", "fanSpeed", "fan", SPEEDS),
        SmartFanDefaultSelect(
            fan,
            "humidity_sensitivity",
            "humidity",
            "fan",
            SPEEDS,
            shown_with="humidityThreshold",
        ),
    ]
    if light:
        selects.append(
            SmartFanDefaultSelect(
                fan,
                "default_light_color",
                "lightColor",
                "light",
                COLORS,
                hidden_with="circadianMode",
            )
        )
    return selects


def _switches(fan: SmartFanCoordinator, light: bool) -> list[Entity]:
    switches: list[Entity] = [
        # The app puts the run time back to 30 minutes whenever the timer is switched.
        SmartFanDefaultSwitch(
            fan, "default_fan_timer", "fanTimer", "fan", also={"fanTime": "30"}
        ),
        SmartFanDefaultSwitch(fan, "humidity_sensing", "humidityThreshold", "fan"),
        SmartFanDefaultSwitch(fan, "audible_feedback", "buzzerSetting", "any"),
        SmartFanSchedule(fan, "fan_schedule", "fsSchedule", "fan"),
    ]
    if light:
        switches.append(
            SmartFanDefaultSwitch(fan, "circadian_lighting", "circadianMode", "light")
        )
    return switches


def _sensors(fan: SmartFanCoordinator, light: bool) -> list[Entity]:
    return [
        SmartFanFanState(fan),
        SmartFanFirmware(fan, "gateway_firmware", "gateway"),
        SmartFanFirmware(fan, "controller_firmware", "sfc"),
        SmartFanLastUpdate(fan),
        SmartFanAir(fan, "air_temperature", "temperature"),
        SmartFanAir(fan, "air_humidity", "humidity"),
        SmartFanAir(fan, "co2", "co2value"),
        SmartFanAir(fan, "voc", "vocvalue"),
    ]


_BUILDERS: dict[str, Callable[[SmartFanCoordinator, bool], list[Entity]]] = {
    "binary_sensor": lambda fan, light: [
        SmartFanConnected(fan),
        *([SmartFanLightState(fan)] if light else []),
    ],
    "button": lambda fan, light: [SmartFanStopAll(fan), SmartFanSyncClock(fan)],
    "fan": lambda fan, light: [SmartFanFan(fan)],
    "light": lambda fan, light: (
        [SmartFanLight(fan), SmartFanNightLight(fan)] if light else []
    ),
    "number": _numbers,
    "select": _selects,
    "sensor": _sensors,
    "switch": _switches,
    "update": lambda fan, light: [SmartFanFirmwareUpdate(fan)],
}


def smart_fan_entities(fan: SmartFanCoordinator, platform: str) -> list[Entity]:
    """One fan's entities for one platform; its light's only if it has one."""
    build = _BUILDERS.get(platform)
    return [] if build is None else build(fan, fan.has_light)
