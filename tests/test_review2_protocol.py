"""Regressions from the second 2026-10-16 review of the protocol layer.

Each test pins one finding: a reply or message that used to be read as something it does not
say — a garbled outlet count as "single zone", half a valve report as a whole one, a
degraded configuration as "no accessories" — or a number that crashed the parse. All pure:
no Home Assistant, no network.
"""

from __future__ import annotations

import asyncio
import math
import time
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from custom_components.kohler_konnect.coordinator import Valve
from custom_components.kohler_konnect.konnect.const import (
    MSG_GCS_SOLO_STATUS,
    MSG_HUB_FAVORITE,
    MSG_HUB_SHOWER_VALVE,
)
from custom_components.kohler_konnect.konnect.gcs import GcsDevice, plan_preset_timer
from custom_components.kohler_konnect.konnect.hub import HubCapabilities, outlet_flags
from custom_components.kohler_konnect.konnect.models import get_valve_model
from custom_components.kohler_konnect.konnect.mqtt import Envelope
from custom_components.kohler_konnect.konnect.state import (
    GcsState,
    HubState,
    outlet_limits_from_settings,
)
from custom_components.kohler_konnect.konnect.topology import (
    topology_from_hub_configuration,
    topology_from_valve_settings,
)
from custom_components.kohler_konnect.konnect.valve_hex import (
    VALVE1_PREFIX,
    decode_word,
    encode_word,
)

SINGLE = get_valve_model("K-28210")  # 3 outlets, one zone
DOUBLE = get_valve_model("K-28212")  # 3 + 3


def gcs(code: str, *attributes: Any) -> Envelope:
    return Envelope("GCS", "gcs-test0001", code, list(attributes), 1000.0)


def hub(code: str, *attributes: Any) -> Envelope:
    return Envelope(
        "HUB",
        "hub-test0001",
        code,
        list(attributes),
        1000.0,
        raw={"data": {"code": code}},
    )


def solo(**words: Any) -> Envelope:
    return gcs(MSG_GCS_SOLO_STATUS, {"code": MSG_GCS_SOLO_STATUS, **words})


def valve_settings(first: Any, second: Any = ...) -> dict[str, Any]:
    """A `gcsadvancestate` `setting` block; `...` leaves the `Valve2` entry out."""
    entries = [{"valve": "Valve1", "noOfOutlets": first}]
    if second is not ...:
        entries.append({"valve": "Valve2", "noOfOutlets": second})
    return {"valveSettings": entries}


# --------------------------------------------------------------------------- #
# 1. A garbled or missing zone-2 count is no answer, never "single zone"
# --------------------------------------------------------------------------- #
GARBLED = [None, "", "abc", "2.5", "Infinity", "NaN", "1e400", True, [3], {"n": 3}]


@pytest.mark.parametrize("count", GARBLED)
def test_a_garbled_valve2_count_detects_nothing(count):
    """Read as 0, it made a 3 + 3 valve a K-28210 and zone 2 unstoppable from HA."""
    assert topology_from_valve_settings(valve_settings(3, count)) is None


def test_a_valve2_entry_left_out_detects_nothing():
    """The valve lists all eight slots, so a reply without `Valve2` is a broken one."""
    assert topology_from_valve_settings(valve_settings(3)) is None
    entry = {
        "valveSettings": [{"valve": "Valve1", "noOfOutlets": 3}, {"valve": "Valve2"}]
    }
    assert topology_from_valve_settings(entry) is None


@pytest.mark.parametrize("zero", [0, "0", " 0 ", "0.0"])
def test_a_single_zone_valve_still_says_so_with_an_explicit_zero(zero):
    assert topology_from_valve_settings(valve_settings(3, zero)) == (3, 0)
    assert topology_from_valve_settings(valve_settings("2", zero)) == (2, 0)


def test_a_whole_count_written_as_a_float_is_read():
    """`"3.0"` is three; it read as no valve 1 at all, and as no zone 2."""
    assert topology_from_valve_settings(valve_settings("3.0", "3.0")) == (3, 3)
    assert topology_from_valve_settings(valve_settings(2.0, " 2.0 ")) == (2, 2)


@pytest.mark.parametrize("zone_two", [*({"configuredoutlets": c} for c in GARBLED), {}])
def test_a_garbled_controller_zone_two_count_detects_nothing(zone_two):
    configuration = {"zoneone": {"configuredoutlets": "3"}, "zonetwo": zone_two}
    assert topology_from_hub_configuration(configuration) is None


def test_a_controller_configuration_without_zone_two_detects_nothing():
    assert (
        topology_from_hub_configuration({"zoneone": {"configuredoutlets": 3}}) is None
    )
    configuration = {"zoneone": {"configuredoutlets": 3}, "zonetwo": None}
    assert topology_from_hub_configuration(configuration) is None


def test_a_controller_states_its_split_in_any_whole_number_spelling():
    configuration = {
        "zoneone": {"configuredoutlets": "3.0"},
        "zonetwo": {"configuredoutlets": 3.0},
    }
    assert topology_from_hub_configuration(configuration) == (3, 3)
    configuration = {
        "zoneone": {"configuredoutlets": "3"},
        "zonetwo": {"configuredoutlets": "0"},
    }
    assert topology_from_hub_configuration(configuration) == (3, 0)


def _valve_stand_in(model) -> SimpleNamespace:
    """What `Valve._apply_topology` touches: the model on its state and on its device."""
    return SimpleNamespace(
        name="Anthem Valve",
        model=model,
        gcs_state=GcsState(model),
        gcs=SimpleNamespace(model=model),
    )


def test_a_garbled_read_leaves_a_two_zone_valve_two_zones():
    """No latch, no shrink — so no reload, no remembered layout, no deleted devices."""
    valve = _valve_stand_in(DOUBLE)
    assert Valve._apply_topology(valve, valve_settings(3, None)) is False
    assert valve.gcs_state.model is DOUBLE
    assert valve.gcs.model is DOUBLE


# --------------------------------------------------------------------------- #
# 2. The valve words are applied as a pair or not at all
# --------------------------------------------------------------------------- #
def _overlay(state: GcsState, sent: tuple[str, str]) -> SimpleNamespace:
    """A valve whose last write sent ``sent`` over the words ``state`` holds now.

    Exactly what `Valve._async_send_words` records once its POST lands. The helpers
    `_valve_baseline` calls are the real ones, bound to this stand-in, so the overlay's own
    narrowing and warm-up rules are what is under test.
    """
    valve = SimpleNamespace(
        name="Anthem Valve",
        gcs_state=state,
        _sent_words=(decode_word(sent[0]), decode_word(sent[1])),
        _sent_over=(state.valve1, state.valve2),
        _sent_at=time.monotonic(),
        _sent_hold_seconds=5.0,
    )
    valve._warming_up = MethodType(Valve._warming_up, valve)
    valve._narrowed = Valve._narrowed  # a staticmethod: nothing to bind
    return valve


def test_a_garbled_first_word_does_not_end_the_write_overlay():
    """The next write must not rebuild zone 1 from the word before the stop.

    Zone 1 was just stopped from Home Assistant. A report whose `primaryValve1` would not
    decode used to replace valve 2 alone, which ended the overlay with zone 1's running
    word as the baseline — and the next write, say a zone 2 temperature, re-opened it.
    """
    state = GcsState(DOUBLE)
    state.apply_envelope(solo(primaryValve1="0184C801", secondaryValve1="1184C802"))
    valve = _overlay(state, ("0184C800", "1184C802"))

    state.apply_envelope(solo(primaryValve1="garbage!", secondaryValve1="1184C802"))

    word1, word2 = Valve._valve_baseline(valve)
    assert word1.outlet_mask == 0, "zone 1 would be re-opened"
    assert word2.outlet_mask == 2


def test_a_report_missing_zone_two_does_not_end_the_write_overlay():
    """Half a report is no more of an answer than a garbled one on a two-zone valve."""
    state = GcsState(DOUBLE)
    state.apply_envelope(solo(primaryValve1="0184C801", secondaryValve1="1184C802"))
    valve = _overlay(state, ("0184C801", "1184C800"))

    state.apply_envelope(solo(primaryValve1="0184C801"))

    word1, word2 = Valve._valve_baseline(valve)
    assert word2.outlet_mask == 0, "zone 2 would be re-opened"
    assert word1.outlet_mask == 1


@pytest.mark.parametrize(
    ("words", "masks", "kept"),
    [
        ({"primaryValve1": "garbage!", "secondaryValve1": "1184C804"}, (1, 4), (1,)),
        ({"primaryValve1": "0184C804", "secondaryValve1": "garbage!"}, (4, 2), (2,)),
        ({"primaryValve1": "0184C804"}, (4, 2), (2,)),
        ({"secondaryValve1": "1184C804"}, (1, 4), (1,)),
        ({"primaryValve1": "0184C804", "secondaryValve1": ""}, (4, 2), (2,)),
        ({"primaryValve1": None, "secondaryValve1": "1184C804"}, (1, 4), (1,)),
    ],
)
def test_a_two_zone_valve_takes_each_word_that_decodes(words, masks, kept):
    """A word that decodes is its zone's answer; one that does not keeps its zone as it was.

    The pair-or-nothing rule tried on 2026-10-16 is gone: the overlay narrows zone by
    zone, so half a pair is safe, and refusing the good half hid a real Stop.
    """
    state = GcsState(DOUBLE)
    state.apply_envelope(solo(primaryValve1="0184C801", secondaryValve1="1184C802"))
    before = {1: state.valve1, 2: state.valve2}

    state.apply_envelope(solo(currentSystemState="error", **words))

    assert (state.valve1.outlet_mask, state.valve2.outlet_mask) == masks
    for zone in kept:
        assert state.zone_word(zone) is before[zone]
    # The rest of the message is still the valve's own report.
    assert state.has_fault is True


def test_a_stop_at_the_panel_beside_a_garbled_word_is_not_lost():
    """The case the pair rule broke: zone 1 stopped, zone 2's word garbled.

    Dropping the whole report kept zone 1 running in Home Assistant, and the next zone 1
    write re-opened it.
    """
    state = GcsState(DOUBLE)
    state.apply_envelope(solo(primaryValve1="0184C801", secondaryValve1="1184C802"))
    state.apply_envelope(solo(primaryValve1="0184C800", secondaryValve1="garbage!"))
    assert state.valve1.outlet_mask == 0
    assert state.valve2.outlet_mask == 2


def test_a_whole_pair_still_replaces_both_words():
    state = GcsState(DOUBLE)
    state.apply_envelope(solo(primaryValve1="0184C801", secondaryValve1="1184C802"))
    state.apply_envelope(solo(primaryValve1="0184C804", secondaryValve1="1184C800"))
    assert (state.valve1.outlet_mask, state.valve2.outlet_mask) == (4, 0)


@pytest.mark.parametrize("secondary", [None, "", "garbage!", "000000", "1184C802"])
def test_a_single_zone_valve_reads_its_one_word_whatever_rides_beside_it(secondary):
    """Its `secondaryValve1` is the unused-valve word; nothing in it is about zone 1."""
    state = GcsState(SINGLE)
    state.apply_envelope(solo(primaryValve1="0184C801", secondaryValve1=secondary))
    assert state.valve1.outlet_mask == 1
    assert state.valve2 is None


# --------------------------------------------------------------------------- #
# 3. A configuration that names no parts is unread, not "no accessories"
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "configuration",
    [
        None,
        {},
        {"parts": None},
        {"parts": {}},
        {"zoneone": {"configuredoutlets": "3"}, "systemSettings": {}},
    ],
)
def test_a_degraded_configuration_leaves_capabilities_unread(configuration):
    """Latched as known, it hid Music, Light and Steam until a manual reload."""
    caps = HubCapabilities.from_configuration(configuration)
    assert caps == HubCapabilities()
    assert caps.known is False


def test_a_real_configuration_after_a_degraded_one_is_read():
    """The coordinator re-reads while `known` is False; the next real reply settles it."""
    assert HubCapabilities.from_configuration({}).known is False
    caps = HubCapabilities.from_configuration(
        {"parts": {"valve1": "Connected", "amplifier": "Connected", "steam": None}}
    )
    assert (caps.water, caps.music, caps.steam, caps.known) == (True, True, False, True)


# --------------------------------------------------------------------------- #
# 4. A preset holding a word that fails validation is never rewritten
# --------------------------------------------------------------------------- #
def _default_preset(*words: tuple[str, Any]) -> dict[str, Any]:
    return {
        "gcsPresetExperienceDetails": [
            {
                "presetId": "1",
                "title": "Default Shower",
                "time": "900",
                "valveDetails": [
                    {"valveIndex": index, "hexString": word} for index, word in words
                ],
            }
        ]
    }


class _Client:
    """Records every request; the stand-in `GcsDevice` talks to."""

    tenant_id = "tenant-guid"

    def __init__(self, payload: Any) -> None:
        self.sent: list[tuple[str, str, Any]] = []
        self.payload = payload

    async def async_tenant_id(self) -> str:
        return self.tenant_id

    async def async_request(self, method: str, path: str, json_body: Any = None):
        self.sent.append((method, path, json_body))
        return {"correlationId": "x"}

    async def async_get_gcs_presets(self, device_id: str) -> Any:
        return self.payload


@pytest.mark.parametrize(
    "bad",
    [
        "05849C01",  # a command word where a preset word belongs
        "03FFC8",  # 102.3 °C
        "zzzzzz",
        "0584",
    ],
)
def test_a_preset_with_an_invalid_stored_word_is_not_rewritten(bad):
    """Rewritten without it, that valve went out as `000000`: zone 2 left the preset."""
    payload = _default_preset(("Valve1", "018448"), ("Valve2", bad))
    plan = plan_preset_timer(payload, 1, 3600)
    assert plan.reason == "unsafe"
    assert not plan.needed
    assert plan.valves == {1: "018448"}

    client = _Client(payload)
    device = GcsDevice(client, "gcs-x", "Celsius", DOUBLE)  # type: ignore[arg-type]
    assert asyncio.run(device.async_sync_preset_timer(1, 3600)).reason == "unsafe"
    assert client.sent == []


def test_unused_valve_words_still_do_not_block_the_rewrite():
    """`000000` and an empty field are how a preset says "not this valve"."""
    payload = _default_preset(
        ("Valve1", "018448"), ("Valve2", "000000"), ("Valve3", ""), ("Valve4", None)
    )
    plan = plan_preset_timer(payload, 1, 3600)
    assert (plan.reason, plan.valves) == ("rewrite", {1: "018448"})


# --------------------------------------------------------------------------- #
# 5. A favorite switch sent as one message lands on the new favorite
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("running", [None, "1"])
def test_a_stop_and_a_start_in_one_message_show_the_new_favorite(running):
    state = HubState(SINGLE, active_favorite_id=running)
    state.apply_envelope(
        hub(
            MSG_HUB_FAVORITE,
            {"id": "1", "name": "Hair Wash", "status": "OFF"},
            {"id": "2", "name": "Steam", "status": "ON"},
        )
    )
    assert (state.active_favorite_id, state.active_favorite_name) == ("2", "Steam")


def test_a_start_after_its_own_stop_in_one_message_does_not_resurrect_it():
    state = HubState(SINGLE, active_favorite_id="1", active_favorite_name="Hair Wash")
    state.apply_envelope(
        hub(
            MSG_HUB_FAVORITE,
            {"id": "1", "status": "OFF"},
            {"id": "1", "name": "Hair Wash", "status": "ON"},
        )
    )
    assert state.active_favorite_id is None


def test_every_stop_in_one_message_is_applied():
    """A late stop for another favorite no longer hides the running one's own stop."""
    state = HubState(SINGLE, active_favorite_id="2", active_favorite_name="Steam")
    state.apply_envelope(
        hub(
            MSG_HUB_FAVORITE, {"id": "1", "status": "OFF"}, {"id": "2", "status": "OFF"}
        )
    )
    assert (state.active_favorite_id, state.active_favorite_name) == (None, None)


def test_a_late_stop_alone_still_leaves_the_running_favorite_shown():
    state = HubState(SINGLE, active_favorite_id="2", active_favorite_name="Steam")
    state.apply_envelope(hub(MSG_HUB_FAVORITE, {"id": "1", "status": "OFF"}))
    assert (state.active_favorite_id, state.active_favorite_name) == ("2", "Steam")


# --------------------------------------------------------------------------- #
# 6. Infinity and NaN read as unparseable, everywhere a valve number is parsed
# --------------------------------------------------------------------------- #
NON_FINITE = [
    "Infinity",
    "-Infinity",
    "inf",
    "1e400",
    "NaN",
    float("inf"),
    float("nan"),
]


def _outlet(**fields: Any) -> dict[str, Any]:
    record = {"outLetId": "0", "minimumFlowrate": "4", "maximumFlowrate": "50"}
    return {"valveSettings": [{"outletConfigurations": [{**record, **fields}]}]}


@pytest.mark.parametrize("value", NON_FINITE)
@pytest.mark.parametrize("key", ["minimumFlowrate", "maximumFlowrate"])
def test_an_outlet_with_a_non_finite_flow_bound_is_skipped_not_raised(key, value):
    """`round(inf)` raised `OverflowError` out of the REST seed."""
    assert outlet_limits_from_settings(_outlet(**{key: value})) == {}


@pytest.mark.parametrize("value", [*NON_FINITE, "1e308"])
@pytest.mark.parametrize(
    ("key", "field"),
    [
        ("defaultFlowrate", "default_flow_byte"),
        ("maximumOutletTemperature", "maximum_temperature_tenths"),
        ("minimumOutletTemperature", "minimum_temperature_tenths"),
        ("defaultOutletTemperature", "default_temperature_tenths"),
    ],
)
def test_a_non_finite_outlet_field_reads_as_not_learned(key, field, value):
    """`1e308` is finite until it is scaled — so the check is made after scaling."""
    limits = outlet_limits_from_settings(_outlet(**{key: value}))
    assert getattr(limits[0], field) is None
    # The rest of the record is still read.
    assert (limits[0].minimum_flow_byte, limits[0].maximum_flow_byte) == (16, 200)


def test_a_flow_bound_too_big_to_scale_is_skipped():
    assert outlet_limits_from_settings(_outlet(maximumFlowrate="1e308")) == {}


@pytest.mark.parametrize("value", [*NON_FINITE, 10**400])
def test_a_non_finite_rest_setpoint_takes_the_fallback_and_can_be_written(value):
    """A NaN setpoint was stored as read, and every write inheriting it raised."""
    state = GcsState(DOUBLE)
    state.apply_rest_state(
        {
            "state": {
                "valve1": {"out1": "1", "temperatureSetpoint": value},
                "valve2": {"out2": "1", "temperatureSetpoint": value},
            }
        }
    )
    for word in (state.valve1, state.valve2):
        assert word.temperature_celsius == 38.0
        # The write that inherits it — a flow change, an outlet toggle — encodes.
        encode_word(VALVE1_PREFIX, word.temperature_celsius, 100, word.outlet_mask)
    assert math.isfinite(state.temperature)


@pytest.mark.parametrize("value", [*NON_FINITE, 10**400, "1e308"])
def test_a_non_finite_rest_flow_takes_the_fallback(value):
    state = GcsState(SINGLE)
    state.apply_rest_state({"state": {"valve1": {"out1": "1", "flowSetpoint": value}}})
    assert state.valve1.flow_percent == 0.0


def test_ordinary_rest_numbers_read_as_before():
    state = GcsState(SINGLE)
    state.apply_rest_state(
        {
            "state": {
                "valve1": {
                    "out1": "1",
                    "temperatureSetpoint": "38.5",
                    "flowSetpoint": 25,
                }
            }
        }
    )
    assert (state.valve1.temperature_celsius, state.valve1.flow_percent) == (38.5, 50.0)
    limits = outlet_limits_from_settings(_outlet(maximumOutletTemperature="47.8"))
    assert limits[0].maximum_temperature_tenths == 478


# --------------------------------------------------------------------------- #
# 7. A controller outlet slot of "1.0" is on
# --------------------------------------------------------------------------- #
def test_a_slot_written_as_a_float_string_reads_as_its_number():
    assert outlet_flags(["1.0", "0.0", " 2.0 "], 3) == [True, False, True]


@pytest.mark.parametrize("value", ["nan", "inf", "-Infinity", "1e400", "x", ""])
def test_a_slot_that_is_no_finite_number_reads_as_off(value):
    assert outlet_flags([value], 1) == [False]


def test_a_controller_report_with_float_string_slots_shows_the_open_outlet():
    state = HubState(SINGLE)
    state.apply_envelope(
        hub(
            MSG_HUB_SHOWER_VALVE,
            {"zone": "1", "status": "ON", "outlets": ["1.0", "0.0", "0.0"]},
        )
    )
    assert state.zones[1].outlets == [True, False, False]
