"""The Smart Fan in Home Assistant: readings, and controls built to the app's requests.

Against a fake of its cloud reads and commands (`docs/protocol/smart_fan.md`), since no fan
has been seen by this project.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.kohler_konnect.const import DOMAIN
from custom_components.kohler_konnect.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.kohler_konnect.konnect.smart_fan import SETTINGS_FIELDS

from .conftest import (
    TENANT_ID,
    FakeKohler,
    FakeMqttClient,
    account,
    registered_device,
)
from .test_shower_entities import enable, setup

FAN_ID = "sfc-0000ab12"
FAN = {"deviceId": FAN_ID, "sku": "SFC", "logicalName": "Primary Bath"}
SETTINGS = {
    **{field: "DISABLE" for field in SETTINGS_FIELDS},
    "fanSpeed": "MEDIUM",
    "fanTime": "30",
    "lightColor": "WARMWHITE",
    "lightBrightnessLevel": "50",
    "nightLightBrightness": "50",
    "humidity": "LOW",
    "fsDays": "31",
    "nlsStartTimeAmPm": "PM",
    "nlsEndTimeAmPm": "AM",
    "fanScheduleCreated": False,
}
FAVORITES = [
    {"id": "DEFAULT-FAN", "title": "Fan", "favType": "FAN", "isDefault": True},
    {
        "id": "DEFAULT-LIGHT",
        "title": "Main Light",
        "favType": "LIGHT",
        "isDefault": True,
    },
    {
        "id": "fav-1",
        "title": "Steamy",
        "favType": "FAN",
        "isDefault": False,
        "fan": {"speed": "HIGH", "timer": "ENABLE", "duration": "40"},
    },
    {
        "id": "fav-2",
        "title": "Morning",
        "favType": "LIGHT",
        "isDefault": False,
        "light": {"brightness": "70", "circadianEnabled": "DISABLE", "color": "COOL"},
    },
]


class FanKohler(FakeKohler):
    """`FakeKohler` with a Smart Fan's reads; its commands land in `commands`."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.devices = [{**FAN, "isFanWithLight": True}]
        self.connection_state = "Connected"
        self.fan_state: dict[str, Any] = {
            "fanStatus": "OFF",
            "fanSpeed": "MEDIUM",
            "lightStatus": "OFF",
            "lightColor": "None",
            "lightBrightnessLevel": "0",
            "runningFanFavorite": "DEFAULT-FAN",
            "temperature": "22",
            "humidity": "48",
        }
        self.settings: dict[str, Any] | None = dict(SETTINGS)
        self.reads: dict[str, int] = {}

    async def _api(self, method: str, url: Any, data: Any) -> Any:
        path = url.path
        queued = any(path.endswith(s) and q for s, q in self.api_queue.items())
        if method.lower() == "get" and not queued:
            reply = self._fan_read(path)
            if reply is not None:
                return self._respond(method, url, reply)
        return await super()._api(method, url, data)

    def _fan_read(self, path: str) -> tuple[int, Any] | None:
        for part in (
            "sfc-state",
            "sfc-configuration",
            "sfc-experience",
            "firmware/sfc",
        ):
            if f"/{part}/{FAN_ID}" in path:
                self.reads[part] = self.reads.get(part, 0) + 1
                break
        else:
            return None
        if part == "sfc-state":
            return 200, {
                "connectionState": self.connection_state,
                "deviceId": FAN_ID,
                "tenantId": TENANT_ID,
                "sku": "SFC",
                "state": copy.deepcopy(self.fan_state),
            }
        if part == "sfc-configuration":
            configuration: dict[str, Any] = {
                "about": {
                    "firmware": {"version": "3.1"},
                    "gateway": {"firmware": "1.4", "serialNo": "GW-SECRET"},
                    "sfc": {"firmware": "2.0", "serialNo": "SFC-SECRET"},
                }
            }
            if self.settings is not None:
                configuration["sfcSettings"] = copy.deepcopy(self.settings)
            return 200, {"deviceId": FAN_ID, "configuration": configuration}
        if part == "sfc-experience":
            return 200, {"deviceId": FAN_ID, "experiences": copy.deepcopy(FAVORITES)}
        return 200, {
            "firmwareUpdateAvailable": True,
            "firmware": "3.2",
            "currentFirmware": "3.1",
        }

    def sent(self, command: str) -> list[dict[str, Any]]:
        return [body for name, body in self.commands if name == command]


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> FanKohler:
    return FanKohler(aioclient_mock)


def message(**attributes: str) -> dict[str, Any]:
    return {
        "deviceid": FAN_ID,
        "sku": "SFC",
        "data": {"type": "Status", "code": "SFC_STS", "attributes": [attributes]},
    }


async def deliver(hass: HomeAssistant, payload: dict[str, Any]) -> None:
    FakeMqttClient.instances[-1].deliver(payload)
    await hass.async_block_till_done()


async def test_a_smart_fan_shows_its_readings_and_flags_its_controls(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    await setup(hass, config_entry)

    assert hass.states.get("sensor.primary_bath_fan").state == "off"
    assert hass.states.get("binary_sensor.primary_bath_light").state == "off"
    assert hass.states.get("binary_sensor.primary_bath_cloud_connection").state == "on"
    assert hass.states.get("sensor.primary_bath_controller_firmware").state == "2.0"
    firmware = hass.states.get("update.primary_bath_firmware_status")
    assert (firmware.state, firmware.attributes["latest_version"]) == ("on", "3.2")

    # Every control is on, and says nobody has seen it work on hardware.
    for entity_id in (
        "fan.primary_bath_fan",
        "light.primary_bath_light",
        "button.primary_bath_stop_all",
        "select.primary_bath_default_fan_speed",
        "switch.primary_bath_audible_feedback",
        "number.primary_bath_default_fan_run_time",
    ):
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.attributes["untested"] is True, entity_id
    # What the app never shows is off until an owner turns it on: the air readings,
    # whose units nobody knows, and the night light and its default.
    registry = er.async_get(hass)
    for entity_id in (
        "sensor.primary_bath_humidity",
        "sensor.primary_bath_co2",
        "light.primary_bath_night_light",
        "number.primary_bath_default_night_light_brightness",
    ):
        entry = registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION, entity_id
        assert hass.states.get(entity_id) is None, entity_id


async def test_an_air_reading_turned_on_shows_as_it_comes(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    humidity = enable(
        hass, config_entry, "sensor", f"{FAN_ID}_air_humidity", "primary_bath_humidity"
    )
    await setup(hass, config_entry)

    # Its unit nobody knows yet, so it is the bare number.
    assert hass.states.get(humidity).state == "48"


async def test_a_status_message_changes_the_fan_without_reading_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    await setup(hass, config_entry)
    reads = kohler.reads["sfc-state"]

    await deliver(hass, message(fanstatus="ON", fanspeed="HIGH", favoriteid="fav-1"))

    fan = hass.states.get("sensor.primary_bath_fan")
    assert (fan.state, fan.attributes["favorite"]) == ("high", "Steamy")
    assert kohler.reads["sfc-state"] == reads


async def test_the_fan_runs_as_its_defaults_or_as_a_favorite(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    enable(hass, config_entry, "fan", f"{FAN_ID}_fan", "primary_bath_fan")
    await setup(hass, config_entry)
    identity = {"deviceId": FAN_ID, "tenantId": TENANT_ID, "sku": "SFC"}
    fan = hass.states.get("fan.primary_bath_fan")
    assert fan.attributes["untested"] is True
    assert fan.attributes["preset_modes"] == ["Steamy"]

    await hass.services.async_call(
        "fan", "turn_on", {"entity_id": "fan.primary_bath_fan"}, blocking=True
    )
    assert kohler.sent("fancontrol") == [
        {**identity, "id": "DEFAULT-FAN", "status": "ON"}
    ]

    await hass.services.async_call(
        "fan",
        "set_preset_mode",
        {"entity_id": "fan.primary_bath_fan", "preset_mode": "Steamy"},
        blocking=True,
    )
    started = {"id": "fav-1", "speed": "HIGH", "timer": "ENABLE", "time": "40"}
    assert kohler.sent("fanfavoritecontrol") == [
        {**identity, **started, "status": "ON"}
    ]

    # Off goes to whatever runs: the favorite, not the built-in.
    await deliver(hass, message(fanstatus="ON", fanspeed="HIGH", favoriteid="fav-1"))
    await hass.services.async_call(
        "fan", "turn_off", {"entity_id": "fan.primary_bath_fan"}, blocking=True
    )
    assert kohler.sent("fanfavoritecontrol")[-1] == {
        **identity,
        **started,
        "status": "OFF",
    }


async def test_a_default_is_written_whole_and_refused_while_its_part_runs(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    speed = enable(
        hass,
        config_entry,
        "select",
        f"{FAN_ID}_default_fan_speed",
        "primary_bath_speed",
    )
    await setup(hass, config_entry)
    assert hass.states.get(speed).state == "medium"
    reads = kohler.reads["sfc-configuration"]

    await hass.services.async_call(
        "select", "select_option", {"entity_id": speed, "option": "low"}, blocking=True
    )
    # Read again first: the write replaces every default.
    assert kohler.reads["sfc-configuration"] >= reads + 1
    (body,) = kohler.sent("updatedefaultsettings")
    assert len(body) == 34
    assert (body["fanSpeed"], body["color"], body["nlsStartTimeAmPm"]) == (
        "LOW",
        "WARMWHITE",
        "PM",
    )
    # Kohler's copy still says MEDIUM; what was written shows until it catches up.
    assert hass.states.get(speed).state == "low"

    # The fan's own state, read fresh, says whether it runs, as the app's Save reads it.
    kohler.fan_state["fanStatus"] = "ON"
    await deliver(hass, message(fanstatus="ON", fanspeed="LOW", favoriteid="0"))
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": speed, "option": "high"},
            blocking=True,
        )
    assert refused.value.translation_key == "settings_while_running"

    kohler.fan_state["fanStatus"] = "OFF"
    await deliver(hass, message(fanstatus="OFF", favoriteid="0"))
    kohler.settings = None
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": speed, "option": "high"},
            blocking=True,
        )
    assert refused.value.translation_key == "settings_unreadable"
    assert len(kohler.sent("updatedefaultsettings")) == 1


async def test_a_fan_without_a_light_shows_none(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    kohler.devices = [{**FAN, "isFanWithLight": False}]
    await setup(hass, config_entry)

    entity_ids = {
        entry.entity_id
        for entry in er.async_entries_for_config_entry(
            er.async_get(hass), config_entry.entry_id
        )
    }
    assert "sensor.primary_bath_fan" in entity_ids
    assert not {e for e in entity_ids if "light" in e}


async def test_an_offline_fan_s_controls_are_unavailable(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    enable(hass, config_entry, "fan", f"{FAN_ID}_fan", "primary_bath_fan")
    kohler.connection_state = "Disconnected"
    await setup(hass, config_entry)

    assert hass.states.get("fan.primary_bath_fan").state == "unavailable"
    assert hass.states.get("binary_sensor.primary_bath_cloud_connection").state == "off"
    assert hass.states.get("sensor.primary_bath_fan").state == "off"


async def test_diagnostics_take_out_the_fan_s_identity(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    await setup(hass, config_entry)

    report = await async_get_config_entry_diagnostics(hass, config_entry)
    (fan,) = report["smart_fans"]
    assert fan["has_light"] is True
    assert fan["replies"]["state"]["state"]["fanSpeed"] == "MEDIUM"
    text = json.dumps(fan)
    for secret in (FAN_ID, TENANT_ID, "GW-SECRET", "SFC-SECRET", "Steamy"):
        assert secret not in text, secret


async def test_the_panel_lists_the_fan_and_its_untested_controls(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FanKohler,
    hass_ws_client: Any,
) -> None:
    assert await async_setup_component(hass, "http", {})
    await setup(hass, config_entry)
    ws = await hass_ws_client(hass)

    await ws.send_json_auto_id({"type": f"{DOMAIN}/subscribe_devices"})
    async with asyncio.timeout(5):
        assert (await ws.receive_json())["success"]
        event = (await ws.receive_json())["event"]
    (fan,) = event["devices"]
    assert (fan["kind"], fan["name"], fan["model"]) == (
        "smart_fan",
        "Primary Bath",
        "Smart Fan",
    )
    assert fan["roles"]["fan_state"] == "sensor.primary_bath_fan"
    assert {"fan", "light", "stop_all", "default_fan_speed"} <= set(fan["untested"])
    assert fan["untested_device"] is True
    assert FAN_ID not in json.dumps(event)


async def test_the_light_runs_as_its_defaults_or_as_a_favorite(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    light = enable(hass, config_entry, "light", f"{FAN_ID}_light", "primary_bath_light")
    await setup(hass, config_entry)
    identity = {"deviceId": FAN_ID, "tenantId": TENANT_ID, "sku": "SFC"}
    assert hass.states.get(light).attributes["effect_list"] == ["Morning"]

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": light}, blocking=True
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": light, "effect": "Morning"}, blocking=True
    )
    started = {
        "id": "fav-2",
        "circadianMode": "DISABLE",
        "color": "COOL",
        "brightnessLevel": "70",
    }
    assert kohler.sent("lightcontrol") == [
        {**identity, "id": "DEFAULT-LIGHT", "status": "ON"}
    ]
    assert kohler.sent("lightfavoritecontrol") == [
        {**identity, **started, "status": "ON"}
    ]

    await deliver(
        hass,
        message(
            lightstatus="ON",
            lightcolour="COOL",
            lightbrightnesslevel="70",
            favoriteid="fav-2",
        ),
    )
    state = hass.states.get("binary_sensor.primary_bath_light")
    assert state.attributes == {
        **state.attributes,
        "brightness": 70,
        "color": "cool",
        "favorite": "Morning",
    }
    assert hass.states.get(light).attributes["effect"] == "Morning"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": light}, blocking=True
    )
    assert kohler.sent("lightfavoritecontrol")[-1] == {
        **identity,
        **started,
        "status": "OFF",
    }


async def test_stop_all_and_the_other_defaults(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    stop = enable(hass, config_entry, "button", f"{FAN_ID}_stop_all", "primary_stop")
    beep = enable(
        hass, config_entry, "switch", f"{FAN_ID}_audible_feedback", "primary_beep"
    )
    minutes = enable(
        hass, config_entry, "number", f"{FAN_ID}_default_fan_run_time", "primary_time"
    )
    schedule = enable(
        hass, config_entry, "switch", f"{FAN_ID}_fan_schedule", "primary_schedule"
    )
    timer = enable(
        hass, config_entry, "switch", f"{FAN_ID}_default_fan_timer", "primary_timer"
    )
    await setup(hass, config_entry)

    await hass.services.async_call(
        "button", "press", {"entity_id": stop}, blocking=True
    )
    assert kohler.sent("stopallcontrol") == [
        {"deviceId": FAN_ID, "tenantId": TENANT_ID, "sku": "SFC"}
    ]

    assert hass.states.get(beep).state == "off"
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": beep}, blocking=True
    )
    # The run time is the timer's: the app shows it only while the timer is on.
    assert hass.states.get(minutes).state == "unavailable"
    kohler.settings["fanTime"] = "50"
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": timer}, blocking=True
    )
    # Switching the timer puts the run time back to 30, as the app does.
    assert hass.states.get(minutes).state == "30"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": minutes, "value": 40}, blocking=True
    )
    written = kohler.sent("updatedefaultsettings")
    assert written[0]["buzzerSetting"] == "ENABLE"
    assert (written[1]["fanTimer"], written[1]["fanTime"]) == ("ENABLE", "30")
    assert (written[2]["fanTimer"], written[2]["fanTime"]) == ("ENABLE", "40")
    # The schedule is set up in the app; until then there is nothing to turn on.
    assert hass.states.get(schedule).state == "unavailable"


async def test_a_failed_read_of_the_defaults_keeps_the_last(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    speed = enable(
        hass, config_entry, "select", f"{FAN_ID}_default_fan_speed", "primary_speed"
    )
    await setup(hass, config_entry)
    fan = account(hass, config_entry).products["smart_fan"][0]

    kohler.fail_api(f"/sfc-configuration/{FAN_ID}", (500, {"message": "down"}))
    await fan.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(speed).state == "medium"
    assert fan.last_update_success


async def test_a_fan_only_account_can_still_record_a_report_log(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FanKohler,
    tmp_path: Path,
) -> None:
    """The account's one capture, switched from the fan's page: no shower needed."""
    with patch(
        "custom_components.kohler_konnect.coordinator.REPORT_LOG_DIR_NAME",
        str(tmp_path),
    ):
        await setup(hass, config_entry)
    log = "switch.primary_bath_report_log"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": log}, blocking=True
    )
    switch = hass.states.get(log)
    assert switch.state == "on"
    assert switch.attributes["file"]
    assert (tmp_path / switch.attributes["file"]).exists()

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": log}, blocking=True
    )
    assert hass.states.get(log).state == "off"


async def test_the_night_light_and_the_clock_the_app_leaves_alone(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    enable(
        hass, config_entry, "light", f"{FAN_ID}_night_light", "primary_bath_night_light"
    )
    enable(
        hass,
        config_entry,
        "number",
        f"{FAN_ID}_default_night_light_brightness",
        "primary_bath_default_night_light_brightness",
    )
    await setup(hass, config_entry)

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.primary_bath_night_light"},
        blocking=True,
    )
    (night,) = kohler.sent("nightlight")
    assert (night["id"], night["status"]) == ("DEFAULT-NIGHTLIGHT", "ON")

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.primary_bath_sync_clock"},
        blocking=True,
    )
    (clock,) = kohler.sent("setrtccontrol")
    assert clock["timeZone"] == hass.config.time_zone

    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": "number.primary_bath_default_night_light_brightness",
            "value": 30,
        },
        blocking=True,
    )
    (written,) = kohler.sent("updatedefaultsettings")
    # Written under the name the command takes, not the one the read gives (§3.4).
    assert written["nightLightBrightnessLevel"] == "30"
    assert "nightLightBrightness" not in written


async def test_defaults_wait_for_the_night_light_go_one_at_a_time_and_keep_the_app_s_steps(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    feedback = enable(
        hass,
        config_entry,
        "switch",
        f"{FAN_ID}_audible_feedback",
        "primary_bath_audible_feedback",
    )
    run_time = enable(
        hass,
        config_entry,
        "number",
        f"{FAN_ID}_default_fan_run_time",
        "primary_bath_default_fan_run_time",
    )
    speed = enable(
        hass,
        config_entry,
        "select",
        f"{FAN_ID}_default_fan_speed",
        "primary_bath_speed",
    )
    glow = enable(
        hass,
        config_entry,
        "number",
        f"{FAN_ID}_default_night_light_brightness",
        "primary_bath_default_night_light_brightness",
    )
    # The run time shows only with the timer on.
    kohler.settings["fanTimer"] = "ENABLE"
    await setup(hass, config_entry)

    # The night light holds back what waits for anything running, and its own default.
    kohler.fan_state["nightLightStatus"] = "ON"
    for domain, service, data in (
        ("switch", "turn_on", {"entity_id": feedback}),
        ("number", "set_value", {"entity_id": glow, "value": 30}),
    ):
        with pytest.raises(HomeAssistantError) as refused:
            await hass.services.async_call(domain, service, data, blocking=True)
        assert refused.value.translation_key == "settings_while_running"
    # The main light running doesn't hold back the night light's default.
    kohler.fan_state["nightLightStatus"] = "OFF"
    kohler.fan_state["lightStatus"] = "ON"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": glow, "value": 30}, blocking=True
    )
    kohler.fan_state["lightStatus"] = "OFF"

    # Two changes at once, as a scene makes them: one after the other, the second
    # keeping the first, though Kohler's copy shows neither yet.
    await asyncio.gather(
        hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": speed, "option": "low"},
            blocking=True,
        ),
        # Between the app's steps of ten: the nearest.
        hass.services.async_call(
            "number", "set_value", {"entity_id": run_time, "value": 44}, blocking=True
        ),
    )
    bodies = kohler.sent("updatedefaultsettings")
    assert [(b["fanSpeed"], b["fanTime"]) for b in bodies[-2:]] in (
        [("LOW", "30"), ("LOW", "40")],
        [("MEDIUM", "40"), ("LOW", "40")],
    )
    # Written under the command's own name for it (§3.4).
    assert bodies[0]["nightLightBrightnessLevel"] == "30"
    # What is already so is not written again.
    await hass.services.async_call(
        "select", "select_option", {"entity_id": speed, "option": "low"}, blocking=True
    )
    assert len(kohler.sent("updatedefaultsettings")) == 3


async def test_a_fan_the_account_doesn_t_say_has_a_light_shows_none(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    # The app shows a light only when the account says so, whatever the fan reports.
    kohler.devices = [dict(FAN)]
    kohler.fan_state["isFanWithLight"] = True
    await setup(hass, config_entry)

    assert hass.states.get("binary_sensor.primary_bath_light") is None
    assert hass.states.get("light.primary_bath_light") is None


async def test_settings_the_app_shows_only_with_another_on(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    kohler.settings["circadianMode"] = "ENABLE"
    await setup(hass, config_entry)

    # Humidity sensitivity is for humidity sensing, off here; the default colour waits
    # while circadian lighting sets the colour.
    assert hass.states.get("select.primary_bath_humidity_sensitivity").state == (
        "unavailable"
    )
    assert hass.states.get("select.primary_bath_default_light_color").state == (
        "unavailable"
    )
    assert hass.states.get("select.primary_bath_default_fan_speed").state == "medium"


async def test_a_schedule_is_saved_as_the_app_s_editor_saves_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    kohler.settings["fsFanSpeed"] = "LOW"
    await setup(hass, config_entry)
    device = registered_device(hass, FAN_ID)

    async def schedule(**data: Any) -> None:
        await hass.services.async_call(
            DOMAIN,
            "set_fan_schedule",
            {"device_id": device.id, **data},
            blocking=True,
        )

    await schedule(
        start="19:05", end="06:30", repeat="custom", days=["mon", "wed", "fri"]
    )
    (body,) = kohler.sent("updatedefaultsettings")
    # Switched on as it is saved; 12-hour parts; Monday, Wednesday and Friday are bits
    # 0, 2 and 4; the speed it had; the end hour under the request's own name.
    assert {key: body[key] for key in body if key.startswith("fs")} == {
        "fsSchedule": "ENABLE",
        "fsSetting": "CUSTOM",
        "fsDays": "21",
        "fsStartTimeHr": "7",
        "fsStartTimeMin": "05",
        "fsStartTimeAmPm": "PM",
        "fsEndTimeHour": "6",
        "fsEndTimeMin": "30",
        "fsEndTimeAmPm": "AM",
        "fsFanSpeed": "LOW",
    }
    assert body["fanSpeed"] == "MEDIUM"
    schedule_state = hass.states.get("switch.primary_bath_fan_schedule")
    assert {
        key: schedule_state.attributes[key]
        for key in ("start", "end", "repeat", "days", "speed")
    } == {
        "start": "19:05",
        "end": "06:30",
        "repeat": "custom",
        "days": ["mon", "wed", "fri"],
        "speed": "low",
    }

    # A mask under ten goes out padded with a space, as the app writes it.
    await schedule(
        start="07:00", end="08:00", repeat="custom", days=["mon", "tue"], speed="high"
    )
    body = kohler.sent("updatedefaultsettings")[-1]
    assert (body["fsDays"], body["fsFanSpeed"]) == (" 3", "HIGH")
    await schedule(start="00:15", end="12:45")
    body = kohler.sent("updatedefaultsettings")[-1]
    assert (body["fsSetting"], body["fsDays"]) == ("EVERYDAY", "0")
    assert (body["fsStartTimeHr"], body["fsStartTimeAmPm"]) == ("12", "AM")
    assert (body["fsEndTimeHour"], body["fsEndTimeAmPm"]) == ("12", "PM")

    for data, key in (
        ({"start": "07:00", "end": "07:00"}, "fan_schedule_same_times"),
        (
            {"start": "07:00", "end": "08:00", "repeat": "custom"},
            "fan_schedule_no_days",
        ),
    ):
        with pytest.raises(ServiceValidationError) as refused:
            await schedule(**data)
        assert refused.value.translation_key == key
    # Not while the fan runs, as in the app.
    kohler.fan_state["fanStatus"] = "ON"
    with pytest.raises(HomeAssistantError) as refused:
        await schedule(start="09:00", end="10:00")
    assert refused.value.translation_key == "settings_while_running"
    assert len(kohler.sent("updatedefaultsettings")) == 3


async def test_the_clock_slows_once_the_stream_carries_the_fan(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FanKohler
) -> None:
    await setup(hass, config_entry)
    fan = account(hass, config_entry).products["smart_fan"][0]
    assert fan.update_interval == timedelta(minutes=5)

    await deliver(hass, message(fanstatus="OFF", favoriteid="0"))
    assert fan.update_interval == timedelta(minutes=15)
    FakeMqttClient.instances[-1].drop()
    await hass.async_block_till_done()
    assert fan.update_interval == timedelta(minutes=5)
