"""The product decoders, offline, against values a reply or message should never hold.

A null block, a non-finite number (`Infinity`, which Python's JSON parser accepts as a
literal and `float()` as text) or an out-of-range time reads as unknown: it never raises
out of a read or a message, where one bad field would stall every reading of the device.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from custom_components.kohler_konnect.evocycle import coordinator as evo_coordinator
from custom_components.kohler_konnect.evocycle import entities as evo_entities
from custom_components.kohler_konnect.konnect import evocycle, ice_bath
from custom_components.kohler_konnect.konnect.toilet import apply_message

NON_FINITE = ["Infinity", "-Infinity", "NaN", float("inf"), float("nan"), 10**400]
NON_FINITE_IDS = ["Infinity", "-Infinity", "NaN", "inf", "nan", "huge-int"]


# --- Numi and Purewash ------------------------------------------------------------------


@pytest.mark.parametrize("block", [None, "", [], 0])
def test_a_null_ambient_block_is_read_as_empty(block: Any) -> None:
    state = {"light": {"nightLight": "Off", "ambientLight": block}}
    awake = apply_message(
        state, "NUMI_MULTIMEDIA_STS", {"multimediastatus": "MultimediaIsAwake"}
    )
    assert awake["light"]["ambientLight"] == {"state": "On"}
    lit = apply_message(
        state, "NUMI_SET_LIGHTING_PARAMETERS_STS", {"color": "Color4", "luminance": "7"}
    )
    assert lit["light"]["ambientLight"] == {"colorTheme": "Color4", "brightness": "7"}


def test_an_ambient_message_leaves_the_state_it_was_given_alone() -> None:
    state = {"light": {"nightLight": "Off", "ambientLight": {"state": "Off"}}}
    before = copy.deepcopy(state)
    apply_message(state, "NUMI_MULTIMEDIA_STS", {"multimediastatus": "Awake"})
    apply_message(state, "NUMI_SET_LIGHTING_PARAMETERS_STS", {"color": "Color1"})
    assert state == before


# --- EvoCycle -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", NON_FINITE, ids=NON_FINITE_IDS)
def test_evocycle_reads_a_non_finite_number_as_unknown(value: Any) -> None:
    state = {
        "shower": {"standardModeShower": {"showerOn": value}},
        "clean": {"rapidClean": {"cleanOn": value}},
        "deviceErrorState": {"currentDeviceState": "error", "currentErrorCode": value},
    }
    assert evocycle.activity(state) is None
    assert evocycle.fault(state) is None
    assert evocycle.default_settings({"defaultTemperature": value}, {}) is None
    assert evo_coordinator._whole(value) is None
    assert evo_entities._whole(value) is None


@pytest.mark.parametrize("value", NON_FINITE, ids=NON_FINITE_IDS)
def test_an_evocycle_message_with_a_non_finite_number_applies(value: Any) -> None:
    running = {"shower": {"reCycleModeShower": {"showerOn": 1}}}
    updated = evocycle.apply_data(
        running,
        {
            "shower": {"showerOn": value, "showerMode": 1},
            "clean": {"cleanOn": value, "cleanMode": value, "lastCleanDate": value},
            "product": {"errorCode": value},
            "valve": {"errorCode": value},
        },
        1795000000,
    )
    # Anything but `showerOn` 1 is "off", as the app reads it.
    assert evocycle.activity(updated) is None


@pytest.mark.parametrize("value", NON_FINITE, ids=NON_FINITE_IDS)
def test_evocycle_savings_read_a_non_finite_volume_as_unknown(value: Any) -> None:
    week = {"currentWeekUsageData": {"waterSaved": value, "waterUsage": value}}
    assert evocycle.week_split(week) == (None, None)
    months = {"waterUsageData": [{"intervalKey": "2026-10", "waterSaved": value}]}
    assert evocycle.savings_months(months)["2026-10"]["saved"] is None


@pytest.mark.parametrize("block", ["running", [1], 5])
def test_an_evocycle_message_tolerates_an_odd_block_in_the_state(block: Any) -> None:
    state = {
        "shower": {"standardModeShower": block},
        "clean": {"rapidClean": block},
    }
    updated = evocycle.apply_data(
        state,
        {"shower": {"showerOn": 1, "showerMode": 0}, "clean": {"cleanOn": 0}},
        1795000000,
    )
    assert evocycle.activity(updated) == "standard"


# --- Ice Bath -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", NON_FINITE, ids=NON_FINITE_IDS)
@pytest.mark.parametrize("field", ["completionTime", "durationMin", "durationSec"])
def test_an_ice_bath_record_with_a_non_finite_number_does_not_stop_the_rest(
    field: str, value: Any
) -> None:
    good = {"routineMissed": False, "completionTime": 1793023200, "durationMin": 3}
    bad = {**good, field: value}
    payload = {"records": [{"record": bad}, {"record": good}]}
    found = ice_bath.sessions(payload)
    # A bad time or length drops that record; a bad seconds field reads as none.
    assert len(found) == (2 if field == "durationSec" else 1)
    assert all(session.duration == 180 for session in found)


@pytest.mark.parametrize("value", NON_FINITE, ids=NON_FINITE_IDS)
def test_a_routine_with_a_non_finite_count_asks_for_nothing(value: Any) -> None:
    configuration = {
        "configuration": {"ibhConfig": {"isDaysOfWeek": False, "noOfDays": value}}
    }
    assert ice_bath.routine_target(configuration) is None
