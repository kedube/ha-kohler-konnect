"""Binary sensors for the valve, the Anthem Plus controller and faucets.

* **Valve** — faults, cloud reachability, stream health, at-temperature, and per-zone
  running state.
* **Controller** — faults, accessories, and the controller's own view of its outlets.

The controller does not reliably see a session driven through the valve's API
(`docs/protocol/hub_controller.md` §5), so wherever a valve exists its entities, not the
controller's, answer "is water running". See ``konnect.models.resolve_outlet_source``.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    OUTLET_TYPE_TUB_FILLER,
    ZONE_GROUPING_NUMBERED,
)
from .coordinator import Controller, KohlerKonnectCoordinator, Valve
from .entity import KohlerControllerEntity, KohlerValveEntity, zone_label
from .konnect.state import GcsPreset
from .mqtt_connection import MqttConnectionMixin
from .panel_settings import panel_entities
from .product_platforms import product_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up valve diagnostics, controller accessories and outlets, and faucets."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []

    # One set per valve — each is its own device with its own state and layout.
    for valve in coordinator.valves:
        entities += [
            ValveAtTemperatureSensor(coordinator, valve),
            ValveProblemSensor(coordinator, valve),
            MqttConnectionSensor(coordinator, valve),
            # On every valve: its quiet-interval trigger needs no controller, and the
            # contradiction trigger joins in where there is one (`cloud_watch.py`).
            ValveCloudConnectionSensor(coordinator, valve),
            ValvePresetActiveSensor(coordinator, valve),
            *(
                [ValveBathFillSensor(coordinator, valve)]
                if _has_tub_filler(valve)
                else []
            ),
            *panel_entities(coordinator, valve, "binary_sensor"),
        ]
        # Only on a multi-zone valve. With one zone, `System Status` already answers "is
        # it running", with warm-up and pause besides; with two, only these say which
        # zone runs, since pause and warm-up are system-level.
        if len(valve.model.zones) > 1:
            entities += [
                ValveZoneActiveSensor(coordinator, valve, zone)
                for zone in valve.model.zones
            ]

    # One set per controller, each its own device with its own state, accessories and
    # outlet layout. The outlet sensors exist beside a valve too; see
    # `ControllerOutletSensor` for how their answer differs from the valve's.
    for controller in coordinator.controllers:
        # The controller's copy of the stream-health diagnostic; see
        # `MqttConnectionMixin`.
        entities.append(ControllerMqttConnectionSensor(coordinator, controller))

        capabilities = controller.capabilities

        def attached(present: bool, known: bool = capabilities.known) -> bool:
            """Whether to create an accessory entity: ``not known or present``.

            An unread configuration leaves every capability False, so creating when it
            is not known turns a missed read into a sensor reading unknown, not a
            missing one.
            """
            return not known or present

        # Problem on every controller. Accessories are gated on
        # `hub-configuration.parts`, the only source of what is fitted: the controller
        # sends STEAM_STS and LIGHT_STS even for hardware it lacks
        # (`docs/protocol/hub_controller.md` §1).
        entities.append(ControllerProblemSensor(coordinator, controller))
        if attached(capabilities.music):
            entities.append(ControllerMusicSensor(coordinator, controller))
        if attached(capabilities.light):
            entities.append(ControllerLightSensor(coordinator, controller))
        if attached(capabilities.steam):
            entities.append(ControllerSteamSensor(coordinator, controller))

        # This controller's layout, not the entry's: a second bathroom need not have the
        # same valve model. See `Controller.model`.
        model = controller.model
        entities += [
            ControllerOutletSensor(coordinator, controller, zone, outlet)
            for zone in model.zones
            for outlet in range(1, model.outlets_in_zone(zone) + 1)
        ]

    # Every other product's, each device with its own coordinator.
    entities += product_entities(
        hass, entry, coordinator, Platform.BINARY_SENSOR, async_add_entities
    )
    async_add_entities(entities)


class ValveAtTemperatureSensor(KohlerValveEntity, BinarySensorEntity):
    """On once the valve reports reaching its temperature setpoint (``atTemp``).

    System-level: read from the primary valve word whichever zone delivers the water. It
    follows the touchscreen, which shows a solid setpoint once this is on, and lags
    briefly after a setpoint change.
    """

    _attr_name = "At Temperature"
    _attr_icon = "mdi:thermometer-check"
    # `HEAT` labels the states "Hot"/"Normal". The explicit icon keeps the thermometer,
    # which the device class would otherwise replace.
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_at_temperature"

    @property
    def is_on(self) -> bool | None:
        state = self._state
        return None if state is None else state.at_temperature


class ValveProblemSensor(KohlerValveEntity, BinarySensorEntity):
    """Whether the valve reports a fault, in its fault log or in its status word.

    Enabled and not a diagnostic: it is the one entity that says the hardware is
    unhappy, so it must show without anyone looking for it. On when any of these holds:

    * **Active faults** — entries in the ``gcs-diagnostics`` log that are ``isActive``
      with a real ``errorCode``, as the app lists them; published as ``active_faults``.
      Read on each seed and twice a day; a failed read keeps the faults already known.
    * **The word's error flag** on either zone's word (`GcsState.has_fault`). Byte 7,
      the error code, is not a trigger: it reads ``1`` on healthy hardware.
    * **``currentSystemState`` of ``error``**, which the app treats as a fault (folded
      into `has_fault`).

    ⚠️ The word's error flag has never been seen set on hardware, so its False is weak
    evidence; `fault_detection_verified` says so. The fault log is the source known to
    fire. See `docs/gcs/valve_hex.md` (byte 3) and `docs/protocol/platform.md` §8.
    """

    _attr_name = "Problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_problem"

    @property
    def is_on(self) -> bool | None:
        if self._valve.active_faults:
            return True
        state = self._state
        # Unknown, not "no problem", before the first word arrives.
        return None if state is None else state.has_fault

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self._state
        codes = {} if state is None else state.error_codes
        return {
            # Code, area, valve and timestamp; nothing that identifies the device.
            "active_faults": [dict(fault) for fault in self._valve.active_faults],
            # Per-zone byte 7. Not a fault indicator on its own; published so a real
            # fault can be characterised from a bug report.
            "error_codes": codes,
            # So it is clear which signal fired.
            "system_state": None if state is None else state.system_state,
            # The word's error flag has never fired on hardware. Does not cover the log.
            "fault_detection_verified": False,
        }


class ValveDiagnosticBinarySensor(KohlerValveEntity, BinarySensorEntity):
    """Base for valve diagnostics: hidden unless deliberately enabled."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False


class MqttConnectionSensor(MqttConnectionMixin, ValveDiagnosticBinarySensor):
    """Whether the MQTT stream that carries all state is connected.

    With no polling, push is the only way state reaches Home Assistant: while this is
    off, every entity is frozen. It reflects what the MQTT client believes, so a
    half-open socket reads connected until the 60 s keepalive fails; a stale
    ``sensor.anthem_valve_last_update`` alongside "connected" is the sign of that.
    """

    _attr_name = "MQTT Connection"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_mqtt_connection"


class ValveCloudConnectionSensor(KohlerValveEntity, BinarySensorEntity):
    """Whether Kohler's cloud can reach the valve, not whether we can reach Kohler.

    Answers "the Konnect app says my valve is offline": the valve can drop off Kohler's
    cloud and return only when power-cycled, while ``MQTT Connection`` stays on and
    every other valve entity just freezes. Not polled: push events decide when to read
    (`cloud_watch.py`).

    Enabled and visible, unlike the other valve diagnostics: its value is the record it
    keeps while nobody is looking, and it matters most when nobody wants to unhide it
    first.

    * **on** — the last successful read said ``Connected``, or the valve has sent a
      message since: a valve message comes through Kohler's cloud, so it proves
      reachability, and ``reachable_because`` says so until the next read.
    * **off** — the last successful read said otherwise; the exact value is in
      ``connection_state``.
    * **unknown** — no successful read yet. A failed read changes nothing: "we could not
      reach Kohler" is a different fault, reported in ``last_error``.
    """

    _attr_name = "Cloud Connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_cloud_connection"

    @property
    def is_on(self) -> bool | None:
        watch = self._valve.cloud_watch
        return None if watch is None else watch.connected

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """When it was last checked, why, and the verbatim result.

        ``checked_because`` weighs the result: a check from the contradiction trigger
        ran while a shower was running, much stronger evidence than one after a quiet
        night.
        """
        watch = self._valve.cloud_watch
        return {} if watch is None else watch.attributes


class ControllerDiagnosticBinarySensor(KohlerControllerEntity, BinarySensorEntity):
    """Base for controller diagnostics: hidden unless deliberately enabled."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False


class ControllerMqttConnectionSensor(
    MqttConnectionMixin, ControllerDiagnosticBinarySensor
):
    """The controller's copy of the MQTT-health diagnostic; see `MqttConnectionSensor`.

    Pair it with ``sensor.anthem_plus_last_update`` the same way.
    """

    _attr_name = "MQTT Connection"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_mqtt_connection"


class ValveZoneActiveSensor(KohlerValveEntity, BinarySensorEntity):
    """Whether this zone is delivering water. Only on a multi-zone valve.

    Named per :func:`zone_label`, e.g. `Shower Active 2`. Not a diagnostic: which of two
    showers is running is primary state.

    "Active" means flowing: outlet bits set and not paused, as the valve's run-time
    timer reads it. A paused zone keeps its assignment in byte 3 (``0x41`` is paused
    with outlet 1 assigned) but delivers no water.
    """

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:water"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, valve: Valve, zone: int
    ) -> None:
        super().__init__(coordinator, valve, zone=zone)
        self._zone = zone
        grouping = valve.zone_grouping
        base = "Shower Active" if grouping == ZONE_GROUPING_NUMBERED else "Zone Active"
        self._attr_name = zone_label(valve, zone, base)
        self._attr_unique_id = f"{self._device_id}_zone_{zone}_active"

    @property
    def _word(self):
        state = self._state
        return None if state is None else state.zone_word(self._zone)

    @property
    def is_on(self) -> bool | None:
        word = self._word
        if word is None:
            return None
        return bool(word.outlet_mask) and not word.paused

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Why it reads as it does, and how close the run-time cutoff is.

        `seconds_remaining` is None when the limit is unknown, the zone is idle, or
        after a reconnect: zone clocks are dropped across a gap rather than guessed.
        """
        word = self._word
        if word is None:
            return {}
        model = self._valve.model
        base = sum(model.outlets_in_zone(z) for z in model.zones if z < self._zone)
        attributes: dict[str, object] = {
            "outlet_mask": f"0x{word.outlet_mask:02X}",
            # The outlets this zone would resume to; present while paused too.
            "assigned_outlets": [
                base + bit + 1
                for bit in range(model.outlets_in_zone(self._zone))
                if word.outlet(bit)
            ],
            "paused": word.paused,
        }
        flowing_for = self._valve.zone_flowing_for(self._zone)
        attributes["flowing_for_seconds"] = (
            None if flowing_for is None else round(flowing_for, 1)
        )
        limits = self._valve.run_time_limits_for_zone(self._zone)
        attributes["run_time_limit_seconds"] = list(limits)
        attributes["seconds_remaining"] = (
            None
            if flowing_for is None or not limits
            else round(min(limits) - flowing_for, 1)
        )
        return attributes


class ValveBathFillSensor(KohlerValveEntity, BinarySensorEntity):
    """Whether a bath-fill preset is filling the tub: water runs and the preset driving
    the valve has a fill amount, as the app shows "Bathfill is running"
    (`docs/protocol/gcs_valve.md` §8). Only on a valve with a tub filler."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_name = "Bath Filling"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_bath_filling"

    @property
    def _preset(self) -> GcsPreset | None:
        state = self._state
        if state is None or state.active_preset_id is None:
            return None
        return state.presets.get(state.active_preset_id)

    @property
    def is_on(self) -> bool | None:
        state = self._state
        if state is None:
            return None
        preset = self._preset
        return state.is_running and preset is not None and preset.is_bath_fill

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        preset = self._preset if self.is_on else None
        return {
            "preset_name": None if preset is None else preset.name,
            "fill_litres": None if preset is None else preset.volume,
        }


def _has_tub_filler(valve: Valve) -> bool:
    limits = valve.gcs_state.outlet_limits.values()
    return any(limit.outlet_type == OUTLET_TYPE_TUB_FILLER for limit in limits)


class ValvePresetActiveSensor(KohlerValveEntity, BinarySensorEntity):
    """Whether a stored preset or experience is driving the valve.

    From ``presetOrExperienceId``. It answers "is a preset driving this", never "is
    water running":

    * it latches for the session: temperature, flow or outlet changes do not clear it;
    * pause and stop both clear it;
    * opening outlets directly leaves it off with water flowing. That includes the
      Shower switch, which sends preset 1's words as a plain valve write, as Simple
      Start does.

    A run-time cutoff in a preset-driven session pauses every zone the preset owns
    (`docs/protocol/gcs_valve.md` §4.1).

    ⚠️ A preset started during warm-up never sets the id, so this reads off while the
    preset is in effect, and stays off because warm-up ending ends the session. That is
    the device's behaviour, and this does not guess around it
    (`docs/protocol/gcs_valve.md` §7).
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Preset Active"
    _attr_icon = "mdi:playlist-star"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_preset_active"

    @property
    def is_on(self) -> bool | None:
        state = self._state
        return None if state is None else state.active_preset_id is not None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self._state
        if state is None:
            return {}
        preset_id = state.active_preset_id
        preset = state.presets.get(preset_id) if preset_id is not None else None
        return {
            "preset_id": preset_id,
            # None when no stored preset matches the id: an experience run from the
            # controller shares the id space without appearing in `presets`.
            "preset_name": preset.name if preset else None,
        }


class ControllerAccessorySensor(KohlerControllerEntity, BinarySensorEntity):
    """Base for the controller's accessory on/off sensors.

    Each is created only when ``hub-configuration.parts`` reports the hardware, and
    reads the on/off state the controller pushes for it, whatever drives the water.
    There is no volume entity: REST's ``amplifierSettings.monoVolume`` lags a change
    made at the touchscreen.
    """

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller, key: str
    ) -> None:
        super().__init__(coordinator, controller)
        self._key = key
        self._attr_unique_id = f"{self._device_id}_{key}"

    @property
    def is_on(self) -> bool | None:
        state = self._state
        return None if state is None else getattr(state, f"{self._key}_on")


class ControllerProblemSensor(KohlerControllerEntity, BinarySensorEntity):
    """Whether the controller reports a fault, or a fitted accessory has dropped off.

    Three sources, each one the Konnect app shows:

    * **Fault flags** — ``hub-state`` ``errorState`` / ``errorComponent`` and the
      ``errorstate`` every accessory message carries (`HubState.has_fault`), pushed.
    * **Active errors** — ``hub-diagnostics/{id}/active``. Read on each seed and twice a
      day; a failed read keeps the errors already known.
    * **Disconnected accessories** — one the configuration says is fitted whose
      ``parts`` entry is not ``Connected``, or an absent or empty SD card
      (`HubSettings.disconnected`), read on each seed. The accessory entities are gated
      on ``parts``, so without this a fitted accessory that drops off would just vanish.

    See `docs/protocol/hub_controller.md` §2.2.
    """

    _attr_name = "Problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_problem"

    @property
    def is_on(self) -> bool | None:
        state = self._state
        flags = None if state is None else state.has_fault
        if self._controller.active_errors or self._controller.settings.disconnected:
            return True
        return flags

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self._state
        return {
            "error_components": (
                sorted(k for k, v in state.error_components.items() if v)
                if state is not None
                else []
            ),
            "disconnected": list(self._controller.settings.disconnected),
            "active_errors": [
                {
                    "code": entry.get("errorCode"),
                    "title": entry.get("title"),
                    "component": entry.get("component"),
                }
                for entry in self._controller.active_errors
            ],
        }


class ControllerMusicSensor(ControllerAccessorySensor):
    """Whether the controller's amplifier is playing. From ``MUSIC_STS``.

    ``parts`` reports the amplifier under ``amplifier`` with its ``music`` key null, so
    a presence check must look at both.
    """

    _attr_name = "Music"
    _attr_icon = "mdi:music"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller, "music")


class ControllerLightSensor(ControllerAccessorySensor):
    """Whether the controller's lighting is on. From ``LIGHT_STS``.

    Untested: no system with lighting has been captured, so even the parse is
    unverified.
    """

    _attr_name = "Light"
    _attr_icon = "mdi:lightbulb"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller, "light")

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Each light group's own state; the sensor is on if any group is on.

        `LIGHT_STS` reports one group per message (`component` `lightgroupA/B/C`).
        """
        state = self._state
        return {
            "groups": {} if state is None else dict(state.lights),
            "configured_groups": list(self._controller.settings.light_groups),
        }


class ControllerSteamSensor(ControllerAccessorySensor):
    """Whether the steam generator is running. From ``STEAM_STS``.

    Untested with steam fitted: the message shape is known, the ON case has not been
    seen.
    """

    _attr_name = "Steam"
    _attr_icon = "mdi:hot-tub"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller, "steam")

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """The detail `STEAM_STS` carries.

        `status` is raw: `ON`, `OFF`, or `POWERCLEAN` while the generator cleans itself.
        Temperature is as the controller sends it, in °F.
        """
        state = self._state
        if state is None:
            return {}
        return {
            "status": state.steam_status,
            "power_clean": state.steam_powerclean,
            "temperature": state.steam_temperature,
            "start_time": state.steam_start_time,
            "total_time": state.steam_total_time,
        }


class ControllerOutletSensor(KohlerControllerEntity, BinarySensorEntity):
    """One outlet within one zone, as the controller reports it.

    The controller's belief, not the plumbing: it may miss a valve-driven session and
    read off while water runs. The Anthem Valve's outlet entities answer "is water
    coming out"; these answer "does the controller know", which decides whether its
    ``stopall`` and its own Max Shower Duration apply. ``Controller.water_is_running``,
    behind both Anthem Plus switches, is the any-of over exactly these, so the switches
    never disagree with them.

    Addressed per zone, like the valve's switches. Each zone reports a 6-slot array
    padded whatever the hardware, so only the zone's real outlets are read
    (`docs/protocol/hub_controller.md` §1).
    """

    _attr_icon = "mdi:shower-head"

    def __init__(
        self,
        coordinator: KohlerKonnectCoordinator,
        controller: Controller,
        zone: int,
        outlet: int,
    ) -> None:
        super().__init__(coordinator, controller)
        self._zone = zone
        self._outlet = outlet
        self._attr_name = f"Zone {zone} Outlet {outlet}"
        self._attr_unique_id = f"{self._device_id}_zone_{zone}_outlet_{outlet}"

    @property
    def is_on(self) -> bool | None:
        state = self._state
        if state is None:
            return None
        zone = state.zones.get(self._zone)
        if zone is None:
            return None
        if self._outlet - 1 >= len(zone.outlets):
            return None
        return zone.outlets[self._outlet - 1]
