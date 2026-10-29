"""Kohler Smart Fan (SKU ``SFC``): reads, command bodies and `SFC_STS` messages.

See `docs/protocol/smart_fan.md`; section numbers below refer to it. Nobody on this
project has a Smart Fan, so nothing here has been seen against hardware: every request
body is the Konnect app's own, and every read is decoded defensively, so an odd or missing
field leaves one value unknown rather than the device.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import time
from typing import Any

from .client import KohlerClient
from .const import SFC_COMMAND, SFC_CONFIGURATION, SFC_EXPERIENCE, SFC_STATE

__all__ = [
    "COLORS",
    "DEFAULT_FAN",
    "DEFAULT_LIGHT",
    "REPEATS",
    "SETTINGS_FIELDS",
    "SPEEDS",
    "WEEKDAYS",
    "Favorite",
    "SmartFanDevice",
    "apply_status",
    "clock_time",
    "is_on",
    "parse_favorites",
    "schedule_changes",
    "schedule_days",
    "settings_request",
]

ON = "ON"
OFF = "OFF"
ENABLE = "ENABLE"
DISABLE = "DISABLE"
SPEEDS = ("LOW", "MEDIUM", "HIGH")
COLORS = ("WARM", "WARMWHITE", "COOL", "COOLWHITE", "DAYLIGHT")
#: A schedule's days, by their bit in its `fsDays` mask: Monday is bit 0 (§3.4).
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
#: How a schedule repeats, by the `fsSetting` it writes (§3.4).
REPEATS = {"everyday": "EVERYDAY", "once": "ONCE", "custom": "CUSTOM"}
#: The built-in favorites, which run what the stored defaults say (§3.2).
DEFAULT_FAN = "DEFAULT-FAN"
DEFAULT_LIGHT = "DEFAULT-LIGHT"
#: The built-in night light, which runs its stored brightness (§3.2).
DEFAULT_NIGHTLIGHT = "DEFAULT-NIGHTLIGHT"
DEFAULT_NIGHTLIGHT = "DEFAULT-NIGHTLIGHT"
KIND_FAN = "FAN"
KIND_LIGHT = "LIGHT"
KIND_NIGHTLIGHT = "NIGHTLIGHT"
#: The message that carries the whole live state (§4).
MSG_STATUS = "SFC_STS"

#: Every setting `updatedefaultsettings` carries, by the name it is read under (§2.3).
SETTINGS_FIELDS = (
    "fanSpeed",
    "fanTimer",
    "fanTime",
    "lightColor",
    "lightBrightnessLevel",
    "circadianMode",
    "nightLightBrightness",
    "buzzerSetting",
    "humidityThreshold",
    "humidity",
    "airQuality",
    "fsSchedule",
    "fsSetting",
    "fsDays",
    "fsStartTimeHr",
    "fsStartTimeMin",
    "fsStartTimeAmPm",
    "fsEndTimeHr",
    "fsEndTimeMin",
    "fsEndTimeAmPm",
    "fsFanSpeed",
    "nlSchedule",
    "nlsSetting",
    "nlsDays",
    "nlsStartTimeHr",
    "nlsStartTimeMin",
    "nlsStartTimeAmPm",
    "nlsEndTimeHr",
    "nlsEndTimeMin",
    "nlsEndTimeAmPm",
    "nlsLightBrightness",
)
# The four that `updatedefaultsettings` names differently from the read (§3.4).
_WRITE_NAMES = {
    "lightColor": "color",
    "nightLightBrightness": "nightLightBrightnessLevel",
    "nlsLightBrightness": "nlsBrightness",
    "fsEndTimeHr": "fsEndTimeHour",
}
# `SFC_STS` attribute → `sfc-state` field (§4).
_STATUS_FIELDS = {
    "fanstatus": "fanStatus",
    "fanspeed": "fanSpeed",
    "lightstatus": "lightStatus",
    "lightcolour": "lightColor",
    "lightbrightnesslevel": "lightBrightnessLevel",
    "nightlightstatus": "nightLightStatus",
    "nightlightbrightnesslevel": "nightLightBrightnessLevel",
    "temperature": "temperature",
    "humidity": "humidity",
    "voc": "vocvalue",
    "co2": "co2value",
}


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def is_on(value: Any) -> bool:
    """Whether a status field reads ``ON``; anything else, missing included, is off."""
    return isinstance(value, str) and value.strip().upper() == ON


@dataclass(frozen=True)
class Favorite:
    """One entry of `sfc-experience` (§2.4): a built-in or a saved favorite."""

    id: str
    title: str
    #: ``FAN``, ``LIGHT`` or ``NIGHTLIGHT``.
    kind: str
    is_default: bool
    #: A fan favorite's `{speed, timer, duration}`; a light favorite's
    #: `{brightness, circadianEnabled, color}`.
    values: Mapping[str, str]

    def command(self, status: str) -> tuple[str, dict[str, str]]:
        """The endpoint and keys that start or stop this favorite, as the app sends them.

        A built-in names only its id; a saved favorite carries its own values too, under
        the command's names (§3.2). A value the favorite lacks is left out, as the app
        leaves out a null one.
        """
        keys = {"id": self.id, "status": status}
        if self.kind == KIND_LIGHT:
            if self.is_default:
                return "lightcontrol", keys
            names = {
                "circadianMode": "circadianEnabled",
                "color": "color",
                "brightnessLevel": "brightness",
            }
            command = "lightfavoritecontrol"
        else:
            if self.is_default:
                return "fancontrol", keys
            names = {"speed": "speed", "timer": "timer", "time": "duration"}
            command = "fanfavoritecontrol"
        for key, field in names.items():
            if (value := self.values.get(field)) is not None:
                keys[key] = value
        return command, keys


def parse_favorites(payload: Any) -> list[Favorite]:
    """The fan and light favorites in an `sfc-experience` reply, in the server's order.

    Night-light favorites are left out: the app hides the night light (§1).
    """
    entries = payload.get("experiences") if isinstance(payload, dict) else None
    favorites: list[Favorite] = []
    for entry in entries if isinstance(entries, list) else ():
        if not isinstance(entry, dict) or not (favorite_id := _text(entry.get("id"))):
            continue
        kind = (_text(entry.get("favType")) or "").upper()
        if kind not in (KIND_FAN, KIND_LIGHT):
            continue
        block = entry.get("fan" if kind == KIND_FAN else "light")
        values = {
            key: text
            for key, value in (block.items() if isinstance(block, dict) else ())
            if (text := _text(value)) is not None
        }
        favorites.append(
            Favorite(
                id=favorite_id,
                title=_text(entry.get("title")) or favorite_id,
                kind=kind,
                is_default=entry.get("isDefault") is True
                or favorite_id in (DEFAULT_FAN, DEFAULT_LIGHT),
                values=values,
            )
        )
    return favorites


def apply_status(
    state: Mapping[str, Any],
    attributes: Mapping[str, Any],
    favorites: list[Favorite],
) -> dict[str, Any]:
    """``state`` brought up to date by one `SFC_STS` message's attributes (§4).

    Only the fields the message carries change: one missing from a message is more likely
    left out than cleared. Its `favoriteid` names what is running. ``"0"`` names nothing
    in particular: as the app does, each part that is on keeps the favorite running it if
    the message still shows that favorite's values, and otherwise runs its built-in.
    """
    updated = dict(state)
    for attribute, field in _STATUS_FIELDS.items():
        if attribute in attributes:
            updated[field] = attributes[attribute]
    favorite_id = _text(attributes.get("favoriteid"))
    if favorite_id is None:
        return updated
    if favorite_id == "0":
        _name_running(updated, favorites)
        return updated
    if favorite_id == DEFAULT_FAN:
        updated["runningFanFavorite"] = DEFAULT_FAN
        return updated
    if favorite_id in (DEFAULT_LIGHT, DEFAULT_NIGHTLIGHT):
        updated["runningLightFavorite"] = favorite_id
        return updated
    favorite = next((f for f in favorites if f.id == favorite_id), None)
    if favorite is not None:
        slot = (
            "runningFanFavorite"
            if favorite.kind == KIND_FAN
            else "runningLightFavorite"
        )
        updated[slot] = favorite_id
    return updated


def _name_running(state: dict[str, Any], favorites: list[Favorite]) -> None:
    """Name what runs when a message names nothing (`favoriteid` ``"0"``), in place."""

    def still(slot: str, kind: str, shown: Mapping[str, Any]) -> str | None:
        running = next(
            (f for f in favorites if f.id == state.get(slot) and f.kind == kind), None
        )
        if running is None or running.is_default:
            return None
        values = running.values
        return (
            running.id
            if all(
                _text(values.get(key)) == _text(value) for key, value in shown.items()
            )
            else None
        )

    if is_on(state.get("fanStatus")):
        state["runningFanFavorite"] = (
            still("runningFanFavorite", KIND_FAN, {"speed": state.get("fanSpeed")})
            or DEFAULT_FAN
        )
    if is_on(state.get("lightStatus")):
        shown = {
            "color": state.get("lightColor"),
            "brightness": state.get("lightBrightnessLevel"),
        }
        state["runningLightFavorite"] = (
            still("runningLightFavorite", KIND_LIGHT, shown) or DEFAULT_LIGHT
        )
    if is_on(state.get("nightLightStatus")):
        # Night-light favorites aren't read (`parse_favorites`), so it is the built-in.
        state["runningLightFavorite"] = DEFAULT_NIGHTLIGHT


def schedule_days(mask: Any) -> list[str]:
    """The weekdays a schedule's `fsDays` mask names (§3.4); none for one unreadable."""
    try:
        value = int(str(mask).strip())
    except ValueError:
        return []
    return [day for bit, day in enumerate(WEEKDAYS) if value >> bit & 1]


def clock_time(hour: Any, minute: Any, meridiem: Any) -> time | None:
    """A schedule time from its 12-hour parts (§3.4); None when they make none."""
    try:
        hours, minutes = int(str(hour).strip()), int(str(minute).strip())
    except ValueError:
        return None
    half = str(meridiem).strip().upper()
    if not (1 <= hours <= 12 and 0 <= minutes <= 59) or half not in ("AM", "PM"):
        return None
    return time(hours % 12 + (12 if half == "PM" else 0), minutes)


def _clock_parts(moment: time) -> tuple[str, str, str]:
    """A time as the app's schedule writes it: the hour unpadded, the minute padded."""
    return (
        str(moment.hour % 12 or 12),
        f"{moment.minute:02d}",
        "AM" if moment.hour < 12 else "PM",
    )


def schedule_changes(
    start: time, end: time, speed: str, repeat: str, days: Iterable[str]
) -> dict[str, str]:
    """What the app's Fan Schedule editor saves, by the read's names (§3.4).

    Saving always switches the schedule on, as the app's does. A custom schedule's days
    go out as the app writes them, a mask padded with a space to two characters; the
    others send ``"0"``.
    """
    mask = sum(1 << WEEKDAYS.index(day) for day in set(days))
    begins, ends = _clock_parts(start), _clock_parts(end)
    return {
        "fsSchedule": ENABLE,
        "fsSetting": REPEATS[repeat],
        "fsDays": f"{mask:>2}" if repeat == "custom" else "0",
        "fsStartTimeHr": begins[0],
        "fsStartTimeMin": begins[1],
        "fsStartTimeAmPm": begins[2],
        "fsEndTimeHr": ends[0],
        "fsEndTimeMin": ends[1],
        "fsEndTimeAmPm": ends[2],
        "fsFanSpeed": speed.upper(),
    }


def settings_request(
    identity: Mapping[str, str],
    settings: Mapping[str, Any],
    changes: Mapping[str, str],
) -> dict[str, str]:
    """`updatedefaultsettings`'s body: every stored setting, with ``changes`` applied.

    The command replaces the whole document, so every field goes out each time, under the
    name the command takes, which for four of them differs from the read's (§3.4).
    ``changes`` are keyed by the read's names. A field never read is left out, as the app
    leaves out a null one.
    """
    body = dict(identity)
    for field in SETTINGS_FIELDS:
        value = changes[field] if field in changes else settings.get(field)
        if (text := _text(value)) is not None:
            body[_WRITE_NAMES.get(field, field)] = text
    return body


class SmartFanDevice:
    """One Smart Fan's endpoints, by device id."""

    def __init__(self, client: KohlerClient, device_id: str, sku: str) -> None:
        self._client = client
        self.device_id = device_id
        self.sku = sku

    async def async_get_state(self) -> dict[str, Any]:
        payload = await self._client.async_request(
            "GET", SFC_STATE.format(device_id=self.device_id)
        )
        return payload if isinstance(payload, dict) else {}

    async def async_get_configuration(self) -> dict[str, Any]:
        payload = await self._client.async_request(
            "GET", SFC_CONFIGURATION.format(device_id=self.device_id)
        )
        return payload if isinstance(payload, dict) else {}

    async def async_get_favorites(self) -> dict[str, Any]:
        payload = await self._client.async_request(
            "GET", SFC_EXPERIENCE.format(device_id=self.device_id)
        )
        return payload if isinstance(payload, dict) else {}

    async def async_identity(self) -> dict[str, str]:
        """What every command body starts with (§3.1)."""
        return {
            "deviceId": self.device_id,
            "tenantId": await self._client.async_tenant_id(),
            "sku": self.sku,
        }

    async def async_command(self, command: str, keys: Mapping[str, str]) -> Any:
        """Send one command: `fancontrol`, `stopallcontrol` and so on (§3.2)."""
        body = {**await self.async_identity(), **keys}
        return await self._client.async_request(
            "POST", SFC_COMMAND.format(command=command), json_body=body
        )

    async def async_write_settings(
        self, settings: Mapping[str, Any], changes: Mapping[str, str]
    ) -> Any:
        """Write the stored defaults, every one of them, with ``changes`` applied."""
        body = settings_request(await self.async_identity(), settings, changes)
        return await self._client.async_request(
            "POST",
            SFC_COMMAND.format(command="updatedefaultsettings"),
            json_body=body,
        )
