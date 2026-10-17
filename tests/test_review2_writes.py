"""The write-path review's second round (2026-10-16), the parts that need no Home Assistant.

The outlet record check that `GcsDevice.async_write_outlet_config` and
`Valve.async_write_outlet_setting` now share, so a write is refused before its first outlet
rather than at the outlet that refuses it; and the whole-degree bounds the Celsius controls
offer, none of which may round a limit past itself. The write path itself runs end to end in
`tests/integration/test_review2_writes.py`.
"""

from __future__ import annotations

import pytest

from custom_components.kohler_konnect.konnect.client import KohlerError
from custom_components.kohler_konnect.konnect.gcs import validate_outlet_record
from custom_components.kohler_konnect.konnect.models import get_valve_model
from custom_components.kohler_konnect.konnect.state import OutletLimits
from custom_components.kohler_konnect.number import (
    OutletDefaultTemperatureNumber,
    OutletMaxTemperatureNumber,
    ZoneTemperatureNumber,
    _whole_floor,
)
from custom_components.kohler_konnect.services import _temperature_bounds

from .conftest import make_coordinator, make_valve

FULL = OutletLimits(0, 16, 200, 1800, 200, 31, 477, 150, 388, 1)


# --------------------------------------------------------------------------- #
# One check for every outlet, before the first is written
# --------------------------------------------------------------------------- #
def test_the_outlet_record_check_returns_the_record_the_write_sends():
    """Wire units, the named fields overridden and everything else echoed."""
    record = validate_outlet_record(
        FULL, maximum_temperature_tenths=433, maximum_run_time=2700
    )
    assert record == {
        "outLetId": 0,
        "outLetType": 31,
        "outLetFlags": 1,
        "minimumOutletTemperature": 150,
        "defaultOutletTemperature": 388,
        "maximumOutletTemperature": 433,
        "minimumFlowrate": 16,
        "defaultFlowrate": 200,
        "maximumFlowrate": 200,
        "maximumRuntime": 2700,
    }


@pytest.mark.parametrize(
    ("limits", "overrides", "message"),
    [
        (
            OutletLimits(0, 16, 200, None, 200, 31, 477, 150, 388, 1),
            {},
            "maximumRuntime",
        ),
        (FULL, {"maximum_temperature_tenths": 489}, "maximumOutletTemperature"),
        (
            OutletLimits(0, 16, 255, 1800, 200, 31, 477, 150, 388, 1),
            {},
            "maximumFlowrate",
        ),
        (FULL, {"default_temperature_tenths": 480}, "above the scald limit"),
    ],
)
def test_the_outlet_record_check_refuses_what_the_write_would(
    limits, overrides, message
):
    with pytest.raises(KohlerError, match=message):
        validate_outlet_record(limits, **overrides)


# --------------------------------------------------------------------------- #
# A Celsius bound is never rounded past the limit it stands for
# --------------------------------------------------------------------------- #
def _outlet_settings(unit: str):
    valve = make_valve(get_valve_model("K-28210"), [31, 11, 1])
    coordinator = make_coordinator([valve])
    coordinator.temperature_unit = unit
    return (
        OutletMaxTemperatureNumber(coordinator, valve),
        OutletDefaultTemperatureNumber(coordinator, valve),
    )


def test_the_celsius_outlet_settings_stop_at_47_not_48():
    """118 °F, the app's top, is 47.7 °C; 48 would write 48.0, past it."""
    maximum, default = _outlet_settings("Celsius")
    assert maximum.native_max_value == 47
    assert default.native_max_value == 47


def test_the_fahrenheit_outlet_settings_keep_the_apps_range():
    maximum, default = _outlet_settings("Fahrenheit")
    assert (maximum.native_min_value, maximum.native_max_value) == (92, 118)
    assert (default.native_min_value, default.native_max_value) == (59, 118)


def test_the_celsius_custom_shower_form_stops_at_47_not_48():
    assert _temperature_bounds("Celsius") == (15.0, 47.0, "°C")
    assert _temperature_bounds("Fahrenheit") == (59.0, 118.0, "°F")


def test_the_celsius_zone_slider_stops_at_47_before_the_valve_reports_its_outlets():
    valve = make_valve(get_valve_model("K-28210"), [])
    coordinator = make_coordinator([valve])
    coordinator.temperature_unit = "Celsius"
    number = ZoneTemperatureNumber(coordinator, valve, 1)
    assert number.native_max_value == 47
    assert number.native_min_value == 14  # Cold, one below 15 °C


@pytest.mark.parametrize(
    ("tenths", "unit", "expected"),
    [
        (150, "Celsius", 15),
        (152, "Celsius", 16),  # 15 °C would write 15.0, below a 15.2 minimum
        (155, "Celsius", 16),
        (150, "Fahrenheit", 59),  # Kohler's table: 59 °F writes exactly 15.0
        (152, "Fahrenheit", 60),  # 59 °F writes 15.0, below a 15.2 minimum
        (153, "Fahrenheit", 60),
    ],
)
def test_a_floor_is_never_rounded_below_the_limit(tenths, unit, expected):
    assert _whole_floor(tenths, unit) == expected


@pytest.mark.parametrize(("unit", "floor"), [("Celsius", 16), ("Fahrenheit", 60)])
def test_the_zone_slider_offers_nothing_below_the_outlets_minimum(unit, floor):
    """Its lowest real setpoint is the first whole degree at or above the minimum."""
    valve = make_valve(get_valve_model("K-28210"), [31, 11, 1])
    coordinator = make_coordinator([valve])
    coordinator.temperature_unit = unit
    valve.gcs_state.outlet_limits[0].minimum_temperature_tenths = 152
    number = ZoneTemperatureNumber(coordinator, valve, 1)
    # The Cold stop is one step below that.
    assert number.native_min_value == floor - 1
