"""Faucet fixes from the 2026-10-16 review: the safety limit, polls racing the stream,
re-reads per message, diagnostics, stream-only states, dispense timers and restores.

Each test here failed before its fix.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import async_get as async_get_restore_state
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.kohler_konnect.const import (
    DISPENSE_MAX,
    FAUCET_CONFIG_REFRESH_INTERVAL,
    FAUCET_SCAN_INTERVAL_IDLE,
    FAUCET_SCAN_INTERVAL_PUSH_ACTIVE,
)
from custom_components.kohler_konnect.faucet.coordinator import AUTO_OFF_RETRIES
from custom_components.kohler_konnect.konnect.faucet import FaucetEvent

from .conftest import (
    DEVICE_ID,
    FakeKohler,
    FakeMqttClient,
    faucet,
    feed_message,
    preset_message,
    water_message,
)

SWITCH = "switch.kitchen_water"
DISPENSING = "binary_sensor.kitchen_dispensing"
HANDLE = "sensor.kitchen_handle"
NUMBER = "number.kitchen_dispense_amount"
PRESET = "select.kitchen_preset"
STATE_PATH = f"/faucet-state/{DEVICE_ID}"
PRESETS = [
    {"experienceId": "p1", "title": "Pasta pot", "dispenseAmount": 3.0},
    {"experienceId": "p2", "title": "Kettle", "dispenseAmount": 1.2},
]


class SlowKohler(FakeKohler):
    """`FakeKohler` that can hold replies back until a test lets them go."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        # Held replies, by a fragment of the path, until the event is set.
        self.held: dict[str, asyncio.Event] = {}
        # Fragments whose held reply then fails, as a network error.
        self.fail_held: set[str] = set()

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        fragment = next((f for f in self.held if f in url.path), None)
        if fragment is None:
            return await super()._api(method, url, data)
        if fragment in self.fail_held:
            await self.held[fragment].wait()
            return self._respond(method, url, self.network_error())
        # Answered as of the request, as a real reply is, but delivered later.
        response = await super()._api(method, url, data)
        await self.held[fragment].wait()
        return response


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> SlowKohler:
    return SlowKohler(aioclient_mock)


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _spin(hass: HomeAssistant) -> None:
    """Let tasks run while a held reply keeps `async_block_till_done` from returning."""
    for _ in range(50):
        await asyncio.sleep(0)


async def _deliver(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, message: dict[str, Any]
) -> None:
    FakeMqttClient.instances[-1].deliver(message)
    await _advance(hass, freezer, timedelta(seconds=1))  # refresh cooldown


async def _verify_stream(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """A message about the faucet: the stream has proven itself."""
    await _deliver(hass, freezer, {"sku": "SEN", "deviceid": DEVICE_ID, "data": {}})


async def _water_on(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )


def _calls(kohler: FakeKohler, fragment: str) -> int:
    return sum(1 for _, url, _, _ in kohler.mocker.mock_calls if fragment in str(url))


def _actions(kohler: FakeKohler) -> list[str]:
    return [body["action"] for cmd, body in kohler.commands if cmd == "onoff"]


def _states_of(hass: HomeAssistant, entity_id: str) -> list[str]:
    seen: list[str] = []

    @callback
    def record(event: Event) -> None:
        seen.append(event.data["new_state"].state)

    async_track_state_change_event(hass, [entity_id], record)
    return seen


# --- 1. the safety limit ------------------------------------------------------------


async def test_water_stopped_during_a_restart_drops_the_safety_limit(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Seen running before the restart, off after it: not cut off at minute 10."""
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(seconds=10))
    # Turned off by hand while Home Assistant restarts.
    kohler.state["status"] = "Off"
    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert faucet(hass, setup_entry)._auto_off_at is None

    # Minute 9: someone uses the faucet by hand, and is still using it at minute 10.
    await _advance(hass, freezer, timedelta(minutes=9))
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(minutes=1, seconds=30))
    assert _actions(kohler) == ["ON"]


async def test_the_stream_seeing_the_water_run_and_stop_drops_the_safety_limit(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Water that ran and stopped between polls, as only the stream saw it."""
    await _verify_stream(hass, freezer)
    await _water_on(hass)
    await _deliver(hass, freezer, water_message("On"))
    await _deliver(hass, freezer, water_message("Off"))
    assert faucet(hass, push_entry)._auto_off_at is None

    await _advance(hass, freezer, timedelta(minutes=9))
    kohler.state["status"] = "On"
    await _deliver(hass, freezer, water_message("On"))
    await _advance(hass, freezer, timedelta(minutes=1, seconds=30))
    assert _actions(kohler) == ["ON"]


async def test_a_poll_lagging_the_stream_keeps_the_safety_limit(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """After the stream saw the water running, a poll that still says "off" is stale."""
    await _verify_stream(hass, freezer)
    await _water_on(hass)
    await _deliver(hass, freezer, water_message("On"))
    await _advance(hass, freezer, FAUCET_SCAN_INTERVAL_PUSH_ACTIVE)
    assert faucet(hass, push_entry)._auto_off_at is not None

    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(minutes=10))
    assert _actions(kohler) == ["ON", "OFF"]


async def test_no_off_for_water_already_off(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The command never took: every read since says off, so there is nothing to stop."""
    await _water_on(hass)
    await _advance(hass, freezer, timedelta(minutes=10, seconds=1))
    assert _actions(kohler) == ["ON"]
    assert faucet(hass, setup_entry)._auto_off_at is None


async def test_off_sent_when_nothing_was_learned_since_the_command(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The "off" read right after the command is no proof; failed polls are none."""
    await _water_on(hass)
    kohler.fail_api(STATE_PATH, *[kohler.network_error() for _ in range(100)])
    await _advance(hass, freezer, timedelta(minutes=10, seconds=1))
    assert _actions(kohler) == ["ON", "OFF"]


async def test_auto_off_retries_are_capped(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A faucet that stays unreachable is not retried, and warned about, for ever."""
    caplog.set_level(logging.WARNING)
    await _water_on(hass)
    kohler.state["status"] = "On"
    kohler.fail_api("/onoff", *[kohler.network_error() for _ in range(50)])

    await _advance(hass, freezer, timedelta(minutes=10, seconds=1))
    for _ in range(AUTO_OFF_RETRIES + 5):
        await _advance(hass, freezer, timedelta(minutes=1))
    # The ON, then the first OFF and each retry.
    assert _calls(kohler, "/onoff") == 1 + 1 + AUTO_OFF_RETRIES
    assert faucet(hass, setup_entry)._auto_off_at is None
    retrying = [r for r in caplog.records if "Trying again" in r.getMessage()]
    assert len(retrying) == 1
    assert "Giving up" in caplog.text


async def test_no_auto_off_retry_after_unload(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """An OFF that fails after the entry is unloaded is not retried by the old copy."""
    await _water_on(hass)
    kohler.state["status"] = "On"
    kohler.held["/onoff"] = gate = asyncio.Event()
    kohler.fail_held.add("/onoff")

    freezer.tick(timedelta(minutes=10, seconds=1))
    async_fire_time_changed(hass)
    await _spin(hass)
    attempts = _calls(kohler, "/onoff")
    assert attempts == 2  # the ON, and the OFF still on its way

    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    gate.set()
    await _spin(hass)
    kohler.held.clear()
    await _advance(hass, freezer, timedelta(minutes=3))
    assert _calls(kohler, "/onoff") == attempts


# --- 2. polls and the stream ----------------------------------------------------------


async def test_stream_report_during_the_state_read_is_kept(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """A poll that read "off" before the stream said "on" does not turn it back off."""
    states = _states_of(hass, SWITCH)
    kohler.held["/faucet-state/"] = gate = asyncio.Event()
    freezer.tick(FAUCET_SCAN_INTERVAL_IDLE)
    async_fire_time_changed(hass)
    await _spin(hass)  # the poll has read "Off" and waits for its reply

    freezer.tick(timedelta(seconds=1))
    kohler.state["status"] = "On"
    FakeMqttClient.instances[-1].deliver(water_message("On"))
    await _spin(hass)
    assert hass.states.get(SWITCH).state == "on"

    gate.set()
    kohler.held.clear()
    await _advance(hass, freezer, timedelta(seconds=1))
    await _advance(hass, freezer, timedelta(seconds=1))
    assert states == ["on"]
    assert hass.states.get(SWITCH).state == "on"


async def test_slow_side_reads_do_not_hold_back_newer_state(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """A slow configuration read neither delays the others nor brings back old state."""
    states = _states_of(hass, SWITCH)
    kohler.held["/faucet-configuration/"] = gate = asyncio.Event()
    presets = _calls(kohler, "/faucet-experience")
    freezer.tick(FAUCET_CONFIG_REFRESH_INTERVAL)
    async_fire_time_changed(hass)
    await _spin(hass)
    # Read beside the configuration, not after it.
    assert _calls(kohler, "/faucet-experience") == presets + 1

    freezer.tick(timedelta(seconds=1))
    kohler.state["status"] = "On"
    FakeMqttClient.instances[-1].deliver(water_message("On"))
    await _spin(hass)

    gate.set()
    kohler.held.clear()
    await _advance(hass, freezer, timedelta(seconds=1))
    assert states == ["on"]
    assert hass.states.get(SWITCH).state == "on"


# --- 3. what a message re-reads -----------------------------------------------------


async def test_water_reports_do_not_reread_the_configuration(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    await _advance(hass, freezer, timedelta(seconds=5))
    side = ("/faucet-configuration/", "/faucet-experience", "/customer-experience/")
    before = {fragment: _calls(kohler, fragment) for fragment in side}
    polls = kohler.state_polls
    for status in ("On", "Off", "On", "Off"):
        kohler.state["status"] = status
        await _deliver(hass, freezer, water_message(status))
    assert {fragment: _calls(kohler, fragment) for fragment in side} == before
    assert kohler.state_polls == polls + 4


@pytest.mark.parametrize(
    "message",
    [
        feed_message("SENSATE_LEAK_DETECTED_ALT"),
        preset_message("Tea", "ON"),
        feed_message("INSTALL_FIRMWARE_STS", status="Installed"),
        {"sku": "SEN", "deviceid": DEVICE_ID, "data": {}},
    ],
    ids=["leak", "preset", "firmware", "unknown"],
)
async def test_other_messages_still_reread_the_configuration(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
    message: dict[str, Any],
) -> None:
    await _advance(hass, freezer, timedelta(seconds=5))
    before = _calls(kohler, "/faucet-configuration/")
    await _deliver(hass, freezer, message)
    assert _calls(kohler, "/faucet-configuration/") == before + 1


async def test_an_empty_faucet_preset_list_is_kept(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: SlowKohler
) -> None:
    """No presets on the faucet: the account's legacy list does not bring old ones back."""
    kohler.faucet_presets = []
    kohler.presets = PRESETS  # deleted in the app, still in `sensateExperiences`
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    coordinator = faucet(hass, config_entry)
    assert coordinator.presets == {}
    assert coordinator.preset_source == "faucet-experience"
    assert _calls(kohler, "/customer-experience/") == 0
    assert hass.states.get(PRESET) is None


# --- 4. diagnostics ---------------------------------------------------------------


async def test_cloud_addressing_stays_out_of_faucet_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """`iot` and connection strings, however Kohler shapes the configuration reply."""
    kohler.config["iot"] = {
        "connectionString": (
            f"HostName=kohler.azure-devices.net;DeviceId={DEVICE_ID};"
            "SharedAccessKey=c2VjcmV0LWtleS0x"
        ),
        "hubName": "kohler.azure-devices.net",
    }
    kohler.config["firmwareUpdate"] = {
        "status": "Completed",
        "url": "https://fw.blob.core.windows.net/sen.bin?sv=1&sig=fw-sig-0f9e8d7c",
    }
    kohler.config["twin"] = {
        "link": "HostName=hub.example.net;DeviceId=x;SharedAccessKey=b3RoZXIta2V5",
        "token": "SharedAccessSignature sr=hub.example.net&sig=c2lnLTE&se=1",
    }
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )

    text = json.dumps(diagnostics).lower()
    for secret in (
        "c2VjcmV0LWtleS0x",
        "b3RoZXIta2V5",
        "c2lnLTE",
        "fw-sig-0f9e8d7c",
        "sharedaccess",
        "azure-devices.net",
        "hub.example.net",
    ):
        assert secret.lower() not in text, secret
    configuration = diagnostics["faucets"][0]["configuration"]
    assert "iot" not in configuration
    # Everything else is still reported.
    assert configuration["firmwareUpdate"]["status"] == "Completed"
    assert configuration["configuration"]["about"]["hardware"] == "CC3235SF"


# --- 5. states only the stream reported ---------------------------------------------


async def test_a_handle_position_only_the_stream_reported(
    hass: HomeAssistant, push_entry: MockConfigEntry
) -> None:
    """An enum sensor rejects a state missing from its options; the stream's count."""
    coordinator = faucet(hass, push_entry)
    coordinator._note_event(FaucetEvent(status="On", handle="HALF"))
    assert hass.states.get(HANDLE).state == "half"
    assert "half" in hass.states.get(HANDLE).attributes["options"]


# --- 6. dispense timers -------------------------------------------------------------


async def test_a_preset_does_not_cancel_a_dispenses_timer(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Home Assistant's dispense still ends on time when an app preset ran meanwhile."""
    await _verify_stream(hass, freezer)
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: "button.kitchen_dispense_250_ml"},
        blocking=True,
    )
    # Seen by the stream, so only the stream (or the limit) ends it; its end never comes.
    kohler.state["status"] = "On"
    await _deliver(hass, freezer, water_message("On"))
    await _deliver(hass, freezer, preset_message("New Preset", "ON"))
    await _deliver(hass, freezer, preset_message("New Preset", "OFF"))
    assert hass.states.get(DISPENSING).state == "on"

    # The polls while water runs, every 30 s, the last 93 s after the press...
    for _ in range(3):
        await _advance(hass, freezer, FAUCET_SCAN_INTERVAL_PUSH_ACTIVE)
    assert hass.states.get(DISPENSING).state == "on"
    # ...and a second past the dispense's limit, before the next.
    await _advance(hass, freezer, DISPENSE_MAX - timedelta(seconds=91))
    assert hass.states.get(DISPENSING).state == "off"


# --- 7. the restored dispense amount ------------------------------------------------


@pytest.mark.parametrize(
    ("stored_ml", "shown"), [(10.0, 0.5), (11360.0, 384.0)], ids=["min", "max"]
)
async def test_restored_amount_kept_within_us_units(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
    stored_ml: float,
    shown: float,
) -> None:
    """Metric limits restored as US units, which start at 0.5 fl oz and end at 384."""
    kohler.water_units = "Standard"
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(NUMBER, str(stored_ml)),
                {
                    "native_value": stored_ml,
                    "native_unit_of_measurement": "mL",
                    "native_min_value": 10.0,
                    "native_max_value": 11360.0,
                    "native_step": 10.0,
                },
            ),
        ),
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    state = hass.states.get(NUMBER)
    assert float(state.state) == shown
    assert state.attributes["min"] <= float(state.state) <= state.attributes["max"]


# --- 8. the restored preset choice -------------------------------------------------


async def test_choice_restored_by_id_after_a_rename(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: SlowKohler
) -> None:
    """Renamed in the app while Home Assistant was down: still chosen."""
    kohler.presets = [PRESETS[0], {**PRESETS[1], "title": "Tea kettle"}]
    mock_restore_cache_with_extra_data(
        hass, ((State(PRESET, "Kettle"), {"preset_id": "p2"}),)
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert hass.states.get(PRESET).state == "Tea kettle"


async def test_choice_restored_by_name_when_its_id_is_gone(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: SlowKohler
) -> None:
    """Deleted and saved again under the same name: the new one, not the first."""
    kohler.presets = [
        PRESETS[0],
        {"experienceId": "p7", "title": "Kettle", "dispenseAmount": 1.0},
    ]
    mock_restore_cache_with_extra_data(
        hass, ((State(PRESET, "Kettle"), {"preset_id": "p2"}),)
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert hass.states.get(PRESET).state == "Kettle"
    assert faucet(hass, config_entry).preset_choice == "p7"


async def test_choice_saved_with_its_id(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: SlowKohler
) -> None:
    kohler.presets = PRESETS
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: PRESET, "option": "Kettle"},
        blocking=True,
    )
    stored = {
        s.state.entity_id: s.extra_data
        for s in async_get_restore_state(hass).async_get_stored_states()
    }
    assert stored[PRESET] is not None
    assert stored[PRESET].as_dict() == {"preset_id": "p2"}
