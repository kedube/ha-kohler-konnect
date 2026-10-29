"""A Numi's or Purewash's entities, platform by platform (`product_platforms.py`).

Readings: who is seated, what runs, the seat heat, cleaning, errors, the day's counts and
water, the connection and firmware. Every control is an `UntestedControl`: the sprays and
dryer, flush, cover and seat ring, cleaning and the wand, seat heat, power save, the auto
settings, the wash, dryer and seat defaults, and the Numi's ambient light.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.update import UpdateEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from ..konnect.const import SKU_NUMI
from ..konnect.toilet import (
    AUTO_COVER_RANGES,
    COUNTS,
    FLOW_LEVELS,
    HEAT_LEVELS,
    PATTERNS,
    POSITIONS,
    THEMES,
    WASHES,
    day_bucket,
)
from ..product_base import ProductEntity, UntestedControl
from ..product_usage import usage_entities
from .coordinator import ToiletCoordinator

__all__ = ["toilet_entities"]

# The app's names for the heat levels, as option keys (§2.3).
_HEAT = ("off", "lowest", "low", "medium", "high", "highest")
_ACTIVITY = {
    "frontWash": "front_wash",
    "rearWash": "rear_wash",
    "boostWash": "boost_wash",
    "kidWash": "kid_wash",
    "dryer": "drying",
}


def _on(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() == "on"


class _ToiletEntity(ProductEntity):
    coordinator: ToiletCoordinator

    def __init__(self, coordinator: ToiletCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key


class _Control(UntestedControl, _ToiletEntity):
    """A control: available only while the app's own controls would be (§7.1)."""

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.controllable


# --- readings -------------------------------------------------------------------------


class ToiletReading(_ToiletEntity, BinarySensorEntity):
    """A yes-or-no reading from the state."""

    def __init__(
        self,
        coordinator: ToiletCoordinator,
        key: str,
        read: Callable[[ToiletCoordinator], bool | None],
        device_class: BinarySensorDeviceClass | None = None,
        *,
        diagnostic: bool = False,
    ) -> None:
        super().__init__(coordinator, key)
        self._read = read
        self._attr_device_class = device_class
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        return self._read(self.coordinator)


class ToiletCount(_ToiletEntity, SensorEntity):
    """Today's count of one thing from the daily usage series: flushes, washes, dryer runs.

    Each part that adds to it is an attribute: full and eco flushes, front and rear washes.
    """

    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator: ToiletCoordinator, what: str) -> None:
        super().__init__(coordinator, f"{what}_today")
        self._parts = COUNTS[what]
        if what == "power":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    def _part(self, field: str) -> float | int | None:
        bucket = day_bucket(
            self.coordinator.usage_daily, dt_util.now().date().isoformat()
        )
        if bucket is None:
            # No bucket for today: nothing counted yet, if the series was read at all.
            return 0 if self.coordinator.usage_daily else None
        value = bucket.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value) if float(value).is_integer() else value

    @property
    def native_value(self) -> float | int | None:
        parts = [self._part(field) for field in self._parts.values()]
        numbers = [part for part in parts if part is not None]
        return sum(numbers) if numbers else None

    @property
    def last_reset(self) -> datetime:
        return dt_util.start_of_local_day()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if len(self._parts) < 2:
            return None
        return {name: self._part(field) for name, field in self._parts.items()}


class ToiletActivity(_ToiletEntity, SensorEntity):
    """What runs: `idle`, a wash, or `drying`."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: ToiletCoordinator) -> None:
        super().__init__(coordinator, "activity")
        self._attr_options = ["idle", *(_ACTIVITY[key] for key in coordinator.starts)]

    @property
    def native_value(self) -> str:
        running = self.coordinator.activity
        return "idle" if running is None else _ACTIVITY[running]


class ToiletSeatHeat(_ToiletEntity, SensorEntity):
    """The seat heat, as the app's six levels."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: ToiletCoordinator) -> None:
        super().__init__(coordinator, "seat_heat")
        self._attr_options = list(_HEAT)

    @property
    def native_value(self) -> str | None:
        return _heat(self.coordinator.block("seat").get("temperature"))


class ToiletError(_ToiletEntity, SensorEntity):
    """The last error the seat reported, as its own text; empty once it says none."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ToiletCoordinator) -> None:
        super().__init__(coordinator, "last_error")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.state.get("error")


class ToiletFirmwareUpdate(_ToiletEntity, UpdateEntity):
    """Installed and latest firmware, from Kohler's check. Installing stays with the app."""

    def __init__(self, coordinator: ToiletCoordinator) -> None:
        super().__init__(coordinator, "firmware_status")

    @property
    def entity_picture(self) -> str | None:
        return None

    @property
    def installed_version(self) -> str | None:
        check = self.coordinator.firmware
        return check.current if check else None

    @property
    def latest_version(self) -> str | None:
        check = self.coordinator.firmware
        if check is not None and check.available and check.latest:
            return check.latest
        return self.installed_version


def _heat(value: Any) -> str | None:
    if value in ("Off", "OFF"):
        return "off"
    return _HEAT[HEAT_LEVELS.index(value)] if value in HEAT_LEVELS else None


# --- controls -------------------------------------------------------------------------


class ToiletButton(_Control, ButtonEntity):
    """One command, as the app sends it."""

    def __init__(
        self,
        coordinator: ToiletCoordinator,
        key: str,
        press: Callable[[ToiletCoordinator], Awaitable[None]],
    ) -> None:
        super().__init__(coordinator, key)
        self._press = press

    async def async_press(self) -> None:
        await self._press(self.coordinator)


class ToiletSwitch(_Control, SwitchEntity):
    """A setting that is on or off."""

    def __init__(
        self,
        coordinator: ToiletCoordinator,
        key: str,
        read: Callable[[ToiletCoordinator], bool],
        write: Callable[[ToiletCoordinator, bool], Awaitable[None]],
        *,
        config: bool = True,
    ) -> None:
        super().__init__(coordinator, key)
        self._read = read
        self._write = write
        if config:
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool:
        return self._read(self.coordinator)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(self.coordinator, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(self.coordinator, False)


class ToiletSelect(_Control, SelectEntity):
    """A setting chosen from a fixed list."""

    def __init__(
        self,
        coordinator: ToiletCoordinator,
        key: str,
        options: list[str],
        read: Callable[[ToiletCoordinator], str | None],
        write: Callable[[ToiletCoordinator, str], Awaitable[None]],
        *,
        config: bool = True,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_options = options
        self._read = read
        self._write = write
        if config:
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def current_option(self) -> str | None:
        return self._read(self.coordinator)

    async def async_select_option(self, option: str) -> None:
        await self._write(self.coordinator, option)


class ToiletAmbientLight(_Control, LightEntity):
    """The Numi's ambient light: on or off, its brightness, and its theme as an effect."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(self, coordinator: ToiletCoordinator) -> None:
        super().__init__(coordinator, "ambient_light")
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_effect_list = list(THEMES)
        self._themes = {code: name for name, code in THEMES.items()}

    @property
    def _ambient(self) -> dict[str, Any]:
        ambient = self.coordinator.block("light").get("ambientLight")
        return ambient if isinstance(ambient, dict) else {}

    @property
    def is_on(self) -> bool:
        return _on(self._ambient.get("state"))

    @property
    def brightness(self) -> int | None:
        try:
            level = int(str(self._ambient.get("brightness")))
        except ValueError:
            return None
        return round(level * 255 / 10) if 1 <= level <= 10 else None

    @property
    def effect(self) -> str | None:
        return self._themes.get(self._ambient.get("colorTheme"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        level = None if brightness is None else max(1, round(brightness * 10 / 255))
        effect = kwargs.get(ATTR_EFFECT)
        await self.coordinator.async_ambient_light(
            True, theme=None if effect is None else THEMES[effect], brightness=level
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_ambient_light(False)


def _clean(coordinator: ToiletCoordinator) -> dict[str, Any]:
    return coordinator.block("clean")


def _numi_settings(toilet: ToiletCoordinator) -> list[Entity]:
    def flag(field: str) -> Callable[[ToiletCoordinator, bool], Awaitable[None]]:
        return lambda t, on: t.async_configure({field: "true" if on else "false"})

    def auto_cover(t: ToiletCoordinator) -> str | None:
        auto_open = t.block("seat").get("autoOpen")
        if not isinstance(auto_open, dict):
            return None
        if not _on(auto_open.get("state")):
            return "off"
        distance = auto_open.get("sensorDistance")
        return {"RangeNear": "near", "RangeMid": "medium", "RangeFar": "far"}.get(
            distance
        )

    async def set_auto_cover(t: ToiletCoordinator, option: str) -> None:
        if option == "off":
            await t.async_configure(
                {"autoLidEnable": "false", "microWaveRange": "RangeMid"}
            )
            return
        distance = AUTO_COVER_RANGES[("near", "medium", "far").index(option)]
        await t.async_configure({"autoLidEnable": "true", "microWaveRange": distance})

    return [
        ToiletSwitch(
            toilet,
            "night_light",
            lambda t: _on(t.block("light").get("nightLight")),
            flag("nightLightEnable"),
        ),
        ToiletSwitch(
            toilet,
            "auto_flush",
            lambda t: _on(t.block("flush").get("auto")),
            flag("autoFlushEnable"),
        ),
        ToiletSwitch(
            toilet,
            "auto_clean",
            lambda t: _clean(t).get("sanitizer") == "SanitizeOn",
            flag("autoUVEnable"),
        ),
        ToiletSelect(
            toilet,
            "auto_cover",
            ["off", "near", "medium", "far"],
            auto_cover,
            set_auto_cover,
        ),
    ]


def _purewash_settings(toilet: ToiletCoordinator) -> list[Entity]:
    def flag(field: str) -> Callable[[ToiletCoordinator, bool], Awaitable[None]]:
        return lambda t, on: t.async_configure({field: "true" if on else "false"})

    def seat(field: str) -> Callable[[ToiletCoordinator], bool]:
        return lambda t: _on(t.block("seat").get(field))

    def night_light(t: ToiletCoordinator) -> str | None:
        light = t.block("light")
        if not _on(light.get("nightLight")):
            return "off"
        level = str(light.get("nightLightBrightness") or "").casefold()
        return level if level in ("low", "high") else None

    async def set_night_light(t: ToiletCoordinator, option: str) -> None:
        on = option != "off"
        await t.async_configure(
            {
                "nightLightEnable": "true" if on else "false",
                "nightLightBrightness": option if on else "high",
            }
        )

    return [
        ToiletSwitch(toilet, "auto_cover", seat("autoOpen"), flag("autoLidEnable")),
        ToiletSwitch(
            toilet,
            "deodorizer",
            lambda t: _clean(t).get("deo") == "AirCleanOn",
            flag("autoDeodorizeEnable"),
        ),
        ToiletSwitch(
            toilet,
            "bowl_mist",
            lambda t: _on(_clean(t).get("preRinse")),
            flag("waterBowlPremistEnable"),
        ),
        ToiletSwitch(
            toilet,
            "long_sitting_reminder",
            seat("longTimeSeatReminder"),
            flag("longTimeSeatReminderEnable"),
        ),
        ToiletSwitch(
            toilet,
            "auto_temperature",
            seat("autoWaterTemp"),
            flag("autoWaterTempEnable"),
        ),
        ToiletSwitch(
            toilet,
            "auto_clean",
            lambda t: _clean(t).get("sanitizer") == "SanitizeOn",
            flag("autoUVEnable"),
        ),
        ToiletSelect(
            toilet, "night_light", ["off", "low", "high"], night_light, set_night_light
        ),
    ]


def _buttons(toilet: ToiletCoordinator) -> list[Entity]:
    def start(key: str) -> Callable[[ToiletCoordinator], Awaitable[None]]:
        return lambda t: t.async_start(key)

    starts = {
        "frontWash": "start_front_wash",
        "rearWash": "start_rear_wash",
        "boostWash": "start_boost_wash",
        "kidWash": "start_kid_wash",
        "dryer": "start_dryer",
    }
    buttons: list[Entity] = [
        ToiletButton(toilet, "stop", lambda t: t.async_stop()),
        *(ToiletButton(toilet, starts[key], start(key)) for key in toilet.starts),
        ToiletButton(toilet, "cover", lambda t: t.async_move("lid")),
        ToiletButton(toilet, "seat_ring", lambda t: t.async_move("ring")),
        ToiletButton(toilet, "start_clean", lambda t: t.async_clean(True)),
        ToiletButton(toilet, "stop_clean", lambda t: t.async_clean(False)),
        ToiletButton(toilet, "extend_wand", lambda t: t.async_wand(True)),
        ToiletButton(toilet, "retract_wand", lambda t: t.async_wand(False)),
    ]
    if toilet.is_numi:
        buttons += [
            ToiletButton(toilet, "full_flush", lambda t: t.async_flush(True)),
            ToiletButton(toilet, "eco_flush", lambda t: t.async_flush(False)),
        ]
    return buttons


def _readings(toilet: ToiletCoordinator) -> list[Entity]:
    return [
        ToiletReading(
            toilet, "occupied", lambda t: t.seated, BinarySensorDeviceClass.OCCUPANCY
        ),
        ToiletReading(
            toilet,
            "cleaning",
            lambda t: t.block("cleanStatus").get("uvClean") == "SanitizeOn",
            BinarySensorDeviceClass.RUNNING,
        ),
        ToiletReading(
            toilet,
            "wand_extended",
            lambda t: _on(t.block("cleanStatus").get("manualClean")),
        ),
        ToiletReading(
            toilet,
            "connected",
            lambda t: t.online,
            BinarySensorDeviceClass.CONNECTIVITY,
            diagnostic=True,
        ),
        ToiletReading(
            toilet,
            "low_power",
            lambda t: (
                t.state.get("deviceState") in ("DeviceLowPower", "DeviceSleep")
                if "deviceState" in t.state
                else None
            ),
            diagnostic=True,
        ),
    ]


def _switches(toilet: ToiletCoordinator) -> list[Entity]:
    switches: list[Entity] = [
        ToiletSwitch(
            toilet,
            "power_save",
            lambda t: _on(t.block("seat").get("powerSave")),
            lambda t, on: t.async_power_save(on),
        )
    ]
    if toilet.is_numi:
        switches.append(
            ToiletSwitch(
                toilet,
                "circadian_light",
                lambda t: _on(t.block("light").get("circadianLight")),
                lambda t, on: t.async_circadian(on),
                config=False,
            )
        )
        return switches + [
            e for e in _numi_settings(toilet) if isinstance(e, SwitchEntity)
        ]
    return switches + [
        e for e in _purewash_settings(toilet) if isinstance(e, SwitchEntity)
    ]


def _selects(toilet: ToiletCoordinator) -> list[Entity]:
    async def set_heat(t: ToiletCoordinator, option: str) -> None:
        await t.async_heat_seat(HEAT_LEVELS[_HEAT.index(option)])

    heat = ToiletSelect(
        toilet,
        "seat_heat_level",
        list(_HEAT),
        lambda t: _heat(t.block("seat").get("temperature")),
        set_heat,
        config=False,
    )
    settings = _numi_settings(toilet) if toilet.is_numi else _purewash_settings(toilet)
    return [heat, *(e for e in settings if isinstance(e, SelectEntity))]


class ToiletSetting(_Control, NumberEntity):
    """A wash's, the dryer's or the seat's setting, on the app's dial: 0–5 or 1–5 (§2.3).

    While that wash or the dryer runs it shows and changes the live setting; otherwise the
    stored default, as the app's dial does. The seat's is always its default.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: ToiletCoordinator,
        key: str,
        part: str,
        field: str,
        scale: tuple[str, ...],
        write: Callable[[ToiletCoordinator, str], Awaitable[None]],
    ) -> None:
        super().__init__(coordinator, key)
        self._part = part
        # The state and the settings say `massage` where commands say `mode`.
        self._source = "massage" if field == "mode" else field
        self._scale = scale
        # Heat starts at 0, Off; pressure, fan speed and position at 1.
        self._first = 0 if scale is HEAT_LEVELS else 1
        self._attr_native_min_value = self._first
        self._attr_native_max_value = self._first + len(scale) - 1
        self._write = write

    @property
    def _wire(self) -> Any:
        toilet = self.coordinator
        live = self._part != "seat" and toilet.running(self._part)
        source = toilet.block(self._part) if live else toilet.settings.get(self._part)
        return source.get(self._source) if isinstance(source, Mapping) else None

    @property
    def native_value(self) -> int | None:
        wire = self._wire
        return self._first + self._scale.index(wire) if wire in self._scale else None

    async def async_set_native_value(self, value: float) -> None:
        await self._write(self.coordinator, self._scale[round(value) - self._first])


class ToiletPattern(_Control, SelectEntity):
    """A wash's spray pattern, live while it runs, else its default (§2.3, §3.3)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ToiletCoordinator, wash: str) -> None:
        super().__init__(coordinator, f"{_snake(wash)}_pattern")
        self._wash = wash
        self._attr_options = list(PATTERNS)

    @property
    def current_option(self) -> str | None:
        toilet = self.coordinator
        live = toilet.running(self._wash)
        source = toilet.block(self._wash) if live else toilet.settings.get(self._wash)
        wire = source.get("massage") if isinstance(source, Mapping) else None
        return next((name for name, code in PATTERNS.items() if code == wire), None)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_wash(self._wash, "mode", PATTERNS[option])


def _snake(key: str) -> str:
    """`frontWash` as `front_wash`."""
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in key)


def _settings(toilet: ToiletCoordinator) -> list[Entity]:
    """Each wash's temperature, pressure and position, the dryer's two and the seat's."""

    def wash(
        name: str, field: str
    ) -> Callable[[ToiletCoordinator, str], Awaitable[None]]:
        return lambda t, value: t.async_set_wash(name, field, value)

    def dryer(field: str) -> Callable[[ToiletCoordinator, str], Awaitable[None]]:
        return lambda t, value: t.async_set_dryer(field, value)

    numbers: list[Entity] = []
    for name in WASHES.get(toilet.sku, WASHES[SKU_NUMI]):
        for label, field, scale in (
            ("temperature", "temperature", HEAT_LEVELS),
            ("pressure", "flow", FLOW_LEVELS),
            ("position", "position", POSITIONS),
        ):
            numbers.append(
                ToiletSetting(
                    toilet,
                    f"{_snake(name)}_{label}",
                    name,
                    field,
                    scale,
                    wash(name, field),
                )
            )
    numbers += [
        ToiletSetting(
            toilet,
            "dryer_temperature",
            "dryer",
            "temperature",
            HEAT_LEVELS,
            dryer("temperature"),
        ),
        ToiletSetting(
            toilet, "dryer_fan_speed", "dryer", "flow", FLOW_LEVELS, dryer("flow")
        ),
        ToiletSetting(
            toilet,
            "default_seat_heat",
            "seat",
            "temperature",
            HEAT_LEVELS,
            lambda t, value: t.async_set_seat_default(value),
        ),
    ]
    return numbers


_BUILDERS: dict[str, Callable[[ToiletCoordinator], list[Entity]]] = {
    "binary_sensor": _readings,
    "button": _buttons,
    "light": lambda t: [ToiletAmbientLight(t)] if t.is_numi else [],
    "number": _settings,
    "select": lambda t: [
        *_selects(t),
        *(ToiletPattern(t, name) for name in WASHES.get(t.sku, WASHES[SKU_NUMI])),
    ],
    "sensor": lambda t: [
        ToiletActivity(t),
        ToiletSeatHeat(t),
        ToiletError(t),
        *(ToiletCount(t, what) for what in COUNTS),
        *usage_entities(t),
    ],
    "switch": _switches,
    "update": lambda t: [ToiletFirmwareUpdate(t)],
}


def toilet_entities(toilet: ToiletCoordinator, platform: str) -> list[Entity]:
    """One toilet's entities for one platform."""
    build = _BUILDERS.get(platform)
    return [] if build is None else build(toilet)
