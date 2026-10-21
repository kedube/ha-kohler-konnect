"""The account's lifecycle, seeding and reporting, as reviewed on 2026-10-16.

Shutdown while paho is still delivering, a REST read overtaken by a push, entity updates
scoped to the device a message was about, a layout read after the entities exist, reads
retried after a failed boot, which seed reads wait for which, the valve's fault log, the
diagnostics report's shape, and the device registry.

Runs against `ShowerKohler` from `test_coordinator`, extended with the reads it does not
serve — the valve's fault log, its usage series and its per-part `about` — and with gates
that hold a request, matched on the end of its path, until a test lets it through.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Generator, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect import (
    async_remove_config_entry_device,
    diagnostics,
)
from custom_components.kohler_konnect import coordinator as coordinator_module
from custom_components.kohler_konnect.const import (
    CONF_REFRESH_TOKEN,
    CONF_VALVE_MODEL,
    CONF_ZONE_GROUPING,
    CONF_ZONE_OUTLETS,
    DOMAIN,
)
from custom_components.kohler_konnect.konnect import AuthError, AuthUnavailable

from .conftest import (
    DEVICE_ID,
    FAUCET,
    USERNAME,
    FakeMqttClient,
    account,
    registered_device,
    unload,
    wait_for,
)
from .test_coordinator import (
    LEFT,
    MAIN,
    RIGHT,
    SHOWER_DATA,
    ShowerKohler,
    hub_message,
    hub_state,
    prompts,
    push,
    reconnect,
    solo_message,
    start,
    state,
    stream,
    valve_settings,
    valve_state,
    valve_zone,
)

# The owner's own fault, from Shower Left's `gcs-diagnostics` log — with the identity a
# real entry carries beside it, which must not travel any further.
OWNER_FAULT = {
    "id": "err-0001",
    "deviceId": LEFT,
    "details": f"Raised by {LEFT}",
    "area": "Algorithm",
    "errorCode": "37",
    "isActive": True,
    "valveId": "Valve 1",
    "timestamp": "2026-04-21T09:12:00Z",
}
SERVER_ERROR = (500, {"message": "Something went wrong"})


class LifecycleKohler(ShowerKohler):
    """`ShowerKohler`, plus the fault log, usage and `about` reads, and request gates."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        # `gcs-diagnostics` replies by device id; a device missing answers 404, which the
        # client turns into `{}`.
        self.gcs_faults: dict[str, Any] = {}
        # `about` replies by device id; missing answers 404, which reads as `{}`.
        self.about: dict[str, Any] = {}
        # Usage intervals (`MONTH`, `DAY`) that currently fail.
        self.usage_down: set[str] = set()
        # Held requests: path suffix -> gate, and how many requests have reached each.
        self.holds: dict[str, asyncio.Event] = {}
        self.arrived: Counter[str] = Counter()

    def hold(self, suffix: str) -> asyncio.Event:
        """Hold every request whose path ends with ``suffix`` until the event is set."""
        gate = self.holds[suffix] = asyncio.Event()
        return gate

    async def until_held(self, suffix: str, count: int = 1) -> None:
        await until(lambda: self.arrived[suffix] >= count, f"{count} at {suffix}")

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        path = url.path
        for suffix, gate in list(self.holds.items()):
            if path.endswith(suffix) and not gate.is_set():
                self.arrived[suffix] += 1
                await gate.wait()
        queued = any(path.endswith(s) and q for s, q in self.api_queue.items())
        if method.lower() == "get" and not queued:
            device_id = path.rstrip("/").rsplit("/", 1)[-1]
            if "/gcs-diagnostics/" in path and device_id in self.gcs_faults:
                return self._respond(method, url, (200, self.gcs_faults[device_id]))
            if path.endswith("/about") and "/gcs-configuration/" in path:
                device_id = path.split("/gcs-configuration/")[1].split("/")[0]
                if device_id in self.about:
                    return self._respond(method, url, (200, self.about[device_id]))
            if "/gcs-usage/" in path:
                interval = url.query.get("Interval")
                if interval in self.usage_down:
                    return self._respond(method, url, SERVER_ERROR)
                key = "2026-10" if interval == "MONTH" else "2026-10-15"
                return self._respond(
                    method,
                    url,
                    (
                        200,
                        {
                            "gcsUsageDataDetailsList": [
                                {"intervalKey": key, "volume": 5}
                            ]
                        },
                    ),
                )
        return await super()._api(method, url, data)


async def until(condition: Callable[[], bool], what: str) -> None:
    """Wait for a condition without `async_block_till_done`, which a held request blocks."""
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"timed out waiting for {what}")


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


@contextmanager
def state_writes() -> Iterator[list[str]]:
    """Every entity state written while the block runs, by entity id."""
    written: list[str] = []
    original = Entity.async_write_ha_state

    def record(self: Entity) -> None:
        written.append(self.entity_id)
        original(self)

    with patch.object(Entity, "async_write_ha_state", record):
        yield written


def device_of(hass: HomeAssistant) -> dict[str, str]:
    """Each of this integration's entity ids, to the Kohler identifier of its device."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    owners: dict[str, str] = {}
    for row in entities.entities.values():
        if row.platform != DOMAIN or row.device_id is None:
            continue
        device = devices.async_get(row.device_id)
        owners[row.entity_id] = next(i for d, i in device.identifiers if d == DOMAIN)
    return owners


def two_zones(hass: HomeAssistant, entry: MockConfigEntry, **options: Any) -> None:
    hass.config_entries.async_update_entry(
        entry,
        data={**SHOWER_DATA, CONF_VALVE_MODEL: "K-28212", CONF_ZONE_OUTLETS: [3, 3]},
        options=options,
    )


# =========================================================================== #
# 1. Shutting down while the stream is still delivering
# =========================================================================== #


async def test_what_paho_delivers_while_the_stream_stops_starts_nothing(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """`async_stop` yields while paho's thread winds down, and paho can deliver meanwhile.

    A connect then started a fresh reseed, and an envelope reached a valve already
    stopped, free to start a warm-up restore or a usage read nothing would cancel.
    """
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    client = stream()
    real_stop = client.loop_stop

    def stop_while_delivering() -> None:
        client.on_connect(client, None, None, 0)
        client.deliver(solo_message(LEFT, "0184C801"))
        real_stop()

    client.loop_stop = stop_while_delivering
    reads = kohler.reads(f"/gcs-state/{LEFT}")

    assert await hass.config_entries.async_unload(shower_entry.entry_id)
    await hass.async_block_till_done()

    assert coordinator.closed
    assert coordinator._reseed_task is None
    assert kohler.reads(f"/gcs-state/{LEFT}") == reads
    assert valve.gcs_state.outlets == [False, False, False]
    assert valve._background_tasks == set()


async def test_an_unload_cancels_a_firmware_check_in_flight(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """Only the twice-daily timer used to be cancelled, not a check it had started."""
    coordinator = await start(hass, shower_entry)
    gate = kohler.hold(f"/firmware/gcs/{LEFT}")
    coordinator._async_firmware_tick(None)
    task = coordinator._firmware_task
    await kohler.until_held(f"/firmware/gcs/{LEFT}")

    assert await hass.config_entries.async_unload(shower_entry.entry_id)
    gate.set()
    await asyncio.wait({task}, timeout=1)
    assert task.cancelled()
    assert coordinator._firmware_task is None


async def test_a_sign_in_failure_after_unload_asks_nobody(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """A stale copy of the token failing is not the account's sign-in failing."""
    coordinator = await start(hass, shower_entry)
    assert await hass.config_entries.async_unload(shower_entry.entry_id)
    coordinator._handle_auth_error(AuthError("AADB2C90080: The grant expired."))
    await hass.async_block_till_done()
    assert prompts(hass) == []


async def test_a_token_rotated_by_the_old_coordinator_does_not_replace_the_new_ones(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """A command in flight across a reload still belongs to the old sign-in."""
    old = await start(hass, shower_entry)
    assert await hass.config_entries.async_reload(shower_entry.entry_id)
    await hass.async_block_till_done()
    new = account(hass, shower_entry)
    assert new is not old

    new._store_refresh_token("refresh-new")
    old._store_refresh_token("refresh-late")
    assert shower_entry.data[CONF_REFRESH_TOKEN] == "refresh-new"


async def test_a_token_rotated_after_unload_is_still_kept_when_nothing_newer_was(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """Dropped, it would be the only copy of a live credential — and the account lost."""
    old = await start(hass, shower_entry)
    assert await hass.config_entries.async_unload(shower_entry.entry_id)
    old._store_refresh_token("refresh-late")
    assert shower_entry.data[CONF_REFRESH_TOKEN] == "refresh-late"


async def test_home_assistant_stopping_stops_the_stream(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """Entries are not unloaded on stop; the stream kept dispatching through it."""
    coordinator = await start(hass, shower_entry)
    client = stream()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert coordinator.closed
    assert client.stopped
    assert coordinator.stream is None


async def test_a_setup_that_fails_in_the_platforms_leaves_nothing_running(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """The coordinator was stored, and the stream up, before the old cleanup's reach."""
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(side_effect=RuntimeError("a platform failed")),
    ):
        assert not await hass.config_entries.async_setup(shower_entry.entry_id)
    await hass.async_block_till_done()
    (client,) = FakeMqttClient.instances
    assert client.stopped
    assert shower_entry.entry_id not in hass.data.get(DOMAIN, {})


# =========================================================================== #
# 2. A REST read overtaken by a push
# =========================================================================== #


async def test_a_valve_reseed_overtaken_by_a_push_keeps_the_push(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """`gcs-state` trails the device: the shower stopped, the read still showed it open.

    Its `Disconnected` is older than the valve's own message too, so that does not turn the
    sensor off either: a message the valve sent after the read went out proves it reachable
    (2026-10-16). Kohler's word is still recorded, with the reason it was overruled.
    """
    kohler.valve_states[LEFT] = valve_state(valve_zone(1))
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    assert valve.gcs_state.outlets == [True, False, False]

    kohler.valve_states[LEFT]["connectionState"] = "Disconnected"
    gate = kohler.hold(f"/gcs-state/{LEFT}")
    coordinator._handle_connected()
    await kohler.until_held(f"/gcs-state/{LEFT}")
    stream().deliver(solo_message(LEFT, "0184C800"))
    await until(lambda: valve.gcs_state.outlets == [False] * 3, "the stop")
    gate.set()
    await hass.async_block_till_done()

    assert valve.gcs_state.outlets == [False, False, False]
    assert state(hass, "switch.anthem_valve_rainhead_1") == "off"
    assert valve.cloud_watch.connected is True
    attributes = valve.cloud_watch.attributes
    assert attributes["connection_state"] == "Disconnected"
    assert "after this read was sent" in attributes["reachable_because"]


async def test_a_valve_reseed_not_overtaken_is_applied(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """A message that carries none of what `gcs-state` does is no reason to drop it."""
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    kohler.valve_states[LEFT] = valve_state(valve_zone(2))
    gate = kohler.hold(f"/gcs-state/{LEFT}")
    coordinator._handle_connected()
    await kohler.until_held(f"/gcs-state/{LEFT}")
    stream().deliver(
        {
            "sku": "GCS",
            "deviceid": LEFT,
            "data": {"code": "READ_GCS_UI_CFG", "attributes": [{}]},
        }
    )
    await asyncio.sleep(0.05)
    gate.set()
    await hass.async_block_till_done()
    assert valve.gcs_state.outlets == [False, True, False]


async def test_a_controller_reseed_overtaken_by_a_push_keeps_the_push(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    kohler.add_controller()
    kohler.hub_states[MAIN] = hub_state(1, status="ON")
    coordinator = await start(hass, shower_entry)
    controller = coordinator.controllers[0]
    assert controller.water_is_running is True

    gate = kohler.hold(f"/hub-state/{MAIN}")
    coordinator._handle_connected()
    await kohler.until_held(f"/hub-state/{MAIN}")
    stream().deliver(
        hub_message(
            MAIN,
            "SHOWER_VALVE_STS",
            {"zone": "1", "status": "OFF", "outlets": [0, 0, 0]},
        )
    )
    await until(lambda: controller.water_is_running is False, "the stop")
    gate.set()
    await hass.async_block_till_done()
    assert controller.water_is_running is False


# =========================================================================== #
# 3. A message re-renders only the device it was about
# =========================================================================== #


async def test_a_valves_message_writes_only_that_valves_entities(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    kohler.add_valve(RIGHT, "Tub")
    kohler.add_controller(MAIN, "Main Bath")
    kohler.devices.append(dict(FAUCET))
    coordinator = await start(hass, shower_entry)
    # Past the faucet's 1 s refresh cooldown, so its own clock cannot write meanwhile.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    owners = device_of(hass)

    with state_writes() as written:
        await push(hass, solo_message(LEFT, "0184C801"))
    assert written, "the valve's own entities must still update"
    assert {owners[entity_id] for entity_id in written} == {LEFT}
    left = coordinator.valves[0]
    assert left.gcs_state.outlets == [True, False, False]

    with state_writes() as written:
        await push(
            hass,
            hub_message(
                MAIN,
                "SHOWER_VALVE_STS",
                {"zone": "1", "status": "ON", "outlets": [1, 0, 0]},
            ),
        )
    # Except the capture button: it lives on the first valve, but describes the account's
    # one capture, which every device's messages go into.
    capture = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{LEFT}_new_mqtt_capture"
    )
    assert capture in written
    assert {owners[entity_id] for entity_id in written if entity_id != capture} == {
        MAIN
    }

    # Not about one device: everything re-renders, as before.
    with state_writes() as written:
        coordinator.async_refresh_entities()
    assert {owners[entity_id] for entity_id in written} == {LEFT, RIGHT, MAIN}


async def test_a_zone_sub_device_updates_with_its_valve(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    kohler.valve_settings[LEFT] = valve_settings((3, 3))
    kohler.add_valve(RIGHT, "Tub")
    two_zones(hass, shower_entry, **{CONF_ZONE_GROUPING: "subdevices"})
    await start(hass, shower_entry)
    owners = device_of(hass)

    with state_writes() as written:
        await push(hass, solo_message(LEFT, "0184C800", "1184C801"))
    devices = {owners[entity_id] for entity_id in written}
    assert f"{LEFT}_zone_2" in devices
    assert not [d for d in devices if d.startswith(RIGHT)]


async def test_a_paired_valve_still_hears_its_controllers_warmup(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """One valve, one controller: `System Status` merges the controller's warm-up."""
    kohler.add_controller()
    await start(hass, shower_entry)
    assert state(hass, "sensor.anthem_valve_system_status") == "Idle"
    await push(
        hass,
        hub_message(
            MAIN,
            "SHOWER_VALVE_STS",
            {"zone": "1", "status": "OFF", "outlets": [0, 0, 0]},
            showerwarmup="1",
        ),
    )
    assert state(hass, "sensor.anthem_valve_system_status") == "Warming Up"


# =========================================================================== #
# 4. A layout read after the entities exist
# =========================================================================== #


async def test_a_layout_found_after_the_entities_exist_reloads_once(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """The settings read is retried until it answers, so it can answer late.

    The entry says two zones; the valve has one, but its read fails at setup and again at
    the stream's first connect, which retries a valve whose setup read never answered — so
    the entities are built for two.
    """
    two_zones(hass, shower_entry)
    kohler.fail_api(f"/gcsadvancestate/{LEFT}", SERVER_ERROR, SERVER_ERROR)
    first = await start(hass, shower_entry)
    assert first.valves[0].model.total_outlets == 6
    zone_2 = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{LEFT}_zone_2_outlet_1"
    )
    assert zone_2 is not None

    # The next reconnect's read answers: three outlets, one zone.
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
    assert second.valves[0].model.total_outlets == 3
    zone_2_state = hass.states.get(zone_2)
    assert zone_2_state is None or zone_2_state.state == "unavailable"

    # A later setup whose read fails again starts from the layout the valve reported,
    # not the entry's — so it cannot reload again over the same answer.
    kohler.fail_api(f"/gcsadvancestate/{LEFT}", SERVER_ERROR, SERVER_ERROR)
    assert await hass.config_entries.async_reload(shower_entry.entry_id)
    await hass.async_block_till_done()
    third = account(hass, shower_entry)
    assert not third.valves[0].topology_confirmed
    assert third.valves[0].model.total_outlets == 3
    await reconnect(hass, third)
    assert third.valves[0].topology_confirmed
    assert account(hass, shower_entry) is third


# =========================================================================== #
# 5. Reads lost at boot are read again
# =========================================================================== #


async def test_usage_and_parts_lost_at_boot_are_read_on_the_next_seed(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """They sat behind the configuration's once-only gate: lost until a restart."""
    kohler.usage_down = {"MONTH"}
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    assert valve.usage == {} and valve.about_parts == {}
    assert valve.configuration  # read, and latched

    kohler.usage_down = set()
    kohler.about[LEFT] = {"gatewayConfigInfo": {"model": "GW-1", "firmware": "00.74"}}
    await reconnect(hass, coordinator)
    assert valve.usage["gcsUsageDataDetailsList"]
    assert valve._usage_seeded_month is not None
    assert valve.about_parts == kohler.about[LEFT]

    # Once they have answered, a reconnect does not ask again.
    months, abouts = kohler.reads("Interval=MONTH"), kohler.reads("/about")
    configurations = kohler.reads(f"/gcs-configuration/{LEFT}")
    await reconnect(hass, coordinator)
    assert kohler.reads("Interval=MONTH") == months
    assert kohler.reads("/about") == abouts
    assert kohler.reads(f"/gcs-configuration/{LEFT}") == configurations


async def test_a_daily_read_retries_a_monthly_series_never_read(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """Without a first month on record, the month-rollover re-read could never fire."""
    kohler.usage_down = {"MONTH"}
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    kohler.usage_down = set()
    await valve.async_refresh_daily_usage()
    assert valve._usage_seeded_month is not None
    assert valve.usage["gcsUsageDataDetailsList"]


async def test_the_monthly_series_is_read_once_per_attempt(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """The seed and the daily read both retry it, and must not both ask at once."""
    coordinator = await start(hass, shower_entry)
    assert kohler.reads("Interval=MONTH") == 1

    coordinator.valves[0]._usage_seeded_month = None
    await coordinator.async_refresh()
    assert kohler.reads("Interval=MONTH") == 2


# =========================================================================== #
# 6. What waits for what
# =========================================================================== #


async def test_a_valves_other_reads_do_not_wait_for_its_configuration(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """Usage, `about` and the fault log queued behind `gcs-configuration` at every start."""
    gate = kohler.hold(f"/gcs-configuration/{LEFT}")
    setup = hass.async_create_task(
        hass.config_entries.async_setup(shower_entry.entry_id)
    )
    await kohler.until_held(f"/gcs-configuration/{LEFT}")
    await until(
        lambda: (
            kohler.reads("Interval=MONTH") == 1
            and kohler.reads("Interval=DAY") == 1
            and kohler.reads(f"/gcs-configuration/{LEFT}/about") == 1
            and kohler.reads(f"/gcs-diagnostics/{LEFT}") == 1
        ),
        "the reads beside the configuration",
    )
    gate.set()
    assert await setup


async def test_a_controllers_other_reads_do_not_wait_for_its_configuration(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """Only its state waits: the configuration says how to decode it."""
    kohler.add_controller()
    coordinator = await start(hass, shower_entry)
    coordinator.controllers[0].experiences = {}
    before = {
        marker: kohler.reads(marker)
        for marker in (
            f"/hub-experience/{MAIN}/experiences",
            f"/hub-diagnostics/{MAIN}/active",
            f"/hub-experience/{MAIN}/favorites",
            f"/hub-state/{MAIN}",
        )
    }
    gate = kohler.hold(f"/hub-configuration/{MAIN}")
    coordinator._handle_connected()
    await kohler.until_held(f"/hub-configuration/{MAIN}")
    await until(
        lambda: all(
            kohler.reads(marker) == count + 1
            for marker, count in before.items()
            if "hub-state" not in marker
        ),
        "the reads beside the configuration",
    )
    assert kohler.reads(f"/hub-state/{MAIN}") == before[f"/hub-state/{MAIN}"]
    gate.set()
    await hass.async_block_till_done()
    assert kohler.reads(f"/hub-state/{MAIN}") == before[f"/hub-state/{MAIN}"] + 1


async def test_a_faucets_first_read_does_not_wait_for_the_showers(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """It began only once every shower was seeded and the stream started."""
    kohler.devices.append(dict(FAUCET))
    gate = kohler.hold(f"/gcsadvancestate/{LEFT}")
    setup = hass.async_create_task(
        hass.config_entries.async_setup(shower_entry.entry_id)
    )
    await kohler.until_held(f"/gcsadvancestate/{LEFT}")
    await until(
        lambda: kohler.reads(f"/faucet-state/{DEVICE_ID}") >= 1, "the faucet's read"
    )
    gate.set()
    assert await setup
    # And its entities are still built from that read.
    assert hass.states.get("switch.kitchen_water") is not None


# =========================================================================== #
# 7. The valve's fault log reaches its Problem sensor
# =========================================================================== #


def fault_log(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"deviceId": LEFT, "id": "diag-1", "errorDetails": list(entries)}


async def test_an_active_fault_in_the_valves_log_turns_its_problem_sensor_on(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """The owner's diagnostics showed this fault with the sensor off."""
    kohler.gcs_faults[LEFT] = fault_log(
        OWNER_FAULT,
        {**OWNER_FAULT, "errorCode": "12", "isActive": False},
        {"errorCode": "0", "isActive": True, "title": "No fault"},
        {"errorCode": "", "isActive": True},
    )
    await start(hass, shower_entry)
    problem = hass.states.get("binary_sensor.anthem_valve_problem")
    assert problem.state == "on"
    assert problem.attributes["active_faults"] == [
        {
            "code": "37",
            "area": "Algorithm",
            "valve_id": "Valve 1",
            "timestamp": "2026-04-21T09:12:00Z",
        }
    ]
    assert LEFT not in json.dumps(dict(problem.attributes))


async def test_a_failed_fault_log_read_keeps_the_faults_already_known(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """Kohler hiccuping is not the fault clearing — and is not a failed refresh either."""
    kohler.gcs_faults[LEFT] = fault_log(OWNER_FAULT)
    coordinator = await start(hass, shower_entry)
    kohler.fail_every_time(f"/gcs-diagnostics/{LEFT}", SERVER_ERROR)
    await reconnect(hass, coordinator)
    assert state(hass, "binary_sensor.anthem_valve_problem") == "on"

    with patch.object(
        coordinator.client,
        "async_get_gcs_diagnostics",
        AsyncMock(side_effect=AuthUnavailable("Kohler sign-in is down")),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert state(hass, "binary_sensor.anthem_valve_problem") == "on"


async def test_a_fault_that_clears_turns_the_sensor_off_on_the_next_seed(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    kohler.gcs_faults[LEFT] = fault_log(OWNER_FAULT)
    coordinator = await start(hass, shower_entry)
    assert state(hass, "binary_sensor.anthem_valve_problem") == "on"
    kohler.gcs_faults[LEFT] = fault_log({**OWNER_FAULT, "isActive": False})
    await reconnect(hass, coordinator)
    assert state(hass, "binary_sensor.anthem_valve_problem") == "off"


# =========================================================================== #
# 8 and 9. The diagnostics report
# =========================================================================== #


async def test_a_sign_in_failure_costs_the_fault_log_not_the_report(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """The token layer's errors are not `KohlerError`s, so the client's guard missed them."""
    kohler.add_valve(RIGHT, "Tub")
    coordinator = await start(hass, shower_entry)
    read = AsyncMock(side_effect=AuthUnavailable("Kohler sign-in is down"))
    with patch.object(coordinator.client, "async_get_gcs_diagnostics", read):
        report = await diagnostics.async_get_config_entry_diagnostics(
            hass, shower_entry
        )

    # The second valve is not asked: it would only fail the same way.
    assert read.await_count == 1
    for valve in report["valves"]:
        assert valve["fault_log"] is None
        assert valve["fault_log_error"] == "sign-in failed (AuthUnavailable)"
    assert report["valve"]["fault_log_error"] == report["valves"][0]["fault_log_error"]


async def test_a_faucets_report_describes_the_faucet_not_valve_zero(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    kohler.add_controller()
    kohler.devices.append(dict(FAUCET))
    await start(hass, shower_entry)
    report = await diagnostics.async_get_device_diagnostics(
        hass, shower_entry, registered_device(hass, DEVICE_ID)
    )

    assert report["requested_for"] == "faucet_0"
    assert report["faucet"] == report["faucets"][0]
    assert not {"valve", "run_time", "warmup", "controller"} & set(report)
    # The whole installation is still there, fault logs included.
    assert "fault_log" in report["valves"][0]
    assert len(report["controllers"]) == 1


async def test_the_report_says_why_the_cloud_watch_reads_as_it_does(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """`cloud_connected: false` alone said nothing of when, why, or what came back."""
    coordinator = await start(hass, shower_entry)
    watch = coordinator.valves[0].cloud_watch
    # An error whose text quotes a reply body that names the device.
    watch._last_error = f"KohlerError: failed with HTTP 404: {{'device': '{LEFT}'}}"
    report = await diagnostics.async_get_config_entry_diagnostics(hass, shower_entry)

    reported = report["valves"][0]["cloud_watch"]
    assert reported["connection_state"] == "Connected"
    assert (
        reported["checked_because"] == "REST seed (setup, reconnect or update_entity)"
    )
    assert {
        "last_checked",
        "last_error",
        "cloud_last_connected",
        "seconds_since_valve_message",
    } <= set(reported)
    assert reported["last_error"].startswith("<str, ")
    assert LEFT not in json.dumps(report)


async def test_version_blocks_get_the_identity_check_the_version_scan_does(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    kohler.valve_configurations[LEFT] = {
        "configuration": {"about": {"uI2": {"firmware": "2.2"}}},
        "otaReportedProperties": {
            "firmwareType": "Application",
            "updatedVersion": "2.20",
        },
        "firmwareUpdate": {
            "firmwareType": "Application",
            "version": "2.20",
            "deviceId": LEFT,
            "url": "https://fw.example.net/image.bin?sv=1&sig=abc",
        },
    }
    await start(hass, shower_entry)
    report = await diagnostics.async_get_config_entry_diagnostics(hass, shower_entry)

    blocks = report["valves"][0]["configuration"]["version_blocks"]
    assert blocks["otaReportedProperties"] == {
        "firmwareType": "Application",
        "updatedVersion": "2.20",
    }
    assert blocks["firmwareUpdate"]["version"] == "2.20"
    assert blocks["firmwareUpdate"]["deviceId"] == f"<str, {len(LEFT)} chars>"
    assert blocks["firmwareUpdate"]["url"] == "<str, 45 chars>"
    assert LEFT not in json.dumps(report)


async def test_light_groups_named_by_the_owner_are_labelled_not_copied(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    kohler.add_controller()
    kohler.hub_states[MAIN]["state"]["light"] = [
        {"name": "Vanity Lights", "status": "ON"},
        {"component": "lightgroupB", "status": "OFF"},
    ]
    await start(hass, shower_entry)
    report = await diagnostics.async_get_config_entry_diagnostics(hass, shower_entry)
    assert report["controllers"][0]["light_groups"] == {"b": False, "group_1": True}
    assert "vanity" not in json.dumps(report).lower()


# =========================================================================== #
# 10. The device registry
# =========================================================================== #


async def test_only_a_device_that_left_the_account_can_be_deleted(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """Without this a device removed in the app stayed, with no Delete offered."""
    kohler.valve_settings[LEFT] = valve_settings((3, 3))
    kohler.add_controller()
    kohler.devices.append(dict(FAUCET))
    two_zones(hass, shower_entry, **{CONF_ZONE_GROUPING: "subdevices"})
    await start(hass, shower_entry)
    devices = dr.async_get(hass)
    gone = "gcs-gone00000009"
    for identifier in (gone, f"{gone}_zone_1"):
        devices.async_get_or_create(
            config_entry_id=shower_entry.entry_id, identifiers={(DOMAIN, identifier)}
        )

    for identifier, removable in (
        (LEFT, False),
        (f"{LEFT}_zone_2", False),
        (MAIN, False),
        (DEVICE_ID, False),
        (gone, True),
        (f"{gone}_zone_1", True),
    ):
        device = registered_device(hass, identifier)
        assert (
            await async_remove_config_entry_device(hass, shower_entry, device)
            is removable
        ), identifier

    # Unloaded, nothing can be checked against the account, so nothing is deleted.
    assert await hass.config_entries.async_unload(shower_entry.entry_id)
    device = registered_device(hass, gone)
    assert await async_remove_config_entry_device(hass, shower_entry, device) is False


async def test_zone_devices_survive_a_boot_whose_settings_read_failed(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: LifecycleKohler
) -> None:
    """The fallback layout is not the valve's: a two-zone valve looked zoneless.

    Both zone devices were deleted, and the area and name the owner gave each with them.
    """
    kohler.valve_settings[LEFT] = valve_settings((3, 3))
    hass.config_entries.async_update_entry(
        shower_entry, options={CONF_ZONE_GROUPING: "subdevices"}
    )
    await start(hass, shower_entry)
    zones = (f"{LEFT}_zone_1", f"{LEFT}_zone_2")
    assert all(registered_device(hass, zone) for zone in zones)

    # A restart — nothing remembered — whose settings reads fail: the entry says one zone.
    hass.data.pop(coordinator_module._VALVE_TOPOLOGY, None)
    kohler.fail_every_time(f"/gcsadvancestate/{LEFT}", SERVER_ERROR)
    assert await hass.config_entries.async_reload(shower_entry.entry_id)
    await hass.async_block_till_done()
    valve = account(hass, shower_entry).valves[0]
    assert valve.model.total_outlets == 3
    assert not valve.topology_confirmed
    assert all(registered_device(hass, zone) for zone in zones)
