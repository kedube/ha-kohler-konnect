"""Coordinator: holds the MQTT stream, the REST client, and per-device state.

One per Kohler account. It owns what every device shares — the sign-in, the REST client and
the one MQTT connection — and the per-device objects: a `Valve` for each Anthem valve, a
`Controller` for each Anthem Plus, and for every other product (`products.py`) a
coordinator per device on its own clock, such as a faucet's (`faucet/coordinator.py`).

Shower state is push-only: MQTT carries every change, and REST is read on events, never on
a clock. The one exception is firmware availability, which nothing announces; it is
checked twice a day.

* Cold start: MQTT is silent until the shower next changes, so one REST read at setup
  seeds everything. It is made while the stream connects, so nothing changes unseen.
* Reconnects: the broker replays nothing on connect (`docs/protocol/platform.md` §4.2),
  so every reconnect re-seeds. The first connect re-reads only the live state that setup
  read before the stream was up.
* Token rotation: B2C issues a new refresh token on every refresh and invalidates the old
  one, so it is written back to the config entry whenever it changes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import time
import uuid
from collections import Counter, deque
from collections.abc import Coroutine, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .cloud_watch import CloudConnectionWatch
from .const import (
    CONF_MOBILE_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_REPORT_LOG_FILE,
    CONF_TEMPERATURE_UNIT,
    CONF_TENANT_ID,
    CONF_VALVE_MODEL,
    CONF_VALVES,
    CONF_WATER_UNITS,
    CONF_ZONE_GROUPING,
    CONF_ZONE_OUTLETS,
    DEFAULT_FLOW_PERCENT,
    DEFAULT_ZONE_GROUPING,
    DEVICE_NAME_CONTROLLER,
    DEVICE_NAME_VALVE,
    DOMAIN,
    ENABLE_RAW_MQTT_LOG,
    ENABLE_WARMUP_DEBUG_LOG,
    FIRMWARE_CHECK_INTERVAL,
    OUTLET_WRITE_VERIFY_DELAY_SECONDS,
    RAW_MQTT_LOG_DIR,
    RAW_MQTT_LOG_KEEP_FILES,
    RAW_MQTT_LOG_MAX_BYTES,
    RELOAD_IGNORED_DATA_KEYS,
    RELOAD_IGNORED_OPTION_KEYS,
    REPORT_LOG_DIR_NAME,
    REPORT_LOG_MAX_BYTES,
    SCAN_INTERVAL,
    SHOWER_ON_PRESET_ID,
    USAGE_REFRESH_DELAY_SECONDS,
    USAGE_RETRY_DELAY_SECONDS,
    VALVE_OFFLINE,
    WARMUP_CONTEXT_AFTER_SECONDS,
    WARMUP_CONTEXT_BEFORE_SECONDS,
    WARMUP_CONTEXT_MAX_MESSAGES,
    WARMUP_DEBUG_LOG_KEEP_FILES,
    ZONE_GROUPING_MODES,
    device_issue_key,
)
from .faucet.coordinator import FaucetCoordinator
from .konnect import (
    MSG_GCS_SOLO_STATUS,
    MSG_GCS_WARMUP_STATUS,
    WARMUP_README,
    AuthError,
    AuthUnavailable,
    DebugJournal,
    Device,
    DeviceOffline,
    Envelope,
    GcsDevice,
    GcsState,
    HubCapabilities,
    HubDevice,
    HubSettings,
    HubState,
    KohlerAuth,
    KohlerClient,
    KohlerError,
    KonnectMqttStream,
    RawMqttLog,
    ReportLog,
    ValveModel,
    ZoneClock,
    credential_is_dead,
    describe_topology,
    get_valve_model,
    model_for_topology,
    topology_from_hub_configuration,
    topology_from_valve_settings,
    unit_to_celsius,
)
from .konnect.const import (
    MSG_GCS_OUTLET_CONFIG,
    MSG_HUB_LIGHT,
    MSG_HUB_MUSIC,
    MSG_HUB_SHOWER_VALVE,
    MSG_HUB_STEAM,
)
from .konnect.entry_reload import reload_signature
from .konnect.gcs import validate_outlet_record
from .konnect.models import DEFAULT_VALVE_MODEL
from .konnect.state import outlet_limits_from_settings
from .konnect.ui_config import (
    UnwritableRecords,
    control_panel_writes,
    read_back,
    ui_records,
)
from .konnect.usage import usage_series
from .konnect.valve_hex import (
    FLOW_BYTE_MAX,
    TEMPERATURE_MAX_TENTHS,
    TEMPERATURE_TENTHS_PER_DEGREE,
    UNUSED_VALVE_WORD,
    VALVE1_PREFIX,
    VALVE2_PREFIX,
    VALVE_STOP_MASK,
    ValveHexError,
    decode_word,
    encode_word,
    flow_byte_to_percent,
    flow_percent_to_byte,
    normalize_word,
    preset_words_to_pair,
)
from .konnect.warmup_resume import (
    RESUME_DEADLINE_SECONDS,
    WARMUP_WINDOW_SECONDS,
    Decision,
    WarmupResume,
)
from .products import FAUCETS, PRODUCTS, Product, ProductDevice
from .registry import device_by_identifier
from .warmup_manager import WarmupManager

_LOGGER = logging.getLogger(__name__)

#: The `GcsState` fields of a `gcs-state` read that a valve message can carry too: the zone
#: words and the session fields `GCS_SOLO_STS` reports with them (`GCS_WARM_STS` carries
#: `warmup_mode`). `Valve._push_serials` counts each field a message carried
#: (`GcsState.carried`): a word only if it decoded, a session field only if present. A
#: field carried while a read is in flight, or just before it went out, is newer than the
#: read for that field only; see `Valve.apply_rest_reply`. Presets, outlet records and the
#: dispensed volume come from other reads, so messages about them conflict with nothing.
_GCS_WORD_FIELDS = ("valve1", "valve2")
_GCS_SESSION_FIELDS = (
    "warmup_in_progress",
    "system_state",
    "active_preset_id",
    "total_volume",
    "total_flow",
)

#: How far `gcs-state` can trail the valve's own reports, in seconds, with margin: right
#: after a write it still shows the old value and catches up in about 3 s. A report that
#: arrived this shortly before a read was sent may be newer than the reply, so what it
#: carried is kept over the reply's.
_GCS_STATE_TRAILS_SECONDS = 5.0

#: How long after setup's reads went out the stream's first connect may come and still be
#: caught up with the live state alone; one later — a first start that failed, connecting
#: on the back-off — reads everything again. See `KohlerKonnectCoordinator._first_connect_late`.
_FIRST_CONNECT_GRACE_SECONDS = 30.0

#: The controller's counterpart, by message: the `HubState` fields each carries of a
#: `hub-state` read, and the `errorComponent` keys its `errorstate` feeds. `MUSIC_STS`,
#: `STEAM_STS` and `LIGHT_STS` report one accessory each and `SHOWER_VALVE_STS` the zones,
#: so one of them overtaking the read says nothing about the rest of the reply.
_HUB_PUSH_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    MSG_HUB_SHOWER_VALVE: (("zones", "shower_warmup"), ("valve1", "valve2")),
    MSG_HUB_MUSIC: (("music_on",), ("amplifier",)),
    MSG_HUB_STEAM: (
        (
            "steam_on",
            "steam_status",
            "steam_temperature",
            "steam_start_time",
            "steam_total_time",
        ),
        ("steam",),
    ),
    MSG_HUB_LIGHT: (("lights",), ("light",)),
}

#: `hass.data` key for each device's own outlet split, as last read this run, by entry and
#: device id — a valve's from its settings, a controller's (the valve behind it) from its
#: configuration. Outlives a reload, which is the point: see `_async_note_topology`.
_VALVE_TOPOLOGY = f"{DOMAIN}_valve_topology"

#: How long an unload waits for a sign-in refresh already in flight; see
#: `KohlerKonnectCoordinator.async_shutdown_stream`.
_REFRESH_WAIT_SECONDS = 10

#: Messages held while setup does not yet know which devices the account has; see
#: `KohlerKonnectCoordinator._handle_envelope`. The stream reaches that point within
#: seconds, so this only bounds an account read that hangs.
_EARLY_MESSAGES_KEPT = 200


@contextmanager
def _keeping(
    state: Any, fields: Iterable[str], components: Iterable[str] = ()
) -> Iterator[None]:
    """Run the block, then put ``fields`` of ``state`` back as they were.

    How a REST read is applied around a push that overtook it: the read sets everything it
    carries, and what the push carried goes back to the push's values. A dict is held as a
    copy, since `HubState.apply_rest_state` fills `zones` and `lights` in place; anything
    else as the very object it was — a valve word's identity is how `Valve._valve_baseline`
    tells a report from the words it sent. ``components`` are keys of a controller's
    `error_components`, which messages and the read share.
    """
    kept: dict[str, Any] = {}
    for name in fields:
        value = getattr(state, name)
        kept[name] = dict(value) if isinstance(value, dict) else value
    kept_components = {key: state.error_components.get(key) for key in components}
    yield
    for name, value in kept.items():
        setattr(state, name, value)
    for key, flag in kept_components.items():
        if flag is None:
            state.error_components.pop(key, None)
        else:
            state.error_components[key] = flag


def _raise_first(results: Iterable[Any]) -> None:
    """Raise the first exception `gather(..., return_exceptions=True)` collected, if any.

    For reads that must all finish before a failure is reported: `gather` without
    `return_exceptions` raises the first failure at once and leaves the rest running, against
    a setup that has already failed.
    """
    for result in results:
        if isinstance(result, BaseException):
            raise result


def _usage_window(days_back: int) -> tuple[str, str]:
    """`FromDate` and `ToDate` for a usage read covering ``days_back`` days up to today.

    "Today" is the later of the local and UTC dates. East of UTC the UTC date is still
    yesterday after local midnight; on accounts whose buckets carry UTC dates, a US
    evening's water sits under tomorrow's UTC date (which `water.py` reads as today's). The
    window starts from the earlier date, so it only ever grows.
    """
    local = dt_util.now().date()
    utc = datetime.now(UTC).date()
    start = min(local, utc) - timedelta(days=days_back)
    return start.isoformat(), max(local, utc).isoformat()


def _fault_is_active(entry: Any) -> bool:
    """Whether one `gcs-diagnostics` entry is an active fault the app would show.

    The app drops entries whose `errorCode` is `"0"` or empty (`docs/protocol/platform.md`
    §8), as `diagnostics._fault_log` does. `isActive` is a JSON bool; a string spelling is
    accepted too rather than read as inactive.
    """
    if not isinstance(entry, dict):
        return False
    if str(entry.get("errorCode") or "").strip() in ("", "0"):
        return False
    return str(entry.get("isActive")).strip().lower() in ("true", "1")


def _daily_usage_volumes(payload: dict[str, Any] | None) -> dict[str, float]:
    """Per-day volume map (`YYYY-MM-DD -> litres`) from a `gcs-usage` payload."""
    if not isinstance(payload, dict):
        return {}
    volumes: dict[str, float] = {}
    for entry in usage_series(payload):
        key = str(entry.get("intervalKey") or "")
        vol = entry.get("volume")
        if key and isinstance(vol, (int, float)):
            volumes[key] = float(vol)
    return volumes


#: Keys that hold the version a device is *running*, in the two nested blocks Kohler uses.
#: Ordered — the first match wins. Deliberately excludes anything desired/target shaped:
#: ``firmwareUpdate`` can carry the version the cloud wants installed, and reporting that as
#: the running version would be worse than reporting nothing.
_FIRMWARE_CURRENT_KEYS = (
    # An OTA record reports `updatedVersion` (now running) beside `initialVersion` (before
    # the update), so `updatedVersion` leads.
    "updatedVersion",
    "currentFirmwareVersion",
    "currentVersion",
    "firmwareVersion",
    "swVersion",
    "firmware",
    "version",
)

#: A valve reports several OTA payloads, distinguished by `firmwareType`: `Application` is
#: the valve's firmware, `Assets` the bundled UI artwork with its own unrelated version.
#: The application build is preferred; anything else is only a fallback.
_FIRMWARE_PREFERRED_TYPE = "Application"


def _firmware_string(value: Any) -> str | None:
    """A firmware version as a non-empty string, or None.

    Numbers are stringified (`74` for `"00.74"` is still a version). Bools are rejected,
    since they would otherwise pass the `int` check.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _firmware_from_block(block: Any) -> str | None:
    """Pull a running-firmware version out of one of Kohler's nested blocks.

    Handles a flat mapping of version keys and an Azure IoT device twin whose content sits
    under ``reported``. A string block is the version itself (a bare
    ``otaReportedProperties: "00.74"``).
    """
    if isinstance(block, str):
        return _firmware_string(block)
    if not isinstance(block, dict):
        return None
    # Azure IoT device twins nest the live values one level down.
    for nested in ("reported", "properties"):
        inner = block.get(nested)
        if isinstance(inner, dict):
            found = _firmware_from_block(inner)
            if found is not None:
                return found
    for key in _FIRMWARE_CURRENT_KEYS:
        found = _firmware_string(block.get(key))
        if found is not None:
            return found
    return None


def entry_reload_signature(entry: ConfigEntry) -> tuple[Any, ...]:
    """Fingerprint the parts of a config entry that are worth a reload.

    Shared by the coordinator, which takes one at setup, and `_async_update_listener` in
    `__init__.py`, which takes one per update and compares; both must apply the same
    exclusions, hence one function.
    """
    return reload_signature(
        entry.data,
        entry.options,
        ignore_data=RELOAD_IGNORED_DATA_KEYS,
        ignore_options=RELOAD_IGNORED_OPTION_KEYS,
    )


def _command_half(value: str, field: str) -> str:
    """Validate a user-supplied valve word, accepting either length the system shows.

    Only 8 characters (a command word) or 16 (as `sensor.anthem_valve_zone_N_hex` shows it)
    are accepted. `normalize_word` alone would truncate anything longer to 8, turning a
    typo into a different, valid command. Raises `HomeAssistantError` on a bad length,
    a malformed word, or a temperature above `TEMPERATURE_MAX_TENTHS`.
    """
    text = str(value or "").strip()
    if len(text) not in (8, 16):
        raise HomeAssistantError(
            f"{field}: expected 8 characters (a command word) or 16 (as shown by the "
            f"Zone Hex sensor), got {len(text)}: {value!r}"
        )
    try:
        word = normalize_word(text)
    except ValveHexError as err:
        raise HomeAssistantError(f"{field}: {err}") from err

    # ⚠️ This path bypasses `encode_word`'s clamp, and the 10-bit field can encode
    # 102.3 °C, so the ceiling is enforced here. Only the temperature is checked: masks,
    # flow and flags are what this escape hatch is for, and none of them can scald.
    ceiling = TEMPERATURE_MAX_TENTHS / TEMPERATURE_TENTHS_PER_DEGREE
    try:
        commanded = decode_word(word).temperature_celsius
    except ValveHexError as err:  # pragma: no cover - normalize_word already validated
        raise HomeAssistantError(f"{field}: {err}") from err
    if commanded > ceiling:
        raise HomeAssistantError(
            f"{field}: that word commands {commanded:.1f} °C, above the "
            f"{ceiling:.1f} °C the valve is written to anywhere else. Refused — check the "
            f"temperature bytes."
        )
    return word


def _describe_word(word: str) -> str:
    """Plain-language reading of a command word, for logs and service responses.

    Tolerant: it annotates an already-validated word, so a decode failure must not block
    the write.
    """
    try:
        decoded = decode_word(word)
    except ValveHexError:
        return "undecodable"
    if word == UNUSED_VALVE_WORD:
        return "unused / closed"
    open_outlets = [
        str(index + 1) for index in range(3) if decoded.outlet_mask >> index & 1
    ]
    return (
        f"{decoded.temperature_celsius:.1f}C, {decoded.flow_percent:.0f}% flow, "
        f"outlets {','.join(open_outlets) or 'none'}"
        f"{', paused' if decoded.paused else ''}"
    )


def _controller_offline(controller: Controller) -> str:
    """The message for a controller that answered `statusCode 900`, naming it."""
    return (
        f"{controller.name} is offline. Check that the controller is powered on and "
        "connected, then try again."
    )


def _experience_title(item: dict[str, Any]) -> str:
    """An experience's title — the string its control body names it by."""
    return str(item.get("title") or item.get("name") or "").strip()


def _experiences_by_category(payload: Any) -> dict[str, list[dict[str, Any]]]:
    """`hub-experience/{id}/experiences` -> `{category: [experience, ...]}`.

    Accepts the payload with or without its `experiences` wrapper. Only the three categories
    an endpoint exists for are kept, and entries without a title are dropped: the title is
    what the control body carries.
    """
    if not isinstance(payload, dict):
        return {}
    source = payload.get("experiences")
    source = source if isinstance(source, dict) else payload
    result: dict[str, list[dict[str, Any]]] = {}
    for category in ("showerExperiences", "steamExperiences", "iceShowerExperiences"):
        items = source.get(category)
        kept = [
            item
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict) and _experience_title(item)
        ]
        if kept:
            result[category] = kept
    return result


class Controller:
    """One Anthem Plus system controller, with everything the coordinator keeps for it.

    An account can carry several controllers (one per bathroom is ordinary), so the
    coordinator holds a list of these. Every controller entity is bound to exactly one, and
    each MQTT envelope is routed to the one whose id it carries.
    """

    def __init__(
        self, device: Device, hub: HubDevice, state: HubState, name: str
    ) -> None:
        self.device = device
        #: Command surface — favorites, `valvecontrol`, `stopall`.
        self.hub = hub
        #: Live state, fed by the REST seed and then by MQTT.
        self.state = state
        #: Device name shown in Home Assistant; see `controller_names`.
        self.name = name
        #: Which accessories are attached, latched by the first configuration read that
        #: answers (`known` says whether it has).
        self.capabilities = HubCapabilities()
        #: Settings from `hub-configuration` (Max Shower Duration, steam defaults, light
        #: groups, LAN address, dropped accessories). Re-read on every seed.
        self.settings = HubSettings()
        #: The firmware's fixed experience catalogue by category, read once.
        self.experiences: dict[str, list[dict[str, Any]]] = {}
        #: `hub-diagnostics/{id}/active` `errorDetails[]`, refreshed on each seed and twice
        #: a day; a failed read keeps what was known.
        self.active_errors: list[dict[str, Any]] = []
        #: The firmware read for this controller (`firmware/hub`), refreshed twice a day.
        self.firmware_info: dict[str, Any] = {}
        #: Applied messages that carry part of `hub-state`, per code (`_HUB_PUSH_FIELDS`);
        #: compared across a read as `Valve._push_serials` is.
        self.push_serials: Counter[str] = Counter()
        #: The stream connection the last successful `hub-state` read was sent under; see
        #: `Valve.state_read_on`.
        self.state_read_on: int | None = None

    @property
    def device_id(self) -> str:
        return self.device.device_id

    @property
    def favorites(self) -> list[dict[str, Any]]:
        """This controller's favorites. Ids are reassigned on delete; resolve by name.

        Seeded over REST, then replaced wholesale by every `FAVORITES_SNAPSHOT`. Kept only
        on the state object, so there is one copy to keep current.
        """
        return self.state.favorites

    @favorites.setter
    def favorites(self, favorites: list[dict[str, Any]]) -> None:
        self.state.favorites = favorites

    @property
    def model(self) -> ValveModel:
        """Outlet layout of the valve behind this controller.

        Starts as the entry's model and is replaced by what the controller's own
        `hub-configuration` reports on the first seed, since two controllers can front
        different valve models. Lives on the state object, which decodes the outlet arrays
        with it. See `KohlerKonnectCoordinator._apply_controller_topology`.
        """
        return self.state.model

    @property
    def water_is_running(self) -> bool | None:
        """Whether the controller believes water is running. Never asks the valve.

        Deliberately the controller's own view: the Anthem Plus entities answer "what is
        this controller doing", and for a session it was not told about, its ``stopall``
        and ``valvecontrol OFF`` have nothing to stop and its timers are not counting. The
        physical water state belongs to the Anthem Valve entities.

        Read from the outlet arrays rather than ``HubState.is_running``'s zone ``status``,
        so the Shower switch is on exactly when a ``ControllerOutletSensor`` is. ``None``
        (unknown) until the controller has reported a zone, since an empty ``zones`` map
        pads to all-False.
        """
        state = self.state
        if not state.zones:
            return None
        return any(state.outlets)

    def __repr__(self) -> str:
        return f"<Controller {self.device_id} {self.name!r}>"


def controller_names(devices: list[Device]) -> dict[str, str]:
    """Home Assistant device name per controller, keyed by device id. See `_device_names`."""
    return _device_names(devices, DEVICE_NAME_CONTROLLER)


def valve_names(devices: list[Device]) -> dict[str, str]:
    """Home Assistant device name per valve, keyed by device id. See `_device_names`."""
    return _device_names(devices, DEVICE_NAME_VALVE)


def product_names(devices: list[Device], fallback: str) -> dict[str, str]:
    """Home Assistant device name per device of one product, keyed by device id.

    Unlike the showers', the Konnect name alone (usually the room), as the separate faucet
    integration named faucets, so their entity ids carry over. A device with no name, or
    one sharing it, is numbered instead, from ``fallback`` when it has none.
    """
    labels, ordinals = _konnect_labels(devices)
    seen = Counter(labels.values())
    names: dict[str, str] = {}
    for device_id, label in labels.items():
        if label and seen[label] == 1:
            names[device_id] = label
        elif len(devices) == 1:
            names[device_id] = label or fallback
        else:
            names[device_id] = f"{label or fallback} {ordinals[device_id]}"
    return names


def _device_names(devices: list[Device], base: str) -> dict[str, str]:
    """Home Assistant device name per device of one kind, keyed by device id.

    A lone device keeps the plain ``base`` name, which existing entity ids and the docs
    use. With several, each takes ``base`` plus its Konnect name (usually the bathroom) so
    entity ids cannot collide; a shared or missing name falls back to the device's position
    in the cloud's list.

    Names only decide entity ids at first registration; an existing registry row or a
    rename in Home Assistant is not affected.
    """
    if len(devices) <= 1:
        return {device.device_id: base for device in devices}
    labels, ordinals = _konnect_labels(devices)
    seen = Counter(labels.values())
    return {
        device_id: (
            f"{base} {label}"
            if label and seen[label] == 1
            else f"{base} {ordinals[device_id]}"
        )
        for device_id, label in labels.items()
    }


def _konnect_labels(devices: list[Device]) -> tuple[dict[str, str], dict[str, int]]:
    """Each device's Konnect name ("" for none), and its 1-based place in the list.

    Never the device id: `Device.name` falls back to it, and a device name reaches entity
    ids, the dashboard and logs, so a name equal to the id counts as none.
    """
    labels = {
        device.device_id: (
            device.name.strip()
            if device.name and device.name.strip() != device.device_id
            else ""
        )
        for device in devices
    }
    ordinals = {device.device_id: index + 1 for index, device in enumerate(devices)}
    return labels, ordinals


def _setting_label(
    maximum_run_time: int | None,
    maximum_temperature_tenths: int | None,
    default_temperature_tenths: int | None,
) -> str:
    """Name the setting a write changed, for a message someone has to read."""
    if maximum_run_time is not None:
        return f"Max Shower Duration ({maximum_run_time // 60} minutes)"
    if maximum_temperature_tenths is not None:
        return f"Max Temperature ({maximum_temperature_tenths / 10:.1f} °C)"
    if default_temperature_tenths is not None:
        return f"Default Temperature ({default_temperature_tenths / 10:.1f} °C)"
    return "an outlet setting"


def _field_label(field: str, value: int | None) -> str:
    """`_setting_label` for one `OutletLimits` field at ``value``; its bare name if None."""
    if value is None:
        return {
            "maximum_run_time": "Max Shower Duration",
            "maximum_temperature_tenths": "Max Temperature",
            "default_temperature_tenths": "Default Temperature",
        }.get(field, "an outlet setting")
    values: dict[str, int | None] = {
        "maximum_run_time": None,
        "maximum_temperature_tenths": None,
        "default_temperature_tenths": None,
    }
    values[field] = value
    return _setting_label(**values)


class Valve:
    """One Anthem digital valve, with everything the coordinator keeps for it.

    The counterpart of :class:`Controller`: state, warm-up auto-restore, the cloud
    reachability watch and the custom-shower watcher are all per valve, and the coordinator
    holds a list. `hass`, `client`, `entry` and similar delegate to the coordinator.

    Settings are per valve: the Warmup Auto-Restore switch and the remembered warm-up mode
    sit under `CONF_VALVES` in the entry's options, keyed by device id (see `option`).
    """

    def __init__(
        self,
        coordinator: KohlerKonnectCoordinator,
        device: Device,
        model: ValveModel,
        name: str,
        tag: str | None,
    ) -> None:
        self.coordinator = coordinator
        self.gcs_device = device
        #: Device name shown in Home Assistant; see `valve_names`.
        self.name = name
        #: The valve's device-registry id, set when setup registers it as the parent its
        #: zone devices link to; None otherwise.
        self.registry_id: str | None = None
        #: Stamped onto every journal record when the account has several valves, so the
        #: shared warmup journal stays attributable; None on a single-valve account.
        self.tag = tag
        self.gcs = GcsDevice(
            coordinator.client, device.device_id, coordinator.temperature_unit, model
        )
        self.gcs_state = GcsState(model, coordinator.temperature_unit)
        # One per valve: it reports this valve's reachability. See `cloud_watch.py`.
        self.cloud_watch = CloudConnectionWatch(coordinator, self)
        #: Writes, watches and restores the warm-up mode. It records what it wrote, so a
        #: change we caused is not taken for the device misbehaving. See `warmup_manager`.
        self.warmup = WarmupManager(self)
        # The "No pausing warm-up" watcher (one at a time), and a serial every command sent
        # from here bumps so it can tell something else was sent after its own write. See
        # `konnect/warmup_resume.py`.
        self._custom_shower_task: asyncio.Future[Any] | None = None
        # Every task this valve starts, so `stop()` can cancel it: a warm-up restore sleeps
        # a minute and then writes, which must not happen after an unload.
        self._background_tasks: set[asyncio.Task] = set()
        self._local_write_serial = 0
        # Valve writes run one at a time, each built on the last: every write re-sends both
        # zones rebuilt from a baseline, so two built from the same baseline undo each
        # other. `_sent_words` is what the last write sent: the next write's baseline for
        # `_sent_hold_seconds` (narrowed by any report since), and the shower a warm-up is
        # warming up; see `_valve_baseline`. `_sent_over` is the reported pair when the
        # write was asked for, read before its POST.
        self._valve_write_lock = asyncio.Lock()
        self._sent_words: tuple[Any, Any] | None = None
        self._sent_over: tuple[Any, Any] = (None, None)
        self._sent_at = 0.0
        # The echo lands about 1-2 s after the POST returns; this covers it with margin
        # without holding off a change made at the wall for long. A report inside it
        # narrows the words rather than ending them.
        self._sent_hold_seconds = 5.0
        # Bumped by every command that decides the valve's words (`_note_local_write`): a
        # word write, a preset, a restart, a controller's command. Lets a word write tell
        # that one landed while its own POST was in flight. `_local_write_serial` also
        # counts outlet settings.
        self._words_serial = 0
        # Bumped the moment a Stop is asked for, before it waits for the lock: every
        # command asked for before it steps aside when its turn comes. See
        # `async_stop_shower`.
        self._stop_generation = 0
        # Outlet-setting writes run one at a time; the serial lets a delayed read-back tell
        # another landed since. See `async_write_outlet_setting`.
        self._outlet_write_lock = asyncio.Lock()
        self._outlet_write_serial = 0
        # Touch-panel settings writes run one at a time, each from its own fresh read.
        self._ui_config_lock = asyncio.Lock()
        # Outlet settings written from here (`OutletLimits` field -> value) until a read
        # shows them on every outlet; see `_adopt_outlet_read`.
        self._outlet_unconfirmed: dict[str, int] = {}
        # Those a read past the catch-up window still contradicts, with the value it
        # showed; see `_settle_outlet_fields`.
        self._outlet_disputed: dict[str, int | None] = {}
        # What the valve last reported for each outlet (announced or read), and when it
        # last announced it (monotonic); see `_reported_outlets`.
        self._outlet_reported: dict[int, Any] = {}
        self._outlet_announced_at: dict[int, float] = {}
        # Fields of each outlet's latest announcement taken for a late echo, which a read
        # still decides; see `_note_outlet_announcement`.
        self._outlet_guessed: dict[int, frozenset[str]] = {}
        # Until when (monotonic) the outlet records cannot be vouched for after a stream
        # gap; see `note_stream_gap`.
        self._outlets_unsure_until: float | None = None
        # Per field, the values a write from here replaced within the catch-up window: an
        # announcement showing one is a late echo; see `_note_outlet_announcement`.
        self._outlet_superseded: dict[str, set[int | None]] = {}
        # When the latest outlet write began (monotonic); see `_outlet_limits_settled`.
        self._outlet_written_at: float | None = None
        # How long each zone has been running, for the time-left attributes; the valve
        # reports nothing about its own clock. See `konnect/zone_clock.py`.
        self._zone_clock = ZoneClock()
        # Whether the valve's own outlet split has been read yet — see `async_seed`.
        self._topology_checked = False
        # Whether its settings and its presets have answered since setup; see
        # `seed_incomplete`.
        self._settings_read = self._presets_read = False
        # Per field, how many applied messages carried part of `gcs-state`
        # (`GcsState.carried`). Compared across a read; see `apply_rest_reply`. The warm-up
        # manager's own read of the mode compares "warmup_mode".
        self._push_serials: Counter[str] = Counter()
        # When each of those fields was last reported (monotonic, as the message arrived):
        # a read sent within `_GCS_STATE_TRAILS_SECONDS` of it can answer from before it.
        self._reported_at: dict[str, float] = {}
        #: The `KohlerKonnectCoordinator.stream_connects` count the last successful
        #: `gcs-state` read was sent under (0 before the first connect, None until a read
        #: succeeds); see `KohlerKonnectCoordinator._handle_connected`.
        self.state_read_on: int | None = None
        #: Active faults from the `gcs-diagnostics` log, for the Problem sensor; see
        #: `async_refresh_faults`.
        self.active_faults: list[dict[str, Any]] = []
        # The `gcs-configuration` record, read once at the first seed. None: not read yet;
        # `{}`: read, nothing usable (normal on a controller-attached valve).
        self.configuration: dict[str, Any] | None = None
        #: `gcs-configuration/{id}/about` — serials, models and firmware per part. Read
        #: beside `configuration`, and again on each seed until a read answers; {} until then.
        self.about_parts: dict[str, Any] = {}
        #: Firmware reads keyed `gcs` (the valve) and `gateway`, refreshed twice a day;
        #: see `KohlerKonnectCoordinator.async_refresh_firmware`.
        self.firmware_info: dict[str, dict[str, Any]] = {}
        #: The monthly `gcs-usage` series ({} until a read succeeds), retried until it
        #: does and re-read when the month rolls over.
        self.usage: dict[str, Any] = {}
        self._usage_seeded_month: str | None = None
        # The per-day series, refreshed when a shower ends — see `async_refresh_daily_usage`.
        self.usage_daily: dict[str, Any] = {}
        # One pending read per shower end; see `_note_running_for_usage`.
        self._daily_usage_task: asyncio.Task | None = None
        self._was_running = False
        # The pending read-back per outlet setting; a newer write of the same setting
        # replaces it. See `async_write_outlet_setting`.
        self._verify_tasks: dict[tuple[str, ...], asyncio.Task] = {}
        # The flow each zone's Flow number shows, as a percent of that zone's own ceiling
        # (restated for the wire only when a word is built, `_wire_flow`). Recorded by every
        # write that names a flow and used by every write that does not, so an outlet toggle
        # or temperature change keeps the user's flow; see `async_apply_valve`.
        self.zone_flow: dict[int, float] = {
            zone: DEFAULT_FLOW_PERCENT for zone in self.model.zones
        }

    def __repr__(self) -> str:
        return f"<Valve {self.device_id} {self.name!r}>"

    @property
    def created_time(self) -> str | None:
        """When Kohler's cloud first created this device's record, as it reports it.

        The closest thing to an install date the API offers, but it is the cloud record's
        creation: a re-registered valve reads as newer. Returned raw;
        `sensor.kohler_timestamp` parses it.
        """
        value = (self.configuration or {}).get("createdTime")
        return None if value in (None, "") else str(value)

    @property
    def firmware(self) -> str | None:
        """The interface (touchscreen) firmware, as the device registry shows it.

        The valve and gateway have their own versions; see :meth:`component_firmware`.
        First hit wins:

        1. ``configuration.about.uI2.firmware``, where the record nests it.
        2. A top-level ``about.firmware`` string.
        3. ``otaReportedProperties`` / ``firmwareUpdate``, Application blocks first. A block
           declaring another ``firmwareType`` (Assets, the artwork bundle) is never read.
        4. A bare top-level ``version`` string.

        None when nothing matches: a blank beats a number that means something else.
        """
        about = self.about
        value = _firmware_string((about.get("uI2") or {}).get("firmware"))
        if value is not None:
            return value

        # A top-level `about.firmware` counts only as a string: as a mapping it is the
        # gateway's version (`about.firmware.version` equals `about.gateway.firmware`),
        # which `component_firmware("gateway")` reports.
        configuration = self.configuration or {}
        top_about = configuration.get("about")
        if isinstance(top_about, dict):
            value = _firmware_string(top_about.get("firmware"))
            if value is not None:
                return value

        blocks = [
            configuration.get(key)
            for key in ("otaReportedProperties", "firmwareUpdate")
        ]
        # Application first, wherever it appears.
        for block in blocks:
            if (
                isinstance(block, dict)
                and block.get("firmwareType") == _FIRMWARE_PREFERRED_TYPE
            ):
                value = _firmware_from_block(block)
                if value is not None:
                    return value

        # Then any block that does not declare another type: some accounts'
        # `otaReportedProperties` carry no `firmwareType` at all.
        for block in blocks:
            declared = block.get("firmwareType") if isinstance(block, dict) else None
            if declared is not None and declared != _FIRMWARE_PREFERRED_TYPE:
                continue
            value = _firmware_from_block(block)
            if value is not None:
                return value

        return _firmware_string(configuration.get("version"))

    @property
    def about(self) -> dict[str, Any]:
        """The record's ``about`` block, wherever this account nests it.

        Nested under the record's own ``configuration`` key on some accounts and at the top
        level on others (`docs/protocol/gcs_valve.md` §2). Nested is checked first: it
        carries the per-component breakdown, where a top-level one may hold only
        ``firmware``.
        """
        configuration = self.configuration or {}
        inner = configuration.get("configuration")
        if isinstance(inner, dict):
            about = inner.get("about")
            if isinstance(about, dict):
                return about
        about = configuration.get("about")
        return about if isinstance(about, dict) else {}

    def component_firmware(self, component: str) -> str | None:
        """The firmware of one named part of the system, or None.

        ``component`` is a key of the ``about`` block: ``uI2`` (interface),
        ``primaryValve``, ``secondaryValve1``, ``gateway``. Each holds ``firmware`` and
        sometimes ``assetsFirmware`` and ``bleVersion``; only ``firmware`` is read. The
        parts run different versions, and two valves on one account can differ while the
        app shows one (`docs/protocol/gcs_valve.md` §9).
        """
        block = self.about.get(component)
        if not isinstance(block, dict):
            return None
        return _firmware_string(block.get("firmware"))

    # ------------------------------------------------------------------ #
    # Delegates to the coordinator
    # ------------------------------------------------------------------ #
    @property
    def hass(self) -> HomeAssistant:
        return self.coordinator.hass

    @property
    def client(self) -> KohlerClient:
        return self.coordinator.client

    @property
    def entry(self) -> ConfigEntry:
        return self.coordinator.entry

    @property
    def temperature_unit(self) -> str:
        return self.coordinator.temperature_unit

    @property
    def zone_grouping(self) -> str:
        return self.coordinator.zone_grouping

    @property
    def warmup_log(self) -> DebugJournal | None:
        return self.coordinator.warmup_log

    @property
    def device_id(self) -> str:
        return self.gcs_device.device_id

    @property
    def model(self) -> ValveModel:
        """This valve's outlet layout.

        Starts as the entry's model and is replaced by what the valve's own
        `gcsadvancestate` reports on the first seed, since two valves on one account can
        differ. Lives on the state object, which decodes every word with it; `GcsDevice`
        keeps a copy for encoding, and `_apply_topology` moves both together.
        """
        return self.gcs_state.model

    def _outlet_label(self, outlet_id: int) -> str:
        """A hardware `outLetId` as the outlet number a message should show.

        An id this model has no outlet for is shown as itself rather than as `id + 1`: on a
        K-28211 the unused slot 2 would otherwise read as outlet 3, a real outlet.
        """
        outlet = self.model.outlet_from_id(outlet_id)
        return f"id {outlet_id}" if outlet is None else str(outlet)

    def _tagged(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Journal fields, stamped with this valve when the account has several."""
        return fields if self.tag is None else {"valve": self.tag, **fields}

    # ------------------------------------------------------------------ #
    # Per-valve settings on the config entry
    # ------------------------------------------------------------------ #
    def option(self, key: str, default: Any = None) -> Any:
        """A per-valve value from `entry.options[CONF_VALVES][device_id]`."""
        return (
            (self.entry.options.get(CONF_VALVES) or {}).get(self.device_id) or {}
        ).get(key, default)

    def set_option(self, key: str, value: Any) -> None:
        """Write a per-valve option. The switches call this; nothing reloads on it."""
        valves = dict(self.entry.options.get(CONF_VALVES) or {})
        valves[self.device_id] = {**(valves.get(self.device_id) or {}), key: value}
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, CONF_VALVES: valves}
        )

    # ------------------------------------------------------------------ #
    # Lifecycle, driven by the coordinator
    # ------------------------------------------------------------------ #
    def _note_local_write(self) -> None:
        """Count a command sent from here that decides this valve's words.

        The custom-shower watcher reads `_local_write_serial`: if it moved since the
        watcher's own write, something else was sent and it must not resume on top of it.
        Controller commands bump every valve's serial through the coordinator, since which
        valve a controller fronts is not knowable from the cloud.

        Also bumps `_words_serial` and drops the last-sent words (`_valve_baseline`): a
        preset, favorite or restart decides the words next, so the last ones written no
        longer describe them. A word write re-arms them once its own POST lands, or puts
        back its baseline if the POST fails (`_async_send_words`). Outlet settings change
        no word and must not come through here.
        """
        self._local_write_serial += 1
        self._words_serial += 1
        self._sent_words = None

    def handle_envelope(self, envelope: Envelope) -> bool:
        """Apply one of this valve's MQTT messages. True if anything changed."""
        state = self.gcs_state
        was_warmup = state.warmup_mode
        outlets = dict(state.outlet_limits)
        changed = state.apply_envelope(envelope)
        # Stamped with when it arrived, not when it is handled: a message held until the
        # devices were known is handled seconds late and would otherwise outrank a newer
        # read.
        arrived = envelope.received_monotonic
        if envelope.code == MSG_GCS_OUTLET_CONFIG:
            self._note_outlet_announcement(outlets, at=arrived)
        for field in state.carried:
            self._push_serials[field] += 1
            self._reported_at[field] = max(
                arrived, self._reported_at.get(field, arrived)
            )
        self._handle_warmup_mode_change(
            was_warmup,
            self.gcs_state.warmup_mode,
            announced=envelope.code == MSG_GCS_WARMUP_STATUS,
        )
        self._update_zone_clock()
        self._note_running_for_usage()
        # A valve message is proof of reachability and settles any pending contradiction
        # check.
        self.cloud_watch.note_gcs_message()
        return changed

    def _note_running_for_usage(self) -> None:
        """Re-read the daily usage when a shower ends.

        Usage moves only while water runs and nothing polls, so the running-to-stopped
        edge is when to read it. The valve sends several messages as a shower winds down,
        so a second edge is a no-op while a read is pending; water starting again cancels
        the pending read so it waits for the session to end.
        """
        running = self.gcs_state.is_running
        was_running, self._was_running = self._was_running, running
        if running:
            if self._daily_usage_task is not None and not self._daily_usage_task.done():
                self._daily_usage_task.cancel()
            self._daily_usage_task = None
            return
        if not was_running:
            return
        if self._daily_usage_task is not None and not self._daily_usage_task.done():
            return
        self._daily_usage_task = self._track(self._async_refresh_daily_usage_soon())

    async def _async_refresh_daily_usage_soon(self) -> None:
        """Wait for Kohler to record the session, then re-read and re-render.

        The cloud aggregates a session some time after the valve reports it closed, so an
        immediate read misses it. If the read after `USAGE_REFRESH_DELAY_SECONDS` shows no
        increase on any day, one follow-up runs after `USAGE_RETRY_DELAY_SECONDS`; none
        when there was no baseline to compare with.
        """
        had_baseline = bool(usage_series(self.usage_daily))
        baseline = _daily_usage_volumes(self.usage_daily)

        if not await self._async_read_daily_usage_after(USAGE_REFRESH_DELAY_SECONDS):
            return
        if not had_baseline:
            return
        fresh = _daily_usage_volumes(self.usage_daily)
        if any(vol > baseline.get(day, 0.0) for day, vol in fresh.items()):
            return
        await self._async_read_daily_usage_after(USAGE_RETRY_DELAY_SECONDS)

    async def _async_read_daily_usage_after(self, delay: float) -> bool:
        """Wait, then re-read the daily series unless water is running again.

        True when the read happened. Entities read `usage_daily` directly, so a successful
        read re-renders them.
        """
        await asyncio.sleep(delay)
        if self.gcs_state.is_running:
            return False
        try:
            await self.async_refresh_daily_usage()
        except (AuthError, KohlerError) as err:
            _LOGGER.debug("Could not refresh daily usage: %s", err)
            if credential_is_dead(err):
                self.coordinator._handle_auth_error(err)
            return False
        self.coordinator.async_refresh_entities()
        return True

    def forget_timings(self) -> None:
        """Drop the zone clocks across a stream gap. See `_handle_connected`."""
        self._zone_clock.forget()

    def _track(self, coro) -> asyncio.Future[Any]:
        """Start a background task and keep a reference until it finishes.

        The reference keeps it from being garbage-collected mid-await and lets `stop()`
        cancel it. Once the coordinator is closing nothing starts (paho can still deliver
        while the stream stops): the coroutine is closed unstarted and an already-cancelled
        future is returned.
        """
        if self.coordinator.closed:
            coro.close()
            future: asyncio.Future[Any] = self.hass.loop.create_future()
            future.cancel()
            return future
        task = self.hass.async_create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def stop(self) -> None:
        """Cancel everything that could fire into a torn-down coordinator."""
        self._cancel_custom_shower("the integration is shutting down")
        # The warm-up restore and the journal write sleep before acting and can outlive an
        # unload; the restore ends in a valve write and an entry update, so cancel them.
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        self.warmup.reset_restore_task()
        # After the stream has stopped (see `async_shutdown_stream`), so this also cancels a
        # check that a timer started while the stream was winding down.
        self.cloud_watch.async_stop()

    @property
    def topology_confirmed(self) -> bool:
        """Whether this valve's own settings have stated its outlet split since setup.

        False means the layout in use is a fallback — the entry's, or one remembered from
        earlier this run — which is not a basis for deleting zone devices; see
        `__init__._async_cleanup_zone_subdevices`.
        """
        return self._topology_checked

    @callback
    def _apply_topology(self, settings: dict[str, Any]) -> bool:
        """Give this valve the outlet layout its own settings report.

        Returns whether the read stated a layout; the caller latches only then, so a read
        without one is retried on the next reconnect. The entry's model came from the
        config flow, which asked only the first valve; a read that yields nothing leaves it
        in place. Decoding switches at once; entities already built are handled by
        `KohlerKonnectCoordinator._async_note_topology`.
        """
        detected = topology_from_valve_settings(settings)
        if not detected:
            return False
        model = model_for_topology(*detected)
        current = self.model
        if (model.outlets_valve1, model.outlets_valve2) == (
            current.outlets_valve1,
            current.outlets_valve2,
        ):
            # Answered and agreed: settled.
            return True
        # The valve's name, not its id: INFO lines get pasted into issues, and a Kohler
        # device id is a cloud address.
        _LOGGER.info(
            "%s reports %s; using that for this valve instead of the entry's %s",
            self.name,
            describe_topology(detected),
            current.sku,
        )
        self.gcs_state.model = model
        self.gcs.model = model
        if not model.uses_valve2:
            self.gcs_state.valve2 = None
        return True

    async def async_write_outlet_setting(
        self,
        *,
        maximum_run_time: int | None = None,
        maximum_temperature_tenths: int | None = None,
        default_temperature_tenths: int | None = None,
    ) -> None:
        """Write one outlet setting to every outlet, then verify it in the background.

        `writeoutletconfig` replaces one outlet's whole record per call, so this sends one
        call per outlet, each after the previous succeeds, under `_outlet_write_lock`.
        ⚠️ Unchanged fields are re-sent from `outlet_limits`, so that cache must be
        current: each cached record takes the written fields once its POST is accepted and
        keeps them against lagging reads (`_adopt_outlet_read`).

        Every refusal (default above maximum, a disputed field, whatever
        `validate_outlet_record` refuses) is checked on every outlet before anything is
        sent. A 201 means accepted, not applied, so a background read-back follows; a failed
        check raises a repair issue.

        Raises `HomeAssistantError` if refused, or if the chain fails part-way, naming the
        outlets that took the new value. A partial write is not retried: re-sending a
        half-applied safety setting without knowing why it failed can spread the fault.
        """
        changes = {
            name: value
            for name, value in (
                ("maximum_run_time", maximum_run_time),
                ("maximum_temperature_tenths", maximum_temperature_tenths),
                ("default_temperature_tenths", default_temperature_tenths),
            )
            if value is not None
        }
        async with self._outlet_write_lock:
            limits = self.gcs_state.outlet_limits
            if not limits:
                raise HomeAssistantError(
                    f"{self.name} has not reported its outlet configuration yet, so "
                    "there is nothing to write back. Try again once it has."
                )
            # ⚠️ Every other field is re-sent from the cache, and one written from here that
            # no read has confirmed may since have been changed in the app (a lowered scald
            # limit, say). One still unconfirmed past the catch-up window is read again
            # first; if the cloud still disagrees, the write is refused until the owner sets
            # that field again.
            await self._async_confirm_outlet_fields(changes)
            others = [field for field in self._outlet_disputed if field not in changes]
            if others and (
                not any(field in self._outlet_disputed for field in changes)
                or any(o not in self._outlet_reported for o in limits)
            ):
                field = others[0]
                raise HomeAssistantError(
                    f"Nothing was written to {self.name}: "
                    f"{_field_label(field, self._outlet_unconfirmed.get(field))} was set "
                    "from Home Assistant, but the valve still reports "
                    f"{_field_label(field, self._outlet_disputed[field])}, and every "
                    "outlet setting sends the others with it. Set "
                    f"{_field_label(field, None)} again first, then this one."
                )
            # When a disputed field is being set again, any other disputed field goes as the
            # valve reports it; refusing instead would leave two disputes each waiting on
            # the other.
            for field in others:
                self._drop_outlet_field(field)
            for outlet_id in sorted(limits):
                record = limits[outlet_id]
                maximum = changes.get(
                    "maximum_temperature_tenths", record.maximum_temperature_tenths
                )
                default = changes.get(
                    "default_temperature_tenths", record.default_temperature_tenths
                )
                if maximum is not None and default is not None and default > maximum:
                    raise HomeAssistantError(
                        f"Nothing was written to {self.name}: outlet "
                        f"{self._outlet_label(outlet_id)} would have a Default "
                        f"Temperature of {default / 10:.1f} °C above its Max Temperature "
                        f"of {maximum / 10:.1f} °C, which the valve refuses. Lower "
                        "Default Temperature first, or raise Max Temperature."
                    )
            # Every other refusal, on every outlet, before the first is written, so a
            # refusal never leaves a partial write.
            for outlet_id in sorted(limits):
                try:
                    validate_outlet_record(
                        limits[outlet_id],
                        maximum_run_time=maximum_run_time,
                        maximum_temperature_tenths=maximum_temperature_tenths,
                        default_temperature_tenths=default_temperature_tenths,
                    )
                except KohlerError as err:
                    raise HomeAssistantError(
                        f"Nothing was written to {self.name}: outlet "
                        f"{self._outlet_label(outlet_id)}'s record would be refused, so "
                        f"no outlet was changed. ({err})"
                    ) from err
            # The watcher's count only; see `_note_local_write`.
            self._local_write_serial += 1
            self._outlet_write_serial += 1
            now = time.monotonic()
            if (
                self._outlet_written_at is None
                or now - self._outlet_written_at >= OUTLET_WRITE_VERIFY_DELAY_SECONDS
            ):
                self._outlet_superseded.clear()  # nothing older can still be on its way
            for field in changes:
                replaced = self._outlet_superseded.setdefault(field, set())
                replaced.update(getattr(record, field) for record in limits.values())
                # And what the valve reports, which for a setting not yet confirmed (the
                # re-set a dispute asks for) is not what the cache holds.
                replaced.update(
                    getattr(record, field) for record in self._outlet_reported.values()
                )
            self._outlet_written_at = now
            serial = self._outlet_write_serial

            written: list[int] = []
            # A sign-in failure goes through the shared handling; any other failure says
            # how far the write got.
            with self.coordinator.command_errors(VALVE_OFFLINE):
                try:
                    for outlet_id in sorted(limits):
                        await self.gcs.async_write_outlet_config(
                            limits[outlet_id],
                            maximum_run_time=maximum_run_time,
                            maximum_temperature_tenths=maximum_temperature_tenths,
                            default_temperature_tenths=default_temperature_tenths,
                        )
                        written.append(outlet_id)
                        # What the valve was just told, so the next write echoes it rather
                        # than the value it replaced. Read again after the await: an
                        # announcement can have replaced the record meanwhile.
                        limits[outlet_id] = dataclasses.replace(
                            limits[outlet_id], **changes
                        )
                except KohlerError as err:
                    if not written:
                        raise HomeAssistantError(
                            f"Writing {self.name} failed at the first outlet, so no "
                            f"outlet confirmed the new value. ({err})"
                        ) from err
                    # The outlets already written hold the new value.
                    done = ", ".join(self._outlet_label(o) for o in written)
                    raise HomeAssistantError(
                        f"Writing {self.name} failed after outlet {done}. Outlets are now "
                        f"in a mixed state — re-saving the setting rewrites them all. "
                        f"({err})"
                    ) from err
                finally:
                    if written:
                        # Until a read-back shows it, no read may put the old value back.
                        self._outlet_unconfirmed.update(changes)
                        # Set again, so whatever disputed it is answered.
                        for field in changes:
                            self._outlet_disputed.pop(field, None)
                        self.coordinator.async_refresh_entities()

            # Not awaited: the wait would block the service call. One check per setting:
            # only the newest write's check can be right, so it replaces any pending one.
            setting = tuple(changes)
            if (pending := self._verify_tasks.get(setting)) is not None:
                pending.cancel()
            self._verify_tasks[setting] = self._track(
                self._async_verify_outlet_write(
                    maximum_run_time=maximum_run_time,
                    maximum_temperature_tenths=maximum_temperature_tenths,
                    default_temperature_tenths=default_temperature_tenths,
                    serial=serial,
                )
            )

    async def _async_verify_outlet_write(
        self,
        *,
        maximum_run_time: int | None,
        maximum_temperature_tenths: int | None,
        default_temperature_tenths: int | None,
        serial: int,
    ) -> None:
        """Re-read the outlet configuration and confirm every outlet took the value.

        Waits `OUTLET_WRITE_VERIFY_DELAY_SECONDS` first: `gcsadvancestate` updates only once
        the device reports, up to ~25 s after the 201, so an immediate read shows the old
        value. Runs detached, so nothing here may raise; a failure becomes a repair issue,
        one per setting and cleared only by that setting's own check.

        ``serial`` is `_outlet_write_serial` as the scheduling write left it. The read
        replaces the cached records only if no outlet write has happened since, and even
        then not a value written from here that it does not show yet
        (`_adopt_outlet_read`).
        """
        setting = _setting_label(
            maximum_run_time, maximum_temperature_tenths, default_temperature_tenths
        )
        wanted = {
            field: value
            for field, value in (
                ("maximum_run_time", maximum_run_time),
                ("maximum_temperature_tenths", maximum_temperature_tenths),
                ("default_temperature_tenths", default_temperature_tenths),
            )
            if value is not None
        }
        # The same key `async_write_outlet_setting` files this check under.
        fields = tuple(wanted)
        await asyncio.sleep(OUTLET_WRITE_VERIFY_DELAY_SECONDS)
        requested_at = time.monotonic()
        try:
            settings = await self.client.async_get_gcs_settings(
                self.gcs_device.device_id
            )
        except (AuthError, KohlerError) as err:
            if credential_is_dead(err):
                self.coordinator._handle_auth_error(err)
            self._raise_write_issue(
                fields,
                setting,
                f"Reading the value back from {self.name} failed: {err}",
            )
            return

        fresh = outlet_limits_from_settings(settings)
        if not fresh:
            self._raise_write_issue(
                fields,
                setting,
                f"{self.name} reported no outlet configuration to check the change "
                "against.",
            )
            return
        fresh = self._reported_outlets(fresh, requested_at)
        if self._outlet_write_serial == serial:
            self._adopt_outlet_read(fresh)
            self._outlets_caught_up(requested_at)
            # The read is fresher than anything shown, so show it now rather than waiting
            # for the valve's own outlet-config announcement.
            self.coordinator.async_refresh_entities()
        # Whatever this read shows on every outlet is confirmed. A value it contradicts
        # stays unconfirmed rather than disputed: right at the catch-up window a lagging
        # cloud is the likely reason, and the next write of another setting reads again.
        self._settle_outlet_fields(fresh, dispute=False)

        stale = [
            self._outlet_label(outlet_id)
            for outlet_id, limit in sorted(fresh.items())
            for field, value in wanted.items()
            if getattr(limit, field) != value
        ]
        if stale:
            self._raise_write_issue(
                fields,
                setting,
                f"{self.name} is still reporting the old value on outlet(s) "
                f"{', '.join(stale)}.",
            )
            return
        # Verified: clear this setting's warning, and only this setting's.
        self._clear_write_issue(fields)

    def _adopt_outlet_read(self, fresh: dict[int, Any]) -> None:
        """Take a `gcs-settings` read into the cached outlet records, except what it undoes.

        The read replaces each outlet's record, except a field written from here that it
        does not show yet (`_outlet_unconfirmed`): the cloud trails a write by ~25 s or
        more, and the cache is what the next outlet write echoes. Such a field keeps the
        cached value (the one written, or what the valve has announced since).

        The reseed's read comes through here too, once `_outlet_limits_settled` allows it.
        Callers pass reads through `_reported_outlets` first.
        """
        cache = self.gcs_state.outlet_limits
        for outlet_id, record in fresh.items():
            cached = cache.get(outlet_id)
            keep = {
                field: getattr(cached, field)
                for field, value in self._outlet_unconfirmed.items()
                if cached is not None and getattr(record, field) != value
            }
            cache[outlet_id] = dataclasses.replace(record, **keep) if keep else record

    def _reported_outlets(
        self, fresh: dict[int, Any], requested_at: float
    ) -> dict[int, Any]:
        """A `gcs-settings` read's outlets, each as the valve last reported it.

        The read's own record, except for an outlet the valve announced within the cloud's
        catch-up window (`OUTLET_WRITE_VERIFY_DELAY_SECONDS`) before the read was sent: the
        document can still show what came before, so the announcement wins. ⚠️ Without
        this, a read could put an old scald limit back into the cache that the next write
        echoes. ``requested_at`` is when the read was sent (monotonic).
        """
        cutoff = requested_at - OUTLET_WRITE_VERIFY_DELAY_SECONDS
        view: dict[int, Any] = {}
        for outlet_id, record in fresh.items():
            reported = self._outlet_reported.get(outlet_id)
            if (
                reported is not None
                and self._outlet_announced_at.get(outlet_id, cutoff) > cutoff
            ):
                # The announcement's own word; what it was guessed to echo, the read's.
                guessed = self._outlet_guessed.get(outlet_id, frozenset())
                record = dataclasses.replace(
                    reported, **{field: getattr(record, field) for field in guessed}
                )
            view[outlet_id] = record
        self._outlet_reported.update(view)
        return view

    def _drop_outlet_field(self, field: str) -> None:
        """Give up a disputed setting written from here: each outlet takes what it reports.

        For a write of another disputed setting; see `async_write_outlet_setting`. Sent as
        the valve reports it, the field changes nothing there.
        """
        limits = self.gcs_state.outlet_limits
        for outlet_id, record in limits.items():
            limits[outlet_id] = dataclasses.replace(
                record, **{field: getattr(self._outlet_reported[outlet_id], field)}
            )
        written = self._outlet_unconfirmed.pop(field, None)
        reported = self._outlet_disputed.pop(field, None)
        self._outlet_superseded.pop(field, None)
        self._clear_write_issue((field,))
        _LOGGER.warning(
            "%s set from Home Assistant was never taken up by %s, which reports %s: "
            "dropped, so that outlet settings send it as the valve reports it",
            _field_label(field, written),
            self.name,
            _field_label(field, reported),
        )

    def _settle_outlet_fields(
        self, fresh: dict[int, Any], *, dispute: bool = True
    ) -> None:
        """Confirm, or dispute, each outlet setting written from here against a read.

        Confirmed when the read shows the value written on every outlet; from then on a
        read may replace it. Disputed when a read made after the cloud's catch-up window
        (`OUTLET_WRITE_VERIFY_DELAY_SECONDS`) still shows something else. A dispute cannot
        tell a lagging cloud from a change made in the app since, so the cache keeps the
        value written and no other outlet setting may be written while it stands
        (`async_write_outlet_setting`), since each would re-send it. Settled by a read that
        shows it, or by setting it again. A new dispute raises the setting's repair issue.

        ``fresh`` is a REST read or a valve announcement's outlets (`OutletLimits` by
        outlet id); an announcement carries one outlet, so it can dispute but never
        confirm. ``dispute`` False confirms only (the write's own read-back).
        """
        outlets = self.gcs_state.outlet_limits
        written_at = self._outlet_written_at
        settled = (
            written_at is None
            or time.monotonic() - written_at >= OUTLET_WRITE_VERIFY_DELAY_SECONDS
        )
        for field, value in list(self._outlet_unconfirmed.items()):
            shown = [getattr(fresh[o], field) for o in outlets if o in fresh]
            if len(shown) == len(outlets) and all(v == value for v in shown):
                del self._outlet_unconfirmed[field]
                self._outlet_disputed.pop(field, None)
                self._outlet_superseded.pop(field, None)
                # Applied after all, so its "may not have been applied" goes too.
                self._clear_write_issue((field,))
                continue
            other = next((v for v in shown if v != value), None)
            if (
                not dispute
                or not settled
                or other is None
                or field in self._outlet_disputed
            ):
                continue
            self._outlet_disputed[field] = other
            self._raise_write_issue(
                (field,),
                _field_label(field, value),
                f"{self.name} still reports {_field_label(field, other)} well after the "
                "change. Until it is set again, no other outlet setting can be changed "
                "from Home Assistant, because each change sends this one with it — "
                "except another that disagrees the same way, which sends this one as "
                "the valve reports it.",
            )

    async def _async_confirm_outlet_fields(self, changes: dict[str, int]) -> None:
        """Read the outlet records again before a write re-sends what may be stale.

        Reads when a field written from here, not part of this write, is still unconfirmed
        past the catch-up window (inside it, re-sending the written value is right), and
        whenever the stream cannot vouch for the records: down, so a change made in the app
        is never announced, or just back (`note_stream_gap`), when the write is refused
        until the window has passed. The read confirms or disputes each field
        (`_settle_outlet_fields`). Called with `_outlet_write_lock` held.
        """
        now = time.monotonic()
        unsure = self._outlets_unsure_until
        if unsure is not None and now < unsure:
            raise HomeAssistantError(
                f"Nothing was written to {self.name}: the connection to Kohler has just "
                "come back, and a change made in the Konnect app meanwhile may not show "
                "yet. Every outlet setting sends the others with it, so try again in "
                f"{int(unsure - now) + 1} s."
            )
        stream = self.coordinator.stream
        unannounced = unsure is not None or stream is None or not stream.connected
        written_at = self._outlet_written_at
        if not unannounced and (
            written_at is None
            or now - written_at < OUTLET_WRITE_VERIFY_DELAY_SECONDS
            or not any(field not in changes for field in self._outlet_unconfirmed)
        ):
            return
        requested_at = now
        try:
            settings = await self.client.async_get_gcs_settings(
                self.gcs_device.device_id
            )
        except (AuthError, KohlerError) as err:
            if credential_is_dead(err):
                self.coordinator._handle_auth_error(err)
            self._refuse_unchecked(
                changes, f"checking it first failed ({err})", err, always=unannounced
            )
            return
        fresh = outlet_limits_from_settings(settings)
        if not fresh:
            # An empty read checks nothing; refuse as for a failed one.
            self._refuse_unchecked(
                changes,
                "the valve reported no outlet configuration to check it against",
                always=unannounced,
            )
            return
        fresh = self._reported_outlets(fresh, requested_at)
        self._adopt_outlet_read(fresh)
        self._outlets_caught_up(requested_at)
        self._settle_outlet_fields(fresh)

    def note_stream_gap(self) -> None:
        """The stream is back after a gap, in which an announcement may have been missed.

        Nothing replays it, and the cloud's copy can take the catch-up window to show the
        change, so a read now could hand the next write an old scald limit. Outlet writes
        wait out the window, and the first after it reads the records again
        (`_async_confirm_outlet_fields`).
        """
        self._outlets_unsure_until = (
            time.monotonic() + OUTLET_WRITE_VERIFY_DELAY_SECONDS
        )

    def _outlets_caught_up(self, requested_at: float) -> None:
        """A read taken in: sent once `_outlets_unsure_until` is over, it vouches again."""
        until = self._outlets_unsure_until
        if until is not None and requested_at >= until:
            self._outlets_unsure_until = None

    def _refuse_unchecked(
        self,
        changes: dict[str, int],
        why: str,
        err: Exception | None = None,
        *,
        always: bool = False,
    ) -> None:
        """Refuse a write over a setting still unconfirmed, which could not be checked.

        Unless the write's own read-back has confirmed it meanwhile; then the write goes
        ahead. ``always`` refuses even with nothing unconfirmed, for a read that was needed
        for the records as a whole.
        """
        pending = next((f for f in self._outlet_unconfirmed if f not in changes), None)
        if pending is None and always:
            raise HomeAssistantError(
                f"Nothing was written to {self.name}: with no word from the valve "
                f"meanwhile, its outlet settings are read first, and {why}. Every outlet "
                "setting sends the others with it, so try again shortly."
            ) from err
        if pending is None:
            return
        raise HomeAssistantError(
            f"Nothing was written to {self.name}: "
            f"{_field_label(pending, self._outlet_unconfirmed[pending])} set from "
            f"Home Assistant has not been confirmed yet, and {why}. Every outlet "
            "setting sends the others with it, so try again shortly."
        ) from err

    def _note_outlet_announcement(self, before: dict[int, Any], *, at: float) -> None:
        """Hold the valve's outlet-config announcement to the same rule as a read.

        `GcsState` takes an announcement into the cache as it comes, one outlet at a time,
        but it is not always the latest word: one save's announcement can land after the
        next save was sent. Inside the catch-up window, a value written from here stands
        against such a late echo, meaning an announced value the setting held before
        (`_outlet_superseded`), not one set in the app meanwhile. Past the window, a
        disagreeing announcement disputes the field, as a read would
        (`_settle_outlet_fields`). ``before`` is the cache before the message; ``at`` is
        when it arrived (monotonic). The announcement is recorded as the outlet's report
        for reads sent soon after (`_reported_outlets`).
        """
        outlets = self.gcs_state.outlet_limits
        written_at = self._outlet_written_at
        settled = (
            written_at is None
            or time.monotonic() - written_at >= OUTLET_WRITE_VERIFY_DELAY_SECONDS
        )
        announced = {
            outlet_id: record
            for outlet_id, record in outlets.items()
            if record is not before.get(outlet_id)
        }
        guessed: dict[int, frozenset[str]] = {}
        if announced and self._outlet_unconfirmed and not settled:
            for outlet_id, record in announced.items():
                old = before.get(outlet_id)
                if old is None:
                    continue
                keep = {
                    field: getattr(old, field)
                    for field, value in self._outlet_unconfirmed.items()
                    if getattr(record, field) != value
                    and getattr(record, field) in self._outlet_superseded.get(field, ())
                }
                if keep:
                    outlets[outlet_id] = dataclasses.replace(record, **keep)
                    guessed[outlet_id] = frozenset(keep)
        for outlet_id, record in announced.items():
            # What the valve said, and when. Fields taken for a late echo are only a
            # guess, so they are marked and a read still decides them; the rest of the
            # record stands as announced.
            self._outlet_reported[outlet_id] = record
            self._outlet_announced_at[outlet_id] = at
            self._outlet_guessed[outlet_id] = guessed.get(outlet_id, frozenset())
        if announced and self._outlet_unconfirmed and settled:
            self._settle_outlet_fields(
                {outlet_id: outlets[outlet_id] for outlet_id in announced}
            )

    def _write_issue_id(self, fields: tuple[str, ...]) -> str:
        """One issue per valve and per setting, so no setting clears another's warning.

        The device's key stays last: that is how removing the entry finds its devices'
        issues (`async_remove_entry`).
        """
        setting = "_".join(fields)
        return f"outlet_write_unverified_{setting}_{device_issue_key(self.device_id)}"

    def _clear_write_issue(self, fields: tuple[str, ...]) -> None:
        """Drop one setting's warning — a verified write means its last one is stale."""
        ir.async_delete_issue(self.hass, DOMAIN, self._write_issue_id(fields))

    def forget_outlet_write_issues(self) -> None:
        """Drop every outlet-setting warning a previous run left for this valve.

        Repair issues outlive a restart; the unconfirmed and disputed state they describe
        does not.
        """
        prefix = "outlet_write_unverified_"
        suffix = f"_{device_issue_key(self.device_id)}"
        for domain, issue_id in list(ir.async_get(self.hass).issues):
            if (
                domain == DOMAIN
                and issue_id.startswith(prefix)
                and issue_id.endswith(suffix)
            ):
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _raise_write_issue(
        self, fields: tuple[str, ...], setting: str, detail: str
    ) -> None:
        """Surface an unverified write where the user will actually see it."""
        _LOGGER.warning("%s — %s", setting, detail)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._write_issue_id(fields),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="outlet_write_unverified",
            translation_placeholders={"setting": setting, "detail": detail},
        )

    def _seed_independent_reads(self) -> asyncio.Future[list[Any]]:
        """Start the seed reads that depend on nothing, so they overlap the ones that do.

        `gcs-configuration`, the two `gcs-usage` series, `about` and `gcs-preset` are not
        decoded against the valve's model, so they need not wait for the `gcs-settings`
        read that `gcs-state` does. Each read is guarded against `KohlerError`; anything
        else (`AuthError` from the token layer) is collected, not raised, and the caller
        raises the first once all have finished (`_raise_first`). The caller always awaits
        the returned future.
        """
        return asyncio.gather(
            self._async_seed_configuration(),
            self._async_seed_presets(),
            return_exceptions=True,
        )

    async def _async_seed_configuration(self) -> None:
        """The `gcs-configuration` record, plus the usage series and `about` beside it.

        The configuration record (installation data) and the first daily usage read are
        first-seed only; later daily reads happen when a shower ends. The monthly series
        and `about` are retried on every seed until a read answers (`_usage_seeded_month`,
        a non-empty `about_parts`). Entirely diagnostic: a failure is logged and setup
        continues. The reads overlap, and all finish before a sign-in failure is raised
        (`_seed_independent_reads`).
        """
        reads: list[Coroutine[Any, Any, None]] = []
        if self.configuration is None:
            reads.append(self._async_read_configuration())
            # `monthly=False`: the monthly series is read just below, in parallel.
            reads.append(self.async_refresh_daily_usage(monthly=False))
        if self._usage_seeded_month is None:
            # `async_get_usage` answers `{}` on failure rather than raising.
            reads.append(self.async_refresh_monthly_usage())
        if not self.about_parts:
            reads.append(self._async_read_about())
        _raise_first(await asyncio.gather(*reads, return_exceptions=True))

    async def _async_read_configuration(self) -> None:
        """Read `gcs-configuration` once; a failure latches `{}`."""
        try:
            self.configuration = await self.client.async_get_gcs_configuration(
                self.gcs_device.device_id
            )
        except KohlerError as err:
            _LOGGER.debug("Could not read gcs-configuration: %s", err)
            # `{}`, not None: static data, not worth retrying on every reconnect.
            self.configuration = {}

    async def _async_read_about(self) -> None:
        """Per-part serials and models, for the device registry and diagnostics.

        The client answers {} on failure, which tells the next seed to ask again.
        """
        self.about_parts = await self.client.async_get_gcs_about(
            self.gcs_device.device_id
        )

    async def async_refresh_monthly_usage(self) -> None:
        """Read the per-month usage series (`Interval=MONTH`, 400 days back).

        400 days covers a full year plus the current partial month.
        """
        from_date, to_date = _usage_window(400)
        fresh = await self.client.async_get_usage(
            self.gcs_device.device_id, from_date=from_date, to_date=to_date
        )
        # `async_get_usage` returns `{}` on failure; keep what is held rather than drop
        # prior months.
        if fresh or not self.usage:
            self.usage = fresh
        if fresh:
            self._usage_seeded_month = dt_util.now().strftime("%Y-%m")

    async def async_refresh_daily_usage(self, *, monthly: bool = True) -> None:
        """Read the per-day usage series (`Interval=DAY`, 35 days back).

        `DAY` rather than `WEEK`, which the server refuses; seven daily buckets make the
        week (`docs/protocol/platform.md` §6). 35 days covers a seven-day window in any
        timezone with slack.

        ``monthly`` False leaves the monthly series alone, for a caller reading it
        alongside this one.
        """
        # In a new calendar month, or while the monthly series has never been read, re-read
        # it first, so the finished month is in `self.usage` before the daily window rolls
        # past its start. ⚠️ Before the daily series, not after: each month counts the
        # larger of the two series' figures (`water._month_totals`), so a new daily window
        # starting part-way into last month would otherwise lower it until the monthly read
        # landed.
        seeded_month = self._usage_seeded_month
        if monthly and (
            seeded_month is None or dt_util.now().strftime("%Y-%m") != seeded_month
        ):
            await self.async_refresh_monthly_usage()
        from_date, to_date = _usage_window(35)
        fresh = await self.client.async_get_usage(
            self.gcs_device.device_id,
            from_date=from_date,
            to_date=to_date,
            interval="DAY",
        )
        # Keep the prior daily series if a read returns `{}`, so the usage sensors do not
        # blank to `unknown`.
        if fresh or not self.usage_daily:
            self.usage_daily = fresh

    async def _async_seed_presets(self) -> None:
        """Seed the preset slots. Independent of topology — see `_seed_independent_reads`."""
        try:
            presets = await self.client.async_get_gcs_presets(self.gcs_device.device_id)
            self.gcs_state.apply_preset_list(presets)
            self._presets_read = True
        except KohlerError as err:
            _LOGGER.debug("Could not read GCS presets: %s", err)

    @property
    def seed_incomplete(self) -> bool:
        """Whether a read this valve's controls depend on has not answered since setup.

        Its outlet layout and records (without them every outlet setting is refused) and
        its presets (without them the Favorite list is empty). The stream's first connect
        reads such a valve whole rather than only its live state; see
        `KohlerKonnectCoordinator._async_read_missed_live_state`. Tracks whether the reads
        answered, not whether what they fill is empty: a held message can fill one, and a
        valve can have no presets.
        """
        return not (
            self._topology_checked and self._settings_read and self._presets_read
        )

    async def async_seed(self) -> None:
        """Read this valve's state, limits and presets over REST.

        The valve half of `KohlerKonnectCoordinator._async_seed_state`, which says when it
        runs.
        """
        # The one real ordering: `gcs-settings` decides the outlet layout, which `gcs-state`
        # is decoded with (a single-zone valve on a two-zone entry would otherwise get a
        # zone 2). It also carries the per-outlet limits, which MQTT sends only unprompted,
        # one outlet at a time; re-read on every seed. The independent reads start first and
        # overlap that pair.
        background = self._seed_independent_reads()
        try:
            await self._async_seed_topology_and_state()
        except BaseException:
            # Cancelled or failed: cancel and reap the reads rather than orphan them against
            # an entry that may be going away.
            background.cancel()
            with suppress(asyncio.CancelledError):
                await background
            raise
        _raise_first(await background)

    def _outlet_limits_settled(self, serial_before_read: int) -> bool:
        """Whether a `gcs-settings` read may replace the cached outlet records.

        Not while an outlet write is in flight or began during the read, and not within
        `OUTLET_WRITE_VERIFY_DELAY_SECONDS` of the latest one: the cloud trails a write by
        ~25 s, and an old value read into the cache would be echoed by the next write. The
        write's own read-back refreshes the cache instead.
        """
        if self._outlet_write_lock.locked():
            return False
        if self._outlet_write_serial != serial_before_read:
            return False
        written_at = self._outlet_written_at
        return (
            written_at is None
            or time.monotonic() - written_at >= OUTLET_WRITE_VERIFY_DELAY_SECONDS
        )

    async def _async_seed_topology_and_state(self) -> None:
        """The one genuinely ordered pair: settings decide topology, topology decodes state."""
        try:
            outlet_writes = self._outlet_write_serial
            requested_at = time.monotonic()
            settings = await self.client.async_get_gcs_settings(
                self.gcs_device.device_id
            )
            self._settings_read = True
            if records := ui_records(settings):
                self.gcs_state.ui_config = records
            # The outlet split, checked until a read states it (`_apply_topology`).
            if not self._topology_checked:
                before = (self.model.outlets_valve1, self.model.outlets_valve2)
                self._topology_checked = self._apply_topology(settings)
                if self._topology_checked:
                    self.coordinator._async_note_topology(
                        self,
                        changed=before
                        != (self.model.outlets_valve1, self.model.outlets_valve2),
                    )
            limits = outlet_limits_from_settings(settings)
            if limits and self._outlet_limits_settled(outlet_writes):
                # As the valve reports it, and without undoing unconfirmed writes; see
                # `_reported_outlets` and `_adopt_outlet_read`.
                limits = self._reported_outlets(limits, requested_at)
                self._adopt_outlet_read(limits)
                self._outlets_caught_up(requested_at)
                self._settle_outlet_fields(limits)
        except KohlerError as err:
            _LOGGER.debug("Could not read outlet limits over REST: %s", err)

        await self.async_read_live_state()

    async def async_read_live_state(self) -> None:
        """Read `gcs-state` and apply what nothing newer has overtaken.

        The second half of every seed, and on its own the first connect's catch-up for a
        valve read before the stream was up (`KohlerKonnectCoordinator._handle_connected`).
        A `KohlerError` is logged and changes nothing; a sign-in failure propagates.
        `apply_rest_reply` decides what is applied and what is kept.
        """
        serials = dict(self._push_serials)
        # Word writes only, not `_local_write_serial`: an outlet setting changes no word.
        writes = self._words_serial
        connection = self.coordinator.stream_connects
        # When the read was sent, so a `Disconnected` it brings back cannot overrule a
        # message the valve sent after it; see `CloudConnectionWatch.note_rest_payload`.
        requested_at = time.monotonic()
        try:
            payload = await self.client.async_get_gcs_state(self.gcs_device.device_id)
        except KohlerError as err:
            _LOGGER.debug("Could not seed GCS state: %s", err)
            return
        if self.cloud_watch is not None:
            # `connectionState` sits beside `state`, which is all `apply_rest_state` reads;
            # hand it to the watch, at no extra request or cooldown. `notify=False`: this
            # can run before the platforms exist, and every caller pushes its own snapshot.
            self.cloud_watch.note_rest_payload(
                payload,
                "REST seed (setup, reconnect or update_entity)",
                notify=False,
                requested_at=requested_at,
            )
        was_warmup = self.gcs_state.warmup_mode
        self.apply_rest_reply(payload, serials, writes, requested_at=requested_at)
        self.state_read_on = connection
        # A shower that ended while the stream was down still gets its usage read.
        self._note_running_for_usage()
        # A warm-up mode that moved while the stream was down reaches nothing else.
        self.warmup.note_seeded_mode(was_warmup, self.gcs_state.warmup_mode)

    def apply_rest_reply(
        self,
        payload: Any,
        serials: dict[str, int],
        writes: int,
        *,
        requested_at: float | None = None,
    ) -> set[str]:
        """Apply a `gcs-state` reply, keeping what overtook it since the read was sent.

        ``serials`` and ``writes`` are `_push_serials` and `_words_serial` as they stood
        when the read went out, and ``requested_at`` the monotonic time it went out. Returns
        the fields kept rather than applied, so a caller reading for one of them can tell
        its answer was overtaken.

        `gcs-state` trails the device by seconds, so the reply can be older than state
        already held. Kept over the reply:

        * each field a push carried while the read was in flight (`_push_serials`), and
          only those fields;
        * the zone words and session fields, when a word write from here was sent during
          the read or its overlay still stands (`_sent_words_pending`);
        * any field reported within `_GCS_STATE_TRAILS_SECONDS` before the read was sent.

        Everything else in the reply applies.
        """
        kept = {
            field
            for field, count in self._push_serials.items()
            if count != serials.get(field, 0)
        }
        if self._words_serial != writes or self._sent_words_pending():
            kept |= {*_GCS_WORD_FIELDS, *_GCS_SESSION_FIELDS}
        if requested_at is not None:
            kept |= {
                field
                for field, at in self._reported_at.items()
                if requested_at - at < _GCS_STATE_TRAILS_SECONDS
            }
        if kept:
            _LOGGER.debug(
                "Keeping %s's %s over a gcs-state reply that is older",
                self.name,
                ", ".join(sorted(kept)),
            )
        with _keeping(self.gcs_state, sorted(kept)):
            self.gcs_state.apply_rest_state(payload)
        return kept

    def _sent_words_pending(self) -> bool:
        """Whether the words last sent from here still stand in for the valve's report.

        `_valve_baseline`'s test without its side effect: armed, inside the hold, and no
        report since the write. `_sent_words` alone is not enough: it is cleared only when
        the next write asks for a baseline, so it can outlive its hold indefinitely.
        """
        if self._sent_words is None:
            return False
        state = self.gcs_state
        return (
            time.monotonic() - self._sent_at <= self._sent_hold_seconds
            and state.valve1 is self._sent_over[0]
            and state.valve2 is self._sent_over[1]
        )

    async def async_refresh_faults(self) -> None:
        """Read the valve's fault log and keep the entries that are still active.

        The valve's counterpart of the controller's `hub-diagnostics/{id}/active` read, run
        on every seed and twice a day (`KohlerKonnectCoordinator.async_refresh_faults`).
        Feeds the Problem sensor: an active fault can sit in the log while the status
        word's error flag and `currentSystemState` stay clear.

        Kept: entries the app would show (`_fault_is_active`). Only code, area, valve and
        timestamp are copied; the log's `id`, `deviceId` and `details` can carry identity.

        A failed read changes nothing: the client's `{}` has no `errorDetails` list, which
        is not "no faults". Nor does it fail the seed; the other reads report a dead sign-in.
        """
        try:
            payload = await self.client.async_get_gcs_diagnostics(self.device_id)
        except (AuthError, KohlerError) as err:
            _LOGGER.debug("Could not read %s's fault log: %s", self.name, err)
            return
        details = payload.get("errorDetails") if isinstance(payload, dict) else None
        if not isinstance(details, list):
            return
        self.active_faults = [
            {
                "code": str(entry.get("errorCode")).strip(),
                "area": entry.get("area"),
                "valve_id": entry.get("valveId"),
                "timestamp": entry.get("timestamp"),
            }
            for entry in details
            if _fault_is_active(entry)
        ]

    async def async_clear_fault_log(self) -> None:
        """Clear the valve's fault log, as the Konnect app's Diagnostics screen does.

        ``DELETE gcs-diagnostics/{id}``. The faults known from the log go with it, and the
        log is read again at once: clearing it is not clearing a fault the valve still
        has, which its next entry, or its status word, shows again. Not taken in turn with
        the word writes: it touches the cloud's record, not the valve.
        """
        with self.coordinator.command_errors(VALVE_OFFLINE):
            await self.client.async_clear_gcs_diagnostics(self.device_id)
        self.active_faults = []
        await self.async_refresh_faults()
        self.coordinator.async_refresh_entities()

    def journal_baseline(self) -> None:
        """Record the mode in force when the warmup journal opened, from the REST seed.

        The valve never announces its warm-up mode on connect, so without this a journal
        cannot say what a later change displaced, and an empty one looks broken. Called
        once per file, from `async_setup` after its seed (not `_async_seed_state`, which
        also runs on every reconnect).
        """
        self.warmup._warmup_journal(
            "baseline",
            mode=self.gcs_state.warmup_mode,
            auto_restore=self.warmup_auto_restore,
            restores_to=self.last_warmup_mode,
            source="rest",
        )

    # ------------------------------------------------------------------ #
    # Run-time limits, zone clocks and valve-word writes
    # ------------------------------------------------------------------ #
    @property
    def outlet_run_times(self) -> dict[int, int]:
        """Each outlet's `maximumRunTime` in seconds, keyed by 1-based outlet number.

        Read from the outlet records the seed and MQTT keep current. An outlet whose record
        has not arrived is simply absent.
        """
        limits = self.gcs_state.outlet_limits
        result: dict[int, int] = {}
        for outlet in range(1, self.model.total_outlets + 1):
            zone, bit = self.model.outlet_location(outlet)
            record = limits.get(self.model.outlet_id(zone, bit + 1))
            if record is not None and record.maximum_run_time is not None:
                result[outlet] = record.maximum_run_time
        return result

    def _zone_limits(self) -> dict[int, tuple[int, ...]]:
        """The distinct `maximumRunTime` values configured for each zone's outlets.

        Set per outlet but timed per zone, and one zone's outlets can disagree after a
        write that stopped part-way (`docs/protocol/gcs_valve.md` §4.1), so every distinct
        value is returned. A preset's own `time`, a separate limit, is not included.
        """
        limits: dict[int, set[int]] = {zone: set() for zone in self.model.zones}
        for outlet, seconds in self.outlet_run_times.items():
            zone, _ = self.model.outlet_location(outlet)
            limits.setdefault(zone, set()).add(seconds)
        return {zone: tuple(sorted(values)) for zone, values in limits.items()}

    def run_time_limits_for_zone(self, zone: int) -> tuple[int, ...]:
        """The `maximumRunTime` candidates for one zone. Empty until the valve announces."""
        return self._zone_limits().get(zone, ())

    def zone_flowing_for(self, zone: int) -> float | None:
        """Seconds this zone has been running, or None when it is idle.

        Also None after a reconnect until the zone next starts — see `forget_timings`.
        """
        return self._zone_clock.flowing_for(zone)

    @callback
    def _update_zone_clock(self) -> None:
        """Start or stop each zone's clock from the latest valve words.

        A zone is running when it has outlets open and is not paused (`0x40`), as the
        valve's own timer reads it (`docs/protocol/gcs_valve.md` §4.1).
        """
        state = self.gcs_state
        if state is None:
            return
        flowing: dict[int, bool] = {}
        for zone in self.model.zones:
            word = state.zone_word(zone)
            flowing[zone] = bool(word and word.outlet_mask and not word.paused)
        self._zone_clock.update(flowing)

    def _valve_baseline(self, *, outlets: bool = True) -> tuple[Any, Any]:
        """The zone words the next write builds on: the last ones sent, then the report.

        Every write rebuilds both zones, so its baseline decides what it keeps. The report
        lags a write by the echo (~1-2 s), so for `_sent_hold_seconds` the words last sent
        from here stand in for it.

        ⚠️ A report inside that window narrows the sent words; it never widens them. A
        report is not always newer than the write (a late echo of an earlier write, or a
        status sent before this one applied), and content cannot tell a stale report from a
        change at the wall. So for each zone reported since, the outlets are those open in
        both the sent word and the report, and the zone is paused if either is
        (`_narrowed`); the temperature is the report's. A stale report cannot reopen an
        outlet or lift a pause, and a Stop at the wall still holds; the cost is that an
        outlet opened at the wall within those seconds may be closed. Do not try to
        recognise late echoes instead: that could only keep an outlet a report shows
        closed, which a Stop at the wall must never get.

        A report is recognised by identity, not content: `GcsState` decodes every
        `GCS_SOLO_STS` and REST seed into new word objects, so a word other than the one in
        place when the write was asked for (`_sent_over`) means the valve has spoken since.
        A garbled or missing word leaves its zone's object as it was
        (`GcsState._apply_solo`), so it cannot end that zone's hold.

        During a warm-up the report shows the warm-up's outlets, not the shower's, so the
        outlets come from `_warmup_shower`; raises `HomeAssistantError` when that shower was
        not sent from here. ``outlets`` False (a Stop, which names every zone's outlets)
        skips this and is never refused.
        """

        def described(word: Any) -> str:
            return f"outlets 0x{word.outlet_mask:02X}" + (
                " paused" if word.paused else ""
            )

        state = self.gcs_state
        reported = (state.valve1, state.valve2)
        # During a warm-up the outlets come from `_warmup_shower`, not from any narrowing.
        warming = outlets and self._warming_up()
        sent = self._sent_words
        baseline = reported
        if sent is not None and (
            time.monotonic() - self._sent_at <= self._sent_hold_seconds
        ):
            words = []
            for zone, own, now, then in zip(
                (1, 2), sent, reported, self._sent_over, strict=True
            ):
                word = own if now is then else self._narrowed(now, own)
                if (
                    outlets
                    and not warming
                    and own is not None
                    and word is not None
                    and (word.outlet_mask, word.paused) != (own.outlet_mask, own.paused)
                ):
                    _LOGGER.info(
                        "%s: zone %d reported %s since the last command sent it %s; "
                        "building on what both say, %s, rather than reopening anything",
                        self.name,
                        zone,
                        described(now),
                        described(own),
                        described(word),
                    )
                words.append(word)
            baseline = (words[0], words[1])
        if not warming:
            return baseline
        shower = self._warmup_shower()
        if shower is None:
            _LOGGER.info(
                "%s: not sending a command that keeps outlets as they stand: the valve "
                "is warming up a shower whose outlets were not sent from here",
                self.name,
            )
            raise HomeAssistantError(
                f"{self.name} is warming up, and until it finishes the outlets it reports "
                "are the warm-up's own rather than the shower's, so a change sent now "
                "would run every one of them. Nothing was sent. Try again once the "
                "warm-up is over, stop the shower, or send the whole shower in one "
                "command with the custom_shower action."
            )
        # The shower's outlets go only to a zone still warming; one reported stopped or
        # paused since keeps what it reports, so a zone closed elsewhere is not reopened.
        return tuple(
            self._with_assignment(word, own)
            if now is not None and now.outlet_mask and not now.paused
            else word
            for word, own, now in zip(baseline, shower, reported, strict=True)
        )

    @staticmethod
    def _narrowed(word: Any, other: Any) -> Any:
        """``word`` with only the outlets ``other`` also has open, paused if either is.

        The merge `_valve_baseline` and `_async_send_words` use; it never opens water, only
        closes an outlet or keeps a pause. ``word`` supplies every other field and is
        returned unchanged when there is no ``other`` (never ``other`` in its place).
        """
        if word is None or other is None:
            return word
        return dataclasses.replace(
            word,
            outlet_mask=word.outlet_mask & other.outlet_mask,
            paused=word.paused or other.paused,
        )

    @staticmethod
    def _with_assignment(word: Any, own: Any) -> Any:
        """``word`` with ``own``'s outlets and pause — or ``own`` where there is no word."""
        if word is None or own is None:
            return own if word is None else word
        return dataclasses.replace(word, outlet_mask=own.outlet_mask, paused=own.paused)

    def _warming_up(self) -> bool:
        """Whether the valve says it is warming up, and is running water to do it.

        Both, so a ``warmUpStatus`` left over from a report that did not repeat it cannot
        make an idle or paused valve's words read as a warm-up's.
        """
        state = self.gcs_state
        return bool(state.warmup_in_progress) and any(
            word is not None and word.outlet_mask and not word.paused
            for word in (state.valve1, state.valve2)
        )

    def _warmup_shower(self) -> tuple[Any, Any] | None:
        """The last words sent from here, when they are the shower the valve is warming up.

        ⚠️ A warming valve reports every outlet of its warm-up set, not the shower's, and a
        write during the warm-up moves it onto the outlets the write names; so a write must
        not build on the reported masks.

        The sent words count as that shower when they left an outlet running, nothing has
        decided the valve's words since (`_note_local_write` drops them), they were sent
        less than `RESUME_DEADLINE_SECONDS` ago, and the valve has run without a break
        since: some zone has been flowing (`zone_flowing_for`) since no later than
        `WARMUP_WINDOW_SECONDS` after they were sent. Otherwise the warm-up was started
        elsewhere (panel, app, favorite), its outlets are unknown, and this returns None.
        """
        sent = self._sent_words
        if sent is None or not any(
            word is not None and word.outlet_mask and not word.paused for word in sent
        ):
            return None
        age = time.monotonic() - self._sent_at
        if age > RESUME_DEADLINE_SECONDS:
            return None
        flowing = (self.zone_flowing_for(zone) for zone in self.model.zones)
        if not any(
            seconds is not None and seconds >= age - WARMUP_WINDOW_SECONDS
            for seconds in flowing
        ):
            return None
        return sent

    def _wire_flow(self, zone: int, percent: float) -> float:
        """A zone's flow as chosen (percent of its own ceiling), restated for `encode_word`.

        `encode_word` reads percent against a 200 ceiling byte, so on a zone with another
        ceiling the percent is re-expressed: the byte it means on this zone, as the percent
        that gives that byte at 200. Identity where the ceiling is 200. Converted only here,
        so `zone_flow` stays in the entity's scale.
        """
        _, high = self.gcs_state.zone_flow_limits(zone)
        return flow_byte_to_percent(flow_percent_to_byte(percent, high), FLOW_BYTE_MAX)

    async def _async_send_words(self, word1: str, word2: str) -> int:
        """POST a pair of words and arm the overlay; return the serial this write took.

        The caller holds `_valve_write_lock` and built the words from `_valve_baseline`.
        The overlay is armed only once the POST has landed, and only if nothing else decided
        the valve's words meanwhile (`_words_serial`: a controller command counts, an outlet
        setting does not). The report the words are sent over is read before the POST, so
        a report arriving during it counts as newer.

        ⚠️ A POST that fails or is cancelled may still have reached the valve, so the
        baseline it was built on comes back narrowed by it (`_narrowed`) and is held for
        `_sent_hold_seconds`: an outlet it closed stays closed and a pause it set stays set
        (a Stop that "failed" after landing is not undone), and nothing it would have opened
        is taken up. Not when something else decided the words meanwhile.
        """
        state = self.gcs_state
        previous = (self._sent_words, self._sent_over, self._sent_at)
        asked_over = (state.valve1, state.valve2)
        asked_at = time.monotonic()
        warming = self._warming_up()
        # The sentinel addresses no valve and the device discards the whole command, so a
        # write carrying it changes nothing worth remembering.
        words = (
            None
            if word1 == UNUSED_VALVE_WORD
            or (self.model.uses_valve2 and word2 == UNUSED_VALVE_WORD)
            else (
                decode_word(word1),
                decode_word(word2) if self.model.uses_valve2 else None,
            )
        )
        self._note_local_write()
        serial = self._local_write_serial
        decided = self._words_serial
        try:
            with self.coordinator.command_errors(VALVE_OFFLINE):
                await self.gcs.async_write_valves(word1, word2)
        except BaseException:
            if self._words_serial == decided:
                sent, over, at = previous
                # A Stop takes the usual path even during a warm-up and holds afresh: the
                # warm-up can end as it lands, leaving its last report (the warm-up's
                # outlets) as the next baseline.
                stops = words is not None and not any(
                    word is not None and word.outlet_mask for word in words
                )
                if words is not None and warming and not stops:
                    # ⚠️ Never narrow the report during a warm-up: it shows the warm-up's
                    # whole outlet set, and narrowing it would hand the failed command's
                    # outlets to `_warmup_shower`. Only shower words sent from here are
                    # narrowed, keeping the time `_warmup_shower` ages them by.
                    if sent is not None:
                        sent = (
                            self._narrowed(sent[0], words[0]),
                            self._narrowed(sent[1], words[1]),
                        )
                elif words is not None:
                    if sent is None or asked_at - at > self._sent_hold_seconds:
                        # Nothing stood in for the report, so the report is what this
                        # write was built on.
                        sent, over = asked_over, asked_over
                    sent = (
                        self._narrowed(sent[0], words[0]),
                        self._narrowed(sent[1], words[1]),
                    )
                    at = time.monotonic()
                self._sent_words, self._sent_over, self._sent_at = sent, over, at
            raise
        if self._words_serial != decided or words is None:
            return serial
        self._sent_words = words
        self._sent_over = asked_over
        self._sent_at = time.monotonic()
        return serial

    async def async_apply_valve(
        self,
        *,
        zone1_temperature: float | None = None,
        zone2_temperature: float | None = None,
        zone1_flow: float | None = None,
        zone2_flow: float | None = None,
        zone_masks: dict[int, int] | None = None,
        zone_paused: dict[int, bool] | None = None,
    ) -> None:
        """Re-send both valve words with selected fields overridden.

        The valve takes no partial write: every ``solowritesystem`` carries both zones' full
        state, as the Konnect app sends on every adjustment. Anything not overridden comes
        from `_valve_baseline` (which describes the overlay and the warm-up rule), under
        `_valve_write_lock`.

        * ⚠️ Pause: a zone whose mask is not overridden keeps its pause exactly; re-sending
          a paused ``0x41`` as ``0x01`` would start water. ``zone_masks`` replaces a zone's
          mask and clears its pause unless ``zone_paused`` says otherwise, so a stop stays a
          plain ``0x00``; ``zone_paused`` sets the flag outright.
        * Flow never comes from the valve: an omitted flow writes the zone's ``zone_flow``
          (what its Flow number shows), never the byte the valve holds, which is not the
          flow setting (`GcsState.flow_is_live`). A flow passed here is a percent of that
          zone's own ceiling, remembered once the write is accepted. So a temperature change
          mid-shower also restores Home Assistant's flow if the panel changed it.

        Raises `HomeAssistantError` if a Stop was asked for after this
        (`async_stop_shower`), or during a warm-up started elsewhere (`_warmup_shower`).
        """
        requested = self._stop_generation
        async with self._valve_write_lock:
            self._refuse_if_stopped_since(requested)
            await self._async_apply_valve_locked(
                temperatures={1: zone1_temperature, 2: zone2_temperature},
                flows={1: zone1_flow, 2: zone2_flow},
                zone_masks=zone_masks or {},
                zone_paused=zone_paused or {},
            )

    def _refuse_if_stopped_since(self, requested: int) -> None:
        """Refuse a command asked for before a Stop that is still waiting behind it.

        ``requested`` is `_stop_generation` as it stood when the command was asked for,
        before it waited for `_valve_write_lock`; this runs once it holds the lock. A
        command asked for after the Stop goes ahead as usual, behind it. See
        `async_stop_shower`.
        """
        if self._stop_generation == requested:
            return
        _LOGGER.info(
            "%s: a command asked for before a Stop was not sent; the Stop goes first",
            self.name,
        )
        raise HomeAssistantError(
            f"Not sent to {self.name}: superseded by a Stop asked for after it, which "
            "goes first."
        )

    async def _async_apply_valve_locked(
        self,
        *,
        temperatures: dict[int, float | None],
        flows: dict[int, float | None],
        zone_masks: dict[int, int],
        zone_paused: dict[int, bool],
        baseline: tuple[Any, Any] | None = None,
    ) -> None:
        """`async_apply_valve`'s body, for a caller already holding `_valve_write_lock`.

        ``baseline`` is the caller's own `_valve_baseline`, where it built a mask from it
        already, so both are built on the same one.
        """
        if baseline is None:
            # Outlets come from the baseline for every zone the write does not name; a Stop
            # names them all, so it is never refused during a warm-up.
            baseline = self._valve_baseline(
                outlets=any(zone not in zone_masks for zone in self.model.zones)
            )
        # Masks are per zone throughout; no global outlet numbering is involved.
        current = dict(zip((1, 2), baseline, strict=True))

        def resolve(zone: int, prefix: int) -> str:
            word = current[zone]
            temperature = temperatures.get(zone)
            celsius = (
                unit_to_celsius(temperature, self.temperature_unit)
                if temperature is not None
                else (word.temperature_celsius if word else 38.0)
            )
            # Never the current word's flow; see `async_apply_valve`.
            flow = flows.get(zone)
            percent = (
                flow
                if flow is not None
                else self.zone_flow.get(zone, DEFAULT_FLOW_PERCENT)
            )
            if zone in zone_masks:
                mask = zone_masks[zone]
                paused = zone_paused.get(zone, False)
            else:
                mask = word.outlet_mask if word else 0
                paused = zone_paused.get(zone, bool(word and word.paused))
            return encode_word(
                prefix, celsius, self._wire_flow(zone, percent), mask, paused=paused
            )

        valve1 = resolve(1, VALVE1_PREFIX)
        if self.model.uses_valve2:
            valve2 = resolve(2, VALVE2_PREFIX)
        else:
            valve2 = UNUSED_VALVE_WORD

        await self._async_send_words(valve1, valve2)
        for zone, flow in flows.items():
            if flow is not None and zone in self.model.zones:
                self.zone_flow[zone] = float(flow)

    async def async_send_valve_hex(
        self, zone1_hex: str, zone2_hex: str | None = None
    ) -> dict[str, Any]:
        """POST raw command words to ``solowritesystem``. **This can run water.**

        The escape hatch for what the entities do not model, on the same endpoint as every
        other control path. Both words are validated (`_command_half`) before anything is
        sent, and the decoded meaning is logged.

        ``zone2_hex`` omitted re-sends zone 2's current word (setpoint, assignment and pause
        as `_valve_baseline` has them, flow from ``zone_flow``), so zone 2 carries on.
        ⚠️ Never ``00000000`` for that: on a two-valve system the all-zero word makes the
        device discard the whole command, zone 1 included. A zone that should stay shut
        gets a well-formed word with mask ``0x00``; only a single-zone model sends the
        all-zero word, for the valve it does not have.

        Refused during a warm-up started elsewhere when zone 2 is left to the baseline, and,
        like every write, when a Stop was asked for after it (`async_stop_shower`).

        Returns the decoded interpretation of what was sent.
        """
        result, _ = await self._async_send_valve_hex(zone1_hex, zone2_hex)
        return result

    async def _async_send_valve_hex(
        self, zone1_hex: str, zone2_hex: str | None, *, requested: int | None = None
    ) -> tuple[dict[str, Any], int]:
        """`async_send_valve_hex`, also returning the serial its write took.

        For `async_custom_shower`, whose watcher must notice anything sent after its own
        write, including while this POST was in flight. ``requested`` is the
        `_stop_generation` the caller took; if omitted it is taken here, before the lock
        (nothing awaits before that point).
        """
        word1 = _command_half(zone1_hex, "zone1_hex")

        # On a two-valve system a typed all-zero zone 2 is treated as blank: sent literally,
        # it would make the device discard the whole command.
        if zone2_hex and zone2_hex.strip("0") == "" and self.model.uses_valve2:
            _LOGGER.info(
                "send_valve_hex: zone2_hex was all zeroes, which addresses no valve; "
                "re-sending zone 2's current state instead so the command is not discarded"
            )
            zone2_hex = None

        if zone2_hex and not self.model.uses_valve2 and zone2_hex.strip("0") != "":
            # The service form offers Zone 2 whenever any valve on the account has one, so
            # refuse a word for a zone this valve lacks. All zeroes is exempt: it is this
            # valve's own "no second valve" word, which the branch below sends anyway.
            raise HomeAssistantError(
                f"{self.name} has one zone, so zone2_hex addresses nothing. Leave it empty "
                f"(or all zeroes) — the word {zone2_hex!r} was not sent."
            )
        word2: str | None = None
        if zone2_hex:
            word2 = _command_half(zone2_hex, "zone2_hex")
        elif not self.model.uses_valve2:
            # The all-zero word's one legitimate use: there is no second valve.
            word2 = UNUSED_VALVE_WORD

        if word1 == UNUSED_VALVE_WORD:
            # Allowed (the worst outcome is that nothing happens), but never silently.
            _LOGGER.warning(
                "send_valve_hex: zone1_hex is the all-zero sentinel, which addresses no "
                "valve — the device is expected to discard this entire command, zone 2 "
                "included. To close zone 1 instead, send a word with outlet mask 00."
            )

        if requested is None:
            requested = self._stop_generation
        async with self._valve_write_lock:
            self._refuse_if_stopped_since(requested)
            if word2 is None:
                # Re-send zone 2 as it stands, never the all-zero word; see the docstring.
                current = self._valve_baseline()[1]
                word2 = encode_word(
                    VALVE2_PREFIX,
                    current.temperature_celsius if current else 38.0,
                    self._wire_flow(2, self.zone_flow.get(2, DEFAULT_FLOW_PERCENT)),
                    current.outlet_mask if current else VALVE_STOP_MASK,
                    paused=current.paused if current else False,
                )

            decoded = {
                "zone1": _describe_word(word1),
                "zone2": _describe_word(word2),
            }
            _LOGGER.info(
                "send_valve_hex: zone1=%s (%s), zone2=%s (%s)",
                word1,
                decoded["zone1"],
                word2,
                decoded["zone2"],
            )
            serial = await self._async_send_words(word1, word2)

        return {"zone1_hex": word1, "zone2_hex": word2, "decoded": decoded}, serial

    async def async_custom_shower(
        self,
        zone1_hex: str,
        zone2_hex: str,
        *,
        keep_on_after_warmup: bool,
        flows: dict[int, float] | None = None,
    ) -> dict[str, Any]:
        """Send one complete shower command, optionally resuming it after the warm-up pause.

        The form-driven sibling of `async_send_valve_hex`: `services.py` builds both words
        with `encode_shower`, so nothing is read from the valve's last report. ``flows``
        are the percentages the words carry, per zone; once the write lands they become the
        zones' `zone_flow`, which later outlet and temperature changes re-send. It writes
        once, since a second write during a warm-up moves the warm-up onto the written
        outlets; the only re-send is the one the caller opts into:

        ``keep_on_after_warmup``: with warm-up enabled, the valve warms up and then pauses,
        and left alone that pause ends the session (`konnect/warmup_resume.py`). A
        background watcher re-sends the same two words once when that pause arrives. It
        does nothing if no warm-up follows, the warm-up ends in a plain stop, someone takes
        over at the wall, any other command is sent from here meanwhile, or a new custom
        shower replaces it.

        A pending watcher is cancelled before the write and again before the new one is
        stored, so overlapping calls cannot orphan one. A later Stop outranks both the
        write and the watcher (`async_stop_shower`).
        """
        requested = self._stop_generation
        self._cancel_custom_shower("a new custom shower was sent")
        result, serial = await self._async_send_valve_hex(
            zone1_hex, zone2_hex, requested=requested
        )
        for zone, percent in (flows or {}).items():
            if zone in self.model.zones:
                self.zone_flow[zone] = float(percent)
        self._cancel_custom_shower("a new custom shower was sent")
        if keep_on_after_warmup:
            # Tracked, so an unload during the write leaves no watcher behind.
            task = self._track(
                self._async_keep_on_after_warmup(
                    result["zone1_hex"], result["zone2_hex"], serial, requested
                )
            )
            task.add_done_callback(self._custom_shower_done)
            self._custom_shower_task = task
        return result

    @callback
    def _custom_shower_done(self, task: asyncio.Future[Any]) -> None:
        """Drop the finished watcher, and log anything it died of that it did not expect."""
        if self._custom_shower_task is task:
            self._custom_shower_task = None
        if task.cancelled():
            return
        if (err := task.exception()) is not None:
            _LOGGER.error("custom_shower: keep-on watcher failed: %r", err)

    @callback
    def _cancel_custom_shower(self, reason: str) -> None:
        """Drop a pending keep-on watcher, saying why."""
        task = self._custom_shower_task
        self._custom_shower_task = None
        if task is not None and not task.done():
            _LOGGER.info("custom_shower: keep-on watcher cancelled, %s", reason)
            task.cancel()

    async def _async_keep_on_after_warmup(
        self, word1: str, word2: str, serial: int, requested: int
    ) -> None:
        """Re-send a custom shower once the valve's warm-up ends in its pause.

        Woken by every coordinator update and once a second regardless, so `WarmupResume`'s
        deadlines hold if the valve goes quiet. Reads only the valve's own state:
        `warmUpStatus`, the pause flag, the masks, and the primary word's at-temperature
        bit, which tells the warm-up's own pause from a stop pressed elsewhere.

        ``serial`` is the `_local_write_serial` the custom shower's write took and
        ``requested`` its `_stop_generation`; if anything was sent, or a Stop asked for,
        since (including a Stop still queued for the lock), this gives up.
        """
        poke = asyncio.Event()

        @callback
        def _on_update() -> None:
            poke.set()

        def superseded() -> str | None:
            if self._stop_generation != requested:
                return "a Stop was asked for since"
            if self._local_write_serial != serial:
                return "another command was sent since"
            return None

        remove = self.coordinator.async_add_listener(_on_update)
        watch = WarmupResume(time.monotonic())
        try:
            while True:
                try:
                    await asyncio.wait_for(poke.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                poke.clear()
                if (reason := superseded()) is not None:
                    _LOGGER.info(
                        "custom_shower: %s; not resuming after the warm-up", reason
                    )
                    return
                state = self.gcs_state
                if state is None:
                    return
                words = [state.valve1]
                if self.model.uses_valve2:
                    words.append(state.valve2)
                outcome = watch.observe(
                    time.monotonic(),
                    state.warmup_in_progress,
                    [bool(word and word.paused) for word in words],
                    [word.outlet_mask if word else 0 for word in words],
                    at_temperature=state.at_temperature,
                )
                if outcome.decision is Decision.WAIT:
                    continue
                if outcome.decision is Decision.RESUME:
                    async with self._valve_write_lock:
                        # Again under the lock: a queued write counts itself only once it
                        # holds the lock, and resuming after it would undo it.
                        if (reason := superseded()) is not None:
                            _LOGGER.info(
                                "custom_shower: %s; not resuming after the warm-up",
                                reason,
                            )
                            return
                        _LOGGER.info(
                            "custom_shower: %s; resuming zone1=%s zone2=%s",
                            outcome.reason,
                            word1,
                            word2,
                        )
                        await self._async_send_words(word1, word2)
                else:
                    _LOGGER.log(
                        logging.WARNING if outcome.warn else logging.INFO,
                        "custom_shower: %s",
                        outcome.reason,
                    )
                return
        except HomeAssistantError as err:
            _LOGGER.warning(
                "custom_shower: could not resume after the warm-up: %s", err
            )
        finally:
            remove()

    async def async_set_zone_outlet(
        self, zone: int, outlet: int, on: bool, *, flow: float | None = None
    ) -> None:
        """Open or close one outlet within a zone.

        ``outlet`` is 1-based within that zone, as the hardware addresses it. Every other
        outlet, in both zones, is preserved. An outlet the current layout lacks is refused.
        ``flow`` is the flow to write for this zone; omitted, the zone's ``zone_flow`` is
        read when the write is built, so a flow change queued ahead is not undone.

        ⚠️ A paused zone (byte 3 ``0x4n``) shows every outlet off (`GcsState.zone_outlets`),
        so the toggle means what that view implies: On opens only this outlet and clears
        the pause, not the outlets the pause was holding; Off keeps the zone paused and
        drops this outlet from the assignment it would resume to.

        The assignment toggled is `_valve_baseline`'s, so it can be refused during a
        warm-up (`_warmup_shower`) and steps aside for a later Stop (`async_stop_shower`).
        """
        requested = self._stop_generation
        try:
            count = self.model.outlets_in_zone(zone)
        except ValueError:
            count = 0
        if not 1 <= outlet <= count:
            raise HomeAssistantError(
                f"{self.name} reports no outlet {outlet} in zone {zone}, so nothing was "
                "sent. Its outlet layout has changed since this entity was created."
            )
        bit = 1 << (outlet - 1)
        async with self._valve_write_lock:
            self._refuse_if_stopped_since(requested)
            baseline = self._valve_baseline()
            word = baseline[zone - 1]
            mask = word.outlet_mask if word else 0
            paused = bool(word and word.paused)
            if on:
                mask = bit if paused else mask | bit
                paused = False
            else:
                mask &= ~bit
            await self._async_apply_valve_locked(
                temperatures={},
                flows={zone: flow},
                zone_masks={zone: mask},
                zone_paused={zone: paused},
                baseline=baseline,
            )

    async def async_activate_preset(self, preset_id: int | str) -> None:
        """Start a stored GCS preset. **This runs water.**

        One ``controlpresetorexperience {preset, action}`` call; the valve runs the preset
        itself. A preset applies only the zones it opens an outlet on: a zone at mask
        ``0x00`` keeps its setpoint, so this cannot set an idle zone's temperature.

        Serialised with the word writes (`_valve_write_lock`), since it changes the same
        words, and drops their last-sent overlay. Steps aside for a Stop asked for after it
        (`async_stop_shower`).
        """
        requested = self._stop_generation
        async with self._valve_write_lock:
            self._refuse_if_stopped_since(requested)
            self._note_local_write()
            with self.coordinator.command_errors(VALVE_OFFLINE):
                await self.gcs.async_activate_preset(preset_id, True)

    async def async_start_default_shower(self) -> None:
        """Run the default shower from preset 1's stored valve words. **This runs water.**

        As Kohler's clients do, preset 1's words go out as one ``solowritesystem`` (built by
        `preset_words_to_pair` at the stored flow), never as a preset run, so preset 1 is
        never activated or written and only ``maximumRunTime`` ends the shower
        (`docs/protocol/gcs_valve.md` §3.4). Raises `HomeAssistantError` with
        `default_shower_words`' reason when there are no usable words.

        A whole shower, so it is never built on the valve's last report and goes out during
        a warm-up too. Steps aside for a Stop asked for after it (`async_stop_shower`).
        """
        requested = self._stop_generation
        try:
            word1, word2 = self.default_shower_words()
        except ValveHexError as err:
            raise HomeAssistantError(
                f"{self.name} cannot start its default shower (preset "
                f"{SHOWER_ON_PRESET_ID}), so nothing was sent: {err}."
            ) from err
        async with self._valve_write_lock:
            self._refuse_if_stopped_since(requested)
            _LOGGER.debug(
                "Default shower from preset %s's stored words: zone1=%s, zone2=%s",
                SHOWER_ON_PRESET_ID,
                word1,
                word2,
            )
            await self._async_send_words(word1, word2)

    def default_shower_words(self) -> tuple[str, str]:
        """The two command words `Shower on` sends: preset 1's stored words, ready to go.

        From the setup read and every ``GCS_PRESET_STS`` push; nothing is read here. Raises
        `ValveHexError` saying why there are none: preset 1 not reported, holding no word,
        a word failing validation, or opening no outlet. Shared with the diagnostics report
        (`refused_because`), so the reason never names the valve.
        """
        preset = self.gcs_state.presets.get(SHOWER_ON_PRESET_ID)
        if preset is None:
            raise ValveHexError(
                "the valve has not reported it; reloading the integration reads it again"
            )
        word1, word2 = preset_words_to_pair(self.model, preset.valve_words)
        if not any(decode_word(word).outlet_mask for word in (word1, word2)):
            raise ValveHexError(
                "it opens no outlet; the default outlets are set in the Konnect app"
            )
        return word1, word2

    async def async_write_ui_config(
        self, changes: Mapping[str, str], *, flow_control_on: bool | None = None
    ) -> None:
        """Change the touch panels' settings as the app's Control Panel saves them
        (`docs/protocol/gcs_valve.md` §6): every panel's record, whole, one after another,
        from a fresh read, with ``changes`` (written form) applied to the shared settings.

        Refused while water runs, as the app refuses it, and when the records don't read as
        the app expects (`ui_config.control_panel_writes`): written then, they would guess.
        A failure part-way leaves the panels written so far as written.
        """
        if self.gcs_state.is_running:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="panel_settings_while_running",
                translation_placeholders={"name": self.name},
            )
        offline = "The Anthem valve is offline, so its touch panels can't be changed."
        async with self._ui_config_lock:
            with self.coordinator.command_errors(offline):
                settings = await self.client.async_get_gcs_settings(
                    self.gcs_device.device_id
                )
            records = ui_records(settings)
            try:
                writes = control_panel_writes(
                    records,
                    changes,
                    second_valve=self.model.outlets_valve2 > 0,
                    flow_control_on=flow_control_on,
                )
            except UnwritableRecords as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="panel_settings_unreadable",
                    translation_placeholders={"name": self.name, "reason": str(err)},
                ) from err
            held = list(records)
            try:
                for index, record in enumerate(writes):
                    with self.coordinator.command_errors(offline):
                        await self.gcs.async_write_ui_config(record)
                    held[index] = read_back(records[index], record)
            finally:
                self.gcs_state.ui_config = held
                self.coordinator.async_update_listeners()

    async def async_restart(self) -> None:
        """Reboot the valve — the app's Restart Product. **Stops any running water.**

        ``valvereset {reset: "productRestart"}``. Serialised with the word writes, so a
        write still in flight cannot land after the reboot.
        """
        async with self._valve_write_lock:
            self._note_local_write()
            with self.coordinator.command_errors(
                "The Anthem valve is offline, so it cannot receive a restart. Power-cycle "
                "it at the breaker instead."
            ):
                await self.gcs.async_restart()

    async def async_control_experience(self, preset_id: int, on: bool) -> None:
        """Start or stop a stored valve experience. **Starting one runs water.**

        Same ``controlpresetorexperience {preset, action}`` body as a preset, which Konnect
        3.0.6 sends for an experience id. Starting is live-verified, stopping only
        app-confirmed (`docs/protocol/gcs_valve.md` §10). Serialised with the word writes,
        like a preset; starting steps aside for a Stop asked for after it, stopping goes
        ahead regardless.
        """
        requested = self._stop_generation
        async with self._valve_write_lock:
            if on:
                self._refuse_if_stopped_since(requested)
            self._note_local_write()
            with self.coordinator.command_errors(VALVE_OFFLINE):
                await self.gcs.async_activate_preset(preset_id, on)

    async def async_stop_shower(self) -> None:
        """Stop the water: mask byte ``0x00`` on both zones, not the ``0x40`` pause.

        Konnect 3.0.6 stops with ``0x40`` (pause bit, no outlets), the Anthem Plus
        controller with ``0x00``; both leave the valve idle. ``0x00`` is the form verified
        live from here, and it cannot be mistaken for the valve's run-time cutoff, which
        pauses (`docs/protocol/gcs_valve.md` §3.1, §4.1).

        Built by `async_apply_valve`'s body rather than `GcsDevice.async_turn_off()` (which
        writes a flat 38.0 °C), so each zone keeps its setpoint; a paused zone also gets a
        plain ``0x00``, and it is never refused, warm-up or not.

        ⚠️ A Stop outranks every command asked for before it: it bumps `_stop_generation`
        before any await, and each earlier command steps aside when its turn at
        `_valve_write_lock` comes (`_refuse_if_stopped_since`); the custom-shower watcher
        gives up. It still queues behind the POST already in flight (never cancelled), so
        an open cannot land after it. A Stop cancelled while it waits still counts.
        """
        self._stop_generation += 1
        async with self._valve_write_lock:
            await self._async_apply_valve_locked(
                temperatures={}, flows={}, zone_masks={1: 0, 2: 0}, zone_paused={}
            )

    # ------------------------------------------------------------------ #
    # Warm-up: the machinery is `warmup_manager.WarmupManager`; these are the names
    # entities, services and diagnostics call.
    # ------------------------------------------------------------------ #
    async def async_set_warmup(self, mode: str) -> None:
        """Set the valve's warmup mode. **This does not run water now.**"""
        await self.warmup.async_set_warmup(mode)

    async def async_read_warmup_mode(self) -> str | None:
        """Read the warmup mode from the REST API and apply it, returning what it said."""
        return await self.warmup.async_read_warmup_mode()

    @property
    def warmup_auto_restore(self) -> bool:
        """Whether to put the warmup mode back after something else disables it."""
        return self.warmup.auto_restore

    def set_warmup_auto_restore(self, enabled: bool) -> None:
        """Turn Warmup Auto-Restore on or off; the switch and the sidebar panel both do."""
        self.warmup.set_auto_restore(enabled)

    @property
    def last_warmup_mode(self) -> str | None:
        """The last *enabled* warmup mode seen on the valve, or None if never seen."""
        return self.warmup.last_mode

    def _message_window(self, since: float, until: float | None = None) -> list[dict]:
        """Messages between two monotonic instants, oldest first, without the clock field."""
        return self.warmup._message_window(since, until)

    @callback
    def _handle_warmup_mode_change(
        self, before: str | None, after: str | None, *, announced: bool = False
    ) -> None:
        """React to the valve announcing a new warmup mode."""
        self.warmup.handle_mode_change(before, after, announced=announced)


class KohlerKonnectCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Owns the connection and the per-device state objects."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # `update_interval=SCAN_INTERVAL` (None) disables polling: REST is read at setup and
        # on every MQTT reconnect, plus the twice-daily firmware and fault reads
        # (`FIRMWARE_CHECK_INTERVAL`). `_async_update_data` runs only when asked
        # (`homeassistant.update_entity`, the manual refresh), and skips the base class's
        # first refresh, which would repeat setup's seed.
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            config_entry=entry,
        )
        # The base class's `config_entry`, under the name the rest of this integration uses.
        self.entry = entry
        # The entry as it looked when this coordinator was built. Home Assistant mutates the
        # entry in place, so `_async_update_listener` compares against this, not `entry`.
        self.reload_signature = entry_reload_signature(entry)
        # The stored split wins over the SKU: an install that matches no catalogue model
        # still reloads correctly, and a SKU label can never silently change topology.
        # A faucet-only account stores neither: the setup flow skips the valve question
        # when there is no valve or controller to ask it about.
        stored = entry.data.get(CONF_ZONE_OUTLETS)
        if isinstance(stored, (list, tuple)) and len(stored) == 2:
            self.model = model_for_topology(int(stored[0]), int(stored[1]))
        else:
            self.model = get_valve_model(
                entry.data.get(CONF_VALVE_MODEL) or DEFAULT_VALVE_MODEL
            )
        self.temperature_unit: str = entry.data.get(CONF_TEMPERATURE_UNIT, "Fahrenheit")
        # `Standard` (US gallons) or `Metric`, as the Konnect account is set. Captured at
        # config time beside the temperature unit; refreshed from the customer read below.
        self.water_units: str = entry.data.get(CONF_WATER_UNITS, "Standard")

        session = async_get_clientsession(hass)
        self.auth = KohlerAuth(
            session,
            entry.data.get(CONF_REFRESH_TOKEN),
            refresh_token_source=self._stored_refresh_token,
        )
        # Persist a rotated refresh token the moment B2C issues one, or the entry keeps a
        # retired token; see `KohlerAuth._async_token_request`.
        self.auth.on_token_rotated = self._store_refresh_token
        # The token this coordinator last saw in, or wrote to, the entry. Once closed it
        # writes only over that one; see `_store_refresh_token`.
        self._entry_token: str | None = entry.data.get(CONF_REFRESH_TOKEN)
        self.client = KohlerClient(session, self.auth, entry.data.get(CONF_TENANT_ID))
        #: Set at the top of `async_shutdown_stream` and never cleared. Every way in from
        #: the stream checks it, and so does `Valve._track`, because the stream can still
        #: deliver while it stops. See `async_shutdown_stream`.
        self.closed = False
        #: The Kohler device ids an envelope just changed, for the length of the
        #: notification it causes; None for an account-wide one. The entity base reads it to
        #: skip re-rendering every other device — see `_async_notify_devices`.
        self.devices_changed: frozenset[str] | None = None
        #: Set by `async_setup_entry` just before the platforms build entities from each
        #: valve's layout. A layout found to differ after that needs a reload.
        self.entities_built = False
        self._topology_reload_scheduled = False

        # Every Anthem valve on the account, in the order the cloud lists them, plus the
        # same objects keyed by device id for envelope routing. Empty on a controller-only
        # account. See `Valve`.
        self.valves: list[Valve] = []
        self._valves_by_id: dict[str, Valve] = {}
        # Every Anthem Plus controller on the account, in the order the cloud lists them,
        # plus the same objects keyed by device id for envelope routing. Empty on a
        # valve-only account. See `Controller` for what each one carries.
        self.controllers: list[Controller] = []
        self._controllers_by_id: dict[str, Controller] = {}
        # Every other product's devices (`products.py`), each with its own coordinator, by
        # product key in `PRODUCTS` order; and the same keyed by lowercase device id, since
        # their messages are routed case-insensitively.
        self.products: dict[str, list[ProductDevice]] = {}
        self._product_devices: dict[str, ProductDevice] = {}
        # What they keep across restarts (see each one's `stored`): one file per entry and
        # product, keyed by device id.
        self._product_stores: dict[str, Store[dict[str, Any]]] = {}
        self._product_data: dict[str, dict[str, Any]] = {}
        # Their first reads, started early in `async_setup` so they overlap the showers'
        # seed, and collected at its end. Held so a failed setup can cancel them.
        self._products_first_refresh: asyncio.Task | None = None
        # When those reads finished (monotonic) — what decides whether the first connect
        # reads them again; see `_products_just_read`.
        self._products_read_at: float | None = None
        self.stream: KonnectMqttStream | None = None
        # The stream's registration and first connection attempt, started first thing in
        # `async_setup` and collected at its end. Held so a failed setup can cancel it.
        self._stream_start: asyncio.Task | None = None
        #: How many times the stream has connected. Each live-state read records the count
        #: it was sent under (`Valve.state_read_on`, `Controller.state_read_on`), which is
        #: how the first connect tells the reads it covers from the ones it does not.
        self.stream_connects = 0
        # Set once the setup's seed has finished. A connect before then is caught up on by
        # `async_setup` itself, so the two reads never run over the same state at once.
        self._setup_seed_done = False
        # When setup's seed went out, and when the stream first connected (monotonic); see
        # `_first_connect_late`.
        self._seed_started_at: float | None = None
        self._first_connect_at: float | None = None
        # Messages that arrived before the account read said which devices exist, handed on
        # once it has; None from then on. See `_handle_envelope`.
        self._early_envelopes: deque[Envelope] | None = deque(
            maxlen=_EARLY_MESSAGES_KEPT
        )
        self.raw_log: RawMqttLog | None = None
        # The capture behind the "Report Log" switch: one file per switch-on, appended
        # across restarts. See `konnect/report_log.py`.
        self.report_log: ReportLog | None = None
        #: The reseed spawned on an MQTT connect (on the first, only the live-state
        #: catch-up). Held so `async_shutdown_stream` can cancel it; see `_handle_connected`.
        self._reseed_task: asyncio.Task | None = None
        # A connect came while that reseed was running, so it reads everything again.
        self._reseed_again = False
        # The twice-daily firmware check (nothing pushes "an update is available") and the
        # fault reads that ride on it. `_firmware_task` holds the one running, so unload
        # can cancel it.
        self._firmware_unsub: Any = None
        self._firmware_task: asyncio.Task | None = None
        # Local midnight tick: re-renders the date-sensitive usage entities, no network.
        self._midnight_unsub: Any = None
        # One-shot: lets `_async_update_data` skip the first refresh that follows setup's
        # seed.
        self._seeded_during_setup = False
        # The warmup journal, built in `async_setup` once `hass.config.path` is usable.
        self.warmup_log: DebugJournal | None = None
        # Rolling record of recent messages, so a warmup disable can be journalled
        # with what surrounded it. Bounded by count and trimmed by age on read.
        self._recent_messages: deque = deque(maxlen=WARMUP_CONTEXT_MAX_MESSAGES)

    @property
    def zone_grouping(self) -> str:
        """How multi-zone valve outlets, controls, and sensors are grouped and named."""
        mode = self.entry.options.get(CONF_ZONE_GROUPING, DEFAULT_ZONE_GROUPING)
        return mode if mode in ZONE_GROUPING_MODES else DEFAULT_ZONE_GROUPING

    # ------------------------------------------------------------------ #
    # Setup / teardown
    # ------------------------------------------------------------------ #
    async def async_setup(self) -> None:
        """Start the MQTT stream, discover devices, and seed their state from REST.

        The stream starts as soon as sign-in works and connects while the reads run, since
        it needs nothing they produce; its first connect re-reads only what setup read
        before it was up (`_handle_connected`). Home Assistant stopping part-way ends setup:
        each step that starts something checks `_raise_if_closed` first.
        """
        # One identity for the life of this config entry, persisted, so restarts and
        # reconnects do not leave a trail of dead registrations on the Kohler account.
        mobile_device_id = self.entry.data.get(CONF_MOBILE_DEVICE_ID)
        first_registration = not mobile_device_id
        if first_registration:
            mobile_device_id = uuid.uuid4().hex[:16]
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_MOBILE_DEVICE_ID: mobile_device_id},
            )

        # The captures and the warm-up journal come before the stream, which can write to
        # them from its first message. Local files only, so no round trip.
        #
        # Raw MQTT log: built unconditionally and switched on at runtime, so capture can
        # start from the UI without a reload. See `konnect/raw_log.py`.
        self.raw_log = RawMqttLog(
            self.hass.config.path(RAW_MQTT_LOG_DIR),
            forced=ENABLE_RAW_MQTT_LOG,
            max_bytes=RAW_MQTT_LOG_MAX_BYTES,
            keep_files=RAW_MQTT_LOG_KEEP_FILES,
        )
        # Open the file now when capture is already on, so it exists before the next push
        # (possibly hours away). In the executor: it creates a directory and opens a file.
        await self.hass.async_add_executor_job(self.raw_log.prepare)

        # Report log, in the integration's own folder. The options key names the active
        # file; if present, the switch was on when Home Assistant stopped, so re-attach to
        # the same file and keep the capture across the restart.
        self.report_log = ReportLog(
            os.path.join(os.path.dirname(__file__), REPORT_LOG_DIR_NAME),
            max_bytes=REPORT_LOG_MAX_BYTES,
        )
        episode = self.entry.options.get(CONF_REPORT_LOG_FILE)
        if episode:
            resumed = await self.hass.async_add_executor_job(
                self.report_log.resume, episode
            )
            # A refused name means a new episode started: persist it, or every restart
            # would start another one.
            if resumed != episode:
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    options={**self.entry.options, CONF_REPORT_LOG_FILE: resumed},
                )

        # Warmup journal: beside the raw capture and on the same clock, so the two
        # interleave (`WARMUP_README`). Its baseline record waits for the seed.
        self.warmup_log = DebugJournal(
            self.hass.config.path(RAW_MQTT_LOG_DIR),
            forced=ENABLE_WARMUP_DEBUG_LOG,
            keep_files=WARMUP_DEBUG_LOG_KEEP_FILES,
            prefix="warmup",
            readme=WARMUP_README,
            readme_fields={
                "before": int(WARMUP_CONTEXT_BEFORE_SECONDS),
                "after": int(WARMUP_CONTEXT_AFTER_SECONDS),
            },
            label="Warmup journal",
        )
        await self.hass.async_add_executor_job(self.warmup_log.prepare)

        try:
            # Sign in first: everything after waits for the token anyway, and a failed
            # sign-in then fails setup before the stream has started.
            await self.auth.async_get_access_token()
        except AuthUnavailable as err:
            raise ConfigEntryNotReady(f"Cannot reach Kohler: {err}") from err
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        self._raise_if_closed()

        self.stream = KonnectMqttStream(
            self.client,
            self._handle_envelope,
            on_connect=self._handle_connected,
            on_disconnect=self._handle_disconnected,
            on_auth_error=self._handle_auth_error,
            mobile_device_id=mobile_device_id,
            raw_log=self.raw_log,
            report_log=self.report_log,
            # Only a brand-new identity can plausibly need provisioning time. A reused one
            # has connected before, so silence from it is real silence.
            expect_warmup=first_registration,
        )
        # Started, not awaited: registering needs only the client and the identity, so it
        # overlaps the account read instead of waiting for it. Collected below.
        self._stream_start = self.hass.async_create_task(
            self._async_start_stream(self.stream)
        )

        try:
            customer = await self.client.async_get_customer()
        except AuthUnavailable as err:
            # Kohler unreachable, not a bad credential: retry setup, no reauth.
            raise ConfigEntryNotReady(f"Cannot reach Kohler: {err}") from err
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KohlerError as err:
            raise ConfigEntryNotReady(f"Cannot reach Kohler: {err}") from err
        self._raise_if_closed()

        self.temperature_unit = customer.temperature_unit or self.temperature_unit
        self.water_units = customer.water_units or self.water_units
        valves = customer.gcs_devices
        controllers = customer.hub_devices
        owned = {
            product: devices
            for product in PRODUCTS
            if (devices := customer.product_devices(product.kind))
        }
        if not valves and not controllers and not owned:
            raise ConfigEntryNotReady("No supported Kohler devices on this account")

        # Each valve starts from its own layout if one was read earlier this run, else the
        # entry's; see `_known_model`.
        names = valve_names(valves)
        self.valves = [
            Valve(
                self,
                device,
                self._known_model(device.device_id),
                names[device.device_id],
                tag=device.device_id if len(valves) > 1 else None,
            )
            for device in valves
        ]
        self._valves_by_id = {v.device_id: v for v in self.valves}
        if len(self.valves) > 1:
            _LOGGER.info(
                "Account has %d Anthem valves: %s",
                len(self.valves),
                ", ".join(valve.name for valve in self.valves),
            )
        # Each controller starts from the layout it reported earlier this run, else the
        # entry's; `_async_seed_state` replaces it with what its configuration says.
        names = controller_names(controllers)
        self.controllers = [
            Controller(
                device,
                HubDevice(self.client, device.device_id, self.temperature_unit),
                HubState(self._known_model(device.device_id)),
                names[device.device_id],
            )
            for device in controllers
        ]
        self._controllers_by_id = {c.device_id: c for c in self.controllers}
        if len(self.controllers) > 1:
            _LOGGER.info(
                "Account has %d Anthem Plus controllers: %s",
                len(self.controllers),
                ", ".join(controller.name for controller in self.controllers),
            )
        # Every other product's devices, each with its own coordinator. They keep their own
        # clocks, so they are not seeded with the showers: each takes its own first read.
        for product, devices in owned.items():
            names = product_names(devices, product.default_name)
            built = [
                product.create(self, device, names[device.device_id])
                for device in devices
            ]
            self.products[product.key] = built
            data = dict(await self._product_store(product).async_load() or {})
            self._product_data[product.key] = data
            for device in built:
                device.load(data.get(device.device_id) or {})
        self._product_devices = {
            device.device_id.lower(): device
            for devices in self.products.values()
            for device in devices
        }
        self._raise_if_closed()
        # The devices are known: hand on what the stream delivered before, after each
        # product device's stored state is back (which would otherwise overwrite it).
        early, self._early_envelopes = self._early_envelopes, None
        for envelope in early or ():
            self._handle_envelope(envelope)
        if self._product_devices:
            # Started now and collected at the end of this method, so each device's first
            # read (several round trips) overlaps the showers' seed. It cannot fail the
            # entry: a device Kohler cannot answer for shows unavailable and retries on its
            # own clock.
            self._products_first_refresh = self.hass.async_create_task(
                self._async_first_refresh_products()
            )

        # Outlet-setting warnings from a previous run are stale: nothing set from here is
        # pending now.
        for valve in self.valves:
            valve.forget_outlet_write_issues()
        self._seed_started_at = time.monotonic()
        try:
            await self._async_seed_state()
        except AuthUnavailable as err:
            # Same split as the customer read. The seed swallows `KohlerError` per read,
            # but the token layer raises `AuthError`, which is not one.
            raise ConfigEntryNotReady(f"Cannot reach Kohler: {err}") from err
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        self._raise_if_closed()
        # The mode the seed found, before anything read since can be journalled over it.
        for valve in self.valves:
            valve.journal_baseline()
        # `async_config_entry_first_refresh()` follows in `async_setup_entry` and would
        # repeat every read above; `_async_update_data` spends this to skip it.
        self._seeded_during_setup = True
        # A connect during the seed left its catch-up to this point, so the two never read
        # into the same state at once; see `_handle_connected`.
        self._setup_seed_done = True
        if self.stream_connects:
            self._reseed_task = self.hass.async_create_task(
                self._async_reseed_after_connect()
            )
        self._persist_refresh_token()

        # Waited for rather than awaited: an unload cancels it, which is not this setup
        # being cancelled — `_raise_if_closed` says what happened instead.
        start = self._stream_start
        if start is not None:
            await asyncio.wait({start})
            self._stream_start = None
        self._raise_if_closed()
        if start is not None:
            start.result()

        for valve in self.valves:
            # Arms trigger B (`cloud_watch.py`): nothing is asked of Kohler until the valve
            # has been quiet for the full interval, and any valve message resets it.
            valve.cloud_watch.async_start()

        # Firmware: once now, in the background, then every `FIRMWARE_CHECK_INTERVAL`;
        # nothing announces a release.
        self._firmware_task = self.hass.async_create_task(self.async_refresh_firmware())
        self._firmware_unsub = async_track_time_interval(
            self.hass, self._async_firmware_tick, FIRMWARE_CHECK_INTERVAL
        )
        self._midnight_unsub = async_track_time_change(
            self.hass, self._async_midnight_tick, hour=0, minute=0, second=1
        )

        # The product devices' first reads, started before the seed. Collected here because
        # the platforms build their entities from them as soon as this returns.
        if (refresh := self._products_first_refresh) is not None:
            await asyncio.wait({refresh})
            self._products_first_refresh = None
            self._raise_if_closed()
            refresh.result()

    async def _async_start_stream(self, stream: KonnectMqttStream) -> None:
        """Register and start connecting — the task `async_setup` starts first.

        A failure is a warning rather than a setup failure: the stream retries on its own
        backoff, and the first connect that succeeds catches up on what setup read before
        it. A credential the registration found dead still asks to sign in again.
        """
        try:
            await stream.async_start()
        except (AuthError, KohlerError) as err:
            if self.closed:
                return
            _LOGGER.warning("Kohler MQTT stream did not start: %s", err)
            if credential_is_dead(err):
                self._handle_auth_error(err)

    def _raise_if_closed(self) -> None:
        """Abandon setup once `async_shutdown_stream` has run.

        During `async_setup` that means Home Assistant is stopping, and anything started
        after this point would run on with nothing left to stop it. Raises
        `ConfigEntryNotReady` rather than an error: nothing is wrong with the account.
        """
        if self.closed:
            raise ConfigEntryNotReady("Home Assistant is stopping")

    async def _async_first_refresh_products(self) -> None:
        """Each product device's first read, all at once; see where `async_setup` starts it."""
        await asyncio.gather(
            *(device.async_refresh() for device in self._product_devices.values())
        )
        self._products_read_at = time.monotonic()

    @callback
    def _first_connect_late(self) -> bool:
        """Whether the stream's first connect came long after setup's reads went out.

        A first start that failed connects on the back-off, possibly minutes later, so the
        outlet settings and presets setup read may have changed unannounced. Each device is
        then read whole, not just its live state: a stale scald limit in the cache would be
        echoed by the next outlet write.
        """
        seeded, connected = self._seed_started_at, self._first_connect_at
        return (
            seeded is None
            or connected is None
            or connected - seeded > _FIRST_CONNECT_GRACE_SECONDS
        )

    def _products_just_read(self, product: Product) -> bool:
        """Whether the first reads are still running, or finished only just now.

        If so, the stream's first connect does not read ``product``'s devices again
        (`_handle_connected`). "Just now" is the product's `fresh_for`.
        """
        if self._products_first_refresh is not None:
            return True
        read_at = self._products_read_at
        return (
            read_at is not None
            and time.monotonic() - read_at < product.fresh_for.total_seconds()
        )

    @callback
    def _known_model(self, device_id: str) -> ValveModel:
        """The layout a device starts from: its own, if read earlier this run, else the entry's.

        Recorded by `_async_note_topology`, so a device whose layout read fails after a
        topology reload does not fall back to the layout that caused the reload.
        """
        known = self.hass.data.get(_VALVE_TOPOLOGY, {}).get(self.entry.entry_id, {})
        split = known.get(device_id)
        return self.model if split is None else model_for_topology(*split)

    @callback
    def _async_note_topology(
        self, device: Valve | Controller, *, changed: bool
    ) -> None:
        """Remember a device's own layout, and reload if its entities were built from another.

        The layout read is retried until it answers, so it can arrive after the entities
        were built from the entry's layout. Once the platforms have loaded, a changed
        layout reloads the entry, at most once per coordinator. The layout is kept in
        `hass.data`, which outlives the reload, so the next coordinator starts from it
        (`_known_model`) and only a genuinely different answer reloads again.
        """
        model = device.model
        known = self.hass.data.setdefault(_VALVE_TOPOLOGY, {}).setdefault(
            self.entry.entry_id, {}
        )
        known[device.device_id] = (model.outlets_valve1, model.outlets_valve2)
        if (
            not changed
            or not self.entities_built
            or self.closed
            or self._topology_reload_scheduled
        ):
            return
        self._topology_reload_scheduled = True
        # The device's name, not its id, for the reason given in `Valve._apply_topology`.
        _LOGGER.info(
            "%s reports a different outlet layout (%s) from the one its entities were "
            "built with; reloading Kohler Konnect so they match",
            device.name,
            model.sku,
        )
        self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

    @callback
    def _async_midnight_tick(self, _now: Any) -> None:
        """Re-render date-sensitive usage entities at local midnight with no API call."""
        self.async_refresh_entities()

    @contextmanager
    def command_errors(self, offline: str) -> Iterator[None]:
        """Turn a shower command's failures into what the person who sent it sees.

        Shared by every valve and controller command. Catches `AuthError` (not a
        `KohlerError`) too, so a revoked sign-in prompts reauth rather than a traceback.
        ``offline`` is the message for a device Kohler reports offline (`statusCode 900`).
        """
        try:
            yield
        except DeviceOffline as err:
            raise HomeAssistantError(offline) from err
        except AuthError as err:
            if credential_is_dead(err):
                self._handle_auth_error(err)
                raise HomeAssistantError(
                    "Kohler rejected the saved sign-in. Sign in again from Home "
                    "Assistant's notifications, then try again."
                ) from err
            raise HomeAssistantError(f"Could not reach Kohler: {err}") from err
        except KohlerError as err:
            raise HomeAssistantError(f"Kohler command failed: {err}") from err

    @callback
    def _handle_auth_error(self, err: Exception) -> None:
        """Surface a rejected credential as a reauth prompt.

        With no polling, `_async_update_data` (which raises ``ConfigEntryAuthFailed``) runs
        only on a manual refresh, so without this a dead refresh token would leave the entry
        looking healthy while the reconnect loop retries forever. `async_start_reauth` is
        idempotent and the stream latches, so repeated failures do not stack flows.

        Ignored once this coordinator is closed: a command in flight across a reload can
        find its token already redeemed by the new coordinator, which is no reason to ask
        the owner to sign in.
        """
        if self.closed:
            _LOGGER.debug("Ignoring a sign-in failure after unload: %s", err)
            return
        _LOGGER.error(
            "Kohler rejected the stored credential (%s); reauthentication required", err
        )
        self.entry.async_start_reauth(self.hass)

    @callback
    def _handle_connected(self) -> None:
        """Catch up on what the stream could not report before it connected.

        This replaces polling: the broker sends no state on connect, so a reconnect
        re-seeds everything. The first connect does not: the stream starts before setup's
        reads, so only a device whose live-state read went out before it was up
        (`state_read_on`) has its `gcs-state` or `hub-state` read again, unless the connect
        came late (`_first_connect_late`). While setup's own seed is still reading nothing
        starts here, so two reads never race into the same state; `async_setup` starts the
        catch-up once its seed is done.
        """
        if self.closed:
            # Delivered while the stream was stopping; a reseed now would outlive the unload.
            return
        self.stream_connects += 1
        first = self.stream_connects == 1
        if first:
            self._first_connect_at = time.monotonic()
        # Durations across a disconnect are meaningless, so the zone clocks restart from the
        # next report. A first connect long after setup's reads (its first start failed) is
        # a gap as much as a reconnect is.
        seeded = self._seed_started_at
        gap = not first or (
            seeded is not None
            and time.monotonic() - seeded > _FIRST_CONNECT_GRACE_SECONDS
        )
        for valve in self.valves:
            valve.forget_timings()
            if gap:
                valve.note_stream_gap()
        # Nothing is replayed on connect for the other products either: each re-reads its
        # state — except on the first connect, when its first read is under way or only
        # just done.
        for product in PRODUCTS:
            just_read = first and self._products_just_read(product)
            for device in self.products.get(product.key, ()):
                # One device's fault must not cost the showers their catch-up below.
                try:
                    device.async_stream_connected(just_read=just_read)
                except Exception:
                    _LOGGER.exception("%s failed to catch up", device.device_name)
        # The MQTT Connection sensors: nothing else tells them before a reseed ends, and the
        # first connect may not reseed at all.
        self.async_refresh_entities()
        if not self._setup_seed_done:
            return
        # Held, so an unload can cancel it: a reseed can be a dozen round trips and ends in
        # an entry write and an entity push.
        if self._reseed_task is not None and not self._reseed_task.done():
            # A connect during a running reseed: no rival (it would race over the same
            # state), but what that reseed read before this gap cannot vouch for it, so it
            # reads everything again once through.
            self._reseed_again = True
            return
        self._reseed_task = self.hass.async_create_task(
            self._async_reseed_after_connect()
        )

    @callback
    def _handle_disconnected(self) -> None:
        """A dropped stream: say so, and let the product devices catch up at their pace.

        Re-renders every entity so the MQTT Connection sensors show the drop, without the
        stream claiming a successful update.
        """
        if self.closed:
            return
        for device in self._product_devices.values():
            try:
                device.async_stream_disconnected()
            except Exception:
                _LOGGER.exception("%s failed to note the drop", device.device_name)
        self.async_update_listeners()

    @property
    def faucets(self) -> list[FaucetCoordinator]:
        """Every Sensate and Setra faucet on the account."""
        return self.products.get(FAUCETS.key, [])  # type: ignore[return-value]

    def _product_store(self, product: Product) -> Store[dict[str, Any]]:
        """The file this entry keeps ``product``'s devices' state in."""
        store = self._product_stores.get(product.key)
        if store is None:
            store = self._product_stores[product.key] = Store(
                self.hass,
                product.storage_version,
                product.storage_key.format(entry_id=self.entry.entry_id),
            )
        return store

    async def async_save_device_state(self, device: ProductDevice) -> None:
        """Persist what one product device keeps across restarts (its `stored`)."""
        for product in PRODUCTS:
            if device in self.products.get(product.key, ()):
                data = self._product_data.setdefault(product.key, {})
                data[device.device_id] = device.stored()
                await self._product_store(product).async_save(data)
                return

    async def _async_reseed_after_connect(self) -> None:
        """Read what the stream's connect cannot vouch for; see `_handle_connected`.

        Again, whole, for each connect that came while it read.
        """
        self._reseed_again = False
        while True:
            try:
                if self.stream_connects == 1 and not self._first_connect_late():
                    await self._async_read_missed_live_state()
                else:
                    await self._async_seed_state()
            except (AuthError, KohlerError) as err:
                # The stream is up regardless. Do not fail the entry or retry here; the
                # next connect tries again, unless one already has.
                _LOGGER.warning("Kohler re-seed after MQTT connect failed: %s", err)
                if credential_is_dead(err):
                    # The one failure the next connect cannot fix.
                    self._handle_auth_error(err)
                    return
                if not self._reseed_again:
                    return
            else:
                if not self._reseed_again:
                    break
            self._reseed_again = False
        self._persist_refresh_token()
        self.async_set_updated_data(self._snapshot())

    async def async_shutdown_stream(self) -> None:
        """Stop the MQTT stream and everything this coordinator started. Idempotent.

        Runs on unload, when a setup fails part-way, and when Home Assistant stops.
        """
        if self.closed:
            return
        # ⚠️ Set before anything awaits: paho can still deliver while the stream stops, and
        # every way in from the stream, `Valve._track` and `async_setup` check this flag.
        self.closed = True
        # The reseed before the stream: it writes to the entry and pushes into entities.
        if self._reseed_task is not None:
            self._reseed_task.cancel()
            self._reseed_task = None
        if self._firmware_unsub is not None:
            self._firmware_unsub()
            self._firmware_unsub = None
        if self._midnight_unsub is not None:
            self._midnight_unsub()
            self._midnight_unsub = None
        # The check in flight, whether setup's or the twice-daily tick's.
        if self._firmware_task is not None:
            self._firmware_task.cancel()
            self._firmware_task = None
        if self._products_first_refresh is not None:
            self._products_first_refresh.cancel()
            self._products_first_refresh = None
        # A registration still under way from setup; the stream's own stop covers the rest.
        if self._stream_start is not None:
            self._stream_start.cancel()
            self._stream_start = None
        # The stream before the valves, so nothing it delivers while stopping can hand them
        # work after `stop()`.
        if self.stream is not None:
            await self.stream.async_stop()
            self.stream = None
        else:
            # The stream closes the captures on its way out; without one (setup failed
            # before it existed) close them here. Blocking close, off the loop.
            for capture in (self.raw_log, self.report_log):
                if capture is not None:
                    await self.hass.async_add_executor_job(capture.close)
        # And a reseed that slipped in the same way, before the flag reached the handler.
        if self._reseed_task is not None:
            self._reseed_task.cancel()
            self._reseed_task = None
        for valve in self.valves:
            valve.stop()
        # The warmup journal is not the stream's to close. Blocking close, off the loop.
        if self.warmup_log is not None:
            await self.hass.async_add_executor_job(self.warmup_log.close)
        # ⚠️ Last, let a sign-in refresh in flight finish (bounded by
        # `_REFRESH_WAIT_SECONDS`): a reload builds the next coordinator from the entry as
        # it stands when this returns, and it must hold the rotated token, not the one this
        # refresh just spent. Its failure is its own caller's to report.
        refresh = self.auth._refresh_task
        if refresh is not None and not refresh.done():
            with suppress(Exception):
                async with asyncio.timeout(_REFRESH_WAIT_SECONDS):
                    await asyncio.shield(refresh)

    # ------------------------------------------------------------------ #
    # Push
    # ------------------------------------------------------------------ #
    def _handle_envelope(self, envelope: Envelope) -> None:
        """Apply an MQTT message and notify the entities of the device it was for."""
        if self.closed:
            # Delivered while the stream was stopping; see `async_shutdown_stream`.
            return
        if self._early_envelopes is not None:
            # The stream starts before the account read, so messages can arrive before any
            # device exists. Held, not dropped: an event such as a faucet's leak alert is
            # not something a later read brings back.
            self._early_envelopes.append(envelope)
            return
        changed: set[str] = set()
        # The one account-level stream carries every device's messages; the device id says
        # whose each is. A device added in the app since setup is ignored until a reload.
        valve = self._valves_by_id.get(envelope.device_id)
        if valve is not None:
            if valve.handle_envelope(envelope):
                changed.add(valve.device_id)
            self._remember_message(envelope)
        controller = self._controllers_by_id.get(envelope.device_id)
        if controller is not None:
            if controller.state.apply_envelope(envelope):
                changed.add(controller.device_id)
                if envelope.code in _HUB_PUSH_FIELDS:
                    controller.push_serials[envelope.code] += 1
            self._remember_message(envelope)
            # Trigger A (`cloud_watch.py`). Every valve's watch hears every controller:
            # which controller fronts which valve is not knowable from the cloud, and a
            # spurious trigger costs one rate-limited read.
            for valve in self.valves:
                valve.cloud_watch.note_hub_envelope(envelope)
        # Any other product's message goes to that device's own coordinator.
        device = self._product_devices.get(envelope.device_id.lower())
        if device is not None:
            # Not remembered: that window is the valves' warm-up evidence.
            device.async_handle_envelope(envelope)
        if changed:
            self._async_notify_devices(changed)

    @callback
    def _async_notify_devices(self, device_ids: set[str]) -> None:
        """Notify listeners of a change to these devices only.

        One coordinator serves the whole account, so a plain notification re-renders every
        entity on it. For the length of this one, `devices_changed` names the devices
        concerned and the entity base (`entity._KohlerDeviceEntity`) skips entities of any
        other; non-entity listeners are still called. After a failed refresh everything is
        re-rendered, since every entity was marked unavailable and is available again now.
        """
        recovering = not self.last_update_success
        self.devices_changed = None if recovering else frozenset(device_ids)
        try:
            self.async_set_updated_data(self._snapshot())
        finally:
            self.devices_changed = None

    def _snapshot(self) -> dict[str, Any]:
        """A cheap dict so DataUpdateCoordinator has something to hand entities.

        Entities read the state objects directly; this only carries freshness markers.
        """
        return {
            "gcs_last_update": {
                v.device_id: v.gcs_state.last_update for v in self.valves
            },
            "hub_last_update": {
                c.device_id: c.state.last_update for c in self.controllers
            },
            "mqtt_connected": bool(self.stream and self.stream.connected),
            # So a cloud check result re-renders like a push; the value lives on the watch.
            "cloud_connected": {
                v.device_id: v.cloud_watch.connected for v in self.valves
            },
        }

    @callback
    def async_refresh_entities(self) -> None:
        """Re-render entities from what is already in memory, with no network read.

        For state that changes without a message: a cloud reachability check, a usage or
        firmware read, the day turning over at midnight.
        """
        self.async_set_updated_data(self._snapshot())

    # ------------------------------------------------------------------ #
    # Poll
    # ------------------------------------------------------------------ #
    async def _async_update_data(self) -> dict[str, Any]:
        if self._seeded_during_setup:
            # The first refresh, right after `async_setup`, only populates
            # `coordinator.data` for the platforms: setup has just read everything, and what
            # changes after is reported by the stream or re-read on its first connect
            # (`_handle_connected`). Only the first: a manual `update_entity`, the only
            # other way in, must read for real.
            self._seeded_during_setup = False
            self._persist_refresh_token()
            return self._snapshot()
        try:
            await asyncio.gather(
                self._async_seed_state(),
                *(
                    # A monthly series never yet read is retried by the seed; not twice.
                    valve.async_refresh_daily_usage(
                        monthly=valve._usage_seeded_month is not None
                    )
                    for valve in self.valves
                ),
            )
        except AuthUnavailable as err:
            raise UpdateFailed(f"Kohler auth service unreachable: {err}") from err
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KohlerError as err:
            raise UpdateFailed(f"Kohler poll failed: {err}") from err
        self._persist_refresh_token()
        return self._snapshot()

    async def _async_seed_state(self) -> None:
        """Read current state over REST into the state objects.

        Runs at setup, on every MQTT reconnect, and on a manual `update_entity`. Failures for
        one device do not blank another.
        """
        # In parallel: devices are independent, and the ordering inside one device
        # (settings or configuration before state) stays within its coroutine. Each valve's
        # fault log depends on nothing the seed reads, so it runs beside it.
        await self._async_read_devices(
            *(valve.async_seed() for valve in self.valves),
            *(valve.async_refresh_faults() for valve in self.valves),
            *(
                self._async_seed_controller(controller)
                for controller in self.controllers
            ),
        )

    async def _async_read_missed_live_state(self) -> None:
        """Read the live state the stream's first connect cannot vouch for.

        For each device whose `gcs-state` or `hub-state` read went out before the stream was
        up, or has not succeeded, only that read: the rest does not move in the seconds
        between setup and the first connect. A device whose setup left something unread
        (`Valve.seed_incomplete`, a controller's unknown capabilities) gets its whole seed,
        since the next reconnect may be days away. See `_handle_connected`.
        """
        connection = self.stream_connects
        reads: list[Coroutine[Any, Any, None]] = []
        for valve in self.valves:
            if valve.seed_incomplete:
                reads.append(valve.async_seed())
            elif valve.state_read_on != connection:
                reads.append(valve.async_read_live_state())
        for controller in self.controllers:
            if not controller.capabilities.known:
                reads.append(self._async_seed_controller(controller))
            elif controller.state_read_on != connection:
                reads.append(self._async_read_hub_state(controller))
        await self._async_read_devices(*reads)

    async def _async_read_devices(self, *reads: Coroutine[Any, Any, None]) -> None:
        """Run per-device reads side by side, and report how they went.

        One device's failure does not cancel or blank the others. Each read catches
        `KohlerError` itself, so anything else is logged, except a sign-in failure
        (`AuthError`, not a `KohlerError`), which is raised once every read has finished so
        the caller can prompt reauth.
        """
        results = await asyncio.gather(*reads, return_exceptions=True)
        auth_error: AuthError | None = None
        for result in results:
            if isinstance(result, AuthError):
                auth_error = auth_error or result
            elif isinstance(result, Exception):
                _LOGGER.warning("Seeding a Kohler device failed: %s", result)
        if auth_error is not None:
            raise auth_error

    async def _async_seed_controller(self, controller: Controller) -> None:
        """Seed one controller: configuration then state, with the other reads alongside.

        The experience catalogue, active faults and favorites depend on neither. Each read
        is guarded on its own, and all finish before any failure is raised.
        """
        device_id = controller.device_id

        async def configuration_then_state() -> None:
            # Before the state: it says how many outlets each zone has, which decides how
            # the state's zone arrays decode (`_apply_controller_topology`). Installation
            # facts (zones, outlet types, parts) are latched on the first answer; the
            # settings it carries, Max Shower Duration among them, change at runtime, so it
            # is read on every seed.
            try:
                config = await self.client.async_get_hub_configuration(device_id)
                configuration = config.get("configuration") or {}
                if not controller.capabilities.known:
                    controller.capabilities = HubCapabilities.from_configuration(
                        configuration
                    )
                    self._apply_controller_topology(controller, configuration)
                controller.settings = HubSettings.from_configuration(configuration)
                self._async_link_controller_page(controller)
            except KohlerError as err:
                _LOGGER.debug(
                    "Could not read HUB configuration for %s: %s", device_id, err
                )
            await self._async_read_hub_state(controller)

        async def experiences() -> None:
            # The firmware's fixed catalogue, read once. A failure leaves the Experience
            # select empty.
            if controller.experiences:
                return
            try:
                payload = await self.client.async_get_hub_experiences(device_id)
                controller.experiences = _experiences_by_category(payload)
            except KohlerError as err:
                _LOGGER.debug(
                    "Could not read HUB experiences for %s: %s", device_id, err
                )

        async def saved_favorites() -> None:
            try:
                payload = await self.client.async_get_hub_favorites(device_id)
                favorites = payload.get("favorites")
                if isinstance(favorites, list):
                    # Favorite ids are reassigned on delete: resolve by name from this list.
                    controller.favorites = favorites
            except KohlerError as err:
                if getattr(err, "status", None) == 404:
                    # Not a failure: the endpoint answers 404 when no favorites are
                    # saved, not an empty list (`docs/protocol/hub_controller.md` §2).
                    controller.favorites = []
                    _LOGGER.debug("No HUB favorites are saved on %s", device_id)
                else:
                    _LOGGER.debug(
                        "Could not read HUB favorites for %s: %s", device_id, err
                    )

        _raise_first(
            await asyncio.gather(
                configuration_then_state(),
                experiences(),
                self._async_refresh_active_errors(controller),
                saved_favorites(),
                return_exceptions=True,
            )
        )

    async def _async_read_hub_state(self, controller: Controller) -> None:
        """Read `hub-state` and apply what no message has overtaken.

        The controller's counterpart of `Valve.apply_rest_reply`: what a message carried
        while the read was in flight (`_HUB_PUSH_FIELDS`), and only that, is kept over the
        reply. A `KohlerError` is logged and changes nothing.
        """
        serials = Counter(controller.push_serials)
        connection = self.stream_connects
        try:
            payload = await self.client.async_get_hub_state(controller.device_id)
        except KohlerError as err:
            _LOGGER.debug(
                "Could not seed HUB state for %s: %s", controller.device_id, err
            )
            return
        moved = [
            code
            for code in _HUB_PUSH_FIELDS
            if controller.push_serials[code] != serials[code]
        ]
        if moved:
            _LOGGER.debug(
                "Keeping what %s reported in %s over a hub-state reply that is older",
                controller.name,
                ", ".join(moved),
            )
        fields = [name for code in moved for name in _HUB_PUSH_FIELDS[code][0]]
        components = [key for code in moved for key in _HUB_PUSH_FIELDS[code][1]]
        with _keeping(controller.state, fields, components):
            controller.state.apply_rest_state(payload)
        controller.state_read_on = connection

    async def _async_refresh_active_errors(self, controller: Controller) -> None:
        """Read the controller's active faults, for its Problem sensor.

        A failed read changes nothing: the client's `{}` is not "no faults", so only an
        `errorDetails` list (empty when nothing is active) is an answer. A sign-in failure
        is raised for the caller to report.
        """
        payload = await self.client.async_get_hub_active_errors(controller.device_id)
        details = payload.get("errorDetails") if isinstance(payload, dict) else None
        if not isinstance(details, list):
            _LOGGER.debug(
                "No active-error list for %s; keeping what was known", controller.name
            )
            return
        controller.active_errors = [
            entry
            for entry in details
            if isinstance(entry, dict) and str(entry.get("errorCode") or "0") != "0"
        ]

    async def async_clear_hub_fault_log(self, controller: Controller) -> None:
        """Clear a controller's fault log: ``DELETE hub-diagnostics/{id}``.

        `Valve.async_clear_fault_log` for an Anthem Plus. Untried on hardware
        (`docs/protocol/hub_controller.md` §2), so a refusal is passed on as it comes. The
        active errors are read again afterwards.
        """
        with self.command_errors(_controller_offline(controller)):
            await self.client.async_clear_hub_diagnostics(controller.device_id)
        controller.active_errors = []
        try:
            await self._async_refresh_active_errors(controller)
        except AuthError as err:
            _LOGGER.debug("Could not read %s's active errors: %s", controller.name, err)
            if credential_is_dead(err):
                self._handle_auth_error(err)
        self.async_refresh_entities()

    @callback
    def _async_link_controller_page(self, controller: Controller) -> None:
        """Point the controller's device page at its web settings page.

        `DeviceInfo` sets it at creation; this keeps it current when the address changes.
        Cosmetic, so registry trouble is logged rather than failing the seed.
        """
        url = controller.settings.web_url
        try:
            registry = dr.async_get(self.hass)
            device = device_by_identifier(
                registry, (DOMAIN, controller.device_id), self.entry.entry_id
            )
            if device is not None and device.configuration_url != url:
                registry.async_update_device(device.id, configuration_url=url)
        except Exception as err:  # A link is not worth failing a seed over.
            _LOGGER.debug("Could not update the controller's web page link: %s", err)

    @callback
    def _apply_controller_topology(
        self, controller: Controller, configuration: dict[str, Any]
    ) -> None:
        """Give a controller the outlet layout its own configuration reports.

        The entry's model came from the config flow (the valve, else the first controller)
        and need not fit a second controller in another bathroom. So each controller
        decodes its zone arrays with the split its own `hub-configuration` states, keeping
        the entry's model only when the read yields nothing. Runs once
        (`capabilities.known`), normally in the setup seed before the platforms build their
        outlet entities; a read that answers only later goes through
        `_async_note_topology`, which may reload the entry.
        """
        detected = topology_from_hub_configuration(configuration)
        if not detected:
            return
        model = model_for_topology(*detected)
        current = controller.model
        changed = (model.outlets_valve1, model.outlets_valve2) != (
            current.outlets_valve1,
            current.outlets_valve2,
        )
        if changed:
            # The name, not the id; see `Valve._apply_topology`.
            _LOGGER.info(
                "%s reports %s; using that for this controller instead of the entry's %s",
                controller.name,
                describe_topology(detected),
                current.sku,
            )
            controller.state.model = model
        self._async_note_topology(controller, changed=changed)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _persist_refresh_token(self) -> None:
        """Write the current refresh token back to the config entry.

        A safety net: rotation itself persists through `_store_refresh_token`.
        """
        self._store_refresh_token(self.auth.refresh_token)

    @callback
    def _stored_refresh_token(self) -> str | None:
        """The entry's refresh token, for `KohlerAuth` to fall back on; None once closed.

        If a reload lands while the old coordinator's refresh redeems this one's token, the
        old one stores the rotated token and this one's attempt is refused; it then reads
        the entry again (`KohlerAuth._adopt_newer_refresh_token`). Once closed it answers
        None: an unloaded coordinator must never redeem its successor's token.
        """
        if self.closed:
            return None
        token = self.entry.data.get(CONF_REFRESH_TOKEN)
        # About to be redeemed: if this coordinator closes during that retry,
        # `_store_refresh_token` must still store what it rotates to.
        if token:
            self._entry_token = token
        return token

    def _store_refresh_token(self, token: str | None) -> None:
        """Persist one refresh token, if it is new.

        Called from `KohlerAuth` the instant a rotation happens, inside the auth lock on
        the event loop, so `async_update_entry` is safe here.

        Once closed, it writes only over the token this coordinator last saw in the entry;
        a different one there belongs to the successor. It is not simply detached: a
        rotation an old command completes first is the only copy of a live credential.
        """
        stored = self.entry.data.get(CONF_REFRESH_TOKEN)
        if not token:
            return
        if token == stored:
            self._entry_token = token
            return
        if self.closed and stored != self._entry_token:
            _LOGGER.debug(
                "Not storing a token rotated after unload: the entry holds a newer one"
            )
            return
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_REFRESH_TOKEN: token}
        )
        self._entry_token = token

    # ------------------------------------------------------------------ #
    # Local writes — what the valves' custom-shower watchers count
    # ------------------------------------------------------------------ #
    def _note_local_write(self) -> None:
        """Count a controller command against every valve's custom-shower watcher.

        A valve command bumps only its own serial (`Valve._note_local_write`). A
        controller command — a favorite, the controller's own shower on/off, stop-all —
        cannot be attributed to one valve from the cloud, so it counts against all of
        them: a watcher that then declines to resume is the safe direction of error.
        """
        for valve in self.valves:
            valve._note_local_write()

    # ------------------------------------------------------------------ #
    # Report log — the consumer capture behind the "Report Log" switch
    # ------------------------------------------------------------------ #
    @property
    def report_log_active(self) -> bool:
        """Whether a capture episode is in force.

        Read from the entry options, which survive a restart, so the switch shows on even
        before `async_setup` has re-attached the file.
        """
        return bool(self.entry.options.get(CONF_REPORT_LOG_FILE))

    async def async_start_report_log(self) -> None:
        """Begin a new capture episode — a fresh file, named for this moment.

        Idempotent while an episode is running, so the file is never split. The episode
        name goes in the entry options so a restart resumes the same file; the key is in
        `RELOAD_IGNORED_OPTION_KEYS`, so the write does not reload the entry.
        """
        if self.report_log is None or self.report_log_active:
            return
        episode = await self.hass.async_add_executor_job(self.report_log.start)
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={**self.entry.options, CONF_REPORT_LOG_FILE: episode},
        )
        # Both devices carry this switch; refresh them together so they never disagree.
        self.async_update_listeners()

    async def async_stop_report_log(self) -> None:
        """End the capture episode. The files stay on disk until deleted by hand."""
        if self.report_log is not None:
            await self.hass.async_add_executor_job(self.report_log.stop)
        if CONF_REPORT_LOG_FILE in self.entry.options:
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={
                    k: v
                    for k, v in self.entry.options.items()
                    if k != CONF_REPORT_LOG_FILE
                },
            )
        self.async_update_listeners()

    # ------------------------------------------------------------------ #
    # Message record — context for the valves' warmup journals
    # ------------------------------------------------------------------ #
    @callback
    def _remember_message(self, envelope: Envelope) -> None:
        """Keep a light record of every message, for the warmup journal's context windows.

        Deliberately small: code, sku, timestamp, device and, for the valve's status
        message, the four fields that tell a configuration write from an ordinary status.
        The raw capture holds every payload in full.
        """
        # Stamped with arrival time, so a held message (`_early_envelopes`) stays in order
        # against the journal's own records.
        age = max(0.0, time.monotonic() - envelope.received_monotonic)
        record: dict[str, Any] = {
            # The same stamp shape the journal and the raw capture use, so the three sort
            # together on one clock.
            "ts": (datetime.now(UTC) - timedelta(seconds=age))
            .isoformat()
            .replace("+00:00", "Z"),
            "at": envelope.received_monotonic,
            "sku": envelope.sku,
            "code": envelope.code,
            # Which device, so `Valve._message_window` can leave other valves' traffic
            # out. Stripped again before the record reaches the journal.
            "device": envelope.device_id,
        }
        if envelope.code == MSG_GCS_SOLO_STATUS:
            attribute = envelope.attribute() or {}
            for key in (
                "configChangeIndent",
                "configWriteAllowedFlag",
                "currentSystemState",
                "warmUpStatus",
            ):
                if key in attribute:
                    record[key] = attribute[key]
        self._recent_messages.append(record)

    # ------------------------------------------------------------------ #
    # Controller commands
    # ------------------------------------------------------------------ #
    async def async_activate_favorite(
        self, controller: Controller, favorite_id: Any, name: str
    ) -> None:
        """Start a controller favorite. **This runs water.**

        The controller's only way to set water state: it has no direct temperature/outlet
        command, so a favorite is created holding that configuration and then activated.
        Activation is allowed even while something else is running.
        """
        self._note_local_write()
        with self.command_errors(_controller_offline(controller)):
            await controller.hub.async_activate_favorite(favorite_id, name, True)

    async def async_set_hub_shower(self, controller: Controller, on: bool) -> None:
        """Run or stop the controller's own default shower. **On runs water.**

        ``valvecontrol {valveOnOff}``, the controller's one bare on/off, which runs its own
        stored default. (The valve has none; see `Valve.async_start_default_shower`.) Off
        stops the water only, leaving music, steam and lighting; :meth:`async_stop_hub`
        idles everything.
        """
        self._note_local_write()
        with self.command_errors(_controller_offline(controller)):
            await controller.hub.async_set_shower(on)

    async def async_set_hub_steam(self, controller: Controller, on: bool) -> None:
        """Run or stop the controller's default steam. **On starts the steam generator.**

        ``steamcontrol {steamOnOff}`` at ``steamSettings`` defaults. Refused while the
        controller reports water running, as the app does. App-confirmed only
        (`docs/protocol/hub_controller.md` §8).
        """
        if on and controller.water_is_running:
            raise HomeAssistantError(
                f"{controller.name} is running the shower. The Konnect app does not allow "
                "shower and steam at the same time — stop the shower first."
            )
        self._note_local_write()
        with self.command_errors(_controller_offline(controller)):
            await controller.hub.async_set_steam(on)

    async def async_control_hub_experience(
        self, controller: Controller, title: str, on: bool
    ) -> None:
        """Start or stop a controller experience by title. **Starting one runs water.**

        The endpoint must match the category the title was listed under
        (`docs/protocol/hub_controller.md` §3), so the title is resolved against the read
        catalogue.
        """
        category = next(
            (
                name
                for name, items in controller.experiences.items()
                if any(_experience_title(item) == title for item in items)
            ),
            None,
        )
        if category is None:
            raise HomeAssistantError(
                f"No experience called {title!r} on {controller.name}."
            )
        self._note_local_write()
        with self.command_errors(_controller_offline(controller)):
            await controller.hub.async_control_experience(title, category, on)

    # ------------------------------------------------------------------ #
    # Firmware and faults, twice a day
    # ------------------------------------------------------------------ #
    @callback
    def _async_firmware_tick(self, _now: Any) -> None:
        """Start the twice-daily check as a task held in `_firmware_task`.

        Held so unload can cancel a check already running. One check at a time.
        """
        if self.closed or (
            self._firmware_task is not None and not self._firmware_task.done()
        ):
            return
        self._firmware_task = self.hass.async_create_task(self._async_twice_daily())

    async def _async_twice_daily(self) -> None:
        """The twice-daily reads: firmware, and every device's faults beside it."""
        _raise_first(
            await asyncio.gather(
                self.async_refresh_firmware(),
                self.async_refresh_faults(),
                return_exceptions=True,
            )
        )

    async def async_refresh_faults(self) -> None:
        """Read every valve's fault log and every controller's active errors again.

        Twice a day, on the firmware check's clock, as well as on every seed: nothing pushes
        a fault, and on a stable stream seeds are rare. A read that fails keeps what was
        known; a dead sign-in still prompts reauth.
        """
        results = await asyncio.gather(
            *(valve.async_refresh_faults() for valve in self.valves),
            *(
                self._async_refresh_active_errors(controller)
                for controller in self.controllers
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, AuthError):
                _LOGGER.debug("Could not read active faults: %s", result)
                if credential_is_dead(result):
                    self._handle_auth_error(result)
            elif isinstance(result, Exception):
                _LOGGER.warning("Reading a Kohler device's faults failed: %s", result)
        self.async_refresh_entities()

    async def async_refresh_firmware(self) -> None:
        """Read installed-vs-latest firmware for every valve, gateway and controller.

        Read-only; installing stays with the app. Each read answers `{}` on failure, so
        one unreachable part leaves only its own update entity unknown.
        """
        try:
            for valve in self.valves:
                device_id = valve.device_id
                gcs, gateway = await asyncio.gather(
                    self.client.async_get_firmware(device_id, "gcs"),
                    self.client.async_get_firmware(device_id, "gateway"),
                )
                valve.firmware_info = {"gcs": gcs, "gateway": gateway}
            for controller in self.controllers:
                controller.firmware_info = await self.client.async_get_firmware(
                    controller.device_id, "hub"
                )
        except AuthError as err:
            # Nobody is waiting on a timer, but a dead sign-in must still reach someone.
            _LOGGER.debug("Could not check firmware: %s", err)
            if credential_is_dead(err):
                self._handle_auth_error(err)
            return
        self.async_refresh_entities()

    async def async_stop_hub(self, controller: Controller) -> None:
        """Stop everything the controller is running — water, steam, music, lighting.

        ``stopall`` rather than deactivating a favorite, so a stop does not depend on
        identifying what is running.
        """
        self._note_local_write()
        with self.command_errors(_controller_offline(controller)):
            await controller.hub.async_stop_all()
