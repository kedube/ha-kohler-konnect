"""One faucet's coordinator: polled state, dispenses, leaks, usage and the safety limit.

Unlike the showers, which are push-only, a faucet polls. That is the cadence the separate
``kohler_sensate`` integration settled on against a real faucet: the MQTT stream is a
speed-up, proven per faucet before polling relaxes, and a poll that finds a change the stream
never announced puts polling back in charge (`_check_push_announced`).

Everything else is shared with the showers and owned by the account coordinator — the
sign-in, the REST client and the one MQTT connection. It hands this faucet its messages and
tells it when the connection comes and goes (`async_push_activity`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable, Iterable
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
from ..konnect import AuthError, Device, KohlerError, credential_is_dead
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
    parse_firmware,
    water_running,
)
from .units import UnitProfile, profile_for, to_ml

if TYPE_CHECKING:
    from ..coordinator import KohlerKonnectCoordinator

_LOGGER = logging.getLogger(__name__)

# Leak events are {"leakDetectionTime": <epoch seconds>}, and are keyed by that time. One
# without it — none has been seen — is keyed by one of these ids, else a hash of the event.
_LEAK_ID_KEYS = ("id", "eventId", "leakId")
_LEAK_TIME = "leakDetectionTime"

# Fields whose values are recorded for diagnostics.
TRACKED_FIELDS = ("status", "progress", "handleState")
AUTO_OFF_RETRY = 60
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
        # Sent with every command. The app takes it from the account's device list, never
        # from the faucet's own state, and so does this.
        sku = device.sku.upper()
        self.sku: str = sku if sku in FAUCET_SKUS else SKU_SENSATE
        self.device = FaucetDevice(self.client, self.device_id, self.sku)
        self.profile: UnitProfile = profile_for(account.water_units)
        self.max_run_minutes: int = int(
            entry.options.get(CONF_MAX_RUN_MINUTES, DEFAULT_MAX_RUN_MINUTES)
        )
        self.config: dict[str, Any] = {}
        self._config_fetched_at: float | None = None
        self._config_due = False
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
        # Ends a dispense whose end never arrives, at its dispense_limit().
        self._dispense_timer: CALLBACK_TYPE | None = None
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
        self.push_verified = False
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
        # Safety auto-off: wall-clock deadline (persisted) and its timer.
        self._auto_off_at: float | None = None
        self._auto_off_timer: CALLBACK_TYPE | None = None
        self._seen_running = False

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
        # A reload or restart must not lose the safety limit.
        if isinstance(deadline := stored.get("auto_off_at"), (int, float)):
            self._auto_off_at = float(deadline)
            self._schedule_auto_off(max(0.0, deadline - time.time()))

    def stored(self) -> dict[str, Any]:
        return {
            "cleared_leaks": self._cleared_leaks,
            "leaks_cleared_through": self._leaks_cleared_through,
            "leak_alert_at": self._leak_alert_at,
            "auto_off_at": self._auto_off_at,
        }

    async def async_save(self) -> None:
        """Persist this faucet's state through the account's store."""
        await self.account.async_save_faucet(self)

    async def async_shutdown(self) -> None:
        # The persisted deadline is picked up again by the next setup.
        for cancel in (self._auto_off_timer, self._push_check, self._dispense_timer):
            if cancel is not None:
                cancel()
        self._auto_off_timer = self._push_check = self._dispense_timer = None
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
        self._check_push_announced(state)
        self._polled_at = polled_at
        self.connection_state = snapshot.connection_state
        self.last_connected = snapshot.last_connected
        self._record_values(state)
        self._note_water(water_running(state), from_feed=False)
        await self._async_refresh_config()
        await self._async_refresh_usage()

        quantity = state.get("quantity")
        if isinstance(quantity, (int, float)) and quantity > 0:
            self.last_dispense_liters = float(quantity)

        running = water_running(state)
        if running:
            self._seen_running = True
        elif running is False and self._seen_running and self._auto_off_at:
            # Seen running, now off: no need for the safety limit any more.
            await self._async_cancel_auto_off()
        if running or time.monotonic() < self._fast_poll_until:
            # A trusted stream reports the change the moment it happens.
            self.update_interval = (
                FAUCET_SCAN_INTERVAL_PUSH_ACTIVE
                if self.push_trusted
                else FAUCET_SCAN_INTERVAL_ACTIVE
            )
        return state

    def _record_values(self, state: dict[str, Any]) -> None:
        values = {field: state.get(field) for field in TRACKED_FIELDS}
        values["connectionState"] = self.connection_state
        for field, value in values.items():
            if isinstance(value, str) and value not in self.seen_values[field]:
                self.seen_values[field].add(value)
                _LOGGER.debug("New faucet %s value: %s", field, value)

    async def _async_refresh_config(self) -> None:
        """Refresh firmware, leak history and presets; failures keep the old copy."""
        now = time.monotonic()
        if (
            not self._config_due
            and self._config_fetched_at is not None
            and now - self._config_fetched_at
            < FAUCET_CONFIG_REFRESH_INTERVAL.total_seconds()
        ):
            return
        try:
            self.config = await self.client.async_get_faucet_configuration(
                self.device_id
            )
        except (AuthError, KohlerError) as err:
            self._raise_if_dead(err)
            _LOGGER.debug("Could not refresh faucet configuration: %s", err)
            # Try again next poll only if it has never succeeded.
            if self._config_fetched_at is None:
                return
        try:
            presets, self.preset_source = await self.device.async_get_presets()
        except (AuthError, KohlerError) as err:
            self._raise_if_dead(err)
            _LOGGER.debug("Could not refresh Konnect presets: %s", err)
        else:
            self.presets = {p.preset_id: p for p in presets}
        await self._async_check_firmware(now)
        self._config_fetched_at = now
        self._config_due = False

    async def _async_check_firmware(self, now: float) -> None:
        """Ask Kohler about newer firmware now and then; failures keep the old answer."""
        if (
            not self._firmware_due
            and self._firmware_checked_at is not None
            and now - self._firmware_checked_at
            < FIRMWARE_CHECK_INTERVAL.total_seconds()
        ):
            return
        self._firmware_checked_at = now
        self._firmware_due = False
        try:
            self.firmware = parse_firmware(
                await self.client.async_get_faucet_firmware(self.device_id)
            )
        except (AuthError, KohlerError) as err:
            self._raise_if_dead(err)
            _LOGGER.debug("Could not check for faucet firmware: %s", err)

    async def _async_refresh_usage(self) -> None:
        """Refresh water usage now and then, and soon after the water stops.

        The daily series is what moves — it covers this month and the last, which is
        everything Today, This Week and This Month read. The monthly series only adds the
        months before that, which no longer change, so it is read as the valves read it: at
        startup and once the month turns, not on every refresh.
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
        try:
            if month != self._usage_month:
                monthly = await self.client.async_get_usage(
                    self.device_id,
                    from_date=(today - timedelta(days=_USAGE_MONTHS_BACK)).isoformat(),
                    to_date=today.isoformat(),
                    interval="MONTH",
                    faucet=True,
                )
                # Kept, and read again next time, after a failed read (`{}`).
                if monthly:
                    self.usage = monthly
                    self._usage_month = month
            daily = await self.client.async_get_usage(
                self.device_id,
                from_date=(today - timedelta(days=_USAGE_DAYS_BACK)).isoformat(),
                to_date=today.isoformat(),
                interval="DAY",
                faucet=True,
            )
        except AuthError as err:
            # A failed usage read answers `{}` rather than raising; only sign-in raises here.
            self._raise_if_dead(err)
            _LOGGER.debug("Could not refresh water usage: %s", err)
            return
        # `async_get_usage` answers `{}` for a failed read; keep what was held.
        if daily:
            self.usage_daily = daily

    # --- dispenses ----------------------------------------------------------------

    def _note_water(self, running: bool | None, *, from_feed: bool) -> None:
        """Follow dispenses and usage from the water turning on and off."""
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

    def _start_dispense_timer(self, seconds: float) -> None:
        if self._dispense_timer is not None:
            self._dispense_timer()
        self._dispense_timer = async_call_later(
            self.hass, seconds + _DISPENSE_TIMER_SLACK, self._async_dispense_timeout
        )

    @callback
    def _async_dispense_timeout(self, _now: datetime) -> None:
        self._dispense_timer = None
        self.async_update_listeners()

    @callback
    def _note_event(self, event: FaucetEvent) -> None:
        if event.preset_on is not None:
            self._preset_since = time.monotonic() if event.preset_on else None
            self._app_preset = event.preset if event.preset_on else None
            if event.preset_on:
                preset = self.presets.get(event.preset_id or "") or (
                    self.find_preset(event.preset) if event.preset else None
                )
                self._preset_limit = dispense_limit(preset.liters if preset else None)
                self._start_dispense_timer(self._preset_limit)
        if event.status is not None:
            status = event.status.lower()
            if status in STATUS_ON or status in STATUS_OFF:
                self._note_water(status in STATUS_ON, from_feed=True)
        # Like the app, take the stream's water and handle state at once; the re-read that
        # follows confirms it.
        if self.data is not None and (event.status or event.handle):
            self.data = {
                **self.data,
                **({"status": event.status} if event.status else {}),
                **({"handleState": event.handle} if event.handle else {}),
            }
        if event.leak:
            self._leak_alert_at = time.time()
            self.config_entry.async_create_task(
                self.hass, self.async_save(), f"{DOMAIN} leak alert"
            )
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
        self._start_dispense_timer(self._dispense_limit)
        self.async_update_listeners()
        await self.async_request_refresh()

    async def async_set_water(self, on: bool) -> None:
        """Turn the water on or off and refresh the state."""
        await self._async_command(
            lambda: self.device.async_set_water(on), starts_water=on
        )
        if on and self.max_run_minutes > 0:
            self._seen_running = False
            self._auto_off_at = time.time() + self.max_run_minutes * 60
            self._schedule_auto_off(self.max_run_minutes * 60)
            await self.async_save()
        elif not on:
            await self._async_cancel_auto_off()
        await self.async_request_refresh()

    def _refusal(self, key: str) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={"name": self.device_name},
        )

    async def _async_command(
        self, send: Callable[[], Awaitable[Any]], *, starts_water: bool
    ) -> None:
        if not self.faucet_online:
            raise self._refusal("faucet_offline")
        # The Konnect app refuses these too. Turning water off is never refused.
        if starts_water and self.handle_closed:
            raise self._refusal("handle_closed")
        if starts_water and self.firmware_downloading:
            raise self._refusal("firmware_updating")
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

    def _schedule_auto_off(self, delay: float) -> None:
        if self._auto_off_timer is not None:
            self._auto_off_timer()
        self._auto_off_timer = async_call_later(self.hass, delay, self._async_auto_off)

    async def _async_cancel_auto_off(self) -> None:
        if self._auto_off_timer is not None:
            self._auto_off_timer()
            self._auto_off_timer = None
        if self._auto_off_at is not None:
            self._auto_off_at = None
            await self.async_save()

    async def _async_auto_off(self, _now: datetime) -> None:
        """Turn off water Home Assistant turned on and nobody turned off."""
        self._auto_off_timer = None
        _LOGGER.warning(
            "Turning off the water on %s: it was turned on from Home Assistant "
            "%s minutes ago, the limit set in the integration options",
            self.device_name,
            self.max_run_minutes,
        )
        try:
            await self.device.async_set_water(False)
        except (AuthError, KohlerError) as err:
            _LOGGER.warning(
                "Could not turn off the water on %s: %s. Trying again in a minute",
                self.device_name,
                err,
            )
            self._schedule_auto_off(AUTO_OFF_RETRY)
            return
        await self._async_cancel_auto_off()
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
    def async_push_activity(
        self, about_faucet: bool, event: FaucetEvent | None = None
    ) -> None:
        """The stream saw a change, connected or dropped: re-read the faucet."""
        # In every case, the refresh below catches up on everything until now.
        self._push_synced_at = time.monotonic()
        if about_faucet:
            # Proof the stream delivers for this faucet; polling can relax.
            self.push_verified = True
            self.push_messages += 1
            self._config_due = True  # it may be a leak alert
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
        self.push_verified = False
        # Apply the faster polling now, not after the current long interval.
        self.config_entry.async_create_task(
            self.hass, self.async_request_refresh(), f"{DOMAIN} faucet push check"
        )

    # --- what entities read ---------------------------------------------------------

    @property
    def faucet_online(self) -> bool:
        """False when Kohler reports the faucet is not connected to the cloud.

        A missing `connectionState` counts as online, unlike in the app: it has always been
        present, and treating its absence as offline would refuse every command on a reply
        that merely left it out.
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
