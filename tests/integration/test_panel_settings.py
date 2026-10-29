"""An Anthem valve's touch-panel settings and bath fill, in Home Assistant.

Against the shower fake (`test_coordinator.ShowerKohler`); the record shapes are the
app's (`docs/protocol/gcs_valve.md` §6, §8), since none has been captured.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.kohler_konnect.const import DOMAIN
from custom_components.kohler_konnect.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import TENANT_ID, USERNAME, panel_record, unload
from .test_coordinator import (
    LEFT,
    SHOWER_DATA,
    ShowerKohler,
    push,
    solo_message,
    start,
)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> ShowerKohler:
    return ShowerKohler(aioclient_mock)


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


def panels(kohler: ShowerKohler) -> list[dict[str, Any]]:
    return kohler.valve_settings[LEFT]["setting"]["uiConfig"]


async def test_the_touch_panels_read_and_are_written_as_the_control_panel_writes(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    panels(kohler).append({**panel_record(), "ui": "UI2", "backLight": "9"})
    await start(hass, shower_entry)

    assert hass.states.get("select.anthem_valve_panel_language").state == "en_us"
    assert hass.states.get("select.anthem_valve_panel_units").state == "us"
    assert hass.states.get("select.anthem_valve_wake_screen").state == "outlets"
    haptics = hass.states.get("switch.anthem_valve_haptic_feedback")
    assert (haptics.state, haptics.attributes["untested"]) == ("on", True)
    assert hass.states.get("switch.anthem_valve_accessible_display").state == "off"
    assert hass.states.get("number.anthem_valve_display_brightness").state == "5"
    # Set at setup, and never changed by the app: a reading.
    assert hass.states.get("binary_sensor.anthem_valve_water_saving_mode").state == "on"

    reads = kohler.reads(f"/gcsadvancestate/{LEFT}")
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.anthem_valve_display_brightness", "value": 7},
        blocking=True,
    )
    # A fresh read first, then every panel, whole, the shared settings the first's.
    assert kohler.reads(f"/gcsadvancestate/{LEFT}") == reads + 1
    first, second = kohler.posted("/writeuiconfig")
    assert {key: first[key] for key in ("deviceId", "sku", "tenantId")} == {
        "deviceId": LEFT,
        "sku": "GCS",
        "tenantId": TENANT_ID,
    }
    model = first["gcsUIConfigModel"]
    assert (model["ui"], model["backLight"], model["waterSavingMode"]) == (
        "UI1",
        "7",
        "1",
    )
    assert (model["hapticFeedback"], model["defaultMemory"], model["serialNo"]) == (
        "1",
        "1",
        "",
    )
    assert (
        second["gcsUIConfigModel"]["ui"],
        second["gcsUIConfigModel"]["backLight"],
    ) == (
        "UI2",
        "7",
    )
    assert hass.states.get("number.anthem_valve_display_brightness").state == "7"

    # A panel's pushed record is merged by its name.
    await push(
        hass,
        {
            "sku": "GCS",
            "deviceid": LEFT,
            "data": {
                "code": "READ_GCS_UI_CFG",
                "attributes": [{"UI": "UI1", "backLight": "3"}],
            },
        },
    )
    assert hass.states.get("number.anthem_valve_display_brightness").state == "3"


async def test_the_touch_panels_are_left_alone_when_they_can_t_be_written_safely(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await start(hass, shower_entry)
    registry = er.async_get(hass)
    # The app's current screens hide Flow Control.
    flow = registry.async_get("switch.anthem_valve_flow_control")
    assert flow is not None and flow.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    # A switch read in a form the app doesn't expect would be written as off.
    panels(kohler)[0]["hapticFeedback"] = "1"
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.anthem_valve_accessible_display"},
            blocking=True,
        )
    assert refused.value.translation_key == "panel_settings_unreadable"

    # Not while water runs, as in the app.
    panels(kohler)[0]["hapticFeedback"] = "Enabled"
    await push(hass, solo_message(LEFT, "0184C801"))
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.anthem_valve_wake_screen", "option": "home"},
            blocking=True,
        )
    assert refused.value.translation_key == "panel_settings_while_running"
    assert kohler.posted("/writeuiconfig") == []


async def test_a_bath_fill_preset_is_known_and_its_fill_shown_on_a_tub_filler(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    outlets = kohler.valve_settings[LEFT]["setting"]["valveSettings"][0][
        "outletConfigurations"
    ]
    outlets[2]["outLetType"] = "21"
    kohler.valve_presets[LEFT]["gcsPresetExperienceDetails"].append(
        {
            "presetId": "3",
            "title": "Bath",
            "isExperience": "False",
            "time": "1800",
            "volume": "37.5",
            "valveDetails": [{"valveIndex": "Valve1", "hexString": "1190A0"}],
        }
    )
    await start(hass, shower_entry)

    favorite = hass.states.get("select.anthem_valve_favorite")
    assert favorite.attributes["bath_fill_litres"] == {"Bath": 37.5}
    filling = "binary_sensor.anthem_valve_bath_filling"
    assert hass.states.get(filling).state == "off"
    await push(hass, solo_message(LEFT, "0190A004", presetOrExperienceId="3"))
    state = hass.states.get(filling)
    assert (state.state, state.attributes["fill_litres"]) == ("on", 37.5)


async def test_no_bath_fill_sensor_without_a_tub_filler(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await start(hass, shower_entry)
    assert hass.states.get("binary_sensor.anthem_valve_bath_filling") is None


async def test_diagnostics_show_the_panels_records_without_their_identity(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await start(hass, shower_entry)

    report = await async_get_config_entry_diagnostics(hass, shower_entry)
    (panel,) = report["valve"]["touch_panels"]
    assert (panel["ui"], panel["hapticFeedback"], panel["defaultMemory"]) == (
        "UI1",
        "Enabled",
        "1",
    )
    assert "UI-SECRET" not in str(report)
