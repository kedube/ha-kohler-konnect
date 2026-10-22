"""Service actions for Kohler Konnect.

The faucets' ``dispense`` lives in `faucet/services.py` and is registered from here. The two
valve actions write through ``solowritesystem`` and are registered only when the account has
an Anthem valve:

* ``custom_shower`` — outlets, temperature and optional flow, sent as **one** complete
  command built only from the form (`konnect.valve_hex.encode_shower`). One command
  because the valve cannot take two back to back: the second is built from a report that
  predates the first, and undoes it.
* ``send_valve_hex`` — a raw command word, for anything else.

The device field may be left empty with one valve; with several it is required.

⚠️ These services can run water. Input is validated, but otherwise unrestricted by design.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service import async_set_service_schema

from .const import (
    DEFAULT_FLOW_PERCENT,
    DOMAIN,
    SERVICE_CUSTOM_SHOWER,
    SERVICE_DISPENSE,
    SERVICE_SEND_VALVE_HEX,
    UI_TEMPERATURE_MAX_F,
    ZONE_TEMPERATURE_MIN_F,
)
from .coordinator import KohlerKonnectCoordinator, Valve
from .faucet.services import async_register_faucet_services
from .konnect.models import ValveModel
from .konnect.valve_hex import (
    FLOW_BYTE_MAX,
    FLOW_BYTE_MIN,
    FLOW_PER_PERCENT,
    ValveHexError,
    encode_shower,
    unit_to_celsius,
)
from .number import _celsius_ceiling

_LOGGER = logging.getLogger(__name__)

# Device-registry id, as the device selector yields it.
ATTR_DEVICE_ID = "device_id"
ATTR_ZONE1_HEX = "zone1_hex"
ATTR_ZONE2_HEX = "zone2_hex"

ATTR_ZONE1_TEMPERATURE = "zone1_temperature"
ATTR_ZONE2_TEMPERATURE = "zone2_temperature"
ATTR_FLOW = "flow"
ATTR_KEEP_ON = "keep_on_after_warmup"
# Flat call-data keys; the form's per-zone sections are display only.
_ZONE_TEMPERATURE_FIELDS = {1: ATTR_ZONE1_TEMPERATURE, 2: ATTR_ZONE2_TEMPERATURE}
_ZONE_OUTLET_FIELDS: dict[int, tuple[str, ...]] = {
    1: ("zone1_outlet_1", "zone1_outlet_2", "zone1_outlet_3"),
    2: ("zone2_outlet_1", "zone2_outlet_2", "zone2_outlet_3"),
}
_ZONE_SECTIONS = {1: "zone_1", 2: "zone_2"}
# Flow byte range 0x10-0xC8 (16-200) as a percentage: 8-100 %.
_FLOW_MIN_PERCENT = FLOW_BYTE_MIN // FLOW_PER_PERCENT
_FLOW_MAX_PERCENT = FLOW_BYTE_MAX // FLOW_PER_PERCENT

# 8 hex characters (a command word, as the Hex sensor shows it) or 16 (a whole status word;
# `_command_half` drops the feedback half). `async_send_valve_hex` re-checks the length.
_HEX_WORD = vol.All(
    cv.string, cv.matches_regex(r"^(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{16})$")
)

SEND_VALVE_HEX_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): vol.Any(None, cv.string),
        vol.Required(ATTR_ZONE1_HEX): _HEX_WORD,
        # The UI submits "" for a cleared optional text field.
        vol.Optional(ATTR_ZONE2_HEX): vol.Any("", None, _HEX_WORD),
    }
)

# Temperatures are range-checked in the handler, since bounds depend on the account's unit.
# Booleans default to off for YAML callers; see `_FIELD_OUTLETS` for the form.
CUSTOM_SHOWER_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): vol.Any(None, cv.string),
        vol.Required(ATTR_ZONE1_TEMPERATURE): vol.Coerce(float),
        vol.Optional(ATTR_ZONE2_TEMPERATURE): vol.Any(None, vol.Coerce(float)),
        vol.Optional(ATTR_FLOW): vol.All(
            vol.Coerce(float), vol.Range(min=_FLOW_MIN_PERCENT, max=_FLOW_MAX_PERCENT)
        ),
        vol.Optional(ATTR_KEEP_ON, default=False): cv.boolean,
        **{
            vol.Optional(key, default=False): cv.boolean
            for keys in _ZONE_OUTLET_FIELDS.values()
            for key in keys
        },
    }
)


# The forms as the UI renders them. `services.yaml` holds the static fallback; keep the two
# in step by hand, as no test compares them.
_FIELD_DEVICE = {
    "name": "Valve",
    "required": False,
    "description": "Which Anthem valve, when the account has more than one.",
    "selector": {"device": {"filter": {"integration": DOMAIN}}},
}
_FIELD_ZONE1 = {
    "name": "Zone 1 Hex",
    "required": True,
    "description": (
        'The 8-character code from the "Hex" sensor ("Hex 1" on a two-zone valve) on '
        "the Anthem Valve device. Set the outlet switches and temperature the way you "
        "want them first, then copy the code."
    ),
    "example": "0184C801",
    "selector": {"text": None},
}
_FIELD_ZONE2 = {
    "name": "Zone 2 Hex",
    "required": False,
    "description": (
        'The 8-character code from the "Hex 2" sensor on the Anthem Valve device. Set '
        "the outlet switches and temperature the way you want them first, then copy "
        "the code."
    ),
    "example": "1184C801",
    "selector": {"text": None},
}
_SERVICE_DESCRIPTION = (
    "Send a command code straight to the Anthem valve, for anything the normal "
    "controls cannot do. Set the shower up how you want it with the outlet switches "
    "and temperature controls, then copy the code from the Hex diagnostic sensor "
    "and paste it below. WARNING: this can start water."
)

_CUSTOM_SHOWER_DESCRIPTION = (
    "Start the shower with the outlets and temperature you choose, sent to the valve as "
    "one command. This is the reliable way to open an outlet and set its temperature from "
    "a single automation step. Outlets you leave off are closed, and leaving every outlet "
    "off stops the shower. On a valve with warm-up enabled the valve warms up first and "
    'then pauses for two minutes, just as it always does; turn on "No pausing warm-up" '
    "(beta) to have it carry on with your outlets and temperature the moment "
    "that pause begins. WARNING: this can start water."
)
# Static defaults (°F, K-28212 outlets); `_async_describe_custom_shower` adapts them.
_FIELD_ZONE1_TEMPERATURE = {
    "name": "Zone 1 temperature",
    "required": True,
    "description": "Target temperature for zone 1, in the unit your account uses.",
    "example": 104,
    "selector": {
        "number": {
            "min": 59,
            "max": 118,
            "step": 1,
            "unit_of_measurement": "°F",
            "mode": "slider",
        }
    },
}
_FIELD_ZONE2_TEMPERATURE = {
    "name": "Zone 2 temperature",
    "required": False,
    "description": "Target temperature for zone 2. Leave it unset to use the zone 1 temperature.",
    "example": 104,
    "selector": {
        "number": {
            "min": 59,
            "max": 118,
            "step": 1,
            "unit_of_measurement": "°F",
            "mode": "slider",
        }
    },
}
# `required: True` on the booleans is for display only: the automation editor then shows a
# bare toggle, pre-filled off, instead of a toggle behind an "include this field" checkbox.
# The schema still defaults them for YAML callers.
_FIELD_OUTLETS = {
    "zone1_outlet_1": {
        "name": "Zone 1 outlet 1",
        "required": True,
        "selector": {"boolean": None},
    },
    "zone1_outlet_2": {
        "name": "Zone 1 outlet 2",
        "required": True,
        "selector": {"boolean": None},
    },
    "zone1_outlet_3": {
        "name": "Zone 1 outlet 3",
        "required": True,
        "selector": {"boolean": None},
    },
    "zone2_outlet_1": {
        "name": "Zone 2 outlet 1",
        "required": True,
        "selector": {"boolean": None},
    },
    "zone2_outlet_2": {
        "name": "Zone 2 outlet 2",
        "required": True,
        "selector": {"boolean": None},
    },
    "zone2_outlet_3": {
        "name": "Zone 2 outlet 3",
        "required": True,
        "selector": {"boolean": None},
    },
}
# "(beta)" is deliberate and matches the docs.
_FIELD_KEEP_ON = {
    "name": "No pausing warm-up (beta)",
    "required": True,
    "description": (
        "Keeps the shower on after the valve's warm-up is finished. Beta, tested on one "
        "valve so far. Only matters when the valve's warm-up is enabled. After warming "
        "up, the valve pauses for two minutes, just as it always "
        "does. With this on, the shower carries on with your outlets and temperature the "
        "moment that pause begins. Off, the valve's two-minute pause runs as usual."
    ),
    "selector": {"boolean": None},
}
_FIELD_FLOW = {
    "name": "Flow",
    "required": False,
    "description": (
        "Percentage of full flow, from 8 to 100. Leave it unset for full flow. The flow "
        "set here holds only until someone presses the flow button on the touchscreen, "
        "which takes over from then on."
    ),
    "example": 100,
    "selector": {
        "number": {
            "min": 8,
            "max": 100,
            "step": 1,
            "unit_of_measurement": "%",
            "mode": "box",
        }
    },
}
_SECTION_NAMES = {
    "zone_1": "Zone 1",
    "zone_2": "Zone 2",
    "advanced_fields": "Advanced",
}


def _temperature_bounds(unit: str) -> tuple[float, float, str]:
    """The form's range in the account's unit: 59-118 °F, 15-47 °C.

    The zone slider's range without its Cold stop, fixed because one form serves every
    valve (each valve clamps to its own scald limit). ⚠️ Cold is deliberately unreachable:
    typing 0 must be an error, not a cold shower.
    """
    if unit.lower().startswith("f"):
        return float(ZONE_TEMPERATURE_MIN_F), float(UI_TEMPERATURE_MAX_F), "°F"
    return (
        float(round(unit_to_celsius(ZONE_TEMPERATURE_MIN_F, "Fahrenheit"))),
        _celsius_ceiling(UI_TEMPERATURE_MAX_F),
        "°C",
    )


def _async_describe_service(
    hass: HomeAssistant, two_zones: bool, several_valves: bool
) -> None:
    """Publish the `send_valve_hex` form for this installation.

    Shows Zone 2 only on a two-zone system and the Valve picker only with several valves.
    Best effort: on failure the static `services.yaml` form stands.
    """
    fields: dict[str, Any] = {}
    if several_valves:
        fields[ATTR_DEVICE_ID] = _FIELD_DEVICE
    fields["zone1_hex"] = _FIELD_ZONE1
    if two_zones:
        fields["zone2_hex"] = _FIELD_ZONE2
    try:
        async_set_service_schema(
            hass,
            DOMAIN,
            SERVICE_SEND_VALVE_HEX,
            {
                "name": "Send valve hex",
                "description": _SERVICE_DESCRIPTION,
                "fields": fields,
            },
        )
    except Exception:
        _LOGGER.debug("Could not override the service description", exc_info=True)


def _async_describe_custom_shower(
    hass: HomeAssistant, models: list[ValveModel], temperature_unit: str
) -> None:
    """Publish the `custom_shower` form for this installation.

    Adapts the temperature unit and range, and shows the zones and outlets of the union of
    ``models``; the handler rejects an outlet the chosen valve lacks. The Valve picker
    appears only with several valves. Best effort, like `_async_describe_service`.
    """
    low, high, symbol = _temperature_bounds(temperature_unit)

    def temperature_field(static: dict[str, Any]) -> dict[str, Any]:
        return {
            **static,
            "selector": {
                "number": {
                    **static["selector"]["number"],
                    "min": int(low),
                    "max": int(high),
                    "unit_of_measurement": symbol,
                }
            },
        }

    statics = {1: _FIELD_ZONE1_TEMPERATURE, 2: _FIELD_ZONE2_TEMPERATURE}
    fields: dict[str, Any] = {}
    if len(models) > 1:
        fields[ATTR_DEVICE_ID] = _FIELD_DEVICE
    zones = [1, 2] if any(model.uses_valve2 for model in models) else [1]
    for zone in zones:
        widest = max(
            model.outlets_in_zone(zone) for model in models if zone in model.zones
        )
        keys = _ZONE_OUTLET_FIELDS[zone][:widest]
        section = _ZONE_SECTIONS[zone]
        fields[section] = {
            "name": _SECTION_NAMES[section],
            "collapsed": False,
            "fields": {
                _ZONE_TEMPERATURE_FIELDS[zone]: temperature_field(statics[zone]),
                **{key: _FIELD_OUTLETS[key] for key in keys},
            },
        }
    fields[ATTR_KEEP_ON] = _FIELD_KEEP_ON
    fields["advanced_fields"] = {
        "name": _SECTION_NAMES["advanced_fields"],
        "collapsed": True,
        "fields": {ATTR_FLOW: _FIELD_FLOW},
    }
    try:
        async_set_service_schema(
            hass,
            DOMAIN,
            SERVICE_CUSTOM_SHOWER,
            {
                "name": "Custom shower",
                "description": _CUSTOM_SHOWER_DESCRIPTION,
                "fields": fields,
            },
        )
    except Exception:
        _LOGGER.debug("Could not override the custom_shower description", exc_info=True)


def _resolve_valve(hass: HomeAssistant, device_id: str | None) -> Valve:
    """Find the valve an action is for.

    ``device_id`` is a device-registry id (a valve or one of its zone sub-devices). Without
    one, the only valve across all loaded entries is used; with several, raises
    `ServiceValidationError`.
    """
    entries: dict[str, KohlerKonnectCoordinator] = hass.data.get(DOMAIN, {})
    found = [
        (coordinator, valve)
        for coordinator in entries.values()
        for valve in coordinator.valves
    ]
    if not found:
        raise HomeAssistantError(
            "No Anthem valve on this account — solowritesystem is a valve endpoint, and an "
            "Anthem Plus controller is driven through favorites instead"
        )
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        if device is None:
            raise ServiceValidationError(f"No device with id {device_id!r}")
        wanted = {
            identifier for domain, identifier in device.identifiers if domain == DOMAIN
        }
        for coordinator, valve in found:
            if any(
                ident == valve.device_id or ident.startswith(f"{valve.device_id}_zone_")
                for ident in wanted
            ):
                return _open(coordinator, valve)
        raise ServiceValidationError(
            f"{device.name_by_user or device.name} is not an Anthem valve; choose the "
            "valve device, not the controller"
        )
    if len(found) > 1:
        raise ServiceValidationError(
            "More than one Anthem valve is set up ("
            + ", ".join(valve.name for _, valve in found)
            + "); say which one with the Valve field (device_id)"
        )
    return _open(*found[0])


def _open(coordinator: KohlerKonnectCoordinator, valve: Valve) -> Valve:
    """``valve``, unless its entry is unloading.

    An unloading valve stays listed until shutdown finishes, and still counts when picking
    the only valve, so a call meant for it is refused rather than sent to another.
    """
    if coordinator.closed:
        raise HomeAssistantError(
            f"{valve.name} is being reloaded; try again in a moment"
        )
    return valve


async def _async_send_valve_hex(call: ServiceCall) -> ServiceResponse:
    """Handle `kohler_konnect.send_valve_hex`."""
    valve = _resolve_valve(call.hass, call.data.get(ATTR_DEVICE_ID))
    result: dict[str, Any] = await valve.async_send_valve_hex(
        call.data[ATTR_ZONE1_HEX], call.data.get(ATTR_ZONE2_HEX) or None
    )
    return result


async def _async_custom_shower(call: ServiceCall) -> ServiceResponse:
    """Handle `kohler_konnect.custom_shower`: build both words and send them as one write.

    Temperatures are checked against `_temperature_bounds`; zone 2's defaults to zone 1's.
    Flow (default full) is a percent of each zone's own ceiling.
    """
    valve = _resolve_valve(call.hass, call.data.get(ATTR_DEVICE_ID))
    unit = valve.temperature_unit
    low, high, symbol = _temperature_bounds(unit)
    zone1 = float(call.data[ATTR_ZONE1_TEMPERATURE])
    zone2_raw = call.data.get(ATTR_ZONE2_TEMPERATURE)
    zone2 = zone1 if zone2_raw is None else float(zone2_raw)
    for zone, temperature in ((1, zone1), (2, zone2)):
        if not low <= temperature <= high:
            raise ServiceValidationError(
                f"Zone {zone} temperature must be between {low:g} and {high:g} {symbol}; "
                f"got {temperature:g}"
            )
    zone_flags = {
        zone: [bool(call.data.get(key, False)) for key in keys]
        for zone, keys in _ZONE_OUTLET_FIELDS.items()
    }
    flow = call.data.get(ATTR_FLOW)
    percent = DEFAULT_FLOW_PERCENT if flow is None else float(flow)
    temperatures = {1: unit_to_celsius(zone1, unit), 2: unit_to_celsius(zone2, unit)}
    try:
        # `encode_shower` takes one flow for both words, so zone 2's word comes from a
        # second pass at its own `Valve._wire_flow`.
        word1, word2 = encode_shower(
            valve.model, temperatures, valve._wire_flow(1, percent), zone_flags
        )
        if valve.model.uses_valve2:
            _, word2 = encode_shower(
                valve.model, temperatures, valve._wire_flow(2, percent), zone_flags
            )
    except ValveHexError as err:
        raise ServiceValidationError(str(err)) from err
    keep_on = bool(call.data.get(ATTR_KEEP_ON, False))
    result: dict[str, Any] = await valve.async_custom_shower(
        word1, word2, keep_on_after_warmup=keep_on
    )
    return {**result, ATTR_KEEP_ON: keep_on}


def async_register_services(
    hass: HomeAssistant, coordinator: KohlerKonnectCoordinator
) -> None:
    """Register the services this entry's devices can use; idempotent across entries.

    `dispense` needs a faucet and the valve actions a valve, so a controller-only entry
    registers nothing.
    """
    if coordinator.faucets:
        async_register_faucet_services(hass)
    if not coordinator.valves:
        return
    # Every valve loaded so far, across entries (this entry's coordinator is already in
    # `hass.data`). The forms are re-published on each setup; an unload doesn't, which
    # can leave a stale but harmless Valve picker.
    models = [
        valve.model
        for other in hass.data.get(DOMAIN, {}).values()
        for valve in other.valves
    ]
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_VALVE_HEX):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_VALVE_HEX,
            _async_send_valve_hex,
            schema=SEND_VALVE_HEX_SCHEMA,
            # Responds with the decoded words.
            supports_response=SupportsResponse.OPTIONAL,
        )
    # Must follow registration: the description attaches to an existing service.
    _async_describe_service(
        hass, any(model.uses_valve2 for model in models), len(models) > 1
    )
    if not hass.services.has_service(DOMAIN, SERVICE_CUSTOM_SHOWER):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CUSTOM_SHOWER,
            _async_custom_shower,
            schema=CUSTOM_SHOWER_SCHEMA,
            # Same response as `send_valve_hex`, so it shows the words it sent.
            supports_response=SupportsResponse.OPTIONAL,
        )
    _async_describe_custom_shower(hass, models, coordinator.temperature_unit)


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the services when the last entry unloads; safe if never registered."""
    # ⚠️ Must list every service `async_register_services` can register.
    for service in (
        SERVICE_SEND_VALVE_HEX,
        SERVICE_CUSTOM_SHOWER,
        SERVICE_DISPENSE,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
