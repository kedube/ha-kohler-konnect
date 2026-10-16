"""The write-path review of 2026-10-16, end to end inside a real Home Assistant.

Every valve write rebuilds both zones' words, so what it builds on decides what it keeps:
a paused zone kept paused, a second write inside the echo's round trip building on the
first rather than undoing it, the flow Home Assistant holds rather than 100 %. Outlet-setting
writes that no longer revert each other's scald limit; switches that hold their position
through unrelated messages; entities that outlive a layout that shrank under them; and the
custom-shower watcher that will not resume over a Stop or a command it could not see.

The valve's REST surface is `test_shower_entities.ShowerKohler`, extended here to hold a
command in flight until a test lets it go — the only way to show two writes overlapping.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Generator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect.konnect.models import get_valve_model

from .conftest import VALVE_ID, FakeMqttClient, account
from .test_shower_entities import (
    DURATION,
    FLOW,
    HUB,
    HUB_ID,
    MAX_TEMPERATURE,
    RAINHEAD,
    SHOWERHEAD,
    SOLO,
    TEMPERATURE,
    WRITE_OUTLET,
    ShowerKohler,
    call,
    choose,
    ids_by_unique_id,
    outlet_config_message,
    report,
    set_number,
    setup,
    solo,
    state,
    word,
)

COORDINATOR = "custom_components.kohler_konnect.coordinator"


class HeldKohler(ShowerKohler):
    """`ShowerKohler`, able to hold one endpoint's commands in flight until released."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.gate: asyncio.Event | None = None
        self.gate_endpoint = ""
        self.held = 0

    def hold(self, endpoint: str) -> asyncio.Event:
        """Hold every POST to ``endpoint`` until the returned event is set."""
        self.gate = asyncio.Event()
        self.gate_endpoint = endpoint
        self.held = 0
        return self.gate

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        if (
            self.gate is not None
            and method.lower() == "post"
            and url.path.endswith(self.gate_endpoint)
        ):
            self.held += 1
            await self.gate.wait()
        return await super()._api(method, url, data)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> HeldKohler:
    return HeldKohler(aioclient_mock)


@pytest.fixture(autouse=True)
def us_units(hass: HomeAssistant) -> None:
    """Show temperatures in the account's own unit, so states read as the app does."""
    hass.config.units = US_CUSTOMARY_SYSTEM


@pytest.fixture(autouse=True)
def quick_usage_reads() -> Generator[None]:
    """Re-read water usage at once when a shower ends, rather than 90 s later."""
    with (
        patch(f"{COORDINATOR}.USAGE_REFRESH_DELAY_SECONDS", 0),
        patch(f"{COORDINATOR}.USAGE_RETRY_DELAY_SECONDS", 0),
    ):
        yield


def valve_of(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    return account(hass, entry).valves[0]


def entity_id(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    return ids_by_unique_id(hass, entry)[f"{VALVE_ID}_{key}"]


def deliver_solo(zone1: str, zone2: str | None = None, **fields: Any) -> None:
    """A `GCS_SOLO_STS`, delivered without waiting on Home Assistant.

    The custom-shower watcher is a tracked task that lives until it decides, so
    `async_block_till_done` — which every `solo` waits on — would wait on it too.
    """
    attribute = {
        "code": "GCS_SOLO_STS",
        "primaryValve1": zone1,
        "secondaryValve1": zone2 or "0000000000000000",
        **fields,
    }
    FakeMqttClient.instances[-1].deliver(
        {
            "sku": "GCS",
            "deviceid": VALVE_ID,
            "data": {"code": "GCS_SOLO_STS", "attributes": [attribute]},
        }
    )


async def eventually(condition: Callable[[], bool], what: str) -> None:
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"timed out waiting for {what}")


def watchers() -> list[asyncio.Task]:
    """Every custom-shower keep-on watcher still running, referenced or not."""
    return [
        task
        for task in asyncio.all_tasks()
        if task.get_coro().__name__ == "_async_keep_on_after_warmup" and not task.done()
    ]


# --------------------------------------------------------------------------- #
# 1. A paused zone stays paused
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("number", "value"), [(TEMPERATURE, 104), (FLOW, 60)])
async def test_changing_a_paused_zones_setpoint_does_not_start_its_water(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: HeldKohler,
    number: str,
    value: float,
) -> None:
    """0x41 — paused, outlet 1 still assigned — used to go back as 0x01, water running."""
    await setup(hass, config_entry)
    await solo(hass, word(0b001, paused=True))

    await set_number(hass, number, value)

    [(zone1, _)] = kohler.words()
    assert (zone1.outlet_mask, zone1.paused) == (0b001, True)


async def test_changing_one_zone_keeps_the_other_zones_pause(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)
    await solo(hass, word(0b001), word(0b100, zone=2, paused=True))

    await set_number(hass, entity_id(hass, config_entry, "temperature_zone_1"), 104)

    [(zone1, zone2)] = kohler.words()
    assert (zone1.outlet_mask, zone1.paused) == (0b001, False)
    assert zone2 is not None
    assert (zone2.outlet_mask, zone2.paused) == (0b100, True)


async def test_switching_an_outlet_on_in_a_paused_zone_opens_only_that_outlet(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Every switch read off; opening the held assignment too would surprise everyone."""
    await setup(hass, config_entry)
    await solo(hass, word(0b001, paused=True))

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWERHEAD)

    [(zone1, _)] = kohler.words()
    assert (zone1.outlet_mask, zone1.paused) == (0b010, False)


async def test_switching_an_outlet_off_in_a_paused_zone_keeps_it_paused(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """It leaves the assignment the pause would resume to, and nothing starts."""
    await setup(hass, config_entry)
    await solo(hass, word(0b011, paused=True))

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, RAINHEAD)

    [(zone1, _)] = kohler.words()
    assert (zone1.outlet_mask, zone1.paused) == (0b010, True)


# --------------------------------------------------------------------------- #
# 2. Outlet settings do not revert each other
# --------------------------------------------------------------------------- #
async def test_a_second_outlet_setting_does_not_restore_the_first(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """🚨 The scald limit went back to 118 °F when the duration was set straight after."""
    await setup(hass, config_entry)

    await set_number(hass, MAX_TEMPERATURE, 110)
    await choose(hass, DURATION, "45 minutes")

    records = [b["gcsOutletConfigControlModel"] for b in kohler.sent(WRITE_OUTLET)]
    duration = records[3:]
    assert [r["maximumRuntime"] for r in duration] == ["2700"] * 3
    assert [r["maximumOutletTemperature"] for r in duration] == ["433"] * 3


async def test_two_outlet_settings_at_once_are_written_one_after_the_other(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Interleaved, each chain echoed the other's old value outlet by outlet."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)

    await asyncio.gather(
        valve.async_write_outlet_setting(maximum_temperature_tenths=433),
        valve.async_write_outlet_setting(maximum_run_time=2700),
    )

    records = [b["gcsOutletConfigControlModel"] for b in kohler.sent(WRITE_OUTLET)]
    assert [r["outLetId"] for r in records] == ["0", "1", "2"] * 2
    assert [(r["maximumOutletTemperature"], r["maximumRuntime"]) for r in records] == [
        ("433", "1800")
    ] * 3 + [("433", "2700")] * 3


async def test_a_limit_below_any_outlets_default_is_refused_before_anything_is_sent(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """It used to write outlets 1 and 2 and then report a mixed state at outlet 3."""
    kohler.outlet_fields = {2: {"defaultOutletTemperature": "45"}}
    await setup(hass, config_entry)

    with pytest.raises(HomeAssistantError, match="Nothing was written") as raised:
        await valve_of(hass, config_entry).async_write_outlet_setting(
            maximum_temperature_tenths=440
        )
    assert "outlet 3" in str(raised.value)
    assert kohler.sent(WRITE_OUTLET) == []


async def test_a_read_back_older_than_a_later_write_does_not_revert_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """The cloud document lags a write by up to 25 s; the cache is what the next echoes."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await valve.async_write_outlet_setting(maximum_run_time=2700)
    assert valve.outlet_run_times == {1: 2700, 2: 2700, 3: 2700}

    # An earlier write's check, reading a document that does not show 2700 yet.
    kohler.outlet_fields = {i: {"maximumRuntime": "1800"} for i in range(3)}
    with patch(f"{COORDINATOR}.OUTLET_WRITE_VERIFY_DELAY_SECONDS", 0):
        await valve._async_verify_outlet_write(
            maximum_run_time=None,
            maximum_temperature_tenths=477,
            default_temperature_tenths=None,
            serial=valve._outlet_write_serial - 1,
        )
    assert valve.outlet_run_times == {1: 2700, 2: 2700, 3: 2700}


# --------------------------------------------------------------------------- #
# 3. Rapid and concurrent writes build on each other (GitHub issue #1)
# --------------------------------------------------------------------------- #
async def test_two_outlets_switched_on_together_both_open(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    await setup(hass, config_entry)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: [RAINHEAD, SHOWERHEAD]},
        blocking=True,
    )

    assert [zone1.outlet_mask for zone1, _ in kohler.words()][-1] == 0b011


async def test_a_temperature_change_inside_the_echo_keeps_the_outlet_just_opened(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await set_number(hass, TEMPERATURE, 104)

    zone1, _ = kohler.words()[-1]
    assert (zone1.outlet_mask, zone1.temperature_celsius) == (0b001, 40.0)


async def test_a_report_after_the_write_is_the_next_ones_baseline(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """A Stop at the wall must not be undone by the overlay of what was sent before it."""
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await solo(hass, word(0))
    await set_number(hass, TEMPERATURE, 104)

    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0


async def test_the_last_sent_words_stop_standing_in_after_a_few_seconds(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """With no echo at all, the report is the baseline again soon after."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    valve._sent_at -= valve._sent_hold_seconds + 1
    await set_number(hass, TEMPERATURE, 104)

    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0


async def test_a_write_waits_for_the_one_in_flight_and_builds_on_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    gate = kohler.hold(SOLO)

    opening = asyncio.create_task(valve.async_set_zone_outlet(1, 1, True))
    await eventually(lambda: kohler.held == 1, "the first write to be in flight")
    warming = asyncio.create_task(valve.async_apply_valve(zone1_temperature=104))
    await asyncio.sleep(0.05)
    assert kohler.held == 1, "the second write must not be sent alongside the first"

    gate.set()
    await asyncio.gather(opening, warming)
    zone1, _ = kohler.words()[-1]
    assert (zone1.outlet_mask, zone1.temperature_celsius) == (0b001, 40.0)


# --------------------------------------------------------------------------- #
# 4 and 5. Flow is Home Assistant's own, on the zone's own scale
# --------------------------------------------------------------------------- #
async def test_a_temperature_change_keeps_the_flow_home_assistant_holds(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    await setup(hass, config_entry)

    await set_number(hass, FLOW, 50)
    await set_number(hass, TEMPERATURE, 104)

    assert [zone1.flow_percent for zone1, _ in kohler.words()] == [50.0, 50.0]


async def test_a_raw_word_for_zone_one_keeps_zone_twos_flow(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)
    await set_number(hass, entity_id(hass, config_entry, "flow_zone_2"), 60)

    await valve_of(hass, config_entry).async_send_valve_hex("0184C801")

    _, zone2 = kohler.words()[-1]
    assert zone2 is not None
    assert zone2.flow_percent == 60.0


async def test_flow_on_a_zone_with_its_own_ceiling_reads_and_writes_as_chosen(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """A ceiling of 100 bytes: 50 % is byte 50 — shown as 50, never as 25."""
    kohler.outlet_fields = {i: {"maximumFlowrate": "25"} for i in range(3)}
    await setup(hass, config_entry)

    await set_number(hass, FLOW, 50)
    assert float(state(hass, FLOW).state) == 50
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await set_number(hass, TEMPERATURE, 104)

    # Byte 50 each time — 25 % on the codec's 200 scale.
    assert [zone1.flow_percent for zone1, _ in kohler.words()] == [25.0] * 3


# --------------------------------------------------------------------------- #
# 6. Each zone's Flow reads its own zone
# --------------------------------------------------------------------------- #
async def test_each_zones_flow_reads_its_own_word(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)

    await solo(hass, word(0, flow=24.5), word(0b001, zone=2, flow=60.0))

    second = state(hass, entity_id(hass, config_entry, "flow_zone_2"))
    assert float(second.state) == 60
    assert second.attributes["flow_is_live"] is True
    first = state(hass, entity_id(hass, config_entry, "flow_zone_1"))
    assert float(first.state) == 100
    assert first.attributes["flow_is_live"] is False


# --------------------------------------------------------------------------- #
# 7. A layout that shrinks after setup
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("now", "gone"),
    [
        ("K-28210", ["temperature_zone_2", "flow_zone_2", "zone_2_outlet_1"]),
        ("K-28211", ["zone_1_outlet_3", "zone_2_outlet_3"]),
    ],
)
async def test_entities_outlive_a_layout_that_shrinks_under_them(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: HeldKohler,
    now: str,
    gone: list[str],
) -> None:
    """Until the entry reloads, they read unavailable; one that raised stopped the rest."""
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)
    await solo(hass, word(0b111), word(0b111, zone=2))
    valve = valve_of(hass, config_entry)

    # What `Valve._apply_topology` does when the valve reports a different split.
    model = get_valve_model(now)
    valve.gcs_state.model = model
    valve.gcs.model = model
    if not model.uses_valve2:
        valve.gcs_state.valve2 = None
    account(hass, config_entry).async_refresh_entities()
    await hass.async_block_till_done()

    for key in gone:
        assert state(hass, entity_id(hass, config_entry, key)).state == (
            STATE_UNAVAILABLE
        ), key
    assert state(hass, entity_id(hass, config_entry, "zone_1_outlet_1")).state == (
        STATE_ON
    )


# --------------------------------------------------------------------------- #
# 10. A switch holds its position through unrelated messages
# --------------------------------------------------------------------------- #
async def test_an_outlet_toggle_holds_through_its_own_valves_other_messages(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Every message advances `last_update`; only the valve's word may answer the toggle."""
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await report(hass, "GCS_RECIEVED_STS", {"code": "GCS_RECIEVED_STS"})
    assert state(hass, RAINHEAD).state == STATE_ON
    await report(hass, "READ_GCS_OUTLET_CONFIG_CFG", outlet_config_message(0, 1800))
    assert state(hass, RAINHEAD).state == STATE_ON

    # The valve agrees, so the guess is done with and the grace changes nothing.
    await solo(hass, word(0b001))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=13))
    await hass.async_block_till_done()
    assert state(hass, RAINHEAD).state == STATE_ON


async def test_a_controller_switch_holds_through_its_controllers_other_messages(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    kohler.devices.append(dict(HUB))
    await setup(hass, config_entry)
    shower = ids_by_unique_id(hass, config_entry)[f"{HUB_ID}_shower"]

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, shower)
    await report(
        hass,
        "MUSIC_STS",
        {"component": "amplifier", "status": "ON"},
        device_id=HUB_ID,
        sku="HUB",
    )

    assert state(hass, shower).state == STATE_ON


# --------------------------------------------------------------------------- #
# 11 and 12. No pausing warm-up: a Stop, and commands it could not see
# --------------------------------------------------------------------------- #
async def test_a_pause_without_at_temperature_is_not_resumed_and_says_so(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: HeldKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """🚨 A Stop from the app or the panel during the warm-up is this same 0x40."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await valve.async_custom_shower("0184C801", "00000000", keep_on_after_warmup=True)

    deliver_solo(word(0b111), warmUpStatus="warmUpInProgress")
    await asyncio.sleep(0.05)
    deliver_solo(word(0, paused=True), warmUpStatus="warmUpNotInProgress")
    await eventually(lambda: valve._custom_shower_task is None, "the watcher")

    assert len(kohler.sent(SOLO)) == 1
    assert any(
        r.levelno == logging.WARNING and "may be a Stop" in r.getMessage()
        for r in caplog.records
    )


async def test_a_command_counted_while_the_custom_shower_was_in_flight_stops_the_resume(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """The watcher's serial used to be read after the POST, so it took this one in."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    gate = kohler.hold(SOLO)

    sending = asyncio.create_task(
        valve.async_custom_shower("0184C801", "00000000", keep_on_after_warmup=True)
    )
    await eventually(lambda: kohler.held == 1, "the custom shower to be in flight")
    # A controller command meanwhile, which counts against every valve.
    account(hass, config_entry)._note_local_write()
    gate.set()
    await sending

    deliver_solo(word(0b111), warmUpStatus="warmUpInProgress")
    await asyncio.sleep(0.05)
    deliver_solo(
        word(0, paused=True, at_temperature=True), warmUpStatus="warmUpNotInProgress"
    )
    await eventually(lambda: valve._custom_shower_task is None, "the watcher")
    assert len(kohler.sent(SOLO)) == 1


async def test_overlapping_custom_showers_leave_one_watcher_resuming_the_newer(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """The second used to replace the first's watcher without cancelling it."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    gate = kohler.hold(SOLO)

    older = asyncio.create_task(
        valve.async_custom_shower("0184C801", "00000000", keep_on_after_warmup=True)
    )
    await eventually(lambda: kohler.held == 1, "the first custom shower in flight")
    newer = asyncio.create_task(
        valve.async_custom_shower("0184C802", "00000000", keep_on_after_warmup=True)
    )
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.gather(older, newer)
    assert len(watchers()) == 1

    deliver_solo(word(0b111), warmUpStatus="warmUpInProgress")
    await asyncio.sleep(0.05)
    deliver_solo(
        word(0, paused=True, at_temperature=True), warmUpStatus="warmUpNotInProgress"
    )
    await eventually(lambda: not watchers(), "the watcher")
    sent = [body["gcsValveControlModel"]["primaryValve1"] for body in kohler.sent(SOLO)]
    assert sent == ["0184C801", "0184C802", "0184C802"]
