"""Regression tests for the final review of the 2026-10-16 faucet fixes.

The water safety limit's two rules: **A** — while the water is not known to be off, never
stop trying to turn it off once the limit is reached; **B** — never cut off water a person
turned on by hand after Home Assistant's run ended.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kohler_konnect.const import (
    CONF_MAX_RUN_MINUTES,
    FAUCET_STORAGE_KEY,
    FAUCET_STORAGE_VERSION,
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
from .test_review2_faucet import DELETED, _steps, _stored, _water_off
from .test_review_faucet import (
    SlowKohler,
    _actions,
    _advance,
    _calls,
    _deliver,
    _verify_stream,
    _water_on,
)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> SlowKohler:
    return SlowKohler(aioclient_mock)


def _store(
    hass_storage: dict[str, Any], entry: MockConfigEntry, data: dict[str, Any]
) -> None:
    """What this faucet kept across a restart, before the entry is set up."""
    key = FAUCET_STORAGE_KEY.format(entry_id=entry.entry_id)
    hass_storage[key] = {
        "version": FAUCET_STORAGE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": {DEVICE_ID: data},
    }


# --------------------------------------------------------------------------- #
# Rule A: an "off" the cloud has not caught up with does not end the limit
# --------------------------------------------------------------------------- #
async def _stream_saw_it_running(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, entry: MockConfigEntry
) -> Any:
    """Home Assistant's ON; the stream reports it; the cloud still says "Off"."""
    await _verify_stream(hass, freezer)
    await _water_on(hass)
    await _deliver(hass, freezer, water_message("On"))
    coordinator = faucet(hass, entry)
    assert coordinator._auto_off_at is not None
    return coordinator


async def test_a_reconnect_while_the_cloud_lags_keeps_the_limit(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    coordinator = await _stream_saw_it_running(hass, freezer, push_entry)
    stream = account(hass, push_entry).stream
    FakeMqttClient.instances[-1].drop()
    for _ in range(40):
        if stream.connected:
            break
        await _advance(hass, freezer, timedelta(seconds=0.1))
    await wait_for(hass, lambda: stream.connected, "the stream to reconnect")
    await _advance(hass, freezer, timedelta(seconds=1))  # the re-read: a lagging "Off"
    assert coordinator._auto_off_at is not None, "dropped on a lagging read"

    kohler.state["status"] = "On"  # the cloud catches up: the water is running
    await _steps(hass, freezer, timedelta(minutes=1), 12)
    # The limit fired (and, as this faucet never turns off, goes on firing).
    assert _actions(kohler)[:2] == ["ON", "OFF"]


async def test_a_drop_while_the_cloud_lags_keeps_the_limit(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    await _stream_saw_it_running(hass, freezer, push_entry)
    FakeMqttClient.refuse = True
    FakeMqttClient.instances[-1].drop()
    await _advance(hass, freezer, timedelta(seconds=1))
    kohler.state["status"] = "On"
    await _steps(hass, freezer, timedelta(minutes=1), 12)
    assert _actions(kohler)[:2] == ["ON", "OFF"]


async def test_with_no_drop_the_limit_holds_as_before(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Guard: the stream's own word was always needed while it was trusted."""
    await _stream_saw_it_running(hass, freezer, push_entry)
    await _advance(hass, freezer, timedelta(seconds=3))
    kohler.state["status"] = "On"
    await _steps(hass, freezer, timedelta(minutes=1), 12)
    assert _actions(kohler)[:2] == ["ON", "OFF"]


# --------------------------------------------------------------------------- #
# Rule A: an OFF Kohler takes is not the water off
# --------------------------------------------------------------------------- #
async def test_an_off_the_faucet_ignores_does_not_end_the_limit(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Before the deadline: the switch's OFF is accepted, the faucet runs on."""
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(seconds=30))
    await _water_off(hass)  # accepted; the state stays "On"
    coordinator = faucet(hass, setup_entry)
    assert coordinator._auto_off_at is not None
    await _steps(hass, freezer, timedelta(minutes=1), 12)
    assert _actions(kohler).count("OFF") >= 2, (
        "the limit was dropped on an accepted OFF"
    )


async def test_the_same_after_a_restart_past_the_deadline(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The tries were not saved, so after a restart an accepted OFF ended the limit."""
    now = time.time()
    _store(
        hass_storage,
        config_entry,
        {
            "auto_off_at": now - 3600,
            "auto_off_set_at": now - 4200,
            "auto_off_seen_running": True,
        },
    )
    kohler.state["status"] = "On"  # still running
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = faucet(hass, config_entry)
    await _water_off(hass)  # accepted; the faucet keeps running
    await _steps(hass, freezer, timedelta(minutes=1), 30)
    assert coordinator._auto_off_at is not None
    assert _actions(kohler).count("OFF") >= 2


async def test_an_off_that_works_ends_the_limit_promptly(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Guard for rule B: the reading after it ends the limit, polled quickly — once the
    cloud has had its minute for the ON."""
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(seconds=65))
    await _water_off(hass)
    kohler.state["status"] = "Off"
    await _steps(hass, freezer, timedelta(seconds=5), 2)
    assert faucet(hass, setup_entry)._auto_off_at is None


# --------------------------------------------------------------------------- #
# A failed re-read is not repeated at every poll
# --------------------------------------------------------------------------- #
async def test_a_firmware_check_kohler_keeps_failing_is_not_asked_every_poll(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """An install result, then 503s for ten minutes with the stream down.

    Every 30 s poll used to re-read the configuration, both preset lists and firmware.
    """
    kohler.firmware = {"firmwareUpdateAvailable": False, "firmware": "16.0"}
    await _advance(hass, freezer, timedelta(seconds=5))
    kohler.fail_api(f"/firmware/sensate/{DEVICE_ID}", *[(503, {}) for _ in range(200)])
    await _deliver(
        hass, freezer, feed_message("INSTALL_FIRMWARE_STS", status="Installed")
    )
    FakeMqttClient.refuse = True
    FakeMqttClient.instances[-1].drop()
    await _advance(hass, freezer, timedelta(seconds=1))
    config, presets, firmware = (
        _calls(kohler, "/faucet-configuration/"),
        _calls(kohler, "/faucet-experience"),
        _calls(kohler, "/firmware/sensate/"),
    )
    await _steps(hass, freezer, timedelta(seconds=30), 20)  # ten minutes
    assert _calls(kohler, "/firmware/sensate/") - firmware <= 6
    assert _calls(kohler, "/faucet-configuration/") - config <= 3
    assert _calls(kohler, "/faucet-experience") - presets <= 3


# --------------------------------------------------------------------------- #
# Instant-update verification restored at setup survives the first connect
# --------------------------------------------------------------------------- #
async def test_the_first_connect_vouches_for_the_stream_from_then_on(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Without it, a change before the stream was up read as a missed announcement."""
    _store(hass_storage, config_entry, {"push_verified": True})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await wait_for(
        hass, lambda: account(hass, config_entry).stream.connected, "the stream"
    )
    coordinator = faucet(hass, config_entry)
    assert coordinator.push_verified
    assert coordinator._push_synced_at is not None


# --------------------------------------------------------------------------- #
# A faucet whose presets were all deleted stays without after a restart
# --------------------------------------------------------------------------- #
async def test_a_404_from_a_list_read_before_is_no_presets_after_a_restart(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Not the account's legacy list, which can bring deleted presets back."""
    _store(hass_storage, config_entry, {"faucet_preset_ids": ["deleted"]})
    kohler.faucet_presets = None  # its own list answers 404
    kohler.presets = [DELETED]  # deleted in the app, still in the legacy list
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert faucet(hass, config_entry).presets == {}
    assert _calls(kohler, "/customer-experience/") == 0


# --------------------------------------------------------------------------- #
# A limit turned off (0 minutes) leaves no old limit behind
# --------------------------------------------------------------------------- #
async def test_turning_the_limit_off_drops_one_kept_from_before(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    now = time.time()
    _store(
        hass_storage,
        config_entry,
        {"auto_off_at": now + 300, "auto_off_set_at": now - 300},
    )
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_MAX_RUN_MINUTES: 0}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert faucet(hass, config_entry)._auto_off_at is None


async def test_water_turned_on_with_the_limit_off_is_not_cut_off_by_an_old_one(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    coordinator = faucet(hass, setup_entry)
    await _water_on(hass)  # under the limit
    assert coordinator._auto_off_at is not None
    coordinator.max_run_minutes = 0  # turned off
    await _water_on(hass)  # this run is not to be limited
    assert coordinator._auto_off_at is None
    kohler.state["status"] = "On"
    await _steps(hass, freezer, timedelta(minutes=1), 15)
    assert _actions(kohler) == ["ON", "ON"]


# --------------------------------------------------------------------------- #
# Follow-up review (2026-10-16)
# --------------------------------------------------------------------------- #
async def test_a_limit_turned_off_does_not_come_back_with_the_next_one(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Dropped only in memory, the old deadline stayed in the store.

    Turned on again an hour later, the reload restored it long past, and 30 s on it cut
    off whoever was using the faucet by then.
    """
    now = time.time()
    _store(
        hass_storage,
        config_entry,
        {
            "auto_off_at": now + 300,
            "auto_off_set_at": now - 300,
            "auto_off_seen_running": True,
            "push_verified": True,
        },
    )
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_MAX_RUN_MINUTES: 0}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    kept = _stored(hass_storage, config_entry)
    assert kept["auto_off_at"] is None
    assert kept["push_verified"] is True, "the save lost what the store kept"

    await _advance(hass, freezer, timedelta(hours=1))
    kohler.state["status"] = "On"  # someone is using the faucet by hand
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_MAX_RUN_MINUTES: 10}
    )
    await hass.async_block_till_done()
    assert faucet(hass, config_entry)._auto_off_at is None
    await _steps(hass, freezer, timedelta(seconds=30), 6)
    assert _actions(kohler) == []


async def test_a_reread_that_fails_for_days_does_not_overflow_its_back_off(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: SlowKohler
) -> None:
    """At 1025 failures in a row the delay overflowed a float, failing every poll."""
    coordinator = faucet(hass, setup_entry)
    coordinator._reread_failures = 5000
    coordinator._config_due = True
    kohler.fail_api(f"/faucet-configuration/{DEVICE_ID}", (503, {}))
    await coordinator._async_refresh_config()
    assert coordinator._reread_after - time.monotonic() <= 300


async def test_a_drop_while_the_cloud_lags_past_half_a_minute_keeps_the_limit(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Seen running by the stream, which drops; the cloud says "Off" for 35 s.

    A poll's "off" counted from 30 s after the ON, and the limit was dropped over water
    running. Now no poll's does until one has shown the water running, or a minute has
    passed.
    """
    coordinator = await _stream_saw_it_running(hass, freezer, push_entry)
    FakeMqttClient.refuse = True
    FakeMqttClient.instances[-1].drop()
    await _steps(hass, freezer, timedelta(seconds=5), 7)
    assert coordinator._auto_off_at is not None, "dropped on a lagging read"
    kohler.state["status"] = "On"  # the cloud catches up: the water is running
    await _steps(hass, freezer, timedelta(minutes=1), 12)
    assert _actions(kohler)[:2] == ["ON", "OFF"]


async def test_at_the_deadline_a_poll_the_cloud_had_not_caught_up_with_is_not_off(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """A 1-minute limit, the cloud 45 s behind, no poll between 35 s and the deadline.

    The newest reading at the deadline was a stale "off" from more than 30 s after the
    ON, and was taken for the water off: the limit was dropped over water running.
    """
    coordinator = faucet(hass, setup_entry)
    coordinator.max_run_minutes = 1
    await _water_on(hass)
    await _steps(hass, freezer, timedelta(seconds=5), 7)  # the cloud still says "Off"
    assert coordinator._auto_off_at is not None
    kohler.state["status"] = "On"  # caught up, but nothing reads it before the deadline
    coordinator._auto_off_timer()  # the deadline, now: its timer fires in its place
    await coordinator._async_auto_off(dt_util.utcnow())
    assert _actions(kohler)[:2] == ["ON", "OFF"]


async def test_a_configuration_kohler_never_serves_is_not_read_every_poll(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Until it first answered, every poll read it again, with both preset lists."""
    kohler.faucet_presets = [{"experienceId": "p1", "title": "Kettle"}]
    kohler.fail_api(
        f"/faucet-configuration/{DEVICE_ID}", *[(500, {}) for _ in range(500)]
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    kohler.state["status"] = "On"  # running, so polled every 5 s
    await _advance(hass, freezer, timedelta(seconds=5))
    config, presets = (
        _calls(kohler, "/faucet-configuration/"),
        _calls(kohler, "/faucet-experience"),
    )
    await _steps(hass, freezer, timedelta(seconds=5), 12)  # a minute
    assert _calls(kohler, "/faucet-configuration/") - config <= 2
    assert _calls(kohler, "/faucet-experience") - presets == 0
    assert not faucet(hass, config_entry).config_loaded


async def test_a_first_configuration_read_that_failed_is_tried_again_soon(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Not left to the five-minute interval: the leak sensor waits on it."""
    kohler.fail_api(f"/faucet-configuration/{DEVICE_ID}", (500, {}))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = faucet(hass, config_entry)
    assert not coordinator.config_loaded
    await _steps(hass, freezer, timedelta(seconds=30), 3)
    assert coordinator.config_loaded


async def test_a_refusal_after_a_restart_still_finds_the_account_s_presets(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """Remembering that the faucet's list once answered is for its 404 alone.

    Any other refusal after a restart left the presets empty for good, with nothing read
    since setup to keep. The account's list stands in — with only the presets the
    faucet's own list last held: its legacy field still carries ones deleted in the app,
    which "Dispense preset" would have poured.
    """
    _store(hass_storage, config_entry, {"faucet_preset_ids": ["p2"]})
    kohler.fail_api("/faucet-experience", (400, {"message": "Bad Request"}))
    kohler.presets = [
        {"experienceId": "p2", "title": "Kettle", "dispenseAmount": 1.2},
        {"experienceId": "old", "title": "Stock pot", "dispenseAmount": 8.0},
    ]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = faucet(hass, config_entry)
    assert [p.title for p in coordinator.presets.values()] == ["Kettle"]
    assert coordinator.preset_source == "customer-experience"


# --------------------------------------------------------------------------- #
# Second follow-up (2026-10-16): no poll vouches for the cloud having caught up
# --------------------------------------------------------------------------- #
async def test_off_and_on_again_within_the_cloud_s_lag_keeps_the_new_limit(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    """The polls after the second ON still show the first run, then its OFF.

    The stale "running" was taken for the cloud having caught up with the second ON, so
    the stale "off" after it dropped the second run's limit, with the water running.
    """
    coordinator = faucet(hass, setup_entry)
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _steps(hass, freezer, timedelta(seconds=5), 3)
    await _water_off(hass)  # the cloud still shows the first run
    await _advance(hass, freezer, timedelta(seconds=5))
    await _water_on(hass)
    armed = coordinator._auto_off_at
    await _advance(hass, freezer, timedelta(seconds=5))  # "On": the first run, stale
    kohler.state["status"] = "Off"  # the first OFF reaches the cloud
    await _advance(hass, freezer, timedelta(seconds=5))
    assert coordinator._auto_off_at == armed, "dropped on the first run's OFF"
    kohler.state["status"] = "On"  # the second run reaches it; nobody turns it off
    await _steps(hass, freezer, timedelta(minutes=1), 11)
    assert _actions(kohler)[:4] == ["ON", "OFF", "ON", "OFF"]


async def test_a_hand_run_just_ended_does_not_vouch_for_the_on(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    coordinator = faucet(hass, setup_entry)
    kohler.state["status"] = "On"  # a hand run
    await _steps(hass, freezer, timedelta(seconds=30), 2)
    await _water_on(hass)  # the read after it: the hand run, stale
    armed = coordinator._auto_off_at
    await _advance(hass, freezer, timedelta(seconds=5))
    kohler.state["status"] = "Off"  # the hand run's end reaches the cloud
    await _advance(hass, freezer, timedelta(seconds=5))
    assert coordinator._auto_off_at == armed, "dropped on the hand run's end"
    kohler.state["status"] = "On"  # Home Assistant's run reaches it
    await _steps(hass, freezer, timedelta(minutes=1), 11)
    assert _actions(kohler)[:2] == ["ON", "OFF"]


@pytest.mark.parametrize("minutes", [1, 10])
async def test_an_off_from_here_soon_after_the_on_frees_the_faucet(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
    minutes: int,
) -> None:
    """ON, then OFF from Home Assistant 10 s on; the cloud never showed it running.

    Held for the cloud's minute, the limit was still standing when someone used the
    faucet by hand, and cut them off.
    """
    coordinator = faucet(hass, setup_entry)
    coordinator.max_run_minutes = minutes
    await _water_on(hass)  # the cloud has not caught up: "Off"
    await _steps(hass, freezer, timedelta(seconds=5), 2)
    await _water_off(hass)
    await _advance(hass, freezer, timedelta(seconds=5))  # "Off", after the OFF
    kohler.state["status"] = "On"  # someone uses the faucet by hand
    await _steps(hass, freezer, timedelta(seconds=5), 6)
    await _steps(hass, freezer, timedelta(minutes=1), minutes + 1)
    assert _actions(kohler) == ["ON", "OFF"], "the hand run was cut off"


# --------------------------------------------------------------------------- #
# Third follow-up (2026-10-16): an "off" after an OFF from here counts at once
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("minutes", [1, 2])
async def test_a_hand_run_after_the_switch_turned_running_water_off_is_not_cut_off(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
    minutes: int,
) -> None:
    """Seen running, turned off from here 10 s in, seen off: Home Assistant's run is over.

    Held for the cloud's minute all the same, the limit cut off a hand run that began
    25 s in — always, with a 1-minute limit, whose deadline came first.
    """
    coordinator = faucet(hass, setup_entry)
    coordinator.max_run_minutes = minutes
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _steps(hass, freezer, timedelta(seconds=5), 2)
    await _water_off(hass)
    kohler.state["status"] = "Off"
    await _steps(hass, freezer, timedelta(seconds=5), 3)
    kohler.state["status"] = "On"  # a hand run
    await _steps(hass, freezer, timedelta(seconds=5), 12 * minutes + 6)
    assert _actions(kohler) == ["ON", "OFF"], "the hand run was cut off"


async def test_a_dispense_after_the_switch_turned_running_water_off_is_not_cut_off(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: SlowKohler,
) -> None:
    coordinator = faucet(hass, setup_entry)
    coordinator.max_run_minutes = 1
    await _water_on(hass)
    kohler.state["status"] = "On"
    await _steps(hass, freezer, timedelta(seconds=5), 2)
    await _water_off(hass)
    kohler.state["status"] = "Off"
    await _steps(hass, freezer, timedelta(seconds=5), 3)
    await coordinator.async_dispense(4.0)
    kohler.state["status"] = "On"
    await _steps(hass, freezer, timedelta(seconds=5), 8)
    assert _actions(kohler) == ["ON", "OFF"], "the dispense was cut off"
