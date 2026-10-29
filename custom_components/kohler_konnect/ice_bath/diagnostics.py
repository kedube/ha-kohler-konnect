"""One Ice Bath journal's part of the diagnostics report: counts and the routine only.

⚠️ No record goes in: each can carry the person's notes, answers and follow-ups.
"""

from __future__ import annotations

from typing import Any

from ..redaction import redact_report
from .coordinator import IceBathCoordinator


def ice_bath_report(journal: IceBathCoordinator) -> dict[str, Any]:
    data = journal.data or {}
    report = {
        "sku": journal.sku,
        "last_update_success": journal.last_update_success,
        "sessions_read": data.get("record_count"),
        "target": journal.target,
        "routine": journal.routine,
    }
    return redact_report(
        report, (journal.device_id, journal.client.tenant_id, journal.serial_number)
    )
