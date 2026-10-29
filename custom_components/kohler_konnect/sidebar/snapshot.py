"""What the sidebar panel is told about each device.

A descriptor names a device the way the owner sees it — its Home Assistant name and area —
and says what it is: its zones and outlets, its units, what it can do, and which entity fills
each role. Live values never travel here; the panel reads them from `hass.states`.

⚠️ Nothing here carries a Kohler device id: those are cloud addresses (see
`diagnostics.py`). Devices go out under Home Assistant's own ids. The controller's LAN link
goes to admins only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr

from ..const import (
    DOMAIN,
    OUTLET_TYPE_VARIANTS,
    ZONE_GROUPING_SUBDEVICES,
)
from ..entity import outlet_name
from ..faucet.units import from_ml
from ..konnect.usage import is_metric
from ..registry import device_by_identifier
from .roles import resolve_roles

if TYPE_CHECKING:
    from ..coordinator import Controller, KohlerKonnectCoordinator, Valve
    from ..faucet.coordinator import FaucetCoordinator

__all__ = ["describe_devices", "ha_device"]

# The faucet's quick amounts on a US account, as its buttons name them; metric amounts are
# named by their size. The panel translates these by key where it can.
_US_QUICK_LABELS = {
    "quarter_cup": "¼ cup",
    "half_cup": "½ cup",
    "one_cup": "1 cup",
    "two_cups": "2 cups",
    "one_quart": "1 quart",
    "half_gallon": "½ gallon",
    "one_gallon": "1 gallon",
}


def ha_device(
    hass: HomeAssistant, entry: ConfigEntry, kohler_id: str
) -> dr.DeviceEntry | None:
    """The device registry's entry for one of the account's devices."""
    return device_by_identifier(dr.async_get(hass), (DOMAIN, kohler_id), entry.entry_id)


def _area(hass: HomeAssistant, device: dr.DeviceEntry) -> str | None:
    if device.area_id is None:
        return None
    area = ar.async_get(hass).async_get_area(device.area_id)
    return None if area is None else area.name


def _units(coordinator: KohlerKonnectCoordinator) -> dict[str, str]:
    fahrenheit = coordinator.temperature_unit.lower().startswith("f")
    return {
        "temperature": "°F" if fahrenheit else "°C",
        "volume": "L" if is_metric(coordinator.water_units) else "gal",
    }


def _base(
    hass: HomeAssistant,
    entry: ConfigEntry,
    kind: str,
    kohler_id: str,
    units: dict[str, str],
) -> dict[str, Any] | None:
    """The fields every kind shares; None until the device is registered."""
    device = ha_device(hass, entry, kohler_id)
    if device is None:
        return None
    roles = resolve_roles(hass, entry.entry_id, kind, kohler_id)
    return {
        "id": device.id,
        "entry_id": entry.entry_id,
        "kind": kind,
        "name": device.name_by_user or device.name or "",
        "area": _area(hass, device),
        "model": device.model,
        "model_name": device.model_id,
        "units": units,
        "zones": [],
        "capabilities": {},
        "roles": roles.enabled,
        "disabled_roles": roles.disabled,
        "untested": roles.untested,
        "untested_device": roles.untested_device,
    }


def _valve(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: KohlerKonnectCoordinator,
    valve: Valve,
) -> dict[str, Any] | None:
    descriptor = _base(hass, entry, "valve", valve.device_id, _units(coordinator))
    if descriptor is None:
        return None
    zones = []
    for zone in valve.model.zones:
        outlets = []
        for position in range(1, valve.model.outlets_in_zone(zone) + 1):
            limits = valve.gcs_state.outlet_limits.get(
                valve.model.outlet_id(zone, position)
            )
            code = None if limits is None else limits.outlet_type
            outlets.append(
                {
                    "position": position,
                    "code": code,
                    # Unnumbered: the panel draws each zone on its own card.
                    "name": outlet_name(
                        valve, zone, position, grouping=ZONE_GROUPING_SUBDEVICES
                    ),
                    "variant": None if code is None else OUTLET_TYPE_VARIANTS.get(code),
                }
            )
        zones.append({"zone": zone, "outlets": outlets})
    descriptor["zones"] = zones
    # What `usage_history` charts.
    descriptor["capabilities"]["usage"] = True
    if coordinator.controllers:
        # Warmup Auto-Restore exists only beside a controller; see `switch.py`.
        descriptor["capabilities"]["warmup_auto_restore"] = valve.warmup_auto_restore
    return descriptor


def _controller(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: KohlerKonnectCoordinator,
    controller: Controller,
    *,
    admin: bool,
) -> dict[str, Any] | None:
    descriptor = _base(
        hass, entry, "controller", controller.device_id, _units(coordinator)
    )
    if descriptor is None:
        return None
    descriptor["zones"] = [
        {
            "zone": zone,
            "outlets": [
                {"position": n, "code": None, "name": f"Outlet {n}", "variant": None}
                for n in range(1, controller.model.outlets_in_zone(zone) + 1)
            ],
        }
        for zone in controller.model.zones
    ]
    capabilities = descriptor["capabilities"]
    capabilities["steam"] = "steam" in descriptor["roles"]
    if admin and controller.settings.web_url:
        capabilities["web_url"] = controller.settings.web_url
    return descriptor


def _faucet(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: KohlerKonnectCoordinator,
    faucet: FaucetCoordinator,
) -> dict[str, Any] | None:
    profile = faucet.profile
    metric = profile.number_unit == "ml"
    unit = "mL" if metric else "fl oz"
    descriptor = _base(
        hass,
        entry,
        "faucet",
        faucet.device_id,
        {**_units(coordinator), "dispense": unit},
    )
    if descriptor is None:
        return None
    descriptor["quick_amounts"] = [
        {
            "key": quick.key,
            "amount": round(from_ml(quick.ml, profile.number_unit), profile.precision),
            "unit": unit,
            "label": quick.placeholders.get("amount")
            or _US_QUICK_LABELS.get(quick.key, quick.key),
        }
        for quick in profile.quick_amounts
    ]
    descriptor["capabilities"]["usage"] = True
    descriptor["capabilities"]["presets"] = [
        {
            "name": name,
            "amount": round(from_ml(preset.liters * 1000, profile.number_unit), 2),
            "unit": unit,
        }
        for name, preset in faucet.preset_options.items()
    ]
    return descriptor


def _product(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: KohlerKonnectCoordinator,
    kind: str,
    device: Any,
) -> dict[str, Any] | None:
    """A device of a product with no view of its own, for the panel's generic view."""
    descriptor = _base(hass, entry, kind, device.device_id, _units(coordinator))
    if descriptor is not None and getattr(device, "usage_reader", None) is not None:
        descriptor["capabilities"]["usage"] = True
    return descriptor


def describe_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: KohlerKonnectCoordinator,
    *,
    admin: bool,
) -> list[dict[str, Any]]:
    """One descriptor per registered device of this entry: valves, controllers, products."""
    descriptors = [
        *(_valve(hass, entry, coordinator, valve) for valve in coordinator.valves),
        *(
            _controller(hass, entry, coordinator, controller, admin=admin)
            for controller in coordinator.controllers
        ),
        *(
            _faucet(hass, entry, coordinator, device)
            if kind == "faucet"
            else _product(hass, entry, coordinator, kind, device)
            for kind, devices in coordinator.products.items()
            for device in devices
        ),
    ]
    return [d for d in descriptors if d is not None]
