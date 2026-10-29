"""The Numi and Purewash protocol, offline: what each message changes, what goes out.

Checked against the Konnect app's own handling (`docs/protocol/numi_purewash.md`), since
no seat has been seen to answer.
"""

from __future__ import annotations

from custom_components.kohler_konnect.konnect.client import KohlerClient
from custom_components.kohler_konnect.konnect.toilet import (
    activity,
    apply_message,
    is_seated,
    numi_configuration,
    purewash_configuration,
)

STATE = {
    "frontWash": {"state": "Off"},
    "rearWash": {"state": "Off"},
    "dryer": {"state": "Off"},
    "seat": {
        "userStatus": "UserLeave",
        "temperature": "HeatLow",
        "powerSave": "Off",
        "autoOpen": {"state": "On", "sensorDistance": "RangeFar"},
    },
    "light": {"nightLight": "On", "ambientLight": {"state": "Off"}},
    "flush": {"auto": "Off"},
    "clean": {"sanitizer": "SanitizeOn", "deo": "AirCleanOff", "preRinse": "On"},
    "cleanStatus": {"uvClean": "SanitizeOff", "manualClean": "Off"},
}


def test_the_spray_and_dryer_messages_say_what_runs() -> None:
    rear = apply_message(STATE, "NUMI_SPRAY_STS", {"status": "SprayingRear"})
    assert activity(rear) == "rearWash"
    assert rear["frontWash"]["state"] == "Off"
    drying = apply_message(rear, "NUMI_DRYER_STS", {"status": "DryingOn"})
    assert activity(drying) == "dryer"
    assert (
        activity(apply_message(drying, "NUMI_SPRAY_STS", {"status": "SprayingOff"}))
        is None
    )
    # The original is left alone.
    assert STATE["rearWash"]["state"] == "Off"


def test_the_seat_messages() -> None:
    seated = apply_message(STATE, "NUMI_USER_STS", {"status": "UserSeated"})
    assert is_seated(seated) and not is_seated(STATE)
    warm = apply_message(
        STATE, "NUMI_SET_RING_HEATER_PARAMETERS_STS", {"temperature": "HeatHigh"}
    )
    assert warm["seat"]["temperature"] == "HeatHigh"
    saving = apply_message(
        STATE, "NUMI_DEVICE_STS", {"autopowersave": "1", "devicestate": "DeviceSleep"}
    )
    assert (saving["seat"]["powerSave"], saving["deviceState"]) == ("On", "DeviceSleep")


def test_the_wash_parameters_message_names_the_pattern_massage() -> None:
    updated = apply_message(
        STATE,
        "NUMI_SET_REAR_PARAMETERS_STS",
        {
            "flow": "FlowHigh",
            "temperature": "HeatMid",
            "position": "PositionFar",
            "mode": "ModeWave",
        },
    )
    assert updated["rearWash"] == {
        "state": "Off",
        "flow": "FlowHigh",
        "temperature": "HeatMid",
        "position": "PositionFar",
        "massage": "ModeWave",
    }


def test_cleaning_and_the_wand() -> None:
    wand = apply_message(STATE, "NUMI_SANITIZE_STS", {"status": "WandCleanOn"})
    assert wand["cleanStatus"] == {"uvClean": "SanitizeOff", "manualClean": "On"}
    uv = apply_message(wand, "NUMI_SANITIZE_STS", {"status": "SanitizeOn"})
    assert uv["cleanStatus"] == {"uvClean": "SanitizeOn", "manualClean": "Off"}


def test_errors_and_no_error() -> None:
    failed = apply_message(STATE, "NUMI_ERROR_STS", {"status": "Water supply error"})
    assert failed["error"] == "Water supply error"
    assert (
        apply_message(failed, "NUMI_ERROR_STS", {"status": "NoError"})["error"] is None
    )


def test_the_configuration_message_the_app_cannot_parse_is_read() -> None:
    updated = apply_message(
        STATE,
        "NUMI_CONFIG_STS",
        {
            "autolidenable": "false",
            "microwaverange": "RangeNear",
            "nightlightenable": "false",
            "autoflushenable": "true",
            "autouvenable": "false",
        },
    )
    assert updated["seat"]["autoOpen"] == {
        "state": "Off",
        "sensorDistance": "RangeNear",
    }
    assert (updated["light"]["nightLight"], updated["flush"]["auto"]) == ("Off", "On")
    assert updated["clean"]["sanitizer"] == "SanitizeOff"


def test_the_ambient_light_messages() -> None:
    awake = apply_message(
        STATE, "NUMI_MULTIMEDIA_STS", {"multimediastatus": "MultimediaIsAwake"}
    )
    assert awake["light"]["ambientLight"]["state"] == "On"
    lit = apply_message(
        awake, "NUMI_SET_LIGHTING_PARAMETERS_STS", {"color": "Color4", "luminance": "7"}
    )
    assert lit["light"]["ambientLight"] == {
        "state": "On",
        "colorTheme": "Color4",
        "brightness": "7",
    }
    asleep = apply_message(
        lit, "NUMI_MULTIMEDIA_STS", {"multimediastatus": "MultimediaIsSleeping"}
    )
    assert asleep["light"]["ambientLight"]["state"] == "Off"
    rhythm = apply_message(STATE, "NUMI_CIRCADIAN_STS", {"startstop": "Start"})
    assert rhythm["light"]["circadianLight"] == "On"


def test_an_unknown_message_asks_for_a_read() -> None:
    assert apply_message(STATE, "NUMI_REBOOT_STS", {}) is None


def test_each_models_auto_settings_go_out_whole() -> None:
    assert numi_configuration(STATE) == {
        "nightLightEnable": "true",
        "autoFlushEnable": "false",
        "autoLidEnable": "true",
        "microWaveRange": "RangeFar",
        "autoUVEnable": "true",
        "autoDeodorizeEnable": "false",
        "waterBowlPremistEnable": "true",
    }
    purewash = {
        **STATE,
        "seat": {
            "autoOpen": "On",
            "autoWaterTemp": "On",
            "longTimeSeatReminder": "Off",
        },
        "light": {"nightLight": "On", "nightLightBrightness": "Low"},
    }
    assert purewash_configuration(purewash) == {
        "autoLidEnable": "true",
        "autoOpen": "true",
        "autoDeodorizeEnable": "false",
        "waterBowlPremistEnable": "true",
        "longTimeSeatReminderEnable": "false",
        "autoWaterTempEnable": "true",
        "autoSeatTemp": "false",
        "autoUVEnable": "true",
        "nightLightEnable": "true",
        # Lower case, as the app sends it.
        "nightLightBrightness": "low",
        "autoFlushEnable": "false",
    }


def test_toilet_paths_reach_logs_without_the_device_id() -> None:
    for path in (
        "/devices/api/v1/device-management/numi-state/NUM-1234",
        "/devices/api/v1/device-management/blade-state/BLD-1234",
        "/platform/api/v1/firmware/numi/NUM-1234",
        "/platform/api/v1/firmware/blade/BLD-1234",
    ):
        safe = KohlerClient.safe_path(path)
        assert "1234" not in safe, safe
