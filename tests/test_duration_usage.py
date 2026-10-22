"""Duration totals at the edges of their periods.

`Duration Used Today`, `This Week`, `This Month` and `This Year` are the `Water Used`
totals' twins: the same two usage series, the same bucketing and the same period rules,
summed over `onDuration` — the seconds the water ran — and published in minutes. So these
tests mirror `test_water_usage`'s, which were written against real boundary bugs, and add
the boundaries the two figures do not share: a bucket that carries a volume but no
duration, and the unit itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTime, UnitOfVolume
from homeassistant.util import dt as dt_util

from .conftest import make_coordinator, make_valve

# Where the totals live, for pinning their clock.
WATER = "custom_components.kohler_konnect.water"

NEW_YORK = ZoneInfo("America/New_York")


def _model():
    from custom_components.kohler_konnect.konnect.models import get_valve_model

    return get_valve_model("K-28210")


@pytest.fixture
def clock(monkeypatch):
    """Pin "now" to a UTC instant, with Home Assistant's time zone set to New York."""

    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", NEW_YORK)

    def pin(utc_iso: str) -> None:
        fixed = datetime.fromisoformat(utc_iso).replace(tzinfo=UTC)

        class _Pinned(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed if tz in (None, UTC) else fixed.astimezone(tz)

        monkeypatch.setattr(f"{WATER}.datetime", _Pinned)
        monkeypatch.setattr(
            dt_util,
            "now",
            lambda time_zone=None: fixed.astimezone(time_zone or NEW_YORK),
        )

    return pin


def _sensors(monthly, daily):
    """The four duration totals, as a valve's entities build them."""
    from custom_components.kohler_konnect import sensor as module

    valve = make_valve(_model(), [31, 11, 1])
    valve.usage = {"gcsUsageDataDetailsList": monthly}
    valve.usage_daily = {"gcsUsageDataDetailsList": daily}
    coordinator = make_coordinator([valve])
    return (
        module.ValveDailyDurationSensor(coordinator, valve),
        module.ValveWeeklyDurationSensor(coordinator, valve),
        module.ValveMonthlyDurationSensor(coordinator, valve),
        module.ValveYearlyDurationSensor(coordinator, valve),
    )


# Read at startup in mid-October: nine full months, and October's partial 600 L / 10 000 s.
STARTUP_MONTHLY = [
    {"intervalKey": f"2026-{month:02d}", "volume": 1000.0, "onDuration": 15000}
    for month in range(1, 10)
] + [{"intervalKey": "2026-10", "volume": 600.0, "onDuration": 10000}]
# Re-read after the last shower of October: ten minutes a day, 310 minutes for the month.
OCTOBER_DAILY = [
    {"intervalKey": f"2026-10-{day:02d}", "volume": 40.0, "onDuration": 600}
    for day in range(1, 32)
]


def test_the_duration_unit_is_minutes_whatever_the_water_unit(clock):
    """`onDuration` is seconds on the wire; `waterUnits` decides litres, not time."""
    clock("2026-10-15T16:00:00")
    _, _, month, _ = _sensors(STARTUP_MONTHLY, OCTOBER_DAILY)
    for units in ("Standard", "Metric"):
        month.coordinator.water_units = units
        assert month.native_unit_of_measurement == UnitOfTime.MINUTES
    assert month.device_class == SensorDeviceClass.DURATION


def test_the_month_and_year_follow_local_time_not_utc(clock):
    """9 pm on Oct 31 in New York is already November in UTC; it is still October here."""
    clock("2026-11-01T01:00:00")
    _, _, month, year = _sensors(STARTUP_MONTHLY, OCTOBER_DAILY)
    assert month.extra_state_attributes["month"] == "2026-10"
    assert month.native_value == pytest.approx(310.0)  # 18 600 s
    assert year.native_value == pytest.approx(2560.0)  # 135 000 + 18 600 s
    # The water twin of the same bucket: what the two figures look like side by side.
    from custom_components.kohler_konnect import sensor as module

    valve = make_valve(_model(), [31, 11, 1])
    valve.usage = {"gcsUsageDataDetailsList": STARTUP_MONTHLY}
    valve.usage_daily = {"gcsUsageDataDetailsList": OCTOBER_DAILY}
    water = module.ValveMonthlyWaterSensor(make_coordinator([valve]), valve)
    assert water.native_unit_of_measurement == UnitOfVolume.GALLONS
    assert water.native_value == pytest.approx(327.6)  # 1240 L


def test_a_new_period_reads_zero_before_the_first_shower(clock):
    clock("2026-11-01T05:00:00")
    today, _, month, _ = _sensors(STARTUP_MONTHLY, OCTOBER_DAILY)
    assert today.native_value == 0.0
    assert month.native_value == 0.0
    assert month.extra_state_attributes["month"] == "2026-11"


def test_no_data_at_all_still_reads_unknown(clock):
    """Zero needs an answer from Kohler to stand on; an empty read is not one."""
    clock("2026-11-01T05:00:00")
    today, _, month, year = _sensors([], [])
    assert (today.native_value, month.native_value, year.native_value) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize("now", ["2026-10-15T16:00:00", "2026-01-15T17:00:00"])
def test_a_reply_without_durations_reads_unknown_not_zero(clock, now):
    """Buckets with litres but no `onDuration` don't say that no time passed."""
    clock(now)
    monthly = [{"intervalKey": "2025-12", "volume": 1000.0}]
    daily = [{"intervalKey": now[:10], "volume": 40.0}]
    sensors = _sensors(monthly, daily)
    assert [sensor.native_value for sensor in sensors] == [None] * 4


def test_period_totals_say_when_their_period_started(clock):
    """`last_reset` at local midnight, so statistics see a new period, not negative time."""
    clock("2026-11-01T05:00:00")  # 1 am, November 1, New York
    today, week, month, year = _sensors(STARTUP_MONTHLY, OCTOBER_DAILY)
    assert today.last_reset == datetime(2026, 11, 1, tzinfo=NEW_YORK)
    assert month.last_reset == datetime(2026, 11, 1, tzinfo=NEW_YORK)
    assert year.last_reset == datetime(2026, 1, 1, tzinfo=NEW_YORK)
    # A rolling seven days has no start to report.
    assert week.last_reset is None


def test_the_duration_totals_follow_the_water_totals_period_rules():
    """Each of the four keeps the state class its water twin has, including the week's."""
    from custom_components.kohler_konnect import sensor as module
    from custom_components.kohler_konnect.faucet.sensor import FaucetWeeklyWaterSensor

    valve = make_valve(_model(), [31, 11, 1])
    coordinator = make_coordinator([valve])
    for sensor in (
        module.ValveDailyDurationSensor(coordinator, valve),
        module.ValveMonthlyDurationSensor(coordinator, valve),
        module.ValveYearlyDurationSensor(coordinator, valve),
    ):
        assert sensor.state_class is SensorStateClass.TOTAL
    week = module.ValveWeeklyDurationSensor(coordinator, valve)
    assert week.state_class is SensorStateClass.TOTAL
    assert week.last_reset is None
    # The water twins beside them, unchanged: this test guards the sharing of the rules.
    assert (
        module.ValveWeeklyWaterSensor(coordinator, valve).state_class
        is SensorStateClass.TOTAL
    )
    assert (
        FaucetWeeklyWaterSensor.__new__(FaucetWeeklyWaterSensor).state_class
        is SensorStateClass.TOTAL
    )


def test_each_month_takes_the_larger_of_its_two_figures():
    from custom_components.kohler_konnect.water import _month_totals

    monthly = {
        "gcsUsageDataDetailsList": [
            {"intervalKey": "2026-09", "volume": 900.0, "onDuration": 5400},
            {"intervalKey": "2026-10", "volume": 600.0},
        ]
    }
    # The 35-day window starts on Sep 27, so September's daily sum is partial.
    daily = {
        "gcsUsageDataDetailsList": [
            {"intervalKey": f"2026-09-{d}", "volume": 40.0} for d in (27, 28, 29, 30)
        ]
        + [
            {"intervalKey": f"2026-10-{d:02d}", "volume": 40.0, "onDuration": 600}
            for d in range(1, 32)
        ]
    }
    totals = _month_totals(monthly, daily)
    assert totals["2026-09"] == {"volume": 900.0}
    assert totals["2026-10"] == {"volume": 1240.0}

    # The duration run of the same series: the months with a duration are held by it.
    durations = _month_totals(monthly, daily, figure="onDuration")
    assert durations["2026-09"] == {"onDuration": 5400.0}
    assert durations["2026-10"] == {"onDuration": 18600.0}
    assert "volume" not in durations["2026-10"]


def test_a_bucket_with_no_duration_is_left_out_of_the_year_of_them(clock):
    """A month Kohler never gave a duration for cannot be summed into a year of them."""
    clock("2026-10-31T20:00:00")
    monthly = {
        "gcsUsageDataDetailsList": [
            {"intervalKey": "2026-08", "volume": 1000.0, "onDuration": 20000},
            {"intervalKey": "2026-09", "volume": 500.0},  # a volume, no duration
        ]
    }
    *_, year = _sensors(monthly["gcsUsageDataDetailsList"], [])
    assert year.native_value == pytest.approx(333.3)  # August alone: 20 000 s
    assert year.extra_state_attributes["per_month"] == {"2026-08": pytest.approx(333.3)}
    assert "2026-09" not in year.extra_state_attributes["per_month"]


def test_water_borrowed_from_tomorrows_bucket_is_not_counted_again_tomorrow(clock):
    """The UTC borrow the water totals keep, kept for durations too — in seconds."""
    clock("2026-10-16T01:00:00")  # 9 pm, October 15, New York
    daily = [
        {"intervalKey": "2026-10-14", "volume": 40.0, "onDuration": 1200},
        {"intervalKey": "2026-10-16", "volume": 30.0, "onDuration": 900},
    ]
    today, week, _, _ = _sensors([], daily)
    assert today.native_value == 15.0
    assert today.extra_state_attributes["bucket_date"] == "2026-10-16"
    assert (
        week.native_value == 35.0
    )  # the whole rolling window, borrowed bucket included

    clock("2026-10-16T04:00:01")  # local midnight: a new period, the same bucket
    assert today.native_value == 0.0
    assert today.extra_state_attributes["counted_yesterday"] == 15.0
    assert week.native_value == 35.0  # the week never counted it but once


def test_the_seconds_behind_the_minutes_are_only_published_rounded(clock):
    """`per_day` and `history` are minutes too — nothing shows a raw second figure."""
    clock("2026-10-15T16:00:00")
    _, week, month, _ = _sensors(STARTUP_MONTHLY, OCTOBER_DAILY)
    # The rolling window ends today, October 15.
    assert week.extra_state_attributes["per_day"]["2026-10-15"] == 10.0
    # Every published figure is minutes: 600 s -> 10.0, never 600.
    assert 600 not in week.extra_state_attributes["per_day"].values()
    assert month.extra_state_attributes["history"]["2026-10"] == pytest.approx(310.0)
