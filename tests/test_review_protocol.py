"""Regressions from the 2026-10-16 review of the protocol layer.

Each test pins one finding: a message, read or write that used to be dropped, crash, or go
out wrong. All pure — no Home Assistant, no network — so a failure points straight at the
decode or the body rather than at the harness.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.kohler_konnect.konnect.client import KohlerError
from custom_components.kohler_konnect.konnect.const import (
    MSG_GCS_SOLO_STATUS,
    MSG_HUB_FAVORITE,
    MSG_HUB_SHOWER_VALVE,
)
from custom_components.kohler_konnect.konnect.gcs import GcsDevice
from custom_components.kohler_konnect.konnect.hub import (
    HubCapabilities,
    HubDevice,
    HubSettings,
    outlet_flags,
)
from custom_components.kohler_konnect.konnect.models import (
    get_valve_model,
    model_for_topology,
)
from custom_components.kohler_konnect.konnect.mqtt import Envelope
from custom_components.kohler_konnect.konnect.state import (
    GcsState,
    HubState,
    OutletLimits,
)
from custom_components.kohler_konnect.konnect.topology import (
    topology_from_hub_configuration,
    topology_from_valve_settings,
)
from custom_components.kohler_konnect.konnect.valve_hex import (
    UNUSED_VALVE_WORD,
    ValveHexError,
)

SINGLE = get_valve_model("K-28210")  # 3 outlets, one zone
DOUBLE = get_valve_model("K-28212")  # 3 + 3


def gcs(code: str, *attributes: dict[str, Any]) -> Envelope:
    return Envelope("GCS", "gcs-test0001", code, list(attributes), 1000.0)


def hub(code: str, *attributes: dict[str, Any]) -> Envelope:
    return Envelope(
        "HUB",
        "hub-test0001",
        code,
        list(attributes),
        1000.0,
        raw={"data": {"code": code}},
    )


class _Client:
    """Records every request; the stand-in `GcsDevice`/`HubDevice` talk to."""

    tenant_id = "tenant-guid"

    def __init__(self, payload: Any = None) -> None:
        self.sent: list[tuple[str, str, Any]] = []
        self.payload = payload

    async def async_tenant_id(self) -> str:
        return self.tenant_id

    async def async_request(self, method: str, path: str, json_body: Any = None):
        self.sent.append((method, path, json_body))
        return {"correlationId": "x"}

    async def async_get_gcs_presets(self, device_id: str) -> Any:
        return self.payload


# --------------------------------------------------------------------------- #
# 1. A bad valve word no longer costs the rest of the message
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "words",
    [
        {},  # no word at all
        {"primaryValve1": "nonsense"},
        {"primaryValve1": None, "secondaryValve1": None},
    ],
)
def test_a_fault_announced_without_a_valve_word_still_reaches_the_problem_sensor(
    words,
):
    state = GcsState(DOUBLE)
    state.apply_envelope(
        gcs(
            MSG_GCS_SOLO_STATUS,
            {
                "code": MSG_GCS_SOLO_STATUS,
                "primaryValve1": "0184C801",
                "secondaryValve1": "1184C802",
            },
        )
    )
    before = (state.valve1, state.valve2)
    assert state.has_fault is False

    state.apply_envelope(
        gcs(
            MSG_GCS_SOLO_STATUS,
            {
                "code": MSG_GCS_SOLO_STATUS,
                "currentSystemState": "error",
                "warmUpStatus": "warmUpInProgress",
                "presetOrExperienceId": "3",
                "totalFlow": "2056.0",
                **words,
            },
        )
    )
    assert state.has_fault is True
    assert state.warmup_in_progress is True
    assert state.active_preset_id == 3
    assert state.total_flow == 2056.0
    # The words themselves keep the last good state; neither zone is blanked.
    assert (state.valve1, state.valve2) == before


def test_a_good_second_word_is_applied_when_the_first_is_garbled():
    """Each word is its own zone's answer; a garbled one keeps only its own zone.

    A pair-or-nothing rule was tried and undone on 2026-10-16; see `GcsState._apply_solo`
    and `tests/test_review2_protocol.py`.
    """
    state = GcsState(DOUBLE)
    state.apply_envelope(
        gcs(
            MSG_GCS_SOLO_STATUS,
            {"primaryValve1": "0184C801", "secondaryValve1": "1184C800"},
        )
    )
    state.apply_envelope(
        gcs(
            MSG_GCS_SOLO_STATUS,
            {"primaryValve1": "garbage!", "secondaryValve1": "1184C804"},
        )
    )
    assert state.valve1.outlet_mask == 1
    assert state.valve2.outlet_mask == 4


# --------------------------------------------------------------------------- #
# 2. Flow bounds for a zone the model no longer has
# --------------------------------------------------------------------------- #
def test_flow_bounds_for_a_zone_the_shrunk_model_lacks_fall_back_to_the_constants():
    state = GcsState(DOUBLE)
    state.outlet_limits[3] = OutletLimits(3, 40, 180)
    assert state.zone_flow_limits(2) == (40, 180)
    # The valve's own settings reported one zone; a zone 2 entity still asks.
    state.model = SINGLE
    assert state.zone_flow_limits(2) == (16, 200)
    assert state.zone_flow_limits(3) == (16, 200)


# --------------------------------------------------------------------------- #
# 3. Wrong-shaped replies read as no answer instead of raising
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("setting", [[{"valve": "Valve1"}], "x", 5])
def test_valve_settings_of_the_wrong_shape_detect_nothing(setting):
    assert topology_from_valve_settings(setting) is None


@pytest.mark.parametrize(
    "configuration",
    [
        {"zoneone": "3"},
        {"zoneone": ["3"]},
        [{"zoneone": {"configuredoutlets": 3}}],
        "x",
    ],
)
def test_controller_configuration_of_the_wrong_shape_detects_nothing(configuration):
    assert topology_from_hub_configuration(configuration) is None


def test_a_wrong_shaped_zone_two_counts_as_absent():
    # And absent is no answer, not "single zone" (2026-10-16): see `konnect/topology.py`.
    configuration = {"zoneone": {"configuredoutlets": 2}, "zonetwo": "3"}
    assert topology_from_hub_configuration(configuration) is None


@pytest.mark.parametrize(
    "configuration",
    [{"parts": ["valve1", "Connected"]}, {"parts": "Connected"}, ["parts"], "x"],
)
def test_capabilities_from_a_wrong_shaped_reply_are_unread_not_empty(configuration):
    """Unread lets the platforms create accessory entities; "empty" would hide them."""
    caps = HubCapabilities.from_configuration(configuration)
    assert caps == HubCapabilities()
    assert caps.known is False


@pytest.mark.parametrize("payload", [[{"presetId": "1"}], "x", 5])
def test_a_preset_read_of_the_wrong_shape_reads_nothing(payload):
    state = GcsState(SINGLE)
    assert state.apply_preset_list(payload) is False
    assert state.presets == {}


def test_a_preset_whose_valve_details_are_not_a_list_is_read_without_them():
    record = {"presetId": "1", "title": "Default", "time": "0", "valveDetails": 5}
    state = GcsState(SINGLE)
    state.apply_preset_list({"gcsPresetExperienceDetails": [record]})
    assert (state.presets[1].name, state.presets[1].valve_words) == ("Default", ())


# --------------------------------------------------------------------------- #
# 5. Detected outlet counts outside 0-3 are rejected
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("first", "second"),
    [(3, "-1"), (4, 0), (3, 4), ("7", "3")],
)
def test_a_valve_reporting_an_impossible_outlet_count_detects_nothing(first, second):
    setting = {
        "valveSettings": [
            {"valve": "Valve1", "noOfOutlets": first},
            {"valve": "Valve2", "noOfOutlets": second},
        ]
    }
    assert topology_from_valve_settings(setting) is None


@pytest.mark.parametrize(("first", "second"), [(4, 0), (3, -1), (2, 4)])
def test_a_controller_reporting_an_impossible_outlet_count_detects_nothing(
    first, second
):
    configuration = {
        "zoneone": {"configuredoutlets": first},
        "zonetwo": {"configuredoutlets": second},
    }
    assert topology_from_hub_configuration(configuration) is None


def test_every_split_a_valve_can_have_is_still_detected():
    for first in (1, 2, 3):
        for second in (0, 1, 2, 3):
            setting = {
                "valveSettings": [
                    {"valve": "Valve1", "noOfOutlets": first},
                    {"valve": "Valve2", "noOfOutlets": second},
                ]
            }
            assert topology_from_valve_settings(setting) == (first, second)
            # And the model it builds can encode a write for every outlet.
            model = model_for_topology(first, second)
            assert model.total_outlets == first + second


# --------------------------------------------------------------------------- #
# 6. `parts` is compared case-insensitively
# --------------------------------------------------------------------------- #
def test_capabilities_read_connected_in_any_case():
    caps = HubCapabilities.from_configuration(
        {
            "parts": {
                "valve1": "connected",
                "amplifier": " CONNECTED ",
                "light": "Connected",
                "steam": "notconnected",
            }
        }
    )
    assert (caps.water, caps.music, caps.light, caps.steam, caps.known) == (
        True,
        True,
        True,
        False,
        True,
    )


# --------------------------------------------------------------------------- #
# 7. A late OFF for another favorite does not reset the running one
# --------------------------------------------------------------------------- #
def test_a_late_stop_for_the_previous_favorite_leaves_the_running_one_shown():
    state = HubState(SINGLE)
    state.apply_envelope(
        hub(MSG_HUB_FAVORITE, {"id": "2", "name": "Steam", "status": "ON"})
    )
    state.apply_envelope(
        hub(MSG_HUB_FAVORITE, {"id": "1", "name": "Hair Wash", "status": "OFF"})
    )
    assert (state.active_favorite_id, state.active_favorite_name) == ("2", "Steam")

    # Its own stop still clears it.
    state.apply_envelope(hub(MSG_HUB_FAVORITE, {"id": 2, "status": "OFF"}))
    assert (state.active_favorite_id, state.active_favorite_name) == (None, None)


def test_a_stop_naming_nothing_running_clears_whatever_shows():
    state = HubState(SINGLE, active_favorite_id="2", active_favorite_name="Steam")
    state.apply_envelope(hub(MSG_HUB_FAVORITE, {"id": "0", "status": "OFF"}))
    assert state.active_favorite_id is None
    state = HubState(SINGLE)
    state.apply_envelope(hub(MSG_HUB_FAVORITE, {"id": "1", "status": "OFF"}))
    assert state.active_favorite_id is None


# --------------------------------------------------------------------------- #
# 8. Hardening
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["Infinity", "-inf", "1e400", "nan"])
def test_controller_settings_survive_a_number_too_big_for_an_int(value):
    settings = HubSettings.from_configuration(
        {
            "systemSettings": {"maxShowerDuration": value},
            "steamSettings": {"defaultTime": value},
        }
    )
    assert settings.max_shower_duration_minutes is None
    assert settings.steam_default_time is None


def test_a_string_outlet_array_means_what_the_same_numbers_do():
    """`bool("0")` is True: every outlet in the zone read as running."""
    assert outlet_flags(["0", "1", "0", "0", "0", "0"], 3) == [False, True, False]
    assert outlet_flags([" 1 ", "true", "x"], 3) == [True, True, False]
    assert outlet_flags([0, 1, True], 3) == [False, True, True]

    state = HubState(SINGLE)
    state.apply_envelope(
        hub(MSG_HUB_SHOWER_VALVE, {"zone": "1", "status": "ON", "outlets": ["0", "1"]})
    )
    assert state.zones[1].outlets == [False, True, False]
    state.apply_rest_state(
        {"state": {"shower": [{"zone": "1", "outlets": ["1", "0", "0"]}]}}
    )
    assert state.zones[1].outlets == [True, False, False]


def test_a_lowercase_zone_status_still_reads_as_running():
    state = HubState(SINGLE)
    state.apply_envelope(
        hub(MSG_HUB_SHOWER_VALVE, {"zone": "1", "status": "on", "outlets": [1]})
    )
    assert state.zones[1].status == "ON"
    assert state.is_running
    state = HubState(SINGLE)
    state.apply_rest_state({"state": {"shower": [{"zone": "1", "status": " on "}]}})
    assert state.is_running


def test_an_explicit_mask_write_sends_the_ignore_word_for_a_valve_the_model_lacks():
    client = _Client()
    device = GcsDevice(client, "gcs-x", "Celsius", SINGLE)  # type: ignore[arg-type]
    asyncio.run(device.async_set_outlet_mask(0b101, 0, 38))
    model = client.sent[0][2]["gcsValveControlModel"]
    assert model["primaryValve1"] == "017CC805"
    assert model["secondaryValve1"] == UNUSED_VALVE_WORD

    # A mask for that valve is a caller asking for an outlet it does not have.
    with pytest.raises(ValveHexError, match="one zone"):
        asyncio.run(device.async_set_outlet_mask(0b001, 0b001, 38))
    assert len(client.sent) == 1


FULL = OutletLimits(0, 16, 200, 1800, 200, 31, 477, 150, 388, 1)


@pytest.mark.parametrize(
    ("limits", "overrides", "field"),
    [
        # A scald limit above the app's ceiling, with the default safely below it — the
        # one case the relative `default > maximum` check let through.
        (FULL, {"maximum_temperature_tenths": 1023}, "maximumOutletTemperature"),
        (FULL, {"maximum_temperature_tenths": 489}, "maximumOutletTemperature"),
        (FULL, {"default_temperature_tenths": -5}, "defaultOutletTemperature"),
        # Echoed from a corrupt read, not chosen: still refused.
        (
            OutletLimits(0, 16, 200, 1800, 200, 31, 900, 150, 388, 1),
            {"maximum_run_time": 900},
            "maximumOutletTemperature",
        ),
        (
            OutletLimits(0, 16, 255, 1800, 200, 31, 477, 150, 388, 1),
            {"maximum_run_time": 900},
            "maximumFlowrate",
        ),
        (
            OutletLimits(0, 0, 200, 1800, 200, 31, 477, 150, 388, 1),
            {},
            "minimumFlowrate",
        ),
        (
            OutletLimits(0, 16, 200, 1800, 4, 31, 477, 150, 388, 1),
            {},
            "defaultFlowrate",
        ),
        (
            OutletLimits(0, 16, 200, 1800, 200, 31, 477, 600, 388, 1),
            {},
            "minimumOutletTemperature",
        ),
    ],
)
def test_an_outlet_write_outside_the_absolute_ranges_is_refused_before_sending(
    limits, overrides, field
):
    client = _Client()
    device = GcsDevice(client, "gcs-x", "Celsius", SINGLE)  # type: ignore[arg-type]
    with pytest.raises(KohlerError, match=field):
        asyncio.run(device.async_write_outlet_config(limits, **overrides))
    assert client.sent == []


def test_an_outlet_write_at_the_edges_of_the_ranges_is_sent():
    client = _Client()
    device = GcsDevice(client, "gcs-x", "Celsius", SINGLE)  # type: ignore[arg-type]
    asyncio.run(
        device.async_write_outlet_config(
            FULL, maximum_temperature_tenths=488, default_temperature_tenths=488
        )
    )
    model = client.sent[0][2]["gcsOutletConfigControlModel"]
    assert model["maximumOutletTemperature"] == "488"
    assert (model["minimumFlowrate"], model["maximumFlowrate"]) == ("16", "200")


# --------------------------------------------------------------------------- #
# 9. A favorite leaves out a zone with nothing open, as its docstring says
# --------------------------------------------------------------------------- #
def _hub() -> HubDevice:
    return HubDevice(_Client(), "hub-x")  # type: ignore[arg-type]


def test_a_favorite_body_leaves_out_a_zone_with_no_outlet_open():
    zones = HubDevice.zones_for(DOUBLE, [True, False, False, False, False, False], 100)
    body = _hub()._favorite_body("Rinse", **zones, steam=None, music=None, light=None)
    assert body["water"] == {
        "zone1": {"temperature": 100, "flowrate": 100, "outlets": [0]}
    }


def test_a_favorite_with_no_outlet_open_anywhere_carries_no_water():
    """So a steam-only favorite built through `zones_for` is not refused as both."""
    zones = HubDevice.zones_for(DOUBLE, [False] * 6, 100)
    steam = {"temperature": 110, "time": 15}
    body = _hub()._favorite_body("Steam", **zones, steam=steam, music=None, light=None)
    assert "water" not in body
    assert body["steam"] == steam
