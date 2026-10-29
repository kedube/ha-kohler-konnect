"""An Anthem valve's touch panels: their settings records and the Control Panel's write.

See `docs/protocol/gcs_valve.md` §6. The valve keeps one record per touch interface, in
`gcsadvancestate` `setting.uiConfig[]`, and pushes one at a time as `READ_GCS_UI_CFG`. The
app's Settings → Control Panel is the only screen that changes them in everyday use: it
writes every record, whole, one after another, with the shared settings taken from the
first. This builds those writes the same way.

⚠️ A whole-record write has two hazards. The switches are read as `Enabled`/`Disabled` but
written as `1`/`0`, so a value read in any other form would go out as off; for
`waterSavingMode`, that turns off a low-flow mode the app itself never changes. And
`defaultMemory` is the valve's setup-complete flag: anything but `1` makes the app demand the
whole setup again. So writes are built only when every record reads as the app expects.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "BRIGHTNESS",
    "PANEL_LANGUAGES",
    "PANEL_UNITS",
    "WAKE_SCREENS",
    "UnwritableRecords",
    "brightness",
    "control_panel_writes",
    "flow_control",
    "merge_push",
    "panel_language",
    "panel_units",
    "read_back",
    "switch_on",
    "ui_records",
    "wake_screen",
]

#: The panel languages the app offers, each with the region it is written with.
PANEL_LANGUAGES: dict[str, tuple[str, str]] = {
    "en_us": ("en", "US"),
    "en_gb": ("en", "GB"),
    "es_mx": ("es", "MX"),
    "fr_ca": ("fr", "CA"),
    "zh_cn": ("zh", "CN"),
}
#: The panel's units: temperature and flow, always written as a pair.
PANEL_UNITS: dict[str, tuple[str, str]] = {
    "us": ("F", "Gallons"),
    "metric": ("C", "Liters"),
}
#: The screen a panel wakes to, by the number written for it before the second valve's
#: flag is added.
WAKE_SCREENS: dict[str, int] = {"outlets": 1, "home": 0, "favorites": 2}
#: The brightness the app's slider offers.
BRIGHTNESS = (1, 10)

# Added to the wake screen when the second valve has outlets (`dc0/s.java` `X()`).
_SECOND_VALVE = 192
# How the app reads the wake screen; anything else, unreadable included, is the outlets.
_HOME = frozenset({0, 64, 192})
_FAVORITES = frozenset({2, 66, 130, 194})
# Flow Control is this bit of `notification` (`mc0/n.java` `p1`).
_FLOW_CONTROL = 0x04
# Read `Enabled`/`Disabled`, written `1`/`0`.
_SWITCHES = ("waterSavingMode", "accessibilityMode", "hapticFeedback")
# Echoed from each record as read.
_COPIED = (
    "ui",
    "standbyLighting",
    "timeFormat",
    "demoMode",
    "bathFillPresetId",
    "delayStart",
    "toggleOutlets",
    "sounderVolume",
    "proximitySensorMode",
    "proximitySensorDistance",
    "notification",
    "defaultMemory",
)
# Sent empty, as the app sends them: identity it never reads, and a duplicate key.
_BLANK = (
    "standByLighting",
    "proximitySensorTime",
    "status",
    "firmware",
    "bootloder",
    "installedDate",
    "model",
    "serialNo",
)


class UnwritableRecords(ValueError):
    """The records don't read as the app expects, so a whole-record write would guess."""


def ui_records(settings: Any) -> list[dict[str, Any]]:
    """The records in a `gcsadvancestate` `setting` block, in the valve's order."""
    records = settings.get("uiConfig") if isinstance(settings, Mapping) else None
    if not isinstance(records, list):
        return []
    return [dict(record) for record in records if isinstance(record, Mapping)]


def merge_push(
    records: list[dict[str, Any]], attributes: Mapping[str, Any]
) -> list[dict[str, Any]] | None:
    """``records`` with one `READ_GCS_UI_CFG` record merged in: matched by its `UI` against
    the read's `ui`, as the app matches them. None when it names no record held."""
    name = attributes.get("UI")
    for index, record in enumerate(records):
        if isinstance(name, str) and record.get("ui") == name:
            merged = list(records)
            merged[index] = {
                **record,
                **{key: value for key, value in attributes.items() if key != "UI"},
            }
            return merged
    return None


def switch_on(record: Mapping[str, Any], field: str) -> bool | None:
    """A switch as read: `Enabled` or `Disabled`, else unknown."""
    return {"Enabled": True, "Disabled": False}.get(str(record.get(field)))


def panel_language(record: Mapping[str, Any]) -> str | None:
    pair = (record.get("language"), record.get("languageRegion"))
    return next((key for key, value in PANEL_LANGUAGES.items() if value == pair), None)


def panel_units(record: Mapping[str, Any]) -> str | None:
    pair = (record.get("temperatureUnits"), record.get("flowUnits"))
    return next((key for key, value in PANEL_UNITS.items() if value == pair), None)


def _whole(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def brightness(record: Mapping[str, Any]) -> int | None:
    return _whole(record.get("backLight"))


def wake_screen(record: Mapping[str, Any]) -> str:
    """The screen a panel wakes to, as the app reads it (`dc0/s.java` `w0()`)."""
    value = _whole(record.get("defaultHomeScreen"))
    if value in _HOME:
        return "home"
    if value in _FAVORITES:
        return "favorites"
    return "outlets"


def flow_control(record: Mapping[str, Any]) -> bool | None:
    """Whether Flow Control is on: a bit of `notification`."""
    value = _whole(record.get("notification"))
    return None if value is None else bool(value & _FLOW_CONTROL)


def _written(value: Any) -> str:
    """A switch as written: ``1`` for `Enabled`, ``0`` for anything else, as the app's
    conversion does (`mc0/n.java` `y0`)."""
    return "1" if value == "Enabled" else "0"


def control_panel_writes(
    records: list[Mapping[str, Any]],
    changes: Mapping[str, str],
    *,
    second_valve: bool,
    flow_control_on: bool | None = None,
) -> list[dict[str, str]]:
    """Each record's `gcsUIConfigModel`, as the app's Control Panel builds it
    (`dc0/s.java` `M()`), with ``changes`` (written form) applied to the shared settings.

    ``flow_control_on`` sets Flow Control in every record, as its own switch writes it.
    Raises `UnwritableRecords` when a record doesn't read as the app expects, or the
    result is one its Save wouldn't send.
    """
    if not records:
        raise UnwritableRecords("no touch panel reported")
    for record in records:
        if not isinstance(record.get("ui"), str) or not record["ui"]:
            raise UnwritableRecords("a touch panel without its name")
        if str(record.get("defaultMemory")) != "1":
            raise UnwritableRecords("a touch panel whose setup isn't finished")
        for field in _SWITCHES:
            if record.get(field) not in (None, "Enabled", "Disabled"):
                raise UnwritableRecords(f"{field} reads {record.get(field)!r}")
    first = records[0]
    shared: dict[str, Any] = {
        "temperatureUnits": first.get("temperatureUnits"),
        "flowUnits": first.get("flowUnits"),
        "accessibilityMode": _written(first.get("accessibilityMode")),
        "hapticFeedback": _written(first.get("hapticFeedback")),
        "backLight": first.get("backLight"),
        "defaultHomeScreen": str(
            WAKE_SCREENS[wake_screen(first)] + (_SECOND_VALVE if second_valve else 0)
        ),
        "language": first.get("language"),
        "languageRegion": first.get("languageRegion"),
        **changes,
    }
    if flow_control_on is not None:
        notification = _whole(first.get("notification"))
        if notification is None:
            raise UnwritableRecords("notification is unreadable")
        shared["notification"] = str(
            notification | _FLOW_CONTROL
            if flow_control_on
            else notification & ~_FLOW_CONTROL
        )
    # The app's Save waits for a unit pair it can show; it echoes any language.
    if (shared["temperatureUnits"], shared["flowUnits"]) not in PANEL_UNITS.values():
        raise UnwritableRecords("the panel's units are unreadable")
    for field in ("language", "languageRegion", "backLight"):
        if shared[field] in (None, ""):
            raise UnwritableRecords(f"{field} is unreadable")
    writes = []
    for record in records:
        body: dict[str, Any] = {key: record.get(key) for key in _COPIED}
        if record.get("waterSavingMode") is not None:
            body["waterSavingMode"] = _written(record["waterSavingMode"])
        body.update(shared)
        body.update(dict.fromkeys(_BLANK, ""))
        # Every value a string; a null left out, as the app's converter leaves it out.
        writes.append(
            {key: str(value) for key, value in body.items() if value is not None}
        )
    return writes


def read_back(record: Mapping[str, Any], written: Mapping[str, str]) -> dict[str, Any]:
    """A record as it should read once ``written`` has applied: the written values in the
    read's form, and what the write sent empty kept as read."""
    held = dict(record)
    for key, value in written.items():
        if key in _BLANK:
            continue
        held[key] = (
            ("Enabled" if value == "1" else "Disabled") if key in _SWITCHES else value
        )
    return held
