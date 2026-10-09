"""One faucet's part of the diagnostics report. See `diagnostics.py` for the whole."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data

from ..konnect.usage import usage_series
from .coordinator import FaucetCoordinator

# Keys whose values are identity or location in the faucet's own payloads.
TO_REDACT = {
    "deviceId",
    "tenantId",
    "customerId",
    "serialNumber",
    "serial",
    "macAddress",
    "mac",
    "ssid",
    "address",
    "email",
    "latitude",
    "longitude",
    "homeLatitude",
    "homeLongitude",
}


def _redact_values(data: Any, pattern: re.Pattern[str]) -> Any:
    """Redact identifiers wherever they appear, whatever the field is called.

    Kohler echoes the device id in fields like "id", which key-based redaction cannot catch
    without hiding every other "id".
    """
    if isinstance(data, str):
        return pattern.sub(REDACTED, data)
    if isinstance(data, dict):
        return {key: _redact_values(value, pattern) for key, value in data.items()}
    if isinstance(data, list):
        return [_redact_values(item, pattern) for item in data]
    return data


def faucet_report(faucet: FaucetCoordinator) -> dict[str, Any]:
    """What one faucet reports, with its identity taken out.

    Preset names stay out, as the showers' favorite names do: they are the owner's own
    words, and the count and amounts carry the signal.
    """
    firmware = faucet.firmware
    report = {
        "sku": faucet.sku,
        "last_update_success": faucet.last_update_success,
        "update_interval": str(faucet.update_interval),
        "connection_state": faucet.connection_state,
        "state": async_redact_data(faucet.data or {}, TO_REDACT),
        "configuration": async_redact_data(faucet.config, TO_REDACT),
        "uncleared_leak_events": len(faucet.active_leaks),
        "leak_alert": faucet.leak_alert,
        "firmware_check": None
        if firmware is None
        else {
            "available": firmware.available,
            "latest": firmware.latest,
            "current": firmware.current,
            "mandatory": firmware.mandatory,
        },
        # Every status/progress/handle value seen since startup, to extend the list of
        # values the integration knows.
        "seen_values": {
            field: sorted(values) for field, values in faucet.seen_values.items()
        },
        "presets": {
            "count": len(faucet.presets),
            "liters": [round(p.liters, 4) for p in faucet.presets.values()],
            "source": faucet.preset_source,
        },
        "water_safety_limit_minutes": faucet.max_run_minutes,
        "usage_series": {
            "monthly_buckets": len(usage_series(faucet.usage)),
            "daily_buckets": len(usage_series(faucet.usage_daily)),
        },
        "dispensing": faucet.is_dispensing(),
        "instant_updates": {
            "messages": faucet.push_messages,
            "verified": faucet.push_verified,
            "missed_changes": faucet.push_missed,
        },
    }
    identifiers = [
        re.escape(value)
        for value in (faucet.device_id, faucet.client.tenant_id, faucet.serial_number)
        if value
    ]
    if not identifiers:
        return report
    redacted: dict[str, Any] = _redact_values(
        report, re.compile("|".join(identifiers), re.IGNORECASE)
    )
    return redacted
