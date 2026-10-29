"""One Numi or Purewash: its state read over REST and kept current by its `NUMI_*` messages.

The app reads the state when a screen opens and otherwise follows the messages, each of
which says what changed (`docs/protocol/numi_purewash.md` §2.6, §4). This reads it at setup,
after a reconnect and on the product's slow clock, and applies each message as it comes.

Commands follow the app's own interlocks (§7.2): a spray or the dryer starts only for
someone seated and never during the UV clean, cleaning, the wand, the cover and the seat
ring wait until nobody is, and Stop is always sent. Settings changes go one at a time,
each from a fresh read (`write_lock`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError

from ..const import DOMAIN, FIRMWARE_CHECK_INTERVAL
from ..konnect import Device, Envelope, KohlerError
from ..konnect.const import SKU_NUMI
from ..konnect.faucet import FirmwareInfo, parse_firmware
from ..konnect.toilet import (
    STARTS,
    STOP,
    WASH_PATHS,
    WASHES,
    ToiletDevice,
    activity,
    apply_message,
    is_cleaning,
    is_seated,
    numi_configuration,
    purewash_configuration,
)
from ..product_base import PendingWrites, ProductCoordinator
from ..product_usage import ProductUsage

if TYPE_CHECKING:
    from ..coordinator import KohlerKonnectCoordinator

__all__ = ["ToiletCoordinator"]

_LOGGER = logging.getLogger(__name__)

TOILET_OFFLINE = "Kohler reports the {model} offline. Check that it has power and Wi-Fi, then try again."
# Kept across whole reads: only messages say them.
_FROM_MESSAGES = ("error", "deviceState")
#: How long the app waits after a default's device command before storing it (§3.5).
SAVE_DELAY = 2.0
# A setting's field in a command, against its name in the state and settings (§3.3).
_STATE_FIELD = {
    "temperature": "temperature",
    "flow": "flow",
    "position": "position",
    "mode": "massage",
}
# What the app's dial holds for a default never stored (§3.5).
_MIDDLE = {
    "temperature": "HeatMid",
    "flow": "FlowMid",
    "position": "PositionMid",
    "mode": "ModeNormal",
}


class ToiletCoordinator(ProductCoordinator):
    """One Numi 2.0 or Purewash E930."""

    def __init__(
        self, account: KohlerKonnectCoordinator, device: Device, name: str
    ) -> None:
        super().__init__(account, device, name)
        self.device = ToiletDevice(self.client, self.device_id, self.sku)
        #: The Numi flushes and has the ambient light; the Purewash has more washes.
        self.is_numi = self.sku == SKU_NUMI
        self.model = "Numi 2.0" if self.is_numi else "Purewash E930"
        #: The sprays and the dryer this model has, by their state key.
        self.starts = (*WASHES.get(self.sku, WASHES[SKU_NUMI]), "dryer")
        self.firmware: FirmwareInfo | None = None
        self._firmware_read_at: float | None = None
        #: The last reply, as read, for diagnostics.
        self.raw: dict[str, Any] = {}
        self.usage_reader = ProductUsage(
            self.client, self.device_id, "numi" if self.is_numi else "blade"
        )
        #: The time zone has been sent this run (§3.12); the app sends it once too.
        self._zone_sent = False
        # Defaults just stored, by part, and auto settings just written.
        self._pending_settings: defaultdict[str, PendingWrites] = defaultdict(
            PendingWrites
        )
        self._pending_configuration = PendingWrites()

    @property
    def _offline(self) -> str:
        return TOILET_OFFLINE.format(model=self.model)

    async def async_read(self) -> dict[str, Any]:
        reply = await self.device.async_get_state()
        try:
            settings = await self.device.async_get_settings()
        except KohlerError as err:
            # The stored defaults aren't live: keep the last ones.
            _LOGGER.debug("Could not read %s's defaults: %s", self.device_name, err)
            settings = self.raw.get("settings") or {}
        await self._async_check_firmware()
        self.raw = {"state": reply, "settings": settings}
        live = reply.get("state")
        state = dict(live) if isinstance(live, dict) else {}
        for key in _FROM_MESSAGES:
            if key in self.state:
                state.setdefault(key, self.state[key])
        await self._async_send_zone(state)
        stored = settings.get("setting")
        return {
            "connection": reply.get("connectionState"),
            "state": state,
            "settings": stored if isinstance(stored, dict) else {},
        }

    async def _async_send_zone(self, state: Mapping[str, Any]) -> None:
        """Tell a Numi its time zone when it reports none, as the app does (§3.12):
        with no `cleanStatus`, or none in it."""
        if not self.is_numi or self._zone_sent:
            return
        clean = state.get("cleanStatus")
        if isinstance(clean, Mapping) and clean.get("timezone") is not None:
            return
        self._zone_sent = True
        try:
            await self.device.async_command(
                "setrtccontrol", {"timeZone": str(self.hass.config.time_zone)}
            )
        except KohlerError as err:
            _LOGGER.debug("Could not set %s's time zone: %s", self.device_name, err)

    async def _async_check_firmware(self) -> None:
        now = time.monotonic()
        read_at = self._firmware_read_at
        if (
            read_at is not None
            and now - read_at < FIRMWARE_CHECK_INTERVAL.total_seconds()
        ):
            return
        self._firmware_read_at = now
        payload = await self.client.async_get_firmware(
            self.device_id, self.device.segment
        )
        if (firmware := parse_firmware(payload)) is not None:
            self.firmware = firmware

    def apply_envelope(self, envelope: Envelope) -> bool:
        if not self.data:
            return False
        attributes = envelope.attributes[0] if envelope.attributes else {}
        state = apply_message(self.state, envelope.code, attributes)
        if state is None:
            return False
        self.data = {**self.data, "state": state}
        return True

    # --- what the entities read ---------------------------------------------------

    @property
    def state(self) -> dict[str, Any]:
        return (self.data or {}).get("state") or {}

    def block(self, key: str) -> dict[str, Any]:
        value = self.state.get(key)
        return value if isinstance(value, dict) else {}

    @property
    def online(self) -> bool | None:
        """Only an explicit ``Disconnected`` is offline (`platform.md` §5)."""
        connection = (self.data or {}).get("connection")
        if not isinstance(connection, str):
            return None
        return connection.strip().casefold() != "disconnected"

    @property
    def controllable(self) -> bool:
        """The app's stricter test for its controls: exactly ``Connected`` (§7.1)."""
        return (self.data or {}).get("connection") == "Connected"

    @property
    def seated(self) -> bool:
        return is_seated(self.state)

    @property
    def activity(self) -> str | None:
        return activity(self.state, self.starts)

    @property
    def cleaning(self) -> bool:
        return is_cleaning(self.state)

    # --- commands --------------------------------------------------------------------

    def _refuse(self, key: str) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={"name": self.device_name},
        )

    async def _async_send(self, verb: str, keys: Mapping[str, str]) -> None:
        await self.async_command(self.device.async_command(verb, keys), self._offline)

    def _refuse_while_cleaning(self) -> None:
        if self.cleaning:
            raise self._refuse("toilet_cleaning")

    async def async_start(self, key: str) -> None:
        """Start one spray or the dryer: refused unless someone is seated (913), and
        during the UV clean. Then, as the app's dial does, it is set to its stored
        defaults where it runs otherwise (§3.2)."""
        if not self.seated:
            raise self._refuse("toilet_not_seated")
        self._refuse_while_cleaning()
        async with self.write_lock:
            await self._async_send("spray-dryer", {"operation": STARTS[key]})
            if (keys := self._defaults_to_apply(key)) is not None:
                command = "dryer" if key == "dryer" else WASH_PATHS[key][0]
                await self._async_send(command, keys)

    def _defaults_to_apply(self, key: str) -> dict[str, str] | None:
        """The live settings a part just started needs to run at its stored defaults,
        or None when it runs at them already or none are stored."""
        stored = self.settings.get(key)
        live = self.block(key)
        if not isinstance(stored, dict):
            return None
        if key == "dryer":
            # The Purewash keeps its fan speed only as a default (§3.3).
            fields = ("temperature", "flow") if self.is_numi else ("temperature",)
            if not all(stored.get(name) for name in fields):
                return None
            if all(stored.get(name) == live.get(name) for name in fields):
                return None
            keys = {"temperature": stored["temperature"], "position": "", "mode": ""}
            keys["flow"] = stored["flow"] if self.is_numi else ""
            return keys
        keys = {name: stored.get(field) for name, field in _STATE_FIELD.items()}
        if not all(isinstance(value, str) and value for value in keys.values()):
            return None
        if all(live.get(field) == keys[name] for name, field in _STATE_FIELD.items()):
            return None
        return keys

    async def async_stop(self) -> None:
        """Stop any spray and the dryer. Always sent."""
        await self._async_send("spray-dryer", {"operation": STOP})

    async def async_flush(self, full: bool) -> None:
        """A full or eco flush (Numi only), as the app sends it: no seated test."""
        await self._async_send(
            "flush", {"flushType": "FullFlushStart" if full else "EcoFlushStart"}
        )

    async def async_move(self, part: str) -> None:
        """Open or close the cover (``lid``) or seat ring (``ring``): each toggles."""
        if self.seated:
            raise self._refuse("toilet_seated")
        await self._async_send(part, {})

    async def async_clean(self, start: bool) -> None:
        """Start or stop the clean cycle; starting waits until nobody is seated."""
        if start and self.seated:
            raise self._refuse("toilet_seated")
        await self._async_send(
            "sanitize", {"operation": "SanitizeStart" if start else "SanitizeStop"}
        )

    async def async_wand(self, extend: bool) -> None:
        """Extend the wand for cleaning, or retract it; extending waits for an empty seat."""
        if extend and self.seated:
            raise self._refuse("toilet_seated")
        await self._async_send(
            "clean-wand", {"operation": "CleanWandStart" if extend else "CleanWandStop"}
        )

    async def async_heat_seat(self, level: str) -> None:
        await self.async_command(self.device.async_heat_seat(level), self._offline)

    @property
    def settings(self) -> dict[str, Any]:
        """The stored defaults: each wash's, the dryer's and the seat's (§2.4), with any
        just stored that Kohler doesn't show yet."""
        stored = dict((self.data or {}).get("settings") or {})
        for part, pending in self._pending_settings.items():
            block = stored.get(part)
            stored[part] = pending.over(block if isinstance(block, dict) else {})
        return stored

    def running(self, part: str) -> bool:
        """Whether a wash (its state key) or the dryer runs now."""
        return self.block(part).get("state") == "On"

    async def async_set_wash(self, wash: str, field: str, value: str) -> None:
        """Change one of a wash's four settings, as the app's dial does (§3.3, §3.5).

        While that wash runs, the change goes to it alone and the default stays. Otherwise
        it is the default: the device command, then two seconds later the stored setting.
        Every write carries all four, from the live state or a fresh read of the defaults.
        """
        command, part = WASH_PATHS[wash]
        async with self.write_lock:
            if self.running(wash):
                self._refuse_while_cleaning()
                await self._async_send(
                    command, _settings_keys(self.block(wash), field, value, self)
                )
                return
            stored = await self._async_fresh_settings(wash)
            keys = _settings_keys(stored, field, value, self, fill=True)
            await self._async_send(command, keys)
            await self._async_store(
                wash,
                part,
                {
                    "flow": keys["flow"],
                    "temperature": keys["temperature"],
                    "position": keys["position"],
                    "massage": keys["mode"],
                },
            )

    async def async_set_dryer(self, field: str, value: str) -> None:
        """Change the dryer's temperature or fan speed, as the app's dial does (§3.3, §3.5).

        The Purewash keeps its fan speed only as a default, so that one waits until the
        dryer stops rather than reach it mid-run.
        """
        async with self.write_lock:
            if self.running("dryer"):
                if field == "flow" and not self.is_numi:
                    raise self._refuse("settings_while_running")
                self._refuse_while_cleaning()
                live = self.block("dryer")
                keys = {
                    "temperature": live.get("temperature"),
                    # The Purewash's live write leaves the fan speed empty (§3.3).
                    "flow": live.get("flow") if self.is_numi else "",
                    "position": "",
                    "mode": "",
                }
                keys[field] = value
                if not keys["temperature"] or (self.is_numi and not keys["flow"]):
                    raise self._refuse("settings_unreadable")
                await self._async_send("dryer", keys)
                return
            stored = await self._async_fresh_settings("dryer")
            # A default never stored is the dial's middle, as the app fills it.
            keys = {
                "temperature": stored.get("temperature") or _MIDDLE["temperature"],
                "flow": stored.get("flow") or _MIDDLE["flow"],
            }
            keys[field] = value
            # The default write keeps the model's position and pattern (§3.5).
            await self._async_send(
                "dryer", {**keys, "position": "PositionMid", "mode": "ModeNormal"}
            )
            await self._async_store("dryer", "dryer", keys)

    async def async_set_seat_default(self, level: str) -> None:
        """Store the seat heat a session starts with: C5, then S3 (§3.5)."""
        async with self.write_lock:
            await self._async_send("heated-seat", {"temperature": level})
            await self._async_store("seat", "seat", {"temperature": level})

    async def _async_fresh_settings(self, part: str) -> Mapping[str, Any]:
        """One part's stored defaults, read now, with any just stored laid over them; a
        part never stored is empty, and the reply must at least hold the defaults."""
        with self.account.command_errors(self._offline):
            reply = await self.device.async_get_settings()
        stored = reply.get("setting")
        if not isinstance(stored, dict):
            raise self._refuse("settings_unreadable")
        block = stored.get(part)
        return self._pending_settings[part].over(
            block if isinstance(block, dict) else {}
        )

    async def _async_store(self, key: str, part: str, body: Mapping[str, str]) -> None:
        """Store a default after its device command, as the app does two seconds on.

        ``key`` names the part as the settings read does, ``part`` as its path does.
        """
        await asyncio.sleep(SAVE_DELAY)
        await self.async_command(
            self.device.async_store_setting(part, body), self._offline
        )
        self._pending_settings[key].add(body)
        self.async_update_listeners()

    async def async_power_save(self, on: bool) -> None:
        await self._async_send(
            "auto-power-save", {"state": "Enable" if on else "Disable"}
        )

    async def async_configure(self, changes: Mapping[str, str]) -> None:
        """Change auto settings: every one of them is written at once (§3.10).

        So the state is read again first, and the rest go out as it says, or as just
        written where the state doesn't show it yet. With no state to go by, nothing is
        sent: the write would turn every other setting off.
        """
        async with self.write_lock:
            with self.account.command_errors(self._offline):
                reply = await self.device.async_get_state()
            live = reply.get("state")
            if not isinstance(live, dict) or not live:
                raise self._refuse("settings_unreadable")
            build = numi_configuration if self.is_numi else purewash_configuration
            body = {**self._pending_configuration.over(build(live)), **changes}
            await self._async_send("write-configuration", body)
            self._pending_configuration.add(changes)

    async def async_ambient_light(
        self, on: bool, theme: str | None = None, brightness: int | None = None
    ) -> None:
        """The Numi's ambient light (§3.11): its theme and brightness, then wake or sleep.

        ``brightness`` is the app's 1–10 scale.
        """
        if not on:
            await self._async_send(
                "multimedia-operation", {"operation": "MultimediaSleep"}
            )
            return
        ambient = self.block("light").get("ambientLight")
        ambient = ambient if isinstance(ambient, dict) else {}
        level = brightness or _level(ambient.get("brightness")) or 10
        await self._async_send(
            "lighting",
            {
                "color": theme or str(ambient.get("colorTheme") or "DeepOcean"),
                "luminance": str(level),
                "r": "0",
                "g": "0",
                "b": "0",
                "msp": "NormalSpeed",
            },
        )
        if ambient.get("state") != "On":
            await self._async_send(
                "multimedia-operation", {"operation": "MultimediaWakeUp"}
            )

    async def async_circadian(self, on: bool) -> None:
        await self._async_send("rhythm", {"operation": "Start" if on else "Stop"})


def _level(value: Any) -> int | None:
    try:
        level = int(str(value).strip())
    except ValueError:
        return None
    return level if 1 <= level <= 10 else None


def _settings_keys(
    source: Mapping[str, Any],
    field: str,
    value: str,
    toilet: ToiletCoordinator,
    *,
    fill: bool = False,
) -> dict[str, str]:
    """A wash's four settings for a command, from ``source`` with one of them changed.

    ``fill`` takes a stored default never set as the dial's middle, as the app does.
    Otherwise a gap is refused rather than sent: it would go out as nothing.
    """
    keys = {name: source.get(state_name) for name, state_name in _STATE_FIELD.items()}
    if fill:
        keys = {name: setting or _MIDDLE[name] for name, setting in keys.items()}
    keys[field] = value
    if not all(isinstance(setting, str) and setting for setting in keys.values()):
        raise toilet._refuse("settings_unreadable")
    return keys
