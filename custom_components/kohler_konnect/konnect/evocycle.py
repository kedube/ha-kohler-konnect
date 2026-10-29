"""Kohler EvoCycle (SKU ``EVO``): a recirculating shower with ozone clean cycles.

See `docs/protocol/evocycle.md`; section numbers below refer to it. Nobody on this project
has one: every request is the Konnect app's own, and every read is decoded defensively.
Temperatures are tenths of °C and flow a percent throughout.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from .client import KohlerClient
from .const import (
    EVO_COMMAND,
    EVO_CONFIGURATION,
    EVO_DASHBOARD,
    EVO_DASHBOARD_USAGE,
    EVO_STATE,
)

__all__ = [
    "CLEAN_FIELDS",
    "DEFAULT_FIELDS",
    "MODES",
    "EvoCycleDevice",
    "activity",
    "apply_data",
    "default_settings",
    "fault",
    "fault_stops",
    "savings_months",
    "week_split",
]

#: The shower's two modes, by the number the commands take (§3.2).
MODES = {"standard": 0, "cycle": 1}
#: The two ozone cleans, by the number `clean` takes (§3.4).
CLEANS = {"rapid_clean": 0, "deep_clean": 1}
_SHOWERS = {"standard": "standardModeShower", "cycle": "reCycleModeShower"}
_CLEAN_BLOCKS = {"rapid_clean": "rapidClean", "deep_clean": "deepClean"}

# The app's own fault table (§5.3), by what each fault stops; a code not in it is no fault.
_STOPS_ALL = frozenset(
    {1616, 1617, 1618, 1619, 1640, 1643, 1644, 1645, 1646, 1647, 1648}
    | {16411, 16412, 16416, 16418, 16419, 16432, 16435, 16436, 16437, 16440}
    | {16445, 16446, 16447, 16450, 16453, 16454, 16480, 16490}
    | {164110, 164111, 164112, 164115, 164116, 164117}
)
# Everything but the Standard shower.
_STOPS_OZONE_AND_CYCLE = frozenset(
    {1651, 1653, 1655, 1656, 1657, 1658, 1659, 1663, 1664, 1665, 1666, 1671, 1679}
    | {16141, 16142, 16510, 16511, 16512}
)
_STOPS_OZONE = frozenset({16143})
# A banner, and nothing stopped.
_STOPS_NOTHING = frozenset({1615})
_FAULTS = _STOPS_ALL | _STOPS_OZONE_AND_CYCLE | _STOPS_OZONE | _STOPS_NOTHING


def _block(mapping: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = mapping.get(key) if isinstance(mapping, Mapping) else None
        mapping = value if isinstance(value, Mapping) else {}
    return dict(mapping)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def shower(state: Mapping[str, Any], mode: str) -> dict[str, Any]:
    """One mode's block of the state: `showerOn`, `temperature`, `flow` …"""
    return _block(state, "shower", _SHOWERS[mode])


def clean(state: Mapping[str, Any], kind: str) -> dict[str, Any]:
    """One clean's block of the state: `cleanOn`, `lastCleanDate` …"""
    return _block(state, "clean", _CLEAN_BLOCKS[kind])


def activity(state: Mapping[str, Any]) -> str | None:
    """What runs: `standard`, `cycle`, `rapid_clean`, `deep_clean`, `cancelling`, or None.

    A clean being cancelled reads `cleanOn` 2, over MQTT only (§4.2).
    """
    for kind in CLEANS:
        on = _int(clean(state, kind).get("cleanOn"))
        if on == 1:
            return kind
        if on == 2:
            return "cancelling"
    for mode in MODES:
        if _int(shower(state, mode).get("showerOn")) == 1:
            return mode
    return None


def fault(state: Mapping[str, Any]) -> int | None:
    """The fault code the shower reports, or None (§5.3).

    From a message's `product`/`valve` block when one came, else the state's
    `deviceErrorState`, whose code counts only while its state says `error`. Either
    way, only a code in the app's table is a fault, as the app reads them.
    """
    if "fault" in state:
        code = state["fault"]
        return code if isinstance(code, int) and code in _FAULTS else None
    error = _block(state, "deviceErrorState")
    if str(error.get("currentDeviceState") or "").casefold() != "error":
        return None
    code = _int(error.get("currentErrorCode"))
    return code if code in _FAULTS else None


def fault_stops(code: int | None, what: str) -> bool:
    """Whether a fault stops ``what`` (a `MODES` or `CLEANS` key) from starting, as the
    app's impact classes stop its buttons (§5.3)."""
    if code is None or code in _STOPS_NOTHING:
        return False
    if code in _STOPS_ALL:
        return True
    if code in _STOPS_OZONE_AND_CYCLE:
        return what != "standard"
    return code in _STOPS_OZONE and what in CLEANS


def apply_data(
    state: Mapping[str, Any], data: Mapping[str, Any], now: int
) -> dict[str, Any]:
    """``state`` as one message's `data` blocks leave it, as the app merges them (§4.2).

    ``now`` (epoch seconds) dates a clean that ends without a date of its own.
    """
    updated: dict[str, Any] = {
        key: dict(value) if isinstance(value, Mapping) else value
        for key, value in state.items()
    }
    # A block the state read gave as null, or as anything but a block, is an empty one.
    for key in ("shower", "clean"):
        if not isinstance(updated.get(key), dict):
            updated[key] = {}
    showers, cleans = updated["shower"], updated["clean"]
    if isinstance(news := data.get("shower"), Mapping):
        on, mode = _int(news.get("showerOn")), _int(news.get("showerMode"))
        for name, key in _SHOWERS.items():
            block = dict(showers.get(key) or {})
            block["showerOn"] = 1 if on == 1 and mode == MODES[name] else 0
            showers[key] = block
    if isinstance(news := data.get("clean"), Mapping):
        on, mode = _int(news.get("cleanOn")), _int(news.get("cleanMode"))
        for name, key in _CLEAN_BLOCKS.items():
            block = dict(cleans.get(key) or {})
            was_on = _int(block.get("cleanOn")) in (1, 2)
            if on in (1, 2) and mode == CLEANS[name]:
                block["cleanOn"] = on
            else:
                if was_on:
                    block["lastCleanDate"] = _int(news.get("lastCleanDate")) or now
                block["cleanOn"] = 0
            cleans[key] = block
    if isinstance(product := data.get("product"), Mapping):
        code = _int(product.get("errorCode"))
        if code == 0:
            updated["fault"] = None
        else:
            # The valve's code, when it gives one, speaks for the product's.
            valve = data.get("valve")
            valve_code = (
                _int(valve.get("errorCode")) if isinstance(valve, Mapping) else None
            )
            code = valve_code if valve_code is not None else code or 0
            # A code the app doesn't know leaves the fault as it was, as in the app.
            if code in _FAULTS:
                updated["fault"] = code
    return updated


#: `showersetting`'s six settings, each with the name the stored defaults read it by.
DEFAULT_FIELDS = {
    "showerMode": "defaultShowerMode",
    "temperature": "defaultTemperature",
    "flow": "defaultFlow",
    "warmupMode": "defaultWarmupMode",
    "standardModeMaxTemperature": "defaultStandardMaxTemperature",
    "cycleModeMaxTemperature": "defaultCycleMaxTemperature",
}
#: `cleansetting`'s three reminders, likewise.
CLEAN_FIELDS = {
    "rapidCleanEnable": "enableRapidClean",
    "deepCleanEnable": "enableDeepClean",
    "showerFilterCleanEnable": "showerFilterCleanEnable",
}


def shower_defaults(configuration: Mapping[str, Any]) -> dict[str, Any]:
    """The stored defaults (`showerConfiguration`) in a configuration read."""
    return _block(configuration, "evoAboutModel", "showerConfiguration")


def clean_reminders(configuration: Mapping[str, Any]) -> dict[str, Any]:
    """The clean reminders (`cleanConfiguration`) in a configuration read."""
    return _block(configuration, "evoAboutModel", "cleanConfiguration")


def _litres(value: Any) -> float | None:
    """A volume the dashboard reads give as a number or a numeric string."""
    if isinstance(value, bool):
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def week_split(payload: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """This week's cycled and fresh water, in litres, from R3 (§2.4, §6.3)."""
    week = _block(payload, "currentWeekUsageData")
    return _litres(week.get("waterSaved")), _litres(week.get("waterUsage"))


def savings_months(payload: Mapping[str, Any]) -> dict[str, dict[str, float | None]]:
    """R4's monthly buckets (§2.5): each month's saved litres and cycle-mode share, by
    its `yyyy-MM` key."""
    entries = payload.get("waterUsageData") if isinstance(payload, Mapping) else None
    months: dict[str, dict[str, float | None]] = {}
    for entry in entries if isinstance(entries, list) else ():
        key = entry.get("intervalKey") if isinstance(entry, Mapping) else None
        if isinstance(key, str) and len(key) >= 7:
            months[key[:7]] = {
                "saved": _litres(entry.get("waterSaved")),
                "cycle_share": _litres(entry.get("reCycleWaterPercentage")),
            }
    return months


def default_settings(
    stored: Mapping[str, Any], changes: Mapping[str, int]
) -> dict[str, int] | None:
    """`showersetting`'s six settings, from the stored defaults with ``changes`` applied.

    Every write carries them all (§3.5); the command adds the identity. None when the
    defaults can't be read whole: written from a gap, a field would go out as nothing.
    """
    body: dict[str, int] = {}
    for field, source in DEFAULT_FIELDS.items():
        value = changes[field] if field in changes else _int(stored.get(source))
        if value is None:
            return None
        body[field] = value
    return body


#: The interface's languages, each with the region the app pairs it with (§3.7).
PANEL_LANGUAGES = {"en": "US", "es": "MX", "fr": "CA"}
#: The interface's units: `0` US gallons and °F, `1` litres and °C (§3.7).
PANEL_UNITS = {"us": 0, "metric": 1}


def interface(configuration: Mapping[str, Any]) -> Mapping[str, Any]:
    """The first digital interface's settings, the one the app sets (§2.3)."""
    about = _block(configuration, "evoAboutModel")
    panels = about.get("uiConfigurations")
    first = panels[0] if isinstance(panels, list) and panels else None
    return first if isinstance(first, Mapping) else {}


def interface_settings(
    panel: Mapping[str, Any], changes: Mapping[str, Any]
) -> dict[str, Any] | None:
    """`uisetting`'s body from the interface's settings (`interface`) with ``changes``
    applied (§3.7).

    ``changes`` sets `language` (a `PANEL_LANGUAGES` key), `units` or `hapticFeedback`
    (each 0 or 1). None when one of them can't be read: written from a gap, a setting would
    go out as nothing. The command adds the identity.
    """
    language = changes.get("language", panel.get("language"))
    units = changes.get("units", _int(panel.get("temperatureUnits")))
    haptics = changes.get("hapticFeedback", _int(panel.get("hapticFeedback")))
    if language not in PANEL_LANGUAGES or units not in (0, 1) or haptics not in (0, 1):
        return None
    return {
        "language": language,
        "languageRegion": PANEL_LANGUAGES[language],
        "temperatureUnits": units,
        # The app offers one "Panel Units" choice and sends it as both.
        "flowUnits": units,
        "hapticFeedback": haptics,
        "backlight": "0",
        "isUpdateUI0Setting": True,
    }


def goal_request(
    record: Mapping[str, Any], *, percent: int, enabled: bool
) -> dict[str, Any]:
    """The account record update that sets the water-savings goal (§3.8).

    The record also names the shower (`logicalname`, lowercase), so ``record`` must be
    fresh: a stale one would undo a rename made since.
    """
    body = {
        "deviceId": record.get("deviceId"),
        "logicalname": record.get("logicalName"),
        "sku": "EVO",
        "operation": "Update",
        "showOnBoardingScreen": record.get("showOnBoardingScreen"),
        "showWaterSavingGoalScreen": enabled,
        "waterSavingGoal": {"isEnableWaterSave": enabled, "goalPercentage": percent},
    }
    # Left out when unknown, as the app's converter leaves out a null.
    return {key: value for key, value in body.items() if value is not None}


class EvoCycleDevice:
    """One EvoCycle's endpoints, by device id."""

    def __init__(self, client: KohlerClient, device_id: str, sku: str) -> None:
        self._client = client
        self.device_id = device_id
        self.sku = sku

    async def async_get_state(self) -> dict[str, Any]:
        payload = await self._client.async_request(
            "GET", EVO_STATE.format(device_id=self.device_id)
        )
        return payload if isinstance(payload, dict) else {}

    async def async_get_configuration(self) -> dict[str, Any]:
        payload = await self._client.async_request(
            "GET", EVO_CONFIGURATION.format(device_id=self.device_id)
        )
        return payload if isinstance(payload, dict) else {}

    async def async_get_week(self, today: date) -> dict[str, Any]:
        """R3: this week's cycled and fresh water, as the Water Savings screen reads it."""
        payload = await self._client.async_request(
            "GET",
            f"{EVO_DASHBOARD.format(device_id=self.device_id)}"
            f"?CurrentDate={today:%m-%d-%Y}&TimeZone=UTC",
        )
        return payload if isinstance(payload, dict) else {}

    async def async_get_savings(self, year: int) -> dict[str, Any]:
        """R4: a year's saved water by month, as the screen's Year tab reads it: January
        to December, by `Month`, the end not cut to today."""
        payload = await self._client.async_request(
            "GET",
            f"{EVO_DASHBOARD_USAGE.format(device_id=self.device_id)}"
            f"?FromDate=01-01-{year}&ToDate=12-31-{year}&TimeZone=UTC&Interval=Month",
        )
        return payload if isinstance(payload, dict) else {}

    async def async_identity(self) -> dict[str, str]:
        return {
            "deviceId": self.device_id,
            "tenantId": await self._client.async_tenant_id(),
            "sku": self.sku,
        }

    async def async_command(self, command: str, body: Mapping[str, Any]) -> Any:
        """Send `shower`, `clean`, `showersetting` or `cleansetting` (§3.1)."""
        return await self._client.async_request(
            "POST",
            EVO_COMMAND.format(command=command),
            json_body={**await self.async_identity(), **body},
        )
