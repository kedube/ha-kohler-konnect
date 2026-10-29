"""The sidebar panel's websocket API.

Registered once per Home Assistant run, from `async_setup`, while entries load, reload and
unload underneath it; so every handler looks its coordinator up when called, and an entry
that is not loaded simply contributes no devices.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval

from ..const import CONF_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL, DOMAIN
from .account import account_options, async_update_account_options
from .details import device_details
from .history import PERIODS, async_usage_history
from .live import DeviceRef, device_live, find_device
from .snapshot import describe_devices

__all__ = ["async_register_commands", "devices_message"]

_LOGGER = logging.getLogger(__name__)

# Some of a descriptor changes with no registry event: a preset made in the Konnect app,
# the push stream dropping. It is rebuilt this often, and sent only if it changed.
REFRESH_INTERVAL = timedelta(seconds=30)
# Registry events come in bursts (a reload re-registers every entity); one message each.
DEBOUNCE_SECONDS = 0.5


def devices_message(hass: HomeAssistant, *, admin: bool) -> dict[str, Any]:
    """Every enabled account, and the devices of those that show the panel."""
    coordinators = hass.data.get(DOMAIN, {})
    entries: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.disabled_by is not None:
            continue
        shown = bool(entry.options.get(CONF_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL))
        coordinator = coordinators.get(entry.entry_id)
        stream = None if coordinator is None else coordinator.stream
        entries.append(
            {
                "entry_id": entry.entry_id,
                # The title carries the account's email.
                "title": entry.title if admin else None,
                "sidebar_panel": shown,
                "stream_connected": None if stream is None else bool(stream.connected),
            }
        )
        if coordinator is not None and shown:
            devices.extend(describe_devices(hass, entry, coordinator, admin=admin))
    return {"entries": entries, "devices": devices}


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe_devices"})
@callback
def ws_subscribe_devices(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send the accounts and devices now, then again whenever they change."""
    admin = connection.user.is_admin
    last: dict[str, Any] = {}

    @callback
    def send() -> None:
        message = devices_message(hass, admin=admin)
        if message != last.get("message"):
            last["message"] = message
            connection.send_message(websocket_api.event_message(msg["id"], message))

    debouncer = Debouncer(
        hass, _LOGGER, cooldown=DEBOUNCE_SECONDS, immediate=False, function=send
    )

    @callback
    def changed(*_: Any) -> None:
        debouncer.async_schedule_call()

    unsubscribers = [
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, changed),
        hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, changed),
        hass.bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, changed),
        async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, changed),
        async_track_time_interval(hass, changed, REFRESH_INTERVAL),
    ]

    @callback
    def unsubscribe() -> None:
        for unsubscriber in unsubscribers:
            unsubscriber()
        debouncer.async_cancel()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    send()


class _LiveFeed:
    """One device screen's live extras, re-sent when they change.

    Listens to whatever serves the device now — the account's coordinator, or a product
    device's own — and finds it again after a reload replaces it. A scald-limit change reaches it
    the same way: the coordinator update that carries it also redraws the Temperature
    number, whose range the debounced re-send then reads.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg_id: int,
        device_id: str,
    ) -> None:
        self._hass = hass
        self._connection = connection
        self._msg_id = msg_id
        self._device_id = device_id
        self._last: dict[str, Any] | None = None
        self._listened: Any = None
        self._detach: Callable[[], None] | None = None
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=DEBOUNCE_SECONDS,
            immediate=False,
            function=self.send,
        )

    @callback
    def changed(self, *_: Any) -> None:
        self._debouncer.async_schedule_call()

    @callback
    def send(self, ref: DeviceRef | None = None) -> None:
        ref = ref or find_device(self._hass, self._device_id)
        if ref is None:
            return
        # A product device is its own coordinator; a valve or controller is the account's.
        own = ref.kind in ref.coordinator.products
        listened = ref.target if own else ref.coordinator
        if listened is not self._listened:
            if self._detach is not None:
                self._detach()
            self._listened = listened
            self._detach = listened.async_add_listener(self.changed)
        live = device_live(self._hass, ref)
        if live != self._last:
            self._last = live
            self._connection.send_message(
                websocket_api.event_message(self._msg_id, live)
            )

    @callback
    def start(self, ref: DeviceRef) -> Callable[[], None]:
        """Send now, and keep sending; returns the unsubscribe."""
        unsubscribers = [
            async_dispatcher_connect(
                self._hass, SIGNAL_CONFIG_ENTRY_CHANGED, self.changed
            ),
            async_track_time_interval(self._hass, self.changed, REFRESH_INTERVAL),
        ]
        self.send(ref)

        @callback
        def stop() -> None:
            for unsubscriber in unsubscribers:
                unsubscriber()
            if self._detach is not None:
                self._detach()
            self._debouncer.async_cancel()

        return stop


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/subscribe_device",
        vol.Required("device_id"): str,
    }
)
@callback
def ws_subscribe_device(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send one device's live extras now, then again whenever they change."""
    ref = find_device(hass, msg["device_id"])
    if ref is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No such device")
        return
    feed = _LiveFeed(hass, connection, msg["id"], ref.ha_id)
    connection.send_result(msg["id"])
    connection.subscriptions[msg["id"]] = feed.start(ref)


def _device(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    *,
    kinds: tuple[str, ...] | None = None,
    refusal: str = "",
) -> DeviceRef | None:
    """The device a command names, or None once the refusal has been sent."""
    ref = find_device(hass, msg["device_id"])
    if ref is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No such device")
        return None
    if kinds is not None and ref.kind not in kinds:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_SUPPORTED, refusal)
        return None
    return ref


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/usage_history",
        vol.Required("device_id"): str,
        vol.Required("period"): vol.In(PERIODS),
        vol.Optional("anchor"): cv.date,
        # Python's weekday numbers: Monday 0 to Sunday 6.
        vol.Optional("week_start", default=6): vol.All(int, vol.Range(min=0, max=6)),
    }
)
@websocket_api.async_response
async def ws_usage_history(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """One period of a device's water usage, for the Water Usage chart."""
    ref = _device(
        hass,
        connection,
        msg,
        kinds=("valve", "faucet", "toilet", "evocycle"),
        refusal="This device reports no usage",
    )
    if ref is None:
        return
    history = await async_usage_history(
        hass, ref, msg["period"], msg.get("anchor"), msg["week_start"]
    )
    connection.send_result(msg["id"], history)


# --------------------------------------------------------------------------- #
# Administrators only: Settings, About, Diagnostics and Account
# --------------------------------------------------------------------------- #


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/device_details", vol.Required("device_id"): str}
)
@callback
def ws_device_details(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Identity, firmware, parts, faults and leaks, for About and Diagnostics."""
    ref = _device(hass, connection, msg)
    if ref is not None:
        connection.send_result(msg["id"], device_details(hass, ref))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/restart_valve", vol.Required("device_id"): str}
)
@websocket_api.async_response
async def ws_restart_valve(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Restart a valve, as the app's Restart Product does. It stops running water."""
    ref = _device(
        hass, connection, msg, kinds=("valve",), refusal="Only a valve restarts"
    )
    if ref is None:
        return
    await ref.target.async_restart()
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_warmup_auto_restore",
        vol.Required("device_id"): str,
        vol.Required("enabled"): bool,
    }
)
@callback
def ws_set_warmup_auto_restore(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Turn a valve's Warmup Auto-Restore on or off, even with its switch disabled."""
    ref = _device(hass, connection, msg)
    if ref is None:
        return
    # Only beside a controller, whose doing it undoes; see `switch.WarmupAutoRestoreSwitch`.
    if ref.kind != "valve" or not ref.coordinator.controllers:
        connection.send_error(
            msg["id"],
            websocket_api.ERR_NOT_SUPPORTED,
            "Warmup Auto-Restore is for a valve beside an Anthem Plus",
        )
        return
    ref.target.set_warmup_auto_restore(msg["enabled"])
    connection.send_result(msg["id"])


def _entry(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> ConfigEntry | None:
    """The account a command names, or None once the refusal has been sent."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No such account")
        return None
    return entry


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/get_options", vol.Required("entry_id"): str}
)
@callback
def ws_get_options(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """The account's options, as Configure offers them."""
    entry = _entry(hass, connection, msg)
    if entry is not None:
        connection.send_result(msg["id"], account_options(hass, entry))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/update_options",
        vol.Required("entry_id"): str,
        vol.Required("changes"): dict,
    }
)
@callback
def ws_update_options(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Save some of the account's options, checked as Configure checks them."""
    entry = _entry(hass, connection, msg)
    if entry is not None:
        result = async_update_account_options(hass, entry, msg["changes"])
        connection.send_result(msg["id"], result)


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    for command in (
        ws_subscribe_devices,
        ws_subscribe_device,
        ws_usage_history,
        ws_device_details,
        ws_restart_valve,
        ws_set_warmup_auto_restore,
        ws_get_options,
        ws_update_options,
    ):
        websocket_api.async_register_command(hass, command)
