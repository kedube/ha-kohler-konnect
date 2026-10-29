"""Commands for the Anthem digital valve (SKU ``GCS``).

The valve has no bare on/off and no "run my default": every direct write
(``solowritesystem``) specifies the whole valve state as two command words. The default
shower is started by sending preset 1's words as such a write
(``valve_hex.preset_words_to_pair``). ``controlpresetorexperience`` starts a stored
preset or experience by itself, with no valve write. See
``docs/protocol/gcs_valve.md`` §3.
"""

from __future__ import annotations

import logging
from typing import Any

from .client import KohlerClient, KohlerError
from .const import (
    GCS_CONTROL_PRESET,
    GCS_CREATE_PRESET,
    GCS_RESET_RESTART,
    GCS_SOLOWRITESYSTEM,
    GCS_VALVE_RESET,
    GCS_WARMUP,
    GCS_WRITE_OUTLET_CONFIG,
    GCS_WRITE_PRESET,
    GCS_WRITE_UI_CONFIG,
    SKU_GCS,
)
from .models import DEFAULT_VALVE_MODEL, ValveModel, get_valve_model
from .state import OutletLimits
from .valve_hex import (
    FLOW_BYTE_MAX,
    FLOW_BYTE_MIN,
    TEMPERATURE_MAX_TENTHS,
    UNUSED_VALVE_WORD,
    VALVE1_PREFIX,
    VALVE2_PREFIX,
    ValveHexError,
    encode_pair,
    encode_word,
    pause_pair,
    stop_pair,
    unit_to_celsius,
)

_LOGGER = logging.getLogger(__name__)


# The payload carries eight valve slots. Only the first two are ever populated; the rest
# must still be present and zeroed.
SECONDARY_SLOTS = tuple(f"secondaryValve{n}" for n in range(2, 8))

#: What a preset sends for a valve it does not use, as the app does on both
#: `createpreset` and `writepreset`.
UNUSED_PRESET_WORD = "000000"


def validate_outlet_record(
    limits: OutletLimits,
    *,
    maximum_run_time: int | None = None,
    maximum_temperature_tenths: int | None = None,
    default_temperature_tenths: int | None = None,
) -> dict[str, Any]:
    """Build the record `GcsDevice.async_write_outlet_config` sends.

    ``limits`` is the outlet's current record and the named fields override it, in wire
    units as `OutletLimits` holds them. Raises `KohlerError` for every refusal of that
    write, so a caller writing several outlets (one call each) can check them all before
    sending any.
    """
    run_time = limits.maximum_run_time if maximum_run_time is None else maximum_run_time
    maximum = (
        limits.maximum_temperature_tenths
        if maximum_temperature_tenths is None
        else maximum_temperature_tenths
    )
    default = (
        limits.default_temperature_tenths
        if default_temperature_tenths is None
        else default_temperature_tenths
    )
    record = {
        "outLetId": limits.outlet_id,
        "outLetType": limits.outlet_type,
        "outLetFlags": limits.outlet_flags,
        "minimumOutletTemperature": limits.minimum_temperature_tenths,
        "defaultOutletTemperature": default,
        "maximumOutletTemperature": maximum,
        "minimumFlowrate": limits.minimum_flow_byte,
        "defaultFlowrate": limits.default_flow_byte,
        "maximumFlowrate": limits.maximum_flow_byte,
        "maximumRuntime": run_time,
    }
    # Refuse rather than guess: the write replaces the whole record, so a field never
    # read (None) cannot be invented without changing a setting nobody asked to touch.
    # The only defaults are the two `async_write_outlet_config` adds, which the app
    # always sends.
    missing = sorted(key for key, value in record.items() if value is None)
    if missing:
        raise KohlerError(
            f"Refusing to write outlet {limits.outlet_id}: {', '.join(missing)} "
            "has not been read from the valve, and this endpoint replaces the whole "
            "record. Reload the integration to re-read the outlet configuration."
        )
    # Absolute ranges first: most values are echoed from a read, and a corrupt read must
    # not become the scald limit or flow envelope. The temperature ceiling is the app's
    # clamp for this write (`docs/protocol/gcs_valve.md` §2.1); flow is the byte range.
    for key, low, high, unit in (
        ("minimumOutletTemperature", 0, TEMPERATURE_MAX_TENTHS, "tenths of °C"),
        ("defaultOutletTemperature", 0, TEMPERATURE_MAX_TENTHS, "tenths of °C"),
        ("maximumOutletTemperature", 0, TEMPERATURE_MAX_TENTHS, "tenths of °C"),
        ("minimumFlowrate", FLOW_BYTE_MIN, FLOW_BYTE_MAX, "flow bytes"),
        ("defaultFlowrate", FLOW_BYTE_MIN, FLOW_BYTE_MAX, "flow bytes"),
        ("maximumFlowrate", FLOW_BYTE_MIN, FLOW_BYTE_MAX, "flow bytes"),
    ):
        if not low <= record[key] <= high:
            raise KohlerError(
                f"Refusing to write outlet {limits.outlet_id}: {key} {record[key]} is "
                f"outside {low}-{high} {unit}, the range this write accepts."
            )
    if default > maximum:
        raise KohlerError(
            f"Default temperature {default / 10:.1f} °C is above the scald limit "
            f"{maximum / 10:.1f} °C for outlet {limits.outlet_id}."
        )
    return record


class GcsDevice:
    """Command surface for one Anthem digital valve."""

    def __init__(
        self,
        client: KohlerClient,
        device_id: str,
        temperature_unit: str = "Fahrenheit",
        model: ValveModel | str = DEFAULT_VALVE_MODEL,
    ) -> None:
        self._client = client
        self.device_id = device_id
        self.temperature_unit = temperature_unit
        # Decides how many outlets exist and which valve each sits on (see models.py).
        self.model = model if isinstance(model, ValveModel) else get_valve_model(model)

    @property
    def outlet_count(self) -> int:
        """How many physical outlets this valve has."""
        return self.model.total_outlets

    # ------------------------------------------------------------------ #
    # Raw valve writes
    # ------------------------------------------------------------------ #
    async def async_write_valves(self, valve1: str, valve2: str) -> Any:
        """Send a pair of already-encoded valve command words (``solowritesystem``).

        Every direct write in this class ends here.
        """
        payload = {
            "deviceId": self.device_id,
            "sku": SKU_GCS,
            "tenantId": await self._client.async_tenant_id(),
            "gcsValveControlModel": {
                "primaryValve1": valve1.upper(),
                "secondaryValve1": valve2.upper(),
                **{slot: UNUSED_VALVE_WORD for slot in SECONDARY_SLOTS},
            },
        }
        return await self._client.async_request(
            "POST", GCS_SOLOWRITESYSTEM, json_body=payload
        )

    async def async_turn_on(
        self,
        outlets: list[bool],
        temperature: float,
        flow_percent: float = 100,
    ) -> Any:
        """Open the given outlets at a temperature and flow; runs water.

        ``outlets`` has one flag per physical outlet on this model. ``temperature`` is
        in the account's unit (converted here; the valve word is always Celsius) and
        ``flow_percent`` is a percentage of maximum flow.
        """
        if len(outlets) != self.model.total_outlets:
            raise ValveHexError(
                f"{self.model.sku} has {self.model.total_outlets} outlets, "
                f"got {len(outlets)} flags"
            )
        celsius = unit_to_celsius(temperature, self.temperature_unit)
        valve1, valve2 = encode_pair(self.model, celsius, flow_percent, outlets)
        return await self.async_write_valves(valve1, valve2)

    async def async_turn_off(self, temperature: float | None = None) -> Any:
        """Stop every outlet on both valves.

        Sends mask ``0x00`` in an otherwise valid word, never the all-zero
        :data:`~.valve_hex.UNUSED_VALVE_WORD`, which the valve ignores as
        ``primaryValve1``.
        """
        celsius = (
            unit_to_celsius(temperature, self.temperature_unit)
            if temperature is not None
            else 38.0
        )
        valve1, valve2 = stop_pair(self.model, celsius)
        return await self.async_write_valves(valve1, valve2)

    async def async_restart(self) -> Any:
        """Reboot the valve (the app's Restart Product); stops any running water.

        Sends ``valvereset`` with ``reset: "productRestart"``, as the app does. It
        cannot revive a valve that has lost its cloud link, which never receives it.
        """
        payload = {
            "deviceId": self.device_id,
            "sku": SKU_GCS,
            "tenantId": await self._client.async_tenant_id(),
            "reset": GCS_RESET_RESTART,
        }
        return await self._client.async_request(
            "POST", GCS_VALVE_RESET, json_body=payload
        )

    async def async_pause(self, temperature: float | None = None) -> Any:
        """Pause both valves, holding the session open (mask ``0x40``)."""
        celsius = (
            unit_to_celsius(temperature, self.temperature_unit)
            if temperature is not None
            else 38.0
        )
        valve1, valve2 = pause_pair(self.model, celsius)
        return await self.async_write_valves(valve1, valve2)

    async def async_set_outlet_mask(
        self,
        valve1_mask: int,
        valve2_mask: int,
        temperature: float,
        flow_percent: float = 100,
    ) -> Any:
        """Write explicit per-valve outlet masks, for callers that already have them.

        A single-zone model sends :data:`~.valve_hex.UNUSED_VALVE_WORD` for valve 2, and
        a nonzero valve-2 mask raises `ValveHexError` rather than being dropped.
        """
        if valve2_mask and not self.model.uses_valve2:
            raise ValveHexError(
                f"{self.model.sku} has one zone; valve 2 mask 0x{valve2_mask:02X} "
                "addresses outlets it does not have"
            )
        celsius = unit_to_celsius(temperature, self.temperature_unit)
        valve1 = encode_word(VALVE1_PREFIX, celsius, flow_percent, valve1_mask)
        if not self.model.uses_valve2:
            return await self.async_write_valves(valve1, UNUSED_VALVE_WORD)
        return await self.async_write_valves(
            valve1, encode_word(VALVE2_PREFIX, celsius, flow_percent, valve2_mask)
        )

    # ------------------------------------------------------------------ #
    # Presets
    # ------------------------------------------------------------------ #
    async def async_activate_preset(self, preset_id: Any, on: bool = True) -> Any:
        """Start or stop a preset or experience (ids 17 and up). Runs water when on.

        One call; the valve runs the stored program and no valve write follows. The body
        is ``{preset, action}``: ``presetOrExperienceId`` here is accepted and ignored
        (``docs/protocol/gcs_valve.md`` §3).
        """
        payload = {
            "deviceId": self.device_id,
            "sku": SKU_GCS,
            "tenantId": await self._client.async_tenant_id(),
            "preset": str(preset_id),
            "action": "On" if on else "Off",
        }
        return await self._client.async_request(
            "POST", GCS_CONTROL_PRESET, json_body=payload
        )

    async def async_write_preset(
        self,
        preset_id: Any,
        name: str,
        valves: dict[int, str],
        *,
        time_seconds: int = 1800,
        volume: str = "0",
    ) -> Any:
        """Overwrite an existing preset (a whole-record replace).

        ``valves`` maps a 1-based valve number to a 3-byte preset word
        (:func:`~.valve_hex.encode_preset_word`); unlisted valves get
        :data:`UNUSED_PRESET_WORD`. ``time_seconds`` is the preset's own run-time limit.
        The wrapper, its ``presetId`` and 3-byte words are all required: without any of
        them the call still succeeds and changes nothing
        (``docs/protocol/gcs_valve.md`` §3).
        """
        model = {
            "presetId": str(preset_id),
            "name": name,
            "time": str(time_seconds),
            "volume": volume,
        }
        model.update(self._valve_fields(valves))
        payload = {
            "deviceId": self.device_id,
            "sku": SKU_GCS,
            "tenantId": await self._client.async_tenant_id(),
            "gcsPresetControlModel": model,
        }
        return await self._client.async_request(
            "POST", GCS_WRITE_PRESET, json_body=payload
        )

    async def async_write_outlet_config(
        self,
        limits: OutletLimits,
        *,
        maximum_run_time: int | None = None,
        maximum_temperature_tenths: int | None = None,
        default_temperature_tenths: int | None = None,
    ) -> Any:
        """Replace one outlet's configuration record. **This writes a scald limit.**

        `writeoutletconfig` has no partial form: all twelve keys are sent and become the
        record. So this takes the outlet's current ``limits`` and overrides only the
        named fields (seconds, tenths of °C); everything else is echoed as read,
        including the minimum temperature and flow the app would force. ``maxVolume``
        and ``purge`` are echoed when reported, else sent as the app sends them
        (``"0"``, ``""``).

        ⚠️ A missing key, a read-side spelling or display units all succeed and silently
        corrupt the record (``docs/protocol/gcs_valve.md`` §2.1): send `OutletLimits`'s
        wire values under the write keys. Raises `KohlerError` for every refusal in
        `validate_outlet_record`.

        A 201 means accepted, not applied (``docs/protocol/platform.md`` §2.1): verify
        by reading back after a few seconds.
        """
        record = validate_outlet_record(
            limits,
            maximum_run_time=maximum_run_time,
            maximum_temperature_tenths=maximum_temperature_tenths,
            default_temperature_tenths=default_temperature_tenths,
        )
        payload = {
            "deviceId": self.device_id,
            "sku": SKU_GCS,
            "tenantId": await self._client.async_tenant_id(),
            # Every value is a string on the wire.
            "gcsOutletConfigControlModel": {
                **{key: str(value) for key, value in record.items()},
                "maxVolume": "0" if limits.max_volume is None else limits.max_volume,
                "purge": "" if limits.purge is None else limits.purge,
            },
        }
        return await self._client.async_request(
            "POST", GCS_WRITE_OUTLET_CONFIG, json_body=payload
        )

    async def async_write_ui_config(self, record: dict[str, str]) -> Any:
        """Replace one touch panel's settings record (`gcs_valve.md` §6).

        ``record`` is whole, as `ui_config.control_panel_writes` builds it: the write has no
        partial form.
        """
        payload = {
            "deviceId": self.device_id,
            "sku": SKU_GCS,
            "tenantId": await self._client.async_tenant_id(),
            "gcsUIConfigModel": record,
        }
        return await self._client.async_request(
            "POST", GCS_WRITE_UI_CONFIG, json_body=payload
        )

    async def async_create_preset(
        self,
        name: str,
        valves: dict[int, str],
        *,
        time_seconds: int = 1800,
        volume: str = "0",
    ) -> Any:
        """Create a new preset. Flat body, no wrapper and no ``presetId``."""
        payload = {
            "deviceId": self.device_id,
            "sku": SKU_GCS,
            "tenantId": await self._client.async_tenant_id(),
            "name": name,
            "time": str(time_seconds),
            "volume": volume,
        }
        payload.update(self._valve_fields(valves))
        return await self._client.async_request(
            "POST", GCS_CREATE_PRESET, json_body=payload
        )

    @staticmethod
    def _valve_fields(valves: dict[int, str]) -> dict[str, str]:
        """The valve1..valve8 fields, :data:`UNUSED_PRESET_WORD` where unused."""
        fields = {}
        for number in range(1, 9):
            word = valves.get(number)
            fields[f"valve{number}"] = word.lower() if word else UNUSED_PRESET_WORD
        return fields

    # ------------------------------------------------------------------ #
    # Warmup
    # ------------------------------------------------------------------ #
    async def async_set_warmup(self, mode: str) -> Any:
        """Set the warmup mode (``docs/protocol/gcs_valve.md`` §5). Does not run water.

        Warmup is a stored mode, not a run-now command; turning it off means writing
        ``warmUpDisabled``. ``mode`` is validated by the caller
        (``WarmupManager.async_set_warmup``), which also refuses while water runs, as
        the app does.

        Raises `ValueError` for an empty mode: the cloud returns 200 for it and the
        valve ignores it, so the failure would look like success.
        """
        if not mode or not mode.strip():
            raise ValueError(
                "warmUp mode is required: an empty mode is accepted by Kohler's cloud with "
                "HTTP 200 and then ignored by the valve"
            )
        payload = {
            "deviceId": self.device_id,
            "sku": SKU_GCS,
            "tenantId": await self._client.async_tenant_id(),
            "warmUp": mode,
        }
        return await self._client.async_request("POST", GCS_WARMUP, json_body=payload)
