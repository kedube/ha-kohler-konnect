"""Protocol constants for the Kohler Konnect cloud and the Anthem Plus local API.

Values here are app-global — baked into the Konnect Android app and identical across
accounts. Nothing in this module is a per-user secret.

Checked against Konnect Android 3.0.6. ``docs/protocol/`` is the developer reference for
everything here — what each endpoint takes and returns, which facts are app-confirmed and
which live-verified.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Cloud API
# ---------------------------------------------------------------------------
API_BASE = "https://api-kohler-us.kohler.io"

# The APIM subscription key identifies the app to Kohler's API gateway: app-global and
# stable, not a per-user credential. This host needs no mTLS; the APK's client certificate
# is only for the alternate gateway (`docs/protocol/platform.md` §2).
APIM_SUBSCRIPTION_KEY = "429ecb1d0b5e4258aa0a2bfadd82a493"

# ---------------------------------------------------------------------------
# Azure AD B2C auth
# ---------------------------------------------------------------------------
# Anthem `/commands/*` accept only tokens from the B2C_1A_signin policy (ROPC tokens get
# 403; reads accept either), so sign-in drives the custom policy server-side. One such
# token, as the Konnect app holds, covers every product. See `docs/protocol/platform.md` §1.
CLIENT_ID = "8caf9530-1d13-48e6-867c-0f082878debc"
API_RESOURCE = "f5d87f3d-bdeb-4933-ab70-ef56cc343744"
B2C_TENANT = "konnectkohler.onmicrosoft.com"
B2C_SIGNIN_POLICY = "B2C_1A_signin"
B2C_AUTHORITY = (
    f"https://konnectkohler.b2clogin.com/tfp/{B2C_TENANT}/{B2C_SIGNIN_POLICY}"
)
B2C_AUTHORIZE_URL = f"{B2C_AUTHORITY}/oauth2/v2.0/authorize"
B2C_TOKEN_URL = f"{B2C_AUTHORITY}/oauth2/v2.0/token"
B2C_SCOPE = f"openid offline_access https://{B2C_TENANT}/{API_RESOURCE}/apiaccess"

# The registered redirect URI, which B2C validates strictly; the older APK-derived
# `msauth://com.kohler.hermoth/...` is no longer registered. Nothing navigates here:
# sign-in runs server-side and reads the code from the 302 Location header.
B2C_REDIRECT_URI = "msauth.com.kohler.hermoth://auth"

# Server-side sign-in endpoints (B2C custom-policy "SelfAsserted" flow).
B2C_POLICY_BASE = f"https://konnectkohler.b2clogin.com/{B2C_TENANT}/{B2C_SIGNIN_POLICY}"
B2C_SELF_ASSERTED_URL = f"{B2C_POLICY_BASE}/SelfAsserted"
B2C_CONFIRMED_URL = f"{B2C_POLICY_BASE}/api/CombinedSigninAndSignup/confirmed"

# B2C error codes worth distinguishing for the UI.
ERROR_BAD_CREDENTIALS = "AADB2C90053"
ERROR_REDIRECT_NOT_REGISTERED = "AADB2C90006"

# Seconds before expiry to renew the access token. B2C rotates the refresh token on every
# refresh; dropping the new one strands the account until the next interactive sign-in.
TOKEN_EXPIRY_MARGIN_SECONDS = 300

# ---------------------------------------------------------------------------
# SKUs
# ---------------------------------------------------------------------------
# GCS is the Anthem digital valve body (built-in Wi-Fi, addressed directly).
# HUB is the Anthem Plus Linux system controller (drives valves, music, light, steam).
# Device IDs are NOT a reliable discriminator — an Anthem Plus controller's id can begin
# with "gcs". Always branch on the sku field.
SKU_GCS = "GCS"
SKU_HUB = "HUB"
# The Sensate kitchen faucet, and a second Konnect faucet (almost certainly the Setra) that
# the app handles identically everywhere. Commands carry the device's own SKU.
SKU_SENSATE = "SEN"
SKU_SETRA = "SET"
FAUCET_SKUS = frozenset({SKU_SENSATE, SKU_SETRA})
SKU_SMART_FAN = "SFC"
SKU_NUMI = "NUM"
SKU_PUREWASH = "BLD"
SKU_EVOCYCLE = "EVO"
SKU_ICE_BATH = "IBH"

# ---------------------------------------------------------------------------
# Reads — /devices/api/v1/device-management/
# ---------------------------------------------------------------------------
DEVICE_API = "/devices/api/v1/device-management"
FIRMWARE_API = "/platform/api/v1/firmware"
CUSTOMER_DEVICE = f"{DEVICE_API}/customer-device/{{tenant_id}}"
# PATCH: the account's record of one device, which names it and holds an EvoCycle's
# water-savings goal (platform.md §3, evocycle.md §3.8).
CUSTOMER_HOME_DEVICE = f"{CUSTOMER_DEVICE}/{{home_id}}/device"
HUB_STATE = f"{DEVICE_API}/hub-state/{{device_id}}"
HUB_FAVORITES = f"{DEVICE_API}/hub-experience/{{device_id}}/favorites"
HUB_EXPERIENCES = f"{DEVICE_API}/hub-experience/{{device_id}}/experiences"
HUB_CONFIGURATION = f"{DEVICE_API}/hub-configuration/{{device_id}}"
HUB_DIAGNOSTICS = f"{DEVICE_API}/hub-diagnostics/{{device_id}}"
HUB_DIAGNOSTICS_ACTIVE = f"{DEVICE_API}/hub-diagnostics/{{device_id}}/active"
# Same contract as `GCS_USAGE` below; the response adds hot inlet temperatures.
HUB_USAGE = f"{DEVICE_API}/hub-usage/{{device_id}}"
GCS_PRESETS = f"{DEVICE_API}/gcs-preset/{{device_id}}"
GCS_STATE = f"{DEVICE_API}/gcs-state/{{device_id}}"
# The valve's settings block — the only source of outlet topology that does not need a
# controller. Distinct from gcs-configuration, which is null on a controller-attached valve.
GCS_ADVANCE_STATE = f"{DEVICE_API}/gcs-state/gcsadvancestate/{{device_id}}"
# The valve's own configuration record, read for `about` (firmware). Not a topology source:
# on a controller-attached valve every structural field is null; read `GCS_ADVANCE_STATE`.
GCS_CONFIGURATION = f"{DEVICE_API}/gcs-configuration/{{device_id}}"
# Per-part identity — gateway, valves and touch interfaces, with models and serial numbers
# (`docs/protocol/gcs_valve.md` §2).
GCS_ABOUT = f"{DEVICE_API}/gcs-configuration/{{device_id}}/about"
# The valve's fault log (`docs/protocol/platform.md` §8). Read on demand for diagnostics,
# never polled.
GCS_DIAGNOSTICS = f"{DEVICE_API}/gcs-diagnostics/{{device_id}}"
# The catalogue of experience programs the app offers to add, not the ones stored on this
# valve (those come back in `gcs-preset` with `isExperience: "True"`).
GCS_EXPERIENCE_CATALOGUE = f"{DEVICE_API}/gcs-experience"
# Water usage history — the endpoint behind the Konnect app's charts. Query parameters are
# PascalCase: `?FromDate=…&ToDate=…&Interval=…`. The app sends `MM-dd-yyyy` dates and
# `Day`/`Month`, and never sends `WEEK` (the server refuses it with a 400), so a week is
# seven daily buckets, as in `ValveWeeklyWaterSensor`. This client sends ISO dates and
# uppercase `DAY`/`MONTH`, which the server also accepts. See
# `docs/protocol/platform.md` §6.
GCS_USAGE = f"{DEVICE_API}/gcs-usage/{{device_id}}"

# Faucets (`SEN`/`SET`). See `docs/protocol/sensate_faucet.md`.
#
# Live state: `{connectionState, lastConnected, sku, state: {status, handleState, progress,
# quantity}}`. `progress` is the firmware download, not a dispense.
FAUCET_STATE = f"{DEVICE_API}/faucet-state/{{device_id}}"
# `configuration.about` (firmware, serial, hardware) and `leakDetectionHistory[]`, each entry
# `{leakDetectionTime: <epoch seconds>}`.
FAUCET_CONFIGURATION = f"{DEVICE_API}/faucet-configuration/{{device_id}}"
# Same query contract as `GCS_USAGE`. Buckets are `faucetUsageDataDetailsList[]`, with litres
# in `waterUsage` and seconds in `usageDuration`.
FAUCET_USAGE = f"{DEVICE_API}/faucet-usage/{{device_id}}"
# One faucet's presets, as the app's faucet screen lists them: `?DeviceIds={id}` (PascalCase)
# answers `{faucetExperienceList: [{deviceId, experience: [...]}]}`.
FAUCET_EXPERIENCE = f"{DEVICE_API}/faucet-experience"
# Every preset on the account; the faucet's are under `sensateExperiences`. The fallback when
# `FAUCET_EXPERIENCE` fails or is empty.
CUSTOMER_EXPERIENCE = f"{DEVICE_API}/customer-experience/{{tenant_id}}"

# Smart Fan (`SFC`). See `docs/protocol/smart_fan.md`.
#
# Live state, `{connectionState, deviceId, tenantId, sku, state: {fanStatus, fanSpeed,
# lightStatus, …}}`; the stored defaults and parts, under `configuration.sfcSettings` and
# `configuration.about`; and the favorites, `experiences[]`.
SFC_STATE = f"{DEVICE_API}/sfc-state/{{device_id}}"
SFC_CONFIGURATION = f"{DEVICE_API}/sfc-configuration/{{device_id}}"
SFC_EXPERIENCE = f"{DEVICE_API}/sfc-experience/{{device_id}}"

# Numi 2.0 (`NUM`) and Purewash E930 (`BLD`): one protocol, whose paths take `numi` or
# `blade`. See `docs/protocol/numi_purewash.md`.
TOILET_STATE = f"{DEVICE_API}/{{segment}}-state/{{device_id}}"
# Water used, with flush, wash and dryer counts: `numiUsageDataDetailsList[]` for both
# (`numi_purewash.md` §6.2). The query is `GCS_USAGE`'s.
TOILET_USAGE = f"{DEVICE_API}/{{segment}}-usage/{{device_id}}"
# The stored defaults (`setting`), read whole and patched one part at a time (§2.4, §3.5).
TOILET_SETTING = f"{DEVICE_API}/{{segment}}-setting/{{device_id}}"
TOILET_SETTING_PART = f"{TOILET_SETTING}/{{part}}"

# EvoCycle (`EVO`). See `docs/protocol/evocycle.md`. Its settings come back under
# `evoAboutModel`, not `configuration`.
EVO_STATE = f"{DEVICE_API}/evo-state/{{device_id}}"
EVO_CONFIGURATION = f"{DEVICE_API}/evo-configuration/{{device_id}}"
# Water used and saved: `evoUsageDataDetailsList[]` (`evocycle.md` §2.5). The query is
# `GCS_USAGE`'s plus `TimeZone`.
EVO_USAGE = f"{DEVICE_API}/evo-usage/{{device_id}}"
# The Water Savings screen's two reads (`evocycle.md` §2.4, §2.5): this week's cycled and
# fresh water (`?CurrentDate&TimeZone`), and saved water by period
# (`?FromDate&ToDate&TimeZone&Interval`).
EVO_DASHBOARD = f"{DEVICE_API}/evo-dashboard/{{device_id}}"
EVO_DASHBOARD_USAGE = f"{DEVICE_API}/evo-dashboard/waterusage/{{device_id}}"

# Ice Bath (`IBH`): a session journal, not a connected device. See
# `docs/protocol/ice_bath.md`. Records take `?FromDate=&ToDate=` in epoch seconds.
IBH_CONFIGURATION = f"{DEVICE_API}/ibh-configuration/{{device_id}}"
# PATCH: the routine's settings (§4.3).
IBH_CONFIGURATION_WRITE = f"{DEVICE_API}/ibh-configuration/{{device_id}}/update"
IBH_RECORD = f"{DEVICE_API}/ibh-record/{{device_id}}"
# PATCH: insert, update or delete one record; the body names the journal (§4.1).
IBH_RECORD_WRITE = f"{DEVICE_API}/ibh-record"

# ---------------------------------------------------------------------------
# Firmware — /platform/api/v1/firmware/
# ---------------------------------------------------------------------------
# Version checks (`docs/protocol/platform.md` §7): "update available" is
# `firmwareUpdateAvailable` alone. This integration only reads; installing is left to the
# app.
GCS_FIRMWARE = f"{FIRMWARE_API}/gcs/{{device_id}}?releasetarget=Public"
GCS_GATEWAY_FIRMWARE = f"{FIRMWARE_API}/gcs/gateway/{{device_id}}?releasetarget=Public"
HUB_FIRMWARE = f"{FIRMWARE_API}/hub/{{device_id}}?releasetarget=Public"
# Faucets use the `sensate` type and, unlike the Anthem parts, no `releasetarget` query.
FAUCET_FIRMWARE = f"{FIRMWARE_API}/sensate/{{device_id}}"
# As the faucet's: no `releasetarget`.
SFC_FIRMWARE = f"{FIRMWARE_API}/sfc/{{device_id}}"
NUMI_FIRMWARE = f"{FIRMWARE_API}/numi/{{device_id}}"
PUREWASH_FIRMWARE = f"{FIRMWARE_API}/blade/{{device_id}}"
# EvoCycle's own screens ask with `releasetarget`, as the Anthem parts do.
EVO_FIRMWARE = f"{FIRMWARE_API}/evo/{{device_id}}?releasetarget=Public"

# ---------------------------------------------------------------------------
# Commands — /platform/api/v1/commands/
# ---------------------------------------------------------------------------
COMMANDS = "/platform/api/v1/commands"

# Smart Fan: one endpoint per action, `{COMMANDS}/sfc/fancontrol` and so on.
SFC_COMMAND = f"{COMMANDS}/sfc/{{command}}"
# Numi and Purewash: `{COMMANDS}/numi/flush`, `{COMMANDS}/blade/flush` and so on.
TOILET_COMMAND = f"{COMMANDS}/{{segment}}/{{command}}"
# EvoCycle: `{COMMANDS}/evo/shower`, `{COMMANDS}/evo/clean` and so on.
EVO_COMMAND = f"{COMMANDS}/evo/{{command}}"

# GCS: no bare on/off exists. Every start specifies the full valve state.
GCS_SOLOWRITESYSTEM = f"{COMMANDS}/gcs/solowritesystem"
GCS_CONTROL_PRESET = f"{COMMANDS}/gcs/controlpresetorexperience"
GCS_START_PRESET = f"{COMMANDS}/gcs/startpreset"
GCS_WRITE_PRESET = f"{COMMANDS}/gcs/writepreset"
# Whole-record replace of one outlet's configuration, one call per outlet, chained on
# success (`docs/protocol/gcs_valve.md` §2.1). See `GcsDevice.async_write_outlet_config`
# for the guards it needs.
GCS_WRITE_OUTLET_CONFIG = f"{COMMANDS}/gcs/writeoutletconfig"
# One touch panel's settings record, whole (`gcs_valve.md` §6; `ui_config.py`).
GCS_WRITE_UI_CONFIG = f"{COMMANDS}/gcs/writeuiconfig"
GCS_CREATE_PRESET = f"{COMMANDS}/gcs/createpreset"
GCS_WARMUP = f"{COMMANDS}/gcs/warmup"
# Restart the valve: `{deviceId, sku, tenantId, reset: "productRestart"}`, the app's
# Settings → Restart Product. Any running water stops. It cannot revive a valve that has
# dropped off the cloud: that valve never receives the command.
GCS_VALVE_RESET = f"{COMMANDS}/gcs/valvereset"
GCS_RESET_RESTART = "productRestart"

# Preset and experience ids share `presetOrExperienceId`: 1-11 are presets and 17 and up
# experiences, and both start with the same `controlpresetorexperience {preset, action}`.
GCS_EXPERIENCE_MIN_ID = 17

# HUB: favorite-centric. There is no direct "set outlet/temp/flow now" command —
# `valvecontrol` and `steamcontrol` take only an on/off toggle and run the controller's own
# defaults. The app has exactly the nine HUB command paths below plus `hub/factoryreset`;
# there is no light, music, volume or temperature command anywhere.
HUB_VALVE_CONTROL = f"{COMMANDS}/hub/valvecontrol"
HUB_STEAM_CONTROL = f"{COMMANDS}/hub/steamcontrol"
HUB_FAVORITE_CONTROL = f"{COMMANDS}/hub/favorite/control"
HUB_FAVORITE = f"{COMMANDS}/hub/favorite"
HUB_STOP_ALL = f"{COMMANDS}/hub/stopall"
HUB_SHOWER_EXPERIENCE = f"{COMMANDS}/hub/shower/experience/control"
HUB_STEAM_EXPERIENCE = f"{COMMANDS}/hub/steam/experience/control"
HUB_ICESHOWER_EXPERIENCE = f"{COMMANDS}/hub/iceshower/experience/control"

# Faucets: a measured dispense `{deviceId, quantity (litres), sku, tenantId}` and plain
# water on/off `{action: "ON"|"OFF", deviceId, sku, tenantId}`. Both live-verified.
FAUCET_DISPENSE = f"{COMMANDS}/faucet/dispense"
FAUCET_ONOFF = f"{COMMANDS}/faucet/onoff"

# All three HUB experience endpoints share one body; the path is chosen by the category the
# experience came from in the experiences read. Sending a shower experience to the steam
# path does not work.
EXPERIENCE_ENDPOINTS = {
    "showerExperiences": HUB_SHOWER_EXPERIENCE,
    "steamExperiences": HUB_STEAM_EXPERIENCE,
    "iceShowerExperiences": HUB_ICESHOWER_EXPERIENCE,
}

# ---------------------------------------------------------------------------
# Response status codes
# ---------------------------------------------------------------------------
# Kohler returns these inside the response body, not only as HTTP status. `statusCode` is
# a string (`docs/protocol/platform.md` §2.1), so compare as strings — an int comparison
# misses `"900"`.
STATUS_DEVICE_OFFLINE = "900"
# Editing a favorite while the system is running is rejected. Activating one is not. The
# app shows the same text for 901.
STATUS_DEVICE_RUNNING = "902"
STATUS_DEVICE_RUNNING_ALT = "901"
# A firmware update is installing; the device refuses commands until it finishes.
STATUS_FIRMWARE_UPDATING = "903"
# Faucet-only: "Water could not be dispensed. Please turn on your faucet manually."
STATUS_NOT_DISPENSED = "906"
# The rest of the app's table, for messages, in its words where they are clear.
STATUS_MESSAGES = {
    STATUS_FIRMWARE_UPDATING: "a firmware update is in progress on the device",
    "904": "the device reported a product error",
    "905": "the device is preparing to retry a firmware update",
    "908": "the firmware is already up to date",
    "909": "the device reported a product error",
    "911": "the device reported a product error",
    STATUS_NOT_DISPENSED: "the faucet could not dispense water",
    "915": "the maximum number of favorites or presets is already stored",
    "916": "a favorite or preset with that name already exists",
    "917": "Kohler's cloud reported an unspecified error",
    "918": "the device reported a product error",
    # Toilets: the app's "Please be seated…" and "Please ensure no one is seated…".
    "913": "someone must be seated on the toilet for this",
    "914": "nobody may be seated on the toilet for this",
    # EvoCycle: the app adds "Please wait while ozone dissipates".
    "919": (
        "the shower's ozone generator reported an error; stay out of the shower while "
        "the ozone clears"
    ),
}

# ---------------------------------------------------------------------------
# GCS warmup modes
# ---------------------------------------------------------------------------
# Warm-up is a stored mode, not a run-now command: writing the mode is the enable/disable,
# and an enabled mode runs by itself. A `warmup` command without a `warmUp` field returns
# 200 and is ignored. See `docs/protocol/gcs_valve.md` §5.
WARMUP_DISABLED = "warmUpDisabled"
WARMUP_ALL_OUTLETS_NOW = "warmUpAllOutletsWithNoStartDelay"
WARMUP_ALL_OUTLETS = "warmUpAllOutlets"
WARMUP_SELECTED_OUTLETS_NOW = "warmUpSelectedOutletsWithNoStartDelay"
WARMUP_SELECTED_OUTLETS = "warmUpSelectedOutlets"

#: The three modes the current Konnect app writes, and the only ones anything should write.
WARMUP_MODES_CURRENT = (
    WARMUP_DISABLED,
    WARMUP_ALL_OUTLETS_NOW,
    WARMUP_SELECTED_OUTLETS_NOW,
)

#: The two delayed-start variants, kept because the firmware still parses them and a valve
#: can be holding one. Decodable, not writable: nothing defines what their delay is.
WARMUP_MODES_LEGACY = (WARMUP_ALL_OUTLETS, WARMUP_SELECTED_OUTLETS)

#: Every value the firmware recognises.
WARMUP_MODES = WARMUP_MODES_CURRENT + WARMUP_MODES_LEGACY

# warmUpState carries two independent axes: `warmUp` is the mode above, `state` is whether
# it is running right now.
WARMUP_IN_PROGRESS = "warmUpInProgress"
WARMUP_NOT_IN_PROGRESS = "warmUpNotInProgress"

# Registering a "mobile device" is how a client obtains Azure IoT Hub credentials for the
# real-time status stream. The returned SAS password is short-lived and per-session: obtain
# it per run and never persist it.
MOBILE_SETTINGS = "/platform/api/v1/mobile/settings"

# ---------------------------------------------------------------------------
# MQTT — Azure IoT Hub
# ---------------------------------------------------------------------------
# Status arrives as direct-method messages; all writes go over HTTPS `/commands/*`. The
# app also publishes a `MOBILECONNECT` telemetry event on every connect, which this client
# does not send (`docs/protocol/platform.md` §4.2).
MQTT_PORT = 8883
# Direct methods must be answered here or the service treats them as unhandled.
MQTT_RESPONSE_TOPIC = "$iothub/methods/res/200/?$rid={rid}"

# Every message arrives on `$iothub/methods/POST/ExecuteControlCommand/?$rid=N`; the
# device-scoped topics some clients also subscribe to deliver nothing.
MQTT_SUBSCRIBE_TOPIC = "$iothub/methods/POST/#"

# A fresh registration receives nothing for roughly the first minute, despite a clean
# CONNECT and granted SUBACKs. Register once, hold the connection, and treat early silence
# as meaningless; reconnecting per command receives nothing at all.
MQTT_WARMUP_SECONDS = 60

# The direct-method subscription is account-level, so a session receives every device's
# messages. Filter on payload deviceid and sku.
MSG_GCS_SOLO_STATUS = "GCS_SOLO_STS"
MSG_GCS_PRESET_STATUS = "GCS_PRESET_STS"
MSG_GCS_WARMUP_STATUS = "GCS_WARM_STS"
MSG_GCS_EXPERIENCE_STATUS = "READ_GCS_EXPERIENCE_STS"
MSG_GCS_OUTLET_CONFIG = "READ_GCS_OUTLET_CONFIG_CFG"
# One record per touch interface (`docs/protocol/gcs_valve.md` §6). This integration only
# treats it as a sign of life.
MSG_GCS_UI_CONFIG = "READ_GCS_UI_CFG"
MSG_GCS_REBOOT = "DEVICE_REBOOT_STS"
# Seen live but not handled anywhere in the app, so its shape is known only from captures.
MSG_FIRMWARE_VERSIONS = "READ_ALL_INTERFACES_FIRMWARE_VERSION_STATUS_INFO"
# Two the app handles that this integration only records as proof of life: the Remove
# Product confirmation and the dispensed-volume counter `bathfillervolume` requests
# (`docs/protocol/gcs_valve.md` §7-8).
MSG_GCS_DELETE_PROFILE = "GCS_DELETE_PROFILE_STS"
MSG_GCS_DISPENSED_VOLUME = "READ_DISPENSED_WATER_VOLUME_STS"

#: `currentSystemState` values. Only the first two have been captured; the app also acts
#: on `error` (case-insensitive; a valve fault) and `FirmwareUpdate` (an install is
#: running).
SYSTEM_STATE_NORMAL = "normalOperation"
SYSTEM_STATE_SHOWER = "showerInProgress"
SYSTEM_STATE_ERROR = "error"
SYSTEM_STATE_FIRMWARE = "FirmwareUpdate"

MSG_HUB_SHOWER_VALVE = "SHOWER_VALVE_STS"
MSG_HUB_STEAM = "STEAM_STS"
MSG_HUB_MUSIC = "MUSIC_STS"
MSG_HUB_LIGHT = "LIGHT_STS"
MSG_HUB_FAVORITE = "FAVORITE_STS"
MSG_HUB_SYSTEM = "SYSTEM_STS"
# The whole favorites list, pushed after every create, edit and delete and on reboot, so it
# is the only favorite refresh needed. The CREATE/UPDATE/DELETE_FAVORITE_STS acks are not
# modelled: this snapshot follows each of them 1-3 s later.
MSG_HUB_FAVORITES_SNAPSHOT = "FAVORITES_SNAPSHOT"
# Experience run state, one code per category — `{code, name, ready, status}` per attribute,
# where `name` is the experience title and `status` is `ON`/`OFF`.
MSG_HUB_SHOWER_EXPERIENCE = "SHOWER_EXP_STS"
MSG_HUB_STEAM_EXPERIENCE = "STEAM_EXP_STS"
MSG_HUB_ICE_EXPERIENCE = "ICE_SHOWER_EXP_STS"
MSG_HUB_EXPERIENCE_CODES = frozenset(
    {MSG_HUB_SHOWER_EXPERIENCE, MSG_HUB_STEAM_EXPERIENCE, MSG_HUB_ICE_EXPERIENCE}
)

# Faucets (`SEN`/`SET`). `SENSATE_STS` carries `status` ("On"/"Off") and `handle`
# ("OPEN"/"CLOSED"); `SENSATE_EXP_STS` a preset starting or ending (`experienceid`, `name`,
# `status`); `SENSATE_LEAK_DETECTED_ALT` a leak, of which the app checks only the code; and
# `INSTALL_FIRMWARE_STS` the end of a firmware install (`status` "Installed"/"Aborted").
MSG_FAUCET_STATUS = "SENSATE_STS"
MSG_FAUCET_PRESET = "SENSATE_EXP_STS"
MSG_FAUCET_LEAK = "SENSATE_LEAK_DETECTED_ALT"
MSG_FIRMWARE_INSTALL = "INSTALL_FIRMWARE_STS"

#: `STEAM_STS` `status` while the steam generator runs its self-clean. The app shows "Power
#: clean is in progress. Please stay out of your shower." Neither ON nor OFF.
HUB_STEAM_POWERCLEAN = "POWERCLEAN"

#: Device `connectionState` values. Only `Connected` has been captured; the app treats a
#: device as online unless the value is exactly `Disconnected`
#: (`docs/protocol/platform.md` §5).
CONNECTION_CONNECTED = "Connected"
CONNECTION_DISCONNECTED = "Disconnected"

# ---------------------------------------------------------------------------
# HUB local LAN API
# ---------------------------------------------------------------------------
# Setup, configuration and diagnostics only: this surface cannot actuate anything, so real
# control is cloud-side. The host is `hub-configuration`'s
# `configuration.about.hub.wlan.ip`. See `docs/protocol/hub_controller.md` §6.
LOCAL_API_BASE = "http://{host}/web/api/v1/device"
LOCAL_LOGIN = "request_user_login"
LOCAL_COMMAND = "req_update_command"

# The hub's JWT is short-lived (minutes) and obtained from a PIN. These endpoints are
# reachable with no token at all, and two of them mutate state.
LOCAL_PREAUTH_ENDPOINTS = frozenset(
    {
        "get_hub_running_state",
        "get_hub_version_info",
        "hub_date_config_state",
        "set_hub_datetime",
    }
)

# Baked into the hub's Angular bundle. Used to encrypt the PIN as
# base64(RSA_PKCS1v15(sha256(pin).hexdigest_ascii)).
LOCAL_RSA_PUBLIC_KEY_B64 = (
    "MIGJAoGBAOBnPtJlU6y62vyrcHgqZPAlr+FM10BpUxBvRx5u0fXNEjXcda4y3WSU"
    "2ECzf9HcmDU5r6fD2jiFPyTuXu7jY2qzAI7QME6eoaJd2q+QLKpcUVq5MTeFo9b6"
    "zpZlGHUiiy0NrFdKPjD+UdPXi/t1oEKaj/loWiZ7p0P02paUoI41AgMBAAE="
)
