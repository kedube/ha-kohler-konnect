"""Encode and decode the Anthem GCS valve command word and 3-byte preset word.

The single source of truth for both formats. Command word layout, identical for reads
and writes::

     0 1 | 2 3 | 4 5 | 6 7
      01 | 84  | C8  | 07
      ^     ^     ^     ^
      |     |     |     └─ outlet mask + flags
      |     |     └─────── flow
      |     └───────────── temperature (low byte)
      └─────────────────── valve index + temperature high bits

A status report's field is 16 hex characters; the trailing 8 are live feedback
(:func:`decode_word`), and a command uses only the first 8.

``docs/gcs/valve_hex.md`` is the byte-by-byte reference, and
``docs/protocol/gcs_valve.md`` §3.2-3.3 has what the app adds to it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .models import ValveModel

# Sent for a valve the model does not populate; the field must still be present.
# ⚠️ Never send it for a valve that exists: the valve ignores the word, and as
# ``primaryValve1`` it is the "turns on but never off" bug
# (`docs/protocol/gcs_valve.md` §3.1). Only this exact word is the sentinel: a 0x00
# first byte alone is an ordinary valve-1 word below 25.6 °C.
UNUSED_VALVE_WORD = "00000000"

# Byte 0. Status and command words differ:
#
#     status:  [valve index : 4][atFlow : 1][atTemp : 1][temperature high : 2]
#     write:   [valve index : 4][   0       0        ][temperature high : 2]
#
# atFlow (0x08) and atTemp (0x04) are read-only: the valve has reached its flow /
# temperature setpoint. A command always writes them as zero.
VALVE_INDEX_SHIFT = 4
VALVE1_INDEX = 0
VALVE2_INDEX = 1
AT_FLOW_BIT = 0x08
AT_TEMP_BIT = 0x04
# Byte 0 as written for each valve at 25.6 °C or above; `encode_word` keeps only the
# index nibble and sets the temperature bits itself.
VALVE1_PREFIX = 0x01
VALVE2_PREFIX = 0x11

# Temperature: a 10-bit value in tenths of °C, its high bits in byte 0 and low byte in
# byte 1, so 0.0-102.3 °C is representable:
#
#     °C = ((byte0 & 0x03) << 8 | byte1) / 10
TEMPERATURE_TENTHS_PER_DEGREE = 10
TEMPERATURE_HIGH_BITS = 0x03
# Writes clamp at 48.8 °C, the most the Konnect app ever sends. Whether the firmware
# enforces a cap is untested, so the app's limit is honoured.
TEMPERATURE_MAX_TENTHS = 488
# Not read by the codec, which works in tenths: 25.6 °C is the weight of byte 0's low
# temperature bit, and each step of byte 1 is 0.1 °C.
TEMPERATURE_BASE_C = 25.6
TEMPERATURE_STEP_C = 0.1

# Whole °F to tenths of °C, the app's table for direct writes
# (`docs/protocol/gcs_valve.md` §3.2). It sits one tenth below
# `round((f - 32) * 50 / 9)` at sixteen entries above 86 °F, which makes the app's
# display conversion `round(c * 1.8 + 32)` round-trip exactly. The valve accepts any
# value; the table keeps a setpoint written from Home Assistant where the touchscreen
# would put it. Presets convert arithmetically instead (see `PRESET_ECHO_MAX_TENTHS`).
# Outside 59-122 °F the app sends 0, full cold, so `unit_to_celsius` falls back to
# arithmetic there.
FAHRENHEIT_TO_TENTHS_C = {
    59: 150,
    60: 156,
    61: 161,
    62: 167,
    63: 172,
    64: 178,
    65: 183,
    66: 189,
    67: 194,
    68: 200,
    69: 206,
    70: 211,
    71: 217,
    72: 222,
    73: 228,
    74: 233,
    75: 239,
    76: 244,
    77: 250,
    78: 256,
    79: 261,
    80: 267,
    81: 272,
    82: 278,
    83: 283,
    84: 289,
    85: 294,
    86: 300,
    87: 305,
    88: 311,
    89: 316,
    90: 322,
    91: 327,
    92: 333,
    93: 338,
    94: 344,
    95: 350,
    96: 355,
    97: 361,
    98: 366,
    99: 372,
    100: 377,
    101: 383,
    102: 388,
    103: 394,
    104: 400,
    105: 405,
    106: 411,
    107: 416,
    108: 422,
    109: 427,
    110: 433,
    111: 438,
    112: 444,
    113: 450,
    114: 455,
    115: 461,
    116: 466,
    117: 472,
    118: 477,
    119: 483,
    120: 488,
    121: 494,
    122: 500,
}

# Byte 2 — flow. One quantity on three scales, a standing source of 2x and 4x errors:
#
#   byte            0x10-0xC8   the wire format, MQTT and outlet-record flow limits
#   flowSetpoint    0-50        `gcs-state`, byte / 4
#   percent         0-100       HUB favorite `flowrate` and HA entities, byte / 2
#
# Percent is really a ratio against the outlet's own `maximumFlowRate`
# (`flow_byte_to_percent`). FLOW_PER_PERCENT is the default for callers with no
# per-outlet context, exact for the usual ceiling of 200. Don't truncate (`byte // 2`):
# odd bytes are common.
FLOW_PER_PERCENT = 2
FLOW_PER_SETPOINT = 4

# The flow byte's range is 16-200 (0x10-0xC8), not from zero, so the usable percent
# range is 8-100. It is continuous over that range: `flowSetpoint` (byte / 4) is a
# derived display value, not an integer scale. The valve holds a directly written flow
# byte; the touchscreen applies its own scaling before it writes, so treat the echo as
# truth.
FLOW_BYTE_MIN = 0x10
FLOW_SETPOINT_MAX = 50
FLOW_BYTE_MAX = 0xC8


def flow_byte_to_percent(byte: int, max_flow_byte: int = FLOW_BYTE_MAX) -> float:
    """Flow byte to percent of the outlet's ceiling: ``byte * 100 / max``.

    As the app computes it. ``max_flow_byte`` is the outlet's ``maximumFlowrate``; the
    default, 200, gives ``byte / 2``.
    """
    if max_flow_byte <= 0:  # pragma: no cover - defensive
        return 0.0
    return round(byte * 100 / max_flow_byte, 1)


def flow_percent_to_byte(percent: float, max_flow_byte: int = FLOW_BYTE_MAX) -> int:
    """Percent to flow byte, the inverse of `flow_byte_to_percent`, rounded."""
    if max_flow_byte <= 0:  # pragma: no cover - defensive
        return FLOW_BYTE_MIN
    return round(percent * max_flow_byte / 100)


# Byte 3 = [0x80][pause 0x40][0 0 0][outlet3 0x04][outlet2 0x02][outlet1 0x01]
#
# 0x80 differs by direction: read, `errorFlag` (paired with the error code in byte 7);
# write, `skipWarmUp`, which the app sets only for bath fill
# (`docs/protocol/gcs_valve.md` §8) and this integration never sends.
#
# The outlet mask is only the low three bits. 0x40 is an independent pause flag that
# coexists with them, so a paused valve keeps the outlets it will resume to:
#
#     00  idle, nothing assigned          01/02/04  running to outlet 1/2/3
#     40  paused, nothing assigned        41/42/44  paused, outlet 1/2/3 still assigned
#
# Never treat 0x40 as a mask value: 0x41 would be unrepresentable.
OUTLET_MASK_BITS = 0x07
VALVE_PAUSE_FLAG = 0x40
VALVE_SKIP_WARMUP_FLAG = 0x80  # write meaning of bit 0x80
VALVE_ERROR_FLAG = 0x80  # read meaning of the same bit
VALVE_STOP_MASK = 0x00
OUTLETS_PER_VALVE = 3

VALVE_WORD = re.compile(r"^[0-9A-Fa-f]{8}$")

# A preset hexString is 3 bytes, [byte0][temp low][flow], with no valve-index nibble
# (the field name identifies the valve). Byte 0 carries the temperature high bits and
# the outlet flags, at different positions from a command word
# (`docs/protocol/gcs_valve.md` §3.3):
#
#     preset  byte0:  0x04 outlet1   0x08 outlet2   0x10 outlet3   0x01 temp high bit
#     command byte3:  0x01 outlet1   0x02 outlet2   0x04 outlet3
#
# e.g. 05849C: byte0 0x05 -> outlet 1 at 38.8 °C, flow 0x9C.
PRESET_OUTLET_BITS = (0x04, 0x08, 0x10)
PRESET_WORD = re.compile(r"^[0-9A-Fa-f]{6}$")


def encode_preset_word(
    temperature_celsius: float, flow_percent: float, outlet_mask: int
) -> str:
    """Build one valve's 3-byte preset hexString (``writepreset``, ``createpreset``).

    Temperature (°C) and flow (percent) are clamped as in :func:`encode_word`. Those
    endpoints silently ignore a 4-byte command word, so never send one there.
    """
    if outlet_mask & ~OUTLET_MASK_BITS:
        raise ValveHexError(f"Outlet mask 0x{outlet_mask:02X} sets unknown bits")
    tenths = min(
        max(round(temperature_celsius * TEMPERATURE_TENTHS_PER_DEGREE), 0),
        TEMPERATURE_MAX_TENTHS,
    )
    byte0 = (tenths >> 8) & TEMPERATURE_HIGH_BITS
    for index, bit in enumerate(PRESET_OUTLET_BITS):
        if outlet_mask >> index & 1:
            byte0 |= bit
    flow_byte = min(
        max(round(flow_percent * FLOW_PER_PERCENT), FLOW_BYTE_MIN), FLOW_BYTE_MAX
    )
    return f"{byte0:02X}{tenths & 0xFF:02X}{flow_byte:02X}"


def preset_word_temperature(word: str) -> float:
    """Read the commanded temperature, in °C, out of a 3-byte preset hexString.

    Raises :class:`ValveHexError` if the word is not six hex characters.
    """
    text = str(word or "").strip()
    if not PRESET_WORD.match(text):
        raise ValveHexError(f"Preset word must be 6 hex characters, got {word!r}")
    byte0 = int(text[0:2], 16)
    tenths = ((byte0 & TEMPERATURE_HIGH_BITS) << 8) | int(text[2:4], 16)
    return round(tenths / TEMPERATURE_TENTHS_PER_DEGREE, 1)


#: The ceiling for a preset word read back from the cloud and sent on to the valve:
#: 48.9 °C, one tenth above `TEMPERATURE_MAX_TENTHS`, because the app converts a
#: preset's °F arithmetically and stores 120 °F as 489
#: (`docs/protocol/gcs_valve.md` §3.3). The valve still clamps each outlet at its
#: `maximumOutletTemperature`, which the app never writes above 48.8 °C.
PRESET_ECHO_MAX_TENTHS = 489


def check_preset_word(word: str) -> str:
    """Return ``word`` normalised, or raise `ValveHexError` if malformed or too hot.

    For preset words read back from the cloud before they become a command
    (:func:`preset_words_to_pair`): a corrupted record could otherwise ask for up to
    102.3 °C. The ceiling is :data:`PRESET_ECHO_MAX_TENTHS`, so anything this
    integration or the app stores passes.
    """
    text = str(word or "").strip().lower()
    ceiling = PRESET_ECHO_MAX_TENTHS / TEMPERATURE_TENTHS_PER_DEGREE
    commanded = preset_word_temperature(text)
    if commanded > ceiling:
        raise ValveHexError(
            f"Preset word {text!r} commands {commanded:.1f} °C, above the "
            f"{ceiling:.1f} °C ceiling"
        )
    return text


class ValveHexError(ValueError):
    """Raised when a valve word is malformed or a value is out of range."""


@dataclass(frozen=True)
class ValveWord:
    """A decoded valve command word."""

    prefix: int
    temperature_celsius: float
    flow_percent: float
    outlet_mask: int
    paused: bool
    # Read-only status the device reports; always zero in a word we send.
    at_temperature: bool = False
    at_flow: bool = False
    error_flag: bool = False
    # Live feedback from the second half of a 16-character status word; None when only
    # the command half was available.
    measured_temperature_celsius: float | None = None
    measured_flow_percent: float | None = None
    error_code: int | None = None
    # The word exactly as received, for showing the wire value without re-encoding it
    # (which would drop bits this class does not model). Empty for a word built from a
    # REST read. Excluded from equality so a reserved-bit difference wakes no entity.
    raw: str = field(default="", compare=False)

    @property
    def measured_flow_setpoint(self) -> float | None:
        """Measured flow on the device's own 0-50 scale."""
        if self.measured_flow_percent is None:
            return None
        return round(
            self.measured_flow_percent * FLOW_PER_PERCENT / FLOW_PER_SETPOINT, 1
        )

    @property
    def stopped(self) -> bool:
        """True when no outlet is open and the valve is not merely paused."""
        return self.outlet_mask == VALVE_STOP_MASK and not self.paused

    @property
    def flow_setpoint(self) -> float:
        """Flow on the GCS device's own 0-50 scale, as ``gcs-state`` reports it."""
        return round(self.flow_percent * FLOW_PER_PERCENT / FLOW_PER_SETPOINT, 1)

    def outlet(self, index: int) -> bool:
        """Whether this valve's outlet ``index`` (0-2) is open."""
        return bool(self.outlet_mask >> index & 1)


def normalize_word(value: str | None) -> str:
    """Return the uppercased 8-character command half of a valve field."""
    word = str(value or "")[:8].upper()
    if not VALVE_WORD.fullmatch(word):
        raise ValveHexError(f"Not an 8-character valve command word: {value!r}")
    return word


def decode_word(value: str) -> ValveWord:
    """Decode an 8-character command word or a 16-character status word.

    A status word appends four bytes of live feedback:

    ===== ============================================================
    byte  meaning
    ===== ============================================================
    4-5   measured temperature, ``((byte4 & 3) << 8 | byte5) / 10`` °C
    6     measured flow, same scale as the byte-2 setpoint
    7     error code, pairing with ``errorFlag`` in byte 3
    ===== ============================================================

    The hardware tested always reports ``00000001`` there: no measurement, and error
    code 1.
    """
    full = str(value or "").strip().upper()
    word = normalize_word(full)
    mask_byte = int(word[6:8], 16)
    byte0 = int(word[0:2], 16)
    tenths = ((byte0 & TEMPERATURE_HIGH_BITS) << 8) | int(word[2:4], 16)
    return ValveWord(
        prefix=byte0,
        temperature_celsius=round(tenths / TEMPERATURE_TENTHS_PER_DEGREE, 1),
        flow_percent=round(int(word[4:6], 16) / FLOW_PER_PERCENT, 1),
        outlet_mask=mask_byte & OUTLET_MASK_BITS,
        paused=bool(mask_byte & VALVE_PAUSE_FLAG),
        at_temperature=bool(byte0 & AT_TEMP_BIT),
        at_flow=bool(byte0 & AT_FLOW_BIT),
        error_flag=bool(mask_byte & VALVE_ERROR_FLAG),
        raw=full,
        **_decode_measurements(full),
    )


def _decode_measurements(full: str) -> dict[str, object]:
    """Pull the live-feedback half out of a 16-character status word."""
    if len(full) < 16 or not VALVE_WORD.fullmatch(full[8:16]):
        return {}
    byte4, byte5, byte6, byte7 = (int(full[i : i + 2], 16) for i in range(8, 16, 2))
    return {
        "measured_temperature_celsius": round(
            (((byte4 & TEMPERATURE_HIGH_BITS) << 8) | byte5)
            / TEMPERATURE_TENTHS_PER_DEGREE,
            1,
        ),
        "measured_flow_percent": round(byte6 / FLOW_PER_PERCENT, 1),
        "error_code": byte7,
    }


def encode_word(
    prefix: int,
    temperature_celsius: float,
    flow_percent: float,
    outlet_mask: int,
    *,
    paused: bool = False,
    skip_warmup: bool = False,
) -> str:
    """Build one 8-character valve command word.

    ``prefix`` is :data:`VALVE1_PREFIX` or :data:`VALVE2_PREFIX`. Temperature (°C, up to
    :data:`TEMPERATURE_MAX_TENTHS`) and flow (percent) are clamped to what the device
    accepts. An outlet mask outside 0x00-0x07 raises `ValveHexError` rather than being
    reinterpreted; pause is the ``paused`` flag, never part of the mask.
    """
    if outlet_mask & ~OUTLET_MASK_BITS:
        raise ValveHexError(
            f"Outlet mask 0x{outlet_mask:02X} sets bits outside the low three. "
            "Pause is a separate flag — pass paused=True rather than folding 0x40 in."
        )
    tenths = min(
        max(round(temperature_celsius * TEMPERATURE_TENTHS_PER_DEGREE), 0),
        TEMPERATURE_MAX_TENTHS,
    )
    # Keep only the prefix's valve-index nibble and set the temperature bits from the
    # temperature; atFlow/atTemp are status, never commanded.
    byte0 = (prefix & 0xF0) | ((tenths >> 8) & TEMPERATURE_HIGH_BITS)
    # Clamp to the valve's flow range. The app's encoder has no clamp of its own: its
    # slider is bounded by the outlet's flow limits instead.
    flow_byte = min(
        max(round(flow_percent * FLOW_PER_PERCENT), FLOW_BYTE_MIN), FLOW_BYTE_MAX
    )
    byte3 = outlet_mask
    if paused:
        byte3 |= VALVE_PAUSE_FLAG
    if skip_warmup:
        byte3 |= VALVE_SKIP_WARMUP_FLAG
    return f"{byte0:02X}{tenths & 0xFF:02X}{flow_byte:02X}{byte3:02X}"


def outlet_mask(*outlets: bool) -> int:
    """Combine up to three outlet flags into a mask (bit 0 is the first)."""
    if len(outlets) > OUTLETS_PER_VALVE:
        raise ValveHexError(
            f"A valve has {OUTLETS_PER_VALVE} outlets, not {len(outlets)}"
        )
    return sum(1 << index for index, is_on in enumerate(outlets) if is_on)


def encode_pair(
    model: ValveModel,
    temperature_celsius: float,
    flow_percent: float,
    outlets: list[bool],
) -> tuple[str, str]:
    """Build both valve words from this model's flat outlet flags.

    ``outlets`` has one flag per physical outlet, split between the valves by the model
    (on a K-28211, outlet 3 is valve 2's first). A model with no second valve gets
    :data:`UNUSED_VALVE_WORD` for valve 2.
    """
    valve1_flags, valve2_flags = model.split_outlets(outlets)
    valve1 = encode_word(
        VALVE1_PREFIX, temperature_celsius, flow_percent, outlet_mask(*valve1_flags)
    )
    if not model.uses_valve2:
        return valve1, UNUSED_VALVE_WORD
    return valve1, encode_word(
        VALVE2_PREFIX, temperature_celsius, flow_percent, outlet_mask(*valve2_flags)
    )


def encode_shower(
    model: ValveModel,
    temperatures_celsius: Mapping[int, float],
    flow_percent: float,
    zone_flags: Mapping[int, Sequence[bool]],
) -> tuple[str, str]:
    """Build both words for a whole-shower command from per-zone flags and temperatures.

    Stateless, for the ``custom_shower`` action: nothing comes from what the valve last
    reported. A zone missing from ``zone_flags`` or with no flag set is written closed
    (mask ``0x00``), never "left as it was", because that report lags a write.
    ``temperatures_celsius`` is per zone; zone 2 falls back to zone 1's.

    Flags are zone-local (index 0 is that zone's outlet 1). A flag beyond the zone's
    outlets raises `ValveHexError`, including any zone 2 flag on a single-zone model.
    """

    def mask_for(zone: int) -> int:
        flags = list(zone_flags.get(zone, ()))
        capacity = model.outlets_in_zone(zone)
        if any(flags[capacity:]):
            raise ValveHexError(
                f"{model.name} has {capacity} outlet(s) in zone {zone}; "
                f"outlet {capacity + 1} or beyond was requested"
            )
        return outlet_mask(*flags[:capacity])

    # Check both masks first, so a zone 2 flag on a single-zone model raises rather than
    # vanishing into the sentinel.
    mask1 = mask_for(1)
    mask2 = mask_for(2)
    celsius1 = temperatures_celsius[1]
    celsius2 = temperatures_celsius.get(2, celsius1)
    valve1 = encode_word(VALVE1_PREFIX, celsius1, flow_percent, mask1)
    if not model.uses_valve2:
        return valve1, UNUSED_VALVE_WORD
    return valve1, encode_word(VALVE2_PREFIX, celsius2, flow_percent, mask2)


def _mask_pair(
    model: ValveModel,
    mask: int,
    temperature_celsius: float,
    flow_percent: float,
    *,
    paused: bool = False,
) -> tuple[str, str]:
    """Build both words carrying the same mask, respecting the model's valve count."""
    valve1 = encode_word(
        VALVE1_PREFIX, temperature_celsius, flow_percent, mask, paused=paused
    )
    if not model.uses_valve2:
        return valve1, UNUSED_VALVE_WORD
    return valve1, encode_word(
        VALVE2_PREFIX, temperature_celsius, flow_percent, mask, paused=paused
    )


def stop_pair(
    model: ValveModel,
    temperature_celsius: float = 38.0,
    flow_percent: float = 100,
) -> tuple[str, str]:
    """Build the pair of words that stops every outlet (mask ``0x00``).

    The valve ignores temperature and flow in a stop, but the word must still be well
    formed: :data:`UNUSED_VALVE_WORD` is not a stop. The app's Stop writes the pause bit
    instead (:func:`pause_pair` with no mask); this uses ``0x00``, as the Anthem Plus
    controller does, so it can never be mistaken for the valve's run-time cutoff
    (``docs/protocol/gcs_valve.md`` §3.1).
    """
    return _mask_pair(model, VALVE_STOP_MASK, temperature_celsius, flow_percent)


def pause_pair(
    model: ValveModel,
    temperature_celsius: float = 38.0,
    flow_percent: float = 100,
    outlet_mask: int = 0x00,
) -> tuple[str, str]:
    """Build the pair of words that pauses the valves, holding the session open.

    ``outlet_mask`` is the outlets the session resumes to; pass the running mask to keep
    them.
    """
    return _mask_pair(
        model, outlet_mask, temperature_celsius, flow_percent, paused=True
    )


def decode_preset_word(word: str) -> ValveWord:
    """Decode a 3-byte preset hexString into the shape of a command word.

    The inverse of :func:`encode_preset_word`; ``paused`` is always False. Raises
    :class:`ValveHexError` if the word is not six hex characters.
    """
    text = str(word or "").strip().upper()
    if not PRESET_WORD.fullmatch(text):
        raise ValveHexError(f"Preset word must be 6 hex characters, got {word!r}")
    byte0 = int(text[0:2], 16)
    return ValveWord(
        prefix=byte0,
        temperature_celsius=preset_word_temperature(text),
        flow_percent=round(int(text[4:6], 16) / FLOW_PER_PERCENT, 1),
        outlet_mask=sum(
            1 << index for index, bit in enumerate(PRESET_OUTLET_BITS) if byte0 & bit
        ),
        paused=False,
        raw=text,
    )


def preset_words_to_pair(model: ValveModel, words: Sequence[str]) -> tuple[str, str]:
    """Build both command words from a preset's stored valve words; they run water.

    How the Shower switch runs the default shower: preset 1's words sent as one
    ``solowritesystem``, never run as a preset (``docs/protocol/gcs_valve.md`` §3.4).
    ``words`` is in valve order (index 0 ``Valve1``, 1 ``Valve2``), as
    `GcsPreset.valve_words` holds them. Each passes :func:`check_preset_word` and is
    re-encoded with its own outlets, temperature and flow, pause clear.

    ⚠️ A zone the preset opens nothing on is an addressed word with mask ``0x00``, never
    :data:`UNUSED_VALVE_WORD`, which makes a two-valve system discard the whole command
    (``docs/protocol/gcs_valve.md`` §3.1). A zone with no stored word (missing, or
    ``000000``) is sent closed at the other zone's temperature and flow, not the 0 °C
    that word decodes to.

    Raises :class:`ValveHexError` when no zone has a word, or a word is malformed or
    above :data:`PRESET_ECHO_MAX_TENTHS`.
    """
    stored: dict[int, ValveWord] = {}
    for zone in model.zones:
        text = words[zone - 1] if zone <= len(words) else ""
        if text and text.strip("0"):
            stored[zone] = decode_preset_word(check_preset_word(text))
    if not stored:
        raise ValveHexError("the preset holds no valve word for any zone")
    fallback = stored[min(stored)]

    def command(zone: int, prefix: int) -> str:
        word = stored.get(zone)
        if word is None:
            return encode_word(
                prefix,
                fallback.temperature_celsius,
                fallback.flow_percent,
                VALVE_STOP_MASK,
            )
        return encode_word(
            prefix, word.temperature_celsius, word.flow_percent, word.outlet_mask
        )

    valve1 = command(1, VALVE1_PREFIX)
    if not model.uses_valve2:
        return valve1, UNUSED_VALVE_WORD
    return valve1, command(2, VALVE2_PREFIX)


def celsius_to_unit(value_c: float, temperature_unit: str) -> float:
    """Convert a decoded Celsius value into the account's display unit.

    The API and the valve word are Celsius whatever the account's unit; convert only at
    the edge.
    """
    if temperature_unit.lower().startswith("f"):
        return round(value_c * 9 / 5 + 32, 1)
    return round(value_c, 1)


def unit_to_celsius(value: float, temperature_unit: str) -> float:
    """Convert an account-unit temperature into Celsius for encoding.

    A whole °F in 59-122 goes through `FAHRENHEIT_TO_TENTHS_C`, so it lands where the
    touchscreen would put it. Any other °F (fractional, or outside the table) uses
    arithmetic.
    """
    if not temperature_unit.lower().startswith("f"):
        return value
    whole = round(value)
    if abs(value - whole) < 0.01 and whole in FAHRENHEIT_TO_TENTHS_C:
        return FAHRENHEIT_TO_TENTHS_C[whole] / TEMPERATURE_TENTHS_PER_DEGREE
    return (value - 32) * 5 / 9
