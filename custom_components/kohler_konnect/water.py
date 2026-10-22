"""Water used today, this week, this month and this year, for valves and faucets alike.

Both kinds of device keep the same two usage series: monthly (`Interval=MONTH`, 400 days
back) and daily (`Interval=DAY`, 35 days back), read from `gcs-usage` or `faucet-usage`.
`konnect/usage.py` puts both endpoints' buckets into one shape, so the four totals are
written once, here, as mixins. A valve or faucet sensor adds only the device it reads
(`_usage_series`), its name and its unique id. The daily series is re-read after the
water stops (a faucet also polls it); the monthly one at startup and again with the first
daily read of a new month.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolume
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util

from .konnect.usage import is_metric, usage_series, usage_volume_gallons

_MONTH_KEY = re.compile(r"\d{4}-\d{2}")


def _usage_bucket_date(interval: object) -> date | None:
    """Return the calendar date from a usage interval key."""
    if not isinstance(interval, str):
        return None
    try:
        return date.fromisoformat(interval[:10])
    except ValueError:
        return None


def _finite(value: Any) -> float | None:
    """A usage figure as a float, or None for anything that is not a finite number.

    Python's JSON parser accepts a bare `NaN` or `Infinity`, and one such bucket would
    make every total built on it a value Home Assistant refuses. A bool is not a volume.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _month_totals(
    usage: dict[str, Any] | None,
    usage_daily: dict[str, Any] | None,
    *,
    peaks: dict[str, dict[str, float]] | None = None,
    this_month: str | None = None,
) -> dict[str, dict[str, float]]:
    """Each calendar month's usage (`YYYY-MM` -> `volume` litres, `onDuration` seconds).

    A month takes the larger of Kohler's monthly bucket and the sum of its daily buckets.
    The monthly series is read rarely, so its figure for a recent month can be a partial
    total; the 35-day daily window can start mid-month and sum too little. Using the daily
    series for every month it covers keeps `Water Used This Year` from dropping when a
    month ends.

    ``peaks`` is the caller's memory of ended months (updated here) and ``this_month`` the
    month in progress; given both, an ended month never reads lower than it has this run.
    """
    totals: dict[str, dict[str, float]] = {}
    for entry in usage_series(usage):
        month = entry.get("intervalKey")
        volume = _finite(entry.get("volume"))
        if not (isinstance(month, str) and _MONTH_KEY.fullmatch(month)):
            continue
        if volume is None:
            continue
        totals[month] = {"volume": volume}
        duration = _finite(entry.get("onDuration"))
        if duration is not None:
            totals[month]["onDuration"] = duration

    daily: dict[str, dict[str, float]] = {}
    for entry in usage_series(usage_daily):
        bucket = _usage_bucket_date(entry.get("intervalKey"))
        volume = _finite(entry.get("volume"))
        if bucket is None or volume is None:
            continue
        record = daily.setdefault(bucket.strftime("%Y-%m"), {"volume": 0.0})
        record["volume"] += volume
        duration = _finite(entry.get("onDuration"))
        if duration is not None:
            record["onDuration"] = record.get("onDuration", 0.0) + duration

    for month, record in daily.items():
        if month not in totals or record["volume"] >= totals[month]["volume"]:
            totals[month] = record

    # ⚠️ An ended month must never read lower than it has: a drop in `Water Used This Year`
    # is recorded as negative water, then counted again on recovery. Both figures can sink
    # after the month ends (the daily window slides past its first days; the monthly
    # re-read can fail or lag), so each keeps its highest figure this run. The month in
    # progress is left out: it is still moving, and the daily window spans all of it.
    if peaks is not None and this_month is not None:
        for month in set(totals) | set(peaks):
            if month >= this_month:
                continue
            record, peak = totals.get(month), peaks.get(month)
            if peak is not None and (
                record is None or peak["volume"] > record["volume"]
            ):
                totals[month] = dict(peak)
            elif record is not None:
                peaks[month] = dict(record)
    return totals


def _daily_spans_the_year(usage_daily: dict[str, Any] | None, this_month: str) -> bool:
    """Whether the daily series alone holds every day of this year so far.

    Always in January, since the 35-day window spans the whole month in progress. Later,
    only a bucket dated January 1 or earlier proves the window reached back that far.
    """
    if this_month.endswith("-01"):
        return True
    new_year = date(int(this_month[:4]), 1, 1)
    for entry in usage_series(usage_daily):
        bucket = _usage_bucket_date(entry.get("intervalKey"))
        if bucket is not None and bucket <= new_year:
            return True
    return False


@dataclass(frozen=True)
class _Borrowed(ExtraStoredData):
    """What `Water Used Today` reported out of tomorrow's UTC bucket, kept over a restart.

    See `DailyWaterUsage._rendered`. Home Assistant's restore-state store writes it when
    the entity is removed for a reload, at shutdown, and every fifteen minutes.
    """

    day: date
    litres: float

    def as_dict(self) -> dict[str, Any]:
        return {"borrowed_day": self.day.isoformat(), "borrowed_litres": self.litres}

    @classmethod
    def from_dict(cls, data: Any) -> _Borrowed | None:
        """The stored record, or None for anything that is not one."""
        if not isinstance(data, dict):
            return None
        day = _usage_bucket_date(data.get("borrowed_day"))
        litres = data.get("borrowed_litres")
        if (
            day is None
            or isinstance(litres, bool)
            or not isinstance(litres, (int, float))
            or not math.isfinite(litres)
            or litres < 0
        ):
            return None
        return cls(day, float(litres))


def _start_of_local_month(now: datetime) -> datetime:
    return dt_util.start_of_local_day(now.date().replace(day=1))


class WaterUsage(SensorEntity):
    """What the four totals share: the device class, the series and the unit.

    The unit is the Konnect account's (`waterUnits`), for valves and faucets alike — the
    same choice the app makes for its charts.
    """

    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL

    def _usage_series(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """This device's (monthly, daily) usage payloads."""
        raise NotImplementedError

    def _water_units(self) -> str | None:
        """The account's `waterUnits`."""
        raise NotImplementedError

    @property
    def _metric(self) -> bool:
        return is_metric(self._water_units())

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfVolume.LITERS if self._metric else UnitOfVolume.GALLONS


class MonthlyWaterUsage(WaterUsage):
    """Water used in the current calendar month, from Kohler's own usage history.

    The figure the Konnect app charts, kept current by the daily series
    (`_month_totals`), so it moves after every use with no extra API call. Litres are
    converted with the app's own constant (`GALLONS_PER_LITRE`), so it matches the app.

    `TOTAL` with a `last_reset` of local midnight on the 1st, so long-term statistics
    record each month as a new period rather than the drop on the 1st as negative usage.
    """

    _attr_icon = "mdi:calendar-month"
    # Rendered once per usage update, not per MQTT message: keyed on both usage payloads
    # and the calendar month. The payloads are held and compared with `is`, not by `id()`,
    # which Python reuses once an object is freed.
    _cache_key: tuple[Any, Any, str] | None = None
    _cached: tuple[dict[str, Any] | None, dict[str, Any]] = (None, {})
    #: Each ended month's highest figure this run — see `_month_totals`. Per entity, and
    #: created on first use: these mixins never see `__init__`.
    _month_peaks: dict[str, dict[str, float]] | None = None

    def _rendered(self) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """This month's entry and the attribute dict, built once per usage payload.

        The month is matched on `intervalKey`, not taken as the last entry: the series can
        end on a month with nothing reported.
        """
        usage, usage_daily = self._usage_series()
        month = dt_util.now().strftime("%Y-%m")
        cached = self._cache_key
        if (
            cached is not None
            and cached[0] is usage
            and cached[1] is usage_daily
            and cached[2] == month
        ):
            return self._cached

        if self._month_peaks is None:
            self._month_peaks = {}
        totals = _month_totals(
            usage, usage_daily, peaks=self._month_peaks, this_month=month
        )
        current = totals.get(month)
        if current is None and usage_series(usage_daily):
            # The daily series spans every day of this month and has nothing in it, so
            # nothing has been used yet: 0, not unknown.
            current = {"volume": 0.0}

        attributes: dict[str, Any] = {}
        history: dict[str, float | None] = {
            key_: self._volume(record) for key_, record in sorted(totals.items())
        }
        if current is not None:
            history[month] = self._volume(current)
        if history:
            attributes["history"] = history
        if current is not None:
            attributes["month"] = month
            duration = current.get("onDuration")
            if isinstance(duration, (int, float)):
                # Seconds on the wire, shown in minutes.
                attributes["running_minutes"] = round(float(duration) / 60, 1)

        self._cache_key = (usage, usage_daily, month)
        self._cached = (current, attributes)
        return self._cached

    def _volume(self, entry: dict[str, Any]) -> float | None:
        """One entry's volume in the account's unit, or None when it is not a number."""
        litres = entry.get("volume")
        if not isinstance(litres, (int, float)):
            return None
        value = float(litres) if self._metric else usage_volume_gallons(float(litres))
        return round(value, 1)

    @property
    def _current(self) -> dict[str, Any] | None:
        return self._rendered()[0]

    @property
    def native_value(self) -> float | None:
        entry = self._current
        return None if entry is None else self._volume(entry)

    @property
    def last_reset(self) -> datetime:
        """Local midnight on the 1st — when this month's total started from zero."""
        return _start_of_local_month(dt_util.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The month this covers, how long the water ran, and the series behind it.

        `history` is every month Kohler returned, in the account's unit. Built once per
        payload; see `_rendered`.
        """
        return self._rendered()[1]


class YearlyWaterUsage(WaterUsage):
    """Water used in the current calendar year (Jan 1 – today), from Kohler's usage history.

    Matches the Konnect app's Year tab and the other totals: each month comes from
    `_month_totals`, so it moves after every use with no extra API call.

    `TOTAL` with a `last_reset` of local midnight on January 1, for the same reason as
    `MonthlyWaterUsage`. Unknown, not partial, while the monthly series is missing (see
    `_rendered`).
    """

    _attr_icon = "mdi:calendar-range"
    # Held payloads compared with `is`, as on `MonthlyWaterUsage._cache_key`.
    _cache_key: tuple[Any, Any, str] | None = None
    _cached: tuple[float | None, dict[str, Any]] = (None, {})
    #: As on `MonthlyWaterUsage`: each ended month's highest figure this run.
    _month_peaks: dict[str, dict[str, float]] | None = None

    def _rendered(self) -> tuple[float | None, dict[str, Any]]:
        usage, usage_daily = self._usage_series()
        this_month = dt_util.now().strftime("%Y-%m")
        this_year = this_month[:4]
        cached = self._cache_key
        if (
            cached is not None
            and cached[0] is usage
            and cached[1] is usage_daily
            and cached[2] == this_month
        ):
            return self._cached

        total: float | None = None
        attributes: dict[str, Any] = {}
        # ⚠️ No monthly series, no year, unless the daily one reaches back to January 1.
        # A year summed from the 35-day window alone would record a large negative change,
        # then a positive one once the monthly read recovers. The recorder skips unknown,
        # so the sum carries on from the last real figure when the monthly series arrives.
        if not usage_series(usage) and not _daily_spans_the_year(
            usage_daily, this_month
        ):
            self._cache_key = (usage, usage_daily, this_month)
            self._cached = (total, attributes)
            return self._cached

        if self._month_peaks is None:
            self._month_peaks = {}
        months = {
            month: record["volume"]
            for month, record in _month_totals(
                usage, usage_daily, peaks=self._month_peaks, this_month=this_month
            ).items()
            if month.startswith(f"{this_year}-") and month <= this_month
        }

        if months:
            ordered = sorted(months)
            litres = sum(months[key_] for key_ in ordered)
            value = litres if self._metric else usage_volume_gallons(litres)
            total = round(value, 1)
            attributes = {
                "year": this_year,
                "months_counted": len(ordered),
                "first_month": ordered[0],
                "last_month": ordered[-1],
                "per_month": {
                    key_: round(
                        (
                            months[key_]
                            if self._metric
                            else usage_volume_gallons(months[key_])
                        ),
                        1,
                    )
                    for key_ in ordered
                },
            }
        elif usage_series(usage) or usage_series(usage_daily):
            # Kohler answered, and nothing in it falls in this year: none used yet. 0 on
            # January 1 rather than unknown, matching `Water Used Today`.
            total = 0.0
            attributes = {"year": this_year, "months_counted": 0, "per_month": {}}

        self._cache_key = (usage, usage_daily, this_month)
        self._cached = (total, attributes)
        return self._cached

    @property
    def native_value(self) -> float | None:
        return self._rendered()[0]

    @property
    def last_reset(self) -> datetime:
        """Local midnight on January 1 — when this year's total started from zero."""
        return dt_util.start_of_local_day(dt_util.now().date().replace(month=1, day=1))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._rendered()[1]


class DailyWaterUsage(WaterUsage, RestoreEntity):
    """Shared base for the day- and week-scoped water totals.

    Both sum trailing buckets of the `Interval=DAY` series. `DAY` is the only sub-monthly
    interval Kohler serves (`docs/protocol/platform.md` §6), so a week is seven daily
    buckets.

    A `RestoreEntity` for `Water Used Today`'s sake: what it borrowed from tomorrow's
    bucket must outlive a restart (see `_rendered`; the week keeps nothing).
    """

    #: Trailing days to sum, counting today.
    _days: int = 1
    # `TOTAL`, not `TOTAL_INCREASING`: both fall (one at midnight, one as the window
    # rolls), and a meter reset would inject phantom water into long-term statistics.
    # `Water Used Today` also sets `last_reset` to local midnight, which makes it the
    # consumption meter for the Energy dashboard. The rolling week is a net total with no
    # `last_reset`: its statistics follow the window's rise and fall, negative on some
    # days. Keep its state class anyway: removing it orphans the statistics users already
    # have, with a `state_class_removed` repair.
    _attr_state_class = SensorStateClass.TOTAL
    # Held payload compared with `is`, as on `MonthlyWaterUsage._cache_key`.
    _cache_key: tuple[Any, date, date] | None = None
    _cached: tuple[float | None, dict[str, Any]] = (None, {})
    #: `Water Used Today` only: the bucket it last read under the UTC fallback in
    #: `_rendered`, and the litres it reported from it. See there.
    _borrowed: tuple[date, float] | None = None

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """What `Water Used Today` borrowed, for the restore-state store to keep."""
        borrowed = self._borrowed if self._days == 1 else None
        return None if borrowed is None else _Borrowed(*borrowed)

    async def async_added_to_hass(self) -> None:
        """Take back a borrow from before a restart or reload, while it is still owed.

        Runs before the first state write, so the first figure already leaves out what
        yesterday reported. Owed while its bucket is today's or tomorrow's.
        """
        await super().async_added_to_hass()
        if self._days != 1 or self._borrowed is not None:
            return
        last = await self.async_get_last_extra_data()
        stored = None if last is None else _Borrowed.from_dict(last.as_dict())
        if stored is not None and stored.day >= dt_util.now().date():
            self._borrowed = (stored.day, stored.litres)
            self._cache_key = None

    def _rendered(self) -> tuple[float | None, dict[str, Any]]:
        """Sum the trailing `_days` buckets, cached on the payload's identity.

        Every MQTT message re-renders every entity; the series changes only after use.
        """
        usage = self._usage_series()[1]
        local_today = dt_util.now().date()
        utc_today = datetime.now(UTC).date()
        cached = self._cache_key
        if (
            cached is not None
            and cached[0] is usage
            and cached[1] == local_today
            and cached[2] == utc_today
        ):
            return self._cached

        # Local dates, not UTC: buckets are keyed by calendar date, and "today" is the
        # user's. The UTC fallback below is the exception.
        wanted = [local_today - timedelta(days=offset) for offset in range(self._days)]

        litres = 0.0
        days: dict[str, float] = {}
        volumes: dict[date, float] = {}
        # A valve's buckets carry `numberOfTimesValveSwitchedOn`, how many times the water
        # was turned on that day: a plain count, published as is.
        starts: dict[date, int] = {}
        for entry in usage_series(usage):
            interval = entry.get("intervalKey")
            bucket = _usage_bucket_date(interval)
            if bucket is None:
                continue
            volume = _finite(entry.get("volume"))
            if volume is None:
                continue
            volumes[bucket] = volumes.get(bucket, 0.0) + volume
            count = entry.get("numberOfTimesValveSwitchedOn")
            # Finite first: `int()` of a JSON `NaN` raises.
            starts_that_day = (
                int(count)
                if isinstance(count, str) and count.isdigit()
                else _finite(count)
            )
            if starts_that_day is not None:
                starts[bucket] = starts.get(bucket, 0) + int(starts_that_day)

        # Some accounts appear to label daily buckets by UTC date, which in US evenings puts
        # local "today" under tomorrow's date. So when today's own bucket is empty, today
        # reads that one instead.
        #
        # ⚠️ What today borrowed must not be counted again tomorrow: after local midnight
        # that bucket is today, under a new `last_reset`, and statistics count a new
        # period's first value in full. `_borrowed` records how much today reported, and
        # tomorrow reads only the rest, across restarts too (`extra_restore_state_data`,
        # `async_added_to_hass`). Only a crash before the restore-state store's next
        # fifteen-minute write can lose it.
        borrowed = self._borrowed if self._days == 1 else None
        if borrowed is not None and borrowed[0] < local_today:
            # Its day has come and gone; there is nothing left to subtract from.
            self._borrowed = borrowed = None
        counted_already = 0.0

        turned_on: int | None = None
        for day in wanted:
            bucket = day
            volume = volumes.get(day)
            if (
                day == local_today
                and borrowed is not None
                and borrowed[0] == day
                and volume is not None
            ):
                counted_already = min(borrowed[1], volume)
                volume -= counted_already
            if (
                day == local_today
                and utc_today > local_today
                and not volume
                and volumes.get(utc_today, 0.0) > 0
            ):
                bucket = utc_today
                volume = volumes[utc_today]
                counted_already = 0.0
                if self._days == 1:
                    self._borrowed = (utc_today, volume)
            elif day == local_today and borrowed is not None and borrowed[0] > day:
                # Today stopped borrowing — its own bucket filled in — so the figure it
                # reports no longer includes tomorrow's water, and tomorrow owes nothing.
                self._borrowed = None
            if volume is None:
                continue
            if bucket in starts:
                turned_on = (turned_on or 0) + starts[bucket]
            litres += volume
            days[bucket.isoformat()] = round(
                volume if self._metric else usage_volume_gallons(volume),
                1,
            )

        total: float | None = None
        attributes: dict[str, Any] = {}
        if days:
            value = litres if self._metric else usage_volume_gallons(litres)
            total = round(value, 1)
            attributes = {"days_counted": len(days)}
            if turned_on is not None:
                attributes["times_turned_on"] = turned_on
            if self._days == 1:
                bucket_date = next(iter(days))
                if bucket_date != local_today.isoformat():
                    attributes["bucket_date"] = bucket_date
                if counted_already:
                    # Left out of today because yesterday already reported it; see above.
                    attributes["counted_yesterday"] = round(
                        counted_already
                        if self._metric
                        else usage_volume_gallons(counted_already),
                        1,
                    )
            if self._days > 1:
                # The per-day breakdown is the point of a rolling window: it says which day
                # the water went, which a single figure cannot.
                attributes["per_day"] = dict(sorted(days.items()))
                attributes["window_days"] = self._days
        elif volumes:
            # Kohler answered, and no bucket falls in the window yet (e.g. after midnight,
            # before the day's first use).
            total = 0.0
            attributes = {"days_counted": 0}
            if self._days > 1:
                attributes["per_day"] = {}
                attributes["window_days"] = self._days

        self._cache_key = (usage, local_today, utc_today)
        self._cached = (total, attributes)
        return self._cached

    @property
    def native_value(self) -> float | None:
        return self._rendered()[0]

    @property
    def last_reset(self) -> datetime | None:
        """Local midnight for `Water Used Today`; None for the rolling week, a net total
        with no start to give."""
        return dt_util.start_of_local_day() if self._days == 1 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._rendered()[1]
