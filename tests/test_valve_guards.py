"""Valve and account values that must never be counted twice, raise, or be written.

Water Used Today counts each bucket once across midnight; an interface block, a
`Retry-After` or token claims of an odd shape read as unknown; and an outlet record with
an unusable run time, or a minimum above its maximum, is refused rather than written.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.kohler_konnect.coordinator import Valve
from custom_components.kohler_konnect.konnect.auth import decode_tenant_id
from custom_components.kohler_konnect.konnect.client import KohlerError, _retry_after
from custom_components.kohler_konnect.konnect.gcs import validate_outlet_record
from custom_components.kohler_konnect.konnect.models import get_valve_model
from custom_components.kohler_konnect.konnect.state import OutletLimits

from .conftest import make_coordinator, make_valve

WATER = "custom_components.kohler_konnect.water"
NEW_YORK = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------- #
# Water Used Today, on an account whose days are filed by UTC date
# --------------------------------------------------------------------------- #
@pytest.fixture
def pin(monkeypatch: pytest.MonkeyPatch):
    """Pin the clock to a UTC instant, with New York as the local time zone."""
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", NEW_YORK)

    def _pin(utc_iso: str) -> None:
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

    return _pin


def _daily(litres: dict[str, float]) -> dict:
    return {
        "gcsUsageDataDetailsList": [
            {"intervalKey": key, "volume": value} for key, value in litres.items()
        ]
    }


def _today_sensor():
    from custom_components.kohler_konnect import sensor

    valve = make_valve(get_valve_model("K-28210"), [31, 11, 1])
    coordinator = make_coordinator([valve])
    coordinator.water_units = "Metric"
    return valve, sensor.ValveDailyWaterSensor(coordinator, valve)


def test_a_day_that_pays_back_and_then_borrows_counts_each_bucket_once(pin):
    """Oct 15's evening shower is filed under Oct 16 and reported on Oct 15; on Oct 16
    evening, today borrowed Oct 17's bucket and forgot that Oct 16's was already
    counted, so the next fresh read reported it again."""
    valve, today = _today_sensor()

    # Oct 15, 9 pm EDT: the shower is filed under UTC Oct 16; today borrows it.
    pin("2026-10-16T01:00:00")
    valve.usage_daily = _daily({"2026-10-16": 30.0})
    assert today.native_value == 30.0

    # Oct 16 after local midnight: those 30 L were reported yesterday.
    pin("2026-10-16T04:00:01")
    assert today.native_value == 0.0

    # Oct 16, 9 pm and 10 pm EDT: two evening showers, filed under Oct 17.
    pin("2026-10-17T01:00:00")
    valve.usage_daily = _daily({"2026-10-16": 30.0, "2026-10-17": 25.0})
    today.native_value  # noqa: B018 - rendered, as each message re-renders it
    pin("2026-10-17T02:00:00")
    valve.usage_daily = _daily({"2026-10-16": 30.0, "2026-10-17": 45.0})
    oct_16 = today.native_value

    pin("2026-10-17T04:00:01")
    oct_17 = today.native_value

    # 75 L were used in all: the statistics count each day's final figure.
    assert 30.0 + oct_16 + oct_17 == 75.0


def test_a_restart_does_not_change_water_used_today(pin):
    """Restored from what was stored, the same data reads the same figure."""
    from custom_components.kohler_konnect import sensor
    from custom_components.kohler_konnect.water import _Borrowed

    valve, today = _today_sensor()
    pin("2026-10-16T01:00:00")
    valve.usage_daily = _daily({"2026-10-16": 30.0})
    assert today.native_value == 30.0
    pin("2026-10-16T04:00:01")
    assert today.native_value == 0.0
    pin("2026-10-17T01:00:00")
    valve.usage_daily = _daily({"2026-10-16": 30.0, "2026-10-17": 25.0})
    before = today.native_value
    extra = today.extra_restore_state_data

    after = sensor.ValveDailyWaterSensor(today.coordinator, valve)
    kept = None if extra is None else _Borrowed.from_dict(extra.as_dict())
    if kept is not None:
        after._borrowed = (kept.day, kept.litres)
    assert after.native_value == before


# --------------------------------------------------------------------------- #
# Values of an odd shape
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ui", ["7.2", ["7.2"], 72])
def test_an_interface_block_that_is_not_an_object_is_passed_over(ui):
    configuration = {
        "configuration": {"about": {"uI2": ui, "gateway": {"firmware": "1.0"}}},
        "version": "9.9",
    }
    valve = SimpleNamespace(configuration=configuration)
    valve.about = Valve.about.fget(valve)
    assert Valve.firmware.fget(valve) == "9.9"


@pytest.mark.parametrize("header", ["inf", "1e999", "Infinity", "nan", "-inf"])
def test_a_retry_after_that_is_no_number_of_seconds_is_ignored(header):
    assert _retry_after(429, header) is None


def test_a_retry_after_in_seconds_is_read():
    assert _retry_after(429, "120") == 120.0
    assert _retry_after(503, "-5") == 0.0


def _token(claims: object) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


@pytest.mark.parametrize(
    "claims", [[1], "text", 7, {"oid": 7}, {"oid": ""}, {"sub": None}]
)
def test_token_claims_without_a_usable_account_id_give_none(claims):
    assert decode_tenant_id(_token(claims)) is None


def test_the_account_id_is_read_from_the_token():
    assert decode_tenant_id(_token({"oid": "abc"})) == "abc"
    assert decode_tenant_id(_token({"sub": "def"})) == "def"


# --------------------------------------------------------------------------- #
# Outlet records
# --------------------------------------------------------------------------- #
# Outlet 0 as `OutletLimits` holds it: flow 16-200, run time 1800 s, temperatures
# 15.0 / 38.8 / 47.7 °C.
FULL = OutletLimits(0, 16, 200, 1800, 200, 31, 477, 150, 388, 1)


@pytest.mark.parametrize("run_time", [0, -1, 3601, 10**9])
def test_an_outlet_record_with_an_unusable_run_time_is_refused(run_time):
    """The valve's own shut-off: 0 or a huge value could leave water running."""
    with pytest.raises(KohlerError, match="maximumRuntime"):
        validate_outlet_record(FULL, maximum_run_time=run_time)


@pytest.mark.parametrize("run_time", [900, 3600])
def test_an_outlet_record_with_an_offered_run_time_is_written(run_time):
    record = validate_outlet_record(FULL, maximum_run_time=run_time)
    assert record["maximumRuntime"] == run_time


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        (
            OutletLimits(0, 120, 100, 1800, 110, 31, 477, 150, 388, 1),
            "minimumFlowrate 120 is above maximumFlowrate 100",
        ),
        (
            OutletLimits(0, 16, 200, 1800, 200, 31, 300, 400, 200, 1),
            "minimumOutletTemperature 400 is above maximumOutletTemperature 300",
        ),
    ],
)
def test_an_outlet_record_whose_minimum_is_above_its_maximum_is_refused(
    limits, message
):
    with pytest.raises(KohlerError, match=message):
        validate_outlet_record(limits)
