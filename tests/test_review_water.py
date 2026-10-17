"""Review findings of 2026-10-16: water totals, cloud reachability, warm-up, the journals.

Each test here failed on the code before its fix, unless its docstring says it guards the
fix itself. The harnesses are borrowed from the suites that own each module —
`test_cloud_watch.Rig` and `test_warmup`'s stand-in valve and virtual clock — so these
exercise the same real classes the same way.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.util import dt as dt_util

from custom_components.kohler_konnect.const import (
    CLOUD_CHECK_QUIET_SECONDS as QUIET,
)
from custom_components.kohler_konnect.const import CONF_WARMUP_AUTO_RESTORE
from custom_components.kohler_konnect.konnect.journal import (
    WARMUP_README,
    DebugJournal,
)
from custom_components.kohler_konnect.konnect.report_log import ReportLog

from . import test_warmup
from .conftest import make_coordinator, make_valve
from .test_cloud_watch import Rig
from .test_warmup import ALL, DELAY, OFF, SELECTED, _settle

# `test_warmup`'s virtual clock and stand-in valves, as fixtures here too.
clock = test_warmup.clock
make = test_warmup.make

WATER = "custom_components.kohler_konnect.water"
NEW_YORK = ZoneInfo("America/New_York")
TOPIC = "$iothub/methods/POST/ExecuteControlCommand/?$rid=1"


def _model():
    from custom_components.kohler_konnect.konnect.models import get_valve_model

    return get_valve_model("K-28210")


# =========================================================================== #
# 1. A rolling week is a net total, not a meter
# =========================================================================== #
def test_a_rolling_week_keeps_its_state_class_as_a_net_total():
    """`TOTAL` with no `last_reset`, as through 1.02 (restored 2026-10-16).

    1.03 removed the state class, and Home Assistant stopped tracking the statistics owners
    already had, with a `state_class_removed` repair. A rolling window is a net total: its
    change can be negative, so `Water Used Today` is the meter. The faucets' week shares
    the base.
    """
    from custom_components.kohler_konnect import sensor as module
    from custom_components.kohler_konnect.faucet.sensor import (
        FaucetDailyWaterSensor,
        FaucetWeeklyWaterSensor,
    )

    valve = make_valve(_model(), [31, 11, 1])
    coordinator = make_coordinator([valve])
    for week in (
        module.ValveWeeklyWaterSensor(coordinator, valve),
        FaucetWeeklyWaterSensor.__new__(FaucetWeeklyWaterSensor),
    ):
        assert week.state_class is SensorStateClass.TOTAL, type(week).__name__
        assert week.last_reset is None, type(week).__name__
    # The period totals keep theirs: each says when its period started.
    for sensor in (
        module.ValveDailyWaterSensor(coordinator, valve),
        module.ValveMonthlyWaterSensor(coordinator, valve),
        module.ValveYearlyWaterSensor(coordinator, valve),
        FaucetDailyWaterSensor.__new__(FaucetDailyWaterSensor),
    ):
        assert sensor.state_class is SensorStateClass.TOTAL, type(sensor).__name__


# =========================================================================== #
# 2. Water Used Today, borrowed from tomorrow's UTC bucket, is not counted twice
# =========================================================================== #
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


def _daily(litres: dict[str, float]) -> dict:
    """A fresh `Interval=DAY` payload — fresh, so the sensors' cache sees a new read."""
    return {
        "gcsUsageDataDetailsList": [
            {"intervalKey": key, "volume": value} for key, value in litres.items()
        ]
    }


def _water(daily: dict):
    from custom_components.kohler_konnect import sensor as module

    valve = make_valve(_model(), [31, 11, 1])
    valve.usage_daily = daily
    coordinator = make_coordinator([valve])
    coordinator.water_units = "Metric"
    return (
        valve,
        module.ValveDailyWaterSensor(coordinator, valve),
        module.ValveWeeklyWaterSensor(coordinator, valve),
    )


def test_water_borrowed_from_tomorrows_bucket_is_not_counted_again_tomorrow(pin):
    """9 pm in New York is already tomorrow in UTC, and the evening's water is filed there.

    Today read it — and after local midnight read the same bucket again as the new day's,
    under a new `last_reset`. Home Assistant counts the first value of a period in full,
    so those 30 L went into long-term statistics twice.
    """
    pin("2026-10-16T01:00:00")  # 9 pm, October 15
    valve, today, _ = _water(_daily({"2026-10-14": 40.0, "2026-10-16": 30.0}))
    assert today.native_value == 30.0
    assert today.last_reset == datetime(2026, 10, 15, tzinfo=NEW_YORK)
    assert today.extra_state_attributes["bucket_date"] == "2026-10-16"

    pin("2026-10-16T04:00:01")  # local midnight: a new period, the same bucket
    assert today.last_reset == datetime(2026, 10, 16, tzinfo=NEW_YORK)
    assert today.native_value == 0.0
    assert today.extra_state_attributes["counted_yesterday"] == 30.0

    # This morning's shower still counts, and only it.
    valve.usage_daily = _daily({"2026-10-14": 40.0, "2026-10-16": 50.0})
    assert today.native_value == 20.0

    # The day after, there is nothing left to subtract.
    pin("2026-10-17T16:00:00")
    valve.usage_daily = _daily({"2026-10-16": 50.0, "2026-10-17": 5.0})
    assert today.native_value == 5.0
    assert "counted_yesterday" not in today.extra_state_attributes


def test_a_borrow_given_up_the_same_evening_owes_nothing_the_next_day(pin):
    """Guards the fix: once today's own bucket fills in, the borrowed water is not today's.

    Statistics then saw it leave yesterday's figure, so the next day must count it.
    """
    pin("2026-10-16T01:00:00")  # 9 pm, October 15
    valve, today, _ = _water(_daily({"2026-10-16": 30.0}))
    assert today.native_value == 30.0

    pin("2026-10-16T02:00:00")  # 10 pm: Kohler files an earlier shower under the 15th
    valve.usage_daily = _daily({"2026-10-15": 10.0, "2026-10-16": 30.0})
    assert today.native_value == 10.0

    pin("2026-10-16T04:00:01")  # midnight
    assert today.native_value == 30.0


def test_the_rolling_week_counts_a_borrowed_bucket_exactly_once(pin):
    """Guards the fix: the subtraction is the period total's, not the window's."""
    pin("2026-10-16T01:00:00")  # 9 pm, October 15
    _, _, week = _water(_daily({"2026-10-14": 40.0, "2026-10-16": 30.0}))
    assert week.native_value == 70.0
    pin("2026-10-16T04:00:01")
    assert week.native_value == 70.0


# =========================================================================== #
# 3 and 4. Cloud Connection
# =========================================================================== #
@pytest.fixture
def rig(monkeypatch):
    rig = Rig(monkeypatch)
    rig.watch.async_start()
    yield rig
    for task in rig.hass.tasks:
        if not task.done():
            task.cancel()


def test_a_valve_power_cycled_inside_the_cooldown_reads_connected_at_once(rig):
    """The owner's case: Disconnected, power-cycled ten minutes after it was confirmed.

    The recovery used to be a read, and the read that confirmed the outage had just
    stamped the 30-minute cooldown — so every boot message's re-check was skipped, the
    valve went quiet, and the next ask was trigger B, three hours on.
    """
    rig.watch.note_rest_payload(
        {"connectionState": "Disconnected"}, "REST seed", notify=False
    )
    rig.client.replies = [{"connectionState": "Disconnected"}]
    rig.timers.advance(QUIET)
    rig.run_checks()
    assert rig.watch.connected is False

    rig.timers.advance(600)
    for _ in range(4):  # the boot burst
        rig.valve_speaks()

    assert rig.watch.connected is True
    assert rig.reads == 1, "no read needed: the message came through Kohler's cloud"
    attributes = rig.watch.attributes
    # Kohler's last word stays verbatim; why the sensor disagrees with it is said too.
    assert attributes["connection_state"] == "Disconnected"
    assert "after Kohler's cloud reported it offline" in attributes["reachable_because"]


def test_a_stopped_watch_leaves_a_valve_reported_offline_alone(rig):
    """Unload: nothing may refresh entities on a coordinator that is going away."""
    rig.watch.note_rest_payload({"connectionState": "Disconnected"}, "t", notify=False)
    rig.watch.async_stop()
    rig.valve_speaks()
    assert rig.watch.connected is False
    assert rig.refreshes == 0


@pytest.mark.parametrize("payload", [["Connected"], "Connected", 503])
def test_a_gcs_state_body_that_is_not_an_object_never_raises(rig, payload):
    """`(payload or {}).get` raised `AttributeError`, which the seed does not catch."""
    rig.watch.note_rest_payload({"connectionState": "Connected"}, "t", notify=False)

    rig.watch.note_rest_payload(payload, "REST seed", notify=False)

    assert rig.watch.connected is True, "not a verdict"
    assert "not a JSON object" in rig.watch.attributes["last_error"]

    # The module's own read takes the same path.
    rig.client.replies = [payload]
    rig.timers.advance(QUIET)
    assert rig.run_checks() == 1
    assert rig.watch.connected is True


# =========================================================================== #
# 5 and 6. Warm-up auto-restore
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_restore_refused_for_running_water_is_not_counted_toward_giving_up(
    make, clock
):
    """The refusal counted as an attempt, though nothing was sent."""
    valve = make(ALL)
    valve.run_water()
    valve.announce(OFF)
    await clock.advance(DELAY * 5)
    assert valve.cloud.writes == []
    assert valve.warmup._warmup_restores == 0
    assert valve.warmup_log.events().count("restore_deferred") == 1

    valve.run_water(False)
    await clock.advance(DELAY)
    assert valve.cloud.writes == [ALL]
    assert valve.warmup_log.last("restore")["attempt"] == 1


@pytest.mark.asyncio
async def test_a_shower_resumed_before_the_delay_ends_is_waited_out_again(make, clock):
    """Guards the fix: the running flag flickers as a shower winds down."""
    valve = make(ALL)
    valve.run_water()
    valve.announce(OFF)
    await clock.advance(DELAY)
    valve.run_water(False)
    await clock.advance(DELAY / 2)
    valve.run_water()
    await clock.advance(DELAY * 2)
    assert valve.cloud.writes == []

    valve.run_water(False)
    await clock.advance(DELAY)
    assert valve.cloud.writes == [ALL]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("during", "reason"),
    [
        ("reenable", "re-enabled during the wait"),
        ("switch_off", "switched off during the wait"),
    ],
)
async def test_after_the_shower_every_check_is_made_again(make, clock, during, reason):
    """Guards the fix: a wait that long can see the mode or the switch change."""
    valve = make(ALL)
    valve.run_water()
    valve.announce(OFF)
    await clock.advance(DELAY)
    if during == "reenable":
        valve.announce(SELECTED)
    else:
        valve.options[CONF_WARMUP_AUTO_RESTORE] = False
    valve.run_water(False)
    await clock.advance(DELAY)
    assert valve.cloud.writes == []
    assert valve.warmup_log.last("restore_skipped")["reason"] == reason


@pytest.mark.asyncio
async def test_a_deferred_restore_dies_with_an_unload_and_stops_listening(make, clock):
    """Still the one tracked restore task, so `Valve.stop` reaches it."""
    valve = make(ALL)
    valve.run_water()
    valve.announce(OFF)
    await clock.advance(DELAY)
    restore = valve.warmup._warmup_restore_task
    assert restore in valve.tasks
    assert len(valve.listeners) == 1

    valve.stop()
    valve.warmup.reset_restore_task()
    await _settle()

    assert restore.cancelled()
    assert valve.listeners == []
    valve.run_water(False)
    await clock.advance(DELAY * 2)
    assert valve.cloud.writes == []


@pytest.mark.asyncio
async def test_off_chosen_here_during_the_wait_is_not_overridden(make, clock):
    """The hub disables warm-up; inside the minute, the owner picks Off on purpose.

    The mode still read disabled when the wait ended, so the restore wrote the old mode
    straight over that choice.
    """
    valve = make(ALL)
    valve.announce(OFF)
    await clock.advance(DELAY / 2)
    await valve.warmup.async_set_warmup(OFF)
    await clock.advance(DELAY)

    assert valve.cloud.writes == [OFF]
    assert valve.warmup_log.last("restore_skipped") == {
        "reason": "Off chosen here during the wait"
    }


# =========================================================================== #
# 7. The journals never write, flush or wait on a lock from the event loop
# =========================================================================== #
def _returns_while_locked(lock: threading.Lock, call) -> bool:
    """Whether `call` finishes while another thread holds `lock`."""
    with lock:
        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        worker.join(timeout=2)
        finished = not worker.is_alive()
    worker.join(timeout=2)
    return finished


def _journal(directory, **kwargs) -> DebugJournal:
    return DebugJournal(
        str(directory),
        forced=True,
        prefix="warmup",
        readme=WARMUP_README,
        readme_fields={"before": 120, "after": 45},
        label="Warmup journal",
        **kwargs,
    )


def _lines(path) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def test_a_journal_note_with_a_file_open_neither_writes_nor_waits(tmp_path):
    """It wrote and flushed on the loop, under a lock an executor could be holding."""
    journal = _journal(tmp_path)
    journal.prepare()
    path = journal.path

    assert _returns_while_locked(
        journal._lock, lambda: journal.note("disabled", window=["x"] * 400)
    )
    assert _lines(path) == []
    assert journal.wants_open is True

    journal.prepare()  # the executor job the warm-up manager schedules
    assert len(_lines(path)) == 1


def test_queued_journal_records_keep_their_order_whichever_thread_writes(tmp_path):
    """Guards the fix: one `prepare()` per note, run on whichever worker is free."""
    journal = _journal(tmp_path)
    journal.prepare()
    path = journal.path
    stop = threading.Event()

    def worker() -> None:
        while not stop.is_set():
            journal.prepare()

    workers = [threading.Thread(target=worker) for _ in range(4)]
    for thread in workers:
        thread.start()
    for n in range(150):
        journal.note("announced", n=n)
    stop.set()
    for thread in workers:
        thread.join()
    journal.prepare()

    assert [json.loads(line)["n"] for line in _lines(path)] == list(range(150))


def test_closing_the_journal_at_unload_writes_what_is_queued(tmp_path):
    """Guards the fix: a record noted just before unload is not lost to the queue."""
    journal = _journal(tmp_path)
    journal.prepare()
    path = journal.path
    journal.note("restore_done", mode="warmUpAllOutletsWithNoStartDelay")
    journal.close()
    assert len(_lines(path)) == 1
    assert journal.path is None

    # The executor job that note scheduled, running after the close.
    journal.prepare()
    assert journal.path is None
    assert list(tmp_path.glob("warmup_*.jsonl")) == [tmp_path / path.rsplit("/", 1)[1]]


def test_a_report_decision_never_waits_on_the_lock_paho_holds(tmp_path):
    """Paho's thread holds it while it opens or rolls a part; the loop waited there."""
    log = ReportLog(str(tmp_path))
    log.start()
    path = log.path

    assert _returns_while_locked(
        log._lock, lambda: log.note("warmup", "disabled", {"window": ["x"] * 400})
    )
    assert _lines(path) == []

    log.prepare()
    assert len(_lines(path)) == 1
    log.close()


def test_a_message_after_a_queued_decision_lands_after_it(tmp_path):
    """Guards the fix: one file, in the order things happened, whoever writes first."""
    log = ReportLog(str(tmp_path))
    log.start()
    log.note("warmup", "disabled", {"ours": False})
    log.write(TOPIC, b'{"n":1}')  # paho, before the executor got to the decision
    log.prepare()
    log.close()

    lines = [json.loads(line) for line in _lines(next(tmp_path.glob("*.jsonl")))]
    assert [line.get("event") or line["payload"] for line in lines] == [
        "disabled",
        '{"n":1}',
    ]


def test_nothing_reopens_a_report_after_close(tmp_path):
    """A note after unload reopened the episode — a leaked handle, and a second writer.

    The reloaded coordinator resumes the same file, so the old object writing to it too
    interleaves two writers on one file.
    """
    log = ReportLog(str(tmp_path))
    stem = log.start()
    log.note("warmup", "mode", {"after": "warmUpDisabled"})
    log.close()  # unload, with a decision still queued
    first = tmp_path / f"{stem}.jsonl"
    assert len(_lines(first)) == 1, "written into the open part on the way out"

    log.note("warmup", "disabled", {"ours": False})
    assert log.wants_open is False
    log.prepare()  # one an earlier note scheduled, running late
    log.write(TOPIC, b"{}")

    assert log.path is None
    assert log._handle is None
    assert len(_lines(first)) == 1

    # An explicit resume, as the reloaded coordinator's own object does, still works.
    log.resume(stem)
    log.write(TOPIC, b"{}")
    assert len(_lines(first)) == 2
    log.close()
