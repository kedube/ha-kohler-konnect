"""Regression tests for the final review of the 2026-10-16 valve-path fixes.

Each was a reproduction of a way the merged fixes could still open water or loosen a scald
limit; each now asserts the outcome that must hold.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.select import SERVICE_SELECT_OPTION
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect.const import (
    DOMAIN,
    OUTLET_WRITE_VERIFY_DELAY_SECONDS,
    device_issue_key,
)
from custom_components.kohler_konnect.konnect import Envelope
from custom_components.kohler_konnect.konnect.client import KohlerError

from .conftest import VALVE_ID, FakeMqttClient, account, wait_for
from .test_review_writes import (  # noqa: F401 - `quick_usage_reads`, `us_units`: autouse
    COORDINATOR,
    HeldKohler,
    entity_id,
    eventually,
    quick_usage_reads,
    us_units,
    valve_of,
)
from .test_shower_entities import (
    RAINHEAD,
    SHOWERHEAD,
    SOLO,
    TEMPERATURE,
    WARMUP,
    WRITE_OUTLET,
    call,
    custom_shower,
    report,
    set_number,
    setup,
    solo,
    word,
)


class GetHeldKohler(HeldKohler):
    """`HeldKohler` that can also hold the plain `gcs-state` GET in flight."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.get_gate: asyncio.Event | None = None
        self.get_held = 0

    def hold_state_read(self) -> asyncio.Event:
        self.get_gate = asyncio.Event()
        self.get_held = 0
        return self.get_gate

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        if (
            self.get_gate is not None
            and method.lower() == "get"
            and "/gcs-state/" in url.path
            and "gcsadvancestate" not in url.path
        ):
            self.get_held += 1
            gate = self.get_gate
            await gate.wait()
        return await super()._api(method, url, data)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> GetHeldKohler:
    return GetHeldKohler(aioclient_mock)


def deliver(attribute: dict[str, Any]) -> None:
    FakeMqttClient.instances[-1].deliver(
        {
            "sku": "GCS",
            "deviceid": VALVE_ID,
            "data": {"code": "GCS_SOLO_STS", "attributes": [attribute]},
        }
    )


def write_issue(hass: HomeAssistant, setting: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"outlet_write_unverified_{setting}_{device_issue_key(VALVE_ID)}"
    )


def stream_gap(valve: Any) -> None:
    """Age the last report as a stream gap would: the cloud has caught up since."""
    valve._reported_at = {field: at - 60 for field, at in valve._reported_at.items()}


# --------------------------------------------------------------------------- #
# A warm-up read-back from a lagging cloud document no longer ends the warm-up guard
# --------------------------------------------------------------------------- #
async def test_a_lagging_warmup_read_back_does_not_let_a_write_take_the_warmup(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """The document still said "not in progress"; the next change ran every outlet."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)  # 0x01 from here
    await solo(hass, word(0b111), warmUpStatus="warmUpInProgress")  # the warm-up set
    assert valve._warming_up()

    # A mode chosen seconds before the shower: the new mode, not yet the warm-up.
    kohler.gcs["warmUpState"] = {
        "warmUp": "warmUpAllOutletsWithNoStartDelay",
        "state": "warmUpNotInProgress",
    }
    await valve.async_read_warmup_mode()
    assert valve._warming_up()

    valve._sent_at -= (
        valve._sent_hold_seconds + 1
    )  # the echo's hold over, still warming
    await set_number(hass, TEMPERATURE, 104)
    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0b001, "the warm-up was taken onto every warm-up outlet"


async def test_the_same_through_the_warmup_select(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """End to end: a mode chosen, a shower started from here, the later read-back."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    kohler.apply_warmup = False  # the document lags the mode write at first
    with patch(
        "custom_components.kohler_konnect.warmup_manager.WARMUP_READBACK_DELAYS",
        (0.0, 0.3),
    ):
        options = hass.states.get(WARMUP).attributes["options"]
        selecting = hass.async_create_task(
            hass.services.async_call(
                SELECT_DOMAIN,
                SERVICE_SELECT_OPTION,
                {ATTR_ENTITY_ID: WARMUP, "option": options[1]},
                blocking=True,
            )
        )
        await asyncio.sleep(0.05)  # the first read-back done, the second pending
        await valve.async_set_zone_outlet(1, 1, True)  # the shower, from here
        deliver(
            {
                "code": "GCS_SOLO_STS",
                "primaryValve1": word(0b111),
                "secondaryValve1": "0000000000000000",
                "warmUpStatus": "warmUpInProgress",
            }
        )
        await asyncio.sleep(0.01)
        assert valve._warming_up()
        kohler.gcs["warmUpState"]["warmUp"] = "warmUpAllOutletsWithNoStartDelay"
        await selecting
    assert valve._warming_up()
    valve._sent_at -= valve._sent_hold_seconds + 1
    await set_number(hass, TEMPERATURE, 104)
    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0b001, "the warm-up was taken onto every warm-up outlet"


# --------------------------------------------------------------------------- #
# A reconnect's state read keeps only what is newer than it
# --------------------------------------------------------------------------- #
async def test_an_outlet_setting_during_a_state_read_does_not_void_its_words(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """An outlet setting changes no word, so the read's words stand: the panel's Stop."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await solo(hass, word(0b001))  # running, as last reported before a stream gap
    stream_gap(valve)
    kohler.gcs["valve1"] = kohler.idle_valve()  # stopped at the panel during the gap

    gate = kohler.hold_state_read()
    with patch(f"{COORDINATOR}.OUTLET_WRITE_VERIFY_DELAY_SECONDS", 0):
        reading = asyncio.create_task(valve.async_read_live_state())
        await eventually(lambda: kohler.get_held == 1, "the state read")
        await valve.async_write_outlet_setting(maximum_run_time=2700)
        gate.set()
        kohler.get_gate = None
        await reading
        await hass.async_block_till_done()
    await set_number(hass, TEMPERATURE, 104)
    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0, "water re-opened after a Stop at the panel"


@pytest.mark.parametrize(
    "attribute",
    [
        {"code": "GCS_SOLO_STS", "currentSystemState": "normalOperation"},
        {"code": "GCS_SOLO_STS", "primaryValve1": "zz", "secondaryValve1": "00"},
    ],
    ids=["no-words", "garbled"],
)
async def test_a_report_with_no_usable_word_does_not_void_a_state_reads_words(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: GetHeldKohler,
    attribute: dict[str, Any],
) -> None:
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await solo(hass, word(0b001))
    stream_gap(valve)
    kohler.gcs["valve1"] = kohler.idle_valve()

    gate = kohler.hold_state_read()
    reading = asyncio.create_task(valve.async_read_live_state())
    await eventually(lambda: kohler.get_held == 1, "the state read")
    deliver(attribute)
    await asyncio.sleep(0.01)
    gate.set()
    kohler.get_gate = None
    await reading
    await hass.async_block_till_done()
    await set_number(hass, TEMPERATURE, 104)
    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0, "water re-opened after a Stop at the panel"


async def test_a_state_read_sent_just_after_a_report_keeps_its_words(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """The document trails the valve: a read a moment after the Stop still says running."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await solo(hass, word(0b001))
    stream_gap(valve)
    await solo(hass, word(0))  # a Stop at the panel, reported just now
    await valve.async_read_live_state()  # the document has not caught up
    await set_number(hass, TEMPERATURE, 104)
    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0, "water re-opened after a Stop at the panel"


# --------------------------------------------------------------------------- #
# An outlet setting the valve never confirmed is not re-sent over an app change
# --------------------------------------------------------------------------- #
async def test_an_unconfirmed_scald_limit_is_not_sent_over_a_change_in_the_app(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """47.7 °C written here, never shown; 40.0 °C then set in the app; a duration change.

    Sending the duration would re-send 47.7 °C to every outlet. It is refused instead,
    until the owner sets Max Temperature again.
    """
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "43.3"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    with patch(f"{COORDINATOR}.OUTLET_WRITE_VERIFY_DELAY_SECONDS", 0):
        kohler.apply_outlet_writes = False  # never applied, never shown
        await valve.async_write_outlet_setting(maximum_temperature_tenths=477)
        await hass.async_block_till_done()

        # The owner lowers the scald limit in the app while the stream is down.
        for outlet in range(3):
            kohler.outlet_fields[outlet]["maximumOutletTemperature"] = "40"
        valve._outlet_written_at -= 3600  # long past the catch-up window
        await account(hass, config_entry)._async_seed_state()  # the reconnect's reseed
        assert valve._outlet_disputed == {"maximum_temperature_tenths": 400}

        kohler.apply_outlet_writes = True
        kohler.posts.clear()
        with pytest.raises(HomeAssistantError, match="Set Max Temperature again first"):
            await valve.async_write_outlet_setting(maximum_run_time=2700)
        await hass.async_block_till_done()
    assert kohler.sent(WRITE_OUTLET) == []
    assert write_issue(hass, "maximum_temperature_tenths") is not None


async def test_the_same_when_the_read_back_failed(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "43.3"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    with patch(f"{COORDINATOR}.OUTLET_WRITE_VERIFY_DELAY_SECONDS", 0):
        kohler.fail_api(
            "/gcs-state/gcsadvancestate/" + VALVE_ID, kohler.network_error()
        )
        await valve.async_write_outlet_setting(maximum_temperature_tenths=477)
        await hass.async_block_till_done()
        for outlet in range(3):
            kohler.outlet_fields[outlet]["maximumOutletTemperature"] = "40"
        valve._outlet_written_at -= 3600
        await account(hass, config_entry)._async_seed_state()
        kohler.posts.clear()
        with pytest.raises(HomeAssistantError, match="Set Max Temperature again first"):
            await valve.async_write_outlet_setting(maximum_run_time=2700)
        await hass.async_block_till_done()
    assert kohler.sent(WRITE_OUTLET) == []
    assert write_issue(hass, "maximum_temperature_tenths") is not None


async def test_setting_it_again_settles_the_dispute(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "43.3"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    with patch(f"{COORDINATOR}.OUTLET_WRITE_VERIFY_DELAY_SECONDS", 0):
        kohler.apply_outlet_writes = False
        await valve.async_write_outlet_setting(maximum_temperature_tenths=477)
        await hass.async_block_till_done()
        valve._outlet_written_at -= 3600
        await account(hass, config_entry)._async_seed_state()
        assert "maximum_temperature_tenths" in valve._outlet_disputed

        kohler.apply_outlet_writes = True
        await valve.async_write_outlet_setting(maximum_temperature_tenths=400)
        await hass.async_block_till_done()
        assert valve._outlet_disputed == {}
        kohler.posts.clear()
        await valve.async_write_outlet_setting(maximum_run_time=2700)
        await hass.async_block_till_done()
    records = [b["gcsOutletConfigControlModel"] for b in kohler.sent(WRITE_OUTLET)]
    assert [r["maximumOutletTemperature"] for r in records] == ["400"] * 3


# --------------------------------------------------------------------------- #
# A Stop at the panel beside a garbled word is not lost
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("secondary", ["", "11zz"], ids=["absent", "garbled"])
async def test_a_panel_stop_beside_a_bad_zone_two_word_is_not_reopened(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: GetHeldKohler,
    secondary: str,
) -> None:
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)
    await solo(hass, word(0b001), word(0, zone=2))  # zone 1 running, from the panel
    attribute = {"code": "GCS_SOLO_STS", "primaryValve1": word(0)}  # its Stop
    if secondary:
        attribute["secondaryValve1"] = secondary
    deliver(attribute)
    await hass.async_block_till_done()
    await set_number(hass, entity_id(hass, config_entry, "temperature_zone_1"), 104)
    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0, "zone 1 re-opened after a Stop at the panel"


# --------------------------------------------------------------------------- #
# During a warm-up, a zone stopped or paused at the panel stays that way
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("zone2_report", ["stopped", "paused"])
async def test_a_zone_stopped_at_the_panel_during_a_warmup_stays_stopped(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: GetHeldKohler,
    zone2_report: str,
) -> None:
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await valve.async_set_zone_outlet(1, 1, True)
    await valve.async_set_zone_outlet(2, 1, True)
    await solo(hass, word(0b111), word(0b011, zone=2), warmUpStatus="warmUpInProgress")
    valve._sent_at -= valve._sent_hold_seconds + 1
    zone2 = (
        word(0, zone=2) if zone2_report == "stopped" else word(0, zone=2, paused=True)
    )
    await solo(hass, word(0b111), zone2, warmUpStatus="warmUpInProgress")
    assert valve._warming_up()
    await set_number(hass, entity_id(hass, config_entry, "temperature_zone_1"), 104)
    sent1, sent2 = kohler.words()[-1]
    assert sent1.outlet_mask == 0b001
    assert sent2.outlet_mask == 0 or sent2.paused, "zone 2 re-opened"


# --------------------------------------------------------------------------- #
# A command that fails during a warm-up is not taken up by the next
# --------------------------------------------------------------------------- #
async def test_a_failed_open_during_a_warmup_is_not_taken_up(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await solo(hass, word(0b111), warmUpStatus="warmUpInProgress")
    valve._sent_at -= valve._sent_hold_seconds + 1
    kohler.fail_api(SOLO, kohler.network_error())
    with pytest.raises(HomeAssistantError):
        await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWERHEAD)
    await set_number(hass, TEMPERATURE, 104)
    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0b001, "the failed command's outlet was opened"


async def test_a_failed_custom_shower_does_not_lift_the_warmup_refusal(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """A change during a warm-up started at the panel is refused, and stays refused."""
    await setup(hass, config_entry)
    await solo(hass, word(0b111), warmUpStatus="warmUpInProgress")  # the panel's
    with pytest.raises(HomeAssistantError, match="warming up"):
        await set_number(hass, TEMPERATURE, 104)
    kohler.fail_api(SOLO, kohler.network_error())
    with pytest.raises(HomeAssistantError):
        await custom_shower(hass, zone1_temperature=104, zone1_outlet_2=True)
    with pytest.raises(HomeAssistantError, match="warming up"):
        await set_number(hass, TEMPERATURE, 104)


# --------------------------------------------------------------------------- #
# Follow-up review (2026-10-16): nothing outlives an unload
# --------------------------------------------------------------------------- #
async def test_an_unload_while_a_custom_shower_is_on_its_way_starts_no_watcher(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Started untracked once the write landed, it outlived the unload by minutes."""
    from .test_review2_writes import in_flight, release
    from .test_review_writes import watchers

    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    gate = kohler.hold(SOLO)
    shower = asyncio.create_task(
        valve.async_custom_shower("0184C801", "00000000", keep_on_after_warmup=True)
    )
    await in_flight(kohler)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    release(kohler, gate)
    await shower
    assert watchers() == []


# --------------------------------------------------------------------------- #
# Follow-up review (2026-10-16): a read behind the valve's own announcement
# --------------------------------------------------------------------------- #
def _announce(outlet_id: int, **fields: str) -> dict[str, Any]:
    """`READ_GCS_OUTLET_CONFIG_CFG` for one outlet, in wire units (tenths of °C)."""
    return {
        "code": "READ_GCS_OUTLET_CONFIG_CFG",
        "outLetId": str(outlet_id),
        "minimumFlowRate": "16",
        "maximumFlowRate": "200",
        **fields,
    }


async def _announced(hass: HomeAssistant, **fields: str) -> None:
    """The valve announces every outlet's record, as after a change made in the app."""
    for outlet in range(3):
        await report(hass, "READ_GCS_OUTLET_CONFIG_CFG", _announce(outlet, **fields))


def _sent(kohler: HeldKohler, key: str) -> list[str]:
    return [b["gcsOutletConfigControlModel"][key] for b in kohler.sent(WRITE_OUTLET)]


async def _unconfirmed(hass: HomeAssistant, valve: Any, kohler: HeldKohler, **setting):
    """A setting accepted and never shown, its read-back gone, the window long past."""
    kohler.apply_outlet_writes = False
    await valve.async_write_outlet_setting(**setting)
    for task in list(valve._verify_tasks.values()):
        task.cancel()
    await hass.async_block_till_done()
    valve._outlet_written_at -= 2 * OUTLET_WRITE_VERIFY_DELAY_SECONDS
    kohler.apply_outlet_writes = True


async def test_a_check_behind_the_valve_s_announcement_does_not_settle_a_dispute(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """45.0 °C set here, unconfirmed; 40.0 °C then set in the app, and announced.

    The check before a duration write read the document, still at 45.0 °C, took it for
    confirmation, and sent 45.0 °C to every outlet over the app's 40.0 °C.
    """
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "47.7"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await _unconfirmed(hass, valve, kohler, maximum_temperature_tenths=450)
    for outlet in range(3):
        kohler.outlet_fields[outlet]["maximumOutletTemperature"] = "45.0"
    await _announced(hass, maximumOutletTemperature="400")
    assert valve._outlet_disputed == {"maximum_temperature_tenths": 400}
    kohler.posts.clear()
    with pytest.raises(HomeAssistantError, match="Set Max Temperature again first"):
        await valve.async_write_outlet_setting(maximum_run_time=2700)
    assert kohler.sent(WRITE_OUTLET) == []
    assert valve._outlet_disputed == {"maximum_temperature_tenths": 400}


async def test_a_check_behind_the_announcement_does_not_put_the_old_value_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """The check was for Default Temperature; Max Temperature came back from it as 47.7."""
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "47.7"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await _unconfirmed(hass, valve, kohler, default_temperature_tenths=350)
    for outlet in range(3):
        kohler.outlet_fields[outlet]["defaultOutletTemperature"] = "35.0"
    await _announced(
        hass, maximumOutletTemperature="400", defaultOutletTemperature="350"
    )
    kohler.posts.clear()
    await valve.async_write_outlet_setting(maximum_run_time=2700)
    assert _sent(kohler, "maximumOutletTemperature") == ["400"] * 3


async def test_a_refused_write_leaves_the_announced_values_for_the_next(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """Refused over a dispute, the write's check had already cached the old 47.7 °C; the
    re-set the refusal asks for then sent it."""
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "47.7"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await _unconfirmed(hass, valve, kohler, default_temperature_tenths=350)
    await _announced(
        hass, maximumOutletTemperature="400", defaultOutletTemperature="360"
    )
    with pytest.raises(HomeAssistantError, match="Set Default Temperature again"):
        await valve.async_write_outlet_setting(maximum_run_time=2700)
    kohler.posts.clear()
    await valve.async_write_outlet_setting(default_temperature_tenths=360)
    assert _sent(kohler, "maximumOutletTemperature") == ["400"] * 3


async def test_a_reseed_behind_the_announcement_does_not_settle_a_dispute(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "47.7"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await _unconfirmed(hass, valve, kohler, maximum_temperature_tenths=450)
    for outlet in range(3):
        kohler.outlet_fields[outlet]["maximumOutletTemperature"] = "45.0"
    await _announced(hass, maximumOutletTemperature="400")
    await account(hass, config_entry)._async_seed_state()  # a reconnect's, seconds on
    assert valve._outlet_disputed == {"maximum_temperature_tenths": 400}
    limits = valve.gcs_state.outlet_limits.values()
    assert {r.maximum_temperature_tenths for r in limits} == {400}
    with pytest.raises(HomeAssistantError, match="Set Max Temperature again first"):
        await valve.async_write_outlet_setting(maximum_run_time=2700)


@pytest.mark.parametrize(
    ("announced", "sent"),
    [("400", "400"), ("477", "450")],
    ids=["set-in-the-app", "late-echo"],
)
async def test_inside_the_window_only_a_late_echo_gives_way_to_the_value_written(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: GetHeldKohler,
    announced: str,
    sent: str,
) -> None:
    """45.0 °C set here over 47.7 °C; seconds later the valve announces something else.

    Its old 47.7 °C is a late echo, and the value written stands against it. Anything
    else was set elsewhere — the app's 40.0 °C — and used to be put back to 45.0 °C all
    the same, which the next write then sent.
    """
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "47.7"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await valve.async_write_outlet_setting(maximum_temperature_tenths=450)
    for task in list(valve._verify_tasks.values()):
        task.cancel()  # its read-back comes once the window is over; this is inside it
    await _announced(hass, maximumOutletTemperature=announced)
    kohler.posts.clear()
    await valve.async_write_outlet_setting(maximum_run_time=2700)
    assert _sent(kohler, "maximumOutletTemperature") == [sent] * 3


async def test_two_disputed_settings_do_not_each_wait_on_the_other(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """Both accepted and never applied, the valve off the cloud: each then refused the
    other's re-set, and every outlet setting, until a restart."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    reported = [
        str(r.default_temperature_tenths)
        for _, r in sorted(valve.gcs_state.outlet_limits.items())
    ]
    kohler.apply_outlet_writes = False
    await valve.async_write_outlet_setting(maximum_temperature_tenths=450)
    await valve.async_write_outlet_setting(default_temperature_tenths=370)
    for task in list(valve._verify_tasks.values()):
        task.cancel()
    await hass.async_block_till_done()
    valve._outlet_written_at -= 2 * OUTLET_WRITE_VERIFY_DELAY_SECONDS
    await account(hass, config_entry)._async_seed_state()
    assert set(valve._outlet_disputed) == {
        "maximum_temperature_tenths",
        "default_temperature_tenths",
    }
    kohler.apply_outlet_writes = True
    kohler.posts.clear()
    await valve.async_write_outlet_setting(maximum_temperature_tenths=450)
    assert _sent(kohler, "maximumOutletTemperature") == ["450"] * 3
    # The other goes as the valve reports it, which changes nothing there.
    assert _sent(kohler, "defaultOutletTemperature") == reported
    assert "default_temperature_tenths" not in valve._outlet_disputed
    assert write_issue(hass, "default_temperature_tenths") is None


async def test_a_check_with_no_outlet_records_is_no_check(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """Passed over, the setting it was for went out again unchecked."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await _unconfirmed(hass, valve, kohler, maximum_temperature_tenths=450)
    kohler.fail_api("/gcs-state/gcsadvancestate/" + VALVE_ID, (200, {"setting": {}}))
    kohler.posts.clear()
    with pytest.raises(HomeAssistantError, match="no outlet configuration"):
        await valve.async_write_outlet_setting(maximum_run_time=2700)
    assert kohler.sent(WRITE_OUTLET) == []


async def test_a_check_that_fails_once_the_setting_is_confirmed_lets_the_write_go(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """The write's own read-back confirmed it meanwhile: asking which setting was still
    unconfirmed raised `StopIteration` out of the coroutine, a `RuntimeError`."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await _unconfirmed(hass, valve, kohler, maximum_temperature_tenths=450)

    async def confirmed_then_failed(device_id: str) -> Any:
        valve._outlet_unconfirmed.clear()
        raise KohlerError("connection reset")

    kohler.posts.clear()
    with patch.object(valve.client, "async_get_gcs_settings", confirmed_then_failed):
        await valve.async_write_outlet_setting(maximum_run_time=2700)
    assert len(kohler.sent(WRITE_OUTLET)) == 3


async def test_a_warning_about_an_outlet_setting_does_not_outlive_a_restart(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """It said no other setting could be changed — which a restart had made untrue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"outlet_write_unverified_maximum_temperature_tenths_{device_issue_key(VALVE_ID)}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="outlet_write_unverified",
        translation_placeholders={"setting": "Max Temperature", "detail": "…"},
    )
    await setup(hass, config_entry)
    assert write_issue(hass, "maximum_temperature_tenths") is None


# --------------------------------------------------------------------------- #
# Follow-up review (2026-10-16): a late first connect, and a report handled late
# --------------------------------------------------------------------------- #
async def test_a_first_connect_long_after_setup_reads_the_outlet_settings_again(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """The stream's first start failed; it connects a minute on, on the back-off.

    Only `gcs-state` was read again, so a scald limit lowered in the app meanwhile stayed
    at 47.7 °C in the cache, and the next setting written sent that to every outlet.
    """
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "47.7"} for i in range(3)}
    await setup(hass, config_entry)
    coordinator = account(hass, config_entry)
    valve = valve_of(hass, config_entry)
    coordinator.stream_connects = 0  # as if this were the stream's first connect
    coordinator._seed_started_at -= 60
    for outlet in range(3):
        kohler.outlet_fields[outlet]["maximumOutletTemperature"] = "40.0"
    coordinator._handle_connected()
    await hass.async_block_till_done()
    limits = valve.gcs_state.outlet_limits.values()
    assert {r.maximum_temperature_tenths for r in limits} == {400}
    # A change made just before it may not show yet: outlet writes wait as after a gap.
    with pytest.raises(HomeAssistantError, match="try again in"):
        await valve.async_write_outlet_setting(maximum_run_time=2700)


async def test_a_report_handled_late_is_kept_by_when_it_arrived(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """Held until the devices were known, a report is handled seconds after it came.

    Stamped when handled, it kept a read's words over a Stop at the panel the document
    had long caught up with, and the next temperature change opened the water.
    """
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    account(hass, config_entry)._handle_envelope(
        Envelope(
            sku="GCS",
            device_id=VALVE_ID,
            code="GCS_SOLO_STS",
            attributes=[{"code": "GCS_SOLO_STS", "primaryValve1": word(0b001)}],
            received_at=time.time() - 30,
            received_monotonic=time.monotonic() - 30,
        )
    )
    kohler.gcs["valve1"] = kohler.idle_valve()  # stopped at the panel since
    await valve.async_read_live_state()
    await set_number(hass, TEMPERATURE, 104)
    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0, "water re-opened after a Stop at the panel"


async def test_a_stop_that_fails_during_a_warmup_still_holds_as_the_warmup_ends(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """A "failed" Stop can have landed, and the warm-up end as it does: its last report,
    the warm-up's outlets no longer warming up, became what the next command was built
    on — every outlet opened."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await solo(hass, word(0b111), warmUpStatus="warmUpInProgress")
    valve._sent_at -= valve._sent_hold_seconds + 1
    kohler.fail_api(SOLO, kohler.network_error())
    with pytest.raises(HomeAssistantError):
        await valve.async_stop_shower()
    await solo(hass, word(0b111), warmUpStatus="warmUpNotInProgress")
    await set_number(hass, TEMPERATURE, 104)
    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0, "the Stop was undone"


# --------------------------------------------------------------------------- #
# Second follow-up (2026-10-16): a guess at a late echo is not the valve's word
# --------------------------------------------------------------------------- #
async def _write_holding_back_its_check(valve: Any, hass: HomeAssistant, **setting):
    await valve.async_write_outlet_setting(**setting)
    for task in list(valve._verify_tasks.values()):
        task.cancel()
    await hass.async_block_till_done()


def _age(valve: Any, *, write: float, announcement: float) -> None:
    """The clocks moved on: the write ``write`` s ago, the announcements too."""
    if valve._outlet_written_at is not None:
        valve._outlet_written_at -= write
    valve._outlet_announced_at = {
        o: t - announcement for o, t in valve._outlet_announced_at.items()
    }


async def _read_back(valve: Any, **wanted: int) -> None:
    """The write's own read-back, now rather than after its 30 s sleep."""
    real_sleep = asyncio.sleep

    async def no_wait(*_: Any, **__: Any) -> None:
        await real_sleep(0)

    with patch("asyncio.sleep", no_wait):
        await valve._async_verify_outlet_write(
            maximum_run_time=wanted.get("maximum_run_time"),
            maximum_temperature_tenths=wanted.get("maximum_temperature_tenths"),
            default_temperature_tenths=wanted.get("default_temperature_tenths"),
            serial=valve._outlet_write_serial,
        )


async def test_a_limit_set_back_in_the_app_is_not_loosened_as_a_late_echo(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """47.7 °C set here over 45.0 °C; seconds on, 45.0 °C set back in the app.

    Its announcement showed the value replaced, so it was taken for a late echo — and
    kept as what the valve reported, it stood in for the cloud at the read-back, which
    "confirmed" 47.7 °C; the next write sent it to every outlet.
    """
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "45.0"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await _write_holding_back_its_check(valve, hass, maximum_temperature_tenths=477)
    for outlet in range(3):
        kohler.outlet_fields[outlet]["maximumOutletTemperature"] = "45.0"
    await _announced(hass, maximumOutletTemperature="450")
    _age(valve, write=OUTLET_WRITE_VERIFY_DELAY_SECONDS, announcement=20)
    await _read_back(valve, maximum_temperature_tenths=477)
    kohler.posts.clear()
    with pytest.raises(HomeAssistantError, match="Set Max Temperature again first"):
        await valve.async_write_outlet_setting(maximum_run_time=2700)
    assert kohler.sent(WRITE_OUTLET) == []


async def test_a_lowered_limit_the_valve_never_applied_is_not_verified(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """45.0 °C set here, accepted and never applied; the valve announces its 47.7 °C."""
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "47.7"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    kohler.apply_outlet_writes = False
    await _write_holding_back_its_check(valve, hass, maximum_temperature_tenths=450)
    await _announced(hass, maximumOutletTemperature="477")
    _age(valve, write=OUTLET_WRITE_VERIFY_DELAY_SECONDS, announcement=25)
    await _read_back(valve, maximum_temperature_tenths=450)
    assert write_issue(hass, "maximum_temperature_tenths") is not None
    assert "maximum_temperature_tenths" in valve._outlet_unconfirmed


async def test_a_re_set_limit_is_not_undone_by_the_valve_s_own_late_echo(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """Set again as the refusal asks; outlet 2's announcement from before its turn lands
    after the chain. The values a write replaces came from the cache, which held Home
    Assistant's, so the valve's 47.7 °C read as a change in the app and was sent back."""
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "47.7"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await _unconfirmed(hass, valve, kohler, maximum_temperature_tenths=450)
    with pytest.raises(HomeAssistantError, match="again first"):
        await valve.async_write_outlet_setting(maximum_run_time=2700)
    await _write_holding_back_its_check(valve, hass, maximum_temperature_tenths=450)
    await report(
        hass, "READ_GCS_OUTLET_CONFIG_CFG", _announce(2, maximumOutletTemperature="477")
    )
    kohler.posts.clear()
    await valve.async_write_outlet_setting(maximum_run_time=2700)
    assert _sent(kohler, "maximumOutletTemperature") == ["450"] * 3


async def test_after_a_gap_outlet_writes_wait_for_the_cloud_and_read_first(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """An announcement just before the stream dropped; the scald limit lowered in the app
    while it was down, which nothing replays; a reconnect within half a minute.

    The announcement stood in for the reconnect's read, which showed 40.0 °C, and the
    next write sent 47.7 °C. Neither can vouch for the records then: a write waits out the
    window, and reads first.
    """
    kohler.outlet_fields = {i: {"maximumRuntime": "2400"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    stream = account(hass, config_entry).stream
    await _announced(hass, maximumRunTime="2400", maximumOutletTemperature="477")
    FakeMqttClient.instances[-1].drop()
    for outlet in range(3):
        kohler.outlet_fields[outlet]["maximumOutletTemperature"] = "40.0"
    await wait_for(
        hass,
        lambda: stream.connected and len(FakeMqttClient.instances) == 2,
        "the reconnection",
    )
    await hass.async_block_till_done()
    kohler.posts.clear()
    with pytest.raises(HomeAssistantError, match="try again in"):
        await valve.async_write_outlet_setting(maximum_run_time=2700)
    # Half a minute on.
    valve._outlets_unsure_until -= OUTLET_WRITE_VERIFY_DELAY_SECONDS + 1
    _age(valve, write=0, announcement=OUTLET_WRITE_VERIFY_DELAY_SECONDS + 1)
    await valve.async_write_outlet_setting(maximum_run_time=2700)
    assert _sent(kohler, "maximumOutletTemperature") == ["400"] * 3


async def test_while_the_stream_is_down_an_outlet_write_reads_first(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """A change made in the app is announced only over the stream."""
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "47.7"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    FakeMqttClient.refuse = True
    FakeMqttClient.instances[-1].drop()
    await hass.async_block_till_done()
    for outlet in range(3):
        kohler.outlet_fields[outlet]["maximumOutletTemperature"] = "40.0"
    kohler.posts.clear()
    await valve.async_write_outlet_setting(maximum_run_time=2700)
    assert _sent(kohler, "maximumOutletTemperature") == ["400"] * 3


async def test_a_report_s_arrival_is_measured_on_one_clock(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """Its wall-clock age, taken from the monotonic clock, moved with every clock step:
    stepped back an hour, a report held 30 s looked new, and kept a read's words over a
    Stop at the panel since."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    account(hass, config_entry)._handle_envelope(
        Envelope(
            sku="GCS",
            device_id=VALVE_ID,
            code="GCS_SOLO_STS",
            attributes=[{"code": "GCS_SOLO_STS", "primaryValve1": word(0b001)}],
            received_at=time.time() + 3600,
            received_monotonic=time.monotonic() - 30,
        )
    )
    kohler.gcs["valve1"] = kohler.idle_valve()
    await valve.async_read_live_state()
    await set_number(hass, TEMPERATURE, 104)
    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0, "water re-opened after a Stop at the panel"


# --------------------------------------------------------------------------- #
# Third follow-up (2026-10-16): a guess at a late echo is per field
# --------------------------------------------------------------------------- #
async def test_a_late_echo_guessed_in_one_field_leaves_the_others_the_valve_s(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: GetHeldKohler
) -> None:
    """36.0 °C Default set here over 38.8 °C; seconds later the app saves outlet 1 with
    Max Temperature lowered to 40.0 °C and its own older Default of 38.8 °C.

    38.8 °C is a value the write replaced, so that field is taken for a late echo — and
    the whole announcement stopped counting, Max Temperature with it. A read-back still
    behind the app's save put 47.7 °C back, and the next write sent it to outlet 1.
    """
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "47.7"} for i in range(3)}
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await _write_holding_back_its_check(valve, hass, default_temperature_tenths=360)
    await report(
        hass,
        "READ_GCS_OUTLET_CONFIG_CFG",
        _announce(1, maximumOutletTemperature="400", defaultOutletTemperature="388"),
    )
    _age(valve, write=OUTLET_WRITE_VERIFY_DELAY_SECONDS, announcement=3)
    await _read_back(valve, default_temperature_tenths=360)
    kohler.posts.clear()
    await valve.async_write_outlet_setting(maximum_run_time=2700)
    assert _sent(kohler, "maximumOutletTemperature") == ["477", "400", "477"]


async def test_a_stop_reported_just_before_a_read_stands_on_a_frozen_clock(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: GetHeldKohler,
) -> None:
    """Stamped on the real clock, a message under a frozen one looked decades old, so the
    rule that keeps a report over a read sent just after it never held in such tests."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await solo(hass, word(0b001))
    freezer.tick(timedelta(seconds=20))
    await solo(hass, word(0))  # the Stop at the panel, just now
    kohler.gcs["valve1"] = kohler.idle_valve(out1="1")  # the document lags
    await valve.async_read_live_state()
    await set_number(hass, TEMPERATURE, 104)
    zone1, _ = kohler.words()[-1]
    assert zone1.outlet_mask == 0, "water re-opened after a Stop at the panel"
