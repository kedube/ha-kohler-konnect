"""Home Assistant constants for the Kohler Konnect integration.

Protocol constants live in ``konnect/const.py``. This module holds only what Home
Assistant itself needs: the domain, config-entry keys, and tuning.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

DOMAIN = "kohler_konnect"

# Device display names. They also set entity_id prefixes, because Home Assistant builds
# entity ids from device name plus entity name: "Anthem Valve" + "Rainhead" gives
# `switch.anthem_valve_rainhead`. With several controllers on one account, each name is
# suffixed with its Konnect name so none gets `_2` entity ids; see
# `coordinator.controller_names`.
DEVICE_NAME_VALVE = "Anthem Valve"
DEVICE_NAME_CONTROLLER = "Anthem Plus"
# A faucet is named after its Konnect name; this is for one the app gives none.
DEVICE_NAME_FAUCET = "Sensate"

# ---------------------------------------------------------------------------
# Config-entry keys
# ---------------------------------------------------------------------------
# username comes from homeassistant.const.CONF_USERNAME.
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TENANT_ID = "tenant_id"
CONF_VALVE_MODEL = "valve_model"
# The detected outlet split, e.g. [3, 3]. Stored alongside the SKU so an install that does
# not match a catalogue model still works, and so a later SKU rename cannot change topology.
CONF_ZONE_OUTLETS = "zone_outlets"
CONF_TEMPERATURE_UNIT = "temperature_unit"
CONF_WATER_UNITS = "water_units"
# The Azure IoT Hub identity this entry registers as: generated once, then reused, so
# reconnects don't leave a new "phone" on the Kohler account each time. Per config entry,
# never global: two Home Assistant instances on one account must not share an MQTT
# client id.
CONF_MOBILE_DEVICE_ID = "mobile_device_id"
# Per-valve settings in `entry.options`, keyed by the valve's device id: Warmup
# Auto-Restore, the remembered warm-up mode and any pending restore. Reload-ignored, because
# the integration writes them while running.
CONF_VALVES = "valves"

# How multi-zone valve outlets, controls and sensors are grouped and named. Deliberately
# not in `RELOAD_IGNORED_OPTION_KEYS`: changing it reloads the entry so entities and zone
# sub-devices re-register with the chosen layout.
CONF_ZONE_GROUPING = "zone_grouping"
ZONE_GROUPING_NUMBERED = "numbered"
ZONE_GROUPING_SUBDEVICES = "subdevices"
ZONE_GROUPING_OUTLET_LABELS = "outlet_labels"
ZONE_GROUPING_MODES: tuple[str, ...] = (
    ZONE_GROUPING_NUMBERED,
    ZONE_GROUPING_SUBDEVICES,
    ZONE_GROUPING_OUTLET_LABELS,
)
DEFAULT_ZONE_GROUPING = ZONE_GROUPING_NUMBERED

# Whether the Kohler Konnect panel shows in Home Assistant's sidebar. The sidebar applies it
# as soon as it changes (`sidebar.async_sync_sidebar`), so it is reload-ignored.
CONF_SIDEBAR_PANEL = "sidebar_panel"
DEFAULT_SIDEBAR_PANEL = True

# ---------------------------------------------------------------------------
# Polling — none for showers
# ---------------------------------------------------------------------------
# `None` disables the account coordinator's interval. Valves and controllers are
# push-driven over MQTT; REST is read at setup and on each MQTT (re)connect, because the
# broker replays nothing on connect (`docs/protocol/platform.md` §4.2). Faucets have their
# own polling coordinator; see "Faucets" below. `homeassistant.update_entity` forces a
# refresh on demand.
SCAN_INTERVAL = None

# Delay after a shower stops before re-reading the daily usage series. Kohler aggregates a
# session server-side after the valve reports it closed, so an immediate read misses the
# shower that just ended. Fires on the running -> stopped edge only; it is not polling.
USAGE_REFRESH_DELAY_SECONDS = 90

# A second read, this long after the first, made only when the first still shows the same
# daily total as before the shower (aggregation not finished yet).
USAGE_RETRY_DELAY_SECONDS = 180

# Delay before reading an outlet configuration back after writing it.
# ⚠️ An immediate read-back shows the old value: `gcsadvancestate` updates only once the
# device reports, which has been seen to take up to 25 s after the 201.
OUTLET_WRITE_VERIFY_DELAY_SECONDS = 30


# ---------------------------------------------------------------------------
# Repair issues
# ---------------------------------------------------------------------------
def device_issue_key(device_id: str) -> str:
    """A device's stand-in in repair-issue ids: stable, but not the device id.

    Issue ids appear in the diagnostics download, and a Kohler device id is the address
    the cloud reaches the device by, which diagnostics redact. A one-way hash keeps one
    issue per device without exposing it.
    """
    return hashlib.sha256(device_id.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Optimistic controls
# ---------------------------------------------------------------------------
# How long a just-chosen option or switch position is shown if the device never agrees;
# agreement clears it sooner. Device confirmations arrive within about 3 s, and
# `async_set_warmup` awaits its own read-backs (`WARMUP_READBACK_DELAYS`) before this
# starts, so this is margin on top.
OPTIMISTIC_GRACE_SECONDS = 12.0

# ---------------------------------------------------------------------------
# Shower switch
# ---------------------------------------------------------------------------
# The preset whose stored words the `Shower on` switch sends. The valve has no "run my
# default" command, so the setup wizard and the Konnect app keep the default outlets,
# temperature and flow in preset 1, "Default shower". Clients send its words as a plain
# `solowritesystem`, and so does `Valve.async_start_default_shower`: preset 1 is never
# activated or written, so its own `time` never applies and only the outlets'
# `maximumRunTime` ends that shower. See `docs/protocol/gcs_valve.md` §3.4.
#
# The app hides preset 1 from its list, so the id shown in the app is not this id.
SHOWER_ON_PRESET_ID = 1

# Presets never offered as a choosable scene: preset 1 is the default-shower template
# (`SHOWER_ON_PRESET_ID`), which the Konnect app hides too.
PRESET_HIDDEN_IDS: frozenset[int] = frozenset({1})

# ---------------------------------------------------------------------------
# RAW MQTT LOG — diagnostic capture of every payload, before decoding
# ---------------------------------------------------------------------------
# Details, file format and the runtime switch: `konnect/raw_log.py`. Prefer the runtime
# switch, which needs no restart: `logger.set_level` with
#
#     custom_components.kohler_konnect.konnect.raw_log: debug
#
# This constant pins capture on across restarts instead.
#
# ⚠️ Must ship False. It overrides the logger switch, so a release with it True captures
# every MQTT payload with no supported way to stop it, and `RAW_MQTT_LOG_KEEP_FILES` never
# prunes. Set it True only locally, for a debugging session.
ENABLE_RAW_MQTT_LOG = False

# Under the Home Assistant config directory, so it is reachable from the File editor and
# Samba add-ons rather than buried in the container.
RAW_MQTT_LOG_DIR = "kohler_konnect_raw"
RAW_MQTT_LOG_MAX_BYTES = 8 * 1024 * 1024
# None: never prune. The directory is a permanent record, and automatic deletion could lose
# the capture a later investigation needs. Each file is capped at `RAW_MQTT_LOG_MAX_BYTES`,
# so growth is in file count.
RAW_MQTT_LOG_KEEP_FILES = None

# ---------------------------------------------------------------------------
# REPORT LOG — the consumer-side capture, keyed to the "Report Log" switch
# ---------------------------------------------------------------------------
# A user's bug-report capture, separate from the RAW MQTT LOG above: a switch on each valve
# and controller records every device's messages to one file per switch-on, and a restart
# appends to the same file. See `konnect/report_log.py`.
#
# The options key holds the active capture's name; its presence is the switch state, so a
# capture survives restarts. It is in `RELOAD_IGNORED_OPTION_KEYS` so toggling it does not
# reload the entry and drop the MQTT stream being captured.
CONF_REPORT_LOG_FILE = "report_log_file"

# Inside the integration folder, beside the integration the reports describe. A HACS update
# or reinstall replaces that folder and deletes any reports still in it; the README written
# beside them says so.
REPORT_LOG_DIR_NAME = "reports"
REPORT_LOG_MAX_BYTES = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# What a config-entry change has to be before it is worth a reload
# ---------------------------------------------------------------------------
# Read by `_async_update_listener` in `__init__.py`; the mechanism is in
# `konnect/entry_reload.py`.
#
# Data keys the running integration writes to its own entry. They are bookkeeping, not
# configuration, and must never cause a reload:
#
# * `CONF_REFRESH_TOKEN` — B2C rotates it on every refresh, so each new one is persisted
#   at once. It is the most frequently written key; reloading on it would drop MQTT for
#   nothing.
# * `CONF_MOBILE_DEVICE_ID` — written once, on first connect.
RELOAD_IGNORED_DATA_KEYS = frozenset({CONF_REFRESH_TOKEN, CONF_MOBILE_DEVICE_ID})

# `RELOAD_IGNORED_OPTION_KEYS` is defined after the warm-up constants it names.

# ---------------------------------------------------------------------------
# Outlet type codes
# ---------------------------------------------------------------------------
# The valve's per-outlet `outLetType`, named as the Konnect app's outlet picker titles it.
# This is the app's full table (`docs/protocol/gcs_valve.md` §1.2). Take any new code from
# the app's table, never from lining up one install's outlet order against another's.
#
# Where the app distinguishes one fixture from several, the plural is kept, because "Body
# Sprays" switches more than one head. Rainhead and spray variants (Katalyst, Silk, …) are
# the same fixture to switch, so they share a name; the variant is in
# `OUTLET_TYPE_VARIANTS`.
#
# A label only: nothing reads these names to decide behaviour. Names given to outlets in
# the app are not transmitted, so the fixture type is the closest name available.
#
# Naming a code renames its switch at the next setup. The unique id is the outlet's
# position, so it stays the same entity with its original entity id; see
# `entity.outlet_unique_id`.
#: The fixture code of a tub filler, which a bath-fill preset runs (`gcs_valve.md` §8).
OUTLET_TYPE_TUB_FILLER = 21

OUTLET_TYPE_NAMES: dict[int, str] = {
    1: "Handshower",
    11: "Showerhead",
    12: "Showerheads",
    21: "Tub Filler",
    30: "Not Plumbed",
    31: "Rainhead",
    32: "Rainhead",
    33: "Rainhead",
    34: "Rainhead",
    35: "Rainhead",
    36: "Rainhead",
    37: "Rainhead",
    38: "Rainhead",
    39: "Rainhead",
    51: "Body Spray",
    52: "Body Sprays",
    53: "Body Spray",
    61: "Foot Spray",
    62: "Foot Sprays",
}

#: The variant the app shows beneath the fixture name, for codes that have one. Published as
#: the outlet switch's `outlet_variant` attribute; never part of a name or an id.
OUTLET_TYPE_VARIANTS: dict[int, str] = {
    31: "Katalyst",
    32: "Cascade",
    33: "Kinetic",
    34: "Rain Curtain",
    35: "Laminar",
    36: "Massage (Wave)",
    37: "Hydro Massage",
    38: "Silk",
    39: "Real Rain",
    51: "Single",
    52: "Multiple",
    53: "Massage (Wave)",
    61: "Single",
    62: "Multiple",
}

# ---------------------------------------------------------------------------
# Temperature slider bounds (Home Assistant side only)
# ---------------------------------------------------------------------------
# 92-118 °F: the Konnect app's range for Max Temperature (33-48 °C;
# `docs/protocol/gcs_valve.md` §4), used by `Max Temperature`, as the top of
# `Default Temperature`, and as the zone control's ceiling until the valve reports its own.
# A UI gate, not a device limit: the valve accepts 0 °C (full cold) and more, `valve_hex.py`
# encodes the whole range, and a preset, the touchscreen or `send_valve_hex` can go outside
# it. The scald limit in force is the valve's `maximumOutletTemperature`, a per-valve
# setting.
#
# Stated in Fahrenheit and converted for a Celsius account, so they read as the app shows
# them. A temperature set elsewhere outside these bounds is still reported, but a slider
# cannot show it accurately.
UI_TEMPERATURE_MIN_F = 92
UI_TEMPERATURE_MAX_F = 118

#: The zone temperature control's floor when the valve has not reported its own minimum —
#: the `minimumOutletTemperature` of 15.0 °C the app forces on every outlet-config write.
#: The control's lowest step is one below this, and means full cold, as the app's `COLD`
#: stop does.
ZONE_TEMPERATURE_MIN_F = 59

# ---------------------------------------------------------------------------
# Writable outlet configuration — the Konnect app's own three settings
# ---------------------------------------------------------------------------
# Max Shower Duration: the six values the app offers, not a range. 35/40/50/55 minutes are
# legal multiples of 300 s, but whether the valve accepts them is untested, so a select
# offers only the app's list. 3600 s is live-verified writable.
OUTLET_RUN_TIME_CHOICES_SECONDS = (900, 1200, 1500, 1800, 2700, 3600)

# ⚠️ Konnect 3.0.1 shows any duration above 1800 s as 25 minutes, and one Save there writes
# 1500 (fixed in 3.0.5). Choosing a higher value here warns about it.
OUTLET_RUN_TIME_APP_SAFE_MAX_SECONDS = 1800

# Default Temperature's floor. Its top is `UI_TEMPERATURE_MAX_F`; a value above the valve's
# current scald limit is refused (`number.OutletDefaultTemperatureNumber`).
UI_DEFAULT_TEMPERATURE_MIN_F = 59

# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------
# Flow for a write when the command names none. Never the valve's current flow byte: that
# is whatever the panel last wrote, often far below 100%, so re-sending it could start a
# trickle nobody chose.
DEFAULT_FLOW_PERCENT = 100.0

# ---------------------------------------------------------------------------
# Warmup dropdown labels
# ---------------------------------------------------------------------------
# Display labels for the device's warm-up mode strings. Deliberately Home Assistant's
# wording, not the app's: the app's "Selected outlets" is "Started Outlets" here, which
# says what the mode does. The two delayed-start modes are never offered; they appear only
# while the valve holds one (see `select.py`).
WARMUP_LABELS = {
    "warmUpDisabled": "Off",
    "warmUpAllOutletsWithNoStartDelay": "All Outlets",
    "warmUpSelectedOutletsWithNoStartDelay": "Started Outlets",
    "warmUpAllOutlets": "All Outlets (delayed start)",
    "warmUpSelectedOutlets": "Started Outlets (delayed start)",
}

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
# Three actions in all: `send_valve_hex` and `custom_shower` here, `dispense` with the
# faucet constants. Everything else is an entity, because an entity shows state as well as
# accepting a command.
#: What a valve command says when Kohler answers `statusCode 900`.
VALVE_OFFLINE = (
    "The Anthem valve is offline. Check that it is powered on and connected to Wi-Fi, "
    "then try again."
)

#: How often firmware availability is re-read, for every device. Twice a day: a release is
#: rare, an update entity only has to notice one within hours, and nothing pushes one — a
#: faucet's finished install is announced (`INSTALL_FIRMWARE_STS`) and re-checks at once.
FIRMWARE_CHECK_INTERVAL = timedelta(hours=12)

SERVICE_SEND_VALVE_HEX = "send_valve_hex"
# The form-driven sibling: outlets, temperature and an optional flow as typed fields, sent
# as one complete write. A UI-built automation would otherwise send two valve commands back
# to back, which the valve cannot take.
SERVICE_CUSTOM_SHOWER = "custom_shower"

# ---------------------------------------------------------------------------
# Warmup auto-restore
# ---------------------------------------------------------------------------
# The Anthem Plus hub's web UI writes `warmUpDisabled` on every signed-in use, which
# nothing outside the hub's firmware can prevent (`docs/protocol/gcs_valve.md` §5). This
# switch puts the mode back, once, `WARMUP_AUTO_RESTORE_DELAY_SECONDS` later.
CONF_WARMUP_AUTO_RESTORE = "warmup_auto_restore"

# The last enabled mode seen on the valve, persisted so a restore puts back what was in
# force rather than a guess: all outlets and selected outlets are different water.
CONF_LAST_WARMUP_MODE = "last_warmup_mode"

# The mode a scheduled restore will write, kept from scheduling until it is written or no
# longer wanted, so a restart or reload during the wait does not lose it; the next seed
# finishes it (`WarmupManager.note_seeded_mode`). Absent or None: nothing owed.
CONF_WARMUP_RESTORE_PENDING = "warmup_restore_pending"


# Options read live from `entry.options` on every access, so saving one needs no reload.
# Anything not listed reloads, so a new option is safe by default; add one here only once
# it is known to be read live. `CONF_VALVES` covers the per-valve switch, remembered mode
# and pending restore, all written while running; `CONF_SIDEBAR_PANEL` is applied by the
# sidebar itself.
RELOAD_IGNORED_OPTION_KEYS = frozenset(
    {CONF_REPORT_LOG_FILE, CONF_VALVES, CONF_SIDEBAR_PANEL}
)

# Long enough for the config re-sync burst around a disable to finish; a restore written
# into it would be overwritten again.
WARMUP_AUTO_RESTORE_DELAY_SECONDS = 60.0

# A disable we wrote (choosing `Off`) must never be undone, or `Off` could not be chosen.
# A matching disable within this window of our own write is ours; the device echo takes
# about 3.4 s.
WARMUP_SELF_WRITE_GRACE_SECONDS = 30.0

# Stop after this many consecutive restores that did not stick, rather than fight whatever
# keeps disabling the mode and hammer Kohler's API, and say so.
WARMUP_AUTO_RESTORE_MAX_CONSECUTIVE = 5

# A restore that holds this long counts as having stuck, and resets the count above.
WARMUP_AUTO_RESTORE_SETTLED_SECONDS = 900.0

WARMUP_AUTO_RESTORE_ON = (
    "Warmup Auto-Restore is ON. If something sets the Anthem valve's warmup mode to Off, "
    "Home Assistant will set it back to %s after %.0f seconds. Turning warmup off from the "
    "Warmup dropdown is not affected — only changes this integration did not make."
)

WARMUP_AUTO_RESTORE_NO_TARGET = (
    "Warmup Auto-Restore is ON but no enabled warmup mode has been seen yet, so there is "
    "nothing to restore to. Pick a mode on the Warmup dropdown and it will be remembered."
)

WARMUP_AUTO_RESTORE_GIVING_UP = (
    "Warmup Auto-Restore has put the mode back %d times and it keeps being disabled again. "
    "Something on the system is actively rewriting it, and retrying is not fixing that — "
    "stopping until the mode stays enabled or Home Assistant restarts. See "
    "docs/protocol/gcs_valve.md section 5."
)

WARMUP_AUTO_RESTORE_NOT_WRITABLE = (
    "Warmup Auto-Restore cannot put the Anthem valve's warmup mode back to %s: that is not "
    "one of the modes this integration writes (a delayed-start mode, or one it does not "
    "know), so it has been left off. Choose a mode on the Warmup dropdown and auto-restore "
    "will put that one back from then on."
)

# ---------------------------------------------------------------------------
# Warmup diagnostic journal
# ---------------------------------------------------------------------------
# ⚠️ Must ship False: like `ENABLE_RAW_MQTT_LOG`, it pins the journal on over its logger
# switch (`docs/mqtt/capture_runbook.md`). Worth turning on locally if something keeps
# rewriting the warm-up mode: it records every auto-restore end to end. A handful of
# records a day; `DebugJournal` rolls each file at 8 MB.
ENABLE_WARMUP_DEBUG_LOG = False

# Never pruned: the journal's value is comparing an event with ones weeks earlier.
WARMUP_DEBUG_LOG_KEEP_FILES = None

# Wire traffic carried in a disable record, either side of it. The config re-sync burst
# around a disable runs for about a minute beforehand, and the controller's
# `SYSTEM_STS: SYSTEM_READY` arrives 7-9 s after it, so the record looks both ways.
#
# ⚠️ The forward window must stay shorter than `WARMUP_AUTO_RESTORE_DELAY_SECONDS`, so the
# evidence closes before any restore of ours is written.
WARMUP_CONTEXT_BEFORE_SECONDS = 120.0
WARMUP_CONTEXT_AFTER_SECONDS = 45.0

# Cap on the rolling buffer of recent messages, so a chatty hour cannot grow it without bound.
WARMUP_CONTEXT_MAX_MESSAGES = 400

# ---------------------------------------------------------------------------
# Warmup write confirmation
# ---------------------------------------------------------------------------
# Waits before each read-back of a warm-up write (reads at about 0, 2 and 6 s). Right
# after a successful POST, `gcs-state` still shows the old mode, catching up within about
# 3 s, so a single immediate read would always disagree.
WARMUP_READBACK_DELAYS = (0.0, 2.0, 4.0)

# ---------------------------------------------------------------------------
# CLOUD CONNECTION WATCH — is the valve still reachable by Kohler's cloud?
# ---------------------------------------------------------------------------
# Implemented in `cloud_watch.CloudConnectionWatch`; user-facing behaviour in
# `docs/user_guide.md`, "When the valve drops off the cloud".
#
# The valve can drop off Kohler's cloud and return only on a power cycle. MQTT cannot show
# that: everything on it is published by the valve, so a disconnect is silence, and benign
# idles can last longer than real outages. These settings only decide when to ask the cloud
# (`connectionState`), never when to raise an alarm.

# How close a valve message must be to a controller `SHOWER_VALVE_STS`, either side, for
# the pair to count as confirmed. The controller normally trails the valve by 0.3-2 s but
# can lag by minutes, so the rule also requires a zone to be on rather than trusting timing
# alone.
CLOUD_CHECK_PAIR_WINDOW_SECONDS = 60.0

# Minimum spacing between cloud checks, whichever trigger asks. A shower produces a burst
# of `SHOWER_VALVE_STS`, each of which could ask; the failure lasts hours and needs a person
# at the wall, so one check per half hour is enough.
CLOUD_CHECK_COOLDOWN_SECONDS = 1800.0

# How long the valve may be silent before the cloud is asked about it.
# ⚠️ Not a silence alarm, and must never become one: 12-hour quiet periods are normal. It
# only decides when one GET is worth making, and `connectionState` gives the answer.
CLOUD_CHECK_QUIET_SECONDS = 3 * 60 * 60.0


# ---------------------------------------------------------------------------
# Faucets (Sensate and Setra)
# ---------------------------------------------------------------------------
# Each faucet has its own coordinator, polling its live state slowly while idle and quickly
# while water runs or right after a command, at cadences carried over from the
# `kohler_sensate` integration. The MQTT stream is shared with the showers; once it has
# proven it reports a faucet's changes, polling that faucet is only a safety net.
FAUCET_SCAN_INTERVAL_IDLE = timedelta(seconds=30)
FAUCET_SCAN_INTERVAL_ACTIVE = timedelta(seconds=5)
FAUCET_SCAN_INTERVAL_PUSH = timedelta(minutes=5)
FAUCET_SCAN_INTERVAL_PUSH_ACTIVE = timedelta(seconds=30)
# How long to keep polling quickly after a command, while the cloud catches up.
FAUCET_COMMAND_FOLLOW_UP = timedelta(seconds=30)
# How long the stream gets to announce a change a poll saw first, before polling stops
# relying on it.
FAUCET_PUSH_GRACE = timedelta(seconds=15)
# Configuration (firmware, leak history, presets) is re-read less often than the state.
FAUCET_CONFIG_REFRESH_INTERVAL = timedelta(minutes=5)
# Water usage: re-read this often, and this long after the water stops, once Kohler has
# counted it.
FAUCET_USAGE_REFRESH_INTERVAL = timedelta(minutes=30)
FAUCET_USAGE_SETTLE = timedelta(minutes=2)
# A dispense from Home Assistant counts as running until the faucet reports the water off,
# once it has reported it on or this long after the command.
DISPENSE_SETTLE = timedelta(seconds=3)
# A dispense whose end is never reported counts as over after DISPENSE_MAX, or longer for
# large amounts: the time to pour them at DISPENSE_SLOWEST_FLOW (litres per minute, well under
# the Sensate's rated flow) plus a minute.
DISPENSE_MAX = timedelta(minutes=2)
DISPENSE_SLOWEST_FLOW = 3.0
# Bounds for honouring Kohler's Retry-After when it throttles a faucet poll.
RETRY_AFTER_MIN = timedelta(seconds=30)
RETRY_AFTER_MAX = timedelta(minutes=15)
# Consecutive rejected faucet reads (4xx or unreadable replies) before a repair issue is
# raised; one-offs are normal.
REJECTIONS_BEFORE_ISSUE = 3
ISSUE_FAUCET_NOT_FOUND = "faucet_not_found"
ISSUE_FAUCET_API_CHANGED = "faucet_api_changed"
# Cleared leak events, the leak alert, the safety limit and whether instant updates have
# proved themselves, for every faucet of an entry.
FAUCET_STORAGE_VERSION = 1
FAUCET_STORAGE_KEY = DOMAIN + ".{entry_id}.faucets"
# Cap on remembered cleared events; old ones have long left Kohler's history.
MAX_CLEARED_LEAKS = 200

# Water turned on from Home Assistant is turned off after this long; 0 = never. One setting
# for every faucet on the account.
CONF_MAX_RUN_MINUTES = "max_run_minutes"
DEFAULT_MAX_RUN_MINUTES = 10

# Hard limits for a single dispense, in millilitres, whatever unit is used. The Konnect app
# dispenses and saves presets up to 3 gallons (12 quarts, 48 cups: 11.356 L, a little more
# after its single-precision conversion).
DISPENSE_MIN_ML = 10.0
DISPENSE_MAX_ML = 11360.0

SERVICE_DISPENSE = "dispense"
ATTR_AMOUNT = "amount"
ATTR_UNIT = "unit"
ATTR_PRESET = "preset"

# Old integrations this one replaces. Their entries are pointed out in Repairs, since their
# entities would otherwise sit beside this integration's as duplicates.
REPLACED_DOMAINS = ("kohler_anthem", "kohler_sensate")
ISSUE_REPLACED_INTEGRATION = "replaced_integration"
