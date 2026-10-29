"""One Smart Fan: read whole over REST, kept current by its `SFC_STS` messages.

The Konnect app reads the fan when its screen opens and otherwise waits for messages, each
of which carries the whole live state (`docs/protocol/smart_fan.md` §2.7, §4). This reads
it at setup, after a reconnect and on the product's slow clock, and applies each message as
it comes. The stored defaults and the favorites change only from the app, with nothing
announced, so they are read again with each whole read.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError

from ..const import DOMAIN, FIRMWARE_CHECK_INTERVAL
from ..konnect import Device, DeviceOffline, Envelope, KohlerError
from ..konnect.faucet import FirmwareInfo, parse_firmware
from ..konnect.smart_fan import (
    DEFAULT_FAN,
    DEFAULT_LIGHT,
    DEFAULT_NIGHTLIGHT,
    KIND_FAN,
    MSG_STATUS,
    OFF,
    ON,
    Favorite,
    SmartFanDevice,
    apply_status,
    is_on,
    parse_favorites,
)
from ..product_base import PendingWrites, ProductCoordinator

if TYPE_CHECKING:
    from ..coordinator import KohlerKonnectCoordinator

__all__ = ["SmartFanCoordinator"]

_LOGGER = logging.getLogger(__name__)

#: What a command says when Kohler answers that the fan is offline (`statusCode 900`).
SMART_FAN_OFFLINE = (
    "The Smart Fan is offline. Check that it is powered on and connected to Wi-Fi, then "
    "try again."
)
# The setup flow's clock report: nothing to show.
_MSG_CLOCK = "SFC_GET_RTC_STS"


class SmartFanCoordinator(ProductCoordinator):
    """One Smart Fan's live state, stored defaults, favorites and firmware."""

    model = "Smart Fan"

    def __init__(
        self, account: KohlerKonnectCoordinator, device: Device, name: str
    ) -> None:
        super().__init__(account, device, name)
        self.device = SmartFanDevice(self.client, self.device_id, self.sku)
        # Chosen in the app at setup, not detected (§1); the account's word is final.
        self._listed_light = device.raw.get("isFanWithLight")
        self._pending = PendingWrites()
        self.firmware: FirmwareInfo | None = None
        self._firmware_read_at: float | None = None
        #: The last replies, as read, for diagnostics.
        self.raw: dict[str, Any] = {}

    # --- reads ------------------------------------------------------------------

    async def async_read(self) -> dict[str, Any]:
        state = await self.device.async_get_state()
        configuration = await self._read_or_keep(
            "configuration", self.device.async_get_configuration
        )
        favorites = await self._read_or_keep(
            "favorites", self.device.async_get_favorites
        )
        await self._async_check_firmware()
        self.raw = {
            "state": state,
            "configuration": configuration,
            "favorites": favorites,
        }
        inner = configuration.get("configuration")
        inner = inner if isinstance(inner, dict) else {}
        live = state.get("state")
        return {
            "connection": state.get("connectionState"),
            "state": dict(live) if isinstance(live, dict) else {},
            "settings": _mapping(inner.get("sfcSettings")),
            "about": _mapping(inner.get("about")),
            "firmware_update": _mapping(inner.get("firmwareUpdate")),
            "favorites": parse_favorites(favorites),
        }

    async def _read_or_keep(self, part: str, read: Any) -> dict[str, Any]:
        """A read that, failing, leaves what the last one said: its data isn't live."""
        try:
            return await read()
        except KohlerError as err:
            _LOGGER.debug("Could not read %s's %s: %s", self.device_name, part, err)
            return self.raw.get(part) or {}

    async def _async_check_firmware(self) -> None:
        """Ask whether newer firmware exists, twice a day; nothing announces one."""
        now = time.monotonic()
        read_at = self._firmware_read_at
        if (
            read_at is not None
            and now - read_at < FIRMWARE_CHECK_INTERVAL.total_seconds()
        ):
            return
        self._firmware_read_at = now
        firmware = parse_firmware(
            await self.client.async_get_firmware(self.device_id, "sfc")
        )
        if firmware is not None:
            self.firmware = firmware

    def apply_envelope(self, envelope: Envelope) -> bool:
        if envelope.code == _MSG_CLOCK:
            return True
        if envelope.code != MSG_STATUS or not envelope.attributes or not self.data:
            return False
        self.data = {
            **self.data,
            "state": apply_status(
                self.data["state"], envelope.attributes[0], self.data["favorites"]
            ),
        }
        return True

    # --- what the entities read ---------------------------------------------------

    @property
    def state(self) -> dict[str, Any]:
        return (self.data or {}).get("state") or {}

    @property
    def settings(self) -> dict[str, Any]:
        """The stored defaults, with any just written that Kohler doesn't show yet."""
        return self._pending.over((self.data or {}).get("settings") or {})

    @property
    def about(self) -> dict[str, Any]:
        return (self.data or {}).get("about") or {}

    @property
    def has_light(self) -> bool:
        """Whether this fan has its main light: only when the account lists one, as the
        app shows it, whatever the fan's own state says."""
        return self._listed_light is True

    @property
    def online(self) -> bool | None:
        """Only an explicit ``Disconnected`` is offline (`platform.md` §5)."""
        connection = (self.data or {}).get("connection")
        if not isinstance(connection, str):
            return None
        return connection.strip().casefold() != "disconnected"

    @property
    def fan_running(self) -> bool:
        return is_on(self.state.get("fanStatus"))

    @property
    def light_on(self) -> bool:
        return is_on(self.state.get("lightStatus"))

    @property
    def night_light_on(self) -> bool:
        return is_on(self.state.get("nightLightStatus"))

    def favorites(self, kind: str) -> list[Favorite]:
        """This fan's saved favorites of one kind, the built-ins left out."""
        favorites: list[Favorite] = (self.data or {}).get("favorites") or []
        return [f for f in favorites if f.kind == kind and not f.is_default]

    def running(self, kind: str) -> Favorite | None:
        """The saved favorite of one kind running now, or None."""
        if not (self.fan_running if kind == KIND_FAN else self.light_on):
            return None
        slot = "runningFanFavorite" if kind == KIND_FAN else "runningLightFavorite"
        running_id = self.state.get(slot)
        return next((f for f in self.favorites(kind) if f.id == running_id), None)

    # --- commands ------------------------------------------------------------------

    def _builtin(self, kind: str) -> Favorite:
        """The built-in fan or light, which runs the stored defaults (§3.2)."""
        favorite_id = DEFAULT_FAN if kind == KIND_FAN else DEFAULT_LIGHT
        return Favorite(favorite_id, favorite_id, kind, True, {})

    async def async_turn_on(self, kind: str, favorite: Favorite | None = None) -> None:
        """Start the fan or light, as its defaults or as one of its favorites."""
        command, keys = (favorite or self._builtin(kind)).command(ON)
        await self.async_command(
            self.device.async_command(command, keys), SMART_FAN_OFFLINE
        )

    async def async_turn_off(self, kind: str) -> None:
        """Stop the fan or light: the favorite running it, else the built-in (§10 Q2)."""
        command, keys = (self.running(kind) or self._builtin(kind)).command(OFF)
        await self.async_command(
            self.device.async_command(command, keys), SMART_FAN_OFFLINE
        )

    async def async_night_light(self, on: bool) -> None:
        """The built-in night light on or off: it runs its stored brightness (§3.2, C5)."""
        keys = {"id": DEFAULT_NIGHTLIGHT, "status": ON if on else OFF}
        await self.async_command(
            self.device.async_command("nightlight", keys), SMART_FAN_OFFLINE
        )

    async def async_sync_clock(self) -> None:
        """Give the fan Home Assistant's time zone (C9).

        The app sends it only while setting the fan up, so a fan that has moved keeps the
        zone it was set up in until this is pressed. Whether it follows daylight saving
        by itself is open (§10).
        """
        keys = {"timeZone": str(self.hass.config.time_zone)}
        await self.async_command(
            self.device.async_command("setrtccontrol", keys), SMART_FAN_OFFLINE
        )

    async def async_stop_all(self) -> None:
        await self.async_command(
            self.device.async_command("stopallcontrol", {}), SMART_FAN_OFFLINE
        )

    def find_favorite(self, kind: str, title: str) -> Favorite:
        favorite = next((f for f in self.favorites(kind) if f.title == title), None)
        if favorite is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="favorite_not_found",
                translation_placeholders={"name": self.device_name, "favorite": title},
            )
        return favorite

    async def async_set_schedule(self, changes: Mapping[str, str]) -> None:
        """Save the fan schedule as the app's editor does (§3.4), refused while the fan
        runs. Once saved, one exists, though the settings say so only when read again."""
        await self.async_set_defaults(changes, blocked_by="fan")
        self._pending.add({"fanScheduleCreated": True})
        self.async_update_listeners()

    async def async_set_default(
        self, field: str, value: str, *, blocked_by: str
    ) -> None:
        """Change one stored default (`async_set_defaults`)."""
        await self.async_set_defaults({field: value}, blocked_by=blocked_by)

    async def async_set_defaults(
        self, changes: Mapping[str, str], *, blocked_by: str
    ) -> None:
        """Change stored defaults, keyed by the read's names, as one of the app's
        settings screens saves them; the fan refuses them while ``blocked_by`` runs.

        `updatedefaultsettings` replaces every default at once, so the fan's state and its
        stored defaults are read again first, as the app's Save reads them: a change made
        in the app since the last read must not be undone by this one (§3.4). Changes
        made together go one at a time. ``blocked_by`` is `fan`, `light`, `night_light`
        or `any`, as in the app (§7).
        """
        async with self.write_lock:
            with self.account.command_errors(SMART_FAN_OFFLINE):
                reply = await self.device.async_get_state()
                if str(reply.get("connectionState")).casefold() == "disconnected":
                    raise DeviceOffline("The fan reports itself disconnected")
                configuration = await self.device.async_get_configuration()
            live = reply.get("state")
            live = live if isinstance(live, dict) else {}
            running = {
                "fan": is_on(live.get("fanStatus")),
                "light": is_on(live.get("lightStatus")),
                "night_light": is_on(live.get("nightLightStatus")),
            }
            if any(running.values()) if blocked_by == "any" else running[blocked_by]:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="settings_while_running",
                    translation_placeholders={"name": self.device_name},
                )
            inner = configuration.get("configuration")
            stored = _mapping(
                inner.get("sfcSettings") if isinstance(inner, dict) else None
            )
            if not stored:
                # ⚠️ Written from nothing, the one change would clear every other default.
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="settings_unreadable",
                    translation_placeholders={"name": self.device_name},
                )
            stored = self._pending.over(stored)
            if all(
                str(stored.get(key)) == str(value) for key, value in changes.items()
            ):
                # Already so: the app's Save stays off until something changes.
                return
            with self.account.command_errors(SMART_FAN_OFFLINE):
                await self.device.async_write_settings(stored, changes)
            self._pending.add(changes)
            self.async_update_listeners()
        await self.async_after_change()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
