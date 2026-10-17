"""Second review of 2026-10-16, end to end inside a real Home Assistant.

The unit tests in `tests/test_review2_water.py` hold each decision; these check the wiring —
Home Assistant's restore-state store and recorder, the real coordinator and cloud watch,
and the (fake) Kohler cloud and MQTT stream — for the findings where the wiring is the
point. Names added by the fixes are imported inside the tests that use them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect.const import (
    CONF_LAST_WARMUP_MODE,
    CONF_VALVES,
    CONF_WARMUP_AUTO_RESTORE,
    FAUCET_USAGE_REFRESH_INTERVAL,
)

from .conftest import VALVE_ID, account
from .test_review_water import CLOUD, ReviewKohler
from .test_warmup import (
    ALL,
    OFF,
    RUNNING_WORD,
    STOPPED_WORD,
    WARMUP_SELECT,
    _announce,
    _quiet,
    _report,
    _set_up,
    _until,
)

FAUCET_TODAY = "sensor.kitchen_water_used_today"
FAUCET_MONTH = "sensor.kitchen_water_used_this_month"
FAUCET_YEAR = "sensor.kitchen_water_used_this_year"
WEEKS = (
    "sensor.anthem_valve_water_used_this_week",
    "sensor.kitchen_water_used_this_week",
)
OUTLET_1 = "switch.anthem_valve_outlet_1"
STATUS = "sensor.anthem_valve_system_status"


class Review2Kohler(ReviewKohler):
    """The review fake, and a few things more about the valve.

    `gcs-state` can lag a write (`lagging_reads`, as the warm-up fake's can), carry a
    `valve1` record the valve has moved on from (`stale_valve1`), and have the valve speak
    while it is being read (`speak_during_read`). `gcs-usage` answers from `valve_usage`.
    """

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.stale_valve1: dict[str, Any] | None = None
        self.speak_during_read = False
        self.valve_usage: dict[str, dict[str, float]] = {}

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        get = method.lower() == "get"
        if get and url.path.endswith(f"/gcs-state/{VALVE_ID}"):
            self.gcs_state_reads += 1
            if self.speak_during_read:
                _report(STOPPED_WORD)  # the valve, while the read is in flight
                await asyncio.sleep(0.01)
            mode = self.warmup_mode
            if self.lagging_reads:
                self.lagging_reads -= 1
                mode = self._previous_mode
            state: dict[str, Any] = {"totalVolume": "1"}
            if mode is not None:
                state["warmUpState"] = {"warmUp": mode, "state": "warmUpNotInProgress"}
            if self.stale_valve1 is not None:
                state["valve1"] = dict(self.stale_valve1)
            body = {"state": state, "connectionState": self.valve_connection}
            return self._respond(method, url, (200, body))
        if get and url.path.endswith(f"/gcs-usage/{VALVE_ID}"):
            buckets = self.valve_usage.get(url.query.get("Interval"))
            if buckets is None:
                return self._respond(method, url, (404, {"message": "not found"}))
            rows = [{"intervalKey": key, "volume": v} for key, v in buckets.items()]
            return self._respond(method, url, (200, {"gcsUsageDataDetailsList": rows}))
        return await super()._api(method, url, data)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> Review2Kohler:
    """Overrides the shared fixture: this account has a valve as well as the faucet."""
    return Review2Kohler(aioclient_mock)


@pytest.fixture(autouse=True)
def short_waits() -> Generator[None]:
    """A minute's restore delay, the read-back retries and the evidence window, in ms."""
    with (
        patch(
            "custom_components.kohler_konnect.warmup_manager."
            "WARMUP_AUTO_RESTORE_DELAY_SECONDS",
            0.02,
        ),
        patch(
            "custom_components.kohler_konnect.warmup_manager."
            "WARMUP_CONTEXT_AFTER_SECONDS",
            0.005,
        ),
        patch(
            "custom_components.kohler_konnect.warmup_manager.WARMUP_READBACK_DELAYS",
            (0.0, 0.01, 0.01),
        ),
        patch(
            "custom_components.kohler_konnect.coordinator.USAGE_REFRESH_DELAY_SECONDS",
            0.01,
        ),
        patch(
            "custom_components.kohler_konnect.coordinator.USAGE_RETRY_DELAY_SECONDS",
            0.01,
        ),
    ):
        yield


async def _new_york(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, at: str
) -> None:
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to(at)


def _pending(entry: MockConfigEntry) -> Any:
    from custom_components.kohler_konnect.const import CONF_WARMUP_RESTORE_PENDING

    valve = (entry.options.get(CONF_VALVES) or {}).get(VALVE_ID) or {}
    return valve.get(CONF_WARMUP_RESTORE_PENDING)


# --------------------------------------------------------------------------- #
# 1. Water Used Today still leaves out what it borrowed, after a restart or reload
# --------------------------------------------------------------------------- #
async def test_a_borrow_kept_from_before_a_restart_is_taken_back(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
    kohler: Review2Kohler,
) -> None:
    """9 am: the bucket holds last evening's 50 L, already reported yesterday, and 30 L.

    The restarted entity read all 80 L under the same `last_reset` as the 30 L before the
    restart, so statistics counted the evening's water twice.
    """
    await _new_york(hass, freezer, "2026-10-16T13:00:00+00:00")
    kohler.usage_days = {"2026-10-14": 10.0, "2026-10-16": 80.0}
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(FAUCET_TODAY, "30.0"),
                {"borrowed_day": "2026-10-16", "borrowed_litres": 50.0},
            )
        ],
    )
    await _set_up(hass, config_entry)

    state = hass.states.get(FAUCET_TODAY)
    assert float(state.state) == 30.0
    assert state.attributes["counted_yesterday"] == 50.0


async def test_a_borrow_is_handed_over_across_a_reload(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
    kohler: Review2Kohler,
) -> None:
    """The same, through the entity Home Assistant removes and adds again."""
    await _new_york(hass, freezer, "2026-10-16T01:05:00+00:00")  # 9:05 pm, Oct 15
    kohler.usage_days = {"2026-10-14": 10.0, "2026-10-16": 50.0}
    await _set_up(hass, config_entry)
    state = hass.states.get(FAUCET_TODAY)
    assert float(state.state) == 50.0
    assert state.attributes["bucket_date"] == "2026-10-16"

    freezer.move_to("2026-10-16T13:00:00+00:00")  # 9 am: 30 L more in that bucket
    kohler.usage_days = {"2026-10-14": 10.0, "2026-10-16": 80.0}
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get(FAUCET_TODAY).state) == 30.0


# --------------------------------------------------------------------------- #
# 2. Water Used This Year waits for the monthly series
# --------------------------------------------------------------------------- #
async def test_this_year_is_unknown_until_the_monthly_series_answers(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
    kohler: Review2Kohler,
) -> None:
    """Summed from the daily window alone it read a fraction of the year, and statistics
    recorded the difference as negative water."""
    await _new_york(hass, freezer, "2026-10-16T16:00:00+00:00")
    kohler.usage_months = None  # refused, as a failed read is
    kohler.usage_days = {f"2026-10-{day:02d}": 1.0 for day in range(1, 17)}
    await _set_up(hass, config_entry)
    assert hass.states.get(FAUCET_YEAR).state == "unknown"
    assert float(hass.states.get(FAUCET_MONTH).state) == 16.0

    kohler.usage_months = {f"2026-{month:02d}": 100.0 for month in range(1, 11)}
    freezer.tick(FAUCET_USAGE_REFRESH_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(hass.states.get(FAUCET_YEAR).state) == 1000.0


# --------------------------------------------------------------------------- #
# 4. A Disconnected older than the valve's own word does not turn it off
# --------------------------------------------------------------------------- #
async def test_a_read_back_answer_older_than_a_valve_message_leaves_it_connected(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: Review2Kohler
) -> None:
    """Each warm-up read-back is a free cloud check, and the valve spoke during them.

    The replies said Disconnected — older than the message — and turned the sensor off
    straight after it proved the valve reachable.
    """
    await _set_up(hass, config_entry)
    assert hass.states.get(CLOUD).state == "on"
    kohler.valve_connection = "Disconnected"
    kohler.speak_during_read = True

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": WARMUP_SELECT, "option": "Started Outlets"},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(CLOUD)
    assert state.state == "on"
    assert state.attributes["connection_state"] == "Disconnected"
    assert "after this read was sent" in state.attributes["reachable_because"]


# --------------------------------------------------------------------------- #
# 5. A restore owed when Home Assistant stops is made after it starts
# --------------------------------------------------------------------------- #
async def test_a_restore_owed_before_a_restart_is_made_after_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: Review2Kohler
) -> None:
    """The setup seed found warm-up off with nothing before it, which is not a disable."""
    from custom_components.kohler_konnect.const import CONF_WARMUP_RESTORE_PENDING

    kohler.warmup_mode = OFF
    await _set_up(
        hass,
        config_entry,
        **{
            CONF_WARMUP_AUTO_RESTORE: True,
            CONF_LAST_WARMUP_MODE: ALL,
            CONF_WARMUP_RESTORE_PENDING: ALL,
        },
    )
    await _until(lambda: kohler.warmup_writes == [ALL], "the restore")
    await hass.async_block_till_done()
    assert hass.states.get(WARMUP_SELECT).state == "All Outlets"
    assert _pending(config_entry) is None


async def test_a_restore_waiting_out_a_shower_survives_a_reload(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: Review2Kohler
) -> None:
    """The reload cancelled the restore's task, and nothing took it up again.

    Waits on real time rather than `async_block_till_done`, which the restore waiting on
    the shower would hold up.
    """
    await _set_up(hass, config_entry, **{CONF_WARMUP_AUTO_RESTORE: True})
    _report(RUNNING_WORD)
    await hass.async_block_till_done()
    kohler.warmup_mode = OFF
    _announce(OFF)
    await _until(lambda: _pending(config_entry) == ALL, "the restore to be owed")
    await _quiet()
    assert kohler.warmup_writes == []

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await _until(lambda: kohler.warmup_writes == [ALL], "the restore after the reload")
    assert _pending(config_entry) is None


# --------------------------------------------------------------------------- #
# 7. Off chosen here, and seen only by a later reseed, stays off
# --------------------------------------------------------------------------- #
async def test_off_picked_here_and_found_by_a_later_reseed_stays_off(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: Review2Kohler
) -> None:
    """Every read-back still showed All Outlets, and the valve's echo never came; the next
    reseed, past the echo's grace, found warm-up off and restored over the owner's Off."""
    await _set_up(hass, config_entry, **{CONF_WARMUP_AUTO_RESTORE: True})
    with patch(
        "custom_components.kohler_konnect.warmup_manager."
        "WARMUP_SELF_WRITE_GRACE_SECONDS",
        0.05,
    ):
        kohler.lagging_reads = 3
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": WARMUP_SELECT, "option": "Off"},
            blocking=True,
        )
        await _quiet(0.1)  # past the grace, with no echo
        await account(hass, config_entry).async_refresh()  # update_entity's reseed
        await _quiet()
        await hass.async_block_till_done()

    assert kohler.warmup_writes == [OFF]
    assert hass.states.get(WARMUP_SELECT).state == "Off"


# --------------------------------------------------------------------------- #
# 8. The warm-up read-back applies the warm-up fields, and leaves the outlets alone
# --------------------------------------------------------------------------- #
async def test_the_warmup_read_back_leaves_the_outlets_as_the_valve_reported(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: Review2Kohler
) -> None:
    """The read-back applied all of `gcs-state`, whose `valve1` can still show an outlet
    the valve has closed: choosing a warm-up mode switched the outlet back on."""
    await _set_up(hass, config_entry)
    _report(STOPPED_WORD)
    await hass.async_block_till_done()
    assert hass.states.get(OUTLET_1).state == "off"

    kohler.stale_valve1 = {
        "out1": "1",
        "out2": "0",
        "out3": "0",
        "temperatureSetpoint": 40,
        "flowSetpoint": 50,
        "pauseFlag": "0",
    }
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": WARMUP_SELECT, "option": "Started Outlets"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(WARMUP_SELECT).state == "Started Outlets"
    assert hass.states.get(OUTLET_1).state == "off"
    assert hass.states.get(STATUS).state == "Idle"


# --------------------------------------------------------------------------- #
# 12. Statistics recorded for the rolling weeks before 1.03 carry on
# --------------------------------------------------------------------------- #
class TestRollingWeekStatistics:
    """Inside a recorder, with the statistics a 1.02 install already has."""

    @pytest.fixture
    def mock_recorder_before_hass(self, async_setup_recorder_instance: Any) -> None:
        """The recorder's database comes before `hass`, as the test plugin requires."""

    async def test_no_state_class_removed_issue_for_the_weekly_sensors(
        self,
        recorder_mock: Any,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        kohler: Review2Kohler,
    ) -> None:
        """1.03 dropped the weeks' state class, and Home Assistant raised
        `state_class_removed` for the statistics they already had — telling the owner
        that long-term statistics could no longer be tracked for them."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.models import StatisticMeanType
        from homeassistant.components.recorder.statistics import (
            async_import_statistics,
            get_metadata,
            validate_statistics,
        )
        from pytest_homeassistant_custom_component.components.recorder.common import (
            async_wait_recording_done,
        )

        kohler.valve_usage = {"DAY": {"2026-10-14": 40.0}, "MONTH": {"2026-10": 40.0}}
        await _set_up(hass, config_entry)
        hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        for week in WEEKS:
            float(hass.states.get(week).state)  # a number, as the check requires
            async_import_statistics(
                hass,
                {
                    "has_sum": True,
                    "mean_type": StatisticMeanType.NONE,
                    "name": None,
                    "source": "recorder",
                    "statistic_id": week,
                    "unit_class": "volume",
                    "unit_of_measurement": "L",
                },
                [{"start": hour - timedelta(hours=1), "state": 1.0, "sum": 1.0}],
            )
        await async_wait_recording_done(hass)

        recorder = get_instance(hass)
        recorded = await recorder.async_add_executor_job(
            lambda: get_metadata(hass, statistic_ids=set(WEEKS))
        )
        assert set(recorded) == set(WEEKS)
        issues = await recorder.async_add_executor_job(validate_statistics, hass)
        for week in WEEKS:
            assert [issue.type for issue in issues.get(week, [])] == [], issues
