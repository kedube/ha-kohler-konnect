"""Setting up one account with any mix of showers and faucets, and taking it down again."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kohler_konnect.const import DOMAIN

from .conftest import (
    DEVICE_ID,
    MOBILE_ID,
    VALVE,
    VALVE_ID,
    FakeKohler,
    FakeMqttClient,
    account,
    registered_device,
    unload,
    wait_for,
)

FAUCET_ENTITIES = (
    "switch.kitchen_water",
    "sensor.kitchen_status",
    "sensor.kitchen_water_used_today",
    "sensor.kitchen_water_used_this_week",
    "sensor.kitchen_water_used_this_month",
    "sensor.kitchen_water_used_this_year",
    "binary_sensor.kitchen_leak",
    "binary_sensor.kitchen_dispensing",
    "button.kitchen_dispense_250_ml",
    "button.kitchen_clear_leak_alert",
    "number.kitchen_dispense_amount",
    "update.kitchen_firmware_status",
)


async def test_an_account_with_only_a_faucet(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    for entity_id in FAUCET_ENTITIES:
        assert hass.states.get(entity_id) is not None, entity_id
    coordinator = account(hass, setup_entry)
    assert coordinator.valves == [] and coordinator.controllers == []
    assert [f.device_id for f in coordinator.faucets] == [DEVICE_ID]
    # One registration, under the entry's own identity, for the one connection.
    assert [r["mobileDeviceId"] for r in kohler.push_registrations] == [MOBILE_ID]
    assert len(FakeMqttClient.instances) == 1
    # The valve actions need a valve; the faucet's needs a faucet.
    assert hass.services.has_service(DOMAIN, "dispense")
    assert not hass.services.has_service(DOMAIN, "send_valve_hex")

    device = registered_device(hass, DEVICE_ID)
    assert device.name == "Kitchen"
    assert device.model == "Sensate"
    assert device.sw_version == "16.0"


async def test_an_account_with_a_valve_and_a_faucet(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.devices.append(dict(VALVE))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    coordinator = account(hass, config_entry)
    assert [v.device_id for v in coordinator.valves] == [VALVE_ID]
    assert [f.device_id for f in coordinator.faucets] == [DEVICE_ID]
    assert registered_device(hass, VALVE_ID) is not None
    assert registered_device(hass, DEVICE_ID) is not None
    assert hass.states.get("switch.anthem_valve_shower_on") is not None
    assert hass.states.get("switch.kitchen_water") is not None
    # Still one connection, carrying both devices' messages.
    assert len(FakeMqttClient.instances) == 1
    assert hass.services.has_service(DOMAIN, "send_valve_hex")
    assert hass.services.has_service(DOMAIN, "dispense")
    await unload(hass, config_entry)


async def test_a_faucet_kohler_cannot_answer_for_does_not_stop_the_rest(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.devices.append(dict(VALVE))
    # Every read of it fails, however often it retries.
    kohler.fail_api("/faucet-state/" + DEVICE_ID, *[(500, {"message": "oops"})] * 20)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("switch.kitchen_water").state == "unavailable"
    assert hass.states.get("switch.anthem_valve_shower_on") is not None
    await unload(hass, config_entry)


async def test_no_supported_device_retries_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.devices = [{"deviceId": "dtv-1", "sku": "DTV", "logicalName": "Steam"}]
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_removing_the_entry_unregisters_its_identity(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    await hass.config_entries.async_remove(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert kohler.unregistered == [MOBILE_ID]


async def test_old_integrations_are_pointed_out(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    sensate = MockConfigEntry(domain="kohler_sensate", title="Kitchen")
    sensate.add_to_hass(hass)
    anthem = MockConfigEntry(domain="kohler_anthem", title="Kohler Anthem (me)")
    anthem.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await wait_for(hass, lambda: account(hass, config_entry).stream.connected, "stream")
    issues = ir.async_get(hass)
    issue = issues.async_get_issue(DOMAIN, "replaced_integration")
    assert issue is not None
    assert issue.translation_placeholders == {"entries": "Kitchen, Kohler Anthem (me)"}

    # It follows the old entries as they are deleted, without a restart.
    await hass.config_entries.async_remove(sensate.entry_id)
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, "replaced_integration")
    assert issue.translation_placeholders == {"entries": "Kohler Anthem (me)"}
    await hass.config_entries.async_remove(anthem.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, "replaced_integration") is None


async def test_a_rejected_sign_in_while_seeding_reaches_the_caller(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """The seed used to log it as one more failed read, so nothing asked to sign in."""
    from custom_components.kohler_konnect.konnect import AuthError, credential_is_dead

    kohler.devices.append(dict(VALVE))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    coordinator = account(hass, config_entry)
    coordinator.auth.invalidate_access_token()
    kohler.token_queue.append(
        (400, {"error": "invalid_grant", "error_description": "AADB2C90080: expired"})
    )
    try:
        await coordinator._async_seed_state()
    except AuthError as err:
        assert credential_is_dead(err)
    else:
        raise AssertionError("the rejected credential was swallowed")
    await unload(hass, config_entry)


async def test_a_rejected_sign_in_during_a_shower_command_asks_to_sign_in_again(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """Shower commands caught only Kohler's own errors: this reached the user raw."""
    import pytest
    from homeassistant.config_entries import SOURCE_REAUTH
    from homeassistant.exceptions import HomeAssistantError

    kohler.devices.append(dict(VALVE))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    account(hass, config_entry).auth.invalidate_access_token()
    kohler.token_queue.append(
        (400, {"error": "invalid_grant", "error_description": "AADB2C90080: expired"})
    )
    with pytest.raises(HomeAssistantError, match="rejected the saved sign-in"):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.anthem_valve_shower_on"},
            blocking=True,
        )
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    await unload(hass, config_entry)
