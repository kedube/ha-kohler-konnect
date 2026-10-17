"""The water totals in Home Assistant's Energy dashboard (2026-10-16).

The dashboard's water picker lists a statistic in a volume unit whose entity is a water
sensor, and its checks want a number in L, gal, m³ or ft³ with a total state class. These
run Home Assistant's own recorder and those checks against the valve's and the faucet's
`Today`, `This Month` and `This Year`, then follow them through a local midnight in the
hourly statistics the dashboard draws from, to show each litre is counted once.

`This Week` is left out on purpose: a rolling window is a net total, which falls when a heavy
day leaves it, so it is no meter to point the dashboard at.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from .conftest import account
from .test_review2_water import Review2Kohler
from .test_warmup import _set_up

VALVE_TOTALS = (
    "sensor.anthem_valve_water_used_today",
    "sensor.anthem_valve_water_used_this_month",
    "sensor.anthem_valve_water_used_this_year",
)
FAUCET_TOTALS = (
    "sensor.kitchen_water_used_today",
    "sensor.kitchen_water_used_this_month",
    "sensor.kitchen_water_used_this_year",
)
TOTALS = VALVE_TOTALS + FAUCET_TOTALS

#: The months before this one, and this month's figure Kohler's monthly series holds.
MONTHS = {"2026-09": 100.0, "2026-10": 5.0}

#: An evening and the night after it in New York, each a few minutes before the hour ends,
#: with the daily series as Kohler then has it: 10 L, 5 L more, 4 L after midnight, 2 L more.
DAY = [
    ("2026-10-16T00:56:00+00:00", {"2026-10-15": 10.0}),  # 8:56 pm, October 15
    ("2026-10-16T01:56:00+00:00", {"2026-10-15": 15.0}),  # 9:56 pm
    ("2026-10-16T04:56:00+00:00", {"2026-10-15": 15.0, "2026-10-16": 4.0}),  # 12:56 am
    ("2026-10-16T05:56:00+00:00", {"2026-10-15": 15.0, "2026-10-16": 6.0}),  # 1:56 am
]


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> Review2Kohler:
    """An account with a valve and a faucet."""
    return Review2Kohler(aioclient_mock)


@pytest.fixture
def mock_recorder_before_hass(async_setup_recorder_instance: Any) -> None:
    """The recorder's database comes before `hass`, as the test plugin requires."""


def _use(kohler: Review2Kohler, days: dict[str, float]) -> None:
    """Kohler's usage series for both devices, with these days in the daily one."""
    kohler.usage_months = dict(MONTHS)
    kohler.usage_days = dict(days)
    kohler.valve_usage = {"MONTH": dict(MONTHS), "DAY": dict(days)}


async def _start(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    entry: MockConfigEntry,
    kohler: Review2Kohler,
) -> Any:
    """Set the account up at the first moment of `DAY`, and return the valve."""
    await hass.config.async_set_time_zone("America/New_York")
    at, days = DAY[0]
    freezer.move_to(at)
    _use(kohler, days)
    return await _set_up(hass, entry)


async def _compile_the_hour(hass: HomeAssistant) -> None:
    """Compile the hour now ending into long-term statistics, as the recorder does at :55."""
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
        do_adhoc_statistics,
    )

    await async_wait_recording_done(hass)
    start = dt_util.utcnow().replace(minute=55, second=0, microsecond=0)
    do_adhoc_statistics(hass, start=start)
    await async_wait_recording_done(hass)


async def test_every_water_total_passes_the_energy_dashboard_checks(
    recorder_mock: Any,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
    kohler: Review2Kohler,
) -> None:
    """As a water source and as an individual water device, for valve and faucet alike."""
    from homeassistant.components.energy import data as energy_data
    from homeassistant.components.energy.validate import async_validate
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import get_metadata

    await _start(hass, freezer, config_entry, kohler)
    await _compile_the_hour(hass)

    # What the dashboard's picker filters on: the entity's device class, and a `sum`
    # statistic of the volume unit class.
    recorded = await get_instance(hass).async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids=set(TOTALS))
    )
    assert set(recorded) == set(TOTALS)
    for entity_id in TOTALS:
        state = hass.states.get(entity_id)
        assert state.attributes.get("device_class") == "water", entity_id
        metadata = recorded[entity_id][1]
        assert metadata["has_sum"] is True, entity_id
        assert metadata["unit_class"] == "volume", entity_id
        assert metadata["unit_of_measurement"] == "L", entity_id

    manager = await energy_data.async_get_manager(hass)
    await manager.async_update(
        {
            "energy_sources": [
                {
                    "type": "water",
                    "stat_energy_from": entity_id,
                    "stat_cost": None,
                    "entity_energy_price": None,
                    "number_energy_price": None,
                }
                for entity_id in TOTALS
            ],
            "device_consumption_water": [
                {"stat_consumption": entity_id, "name": None} for entity_id in TOTALS
            ],
        }
    )
    validation = (await async_validate(hass)).as_dict()
    assert len(validation["energy_sources"]) == len(TOTALS)
    assert len(validation["device_consumption_water"]) == len(TOTALS)
    for section, results in validation.items():
        for issues in results:
            assert issues == [], (section, issues)


async def test_each_litre_is_counted_once_through_midnight(
    recorder_mock: Any,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
    kohler: Review2Kohler,
) -> None:
    """11 L after the first reading, whichever total the dashboard is pointed at.

    `Today` falls to 4 L at midnight and the month and year carry on, but each starts its
    new period from zero or carries its sum on, so the hourly sums the dashboard draws from
    climb by exactly the water used: never down, and nothing counted twice.
    """
    from homeassistant.components.recorder import get_instance
    from pytest_homeassistant_custom_component.components.recorder.common import (
        statistics_during_period,
    )

    valve = await _start(hass, freezer, config_entry, kohler)
    await _compile_the_hour(hass)
    first_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    for at, days in DAY[1:]:
        freezer.move_to(at)
        _use(kohler, days)
        # The faucet re-reads on its own clock and at a new day; a valve, when a shower
        # ends — done here directly, as the stream's end-of-shower read would.
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        await valve.async_refresh_daily_usage()
        account(hass, config_entry).async_refresh_entities()
        await _compile_the_hour(hass)

    stats = await get_instance(hass).async_add_executor_job(
        lambda: statistics_during_period(
            hass, first_hour, None, set(TOTALS), "hour", None, {"sum"}
        )
    )
    for entity_id in TOTALS:
        sums = [row["sum"] for row in stats[entity_id]]
        assert sums == [0.0, 5.0, 9.0, 11.0], entity_id

    today = (
        datetime.fromisoformat(DAY[-1][0]).astimezone(dt_util.get_default_time_zone())
    ).date()
    assert float(hass.states.get(VALVE_TOTALS[0]).state) == 6.0
    assert float(hass.states.get(FAUCET_TOTALS[0]).state) == 6.0
    assert dt_util.parse_datetime(
        hass.states.get(VALVE_TOTALS[0]).attributes["last_reset"]
    ) == dt_util.start_of_local_day(today)
    assert float(hass.states.get(VALVE_TOTALS[2]).state) == 121.0
