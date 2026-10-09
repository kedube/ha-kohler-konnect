"""Faucet update entity: whether Kohler has newer firmware for the faucet."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature

from .coordinator import FaucetCoordinator
from .entity import KohlerFaucetEntity, as_text


def faucet_updates(coordinator: FaucetCoordinator) -> list[UpdateEntity]:
    return [FaucetFirmwareUpdate(coordinator)]


class FaucetFirmwareUpdate(KohlerFaucetEntity, UpdateEntity):
    """Installed and latest firmware, as Kohler's cloud reports them.

    **Read-only**, like the shower's: the latest version comes from Kohler's firmware check,
    as in the Konnect app, and installing stays with the app. Named "Firmware status" to
    match the showers' update entities, which answer "is there an update" rather than
    giving a version number.
    """

    _attr_translation_key = "firmware_status"
    # Reports installs started from the app; cannot start one itself.
    _attr_supported_features = UpdateEntityFeature.PROGRESS
    # Firmware details come from Kohler's cloud, not the live faucet.
    _requires_online = False

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "firmware_update")

    @property
    def entity_picture(self) -> str | None:
        # Update entities show the integration's logo by default; use Home Assistant's
        # update icons, which also show when one is available.
        return None

    @property
    def _firmware(self) -> dict[str, Any]:
        firmware = self.coordinator.about.get("firmware")
        return firmware if isinstance(firmware, dict) else {}

    @property
    def installed_version(self) -> str | None:
        firmware = self.coordinator.firmware
        return as_text(self.coordinator.about.get("firmware")) or (
            firmware.current if firmware else None
        )

    @property
    def latest_version(self) -> str | None:
        installed = self.installed_version
        if (firmware := self.coordinator.firmware) is not None:
            # The app decides from firmwareUpdateAvailable alone.
            if firmware.available and firmware.latest:
                return firmware.latest
            return installed
        # No answer from the firmware check: fall back to the configuration, and with
        # nothing reported, assume the installed version is current.
        return as_text(self._firmware.get("latestVersion")) or installed

    @property
    def in_progress(self) -> bool:
        """A firmware download, as `faucet-state` reports it — what the app reacts to."""
        return self.coordinator.firmware_downloading

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        firmware = self.coordinator.firmware
        return {"mandatory_update": firmware.mandatory if firmware else None}
