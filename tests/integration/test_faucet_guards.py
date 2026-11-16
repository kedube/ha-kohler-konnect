"""Faucet guards against amounts, readings and replies that must not be acted on.

A dispense amount that is not a number, a poll read before a dispense, a configuration of
the wrong shape, an unusable leak time and a dispense answered after unload. Each test
here failed on the code before its fix, unless its docstring says it guards the fix itself.
"""

from __future__ import annotations

import asyncio
import math
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect.const import (
    FAUCET_CONFIG_REFRESH_INTERVAL,
    FAUCET_SCAN_INTERVAL_IDLE,
)
from custom_components.kohler_konnect.faucet.coordinator import leak_time

from .conftest import FakeKohler, faucet

NUMBER = "number.kitchen_dispense_amount"
SET_AMOUNT = "button.kitchen_dispense_set_amount"
QUICK = "button.kitchen_dispense_250_ml"
LEAK = "binary_sensor.kitchen_leak"
SWITCH = "switch.kitchen_water"


class HeldKohler(FakeKohler):
    """`FakeKohler` that can hold back one faucet-state read, or every dispense."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.hold_state = False
        self.hold_dispense = False
        self.holding = False
        self.gate = asyncio.Event()

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        if self.hold_state and "/faucet-state/" in url.path:
            self.hold_state = False
            # Answered as of the request, as a real reply is, but delivered later.
            response = await super()._api(method, url, data)
            self.holding = True
            await self.gate.wait()
            self.holding = False
            return response
        if self.hold_dispense and url.path.endswith("/dispense"):
            self.holding = True
            await self.gate.wait()
            self.holding = False
        return await super()._api(method, url, data)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> HeldKohler:
    return HeldKohler(aioclient_mock)


async def _spin() -> None:
    """Let tasks run while a held reply keeps `async_block_till_done` from returning."""
    for _ in range(50):
        await asyncio.sleep(0)


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


def _dispensed(kohler: FakeKohler) -> list[Any]:
    return [body["quantity"] for cmd, body in kohler.commands if cmd == "dispense"]


# --------------------------------------------------------------------------- #
# A dispense amount that is not a number is never sent
# --------------------------------------------------------------------------- #
async def test_the_dispense_amount_refuses_nan(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Home Assistant's own range check lets NaN through: it fails both comparisons."""
    before = hass.states.get(NUMBER).state
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "number",
            "set_value",
            {ATTR_ENTITY_ID: NUMBER, "value": "nan"},
            blocking=True,
        )
    assert err.value.translation_key == "amount_out_of_range"
    assert hass.states.get(NUMBER).state == before

    await _press(hass, SET_AMOUNT)
    assert _dispensed(kohler) == [0.25]


async def test_the_set_amount_button_refuses_an_amount_out_of_range(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    coordinator = faucet(hass, setup_entry)
    for ml in (math.nan, math.inf, 5.0, 20_000.0):
        coordinator.dispense_amount_ml = ml
        with pytest.raises(ServiceValidationError):
            await _press(hass, SET_AMOUNT)
    assert _dispensed(kohler) == []


@pytest.mark.parametrize("liters", [math.nan, math.inf, 0.0, 0.001, 12.0])
async def test_the_coordinator_never_sends_an_amount_out_of_range(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: HeldKohler,
    liters: float,
) -> None:
    """The last line before Kohler, whatever a caller checked."""
    with pytest.raises(ServiceValidationError):
        await faucet(hass, setup_entry).async_dispense(liters)
    assert _dispensed(kohler) == []


async def test_a_restored_nan_amount_falls_back_to_the_default(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(NUMBER, "nan"),
                {
                    "native_value": math.nan,
                    "native_unit_of_measurement": "mL",
                    "native_min_value": 10.0,
                    "native_max_value": 11360.0,
                    "native_step": 10.0,
                },
            ),
        ),
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert faucet(hass, config_entry).dispense_amount_ml == 250.0
    assert float(hass.states.get(NUMBER).state) == 250.0


# --------------------------------------------------------------------------- #
# A poll read before the dispense command does not end it
# --------------------------------------------------------------------------- #
async def test_a_poll_read_before_the_dispense_does_not_end_it(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: HeldKohler,
) -> None:
    coordinator = faucet(hass, setup_entry)
    # A scheduled poll reads the faucet ("Off"), and its reply is slow to come back.
    kohler.hold_state = True
    freezer.tick(FAUCET_SCAN_INTERVAL_IDLE)
    async_fire_time_changed(hass)
    await _spin()
    assert kohler.holding

    # A second later a dispense starts pouring.
    freezer.tick(timedelta(seconds=1))
    kohler.state["status"] = "On"
    press = hass.async_create_task(_press(hass, QUICK))
    await _spin()
    assert press.done()
    assert coordinator.is_dispensing()

    # The old poll's "Off" arrives well after the dispense had time to start.
    freezer.tick(timedelta(seconds=4))
    kohler.gate.set()
    await _spin()
    await hass.async_block_till_done()
    assert coordinator.is_dispensing()


# --------------------------------------------------------------------------- #
# A configuration whose `configuration` is not an object
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shape", [["about"], "unavailable"], ids=["list", "text"])
async def test_a_configuration_of_the_wrong_shape_leaves_the_faucet_working(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: HeldKohler,
    shape: Any,
) -> None:
    kohler.config["configuration"] = shape
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert faucet(hass, config_entry).about == {}
    for entity_id in (SWITCH, LEAK, "sensor.kitchen_status"):
        assert hass.states.get(entity_id) is not None, entity_id


# --------------------------------------------------------------------------- #
# A leak time that is no usable date
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    [math.nan, math.inf, 1_766_180_400_000_000, 10**400],
    ids=["nan", "inf", "microseconds", "huge"],
)
def test_an_unusable_leak_time_is_none(raw: Any) -> None:
    assert leak_time({"leakDetectionTime": raw}) is None


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        (1_772_435_640, 1_772_435_640.0),
        (1_766_180_400_000, 1_766_180_400.0),
        (-5, None),
        (0, None),
    ],
    ids=["seconds", "milliseconds", "negative", "zero"],
)
def test_a_leak_time_in_seconds_or_milliseconds_is_read(
    raw: int, seconds: float | None
) -> None:
    """Guards the fix itself: real times read as before."""
    assert leak_time({"leakDetectionTime": raw}) == seconds


async def test_a_leak_with_an_unusable_time_still_shows(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: HeldKohler,
) -> None:
    """A microsecond epoch is past any date; the leak still counts, without a time."""
    assert hass.states.get(LEAK).state == "off"
    kohler.config["leakDetectionHistory"] = [
        {"leakDetectionTime": 1_766_180_400_000_000}
    ]
    freezer.tick(FAUCET_CONFIG_REFRESH_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(LEAK)
    assert state.state == "on"
    assert state.attributes["last_detected"] is None
    assert faucet(hass, setup_entry).last_leak_at is None


# --------------------------------------------------------------------------- #
# A dispense answered after unload
# --------------------------------------------------------------------------- #
async def test_a_dispense_answered_after_unload_arms_no_timer(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    coordinator = faucet(hass, setup_entry)
    kohler.hold_dispense = True
    press = hass.async_create_task(_press(hass, QUICK))
    await _spin()
    assert kohler.holding

    unload = hass.async_create_task(
        hass.config_entries.async_unload(setup_entry.entry_id)
    )
    await _spin()
    kohler.gate.set()
    await _spin()
    assert await unload
    try:
        await press
    except HomeAssistantError:
        pass
    await hass.async_block_till_done()

    assert coordinator._dispense_timers == {}
    assert coordinator._dispense_started is None
    # The pour itself was asked for before the unload.
    assert _dispensed(kohler) == [0.25]


async def test_a_dispense_answered_before_unload_is_followed(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: HeldKohler
) -> None:
    """Guards the fix itself: the usual dispense is unchanged."""
    await _press(hass, QUICK)
    coordinator = faucet(hass, setup_entry)
    assert coordinator.is_dispensing()
    assert set(coordinator._dispense_timers) == {"dispense"}
    assert coordinator.last_dispense_liters == 0.25
