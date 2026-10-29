"""Numi 2.0 and Purewash E930 in Home Assistant: readings, controls and the app's rules.

Against a fake of their cloud reads and commands (`docs/protocol/numi_purewash.md`), since
no seat has been seen by this project.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.kohler_konnect.const import DOMAIN
from custom_components.kohler_konnect.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.kohler_konnect.toilet import coordinator as toilet_coordinator

from .conftest import TENANT_ID, FakeKohler, FakeMqttClient, account
from .test_shower_entities import enable, setup

NUMI_ID = "NUM-00c0ffee"
PUREWASH_ID = "BLD-00facade"
STATE = {
    "frontWash": {"state": "Off"},
    "rearWash": {"state": "Off"},
    "boostWash": {"state": "Off"},
    "kidWash": {"state": "Off"},
    "dryer": {"state": "Off"},
    "seat": {
        "userStatus": "UserLeave",
        "temperature": "HeatLowMid",
        "powerSave": "Off",
        "autoOpen": {"state": "On", "sensorDistance": "RangeMid"},
    },
    "light": {"nightLight": "Off", "ambientLight": {"state": "Off"}},
    "flush": {"auto": "On"},
    "clean": {"sanitizer": "SanitizeOff", "deo": "AirCleanOff", "preRinse": "Off"},
    "cleanStatus": {"uvClean": "SanitizeOff", "manualClean": "Off", "timezone": "UTC"},
}


WASH = {"temperature": "HeatMid", "flow": "FlowMid", "position": "PositionMid"}
SETTINGS = {
    "frontWash": {**WASH, "massage": "ModeNormal"},
    "rearWash": {**WASH, "massage": "ModeWave"},
    "boostWash": {**WASH, "massage": "ModeNormal"},
    "kidWash": {**WASH, "massage": "ModeNormal"},
    "dryer": {"temperature": "HeatMid", "flow": "FlowMid"},
    "seat": {"temperature": "HeatLow"},
}


def usage(interval: str) -> dict[str, Any]:
    """A usage reply: today's bucket for `Day`, this month's for `Month`."""
    today = dt_util.now().date()
    if interval == "Day":
        bucket = {
            "intervalKey": today.isoformat(),
            "waterUsage": 12.0,
            "usageDuration": 300.0,
            "fullFlushCount": 3.0,
            "ecoFlushCount": 2.0,
            "sprayFrontCount": 1.0,
            "sprayRearCount": 2.0,
            "dryerCount": 1.0,
            "powerConsumption": 15.5,
        }
    else:
        bucket = {"intervalKey": today.strftime("%Y-%m"), "waterUsage": 120.0}
    return {"numiUsageDataDetailsList": [bucket]}


class ToiletKohler(FakeKohler):
    """`FakeKohler` with a Numi's and a Purewash's state reads; commands land in `sent`."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.devices = [
            {"deviceId": NUMI_ID, "sku": "NUM", "logicalName": "Primary"},
            {"deviceId": PUREWASH_ID, "sku": "BLD", "logicalName": "Guest"},
        ]
        self.connection_state = "Connected"
        self.toilets: dict[str, dict[str, Any] | None] = {
            NUMI_ID: copy.deepcopy(STATE),
            PUREWASH_ID: copy.deepcopy(STATE),
        }
        self.toilets[PUREWASH_ID]["seat"]["autoOpen"] = "Off"
        self.reads = 0
        #: Every command: the path segment, verb and body.
        self.sent: list[tuple[str, str, dict[str, Any]]] = []
        #: Every usage read's query.
        self.usage_queries: list[dict[str, str]] = []
        #: Every stored default: the path segment, the part and the body.
        self.stored: list[tuple[str, str, dict[str, Any]]] = []

    async def _api(self, method: str, url: Any, data: Any) -> Any:
        path = url.path
        for segment, device_id in (("numi", NUMI_ID), ("blade", PUREWASH_ID)):
            if method.lower() == "get" and path.endswith(
                f"/{segment}-state/{device_id}"
            ):
                self.reads += 1
                state = self.toilets[device_id]
                body = {
                    "connectionState": self.connection_state,
                    "deviceId": device_id,
                    "tenantId": TENANT_ID,
                    "sku": "NUM" if segment == "numi" else "BLD",
                    **({} if state is None else {"state": copy.deepcopy(state)}),
                }
                return self._respond(method, url, (200, body))
            if method.lower() == "get" and path.endswith(
                f"/{segment}-setting/{device_id}"
            ):
                body = {"deviceId": device_id, "setting": copy.deepcopy(SETTINGS)}
                return self._respond(method, url, (200, body))
            if method.lower() == "patch" and f"/{segment}-setting/{device_id}/" in path:
                self.stored.append((segment, path.rsplit("/", 1)[-1], data))
                return self._respond(method, url, (200, {}))
            if method.lower() == "get" and path.endswith(
                f"/{segment}-usage/{device_id}"
            ):
                self.usage_queries.append(dict(url.query))
                return self._respond(method, url, (200, usage(url.query["Interval"])))
            if method.lower() == "post" and f"/commands/{segment}/" in path:
                self.sent.append((segment, path.rsplit("/", 1)[-1], data))
                return self._respond(method, url, (200, {"correlationId": "c"}))
        return await super()._api(method, url, data)

    def verbs(self, segment: str) -> list[tuple[str, dict[str, Any]]]:
        return [(verb, body) for seg, verb, body in self.sent if seg == segment]


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> ToiletKohler:
    return ToiletKohler(aioclient_mock)


async def deliver(
    hass: HomeAssistant, device_id: str, code: str, **attributes: str
) -> None:
    FakeMqttClient.instances[-1].deliver(
        {
            "deviceid": device_id,
            "sku": device_id[:3],
            "data": {"code": code, "attributes": [attributes]},
        }
    )
    await hass.async_block_till_done()


async def test_each_model_shows_its_readings_and_flags_its_controls(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ToiletKohler
) -> None:
    await setup(hass, config_entry)

    assert hass.states.get("binary_sensor.primary_occupied").state == "off"
    assert hass.states.get("sensor.primary_activity").state == "idle"
    assert hass.states.get("sensor.primary_seat_heat").state == "low"
    assert hass.states.get("binary_sensor.primary_cloud_connection").state == "on"
    assert hass.states.get("sensor.guest_activity").attributes["options"] == [
        "idle",
        "front_wash",
        "rear_wash",
        "boost_wash",
        "kid_wash",
        "drying",
    ]

    registry = er.async_get(hass)
    ids = {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)
    }
    for entity_id in (
        "button.primary_full_flush",
        "button.primary_start_front_wash",
        "button.primary_cover",
        "light.primary_ambient_light",
        "select.primary_auto_cover",
        "switch.primary_night_light",
        "button.guest_start_kid_wash",
        "select.guest_night_light",
        "switch.guest_long_sitting_reminder",
    ):
        assert entity_id in ids, entity_id
        assert hass.states.get(entity_id).attributes["untested"] is True, entity_id
    # The Purewash never flushes, and has no ambient light.
    assert "button.guest_full_flush" not in ids
    assert "light.guest_ambient_light" not in ids


async def test_messages_say_who_is_seated_what_runs_and_what_failed(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ToiletKohler
) -> None:
    await setup(hass, config_entry)
    reads = kohler.reads

    await deliver(hass, NUMI_ID, "NUMI_USER_STS", status="UserSeated")
    await deliver(hass, NUMI_ID, "NUMI_SPRAY_STS", status="SprayingRear")
    await deliver(hass, NUMI_ID, "NUMI_ERROR_STS", status="Water supply error")

    assert hass.states.get("binary_sensor.primary_occupied").state == "on"
    assert hass.states.get("sensor.primary_activity").state == "rear_wash"
    assert hass.states.get("sensor.primary_last_error").state == "Water supply error"
    assert kohler.reads == reads

    await deliver(hass, NUMI_ID, "NUMI_ERROR_STS", status="NoError")
    assert hass.states.get("sensor.primary_last_error").state == "unknown"


async def test_a_spray_starts_only_for_someone_seated(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ToiletKohler
) -> None:
    start = enable(
        hass, config_entry, "button", f"{NUMI_ID}_start_front_wash", "primary_front"
    )
    stop = enable(hass, config_entry, "button", f"{NUMI_ID}_stop", "primary_stop")
    await setup(hass, config_entry)
    assert hass.states.get(start).attributes["untested"] is True

    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "button", "press", {"entity_id": start}, blocking=True
        )
    assert refused.value.translation_key == "toilet_not_seated"
    assert kohler.sent == []

    await deliver(hass, NUMI_ID, "NUMI_USER_STS", status="UserSeated")
    await hass.services.async_call(
        "button", "press", {"entity_id": start}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": stop}, blocking=True
    )
    identity = {"deviceId": NUMI_ID, "tenantId": TENANT_ID, "sku": "NUM"}
    defaults = {
        "temperature": "HeatMid",
        "flow": "FlowMid",
        "position": "PositionMid",
        "mode": "ModeNormal",
    }
    assert kohler.verbs("numi") == [
        ("spray-dryer", {**identity, "operation": "FrontSprayStart"}),
        # The wash reports none of its stored defaults yet, so the app's dial sets them
        # once Start succeeds.
        ("front-wash", {**identity, **defaults}),
        ("spray-dryer", {**identity, "operation": "SprayAndDryerStop"}),
    ]

    # Already at its defaults, it is only started.
    kohler.sent.clear()
    await deliver(
        hass,
        NUMI_ID,
        "NUMI_SET_FRONT_PARAMETERS_STS",
        temperature="HeatMid",
        flow="FlowMid",
        position="PositionMid",
        mode="ModeNormal",
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": start}, blocking=True
    )
    assert kohler.verbs("numi") == [
        ("spray-dryer", {**identity, "operation": "FrontSprayStart"}),
    ]

    # Never while the UV clean runs.
    await deliver(hass, NUMI_ID, "NUMI_SANITIZE_STS", status="SanitizeOn")
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "button", "press", {"entity_id": start}, blocking=True
        )
    assert refused.value.translation_key == "toilet_cleaning"


async def test_the_cover_waits_until_nobody_is_seated(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ToiletKohler
) -> None:
    cover = enable(hass, config_entry, "button", f"{PUREWASH_ID}_cover", "guest_cover")
    await setup(hass, config_entry)

    await hass.services.async_call(
        "button", "press", {"entity_id": cover}, blocking=True
    )
    assert kohler.verbs("blade") == [
        ("lid", {"deviceId": PUREWASH_ID, "tenantId": TENANT_ID, "sku": "BLD"})
    ]

    await deliver(hass, PUREWASH_ID, "NUMI_USER_STS", status="UserSeated")
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "button", "press", {"entity_id": cover}, blocking=True
        )
    assert refused.value.translation_key == "toilet_seated"


async def test_an_auto_setting_is_written_with_all_the_others(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ToiletKohler
) -> None:
    night_light = enable(
        hass, config_entry, "switch", f"{NUMI_ID}_night_light", "primary_night_light"
    )
    await setup(hass, config_entry)
    reads = kohler.reads

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": night_light}, blocking=True
    )
    # Read again first: the write replaces every auto setting.
    assert kohler.reads >= reads + 1
    ((verb, body),) = kohler.verbs("numi")
    assert verb == "write-configuration"
    assert body == {
        "deviceId": NUMI_ID,
        "tenantId": TENANT_ID,
        "sku": "NUM",
        "nightLightEnable": "true",
        "autoFlushEnable": "true",
        "autoLidEnable": "true",
        "microWaveRange": "RangeMid",
        "autoUVEnable": "false",
        "autoDeodorizeEnable": "false",
        "waterBowlPremistEnable": "false",
    }

    kohler.toilets[NUMI_ID] = None
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": night_light}, blocking=True
        )
    assert refused.value.translation_key == "settings_unreadable"
    assert len(kohler.sent) == 1


async def test_seat_heat_and_the_ambient_light(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ToiletKohler
) -> None:
    heat = enable(
        hass, config_entry, "select", f"{NUMI_ID}_seat_heat_level", "primary_heat"
    )
    light = enable(
        hass, config_entry, "light", f"{NUMI_ID}_ambient_light", "primary_ambient"
    )
    await setup(hass, config_entry)
    identity = {"deviceId": NUMI_ID, "tenantId": TENANT_ID, "sku": "NUM"}

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": heat, "option": "highest"},
        blocking=True,
    )
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": light, "effect": "blue", "brightness": 128},
        blocking=True,
    )
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": light}, blocking=True
    )
    assert kohler.verbs("numi") == [
        (
            "heated-seat",
            {
                **identity,
                "flow": "",
                "temperature": "HeatHigh",
                "position": "",
                "mode": "",
            },
        ),
        (
            "lighting",
            {
                **identity,
                "color": "Color4",
                "luminance": "5",
                "r": "0",
                "g": "0",
                "b": "0",
                "msp": "NormalSpeed",
            },
        ),
        ("multimedia-operation", {**identity, "operation": "MultimediaWakeUp"}),
        ("multimedia-operation", {**identity, "operation": "MultimediaSleep"}),
    ]


async def test_controls_wait_for_the_app_s_own_connected(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ToiletKohler
) -> None:
    stop = enable(hass, config_entry, "button", f"{NUMI_ID}_stop", "primary_stop")
    kohler.connection_state = "Connecting"
    await setup(hass, config_entry)

    assert hass.states.get(stop).state == "unavailable"
    # Not offline, though: only "Disconnected" is.
    assert hass.states.get("binary_sensor.primary_cloud_connection").state == "on"


async def test_diagnostics_take_out_each_seat_s_identity(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ToiletKohler
) -> None:
    await setup(hass, config_entry)

    report = await async_get_config_entry_diagnostics(hass, config_entry)
    assert report["devices"]["toilet_count"] == 2
    assert [t["model"] for t in report["toilets"]] == ["Numi 2.0", "Purewash E930"]
    text = json.dumps(report["toilets"])
    for secret in (NUMI_ID, PUREWASH_ID, TENANT_ID):
        assert secret not in text, secret


async def test_flush_cleaning_the_wand_power_save_and_the_selects(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ToiletKohler
) -> None:
    flush = enable(hass, config_entry, "button", f"{NUMI_ID}_eco_flush", "primary_eco")
    clean = enable(
        hass, config_entry, "button", f"{NUMI_ID}_start_clean", "primary_clean"
    )
    wand = enable(
        hass, config_entry, "button", f"{NUMI_ID}_retract_wand", "primary_wand"
    )
    save = enable(hass, config_entry, "switch", f"{NUMI_ID}_power_save", "primary_save")
    cover = enable(
        hass, config_entry, "select", f"{NUMI_ID}_auto_cover", "primary_auto"
    )
    night = enable(
        hass, config_entry, "select", f"{PUREWASH_ID}_night_light", "guest_night"
    )
    await setup(hass, config_entry)
    assert (hass.states.get(cover).state, hass.states.get(night).state) == (
        "medium",
        "off",
    )

    for domain, service, data in (
        ("button", "press", {"entity_id": flush}),
        ("button", "press", {"entity_id": clean}),
        ("button", "press", {"entity_id": wand}),
        ("switch", "turn_on", {"entity_id": save}),
        ("select", "select_option", {"entity_id": cover, "option": "off"}),
        ("select", "select_option", {"entity_id": night, "option": "low"}),
    ):
        await hass.services.async_call(domain, service, data, blocking=True)

    sent = [
        (
            verb,
            {k: v for k, v in body.items() if k not in ("deviceId", "tenantId", "sku")},
        )
        for verb, body in kohler.verbs("numi")
    ]
    assert sent[:4] == [
        ("flush", {"flushType": "EcoFlushStart"}),
        ("sanitize", {"operation": "SanitizeStart"}),
        ("clean-wand", {"operation": "CleanWandStop"}),
        ("auto-power-save", {"state": "Enable"}),
    ]
    assert sent[4][0] == "write-configuration"
    assert (sent[4][1]["autoLidEnable"], sent[4][1]["microWaveRange"]) == (
        "false",
        "RangeMid",
    )
    ((verb, body),) = kohler.verbs("blade")
    assert (verb, body["nightLightEnable"], body["nightLightBrightness"]) == (
        "write-configuration",
        "true",
        "low",
    )


async def test_a_toilet_counts_its_day_and_charts_its_water(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ToiletKohler,
    hass_ws_client: Any,
) -> None:
    kohler.water_units = "Metric"
    assert await async_setup_component(hass, "http", {})
    await setup(hass, config_entry)

    assert hass.states.get("sensor.primary_water_used_today").state == "12.0"
    assert hass.states.get("sensor.primary_duration_used_today").state == "5.0"
    flushes = hass.states.get("sensor.primary_flushes_today")
    assert (flushes.state, flushes.attributes["full"], flushes.attributes["eco"]) == (
        "5",
        3,
        2,
    )
    assert hass.states.get("sensor.primary_washes_today").state == "3"
    assert hass.states.get("sensor.primary_dryer_runs_today").state == "1"
    assert hass.states.get("sensor.primary_power_used_today").state == "15.5"
    # Each model its own read, dated year first as the app's toilet screen asks, over
    # a month of days that reaches back at least a week.
    days = [q for q in kohler.usage_queries if q["Interval"] == "Day"]
    assert len(days) == 2
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\d", q["FromDate"]) for q in days)
    local = dt_util.now().date()
    start = min(local.replace(day=1), local - timedelta(days=6)).isoformat()
    assert {q["FromDate"] for q in days} == {start}
    months = [q for q in kohler.usage_queries if q["Interval"] == "Month"]
    assert {q["FromDate"] for q in months} == {
        local.replace(month=1, day=1).isoformat()
    }

    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": f"{DOMAIN}/subscribe_devices"})
    async with asyncio.timeout(5):
        assert (await ws.receive_json())["success"]
        event = (await ws.receive_json())["event"]
    numi = next(d for d in event["devices"] if d["name"] == "Primary")
    assert numi["capabilities"]["usage"] is True
    assert numi["roles"]["water_month"] == "sensor.primary_water_used_this_month"

    await ws.send_json_auto_id(
        {"type": f"{DOMAIN}/usage_history", "device_id": numi["id"], "period": "week"}
    )
    async with asyncio.timeout(5):
        reply = await ws.receive_json()
    assert reply["success"], reply
    today = dt_util.now().date().isoformat()
    bucket = next(b for b in reply["result"]["buckets"] if b["key"] == today)
    assert bucket["volume"] == 12.0


def dial(body: dict[str, Any]) -> dict[str, Any]:
    """A command's settings, once its identity is checked."""
    identity = {key: body[key] for key in ("deviceId", "tenantId", "sku")}
    assert identity in (
        {"deviceId": NUMI_ID, "tenantId": TENANT_ID, "sku": "NUM"},
        {"deviceId": PUREWASH_ID, "tenantId": TENANT_ID, "sku": "BLD"},
    )
    return {key: body[key] for key in ("temperature", "flow", "position", "mode")}


async def refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Read the Numi again, as its clock would."""
    numi = next(
        t for t in account(hass, entry).products["toilet"] if t.device_id == NUMI_ID
    )
    await numi.async_refresh()
    await hass.async_block_till_done()


async def test_a_setting_is_the_default_until_its_wash_runs(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ToiletKohler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(toilet_coordinator, "SAVE_DELAY", 0)
    await setup(hass, config_entry)
    pressure = "number.primary_front_wash_pressure"
    assert hass.states.get(pressure).state == "3"
    assert hass.states.get("select.primary_rear_wash_spray_pattern").state == "wave"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": pressure, "value": 4}, blocking=True
    )
    verb, body = kohler.verbs("numi")[-1]
    assert (verb, dial(body)) == (
        "front-wash",
        {
            "temperature": "HeatMid",
            "flow": "FlowMidHigh",
            "position": "PositionMid",
            "mode": "ModeNormal",
        },
    )
    assert kohler.stored == [
        (
            "numi",
            "frontwash",
            {
                "flow": "FlowMidHigh",
                "temperature": "HeatMid",
                "position": "PositionMid",
                "massage": "ModeNormal",
            },
        )
    ]

    # While the rear wash runs, a change goes to it alone and the default stays.
    kohler.toilets[NUMI_ID]["rearWash"] = {
        "state": "On",
        "temperature": "HeatHigh",
        "flow": "FlowHigh",
        "position": "PositionFar",
        "massage": "ModePulsate",
    }
    temperature = "number.primary_rear_wash_temperature"
    await refresh(hass, config_entry)
    assert hass.states.get(temperature).state == "5"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": temperature, "value": 2}, blocking=True
    )
    verb, body = kohler.verbs("numi")[-1]
    assert (verb, dial(body)) == (
        "rear-wash",
        {
            "temperature": "HeatLowMid",
            "flow": "FlowHigh",
            "position": "PositionFar",
            "mode": "ModePulsate",
        },
    )
    assert len(kohler.stored) == 1

    # The seat's default: its command, then stored.
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.primary_default_seat_heat", "value": 4},
        blocking=True,
    )
    verb, body = kohler.verbs("numi")[-1]
    assert (verb, body["temperature"]) == ("heated-seat", "HeatMidHigh")
    assert kohler.stored[-1] == ("numi", "seat", {"temperature": "HeatMidHigh"})


async def test_a_purewash_fan_speed_waits_for_its_dryer_to_stop(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ToiletKohler,
) -> None:
    kohler.toilets[PUREWASH_ID]["dryer"] = {"state": "On", "temperature": "HeatMid"}
    await setup(hass, config_entry)

    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.guest_dryer_fan_speed", "value": 2},
            blocking=True,
        )
    assert refused.value.translation_key == "settings_while_running"
    assert kohler.verbs("blade") == []


async def test_a_numi_with_no_time_zone_is_told_hers_once(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ToiletKohler,
) -> None:
    kohler.toilets[NUMI_ID]["cleanStatus"]["timezone"] = None
    kohler.toilets[PUREWASH_ID]["cleanStatus"]["timezone"] = None
    await setup(hass, config_entry)
    await refresh(hass, config_entry)

    zones = [body for verb, body in kohler.verbs("numi") if verb == "setrtccontrol"]
    assert [body["timeZone"] for body in zones] == [hass.config.time_zone]
    # The app never sends it to a Purewash.
    assert kohler.verbs("blade") == []


async def test_the_dryer_is_set_live_while_it_runs_and_stored_otherwise(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ToiletKohler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(toilet_coordinator, "SAVE_DELAY", 0)
    await setup(hass, config_entry)
    temperature = "number.primary_dryer_temperature"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": temperature, "value": 4}, blocking=True
    )
    verb, body = kohler.verbs("numi")[-1]
    # The stored default keeps the model's position and pattern (§3.5).
    assert (verb, dial(body)) == (
        "dryer",
        {
            "temperature": "HeatMidHigh",
            "flow": "FlowMid",
            "position": "PositionMid",
            "mode": "ModeNormal",
        },
    )
    assert kohler.stored == [
        ("numi", "dryer", {"temperature": "HeatMidHigh", "flow": "FlowMid"})
    ]

    kohler.toilets[NUMI_ID]["dryer"] = {
        "state": "On",
        "temperature": "HeatLow",
        "flow": "FlowHigh",
    }
    await refresh(hass, config_entry)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.primary_dryer_fan_speed", "value": 2},
        blocking=True,
    )
    verb, body = kohler.verbs("numi")[-1]
    assert (verb, dial(body)) == (
        "dryer",
        {"temperature": "HeatLow", "flow": "FlowLowMid", "position": "", "mode": ""},
    )
    assert len(kohler.stored) == 1


async def test_two_defaults_changed_together_both_stand(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ToiletKohler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(toilet_coordinator, "SAVE_DELAY", 0)
    await setup(hass, config_entry)

    # As a scene sets them: at once, while Kohler's copy shows neither.
    await asyncio.gather(
        hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.primary_front_wash_temperature", "value": 5},
            blocking=True,
        ),
        hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.primary_front_wash_pressure", "value": 1},
            blocking=True,
        ),
    )
    first, second = (body for _, _, body in kohler.stored[-2:])
    # One after the other, the second keeping the first.
    assert {first["temperature"], first["flow"]} & {"HeatHigh", "FlowLow"}
    assert (second["temperature"], second["flow"]) == ("HeatHigh", "FlowLow")
    assert hass.states.get("number.primary_front_wash_temperature").state == "5"
    assert hass.states.get("number.primary_front_wash_pressure").state == "1"


async def test_a_status_the_app_doesn_t_know_is_read_rather_than_guessed(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ToiletKohler
) -> None:
    await setup(hass, config_entry)
    kohler.toilets[NUMI_ID]["frontWash"] = {"state": "On"}

    # Read again, not taken as every spray stopping.
    await deliver(hass, NUMI_ID, "NUMI_SPRAY_STS", status="SprayingSomethingNew")
    assert hass.states.get("sensor.primary_activity").state == "front_wash"
