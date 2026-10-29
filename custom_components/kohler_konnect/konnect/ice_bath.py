"""Kohler Ice Bath (SKU ``IBH``): one person's session journal.

See `docs/protocol/ice_bath.md`; section numbers below refer to it. The bath never talks to
the cloud: each `IBH` entry is a journal the app keeps, of sessions the person timed on the
bath and typed in (§1, §5.3). So there is nothing live to read and nothing to control; a
session can only be read, or logged as the app's New Record logs one.

⚠️ A record's note, answers, follow-up and sleep-quality fields are personal health notes:
nothing here passes them on.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from .client import KohlerClient
from .const import (
    IBH_CONFIGURATION,
    IBH_CONFIGURATION_WRITE,
    IBH_RECORD,
    IBH_RECORD_WRITE,
)

__all__ = [
    "IceBathJournal",
    "Session",
    "missed_days",
    "record_request",
    "routine_request",
    "routine_target",
    "sessions",
    "taken",
    "week_start",
]

# `durationMin` 11 means "ten minutes or more" (§5.2).
_TEN_PLUS = 11


class Session:
    """One session from the journal: when, for how long, and how cold. Nothing else."""

    def __init__(
        self, completed: datetime, minutes: int, seconds: int, temperature: int | None
    ) -> None:
        self.completed = completed
        self.ten_plus = minutes == _TEN_PLUS
        self.duration = 600 if self.ten_plus else max(0, minutes * 60 + seconds)
        #: Whole °F, whatever the account's unit (§5.1).
        self.temperature = temperature


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _records(payload: Any) -> Iterator[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Each entry of an `ibh-record` reply with its `record` block."""
    records = payload.get("records") if isinstance(payload, Mapping) else None
    for entry in records if isinstance(records, list) else ():
        record = entry.get("record") if isinstance(entry, Mapping) else None
        if isinstance(record, Mapping):
            yield entry, record


def _completed(record: Mapping[str, Any]) -> datetime | None:
    """When a record's session ended; None when its `completionTime` isn't a usable time."""
    seconds = _int(record.get("completionTime"))
    if not seconds:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def sessions(payload: Any) -> list[Session]:
    """The sessions in an `ibh-record` reply, oldest first.

    Only records whose `routineMissed` is false: the ones the app counts and marks done
    (§3.2). A missed routine day is marked true; one with no mark the app doesn't count.
    """
    found: list[Session] = []
    for _, record in _records(payload):
        if record.get("routineMissed") is not False:
            continue
        completed = _completed(record)
        minutes = _int(record.get("durationMin"))
        if completed is None or minutes is None:
            continue
        found.append(
            Session(
                completed,
                minutes,
                _int(record.get("durationSec")) or 0,
                _int(record.get("temperature")),
            )
        )
    return sorted(found, key=lambda session: session.completed)


def _record_day(entry: Mapping[str, Any]) -> date | None:
    """A record's day: from its id, `<journal>-<dd-MM-yyyy>`, as the app files it (§4.1)."""
    record_id = entry.get("id")
    if not isinstance(record_id, str):
        return None
    try:
        return datetime.strptime(record_id[-10:], "%d-%m-%Y").date()
    except ValueError:
        return None


def missed_days(payload: Any, zone: tzinfo) -> list[date]:
    """The routine days the journal marks missed (`routineMissed`), oldest first (§3.2).

    Dated as the app's calendar dates them, by `completionTime` in the local ``zone``;
    by the record's id when it has none.
    """
    days: set[date] = set()
    for entry, record in _records(payload):
        if record.get("routineMissed") is not True:
            continue
        completed = _completed(record)
        day = completed.astimezone(zone).date() if completed else _record_day(entry)
        if day is not None:
            days.add(day)
    return sorted(days)


def taken(payload: Any, record_id: str, day: date, zone: tzinfo) -> bool:
    """Whether the journal already holds a session for ``day``, which a new record would
    replace: one filed under ``record_id``, or one completed that day in ``zone``.

    A day marked missed holds no session. One whose mark is missing counts, to be safe.
    """
    for entry, record in _records(payload):
        if record.get("routineMissed") is True:
            continue
        if entry.get("id") == record_id or entry.get("recordId") == record_id:
            return True
        completed = _completed(record)
        if completed is not None and completed.astimezone(zone).date() == day:
            return True
    return False


def record_request(
    journal_id: str,
    tenant_id: str,
    *,
    day: date,
    completed: datetime,
    seconds: int,
    fahrenheit: int,
) -> dict[str, Any]:
    """The insert the app's "New Record" sends for one day's session (§4.1, §5.2).

    ``seconds`` is how long it lasted: ten minutes or more is the app's "10+", which it
    stores as 11 minutes and no seconds. ``day`` names the record: the session's local
    date, as the phone's clock dates it in the app. The note goes out empty, as the app
    sends it when nothing is typed; the answers it leaves out.
    """
    minutes, rest = divmod(max(0, seconds), 60)
    if minutes >= 10:
        minutes, rest = _TEN_PLUS, 0
    record_id = f"{journal_id}-{day:%d-%m-%Y}"
    return {
        "id": record_id,
        "recordId": record_id,
        "deviceId": journal_id,
        # The literal the app sends here, not the device's own value.
        "sku": "IBH",
        "operation": "Insert",
        "tenantId": tenant_id,
        "record": {
            "durationMin": minutes,
            "durationSec": rest,
            "temperature": fahrenheit,
            "note": "",
            "isManual": True,
            "completionTime": int(completed.timestamp()),
        },
    }


#: The routine's settings the app's settings screen writes, all at once (§4.3).
ROUTINE_FIELDS = (
    "isDaysOfWeek",
    "days",
    "noOfDays",
    "isReminderEnabled",
    "isFollowUpEnabled",
    "reminderTime",
)


def routine_request(
    routine: Mapping[str, Any], changes: Mapping[str, Any]
) -> dict[str, Any]:
    """W1's body: the routine as read with ``changes`` applied (§4.3). A setting it
    doesn't have is left out, as the app's converter leaves out a null."""
    merged = {**routine, **changes}
    return {
        field: merged[field]
        for field in ROUTINE_FIELDS
        if merged.get(field) is not None
    }


def routine_target(configuration: Any) -> int | None:
    """Sessions a week the routine asks for: its weekdays, or its count (§3.1)."""
    routine = (
        configuration.get("configuration")
        if isinstance(configuration, Mapping)
        else None
    )
    routine = routine.get("ibhConfig") if isinstance(routine, Mapping) else None
    if not isinstance(routine, Mapping):
        return None
    if routine.get("isDaysOfWeek") is True:
        days = routine.get("days")
        # No day picked is no routine, as the app reads it.
        return (days.count("1") or None) if isinstance(days, str) else None
    count = _int(routine.get("noOfDays"))
    return count if count and 1 <= count <= 7 else None


def week_start(today: date) -> date:
    """The Sunday that starts this week, as the app counts weeks (§8)."""
    return today - timedelta(days=(today.weekday() + 1) % 7)


class IceBathJournal:
    """One journal's reads and writes."""

    def __init__(self, client: KohlerClient, device_id: str) -> None:
        self._client = client
        self.device_id = device_id

    async def async_get_configuration(self) -> dict[str, Any]:
        payload = await self._client.async_request(
            "GET", IBH_CONFIGURATION.format(device_id=self.device_id)
        )
        return payload if isinstance(payload, dict) else {}

    async def async_write_routine(self, body: dict[str, Any]) -> Any:
        """Change the routine (W1)."""
        return await self._client.async_request(
            "PATCH",
            IBH_CONFIGURATION_WRITE.format(device_id=self.device_id),
            json_body=body,
        )

    async def async_write_record(self, body: dict[str, Any]) -> Any:
        """Insert, update or delete one record (W2)."""
        return await self._client.async_request(
            "PATCH", IBH_RECORD_WRITE, json_body=body
        )

    async def async_get_records(self, start: datetime, end: datetime) -> dict[str, Any]:
        """Records between two instants; the app asks for about a month at a time (§3.2)."""
        path = (
            f"{IBH_RECORD.format(device_id=self.device_id)}"
            f"?FromDate={int(start.timestamp())}&ToDate={int(end.timestamp())}"
        )
        payload = await self._client.async_request("GET", path)
        return payload if isinstance(payload, dict) else {}
