"""The valve actions keep to each zone's own Max Temperature, as the Temperature control does.

Max Temperature is a setting the valve holds, not a hardware limit, so `custom_shower` and
`send_valve_hex` check it themselves. Before the valve reports it, the fixed ceiling of
the form (118 °F) and of every write (48.8 °C) still applies.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.kohler_konnect.const import DOMAIN
from custom_components.kohler_konnect.konnect.valve_hex import encode_word

from .conftest import account
from .test_shower_entities import (
    SOLO,
    TEMPERATURE,
    ShowerKohler,
    custom_shower,
    set_number,
    setup,
    solo,
    state,
    word,
)

# 110 °F, set as the valve's Max Temperature.
SCALD_C = 43.3
# 118 °F, the top of the form and of the app's Max Temperature.
HOT_C = 47.7


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> ShowerKohler:
    return ShowerKohler(aioclient_mock)


def scald_limit(kohler: ShowerKohler, celsius: float, outlets: range) -> None:
    for outlet in outlets:
        kohler.outlet_fields.setdefault(outlet, {})["maximumOutletTemperature"] = str(
            celsius
        )


async def send_valve_hex(hass: HomeAssistant, **data: str) -> None:
    await hass.services.async_call(
        DOMAIN, "send_valve_hex", data, blocking=True, return_response=True
    )


async def test_the_temperature_control_keeps_to_the_scald_limit(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The control the actions are measured against."""
    scald_limit(kohler, SCALD_C, range(3))
    await setup(hass, config_entry)

    # Shown in °C: the harness runs Home Assistant in metric.
    assert float(state(hass, TEMPERATURE).attributes["max"]) < 44
    with pytest.raises(ServiceValidationError):
        await set_number(hass, TEMPERATURE, HOT_C)
    assert kohler.sent(SOLO) == []


async def test_custom_shower_refuses_a_temperature_above_the_scald_limit(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    scald_limit(kohler, SCALD_C, range(3))
    await setup(hass, config_entry)
    await solo(hass, word(0))

    with pytest.raises(ServiceValidationError, match="Max Temperature of 110"):
        await custom_shower(hass, zone1_temperature=118, zone1_outlet_1=True)
    assert kohler.sent(SOLO) == []

    await custom_shower(hass, zone1_temperature=110, zone1_outlet_1=True)
    [(zone1, _)] = kohler.words()
    assert zone1.temperature_celsius == SCALD_C


async def test_send_valve_hex_refuses_a_word_above_the_scald_limit(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    scald_limit(kohler, SCALD_C, range(3))
    await setup(hass, config_entry)
    await solo(hass, word(0))

    with pytest.raises(HomeAssistantError, match=r"Max Temperature of 43\.3"):
        await send_valve_hex(hass, zone1_hex=encode_word(0x01, HOT_C, 100.0, 0b001))
    assert kohler.sent(SOLO) == []

    await send_valve_hex(hass, zone1_hex=encode_word(0x01, SCALD_C, 100.0, 0b001))
    [(zone1, _)] = kohler.words()
    assert zone1.temperature_celsius == SCALD_C


async def test_a_blank_zone_2_temperature_follows_zone_1_only_to_its_own_limit(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Zone 2 left blank takes zone 1's temperature, as far as zone 2 allows; given
    explicitly above its limit, it is refused."""
    kohler.valve_outlets = (3, 3)
    scald_limit(kohler, HOT_C, range(6))
    await setup(hass, config_entry)
    valve = account(hass, config_entry).valves[0]
    zone2_first = valve.model.outlet_id(2, 1)
    kohler.outlet_fields[zone2_first]["maximumOutletTemperature"] = "40.0"
    await account(hass, config_entry).async_refresh()
    assert valve.scald_limit_tenths(2) == 400
    await solo(hass, word(0), word(0, zone=2))

    await custom_shower(
        hass, zone1_temperature=110, zone1_outlet_1=True, zone2_outlet_1=True
    )
    [(zone1, zone2)] = kohler.words()
    assert zone1.temperature_celsius == SCALD_C
    assert zone2 is not None
    assert zone2.temperature_celsius == 40.0

    with pytest.raises(ServiceValidationError, match="Zone 2"):
        await custom_shower(
            hass,
            zone1_temperature=104,
            zone2_temperature=110,
            zone1_outlet_1=True,
            zone2_outlet_1=True,
        )
    assert len(kohler.words()) == 1
