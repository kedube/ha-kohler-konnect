"""An EvoCycle's entities, platform by platform (`product_platforms.py`).

Readings: what runs, the last cleans, the water used and saved, faults, the connection
and firmware. Every control is an `UntestedControl`: the shower, the ozone cleans, the
stored defaults, the clean reminders, the interface settings and the water-savings goal.
All are on but the two ozone starts, which an owner turns on.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.update import UpdateEntity
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from ..konnect.evocycle import CLEANS, MODES, PANEL_LANGUAGES, PANEL_UNITS
from ..konnect.usage import is_metric, usage_volume_gallons
from ..product_base import ProductEntity, UntestedControl
from ..product_usage import usage_entities
from .coordinator import EvoCycleCoordinator

__all__ = ["evocycle_entities"]


class _EvoEntity(ProductEntity):
    coordinator: EvoCycleCoordinator

    def __init__(self, coordinator: EvoCycleCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key


class _Control(UntestedControl, _EvoEntity):
    """A control: unavailable while Kohler says the shower is offline."""

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.online is not False


# --- readings -------------------------------------------------------------------------


class EvoActivity(_EvoEntity, SensorEntity):
    """What runs: `idle`, a shower mode, an ozone clean, or a clean being cancelled."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: EvoCycleCoordinator) -> None:
        super().__init__(coordinator, "evo_activity")
        self._attr_options = ["idle", *MODES, *CLEANS, "cancelling"]

    @property
    def native_value(self) -> str:
        return self.coordinator.activity or "idle"


class EvoReading(_EvoEntity, BinarySensorEntity):
    def __init__(
        self,
        coordinator: EvoCycleCoordinator,
        key: str,
        read: Callable[[EvoCycleCoordinator], bool | None],
        device_class: BinarySensorDeviceClass,
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


class EvoProblem(_EvoEntity, BinarySensorEntity):
    """A fault the shower reports; its code is the attribute (§5.3)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: EvoCycleCoordinator) -> None:
        super().__init__(coordinator, "evo_problem")

    @property
    def is_on(self) -> bool:
        return self.coordinator.fault is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"error_code": self.coordinator.fault}


class EvoLastClean(_EvoEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: EvoCycleCoordinator, kind: str) -> None:
        super().__init__(coordinator, f"last_{kind}")
        self._kind = kind

    @property
    def native_value(self) -> datetime | None:
        seconds = self.coordinator.last_clean.get(self._kind)
        return None if not seconds else datetime.fromtimestamp(seconds, tz=UTC)


class EvoWaterSaved(_EvoEntity, SensorEntity):
    """Water recirculated rather than drawn fresh, since the shower was set up."""

    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: EvoCycleCoordinator) -> None:
        super().__init__(coordinator, "water_saved")
        self._metric = is_metric(coordinator.account.water_units)
        self._attr_native_unit_of_measurement = (
            UnitOfVolume.LITERS if self._metric else UnitOfVolume.GALLONS
        )

    @property
    def native_value(self) -> float | None:
        litres = (self.coordinator.data or {}).get("saved_litres")
        if isinstance(litres, bool) or not isinstance(litres, (int, float)):
            return None
        return round(litres if self._metric else usage_volume_gallons(litres), 1)


def _period_start(period: str, today: date) -> date:
    """The day a Water Savings period starts: Sunday, the 1st, or New Year's Day."""
    if period == "week":
        return today - timedelta(days=(today.weekday() + 1) % 7)
    if period == "month":
        return today.replace(day=1)
    return today.replace(month=1, day=1)


class EvoSavings(_EvoEntity, SensorEntity):
    """One figure from the app's Water Savings screen (§6.3): water in the account's
    unit, or a share in percent. Read with the usage, every half hour."""

    def __init__(
        self,
        coordinator: EvoCycleCoordinator,
        key: str,
        read: Callable[[EvoCycleCoordinator], float | None],
        period: str,
        *,
        share: bool = False,
    ) -> None:
        super().__init__(coordinator, key)
        self._read = read
        self._period = period
        self._share = share
        self._metric = is_metric(coordinator.account.water_units)
        if share:
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        else:
            self._attr_device_class = SensorDeviceClass.WATER
            self._attr_state_class = SensorStateClass.TOTAL
            self._attr_native_unit_of_measurement = (
                UnitOfVolume.LITERS if self._metric else UnitOfVolume.GALLONS
            )

    @property
    def native_value(self) -> float | None:
        value = self._read(self.coordinator)
        if value is None:
            return None
        if not self._share and not self._metric:
            value = usage_volume_gallons(value)
        return round(value, 1)

    @property
    def last_reset(self) -> datetime | None:
        if self._share:
            return None
        return dt_util.start_of_local_day(
            _period_start(self._period, dt_util.now().date())
        )


def _this_month(evo: EvoCycleCoordinator) -> dict[str, float | None]:
    months = evo.savings.get("months") or {}
    return months.get(dt_util.now().strftime("%Y-%m")) or {}


def _year_saved(evo: EvoCycleCoordinator) -> float | None:
    year = dt_util.now().strftime("%Y-")
    saved = [
        month["saved"]
        for key, month in (evo.savings.get("months") or {}).items()
        if key.startswith(year) and month.get("saved") is not None
    ]
    return sum(saved) if saved else None


def _week_share(evo: EvoCycleCoordinator) -> float | None:
    """This week's cycled water as a share of all it used, computed from the litres:
    the app's own donut divides whole numbers and reads 0 % (§6.3)."""
    cycled, fresh = evo.savings.get("cycled"), evo.savings.get("fresh")
    if cycled is None or fresh is None or cycled + fresh <= 0:
        return None
    return cycled / (cycled + fresh) * 100


def _savings(evo: EvoCycleCoordinator) -> list[Entity]:
    return [
        EvoSavings(
            evo, "cycled_water_this_week", lambda c: c.savings.get("cycled"), "week"
        ),
        EvoSavings(
            evo, "fresh_water_this_week", lambda c: c.savings.get("fresh"), "week"
        ),
        EvoSavings(evo, "cycled_share_this_week", _week_share, "week", share=True),
        EvoSavings(
            evo,
            "water_saved_this_month",
            lambda c: _this_month(c).get("saved"),
            "month",
        ),
        EvoSavings(evo, "water_saved_this_year", _year_saved, "year"),
        EvoSavings(
            evo,
            "cycle_share_this_month",
            lambda c: _this_month(c).get("cycle_share"),
            "month",
            share=True,
        ),
    ]


class EvoFirmwareUpdate(_EvoEntity, UpdateEntity):
    def __init__(self, coordinator: EvoCycleCoordinator) -> None:
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


# --- controls -------------------------------------------------------------------------


class EvoShower(_Control, SelectEntity):
    """The shower: off, or running in Standard or Cycle mode."""

    def __init__(self, coordinator: EvoCycleCoordinator) -> None:
        super().__init__(coordinator, "evo_shower")
        self._attr_options = ["off", *MODES]

    @property
    def current_option(self) -> str:
        running = self.coordinator.activity
        return running if running in MODES else "off"

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_shower(None if option == "off" else option)


class EvoButton(_Control, ButtonEntity):
    def __init__(
        self,
        coordinator: EvoCycleCoordinator,
        key: str,
        press: Callable[[EvoCycleCoordinator], Awaitable[None]],
        *,
        enabled: bool = True,
    ) -> None:
        super().__init__(coordinator, key)
        self._press = press
        self._attr_entity_registry_enabled_default = enabled

    async def async_press(self) -> None:
        await self._press(self.coordinator)


class EvoDefaultSelect(_Control, SelectEntity):
    """The mode Simple Start runs."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EvoCycleCoordinator) -> None:
        super().__init__(coordinator, "default_shower_mode")
        self._attr_options = list(MODES)

    @property
    def current_option(self) -> str | None:
        stored = self.coordinator.shower_configuration.get("defaultShowerMode")
        return next((name for name, number in MODES.items() if number == stored), None)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_default("showerMode", MODES[option])


def _snapped(value: float, step: float, low: float, high: float) -> float:
    """``value`` on the app's steps, the nearest, within its range."""
    return min(max(math.floor(value / step + 0.5) * step, low), high)


class EvoDefaultNumber(_Control, NumberEntity):
    """A stored default: a temperature, kept in tenths of °C, or the flow percent, in the
    app's steps. The default temperature stops at the lower of the two maximums."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: EvoCycleCoordinator,
        key: str,
        field: str,
        source: str,
        bounds: tuple[float, float],
        *,
        temperature: bool,
        capped: bool = False,
    ) -> None:
        super().__init__(coordinator, key)
        self._field = field
        self._source = source
        self._tenths = temperature
        self._capped = capped
        self._attr_native_min_value, self._attr_native_max_value = bounds
        if temperature:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_native_step = 0.5
        else:
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_native_step = 4

    @property
    def native_max_value(self) -> float:
        highest = self._attr_native_max_value
        if self._capped:
            return min(highest, self.coordinator.temperature_ceiling / 10)
        return highest

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.shower_configuration.get(self._source)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value / 10 if self._tenths else value

    async def async_set_native_value(self, value: float) -> None:
        value = _snapped(
            value, self.native_step, self.native_min_value, self.native_max_value
        )
        stored = round(value * 10) if self._tenths else round(value)
        await self.coordinator.async_set_default(self._field, stored)


class EvoSwitch(_Control, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: EvoCycleCoordinator,
        key: str,
        read: Callable[[EvoCycleCoordinator], Any],
        write: Callable[[EvoCycleCoordinator, bool], Awaitable[None]],
    ) -> None:
        super().__init__(coordinator, key)
        self._read = read
        self._write = write

    @property
    def is_on(self) -> bool:
        return self._read(self.coordinator) != 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(self.coordinator, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(self.coordinator, False)


class EvoPanelLanguage(_Control, SelectEntity):
    """The language on the shower's digital interface (§3.7)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EvoCycleCoordinator) -> None:
        super().__init__(coordinator, "panel_language")
        self._attr_options = list(PANEL_LANGUAGES)

    @property
    def current_option(self) -> str | None:
        language = self.coordinator.interface.get("language")
        return language if language in PANEL_LANGUAGES else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_interface(language=option)


class EvoPanelUnits(_Control, SelectEntity):
    """The units the digital interface shows: US gallons and °F, or litres and °C."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EvoCycleCoordinator) -> None:
        super().__init__(coordinator, "panel_units")
        self._attr_options = list(PANEL_UNITS)

    @property
    def current_option(self) -> str | None:
        units = _whole(self.coordinator.interface.get("temperatureUnits"))
        return next((name for name, n in PANEL_UNITS.items() if n == units), None)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_interface(units=PANEL_UNITS[option])


class EvoHaptics(_Control, SwitchEntity):
    """Whether the digital interface answers a touch with a buzz; unknown until read."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EvoCycleCoordinator) -> None:
        super().__init__(coordinator, "haptic_feedback")

    @property
    def is_on(self) -> bool | None:
        haptics = _whole(self.coordinator.interface.get("hapticFeedback"))
        return None if haptics is None else haptics != 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_interface(hapticFeedback=1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_interface(hapticFeedback=0)


class EvoGoal(_Control, SwitchEntity):
    """Whether the monthly water-savings goal is set and shown (§6.4)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EvoCycleCoordinator) -> None:
        super().__init__(coordinator, "water_saving_goal")

    @property
    def is_on(self) -> bool | None:
        enabled = self.coordinator.goal.get("isEnableWaterSave")
        return enabled if isinstance(enabled, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_goal(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_goal(enabled=False)


class EvoGoalTarget(_Control, NumberEntity):
    """The goal itself, a percent in tens as the app's slider sets it (§3.8)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 10
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: EvoCycleCoordinator) -> None:
        super().__init__(coordinator, "water_saving_target")

    @property
    def available(self) -> bool:
        """Only while the goal is on, as the app shows the slider."""
        return (
            super().available and self.coordinator.goal.get("isEnableWaterSave") is True
        )

    @property
    def native_value(self) -> int | None:
        return _whole(self.coordinator.goal.get("goalPercentage"))

    async def async_set_native_value(self, value: float) -> None:
        percent = _snapped(value, 10, 10, 100)
        await self.coordinator.async_set_goal(percent=round(percent))


def _whole(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def _numbers(evo: EvoCycleCoordinator) -> list[Entity]:
    return [
        EvoDefaultNumber(
            evo,
            "default_temperature",
            "temperature",
            "defaultTemperature",
            (15, 48),
            temperature=True,
            capped=True,
        ),
        EvoDefaultNumber(
            evo,
            "standard_max_temperature",
            "standardModeMaxTemperature",
            "defaultStandardMaxTemperature",
            (15, 48),
            temperature=True,
        ),
        EvoDefaultNumber(
            evo,
            "cycle_max_temperature",
            "cycleModeMaxTemperature",
            "defaultCycleMaxTemperature",
            (15, 43),
            temperature=True,
        ),
        EvoDefaultNumber(
            evo, "default_flow", "flow", "defaultFlow", (4, 100), temperature=False
        ),
        EvoGoalTarget(evo),
    ]


def _switches(evo: EvoCycleCoordinator) -> list[Entity]:
    def reminder(key: str, field: str, source: str) -> EvoSwitch:
        return EvoSwitch(
            evo,
            key,
            lambda e: e.clean_configuration.get(source),
            lambda e, on: e.async_set_reminder(field, on),
        )

    return [
        EvoSwitch(
            evo,
            "warmup",
            lambda e: e.shower_configuration.get("defaultWarmupMode"),
            lambda e, on: e.async_set_default("warmupMode", 1 if on else 0),
        ),
        reminder("rapid_clean_reminder", "rapidCleanEnable", "enableRapidClean"),
        reminder("deep_clean_reminder", "deepCleanEnable", "enableDeepClean"),
        reminder(
            "filter_clean_reminder",
            "showerFilterCleanEnable",
            "showerFilterCleanEnable",
        ),
        EvoHaptics(evo),
        EvoGoal(evo),
    ]


_BUILDERS: dict[str, Callable[[EvoCycleCoordinator], list[Entity]]] = {
    "binary_sensor": lambda e: [
        EvoReading(
            e,
            "shower_running",
            lambda c: c.activity in MODES,
            BinarySensorDeviceClass.RUNNING,
        ),
        EvoReading(
            e,
            "ozone_cleaning",
            lambda c: c.activity in (*CLEANS, "cancelling"),
            BinarySensorDeviceClass.RUNNING,
        ),
        EvoProblem(e),
        EvoReading(
            e,
            "connected",
            lambda c: c.online,
            BinarySensorDeviceClass.CONNECTIVITY,
            diagnostic=True,
        ),
    ],
    # The ozone starts are off until an owner turns them on: the app asks "Please stay
    # out of shower!" first, and a button here can't ask. A stop is always safe.
    "button": lambda e: [
        EvoButton(
            e,
            "start_rapid_clean",
            lambda c: c.async_clean("rapid_clean"),
            enabled=False,
        ),
        EvoButton(
            e, "start_deep_clean", lambda c: c.async_clean("deep_clean"), enabled=False
        ),
        EvoButton(e, "stop_clean", lambda c: c.async_clean(None)),
    ],
    "number": _numbers,
    "select": lambda e: [
        EvoShower(e),
        EvoDefaultSelect(e),
        EvoPanelLanguage(e),
        EvoPanelUnits(e),
    ],
    "sensor": lambda e: [
        EvoActivity(e),
        EvoLastClean(e, "rapid_clean"),
        EvoLastClean(e, "deep_clean"),
        EvoWaterSaved(e),
        *_savings(e),
        *usage_entities(e),
    ],
    "switch": _switches,
    "update": lambda e: [EvoFirmwareUpdate(e)],
}


def evocycle_entities(evo: EvoCycleCoordinator, platform: str) -> list[Entity]:
    """One EvoCycle's entities for one platform."""
    build = _BUILDERS.get(platform)
    return [] if build is None else build(evo)
