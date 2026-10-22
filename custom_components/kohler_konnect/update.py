"""Firmware update entities: installed version, latest version, and whether to update.

Read-only. Each entity reports Kohler's firmware check for one part (``gcs``,
``gcs/gateway`` or ``hub``) and offers no Install: installing stays with the Konnect
app, which refuses while water runs or the device is offline and supervises an install
that can take up to two hours. "Update available" comes from ``firmwareUpdateAvailable``
alone, as in the app. Checked every `FIRMWARE_CHECK_INTERVAL`; nothing pushes a release.
See `docs/protocol/platform.md` §7.

Named "… Firmware Status" so they don't share a name with the `… Firmware` diagnostic
sensors that carry the version numbers.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import Controller, KohlerKonnectCoordinator, Valve
from .entity import KohlerControllerEntity, KohlerValveEntity
from .faucet.update import faucet_updates


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One update entity per valve, per gateway, and per controller."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[UpdateEntity] = []
    for valve in coordinator.valves:
        entities.append(ValveFirmwareUpdate(coordinator, valve, "gcs"))
        entities.append(ValveFirmwareUpdate(coordinator, valve, "gateway"))
    for controller in coordinator.controllers:
        entities.append(ControllerFirmwareUpdate(coordinator, controller))
    # Each faucet is its own device, with its own coordinator.
    for faucet in coordinator.faucets:
        entities += faucet_updates(faucet)
    async_add_entities(entities)


def _text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


class _FirmwareMixin:
    """Shared reading of one firmware response."""

    def _info(self) -> dict[str, Any]:
        raise NotImplementedError

    @property
    def entity_picture(self) -> str | None:
        """None, so the update domain's state icons show instead of the brand image."""
        return None

    @property
    def installed_version(self) -> str | None:
        return _text(self._info().get("currentFirmware"))

    @property
    def latest_version(self) -> str | None:
        """The offered release when the cloud reports an update, else the installed one.

        Home Assistant shows an update whenever the two differ, so a newer number the
        cloud is not offering this device must not appear here.
        """
        info = self._info()
        installed = self.installed_version
        if str(info.get("firmwareUpdateAvailable")).strip().lower() == "true":
            return _text(info.get("firmware")) or installed
        return installed

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self._info()
        return {
            "mandatory_update": info.get("mandatoryUpdate"),
            "ota_status": info.get("otaStatus"),
            "skipped": info.get("skip"),
        }


class ValveFirmwareUpdate(_FirmwareMixin, KohlerValveEntity, UpdateEntity):
    """Firmware for the valve (``gcs``) or its Wi-Fi gateway (``gateway``)."""

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, valve: Valve, part: str
    ) -> None:
        super().__init__(coordinator, valve)
        self._part = part
        if part == "gateway":
            self._attr_name = "Gateway Firmware Status"
            self._attr_unique_id = f"{self._device_id}_gateway_firmware_update"
        else:
            self._attr_name = "Firmware Status"
            self._attr_unique_id = f"{self._device_id}_firmware_update"
            # Without the feature Home Assistant never reads `in_progress`, which only
            # the valve's own firmware has a source for.
            self._attr_supported_features = UpdateEntityFeature.PROGRESS

    def _info(self) -> dict[str, Any]:
        return self._valve.firmware_info.get(self._part) or {}

    @property
    def in_progress(self) -> bool:
        """The valve's own `currentSystemState == "FirmwareUpdate"`, for its main firmware."""
        if self._part != "gcs":
            return False
        state = self._state
        return bool(state is not None and state.firmware_updating)


class ControllerFirmwareUpdate(_FirmwareMixin, KohlerControllerEntity, UpdateEntity):
    """Firmware for the Anthem Plus controller (``hub``)."""

    _attr_name = "Firmware Status"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_firmware_update"

    def _info(self) -> dict[str, Any]:
        return self._controller.firmware_info or {}
