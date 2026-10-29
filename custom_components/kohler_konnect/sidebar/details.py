"""What the panel's About and Diagnostics screens show of a device, for administrators.

Identity comes from the device registry, as Home Assistant's own device page shows it; the
rest from what each device's coordinator already holds, so opening a screen reads nothing
from Kohler. Serial numbers, the controller's LAN address and the fault log are why only
administrators are sent this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from ..faucet.coordinator import leak_time
from ..faucet.entity import as_text
from ..sensor import kohler_timestamp
from .live import DeviceRef, _iso

if TYPE_CHECKING:
    from ..coordinator import Controller, Valve
    from ..faucet.coordinator import FaucetCoordinator

__all__ = ["device_details"]

# The parts' firmware, in the order the panel lists it: (key, block of the `about` record).
_VALVE_FIRMWARE = (
    ("interface", "uI2"),
    ("valve", "primaryValve"),
    ("second_valve", "secondaryValve1"),
    ("gateway", "gateway"),
)
# Beneath a faucet's own firmware, which the app calls the gateway's.
_FAUCET_FIRMWARE = (
    ("application", "application"),
    ("bootloader", "bootloader"),
    ("os", "os"),
    ("certificate", "certificate"),
    ("service_pack", "servicePack"),
)


def _text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _date(raw: Any) -> str | None:
    """A date as Kohler sent it, with an epoch made ISO-8601, for the panel to format."""
    text = _text(raw)
    if text is not None and text.strip().isdigit():
        stamp = kohler_timestamp(text)
        return None if stamp is None else stamp.isoformat()
    return text


def _registered(raw: Any) -> str | None:
    stamp = kohler_timestamp(raw)
    return None if stamp is None else stamp.isoformat()


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _part(kind: str, record: dict[str, Any]) -> dict[str, Any]:
    """One entry of a valve's `gcs-configuration/{id}/about`."""
    return {
        "kind": kind,
        "name": _text(record.get("name")),
        "model": _text(record.get("model")),
        "serial": _text(record.get("serialNo")),
        "firmware": _text(record.get("firmware")),
        "status": _text(record.get("status")),
        "install_date": _date(record.get("installDate")),
    }


def _valve(valve: Valve, details: dict[str, Any]) -> None:
    about = valve.about_parts
    gateway = about.get("gatewayConfigInfo")
    gateway = gateway if isinstance(gateway, dict) else {}
    # The app's Install Date; `createdTime` is when the cloud record was made.
    details["install_date"] = _date(gateway.get("installDate"))
    details["registered"] = _registered(valve.created_time)
    details["firmware"] = [
        {"key": key, "version": valve.component_firmware(block)}
        for key, block in _VALVE_FIRMWARE
        # A placeholder `0` without a second valve.
        if block != "secondaryValve1" or valve.model.uses_valve2
    ]
    details["parts"] = [
        *([_part("gateway", gateway)] if gateway else []),
        *(_part("valve", v) for v in _records(about.get("valvesConfigInfo"))),
        *(_part("interface", i) for i in _records(about.get("interfacesConfigInfo"))),
    ]
    # The log's own text isn't kept for a valve: entries seen on hardware carry none.
    details["faults"] = [
        {
            "code": _text(fault.get("code")),
            "title": None,
            "area": _text(fault.get("area")),
            "time": _date(fault.get("timestamp")),
        }
        for fault in valve.active_faults
    ]


def _controller(controller: Controller, details: dict[str, Any]) -> None:
    details["firmware"] = [
        {
            "key": "controller",
            "version": _text((controller.firmware_info or {}).get("currentFirmware")),
        }
    ]
    details["faults"] = [
        {
            "code": _text(error.get("errorCode")),
            "title": _text(error.get("title")),
            "area": _text(error.get("component")),
            # A controller's log spells it `timeStamp`.
            "time": _date(error.get("timeStamp") or error.get("timestamp")),
        }
        for error in controller.active_errors
    ]
    details["web_url"] = controller.settings.web_url


def _faucet(faucet: FaucetCoordinator, details: dict[str, Any]) -> None:
    about = faucet.about
    firmware = about.get("firmware")
    parts = firmware if isinstance(firmware, dict) else {}
    details["install_date"] = _date(
        about.get("installedDate") or faucet.config.get("installedDate")
    )
    details["registered"] = _registered(faucet.config.get("createdTime"))
    details["firmware"] = [
        {"key": "gateway", "version": as_text(firmware)},
        *(
            {"key": key, "version": as_text(parts.get(field))}
            for key, field in _FAUCET_FIRMWARE
        ),
    ]
    detected = (leak_time(event) for event in faucet.leak_history)
    details["leak_history"] = sorted(
        (_iso(at) for at in detected if at is not None), reverse=True
    )


def device_details(hass: HomeAssistant, ref: DeviceRef) -> dict[str, Any]:
    """Identity, firmware, parts, faults and leaks for one device's About screen."""
    device = dr.async_get(hass).async_get(ref.ha_id)
    details: dict[str, Any] = {
        "device_id": ref.ha_id,
        "model": None if device is None else device.model,
        "model_name": None if device is None else device.model_id,
        "serial": None if device is None else device.serial_number,
        "install_date": None,
        "registered": None,
        "hardware": None if device is None else device.hw_version,
        "firmware": [],
        "parts": [],
        "faults": [],
        "leak_history": [],
        "web_url": None,
        # Home Assistant's own download, which `auth/sign_path` makes a link of.
        "diagnostics_path": (
            f"/api/diagnostics/config_entry/{ref.entry.entry_id}/device/{ref.ha_id}"
        ),
    }
    if ref.kind == "valve":
        _valve(ref.target, details)
    elif ref.kind == "controller":
        _controller(ref.target, details)
    elif ref.kind == "faucet":
        _faucet(ref.target, details)
    return details
