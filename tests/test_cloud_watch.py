"""Cloud reachability: when the valve's `connectionState` is read, and what is made of it.

The module's whole design is that **silence only decides when to ask** — the answer always
comes from Kohler. So these tests drive the two triggers and the guards around them against
a fake clock and a fake timer wheel, and check two things at every step: whether a read was
made, and what the sensor now says.

The fakes stand in for `async_call_later`, `time` and `hass.async_create_task` only; the
`CloudConnectionWatch` under test is the real class, wired to a coordinator stand-in that
carries just what it reads.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.kohler_konnect import cloud_watch as cloud_watch_module
from custom_components.kohler_konnect.cloud_watch import CloudConnectionWatch
from custom_components.kohler_konnect.const import (
    CLOUD_CHECK_COOLDOWN_SECONDS as COOLDOWN,
)
from custom_components.kohler_konnect.const import (
    CLOUD_CHECK_PAIR_WINDOW_SECONDS as PAIR,
)
from custom_components.kohler_konnect.const import (
    CLOUD_CHECK_QUIET_SECONDS as QUIET,
)
from custom_components.kohler_konnect.konnect import AuthError, KohlerError
from custom_components.kohler_konnect.konnect.mqtt import Envelope

VALVE_ID = "gcs-test0001"
WALL_START = 1_790_000_000.0


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
@dataclass
class Timer:
    due: float
    action: Any
    cancelled: bool = False


class Clock:
    """`time.monotonic` and `time.time`, moved only by the test."""

    def __init__(self) -> None:
        self.now = 10_000.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return WALL_START + self.now


class Timers:
    """Stands in for `async_call_later`, firing callbacks as the clock passes them."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.pending: list[Timer] = []

    def call_later(self, _hass: Any, delay: float, action: Any):
        timer = Timer(self.clock.now + delay, action)
        self.pending.append(timer)

        def cancel() -> None:
            timer.cancelled = True
            if timer in self.pending:
                self.pending.remove(timer)

        return cancel

    def advance(self, seconds: float) -> None:
        """Move time forward, firing every timer that falls due on the way, in order."""
        target = self.clock.now + seconds
        while due := [t for t in self.pending if t.due <= target]:
            timer = min(due, key=lambda t: t.due)
            self.pending.remove(timer)
            self.clock.now = max(self.clock.now, timer.due)
            timer.action(None)
        self.clock.now = target


class Task:
    """A coroutine `async_create_task` was handed, run only when the test says so."""

    def __init__(self, coro: Any) -> None:
        self.coro = coro
        self.finished = False
        self.cancelled = False

    def done(self) -> bool:
        return self.finished

    def cancel(self) -> None:
        self.coro.close()
        self.cancelled = self.finished = True

    def run(self) -> None:
        asyncio.run(self.coro)
        self.finished = True


class Hass:
    def __init__(self) -> None:
        self.tasks: list[Task] = []

    def async_create_task(self, coro: Any) -> Task:
        task = Task(coro)
        self.tasks.append(task)
        return task


@dataclass
class Client:
    """`async_get_gcs_state`, answering from a queue; Connected when the queue is empty."""

    replies: list[Any] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    async def async_get_gcs_state(self, device_id: str) -> Any:
        self.calls.append(device_id)
        reply = (
            self.replies.pop(0) if self.replies else {"connectionState": "Connected"}
        )
        if isinstance(reply, BaseException):
            raise reply
        return reply


class Rig:
    """One watch, its fakes, and helpers that read like the events they stand for."""

    def __init__(self, monkeypatch, *, controllers: int = 1) -> None:
        self.clock = Clock()
        self.timers = Timers(self.clock)
        monkeypatch.setattr(cloud_watch_module, "time", self.clock)
        monkeypatch.setattr(
            cloud_watch_module, "async_call_later", self.timers.call_later
        )
        self.hass = Hass()
        self.client = Client()
        self.refreshes = 0
        self.applied: list[Any] = []
        self.coordinator = SimpleNamespace(
            hass=self.hass,
            client=self.client,
            stream=SimpleNamespace(connected=True),
            controllers=[object()] * controllers,
            async_refresh_entities=self._refresh,
        )
        # `gcs_state` is here only to prove the read never touches it.
        self.valve = SimpleNamespace(
            gcs_device=SimpleNamespace(device_id=VALVE_ID),
            gcs_state=SimpleNamespace(apply_rest_state=self.applied.append),
        )
        self.watch = CloudConnectionWatch(self.coordinator, self.valve)

    def _refresh(self) -> None:
        self.refreshes += 1

    # Events ---------------------------------------------------------------- #
    def zone_on(self, *statuses: str) -> None:
        statuses = statuses or ("ON", "OFF")
        self.watch.note_hub_envelope(
            Envelope(
                sku="HUB",
                device_id="hub-test0001",
                code="SHOWER_VALVE_STS",
                attributes=[
                    {"zone": str(i + 1), "status": s} for i, s in enumerate(statuses)
                ],
                received_at=0.0,
            )
        )

    def valve_speaks(self) -> None:
        self.watch.note_gcs_message()

    def run_checks(self) -> int:
        """Let every spawned read finish. Returns how many ran."""
        pending = [t for t in self.hass.tasks if not t.done()]
        for task in pending:
            task.run()
        return len(pending)

    @property
    def reads(self) -> int:
        return len(self.client.calls)

    @property
    def spawned(self) -> int:
        return len(self.hass.tasks)


@pytest.fixture
def rig(monkeypatch):
    rig = Rig(monkeypatch)
    rig.watch.async_start()
    yield rig
    # A read a test left in flight is closed, not left for the garbage collector to warn about.
    for task in rig.hass.tasks:
        if not task.done():
            task.cancel()


# --------------------------------------------------------------------------- #
# Trigger A — the controller says a zone is ON, the valve says nothing
# --------------------------------------------------------------------------- #
def test_a_zone_on_with_a_silent_valve_asks_kohler_once_the_pair_window_closes(rig):
    """The only signal that separated the real outage from 18 healthy days."""
    rig.zone_on()
    rig.timers.advance(PAIR - 1)
    assert rig.spawned == 0, "asked before the valve had its chance to answer"

    rig.timers.advance(1)
    assert rig.run_checks() == 1

    assert rig.client.calls == [VALVE_ID]
    assert rig.watch.connected is True
    assert (
        rig.watch.attributes["checked_because"]
        == "controller reported a zone ON, valve silent"
    )


def test_a_valve_message_inside_the_pair_window_settles_the_contradiction(rig):
    """435 of 437 healthy zone-ON reports had a valve message within the minute."""
    rig.zone_on()
    rig.timers.advance(PAIR / 2)
    rig.valve_speaks()
    rig.timers.advance(PAIR)

    assert rig.spawned == 0


def test_a_zone_on_just_after_a_valve_message_is_already_paired(rig):
    rig.valve_speaks()
    rig.timers.advance(PAIR - 1)
    rig.zone_on()
    rig.timers.advance(PAIR + 1)

    assert rig.spawned == 0


def test_an_all_off_card_is_not_evidence(rig):
    """The controller republishes its OFF cards in favorite bursts with no valve action."""
    rig.zone_on("OFF", "OFF")
    rig.timers.advance(PAIR + 1)
    assert rig.spawned == 0


def test_only_the_shower_valve_card_is_read_for_trigger_a(rig):
    rig.watch.note_hub_envelope(
        Envelope(
            sku="HUB",
            device_id="hub-test0001",
            code="STEAM_STS",
            attributes=[{"status": "ON"}],
            received_at=0.0,
        )
    )
    rig.timers.advance(PAIR + 1)
    assert rig.spawned == 0


def test_repeated_zone_on_reports_in_one_shower_ask_only_once(rig):
    """Trigger A fires on bursty controller traffic; one pending check per contradiction."""
    rig.zone_on()
    rig.timers.advance(10)
    rig.zone_on("ON", "ON")
    rig.timers.advance(PAIR)

    assert rig.run_checks() == 1


# --------------------------------------------------------------------------- #
# Trigger B — prolonged quiet
# --------------------------------------------------------------------------- #
def test_a_valve_quiet_for_the_whole_window_is_asked_about_and_asked_again(rig):
    """Trigger B keeps asking while the quiet lasts — the outage was 12 h 22 m long."""
    rig.valve_speaks()
    rig.timers.advance(QUIET - 1)
    assert rig.spawned == 0

    rig.timers.advance(1)
    assert rig.run_checks() == 1
    assert rig.watch.attributes["checked_because"] == "no valve message for 3h"

    rig.timers.advance(QUIET)
    assert rig.run_checks() == 1
    assert rig.reads == 2


def test_a_valve_that_has_never_spoken_is_asked_about_after_the_window(rig):
    """No timestamp means nothing heard since setup — exactly what this trigger asks about."""
    rig.timers.advance(QUIET)
    assert rig.run_checks() == 1
    assert (
        rig.watch.attributes["checked_because"] == "no valve message since setup (3h)"
    )

    rig.timers.advance(QUIET)
    assert rig.run_checks() == 1


def test_a_talking_valve_pushes_the_quiet_deadline_back_without_a_read(rig):
    """The timer is armed once; when it fires early it waits out the remainder instead."""
    rig.timers.advance(QUIET - 3600)
    rig.valve_speaks()
    rig.timers.advance(3600)  # the original deadline passes
    assert rig.spawned == 0

    rig.timers.advance(QUIET - 3600 - 1)
    assert rig.spawned == 0
    rig.timers.advance(1)  # a full window after the last message
    assert rig.run_checks() == 1


def test_the_quiet_timer_is_not_rebuilt_on_every_valve_message(rig):
    """Thousands of messages a day must not each allocate a new three-hour timer."""
    for _ in range(50):
        rig.valve_speaks()
        rig.timers.advance(1)

    assert len(rig.timers.pending) == 1


def test_starting_twice_leaves_one_quiet_timer(rig):
    """`async_start` runs again on every reconnect."""
    rig.watch.async_start()
    rig.watch.async_start()
    assert len(rig.timers.pending) == 1


# --------------------------------------------------------------------------- #
# The guards
# --------------------------------------------------------------------------- #
def test_reads_this_module_starts_respect_the_cooldown(rig):
    rig.zone_on()
    rig.timers.advance(PAIR)
    assert rig.run_checks() == 1

    rig.timers.advance(600)
    rig.zone_on()
    rig.timers.advance(PAIR)
    assert rig.spawned == 1, "a second read inside the cooldown"

    rig.timers.advance(COOLDOWN)
    rig.zone_on()
    rig.timers.advance(PAIR)
    assert rig.run_checks() == 1
    assert rig.reads == 2


def test_a_free_answer_never_suppresses_an_investigation(rig):
    """The valve can be Connected one moment and gone two minutes later."""
    rig.watch.note_rest_payload({"connectionState": "Connected"}, "REST seed")
    rig.zone_on()
    rig.timers.advance(PAIR)

    assert rig.run_checks() == 1


@pytest.mark.parametrize("stream", [SimpleNamespace(connected=False), None])
def test_nothing_is_asked_while_our_own_stream_is_down(rig, stream):
    """Then the valve's silence is ours, and asking would report our outage as its."""
    rig.coordinator.stream = stream
    rig.zone_on()
    rig.timers.advance(PAIR)
    assert rig.spawned == 0

    # A skipped check did nothing, so it must not have used up the cooldown either.
    rig.coordinator.stream = SimpleNamespace(connected=True)
    rig.zone_on()
    rig.timers.advance(PAIR)
    assert rig.run_checks() == 1


def test_a_read_already_in_flight_is_not_doubled_and_does_not_burn_the_cooldown(rig):
    rig.zone_on()
    rig.timers.advance(PAIR)
    assert rig.spawned == 1  # in flight, not yet answered

    rig.timers.advance(COOLDOWN)
    rig.zone_on()
    rig.timers.advance(PAIR)
    assert rig.spawned == 1, "a second read while the first is still running"

    # The first read lands; the next contradiction is not held back by the skipped call.
    rig.run_checks()
    rig.zone_on()
    rig.timers.advance(PAIR)
    assert rig.spawned == 2


# --------------------------------------------------------------------------- #
# What the answer means
# --------------------------------------------------------------------------- #
def test_the_sensor_is_unknown_until_the_first_answer(rig):
    """ "Not asked yet" and "it said no" must not render the same."""
    attrs = rig.watch.attributes
    assert rig.watch.connected is None
    assert attrs["connection_state"] is None
    assert attrs["last_checked"] is None
    assert attrs["checks"] == 0
    assert attrs["seconds_since_valve_message"] is None
    assert attrs["contradiction_watch"] is True


def test_disconnected_reads_offline(rig):
    rig.client.replies = [{"connectionState": "Disconnected", "lastConnected": 17}]
    rig.timers.advance(QUIET)
    rig.run_checks()

    attrs = rig.watch.attributes
    assert rig.watch.connected is False
    assert attrs["connection_state"] == "Disconnected"
    assert attrs["cloud_last_connected"] == 17
    assert attrs["checks"] == 1
    assert attrs["last_checked"].endswith("Z")


@pytest.mark.parametrize(
    ("reported", "connected"),
    [
        ("Connected", True),
        ("connected", True),
        ("DISCONNECTED", False),
        (" Disconnected ", False),
        ("Sleeping", True),  # unfamiliar: the Konnect app treats it as online
        ("", True),
    ],
)
def test_only_disconnected_is_an_outage(rig, reported, connected):
    """The app's own rule: online unless it reads exactly `Disconnected`, any case."""
    rig.watch.note_rest_payload({"connectionState": reported}, "t", notify=False)
    assert rig.watch.connected is connected


def test_an_outage_is_warned_about_once_per_transition(rig, caplog):
    """The condition the module exists for, at WARNING — but not on every repeat read."""
    with caplog.at_level(logging.DEBUG, logger=cloud_watch_module.__name__):
        for state in ("Disconnected", "Disconnected", "Connected", "Disconnected"):
            rig.watch.note_rest_payload({"connectionState": state}, "t", notify=False)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "power cycle" in warnings[0].getMessage()


def test_an_unfamiliar_value_is_logged_once_and_read_as_reachable(rig, caplog):
    with caplog.at_level(logging.INFO, logger=cloud_watch_module.__name__):
        for state in ("Sleeping", "Sleeping", "Rebooting", "Rebooting"):
            rig.watch.note_rest_payload({"connectionState": state}, "t", notify=False)

    unfamiliar = [r for r in caplog.records if "unfamiliar" in r.getMessage()]
    assert [r.args[0] for r in unfamiliar] == ["Sleeping", "Rebooting"]
    assert rig.watch.connected is True


@pytest.mark.parametrize("payload", [{}, None, {"state": {}}])
def test_a_reply_without_connection_state_is_not_a_verdict(rig, payload, caplog):
    """A missing field must not read as "not connected" — nor overwrite the last answer."""
    rig.watch.note_rest_payload({"connectionState": "Connected"}, "t", notify=False)
    with caplog.at_level(logging.WARNING, logger=cloud_watch_module.__name__):
        rig.watch.note_rest_payload(payload, "t")

    assert rig.watch.connected is True
    assert rig.watch.attributes["last_error"] == (
        "gcs-state carried no connectionState field"
    )
    assert rig.refreshes == 1
    assert any("no connectionState" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "error", [KohlerError("HTTP 503"), AuthError("token refresh failed")]
)
@pytest.mark.parametrize("before", [True, False])
def test_a_failed_read_does_not_change_connected(rig, error, before):
    """ "We could not reach Kohler" is a different fault from "Kohler cannot reach the valve"."""
    rig.watch.note_rest_payload(
        {"connectionState": "Connected" if before else "Disconnected"},
        "t",
        notify=False,
    )
    rig.client.replies = [error]
    rig.timers.advance(QUIET)
    rig.run_checks()

    assert rig.watch.connected is before
    assert rig.watch.attributes["last_error"] == f"{type(error).__name__}: {error}"
    assert rig.refreshes == 1, "the error should still reach the entity's attributes"


def test_a_failed_read_before_any_answer_leaves_the_sensor_unknown(rig):
    rig.client.replies = [KohlerError("HTTP 500")]
    rig.timers.advance(QUIET)
    rig.run_checks()
    assert rig.watch.connected is None


def test_a_successful_read_clears_the_last_error(rig):
    rig.client.replies = [KohlerError("HTTP 500"), {"connectionState": "Connected"}]
    rig.timers.advance(QUIET)
    rig.run_checks()
    rig.timers.advance(QUIET)
    rig.run_checks()

    assert rig.watch.attributes["last_error"] is None
    assert rig.watch.connected is True


def test_a_reachability_check_never_feeds_valve_state(rig):
    """It runs unattended at 3 a.m.; it must not be able to start a warm-up restore."""
    rig.client.replies = [{"connectionState": "Connected", "state": {"warmUp": "off"}}]
    rig.timers.advance(QUIET)
    rig.run_checks()

    assert rig.applied == []
    assert rig.watch.connected is True


def test_entities_are_told_unless_the_caller_pushes_its_own_snapshot(rig):
    """The setup seed passes `notify=False`: the platforms do not exist yet."""
    rig.watch.note_rest_payload({"connectionState": "Connected"}, "t", notify=False)
    assert rig.refreshes == 0
    rig.watch.note_rest_payload({"connectionState": "Connected"}, "t")
    assert rig.refreshes == 1


# --------------------------------------------------------------------------- #
# Recovery — the power cycle that brings a valve back
# --------------------------------------------------------------------------- #
def test_a_valve_reported_offline_is_asked_about_as_soon_as_it_speaks(rig):
    """Without this the sensor stayed off for hours after the valve was back."""
    rig.watch.note_rest_payload({"connectionState": "Disconnected"}, "REST seed")
    assert rig.watch.connected is False

    rig.valve_speaks()
    assert rig.run_checks() == 1
    assert rig.watch.connected is True
    assert rig.watch.attributes["checked_because"] == (
        "valve spoke again after being reported offline"
    )

    # Back online: further messages cost nothing.
    for _ in range(5):
        rig.valve_speaks()
    assert rig.spawned == 1


def test_the_recovery_read_still_respects_the_cooldown(rig):
    """Ask rather than assume — but not more often than any other read this module makes."""
    rig.client.replies = [{"connectionState": "Disconnected"}]
    rig.timers.advance(QUIET)
    rig.run_checks()
    assert rig.watch.connected is False

    rig.timers.advance(300)
    rig.valve_speaks()
    assert rig.spawned == 1, "inside the cooldown of the read that found it offline"

    rig.timers.advance(COOLDOWN)
    rig.valve_speaks()
    assert rig.run_checks() == 1
    assert rig.watch.connected is True


def test_a_valve_still_offline_after_speaking_stays_offline(rig):
    """The answer comes from `connectionState`, not from the message that prompted it."""
    rig.watch.note_rest_payload({"connectionState": "Disconnected"}, "REST seed")
    rig.client.replies = [{"connectionState": "Disconnected"}]

    rig.valve_speaks()
    rig.run_checks()

    assert rig.watch.connected is False


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def test_stopping_cancels_every_timer_and_the_read_in_flight(rig):
    """Called from entry unload: nothing may fire against a discarded coordinator."""
    rig.zone_on()
    rig.timers.advance(PAIR)  # read spawned
    rig.zone_on()  # a fresh pair window armed
    task = rig.hass.tasks[0]
    assert rig.timers.pending

    rig.watch.async_stop()

    assert rig.timers.pending == []
    assert task.cancelled
    rig.valve_speaks()
    rig.timers.advance(QUIET * 2)
    assert rig.timers.pending == []
    assert rig.spawned == 1


def test_a_stopped_watch_asks_nothing_even_for_a_valve_reported_offline(rig):
    rig.watch.note_rest_payload({"connectionState": "Disconnected"}, "t", notify=False)
    rig.watch.async_stop()
    rig.valve_speaks()
    assert rig.spawned == 0


def test_a_gcs_only_account_has_no_contradiction_watch(monkeypatch):
    rig = Rig(monkeypatch, controllers=0)
    assert rig.watch.attributes["contradiction_watch"] is False


def test_the_quiet_age_is_reported_from_the_monotonic_clock(rig):
    rig.valve_speaks()
    rig.timers.advance(42)
    assert rig.watch.attributes["seconds_since_valve_message"] == 42.0
