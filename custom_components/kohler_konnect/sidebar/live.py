"""What a device screen in the sidebar panel needs beyond entity states.

A descriptor (`snapshot.py`) says what a device is; this says what it is doing that no
entity carries: the shower Simple Start would run, how warm a staged shower may be, when a
faucet's safety limit turns its water off, and how its connections are.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from ..const import DOMAIN
from ..konnect.valve_hex import ValveHexError, decode_word
from ..number import _whole_degrees, zone_temperature_range
from .roles import resolve_roles

if TYPE_CHECKING:
    from ..coordinator import Controller, KohlerKonnectCoordinator, Valve
    from ..faucet.coordinator import FaucetCoordinator
    from ..product_base import ProductCoordinator

__all__ = ["DeviceRef", "device_live", "find_device"]


@dataclass(frozen=True)
class DeviceRef:
    """A device as the panel names it, tied to what serves it now."""

    ha_id: str
    entry: ConfigEntry
    coordinator: KohlerKonnectCoordinator
    kind: str
    kohler_id: str
    target: Valve | Controller | FaucetCoordinator | ProductCoordinator


def find_device(hass: HomeAssistant, ha_id: str) -> DeviceRef | None:
    """The loaded device behind a Home Assistant device id, or None."""
    device = dr.async_get(hass).async_get(ha_id)
    if device is None:
        return None
    kohler_ids = {ident for domain, ident in device.identifiers if domain == DOMAIN}
    coordinators = hass.data.get(DOMAIN, {})
    for entry_id in device.config_entries:
        coordinator = coordinators.get(entry_id)
        if coordinator is None:
            continue
        candidates: list[tuple[str, Any]] = [
            *(("valve", v) for v in coordinator.valves),
            *(("controller", c) for c in coordinator.controllers),
            *(
                (kind, device)
                for kind, devices in coordinator.products.items()
                for device in devices
            ),
        ]
        for kind, target in candidates:
            if target.device_id in kohler_ids:
                return DeviceRef(
                    ha_id,
                    coordinator.entry,
                    coordinator,
                    kind,
                    target.device_id,
                    target,
                )
    return None


def _iso(epoch: float | None) -> str | None:
    return None if epoch is None else datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _default_shower(valve: Valve, fahrenheit: bool) -> dict[str, Any]:
    """Simple Start's shower, zone by zone, or why `Shower on` would refuse it."""
    try:
        words = valve.default_shower_words()
    except ValveHexError as err:
        return {"zones": [], "refused_because": str(err)}
    zones = []
    for zone in valve.model.zones:
        decoded = decode_word(words[zone - 1])
        count = valve.model.outlets_in_zone(zone)
        zones.append(
            {
                "zone": zone,
                "outlets": [
                    b + 1 for b in range(count) if decoded.outlet_mask >> b & 1
                ],
                # Rounded as the Temperature number rounds, so the two always agree.
                "temperature": _whole_degrees(
                    round(decoded.temperature_celsius * 10), fahrenheit
                ),
            }
        )
    return {"zones": zones, "refused_because": None}


def _staging_bounds(ref: DeviceRef) -> list[dict[str, Any]]:
    """How warm a staged shower may be, in the account's unit as `custom_shower` takes it.

    The Temperature number's own range above its Cold step, so a lowered scald limit
    tightens it at once. Not read from the entity's state, which Home Assistant converts to
    its own unit system.
    """
    unit = ref.coordinator.temperature_unit
    bounds = []
    for zone in ref.target.model.zones:
        floor, ceiling = zone_temperature_range(ref.target, zone, unit)
        bounds.append({"zone": zone, "min": floor, "max": ceiling})
    return bounds


def _cloud(hass: HomeAssistant, ref: DeviceRef) -> bool | None:
    if ref.kind == "faucet":
        return ref.target.faucet_online
    if ref.kind != "valve":
        return None
    roles = resolve_roles(hass, ref.entry.entry_id, "valve", ref.kohler_id).enabled
    state = hass.states.get(roles.get("cloud", ""))
    if state is None or state.state not in ("on", "off"):
        return None
    return state.state == "on"


def device_live(hass: HomeAssistant, ref: DeviceRef) -> dict[str, Any]:
    """The live extras for one device screen."""
    stream = ref.coordinator.stream
    live: dict[str, Any] = {
        "device_id": ref.ha_id,
        "stream_connected": None if stream is None else bool(stream.connected),
        "cloud_connected": _cloud(hass, ref),
    }
    if ref.kind == "valve":
        valve: Valve = ref.target
        fahrenheit = ref.coordinator.temperature_unit.lower().startswith("f")
        live["last_update"] = _iso(valve.gcs_state.last_update)
        live["default_shower"] = _default_shower(valve, fahrenheit)
        live["staging_bounds"] = _staging_bounds(ref)
    elif ref.kind == "controller":
        live["last_update"] = _iso(getattr(ref.target.state, "last_update", None))
    elif ref.kind == "faucet":
        faucet: FaucetCoordinator = ref.target
        live["last_update"] = _iso(faucet.last_report)
        live["auto_off_at"] = _iso(faucet.auto_off_at)
    else:
        live["last_update"] = None
    return live
