"""Water and time used, period by period, for the sidebar panel's usage charts.

A week or month is charted by day and a year by month, as the Konnect app charts them.
A period the integration already holds is answered from memory: the daily series reaches
35 days back and the monthly one 400, and the usage sensors read the same series, so the
totals match theirs. Older periods are read from Kohler when the panel pages back to them,
and kept:

* a period that ended at least two days ago no longer changes (a finished shower appears
  late, and some accounts' days are filed by UTC date), so it is kept until restart;
* a more recent one is kept for half an hour;
* a failed read is never kept;
* each account reads one period at a time, so a repeated request waits for the first and
  takes its answer.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from ..const import DOMAIN
from ..konnect.usage import is_metric, usage_series, usage_volume_gallons
from ..sensor import kohler_timestamp
from ..water import (
    _daily_spans_the_year,
    _finite,
    _month_totals,
    _switch_ons,
    _usage_bucket_date,
    published,
)
from .live import DeviceRef

__all__ = ["PERIODS", "UsageUnavailable", "async_usage_history", "period_bounds"]

PERIODS = ("week", "month", "year")

# The daily series reaches 35 days back, so a week or month starting within this many days
# of today lies wholly inside it.
_DAILY_HELD_DAYS = 30
_SETTLED_AFTER = timedelta(days=2)
_RECENT_SECONDS = 30 * 60
_CACHE_SIZE = 64
# With no registration date to stop at, paging back stops at January 1 this many years ago.
_FALLBACK_YEARS = 3
# For `command_errors`, which a usage read never needs: Kohler's offline answer comes back
# from `async_get_usage` as `{}`, like every failure but a rejected sign-in.
_OFFLINE = "Kohler reports this device offline."


class UsageUnavailable(HomeAssistantError):
    """Kohler didn't answer for the period asked for."""


Fetch = Callable[[], Awaitable[dict[str, Any]]]


@dataclass
class _UsageCache:
    """Kohler's answers for periods the integration doesn't hold; see the module docstring."""

    entries: OrderedDict[tuple[str, ...], tuple[dict[str, Any], float | None]] = field(
        default_factory=OrderedDict
    )
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def get(self, key: tuple[str, ...]) -> dict[str, Any] | None:
        item = self.entries.get(key)
        if item is None:
            return None
        payload, expires = item
        if expires is not None and time.monotonic() > expires:
            del self.entries[key]
            return None
        self.entries.move_to_end(key)
        return payload

    def put(self, key: tuple[str, ...], payload: dict[str, Any], settled: bool) -> None:
        expires = None if settled else time.monotonic() + _RECENT_SECONDS
        self.entries[key] = (payload, expires)
        self.entries.move_to_end(key)
        while len(self.entries) > _CACHE_SIZE:
            self.entries.popitem(last=False)

    async def read(
        self, key: tuple[str, ...], account: str, fetch: Fetch, settled: bool
    ) -> dict[str, Any]:
        """The kept answer for ``key``, else ``fetch``'s, kept if Kohler answered."""
        async with self.locks.setdefault(account, asyncio.Lock()):
            cached = self.get(key)
            if cached is not None:
                return cached
            payload = await fetch()
            # `async_get_usage` answers `{}` for a failed read.
            if payload:
                self.put(key, payload, settled)
            return payload


_DATA_CACHE: HassKey[_UsageCache] = HassKey(f"{DOMAIN}_sidebar_usage")


def period_bounds(period: str, anchor: date, week_start: int) -> tuple[date, date]:
    """The calendar period around ``anchor``; ``week_start`` counts Monday as 0."""
    if period == "week":
        start = anchor - timedelta(days=(anchor.weekday() - week_start) % 7)
        return start, start + timedelta(days=6)
    if period == "month":
        start = anchor.replace(day=1)
        following = (start + timedelta(days=32)).replace(day=1)
        return start, following - timedelta(days=1)
    return date(anchor.year, 1, 1), date(anchor.year, 12, 31)


def _earliest(ref: DeviceRef, today: date) -> date:
    """How far back paging may go: the device's registration, else a few years."""
    if ref.kind == "valve":
        raw = ref.target.created_time
    elif ref.kind == "faucet":
        raw = ref.target.about.get("installedDate")
    else:
        # The other products report no install date.
        raw = None
    stamp = kohler_timestamp(raw)
    if stamp is None:
        return date(today.year - _FALLBACK_YEARS, 1, 1)
    return min(dt_util.as_local(stamp).date(), today)


def _usage_product(ref: DeviceRef) -> str:
    """Which usage read serves this device (`KohlerClient.async_get_usage`)."""
    if ref.kind == "valve":
        return "gcs"
    if ref.kind == "faucet":
        return "faucet"
    return ref.target.usage_product


def _volume(litres: float, metric: bool) -> float:
    """Litres in the account's unit, to two decimals for the chart."""
    return round(litres if metric else usage_volume_gallons(litres), 2)


@dataclass(frozen=True)
class _Buckets:
    rows: list[dict[str, Any]]
    litres: float
    seconds: float


def _row(
    key: str, litres: float, seconds: float, uses: int | None, metric: bool
) -> dict[str, Any]:
    return {
        "key": key,
        "volume": _volume(litres, metric),
        "minutes": round(seconds / 60, 1),
        "uses": uses,
    }


def _empty(key: str) -> dict[str, Any]:
    """A day or month not yet reached: drawn as a bare track."""
    return {"key": key, "volume": None, "minutes": None, "uses": None}


def _day_buckets(
    start: date, end: date, today: date, daily: dict[str, Any] | None, metric: bool
) -> _Buckets:
    """Each day of a week or month, summed as the usage sensors sum Kohler's buckets.

    Water dated after today is today's: some accounts' days are filed by UTC date, which in
    a US evening is already tomorrow.
    """
    litres: dict[date, float] = {}
    seconds: dict[date, float] = {}
    uses: dict[date, int] = {}
    for entry in usage_series(daily):
        day = _usage_bucket_date(entry.get("intervalKey"))
        if day is None or not start <= day <= end:
            continue
        day = min(day, today)
        litres[day] = litres.get(day, 0.0) + (_finite(entry.get("volume")) or 0.0)
        seconds[day] = seconds.get(day, 0.0) + (_finite(entry.get("onDuration")) or 0.0)
        count = _switch_ons(entry)
        if count is not None:
            uses[day] = uses.get(day, 0) + count
    rows: list[dict[str, Any]] = []
    day = start
    while day <= end:
        key = day.isoformat()
        if day > today:
            rows.append(_empty(key))
        else:
            rows.append(
                _row(
                    key,
                    litres.get(day, 0.0),
                    seconds.get(day, 0.0),
                    uses.get(day),
                    metric,
                )
            )
        day += timedelta(days=1)
    return _Buckets(rows, sum(litres.values()), sum(seconds.values()))


def _year_buckets(
    year: int,
    today: date,
    monthly: dict[str, Any] | None,
    daily: dict[str, Any] | None,
    metric: bool,
) -> _Buckets:
    """Each month of ``year`` as the This Month and This Year sensors count it."""
    litres = _month_totals(monthly, daily, figure="volume")
    seconds = _month_totals(monthly, daily, figure="onDuration")
    rows: list[dict[str, Any]] = []
    total_litres = total_seconds = 0.0
    for month in range(1, 13):
        key = f"{year}-{month:02d}"
        if date(year, month, 1) > today:
            rows.append(_empty(key))
            continue
        month_litres = litres.get(key, {}).get("volume", 0.0)
        month_seconds = seconds.get(key, {}).get("onDuration", 0.0)
        total_litres += month_litres
        total_seconds += month_seconds
        rows.append(_row(key, month_litres, month_seconds, None, metric))
    return _Buckets(rows, total_litres, total_seconds)


def _summary(
    period: str,
    anchor: date,
    bounds: tuple[date, date],
    today: date,
    earliest: date,
    buckets: _Buckets,
    metric: bool,
) -> dict[str, Any]:
    start, end = bounds
    reached = [row for row in buckets.rows if row["volume"] is not None]
    # Per day, over the days of the period that have come since the device's registration.
    days = max((min(end, today) - max(start, earliest)).days + 1, 1)
    previous = start - timedelta(days=1)
    following = end + timedelta(days=1)
    return {
        "period": period,
        "anchor": anchor.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "unit": "L" if metric else "gal",
        "buckets": buckets.rows,
        "total": {
            "volume": published(buckets.litres, "volume", metric),
            "minutes": published(buckets.seconds, "onDuration", metric),
        },
        "average": {
            "volume": _volume(buckets.litres / days, metric),
            "minutes": round(buckets.seconds / 60 / days, 1),
        },
        "high": {
            "volume": max((row["volume"] for row in reached), default=0.0),
            "minutes": max((row["minutes"] for row in reached), default=0.0),
        },
        "previous": previous.isoformat() if previous >= earliest else None,
        "next": following.isoformat() if following <= today else None,
    }


async def async_usage_history(
    hass: HomeAssistant,
    ref: DeviceRef,
    period: str,
    anchor: date | None,
    week_start: int,
) -> dict[str, Any]:
    """One period's buckets and figures for one device that reports its water usage.

    ``anchor`` is any day in the period, today when None; it is kept between the device's
    registration and today.
    """
    today = dt_util.now().date()
    earliest = _earliest(ref, today)
    anchor = min(max(anchor or today, earliest), today)
    start, end = period_bounds(period, anchor, week_start)
    metric = is_metric(ref.coordinator.water_units)
    target = ref.target
    cache = hass.data.setdefault(_DATA_CACHE, _UsageCache())

    async def fetch(interval: str) -> dict[str, Any]:
        """The period from Kohler, through the cache."""

        async def read() -> dict[str, Any]:
            # `command_errors` turns a rejected sign-in into a reauth prompt.
            with ref.coordinator.command_errors(_OFFLINE):
                return await target.client.async_get_usage(
                    ref.kohler_id,
                    from_date=start.isoformat(),
                    # Tomorrow at the latest, for a day already filed under its UTC date.
                    to_date=min(end, today + timedelta(days=1)).isoformat(),
                    interval=interval,
                    product=_usage_product(ref),
                )

        key = (ref.kohler_id, interval, start.isoformat(), end.isoformat())
        settled = end + _SETTLED_AFTER < today
        payload = await cache.read(key, ref.entry.entry_id, read, settled)
        if not payload:
            raise UsageUnavailable("Kohler didn't return usage for this period")
        return payload

    if period == "year":
        held = start.year == today.year
        monthly = target.usage if held else {}
        daily = target.usage_daily if held else {}
        # The year needs the monthly series unless the daily one reaches January 1.
        if not monthly and not (
            daily and _daily_spans_the_year(daily, today.strftime("%Y-%m"))
        ):
            monthly = await fetch("MONTH")
        buckets = _year_buckets(start.year, today, monthly, daily, metric)
    else:
        held = start >= today - timedelta(days=_DAILY_HELD_DAYS)
        daily = target.usage_daily if held else {}
        if not daily:
            daily = await fetch("DAY")
        buckets = _day_buckets(start, end, today, daily, metric)

    return _summary(period, anchor, (start, end), today, earliest, buckets, metric)
