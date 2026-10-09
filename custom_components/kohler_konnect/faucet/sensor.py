"""Faucet sensors: status, handle, firmware download, last dispense and water usage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory

from ..water import DailyWaterUsage, MonthlyWaterUsage, YearlyWaterUsage
from .coordinator import FaucetCoordinator
from .entity import KohlerFaucetEntity
from .units import HA_UNIT_KEYS, from_ml, to_ml


def state_key(value: Any) -> str | None:
    """Kohler's text as a translatable state: "NotStarted" -> "not_started"."""
    if not isinstance(value, str) or not value.strip():
        return None
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value.strip()).lower()
    return re.sub(r"[^a-z0-9]+", "_", words).strip("_") or None


@dataclass(frozen=True, kw_only=True)
class FaucetSensorDescription(SensorEntityDescription):
    field: str  # key in Kohler's faucet state
    known: tuple[str, ...]  # states with translations


SENSORS: tuple[FaucetSensorDescription, ...] = (
    FaucetSensorDescription(
        key="status",
        translation_key="status",
        field="status",
        known=("off", "on"),
    ),
    FaucetSensorDescription(
        key="handle_state",
        translation_key="handle_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        field="handleState",
        known=("open", "closed"),
    ),
    # Kohler's "progress" is the firmware download ("Downloading" during an update), not
    # the dispense; see the Dispensing binary sensor for that.
    FaucetSensorDescription(
        key="progress",
        translation_key="progress",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        field="progress",
        known=("not_started", "downloading", "completed"),
    ),
)


def faucet_sensors(coordinator: FaucetCoordinator) -> list[SensorEntity]:
    """Every sensor for one faucet."""
    entities: list[SensorEntity] = [
        FaucetStateSensor(coordinator, description) for description in SENSORS
    ]
    entities += [
        FaucetLastDispenseSensor(coordinator),
        FaucetDailyWaterSensor(coordinator),
        FaucetWeeklyWaterSensor(coordinator),
        FaucetMonthlyWaterSensor(coordinator),
        FaucetYearlyWaterSensor(coordinator),
    ]
    return entities


class FaucetStateSensor(KohlerFaucetEntity, SensorEntity):
    """One of Kohler's state texts, as translated states.

    A value Kohler adds later still shows, untranslated, rather than failing.
    """

    entity_description: FaucetSensorDescription
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(
        self, coordinator: FaucetCoordinator, description: FaucetSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def options(self) -> list[str]:
        description = self.entity_description
        seen = {
            key
            for value in self.coordinator.seen_values[description.field]
            if (key := state_key(value)) is not None
        }
        return [*description.known, *sorted(seen - set(description.known))]

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.data or {}
        return state_key(state.get(self.entity_description.field))


class FaucetLastDispenseSensor(KohlerFaucetEntity, RestoreSensor):
    """The most recent dispense amount, in the account's units.

    The API only reports ``quantity`` while a dispense is running, so this also records
    amounts dispensed from Home Assistant and survives restarts. It deliberately has no
    volume device class: with one, Home Assistant pins the display unit on first
    registration, and a change of the account's units would stop applying after that.
    """

    _requires_online = False
    _attr_translation_key = "last_dispense"

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "last_dispense")
        profile = coordinator.profile
        self._unit = profile.number_unit
        self._attr_native_unit_of_measurement = profile.number_native_unit
        self._attr_suggested_display_precision = profile.precision

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.last_dispense_liters is not None:
            return
        last = await self.async_get_last_sensor_data()
        if last is None or not isinstance(last.native_value, (int, float)):
            return
        # Convert from whatever unit the value was stored in.
        unit = HA_UNIT_KEYS.get(str(last.native_unit_of_measurement))
        if unit is not None:
            self.coordinator.last_dispense_liters = (
                to_ml(float(last.native_value), unit) / 1000
            )

    @property
    def native_value(self) -> float | None:
        liters = self.coordinator.last_dispense_liters
        if liters is None:
            return None
        return round(from_ml(liters * 1000, self._unit), 2)


class _FaucetWater(KohlerFaucetEntity):
    """A water total read from one faucet's usage series. See `water.py`."""

    # Kohler's cloud keeps the history, whether or not the faucet is online.
    _requires_online = False

    def __init__(self, coordinator: FaucetCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key

    def _usage_series(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        return self.coordinator.usage, self.coordinator.usage_daily

    def _water_units(self) -> str | None:
        return self.coordinator.account.water_units


class FaucetDailyWaterSensor(_FaucetWater, DailyWaterUsage):
    """Water used today, as the Konnect app's daily chart shows it."""

    _attr_icon = "mdi:water-check"
    _days = 1

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "water_today")


class FaucetWeeklyWaterSensor(_FaucetWater, DailyWaterUsage):
    """Water used over the last seven days, today included."""

    _attr_icon = "mdi:calendar-week"
    _days = 7

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "water_this_week")


class FaucetMonthlyWaterSensor(_FaucetWater, MonthlyWaterUsage):
    """Water used in the current calendar month."""

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "water_this_month")


class FaucetYearlyWaterSensor(_FaucetWater, YearlyWaterUsage):
    """Water used in the current calendar year."""

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "water_this_year")
