"""Second review of 2026-10-16: water statistics, cloud reachability, warm-up, the journal.

Each test here failed on the code before its fix, unless its docstring says it guards the
fix itself. The harnesses are borrowed from the suites that own each module, as in
`test_review_water.py`: `test_cloud_watch.Rig`, and `test_warmup`'s stand-in valve and
virtual clock. Names added by the fixes are imported inside the tests that use them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.helpers.restore_state import RestoredExtraData
from homeassistant.util import dt as dt_util

from custom_components.kohler_konnect import warmup_manager
from custom_components.kohler_konnect.const import (
    CLOUD_CHECK_QUIET_SECONDS as QUIET,
)
from custom_components.kohler_konnect.const import (
    CONF_WARMUP_AUTO_RESTORE,
    WARMUP_READBACK_DELAYS,
)
from custom_components.kohler_konnect.konnect import KohlerError
from custom_components.kohler_konnect.konnect.const import WARMUP_ALL_OUTLETS
from custom_components.kohler_konnect.konnect.journal import (
    WARMUP_README,
    DebugJournal,
)

from . import test_warmup
from .conftest import make_coordinator, make_valve
from .test_cloud_watch import Rig
from .test_warmup import ALL, DELAY, GRACE, OFF, SELECTED, _settle

# `test_warmup`'s virtual clock and stand-in valves, as fixtures here too.
clock = test_warmup.clock
make = test_warmup.make

WATER = "custom_components.kohler_konnect.water"
NEW_YORK = ZoneInfo("America/New_York")


@pytest.fixture
def pin(monkeypatch):
    """Pin "now" to a UTC instant, with Home Assistant's time zone set to New York."""
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", NEW_YORK)

    def _pin(utc_iso: str) -> None:
        fixed = datetime.fromisoformat(utc_iso).replace(tzinfo=UTC)

        class _Pinned(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed if tz in (None, UTC) else fixed.astimezone(tz)

        monkeypatch.setattr(f"{WATER}.datetime", _Pinned)
        monkeypatch.setattr(
            dt_util,
            "now",
            lambda time_zone=None: fixed.astimezone(time_zone or NEW_YORK),
        )

    return _pin


def _model():
    from custom_components.kohler_konnect.konnect.models import get_valve_model

    return get_valve_model("K-28210")


def _usage(litres: dict[str, float]) -> dict:
    """A fresh `gcs-usage` payload, monthly or daily — fresh, so caches see a new read.

    The outer dict is made first, as the cache test below needs: it is the object that,
    built straight after the last one is freed, lands at that one's address.
    """
    payload: dict[str, Any] = {}
    payload["gcsUsageDataDetailsList"] = [
        {"intervalKey": key, "volume": value} for key, value in litres.items()
    ]
    return payload


def _valve_water(*, monthly: dict | None = None, daily: dict | None = None):
    """A valve's four water totals, on a metric account; `build` makes more of a kind."""
    from custom_components.kohler_konnect import sensor as module

    valve = make_valve(_model(), [31, 11, 1])
    valve.usage = monthly or {}
    valve.usage_daily = daily or {}
    coordinator = make_coordinator([valve])
    coordinator.water_units = "Metric"
    kinds = {
        "today": module.ValveDailyWaterSensor,
        "week": module.ValveWeeklyWaterSensor,
        "month": module.ValveMonthlyWaterSensor,
        "year": module.ValveYearlyWaterSensor,
    }

    def build(kind: str):
        return kinds[kind](coordinator, valve)

    return valve, SimpleNamespace(build=build, **{k: build(k) for k in kinds})


def _faucet_water(cls, *, monthly: dict | None = None, daily: dict | None = None):
    """A faucet water total, built without Home Assistant around a stand-in coordinator."""
    sensor = cls.__new__(cls)
    sensor.coordinator = SimpleNamespace(
        usage=monthly or {},
        usage_daily=daily or {},
        account=SimpleNamespace(water_units="Metric"),
        async_add_listener=lambda *args, **kwargs: lambda: None,
    )
    sensor.coordinator_context = None
    return sensor


class _Statistics:
    """Home Assistant's long-term sum for a `TOTAL` sensor, fed one state at a time.

    As `sensor/recorder.py` compiles it: the first state is the zero point; a new
    `last_reset` starts a cycle whose first value counts in full; inside a cycle every
    change counts, a fall as negative water; unknown is skipped.
    """

    def __init__(self) -> None:
        self._sum = 0.0
        self._old: float | None = None
        self._new: float | None = None
        self._reset: datetime | None = None

    def record(self, sensor: Any) -> None:
        value, last_reset = sensor.native_value, sensor.last_reset
        if value is None:
            return
        if last_reset is not None and last_reset != self._reset:
            if self._old is not None and self._new is not None:
                self._sum += self._new - self._old
            self._old = 0.0 if self._old is not None else value
            self._reset = last_reset
        elif self._old is None:
            self._old = value
        self._new = value

    @property
    def total(self) -> float:
        return self._sum + (self._new or 0.0) - (self._old or 0.0)


# =========================================================================== #
# 1. What Water Used Today borrowed is still owed after a restart or reload
# =========================================================================== #
@pytest.mark.asyncio
@pytest.mark.parametrize("handover", ["restart", "reload"])
async def test_a_borrow_is_still_owed_after_a_restart_the_next_morning(pin, handover):
    """US Eastern, UTC-labelled buckets: 50 L at 9:05 pm lands in tomorrow's bucket.

    Midnight reads 0, a 30 L shower at 8 am reads 30 — and Home Assistant restarting at
    9 am made a new entity that read the whole bucket, 80 L, under the same `last_reset`.
    Statistics recorded 130 L for the 80 L used. The borrow was held in memory only.
    """
    statistics = _Statistics()
    valve, water = _valve_water()
    today = water.today

    pin("2026-10-15T11:00:00")  # 7 am, October 15
    valve.usage_daily = _usage({"2026-10-14": 10.0})
    statistics.record(today)
    pin("2026-10-16T01:05:00")  # 9:05 pm, filed under October 16
    valve.usage_daily = _usage({"2026-10-14": 10.0, "2026-10-16": 50.0})
    statistics.record(today)
    assert today.native_value == 50.0
    pin("2026-10-16T04:00:01")  # local midnight: a new period
    statistics.record(today)
    pin("2026-10-16T12:00:00")  # 8 am: 30 L more in the same bucket
    valve.usage_daily = _usage({"2026-10-14": 10.0, "2026-10-16": 80.0})
    statistics.record(today)
    assert today.native_value == 30.0

    # What Home Assistant's restore-state store keeps when the entity goes: through a
    # JSON file for a restart, the object itself for a reload.
    kept = today.extra_restore_state_data
    if handover == "restart":
        kept = RestoredExtraData(json.loads(json.dumps(kept.as_dict())))
    pin("2026-10-16T13:00:00")  # 9 am: the new entity
    restarted = water.build("today")
    restarted.async_get_last_extra_data = AsyncMock(return_value=kept)
    await restarted.async_added_to_hass()
    statistics.record(restarted)

    assert restarted.native_value == 30.0
    assert restarted.extra_state_attributes["counted_yesterday"] == 50.0
    assert statistics.total == pytest.approx(80.0), "the water used, counted once"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [
        {"borrowed_day": "2026-10-16", "borrowed_litres": 50.0},  # its day has passed
        {"borrowed_day": "never", "borrowed_litres": 50.0},
        {"borrowed_day": "2026-10-17", "borrowed_litres": "50"},
        {"borrowed_day": "2026-10-17", "borrowed_litres": -1.0},
        {"borrowed_day": "2026-10-17", "borrowed_litres": float("nan")},
        {},
    ],
)
async def test_only_a_borrow_still_owed_is_taken_back(pin, stored):
    """Guards the fix: a record from a day gone by, or one that is not a record, is ignored."""
    pin("2026-10-17T16:00:00")  # noon, October 17
    _, water = _valve_water(daily=_usage({"2026-10-16": 50.0, "2026-10-17": 5.0}))
    today = water.today
    today.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(stored))
    await today.async_added_to_hass()
    assert today.native_value == 5.0
    assert "counted_yesterday" not in today.extra_state_attributes
    assert today.extra_restore_state_data is None


@pytest.mark.asyncio
async def test_the_faucets_water_used_today_keeps_its_borrow_too(pin):
    """Guards the fix: faucet sensors subclass the same base, unchanged."""
    from custom_components.kohler_konnect.faucet.sensor import (
        FaucetDailyWaterSensor,
        FaucetWeeklyWaterSensor,
    )

    pin("2026-10-16T01:05:00")  # 9:05 pm, October 15
    daily = _usage({"2026-10-16": 2.5})
    today = _faucet_water(FaucetDailyWaterSensor, daily=daily)
    assert today.native_value == 2.5
    kept = today.extra_restore_state_data
    assert kept.as_dict() == {"borrowed_day": "2026-10-16", "borrowed_litres": 2.5}
    # A rolling window keeps nothing: the subtraction is the period total's alone.
    week = _faucet_water(FaucetWeeklyWaterSensor, daily=daily)
    assert week.native_value == 2.5
    assert week.extra_restore_state_data is None

    pin("2026-10-16T13:00:00")
    restarted = _faucet_water(FaucetDailyWaterSensor, daily=_usage({"2026-10-16": 3.0}))
    restarted.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData(kept.as_dict())
    )
    await restarted.async_added_to_hass()
    assert restarted.native_value == 0.5


# =========================================================================== #
# 2. Water Used This Year is unknown, not partial, without the monthly series
# =========================================================================== #
# Read mid-October: ten months of 1000 L, the last of them partial.
MONTHS = {f"2026-{month:02d}": 1000.0 for month in range(1, 11)}
# The 35-day daily window: 30 L a day from September 11, 60 L a day in October.
WINDOW = {f"2026-09-{day:02d}": 30.0 for day in range(11, 31)} | {
    f"2026-10-{day:02d}": 60.0 for day in range(1, 17)
}


@pytest.mark.parametrize("device", ["valve", "faucet"])
def test_the_year_is_unknown_while_the_monthly_series_is_missing(pin, device):
    """A restart whose monthly read failed summed the year from the daily window alone.

    10 000 L read 1 560 L, so statistics recorded −8 440 L — and +8 440 L once the
    monthly read came back, or never, if it did not before January 1.
    """
    from custom_components.kohler_konnect.faucet.sensor import FaucetYearlyWaterSensor

    def year(monthly: dict):
        if device == "valve":
            return _valve_water(monthly=monthly, daily=_usage(WINDOW))[1].year
        return _faucet_water(
            FaucetYearlyWaterSensor, monthly=monthly, daily=_usage(WINDOW)
        )

    pin("2026-10-16T16:00:00")
    statistics = _Statistics()
    before = year(_usage(MONTHS))
    statistics.record(before)
    assert before.native_value == 10_000.0

    restarted = year({})  # the monthly read failed; the daily one did not
    statistics.record(restarted)
    assert restarted.native_value is None
    assert restarted.extra_state_attributes == {}

    recovered = year(_usage(MONTHS | {"2026-10": 1020.0}))  # read again, 20 L later
    statistics.record(recovered)
    assert recovered.native_value == 10_020.0
    assert statistics.total == pytest.approx(20.0)


def test_in_january_the_daily_series_is_the_whole_year(pin):
    """Guards the fix: the window spans all of January, as it spans any month in progress."""
    pin("2027-01-20T17:00:00")
    daily = {f"2026-12-{day:02d}": 50.0 for day in range(16, 32)} | {
        f"2027-01-{day:02d}": 40.0 for day in range(1, 21)
    }
    _, water = _valve_water(daily=_usage(daily))
    assert water.year.native_value == 800.0
    assert water.year.extra_state_attributes["per_month"] == {"2027-01": 800.0}


def test_a_daily_bucket_from_last_year_vouches_for_the_whole_year(pin):
    """Guards the fix: a bucket on or before January 1 proves the window reached it."""
    pin("2027-02-03T17:00:00")
    daily = {f"2027-01-{day:02d}": 10.0 for day in range(2, 32)} | {"2027-02-01": 5.0}
    _, water = _valve_water(daily=_usage(daily))
    assert water.year.native_value is None, "nothing says the window reached January 1"

    _, water = _valve_water(daily=_usage({"2026-12-31": 7.0} | daily))
    assert water.year.native_value == 305.0


# =========================================================================== #
# 3. A month that has ended never falls
# =========================================================================== #
def test_a_month_that_has_ended_never_falls_as_the_daily_window_slides(pin):
    """October's daily total is whole until about November 5; then the window slides past
    October 1, while the monthly figure on hand is still the partial one read mid-October.

    The year fell by October 1's water — and the 20 L of the November shower that brought
    the new daily series reached statistics as −20 L.
    """
    october = {f"2026-10-{day:02d}": 40.0 for day in range(1, 32)}
    valve, water = _valve_water(
        monthly=_usage(
            dict.fromkeys((f"2026-{m:02d}" for m in range(1, 10)), 1000.0)
            | {"2026-10": 600.0}
        ),
        daily=_usage(october),
    )
    statistics = _Statistics()

    pin("2026-10-31T23:00:00")  # 7 pm, October 31
    statistics.record(water.year)
    assert water.year.native_value == 10_240.0
    pin("2026-11-01T04:00:01")  # the midnight re-render: October has ended
    statistics.record(water.year)
    assert water.month.extra_state_attributes["history"]["2026-10"] == 1240.0

    pin("2026-11-06T17:00:00")  # the first November shower, and the read after it
    del october["2026-10-01"]
    valve.usage_daily = _usage(october | {"2026-11-06": 20.0})
    statistics.record(water.year)

    assert water.year.native_value == 10_260.0
    assert water.year.extra_state_attributes["per_month"]["2026-10"] == 1240.0
    assert water.month.extra_state_attributes["history"]["2026-10"] == 1240.0
    assert water.month.native_value == 20.0
    assert statistics.total == pytest.approx(20.0)


def test_the_month_in_progress_still_follows_the_series(pin):
    """Guards the fix: only ended months are held; one in progress is still moving."""
    pin("2026-10-20T16:00:00")
    valve, water = _valve_water(daily=_usage({"2026-10-05": 300.0}))
    assert water.month.native_value == 300.0
    valve.usage_daily = _usage({"2026-10-05": 280.0})  # Kohler revised the day
    assert water.month.native_value == 280.0
    assert water.year.native_value is None  # and no monthly series: see item 2


# =========================================================================== #
# 10. A new payload where the last one was is not served from the cache
# =========================================================================== #
@pytest.mark.parametrize("kind", ["today", "week", "month", "year"])
def test_a_payload_built_where_the_last_one_was_is_rendered_afresh(pin, kind):
    """The caches were keyed on `id()`, which Python hands out again once an object is
    freed: a new payload allocated at the old one's address published the old figure.

    The year needs its monthly series (see item 2), and keeps one throughout.
    """
    pin("2026-10-16T16:00:00")
    valve, water = _valve_water(monthly=_usage({"2026-09": 100.0}))
    sensor = getattr(water, kind)
    valve.usage_daily = _usage({"2026-10-16": 1.0})
    figure = sensor.native_value
    for litres in (2.0, 3.0, 4.0):
        # Built before the old payload goes, so the new one is the very next dict made —
        # which CPython puts where the old one was.
        buckets = {"2026-10-16": litres}
        valve.usage_daily = None  # the payload's only reference: freed here
        valve.usage_daily = _usage(buckets)
        assert sensor.native_value == figure + litres - 1.0


# =========================================================================== #
# 4. A Disconnected older than the valve's own word does not turn it off
# =========================================================================== #
@pytest.fixture
def rig(monkeypatch):
    rig = Rig(monkeypatch)
    rig.watch.async_start()
    yield rig
    for task in rig.hass.tasks:
        if not task.done():
            task.cancel()


def test_a_disconnected_read_sent_before_the_valve_spoke_leaves_it_reachable(rig):
    """The valve spoke while the read was in flight; the reply, older, said Disconnected.

    The sensor went off again straight after the message proved the valve reachable, and
    stayed off until the next check — up to three hours later.
    """
    rig.watch.note_rest_payload({"connectionState": "Connected"}, "seed", notify=False)
    rig.timers.advance(QUIET)  # trigger B: a check is due

    async def overtaken(device_id: str) -> Any:
        rig.clock.now += 2
        rig.valve_speaks()  # mid-read
        return {"connectionState": "Disconnected"}

    rig.client.async_get_gcs_state = overtaken
    assert rig.run_checks() == 1

    assert rig.watch.connected is True
    attributes = rig.watch.attributes
    # Kohler's answer stays verbatim, with the reason the sensor disagrees with it.
    assert attributes["connection_state"] == "Disconnected"
    assert "after this read was sent" in attributes["reachable_because"]
    assert attributes["last_error"] is None


def test_a_disconnected_read_sent_after_the_valve_last_spoke_still_counts(rig):
    """Guards the fix: silence since the message, then Disconnected, is an outage."""
    rig.valve_speaks()
    rig.timers.advance(QUIET)
    rig.client.replies = [{"connectionState": "Disconnected"}]
    assert rig.run_checks() == 1
    assert rig.watch.connected is False
    assert rig.watch.attributes["reachable_because"] is None


@pytest.mark.asyncio
async def test_the_warmup_read_back_says_when_its_read_was_sent(make, clock):
    """The read-back is a free cloud check too, and must carry `requested_at` with it."""
    valve = make(SELECTED)
    sent_at = clock.now
    assert await valve.warmup.async_read_warmup_mode() == SELECTED
    assert valve.cloud_requested_at == [sent_at]


# =========================================================================== #
# 5. A restore owed when Home Assistant stops is finished by the next seed
# =========================================================================== #
@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_for", ["the delay", "the shower"])
async def test_a_pending_restore_is_finished_after_a_restart(make, clock, waiting_for):
    """The restore lived in a task, and a restart cancelled it.

    The next seed found warm-up off with nothing before it — which is not a disable — so
    it stayed off. Waiting out a shower made the window as long as the shower.
    """
    from custom_components.kohler_konnect.const import CONF_WARMUP_RESTORE_PENDING

    valve = make(ALL)
    if waiting_for == "the shower":
        valve.run_water()
    valve.announce(OFF)
    await clock.advance(DELAY / 2 if waiting_for == "the delay" else DELAY)
    valve.stop()  # Home Assistant stops, and the restore task with it
    await _settle()
    assert valve.options[CONF_WARMUP_RESTORE_PENDING] == ALL

    restarted = make(None)
    restarted.options = valve.options  # the config entry outlives the restart
    restarted.reseed(OFF)  # the first seed: off, with nothing before it
    assert restarted.warmup_log.last("restore_resumed") == {
        "target": ALL,
        "source": "rest",
    }
    await clock.advance(DELAY)
    assert restarted.cloud.writes == [ALL]
    assert restarted.options[CONF_WARMUP_RESTORE_PENDING] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["enabled again", "auto-restore off"])
async def test_a_pending_restore_no_longer_wanted_is_dropped(make, clock, change):
    """Guards the fix: honoured only while warm-up is still off and the switch still on."""
    from custom_components.kohler_konnect.const import CONF_WARMUP_RESTORE_PENDING

    valve = make(ALL)
    valve.announce(OFF)
    await clock.advance(DELAY / 2)
    valve.stop()
    await _settle()

    restarted = make(None)
    restarted.options = valve.options
    if change == "enabled again":
        restarted.reseed(SELECTED)
    else:
        restarted.options[CONF_WARMUP_AUTO_RESTORE] = False
        restarted.reseed(OFF)
    await clock.advance(DELAY * 3)
    assert restarted.cloud.writes == []
    assert restarted.options[CONF_WARMUP_RESTORE_PENDING] is None
    assert "restore_resumed" not in restarted.warmup_log.events()


@pytest.mark.asyncio
async def test_off_chosen_here_settles_a_pending_restore_for_good(make, clock):
    """Guards the fix: after a restart there is no memory of the write to check against."""
    from custom_components.kohler_konnect.const import CONF_WARMUP_RESTORE_PENDING

    valve = make(ALL)
    valve.announce(OFF)
    await clock.advance(DELAY / 2)
    await valve.warmup.async_set_warmup(OFF)
    assert valve.options[CONF_WARMUP_RESTORE_PENDING] is None
    valve.stop()
    await _settle()

    restarted = make(None)
    restarted.options = valve.options
    restarted.reseed(OFF)
    await clock.advance(DELAY * 3)
    assert restarted.cloud.writes == []


# =========================================================================== #
# 6. A delayed-start mode is not decided for and then refused
# =========================================================================== #
def test_only_a_mode_this_integration_writes_is_a_restore_decision():
    """`async_set_warmup` refuses the delayed-start modes, so deciding for one was
    deciding for a `restore_failed` — counted toward giving up, on every disable."""
    from custom_components.kohler_konnect.konnect.const import WARMUP_SELECTED_OUTLETS
    from custom_components.kohler_konnect.konnect.warmup import (
        restorable,
        restore_target,
        should_restore_warmup,
    )

    for legacy in (WARMUP_ALL_OUTLETS, WARMUP_SELECTED_OUTLETS, "warmUpSomethingNew"):
        assert not should_restore_warmup(
            legacy,
            OFF,
            enabled=True,
            self_write_mode=None,
            self_write_age=None,
            grace_seconds=GRACE,
        )
        # Not swapped for the remembered mode either: older, and maybe other outlets.
        assert restore_target(legacy, SELECTED) is None
        assert not restorable(legacy)
    assert restore_target(None, WARMUP_ALL_OUTLETS) is None
    assert [m for m in (ALL, SELECTED, OFF, None) if restorable(m)] == [ALL, SELECTED]


@pytest.mark.asyncio
async def test_a_delayed_start_mode_disabled_is_explained_once_and_never_counted(
    make, clock, caplog
):
    """It was a `restore_failed` on every hub sign-in, each one counted toward giving up."""
    valve = make(WARMUP_ALL_OUTLETS)
    with caplog.at_level(logging.WARNING, logger=warmup_manager.__name__):
        for _ in range(3):
            valve.announce(OFF)
            await clock.advance(DELAY)
            valve.announce(WARMUP_ALL_OUTLETS)  # set again at the panel
    assert valve.cloud.writes == []
    assert valve.warmup._warmup_restores == 0
    assert "restore_failed" not in valve.warmup_log.events()
    assert valve.warmup_log.last("restore_skipped") == {
        "reason": "the mode taken away is not one this integration writes",
        "mode": WARMUP_ALL_OUTLETS,
    }
    warnings = [r for r in caplog.records if "cannot put" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_a_delayed_start_mode_taken_away_during_the_wait_is_not_papered_over(
    make, clock
):
    """A restore waiting for All Outlets; meanwhile the panel sets a delayed-start mode and
    the hub disables that. The latest mode taken away is not one this writes, so the wait
    ends in nothing — not in the older mode, and not in a counted failure."""
    valve = make(ALL)
    valve.announce(OFF)
    await clock.advance(10)
    valve.announce(WARMUP_ALL_OUTLETS)
    valve.announce(OFF)
    await clock.advance(DELAY)
    assert valve.cloud.writes == []
    assert valve.warmup._warmup_restores == 0
    assert valve.warmup_log.last("restore_skipped")["mode"] == WARMUP_ALL_OUTLETS


# =========================================================================== #
# 7. Off chosen here and seen late is not overwritten
# =========================================================================== #
@pytest.mark.asyncio
@pytest.mark.parametrize("read_back", ["failed", "lagging"])
async def test_off_chosen_here_and_found_by_a_late_reseed_is_left_off(
    make, clock, read_back
):
    """The owner picks Off; the read-back cannot confirm it — Kohler is unreachable, or
    still shows the old mode — and the reconnect's reseed finds warm-up off after the
    echo's thirty-second grace.

    The reseed read that as somebody else disabling warm-up and scheduled a restore, after
    the write — so the check for an Off chosen during the wait did not cover it, and the
    old mode was written straight over the owner's choice.
    """
    valve = make(ALL)
    if read_back == "failed":
        valve.cloud.read_error = KohlerError("unreachable")
    else:
        valve.cloud.stale_reads = len(WARMUP_READBACK_DELAYS)
    write = asyncio.ensure_future(valve.warmup.async_set_warmup(OFF))
    await clock.advance(sum(WARMUP_READBACK_DELAYS))
    await write
    valve.cloud.read_error = None
    assert valve.gcs_state.warmup_mode == ALL, "nothing has shown the Off yet"

    await clock.advance(GRACE + 30)
    valve.reseed(OFF)
    mode = valve.warmup_log.last("mode")
    assert (mode["ours"], mode["restoring"]) == (True, False)
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == [OFF]


@pytest.mark.asyncio
async def test_a_mode_enabled_since_our_off_makes_the_next_disable_somebody_elses(
    make, clock
):
    """Guards the fix: the Off is ours only until something turns warm-up back on."""
    valve = make(ALL)
    valve.cloud.read_error = KohlerError("unreachable")
    write = asyncio.ensure_future(valve.warmup.async_set_warmup(OFF))
    await clock.advance(sum(WARMUP_READBACK_DELAYS))
    await write
    valve.cloud.read_error = None
    await clock.advance(GRACE + 30)
    valve.reseed(SELECTED)  # chosen in the app while the stream was down
    await clock.advance(60)
    valve.announce(OFF)  # and then a hub sign-in
    await clock.advance(DELAY)
    assert valve.cloud.writes == [OFF, SELECTED]


@pytest.mark.asyncio
async def test_a_seed_inside_the_grace_showing_the_old_mode_proves_nothing(make, clock):
    """Guards the fix: the cloud document trails the valve, so a seed seconds after our Off
    can still show the mode it replaced. That is not warm-up being turned back on."""
    valve = make(ALL)
    valve.cloud.read_error = KohlerError("unreachable")
    write = asyncio.ensure_future(valve.warmup.async_set_warmup(OFF))
    await clock.advance(sum(WARMUP_READBACK_DELAYS))
    await write
    valve.cloud.read_error = None
    valve.reseed(ALL)  # inside the grace, the old mode
    await clock.advance(GRACE + 30)
    valve.reseed(OFF)
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == [OFF]


# =========================================================================== #
# 8. The warm-up read-back applies the warm-up fields and nothing else
# =========================================================================== #
@pytest.mark.asyncio
async def test_the_read_back_never_puts_back_zone_words(make, clock):
    """It applied the whole `gcs-state`, and the cloud document can still show an outlet
    open that the valve has closed since — opening it again in Home Assistant."""
    valve = make(OFF)
    valve.run_water(False)  # the valve's own report: nothing running
    read = valve.cloud.async_get_gcs_state

    async def behind(device_id: str) -> dict[str, Any]:
        payload = await read(device_id)
        payload["state"]["valve1"] = {
            "out1": "1",
            "temperatureSetpoint": 40,
            "flowSetpoint": 50,
            "pauseFlag": "0",
        }
        return payload

    valve.cloud.async_get_gcs_state = behind
    await valve.warmup.async_set_warmup(SELECTED)
    assert valve.gcs_state.warmup_mode == SELECTED
    assert valve.gcs_state.valve1.outlet_mask == 0
    assert not valve.gcs_state.is_running


@pytest.mark.asyncio
async def test_a_status_report_during_the_read_back_does_not_void_it(make, clock):
    """Any push voided the read-back, an ordinary status report included; only one carrying
    the warm-up mode is newer than the reply's mode. The running flag is never the
    read-back's to set: the document trails the valve, and a lagging "not in progress"
    switched off the guard on changes made during a warm-up (2026-10-16)."""
    valve = make(SELECTED)
    valve.gcs_state.warmup_mode = None
    valve.gcs_state.warmup_in_progress = True  # as the report mid-read says
    read = valve.cloud.async_get_gcs_state

    async def reported_meanwhile(device_id: str) -> dict[str, Any]:
        valve._push_serials["valve1"] += 1
        return await read(device_id)

    valve.cloud.async_get_gcs_state = reported_meanwhile
    assert await valve.warmup.async_read_warmup_mode() == SELECTED
    assert valve.gcs_state.warmup_mode == SELECTED
    assert valve.gcs_state.warmup_in_progress is True
    assert valve.refreshes == 1

    valve.cloud.async_get_gcs_state = read  # nor with no report meanwhile
    await valve.warmup.async_read_warmup_mode()
    assert valve.gcs_state.warmup_in_progress is True


# =========================================================================== #
# 9. Closing the warm-up journal is final
# =========================================================================== #
def _journal(directory) -> DebugJournal:
    return DebugJournal(
        str(directory),
        forced=True,
        prefix="warmup",
        readme=WARMUP_README,
        readme_fields={"before": 120, "after": 45},
        label="Warmup journal",
    )


def test_a_note_after_close_never_opens_a_file(tmp_path):
    """A decision noted after unload queued itself and asked for `prepare()`, which opened
    a fresh file that nothing would ever close."""
    journal = _journal(tmp_path)
    journal.prepare()
    first = Path(journal.path).name
    journal.close()

    journal.note("restore_done", target=ALL)
    assert journal.wants_open is False
    journal.prepare()  # scheduled anyway, by a caller that did not look

    assert journal.path is None
    assert journal._handle is None
    assert [path.name for path in tmp_path.glob("warmup_*.jsonl")] == [first]


def test_a_first_prepare_that_loses_the_race_with_close_opens_nothing(tmp_path):
    """Setup schedules the first `prepare()`; an unload's `close()` can run before it."""
    journal = _journal(tmp_path)
    journal.close()
    journal.prepare()
    assert journal._handle is None
    assert list(tmp_path.glob("warmup_*.jsonl")) == []
    assert journal.roll() is None
    assert list(tmp_path.glob("warmup_*.jsonl")) == []
