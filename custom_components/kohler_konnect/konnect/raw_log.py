"""Raw MQTT capture — every payload paho hands us, before any decoding.

Grep marker: ``RAW MQTT LOG`` (this module, the constants in `const.py`, the call in
`mqtt.py:_on_message`, the wiring in `coordinator.py`). Off by default; removing the
marked blocks removes the feature.

The decoded `Envelope` keeps only `sku`, `deviceid`, `code` and `attributes`, so it cannot
say what Kohler actually sent. This writes each payload's text exactly as it arrived,
including payloads that are not valid JSON or UTF-8, which the decode path drops.

Two switches (`docs/mqtt/capture_runbook.md`):

* At runtime, until restart: `logger.set_level` with
  ``custom_components.kohler_konnect.konnect.raw_log: debug``. Setting it back to
  ``info`` stops capture at the next message. Only this logger's own level counts, not
  `logger.set_default_level` (see :attr:`RawMqttLog.enabled`).
* Pinned on across restarts: ``ENABLE_RAW_MQTT_LOG = True`` in `const.py`.

Switched off, it costs one check per message and creates nothing on disk.

Thread safety: paho calls `write()` on its own network thread, never the event loop, so the
file I/O here is already off-loop. The lock guards against a `close()` racing a write from
that thread — the unload's, from a worker thread (`KonnectMqttStream._close_captures`), or
`write()`'s own when capture is switched off.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import threading
import time
from datetime import UTC, datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

# This module's logger, whose level is the runtime switch. Nothing is logged through it at
# DEBUG, which keeps the capture out of `home-assistant.log`.
_SWITCH_LOGGER = _LOGGER

DEFAULT_MAX_BYTES = 8 * 1024 * 1024
#: None keeps every file: the directory is diagnostic output, not a rotating buffer.
DEFAULT_KEEP_FILES: int | None = None

_README = """\
Raw Kohler MQTT capture — written by the kohler_konnect integration.

Each .jsonl file is one capture session, one JSON object per line:

    ts       ISO-8601 UTC, when paho handed us the message
    topic    the MQTT topic it arrived on
    payload  the payload text exactly as received (undecoded)
    payload_b64  present INSTEAD of `payload` when the bytes were not valid UTF-8
    qos, retain  from the paho message

⚠️ Before sharing, know that these files contain your device identifiers and show when your
shower was used. Kohler device ids double as cloud addresses, so skim a capture before
attaching it to a public issue.

This capture is OFF by default. Right now it is on because:

{why}

Files roll at {max_mb} MB. {keep_desc}
Delete it freely — it is pure diagnostic output.
"""

_WHY_FORCED = """\
    ENABLE_RAW_MQTT_LOG = True

in the integration's const.py, which pins capture on across restarts.

TO STOP IT: set that constant back to False and restart Home Assistant.
The `logger.set_level` action will NOT turn it off while the constant is
True — the constant wins on purpose, so a restart cannot silently end a
capture you meant to keep running."""

_WHY_LOGGER = """\
    custom_components.kohler_konnect.konnect.raw_log

is set to debug. To stop it, call the `logger.set_level` action with that
same logger name set to `info` — no restart needed. Note this does not
survive a restart; to keep capture on across restarts, set
ENABLE_RAW_MQTT_LOG = True in the integration's const.py instead."""


def format_record(
    topic: str, payload: bytes, *, qos: int = 0, retain: bool = False
) -> str | None:
    """One capture line: ts/topic/qos/retain plus the payload exactly as it arrived.

    Shared with `report_log.py`, so both captures write the same records. The payload is
    not parsed: re-serialising would normalise key order, duplicates and number
    formatting, and an unparseable payload is the most interesting kind. None when the
    record cannot be serialised.
    """
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "topic": topic,
        "qos": qos,
        "retain": retain,
    }
    try:
        record["payload"] = payload.decode("utf-8")
    except UnicodeDecodeError:
        record["payload_b64"] = base64.b64encode(payload).decode("ascii")
    try:
        return json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


class RawMqttLog:
    """Append raw MQTT payloads to rolling JSONL files, when switched on.

    `forced` comes from `ENABLE_RAW_MQTT_LOG` and pins the capture on. Independently, the
    module logger sitting at DEBUG turns it on at runtime — checked per message so it can be
    flipped from the UI mid-session, in both directions.
    """

    def __init__(
        self,
        directory: str,
        *,
        forced: bool = False,
        max_bytes: int = DEFAULT_MAX_BYTES,
        keep_files: int | None = DEFAULT_KEEP_FILES,
    ) -> None:
        self._directory = directory
        self._forced = forced
        self._max_bytes = max_bytes
        self._keep_files = keep_files
        self._lock = threading.Lock()
        self._handle: Any = None
        self._written = 0
        self._path: str | None = None
        # Logged once each way, so flipping the switch is visible in the HA log without the
        # capture itself ever going there.
        self._announced = False

    @property
    def enabled(self) -> bool:
        """True when capture is switched on, by either mechanism.

        Reads `.level` on this exact logger, not `isEnabledFor()`, which inherits:
        debugging the whole integration, or `logger: default: debug`, must not start
        writing capture files. `.level` is `NOTSET` until set, so the default is off.
        """
        return self._forced or _SWITCH_LOGGER.level == logging.DEBUG

    @property
    def path(self) -> str | None:
        """The file currently being written, or None when not capturing."""
        return self._path

    def prepare(self) -> None:
        """Create the directory and open an empty capture file now, if switched on.

        Otherwise nothing appears until the first message, which can be hours away, and
        whoever switched capture on cannot tell it worked. Blocking file I/O: call from an
        executor.
        """
        if not self.enabled:
            return
        with self._lock:
            if self._handle is None:
                try:
                    self._open_locked()
                except OSError as err:
                    _LOGGER.warning("Raw MQTT capture could not open a file: %s", err)

    def write(
        self, topic: str, payload: bytes, *, qos: int = 0, retain: bool = False
    ) -> None:
        """Record one message. Cheap no-op when switched off."""
        if not self.enabled:
            # Release the file as soon as capture is switched off, not at unload.
            if self._handle is not None:
                self.close()
            return

        line = format_record(topic, payload, qos=qos, retain=retain)
        if line is None:  # pragma: no cover - defensive
            return

        with self._lock:
            try:
                self._write_line_locked(line)
            except OSError as err:
                # A diagnostic must never take the stream down with it.
                _LOGGER.warning("Raw MQTT capture write failed, disabling: %s", err)
                self._close_locked()
                self._forced = False

    def _write_line_locked(self, line: str) -> None:
        if self._handle is None or self._written >= self._max_bytes:
            self._open_locked()
        assert self._handle is not None
        encoded = line + "\n"
        self._handle.write(encoded)
        self._handle.flush()
        # Flushed per line: a capture is usually read while the thing being debugged is
        # still running.
        self._written += len(encoded.encode("utf-8"))

    def _open_locked(self) -> None:
        self._close_locked()
        os.makedirs(self._directory, exist_ok=True)
        self._write_readme()
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        suffix = binascii.hexlify(os.urandom(4)).decode("ascii")
        self._path = os.path.join(
            self._directory, f"mqtt_raw_{stamp}Z_{os.getpid()}_{suffix}.jsonl"
        )
        self._handle = open(self._path, "a", encoding="utf-8")  # noqa: SIM115 - handle outlives this call; closed by close()
        self._written = 0
        self._prune()
        if not self._announced:
            self._announced = True
            _LOGGER.info("Raw MQTT capture is ON, writing to %s", self._path)

    def _write_readme(self) -> None:
        """Leave a note saying what these files are and how to stop them.

        Rewritten on every open, because which switch is active decides how to stop
        capture: `logger.set_level` cannot stop a capture pinned by the constant.
        """
        try:
            with open(
                os.path.join(self._directory, "README.txt"), "w", encoding="utf-8"
            ) as fh:
                fh.write(
                    _README.format(
                        why=_WHY_FORCED if self._forced else _WHY_LOGGER,
                        max_mb=self._max_bytes // (1024 * 1024),
                        keep_desc=(
                            "No limit on the number of files — every one is kept."
                            if self._keep_files is None
                            else f"Only the newest {self._keep_files} are kept."
                        ),
                    )
                )
        except OSError:  # pragma: no cover - the capture still works without it
            pass

    def _prune(self) -> None:
        """Keep the newest `keep_files` captures; a no-op when it is None (the default)."""
        if self._keep_files is None:
            return
        try:
            captures = sorted(
                (
                    os.path.join(self._directory, name)
                    for name in os.listdir(self._directory)
                    if name.startswith("mqtt_raw_") and name.endswith(".jsonl")
                ),
                key=os.path.getmtime,
            )
        except OSError:  # pragma: no cover
            return
        for stale in captures[: max(0, len(captures) - self._keep_files)]:
            try:
                os.remove(stale)
            except OSError:  # pragma: no cover
                pass

    def roll(self) -> str | None:
        """Start a new capture file immediately. Returns its path, or None if capture is off.

        Separates one experiment from the next without a restart. Blocking file I/O: call
        from an executor.
        """
        if not self.enabled:
            return None
        with self._lock:
            self._close_locked()
            try:
                self._open_locked()
            except OSError as err:
                _LOGGER.warning("Could not start a new capture file: %s", err)
                return None
            return self._path

    def close(self) -> None:
        """Close the current capture file, if one is open."""
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:  # pragma: no cover
                pass
            if self._announced:
                _LOGGER.info("Raw MQTT capture is OFF (%s)", self._path)
                self._announced = False
        self._handle = None
        self._path = None
        self._written = 0
