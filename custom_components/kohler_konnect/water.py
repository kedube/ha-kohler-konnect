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
    """Return the calendar date from a `gcs-usage` interval key."""
    if not isinstance(interval, str):
        return None
    try:
        return date.fromisoformat(interval[:10])
    except ValueError:
        return None


def _finite(value: Any) -> float | None:
    """A usage figure as a float, or None for anything that is not a finite number.

    Python's JSON parser takes a bare `NaN` or `Infinity` as a float, so `isinstance`
    alone let one through — and a single such bucket made every total built on it a
    value Home Assistant refuses to record. A bool is not a volume either.
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

    ``peaks`` is the caller's memory of the months that have ended, and ``this_month`` the
    month in progress; given both, a month before ``this_month`` never reads lower than it
    has before. See the note at the end.
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

    # **A month that has ended never reads lower than it has** (2026-10-16). Both of its
    # figures can sink after the month is over: about five days into the next month the
    # daily window slides past its first days, and the monthly series can still hold the
    # partial total from when it was read — the re-read after the new month's first shower
    # can fail, or land after the daily one. Either way `Water Used This Year` fell, which
    # statistics record as negative water, and counted it again once the monthly re-read
    # caught up. So each month keeps the highest figure it has had since it ended: in
    # memory, for this run. The month in progress is left out, because it is still moving
    # and the daily window always spans all of it.
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

    In January it always does: the 35-day window spans the whole month in progress, which
    is what `Water Used This Month` relies on every day of the year. Later, only a bucket
    dated January 1 or earlier proves the window reached back that far — a bucket is in
    the series only if the window covered its day. Without one there is no telling.
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

    See `DailyWaterUsage._rendered` for why it is owed, and `async_added_to_hass` for when
    it is taken back. Home Assistant's restore-state store keeps it: written when the entity
    is removed for a reload, at shutdown, and every fifteen minutes in between.
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
    # **Rendered once per usage update, not per MQTT message.** Keyed on both usage payloads
    # plus the calendar month, so post-shower `usage_daily` refreshes and month rollovers
    # invalidate the cache automatically. The payloads themselves are held and compared with
    # `is`: until 2026-10-16 the key was their `id()`, which Python hands out again once an
    # object is freed — so a new payload allocated where the last one had been could hit the
    # cache and publish the old figure.
    _cache_key: tuple[Any, Any, str] | None = None
    _cached: tuple[dict[str, Any] | None, dict[str, Any]] = (None, {})
    #: Each ended month's highest figure this run — see `_month_totals`. Per entity, and
    #: created on first use: these mixins never see `__init__`.
    _month_peaks: dict[str, dict[str, float]] | None = None

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

    **Unknown, not partial, while the monthly series is missing** — see `_rendered`.
    """

    _attr_icon = "mdi:calendar-range"
    # Held payloads compared with `is`, for the reason on `MonthlyWaterUsage._cache_key`.
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
        # ⚠️ **No monthly series, no year** — unless the daily one reaches back to January 1
        # (2026-10-16). The monthly read can fail at startup while the daily one succeeds,
        # and the year used to be summed from the daily window alone: 35 days of it. A year
        # of 10 000 L read 1 560 L after such a restart, so statistics recorded −8 440 L,
        # then +8 440 L once the monthly read recovered — and if it did not recover before
        # January 1, the loss stood for good. Unknown is skipped by the recorder instead, so
        # the sum carries on from the last real figure when the monthly series arrives; it
        # is read again on later seeds and usage reads until it answers. The month sensor
        # needs no such guard: its figure is the month in progress, which the daily window
        # always spans.
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

    A `RestoreEntity` since 2026-10-16, for `Water Used Today`'s sake: what it borrowed
    from tomorrow's bucket must outlive a restart (see `_rendered`; the week keeps nothing).
    Valve and faucet sensors both get it from here.
    """

    #: Trailing days to sum, counting today.
    _days: int = 1
    # `TOTAL` for both, and not `TOTAL_INCREASING`: both fall — one at midnight, one as the
    # window rolls — and calling that a meter reset would inject phantom water into
    # long-term statistics. `Water Used Today` also sets `last_reset` to local midnight, so
    # statistics record each day as a new period: it is the consumption meter, and the one
    # for the Energy dashboard.
    #
    # **A rolling window is a net total**, with no start to give as `last_reset`: its
    # long-term "change" is the window's own rise and fall, negative on a day a heavy day
    # leaves it. So it is no consumption meter, though its statistics are right for what it
    # is. 1.03 dropped its state class to keep those negative changes out, and Home
    # Assistant stopped tracking the statistics owners already had for it, with a
    # `state_class_removed` repair; at the owner's request it is `TOTAL` again the same day
    # (2026-10-16), and the existing statistic carries on under its old id.
    _attr_state_class = SensorStateClass.TOTAL
    # Held payload compared with `is`, for the reason on `MonthlyWaterUsage._cache_key`.
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

        Before the first state write — Home Assistant writes the state straight after this
        — so the first figure already leaves out what yesterday reported. Owed while its
        bucket is today's or still tomorrow's; once its day has passed, nothing is.
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

        Cached the same way the monthly sensors are: every MQTT message re-renders every
        entity, and this series changes only when a shower ends.
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
            volume = _finite(entry.get("volume"))
            if volume is None:
                continue
            volumes[bucket] = volumes.get(bucket, 0.0) + volume
            count = entry.get("numberOfTimesValveSwitchedOn")
            # Finite first, as every figure here: `int()` of a JSON `NaN` raises, which
            # took the whole sensor's value with it.
            starts_that_day = (
                int(count)
                if isinstance(count, str) and count.isdigit()
                else _finite(count)
            )
            if starts_that_day is not None:
                starts[bucket] = starts.get(bucket, 0) + int(starts_that_day)

        # Some accounts appear to expose the daily chart on UTC bucket labels. In US
        # evenings that can put local "today" under tomorrow's ISO date, which used to
        # render as zero/unknown even after water was used — so when today's own bucket is
        # empty, today reads that one instead.
        #
        # ⚠️ **What today borrowed must not be counted again tomorrow.** After local
        # midnight the borrowed bucket *is* today, under a new `last_reset`, and Home
        # Assistant counts the first value of a new period in full — so the evening's water
        # went into long-term statistics twice. `_borrowed` remembers how much of that
        # bucket today already reported, and tomorrow reads only the rest — for the whole
        # of tomorrow, restarts included. 1.03 held it in memory only, and a restart or
        # reload the next morning brought the whole bucket back under the same
        # `last_reset`: the evening's water counted twice after all. Home Assistant's
        # restore-state store keeps it now (`extra_restore_state_data`, read back in
        # `async_added_to_hass`). It is saved at shutdown, kept across a reload, and
        # written every fifteen minutes in between — so only a crash within a quarter of
        # an hour of the borrow can still lose it.
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
            # A valid daily series is present from Kohler, and no bucket exists in the
            # requested window yet (e.g. after midnight before the day's first shower).
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
