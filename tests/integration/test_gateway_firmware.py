"""The faucet's "Gateway firmware", as the Konnect app shows it (2026-10-16)."""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import FakeKohler

GATEWAY = "sensor.kitchen_gateway_firmware"


async def test_the_gateway_firmware_is_a_diagnostic_sensor(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """`configuration.about.firmware.version`: 16.0 in the app, on the owner's faucet."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(GATEWAY).state == "16.0"
    entry = er.async_get(hass).async_get(GATEWAY)
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    assert entry.disabled_by is None


async def test_the_firmware_check_stands_in_until_the_configuration_answers(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    del kohler.config["configuration"]["about"]["firmware"]
    kohler.firmware = {"firmwareUpdateAvailable": False, "currentFirmware": "15.2"}
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(GATEWAY).state == "15.2"


async def test_the_gateway_firmware_shows_while_the_faucet_is_offline(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """Kohler's record of it, not a live reading."""
    kohler.connection = "Disconnected"
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(GATEWAY).state == "16.0"
