"""One Smart Fan's part of the diagnostics report, identity taken out.

Its replies go in as Kohler sent them, since nobody on this project has seen one: an
owner's report is how the open questions in `docs/protocol/smart_fan.md` §10 get settled.
Favorite titles stay out, as the showers' and faucets' do: they are the owner's own words.
"""

from __future__ import annotations

from typing import Any

from ..redaction import redact_report
from .coordinator import SmartFanCoordinator


def _without_titles(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            key: _without_titles(value)
            for key, value in payload.items()
            if key not in ("title", "logicalName")
        }
    if isinstance(payload, list):
        return [_without_titles(item) for item in payload]
    return payload


def smart_fan_report(fan: SmartFanCoordinator) -> dict[str, Any]:
    firmware = fan.firmware
    report = {
        "sku": fan.sku,
        "has_light": fan.has_light,
        "online": fan.online,
        "last_update_success": fan.last_update_success,
        "firmware_check": None
        if firmware is None
        else {
            "available": firmware.available,
            "latest": firmware.latest,
            "current": firmware.current,
        },
        "replies": _without_titles(fan.raw),
    }
    return redact_report(
        report, (fan.device_id, fan.client.tenant_id, fan.serial_number)
    )
