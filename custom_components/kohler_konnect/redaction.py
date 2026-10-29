"""Taking identity out of a product's diagnostics: its ids, account, location and secrets.

Shared by the products that report their own payloads (the faucet's, the Smart Fan's …).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data

__all__ = ["TO_REDACT", "redact_report", "redact_values", "without_cloud_secrets"]

# Keys whose values are identity or location in a product's own payloads.
TO_REDACT = {
    "deviceId",
    "tenantId",
    # Another account's id, in a Smart Fan's or an EvoCycle's state.
    "deletedTenantId",
    "customerId",
    "serialNumber",
    "serialNo",
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
# wherever they appear (compared without case). A configuration reply is a
# `DeviceConfigurationResponse`, whose top-level `iot` can carry an IoT Hub connection
# string, SharedAccessKey included.
_CLOUD_BLOCKS = frozenset({"iot", "iothub", "iothubsettings"})
# Secrets by their shape, whatever the field is called: a connection string or its key, a
# shared access signature, a signed URL. The whole value goes, host and device id included.
_SECRET_VALUE = re.compile(
    r"sharedaccess|hostname=|\bsig=|azure-devices\.net", re.IGNORECASE
)


def without_cloud_secrets(data: Any) -> Any:
    """``data`` without cloud-addressing blocks, and with secret-shaped values redacted."""
    if isinstance(data, dict):
        return {
            key: without_cloud_secrets(value)
            for key, value in data.items()
            if str(key).lower() not in _CLOUD_BLOCKS
        }
    if isinstance(data, list):
        return [without_cloud_secrets(item) for item in data]
    if isinstance(data, str) and _SECRET_VALUE.search(data):
        return REDACTED
    return data


def redact_values(data: Any, pattern: re.Pattern[str]) -> Any:
    """Redact identifiers wherever they appear, whatever the field is called.

    Kohler echoes the device id in fields like "id", which key-based redaction cannot catch
    without hiding every other "id".
    """
    if isinstance(data, str):
        return pattern.sub(REDACTED, data)
    if isinstance(data, dict):
        return {key: redact_values(value, pattern) for key, value in data.items()}
    if isinstance(data, list):
        return [redact_values(item, pattern) for item in data]
    return data


def redact_report(report: dict[str, Any], identifiers: Iterable[str | None]) -> Any:
    """``report`` with cloud secrets dropped, identity keys redacted, and ``identifiers``
    (the device id, tenant id, serial number) redacted wherever they appear."""
    cleaned = async_redact_data(without_cloud_secrets(report), TO_REDACT)
    pattern = "|".join(re.escape(value) for value in identifiers if value)
    if not pattern:
        return cleaned
    return redact_values(cleaned, re.compile(pattern, re.IGNORECASE))
