"""The ``dispense`` action: a measured amount of water, or a Konnect preset, from a faucet.

Usable from automations, scripts and voice assistants — "dispense 300 mL", "dispense 2
cups", or a preset saved in the Konnect app.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from ..const import (
    ATTR_AMOUNT,
    ATTR_PRESET,
    ATTR_UNIT,
    DISPENSE_MAX_ML,
    DISPENSE_MIN_ML,
    DOMAIN,
    SERVICE_DISPENSE,
)
from .units import ML_PER_UNIT, UNIT_LABELS, from_ml, to_ml

if TYPE_CHECKING:
    from .coordinator import FaucetCoordinator

_POSITIVE = vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False))

DISPENSE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional(ATTR_AMOUNT): _POSITIVE,
            vol.Optional(ATTR_UNIT): vol.In(list(ML_PER_UNIT)),
            vol.Optional(ATTR_PRESET): vol.All(cv.string, vol.Strip, vol.Length(min=1)),
        }
    ),
    cv.has_at_least_one_key(ATTR_AMOUNT, ATTR_PRESET),
    cv.has_at_most_one_key(ATTR_AMOUNT, ATTR_PRESET),
)


def _target_faucets(hass: HomeAssistant, call: ServiceCall) -> list[FaucetCoordinator]:
    """The loaded faucets the call is aimed at."""
    faucets: list[FaucetCoordinator] = [
        faucet
        for coordinator in hass.data.get(DOMAIN, {}).values()
        for faucet in coordinator.faucets
    ]
    if not faucets:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_faucet"
        )
    if device_ids := call.data.get(ATTR_DEVICE_ID):
        dev_reg = dr.async_get(hass)
        wanted = {
            identifier
            for device_id in device_ids
            if (device := dev_reg.async_get(device_id))
            for domain, identifier in device.identifiers
            if domain == DOMAIN
        }
        targets = [faucet for faucet in faucets if faucet.device_id in wanted]
        if not targets:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="faucet_not_selected"
            )
        return targets
    if len(faucets) > 1:
        # Never run water at every faucet in the house by accident.
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="faucet_required"
        )
    return faucets


def limit_text(value: float, *, up: bool) -> str:
    """A dispense limit to 4 significant digits, without exponent notation.

    Rounded inward (the minimum up, the maximum down), so every amount in the range the
    message states is accepted.
    """
    digits = max(0, 3 - math.floor(math.log10(value)))
    scale = 10**digits
    # The nudge keeps float noise like 1135.9999999 from rounding a step away.
    scaled = math.ceil(value * scale - 1e-6) if up else math.floor(value * scale + 1e-6)
    return (
        f"{scaled / scale:.{digits}f}".rstrip("0").rstrip(".")
        if digits
        else str(scaled)
    )


def _requested(
    call: ServiceCall, coordinator: FaucetCoordinator
) -> tuple[float, str | None]:
    """(litres, preset name or None) for one faucet, or raise why not."""
    if (name := call.data.get(ATTR_PRESET)) is not None:
        if (preset := coordinator.find_preset(name)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="preset_not_found",
                translation_placeholders={
                    "name": coordinator.device_name,
                    "preset": name,
                    "presets": ", ".join(coordinator.preset_options) or "—",
                },
            )
        if not DISPENSE_MIN_ML <= preset.liters * 1000 <= DISPENSE_MAX_ML:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="preset_out_of_range",
                translation_placeholders={"title": preset.title},
            )
        return preset.liters, preset.title
    unit = call.data.get(ATTR_UNIT, coordinator.profile.service_unit)
    ml = to_ml(call.data[ATTR_AMOUNT], unit)
    if not DISPENSE_MIN_ML <= ml <= DISPENSE_MAX_ML:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="amount_out_of_range",
            translation_placeholders={
                "min": limit_text(from_ml(DISPENSE_MIN_ML, unit), up=True),
                "max": limit_text(from_ml(DISPENSE_MAX_ML, unit), up=False),
                "unit": UNIT_LABELS[unit],
            },
        )
    return ml / 1000, None


async def _async_dispense(call: ServiceCall) -> None:
    # Check every faucet before running water at any of them.
    requests = [
        (faucet, _requested(call, faucet))
        for faucet in _target_faucets(call.hass, call)
    ]
    for faucet, (liters, preset) in requests:
        await faucet.async_dispense(liters, preset)


def async_register_faucet_services(hass: HomeAssistant) -> None:
    """Register ``dispense``, once."""
    if not hass.services.has_service(DOMAIN, SERVICE_DISPENSE):
        hass.services.async_register(
            DOMAIN, SERVICE_DISPENSE, _async_dispense, schema=DISPENSE_SCHEMA
        )
