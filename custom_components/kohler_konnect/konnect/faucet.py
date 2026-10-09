"""The Sensate and Setra kitchen faucets (SKUs ``SEN`` and ``SET``).

Unlike an Anthem valve, a faucet has plain commands — water on, water off, and a measured
dispense in litres — and its live state is a small text record rather than a hex word. The
reads live on :class:`~.client.KohlerClient` beside every other product's; this module holds
the command surface, the readers for the faucet's own payloads, and the decoding of its MQTT
messages, which arrive on the same account-wide stream as the showers'.

Everything here was live-verified by the separate ``kohler_sensate`` integration this merged
into, except where a comment says otherwise. ``docs/protocol/sensate_faucet.md`` is the
reference, with the Konnect 3.0.6 source for each fact.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from .client import KohlerClient, KohlerError, status_code
from .const import (
    FAUCET_DISPENSE,
    FAUCET_ONOFF,
    MSG_FAUCET_LEAK,
    MSG_FAUCET_PRESET,
    MSG_FAUCET_STATUS,
    MSG_FIRMWARE_INSTALL,
    SKU_SENSATE,
    STATUS_DEVICE_OFFLINE,
    STATUS_FIRMWARE_UPDATING,
    STATUS_NOT_DISPENSED,
)
from .mqtt import Envelope

_LOGGER = logging.getLogger(__name__)

#: In-body codes that mean a faucet command was **not** carried out, even on an HTTP 200.
#: Kohler can refuse a command that way, and then nothing happened; reads are taken as they
#: come. (900, offline, is already raised by the client for every path.)
COMMAND_FAILURES = frozenset(
    {
        STATUS_DEVICE_OFFLINE,
        STATUS_FIRMWARE_UPDATING,
        STATUS_NOT_DISPENSED,
        "904",
        "909",
        "911",
        "917",
        "918",
    }
)

#: Faucet `state.status` values known for certain. Anything else is "unknown" and is
#: recorded for diagnostics so it can be added here.
STATUS_OFF = frozenset({"off", "notstarted"})
STATUS_ON = frozenset({"on"})
#: `handleState` that blocks remote water, as in the Konnect app.
HANDLE_CLOSED = "closed"
#: `progress` while a firmware download runs — which also blocks remote water.
PROGRESS_DOWNLOADING = "downloading"


def water_running(state: dict[str, Any]) -> bool | None:
    """True/False for statuses known for certain, None for anything else.

    ``progress`` is not consulted: it is the firmware download, not the water.
    """
    status = state.get("status")
    if not isinstance(status, str):
        return None
    if status.lower() in STATUS_ON:
        return True
    if status.lower() in STATUS_OFF:
        return False
    return None


@dataclass(frozen=True, slots=True)
class FaucetSnapshot:
    """One `faucet-state` read: the live state plus whether Kohler can reach the faucet."""

    state: dict[str, Any]
    connection_state: str | None
    last_connected: Any
    sku: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> FaucetSnapshot:
        connection = payload.get("connectionState")
        sku = payload.get("sku")
        return cls(
            state=dict(payload.get("state") or {}),
            connection_state=connection if isinstance(connection, str) else None,
            last_connected=payload.get("lastConnected"),
            sku=sku.upper() if isinstance(sku, str) and sku else None,
        )


@dataclass(frozen=True, slots=True)
class FaucetPreset:
    """A dispense preset saved in the Konnect app."""

    preset_id: str
    title: str
    liters: float


@dataclass(frozen=True, slots=True)
class FirmwareInfo:
    """Kohler's answer to "is there newer firmware for this faucet?"."""

    available: bool
    latest: str | None
    current: str | None
    mandatory: bool


# The Konnect app's preset units, in litres per unit; it converts in single precision,
# hence the cup factor.
_DISPLAY_UNITS = {
    "milliliters": 0.001,
    "liters": 1.0,
    "cups": 0.2365880012512207,
    "quarts": 0.946353,
    "gallons": 3.785411784,
}
_FRACTIONS = {"¼": 0.25, "½": 0.5, "¾": 0.75}
_DISPLAY_QUANTITY = re.compile(r"^\s*(\d+(?:\.\d+)?)?\s*([¼½¾])?\s+([A-Za-z]+)\s*$")


def parse_display_quantity(text: Any) -> float | None:
    """Litres from a preset's ``displayQuantity``, such as "1¾ Quarts"."""
    if not isinstance(text, str) or not (match := _DISPLAY_QUANTITY.match(text)):
        return None
    whole, fraction, unit = match.groups()
    factor = _DISPLAY_UNITS.get(unit.lower())
    if factor is None or (whole is None and fraction is None):
        return None
    amount = float(whole or 0) + _FRACTIONS.get(fraction or "", 0.0)
    return amount * factor if amount > 0 else None


def _parse_preset_items(items: list[Any], device_id: str | None) -> list[FaucetPreset]:
    """Presets from Konnect experience entries; anything malformed is skipped.

    ``dispenseAmount`` is in litres; ``displayQuantity`` stands in if it is missing. With
    ``device_id``, entries for other devices are skipped.
    """
    presets: list[FaucetPreset] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if (
            device_id is not None
            and str(item.get("deviceId") or "").lower() != device_id.lower()
        ):
            continue
        preset_id = item.get("experienceId")
        title = item.get("title")
        amount = item.get("dispenseAmount")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            amount = parse_display_quantity(item.get("displayQuantity"))
        if preset_id in (None, "") or not title or amount is None or amount <= 0:
            continue
        presets.append(FaucetPreset(str(preset_id), str(title), float(amount)))
    return presets


def parse_presets(payload: Any, device_id: str) -> list[FaucetPreset]:
    """One faucet's presets from ``customer-experience``.

    The reply lists the presets of every device on the account; the faucet's are in
    ``sensateExperiences``. Konnect 3.0.6 no longer reads that field, which is why this is
    only the fallback for `parse_faucet_experiences`.
    """
    items = payload.get("sensateExperiences") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return _parse_preset_items(items, device_id)


def parse_faucet_experiences(payload: Any, device_id: str) -> list[FaucetPreset] | None:
    """One faucet's presets from ``faucet-experience?DeviceIds=``.

    The reply is ``{faucetExperienceList: [{deviceId, experience: [...]}]}``. Returns None
    when it is not shaped like that.
    """
    groups = payload.get("faucetExperienceList") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        return None
    groups = [group for group in groups if isinstance(group, dict)]
    # The app reads the first entry; prefer one that names this faucet.
    group = next(
        (
            group
            for group in groups
            if str(group.get("deviceId") or "").lower() == device_id.lower()
        ),
        groups[0] if groups else None,
    )
    if group is None:
        return []
    items = group.get("experience")
    if not isinstance(items, list):
        return None
    return _parse_preset_items(items, None)


def parse_firmware(payload: Any) -> FirmwareInfo | None:
    """Read the firmware check; None if it does not say whether one is available."""
    if not isinstance(payload, dict) or not isinstance(
        available := payload.get("firmwareUpdateAvailable"), bool
    ):
        return None

    def text(key: str) -> str | None:
        value = payload.get(key)
        return str(value) if value not in (None, "") else None

    return FirmwareInfo(
        available=available,
        latest=text("firmware"),
        current=text("currentFirmware"),
        mandatory=payload.get("mandatoryUpdate") is True,
    )


@dataclass(frozen=True, slots=True)
class FaucetEvent:
    """What a faucet's MQTT message says happened, as far as it is understood."""

    status: str | None = None  # water "On"/"Off"
    handle: str | None = None  # "OPEN"/"CLOSED"
    preset: str | None = None  # name of the preset that started or finished
    preset_id: str | None = None
    preset_on: bool | None = None
    leak: bool = False
    firmware: str | None = None  # install result: "Installed"/"Aborted"


def _text(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) and value else None


def parse_event(envelope: Envelope) -> FaucetEvent | None:
    """Read a faucet message the Konnect app acts on; None for anything else."""
    items = envelope.attributes
    # The app checks only the code of a leak alert, wherever it is.
    if MSG_FAUCET_LEAK in (envelope.code, *(item.get("code") for item in items)):
        return FaucetEvent(leak=True)
    for item in items:
        status = _text(item, "status")
        code = item.get("code")
        if code == MSG_FAUCET_STATUS:
            return FaucetEvent(status=status, handle=_text(item, "handle"))
        if code == MSG_FAUCET_PRESET and status:
            return FaucetEvent(
                preset=_text(item, "name"),
                preset_id=_text(item, "experienceid"),
                # Like the app: anything but "OFF" means running.
                preset_on=status.upper() != "OFF",
            )
        if code == MSG_FIRMWARE_INSTALL and status:
            return FaucetEvent(firmware=status)
    return None


class FaucetDevice:
    """Commands and preset reads for one faucet."""

    def __init__(
        self, client: KohlerClient, device_id: str, sku: str = SKU_SENSATE
    ) -> None:
        self._client = client
        self.device_id = device_id
        #: Sent with every command. The app always sends the device's own SKU.
        self.sku = sku

    async def _async_command(self, path: str, **fields: Any) -> Any:
        body = {
            "deviceId": self.device_id,
            "sku": self.sku,
            "tenantId": await self._client.async_tenant_id(),
            **fields,
        }
        payload = await self._client.async_request("POST", path, json_body=body)
        # Refused in the body of an HTTP 200, and so not carried out.
        if (code := status_code(payload)) in COMMAND_FAILURES:
            raise KohlerError(
                f"{KohlerClient.safe_path(path)} was refused (statusCode {code})",
                payload,
                200,
            )
        return payload

    async def async_dispense(self, liters: float) -> Any:
        """Dispense a measured amount of water. The API unit is litres."""
        return await self._async_command(FAUCET_DISPENSE, quantity=round(liters, 4))

    async def async_set_water(self, on: bool) -> Any:
        """Turn the water on or off."""
        return await self._async_command(FAUCET_ONOFF, action="ON" if on else "OFF")

    async def async_get_presets(self) -> tuple[list[FaucetPreset], str]:
        """The faucet's Konnect presets, and which list they came from.

        The faucet's own list (``faucet-experience``) is what the app's faucet screen
        reads. The account-wide ``customer-experience`` list stands in if that fails or is
        empty.
        """
        try:
            presets = parse_faucet_experiences(
                await self._client.async_get_faucet_presets(self.device_id),
                self.device_id,
            )
        except KohlerError as err:
            _LOGGER.debug("Faucet preset list unavailable: %s", err)
            presets = None
        if presets:
            return presets, "faucet-experience"
        payload = await self._client.async_get_customer_experience()
        return parse_presets(payload, self.device_id), "customer-experience"
