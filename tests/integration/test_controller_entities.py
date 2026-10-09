"""The Anthem Plus controller's entities, run inside a real Home Assistant.

The controller's own view of the shower, favorites, experiences and accessories, and the
commands its controls send — `docs/user_guide.md`, "Anthem+ controller". Uses the harness
from `test_shower_entities.py`: ``ShowerKohler`` answers the controller's REST reads, and its
reports arrive over the fake MQTT client.
"""

from __future__ import annotations

import copy
from collections.abc import Generator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.kohler_konnect.const import DOMAIN

from .conftest import VALVE, registered_device
from .test_shower_entities import (
    HUB,
    HUB_ID,
    ShowerKohler,
    call,
    choose,
    ids_by_unique_id,
    report,
    setup,
    solo,
    state,
    word,
)

SHOWER = "switch.anthem_plus_shower"
SYSTEM = "switch.anthem_plus_system"
STEAM = "switch.anthem_plus_steam"
FAVORITE = "select.anthem_plus_favorite"
EXPERIENCE = "select.anthem_plus_experience"
STATUS = "sensor.anthem_plus_system_status"
TEMPERATURE = "sensor.anthem_plus_temperature"
PROBLEM = "binary_sensor.anthem_plus_problem"

VALVE_CONTROL = "hub/valvecontrol"
STEAM_CONTROL = "hub/steamcontrol"
STOP_ALL = "hub/stopall"
FAVORITE_CONTROL = "hub/favorite/control"

# A `hub-configuration` `configuration` block: one 3-outlet zone, an amplifier and a steam
# generator attached, lighting not, and the controller's LAN address.
CONFIGURATION: dict[str, Any] = {
    "parts": {
        "valve1": "Connected",
        "valve2": "NotConnected",
        "amplifier": "Connected",
        "music": None,
        "light": "NotConnected",
        "steam": "Connected",
    },
    "zoneone": {"configuredoutlets": "3"},
    "zonetwo": {"configuredoutlets": "0"},
    "systemSettings": {"maxShowerDuration": "45", "temperatureUnit": "Fahrenheit"},
    "steamSettings": {"defaultTemperature": "110", "defaultTime": "20"},
    "about": {"hub": {"wlan": {"ip": "192.168.1.40"}}},
}
# REST `hub-experience/{id}/favorites` — `title`, no `name`; one is an experience.
FAVORITES = [
    {"id": 1, "title": "Morning"},
    {"id": 2, "title": "Steam Coach", "isExperience": "True"},
    {"id": 3, "title": "Evening"},
]
EXPERIENCES = {
    "showerExperiences": [{"title": "Breathe"}, {"title": "Focus"}],
    "steamExperiences": [{"title": "Detox"}],
    "iceShowerExperiences": [{"title": "Ice Plunge"}],
}


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> ShowerKohler:
    """An account holding one Anthem Plus controller and nothing else."""
    fake = ShowerKohler(aioclient_mock)
    fake.devices = [dict(HUB)]
    fake.hub_configuration = copy.deepcopy(CONFIGURATION)
    fake.hub_favorites = copy.deepcopy(FAVORITES)
    fake.hub_experiences = copy.deepcopy(EXPERIENCES)
    return fake


@pytest.fixture(autouse=True)
def us_units(hass: HomeAssistant) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM


@pytest.fixture(autouse=True)
def quick_usage_reads() -> Generator[None]:
    with patch(
        "custom_components.kohler_konnect.coordinator.USAGE_REFRESH_DELAY_SECONDS", 0
    ):
        yield


async def hub(hass: HomeAssistant, code: str, *attributes: dict, **data: Any) -> None:
    """Deliver one message from the controller."""
    await report(hass, code, *attributes, device_id=HUB_ID, sku="HUB", **data)


async def shower_valve(
    hass: HomeAssistant, status: str, outlets: list[int], **data: Any
) -> None:
    """`SHOWER_VALVE_STS` for zone 1, as the controller sees it."""
    padded = outlets + [0] * (6 - len(outlets))
    await hub(
        hass,
        "SHOWER_VALVE_STS",
        {"zone": "1", "status": status, "outlets": padded, "temperature": "102"},
        **data,
    )


# --------------------------------------------------------------------------- #
# What gets created
# --------------------------------------------------------------------------- #
async def test_a_controller_only_account_has_no_valve_controls(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The valve actions write the valve; a controller is driven through favorites."""
    await setup(hass, config_entry)

    assert not hass.services.has_service(DOMAIN, "send_valve_hex")
    assert not hass.services.has_service(DOMAIN, "custom_shower")
    unique_ids = set(ids_by_unique_id(hass, config_entry))
    # The capture button lives on the controller when there is no valve to carry it.
    assert f"{HUB_ID}_new_mqtt_capture" in unique_ids
    assert state(hass, "button.anthem_plus_start_new_mqtt_capture")
    assert not any(uid.startswith("gcs-") for uid in unique_ids)


async def test_controller_entities_follow_its_own_outlet_layout(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The entry's model need not be this controller's; its configuration decides."""
    await setup(hass, config_entry)

    unique_ids = set(ids_by_unique_id(hass, config_entry))
    outlets = sorted(uid for uid in unique_ids if "_outlet_" in uid)
    assert outlets == [f"{HUB_ID}_zone_1_outlet_{n}" for n in (1, 2, 3)]
    assert state(hass, TEMPERATURE).name == "Anthem Plus Temperature"


async def test_a_two_zone_controller_numbers_its_zones(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    kohler.hub_configuration["zonetwo"] = {"configuredoutlets": "2"}
    await setup(hass, config_entry)

    assert state(hass, "sensor.anthem_plus_temperature_2").name == (
        "Anthem Plus Temperature 2"
    )
    assert state(hass, "binary_sensor.anthem_plus_zone_2_outlet_2")
    assert hass.states.get("binary_sensor.anthem_plus_zone_2_outlet_3") is None


async def test_accessories_appear_only_where_the_controller_has_them(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A light the controller reports on but has no hardware for would read OFF forever."""
    await setup(hass, config_entry)

    assert state(hass, "binary_sensor.anthem_plus_music")
    assert state(hass, "binary_sensor.anthem_plus_steam")
    assert state(hass, STEAM)
    assert hass.states.get("binary_sensor.anthem_plus_light") is None


async def test_no_steam_control_without_a_steam_generator(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    kohler.hub_configuration["parts"]["steam"] = "NotConnected"
    kohler.hub_configuration["steamSettings"] = {}
    await setup(hass, config_entry)

    assert hass.states.get(STEAM) is None
    assert hass.states.get("binary_sensor.anthem_plus_steam") is None


async def test_an_unread_configuration_still_creates_the_accessory_sensors(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A missed read should cost a sensor reading unknown, not a missing entity."""
    kohler.hub_configuration = None
    await setup(hass, config_entry)

    for accessory in ("music", "light", "steam"):
        assert state(hass, f"binary_sensor.anthem_plus_{accessory}").state == (
            STATE_UNKNOWN
        )
    # A control is different: nothing says a steam generator is there to start.
    assert hass.states.get(STEAM) is None


async def test_the_device_page_links_to_the_controllers_web_settings(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)

    device = registered_device(hass, HUB_ID)
    assert device.name == "Anthem Plus"
    assert device.configuration_url == "http://192.168.1.40/"


async def test_two_controllers_are_two_devices_each_with_its_own_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """One per bathroom; each message goes to the controller whose id it carries."""
    kohler.devices.append({**HUB, "deviceId": "hub-guest", "logicalName": "Guest"})
    await setup(hass, config_entry)

    await shower_valve(hass, "ON", [1])

    assert state(hass, "switch.anthem_plus_bathroom_shower").state == STATE_ON
    assert state(hass, "switch.anthem_plus_guest_shower").state == STATE_UNKNOWN


# --------------------------------------------------------------------------- #
# Shower and System
# --------------------------------------------------------------------------- #
async def test_shower_runs_the_controllers_own_default_shower(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The only bare on/off in the system: the controller stores its own default."""
    await setup(hass, config_entry)
    # Unknown, not off, until the controller has reported a zone.
    assert state(hass, SHOWER).state == STATE_UNKNOWN

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWER)
    assert state(hass, SHOWER).state == STATE_ON
    await shower_valve(hass, "ON", [1])
    assert state(hass, SHOWER).state == STATE_ON

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, SHOWER)
    assert [b["valveOnOff"] for b in kohler.sent(VALVE_CONTROL)] == ["ON", "OFF"]
    # Water only: music, steam and lighting are left alone.
    assert kohler.sent(STOP_ALL) == []


async def test_shower_reports_only_what_the_controller_knows(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A shower driven at the valve is invisible to the controller, and so to this."""
    kohler.devices.append(dict(VALVE))
    kohler.hub_state = {
        "state": {"shower": [{"zone": "1", "status": "OFF", "outlets": [0] * 6}]}
    }
    await setup(hass, config_entry)

    await solo(hass, word(0b001))

    assert state(hass, "switch.anthem_valve_shower_on").state == STATE_ON
    assert state(hass, SHOWER).state == STATE_OFF
    assert state(hass, "binary_sensor.anthem_plus_zone_1_outlet_1").state == STATE_OFF


async def test_a_refused_controller_command_puts_the_switch_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)
    await shower_valve(hass, "OFF", [])
    kohler.fail_api("/hub/valvecontrol", (200, {"statusCode": "900"}))

    with pytest.raises(HomeAssistantError, match="Anthem Plus is offline"):
        await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWER)
    assert state(hass, SHOWER).state == STATE_OFF


async def test_system_is_on_while_anything_the_controller_runs_is_on(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """One row for "is the shower room doing something", with the breakdown beside it."""
    await setup(hass, config_entry)
    await shower_valve(hass, "OFF", [])
    await hub(hass, "MUSIC_STS", {"component": "amplifier", "status": "OFF"})
    assert state(hass, SYSTEM).state == STATE_OFF

    await hub(hass, "MUSIC_STS", {"component": "amplifier", "status": "ON"})

    system = state(hass, SYSTEM)
    assert system.state == STATE_ON
    assert (system.attributes["water"], system.attributes["music"]) == (False, True)
    # An accessory that has not reported is unknown, not idle.
    assert system.attributes["light"] is None


async def test_system_off_stops_everything_and_on_starts_only_water(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """There is no "start everything" command; the asymmetry is deliberate."""
    await setup(hass, config_entry)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, SYSTEM)
    assert len(kohler.sent(STOP_ALL)) == 1
    assert state(hass, SYSTEM).state == STATE_OFF

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SYSTEM)
    assert [b["valveOnOff"] for b in kohler.sent(VALVE_CONTROL)] == ["ON"]
    assert state(hass, SYSTEM).state == STATE_ON


async def test_a_refused_stop_puts_the_system_switch_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)
    await hub(hass, "MUSIC_STS", {"component": "amplifier", "status": "ON"})
    kohler.fail_api("/hub/stopall", (500, {"message": "oops"}))

    with pytest.raises(HomeAssistantError, match="Kohler command failed"):
        await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, SYSTEM)
    assert state(hass, SYSTEM).state == STATE_ON


# --------------------------------------------------------------------------- #
# Steam
# --------------------------------------------------------------------------- #
async def test_steam_starts_at_the_controllers_own_defaults(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)
    steam = state(hass, STEAM)
    assert (
        steam.attributes["default_temperature"],
        steam.attributes["default_time"],
    ) == (110, 20)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, STEAM)

    # No temperature or duration in the body: the controller runs its own.
    [body] = kohler.sent(STEAM_CONTROL)
    assert body["steamOnOff"] == "ON"
    assert set(body) == {"deviceId", "sku", "tenantId", "steamOnOff"}
    assert state(hass, STEAM).state == STATE_ON


async def test_steam_is_refused_while_the_controller_runs_the_shower(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The Konnect app will not run shower and steam at once; neither does this."""
    await setup(hass, config_entry)
    await shower_valve(hass, "ON", [1])

    with pytest.raises(HomeAssistantError, match="stop the shower first"):
        await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, STEAM)
    assert kohler.sent(STEAM_CONTROL) == []
    assert state(hass, STEAM).state != STATE_ON

    # Stopping steam is never refused.
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, STEAM)
    assert [b["steamOnOff"] for b in kohler.sent(STEAM_CONTROL)] == ["OFF"]


async def test_a_self_cleaning_steam_generator_reads_off(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Power clean is not steam anyone should stand in."""
    await setup(hass, config_entry)

    await hub(
        hass,
        "STEAM_STS",
        {"status": "POWERCLEAN", "temperature": "110", "totaltime": "20"},
    )

    steam = state(hass, STEAM)
    assert steam.state == STATE_OFF
    assert steam.attributes["power_clean"] is True
    sensor = state(hass, "binary_sensor.anthem_plus_steam")
    assert sensor.state == STATE_OFF
    assert (sensor.attributes["status"], sensor.attributes["temperature"]) == (
        "POWERCLEAN",
        "110",
    )


# --------------------------------------------------------------------------- #
# Favorite
# --------------------------------------------------------------------------- #
async def test_favorite_offers_the_controllers_favorites_but_not_experiences(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A different list from the valve's: these are stored on the controller."""
    await setup(hass, config_entry)

    favorite = state(hass, FAVORITE)
    assert favorite.state == "Off"
    assert favorite.attributes["options"] == ["Off", "Morning", "Evening"]
    assert favorite.attributes["favourite_count"] == 2


async def test_favorite_follows_the_list_the_controller_pushes(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Snapshots name favorites with `name`, where REST used `title`."""
    await setup(hass, config_entry)

    await hub(
        hass,
        "FAVORITES_SNAPSHOT",
        {"id": "1", "name": "Morning"},
        {"id": "2", "name": "Hair Wash"},
    )
    assert state(hass, FAVORITE).attributes["options"] == [
        "Off",
        "Morning",
        "Hair Wash",
    ]

    # Its last favorite deleted: an empty list is an answer too.
    await hub(hass, "FAVORITES_SNAPSHOT")
    assert state(hass, FAVORITE).attributes["options"] == ["Off"]


async def test_choosing_a_controller_favorite_activates_it_by_name_and_id(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Ids shift when one is deleted, so the name is resolved at the moment of choosing."""
    await setup(hass, config_entry)

    await choose(hass, FAVORITE, "Evening")

    [body] = kohler.sent(FAVORITE_CONTROL)
    assert (body["id"], body["name"], body["state"]) == ("3", "Evening", "ON")
    assert state(hass, FAVORITE).state == "Evening"


async def test_a_chosen_controller_favorite_is_held_until_favorite_sts(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Measured: STEAM_STS and MUSIC_STS land before the FAVORITE_STS that answers."""
    await setup(hass, config_entry)
    await choose(hass, FAVORITE, "Morning")

    await hub(hass, "STEAM_STS", {"status": "OFF"})
    await hub(hass, "MUSIC_STS", {"component": "amplifier", "status": "ON"})
    assert state(hass, FAVORITE).state == "Morning"

    await hub(hass, "FAVORITE_STS", {"id": "1", "name": "Morning", "status": "ON"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=13))
    await hass.async_block_till_done()
    assert state(hass, FAVORITE).state == "Morning"

    await hub(hass, "FAVORITE_STS", {"id": "1", "name": "Morning", "status": "OFF"})
    assert state(hass, FAVORITE).state == "Off"


async def test_a_running_favorite_the_list_does_not_have_yet_is_still_shown(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """One created moments ago, or a cold start before the list arrives."""
    kohler.hub_favorites = None  # 404: no favorites saved
    await setup(hass, config_entry)
    assert state(hass, FAVORITE).attributes["options"] == ["Off"]

    await hub(hass, "FAVORITE_STS", {"id": "7", "name": "Brand New", "status": "ON"})

    favorite = state(hass, FAVORITE)
    assert favorite.state == "Brand New"
    assert favorite.attributes["options"] == ["Off", "Brand New"]
    assert favorite.attributes["active_favorite_id"] == "7"


async def test_a_running_favorite_without_a_name_is_found_by_its_id(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A fallback for a message that carries only the id; an unknown id reads Off."""
    await setup(hass, config_entry)

    await hub(hass, "FAVORITE_STS", {"id": "3", "status": "ON"})
    assert state(hass, FAVORITE).state == "Evening"

    await hub(hass, "FAVORITE_STS", {"id": "9", "status": "ON"})
    favorite = state(hass, FAVORITE)
    assert favorite.state == "Off"
    assert favorite.attributes["active_favorite_id"] == "9"


async def test_a_favorite_only_shown_because_it_runs_cannot_be_started(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Without it in the list there is no id to send; say so rather than guess."""
    kohler.hub_favorites = None
    await setup(hass, config_entry)
    await hub(hass, "FAVORITE_STS", {"id": "7", "name": "Brand New", "status": "ON"})

    with pytest.raises(HomeAssistantError, match="No Anthem Plus favorite"):
        await choose(hass, FAVORITE, "Brand New")
    assert kohler.posts == []


async def test_controller_favorite_off_stops_the_water_only(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The whole-system stop belongs to the System switch."""
    await setup(hass, config_entry)
    await hub(hass, "FAVORITE_STS", {"id": "1", "name": "Morning", "status": "ON"})

    await choose(hass, FAVORITE, "Off")

    assert [b["valveOnOff"] for b in kohler.sent(VALVE_CONTROL)] == ["OFF"]
    assert kohler.sent(STOP_ALL) == []
    assert kohler.sent(FAVORITE_CONTROL) == []


async def test_a_controller_favorite_that_never_starts_falls_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)
    await choose(hass, FAVORITE, "Morning")

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=13))
    await hass.async_block_till_done()
    assert state(hass, FAVORITE).state == "Off"


# --------------------------------------------------------------------------- #
# Experience
# --------------------------------------------------------------------------- #
async def test_experience_lists_the_controllers_catalogue(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)

    assert state(hass, EXPERIENCE).attributes["options"] == [
        "Off",
        "Breathe",
        "Focus",
        "Detox",
        "Ice Plunge",
    ]


@pytest.mark.parametrize(
    ("title", "endpoint"),
    [
        ("Breathe", "hub/shower/experience/control"),
        ("Detox", "hub/steam/experience/control"),
        ("Ice Plunge", "hub/iceshower/experience/control"),
    ],
)
async def test_an_experience_is_started_on_its_own_categorys_endpoint(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    title: str,
    endpoint: str,
) -> None:
    """A shower experience sent to the steam path does nothing."""
    await setup(hass, config_entry)

    await choose(hass, EXPERIENCE, title)

    assert [(path, body["name"], body["status"]) for path, body in kohler.posts] == [
        (endpoint, title, "ON")
    ]
    assert state(hass, EXPERIENCE).state == title


async def test_experience_shows_what_runs_and_off_stops_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)

    await hub(hass, "SHOWER_EXP_STS", {"name": "Focus", "status": "ON"})
    assert state(hass, EXPERIENCE).state == "Focus"

    await choose(hass, EXPERIENCE, "Off")
    assert [(path, body["name"], body["status"]) for path, body in kohler.posts] == [
        ("hub/shower/experience/control", "Focus", "OFF")
    ]


async def test_experience_off_with_nothing_running_sends_nothing(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)

    await choose(hass, EXPERIENCE, "Off")
    assert kohler.posts == []


async def test_a_running_experience_missing_from_the_catalogue_is_still_shown(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await setup(hass, config_entry)

    await hub(hass, "STEAM_EXP_STS", {"name": "Steam Coach", "status": "ON"})

    experience = state(hass, EXPERIENCE)
    assert experience.state == "Steam Coach"
    assert experience.attributes["options"][-1] == "Steam Coach"


async def test_an_experience_missing_from_the_catalogue_cannot_be_started(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Its category decides the endpoint, and nothing says which category it is in."""
    await setup(hass, config_entry)
    await hub(hass, "STEAM_EXP_STS", {"name": "Steam Coach", "status": "ON"})

    with pytest.raises(HomeAssistantError, match="No experience called"):
        await choose(hass, EXPERIENCE, "Steam Coach")
    assert kohler.posts == []


# --------------------------------------------------------------------------- #
# Sensors
# --------------------------------------------------------------------------- #
async def test_system_status_is_the_controllers_view_without_a_paused_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Warm-up runs water, so it outranks running; the controller has no "Paused"."""
    await setup(hass, config_entry)
    assert state(hass, STATUS).state == "Idle"

    await shower_valve(hass, "ON", [1])
    assert state(hass, STATUS).state == "Water Running"

    await shower_valve(hass, "ON", [1], showerwarmup="1")
    status = state(hass, STATUS)
    assert status.state == "Warming Up"
    assert status.attributes["zone_status"] == {1: "ON"}
    assert status.attributes["supports_paused"] is False

    await shower_valve(hass, "OFF", [], showerwarmup="0")
    assert state(hass, STATUS).state == "Idle"


async def test_zone_temperature_is_in_the_accounts_unit(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The controller sends the account's unit, unlike the valve word's Celsius."""
    await setup(hass, config_entry)
    assert state(hass, TEMPERATURE).state == STATE_UNKNOWN

    await shower_valve(hass, "ON", [1])

    temperature = state(hass, TEMPERATURE)
    assert float(temperature.state) == 102
    assert temperature.attributes["unit_of_measurement"] == "°F"

    # A reading that is not a number is no reading at all.
    await hub(
        hass,
        "SHOWER_VALVE_STS",
        {"zone": "1", "status": "OFF", "outlets": [0] * 6, "temperature": "--"},
    )
    assert state(hass, TEMPERATURE).state == STATE_UNKNOWN


async def test_max_shower_duration_is_the_controllers_own_limit(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The shorter of this and the valve's ends a shower."""
    await setup(hass, config_entry)

    duration = state(hass, "sensor.anthem_plus_max_shower_duration")
    assert float(duration.state) == 45
    assert duration.attributes["unit_of_measurement"] == "min"


async def test_outlet_sensors_are_the_controllers_outlet_arrays(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Six padded slots per zone, of which only this valve's three mean anything."""
    await setup(hass, config_entry)
    first = "binary_sensor.anthem_plus_zone_1_outlet_1"
    assert state(hass, first).state == STATE_UNKNOWN

    await shower_valve(hass, "ON", [0, 1, 0, 1, 1, 1])

    assert [
        state(hass, f"binary_sensor.anthem_plus_zone_1_outlet_{n}").state
        for n in (1, 2, 3)
    ] == [STATE_OFF, STATE_ON, STATE_OFF]
    # The Shower switch agrees with the rows beneath it.
    assert state(hass, SHOWER).state == STATE_ON


async def test_light_reports_each_group_and_is_on_while_any_is(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """One group per message: the last one alone must not decide."""
    kohler.hub_configuration["parts"]["light"] = "Connected"
    kohler.hub_configuration["lightSettings"] = [{"name": "Ceiling"}]
    await setup(hass, config_entry)

    await hub(hass, "LIGHT_STS", {"component": "lightgroupA", "status": "ON"})
    await hub(hass, "LIGHT_STS", {"component": "lightgroupB", "status": "OFF"})

    light = state(hass, "binary_sensor.anthem_plus_light")
    assert light.state == STATE_ON
    assert light.attributes["groups"] == {"a": True, "b": False}
    assert light.attributes["configured_groups"] == ["Ceiling"]


async def test_problem_reports_an_active_error(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """What the app words as "<title> error <errorCode> detected"."""
    kohler.hub_errors = [
        {"errorCode": "0", "title": "Cleared", "component": "hub"},
        {"errorCode": "12", "title": "Valve 1", "component": "valve1"},
    ]
    await setup(hass, config_entry)

    problem = state(hass, PROBLEM)
    assert problem.state == STATE_ON
    assert problem.attributes["active_errors"] == [
        {"code": "12", "title": "Valve 1", "component": "valve1"}
    ]


async def test_problem_reports_a_fitted_accessory_that_dropped_off(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """The accessory's own entities vanish with `parts`; this is what says so."""
    kohler.hub_configuration["parts"]["steam"] = "NotConnected"
    await setup(hass, config_entry)

    problem = state(hass, PROBLEM)
    assert problem.state == STATE_ON
    assert problem.attributes["disconnected"] == ["steam"]


async def test_problem_follows_the_controllers_fault_flags(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    kohler.hub_state = {"state": {}, "errorState": "0", "errorComponent": {"hub": "0"}}
    await setup(hass, config_entry)
    assert state(hass, PROBLEM).state == STATE_OFF

    await hub(
        hass, "MUSIC_STS", {"component": "amplifier", "status": "ON", "errorstate": "1"}
    )

    problem = state(hass, PROBLEM)
    assert problem.state == STATE_ON
    assert problem.attributes["error_components"] == ["amplifier"]


async def test_controller_firmware_status(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    kohler.firmware_parts = {
        "hub": {
            "currentFirmware": "2.88",
            "firmware": "2.90",
            "firmwareUpdateAvailable": True,
            "mandatoryUpdate": True,
        }
    }
    await setup(hass, config_entry)

    update = state(hass, "update.anthem_plus_firmware_status")
    assert update.state == STATE_ON
    assert (
        update.attributes["installed_version"],
        update.attributes["latest_version"],
        update.attributes["mandatory_update"],
    ) == ("2.88", "2.90", True)


async def test_the_last_update_sensor_carries_the_controllers_own_time(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Every controller message counts, decoded or not: it is proof of life."""
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{HUB_ID}_last_update",
        config_entry=config_entry,
        suggested_object_id="anthem_plus_last_update",
    )
    await setup(hass, config_entry)
    before = dt_util.utcnow()

    await hub(hass, "STATUS_SNAPSHOT")

    reported = dt_util.parse_datetime(state(hass, entity.entity_id).state)
    assert reported >= before - timedelta(seconds=1)
