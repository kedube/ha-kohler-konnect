"""Which of a device's entities plays which part in the sidebar panel.

The panel asks for "the valve's Temperature for zone 2", never for an entity id, and this
table answers from the entity registry. It reads unique ids, the one thing about an entity
that never changes once created: an owner can rename an entity or its id, disable it, or
move a valve to zone sub-devices, and the role still resolves.

Every unique id is the Kohler device id, an underscore, then a suffix fixed in the entity's
own class; the tables here are keyed by those suffixes. ⚠️ A new entity needs a row here,
or `test_sidebar.py`'s drift test fails, so the panel never silently misses one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

__all__ = ["Roles", "resolve_roles", "role_for"]

# Kohler's per-period figures, which valves and faucets share.
_USAGE: dict[tuple[str, str], str] = {
    ("sensor", f"{figure}_{period}"): f"{figure}_{role}"
    for figure in ("water", "duration")
    for period, role in (
        ("today", "today"),
        ("this_week", "week"),
        ("this_month", "month"),
        ("this_year", "year"),
    )
}

_EXACT: dict[str, dict[tuple[str, str], str]] = {
    "valve": {
        ("switch", "shower"): "shower",
        ("number", "max_temperature_setting"): "max_temperature",
        ("number", "default_temperature"): "default_temperature",
        ("select", "favourite"): "favorite",
        ("select", "experience"): "experience",
        ("select", "warmup"): "warmup",
        ("select", "max_run_time_setting"): "max_duration",
        ("sensor", "status"): "status",
        ("sensor", "system_state"): "system_state",
        ("sensor", "last_update"): "last_update",
        ("sensor", "firmware"): "interface_firmware",
        ("sensor", "firmware_valve"): "valve_firmware",
        ("sensor", "firmware_valve2"): "second_valve_firmware",
        ("sensor", "firmware_gateway"): "gateway_firmware_version",
        ("sensor", "registered"): "registered",
        ("binary_sensor", "at_temperature"): "at_temperature",
        ("binary_sensor", "problem"): "problem",
        ("binary_sensor", "mqtt_connection"): "mqtt",
        ("binary_sensor", "cloud_connection"): "cloud",
        ("binary_sensor", "preset_active"): "preset_active",
        ("update", "firmware_update"): "firmware",
        ("update", "gateway_firmware_update"): "gateway_firmware",
        ("button", "restart"): "restart",
        ("button", "clear_error_log"): "clear_errors",
        ("button", "new_mqtt_capture"): "new_capture",
        ("switch", "report_log"): "report_log",
        ("switch", "warmup_auto_restore"): "warmup_auto_restore",
        ("binary_sensor", "bath_filling"): "bath_filling",
        # The touch panels' settings (`panel_settings.py`).
        ("select", "valve_panel_language"): "panel_language",
        ("select", "panel_units"): "panel_units",
        ("select", "wake_screen"): "wake_screen",
        ("switch", "haptic_feedback"): "haptic_feedback",
        ("switch", "accessible_display"): "accessible_display",
        ("switch", "flow_control"): "flow_control",
        ("number", "display_brightness"): "display_brightness",
        ("binary_sensor", "water_saving_mode"): "water_saving_mode",
        **_USAGE,
    },
    "controller": {
        ("switch", "shower"): "shower",
        ("switch", "system"): "system",
        ("switch", "steam_control"): "steam",
        ("select", "favourite"): "favorite",
        ("select", "experience"): "experience",
        ("sensor", "status"): "status",
        ("sensor", "max_shower_duration"): "max_duration",
        ("sensor", "last_update"): "last_update",
        ("binary_sensor", "music"): "music",
        ("binary_sensor", "light"): "light",
        ("binary_sensor", "steam"): "steam_state",
        ("binary_sensor", "problem"): "problem",
        ("binary_sensor", "mqtt_connection"): "mqtt",
        ("update", "firmware_update"): "firmware",
        ("button", "clear_error_log"): "clear_errors",
        ("button", "new_mqtt_capture"): "new_capture",
        ("switch", "report_log"): "report_log",
    },
    "faucet": {
        ("switch", "water"): "water",
        ("sensor", "status"): "status",
        ("sensor", "handle_state"): "handle",
        ("sensor", "progress"): "firmware_download",
        ("sensor", "gateway_firmware"): "gateway_firmware_version",
        ("sensor", "last_dispense"): "last_dispensed",
        ("sensor", "last_update"): "last_update",
        ("binary_sensor", "leak"): "leak",
        ("binary_sensor", "dispensing"): "dispensing",
        ("binary_sensor", "connected"): "cloud",
        ("binary_sensor", "mqtt_connection"): "mqtt",
        ("button", "dispense_set_amount"): "dispense_set_amount",
        ("button", "dispense_preset"): "dispense_preset",
        ("button", "clear_leak"): "clear_leak",
        ("number", "dispense_amount"): "dispense_amount",
        ("select", "preset"): "preset",
        ("update", "firmware_update"): "firmware",
        **_USAGE,
    },
}

# Roles that carry a zone, an outlet or an amount. Tried only after `_EXACT`, since a
# faucet's quick-dispense buttons share the `dispense_` prefix with its fixed ones.
_PATTERNS: dict[str, list[tuple[str, re.Pattern[str], str]]] = {
    "valve": [
        ("switch", re.compile(r"zone_(\d)_outlet_(\d)"), "outlet:{0}:{1}"),
        ("number", re.compile(r"temperature_zone_(\d)"), "temperature:{0}"),
        ("number", re.compile(r"flow_zone_(\d)"), "flow:{0}"),
        ("binary_sensor", re.compile(r"zone_(\d)_active"), "zone_active:{0}"),
        ("sensor", re.compile(r"zone_(\d)_hex"), "hex:{0}"),
    ],
    "controller": [
        ("binary_sensor", re.compile(r"zone_(\d)_outlet_(\d)"), "outlet:{0}:{1}"),
        ("sensor", re.compile(r"zone_(\d)_temperature"), "temperature:{0}"),
    ],
    "faucet": [
        ("button", re.compile(r"dispense_(.+)"), "dispense:{0}"),
    ],
}


def role_for(kind: str, domain: str, suffix: str) -> str | None:
    """The role of an entity of this kind of device, from its platform and id suffix.

    A kind with no table here, shown in the panel's generic view, gives each entity its
    suffix as its role, but its water totals the roles every product's have.
    """
    if kind not in _EXACT and kind not in _PATTERNS:
        return _USAGE.get((domain, suffix), suffix)
    role = _EXACT.get(kind, {}).get((domain, suffix))
    if role is not None:
        return role
    for platform, pattern, template in _PATTERNS.get(kind, ()):
        if platform == domain and (match := pattern.fullmatch(suffix)):
            return template.format(*match.groups())
    return None


class Roles:
    """A device's roles, split by whether the entity is enabled."""

    def __init__(self) -> None:
        self.enabled: dict[str, str] = {}
        self.disabled: dict[str, str] = {}
        #: Roles of controls not yet seen to work on hardware (`UntestedControl`), on or off.
        self.untested: list[str] = []
        #: Whether one of those is a main control rather than a setting: then the device
        #: itself is untested, not just a setting of it.
        self.untested_device = False


def resolve_roles(
    hass: HomeAssistant, entry_id: str, kind: str, kohler_id: str
) -> Roles:
    """Every role this device's entities fill, by entity id."""
    prefix = f"{kohler_id}_"
    roles = Roles()
    entries: Iterable[er.RegistryEntry] = er.async_entries_for_config_entry(
        er.async_get(hass), entry_id
    )
    for entity in entries:
        if not entity.unique_id.startswith(prefix):
            continue
        role = role_for(kind, entity.domain, entity.unique_id.removeprefix(prefix))
        if role is None:
            continue
        target = roles.disabled if entity.disabled_by else roles.enabled
        target[role] = entity.entity_id
        if (entity.capabilities or {}).get("untested"):
            roles.untested.append(role)
            if entity.entity_category is None:
                roles.untested_device = True
    return roles
