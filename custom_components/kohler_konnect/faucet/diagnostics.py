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
    "connectionString",
    "sharedAccessKey",
    "password",
}

# Blocks that address the faucet in Kohler's cloud rather than describe it, dropped whole
# wherever they appear (compared without case). `faucet-configuration` is a
# `DeviceConfigurationResponse`, whose top-level `iot` can carry an IoT Hub connection
# string, SharedAccessKey included.
_CLOUD_BLOCKS = frozenset({"iot", "iothub", "iothubsettings"})
# Secrets by their shape, whatever the field is called: a connection string or its key, a
# shared access signature, a signed URL. The whole value goes, host and device id included.
_SECRET_VALUE = re.compile(
    r"sharedaccess|hostname=|\bsig=|azure-devices\.net", re.IGNORECASE
)


def _without_cloud_secrets(data: Any) -> Any:
    """``data`` without cloud-addressing blocks, and with secret-shaped values redacted."""
    if isinstance(data, dict):
        return {
            key: _without_cloud_secrets(value)
            for key, value in data.items()
            if str(key).lower() not in _CLOUD_BLOCKS
        }
    if isinstance(data, list):
        return [_without_cloud_secrets(item) for item in data]
    if isinstance(data, str) and _SECRET_VALUE.search(data):
        return REDACTED
    return data


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


def _with_duration(series: dict[str, Any] | None) -> int:
    """How many of a usage series' buckets carry a duration."""
    return sum("onDuration" in bucket for bucket in usage_series(series))


def faucet_report(faucet: FaucetCoordinator) -> dict[str, Any]:
    """What one faucet reports, with its identity taken out.

    Preset names stay out, as the showers' favorite names do: they are the user's own
    words, and the count and amounts carry the signal.
    """
    firmware = faucet.firmware
    report = {
        "sku": faucet.sku,
        "last_update_success": faucet.last_update_success,
        "update_interval": str(faucet.update_interval),
        "connection_state": faucet.connection_state,
        "state": async_redact_data(
            _without_cloud_secrets(faucet.data or {}), TO_REDACT
        ),
        "configuration": async_redact_data(
            _without_cloud_secrets(faucet.config), TO_REDACT
        ),
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
            # With none, the faucet's Duration totals read unknown.
            "monthly_buckets_with_duration": _with_duration(faucet.usage),
            "daily_buckets_with_duration": _with_duration(faucet.usage_daily),
        },
        "dispensing": faucet.is_dispensing(),
        # Messages and missed changes since setup. Verification is kept across restarts:
        # "restored" says it was verified before the restart and nothing has come since.
        "instant_updates": {
            "messages": faucet.push_messages,
            "verified": faucet.push_verified,
            "restored": faucet.push_restored,
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
