"""Consumer-side raw MQTT capture, keyed to the "Report Log" switch.

Unlike `raw_log.py`, the development capture (one file per Home Assistant run), this is
the user's capture: one file per **episode**, from switch-on to switch-off, to attach to
an issue.

* Switch on starts a new file, named for the moment it was enabled.
* A Home Assistant restart does not split the episode: the coordinator persists the
  episode name in the config entry options, and `resume` reattaches to the same file in
  append mode.
* Switch off ends the episode; the next switch-on starts a fresh file.

Records use `raw_log.format_record`, so both captures share one format. A file that
reaches ``max_bytes`` continues in ``<episode>_p2.jsonl``, ``_p3``, … sharing the stem;
a restart reattaches to the latest part.

⚠️ The directory is inside the integration folder (``custom_components/kohler_konnect/
reports/``), so a HACS update or reinstall deletes it; `_README` warns the user.

Thread safety: paho calls `write()` on its network thread; `start`/`resume`/`stop`/
`prepare`/`close` run in an executor. The lock covers all file state. `note()` runs on the
event loop and takes no lock: it queues the decision for the next `prepare()` or
`write()`, whichever thread gets there first.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from .raw_log import format_record

_LOGGER = logging.getLogger(__name__)

# Episode names `start()` generates: `report_` plus a UTC stamp. `resume` rejects others.
_SAFE_STEM = re.compile(r"report_\d{8}T\d{6}Z")

DEFAULT_MAX_BYTES = 8 * 1024 * 1024

#: Decisions queued for `prepare()` to write. A handful arrive per shower.
_PENDING_MAX = 200

_PART_RE = re.compile(r"_p(\d+)\.jsonl$")

_README = """\
Kohler Konnect — report captures
====================================

Each report_*.jsonl file here is one capture episode of the raw Kohler MQTT
stream, recorded because the "Report Log" switch on the device page was on.
One file per switch-on; a Home Assistant restart continues the same file; a
file that reaches {max_mb} MB continues in _p2, _p3, ... parts.

One JSON object per line:

    ts           ISO-8601 UTC, when the message arrived
    topic        the MQTT topic
    payload      the payload text exactly as received (undecoded)
    payload_b64  present INSTEAD of payload when the bytes were not UTF-8
    qos, retain  from the MQTT message

Attach these files to a GitHub issue to document a bug — or a healthy run on
hardware the integration has never been verified against.

⚠️ Before sharing, know that these files contain your device identifiers and
show when your shower was used. Review anything you'd rather not publish.

⚠️ This folder is inside the integration itself, so updating or reinstalling
the integration DELETES it. Move files you want to keep somewhere else first.

Turning the switch off ends the capture; the files stay until you delete them
(or an update does).
"""


class ReportLog:
    """One raw-capture file per switch-on episode, append-through-restart.

    The coordinator owns the episode lifecycle: `start()` returns the episode name for it
    to persist, `resume(name)` re-attaches after a restart, `stop()` ends the episode, and
    `close()` merely releases the file handle at unload without ending the episode (the
    options key, not this object, is what says an episode is in force).
    """

    def __init__(self, directory: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._directory = directory
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._handle = None
        self._written = 0
        self._path: str | None = None
        self._stem: str | None = None
        # Set when `note()` queued a decision. See `wants_open`.
        self._wants_open = False
        # Decisions from `note()`, oldest first, waiting for `prepare()` or `write()`.
        # Bounded: if neither ever comes, the oldest are dropped rather than held for ever.
        self._pending: deque[str] = deque(maxlen=_PENDING_MAX)
        # Set by `close()`; only `start()` or `resume()` reattaches after it.
        self._closed = False

    @property
    def active(self) -> bool:
        """Whether an episode is being written by this object right now."""
        return self._stem is not None

    @property
    def wants_open(self) -> bool:
        """True when a decision is queued, so `prepare()` should be called from an executor.

        `note()` runs on the event loop, so it never touches the file or the lock (which
        paho's thread holds while it opens or rolls a part); it queues the decision and
        raises this, as `DebugJournal` does.
        """
        return self._wants_open

    def prepare(self) -> None:
        """Write the queued decisions, opening or rolling the episode's part as needed.

        **Blocking — call from an executor.** Does nothing without an episode, or after
        `close()`.
        """
        with self._lock:
            self._wants_open = False
            if self._stem is None or self._closed:
                self._pending.clear()
                return
            try:
                self._drain_locked()
            except OSError as err:
                # The handle goes; the episode and what is still queued stay, as in
                # `write()`, for when the disk comes back.
                _LOGGER.warning("Report log write failed: %s", err)
                self._close_locked()

    @property
    def path(self) -> str | None:
        """The file currently being written, or None."""
        return self._path

    def start(self) -> str:
        """Begin a new episode. Returns the episode name for the caller to persist.

        Opens the file now rather than at the first message, which can be hours away, so
        the user can see the capture started. Blocking I/O: call from an executor.
        """
        stem = "report_" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        with self._lock:
            self._close_locked()
            self._closed = False
            self._pending.clear()
            self._stem = stem
            try:
                self._open_locked()
            except OSError as err:
                _LOGGER.warning("Report log could not open a file: %s", err)
        _LOGGER.info("Report log ON — new episode %s", stem)
        return stem

    def resume(self, stem: str) -> str:
        """Reattach to a persisted episode after a restart, appending to its last part.

        Returns the episode now in force, which the caller must persist if it differs from
        `stem`. Blocking I/O: call from an executor. A deleted file is recreated by the
        append open; the episode name is the identity. A name `start()` could not have
        written starts a new episode instead: `_part_path` joins the name onto the
        directory, so a hand-edited `..` or separator would write outside it.
        """
        if not isinstance(stem, str) or not _SAFE_STEM.fullmatch(stem):
            _LOGGER.warning(
                "Ignoring a report-log episode name that is not one we could have "
                "written: %r. Starting a new episode instead.",
                stem,
            )
            return self.start()
        with self._lock:
            self._close_locked()
            self._closed = False
            self._stem = stem
            try:
                self._open_locked()
            except OSError as err:
                _LOGGER.warning("Report log could not reopen %s: %s", stem, err)
        _LOGGER.info("Report log resumed episode %s after restart", stem)
        return stem

    def stop(self) -> None:
        """End the episode, writing decisions still queued first. `start()` opens anew."""
        with self._lock:
            if self._stem is not None and not self._closed:
                try:
                    self._drain_locked()
                except OSError as err:
                    _LOGGER.warning("Report log write failed: %s", err)
            self._pending.clear()
            path = self._path
            self._close_locked()
            self._stem = None
        _LOGGER.info("Report log OFF (%s)", path)

    def close(self) -> None:
        """Release the file handle without ending the episode — for unload. **Final.**

        After a reload the new coordinator's `ReportLog` resumes the persisted episode.

        ⚠️ Nothing reopens the file through this object afterwards: `note()`, `write()` and
        `prepare()` do nothing once it has run, and only `start()` or `resume()`
        reattaches. Otherwise a late decision would leak a handle and put two writers on
        one file.

        Decisions still queued go into the part already open, without opening or rolling
        one. Blocking file I/O: called from a worker thread
        (`KonnectMqttStream._close_captures`) after paho's network thread has stopped.
        """
        with self._lock:
            self._closed = True
            self._wants_open = False
            while (
                self._pending
                and self._handle is not None
                and self._written < self._max_bytes
            ):
                try:
                    self._write_line_locked(self._pending.popleft())
                except OSError as err:
                    _LOGGER.warning("Report log write failed: %s", err)
                    break
            self._pending.clear()
            self._close_locked()

    def write(
        self, topic: str, payload: bytes, *, qos: int = 0, retain: bool = False
    ) -> None:
        """Record one message. Cheap no-op when no episode is active, or after `close()`."""
        if self._stem is None or self._closed:
            return
        line = format_record(topic, payload, qos=qos, retain=retain)
        if line is None:  # pragma: no cover - defensive
            return
        with self._lock:
            if self._stem is None or self._closed:
                return
            try:
                # Decisions queued before this message happened before it; keep that order.
                self._drain_locked()
                self._write_line_locked(line)
            except OSError as err:
                # A diagnostic must never take the stream down. Drop the handle but keep
                # the episode: the disk may come back, and a restart reattaches by name.
                _LOGGER.warning("Report log write failed: %s", err)
                self._close_locked()

    def note(self, journal: str, event: str, fields: dict[str, Any]) -> None:
        """Record one **decision** — what the integration concluded, not what arrived.

        Decisions share the file and clock with the raw messages, in order, so one
        attachment explains itself. A message has `topic`; a decision has `journal` (e.g.
        `warmup`) and `event`:

            jq -c 'select(.topic)'    report_*.jsonl   # the wire
            jq -c 'select(.journal)'  report_*.jsonl   # the reasoning

        Written whether or not the feature is switched on, as the standalone journal is,
        so a report with Auto-Restore off still shows a disable that was seen and left
        alone.
        """
        if self._stem is None or self._closed:
            return
        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "journal": journal,
            "event": event,
        }
        record.update(fields)
        try:
            line = json.dumps(record, default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return
        # ⚠️ Never touches the file or the lock: this runs on the event loop. Queue the
        # line for `prepare()`; `write()` drains the queue first, so a message arriving
        # meanwhile still lands after the decisions made before it.
        self._pending.append(line)
        self._wants_open = True

    # ------------------------------------------------------------------ #
    # Locked internals
    # ------------------------------------------------------------------ #
    def _drain_locked(self) -> None:
        """Write the queued decisions in order, opening or rolling parts as needed.

        Raises `OSError` from the write; the line being written is lost with the handle,
        and the rest stay queued.
        """
        while self._pending:
            self._write_line_locked(self._pending.popleft())

    def _write_line_locked(self, line: str) -> None:
        if self._handle is None or self._written >= self._max_bytes:
            self._open_locked()
        assert self._handle is not None
        encoded = line + "\n"
        self._handle.write(encoded)
        self._handle.flush()
        # Flushed per line: reports are read while the problem is still happening.
        self._written += len(encoded.encode("utf-8"))

    def _part_path(self, part: int) -> str:
        name = f"{self._stem}.jsonl" if part == 1 else f"{self._stem}_p{part}.jsonl"
        return os.path.join(self._directory, name)

    def _latest_part(self) -> int:
        """The highest existing part number for this episode, or 1 if none exist yet."""
        assert self._stem is not None
        latest = 1
        try:
            for name in os.listdir(self._directory):
                if not name.startswith(self._stem):
                    continue
                if name == f"{self._stem}.jsonl":
                    latest = max(latest, 1)
                else:
                    match = _PART_RE.search(name)
                    if match and name == f"{self._stem}_p{match.group(1)}.jsonl":
                        latest = max(latest, int(match.group(1)))
        except OSError:
            pass
        return latest

    def _open_locked(self) -> None:
        """Open the episode's latest part for append, rolling to the next when full.

        Append mode is what lets `resume()` continue the same file across a restart.
        """
        self._close_locked(quiet=True)
        os.makedirs(self._directory, exist_ok=True)
        self._write_readme()
        part = self._latest_part()
        path = self._part_path(part)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size >= self._max_bytes:
            part += 1
            path = self._part_path(part)
            size = 0
        self._path = path
        self._handle = open(path, "a", encoding="utf-8")  # noqa: SIM115 - handle outlives this call; closed by close()
        self._written = size

    def _write_readme(self) -> None:
        try:
            with open(
                os.path.join(self._directory, "README.txt"), "w", encoding="utf-8"
            ) as fh:
                fh.write(_README.format(max_mb=self._max_bytes // (1024 * 1024)))
        except OSError:  # pragma: no cover - the capture still works without it
            pass

    def _close_locked(self, *, quiet: bool = False) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:  # pragma: no cover
                pass
        self._handle = None
        if not quiet:
            self._path = None
        self._written = 0
