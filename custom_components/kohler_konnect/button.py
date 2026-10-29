"""Buttons for the Kohler Konnect integration.

Restart a valve, clear a valve's or a controller's error log, and start a new raw MQTT
capture file. The capture button closes the current file and opens a new one, so each
experiment gets its own file without restarting Home Assistant and dropping the MQTT
connection.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import Controller, KohlerKonnectCoordinator, Valve
from .entity import KohlerControllerEntity, KohlerValveEntity
from .product_platforms import product_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the valve, controller, capture and faucet buttons."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [ValveRestartButton(coordinator, valve) for valve in coordinator.valves]
    )
    async_add_entities(
        [ValveClearErrorLogButton(coordinator, valve) for valve in coordinator.valves]
    )
    async_add_entities(
        [
            ControllerClearErrorLogButton(coordinator, controller)
            for controller in coordinator.controllers
        ]
    )

    # One capture covers the whole account, so one button: on the first valve, or on the
    # first controller when the account has no valve.
    if coordinator.valves:
        async_add_entities([ValveNewCaptureButton(coordinator, coordinator.valves[0])])
    elif coordinator.controllers:
        async_add_entities(
            [ControllerNewCaptureButton(coordinator, coordinator.controllers[0])]
        )

    # Every other product's; a faucet's preset button follows once it has a preset.
    if products := product_entities(
        hass, entry, coordinator, Platform.BUTTON, async_add_entities
    ):
        async_add_entities(products)


class ValveRestartButton(KohlerValveEntity, ButtonEntity):
    """Reboot the valve, as the Konnect app's Settings → Restart Product does.

    Sends ``valvereset {reset: "productRestart"}``. Disabled by default because, unlike
    the app, a button cannot ask for confirmation. It cannot reach a valve that has
    dropped off the cloud; that one needs a power cycle. App-confirmed only
    (`docs/protocol/gcs_valve.md` §3).

    ⚠️ A restart stops any running water.
    """

    _attr_name = "Restart"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_restart"

    async def async_press(self) -> None:
        await self._valve.async_restart()


class ValveClearErrorLogButton(KohlerValveEntity, ButtonEntity):
    """Clear the valve's error log (``DELETE gcs-diagnostics/{id}``), as the app does.

    `Problem`'s `active_faults` come from this log, so a fault that has ended stops
    showing; one the valve still has returns on the next read. The status word is
    unaffected. App-confirmed only.
    """

    _attr_name = "Clear Error Log"
    _attr_icon = "mdi:playlist-remove"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_clear_error_log"

    async def async_press(self) -> None:
        await self._valve.async_clear_fault_log()


class ControllerClearErrorLogButton(KohlerControllerEntity, ButtonEntity):
    """Clear an Anthem Plus controller's error log: ``DELETE hub-diagnostics/{id}``.

    The app declares this call but no screen uses it, and it is untried on hardware; if
    Kohler refuses it, the press reports that (`docs/protocol/hub_controller.md` §2).
    """

    _attr_name = "Clear Error Log"
    _attr_icon = "mdi:playlist-remove"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_clear_error_log"

    async def async_press(self) -> None:
        await self.coordinator.async_clear_hub_fault_log(self._controller)


class _NewCaptureMixin:
    """Roll the raw MQTT capture file; shared by the valve and controller variants.

    Mixed in ahead of the device base so `_update_device_ids` wins the MRO.
    """

    _attr_name = "Start new MQTT capture"
    _attr_icon = "mdi:file-restore-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def _update_device_ids(self) -> frozenset[str] | None:
        """Every message, from any device: the capture file is account-wide."""
        return None

    async def async_press(self) -> None:
        raw_log = self.coordinator.raw_log
        if raw_log is None:
            _LOGGER.warning("No diagnostic capture is set up; nothing to roll")
            return
        # Opens a file and creates a directory — off the event loop.
        raw_path = await self.hass.async_add_executor_job(raw_log.roll)
        if raw_path is None:
            _LOGGER.warning(
                "The raw MQTT capture is OFF, so there is nothing to roll. Turn it on with "
                "ENABLE_RAW_MQTT_LOG in const.py, or the logger.set_level action on "
                "custom_components.kohler_konnect.konnect.raw_log"
            )
            return
        _LOGGER.warning("Started a new raw MQTT capture file: %s", raw_path)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        raw_log = self.coordinator.raw_log
        return {
            "capture_enabled": bool(raw_log and raw_log.enabled),
            "current_file": (raw_log.path if raw_log else None),
        }


class ValveNewCaptureButton(_NewCaptureMixin, KohlerValveEntity, ButtonEntity):
    """Capture button on the Anthem Valve device."""

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_new_mqtt_capture"

    @property
    def available(self) -> bool:
        """A diagnostic action, usable whenever the entry is loaded."""
        return self.coordinator.last_update_success


class ControllerNewCaptureButton(
    _NewCaptureMixin, KohlerControllerEntity, ButtonEntity
):
    """Capture button on a controller-only account. See :class:`ValveNewCaptureButton`."""

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_new_mqtt_capture"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
