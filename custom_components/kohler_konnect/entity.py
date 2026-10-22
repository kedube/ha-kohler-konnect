"""Shared entity bases for the showers. Faucet entities have their own, in `faucet/`.

Two kinds of shower device, each bound to its own coordinator object:

* **Anthem Valve** (:class:`~.coordinator.Valve`) — authoritative for outlets,
  temperature and flow.
* **Anthem Plus** (:class:`~.coordinator.Controller`) — favorites, music, steam and
  lighting.

A valve and a controller often drive the same physical shower, but they are never merged:
their state arrives separately and can disagree. User-facing strings say "Anthem" and
"Anthem Plus", never the API product codes ``GCS`` and ``HUB``.
"""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    OUTLET_TYPE_NAMES,
    ZONE_GROUPING_NUMBERED,
    ZONE_GROUPING_OUTLET_LABELS,
    ZONE_GROUPING_SUBDEVICES,
)
from .coordinator import Controller, KohlerKonnectCoordinator, Valve
from .registry import parent_link

__all__ = [
    "KohlerControllerEntity",
    "KohlerValveEntity",
    "ZoneWordEntity",
    "outlet_name",
    "outlet_unique_id",
    "valve_device_info",
    "zone_device_id",
    "zone_device_info",
    "zone_label",
]


def valve_device_info(valve: Valve) -> DeviceInfo:
    """The valve's own device; shared by valve entities and setup's parent registration."""
    return DeviceInfo(
        identifiers={(DOMAIN, valve.device_id)},
        # See `coordinator.valve_names`.
        name=valve.name,
        manufacturer="Kohler",
        # The valve's layout model number (e.g. K-28212), as printed on the hardware.
        model=valve.model.sku,
        model_id=valve.model.name,
        serial_number=valve.gcs_device.serial_number,
    )


def zone_device_id(valve: Valve, zone: int) -> str:
    """The identifier of a zone's sub-device in `ZONE_GROUPING_SUBDEVICES` mode."""
    return f"{valve.device_id}_zone_{zone}"


def zone_device_info(valve: Valve, zone: int) -> DeviceInfo:
    """One zone of a multi-zone valve as its own device, linked to the valve's."""
    return DeviceInfo(
        identifiers={(DOMAIN, zone_device_id(valve, zone))},
        **parent_link((DOMAIN, valve.device_id), valve.registry_id),
        name=f"{valve.name} Zone {zone}",
        manufacturer="Kohler",
        model=valve.model.sku,
        model_id=f"{valve.model.name} (Zone {zone})",
        serial_number=valve.gcs_device.serial_number,
    )


def _fixture_at(valve: Valve, zone: int, position: int) -> str | None:
    """The confirmed fixture name for a 1-based outlet in this zone, or None."""
    flat = valve.model.outlet_id(zone, position)
    limits = valve.gcs_state.outlet_limits.get(flat)
    code = None if limits is None else limits.outlet_type
    return None if code is None else OUTLET_TYPE_NAMES.get(code)


def zone_label(
    device: Valve | Controller,
    zone: int,
    label: str,
    grouping: str | None = None,
) -> str:
    """`label` unchanged on a single-zone device, else disambiguated by grouping mode.

    On a multi-zone valve:

    * **numbered** (default) — `Temperature 1`, `Temperature 2`;
    * **subdevices** — `Temperature`, since the zone's own device scopes it;
    * **outlet_labels** — `Temperature (Showerhead, Body Sprays)`, or the zone number
      until a fixture in the zone is known.

    A controller has no fixture codes, so a multi-zone controller always gets the number.
    """
    if len(device.model.zones) <= 1:
        return label
    if hasattr(device, "gcs_state"):
        mode = grouping if grouping is not None else device.zone_grouping
        if mode == ZONE_GROUPING_SUBDEVICES:
            return label
        if mode == ZONE_GROUPING_OUTLET_LABELS:
            count = device.model.outlets_in_zone(zone)
            if any(
                _fixture_at(device, zone, pos) is not None
                for pos in range(1, count + 1)
            ):
                outlets = ", ".join(
                    outlet_name(device, zone, pos, grouping=ZONE_GROUPING_SUBDEVICES)
                    for pos in range(1, count + 1)
                )
                return f"{label} ({outlets})"
    return f"{label} {zone}"


def outlet_name(
    valve: Valve,
    zone: int,
    outlet: int,
    grouping: str | None = None,
) -> str:
    """An outlet's display name: `Rainhead`, `Rainhead 2`, or `Outlet 1` when unknown.

    Shared by the outlet switches and the Max Shower Duration select's `per_outlet`.

    * The fixture name is used where the valve's `outLetType` maps to a confirmed one;
      otherwise the position (`Outlet 1`, or `Outlet 1.2` with several zones on one
      device).
    * The zone suffix follows the grouping mode: none on a single-zone valve or in
      `subdevices`; in `outlet_labels` only when the fixture type appears in more than one
      zone; in `numbered` always (`Showerhead 1`).

    The outlet switches read it once, at construction, so a fixture type that arrives
    after setup is picked up at the next reload.
    """
    mode = grouping if grouping is not None else valve.zone_grouping
    single_zone_scope = len(valve.model.zones) <= 1 or mode == ZONE_GROUPING_SUBDEVICES

    fixture = _fixture_at(valve, zone, outlet)
    if fixture is None:
        # No confirmed fixture: name it by position.
        if not single_zone_scope:
            return f"Outlet {zone}.{outlet}"
        return f"Outlet {outlet}"

    # A zone can hold two outlets of one fixture type (e.g. two showerheads on a
    # K-28212); each gets its position among them as a suffix so the names differ.
    same = [
        position
        for position in range(1, valve.model.outlets_in_zone(zone) + 1)
        if _fixture_at(valve, zone, position) == fixture
    ]
    if len(same) > 1:
        # Zone first, then position: the second showerhead in zone 2 is `Showerhead 2.2`.
        position = same.index(outlet) + 1
        if not single_zone_scope:
            return f"{fixture} {zone}.{position}"
        return f"{fixture} {position}"

    if single_zone_scope:
        return fixture

    if mode == ZONE_GROUPING_OUTLET_LABELS:
        # Omit the zone number when this fixture type appears in only one zone.
        zones_with_fixture = [
            z
            for z in valve.model.zones
            for pos in range(1, valve.model.outlets_in_zone(z) + 1)
            if _fixture_at(valve, z, pos) == fixture
        ]
        if len(zones_with_fixture) == 1:
            return fixture
        return f"{fixture} {zone}"

    return zone_label(valve, zone, fixture, grouping=ZONE_GROUPING_NUMBERED)


def outlet_unique_id(valve: Valve, zone: int, outlet: int) -> str:
    """An outlet switch's unique id: the valve's id and the outlet's position.

    ⚠️ Never derived from the fixture name or `zone_grouping`: the fixture type may not be
    known at setup, and the id must not change once it is.
    """
    return f"{valve.device_id}_zone_{zone}_outlet_{outlet}"


class _KohlerDeviceEntity(CoordinatorEntity[KohlerKonnectCoordinator]):
    """Re-render only for a message about this entity's own device.

    One coordinator serves every shower entity. `KohlerKonnectCoordinator.devices_changed`
    names the device a notification is about; entities of other devices skip the state
    write. When it is None (reseed, usage read, first message after a failed refresh, ...)
    everything re-renders.

    `_device_id` is the Kohler device's id, the valve's for zone sub-device entities too.
    """

    _device_id: str

    @property
    def _update_device_ids(self) -> frozenset[str] | None:
        """The devices whose messages re-render this entity; None for every message."""
        return frozenset((self._device_id,))

    @callback
    def _handle_coordinator_update(self) -> None:
        changed = self.coordinator.devices_changed
        if changed is not None:
            wanted = self._update_device_ids
            if wanted is not None and changed.isdisjoint(wanted):
                return
        super()._handle_coordinator_update()


class KohlerValveEntity(_KohlerDeviceEntity):
    """Base for entities belonging to one Anthem digital valve.

    Unique ids are built on the valve's device id. With `zone` on a multi-zone valve in
    `ZONE_GROUPING_SUBDEVICES` mode the entity attaches to that zone's sub-device, but
    `_device_id` (and so the unique id) stays the valve's.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KohlerKonnectCoordinator,
        valve: Valve,
        *,
        zone: int | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._valve = valve
        self._device_id = valve.device_id
        grouping = valve.zone_grouping
        if (
            zone is not None
            and len(valve.model.zones) > 1
            and grouping == ZONE_GROUPING_SUBDEVICES
        ):
            self._attr_device_info = zone_device_info(valve, zone)
        else:
            self._attr_device_info = valve_device_info(valve)

    @property
    def _update_device_ids(self) -> frozenset[str] | None:
        """This valve, plus the controller when the account has exactly one of each.

        Only that pairing is treated as one shower; the valve's `System Status` then reads
        the controller's warm-up.
        """
        coordinator = self.coordinator
        if len(coordinator.valves) == 1 and len(coordinator.controllers) == 1:
            return frozenset((self._device_id, coordinator.controllers[0].device_id))
        return frozenset((self._device_id,))

    @property
    def _state(self):
        return self._valve.gcs_state


class ZoneWordEntity(KohlerValveEntity):
    """A valve entity scoped to one zone, reading that zone's command word.

    The zone-to-word mapping lives only here: a mistake silently reads the other zone.
    """

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, valve: Valve, zone: int
    ) -> None:
        super().__init__(coordinator, valve, zone=zone)
        self._zone = zone

    @property
    def _word(self):
        """This zone's command word, or None before any state has arrived."""
        state = self._state
        if state is None:
            return None
        return state.valve1 if self._zone == 1 else state.valve2


class KohlerControllerEntity(_KohlerDeviceEntity):
    """Base for entities belonging to one Anthem Plus system controller.

    Unique ids are built on the controller's device id. Available whenever the entry is,
    with no staleness timeout: state is push-only, an idle shower is silent, and the REST
    reseed refreshes it on every reconnect. The Last Update sensor shows freshness.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator)
        self._controller = controller
        self._device_id = controller.device_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, controller.device_id)},
            # See `coordinator.controller_names`.
            name=controller.name,
            manufacturer="Kohler",
            model="Anthem+ System Controller",
            serial_number=controller.device.serial_number,
            # LAN web settings page from `hub-configuration`; the coordinator updates it
            # after each seed.
            configuration_url=controller.settings.web_url,
        )

    @property
    def _state(self):
        return self._controller.state
