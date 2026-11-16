"""The products in Home Assistant, against replies and messages they should never send.

A null block or a non-finite number reads as unknown and the rest of the device carries on;
a message that still fails to apply is logged, and the device is read instead.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.kohler_konnect.toilet import coordinator as toilet_coordinator

from . import test_ice_bath
from .test_evocycle import EvoKohler
from .test_evocycle import deliver as evo_deliver
from .test_ice_bath import IceBathKohler, wednesday  # noqa: F401
from .test_shower_entities import enable, setup
from .test_toilet import NUMI_ID, ToiletKohler
from .test_toilet import deliver as toilet_deliver


@pytest.fixture
def numi(aioclient_mock: AiohttpClientMocker) -> ToiletKohler:
    return ToiletKohler(aioclient_mock)


@pytest.fixture
def evo(aioclient_mock: AiohttpClientMocker) -> EvoKohler:
    return EvoKohler(aioclient_mock)


async def test_the_ambient_light_follows_its_message_after_a_null_read(
    hass: HomeAssistant, config_entry: MockConfigEntry, numi: ToiletKohler
) -> None:
    numi.toilets[NUMI_ID]["light"]["ambientLight"] = None
    light = enable(
        hass, config_entry, "light", f"{NUMI_ID}_ambient_light", "primary_ambient"
    )
    await setup(hass, config_entry)
    assert hass.states.get(light).state == "off"

    await toilet_deliver(
        hass, NUMI_ID, "NUMI_MULTIMEDIA_STS", multimediastatus="MultimediaIsAwake"
    )
    assert hass.states.get(light).state == "on"


async def test_a_message_that_fails_to_apply_reads_the_device_instead(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    numi: ToiletKohler,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup(hass, config_entry)
    reads = numi.reads

    def broken(*_: Any) -> None:
        raise RuntimeError("decoder bug")

    monkeypatch.setattr(toilet_coordinator, "apply_message", broken)
    numi.toilets[NUMI_ID]["seat"]["userStatus"] = "UserSeated"
    await toilet_deliver(hass, NUMI_ID, "NUMI_USER_STS", status="UserSeated")

    assert numi.reads > reads
    assert hass.states.get("binary_sensor.primary_occupied").state == "on"
    assert "Could not apply NUMI_USER_STS" in caplog.text


async def test_an_evocycle_with_non_finite_numbers_still_shows_the_rest(
    hass: HomeAssistant, config_entry: MockConfigEntry, evo: EvoKohler
) -> None:
    evo.evo_state["clean"]["deepClean"]["cleanOn"] = float("inf")
    # A count in milliseconds is no time in seconds.
    evo.evo_state["clean"]["deepClean"]["lastCleanDate"] = 1790000000000
    await setup(hass, config_entry)

    assert hass.states.get("sensor.spa_shower_activity").state == "idle"
    assert hass.states.get("sensor.spa_shower_last_deep_clean").state == "unknown"
    assert hass.states.get("sensor.spa_shower_last_rapid_clean").state.startswith(
        "2026-09-21"
    )


async def test_an_evocycle_message_with_a_non_finite_number_still_applies(
    hass: HomeAssistant, config_entry: MockConfigEntry, evo: EvoKohler
) -> None:
    await setup(hass, config_entry)
    await evo_deliver(hass, {"shower": {"showerOn": 1, "showerMode": 1}})
    assert hass.states.get("sensor.spa_shower_activity").state == "cycle"

    await evo_deliver(hass, {"shower": {"showerOn": float("inf"), "showerMode": 1}})
    # Anything but `showerOn` 1 is "off", as the app reads it.
    assert hass.states.get("sensor.spa_shower_activity").state == "idle"


@pytest.fixture
def journal(
    aioclient_mock: AiohttpClientMocker, monkeypatch: pytest.MonkeyPatch
) -> IceBathKohler:
    records = copy.deepcopy(test_ice_bath.RECORDS)
    # One bad record beside good ones.
    records[1]["record"]["durationMin"] = "Infinity"
    monkeypatch.setattr(test_ice_bath, "RECORDS", records)
    return IceBathKohler(aioclient_mock)


async def test_one_bad_ice_bath_record_does_not_take_the_journal_down(
    hass: HomeAssistant,
    wednesday: None,  # noqa: F811
    config_entry: MockConfigEntry,
    journal: IceBathKohler,
) -> None:
    await setup(hass, config_entry)

    # 27 October's session still shows; 26 October's, the bad one, is left out.
    assert (
        hass.states.get("sensor.sam_last_session").state == "2026-10-27T14:00:00+00:00"
    )
    assert hass.states.get("sensor.sam_sessions_this_week").state == "1"
