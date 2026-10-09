"""The account coordinator with showers on it: seeding, routing, commands, firmware.

The shared ``FakeKohler`` answers for a faucet and 404s almost everything a valve or a
controller reads, so every shower test here runs against ``ShowerKohler``, which serves the
valve's and the controller's REST surface too, per device, from plain dicts a test edits.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from collections.abc import AsyncGenerator, Callable, Generator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientError
from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect.const import (
    CONF_MOBILE_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_REPORT_LOG_FILE,
    CONF_TEMPERATURE_UNIT,
    CONF_TENANT_ID,
    CONF_VALVE_MODEL,
    CONF_VALVES,
    CONF_WATER_UNITS,
    CONF_ZONE_GROUPING,
    CONF_ZONE_OUTLETS,
    DEFAULT_PRESET_TIMER_SECONDS,
    DOMAIN,
    FIRMWARE_CHECK_INTERVAL,
    device_issue_key,
)
from custom_components.kohler_konnect.coordinator import (
    KohlerKonnectCoordinator,
    Valve,
)
from custom_components.kohler_konnect.konnect import (
    AuthError,
    AuthUnavailable,
    DeviceOffline,
    KohlerAuth,
    KohlerError,
    SignInBlocked,
    TokenSet,
)
from custom_components.kohler_konnect.konnect.valve_hex import decode_word

from .conftest import (
    MOBILE_ID,
    TENANT_ID,
    USERNAME,
    FakeKohler,
    FakeMqttClient,
    account,
    make_jwt,
    registered_device,
    unload,
    wait_for,
)

LEFT = "gcs-left00000001"
RIGHT = "gcs-right0000002"
MAIN = "hub-main00000001"
GUEST = "hub-guest0000002"

# What Azure B2C answers for a refresh token it has retired.
REJECTED = (
    400,
    {"error": "invalid_grant", "error_description": "AADB2C90080: The grant expired."},
)


def token_reply(n: int = 90) -> tuple[int, dict[str, Any]]:
    """A good token response, for queueing ahead of a bad one."""
    return (
        200,
        {
            "access_token": make_jwt({"oid": TENANT_ID, "n": n}),
            "refresh_token": f"refresh-queued-{n}",
            "expires_in": "3600",
        },
    )


# --------------------------------------------------------------------------- #
# Payloads, shaped like Kohler's
# --------------------------------------------------------------------------- #


def valve_device(device_id: str = LEFT, name: str = "Shower") -> dict[str, Any]:
    return {"deviceId": device_id, "sku": "GCS", "logicalName": name}


def hub_device(device_id: str = MAIN, name: str = "Main Bath") -> dict[str, Any]:
    return {"deviceId": device_id, "sku": "HUB", "logicalName": name}


def outlet_records(ids: range, *, run_time: int = 1800) -> list[dict[str, str]]:
    """`outletConfigurations[]` as REST reports them: display units, REST spellings."""
    return [
        {
            "outLetId": str(outlet_id),
            "outLetType": "31",
            "outLetFlags": "1",
            "minimumOutletTemperature": "15",
            "defaultOutletTemperature": "38.8",
            "maximumOutletTemperature": "47.7",
            "minimumFlowrate": "4",
            "maximumFlowrate": "50",
            "defaultFlowrate": "50",
            "maximumRuntime": str(run_time),
        }
        for outlet_id in ids
    ]


def valve_settings(
    split: tuple[int, int] = (3, 0), *, run_time: int = 1800, records: bool = True
) -> dict[str, Any]:
    """A whole `gcsadvancestate` reply: the outlet split and every outlet's record.

    Zone 2's first outlet is `outLetId` 3 on every model, so its range starts there.
    """
    first, second = split

    def valve(name: str, count: int, ids: range) -> dict[str, Any]:
        entry: dict[str, Any] = {"valve": name, "noOfOutlets": count}
        if records:
            entry["outletConfigurations"] = outlet_records(ids, run_time=run_time)
        return entry

    return {
        "setting": {
            "valveSettings": [
                valve("valve1", first, range(first)),
                valve("valve2", second, range(3, 3 + second)),
            ]
        }
    }


def valve_zone(
    *open_outlets: int, celsius: str = "38.5", flow: str = "50", paused: bool = False
) -> dict[str, str]:
    """One zone of a `gcs-state` read: `outN` flags, setpoints, the pause flag."""
    zone = {f"out{n}": "1" if n in open_outlets else "0" for n in (1, 2, 3)}
    zone.update(
        temperatureSetpoint=celsius, flowSetpoint=flow, pauseFlag="1" if paused else "0"
    )
    return zone


def valve_state(
    zone1: dict[str, str] | None = None,
    zone2: dict[str, str] | None = None,
    *,
    preset: str = "0",
) -> dict[str, Any]:
    """A whole `gcs-state` reply."""
    state: dict[str, Any] = {
        "valve1": zone1 or valve_zone(),
        "warmUpState": {"warmUp": "warmUpDisabled", "state": "warmUpNotInProgress"},
        "currentSystemState": "normalOperation",
        "presetOrExperienceId": preset,
        "totalVolume": "537557808",
        "totalFlow": "2056.0",
    }
    if zone2 is not None:
        state["valve2"] = zone2
    return {"connectionState": "Connected", "state": state}


def hub_configuration(
    split: tuple[int, int] = (3, 0), *, ip: str = "192.168.1.50"
) -> dict[str, Any]:
    first, second = split
    return {
        "configuration": {
            "parts": {
                "valve1": "Connected",
                "amplifier": "Connected",
                "light": "Connected",
                "steam": "Connected",
            },
            "zoneone": {"configuredoutlets": str(first)},
            "zonetwo": {"configuredoutlets": str(second)},
            "systemSettings": {"maxShowerDuration": "30"},
            "steamSettings": {"defaultTemperature": "110", "defaultTime": "15"},
            "amplifierSettings": {"monoVolume": "5"},
            "lightSettings": [{"name": "groupA"}],
            "about": {"hub": {"wlan": {"ip": ip}}},
        }
    }


def hub_state(*zone1_outlets: int, status: str = "OFF") -> dict[str, Any]:
    return {
        "state": {
            "shower": [
                {
                    "zone": "1",
                    "status": status,
                    "outlets": [1 if n in zone1_outlets else 0 for n in (1, 2, 3)],
                    "temperature": "100",
                    "flowRate": "50",
                }
            ],
            "musicStateModel": {"status": "OFF"},
            "hubSteamState": {"status": "OFF"},
        },
        "errorState": "0",
    }


def firmware(current: str, latest: str | None = None) -> dict[str, Any]:
    return {
        "currentFirmware": current,
        "firmware": latest or current,
        "firmwareUpdateAvailable": latest is not None and latest != current,
        "mandatoryUpdate": False,
    }


def solo_message(
    device_id: str, primary: str, secondary: str = "00000000", **fields: Any
) -> dict[str, Any]:
    """A `GCS_SOLO_STS` as the valve pushes it, 16-character words and all."""
    return {
        "sku": "GCS",
        "deviceid": device_id,
        "data": {
            "type": "Status",
            "code": "GCS_SOLO_STS",
            "attributes": [
                {
                    "code": "GCS_SOLO_STS",
                    "primaryValve1": f"{primary}00000001",
                    "secondaryValve1": f"{secondary}00000001",
                    **fields,
                }
            ],
        },
    }


def hub_message(
    device_id: str, code: str, *attributes: dict[str, Any], **data: Any
) -> dict[str, Any]:
    return {
        "sku": "HUB",
        "deviceid": device_id,
        "data": {"code": code, "attributes": list(attributes), **data},
    }


class ShowerKohler(FakeKohler):
    """`FakeKohler`, plus the valve and controller reads, per device.

    Each table is keyed by device id; a device missing from one answers 404 there, as
    Kohler does for a read it has nothing for. Every POST is recorded in ``posts`` with its
    full path, refused or not.
    """

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.devices = [valve_device()]
        self.valve_settings: dict[str, Any] = {LEFT: valve_settings()}
        self.valve_states: dict[str, Any] = {LEFT: valve_state()}
        self.valve_presets: dict[str, Any] = {
            LEFT: {
                "gcsPresetExperienceDetails": [
                    {
                        "presetId": "1",
                        "title": "Default",
                        "time": str(DEFAULT_PRESET_TIMER_SECONDS),
                    },
                    {"presetId": "2", "title": "Morning", "isExperience": "False"},
                    {"presetId": "20", "title": "Cool Down", "isExperience": "True"},
                ]
            }
        }
        self.valve_configurations: dict[str, Any] = {
            LEFT: {
                "createdTime": "2024-03-11T14:22:31Z",
                "configuration": {"about": {"uI2": {"firmware": "2.2"}}},
            }
        }
        self.hub_configurations: dict[str, Any] = {}
        self.hub_states: dict[str, Any] = {}
        # None answers 404, which is how Kohler says a controller has no favorites.
        self.hub_favorites: dict[str, Any] = {}
        self.hub_experiences: dict[str, Any] = {}
        self.hub_errors: dict[str, Any] = {}
        # Keyed (part, device id), part being `gcs`, `gateway` or `hub`.
        self.firmware_replies: dict[tuple[str, str], Any] = {}
        self.posts: list[tuple[str, Any]] = []
        # Set to hold any request whose path contains `gate_marker` until released.
        self.gate: asyncio.Event | None = None
        self.gate_marker = ""
        self.waiting = False

    def add_controller(
        self,
        device_id: str = MAIN,
        name: str = "Main Bath",
        *,
        split: tuple[int, int] = (3, 0),
    ) -> None:
        self.devices.append(hub_device(device_id, name))
        self.hub_configurations[device_id] = hub_configuration(split)
        self.hub_states[device_id] = hub_state()
        self.hub_favorites[device_id] = {
            "favorites": [{"id": "1", "name": "Hair Wash", "isExperience": False}]
        }
        self.hub_experiences[device_id] = {
            "experiences": {
                "showerExperiences": [{"title": "Rain"}, {"title": ""}],
                "steamExperiences": [{"name": "Eucalyptus"}],
                "iceShowerExperiences": [],
            }
        }
        self.hub_errors[device_id] = {
            "errorDetails": [
                {"errorCode": "0", "title": "No fault"},
                {"errorCode": "E12", "title": "Steam generator"},
                "not an object",
            ]
        }

    def add_valve(self, device_id: str = RIGHT, name: str = "Tub") -> None:
        self.devices.append(valve_device(device_id, name))
        self.valve_settings[device_id] = valve_settings()
        self.valve_states[device_id] = valve_state()

    def _shower_read(self, path: str) -> tuple[int, Any] | None:
        """The reply to a shower read, or None when this is not one."""
        if path.endswith("/about"):
            return None  # the base class's 404, which reads as {}
        device_id = path.rstrip("/").rsplit("/", 1)[-1]
        routes = [
            ("/gcs-state/gcsadvancestate/", self.valve_settings),
            ("/gcs-state/", self.valve_states),
            ("/gcs-preset/", self.valve_presets),
            ("/gcs-configuration/", self.valve_configurations),
            ("/hub-configuration/", self.hub_configurations),
            ("/hub-state/", self.hub_states),
        ]
        for marker, table in routes:
            if marker in path:
                if device_id not in table:
                    return (404, {"message": "not found"})
                return (200, copy.deepcopy(table[device_id]))
        for marker in ("/hub-experience/", "/hub-diagnostics/"):
            if marker in path:
                device_id = path.split(marker)[1].split("/")[0]
                if marker == "/hub-diagnostics/":
                    return (200, copy.deepcopy(self.hub_errors.get(device_id, {})))
                table = (
                    self.hub_favorites
                    if path.endswith("/favorites")
                    else self.hub_experiences
                )
                if table.get(device_id) is None:
                    return (404, {"message": "No favorites found"})
                return (200, copy.deepcopy(table[device_id]))
        if "/firmware/" in path:
            tail = path.split("/firmware/")[1].split("/")
            part = "gateway" if tail[1] == "gateway" else tail[0]
            reply = self.firmware_replies.get((part, tail[-1]))
            return (404, {"message": "not found"}) if reply is None else (200, reply)
        return None

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        path = url.path
        if method.lower() == "post":
            self.posts.append((path, data))
        if self.gate is not None and self.gate_marker and self.gate_marker in path:
            self.waiting = True
            await self.gate.wait()
        # A queued reply wins, exactly as in the base class.
        queued = any(path.endswith(s) and q for s, q in self.api_queue.items())
        if not queued and method.lower() == "get":
            reply = self._shower_read(path)
            if reply is not None:
                return self._respond(method, url, reply)
        return await super()._api(method, url, data)

    def posted(self, suffix: str) -> list[Any]:
        """The bodies POSTed to a path ending in ``suffix``, oldest first."""
        return [body for path, body in self.posts if path.endswith(suffix)]

    def reads(self, marker: str) -> int:
        """How many requests went to a path containing ``marker``."""
        return sum(1 for _, url, _, _ in self.mocker.mock_calls if marker in str(url))

    def fail_every_time(self, suffix: str, reply: Any, times: int = 10) -> None:
        """Fail a read on setup, on the reseed that follows connecting, and after."""
        self.fail_api(suffix, *[reply] * times)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> ShowerKohler:
    """Replaces the faucet fake for this module: an account with one valve."""
    return ShowerKohler(aioclient_mock)


SHOWER_DATA = {
    CONF_USERNAME: USERNAME,
    CONF_REFRESH_TOKEN: "refresh-0",
    CONF_TENANT_ID: TENANT_ID,
    CONF_TEMPERATURE_UNIT: "Fahrenheit",
    CONF_WATER_UNITS: "Standard",
    CONF_MOBILE_DEVICE_ID: MOBILE_ID,
    CONF_VALVE_MODEL: "K-28210",
    CONF_ZONE_OUTLETS: [3, 0],
}


@pytest.fixture
async def shower_entry(hass: HomeAssistant) -> AsyncGenerator[MockConfigEntry]:
    """An entry as the setup flow leaves it for a K-28210 account. Not set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Kohler Konnect ({USERNAME})",
        unique_id=USERNAME,
        data=dict(SHOWER_DATA),
    )
    entry.add_to_hass(hass)
    yield entry
    await unload(hass, entry)


@pytest.fixture(autouse=True)
def prompt_usage_reads() -> Generator[None]:
    """Re-read usage the moment a shower ends rather than 90 s later.

    Any test whose shower stops would otherwise sit in `async_block_till_done` for the
    whole delay, waiting on that read.
    """
    with (
        patch(
            "custom_components.kohler_konnect.coordinator.USAGE_REFRESH_DELAY_SECONDS",
            0,
        ),
        patch(
            "custom_components.kohler_konnect.coordinator.USAGE_RETRY_DELAY_SECONDS", 0
        ),
    ):
        yield


@pytest.fixture
def instant_verify() -> Generator[None]:
    """Read an outlet write back at once rather than after the cloud's 30 s lag."""
    with patch(
        "custom_components.kohler_konnect.coordinator.OUTLET_WRITE_VERIFY_DELAY_SECONDS",
        0,
    ):
        yield


async def start(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Set the entry up, wait for the stream and its reseed, return the coordinator."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    coordinator = account(hass, entry)
    await wait_for(hass, lambda: coordinator.stream.connected, "the stream")
    await hass.async_block_till_done()
    return coordinator


def stream() -> FakeMqttClient:
    """The live MQTT client — the newest one, after any reconnect."""
    return FakeMqttClient.instances[-1]


async def push(hass: HomeAssistant, *payloads: dict[str, Any]) -> None:
    """Deliver messages over the stream and let them land."""
    for payload in payloads:
        stream().deliver(payload)
    await hass.async_block_till_done()


async def reconnect(hass: HomeAssistant, coordinator: Any) -> None:
    """Drop the stream and wait for the reconnect and its reseed."""
    clients = len(FakeMqttClient.instances)
    stream().drop()
    await wait_for(
        hass,
        lambda: (
            len(FakeMqttClient.instances) > clients and coordinator.stream.connected
        ),
        "the reconnection",
    )
    await hass.async_block_till_done()


async def eventually(condition: Callable[[], bool], what: str) -> None:
    """Wait for a condition without `async_block_till_done`, for a task that lingers."""
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"timed out waiting for {what}")


def prompts(hass: HomeAssistant) -> list[str]:
    """The sources of every flow in progress — `reauth` is a sign-in prompt."""
    return [f["context"]["source"] for f in hass.config_entries.flow.async_progress()]


def state(hass: HomeAssistant, entity_id: str) -> str:
    entity = hass.states.get(entity_id)
    assert entity is not None, entity_id
    return entity.state


# =========================================================================== #
# Seeding at setup
# =========================================================================== #


async def test_setup_seeds_the_valve_over_rest_before_any_message(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """MQTT says nothing until the shower next changes, so the seed is all there is."""
    kohler.valve_states[LEFT] = valve_state(valve_zone(1, celsius="40.5"), preset="2")
    coordinator = await start(hass, shower_entry)

    (valve,) = coordinator.valves
    assert valve.name == "Anthem Valve" and valve.tag is None
    assert valve.gcs_state.outlets == [True, False, False]
    assert valve.gcs_state.valve1.temperature_celsius == 40.5
    assert valve.gcs_state.active_preset_id == 2
    assert state(hass, "switch.anthem_valve_rainhead_1") == "on"
    assert state(hass, "switch.anthem_valve_rainhead_2") == "off"
    # Every outlet's record, so a duration write has something to write back.
    assert valve.outlet_run_times == {1: 1800, 2: 1800, 3: 1800}
    assert valve.run_time_limits_for_zone(1) == (1800,)
    assert sorted(valve.gcs_state.presets) == [1, 2, 20]
    assert valve.firmware == "2.2"
    assert valve.created_time == "2024-03-11T14:22:31Z"
    # Read at setup and again once the stream connects — and not a third time by the
    # first refresh in between, which used to repeat every read.
    assert kohler.reads(f"/gcs-state/{LEFT}") == 2


async def test_setup_seeds_each_controller_from_its_own_reads(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Configuration, experiences, faults, state, favorites: what its entities stand on."""
    kohler.add_controller()
    kohler.hub_states[MAIN] = hub_state(1, status="ON")
    coordinator = await start(hass, shower_entry)

    (controller,) = coordinator.controllers
    assert controller.name == "Anthem Plus"
    assert controller.water_is_running is True
    assert controller.favorites == [
        {"id": "1", "name": "Hair Wash", "isExperience": False}
    ]
    # Only categories with a titled entry: the control body carries the title.
    assert controller.experiences == {
        "showerExperiences": [{"title": "Rain"}],
        "steamExperiences": [{"name": "Eucalyptus"}],
    }
    # Code "0" is the controller saying "no fault", not a fault.
    assert [e["errorCode"] for e in controller.active_errors] == ["E12"]
    assert controller.capabilities.known and controller.capabilities.steam
    assert controller.settings.max_shower_duration_minutes == 30
    assert state(hass, "binary_sensor.anthem_plus_zone_1_outlet_1") == "on"
    assert state(hass, "binary_sensor.anthem_plus_problem") == "on"
    device = registered_device(hass, MAIN)
    assert device.configuration_url == "http://192.168.1.50/"


async def test_a_controller_decodes_its_zones_with_the_layout_it_reports(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Two bathrooms need not have the same valve; the entry stores only one model."""
    kohler.add_controller(split=(3, 3))
    coordinator = await start(hass, shower_entry)
    assert coordinator.controllers[0].model.total_outlets == 6
    # The valve's own read said 3 + 0, and it keeps that.
    assert coordinator.valves[0].model.total_outlets == 3
    assert hass.states.get("binary_sensor.anthem_plus_zone_2_outlet_3") is not None


async def test_a_valve_whose_layout_differs_from_the_entry_is_seeded_with_its_own(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Topology is applied before state, or zone 2 would be thrown away on the way in."""
    kohler.valve_settings[LEFT] = valve_settings((3, 3))
    kohler.valve_states[LEFT] = valve_state(valve_zone(), valve_zone(2))
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    assert valve.model.sku == "K-28212"
    assert valve.gcs.model.sku == "K-28212"  # encodes with it too
    assert valve.gcs_state.zone_outlets(2) == [False, True, False]


async def test_a_controller_with_no_saved_favorites_is_not_an_error(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The endpoint 404s for "none saved" rather than returning an empty list."""
    caplog.set_level(logging.DEBUG, logger="custom_components.kohler_konnect")
    kohler.add_controller()
    kohler.hub_favorites[MAIN] = None
    coordinator = await start(hass, shower_entry)
    assert coordinator.controllers[0].favorites == []
    assert "No HUB favorites are saved" in caplog.text
    assert "Could not read HUB favorites" not in caplog.text


async def test_one_failing_controller_read_does_not_cost_the_rest_of_its_seed(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Each read is guarded on its own, so a bad endpoint blanks only what it feeds."""
    kohler.add_controller()
    kohler.hub_states[MAIN] = hub_state(2, status="ON")
    for suffix in (
        f"/hub-configuration/{MAIN}",
        f"/hub-experience/{MAIN}/experiences",
        f"/hub-experience/{MAIN}/favorites",
    ):
        kohler.fail_every_time(suffix, (500, {"message": "Something went wrong"}))
    coordinator = await start(hass, shower_entry)

    controller = coordinator.controllers[0]
    # Nothing latched from a read that failed: the next reseed tries again.
    assert not controller.capabilities.known
    assert controller.experiences == {}
    assert controller.favorites == []
    # And the reads that did answer still landed.
    assert controller.state.outlets == [False, True, False]
    assert [e["errorCode"] for e in controller.active_errors] == ["E12"]


async def test_one_device_failing_unexpectedly_does_not_blank_the_others(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each device seeds alongside the others; a bug in one must not cancel its siblings."""
    kohler.add_controller()
    kohler.hub_states[MAIN] = hub_state(1, status="ON")
    with patch.object(Valve, "async_seed", AsyncMock(side_effect=RuntimeError("bug"))):
        coordinator = await start(hass, shower_entry)
    assert shower_entry.state is ConfigEntryState.LOADED
    assert coordinator.valves[0].gcs_state.valve1 is None
    assert coordinator.controllers[0].water_is_running is True
    assert "Seeding a Kohler device failed: bug" in caplog.text


async def test_an_empty_controller_configuration_does_not_cost_the_rest_of_its_seed(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The same guarantee, for a reply that is empty rather than an error."""
    kohler.add_controller()
    kohler.hub_states[MAIN] = hub_state(1, status="ON")
    kohler.hub_configurations[MAIN] = ""  # HTTP 200 with an empty body
    coordinator = await start(hass, shower_entry)
    controller = coordinator.controllers[0]
    assert controller.water_is_running is True
    assert [e["errorCode"] for e in controller.active_errors] == ["E12"]
    assert controller.favorites


# =========================================================================== #
# Several devices on one account
# =========================================================================== #


async def test_several_valves_and_controllers_each_get_a_name_of_their_own(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Names decide entity ids; with two of a kind "Anthem Valve" would collide.

    The Konnect name is used when it is unique; a shared one, or one that is really the
    device id, falls back to the device's place in the list — never to the id itself.
    """
    kohler.devices = [valve_device(LEFT, "Shower"), valve_device(RIGHT, "Shower")]
    kohler.valve_settings[RIGHT] = valve_settings()
    kohler.valve_states[RIGHT] = valve_state()
    kohler.add_controller(MAIN, "Main Bath")
    kohler.add_controller(GUEST, GUEST)
    coordinator = await start(hass, shower_entry)

    assert [v.name for v in coordinator.valves] == ["Anthem Valve 1", "Anthem Valve 2"]
    # The shared warm-up journal is stamped per valve once there are several.
    assert [v.tag for v in coordinator.valves] == [LEFT, RIGHT]
    assert [c.name for c in coordinator.controllers] == [
        "Anthem Plus Main Bath",
        "Anthem Plus 2",
    ]
    for device_id, name in ((RIGHT, "Anthem Valve 2"), (GUEST, "Anthem Plus 2")):
        assert registered_device(hass, device_id).name == name
    # Still one connection, carrying everything.
    assert len(FakeMqttClient.instances) == 1
    assert repr(coordinator.valves[1]) == f"<Valve {RIGHT} 'Anthem Valve 2'>"
    assert repr(coordinator.controllers[1]) == f"<Controller {GUEST} 'Anthem Plus 2'>"


async def test_each_message_changes_only_the_device_it_names(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """One account-level stream carries every device's messages; the id sorts them."""
    kohler.add_valve(RIGHT, "Tub")
    kohler.add_controller(MAIN, "Main Bath")
    kohler.add_controller(GUEST, "Guest Bath")
    coordinator = await start(hass, shower_entry)
    left, right = coordinator.valves
    main, guest = coordinator.controllers
    left_before = (left.gcs_state.valve1, left.gcs_state.last_update)
    main_before = (dict(main.state.zones), main.state.last_update)

    await push(
        hass,
        solo_message(RIGHT, "0184C802"),
        hub_message(
            GUEST,
            "SHOWER_VALVE_STS",
            {"zone": "1", "status": "ON", "outlets": [0, 0, 1]},
        ),
        # A device added in the app since setup is ignored until a reload lists it.
        solo_message("gcs-added-later", "0184C807"),
    )

    assert right.gcs_state.outlets == [False, True, False]
    assert guest.state.outlets == [False, False, True]
    assert (left.gcs_state.valve1, left.gcs_state.last_update) == left_before
    assert (dict(main.state.zones), main.state.last_update) == main_before
    # Entities read freshness from the snapshot, keyed by device.
    assert coordinator.data["gcs_last_update"][RIGHT] is not None
    assert coordinator.data["gcs_last_update"][LEFT] == left_before[1]


async def test_valve_and_controller_messages_are_kept_as_warmup_context(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Light records, by device, so one valve's journal can leave out another's traffic.

    Only the valve's own status keeps the fields that tell a configuration write apart.
    """
    kohler.add_controller()
    coordinator = await start(hass, shower_entry)
    await push(
        hass,
        solo_message(
            LEFT,
            "0184C800",
            configChangeIndent="1",
            warmUpStatus="warmUpNotInProgress",
            totalFlow="2056.0",
        ),
        hub_message(MAIN, "MUSIC_STS", {"component": "amplifier", "status": "ON"}),
        solo_message("gcs-not-ours", "0184C800"),
    )
    records = list(coordinator._recent_messages)
    assert [(r["device"], r["code"]) for r in records] == [
        (LEFT, "GCS_SOLO_STS"),
        (MAIN, "MUSIC_STS"),
    ]
    assert records[0]["configChangeIndent"] == "1"
    assert records[0]["warmUpStatus"] == "warmUpNotInProgress"
    assert "totalFlow" not in records[0]
    assert "configChangeIndent" not in records[1]


# =========================================================================== #
# Messages, decoded into entities
# =========================================================================== #


async def test_a_valve_status_message_reaches_the_entities_at_once(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Push-only: this is the only way a shower change reaches Home Assistant."""
    coordinator = await start(hass, shower_entry)
    await push(
        hass, solo_message(LEFT, "0184C803", currentSystemState="showerInProgress")
    )
    assert state(hass, "switch.anthem_valve_rainhead_1") == "on"
    assert state(hass, "switch.anthem_valve_rainhead_2") == "on"
    assert state(hass, "switch.anthem_valve_rainhead_3") == "off"
    assert state(hass, "sensor.anthem_valve_system_state") == "showerInProgress"
    assert coordinator.valves[0].zone_flowing_for(1) is not None

    # Paused with both outlets still assigned: no water comes out of either.
    await push(hass, solo_message(LEFT, "0184C843"))
    assert state(hass, "switch.anthem_valve_rainhead_1") == "off"
    assert coordinator.valves[0].gcs_state.assigned_outlets == [True, True, False]
    assert coordinator.valves[0].zone_flowing_for(1) is None


async def test_malformed_valve_messages_are_ignored_and_the_last_state_kept(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each of these has been, or could be, what a firmware quirk or a schema change sends.

    None may raise in the callback; each still proves the valve is talking.
    """
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    await push(hass, solo_message(LEFT, "0184C801"))
    word = valve.gcs_state.valve1
    heard = valve.gcs_state.last_update

    await asyncio.sleep(0.01)  # so a fresh timestamp is distinguishable
    await push(
        hass,
        {"sku": "GCS", "deviceid": LEFT, "data": "not an object"},
        {"sku": "GCS", "deviceid": LEFT, "data": {"code": "GCS_SOLO_STS"}},
        {
            "sku": "GCS",
            "deviceid": LEFT,
            "data": {"code": "GCS_SOLO_STS", "attributes": "x"},
        },
        solo_message(LEFT, "nonsense"),
        {"sku": "GCS", "deviceid": LEFT, "data": {"code": "SOMETHING_NEW_STS"}},
    )
    assert valve.gcs_state.valve1 == word
    assert state(hass, "switch.anthem_valve_rainhead_1") == "on"
    assert valve.gcs_state.last_update > heard
    assert "Traceback" not in caplog.text


async def test_a_controller_message_updates_its_zones_and_favorites(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The controller's pushes, routed to its state and on to its entities."""
    kohler.add_controller()
    coordinator = await start(hass, shower_entry)
    controller = coordinator.controllers[0]

    await push(
        hass,
        hub_message(
            MAIN,
            "SHOWER_VALVE_STS",
            {"zone": "1", "status": "ON", "outlets": [1, 0, 0], "temperature": "104"},
        ),
        hub_message(
            MAIN, "FAVORITE_STS", {"id": "1", "name": "Hair Wash", "status": "ON"}
        ),
    )
    assert state(hass, "binary_sensor.anthem_plus_zone_1_outlet_1") == "on"
    assert state(hass, "switch.anthem_plus_shower") == "on"
    assert state(hass, "select.anthem_plus_favorite") == "Hair Wash"

    # The last favorite deleted in the app: an empty snapshot is an answer too.
    await push(hass, hub_message(MAIN, "FAVORITES_SNAPSHOT"))
    assert controller.favorites == []


# =========================================================================== #
# Reconnecting, and the reseed that follows
# =========================================================================== #


async def test_a_reconnect_reseeds_what_changed_while_the_stream_was_down(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The broker replays nothing on connect; without the read the gap is never filled."""
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    await push(hass, solo_message(LEFT, "0184C801"))
    assert valve.zone_flowing_for(1) is not None

    kohler.valve_states[LEFT] = valve_state(valve_zone(2))
    reads = kohler.reads(f"/gcs-state/{LEFT}")
    await reconnect(hass, coordinator)

    assert kohler.reads(f"/gcs-state/{LEFT}") == reads + 1
    assert valve.gcs_state.outlets == [False, True, False]
    assert state(hass, "switch.anthem_valve_rainhead_2") == "on"
    # How long the zone ran across the gap is unknowable, so it is not made up.
    assert valve.zone_flowing_for(1) is None


async def test_a_controller_that_moved_address_has_its_page_link_follow(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The device page links to the controller's web settings; a DHCP move must not strand it."""
    kohler.add_controller()
    coordinator = await start(hass, shower_entry)
    kohler.hub_configurations[MAIN] = hub_configuration(ip="192.168.1.77")
    await reconnect(hass, coordinator)
    device = registered_device(hass, MAIN)
    assert device.configuration_url == "http://192.168.1.77/"


async def test_a_second_connect_while_reseeding_does_not_start_a_rival(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Two reseeds would race each other over the same state objects."""
    coordinator = await start(hass, shower_entry)
    reads = kohler.reads(f"/gcs-state/{LEFT}")
    coordinator._handle_connected()
    coordinator._handle_connected()
    await hass.async_block_till_done()
    assert kohler.reads(f"/gcs-state/{LEFT}") == reads + 1


@pytest.mark.parametrize(
    ("token", "asks"),
    [(REJECTED, True), (ClientError("connection reset"), False)],
    ids=["rejected", "unreachable"],
)
async def test_a_reseed_that_cannot_sign_in_asks_only_when_the_sign_in_is_dead(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    token: Any,
    asks: bool,
) -> None:
    """The next connect cannot fix a rejected credential; it can fix an outage."""
    coordinator = await start(hass, shower_entry)
    coordinator.auth.invalidate_access_token()
    kohler.token_queue.append(token)
    coordinator._handle_connected()
    await hass.async_block_till_done()
    assert (prompts(hass) == [SOURCE_REAUTH]) is asks
    assert shower_entry.state is ConfigEntryState.LOADED


async def test_unloading_mid_reseed_cancels_it(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A dozen round trips, ending in an entry write: it must not outlive the entry."""
    coordinator = await start(hass, shower_entry)
    kohler.gate = asyncio.Event()
    kohler.gate_marker = f"/gcsadvancestate/{LEFT}"
    coordinator._handle_connected()
    task = coordinator._reseed_task
    for _ in range(100):
        if kohler.waiting:
            break
        await asyncio.sleep(0.01)
    assert kohler.waiting

    assert await hass.config_entries.async_unload(shower_entry.entry_id)
    kohler.gate.set()
    # The cancellation lands a few turns of the loop later — one on Home Assistant 2026.3,
    # three on 2026.10 — so wait for the task to end rather than counting turns. Released
    # and not cancelled, it would run to the end instead, and fail the check below.
    await asyncio.wait({task}, timeout=1)
    assert task.cancelled()
    assert coordinator._reseed_task is None


async def test_a_manual_refresh_reads_the_account_for_real(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Only the first refresh after setup is skipped; update_entity must really read."""
    coordinator = await start(hass, shower_entry)
    kohler.valve_states[LEFT] = valve_state(valve_zone(3))
    reads = kohler.reads(f"/gcs-state/{LEFT}")
    await coordinator.async_refresh()
    assert kohler.reads(f"/gcs-state/{LEFT}") == reads + 1
    assert coordinator.valves[0].gcs_state.outlets == [False, False, True]
    assert coordinator.last_update_success


@pytest.mark.parametrize(
    ("token", "asks"),
    [(REJECTED, True), (ClientError("connection reset"), False)],
    ids=["rejected", "unreachable"],
)
async def test_a_manual_refresh_that_cannot_sign_in_fails_and_asks_only_if_dead(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    token: Any,
    asks: bool,
) -> None:
    """As at setup: an outage is retried, a rejected credential needs the owner."""
    coordinator = await start(hass, shower_entry)
    coordinator.auth.invalidate_access_token()
    kohler.token_queue.append(token)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success
    assert (prompts(hass) == [SOURCE_REAUTH]) is asks


# =========================================================================== #
# Setting up, and failing to
# =========================================================================== #


def _sign_in_unreachable(kohler: ShowerKohler) -> None:
    kohler.token_queue.append(ClientError("connection reset"))


def _sign_in_rejected(kohler: ShowerKohler) -> None:
    kohler.token_queue.append(REJECTED)


def _account_unreadable(kohler: ShowerKohler) -> None:
    kohler.fail_api(f"/customer-device/{TENANT_ID}", (500, {"message": "oops"}))


def _seed_rejected(kohler: ShowerKohler) -> None:
    # The account read signs in; the seed's first read then needs a new token.
    kohler.token_queue.extend([token_reply(), REJECTED])
    kohler.fail_api(f"/gcsadvancestate/{LEFT}", (401, {"message": "Unauthorized"}))


def _seed_unreachable(kohler: ShowerKohler) -> None:
    kohler.token_queue.extend([token_reply(), ClientError("connection reset")])
    kohler.fail_api(f"/gcsadvancestate/{LEFT}", (401, {"message": "Unauthorized"}))


@pytest.mark.parametrize(
    ("arrange", "expected", "asks"),
    [
        (_sign_in_unreachable, ConfigEntryState.SETUP_RETRY, False),
        (_sign_in_rejected, ConfigEntryState.SETUP_ERROR, True),
        (_account_unreadable, ConfigEntryState.SETUP_RETRY, False),
        (_seed_rejected, ConfigEntryState.SETUP_ERROR, True),
        (_seed_unreachable, ConfigEntryState.SETUP_RETRY, False),
    ],
    ids=lambda value: getattr(value, "__name__", str(value)).strip("_"),
)
async def test_a_failed_setup_retries_an_outage_and_asks_about_a_dead_sign_in(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    arrange: Callable[[ShowerKohler], None],
    expected: ConfigEntryState,
    asks: bool,
) -> None:
    """Kohler unreachable is retried quietly; a rejected credential needs the owner."""
    arrange(kohler)
    assert not await hass.config_entries.async_setup(shower_entry.entry_id)
    await hass.async_block_till_done()
    assert shower_entry.state is expected
    assert (SOURCE_REAUTH in prompts(hass)) is asks
    # Nothing got as far as connecting.
    assert FakeMqttClient.instances == []


async def test_a_setup_that_fails_after_the_stream_started_leaves_nothing_running(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Home Assistant never unloads an entry that failed to load, so setup must unwind.

    Otherwise each retry stacks another paho thread and another set of timers.
    """
    with patch.object(
        KohlerKonnectCoordinator,
        "async_config_entry_first_refresh",
        AsyncMock(side_effect=ConfigEntryNotReady("late failure")),
    ):
        assert not await hass.config_entries.async_setup(shower_entry.entry_id)
    assert shower_entry.state is ConfigEntryState.SETUP_RETRY
    (client,) = FakeMqttClient.instances
    assert client.stopped


async def test_a_stream_refused_for_a_dead_sign_in_still_loads_and_asks(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """State is seeded, so the entry is usable; the credential still needs the owner."""
    kohler.token_queue.extend([token_reply(), REJECTED])
    kohler.fail_api("/mobile/settings", (401, {"message": "Unauthorized"}))
    coordinator = await start(hass, shower_entry)
    assert shower_entry.state is ConfigEntryState.LOADED
    assert prompts(hass) == [SOURCE_REAUTH]
    # And the stream kept trying until it got through.
    assert coordinator.stream.connected


async def test_the_first_setup_registers_one_identity_and_keeps_it(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A fresh identity per start left a dead registration on the account every time."""
    hass.config_entries.async_update_entry(
        shower_entry,
        data={k: v for k, v in SHOWER_DATA.items() if k != CONF_MOBILE_DEVICE_ID},
    )
    coordinator = await start(hass, shower_entry)
    identity = shower_entry.data[CONF_MOBILE_DEVICE_ID]
    assert re.fullmatch(r"[0-9a-f]{16}", identity)
    assert kohler.push_registrations[-1]["mobileDeviceId"] == identity
    # Only a brand-new identity can need provisioning time.
    assert coordinator.stream.warming_up

    assert await hass.config_entries.async_reload(shower_entry.entry_id)
    await hass.async_block_till_done()
    assert shower_entry.data[CONF_MOBILE_DEVICE_ID] == identity
    assert {r["mobileDeviceId"] for r in kohler.push_registrations} == {identity}
    assert not account(hass, shower_entry).stream.warming_up


# =========================================================================== #
# Command failures
# =========================================================================== #


@pytest.mark.parametrize(
    ("error", "message", "asks"),
    [
        (DeviceOffline("offline", {"statusCode": "900"}), "The valve is off", False),
        (AuthUnavailable("token endpoint down"), "Could not reach Kohler", False),
        (AuthError("AADB2C90080: expired"), "rejected the saved sign-in", True),
        (KohlerError("HTTP 500"), "Kohler command failed: HTTP 500", False),
    ],
    ids=["offline", "unreachable", "rejected", "refused"],
)
async def test_a_failed_command_becomes_a_message_the_person_can_act_on(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    error: Exception,
    message: str,
    asks: bool,
) -> None:
    """One mapping for every valve and controller command — see `command_errors`."""
    coordinator = await start(hass, shower_entry)
    with (
        pytest.raises(HomeAssistantError, match=message) as caught,
        coordinator.command_errors("The valve is off."),
    ):
        raise error
    assert caught.value.__cause__ is error
    await hass.async_block_till_done()
    assert (prompts(hass) == [SOURCE_REAUTH]) is asks


async def test_an_offline_controller_is_named_in_the_error(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """With several controllers, "the controller" no longer says which one to check."""
    kohler.add_controller()
    coordinator = await start(hass, shower_entry)
    kohler.fail_api("/hub/stopall", (200, {"statusCode": "900"}))
    with pytest.raises(HomeAssistantError, match=r"^Anthem Plus is offline"):
        await coordinator.async_stop_hub(coordinator.controllers[0])


# =========================================================================== #
# Valve commands
# =========================================================================== #


async def two_zone_valve(hass: HomeAssistant, entry: MockConfigEntry, kohler) -> Valve:
    """A K-28212: zone 1 running outlet 1 at 38.5 °C, zone 2 paused on outlet 3 at 40."""
    kohler.valve_settings[LEFT] = valve_settings((3, 3))
    kohler.valve_states[LEFT] = valve_state(
        valve_zone(1), valve_zone(3, celsius="40", paused=True)
    )
    coordinator = await start(hass, entry)
    return coordinator.valves[0]


def words(body: dict[str, Any]) -> tuple[str, str]:
    model = body["gcsValveControlModel"]
    return model["primaryValve1"], model["secondaryValve1"]


@pytest.mark.parametrize(
    ("zone1", "match"),
    [
        ("0184C8", "expected 8 characters"),
        ("0184C80100", "got 10"),
        ("0184C8ZZ", "zone1_hex: Not an 8-character"),
        # A 10-bit temperature can say 51.1 °C; nothing else writes above 48.8.
        ("01FFC801", "commands 51.1 °C"),
    ],
)
async def test_a_raw_valve_word_is_checked_before_anything_is_sent(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    zone1: str,
    match: str,
) -> None:
    """This input reaches something that opens water valves."""
    coordinator = await start(hass, shower_entry)
    with pytest.raises(HomeAssistantError, match=match):
        await coordinator.valves[0].async_send_valve_hex(zone1)
    assert kohler.posted("/solowritesystem") == []


async def test_the_sixteen_characters_the_hex_sensor_shows_can_be_pasted_in(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The Zone Hex sensor shows the whole status word; its second half is discarded."""
    coordinator = await start(hass, shower_entry)
    result = await coordinator.valves[0].async_send_valve_hex("0184c80100000001")
    assert result == {
        "zone1_hex": "0184C801",
        "zone2_hex": "00000000",
        "decoded": {"zone1": "38.8C, 100% flow, outlets 1", "zone2": "unused / closed"},
    }
    (body,) = kohler.posted("/solowritesystem")
    assert words(body) == ("0184C801", "00000000")
    assert body["deviceId"] == LEFT


@pytest.mark.parametrize("zone2", [None, "00000000", "0000000000000000"])
async def test_leaving_zone_two_out_resends_what_it_is_doing(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    zone2: str | None,
) -> None:
    """The ignore word addresses no valve, and on two valves it voids the whole command."""
    valve = await two_zone_valve(hass, shower_entry, kohler)
    result = await valve.async_send_valve_hex("0184C802", zone2)
    assert result["zone2_hex"] == "1190C844"
    assert result["decoded"]["zone2"] == "40.0C, 100% flow, outlets 3, paused"
    (body,) = kohler.posted("/solowritesystem")
    assert words(body) == ("0184C802", "1190C844")


async def test_an_all_zero_zone_one_word_is_sent_but_warned_about(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The escape hatch allows the experiment; it must never be a silent surprise."""
    coordinator = await start(hass, shower_entry)
    await coordinator.valves[0].async_send_valve_hex("00000000")
    assert "addresses no valve" in caplog.text
    assert len(kohler.posted("/solowritesystem")) == 1


async def test_changing_the_temperature_keeps_the_open_outlets_and_full_flow(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """No partial write exists, so both words are rebuilt from current state.

    Flow is the deliberate exception: the panel's 40 % is not carried into Home
    Assistant's write.
    """
    kohler.valve_states[LEFT] = valve_state(valve_zone(1, 3, flow="20"))
    coordinator = await start(hass, shower_entry)
    await coordinator.valves[0].async_apply_valve(zone1_temperature=104)
    (body,) = kohler.posted("/solowritesystem")
    primary, secondary = words(body)
    word = decode_word(primary)
    assert word.temperature_celsius == 40.0  # Kohler's own table, from 104 °F
    assert word.outlet_mask == 0b101
    assert word.flow_percent == 100.0
    assert secondary == "00000000"


async def test_an_outlet_switch_changes_only_its_own_outlet(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Every write carries the whole state, so one toggle must preserve the rest."""
    kohler.valve_states[LEFT] = valve_state(valve_zone(1, celsius="39.0"))
    await start(hass, shower_entry)
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.anthem_valve_rainhead_2"},
        blocking=True,
    )
    (body,) = kohler.posted("/solowritesystem")
    word = decode_word(words(body)[0])
    assert word.outlet_mask == 0b011
    assert word.temperature_celsius == 39.0


async def test_stop_closes_both_zones_without_pausing_and_keeps_their_setpoints(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Mask 0x00, not the 0x40 pause, and each zone keeps its own temperature."""
    valve = await two_zone_valve(hass, shower_entry, kohler)
    await valve.async_stop_shower()
    (body,) = kohler.posted("/solowritesystem")
    assert words(body) == ("0181C800", "1190C800")


async def test_restart_presets_and_experiences_go_to_the_valve_they_belong_to(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """With two valves, a command for one must not reach the other."""
    kohler.add_valve(RIGHT, "Tub")
    coordinator = await start(hass, shower_entry)
    right = coordinator.valves[1]
    await right.async_restart()
    await right.async_activate_preset(2)
    await right.async_control_experience(20, False)

    (restart,) = kohler.posted("/valvereset")
    assert (restart["deviceId"], restart["reset"]) == (RIGHT, "productRestart")
    assert [
        (b["deviceId"], b["preset"], b["action"])
        for b in kohler.posted("/controlpresetorexperience")
    ] == [(RIGHT, "2", "On"), (RIGHT, "20", "Off")]
    # Each counts against this valve's custom-shower watcher, not the other's.
    assert right._local_write_serial == 3
    assert coordinator.valves[0]._local_write_serial == 0


async def test_a_custom_shower_resumes_once_after_the_warmup_pauses_it(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Left alone, the pause at the end of a warm-up ends the session."""
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    await valve.async_custom_shower("0184C801", "00000000", keep_on_after_warmup=True)
    # The watcher is a tracked task that lives until it decides, so these wait on the
    # condition rather than on `async_block_till_done`, which would wait on it too.
    stream().deliver(solo_message(LEFT, "0184C801", warmUpStatus="warmUpInProgress"))
    await asyncio.sleep(0.05)  # it wakes on the update and sees the warm-up
    stream().deliver(solo_message(LEFT, "0184C841", warmUpStatus="warmUpNotInProgress"))
    await eventually(lambda: valve._custom_shower_task is None, "the watcher to decide")
    assert [words(b) for b in kohler.posted("/solowritesystem")] == [
        ("0184C801", "00000000")
    ] * 2


async def test_a_custom_shower_does_not_resume_over_a_command_sent_since(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Resuming on top of someone else's command would undo it."""
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    await valve.async_custom_shower("0184C801", "00000000", keep_on_after_warmup=True)
    watcher = valve._custom_shower_task
    await valve.async_activate_preset(2)
    await push(
        hass,
        solo_message(LEFT, "0184C801", warmUpStatus="warmUpInProgress"),
        solo_message(LEFT, "0184C841", warmUpStatus="warmUpNotInProgress"),
    )
    await wait_for(hass, watcher.done, "the watcher to give up")
    assert len(kohler.posted("/solowritesystem")) == 1


async def test_a_new_custom_shower_replaces_the_pending_watcher(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Two watchers could both resume, each with different words."""
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    await valve.async_custom_shower("0184C801", "00000000", keep_on_after_warmup=True)
    first = valve._custom_shower_task
    await valve.async_custom_shower("0184C802", "00000000", keep_on_after_warmup=False)
    await hass.async_block_till_done()
    assert first.cancelled()
    assert valve._custom_shower_task is None


# =========================================================================== #
# Controller commands
# =========================================================================== #


async def test_controller_commands_go_to_the_controller_they_name(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Which valve a controller fronts is unknowable, so every valve's watcher hears it."""
    kohler.add_valve(RIGHT, "Tub")
    kohler.add_controller(MAIN, "Main Bath")
    kohler.add_controller(GUEST, "Guest Bath")
    coordinator = await start(hass, shower_entry)
    guest = coordinator.controllers[1]

    await coordinator.async_activate_favorite(guest, 1, "Hair Wash")
    await coordinator.async_set_hub_shower(guest, True)
    await coordinator.async_set_hub_steam(guest, False)
    await coordinator.async_stop_hub(guest)

    (favorite,) = kohler.posted("/hub/favorite/control")
    # Control takes the id as a string.
    assert (favorite["id"], favorite["name"], favorite["state"]) == (
        "1",
        "Hair Wash",
        "ON",
    )
    assert kohler.posted("/hub/valvecontrol")[0]["valveOnOff"] == "ON"
    assert kohler.posted("/hub/steamcontrol")[0]["steamOnOff"] == "OFF"
    assert {
        body["deviceId"] for path, body in kohler.posts if "/commands/" in path
    } == {GUEST}
    assert [v._local_write_serial for v in coordinator.valves] == [4, 4]


async def test_an_experience_is_started_on_the_endpoint_for_its_category(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A shower experience sent to the steam path does nothing, so the title decides."""
    kohler.add_controller()
    coordinator = await start(hass, shower_entry)
    controller = coordinator.controllers[0]
    await coordinator.async_control_hub_experience(controller, "Rain", True)
    await coordinator.async_control_hub_experience(controller, "Eucalyptus", False)
    with pytest.raises(HomeAssistantError, match="No experience called 'Ice'"):
        await coordinator.async_control_hub_experience(controller, "Ice", True)
    assert [p.rsplit("/hub/", 1)[1] for p, _ in kohler.posts if "/commands/" in p] == [
        "shower/experience/control",
        "steam/experience/control",
    ]


# =========================================================================== #
# Writing an outlet setting, and checking it took
# =========================================================================== #


def issue(hass: HomeAssistant, device_id: str = LEFT) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"outlet_write_unverified_{device_issue_key(device_id)}"
    )


async def test_a_write_the_valve_ignored_is_flagged_and_a_later_one_that_lands_clears_it(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    instant_verify: None,
) -> None:
    """A 201 means accepted for delivery, never applied; only a read-back says which."""
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]

    await valve.async_write_outlet_setting(maximum_run_time=2700)
    await hass.async_block_till_done()
    # One call per outlet, each the whole record with only the duration changed.
    bodies = [
        b["gcsOutletConfigControlModel"] for b in kohler.posted("/writeoutletconfig")
    ]
    assert [(b["outLetId"], b["maximumRuntime"]) for b in bodies] == [
        ("0", "2700"),
        ("1", "2700"),
        ("2", "2700"),
    ]
    assert {b["maximumOutletTemperature"] for b in bodies} == {"477"}
    flagged = issue(hass)
    assert flagged is not None
    assert flagged.translation_placeholders["setting"] == (
        "Max Shower Duration (45 minutes)"
    )
    assert (
        "old value on outlet(s) 1, 2, 3" in flagged.translation_placeholders["detail"]
    )

    kohler.valve_settings[LEFT] = valve_settings(run_time=2700)
    await valve.async_write_outlet_setting(maximum_run_time=2700)
    await hass.async_block_till_done()
    assert issue(hass) is None
    assert valve.outlet_run_times == {1: 2700, 2: 2700, 3: 2700}


@pytest.mark.parametrize(
    ("setting", "label"),
    [
        ({"maximum_run_time": 3600}, "Max Shower Duration (60 minutes)"),
        ({"maximum_temperature_tenths": 450}, "Max Temperature (45.0 °C)"),
        ({"default_temperature_tenths": 400}, "Default Temperature (40.0 °C)"),
    ],
)
async def test_a_write_that_cannot_be_read_back_is_flagged_naming_the_setting(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    instant_verify: None,
    setting: dict[str, int],
    label: str,
) -> None:
    """Unverified is not verified; the repair says which setting to check."""
    coordinator = await start(hass, shower_entry)
    kohler.fail_api(f"/gcsadvancestate/{LEFT}", (500, {"message": "oops"}))
    await coordinator.valves[0].async_write_outlet_setting(**setting)
    await hass.async_block_till_done()
    flagged = issue(hass)
    assert flagged.translation_placeholders["setting"] == label
    assert flagged.translation_placeholders["detail"].startswith(
        "Reading the value back from Anthem Valve failed"
    )


async def test_a_read_back_rejected_by_kohler_flags_the_write_and_asks_to_sign_in(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    instant_verify: None,
) -> None:
    """Detached, so the prompt is the only way the dead credential surfaces."""
    coordinator = await start(hass, shower_entry)
    kohler.fail_api(f"/gcsadvancestate/{LEFT}", (401, {"message": "Unauthorized"}))
    kohler.token_queue.append(REJECTED)
    await coordinator.valves[0].async_write_outlet_setting(maximum_run_time=2700)
    await hass.async_block_till_done()
    assert issue(hass) is not None
    assert prompts(hass) == [SOURCE_REAUTH]


async def test_a_read_back_without_outlet_records_is_flagged_not_trusted(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    instant_verify: None,
) -> None:
    """Nothing to compare against is not the same as the values matching."""
    coordinator = await start(hass, shower_entry)
    kohler.valve_settings[LEFT] = valve_settings(records=False)
    await coordinator.valves[0].async_write_outlet_setting(maximum_run_time=2700)
    await hass.async_block_till_done()
    assert (
        "reported no outlet configuration"
        in (issue(hass).translation_placeholders["detail"])
    )


async def test_nothing_is_written_before_the_valve_has_reported_its_outlets(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The endpoint replaces the whole record; there is nothing yet to write back."""
    kohler.valve_settings[LEFT] = valve_settings(records=False)
    coordinator = await start(hass, shower_entry)
    with pytest.raises(HomeAssistantError, match="not reported its outlet config"):
        await coordinator.valves[0].async_write_outlet_setting(maximum_run_time=2700)
    assert kohler.posted("/writeoutletconfig") == []


# =========================================================================== #
# Firmware
# =========================================================================== #


async def test_firmware_is_read_for_every_valve_gateway_and_controller(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A part Kohler cannot answer for leaves only its own entity unknown."""
    kohler.add_controller()
    kohler.firmware_replies[("gcs", LEFT)] = firmware("10", "11")
    kohler.firmware_replies[("hub", MAIN)] = firmware("2.88")
    coordinator = await start(hass, shower_entry)
    valve, controller = coordinator.valves[0], coordinator.controllers[0]
    assert valve.firmware_info == {"gcs": firmware("10", "11"), "gateway": {}}
    assert controller.firmware_info == firmware("2.88")
    assert state(hass, "update.anthem_valve_firmware_status") == "on"
    assert state(hass, "update.anthem_plus_firmware_status") == "off"
    assert state(hass, "update.anthem_valve_gateway_firmware_status") == "unknown"


async def test_firmware_is_checked_again_twice_a_day(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
) -> None:
    """Nothing announces a release; this is the one clock in a push-only design."""
    kohler.firmware_replies[("gcs", LEFT)] = firmware("10")
    coordinator = await start(hass, shower_entry)
    assert state(hass, "update.anthem_valve_firmware_status") == "off"

    kohler.firmware_replies[("gcs", LEFT)] = firmware("10", "11")
    freezer.tick(FIRMWARE_CHECK_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    # The interval runs the check as a background task.
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.valves[0].firmware_info["gcs"] == firmware("10", "11")
    assert state(hass, "update.anthem_valve_firmware_status") == "on"


async def test_a_firmware_check_rejected_by_kohler_asks_to_sign_in_again(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Nobody waits on a timer, but a dead sign-in must still reach someone."""
    kohler.firmware_replies[("gcs", LEFT)] = firmware("10")
    coordinator = await start(hass, shower_entry)
    coordinator.auth.invalidate_access_token()
    kohler.token_queue.append(REJECTED)
    await coordinator.async_refresh_firmware()
    await hass.async_block_till_done()
    assert prompts(hass) == [SOURCE_REAUTH]
    assert coordinator.valves[0].firmware_info["gcs"] == firmware("10")


# =========================================================================== #
# Preset 1's hidden timer
# =========================================================================== #


async def test_the_hidden_default_preset_timer_is_rewritten_once_at_setup(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Everything but `time` goes back exactly as read; `writepreset` replaces the record."""
    kohler.valve_presets[LEFT] = {
        "gcsPresetExperienceDetails": [
            {
                "presetId": "1",
                "title": "Default Shower",
                "time": "1800",
                "volume": "0",
                "valveDetails": [{"valveIndex": "Valve1", "hexString": "018448"}],
            }
        ]
    }
    coordinator = await start(hass, shower_entry)
    (body,) = kohler.posted("/writepreset")
    model = body["gcsPresetControlModel"]
    assert model["presetId"] == "1"
    assert model["name"] == "Default Shower"
    assert model["time"] == str(DEFAULT_PRESET_TIMER_SECONDS)
    assert "018448" in model.values()

    # A reconnect reseeds the presets but never rewrites from that payload.
    await reconnect(hass, coordinator)
    assert len(kohler.posted("/writepreset")) == 1


async def test_a_failed_preset_timer_rewrite_does_not_fail_setup(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A convenience, not a prerequisite: the shower works with the timer as it was."""
    kohler.valve_presets[LEFT]["gcsPresetExperienceDetails"][0]["time"] = "1800"
    kohler.fail_api("/writepreset", (500, {"message": "Something went wrong"}))
    await start(hass, shower_entry)
    assert shower_entry.state is ConfigEntryState.LOADED


# =========================================================================== #
# Reloads, options and the report log
# =========================================================================== #


@pytest.mark.parametrize(
    ("change", "reloads"),
    [
        ({"data": {CONF_REFRESH_TOKEN: "rotated"}}, False),
        ({"data": {CONF_MOBILE_DEVICE_ID: "another"}}, False),
        ({"options": {CONF_VALVES: {LEFT: {"warmup_auto_restore": True}}}}, False),
        ({"options": {CONF_REPORT_LOG_FILE: "report-1.jsonl"}}, False),
        ({"options": {CONF_ZONE_GROUPING: "outlet_labels"}}, True),
        ({"data": {CONF_TEMPERATURE_UNIT: "Celsius"}}, True),
        ({"data": {"added_by_a_later_release": 1}}, True),
    ],
    ids=[
        "token rotation",
        "identity",
        "per-valve switch",
        "report log",
        "zone grouping",
        "temperature unit",
        "unknown key",
    ],
)
async def test_only_a_real_configuration_change_reloads_the_entry(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    change: dict[str, dict[str, Any]],
    reloads: bool,
) -> None:
    """Reloading on bookkeeping flaps every entity and drops the stream with its warm-up."""
    coordinator = await start(hass, shower_entry)
    hass.config_entries.async_update_entry(
        shower_entry,
        **{
            part: {**getattr(shower_entry, part), **values}
            for part, values in change.items()
        },
    )
    await hass.async_block_till_done()
    assert (account(hass, shower_entry) is not coordinator) is reloads


async def test_options_for_a_shower_account_offer_grouping_and_keep_per_valve_settings(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The options hold the per-valve switches too; saving the dialog must not drop them."""
    hass.config_entries.async_update_entry(
        shower_entry, options={CONF_VALVES: {LEFT: {"warmup_auto_restore": True}}}
    )
    await start(hass, shower_entry)
    result = await hass.config_entries.options.async_init(shower_entry.entry_id)
    assert [str(key) for key in result["data_schema"].schema] == [CONF_ZONE_GROUPING]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ZONE_GROUPING: "subdevices"}
    )
    await hass.async_block_till_done()
    assert shower_entry.options == {
        CONF_VALVES: {LEFT: {"warmup_auto_restore": True}},
        CONF_ZONE_GROUPING: "subdevices",
    }


async def test_the_report_log_episode_is_kept_in_the_options_without_a_reload(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The capture must survive a restart, and turning it on must not drop the stream."""

    class Log:
        started = stopped = 0
        path = "/config/custom_components/kohler_konnect/reports/report-1.jsonl"

        def start(self) -> str:
            self.started += 1
            return "report-1.jsonl"

        def stop(self) -> None:
            self.stopped += 1

    coordinator = await start(hass, shower_entry)
    coordinator.report_log = log = Log()
    await coordinator.async_start_report_log()
    await coordinator.async_start_report_log()  # already on: must not split the file
    await hass.async_block_till_done()
    assert log.started == 1
    assert shower_entry.options[CONF_REPORT_LOG_FILE] == "report-1.jsonl"
    assert coordinator.report_log_active
    assert state(hass, "switch.anthem_valve_report_log") == "on"
    assert account(hass, shower_entry) is coordinator

    await coordinator.async_stop_report_log()
    await hass.async_block_till_done()
    assert log.stopped == 1
    assert CONF_REPORT_LOG_FILE not in shower_entry.options
    assert account(hass, shower_entry) is coordinator


async def test_zone_subdevices_hang_off_the_valve_and_their_entities_survive_removal(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Deleting a zone device deletes every entity still on it — disabled ones included.

    A disabled entity is never re-added by its platform, so it never moves back to the
    valve on its own; the cleanup has to move it first.
    """
    kohler.valve_settings[LEFT] = valve_settings((3, 3))
    hass.config_entries.async_update_entry(
        shower_entry,
        data={**SHOWER_DATA, CONF_VALVE_MODEL: "K-28212", CONF_ZONE_OUTLETS: [3, 3]},
        options={CONF_ZONE_GROUPING: "subdevices"},
    )
    await start(hass, shower_entry)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    valve = registered_device(hass, LEFT)
    zones = {
        identifier
        for device in dr.async_entries_for_config_entry(devices, shower_entry.entry_id)
        if device.via_device_id == valve.id
        for _, identifier in device.identifiers
    }
    assert zones == {f"{LEFT}_zone_1", f"{LEFT}_zone_2"}
    hex_id = entities.async_get_entity_id("sensor", DOMAIN, f"{LEFT}_zone_2_hex")
    assert entities.async_get(hex_id).disabled_by is not None

    hass.config_entries.async_update_entry(
        shower_entry, options={CONF_ZONE_GROUPING: "numbered"}
    )
    await hass.async_block_till_done()
    left = dr.async_entries_for_config_entry(devices, shower_entry.entry_id)
    assert [d for d in left if d.via_device_id == valve.id] == []
    assert entities.async_get(hex_id).device_id == valve.id


async def test_removing_the_entry_clears_its_repairs_but_not_other_integrations(
    hass: HomeAssistant, shower_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Repairs are stored apart from the entry, so they would outlive it."""
    await start(hass, shower_entry)
    for domain in (DOMAIN, "another_integration"):
        ir.async_create_issue(
            hass,
            domain,
            f"outlet_write_unverified_{device_issue_key(LEFT)}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="outlet_write_unverified",
        )
    await hass.config_entries.async_remove(shower_entry.entry_id)
    await hass.async_block_till_done()
    issues = ir.async_get(hass)
    assert (
        issues.async_get_issue(
            DOMAIN, f"outlet_write_unverified_{device_issue_key(LEFT)}"
        )
        is None
    )
    assert issues.async_get_issue(
        "another_integration", f"outlet_write_unverified_{device_issue_key(LEFT)}"
    )


# =========================================================================== #
# The setup flow, for showers
# =========================================================================== #


PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def signed_in() -> Generator[None]:
    """The server-side B2C sign-in, which drives Kohler's web pages, stood in for."""

    async def _sign_in(self: KohlerAuth, username: str, password: str) -> TokenSet:
        self._tokens = TokenSet(
            access_token=make_jwt({"oid": TENANT_ID}),
            refresh_token="refresh-signed-in",
            expires_at=9_999_999_999.0,
        )
        self._refresh_token = "refresh-signed-in"
        return self._tokens

    with patch.object(KohlerAuth, "async_sign_in", _sign_in):
        yield


async def _add_account(hass: HomeAssistant) -> Any:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (SignInBlocked("Kohler wants a verification code"), "signin_blocked"),
        (AuthUnavailable("token endpoint down"), "cannot_connect"),
    ],
)
async def test_a_sign_in_that_cannot_complete_says_why(
    hass: HomeAssistant, kohler: ShowerKohler, failure: Exception, error: str
) -> None:
    """A lockout needs the owner at Kohler's site; an outage needs only a retry."""
    with patch.object(KohlerAuth, "async_sign_in", AsyncMock(side_effect=failure)):
        result = await _add_account(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_an_account_kohler_cannot_list_cannot_be_added(
    hass: HomeAssistant, kohler: ShowerKohler, signed_in: None
) -> None:
    """Without the device list there is nothing to set up."""
    kohler.fail_api(f"/customer-device/{TENANT_ID}", (500, {"message": "oops"}))
    result = await _add_account(hass)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_controller_only_account_takes_the_layout_of_the_first_that_answers(
    hass: HomeAssistant, kohler: ShowerKohler, signed_in: None
) -> None:
    """No valve to ask, so the controllers' zone configuration decides — no question."""
    kohler.devices = [hub_device(MAIN), hub_device(GUEST, "Guest Bath")]
    # The first controller's read fails (404 here); the second answers.
    kohler.hub_configurations[GUEST] = hub_configuration((3, 3))
    with patch("custom_components.kohler_konnect.async_setup_entry", return_value=True):
        result = await _add_account(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_VALVE_MODEL] == "K-28212"
    assert result["data"][CONF_ZONE_OUTLETS] == [3, 3]


async def test_an_empty_controller_configuration_asks_for_the_model_in_the_flow(
    hass: HomeAssistant, kohler: ShowerKohler, signed_in: None
) -> None:
    """Failed detection is what the model question exists for; it must not end the flow."""
    kohler.devices = [hub_device(MAIN)]
    kohler.hub_configurations[MAIN] = ""  # HTTP 200 with an empty body
    result = await _add_account(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "valve"


async def test_signing_in_again_reloads_with_the_new_token(
    hass: HomeAssistant,
    shower_entry: MockConfigEntry,
    kohler: ShowerKohler,
    signed_in: None,
) -> None:
    """The update listener ignores the token, so the reauth step must reload itself.

    Otherwise the running client keeps the dead token that caused the prompt.
    """
    coordinator = await start(hass, shower_entry)
    result = await shower_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert account(hass, shower_entry) is not coordinator
    # The detected layout was confirmed rather than asked for again.
    assert shower_entry.data[CONF_ZONE_OUTLETS] == [3, 0]
    # Only the reloaded client can have redeemed the token the sign-in produced.
    assert "refresh-signed-in" in [r["refresh_token"] for r in kohler.token_requests]
