"""The Kohler Konnect integration.

Every Kohler Konnect device this integration knows, on one account, under one sign-in — any
number of each, every one its own device:

* **Anthem** (SKU ``GCS``) — the digital valve with built-in Wi-Fi. Full outlet,
  temperature, and flow control.
* **Anthem Plus** (SKU ``HUB``) — the Linux system controller that adds music, lighting,
  and steam. Controlled through favorites.
* **Sensate** and its sibling (SKUs ``SEN``, ``SET``) — kitchen faucets: water on and off,
  measured dispenses, presets, leak alerts.

One MQTT connection over Azure IoT Hub carries every device's messages. Shower state is
push-only — REST is read on events: once at setup, while the stream connects, and again on
every MQTT reconnect, because the broker replays nothing on connect. Faucets poll as well,
on their own coordinator, relaxing once the stream has proven it reports them. All
protocol handling lives in the bundled ``konnect`` package, which has no Home Assistant
imports and can be tested offline.

This replaced two integrations, ``kohler_anthem`` (showers) and ``kohler_sensate``
(faucets), which signed in, connected and read usage separately for the same account.
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
    """Point out entries of the two integrations this one replaced, in Repairs.

    Their entities would sit beside this integration's as duplicates for the same
    hardware, and a second sign-in to the same account keeps a second MQTT connection open.
    Removing them is the owner's call — they may want to move automations over first — so
    this only says so. The card goes once they are gone.
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

    Each valve keeps its registry id, which is how a zone device names its parent on Home
    Assistant 2026.10 and later; see `registry.parent_link`.
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

    Removing a device also removes every entity still attached to it, and an entity the
    platforms did not add this time is still attached: a **disabled** one is never added,
    so it never moves back to the valve. Deleting the device outright therefore deleted
    every disabled per-zone entity — the Hex sensors by default, and anything the owner
    had turned off, which then came back enabled and lost its name. So each zone device's
    remaining entities are moved onto the valve's own device first.

    ⚠️ **Only for a valve whose own settings stated its layout this boot** (2026-10-16).
    When that read fails the valve runs on a fallback layout — the entry's — and a
    two-zone valve on a single-zone entry looked as if its zones had gone: both zone
    devices were deleted, and with them the area and name the owner had given each. A
    valve still waiting on its read keeps its zone devices until a boot that has it.
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
    # **Everything `async_setup` starts must be unwound however setup ends** — including a
    # failure inside it, once the stream is up, and one after it, in the platforms. By
    # then the MQTT stream is connecting, any capture file switched on is open, and every
    # valve has armed its cloud-watch timers, and Home Assistant never calls
    # `async_unload_entry` for an entry that failed to load. Left alone that strands a paho
    # network thread with its own reconnect loop, the open files, and timers that fire into
    # a dead coordinator; and because `ConfigEntryNotReady` is retried, each attempt stacks
    # another set. Home Assistant does run an entry's unload callbacks when its setup fails,
    # so registering the shutdown here, before anything has started, covers every failure
    # from this line on — until 2026-10-16 only the first two calls below were covered.
    # `async_shutdown_stream` copes with whatever was never started, and with being called
    # again by `async_unload_entry`.
    entry.async_on_unload(coordinator.async_shutdown_stream)

    async def _async_stop(_event: Event) -> None:
        # Home Assistant does not unload entries when it stops, so without this the stream
        # kept dispatching into entities through the whole shutdown. It can fire while the
        # setup below is still running, which then stops starting things and gives up —
        # see `KohlerKonnectCoordinator._raise_if_closed`.
        await coordinator.async_shutdown_stream()

    entry.async_on_unload(hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, _async_stop))

    # The faucets' first reads run inside `async_setup` now, alongside the showers' seed;
    # it returns once they have, so the platforms below still build from them.
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()
    if coordinator.closed:
        # Home Assistant began stopping after `async_setup` had finished — it checks for
        # itself before that. Nothing is left running; building entities now would only
        # hang them on a coordinator that has shut down.
        raise ConfigEntryNotReady("Home Assistant is stopping")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    # Dropped again by `async_unload_entry`; this covers a setup that fails from here on,
    # which would otherwise leave the dead coordinator for services to find.
    entry.async_on_unload(lambda: _async_forget_coordinator(hass, entry, coordinator))
    # From here on entities exist, built from each valve's and controller's layout as it
    # stands — a layout read later that differs reloads the entry; see
    # `_async_note_topology`.
    coordinator.entities_built = True
    _async_ensure_parent_valve_devices(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_cleanup_zone_subdevices(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    # Services are global, not per entry — `async_register_services` is idempotent so this
    # is safe on every entry and every reload. Each action is registered only where a
    # device can use it; with several devices of a kind they take a `device_id`.
    async_register_services(hass, coordinator)
    _async_flag_replaced_integrations(hass)

    @callback
    def _async_entry_changed(change: ConfigEntryChange, changed: ConfigEntry) -> None:
        # So the card goes the moment the last old entry is deleted, not at a restart.
        if change is ConfigEntryChange.REMOVED and changed.domain in REPLACED_DOMAINS:
            _async_flag_replaced_integrations(hass)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_CONFIG_ENTRY_CHANGED, _async_entry_changed
        )
    )

    # Deliberately no "GCS"/"HUB"/"SEN" here: those strings exist only inside Kohler's API
    # and appear nowhere the owner can see them — not the app, the manual, or the hardware.
    found = ", ".join(
        filter(
            None,
            (
                # Every device, each by the name its device page will carry — and, for a
                # valve, the layout it decodes with, which is its own rather than the entry's.
                #
                # **No device ids here.** They are cloud addresses, this line is INFO, and
                # `home-assistant.log` is what people attach to issues — so printing them
                # here handed over exactly what `diagnostics.py` goes to length to redact.
                # The name and SKU identify the device to its owner, which is all this line
                # is for; anyone needing the id has diagnostics, where it is labelled.
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
        # **Shut down first, then forget** (2026-10-16). Shutting down waits for a token
        # refresh in flight, which stores the rotated token in the entry — and that write
        # fires `_async_update_listener`. With the coordinator already forgotten, the
        # listener read "no coordinator" as "reload": an unload set the entry up again, a
        # reload ran twice, and a removal raised. Still known, the write is just a token
        # change, which reloads nothing.
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
        # Only once the last entry is gone: the services are shared, so removing them
        # while another entry is still loaded would break it.
        async_unregister_services(hass)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Offer Delete on a device page only for a device that has left the Kohler account.

    Without this, a valve, controller or faucet removed in the Konnect app stayed in Home
    Assistant with no way to delete it short of removing the whole entry. A device the
    account still lists is refused — it would only be recreated at the next start — and
    so is a zone sub-device of a valve still on it, which setup manages on its own (see
    `_async_cleanup_zone_subdevices`). With the entry not loaded nothing can be checked,
    so nothing is deleted.
    """
    coordinator: KohlerKonnectCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        return False
    # Lowercase throughout: faucet messages are matched without regard to case, so their
    # ids are compared the same way here.
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

    The MQTT stream's registration is removed from the Kohler account, as the Konnect app
    does on sign-out, so the account does not keep a phantom "phone" nothing will connect as
    again. Best effort: a failure here leaves only that registration behind.
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
    # Repairs cards are per device and are stored apart from the entry, so they would
    # outlive it. The devices are still registered at this point; Home Assistant clears
    # them after this returns.
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

    This integration writes to its own config entry while running — above all the rotating
    refresh token, whenever B2C issues a new one. Every one of those writes fires this
    listener. Reloading on them would flap all entities to ``unavailable`` and drop the MQTT
    connection with its warm-up.

    So the decision is a comparison against ``coordinator.reload_signature``, the frozen
    snapshot taken when the coordinator was built. ``RELOAD_IGNORED_DATA_KEYS`` and
    ``RELOAD_IGNORED_OPTION_KEYS`` in ``const.py`` say what is excluded and why; anything
    else — including a key nobody anticipated — reloads.

    ⚠️ **Do not compare against ``coordinator.entry``.** That is the same object Home
    Assistant mutates in place, so it always equals ``entry`` and this listener becomes dead
    code that returns early every time. That was the defect here until 2026-08-17; see
    ``konnect/entry_reload.py``.
    """
    coordinator: KohlerKonnectCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is not None and entry_reload_signature(entry) == (
        coordinator.reload_signature
    ):
        return
    await hass.config_entries.async_reload(entry.entry_id)
