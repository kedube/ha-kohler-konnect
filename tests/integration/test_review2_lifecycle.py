"""The account's lifecycle, as reviewed a second time on 2026-10-16.

What a REST read may overwrite when a push or a write overtakes it, entities left
unavailable after a failed refresh, a reload during a token refresh, Home Assistant
stopping during setup, the stream starting beside the setup reads so that each device is
read once, faults that went stale, the stream-health sensor during a gap, a controller
layout learned late, and a fault log that could not be read.

Runs against `LifecycleKohler` from `test_review_lifecycle`, whose gates hold a request,
matched on the end of its path, until a test lets it through.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp import ClientError
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect import coordinator as coordinator_module
from custom_components.kohler_konnect import diagnostics
from custom_components.kohler_konnect.const import (
    CONF_REFRESH_TOKEN,
    DOMAIN,
    FIRMWARE_CHECK_INTERVAL,
)
from custom_components.kohler_konnect.konnect.const import (
    WARMUP_ALL_OUTLETS_NOW,
    WARMUP_DISABLED,
)
from custom_components.kohler_konnect.konnect.valve_hex import decode_word

from .conftest import (
    DEVICE_ID,
    FAUCET,
    TENANT_ID,
    USERNAME,
    FakeMqttClient,
    account,
    unload,
    wait_for,
)
from .test_coordinator import (
    LEFT,
    MAIN,
    REJECTED,
    RIGHT,
    SHOWER_DATA,
    hub_message,
    hub_state,
    prompts,
    push,
    reconnect,
    solo_message,
    start,
    state,
    stream,
    valve_state,
    valve_zone,
    words,
)
from .test_review_lifecycle import (
    OWNER_FAULT,
    SERVER_ERROR,
    LifecycleKohler,
    device_of,
    fault_log,
    until,
)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> LifecycleKohler:
    return LifecycleKohler(aioclient_mock)


@pytest.fixture
async def shower_entry(hass: HomeAssistant) -> AsyncGenerator[MockConfigEntry]:
    """An entry as the setup flow leaves it for a K-28210 account. Not set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Kohler Konnect ({USERNAME})",
        unique_id=USERNAME,
        data=dict(SHOWER_DATA),
    )
    entry.add_to_hass(hass)
    yield entry
    await unload(hass, entry)


@pytest.fixture(autouse=True)
def prompt_usage_reads() -> Generator[None]:
    """Re-read usage the moment a shower ends rather than 90 s later."""
    with (
        patch.object(coordinator_module, "USAGE_REFRESH_DELAY_SECONDS", 0),
        patch.object(coordinator_module, "USAGE_RETRY_DELAY_SECONDS", 0),
    ):
        yield


@pytest.fixture
def built() -> Generator[list[Any]]:
    """Every account coordinator constructed, so a test can reach one mid-setup."""
    coordinators: list[Any] = []
    cls = coordinator_module.KohlerKonnectCoordinator
    original = cls.__init__

    def record(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        coordinators.append(self)

    with patch.object(cls, "__init__", record):
        yield coordinators


def warm_message(device_id: str, mode: str) -> dict[str, Any]:
    """A `GCS_WARM_STS`: the warm-up mode, and nothing else."""
    return {
        "sku": "GCS",
        "deviceid": device_id,
        "data": {
            "type": "Status",
            "code": "GCS_WARM_STS",
            "attributes": [{"code": "GCS_WARM_STS", "warmup": mode}],
        },
    }


def faucet_message(device_id: str = DEVICE_ID) -> dict[str, Any]:
    return {"sku": "SEN", "deviceid": device_id, "data": {}}


# =========================================================================== #
# 1. A REST read overtaken by a push keeps only what the push carried
# =========================================================================== #


async def test_a_warmup_announcement_during_a_reseed_keeps_the_rest_of_the_reply(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """`GCS_WARM_STS` carries the warm-up mode alone; the reply's zone words still apply.

    The whole reply used to be dropped: a shower stopped while the stream was down stayed
    shown running, and every later write rebuilt both zones from it.
    """
    kohler.valve_states[LEFT] = valve_state(valve_zone(1))
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    assert valve.gcs_state.outlets == [True, False, False]

    kohler.valve_states[LEFT] = valve_state()  # stopped, warm-up off, per the cloud
    gate = kohler.hold(f"/gcs-state/{LEFT}")
    coordinator._handle_connected()
    await kohler.until_held(f"/gcs-state/{LEFT}")
    stream().deliver(warm_message(LEFT, WARMUP_ALL_OUTLETS_NOW))
    await until(
        lambda: valve.gcs_state.warmup_mode == WARMUP_ALL_OUTLETS_NOW,
        "the announcement",
    )
    gate.set()
    await hass.async_block_till_done()

    assert valve.gcs_state.outlets == [False, False, False]
    assert state(hass, "switch.anthem_valve_rainhead_1") == "off"
    # The announcement is newer than the reply's mode, and stands.
    assert valve.gcs_state.warmup_mode == WARMUP_ALL_OUTLETS_NOW


async def test_a_status_report_during_a_reseed_does_not_hide_a_warmup_change(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """`GCS_SOLO_STS` carries no warm-up mode, so the reply's mode still applies.

    The disable auto-restore exists for — the hub's web UI, signed in while the stream was
    down — went unseen whenever the running shower reported during the reseed.
    """
    running = valve_state(valve_zone(1))
    running["state"]["warmUpState"]["warmUp"] = WARMUP_ALL_OUTLETS_NOW
    kohler.valve_states[LEFT] = running
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    assert valve.gcs_state.warmup_mode == WARMUP_ALL_OUTLETS_NOW

    kohler.valve_states[LEFT] = valve_state(valve_zone(1))  # warm-up disabled meanwhile
    gate = kohler.hold(f"/gcs-state/{LEFT}")
    coordinator._handle_connected()
    await kohler.until_held(f"/gcs-state/{LEFT}")
    heard = len(coordinator._recent_messages)
    stream().deliver(solo_message(LEFT, "0184C801"))
    await until(lambda: len(coordinator._recent_messages) > heard, "the report")
    gate.set()
    await hass.async_block_till_done()

    assert valve.gcs_state.warmup_mode == WARMUP_DISABLED
    assert valve.gcs_state.outlets == [True, False, False]


async def test_an_accessory_report_during_a_reseed_keeps_the_reply_for_the_zones(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """`MUSIC_STS` reports the amplifier alone; the zones in `hub-state` still apply."""
    kohler.add_controller()
    kohler.hub_states[MAIN] = hub_state(1, status="ON")
    coordinator = await start(hass, shower_entry)
    controller = coordinator.controllers[0]
    assert controller.water_is_running is True

    kohler.hub_states[MAIN] = hub_state()  # water and music off, per the cloud
    gate = kohler.hold(f"/hub-state/{MAIN}")
    coordinator._handle_connected()
    await kohler.until_held(f"/hub-state/{MAIN}")
    stream().deliver(
        hub_message(MAIN, "MUSIC_STS", {"component": "amplifier", "status": "ON"})
    )
    await until(lambda: controller.state.music_on is True, "the music report")
    gate.set()
    await hass.async_block_till_done()

    assert controller.water_is_running is False
    assert state(hass, "binary_sensor.anthem_plus_zone_1_outlet_1") == "off"
    assert controller.state.music_on is True


# =========================================================================== #
# 2. A REST read must not end a write's overlay with words from before it
# =========================================================================== #


@pytest.mark.parametrize("during", [True, False], ids=["during-read", "before-read"])
async def test_a_reseed_does_not_undo_a_write_its_reply_predates(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: LifecycleKohler,
    during: bool,
) -> None:
    """The next write builds on the last one sent, not on a reply from before it.

    The reply put new words in place, which `_valve_baseline` reads as the valve
    reporting — so a second outlet opened just after the first closed the first again.
    """
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    if not during:
        await valve.async_set_zone_outlet(1, 1, True)
    gate = kohler.hold(f"/gcs-state/{LEFT}")
    coordinator._handle_connected()
    await kohler.until_held(f"/gcs-state/{LEFT}")
    if during:
        await valve.async_set_zone_outlet(1, 1, True)
    gate.set()
    await hass.async_block_till_done()

    await valve.async_set_zone_outlet(1, 2, True)
    first, second = kohler.posted("/solowritesystem")
    assert decode_word(words(first)[0]).outlet_mask == 0b001
    assert decode_word(words(second)[0]).outlet_mask == 0b011


# =========================================================================== #
# 3. A failed refresh, then a message
# =========================================================================== #


async def test_after_a_failed_refresh_a_message_makes_every_device_available(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """The account is available again, so every device's entities must hear so."""
    kohler.add_valve(RIGHT, "Tub")
    kohler.add_controller(MAIN, "Main Bath")
    coordinator = await start(hass, shower_entry)
    owners = device_of(hass)
    coordinator.auth.invalidate_access_token()
    kohler.token_queue.append(ClientError("connection reset"))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success
    others = [
        entity_id
        for entity_id, owner in owners.items()
        if owner in (RIGHT, MAIN) and hass.states.get(entity_id) is not None
    ]
    assert others
    assert {state(hass, entity_id) for entity_id in others} == {"unavailable"}

    await push(hass, solo_message(LEFT, "0184C801"))
    assert coordinator.last_update_success
    assert [e for e in others if state(hass, e) == "unavailable"] == []


# =========================================================================== #
# 4. A reload during a token refresh
# =========================================================================== #


class RotatingKohler(LifecycleKohler):
    """Retires each refresh token once redeemed, as B2C does, and can hold token requests."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        self.redeemed: set[str] = set()
        self.token_gates: list[asyncio.Event] = []
        self.token_hold = False
        super().__init__(mocker)

    async def _token(
        self, method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        if self.token_hold:
            gate = asyncio.Event()
            self.token_gates.append(gate)
            await gate.wait()
        token = dict(data).get("refresh_token")
        if token in self.redeemed:
            self.token_requests.append(dict(data))
            return self._respond(method, url, REJECTED)
        if token:
            self.redeemed.add(token)
        return await super()._token(method, url, data)


async def test_a_reload_during_a_token_refresh_keeps_the_sign_in(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The next coordinator must start from the token the refresh in flight rotates to.

    It used to be built from the entry before that refresh had stored its token, so its
    own first refresh redeemed a token B2C had just retired: rejected, and the owner was
    asked to sign in again for nothing.
    """
    kohler = RotatingKohler(aioclient_mock)
    old = await start(hass, shower_entry)
    kohler.token_hold = True
    old.auth.invalidate_access_token()
    read = hass.async_create_task(old.client.async_get_gcs_state(LEFT))
    await until(lambda: len(kohler.token_gates) == 1, "the refresh")
    reload = hass.async_create_task(
        hass.config_entries.async_reload(shower_entry.entry_id)
    )
    # Long enough for a reload that does not wait to build the next coordinator and have
    # it ask for a token of its own.
    await asyncio.sleep(0.2)
    kohler.token_hold = False
    for gate in list(kohler.token_gates):
        gate.set()
    assert await reload
    await hass.async_block_till_done()
    await read

    assert shower_entry.state is ConfigEntryState.LOADED
    assert prompts(hass) == []
    assert account(hass, shower_entry) is not old
    assert shower_entry.data[CONF_REFRESH_TOKEN] not in kohler.redeemed


# =========================================================================== #
# 5. Home Assistant stopping during setup
# =========================================================================== #


async def test_stopping_during_setup_leaves_nothing_running(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: LifecycleKohler,
    built: list[Any],
) -> None:
    """The stop listener shut the coordinator down; setup then started it all again."""
    gate = kohler.hold(f"/gcsadvancestate/{LEFT}")
    setup = hass.async_create_task(
        hass.config_entries.async_setup(shower_entry.entry_id)
    )
    await kohler.until_held(f"/gcsadvancestate/{LEFT}")
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await until(lambda: built and built[-1].closed, "the shutdown")
    gate.set()
    assert not await setup
    await hass.async_block_till_done()

    (coordinator,) = built
    assert coordinator.stream is None
    assert all(client.stopped for client in FakeMqttClient.instances)
    assert coordinator._firmware_unsub is None
    assert coordinator._midnight_unsub is None
    assert coordinator._firmware_task is None
    assert coordinator._reseed_task is None
    assert coordinator.valves[0].cloud_watch._quiet_cancel is None
    assert shower_entry.entry_id not in hass.data.get(DOMAIN, {})


# =========================================================================== #
# 6. Each device read once at startup
# =========================================================================== #


def _reads(kohler: LifecycleKohler) -> dict[str, int]:
    """How often each of the setup reads went out."""
    markers = {
        "settings": f"/gcsadvancestate/{LEFT}",
        "state": f"/gcs-state/{LEFT}",
        "presets": f"/gcs-preset/{LEFT}",
        "hub configuration": f"/hub-configuration/{MAIN}",
        "hub state": f"/hub-state/{MAIN}",
        "faucet": f"/faucet-state/{DEVICE_ID}",
    }
    reads = {name: kohler.reads(marker) for name, marker in markers.items()}
    # `/about` sits under the configuration's own path; counted apart.
    reads["configuration"] = kohler.reads(f"/gcs-configuration/{LEFT}") - kohler.reads(
        f"/gcs-configuration/{LEFT}/about"
    )
    return reads


@pytest.fixture
def whole_account(kohler: LifecycleKohler) -> LifecycleKohler:
    kohler.add_controller()
    kohler.devices.append(dict(FAUCET))
    return kohler


def connected_once(built: list[Any]) -> bool:
    """Whether the coordinator being set up has handled the stream's first connect."""
    return bool(built) and getattr(built[-1], "stream_connects", 0) == 1


async def test_a_stream_up_before_the_state_reads_spares_every_second_read(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    whole_account: LifecycleKohler,
    built: list[Any],
) -> None:
    """The stream starts beside the setup reads; reads sent once it is up need no repeat.

    Each start used to read every device twice: the setup seed, then the whole seed
    again seconds later when the stream first connected — the faucets too.
    """
    kohler = whole_account
    settings = kohler.hold(f"/gcsadvancestate/{LEFT}")
    configuration = kohler.hold(f"/hub-configuration/{MAIN}")
    setup = hass.async_create_task(
        hass.config_entries.async_setup(shower_entry.entry_id)
    )
    await until(lambda: connected_once(built), "the stream")
    settings.set()
    configuration.set()
    assert await setup
    await hass.async_block_till_done()

    assert _reads(kohler) == dict.fromkeys(_reads(kohler), 1)

    # A reconnect still reads everything again: the gap can be hours.
    coordinator = account(hass, shower_entry)
    before = _reads(kohler)
    await reconnect(hass, coordinator)
    after = _reads(kohler)
    for name in ("settings", "state", "presets", "hub configuration", "hub state"):
        assert after[name] == before[name] + 1, name


async def test_a_stream_up_after_the_state_reads_reads_only_the_live_state_again(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    whole_account: LifecycleKohler,
    built: list[Any],
) -> None:
    """What changed between the setup read and the subscription must not be missed.

    Only the live state can have moved in those seconds, so only it is read again — and
    not the faucet's, whose first read has only just finished.
    """
    kohler = whole_account
    registration = kohler.hold("/mobile/settings")
    setup = hass.async_create_task(
        hass.config_entries.async_setup(shower_entry.entry_id)
    )
    await until(
        lambda: (
            kohler.reads(f"/gcs-state/{LEFT}") == 1
            and kohler.reads(f"/hub-state/{MAIN}") == 1
        ),
        "the setup's state reads",
    )
    kohler.valve_states[LEFT] = valve_state(valve_zone(2))  # changed meanwhile
    registration.set()
    assert await setup
    coordinator = account(hass, shower_entry)
    await wait_for(hass, lambda: coordinator.stream.connected, "the stream")
    await hass.async_block_till_done()

    assert _reads(kohler) == {
        "settings": 1,
        "state": 2,
        "presets": 1,
        "configuration": 1,
        "hub configuration": 1,
        "hub state": 2,
        "faucet": 1,
    }
    assert coordinator.valves[0].gcs_state.outlets == [False, True, False]


async def test_messages_from_before_the_account_read_reach_their_devices(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    whole_account: LifecycleKohler,
    built: list[Any],
) -> None:
    """The stream can deliver before any device exists to take a message; none is lost."""
    kohler = whole_account
    gate = kohler.hold(f"/customer-device/{TENANT_ID}")
    setup = hass.async_create_task(
        hass.config_entries.async_setup(shower_entry.entry_id)
    )
    await until(lambda: connected_once(built), "the stream")
    stream().deliver(solo_message(LEFT, "0184C801"))
    stream().deliver(faucet_message())
    await asyncio.sleep(0.05)
    gate.set()
    assert await setup
    await hass.async_block_till_done()

    coordinator = account(hass, shower_entry)
    assert [(r["device"], r["code"]) for r in coordinator._recent_messages] == [
        (LEFT, "GCS_SOLO_STS")
    ]
    assert coordinator.faucets[0].push_verified


# =========================================================================== #
# 7. Faults read twice a day, and kept when a read fails
# =========================================================================== #


async def test_faults_are_read_again_twice_a_day(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    shower_entry: MockConfigEntry,
    kohler: LifecycleKohler,
) -> None:
    """Nothing pushes a fault; on a stable stream a cleared one stayed on for days."""
    kohler.gcs_faults[LEFT] = fault_log(OWNER_FAULT)
    kohler.add_controller()
    coordinator = await start(hass, shower_entry)
    assert state(hass, "binary_sensor.anthem_valve_problem") == "on"
    assert [e["errorCode"] for e in coordinator.controllers[0].active_errors] == ["E12"]

    kohler.gcs_faults[LEFT] = fault_log({**OWNER_FAULT, "isActive": False})
    kohler.hub_errors[MAIN] = {"errorDetails": []}
    freezer.tick(FIRMWARE_CHECK_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert state(hass, "binary_sensor.anthem_valve_problem") == "off"
    assert coordinator.controllers[0].active_errors == []


async def test_a_failed_active_error_read_keeps_the_controllers_errors(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """`{}` is the client's answer to a read it could not make, not "no errors"."""
    kohler.add_controller()
    coordinator = await start(hass, shower_entry)
    assert state(hass, "binary_sensor.anthem_plus_problem") == "on"

    kohler.fail_every_time(f"/hub-diagnostics/{MAIN}/active", SERVER_ERROR)
    await reconnect(hass, coordinator)
    assert [e["errorCode"] for e in coordinator.controllers[0].active_errors] == ["E12"]
    assert state(hass, "binary_sensor.anthem_plus_problem") == "on"


# =========================================================================== #
# 8. The stream-health sensor during a gap
# =========================================================================== #


async def test_the_mqtt_connection_sensor_goes_off_when_the_stream_drops(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """Nothing told it of a drop, so it read connected through every outage."""
    await start(hass, shower_entry)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{LEFT}_mqtt_connection"
    )
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(shower_entry.entry_id)
    await hass.async_block_till_done()
    assert state(hass, entity_id) == "on"

    # Slow reconnects, so the gap lasts long enough to be seen.
    with (
        patch(
            "custom_components.kohler_konnect.konnect.mqtt.RECONNECT_MIN_SECONDS", 30
        ),
        patch(
            "custom_components.kohler_konnect.konnect.mqtt.RECONNECT_MAX_SECONDS", 30
        ),
    ):
        stream().drop()
        await hass.async_block_till_done()
        assert state(hass, entity_id) == "off"
        assert await hass.config_entries.async_unload(shower_entry.entry_id)


# =========================================================================== #
# 9. A controller's layout learned after its entities exist
# =========================================================================== #


async def test_a_controller_layout_found_after_the_entities_exist_reloads_once(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """Its configuration read fails at setup, so its entities are built from the entry's
    layout; the read that answers later must rebuild them, once."""
    kohler.add_controller(split=(3, 3))
    configuration = kohler.hub_configurations.pop(MAIN)  # read: 404 until put back
    first = await start(hass, shower_entry)
    assert first.controllers[0].model.total_outlets == 3
    assert hass.states.get("binary_sensor.anthem_plus_zone_2_outlet_1") is None

    kohler.hub_configurations[MAIN] = configuration
    stream().drop()
    await wait_for(
        hass,
        lambda: (
            (current := account(hass, shower_entry)) is not first
            and current.stream is not None
            and current.stream.connected
        ),
        "the reload",
    )
    await hass.async_block_till_done()
    second = account(hass, shower_entry)
    assert second.controllers[0].model.total_outlets == 6
    assert hass.states.get("binary_sensor.anthem_plus_zone_2_outlet_1") is not None

    # A later setup whose read fails again starts from the layout the controller
    # reported, so it cannot reload over the same answer again.
    kohler.fail_api(f"/hub-configuration/{MAIN}", SERVER_ERROR)
    assert await hass.config_entries.async_reload(shower_entry.entry_id)
    await hass.async_block_till_done()
    third = account(hass, shower_entry)
    assert third.controllers[0].model.total_outlets == 6
    await reconnect(hass, third)
    assert account(hass, shower_entry) is third


# =========================================================================== #
# 10. A fault log that could not be read
# =========================================================================== #


async def test_a_fault_log_that_could_not_be_read_is_not_reported_as_empty(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """`fault_log: []` says "no faults" — the one thing a failed read cannot say."""
    kohler.add_valve(RIGHT, "Tub")
    kohler.gcs_faults[LEFT] = fault_log(OWNER_FAULT)
    kohler.gcs_faults[RIGHT] = fault_log()
    await start(hass, shower_entry)
    kohler.fail_every_time(f"/gcs-diagnostics/{LEFT}", SERVER_ERROR)
    report = await diagnostics.async_get_config_entry_diagnostics(hass, shower_entry)

    left, right = report["valves"]
    assert left["fault_log"] is None
    assert left["fault_log_error"] == "the read failed or returned no log"
    assert report["valve"]["fault_log_error"] == left["fault_log_error"]
    # Another valve's read is still made, and an empty log that answered is just that.
    assert right["fault_log"] == []
    assert "fault_log_error" not in right
