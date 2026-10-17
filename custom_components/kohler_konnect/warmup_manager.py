"""Warm-up mode: writing it, watching it, and putting it back.

Warm-up is a **stored mode**, not an action — an enabled mode means the valve warms the
water by itself at the start of a session — and it is the one setting on this integration
that something else routinely takes away. The hub's web UI writes `warmUpDisabled` on any
signed-in use of it, the Konnect app can, and the fixture itself can. Auto-restore exists
to notice that and undo it, which needs more machinery than a setting normally does: a
memory of the last enabled mode, a record of our own writes so we do not restore over
ourselves, a delay-and-recheck before writing, a counter that gives up rather than fight
forever, and a journal of the whole thing because the cause is still an open question.

That is thirteen methods and six pieces of state, which lived on
:class:`~.coordinator.Valve` until 0.10.0 and made up a fifth of it. They are here now
because they form a closed system: everything that touches the warm-up mode is in this
file, and the rest of `Valve` reaches it through a few members — chiefly
:attr:`auto_restore`, :attr:`last_mode`, :meth:`async_set_warmup` and
:meth:`handle_mode_change`.

**The valve keeps its public surface unchanged.** `valve.warmup_auto_restore`,
`valve.last_warmup_mode` and `valve.async_set_warmup(...)` still work and still mean the
same thing — they delegate here. Entities, services and diagnostics were not touched by
the move, and neither were their unique ids.

The pure decision logic — *should* this disable be restored, and to what — is not here
either: it is in :mod:`.konnect.warmup`, with no I/O and no Home Assistant, so it can
be tested against captured sequences. This module is the part with the side effects.
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

    Holds a back-reference to its valve rather than taking the pieces it needs, because
    almost everything it reads — `gcs_state`, `gcs`, `client`, the option store — is
    replaced or re-seeded during the valve's life, and a snapshot taken at construction
    would go stale. One manager per valve, created in `Valve.__init__` and never shared.
    """

    def __init__(self, valve: Valve) -> None:
        self._valve = valve
        # When we last wrote a mode ourselves, and which — the pair that tells an echo of
        # our own write apart from somebody else disabling warm-up. Read together by
        # `_warmup_write_status`; see `should_restore_warmup`'s self-write grace.
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
        # When the valve was last seen in an enabled mode — announced over MQTT, or found by
        # a seed — which is what our own `Off` is judged against: until something enables
        # warm-up again, a disable seen after that `Off` is still ours. See
        # `_enabled_seen_since_write`.
        self._enabled_seen_at: float | None = None
        # Latch, so a mode auto-restore cannot write is explained once rather than on every
        # hub sign-in.
        self._warmup_not_writable_reported = False

    def _reset_restore_count(self) -> None:
        """End a fight: zero the counter that stops endless restores."""
        self._warmup_restores = 0
        self._warmup_restored_at = None
        # Cleared with the counter, so a fight that stops and later restarts is reported
        # again rather than staying silent for the rest of the run.
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
        """Handle a mode change discovered by the REST seed rather than announced.

        Lifted out of `Valve.async_seed`, where it was thirty lines of warm-up reasoning in
        the middle of a method about seeding everything else.

        `apply_rest_state` writes `warmup_mode` straight in, so a change that happened while
        the stream was down never reaches `_handle_warmup_mode_change` and nothing else
        would record it — the one way a disable can happen and leave no trace in the journal
        at all. That reseed runs on every MQTT reconnect, so the gap it covers is real: any
        signed-in use of the hub web UI writes `warmUpDisabled` (api.md §3h), and a sign-in
        while the stream is down lands exactly here.

        A discovered disable restores through the same machinery as an announced one — same
        decision function, same self-write grace, same single-flight guard — and
        `_async_restore_warmup` waits its delay and re-checks the *live* mode before
        writing, so "the REST read is of unknown age" costs nothing by write time. What a
        discovery still cannot have is a `before_window` (the wire context happened while
        there was no wire), so `source: "rest"` stays on the record and the `restoring`
        field says what was decided.

        It is also where a restore owed from before a restart or reload is finished; see
        `_resume_pending_restore`.
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
        # The third way to learn a mode, and the one `_remember_warmup_mode`'s docstring
        # used to miss. MQTT alone forgets a mode set while the stream was down; our own
        # writes alone forget a mode set from the app or touchscreen; and *both* forget a
        # mode that was simply already in force when we started.
        #
        # That third gap disabled auto-restore for seven hours on 2026-08-20: the valve was
        # in `warmUpAllOutletsWithNoStartDelay`, read correctly over REST at 19:35:32Z, then
        # disabled at 20:36:28Z — and the restore was skipped with "no enabled mode has ever
        # been seen", because no *announcement* had happened in that session. See
        # `_async_restore_warmup`.
        if after is not None and after != WARMUP_DISABLED:
            self._remember_warmup_mode(after)
            # Seen enabled — unless this read could be from before our own `Off`: the cloud
            # document trails the device by seconds (~3 s measured on 2026-08-20), and
            # within the echo's grace it can still show the mode that `Off` replaced.
            if not (
                self._warmup_self_write_mode == WARMUP_DISABLED
                and write_age is not None
                and write_age <= WARMUP_SELF_WRITE_GRACE_SECONDS
            ):
                self._enabled_seen_at = time.monotonic()
        self._resume_pending_restore(after)

    def _resume_pending_restore(self, after: str | None) -> None:
        """Finish a restore that was owed when Home Assistant stopped or the entry reloaded.

        ⚠️ **A restore lives in a task, and a restart or reload cancels it** — inside its
        minute's wait, or while it waits out a running shower. Until 2026-10-16 that was the
        end of it: the next seed found warm-up off with nothing before it (`before` None), and
        arriving to find warm-up off is not a disable, so it stayed off. So a restore records
        its target in the per-valve options while it is owed (`CONF_WARMUP_RESTORE_PENDING`,
        kept through `_set_pending_restore`), and the seed takes it up again — provided the
        mode is still off and auto-restore still on. Anything else means it is no longer
        wanted, and the record goes. A seed that read no mode at all decides nothing.

        It then runs as any restore does — the delay, every check again, the wait for the
        water — so a seed of unknown age costs nothing by the time it writes.
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

        Written only on a change: each write is a config-entry update. Never cleared by a
        restore task being cancelled — that is the unload this record exists to outlive —
        only by the restore being done, or no longer wanted.
        """
        if self._valve.option(CONF_WARMUP_RESTORE_PENDING) != target:
            self._valve.set_option(CONF_WARMUP_RESTORE_PENDING, target)

    async def async_set_warmup(self, mode: str) -> None:
        """Set the valve's warmup mode. **This does not run water now.**

        Warmup is a stored mode, not an action: an enabled mode means the valve warms up by
        itself at the start of a session. The setting persists — it survives a power cycle,
        and the valve re-announces it about 4 s after every boot.

        ``mode`` must be one of ``WARMUP_MODES_CURRENT`` — the three the current Konnect app
        offers. The two legacy delayed-start values are rejected here rather than passed
        through: the valve may still be *holding* one, and `select.py` shows it when it is,
        but nothing knows what their delay does and writing one would be guessing.

        ⚠️ **Refused while water is running.** The Konnect app checks whether any outlet on
        either valve is on and silently reverts its own control rather than calling the API;
        this mirrors that check, but says so instead of reverting. Whether the *device*
        enforces it is untested — the guard is client-side in the app, so a write during a
        shower might land, might be ignored, and there is no way to tell which from the
        response. Raising keeps us on the app's side of a question nobody has answered.
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
        # Whatever restore was owed is settled by this write: it is either that restore's
        # own, or the owner choosing a mode here — `Off` included, which a restore finished
        # after a restart must not then overwrite.
        self._set_pending_restore(None)
        with self._valve.coordinator.command_errors(VALVE_OFFLINE):
            await self._valve.gcs.async_set_warmup(mode)

        # Read the field back rather than trusting the write. A 200 here means the *cloud*
        # accepted the command; the valve can still ignore it, and does when warmup is
        # disabled on the fixture itself. `warmUpState.warmUp` is the device's own answer.
        #
        # ⚠️ **The first read is too early and will disagree.** Measured live 2026-08-20:
        # `gcs-state` still returned the OLD mode immediately after a successful POST and
        # only caught up by t+3 s, while the valve's own `GCS_WARM_STS` echo landed at
        # +3.42 s. So a single immediate read-back reports a false mismatch on every write.
        # Hence the retries: disagreement only means something after the device has had a
        # few seconds to answer.
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
            # Past the settle window, so this is a real disagreement rather than lag. The
            # known cause is warmup being disabled on the fixture itself, where the cloud
            # accepts the command and the valve ignores it.
            _LOGGER.warning(
                "Warmup mode was set to %s but the valve still reports %s after %.0f s. The "
                "cloud accepted the command and the valve did not apply it — the usual cause "
                "is warmup being disabled on the fixture. Nothing has been retried.",
                mode,
                confirmed,
                sum(WARMUP_READBACK_DELAYS),
            )

    async def async_read_warmup_mode(self) -> str | None:
        """Read the warmup mode from the REST API and apply it, returning what it said.

        The source is `gcs-state`'s `warmUpState.warmUp` — the same field the Konnect app
        reads, and the same read that seeds this at setup and on every MQTT reconnect. It
        carries the mode axis, and the mode is all this applies.

        **The mode, and nothing else in the reply** (2026-10-16). This read exists to
        confirm a mode write, and it used to apply the whole of `gcs-state` — zone words
        included — whenever no push had landed during it, putting back whatever the cloud
        document still said about outlets the valve had since changed. And any push voided
        it, an ordinary status report included; now only one carrying the warm-up mode
        does (`_push_serials["warmup_mode"]`).

        🚨 **Not the running flag beside it** (`warmUpState.state`). The document trails the
        valve by seconds, and a read-back a few seconds into the warm-up its own write set
        off said "not in progress" over the valve's report that it was — which switched
        off the guard that keeps a change made during a warm-up from turning it into a
        shower on every warm-up outlet (`Valve._warming_up`). The valve reports the flag
        with every status message (`warmUpStatus`), so nothing is lost by leaving it to
        them.

        ⚠️ **REST reads here are partly cached** — `amplifierSettings.monoVolume` famously
        did not follow a live change. So this is authoritative about
        what Kohler's cloud believes, which is not always what the valve did a second ago.
        The device's own push, `GCS_WARM_STS`, remains the final word and arrives by itself.

        Returns `None` if the read failed or carried no warmup field — never a guess, since
        "Off" and "we could not tell" are different answers and only one of them is safe to
        show on a control.
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
            # CLOUD CONNECTION WATCH — the fourth free read. A warmup write reads this back
            # up to three times; each one carries `connectionState` and would otherwise
            # discard it. `requested_at` lets a valve message that lands during the read
            # outrank a `Disconnected` in it.
            self._valve.cloud_watch.note_rest_payload(
                payload, "warmup read-back", requested_at=requested_at
            )
        state = payload.get("state") if isinstance(payload, dict) else None
        warm = state.get("warmUpState") if isinstance(state, dict) else None
        if not isinstance(warm, dict):
            # No warm-up fields, so nothing to apply and no opinion on the write.
            return None
        if pushes["warmup_mode"] != mode_pushes:
            # The valve pushed its warm-up mode while this read was in flight, so the reply
            # is the older of the two — the same rule as the seed in `coordinator.py`
            # (2026-10-16). Applying it would put back the mode the push replaced, so this
            # read has no opinion; the caller's next read, or the push itself, answers.
            return None
        gcs_state = self._valve.gcs_state
        mode = warm.get("warmUp")
        if mode:
            gcs_state.warmup_mode = str(mode)
        # Same notification path as an MQTT update, so the dropdown lands on the confirmed
        # value and drops its optimistic guess exactly as it would on a device push.
        self._valve.coordinator.async_refresh_entities()
        # What *this read* said, not the mode cached before it: a read with no warmup field
        # has no opinion, and must not be taken for the valve disagreeing with a write.
        return gcs_state.warmup_mode if mode else None

    @property
    def auto_restore(self) -> bool:
        """Whether to put the warmup mode back after something else disables it.

        Read live from the entry options, so the switch takes effect immediately. Off unless explicitly enabled.
        """
        return bool(self._valve.option(CONF_WARMUP_AUTO_RESTORE, False))

    @property
    def last_mode(self) -> str | None:
        """The last *enabled* warmup mode seen on the valve, or None if we have never seen one.

        Persisted in the entry options so a restore after a Home Assistant restart reinstates
        the mode the fixture actually had. `None` is a real answer and is treated as one: with
        no prior, auto-restore does nothing rather than picking a default, because "all
        outlets" and "selected outlets" are different fixtures' worth of water.
        """
        stored = self._valve.option(CONF_LAST_WARMUP_MODE)
        return stored if stored in WARMUP_MODES_CURRENT else None

    def _message_window(self, since: float, until: float | None = None) -> list[dict]:
        """Messages between two monotonic instants, oldest first, without the clock field."""
        # This valve's own messages and every controller's — never another valve's.
        # The controller ones matter: `SYSTEM_STS: SYSTEM_READY` is the most
        # distinctive marker seen around a disable, and it is a controller message.
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
        """Append to the warmup journal, and to any active Report Log.

        The journal file opens on first use. The Report Log copy is written
        **independently of the standalone journal** — that one has its own enabled flag, and
        a decision must reach an active report whether or not the dedicated warm-up journal
        is switched on. One switch, one attachment; see `report_log.ReportLog.note`.

        Both `note`s only queue the record — this runs on the loop — and `prepare` writes
        it in an executor, opening the file first if need be.
        """
        tagged = self._valve._tagged(fields)
        report_log = self._valve.coordinator.report_log
        if report_log is not None:
            report_log.note("warmup", event, dict(tagged))
            # `note` never touches the file — it runs on the loop. Same deferred write the
            # warm-up journal below uses.
            if report_log.wants_open:
                self._valve.hass.async_add_executor_job(report_log.prepare)
        if self._valve.warmup_log is None:
            return
        self._valve.warmup_log.note(event, **tagged)
        if self._valve.warmup_log.wants_open:
            self._valve.hass.async_add_executor_job(self._valve.warmup_log.prepare)

    async def _async_journal_warmup_context(self, at: float) -> None:
        """Record what arrived *after* a disable.

        Separate from the disable record because the most distinctive marker seen so far —
        `SYSTEM_STS: SYSTEM_READY` — landed 7 to 9 s afterwards in the two clearest of the
        four known cases. A record written at the moment of the disable cannot contain it.
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
        """Record an enabled mode as what auto-restore should reinstate.

        Called from three directions, because any one of them alone leaves a gap:

        * **The valve announcing a mode over MQTT.** Alone, it forgets a mode chosen while
          the stream was down.
        * **A write of ours confirming.** Alone, it forgets a mode set from the Konnect app
          or the touchscreen — and those are most of them.
        * **The REST seed at setup** (``_async_seed_state``). Alone, neither of the other two
          knows about a mode that was simply already in force when the integration started.
          Added 2026-08-21: its absence cost a seven-hour unrestored disable on 08-20, since
          a mode read but never announced left auto-restore with no target.

        Seeing an enabled mode can also end a fight in progress — but only once our last
        restore has had the settle window to prove it stuck. Within it, the enabled mode is
        most likely that restore's own confirmation or echo, and resetting on it kept the
        counter at one: a fight never reached the limit and restored once a minute for ever.

        And it settles any restore still owed: warm-up is on, so there is nothing to put back.
        A disable after this schedules its own.
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
        """How long ago we last wrote a warmup mode, and whether ``after`` was that write.

        The pair every warmup observation needs, whichever channel it arrived on:
        ``write_age`` feeds ``should_restore_warmup``'s self-write grace, and ``ours`` is
        the journal's answer to "did we do this?" — true only when the observed mode matches
        the mode we wrote and the write is recent enough to be the cause. For our own `Off`,
        recent enough means nothing has enabled warm-up since; see `_our_off_stands`.
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

        "Seen" means the valve announcing one over MQTT, or a seed finding one beyond the
        echo's grace — never a read-back, which can still show the mode our write replaced.
        True when we have written nothing, so that it never excuses anything on its own.
        """
        written = self._warmup_self_write_at
        if written is None:
            return True
        seen = self._enabled_seen_at
        return seen is not None and seen > written

    def _our_off_stands(self) -> bool:
        """Whether our last warm-up write was `Off`, and nothing has enabled warm-up since.

        ⚠️ **Our `Off` is ours until something turns warm-up back on**, not just for the
        echo's thirty-second grace (2026-10-16). Chosen while the stream was down, with a
        read-back that failed or lagged, the `Off` is first seen by the reconnect's reseed —
        which can be long after the grace — and that reseed read it as somebody else
        disabling warm-up, scheduled a restore after the write, and so got past the check in
        `_async_restore_warmup` that only covers an `Off` written during the wait: the
        owner's `Off` was overwritten a minute later.
        """
        return (
            self._warmup_self_write_mode == WARMUP_DISABLED
            and not self._enabled_seen_since_write()
        )

    def _note_unwritable_disable(self, before: str | None, *, ours: bool) -> None:
        """Say why a disable out of a mode this integration does not write is left alone.

        `should_restore_warmup` refuses such a disable (2026-10-16): the delayed-start modes
        are decodable, not writable, and a restore of one used to be decided for and then
        refused by `async_set_warmup` — a `restore_failed` on every hub sign-in, each counted
        toward giving up. Nothing is written instead, and the owner is told once.

        A restore already waiting is told too: the most recent disable took away this mode,
        so what that restore would write is no longer what was taken away.
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
        """Spawn one restore task, or record why not.

        Shared by both callers — the MQTT announcement path and the reseed discovery path —
        because two restores in flight would race `async_set_warmup` against itself over a
        single field, and the journal should say a second trigger arrived rather than let
        the tasks interleave silently.
        """
        if (
            self._warmup_restore_task is not None
            and not self._warmup_restore_task.done()
        ):
            # Folded into the pending restore, but not forgotten: this disable took away a
            # newer mode than the one that started the wait — and that is what is owed now,
            # should a restart or reload end the wait first.
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
        """React to the valve announcing a new warmup mode.

        Two jobs: remember any enabled mode as the restore target, and notice a transition
        *into* disabled that this integration did not cause.

        ``announced`` says this envelope was a `GCS_WARM_STS` — the valve volunteering its
        mode — as opposed to any of the other messages that reach this method unchanged.
        It only matters when the mode did **not** move; a change can come from nowhere else,
        since `_apply_warmup` is the only envelope handler that writes `warmup_mode`.
        """
        record = journal_event(before, after, announced=announced)
        if record is None:
            return

        # Computed before the branch because **an announcement needs `ours` just as much as a
        # transition does** — see the note on the `announced` record below.
        write_age, ours = self._warmup_write_status(after)

        if after != WARMUP_DISABLED:
            # The valve's own word that warm-up is on — what our `Off` is judged against.
            # A restatement counts as much as a change: after our `Off`, it says the `Off`
            # did not take, or something has since turned warm-up back on.
            self._enabled_seen_at = time.monotonic()

        if record == "announced":
            # The valve restating a mode it is already in.
            #
            # ⚠️ **Most of these are our own dropdown writes, and the journal has to say so.**
            # Measured live 2026-08-21: `async_set_warmup` reads the mode back over REST at
            # `WARMUP_READBACK_DELAYS = (0.0, 2.0, 4.0)`, and the first of those is immediate,
            # so `apply_rest_state` has already moved `warmup_mode` by the time the valve's
            # own echo lands ~3.4 s later. `before == after`, and what would have been a
            # `mode` record with `ours: true` arrives here instead. Two dropdown changes that
            # evening produced exactly this, at +0.81 s and +0.33 s after their readbacks.
            #
            # Without `ours` the whole class is indistinguishable from the valve volunteering
            # its state — which is the one distinction §3e's open question turns on.
            #
            # Deliberately does not fall through to the disable path: a repeat of
            # `warmUpDisabled` is not a fresh disable, and `should_restore_warmup` would
            # refuse it anyway — but relying on that implicitly is how a restore loop starts.
            self._warmup_journal("announced", mode=after, ours=ours, source="mqtt")
            return

        # Every announcement is journalled, not only the disables. Establishing what a normal
        # week looks like is half of recognising the abnormal event.
        #
        # ⚠️ **That sentence was false from the day it was written until 2026-08-21.** Only
        # *changes* reached this line; a repeat returned above, and the mode a file opened on
        # was never recorded at all. Both are covered now — the `announced` record above and
        # the `baseline` record in `async_setup` — so it is true as stated. Check all three
        # before trusting it again.
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
        # The after-window is worth having whether or not we restore — an unrestored disable
        # is the cleaner observation of the two, since nothing of ours is in the way.
        self._valve._track(self._async_journal_warmup_context(now))
        self._note_unwritable_disable(before, ours=ours)

        if not restoring:
            return
        self._schedule_warmup_restore(before)

    async def _async_restore_warmup(self, taken_away: str | None = None) -> None:
        """Wait out the delay, re-check, and put the mode back.

        Re-checks rather than cancels: during the wait the mode may have been re-enabled by
        hand, the switch turned off, or `Off` chosen here on purpose. Every one of those
        means do nothing, and asking at the end is simpler than keeping a cancellation path
        correct for each. A shower started meanwhile means *not yet*: the restore waits for
        the water to stop, then waits the delay again and asks all of it again.

        ``taken_away`` is the mode the disable moved *away* from, and it is the restore
        target. ``should_restore_warmup`` refuses unless ``before`` is a known enabled mode
        this integration writes, so whenever a restore is scheduled that value is present
        and is the most current answer available — more current than ``last_warmup_mode``,
        which is a persisted memory that can be older or, before 2026-08-21, absent entirely.

        ⚠️ **It used to restore to ``last_warmup_mode`` alone, and that had a seven-hour
        failure on 2026-08-20.** The valve was disabled out of
        ``warmUpAllOutletsWithNoStartDelay``; the decision function said restore; the journal
        recorded ``"before": "warmUpAllOutletsWithNoStartDelay"`` and ``"restores_to": null``
        in the same entry — because no enabled mode had been *announced* during that
        integration session, only read over REST at setup. The mode being taken away was in
        hand the whole time and was thrown away. Seeding from REST (see
        ``_async_seed_state``) closes the same gap from the other side.

        From being scheduled until it writes, or is no longer wanted, the target is kept in
        the per-valve options, so a restart or reload — which cancels this task — does not
        lose it; see `_resume_pending_restore`. Cancellation itself leaves that record alone.
        """
        if taken_away is not None:
            self._warmup_taken_away = taken_away
        target = restore_target(taken_away, self.last_mode)
        if target is None:
            # Now unreachable for a genuine disable — kept because it is cheap, and because
            # a future caller that passes nothing should say so rather than write a default.
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
            # **This is where giving up actually happens**, and until 0.8.1 it happened
            # silently — the warning lived below, behind a `>` test on a counter this gate
            # stops at `>=`, so it could not fire. Something rewriting the valve's warmup
            # mode is exactly what the owner needs told, and it was only ever written to the
            # journal nobody reads until they already suspect a problem.
            #
            # Latched so a persistent fight logs once rather than every time it recurs; the
            # latch clears in `_remember_warmup_mode` beside the counter, so a fight that
            # genuinely stops and restarts is reported again.
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
                # Someone got there first. Nothing to do, and saying so beats a silent
                # return when the owner is watching the log to see whether this works.
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
            # ⚠️ **`Off` chosen here during the wait is deliberate, and final.** The mode
            # still reads disabled, so the check above passes — and until 2026-10-16 the
            # restore then wrote the old mode straight over the owner's choice. Our own
            # `Off` written after this restore was scheduled outranks the disable that
            # scheduled it.
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
            # …and so is one chosen before it, while nothing has turned warm-up back on
            # since: the disable this restore would undo is that `Off`, seen late. See
            # `_our_off_stands`; the decision to restore makes the same check, so this
            # catches only what got past it.
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
            # ⚠️ **Water running defers the restore; it does not end it.** `async_set_warmup`
            # refuses during a shower, and until 2026-10-16 that refusal was logged as
            # `restore_failed` with a note that "the next disable will schedule another" —
            # which never comes: the mode is already off, and a reseed sees disabled ->
            # disabled. The refusal also counted toward the give-up limit. Now nothing is
            # attempted or counted until the water has stopped and stayed stopped for the
            # usual delay (the running flag flickers as a shower winds down), and then
            # every check above is made again. Still this one tracked task, so an unload
            # cancels it and a disable meanwhile folds into it.
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

        # Chosen again now rather than before the wait: a disable folded into this one may
        # have taken away a newer mode, and `restore_target` trusts the mode taken away
        # precisely because it is the most current answer. That includes one this
        # integration does not write (see `_note_unwritable_disable`): then nothing goes
        # back, rather than the older mode this restore was scheduled for.
        #
        # Nothing awaits between the running check above and the one `async_set_warmup`
        # makes, so a write refused for running water cannot reach the counter below.
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
            # Not retried. Running water is waited out above, so what lands here is Kohler
            # or the valve refusing the write — offline, a rejected sign-in, an error — and
            # a write that was actually sent counts toward the give-up limit.
            _LOGGER.warning("Warmup auto-restore could not write: %s", err)
            self._warmup_journal("restore_failed", target=target, error=str(err))
            # Not retried after a restart either. A write that was sent cleared the record
            # already (`async_set_warmup`); this covers one refused before it was sent.
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

        Woken by every coordinator update, as `Valve._async_keep_on_after_warmup` is, and
        reading only the valve's own state. No timeout: a shower can last as long as it
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
