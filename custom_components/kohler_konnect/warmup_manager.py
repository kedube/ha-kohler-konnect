"""Warm-up mode: writing it, watching it, and putting it back.

Warm-up is a stored mode, not an action: an enabled mode makes the valve warm the water by
itself at the start of a session. Other writers routinely turn it off — the hub's web UI on
every signed-in use (``docs/protocol/gcs_valve.md`` §5), the Konnect app, the fixture
itself — and auto-restore notices and undoes that.

Everything that touches the warm-up mode is here; `Valve` keeps
`valve.warmup_auto_restore`, `valve.last_warmup_mode` and `valve.async_set_warmup` as
delegates. The pure decisions — whether to restore, to what, and what to journal — are in
:mod:`.konnect.warmup`; this module holds the side effects.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_LAST_WARMUP_MODE,
    CONF_WARMUP_AUTO_RESTORE,
    CONF_WARMUP_RESTORE_PENDING,
    VALVE_OFFLINE,
    WARMUP_AUTO_RESTORE_DELAY_SECONDS,
    WARMUP_AUTO_RESTORE_GIVING_UP,
    WARMUP_AUTO_RESTORE_MAX_CONSECUTIVE,
    WARMUP_AUTO_RESTORE_NO_TARGET,
    WARMUP_AUTO_RESTORE_NOT_WRITABLE,
    WARMUP_AUTO_RESTORE_SETTLED_SECONDS,
    WARMUP_CONTEXT_AFTER_SECONDS,
    WARMUP_CONTEXT_BEFORE_SECONDS,
    WARMUP_READBACK_DELAYS,
    WARMUP_SELF_WRITE_GRACE_SECONDS,
)
from .konnect import (
    WARMUP_DISABLED,
    WARMUP_MODES_CURRENT,
    AuthError,
    KohlerError,
    credential_is_dead,
)
from .konnect.warmup import (
    journal_event,
    restorable,
    restore_target,
    should_restore_warmup,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle; only needed for the annotation
    from .coordinator import Valve

_LOGGER = logging.getLogger(__name__)

__all__ = ["WarmupManager"]


class WarmupManager:
    """The warm-up half of one :class:`~.coordinator.Valve`.

    Keeps a reference to the valve rather than copies of what it reads, because
    `gcs_state`, `gcs`, `client` and the option store are replaced or re-seeded during the
    valve's life. One per valve, created in `Valve.__init__`.
    """

    def __init__(self, valve: Valve) -> None:
        self._valve = valve
        # When we last wrote a mode, and which: together they tell the echo of our own
        # write from somebody else disabling warm-up (`_warmup_write_status`).
        self._warmup_self_write_at: float | None = None
        self._warmup_self_write_mode: str | None = None
        # One restore in flight at a time; two would race each other over a single field.
        self._warmup_restore_task: asyncio.Task | None = None
        # Consecutive restores that did not stick, and when the last one ran. Together they
        # decide when to stop fighting whatever keeps disabling it.
        self._warmup_restores = 0
        self._warmup_restored_at: float | None = None
        # Latch, so a persistent fight warns once rather than on every recurrence.
        self._warmup_gave_up_reported = False
        # The mode the most recent disable took away. A pending restore reads it when its
        # wait ends, so a disable folded into it can still change what goes back.
        self._warmup_taken_away: str | None = None
        # When the valve was last seen in an enabled mode, announced or found by a seed.
        # Our own `Off` is judged against it; see `_enabled_seen_since_write`.
        self._enabled_seen_at: float | None = None
        # Latch, so a mode auto-restore cannot write is explained once rather than on every
        # hub sign-in.
        self._warmup_not_writable_reported = False

    def _reset_restore_count(self) -> None:
        """End a fight: zero the counter that stops endless restores."""
        self._warmup_restores = 0
        self._warmup_restored_at = None
        # Cleared with the counter, so a fight that stops and later restarts is reported
        # again.
        self._warmup_gave_up_reported = False

    def reset_restore_task(self) -> None:
        """Forget the pending restore. Called from `Valve.stop` after cancelling tasks."""
        self._warmup_restore_task = None

    @callback
    def handle_mode_change(
        self, before: str | None, after: str | None, *, announced: bool = False
    ) -> None:
        """Public name for `_handle_warmup_mode_change` — see there."""
        self._handle_warmup_mode_change(before, after, announced=announced)

    def note_seeded_mode(self, before: str | None, after: str | None) -> None:
        """Handle a mode the REST seed found, rather than one the valve announced.

        The seed runs at setup and on every MQTT reconnect and writes `warmup_mode`
        directly, so a change made while the stream was down (a hub web-UI sign-in, say)
        reaches nothing else. A discovered disable goes through the same decision,
        self-write grace and single-flight guard as an announced one, and the restore
        re-checks the live mode before writing, so the read's unknown age does not matter.
        It is journalled with `source: "rest"` and no traffic window.

        Also remembers an enabled mode found here, and resumes a restore owed from before a
        restart or reload (`_resume_pending_restore`).
        """
        write_age, ours = self._warmup_write_status(after)
        if before is not None and after != before:
            restoring = should_restore_warmup(
                before,
                after,
                enabled=self.auto_restore,
                self_write_mode=self._warmup_self_write_mode,
                self_write_age=write_age,
                grace_seconds=WARMUP_SELF_WRITE_GRACE_SECONDS,
                enabled_seen_since_write=self._enabled_seen_since_write(),
            )
            self._warmup_journal(
                "mode",
                before=before,
                after=after,
                ours=ours,
                source="rest",
                restoring=restoring,
            )
            if after == WARMUP_DISABLED:
                self._note_unwritable_disable(before, ours=ours)
            if restoring:
                self._schedule_warmup_restore(before)
        # Remembered here too: MQTT and our own writes both miss a mode that was already in
        # force at startup, which would leave auto-restore with no target.
        if after is not None and after != WARMUP_DISABLED:
            self._remember_warmup_mode(after)
            # Seen enabled, unless this read could predate our own `Off`: the cloud
            # document trails the device by seconds, so within the echo's grace it can
            # still show the mode that `Off` replaced.
            if not (
                self._warmup_self_write_mode == WARMUP_DISABLED
                and write_age is not None
                and write_age <= WARMUP_SELF_WRITE_GRACE_SECONDS
            ):
                self._enabled_seen_at = time.monotonic()
        self._resume_pending_restore(after)

    def _resume_pending_restore(self, after: str | None) -> None:
        """Resume a restore that was owed when Home Assistant stopped or the entry reloaded.

        A restart or reload cancels the restore task, and the next seed sees warm-up off
        with no `before`, which is not a disable. So a scheduled restore records its target
        in `CONF_WARMUP_RESTORE_PENDING`, and the seed restarts it if warm-up is still off
        and auto-restore still on; otherwise the record is cleared. A seed that read no mode
        decides nothing. The resumed restore waits and re-checks like any other.
        """
        pending = self._valve.option(CONF_WARMUP_RESTORE_PENDING)
        if pending is None or after is None:
            return
        if after != WARMUP_DISABLED or not self.auto_restore or not restorable(pending):
            self._set_pending_restore(None)
            return
        if (
            self._warmup_restore_task is not None
            and not self._warmup_restore_task.done()
        ):
            # Already being done, by the restore that recorded it.
            return
        self._warmup_journal("restore_resumed", target=pending, source="rest")
        self._schedule_warmup_restore(pending)

    def _set_pending_restore(self, target: str | None) -> None:
        """Record the mode a restore owes — or, with None, that none is owed.

        Written only on a change, since each write is a config-entry update. Never cleared
        by cancelling the restore task — that is the unload this record exists to outlive —
        only by the restore being done or no longer wanted.
        """
        if self._valve.option(CONF_WARMUP_RESTORE_PENDING) != target:
            self._valve.set_option(CONF_WARMUP_RESTORE_PENDING, target)

    async def async_set_warmup(self, mode: str) -> None:
        """Set the valve's warm-up mode. Does not run water.

        The mode persists across power cycles; the valve re-announces it about 4 s after
        every boot. ``mode`` must be one of ``WARMUP_MODES_CURRENT``: the delayed-start
        modes are refused, since nothing defines their delay.

        Raises `HomeAssistantError` for any other mode, while water is running (the Konnect
        app blocks this too; whether the device would refuse is untested), or if the write
        fails. After a write, reads the mode back (`WARMUP_READBACK_DELAYS`) and logs
        whether the valve applied it.
        """
        if mode not in WARMUP_MODES_CURRENT:
            raise HomeAssistantError(
                f"{mode!r} is not a warmup mode this integration writes. Expected one of: "
                + ", ".join(WARMUP_MODES_CURRENT)
            )
        if self._valve.gcs_state.is_running:
            raise HomeAssistantError(
                "Warmup cannot be changed while the shower is running. The Konnect app "
                "blocks this too. Turn the water off and try again."
            )
        # Recorded before the call, not after: the valve's echo can arrive while the POST
        # is still in flight, and a disable we caused must be recognisable by then.
        self._warmup_self_write_at = time.monotonic()
        self._warmup_self_write_mode = mode
        # This write settles any restore owed: it is either that restore's own write or a
        # mode chosen here, `Off` included, which a resumed restore must not overwrite.
        self._set_pending_restore(None)
        with self._valve.coordinator.command_errors(VALVE_OFFLINE):
            await self._valve.gcs.async_set_warmup(mode)

        # Read the mode back: a 200 means the cloud accepted the command, not that the
        # valve applied it (it ignores the command while warm-up is disabled on the fixture
        # itself). The first reads lag the valve; see `WARMUP_READBACK_DELAYS`.
        confirmed = None
        for delay in WARMUP_READBACK_DELAYS:
            if delay:
                await asyncio.sleep(delay)
            confirmed = await self.async_read_warmup_mode()
            if confirmed == mode:
                self._remember_warmup_mode(mode)
                _LOGGER.info("Warmup mode set to %s and confirmed by the valve", mode)
                return
        if confirmed is None:
            _LOGGER.info(
                "Warmup mode %s sent to the Anthem valve; the read-back did not answer, so "
                "the stored mode is whatever the valve reports next over MQTT",
                mode,
            )
        else:
            # Past the settle window, so a real disagreement rather than lag.
            _LOGGER.warning(
                "Warmup mode was set to %s but the valve still reports %s after %.0f s. The "
                "cloud accepted the command and the valve did not apply it — the usual cause "
                "is warmup being disabled on the fixture. Nothing has been retried.",
                mode,
                confirmed,
                sum(WARMUP_READBACK_DELAYS),
            )

    async def async_read_warmup_mode(self) -> str | None:
        """Read `gcs-state` `warmUpState.warmUp`, apply that mode, and return it.

        Applies the mode and nothing else — not the zone words, and not the running flag
        (`warmUpState.state`). The cloud document trails the valve by seconds, and a stale
        "not in progress" would switch off the guard that stops a change made during a
        warm-up from becoming a shower on every warm-up outlet (`Valve._warming_up`); the
        valve reports that flag with every status message anyway. REST is what Kohler's
        cloud believes; the valve's own `GCS_WARM_STS` remains the final word.

        Returns None if the read failed, carried no warm-up field, or was overtaken by a
        push carrying the mode (`_push_serials["warmup_mode"]`) — never a guess, since
        "Off" and "unknown" must stay distinct.
        """
        pushes = self._valve._push_serials
        mode_pushes = pushes["warmup_mode"]
        requested_at = time.monotonic()
        try:
            payload = await self._valve.client.async_get_gcs_state(
                self._valve.gcs_device.device_id
            )
        except (AuthError, KohlerError) as err:
            _LOGGER.debug("Could not read warmup mode: %s", err)
            if credential_is_dead(err):
                self._valve.coordinator._handle_auth_error(err)
            return None
        if self._valve.cloud_watch is not None:
            # CLOUD CONNECTION WATCH — this read carries `connectionState`; pass it on.
            # `requested_at` lets a valve message that lands during the read outrank a
            # `Disconnected` in it.
            self._valve.cloud_watch.note_rest_payload(
                payload, "warmup read-back", requested_at=requested_at
            )
        state = payload.get("state") if isinstance(payload, dict) else None
        warm = state.get("warmUpState") if isinstance(state, dict) else None
        if not isinstance(warm, dict):
            # No warm-up fields, so nothing to apply and no opinion on the write.
            return None
        if pushes["warmup_mode"] != mode_pushes:
            # The valve pushed its mode while this read was in flight, so the reply is the
            # older of the two and applying it would undo the push. Same rule as the seed
            # in `coordinator.py`.
            return None
        gcs_state = self._valve.gcs_state
        mode = warm.get("warmUp")
        if mode:
            gcs_state.warmup_mode = str(mode)
        # Same notification path as an MQTT update, so the dropdown lands on the confirmed
        # value and drops its optimistic guess.
        self._valve.coordinator.async_refresh_entities()
        # What this read said, not the mode cached before it: a read with no warm-up field
        # has no opinion, and must not be taken for the valve disagreeing with a write.
        return gcs_state.warmup_mode if mode else None

    @property
    def auto_restore(self) -> bool:
        """Whether to put the warm-up mode back after something else disables it.

        Read live from the entry options, so the switch takes effect at once. Off unless
        enabled.
        """
        return bool(self._valve.option(CONF_WARMUP_AUTO_RESTORE, False))

    @property
    def last_mode(self) -> str | None:
        """The last enabled warm-up mode seen on the valve, or None if none has been seen.

        Persisted in the entry options so it survives restarts. With None, auto-restore
        does nothing rather than guess between all and selected outlets.
        """
        stored = self._valve.option(CONF_LAST_WARMUP_MODE)
        return stored if stored in WARMUP_MODES_CURRENT else None

    def _message_window(self, since: float, until: float | None = None) -> list[dict]:
        """Messages between two monotonic instants, oldest first, without the clock field."""
        # This valve's messages and every controller's, never another valve's: the
        # controller's `SYSTEM_STS: SYSTEM_READY` is a marker seen around disables.
        valve = self._valve
        others = {v.device_id for v in valve.coordinator.valves if v is not valve}
        return [
            {k: v for k, v in item.items() if k not in ("at", "device")}
            for item in valve.coordinator._recent_messages
            if item["at"] >= since
            and (until is None or item["at"] <= until)
            and item.get("device") not in others
        ]

    @callback
    def _warmup_journal(self, event: str, **fields: Any) -> None:
        """Append to the warm-up journal and to any active Report Log.

        The two are independent: a decision reaches an active report whether or not the
        warm-up journal is on (`report_log.ReportLog.note`). This runs on the loop, so each
        `note` only queues the record and `prepare` writes it in an executor.
        """
        tagged = self._valve._tagged(fields)
        report_log = self._valve.coordinator.report_log
        if report_log is not None:
            report_log.note("warmup", event, dict(tagged))
            if report_log.wants_open:
                self._valve.hass.async_add_executor_job(report_log.prepare)
        if self._valve.warmup_log is None:
            return
        self._valve.warmup_log.note(event, **tagged)
        if self._valve.warmup_log.wants_open:
            self._valve.hass.async_add_executor_job(self._valve.warmup_log.prepare)

    async def _async_journal_warmup_context(self, at: float) -> None:
        """Record the traffic in the `WARMUP_CONTEXT_AFTER_SECONDS` after a disable.

        A separate record because `SYSTEM_STS: SYSTEM_READY`, the most distinctive marker,
        arrives seconds after the disable.
        """
        await asyncio.sleep(WARMUP_CONTEXT_AFTER_SECONDS)
        state = self._valve.gcs_state
        self._warmup_journal(
            "context",
            after_window=self._message_window(at),
            window_seconds=WARMUP_CONTEXT_AFTER_SECONDS,
            mode_now=state.warmup_mode,
        )

    @callback
    def _remember_warmup_mode(self, mode: str) -> None:
        """Record an enabled mode as what auto-restore should put back.

        Called from the valve's MQTT announcement, from a confirmed write of ours, and from
        the REST seed; each sees modes the others miss.

        Resets the give-up counter only once the last restore has held for
        `WARMUP_AUTO_RESTORE_SETTLED_SECONDS`: before that, the enabled mode is most likely
        that restore's own echo, and resetting on it would let a fight restore forever.
        Also clears any pending restore, since warm-up is on.
        """
        if mode == WARMUP_DISABLED:
            return
        restored_at = self._warmup_restored_at
        if (
            restored_at is None
            or time.monotonic() - restored_at >= WARMUP_AUTO_RESTORE_SETTLED_SECONDS
        ):
            self._reset_restore_count()
        if mode != self._valve.option(CONF_LAST_WARMUP_MODE):
            self._valve.set_option(CONF_LAST_WARMUP_MODE, mode)
        self._set_pending_restore(None)

    def _warmup_write_status(self, after: str | None) -> tuple[float | None, bool]:
        """How long ago we last wrote a mode, and whether ``after`` is that write.

        ``ours`` is true when ``after`` is the mode we wrote and the write is within
        `WARMUP_SELF_WRITE_GRACE_SECONDS` — or, for our `Off`, while `_our_off_stands`.
        """
        write_age = (
            None
            if self._warmup_self_write_at is None
            else time.monotonic() - self._warmup_self_write_at
        )
        ours = (
            self._warmup_self_write_mode == after
            and write_age is not None
            and (
                write_age <= WARMUP_SELF_WRITE_GRACE_SECONDS
                or (after == WARMUP_DISABLED and self._our_off_stands())
            )
        )
        return write_age, ours

    def _enabled_seen_since_write(self) -> bool:
        """Whether the valve has been seen in an enabled mode since our last warm-up write.

        "Seen" means announced over MQTT, or found by a seed outside the echo's grace —
        never a read-back, which can still show the mode our write replaced. True when we
        have written nothing, so it never excuses anything on its own.
        """
        written = self._warmup_self_write_at
        if written is None:
            return True
        seen = self._enabled_seen_at
        return seen is not None and seen > written

    def _our_off_stands(self) -> bool:
        """Whether our last write was `Off` and nothing has enabled warm-up since.

        Our `Off` stays ours beyond the echo's grace: one chosen while the stream was down
        is first seen by the reconnect's reseed, possibly much later, and must not be
        restored over.
        """
        return (
            self._warmup_self_write_mode == WARMUP_DISABLED
            and not self._enabled_seen_since_write()
        )

    def _note_unwritable_disable(self, before: str | None, *, ours: bool) -> None:
        """Explain, once, why a disable out of a mode we cannot write is left alone.

        `should_restore_warmup` refuses such a disable (the delayed-start modes are not
        writable). A restore already waiting is updated too: the mode most recently taken
        away is no longer what it would write.
        """
        if before is None or before == WARMUP_DISABLED or restorable(before):
            return
        if (
            self._warmup_restore_task is not None
            and not self._warmup_restore_task.done()
        ):
            self._warmup_taken_away = before
            self._set_pending_restore(None)
        if not self.auto_restore or ours:
            return
        self._warmup_journal(
            "restore_skipped",
            reason="the mode taken away is not one this integration writes",
            mode=before,
        )
        if not self._warmup_not_writable_reported:
            self._warmup_not_writable_reported = True
            _LOGGER.warning(WARMUP_AUTO_RESTORE_NOT_WRITABLE, before)

    def _schedule_warmup_restore(self, taken_away: str | None) -> None:
        """Start one restore task, or journal why not.

        Only one runs at a time: two would race `async_set_warmup` over a single field. A
        later disable folds into the pending restore instead.
        """
        if (
            self._warmup_restore_task is not None
            and not self._warmup_restore_task.done()
        ):
            # Folded in, but it took away a newer mode than the one that started the wait,
            # so that is what is owed now if a restart or reload ends the wait first.
            if taken_away is not None:
                self._warmup_taken_away = taken_away
                self._set_pending_restore(restore_target(taken_away, self.last_mode))
            self._warmup_journal(
                "restore_skipped", reason="a restore is already pending"
            )
            return
        self._warmup_restore_task = self._valve._track(
            self._async_restore_warmup(taken_away)
        )

    @callback
    def _handle_warmup_mode_change(
        self, before: str | None, after: str | None, *, announced: bool = False
    ) -> None:
        """React to the valve reporting a warm-up mode over MQTT.

        Remembers any enabled mode as the restore target, journals the observation, and
        schedules a restore for a disable this integration did not cause. ``announced``
        marks a `GCS_WARM_STS`; it matters only when the mode did not move (see
        `journal_event`).
        """
        record = journal_event(before, after, announced=announced)
        if record is None:
            return

        # Before the branch, because an announcement needs `ours` as much as a change does.
        write_age, ours = self._warmup_write_status(after)

        if after != WARMUP_DISABLED:
            # The valve's own word that warm-up is on, which our `Off` is judged against. A
            # restatement counts too: after our `Off`, it means the `Off` did not take or
            # something has turned warm-up back on.
            self._enabled_seen_at = time.monotonic()

        if record == "announced":
            # The valve restating its current mode. Most are the echo of our own write,
            # arriving after the read-back already moved `warmup_mode`, so the record needs
            # `ours` to be told apart from the valve volunteering its state.
            #
            # Never falls through to the disable path: a repeated `warmUpDisabled` is not a
            # fresh disable, and relying on `should_restore_warmup` to refuse it implicitly
            # is how a restore loop starts.
            self._warmup_journal("announced", mode=after, ours=ours, source="mqtt")
            return

        # Every change is journalled, not only disables: knowing what a normal week looks
        # like is half of recognising an abnormal event. With the `announced` record above
        # and `Valve.journal_baseline`, every announcement is recorded.
        self._warmup_journal(
            "mode", before=before, after=after, ours=ours, source="mqtt"
        )

        if after != WARMUP_DISABLED:
            self._remember_warmup_mode(after)
            return

        restoring = should_restore_warmup(
            before,
            after,
            enabled=self.auto_restore,
            self_write_mode=self._warmup_self_write_mode,
            self_write_age=write_age,
            grace_seconds=WARMUP_SELF_WRITE_GRACE_SECONDS,
            enabled_seen_since_write=self._enabled_seen_since_write(),
        )
        now = time.monotonic()
        state = self._valve.gcs_state
        self._warmup_journal(
            "disabled",
            before=before,
            ours=ours,
            restoring=restoring,
            auto_restore=self.auto_restore,
            restores_to=restore_target(before, self.last_mode),
            water_running=state.is_running,
            before_window=self._message_window(
                now - WARMUP_CONTEXT_BEFORE_SECONDS, now
            ),
            window_seconds=WARMUP_CONTEXT_BEFORE_SECONDS,
        )
        # Recorded whether or not we restore; an unrestored disable is the cleaner
        # observation, with nothing of ours in the way.
        self._valve._track(self._async_journal_warmup_context(now))
        self._note_unwritable_disable(before, ours=ours)

        if not restoring:
            return
        self._schedule_warmup_restore(before)

    async def _async_restore_warmup(self, taken_away: str | None = None) -> None:
        """Wait out the delay, re-check, and put the mode back.

        Re-checks rather than cancels: if during the wait the mode was re-enabled, the
        switch turned off, or `Off` chosen here, it does nothing. If water is running it
        waits for the water to stop, then waits the delay again and re-checks everything.

        ``taken_away``, the mode the disable moved away from, is the target in preference
        to the remembered `last_mode` (see `restore_target`).

        The target stays in `CONF_WARMUP_RESTORE_PENDING` until it is written or no longer
        wanted, so a restart or reload, which cancels this task, does not lose it;
        cancellation leaves that record alone.
        """
        if taken_away is not None:
            self._warmup_taken_away = taken_away
        target = restore_target(taken_away, self.last_mode)
        if target is None:
            # Unreachable for a genuine disable; kept so a caller passing nothing says so
            # rather than writing a default.
            _LOGGER.warning(WARMUP_AUTO_RESTORE_NO_TARGET)
            self._warmup_journal(
                "restore_skipped", reason="no enabled mode has ever been seen"
            )
            self._set_pending_restore(None)
            return

        if (
            self._warmup_restored_at is not None
            and time.monotonic() - self._warmup_restored_at
            >= WARMUP_AUTO_RESTORE_SETTLED_SECONDS
        ):
            # The last restore held for the settle window, so it stuck: whatever undid it
            # this time starts a new count.
            self._reset_restore_count()
        if (
            self._warmup_restores >= WARMUP_AUTO_RESTORE_MAX_CONSECUTIVE
            and self._warmup_restored_at is not None
            and time.monotonic() - self._warmup_restored_at
            < WARMUP_AUTO_RESTORE_SETTLED_SECONDS
        ):
            # Give up, and tell the user: something keeps rewriting the mode. Latched so a
            # persistent fight warns once; `_reset_restore_count` clears the latch, so a
            # fight that stops and restarts is reported again.
            if not self._warmup_gave_up_reported:
                self._warmup_gave_up_reported = True
                _LOGGER.warning(
                    WARMUP_AUTO_RESTORE_GIVING_UP, WARMUP_AUTO_RESTORE_MAX_CONSECUTIVE
                )
            self._warmup_journal(
                "restore_skipped",
                reason="gave up after %d restores that did not stick"
                % WARMUP_AUTO_RESTORE_MAX_CONSECUTIVE,
            )
            self._set_pending_restore(None)
            return

        scheduled_at = time.monotonic()
        self._set_pending_restore(target)
        self._warmup_journal(
            "restore_scheduled",
            target=target,
            delay_seconds=WARMUP_AUTO_RESTORE_DELAY_SECONDS,
        )
        await asyncio.sleep(WARMUP_AUTO_RESTORE_DELAY_SECONDS)

        deferred = False
        while True:
            if not self.auto_restore:
                self._warmup_journal(
                    "restore_skipped", reason="switched off during the wait"
                )
                self._set_pending_restore(None)
                return
            state = self._valve.gcs_state
            if state.warmup_mode != WARMUP_DISABLED:
                # Someone got there first; say so rather than return silently.
                _LOGGER.info(
                    "Warmup was re-enabled before auto-restore ran; leaving it alone"
                )
                self._warmup_journal(
                    "restore_skipped",
                    reason="re-enabled during the wait",
                    mode_now=state.warmup_mode,
                )
                self._set_pending_restore(None)
                return
            # ⚠️ An `Off` chosen here during the wait is deliberate and final. The mode
            # still reads disabled, so the check above passes, but our `Off` written after
            # this restore was scheduled outranks the disable that scheduled it.
            if (
                self._warmup_self_write_mode == WARMUP_DISABLED
                and self._warmup_self_write_at is not None
                and self._warmup_self_write_at >= scheduled_at
            ):
                _LOGGER.info(
                    "Warmup was set to Off from Home Assistant before auto-restore ran; "
                    "leaving it off"
                )
                self._warmup_journal(
                    "restore_skipped", reason="Off chosen here during the wait"
                )
                self._set_pending_restore(None)
                return
            # So is one chosen before it, if nothing has enabled warm-up since: the disable
            # being undone is that `Off`, seen late. The decision to restore makes the same
            # check (`_our_off_stands`); this catches what got past it.
            if self._our_off_stands():
                _LOGGER.info(
                    "Warmup was set to Off from Home Assistant and nothing has enabled "
                    "it since; leaving it off"
                )
                self._warmup_journal(
                    "restore_skipped",
                    reason="Off chosen here, and nothing has enabled warm-up since",
                )
                self._set_pending_restore(None)
                return
            if not state.is_running:
                break
            # ⚠️ Water running defers the restore; it must not end it. `async_set_warmup`
            # refuses during a shower, and with the mode already off no later disable would
            # schedule another. Nothing is attempted or counted until the water has stayed
            # off for the delay (the running flag flickers as a shower winds down), and then
            # every check above runs again.
            if not deferred:
                deferred = True
                _LOGGER.info(
                    "Warmup auto-restore is waiting for the water to stop before it "
                    "writes"
                )
                self._warmup_journal(
                    "restore_deferred", target=target, reason="water is running"
                )
            await self._async_until_water_stops()
            await asyncio.sleep(WARMUP_AUTO_RESTORE_DELAY_SECONDS)

        # Chosen now, not before the wait: a disable folded in may have taken away a newer
        # mode — possibly one we cannot write (`_note_unwritable_disable`), and then
        # nothing goes back rather than the older mode this restore was scheduled for.
        #
        # Nothing awaits between the running check above and the one `async_set_warmup`
        # makes, so a refusal for running water cannot reach the counter below.
        latest = self._warmup_taken_away
        if latest and latest != WARMUP_DISABLED and not restorable(latest):
            self._warmup_journal(
                "restore_skipped",
                reason="the mode taken away last is not one this integration writes",
                mode=latest,
            )
            self._set_pending_restore(None)
            return
        target = restore_target(latest, self.last_mode) or target
        self._warmup_restores += 1
        self._warmup_restored_at = time.monotonic()
        _LOGGER.warning(
            "Warmup was disabled by something other than Home Assistant. Setting it back "
            "to %s (attempt %d).",
            target,
            self._warmup_restores,
        )
        self._warmup_journal("restore", target=target, attempt=self._warmup_restores)
        try:
            await self.async_set_warmup(target)
        except HomeAssistantError as err:
            # Not retried, now or after a restart. Running water is waited out above, so
            # this is Kohler or the valve refusing (offline, sign-in, an error), and a write
            # that was sent counts toward the give-up limit. A sent write already cleared
            # the pending record; this clears one refused before sending.
            _LOGGER.warning("Warmup auto-restore could not write: %s", err)
            self._warmup_journal("restore_failed", target=target, error=str(err))
            self._set_pending_restore(None)
            return
        self._warmup_journal(
            "restore_done",
            target=target,
            attempt=self._warmup_restores,
            mode_now=self._valve.gcs_state.warmup_mode,
        )

    async def _async_until_water_stops(self) -> None:
        """Return once the valve reports no water running.

        Woken by every coordinator update. No timeout: a shower can last as long as it
        likes, a stream that drops meanwhile reseeds and notifies on reconnect, and an
        unload cancels the restore task this runs in.
        """
        poke = asyncio.Event()

        @callback
        def _on_update() -> None:
            poke.set()

        remove = self._valve.coordinator.async_add_listener(_on_update)
        try:
            while self._valve.gcs_state.is_running:
                await poke.wait()
                poke.clear()
        finally:
            remove()
