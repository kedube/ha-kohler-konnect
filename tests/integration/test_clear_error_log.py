"""Clearing a valve's or an Anthem Plus controller's error log (2026-10-16).

The Konnect app's Diagnostics screen clears a valve's fault log with
``DELETE gcs-diagnostics/{id}``; the controller's ``hub-diagnostics/{id}`` takes the same
call. Each is a configuration button, and its device's Problem sensor follows the log.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect.const import DOMAIN

from .conftest import USERNAME, unload
from .test_coordinator import LEFT, MAIN, SHOWER_DATA, start, state
from .test_review_lifecycle import (
    OWNER_FAULT,
    SERVER_ERROR,
    LifecycleKohler,
    fault_log,
)

VALVE_BUTTON = "button.anthem_valve_clear_error_log"
VALVE_PROBLEM = "binary_sensor.anthem_valve_problem"
CONTROLLER_BUTTON = "button.anthem_plus_clear_error_log"
CONTROLLER_PROBLEM = "binary_sensor.anthem_plus_problem"


class ClearingKohler(LifecycleKohler):
    """Empties a fault log on its DELETE, as Kohler does, and records each one.

    ``relogged`` holds faults the device raises again at once, as one it still has.
    """

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.cleared: list[str] = []
        self.relogged: list[dict[str, Any]] = []

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        path = url.path
        queued = any(path.endswith(s) and q for s, q in self.api_queue.items())
        if method.lower() == "delete" and not queued:
            device_id = path.rstrip("/").rsplit("/", 1)[-1]
            if "/gcs-diagnostics/" in path:
                self.cleared.append(path)
                self.gcs_faults[device_id] = fault_log(*self.relogged)
                return self._respond(method, url, (200, {}))
            if "/hub-diagnostics/" in path:
                self.cleared.append(path)
                self.hub_errors[device_id] = {"errorDetails": list(self.relogged)}
                return self._respond(method, url, (200, {}))
        return await super()._api(method, url, data)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> ClearingKohler:
    return ClearingKohler(aioclient_mock)


@pytest.fixture
async def shower_entry(hass: HomeAssistant) -> AsyncGenerator[MockConfigEntry]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Kohler Konnect ({USERNAME})",
        unique_id=USERNAME,
        data=dict(SHOWER_DATA),
    )
    entry.add_to_hass(hass)
    yield entry
    await unload(hass, entry)


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()


async def test_clearing_a_valve_s_error_log_clears_its_problem_sensor(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ClearingKohler
) -> None:
    """The owner's fault 37, active in Shower Left's log since April."""
    kohler.gcs_faults[LEFT] = fault_log(OWNER_FAULT)
    await start(hass, shower_entry)
    assert state(hass, VALVE_PROBLEM) == "on"
    entry = er.async_get(hass).async_get(VALVE_BUTTON)
    assert entry is not None
    assert entry.entity_category is EntityCategory.CONFIG

    await _press(hass, VALVE_BUTTON)
    assert kohler.cleared == [
        f"/devices/api/v1/device-management/gcs-diagnostics/{LEFT}"
    ]
    assert state(hass, VALVE_PROBLEM) == "off"
    assert hass.states.get(VALVE_PROBLEM).attributes.get("active_faults") in (None, [])


async def test_a_fault_the_valve_still_has_shows_again_once_its_log_is_cleared(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ClearingKohler
) -> None:
    """Clearing the log is not clearing the fault: the log is read again at once."""
    kohler.gcs_faults[LEFT] = fault_log(OWNER_FAULT)
    kohler.relogged = [OWNER_FAULT]
    await start(hass, shower_entry)
    await _press(hass, VALVE_BUTTON)
    assert state(hass, VALVE_PROBLEM) == "on"


async def test_a_clear_kohler_refuses_says_so_and_keeps_the_faults(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ClearingKohler
) -> None:
    kohler.gcs_faults[LEFT] = fault_log(OWNER_FAULT)
    await start(hass, shower_entry)
    kohler.fail_api(f"/gcs-diagnostics/{LEFT}", SERVER_ERROR)
    with pytest.raises(HomeAssistantError, match="Kohler command failed"):
        await _press(hass, VALVE_BUTTON)
    assert state(hass, VALVE_PROBLEM) == "on"


async def test_clearing_a_controller_s_error_log_clears_its_problem_sensor(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ClearingKohler
) -> None:
    """The log itself, not the read of its active entries."""
    kohler.add_controller()
    await start(hass, shower_entry)
    assert state(hass, CONTROLLER_PROBLEM) == "on"
    entry = er.async_get(hass).async_get(CONTROLLER_BUTTON)
    assert entry is not None
    assert entry.entity_category is EntityCategory.CONFIG

    await _press(hass, CONTROLLER_BUTTON)
    assert kohler.cleared == [
        f"/devices/api/v1/device-management/hub-diagnostics/{MAIN}"
    ]
    assert state(hass, CONTROLLER_PROBLEM) == "off"
