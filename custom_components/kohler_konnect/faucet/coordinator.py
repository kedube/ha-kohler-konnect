"""One faucet's coordinator: polled state, dispenses, leaks, usage and the safety limit.

Unlike the showers, which are push-only, a faucet polls. The MQTT stream is a speed-up:
polling relaxes once the stream has proven it delivers for this faucet, and a poll that
finds a change the stream never announced puts polling back in charge
(`_check_push_announced`). The cadence is described in `docs/user_guide.md`, "How it works".

The sign-in, REST client and MQTT connection belong to the account coordinator, which hands
this faucet its messages and connection changes (`async_push_activity`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from datetime import date, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.async_ import create_eager_task

from ..const import (
    CONF_MAX_RUN_MINUTES,
    DEFAULT_MAX_RUN_MINUTES,
    DISPENSE_MAX,
    DISPENSE_MAX_ML,
    DISPENSE_SETTLE,
    DISPENSE_SLOWEST_FLOW,
    DOMAIN,
    FAUCET_COMMAND_FOLLOW_UP,
    FAUCET_CONFIG_REFRESH_INTERVAL,
    FAUCET_PUSH_GRACE,
    FAUCET_SCAN_INTERVAL_ACTIVE,
    FAUCET_SCAN_INTERVAL_IDLE,
    FAUCET_SCAN_INTERVAL_PUSH,
    FAUCET_SCAN_INTERVAL_PUSH_ACTIVE,
    FAUCET_USAGE_REFRESH_INTERVAL,
    FAUCET_USAGE_SETTLE,
    FIRMWARE_CHECK_INTERVAL,
    ISSUE_FAUCET_API_CHANGED,
    ISSUE_FAUCET_NOT_FOUND,
    MAX_CLEARED_LEAKS,
    REJECTIONS_BEFORE_ISSUE,
    RETRY_AFTER_MAX,
    RETRY_AFTER_MIN,
    device_issue_key,
)
from ..konnect import AuthError, Device, Envelope, KohlerError, credential_is_dead
from ..konnect.const import (
    CONNECTION_CONNECTED,
    FAUCET_SKUS,
    SKU_SENSATE,
    STATUS_DEVICE_OFFLINE,
    STATUS_FIRMWARE_UPDATING,
    STATUS_NOT_DISPENSED,
)
from ..konnect.faucet import (
    HANDLE_CLOSED,
    PROGRESS_DOWNLOADING,
    STATUS_OFF,
    STATUS_ON,
    FaucetDevice,
    FaucetEvent,
    FaucetPreset,
    FaucetSnapshot,
    FirmwareInfo,
    parse_event,
    parse_firmware,
    water_running,
)
from .units import UnitProfile, profile_for, to_ml

if TYPE_CHECKING:
    from ..coordinator import KohlerKonnectCoordinator

_LOGGER = logging.getLogger(__name__)

# Leak events are {"leakDetectionTime": <epoch seconds>} (`docs/protocol/sensate_faucet.md`
# §2.2) and are keyed by that time; one without it by one of these ids, else a hash.
_LEAK_ID_KEYS = ("id", "eventId", "leakId")
_LEAK_TIME = "leakDetectionTime"

# Fields whose values are recorded for diagnostics.
TRACKED_FIELDS = ("status", "progress", "handleState")
# Delays (seconds) before each retry of a safety auto-off that has not turned the water
# off (the OFF failed, or was taken but no reading shows the water off); the last repeats.
# ⚠️ Never give up while the water may be running.
AUTO_OFF_BACKOFF = (60.0, 120.0, 300.0, 600.0, 900.0)
# The tries in a row that have not turned the water off before the repair issue is raised.
AUTO_OFF_FAILURES_BEFORE_ISSUE = 3
ISSUE_FAUCET_AUTO_OFF_FAILED = "faucet_auto_off_failed"
# The longest the cloud's state is allowed to lag Home Assistant's ON (seconds): until
# then a poll's "off" may predate the command. Water never seen running is reliably off
# once two readings after this say so. See `_polls_caught_up`.
_NEVER_RAN_AFTER = 60.0
# A failed re-read (one a message asked for, or a first read that has never answered) is
# retried after this many seconds, doubling up to FAUCET_CONFIG_REFRESH_INTERVAL, rather
# than at every poll. The doublings are capped so a read failing for days cannot overflow.
_REREAD_RETRY = 60.0
_REREAD_DOUBLINGS = 8
# The dispense timer fires this long after the limit, so float rounding of the deadline
# cannot leave a dispense counted as running when it fires.
_DISPENSE_TIMER_SLACK = 1.0
# Kohler statusCodes that have their own message for a refused command.
_REFUSALS = {
    STATUS_DEVICE_OFFLINE: "faucet_offline",
    STATUS_FIRMWARE_UPDATING: "firmware_updating",
    STATUS_NOT_DISPENSED: "not_dispensed",
}
# Water usage history: the monthly series for This Month and This Year, the daily one for
# Today and This Week — the same windows the valves read.
_USAGE_MONTHS_BACK = 400
_USAGE_DAYS_BACK = 35


def leak_fingerprint(event: Any) -> str:
    """The key of a leak event that carries no detection time."""
    if isinstance(event, dict):
        for key in _LEAK_ID_KEYS:
            if event.get(key) not in (None, ""):
                return f"{key}:{event[key]}"
    raw = json.dumps(event, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def leak_time(event: Any) -> float | None:
    """When a leak event was detected, in epoch seconds."""
    value = event.get(_LEAK_TIME) if isinstance(event, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    # Seconds, as the app reads it; tolerate milliseconds.
    return value / 1000 if value > 1e11 else float(value)


def leak_key(event: Any) -> str:
    """A leak event's identity: its detection time, else its old key."""
    if leak_time(event) is not None:
        return f"{_LEAK_TIME}:{event[_LEAK_TIME]}"
    return leak_fingerprint(event)


def dispense_limit(liters: float | None) -> float:
    """Seconds after which a dispense whose end is never reported counts as over.

    ``None`` (an amount that is not known) allows for the largest dispense.
    """
    if liters is None:
        liters = DISPENSE_MAX_ML / 1000
    return max(
        DISPENSE_MAX.total_seconds(),
        float(math.ceil(60 + liters / DISPENSE_SLOWEST_FLOW * 60)),
    )


def preset_labels(presets: Iterable[FaucetPreset]) -> dict[str, FaucetPreset]:
    """Each preset by a distinct name; repeats get " (2)", " (3)" and so on."""
    labels: dict[str, FaucetPreset] = {}
    for preset in presets:
        label, n = preset.title, 1
        while label in labels:
            n += 1
            label = f"{preset.title} ({n})"
        labels[label] = preset
    return labels


def _push_signature(state: dict[str, Any]) -> bool | None:
    """The change the stream must announce: water on or off."""
    return water_running(state)


def _may_change_config(event: FaucetEvent | None) -> bool:
    """Whether a faucet message can change what the configuration read holds.

    A water or handle report cannot; anything else (a leak alert, an install result, a
    preset run, a message not understood) may. Skipping the re-read for water reports
    spares REST calls on the account's shared rate limit.
    """
    return event is None or not (event.status or event.handle)


async def _answered() -> bool:
    """A read that was not due: nothing to do, so nothing failed."""
    return True


async def _side_by_side(*reads: Coroutine[Any, Any, Any]) -> list[Any]:
    """Run independent reads at once; the first to raise is raised once all have finished.

    Each read catches its own failures, so what escapes is a dead credential (or a bug).
    Waiting for all keeps the others from updating state after the refresh has failed.
    Eager tasks let reads with nothing to do finish without a trip round the event loop.
    """
    results = await asyncio.gather(
        *(create_eager_task(read) for read in reads), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


def _not_carried_out(err: HomeAssistantError) -> bool:
    """Whether a failed command certainly did nothing at the faucet.

    True when it was refused before it was sent, or Kohler answered that it was not carried
    out. One that got no answer — a network error, a timeout — or a server error may still
    have reached the faucet.
    """
    cause = err.__cause__
    if not isinstance(cause, KohlerError):
        return True  # refused here, or the sign-in failed before anything was sent
    if cause.status is not None:
        return cause.status < 500
    return cause.code is not None  # "offline" and the like, in the body of a reply


class FaucetCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one faucet's state and, less often, its configuration, presets and usage."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        account: KohlerKonnectCoordinator,
        device: Device,
        name: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {name}",
            update_interval=FAUCET_SCAN_INTERVAL_IDLE,
            # The default 10 s cooldown would delay a stream message that arrives right
            # after a command; 1 s still collapses bursts.
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=1.0, immediate=True
            ),
        )
        self.account = account
        self.client = account.client
        self.device_id: str = device.device_id
        #: The device name in Home Assistant — the faucet's Konnect name.
        self.device_name = name
        self.serial_number = device.serial_number
        # Sent with every command; from the account's device list, as the app does.
        sku = device.sku.upper()
        self.sku: str = sku if sku in FAUCET_SKUS else SKU_SENSATE
        self.device = FaucetDevice(self.client, self.device_id, self.sku)
        self.profile: UnitProfile = profile_for(account.water_units)
        self.max_run_minutes: int = int(
            entry.options.get(CONF_MAX_RUN_MINUTES, DEFAULT_MAX_RUN_MINUTES)
        )
        self.config: dict[str, Any] = {}
        # When the configuration last answered, and when it and the presets were last read
        # on their interval, answered or not.
        self._config_fetched_at: float | None = None
        self._interval_read_at: float | None = None
        self._config_due = False
        # The reads a message asked for, or that have never answered, that have not worked
        # yet — "config", "presets", "firmware" — and when to try them again; see
        # `_async_refresh_config`.
        self._rereads: set[str] = set()
        self._reread_after = 0.0
        self._reread_failures = 0
        self.presets: dict[str, FaucetPreset] = {}
        self.preset_source: str | None = None
        # The preset id chosen in the Preset select; see chosen_preset.
        self.preset_choice: str | None = None
        # Kohler's firmware check; None until it has answered.
        self.firmware: FirmwareInfo | None = None
        self._firmware_checked_at: float | None = None
        self._firmware_due = False
        self.connection_state: str | None = None
        self.last_connected: Any = None
        #: When the faucet last reported something new (epoch seconds): a stream message
        #: about it, or a poll whose state differs from the last. Not every poll, which
        #: says only when we asked.
        self.last_report: float | None = None
        self.seen_values: dict[str, set[str]] = {
            field: set() for field in (*TRACKED_FIELDS, "connectionState")
        }
        self._fast_poll_until = 0.0
        self._rejections = 0
        self._check_issues = True
        # Shared between the "Dispense amount" number and the button that uses it.
        self.dispense_amount_ml: float = to_ml(
            self.profile.number_default, self.profile.number_unit
        )
        self.last_dispense_liters: float | None = None
        # Dispenses: one Home Assistant started (until the faucet reports the water off)
        # and a preset run from the app (until the stream says so).
        self._dispense_started: float | None = None
        self._dispense_limit = DISPENSE_MAX.total_seconds()
        self._dispense_seen_on = False
        self._dispense_feed_on = False
        self._dispense_preset: str | None = None
        self._preset_since: float | None = None
        self._preset_limit = DISPENSE_MAX.total_seconds()
        self._app_preset: str | None = None
        self._water_was_running: bool | None = None
        # Ends a dispense whose end never arrives, at its dispense_limit(): one timer for
        # Home Assistant's dispense ("dispense") and one for the app's preset ("preset"),
        # since the two can overlap.
        self._dispense_timers: dict[str, CALLBACK_TYPE] = {}
        # The stream's latest water status and handle position, with when each arrived
        # (monotonic), so a poll that read the faucet before then cannot put the older
        # state back. See `_stream_values_since`.
        self._stream_values: dict[str, tuple[float, str]] = {}
        # When the water status in `data` was learned (monotonic), and whether the stream
        # reported it rather than a poll. Only a status report sets it, not a handle
        # report; see `_known_off`.
        self._status_at: float | None = None
        self._status_from_feed = False
        # Water usage, as the raw monthly and daily series — the same shape the valves
        # keep, so the water sensors are shared. See `konnect/usage.py`.
        self.usage: dict[str, Any] = {}
        self.usage_daily: dict[str, Any] = {}
        self._usage_fetched_at: float | None = None
        self._usage_due_at: float | None = None
        self._usage_day: date | None = None
        # The month the monthly series was last read in; see `_async_refresh_usage`.
        self._usage_month: str | None = None
        # The stream proves itself per faucet: a message about this faucet. Polls that find
        # a change it never announced count as missed and stop polling from relying on it.
        # Both are kept across restarts (see `load`); `push_restored` says the verification
        # came from before the restart, with no message since.
        self.push_verified = False
        self.push_restored = False
        self.push_messages = 0
        self._push_synced_at: float | None = None
        self._polled_at: float | None = None
        self._push_check: CALLBACK_TYPE | None = None
        self.push_missed = 0
        self._cleared_leaks: list[str] = []
        # Leak events detected up to this time (epoch seconds) are cleared, even if Kohler
        # adds them to the history after "Clear leak alert".
        self._leaks_cleared_through: float | None = None
        # A real-time leak alert from the stream, until cleared (epoch seconds).
        self._leak_alert_at: float | None = None
        # Safety auto-off: wall-clock deadline and when it was set (both persisted), and its
        # timer, which fires at the deadline and then for each try after it.
        self._auto_off_at: float | None = None
        self._auto_off_set_at: float | None = None
        self._auto_off_timer: CALLBACK_TYPE | None = None
        # Whether the water has been seen running since the deadline was set, by a poll or
        # the stream — persisted with it, so water turned off during a restart still drops
        # the limit. Until then "off" may only be the cloud lagging the command.
        self._seen_running = False
        # The stream saw it running: a poll can lag the stream, so then only the stream
        # can say it stopped (while it is trusted). Not persisted: after a restart the
        # stream's "off" may have been missed, and polls must be able to say so.
        self._feed_saw_running = False
        # When the limit was set (monotonic; for a restored one, worked out from the saved
        # time); see `_known_off`.
        self._auto_off_armed_at = 0.0
        # "Off" readings, well after the command, of water never seen running.
        self._off_readings = 0
        # The OFFs tried once the limit was reached, which set the back-off; how many in a
        # row have not turned the water off; when the latest was sent (monotonic); and
        # whether Kohler took it but nothing has shown the water off since.
        self._auto_off_tries = 0
        self._auto_off_failures = 0
        self._auto_off_sent_at: float | None = None
        self._auto_off_unconfirmed = False
        # Set by `async_shutdown`: from then on nothing is sent, armed or saved — the next
        # setup's copy is the live one.
        self._shut_down = False

    def __repr__(self) -> str:
        return f"<FaucetCoordinator {self.device_id} {self.device_name!r}>"

    # --- persistence ------------------------------------------------------------

    def load(self, stored: dict[str, Any]) -> None:
        """Pick up what this faucet kept across restarts. See `async_save`."""
        self._cleared_leaks = list(stored.get("cleared_leaks", []))
        for key, attr in (
            ("leaks_cleared_through", "_leaks_cleared_through"),
            ("leak_alert_at", "_leak_alert_at"),
        ):
            if isinstance(value := stored.get(key), (int, float)):
                setattr(self, attr, float(value))
        # ⚠️ A reload or restart must not lose the safety limit, nor whether its water was
        # seen running, unless the limit has since been set to 0 minutes, which drops it.
        deadline = stored.get("auto_off_at")
        # A dropped limit is saved as dropped (at the end, once the rest is loaded), so a
        # stale deadline cannot come back if the limit is turned on again later.
        dropped = isinstance(deadline, (int, float)) and self.max_run_minutes <= 0
        if isinstance(deadline, (int, float)) and not dropped:
            now = time.time()
            set_at = stored.get("auto_off_set_at")
            if not isinstance(set_at, (int, float)):
                # Not stored: assume it was set one limit's length before the deadline.
                set_at = deadline - self.max_run_minutes * 60
            self._auto_off_at = float(deadline)
            self._auto_off_set_at = min(float(set_at), now)
            self._seen_running = stored.get("auto_off_seen_running") is True
            # Counted from when the water was turned on, not from setup, so a read at
            # setup can already count as caught up with the command (`_known_off`).
            self._auto_off_armed_at = time.monotonic() - (now - self._auto_off_set_at)
            # Not before a read has had the chance to say whether the water is still on,
            # so a deadline that passed while Home Assistant was down does not send a
            # blind OFF. If the read fails, the OFF is sent all the same.
            self._schedule_auto_off(
                max(deadline - now, FAUCET_COMMAND_FOLLOW_UP.total_seconds())
            )
        # Restored so a restart does not fall back to fast polling until the faucet is next
        # used. Polling still relaxes only while the stream is connected (`push_trusted`),
        # and a missed change still revokes it (`_async_push_check`).
        self.push_verified = self.push_restored = stored.get("push_verified") is True
        # What this faucet's own preset list last held; see
        # `FaucetDevice.async_get_presets` for how it is used after a restart.
        ids = stored.get("faucet_preset_ids")
        if isinstance(ids, list):
            self.device.faucet_preset_ids = frozenset(str(i) for i in ids)
        if dropped:
            self._async_save_soon("safety limit")

    def stored(self) -> dict[str, Any]:
        return {
            "cleared_leaks": self._cleared_leaks,
            "leaks_cleared_through": self._leaks_cleared_through,
            "leak_alert_at": self._leak_alert_at,
            "auto_off_at": self._auto_off_at,
            "auto_off_set_at": self._auto_off_set_at,
            "auto_off_seen_running": self._seen_running,
            "push_verified": self.push_verified,
            "faucet_preset_ids": (
                None if (ids := self.device.faucet_preset_ids) is None else sorted(ids)
            ),
        }

    async def async_save(self) -> None:
        """Persist this faucet's state through the account's store.

        Never after unload: the next setup has read the store and is the live copy, and a
        late save from this one would overwrite what that one keeps.
        """
        if not self._shut_down:
            await self.account.async_save_device_state(self)

    @callback
    def _async_save_soon(self, what: str) -> None:
        """Persist from a callback. Not after unload, as `async_save`."""
        if not self._shut_down:
            self.config_entry.async_create_task(
                self.hass, self.async_save(), f"{DOMAIN} {what}"
            )

    async def async_shutdown(self) -> None:
        # The persisted deadline is picked up again by the next setup, which keeps trying.
        self._shut_down = True
        for cancel in (
            self._auto_off_timer,
            self._push_check,
            *self._dispense_timers.values(),
        ):
            if cancel is not None:
                cancel()
        self._auto_off_timer = self._push_check = None
        self._dispense_timers.clear()
        await super().async_shutdown()

    # --- polling ----------------------------------------------------------------

    @property
    def push_trusted(self) -> bool:
        """The stream is connected and has proven it reports this faucet's changes."""
        stream = self.account.stream
        return stream is not None and stream.connected and self.push_verified

    @property
    def _idle_interval(self) -> timedelta:
        return (
            FAUCET_SCAN_INTERVAL_PUSH
            if self.push_trusted
            else FAUCET_SCAN_INTERVAL_IDLE
        )

    def _raise_if_dead(self, err: Exception) -> None:
        """Re-raise a rejected credential as reauth; anything else is the caller's to log.

        For the reads beside the state poll — configuration, presets, firmware, usage — where
        a failure keeps what was held, but a dead credential must still reach the user.
        """
        if credential_is_dead(err):
            raise self._auth_failed(err) from err

    def _auth_failed(self, err: Exception) -> Exception:
        """The exception a failed read raises: reauth for a dead credential, else retry."""
        if credential_is_dead(err):
            return ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            )
        return UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="update_failed",
            translation_placeholders={"error": str(err)},
        )

    async def _async_update_data(self) -> dict[str, Any]:
        # Errors fall back to slow polling; no point hammering a failing cloud.
        self.update_interval = self._idle_interval
        # The configuration, preset, firmware and usage reads come first, so none sits
        # between reading the state and applying it: a stream message arriving in that gap
        # would be overwritten by older state. After a failed poll they wait until the
        # state answers again.
        if self.last_update_success:
            await self._async_refresh_extras()
        polled_at = time.monotonic()
        try:
            snapshot = FaucetSnapshot.from_payload(
                await self.client.async_get_faucet_state(self.device_id)
            )
        except AuthError as err:
            raise self._auth_failed(err) from err
        except KohlerError as err:
            await self._async_note_rejection(err)
            if err.retry_after is not None:
                self.update_interval = timedelta(
                    seconds=min(
                        max(err.retry_after, RETRY_AFTER_MIN.total_seconds()),
                        RETRY_AFTER_MAX.total_seconds(),
                    )
                )
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        self._clear_issues()
        state = snapshot.state
        if newer := self._stream_values_since(polled_at):
            # The stream reported while the read was on its way: its water and handle are
            # the newer. Read again to be sure, as the re-read the stream asked for waited
            # on this one and may have been dropped.
            state = {**state, **newer}
            self.config_entry.async_create_task(
                self.hass, self.async_request_refresh(), f"{DOMAIN} faucet re-read"
            )
        self._check_push_announced(state)
        if state != self.data:
            self.last_report = time.time()
        self._polled_at = polled_at
        self.connection_state = snapshot.connection_state
        self.last_connected = snapshot.last_connected
        self._record_values(state)
        running = water_running(state)
        if "status" in newer:
            # The stream's status, followed when it arrived; counting it again as this
            # poll's would make one reading two.
            self._status_at = self._stream_values["status"][0]
            self._status_from_feed = True
        else:
            self._status_at, self._status_from_feed = polled_at, False
            self._note_water(running, from_feed=False, at=polled_at)

        quantity = state.get("quantity")
        if isinstance(quantity, (int, float)) and quantity > 0:
            self.last_dispense_liters = float(quantity)

        if running or time.monotonic() < self._fast_poll_until:
            # A trusted stream reports the change the moment it happens.
            self.update_interval = (
                FAUCET_SCAN_INTERVAL_PUSH_ACTIVE
                if self.push_trusted
                else FAUCET_SCAN_INTERVAL_ACTIVE
            )
        elif self._auto_off_at is not None and not self._seen_running:
            # Water Home Assistant turned on that has not been seen running: keep reading
            # quickly until it is, or is reliably off (`_note_water_for_auto_off`). The
            # stream never announces that nothing happened, so idle polling would leave
            # the limit armed for whoever uses the faucet next.
            self.update_interval = min(
                self.update_interval, FAUCET_SCAN_INTERVAL_PUSH_ACTIVE
            )
        return state

    def _stream_values_since(self, since: float) -> dict[str, str]:
        """The stream's water status and handle position that arrived after ``since``."""
        return {
            field: value
            for field, (at, value) in self._stream_values.items()
            if at > since
        }

    def _record_values(self, state: dict[str, Any]) -> None:
        values = {field: state.get(field) for field in TRACKED_FIELDS}
        values["connectionState"] = self.connection_state
        for field, value in values.items():
            self._record_value(field, value)

    def _record_value(self, field: str, value: Any) -> None:
        if isinstance(value, str) and value not in self.seen_values[field]:
            self.seen_values[field].add(value)
            _LOGGER.debug("New faucet %s value: %s", field, value)

    async def _async_refresh_extras(self) -> None:
        """Refresh the configuration, presets, firmware check and water usage, at once."""
        await _side_by_side(self._async_refresh_config(), self._async_refresh_usage())

    async def _async_refresh_config(self) -> None:
        """Refresh firmware, leak history and presets; failures keep the old copy.

        On its interval, every read; between, only what a message asked for (a leak, an
        install, a new preset) or what has never answered, until it works. Each failed read
        is retried on its own after `_REREAD_RETRY`, doubling up to the interval, not at
        every poll.
        """
        now = time.monotonic()
        if self._config_due:
            # Cleared first, so a message that arrives during the reads asks again — and a
            # new ask is tried at once, whatever an older one is waiting out.
            self._config_due = False
            self._rereads |= {"config", "presets"}
            self._reread_after = 0.0
        interval = (
            self._interval_read_at is None
            or now - self._interval_read_at
            >= FAUCET_CONFIG_REFRESH_INTERVAL.total_seconds()
        )
        asked = set(self._rereads) if now >= self._reread_after else set()
        # A firmware check that failed waits with the rest: it is in `_rereads` too.
        if not interval and not asked:
            return
        wanted = {"config", "presets"} if interval else asked & {"config", "presets"}
        read, presets_read, firmware_read = await _side_by_side(
            self._async_read_config() if "config" in wanted else _answered(),
            self._async_read_presets() if "presets" in wanted else _answered(),
            self._async_check_firmware(now),
        )
        if interval:
            self._interval_read_at = now
        if "config" in wanted and read:
            self._config_fetched_at = now
        failed = {
            name
            for name, ok in (
                ("config", read),
                ("presets", presets_read),
                ("firmware", firmware_read),
            )
            if not ok
        }
        # Retried before the interval: what a message asked for, and what has never
        # answered — without which the leak sensor and the preset list stay empty.
        never = {"config"} if self._config_fetched_at is None else set()
        if self.preset_source is None:
            never.add("presets")
        self._rereads = (self._rereads - wanted - {"firmware"}) | (
            failed & (self._rereads | never | {"firmware"})
        )
        if self._rereads & failed:
            self._reread_failures = min(self._reread_failures + 1, _REREAD_DOUBLINGS)
            self._reread_after = now + min(
                _REREAD_RETRY * 2 ** (self._reread_failures - 1),
                FAUCET_CONFIG_REFRESH_INTERVAL.total_seconds(),
            )
        elif not self._rereads:
            self._reread_failures = 0

    async def _async_read_config(self) -> bool:
        """Read the configuration (firmware, leak history); False if that failed."""
        try:
            self.config = await self.client.async_get_faucet_configuration(
                self.device_id
            )
        except (AuthError, KohlerError) as err:
            self._raise_if_dead(err)
            _LOGGER.debug("Could not refresh faucet configuration: %s", err)
            return False
        return True

    async def _async_read_presets(self) -> bool:
        """Read the presets; False if that failed, keeping those read last."""
        listed = self.device.faucet_preset_ids
        try:
            presets, self.preset_source = await self.device.async_get_presets()
        except (AuthError, KohlerError) as err:
            self._raise_if_dead(err)
            _LOGGER.debug("Could not refresh Konnect presets: %s", err)
            return False
        self.presets = {p.preset_id: p for p in presets}
        if self.device.faucet_preset_ids != listed:
            self._async_save_soon("preset list")
        return True

    async def _async_check_firmware(self, now: float) -> bool:
        """Ask Kohler about newer firmware now and then; failures keep the old answer.

        A check Kohler did not answer is asked again with the next configuration read; one a
        message asked for (an install ended), at the next poll — False says so.
        """
        if (
            not self._firmware_due
            and self._firmware_checked_at is not None
            and now - self._firmware_checked_at
            < FIRMWARE_CHECK_INTERVAL.total_seconds()
        ):
            return True
        checked_at, due = self._firmware_checked_at, self._firmware_due
        self._firmware_checked_at = now
        self._firmware_due = False
        try:
            self.firmware = parse_firmware(
                await self.client.async_get_faucet_firmware(self.device_id)
            )
        except (AuthError, KohlerError) as err:
            self._raise_if_dead(err)
            _LOGGER.debug("Could not check for faucet firmware: %s", err)
            if isinstance(err, KohlerError) and err.rejected:
                return True  # an answer, if not the one wanted: asking again won't help
            self._firmware_checked_at = checked_at
            self._firmware_due = self._firmware_due or due
            return not due
        return True

    async def _async_refresh_usage(self) -> None:
        """Refresh water usage now and then, and soon after the water stops.

        The daily series is what moves — it covers this month and the last, which is
        everything Today, This Week and This Month read. The monthly series only adds the
        months before that, which no longer change, so it is read at startup and with the
        first refresh of a new month, not on every refresh.
        """
        now = time.monotonic()
        today = dt_util.now().date()
        if not (
            self._usage_fetched_at is None
            or now - self._usage_fetched_at
            >= FAUCET_USAGE_REFRESH_INTERVAL.total_seconds()
            or (self._usage_due_at is not None and now >= self._usage_due_at)
            # A new day starts "today" over, without waiting out the interval.
            or today != self._usage_day
        ):
            return
        # A failure waits for the next interval rather than retrying each poll.
        self._usage_fetched_at = now
        self._usage_due_at = None
        self._usage_day = today
        month = today.strftime("%Y-%m")

        def read(interval: str, days_back: int) -> Coroutine[Any, Any, dict[str, Any]]:
            return self.client.async_get_usage(
                self.device_id,
                from_date=(today - timedelta(days=days_back)).isoformat(),
                to_date=today.isoformat(),
                interval=interval,
                product="faucet",
            )

        try:
            # Both at once; the monthly series only when it is due.
            *monthly, daily = await _side_by_side(
                *(
                    [read("MONTH", _USAGE_MONTHS_BACK)]
                    if month != self._usage_month
                    else []
                ),
                read("DAY", _USAGE_DAYS_BACK),
            )
        except AuthError as err:
            # A failed usage read answers `{}` rather than raising; only sign-in raises here.
            self._raise_if_dead(err)
            _LOGGER.debug("Could not refresh water usage: %s", err)
            return
        # `async_get_usage` answers `{}` for a failed read; keep what was held, and read a
        # failed monthly series again next time.
        if monthly and monthly[0]:
            self.usage = monthly[0]
            self._usage_month = month
        if daily:
            self.usage_daily = daily

    # --- dispenses ----------------------------------------------------------------

    def _note_water(self, running: bool | None, *, from_feed: bool, at: float) -> None:
        """Follow dispenses, usage and the safety limit from the water turning on and off.

        ``at`` is when the reading was taken (monotonic): a poll's when it was sent.
        """
        if running is None:
            return
        now = time.monotonic()
        if running:
            if self._dispense_started is not None:
                self._dispense_seen_on = True
                self._dispense_feed_on |= from_feed
        else:
            if self._water_was_running:
                # Kohler counts the water used shortly after it stops.
                self._usage_due_at = now + FAUCET_USAGE_SETTLE.total_seconds()
            # Polls can lag the stream, so only the stream ends an app preset.
            if from_feed:
                self._preset_since = None
                self._app_preset = None
            # Ended, once the water was seen running or had time to start; after the
            # stream saw it running, only the stream can say it stopped.
            if (
                self._dispense_started is not None
                and (
                    self._dispense_seen_on
                    or now - self._dispense_started >= DISPENSE_SETTLE.total_seconds()
                )
                and (from_feed or not self._dispense_feed_on)
            ):
                self._dispense_started = None
                self._dispense_preset = None
        self._water_was_running = running
        self._note_water_for_auto_off(running, from_feed=from_feed, at=at)

    def _start_dispense_timer(self, kind: str, seconds: float) -> None:
        """Re-render "Dispensing" once a dispense of ``kind`` reaches its limit."""
        if (cancel := self._dispense_timers.pop(kind, None)) is not None:
            cancel()
        self._dispense_timers[kind] = async_call_later(
            self.hass,
            seconds + _DISPENSE_TIMER_SLACK,
            partial(self._async_dispense_timeout, kind),
        )

    @callback
    def _async_dispense_timeout(self, kind: str, _now: datetime) -> None:
        self._dispense_timers.pop(kind, None)
        self.async_update_listeners()

    @callback
    def _note_event(self, event: FaucetEvent) -> None:
        now = time.monotonic()
        if event.preset_on is not None:
            self._preset_since = now if event.preset_on else None
            self._app_preset = event.preset if event.preset_on else None
            if event.preset_on:
                preset = self.presets.get(event.preset_id or "") or (
                    self.find_preset(event.preset) if event.preset else None
                )
                self._preset_limit = dispense_limit(preset.liters if preset else None)
                self._start_dispense_timer("preset", self._preset_limit)
        if event.status is not None:
            status = event.status.lower()
            if status in STATUS_ON or status in STATUS_OFF:
                self._note_water(status in STATUS_ON, from_feed=True, at=now)
        # Like the app, apply the stream's water and handle state at once; the re-read that
        # follows confirms it. Each is timed, so an older poll cannot put the previous
        # state back (`_stream_values_since`), and recorded, so an enum state sensor
        # accepts a value only the stream has reported.
        reported = {
            field: value
            for field, value in (
                ("status", event.status),
                ("handleState", event.handle),
            )
            if value
        }
        for field, value in reported.items():
            self._stream_values[field] = (now, value)
            self._record_value(field, value)
        if self.data is not None and reported:
            self.data = {**self.data, **reported}
            if "status" in reported:
                self._status_at, self._status_from_feed = now, True
        if event.leak:
            self._leak_alert_at = time.time()
            self._async_save_soon("leak alert")
        if event.firmware is not None:
            # An install ended: read the new version and check again.
            self._firmware_due = self._config_due = True
        self.async_update_listeners()

    # --- repairs ------------------------------------------------------------------

    def _issue_id(self, issue: str) -> str:
        return f"{issue}_{device_issue_key(self.device_id)}"

    async def _async_note_rejection(self, err: KohlerError) -> None:
        """Raise a repair issue when Kohler keeps refusing to answer."""
        if not err.rejected:
            return  # outages and throttling fix themselves
        self._rejections += 1
        if self._rejections != REJECTIONS_BEFORE_ISSUE:
            return
        issue = ISSUE_FAUCET_API_CHANGED
        if err.status == 404:
            # Tell "faucet removed from the account" apart from "API moved".
            try:
                customer = await self.client.async_get_customer()
            except (AuthError, KohlerError):
                customer = None
            if customer is not None and all(
                f.device_id != self.device_id for f in customer.faucet_devices
            ):
                issue = ISSUE_FAUCET_NOT_FOUND
        _LOGGER.debug("Raising repair issue %s after: %s", issue, err)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(issue),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=issue,
            translation_placeholders={"name": self.device_name, "error": str(err)},
        )

    def _clear_issues(self) -> None:
        if not self._rejections and not self._check_issues:
            return
        self._rejections = 0
        self._check_issues = False
        for issue in (ISSUE_FAUCET_API_CHANGED, ISSUE_FAUCET_NOT_FOUND):
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(issue))

    # --- commands -----------------------------------------------------------------

    async def async_dispense(self, liters: float, preset: str | None = None) -> None:
        """Dispense ``liters`` (for ``preset``, if any) and refresh the state."""
        await self._async_command(
            lambda: self.device.async_dispense(liters), starts_water=True
        )
        self.last_dispense_liters = liters
        self._dispense_started = time.monotonic()
        self._dispense_limit = dispense_limit(liters)
        self._dispense_seen_on = self._dispense_feed_on = False
        self._dispense_preset = preset
        self._start_dispense_timer("dispense", self._dispense_limit)
        self.async_update_listeners()
        await self.async_request_refresh()

    async def async_set_water(self, on: bool) -> None:
        """Turn the water on or off and refresh the state."""
        if on and self.max_run_minutes > 0:
            await self._async_water_on_with_limit()
        else:
            sent_at = time.monotonic()
            await self._async_command(
                lambda: self.device.async_set_water(on), starts_water=on
            )
            if on and self._auto_off_at is not None:
                # The limit is off (0 minutes) and this run is not to be limited: one left
                # from an earlier run — retrying, or restored — must not cut it off.
                await self._async_cancel_auto_off()
            if not on and self._auto_off_at is not None:
                # ⚠️ Only a reading of the water off ends the limit, never Kohler accepting
                # an OFF, which the faucet may ignore. `_note_water_for_auto_off` ends it
                # at the first "off" read after this OFF; poll quickly so that comes soon.
                self._auto_off_sent_at = sent_at
                self._fast_poll_until = (
                    time.monotonic() + FAUCET_COMMAND_FOLLOW_UP.total_seconds()
                )
        await self.async_request_refresh()

    async def _async_water_on_with_limit(self) -> None:
        """Turn the water on, under the safety limit.

        The limit is set and saved before the command is sent, so a reload while it is in
        flight cannot lose it. A limit already set stays as it is until this command is
        through, then starts again.

        If the command fails, its limit is dropped only if Kohler certainly did not carry
        it out (`_not_carried_out`); otherwise it stays until the water is known off.
        """
        self._check_command(starts_water=True)
        armed = None
        if self._auto_off_at is None:
            self._arm_auto_off()
            armed = self._auto_off_at
            await self.async_save()
        try:
            await self._async_command(
                lambda: self.device.async_set_water(True), starts_water=True
            )
        except HomeAssistantError as err:
            if (
                armed is not None
                and self._auto_off_at == armed
                and not self._shut_down
                and _not_carried_out(err)
            ):
                await self._async_cancel_auto_off()
            raise
        if self._shut_down:
            return  # the next setup has the limit, from the store
        if armed is None or self._auto_off_at != armed:
            self._arm_auto_off()
            await self.async_save()
        else:
            # The cloud's lag is counted from the command, not from the save before it.
            self._auto_off_armed_at = time.monotonic()

    def _refusal(self, key: str) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={"name": self.device_name},
        )

    def _check_command(self, *, starts_water: bool) -> None:
        """Refuse a command that cannot or must not be sent."""
        if self._shut_down:
            # Unloaded while the command waited its turn: the next setup is the live copy.
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": "the integration was reloaded"},
            )
        if not self.faucet_online:
            raise self._refusal("faucet_offline")
        # The Konnect app refuses these too. Turning water off is never refused.
        if starts_water and self.handle_closed:
            raise self._refusal("handle_closed")
        if starts_water and self.firmware_downloading:
            raise self._refusal("firmware_updating")

    async def _async_command(
        self, send: Callable[[], Awaitable[Any]], *, starts_water: bool
    ) -> None:
        self._check_command(starts_water=starts_water)
        try:
            await send()
        except AuthError as err:
            if credential_is_dead(err):
                self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="auth_failed"
                ) from err
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except KohlerError as err:
            if err.retry_after is not None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="rate_limited",
                    translation_placeholders={"seconds": str(round(err.retry_after))},
                ) from err
            if (key := _REFUSALS.get(err.code or "")) is not None:
                raise self._refusal(key) from err
            if err.status == 403:
                # Commands are signed with the same token the Konnect app holds, which
                # every product accepts; a 403 means Kohler changed that.
                raise self._refusal("command_forbidden") from err
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        # The cloud can lag the command; watch closely until it catches up.
        self._fast_poll_until = (
            time.monotonic() + FAUCET_COMMAND_FOLLOW_UP.total_seconds()
        )

    # --- safety auto-off ------------------------------------------------------------
    #
    # ⚠️ Two rules. Once reached, the limit never stops trying to turn the water off while
    # it is not known to be off, however long Kohler is unreachable and across restarts and
    # reloads. And it never cuts off water someone turned on by hand after Home Assistant's
    # run ended: it is dropped as soon as the water is known to be off.

    def _schedule_auto_off(self, delay: float) -> None:
        if self._auto_off_timer is not None:
            self._auto_off_timer()
        self._auto_off_timer = async_call_later(self.hass, delay, self._async_auto_off)

    def _arm_auto_off(self) -> None:
        """Set a new safety limit, for water Home Assistant turns on now."""
        now = time.time()
        self._auto_off_at = now + self.max_run_minutes * 60
        self._auto_off_set_at = now
        self._auto_off_armed_at = time.monotonic()
        self._seen_running = self._feed_saw_running = False
        self._reset_auto_off_tries()
        self._schedule_auto_off(self.max_run_minutes * 60)

    @callback
    def _reset_auto_off_tries(self) -> None:
        self._off_readings = self._auto_off_tries = self._auto_off_failures = 0
        self._auto_off_sent_at = None
        self._auto_off_unconfirmed = False
        # Whatever it said about the water is over with the limit it was about.
        ir.async_delete_issue(
            self.hass, DOMAIN, self._issue_id(ISSUE_FAUCET_AUTO_OFF_FAILED)
        )

    @callback
    def _clear_auto_off(self) -> bool:
        """Drop the safety limit; True if there was one, and so something to save."""
        if self._auto_off_timer is not None:
            self._auto_off_timer()
            self._auto_off_timer = None
        self._reset_auto_off_tries()
        if self._auto_off_at is None:
            return False
        self._auto_off_at = self._auto_off_set_at = None
        return True

    async def _async_cancel_auto_off(self) -> None:
        if self._clear_auto_off():
            await self.async_save()

    @callback
    def _note_water_for_auto_off(
        self, running: bool, *, from_feed: bool, at: float
    ) -> None:
        """Follow the water for the safety limit, from polls and the stream alike.

        The limit is dropped once its water is known to be off: it ran and stopped, or an
        OFF went out before the reading, or it was never seen running and two readings
        well after the command say "off".
        """
        if self._auto_off_at is None:
            return
        if running:
            save = not self._seen_running
            self._seen_running = True
            self._feed_saw_running |= from_feed
            if save:
                self._async_save_soon("safety limit")
            return
        if self._may_lag_stream(at, from_feed=from_feed):
            return
        if (
            not from_feed
            and self._seen_running
            and not self._polls_caught_up(at)
            and not (
                self._auto_off_sent_at is not None and at >= self._auto_off_sent_at
            )
        ):
            # Until the polls have caught up with the ON (`_polls_caught_up`), a poll may
            # show the state from before it: "off", or a run that ended just before it.
            # So only the stream may say this water stopped, trusted or not (unlike
            # `_may_lag_stream`). An "off" read after an OFF sent from here does count, as
            # that OFF's doing. Poll quickly meanwhile, so the first reading that counts
            # comes within seconds.
            self._fast_poll_until = max(
                self._fast_poll_until, self._auto_off_armed_at + _NEVER_RAN_AFTER + 1
            )
            return
        if not self._seen_running and not (
            self._auto_off_sent_at is not None and at >= self._auto_off_sent_at
        ):
            # Never seen running, and no OFF sent before this read: a reading soon after
            # the command can say "off" while the cloud catches up, so it takes two later
            # ones, from polls or the stream.
            if at - self._auto_off_armed_at < _NEVER_RAN_AFTER:
                return
            self._off_readings += 1
            if self._off_readings < 2:
                return
        # Known off: Home Assistant's run is over.
        self._clear_auto_off()
        self._async_save_soon("safety limit")

    def _may_lag_stream(self, at: float, *, from_feed: bool) -> bool:
        """Whether an "off" read at ``at`` may be a poll that has not caught up yet.

        A poll can lag the stream: after the stream saw the water running, only it can say
        the water stopped — unless polling is back in charge (`push_trusted`), or the cloud
        has had time to catch up with an OFF sent before the read.
        """
        return (
            not from_feed
            and self._feed_saw_running
            and self.push_trusted
            and not (
                self._auto_off_sent_at is not None
                and at - self._auto_off_sent_at
                >= FAUCET_COMMAND_FOLLOW_UP.total_seconds()
            )
        )

    def _polls_caught_up(self, at: float) -> bool:
        """Whether a poll sent at ``at`` is late enough to show Home Assistant's ON.

        That is `_NEVER_RAN_AFTER` after the water was turned on, for a restored limit too.
        An earlier poll may show the state from before the ON, "running" included.
        """
        return at - self._auto_off_armed_at >= _NEVER_RAN_AFTER

    def _known_off(self) -> bool:
        """The water is off by what was learned since the safety limit was set.

        Only a reading taken once the cloud has had time to catch up with the command
        counts (for a poll, `_polls_caught_up`), and not a poll that may lag the stream.
        Having learned nothing (failed polls, a silent stream) does not count as off.
        """
        at = self._status_at
        if self.water_running is not False or at is None:
            return False
        if self._status_from_feed:
            caught_up = (
                at - self._auto_off_armed_at >= FAUCET_COMMAND_FOLLOW_UP.total_seconds()
            )
        else:
            caught_up = self._polls_caught_up(at)
        return caught_up and not self._may_lag_stream(
            at, from_feed=self._status_from_feed
        )

    def _auto_off_delay(self, err: Exception | None) -> float:
        """Seconds until the next try: longer each time, and no sooner than Kohler asks."""
        delay = AUTO_OFF_BACKOFF[min(self._auto_off_tries, len(AUTO_OFF_BACKOFF)) - 1]
        if isinstance(err, KohlerError) and err.retry_after is not None:
            delay = max(delay, min(err.retry_after, RETRY_AFTER_MAX.total_seconds()))
        return delay

    @callback
    def _auto_off_trouble(self, error: str) -> Callable[..., None]:
        """Count a try that has not turned the water off, and say how to log it.

        Only the first is a warning; the `AUTO_OFF_FAILURES_BEFORE_ISSUE`th in a row also
        raises a repair issue, which is cleared with the limit.
        """
        self._auto_off_failures += 1
        if self._auto_off_failures == AUTO_OFF_FAILURES_BEFORE_ISSUE:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id(ISSUE_FAUCET_AUTO_OFF_FAILED),
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_FAUCET_AUTO_OFF_FAILED,
                translation_placeholders={"name": self.device_name, "error": error},
            )
        return _LOGGER.warning if self._auto_off_failures == 1 else _LOGGER.debug

    async def _async_auto_off(self, _now: datetime) -> None:
        """Turn off water Home Assistant turned on and nobody turned off, until it is off.

        An OFF that fails, or that Kohler takes when nothing after it shows the water off,
        is sent again after each of `AUTO_OFF_BACKOFF` in turn, for as long as it takes. The
        deadline stays saved, so a restart or reload carries on.
        """
        self._auto_off_timer = None
        deadline = self._auto_off_at
        if self._shut_down or deadline is None:
            return
        if self._known_off():
            # Never seen running, or not since a restart, or not since the last try, and
            # off now: there is nothing to turn off, and an OFF could only cut off whoever
            # turns the water on next.
            _LOGGER.debug(
                "Water on %s is already off; the safety limit is not needed",
                self.device_name,
            )
            await self._async_cancel_auto_off()
            return
        if self._auto_off_unconfirmed:
            self._auto_off_unconfirmed = False
            self._auto_off_trouble("the faucet has not reported its water off")(
                "Kohler took the command to turn off the water on %s, but nothing has "
                "shown it off since; sending it again, until the water is known to be off",
                self.device_name,
            )
        if not self._auto_off_tries:
            _LOGGER.warning(
                "Turning off the water on %s: it was turned on from Home Assistant "
                "%s minutes ago, the limit set in the integration options",
                self.device_name,
                self.max_run_minutes,
            )
        self._auto_off_tries += 1
        # Before it is sent, so an "off" the stream reports meanwhile ends the limit.
        self._auto_off_sent_at = time.monotonic()
        try:
            await self.device.async_set_water(False)
        except (AuthError, KohlerError) as err:
            # Unloaded (the next setup carries on from the store), or the limit was dropped
            # or set again meanwhile: a retry could cut off water turned on since.
            if self._shut_down or self._auto_off_at != deadline:
                return
            delay = self._auto_off_delay(err)
            self._auto_off_trouble(str(err))(
                "Could not turn off the water on %s: %s. Trying again in %g min, and "
                "until the water is known to be off",
                self.device_name,
                err,
                delay / 60,
            )
            self._schedule_auto_off(delay)
            return
        if self._shut_down or self._auto_off_at != deadline:
            # A limit set again while this OFF was in flight is not this one's to end.
            return
        # Kohler took it. Only a reading that says "off" confirms it
        # (`_note_water_for_auto_off`); until then the next try waits its turn.
        self._auto_off_unconfirmed = True
        self._schedule_auto_off(self._auto_off_delay(None))
        self._fast_poll_until = (
            time.monotonic() + FAUCET_COMMAND_FOLLOW_UP.total_seconds()
        )
        await self.async_request_refresh()

    # --- leaks ----------------------------------------------------------------------

    async def async_clear_leaks(self) -> None:
        """Mark the leak alert and every leak event Kohler reports as cleared."""
        active = self.active_leaks
        if not active and self._leak_alert_at is None:
            return
        new = sorted({leak_key(e) for e in active})
        self._cleared_leaks = (self._cleared_leaks + new)[-MAX_CLEARED_LEAKS:]
        # Also covers an alert's event if it reaches the history only later.
        self._leaks_cleared_through = max(
            time.time(),
            *(t for e in active if (t := leak_time(e)) is not None),
            self._leaks_cleared_through or 0.0,
        )
        self._leak_alert_at = None
        await self.async_save()
        self.async_update_listeners()

    # --- instant updates ------------------------------------------------------------

    @callback
    def async_stream_connected(self, *, just_read: bool) -> None:
        """The stream connected: read the faucet again, unless its first read stands in."""
        if just_read:
            self.async_note_stream_up()
        else:
            self.async_push_activity(False)

    @callback
    def async_stream_disconnected(self) -> None:
        """The stream dropped: catch up now, then poll at the pace for no stream."""
        self.async_push_activity(False)

    @callback
    def async_handle_envelope(self, envelope: Envelope) -> None:
        """A stream message about this faucet: read it again, knowing what happened."""
        self.async_push_activity(True, parse_event(envelope))

    @callback
    def async_note_stream_up(self) -> None:
        """The stream's first connect, which this faucet's first read stands in for.

        That read went out moments ago, so the faucet is not read again
        (`KohlerKonnectCoordinator._handle_connected`). The stream is answerable only from
        now: a change between that read and the connect was never announced and must not
        count as missed.
        """
        self._push_synced_at = time.monotonic()

    @callback
    def async_push_activity(
        self, about_faucet: bool, event: FaucetEvent | None = None
    ) -> None:
        """The stream saw a change, connected or dropped: re-read the faucet."""
        # In every case, the refresh below catches up on everything until now.
        self._push_synced_at = time.monotonic()
        if about_faucet:
            # Proof the stream delivers for this faucet; polling can relax. Saved the first
            # time, so the next setup need not wait for the faucet to be used again.
            if not self.push_verified:
                self.push_verified = True
                self._async_save_soon("instant updates")
            self.push_restored = False
            self.push_messages += 1
            self.last_report = time.time()
            if _may_change_config(event):
                self._config_due = True  # a leak, an install, a new preset...
        else:
            # Connected or dropped: the water may have stopped unreported in the gap, so
            # polls may say so again until the stream next reports it running
            # (`_may_lag_stream`).
            self._feed_saw_running = False
            # The MQTT connection entity shows it now; the refresh can wait on its cooldown.
            self.async_update_listeners()
        if event is not None:
            self._note_event(event)
        self.config_entry.async_create_task(
            self.hass, self.async_request_refresh(), f"{DOMAIN} faucet push refresh"
        )

    def _check_push_announced(self, state: dict[str, Any]) -> None:
        """Start the self-check when a poll finds a change the stream has not announced."""
        if (
            not self.push_trusted
            or self.data is None
            or self._polled_at is None
            or self._push_check is not None
            or _push_signature(state) == _push_signature(self.data)
        ):
            return
        since = self._polled_at  # the change happened after the previous poll
        if self._push_synced_at is not None and self._push_synced_at > since:
            return
        # Its announcement may still be on the way, as after a command.
        self._push_check = async_call_later(
            self.hass, FAUCET_PUSH_GRACE, partial(self._async_push_check, since)
        )

    @callback
    def _async_push_check(self, since: float, _now: datetime) -> None:
        self._push_check = None
        if self._push_synced_at is not None and self._push_synced_at > since:
            return
        self.push_missed += 1
        if not self.push_verified:
            return
        _LOGGER.debug(
            "Instant updates missed a change on %s; polling as usual until they "
            "deliver again",
            self.device_name,
        )
        self.push_verified = self.push_restored = False
        self._async_save_soon("instant updates")
        # Apply the faster polling now, not after the current long interval.
        self.config_entry.async_create_task(
            self.hass, self.async_request_refresh(), f"{DOMAIN} faucet push check"
        )

    # --- what entities read ---------------------------------------------------------

    @property
    def faucet_online(self) -> bool:
        """False when Kohler reports the faucet is not connected to the cloud.

        A missing `connectionState` counts as online, unlike in the app
        (`docs/protocol/sensate_faucet.md` §2.2), so a reply that merely leaves it out does
        not refuse every command.
        """
        return self.connection_state is None or (
            self.connection_state.lower() == CONNECTION_CONNECTED.lower()
        )

    @property
    def config_loaded(self) -> bool:
        return self._config_fetched_at is not None

    @property
    def leak_history(self) -> list[Any]:
        history = self.config.get("leakDetectionHistory")
        return history if isinstance(history, list) else []

    def _leak_cleared(self, event: Any, cleared: set[str]) -> bool:
        if leak_key(event) in cleared:
            return True
        detected = leak_time(event)
        return (
            detected is not None
            and self._leaks_cleared_through is not None
            and detected <= self._leaks_cleared_through
        )

    @property
    def active_leaks(self) -> list[Any]:
        """Leak events that have not been cleared in Home Assistant."""
        cleared = set(self._cleared_leaks)
        return [e for e in self.leak_history if not self._leak_cleared(e, cleared)]

    @property
    def leak_alert(self) -> bool:
        """The stream reported a leak that has not been cleared."""
        return self._leak_alert_at is not None

    @property
    def last_leak_at(self) -> datetime | None:
        """When the most recent leak was detected, by the history or the stream."""
        times = [t for e in self.leak_history if (t := leak_time(e)) is not None]
        if self._leak_alert_at is not None:
            times.append(self._leak_alert_at)
        return dt_util.utc_from_timestamp(max(times)) if times else None

    @property
    def handle_closed(self) -> bool:
        """The manual handle is closed, which blocks remote water."""
        handle = (self.data or {}).get("handleState")
        return isinstance(handle, str) and handle.lower() == HANDLE_CLOSED

    @property
    def firmware_downloading(self) -> bool:
        """The faucet is downloading firmware, which blocks remote water."""
        progress = (self.data or {}).get("progress")
        return isinstance(progress, str) and progress.lower() == PROGRESS_DOWNLOADING

    @property
    def auto_off_at(self) -> float | None:
        """When the water safety limit turns the water off, in epoch seconds; or None."""
        return self._auto_off_at

    @property
    def about(self) -> dict[str, Any]:
        about = (self.config.get("configuration") or {}).get("about")
        return about if isinstance(about, dict) else {}

    @property
    def water_running(self) -> bool | None:
        return water_running(self.data or {})

    def is_dispensing(self) -> bool:
        now = time.monotonic()
        return (
            self._dispense_started is not None
            and now - self._dispense_started < self._dispense_limit
        ) or (
            self._preset_since is not None
            and now - self._preset_since < self._preset_limit
        )

    @property
    def preset_options(self) -> dict[str, FaucetPreset]:
        """Presets by the names the Preset select and the action use."""
        return preset_labels(self.presets.values())

    def find_preset(self, name: str) -> FaucetPreset | None:
        """The preset called ``name``, ignoring case if nothing matches exactly."""
        options = self.preset_options
        if (preset := options.get(name)) is not None:
            return preset
        wanted = name.casefold()
        return next(
            (p for label, p in options.items() if label.casefold() == wanted), None
        )

    @property
    def chosen_preset(self) -> FaucetPreset | None:
        """The preset "Dispense preset" pours: the one chosen, else the first.

        A chosen preset that is deleted in the app falls back to the first, and comes back
        if it reappears.
        """
        if (preset := self.presets.get(self.preset_choice or "")) is not None:
            return preset
        return next(iter(self.presets.values()), None)

    @property
    def dispensing_preset(self) -> str | None:
        """The preset being dispensed, from the app or Home Assistant."""
        if not self.is_dispensing():
            return None
        return self._app_preset or self._dispense_preset
