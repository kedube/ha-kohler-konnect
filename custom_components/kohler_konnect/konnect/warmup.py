"""Warm-up auto-restore decisions, as pure functions testable without Home Assistant.

The Anthem Plus hub's web UI writes ``warmUpDisabled`` on every signed-in use, and nothing
outside the hub's firmware can prevent it (``docs/protocol/gcs_valve.md`` §5), so
auto-restore puts the mode back. This module decides whether a disable should be undone
(``should_restore_warmup``), what to put back (``restore_target``) and which journal record
an observation gets (``journal_event``). Scheduling and writing are in
``warmup_manager.WarmupManager``.
"""

from __future__ import annotations

from .const import WARMUP_DISABLED, WARMUP_MODES_CURRENT

__all__ = ["journal_event", "restorable", "restore_target", "should_restore_warmup"]


def restorable(mode: str | None) -> bool:
    """Whether auto-restore can put ``mode`` back: an enabled mode this integration writes.

    Only the two with no start delay; the delayed-start pair are decodable, not writable
    (`WARMUP_MODES_LEGACY`).
    """
    return mode in WARMUP_MODES_CURRENT and mode != WARMUP_DISABLED


def should_restore_warmup(
    before: str | None,
    after: str | None,
    *,
    enabled: bool,
    self_write_mode: str | None,
    self_write_age: float | None,
    grace_seconds: float,
    enabled_seen_since_write: bool = True,
) -> bool:
    """Whether a warm-up mode change is a disable this integration should undo.

    ``before``/``after`` are the modes either side of one observation. ``enabled`` is the
    auto-restore switch. ``self_write_mode`` and ``self_write_age`` (seconds) describe our
    most recent warm-up write; ``grace_seconds`` is how long a matching disable counts as
    its echo; ``enabled_seen_since_write`` says whether the valve has been seen enabled
    since.

    True only for an externally caused transition into disabled:

    * ``before`` must be a mode :func:`restorable` accepts. A restatement is not a
      transition (the valve restates its mode about 4 s after every boot), and finding
      warm-up already off is not a disable: restoring then would enable a feature nobody
      asked for.
    * Not our own `Off`: within ``grace_seconds`` of writing it (the echo arrives about
      3.4 s later), or at any age while nothing has been seen enabled since — an `Off`
      chosen while the stream was down is first seen by the reconnect's reseed. Only a
      write of `Off` counts; our write of an enabled mode says nothing about a later
      disable.
    """
    if not enabled:
        return False
    # One branch per reason for refusing, so each can be matched against the journal.
    if after != WARMUP_DISABLED:
        return False
    if before is None or before == WARMUP_DISABLED:
        return False
    if not restorable(before):
        return False
    if (  # noqa: SIM103
        self_write_mode == WARMUP_DISABLED
        and self_write_age is not None
        and (self_write_age <= grace_seconds or not enabled_seen_since_write)
    ):
        return False
    return True


def restore_target(taken_away: str | None, remembered: str | None) -> str | None:
    """Which mode a restore should put back, or None.

    ``taken_away`` (the mode the disable moved away from) wins over ``remembered`` (the
    persisted ``last_warmup_mode``): it comes from the observation being acted on, while
    ``remembered`` can be older. Only a :func:`restorable` mode is returned. If
    ``taken_away`` is a delayed-start mode the answer is None, not ``remembered``, which is
    older and may be the other set of outlets.

    Invariant: whenever ``should_restore_warmup`` returns True, this returns a mode.
    """
    if taken_away and taken_away != WARMUP_DISABLED:
        return taken_away if restorable(taken_away) else None
    return remembered if restorable(remembered) else None


def journal_event(
    before: str | None, after: str | None, *, announced: bool
) -> str | None:
    """Which journal record one warm-up observation deserves, or ``None`` for none.

    ``before``/``after`` are the modes either side of applying one MQTT message.
    ``announced`` says that message was a ``GCS_WARM_STS``, the valve volunteering its mode.
    It matters only when the mode did not move, since ``_apply_warmup`` is the only envelope
    handler that changes ``warmup_mode``.

    * ``"mode"`` — the mode moved.
    * ``"announced"`` — the valve restated the mode it was already in. Common, and the
      journal's evidence of when the valve volunteers its state.
    * ``None`` — the message carried no mode, or moved nothing and was not an announcement
      (most valve messages).

    Invariant: an announcement carrying a mode is never silently dropped.
    """
    if after is None:
        return None
    if after == before:
        return "announced" if announced else None
    return "mode"
