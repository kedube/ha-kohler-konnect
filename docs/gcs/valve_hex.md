# GCS Valve Hex Reference

How the Anthem valve's command word encodes temperature, flow, and outlets, for use with the
`kohler_konnect.send_valve_hex` service. The same layout is used for both sending a command
(`primaryValve1` / `secondaryValve1` on the `solowritesystem` endpoint) and reading the
valve's current state back (`GCS_SOLO_STS` over MQTT), so encoding and decoding are the same
operation in reverse.

## Word layout

The valve word is 16 hex characters; only the **first 8** carry the command. In a status
report the trailing 8 are the valve's live feedback — measured temperature (bytes 4-5, same
encoding as bytes 0-1), measured flow (byte 6, same scale as byte 2) and an error code
(byte 7). The hardware tested always reports `00000001` there (no measurement, error code 1),
and a command ignores them, so `send_valve_hex` takes 8 characters or 16.

```text
 0 1 | 2 3 | 4 5 | 6 7
  01 | 84  | C8  | 07
  ^     ^     ^     ^
  |     |     |     └─ outlet mask + flags
  |     |     └─────── flow
  |     └───────────── temperature (low byte)
  └─────────────────── valve index + temperature high bits
```

On a two-zone valve, zone 1 is `primaryValve1` and zone 2 is `secondaryValve1`.

## Byte 0 — valve index and temperature high bits

```text
byte0 = (valveIndex << 4) | (temperature >> 8)
```

| Mask | Meaning |
|---|---|
| `0xF0` | Valve index — `0` zone 1, `1` zone 2 |
| `0x03` | Temperature bits 8-9 (high bits of the 10-bit temperature value) |

When reading the valve's own status reports, byte 0 also carries two read-only status flags:
`0x08` = at flow setpoint and `0x04` = at temperature setpoint, which is what the
`At Temperature` sensor reads. A command always sends them as zero.

## Bytes 0-1 — temperature

Temperature is a 10-bit value spanning bits of byte 0 and all of byte 1, in tenths of a
degree Celsius:

```text
decode:  °C = ((byte0 & 0x03) << 8 | byte1) / 10
encode:  tenths = round(°C * 10)
         byte0 |= (tenths >> 8) & 0x03
         byte1  = tenths & 0xFF
```

Representable range is 0.0-102.3 °C, but writes are conventionally kept at or below **48.8 °C
(488 tenths / `0xE8`)** — the Konnect app never sends above that, and values above it are
untested on the valve.

| Byte0 (low bits) | Byte1 | °C | °F |
|---|---|---|---|
| `0x01` | `0x84` | 38.8 | 101.8 |
| `0x01` | `0x90` | 40.0 | 104.0 |
| `0x00` | `0x28` | 4.0 | 39.2 |

Kohler's REST API and the wire format both use Celsius regardless of the account's display
unit — convert at the edge.

## Byte 2 — flow

```text
encode:  byte = percent * 2
decode:  percent = byte / 2
```

| Byte | Flow |
|---|---|
| `0x10` | 8% (minimum) |
| `0x47` | 35.5% |
| `0xC8` | 100% (maximum) |

Valid range is `0x10`-`0xC8` (16-200). The flow byte is only meaningful while an outlet is
open — on an idle or paused valve it may reflect a calibrated ceiling rather than the last
commanded flow.

## Byte 3 — outlet mask and pause flag

```text
byte3 = [error / skipWarmUp : 1][pause : 1][0 0 0][outlet3][outlet2][outlet1]
                 0x80                0x40              0x04    0x02    0x01
```

| Mask | Meaning |
|---|---|
| `0x40` | **Pause flag.** Set when the session is held (e.g. mid-warmup, or the valve's run-time cutoff). Independent of the outlet bits — a paused valve keeps the outlet assignment it will resume to. |
| `0x07` | The three outlet bits for that zone |
| `0x80` | Differs by direction. **Read:** `errorFlag`, paired with the error code in byte 7 — one of the things the `Problem` sensor reads; never seen set on hardware. **Write:** `skipWarmUp`, which the Konnect app sets only for bath fill and this integration never sends |

Examples:

| Byte 3 | Meaning |
|---|---|
| `00` | Idle — nothing assigned, nothing flowing |
| `01` / `02` / `04` | Running to outlet 1 / 2 / 3 |
| `07` | Running to all three outlets |
| `40` | Paused, nothing assigned |
| `41` / `47` | Paused, outlet 1 / all three still assigned (resumes to this) |

A second, unrelated valve (zone 2 on a two-zone unit, or a second physical valve body) uses
the same byte-3 layout in its own word.

## Worked examples

**Decode** `0184C807`:

```text
byte0=01  byte1=84  byte2=C8  byte3=07
temperature = ((0x01 & 0x03) << 8 | 0x84) / 10 = 38.8 °C (101.8 °F)
flow        = 0xC8 / 2 = 100%
outlets     = 0x07 → outlets 1, 2, 3 all open
```

**Encode** "outlets 1 and 3 at 104 °F, full flow":

```text
104 °F → 40.0 °C → tenths = 400 = 0x190
byte0 = (0 << 4) | (0x190 >> 8 & 0x03) = 0x01
byte1 = 0x190 & 0xFF = 0x90
byte2 (flow 100%) = 0xC8
byte3 (outlets 1 + 3) = 0x01 | 0x04 = 0x05

result → 0190C805
```

The easiest way to get a valid word in practice is the workflow the `send_valve_hex` service
docs describe: set the shower up with the normal outlet switches and temperature controls,
then copy the resulting code off the `Hex` diagnostic sensor.

## Outlet inventory reference

A six-outlet valve (K-28212) reports outlets zero-indexed, split 3 per zone. Shared limits
across outlets are typically `minimumOutletTemperature` 15.0 °C, `default` 38.8 °C, `maximum`
45.0 °C, flow range 16-200 (byte units), and a configurable `maximumRunTime` (commonly 900 or
3600 seconds).
