"""Shower sensors: system status, water usage, and diagnostics.

`System Status` is the headline entity. Most of the rest are diagnostics, disabled by
default.

There are no measured-temperature or measured-flow entities: the status word's feedback
bytes report no measurement on the hardware tested (`docs/gcs/valve_hex.md`, Word layout).
They are still published as attributes of the Hex sensor.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import Controller, KohlerKonnectCoordinator, Valve
from .entity import (
    KohlerControllerEntity,
    KohlerValveEntity,
    ZoneWordEntity,
    zone_label,
)
from .faucet.sensor import faucet_sensors
from .konnect.valve_hex import encode_word
from .water import DailyWaterUsage, MonthlyWaterUsage, YearlyWaterUsage

# Valve states; see `ValveStatusSensor.native_value` for their precedence.
STATE_RUNNING = "Water Running"
STATE_PAUSED = "Paused"
STATE_WARMING = "Warming Up"
STATE_IDLE = "Idle"
VALVE_STATES = [STATE_RUNNING, STATE_PAUSED, STATE_WARMING, STATE_IDLE]

# The valve's states minus "Paused" (see `ControllerStatusSensor`), with the same strings
# so the two sensors can be compared directly.
CONTROLLER_STATES = [STATE_RUNNING, STATE_WARMING, STATE_IDLE]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the valve, controller and faucet sensors."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for valve in coordinator.valves:
        entities += [
            ValveStatusSensor(coordinator, valve),
            ValveSystemStateSensor(coordinator, valve),
            ValveMonthlyWaterSensor(coordinator, valve),
            ValveYearlyWaterSensor(coordinator, valve),
            ValveDailyWaterSensor(coordinator, valve),
            ValveWeeklyWaterSensor(coordinator, valve),
            ValveLastUpdateSensor(coordinator, valve),
            ValveFirmwareSensor(coordinator, valve),
            # The other two firmwares the Konnect app shows; entities so a difference
            # between valves can be graphed and alerted on.
            ValveComponentFirmwareSensor(
                coordinator, valve, "primaryValve", "Valve Firmware", slug="valve"
            ),
            ValveComponentFirmwareSensor(
                coordinator, valve, "gateway", "Gateway Firmware", slug="gateway"
            ),
            ValveRegisteredSensor(coordinator, valve),
            ValveHexSensor(coordinator, valve, 1),
        ]
        if valve.model.uses_valve2:
            entities.append(ValveHexSensor(coordinator, valve, 2))
            # `about.secondaryValve1.firmware` is a placeholder `0` without a second valve.
            entities.append(
                ValveComponentFirmwareSensor(
                    coordinator,
                    valve,
                    "secondaryValve1",
                    "Second Valve Firmware",
                    slug="valve2",
                )
            )

    for controller in coordinator.controllers:
        entities.append(ControllerLastUpdateSensor(coordinator, controller))
        entities.append(ControllerMaxShowerDurationSensor(coordinator, controller))

        # The controller's own view of the water. Created alongside a valve too, though
        # it misses valve-driven sessions (`docs/protocol/hub_controller.md` §5): there
        # it is for comparison; on a controller-only account it is the only answer.
        entities += [
            ControllerZoneTemperatureSensor(coordinator, controller, zone)
            for zone in controller.model.zones
        ]
        entities.append(ControllerStatusSensor(coordinator, controller))

    for faucet in coordinator.faucets:
        entities += faucet_sensors(faucet)
    async_add_entities(entities)


class ValveStatusSensor(KohlerValveEntity, SensorEntity):
    """What the shower is doing right now, as one value (`System Status`).

    Whole-valve: warm-up and pause are system-level, not per zone; `Shower Active` answers
    per zone. The one place valve and controller state are merged: with exactly one of
    each, a controller-initiated warm-up also reads as Warming Up. `ControllerStatusSensor`
    is never merged the other way.

    ⚠️ The unique id stays `_status`; changing it would orphan history and automations.
    """

    _attr_name = "System Status"
    _attr_icon = "mdi:shower"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = VALVE_STATES

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_status"

    @property
    def _hub_warmup(self) -> bool:
        """Whether the paired controller reports a warm-up (`data.showerwarmup`).

        False unless `_paired`: nothing in the cloud says which controller fronts which
        valve.
        """
        if not self._paired:
            return False
        return bool(self.coordinator.controllers[0].state.shower_warmup)

    @property
    def _paired(self) -> bool:
        """Whether the account has exactly one valve and one controller (one shower)."""
        return (
            len(self.coordinator.controllers) == 1 and len(self.coordinator.valves) == 1
        )

    @property
    def native_value(self) -> str | None:
        state = self._state
        if state is None or state.valve1 is None:
            return None
        # Paused first (a paused session still has outlets set), then Warming Up, which
        # runs water nobody started.
        if state.is_paused:
            return STATE_PAUSED
        if state.warmup_in_progress or self._hub_warmup:
            return STATE_WARMING
        if state.is_running:
            return STATE_RUNNING
        return STATE_IDLE

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Which device's warm-up is running, plus the time-left figures."""
        state = self._state
        attributes: dict[str, object] = {
            "valve_warmup": bool(state and state.warmup_in_progress),
            # None ("not asked") when the pairing is ambiguous; False with no controller.
            "controller_warmup": (
                self._hub_warmup
                if self._paired or not self.coordinator.controllers
                else None
            ),
        }
        attributes.update(self._time_left())
        return attributes

    def _time_left(self) -> dict[str, object]:
        """Seconds water has been flowing, and seconds until the valve's run-time cutoff.

        Across zones: the longest-running zone and the soonest cutoff. Per-zone figures are
        on `Shower Active`. None (never 0) when nothing flows, the limit is unknown, or the
        zone clocks were reset by a reconnect.
        """
        flowing: list[float] = []
        remaining: list[float] = []
        for zone in self._valve.model.zones:
            elapsed = self._valve.zone_flowing_for(zone)
            if elapsed is None:
                continue
            flowing.append(elapsed)
            limits = self._valve.run_time_limits_for_zone(zone)
            if limits:
                remaining.append(min(limits) - elapsed)
        return {
            "flowing_for_seconds": round(max(flowing), 1) if flowing else None,
            "seconds_remaining": round(min(remaining), 1) if remaining else None,
        }


class ValveSystemStateSensor(KohlerValveEntity, SensorEntity):
    """The valve's own `currentSystemState` (`docs/protocol/gcs_valve.md` §7).

    Independent of `System Status`, which is decoded from the command word, so the raw
    device strings are kept rather than mapped onto `VALVE_STATES`; a disagreement between
    the two is the useful signal.
    """

    _attr_name = "System State"
    _attr_icon = "mdi:state-machine"
    _attr_device_class = SensorDeviceClass.ENUM
    # Every known value. Anything else reads None (Home Assistant logs an error for an
    # undeclared ENUM option) and appears in the `reported` attribute.
    _attr_options: ClassVar[list[str]] = [
        "normalOperation",
        "showerInProgress",
        "error",
        "FirmwareUpdate",
    ]

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_system_state"

    @property
    def native_value(self) -> str | None:
        state = self._state
        if state is None or state.system_state is None:
            return None
        value = state.system_state
        # Case-insensitive, as in the app.
        if value.strip().lower() == "error":
            return "error"
        return value if value in self._attr_options else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self._state
        reported = None if state is None else state.system_state
        return {
            "reported": reported,
            "recognised": reported in self._attr_options if reported else None,
        }


class _ValveWater(KohlerValveEntity):
    """A water total read from one valve's usage series. See `water.py`."""

    def _usage_series(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        return self._valve.usage, self._valve.usage_daily

    def _water_units(self) -> str | None:
        return self.coordinator.water_units


class ValveMonthlyWaterSensor(_ValveWater, MonthlyWaterUsage):
    """Water used in the current calendar month. See `water.MonthlyWaterUsage`."""

    _attr_name = "Water Used This Month"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_water_this_month"


class ValveDiagnosticSensor(KohlerValveEntity, SensorEntity):
    """Base for the diagnostics: hidden unless deliberately enabled."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False


class ValveYearlyWaterSensor(_ValveWater, YearlyWaterUsage):
    """Water used in the current calendar year. See `water.YearlyWaterUsage`."""

    _attr_name = "Water Used This Year"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_water_this_year"


class ValveDailyWaterSensor(_ValveWater, DailyWaterUsage):
    """Water used today, from Kohler's per-day usage series.

    Re-read when a shower ends, after `USAGE_REFRESH_DELAY_SECONDS`, not on a clock.
    """

    _attr_name = "Water Used Today"
    _attr_icon = "mdi:water-check"
    _days = 1

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_water_today"


class ValveWeeklyWaterSensor(_ValveWater, DailyWaterUsage):
    """Water used over a rolling seven days, today included.

    A net total that can fall as days leave the window, so not for the Energy dashboard;
    use `Water Used Today`. See `water.DailyWaterUsage`.
    """

    _attr_name = "Water Used This Week"
    _attr_icon = "mdi:calendar-week"
    _days = 7

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_water_this_week"


class ValveLastUpdateSensor(ValveDiagnosticSensor):
    """When the valve last reported: the event's own timestamp, unlike ``last_changed``."""

    _attr_name = "Last Update"
    _attr_icon = "mdi:clock-check-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_last_update"

    @property
    def native_value(self) -> datetime | None:
        state = self._state
        if state is None or state.last_update is None:
            return None
        return datetime.fromtimestamp(state.last_update, tz=UTC)


class ValveFirmwareSensor(ValveDiagnosticSensor):
    """The interface (touchscreen) firmware, one of the three versions the app shows.

    See `docs/protocol/gcs_valve.md` §9. Unknown, never a guess, when the record has no
    interface version. ⚠️ Keeps the `_firmware` unique id.
    """

    _attr_name = "Interface Firmware"
    _attr_icon = "mdi:monitor"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_firmware"

    @property
    def native_value(self) -> str | None:
        return self._valve.firmware


class ValveComponentFirmwareSensor(ValveDiagnosticSensor):
    """The firmware of one part of the system: a valve or the gateway.

    Valves on one account can differ while the app shows one version
    (`docs/protocol/gcs_valve.md` §9).
    """

    _attr_icon = "mdi:chip"

    def __init__(
        self,
        coordinator: KohlerKonnectCoordinator,
        valve: Valve,
        component: str,
        name: str,
        *,
        slug: str,
    ) -> None:
        super().__init__(coordinator, valve)
        self._component = component
        self._attr_name = name
        self._attr_unique_id = f"{self._device_id}_firmware_{slug}"

    @property
    def native_value(self) -> str | None:
        return self._valve.component_firmware(self._component)


class ValveRegisteredSensor(ValveDiagnosticSensor):
    """When Kohler's cloud created this valve's record (``createdTime``).

    A registration date, not an installation date: a re-registered valve reads newer than
    its plumbing. Accepts ISO-8601 or epoch seconds/milliseconds.
    """

    _attr_name = "Registered"
    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_registered"

    @property
    def native_value(self) -> datetime | None:
        raw = self._valve.created_time
        if raw is None:
            return None
        text = str(raw).strip()

        # Epoch, seconds or milliseconds.
        if text.isdigit():
            epoch = int(text)
            if epoch > 10**12:
                epoch //= 1000
            try:
                return datetime.fromtimestamp(epoch, tz=UTC)
            except (OverflowError, OSError, ValueError):
                return None

        # ISO-8601, `Z` suffix included (`fromisoformat` reads it from Python 3.11).
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        # A timestamp sensor needs an aware datetime; the API's dates are UTC.
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """The raw string, so an unparsed format is diagnosable rather than just blank."""
        return {"reported": self._valve.created_time}


class ValveHexSensor(ZoneWordEntity, ValveDiagnosticSensor):
    """The valve command word for one zone, ready to paste into `send_valve_hex`.

    The 8-character command half of the 16-character word, uppercased; the feedback half
    is published as attributes. ⚠️ Taken as a slice of the raw word, never re-encoded from
    decoded fields, so it shows exactly what the valve sent.
    """

    _attr_icon = "mdi:hexadecimal"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, valve: Valve, zone: int
    ) -> None:
        super().__init__(coordinator, valve, zone)
        self._attr_name = zone_label(valve, zone, "Hex")
        self._attr_unique_id = f"{self._device_id}_zone_{zone}_hex"

    @property
    def native_value(self) -> str | None:
        word = self._word
        if word is None:
            return None
        # A REST-seeded word has no wire string, so encode one until MQTT supplies it.
        if not word.raw:
            return encode_word(
                word.prefix,
                word.temperature_celsius,
                word.flow_percent,
                word.outlet_mask,
                paused=word.paused,
            )
        return word.raw[:8].upper()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        word = self._word
        if word is None:
            return {}
        return {
            "temperature_celsius": word.temperature_celsius,
            "flow_percent": word.flow_percent,
            # `flow_percent` is the commanded flow only while this is True; an idle
            # valve's flow byte is not the setting (`docs/protocol/gcs_valve.md` §3.4).
            "flow_is_live": bool(word.outlet_mask) and not word.paused,
            "flow_setpoint": word.flow_setpoint,
            "outlet_mask": f"0x{word.outlet_mask:02X}",
            "paused": word.paused,
            "prefix": f"0x{word.prefix:02X}",
            "at_temperature": word.at_temperature,
            "at_flow": word.at_flow,
            "error_flag": word.error_flag,
            "error_code": word.error_code,
            "measured_temperature_celsius": word.measured_temperature_celsius,
            "measured_flow_percent": word.measured_flow_percent,
            # False for a REST-seeded word.
            "from_device": bool(word.raw),
        }


class ControllerDiagnosticSensor(KohlerControllerEntity, SensorEntity):
    """Base for controller diagnostics: hidden unless deliberately enabled."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False


class ControllerLastUpdateSensor(ControllerDiagnosticSensor):
    """When the controller last reported: the event's own timestamp.

    Often lags the valve's: the controller can stay silent through a valve-driven session
    (`docs/protocol/hub_controller.md` §5).
    """

    _attr_name = "Last Update"
    _attr_icon = "mdi:clock-check-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_last_update"

    @property
    def native_value(self) -> datetime | None:
        state = self._state
        if state is None or state.last_update is None:
            return None
        return datetime.fromtimestamp(state.last_update, tz=UTC)


class ControllerMaxShowerDurationSensor(ControllerDiagnosticSensor):
    """The controller's Max Shower Duration, in minutes.

    From ``hub-configuration`` ``systemSettings.maxShowerDuration``, re-read on each
    reconnect. The controller times a shower independently of the valve's own limit, and
    the shorter ends it. Enabled by default: it is a setting, not a protocol detail.
    """

    _attr_name = "Max Shower Duration"
    _attr_icon = "mdi:timer-cog-outline"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_entity_registry_enabled_default = True

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_max_shower_duration"

    @property
    def native_value(self) -> int | None:
        return self._controller.settings.max_shower_duration_minutes


class ControllerStatusSensor(KohlerControllerEntity, SensorEntity):
    """What the controller believes the shower is doing, from its own data only.

    * **Warming Up** — ``data.showerwarmup`` on ``SHOWER_VALVE_STS``; checked first, since
      zones also report ``ON`` during a warm-up;
    * **Water Running** — any zone's ``status`` is ``ON``;
    * **Idle** — otherwise.

    No "Paused": the controller reports only ``ON``/``OFF`` per zone, so a paused session
    reads Idle here; the valve's `System Status` shows the pause. ⚠️ The unique id stays
    `_status`.
    """

    _attr_name = "System Status"
    _attr_icon = "mdi:shower"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CONTROLLER_STATES

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_status"

    @property
    def native_value(self) -> str | None:
        state = self._state
        if state is None:
            return None
        if state.shower_warmup:
            return STATE_WARMING
        if state.is_running:
            return STATE_RUNNING
        return STATE_IDLE

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self._state
        if state is None:
            return {}
        return {
            # None means no message has carried the field yet.
            "shower_warmup": state.shower_warmup,
            "zone_status": {
                number: zone.status for number, zone in sorted(state.zones.items())
            },
            "supports_paused": False,
        }


class ControllerZoneTemperatureSensor(KohlerControllerEntity, SensorEntity):
    """Temperature the controller reports for one zone (``SHOWER_VALVE_STS``).

    What the controller believes; where a valve exists, the valve's setpoint is
    authoritative. ``null`` while the zone is off, hence no ``state_class``.

    Reported in °F, which Home Assistant converts for display. On a Fahrenheit account the
    reading is °F (live). The controller takes every temperature in whole °F whatever the
    account's unit, but whether it also reports °F to a Celsius account is unconfirmed
    (`docs/protocol/hub_controller.md` §5), so there a reading is placed by range: above
    50 it can only be °F (no setpoint reaches 50 °C), and 50 or below is taken as °C.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT

    def __init__(
        self,
        coordinator: KohlerKonnectCoordinator,
        controller: Controller,
        zone: int,
    ) -> None:
        super().__init__(coordinator, controller)
        self._zone = zone
        self._attr_name = zone_label(controller, zone, "Temperature")
        self._attr_unique_id = f"{self._device_id}_zone_{zone}_temperature"
        self._account_fahrenheit = coordinator.temperature_unit.lower().startswith("f")

    @property
    def native_value(self) -> float | None:
        state = self._state
        zone = None if state is None else state.zones.get(self._zone)
        if zone is None or zone.temperature is None:
            return None
        try:
            value = float(zone.temperature)
        except (TypeError, ValueError):
            return None
        # `float("NaN")` parses, and Home Assistant refuses a non-finite sensor value.
        if not math.isfinite(value):
            return None
        if self._account_fahrenheit or value > 50:
            return value
        return round(value * 9 / 5 + 32, 1)
