"""One Ice Bath journal, read every half hour: no message ever announces a new session.

Its records are read a calendar month at a time, as the app reads them
(`docs/protocol/ice_bath.md` §3.2): this month and the last, which between them hold this
week whatever the day. Only when, how long and how cold each session was is kept: notes,
answers and follow-ups are personal, and never read past the reply.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..konnect import Device, Envelope, KohlerError
from ..konnect.ice_bath import (
    IceBathJournal,
    Session,
    missed_days,
    record_request,
    routine_request,
    routine_target,
    sessions,
    taken,
)
from ..product_base import PendingWrites, ProductCoordinator

if TYPE_CHECKING:
    from ..coordinator import KohlerKonnectCoordinator

__all__ = ["IceBathCoordinator"]

_LOGGER = logging.getLogger(__name__)

_OFFLINE = "Kohler couldn't reach the Ice Bath journal. Try again in a moment."
# How far back a calendar reads when the journal's start isn't known.
_EARLIEST = timedelta(days=731)


def _month_start(moment: datetime) -> datetime:
    """Local midnight on the first of ``moment``'s month."""
    return dt_util.start_of_local_day(dt_util.as_local(moment).date().replace(day=1))


def _next_month(month: datetime) -> datetime:
    return _month_start(month + timedelta(days=32))


def _months(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    """The calendar months from ``start`` to ``end``, each cut to them."""
    month = _month_start(start)
    while month < end:
        following = _next_month(month)
        yield max(month, start), min(following, end)
        month = following


def _created(device: Device) -> datetime | None:
    """When the journal began (`createdTime`, epoch seconds): the app offers no day before."""
    try:
        return datetime.fromtimestamp(int(device.raw["createdTime"]), tz=UTC)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


class IceBathCoordinator(ProductCoordinator):
    """One person's Ice Bath journal."""

    model = "Ice Bath"
    poll_interval = timedelta(minutes=30)
    streams = False

    def __init__(
        self, account: KohlerKonnectCoordinator, device: Device, name: str
    ) -> None:
        super().__init__(account, device, name)
        self.journal = IceBathJournal(self.client, self.device_id)
        self.created = _created(device)
        self._configuration: dict[str, Any] = {}
        self._pending_routine = PendingWrites()

    async def async_read(self) -> dict[str, Any]:
        this_month = _month_start(dt_util.now())
        records = await self._async_records(
            _month_start(this_month - timedelta(days=1)), _next_month(this_month)
        )
        try:
            self._configuration = await self.journal.async_get_configuration()
        except KohlerError as err:
            # A routine is optional, so the sessions stand without one; the last one read
            # is kept.
            _LOGGER.debug("Could not read %s's routine: %s", self.device_name, err)
        found = sessions(records)
        return {
            "sessions": found,
            "missed": missed_days(records, dt_util.get_default_time_zone()),
            "routine": _routine(self._configuration),
            "record_count": len(found),
        }

    async def _async_records(self, start: datetime, end: datetime) -> dict[str, Any]:
        """The records between two instants, read a month at a time as the app reads them,
        and none from before the journal began."""
        if self.created is not None:
            start = max(
                start, dt_util.start_of_local_day(dt_util.as_local(self.created))
            )
        start = max(start, dt_util.now() - _EARLIEST)
        records: list[Any] = []
        for month_start, month_end in _months(start, end):
            payload = await self.journal.async_get_records(month_start, month_end)
            found = payload.get("records")
            records.extend(found if isinstance(found, list) else ())
        return {"records": records}

    def apply_envelope(self, envelope: Envelope) -> bool:
        """The journal has no messages (§6); one would only mean "read again"."""
        return False

    @property
    def routine(self) -> dict[str, Any]:
        """The routine as read, with any setting just written that Kohler doesn't show."""
        return self._pending_routine.over((self.data or {}).get("routine") or {})

    @property
    def target(self) -> int | None:
        """Sessions a week the routine asks for (`routine_target`)."""
        return routine_target({"configuration": {"ibhConfig": self.routine}})

    @property
    def sessions(self) -> list[Session]:
        return (self.data or {}).get("sessions") or []

    @property
    def last_session(self) -> Session | None:
        found = self.sessions
        return found[-1] if found else None

    async def async_sessions_between(
        self, start: datetime, end: datetime
    ) -> list[Session]:
        """Sessions completed between two instants: from what is held when it covers
        them, else read from Kohler, as the app's calendar pages back a month at a time."""
        held_from = _month_start(_month_start(dt_util.now()) - timedelta(days=1))
        if start >= held_from:
            found = self.sessions
        else:
            with self.account.command_errors(_OFFLINE):
                found = sessions(
                    await self._async_records(
                        start, min(end, dt_util.now() + timedelta(days=1))
                    )
                )
        return [s for s in found if start <= s.completed <= end]

    async def async_log_session(
        self, completed: datetime, seconds: int, fahrenheit: int
    ) -> None:
        """Record a session as the app's "New Record" does (§4.1).

        The record is named after its day, so a second one for a day would replace the
        first: a day that already has a session is refused rather than overwritten. So is
        a day before the journal began, which the app doesn't offer.
        """
        local = dt_util.as_local(completed)
        day = local.date()
        if self.created is not None and day < (
            began := dt_util.as_local(self.created).date()
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="ice_bath_before_journal",
                translation_placeholders={"name": self.device_name, "day": str(began)},
            )
        body = record_request(
            self.device_id,
            await self.client.async_tenant_id(),
            day=day,
            completed=completed,
            seconds=seconds,
            fahrenheit=fahrenheit,
        )
        start = dt_util.start_of_local_day(local)
        # A day either side as well: the phone that names records may keep another zone.
        with self.account.command_errors(_OFFLINE):
            existing = await self.journal.async_get_records(
                start - timedelta(days=1), start + timedelta(days=2)
            )
        if taken(existing, body["id"], day, dt_util.get_default_time_zone()):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="ice_bath_session_exists",
                translation_placeholders={"name": self.device_name, "day": str(day)},
            )
        await self.async_command(self.journal.async_write_record(body), _OFFLINE)

    async def async_set_routine(self, **changes: Any) -> None:
        """Change the routine as the app's Routine and Settings screens do (§4.3), from a
        fresh read with any setting just written laid over it."""
        async with self.write_lock:
            with self.account.command_errors(_OFFLINE):
                configuration = await self.journal.async_get_configuration()
                routine = self._pending_routine.over(_routine(configuration))
                await self.journal.async_write_routine(
                    routine_request(routine, changes)
                )
            self._configuration = configuration
            self._pending_routine.add(changes)
            self.async_update_listeners()
        await self.async_after_change()


def _routine(configuration: Any) -> dict[str, Any]:
    """The routine's schedule and defaults, for the Routine sensor; never the person's notes."""
    inner = (
        configuration.get("configuration") if isinstance(configuration, dict) else None
    )
    routine = inner.get("ibhConfig") if isinstance(inner, dict) else None
    if not isinstance(routine, dict):
        return {}
    keep = (
        "isDaysOfWeek",
        "days",
        "noOfDays",
        "isReminderEnabled",
        "reminderTime",
        "isFollowUpEnabled",
        "durationMin",
        "durationSec",
        "temperature",
    )
    return {key: routine[key] for key in keep if key in routine}
