"""The Ice Bath's actions (`docs/protocol/ice_bath.md`).

`log_ice_bath_session` records a session as the app's "New Record" does (§4.1): only when,
how long and how cold; the app's notes and answers are never sent from here.
`set_ice_bath_routine` changes the routine as the app's Routine screen does (§4.3).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID, UnitOfTemperature
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from ..const import DOMAIN
from .coordinator import IceBathCoordinator

SERVICE_LOG_SESSION = "log_ice_bath_session"
SERVICE_SET_ROUTINE = "set_ice_bath_routine"
ATTR_DURATION = "duration"
ATTR_TEMPERATURE = "temperature"
ATTR_COMPLETED = "completed"

LOG_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_DURATION): cv.positive_time_period,
        vol.Required(ATTR_TEMPERATURE): vol.Coerce(float),
        vol.Optional(ATTR_COMPLETED): cv.datetime,
    }
)

# The routine's weekdays, Sunday first, as its `days` string holds them (§3.1).
_ROUTINE_DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

ROUTINE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Exclusive("days", "routine"): vol.All(
                cv.ensure_list, vol.Length(min=1), [vol.In(_ROUTINE_DAYS)]
            ),
            vol.Exclusive("sessions_per_week", "routine"): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=7)
            ),
            vol.Optional("reminders"): cv.boolean,
            vol.Optional("reminder_time"): cv.time,
            vol.Optional("follow_ups"): cv.boolean,
        }
    ),
    cv.has_at_least_one_key(
        "days", "sessions_per_week", "reminders", "reminder_time", "follow_ups"
    ),
)

# What the app's temperature pickers can store (§5.1), in the unit each is shown in. The
# journal keeps whole °F, so the °C range is the °F picker's, 36–75, in °C.
_RANGES = {UnitOfTemperature.CELSIUS: (2, 24), UnitOfTemperature.FAHRENHEIT: (36, 75)}
_STORED = _RANGES[UnitOfTemperature.FAHRENHEIT]


def _journal(hass: HomeAssistant, device_id: str) -> IceBathCoordinator:
    device = dr.async_get(hass).async_get(device_id)
    wanted = (
        {ident for domain, ident in device.identifiers if domain == DOMAIN}
        if device
        else set()
    )
    for coordinator in hass.data.get(DOMAIN, {}).values():
        for journal in coordinator.products.get("ice_bath", []):
            if journal.device_id in wanted and not coordinator.closed:
                return journal
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="ice_bath_not_selected"
    )


async def _async_log(call: ServiceCall) -> None:
    hass = call.hass
    journal = _journal(hass, call.data[ATTR_DEVICE_ID])
    now = dt_util.now()
    completed = call.data.get(ATTR_COMPLETED) or now
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=dt_util.get_default_time_zone())
    if completed > now:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="ice_bath_in_future"
        )
    unit = hass.config.units.temperature_unit
    low, high = _RANGES.get(unit, _STORED)
    temperature = call.data[ATTR_TEMPERATURE]
    # The journal keeps whole °F whatever the account's unit (§5.1).
    fahrenheit = round(
        TemperatureConverter.convert(temperature, unit, UnitOfTemperature.FAHRENHEIT)
    )
    if not (low <= temperature <= high and _STORED[0] <= fahrenheit <= _STORED[1]):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="ice_bath_temperature",
            translation_placeholders={"low": str(low), "high": str(high), "unit": unit},
        )
    seconds = int(call.data[ATTR_DURATION].total_seconds())
    await journal.async_log_session(completed, seconds, fahrenheit)


async def _async_set_routine(call: ServiceCall) -> None:
    journal = _journal(call.hass, call.data[ATTR_DEVICE_ID])
    changes: dict[str, Any] = {}
    if "days" in call.data:
        picked = set(call.data["days"])
        changes["isDaysOfWeek"] = True
        changes["days"] = "".join(
            "1" if day in picked else "0" for day in _ROUTINE_DAYS
        )
    if "sessions_per_week" in call.data:
        changes["isDaysOfWeek"] = False
        changes["noOfDays"] = call.data["sessions_per_week"]
    if "reminders" in call.data:
        changes["isReminderEnabled"] = call.data["reminders"]
    if "reminder_time" in call.data:
        # 24-hour `HH:mm`, as the app writes it.
        changes["reminderTime"] = call.data["reminder_time"].strftime("%H:%M")
    if "follow_ups" in call.data:
        changes["isFollowUpEnabled"] = call.data["follow_ups"]
    await journal.async_set_routine(**changes)


def async_register_ice_bath_services(hass: HomeAssistant) -> None:
    """Register the Ice Bath's actions, once."""
    if not hass.services.has_service(DOMAIN, SERVICE_LOG_SESSION):
        hass.services.async_register(
            DOMAIN, SERVICE_LOG_SESSION, _async_log, schema=LOG_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_ROUTINE):
        hass.services.async_register(
            DOMAIN, SERVICE_SET_ROUTINE, _async_set_routine, schema=ROUTINE_SCHEMA
        )
