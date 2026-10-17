"""Regression tests for the final review of the 2026-10-16 lifecycle fixes."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import patch

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kohler_konnect import coordinator as coordinator_module
from custom_components.kohler_konnect.const import CONF_REPORT_LOG_FILE, DOMAIN

from . import test_review2_lifecycle as _lifecycle
from .conftest import account, wait_for
from .test_coordinator import LEFT, MAIN, start
from .test_review_lifecycle import SERVER_ERROR, LifecycleKohler, until

# The second round's account fixtures, shared rather than copied.
RotatingKohler = _lifecycle.RotatingKohler
built = _lifecycle.built
kohler = _lifecycle.kohler
prompt_usage_reads = _lifecycle.prompt_usage_reads
shower_entry = _lifecycle.shower_entry


# --------------------------------------------------------------------------- #
# A read that failed at setup is retried by the stream's first connect
# --------------------------------------------------------------------------- #
def _fail_the_setup_reads(kohler: LifecycleKohler) -> None:
    kohler.add_controller()
    kohler.fail_api(f"/gcsadvancestate/{LEFT}", SERVER_ERROR)
    kohler.fail_api(f"/gcs-preset/{LEFT}", SERVER_ERROR)
    kohler.fail_api(f"/hub-configuration/{MAIN}", SERVER_ERROR)


def _assert_everything_read(coordinator: Any) -> None:
    valve = coordinator.valves[0]
    assert valve.topology_confirmed, "the outlet layout was never read again"
    assert valve.gcs_state.outlet_limits, "outlet settings would be refused"
    assert valve.gcs_state.presets, "the Favorite list would be empty"
    assert coordinator.controllers[0].capabilities.known, "accessories would be missing"


async def test_a_setup_read_that_failed_is_retried_when_the_stream_comes_up_after(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: LifecycleKohler,
    built: list[Any],
) -> None:
    """Transient 500s at setup; 1.03's full reseed on the first connect healed them."""
    _fail_the_setup_reads(kohler)
    # The stream connects only after the setup's state reads went out.
    registration = kohler.hold("/mobile/settings")
    setup = hass.async_create_task(
        hass.config_entries.async_setup(shower_entry.entry_id)
    )
    await until(
        lambda: (
            kohler.reads(f"/gcs-state/{LEFT}") == 1
            and kohler.reads(f"/hub-state/{MAIN}") == 1
        ),
        "the setup's state reads",
    )
    registration.set()
    assert await setup
    coordinator = account(hass, shower_entry)
    await wait_for(hass, lambda: coordinator.stream.connected, "the stream")
    await hass.async_block_till_done()
    _assert_everything_read(coordinator)


async def test_the_same_when_the_stream_connects_during_the_seed(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: LifecycleKohler,
    built: list[Any],
) -> None:
    _fail_the_setup_reads(kohler)
    assert await hass.config_entries.async_setup(shower_entry.entry_id)
    coordinator = account(hass, shower_entry)
    await wait_for(hass, lambda: coordinator.stream.connected, "the stream")
    await hass.async_block_till_done()
    _assert_everything_read(coordinator)


# --------------------------------------------------------------------------- #
# A token stored while the shutdown waits for a refresh triggers no reload
# --------------------------------------------------------------------------- #
async def _refresh_in_flight(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> tuple[RotatingKohler, asyncio.Task]:
    kohler = RotatingKohler(aioclient_mock)
    old = await start(hass, entry)
    kohler.token_hold = True
    old.auth.invalidate_access_token()
    read = hass.async_create_task(old.client.async_get_gcs_state(LEFT))
    await until(lambda: len(kohler.token_gates) == 1, "the refresh")
    return kohler, read


def _release(kohler: RotatingKohler) -> None:
    kohler.token_hold = False
    for gate in list(kohler.token_gates):
        gate.set()


async def test_an_unload_with_a_refresh_in_flight_stays_unloaded(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    built: list[Any],
) -> None:
    """The listener read the stored token's write as "no coordinator, so reload"."""
    kohler, read = await _refresh_in_flight(hass, shower_entry, aioclient_mock)
    unload = hass.async_create_task(
        hass.config_entries.async_unload(shower_entry.entry_id)
    )
    await asyncio.sleep(0.2)
    _release(kohler)
    assert await unload
    await asyncio.gather(read, return_exceptions=True)
    await hass.async_block_till_done()
    assert shower_entry.state is ConfigEntryState.NOT_LOADED
    assert len(built) == 1


async def test_a_reload_with_a_refresh_in_flight_reloads_once(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    built: list[Any],
) -> None:
    kohler, read = await _refresh_in_flight(hass, shower_entry, aioclient_mock)
    reload = hass.async_create_task(
        hass.config_entries.async_reload(shower_entry.entry_id)
    )
    await asyncio.sleep(0.2)
    _release(kohler)
    assert await reload
    await asyncio.gather(read, return_exceptions=True)
    await hass.async_block_till_done()
    assert len(built) == 2, "a second, spurious reload followed"
    assert shower_entry.state is ConfigEntryState.LOADED


async def test_a_removal_with_a_refresh_in_flight_leaves_nothing_behind(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    built: list[Any],
) -> None:
    kohler, read = await _refresh_in_flight(hass, shower_entry, aioclient_mock)
    remove = hass.async_create_task(
        hass.config_entries.async_remove(shower_entry.entry_id)
    )
    await asyncio.sleep(0.2)
    _release(kohler)
    await remove
    await asyncio.gather(read, return_exceptions=True)
    await hass.async_block_till_done()
    assert hass.config_entries.async_entries(DOMAIN) == []
    assert len(built) == 1


# --------------------------------------------------------------------------- #
# Captures opened before a sign-in that fails are closed
# --------------------------------------------------------------------------- #
async def test_captures_are_closed_when_the_sign_in_fails(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: LifecycleKohler,
    built: list[Any],
    tmp_path,
) -> None:
    hass.config.config_dir = str(tmp_path)
    hass.config_entries.async_update_entry(
        shower_entry, options={CONF_REPORT_LOG_FILE: "report_20261016T000000Z"}
    )
    kohler.token_queue.extend([ClientError("down")] * 5)
    with (
        patch.object(coordinator_module, "ENABLE_RAW_MQTT_LOG", True),
        patch.object(
            coordinator_module, "REPORT_LOG_DIR_NAME", str(tmp_path / "reports")
        ),
    ):
        assert not await hass.config_entries.async_setup(shower_entry.entry_id)
        await hass.async_block_till_done()
    assert shower_entry.state is ConfigEntryState.SETUP_RETRY
    (coordinator,) = built
    assert coordinator.stream is None
    assert coordinator.raw_log._handle is None, "the raw capture was left open"
    report = coordinator.report_log._handle
    assert report is None or report.closed, "the report log was left open"
    # Something was captured before the sign-in, so this did exercise an open file.
    assert any(files for _, _, files in os.walk(tmp_path))
