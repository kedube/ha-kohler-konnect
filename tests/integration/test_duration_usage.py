"""The valve's duration totals, end to end inside a real Home Assistant.

`Duration Used Today`, `This Week`, `This Month` and `This Year` ride the same
`gcs-usage` reads as the water totals and follow the same periods. These check the
wiring — the fake Kohler cloud, through the real coordinator and sensor platform, to
the entities' states — where the unit tests in `tests/test_duration_usage.py` hold each
period rule on its own.
"""

from __future__ import annotations

from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from .conftest import VALVE_ID, account
from .test_review2_water import Review2Kohler
from .test_warmup import _set_up

TODAY = "sensor.anthem_valve_duration_used_today"
WEEK = "sensor.anthem_valve_duration_used_this_week"
MONTH = "sensor.anthem_valve_duration_used_this_month"
YEAR = "sensor.anthem_valve_duration_used_this_year"
WATER_TODAY = "sensor.anthem_valve_water_used_today"


class DurationKohler(Review2Kohler):
    """The review fake, with `onDuration` riding each `gcs-usage` bucket.

    `valve_usage` keeps each bucket's two wire figures: `volume` litres and `onDuration`
    seconds — the shape a real `gcs-usage` reply carries, so both totals read from the
    same rows.
    """

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        #: {Interval: {intervalKey: (volume, onDuration)}}
        self.valve_usage_2: dict[str, dict[str, tuple[float, float]]] = {}

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        get = method.lower() == "get"
        if get and url.path.endswith(f"/gcs-usage/{VALVE_ID}"):
            buckets = self.valve_usage_2.get(url.query.get("Interval"))
            if buckets is None:
                return self._respond(method, url, (404, {"message": "not found"}))
            rows = [
                {"intervalKey": key, "volume": v[0], "onDuration": v[1]}
                for key, v in buckets.items()
            ]
            return self._respond(method, url, (200, {"gcsUsageDataDetailsList": rows}))
        return await super()._api(method, url, data)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> DurationKohler:
    """Overrides the shared fixture: this account has a valve as well as the faucet."""
    return DurationKohler(aioclient_mock)


async def _mid_october(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, at: str
) -> None:
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to(at)


async def test_the_four_duration_entities_read_minutes_from_one_usage_read(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
    kohler: DurationKohler,
) -> None:
    """Ten minutes a day through October, and a monthly series for the year to sum."""
    await _mid_october(hass, freezer, "2026-10-16T16:00:00+00:00")  # noon, October 16
    kohler.valve_usage_2 = {
        "DAY": {f"2026-10-{day:02d}": (40.0, 600.0) for day in range(1, 17)},
        "MONTH": {
            **{f"2026-{month:02d}": (1000.0, 15000.0) for month in range(1, 10)},
            # October's monthly figure is the partial one read at startup; the daily
            # series, re-read after the month's showers, is ahead of it.
            "2026-10": (600.0, 9000.0),
        },
    }
    await _set_up(hass, config_entry)

    today = hass.states.get(TODAY)
    assert float(today.state) == 10.0
    assert today.attributes["device_class"] == "duration"
    assert today.attributes["unit_of_measurement"] == "min"
    assert today.attributes["state_class"] == "total"
    assert today.attributes["last_reset"].startswith("2026-10-16T00:00:00")

    week = hass.states.get(WEEK)
    assert float(week.state) == 70.0  # seven days, ten minutes each
    assert "last_reset" not in week.attributes
    assert week.attributes["per_day"]["2026-10-15"] == 10.0

    month = hass.states.get(MONTH)
    assert float(month.state) == 160.0  # 16 days x 10 min, the daily series ahead
    assert month.attributes["history"]["2026-09"] == 250.0  # 15 000 s

    year = hass.states.get(YEAR)
    assert float(year.state) == 2410.0  # Jan–Sep 250 min each + October's 160

    # The water twin reads the same rows: figures from one call, in the account's unit.
    assert hass.states.get(WATER_TODAY).attributes["unit_of_measurement"] == "L"
    assert float(hass.states.get(WATER_TODAY).state) == 40.0


async def test_a_shower_moves_both_figures_after_one_refresh(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
    kohler: DurationKohler,
) -> None:
    """Today's shower lands in both totals after the same post-shower usage read.

    The re-read a stopped shower schedules waits real seconds for Kohler to aggregate the
    session, which a frozen clock cannot wait out — so this calls the read the schedule
    lands on (`async_refresh_daily_usage`), the same method a stopped shower re-reads
    the day's series with.
    """
    await _mid_october(hass, freezer, "2026-10-16T16:00:00+00:00")
    kohler.valve_usage_2 = {"DAY": {"2026-10-16": (40.0, 600.0)}}
    await _set_up(hass, config_entry)
    assert float(hass.states.get(TODAY).state) == 10.0

    # The day's second shower: 40 L and 600 s more in today's bucket.
    kohler.valve_usage_2 = {"DAY": {"2026-10-16": (80.0, 1200.0)}}
    valve = account(hass, config_entry).valves[0]
    await valve.async_refresh_daily_usage(monthly=False)
    account(hass, config_entry).async_refresh_entities()
    await hass.async_block_till_done()

    assert float(hass.states.get(TODAY).state) == 20.0
    assert float(hass.states.get(WATER_TODAY).state) == 80.0
