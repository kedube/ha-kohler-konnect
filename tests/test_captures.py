"""The three diagnostic captures: raw MQTT, the debug journal and the Report Log.

All three write to the owner's disk, usually an SD card, from code that runs beside the
shower's own control path. So the properties that matter are the ones a bug report depends
on and the ones that keep a diagnostic from becoming the fault:

* capture is **off** unless it was asked for by name — debugging the integration as a whole
  must not quietly start filling a directory;
* what is written is what arrived, byte for byte, including the payloads the decoder drops;
* every file is bounded, and rolling never splits or loses a record;
* a full disk or a vanished directory switches the capture off rather than raising into the
  MQTT thread or the event loop.

Every test that touches a logger level restores it: other tests rely on capture being off.
"""

from __future__ import annotations

import base64
import builtins
import errno
import json
import logging
import os
import time
from pathlib import Path

import pytest

from custom_components.kohler_konnect.konnect import journal as journal_module
from custom_components.kohler_konnect.konnect import raw_log as raw_log_module
from custom_components.kohler_konnect.konnect import report_log as report_log_module
from custom_components.kohler_konnect.konnect.journal import (
    WARMUP_README,
    DebugJournal,
)
from custom_components.kohler_konnect.konnect.raw_log import RawMqttLog
from custom_components.kohler_konnect.konnect.report_log import ReportLog

RAW = "custom_components.kohler_konnect.konnect.raw_log"
JOURNAL = "custom_components.kohler_konnect.konnect.journal"
# Ancestors whose level must NOT switch a capture on, root last.
ANCESTORS = (
    "custom_components.kohler_konnect.konnect",
    "custom_components.kohler_konnect",
    "custom_components",
    "",
)
TOPIC = "$iothub/methods/POST/ExecuteControlCommand/?$rid=1"


@pytest.fixture(autouse=True)
def restore_logger_levels():
    """Put every logger this file touches back exactly as it was."""
    names = (RAW, JOURNAL, *ANCESTORS)
    saved = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def switch_on(name: str) -> None:
    """What `logger.set_level` with `<name>: debug` does."""
    logging.getLogger(name).setLevel(logging.DEBUG)


def records(path: str | Path) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line
    ]


def jsonl_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.jsonl")) if directory.exists() else []


class FullDisk:
    """A file handle on a disk that has just filled up."""

    def write(self, _data: str) -> int:
        raise OSError(errno.ENOSPC, "No space left on device")

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


def full_disk_open(module, monkeypatch) -> dict[str, bool]:
    """Make capture files in `module` open onto a full disk while `state['full']` is set.

    Only `.jsonl` handles fail; the README beside them still opens normally, so the test
    exercises the write path rather than the open path.
    """
    state = {"full": True}
    real_open = builtins.open

    def fake_open(path, mode="r", *args, **kwargs):
        if state["full"] and str(path).endswith(".jsonl"):
            return FullDisk()
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(module, "open", fake_open, raising=False)
    return state


def make_journal(directory: Path, **kwargs) -> DebugJournal:
    kwargs.setdefault("prefix", "warmup")
    kwargs.setdefault("readme", WARMUP_README)
    kwargs.setdefault("readme_fields", {"before": 120, "after": 30})
    kwargs.setdefault("label", "Warmup journal")
    return DebugJournal(str(directory), **kwargs)


def age(path: str, seconds: float) -> None:
    """Backdate a file, so pruning by mtime has an unambiguous order to work with."""
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


# =========================================================================== #
# Raw MQTT capture
# =========================================================================== #
def test_raw_capture_is_off_by_default_and_writes_nothing(tmp_path):
    """Installed but off must cost one branch per message and not one byte of disk."""
    directory = tmp_path / "raw"
    log = RawMqttLog(str(directory))
    assert log.enabled is False

    log.write(TOPIC, b'{"a":1}')
    log.prepare()

    assert not directory.exists()
    assert log.path is None


def test_raw_capture_ships_switched_off():
    """Both constants pin a capture on across restarts; a release must never ship with one set."""
    from custom_components.kohler_konnect import const

    assert const.ENABLE_RAW_MQTT_LOG is False
    assert const.ENABLE_WARMUP_DEBUG_LOG is False


def test_setting_the_raw_logger_to_debug_turns_capture_on(tmp_path):
    """`logger.set_level` on the module path is the documented no-restart switch."""
    directory = tmp_path / "raw"
    log = RawMqttLog(str(directory))
    switch_on(RAW)

    assert log.enabled is True
    log.write(TOPIC, b'{"a":1}')

    (capture,) = jsonl_files(directory)
    assert capture.name.startswith("mqtt_raw_")
    assert str(capture) == log.path
    assert [r["payload"] for r in records(capture)] == ['{"a":1}']


@pytest.mark.parametrize("ancestor", ANCESTORS, ids=lambda n: n or "root")
def test_debugging_the_integration_as_a_whole_does_not_start_a_raw_capture(
    tmp_path, ancestor
):
    """`isEnabledFor` would inherit this level; a diagnostic that writes to disk must be named.

    Otherwise `custom_components.kohler_konnect: debug`, or `logger: default: debug`, would
    silently start filling a directory nobody asked for.
    """
    directory = tmp_path / "raw"
    log = RawMqttLog(str(directory))
    switch_on(ancestor)
    # The level really is inherited — this is the trap the `.level` check avoids.
    assert logging.getLogger(RAW).isEnabledFor(logging.DEBUG)

    assert log.enabled is False
    log.write(TOPIC, b'{"a":1}')
    assert not directory.exists()


def test_the_raw_constant_pins_capture_on_whatever_the_logger_says(tmp_path):
    """`ENABLE_RAW_MQTT_LOG` wins on purpose, so a restart cannot end a capture meant to run."""
    directory = tmp_path / "raw"
    log = RawMqttLog(str(directory), forced=True)
    logging.getLogger(RAW).setLevel(logging.INFO)

    assert log.enabled is True
    log.write(TOPIC, b'{"a":1}')
    log.write(TOPIC, b'{"a":2}')

    (capture,) = jsonl_files(directory)
    assert len(records(capture)) == 2


def test_switching_the_raw_logger_back_to_info_closes_the_file_on_the_next_message(
    tmp_path,
):
    """Flipped off mid-session, the file is released at once rather than held until unload."""
    directory = tmp_path / "raw"
    log = RawMqttLog(str(directory))
    switch_on(RAW)
    log.write(TOPIC, b'{"n":1}')
    capture = log.path
    assert capture is not None

    logging.getLogger(RAW).setLevel(logging.INFO)
    log.write(TOPIC, b'{"n":2}')

    assert log.path is None
    assert [r["payload"] for r in records(capture)] == ['{"n":1}']

    # And back on: a fresh file, the old one left exactly as it was.
    switch_on(RAW)
    log.write(TOPIC, b'{"n":3}')
    assert log.path not in (None, capture)
    assert [r["payload"] for r in records(capture)] == ['{"n":1}']
    assert [r["payload"] for r in records(log.path)] == ['{"n":3}']


def test_nothing_is_created_on_disk_until_the_first_message_arrives(tmp_path):
    """Switched on but silent — the stream can be quiet for 11.9 h — must not create files."""
    directory = tmp_path / "raw"
    log = RawMqttLog(str(directory))
    switch_on(RAW)
    assert not directory.exists()

    log.write(TOPIC, b"{}")

    assert (directory / "README.txt").is_file()
    assert len(jsonl_files(directory)) == 1


def test_prepare_opens_an_empty_capture_so_switching_on_is_visibly_working(tmp_path):
    """Called at setup when capture is already on: an empty file beats a missing directory."""
    directory = tmp_path / "raw"
    log = RawMqttLog(str(directory), forced=True)

    log.prepare()

    (capture,) = jsonl_files(directory)
    assert capture.read_text() == ""
    first = log.path
    # Idempotent: a second prepare must not start another file.
    log.prepare()
    assert log.path == first
    assert len(jsonl_files(directory)) == 1


@pytest.mark.parametrize(
    "payload",
    [
        # Key order, spacing, duplicate keys and numeric spelling all survive.
        b'{"z": 1,   "a": 2.50, "a": 3}',
        # Not JSON at all — exactly what the decoder silently drops.
        b'{"truncated": ',
        b"",
        # Non-ASCII UTF-8 is kept as text, not escaped.
        '{"name": "Dusche ü"}'.encode(),
    ],
    ids=["odd-json", "invalid-json", "empty", "utf8"],
)
def test_raw_payloads_are_written_exactly_as_they_arrived(tmp_path, payload):
    """A re-serialised dict would normalise away the very details a bug report needs."""
    log = RawMqttLog(str(tmp_path), forced=True)
    log.write(TOPIC, payload, qos=1, retain=True)

    (record,) = records(log.path)
    assert record["payload"] == payload.decode("utf-8")
    assert "payload_b64" not in record
    assert record["topic"] == TOPIC
    assert record["qos"] == 1
    assert record["retain"] is True
    # Same clock and spelling as the journals, so the files interleave by sorting on `ts`.
    assert record["ts"].endswith("Z")


def test_raw_payloads_that_are_not_utf8_are_kept_as_base64(tmp_path):
    """Invalid bytes are still evidence; they go in `payload_b64` instead of being dropped."""
    raw = b"\xff\xfe\x00GCS\x80"
    log = RawMqttLog(str(tmp_path), forced=True)
    log.write(TOPIC, raw)

    (record,) = records(log.path)
    assert "payload" not in record
    assert base64.b64decode(record["payload_b64"]) == raw


def test_a_full_raw_capture_rolls_to_a_new_file_without_splitting_a_record(tmp_path):
    """Files roll at `max_bytes`; a line is never split and nothing is lost across the roll."""
    cap = 300
    log = RawMqttLog(str(tmp_path), forced=True, max_bytes=cap)
    order: list[str] = []
    for n in range(7):
        log.write(TOPIC, json.dumps({"n": n, "pad": "x" * 60}).encode())
        if log.path not in order:
            order.append(log.path)

    assert len(order) > 1, "the cap never rolled the file"
    seen = [json.loads(r["payload"])["n"] for path in order for r in records(path)]
    assert seen == list(range(7))
    for path in order[:-1]:
        lines = Path(path).read_bytes().splitlines(keepends=True)
        # Rolled only once the cap was reached, never before it.
        assert sum(map(len, lines)) >= cap
        assert sum(map(len, lines[:-1])) < cap


def test_roll_starts_a_new_raw_file_and_keeps_the_old_one(tmp_path):
    """One experiment per file, without restarting Home Assistant."""
    log = RawMqttLog(str(tmp_path), forced=True)
    log.write(TOPIC, b'{"run":1}')
    first = log.path

    second = log.roll()

    assert second is not None and second != first
    assert log.path == second
    log.write(TOPIC, b'{"run":2}')
    assert [r["payload"] for r in records(first)] == ['{"run":1}']
    assert [r["payload"] for r in records(second)] == ['{"run":2}']


def test_roll_does_nothing_while_raw_capture_is_off(tmp_path):
    log = RawMqttLog(str(tmp_path / "raw"))
    assert log.roll() is None
    assert not (tmp_path / "raw").exists()


def test_close_releases_the_raw_file_and_the_next_message_starts_another(tmp_path):
    log = RawMqttLog(str(tmp_path), forced=True)
    log.write(TOPIC, b'{"n":1}')
    first = log.path

    log.close()
    assert log.path is None
    log.close()  # closing twice is harmless — unload can race a switch-off

    log.write(TOPIC, b'{"n":2}')
    assert log.path not in (None, first)
    assert len(jsonl_files(tmp_path)) == 2


def test_raw_captures_are_kept_forever_by_default(tmp_path):
    """The directory is evidence the owner wants to keep, not a rotating buffer."""
    log = RawMqttLog(str(tmp_path), forced=True)
    paths = []
    for _ in range(4):
        paths.append(log.roll())
        age(paths[-1], 100 - len(paths))
    assert all(os.path.exists(p) for p in paths)


def test_keep_files_prunes_only_the_oldest_raw_captures(tmp_path):
    """Bounded on request, and never at the expense of a file that is not a capture."""
    (tmp_path / "notes.jsonl").write_text("{}\n")
    age(str(tmp_path / "notes.jsonl"), 1000)
    log = RawMqttLog(str(tmp_path), forced=True, keep_files=2)

    oldest = log.roll()
    age(oldest, 30)
    middle = log.roll()
    age(middle, 20)
    newest = log.roll()

    assert not os.path.exists(oldest)
    assert os.path.exists(middle) and os.path.exists(newest)
    assert (tmp_path / "notes.jsonl").exists()
    assert (tmp_path / "README.txt").exists()


def test_the_raw_readme_says_how_to_stop_the_switch_that_is_actually_on(tmp_path):
    """The wrong instruction is worse than none: `logger.set_level` cannot stop the constant."""
    by_logger = tmp_path / "logger"
    switch_on(RAW)
    RawMqttLog(str(by_logger)).write(TOPIC, b"{}")
    text = (by_logger / "README.txt").read_text(encoding="utf-8")
    assert "is set to debug" in text
    assert "set to `info`" in text
    assert "pins capture on" not in text
    assert "No limit on the number of files" in text
    assert "Files roll at 8 MB." in text

    logging.getLogger(RAW).setLevel(logging.NOTSET)
    by_constant = tmp_path / "constant"
    RawMqttLog(
        str(by_constant), forced=True, keep_files=5, max_bytes=2 * 1024 * 1024
    ).write(TOPIC, b"{}")
    text = (by_constant / "README.txt").read_text(encoding="utf-8")
    assert "pins capture on across restarts" in text
    assert "will NOT turn it off" in text
    assert "Only the newest 5 are kept." in text
    assert "Files roll at 2 MB." in text


def test_a_full_disk_switches_raw_capture_off_instead_of_raising(tmp_path, monkeypatch):
    """paho calls this on its network thread: an exception here would take the stream down.

    The constant is cleared too, so a pinned capture stops trying rather than failing on
    every message for the rest of the run.
    """
    full_disk_open(raw_log_module, monkeypatch)
    log = RawMqttLog(str(tmp_path), forced=True)

    log.write(TOPIC, b'{"n":1}')  # must not raise

    assert log.enabled is False
    assert log.path is None
    log.write(TOPIC, b'{"n":2}')
    assert log.path is None


@pytest.mark.parametrize("call", ["write", "prepare", "roll"])
def test_an_unusable_raw_directory_never_raises(tmp_path, call):
    """A path that is a file, not a directory, is the simplest unusable capture location."""
    blocker = tmp_path / "raw"
    blocker.write_text("not a directory")
    log = RawMqttLog(str(blocker), forced=True)

    if call == "write":
        log.write(TOPIC, b"{}")
    elif call == "prepare":
        log.prepare()
    else:
        assert log.roll() is None

    assert log.path is None
    assert blocker.read_text() == "not a directory"


# =========================================================================== #
# Debug journal
# =========================================================================== #
def test_the_journal_is_off_by_default(tmp_path):
    journal = make_journal(tmp_path / "j")
    assert journal.enabled is False

    journal.note("mode", before="on", after="off")
    journal.prepare()

    assert not (tmp_path / "j").exists()
    assert journal.wants_open is False


def test_setting_the_journal_logger_to_debug_turns_it_on(tmp_path):
    journal = make_journal(tmp_path)
    switch_on(JOURNAL)
    assert journal.enabled is True


@pytest.mark.parametrize("ancestor", ANCESTORS, ids=lambda n: n or "root")
def test_debugging_the_integration_as_a_whole_does_not_start_the_journal(
    tmp_path, ancestor
):
    """Same rule as the raw capture: only the journal's own logger, named, switches it."""
    journal = make_journal(tmp_path / "j")
    switch_on(ancestor)

    assert journal.enabled is False
    journal.prepare()
    assert not (tmp_path / "j").exists()


def test_the_journal_constant_pins_it_on_whatever_the_logger_says(tmp_path):
    journal = make_journal(tmp_path, forced=True)
    logging.getLogger(JOURNAL).setLevel(logging.WARNING)
    assert journal.enabled is True


def test_a_journal_note_never_opens_a_file_it_asks_for_prepare_instead(tmp_path):
    """`note()` runs on the event loop, where opening a file is a blocking call.

    The record that raised the flag is held, and lands first once prepare() opens the file.
    """
    directory = tmp_path / "j"
    journal = make_journal(directory, forced=True)

    journal.note("mode", before="warmUpAllOutlets", after="warmUpDisabled")

    assert not directory.exists()
    assert journal.wants_open is True

    journal.prepare()
    assert journal.wants_open is False
    journal.note("restore_done", mode="warmUpAllOutlets")

    (path,) = jsonl_files(directory)
    assert path.name.startswith("warmup_")
    assert [r["event"] for r in records(path)] == ["mode", "restore_done"]


def test_switching_the_journal_off_drops_records_held_for_a_file(tmp_path):
    """A record held for a prepare() that never comes must not surface in a later file."""
    journal = make_journal(tmp_path / "j", forced=True)
    journal.note("mode", after="warmUpDisabled")
    journal.close()
    journal.prepare()
    journal.note("restore_done", mode="warmUpAllOutlets")

    (path,) = jsonl_files(tmp_path / "j")
    assert [r["event"] for r in records(path)] == ["restore_done"]


def test_journal_records_carry_the_shared_clock_and_round_floats(tmp_path):
    """Seconds off a monotonic clock are rounded; everything else is written as given."""
    journal = make_journal(tmp_path, forced=True)
    journal.prepare()

    journal.note(
        "disabled",
        ours=False,
        after_s=3.14159265,
        before_window=[{"code": "GCS_WARM_STS"}],
        count=4,
    )

    (record,) = records(journal.path)
    assert record["event"] == "disabled"
    assert record["ts"].endswith("Z")
    assert record["after_s"] == 3.14
    assert record["ours"] is False
    assert record["count"] == 4
    assert record["before_window"] == [{"code": "GCS_WARM_STS"}]


def test_switching_the_journal_logger_off_closes_its_file(tmp_path):
    journal = make_journal(tmp_path)
    switch_on(JOURNAL)
    journal.prepare()
    journal.note("baseline", mode="on")
    path = journal.path

    logging.getLogger(JOURNAL).setLevel(logging.INFO)
    journal.note("mode", before="on", after="off")

    assert journal.path is None
    assert [r["event"] for r in records(path)] == ["baseline"]


def test_a_full_journal_rolls_without_losing_a_record(tmp_path):
    """The cap is a backstop for a misbehaving valve on what is usually an SD card."""
    cap = 250
    journal = make_journal(tmp_path, forced=True, max_bytes=cap)
    journal.prepare()
    order: list[str] = []
    for n in range(6):
        journal.note("announced", n=n, pad="y" * 60)
        # What the warm-up manager does: the roll happens in an executor, not in note().
        if journal.wants_open:
            journal.prepare()
        if journal.path not in order:
            order.append(journal.path)

    assert len(order) > 1, "the cap never rolled the file"
    assert [r["n"] for p in order for r in records(p)] == list(range(6))
    for path in order[:-1]:
        assert Path(path).stat().st_size >= cap


def test_journal_roll_and_close(tmp_path):
    journal = make_journal(tmp_path, forced=True)
    journal.prepare()
    first = journal.path

    second = journal.roll()
    assert second not in (None, first)
    assert os.path.exists(first)

    journal.close()
    assert journal.path is None


def test_journal_roll_does_nothing_while_it_is_off(tmp_path):
    assert make_journal(tmp_path / "j").roll() is None
    assert not (tmp_path / "j").exists()


def test_two_journals_in_one_directory_never_prune_each_other(tmp_path):
    """Pruning matches on the prefix, so the warmup journal cannot delete another's files."""
    warmup = make_journal(tmp_path, forced=True, keep_files=1)
    other = make_journal(
        tmp_path, forced=True, keep_files=1, prefix="other", readme="Other {keep_desc}"
    )
    other_file = other.roll()
    age(other_file, 50)

    old_warmup = warmup.roll()
    age(old_warmup, 40)
    new_warmup = warmup.roll()

    assert not os.path.exists(old_warmup)
    assert os.path.exists(new_warmup)
    assert os.path.exists(other_file)


def test_each_journal_leaves_its_own_readme_filled_in(tmp_path):
    """The README is a template: `readme_fields` and the keep policy are substituted."""
    make_journal(tmp_path, forced=True, keep_files=3).prepare()

    text = (tmp_path / "README-warmup.txt").read_text(encoding="utf-8")
    assert "message seen in the 120s leading up to it" in text
    assert "written 30s later" in text
    assert "Only the newest 3 are kept." in text
    assert "{" not in text.split("Reading them")[0], "an unfilled template field"


def test_a_full_disk_switches_the_journal_off_instead_of_raising(tmp_path, monkeypatch):
    """A diagnostic must never take the integration down with it."""
    full_disk_open(journal_module, monkeypatch)
    journal = make_journal(tmp_path, forced=True)
    journal.prepare()

    journal.note("mode", before="on", after="off")  # must not raise

    assert journal.enabled is False
    assert journal.path is None


def test_an_unusable_journal_directory_never_raises_from_prepare_or_roll(tmp_path):
    blocker = tmp_path / "j"
    blocker.write_text("not a directory")
    journal = make_journal(blocker, forced=True)

    journal.prepare()
    assert journal.roll() is None
    assert journal.path is None


def _fill_journal_to_its_cap(tmp_path) -> DebugJournal:
    journal = make_journal(tmp_path / "j", forced=True, max_bytes=100)
    journal.prepare()
    journal.note("announced", pad="z" * 120)
    assert journal._written >= 100
    return journal


def test_a_journal_note_at_the_size_cap_does_not_open_a_file(tmp_path):
    """The warmup journal is written from a `@callback`; blocking I/O there is an HA error."""
    journal = _fill_journal_to_its_cap(tmp_path)
    opened: list[str] = []
    real_open = builtins.open

    def tracking_open(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    builtins.open = tracking_open
    try:
        journal.note("announced", n=2)
    finally:
        builtins.open = real_open

    assert opened == [], f"note() opened files on the event loop: {opened}"


def test_a_journal_note_at_the_size_cap_never_raises(tmp_path):
    """A diagnostic must never take the integration down with it — even at the cap."""
    journal = _fill_journal_to_its_cap(tmp_path)
    # The directory goes away (an SD card remounted, a cleanup script) and something else
    # takes its name, so the roll cannot recreate it.
    (tmp_path / "j").rename(tmp_path / "moved")
    (tmp_path / "j").write_text("not a directory")

    journal.note("announced", n=2)  # raises FileExistsError today


# =========================================================================== #
# Report Log
# =========================================================================== #
def test_starting_a_report_opens_its_file_at_once(tmp_path):
    """Someone who just flipped the switch deserves a file they can see."""
    directory = tmp_path / "reports"
    log = ReportLog(str(directory))
    assert log.active is False

    stem = log.start()

    assert log.active is True
    assert stem.startswith("report_") and stem.endswith("Z")
    assert log.path == str(directory / f"{stem}.jsonl")
    assert Path(log.path).exists()
    assert "_p2, _p3" in (directory / "README.txt").read_text(encoding="utf-8")
    log.stop()


def test_a_restart_continues_the_same_report_episode(tmp_path):
    """A capture of "it breaks when I restart" must not lose the restart itself."""
    directory = tmp_path / "reports"
    before = ReportLog(str(directory))
    stem = before.start()
    before.write(TOPIC, b'{"n":"before"}')
    path = before.path
    before.close()  # unload: releases the handle, does not end the episode

    after = ReportLog(str(directory))
    after.resume(stem)
    after.write(TOPIC, b'{"n":"after"}')
    after.close()

    assert after.path is None
    assert jsonl_files(directory) == [Path(path)]
    assert [r["payload"] for r in records(path)] == ['{"n":"before"}', '{"n":"after"}']


def test_a_report_file_deleted_mid_episode_is_recreated_on_resume(tmp_path):
    """The episode name is the identity; the file is simply opened again."""
    directory = tmp_path / "reports"
    log = ReportLog(str(directory))
    stem = log.start()
    log.close()
    os.remove(directory / f"{stem}.jsonl")

    log.resume(stem)
    log.write(TOPIC, b"{}")

    assert len(records(directory / f"{stem}.jsonl")) == 1
    log.close()


def test_a_full_report_continues_in_numbered_parts_sharing_the_episode_name(tmp_path):
    """One runaway episode cannot eat the disk in a single unmanageable file."""
    directory = tmp_path / "reports"
    log = ReportLog(str(directory), max_bytes=200)
    stem = log.start()
    for n in range(8):
        log.write(TOPIC, json.dumps({"n": n, "pad": "p" * 60}).encode())
    log.close()

    names = sorted(p.name for p in jsonl_files(directory))
    assert f"{stem}.jsonl" in names and f"{stem}_p2.jsonl" in names
    assert all(n == f"{stem}.jsonl" or n.startswith(f"{stem}_p") for n in names)
    parts = sorted(
        jsonl_files(directory),
        key=lambda p: int(p.stem.rsplit("_p", 1)[1]) if "_p" in p.stem else 1,
    )
    seen = [json.loads(r["payload"])["n"] for p in parts for r in records(p)]
    assert seen == list(range(8))


def test_a_restart_reattaches_to_the_latest_part(tmp_path):
    directory = tmp_path / "reports"
    log = ReportLog(str(directory), max_bytes=200)
    stem = log.start()
    for n in range(5):
        log.write(TOPIC, json.dumps({"n": n, "pad": "p" * 10}).encode())
    latest = log.path
    # Two records to a part, so the fifth sits alone in a third part with room to spare.
    assert Path(latest).name == f"{stem}_p3.jsonl"
    assert os.path.getsize(latest) < 200
    log.close()

    restarted = ReportLog(str(directory), max_bytes=200)
    restarted.resume(stem)
    assert restarted.path == latest
    restarted.close()


def test_a_restart_onto_a_full_part_starts_the_next_one(tmp_path):
    directory = tmp_path / "reports"
    stem = "report_20260911T120000Z"
    directory.mkdir()
    (directory / f"{stem}.jsonl").write_text("x" * 300)

    log = ReportLog(str(directory), max_bytes=200)
    log.resume(stem)

    assert log.path == str(directory / f"{stem}_p2.jsonl")
    log.close()


def test_parts_of_other_episodes_and_lookalike_names_are_not_resumed(tmp_path):
    """Only `<stem>.jsonl` and `<stem>_p<N>.jsonl` belong to an episode."""
    directory = tmp_path / "reports"
    directory.mkdir()
    stem = "report_20260911T120000Z"
    for name in (
        "report_20260101T000000Z_p9.jsonl",  # another episode
        f"{stem}_p5.jsonl.bak",
        f"{stem}_pX.jsonl",
        f"{stem}x_p8.jsonl",  # shares the prefix, not the stem
        f"{stem}_p7.txt",
    ):
        (directory / name).write_text("")

    log = ReportLog(str(directory))
    log.resume(stem)

    assert log.path == str(directory / f"{stem}.jsonl")
    log.close()


@pytest.mark.parametrize(
    "stem",
    [
        "../../escaped",
        "report_20260911T120000Z/../../escaped",
        "/tmp/report_20260911T120000Z",
        "report_20260911T120000Z/x",
        "report_20260911T120000Z_p2",
        "report_20260911T120000Z.jsonl",
        "REPORT_20260911T120000Z",
        "report_2026091T120000Z",
        "",
    ],
)
def test_a_persisted_episode_name_that_start_could_not_have_written_is_refused(
    tmp_path, stem, caplog
):
    """The name round-trips through a hand-editable file and is joined onto the directory.

    A refused name starts a fresh, legitimate episode rather than leaving the switch on with
    nothing being written.
    """
    directory = tmp_path / "a" / "b" / "reports"
    log = ReportLog(str(directory))
    with caplog.at_level(logging.WARNING, logger=report_log_module.__name__):
        log.resume(stem)

    assert log.active
    assert log._stem != stem
    assert report_log_module._SAFE_STEM.fullmatch(log._stem)
    assert any("not one we could have written" in r.message for r in caplog.records)
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written, "the replacement episode should have opened a file"
    assert all(p.parent == directory for p in written), written
    log.close()


def test_stopping_a_report_ends_the_episode(tmp_path):
    """Off means off: nothing more is written, and the handle is released."""
    directory = tmp_path / "reports"
    log = ReportLog(str(directory))
    log.start()
    log.write(TOPIC, b'{"n":1}')
    path = log.path

    log.stop()
    log.write(TOPIC, b'{"n":2}')
    log.note("warmup", "mode", {"after": "warmUpDisabled"})
    log.prepare()

    assert log.active is False
    assert log.path is None
    assert [r["payload"] for r in records(path)] == ['{"n":1}']


def test_report_messages_are_written_exactly_as_they_arrived(tmp_path):
    """Byte-compatible with the development capture, invalid payloads included."""
    log = ReportLog(str(tmp_path))
    log.start()
    log.write(TOPIC, b'{"b": 1,  "a": 2', qos=1, retain=False)
    log.write(TOPIC, b"\xc3\x28")
    log.close()

    text, binary = records(next(iter(jsonl_files(tmp_path))))
    assert text["payload"] == '{"b": 1,  "a": 2'
    assert text["qos"] == 1
    assert base64.b64decode(binary["payload_b64"]) == b"\xc3\x28"


def test_a_failed_report_write_keeps_the_episode_so_the_disk_can_come_back(
    tmp_path, monkeypatch
):
    """The handle is dropped but the episode stays: the next message tries again."""
    disk = full_disk_open(report_log_module, monkeypatch)
    log = ReportLog(str(tmp_path))
    log.start()

    log.write(TOPIC, b'{"n":1}')  # must not raise
    assert log.active is True

    disk["full"] = False
    log.write(TOPIC, b'{"n":2}')
    log.note("warmup", "mode", {"after": "on"})

    lines = records(log.path)
    assert [r.get("payload") for r in lines] == ['{"n":2}', None]
    assert lines[1]["journal"] == "warmup"
    log.close()


def test_a_failed_report_decision_write_never_raises(tmp_path, monkeypatch):
    """Decisions are written from the event loop; an exception there is the loop's problem."""
    full_disk_open(report_log_module, monkeypatch)
    log = ReportLog(str(tmp_path))
    log.start()

    log.note("warmup", "disabled", {"ours": False})  # must not raise

    assert log.active is True


def test_an_unusable_report_directory_never_raises(tmp_path):
    """Starting or resuming still records the episode, so the switch state is honest."""
    blocker = tmp_path / "reports"
    blocker.write_text("not a directory")
    log = ReportLog(str(blocker))

    stem = log.start()
    assert log.active and log.path is None

    log.resume(stem)
    assert log.active and log.path is None
    log.write(TOPIC, b"{}")


def test_prepare_does_nothing_without_an_episode(tmp_path):
    log = ReportLog(str(tmp_path / "reports"))
    log.prepare()
    assert not (tmp_path / "reports").exists()


def test_a_decision_at_a_full_report_part_lands_after_the_prepare_it_asks_for(tmp_path):
    """`wants_open` promises the record that raised it is lost and the *next* one lands."""
    log = ReportLog(str(tmp_path), max_bytes=150)
    log.start()
    log.write(TOPIC, json.dumps({"pad": "q" * 160}).encode())  # fills the part

    log.note("warmup", "mode", {"after": "warmUpDisabled"})
    assert log.wants_open is True
    log.prepare()  # what the warm-up manager schedules in an executor
    log.note("warmup", "disabled", {"ours": False})
    log.close()

    decisions = [
        r for p in jsonl_files(tmp_path) for r in records(p) if r.get("journal")
    ]
    assert [d["event"] for d in decisions] == ["disabled"]
