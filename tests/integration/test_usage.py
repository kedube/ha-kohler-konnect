"""The faucet's water totals: the same four as a valve's, read from `faucet-usage`."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_konnect.const import (
    FAUCET_SCAN_INTERVAL_ACTIVE,
    FAUCET_SCAN_INTERVAL_IDLE,
    FAUCET_USAGE_REFRESH_INTERVAL,
    FAUCET_USAGE_SETTLE,
)

from .conftest import DEVICE_ID, FakeKohler, account

TODAY = "sensor.kitchen_water_used_today"
WEEK = "sensor.kitchen_water_used_this_week"
MONTH = "sensor.kitchen_water_used_this_month"
YEAR = "sensor.kitchen_water_used_this_year"
USAGE_PATH = f"/faucet-usage/{DEVICE_ID}"
GALLONS_PER_LITRE = 0.264172


@pytest.fixture(autouse=True)
async def _mid_october(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """The fake account's usage is from 7 October 2026; read it that day."""
    await hass.config.async_set_time_zone("America/Los_Angeles")
    freezer.move_to("2026-10-07T12:00:00-07:00")


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _usage_queries(kohler: FakeKohler) -> list[dict[str, str]]:
    return [
        dict(url.query)
        for _, url, _, _ in kohler.mocker.mock_calls
        if "/faucet-usage/" in str(url)
    ]


async def test_usage_sensors(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    today = hass.states.get(TODAY)
    assert float(today.state) == pytest.approx(1.5274, abs=0.05)
    assert today.attributes["device_class"] == "water"
    assert today.attributes["state_class"] == "total"
    assert today.attributes["unit_of_measurement"] == "L"
    assert today.attributes["last_reset"].startswith("2026-10-07T00:00:00")
    assert float(hass.states.get(WEEK).state) == pytest.approx(1.5, abs=0.05)
    # The month takes the larger of Kohler's monthly figure and the daily sum.
    assert float(hass.states.get(MONTH).state) == pytest.approx(1.6, abs=0.05)
    # April 2025 is last year.
    assert float(hass.states.get(YEAR).state) == pytest.approx(1.6, abs=0.05)

    # The valves' windows: 400 days by month, 35 by day.
    month, day = _usage_queries(kohler)
    assert (month["Interval"], month["FromDate"], month["ToDate"]) == (
        "MONTH",
        "2025-09-02",
        "2026-10-07",
    )
    assert (day["Interval"], day["FromDate"], day["ToDate"]) == (
        "DAY",
        "2026-09-02",
        "2026-10-07",
    )


async def test_us_units_show_gallons(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """The Konnect account's own unit setting decides, as it does for the valves.

    Home Assistant converts water sensors to its own unit system for display, so this is
    seen with Home Assistant set to US units too.
    """
    hass.config.units = US_CUSTOMARY_SYSTEM
    kohler.water_units = "Standard"
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert account(hass, config_entry).water_units == "Standard"
    today = hass.states.get(TODAY)
    assert today.attributes["unit_of_measurement"] == "gal"
    assert float(today.state) == pytest.approx(1.5274 * GALLONS_PER_LITRE, abs=0.05)


async def test_refreshed_soon_after_the_water_stops(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.state["status"] = "On"
    await _advance(hass, freezer, FAUCET_SCAN_INTERVAL_IDLE)
    kohler.state["status"] = "Off"
    kohler.usage_days = {"2026-10-07": 2.0}
    await _advance(hass, freezer, FAUCET_SCAN_INTERVAL_ACTIVE)
    # Kohler needs a moment to count it.
    assert float(hass.states.get(TODAY).state) == pytest.approx(1.5, abs=0.05)

    await _advance(hass, freezer, FAUCET_USAGE_SETTLE)
    await _advance(hass, freezer, FAUCET_SCAN_INTERVAL_IDLE)
    assert float(hass.states.get(TODAY).state) == pytest.approx(2.0)


async def test_today_starts_over_at_midnight(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    freezer.move_to("2026-10-07T23:59:00-07:00")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert float(hass.states.get(TODAY).state) == pytest.approx(1.5, abs=0.05)

    await _advance(hass, freezer, timedelta(minutes=1))
    # 0 before the day's first use, from the first poll of the new day, not the next
    # 30-minute refresh.
    assert float(hass.states.get(TODAY).state) == 0.0
    assert _usage_queries(kohler)[-1]["ToDate"] == "2026-10-08"


async def test_refreshed_now_and_then(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _advance(hass, freezer, FAUCET_SCAN_INTERVAL_IDLE)
    assert len(_usage_queries(kohler)) == 2  # not on every poll

    kohler.usage_days["2026-10-07"] = 3.0
    await _advance(hass, freezer, FAUCET_USAGE_REFRESH_INTERVAL)
    assert float(hass.states.get(TODAY).state) == pytest.approx(3.0)
    # Only the daily series: the months before this one no longer change.
    assert [q["Interval"] for q in _usage_queries(kohler)] == ["MONTH", "DAY", "DAY"]


async def test_the_monthly_series_is_read_again_when_the_month_turns(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    freezer.move_to("2026-10-31T23:59:00-07:00")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    kohler.usage_months["2026-10"] = 5.0
    await _advance(hass, freezer, timedelta(minutes=1))
    assert [q["Interval"] for q in _usage_queries(kohler)] == [
        "MONTH",
        "DAY",
        "MONTH",
        "DAY",
    ]
    # October, now ended, takes Kohler's figure for it.
    assert float(hass.states.get(YEAR).state) == pytest.approx(5.0)


async def test_a_failed_monthly_read_is_tried_again(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.fail_api(USAGE_PATH, (500, {}))  # the first read: the monthly one
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await _advance(hass, freezer, FAUCET_USAGE_REFRESH_INTERVAL)
    assert [q["Interval"] for q in _usage_queries(kohler)] == [
        "MONTH",
        "DAY",
        "MONTH",
        "DAY",
    ]


async def test_usage_failure_keeps_the_last_values(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.fail_api(USAGE_PATH, (500, {}), (500, {}))
    await _advance(hass, freezer, FAUCET_USAGE_REFRESH_INTERVAL)
    assert float(hass.states.get(MONTH).state) == pytest.approx(1.6, abs=0.05)
    assert float(hass.states.get(TODAY).state) == pytest.approx(1.5, abs=0.05)
    # The faucet itself is unaffected.
    assert hass.states.get("switch.kitchen_water").state == "off"
