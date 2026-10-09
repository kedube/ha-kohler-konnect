"""Kohler Konnect protocol library.

Pure Python with no Home Assistant imports, so it can be tested off-box and lifted into its
own package later without changes. Everything that knows about Kohler's wire formats lives
here; everything that knows about Home Assistant lives in the parent integration.

One sign-in (`auth`), one REST client (`client`) and one MQTT stream (`mqtt`) serve every
product on the account. Product modules hold what differs:

* **Anthem** (SKU ``GCS``, `gcs`) — the digital valve body with built-in Wi-Fi, addressed
  directly. Every start specifies the full valve state as a hex command word.
* **Anthem Plus** (SKU ``HUB``, `hub`) — the Linux system controller that drives the
  valves and integrates music, lighting, and steam. Control is organised around favorites.
* **Sensate and Setra faucets** (SKUs ``SEN`` and ``SET``, `faucet`) — water on and off,
  and measured dispenses in litres.

Water usage history has the same shape for every product (`usage`).

Written against live captures and two decompiles of the Konnect Android app (3.0.1, and
3.0.6 on 2026-10-07). ``docs/protocol/`` is the developer reference — every endpoint, body,
message and code, marked app-confirmed or live-verified — and is written to be reused by
other Kohler Konnect integrations. The ``kohler-anthem`` library reads a few of these
behaviours differently; it was decompiled from the same APK but not checked against
captures, so where the two disagree, this package follows the captures.
"""

from __future__ import annotations

from .auth import (
    AuthError,
    AuthUnavailable,
    InvalidCredentials,
    KohlerAuth,
    SignInBlocked,
    TokenSet,
    credential_is_dead,
    decode_tenant_id,
)
from .client import (
    Customer,
    Device,
    DeviceOffline,
    DeviceRunning,
    KohlerClient,
    KohlerError,
    UnexpectedResponse,
)
from .const import (
    MSG_GCS_SOLO_STATUS,
    MSG_GCS_WARMUP_STATUS,
    WARMUP_DISABLED,
    WARMUP_MODES,
    WARMUP_MODES_CURRENT,
    WARMUP_MODES_LEGACY,
)
from .faucet import (
    FaucetDevice,
    FaucetEvent,
    FaucetPreset,
    FaucetSnapshot,
    FirmwareInfo,
)
from .gcs import GcsDevice
from .hub import (
    HubCapabilities,
    HubDevice,
    HubSettings,
    zone_number,
    zone_outlet_flags,
)
from .journal import WARMUP_README, DebugJournal
from .models import (
    DEFAULT_VALVE_MODEL,
    VALVE_MODELS,
    OutletStateSource,
    ValveModel,
    get_valve_model,
    model_for_topology,
    resolve_outlet_source,
)
from .mqtt import Envelope, KonnectMqttStream
from .raw_log import RawMqttLog
from .report_log import ReportLog
from .state import GcsPreset, GcsState, HubState, HubZone
from .topology import (
    describe as describe_topology,
)
from .topology import (
    topology_from_hub_configuration,
    topology_from_valve_settings,
)
from .usage import usage_series, usage_volume_gallons
from .valve_hex import (
    OUTLETS_PER_VALVE,
    ValveHexError,
    ValveWord,
    celsius_to_unit,
    decode_word,
    encode_pair,
    encode_shower,
    encode_word,
    outlet_mask,
    pause_pair,
    stop_pair,
    unit_to_celsius,
)
from .warmup import journal_event, restore_target, should_restore_warmup
from .warmup_resume import Decision, Outcome, WarmupResume
from .zone_clock import ZoneClock

__all__ = [
    "DEFAULT_VALVE_MODEL",
    "MSG_GCS_SOLO_STATUS",
    "MSG_GCS_WARMUP_STATUS",
    "OUTLETS_PER_VALVE",
    "VALVE_MODELS",
    "WARMUP_DISABLED",
    "WARMUP_MODES",
    "WARMUP_MODES_CURRENT",
    "WARMUP_MODES_LEGACY",
    "WARMUP_README",
    "AuthError",
    "AuthUnavailable",
    "Customer",
    "DebugJournal",
    "Decision",
    "Device",
    "DeviceOffline",
    "DeviceRunning",
    "Envelope",
    "FaucetDevice",
    "FaucetEvent",
    "FaucetPreset",
    "FaucetSnapshot",
    "FirmwareInfo",
    "GcsDevice",
    "GcsPreset",
    "GcsState",
    "HubCapabilities",
    "HubDevice",
    "HubSettings",
    "HubState",
    "HubZone",
    "InvalidCredentials",
    "KohlerAuth",
    "KohlerClient",
    "KohlerError",
    "KonnectMqttStream",
    "Outcome",
    "OutletStateSource",
    "RawMqttLog",
    "ReportLog",
    "SignInBlocked",
    "TokenSet",
    "UnexpectedResponse",
    "ValveHexError",
    "ValveModel",
    "ValveWord",
    "WarmupResume",
    "ZoneClock",
    "celsius_to_unit",
    "credential_is_dead",
    "decode_tenant_id",
    "decode_word",
    "describe_topology",
    "encode_pair",
    "encode_shower",
    "encode_word",
    "get_valve_model",
    "journal_event",
    "model_for_topology",
    "outlet_mask",
    "pause_pair",
    "resolve_outlet_source",
    "restore_target",
    "should_restore_warmup",
    "stop_pair",
    "topology_from_hub_configuration",
    "topology_from_valve_settings",
    "unit_to_celsius",
    "usage_series",
    "usage_volume_gallons",
    "zone_number",
    "zone_outlet_flags",
]
