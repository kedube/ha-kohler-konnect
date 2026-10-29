"""EvoCycle in Home Assistant: its readings and controls, and its water and ozone guarded.

Against a fake of its cloud reads and commands (`docs/protocol/evocycle.md`), since no
EvoCycle has been seen by this project.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.kohler_konnect.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import TENANT_ID, FakeKohler, FakeMqttClient
from .test_shower_entities import enable, setup

EVO_ID = "evo-00decaf0"
STATE = {
    "shower": {
        "standardModeShower": {"showerOn": 0, "temperature": 380.0, "flow": 80},
        "reCycleModeShower": {"showerOn": 0, "temperature": 390.0, "flow": 100},
    },
    "clean": {
        "rapidClean": {"cleanOn": 0, "lastCleanDate": 1790000000},
        "deepClean": {"cleanOn": 0, "lastCleanDate": 0},
    },
}
CONFIGURATION = {
    "deviceId": EVO_ID,
    "evoAboutModel": {
        "showerConfiguration": {
            "defaultShowerMode": 0,
            "defaultTemperature": 380.0,
            "defaultFlow": 80.0,
            "defaultWarmupMode": 1,
            "defaultStandardMaxTemperature": 480.0,
            "defaultCycleMaxTemperature": 430.0,
        },
        "cleanConfiguration": {"enableRapidClean": 1, "enableDeepClean": 0},
        "uiConfigurations": [
            {
                "language": "en",
                "languageRegion": "US",
                "temperatureUnits": 0,
                "flowUnits": 0,
                "hapticFeedback": 1,
            }
        ],
        "gatewayConfiguration": {
            "appVersion": "2.1",
            "mac": "AA:BB:CC:DD:EE:FF",
            "ssid": "Home Wi-Fi",
        },
    },
}


class EvoKohler(FakeKohler):
    """`FakeKohler` with an EvoCycle's reads; its commands land in `commands`."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.devices = [
            {
                "deviceId": EVO_ID,
                "sku": "EVO",
                "logicalName": "Spa Shower",
                "showOnBoardingScreen": False,
                "waterSavingGoal": {"isEnableWaterSave": True, "goalPercentage": 50},
            }
        ]
        #: Each account record update: the home and the body.
        self.patches: list[tuple[str, dict[str, Any]]] = []
        self.evo_state = copy.deepcopy(STATE)
        self.configuration: dict[str, Any] = copy.deepcopy(CONFIGURATION)
        self.reads = 0
        self.usage_queries: list[dict[str, str]] = []
        #: The Water Savings screen's reads: their paths' last part and queries.
        self.savings_queries: list[tuple[str, dict[str, str]]] = []

    async def _api(self, method: str, url: Any, data: Any) -> Any:
        path = url.path
        if method.lower() == "patch" and path.endswith("/device"):
            self.patches.append((path.split("/")[-2], data))
            return self._respond(method, url, (200, {}))
        if method.lower() == "get" and path.endswith(f"/customer-device/{TENANT_ID}"):
            homes = [{"homeId": "home-1", "devices": copy.deepcopy(self.devices)}]
            body = {"waterUnits": self.water_units, "customerHome": homes}
            return self._respond(method, url, (200, body))
        if method.lower() == "get" and path.endswith(f"/evo-state/{EVO_ID}"):
            self.reads += 1
            body = {
                "connectionState": "Connected",
                "recycledWaterUsed": 1234,
                "state": copy.deepcopy(self.evo_state),
            }
            return self._respond(method, url, (200, body))
        if method.lower() == "get" and path.endswith(
            f"/evo-dashboard/waterusage/{EVO_ID}"
        ):
            self.savings_queries.append(("waterusage", dict(url.query)))
            year = dt_util.now().year
            month = dt_util.now().strftime("%Y-%m")
            buckets = [
                {"intervalKey": f"{year}-01", "waterSaved": "5.5"},
                {
                    "intervalKey": month,
                    "waterSaved": "12",
                    "reCycleWaterPercentage": "62.5",
                    "standardWaterPercentage": "37.5",
                },
            ]
            return self._respond(method, url, (200, {"waterUsageData": buckets}))
        if method.lower() == "get" and path.endswith(f"/evo-dashboard/{EVO_ID}"):
            self.savings_queries.append(("dashboard", dict(url.query)))
            week = {"waterSaved": 30, "waterUsage": 10, "totalConsumption": 40}
            body = {"currentWeekUsageData": week, "previousWeekUsageData": None}
            return self._respond(method, url, (200, body))
        if method.lower() == "get" and path.endswith(f"/evo-usage/{EVO_ID}"):
            self.usage_queries.append(dict(url.query))
            today = dt_util.now().date()
            day = url.query["Interval"] == "Day"
            bucket = {
                "intervalKey": today.isoformat() if day else today.strftime("%Y-%m"),
                "waterUsage": 40.0,
                "waterSave": 25.0,
                "totalWaterConsumption": 65.0,
                "duration": 600.0,
            }
            body = {"evoUsageDataDetailsList": [bucket]}
            return self._respond(method, url, (200, body))
        if method.lower() == "get" and path.endswith(f"/evo-configuration/{EVO_ID}"):
            return self._respond(method, url, (200, copy.deepcopy(self.configuration)))
        return await super()._api(method, url, data)

    def sent(self, command: str) -> list[dict[str, Any]]:
        return [body for name, body in self.commands if name == command]


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> EvoKohler:
    return EvoKohler(aioclient_mock)


async def deliver(hass: HomeAssistant, data: dict[str, Any], **envelope: Any) -> None:
    FakeMqttClient.instances[-1].deliver(
        {"deviceid": EVO_ID, "sku": "EVO", "type": "STS", "data": data, **envelope}
    )
    await hass.async_block_till_done()


async def test_an_evocycle_shows_its_readings_and_flags_its_controls(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: EvoKohler
) -> None:
    kohler.water_units = "Metric"
    await setup(hass, config_entry)

    assert hass.states.get("sensor.spa_shower_activity").state == "idle"
    assert hass.states.get("binary_sensor.spa_shower_shower").state == "off"
    assert hass.states.get("sensor.spa_shower_water_saved").state == "1234"
    assert hass.states.get("sensor.spa_shower_last_rapid_clean").state.startswith(
        "2026-09-21"
    )
    assert hass.states.get("sensor.spa_shower_last_deep_clean").state == "unknown"

    for entity_id in (
        "select.spa_shower_shower",
        "button.spa_shower_stop_clean",
        "select.spa_shower_default_shower_mode",
        "number.spa_shower_default_flow",
        "switch.spa_shower_deep_clean_reminder",
    ):
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.attributes["untested"] is True, entity_id
    # The ozone starts wait for an owner to turn them on: the app asks first.
    registry = er.async_get(hass)
    for entity_id in (
        "button.spa_shower_start_rapid_clean",
        "button.spa_shower_start_deep_clean",
    ):
        entry = registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION, entity_id


async def test_messages_say_what_runs_and_info_ones_are_dropped(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: EvoKohler
) -> None:
    await setup(hass, config_entry)
    reads = kohler.reads

    await deliver(hass, {"shower": {"showerOn": 1, "showerMode": 1}})
    assert hass.states.get("sensor.spa_shower_activity").state == "cycle"
    await deliver(hass, {"shower": {"showerOn": 0, "showerMode": 1}}, type="INF")
    assert hass.states.get("sensor.spa_shower_activity").state == "cycle"
    await deliver(hass, {"product": {"errorCode": 1679}})
    problem = hass.states.get("binary_sensor.spa_shower_problem")
    assert (problem.state, problem.attributes["error_code"]) == ("on", 1679)
    assert kohler.reads == reads


async def test_the_shower_runs_with_its_own_temperature_and_flow(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: EvoKohler
) -> None:
    shower = enable(hass, config_entry, "select", f"{EVO_ID}_evo_shower", "spa_shower")
    await setup(hass, config_entry)
    identity = {"deviceId": EVO_ID, "tenantId": TENANT_ID, "sku": "EVO"}

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": shower, "option": "cycle"},
        blocking=True,
    )
    await deliver(hass, {"shower": {"showerOn": 1, "showerMode": 1}})
    await hass.services.async_call(
        "select", "select_option", {"entity_id": shower, "option": "off"}, blocking=True
    )
    assert kohler.sent("shower") == [
        {**identity, "showerOn": 1, "showerMode": 1, "temperature": 390, "flow": 100},
        {**identity, "showerOn": 0, "showerMode": 1, "temperature": 390, "flow": 100},
    ]


async def test_ozone_never_runs_with_the_shower_and_nothing_starts_in_a_fault(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: EvoKohler
) -> None:
    rapid = enable(
        hass, config_entry, "button", f"{EVO_ID}_start_rapid_clean", "spa_rapid"
    )
    shower = enable(hass, config_entry, "select", f"{EVO_ID}_evo_shower", "spa_shower")
    await setup(hass, config_entry)

    await deliver(hass, {"shower": {"showerOn": 1, "showerMode": 0}})
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "button", "press", {"entity_id": rapid}, blocking=True
        )
    assert refused.value.translation_key == "evocycle_running"

    await deliver(hass, {"shower": {"showerOn": 0, "showerMode": 0}})
    await hass.services.async_call(
        "button", "press", {"entity_id": rapid}, blocking=True
    )
    assert kohler.sent("clean")[-1]["cleanOn"] == 1

    await deliver(hass, {"clean": {"cleanOn": 1, "cleanMode": 0}})
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": shower, "option": "standard"},
            blocking=True,
        )
    assert refused.value.translation_key == "evocycle_cleaning"

    # A drain-motor fault stops all but the Standard shower, as the app's buttons stop.
    await deliver(
        hass, {"clean": {"cleanOn": 0, "cleanMode": 0}, "product": {"errorCode": 1651}}
    )
    for refused_too in ((shower, "cycle"),):
        with pytest.raises(HomeAssistantError) as refused:
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": refused_too[0], "option": refused_too[1]},
                blocking=True,
            )
        assert refused.value.translation_key == "evocycle_fault"
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "button", "press", {"entity_id": rapid}, blocking=True
        )
    assert refused.value.translation_key == "evocycle_fault"
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": shower, "option": "standard"},
        blocking=True,
    )
    assert [body["showerMode"] for body in kohler.sent("shower")] == [0]

    # A water-supply fault stops everything.
    await deliver(
        hass,
        {"shower": {"showerOn": 0, "showerMode": 0}, "product": {"errorCode": 16411}},
    )
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": shower, "option": "standard"},
            blocking=True,
        )
    assert refused.value.translation_key == "evocycle_fault"
    assert len(kohler.sent("shower")) == 1


async def test_a_default_is_written_whole_and_a_reminder_with_the_others(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: EvoKohler
) -> None:
    flow = enable(hass, config_entry, "number", f"{EVO_ID}_default_flow", "spa_flow")
    temperature = enable(
        hass, config_entry, "number", f"{EVO_ID}_default_temperature", "spa_temperature"
    )
    deep = enable(
        hass, config_entry, "switch", f"{EVO_ID}_deep_clean_reminder", "spa_deep"
    )
    await setup(hass, config_entry)
    assert hass.states.get(flow).state == "80.0"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": flow, "value": 64}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": temperature, "value": 40}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": deep}, blocking=True
    )
    first, second = kohler.sent("showersetting")
    assert (first["flow"], first["temperature"], first["warmupMode"]) == (64, 380, 1)
    # Kohler's copy still says 80: the second write keeps the first's change.
    assert (second["flow"], second["temperature"]) == (64, 400)
    assert float(hass.states.get(flow).state) == 64
    # A reminder never read counts as on, as the app shows it.
    assert kohler.sent("cleansetting") == [
        {
            "deviceId": EVO_ID,
            "tenantId": TENANT_ID,
            "sku": "EVO",
            "rapidCleanEnable": 1,
            "deepCleanEnable": 1,
            "showerFilterCleanEnable": 1,
        }
    ]

    # Whether it runs is read afresh, as the app reads it before a save.
    kohler.evo_state["shower"]["standardModeShower"]["showerOn"] = 1
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            "number", "set_value", {"entity_id": flow, "value": 100}, blocking=True
        )
    assert refused.value.translation_key == "settings_while_running"


async def test_the_default_temperature_stays_under_both_maximums(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: EvoKohler
) -> None:
    temperature = enable(
        hass, config_entry, "number", f"{EVO_ID}_default_temperature", "spa_temperature"
    )
    standard_max = enable(
        hass,
        config_entry,
        "number",
        f"{EVO_ID}_standard_max_temperature",
        "spa_standard_max",
    )
    flow = enable(hass, config_entry, "number", f"{EVO_ID}_default_flow", "spa_flow")
    await setup(hass, config_entry)

    # The app's slider stops at the lower maximum: Cycle's 43 °C.
    assert hass.states.get(temperature).attributes["max"] == 43
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": temperature, "value": 45},
            blocking=True,
        )
    # Lowering the Standard maximum under the default takes the default down with it.
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": standard_max, "value": 35},
        blocking=True,
    )
    (body,) = kohler.sent("showersetting")
    assert (body["standardModeMaxTemperature"], body["temperature"]) == (350, 350)
    assert hass.states.get(temperature).attributes["max"] == 35
    # Values between the app's steps go to the nearest: flow in fours.
    await hass.services.async_call(
        "number", "set_value", {"entity_id": flow, "value": 66}, blocking=True
    )
    assert kohler.sent("showersetting")[-1]["flow"] == 68


async def test_a_stop_is_sent_though_a_message_was_missed(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: EvoKohler
) -> None:
    shower = enable(hass, config_entry, "select", f"{EVO_ID}_evo_shower", "spa_shower")
    await setup(hass, config_entry)

    # It runs, though no message said so.
    kohler.evo_state["shower"]["reCycleModeShower"]["showerOn"] = 1
    await hass.services.async_call(
        "select", "select_option", {"entity_id": shower, "option": "off"}, blocking=True
    )
    (stop,) = kohler.sent("shower")
    assert (stop["showerOn"], stop["showerMode"]) == (0, 1)
    # Choosing what already runs sends nothing.
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": shower, "option": "cycle"},
        blocking=True,
    )
    assert len(kohler.sent("shower")) == 1


async def test_diagnostics_leave_out_identity_and_the_home_network(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: EvoKohler
) -> None:
    await setup(hass, config_entry)

    report = await async_get_config_entry_diagnostics(hass, config_entry)
    (evo,) = report["evocycles"]
    text = json.dumps(evo)
    for secret in (EVO_ID, TENANT_ID, "AA:BB:CC:DD:EE:FF", "Home Wi-Fi"):
        assert secret not in text, secret
    gateway = evo["replies"]["configuration"]["evoAboutModel"]["gatewayConfiguration"]
    assert gateway == {"appVersion": "2.1"}


async def test_an_evocycle_reports_the_fresh_water_it_used(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: EvoKohler
) -> None:
    kohler.water_units = "Metric"
    await setup(hass, config_entry)

    # Fresh water, as the app's Water Usage screen charts it, not what was recirculated.
    assert hass.states.get("sensor.spa_shower_water_used_today").state == "40.0"
    assert hass.states.get("sensor.spa_shower_duration_used_today").state == "10.0"
    assert {q["TimeZone"] for q in kohler.usage_queries} == {"UTC"}


async def test_the_interface_goes_out_whole_and_the_goal_from_a_fresh_record(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: EvoKohler
) -> None:
    await setup(hass, config_entry)
    assert hass.states.get("select.spa_shower_panel_units").state == "us"
    assert hass.states.get("switch.spa_shower_haptic_feedback").state == "on"
    assert hass.states.get("number.spa_shower_water_savings_target").state == "50"

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.spa_shower_panel_units", "option": "metric"},
        blocking=True,
    )
    assert kohler.sent("uisetting") == [
        {
            "deviceId": EVO_ID,
            "tenantId": TENANT_ID,
            "sku": "EVO",
            "language": "en",
            "languageRegion": "US",
            "temperatureUnits": 1,
            "flowUnits": 1,
            "hapticFeedback": 1,
            "backlight": "0",
            "isUpdateUI0Setting": True,
        }
    ]

    # Renamed in the app since setup: the goal keeps the new name.
    kohler.devices[0]["logicalName"] = "Spa"
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.spa_shower_water_savings_target", "value": 70},
        blocking=True,
    )
    assert kohler.patches == [
        (
            "home-1",
            {
                "deviceId": EVO_ID,
                "logicalname": "Spa",
                "sku": "EVO",
                "operation": "Update",
                "showOnBoardingScreen": False,
                "showWaterSavingGoalScreen": True,
                "waterSavingGoal": {"isEnableWaterSave": True, "goalPercentage": 70},
            },
        )
    ]
    assert hass.states.get("number.spa_shower_water_savings_target").state == "70"


@pytest.mark.parametrize("units", ["Metric", "Standard"])
async def test_the_water_savings_screen_s_figures(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: EvoKohler, units: str
) -> None:
    kohler.water_units = units
    await setup(hass, config_entry)

    def figure(key: str) -> float:
        return float(hass.states.get(f"sensor.spa_shower_{key}").state)

    # In the account's unit, to a tenth; this Home Assistant shows litres either way.
    close = 0.2 if units == "Standard" else 0.05
    assert figure("cycled_water_this_week") == pytest.approx(30, abs=close)
    assert figure("fresh_water_this_week") == pytest.approx(10, abs=close)
    # From the litres, not the app's whole-number division, which reads 0 %.
    assert figure("cycled_share_this_week") == 75.0
    assert figure("water_saved_this_month") == pytest.approx(12, abs=close)
    assert figure("water_saved_this_year") == pytest.approx(17.5, abs=close)
    assert figure("cycle_mode_share_this_month") == 62.5

    today = dt_util.now().date()
    assert kohler.savings_queries == [
        ("dashboard", {"CurrentDate": today.strftime("%m-%d-%Y"), "TimeZone": "UTC"}),
        (
            "waterusage",
            {
                "FromDate": f"01-01-{today.year}",
                "ToDate": f"12-31-{today.year}",
                "TimeZone": "UTC",
                "Interval": "Month",
            },
        ),
    ]
