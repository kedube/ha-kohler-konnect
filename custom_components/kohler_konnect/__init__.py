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
push-only — REST is read on events: once at setup and again on every MQTT (re)connect,
because the broker replays nothing on connect. Faucets poll as well, on their own
coordinator, relaxing once the stream has proven it reports them. All protocol handling
lives in the bundled ``konnect`` package, which has no Home Assistant imports and can be
tested offline.

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
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
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
    """
    active: set[str] = set()
    if coordinator.zone_grouping == ZONE_GROUPING_SUBDEVICES:
        for valve in coordinator.valves:
            if len(valve.model.zones) > 1:
                active.update(zone_device_id(valve, zone) for zone in valve.model.zones)

    owners = {f"{valve.device_id}_zone_": valve for valve in coordinator.valves}
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
    # **Everything `async_setup` starts must be unwound on failure** — including a failure
    # inside it, once the stream is up. By then the MQTT stream is connecting, any capture
    # file switched on is open, and every valve has armed its cloud-watch timers — but the
    # coordinator is not yet in `hass.data`, so a raise here means Home Assistant discards
    # it without ever calling `async_unload_entry`. Left alone that strands a paho network
    # thread with its own reconnect loop, the open files, and timers that fire into a dead
    # coordinator; and because `ConfigEntryNotReady` is retried, each attempt stacks
    # another set. `async_shutdown_stream` copes with whatever was never started.
    try:
        await coordinator.async_setup()
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_shutdown_stream()
        raise

    # Each faucet's first read, before the platforms build their entities from it. Not a
    # first refresh that fails the entry: a faucet Kohler cannot answer for right now
    # should not take the showers down with it. Its entities show unavailable, and it
    # retries on its own clock.
    if coordinator.faucets:
        await asyncio.gather(
            *(faucet.async_refresh() for faucet in coordinator.faucets)
        )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
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
        coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown_stream()
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
            # Only once the last entry is gone: the services are shared, so removing them
            # while another entry is still loaded would break it.
            async_unregister_services(hass)
    return unload_ok


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
