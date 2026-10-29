"""Every Kohler product beyond the Anthem shower: how each is built, fed and kept.

The Anthem valve and controller are the account coordinator's own: they share one seed,
one state and the valve-word machinery. Every other product is a set of devices, each with
a coordinator of its own, which the account only has to build from its device list, hand
its MQTT messages, tell when the stream comes and goes, and keep state for across
restarts. A record here says how for one product. Its entities are listed per platform in
`product_platforms.py`, apart because the entity modules import the account coordinator,
which imports this.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.core import HomeAssistant

from .const import (
    DEVICE_NAME_FAUCET,
    DOMAIN,
    FAUCET_SCAN_INTERVAL_IDLE,
    FAUCET_STORAGE_KEY,
    FAUCET_STORAGE_VERSION,
)
from .evocycle.coordinator import EvoCycleCoordinator
from .evocycle.diagnostics import evocycle_report
from .faucet.coordinator import FaucetCoordinator
from .faucet.diagnostics import faucet_report
from .faucet.services import async_register_faucet_services
from .ice_bath.coordinator import IceBathCoordinator
from .ice_bath.diagnostics import ice_bath_report
from .ice_bath.services import async_register_ice_bath_services
from .konnect import Device, Envelope
from .konnect.products import EVOCYCLE, FAUCET, ICE_BATH, SMART_FAN, TOILET, ProductKind
from .smart_fan.coordinator import SmartFanCoordinator
from .smart_fan.diagnostics import smart_fan_report
from .smart_fan.services import async_register_smart_fan_services
from .toilet.coordinator import ToiletCoordinator
from .toilet.diagnostics import toilet_report

if TYPE_CHECKING:
    from .coordinator import KohlerKonnectCoordinator

__all__ = [
    "EVOCYCLES",
    "FAUCETS",
    "ICE_BATHS",
    "PRODUCTS",
    "SMART_FANS",
    "TOILETS",
    "Product",
    "ProductDevice",
]


class ProductDevice(Protocol):
    """What the account coordinator asks of each product device's own coordinator."""

    device_id: str
    #: Its name in Home Assistant.
    device_name: str

    def load(self, stored: Mapping[str, Any]) -> None:
        """Take back what `stored` returned before a restart."""

    def stored(self) -> dict[str, Any]:
        """What to keep across restarts; saved with `async_save_device_state`."""

    async def async_refresh(self) -> None:
        """Read the device whole: its first read, at setup."""

    def async_stream_connected(self, *, just_read: bool) -> None:
        """The stream connected. ``just_read``: the first read stands in for a re-read."""

    def async_stream_disconnected(self) -> None:
        """The stream dropped; nothing will announce changes until it reconnects."""

    def async_handle_envelope(self, envelope: Envelope) -> None:
        """A stream message about this device."""


@dataclass(frozen=True, kw_only=True)
class Product:
    """How the account builds, feeds and keeps one product's devices."""

    kind: ProductKind
    #: One device's coordinator, given the account, its listing and its name.
    create: Callable[[KohlerKonnectCoordinator, Device, str], ProductDevice]
    #: What a device is called when the Konnect app gives it no usable name.
    default_name: str
    #: Its block of the diagnostics report, identity redacted.
    diagnostics: Callable[[Any], dict[str, Any]]
    #: Registers its actions; idempotent, and only called when the account has one.
    register_services: Callable[[HomeAssistant], None] | None = None
    #: The file per entry its devices' `stored` state is kept in.
    storage_key: str
    storage_version: int
    #: How recent a first read must be to stand in for the stream's first connect.
    fresh_for: timedelta

    @property
    def key(self) -> str:
        return self.kind.key


FAUCETS = Product(
    kind=FAUCET,
    create=lambda account, device, name: FaucetCoordinator(
        account.hass, account.entry, account, device, name
    ),
    default_name=DEVICE_NAME_FAUCET,
    diagnostics=faucet_report,
    register_services=async_register_faucet_services,
    storage_key=FAUCET_STORAGE_KEY,
    storage_version=FAUCET_STORAGE_VERSION,
    # A faucet polls at this pace until the stream proves it reports the faucet.
    fresh_for=FAUCET_SCAN_INTERVAL_IDLE,
)

SMART_FANS = Product(
    kind=SMART_FAN,
    create=SmartFanCoordinator,
    default_name="Smart Fan",
    diagnostics=smart_fan_report,
    register_services=async_register_smart_fan_services,
    storage_key=DOMAIN + ".{entry_id}.smart_fans",
    storage_version=1,
    fresh_for=SmartFanCoordinator.poll_interval,
)

TOILETS = Product(
    kind=TOILET,
    create=ToiletCoordinator,
    default_name="Toilet",
    diagnostics=toilet_report,
    storage_key=DOMAIN + ".{entry_id}.toilets",
    storage_version=1,
    fresh_for=ToiletCoordinator.poll_interval,
)

EVOCYCLES = Product(
    kind=EVOCYCLE,
    create=EvoCycleCoordinator,
    default_name="EvoCycle",
    diagnostics=evocycle_report,
    storage_key=DOMAIN + ".{entry_id}.evocycles",
    storage_version=1,
    fresh_for=EvoCycleCoordinator.poll_interval,
)

ICE_BATHS = Product(
    kind=ICE_BATH,
    create=IceBathCoordinator,
    default_name="Ice Bath",
    diagnostics=ice_bath_report,
    register_services=async_register_ice_bath_services,
    storage_key=DOMAIN + ".{entry_id}.ice_baths",
    storage_version=1,
    fresh_for=IceBathCoordinator.poll_interval,
)

PRODUCTS: tuple[Product, ...] = (FAUCETS, SMART_FANS, TOILETS, EVOCYCLES, ICE_BATHS)
