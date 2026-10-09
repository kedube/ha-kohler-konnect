"""Faucet binary sensors: leak, dispensing, and whether Kohler can reach the faucet."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory

from .coordinator import FaucetCoordinator, leak_time
from .entity import KohlerFaucetEntity


def faucet_binary_sensors(coordinator: FaucetCoordinator) -> list[BinarySensorEntity]:
    """Every binary sensor for one faucet."""
    return [
        FaucetLeakSensor(coordinator),
        FaucetDispensingSensor(coordinator),
        FaucetConnectedSensor(coordinator),
    ]


class FaucetLeakSensor(KohlerFaucetEntity, BinarySensorEntity):
    """On while Kohler reports a leak that has not been cleared.

    Leaks come from the faucet's leak history and the stream's real-time leak alert. Kohler
    keeps the history, so "any event" would stay on for good. Press "Clear leak alert" to
    acknowledge the current leaks; a new one turns the sensor back on.
    """

    _requires_online = False
    _attr_translation_key = "leak"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "leak")

    @property
    def is_on(self) -> bool | None:
        # Unknown, not "dry", until the configuration has been read once.
        if self.coordinator.leak_alert:
            return True
        if not self.coordinator.config_loaded:
            return None
        return bool(self.coordinator.active_leaks)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self.coordinator.leak_history
        last = self.coordinator.last_leak_at
        return {
            "events": len(history),
            "uncleared_events": len(self.coordinator.active_leaks),
            # Kohler's order is not guaranteed; the app sorts by time too.
            "latest": max(history, key=lambda e: leak_time(e) or 0)
            if history
            else None,
            "last_detected": last.isoformat() if last else None,
        }


class FaucetDispensingSensor(KohlerFaucetEntity, BinarySensorEntity):
    """On while a measured amount is being dispensed.

    Covers dispenses started from Home Assistant and presets run from the Konnect app (the
    latter reported by the stream). Kohler does not report other dispenses, such as by
    voice, apart from the water turning on.
    """

    _attr_translation_key = "dispensing"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "dispensing")

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_dispensing()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        preset = self.coordinator.dispensing_preset
        return {"preset": preset} if preset else {}


class FaucetConnectedSensor(KohlerFaucetEntity, BinarySensorEntity):
    """Whether Kohler's cloud can currently reach the faucet."""

    _requires_online = False
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FaucetCoordinator) -> None:
        super().__init__(coordinator, "connected")

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.connection_state is None:
            return None  # Kohler did not say
        return self.coordinator.faucet_online

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_connected": self.coordinator.last_connected}
