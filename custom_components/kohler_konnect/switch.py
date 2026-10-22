"""Switches for the Anthem valve and the Anthem Plus controller.

Valve outlet switches show state and accept commands. ``is_on`` reads the valve's
reported state, so a switch follows changes from any origin: app, touchscreen, preset or
automation.

Outlets are addressed per zone, matching the hardware: a multi-outlet Anthem is two
valve bodies, every API surface addresses them separately, and a global numbering would
need a per-model split (2+2 on a K-28211, 3+3 on a K-28212) that could operate the wrong
outlet.

Turning one on re-sends the full valve command with that outlet's bit set and every
other outlet in both zones kept, at the zone's current temperature and flow; the valve
accepts no partial write. In a paused zone, on opens only that outlet and off drops it
from the assignment the pause would resume to (`Valve.async_set_zone_outlet`). During a
warm-up the outlets kept are the shower's, not the warm-up's, and a toggle is refused
when that shower was started elsewhere (`Valve._warmup_shower`).

Switches are optimistic (`OptimisticSwitchMixin`), so a tap moves at once rather than
waiting a second or two for the device's echo.

No ``PARALLEL_UPDATES``: a platform-wide semaphore would queue every switch on the
account behind one slow valve POST and still not cover the services or the custom-shower
watcher. Valve writes are serialised per valve in `Valve.async_apply_valve`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_WARMUP_AUTO_RESTORE,
    DOMAIN,
    OPTIMISTIC_GRACE_SECONDS,
    OUTLET_TYPE_NAMES,
    OUTLET_TYPE_VARIANTS,
    WARMUP_AUTO_RESTORE_DELAY_SECONDS,
    WARMUP_AUTO_RESTORE_NO_TARGET,
    WARMUP_AUTO_RESTORE_ON,
)
from .coordinator import Controller, KohlerKonnectCoordinator, Valve
from .entity import (
    KohlerControllerEntity,
    KohlerValveEntity,
    outlet_name,
    outlet_unique_id,
)
from .faucet.switch import faucet_switches

_LOGGER = logging.getLogger(__name__)


@callback
def _async_purge_valve_only_warmup_restore(
    hass: HomeAssistant, entry: ConfigEntry, valve: Valve
) -> None:
    """Remove a stale `Warmup Auto-Restore` from a valve on a controller-free account.

    Matched on this valve's own device id. Cleanup only, so registry trouble is logged
    and skipped rather than failing setup (and a test double needs no registry).
    """
    stale = f"{valve.device_id}_warmup_auto_restore"
    try:
        registry = er.async_get(hass)
        rows = list(er.async_entries_for_config_entry(registry, entry.entry_id))
    except Exception:  # Cosmetic cleanup; never worth failing setup over.
        _LOGGER.debug("Entity registry unavailable; leaving %s alone", stale)
        return
    for row in rows:
        if row.unique_id == stale:
            registry.async_remove(row.entity_id)
            _LOGGER.info(
                "Removed %s: the warmup revert it guards against is caused by an "
                "Anthem Plus controller, and this account has none",
                row.entity_id,
            )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a switch per outlet, per zone, when the account has a valve."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []

    # One set per valve. An Anthem Plus controller has no per-outlet command: outlets are
    # chosen by activating a favorite, so a controller-only account gets no outlet
    # switches.
    for valve in coordinator.valves:
        model = valve.model
        entities.append(ShowerSwitch(coordinator, valve))
        # Only with a controller: its web UI is the one known cause of a spontaneous
        # `warmUpDisabled`. Without one the switch would only add an unprompted writer.
        # Per account, not per valve: which valve a controller fronts can't be told from
        # the cloud, so a standalone valve on a mixed account gets it too (off by default).
        if coordinator.controllers:
            entities.append(WarmupAutoRestoreSwitch(coordinator, valve))
        else:
            _async_purge_valve_only_warmup_restore(hass, entry, valve)
        entities.append(ValveReportLogSwitch(coordinator, valve))
        entities.extend(
            ZoneOutletSwitch(coordinator, valve, zone, outlet)
            for zone in model.zones
            for outlet in range(1, model.outlets_in_zone(zone) + 1)
        )
    # One set per controller — each is its own device with its own command surface.
    for controller in coordinator.controllers:
        entities += [
            HubShowerSwitch(coordinator, controller),
            HubSystemSwitch(coordinator, controller),
            ControllerReportLogSwitch(coordinator, controller),
        ]
        # Only with a steam generator, the same `parts` gate as the Steam binary sensor.
        if controller.capabilities.steam:
            entities.append(HubSteamSwitch(coordinator, controller))

    # Each faucet is its own device, with its own coordinator.
    for faucet in coordinator.faucets:
        entities += faucet_switches(faucet)
    async_add_entities(entities)


class OptimisticSwitchMixin:
    """Hold a just-commanded position until the device agrees, or the grace runs out.

    The coordinator pushes an update for every message on the account, so the guess
    clears only when the device reports the position asked for, or when
    `OPTIMISTIC_GRACE_SECONDS` expires and the device's own position shows. The grace
    bounds how long a device that did something else is misreported. A subclass supplies
    `_device_is_on`; this supplies `is_on`. `select.OptimisticOptionMixin` follows the
    same rule.

    ⚠️ Never clear on "this device sent something": devices send unrelated messages
    (`MUSIC_STS`, `GCS_RECIEVED_STS`, outlet-config records) before the answer, and the
    switch would snap back.
    """

    _optimistic: bool | None = None
    _optimistic_cancel = None

    @property
    def _device_is_on(self) -> bool | None:
        """What the device itself reports, ignoring a command not yet confirmed."""
        raise NotImplementedError

    @property
    def is_on(self) -> bool | None:
        if self._optimistic is not None:
            return self._optimistic
        return self._device_is_on

    async def _async_switch(self, target: bool, action: Awaitable[Any]) -> None:
        """Show ``target`` at once, send ``action``, and hold it until the device agrees.

        ⚠️ Any exit but success clears the guess, cancellation included
        (`CancelledError` is not an `Exception`): a guess left behind stands until the
        device agrees, so a cancelled Stop would show the shower off while water runs.
        """
        self._cancel_optimistic_timer()
        self._optimistic = target
        self.async_write_ha_state()
        try:
            await action
        except BaseException:
            # Failed or cancelled: show the device's own report again.
            self._clear_optimistic()
            raise
        self._cancel_optimistic_timer()

        @callback
        def _expire(_now) -> None:
            self._optimistic_cancel = None
            self._clear_optimistic()

        self._optimistic_cancel = async_call_later(
            self.hass, OPTIMISTIC_GRACE_SECONDS, _expire
        )

    def _clear_optimistic(self) -> None:
        self._cancel_optimistic_timer()
        if self._optimistic is None:
            return
        self._optimistic = None
        self.async_write_ha_state()

    def _cancel_optimistic_timer(self) -> None:
        if self._optimistic_cancel is not None:
            self._optimistic_cancel()
            self._optimistic_cancel = None

    @callback
    def _handle_coordinator_update(self) -> None:
        # Clear only when the device reports what was asked for; anything else is left
        # to the grace timer.
        if self._optimistic is not None and self._device_is_on == self._optimistic:
            self._cancel_optimistic_timer()
            self._optimistic = None
        super()._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_optimistic_timer()
        await super().async_will_remove_from_hass()


class ShowerSwitch(OptimisticSwitchMixin, KohlerValveEntity, SwitchEntity):
    """Whole-shower start and stop, as ``switch.anthem_valve_shower_on``.

    **On** runs the default shower: `Valve.async_start_default_shower` sends preset 1's
    stored outlets, temperature and flow as one ``solowritesystem``, as the touchscreen
    and the app's Simple Start do. It never activates or writes preset 1, so Preset
    Active stays off and only the outlets' ``maximumRunTime`` ends it. What it starts is
    set in the Konnect app (`docs/protocol/gcs_valve.md` §3.4).

    **Off** sends mask ``0x00`` on both zones, keeping each zone's temperature, rather
    than a ``0x40`` pause that could be mistaken for the run-time cutoff. It supersedes
    commands queued before it; see `Valve.async_stop_shower`.

    Unlike an outlet switch, which opens one outlet at the zone's current settings, this
    runs the whole configured shower in one command.
    """

    _attr_icon = "mdi:shower"
    # Not plain "Shower": beside outlet switches named after fixtures, that reads as one
    # more outlet rather than the whole-shower control.
    _attr_name = "Shower on"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_shower"

    @property
    def _device_is_on(self) -> bool | None:
        """True while water is actually flowing from any outlet, in either zone."""
        state = self._state
        if state is None or state.valve1 is None:
            return None
        return state.is_running

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state
        if state is None or state.valve1 is None:
            return {}
        return {"paused": state.is_paused}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Run the default shower — one valve write, from preset 1's stored words."""
        await self._async_switch(True, self._valve.async_start_default_shower())

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop with mask 0x00 on both zones."""
        await self._async_switch(False, self._valve.async_stop_shower())


class WarmupAutoRestoreSwitch(KohlerValveEntity, SwitchEntity):
    """Put the warmup mode back when something outside Home Assistant turns it off.

    An Anthem Plus controller's web UI writes ``warmUpDisabled`` on every signed-in use,
    from its own firmware, and nothing outside it can prevent that
    (`docs/protocol/gcs_valve.md` §5), so this undoes it.

    When warmup goes to ``warmUpDisabled`` without this integration causing it (reported
    over MQTT, or found by the reseed after a reconnect), it waits
    `WARMUP_AUTO_RESTORE_DELAY_SECONDS`, checks it is still disabled, and restores the
    last enabled mode seen on the valve. That target is kept in the entry options so it
    survives a restart; never a default, since "all" and "selected" outlets are
    different amounts of water. With no target it does nothing and says so.

    It will not undo an ``Off`` chosen in Home Assistant, will stop after five
    consecutive restores that don't stick, and never writes while water runs. Its on/off
    setting lives in the entry options, read live, so a toggle needs no restart.
    Behaviour lives in `warmup_manager.py`; see `docs/user_guide.md` (Warmup
    auto-restore).
    """

    _attr_name = "Warmup Auto-Restore"
    _attr_icon = "mdi:restore-alert"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Off by default: a workaround for a controller fault, and an owner who has never
    # seen the mode revert does not need another writer to the valve.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_warmup_auto_restore"

    @property
    def available(self) -> bool:
        """A setting, not a reading — usable before any valve state has arrived."""
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        return self._valve.warmup_auto_restore

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What it would restore to, and how long it waits."""
        return {
            "restores_to": self._valve.last_warmup_mode,
            "delay_seconds": WARMUP_AUTO_RESTORE_DELAY_SECONDS,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        self._valve.set_option(CONF_WARMUP_AUTO_RESTORE, value)
        if not value:
            _LOGGER.info("Warmup Auto-Restore disabled")
        elif self._valve.last_warmup_mode is None:
            # On but inert, which is indistinguishable from broken unless it says so.
            _LOGGER.warning(WARMUP_AUTO_RESTORE_NO_TARGET)
        else:
            _LOGGER.warning(
                WARMUP_AUTO_RESTORE_ON,
                self._valve.last_warmup_mode,
                WARMUP_AUTO_RESTORE_DELAY_SECONDS,
            )
        self.async_write_ha_state()


class ZoneOutletSwitch(OptimisticSwitchMixin, KohlerValveEntity, SwitchEntity):
    """One outlet within one zone, readable and controllable.

    Named after its fixture (``Rainhead``), or its position (``Outlet 1.3``) until the
    fixture is known — see ``entity.outlet_name``. The entity id is composed from the device
    name and that name at first registration, as ``switch.anthem_valve_rainhead``, and the
    unique id is the position — see ``entity.outlet_unique_id``.
    """

    _attr_icon = "mdi:shower-head"

    def __init__(
        self,
        coordinator: KohlerKonnectCoordinator,
        valve: Valve,
        zone: int,
        outlet: int,
    ) -> None:
        super().__init__(coordinator, valve, zone=zone)
        self._zone = zone
        self._outlet = outlet
        self._attr_name = outlet_name(valve, zone, outlet)
        # By position, not name — see `entity.outlet_unique_id`.
        self._attr_unique_id = outlet_unique_id(valve, zone, outlet)

    @property
    def _outlet_exists(self) -> bool:
        """Whether the valve's current layout still has this outlet.

        `Valve._apply_topology` can shrink the model after setup; until the entry
        reloads this reads unavailable rather than index past the zone, since one
        listener that raises stops every listener after it.
        """
        return self._outlet <= self._valve.model.outlets_in_zone(self._zone)

    @property
    def available(self) -> bool:
        return super().available and self._outlet_exists

    @property
    def _device_is_on(self) -> bool | None:
        state = self._state
        if (
            state is None
            or not self._outlet_exists
            or state.zone_word(self._zone) is None
        ):
            return None
        return state.zone_outlets(self._zone)[self._outlet - 1]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The assignment a paused session will resume to.

        A paused valve keeps its outlet bits set while no water flows, so ``is_on`` is off
        and this stays on — the difference between "not running" and "not selected".
        """
        state = self._state
        if (
            state is None
            or not self._outlet_exists
            or state.zone_word(self._zone) is None
        ):
            return {}
        assigned = state.zone_outlets(self._zone, flowing=False)
        attributes: dict[str, Any] = {"assigned": assigned[self._outlet - 1]}
        # The valve's type code for this outlet (`docs/protocol/gcs_valve.md` §1.2), the
        # only per-outlet identity it reports. Absent until the valve announces the
        # outlet; those records arrive one at a time, unprompted.
        outlet_type = self._outlet_type(state)
        if outlet_type is not None:
            attributes["outlet_type"] = outlet_type
            # Named from the app's table (`OUTLET_TYPE_NAMES`); unknown codes get none.
            name = OUTLET_TYPE_NAMES.get(outlet_type)
            if name is not None:
                attributes["outlet_type_name"] = name
            variant = OUTLET_TYPE_VARIANTS.get(outlet_type)
            if variant is not None:
                attributes["outlet_variant"] = variant
        return attributes

    def _outlet_type(self, state: Any) -> int | None:
        """This outlet's type code, looked up by the valve's flat 0-based `outLetId`.

        `outlet_limits` is keyed by that id; `ValveModel.outlet_id` converts from this
        entity's per-zone address.
        """
        flat = self._valve.model.outlet_id(self._zone, self._outlet)
        limits = state.outlet_limits.get(flat)
        return None if limits is None else limits.outlet_type

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, target: bool) -> None:
        # No flow passed: the write takes the zone's `zone_flow` when it is built, so a
        # flow change queued ahead is not undone. See `Valve.async_set_zone_outlet`.
        await self._async_switch(
            target, self._valve.async_set_zone_outlet(self._zone, self._outlet, target)
        )


class HubShowerSwitch(OptimisticSwitchMixin, KohlerControllerEntity, SwitchEntity):
    """The controller's own shower, via ``valvecontrol {valveOnOff}``.

    A bare on/off: the controller stores its own default water configuration, so "on"
    needs no scene. Off stops water only; the System switch stops everything.

    It reports the controller's view (`Controller.water_is_running`). A shower driven
    straight at the valve, as every Home Assistant shower is, shows here only if the
    controller registers it, which is unreliable (`docs/protocol/hub_controller.md` §5);
    its ``valvecontrol OFF`` can stop only a session the controller knows of. The Anthem
    Valve device answers whether water is running.
    """

    _attr_name = "Shower"
    _attr_icon = "mdi:shower"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_shower"

    @property
    def _device_is_on(self) -> bool | None:
        return self._controller.water_is_running

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_switch(
            True, self.coordinator.async_set_hub_shower(self._controller, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_switch(
            False, self.coordinator.async_set_hub_shower(self._controller, False)
        )


class HubSystemSwitch(OptimisticSwitchMixin, KohlerControllerEntity, SwitchEntity):
    """Whether anything the controller knows of is running, and the stop for all of it.

    On when any subsystem is active (water, music, steam, lighting); the attributes name
    which. All four are the controller's view, water included: a valve-driven shower is
    invisible here, and ``stopall`` would not stop it either, so the switch covers what
    its off action reaches.

    Deliberately asymmetric: Off sends ``stopall``, the only command that idles
    everything, but there is no "start everything", so On runs the controller's default
    shower (``valvecontrol ON``).
    """

    _attr_name = "System"
    _attr_icon = "mdi:power"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_system"

    @property
    def _subsystems(self) -> dict[str, bool | None]:
        state = self._state
        if state is None:
            return {}
        return {
            # The controller's own outlet arrays, not the valve's; see
            # `Controller.water_is_running`.
            "water": self._controller.water_is_running,
            "music": state.music_on,
            "steam": state.steam_on,
            "light": state.light_on,
        }

    @property
    def _device_is_on(self) -> bool | None:
        subsystems = self._subsystems
        if not subsystems:
            return None
        return any(bool(v) for v in subsystems.values())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which subsystems are on.

        An accessory the controller lacks reads None, not False: it sends STEAM_STS and
        LIGHT_STS even for absent hardware, and False would imply an idle one.
        """
        return dict(self._subsystems)

    async def async_turn_on(self, **kwargs: Any) -> None:
        # Water only: there is no "start everything" command.
        await self._async_switch(
            True, self.coordinator.async_set_hub_shower(self._controller, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_switch(
            False, self.coordinator.async_stop_hub(self._controller)
        )


class HubSteamSwitch(OptimisticSwitchMixin, KohlerControllerEntity, SwitchEntity):
    """The controller's steam generator, at its own default temperature and time.

    ``steamcontrol {steamOnOff}``, as the app's "Steam start" card sends; the controller
    runs ``steamSettings.defaultTemperature`` for ``defaultTime``. Refused while the
    shower runs, as in the app (`KohlerKonnectCoordinator.async_set_hub_steam`).
    App-confirmed only.

    Reads ``STEAM_STS``; a generator self-cleaning (``POWERCLEAN``) reads off with
    ``power_clean`` true, since nobody should stand in it.
    """

    _attr_name = "Steam"
    _attr_icon = "mdi:weather-fog"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        # Not the Steam binary sensor's `_steam`: that is the reading, this the control.
        self._attr_unique_id = f"{self._device_id}_steam_control"

    @property
    def _device_is_on(self) -> bool | None:
        state = self._state
        return None if state is None else state.steam_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state
        settings = self._controller.settings
        attributes: dict[str, Any] = {
            "default_temperature": settings.steam_default_temperature,
            "default_time": settings.steam_default_time,
        }
        if state is not None:
            attributes["power_clean"] = state.steam_powerclean
        return attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_switch(
            True, self.coordinator.async_set_hub_steam(self._controller, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_switch(
            False, self.coordinator.async_set_hub_steam(self._controller, False)
        )


class _ReportLogSwitch(SwitchEntity):
    """Shared half of the Report Log switch: a raw MQTT capture for bug reports.

    One file per switch-on; a Home Assistant restart mid-capture appends to the same
    file. The valve and controller copies toggle the one capture and refresh together
    through `async_update_listeners`. Behaviour lives in `konnect/report_log.py`; see
    `docs/mqtt/capture_runbook.md`.

    Files go to `custom_components/kohler_konnect/reports/`; a HACS update or reinstall
    replaces that folder and deletes them.
    """

    _attr_name = "Report Log"
    _attr_icon = "mdi:record-rec"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """A tool, not a reading — usable before any device state has arrived."""
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        return self.coordinator.report_log_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Where the capture is going, visible without shell access."""
        log = self.coordinator.report_log
        path = log.path if log is not None else None
        return {
            "file": os.path.basename(path) if path else None,
            "folder": "custom_components/kohler_konnect/reports",
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_start_report_log()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_stop_report_log()


class ValveReportLogSwitch(_ReportLogSwitch, KohlerValveEntity):
    """The Report Log switch on the Anthem Valve device page."""

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_report_log"


class ControllerReportLogSwitch(_ReportLogSwitch, KohlerControllerEntity):
    """The Report Log switch on the Anthem Plus device page."""

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_report_log"
