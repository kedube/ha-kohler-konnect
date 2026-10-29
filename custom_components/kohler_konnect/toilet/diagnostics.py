"""One Numi's or Purewash's part of the diagnostics report, identity taken out.

Its replies go in as Kohler sent them, since nobody on this project has seen one: an
owner's report is how the open questions in `docs/protocol/numi_purewash.md` §10 get settled.
"""

from __future__ import annotations

from typing import Any

from ..redaction import redact_report
from .coordinator import ToiletCoordinator


def toilet_report(toilet: ToiletCoordinator) -> dict[str, Any]:
    firmware = toilet.firmware
    report = {
        "sku": toilet.sku,
        "model": toilet.model,
        "online": toilet.online,
        "controllable": toilet.controllable,
        "last_update_success": toilet.last_update_success,
        "firmware_check": None
        if firmware is None
        else {
            "available": firmware.available,
            "latest": firmware.latest,
            "current": firmware.current,
        },
        "replies": toilet.raw,
        # What the messages added: the last error and the device's own state.
        "from_messages": {
            key: toilet.state.get(key) for key in ("error", "deviceState")
        },
    }
    return redact_report(
        report, (toilet.device_id, toilet.client.tenant_id, toilet.serial_number)
    )
