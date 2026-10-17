"""The write-path review's second round (2026-10-16), end to end inside a real Home Assistant.

Each test here failed against release 1.03. A report that arrives after a command is not
always newer than it, so the last words sent may be narrowed by one but never widened —
a Stop stays a Stop, a pause stays a pause. A command that fails puts the one before it
back. An outlet setting is not a valve word. A read-back the cloud has not caught up with
does not put the old scald limit back, and each setting keeps its own warning. A cancelled
command does not leave a switch asserting what the valve never did. A Stop goes ahead of
everything asked for before it. Nothing is written when any outlet would refuse it. A
command during a warm-up keeps the outlets of the shower being warmed up, or is refused
when that shower was started elsewhere. And `custom_shower`'s flow is a percent of each
zone's own ceiling, as everywhere else.

The fake cloud and the in-flight hold are `test_review_writes.HeldKohler`'s.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.select import SERVICE_SELECT_OPTION
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.kohler_konnect.const import DOMAIN, device_issue_key
from custom_components.kohler_konnect.konnect.state import outlet_limits_from_settings

from .conftest import VALVE_ID, account
from .test_review_writes import (  # noqa: F401 - `quick_usage_reads`, `us_units`: autouse
    COORDINATOR,
    HeldKohler,
    deliver_solo,
    eventually,
    quick_usage_reads,
    us_units,
    valve_of,
    watchers,
)
from .test_shower_entities import (
    CONTROL_PRESET,
    DURATION,
    FAVORITE,
    FLOW,
    PRESETS,
    RAINHEAD,
    SHOWER_ON,
    SHOWERHEAD,
    SOLO,
    TEMPERATURE,
    WRITE_OUTLET,
    call,
    choose,
    custom_shower,
    send_valve_hex,
    set_number,
    setup,
    solo,
    state,
    word,
)

WARMING = {"warmUpStatus": "warmUpInProgress"}


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> HeldKohler:
    return HeldKohler(aioclient_mock)


def masks(kohler: HeldKohler) -> list[tuple[int, bool]]:
    """Zone 1's outlet mask and pause in every `solowritesystem` sent, oldest first."""
    return [(zone1.outlet_mask, zone1.paused) for zone1, _ in kohler.words()]


async def in_flight(kohler: HeldKohler, count: int = 1) -> None:
    await eventually(lambda: kohler.held == count, "a command to be in flight")


def release(kohler: HeldKohler, gate: asyncio.Event) -> None:
    gate.set()
    kohler.gate = None


# --------------------------------------------------------------------------- #
# 1. A report after a command narrows the words it sent; it never widens them
# --------------------------------------------------------------------------- #
async def test_a_report_from_before_a_stop_does_not_turn_the_water_back_on(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """🚨 A status the valve sent before it had the Stop used to be the next baseline."""
    await setup(hass, config_entry)
    await solo(hass, word(0b001))

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, SHOWER_ON)
    await solo(hass, word(0b001))  # sent before the valve applied the Stop
    await set_number(hass, TEMPERATURE, 100)

    assert masks(kohler) == [(0, False), (0, False)]


async def test_the_late_echo_of_an_outlet_switched_on_does_not_reopen_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """On, off, the on's echo arriving late, then another outlet: it went out as 0x03."""
    await setup(hass, config_entry)
    await solo(hass, word(0))

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, RAINHEAD)
    await solo(hass, word(0b001))  # the first command's echo, after the second was sent
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWERHEAD)

    assert [mask for mask, _ in masks(kohler)] == [0b001, 0b000, 0b010]


async def test_a_report_from_before_a_pause_does_not_lift_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """0x41 sent, a running 0x01 from before it arrives, and the next command ran water."""
    await setup(hass, config_entry)
    await solo(hass, word(0b001, paused=True))

    await set_number(hass, TEMPERATURE, 104)
    await solo(hass, word(0b001))
    await set_number(hass, FLOW, 60)

    assert masks(kohler) == [(0b001, True), (0b001, True)]


async def test_a_stop_at_the_wall_while_a_command_is_in_flight_is_not_hidden(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """The report the command was sent over is read before its POST, not after it."""
    await setup(hass, config_entry)
    await solo(hass, word(0b010))
    valve = valve_of(hass, config_entry)
    gate = kohler.hold(SOLO)

    opening = asyncio.create_task(valve.async_set_zone_outlet(1, 1, True))
    await in_flight(kohler)
    deliver_solo(word(0))  # Stop pressed at the panel meanwhile
    await eventually(lambda: valve.gcs_state.valve1.outlet_mask == 0, "the report")
    release(kohler, gate)
    await opening
    await set_number(hass, TEMPERATURE, 104)

    assert [mask for mask, _ in masks(kohler)] == [0b011, 0b000]


async def test_the_echo_of_the_last_command_keeps_it_standing(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Guard: a report that agrees narrows nothing (GitHub issue #1 stays fixed)."""
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await solo(hass, word(0b001))
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWERHEAD)

    assert [mask for mask, _ in masks(kohler)] == [0b001, 0b011]


# --------------------------------------------------------------------------- #
# 2. A command that fails puts the one before it back
# --------------------------------------------------------------------------- #
async def test_a_failed_command_leaves_the_one_before_it_standing(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Its overlay used to go with it, and the retry closed the outlet just opened."""
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    kohler.fail_api(SOLO, (500, {"message": "oops"}))
    with pytest.raises(HomeAssistantError):
        await set_number(hass, TEMPERATURE, 104)
    await set_number(hass, TEMPERATURE, 104)

    assert [mask for mask, _ in masks(kohler)] == [0b001, 0b001, 0b001]


async def test_a_cancelled_command_leaves_the_one_before_it_standing(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)

    await valve.async_set_zone_outlet(1, 1, True)
    gate = kohler.hold(SOLO)
    warming = asyncio.create_task(valve.async_apply_valve(zone1_temperature=104))
    await in_flight(kohler)
    warming.cancel()
    with pytest.raises(asyncio.CancelledError):
        await warming
    release(kohler, gate)
    await valve.async_apply_valve(zone1_temperature=104)

    assert kohler.words()[-1][0].outlet_mask == 0b001


async def test_a_stop_that_failed_is_not_undone_by_the_next_command(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """A POST that failed may still have reached the valve; what it closed stays closed."""
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await solo(hass, word(0b001))
    kohler.fail_api(SOLO, kohler.network_error())
    with pytest.raises(HomeAssistantError):
        await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, SHOWER_ON)
    await set_number(hass, TEMPERATURE, 104)

    assert kohler.words()[-1][0].outlet_mask == 0


async def test_a_stop_that_failed_with_nothing_standing_in_is_not_undone_either(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """A shower started at the panel: the report is what the Stop was built on."""
    await setup(hass, config_entry)
    await solo(hass, word(0b001))

    kohler.fail_api(SOLO, kohler.network_error())
    with pytest.raises(HomeAssistantError):
        await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, SHOWER_ON)
    await set_number(hass, TEMPERATURE, 104)

    assert kohler.words()[-1][0].outlet_mask == 0


async def test_an_outlet_whose_command_failed_is_not_opened_by_the_next_one(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Guard: narrowing by a failed command never takes up what it would have opened."""
    await setup(hass, config_entry)
    await solo(hass, word(0))

    kohler.fail_api(SOLO, kohler.network_error())
    with pytest.raises(HomeAssistantError):
        await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await set_number(hass, TEMPERATURE, 104)

    assert [mask for mask, _ in masks(kohler)] == [0b001, 0b000]


# --------------------------------------------------------------------------- #
# 3. An outlet setting is not a valve word
# --------------------------------------------------------------------------- #
async def test_an_outlet_setting_keeps_the_command_standing_in_for_its_echo(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await choose(hass, DURATION, "45 minutes")
    await set_number(hass, TEMPERATURE, 104)

    assert [mask for mask, _ in masks(kohler)] == [0b001, 0b001]


async def test_an_outlet_setting_while_a_command_is_in_flight_lets_it_stand_in(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """It counted as "something else sent meanwhile", so the command never armed."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    gate = kohler.hold(SOLO)

    opening = asyncio.create_task(valve.async_set_zone_outlet(1, 1, True))
    await in_flight(kohler)
    await valve.async_write_outlet_setting(maximum_run_time=2700)
    release(kohler, gate)
    await opening
    await valve.async_apply_valve(zone1_temperature=104)

    assert [mask for mask, _ in masks(kohler)] == [0b001, 0b001]


async def test_an_outlet_setting_still_stops_a_custom_shower_resuming(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Guard: the watcher still counts it, as it counts every command sent since."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await valve.async_custom_shower("0184C801", "00000000", keep_on_after_warmup=True)

    await valve.async_write_outlet_setting(maximum_run_time=2700)
    deliver_solo(word(0b111), **WARMING)
    await asyncio.sleep(0.05)
    deliver_solo(
        word(0, paused=True, at_temperature=True), warmUpStatus="warmUpNotInProgress"
    )
    await eventually(lambda: valve._custom_shower_task is None, "the watcher")

    assert len(kohler.sent(SOLO)) == 1


# --------------------------------------------------------------------------- #
# 4. A read-back the cloud has not caught up with
# --------------------------------------------------------------------------- #
def write_issue(hass: HomeAssistant, setting: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"outlet_write_unverified_{setting}_{device_issue_key(VALVE_ID)}"
    )


def scald_limits(valve: Any) -> set[int]:
    return {
        r.maximum_temperature_tenths for r in valve.gcs_state.outlet_limits.values()
    }


async def test_a_stale_read_back_keeps_the_scald_limit_just_written(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """🚨 The read put 47.7 °C back, the next setting echoed it, and its check hid that.

    The read-back keeps 43.3 °C in the cache. Another setting written once the catch-up
    window has passed reads first: while the cloud still shows 47.7 °C the scald limit is
    disputed and nothing is sent; once it shows 43.3 °C, the write goes out with it.
    """
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    kohler.apply_outlet_writes = False  # the cloud document has not caught up
    with patch(f"{COORDINATOR}.OUTLET_WRITE_VERIFY_DELAY_SECONDS", 0):
        await valve.async_write_outlet_setting(maximum_temperature_tenths=433)
        await hass.async_block_till_done()

        assert scald_limits(valve) == {433}
        assert write_issue(hass, "maximum_temperature_tenths") is not None

        kohler.posts.clear()
        with pytest.raises(HomeAssistantError, match="Set Max Temperature again first"):
            await valve.async_write_outlet_setting(maximum_run_time=2700)
        assert kohler.sent(WRITE_OUTLET) == []
        assert scald_limits(valve) == {433}

        # The cloud catches up; the check before the next write confirms 43.3 °C.
        kohler.apply_outlet_writes = True
        for outlet in range(3):
            kohler.outlet_fields.setdefault(outlet, {})["maximumOutletTemperature"] = (
                "43.3"
            )
        await valve.async_write_outlet_setting(maximum_run_time=2700)
        await hass.async_block_till_done()

    records = [b["gcsOutletConfigControlModel"] for b in kohler.sent(WRITE_OUTLET)]
    assert [r["maximumOutletTemperature"] for r in records] == ["433"] * 3
    assert write_issue(hass, "maximum_run_time") is None
    # Confirmed by the read before the write, so its warning went with it.
    assert write_issue(hass, "maximum_temperature_tenths") is None


async def test_another_settings_read_back_does_not_restore_an_unconfirmed_limit(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Inside the catch-up window the second setting goes out with the value just written,
    and a read still showing the old scald limit does not put it back in the cache."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    kohler.apply_outlet_writes = False
    # A window neither write gets past, so neither reads first.
    with patch(f"{COORDINATOR}.OUTLET_WRITE_VERIFY_DELAY_SECONDS", 3600):
        await valve.async_write_outlet_setting(maximum_temperature_tenths=433)
        kohler.posts.clear()
        await valve.async_write_outlet_setting(maximum_run_time=2700)
        records = [b["gcsOutletConfigControlModel"] for b in kohler.sent(WRITE_OUTLET)]
        assert [
            (r["maximumOutletTemperature"], r["maximumRuntime"]) for r in records
        ] == [("433", "2700")] * 3

        # What either check would read back: the cloud still on 47.7 °C and 30 minutes.
        stale = outlet_limits_from_settings(
            await valve.client.async_get_gcs_settings(VALVE_ID)
        )
        valve._adopt_outlet_read(stale)
        valve._settle_outlet_fields(stale)
        assert scald_limits(valve) == {433}
        # Inside the window a lagging cloud is no dispute.
        assert valve._outlet_disputed == {}
        for task in list(valve._verify_tasks.values()):
            task.cancel()
        await hass.async_block_till_done()


async def test_a_read_that_confirms_a_setting_clears_only_its_warning(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Each setting's warning is its own: a read showing one of them clears that one.

    Two settings written from here and unconfirmed, each with its warning standing; the
    cloud then shows the Default Temperature but not yet the duration, and a reseed reads
    it. A write cannot set this up any more — the second would read first and refuse —
    so it starts from the state two such writes leave behind.
    """
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    for field, setting in (
        ("maximum_run_time", "Max Shower Duration (45 minutes)"),
        ("default_temperature_tenths", "Default Temperature (40.0 °C)"),
    ):
        valve._raise_write_issue((field,), setting, "still the old value")
    valve._outlet_unconfirmed = {
        "maximum_run_time": 2700,
        "default_temperature_tenths": 400,
    }
    for outlet in range(3):
        kohler.outlet_fields.setdefault(outlet, {})["defaultOutletTemperature"] = "40.0"
    with patch(f"{COORDINATOR}.OUTLET_WRITE_VERIFY_DELAY_SECONDS", 0):
        await account(hass, config_entry)._async_seed_state()
        await hass.async_block_till_done()

    assert write_issue(hass, "default_temperature_tenths") is None
    assert write_issue(hass, "maximum_run_time") is not None
    assert valve._outlet_disputed == {"maximum_run_time": 1800}


# --------------------------------------------------------------------------- #
# 5. A cancelled command does not leave its guess standing
# --------------------------------------------------------------------------- #
async def _cancel_while_in_flight(
    hass: HomeAssistant, kohler: HeldKohler, domain: str, service: str, data: dict
) -> None:
    """Start a service call, hold its command in flight, and cancel it there.

    What a script stopped mid-action, or an automation in `mode: restart` re-triggering,
    does to the call it was waiting on.
    """
    gate = kohler.hold(SOLO)
    task = hass.async_create_task(
        hass.services.async_call(domain, service, data, blocking=True)
    )
    await in_flight(kohler)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release(kohler, gate)
    await hass.async_block_till_done()


async def test_a_cancelled_stop_leaves_the_shower_switch_showing_the_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """🚨 It read "off" for as long as the water kept running."""
    await setup(hass, config_entry)
    await solo(hass, word(0b001))

    await _cancel_while_in_flight(
        hass, kohler, SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SHOWER_ON}
    )
    await solo(hass, word(0b001))  # the valve is still running

    assert state(hass, SHOWER_ON).state == STATE_ON


async def test_a_cancelled_favorite_stop_leaves_the_select_showing_the_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    kohler.gcs_presets = copy.deepcopy(PRESETS)
    await setup(hass, config_entry)
    await solo(hass, word(0b001), preset=2)
    assert state(hass, FAVORITE).state == "Morning"

    await _cancel_while_in_flight(
        hass,
        kohler,
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: FAVORITE, "option": "Off"},
    )

    assert state(hass, FAVORITE).state == "Morning"


# --------------------------------------------------------------------------- #
# 6. A Stop goes ahead of everything asked for before it
# --------------------------------------------------------------------------- #
async def test_a_stop_goes_ahead_of_commands_queued_before_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """It waited for every one of them, each a POST of up to 30 s that could run water."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    gate = kohler.hold(SOLO)

    first = asyncio.create_task(valve.async_set_zone_outlet(1, 1, True))
    await in_flight(kohler)
    queued = asyncio.create_task(valve.async_set_zone_outlet(1, 2, True))
    await asyncio.sleep(0)
    stop = asyncio.create_task(valve.async_stop_shower())
    await asyncio.sleep(0)
    later = asyncio.create_task(valve.async_apply_valve(zone1_temperature=104))
    await asyncio.sleep(0)
    release(kohler, gate)

    await first
    with pytest.raises(HomeAssistantError, match="superseded by a Stop"):
        await queued
    await stop
    await later  # asked for after the Stop, so it goes ahead normally
    assert [mask for mask, _ in masks(kohler)] == [0b001, 0b000, 0b000]


async def test_a_favorite_asked_for_before_a_stop_is_not_started(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    gate = kohler.hold(SOLO)

    first = asyncio.create_task(valve.async_set_zone_outlet(1, 1, True))
    await in_flight(kohler)
    favorite = asyncio.create_task(valve.async_activate_preset(2))
    await asyncio.sleep(0)
    stop = asyncio.create_task(valve.async_stop_shower())
    await asyncio.sleep(0)
    release(kohler, gate)

    await first
    with pytest.raises(HomeAssistantError, match="superseded by a Stop"):
        await favorite
    await stop
    assert kohler.sent(CONTROL_PRESET) == []
    assert [mask for mask, _ in masks(kohler)] == [0b001, 0b000]


async def test_a_custom_shower_asked_for_before_a_stop_is_not_sent(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    gate = kohler.hold(SOLO)

    first = asyncio.create_task(valve.async_set_zone_outlet(1, 1, True))
    await in_flight(kohler)
    shower = asyncio.create_task(
        valve.async_custom_shower("0184C802", "00000000", keep_on_after_warmup=True)
    )
    await asyncio.sleep(0)
    stop = asyncio.create_task(valve.async_stop_shower())
    await asyncio.sleep(0)
    release(kohler, gate)

    await first
    with pytest.raises(HomeAssistantError, match="superseded by a Stop"):
        await shower
    await stop
    assert watchers() == []
    assert [mask for mask, _ in masks(kohler)] == [0b001, 0b000]


async def test_a_custom_shower_does_not_resume_once_a_stop_is_asked_for(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Even a Stop still waiting its turn: the watcher yields to it rather than racing it."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await valve.async_custom_shower("0184C801", "00000000", keep_on_after_warmup=True)

    valve._stop_generation += 1  # what `async_stop_shower` does first, before the lock
    deliver_solo(word(0b111), **WARMING)
    await asyncio.sleep(0.05)
    deliver_solo(
        word(0, paused=True, at_temperature=True), warmUpStatus="warmUpNotInProgress"
    )
    await eventually(lambda: valve._custom_shower_task is None, "the watcher")

    assert len(kohler.sent(SOLO)) == 1


# --------------------------------------------------------------------------- #
# 7. Nothing is written when any outlet would refuse it
# --------------------------------------------------------------------------- #
async def test_an_outlet_the_write_would_refuse_stops_it_before_the_first_outlet(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Outlet 3's flow ceiling is out of range: outlets 1 and 2 used to be written first."""
    kohler.outlet_fields = {2: {"maximumFlowrate": "63.75"}}  # 255 on the wire
    await setup(hass, config_entry)

    with pytest.raises(HomeAssistantError, match="Nothing was written") as raised:
        await valve_of(hass, config_entry).async_write_outlet_setting(
            maximum_run_time=2700
        )
    assert "outlet 3" in str(raised.value)
    assert "maximumFlowrate" in str(raised.value)
    assert kohler.sent(WRITE_OUTLET) == []


# --------------------------------------------------------------------------- #
# 8. A command during a warm-up
# --------------------------------------------------------------------------- #
async def _warming_up_after_rainhead(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> Any:
    """Rainhead on from here, the valve warming up all three outlets, the echo's hold over."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await solo(hass, word(0b111), **WARMING)
    valve._sent_at -= valve._sent_hold_seconds + 1
    return valve


async def test_a_change_during_a_warmup_keeps_the_outlets_this_integration_opened(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """🚨 It took the warm-up's 0x07, and the warm-up became a shower on all three."""
    await _warming_up_after_rainhead(hass, config_entry)

    await set_number(hass, TEMPERATURE, 104)

    zone1, _ = kohler.words()[-1]
    assert (zone1.outlet_mask, zone1.temperature_celsius) == (0b001, 40.0)


async def test_an_outlet_switched_on_during_a_warmup_joins_the_shower(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    await _warming_up_after_rainhead(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWERHEAD)

    assert kohler.words()[-1][0].outlet_mask == 0b011


async def test_a_change_during_a_warmup_started_elsewhere_is_refused(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Which outlets the panel's shower is for, nothing here can know."""
    await setup(hass, config_entry)
    await solo(hass, word(0b111), **WARMING)

    with pytest.raises(HomeAssistantError, match="warming up"):
        await set_number(hass, TEMPERATURE, 104)
    with pytest.raises(HomeAssistantError, match="warming up"):
        await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWERHEAD)
    assert kohler.sent(SOLO) == []


async def test_a_warmup_after_the_shower_was_restarted_at_the_wall_is_not_ours(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """The last outlets sent from here a minute ago are not the shower warming up now."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await solo(hass, word(0))  # stopped at the panel
    valve._sent_at -= 60
    await solo(hass, word(0b111), **WARMING)  # and started again there

    with pytest.raises(HomeAssistantError, match="warming up"):
        await set_number(hass, TEMPERATURE, 104)
    assert len(kohler.sent(SOLO)) == 1


async def test_a_stop_and_a_whole_shower_are_sent_during_any_warmup(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Guard: neither builds on the warm-up's outlets, so neither is refused."""
    await setup(hass, config_entry)
    await solo(hass, word(0b111), **WARMING)

    await custom_shower(hass, zone1_temperature=104, zone1_outlet_2=True)
    await solo(hass, word(0b111), **WARMING)
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, SHOWER_ON)

    assert [mask for mask, _ in masks(kohler)] == [0b010, 0b000]


async def test_a_raw_word_for_one_zone_during_a_warmup_started_elsewhere_is_refused(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Zone 2 "as it stands" is the warm-up's set; with both words given, it is sent."""
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)
    await solo(hass, word(0b111), word(0b011, zone=2), **WARMING)

    with pytest.raises(HomeAssistantError, match="warming up"):
        await send_valve_hex(hass, zone1_hex="0184C801")
    await send_valve_hex(hass, zone1_hex="0184C801", zone2_hex="1184C800")

    [(zone1, zone2)] = kohler.words()
    assert zone2 is not None
    assert (zone1.outlet_mask, zone2.outlet_mask) == (0b001, 0)


# --------------------------------------------------------------------------- #
# 10. custom_shower's flow is a percent of each zone's own ceiling
# --------------------------------------------------------------------------- #
async def test_custom_shower_flow_is_a_percent_of_each_zones_own_ceiling(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Zone 1 tops out at byte 100, zone 2 at 200: 50 % is byte 50 and byte 100."""
    kohler.valve_outlets = (3, 3)
    kohler.outlet_fields = {i: {"maximumFlowrate": "25"} for i in range(3)}
    await setup(hass, config_entry)

    await custom_shower(
        hass, zone1_temperature=104, zone1_outlet_1=True, zone2_outlet_1=True, flow=50
    )
    await custom_shower(hass, zone1_temperature=104, zone1_outlet_1=True)

    # Percent of the codec's 200, as `ValveWord.flow_percent` decodes it.
    flows = [
        (zone1.flow_percent, zone2.flow_percent) for zone1, zone2 in kohler.words()
    ]
    assert flows == [(25.0, 50.0), (50.0, 100.0)]
