"""Keep a custom shower running past the valve's warm-up pause. Pure decision logic.

With warm-up enabled, a write that opens an outlet first runs the warm-up. When the water
is warm the valve pauses for two minutes (``0x40`` on both zones, outlets cleared) and,
unless someone picks outlets at the wall, the session ends there. So a write from Home
Assistant on such a valve never produces a running shower by itself. When ``custom_shower``
asks to keep the shower on, the coordinator feeds every GCS report to
:meth:`WarmupResume.observe`, which says when to re-send the caller's own words, once.
Only the GCS stream is read: the controller's ``showerwarmup`` flag does not track the
valve's warm-up.

⚠️ A pause is not proof the warm-up finished. The Konnect app and the first-generation
touchscreen stop with the same word, ``0x40`` and no outlets (``docs/protocol/gcs_valve.md``
§3.1). So a resume also needs the primary word's at-temperature bit
(``ValveWord.at_temperature``, byte 0 ``0x04``) on a report while the warm-up ran or as it
ended. Without it the pause is left alone, with a WARNING, so a valve that never sets the
bit shows up in the log.

Decisions:

* ``WAIT`` — nothing to do yet, including a report just after the warm-up that still shows
  some or all of its own outlets, within the settle window;
* ``NO_WARMUP`` — no ``warmUpInProgress`` within the window after the write (warm-up
  disabled, or the water already warm), so the shower is running as written;
* ``RESUME`` — the warm-up ran, reached temperature and paused: re-send the caller's words;
* ``ABANDON`` — a pause with no at-temperature evidence (``Outcome.warn`` set), a plain
  stop, outlets outside the warm-up's own running unpaused (someone picked them at the
  wall), the warm-up's outlets running unpaused past the settle window, or a deadline.

Once someone has picked outlets the decision is already ``ABANDON``, so a later pause of
theirs cannot be mistaken for the warm-up's. Terminal decisions are sticky, so ``RESUME``
is returned at most once. This never delays or gates the original write or any other; it
re-sends only what its own caller sent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

# How long after the write to wait for `warmUpStatus`. The valve's first report, which
# already carries it, arrives within about 2 s of the REST call returning; 10 s allows for a
# slow cloud. This and `RESUME_DEADLINE_SECONDS` also decide whether a warm-up in progress
# is the one Home Assistant's last words started, and so whose outlets a command sent during
# it keeps (`Valve._warmup_shower`).
WARMUP_WINDOW_SECONDS = 10.0
# Well past the longest warm-up measured (about 70 s). A warm-up still running this long
# after the write is something else, and this watcher should not act on it.
RESUME_DEADLINE_SECONDS = 180.0
# The valve pauses within 0.2 s of `warmUpStatus` clearing; picking outlets at the wall
# takes seconds. Unpaused outlets for longer than this mean someone took over.
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
        # Whether the valve reported reaching temperature during this warm-up or as it
        # ended: the only thing that tells its pause from a Stop.
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
        """Whether unpaused masks are within the warm-up's own outlets.

        The valve can clear ``warmUpStatus`` a report before it pauses, showing all or part
        of the warm-up set. An outlet outside the set is a person. A person picking one
        inside it looks the same until the settle window passes — an accepted risk, since
        the valve's own pause follows within 0.2 s while a pick and then a pause are two
        actions by hand.
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
        (`GcsState.at_temperature`, system-level); ``None``, ``False`` or omitted is no
        evidence, so a caller that does not pass it never gets ``RESUME``.
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
