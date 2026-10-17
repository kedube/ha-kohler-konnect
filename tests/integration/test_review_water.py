"""Review findings of 2026-10-16, end to end inside a real Home Assistant.

The unit tests in `tests/test_review_water.py` hold each decision; these check the wiring —
a message on the (fake) MQTT stream, through the real coordinator, to the entity's state
or a real HTTP write — for the findings where the wiring is the point.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect.const import CONF_WARMUP_AUTO_RESTORE

from .conftest import VALVE_ID
from .test_warmup import (
    ALL,
    OFF,
    RUNNING_WORD,
    STOPPED_WORD,
    WARMUP_SELECT,
    WarmKohler,
    _announce,
    _quiet,
    _report,
    _set_up,
    _until,
)

CLOUD = "binary_sensor.anthem_valve_cloud_connection"


class ReviewKohler(WarmKohler):
    """The warm-up fake, with the valve's `connectionState` settable and its reads counted."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.valve_connection = "Connected"
        self.gcs_state_reads = 0

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        if method.lower() == "get" and url.path.endswith(f"/gcs-state/{VALVE_ID}"):
            self.gcs_state_reads += 1
            state: dict[str, Any] = {"totalVolume": "1"}
            if self.warmup_mode is not None:
                state["warmUpState"] = {
                    "warmUp": self.warmup_mode,
                    "state": "warmUpNotInProgress",
                }
            body = {"state": state, "connectionState": self.valve_connection}
            return self._respond(method, url, (200, body))
        return await super()._api(method, url, data)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> ReviewKohler:
    """Overrides the shared fixture: this account has a valve as well as the faucet."""
    return ReviewKohler(aioclient_mock)


@pytest.fixture(autouse=True)
def short_waits() -> Generator[None]:
    """A minute's restore delay, the read-back retries and the evidence window, in ms."""
    with (
        patch(
            "custom_components.kohler_konnect.warmup_manager."
            "WARMUP_AUTO_RESTORE_DELAY_SECONDS",
            0.02,
        ),
        patch(
            "custom_components.kohler_konnect.warmup_manager."
            "WARMUP_CONTEXT_AFTER_SECONDS",
            0.005,
        ),
        patch(
            "custom_components.kohler_konnect.warmup_manager.WARMUP_READBACK_DELAYS",
            (0.0, 0.01, 0.01),
        ),
        patch(
            "custom_components.kohler_konnect.coordinator.USAGE_REFRESH_DELAY_SECONDS",
            0.01,
        ),
        patch(
            "custom_components.kohler_konnect.coordinator.USAGE_RETRY_DELAY_SECONDS",
            0.01,
        ),
    ):
        yield


# --------------------------------------------------------------------------- #
# 1. A rolling week is a net total, not a meter
# --------------------------------------------------------------------------- #
async def test_the_rolling_weeks_are_net_totals(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ReviewKohler
) -> None:
    """`TOTAL` and no `last_reset`, so statistics recorded before 1.03 carry on.

    1.03 dropped the state class, and Home Assistant raised `state_class_removed` for the
    statistics owners already had (restored 2026-10-16).
    """
    await _set_up(hass, config_entry)
    for week in (
        "sensor.anthem_valve_water_used_this_week",
        "sensor.kitchen_water_used_this_week",
    ):
        attributes = hass.states.get(week).attributes
        assert attributes["state_class"] == "total", week
        assert "last_reset" not in attributes, week
    for today in (
        "sensor.anthem_valve_water_used_today",
        "sensor.kitchen_water_used_today",
    ):
        assert hass.states.get(today).attributes["state_class"] == "total", today


# --------------------------------------------------------------------------- #
# 3. Cloud Connection clears on the valve's own message
# --------------------------------------------------------------------------- #
async def test_a_valve_seeded_disconnected_reads_connected_when_it_speaks(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ReviewKohler
) -> None:
    """The owner's diagnostics: `cloud_connected: false` from the startup seed.

    Kohler's cloud can go on saying `Disconnected` after a power cycle; the valve's own
    message is the better evidence, and needs no read to believe.
    """
    kohler.valve_connection = "Disconnected"
    await _set_up(hass, config_entry)
    assert hass.states.get(CLOUD).state == "off"
    reads = kohler.gcs_state_reads

    _report(STOPPED_WORD)
    await hass.async_block_till_done()

    state = hass.states.get(CLOUD)
    assert state.state == "on"
    assert state.attributes["connection_state"] == "Disconnected"
    assert "reported it offline" in state.attributes["reachable_because"]
    assert kohler.gcs_state_reads == reads


# --------------------------------------------------------------------------- #
# 5 and 6. Warm-up auto-restore
# --------------------------------------------------------------------------- #
async def test_a_disable_during_a_shower_is_put_back_once_the_water_stops(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ReviewKohler
) -> None:
    """Refused while the water ran, and then never tried again: the mode was already off.

    Waits on real time rather than `async_block_till_done`, which the restore waiting on
    the shower would hold up.
    """
    await _set_up(hass, config_entry, **{CONF_WARMUP_AUTO_RESTORE: True})
    _report(RUNNING_WORD)
    await hass.async_block_till_done()

    kohler.warmup_mode = OFF
    _announce(OFF)
    await _quiet()
    assert kohler.warmup_writes == []

    _report(STOPPED_WORD)
    await _until(lambda: kohler.warmup_writes == [ALL], "the restore")
    await hass.async_block_till_done()
    assert hass.states.get(WARMUP_SELECT).state == "All Outlets"


async def test_off_picked_on_the_dropdown_during_the_wait_stays_off(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ReviewKohler
) -> None:
    """The hub disables warm-up, and within the minute the owner chooses Off on purpose."""
    valve = await _set_up(hass, config_entry, **{CONF_WARMUP_AUTO_RESTORE: True})
    with patch(
        "custom_components.kohler_konnect.warmup_manager."
        "WARMUP_AUTO_RESTORE_DELAY_SECONDS",
        0.3,
    ):
        kohler.warmup_mode = OFF
        _announce(OFF)
        await _until(lambda: valve.warmup._warmup_restore_task is not None, "restore")
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": WARMUP_SELECT, "option": "Off"},
            blocking=True,
        )
        await _quiet(0.5)
        await hass.async_block_till_done()

    assert kohler.warmup_writes == [OFF]
    assert hass.states.get(WARMUP_SELECT).state == "Off"
