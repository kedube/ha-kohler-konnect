"""Kohler Numi 2.0 (SKU ``NUM``) and Purewash E930 (``BLD``): one protocol, two path segments.

See `docs/protocol/numi_purewash.md`; section numbers below refer to it. The two share the
request bodies, the state shape and the `NUMI_*` messages; their paths take `numi` or
`blade`, and each has washes and settings the other lacks. Nobody on this project has
either, so every request is the Konnect app's own and every read is decoded defensively.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .client import KohlerClient
from .const import (
    SKU_NUMI,
    SKU_PUREWASH,
    TOILET_COMMAND,
    TOILET_SETTING,
    TOILET_SETTING_PART,
    TOILET_STATE,
)
from .usage import TOILET_SERIES

__all__ = [
    "AUTO_COVER_RANGES",
    "HEAT_LEVELS",
    "NIGHT_LIGHT_LEVELS",
    "THEMES",
    "ToiletDevice",
    "activity",
    "apply_message",
    "is_cleaning",
    "is_seated",
    "numi_configuration",
    "purewash_configuration",
]

ON = "On"
OFF = "Off"
#: Water, dryer and seat heat, coolest first (§2.3).
HEAT_LEVELS = ("HeatOff", "HeatLow", "HeatLowMid", "HeatMid", "HeatMidHigh", "HeatHigh")
#: Pressure or fan speed, and the wand's position: weakest or nearest first, the app's dials
#: 1–5 (§2.3).
FLOW_LEVELS = ("FlowLow", "FlowLowMid", "FlowMid", "FlowMidHigh", "FlowHigh")
POSITIONS = (
    "PositionNear",
    "PositionNearMid",
    "PositionMid",
    "PositionMidFar",
    "PositionFar",
)
#: The spray patterns (§2.3). Commands call the field `mode`; the state and settings call
#: it `massage`.
PATTERNS = {
    "regular": "ModeNormal",
    "oscillate": "ModeOscillate",
    "pulsate": "ModePulsate",
    "wave": "ModeWave",
}
#: Each wash's command, live and default alike (C2, C3), and its settings part (S1).
WASH_PATHS = {
    "frontWash": ("front-wash", "frontwash"),
    "rearWash": ("rear-wash", "rearwash"),
    "boostWash": ("defecation-control", "boostwash"),
    "kidWash": ("kids-control", "kidwash"),
}
#: The Numi's auto-cover sensor distance; ``None`` is off (§3.10).
AUTO_COVER_RANGES = ("RangeNear", "RangeMid", "RangeFar")
#: The Purewash night light (§3.10).
NIGHT_LIGHT_LEVELS = ("Low", "High")
#: The Numi's ambient-light themes and colors: a name of ours against the code `lighting`
#: takes (§3.11).
THEMES = {
    "sunrise": "Sunrise",
    "forest_breeze": "ForestBreeze",
    "deep_ocean": "DeepOcean",
    "bokeh": "Bokeh",
    "miami": "MiamiClub",
    "coral": "Color1",
    "red": "Color2",
    "magenta": "Color3",
    "blue": "Color4",
    "gold": "Color5",
    "yellow": "Color6",
    "green": "Color7",
}
#: Each spray or the dryer, by its key in the state and the `spray-dryer` start (§3.2).
STARTS = {
    "frontWash": "FrontSprayStart",
    "rearWash": "RearSprayStart",
    "boostWash": "DefecationSprayStart",
    "kidWash": "KidsSprayStart",
    "dryer": "DryerStart",
}
STOP = "SprayAndDryerStop"
#: The washes each model has (§1.3).
WASHES = {
    SKU_NUMI: ("frontWash", "rearWash"),
    SKU_PUREWASH: ("frontWash", "rearWash", "boostWash", "kidWash"),
}
_SEGMENTS = {SKU_NUMI: "numi", SKU_PUREWASH: "blade"}
# `NUMI_SPRAY_STS` status → the wash it starts (§4.2); `SprayingOff` stops them all.
_SPRAYING = {
    "SprayingFront": "frontWash",
    "SprayingRear": "rearWash",
    "SprayingDefecation": "boostWash",
    "SprayingKids": "kidWash",
}
# The other statuses the app acts on; one it doesn't know means a read instead.
_SPRAYING_OFF = "SprayingOff"
_DRYING = ("DryingOn", "DryingOff")
_SANITIZING = ("SanitizeOn", "SanitizeOff", "WandCleanOn")
_PARAMETERS = {
    "NUMI_SET_FRONT_PARAMETERS_STS": "frontWash",
    "NUMI_SET_REAR_PARAMETERS_STS": "rearWash",
    "NUMI_SET_DEFECATION_PARAMETERS_STS": "boostWash",
    "NUMI_SET_KIDS_PARAMETERS_STS": "kidWash",
    "NUMI_SET_DRYER_PARAMETERS_STS": "dryer",
}
# The errors that mean none (§4.3 item 4).
_NO_ERROR = frozenset({"noerror", "devicenoerror"})


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _block(state: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = state.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _flag(value: Any, on: str = ON) -> bool:
    return isinstance(value, str) and value.strip().casefold() == on.casefold()


def is_seated(state: Mapping[str, Any]) -> bool:
    """Whether someone is seated: what every spray and the dryer need (§7.2)."""
    return _block(state, "seat").get("userStatus") == "UserSeated"


def is_cleaning(state: Mapping[str, Any]) -> bool:
    """Whether the UV clean runs, when the app locks its dial, Start and tabs (§7.2)."""
    return _block(state, "cleanStatus").get("uvClean") == "SanitizeOn"


def activity(
    state: Mapping[str, Any], parts: tuple[str, ...] = tuple(STARTS)
) -> str | None:
    """Which of ``parts``, the sprays and the dryer by their state keys, runs now."""
    for key in parts:
        if _flag(_block(state, key).get("state")):
            return key
    return None


def apply_message(
    state: Mapping[str, Any], code: str, attributes: Mapping[str, Any]
) -> dict[str, Any] | None:
    """``state`` as one `NUMI_*` message leaves it (§4.2), or None to read it instead.

    Applied as the app applies it, field by field. `NUMI_CONFIG_STS`, which the app's own
    model can't parse (§4.3), is read here directly, its distance from `microwaverange`.
    """
    updated = {
        key: dict(value) if isinstance(value, Mapping) else value
        for key, value in state.items()
    }

    def nested(key: str) -> dict[str, Any]:
        block = updated.get(key)
        if not isinstance(block, dict):
            block = updated[key] = {}
        return block

    status = _text(attributes.get("status"))
    if code == "NUMI_SPRAY_STS" and status is not None:
        running = _SPRAYING.get(status)
        if running is None and status != _SPRAYING_OFF:
            return None
        for key in STARTS:
            nested(key)["state"] = ON if key == running else OFF
        return updated
    if code == "NUMI_DRYER_STS" and status is not None:
        if status not in _DRYING:
            return None
        for key in STARTS:
            nested(key)["state"] = (
                ON if key == "dryer" and status == "DryingOn" else OFF
            )
        return updated
    if code in _PARAMETERS:
        block = nested(_PARAMETERS[code])
        for field, into in (
            ("flow", "flow"),
            ("temperature", "temperature"),
            ("position", "position"),
            ("mode", "massage"),
        ):
            if (value := _text(attributes.get(field))) is not None:
                block[into] = value
        return updated
    if code == "NUMI_SET_RING_HEATER_PARAMETERS_STS":
        if (value := _text(attributes.get("temperature"))) is not None:
            nested("seat")["temperature"] = value
        return updated
    if code == "NUMI_USER_STS" and status is not None:
        nested("seat")["userStatus"] = status
        return updated
    if code == "NUMI_SANITIZE_STS" and status is not None:
        if status not in _SANITIZING:
            return None
        clean = nested("cleanStatus")
        if status == "WandCleanOn":
            clean["manualClean"], clean["uvClean"] = ON, "SanitizeOff"
        else:
            clean["uvClean"], clean["manualClean"] = status, OFF
        return updated
    if code in ("NUMI_AUTO_POWER_SAVE_ENABLE_STS", "NUMI_DEVICE_STS"):
        if (value := _text(attributes.get("autopowersave"))) is not None:
            nested("seat")["powerSave"] = ON if value == "1" else OFF
        if (device_state := _text(attributes.get("devicestate"))) is not None:
            updated["deviceState"] = device_state
        return updated
    if code == "NUMI_CONFIG_STS":
        _apply_configuration(updated, nested, attributes)
        return updated
    if code == "NUMI_ERROR_STS":
        error = (
            status
            or _text(attributes.get("errorcode"))
            or _text(attributes.get("code"))
        )
        updated["error"] = (
            None if error is None or error.casefold() in _NO_ERROR else error
        )
        return updated
    if code == "NUMI_MULTIMEDIA_STS":
        asleep = attributes.get("multimediastatus") == "MultimediaIsSleeping"
        ambient = nested("light").setdefault("ambientLight", {})
        ambient["state"] = OFF if asleep else ON
        return updated
    if code == "NUMI_SET_LIGHTING_PARAMETERS_STS":
        ambient = nested("light").setdefault("ambientLight", {})
        if (color := _text(attributes.get("color"))) is not None:
            ambient["colorTheme"] = color
        if (luminance := _text(attributes.get("luminance"))) is not None:
            ambient["brightness"] = luminance
        return updated
    if code == "NUMI_CIRCADIAN_STS":
        nested("light")["circadianLight"] = (
            ON if attributes.get("startstop") == "Start" else OFF
        )
        return updated
    return None


def _apply_configuration(
    updated: dict[str, Any], nested: Any, attributes: Mapping[str, Any]
) -> None:
    def flag(name: str) -> str | None:
        value = _text(attributes.get(name))
        return None if value is None else (ON if value.casefold() == "true" else OFF)

    if (value := flag("nightlightenable")) is not None:
        nested("light")["nightLight"] = value
    if (value := flag("autoflushenable")) is not None:
        nested("flush")["auto"] = value
    if (value := flag("autouvenable")) is not None:
        nested("clean")["sanitizer"] = "SanitizeOn" if value == ON else "SanitizeOff"
    if (value := flag("autodeodorizeenable")) is not None:
        nested("clean")["deo"] = "AirCleanOn" if value == ON else "AirCleanOff"
    if (value := flag("waterbowlpremistenable")) is not None:
        nested("clean")["preRinse"] = value
    lid = flag("autolidenable")
    distance = _text(attributes.get("microwaverange"))
    seat = nested("seat")
    if isinstance(seat.get("autoOpen"), dict):
        auto_open = dict(seat["autoOpen"])
        if lid is not None:
            auto_open["state"] = lid
        if distance is not None:
            auto_open["sensorDistance"] = distance
        seat["autoOpen"] = auto_open
    elif lid is not None:
        seat["autoOpen"] = lid


def _true(value: bool) -> str:
    return "true" if value else "false"


def numi_configuration(state: Mapping[str, Any]) -> dict[str, str]:
    """`write-configuration` for a Numi: every auto setting, as the state has it (§3.10).

    The command replaces them all at once, so the caller changes one and sends the rest.
    """
    seat, light, flush, clean = (
        _block(state, key) for key in ("seat", "light", "flush", "clean")
    )
    auto_open = seat.get("autoOpen")
    auto_open = auto_open if isinstance(auto_open, Mapping) else {}
    covering = _flag(auto_open.get("state"))
    distance = _text(auto_open.get("sensorDistance"))
    return {
        "nightLightEnable": _true(_flag(light.get("nightLight"))),
        "autoFlushEnable": _true(_flag(flush.get("auto"))),
        "autoLidEnable": _true(covering),
        # Off sends the middle distance, as the app does.
        "microWaveRange": distance
        if covering and distance in AUTO_COVER_RANGES
        else "RangeMid",
        "autoUVEnable": _true(clean.get("sanitizer") == "SanitizeOn"),
        "autoDeodorizeEnable": _true(clean.get("deo") == "AirCleanOn"),
        "waterBowlPremistEnable": _true(_flag(clean.get("preRinse"))),
    }


def purewash_configuration(state: Mapping[str, Any]) -> dict[str, str]:
    """`write-configuration` for a Purewash, every auto setting as the state has it (§3.10)."""
    seat, light, flush, clean = (
        _block(state, key) for key in ("seat", "light", "flush", "clean")
    )
    night_light = _flag(light.get("nightLight"))
    # With none reported, the app's own default.
    brightness = _text(light.get("nightLightBrightness")) or "Low"
    return {
        "autoLidEnable": _true(_flag(seat.get("autoOpen"))),
        "autoOpen": _true(_flag(seat.get("autoOpen"))),
        "autoDeodorizeEnable": _true(clean.get("deo") == "AirCleanOn"),
        "waterBowlPremistEnable": _true(_flag(clean.get("preRinse"))),
        "longTimeSeatReminderEnable": _true(_flag(seat.get("longTimeSeatReminder"))),
        "autoWaterTempEnable": _true(_flag(seat.get("autoWaterTemp"))),
        "autoSeatTemp": _true(_flag(seat.get("autoSeatTemp"))),
        "autoUVEnable": _true(clean.get("sanitizer") == "SanitizeOn"),
        "nightLightEnable": _true(night_light),
        # Lower-case, as the app's resources spell them; the state reports `High`/`Low`.
        "nightLightBrightness": brightness.casefold() if night_light else "high",
        "autoFlushEnable": _true(_flag(flush.get("auto"))),
    }


#: The counts a daily usage bucket carries (§6.2), by what they count, then by part. Each
#: comes as a Double.
COUNTS: dict[str, dict[str, str]] = {
    "flushes": {"full": "fullFlushCount", "eco": "ecoFlushCount"},
    "washes": {"front": "sprayFrontCount", "rear": "sprayRearCount"},
    "dryer_runs": {"dryer": "dryerCount"},
    # Not shown by the app, and in a unit nobody has confirmed.
    "power": {"power": "powerConsumption"},
}


def day_bucket(payload: Any, day: str) -> Mapping[str, Any] | None:
    """One day's bucket (`yyyy-MM-dd`) from a daily usage reply, or None if it has none.

    Matched on the key's date, as the water totals read it, whatever follows it.
    """
    buckets = payload.get(TOILET_SERIES) if isinstance(payload, Mapping) else None
    for bucket in buckets if isinstance(buckets, list) else ():
        key = bucket.get("intervalKey") if isinstance(bucket, Mapping) else None
        if isinstance(key, str) and key[:10] == day:
            return bucket
    return None


class ToiletDevice:
    """One Numi or Purewash's endpoints, on the path segment its SKU names."""

    def __init__(self, client: KohlerClient, device_id: str, sku: str) -> None:
        self._client = client
        self.device_id = device_id
        self.sku = sku
        self.segment = _SEGMENTS.get(sku, "numi")

    async def async_get_state(self) -> dict[str, Any]:
        payload = await self._client.async_request(
            "GET", TOILET_STATE.format(segment=self.segment, device_id=self.device_id)
        )
        return payload if isinstance(payload, dict) else {}

    async def async_get_settings(self) -> dict[str, Any]:
        """The stored defaults: each wash's, the dryer's and the seat's (§2.4)."""
        payload = await self._client.async_request(
            "GET", TOILET_SETTING.format(segment=self.segment, device_id=self.device_id)
        )
        return payload if isinstance(payload, dict) else {}

    async def async_store_setting(self, part: str, body: Mapping[str, str]) -> Any:
        """Store one part's defaults: a wash, `dryer` or `seat` (S1–S3)."""
        return await self._client.async_request(
            "PATCH",
            TOILET_SETTING_PART.format(
                segment=self.segment, device_id=self.device_id, part=part
            ),
            json_body=dict(body),
        )

    async def async_command(self, verb: str, keys: Mapping[str, str]) -> Any:
        """Send one command, `flush`, `spray-dryer` and so on, with ``keys`` (§3.1)."""
        body = {
            "deviceId": self.device_id,
            "tenantId": await self._client.async_tenant_id(),
            "sku": self.sku,
            **keys,
        }
        return await self._client.async_request(
            "POST",
            TOILET_COMMAND.format(segment=self.segment, command=verb),
            json_body=body,
        )

    async def async_heat_seat(self, level: str) -> Any:
        """Set the seat heat now: `heated-seat` with only a temperature (§3.4).

        The app's request starts every field empty, so the unused ones go out as `""`.
        """
        return await self.async_command(
            "heated-seat",
            {"flow": "", "temperature": level, "position": "", "mode": ""},
        )
