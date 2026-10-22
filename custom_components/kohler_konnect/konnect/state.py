"""Device state assembled from the MQTT stream and REST snapshots.

Each device type keeps its own state object. They are fed from two directions:

* ``apply_envelope()`` for live MQTT updates, and
* ``apply_rest_state()`` at startup, because MQTT says nothing until the shower next
  changes.

Pure data, with no Home Assistant imports, so they can be exercised offline against
captured payloads.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Container
from dataclasses import dataclass, field, replace
from typing import Any

from .const import (
    HUB_STEAM_POWERCLEAN,
    MSG_GCS_DISPENSED_VOLUME,
    MSG_GCS_OUTLET_CONFIG,
    MSG_GCS_PRESET_STATUS,
    MSG_GCS_SOLO_STATUS,
    MSG_GCS_WARMUP_STATUS,
    MSG_HUB_EXPERIENCE_CODES,
    MSG_HUB_FAVORITE,
    MSG_HUB_FAVORITES_SNAPSHOT,
    MSG_HUB_LIGHT,
    MSG_HUB_MUSIC,
    MSG_HUB_SHOWER_VALVE,
    MSG_HUB_STEAM,
    SKU_GCS,
    SKU_HUB,
    SYSTEM_STATE_ERROR,
    SYSTEM_STATE_FIRMWARE,
    WARMUP_IN_PROGRESS,
)
from .hub import outlet_flags, zone_number
from .models import ValveModel
from .mqtt import Envelope
from .valve_hex import (
    FLOW_BYTE_MAX,
    FLOW_BYTE_MIN,
    ValveHexError,
    ValveWord,
    celsius_to_unit,
    decode_word,
)

_LOGGER = logging.getLogger(__name__)


def _is_warmup_in_progress(value: object) -> bool:
    """Whether a warmup status field means warmup is actually running.

    Compare the whole value, never a suffix: ``warmUpNotInProgress`` ends with
    ``InProgress``.
    """
    return str(value).strip().lower() == WARMUP_IN_PROGRESS.lower()


def _flag(value: object) -> bool | None:
    """Normalise the HUB's ``"0"``/``"1"`` string flags; None when absent or unknown.

    Anything unrecognised is None, so the ``null`` ``showerwarmup`` on a non-shower
    message cannot clear a running warm-up. Never use ``bool()``: ``bool("0")`` is True.
    """
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "on"}:
        return True
    if text in {"0", "false", "off"}:
        return False
    return None


def _preset_id_or_none(value: object) -> int | None:
    """Normalise ``presetOrExperienceId``: ``"0"`` means no preset, so None."""
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number or None


#: The preset valve slots that are zones, ``Valve1`` and ``Valve2``. The later slots of
#: a push hold junk (``docs/protocol/gcs_valve.md`` §7).
PRESET_ZONE_SLOTS = 2


def _preset_words(by_valve: dict[int, Any]) -> tuple[str, ...]:
    """A preset's 3-byte valve words in valve order — index 0 is ``Valve1``.

    ``by_valve`` maps a 1-based valve number to the word as read. Both zone slots are
    kept whatever the model, which may not be known yet when presets are read.
    Upper-cased; a missing word is ``""`` so the other keeps its place; ``()`` when
    neither has one.

    Not validated here: `check_preset_word` refuses a malformed word where it becomes a
    command. Blanking it here would quietly run the other zone alone.
    """
    words = tuple(
        str(by_valve.get(number) or "").strip().upper()
        for number in range(1, PRESET_ZONE_SLOTS + 1)
    )
    return words if any(words) else ()


def _zone_status(value: object) -> str | None:
    """A HUB zone's ``status``, upper-cased, as ``HubState.is_running`` compares it."""
    return None if value is None else str(value).strip().upper()


def _text(item: dict[str, Any], key: str) -> str | None:
    """A field echoed back verbatim on a write: its string form, or None when absent."""
    value = item.get(key)
    return None if value is None else str(value)


def _finite(value: object, scale: float = 1) -> float | None:
    """``value`` as a number times ``scale``, or None unless that is a finite number.

    NaN and infinity (which ``float`` and the JSON parser both accept) read as
    unparseable, so they never reach `encode_word`. Checked after scaling, which can
    overflow.
    """
    try:
        number = float(str(value)) * scale
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _rounded(value: object, scale: float = 1) -> int | None:
    """`_finite`, rounded to the whole number the outlet records store."""
    number = _finite(value, scale)
    return None if number is None else round(number)


def outlet_limits_from_settings(payload: Any) -> dict[int, OutletLimits]:
    """Read per-outlet limits from a ``gcsadvancestate`` response.

    The same records ``READ_GCS_OUTLET_CONFIG_CFG`` pushes, but readable on demand. REST
    uses its own key spelling and display units (flow 0-50, °C); both are converted to
    the wire units `OutletLimits` holds (``docs/protocol/gcs_valve.md`` §2.1). Outlets
    that cannot be parsed are skipped rather than guessed at.
    """
    limits: dict[int, OutletLimits] = {}
    # Accepts the whole response or just its `setting` block, which
    # `KohlerClient.async_get_gcs_settings` already unwraps.
    source = payload if isinstance(payload, dict) else {}
    if "valveSettings" not in source:
        source = (
            source.get("setting") if isinstance(source.get("setting"), dict) else {}
        )
    if not isinstance(source, dict):
        return limits
    settings = source.get("valveSettings")
    for valve in settings if isinstance(settings, list) else ():
        if not isinstance(valve, dict):
            continue
        configurations = valve.get("outletConfigurations")
        for entry in configurations if isinstance(configurations, list) else ():
            if not isinstance(entry, dict):
                continue
            try:
                outlet_id = int(str(entry.get("outLetId")))
            except (TypeError, ValueError):
                continue

            def _flow(key: str, item: dict = entry) -> int | None:
                """Display flow -> byte scale, the units OutletLimits is defined in."""
                return _rounded(item.get(key), 4)

            try:
                run_time: int | None = int(str(entry.get("maximumRuntime")))
            except (TypeError, ValueError):
                run_time = None
            low, high = _flow("minimumFlowrate"), _flow("maximumFlowrate")
            if low is None or high is None:
                continue
            try:
                outlet_type: int | None = int(str(entry.get("outLetType")))
            except (TypeError, ValueError):
                outlet_type = None
            # REST reports this in display °C (`45`, `47.8`); stored in tenths.
            max_temperature = _rounded(entry.get("maximumOutletTemperature"), 10)

            def _tenths(key: str, item: dict = entry) -> int | None:
                """Display °C -> tenths, the scale this model stores temperatures in."""
                return _rounded(item.get(key), 10)

            try:
                outlet_flags: int | None = int(str(entry.get("outLetFlags")))
            except (TypeError, ValueError):
                outlet_flags = None
            limits[outlet_id] = OutletLimits(
                outlet_id,
                low,
                high,
                run_time,
                _flow("defaultFlowrate"),
                outlet_type,
                max_temperature,
                _tenths("minimumOutletTemperature"),
                _tenths("defaultOutletTemperature"),
                outlet_flags,
                _text(entry, "maxVolume"),
                _text(entry, "purge"),
            )
    return limits


@dataclass(frozen=True)
class OutletLimits:
    """One outlet's configuration record, in wire units (flow byte, tenths of °C, s).

    Also the whole record ``writeoutletconfig`` replaces, so a write can echo every
    field it does not mean to change. The flow bounds are the app's slider limits and
    are not always 16-200. A None field has not been learned yet; never assume one.
    """

    outlet_id: int
    minimum_flow_byte: int
    maximum_flow_byte: int
    # `maximumRunTime`, seconds: set per outlet but timed per zone, and a zone's outlets
    # can disagree (`docs/protocol/gcs_valve.md` §4.1).
    maximum_run_time: int | None = None
    # `defaultFlowRate`, the outlet's starting flow (byte).
    default_flow_byte: int | None = None
    # `outLetType`, the fixture code (`docs/protocol/gcs_valve.md` §1.2). A label only:
    # the valve derives no behaviour from it. Named in the HA layer
    # (`OUTLET_TYPE_NAMES`).
    outlet_type: int | None = None
    # ⚠️ The scald limit, `maximumOutletTemperature`, tenths of °C. A setting that can
    # change at any time, not a hardware limit: never cache it as a device property.
    # None means not reported, not "no limit".
    maximum_temperature_tenths: int | None = None
    # Tenths of °C.
    minimum_temperature_tenths: int | None = None
    # The app's "Default Temperature", tenths of °C, between the minimum and the scald
    # limit.
    default_temperature_tenths: int | None = None
    # `outLetFlags`: meaning unknown, only echoed back on a write, as the app does.
    outlet_flags: int | None = None
    # Kept as the strings they arrive as, since they are only echoed. Usually never
    # reported; see `GcsDevice.async_write_outlet_config` for what a write sends then.
    max_volume: str | None = None
    purge: str | None = None


@dataclass(frozen=True)
class GcsPreset:
    """One stored preset slot.

    ``name`` is empty for a free slot, which is how a deletion is reported.
    ``valve_words`` is as built by `_preset_words`; preset 1's are what the Shower
    switch sends (`preset_words_to_pair`).
    """

    preset_id: int
    name: str
    is_experience: bool = False
    valve_words: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True for a free slot, which has no name."""
        return not self.name.strip()

    @property
    def is_selectable(self) -> bool:
        """Whether this can be offered to a user as a preset to run.

        Experiences share the id space but are offered separately (the Experience
        select).
        """
        return not self.is_empty and not self.is_experience


@dataclass
class GcsState:
    """Live state of one Anthem digital valve.

    Outlet, temperature and flow come from the valve command word, the authoritative
    source whenever a GCS device exists.
    """

    model: ValveModel
    temperature_unit: str = "Fahrenheit"

    valve1: ValveWord | None = None
    valve2: ValveWord | None = None
    warmup_mode: str | None = None
    warmup_in_progress: bool | None = None
    # Not exposed: it changes erratically and is not a monotonic counter.
    total_volume: str | None = None
    # ⚠️ `totalFlow`, raw, for diagnostics only. Not a water meter: it cycles among
    # values 4x apart with no water running (`docs/protocol/gcs_valve.md` §2), so no
    # scaling makes it one. Water use comes from `gcs-usage`
    # (`ValveMonthlyWaterSensor`).
    total_flow: float | None = None
    # `currentSystemState`, the device's own string (`docs/protocol/gcs_valve.md` §7).
    # Not folded into the `Status` vocabulary: it is the valve's session flag,
    # independent of the command word, and the two can disagree. See `has_fault`,
    # `firmware_updating`.
    system_state: str | None = None
    # `READ_DISPENSED_WATER_VOLUME_STS` volume, raw, for diagnostics. Arrives only when
    # something sends `bathfillervolume`, so usually None
    # (`docs/protocol/gcs_valve.md` §8).
    dispensed_volume: str | None = None
    last_update: float | None = None

    # Stored presets keyed by slot id. Ids are slots, not positions: a delete empties
    # one in place (an empty name) and a create fills the lowest free one.
    presets: dict[int, GcsPreset] = field(default_factory=dict)
    # The preset or experience driving the valve, from `presetOrExperienceId`. Answers
    # "is a preset driving this", never "is water running": cleared by pause and stop,
    # and None for a direct write. ⚠️ Never set during warm-up, so None then means
    # unknown (`docs/protocol/gcs_valve.md` §7).
    active_preset_id: int | None = None
    # Outlet records keyed by 0-based `outLetId`. MQTT fills them one outlet at a time,
    # so this may be partial: every reader must tolerate a missing entry.
    outlet_limits: dict[int, OutletLimits] = field(default_factory=dict)
    # The fields the last applied message carried, so `Valve.apply_rest_reply` can keep
    # them over a REST read that message overtook.
    carried: frozenset[str] = field(default=frozenset(), compare=False)

    def _flags(self, *, flowing: bool) -> list[bool]:
        words = {1: self.valve1, 2: self.valve2}
        result: list[bool] = []
        for outlet in range(1, self.model.total_outlets + 1):
            valve_number, bit = self.model.outlet_location(outlet)
            word = words.get(valve_number)
            assigned = bool(word and word.outlet(bit))
            if flowing and word is not None and word.paused:
                assigned = False
            result.append(assigned)
        return result

    def zone_outlets(self, zone: int, *, flowing: bool = True) -> list[bool]:
        """Outlet flags for one zone, indexed from 0 within that zone.

        ``flowing=False`` returns the assignment a paused session will resume to.
        """
        word = self.valve1 if zone == 1 else self.valve2
        count = self.model.outlets_in_zone(zone)
        if word is None:
            return [False] * count
        if flowing and word.paused:
            return [False] * count
        return [word.outlet(bit) for bit in range(count)]

    def zone_word(self, zone: int) -> ValveWord | None:
        """The decoded command word for one zone."""
        return self.valve1 if zone == 1 else self.valve2

    @property
    def outlets(self) -> list[bool]:
        """Per-outlet flags for outlets actually flowing water.

        A paused valve keeps its assignment in byte 3 but runs no water, so this clears
        it while paused. Use :attr:`assigned_outlets` for what the session resumes to.
        """
        return self._flags(flowing=True)

    @property
    def assigned_outlets(self) -> list[bool]:
        """Per-outlet flags in byte 3, ignoring pause: what the session resumes to."""
        return self._flags(flowing=False)

    @property
    def is_running(self) -> bool:
        """True when water is actually flowing from any outlet."""
        return any(self.outlets)

    @property
    def is_paused(self) -> bool:
        """True when the session is held: a pause flag set and no water flowing."""
        words = [w for w in (self.valve1, self.valve2) if w is not None]
        if not words:
            return False
        return any(w.paused for w in words) and not self.is_running

    @property
    def at_temperature(self) -> bool | None:
        """Whether the system has reached its temperature setpoint.

        System-level, read from the primary valve only: the secondary valve never sets
        this bit, even when only zone 2 is running.
        """
        return None if self.valve1 is None else self.valve1.at_temperature

    @property
    def at_flow(self) -> bool | None:
        """Whether the system has reached its flow setpoint (primary valve, as above).

        Never yet seen set, possibly because flow control was disabled where it was
        tested.
        """
        return None if self.valve1 is None else self.valve1.at_flow

    @property
    def has_fault(self) -> bool | None:
        """Whether the valve reports a fault; None with no word and no error state.

        The app's rule: the error flag on either word, or a ``currentSystemState`` of
        ``error`` in any case (``docs/protocol/gcs_valve.md`` §7). Not
        :attr:`error_codes`: byte 7 reads ``1`` on a healthy valve.
        """
        if self.system_state is not None and (
            self.system_state.strip().lower() == SYSTEM_STATE_ERROR
        ):
            return True
        words = [w for w in (self.valve1, self.valve2) if w is not None]
        if not words:
            return None
        return any(w.error_flag for w in words)

    @property
    def firmware_updating(self) -> bool:
        """Whether the valve reports a firmware install in progress.

        ``currentSystemState == "FirmwareUpdate"``, which the app treats as an install
        running.
        """
        return (self.system_state or "").strip().lower() == (
            SYSTEM_STATE_FIRMWARE.lower()
        )

    @property
    def error_codes(self) -> dict[str, int]:
        """Per-zone error code from the status word's byte 7."""
        codes = {}
        for number, word in ((1, self.valve1), (2, self.valve2)):
            if word is not None and word.error_code is not None:
                codes[f"zone{number}"] = word.error_code
        return codes

    @property
    def temperature(self) -> float | None:
        """Setpoint in the account's unit, taken from valve1."""
        if self.valve1 is None:
            return None
        return celsius_to_unit(self.valve1.temperature_celsius, self.temperature_unit)

    @property
    def flow_percent(self) -> float | None:
        """Flow as a percentage, taken from valve1."""
        return None if self.valve1 is None else self.valve1.flow_percent

    @property
    def flow_is_live(self) -> bool:
        """Whether `flow_percent` is a flow somebody asked for: only while water runs.

        An idle valve's flow byte is not the flow setting — it holds the default
        shower's stored flow (`docs/protocol/gcs_valve.md` §3.4) — so anything
        presenting "the flow setting" must gate on this.
        """
        word = self.valve1
        if word is None:
            return False
        return bool(word.outlet_mask) and not word.paused

    def _accept_total_flow_raw(self, value: object) -> bool:
        """Record `totalFlow` verbatim, for diagnostics. True if it changed."""
        try:
            reading = float(str(value))
        except (TypeError, ValueError):
            return False
        changed = reading != self.total_flow
        self.total_flow = reading
        return changed

    def apply_envelope(self, envelope: Envelope) -> bool:
        """Apply a GCS message. Returns True for every valve message, False otherwise.

        Every valve message advances `last_update`, decoded or not: it means "last heard
        from" (liveness), not "last changed".
        """
        if envelope.sku != SKU_GCS:
            return False
        # Before dispatch: an undecodable message is still proof of life.
        self.last_update = envelope.received_at
        self.carried = frozenset()
        handler = {
            MSG_GCS_SOLO_STATUS: self._apply_solo,
            MSG_GCS_WARMUP_STATUS: self._apply_warmup,
            MSG_GCS_PRESET_STATUS: self._apply_preset,
            MSG_GCS_OUTLET_CONFIG: self._apply_outlet_config,
            MSG_GCS_DISPENSED_VOLUME: self._apply_dispensed_volume,
        }.get(envelope.code)
        if handler is not None:
            handler(envelope)
        # Always True: `last_update` moved, which subsumes the handlers' change flags.
        return True

    def _apply_outlet_config(self, envelope: Envelope) -> bool:
        """Record an outlet record the valve pushed, one outlet per message."""
        changed = False
        for attribute in envelope.attributes:
            if not isinstance(attribute, dict):
                continue
            try:
                outlet_id = int(str(attribute.get("outLetId")))
                low = int(str(attribute.get("minimumFlowRate")))
                high = int(str(attribute.get("maximumFlowRate")))
            except (TypeError, ValueError):
                continue
            try:
                run_time: int | None = int(str(attribute.get("maximumRunTime")))
            except (TypeError, ValueError):
                run_time = None
            try:
                default_flow: int | None = int(str(attribute.get("defaultFlowRate")))
            except (TypeError, ValueError):
                default_flow = None
            try:
                outlet_type: int | None = int(str(attribute.get("outLetType")))
            except (TypeError, ValueError):
                outlet_type = None
            try:
                # MQTT reports wire units (tenths of °C), unlike REST.
                max_temperature: int | None = int(
                    str(attribute.get("maximumOutletTemperature"))
                )
            except (TypeError, ValueError):
                max_temperature = None

            # Read every field of the write record: a field left None here would make
            # the next outlet write refuse (`validate_outlet_record`).
            def _int(key: str, item: dict = attribute) -> int | None:
                try:
                    return int(str(item.get(key)))
                except (TypeError, ValueError):
                    return None

            limits = OutletLimits(
                outlet_id,
                low,
                high,
                run_time,
                default_flow,
                outlet_type,
                max_temperature,
                _int("minimumOutletTemperature"),
                _int("defaultOutletTemperature"),
                _int("outLetFlags"),
                _text(attribute, "maxVolume"),
                _text(attribute, "purge"),
            )
            # Merge, do not replace: a field this message lacks keeps the value already
            # learned, for the same reason.
            known = self.outlet_limits.get(outlet_id)
            if known is not None:
                limits = replace(
                    limits,
                    **{
                        field: getattr(known, field)
                        for field in (
                            "maximum_run_time",
                            "default_flow_byte",
                            "outlet_type",
                            "maximum_temperature_tenths",
                            "minimum_temperature_tenths",
                            "default_temperature_tenths",
                            "outlet_flags",
                            "max_volume",
                            "purge",
                        )
                        if getattr(limits, field) is None
                    },
                )
            if known != limits:
                self.outlet_limits[outlet_id] = limits
                changed = True
        return changed

    def _apply_dispensed_volume(self, envelope: Envelope) -> bool:
        """Record the bath-fill volume counter, raw. Diagnostic only."""
        for attribute in envelope.attributes:
            if isinstance(attribute, dict) and attribute.get("volume") is not None:
                volume = str(attribute.get("volume"))
                changed = volume != self.dispensed_volume
                self.dispensed_volume = volume
                return changed
        return False

    def zone_flow_limits(self, zone: int) -> tuple[int, int]:
        """Flow bounds for a zone as byte values, falling back to the constants.

        Taken from the zone's first outlet, as the app bounds its slider. A zone the
        model lacks also gets the constants: the model can shrink at runtime, and a
        zone 2 Flow entity still asks until the next reload.
        """
        try:
            outlet_id = self.model.outlet_id(zone, 1)
        except ValueError:
            return FLOW_BYTE_MIN, FLOW_BYTE_MAX
        limits = self.outlet_limits.get(outlet_id)
        if limits is None:
            return FLOW_BYTE_MIN, FLOW_BYTE_MAX
        return limits.minimum_flow_byte, limits.maximum_flow_byte

    def _apply_preset(self, envelope: Envelope) -> bool:
        """Apply a pushed preset record (``docs/protocol/gcs_valve.md`` §7).

        A delete arrives as the same ``presetId`` with an empty name. A push without
        ``Valve1``/``Valve2`` keeps the words already held, as it keeps
        ``is_experience``: dropping them would leave the Shower switch with nothing to
        send.
        """
        changed = False
        slots = range(1, PRESET_ZONE_SLOTS + 1)
        for attribute in envelope.attributes:
            if not isinstance(attribute, dict):
                continue
            raw_id = attribute.get("presetId")
            try:
                preset_id = int(str(raw_id))
            except (TypeError, ValueError):
                continue
            held = self.presets.get(preset_id)
            if any(f"Valve{n}" in attribute for n in slots):
                valve_words = _preset_words(
                    {n: attribute.get(f"Valve{n}") for n in slots}
                )
            else:
                valve_words = held.valve_words if held is not None else ()
            preset = GcsPreset(
                preset_id=preset_id,
                name=str(attribute.get("name") or "").strip(),
                # The push carries no experience flag; keep what the REST list said.
                is_experience=held.is_experience if held is not None else False,
                valve_words=valve_words,
            )
            if self.presets.get(preset_id) != preset:
                self.presets[preset_id] = preset
                changed = True
        return changed

    def _apply_solo(self, envelope: Envelope) -> bool:
        attribute = envelope.attribute(MSG_GCS_SOLO_STATUS) or (
            envelope.attributes[0] if envelope.attributes else None
        )
        if attribute is None:
            return False
        # Each word is guarded on its own, and neither guards the rest of the message:
        # an absent or garbled word keeps its zone as it was (blanking it could close a
        # running zone), and the session fields below apply whatever the words say.
        # Don't make it pair-or-nothing: `Valve._valve_baseline` narrows zone by zone,
        # so half a pair is safe, and dropping the good half would lose a real report
        # such as a panel Stop. A one-zone model reads `primaryValve1` alone.
        carried: set[str] = set()
        try:
            valve1 = decode_word(str(attribute.get("primaryValve1") or ""))
            carried.add("valve1")
        except ValveHexError:
            _LOGGER.debug("Keeping zone 1's last word: %s has none", envelope.code)
            valve1 = self.valve1
        valve2 = None
        if self.model.uses_valve2:
            try:
                valve2 = decode_word(str(attribute.get("secondaryValve1") or ""))
                carried.add("valve2")
            except ValveHexError:
                _LOGGER.debug("Keeping zone 2's last word: %s has none", envelope.code)
                valve2 = self.valve2

        changed = (valve1, valve2) != (self.valve1, self.valve2)
        self.valve1, self.valve2 = valve1, valve2
        if (volume := attribute.get("totalVolume")) is not None:
            changed |= volume != self.total_volume
            self.total_volume = volume
            carried.add("total_volume")
        if (flow_total := attribute.get("totalFlow")) is not None:
            changed |= self._accept_total_flow_raw(flow_total)
            carried.add("total_flow")
        if (system := attribute.get("currentSystemState")) is not None:
            system = str(system)
            changed |= system != self.system_state
            self.system_state = system
            carried.add("system_state")
        if (status := attribute.get("warmUpStatus")) is not None:
            in_progress = _is_warmup_in_progress(status)
            changed |= in_progress != self.warmup_in_progress
            self.warmup_in_progress = in_progress
            carried.add("warmup_in_progress")
        if "presetOrExperienceId" in attribute:
            active = _preset_id_or_none(attribute.get("presetOrExperienceId"))
            changed |= active != self.active_preset_id
            self.active_preset_id = active
            carried.add("active_preset_id")
        self.carried = frozenset(carried)
        return changed

    def _apply_warmup(self, envelope: Envelope) -> bool:
        attribute = envelope.attributes[0] if envelope.attributes else None
        if attribute is None:
            return False
        # MQTT spells the key `warmup`, REST `warmUp` (`docs/protocol/gcs_valve.md` §7).
        mode = (
            attribute.get("warmup")
            or attribute.get("warmUp")
            or attribute.get("warmUpMode")
        )
        changed = mode is not None and mode != self.warmup_mode
        if mode is not None:
            self.warmup_mode = str(mode)
            self.carried = frozenset({"warmup_mode"})
        return changed

    def apply_rest_state(self, payload: dict[str, Any]) -> None:
        """Seed from a ``gcs-state`` read, so entities are populated before any event.

        Every container is type-checked (``or {}`` would pass a list or string through
        and fail setup); a malformed one keeps the defaults, which MQTT corrects later.
        """
        state = payload.get("state") if isinstance(payload, dict) else None
        if not isinstance(state, dict):
            return
        for number, attr in ((1, "valve1"), (2, "valve2")):
            valve = state.get(attr)
            if not isinstance(valve, dict):
                continue
            mask = 0
            for bit, key in enumerate(("out1", "out2", "out3")):
                if str(valve.get(key)) == "1":
                    mask |= 1 << bit
            # A setpoint or flow that is absent, junk or non-finite takes a fallback.
            celsius = _finite(valve.get("temperatureSetpoint"))
            if celsius is None:
                celsius = 38.0
            # gcs-state reports flow on the device's own 0-50 scale.
            percent = _finite(valve.get("flowSetpoint"), 2)
            if percent is None:
                percent = 0.0
            word = ValveWord(
                prefix=1 if number == 1 else 0x11,
                temperature_celsius=celsius,
                flow_percent=percent,
                outlet_mask=mask,
                paused=str(valve.get("pauseFlag")) == "1",
            )
            if number == 1:
                self.valve1 = word
            elif self.model.uses_valve2:
                self.valve2 = word

        warm = state.get("warmUpState")
        if not isinstance(warm, dict):
            warm = {}
        self.warmup_mode = warm.get("warmUp") or self.warmup_mode
        progress = warm.get("state")
        if progress is not None:
            self.warmup_in_progress = _is_warmup_in_progress(progress)
        self.total_volume = state.get("totalVolume") or self.total_volume
        if (flow_total := state.get("totalFlow")) is not None:
            self._accept_total_flow_raw(flow_total)
        if (system := state.get("currentSystemState")) is not None:
            self.system_state = str(system)
        if "presetOrExperienceId" in state:
            self.active_preset_id = _preset_id_or_none(
                state.get("presetOrExperienceId")
            )
        self.last_update = time.time()

    def apply_preset_list(self, payload: dict[str, Any]) -> bool:
        """Seed every preset slot from a ``gcs-preset`` read. Returns True if changed.

        Replaces the whole mapping rather than merging, so a preset deleted while Home
        Assistant was not listening disappears instead of lingering.
        """
        details = (
            payload.get("gcsPresetExperienceDetails")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(details, list):
            return False
        presets: dict[int, GcsPreset] = {}
        for entry in details:
            if not isinstance(entry, dict):
                continue
            try:
                preset_id = int(str(entry.get("presetId")))
            except (TypeError, ValueError):
                continue
            # `valveDetails[{valveIndex: "Valve1", hexString}]`, keyed by its index so
            # the list's order does not matter; an entry with an odd index is skipped.
            by_valve: dict[int, Any] = {}
            valve_details = entry.get("valveDetails")
            for detail in valve_details if isinstance(valve_details, list) else ():
                if not isinstance(detail, dict):
                    continue
                index = str(detail.get("valveIndex") or "").strip()
                if index.lower().startswith("valve") and index[5:].isdigit():
                    by_valve[int(index[5:])] = detail.get("hexString")
            presets[preset_id] = GcsPreset(
                preset_id=preset_id,
                name=str(entry.get("title") or entry.get("logicalName") or "").strip(),
                # Kohler sends this as the string "True"/"False", so `bool(value)` would
                # be true for both.
                is_experience=str(entry.get("isExperience")).strip().lower() == "true",
                valve_words=_preset_words(by_valve),
            )
        if presets == self.presets:
            return False
        self.presets = presets
        return True

    def selectable_presets(self, hidden: Container[int] = ()) -> list[GcsPreset]:
        """Presets a user may choose, lowest slot first.

        Drops empty slots, experiences and anything in ``hidden``. Callers hide
        preset 1, the default shower, as the app does.
        """
        return [
            preset
            for _, preset in sorted(self.presets.items())
            if preset.is_selectable and preset.preset_id not in hidden
        ]

    def experiences(self) -> list[GcsPreset]:
        """Stored experiences, lowest slot first; started like presets."""
        return [
            preset
            for _, preset in sorted(self.presets.items())
            if preset.is_experience and not preset.is_empty
        ]

    def experience_by_name(self, name: str) -> GcsPreset | None:
        """Resolve an experience by name, case-insensitively, at call time."""
        wanted = name.strip().lower()
        for preset in self.experiences():
            if preset.name.strip().lower() == wanted:
                return preset
        return None

    def preset_by_name(
        self, name: str, hidden: Container[int] = ()
    ) -> GcsPreset | None:
        """Resolve a preset by name, case-insensitively, at call time.

        Never cache the id: a deleted slot is reused for a different preset. Takes the
        same ``hidden`` set as :meth:`selectable_presets`, so a hidden preset cannot be
        reached by name.
        """
        wanted = name.strip().lower()
        for preset in self.selectable_presets(hidden=hidden):
            if preset.name.strip().lower() == wanted:
                return preset
        return None


def _light_key(attribute: dict[str, Any]) -> str:
    """One light group's key, the same whichever surface named it.

    MQTT names a group by ``component`` (``lightgroupA``) and REST by ``name``
    (``groupA``, or a display name); both reduce to the bare letter.
    """
    raw = str(attribute.get("component") or attribute.get("name") or "light")
    text = raw.strip().lower().replace(" ", "").replace("_", "")
    for prefix in ("lightgroup", "group", "light"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :]
    return text


@dataclass
class HubZone:
    """One HUB water zone, which maps to one valve."""

    status: str | None = None
    outlets: list[bool] = field(default_factory=list)
    temperature: Any = None
    flowrate: Any = None


@dataclass
class HubState:
    """Live state of one Anthem Plus system controller.

    Its view of valve-driven sessions is unreliable; on an account with a GCS device,
    read outlets from the valve (:func:`~.models.resolve_outlet_source`).
    """

    model: ValveModel

    zones: dict[int, HubZone] = field(default_factory=dict)
    music_on: bool | None = None
    steam_on: bool | None = None
    # `STEAM_STS` `status` as sent — `ON`, `OFF`, or `POWERCLEAN` while the generator
    # cleans itself. `steam_on` stays a strict on/off; this keeps the third state
    # visible.
    steam_status: str | None = None
    # Steam detail from `STEAM_STS` / `hub-state.hubSteamState`, strings as sent (°F,
    # and times as the controller formats them). None until reported.
    steam_temperature: str | None = None
    steam_start_time: str | None = None
    steam_total_time: str | None = None
    # Per light group, keyed by `_light_key` (`a`, `b`, `c`), because `LIGHT_STS`
    # carries one group per message. `light_on` is derived from it.
    lights: dict[str, bool] = field(default_factory=dict)
    # The running controller experience's title, from `SHOWER_EXP_STS` / `STEAM_EXP_STS`
    # / `ICE_SHOWER_EXP_STS`; None when none is running.
    active_experience: str | None = None
    # Fault flags. `error_components` merges `hub-state.errorComponent` (amplifier, hub,
    # light, steam, valve1, valve2) with the per-message `errorstate` the accessory
    # messages carry; `error` is the controller's own top-level `errorState`.
    error: bool | None = None
    error_components: dict[str, bool] = field(default_factory=dict)
    active_favorite_id: str | None = None
    # The running favorite's name, from `FAVORITE_STS`; see `_apply_favorite`.
    active_favorite_name: str | None = None
    favorites: list[dict[str, Any]] = field(default_factory=list)
    last_update: float | None = None
    # Whether the controller is running a warm-up, from `SHOWER_VALVE_STS` `data` (not
    # per zone). Warm-up runs water, so anything presenting this must rank it above
    # "running". None until reported.
    shower_warmup: bool | None = None

    @property
    def outlets(self) -> list[bool]:
        """Per-outlet flags across both zones, in global numbering."""
        flags = list(self.zones.get(1, HubZone()).outlets)
        flags += [False] * max(0, self.model.outlets_valve1 - len(flags))
        if self.model.uses_valve2:
            second = list(self.zones.get(2, HubZone()).outlets)
            second += [False] * max(0, self.model.outlets_valve2 - len(second))
            flags += second
        return flags[: self.model.total_outlets]

    @property
    def is_running(self) -> bool:
        return any(z.status == "ON" for z in self.zones.values())

    @property
    def light_on(self) -> bool | None:
        """Whether **any** light group is on; None until one has reported."""
        if not self.lights:
            return None
        return any(self.lights.values())

    @property
    def steam_powerclean(self) -> bool:
        """Whether the steam generator is running its self-clean (`POWERCLEAN`)."""
        return (self.steam_status or "").upper() == HUB_STEAM_POWERCLEAN

    @property
    def has_fault(self) -> bool | None:
        """Whether the controller or any component reports a fault; None until read."""
        if self.error is None and not self.error_components:
            return None
        return bool(self.error) or any(self.error_components.values())

    def _note_errorstate(self, envelope: Envelope, component: str) -> None:
        """Record an accessory message's `errorstate` ("1" = fault) under ``component``.

        For the shower valve the component is per zone — `valve1` / `valve2` — matching
        the `errorComponent` keys REST uses, so the two sources share one map.
        """
        for attribute in envelope.attributes:
            if not isinstance(attribute, dict):
                continue
            flag = _flag(attribute.get("errorstate"))
            if flag is None:
                continue
            key = component
            if component == "valve":
                number = zone_number(attribute)
                key = f"valve{number}" if number is not None else "valve1"
            self.error_components[key] = flag

    def apply_envelope(self, envelope: Envelope) -> bool:
        """Apply a HUB message. True for every HUB message, False for anything else.

        Same contract as :meth:`GcsState.apply_envelope`: every message advances
        ``last_update``.
        """
        if envelope.sku != SKU_HUB:
            return False
        # Before dispatch: undecoded codes are proof of life too.
        self.last_update = envelope.received_at
        handler = {
            MSG_HUB_SHOWER_VALVE: self._apply_valve,
            MSG_HUB_MUSIC: self._apply_music,
            MSG_HUB_STEAM: self._apply_steam,
            MSG_HUB_LIGHT: self._apply_light,
            MSG_HUB_FAVORITE: self._apply_favorite,
            MSG_HUB_FAVORITES_SNAPSHOT: self._apply_favorites_snapshot,
        }.get(envelope.code)
        if handler is None and envelope.code in MSG_HUB_EXPERIENCE_CODES:
            handler = self._apply_experience
        if handler is not None:
            handler(envelope)
        return True

    def _apply_valve(self, envelope: Envelope) -> bool:
        changed = False
        self._note_errorstate(envelope, "valve")
        # `showerwarmup` sits under `data`, beside `attributes` (REST: `showerWarmUp`).
        data = envelope.raw.get("data")
        warmup = _flag(data.get("showerwarmup") if isinstance(data, dict) else None)
        if warmup is not None and warmup != self.shower_warmup:
            self.shower_warmup = warmup
            changed = True
        for attribute in envelope.attributes:
            number = zone_number(attribute)
            if number is None:
                continue
            count = (
                self.model.outlets_valve1 if number == 1 else self.model.outlets_valve2
            )
            # Only a list: `outlet_flags` indexes positionally, so a string would read
            # as every outlet running, and an int (the API serves `outlets` as a count
            # elsewhere, `docs/protocol/hub_controller.md` §1) would raise.
            outlets = attribute.get("outlets")
            zone = HubZone(
                status=_zone_status(attribute.get("status")),
                outlets=outlet_flags(
                    outlets if isinstance(outlets, list) else None, count
                ),
                temperature=attribute.get("temperature"),
                flowrate=attribute.get("flowrate"),
            )
            # Merge, do not replace: a message without `temperature` or `flowrate` keeps
            # the earlier value (`ControllerZoneTemperatureSensor` goes unavailable on
            # None). `status` and `outlets` are always rebuilt.
            known = self.zones.get(number)
            if known is not None:
                if zone.temperature is None:
                    zone = replace(zone, temperature=known.temperature)
                if zone.flowrate is None:
                    zone = replace(zone, flowrate=known.flowrate)
            if known != zone:
                self.zones[number] = zone
                changed = True
        return changed

    def _status_flag(
        self, envelope: Envelope, component: str | None = None
    ) -> bool | None:
        for attribute in envelope.attributes:
            if component and attribute.get("component") not in (component, None):
                continue
            status = str(attribute.get("status") or "").upper()
            if status in {"ON", "OFF"}:
                return status == "ON"
        return None

    def _apply_music(self, envelope: Envelope) -> bool:
        # `MUSIC_STS` is on/off only (`docs/protocol/hub_controller.md` §5).
        self._note_errorstate(envelope, "amplifier")
        value = self._status_flag(envelope, "amplifier")
        changed = value is not None and value != self.music_on
        if value is not None:
            self.music_on = value
        return changed

    def _apply_steam(self, envelope: Envelope) -> bool:
        """`STEAM_STS`: status, temperature and timer.

        ``POWERCLEAN`` is recorded in `steam_status`, and turns `steam_on` off.
        """
        self._note_errorstate(envelope, "steam")
        before = (
            self.steam_on,
            self.steam_status,
            self.steam_temperature,
            self.steam_start_time,
            self.steam_total_time,
        )
        value = self._status_flag(envelope)
        if value is not None:
            self.steam_on = value
        for attribute in envelope.attributes:
            if not isinstance(attribute, dict):
                continue
            if (status := attribute.get("status")) is not None:
                self.steam_status = str(status).upper()
                if self.steam_powerclean:
                    # The generator is busy, not steaming for anyone.
                    self.steam_on = False
            for key, name in (
                ("temperature", "steam_temperature"),
                ("starttime", "steam_start_time"),
                ("totaltime", "steam_total_time"),
            ):
                if attribute.get(key) not in (None, ""):
                    setattr(self, name, str(attribute.get(key)))
            break
        return before != (
            self.steam_on,
            self.steam_status,
            self.steam_temperature,
            self.steam_start_time,
            self.steam_total_time,
        )

    def _apply_light(self, envelope: Envelope) -> bool:
        """`LIGHT_STS` — one group per message, so record it per group."""
        self._note_errorstate(envelope, "light")
        before = dict(self.lights)
        for attribute in envelope.attributes:
            if not isinstance(attribute, dict):
                continue
            status = str(attribute.get("status") or "").upper()
            if status in {"ON", "OFF"}:
                self.lights[_light_key(attribute)] = status == "ON"
        return before != self.lights

    def _apply_experience(self, envelope: Envelope) -> bool:
        """`*_EXP_STS` `{code, name, ready, status}`: the experience running, if any.

        An `OFF` clears only the experience it names, so a late stop for one program
        cannot wipe out the start of the next.
        """
        before = self.active_experience
        for attribute in envelope.attributes:
            if not isinstance(attribute, dict):
                continue
            name = str(attribute.get("name") or "").strip() or None
            status = str(attribute.get("status") or "").strip().upper()
            if status == "ON" and name is not None:
                self.active_experience = name
            elif status == "OFF" and (name is None or name == self.active_experience):
                self.active_experience = None
        return before != self.active_experience

    def _apply_favorite(self, envelope: Envelope) -> bool:
        """Track which favorite is running, from `FAVORITE_STS` ``{id, name, status}``.

        The only message that speaks for the favorite: the accessory messages'
        `favoriteid` attributes one component, which a favorite need not include
        (`docs/protocol/hub_controller.md` §5). It is read here only as a fallback key.

        Start and stop carry the same id and differ only in `status`; a missing `status`
        counts as ON (prefer showing a favorite over hiding one). An `OFF` clears only
        the favorite it names, or anything when it names ``"0"``, because a switch
        between favorites sends the stop and the start in either order. One message can
        carry both, so the scan continues past a stop to the first `ON` for a favorite
        it did not just stop. `_apply_experience` has the same guard.

        The name is kept so a running favorite shows before the favorites list is
        seeded. ⚠️ Nothing corrects a missed stop: `hub-state` carries no favorite, so
        the last one shows until the next `FAVORITE_STS`.
        """
        favorite_id: str | None = None
        name: str | None = None
        # What is running as this message's stops are applied in order, and which ids it
        # stopped.
        running = (self.active_favorite_id, self.active_favorite_name)
        stopped: set[str] = set()
        for attribute in envelope.attributes:
            if not isinstance(attribute, dict):
                continue
            # `favoriteid` only as a fallback; live traffic uses `id`.
            identifier = attribute.get("id") or attribute.get("favoriteid")
            if identifier is None:
                continue
            if str(attribute.get("status") or "").strip().upper() == "OFF":
                # An explicit stop. The scan continues, for a start later in the
                # message.
                off = str(identifier)
                stopped.add(off)
                if running[0] in (None, off) or off in {"0", ""}:
                    running = (None, None)
                # Otherwise a late stop for a favorite that is no longer the one
                # running, which leaves that one showing.
                favorite_id, name = running
                continue
            if str(identifier) in stopped:
                # The favorite this message just turned off: a trailing attribute cannot
                # resurrect it.
                continue
            favorite_id = str(identifier)
            name = str(attribute.get("name") or "").strip() or None
            break

        # "0" means nothing is driving the system.
        if str(favorite_id) in {"0", "None", ""}:
            favorite_id = name = None

        changed = (favorite_id, name) != (
            self.active_favorite_id,
            self.active_favorite_name,
        )
        self.active_favorite_id = favorite_id
        self.active_favorite_name = name
        return changed

    def _apply_favorites_snapshot(self, envelope: Envelope) -> bool:
        # The whole list, replacing the last. An empty one is an answer too: the
        # controller sends `attributes: []` once its last favorite is deleted.
        favorites = list(envelope.attributes)
        changed = favorites != self.favorites
        self.favorites = favorites
        return changed

    def apply_rest_state(self, payload: dict[str, Any]) -> None:
        """Seed from a ``hub-state`` read.

        Type-checked like :meth:`GcsState.apply_rest_state`: a malformed container is
        skipped and its fields keep their defaults.
        """
        if not isinstance(payload, dict):
            return
        state = payload.get("state")
        if not isinstance(state, dict):
            return
        shower = state.get("shower")
        for entry in shower if isinstance(shower, list) else ():
            # `zone_number` needs a mapping.
            if not isinstance(entry, dict):
                continue
            number = zone_number(entry)
            if number is None:
                continue
            count = (
                self.model.outlets_valve1 if number == 1 else self.model.outlets_valve2
            )
            outlets = entry.get("outlets")
            self.zones[number] = HubZone(
                status=_zone_status(entry.get("status")),
                # Only a list, as in `_apply_valve`.
                outlets=outlet_flags(
                    outlets if isinstance(outlets, list) else None, count
                ),
                temperature=entry.get("temperature"),
                flowrate=entry.get("flowRate"),
            )
        music_state = state.get("musicStateModel")
        music = music_state.get("status") if isinstance(music_state, dict) else None
        if music is not None:
            self.music_on = str(music).upper() == "ON"
        steam_state = state.get("hubSteamState")
        steam = steam_state.get("status") if isinstance(steam_state, dict) else None
        if steam is not None:
            self.steam_status = str(steam).upper()
            self.steam_on = self.steam_status == "ON"
        if isinstance(steam_state, dict):
            for key, name in (
                ("temperature", "steam_temperature"),
                ("startTime", "steam_start_time"),
                ("totalTime", "steam_total_time"),
            ):
                if steam_state.get(key) not in (None, ""):
                    setattr(self, name, str(steam_state.get(key)))
        lights = state.get("light")
        if isinstance(lights, list):
            for light in lights:
                if not isinstance(light, dict):
                    continue
                status = str(light.get("status") or "").upper()
                if status in {"ON", "OFF"}:
                    self.lights[_light_key(light)] = status == "ON"
        # Fault flags sit at the top level, beside `state` (`AnthemHubStateModel`).
        error = _flag(payload.get("errorState"))
        if error is not None:
            self.error = error
        components = payload.get("errorComponent")
        if isinstance(components, dict):
            for key in ("amplifier", "hub", "light", "steam", "valve1", "valve2"):
                flag = _flag(components.get(key))
                if flag is not None:
                    self.error_components[key] = flag
        # Top level, beside `state` (MQTT: `showerwarmup`).
        warmup = _flag(payload.get("showerWarmUp"))
        if warmup is not None:
            self.shower_warmup = warmup
        self.last_update = time.time()
