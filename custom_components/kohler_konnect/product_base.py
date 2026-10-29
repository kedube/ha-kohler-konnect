"""What the products beyond the shower and the faucet share: one coordinator and entity shape.

Each such product (`products.py`) is read whole over REST and told of changes over the one
MQTT stream. Its coordinator reads it at setup, again whenever a message or a reconnect says
it changed, and on a slow clock in case one was missed: slower once a message has shown the
stream carries the device's changes, as the faucet's slows. Commands go out through
`async_command`, which reports a failure as the person who sent it should see it, then
waits for the device to say what it did, reading it again only if it doesn't.

None of these products has been seen on hardware by this project. Their controls are built
to the Konnect app's exact requests, and each is an `UntestedControl` until an owner's
Report Log confirms it: flagged in its attributes and in the panel.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN
from .konnect import AuthError, Device, Envelope, KohlerError, credential_is_dead

if TYPE_CHECKING:
    from .coordinator import KohlerKonnectCoordinator
    from .product_usage import ProductUsage

__all__ = ["PendingWrites", "ProductCoordinator", "ProductEntity", "UntestedControl"]

_LOGGER = logging.getLogger(__name__)


class ProductCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """One device of a product, read over REST and re-read when the stream says so.

    A subclass names its model and implements `async_read`, one whole read of the device.
    """

    #: How often to read with nothing announced, in case a message was missed.
    poll_interval: ClassVar[timedelta] = timedelta(minutes=5)
    #: How often once the stream has carried a message about it, and while it stays up.
    #: The app reads only when a screen opens, so a stream that works needs little more.
    heard_poll_interval: ClassVar[timedelta] = timedelta(minutes=15)
    #: Shown on the device page.
    model: ClassVar[str] = "Kohler device"
    #: Whether its messages come over the account's stream, which the Report Log records.
    streams: ClassVar[bool] = True
    #: How long after a change to wait for the device's own message before reading it
    #: again, about as long as the app waits (`docs/protocol/smart_fan.md` §2.7).
    settle: ClassVar[timedelta] = timedelta(seconds=8)

    def __init__(
        self, account: KohlerKonnectCoordinator, device: Device, name: str
    ) -> None:
        super().__init__(
            account.hass,
            _LOGGER,
            config_entry=account.entry,
            name=f"{DOMAIN} {name}",
            update_interval=self.poll_interval,
            # Collapses a burst of messages into one read, without delaying the first.
            request_refresh_debouncer=Debouncer(
                account.hass, _LOGGER, cooldown=1.0, immediate=True
            ),
        )
        self.account = account
        self.client = account.client
        self.device_id = device.device_id
        self.device_name = name
        self.sku = device.sku.upper()
        self.serial_number = device.serial_number
        #: When a read last found something new (epoch seconds), for the panel.
        self.last_report: float | None = None
        #: The water this device reports (`product_usage.py`), for one with a usage read.
        self.usage_reader: ProductUsage | None = None
        #: Held by each settings change from its read to its last write, so two changes
        #: made together can't each write back the value the other replaced.
        self.write_lock = asyncio.Lock()
        # One list per read out: the messages applied meanwhile (`_async_update_data`).
        self._reads_out: list[list[Envelope]] = []
        self._reread: CALLBACK_TYPE | None = None
        # Whether the stream has carried a message about this device since it connected.
        self._heard = False

    async def async_read(self) -> dict[str, Any]:
        """Read the device whole; raise `KohlerError` or `AuthError` on failure."""
        raise NotImplementedError

    async def _async_update_data(self) -> dict[str, Any]:
        applied: list[Envelope] = []
        self._reads_out.append(applied)
        try:
            data = await self.async_read()
            if self.usage_reader is not None:
                await self.usage_reader.async_refresh()
        except AuthError as err:
            if credential_is_dead(err):
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN, translation_key="auth_failed"
                ) from err
            raise self._update_failed(err) from err
        except KohlerError as err:
            raise self._update_failed(err) from err
        finally:
            self._reads_out.remove(applied)
        if applied:
            # A message that came while the read was out may be newer than what it read.
            self.data = data
            for envelope in applied:
                self.apply_envelope(envelope)
            data = self.data
        if data != self.data:
            self.last_report = time.time()
        return data

    @property
    def usage(self) -> dict[str, Any]:
        """The monthly usage series, as a faucet holds it; empty without a usage read."""
        return self.usage_reader.usage if self.usage_reader is not None else {}

    @property
    def usage_daily(self) -> dict[str, Any]:
        """The daily usage series, as a faucet holds it; empty without a usage read."""
        return self.usage_reader.usage_daily if self.usage_reader is not None else {}

    @property
    def usage_product(self) -> str | None:
        """The usage read, by its path's product (`KohlerClient.async_get_usage`)."""
        return self.usage_reader.product if self.usage_reader is not None else None

    @staticmethod
    def _update_failed(err: Exception) -> UpdateFailed:
        return UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="update_failed",
            translation_placeholders={"error": str(err)},
        )

    async def async_command(self, command: Awaitable[Any], offline: str) -> Any:
        """Send one command, then see what it did (`async_after_change`).

        A failure is raised as the person who sent it should see it, a rejected sign-in
        as a prompt to sign in again (`command_errors`). ``offline`` is what to say when
        Kohler answers that the device is offline.
        """
        with self.account.command_errors(offline):
            result = await command
        await self.async_after_change()
        return result

    async def async_after_change(self) -> None:
        """Catch up with a change just sent.

        A device on the stream says what it did itself, and Kohler's copy read straight
        after a command can still be the one from before it (`platform.md` §2.1). So, as
        the app does, it is read again only if no message about it comes within `settle`.
        A device off the stream is read at once.
        """
        if not self.streams:
            await self.async_request_refresh()
            return
        self._cancel_reread()
        self._reread = async_call_later(self.hass, self.settle, self._async_reread)

    async def _async_reread(self, _now: datetime) -> None:
        self._reread = None
        await self.async_request_refresh()

    @callback
    def _cancel_reread(self) -> None:
        if self._reread is not None:
            self._reread()
            self._reread = None

    async def async_shutdown(self) -> None:
        self._cancel_reread()
        await super().async_shutdown()

    # --- what the account asks of every product device (`products.ProductDevice`) ---

    def load(self, stored: Mapping[str, Any]) -> None:
        """Nothing is kept across restarts unless a subclass says so."""

    def stored(self) -> dict[str, Any]:
        return {}

    def async_stream_connected(self, *, just_read: bool) -> None:
        """Nothing is replayed on connect: read again, unless the first read stands in or
        the device has nothing on the stream to have missed."""
        if not just_read and self.streams:
            self.config_entry.async_create_task(
                self.hass, self.async_request_refresh(), f"{DOMAIN} {self.name} re-read"
            )

    def async_stream_disconnected(self) -> None:
        """The clock goes back to its quicker pace until the stream proves itself again."""
        self._heard = False
        self.update_interval = self.poll_interval

    def async_handle_envelope(self, envelope: Envelope) -> None:
        """A message about this device: apply it, else read the device again.

        Either way it is the device's word on a change just sent (`async_after_change`),
        and proof the stream carries this device, so the clock can slow.
        """
        self._cancel_reread()
        if self.streams and not self._heard:
            self._heard = True
            self.update_interval = self.heard_poll_interval
        if self.apply_envelope(envelope):
            for applied in self._reads_out:
                applied.append(envelope)
            self.last_report = time.time()
            self.async_update_listeners()
            return
        self.config_entry.async_create_task(
            self.hass, self.async_request_refresh(), f"{DOMAIN} {self.name} re-read"
        )

    def apply_envelope(self, envelope: Envelope) -> bool:
        """Apply what a message says to `data` in place; False to read the device instead."""
        return False


class PendingWrites:
    """Settings written but perhaps not yet in what Kohler returns, whose copy can lag.

    Each is laid over every read until a read shows it, or `HOLD` has passed, which is
    longer than the products' slowest clock so that a read comes first. The next change
    then writes it rather than the value it replaced, and its control doesn't flick back.
    """

    HOLD = timedelta(minutes=20)

    def __init__(self) -> None:
        self._values: dict[str, tuple[Any, float]] = {}

    def add(self, values: Mapping[str, Any]) -> None:
        until = time.monotonic() + self.HOLD.total_seconds()
        self._values.update({key: (value, until) for key, value in values.items()})

    def over(self, read: Mapping[str, Any]) -> dict[str, Any]:
        """``read`` with each value written since, that it doesn't show yet, laid over it."""
        now = time.monotonic()
        merged = dict(read)
        for key, (value, until) in list(self._values.items()):
            if now >= until or _same(read.get(key), value):
                del self._values[key]
            else:
                merged[key] = value
        return merged


def _same(read: Any, written: Any) -> bool:
    """Whether a read shows a written value, across the types Kohler mixes."""
    if read == written:
        return True
    try:
        return float(read) == float(written)
    except (TypeError, ValueError):
        return str(read).casefold() == str(written).casefold()


class ProductEntity(CoordinatorEntity[ProductCoordinator]):
    """An entity of one product device, named after what it shows or does."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ProductCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.device_id)},
            manufacturer="Kohler",
            name=coordinator.device_name,
            model=coordinator.model,
            serial_number=coordinator.serial_number,
        )


class UntestedControl:
    """A control built to the Konnect app's requests that nobody has seen work on hardware.

    Flagged ``untested`` until an owner's Report Log confirms it. The flag is a capability
    attribute, so it is in the entity's attributes and also in the entity registry, where
    the panel finds it even for a control someone has turned off.
    """

    @property
    def capability_attributes(self) -> dict[str, Any]:
        inherited = super().capability_attributes  # type: ignore[misc]
        return {**(inherited or {}), "untested": True}
