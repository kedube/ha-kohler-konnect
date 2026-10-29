"""`set_fan_schedule`: a Smart Fan's schedule, as the app's Fan Schedule editor saves it
(`docs/protocol/smart_fan.md` §3.4): when it starts and ends, how it repeats, and the speed.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from ..const import DOMAIN
from ..konnect.smart_fan import REPEATS, SPEEDS, WEEKDAYS, schedule_changes
from .coordinator import SmartFanCoordinator

SERVICE_SET_FAN_SCHEDULE = "set_fan_schedule"

SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required("start"): cv.time,
        vol.Required("end"): cv.time,
        vol.Optional("speed"): vol.In([speed.lower() for speed in SPEEDS]),
        vol.Optional("repeat", default="everyday"): vol.In(list(REPEATS)),
        vol.Optional("days", default=list): vol.All(cv.ensure_list, [vol.In(WEEKDAYS)]),
    }
)


def _fan(hass: HomeAssistant, device_id: str) -> SmartFanCoordinator:
    device = dr.async_get(hass).async_get(device_id)
    wanted = (
        {ident for domain, ident in device.identifiers if domain == DOMAIN}
        if device
        else set()
    )
    for coordinator in hass.data.get(DOMAIN, {}).values():
        for fan in coordinator.products.get("smart_fan", []):
            if fan.device_id in wanted and not coordinator.closed:
                return fan
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="smart_fan_not_selected"
    )


async def _async_set(call: ServiceCall) -> None:
    fan = _fan(call.hass, call.data[ATTR_DEVICE_ID])
    # To the minute, as the app's pickers set it.
    start = call.data["start"].replace(second=0, microsecond=0)
    end = call.data["end"].replace(second=0, microsecond=0)
    repeat, days = call.data["repeat"], call.data["days"]
    if start == end:
        # The app's "Please set the duration for less than 24 hours."
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="fan_schedule_same_times"
        )
    if repeat == "custom" and not days:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="fan_schedule_no_days"
        )
    # Unless given, the speed it has, as the app's editor opens with it.
    stored = str(fan.settings.get("fsFanSpeed") or "").upper()
    speed = call.data.get("speed") or (stored if stored in SPEEDS else "MEDIUM")
    await fan.async_set_schedule(schedule_changes(start, end, speed, repeat, days))


def async_register_smart_fan_services(hass: HomeAssistant) -> None:
    """Register ``set_fan_schedule``, once."""
    if not hass.services.has_service(DOMAIN, SERVICE_SET_FAN_SCHEDULE):
        hass.services.async_register(
            DOMAIN, SERVICE_SET_FAN_SCHEDULE, _async_set, schema=SCHEDULE_SCHEMA
        )
