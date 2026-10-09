"""The faucets on the account's one MQTT stream — shared with the showers.

Adapted from the faucet integration's own instant-update tests. What they checked about its
private feed — one per account, shared between per-faucet entries — is now the stream every
device on the entry shares, so those became the tests at the end.
"""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_konnect.const import (
    FAUCET_PUSH_GRACE,
    FAUCET_SCAN_INTERVAL_ACTIVE,
    FAUCET_SCAN_INTERVAL_IDLE,
    FAUCET_SCAN_INTERVAL_PUSH,
    FAUCET_SCAN_INTERVAL_PUSH_ACTIVE,
)
from custom_components.kohler_konnect.konnect.faucet import FaucetEvent

from .conftest import (
    BAR,
    DEVICE_ID,
    MOBILE_ID,
    VALVE,
    FakeKohler,
    FakeMqttClient,
    account,
    faucet,
    feed_message,
    wait_for,
)


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _faucet_message(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    rid: str = "1",
    device: str = DEVICE_ID,
) -> None:
    """Deliver a message about a faucet and let its refresh run."""
    FakeMqttClient.instances[-1].deliver(
        {"sku": "SEN", "deviceid": device, "data": {}}, rid=rid
    )
    # At most the 1 s refresh cooldown after the previous refresh.
    await _advance(hass, freezer, timedelta(seconds=1))


async def test_message_for_faucet_refreshes_and_relaxes_polling(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    coordinator = faucet(hass, push_entry)
    (client,) = FakeMqttClient.instances
    polls = kohler.state_polls
    kohler.state["status"] = "On"

    client.deliver({"sku": "SEN", "deviceid": DEVICE_ID, "data": {}}, rid="42")
    await _advance(hass, freezer, timedelta(seconds=1))

    assert client.published == [
        ("$iothub/methods/res/200/?$rid=42", b'{"status":"received"}')
    ]
    assert kohler.state_polls == polls + 1
    assert hass.states.get("switch.kitchen_water").state == "on"
    assert coordinator.push_verified

    kohler.state["status"] = "Off"
    await coordinator.async_refresh()
    assert coordinator.update_interval == FAUCET_SCAN_INTERVAL_PUSH


async def test_device_ids_match_without_regard_to_case(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _faucet_message(hass, freezer, device=DEVICE_ID.upper())
    assert faucet(hass, push_entry).push_verified


async def test_other_devices_are_ignored(
    hass: HomeAssistant, push_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    (client,) = FakeMqttClient.instances
    polls = kohler.state_polls
    client.deliver({"sku": "GCS", "deviceid": "gcs-not-on-this-entry"}, rid="7")
    await hass.async_block_till_done()

    assert len(client.published) == 1  # still acknowledged
    assert kohler.state_polls == polls
    assert not faucet(hass, push_entry).push_verified
    assert faucet(hass, push_entry).update_interval == FAUCET_SCAN_INTERVAL_IDLE


async def test_messages_without_a_device_are_ignored(
    hass: HomeAssistant, push_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """Only a message naming this faucet proves the stream reports it."""
    (client,) = FakeMqttClient.instances
    polls = kohler.state_polls
    client.deliver({"sku": "SEN", "data": {"code": "SENSATE_STS"}}, rid="8")
    await hass.async_block_till_done()

    assert len(client.published) == 1  # still acknowledged
    assert kohler.state_polls == polls
    assert not faucet(hass, push_entry).push_verified


async def test_stream_state_applies_at_once(
    hass: HomeAssistant, push_entry: MockConfigEntry
) -> None:
    """Like the app, the stream's water and handle state count before the re-read."""
    coordinator = faucet(hass, push_entry)
    # The event alone, without the re-read that follows it.
    coordinator._note_event(FaucetEvent(status="On", handle="CLOSED"))
    assert coordinator.handle_closed
    assert coordinator.water_running
    assert hass.states.get("switch.kitchen_water").state == "on"


async def test_leak_alert(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    """A real-time leak alert turns Leak on before the history lists it."""
    leak = "binary_sensor.kitchen_leak"
    assert hass.states.get(leak).state == "off"
    FakeMqttClient.instances[-1].deliver(feed_message("SENSATE_LEAK_DETECTED_ALT"))
    await _advance(hass, freezer, timedelta(seconds=1))
    state = hass.states.get(leak)
    assert state.state == "on"
    assert state.attributes["last_detected"] is not None

    # Kept across a reload until it is cleared.
    assert await hass.config_entries.async_reload(push_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(leak).state == "on"

    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: "button.kitchen_clear_leak_alert"},
        blocking=True,
    )
    assert hass.states.get(leak).state == "off"


async def test_firmware_install_result_rereads_firmware(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    update = "update.kitchen_firmware_status"
    kohler.config["configuration"]["about"]["firmware"]["version"] = "17.1"
    kohler.firmware = {"firmwareUpdateAvailable": False, "firmware": "17.1"}

    FakeMqttClient.instances[-1].deliver(
        feed_message("INSTALL_FIRMWARE_STS", status="Installed", version="17.1")
    )
    await _advance(hass, freezer, timedelta(seconds=1))
    state = hass.states.get(update)
    assert state.attributes["installed_version"] == "17.1"
    assert state.state == "off"


async def test_removal_survives_unregister_failure(
    hass: HomeAssistant,
    hass_storage: dict,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.fail_api(f"/{MOBILE_ID}", kohler.network_error())
    await hass.config_entries.async_remove(push_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(push_entry.entry_id) is None
    assert not [key for key in hass_storage if push_entry.entry_id in key]


async def test_reconnects_with_fresh_credentials_and_same_identity(
    hass: HomeAssistant, push_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    stream = account(hass, push_entry).stream
    first = FakeMqttClient.instances[0]
    first.drop()
    await wait_for(
        hass,
        lambda: len(FakeMqttClient.instances) == 2 and stream.connected,
        "the reconnection",
    )
    assert first.stopped
    assert FakeMqttClient.instances[1].password == "SharedAccessSignature sig-2"
    assert {r["mobileDeviceId"] for r in kohler.push_registrations} == {MOBILE_ID}


async def test_identity_reused_after_reload(
    hass: HomeAssistant, push_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    assert await hass.config_entries.async_reload(push_entry.entry_id)
    await hass.async_block_till_done()
    assert FakeMqttClient.instances[0].stopped
    first, second = kohler.push_registrations
    assert first["mobileDeviceId"] == second["mobileDeviceId"] == MOBILE_ID


async def test_refused_connection_retries(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    FakeMqttClient.refuse = True
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    stream = account(hass, config_entry).stream
    await wait_for(hass, lambda: len(FakeMqttClient.instances) >= 2, "a retry")
    assert not stream.connected
    # Polling keeps working meanwhile.
    assert hass.states.get("sensor.kitchen_status").state == "off"

    FakeMqttClient.refuse = False
    await wait_for(hass, lambda: stream.connected, "the retry to connect")


async def test_registration_failure_at_startup_is_retried(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """Until 2026-10-08 a failed first registration was never retried."""
    kohler.fail_api("/mobile/settings", (500, {}))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert hass.states.get("sensor.kitchen_status").state == "off"
    stream = account(hass, config_entry).stream
    await wait_for(hass, lambda: stream.connected, "the retry to connect")
    assert len(kohler.push_registrations) == 1  # the failed one never reached the fake


async def test_unload_stops_the_connection(
    hass: HomeAssistant, push_entry: MockConfigEntry
) -> None:
    assert await hass.config_entries.async_unload(push_entry.entry_id)
    assert FakeMqttClient.instances[0].stopped


# --- relying on the stream ------------------------------------------------------


async def test_trusted_stream_relaxes_polling_while_water_runs(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    coordinator = faucet(hass, push_entry)
    kohler.state["status"] = "On"
    await _faucet_message(hass, freezer)
    assert hass.states.get("switch.kitchen_water").state == "on"
    assert coordinator.update_interval == FAUCET_SCAN_INTERVAL_PUSH_ACTIVE

    polls = kohler.state_polls
    await _advance(hass, freezer, FAUCET_SCAN_INTERVAL_ACTIVE)
    assert kohler.state_polls == polls
    await _advance(
        hass, freezer, FAUCET_SCAN_INTERVAL_PUSH_ACTIVE - FAUCET_SCAN_INTERVAL_ACTIVE
    )
    assert kohler.state_polls == polls + 1


async def test_unannounced_change_stops_relying_on_the_stream(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    coordinator = faucet(hass, push_entry)
    await _faucet_message(hass, freezer)
    assert coordinator.update_interval == FAUCET_SCAN_INTERVAL_PUSH

    # Water turned on by hand, and the stream says nothing.
    kohler.state["status"] = "On"
    await _advance(hass, freezer, FAUCET_SCAN_INTERVAL_PUSH)
    assert hass.states.get("switch.kitchen_water").state == "on"
    assert coordinator.push_trusted  # its message may still be on the way

    await _advance(hass, freezer, FAUCET_PUSH_GRACE)
    assert not coordinator.push_trusted
    assert coordinator.push_missed == 1
    assert coordinator.update_interval == FAUCET_SCAN_INTERVAL_ACTIVE

    # The next message earns the trust back.
    await _faucet_message(hass, freezer, rid="2")
    assert coordinator.push_trusted
    assert coordinator.update_interval == FAUCET_SCAN_INTERVAL_PUSH_ACTIVE


async def test_change_announced_just_after_a_poll_keeps_trust(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    coordinator = faucet(hass, push_entry)
    await _faucet_message(hass, freezer)
    await _advance(hass, freezer, timedelta(seconds=5))

    # A command re-reads the faucet at once, before the stream announces it.
    kohler.state["status"] = "On"
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "switch.kitchen_water"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("switch.kitchen_water").state == "on"
    await _faucet_message(hass, freezer, rid="2")

    await _advance(hass, freezer, FAUCET_PUSH_GRACE)
    assert coordinator.push_trusted
    assert coordinator.push_missed == 0


async def test_dropped_stream_falls_back_and_catches_up(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    coordinator = faucet(hass, push_entry)
    stream = account(hass, push_entry).stream
    await _faucet_message(hass, freezer)
    assert coordinator.update_interval == FAUCET_SCAN_INTERVAL_PUSH

    polls = kohler.state_polls
    # Keep it down until the test says so.
    FakeMqttClient.refuse = True
    FakeMqttClient.instances[0].drop()
    await _advance(hass, freezer, timedelta(seconds=1))  # refresh cooldown
    # Re-read at once, and poll as usual until the stream is back.
    assert kohler.state_polls > polls
    assert not coordinator.push_trusted
    await coordinator.async_refresh()
    assert coordinator.update_interval == FAUCET_SCAN_INTERVAL_IDLE

    # A change while the stream was down is not one it missed.
    kohler.state["status"] = "On"
    FakeMqttClient.refuse = False
    # The frozen clock holds the stream's retry sleep too; step it along.
    for _ in range(50):
        if stream.connected:
            break
        await _advance(hass, freezer, timedelta(seconds=0.1))
    # The retry then stops the old client on a worker thread, in real time the frozen
    # clock does not count — on a slow CI runner, longer than the steps left. So wait for
    # the reconnect on the real clock too.
    await wait_for(hass, lambda: stream.connected, "the stream to reconnect")
    await _advance(hass, freezer, timedelta(seconds=1))
    assert hass.states.get("switch.kitchen_water").state == "on"
    await _advance(hass, freezer, FAUCET_PUSH_GRACE)
    assert coordinator.push_missed == 0


# --- one stream for everything on the account -------------------------------------


async def test_two_faucets_share_one_connection(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.devices.append(dict(BAR))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    stream = account(hass, config_entry).stream
    await wait_for(hass, lambda: stream.connected, "the stream")
    kitchen, bar = account(hass, config_entry).faucets
    assert len(FakeMqttClient.instances) == 1
    assert len(kohler.push_registrations) == 1

    # Each message goes to the faucet it names, and to that one only.
    await _faucet_message(hass, freezer, device="sen-bar")
    assert bar.push_verified
    assert not kitchen.push_verified


async def test_a_faucet_and_a_valve_share_one_connection(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.devices.append(dict(VALVE))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    stream = account(hass, config_entry).stream
    await wait_for(hass, lambda: stream.connected, "the stream")
    assert len(FakeMqttClient.instances) == 1

    # A valve message is the valve's; the faucet does not take it as its own.
    FakeMqttClient.instances[0].deliver(
        {"sku": "GCS", "deviceid": VALVE["deviceId"], "data": {"code": "GCS_SOLO_STS"}}
    )
    await _advance(hass, freezer, timedelta(seconds=1))
    assert not faucet(hass, config_entry).push_verified
    await _faucet_message(hass, freezer)
    assert faucet(hass, config_entry).push_verified
