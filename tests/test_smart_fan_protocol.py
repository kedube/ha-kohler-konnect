"""The Smart Fan's protocol, offline: what goes on the wire, and what a message changes.

Bodies are checked against the Konnect app's own (`docs/protocol/smart_fan.md`), since no
fan has been seen to answer them.
"""

from __future__ import annotations

from custom_components.kohler_konnect.konnect.client import KohlerClient
from custom_components.kohler_konnect.konnect.smart_fan import (
    SETTINGS_FIELDS,
    apply_status,
    parse_favorites,
    settings_request,
)

IDENTITY = {"deviceId": "sfc-1234", "tenantId": "tenant", "sku": "SFC"}

FAVORITES = {
    "experiences": [
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
            "favType": "light",
            "isDefault": False,
            "light": {
                "brightness": "70",
                "circadianEnabled": "DISABLE",
                "color": "COOL",
            },
        },
        {"id": "fav-3", "title": "Glow", "favType": "NIGHTLIGHT", "isDefault": False},
        {"title": "No id", "favType": "FAN"},
        "not a favorite",
    ]
}


def test_favorites_are_read_defensively_and_the_night_light_left_out() -> None:
    favorites = parse_favorites(FAVORITES)
    assert [(f.id, f.kind, f.is_default) for f in favorites] == [
        ("DEFAULT-FAN", "FAN", True),
        ("DEFAULT-LIGHT", "LIGHT", True),
        ("fav-1", "FAN", False),
        ("fav-2", "LIGHT", False),
    ]
    assert parse_favorites(None) == []
    assert parse_favorites({"experiences": "garbage"}) == []


def test_each_favorite_is_started_as_the_app_starts_it() -> None:
    builtin_fan, builtin_light, steamy, morning = parse_favorites(FAVORITES)
    assert builtin_fan.command("ON") == (
        "fancontrol",
        {"id": "DEFAULT-FAN", "status": "ON"},
    )
    assert builtin_light.command("OFF") == (
        "lightcontrol",
        {"id": "DEFAULT-LIGHT", "status": "OFF"},
    )
    # A saved favorite carries its own values, under the command's names (§3.2).
    assert steamy.command("ON") == (
        "fanfavoritecontrol",
        {
            "id": "fav-1",
            "status": "ON",
            "speed": "HIGH",
            "timer": "ENABLE",
            "time": "40",
        },
    )
    assert morning.command("ON") == (
        "lightfavoritecontrol",
        {
            "id": "fav-2",
            "status": "ON",
            "circadianMode": "DISABLE",
            "color": "COOL",
            "brightnessLevel": "70",
        },
    )


def test_the_defaults_are_written_whole_under_the_commands_names() -> None:
    stored = {field: f"<{field}>" for field in SETTINGS_FIELDS}
    stored["fanScheduleCreated"] = True

    body = settings_request(IDENTITY, stored, {"fanSpeed": "LOW"})

    # Identity, then all 31 defaults: 34 fields, as the app sends.
    assert len(body) == 34
    assert body["fanSpeed"] == "LOW"
    # Four are named differently on the way back (§3.4)…
    assert body["color"] == "<lightColor>"
    assert body["nightLightBrightnessLevel"] == "<nightLightBrightness>"
    assert body["nlsBrightness"] == "<nlsLightBrightness>"
    assert body["fsEndTimeHour"] == "<fsEndTimeHr>"
    assert "lightColor" not in body
    # …the night-light start keeps its own value, not the end's, as the app's Fan
    # Automation screen sends…
    assert body["nlsStartTimeAmPm"] == "<nlsStartTimeAmPm>"
    # …and what is only read is not written.
    assert "fanScheduleCreated" not in body


def test_a_default_never_read_is_left_out_rather_than_sent_empty() -> None:
    body = settings_request(IDENTITY, {"fanSpeed": "HIGH"}, {"buzzerSetting": "ENABLE"})
    assert body == {**IDENTITY, "fanSpeed": "HIGH", "buzzerSetting": "ENABLE"}


def test_a_status_message_updates_only_what_it_carries() -> None:
    favorites = parse_favorites(FAVORITES)
    state = {"fanStatus": "OFF", "lightStatus": "ON", "lightColor": "WARM"}

    updated = apply_status(
        state,
        {
            "fanstatus": "ON",
            "fanspeed": "HIGH",
            "lightcolour": "COOL",
            "favoriteid": "fav-1",
        },
        favorites,
    )

    assert updated == {
        "fanStatus": "ON",
        "fanSpeed": "HIGH",
        "lightStatus": "ON",
        "lightColor": "COOL",
        "runningFanFavorite": "fav-1",
    }
    assert state["fanStatus"] == "OFF"


def test_the_message_names_what_runs() -> None:
    favorites = parse_favorites(FAVORITES)
    running = {"runningFanFavorite": "fav-1", "runningLightFavorite": "fav-2"}

    light = apply_status({}, {"favoriteid": "fav-2"}, favorites)
    assert light == {"runningLightFavorite": "fav-2"}
    builtin = apply_status(running, {"favoriteid": "DEFAULT-FAN"}, favorites)
    assert builtin["runningFanFavorite"] == "DEFAULT-FAN"
    # "0" names nothing in particular, as from the remote. As in the app, a part that is
    # on keeps its favorite while the message shows that favorite's values, and
    # otherwise runs its built-in; a part that is off keeps what it had.
    same = {
        "favoriteid": "0",
        "fanstatus": "ON",
        "fanspeed": "HIGH",
        "lightstatus": "ON",
        "lightcolour": "COOL",
        "lightbrightnesslevel": "70",
    }
    kept = apply_status(running, same, favorites)
    assert (kept["runningFanFavorite"], kept["runningLightFavorite"]) == (
        "fav-1",
        "fav-2",
    )
    changed = apply_status(
        running, {**same, "fanspeed": "LOW", "lightcolour": "WARM"}, favorites
    )
    assert (changed["runningFanFavorite"], changed["runningLightFavorite"]) == (
        "DEFAULT-FAN",
        "DEFAULT-LIGHT",
    )
    off = apply_status(
        running,
        {"favoriteid": "0", "fanstatus": "OFF", "lightstatus": "OFF"},
        favorites,
    )
    assert (off["runningFanFavorite"], off["runningLightFavorite"]) == (
        "fav-1",
        "fav-2",
    )
    night = apply_status(
        running,
        {"favoriteid": "0", "lightstatus": "OFF", "nightlightstatus": "ON"},
        favorites,
    )
    assert night["runningLightFavorite"] == "DEFAULT-NIGHTLIGHT"
    # A favorite not read yet changes nothing.
    assert apply_status(running, {"favoriteid": "fav-9"}, favorites) == running


def test_smart_fan_paths_reach_logs_without_the_device_id() -> None:
    for path in (
        "/devices/api/v1/device-management/sfc-state/sfc-1234",
        "/devices/api/v1/device-management/sfc-experience/sfc-1234",
        "/platform/api/v1/firmware/sfc/sfc-1234",
    ):
        assert "sfc-1234" not in KohlerClient.safe_path(path)
