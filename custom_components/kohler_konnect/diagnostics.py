"""Diagnostics for Kohler Konnect — the "Download diagnostics" button.

Every button (the config entry, and each valve, controller and faucet page) reports the
**whole installation**, since the products form one plumbing system: every device appears
in ``valves``, ``controllers`` and ``faucets``. Only ``requested_for`` and the singular
``valve`` / ``controller`` blocks follow the button and describe the pressed device; a
faucet's button gets a singular ``faucet`` block instead. The singular blocks keep the
report's shape for single-device accounts; the lists are the full picture.

The report is the evidence for the README's hardware support matrix: model and outlet
split as detected, which devices exist, what each reports, whether limits arrived. Users
attach it to public "hardware report" issues.

⚠️ A report never contains credentials, account identity (username, tenant id), device
identity (device ids, serials, the mobile registration id — Kohler device ids double as
cloud addresses), or names the owner chose (presets, favorites, light groups): those are
counted or replaced with generic labels. `TO_REDACT` covers the entry's known keys; values
copied from Kohler's records, whose shapes are not fully known, also pass the
`_identity_shaped` check.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import (
    CONF_MOBILE_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    CONF_VALVES,
    DOMAIN,
    OUTLET_TYPE_NAMES,
    PRESET_HIDDEN_IDS,
    SHOWER_ON_PRESET_ID,
)
from .coordinator import Controller, KohlerKonnectCoordinator, Valve
from .konnect import AuthError, GcsState
from .konnect.valve_hex import ValveHexError, decode_word
from .products import PRODUCTS, Product

_LOGGER = logging.getLogger(__name__)

# Entry data/options keys whose values are secrets or identity. The rest of the entry is
# configuration a report needs (model, outlet split, units, per-valve settings).
TO_REDACT = {
    CONF_USERNAME,
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    CONF_MOBILE_DEVICE_ID,
}


def _redact_entry(
    coordinator: KohlerKonnectCoordinator, section: Mapping[str, Any]
) -> dict[str, Any]:
    """Redact an entry section, including the device ids that key `CONF_VALVES`.

    `async_redact_data` redacts values, never dictionary keys, so the ids are replaced
    here with the `valve_<n>` labels `requested_for` uses (cloud order), which still match
    the `valves` list. An id matching no current valve becomes `valve_unknown`.
    """
    labels = {
        valve.device_id: f"valve_{index}"
        for index, valve in enumerate(coordinator.valves)
    }
    redacted = async_redact_data(dict(section), TO_REDACT)
    valves = redacted.get(CONF_VALVES)
    if isinstance(valves, Mapping):
        redacted[CONF_VALVES] = {
            labels.get(str(device_id), "valve_unknown"): settings
            for device_id, settings in valves.items()
        }
    return redacted


def _word(word: Any) -> dict[str, Any] | None:
    """One zone's valve word: the wire hex verbatim, then the decoded reading.

    ``raw`` is None for a word seeded from REST, which has no wire form. It is kept
    because the raw word settles a disputed decode on unfamiliar hardware.
    """
    if word is None:
        return None
    return {
        "raw": word.raw or None,
        "temperature_celsius": word.temperature_celsius,
        "flow_percent": word.flow_percent,
        "outlet_mask": word.outlet_mask,
        "paused": word.paused,
        "at_temperature": word.at_temperature,
        "at_flow": word.at_flow,
        "error_flag": word.error_flag,
        "measured_temperature_celsius": word.measured_temperature_celsius,
        "measured_flow_percent": word.measured_flow_percent,
    }


# Key-name fragments that mark a version, wherever it sits in the record. Matched on the
# key, not the value: a version may be a string ("2.20"), an int or a float, and matching
# on value shape would also catch timestamps, ids and counts.
_VERSION_KEY_HINTS = ("firmware", "version", "swrev", "revision", "build")

# Keys containing a hint that are not versions (update bookkeeping, flags, type labels).
# Exact names, so a new version key is never dropped by accident.
_VERSION_KEY_SKIP = {
    "firmwaretype",
    "firmwareupdate",
    "isfirmwareupdateavailable",
    "issinglefirmwareupdate",
    "versionblocks",
}

# A version is short; a longer string is described (`_described`), never copied.
_VERSION_VALUE_MAX_LEN = 24

# ⚠️ Length alone does not keep identity out: a device id (`gcs-` plus ten characters)
# fits under the ceiling above. A copied string matching any of these — device id, GUID,
# long hex run, connection-string fragment — is described instead.
_IDENTIFIER_VALUE = re.compile(
    r"(?:^|[^a-z0-9])(?:gcs|hub)-[a-z0-9]{4,}"  # device ids
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # GUID
    r"|[0-9a-f]{16,}"  # long hex run: serials, keys
    r"|(?:hostname|deviceid|sharedaccesskey|accountkey)=",  # connection strings
    re.IGNORECASE,
)


def _identity_shaped(value: str) -> bool:
    """True when a value looks like identity rather than a version number."""
    return bool(_IDENTIFIER_VALUE.search(value))


def _described(value: str) -> str:
    """A string withheld from the report, as its type and length."""
    return f"<{type(value).__name__}, {len(value)} chars>"


def _guarded_block(node: Any) -> Any:
    """A version block copied whole, each string checked as `_version_fields` checks one.

    An update record is where a download address, device id or signed URL would sit, so
    a string that is too long or `_identity_shaped` is described rather than copied.
    """
    if isinstance(node, Mapping):
        return {key: _guarded_block(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_guarded_block(item) for item in node]
    if isinstance(node, str) and (
        len(node) > _VERSION_VALUE_MAX_LEN or _identity_shaped(node)
    ):
        return _described(node)
    return node


def _version_fields(
    node: Any, path: str = "", found: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Every version-shaped field in a record, by dotted path.

    `gcs-configuration` carries separate interface, valve and gateway firmwares
    (`docs/protocol/gcs_valve.md` §9), and only the interface's sits in the blocks
    `_configuration_report` copies whole. The others live in blocks that also describe
    the plumbing and cloud addressing, so this takes only values whose key names a
    version, leaving their siblings behind. A string longer than `_VERSION_VALUE_MAX_LEN`,
    or `_identity_shaped`, is described instead of copied.
    """
    if found is None:
        found = {}
    if isinstance(node, Mapping):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            lowered = str(key).lower()
            if isinstance(value, (Mapping, list)):
                _version_fields(value, here, found)
                continue
            if lowered in _VERSION_KEY_SKIP:
                continue
            if not any(hint in lowered for hint in _VERSION_KEY_HINTS):
                continue
            if isinstance(value, str) and (
                len(value) > _VERSION_VALUE_MAX_LEN or _identity_shaped(value)
            ):
                found[here] = _described(value)
            else:
                found[here] = value
    elif isinstance(node, list):
        # Indexed, so two valves' entries stay distinguishable in the report.
        for index, item in enumerate(node[:8]):
            _version_fields(item, f"{path}[{index}]", found)
    return found


def _configuration_report(valve: Valve) -> dict[str, Any]:
    """What `gcs-configuration` returned, summarised rather than reproduced.

    The structural blocks describe the plumbing and may carry identity, so they appear
    only as present-and-not-null (`populated`); versions are copied through the identity
    checks. `read: False` means there is no record yet (not read, or the read failed), as
    distinct from a record whose fields are all null.
    """
    configuration = valve.configuration
    if not configuration:
        return {"read": configuration is not None, "populated": {}, "firmware": None}

    # Null on a controller-attached valve (`docs/protocol/gcs_valve.md` §1). Named so a
    # controller-free valve's report shows which of them arrive.
    structural = (
        "zoneone",
        "zonetwo",
        "parts",
        "valve1Settings",
        "valve2Settings",
        "systemConfiguration",
        "systemSettings",
    )
    # Version strings and update state, no plumbing: copied whole through `_guarded_block`.
    version_blocks = {
        key: _guarded_block(configuration.get(key))
        for key in ("firmwareUpdate", "otaReportedProperties", "version")
        if key in configuration
    }

    return {
        "read": True,
        "firmware": valve.firmware,
        "version_blocks": version_blocks,
        # Every version-shaped field in the record, wherever it sits: the valve and gateway
        # firmwares are outside `version_blocks`.
        "version_fields": _version_fields(configuration),
        # True where the key is present and not null.
        "populated": {key: configuration.get(key) is not None for key in structural},
        # Every other key, named but not copied, so an unfamiliar field shows up without
        # its contents.
        "other_keys": sorted(
            key for key in configuration if key not in structural and key != "about"
        ),
        # Through `Valve.about`, which handles both nestings of `about`
        # (`docs/protocol/gcs_valve.md` §2). Key names only; the versions in it reach the
        # report through `version_fields`.
        "about_keys": sorted(valve.about),
        # When Kohler's cloud created the device record and last changed it.
        "created_time": configuration.get("createdTime"),
        "updated_timestamp": configuration.get("updatedTimestamp"),
    }


#: Fields of a firmware read worth reporting: versions and the update verdict. `url` (the
#: image's download address) and anything else stay out.
_FIRMWARE_FIELDS = (
    "currentFirmware",
    "firmware",
    "firmwareUpdateAvailable",
    "mandatoryUpdate",
    "otaStatus",
    "skip",
)


def _firmware_report(info: Mapping[str, Any] | None) -> dict[str, Any]:
    info = info or {}
    return {key: info.get(key) for key in _FIRMWARE_FIELDS if key in info}


def _about_report(about: Mapping[str, Any] | None) -> dict[str, Any]:
    """`gcs-configuration/{id}/about`, with each serial reduced to `serial_present`."""
    about = about or {}

    def part(entry: Any) -> dict[str, Any]:
        if not isinstance(entry, Mapping):
            return {}
        return {
            **{
                k: entry.get(k)
                for k in ("model", "name", "firmware", "status")
                if k in entry
            },
            "serial_present": bool(entry.get("serialNo")),
        }

    valves = about.get("valvesConfigInfo")
    interfaces = about.get("interfacesConfigInfo")
    return {
        "gateway": part(about.get("gatewayConfigInfo")),
        "valves": [part(v) for v in valves] if isinstance(valves, list) else [],
        "interfaces": (
            [part(i) for i in interfaces] if isinstance(interfaces, list) else []
        ),
    }


def _fault_log(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """`gcs-diagnostics` entries, ids stripped, minus `errorCode "0"` as the app does."""
    details = (payload or {}).get("errorDetails")
    keep = (
        "errorCode",
        "title",
        "description",
        "errorState",
        "isActive",
        "timestamp",
        "valveId",
        "component",
        "area",
    )
    return [
        {k: entry.get(k) for k in keep if k in entry}
        for entry in (details if isinstance(details, list) else [])
        if isinstance(entry, Mapping) and str(entry.get("errorCode") or "0") != "0"
    ]


def _cloud_watch_report(valve: Valve) -> dict[str, Any]:
    """The `Cloud Connection` sensor's attributes (`CloudConnectionWatch.attributes`).

    The values Kohler supplies — the reported state, its timestamp, and an error's text,
    which can quote a reply body — pass the `_identity_shaped` check.
    """
    attributes = dict(valve.cloud_watch.attributes)
    for key in ("connection_state", "cloud_last_connected", "last_error"):
        value = attributes.get(key)
        if isinstance(value, str) and _identity_shaped(value):
            attributes[key] = _described(value)
    return attributes


def _default_shower_report(valve: Valve) -> dict[str, Any]:
    """Preset 1's stored words, and what the `Shower on` switch would send from them now.

    `refused_because` is the switch's own reason (`Valve.default_shower_words`);
    otherwise `would_send` holds each zone's command word and its reading. Outlets are
    named by the valve's type codes, never by anything the owner chose.
    """
    gcs = valve.gcs_state
    preset = gcs.presets.get(SHOWER_ON_PRESET_ID)
    report: dict[str, Any] = {
        "preset_id": SHOWER_ON_PRESET_ID,
        # As Kohler stores them, `Valve1` first. None when the valve has not reported
        # preset 1 at all, [] when it reported one with no words.
        "stored_words": None if preset is None else list(preset.valve_words),
    }
    try:
        words = valve.default_shower_words()
    except ValveHexError as err:
        report["refused_because"] = str(err)
        return report
    # The model's zones only: a single-zone valve's second word is the unused sentinel.
    report["would_send"] = {
        str(zone): _default_shower_zone(gcs, zone, words[zone - 1])
        for zone in valve.model.zones
    }
    return report


def _default_shower_zone(gcs: GcsState, zone: int, word: str) -> dict[str, Any]:
    """One zone of the default shower: the command word, its outlets and setpoints."""
    decoded = decode_word(word)
    outlets = []
    for bit in range(3):
        if decoded.outlet_mask >> bit & 1:
            # Each zone owns three `outLetId` slots, whatever the model populates.
            limits = gcs.outlet_limits.get((zone - 1) * 3 + bit)
            name = (
                None
                if limits is None or limits.outlet_type is None
                else OUTLET_TYPE_NAMES.get(limits.outlet_type)
            )
            outlets.append(name or f"outlet {bit + 1}")
    return {
        "word": word,
        "outlets": outlets,
        "temperature_celsius": decoded.temperature_celsius,
        "flow_percent": decoded.flow_percent,
    }


# A touch panel's identity, left out of its record in a report.
_PANEL_IDENTITY = frozenset(
    {"serialNo", "model", "firmware", "bootloder", "installedDate", "status"}
)


def _valve_report(valve: Valve) -> dict[str, Any]:
    """One valve's state, limits, run-time clocks and warm-up settings."""
    gcs = valve.gcs_state
    model = valve.model
    return {
        # The layout this valve decodes with; can differ from the entry's `model` when the
        # account has several valves.
        "model": {
            "sku": model.sku,
            "outlets_valve1": model.outlets_valve1,
            "outlets_valve2": model.outlets_valve2,
        },
        "zone_words": {str(zone): _word(gcs.zone_word(zone)) for zone in model.zones},
        "is_running": gcs.is_running,
        "is_paused": gcs.is_paused,
        "warmup_mode": gcs.warmup_mode,
        "warmup_in_progress": gcs.warmup_in_progress,
        "active_preset_id": gcs.active_preset_id,
        # The valve's own session flag (`currentSystemState`), to compare with the decoded
        # `is_running` / `is_paused`.
        "system_state": gcs.system_state,
        "water": {
            # `totalFlow` as sent. Not a meter (`GcsState.total_flow`,
            # `docs/protocol/gcs_valve.md` §2); reported as evidence of what it is.
            "total_flow_raw": gcs.total_flow,
            # Unit unknown; reported so real reports can settle it.
            "total_volume": gcs.total_volume,
        },
        "presets": {
            "slots_seen": len(gcs.presets),
            # `selectable` counts before `PRESET_HIDDEN_IDS` are removed; `offered` counts
            # after, and is what the picker holds. They differ by the default-shower slot,
            # which is startable but never listed.
            "selectable": sum(1 for p in gcs.presets.values() if p.is_selectable),
            "offered": len(gcs.selectable_presets(hidden=PRESET_HIDDEN_IDS)),
            "hidden_ids_present": sorted(
                p.preset_id
                for p in gcs.presets.values()
                if p.preset_id in PRESET_HIDDEN_IDS and not p.is_empty
            ),
            "experiences": sum(1 for p in gcs.presets.values() if p.is_experience),
            "empty": sum(1 for p in gcs.presets.values() if p.is_empty),
            # Bath-fill presets' fill amounts and run times (`gcs_valve.md` §8).
            "bath_fills": [
                {"volume": p.volume, "time": p.run_time}
                for p in gcs.presets.values()
                if p.is_bath_fill
            ],
        },
        # The touch panels' records as read (`gcs_valve.md` §6), less their identity: the
        # shapes a touch-panel write is checked against.
        "touch_panels": [
            {key: value for key, value in record.items() if key not in _PANEL_IDENTITY}
            for record in gcs.ui_config
        ],
        "default_shower": _default_shower_report(valve),
        # Keyed by the device's own 0-based outLetId. Fills in gradually over MQTT
        # and REST; a missing outlet means "never announced", not zero.
        "outlet_limits": {
            str(outlet_id): {
                "minimum_flow_byte": lim.minimum_flow_byte,
                "maximum_flow_byte": lim.maximum_flow_byte,
                "maximum_run_time": lim.maximum_run_time,
                "default_flow_byte": lim.default_flow_byte,
                # The valve's type code, unmapped (`docs/protocol/gcs_valve.md` §1.2).
                "outlet_type": lim.outlet_type,
                # None for a code `OUTLET_TYPE_NAMES` cannot name, which flags it for an
                # owner to confirm.
                "outlet_type_name": (
                    None
                    if lim.outlet_type is None
                    else OUTLET_TYPE_NAMES.get(lim.outlet_type)
                ),
                # The scald limit, in tenths of °C.
                "maximum_temperature_tenths": lim.maximum_temperature_tenths,
                # The other fields `writeoutletconfig` replaces, so a report shows what a
                # write must echo back (`OutletLimits`).
                "minimum_temperature_tenths": lim.minimum_temperature_tenths,
                "default_temperature_tenths": lim.default_temperature_tenths,
                "outlet_flags": lim.outlet_flags,
            }
            for outlet_id, lim in sorted(gcs.outlet_limits.items())
        },
        "last_update": gcs.last_update,
        "cloud_connected": valve.cloud_watch.connected,
        "cloud_watch": _cloud_watch_report(valve),
        # `gcs-configuration`, read once at the first seed. Its `populated` shows whether a
        # controller-free valve fills the structural fields a controller-attached one
        # leaves null.
        "configuration": _configuration_report(valve),
        "run_time": {
            "limits_seconds": {
                str(k): v for k, v in sorted(valve.outlet_run_times.items())
            },
            "flowing_for_seconds": {
                str(zone): valve.zone_flowing_for(zone) for zone in model.zones
            },
        },
        "warmup": {
            "mode": gcs.warmup_mode,
            "auto_restore": valve.warmup_auto_restore,
            "restores_to": valve.last_warmup_mode,
        },
        # Includes `currentSystemState == error`, never yet captured
        # (`docs/protocol/gcs_valve.md` §7).
        "has_fault": gcs.has_fault,
        # What the Problem sensor turns on for, from the last fault-log read (each seed and
        # twice a day): code, area, valve and timestamp only. `fault_log`, read as the
        # report is built, holds the whole log.
        "active_faults": [dict(fault) for fault in valve.active_faults],
        "dispensed_volume": gcs.dispensed_volume,
        "about": _about_report(valve.about_parts),
        "firmware": {
            part: _firmware_report(info)
            for part, info in (valve.firmware_info or {}).items()
        },
    }


def _light_groups_report(lights: Mapping[str, bool]) -> dict[str, bool]:
    """Each light group's on/off, keyed by Kohler's group letter or a generic label.

    MQTT names a group by letter (`lightgroupA` → `a`), but REST can name it by the
    display name the owner chose. A single-letter key passes; any other becomes
    `group_<n>`, in sorted order.
    """
    named = iter(range(1, len(lights) + 1))
    return {
        (
            key
            if len(key) == 1 and key.isascii() and key.isalpha()
            else f"group_{next(named)}"
        ): on
        for key, on in sorted(lights.items())
    }


def _controller_report(controller: Controller) -> dict[str, Any]:
    """One controller's state, capabilities and layout. Device id deliberately absent."""
    hub = controller.state
    caps = controller.capabilities
    model = controller.model
    settings = controller.settings
    return {
        # The layout this controller decodes with; can differ from the entry's `model`
        # when the account has several controllers.
        "model": {
            "sku": model.sku,
            "outlets_valve1": model.outlets_valve1,
            "outlets_valve2": model.outlets_valve2,
        },
        "zones": {
            str(zone): {
                "status": getattr(z, "status", None),
                "outlets": list(getattr(z, "outlets", ()) or ()),
            }
            for zone, z in sorted(hub.zones.items())
        },
        "is_running": hub.is_running,
        "shower_warmup": hub.shower_warmup,
        "music_on": hub.music_on,
        "steam_on": hub.steam_on,
        "light_on": hub.light_on,
        "favorites_count": len(controller.favorites or []),
        "active_favorite": hub.active_favorite_id is not None,
        "capabilities": {
            "known": caps.known,
            "water": caps.water,
            "music": caps.music,
            "light": caps.light,
            "steam": caps.steam,
        },
        "last_update": hub.last_update,
        "steam_status": hub.steam_status,
        "light_groups": _light_groups_report(hub.lights),
        "active_experience": hub.active_experience,
        "error": hub.error,
        "error_components": dict(hub.error_components),
        "active_errors": [
            {k: e.get(k) for k in ("errorCode", "title", "component", "isActive")}
            for e in controller.active_errors
        ],
        # The LAN address is reduced to its presence: it identifies a home network.
        "settings": {
            "max_shower_duration_minutes": settings.max_shower_duration_minutes,
            "shower_max_temperature": settings.shower_max_temperature,
            "temperature_unit": settings.temperature_unit,
            "flow_rate_enabled": settings.flow_rate_enabled,
            "steam_default_temperature": settings.steam_default_temperature,
            "steam_default_time": settings.steam_default_time,
            "light_group_count": len(settings.light_groups),
            "lan_ip_present": settings.lan_ip is not None,
            "disconnected": list(settings.disconnected),
        },
        "experiences": {
            category: len(items) for category, items in controller.experiences.items()
        },
        "firmware": _firmware_report(controller.firmware_info),
    }


def _build(
    coordinator: KohlerKonnectCoordinator,
    requested_for: str,
    valve_index: int = 0,
    controller_index: int = 0,
    product: tuple[str, int] | None = None,
) -> dict[str, Any]:
    """The whole installation, as this integration currently understands it.

    ``valve_index`` / ``controller_index`` pick the device the singular ``valve`` /
    ``controller`` blocks describe: the one whose button was pressed. With ``product``
    set (a product key and an index into its devices), the singular block is that device,
    under the product's key (``faucet``), and there is no ``valve`` or ``controller``. The
    ``valves`` / ``controllers`` lists, and one list per product (``faucets``), always carry
    every device.
    """
    model = coordinator.model

    payload: dict[str, Any] = {
        "requested_for": requested_for,
        "model": {
            "sku": model.sku,
            "name": model.name,
            "outlets_valve1": model.outlets_valve1,
            "outlets_valve2": model.outlets_valve2,
            "total_outlets": model.total_outlets,
            "zones": model.zones,
        },
        "devices": {
            "valve_present": bool(coordinator.valves),
            "valve_count": len(coordinator.valves),
            "controller_present": bool(coordinator.controllers),
            "controller_count": len(coordinator.controllers),
            **{
                f"{p.key}_count": len(coordinator.products.get(p.key, ()))
                for p in PRODUCTS
            },
        },
        "entry": {
            "data": _redact_entry(coordinator, coordinator.entry.data),
            "options": _redact_entry(coordinator, coordinator.entry.options),
        },
        "stream": {
            "mqtt_connected": bool(coordinator.stream and coordinator.stream.connected),
        },
    }

    # One entry per valve, in the cloud's order, each with its own `run_time` and `warmup`.
    # The singular `valve`, `run_time` and `warmup` describe the pressed valve, and are
    # left out of another product's report.
    shower_blocks = product is None
    reports = [_valve_report(valve) for valve in coordinator.valves]
    if reports and shower_blocks:
        # An out-of-range index is a caller bug, but the report must still build.
        primary = (
            reports[valve_index] if 0 <= valve_index < len(reports) else reports[0]
        )
        payload["valve"] = {
            k: v for k, v in primary.items() if k not in ("run_time", "warmup")
        }
        payload["run_time"] = primary["run_time"]
        payload["warmup"] = primary["warmup"]
    if reports:
        payload["valves"] = reports

    # One entry per controller, in the cloud's order. The singular `controller` describes
    # the pressed controller, and is left out of another product's report.
    reports = [_controller_report(controller) for controller in coordinator.controllers]
    if reports and shower_blocks:
        payload["controller"] = (
            reports[controller_index]
            if 0 <= controller_index < len(reports)
            else reports[0]
        )
    if reports:
        payload["controllers"] = reports

    # One entry per device of every other product, in the cloud's order; from a device's
    # button, its report again as the singular block (`faucet`).
    for each in PRODUCTS:
        devices = coordinator.products.get(each.key)
        if not devices:
            continue
        reports = [_product_report(each, device) for device in devices]
        payload[f"{each.key}s"] = reports
        if product is not None and product[0] == each.key:
            index = product[1]
            if 0 <= index < len(reports):
                payload[each.key] = reports[index]

    return payload


def _product_report(product: Product, device: Any) -> dict[str, Any]:
    """One device's block; a fault in it leaves the rest of the download whole."""
    try:
        return product.diagnostics(device)
    except Exception:
        _LOGGER.exception("Could not report %s", device.device_name)
        return {"error": "This device's report failed; see the log."}


def _coordinator(hass: HomeAssistant, entry: ConfigEntry) -> KohlerKonnectCoordinator:
    return hass.data[DOMAIN][entry.entry_id]


async def _async_add_fault_logs(
    coordinator: KohlerKonnectCoordinator,
    payload: dict[str, Any],
    valve_index: int = 0,
) -> dict[str, Any]:
    """Attach each valve's `gcs-diagnostics` fault log, read now.

    The seed keeps only active entries (`active_faults`); the whole log is read only for
    a report. A failed read costs that valve's log, never the report.

    ⚠️ A failed read is never reported as an empty log: only a reply carrying an
    `errorDetails` list becomes `fault_log`; anything else is `fault_log: None` with
    `fault_log_error`, as `Valve.async_refresh_faults` treats it. An `AuthError` (which
    the client does not catch) is recorded the same way, and the remaining valves are
    not asked, since each would fail alike.

    The singular `valve` block, when present, gets the log of the valve it describes.
    """
    failure: str | None = None
    for index, valve in enumerate(coordinator.valves):
        report: dict[str, Any]
        if failure is None:
            try:
                log = await valve.client.async_get_gcs_diagnostics(valve.device_id)
            except AuthError as err:
                # The type only: the message is the sign-in service's, not ours to copy.
                failure = f"sign-in failed ({type(err).__name__})"
            else:
                details = log.get("errorDetails") if isinstance(log, Mapping) else None
                report = (
                    {"fault_log": _fault_log(log)}
                    if isinstance(details, list)
                    else {
                        "fault_log": None,
                        "fault_log_error": "the read failed or returned no log",
                    }
                )
        if failure is not None:
            report = {"fault_log": None, "fault_log_error": failure}
        payload["valves"][index].update(report)
        if index == valve_index and "valve" in payload:
            payload["valve"].update(report)
    return payload


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Diagnostics from the integration card."""
    coordinator = _coordinator(hass, entry)
    return await _async_add_fault_logs(coordinator, _build(coordinator, "config_entry"))


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Diagnostics from any device page.

    The same whole-installation report as the config entry's, with `requested_for` and
    the singular `valve` / `controller` (or, for another product, its own: `faucet`)
    blocks set to the device whose page it came from.
    """
    coordinator = _coordinator(hass, entry)
    requested_for = "unknown_device"
    # Default to the first of each, as the config-entry button does.
    valve_index = 0
    controller_index = 0
    product: tuple[str, int] | None = None
    for domain, identifier in device.identifiers:
        if domain != DOMAIN:
            continue
        for index, valve in enumerate(coordinator.valves):
            if identifier == valve.device_id or identifier.startswith(
                f"{valve.device_id}_zone_"
            ):
                requested_for = (
                    "valve" if len(coordinator.valves) == 1 else f"valve_{index}"
                )
                valve_index = index
        for index, controller in enumerate(coordinator.controllers):
            if identifier == controller.device_id:
                # Plain "controller" with one; an index into `controllers` with several,
                # since ids are redacted.
                requested_for = (
                    "controller"
                    if len(coordinator.controllers) == 1
                    else f"controller_{index}"
                )
                controller_index = index
        for key, devices in coordinator.products.items():
            for index, product_device in enumerate(devices):
                if identifier == product_device.device_id:
                    # Always an index into the product's list (`faucets`).
                    requested_for = f"{key}_{index}"
                    product = (key, index)
    return await _async_add_fault_logs(
        coordinator,
        _build(coordinator, requested_for, valve_index, controller_index, product),
        valve_index,
    )
