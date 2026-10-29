"""One EvoCycle's part of the diagnostics report, identity and network details taken out.

Its replies go in as Kohler sent them, since nobody on this project has seen one, except
the gateway's block: it holds the MAC address, the Wi-Fi name and the certificate details
(`docs/protocol/evocycle.md` §2.3), so only its firmware stays.
"""

from __future__ import annotations

from typing import Any

from ..redaction import redact_report
from .coordinator import EvoCycleCoordinator


def _without_gateway_details(configuration: Any) -> Any:
    if not isinstance(configuration, dict):
        return configuration
    about = configuration.get("evoAboutModel")
    if not isinstance(about, dict) or not isinstance(
        about.get("gatewayConfiguration"), dict
    ):
        return configuration
    gateway = about["gatewayConfiguration"]
    return {
        **configuration,
        "evoAboutModel": {
            **about,
            "gatewayConfiguration": {"appVersion": gateway.get("appVersion")},
        },
    }


def evocycle_report(evo: EvoCycleCoordinator) -> dict[str, Any]:
    firmware = evo.firmware
    report = {
        "sku": evo.sku,
        "online": evo.online,
        "activity": evo.activity,
        "fault": evo.fault,
        "last_update_success": evo.last_update_success,
        "firmware_check": None
        if firmware is None
        else {
            "available": firmware.available,
            "latest": firmware.latest,
            "current": firmware.current,
        },
        "replies": {
            "state": evo.raw.get("state"),
            "configuration": _without_gateway_details(evo.raw.get("configuration")),
        },
    }
    return redact_report(
        report, (evo.device_id, evo.client.tenant_id, evo.serial_number)
    )
