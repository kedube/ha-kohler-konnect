"""The EvoCycle protocol, offline: what each message changes, and the defaults write.

Checked against the Konnect app's own handling (`docs/protocol/evocycle.md`), since no
EvoCycle has been seen to answer.
"""

from __future__ import annotations

from custom_components.kohler_konnect.konnect.client import KohlerClient
from custom_components.kohler_konnect.konnect.evocycle import (
    activity,
    apply_data,
    default_settings,
    fault,
    fault_stops,
    goal_request,
    shower_defaults,
)

NOW = 1796000000

STATE = {
    "shower": {
        "standardModeShower": {"showerOn": 0, "temperature": 380.0, "flow": 80},
        "reCycleModeShower": {"showerOn": 0, "temperature": 390.0, "flow": 100},
    },
    "clean": {
        "rapidClean": {"cleanOn": 0, "lastCleanDate": 1790000000},
        "deepClean": {"cleanOn": 0},
    },
    "deviceErrorState": {"currentDeviceState": "normal", "currentErrorCode": -1},
}


def test_a_shower_message_runs_one_mode_and_stops_the_other() -> None:
    cycle = apply_data(STATE, {"shower": {"showerOn": 1, "showerMode": 1}}, NOW)
    assert activity(cycle) == "cycle"
    assert (
        activity(apply_data(cycle, {"shower": {"showerOn": 0, "showerMode": 1}}, NOW))
        is None
    )
    assert activity(STATE) is None
    # A state read whose blocks came back null takes a message all the same.
    empty = {"shower": None, "clean": None}
    on = {"showerOn": 1, "showerMode": 0}
    assert activity(apply_data(empty, {"shower": on}, NOW)) == "standard"
    on = {"cleanOn": 1, "cleanMode": 0}
    assert activity(apply_data(empty, {"clean": on}, NOW)) == "rapid_clean"


def test_a_clean_message_runs_cancels_and_ends_a_clean() -> None:
    deep = apply_data(STATE, {"clean": {"cleanOn": 1, "cleanMode": 1}}, NOW)
    assert activity(deep) == "deep_clean"
    cancelling = apply_data(deep, {"clean": {"cleanOn": 2, "cleanMode": 1}}, NOW)
    assert activity(cancelling) == "cancelling"
    ended = apply_data(
        cancelling, {"clean": {"cleanOn": 0, "lastCleanDate": "1795000000"}}, NOW
    )
    assert activity(ended) is None
    # The clean that ran takes the date; the other keeps its own.
    assert ended["clean"]["deepClean"]["lastCleanDate"] == 1795000000
    assert ended["clean"]["rapidClean"]["lastCleanDate"] == 1790000000
    # One that ends with no date is dated now, as the app dates it.
    undated = apply_data(deep, {"clean": {"cleanOn": 0}}, NOW)
    assert undated["clean"]["deepClean"]["lastCleanDate"] == NOW


def test_a_fault_comes_from_a_message_else_from_the_state() -> None:
    assert fault(STATE) is None
    assert (
        fault(
            {
                "deviceErrorState": {
                    "currentDeviceState": "Error",
                    "currentErrorCode": 1679,
                }
            }
        )
        == 1679
    )
    faulted = apply_data(
        STATE, {"product": {"errorCode": 1651}, "valve": {"errorCode": 16411}}, NOW
    )
    # The valve's code wins over the product's, as in the app.
    assert fault(faulted) == 16411
    # The product's 0 clears it, whatever the valve says.
    cleared = apply_data(
        faulted, {"product": {"errorCode": 0}, "valve": {"errorCode": 1640}}, NOW
    )
    assert fault(cleared) is None
    # A code the app's table lacks, or none, leaves the fault as it was.
    for unknown in (
        {"product": {"errorCode": 4242}},
        {"product": {}},
        {"product": {"errorCode": 1651}, "valve": {"errorCode": 0}},
    ):
        assert fault(apply_data(faulted, unknown, NOW)) == 16411
    # From the state read, a code outside the table is no fault.
    for code in (0, 4242):
        error = {"currentDeviceState": "error", "currentErrorCode": code}
        assert fault({"deviceErrorState": error}) is None


def test_a_fault_stops_what_the_app_stops() -> None:
    # A water-supply fault stops everything; a pump fault all but Standard; a worn
    # ozone generator the cleans; the controller's memory nothing.
    assert all(fault_stops(16411, what) for what in ("standard", "rapid_clean"))
    assert not fault_stops(1656, "standard")
    assert fault_stops(1656, "cycle") and fault_stops(1656, "deep_clean")
    assert not fault_stops(16143, "cycle")
    assert fault_stops(16143, "rapid_clean")
    assert not fault_stops(1615, "deep_clean")
    assert not fault_stops(None, "standard")


def test_the_defaults_go_out_whole_or_not_at_all() -> None:
    configuration = {
        "evoAboutModel": {
            "showerConfiguration": {
                "defaultShowerMode": 1,
                "defaultTemperature": 380.0,
                "defaultFlow": 80.0,
                "defaultWarmupMode": 0,
                "defaultStandardMaxTemperature": 480.0,
                "defaultCycleMaxTemperature": 430.0,
            }
        }
    }
    stored = shower_defaults(configuration)
    assert default_settings(stored, {"flow": 64}) == {
        "showerMode": 1,
        "temperature": 380,
        "flow": 64,
        "warmupMode": 0,
        "standardModeMaxTemperature": 480,
        "cycleModeMaxTemperature": 430,
    }
    del stored["defaultWarmupMode"]
    assert default_settings(stored, {"flow": 64}) is None


def test_the_goal_leaves_out_what_the_record_doesn_t_say() -> None:
    record = {"deviceId": "evo-1", "logicalName": "Main Shower"}
    assert goal_request(record, percent=60, enabled=True) == {
        "deviceId": "evo-1",
        "logicalname": "Main Shower",
        "sku": "EVO",
        "operation": "Update",
        "showWaterSavingGoalScreen": True,
        "waterSavingGoal": {"isEnableWaterSave": True, "goalPercentage": 60},
    }


def test_evocycle_paths_reach_logs_without_the_device_id() -> None:
    for path in (
        "/devices/api/v1/device-management/evo-state/evo-1234",
        "/devices/api/v1/device-management/evo-configuration/evo-1234",
        "/platform/api/v1/firmware/evo/evo-1234?releasetarget=Public",
    ):
        assert "1234" not in KohlerClient.safe_path(path)


def test_the_account_record_path_reaches_logs_without_the_account_or_home() -> None:
    path = (
        "/devices/api/v1/device-management/customer-device/tenant-123/home-456/device"
    )
    safe = KohlerClient.safe_path(path)
    assert "tenant-123" not in safe
    assert "home-456" not in safe
    assert safe.endswith("/device")
