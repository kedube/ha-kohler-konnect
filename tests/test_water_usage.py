"""Water usage totals at the edges of their periods.

Run against a real time zone (New York) with the clock pinned, because every one of these
was a boundary bug: the month or year switching in UTC hours before it does locally, a
month that just ended falling back to a stale figure, a new period reading unknown, and
resets that long-term statistics could not tell from negative usage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from .conftest import make_coordinator, make_valve

# Where the water totals live, for pinning their clock.
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
    from custom_components.kohler_konnect import sensor as module

    valve = make_valve(_model(), [31, 11, 1])
    valve.usage = {"gcsUsageDataDetailsList": monthly}
    valve.usage_daily = {"gcsUsageDataDetailsList": daily}
    coordinator = make_coordinator([valve])
    return (
        module.ValveDailyWaterSensor(coordinator, valve),
        module.ValveWeeklyWaterSensor(coordinator, valve),
        module.ValveMonthlyWaterSensor(coordinator, valve),
        module.ValveYearlyWaterSensor(coordinator, valve),
    )


# Read at startup in mid-October: nine full months, and October's partial 600 L.
STARTUP_MONTHLY = [
    {"intervalKey": f"2026-{month:02d}", "volume": 1000.0} for month in range(1, 10)
] + [{"intervalKey": "2026-10", "volume": 600.0}]
# Re-read after the last shower of October: 40 L a day, 1240 L for the month.
OCTOBER_DAILY = [
    {"intervalKey": f"2026-10-{day:02d}", "volume": 40.0, "onDuration": 600}
    for day in range(1, 32)
]


def test_the_month_and_year_follow_local_time_not_utc(clock):
    """9 pm on Oct 31 in New York is already November in UTC; it is still October here."""
    clock("2026-11-01T01:00:00")
    _, _, month, year = _sensors(STARTUP_MONTHLY, OCTOBER_DAILY)
    assert month.extra_state_attributes["month"] == "2026-10"
    assert month.native_value == pytest.approx(327.6)  # 1240 L
    assert year.native_value == pytest.approx(2705.1)  # 9000 + 1240 L


def test_this_year_does_not_drop_when_a_month_ends(clock):
    """October keeps its daily total, not the partial monthly figure read at startup."""
    clock("2026-10-31T20:00:00")
    *_, before = _sensors(STARTUP_MONTHLY, OCTOBER_DAILY)
    clock("2026-11-01T05:00:00")
    *_, after = _sensors(STARTUP_MONTHLY, OCTOBER_DAILY)
    assert after.native_value == before.native_value
    assert after.extra_state_attributes["per_month"]["2026-10"] == pytest.approx(327.6)


def test_a_new_period_reads_zero_before_the_first_shower(clock):
    clock("2026-11-01T05:00:00")
    today, _, month, _ = _sensors(STARTUP_MONTHLY, OCTOBER_DAILY)
    assert today.native_value == 0.0
    assert month.native_value == 0.0
    assert month.extra_state_attributes["month"] == "2026-11"

    yearly = [{"intervalKey": f"2026-{m:02d}", "volume": 1000.0} for m in range(1, 13)]
    december = [
        {"intervalKey": f"2026-12-{d:02d}", "volume": 40.0} for d in range(1, 32)
    ]
    clock("2027-01-01T06:00:00")
    *_, year = _sensors(yearly, december)
    assert year.native_value == 0.0
    assert year.extra_state_attributes == {
        "year": "2027",
        "months_counted": 0,
        "per_month": {},
    }


def test_no_data_at_all_still_reads_unknown(clock):
    """Zero needs an answer from Kohler to stand on; an empty read is not one."""
    clock("2026-11-01T05:00:00")
    today, _, month, year = _sensors([], [])
    assert (today.native_value, month.native_value, year.native_value) == (
        None,
        None,
        None,
    )


def test_period_totals_say_when_their_period_started(clock):
    """`last_reset` at local midnight, so statistics see a new period, not negative water."""
    clock("2026-11-01T05:00:00")  # 1 am, November 1, New York
    today, week, month, year = _sensors(STARTUP_MONTHLY, OCTOBER_DAILY)
    assert today.last_reset == datetime(2026, 11, 1, tzinfo=NEW_YORK)
    assert month.last_reset == datetime(2026, 11, 1, tzinfo=NEW_YORK)
    assert year.last_reset == datetime(2026, 1, 1, tzinfo=NEW_YORK)
    # A rolling seven days has no start to report.
    assert week.last_reset is None


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
    assert totals["2026-09"] == {"volume": 900.0, "onDuration": 5400.0}
    assert totals["2026-10"] == {"volume": 1240.0, "onDuration": 18600.0}


# --------------------------------------------------------------------------- #
# Shared with the faucets (`konnect/usage.py`)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("water_units", "metric"),
    [("Standard", False), ("standard", False), (None, False), ("Metric", True)],
)
def test_anything_but_standard_is_metric(water_units, metric):
    """As the app reads `waterUnits`. The valves tested for `Liters`, which no account
    sends, so a metric account was shown gallons until the faucets merged in."""
    from custom_components.kohler_konnect.konnect.usage import is_metric

    assert is_metric(water_units) is metric


def test_a_metric_account_reads_litres(clock):
    clock("2026-10-15T16:00:00")
    _, _, month, _ = _sensors(STARTUP_MONTHLY, OCTOBER_DAILY)
    month.coordinator.water_units = "Metric"
    assert month.native_unit_of_measurement == "L"
    assert month.native_value == pytest.approx(1240.0)


def test_faucet_buckets_read_like_a_valves():
    from custom_components.kohler_konnect.konnect.usage import usage_series

    payload = {
        "faucetUsageDataDetailsList": [
            # Litres are in `waterUsage`; the bucket's own `volume` is not what the app
            # charts, and `usageDuration` is seconds.
            {
                "intervalKey": "2026-10-07",
                "waterUsage": 1.5,
                "volume": 99,
                "usageDuration": 30,
            },
            {"intervalKey": "2026-10-08", "quantity": 0.25},
            {"intervalKey": "2026-10-09", "waterUsage": -1},
            "junk",
        ]
    }
    assert usage_series(payload) == [
        {"intervalKey": "2026-10-07", "volume": 1.5, "onDuration": 30.0},
        {"intervalKey": "2026-10-08", "volume": 0.25},
        {"intervalKey": "2026-10-09", "volume": 0.0},
    ]
    assert usage_series(None) == []
    assert usage_series({"something": []}) == []
