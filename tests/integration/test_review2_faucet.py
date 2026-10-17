"""Faucet fixes from the second 2026-10-16 review.

Above all the water safety limit, which has two rules. While the water is not known to be
off, it never stops trying to turn it off once the limit is reached — across restarts and
reloads, however long Kohler cannot be reached. And it never cuts off water someone turned
on by hand after Home Assistant's run ended. Also: instant-update verification kept across
restarts, presets kept through a failed read, and re-reads a message asked for.

Each test here failed before its fix, except those marked as guards: they pin down a rule
the fixes must keep.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.select import SERVICE_SELECT_OPTION
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.kohler_konnect.const import (
    DOMAIN,
    FAUCET_COMMAND_FOLLOW_UP,
    FAUCET_CONFIG_REFRESH_INTERVAL,
    FAUCET_PUSH_GRACE,
    FAUCET_SCAN_INTERVAL_PUSH,
    FAUCET_STORAGE_KEY,
    FAUCET_STORAGE_VERSION,
    device_issue_key,
)

from .conftest import (
    DEVICE_ID,
    FakeMqttClient,
    account,
    faucet,
    feed_message,
    wait_for,
    water_message,
)
from .test_review_faucet import (
    PRESET,
    PRESETS,
    STATE_PATH,
    SWITCH,
    SlowKohler,
    _actions,
    _advance,
    _calls,
    _deliver,
    _spin,
    _verify_stream,
    _water_on,
)

# Its translation key, the faucet's issue key after it: removing the entry clears it.
ISSUE_ID = f"faucet_auto_off_failed_{device_issue_key(DEVICE_ID)}"
# Deleted in the app, still in the account's legacy `sensateExperiences`.
DELETED = {"experienceId": "old", "title": "Deleted big pot", "dispenseAmount": 8.0}


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> SlowKohler:
    return SlowKohler(aioclient_mock)


def _release(kohler: SlowKohler, gate: asyncio.Event) -> None:
    """Let held replies go, so a failed test cannot hang its teardown on one."""
    kohler.held.clear()
    kohler.fail_held.clear()
    gate.set()


async def _until(condition: Callable[[], bool], tries: int = 500) -> None:
    """Wait on the real clock, while a held reply keeps `async_block_till_done` waiting."""
    for _ in range(tries):
        if condition():
            return
        for _ in range(20):
            await asyncio.sleep(0)
        await asyncio.get_running_loop().run_in_executor(None, time.sleep, 0.01)
    pytest.fail("timed out")


async def _steps(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, step: timedelta, n: int
) -> None:
    """Let time pass a poll at a time, so each timer fires when it would."""
    for _ in range(n):
        await _advance(hass, freezer, step)


async def _past_the_deadline(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The ten minutes of the limit and a second, a minute at a time."""
    await _steps(hass, freezer, timedelta(minutes=1), 10)
    await _advance(hass, freezer, timedelta(seconds=1))


async def _water_off(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )


def _stored(hass_storage: dict[str, Any], entry: MockConfigEntry) -> dict[str, Any]:
    """What the entry's faucet store holds for the faucet."""
    key = FAUCET_STORAGE_KEY.format(entry_id=entry.entry_id)
    return hass_storage.get(key, {}).get("data", {}).get(DEVICE_ID, {})


def _issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)


async def _reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    stream = account(hass, entry).stream
    await wait_for(hass, lambda: stream.connected, "the stream")


# --- 1. the limit never gives up ----------------------------------------------------


async def test_the_safety_limit_never_gives_up(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Hours of outage: tried until it works, the deadline kept, a repair issue raised.

    1.03 gave up after ten failed OFFs a minute apart and dropped the limit, so eleven
    minutes of outage left the water running for good.
    """
    caplog.set_level(logging.WARNING)
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(seconds=30))
    kohler.fail_api("/onoff", *[kohler.network_error() for _ in range(100)])
    coordinator = faucet(hass, setup_entry)

    await _advance(hass, freezer, timedelta(minutes=9, seconds=31))  # the first try
    await _advance(hass, freezer, timedelta(minutes=1))
    assert _issue(hass) is None  # two failures are not yet worth a repair
    await _advance(hass, freezer, timedelta(minutes=2))
    issue = _issue(hass)
    assert issue is not None
    assert issue.translation_placeholders["name"] == "Kitchen"
    assert issue.severity is ir.IssueSeverity.ERROR

    await _steps(hass, freezer, timedelta(minutes=15), 12)  # three hours of it
    assert coordinator._auto_off_at is not None
    assert _stored(hass_storage, setup_entry)["auto_off_at"] == coordinator._auto_off_at
    assert _actions(kohler) == ["ON"]  # every OFF so far failed
    warned = [r for r in caplog.records if "Could not turn off" in r.getMessage()]
    assert len(warned) == 1

    # Kohler is back: the next try goes through, and once the water is reported off the
    # limit and the repair go, and nothing more is sent.
    kohler.api_queue.clear()
    await _advance(hass, freezer, timedelta(minutes=15))
    assert _actions(kohler) == ["ON", "OFF"]
    kohler.state["status"] = "Off"
    await _advance(hass, freezer, timedelta(seconds=5))
    assert coordinator._auto_off_at is None
    assert _issue(hass) is None
    assert _stored(hass_storage, setup_entry)["auto_off_at"] is None
    await _advance(hass, freezer, timedelta(hours=1))
    assert _actions(kohler) == ["ON", "OFF"]


async def test_the_safety_limit_waits_as_long_as_kohler_asks(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """A throttled OFF is tried again after Kohler's Retry-After, not a minute later."""
    await _water_on(hass)
    kohler.state["status"] = "On"
    kohler.fail_api("/onoff", (429, {}, {"Retry-After": "600"}))

    await _advance(hass, freezer, timedelta(minutes=10, seconds=1))
    assert _calls(kohler, "/onoff") == 2  # the ON, and the throttled OFF
    await _advance(hass, freezer, timedelta(minutes=9))
    assert _calls(kohler, "/onoff") == 2
    await _advance(hass, freezer, timedelta(minutes=1, seconds=1))
    assert _actions(kohler) == ["ON", "OFF"]


async def test_the_safety_limit_carries_on_after_a_reload(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Twenty minutes of failed OFFs, then a reload: the next setup keeps trying."""
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(seconds=30))
    kohler.fail_api("/onoff", *[kohler.network_error() for _ in range(100)])
    await _advance(hass, freezer, timedelta(minutes=9, seconds=31))
    await _steps(hass, freezer, timedelta(minutes=1), 20)

    kohler.api_queue.clear()
    await _reload(hass, setup_entry)
    await _advance(hass, freezer, FAUCET_COMMAND_FOLLOW_UP + timedelta(seconds=1))
    assert _actions(kohler) == ["ON", "OFF"]


async def test_an_off_from_home_assistant_past_the_limit_must_be_seen_to_work(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Once the limit is trying, only the water known off ends it, whoever sends the OFF.

    Turning the water off from Home Assistant while the limit kept failing dropped the
    limit as soon as Kohler took the command, though the faucet ran on.
    """
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(seconds=30))
    kohler.fail_api("/onoff", kohler.network_error())
    await _advance(hass, freezer, timedelta(minutes=9, seconds=31))  # the first try

    await _water_off(hass)  # Kohler takes this one; the faucet keeps running
    await _steps(hass, freezer, timedelta(seconds=5), 4)
    assert faucet(hass, setup_entry)._auto_off_at is not None
    await _advance(hass, freezer, timedelta(minutes=1))
    assert _actions(kohler) == ["ON", "OFF", "OFF"]  # the switch's, then the limit's

    kohler.state["status"] = "Off"
    await _advance(hass, freezer, timedelta(seconds=5))
    assert faucet(hass, setup_entry)._auto_off_at is None


# --- 2. an ON on its way --------------------------------------------------------------


async def test_a_reload_while_the_on_is_on_its_way_keeps_the_limit(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The limit is saved before the ON goes, and the unloaded copy saves nothing after.

    It used to be set once the ON was through — in the copy being unloaded, after the next
    setup had read the store, which that copy's late save then overwrote.
    """
    kohler.held["/onoff"] = gate = asyncio.Event()
    on = hass.async_create_task(_water_on(hass))
    old = faucet(hass, setup_entry)
    try:
        await _until(lambda: _calls(kohler, "/onoff") == 1)
        reload = hass.async_create_task(
            hass.config_entries.async_reload(setup_entry.entry_id)
        )
        await _until(reload.done)
        new = faucet(hass, setup_entry)
        limit_at_setup = new._auto_off_at
        # Something the new copy saves meanwhile: a leak alert.
        await _until(lambda: account(hass, setup_entry).stream.connected)
        FakeMqttClient.instances[-1].deliver(feed_message("SENSATE_LEAK_DETECTED_ALT"))
        await _until(lambda: _stored(hass_storage, setup_entry).get("leak_alert_at"))
        kohler.state["status"] = "On"  # the command took
    finally:
        _release(kohler, gate)
    await on
    await hass.async_block_till_done()
    assert new is not old
    assert limit_at_setup is not None
    assert _stored(hass_storage, setup_entry)["leak_alert_at"] is not None
    assert _stored(hass_storage, setup_entry)["auto_off_at"] == new._auto_off_at

    await _past_the_deadline(hass, freezer)
    assert _actions(kohler) == ["ON", "OFF"]


async def test_an_on_that_got_no_answer_keeps_its_limit(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """A timed-out ON may have turned the water on, so its limit stays until it is off."""
    kohler.fail_api("/onoff", kohler.network_error())
    with pytest.raises(HomeAssistantError):
        await _water_on(hass)
    kohler.state["status"] = "On"  # it did reach the faucet
    await _past_the_deadline(hass, freezer)
    assert _actions(kohler) == ["OFF"]


async def test_an_on_that_got_no_answer_and_did_nothing_frees_the_faucet(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Guard: its limit goes once reads say the water is off, before anyone is cut off."""
    kohler.fail_api("/onoff", kohler.network_error())
    with pytest.raises(HomeAssistantError):
        await _water_on(hass)
    await _steps(hass, freezer, timedelta(seconds=30), 4)
    assert faucet(hass, setup_entry)._auto_off_at is None
    kohler.state["status"] = "On"  # someone uses the faucet by hand
    await _steps(hass, freezer, timedelta(minutes=1), 10)
    assert _actions(kohler) == []


async def test_an_on_kohler_refused_leaves_no_limit(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Guard: the limit set before the ON goes again when Kohler refuses it."""
    kohler.fail_api("/onoff", (200, {"statusCode": "906", "message": "x"}))
    with pytest.raises(HomeAssistantError):
        await _water_on(hass)
    assert faucet(hass, setup_entry)._auto_off_at is None
    assert _stored(hass_storage, setup_entry).get("auto_off_at") is None


async def test_no_command_is_sent_once_unloaded(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: SlowKohler
) -> None:
    """A command still waiting when the entry unloads is refused, not sent."""
    coordinator = faucet(hass, setup_entry)
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_water(True)
    assert kohler.commands == []


# --- 3. water never seen running --------------------------------------------------------


async def test_an_on_that_never_took_does_not_cut_off_a_later_user(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Every read says off: Home Assistant's run is over, and the limit with it."""
    await _water_on(hass)
    await _steps(hass, freezer, timedelta(seconds=30), 16)
    kohler.state["status"] = "On"  # minute 8: someone uses the faucet by hand
    await _steps(hass, freezer, timedelta(seconds=30), 6)
    assert _actions(kohler) == ["ON"]


async def test_an_on_that_never_took_with_instant_updates(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The same with a trusted stream, which polls every five minutes when idle."""
    await _verify_stream(hass, freezer)
    await _water_on(hass)
    await _steps(hass, freezer, timedelta(seconds=30), 16)
    assert faucet(hass, push_entry)._auto_off_at is None
    kohler.state["status"] = "On"
    await _deliver(hass, freezer, water_message("On"))
    await _steps(hass, freezer, timedelta(seconds=30), 6)
    assert _actions(kohler) == ["ON"]


async def test_water_stopped_while_the_stream_was_down_frees_the_faucet(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The stream missed the water stopping: the read after it reconnects counts.

    Seen running by the stream, the limit took only the stream's word that it stopped —
    after a reconnect too, though the "off" had been lost in the gap.
    """
    await _verify_stream(hass, freezer)
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _deliver(hass, freezer, water_message("On"))
    stream = account(hass, push_entry).stream
    FakeMqttClient.refuse = True
    FakeMqttClient.instances[-1].drop()
    await _advance(hass, freezer, timedelta(seconds=1))  # its re-read: still running

    kohler.state["status"] = "Off"  # turned off at the sink, unreported
    FakeMqttClient.refuse = False
    for _ in range(40):
        if stream.connected:
            break
        await _advance(hass, freezer, timedelta(seconds=0.1))
    await wait_for(hass, lambda: stream.connected, "the stream to reconnect")
    await _advance(hass, freezer, timedelta(seconds=1))  # the re-read on reconnecting
    # Seconds after the ON its "off" may predate it — the "running" before it too — so it
    # waits until the cloud has had a minute; polled quickly meanwhile.
    assert faucet(hass, push_entry)._auto_off_at is not None
    await _steps(hass, freezer, timedelta(seconds=5), 12)
    assert faucet(hass, push_entry)._auto_off_at is None

    await _advance(hass, freezer, timedelta(minutes=7))
    kohler.state["status"] = "On"  # minute 8: someone uses the faucet by hand
    await _deliver(hass, freezer, water_message("On"))
    await _steps(hass, freezer, timedelta(seconds=30), 6)
    assert _actions(kohler) == ["ON"]


# --- 4. an OFF Kohler took ----------------------------------------------------------


async def test_an_accepted_off_is_sent_again_until_the_water_is_off(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Kohler takes the OFF but the faucet runs on: sent again, the limit kept."""
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(seconds=30))
    await _advance(hass, freezer, timedelta(minutes=9, seconds=31))
    assert _actions(kohler) == ["ON", "OFF"]

    await _steps(hass, freezer, timedelta(seconds=5), 12)  # a minute of "On"
    assert _actions(kohler) == ["ON", "OFF", "OFF"]
    assert faucet(hass, setup_entry)._auto_off_at is not None
    await _advance(hass, freezer, timedelta(minutes=2))
    assert _issue(hass) is None
    await _advance(hass, freezer, timedelta(minutes=5))
    assert _actions(kohler) == ["ON", *["OFF"] * 4]
    assert _issue(hass) is not None

    kohler.state["status"] = "Off"
    await _advance(hass, freezer, timedelta(seconds=5))
    assert faucet(hass, setup_entry)._auto_off_at is None
    assert _issue(hass) is None
    await _advance(hass, freezer, timedelta(hours=1))
    assert _actions(kohler) == ["ON", *["OFF"] * 4]


async def test_an_off_the_stream_confirms_frees_the_faucet_at_once(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Guard: seconds after the limit turned the water off, it is someone else's again."""
    await _verify_stream(hass, freezer)
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _deliver(hass, freezer, water_message("On"))
    await _advance(hass, freezer, timedelta(minutes=10))
    assert _actions(kohler) == ["ON", "OFF"]

    kohler.state["status"] = "Off"
    await _deliver(hass, freezer, water_message("Off"))
    # Water stopped unexpectedly: the person at the sink turns it straight back on.
    kohler.state["status"] = "On"
    await _deliver(hass, freezer, water_message("On"))
    await _steps(hass, freezer, timedelta(minutes=1), 20)
    assert _actions(kohler) == ["ON", "OFF"]


# --- 5. what changes while an OFF is on its way ---------------------------------------


async def test_a_failed_off_after_the_water_stopped_is_not_tried_again(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The stream reports the water off while the OFF fails: no retry for the next user."""
    await _verify_stream(hass, freezer)
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _deliver(hass, freezer, water_message("On"))
    kohler.held["/onoff"] = gate = asyncio.Event()
    kohler.fail_held.add("/onoff")
    coordinator = faucet(hass, push_entry)
    freezer.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    try:
        await _until(lambda: _calls(kohler, "/onoff") == 2)
        kohler.state["status"] = "Off"  # turned off at the sink meanwhile
        FakeMqttClient.instances[-1].deliver(water_message("Off"))
        await _until(lambda: coordinator._auto_off_at is None)
    finally:
        _release(kohler, gate)  # the OFF fails
    await _spin(hass)

    kohler.state["status"] = "On"  # and on again by hand, within the minute
    FakeMqttClient.instances[-1].deliver(water_message("On"))
    await _steps(hass, freezer, timedelta(seconds=30), 6)
    assert _actions(kohler) == ["ON"]


async def test_water_turned_on_again_during_the_off_keeps_its_new_limit(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The limit's OFF lands after the next ON from Home Assistant: the new limit stays."""
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(seconds=30))
    kohler.held["/onoff"] = gate = asyncio.Event()
    coordinator = faucet(hass, setup_entry)
    freezer.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    try:
        await _until(lambda: _calls(kohler, "/onoff") == 2)
        kohler.held.clear()  # the ON goes straight through
        await _water_on(hass)
    finally:
        _release(kohler, gate)  # then the OFF lands
    await hass.async_block_till_done()
    assert coordinator._auto_off_at is not None

    await _past_the_deadline(hass, freezer)
    assert _actions(kohler) == ["ON", "OFF", "ON", "OFF"]


# --- 6. a deadline that passed while Home Assistant was down -----------------------------


@pytest.mark.parametrize("seen_running", [True, False], ids=["seen", "not_seen"])
async def test_a_deadline_that_passed_while_down_waits_for_a_read(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
    seen_running: bool,
) -> None:
    """Turned off by hand while Home Assistant was down: no OFF goes out blind.

    Water not seen running before the restart had its OFF sent before any read could say
    it was off; water seen running is a guard, as its first read already dropped the limit.
    """
    now = time.time()
    key = FAUCET_STORAGE_KEY.format(entry_id=config_entry.entry_id)
    hass_storage[key] = {
        "version": FAUCET_STORAGE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": {
            DEVICE_ID: {
                "auto_off_at": now - 3000,
                "auto_off_set_at": now - 3600,
                "auto_off_seen_running": seen_running,
            }
        },
    }
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await _steps(hass, freezer, timedelta(seconds=30), 4)
    assert _actions(kohler) == []
    assert faucet(hass, config_entry)._auto_off_at is None


async def test_a_deadline_that_passed_while_down_still_ends_with_an_off(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Guard: when no read can say the water is off, the OFF is sent all the same."""
    now = time.time()
    key = FAUCET_STORAGE_KEY.format(entry_id=config_entry.entry_id)
    hass_storage[key] = {
        "version": FAUCET_STORAGE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": {DEVICE_ID: {"auto_off_at": now - 3000}},  # as 1.03 kept it
    }
    kohler.fail_api(STATE_PATH, *[kohler.network_error() for _ in range(5)])
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await _advance(hass, freezer, FAUCET_COMMAND_FOLLOW_UP + timedelta(seconds=1))
    assert _actions(kohler) == ["OFF"]


# --- 7. what a stream message vouches for ---------------------------------------------


async def test_a_handle_report_does_not_vouch_for_an_old_status(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Only the read right after the ON said "off"; a handle report later is no proof."""
    await _water_on(hass)  # the read just after it still says "Off"
    kohler.fail_api(STATE_PATH, *[kohler.network_error() for _ in range(100)])
    await _steps(hass, freezer, timedelta(minutes=1), 5)
    await _deliver(hass, freezer, feed_message("SENSATE_STS", handle="OPEN"))
    await _steps(hass, freezer, timedelta(minutes=1), 5)
    assert _actions(kohler) == ["ON", "OFF"]


# --- 8. instant updates, verified once -------------------------------------------------


async def test_instant_updates_stay_verified_across_a_restart(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """No more 30-second polling after every restart until the faucet is next used."""
    await _verify_stream(hass, freezer)
    await _reload(hass, push_entry)
    await _advance(hass, freezer, timedelta(seconds=1))

    coordinator = faucet(hass, push_entry)
    assert coordinator.push_trusted
    assert coordinator.update_interval == FAUCET_SCAN_INTERVAL_PUSH
    report = (await get_diagnostics_for_config_entry(hass, hass_client, push_entry))[
        "faucets"
    ][0]
    assert report["update_interval"] == "0:05:00"
    assert report["instant_updates"] == {
        "messages": 0,
        "verified": True,
        "restored": True,
        "missed_changes": 0,
    }


async def test_a_restored_verification_is_still_taken_away_by_a_missed_change(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The self-check still works after a restart, and what it finds is kept too."""
    await _verify_stream(hass, freezer)
    await _reload(hass, push_entry)
    coordinator = faucet(hass, push_entry)
    assert coordinator.push_verified

    kohler.state["status"] = "On"  # by hand, and the stream says nothing
    await _advance(hass, freezer, FAUCET_SCAN_INTERVAL_PUSH)
    await _advance(hass, freezer, FAUCET_PUSH_GRACE)
    assert not coordinator.push_verified

    await _reload(hass, push_entry)
    assert not faucet(hass, push_entry).push_verified


# --- 9. presets through a failed read -------------------------------------------------


async def _with_presets(
    hass: HomeAssistant, entry: MockConfigEntry, kohler: SlowKohler
) -> None:
    kohler.faucet_presets = list(PRESETS)
    kohler.presets = [DELETED]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: PRESET, "option": "Kettle"},
        blocking=True,
    )


@pytest.mark.parametrize("failure", ["network", 500, 503])
async def test_a_failed_preset_read_keeps_the_presets(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
    failure: str | int,
) -> None:
    """One failed read used to swap in the legacy list: "Kettle" then poured 8 L."""
    await _with_presets(hass, config_entry, kohler)
    kohler.fail_api(
        "/faucet-experience",
        kohler.network_error() if failure == "network" else (failure, {}),
    )
    await _advance(hass, freezer, FAUCET_CONFIG_REFRESH_INTERVAL + timedelta(seconds=1))

    coordinator = faucet(hass, config_entry)
    assert list(coordinator.presets) == ["p1", "p2"]
    assert coordinator.preset_source == "faucet-experience"
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.kitchen_dispense_preset"},
        blocking=True,
    )
    assert [b["quantity"] for c, b in kohler.commands if c == "dispense"] == [1.2]


async def test_no_presets_left_is_not_the_legacy_list(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Once the faucet's own list has been read, a 404 from it means none are left."""
    await _with_presets(hass, config_entry, kohler)
    kohler.faucet_presets = None  # answered with a 404 from now on
    await _advance(hass, freezer, FAUCET_CONFIG_REFRESH_INTERVAL + timedelta(seconds=1))

    assert faucet(hass, config_entry).presets == {}
    assert _calls(kohler, "/customer-experience/") == 0
    assert hass.states.get(PRESET).state == STATE_UNAVAILABLE


# --- 10. a re-read a message asked for --------------------------------------------------


async def test_a_failed_reread_after_a_leak_alert_is_tried_again(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The leak history a leak alert asked for is read again, after a pause, and alone.

    Not at every refresh: that re-read all three reads at every poll while Kohler kept
    failing — every 5 s while the water ran. And not in five minutes either.
    """
    await _advance(hass, freezer, timedelta(seconds=5))
    reads = _calls(kohler, "/faucet-configuration/")
    presets = _calls(kohler, "/faucet-experience")
    kohler.fail_api(f"/faucet-configuration/{DEVICE_ID}", kohler.network_error())
    await _deliver(hass, freezer, feed_message("SENSATE_LEAK_DETECTED_ALT"))
    assert _calls(kohler, "/faucet-configuration/") == reads + 1  # failed
    assert _calls(kohler, "/faucet-experience") == presets + 1  # worked

    # The next refresh, from a water report that would not ask for one itself: too soon.
    await _deliver(hass, freezer, water_message("Off"))
    assert _calls(kohler, "/faucet-configuration/") == reads + 1

    # A minute on, the next refresh tries the one that failed — and only that one.
    await _advance(hass, freezer, timedelta(seconds=61))
    await _deliver(hass, freezer, water_message("Off"))
    assert _calls(kohler, "/faucet-configuration/") == reads + 2
    assert _calls(kohler, "/faucet-experience") == presets + 1


async def test_a_failed_firmware_check_after_an_install_is_tried_again(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The check an install result asked for is made again after a pause, not in 12 h."""
    kohler.firmware = {"firmwareUpdateAvailable": False, "firmware": "16.0"}
    await _advance(hass, freezer, timedelta(seconds=5))
    checks = _calls(kohler, "/firmware/sensate/")
    reads = _calls(kohler, "/faucet-configuration/")
    kohler.fail_api(f"/firmware/sensate/{DEVICE_ID}", kohler.network_error())
    await _deliver(
        hass, freezer, feed_message("INSTALL_FIRMWARE_STS", status="Installed")
    )
    assert _calls(kohler, "/firmware/sensate/") == checks + 1  # failed

    await _deliver(hass, freezer, water_message("Off"))
    assert _calls(kohler, "/firmware/sensate/") == checks + 1  # too soon

    await _advance(hass, freezer, timedelta(seconds=61))
    await _deliver(hass, freezer, water_message("Off"))
    assert _calls(kohler, "/firmware/sensate/") == checks + 2
    # The configuration it was asked for with worked the first time; it is not re-read.
    assert _calls(kohler, "/faucet-configuration/") == reads + 1
