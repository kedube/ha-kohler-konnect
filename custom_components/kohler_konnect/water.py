"""Water used today, this week, this month and this year — for valves and faucets alike.

Both kinds of device keep the same two usage series: monthly (`Interval=MONTH`, 400 days
back) and daily (`Interval=DAY`, 35 days back), read from `gcs-usage` or `faucet-usage`.
`konnect/usage.py` puts the two endpoints' buckets into one shape, so the four totals are
written once, here, as mixins. A valve or faucet sensor adds only the device it reads
(`_usage_series`), how it is named, and its unique id.

Everything below was written for the valves first, and the comments still speak of
showers; a faucet's series behaves the same way, refreshed on its own poll and after the
water stops rather than when a shower ends.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolume
from homeassistant.util import dt as dt_util

from .konnect.usage import is_metric, usage_series, usage_volume_gallons

_MONTH_KEY = re.compile(r"\d{4}-\d{2}")


def _usage_bucket_date(interval: object) -> date | None:
    """Return the calendar date from a `gcs-usage` interval key."""
    if not isinstance(interval, str):
        return None
    try:
        return date.fromisoformat(interval[:10])
    except ValueError:
        return None


def _month_totals(
    usage: dict[str, Any] | None, usage_daily: dict[str, Any] | None
) -> dict[str, dict[str, float]]:
    """Each calendar month's usage (`YYYY-MM` -> `volume` litres, `onDuration` seconds).

    Built from both series. A month takes whichever figure is larger: Kohler's own monthly
    bucket, or the sum of that month's daily buckets.

    * The **monthly** series is read at startup and again after the first shower of a new
      month, so its figure for the month in progress — or one that has ended since — can be
      the partial total from when it was read.
    * The **daily** series is re-read after every shower and spans 35 days: the whole
      current month and, early in a new one, all of the month before. Using it for every
      month it covers, not only the current one, is what stops `Water Used This Year`
      dropping when a month ends.
    * A daily window that starts mid-month sums less than that month really used, which is
      why the larger figure wins rather than the daily one.
    """
    totals: dict[str, dict[str, float]] = {}
    for entry in usage_series(usage):
        month = entry.get("intervalKey")
        volume = entry.get("volume")
        if not (isinstance(month, str) and _MONTH_KEY.fullmatch(month)):
            continue
        if not isinstance(volume, (int, float)):
            continue
        totals[month] = {"volume": float(volume)}
        duration = entry.get("onDuration")
        if isinstance(duration, (int, float)):
            totals[month]["onDuration"] = float(duration)

    daily: dict[str, dict[str, float]] = {}
    for entry in usage_series(usage_daily):
        bucket = _usage_bucket_date(entry.get("intervalKey"))
        volume = entry.get("volume")
        if bucket is None or not isinstance(volume, (int, float)):
            continue
        record = daily.setdefault(bucket.strftime("%Y-%m"), {"volume": 0.0})
        record["volume"] += float(volume)
        duration = entry.get("onDuration")
        if isinstance(duration, (int, float)):
            record["onDuration"] = record.get("onDuration", 0.0) + float(duration)

    for month, record in daily.items():
        if month not in totals or record["volume"] >= totals[month]["volume"]:
            totals[month] = record
    return totals


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

    **This is the number the Konnect app charts**, not a figure derived from the lifetime
    counter. It comes from `gcs-usage`, whose per-month series is seeded at startup and
    whose per-day series (`Interval=DAY`, trailing 35 days) is refreshed whenever a shower
    ends — keeping the current month's total live after every shower with no extra API call.

    `volume` arrives in **litres** whatever the account's unit setting; the app converts with
    0.264172 when `waterUnits` is `Standard`, and this follows that exactly so the value
    matches the app rather than merely being close.

    `TOTAL` with a `last_reset` of local midnight on the 1st: the figure starts again each
    month, and `last_reset` is how Home Assistant's long-term statistics record that as a
    new period. Without it, the drop on the 1st was counted as a month of negative usage.
    """

    _attr_icon = "mdi:calendar-month"
    # **Rendered once per usage update, not per MQTT message.** Keyed on the identity of both
    # usage payloads plus the calendar month so post-shower `usage_daily` refreshes and month
    # rollovers invalidate the cache automatically.
    _cache_key: tuple[int, int, str] | None = None
    _cached: tuple[dict[str, Any] | None, dict[str, Any]] = (None, {})

    def _rendered(self) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """This month's entry and the attribute dict, built once per usage payload.

        The month is matched on `intervalKey` rather than taken as the last entry: the series
        can end on a month the valve reported nothing for, and trusting position would then
        publish a stale month's total as the current one. When `usage_daily` has newer buckets
        for the current month from a finished shower, its rollup keeps this month's figure and
        running duration current without re-fetching `Interval=MONTH`.
        """
        usage, usage_daily = self._usage_series()
        month = dt_util.now().strftime("%Y-%m")
        key = (id(usage), id(usage_daily), month)
        if key == self._cache_key:
            return self._cached

        totals = _month_totals(usage, usage_daily)
        current = totals.get(month)
        if current is None and usage_series(usage_daily):
            # The daily series spans every day of this month and has nothing in it, so
            # nothing has been used yet: 0, as `Water Used Today` reads before the day's
            # first shower, rather than unknown.
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
                # Seconds on the wire; minutes is what a shower is measured in.
                attributes["running_minutes"] = round(float(duration) / 60, 1)

        self._cache_key = key
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
        """The month this covers, how long the valve ran, and the series behind it.

        `history` is every month Kohler returned, in the account's unit — enough to answer
        "how does this month compare" without a second call, and the reason the whole series
        is fetched rather than only the current month. Built once per payload; see
        `_rendered`.
        """
        return self._rendered()[1]


class YearlyWaterUsage(WaterUsage):
    """Water used in the current calendar year (Jan 1 – today), from Kohler's usage history.

    **Matches the Konnect app's Year tab (`Jan 1` – `Dec 31`, clamped to today)** as well as
    `Water Used Today` and `Water Used This Month`. Each month comes from `_month_totals`:
    Kohler's monthly series, or the daily series re-read after every shower wherever that is
    larger — so every shower updates this sensor alongside the other totals without an
    extra API call. Until 0.27 it summed the last twelve complete months instead.

    `TOTAL` with a `last_reset` of local midnight on January 1, for the same reason as
    `Water Used This Month`: the figure starts again each year, and without `last_reset`
    long-term statistics counted that as a year of negative usage.
    """

    _attr_icon = "mdi:calendar-range"
    _cache_key: tuple[int, int, str] | None = None
    _cached: tuple[float | None, dict[str, Any]] = (None, {})

    def _rendered(self) -> tuple[float | None, dict[str, Any]]:
        usage, usage_daily = self._usage_series()
        this_month = dt_util.now().strftime("%Y-%m")
        this_year = this_month[:4]
        key = (id(usage), id(usage_daily), this_month)
        if key == self._cache_key:
            return self._cached

        months = {
            month: record["volume"]
            for month, record in _month_totals(usage, usage_daily).items()
            if month.startswith(f"{this_year}-") and month <= this_month
        }

        total: float | None = None
        attributes: dict[str, Any] = {}
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

        self._cache_key = key
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


class DailyWaterUsage(WaterUsage):
    """Shared base for the day- and week-scoped water totals.

    Both read the same `Interval=DAY` series and differ only in how many trailing days they
    sum, so the parsing, the unit handling and the cache live here once.

    **`DAY` is the only sub-monthly interval this endpoint serves.** `WEEK` was refused at
    400, 90 and 28 days on 2026-09-11 — the last being four buckets, against the fifteen
    `DAY` returned happily in the same run — so a week is seven daily buckets rather than a
    `WEEK` call. `docs/protocol/platform.md` §6 records the probe.

    `volume` is litres on the wire whatever the account's unit, exactly as in the monthly
    series; the account's `waterUnits` decides only the display. Verified the day this was
    written: the daily entries for the current month summed to the same 465 L the `MONTH`
    series reported for it.
    """

    # `TOTAL`, not `TOTAL_INCREASING`: both of these fall — one at midnight, one as the
    # window rolls — and calling that a meter reset would inject phantom water into
    # long-term statistics. `Water Used Today` also sets `last_reset` to local midnight, so
    # statistics record each day as a new period; a rolling week has no start to give.
    _attr_state_class = SensorStateClass.TOTAL

    #: Trailing days to sum, counting today.
    _days: int = 1
    _cache_key: tuple[int, date, date] | None = None
    _cached: tuple[float | None, dict[str, Any]] = (None, {})

    def _rendered(self) -> tuple[float | None, dict[str, Any]]:
        """Sum the trailing `_days` buckets, cached on the payload's identity.

        Cached the same way the monthly sensors are: every MQTT message re-renders every
        entity, and this series changes only when a shower ends.
        """
        usage = self._usage_series()[1]
        local_today = dt_util.now().date()
        utc_today = datetime.now(UTC).date()
        key = (id(usage), local_today, utc_today)
        if key == self._cache_key:
            return self._cached

        # **Local dates, not UTC.** "Today" is the owner's today; the series keys its
        # buckets by calendar date, and comparing them against a UTC date would roll the
        # day over at the wrong hour for most of the world.
        wanted = [local_today - timedelta(days=offset) for offset in range(self._days)]

        litres = 0.0
        days: dict[str, float] = {}
        volumes: dict[date, float] = {}
        # `numberOfTimesValveSwitchedOn` rides in every bucket beside `volume` — how many
        # times the shower was turned on that day. A plain count, so published as is.
        # (`averageBlendTemperature` rides there too, but nothing — the app included —
        # states its unit, so it is left out rather than shown in a guessed one.)
        starts: dict[date, int] = {}
        for entry in usage_series(usage):
            interval = entry.get("intervalKey")
            bucket = _usage_bucket_date(interval)
            if bucket is None:
                continue
            volume = entry.get("volume")
            if not isinstance(volume, (int, float)):
                continue
            volumes[bucket] = volumes.get(bucket, 0.0) + float(volume)
            count = entry.get("numberOfTimesValveSwitchedOn")
            if isinstance(count, (int, float)) or (
                isinstance(count, str) and count.isdigit()
            ):
                starts[bucket] = starts.get(bucket, 0) + int(count)

        turned_on: int | None = None
        for day in wanted:
            bucket = day
            # Some accounts appear to expose the daily chart on UTC bucket labels. In US
            # evenings that can put local "today" under tomorrow's ISO date, which used to
            # render as zero/unknown even after water was used.
            if (
                day == local_today
                and utc_today > local_today
                and volumes.get(day, 0.0) == 0
                and volumes.get(utc_today, 0.0) > 0
            ):
                bucket = utc_today
            volume = volumes.get(bucket)
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
            if self._days > 1:
                # The per-day breakdown is the point of a rolling window: it says which day
                # the water went, which a single figure cannot.
                attributes["per_day"] = dict(sorted(days.items()))
                attributes["window_days"] = self._days
        elif volumes:
            # A valid daily series is present from Kohler, and no bucket exists in the
            # requested window yet (e.g. after midnight before the day's first shower).
            total = 0.0
            attributes = {"days_counted": 0}
            if self._days > 1:
                attributes["per_day"] = {}
                attributes["window_days"] = self._days

        self._cache_key = key
        self._cached = (total, attributes)
        return self._cached

    @property
    def native_value(self) -> float | None:
        return self._rendered()[0]

    @property
    def last_reset(self) -> datetime | None:
        """Local midnight for `Water Used Today`; None for the rolling week."""
        return dt_util.start_of_local_day() if self._days == 1 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._rendered()[1]
