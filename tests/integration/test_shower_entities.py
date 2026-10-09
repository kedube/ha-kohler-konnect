"""The Anthem valve's entities and actions, run inside a real Home Assistant.

What each entity reports from the valve's own state, what each command sends — and that it
sends nothing when it should refuse — as `docs/user_guide.md` (Entities and Services)
describes them. ``ShowerKohler`` extends the shared ``FakeKohler`` with the valve's REST
surface (``gcs-state``, ``gcs-preset``, ``gcsadvancestate``, the firmware reads) and records
every command by its full path; valve and controller reports arrive over the fake MQTT
client, as they do for real.

`test_controller_entities.py` reuses the harness for the Anthem Plus controller.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.number import SERVICE_SET_VALUE
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.select import SERVICE_SELECT_OPTION
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.update import ATTR_IN_PROGRESS
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.service import SERVICE_DESCRIPTION_CACHE
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect.const import (
    CONF_VALVE_MODEL,
    CONF_VALVES,
    CONF_WARMUP_AUTO_RESTORE,
    CONF_ZONE_GROUPING,
    DOMAIN,
    device_issue_key,
)
from custom_components.kohler_konnect.konnect.valve_hex import (
    AT_TEMP_BIT,
    VALVE_ERROR_FLAG,
    ValveWord,
    decode_word,
    encode_word,
)

from .conftest import (
    VALVE,
    VALVE_ID,
    FakeKohler,
    FakeMqttClient,
    account,
    registered_device,
    wait_for,
)

HUB_ID = "hub-control0001"
HUB = {"deviceId": HUB_ID, "sku": "HUB", "logicalName": "Bathroom"}
GUEST_ID = "gcs-guest00002"
GUEST = {"deviceId": GUEST_ID, "sku": "GCS", "logicalName": "Guest"}

SHOWER_ON = "switch.anthem_valve_shower_on"
RAINHEAD = "switch.anthem_valve_rainhead"
SHOWERHEAD = "switch.anthem_valve_showerhead"
HANDSHOWER = "switch.anthem_valve_handshower"
FAVORITE = "select.anthem_valve_favorite"
EXPERIENCE = "select.anthem_valve_experience"
WARMUP = "select.anthem_valve_warmup"
DURATION = "select.anthem_valve_max_shower_duration"
TEMPERATURE = "number.anthem_valve_temperature"
FLOW = "number.anthem_valve_flow"
MAX_TEMPERATURE = "number.anthem_valve_max_temperature"
DEFAULT_TEMPERATURE = "number.anthem_valve_default_temperature"
STATUS = "sensor.anthem_valve_system_status"
SYSTEM_STATE = "sensor.anthem_valve_system_state"

# The valve's command endpoints, as `ShowerKohler.posts` records them.
SOLO = "gcs/solowritesystem"
CONTROL_PRESET = "gcs/controlpresetorexperience"
WRITE_OUTLET = "gcs/writeoutletconfig"
SET_WARMUP = "gcs/warmup"

# Shaped like the "gcsPresetExperienceDetails" a real valve returns: the hidden default
# shower in slot 1, two favorites, a free slot, and an experience the app added.
PRESETS = [
    {"presetId": "1", "title": "Default", "time": "3600", "isExperience": "False"},
    {"presetId": "2", "title": "Morning", "isExperience": "False"},
    {"presetId": "3", "title": "", "isExperience": "False"},
    {"presetId": "4", "title": "Evening", "isExperience": "False"},
    {"presetId": "17", "title": "Wake Up", "isExperience": "True"},
]


def outlet_record(outlet_id: int, outlet_type: int | None, **fields: str) -> dict:
    """One `outletConfigurations` entry as `gcsadvancestate` reports it, in DISPLAY units."""
    record = {
        "outLetId": str(outlet_id),
        "minimumFlowrate": "4",  # 16 on the wire: 8 %
        "maximumFlowrate": "50",  # 200 on the wire: 100 %
        "defaultFlowrate": "50",
        "maximumRuntime": "1800",
        "maximumOutletTemperature": "47.7",  # 118 °F, the owner's scald limit
        "minimumOutletTemperature": "15",  # 59 °F
        "defaultOutletTemperature": "38.8",  # 102 °F
        "outLetFlags": "1",
    }
    if outlet_type is not None:
        record["outLetType"] = str(outlet_type)
    record.update(fields)
    return record


class ShowerKohler(FakeKohler):
    """`FakeKohler` with an Anthem valve's — and an Anthem Plus controller's — REST surface.

    The account holds one valve, a K-28210 with a rainhead, a showerhead and a handshower,
    and no faucet; tests add a second zone, a controller or a second valve. Every command is
    kept in `posts` under its path below ``/commands/``, refused ones included, since a
    command Kohler refused was still sent.
    """

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.devices = [dict(VALVE)]
        # Type code per hardware `outLetId` — zone 1 is ids 0-2, zone 2 ids 3-5.
        self.outlet_types: dict[int, int | None] = {0: 31, 1: 11, 2: 1}
        # Per-outlet overrides of `outlet_record`'s fields, by `outLetId`.
        self.outlet_fields: dict[int, dict[str, str]] = {}
        # Whether `writeoutletconfig` lands, as the read-back then shows.
        self.apply_outlet_writes = True
        # Whether `warmup` lands, as the `gcs-state` read-back then shows.
        self.apply_warmup = True
        self.gcs: dict[str, Any] = {
            "valve1": self.idle_valve(),
            "valve2": self.idle_valve(),
            "warmUpState": {"warmUp": "warmUpDisabled", "state": "warmUpNotInProgress"},
            "currentSystemState": "normalOperation",
            "presetOrExperienceId": "0",
        }
        self.gcs_presets: list[dict[str, Any]] = copy.deepcopy(PRESETS[:1])
        # `firmware/gcs`, `firmware/gcs/gateway` and `firmware/hub` replies; absent: 404.
        self.firmware_parts: dict[str, dict[str, Any]] = {}
        # The controller's reads. None answers 404.
        self.hub_configuration: dict[str, Any] | None = None
        self.hub_state: dict[str, Any] = {"state": {}}
        self.hub_favorites: list[dict[str, Any]] | None = None
        self.hub_experiences: dict[str, Any] = {}
        self.hub_errors: list[dict[str, Any]] = []
        # The valve's `gcs-configuration` record; None answers 404.
        self.gcs_configuration: dict[str, Any] | None = None
        # The Konnect account's temperature unit, as the customer read reports it.
        self.temperature_unit = "Fahrenheit"
        self.posts: list[tuple[str, dict[str, Any]]] = []

    @staticmethod
    def idle_valve(**fields: Any) -> dict[str, Any]:
        """One zone of `gcs-state`: every outlet closed, 38.8 °C, full flow."""
        return {
            "out1": "0",
            "out2": "0",
            "out3": "0",
            "temperatureSetpoint": 38.8,
            "flowSetpoint": 50,
            "pauseFlag": "0",
            **fields,
        }

    def sent(self, endpoint: str) -> list[dict[str, Any]]:
        """The bodies of every command sent to one endpoint, oldest first."""
        return [body for path, body in self.posts if path == endpoint]

    def words(self) -> list[tuple[ValveWord, ValveWord | None]]:
        """Every `solowritesystem` pair, decoded; None for the unused-valve sentinel."""
        pairs = []
        for body in self.sent(SOLO):
            model = body["gcsValveControlModel"]
            second = model["secondaryValve1"]
            pairs.append(
                (
                    decode_word(model["primaryValve1"]),
                    None if second == "00000000" else decode_word(second),
                )
            )
        return pairs

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        path = url.path
        if method.lower() == "post" and "/commands/" in path:
            endpoint = path.split("/commands/", 1)[1]
            self.posts.append((endpoint, data))
            if not any(path.endswith(s) and q for s, q in self.api_queue.items()):
                self._apply(endpoint, data)
        for suffix, queue in self.api_queue.items():
            if path.endswith(suffix) and queue:
                return self._respond(method, url, queue.pop(0))
        if method.lower() == "get":
            reply = self._read(path)
            if reply is not None:
                return self._respond(method, url, reply)
        return await super()._api(method, url, data)

    def _apply(self, endpoint: str, body: dict[str, Any]) -> None:
        """What the valve does with a command it accepts, as its next read shows."""
        if endpoint == SET_WARMUP and self.apply_warmup:
            self.gcs["warmUpState"]["warmUp"] = body["warmUp"]
        if endpoint == WRITE_OUTLET and self.apply_outlet_writes:
            record = body["gcsOutletConfigControlModel"]
            fields = self.outlet_fields.setdefault(int(record["outLetId"]), {})
            fields["maximumRuntime"] = record["maximumRuntime"]
            # Wire tenths of °C back to the display °C `gcsadvancestate` reports.
            for key in ("maximumOutletTemperature", "defaultOutletTemperature"):
                fields[key] = str(int(record[key]) / 10)

    def _read(self, path: str) -> tuple[int, Any] | None:
        if "/customer-device/" in path:
            home = {"address": "1 Main St", "devices": self.devices}
            return 200, {
                "temperatureUnit": self.temperature_unit,
                "waterUnits": self.water_units,
                "customerHome": [home],
            }
        if "/gcs-state/gcsadvancestate/" in path:
            if self.valve_outlets is None:
                return None
            return 200, {"setting": self.settings()}
        if "/gcs-state/" in path:
            return 200, {"state": copy.deepcopy(self.gcs)}
        if "/gcs-configuration/" in path and not path.endswith("/about"):
            if self.gcs_configuration is None:
                return None
            return 200, copy.deepcopy(self.gcs_configuration)
        if "/gcs-preset/" in path:
            return 200, {"gcsPresetExperienceDetails": copy.deepcopy(self.gcs_presets)}
        for part, marker in (
            ("gateway", "/firmware/gcs/gateway/"),
            ("gcs", "/firmware/gcs/"),
            ("hub", "/firmware/hub/"),
        ):
            if marker in path:
                info = self.firmware_parts.get(part)
                return None if info is None else (200, info)
        if "/hub-diagnostics/" in path:
            return 200, {"errorDetails": self.hub_errors}
        if "/hub-configuration/" in path and self.hub_configuration is not None:
            return 200, {"configuration": self.hub_configuration}
        if "/hub-state/" in path:
            return 200, copy.deepcopy(self.hub_state)
        if path.endswith("/favorites") and self.hub_favorites is not None:
            return 200, {"favorites": copy.deepcopy(self.hub_favorites)}
        if path.endswith("/experiences"):
            return 200, {"experiences": self.hub_experiences}
        return None

    def settings(self) -> dict[str, Any]:
        """The `setting` block of `gcsadvancestate`: the outlet split and every record."""
        first, second = self.valve_outlets
        valves = []
        for index, count in ((1, first), (2, second)):
            ids = [(index - 1) * 3 + n for n in range(count)]
            valves.append(
                {
                    "valve": f"valve{index}",
                    "noOfOutlets": count,
                    "outletConfigurations": [
                        outlet_record(
                            i, self.outlet_types.get(i), **self.outlet_fields.get(i, {})
                        )
                        for i in ids
                    ],
                }
            )
        return {"valveSettings": valves}


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> ShowerKohler:
    return ShowerKohler(aioclient_mock)


@pytest.fixture(autouse=True)
def us_units(hass: HomeAssistant) -> None:
    """Show temperatures in the account's own unit, so states read as the app does."""
    hass.config.units = US_CUSTOMARY_SYSTEM


@pytest.fixture(autouse=True)
def quick_usage_reads() -> Generator[None]:
    """Re-read water usage at once when a shower ends, rather than 90 s later."""
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
def quick_writes() -> Generator[None]:
    """Verify outlet writes and warm-up changes without the real-time waits."""
    with (
        patch(
            "custom_components.kohler_konnect.coordinator."
            "OUTLET_WRITE_VERIFY_DELAY_SECONDS",
            0,
        ),
        patch(
            "custom_components.kohler_konnect.warmup_manager.WARMUP_READBACK_DELAYS",
            (0.0,),
        ),
    ):
        yield


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set the account up and let the stream connect and its re-seed settle."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(hass, lambda: account(hass, entry).stream.connected, "the stream")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()


def enable(
    hass: HomeAssistant, entry: MockConfigEntry, domain: str, key: str, object_id: str
) -> str:
    """Pre-register an entity that starts disabled, so it is created enabled."""
    entity = er.async_get(hass).async_get_or_create(
        domain,
        DOMAIN,
        key,
        config_entry=entry,
        suggested_object_id=object_id,
    )
    return entity.entity_id


async def call(
    hass: HomeAssistant, domain: str, service: str, entity_id: str, **data: Any
) -> None:
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


async def choose(hass: HomeAssistant, entity_id: str, option: str) -> None:
    await call(hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, entity_id, option=option)


async def set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await call(hass, NUMBER_DOMAIN, SERVICE_SET_VALUE, entity_id, value=value)


def word(
    mask: int = 0,
    *,
    zone: int = 1,
    celsius: float = 38.8,
    flow: float = 100.0,
    paused: bool = False,
    at_temperature: bool = False,
    fault: bool = False,
) -> str:
    """A 16-character status word as the valve reports it: lowercase, feedback appended."""
    text = encode_word(0x01 if zone == 1 else 0x11, celsius, flow, mask, paused=paused)
    byte0 = int(text[0:2], 16) | (AT_TEMP_BIT if at_temperature else 0)
    byte3 = int(text[6:8], 16) | (VALVE_ERROR_FLAG if fault else 0)
    return f"{byte0:02x}{text[2:6].lower()}{byte3:02x}00000001"


async def report(
    hass: HomeAssistant,
    code: str,
    *attributes: dict[str, Any],
    device_id: str = VALVE_ID,
    sku: str = "GCS",
    **data: Any,
) -> None:
    """Deliver one message from a device over the (fake) MQTT stream."""
    payload = {
        "sku": sku,
        "deviceid": device_id,
        "data": {"code": code, "attributes": list(attributes), **data},
    }
    FakeMqttClient.instances[-1].deliver(payload)
    await hass.async_block_till_done()


async def solo(
    hass: HomeAssistant,
    zone1: str,
    zone2: str | None = None,
    *,
    preset: int = 0,
    **fields: Any,
) -> None:
    """A `GCS_SOLO_STS` — the valve reporting both zones' words."""
    attribute = {
        "code": "GCS_SOLO_STS",
        "primaryValve1": zone1,
        "secondaryValve1": zone2 or "0000000000000000",
        "presetOrExperienceId": str(preset),
        **fields,
    }
    await report(hass, "GCS_SOLO_STS", attribute)


def state(hass: HomeAssistant, entity_id: str) -> Any:
    current = hass.states.get(entity_id)
    assert current is not None, entity_id
    return current


def ids_by_unique_id(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    return {
        e.unique_id: e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


# --------------------------------------------------------------------------- #
# Shower on
# --------------------------------------------------------------------------- #
async def test_shower_on_runs_the_valves_hidden_default_shower(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """On names preset 1, so the outlets and temperature come from the valve, not here."""
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWER_ON)

    assert [(b["preset"], b["action"]) for b in kohler.sent(CONTROL_PRESET)] == [
        ("1", "On")
    ]
    # One call, no valve write: the valve runs the preset itself.
    assert kohler.sent(SOLO) == []
    # Shown at once, before the valve has said anything.
    assert state(hass, SHOWER_ON).state == STATE_ON


async def test_shower_on_follows_the_valve_once_it_reports(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The switch is the valve's report, whoever started the shower."""
    await setup(hass, config_entry)
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWER_ON)

    # The valve's report wins over what was asked for.
    await solo(hass, word(0))
    assert state(hass, SHOWER_ON).state == STATE_OFF

    # A shower started at the panel shows up too.
    await solo(hass, word(0b010))
    assert state(hass, SHOWER_ON).state == STATE_ON
    assert state(hass, SHOWER_ON).attributes["paused"] is False


async def test_a_paused_shower_reads_off_and_says_it_is_paused(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)
    await solo(hass, word(0b001, paused=True))

    shower = state(hass, SHOWER_ON)
    assert shower.state == STATE_OFF
    assert shower.attributes["paused"] is True


async def test_shower_off_closes_both_zones_and_keeps_each_zones_temperature(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Mask 0x00 on both zones — a stop, never the 0x40 pause the run-time cutoff uses."""
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)
    await solo(hass, word(0b011, celsius=40.0), word(0b100, zone=2, celsius=36.5))

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, SHOWER_ON)

    [(zone1, zone2)] = kohler.words()
    assert (zone1.outlet_mask, zone1.paused, zone1.temperature_celsius) == (
        0,
        False,
        40.0,
    )
    assert zone2 is not None
    assert (zone2.outlet_mask, zone2.paused, zone2.temperature_celsius) == (
        0,
        False,
        36.5,
    )
    assert state(hass, SHOWER_ON).state == STATE_OFF


async def test_a_refused_shower_command_puts_the_switch_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A state the valve never reached must not stay on the dashboard."""
    await setup(hass, config_entry)
    kohler.fail_api("/gcs/controlpresetorexperience", (200, {"statusCode": "900"}))

    with pytest.raises(HomeAssistantError, match="valve is offline"):
        await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWER_ON)
    assert state(hass, SHOWER_ON).state == STATE_OFF


# --------------------------------------------------------------------------- #
# Outlet switches
# --------------------------------------------------------------------------- #
async def test_an_outlet_switch_opens_its_outlet_and_keeps_the_others_open(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The valve takes no partial write: the whole word goes, with this bit added."""
    await setup(hass, config_entry)
    await solo(hass, word(0b010, celsius=40.0))  # the showerhead is running

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)

    [(zone1, zone2)] = kohler.words()
    assert zone1.outlet_mask == 0b011
    assert zone1.temperature_celsius == 40.0
    # A single-zone valve sends the unused-valve word for the zone it lacks.
    assert zone2 is None
    assert state(hass, RAINHEAD).state == STATE_ON


async def test_an_outlet_switch_closing_clears_only_its_own_outlet(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)
    await solo(hass, word(0b111))

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, SHOWERHEAD)

    [(zone1, _)] = kohler.words()
    assert zone1.outlet_mask == 0b101
    assert state(hass, SHOWERHEAD).state == STATE_OFF
    assert state(hass, RAINHEAD).state == STATE_ON


async def test_an_outlet_switch_keeps_the_flow_the_flow_number_holds(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Otherwise every outlet toggle would silently restore full flow."""
    await setup(hass, config_entry)
    await set_number(hass, FLOW, 50)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, HANDSHOWER)

    flows = [zone1.flow_percent for zone1, _ in kohler.words()]
    assert flows == [50.0, 50.0]
    assert kohler.words()[-1][0].outlet_mask == 0b100


async def test_a_refused_outlet_command_puts_the_switch_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)
    kohler.fail_api("/gcs/solowritesystem", (500, {"message": "oops"}))

    with pytest.raises(HomeAssistantError, match="Kohler command failed"):
        await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    assert state(hass, RAINHEAD).state == STATE_OFF


async def test_an_outlet_switch_holds_its_new_position_until_the_valve_reports(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The round trip is 1-2 s on real hardware; the toggle must not sit stuck for it."""
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    assert state(hass, RAINHEAD).state == STATE_ON

    # Then the valve's own word decides, whatever was asked.
    await solo(hass, word(0b010))
    assert state(hass, RAINHEAD).state == STATE_OFF
    assert state(hass, SHOWERHEAD).state == STATE_ON


async def test_another_devices_message_does_not_undo_a_pending_outlet_toggle(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The valve has not answered yet, so there is nothing to correct the toggle with."""
    kohler.devices.append(dict(HUB))
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, RAINHEAD)
    await report(
        hass,
        "MUSIC_STS",
        {"component": "amplifier", "status": "ON"},
        device_id=HUB_ID,
        sku="HUB",
    )

    assert state(hass, RAINHEAD).state == STATE_ON


async def test_a_paused_outlet_reads_off_but_still_assigned(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Not running is not the same as not selected: a pause resumes to the assignment."""
    await setup(hass, config_entry)
    await solo(hass, word(0b001, paused=True))

    rainhead = state(hass, RAINHEAD)
    assert rainhead.state == STATE_OFF
    assert rainhead.attributes["assigned"] is True
    assert state(hass, SHOWERHEAD).attributes["assigned"] is False


async def test_outlet_switches_carry_the_valves_own_type_code(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)

    rainhead = state(hass, RAINHEAD).attributes
    assert (
        rainhead["outlet_type"],
        rainhead["outlet_type_name"],
        rainhead["outlet_variant"],
    ) == (31, "Rainhead", "Katalyst")
    handshower = state(hass, HANDSHOWER).attributes
    assert (handshower["outlet_type"], handshower["outlet_type_name"]) == (
        1,
        "Handshower",
    )
    # A handshower has no variants, so none is invented.
    assert "outlet_variant" not in handshower


async def test_an_outlet_the_valve_has_not_typed_is_named_by_its_position(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Naming it after a code nobody confirmed would be inventing a fixture."""
    kohler.outlet_types = {0: 31, 1: None, 2: 999}
    await setup(hass, config_entry)

    names = {
        state(hass, entity_id).name
        for unique_id, entity_id in ids_by_unique_id(hass, config_entry).items()
        if "_outlet_" in unique_id
    }
    assert names == {
        "Anthem Valve Rainhead",
        "Anthem Valve Outlet 2",
        "Anthem Valve Outlet 3",
    }
    attributes = state(hass, "switch.anthem_valve_outlet_3").attributes
    assert attributes["outlet_type"] == 999
    assert "outlet_type_name" not in attributes


# --------------------------------------------------------------------------- #
# Multi-zone naming
# --------------------------------------------------------------------------- #
# A K-28211 as the user guide's table has it: a showerhead and body sprays on zone 1, a
# rainhead and a handshower on zone 2.
K28211_TYPES = {0: 11, 1: 52, 3: 31, 4: 1}


@pytest.mark.parametrize(
    ("grouping", "expected"),
    [
        (
            "numbered",
            {
                "zone_1_outlet_1": ("Showerhead 1", "Anthem Valve"),
                "zone_2_outlet_1": ("Rainhead 2", "Anthem Valve"),
                "temperature_zone_1": ("Temperature 1", "Anthem Valve"),
                "flow_zone_2": ("Flow 2", "Anthem Valve"),
                "zone_1_active": ("Shower Active 1", "Anthem Valve"),
                "shower": ("Shower on", "Anthem Valve"),
                "warmup": ("Warmup", "Anthem Valve"),
            },
        ),
        (
            "subdevices",
            {
                "zone_1_outlet_1": ("Showerhead", "Anthem Valve Zone 1"),
                "zone_2_outlet_1": ("Rainhead", "Anthem Valve Zone 2"),
                "temperature_zone_1": ("Temperature", "Anthem Valve Zone 1"),
                "flow_zone_2": ("Flow", "Anthem Valve Zone 2"),
                "zone_1_active": ("Zone Active", "Anthem Valve Zone 1"),
                # What belongs to the whole valve stays on the valve.
                "shower": ("Shower on", "Anthem Valve"),
                "warmup": ("Warmup", "Anthem Valve"),
            },
        ),
        (
            "outlet_labels",
            {
                "zone_1_outlet_1": ("Showerhead", "Anthem Valve"),
                "zone_2_outlet_1": ("Rainhead", "Anthem Valve"),
                "temperature_zone_1": (
                    "Temperature (Showerhead, Body Sprays)",
                    "Anthem Valve",
                ),
                "flow_zone_2": ("Flow (Rainhead, Handshower)", "Anthem Valve"),
                "zone_1_active": (
                    "Zone Active (Showerhead, Body Sprays)",
                    "Anthem Valve",
                ),
                "shower": ("Shower on", "Anthem Valve"),
                "warmup": ("Warmup", "Anthem Valve"),
            },
        ),
    ],
)
async def test_the_three_multi_zone_naming_modes(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    grouping: str,
    expected: dict[str, tuple[str, str]],
) -> None:
    """As the user guide's table: names and devices change, unique ids never do."""
    kohler.valve_outlets = (2, 2)
    kohler.outlet_types = dict(K28211_TYPES)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_ZONE_GROUPING: grouping}
    )
    await setup(hass, config_entry)

    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    for key, (name, device_name) in expected.items():
        entry = next(
            e
            for e in er.async_entries_for_config_entry(entities, config_entry.entry_id)
            if e.unique_id == f"{VALVE_ID}_{key}"
        )
        assert entry.original_name == name, key
        assert devices.async_get(entry.device_id).name == device_name, key


async def test_a_fixture_on_both_zones_keeps_its_zone_number_with_outlet_labels(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Two `Showerhead` switches on one device would be indistinguishable."""
    kohler.valve_outlets = (2, 2)
    kohler.outlet_types = {0: 11, 1: 52, 3: 11, 4: 1}
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_ZONE_GROUPING: "outlet_labels"}
    )
    await setup(hass, config_entry)

    names = {
        e.unique_id.removeprefix(f"{VALVE_ID}_"): e.original_name
        for e in er.async_entries_for_config_entry(
            er.async_get(hass), config_entry.entry_id
        )
        if "_outlet_" in e.unique_id
    }
    assert names == {
        "zone_1_outlet_1": "Showerhead 1",
        "zone_1_outlet_2": "Body Sprays",
        "zone_2_outlet_1": "Showerhead 2",
        "zone_2_outlet_2": "Handshower",
    }


async def test_a_single_zone_valve_gets_no_zone_entities(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """`System Status` already answers per-zone questions when there is one zone."""
    await setup(hass, config_entry)

    unique_ids = set(ids_by_unique_id(hass, config_entry))
    assert f"{VALVE_ID}_temperature_zone_1" in unique_ids
    for absent in (
        "zone_1_active",
        "temperature_zone_2",
        "flow_zone_2",
        "zone_2_hex",
        "firmware_valve2",
        "zone_2_outlet_1",
    ):
        assert f"{VALVE_ID}_{absent}" not in unique_ids, absent
    assert state(hass, TEMPERATURE).name == "Anthem Valve Temperature"


async def test_a_two_zone_valve_gets_each_zones_entities(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)

    unique_ids = set(ids_by_unique_id(hass, config_entry))
    for present in (
        "zone_1_active",
        "zone_2_active",
        "temperature_zone_2",
        "flow_zone_2",
        "zone_2_hex",
        "firmware_valve2",
        "zone_2_outlet_3",
    ):
        assert f"{VALVE_ID}_{present}" in unique_ids, present


# --------------------------------------------------------------------------- #
# Warmup Auto-Restore and Report Log
# --------------------------------------------------------------------------- #
AUTO_RESTORE_KEY = f"{VALVE_ID}_warmup_auto_restore"


async def test_warmup_auto_restore_exists_only_on_an_account_with_a_controller(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The one known cause of a spontaneous disable is the controller's web UI."""
    await setup(hass, config_entry)
    assert AUTO_RESTORE_KEY not in ids_by_unique_id(hass, config_entry)


async def test_warmup_auto_restore_is_created_disabled_beside_a_controller(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    kohler.devices.append(dict(HUB))
    await setup(hass, config_entry)

    entity_id = ids_by_unique_id(hass, config_entry)[AUTO_RESTORE_KEY]
    entry = er.async_get(hass).async_get(entity_id)
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_a_left_over_warmup_auto_restore_goes_with_the_controller(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """An account whose controller has gone keeps no switch that defends against nothing."""
    enable(hass, config_entry, SWITCH_DOMAIN, AUTO_RESTORE_KEY, "anthem_valve_restore")
    other = enable(
        hass, config_entry, SWITCH_DOMAIN, f"{GUEST_ID}_warmup_auto_restore", "guest"
    )
    await setup(hass, config_entry)

    registry = er.async_get(hass)
    assert registry.async_get("switch.anthem_valve_restore") is None
    # Matched on this valve's own id: another valve's row is not this one's to remove.
    assert registry.async_get(other) is not None


async def test_warmup_auto_restore_is_a_per_valve_setting(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Stored in the entry's options, so it survives a restart without a reload."""
    kohler.devices.append(dict(HUB))
    restore = enable(
        hass, config_entry, SWITCH_DOMAIN, AUTO_RESTORE_KEY, "anthem_valve_restore"
    )
    await setup(hass, config_entry)
    assert state(hass, restore).state == STATE_OFF
    attributes = state(hass, restore).attributes
    assert (attributes["restores_to"], attributes["delay_seconds"]) == (None, 60)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, restore)
    assert state(hass, restore).state == STATE_ON
    assert config_entry.options[CONF_VALVES][VALVE_ID][CONF_WARMUP_AUTO_RESTORE]
    # A setting, not a command: nothing reaches the valve.
    assert kohler.posts == []

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, restore)
    assert state(hass, restore).state == STATE_OFF
    assert not config_entry.options[CONF_VALVES][VALVE_ID][CONF_WARMUP_AUTO_RESTORE]


async def test_warmup_auto_restore_names_the_mode_it_would_put_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The mode the valve already held at startup is the one a restore reinstates."""
    kohler.devices.append(dict(HUB))
    kohler.gcs["warmUpState"]["warmUp"] = "warmUpAllOutletsWithNoStartDelay"
    restore = enable(
        hass, config_entry, SWITCH_DOMAIN, AUTO_RESTORE_KEY, "anthem_valve_restore"
    )
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, restore)

    attributes = state(hass, restore).attributes
    assert attributes["restores_to"] == "warmUpAllOutletsWithNoStartDelay"
    assert kohler.posts == []


async def test_the_report_log_switch_starts_and_stops_one_capture(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    tmp_path: Path,
) -> None:
    """One capture, so the valve's and the controller's switches always agree."""
    kohler.devices.append(dict(HUB))
    with patch(
        "custom_components.kohler_konnect.coordinator.REPORT_LOG_DIR_NAME",
        str(tmp_path),
    ):
        await setup(hass, config_entry)
    valve_log = "switch.anthem_valve_report_log"
    controller_log = "switch.anthem_plus_report_log"

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, valve_log)
    for entity_id in (valve_log, controller_log):
        assert state(hass, entity_id).state == STATE_ON
    captured = state(hass, controller_log).attributes["file"]
    assert captured and (tmp_path / captured).exists()

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, controller_log)
    for entity_id in (valve_log, controller_log):
        assert state(hass, entity_id).state == STATE_OFF


# --------------------------------------------------------------------------- #
# Favorite and Experience
# --------------------------------------------------------------------------- #
async def test_favorite_offers_the_valves_favorites_but_not_its_default_or_experiences(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Slot 1 is the Shower on switch's, and experiences have their own dropdown."""
    kohler.gcs_presets = copy.deepcopy(PRESETS)
    await setup(hass, config_entry)

    favorite = state(hass, FAVORITE)
    assert favorite.state == "Off"
    assert favorite.attributes["options"] == ["Off", "Morning", "Evening"]
    assert favorite.attributes["favourite_count"] == 2
    assert favorite.attributes["experiences"] == ["Wake Up"]
    assert "no_favourites_reason" not in favorite.attributes
    assert state(hass, EXPERIENCE).attributes["options"] == ["Off", "Wake Up"]


@pytest.mark.parametrize(
    ("presets", "reason"),
    [
        (PRESETS[:1], "No favorites have been created on this valve."),
        (
            [PRESETS[0], PRESETS[4]],
            "No favorites to start. This valve's stored slots are experiences",
        ),
    ],
)
async def test_an_empty_favorite_list_says_why(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    presets: list[dict[str, Any]],
    reason: str,
) -> None:
    """An `Off`-only picker looks the same as one that failed to load."""
    kohler.gcs_presets = copy.deepcopy(presets)
    await setup(hass, config_entry)

    favorite = state(hass, FAVORITE)
    assert favorite.attributes["options"] == ["Off"]
    assert favorite.attributes["no_favourites_reason"].startswith(reason)


async def test_choosing_a_favorite_starts_its_slot(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    kohler.gcs_presets = copy.deepcopy(PRESETS)
    await setup(hass, config_entry)

    await choose(hass, FAVORITE, "Evening")

    assert [(b["preset"], b["action"]) for b in kohler.sent(CONTROL_PRESET)] == [
        ("4", "On")
    ]
    assert state(hass, FAVORITE).state == "Evening"


async def test_a_chosen_favorite_is_held_until_the_valve_confirms_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Other messages arriving first must not snap the dropdown back ("flip flop")."""
    kohler.gcs_presets = copy.deepcopy(PRESETS)
    await setup(hass, config_entry)
    await choose(hass, FAVORITE, "Morning")

    # A report that does not yet carry the favorite.
    await solo(hass, word(0b001))
    assert state(hass, FAVORITE).state == "Morning"

    # The valve confirms it, so nothing is left to hold.
    await solo(hass, word(0b001), preset=2)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=13))
    await hass.async_block_till_done()
    assert state(hass, FAVORITE).state == "Morning"
    assert state(hass, FAVORITE).attributes["active_preset_id"] == 2


async def test_a_favorite_the_valve_never_starts_falls_back_after_the_grace(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """One of these can start water, so it must not keep claiming what it cannot support."""
    kohler.gcs_presets = copy.deepcopy(PRESETS)
    await setup(hass, config_entry)
    await choose(hass, FAVORITE, "Morning")

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=13))
    await hass.async_block_till_done()
    assert state(hass, FAVORITE).state == "Off"


async def test_a_refused_favorite_falls_back_at_once(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    kohler.gcs_presets = copy.deepcopy(PRESETS)
    await setup(hass, config_entry)
    kohler.fail_api("/gcs/controlpresetorexperience", (403, {"message": "Forbidden"}))

    with pytest.raises(HomeAssistantError):
        await choose(hass, FAVORITE, "Morning")
    assert state(hass, FAVORITE).state == "Off"


async def test_favorite_off_stops_the_shower(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    kohler.gcs_presets = copy.deepcopy(PRESETS)
    await setup(hass, config_entry)
    await solo(hass, word(0b011), preset=2)
    assert state(hass, FAVORITE).state == "Morning"

    await choose(hass, FAVORITE, "Off")

    assert kohler.sent(CONTROL_PRESET) == []
    [(zone1, _)] = kohler.words()
    assert zone1.outlet_mask == 0
    assert state(hass, FAVORITE).state == "Off"


async def test_favorite_reports_what_started_the_session(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A favorite run from the app shows; the hidden default and experiences read Off."""
    kohler.gcs_presets = copy.deepcopy(PRESETS)
    await setup(hass, config_entry)

    await solo(hass, word(0b001), preset=4)
    assert state(hass, FAVORITE).state == "Evening"
    preset_active = state(hass, "binary_sensor.anthem_valve_preset_active")
    assert preset_active.state == STATE_ON
    assert (
        preset_active.attributes["preset_id"],
        preset_active.attributes["preset_name"],
    ) == (4, "Evening")

    # The Shower on switch's default shower is not a favorite to show.
    await solo(hass, word(0b001), preset=1)
    assert state(hass, FAVORITE).state == "Off"
    assert state(hass, FAVORITE).attributes["active_preset_id"] == 1
    assert state(hass, "binary_sensor.anthem_valve_preset_active").state == STATE_ON

    # An experience belongs to the Experience select.
    await solo(hass, word(0b001), preset=17)
    assert state(hass, FAVORITE).state == "Off"
    assert state(hass, EXPERIENCE).state == "Wake Up"
    assert state(hass, EXPERIENCE).attributes["active_experience_id"] == 17


async def test_experience_select_starts_and_stops_the_valves_experiences(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The same command body the app sends for an experience id."""
    kohler.gcs_presets = copy.deepcopy(PRESETS)
    await setup(hass, config_entry)

    await choose(hass, EXPERIENCE, "Wake Up")
    assert state(hass, EXPERIENCE).state == "Wake Up"
    await solo(hass, word(0b001), preset=17)

    await choose(hass, EXPERIENCE, "Off")
    assert [(b["preset"], b["action"]) for b in kohler.sent(CONTROL_PRESET)] == [
        ("17", "On"),
        ("17", "Off"),
    ]


async def test_off_right_after_starting_an_experience_stops_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Changing one's mind within the round trip must not be silently ignored."""
    kohler.gcs_presets = copy.deepcopy(PRESETS)
    await setup(hass, config_entry)

    await choose(hass, EXPERIENCE, "Wake Up")
    await choose(hass, EXPERIENCE, "Off")

    assert [(b["preset"], b["action"]) for b in kohler.sent(CONTROL_PRESET)] == [
        ("17", "On"),
        ("17", "Off"),
    ]
    assert state(hass, EXPERIENCE).state == "Off"


async def test_experience_off_with_nothing_running_sends_nothing(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    kohler.gcs_presets = copy.deepcopy(PRESETS)
    await setup(hass, config_entry)

    await choose(hass, EXPERIENCE, "Off")
    assert kohler.posts == []


# --------------------------------------------------------------------------- #
# Warmup
# --------------------------------------------------------------------------- #
async def test_warmup_offers_the_three_modes_the_app_writes(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    quick_writes: None,
) -> None:
    await setup(hass, config_entry)
    warmup = state(hass, WARMUP)
    assert warmup.state == "Off"
    assert warmup.attributes["options"] == ["Off", "All Outlets", "Started Outlets"]

    await choose(hass, WARMUP, "Started Outlets")

    assert [b["warmUp"] for b in kohler.sent(SET_WARMUP)] == [
        "warmUpSelectedOutletsWithNoStartDelay"
    ]
    warmup = state(hass, WARMUP)
    assert warmup.state == "Started Outlets"
    assert warmup.attributes["warmup_mode"] == "warmUpSelectedOutletsWithNoStartDelay"


async def test_warmup_cannot_be_changed_while_the_shower_runs(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    quick_writes: None,
) -> None:
    """The Konnect app blocks this too; here it says so instead of reverting silently."""
    await setup(hass, config_entry)
    await solo(hass, word(0b001))

    with pytest.raises(HomeAssistantError, match="while the shower is running"):
        await choose(hass, WARMUP, "All Outlets")
    assert kohler.sent(SET_WARMUP) == []
    assert state(hass, WARMUP).state == "Off"


async def test_a_legacy_warmup_mode_is_listed_only_while_the_valve_holds_it(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    quick_writes: None,
) -> None:
    """Reported truthfully while in force, never offered otherwise."""
    kohler.gcs["warmUpState"]["warmUp"] = "warmUpAllOutlets"
    await setup(hass, config_entry)
    warmup = state(hass, WARMUP)
    assert warmup.state == "All Outlets (delayed start)"
    assert warmup.attributes["options"] == [
        "Off",
        "All Outlets",
        "Started Outlets",
        "All Outlets (delayed start)",
    ]

    # Nothing establishes what its delay does, so it is not written back.
    with pytest.raises(HomeAssistantError, match="not a warmup mode"):
        await choose(hass, WARMUP, "All Outlets (delayed start)")
    assert kohler.sent(SET_WARMUP) == []
    assert state(hass, WARMUP).state == "All Outlets (delayed start)"

    await choose(hass, WARMUP, "All Outlets")
    warmup = state(hass, WARMUP)
    assert warmup.state == "All Outlets"
    assert warmup.attributes["options"] == ["Off", "All Outlets", "Started Outlets"]


async def test_a_warmup_change_the_valve_ignores_falls_back_after_the_grace(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    quick_writes: None,
) -> None:
    """The cloud accepts the write with a 200 whether or not the valve applies it."""
    kohler.apply_warmup = False
    await setup(hass, config_entry)

    await choose(hass, WARMUP, "All Outlets")
    assert state(hass, WARMUP).state == "All Outlets"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=13))
    await hass.async_block_till_done()
    assert state(hass, WARMUP).state == "Off"


async def test_a_valve_that_never_reported_a_warmup_mode_says_so(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Never reported is not the same as Off, and a write to it may silently do nothing."""
    del kohler.gcs["warmUpState"]
    await setup(hass, config_entry)

    warmup = state(hass, WARMUP)
    assert warmup.state == STATE_UNKNOWN
    assert warmup.attributes["mode_never_reported"] is True
    assert "has not reported a warm-up mode" in warmup.attributes["note"]


# --------------------------------------------------------------------------- #
# Max Shower Duration, Max Temperature, Default Temperature
# --------------------------------------------------------------------------- #
def outlet_config_message(outlet_id: int, run_time: int) -> dict[str, Any]:
    """`READ_GCS_OUTLET_CONFIG_CFG`, the valve announcing one outlet, in WIRE units."""
    return {
        "code": "READ_GCS_OUTLET_CONFIG_CFG",
        "outLetId": str(outlet_id),
        "minimumFlowRate": "16",
        "maximumFlowRate": "200",
        "maximumRunTime": str(run_time),
    }


async def test_max_shower_duration_rewrites_every_outlet(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    quick_writes: None,
) -> None:
    """One duration, written one outlet at a time with each whole record echoed back."""
    await setup(hass, config_entry)
    duration = state(hass, DURATION)
    assert duration.state == "30 minutes"
    assert duration.attributes["options"] == [
        "15 minutes",
        "20 minutes",
        "25 minutes",
        "30 minutes",
        "45 minutes",
        "60 minutes",
    ]
    assert duration.attributes["per_outlet"] == {
        "Rainhead": 30.0,
        "Showerhead": 30.0,
        "Handshower": 30.0,
    }

    await choose(hass, DURATION, "45 minutes")

    records = [b["gcsOutletConfigControlModel"] for b in kohler.sent(WRITE_OUTLET)]
    assert [(r["outLetId"], r["maximumRuntime"]) for r in records] == [
        ("0", "2700"),
        ("1", "2700"),
        ("2", "2700"),
    ]
    # Everything else goes back exactly as read, in wire units.
    assert records[0] == {
        "outLetId": "0",
        "outLetType": "31",
        "outLetFlags": "1",
        "minimumOutletTemperature": "150",
        "defaultOutletTemperature": "388",
        "maximumOutletTemperature": "477",
        "minimumFlowrate": "16",
        "defaultFlowrate": "200",
        "maximumFlowrate": "200",
        "maximumRuntime": "2700",
        "maxVolume": "0",
        "purge": "",
    }
    # Verified against a read-back, so no warning is raised.
    issue = f"outlet_write_unverified_{device_issue_key(VALVE_ID)}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None

    # The valve announces each outlet as it applies the change.
    for outlet_id in range(3):
        await report(
            hass,
            "READ_GCS_OUTLET_CONFIG_CFG",
            outlet_config_message(outlet_id, 2700),
        )
    duration = state(hass, DURATION)
    assert duration.state == "45 minutes"
    # Only an out-of-date Konnect app misreads anything above 30 minutes.
    assert duration.attributes["long_duration_app_warning"] is True


async def test_a_duration_write_the_valve_ignores_raises_a_repair_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    quick_writes: None,
) -> None:
    """A 201 means accepted for delivery, never applied."""
    kohler.apply_outlet_writes = False
    await setup(hass, config_entry)

    await choose(hass, DURATION, "20 minutes")
    await hass.async_block_till_done()

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"outlet_write_unverified_{device_issue_key(VALVE_ID)}"
    )
    assert issue is not None
    assert issue.translation_placeholders["setting"] == (
        "Max Shower Duration (20 minutes)"
    )
    assert "1, 2, 3" in issue.translation_placeholders["detail"]


async def test_a_duration_write_that_fails_part_way_says_which_outlets_took_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)
    kohler.fail_api("/gcs/writeoutletconfig", (201, {}), (500, {"message": "oops"}))

    with pytest.raises(HomeAssistantError, match="failed after outlet 1"):
        await choose(hass, DURATION, "60 minutes")
    # Stopped at the first failure, as the app does.
    assert len(kohler.sent(WRITE_OUTLET)) == 2


async def test_max_shower_duration_shows_outlets_that_disagree(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Disagreement means a write was lost; the shortest limit is the one that bites."""
    kohler.outlet_fields = {0: {"maximumRuntime": "3600"}}
    await setup(hass, config_entry)

    duration = state(hass, DURATION)
    assert duration.state == "30 minutes"
    assert duration.attributes["outlets_agree"] is False
    assert duration.attributes["per_outlet"]["Rainhead"] == 60.0


async def test_max_shower_duration_outside_the_apps_six_reads_unknown(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Showing it as one of the six would be a lie; the real figure is still published."""
    kohler.outlet_fields = {i: {"maximumRuntime": "2400"} for i in range(3)}
    await setup(hass, config_entry)

    duration = state(hass, DURATION)
    assert duration.state == STATE_UNKNOWN
    assert duration.attributes["reported_minutes"] == 40.0
    assert duration.attributes["in_app_picker"] is False
    assert duration.attributes["long_duration_app_warning"] is True


async def test_max_temperature_rewrites_every_outlets_scald_limit(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    quick_writes: None,
) -> None:
    await setup(hass, config_entry)
    limit = state(hass, MAX_TEMPERATURE)
    assert float(limit.state) == 118
    assert (limit.attributes["min"], limit.attributes["max"]) == (92, 118)

    await set_number(hass, MAX_TEMPERATURE, 110)

    records = [b["gcsOutletConfigControlModel"] for b in kohler.sent(WRITE_OUTLET)]
    # 110 °F is 43.3 °C, in the tenths the wire carries.
    assert [r["maximumOutletTemperature"] for r in records] == ["433"] * 3
    assert {r["maximumRuntime"] for r in records} == {"1800"}
    assert {r["defaultOutletTemperature"] for r in records} == {"388"}


async def test_default_temperature_above_the_scald_limit_is_refused(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A shower cannot start hotter than the scald limit; the message names both."""
    kohler.outlet_fields = {i: {"maximumOutletTemperature": "43.3"} for i in range(3)}
    await setup(hass, config_entry)
    default = state(hass, DEFAULT_TEMPERATURE)
    assert float(default.state) == 102
    assert default.attributes["scald_limit"] == 110

    with pytest.raises(HomeAssistantError, match="above this valve's Max Temperature"):
        await set_number(hass, DEFAULT_TEMPERATURE, 115)
    assert kohler.sent(WRITE_OUTLET) == []

    await set_number(hass, DEFAULT_TEMPERATURE, 104)
    records = [b["gcsOutletConfigControlModel"] for b in kohler.sent(WRITE_OUTLET)]
    assert [r["defaultOutletTemperature"] for r in records] == ["400"] * 3


async def test_outlet_settings_are_unavailable_until_the_valve_reports_its_outlets(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Nothing to show, and nothing a whole-record write could safely echo back."""
    kohler.valve_outlets = None
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_VALVE_MODEL: "K-28210"}
    )
    await setup(hass, config_entry)

    assert state(hass, MAX_TEMPERATURE).state == STATE_UNAVAILABLE
    assert state(hass, DEFAULT_TEMPERATURE).state == STATE_UNAVAILABLE
    assert state(hass, DURATION).state == STATE_UNKNOWN
    # The live controls carry on with the app's own fallbacks.
    temperature = state(hass, TEMPERATURE)
    assert (temperature.attributes["min"], temperature.attributes["max"]) == (58, 118)


# --------------------------------------------------------------------------- #
# Temperature and Flow
# --------------------------------------------------------------------------- #
async def test_temperature_rewrites_the_zone_keeping_its_open_outlets(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Mid-shower changes take effect at once, without closing anything."""
    await setup(hass, config_entry)
    await solo(hass, word(0b001))
    temperature = state(hass, TEMPERATURE)
    assert float(temperature.state) == 102
    # The app's slider: Cold, then 59 °F up to the valve's current scald limit.
    assert (temperature.attributes["min"], temperature.attributes["max"]) == (58, 118)

    await set_number(hass, TEMPERATURE, 104)

    [(zone1, _)] = kohler.words()
    assert (zone1.temperature_celsius, zone1.outlet_mask) == (40.0, 0b001)


async def test_the_cold_step_sends_full_cold_and_reads_back_as_cold(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """As the app's COLD stop: the valve stops mixing in hot water."""
    await setup(hass, config_entry)

    await set_number(hass, TEMPERATURE, 58)
    [(zone1, _)] = kohler.words()
    assert zone1.temperature_celsius == 0.0

    await solo(hass, word(0b001, celsius=0.0))
    temperature = state(hass, TEMPERATURE)
    assert float(temperature.state) == 58
    assert temperature.attributes["cold"] is True
    assert temperature.attributes["reported_temperature"] == 32


async def test_flow_is_written_and_shown_while_the_shower_is_off(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Idle, it shows what was last written — never the valve's idle byte."""
    await setup(hass, config_entry)
    await solo(hass, word(0, flow=24.5))
    flow = state(hass, FLOW)
    assert float(flow.state) == 100
    assert flow.attributes["flow_is_live"] is False

    await set_number(hass, FLOW, 60)

    [(zone1, _)] = kohler.words()
    assert zone1.flow_percent == 60.0
    assert float(state(hass, FLOW).state) == 60


async def test_flow_shows_the_valves_own_value_while_water_runs(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Including a change made at the panel mid-shower."""
    await setup(hass, config_entry)
    await solo(hass, word(0b010, flow=75.0))

    flow = state(hass, FLOW)
    assert float(flow.state) == 75
    assert flow.attributes["flow_is_live"] is True
    assert (flow.attributes["min"], flow.attributes["max"]) == (8, 100)


async def test_a_metric_account_uses_the_apps_range_in_celsius(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Before the valve reports its outlets: 15-48 °C, with Cold one step below."""
    hass.config.units = METRIC_SYSTEM
    kohler.temperature_unit = "Celsius"
    kohler.valve_outlets = None
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_VALVE_MODEL: "K-28210"}
    )
    await setup(hass, config_entry)

    temperature = state(hass, TEMPERATURE)
    assert temperature.attributes["unit_of_measurement"] == "°C"
    assert (temperature.attributes["min"], temperature.attributes["max"]) == (14, 48)


# --------------------------------------------------------------------------- #
# Status sensors
# --------------------------------------------------------------------------- #
async def test_system_status_follows_the_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Warming Up outranks Water Running: warm-up runs water nobody started."""
    await setup(hass, config_entry)
    assert state(hass, STATUS).state == "Idle"

    await solo(hass, word(0b001), warmUpStatus="warmUpNotInProgress")
    assert state(hass, STATUS).state == "Water Running"

    await solo(hass, word(0b001), warmUpStatus="warmUpInProgress")
    assert state(hass, STATUS).state == "Warming Up"
    assert state(hass, STATUS).attributes["valve_warmup"] is True

    await solo(hass, word(0b001, paused=True), warmUpStatus="warmUpNotInProgress")
    assert state(hass, STATUS).state == "Paused"

    await solo(hass, word(0))
    status = state(hass, STATUS)
    assert status.state == "Idle"
    assert status.attributes["controller_warmup"] is False
    assert status.attributes["seconds_remaining"] is None


async def test_system_status_counts_down_to_the_valves_run_time_limit(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The soonest cutoff across zones is the one worth warning about."""
    kohler.valve_outlets = (3, 3)
    kohler.outlet_fields = {3: {"maximumRuntime": "900"}}
    await setup(hass, config_entry)

    await solo(hass, word(0b001), word(0b001, zone=2))

    attributes = state(hass, STATUS).attributes
    assert 0 <= attributes["flowing_for_seconds"] < 5
    assert 895 < attributes["seconds_remaining"] <= 900
    # Zone 2's outlets disagree after a lost write; the valve may enforce either.
    zone2 = state(hass, "binary_sensor.anthem_valve_shower_active_2").attributes
    assert zone2["run_time_limit_seconds"] == [900, 1800]
    assert 895 < zone2["seconds_remaining"] <= 900
    zone1 = state(hass, "binary_sensor.anthem_valve_shower_active_1").attributes
    assert 1795 < zone1["seconds_remaining"] <= 1800


async def test_system_status_counts_a_controller_warm_up_on_a_paired_account(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Either device warming up means water is about to move."""
    kohler.devices.append(dict(HUB))
    await setup(hass, config_entry)

    await report(
        hass,
        "SHOWER_VALVE_STS",
        {"zone": "1", "status": "ON", "outlets": [1, 0, 0, 0, 0, 0]},
        device_id=HUB_ID,
        sku="HUB",
        showerwarmup="1",
    )
    await solo(hass, word(0b001))

    status = state(hass, STATUS)
    assert status.state == "Warming Up"
    assert (
        status.attributes["valve_warmup"],
        status.attributes["controller_warmup"],
    ) == (
        False,
        True,
    )


async def test_system_status_does_not_guess_which_controller_fronts_the_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """With two controllers, the guest bathroom's warm-up is not this shower's."""
    kohler.devices += [
        dict(HUB),
        {**HUB, "deviceId": "hub-guest", "logicalName": "Guest"},
    ]
    await setup(hass, config_entry)

    await report(
        hass,
        "SHOWER_VALVE_STS",
        {"zone": "1", "status": "ON", "outlets": [1, 0, 0, 0, 0, 0]},
        device_id=HUB_ID,
        sku="HUB",
        showerwarmup="1",
    )
    await solo(hass, word(0b001))

    status = state(hass, STATUS)
    assert status.state == "Water Running"
    assert status.attributes["controller_warmup"] is None


@pytest.mark.parametrize(
    ("reported", "shown", "recognised"),
    [
        ("showerInProgress", "showerInProgress", True),
        ("ERROR", "error", False),
        ("FirmwareUpdate", "FirmwareUpdate", True),
        ("somethingNew", STATE_UNKNOWN, False),
    ],
)
async def test_system_state_is_the_valves_own_flag(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    reported: str,
    shown: str,
    recognised: bool,
) -> None:
    """A value it does not know is published raw rather than spamming the log."""
    kohler.gcs["currentSystemState"] = reported
    await setup(hass, config_entry)

    system_state = state(hass, SYSTEM_STATE)
    assert system_state.state == shown
    assert system_state.attributes["reported"] == reported
    assert system_state.attributes["recognised"] is recognised


async def test_the_hex_sensor_shows_the_command_word_to_copy(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Eight uppercase characters, whichever way the word arrived."""
    hex_sensor = enable(
        hass, config_entry, "sensor", f"{VALVE_ID}_zone_1_hex", "anthem_valve_hex"
    )
    kohler.gcs["valve1"] = kohler.idle_valve(out2="1", temperatureSetpoint=40.0)
    await setup(hass, config_entry)

    # From the REST seed, which carries no wire string: encoded with the current codec.
    seeded = state(hass, hex_sensor)
    assert seeded.state == encode_word(0x01, 40.0, 100.0, 0b010)
    assert seeded.attributes["from_device"] is False

    # From the valve itself: the command half, uppercased — never re-encoded.
    await solo(hass, word(0b001, celsius=38.8))
    reported = state(hass, hex_sensor)
    assert reported.state == word(0b001, celsius=38.8)[:8].upper()
    assert reported.attributes["from_device"] is True
    assert reported.attributes["outlet_mask"] == "0x01"
    assert reported.attributes["error_code"] == 1


@pytest.mark.parametrize(
    ("created", "expected"),
    [
        ("2024-03-11T14:22:31Z", "2024-03-11T14:22:31+00:00"),
        # A naive time from the cloud is UTC, like every other date it returns.
        ("2024-03-11T14:22:31", "2024-03-11T14:22:31+00:00"),
        ("1710166951", "2024-03-11T14:22:31+00:00"),
        ("1710166951000", "2024-03-11T14:22:31+00:00"),
        ("last Tuesday", STATE_UNKNOWN),
    ],
)
async def test_registered_reads_either_timestamp_shape(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    created: str,
    expected: str,
) -> None:
    """A format it cannot read stays diagnosable through the raw string."""
    registered = enable(
        hass,
        config_entry,
        "sensor",
        f"{VALVE_ID}_registered",
        "anthem_valve_registered",
    )
    kohler.gcs_configuration = {"createdTime": created}
    await setup(hass, config_entry)

    sensor = state(hass, registered)
    assert sensor.state == expected
    assert sensor.attributes["reported"] == created


async def test_the_three_firmwares_the_app_shows_are_three_sensors(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Interface, valve and gateway are different numbers; one entity hid that."""
    sensors = {
        key: enable(hass, config_entry, "sensor", f"{VALVE_ID}_{key}", key)
        for key in ("firmware", "firmware_valve", "firmware_gateway")
    }
    kohler.gcs_configuration = {
        "configuration": {
            "about": {
                "uI2": {"firmware": "2.2"},
                "primaryValve": {"firmware": "10"},
                "gateway": {"firmware": "00.74"},
            }
        }
    }
    await setup(hass, config_entry)

    assert {key: state(hass, entity).state for key, entity in sensors.items()} == {
        "firmware": "2.2",
        "firmware_valve": "10",
        "firmware_gateway": "00.74",
    }


async def test_last_update_moves_on_any_message_from_the_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A message it cannot decode is still proof the valve is talking."""
    last_update = enable(
        hass, config_entry, "sensor", f"{VALVE_ID}_last_update", "anthem_valve_last"
    )
    await setup(hass, config_entry)
    before = dt_util.utcnow()

    await report(hass, "GCS_RECIEVED_STS")

    reported = dt_util.parse_datetime(state(hass, last_update).state)
    assert reported >= before - timedelta(seconds=1)


async def test_the_mqtt_connection_diagnostic_reports_the_stream(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Our link to Kohler, separately from whether the sign-in still works."""
    connection = enable(
        hass,
        config_entry,
        "binary_sensor",
        f"{VALVE_ID}_mqtt_connection",
        "anthem_valve_mqtt",
    )
    await setup(hass, config_entry)
    assert state(hass, connection).attributes["last_message_at"] is None

    await solo(hass, word(0))

    sensor = state(hass, connection)
    assert sensor.state == STATE_ON
    assert sensor.attributes["credentials_present"] is True
    assert sensor.attributes["last_message_at"] is not None
    assert sensor.attributes["access_token_expires_at"] is not None


# --------------------------------------------------------------------------- #
# Binary sensors
# --------------------------------------------------------------------------- #
async def test_at_temperature_and_problem_follow_the_status_word(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)
    at_temperature = "binary_sensor.anthem_valve_at_temperature"
    problem = "binary_sensor.anthem_valve_problem"
    assert state(hass, problem).state == STATE_OFF

    await solo(hass, word(0b001, at_temperature=True))
    assert state(hass, at_temperature).state == STATE_ON
    assert state(hass, problem).state == STATE_OFF

    await solo(hass, word(0b001, fault=True))
    assert state(hass, at_temperature).state == STATE_OFF
    fault = state(hass, problem)
    assert fault.state == STATE_ON
    assert fault.attributes["error_codes"] == {"zone1": 1}
    # It has never fired on real hardware, and says so.
    assert fault.attributes["fault_detection_verified"] is False


async def test_problem_turns_on_when_the_valve_reports_an_error_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Konnect's own rule: the error flag, or a system state of `error`."""
    kohler.gcs["currentSystemState"] = "Error"
    await setup(hass, config_entry)

    problem = state(hass, "binary_sensor.anthem_valve_problem")
    assert problem.state == STATE_ON
    assert problem.attributes["system_state"] == "Error"


async def test_shower_active_reports_each_zone_of_a_two_zone_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Pause and warm-up are system-level, so only these say which zone is running."""
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)

    await solo(hass, word(0b011), word(0b100, zone=2, paused=True))

    zone1 = state(hass, "binary_sensor.anthem_valve_shower_active_1")
    zone2 = state(hass, "binary_sensor.anthem_valve_shower_active_2")
    assert (zone1.state, zone2.state) == (STATE_ON, STATE_OFF)
    assert zone1.attributes["assigned_outlets"] == [1, 2]
    # Numbered across the valve: zone 2's third outlet is outlet 6.
    assert zone2.attributes["assigned_outlets"] == [6]
    assert zone2.attributes["paused"] is True
    assert zone2.attributes["seconds_remaining"] is None


# --------------------------------------------------------------------------- #
# Buttons
# --------------------------------------------------------------------------- #
async def test_restart_is_disabled_until_enabled_on_purpose(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A button cannot ask "are you sure?", and a restart stops the water."""
    await setup(hass, config_entry)
    entity_id = ids_by_unique_id(hass, config_entry)[f"{VALVE_ID}_restart"]
    entry = er.async_get(hass).async_get(entity_id)
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_restart_reboots_the_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    restart = enable(
        hass, config_entry, BUTTON_DOMAIN, f"{VALVE_ID}_restart", "anthem_valve_restart"
    )
    await setup(hass, config_entry)

    await call(hass, BUTTON_DOMAIN, SERVICE_PRESS, restart)

    assert [b["reset"] for b in kohler.sent("gcs/valvereset")] == ["productRestart"]


async def test_restart_says_a_valve_off_the_cloud_cannot_receive_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    restart = enable(
        hass, config_entry, BUTTON_DOMAIN, f"{VALVE_ID}_restart", "anthem_valve_restart"
    )
    await setup(hass, config_entry)
    kohler.fail_api("/gcs/valvereset", (200, {"statusCode": "900"}))

    with pytest.raises(HomeAssistantError, match="Power-cycle it at the breaker"):
        await call(hass, BUTTON_DOMAIN, SERVICE_PRESS, restart)


async def test_the_capture_button_rolls_the_raw_capture_only_while_it_is_on(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    tmp_path: Path,
) -> None:
    capture = "button.anthem_valve_start_new_mqtt_capture"
    # Captures land beside Home Assistant's configuration.
    hass.config.config_dir = str(tmp_path)
    await setup(hass, config_entry)
    assert state(hass, capture).attributes["capture_enabled"] is False

    # Off by default: nothing to roll, and no file appears.
    await call(hass, BUTTON_DOMAIN, SERVICE_PRESS, capture)
    assert account(hass, config_entry).raw_log.path is None

    switch_logger = logging.getLogger(
        "custom_components.kohler_konnect.konnect.raw_log"
    )
    switch_logger.setLevel(logging.DEBUG)
    try:
        await call(hass, BUTTON_DOMAIN, SERVICE_PRESS, capture)
        first = account(hass, config_entry).raw_log.path
        assert first is not None and Path(first).exists()
    finally:
        account(hass, config_entry).raw_log.close()
        switch_logger.setLevel(logging.NOTSET)


async def test_only_the_first_valve_gets_the_capture_button(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """One capture for the account, so one button."""
    kohler.devices.append(dict(GUEST))
    await setup(hass, config_entry)

    capture = [
        unique_id
        for unique_id in ids_by_unique_id(hass, config_entry)
        if unique_id.endswith("_new_mqtt_capture")
    ]
    assert capture == [f"{VALVE_ID}_new_mqtt_capture"]


# --------------------------------------------------------------------------- #
# Firmware
# --------------------------------------------------------------------------- #
async def test_firmware_status_for_the_valve_and_its_gateway(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """An update shows only when Kohler offers one to this device."""
    kohler.firmware_parts = {
        "gcs": {
            "currentFirmware": "10",
            "firmware": "11",
            "firmwareUpdateAvailable": "True",
            "mandatoryUpdate": False,
            "otaStatus": "Idle",
        },
        "gateway": {
            "currentFirmware": "00.74",
            "firmware": "00.80",
            "firmwareUpdateAvailable": "false",
        },
    }
    await setup(hass, config_entry)

    valve_update = state(hass, "update.anthem_valve_firmware_status")
    assert valve_update.state == STATE_ON
    assert (
        valve_update.attributes["installed_version"],
        valve_update.attributes["latest_version"],
    ) == ("10", "11")
    assert valve_update.attributes["ota_status"] == "Idle"
    # A newer release the cloud is not offering this gateway is not an update.
    gateway = state(hass, "update.anthem_valve_gateway_firmware_status")
    assert gateway.state == STATE_OFF
    assert gateway.attributes["latest_version"] == "00.74"


async def test_a_firmware_install_shows_on_the_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """`currentSystemState: FirmwareUpdate` is the state Konnect acts on."""
    kohler.gcs["currentSystemState"] = "FirmwareUpdate"
    await setup(hass, config_entry)

    valve_update = state(hass, "update.anthem_valve_firmware_status")
    assert valve_update.attributes[ATTR_IN_PROGRESS] is True


# --------------------------------------------------------------------------- #
# kohler_konnect.custom_shower
# --------------------------------------------------------------------------- #
async def custom_shower(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    return await hass.services.async_call(
        DOMAIN, "custom_shower", data, blocking=True, return_response=True
    )


async def test_custom_shower_states_the_whole_shower_in_one_command(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Outlets left out are closed, on both zones; zone 2 follows zone 1's temperature."""
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)
    await solo(hass, word(0b111), word(0b111, zone=2))

    response = await custom_shower(
        hass, zone1_temperature=104, zone1_outlet_2=True, zone2_outlet_1=True, flow=50
    )

    [(zone1, zone2)] = kohler.words()
    assert (zone1.outlet_mask, zone1.temperature_celsius, zone1.flow_percent) == (
        0b010,
        40.0,
        50.0,
    )
    assert zone2 is not None
    assert (zone2.outlet_mask, zone2.temperature_celsius) == (0b001, 40.0)
    # The response carries the words, so it doubles as a way to learn them.
    model = kohler.sent(SOLO)[0]["gcsValveControlModel"]
    assert (response["zone1_hex"], response["zone2_hex"]) == (
        model["primaryValve1"],
        model["secondaryValve1"],
    )
    assert response["keep_on_after_warmup"] is False


async def test_custom_shower_with_no_outlets_stops_the_shower(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)
    await solo(hass, word(0b011))

    await custom_shower(hass, zone1_temperature=100)

    [(zone1, zone2)] = kohler.words()
    assert zone1.outlet_mask == 0
    assert zone2 is None


@pytest.mark.parametrize(
    ("layout", "outlet"),
    [((3, 0), "zone2_outlet_1"), ((2, 2), "zone1_outlet_3")],
)
async def test_custom_shower_refuses_outlets_the_valve_does_not_have(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    layout: tuple[int, int],
    outlet: str,
) -> None:
    """An error rather than a silent no-op, or a different outlet opening instead."""
    kohler.valve_outlets = layout
    await setup(hass, config_entry)

    with pytest.raises(ServiceValidationError, match="outlet\\(s\\) in zone"):
        await custom_shower(hass, zone1_temperature=100, **{outlet: True})
    assert kohler.posts == []


@pytest.mark.parametrize(
    ("unit", "data", "message"),
    [
        ("Fahrenheit", {"zone1_temperature": 0}, "between 59 and 118 °F"),
        ("Fahrenheit", {"zone1_temperature": 119}, "between 59 and 118 °F"),
        (
            "Fahrenheit",
            {"zone1_temperature": 100, "zone2_temperature": 40},
            "Zone 2 temperature",
        ),
        ("Celsius", {"zone1_temperature": 100}, "between 15 and 48 °C"),
    ],
)
async def test_custom_shower_refuses_temperatures_outside_the_slider(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    unit: str,
    data: dict[str, Any],
    message: str,
) -> None:
    """Typing 0 must be an error, not a cold shower; Cold is the slider's bottom step."""
    kohler.temperature_unit = unit
    await setup(hass, config_entry)

    with pytest.raises(ServiceValidationError, match=message):
        await custom_shower(hass, zone1_outlet_1=True, **data)
    assert kohler.posts == []


async def test_custom_shower_reports_the_keep_on_choice(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)

    response = await custom_shower(
        hass, zone1_temperature=100, zone1_outlet_1=True, keep_on_after_warmup=True
    )

    assert response["keep_on_after_warmup"] is True
    assert len(kohler.sent(SOLO)) == 1


# --------------------------------------------------------------------------- #
# kohler_konnect.send_valve_hex
# --------------------------------------------------------------------------- #
async def send_valve_hex(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    return await hass.services.async_call(
        DOMAIN, "send_valve_hex", data, blocking=True, return_response=True
    )


async def test_send_valve_hex_sends_the_word_and_says_what_it_means(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A 16-character word, as the valve reports it, can be pasted as is."""
    await setup(hass, config_entry)

    response = await send_valve_hex(hass, zone1_hex="0184c80100000001")

    model = kohler.sent(SOLO)[0]["gcsValveControlModel"]
    assert (model["primaryValve1"], model["secondaryValve1"]) == (
        "0184C801",
        "00000000",
    )
    assert response["zone1_hex"] == "0184C801"
    assert response["decoded"]["zone1"]


@pytest.mark.parametrize("bad", ["0184C8", "0184C80100", "0184C8ZZ"])
async def test_send_valve_hex_refuses_a_malformed_word(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler, bad: str
) -> None:
    """A typo must not become a different, valid command to something that opens water."""
    await setup(hass, config_entry)

    with pytest.raises(vol.Invalid):
        await send_valve_hex(hass, zone1_hex=bad)
    assert kohler.posts == []


async def test_send_valve_hex_refuses_a_scalding_word(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The one path that skips the encoder's ceiling gets the same ceiling here."""
    await setup(hass, config_entry)

    with pytest.raises(HomeAssistantError, match="Refused"):
        await send_valve_hex(hass, zone1_hex="03FFC801")
    assert kohler.posts == []


@pytest.mark.parametrize("zone2_hex", [None, "", "00000000"])
async def test_send_valve_hex_leaves_zone_two_as_it_is_unless_given(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    zone2_hex: str | None,
) -> None:
    """The all-zero sentinel would make the valve discard the zone 1 word too."""
    kohler.valve_outlets = (3, 3)
    await setup(hass, config_entry)
    await solo(hass, word(0), word(0b010, zone=2, celsius=37.0))

    data = {"zone1_hex": "0184C801"}
    if zone2_hex is not None:
        data["zone2_hex"] = zone2_hex
    await send_valve_hex(hass, **data)

    [(zone1, zone2)] = kohler.words()
    assert zone1.outlet_mask == 0b001
    assert zone2 is not None
    assert (zone2.outlet_mask, zone2.temperature_celsius) == (0b010, 37.0)


async def test_send_valve_hex_refuses_a_zone_two_word_for_a_single_zone_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)

    with pytest.raises(HomeAssistantError, match="has one zone"):
        await send_valve_hex(hass, zone1_hex="0184C801", zone2_hex="1184C801")
    assert kohler.posts == []


# --------------------------------------------------------------------------- #
# Which valve an action is for
# --------------------------------------------------------------------------- #
def form(hass: HomeAssistant, service: str) -> dict[str, Any]:
    """The fields an action's form shows, as this install published them."""
    return hass.data[SERVICE_DESCRIPTION_CACHE][(DOMAIN, service)]["fields"]


def device_id(hass: HomeAssistant, identifier: str) -> str:
    device = registered_device(hass, identifier)
    assert device is not None, identifier
    return device.id


async def test_with_two_valves_an_action_must_say_which(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Refused rather than sent to whichever valve happened to load first."""
    kohler.devices += [dict(GUEST), dict(HUB)]
    await setup(hass, config_entry)

    with pytest.raises(ServiceValidationError, match="More than one Anthem valve"):
        await send_valve_hex(hass, zone1_hex="0184C801")
    with pytest.raises(ServiceValidationError, match="not an Anthem valve"):
        await send_valve_hex(
            hass, zone1_hex="0184C801", device_id=device_id(hass, HUB_ID)
        )
    with pytest.raises(ServiceValidationError, match="No device"):
        await send_valve_hex(hass, zone1_hex="0184C801", device_id="nope")
    assert kohler.posts == []

    await custom_shower(
        hass,
        zone1_temperature=100,
        zone1_outlet_1=True,
        device_id=device_id(hass, GUEST_ID),
    )
    assert [b["deviceId"] for b in kohler.sent(SOLO)] == [GUEST_ID]


async def test_a_zone_device_stands_for_its_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """With a sub-device per zone, picking either zone picks the valve."""
    kohler.devices.append(dict(GUEST))
    kohler.valve_outlets = (3, 3)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_ZONE_GROUPING: "subdevices"}
    )
    await setup(hass, config_entry)

    await send_valve_hex(
        hass, zone1_hex="0184C801", device_id=device_id(hass, f"{GUEST_ID}_zone_2")
    )
    assert [b["deviceId"] for b in kohler.sent(SOLO)] == [GUEST_ID]


async def test_the_action_forms_show_only_what_this_install_has(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """No Zone 2 field for a zone the valve lacks, and no picker without a choice."""
    await setup(hass, config_entry)

    assert set(form(hass, "send_valve_hex")) == {"zone1_hex"}
    fields = form(hass, "custom_shower")
    assert set(fields) == {"zone_1", "keep_on_after_warmup", "advanced_fields"}
    zone1 = fields["zone_1"]["fields"]
    assert set(zone1) == {
        "zone1_temperature",
        "zone1_outlet_1",
        "zone1_outlet_2",
        "zone1_outlet_3",
    }
    slider = zone1["zone1_temperature"]["selector"]["number"]
    assert (slider["min"], slider["max"], slider["unit_of_measurement"]) == (
        59,
        118,
        "°F",
    )


async def test_the_action_forms_grow_with_a_second_zone_and_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    kohler.devices.append(dict(GUEST))
    kohler.valve_outlets = (2, 2)
    await setup(hass, config_entry)

    assert set(form(hass, "send_valve_hex")) == {"device_id", "zone1_hex", "zone2_hex"}
    fields = form(hass, "custom_shower")
    assert "device_id" in fields
    assert set(fields["zone_2"]["fields"]) == {
        "zone2_temperature",
        "zone2_outlet_1",
        "zone2_outlet_2",
    }
