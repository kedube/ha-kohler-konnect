"""Keep a custom shower running past the valve's warm-up pause. Pure decision logic.

**What the valve does on its own.** With warm-up enabled, every complete write that opens an
outlet first runs the warm-up set — on the reference install zone 1 outlets 1–3 and zone 2
outlets 1–2, masks ``0x07``/``0x03`` — and when the water is warm the valve **pauses for
two minutes**, just as it always does (``0x40`` on both zones, assignment cleared). A person
may pick outlets at the wall inside those two minutes; otherwise the pause self-terminates. So a write from Home Assistant
on such a valve has never produced a running shower by itself: it warms up, pauses, and ends.

**Corpus, 2026-08-07 → 2026-09-06, 33 valve warm-ups, 31 natural endings** (two were
interrupted by Home Assistant writes and are excluded):

* the pause bit ``0x40`` appears on at least one zone in **30 of 31**, on both zones within
  0.2 s in 29 of 31 — 24 immediately (the two ``custom_shower`` runs of 2026-09-06
  included), 5 after a transitional report 0.1–0.2 s earlier that
  still showed the warm-up set (or zone 2's half of it) with ``warmUpStatus`` already cleared;
* the one ending with no pause bit was a **restart from the wall** — a fresh warm-up began
  14 s later — and reads as a plain stop, masks ``0x00`` with no ``0x40``.

The controller's ``showerwarmup`` flag never coincides with the valve's ``warmUpStatus``
(0 of 30, 0 of 50 in session 24), so this reads the **GCS stream only**.

🚨 **A pause is not proof the warm-up finished.** The Konnect app and the first-generation
touchscreen *stop* with exactly that word — ``0x40``, no outlets (`docs/protocol/gcs_valve.md`
§4.1) — so a Stop pressed during the warm-up reads, report for report, like the warm-up's
natural end. Until 2026-10-16 any pause after a warm-up resumed, which re-sent the shower over
that Stop and put the water back on. No capture of a mid-warm-up manual stop exists to tell
them apart by timing, so the evidence asked for is the valve's own: the primary word's
**at-temperature** bit (``ValveWord.at_temperature``, byte 0 ``0x04``), seen on a report while
the warm-up ran or on the reports where it ended. A warm-up ends because the water is warm; a
Stop pressed before then has not got there. Without that bit the pause is left alone, and the
reason says why at WARNING, so a pause the valve never marks this way shows up in the log
rather than as a shower that quietly stopped resuming.

**How this is used.** ``custom_shower`` fires its write ONCE, so it can never disrupt the
warm-up. When the caller ticks "No pausing warm-up", the coordinator feeds every GCS
report to :meth:`WarmupResume.observe`. The decisions:

* ``WAIT`` — nothing to do yet, including a transitional report after the warm-up that still
  shows its own outlets, or only some of them (zone 2's half, say), within the settle window;
* ``NO_WARMUP`` — no ``warmUpInProgress`` within the window after the write (warm-up disabled,
  or the water was already warm), so the shower is simply running as written;
* ``RESUME`` — the warm-up was seen, the valve reported reaching temperature, and the valve
  has now paused: re-send the caller's own words, once;
* ``ABANDON`` — the warm-up ended in a pause with no at-temperature evidence (possibly a Stop
  from the app or the touchscreen; ``Outcome.warn`` is set), or in a plain stop (someone
  stopped it), or the outlets changed hands (a report after the warm-up shows outlets outside
  the warm-up's own, unpaused — someone picked them at the wall), or the warm-up's own outlets
  kept running unpaused past the settle window, or a deadline passed.

The "changed hands" rule is what keeps a later pause from being mistaken for the warm-up's:
once a person has picked outlets, anything they do afterwards — including pausing — is theirs,
and the decision is already ``ABANDON`` and sticky by then.

Terminal decisions are sticky, so ``RESUME`` can only be returned once.

This is **not** a write baseline, does not gate any other write, and never delays the
original write — the owner's 2026-09-06 constraints. It re-sends only what its own caller
sent, only after the pause that ends a warm-up its own write started.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

# The valve's first echo lands 1.1–2.1 s after the REST call returns, and it already carries
# `warmUpStatus`. Ten seconds is five times that, with room for a slow cloud.
WARMUP_WINDOW_SECONDS = 10.0
# Corpus maximum warm-up is 69 s. If the valve is still warming up this long after the write,
# something else is going on and this watcher should not be the thing that acts on it.
RESUME_DEADLINE_SECONDS = 180.0
# After `warmUpStatus` clears, the pause follows within 0.2 s in every natural ending that
# showed a transitional word. A person picking outlets at the wall takes seconds, so open
# outlets with no pause for longer than this mean the shower is being driven by hand.
SETTLE_SECONDS = 3.0


class Decision(Enum):
    """What the coordinator should do after one observation."""

    WAIT = "wait"
    NO_WARMUP = "no_warmup"
    RESUME = "resume"
    ABANDON = "abandon"


@dataclass(frozen=True)
class Outcome:
    """A decision with a one-line reason, worded for the log.

    ``warn`` marks a reason worth a WARNING rather than INFO: a decision taken on missing
    evidence, which someone reading the log should be able to find.
    """

    decision: Decision
    reason: str
    warn: bool = False

    @property
    def terminal(self) -> bool:
        return self.decision is not Decision.WAIT


class WarmupResume:
    """Decide, report by report, whether to re-send a custom shower after the warm-up pause.

    Times are seconds on any monotonic clock; ``started_at`` is when the write was sent.
    """

    def __init__(
        self,
        started_at: float,
        *,
        warmup_window: float = WARMUP_WINDOW_SECONDS,
        deadline: float = RESUME_DEADLINE_SECONDS,
        settle: float = SETTLE_SECONDS,
    ) -> None:
        self._started_at = started_at
        self._warmup_window = warmup_window
        self._deadline = deadline
        self._settle = settle
        self._warmup_seen_at: float | None = None
        self._ended_at: float | None = None
        # The masks as last reported while the warm-up ran. After it ends, unpaused outlets
        # outside these were picked by a person, not left by the warm-up.
        self._warmup_masks: tuple[int, ...] | None = None
        # Whether the valve said it had reached temperature during this warm-up or as it
        # ended — the only thing that tells its pause from a Stop. See the module docstring.
        self._at_temperature_seen = False
        self.outcome: Outcome | None = None

    @property
    def warmup_seen(self) -> bool:
        return self._warmup_seen_at is not None

    def _finish(
        self, decision: Decision, reason: str, *, warn: bool = False
    ) -> Outcome:
        self.outcome = Outcome(decision, reason, warn)
        return self.outcome

    def _transitional(self, masks: Sequence[int]) -> bool:
        """Whether unpaused masks are still the warm-up's own, or part of them.

        The valve can clear ``warmUpStatus`` a report before it pauses, and that report can
        show the whole warm-up set or only some of it — zone 2's half, or zone 1's. Anything
        within the set is the warm-up winding down; an outlet outside it is a person. A
        person who picks one *inside* it reads the same way until the settle window passes,
        and a pause at temperature inside that window would be resumed — the residual risk,
        accepted because the valve's own pause follows its transitional report within 0.2 s
        while a pick and then a pause are two actions by hand.
        """
        own = self._warmup_masks or ()
        if len(own) != len(masks):
            return False
        return all(mask & ~warm == 0 for mask, warm in zip(masks, own, strict=True))

    def observe(
        self,
        now: float,
        warmup_in_progress: bool | None,
        paused: Sequence[bool],
        masks: Sequence[int],
        *,
        at_temperature: bool | None = None,
    ) -> Outcome:
        """Feed the latest GCS report.

        ``paused`` and ``masks`` carry one entry per zone the model has. ``warmup_in_progress``
        is the valve's ``warmUpStatus`` as last reported; ``None`` (never reported) counts as
        not in progress. ``at_temperature`` is the primary word's at-temperature bit
        (`GcsState.at_temperature`, system-level); ``None`` or ``False`` is no evidence, which
        is also what leaving it out means — so a caller that does not pass it can never get
        ``RESUME``.
        """
        if self.outcome is not None:
            return self.outcome
        elapsed = now - self._started_at

        if warmup_in_progress:
            if self._warmup_seen_at is None:
                self._warmup_seen_at = now
            elif self._ended_at is not None:
                # It started again after ending: evidence from the first run says nothing
                # about how this one ends.
                self._at_temperature_seen = False
            self._at_temperature_seen |= bool(at_temperature)
            self._warmup_masks = tuple(masks)
            self._ended_at = None
            if elapsed > self._deadline:
                return self._finish(
                    Decision.ABANDON,
                    f"the warm-up is still running {elapsed:.0f} s after the command; "
                    "not resuming",
                )
            return Outcome(Decision.WAIT, "warm-up in progress")

        if self._warmup_seen_at is None:
            if elapsed >= self._warmup_window:
                return self._finish(
                    Decision.NO_WARMUP,
                    f"no warm-up within {self._warmup_window:.0f} s of the command, so "
                    "the shower is running as written; nothing to do",
                )
            return Outcome(Decision.WAIT, "waiting to see whether the valve warms up")

        # The warm-up was seen and the valve now says it is over.
        if self._ended_at is None:
            self._ended_at = now
        # The reports where it ends count too: the bit may land with the last of them.
        self._at_temperature_seen |= bool(at_temperature)
        since_warmup = now - self._warmup_seen_at
        if any(paused):
            if not self._at_temperature_seen:
                return self._finish(
                    Decision.ABANDON,
                    f"the valve paused {since_warmup:.0f} s after the warm-up began "
                    "without reporting that the water had reached temperature; that "
                    "pause may be a Stop pressed in the Konnect app or on the "
                    "touchscreen, which sends the same word, so not resuming",
                    warn=True,
                )
            return self._finish(
                Decision.RESUME,
                f"the warm-up reached temperature and paused the valve "
                f"{since_warmup:.0f} s after it began",
            )
        if not any(masks):
            return self._finish(
                Decision.ABANDON,
                "the warm-up ended in a plain stop rather than a pause, so someone "
                "stopped the shower; not resuming",
            )
        if not self._transitional(masks):
            return self._finish(
                Decision.ABANDON,
                "outlets other than the warm-up's own are running with no pause, so "
                "someone picked them at the wall; not resuming",
            )
        if now - self._ended_at > self._settle:
            return self._finish(
                Decision.ABANDON,
                "the warm-up's outlets kept running with no pause after the warm-up, so "
                "someone took over at the wall; not resuming",
            )
        if elapsed > self._deadline:
            return self._finish(
                Decision.ABANDON,
                f"no pause {elapsed:.0f} s after the command; not resuming",
            )
        return Outcome(Decision.WAIT, "warm-up over, waiting for the valve to pause")
