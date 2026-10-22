"""Coordinator: holds the MQTT stream, the REST client, and per-device state.

One per Kohler account. It owns what every device shares — the sign-in, the REST client and
the one MQTT connection — and the per-device objects: a `Valve` for each Anthem valve, a
`Controller` for each Anthem Plus, and a `FaucetCoordinator` for each Sensate or Setra
faucet, which polls on its own clock (see `faucet/coordinator.py`).

Shower state is **push-only**. MQTT carries every change as it happens and there is no
polling interval at all — REST is read on events, never on a clock, with one exception:
firmware availability, which nothing announces, is checked twice a day.

Three things this has to get right:

* **Cold start.** MQTT is event-driven and silent until the shower next changes, so a
  restart would leave every entity unknown. One REST read at setup seeds everything, made
  while the stream connects, so that nothing can change unseen between the two.
* **Reconnects.** The broker replays nothing on connect: measured across 27 sessions, the
  first message is always a change event and six sessions received nothing for hours. So
  every reconnect re-seeds, which is what makes dropping the poll safe. The first connect
  needs less: only live state the setup read before the stream was up is read again.
* **Token rotation.** B2C issues a new refresh token on every refresh and invalidates the
  old one. Losing it strands the account, so it is written back to the config entry
  whenever it changes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import time
import uuid
from collections import Counter, deque
from collections.abc import Coroutine, Iterable, Iterator
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
    DEVICE_NAME_FAUCET,
    DEVICE_NAME_VALVE,
    DOMAIN,
    ENABLE_RAW_MQTT_LOG,
    ENABLE_WARMUP_DEBUG_LOG,
    FAUCET_SCAN_INTERVAL_IDLE,
    FAUCET_STORAGE_KEY,
    FAUCET_STORAGE_VERSION,
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
from .konnect.faucet import parse_event
from .konnect.gcs import validate_outlet_record
from .konnect.models import DEFAULT_VALVE_MODEL
from .konnect.state import outlet_limits_from_settings
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
from .registry import device_by_identifier
from .warmup_manager import WarmupManager

_LOGGER = logging.getLogger(__name__)

#: The `GcsState` fields of a `gcs-state` read that a valve message can carry too: the zone
#: words, and the session fields `GCS_SOLO_STS` reports with them; `GCS_WARM_STS` carries
#: `warmup_mode`. `Valve._push_serials` counts each field a message did carry
#: (`GcsState.carried`) — a word only if it decoded, a session field only if present. One
#: carried while a read is in flight, or just before it went out, is newer than the read
#: **for that field only**; see `Valve.apply_rest_reply`. Until 2026-10-16 they were
#: counted in groups, so a report whose zone 1 word was garbled kept zone 1 shown running
#: over a read that had it stopped, and any status report kept every session field out.
#: Presets, outlet records and the dispensed volume come from other reads, so a message
#: about them conflicts with nothing.
_GCS_WORD_FIELDS = ("valve1", "valve2")
_GCS_SESSION_FIELDS = (
    "warmup_in_progress",
    "system_state",
    "active_preset_id",
    "total_volume",
    "total_flow",
)

#: How far `gcs-state` trails the valve's own reports, in seconds, with room to spare: it
#: still showed the old warm-up mode immediately after a write and caught up by about 3 s
#: (measured 2026-08-20). A report that arrived this shortly before a read was sent can be
#: newer than the read's reply, so what it carried is kept over the reply's.
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

    ⚠️ **"Today" is the later of the local date and the UTC one** (2026-10-16). The query
    ended at the UTC date, which east of UTC — from local midnight to UTC midnight — is
    yesterday, so the morning's first shower was left out of `Water Used Today` until UTC
    caught up. The local date alone, as the faucets and the Konnect app ask, is wrong the
    other way: on the accounts whose buckets carry UTC dates, a US evening's water sits
    under tomorrow's date, which `water.py` reads as today's, and a local `ToDate` would cut
    it off. The later of the two serves both, and each is a date the server already takes
    — the UTC one from this query until now, the local one from the app and the faucets.
    The window starts from the earlier, so it only ever grows.
    """
    local = dt_util.now().date()
    utc = datetime.now(UTC).date()
    start = min(local, utc) - timedelta(days=days_back)
    return start.isoformat(), max(local, utc).isoformat()


def _fault_is_active(entry: Any) -> bool:
    """Whether one `gcs-diagnostics` entry is an active fault the app would show.

    The app drops entries whose `errorCode` is `"0"` or empty (`docs/protocol/platform.md`
    §8), and `diagnostics._fault_log` applies the same filter. `isActive` is a JSON bool on
    the owner's account; a string spelling is accepted too rather than read as inactive.
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
    # Kohler's own shape, confirmed 2026-09-10 against two K-28210 valves: an OTA record
    # reports `updatedVersion` (what is now running) alongside `initialVersion` (what it was
    # before). `updatedVersion` leads for that reason.
    "updatedVersion",
    "currentFirmwareVersion",
    "currentVersion",
    "firmwareVersion",
    "swVersion",
    "firmware",
    "version",
)

#: A valve reports several OTA payloads, distinguished by `firmwareType`. `Application` is
#: the valve's actual firmware; `Assets` is the bundled UI artwork, which carries its own
#: unrelated version — 2.00 while the application is 2.20 on the owner's left valve. Reading
#: whichever arrived first gave the Assets number, so the application build is preferred and
#: anything else is only a fallback.
_FIRMWARE_PREFERRED_TYPE = "Application"


def _firmware_string(value: Any) -> str | None:
    """A firmware version as a non-empty string, or None.

    Numbers are accepted and stringified — a valve reporting `74` rather than `"00.74"` is
    still answering the question. Bools are rejected: `True` is not a version, and in Python
    it would otherwise pass an `isinstance(..., int)` test.
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

    Handles the two shapes seen in the wild: a flat mapping of version keys, and an Azure IoT
    device twin whose real content sits under ``reported``. A string block is taken as the
    version itself, which is what a bare ``otaReportedProperties: "00.74"`` would be.
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
    `__init__.py`, which takes one per update and compares. Both must apply the same
    exclusions or the comparison means nothing, so there is one call site for the pair of
    key sets rather than two that can drift.
    """
    return reload_signature(
        entry.data,
        entry.options,
        ignore_data=RELOAD_IGNORED_DATA_KEYS,
        ignore_options=RELOAD_IGNORED_OPTION_KEYS,
    )


def _command_half(value: str, field: str) -> str:
    """Validate a user-supplied valve word, accepting either length the system shows.

    `normalize_word` truncates to the first 8 characters, which is right for device data —
    the valve reports 16-character words whose second half is sensor feedback. But it means
    a 10-character typo silently becomes a valid, *different* command, and this input reaches
    something that opens water valves.

    So the length is checked first, and only the two lengths a person could legitimately have
    are allowed: **8** (a command word) or **16** (what `sensor.anthem_valve_zone_N_hex`
    displays, so it can be pasted straight in). Anything else is a mistake, not a shorthand.
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

    # **The temperature ceiling applies here too.** This is the one path that reaches the
    # valve without going through `encode_word`, which clamps every other caller to
    # `TEMPERATURE_MAX_TENTHS`. The word carries a 10-bit temperature, so a hand-typed or
    # scripted word can encode 102.3 °C — 216 °F — and be sent verbatim. Whether the firmware
    # would honour it is untested, and this integration should not be the thing depending on
    # that answer.
    #
    # Only the temperature is checked. Outlet masks, flow bytes, pause and warm-up flags are
    # exactly what this escape hatch exists to experiment with, and none of them can scald.
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

    Deliberately tolerant: this only ever annotates something that has already been
    validated and is about to be sent, so a decode failure must not block the write.
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
    """The message for a controller that answered `statusCode 900`.

    Named, because with several controllers on the account "the controller" no longer
    says which one to go and look at.
    """
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

    An account can carry several controllers — one per bathroom is the ordinary case — and
    the cloud lists them all under one tenant, so the coordinator holds a list of these rather
    than one set of singular fields. Every controller entity is bound to exactly one of them,
    and each MQTT envelope is routed to the one whose id it carries. Until 2026-09-08 the
    coordinator kept only the *first* controller the account listed and silently ignored the
    rest.
    """

    def __init__(
        self, device: Device, hub: HubDevice, state: HubState, name: str
    ) -> None:
        self.device = device
        #: Command surface — favorites, `valvecontrol`, `stopall`.
        self.hub = hub
        #: Live state, fed by the REST seed and then by MQTT.
        self.state = state
        #: Device name shown in Home Assistant. "Anthem Plus" on a single-controller account,
        #: so nothing changes for an existing install; see `controller_names`.
        self.name = name
        #: Which accessories are attached. Latched by the first successful configuration
        #: read; `known` is what says whether that read has happened.
        self.capabilities = HubCapabilities()
        #: The controller's own settings from `hub-configuration` — Max Shower Duration,
        #: steam defaults, light groups, LAN address, and which fitted accessories have
        #: dropped off. Re-read on every seed, unlike `capabilities`: these are settings
        #: an owner changes, not installation facts.
        self.settings = HubSettings()
        #: The controller's experience programs, by category (`showerExperiences`,
        #: `steamExperiences`, `iceShowerExperiences`) — the firmware's fixed catalogue,
        #: read once.
        self.experiences: dict[str, list[dict[str, Any]]] = {}
        #: `hub-diagnostics/{id}/active` `errorDetails[]`, refreshed on each seed and twice
        #: a day. A read that fails keeps what was known; see
        #: `KohlerKonnectCoordinator._async_refresh_active_errors`.
        self.active_errors: list[dict[str, Any]] = []
        #: The firmware read for this controller (`firmware/hub`), refreshed twice a day.
        self.firmware_info: dict[str, Any] = {}
        #: Messages applied that carry part of what `hub-state` carries, counted per message
        #: code (`_HUB_PUSH_FIELDS`). Read before that GET and compared after it: what a
        #: message that moved in between carried is newer than the reply, and the rest of
        #: the reply still applies — the same rule, for the same reason, as
        #: `Valve._push_serials`.
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

        Seeded over REST, then replaced wholesale by every `FAVORITES_SNAPSHOT`. One copy,
        on the state object: until 2026-10-08 there were two, and every controller message
        copied the state's over the seeded one — so after a reconnect seed, the next
        MUSIC_STS put back whatever the last snapshot had said, deleted favorites included.
        """
        return self.state.favorites

    @favorites.setter
    def favorites(self, favorites: list[dict[str, Any]]) -> None:
        self.state.favorites = favorites

    @property
    def model(self) -> ValveModel:
        """Outlet layout of the valve behind this controller.

        Starts as the entry's model and is replaced by what the controller's own
        `hub-configuration` reports on the first seed — two controllers on one account can
        front different valve models, and the entry stores only one. Lives on the state
        object, which is what decodes the per-zone outlet arrays with it, so there is exactly
        one copy to get out of step. See `KohlerKonnectCoordinator._apply_controller_topology`.
        """
        return self.state.model

    @property
    def water_is_running(self) -> bool | None:
        """Whether the **controller** believes water is running. Never asks the valve.

        This is deliberately the controller's own, possibly wrong, view — and the entities
        on the Anthem Plus device are the one place that is the right answer. They are
        answering "what does this controller think it is doing", and a controller that has
        not been told about a session is not doing anything: its ``stopall`` and
        ``valvecontrol OFF`` have nothing to stop, and its own timers are not counting.

        **This replaced a valve-backed property on 2026-08-18, because that produced a false
        positive.** `resolve_outlet_source()` is right that the valve owns the *physical*
        water state, and the Anthem Valve entities read it. But feeding it to the
        controller's switches made them report a system the controller knew nothing about.
        Measured that day: a 86-minute GCS-driven shower — open at 07:52:01 local, the
        valve's 3600 s pause and our restore at 08:52, stopped by hand at 09:18 — during
        which the controller published **not one message of any kind**, `SHOWER_VALVE_STS`
        included. The capture holds five `GCS_SOLO_STS` messages and nothing else. Both
        controller switches nonetheless tracked the shower perfectly, which looked like
        health and was actually the valve wearing the controller's name.

        Read from the outlet arrays rather than ``HubState.is_running``'s zone ``status``
        so this agrees exactly with the ``ControllerOutletSensor`` binary sensors — the
        Shower switch is on if and only if one of those outlet rows is on. The two sources
        do not disagree in any capture; matching them is about the dashboard being
        self-consistent, not about correctness.

        ``None`` — "unknown", not "off" — until the controller has reported a zone at all,
        since an empty ``zones`` map pads to all-False and would otherwise read as a
        confident "no water".
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


def faucet_names(devices: list[Device]) -> dict[str, str]:
    """Home Assistant device name per faucet, keyed by device id.

    Unlike the showers', the Konnect name alone — what the owner called the faucet in the
    app, usually the room — as the separate faucet integration did, so its entity ids carry
    over for anyone who removes it first. A faucet with no name, or one sharing it, is
    numbered instead.
    """
    labels, ordinals = _konnect_labels(devices)
    seen = Counter(labels.values())
    names: dict[str, str] = {}
    for device_id, label in labels.items():
        if label and seen[label] == 1:
            names[device_id] = label
        elif len(devices) == 1:
            names[device_id] = label or DEVICE_NAME_FAUCET
        else:
            names[device_id] = f"{label or DEVICE_NAME_FAUCET} {ordinals[device_id]}"
    return names


def _device_names(devices: list[Device], base: str) -> dict[str, str]:
    """Home Assistant device name per device of one kind, keyed by device id.

    One controller keeps the plain "Anthem Plus" that every existing install, the user guide
    and the README's entity ids were built on. With several, each takes its Konnect name —
    what the owner called it in the app, usually the bathroom — so the two devices' entity
    ids cannot collide. Two controllers sharing a Konnect name, or one with none, fall back
    to their position in the cloud's list.

    Names only decide entity ids at first registration; renaming a device later in Home
    Assistant does not disturb this, and neither does this disturb an existing registry row.
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

    **Never the device id.** `Device.name` falls back to it, and a device name reaches entity
    ids, the dashboard, screenshots and every log line that names the device — so a name
    equal to the id counts as none. A position is enough to tell two devices apart.
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

    The counterpart of :class:`Controller`. Until 2026-09-08 the valve path — state,
    warm-up auto-restore, the cloud reachability watch and the custom shower watcher —
    lived on the coordinator as singular fields,
    which meant the first valve the account listed and no other. Every one of those things
    is per valve, so they live here, and the coordinator holds a list.

    **The method bodies below are the coordinator's, moved.** They still say `self.gcs`,
    `self.gcs_state`, `self.hass`, `self.entry` and so on, which is why those names exist
    on this class as attributes and delegating properties: the history in the docstrings
    and the measurements they cite are the valuable part, and rewriting every line to a new
    vocabulary would have put all of it at risk for no behavioural gain.

    **Settings are per valve.** The Warmup Auto-Restore switch and the remembered warm-up
    mode sit under `CONF_VALVES` in the entry's options, keyed by device id — see `option`.
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
        #: Device name shown in Home Assistant. "Anthem Valve" on a single-valve account,
        #: so nothing changes for an existing install; see `valve_names`.
        self.name = name
        #: The valve's id in Home Assistant's device registry, set when setup registers it
        #: as the parent of its zone devices, which link to it by this id. None otherwise.
        self.registry_id: str | None = None
        #: Stamped onto every journal record when the account has several valves, so the
        #: shared warmup journal stays attributable. None keeps a single-valve journal
        #: exactly as it was.
        self.tag = tag
        self.gcs = GcsDevice(
            coordinator.client, device.device_id, coordinator.temperature_unit, model
        )
        self.gcs_state = GcsState(model, coordinator.temperature_unit)
        # CLOUD CONNECTION WATCH: one per valve, because it is this valve's reachability it
        # reports. See `cloud_watch.py`.
        self.cloud_watch = CloudConnectionWatch(coordinator, self)
        #: Everything about the warm-up mode: writing it, watching it, putting it back.
        #: It keeps its own record of what it wrote: a change we caused must not be treated
        #: as the device misbehaving, or turning warmup off from the dropdown would be
        #: undone a minute later.
        #: Its own object because it is a closed system — see `warmup_manager`.
        self.warmup = WarmupManager(self)
        # CUSTOM SHOWER: the "No pausing warm-up" watcher, one at a time, and
        # a serial that every command sent from here bumps, so the watcher can tell that
        # something else was sent after its own write. See `konnect/warmup_resume.py`.
        self._custom_shower_task: asyncio.Future[Any] | None = None
        # **Every task this valve starts is held here so `stop()` can cancel it.** A
        # warm-up restore sleeps for a minute and then writes to the hardware, so one
        # surviving an unload means an HTTP write, and a config-entry mutation, from a
        # coordinator Home Assistant has already discarded. A reload inside that window is enough to trigger it.
        self._background_tasks: set[asyncio.Task] = set()
        self._local_write_serial = 0
        # VALVE WRITES, one at a time and each built on the last (GitHub issue #1). Every
        # write re-sends both zones rebuilt from a baseline, so two built from the same one
        # undo each other: `switch.turn_on` on two outlets opened one, and an outlet toggle
        # followed within the echo's ~2 s by a temperature change closed it again. The lock
        # serialises every command that sets the valve's words; `_sent_words` is what the
        # last one sent — the next one's baseline for `_sent_hold_seconds`, narrowed by any
        # report since, and the shower a warm-up is warming up — see `_valve_baseline`.
        # `_sent_over` is the reported pair when it was asked for, read before its POST.
        self._valve_write_lock = asyncio.Lock()
        self._sent_words: tuple[Any, Any] | None = None
        self._sent_over: tuple[Any, Any] = (None, None)
        self._sent_at = 0.0
        # The echo lands 1.1-2.1 s after the POST returns (measured), so this covers it
        # with room to spare while keeping anything done at the wall from being narrowed
        # away for long. A report inside it narrows the words rather than ending them.
        self._sent_hold_seconds = 5.0
        # Bumped by every command that decides the valve's words (`_note_local_write`) —
        # a word write, a preset, a restart, a controller's command — and by nothing else,
        # so a word write can tell that one landed while its own POST was in flight. The
        # custom-shower watcher's `_local_write_serial` counts outlet settings as well.
        self._words_serial = 0
        # Bumped the moment a Stop is asked for, before it waits for the lock: every
        # command asked for before it steps aside when its turn comes. See
        # `async_stop_shower`.
        self._stop_generation = 0
        # OUTLET SETTINGS, the same rule for `writeoutletconfig` chains: one at a time, and
        # a serial each bumps so a delayed read-back can tell another landed since. See
        # `async_write_outlet_setting`.
        self._outlet_write_lock = asyncio.Lock()
        self._outlet_write_serial = 0
        # Each outlet setting written from here, by `OutletLimits` field, with the value
        # written — until a read-back shows it on every outlet. A read the cloud has not
        # caught up with must not put the old value back in the cache the next write
        # echoes; see `_adopt_outlet_read`.
        self._outlet_unconfirmed: dict[str, int] = {}
        # Those of them a read past the catch-up window still contradicts, with the value
        # it showed: no other outlet setting may be written until each is settled, since
        # every write re-sends it. See `_settle_outlet_fields`.
        self._outlet_disputed: dict[str, int | None] = {}
        # What the valve last reported for each outlet — announced over MQTT or read — and
        # when it last announced it (monotonic, as the message arrived): a `gcs-settings`
        # read sent within the catch-up window after an announcement can still show what
        # came before, so the announcement stands for that outlet. See `_reported_outlets`.
        self._outlet_reported: dict[int, Any] = {}
        self._outlet_announced_at: dict[int, float] = {}
        # The fields of each outlet's latest announcement taken for a late echo: a guess,
        # so a read decides them. See `_note_outlet_announcement`.
        self._outlet_guessed: dict[int, frozenset[str]] = {}
        # Until when the outlet records cannot be vouched for (monotonic): the stream has
        # just come back, and a change it missed may not show in the cloud's copy yet.
        # Outlet writes wait it out, then read first; see `note_stream_gap`.
        self._outlets_unsure_until: float | None = None
        # The values each outlet setting written from here replaced, and any it was set to
        # just before, within the catch-up window: an announcement inside it showing one is
        # a late echo, and one showing anything else a change made elsewhere. See
        # `_note_outlet_announcement`.
        self._outlet_superseded: dict[str, set[int | None]] = {}
        # When the latest outlet write began (monotonic), so a reseed's `gcs-settings` read
        # inside the cloud's catch-up window cannot put the old values back. See
        # `_outlet_limits_settled`.
        self._outlet_written_at: float | None = None
        # How long each zone has been running, for the time-left attributes. The valve
        # times each zone against its `maximumRunTime` and reports nothing about the clock,
        # so this keeps one from the messages. See `konnect/zone_clock.py`.
        self._zone_clock = ZoneClock()
        # Whether the valve's own outlet split has been read yet — see `async_seed`.
        self._topology_checked = False
        # Whether its settings and its presets have answered since setup; see
        # `seed_incomplete`.
        self._settings_read = self._presets_read = False
        # Messages applied that carry part of what `gcs-state` carries, counted per field
        # (`GcsState.carried`; `_GCS_WORD_FIELDS`, `_GCS_SESSION_FIELDS`, `warmup_mode`).
        # Read before that GET and compared after it: a field a push carried in between is
        # newer than the reply, which `gcs-state` serves seconds behind the device, and the
        # rest of the reply still applies. See `apply_rest_reply`; the warm-up manager's own
        # read of the mode compares "warmup_mode".
        self._push_serials: Counter[str] = Counter()
        # When each of those fields was last reported (monotonic, as the message arrived):
        # a read sent within `_GCS_STATE_TRAILS_SECONDS` of it can answer from before it.
        self._reported_at: dict[str, float] = {}
        #: The stream connection (`KohlerKonnectCoordinator.stream_connects`) the last
        #: successful `gcs-state` read was sent under — 0 before the stream first connected,
        #: None until a read has succeeded. How the first connect tells which reads it
        #: covers; see `KohlerKonnectCoordinator._handle_connected`.
        self.state_read_on: int | None = None
        #: The valve's active faults from its `gcs-diagnostics` log, re-read on every seed
        #: and twice a day — code, area, valve and timestamp only. What the Problem sensor
        #: turns on for; see `async_refresh_faults`.
        self.active_faults: list[dict[str, Any]] = []
        # The `gcs-configuration` record, read once at the first seed. None means "not read
        # yet"; `{}` means the read was attempted and produced nothing usable, which is the
        # documented result on a controller-attached valve and is not an error.
        self.configuration: dict[str, Any] | None = None
        #: `gcs-configuration/{id}/about` — serials, models and firmware per part. Read
        #: beside `configuration`, and again on each seed until a read answers; {} until then.
        self.about_parts: dict[str, Any] = {}
        #: The firmware reads for this valve, keyed `gcs` (the valve) and `gateway`, each
        #: `{currentFirmware, firmware, firmwareUpdateAvailable, mandatoryUpdate, ...}`.
        #: Refreshed twice a day — see `KohlerKonnectCoordinator.async_refresh_firmware`.
        self.firmware_info: dict[str, dict[str, Any]] = {}
        #: The most recent `gcs-usage` response, or {} when the read failed. Seeded at
        #: startup — retried on later seeds and daily reads until it succeeds — and
        #: refreshed once when a calendar month boundary rolls over.
        self.usage: dict[str, Any] = {}
        self._usage_seeded_month: str | None = None
        # The per-day series, refreshed when a shower ends — see `async_refresh_daily_usage`.
        self.usage_daily: dict[str, Any] = {}
        # Guards the refresh against a burst of stop-messages: the valve sends several as a
        # shower winds down, and each must not become its own cloud read.
        self._daily_usage_task: asyncio.Task | None = None
        self._was_running = False
        # The pending read-back of each outlet setting written — see
        # `async_write_outlet_setting`. One per setting, so a second write of the same one
        # replaces the first's check rather than racing it.
        self._verify_tasks: dict[tuple[str, ...], asyncio.Task] = {}
        # The flow each zone's Flow number is currently showing, keyed by zone, as the
        # percent the user chose — of **that zone's own** ceiling, the entity's scale, and
        # restated for the wire only when a word is built (`_wire_flow`). Recorded by every
        # write that names a flow and the default for every write that does not, so neither
        # an outlet toggle nor a temperature change silently resets a flow the user chose —
        # see `async_apply_valve`. Seeded with `DEFAULT_FLOW_PERCENT`, so the behaviour
        # before anyone touches the entity is exactly as it was.
        self.zone_flow: dict[int, float] = {
            zone: DEFAULT_FLOW_PERCENT for zone in self.model.zones
        }

    def __repr__(self) -> str:
        return f"<Valve {self.device_id} {self.name!r}>"

    @property
    def created_time(self) -> str | None:
        """When Kohler's cloud first created this device's record, as it reports it.

        The closest thing to an install date the API offers — **the cloud record's
        creation, not the day a plumber fitted the valve**, so a valve re-registered after
        a service call would read as newer than it is. Named for what it is rather than
        what it approximates.

        Returned as the raw string; `sensor.ValveInstalledSensor` parses it.
        """
        value = (self.configuration or {}).get("createdTime")
        return None if value in (None, "") else str(value)

    @property
    def firmware(self) -> str | None:
        """The **interface** firmware — the touchscreen's own version.

        Kept as `firmware` because it is what the device registry shows and what every
        earlier release meant by the word. The valve and gateway have their own versions and
        their own properties; see :meth:`component_firmware`.

        ⚠️ **Two bugs lived here until 0.11.0, and both reported a wrong number rather than
        nothing.** `about` is nested inside the record's own `configuration` block, not at
        the top level, so `configuration.get("about")` was always `None` and every report
        said `about_keys: []` on hardware that populates it in full. And `about.firmware` is
        a *mapping* (`version` / `latestVersion`), not a string, so it would not have parsed
        even at the right depth. Together they meant this fell through to the OTA blocks,
        where a valve whose `otaReportedProperties` describes **Assets** — the artwork
        bundle — reported the artwork version as its firmware: `2.00` where the Konnect app
        showed 2.2, on one of the owner's two otherwise identical valves.

        Order, first hit wins:

        1. ``configuration.about.uI2.firmware`` — the interface, where the record nests it.
        2. ``about.firmware`` at the top level, as a string — the reference install's shape,
           kept so an install that reads correctly today keeps reading correctly.
        3. ``otaReportedProperties`` / ``firmwareUpdate``, **Application only**. A block
           describing Assets is skipped rather than reported: it answers a different
           question, and that is exactly the confusion above.
        4. A bare top-level ``version`` string.

        None where no shape matches. A blank is honest; a number that silently means
        something else is not.
        """
        about = self.about
        value = _firmware_string((about.get("uI2") or {}).get("firmware"))
        if value is not None:
            return value

        # The reference install's shape: `about.firmware` as a plain string at top level.
        # Only accepted as a string here — where it is a mapping it is the *gateway's*
        # version (confirmed 2026-09-10: `about.firmware.version` equals
        # `about.gateway.firmware`), which `component_firmware("gateway")` reports instead.
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

        # Then any block that does not declare itself something else. **An untyped block is
        # not an Assets block** — the reference install's `otaReportedProperties` carries no
        # `firmwareType` at all, and skipping it would trade one wrong answer for a blank on
        # hardware that reads correctly today. Only a block explicitly naming a non-
        # Application type is refused, which is the case that caused the artwork version to
        # pass for an interface version.
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

        Two shapes are known: nested under the record's own ``configuration`` key (both of
        the owner's K-28210 valves, 2026-09-10) and at the top level (the reference
        install). Checked nested-first because that is the shape that carries the full
        per-component breakdown; a top-level ``about`` on the reference install holds only
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

        The Konnect app shows **three different firmwares** for one shower — the touchscreen
        interface, the valves, and the gateway — and they are genuinely different numbers
        (2.2, 10 and 00.74 on the owner's system). Collapsing them into a single `Firmware`
        entity is what let an artwork version pass for an interface version for three
        releases.

        ``component`` is a key of the ``about`` block: ``uI2``, ``primaryValve``,
        ``secondaryValve1``, ``gateway``. Each holds ``firmware`` plus, sometimes,
        ``assetsFirmware`` and ``bleVersion``; only the running firmware is read here.

        ⚠️ **Two valves on one account can differ**, and the app does not show it: the
        owner's read `10` and `11` on 2026-09-10 while Konnect displayed 10 for both. That is
        the case this method exists to make visible.
        """
        block = self.about.get(component)
        if not isinstance(block, dict):
            return None
        return _firmware_string(block.get("firmware"))

    # ------------------------------------------------------------------ #
    # What the moved methods reach for on the coordinator
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
        `gcsadvancestate` reports on the first seed — two valves on one account can be
        different models, and the entry stores only one. Lives on the state object, which is
        what decodes every word with it; `GcsDevice` keeps a copy for encoding, and
        `_apply_topology` moves both together.
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
        """Count a command sent from this integration that decides this valve's words.

        Read by the custom-shower watcher: if the serial has moved since its own write,
        something else was sent in the meantime and the watcher must not resume on top
        of it. Controller commands bump every valve's serial through the coordinator, since
        which valve a controller fronts is not knowable from the cloud.

        **Also drops the last-sent words** (`_valve_baseline`) and counts against
        `_words_serial`. Most commands counted here — a preset, a controller favorite, a
        restart — decide the valve's words next, so the last ones written from here no
        longer describe them; for the rest, falling back to the report costs nothing. A
        word write re-arms the overlay once its own POST has landed, and puts back what it
        was built on, narrowed by it, if that POST fails (`_async_send_words`).

        **An outlet setting is not counted here** (2026-10-16): it changes no word, so it
        bumps the watcher's serial alone. Coming through here, it dropped the words a write
        had just sent — the next temperature change inside the echo's round trip closed the
        outlet just opened — and kept a word write in flight from arming them at all.
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
        # When it arrived, not when it is handled: one held until the devices were known is
        # handled seconds late, and stamped then it kept a read's words over a Stop at the
        # panel made in between (2026-10-16).
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
        # A valve message is proof of reachability, and settles any pending
        # contradiction check. CLOUD CONNECTION WATCH.
        self.cloud_watch.note_gcs_message()
        return changed

    def _note_running_for_usage(self) -> None:
        """Re-read the daily usage when a shower ends.

        **The one moment the number can have changed.** Water usage moves only while water
        runs, and this integration has no polling clock (`SCAN_INTERVAL` is None) — so
        without this, `Water Used Today` would hold whatever it read at startup for the rest
        of the day. Tying the read to the event that changes the value keeps the push-only
        design intact: no timer, and no read on a day nobody showered.

        Fires on the running -> stopped edge only. The valve sends several messages as a
        shower winds down and the running flag can flicker, so `_daily_usage_task` makes a
        second edge a no-op while the first read is still in flight; if water starts running
        again before the delayed read fires, the pending task is cancelled so the read waits
        until the session actually ends.
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

        The delay is not politeness: the cloud aggregates a session after the valve reports
        it closed, so reading the instant the water stops returns the total *without* the
        shower that just happened — the one reading a user would check.

        If the first read at `USAGE_REFRESH_DELAY_SECONDS` returns the exact same daily
        total as before (cloud aggregation lagged past 90 s), one follow-up read runs after
        `USAGE_RETRY_DELAY_SECONDS`. When the first read already reflects the new water —
        or when no baseline was seeded yet — no extra request is made.
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

        Two things at once. Home Assistant's own guidance is to hold a reference to any
        task you create, because the event loop keeps only a weak one and a task nobody
        references can be garbage-collected mid-await. And holding them is what makes
        `stop()` able to cancel them — see `_background_tasks`.

        **Nothing starts once the coordinator is closing.** `stop()` runs only after the
        stream has stopped, and paho can still deliver while it stops; a warm-up restore
        started in that window would write to the valve and the entry a minute after the
        unload. So the coroutine is closed unstarted — closing it is what keeps Python from
        warning that it was never awaited — and the caller gets a future that is already
        cancelled, which every caller's `done()` and `cancel()` handle as they would a
        finished task.
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
        # The warm-up restore sleeps 60 s and the journal write 45 s before touching
        # anything, so both can outlive an unload by a wide margin. Cancelled here rather
        # than left to finish: the restore ends in a write to the valve and a config-entry
        # update, neither of which is safe against an entry that is going away.
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

        Returns **whether the read actually said something** — which is what the caller
        latches on. The entry's model came from the config flow, which asked the *first*
        valve: right for it, and not necessarily for a second one on the account. Same
        reasoning as `KohlerKonnectCoordinator._apply_controller_topology`, and the same
        fallback: a read that yields nothing leaves the entry's model in place.

        ⚠️ **The caller used to latch before knowing the answer** (fixed 0.15.0, found by
        GitHub Copilot's review of upstream #3). `_topology_checked` was set to `True` and
        *then* this was called, so a first read carrying no layout pinned the entry's model
        for the life of the entry — and on an account whose valves differ, that is the wrong
        layout on every valve but the first, permanently. Returning the answer lets the next
        reconnect try again. The decode is fixed immediately; entities already built from
        the old layout are not, so a change found after the platforms loaded reloads the
        entry — see `KohlerKonnectCoordinator._async_note_topology`.
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
            # The read answered and agreed with the layout in use — settled, so latch it.
            # Returning False here would re-read on every reconnect for ever.
            return True
        # The valve's NAME, not its id: this is INFO, so it lands in the log people paste
        # into issues, and a Kohler device id is a cloud address (see 0.9.0). The name is
        # what identifies the valve to its owner anyway.
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
        """Write one outlet setting across every outlet, then verify it landed.

        **There is no list form.** The app writes one call per outlet, each carrying that
        outlet's whole record, and issues the next only after a 2xx — so a failure part-way
        leaves the valve holding the new value on some outlets and the old one on others.
        That is not hypothetical: it is what produced the `outlets_agree: false` this
        integration already reports (`docs/protocol/gcs_valve.md`, "one outlet per call").

        So this chains the same way and then **reads back** — but the read-back does not
        block the caller. A 201 from this endpoint means *accepted for delivery*, never
        *applied*: the response carries no echo of the value and the Konnect app performs no
        verification at all.

        ⚠️ **Verification runs in the background, and that is a responsiveness fix, not a
        weakening** (0.18.2). It has to wait ~30 s for the cloud document to catch up, and
        awaiting that inside a service call froze the slider for the whole time — Home
        Assistant logs a warning at 10 s, and the entity looked broken. The POSTs are still
        awaited, because a rejected write fails immediately and the caller should hear about
        it; only the waiting is deferred. A verification that fails raises a **repair issue**
        instead of an exception nobody is left to catch.

        Raises `HomeAssistantError` for a write that is refused or fails part-way — including
        which outlets took the new value and which did not. A partial write is reported
        rather than retried: retrying a half-applied safety setting without knowing why the
        first attempt failed is how one bad outlet becomes several.

        🚨 **Every other field is re-sent from the cache, so the cache must be current.**
        `writeoutletconfig` replaces the whole record, and the fields this write does not
        change come from `outlet_limits`. That cache used to move only on a report — and the
        valve's outlet-config message is unprompted (`docs/protocol/gcs_valve.md` §7) — so
        setting Max Temperature and then Max Shower Duration inside the ~30 s before
        anything reported re-sent the *old* scald limit with the new duration, silently
        undoing the first write. Two writes could also interleave their per-outlet chains.
        Fixed 2026-10-16: the whole chain runs under `_outlet_write_lock`, and each outlet's
        cached record takes the written fields as soon as its POST is accepted — and keeps
        them against any read the cloud has not caught up with (`_adopt_outlet_read`).

        **Default above maximum is refused before anything is sent**, checked against every
        outlet: the client refuses it per outlet too, but only when it reaches that outlet,
        which used to leave the earlier ones written and a message saying so. **So is every
        other refusal** — an unread field, a value outside the ranges the write accepts —
        through the client's own `validate_outlet_record`, run on every outlet first.

        Counted for the custom-shower watcher, but **not** as a command that decides the
        valve's words (`_note_local_write`): the words this integration last sent stand.
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
            # 🚨 **Not over a setting the valve has not confirmed** (2026-10-16). This write
            # re-sends every other field from the cache, and one written from here that no
            # read has confirmed may since have been set differently in the app — a scald
            # limit lowered there among them. Sending it again on the back of an unrelated
            # change is how such a limit was loosened. So one still unconfirmed past the
            # catch-up window is read again first, and if the valve's cloud still shows
            # something else the write is refused until the owner settles it, by setting
            # that one again.
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
            # Setting a disputed one again, any other goes as the valve reports it, not as
            # set from here: refused for it instead, two disputed settings each waited on
            # the other, and no outlet setting could be written until a restart
            # (2026-10-16).
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
            # Every other refusal, on every outlet, before the first is written: an
            # out-of-range flow ceiling on outlet 3 left outlets 1 and 2 written.
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
            # The watcher's count only: an outlet record is not the valve's words, so
            # `_note_local_write`, which drops the words last sent, is not for this.
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
                # And what the valve reports, which for a setting not yet confirmed — the
                # re-set a dispute asks for — is not what the cache holds (2026-10-16).
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
                    # Say exactly how far it got: the outlets already written hold the new
                    # value.
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

            # **Not awaited.** See the docstring: the wait is what made the entity
            # unresponsive.
            #
            # **One check per setting.** Two quick writes of the same setting each used to
            # read it back 30 s later, so the first check met the second value and raised
            # "still reporting the old value" until the second check cleared it — and N
            # writes cost N reads. The newest write's check is the only one that can be
            # right.
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

        ⚠️ **An immediate read-back lies.** `gcsadvancestate` is a cloud document that
        updates only once the device reports, and a read ~1 s after a 201 still showed the
        old value in the live sweep of 2026-08-21; the change appeared within 25 s. Reading
        too early is exactly how a working write looks like a device-side limit, so this
        waits first.

        **Runs detached**, so nothing here may raise: there is no caller left to catch it.
        A failure becomes a repair issue, which is the surface Home Assistant has for
        "something needs your attention later".

        ``serial`` is `_outlet_write_serial` as the write that scheduled this left it. The
        read replaces the cached records only if no outlet write has happened since: lagging
        by up to 25 s, it can still carry what a later write replaced, and the cache is what
        the next write echoes back.

        🚨 **Even then it does not replace a value written from here that it does not show
        yet** (`_adopt_outlet_read`, 2026-10-16). It used to replace everything and decide
        it was stale afterwards: Max Temperature lowered to 43.3 °C, the cloud still saying
        47.7 °C at this read, put 47.7 back in the cache — and the next setting written,
        Max Shower Duration, re-sent it to every outlet. **And each setting has its own
        repair issue**, cleared only by its own check: with one per valve, that duration's
        successful check deleted the scald limit's warning, so the limit was loosened
        silently.
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
        # Whatever this read shows on every outlet is confirmed, this write's or another's,
        # and from now on a read may replace it. A value it contradicts stays unconfirmed
        # rather than disputed: right at the catch-up window, a lagging cloud is the likely
        # reason, and the next write of another setting reads again before it sends it.
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
        # Verified: clear this setting's warning, left by an earlier attempt — and only
        # this setting's.
        self._clear_write_issue(fields)

    def _adopt_outlet_read(self, fresh: dict[int, Any]) -> None:
        """Take a `gcs-settings` read into the cached outlet records, except what it undoes.

        The read replaces each outlet's record — it is fresher than anything shown for a
        field this integration has not written, a change made in the app, say. **Not for
        a field written from here that it does not show yet** (`_outlet_unconfirmed`): the
        cloud document trails a write by up to ~25 s, sometimes longer, and the cache is
        what the next outlet write echoes to every outlet. Such a field keeps whatever the
        cache holds — the value written, or what the valve has announced over MQTT since,
        which is the device's own word.

        The reseed's read (`_async_seed_topology_and_state`) comes through here too, once
        `_outlet_limits_settled` allows it at all. Every read is taken as the valve reports
        it first (`_reported_outlets`).
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
        document can still show what came before, and the announcement is the device's own
        word. 🚨 **Taken as it came, a read put the old values back** (2026-10-16): the app
        lowered Max Temperature to 40.0 °C, the valve announced it, and a read seconds later
        — the check before a duration write — still said 47.7 °C. That went into the cache,
        and the write sent it to every outlet; it could confirm a value of Home Assistant's
        the announcement had just disputed, too, clearing the dispute. ``requested_at`` is
        when the read was sent (monotonic).
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
        the valve reports it, the setting changes nothing there.
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

        **Confirmed** when the read shows the value written on every outlet: from then on
        a read may replace it. **Disputed** when a read made after the cloud's catch-up
        window (`OUTLET_WRITE_VERIFY_DELAY_SECONDS`) still shows something else
        (2026-10-16). Nothing here can tell why — a cloud still behind, a valve that did not
        apply it, or a change made in the app since, a lower scald limit perhaps — so the
        cache keeps the value written, and no *other* outlet setting may be written while
        it stands (`async_write_outlet_setting`), since each would send it again. Before
        this a field the cloud never confirmed stayed unconfirmed for good: every later
        read kept it out, and the next unrelated write re-sent it over whatever the app
        had set. Settled by a read that shows it, or by setting it again.

        ``fresh`` is a REST read or a valve announcement's outlets (`OutletLimits` by
        outlet id); an announcement carries one outlet, so it can dispute but never
        confirm. ``dispute`` False confirms only — for the write's own read-back, whose
        warning covers a value it does not show yet. A new dispute raises the setting's
        repair issue.
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

        For a field written from here, not part of this write, and still unconfirmed past
        the catch-up window — inside it the value written is what the valve is about to
        show, and re-sending it is right. Costs one `gcs-settings` read in that rare state,
        and settles it: a read that shows the value confirms it and the write goes ahead;
        one that shows another disputes it (`_settle_outlet_fields`).

        **And while the stream cannot vouch for the records** (2026-10-16): down, when a
        change made in the app is never announced, or just back, when one made in the gap
        may not reach the cloud's copy for the catch-up window (`note_stream_gap`) — a
        write then waits that out, and reads first. Called with `_outlet_write_lock` held.
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
            # Nothing to check it against is no check: sent again on that, it went out
            # unchecked (2026-10-16).
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

        Nothing replays one, and the cloud's copy can take the catch-up window to show the
        change it was for. A reconnect's read could still show the old value, or an
        announcement from before the gap stand in for one that showed the new
        (`_reported_outlets`), and the next write sent the old scald limit to every outlet
        (2026-10-16). So outlet writes wait out the window, and the first after it reads
        the records again (`_async_confirm_outlet_fields`).
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

        Unless the write's own read-back has confirmed it meanwhile: then there is nothing
        left to check, and the write goes ahead. Asking for the next one regardless raised
        `StopIteration` out of the coroutine (2026-10-16). ``always`` refuses whatever is
        unconfirmed: the read was for the records as a whole.
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
        and it is the device's own word — but it is not always the latest one: two saves a
        second apart, and the announcement of the first could land after the second was
        sent, putting the value it replaced back in the cache the next write echoes
        (2026-10-16). Inside the catch-up window a value written from here therefore stands
        against such a late echo — **only against one** (2026-10-16): a value the setting
        held before, not one set in the app meanwhile, which the next write echoed back
        over the app's. Past the window, a disagreeing announcement disputes the field, as
        a read would (`_settle_outlet_fields`). ``before`` is the cache before the message;
        ``at`` is when it arrived (monotonic). What it leaves in the cache is what the valve
        reports for the outlet, for reads sent soon after (`_reported_outlets`).
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
            # What the valve said, and when. 🚨 **Not a field taken for a late echo**
            # (2026-10-16): that is a guess, and a read must still be able to show it
            # wrong. Kept as the outlet's report, it stood in for the cloud at the
            # read-back, which never saw the cloud's value: a write the valve ignored
            # counted as confirmed, and a limit set back in the app was loosened again
            # by the next write. Only that field — dropping the whole record let a read
            # still behind put back what the same save had changed in another.
            self._outlet_reported[outlet_id] = record
            self._outlet_announced_at[outlet_id] = at
            self._outlet_guessed[outlet_id] = guessed.get(outlet_id, frozenset())
        if announced and self._outlet_unconfirmed and settled:
            self._settle_outlet_fields(
                {outlet_id: outlets[outlet_id] for outlet_id in announced}
            )

    def _write_issue_id(self, fields: tuple[str, ...]) -> str:
        """One issue per valve **and per setting**, so none overwrites another's warning.

        Per setting since 2026-10-16: one per valve let any setting that verified delete the
        warning another had raised. The device's key stays last, which is how removing the
        entry finds its devices' issues (`async_remove_entry`).
        """
        setting = "_".join(fields)
        return f"outlet_write_unverified_{setting}_{device_issue_key(self.device_id)}"

    def _clear_write_issue(self, fields: tuple[str, ...]) -> None:
        """Drop one setting's warning — a verified write means its last one is stale."""
        ir.async_delete_issue(self.hass, DOMAIN, self._write_issue_id(fields))

    def forget_outlet_write_issues(self) -> None:
        """Drop every outlet-setting warning a previous run left for this valve.

        Repair issues outlive a restart; what they are about does not. One saying no other
        outlet setting can be changed until this one is set again stayed up after a restart
        had let them all be changed again (2026-10-16).
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

        `gcs-configuration`, the two `gcs-usage` series, `about` and `gcs-preset` are
        independent of the outlet topology and of each other — none of them is decoded
        against the valve's model, so none needs the `gcs-settings` read that `gcs-state`
        genuinely does. Issuing them while that pair is in flight removes their round trips
        from every cold start.

        Started rather than awaited inline so the caller can keep working (`gather` schedules
        both at once); every read inside is individually guarded against `KohlerError`, so
        one failure cannot blank the others. Cancellation-safe: the caller always awaits it.

        ⚠️ **Failures are collected, not raised** (2026-10-16). The token layer raises
        `AuthError`, which none of those guards catches, and a plain `gather` raised it at
        once and left the sibling read running against a setup that had already failed.
        The caller raises the first once both have finished; see `_raise_first`.
        """
        return asyncio.gather(
            self._async_seed_configuration(),
            self._async_seed_presets(),
            return_exceptions=True,
        )

    async def _async_seed_configuration(self) -> None:
        """Firmware and the structural fields, plus the usage series and `about` beside them.

        The configuration record is **first seed only:** installation-time data that cannot
        change while Home Assistant runs, so a reconnect must not spend a call on it. The
        daily usage series rides along with it; after that it is read when a shower ends.

        ⚠️ **The monthly series and `about` are retried until a read answers** (2026-10-16).
        They used to sit behind the same first-seed gate, so one failed read at boot lost
        them until a restart: `configuration` is latched to `{}` on failure, the gate shut,
        and with `_usage_seeded_month` never set the month-rollover re-read never fired
        either. Each now has its own record of success — `_usage_seeded_month`, and a
        non-empty `about_parts` — and is asked again on every seed until it has one. The
        daily refresh retries the monthly series too; see `async_refresh_daily_usage`.

        Entirely diagnostic; a failure is logged and setup continues. All of it overlaps,
        since none of it depends on the rest — and all of it finishes before a sign-in
        failure from any of it is raised, for the reason `_seed_independent_reads` gives.
        """
        reads: list[Coroutine[Any, Any, None]] = []
        if self.configuration is None:
            reads.append(self._async_read_configuration())
            # `monthly=False`: the monthly series is read just below, in parallel, and the
            # daily read must not start a second copy of it on its way out.
            reads.append(self.async_refresh_daily_usage(monthly=False))
        if self._usage_seeded_month is None:
            # Thirteen months back covers a full year plus the current partial one, which
            # is what a year-on-year comparison needs. `async_get_usage` answers `{}` on
            # failure rather than raising, so this needs no guard of its own.
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
            # `{}` rather than leaving None, so a failed read is not retried on every
            # reconnect for data that is static anyway.
            self.configuration = {}

    async def _async_read_about(self) -> None:
        """Per-part serials and models, for the device registry and diagnostics.

        Answers {} on failure by contract, so it needs no guard — and {} is what tells the
        next seed to ask again.
        """
        self.about_parts = await self.client.async_get_gcs_about(
            self.gcs_device.device_id
        )

    async def async_refresh_monthly_usage(self) -> None:
        """Read the per-month usage series (`Interval=MONTH`, 400 days back)."""
        from_date, to_date = _usage_window(400)
        fresh = await self.client.async_get_usage(
            self.gcs_device.device_id, from_date=from_date, to_date=to_date
        )
        # `async_get_usage` returns `{}` on a transient HTTP failure; only overwrite
        # `self.usage` when the read succeeds (or on cold start when nothing is held yet),
        # so a transient failure on month rollover never drops prior months from memory.
        if fresh or not self.usage:
            self.usage = fresh
        if fresh:
            self._usage_seeded_month = dt_util.now().strftime("%Y-%m")

    async def async_refresh_daily_usage(self, *, monthly: bool = True) -> None:
        """Read the per-day usage series — `Interval=DAY`, verified working 2026-09-11.

        **Why DAY and not WEEK.** `WEEK` is refused at every range tried: 400 days, 90 days
        and 28 days alike, the last being four buckets against the fifteen `DAY` happily
        serves. So the earlier row-cap theory is dead and the endpoint simply does not take
        `WEEK` — while `DAY` gives both the day and the week, since seven daily buckets are
        a week. See `docs/protocol/gcs_valve.md`.

        Validated on the owner's account the day it was added: the fifteen daily entries
        summed to exactly the 465 L the `MONTH` series reported for the same month, which is
        what rules out the series being an artifact of a different unit or window.

        Thirty-five days back: enough for a seven-day window whatever the timezone, plus
        slack so a restart never renders the week short.

        ``monthly`` False leaves the monthly series alone, for a caller that is reading it
        alongside this one — the first seed, and a manual refresh whose seed is retrying it.
        """
        # When a shower ends in a new local calendar month after initial seed, re-read the
        # monthly series once so the newly completed month is locked into `self.usage`
        # before the 35-day daily window rolls past its start — and while the monthly
        # series has never been read successfully at all, read it here too, rather than
        # leaving it lost until a restart.
        #
        # ⚠️ **Before the daily series, not after it** (2026-10-16). Each month counts the
        # larger of the two series' figures (`water._month_totals`), and the new daily
        # window can start part-way into last month — so swapped in first, it lowered last
        # month, and `Water Used This Year` with it, until the monthly read landed. With the
        # monthly figure in place first the swap cannot lower a month; only a monthly read
        # that fails leaves last month short, until the next one answers.
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
        # Keep the prior daily series if a post-shower read transiently returns `{}`, so
        # `Water Used Today` and `Water Used This Week` do not blank to `unknown`.
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

        Its outlet layout and records — without them every outlet setting is refused — and
        its presets, without which the Favorite list is empty. The stream's first connect
        reads such a valve whole rather than only its live state; see
        `KohlerKonnectCoordinator._async_read_missed_live_state`. **Whether the reads
        answered**, not whether what they fill is empty (2026-10-16): a message replayed
        from before the devices were known filled one and hid a read that had failed, and
        a valve with no presets was read whole at every start.
        """
        return not (
            self._topology_checked and self._settings_read and self._presets_read
        )

    async def async_seed(self) -> None:
        """Read this valve's state, limits and presets over REST.

        The valve half of `KohlerKonnectCoordinator._async_seed_state`, moved here
        unchanged apart from the topology read; that docstring says when it runs.
        """
        # Layout and limits first, state second — the reverse of the order the coordinator
        # used. The state read decodes the second zone's word only if the model has a
        # second zone, so a valve whose own layout differs from the entry's must have that
        # layout applied before its state is seeded, or a single-zone valve on a two-zone
        # entry starts life with a zone 2 it does not have.
        # Per-outlet limits, including `maximumRunTime`, come from `gcsadvancestate`.
        # Over MQTT they arrive only unprompted and one outlet at a time, so this read is
        # what makes them known from the start.
        #
        # Runs on every re-seed, not just the first: cheap, and it re-checks the limits
        # after a reconnect rather than trusting values that may be hours stale.
        #
        # **The independent reads start here and are awaited at the end.** Only one
        # ordering in this method is real: `gcs-settings` decides the outlet topology, and
        # `gcs-state` cannot be decoded until it has been applied (see `_apply_topology`).
        # The configuration, usage and preset reads depend on none of that, so waiting for
        # the settings/state pair before issuing them spent three extra round trips of
        # wall-clock on every cold start for no ordering benefit. Launched as tasks now,
        # they overlap the pair above; the awaits below collect them.
        background = self._seed_independent_reads()
        try:
            await self._async_seed_topology_and_state()
        except BaseException:
            # **Cancelled or failed — do not leave the reads running.** A reload that
            # cancels this coroutine mid-seed would otherwise orphan them against an entry
            # that is going away: the class of bug 0.15.1 fixed for the reconnect reseed.
            # Awaiting inside a plain `finally` would not do it — the await is cancelled
            # too — so the task is cancelled explicitly and then reaped.
            background.cancel()
            with suppress(asyncio.CancelledError):
                await background
            raise
        _raise_first(await background)

    def _outlet_limits_settled(self, serial_before_read: int) -> bool:
        """Whether a `gcs-settings` read may replace the cached outlet records.

        Not while an outlet write is in flight or began during the read, and not within
        `OUTLET_WRITE_VERIFY_DELAY_SECONDS` of the latest one: the cloud document trails a
        write by up to ~25 s, so a reseed in that window (a reconnect, a manual refresh)
        read the old scald limit or run time back into the cache, and the next outlet write
        echoed it to the valve — the revert `async_write_outlet_setting` exists to prevent.
        The write's own read-back, which waits out that window, refreshes the cache instead.
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
            # The same read says how the outlets split across the zones, which is what
            # this valve decodes and encodes every word with — its own layout, not the
            # entry's. See `_apply_topology`; once is enough, plumbing does not change.
            if not self._topology_checked:
                # Latched only once the read actually said something — see `_apply_topology`.
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
                # Through `_adopt_outlet_read`, not a plain update: past the settle window
                # the cloud can still be showing a value written from here that it has not
                # caught up with, and the cache is what the next outlet write echoes. And
                # as the valve reports it: an outlet it announced just before the read
                # stands as announced.
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
        valve read before the stream was up — see
        `KohlerKonnectCoordinator._handle_connected`. A `KohlerError` is logged and changes
        nothing; a sign-in failure is the caller's. What is applied, and what is kept, is
        `apply_rest_reply`'s to decide.
        """
        serials = dict(self._push_serials)
        # This valve's own word writes, not `_local_write_serial`, which an outlet setting
        # bumps too: one saved during the read changed no word, and keeping the words over
        # the read for it held a stopped shower's stale words (2026-10-16).
        writes = self._words_serial
        connection = self.coordinator.stream_connects
        # When the read was sent, so a `Disconnected` it brings back cannot overrule a
        # message the valve sent after it — see `CloudConnectionWatch.note_rest_payload`.
        requested_at = time.monotonic()
        try:
            payload = await self.client.async_get_gcs_state(self.gcs_device.device_id)
        except KohlerError as err:
            _LOGGER.debug("Could not seed GCS state: %s", err)
            return
        if self.cloud_watch is not None:
            # CLOUD CONNECTION WATCH. `connectionState` is a sibling of `state` in this
            # payload, and `apply_rest_state` reads only `state` — so without this line the
            # field we already paid for is discarded, and the sensor sits at `unknown` until
            # a trigger fires hours later. Free: no extra request, and it does not consume
            # the check cooldown. Not about the valve's state, so taken whatever is kept.
            #
            # `notify=False` — this runs during `async_setup`, before the platforms exist;
            # every caller of this method pushes a snapshot of its own.
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
        # Warm-up gets its own call rather than thirty lines here: a mode that moved while
        # the stream was down reaches nothing else, and the reasoning about why belongs
        # beside the rest of the warm-up machinery. See `WarmupManager.note_seeded_mode`.
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

        ⚠️ **A push that lands while the read is in flight is newer than the reply**
        (2026-10-16). `gcs-state` is a cloud document that trails the device by seconds,
        and applying it put back whatever it last held: a shower stopped, the stream
        reconnected, and the reseed's reply still showed the outlet open — and every later
        write rebuilds both words from that. So what a push carried during the read stands.

        ⚠️ **Only what that push carried** (2026-10-16). The guard began by dropping the
        whole reply for any such push, and the two messages carry different halves of it:
        `GCS_WARM_STS` only the warm-up mode, `GCS_SOLO_STS` everything but. A warm-up
        announcement during a reconnect's read left a stopped shower shown running; the
        running shower's own reports during one hid a warm-up disable made while the
        stream was down — the very change auto-restore exists for. Each group the pushes
        moved (`_push_serials`) keeps its pushed values; the rest of the reply applies — and
        since the same day field by field, by what each message carried.

        **The zone words are kept, too, when a write from here can be newer** — one sent
        while the read was in flight, or one whose overlay still stands in for the valve's
        report (`_sent_words_pending`) — and the session fields reported with them. The
        reply can predate the write, and ending the overlay with it put back the words the
        write had replaced, which the next write then built on (2026-10-16).

        **And when the valve reported its words just before the read was sent**: the
        document trails the valve by seconds (`_GCS_STATE_TRAILS_SECONDS`), so a reply to a
        read sent that soon after a report can predate it — a Stop at the panel, reported a
        second before a reconnect's read, put back as running (2026-10-16).
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

        `_valve_baseline`'s test, without its side effect of dropping an overlay that has
        lapsed: armed, inside the hold, and no report since the write. `_sent_words` alone
        will not do — it is cleared only when the next write asks for a baseline, so it can
        outlive its hold by days, and a reseed that trusted it would never again correct
        the words.
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

        The valve's counterpart of the controller's `hub-diagnostics/{id}/active` read, and
        run when it is — on every seed (setup, each reconnect, a manual `update_entity`) and
        twice a day; see `KohlerKonnectCoordinator.async_refresh_faults`.

        ⚠️ **Until 2026-10-16 this log was read only when someone built a diagnostics
        report**, so a fault the valve was reporting never reached its Problem sensor. The
        owner's report showed Shower Left's log holding an active `Algorithm` error `37` on
        `Valve 1` — present since 2026-04-21 — while that Problem sensor read off: it looked
        only at the status word's error flag and `currentSystemState`, neither of which had
        moved.

        Kept: entries the app would show (`errorCode` neither `"0"` nor empty) that are
        still `isActive`. Only the code, area, valve label and timestamp are copied out —
        the log's `id`, `deviceId` and `details` can carry device and account identity.

        **A failed read changes nothing.** The client answers `{}` for a read it could not
        make, which has no `errorDetails` list, and that is not the same answer as "no
        faults": clearing on it would switch the sensor off every time Kohler hiccups. Nor
        does a failure here fail the seed — the other reads report a dead sign-in.
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
        """Record the mode in force when the warmup journal opened. See the comment inside."""
        # BASELINE: what mode was in force when this file opened, from the REST seed above.
        #
        # Without it a journal is unreadable on its own. **The valve never volunteers its
        # warmup mode on connect** — measured 2026-08-21 over all 74 raw captures: 17 hold a
        # `GCS_WARM_STS` at all, and in 16 the first one lands between 137 s and 7 h after
        # the log opened. The 17th, at +1.7 s, only looks like a connect announcement: it is
        # the echo of our own write on 08-21 at 03:40:10Z, which landed in a file that had
        # opened 1.7 s earlier *because* persisting the mode reloaded the entry — the bug
        # `cde9bf4` fixed, so that artefact cannot recur.
        #
        # So a file that records a `disabled` an hour in has no record of what was displaced
        # or since when, and an empty file cannot be told apart from a broken one.
        #
        # Called from `async_setup`, once its seed is done, rather than from
        # `_async_seed_state`, which runs on every reconnect too: this is the one place that
        # happens exactly once per file.
        self.warmup._warmup_journal(
            "baseline",
            mode=self.gcs_state.warmup_mode,
            auto_restore=self.warmup_auto_restore,
            restores_to=self.last_warmup_mode,
            source="rest",
        )

    # ------------------------------------------------------------------ #
    # Moved from the coordinator, 2026-09-08 — bodies unchanged
    # ------------------------------------------------------------------ #
    @property
    def outlet_run_times(self) -> dict[int, int]:
        """Each outlet's `maximumRunTime` in seconds, keyed by **1-based** outlet number.

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

        The valve reports this per outlet but **times it per zone**, so there is no single
        "the" limit for a zone unless its outlets happen to agree.

        ⚠️ **They do not always agree — observed 2026-09-10.** One of the owner's two valves
        read 3600 s on all three outlets at 08:38 and then 1800 s on two of them with 3600 s
        still on the third at 15:22. There is one duration setting; the app writes it one
        outlet at a time and stops at the first failure, so a dropped call strands the old
        value on the outlets it never reached. Every distinct value is returned, because the
        valve really may enforce either.

        `maximumRunTime` only. A preset's own `time` is a second, independent limit that
        ends a preset-driven session early when it is lower; it is not included here.
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

        A zone is running when it has outlets open and is not paused (`0x40`), which is how
        the valve's own timer reads it.
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

        Every write rebuilds both zones, so its baseline decides what it keeps. The reported
        words lag a write by the echo's 1.1-2.1 s, and a write built on them inside that
        window silently reverted the one before it — GitHub issue #1. So for
        `_sent_hold_seconds` the words this integration last sent stand in for the report.

        🚨 **A report inside that window narrows them; it never widens them** (2026-10-16).
        Until then any report ended the overlay, on the reasoning that one which disagrees is
        the valve's own answer. But a report is not always newer than the write: the late
        echo of an *earlier* write, or a status the valve sent before it applied this one,
        arrives just the same — and building on it **opened water**. Running at ``0x01``, a
        Stop sent ``0x00``, a report from before the Stop still said ``0x01``, and the next
        temperature change went out as ``0x01``; outlet 1 on, outlet 1 off, the first one's
        late echo, then outlet 2 on went out as ``0x03``. Content cannot tell a stale report
        from a change made at the wall — a Stop at the panel and the late echo of a Stop
        sent from here are the same word — so neither is trusted to add anything: for each
        zone the valve has reported since, the outlets are those open in **both** the sent
        word and the report, and the zone is paused if **either** is. A Stop stays a Stop
        until the hold ends, a stale report cannot reopen an outlet or lift a pause, and a
        Stop or pause at the wall still holds. The temperature is the report's, as it was.
        The cost lands on the safe side: an outlet opened at the wall inside those seconds
        is closed by a write built on them, and a stale report can close one this
        integration had just opened.

        Remembering recently sent pairs to recognise their late echoes was considered and
        left out: it could only ever be used to *keep* an outlet a report shows closed —
        the one thing a Stop at the wall that happens to match an earlier write must never
        get. Narrowing is already safe for every report, stale or not.

        A report is told apart by identity, not content: `GcsState` decodes every
        `GCS_SOLO_STS` whose words decode as a pair, and every REST seed, into new word
        objects, so a word other than the one in place when the write was asked for
        (`_sent_over`, read before its POST) means the valve has spoken since. A report with
        a garbled or missing word leaves its own zone's object as it was
        (`GcsState._apply_solo`), so it cannot end that zone's hold.

        🚨 **During a warm-up the report describes the warm-up, not the shower**, so the
        outlets come from `_warmup_shower` instead, or the write is refused with a
        `HomeAssistantError` when that shower was not sent from here. ``outlets`` says the
        caller takes outlets from this baseline; a Stop, which names every zone's, does not,
        and is never refused.
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
        # The shower's outlets go only to a zone that is still warming. One reported stopped
        # or paused since — at the panel, in the app — keeps what it reports: taking the
        # shower's assignment there re-opened a zone someone had just closed (2026-10-16).
        return tuple(
            self._with_assignment(word, own)
            if now is not None and now.outlet_mask and not now.paused
            else word
            for word, own, now in zip(baseline, shower, reported, strict=True)
        )

    @staticmethod
    def _narrowed(word: Any, other: Any) -> Any:
        """``word`` with only the outlets ``other`` also has open, paused if either is.

        The one merge `_valve_baseline` and `_async_send_words` make, in the direction that
        never opens water: it can close an outlet or keep a pause, nothing else. ``word``
        supplies every other field, and is returned as it is where there is no ``other`` —
        never ``other`` in its place, whose outlets would then be taken whole.
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

        🚨 **The masks a warming valve reports are the warm-up's**, not the shower's: every
        outlet of its warm-up set (``0x07``/``0x03`` on the reference install), whatever
        was asked for. A write during the warm-up hijacks it onto the outlets the write
        names (2026-09-06 live test, session 24 §2c), so one built on those masks — a
        temperature or flow change, an outlet toggle — turned the warm-up into a shower on
        every warm-up outlet, until 2026-10-16.

        The words count as that shower when they left an outlet running, nothing has decided
        the valve's words since (`_note_local_write` drops them), they were sent less than
        `RESUME_DEADLINE_SECONDS` ago — longer than any warm-up measured — and the valve has
        run without a break since: some zone has been flowing (`zone_flowing_for`) since no
        later than `WARMUP_WINDOW_SECONDS` after they were sent, when a warm-up they started
        shows itself. A shower stopped and started again at the panel meanwhile restarts
        that clock. Anything else is a warm-up started elsewhere — the panel, the app, a
        favorite — whose outlets nothing here knows, and None.
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
        """A zone's flow as chosen — percent of its own ceiling — restated for `encode_word`.

        `encode_word` reads percent against the 200 default, so a zone whose ceiling is
        another byte needs the percent re-expressed: the byte it means on **this** zone, as
        the percent that produces that byte at 200. Identity wherever the ceiling is 200,
        which is every device in the corpus. Done here, where the word is built, so every
        write converts `zone_flow` the same way — it used to be converted once in the Flow
        number and stored converted, so the idle entity showed a number nobody chose and
        nothing else could tell which scale it was on.
        """
        _, high = self.gcs_state.zone_flow_limits(zone)
        return flow_byte_to_percent(flow_percent_to_byte(percent, high), FLOW_BYTE_MAX)

    async def _async_send_words(self, word1: str, word2: str) -> int:
        """POST a pair of words and arm the overlay; return the serial this write took.

        The caller holds `_valve_write_lock` and built the words from `_valve_baseline`.
        The overlay is armed only once the POST has landed, and only if nothing else decided
        the valve's words meanwhile: a controller command moves them without passing
        through here, and counts against `_words_serial`
        (`KohlerKonnectCoordinator._note_local_write`). An outlet setting does not.

        **The report the words are sent over is read before the POST** (2026-10-16), not
        once it has landed: a Stop pressed at the panel and reported while the POST was in
        flight used to become that report, so it read as "nothing since" and the next
        write went out on the words sent — water back on.

        🚨 **A POST that fails, or is cancelled, puts the words before it back**
        (2026-10-16). They used to go with it, so a retry built on the report instead —
        lagging, inside the echo's round trip — and closed the outlet the last command had
        just opened. But a POST that failed may still have reached the valve (a timeout
        after the cloud took it), so what comes back is what this write was built on —
        those words, or the report when none stood in — **narrowed by it**, as by a report,
        and held for `_sent_hold_seconds` from the failure: an outlet it closed stays
        closed and a pause it set stays set, so a Stop that "failed" after it landed is not
        undone by the next command, while nothing it would have opened is taken up. Not
        when something else decided the words meanwhile, whose own dropping of them
        stands.
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
                # A Stop goes the usual way even during a warm-up, so it holds afresh: the
                # warm-up can end as it lands, and its last report — the warm-up's outlets,
                # no longer warming up — became what the next command was built on
                # (2026-10-16).
                stops = words is not None and not any(
                    word is not None and word.outlet_mask for word in words
                )
                if words is not None and warming and not stops:
                    # 🚨 **Never the report during a warm-up** (2026-10-16): it shows the
                    # warm-up's whole outlet set, so narrowing it by this write made exactly
                    # the failed command's outlets, which `_warmup_shower` then handed to
                    # the next write — a change refused before now went through, onto a
                    # warm-up started at the panel. Only the shower words that started this
                    # warm-up, if they were sent from here, are narrowed, keeping the time
                    # `_warmup_shower` ages them by.
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

        The valve accepts no partial write: every command carries the complete state of
        both zones. So changing one zone's temperature means rebuilding both words from
        current state and re-sending. Anything not overridden is preserved, including which
        outlets are open — which is what makes it safe to adjust temperature mid-shower.

        This mirrors the Konnect app, which POSTs a fresh ``solowritesystem`` on every
        temperature, flow, or outlet adjustment.

        **"Current state" is `_valve_baseline`**, and writes are taken one at a time under
        `_valve_write_lock`: for a few seconds the words the last write sent, narrowed by
        any report since, so a second write inside the echo's round trip builds on the first
        rather than undoing it (GitHub issue #1, fixed 2026-10-16), while a report that
        arrives in between can close an outlet or keep a pause but never reopen anything.
        During a warm-up the outlets are those of the shower being warmed up, or the write
        is refused when that shower was started elsewhere — see `_warmup_shower`.

        **A Stop asked for after this goes first** — see `async_stop_shower`: once this
        holds the lock, it is refused with a `HomeAssistantError` if one was.

        🚨 **The pause flag is preserved too.** A zone holding a paused assignment — byte 3
        ``0x41``, outlet 1 still assigned and no water flowing — used to be re-sent as
        ``0x01``, so changing a temperature or a flow from Home Assistant **started the
        water**; a bare pause, ``0x40``, went back as a stop. A zone whose mask is not
        overridden now keeps its pause exactly as it stands. ``zone_masks`` replaces a zone's
        outlet mask and, unless ``zone_paused`` says otherwise, clears its pause — so a stop
        stays a plain ``0x00`` — while ``zone_paused`` sets the flag for a zone outright.

        **Flow never comes from the valve.** Omitting it writes the zone's ``zone_flow`` —
        the value its Flow number shows, `DEFAULT_FLOW_PERCENT` until somebody sets one —
        and never the byte the valve holds: inheriting that let the touchscreen dictate what
        Home Assistant sent, and the idle byte is not the flow setting at all (see
        `GcsState.flow_is_live`). ``zone_flow`` is Home Assistant's own remembered choice,
        so carrying *it* forward is what stops a temperature change or an outlet toggle
        silently resetting the flow; until 2026-10-16 a write that named no flow sent 100 %.
        A flow passed here is a percent of that zone's own ceiling, as the entity shows it,
        and is remembered in ``zone_flow`` once the write is accepted.

        Consequence worth knowing: adjusting temperature from Home Assistant mid-shower also
        restores the flow Home Assistant holds, if the wall panel had changed it.
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
        # Masks are per zone throughout — no global outlet numbering is involved, so no
        # model-dependent mapping can be applied wrongly here.
        current = dict(zip((1, 2), baseline, strict=True))

        def resolve(zone: int, prefix: int) -> str:
            word = current[zone]
            temperature = temperatures.get(zone)
            celsius = (
                unit_to_celsius(temperature, self.temperature_unit)
                if temperature is not None
                else (word.temperature_celsius if word else 38.0)
            )
            # Flow does NOT inherit from the current word — see `DEFAULT_FLOW_PERCENT`.
            # Carrying it forward meant every Home Assistant write silently adopted whatever
            # the touchscreen last set, which is below 100% in 31% of captured words and has
            # been as low as 8%.
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

        The escape hatch for everything the entities do not model — an outlet combination,
        flow value, or temperature the UI cannot express. It is the same endpoint every other
        control path uses; the only difference is that the caller supplies the words.

        Both words are validated with ``normalize_word`` before anything is sent. Malformed
        input is rejected locally rather than posted to a device that opens water valves, and
        the decoded meaning is logged so the journal records what was actually asked for.

        ``zone2_hex`` omitted **re-sends zone 2's current state**, so zone 2 keeps doing
        whatever it was doing. It emphatically does *not* send ``00000000``.

        That sentinel means "no valve addressed", and on a two-valve system it is measured to
        make the device **discard the entire command** — `v1=00000000 v2=11849C01` opened
        nothing, while `v1=0185C800 v2=1185C801` opened valve 2 immediately
        (`docs/protocol/gcs_valve.md`). So a blank zone 2 filled with zeroes would silently throw away
        the zone 1 word the caller had just carefully built. A valve that should stay shut
        gets a well-formed word with mask ``0x00``; only a valve that does not physically
        exist gets the sentinel, which is why a single-zone model still sends it here.

        The protocol has no partial write — every POST carries both zones — so "leave zone 2
        alone" can only be expressed by sending zone 2's own current word, which is what this
        does: its setpoint, assignment and pause as `_valve_baseline` has them, under the
        same `_valve_write_lock` as every other write. Flow follows zone 2's ``zone_flow``
        like every other write. During a warm-up started elsewhere that assignment is the
        warm-up's, so a zone 1 word alone is refused there; both words are always sent.

        Like every write, it steps aside for a Stop asked for after it
        (`async_stop_shower`).

        Returns the decoded interpretation of what was sent, so an automation or a person can
        confirm the word meant what they thought.
        """
        result, _ = await self._async_send_valve_hex(zone1_hex, zone2_hex)
        return result

    async def _async_send_valve_hex(
        self, zone1_hex: str, zone2_hex: str | None, *, requested: int | None = None
    ) -> tuple[dict[str, Any], int]:
        """`async_send_valve_hex`, also returning the serial its write took.

        For `async_custom_shower`, whose watcher must notice anything sent after its own
        write — including while this POST was in flight, which reading the serial once the
        call had returned missed. ``requested`` is the `_stop_generation` it was asked for
        under, when the caller took it; otherwise it is taken here, before the lock — nothing
        before that awaits, so it is still the moment this was asked for.
        """
        word1 = _command_half(zone1_hex, "zone1_hex")

        # A typed-in sentinel is treated exactly like a blank field on a two-valve system.
        # It cannot mean anything useful there — it addresses no valve, so the device
        # discards the whole command, taking the zone 1 word with it — and "00000000 leaves
        # zone 2 alone" is the natural reading for anyone who has seen the sentinel at all.
        # Honouring it literally would satisfy nobody's intent and void the command instead.
        if zone2_hex and zone2_hex.strip("0") == "" and self.model.uses_valve2:
            _LOGGER.info(
                "send_valve_hex: zone2_hex was all zeroes, which addresses no valve; "
                "re-sending zone 2's current state instead so the command is not discarded"
            )
            zone2_hex = None

        if zone2_hex and not self.model.uses_valve2 and zone2_hex.strip("0") != "":
            # **Refused, not silently sent.** The service form shows Zone 2 whenever *any*
            # valve on the account has one, so on a mixed account the field is offered for a
            # single-zone valve too — and the word used to be encoded and sent to a valve
            # with nothing to receive it. Found by GitHub Copilot's review of upstream #3.
            #
            # All zeroes is exempt: that is the sentinel this valve genuinely uses for "no
            # second valve", and the branch below produces it anyway. Only a word that tries
            # to *command* a zone that does not exist is a mistake worth naming.
            raise HomeAssistantError(
                f"{self.name} has one zone, so zone2_hex addresses nothing. Leave it empty "
                f"(or all zeroes) — the word {zone2_hex!r} was not sent."
            )
        word2: str | None = None
        if zone2_hex:
            word2 = _command_half(zone2_hex, "zone2_hex")
        elif not self.model.uses_valve2:
            # The only legitimate use of the sentinel: there is genuinely no second valve.
            word2 = UNUSED_VALVE_WORD

        if word1 == UNUSED_VALVE_WORD:
            # Not blocked: this is the escape hatch, the failure mode is "nothing happens"
            # rather than unexpected water, and it is a documented experiment worth being
            # able to run. But it should never be a silent surprise.
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
                # Re-send zone 2 as it stands — never the sentinel, which would risk the
                # device discarding the whole command. See the docstring.
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
        self, zone1_hex: str, zone2_hex: str, *, keep_on_after_warmup: bool
    ) -> dict[str, Any]:
        """Send one complete shower command, optionally resuming it after the warm-up pause.

        The form-driven sibling of `async_send_valve_hex`: `services.py` builds the words
        from typed fields with `encode_shower`, so both are always supplied and nothing here
        is read from the valve's last report. It writes **once**. A second write during a
        warm-up hijacks it onto the written outlets (2026-09-06 live test, session 24 §2c),
        so nothing is ever held back, delayed or re-sent on its own — except the one case the
        caller opts into:

        ``keep_on_after_warmup`` — on a valve with warm-up enabled, the valve warms up and
        then **pauses** for two minutes, just as it always does; left alone, that pause
        ends the session (`konnect/warmup_resume.py` has the corpus). With this
        set, a background watcher follows the GCS reports and, when that pause arrives,
        re-sends the same two words once. It does nothing if no warm-up follows the write, if
        the warm-up ends in a plain stop, if someone takes over at the wall, if any other
        command is sent from here in the meantime, or if a new custom shower replaces it.

        ⚠️ **The watcher's serial is the one this write took**, captured beside its own
        `_note_local_write`, not read once the call returns: a command sent while this POST
        was in flight used to go unnoticed. And any watcher already pending is cancelled
        again just before the new one is stored — two overlapping calls each found nothing to
        cancel up front, and the second's assignment orphaned the first, which could later
        resume the older shower.

        🚨 **A Stop asked for after this outranks it** (2026-10-16): the write steps aside
        if one is waiting behind it, and the watcher never resumes once one has been asked
        for, even before it has taken the lock — see `async_stop_shower`.
        """
        requested = self._stop_generation
        self._cancel_custom_shower("a new custom shower was sent")
        result, serial = await self._async_send_valve_hex(
            zone1_hex, zone2_hex, requested=requested
        )
        self._cancel_custom_shower("a new custom shower was sent")
        if keep_on_after_warmup:
            # Tracked, so an unload that came while the write was on its way gets no
            # watcher — it lived on for up to three minutes after one (2026-10-16).
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

        Woken by every coordinator update and once a second regardless, so the deadlines in
        `WarmupResume` are honoured even if the valve goes quiet. Reads only the valve's own
        state — `warmUpStatus`, the pause flag, the masks and the primary word's
        at-temperature bit, the evidence that a pause is the warm-up's own end rather than
        a stop pressed in the app or at the panel — never the controller's.

        ``serial`` is the `_local_write_serial` the custom shower's own write took, and
        ``requested`` the `_stop_generation` it was asked for under: anything sent since, or
        a Stop asked for since — counted before it waits for the lock, so a Stop queued
        behind a write in flight counts too — and this gives up rather than resume.
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
                        # Again under the lock: a write that was queued for it counts
                        # itself only once it holds it, so the check above could not see
                        # it — and resuming after it would undo it.
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

        ``outlet`` is 1-based **within that zone**, matching how the hardware and the API
        address it. Every other outlet, in both zones, is preserved. An outlet the valve's
        current layout does not have is refused rather than written as a stray mask bit.

        ``flow`` is the flow to write for this zone. Omitting it falls back to
        `async_apply_valve`'s rule and writes the zone's ``zone_flow`` — the Flow number's
        value, read when the write is built rather than when it was asked for, so a flow
        change queued ahead of this toggle is not undone by it.

        **Why this is not "inherit the valve's flow".** `async_apply_valve` never carries
        the valve's flow byte forward, so that nothing silently adopts whatever the
        touchscreen last wrote. That rule is about the *valve's* byte, not about a value Home
        Assistant itself holds. The Flow entity's value is one the user set, so a
        subsequent outlet toggle should not silently undo it. The valve's idle byte is still
        never read here; see `GcsState.flow_is_live` for why it cannot be trusted.

        🚨 **A paused zone** (byte 3 ``0x4n``: an assignment held, no water) reads as every
        outlet off — `GcsState.zone_outlets` clears a paused assignment — so the toggle is
        given the meaning that view implies. **On** opens only the outlet asked for and
        clears the pause; it does not also open the outlets the pause was holding, which no
        switch showed as on. **Off** keeps the zone paused and drops the outlet from the
        assignment it would resume to. Either way nothing runs that the user did not just
        turn on.

        The assignment toggled is `_valve_baseline`'s, so a report since the last command
        can only have narrowed it, and during a warm-up it is the shower's rather than the
        warm-up set's — or the toggle is refused (`_warmup_shower`). A Stop asked for after
        this goes first (`async_stop_shower`).
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

        One call: the valve runs the preset itself, so no ``solowritesystem`` follow-up is
        needed. Verified live — the body is ``{preset, action}``.

        A preset only applies the zones it opens an outlet on; a zone left at mask ``0x00``
        keeps whatever setpoint it already had, so this cannot be used to set an idle zone's
        temperature.

        Taken in turn with the word writes (`_valve_write_lock`), since it changes the same
        words, and it drops their last-sent overlay: the preset decides them now. Like them,
        it steps aside for a Stop asked for after it (`async_stop_shower`) rather than start
        water the Stop then has to end.
        """
        requested = self._stop_generation
        async with self._valve_write_lock:
            self._refuse_if_stopped_since(requested)
            self._note_local_write()
            with self.coordinator.command_errors(VALVE_OFFLINE):
                await self.gcs.async_activate_preset(preset_id, True)

    async def async_start_default_shower(self) -> None:
        """Run the default shower from preset 1's stored valve words. **This runs water.**

        Preset 1 is where the valve keeps its default outlets, temperature and flow, and
        Kohler's own clients never run it as a preset: Simple Start in the Konnect app
        reads its words and sends them as one ``solowritesystem`` (see
        `SHOWER_ON_PRESET_ID`). This does the same — one write, built by
        `preset_words_to_pair` at the stored flow, so nothing activates or writes preset 1
        and only the outlets' ``maximumRunTime`` ends the shower.

        The words are `default_shower_words`'; when there are none, refused with its
        reason, and nothing is sent.

        A whole shower like `async_custom_shower`'s, so it is never built on the valve's
        last report and goes out during a warm-up too. Like every write, it steps aside for
        a Stop asked for after it (`async_stop_shower`).
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
        `ValveHexError` saying why there are none — preset 1 not reported, holding no word,
        a word failing validation, or opening no outlet. The reason never names the valve:
        it is also the diagnostics report's `refused_because`, and a report never copies a
        name the owner chose. Shared by `async_start_default_shower` and that report, so
        the report says exactly what the switch would do.
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

    async def async_restart(self) -> None:
        """Reboot the valve — the app's Restart Product. **Stops any running water.**

        ``valvereset {reset: "productRestart"}``. In turn with the word writes, like a
        preset, so a write still in flight cannot land after the reboot it preceded.
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

        Same ``controlpresetorexperience {preset, action}`` body as a preset — what Konnect
        3.0.6's current screens send for an experience id. App-confirmed; this integration
        long held that the valve ignores it, on no recorded test. Serialised with the word
        writes for the same reason as a preset; starting one steps aside for a Stop asked
        for after it, as a preset does, while stopping one goes ahead regardless.
        """
        requested = self._stop_generation
        async with self._valve_write_lock:
            if on:
                self._refuse_if_stopped_since(requested)
            self._note_local_write()
            with self.coordinator.command_errors(VALVE_OFFLINE):
                await self.gcs.async_activate_preset(preset_id, on)

    async def async_stop_shower(self) -> None:
        """Stop the water: mask byte ``0x00`` on both zones, **not** the ``0x40`` pause.

        Konnect 3.0.6 stops with ``0x40`` (the pause bit, no outlets); the Anthem Plus
        controller stops with ``0x00``. Both leave the valve idle. This used to pause, and
        moved to ``0x00`` on 2026-08-13 so that a stop from Home Assistant could never be
        mistaken for the valve's own run-time cutoff, which also pauses. Endless Shower,
        which needed that distinction, was removed on 2026-10-08; ``0x00`` stays because it
        is the form verified live from here.

        Still built by `async_apply_valve`'s body rather than `GcsDevice.async_turn_off()`,
        which would write a flat 38.0 °C to both zones — this preserves each zone's own
        setpoint while clearing its outlets. Both masks are overridden and no pause is
        passed, so a zone that was paused is stopped with a plain ``0x00`` like the other —
        and since it takes no outlet from anything reported, it is never refused, warm-up
        or not.

        Queued behind the write in flight (`_valve_write_lock`) rather than racing it: a
        stop that overtook an open would leave the water running once the open landed. That
        POST is never cancelled — whether it reached the valve could not then be known, and
        the Stop follows it either way.

        🚨 **But no longer behind everything else queued** (2026-10-16). The lock is
        first-come, first-served and each holder keeps it for a whole POST — up to 30 s,
        about a minute with a sign-in refresh — so a Stop waited behind every command asked
        for before it, any of which could start water. The Stop is now counted the moment it
        is asked for (`_stop_generation`, before anything is awaited), and every command
        asked for before it — a word write, a favorite or experience starting — steps aside
        when its turn comes, with a `HomeAssistantError` for whoever asked
        (`_refuse_if_stopped_since`); the custom-shower watcher gives up instead of
        resuming. Commands asked for after the Stop go ahead as usual, behind it. A Stop
        that is itself cancelled while it waits still counts: what was asked for before it
        stays unsent, which is the side to err on.
        """
        self._stop_generation += 1
        async with self._valve_write_lock:
            await self._async_apply_valve_locked(
                temperatures={}, flows={}, zone_masks={1: 0, 2: 0}, zone_paused={}
            )

    # ------------------------------------------------------------------ #
    # Warm-up
    #
    # The machinery lives in `warmup_manager.WarmupManager` — thirteen methods and six
    # pieces of state, all of them about one setting. What stays here is the surface the
    # rest of the integration already used, unchanged: entities, services and diagnostics
    # call these names and were not touched by the 0.10.0 move.
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
        # `update_interval=SCAN_INTERVAL` disables interval polling entirely. Shower state is
        # push-only: MQTT carries every change, and the REST reads happen on two *events* —
        # setup, and every MQTT reconnect — rather than on a clock. (Firmware availability,
        # and the fault logs beside it, are the one exception, read twice a day; see
        # `FIRMWARE_CHECK_INTERVAL`.)
        #
        # ⚠️ **That was one event short of the truth until 2026-08-21.**
        # `async_config_entry_first_refresh()` runs immediately after `async_setup()` and the
        # base class turns it into a third read of everything. `_async_update_data` now
        # short-circuits that one, so the sentence above is enforced rather than merely
        # intended — see the comment there before removing it.
        #
        # `_async_update_data()` still exists and still works; with no interval it runs only
        # when something asks, which is what `homeassistant.update_entity` does. That is the
        # manual refresh, and there is no automatic one.
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            config_entry=entry,
        )
        # The same object as the base class's `config_entry`, under the name the rest of
        # this integration has always used.
        self.entry = entry
        # The entry as it looked when this coordinator was built, frozen. Home Assistant
        # mutates the `ConfigEntry` object in place, so `self.entry` is a live view and
        # cannot serve as a "before" — comparing it against the entry compares an object
        # with itself. `_async_update_listener` compares against this instead.
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
        # Persist a rotated refresh token the moment B2C issues one. Without this the entry
        # keeps a token Kohler has already retired, and on a push-only install nothing else
        # writes it back for hours — see `KohlerAuth._async_token_request`.
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
        # account. See `Valve` for what each one carries — everything that used to be a
        # singular `gcs_*` field here, and everything that acted on it.
        self.valves: list[Valve] = []
        self._valves_by_id: dict[str, Valve] = {}
        # Every Anthem Plus controller on the account, in the order the cloud lists them,
        # plus the same objects keyed by device id for envelope routing. Empty on a
        # valve-only account. See `Controller` for what each one carries.
        self.controllers: list[Controller] = []
        self._controllers_by_id: dict[str, Controller] = {}
        # Every faucet on the account, each with its own polling coordinator, plus the same
        # keyed by lowercase device id for envelope routing: the faucet integration this
        # merged with matched faucet messages without regard to case, and so does this.
        self.faucets: list[FaucetCoordinator] = []
        self._faucets_by_id: dict[str, FaucetCoordinator] = {}
        # What the faucets keep across restarts — cleared leaks, the leak alert, the safety
        # limit (its deadline, when it was set, whether the water was seen running) and
        # whether instant updates have proved themselves — in one file per entry, keyed by
        # device id.
        self._faucet_store: Store[dict[str, Any]] = Store(
            hass,
            FAUCET_STORAGE_VERSION,
            FAUCET_STORAGE_KEY.format(entry_id=entry.entry_id),
        )
        self._faucet_data: dict[str, Any] = {}
        # The faucets' first reads, started early in `async_setup` so they overlap the
        # showers' seed, and collected at its end. Held so a failed setup can cancel them.
        self._faucet_first_refresh: asyncio.Task | None = None
        # When those reads finished (monotonic) — what decides whether the first connect
        # reads the faucets again; see `_faucets_just_read`.
        self._faucets_read_at: float | None = None
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
        # REPORT LOG: the consumer-side capture behind the "Report Log" switch — one file
        # per switch-on, appended across restarts. See `konnect/report_log.py`.
        self.report_log: ReportLog | None = None
        #: The reseed spawned on an MQTT connect — after the first, only the live-state
        #: catch-up. Held so `async_shutdown_stream` can cancel it — it outlives an unload
        #: otherwise, see `_handle_connected`.
        self._reseed_task: asyncio.Task | None = None
        # A connect came while that reseed was running, so it reads everything again.
        self._reseed_again = False
        # The twice-daily firmware check — the one clock in an otherwise push-only design,
        # because nothing pushes "an update is available" — and the fault reads that ride on
        # it. Cancelled on unload, the check in flight included: `_firmware_task` holds
        # whichever one is running.
        self._firmware_unsub: Any = None
        self._firmware_task: asyncio.Task | None = None
        # Local midnight tick: re-renders date-sensitive usage entities (`Water Used Today`,
        # `Water Used This Week`, etc.) with zero network calls when the calendar day turns.
        self._midnight_unsub: Any = None
        # One-shot: `async_setup` seeds, then `async_config_entry_first_refresh()` runs
        # milliseconds later and would seed the identical state all over again. See
        # `_async_update_data`.
        self._seeded_during_setup = False
        # WARMUP JOURNAL: built in `async_setup`, once `hass.config.path` is usable.
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

        ⚠️ **The stream starts as soon as the sign-in has worked, and the reads run while it
        connects** (2026-10-16). It used to start only once every device had been seeded, so
        its registration and connection queued behind the account read and each device's
        settings and state — about five cloud round trips in a row, 12.7 s of
        `config_entry_setup` on the owner's Raspberry Pi — and the connect then re-read
        every device in full, seconds after setup had: each valve was read twice per start.
        The stream needs nothing the reads produce, so it overlaps them now, and its first
        connect re-reads only what setup read before it was up; see `_handle_connected`.

        **Home Assistant stopping part-way through ends it** (2026-10-16). The stop listener
        in `__init__.py` shuts this coordinator down while a read here is still awaited, and
        what followed started the stream, the cloud watches and the firmware timers again
        with nothing left to stop them. Each step that starts something checks first; see
        `_raise_if_closed`.
        """
        # One identity for the life of this config entry. Generated on first setup and
        # persisted, so restarts and reconnects reuse it instead of leaving a trail of
        # dead registrations on the Kohler account.
        mobile_device_id = self.entry.data.get(CONF_MOBILE_DEVICE_ID)
        first_registration = not mobile_device_id
        if first_registration:
            mobile_device_id = uuid.uuid4().hex[:16]
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_MOBILE_DEVICE_ID: mobile_device_id},
            )

        # The captures and the warm-up journal come before the stream, which writes to the
        # first two from its first message — and a warm-up announced during setup belongs
        # in the third. Local files only, so they cost no round trip.
        #
        # RAW MQTT LOG: constructed unconditionally and switched on at runtime, so capture
        # can be started from the UI mid-session without a reload. Nothing touches the disk
        # until a message arrives while it is on. See `konnect/raw_log.py`.
        self.raw_log = RawMqttLog(
            self.hass.config.path(RAW_MQTT_LOG_DIR),
            forced=ENABLE_RAW_MQTT_LOG,
            max_bytes=RAW_MQTT_LOG_MAX_BYTES,
            keep_files=RAW_MQTT_LOG_KEEP_FILES,
        )
        # Open the file up front when capture is already on, so it is findable immediately
        # rather than after the next push — which can be hours away. Executor, not the loop:
        # this creates a directory and opens a file.
        await self.hass.async_add_executor_job(self.raw_log.prepare)

        # REPORT LOG: the consumer capture, in the integration's own folder (owner's
        # choice — see the const.py section). The options key holds the active episode's
        # name; its presence here means the switch was on when Home Assistant stopped, so
        # re-attach to the SAME file — a capture of "it breaks when I restart" must not
        # lose the interesting part to the restart itself.
        self.report_log = ReportLog(
            os.path.join(os.path.dirname(__file__), REPORT_LOG_DIR_NAME),
            max_bytes=REPORT_LOG_MAX_BYTES,
        )
        episode = self.entry.options.get(CONF_REPORT_LOG_FILE)
        if episode:
            await self.hass.async_add_executor_job(self.report_log.resume, episode)

        # WARMUP JOURNAL: in the same directory as the raw capture, on the same clock, so
        # the two interleave — see `WARMUP_README`. Its baseline record waits for the seed.
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
            # Signed in first, on its own. The stream's registration and the account read
            # both wait for the token anyway, so this adds no round trip — and a sign-in
            # that fails fails setup before the stream has started, rather than leaving it
            # retrying behind a setup that gave up.
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
            # Kohler unreachable, not a bad credential — retry setup, do not ask the user
            # to sign in again.
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
        faucets = customer.faucet_devices
        if not valves and not controllers and not faucets:
            raise ConfigEntryNotReady("No supported Kohler devices on this account")

        # Every valve, not the first one. Each carries its own state, zone clock, warm-up
        # restore, cloud watch and settings — see `Valve`. Each starts from its own layout
        # where one was read earlier this run, else the entry's; see `_known_model`.
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
        # Every controller, not the first one. Each gets its own command surface and its
        # own state, both keyed by its device id: the one account-level MQTT stream carries
        # messages for all of them, and `_handle_envelope` sorts them by that id. Each
        # starts from the layout it reported earlier this run, else the entry's —
        # `_async_seed_state` replaces it with what its own configuration says.
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
        # Every faucet, each with its own coordinator. They poll on their own clock, so
        # they are not seeded with the showers: each takes its own first read.
        names = faucet_names(faucets)
        self.faucets = [
            FaucetCoordinator(
                self.hass, self.entry, self, device, names[device.device_id]
            )
            for device in faucets
        ]
        self._faucets_by_id = {f.device_id.lower(): f for f in self.faucets}
        if self.faucets:
            self._faucet_data = dict(await self._faucet_store.async_load() or {})
            for faucet in self.faucets:
                faucet.load(self._faucet_data.get(faucet.device_id) or {})
        self._raise_if_closed()
        # The devices are known, so what the stream delivered before they were goes to
        # them now — after each faucet's stored state is back, which an event replayed
        # first would be overwritten by.
        early, self._early_envelopes = self._early_envelopes, None
        for envelope in early or ():
            self._handle_envelope(envelope)
        if self.faucets:
            # **Started now, collected at the end of this method** (2026-10-16). A faucet's
            # first read is half a dozen round trips in a row — state, configuration,
            # presets, firmware, two usage series — and it used to begin only once every
            # shower had been seeded and the stream started, in `async_setup_entry`. None of
            # it depends on the showers, so it overlaps all of that instead: the owner's
            # Raspberry Pi measured 14.1 s of `config_entry_setup` for two valves and a
            # faucet. Not a first refresh that can fail the entry: a faucet Kohler cannot
            # answer for right now should not take the showers down with it — its entities
            # show unavailable and it retries on its own clock.
            self._faucet_first_refresh = self.hass.async_create_task(
                self._async_first_refresh_faucets()
            )

        # What a run before this one warned of about an outlet setting has gone with it:
        # nothing set from here is pending now, and the read below says what the valve has.
        for valve in self.valves:
            valve.forget_outlet_write_issues()
        self._seed_started_at = time.monotonic()
        try:
            await self._async_seed_state()
        except AuthUnavailable as err:
            # Same split as the customer read above. The seed swallows `KohlerError` per
            # read ("failures for one device do not blank the other"), but the token layer
            # under every read raises `AuthError`, which is not a `KohlerError` — left bare,
            # a rejection here escaped `async_setup_entry` as an unhandled exception: no
            # reauth prompt, no retry, an entry stuck on "Failed to set up". Found 2026-08-21
            # while proving the startup-read fold; fixed 2026-08-22.
            raise ConfigEntryNotReady(f"Cannot reach Kohler: {err}") from err
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        self._raise_if_closed()
        # The mode the seed found, before anything read since can be journalled over it.
        for valve in self.valves:
            valve.journal_baseline()
        # `async_config_entry_first_refresh()` follows immediately in `async_setup_entry` and
        # would repeat every read above for nothing. Claimed here, spent in
        # `_async_update_data`.
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
            # Arms trigger B's countdown. Nothing is asked of Kohler until the valve has
            # actually been quiet for the full interval, and any valve message resets it.
            valve.cloud_watch.async_start()

        # FIRMWARE: once now, in the background so setup does not wait on it, then every
        # `FIRMWARE_CHECK_INTERVAL`. Release availability is the one thing no message
        # announces, so this is a deliberate exception to "REST on events, never a clock".
        self._firmware_task = self.hass.async_create_task(self.async_refresh_firmware())
        self._firmware_unsub = async_track_time_interval(
            self.hass, self._async_firmware_tick, FIRMWARE_CHECK_INTERVAL
        )
        self._midnight_unsub = async_track_time_change(
            self.hass, self._async_midnight_tick, hour=0, minute=0, second=1
        )

        # The faucets' first reads, started before the seed. Collected here because the
        # platforms build the faucets' entities from them as soon as this returns.
        if (refresh := self._faucet_first_refresh) is not None:
            await asyncio.wait({refresh})
            self._faucet_first_refresh = None
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

        While `async_setup` runs that means Home Assistant is stopping: its stop listener
        has shut this coordinator down, and anything set up after this point — the stream's
        work, the cloud watches, the firmware and midnight timers — would run on with
        nothing left to stop it. Not ready, rather than an error: nothing is wrong with the
        account, and Home Assistant does not retry an entry while it stops.
        """
        if self.closed:
            raise ConfigEntryNotReady("Home Assistant is stopping")

    async def _async_first_refresh_faucets(self) -> None:
        """Each faucet's first read, all at once. See where `async_setup` starts this."""
        await asyncio.gather(*(faucet.async_refresh() for faucet in self.faucets))
        self._faucets_read_at = time.monotonic()

    @callback
    def _first_connect_late(self) -> bool:
        """Whether the stream's first connect came long after setup's reads went out.

        Then what they read — outlet settings, presets — may have changed unannounced in
        between: a first start that failed connects on the back-off, up to minutes later.
        🚨 **Read whole then** (2026-10-16): only `gcs-state` was, so a scald limit lowered in
        the app meanwhile stayed at its old value in the cache, and the next outlet setting
        written from here sent that old value to every outlet.
        """
        seeded, connected = self._seed_started_at, self._first_connect_at
        return (
            seeded is None
            or connected is None
            or connected - seeded > _FIRST_CONNECT_GRACE_SECONDS
        )

    def _faucets_just_read(self) -> bool:
        """Whether the faucets' first reads are still running, or finished only just now.

        What spares them a second read on the stream's first connect; see
        `_handle_connected`. "Just now" is within one idle poll: a faucet polls at that pace
        until the stream has proven it reports the faucet, so a read that recent is as
        fresh as its own clock would keep it anyway.
        """
        if self._faucet_first_refresh is not None:
            return True
        read_at = self._faucets_read_at
        return (
            read_at is not None
            and time.monotonic() - read_at < FAUCET_SCAN_INTERVAL_IDLE.total_seconds()
        )

    @callback
    def _known_model(self, device_id: str) -> ValveModel:
        """The layout a device starts from: its own, if read earlier this run, else the entry's.

        See `_async_note_topology`, which records it. A device whose layout read fails at
        setup would otherwise fall back to the entry's layout — after a reload made
        precisely because that layout was wrong for it.
        """
        known = self.hass.data.get(_VALVE_TOPOLOGY, {}).get(self.entry.entry_id, {})
        split = known.get(device_id)
        return self.model if split is None else model_for_topology(*split)

    @callback
    def _async_note_topology(
        self, device: Valve | Controller, *, changed: bool
    ) -> None:
        """Remember a device's own layout, and reload if its entities were built from another.

        ⚠️ **A layout can change after the entities exist** (fixed 2026-10-16). The valve's
        settings read that states it is retried until it answers, so when it fails at setup
        the entities are built from the entry's layout and a reconnect's read can replace it
        minutes or hours later. The decode follows at once; the entities did not, so a
        K-28212 entry fronting a K-28210 kept zone 2's controls and sensors — reading a zone
        the valve does not have — until someone thought to reload. A controller learns its
        layout the same way, from the first configuration read that answers, and until the
        second 2026-10-16 review its entities were left as they were built, too.

        So once the platforms have loaded, a changed layout reloads the entry, **once per
        coordinator** — and the layout is remembered in `hass.data`, which outlives the
        reload, so the next coordinator starts from it (see `_known_model`). A read that
        fails at every setup therefore cannot reload again and again: the layout that caused
        the reload is the one the reload starts with. Only a genuinely different answer
        reloads once more.
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

        One place, for every valve and controller command: until 2026-10-08 each carried
        its own copy of this, and none caught `AuthError` — which is not a `KohlerError` —
        so a revoked sign-in reached the action as a traceback, and nothing asked anyone to
        sign in again. The faucets' commands always did; see `FaucetCoordinator`.

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

        Push-only removed the last thing that ran on a clock, and with it the only path that
        regularly reached ``ConfigEntryAuthFailed``. `_async_update_data` still raises it,
        but with ``SCAN_INTERVAL = None`` it fires only on a manual
        ``homeassistant.update_entity``. So without this, an expired or revoked refresh
        token leaves the entry looking healthy — MQTT down, entities frozen at their last
        values rather than unavailable, and no prompt anywhere — while the reconnect loop
        retries forever against a credential that will never be accepted.

        `async_start_reauth` is idempotent; the stream also latches, so repeated failures
        do not stack up flows.

        **Not once this coordinator is closed.** A command still in flight when the entry
        reloaded can find its refresh token already redeemed by the new coordinator, and be
        refused for it. The account's sign-in is fine; only this coordinator's copy of it is
        stale, and a prompt would send the owner to sign in for nothing.
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

        This is what replaces interval polling. The broker sends no state on connect — only
        future change events — so without a read here a reconnect would leave every entity
        holding whatever it had before the gap, with nothing to correct it until the shower
        was next used. A reconnect therefore re-seeds everything.

        ⚠️ **The first connect does not** (2026-10-16). The stream starts before the setup's
        reads (`async_setup`), so there is no gap before it: what changed after a read was
        sent is reported by the stream, provided the read went out once it was up. Every
        live-state read records the connection it was sent under, and only a device read
        before the first one is read again — its `gcs-state` or `hub-state` alone, the one
        thing a few seconds can have moved. Until then each start read every device twice,
        seconds apart, and the faucets too.

        While the setup's own seed is still reading, nothing starts here: two reads racing
        into the same state objects is what the reseed guard below exists to prevent, so
        `async_setup` starts the catch-up itself once its seed is done.
        """
        if self.closed:
            # Delivered while the stream was stopping. A reseed started now would hold this
            # coordinator's sign-in and entry for a dozen round trips after the unload.
            return
        self.stream_connects += 1
        first = self.stream_connects == 1
        if first:
            self._first_connect_at = time.monotonic()
        # Durations measured across a disconnect are meaningless — we cannot know what the
        # outlets did while the stream was down, and the gap has been as long as 11.9 hours.
        # Dropping the timings means the time-left attributes start again from the next
        # report, rather than counting across the gap with a number we made up.
        # A first connect long after setup's reads (its first start failed) follows a gap as
        # much as a reconnect does; one that came with them does not.
        seeded = self._seed_started_at
        gap = not first or (
            seeded is not None
            and time.monotonic() - seeded > _FIRST_CONNECT_GRACE_SECONDS
        )
        for valve in self.valves:
            valve.forget_timings()
            if gap:
                valve.note_stream_gap()
        # Nothing is replayed on connect for faucets either: each re-reads its state —
        # except on the first connect, when its first read is under way or only just done.
        if first and self._faucets_just_read():
            for faucet in self.faucets:
                faucet.async_note_stream_up()
        else:
            for faucet in self.faucets:
                faucet.async_push_activity(False)
        # The MQTT Connection sensors: nothing else tells them before a reseed ends, and the
        # first connect may not reseed at all.
        self.async_refresh_entities()
        if not self._setup_seed_done:
            return
        # **Held, so an unload can cancel it.** This was the one `async_create_task` in the
        # file with no reference kept, and it is the longest-running: `_async_seed_state`
        # can be a dozen REST round trips. A reload inside that window left it awaiting HTTP
        # against a coordinator Home Assistant had already discarded — and it ends in
        # `_persist_refresh_token()` (a config-entry write) and `async_set_updated_data()`
        # (a push into entities that no longer exist), which is exactly the hazard
        # `Valve._background_tasks` was built for.
        if self._reseed_task is not None and not self._reseed_task.done():
            # A second connect while the first reseed is still running: no rival, which
            # would race it over the same state objects — but what it had read before this
            # gap cannot vouch for it, so it reads everything again once it is through. It
            # used to end there, and a change in the gap waited for the next reconnect.
            self._reseed_again = True
            return
        self._reseed_task = self.hass.async_create_task(
            self._async_reseed_after_connect()
        )

    @callback
    def _handle_disconnected(self) -> None:
        """A dropped stream: say so, and let the faucets catch up and poll at their pace.

        ⚠️ **The MQTT Connection sensors are told here** (2026-10-16). Nothing else notified
        them of a drop, so they read connected through every outage — the one time they
        are for. Every entity re-renders, without the stream claiming a successful update.
        """
        if self.closed:
            return
        for faucet in self.faucets:
            faucet.async_push_activity(False)
        self.async_update_listeners()

    async def async_save_faucet(self, faucet: FaucetCoordinator) -> None:
        """Persist one faucet's state into the entry's faucet store."""
        self._faucet_data[faucet.device_id] = faucet.stored()
        await self._faucet_store.async_save(self._faucet_data)

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
                # The stream is up regardless; pushes will still arrive. Do not fail the
                # entry over a re-seed, and do not retry here — the next connect will try
                # again, unless one already has.
                _LOGGER.warning("Kohler re-seed after MQTT connect failed: %s", err)
                if credential_is_dead(err):
                    # A rejected credential is the one failure the next connect cannot
                    # fix, and this path would otherwise absorb it silently.
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

        Runs on unload, when a setup fails part-way, and when Home Assistant stops — so a
        second call finds nothing left to do and returns.
        """
        if self.closed:
            return
        # ⚠️ **First, before anything awaits** (2026-10-16). `stream.async_stop` yields
        # while paho's thread winds down, and paho can still deliver in that window: an
        # envelope that started a 60 s warm-up restore, a shower's end that started a usage
        # read, a connect that started a fresh reseed — all untracked by the time the valves
        # had been stopped, all holding this coordinator's sign-in and entry after the
        # unload. Every way in from the stream checks this flag now, and so does
        # `Valve._track` — and so does `async_setup`, which this can interrupt.
        self.closed = True
        # Before the stream: it writes to the config entry and pushes state into entities,
        # neither of which is safe against an entry that is going away.
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
        if self._faucet_first_refresh is not None:
            self._faucet_first_refresh.cancel()
            self._faucet_first_refresh = None
        # A registration still under way from setup; the stream's own stop covers the rest.
        if self._stream_start is not None:
            self._stream_start.cancel()
            self._stream_start = None
        # **The stream before the valves**, the reverse of the order this used: stopped
        # first, the valves could be handed work by whatever the stream delivered while it
        # stopped. Now nothing can reach them once it has, and `stop()` cancels anything
        # that was started in the window.
        if self.stream is not None:
            await self.stream.async_stop()
            self.stream = None
        else:
            # The captures open before the sign-in, and the stream closes them on its way
            # out — but a setup that failed before there was a stream (Kohler unreachable at
            # sign-in, Home Assistant stopping) left them open, and each retry opened
            # another raw capture file (2026-10-16). Blocking close — off the loop.
            for capture in (self.raw_log, self.report_log):
                if capture is not None:
                    await self.hass.async_add_executor_job(capture.close)
        # And a reseed that slipped in the same way, before the flag reached the handler.
        if self._reseed_task is not None:
            self._reseed_task.cancel()
            self._reseed_task = None
        for valve in self.valves:
            valve.stop()
        # The raw capture is closed by the stream's own teardown; this one has no stream to
        # ride on, so it is released here. Blocking close — off the loop.
        if self.warmup_log is not None:
            await self.hass.async_add_executor_job(self.warmup_log.close)
        # ⚠️ **Last, a sign-in refresh still in flight is let finish** (2026-10-16). It runs
        # shielded and outlives this coordinator, storing the token B2C rotates — but a
        # reload builds the next coordinator from the entry as it stands when this returns,
        # and that one's first refresh then redeems the token this one has just spent:
        # rejected, and the owner was asked to sign in again for nothing. Waiting here puts
        # the rotated token in the entry first. Bounded, so a token endpoint that hangs
        # cannot hold up an unload or a shutdown; its failure is its own caller's to report.
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
            # The stream starts before the account read (`async_setup`), so it can deliver
            # before any device exists to take a message. Held, and handed on once the
            # devices exist, rather than dropped: an event such as a faucet's leak alert is
            # not something a later read brings back.
            self._early_envelopes.append(envelope)
            return
        changed: set[str] = set()
        # Valves and controllers alike: the one account-level stream carries every
        # device's messages, and the device id says whose each one is.
        valve = self._valves_by_id.get(envelope.device_id)
        if valve is not None:
            if valve.handle_envelope(envelope):
                changed.add(valve.device_id)
            self._remember_message(envelope)
        # The one account-level stream carries every controller's messages; the device id
        # says whose this is. A message from a controller this entry does not know — one
        # added in the app since setup — falls through untouched until a reload lists it.
        controller = self._controllers_by_id.get(envelope.device_id)
        if controller is not None:
            if controller.state.apply_envelope(envelope):
                changed.add(controller.device_id)
                if envelope.code in _HUB_PUSH_FIELDS:
                    controller.push_serials[envelope.code] += 1
            self._remember_message(envelope)
            # Trigger A: a controller report of a zone ON, with the valve silent. Every
            # valve's watch hears every controller: which controller fronts which valve
            # is not knowable from the cloud, and a spurious trigger costs one
            # rate-limited read, not a verdict.
            for valve in self.valves:
                valve.cloud_watch.note_hub_envelope(envelope)
        # A faucet message goes to that faucet's own coordinator, which re-reads its state.
        faucet = self._faucets_by_id.get(envelope.device_id.lower())
        if faucet is not None:
            # Not remembered: that window is the valves' warm-up evidence, and a faucet is
            # never part of a shower.
            faucet.async_push_activity(True, parse_event(envelope))
        if changed:
            self._async_notify_devices(changed)

    @callback
    def _async_notify_devices(self, device_ids: set[str]) -> None:
        """Notify listeners of a change to these devices only.

        ⚠️ **One coordinator serves the whole account, so a plain notification re-renders
        every entity on it** — every valve's and every controller's, dozens on a two-valve
        account, for each message from any one of them, and a running shower sends a steady
        stream of them. Until 2026-10-16 that is what every message did.

        So for the length of this notification `devices_changed` names the devices the
        message was about, and the entity base (`entity._KohlerDeviceEntity`) skips the
        write for an entity on any other. Everything that is not an entity — the
        custom-shower watcher's listener — is still called; and every other notification
        (a reseed, a cloud check, a usage or firmware read, midnight) leaves it None, which
        re-renders everything as before.

        ⚠️ **Except after a failed refresh, when this re-renders everything too** (fixed
        2026-10-16). The failure left every entity on the account unavailable, and
        `async_set_updated_data` below marks the whole account available again — but only
        the entities of the device that spoke were told, so the rest stayed unavailable
        until their own device next sent something — 42 of 42 in the reproduction.
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
            # CLOUD CONNECTION WATCH. Carried here so a check result re-renders the entity
            # the same way a device push does — the value itself lives on the watch.
            "cloud_connected": {
                v.device_id: v.cloud_watch.connected for v in self.valves
            },
        }

    @callback
    def async_refresh_entities(self) -> None:
        """Re-render entities from what is already in memory, with no network read.

        For state that changes without a message arriving: a cloud reachability check, a
        usage or firmware read on its own schedule, and the calendar day turning over at
        midnight — none of which has a push to ride in on.
        """
        self.async_set_updated_data(self._snapshot())

    # ------------------------------------------------------------------ #
    # Poll
    # ------------------------------------------------------------------ #
    async def _async_update_data(self) -> dict[str, Any]:
        if self._seeded_during_setup:
            # **The first refresh after setup is not a refresh.** `async_setup_entry` calls
            # `async_setup()` and then `async_config_entry_first_refresh()` on the next line,
            # and the base class turns that into a `_async_update_data()` — so without this,
            # every start read the whole account twice, milliseconds apart, for state that
            # could not have changed in between. Measured 2026-08-21: **five duplicate REST
            # calls per start** (gcs-state, gcsadvancestate, presets, hub-state, favorites).
            #
            # What the first refresh is actually *for* is populating `coordinator.data`
            # before the platforms are forwarded — `async_setup` does not call
            # `async_set_updated_data` itself, so unless the stream has already connected
            # and done so, `data` is None until this returns. That needs the snapshot, not
            # the network.
            #
            # **Why this is safe, and not merely cheap.** `async_setup` starts the stream
            # before it reads anything, and a device whose live state was read before the
            # stream came up is read again once it has (`_handle_connected`). Anything that
            # changed after the setup read is therefore either reported by the stream or
            # caught by *that* read, which happens after the connection exists rather than
            # before it. This one would be redundant with both, and worse placed.
            #
            # ⚠️ **Only the first one.** A manual `homeassistant.update_entity` is the only
            # other way in — with `SCAN_INTERVAL = None` there is no clock — and that one
            # must read for real, so the flag is spent here and never set again.
            self._seeded_during_setup = False
            self._persist_refresh_token()
            return self._snapshot()
        try:
            await asyncio.gather(
                self._async_seed_state(),
                *(
                    # A monthly series never yet read is retried by the seed, so the daily
                    # read leaves it alone then rather than asking for it a second time.
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
        one device do not blank the other.
        """
        # **In parallel.** Each device's reads are independent, and the only ordering that
        # matters is inside one device — settings before state on a valve, configuration
        # before state on a controller — which stays sequential within each coroutine. Run
        # serially this was ~14 round trips end to end on a two-valve, two-controller
        # account: several seconds of waiting on every restart and reload.
        #
        # Each valve's fault log is read beside its seed rather than inside it: it depends
        # on nothing the seed reads, and a failure keeps what was known — see
        # `Valve.async_refresh_faults`.
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

        Each device whose `gcs-state` or `hub-state` read went out before the stream was up
        — or has not succeeded at all — and only that read: settings, presets and the rest
        do not move in the seconds between setup and the first connect, and a reconnect
        re-reads them all. See `_handle_connected`.

        ⚠️ **Except a device whose setup left something unread** (2026-10-16): it gets its
        whole seed, as the reseed that used to follow every connect gave it. One failed
        `gcsadvancestate` or `gcs-preset` at startup otherwise left outlet settings refused
        and the preset list empty until the next reconnect — days, on a stable stream — and
        a failed `hub-configuration` left a controller's accessories missing as long.
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

        `return_exceptions=True` keeps one device's failure from blanking the others; each
        read already catches `KohlerError` itself, so anything else reaching here is
        unexpected and is logged rather than allowed to cancel its siblings.

        **Except a sign-in failure, which is raised once every device has finished.** The
        token layer under every read raises `AuthError`, which is not a `KohlerError`, and
        until 2026-10-08 the seed logged it like any other failure — so the reauth handling
        in every caller (setup, the reseed on connect, a manual refresh) could never run,
        and a revoked credential showed only as a warning.
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
        """Seed one controller. Extracted so every device can be seeded concurrently.

        **One ordering, and everything else alongside it** (2026-10-16). Configuration must
        come before state — see below — but the experience catalogue, the active faults and
        the favorites depend on neither, and used to queue behind both: five round trips
        deep per controller where two will do. Each read is guarded on its own, as before;
        every one is let finish before any failure is raised, so none is left running.
        """
        device_id = controller.device_id

        async def configuration_then_state() -> None:
            # Zones, outlet types, and installed parts — installation-time facts that no
            # message ever pushes because nothing changes them at runtime. Read once and
            # keep it; re-reading on a timer polls forever for an event that happens when
            # a plumber visits.
            #
            # Read BEFORE the state, not after it as this used to: the same response says
            # how many outlets each of this controller's zones has, which decides how its
            # state decodes the zone arrays in everything that follows. See
            # `_apply_controller_topology`.
            #
            # The same read also carries the controller's **settings** — Max Shower
            # Duration above all — which do change at runtime, so it is made on every seed
            # (setup and each reconnect) and only the installation facts stay latched.
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
            # The firmware's fixed experience catalogue, once. A failure leaves it empty
            # and the Experience select offering nothing, which is the honest reading.
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
                    # Favorite ids are reassigned when one is deleted, so this list is the
                    # only safe way to resolve a favorite — never hardcode an id.
                    controller.favorites = favorites
            except KohlerError as err:
                if getattr(err, "status", None) == 404:
                    # Not a failure: this endpoint 404s when the controller has **no**
                    # saved favorites, rather than returning an empty list. Confirmed
                    # 2026-08-17 — the route is handled (it answers with the application's
                    # own error envelope, unlike a genuine bad path), MQTT
                    # `FAVORITES_SNAPSHOT` agrees with `attributes: []`, and
                    # `docs/protocol/hub_controller.md` §5 has a captured 200 from when
                    # this account still had one. Logging it as an error made three
                    # misleading lines per startup.
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

        The controller's half of `Valve.apply_rest_reply`, by the same rule: a message
        that lands while the read is in flight is newer than the reply, and keeps what it
        carried (`_HUB_PUSH_FIELDS`) — **only** what it carried (2026-10-16). Any of four
        message codes used to drop the whole reply, so a `MUSIC_STS` during a reconnect's
        read left the zones as they were before the stream went down. A `KohlerError` is
        logged and changes nothing.
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

        ⚠️ **A failed read changes nothing** (2026-10-16). The client answers `{}` for a read
        it could not make, and this used to take that for "no faults": every hiccup at
        Kohler cleared a fault the controller was reporting, and showed it healthy. A read
        that answers carries `errorDetails` — empty when nothing is active — so only a list
        is taken as the answer. A sign-in failure is raised, for the caller to report.
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

        The valve's `Valve.async_clear_fault_log`, for an Anthem Plus. Konnect 3.0.6
        declares the call beside the valve's, but no screen of the app was found to use it,
        and it has not been tried against a controller from here — so a refusal is passed
        on as it comes. The active errors are then read again, as for a valve.
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

        The entity's `DeviceInfo` sets it when the device is first created; this keeps it
        current when the controller's address changes, since each seed re-reads it. Cosmetic,
        so registry trouble is logged and stepped over rather than failing the seed.
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

        The entry's model is what the config flow detected — from the valve where there is
        one, else from the first controller that answered — and it is right for that device.
        It is not necessarily right for a second controller: the ordinary reason an account
        has two is two bathrooms, and nothing says they were plumbed with the same valve
        model. So each controller decodes its zone arrays with the split its own
        `hub-configuration` states, and keeps the entry's model only when that read yields
        nothing — which is exactly the case in which the config flow would have asked.

        The model decides how many outlet entities the controller gets, which is why this
        runs inside the setup seed, before the platforms are built — and once only, gated
        by `capabilities.known`, since a plumber's visit needs a reload anyway.

        ⚠️ **Unless that read fails at setup** (2026-10-16). It is then made again on each
        reseed until it answers, by which time the entities exist, built from the entry's
        layout — so the layout it finds is noted like a valve's, which reloads the entry
        once if it differs and starts the next setup from it. See `_async_note_topology`.
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
            # Name, not id — same reasoning as the valve's topology message above.
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

        Kept for the callers that already invoke it. Rotation itself now persists through
        `_store_refresh_token`, so this is a safety net rather than the mechanism.
        """
        self._store_refresh_token(self.auth.refresh_token)

    @callback
    def _stored_refresh_token(self) -> str | None:
        """The entry's refresh token, for `KohlerAuth` to fall back on; None once closed.

        A reload can land while the old coordinator's refresh is redeeming the token this
        one was built with. The old one then stores the rotated token, and this one's own
        attempt is refused as a token already redeemed — so it reads the entry again and
        takes that one up (`KohlerAuth._adopt_newer_refresh_token`). Once closed it answers
        nothing: an unloaded coordinator must never redeem the token its successor holds.
        """
        if self.closed:
            return None
        token = self.entry.data.get(CONF_REFRESH_TOKEN)
        # Seen in the entry, and about to be redeemed: if this coordinator closes while that
        # retry is in flight, `_store_refresh_token` must still store what it rotates to, or
        # the entry keeps the token just spent and the next start asks to sign in again.
        if token:
            self._entry_token = token
        return token

    def _store_refresh_token(self, token: str | None) -> None:
        """Persist one refresh token, if it is new.

        Called from `KohlerAuth` the instant a rotation happens — which is inside the auth
        lock, on the event loop, so `async_update_entry` is safe to call directly here.

        ⚠️ **Once closed, only over the token this coordinator last saw there**
        (2026-10-16). A command in flight across a reload belongs to the old coordinator,
        and its `KohlerAuth` still calls this. If the new coordinator has already rotated
        and stored its own token, writing the old one's over it would hand the next start a
        token nobody is using. That is the only case refused: the callback is not simply
        detached, because a rotation the old sign-in completes *before* anyone else has
        stored a newer token is the only copy of a live credential, and dropping it is how an
        account ends up asking to be signed in again.
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

        Read from the entry options, not from the log object: the options key is what
        survives a restart, and the switch must show ON after one even in the moments
        before `async_setup` has re-attached the file.
        """
        return bool(self.entry.options.get(CONF_REPORT_LOG_FILE))

    async def async_start_report_log(self) -> None:
        """Begin a new capture episode — a fresh file, named for this moment.

        Idempotent while an episode is running: turning an already-on switch on again must
        not split the file. The episode name is persisted to the entry options so a
        restart resumes the same file; the key is in `RELOAD_IGNORED_OPTION_KEYS`, so this
        write does not reload the entry and drop the stream being captured.
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

        Deliberately small: a code, a sku, a timestamp, and — only for the valve's own status
        message — the four fields that tell a configuration write apart from an ordinary
        status. The raw capture beside this holds every payload in full; duplicating it here
        would make the journal unreadable for the one thing it is for.
        """
        # When it arrived, not when it is handled: a message that came before the account
        # read is held and handled seconds later (`_early_envelopes`), and stamping the
        # replay put it out of order against the journal's own records (2026-10-16).
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

        ``valvecontrol {valveOnOff}`` — the controller's one direct water command, and the
        only place in the system where a bare on/off exists. It works because the controller
        stores its own default configuration; the GCS valve has no equivalent, which is why
        the valve's shower switch sends the default outlets stored in its preset 1 instead.

        Off stops the water only, leaving music, steam, and lighting running. Use
        :meth:`async_stop_hub` to idle everything.
        """
        self._note_local_write()
        with self.command_errors(_controller_offline(controller)):
            await controller.hub.async_set_shower(on)

    async def async_set_hub_steam(self, controller: Controller, on: bool) -> None:
        """Run or stop the controller's default steam. **On starts the steam generator.**

        ``steamcontrol {steamOnOff}`` at ``steamSettings`` defaults. Refused while the
        controller reports water running, mirroring the app ("shower and steam cannot be
        started at the same time"). App-confirmed; never run against hardware by this
        integration.
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

        The endpoint follows the category the title was listed under — a shower experience
        sent to the steam path does nothing — so the title is resolved against the read
        catalogue rather than trusted.
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

        It used to be awaited here, inside the timer's own job, where nothing kept hold of
        it: unload cancelled the timer but not a check already running, which then wrote to
        entities and could ask for a sign-in after the entry had gone. One check at a time.
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

        ⚠️ **Twice a day, as well as on every seed** (2026-10-16). Nothing pushes a fault,
        and on a stable stream a seed happens only at setup and on a manual refresh — so a
        fault that cleared kept its Problem sensor on for days, and a new one went unseen
        just as long. This rides on the firmware check's clock rather than adding one.

        A read that fails keeps what was known (see `Valve.async_refresh_faults` and
        `_async_refresh_active_errors`); a dead sign-in still asks to sign in again.
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

        Read-only — installing stays with the app, which refuses while water runs or the
        device is `Disconnected`. Each read answers `{}` on failure by contract, so one
        unreachable part leaves only its own update entity unknown.
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
            # On a timer, so nobody is waiting for an answer — but a dead sign-in must
            # still reach someone.
            _LOGGER.debug("Could not check firmware: %s", err)
            if credential_is_dead(err):
                self._handle_auth_error(err)
            return
        self.async_refresh_entities()

    async def async_stop_hub(self, controller: Controller) -> None:
        """Stop everything the controller is running — water, steam, music, lighting.

        Uses ``stopall`` rather than deactivating the active favorite, because the
        favorite may already have been replaced by whatever is running now, and a stop
        should not depend on correctly identifying what to stop.
        """
        self._note_local_write()
        with self.command_errors(_controller_offline(controller)):
            await controller.hub.async_stop_all()
