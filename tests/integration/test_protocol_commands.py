"""What reaches Kohler's API for each valve and controller command, and how replies are read.

Run through the real `KohlerClient` over Home Assistant's aiohttp mock, so every assertion is
about the request on the wire — method, path and JSON body — and the exception a reply turns
into. `docs/protocol/gcs_valve.md` §3 and `docs/protocol/hub_controller.md` §3-4 are the
reference; several of these bodies have shapes that silently no-op when wrong.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.kohler_konnect.konnect import (
    DeviceOffline,
    DeviceRunning,
    GcsDevice,
    HubDevice,
    KohlerAuth,
    KohlerClient,
    KohlerError,
    UnexpectedResponse,
    ValveHexError,
    get_valve_model,
    model_for_topology,
    topology_from_valve_settings,
)
from custom_components.kohler_konnect.konnect.const import (
    API_BASE,
    APIM_SUBSCRIPTION_KEY,
)
from custom_components.kohler_konnect.konnect.state import OutletLimits

from .conftest import DEVICE_ID, TENANT_ID, VALVE_ID, FakeKohler, make_jwt

HUB_ID = "hub-plus0001"
DEVICES = "/devices/api/v1/device-management"
COMMANDS = "/platform/api/v1/commands"
GCS = {"deviceId": VALVE_ID, "sku": "GCS", "tenantId": TENANT_ID}
HUB = {"deviceId": HUB_ID, "sku": "HUB", "tenantId": TENANT_ID}
IGNORED_SLOTS = {f"secondaryValve{n}": "00000000" for n in range(2, 8)}


@pytest.fixture
def client(hass: HomeAssistant, kohler: FakeKohler) -> KohlerClient:
    """Built as the coordinator builds it: with the tenant id the entry stored."""
    session = async_get_clientsession(hass)
    return KohlerClient(session, KohlerAuth(session, "refresh-0"), TENANT_ID)


@pytest.fixture
def valve(client: KohlerClient) -> GcsDevice:
    return GcsDevice(client, VALVE_ID, "Celsius", "K-28212")


@pytest.fixture
def hub(client: KohlerClient) -> HubDevice:
    return HubDevice(client, HUB_ID)


def sent(kohler: FakeKohler) -> list[tuple[str, str, Any]]:
    """Every API request so far as `(METHOD, path, JSON body)`; token requests excluded."""
    return [
        (method.upper(), url.path, data)
        for method, url, data, _ in kohler.mocker.mock_calls
        if str(url).startswith(API_BASE)
    ]


def _solo(primary: str, secondary: str) -> tuple[str, str, dict[str, Any]]:
    return (
        "POST",
        f"{COMMANDS}/gcs/solowritesystem",
        {
            **GCS,
            "gcsValveControlModel": {
                "primaryValve1": primary,
                "secondaryValve1": secondary,
                **IGNORED_SLOTS,
            },
        },
    )


# --------------------------------------------------------------------------- #
# Anthem valve: direct writes
# --------------------------------------------------------------------------- #
async def test_a_valve_write_upper_cases_both_words_and_zeroes_the_six_unused_slots(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    """Eight slots, always: the payload must carry the unused ones, zeroed."""
    await valve.async_write_valves("017cc807", "117cc800")
    assert sent(kohler) == [_solo("017CC807", "117CC800")]


async def test_outlet_three_of_a_four_outlet_valve_is_valve_twos_first_outlet(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    """A K-28211 splits 2+2, so outlet 3 is NOT valve 1's third bit.

    100 °F goes through Kohler's own table to 377 tenths (0x179) — not the 378 arithmetic
    would give — so the setpoint lands where the touchscreen would put it.
    """
    valve = GcsDevice(client, VALVE_ID, "Fahrenheit", "K-28211")
    await valve.async_turn_on([False, False, True, False], 100)
    assert sent(kohler) == [_solo("0179C800", "1179C801")]


async def test_a_single_zone_valve_sends_the_ignore_word_for_the_valve_it_lacks(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    valve = GcsDevice(client, VALVE_ID, "Celsius", get_valve_model("K-28210"))
    await valve.async_turn_on([True, False, True], 38, flow_percent=100)
    assert sent(kohler) == [_solo("017CC805", "00000000")]


async def test_the_wrong_number_of_outlet_flags_is_refused_before_anything_is_sent(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    with pytest.raises(ValveHexError, match="6 outlets, got 3"):
        await valve.async_turn_on([True, False, False], 38)
    assert sent(kohler) == []


async def test_turning_off_stops_both_valves_with_mask_zero_under_a_valid_prefix(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    """An all-zero `primaryValve1` is ignored — the "turns on but never off" bug."""
    await valve.async_turn_off()
    assert sent(kohler) == [_solo("017CC800", "117CC800")]
    assert sent(kohler)[0][2]["gcsValveControlModel"]["primaryValve1"] != "00000000"


async def test_pausing_sets_the_pause_flag_with_no_outlets(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    await valve.async_pause(40)
    assert sent(kohler) == [_solo("0190C840", "1190C840")]


async def test_explicit_outlet_masks_go_out_under_each_valves_own_prefix(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    await valve.async_set_outlet_mask(0b011, 0b100, 40, flow_percent=50)
    assert sent(kohler) == [_solo("01906403", "11906404")]


async def test_a_valve_with_an_uncatalogued_split_is_still_driven_outlet_for_outlet(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    """From the valve's own settings read to the word: a 1+1 valve's second outlet is
    valve 2's first bit, not valve 1's second."""
    kohler.valve_outlets = (1, 1)
    model = model_for_topology(
        *topology_from_valve_settings(await client.async_get_gcs_settings(VALVE_ID))
    )
    assert (model.sku, model.total_outlets) == ("detected", 2)

    await GcsDevice(client, VALVE_ID, "Celsius", model).async_turn_on([False, True], 38)
    assert sent(kohler)[-1] == _solo("017CC800", "117CC801")


async def test_restart_posts_valvereset_with_the_apps_product_restart_body(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    await valve.async_restart()
    assert sent(kohler) == [
        ("POST", f"{COMMANDS}/gcs/valvereset", {**GCS, "reset": "productRestart"})
    ]


# --------------------------------------------------------------------------- #
# Anthem valve: presets, experiences, warmup and outlet settings
# --------------------------------------------------------------------------- #
async def test_a_preset_or_experience_starts_with_preset_and_action_alone(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    """`presetOrExperienceId` in this body is accepted and then ignored by the cloud."""
    await valve.async_activate_preset(3)
    await valve.async_activate_preset(3, on=False)
    await valve.async_activate_preset(17)  # experiences share the id space, from 17
    path = f"{COMMANDS}/gcs/controlpresetorexperience"
    assert sent(kohler) == [
        ("POST", path, {**GCS, "preset": "3", "action": "On"}),
        ("POST", path, {**GCS, "preset": "3", "action": "Off"}),
        ("POST", path, {**GCS, "preset": "17", "action": "On"}),
    ]


async def test_editing_a_preset_wraps_the_whole_record_and_zero_fills_unused_valves(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    """Omit the wrapper or the `presetId` and the cloud answers success and does nothing."""
    await valve.async_write_preset(2, "Morning", {1: "1190C8"}, time_seconds=900)
    assert sent(kohler) == [
        (
            "POST",
            f"{COMMANDS}/gcs/writepreset",
            {
                **GCS,
                "gcsPresetControlModel": {
                    "presetId": "2",
                    "name": "Morning",
                    "time": "900",
                    "volume": "0",
                    "valve1": "1190c8",
                    **{f"valve{n}": "000000" for n in range(2, 9)},
                },
            },
        )
    ]


async def test_creating_a_preset_is_flat_with_no_wrapper_and_no_preset_id(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    await valve.async_create_preset("Evening", {2: "0589C8"}, time_seconds=600)
    assert sent(kohler) == [
        (
            "POST",
            f"{COMMANDS}/gcs/createpreset",
            {
                **GCS,
                "name": "Evening",
                "time": "600",
                "volume": "0",
                "valve1": "000000",
                "valve2": "0589c8",
                **{f"valve{n}": "000000" for n in range(3, 9)},
            },
        )
    ]


async def test_a_preset_timer_sync_reads_once_and_writes_back_all_but_the_time(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    """`writepreset` replaces the record, so a sync that dropped the name renames it to ""."""
    record = {
        "presetId": "1",
        "title": "Default shower",
        "time": "1800",
        "volume": "12",
        "valveDetails": [
            {"valveIndex": "Valve1", "hexString": "018448"},
            {"valveIndex": "Valve2", "hexString": "05849C"},
        ],
    }
    kohler.fail_api(
        f"/gcs-preset/{VALVE_ID}", (200, {"gcsPresetExperienceDetails": [record]})
    )

    plan = await valve.async_sync_preset_timer(1, 900)

    assert plan.reason == "rewrite"
    assert sent(kohler) == [
        ("GET", f"{DEVICES}/gcs-preset/{VALVE_ID}", None),
        (
            "POST",
            f"{COMMANDS}/gcs/writepreset",
            {
                **GCS,
                "gcsPresetControlModel": {
                    "presetId": "1",
                    "name": "Default shower",
                    "time": "900",
                    "volume": "12",
                    "valve1": "018448",
                    "valve2": "05849c",
                    **{f"valve{n}": "000000" for n in range(3, 9)},
                },
            },
        ),
    ]

    # Idempotent: given a fresh read that already holds the target, nothing is sent.
    record["time"] = "900"
    plan = await valve.async_sync_preset_timer(
        1, 900, presets={"gcsPresetExperienceDetails": [record]}
    )
    assert plan.reason == "already"
    assert len(sent(kohler)) == 2


async def test_warmup_sends_the_mode_and_refuses_a_blank_one_before_sending(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    """A blank `warmUp` is answered 200 and ignored by the valve — success that isn't."""
    await valve.async_set_warmup("warmUpDisabled")
    for blank in ("", "   "):
        with pytest.raises(ValueError, match="warmUp mode is required"):
            await valve.async_set_warmup(blank)
    assert sent(kohler) == [
        ("POST", f"{COMMANDS}/gcs/warmup", {**GCS, "warmUp": "warmUpDisabled"})
    ]


async def test_an_outlet_settings_write_goes_to_writeoutletconfig_as_twelve_strings(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    limits = OutletLimits(3, 16, 200, 1800, 200, 31, 477, 150, 388, 1)
    await valve.async_write_outlet_config(limits, maximum_run_time=900)
    ((method, path, body),) = sent(kohler)
    assert (method, path) == ("POST", f"{COMMANDS}/gcs/writeoutletconfig")
    assert {k: body[k] for k in GCS} == GCS
    assert body["gcsOutletConfigControlModel"] == {
        "outLetId": "3",
        "outLetType": "31",
        "outLetFlags": "1",
        "minimumOutletTemperature": "150",
        "defaultOutletTemperature": "388",
        "maximumOutletTemperature": "477",
        "minimumFlowrate": "16",
        "defaultFlowrate": "200",
        "maximumFlowrate": "200",
        "maximumRuntime": "900",
        "maxVolume": "0",
        "purge": "",
    }


# --------------------------------------------------------------------------- #
# Anthem Plus controller
# --------------------------------------------------------------------------- #
async def test_the_controllers_direct_commands_carry_only_their_on_off_field(
    hub: HubDevice, kohler: FakeKohler
) -> None:
    await hub.async_set_shower(True)
    await hub.async_set_shower(False)
    await hub.async_set_steam(True)
    await hub.async_stop_all()
    assert sent(kohler) == [
        ("POST", f"{COMMANDS}/hub/valvecontrol", {**HUB, "valveOnOff": "ON"}),
        ("POST", f"{COMMANDS}/hub/valvecontrol", {**HUB, "valveOnOff": "OFF"}),
        ("POST", f"{COMMANDS}/hub/steamcontrol", {**HUB, "steamOnOff": "ON"}),
        ("POST", f"{COMMANDS}/hub/stopall", HUB),
    ]


async def test_activating_a_favorite_sends_its_id_as_a_string(
    hub: HubDevice, kohler: FakeKohler
) -> None:
    """Control takes the id as a string; create, edit and delete take an integer."""
    await hub.async_activate_favorite(4, "Rinse")
    await hub.async_activate_favorite(4, "Rinse", on=False)
    path = f"{COMMANDS}/hub/favorite/control"
    assert sent(kohler) == [
        ("POST", path, {**HUB, "id": "4", "name": "Rinse", "state": "ON"}),
        ("POST", path, {**HUB, "id": "4", "name": "Rinse", "state": "OFF"}),
    ]


async def test_creating_a_favorite_posts_id_zero_with_only_the_zones_the_valve_has(
    hub: HubDevice, kohler: FakeKohler
) -> None:
    """Components are omitted, not nulled — the app serialises with Gson defaults."""
    single = HubDevice.zones_for(get_valve_model("K-28210"), [True, False, True], 102)
    await hub.async_create_favorite("Rinse", **single)
    double = HubDevice.zones_for(
        get_valve_model("K-28211"), [False, True, True, False], 100, 80
    )
    await hub.async_create_favorite("Both", **double, music=HubDevice.music("Aux"))
    path = f"{COMMANDS}/hub/favorite"
    assert sent(kohler) == [
        (
            "POST",
            path,
            {
                **HUB,
                "name": "Rinse",
                "water": {
                    "zone1": {"temperature": 102, "flowrate": 100, "outlets": [0, 2]}
                },
                "id": 0,
            },
        ),
        (
            "POST",
            path,
            {
                **HUB,
                "name": "Both",
                "water": {
                    "zone1": {"temperature": 100, "flowrate": 80, "outlets": [1]},
                    "zone2": {"temperature": 100, "flowrate": 80, "outlets": [0]},
                },
                "music": {
                    "source": "Aux",
                    "songID": "",
                    "musicRepeat": "",
                    "volume": 70,
                },
                "id": 0,
            },
        ),
    ]


async def test_a_celsius_accounts_favorite_carries_whole_fahrenheit_and_no_water_for_steam(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    """Favorite temperatures are whole °F on the wire whatever the account's unit."""
    hub = HubDevice(client, HUB_ID, "Celsius")
    await hub.async_create_favorite(
        "Steam", steam={"temperature": hub.to_wire_temperature(43), "time": 15}
    )
    await hub.async_create_favorite(
        "Warm", zone1=HubDevice.zone(hub.to_wire_temperature(38), [True])
    )
    bodies = [body for _, _, body in sent(kohler)]
    assert bodies[0] == {
        **HUB,
        "name": "Steam",
        "steam": {"temperature": 109, "time": 15},
        "id": 0,
    }
    assert bodies[1]["water"] == {
        "zone1": {"temperature": 100, "flowrate": 100, "outlets": [0]}
    }


async def test_editing_and_deleting_a_favorite_use_patch_and_delete_with_an_integer_id(
    hub: HubDevice, kohler: FakeKohler
) -> None:
    kohler.fail_api("/commands/hub/favorite", (200, {}), (200, {}))
    await hub.async_edit_favorite("4", "Rinse", light=[{"name": "groupA"}])
    await hub.async_delete_favorite("4", "Rinse")
    path = f"{COMMANDS}/hub/favorite"
    assert sent(kohler) == [
        (
            "PATCH",
            path,
            {**HUB, "name": "Rinse", "light": [{"name": "groupA"}], "id": 4},
        ),
        ("DELETE", path, {**HUB, "name": "Rinse", "id": 4}),
    ]


async def test_a_favorite_holding_shower_and_steam_is_refused_before_anything_is_sent(
    hub: HubDevice, kohler: FakeKohler
) -> None:
    with pytest.raises(ValueError, match="both shower and steam"):
        await hub.async_create_favorite(
            "Both",
            zone1=HubDevice.zone(100, [True]),
            steam={"temperature": 110, "time": 10},
        )
    assert sent(kohler) == []


@pytest.mark.parametrize(
    ("category", "verb"),
    [
        ("showerExperiences", "shower"),
        ("steamExperiences", "steam"),
        ("iceShowerExperiences", "iceshower"),
    ],
)
async def test_an_experience_goes_to_the_path_for_the_category_it_was_listed_under(
    hub: HubDevice, kohler: FakeKohler, category: str, verb: str
) -> None:
    """Sending a shower experience to the steam path does not work."""
    await hub.async_control_experience("Breathe", category)
    await hub.async_control_experience("Breathe", category, on=False)
    path = f"{COMMANDS}/hub/{verb}/experience/control"
    assert sent(kohler) == [
        ("POST", path, {**HUB, "name": "Breathe", "status": "ON"}),
        ("POST", path, {**HUB, "name": "Breathe", "status": "OFF"}),
    ]


async def test_an_unknown_experience_category_is_refused_before_anything_is_sent(
    hub: HubDevice, kohler: FakeKohler
) -> None:
    with pytest.raises(ValueError, match="showerExperiences"):
        await hub.async_control_experience("Breathe", "bathExperiences")
    assert sent(kohler) == []


# --------------------------------------------------------------------------- #
# Reading Kohler's answers
# --------------------------------------------------------------------------- #
async def test_an_offline_valve_is_reported_even_inside_an_http_200(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    """Expected and transient: a `DeviceOffline` that never counts as a rejection."""
    kohler.fail_api("/gcs/solowritesystem", (200, {"statusCode": "900"}))
    with pytest.raises(DeviceOffline) as err:
        await valve.async_turn_off()
    assert err.value.code == "900"
    assert not err.value.rejected


async def test_editing_a_favorite_while_the_system_runs_is_device_running(
    hub: HubDevice, kohler: FakeKohler
) -> None:
    kohler.fail_api("/commands/hub/favorite", (400, {"statusCode": "902"}))
    with pytest.raises(DeviceRunning, match="stopall") as err:
        await hub.async_edit_favorite(4, "Rinse")
    assert isinstance(err.value, KohlerError)
    assert err.value.code == "902"


async def test_a_refusal_from_the_apps_table_is_explained_with_status_and_code(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    """Kohler's own `message` is "Something went wrong"; the code says what actually did."""
    kohler.fail_api(
        "/gcs/createpreset",
        (400, {"statusCode": "915", "message": "Something went wrong"}),
    )
    with pytest.raises(KohlerError) as err:
        await valve.async_create_preset("Eleventh", {1: "1190C8"})
    assert (err.value.status, err.value.code) == (400, "915")
    assert err.value.rejected
    assert str(err.value) == (
        f"{COMMANDS}/gcs/createpreset was refused: the maximum number of favorites or "
        "presets is already stored (statusCode 915)"
    )


async def test_a_command_throttled_with_a_503_carries_how_long_to_wait(
    valve: GcsDevice, kohler: FakeKohler
) -> None:
    kohler.fail_api("/gcs/solowritesystem", (503, {}, {"Retry-After": "30"}))
    with pytest.raises(KohlerError) as err:
        await valve.async_turn_off()
    assert (err.value.status, err.value.retry_after) == (503, 30.0)
    assert not err.value.rejected


@pytest.mark.parametrize(
    ("reply", "result"),
    [
        # 201 means accepted for delivery — the only success a command gets.
        ((201, {"correlationId": "abc"}), {"correlationId": "abc"}),
        ((200, ""), None),
        ((200, "OK"), "OK"),
    ],
)
async def test_a_successful_reply_is_handed_back_as_kohler_sent_it(
    valve: GcsDevice, kohler: FakeKohler, reply: tuple, result: Any
) -> None:
    kohler.fail_api("/gcs/valvereset", reply)
    assert await valve.async_restart() == result


async def test_every_request_carries_the_bearer_token_and_key_and_json_only_with_a_body(
    client: KohlerClient, valve: GcsDevice, kohler: FakeKohler
) -> None:
    kohler.fail_api(f"/gcs-state/{VALVE_ID}", (200, {"state": {}}))
    await client.async_get_gcs_state(VALVE_ID)
    await valve.async_turn_off()
    calls = [
        headers
        for _, url, _, headers in kohler.mocker.mock_calls
        if str(url).startswith(API_BASE)
    ]
    common = {
        "Authorization": f"Bearer {make_jwt({'oid': TENANT_ID, 'n': 1})}",
        "Ocp-Apim-Subscription-Key": APIM_SUBSCRIPTION_KEY,
        "Accept": "application/json",
    }
    assert calls == [common, {**common, "Content-Type": "application/json"}]


async def test_a_mid_request_401_is_logged_at_info_without_the_device_id(
    client: KohlerClient, kohler: FakeKohler, caplog: pytest.LogCaptureFixture
) -> None:
    """It costs seconds; a slow call must be explainable from default logs alone."""
    caplog.set_level(logging.INFO, logger="custom_components.kohler_konnect")
    kohler.fail_api(f"/gcs-state/{VALVE_ID}", (401, {}), (200, {"state": {}}))
    assert await client.async_get_gcs_state(VALVE_ID) == {"state": {}}
    (record,) = [r for r in caplog.records if "401 from" in r.getMessage()]
    assert record.levelno == logging.INFO
    assert f"{DEVICES}/gcs-state/<id>" in record.getMessage()
    assert VALVE_ID not in caplog.text


async def test_the_api_probe_log_redacts_credentials_hidden_under_innocent_keys(
    client: KohlerClient, kohler: FakeKohler, caplog: pytest.LogCaptureFixture
) -> None:
    """Users paste this log into issues; an Azure connection string is a whole secret."""
    caplog.set_level(logging.DEBUG, logger="custom_components.kohler_konnect")
    kohler.fail_api(
        "/mobile/settings",
        (
            200,
            {
                "ioTHubSettings": {
                    "ioTHub": "hub.example.net",
                    "deviceId": "mobile-x",
                    "password": "SharedAccessSignature sr=hub&sig=pw-secret",
                    "connectionString": "HostName=hub;SharedAccessKey=cs-secret",
                }
            },
        ),
    )
    settings = await client.async_register_mobile_device("ha-identity")

    # The caller still gets the real credentials; only the log is scrubbed.
    assert settings["password"].endswith("pw-secret")
    assert "pw-secret" not in caplog.text
    assert "cs-secret" not in caplog.text
    # Structure survives, so the log still shows which fields Kohler sent.
    assert "'connectionString': '**REDACTED**'" in caplog.text
    assert "hub.example.net" in caplog.text


async def test_registering_without_an_identity_makes_one_and_uses_it_as_the_handle(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    await client.async_register_mobile_device()
    (body,) = kohler.push_registrations
    identity = body["mobileDeviceId"]
    assert len(identity) == 16
    assert body["deviceHandle"] == f"ha_{identity}"
    assert body["tenantId"] == TENANT_ID


async def test_a_registration_without_iot_hub_settings_is_an_error(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    """Without a host there is nothing to connect the stream to."""
    kohler.fail_api("/mobile/settings", (200, {"ioTHubSettings": {}}))
    with pytest.raises(KohlerError, match="no IoT Hub settings"):
        await client.async_register_mobile_device("ha-identity")


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("method", "device", "route"),
    [
        ("async_get_gcs_state", VALVE_ID, f"gcs-state/{VALVE_ID}"),
        ("async_get_gcs_presets", VALVE_ID, f"gcs-preset/{VALVE_ID}"),
        ("async_get_hub_state", HUB_ID, f"hub-state/{HUB_ID}"),
        ("async_get_hub_favorites", HUB_ID, f"hub-experience/{HUB_ID}/favorites"),
        ("async_get_hub_experiences", HUB_ID, f"hub-experience/{HUB_ID}/experiences"),
        ("async_get_hub_configuration", HUB_ID, f"hub-configuration/{HUB_ID}"),
    ],
)
async def test_each_read_goes_to_its_documented_route(
    client: KohlerClient, kohler: FakeKohler, method: str, device: str, route: str
) -> None:
    kohler.fail_api(route, (200, {"read": route}))
    assert await getattr(client, method)(device) == {"read": route}
    assert sent(kohler) == [("GET", f"{DEVICES}/{route}", None)]


@pytest.mark.parametrize(
    ("call", "route"),
    [
        (lambda c: c.async_get_hub_active_errors(HUB_ID), "/active"),
        (lambda c: c.async_get_gcs_about(VALVE_ID), "/about"),
        (
            lambda c: c.async_get_gcs_diagnostics(VALVE_ID),
            f"gcs-diagnostics/{VALVE_ID}",
        ),
        (lambda c: c.async_get_firmware(VALVE_ID, "gcs"), f"gcs/{VALVE_ID}"),
        (
            lambda c: c.async_get_firmware(VALVE_ID, "gateway"),
            f"gateway/{VALVE_ID}",
        ),
        (lambda c: c.async_get_firmware(HUB_ID, "hub"), f"hub/{HUB_ID}"),
    ],
)
@pytest.mark.parametrize(
    "reply", [(500, {"message": "oops"}), (200, ["not", "a", "dict"])]
)
async def test_diagnostic_reads_answer_empty_rather_than_failing_setup(
    client: KohlerClient, kohler: FakeKohler, call: Any, route: str, reply: tuple
) -> None:
    """Device-registry detail and fault logs are never worth a broken setup."""
    kohler.fail_api(route, reply)
    assert await call(client) == {}
    assert len(sent(kohler)) == 1


async def test_diagnostic_reads_return_what_kohler_sent_when_it_is_readable(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    kohler.fail_api("/active", (200, {"errorDetails": [{"errorCode": "12"}]}))
    kohler.fail_api(f"gateway/{VALVE_ID}", (200, {"firmwareUpdateAvailable": False}))
    assert await client.async_get_hub_active_errors(HUB_ID) == {
        "errorDetails": [{"errorCode": "12"}]
    }
    assert await client.async_get_firmware(VALVE_ID, "gateway") == {
        "firmwareUpdateAvailable": False
    }
    # The firmware family keeps the documented `releasetarget` query.
    assert str(kohler.mocker.mock_calls[-1][1]).endswith(
        f"/firmware/gcs/gateway/{VALVE_ID}?releasetarget=Public"
    )


@pytest.mark.parametrize("faucet", [False, True])
async def test_usage_is_asked_for_with_pascal_case_query_and_empty_on_failure(
    client: KohlerClient, kohler: FakeKohler, faucet: bool
) -> None:
    """A camelCase query gets the same generic 400 as a bare call (platform.md §6)."""
    device = DEVICE_ID if faucet else VALVE_ID
    product = "faucet" if faucet else "gcs"
    kohler.fail_api(
        f"/{product}-usage/{device}", (200, {"rows": 1}), (400, {"message": "Bad"})
    )
    for expected in ({"rows": 1}, {}):
        usage = await client.async_get_usage(
            device,
            from_date="2026-10-01",
            to_date="2026-10-07",
            interval="DAY",
            faucet=faucet,
        )
        assert usage == expected
    url = kohler.mocker.mock_calls[-1][1]
    assert url.path == f"{DEVICES}/{product}-usage/{device}"
    assert dict(url.query) == {
        "FromDate": "2026-10-01",
        "ToDate": "2026-10-07",
        "Interval": "DAY",
    }


async def test_unreadable_replies_to_reads_entities_depend_on_are_errors(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    """An empty faucet or account must show as a failed refresh, not as an empty device."""
    kohler.fail_api(f"/faucet-configuration/{DEVICE_ID}", (200, ["junk"]))
    with pytest.raises(UnexpectedResponse):
        await client.async_get_faucet_configuration(DEVICE_ID)
    kohler.fail_api(f"/customer-device/{TENANT_ID}", (200, "<html/>"))
    with pytest.raises(KohlerError, match="customer-device"):
        await client.async_get_customer()


async def test_settings_and_configuration_reads_tolerate_a_non_object_reply(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    kohler.fail_api(f"/gcsadvancestate/{VALVE_ID}", (200, "null"), (200, {"x": 1}))
    kohler.fail_api(f"/gcs-configuration/{VALVE_ID}", (200, ["junk"]))
    assert await client.async_get_gcs_settings(VALVE_ID) == {}
    # A reply without a `setting` block is no settings, not the whole reply.
    assert await client.async_get_gcs_settings(VALVE_ID) == {}
    assert await client.async_get_gcs_configuration(VALVE_ID) == {}


async def test_a_client_with_no_tenant_learns_it_from_the_first_token(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    session = async_get_clientsession(hass)
    client = KohlerClient(session, KohlerAuth(session, "refresh-0"))
    assert client.tenant_id is None
    assert await client.async_tenant_id() == TENANT_ID
    assert client.auth.refresh_token == "refresh-1"


async def test_a_token_without_a_tenant_claim_is_an_auth_error(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    from custom_components.kohler_konnect.konnect import AuthError

    kohler.token_queue.append(
        (
            200,
            {
                "access_token": make_jwt({"name": "no ids"}),
                "refresh_token": "refresh-1",
            },
        )
    )
    session = async_get_clientsession(hass)
    client = KohlerClient(session, KohlerAuth(session, "refresh-0"))
    with pytest.raises(AuthError, match="No tenant id"):
        await client.async_get_customer()
    assert sent(kohler) == []


async def test_a_command_sent_as_a_fresh_clients_first_call_still_names_the_tenant(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    session = async_get_clientsession(hass)
    client = KohlerClient(session, KohlerAuth(session, "refresh-0"))
    await GcsDevice(client, VALVE_ID, "Celsius", "K-28212").async_turn_off()
    await HubDevice(client, HUB_ID).async_stop_all()
    assert [body["tenantId"] for _, _, body in sent(kohler)] == [TENANT_ID, TENANT_ID]
