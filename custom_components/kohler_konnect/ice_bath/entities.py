"""An Ice Bath journal's entities (`product_platforms.py` lists them).

The last session's time, length and temperature, this week's count against the routine's,
whether a session is due, missed routine days, the routine, and a calendar of sessions; and
the routine's two settings the app's Settings screen switches. Nothing personal: no notes,
answers or follow-ups.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from ..konnect.ice_bath import Session, week_start
from ..product_base import ProductEntity, UntestedControl
from .coordinator import IceBathCoordinator

__all__ = ["ice_bath_entities"]

_WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


class _IceBathSensor(ProductEntity, SensorEntity):
    coordinator: IceBathCoordinator

    def __init__(self, coordinator: IceBathCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key


class LastSession(_IceBathSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: IceBathCoordinator) -> None:
        super().__init__(coordinator, "last_session")

    @property
    def native_value(self) -> datetime | None:
        session = self.coordinator.last_session
        return None if session is None else session.completed


class LastSessionDuration(_IceBathSensor):
    """How long the last session lasted, as typed in; 600 s and a flag for "10+" (§5.2)."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, coordinator: IceBathCoordinator) -> None:
        super().__init__(coordinator, "last_session_duration")

    @property
    def native_value(self) -> int | None:
        session = self.coordinator.last_session
        return None if session is None else session.duration

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        session = self.coordinator.last_session
        return {"ten_minutes_or_more": bool(session and session.ten_plus)}


class LastSessionTemperature(_IceBathSensor):
    """How cold, as typed in: whole °F on the wire, shown in Home Assistant's unit."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT

    def __init__(self, coordinator: IceBathCoordinator) -> None:
        super().__init__(coordinator, "last_session_temperature")

    @property
    def native_value(self) -> int | None:
        session = self.coordinator.last_session
        return None if session is None else session.temperature


def _this_week(coordinator: IceBathCoordinator) -> int:
    """Days with a session since Sunday, as the app counts its week."""
    start = week_start(dt_util.now().date())
    days = {dt_util.as_local(s.completed).date() for s in coordinator.sessions}
    return sum(1 for day in days if day >= start)


class SessionDue(ProductEntity, BinarySensorEntity):
    """On when a session is still due: today is a routine day with none yet, or for a
    sessions-a-week routine, this week is short of it."""

    coordinator: IceBathCoordinator

    def __init__(self, coordinator: IceBathCoordinator) -> None:
        super().__init__(coordinator, "session_due")
        self._attr_translation_key = "session_due"

    @property
    def is_on(self) -> bool | None:
        coordinator = self.coordinator
        routine = coordinator.routine
        today = dt_util.now().date()
        if routine.get("isDaysOfWeek") is True:
            days = routine.get("days")
            if not isinstance(days, str) or len(days) != 7:
                return None
            # Sunday first (§3.1); `weekday()` counts Monday 0.
            planned = days[(today.weekday() + 1) % 7] == "1"
            done = any(
                dt_util.as_local(s.completed).date() == today
                for s in coordinator.sessions
            )
            return planned and not done
        target = coordinator.target
        return None if target is None else _this_week(coordinator) < target


class MissedThisMonth(_IceBathSensor):
    """Routine days marked missed in the current month, as the app's calendar marks them:
    today never counts, since a session may still come."""

    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator: IceBathCoordinator) -> None:
        super().__init__(coordinator, "missed_this_month")

    @property
    def native_value(self) -> int:
        today = dt_util.now().date()
        missed = (self.coordinator.data or {}).get("missed") or []
        return sum(
            1
            for day in missed
            if (day.year, day.month) == (today.year, today.month) and day != today
        )

    @property
    def last_reset(self) -> datetime:
        return dt_util.start_of_local_day(dt_util.now().replace(day=1))


class SessionsCalendar(ProductEntity, CalendarEntity):
    """Each session as an event: when, how long and how cold. Nothing personal."""

    coordinator: IceBathCoordinator

    def __init__(self, coordinator: IceBathCoordinator) -> None:
        super().__init__(coordinator, "sessions")
        self._attr_translation_key = "sessions"

    def _event(self, session: Session) -> CalendarEvent:
        minutes, seconds = divmod(session.duration, 60)
        length = "10+ min" if session.ten_plus else f"{minutes}:{seconds:02d}"
        parts = [length]
        if session.temperature is not None:
            unit = self.hass.config.units.temperature_unit
            degrees = TemperatureConverter.convert(
                session.temperature, UnitOfTemperature.FAHRENHEIT, unit
            )
            parts.append(f"{round(degrees)} {unit}")
        return CalendarEvent(
            start=session.completed - timedelta(seconds=session.duration),
            end=session.completed,
            summary=" · ".join(parts),
        )

    @property
    def event(self) -> CalendarEvent | None:
        last = self.coordinator.last_session
        return None if last is None else self._event(last)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        found = await self.coordinator.async_sessions_between(start_date, end_date)
        return [self._event(session) for session in found]


class SessionsThisWeek(_IceBathSensor):
    """Days with a session since Sunday, as the app counts its week, against the
    routine's target."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: IceBathCoordinator) -> None:
        super().__init__(coordinator, "sessions_this_week")

    @property
    def native_value(self) -> int:
        return _this_week(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"target": self.coordinator.target}


class Routine(_IceBathSensor):
    """The routine: its days, or how many a week, with its reminder settings."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: IceBathCoordinator) -> None:
        super().__init__(coordinator, "routine")

    @property
    def _routine(self) -> dict[str, Any]:
        return self.coordinator.routine

    @property
    def _days(self) -> list[str] | None:
        """The routine's weekdays, keyed as Home Assistant's time condition keys them."""
        routine = self._routine
        days = routine.get("days")
        if routine.get("isDaysOfWeek") is not True or not isinstance(days, str):
            return None
        if len(days) != 7:
            return None
        # Sunday first (§3.1).
        return [name for name, bit in zip(_WEEKDAYS, days, strict=True) if bit == "1"]

    @property
    def native_value(self) -> str | None:
        if (days := self._days) is not None:
            return ", ".join(day.title() for day in days)
        if self._routine.get("isDaysOfWeek") is True:
            return None
        count = self._routine.get("noOfDays")
        return None if count is None else f"{count}/week"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        routine = self._routine
        return {
            "days": self._days,
            "reminders": routine.get("isReminderEnabled"),
            "reminder_time": routine.get("reminderTime"),
            "follow_ups": routine.get("isFollowUpEnabled"),
        }


class RoutineSwitch(UntestedControl, ProductEntity, SwitchEntity):
    """One of the routine's switches in the app's Settings: its reminders, or the
    questions it asks after a session (§4.3). The answers themselves stay in the app."""

    coordinator: IceBathCoordinator
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IceBathCoordinator, key: str, field: str) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._field = field

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.routine.get(self._field)
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_routine(**{self._field: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_routine(**{self._field: False})


_BUILDERS: dict[str, Callable[[IceBathCoordinator], list[Entity]]] = {
    "binary_sensor": lambda c: [SessionDue(c)],
    "calendar": lambda c: [SessionsCalendar(c)],
    "sensor": lambda c: [
        LastSession(c),
        LastSessionDuration(c),
        LastSessionTemperature(c),
        SessionsThisWeek(c),
        MissedThisMonth(c),
        Routine(c),
    ],
    "switch": lambda c: [
        RoutineSwitch(c, "routine_reminders", "isReminderEnabled"),
        RoutineSwitch(c, "follow_up_questions", "isFollowUpEnabled"),
    ],
}


def ice_bath_entities(journal: IceBathCoordinator, platform: str) -> list[Entity]:
    build = _BUILDERS.get(platform)
    return [] if build is None else build(journal)
