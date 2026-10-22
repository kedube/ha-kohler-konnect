"""The Kohler Konnect integration.

All Kohler Konnect devices on one account, under one sign-in, each its own device:

* **Anthem** (SKU ``GCS``) — digital valve with built-in Wi-Fi: outlet, temperature and
  flow control.
* **Anthem Plus** (SKU ``HUB``) — system controller adding music, lighting and steam;
  controlled through favorites.
* **Sensate** and **Setra** (SKUs ``SEN``, ``SET``) — kitchen faucets: water on and off,
  measured dispenses, presets, leak alerts.

One MQTT connection over Azure IoT Hub carries every device's messages. Shower state is
push-only; REST is read at setup and on every MQTT reconnect, because the broker replays
nothing on connect. Faucets also poll, on their own coordinator. Protocol handling lives
in the bundled ``konnect`` package, which has no Home Assistant imports.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.storage import Store

from .const import (
    CONF_MOBILE_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    DOMAIN,
    FAUCET_STORAGE_KEY,
    FAUCET_STORAGE_VERSION,
    ISSUE_REPLACED_INTEGRATION,
    REPLACED_DOMAINS,
    ZONE_GROUPING_SUBDEVICES,
    device_issue_key,
)
from .coordinator import KohlerKonnectCoordinator, entry_reload_signature
from .entity import valve_device_info, zone_device_id
from .konnect import AuthError, KohlerAuth, KohlerClient, KohlerError
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

# How long deleting an entry waits for Kohler to drop the stream's registration.
UNREGISTER_TIMEOUT = 20


@callback
def _async_flag_replaced_integrations(hass: HomeAssistant) -> None:
    """Raise a Repairs card while entries of a replaced integration still exist.

    Entries of `REPLACED_DOMAINS` duplicate this integration's entities and hold a second
    MQTT connection. Removing them is left to the owner; the card clears once they are gone.
    """
    replaced = sorted(
        {
            entry.title or entry.domain
            for domain in REPLACED_DOMAINS
            for entry in hass.config_entries.async_entries(domain)
        }
    )
    if replaced:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_REPLACED_INTEGRATION,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_REPLACED_INTEGRATION,
            translation_placeholders={"entries": ", ".join(replaced)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_REPLACED_INTEGRATION)


@callback
def _async_ensure_parent_valve_devices(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: KohlerKonnectCoordinator
) -> None:
    """Register parent valve devices before the platforms add their zone sub-devices.

    Stores each valve's registry id, which zone devices use to name their parent; see
    `registry.parent_link`.
    """
    if coordinator.zone_grouping != ZONE_GROUPING_SUBDEVICES:
        return
    try:
        dev_reg = dr.async_get(hass)
        for valve in coordinator.valves:
            if len(valve.model.zones) > 1:
                parent = dev_reg.async_get_or_create(
                    config_entry_id=entry.entry_id, **valve_device_info(valve)
                )
                valve.registry_id = parent.id
    except Exception:
        _LOGGER.debug("Device registry unavailable during parent valve setup")


@callback
def _async_cleanup_zone_subdevices(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: KohlerKonnectCoordinator
) -> None:
    """Remove zone sub-devices that are no longer used, keeping their entities.

    Removing a device deletes every entity still attached to it, including disabled ones
    the platforms never re-added, so remaining entities are moved to the valve's device
    first.

    ⚠️ Only for valves with `topology_confirmed`. A valve on a fallback layout can look
    as if its zones are gone; deleting their devices would lose the owner's names and
    areas.
    """
    confirmed = [valve for valve in coordinator.valves if valve.topology_confirmed]
    active: set[str] = set()
    if coordinator.zone_grouping == ZONE_GROUPING_SUBDEVICES:
        for valve in confirmed:
            if len(valve.model.zones) > 1:
                active.update(zone_device_id(valve, zone) for zone in valve.model.zones)

    owners = {f"{valve.device_id}_zone_": valve for valve in confirmed}
    if not owners:
        return

    try:
        dev_reg = dr.async_get(hass)
        ent_reg = er.async_get(hass)
        devices = list(dr.async_entries_for_config_entry(dev_reg, entry.entry_id))
    except Exception:
        _LOGGER.debug("Device registry unavailable during zone sub-device cleanup")
        return

    for device in devices:
        valve = next(
            (
                owner
                for domain, identifier in device.identifiers
                if domain == DOMAIN and identifier not in active
                for prefix, owner in owners.items()
                if identifier.startswith(prefix)
            ),
            None,
        )
        if valve is None:
            continue
        parent = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id, **valve_device_info(valve)
        )
        for row in er.async_entries_for_device(
            ent_reg, device.id, include_disabled_entities=True
        ):
            ent_reg.async_update_entity(row.entity_id, device_id=parent.id)
        dev_reg.async_remove_device(device.id)
        _LOGGER.info("Removed unused zone sub-device %s", device.name)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kohler Konnect from a config entry."""
    coordinator = KohlerKonnectCoordinator(hass, entry)
    # ⚠️ Registered before anything starts: Home Assistant never calls
    # `async_unload_entry` for a failed setup, but does run unload callbacks, so this
    # stops the MQTT thread, capture files and timers however setup ends. Without it
    # each `ConfigEntryNotReady` retry stacks another set. `async_shutdown_stream` is
    # safe on a partial start and when called twice.
    entry.async_on_unload(coordinator.async_shutdown_stream)

    async def _async_stop(_event: Event) -> None:
        # Home Assistant does not unload entries when it stops. May fire mid-setup;
        # see `KohlerKonnectCoordinator._raise_if_closed`.
        await coordinator.async_shutdown_stream()

    entry.async_on_unload(hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, _async_stop))

    # Includes the faucets' first reads, so the platforms below can build from them.
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()
    if coordinator.closed:
        # Home Assistant began stopping after `async_setup` returned; don't build
        # entities on a coordinator that has shut down.
        raise ConfigEntryNotReady("Home Assistant is stopping")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    # Covers a setup that fails from here on; a normal unload drops it itself.
    entry.async_on_unload(lambda: _async_forget_coordinator(hass, entry, coordinator))
    # Entities are built from the current layouts; a later layout read that differs
    # reloads the entry (see `_async_note_topology`).
    coordinator.entities_built = True
    _async_ensure_parent_valve_devices(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_cleanup_zone_subdevices(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    # Services are global and registration is idempotent, so this is safe per entry and
    # per reload.
    async_register_services(hass, coordinator)
    _async_flag_replaced_integrations(hass)

    @callback
    def _async_entry_changed(change: ConfigEntryChange, changed: ConfigEntry) -> None:
        # Clear the card as soon as the last replaced entry is deleted.
        if change is ConfigEntryChange.REMOVED and changed.domain in REPLACED_DOMAINS:
            _async_flag_replaced_integrations(hass)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_CONFIG_ENTRY_CHANGED, _async_entry_changed
        )
    )

    # No API product codes ("GCS", "HUB", "SEN") here: owners never see them anywhere.
    found = ", ".join(
        filter(
            None,
            (
                # Device names (and each valve's own layout). ⚠️ No device ids: they
                # are cloud addresses that `diagnostics.py` redacts, and this INFO line
                # ends up in shared logs.
                *(
                    f"{v.name} ({v.model.sku}, {v.model.total_outlets} outlets)"
                    for v in coordinator.valves
                ),
                *(c.name for c in coordinator.controllers),
                *(f"{f.device_name} (faucet)" for f in coordinator.faucets),
            ),
        )
    )
    _LOGGER.info("Kohler Konnect ready (%s)", found or "no devices")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
        # ⚠️ Shut down first, then forget. Shutdown can store a rotated token, which
        # fires `_async_update_listener`; that treats "no coordinator" as "reload".
        await coordinator.async_shutdown_stream()
        _async_forget_coordinator(hass, entry, coordinator)
    return unload_ok


@callback
def _async_forget_coordinator(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: KohlerKonnectCoordinator
) -> None:
    """Drop the entry's coordinator from `hass.data`, if it is still the one there."""
    entries: dict[str, KohlerKonnectCoordinator] | None = hass.data.get(DOMAIN)
    if entries is None or entries.get(entry.entry_id) is not coordinator:
        return
    entries.pop(entry.entry_id)
    if not entries:
        hass.data.pop(DOMAIN)
        # Services are shared, so only once the last entry is gone.
        async_unregister_services(hass)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a device only once it has left the Kohler account.

    Refused for devices the account still lists (they would be recreated), for zone
    sub-devices of such a valve (see `_async_cleanup_zone_subdevices`), and whenever the
    entry is not loaded.
    """
    coordinator: KohlerKonnectCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        return False
    # Case-insensitive, matching how faucet messages are matched.
    present = {
        kohler_id.lower()
        for kohler_id in (
            *(valve.device_id for valve in coordinator.valves),
            *(controller.device_id for controller in coordinator.controllers),
            *(faucet.device_id for faucet in coordinator.faucets),
        )
    }
    return not any(
        domain == DOMAIN and identifier.split("_zone_")[0].lower() in present
        for domain, identifier in device.identifiers
    )


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up after a deleted entry, on Kohler's side and on disk.

    Unregisters the MQTT stream's mobile device from the account, as the Konnect app does
    on sign-out (best effort), and removes the faucet store and this entry's Repairs cards.
    """
    identity = entry.data.get(CONF_MOBILE_DEVICE_ID)
    token = entry.data.get(CONF_REFRESH_TOKEN)
    if identity and token:
        session = async_get_clientsession(hass)
        client = KohlerClient(
            session, KohlerAuth(session, token), entry.data.get(CONF_TENANT_ID)
        )
        try:
            async with asyncio.timeout(UNREGISTER_TIMEOUT):
                await client.async_unregister_mobile_device(identity)
        except (AuthError, KohlerError, TimeoutError) as err:
            _LOGGER.debug("Could not remove the MQTT registration: %s", err)
    await Store(
        hass, FAUCET_STORAGE_VERSION, FAUCET_STORAGE_KEY.format(entry_id=entry.entry_id)
    ).async_remove()
    # Repairs cards are stored apart from the entry. Devices are still registered here;
    # Home Assistant removes them after this returns.
    device_ids = {
        identifier
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        for domain, identifier in device.identifiers
        if domain == DOMAIN
    }
    last_entry = not any(
        other.entry_id != entry.entry_id
        for other in hass.config_entries.async_entries(DOMAIN)
    )
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain != DOMAIN:
            continue
        if any(
            issue_id.endswith(f"_{device_issue_key(device_id)}")
            for device_id in device_ids
        ) or (last_entry and issue_id == ISSUE_REPLACED_INTEGRATION):
            ir.async_delete_issue(hass, DOMAIN, issue_id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only when the entry changed in a way that needs one.

    ⚠️ Bookkeeping writes (e.g. the rotated refresh token) must never reload. Compares
    against ``coordinator.reload_signature``, the snapshot taken at setup, never against
    ``coordinator.entry`` (the same mutated object); see `konnect.entry_reload`.
    """
    coordinator: KohlerKonnectCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is not None and entry_reload_signature(entry) == (
        coordinator.reload_signature
    ):
        return
    await hass.config_entries.async_reload(entry.entry_id)
