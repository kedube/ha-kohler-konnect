"""Warm-up: writing the mode, reading it back, putting it back, and keeping a shower on.

Three modules, tested against what their docstrings say they do:

* `konnect/warmup.py` — the pure judgements: is this disable ours to undo, what goes back,
  and what the journal records. Each exists because a bug once lived in the gap between
  the coordinator's behaviour and its own description of it.
* `konnect/warmup_resume.py` — the "No pausing warm-up" decision, report by report.
* `warmup_manager.py` — the side effects: the write and its read-back, auto-restore's
  delay-and-recheck, the give-up counter, and the journal.

The manager is driven through a stand-in valve built around the **real** `GcsState`, and a
virtual clock that stands in for both `time.monotonic` and `asyncio.sleep` inside that one
module — so "waits 60 s" is asserted exactly, without a test paying for it.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from homeassistant.exceptions import HomeAssistantError

from custom_components.kohler_konnect import warmup_manager
from custom_components.kohler_konnect.const import (
    CONF_LAST_WARMUP_MODE,
    CONF_WARMUP_AUTO_RESTORE,
    VALVE_OFFLINE,
    WARMUP_AUTO_RESTORE_DELAY_SECONDS,
    WARMUP_AUTO_RESTORE_MAX_CONSECUTIVE,
    WARMUP_AUTO_RESTORE_SETTLED_SECONDS,
    WARMUP_CONTEXT_AFTER_SECONDS,
    WARMUP_CONTEXT_BEFORE_SECONDS,
    WARMUP_READBACK_DELAYS,
    WARMUP_SELF_WRITE_GRACE_SECONDS,
)
from custom_components.kohler_konnect.konnect import (
    AuthError,
    AuthUnavailable,
    DeviceOffline,
    KohlerError,
)
from custom_components.kohler_konnect.konnect.const import (
    WARMUP_ALL_OUTLETS,
    WARMUP_ALL_OUTLETS_NOW,
    WARMUP_DISABLED,
    WARMUP_MODES,
    WARMUP_SELECTED_OUTLETS_NOW,
)
from custom_components.kohler_konnect.konnect.models import get_valve_model
from custom_components.kohler_konnect.konnect.state import GcsState
from custom_components.kohler_konnect.konnect.valve_hex import ValveWord
from custom_components.kohler_konnect.konnect.warmup import (
    journal_event,
    restore_target,
    should_restore_warmup,
)
from custom_components.kohler_konnect.konnect.warmup_resume import (
    RESUME_DEADLINE_SECONDS,
    SETTLE_SECONDS,
    WARMUP_WINDOW_SECONDS,
    Decision,
    WarmupResume,
)
from custom_components.kohler_konnect.warmup_manager import WarmupManager

ALL = WARMUP_ALL_OUTLETS_NOW
SELECTED = WARMUP_SELECTED_OUTLETS_NOW
OFF = WARMUP_DISABLED
GRACE = WARMUP_SELF_WRITE_GRACE_SECONDS
DELAY = WARMUP_AUTO_RESTORE_DELAY_SECONDS


# =========================================================================== #
# konnect/warmup.py — should_restore_warmup
# =========================================================================== #


def _should(
    before,
    after,
    *,
    enabled=True,
    self_write_mode=None,
    self_write_age=None,
):
    return should_restore_warmup(
        before,
        after,
        enabled=enabled,
        self_write_mode=self_write_mode,
        self_write_age=self_write_age,
        grace_seconds=GRACE,
    )


@pytest.mark.parametrize("before", [ALL, SELECTED, WARMUP_ALL_OUTLETS])
def test_a_disable_out_of_a_watched_enabled_mode_is_restored(before):
    """The one case auto-restore exists for: an enabled mode taken away by someone else."""
    assert _should(before, OFF)


def test_nothing_is_restored_with_the_switch_off():
    """Auto-restore is opt-in; with it off, a disable is somebody's choice to respect."""
    assert not _should(ALL, OFF, enabled=False)


@pytest.mark.parametrize("after", [ALL, SELECTED, WARMUP_ALL_OUTLETS, None])
def test_a_change_that_is_not_a_disable_is_not_our_business(after):
    """Somebody choosing a different enabled mode is setting a mode, not breaking one."""
    assert not _should(OFF, after)
    assert not _should(SELECTED, after)


def test_a_restatement_of_disabled_after_a_reboot_is_not_a_fresh_disable():
    """The valve restates its mode ~4 s after every boot; 25 reboots must not be 25 restores."""
    assert not _should(OFF, OFF)


def test_the_first_mode_ever_seen_being_off_is_not_something_being_disabled():
    """Arriving to find warm-up off and turning it on would be enabling it unasked."""
    assert not _should(None, OFF)


@pytest.mark.parametrize("age", [0.0, 3.4, GRACE])
def test_the_echo_of_our_own_off_write_is_never_undone(age):
    """Choosing `Off` on the dropdown must stick, or `Off` could never be selected."""
    assert not _should(ALL, OFF, self_write_mode=OFF, self_write_age=age)


def test_a_disable_long_after_our_own_off_write_is_restored():
    """The grace covers the echo, not every disable for the rest of the session."""
    assert _should(ALL, OFF, self_write_mode=OFF, self_write_age=GRACE + 0.1)


def test_our_recent_write_of_an_enabled_mode_does_not_excuse_a_disable():
    """The grace is scoped to the mode written: switching to `All` says nothing about `Off`."""
    assert _should(ALL, OFF, self_write_mode=SELECTED, self_write_age=1.0)


# =========================================================================== #
# konnect/warmup.py — restore_target
# =========================================================================== #


def test_the_mode_taken_away_wins_over_the_remembered_one():
    """It is from the same announcement being acted on, so it cannot be stale."""
    assert restore_target(ALL, SELECTED) == ALL


@pytest.mark.parametrize("taken_away", [None, "", OFF])
def test_the_remembered_mode_is_used_only_when_nothing_was_taken_away(taken_away):
    assert restore_target(taken_away, SELECTED) == SELECTED


@pytest.mark.parametrize("remembered", [None, "", OFF])
def test_with_no_enabled_mode_known_there_is_no_target(remembered):
    """`None` rather than a default: "all outlets" and "selected outlets" differ in water."""
    assert restore_target(None, remembered) is None
    assert restore_target(OFF, remembered) is None


def test_whenever_a_restore_is_decided_there_is_a_mode_to_restore_to():
    """The invariant behind the seven-hour unrestored disable of 2026-08-20.

    The decision proved an enabled mode existed and the target lookup then ignored it.
    Checked over every combination of modes either side, remembered modes, and self-write
    state — not only the one that failed.
    """
    modes = [None, *WARMUP_MODES]
    for before, after, remembered, written, age in itertools.product(
        modes, modes, modes, modes, (None, 1.0, GRACE + 1)
    ):
        if _should(before, after, self_write_mode=written, self_write_age=age):
            target = restore_target(before, remembered)
            assert target is not None and target != OFF, (before, after, remembered)


# =========================================================================== #
# konnect/warmup.py — journal_event
# =========================================================================== #


@pytest.mark.parametrize("announced", [True, False])
@pytest.mark.parametrize(("before", "after"), [(ALL, OFF), (OFF, ALL), (None, OFF)])
def test_a_mode_that_moved_is_always_recorded(before, after, announced):
    assert journal_event(before, after, announced=announced) == "mode"


def test_a_restated_mode_is_recorded_when_the_valve_announced_it():
    """28 of the 43 announcements in the corpus restate the value before them."""
    assert journal_event(ALL, ALL, announced=True) == "announced"
    assert journal_event(OFF, OFF, announced=True) == "announced"


def test_an_ordinary_message_that_did_not_touch_warmup_is_silent():
    """The coordinator asks about every valve message; almost none are about warm-up."""
    assert journal_event(ALL, ALL, announced=False) is None
    assert journal_event(ALL, None, announced=True) is None
    assert journal_event(None, None, announced=False) is None


def test_an_announcement_carrying_a_mode_is_never_silently_dropped():
    """The invariant the docstring states, over every pair of modes."""
    modes = [None, *WARMUP_MODES]
    for before, after in itertools.product(modes, WARMUP_MODES):
        assert journal_event(before, after, announced=True) is not None


# =========================================================================== #
# konnect/warmup_resume.py — WarmupResume
# =========================================================================== #
#
# Masks below are the reference install's warm-up set: zone 1 outlets 1–3 (0x07) and zone 2
# outlets 1–2 (0x03). `observe(now, warmup_in_progress, paused, masks)`.

T0 = 1000.0
WARM_MASKS = [0x07, 0x03]
UNPAUSED = [False, False]


def _watch() -> WarmupResume:
    return WarmupResume(T0)


def _warming(watch: WarmupResume, at: float, masks=WARM_MASKS):
    return watch.observe(T0 + at, True, UNPAUSED, masks)


def test_resume_is_decided_when_the_warmup_ends_in_its_pause():
    """The one case it acts on: its own write's warm-up, then the valve's two-minute pause."""
    watch = _watch()
    assert watch.observe(T0 + 1.5, None, UNPAUSED, [0, 0]).decision is Decision.WAIT
    assert _warming(watch, 2.0).decision is Decision.WAIT
    assert watch.warmup_seen
    assert _warming(watch, 40.0).decision is Decision.WAIT
    outcome = watch.observe(T0 + 45.0, False, [True, True], [0, 0])
    assert outcome.decision is Decision.RESUME
    assert outcome.terminal
    assert "43 s after it began" in outcome.reason


def test_a_pause_on_one_zone_is_enough():
    """The pause bit appeared on at least one zone in 30 of 31 natural endings."""
    watch = _watch()
    _warming(watch, 2.0)
    assert (
        watch.observe(T0 + 30.0, False, [False, True], [0x07, 0]).decision
        is Decision.RESUME
    )


def test_a_decision_once_made_does_not_change():
    """Terminal decisions are sticky, so the resend can only be decided once."""
    watch = _watch()
    _warming(watch, 2.0)
    first = watch.observe(T0 + 30.0, False, [True, True], [0, 0])
    assert first.decision is Decision.RESUME
    # A later warm-up, a stop, a person at the wall: none of it re-opens the question.
    assert _warming(watch, 60.0) is first
    assert watch.observe(T0 + 61.0, False, UNPAUSED, [0, 0]) is first
    assert watch.outcome is first


def test_with_no_warmup_inside_the_window_the_shower_is_left_as_written():
    """Warm-up disabled, or the water was already warm: nothing to resume."""
    watch = _watch()
    assert watch.observe(T0 + 2.0, False, UNPAUSED, [0x01, 0]).decision is (
        Decision.WAIT
    )
    assert (
        watch.observe(
            T0 + WARMUP_WINDOW_SECONDS - 0.1, False, UNPAUSED, [0x01, 0]
        ).decision
        is Decision.WAIT
    )
    outcome = watch.observe(T0 + WARMUP_WINDOW_SECONDS, False, UNPAUSED, [0x01, 0])
    assert outcome.decision is Decision.NO_WARMUP
    assert not watch.warmup_seen


def test_a_valve_that_never_reported_warmup_status_counts_as_not_warming():
    """`None` is "never reported", which must not be read as a warm-up in progress."""
    watch = _watch()
    outcome = watch.observe(T0 + WARMUP_WINDOW_SECONDS, None, UNPAUSED, [0x01, 0])
    assert outcome.decision is Decision.NO_WARMUP


def test_a_pause_before_any_warmup_is_not_mistaken_for_the_warmups_pause():
    """Resume needs the warm-up to have been seen; a pause on its own is someone else's."""
    watch = _watch()
    assert watch.observe(T0 + 1.0, False, [True, True], [0, 0]).decision is (
        Decision.WAIT
    )
    assert (
        watch.observe(T0 + WARMUP_WINDOW_SECONDS, False, [True, True], [0, 0]).decision
        is Decision.NO_WARMUP
    )


def test_a_warmup_that_ends_in_a_plain_stop_is_abandoned():
    """Masks 0x00 with no pause bit: somebody stopped the shower, so it stays stopped."""
    watch = _watch()
    _warming(watch, 2.0)
    outcome = watch.observe(T0 + 20.0, False, UNPAUSED, [0, 0])
    assert outcome.decision is Decision.ABANDON
    assert "plain stop" in outcome.reason


def test_outlets_picked_at_the_wall_after_the_warmup_are_left_alone():
    """Outlets other than the warm-up's own, unpaused, are a person driving the shower."""
    watch = _watch()
    _warming(watch, 2.0)
    outcome = watch.observe(T0 + 20.0, False, UNPAUSED, [0x02, 0])
    assert outcome.decision is Decision.ABANDON
    assert "picked them at the wall" in outcome.reason


def test_a_later_pause_by_the_person_who_took_over_is_not_resumed():
    """The "changed hands" rule: once a person has picked outlets, their pause is theirs."""
    watch = _watch()
    _warming(watch, 2.0)
    watch.observe(T0 + 20.0, False, UNPAUSED, [0x02, 0])
    outcome = watch.observe(T0 + 21.0, False, [True, True], [0, 0])
    assert outcome.decision is Decision.ABANDON


def test_a_transitional_report_before_the_pause_is_waited_through():
    """5 of 31 endings showed the warm-up set with status cleared 0.1–0.2 s before the pause."""
    watch = _watch()
    _warming(watch, 2.0)
    transitional = watch.observe(T0 + 30.0, False, UNPAUSED, WARM_MASKS)
    assert transitional.decision is Decision.WAIT
    assert not transitional.terminal
    assert (
        watch.observe(T0 + 30.2, False, [True, True], [0, 0]).decision
        is Decision.RESUME
    )


def test_the_warmups_masks_are_the_last_ones_it_reported():
    """Zone 2's half of the set can arrive a report later; that is still the warm-up's own."""
    watch = _watch()
    _warming(watch, 2.0, masks=[0x07, 0x00])
    _warming(watch, 2.1, masks=[0x07, 0x03])
    assert (
        watch.observe(T0 + 30.0, False, UNPAUSED, [0x07, 0x03]).decision
        is Decision.WAIT
    )


def test_the_warmups_own_outlets_running_on_past_the_settle_window_are_abandoned():
    """Open outlets with no pause for seconds means the shower is being driven by hand."""
    watch = _watch()
    _warming(watch, 2.0)
    watch.observe(T0 + 30.0, False, UNPAUSED, WARM_MASKS)
    assert (
        watch.observe(T0 + 30.0 + SETTLE_SECONDS, False, UNPAUSED, WARM_MASKS).decision
        is Decision.WAIT
    )
    outcome = watch.observe(
        T0 + 30.0 + SETTLE_SECONDS + 0.1, False, UNPAUSED, WARM_MASKS
    )
    assert outcome.decision is Decision.ABANDON
    assert "took over at the wall" in outcome.reason


def test_a_warmup_that_starts_again_restarts_the_settle_clock():
    """The settle window runs from the warm-up's last end, not from a flicker before it."""
    watch = _watch()
    _warming(watch, 2.0)
    watch.observe(T0 + 10.0, False, UNPAUSED, WARM_MASKS)
    _warming(watch, 11.0)
    # 10 s after the first end, but only 1 s after the second: still settling.
    assert (
        watch.observe(T0 + 20.0, False, UNPAUSED, WARM_MASKS).decision is Decision.WAIT
    )
    assert (
        watch.observe(T0 + 20.1, False, [True, True], [0, 0]).decision
        is Decision.RESUME
    )


def test_a_warmup_still_running_past_the_deadline_is_not_acted_on():
    """Corpus maximum is 69 s; at three minutes something else is going on."""
    watch = _watch()
    _warming(watch, 2.0)
    assert _warming(watch, RESUME_DEADLINE_SECONDS).decision is Decision.WAIT
    outcome = _warming(watch, RESUME_DEADLINE_SECONDS + 1)
    assert outcome.decision is Decision.ABANDON
    assert "still running" in outcome.reason
    # Sticky: the pause that eventually follows is not resumed.
    assert watch.observe(T0 + 200.0, False, [True, True], [0, 0]) is outcome


def test_a_warmup_ending_after_the_deadline_without_a_pause_is_abandoned():
    """Even inside the settle window, a warm-up that ends past the deadline is not resumed."""
    watch = _watch()
    _warming(watch, RESUME_DEADLINE_SECONDS - 1)
    outcome = watch.observe(
        T0 + RESUME_DEADLINE_SECONDS + 1, False, UNPAUSED, WARM_MASKS
    )
    assert outcome.decision is Decision.ABANDON
    assert "no pause" in outcome.reason


def test_a_single_zone_valve_is_judged_on_its_one_zone():
    """`paused` and `masks` carry one entry per zone the model has."""
    watch = _watch()
    watch.observe(T0 + 2.0, True, [False], [0x07])
    assert watch.observe(T0 + 30.0, False, [True], [0]).decision is Decision.RESUME


# =========================================================================== #
# warmup_manager.py — harness
# =========================================================================== #


async def _settle() -> None:
    """Let every runnable task run until it blocks again."""
    for _ in range(50):
        await asyncio.sleep(0)


class Clock:
    """`time.monotonic` and `asyncio.sleep` for `warmup_manager`, moved only by the test."""

    def __init__(self) -> None:
        self.now = 50_000.0
        self.sleeps: list[float] = []
        self._sleepers: list[tuple[float, int, asyncio.Future]] = []
        self._order = itertools.count()

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        future = asyncio.get_running_loop().create_future()
        self._sleepers.append((self.now + delay, next(self._order), future))
        await future

    async def advance(self, seconds: float) -> None:
        """Move time forward, waking each sleeper at its own deadline, in order."""
        target = self.now + seconds
        await _settle()
        while True:
            due = sorted(s for s in self._sleepers if s[0] <= target)
            if not due:
                break
            sleeper = due[0]
            self._sleepers.remove(sleeper)
            self.now = max(self.now, sleeper[0])
            if not sleeper[2].done():
                sleeper[2].set_result(None)
            await _settle()
        self.now = target
        await _settle()


class FakeCloud:
    """Kohler's cloud and the valve behind it, as far as the warm-up mode goes.

    Plays both `GcsDevice.async_set_warmup` and `KohlerClient.async_get_gcs_state`. A write
    lands on the valve unless `ignore_writes` (warm-up disabled on the fixture itself);
    `stale_reads` makes that many read-backs still report the old mode, as the cloud did
    for ~3 s after a POST on 2026-08-20.
    """

    def __init__(self, mode: str | None) -> None:
        self.mode = mode
        self.writes: list[str] = []
        self.reads = 0
        self.ignore_writes = False
        self.stale_reads = 0
        self._stale_mode: str | None = None
        self.write_error: Exception | None = None
        self.read_error: Exception | None = None
        self.omit_warmup = False
        # Called with the mode while the POST is "in flight", to land an echo there.
        self.during_write = None

    async def async_set_warmup(self, mode: str) -> None:
        self.writes.append(mode)
        if self.during_write is not None:
            self.during_write(mode)
        if self.write_error is not None:
            raise self.write_error
        self._stale_mode = self.mode
        if not self.ignore_writes:
            self.mode = mode

    async def async_get_gcs_state(self, device_id: str) -> dict[str, Any]:
        self.reads += 1
        if self.read_error is not None:
            raise self.read_error
        mode = self.mode
        if self.stale_reads:
            self.stale_reads -= 1
            mode = self._stale_mode
        state: dict[str, Any] = {"totalVolume": "1"}
        if not self.omit_warmup:
            state["warmUpState"] = {"warmUp": mode, "state": "warmUpNotInProgress"}
        return {"state": state, "connectionState": "Connected"}


class FakeJournal:
    """Records what would be written to the warm-up journal or a Report Log."""

    def __init__(self, wants_open: bool = False) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []
        self.wants_open = wants_open

    def note(self, event: str, **fields: Any) -> None:
        self.records.append((event, fields))

    def prepare(self) -> None:  # pragma: no cover - only handed to the executor
        pass

    def events(self) -> list[str]:
        return [event for event, _ in self.records]

    def last(self, event: str) -> dict[str, Any]:
        return next(f for e, f in reversed(self.records) if e == event)


class FakeReportLog:
    def __init__(self, wants_open: bool = False) -> None:
        self.notes: list[tuple[str, str, dict[str, Any]]] = []
        self.wants_open = wants_open

    def note(self, journal: str, event: str, fields: dict[str, Any]) -> None:
        self.notes.append((journal, event, fields))

    def prepare(self) -> None:  # pragma: no cover - only handed to the executor
        pass


class FakeValve:
    """Enough of `coordinator.Valve` for `WarmupManager`, around a real `GcsState`."""

    def __init__(
        self,
        cloud: FakeCloud,
        *,
        auto_restore: bool = True,
        last_mode: str | None = None,
        device_id: str = "gcs-test0001",
        tag: str | None = None,
    ) -> None:
        from custom_components.kohler_konnect.coordinator import (
            KohlerKonnectCoordinator,
        )

        self.device_id = device_id
        self.tag = tag
        self.gcs_device = SimpleNamespace(device_id=device_id)
        self.gcs_state = GcsState(get_valve_model("K-28210"))
        self.gcs_state.warmup_mode = cloud.mode
        self.gcs = cloud
        self.client = cloud
        self.cloud_payloads: list[tuple[Any, str]] = []
        self.cloud_watch = SimpleNamespace(
            note_rest_payload=lambda payload, why: self.cloud_payloads.append(
                (payload, why)
            )
        )
        self.options: dict[str, Any] = {CONF_WARMUP_AUTO_RESTORE: auto_restore}
        if last_mode is not None:
            self.options[CONF_LAST_WARMUP_MODE] = last_mode
        self.option_writes: list[tuple[str, Any]] = []
        self.warmup_log: FakeJournal | None = FakeJournal()
        self.executor_jobs: list[Any] = []
        self.hass = SimpleNamespace(async_add_executor_job=self.executor_jobs.append)
        self.tasks: set[asyncio.Task] = set()
        self.auth_errors: list[Exception] = []
        self.refreshes = 0
        coordinator = SimpleNamespace(
            valves=[self],
            _recent_messages=[],
            report_log=None,
            async_refresh_entities=self._refresh,
            _handle_auth_error=self.auth_errors.append,
        )
        # The real error translation, so a failed write reads as the user would see it.
        coordinator.command_errors = lambda offline: (
            KohlerKonnectCoordinator.command_errors(coordinator, offline)
        )
        self.coordinator = coordinator
        self.warmup = WarmupManager(self)

    def _refresh(self) -> None:
        self.refreshes += 1

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def set_option(self, key: str, value: Any) -> None:
        self.option_writes.append((key, value))
        self.options[key] = value

    def _tagged(self, fields: dict[str, Any]) -> dict[str, Any]:
        return fields if self.tag is None else {"valve": self.tag, **fields}

    def _track(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    # -- what the valve hears ------------------------------------------------ #
    def announce(self, mode: str) -> None:
        """The valve volunteering its mode (`GCS_WARM_STS`), as `handle_envelope` applies it."""
        was = self.gcs_state.warmup_mode
        self.gcs_state.warmup_mode = mode
        self.cloud.mode = mode
        self.warmup.handle_mode_change(was, mode, announced=True)

    def reseed(self, mode: str | None) -> None:
        """A REST reseed on reconnect, as `Valve.async_seed` reports it."""
        was = self.gcs_state.warmup_mode
        if mode is not None:
            self.gcs_state.warmup_mode = mode
            self.cloud.mode = mode
        self.warmup.note_seeded_mode(was, self.gcs_state.warmup_mode)

    def run_water(self, running: bool = True) -> None:
        self.gcs_state.valve1 = ValveWord(
            prefix=1,
            temperature_celsius=40.0,
            flow_percent=100.0,
            outlet_mask=0x01 if running else 0,
            paused=False,
        )

    @property
    def cloud(self) -> FakeCloud:
        return self.gcs

    def stop(self) -> None:
        for task in list(self.tasks):
            task.cancel()


@pytest.fixture
def clock(monkeypatch) -> Clock:
    """Virtual time for `warmup_manager` only — the event loop keeps its real clock."""
    clock = Clock()
    monkeypatch.setattr(
        warmup_manager, "time", SimpleNamespace(monotonic=clock.monotonic)
    )
    monkeypatch.setattr(warmup_manager, "asyncio", SimpleNamespace(sleep=clock.sleep))
    return clock


@pytest_asyncio.fixture
async def make(clock):
    """Build stand-in valves, and cancel whatever they left sleeping."""
    made: list[FakeValve] = []

    def _make(mode: str | None = ALL, **kwargs: Any) -> FakeValve:
        valve = FakeValve(FakeCloud(mode), **kwargs)
        made.append(valve)
        return valve

    yield _make
    for valve in made:
        valve.stop()
    await _settle()


# =========================================================================== #
# warmup_manager.py — settings
# =========================================================================== #


def test_auto_restore_is_off_unless_explicitly_enabled():
    valve = FakeValve(FakeCloud(ALL))
    valve.options.clear()
    assert valve.warmup.auto_restore is False
    valve.options[CONF_WARMUP_AUTO_RESTORE] = True
    assert valve.warmup.auto_restore is True


@pytest.mark.parametrize(
    ("stored", "expected"),
    [(None, None), (ALL, ALL), (SELECTED, SELECTED), (WARMUP_ALL_OUTLETS, None)],
)
def test_the_remembered_mode_is_only_one_this_integration_could_write(stored, expected):
    """A legacy delayed-start value is not offered back: nothing knows what its delay does."""
    valve = FakeValve(FakeCloud(ALL), last_mode=stored)
    assert valve.warmup.last_mode == expected


# =========================================================================== #
# warmup_manager.py — writing the mode, and reading it back
# =========================================================================== #


@pytest.mark.asyncio
async def test_a_write_confirmed_by_the_first_read_back_returns_at_once(make, clock):
    """The usual case: the valve agrees, the mode is remembered, and nothing sleeps."""
    valve = make(OFF, auto_restore=False)
    await valve.warmup.async_set_warmup(SELECTED)
    assert valve.cloud.writes == [SELECTED]
    assert valve.cloud.reads == 1
    assert clock.sleeps == []
    assert valve.gcs_state.warmup_mode == SELECTED
    assert valve.options[CONF_LAST_WARMUP_MODE] == SELECTED
    # Same notification path as a device push, so the dropdown lands on the real value.
    assert valve.refreshes == 1
    # And the read-back's connection state is not thrown away.
    assert [why for _, why in valve.cloud_payloads] == ["warmup read-back"]


@pytest.mark.asyncio
async def test_a_read_back_that_lags_the_write_is_retried_not_reported(make, clock):
    """Measured live: `gcs-state` returned the OLD mode right after a successful POST."""
    valve = make(OFF, auto_restore=False)
    valve.cloud.stale_reads = 1
    task = asyncio.ensure_future(valve.warmup.async_set_warmup(ALL))
    await clock.advance(WARMUP_READBACK_DELAYS[1])
    await task
    assert valve.cloud.reads == 2
    assert clock.sleeps == [WARMUP_READBACK_DELAYS[1]]
    assert valve.options[CONF_LAST_WARMUP_MODE] == ALL


@pytest.mark.asyncio
async def test_a_write_the_valve_ignores_is_reported_and_not_retried(
    make, clock, caplog
):
    """Disabled on the fixture: the cloud accepts, the valve ignores, and the owner is told."""
    valve = make(OFF, auto_restore=False)
    valve.cloud.ignore_writes = True
    task = asyncio.ensure_future(valve.warmup.async_set_warmup(ALL))
    with caplog.at_level(logging.WARNING, logger=warmup_manager.__name__):
        await clock.advance(sum(WARMUP_READBACK_DELAYS))
        await task
    assert valve.cloud.writes == [ALL]
    assert valve.cloud.reads == len(WARMUP_READBACK_DELAYS)
    assert clock.sleeps == [d for d in WARMUP_READBACK_DELAYS if d]
    assert "did not apply it" in caplog.text
    assert valve.gcs_state.warmup_mode == OFF
    # Never remembered: the valve did not take it, so it is not what to restore to.
    assert CONF_LAST_WARMUP_MODE not in valve.options


@pytest.mark.asyncio
async def test_a_read_back_that_cannot_answer_is_not_mistaken_for_a_mismatch(
    make, clock, caplog
):
    """ "Off" and "we could not tell" are different answers; only one is a warning."""
    valve = make(None, auto_restore=False)
    valve.cloud.read_error = KohlerError("gateway timeout")
    task = asyncio.ensure_future(valve.warmup.async_set_warmup(ALL))
    with caplog.at_level(logging.INFO, logger=warmup_manager.__name__):
        await clock.advance(sum(WARMUP_READBACK_DELAYS))
        await task
    assert valve.cloud.writes == [ALL]
    assert "read-back did not answer" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert CONF_LAST_WARMUP_MODE not in valve.options


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [WARMUP_ALL_OUTLETS, "warmUp", "", "Off"])
async def test_a_mode_this_integration_does_not_write_is_refused_before_sending(
    make, mode
):
    """The legacy delayed-start values may be held, but writing one would be guessing."""
    valve = make(OFF)
    with pytest.raises(HomeAssistantError, match="not a warmup mode"):
        await valve.warmup.async_set_warmup(mode)
    assert valve.cloud.writes == []


@pytest.mark.asyncio
async def test_warmup_cannot_be_changed_while_water_runs(make):
    """The Konnect app blocks this too; whether the device would is unknown."""
    valve = make(ALL)
    valve.run_water()
    with pytest.raises(HomeAssistantError, match="while the shower is running"):
        await valve.warmup.async_set_warmup(OFF)
    assert valve.cloud.writes == []


@pytest.mark.asyncio
async def test_a_refused_off_write_does_not_excuse_the_next_disable(make, clock):
    """Only a write that was sent counts as ours: a refusal must not open the grace."""
    valve = make(ALL)
    valve.run_water()
    with pytest.raises(HomeAssistantError):
        await valve.warmup.async_set_warmup(OFF)
    valve.run_water(False)
    valve.announce(OFF)
    assert valve.warmup_log.last("disabled")["ours"] is False
    assert valve.warmup_log.last("disabled")["restoring"] is True


@pytest.mark.asyncio
async def test_an_offline_valve_says_so_when_the_mode_is_written(make):
    valve = make(OFF)
    valve.cloud.write_error = DeviceOffline("statusCode 900")
    with pytest.raises(HomeAssistantError) as raised:
        await valve.warmup.async_set_warmup(ALL)
    assert str(raised.value) == VALVE_OFFLINE


@pytest.mark.asyncio
async def test_the_echo_of_our_off_landing_mid_post_is_recognised_as_ours(make, clock):
    """Self-write is recorded *before* the call: the echo can beat the POST's response."""
    valve = make(ALL)
    valve.cloud.during_write = lambda mode: valve.announce(mode)
    await valve.warmup.async_set_warmup(OFF)
    disabled = valve.warmup_log.last("disabled")
    assert disabled["ours"] is True
    assert disabled["restoring"] is False
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == [OFF]


@pytest.mark.asyncio
async def test_reading_the_mode_applies_it_and_returns_it(make):
    valve = make(SELECTED)
    valve.gcs_state.warmup_mode = None
    assert await valve.warmup.async_read_warmup_mode() == SELECTED
    assert valve.gcs_state.warmup_mode == SELECTED
    assert valve.refreshes == 1


@pytest.mark.asyncio
async def test_a_failed_read_returns_none_rather_than_a_guess(make):
    valve = make(ALL)
    valve.cloud.read_error = KohlerError("boom")
    assert await valve.warmup.async_read_warmup_mode() is None
    assert valve.auth_errors == []
    assert valve.refreshes == 0


@pytest.mark.asyncio
async def test_a_rejected_sign_in_on_the_read_asks_to_sign_in_again(make):
    """A dead credential is not one more failed read; it needs the user."""
    valve = make(ALL)
    valve.cloud.read_error = AuthError("AADB2C90080: expired")
    assert await valve.warmup.async_read_warmup_mode() is None
    assert valve.auth_errors == [valve.cloud.read_error]


@pytest.mark.asyncio
async def test_an_unreachable_auth_service_on_the_read_does_not_prompt(make):
    """A network blip must not raise reauth cards."""
    valve = make(ALL)
    valve.cloud.read_error = AuthUnavailable("no route")
    assert await valve.warmup.async_read_warmup_mode() is None
    assert valve.auth_errors == []


@pytest.mark.asyncio
async def test_a_read_carrying_no_warmup_field_answers_none(make, clock, caplog):
    """Its docstring: "Returns `None` if the read failed or carried no warmup field."

    The valve announced `warmUpDisabled` over MQTT; the owner picks All Outlets; the cloud
    accepts it; every `gcs-state` read-back omits `warmUpState`. What comes back is the
    cached `warmUpDisabled`, so after six seconds the log claims the valve "did not apply"
    a command nobody has evidence about.
    """
    valve = make(OFF, auto_restore=False)
    valve.cloud.omit_warmup = True
    assert await valve.warmup.async_read_warmup_mode() is None


# =========================================================================== #
# warmup_manager.py — auto-restore
# =========================================================================== #


@pytest.mark.asyncio
async def test_a_disable_nobody_here_caused_is_put_back_after_a_minute(
    make, clock, caplog
):
    """The feature itself: the hub's web UI writes `warmUpDisabled`; this undoes it."""
    valve = make(ALL)
    with caplog.at_level(logging.WARNING, logger=warmup_manager.__name__):
        valve.announce(OFF)
        await clock.advance(DELAY - 0.5)
        assert valve.cloud.writes == []
        await clock.advance(0.5)
    assert valve.cloud.writes == [ALL]
    assert valve.gcs_state.warmup_mode == ALL
    assert DELAY in clock.sleeps
    assert "something other than Home Assistant" in caplog.text

    journal = valve.warmup_log
    disabled = journal.last("disabled")
    assert disabled["before"] == ALL
    assert disabled["ours"] is False
    assert disabled["restoring"] is True
    assert disabled["restores_to"] == ALL
    assert disabled["water_running"] is False
    assert journal.last("restore_scheduled") == {"target": ALL, "delay_seconds": DELAY}
    assert journal.last("restore")["target"] == ALL
    assert journal.last("restore_done")["mode_now"] == ALL


@pytest.mark.asyncio
async def test_the_restore_reinstates_the_mode_taken_away_not_an_older_memory(
    make, clock
):
    """2026-08-20: the remembered mode can be stale or absent; the one taken away is not."""
    valve = make(SELECTED, last_mode=ALL)
    valve.announce(OFF)
    await clock.advance(DELAY)
    assert valve.cloud.writes == [SELECTED]


@pytest.mark.asyncio
async def test_a_mode_read_only_over_rest_is_still_restored(make, clock):
    """The seven-hour failure: the mode was read at setup but never announced."""
    valve = make(None)
    valve.reseed(ALL)
    assert valve.warmup.last_mode == ALL
    valve.announce(OFF)
    await clock.advance(DELAY)
    assert valve.cloud.writes == [ALL]
    assert valve.warmup_log.last("disabled")["restores_to"] == ALL


@pytest.mark.asyncio
async def test_choosing_off_from_the_dropdown_is_not_undone(make, clock):
    """Our own `Off`, its read-back and the valve's echo ~3.4 s later: no restore."""
    valve = make(ALL)
    await valve.warmup.async_set_warmup(OFF)
    await clock.advance(3.4)
    valve.announce(OFF)
    assert valve.warmup_log.last("announced") == {
        "mode": OFF,
        "ours": True,
        "source": "mqtt",
    }
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == [OFF]
    assert "restore_scheduled" not in valve.warmup_log.events()


@pytest.mark.asyncio
async def test_our_off_echoed_as_a_transition_is_not_undone_either(make, clock):
    """When the read-back lags, the echo arrives as a real change — and is still ours."""
    valve = make(ALL)
    valve.cloud.ignore_writes = True  # the cloud has not caught up yet
    write = asyncio.ensure_future(valve.warmup.async_set_warmup(OFF))
    await clock.advance(sum(WARMUP_READBACK_DELAYS))
    await write
    await clock.advance(1.0)
    valve.announce(OFF)
    disabled = valve.warmup_log.last("disabled")
    assert disabled["ours"] is True
    assert disabled["restoring"] is False
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == [OFF]


@pytest.mark.asyncio
async def test_a_disable_after_our_own_off_has_aged_out_is_restored(make, clock):
    """We chose Off, someone re-enabled it, and the hub disabled it a minute later."""
    valve = make(ALL)
    await valve.warmup.async_set_warmup(OFF)
    await clock.advance(10)
    valve.announce(SELECTED)
    await clock.advance(GRACE)
    valve.announce(OFF)
    await clock.advance(DELAY)
    assert valve.cloud.writes == [OFF, SELECTED]


@pytest.mark.asyncio
async def test_our_own_switch_to_another_mode_does_not_excuse_a_disable(make, clock):
    valve = make(ALL)
    await valve.warmup.async_set_warmup(SELECTED)
    await clock.advance(5)
    valve.announce(OFF)
    await clock.advance(DELAY)
    assert valve.cloud.writes == [SELECTED, SELECTED]


@pytest.mark.asyncio
async def test_nothing_is_written_while_the_water_is_running(make, clock, caplog):
    """A shower is exactly when nobody wants this fighting the valve."""
    valve = make(ALL)
    valve.announce(OFF)
    await clock.advance(DELAY / 2)
    valve.run_water()
    with caplog.at_level(logging.WARNING, logger=warmup_manager.__name__):
        await clock.advance(DELAY / 2)
    assert valve.cloud.writes == []
    failed = valve.warmup_log.last("restore_failed")
    assert failed["target"] == ALL
    assert "shower is running" in failed["error"]
    assert "could not write" in caplog.text
    # Not retried on its own: the next disable will schedule another.
    valve.run_water(False)
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == []


@pytest.mark.asyncio
async def test_a_disable_while_water_runs_is_journalled_as_such(make, clock):
    valve = make(ALL)
    valve.run_water()
    valve.announce(OFF)
    assert valve.warmup_log.last("disabled")["water_running"] is True


@pytest.mark.asyncio
async def test_the_first_mode_ever_seen_being_off_is_left_off(make, clock):
    """Needs to have seen the mode enabled: arriving to find it off is not a disable."""
    valve = make(None, last_mode=ALL)
    valve.announce(OFF)
    assert valve.warmup_log.last("disabled")["restoring"] is False
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == []


@pytest.mark.asyncio
async def test_restatements_after_reboots_never_fire_a_restore(make, clock):
    """25 reboots in a week here, each followed by the valve restating `warmUpDisabled`."""
    valve = make(OFF, last_mode=ALL)
    for _ in range(25):
        valve.announce(OFF)
        await clock.advance(4)
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == []
    assert set(valve.warmup_log.events()) == {"announced"}
    assert all(
        f == {"mode": OFF, "ours": False, "source": "mqtt"}
        for _, f in valve.warmup_log.records
    )


@pytest.mark.asyncio
async def test_with_auto_restore_off_a_disable_is_only_journalled(make, clock):
    valve = make(ALL, auto_restore=False)
    valve.announce(OFF)
    disabled = valve.warmup_log.last("disabled")
    assert disabled["restoring"] is False
    assert disabled["auto_restore"] is False
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == []


@pytest.mark.asyncio
async def test_switching_auto_restore_off_during_the_wait_stops_the_write(make, clock):
    """Re-checked at the end of the wait, so the switch takes effect immediately."""
    valve = make(ALL)
    valve.announce(OFF)
    await clock.advance(DELAY / 2)
    valve.options[CONF_WARMUP_AUTO_RESTORE] = False
    await clock.advance(DELAY / 2)
    assert valve.cloud.writes == []
    assert valve.warmup_log.last("restore_skipped") == {
        "reason": "switched off during the wait"
    }


@pytest.mark.asyncio
async def test_a_mode_re_enabled_by_hand_during_the_wait_is_left_alone(
    make, clock, caplog
):
    """Someone got there first — perhaps with a different mode, which must not be undone."""
    valve = make(ALL)
    valve.announce(OFF)
    await clock.advance(DELAY / 2)
    valve.announce(SELECTED)
    with caplog.at_level(logging.INFO, logger=warmup_manager.__name__):
        await clock.advance(DELAY / 2)
    assert valve.cloud.writes == []
    assert valve.warmup_log.last("restore_skipped") == {
        "reason": "re-enabled during the wait",
        "mode_now": SELECTED,
    }
    assert "re-enabled before auto-restore ran" in caplog.text


@pytest.mark.asyncio
async def test_only_one_restore_is_in_flight_at_a_time(make, clock):
    """Two restores would race each other over a single field."""
    valve = make(ALL)
    valve.announce(OFF)
    await clock.advance(10)
    # Re-enabled and disabled again inside the first restore's wait.
    valve.announce(ALL)
    valve.announce(OFF)
    assert valve.warmup_log.last("restore_skipped") == {
        "reason": "a restore is already pending"
    }
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == [ALL]


@pytest.mark.asyncio
async def test_a_pending_restore_puts_back_the_mode_most_recently_taken_away(
    make, clock
):
    """`restore_target`: the mode taken away wins *because it cannot be stale*.

    All Outlets is disabled by a hub web-UI sign-in; within the minute someone picks
    Started Outlets in the Konnect app and a second sign-in disables that too. The second
    disable is folded into the pending restore, which then writes All Outlets — the mode
    the owner moved away from — although both the latest mode taken away and
    `last_warmup_mode` say Started Outlets.
    """
    valve = make(ALL)
    valve.announce(OFF)
    await clock.advance(10)
    valve.announce(SELECTED)
    valve.announce(OFF)
    assert valve.warmup.last_mode == SELECTED
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == [SELECTED]


@pytest.mark.asyncio
async def test_a_legacy_mode_taken_away_is_not_written_back(make, clock):
    """Restoring is still writing, and nothing knows what a delayed start's delay does."""
    valve = make(WARMUP_ALL_OUTLETS)
    valve.announce(OFF)
    await clock.advance(DELAY)
    assert valve.cloud.writes == []
    assert valve.warmup_log.last("restore_failed")["target"] == WARMUP_ALL_OUTLETS


@pytest.mark.asyncio
async def test_a_restore_with_nothing_to_restore_to_says_so_rather_than_guessing(
    make, clock, caplog
):
    """Unreachable from a genuine disable; a future caller passing nothing must not write."""
    valve = make(OFF)
    with caplog.at_level(logging.WARNING, logger=warmup_manager.__name__):
        await valve.warmup._async_restore_warmup(None)
    assert valve.cloud.writes == []
    assert valve.warmup_log.last("restore_skipped") == {
        "reason": "no enabled mode has ever been seen"
    }
    assert "nothing to restore to" in caplog.text


@pytest.mark.asyncio
async def test_after_giving_up_it_skips_warns_once_and_tries_again_once_settled(
    make, clock, caplog
):
    """The gate itself, from the state five unstuck restores leave behind.

    Set directly, so the gate is tested on its own.
    """
    valve = make(OFF)
    manager = valve.warmup
    manager._warmup_restores = WARMUP_AUTO_RESTORE_MAX_CONSECUTIVE
    manager._warmup_restored_at = clock.now
    with caplog.at_level(logging.WARNING, logger=warmup_manager.__name__):
        await manager._async_restore_warmup(ALL)
        await manager._async_restore_warmup(ALL)
    gave_up = [r for r in caplog.records if "keeps being disabled" in r.getMessage()]
    assert len(gave_up) == 1
    assert valve.warmup_log.last("restore_skipped") == {
        "reason": "gave up after %d restores that did not stick"
        % WARMUP_AUTO_RESTORE_MAX_CONSECUTIVE
    }
    assert valve.cloud.writes == []

    # Fifteen quiet minutes count as the last restore having stuck.
    await clock.advance(WARMUP_AUTO_RESTORE_SETTLED_SECONDS)
    restore = asyncio.ensure_future(manager._async_restore_warmup(ALL))
    await clock.advance(DELAY)
    await restore
    assert valve.cloud.writes == [ALL]


@pytest.mark.asyncio
async def test_it_stops_after_repeated_restores_that_do_not_stick(make, clock):
    """`const.py`: "Stop after this many consecutive restores that failed to stick."

    Something disables the mode five seconds after every restore — the fight the counter
    exists for. Each restore is confirmed by its read-back before being undone, which counts
    as "the mode staying enabled" and zeroes the counter, so it never climbs past one and
    Kohler's API is hammered once a minute for as long as the fight lasts.
    """
    valve = make(ALL)
    for _ in range(WARMUP_AUTO_RESTORE_MAX_CONSECUTIVE + 2):
        valve.announce(OFF)
        await clock.advance(DELAY)
        await clock.advance(3.4)
        valve.announce(valve.cloud.mode)  # the valve's echo of the restore
        await clock.advance(5)
    assert len(valve.cloud.writes) == WARMUP_AUTO_RESTORE_MAX_CONSECUTIVE


# =========================================================================== #
# warmup_manager.py — modes discovered by the REST reseed
# =========================================================================== #


@pytest.mark.asyncio
async def test_a_disable_found_by_the_reseed_is_restored_like_an_announced_one(
    make, clock
):
    """A sign-in to the hub's web UI while the stream is down lands exactly here."""
    valve = make(ALL)
    valve.reseed(OFF)
    mode = valve.warmup_log.last("mode")
    assert mode == {
        "before": ALL,
        "after": OFF,
        "ours": False,
        "source": "rest",
        "restoring": True,
    }
    await clock.advance(DELAY)
    assert valve.cloud.writes == [ALL]


@pytest.mark.asyncio
async def test_a_reseed_disable_with_auto_restore_off_is_recorded_not_restored(
    make, clock
):
    valve = make(ALL, auto_restore=False)
    valve.reseed(OFF)
    assert valve.warmup_log.last("mode")["restoring"] is False
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == []


@pytest.mark.asyncio
async def test_a_reseed_finding_our_own_off_does_not_restore_it(make, clock):
    """The same self-write grace as the MQTT path."""
    valve = make(ALL)
    valve.cloud.ignore_writes = True
    valve.cloud.read_error = KohlerError("busy")
    write = asyncio.ensure_future(valve.warmup.async_set_warmup(OFF))
    await clock.advance(sum(WARMUP_READBACK_DELAYS))
    await write
    valve.cloud.read_error = None
    valve.reseed(OFF)
    assert valve.warmup_log.last("mode")["ours"] is True
    assert valve.warmup_log.last("mode")["restoring"] is False
    await clock.advance(DELAY * 3)
    assert valve.cloud.writes == [OFF]


@pytest.mark.asyncio
async def test_a_reseed_that_finds_nothing_new_records_nothing(make):
    valve = make(ALL, last_mode=ALL)
    valve.reseed(ALL)
    valve.reseed(None)
    assert valve.warmup_log.records == []
    # Already remembered: no needless entry update (each one is a config write).
    assert valve.option_writes == []


def test_the_reseed_remembers_a_mode_already_in_force_at_startup():
    """Neither MQTT nor our own writes know a mode that was simply already set."""
    valve = FakeValve(FakeCloud(None))
    valve.reseed(SELECTED)
    assert valve.warmup.last_mode == SELECTED
    assert valve.warmup_log.records == []  # no `before`, so not a change


def test_a_reseed_finding_warmup_off_at_startup_remembers_nothing():
    valve = FakeValve(FakeCloud(None))
    valve.reseed(OFF)
    assert valve.warmup.last_mode is None
    assert valve.option_writes == []


# =========================================================================== #
# warmup_manager.py — remembering modes, and the journal
# =========================================================================== #


def test_an_announced_enabled_mode_becomes_the_restore_target():
    valve = FakeValve(FakeCloud(ALL))
    valve.announce(SELECTED)
    assert valve.warmup.last_mode == SELECTED
    assert valve.warmup_log.last("mode") == {
        "before": ALL,
        "after": SELECTED,
        "ours": False,
        "source": "mqtt",
    }


def test_a_restated_enabled_mode_is_journalled_without_rewriting_the_entry():
    valve = FakeValve(FakeCloud(ALL), last_mode=ALL)
    valve.announce(ALL)
    assert valve.warmup_log.events() == ["announced"]
    assert valve.option_writes == []


def test_a_message_that_does_not_touch_warmup_is_not_journalled():
    valve = FakeValve(FakeCloud(ALL))
    valve.warmup.handle_mode_change(ALL, ALL, announced=False)
    assert valve.warmup_log.records == []


@pytest.mark.asyncio
async def test_a_disable_record_carries_the_wire_traffic_either_side(make, clock):
    """`SYSTEM_STS: SYSTEM_READY` landed 7–9 s *after* the disables; the record must hold it.

    This valve's messages and every controller's are evidence; another valve's are not.
    """
    valve = make(ALL)
    other = SimpleNamespace(device_id="gcs-other")
    valve.coordinator.valves.append(other)
    messages = valve.coordinator._recent_messages

    def heard(ago: float, code: str, device: str) -> None:
        messages.append(
            {"at": clock.now - ago, "code": code, "device": device, "sku": "x"}
        )

    heard(WARMUP_CONTEXT_BEFORE_SECONDS + 5, "TOO_OLD", valve.device_id)
    heard(30, "GCS_SOLO_STS", valve.device_id)
    heard(20, "SYSTEM_STS", "hub-1")
    heard(10, "GCS_SOLO_STS", "gcs-other")
    valve.announce(OFF)

    before = valve.warmup_log.last("disabled")["before_window"]
    assert [m["code"] for m in before] == ["GCS_SOLO_STS", "SYSTEM_STS"]
    assert all("at" not in m and "device" not in m for m in before)

    await clock.advance(8)
    messages.append(
        {"at": clock.now, "code": "SYSTEM_READY", "device": "hub-1", "sku": "x"}
    )
    messages.append(
        {"at": clock.now, "code": "GCS_SOLO_STS", "device": "gcs-other", "sku": "x"}
    )
    await clock.advance(WARMUP_CONTEXT_AFTER_SECONDS - 8)
    context = valve.warmup_log.last("context")
    assert [m["code"] for m in context["after_window"]] == ["SYSTEM_READY"]
    assert context["window_seconds"] == WARMUP_CONTEXT_AFTER_SECONDS
    assert context["mode_now"] == OFF
    # The evidence window closes ahead of our own write, so none of it is ours.
    await clock.advance(DELAY - WARMUP_CONTEXT_AFTER_SECONDS)
    events = valve.warmup_log.events()
    assert events.index("context") < events.index("restore")


@pytest.mark.asyncio
async def test_an_unrestored_disable_still_gets_its_after_window(make, clock):
    """The cleaner observation of the two, since nothing of ours is in the way."""
    valve = make(ALL, auto_restore=False)
    valve.announce(OFF)
    await clock.advance(WARMUP_CONTEXT_AFTER_SECONDS)
    assert "context" in valve.warmup_log.events()


def test_decisions_reach_an_active_report_log_even_with_the_journal_off():
    """One switch, one attachment: a report must not depend on the journal's own flag."""
    valve = FakeValve(FakeCloud(ALL), tag="Upstairs")
    valve.warmup_log = None
    report = FakeReportLog(wants_open=True)
    valve.coordinator.report_log = report
    valve.announce(SELECTED)
    assert report.notes == [
        (
            "warmup",
            "mode",
            {
                "valve": "Upstairs",
                "before": ALL,
                "after": SELECTED,
                "ours": False,
                "source": "mqtt",
            },
        )
    ]
    # The file is opened off the event loop.
    assert valve.executor_jobs == [report.prepare]


def test_the_journal_opens_its_file_off_the_event_loop_on_first_use():
    valve = FakeValve(FakeCloud(ALL))
    valve.warmup_log = FakeJournal(wants_open=True)
    valve.announce(SELECTED)
    assert valve.executor_jobs == [valve.warmup_log.prepare]


def test_records_name_the_valve_on_a_multi_valve_account():
    valve = FakeValve(FakeCloud(ALL), tag="Upstairs")
    valve.announce(SELECTED)
    assert valve.warmup_log.last("mode")["valve"] == "Upstairs"


@pytest.mark.asyncio
async def test_a_restore_cancelled_by_an_unload_never_writes(make, clock):
    """A restore surviving an unload would write to the valve from a discarded coordinator.

    The manager starts it through `Valve._track`, which is what lets `Valve.stop` reach it.
    """
    valve = make(ALL)
    valve.announce(OFF)
    restore = valve.warmup._warmup_restore_task
    assert restore in valve.tasks
    valve.stop()
    valve.warmup.reset_restore_task()
    await clock.advance(DELAY * 2)
    assert restore.cancelled()
    assert valve.cloud.writes == []
    # And the next disable, after setup again, schedules its own.
    valve.announce(ALL)
    valve.announce(OFF)
    assert "restore_skipped" not in valve.warmup_log.events()
    await clock.advance(DELAY)
    assert valve.cloud.writes == [ALL]
