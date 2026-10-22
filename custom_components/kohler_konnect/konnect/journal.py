"""A JSONL debug journal written beside the raw MQTT capture. Off by default.

One writer, parameterised by file prefix and README. It serves the **warmup journal**
(`warmup_*.jsonl`), which records every change to the valve's warmup mode with the
traffic around it; `WARMUP_README` describes the records.

Records stamp `ts` as ISO-8601 UTC with a `Z` suffix, the same clock and format as
`mqtt_raw_*.jsonl` in the same directory, so the two interleave by sorting on it.

Switched on permanently by the journal's `ENABLE_*` constant in `const.py`
(`ENABLE_WARMUP_DEBUG_LOG`), or until restart by `logger.set_level` with this module at
`debug` (`docs/mqtt/capture_runbook.md`). One file per Home Assistant run, each capped at
`max_bytes`, pruned to the newest `keep_files`.

Thread safety: `note()` runs on the event loop and never touches the file or the lock — it
queues the line and raises `wants_open`. Everything that does file I/O (`prepare`, `roll`,
`close`) runs in an executor and holds the lock, so the queue is written in order whichever
worker thread gets there first.
"""

from __future__ import annotations

import binascii
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

# This module's own logger doubles as the runtime switch, read as a flag rather than used
# for output — see `RawMqttLog.enabled` for why `.level` and not `isEnabledFor()`.
_SWITCH_LOGGER = _LOGGER

# Per file; matches `RAW_MQTT_LOG_MAX_BYTES`.
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
#: None keeps every file: the directory is diagnostic output, not a rotating buffer.
DEFAULT_KEEP_FILES: int | None = None
#: Records queued for `prepare()` to write. A handful arrive per shower.
_PENDING_MAX = 200

WARMUP_README = """\
Kohler Anthem — warmup journal
==============================

These `warmup_*.jsonl` files record every change to the Anthem valve's warmup mode, with
the MQTT traffic around it, so a change Home Assistant didn't make can be traced to its
source. The known source is the Anthem Plus controller's web UI, which writes
`warmUpDisabled` on every signed-in use (docs/protocol/gcs_valve.md §5).

{keep_desc}

Records
-------
  baseline          the first line of every file: the mode in force when the journal opened,
                    read over REST at setup, plus whether auto-restore is armed and what it
                    would restore to. The valve doesn't announce its mode on connect, so
                    without this line a file can't say what it started from.
  mode              the mode moved. `before` -> `after`, `ours` (did we write it), `restoring`,
                    and `source`: `mqtt` if the valve announced it, `rest` if a reseed found it
                    already changed — the move happened while the stream was down, so a
                    `rest` record has no `before_window`.
  announced         the valve restated a mode it was already in. Carries `mode` and `ours`.
                    Setting the mode from Home Assistant usually lands here rather than on a
                    `mode` record, because the write reads itself back over REST before the
                    valve's echo arrives. Check `ours` before reading one as the valve
                    volunteering.
  disabled          the mode went to `warmUpDisabled`. Carries `ours` (did we write it),
                    `restoring` (is auto-restore acting), and `before_window`: every MQTT
                    message seen in the {before}s leading up to it.
  context           written {after}s later, holding `after_window` — the messages that
                    followed. The window closes before auto-restore could act, so this
                    record never contains our own write.
  restore*          what auto-restore did: scheduled, deferred (water running — it writes
                    once the water has stopped), resumed (owed when Home Assistant stopped
                    or reloaded, and taken up again by the next seed), skipped, done, or
                    failed.

Reading them
------------
Every record has an ISO-8601 UTC `ts`, the same clock as the raw capture beside it, so the two
interleave:

    jq -c '{{ts, src:"warmup", event, mode, before, after, ours}}' warmup_*.jsonl > /tmp/a.jsonl
    jq -c '{{ts, src:"raw", topic}}' mqtt_raw_*.jsonl > /tmp/b.jsonl
    sort -m /tmp/a.jsonl /tmp/b.jsonl

What a controller web-UI disable looks like
-------------------------------------------
The `disabled` record, then the controller's five-snapshot burst about 3 s later, then
`READ_GCS_EXPERIENCE_STS` about 5 s later; `SYSTEM_STS: SYSTEM_READY` often follows within
7-9 s. The write itself never appears on MQTT, only the valve's echo. A `disabled` record
without that pattern points to a different writer.

Absence of a message means "nothing was pushed", never "nothing happened": MQTT here is the
Konnect app's channel, not device-to-device traffic.

Auto-restore
------------
A disable is recorded the same way whether the Warmup Auto-Restore switch is on or off. The
disable can't be prevented from outside the controller's firmware, so auto-restore is the
mitigation: it puts the mode back about a minute later. Our own writes are marked
(`ours: true`, and the `restore*` events).

Turning it off
--------------
Set ENABLE_WARMUP_DEBUG_LOG = False in const.py and restart Home Assistant Core.
"""


class DebugJournal:
    """Append diagnostic records to a JSONL file, when switched on."""

    def __init__(
        self,
        directory: str,
        *,
        forced: bool = False,
        keep_files: int | None = DEFAULT_KEEP_FILES,
        prefix: str,
        readme: str,
        readme_fields: dict[str, Any] | None = None,
        label: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        """`prefix` names the files and scopes pruning; `readme` is the note left beside them.

        Pruning matches on the prefix, so two journals in one directory never delete each
        other's files. `readme` is a `str.format` template; it may use `{keep_desc}` and
        any key in `readme_fields`.
        """
        self._directory = directory
        self._forced = forced
        self._keep_files = keep_files
        self._prefix = prefix
        self._readme = readme
        self._readme_fields = readme_fields or {}
        self._label = label
        # A backstop so a busy or misbehaving valve cannot grow one file without bound
        # (often on an SD card); normal volume is a handful of lines per shower.
        self._max_bytes = max_bytes
        self._written = 0
        self._lock = threading.Lock()
        self._handle: Any = None
        self._path: str | None = None
        self._announced = False
        # Set when `note()` queued a record, or found a switched-off file still open. See
        # `wants_open`.
        self._wants_open = False
        # Records waiting to be written by `prepare()`, oldest first. Bounded: if no
        # prepare() ever comes, the oldest are dropped rather than held for ever.
        self._pending: deque[str] = deque(maxlen=_PENDING_MAX)
        # Whether a file has ever been opened. See `prepare`.
        self._opened = False
        # Set by `close()`, which is final. See there.
        self._closed = False

    @property
    def enabled(self) -> bool:
        """True when the log is switched on, by either mechanism."""
        return self._forced or _SWITCH_LOGGER.level == logging.DEBUG

    @property
    def path(self) -> str | None:
        """The file currently being written, or None when not logging."""
        return self._path

    @property
    def wants_open(self) -> bool:
        """True when :meth:`prepare` should be called: a record is queued for it to write.

        `note()` runs on the event loop, where file I/O must not happen, so it only queues
        the record and raises this; the caller then schedules `prepare()` in an executor.
        Also raised when a record arrives after the log was switched off with a file still
        open, so `prepare()` can release it off the loop.
        """
        return self._wants_open

    def prepare(self) -> None:
        """Write what `note()` queued, opening a file first if there is none.

        The first call opens a file even with nothing queued, so an empty file shows the
        journal is on. Switched off, it writes what was queued while on and releases the
        file. Blocking file I/O: call from an executor. Does nothing after `close()`.
        """
        with self._lock:
            self._wants_open = False
            if self._closed:
                self._pending.clear()
                return
            if not self.enabled:
                # Here rather than in `note()`, which runs on the loop. Records queued
                # before the switch-off still belong.
                self._drain_locked(roll_full=False)
                self._close_locked()
                self._pending.clear()
                return
            if self._handle is None and not self._pending and self._opened:
                # Only the first call opens an empty file; a later one whose record an
                # earlier call already wrote has nothing to do.
                return
            if self._handle is None:
                try:
                    self._open_locked()
                except OSError as err:
                    _LOGGER.warning("%s could not open a file: %s", self._label, err)
                    self._pending.clear()
                    return
            self._drain_locked(roll_full=True)

    def note(self, event: str, **fields: Any) -> None:
        """Queue one decision or transition for :meth:`prepare`. Cheap no-op when off.

        Runs on the event loop, so never touches the file or the lock (`wants_open`). A
        no-op after `close()`.
        """
        if self._closed:
            return
        if not self.enabled:
            if self._handle is not None:
                # Switched off with a file open. Closing is file I/O; prepare() does it.
                self._wants_open = True
            return

        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "event": event,
        }
        # Rounded: monotonic-clock seconds carry float noise that only hurts readability.
        for key, value in fields.items():
            record[key] = round(value, 2) if isinstance(value, float) else value

        try:
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return

        # Serialised now, so the record is the state at this moment however late the
        # write. `deque.append` is atomic and `prepare()` pops under the lock, so order is
        # kept whichever executor thread writes.
        self._pending.append(line + "\n")
        self._wants_open = True

    def _drain_locked(self, *, roll_full: bool) -> None:
        """Write the queued records in order, into the open file.

        With ``roll_full`` a full file is rolled to a new one; without it (closing) nothing
        new is opened and what does not fit is dropped. Nothing is written when no file is
        open.
        """
        while self._pending:
            if self._handle is not None and self._written >= self._max_bytes:
                if not roll_full:
                    break
                try:
                    self._open_locked()
                except OSError as err:
                    _LOGGER.warning("%s could not open a file: %s", self._label, err)
                    self._pending.clear()
                    return
            if self._handle is None:
                break
            if not self._write_locked(self._pending.popleft()):
                return

    def _write_locked(self, encoded: str) -> bool:
        """Write one line to the open file. False, and switched off, if that failed."""
        try:
            self._handle.write(encoded)
            self._handle.flush()
            self._written += len(encoded.encode("utf-8"))
        except OSError as err:
            # A diagnostic must never take the integration down with it.
            _LOGGER.warning("%s write failed, disabling: %s", self._label, err)
            self._close_locked()
            self._pending.clear()
            self._forced = False
            return False
        return True

    def _open_locked(self) -> None:
        self._close_locked()
        self._opened = True
        self._written = 0
        os.makedirs(self._directory, exist_ok=True)
        self._write_readme()
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        suffix = binascii.hexlify(os.urandom(4)).decode("ascii")
        self._path = os.path.join(
            self._directory, f"{self._prefix}_{stamp}Z_{os.getpid()}_{suffix}.jsonl"
        )
        self._handle = open(self._path, "a", encoding="utf-8")  # noqa: SIM115 - handle outlives this call; closed by close()
        self._prune()
        if not self._announced:
            self._announced = True
            _LOGGER.info("%s is ON, writing to %s", self._label, self._path)

    def _write_readme(self) -> None:
        """Leave a note saying what these files are and how to stop them."""
        try:
            with open(
                os.path.join(self._directory, f"README-{self._prefix}.txt"),
                "w",
                encoding="utf-8",
            ) as fh:
                fh.write(
                    self._readme.format(
                        **self._readme_fields,
                        keep_desc=(
                            "No limit on the number of files — every one is kept."
                            if self._keep_files is None
                            else f"Only the newest {self._keep_files} are kept."
                        ),
                    )
                )
        except OSError:  # pragma: no cover - the log still works without it
            pass

    def _prune(self) -> None:
        """Keep the newest `keep_files` logs; a no-op when it is None (the default)."""
        if self._keep_files is None:
            return
        try:
            logs = sorted(
                (
                    os.path.join(self._directory, name)
                    for name in os.listdir(self._directory)
                    if name.startswith(f"{self._prefix}_") and name.endswith(".jsonl")
                ),
                key=os.path.getmtime,
            )
        except OSError:  # pragma: no cover
            return
        for stale in logs[: max(0, len(logs) - self._keep_files)]:
            try:
                os.remove(stale)
            except OSError:  # pragma: no cover
                pass

    def roll(self) -> str | None:
        """Start a new file immediately. Returns its path, or None if off or closed.

        Rolled together with the raw capture, so a pair of files covers the same
        experiment. Blocking file I/O: call from an executor.
        """
        if not self.enabled or self._closed:
            return None
        with self._lock:
            if self._closed:
                return None
            # Queued before the roll, so they belong to the file it closes.
            self._drain_locked(roll_full=True)
            self._close_locked()
            try:
                self._open_locked()
            except OSError as err:
                _LOGGER.warning("%s could not start a new file: %s", self._label, err)
                return None
            # Anything queued with no file open to take it starts the new one.
            self._drain_locked(roll_full=True)
            return self._path

    def close(self) -> None:
        """Write what is queued into the open file, then close it. For unload. **Final.**

        Records queued with no file open, or with the file full, are dropped rather than a
        file opened only to be closed. Holds the lock, so it waits out a `prepare()` already
        writing. Blocking file I/O: call from an executor.

        ⚠️ Nothing reopens a file through this object afterwards: `note()`, `prepare()` and
        `roll()` do nothing once it has run, so a late record or a `prepare()` racing the
        unload cannot leak a handle. A reload builds a new journal.
        """
        with self._lock:
            self._closed = True
            self._wants_open = False
            self._drain_locked(roll_full=False)
            self._close_locked()
            self._pending.clear()

    def _close_locked(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:  # pragma: no cover
                pass
            if self._announced:
                _LOGGER.info("%s is OFF (%s)", self._label, self._path)
                self._announced = False
        self._handle = None
        self._path = None
