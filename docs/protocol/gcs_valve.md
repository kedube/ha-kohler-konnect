# Anthem digital valve (SKU `GCS`)

The valve has built-in Wi-Fi and is addressed directly. It has **no "run my default" command**: every start specifies the whole valve state as a command word, or names a stored preset. Shared mechanics (sign-in, REST conventions, MQTT, firmware, usage) are in [platform.md](platform.md). Confidence markers are explained in the [README](README.md#confidence).

The command word is documented byte by byte in [../gcs/valve_hex.md](../gcs/valve_hex.md). This chapter covers everything around it.

---

## 1. Hardware and topology

| Model | Outlets | Split (valve 1 + valve 2) |
|---|---|---|
| K-28209 | 2 | 2 + 0 |
| K-28210 | 3 | 3 + 0 |
| K-28211 | 4 | 2 + 2 |
| K-28212 | 6 | 3 + 3 |

- **A "zone" is a valve body's half.** Zone 1 is `primaryValve1` and zone 2 is `secondaryValve1`. Each carries its own temperature, flow and three outlet bits.
- **How a valve gets into the app.** Only the first-generation touchscreen `K-28214`, plugged into the valve, adds a GCS valve to the Konnect app. The Anthem Plus screen `K-28214-ASC` plugs into the controller and adds only the HUB. Both can be on one valve at once, which is how an account ends up with a GCS and a HUB for one shower (live).
- **Topology source.** Read `gcs-state/gcsadvancestate` → `setting.valveSettings[]`, one entry per valve slot: `valve` names it (`Valve1`, `Valve2`, …) and `noOfOutlets` is its outlet count. `Valve1` and `Valve2` give the split (works without a controller, live). Each entry's `outletConfigurations[]` holds that valve's outlet records (§1.1, §2.1). `setting.outlets`, a total beside `valveSettings`, was null on a tested valve while the per-valve counts were right (live).
- **`gcs-configuration` carries no topology.** Its structural fields (`zoneone`, `zonetwo`, `parts`, `valve1Settings`, `valve2Settings`, `systemConfiguration`, `systemSettings`) are null on a controller-attached valve and on the controller-free K-28210 pair alike; only `about` and `firmwareOTADetails` carry data (live).
- **All eight slots are listed**, the unpopulated ones with `noOfOutlets` 0, so a single-zone valve reports `Valve2` as 0. This integration relies on that: a `Valve2` entry that is missing, or whose count is not a whole number, fails detection rather than meaning one zone (`konnect/topology.py`).

### 1.1 `outLetId` and outlet bits

- **Ids.** Each valve body reserves three `outLetId` slots: 0–2 for valve 1 and 3–5 for valve 2. The app numbers new outlets position−1, **except on a 4-port valve, whose zone 2 keeps ids 3 and 4**. So a K-28211 reports `0, 1, 3, 4` (app, `ra0/n0.java`; live on a K-28211 via PR #2).
- **Bits.** The app sorts each valve's `outletConfigurations` by `outLetId` and maps **list index *i* to bit *i*** (`0x01`, `0x02`, `0x04`). A 2-outlet valve or half therefore uses `0x01`/`0x02` (app, `db0/c.java`; not yet live-verified on a 2-outlet valve).
- ⚠️ The app drops outlets whose type is not in its table (§1.2) from that list, which would shift the index-to-bit mapping.

### 1.2 Outlet types (`outLetType`)

This is the app's whole outlet picker (`db0/c.java` `X()`, app). Codes 1, 11, 21, 31 and 52 are also live-confirmed.

| Code | Fixture | Variant |
|---|---|---|
| 0 | unassigned (older screens) | |
| 1 | Handshower | |
| 11 | Showerhead | single |
| 12 | Showerheads | multiple |
| 21 | Bathfiller (tub filler) | "Spout" on older screens |
| 30 | Not plumbed | |
| 31 | Rainhead | Katalyst |
| 32 | Rainhead | Cascade |
| 33 | Rainhead | Kinetic |
| 34 | Rainhead | Rain Curtain |
| 35 | Rainhead | Laminar |
| 36 | Rainhead | Massage (Wave) |
| 37 | Rainhead | Hydro Massage |
| 38 | Rainhead | Silk |
| 39 | Rainhead | Real Rain® |
| 51 | Body Spray | single |
| 52 | Body Spray | multiple |
| 53 | Body Spray | Massage (Wave) |
| 61 | Foot Spray | single |
| 62 | Foot Spray | multiple |

The type is a **label on the valve**. The valve derives no flow envelope from it (the controller does). Valve and controller may hold different codes for the same fixture (live). Names given to outlets in the app are **not transmitted** anywhere.

## 2. Reads

All are under `/devices/api/v1/device-management/`.

| Route | Returns | Notes |
|---|---|---|
| `gcs-state/{id}` | `state.valve1/valve2 {out1..out3, temperatureSetpoint (°C), flowSetpoint (0-50), pauseFlag}`, `warmUpState {warmUp, state}`, `currentSystemState`, `presetOrExperienceId`, `totalVolume`, `totalFlow` | Seeds state at setup (live). Also carries a top-level `connectionState` (live). `totalFlow` is **not** a water meter: it cycles among values 4× apart (live). `totalVolume` changes erratically, is not a monotonic counter, and its unit is unknown (live). Konnect 3.0.6 no longer calls this route |
| `gcs-state/gcsadvancestate/{id}` | Top level: `connectionState`, `lastConnected`, `firmwareUpdate`, `firmwareVersionInfo`. `state.{rebootStatus, ready, ioTActive, bleConnected, blePairing, …}`. `setting.{valveSettings[], uiConfig[], eco{ecoMode, ecoTimeLimit, flowRate}, flowControl, gcsDateTime, interfaceFirmwareTypeVersion, …}` | **The** settings source: topology, outlet records and UI config (live) |
| `gcs-configuration/{id}` | `configuration.about {uI2, primaryValve, secondaryValve1, gateway}` each `{firmware, assetsFirmware}`; top-level `about.firmware {version, latestVersion}`; `otaReportedProperties`; `firmwareUpdate {progress, progressPercent, status, version, …}`; `createdTime` | Firmware per part (live). `about` nests under `configuration` on some accounts and sits at the top on others. `createdTime` is the cloud registration date, not an install date, sent as ISO-8601 or epoch (live). A top-level `iot` block holds the device's IoT Hub connection details: treat it as a secret (live) |
| `gcs-configuration/{id}/about` | `gatewayConfigInfo {firmware, installDate, model, serialNo}`, `valvesConfigInfo[] {firmware, model, name, serialNo, status}`, `interfacesConfigInfo[] {firmware, name, status}` | Per-part identity (app) |
| `gcs-preset/{id}` | `gcsPresetExperienceDetails[] {presetId, title\|logicalName, isExperience ("True"/"False"), time, volume, valveDetails[{valveIndex: "Valve1", hexString}]}` | Ten preset slots plus experiences (live) |
| `gcs-diagnostics/{id}` | Fault log, see [platform §8](platform.md#8-diagnostics-fault-logs). `DELETE` clears it — the integration's `Clear Error Log` button | app |
| `gcs-experience` | `[{name, type, description, duration, url}]` — the catalogue of experiences that can be added | app |
| `gcs-usage/{id}` | See [platform §6](platform.md#6-water-usage) | live |

### 2.1 Outlet record: read and write spellings differ

| Field | REST (`gcsadvancestate`) | MQTT (`READ_GCS_OUTLET_CONFIG_CFG`) | Write (`writeoutletconfig`) |
|---|---|---|---|
| Run time (s) | `maximumRuntime` | `maximumRunTime` | `maximumRuntime` |
| Flow (min / default / max) | `minimumFlowrate` / `defaultFlowrate` / `maximumFlowrate`, **display 0-50** | `minimumFlowRate` / `defaultFlowRate` / `maximumFlowRate`, **bytes 16-200** | lowercase `r`, **bytes** |
| Temperature (min / default / max) | `…OutletTemperature`, **display °C** (`45`, `47.8`) | **tenths of °C** (`450`) | **tenths of °C** |
| `outLetId`, `outLetType`, `outLetFlags` | same on all three surfaces | | |
| `maxVolume`, `purge` | not seen | not seen | always sent by the app |

**The write is a whole-record replace** (live): twelve string keys, one call per outlet, each sent after the previous one succeeds.
- Omitting a key, or sending the read spelling, makes Gson drop it. The API still returns 201, and the field silently takes a server default.
- One of these fields is the **scald limit**.
- The app sends `maxVolume: "0"` and `purge: ""` (newer screens), and forces `minimumOutletTemperature = 150` and `minimumFlowrate = 16`. Flow is ×4 rounded; temperature is ×10 truncated, with 49.0 clamped to 488 (app). `maxVolume` is probably the bath-fill volume limit (inferred).
- `outLetFlags` is echoed back and never interpreted (`1` everywhere); the app defaults it to `"1"` (app).
- The app offers Max and Default Temperature as one setting each and writes it to every outlet's record, with no read-back afterwards (app).
- **`gcsadvancestate` lags a write.** A read about 1 s after the 201 still shows the old value; the change appeared within 25 s (live).
- With flow control off at the fixture, an outlet reports its minimum flow equal to its maximum (inferred).

## 3. Commands

All are under `/platform/api/v1/commands/gcs/`. Every body starts `{deviceId, sku: "GCS", tenantId, …}`.

| Verb | Body (beyond the common three) | Effect | Confidence |
|---|---|---|---|
| `solowritesystem` | `gcsValveControlModel {primaryValve1, secondaryValve1, secondaryValve2..7}` | **The** direct write. The single-valve `secondaryValve1` is `"00000000"`; slots 2-7 are `""` (newer screens) or `"00000000"` | live |
| `controlpresetorexperience` | `preset: "<id>", action: "On"\|"Off"` | Starts or stops a preset **or an experience** by itself; no valve write follows. A preset applies only the zones it opens an outlet on; a zone at mask `0x00` keeps its setpoint (inferred). ⚠️ `presetOrExperienceId` in this body is accepted and then ignored | live (presets, starting an experience), app (stopping an experience) |
| `startpreset` | `presetOrExperienceId` | The older screens' start | app |
| `createpreset` | Flat: `name, time, volume: "0", valve1..valve8` (3-byte preset words; `"000000"` for unused) | Fills the lowest free slot | app |
| `writepreset` | `gcsPresetControlModel {presetId, name, time, volume: "0", valve1..valve8}` | **Whole-record replace.** Delete = name `""`, time `"0"`, all words `"000000"`. Posting to `createpreset`, omitting the wrapper, or sending 4-byte words all silently do nothing | live |
| `writeoutletconfig` | `gcsOutletConfigControlModel {12 keys}` (§2.1) | One outlet's record | live (10 keys), app (12) |
| `warmup` | `warmUp: <mode>` | Mode toggle (§5). An omitted `warmUp` returns 200 and is ignored | live |
| `valvereset` | `reset: "productRestart"` | Reboots the valve; running water stops | app |
| `factoryreset` | — | The app's *Remove Product*, followed by `PATCH customer-device/{tenant}/{home}/device {operation: "Delete", keepDeviceData, logicalName, deviceId, sku}`; MQTT `GCS_DELETE_PROFILE_STS` follows | app |
| `writeuiconfig` | `gcsUIConfigModel {whole record}` (§6) | Touch-panel settings | app |
| `uiconfigsuccess` | — | Handshake at the end of first-time setup; not a setting | app |
| `bathfillervolume` | `index: "0"` | Asks the valve to report `READ_DISPENSED_WATER_VOLUME_STS` | app |
| `experience` | `operation: Insert\|Replace\|Delete, newExperienceTitle, replaceId` | Adds, replaces or removes a stored experience | app |
| `experiences` | `gcsExperiencesManageRequestModel: [{operation: "Insert", experienceTitle}]` | Batch insert | app |

### 3.1 Starting and stopping water

| Action | How | Confidence |
|---|---|---|
| Open outlets | `solowritesystem` with the full words: outlet bits, temperature and flow for both zones | live |
| Stop | **The app writes byte 3 = `0x40`**: the pause bit, no outlets, bit 7 clear. It sets the per-valve pause bit and zeroes the outlets (`db0/c.java`; the older screens do the same) | app |
| Stop, `0x00` | Mask `0x00` with a valid prefix also stops. The Anthem Plus controller stops this way, and so does this integration: a `0x00` stop can never be mistaken for the valve's own run-time cutoff, which pauses with `0x40` (§4.1) | live |
| Broken stop | An all-zero `primaryValve1` is **ignored** — the "can turn on but never off" bug in other libraries. It is the whole word that is inert, not its `0x00` first byte: every valve-1 word below 25.6 °C starts `0x00` too (full cold is `0000C8xx`). On a two-valve system it voids the **whole** command: `v1=00000000 v2=11849C01` opened nothing, while `v1=0185C800 v2=1185C801` (valve 1 addressed and closed) opened valve 2. A zone that should stay shut therefore gets an addressed word with mask `0x00` | live |
| Full cold | Temperature `0` °C: the valve stops mixing in hot water. The app's slider has a `COLD` stop one below the minimum that sends this. It does not make freezing water: the cold supply bottomed out near 60 °F while the setpoint read 32 °F | app + live |

### 3.2 Word details the app adds to valve_hex.md

- **°F setpoints use a lookup table, not arithmetic.** The table covers 59–122 °F and is unchanged from 3.0.1 to 3.0.6 (`db0/c.java` `A()`). Celsius sends whole degrees ×10. Outside the table the app sends 0. The table differs from arithmetic at 16 entries — 87, 89, 91, 93, 96, 98, 100, 102, 105, 107, 109, 111, 114, 116, 118 and 120 °F — which is what makes the app's display rounding, whole °F as `round(c × 1.8 + 32)` half-up, round-trip (app).
- **Byte 3 bit 7** (`skipWarmUp` on write) is set only by **bath fill** (§8). Its read meaning is `errorFlag`.
- **Decode** (`db0/c.java` `C()`):
  - temperature high bits `0x03`, atFlow `0x08`, atTemp `0x04` (byte 0);
  - flow = byte 2 / 4 (setpoint scale);
  - error `0x80` and pause `0x40` (byte 3);
  - measured temperature in bytes 4-5, measured flow in byte 6 (raw), error code in byte 7. The app writes the same measurement to all three per-outlet sub-objects (one reading per valve), and byte 4's upper six bits are unused.
- Words arrive over MQTT as lowercase hex (live).

### 3.3 Preset words and temperatures

- **A preset word is 3 bytes:** `[byte0][temp low][flow]`.
- **Byte 0 carries the outlets at different bit positions from a command word**: `0x04` outlet 1, `0x08` outlet 2, `0x10` outlet 3, plus `0x01` for the temperature high bit (live).
- **Ids share one space.** Presets are 1-11 and experiences are 17 and up (`db0/c.java`). Slot 1 is the mandatory default shower (§3.4).
- **A preset's °F temperature is arithmetic**, `(F − 32) × 0.5555` formatted to one decimal. So 102 °F → 389 (0x185), 100 °F → 378 and 120 °F → 489. That disagrees with the solo-write table at 13 of the 29 values from 92 to 120. A running preset's temperature then shows up in the valve word, so a 389 in a `GCS_SOLO_STS` can come from a favorite.
- **A preset's flow** is outlet 0's `defaultFlowrate` × 4, not a fixed 100% (app).
- **A preset carries its own `time`**, a second limit independent of `maximumRunTime`. The lower of the two stops the shower (live) — but only for a session that preset started; see §3.4 for why that excludes preset 1.

### 3.4 Preset 1, the default shower

Most of this was established by the [kohler-anthem-plus](https://github.com/frozenmartini/kohler-anthem-plus) project (its 0.4.7, 2026-10-19, `docs/gcs/api.md`, "Preset 1 is a stored template"); "live" below means live on that project's hardware unless it says otherwise.

- **Preset 1 is a stored template, not a scene Kohler runs.** The valve has no place of its own for its default outlets, so the setup wizard keeps them, with the default temperature and flow, in preset 1 "Default shower". Every client hides it from its preset lists (`p315jj/h.java:1913-1918`, Konnect 3.0.1) (app).
- **Starting the default shower sends preset 1's words as a direct write.** The app defaults its start screen to preset `"1"` (`Pi/A.java:472`) and builds a `solowritesystem` from preset 1's outlets plus the on-screen temperature and flow (`Pi/A.java:787`) (app, 3.0.1). Simple Start on the valve's card, pressed 2026-10-19: `presetOrExperienceId` `0` throughout, warm-up first, then exactly preset 1's outlets (`Valve1 0184c8` none, `Valve2 0584c8` outlet 1) (live).
- **Confirmed on this integration's controller-free K-28210 pair, 2026-10-22.** On one valve, Simple Start, the touchscreen's own start and `Shower on` each reported `01893502` — Showerhead, 39.3 °C, flow `0x35` (26.5 %), `presetOrExperienceId` `0` — byte for byte. So whatever the decompile's "on-screen flow" is, here it is preset 1's stored flow, not a fixed 100 % (live). About two minutes after each session ended, both valves reset their idle word to the default temperature and the default shower's flow (`0x35`, and `0x31` on the valve whose preset 1 holds `0x30`) — the stable idle bytes `GcsState.flow_is_live` warns about (live).
- **So preset 1's `time` is never used.** It holds Kohler's `1800`. It only ends a shower when preset 1 is *run as a preset* (`controlpresetorexperience {preset: "1"}`): two such runs on 2026-08-17 paused at 30:00 with every outlet at `maximumRunTime` 3600 (live).
- **The app rewrites preset 1 on every default change.** Setting the default temperature to 103 °F rewrote its words (`0189c8` / `0589c8`) and its `time` to `1800`, replacing a stored `3600` (live, 2026-10-19). Only a temperature change was tested.
- **What this integration does since 1.08:** reads preset 1's words from `gcs-preset` and from every `GCS_PRESET_STS` push, and the `Shower on` switch sends them as one `solowritesystem`. A zone the preset opens nothing on goes as an addressed word with mask `0x00` (§3.1), and a word failing the scald check refuses the whole start. It never activates or writes preset 1, so `presetOrExperienceId` stays `0` and only `maximumRunTime` ends that shower.
- **Up to 1.07 it did both:** `Shower on` activated preset 1, which woke the 1800 s timer, and setup rewrote that `time` to `3600` whenever it differed. An install that ran those versions may still hold `3600`; nothing reads it, and the app puts `1800` back at the next default change.

## 4. Settings ranges (what the app offers)

| Setting | Range | Notes |
|---|---|---|
| Max Temperature (scald limit) | 33-48 °C, shown as 92-118 °F (newer screens); 95-120 °F (older) | A **setting**, not a hardware limit; seen changing 450 → 477 tenths live |
| Default Temperature | 59 °F / 15 °C up to the current max | |
| Zone temperature slider | `COLD` (min − 1, sends 0), then the minimum (59 °F) up to the current `maximumOutletTemperature`, taken from each valve's first outlet (`outletConfigurations[0]`) | `qa0/p.java`, `db0/c.java` `n0()` |
| Flow | Max-flow setting 5-50 setpoint (20-200 bytes); default flow minimum 4 setpoint (16 bytes); shown as a % of max. Each zone's slider is bounded by its valve's first outlet's min and max; percent is `value × 100 / maximumFlowrate` (`jj.h$a.X`), the inverse rounded; the encoder, `round(setpoint × 4)`, has no clamp of its own | `0x10`-`0xC8` on the wire |
| Max Shower Duration | 15, 20, 25, 30, 45, 60 min (newer); 15-30 (older); written as minutes × 60 | 3.0.1 misread anything above 30 min as 25; fixed in 3.0.5. `3600` is live-verified writable; 35, 40, 50 and 55 min are untested |

`deviceconfig.properties` in the APK (`maxFahrenheitValve2 = 100`, and so on) holds placeholders that GCS code never reads. The app overwrites them with the valve's own `maximumOutletTemperature`.

**Flow bytes written directly are kept.** 74 and 100 were sent with one to three outlets open; the echo matched exactly and the other zone was untouched (live). The first-generation touchscreen instead rewrites both zones' flow whenever its flow panel opens, with linked-zone scaling and its own ceiling: the same outlets topped out at 200 in one session and 69 in another (live).

### 4.1 Run-time limit

How the valve's Max Shower Duration (`maximumRunTime`) actually ends a shower. All **live**, from 156 zone sessions captured 2026-08-07 to 08-14 (spanning a change from 3600 s to 900 s) and five later case studies.

- **Set per outlet, timed per zone.** The clock starts when a zone goes from nothing flowing to something flowing. It **does not reset** when outlets change within the zone: opening a second head, closing the first or swapping between them leaves it running. Timing each outlet separately misses most cutoffs in sessions where someone moves between heads.
- **A zone is "flowing"** when its word has outlet bits set and the `0x40` pause bit clear.
- **At the limit the valve pauses that zone**: byte 3 becomes `0x40` and the outlet bits are cleared in the same message. Nothing else marks it as a timeout: `currentSystemState` stays `normalOperation`.
- **It fires slightly early**: −0.08 to −0.23 s against the limit. All 11 cutoffs in the corpus landed within 1.32 s of a limit. No other pause came within 334 s of one, and no `0x00` stop within 123 s.
- **A preset-driven session pauses every zone the preset owns** when one of them expires, not only that zone.
- **The outlets of one zone can disagree.** The app writes the duration one outlet at a time and stops at the first failure, so a lost write strands the old value on the rest (seen 2026-09-10: 1800 s on two outlets, 3600 s on the third).
- **Three limits can end a shower, and the shortest wins:**
  - the valve's `maximumRunTime`, which pauses (`0x40`);
  - the **Anthem Plus controller's** own `maxShowerDuration` ([hub_controller.md](hub_controller.md)), also timed per zone, which **stops** (`0x00`) and fires slightly late (+0.20 to +1.00 s);
  - a **preset's `time`** (§3.3), for a session that preset started — never the default shower, which no client runs as a preset (§3.4).
- **Who stops how:** the valve's timer, the Konnect app and the first-generation touchscreen use `0x40` (the touchscreen on both zones). The controller and this integration use `0x00`. So a `0x40` pause near the limit cannot be told apart from an app stop at the same moment.

## 5. Warmup

| Mode | Written by the app? |
|---|---|
| `warmUpDisabled` | yes |
| `warmUpAllOutletsWithNoStartDelay` | yes |
| `warmUpSelectedOutletsWithNoStartDelay` | yes |
| `warmUpAllOutlets`, `warmUpSelectedOutlets` | **read only**, treated as "enabled". No delay is defined anywhere; `delayStart` is echoed, never interpreted |

- `warmUpState.state` reports whether warmup is running now: `warmUpInProgress` / `warmUpNotInProgress`. ⚠️ "NotInProgress" ends with "InProgress", so compare the whole value.
- The app picks **selected outlets** whenever its selected-outlets option is on **or** `uiConfig.waterSavingMode == "Enabled"` (app, `jc0/o.java`). Water-saving mode therefore forces selected-outlet warmup.
- The app writes `warmUpDisabled` before it customises outlets.
- The app won't change the mode while any outlet on either valve is on: it reverts its own control without calling the API. Whether the valve enforces this is untested (app).
- An Anthem Plus controller's web UI writes `warmUpDisabled` on every signed-in use (live). About a minute of configuration re-sync (outlet configs, presets, experience snapshots) comes before it, and the controller's `SYSTEM_STS: SYSTEM_READY` lands 7–9 s after the disable (live). No spontaneous disable has been seen on a controller-free account; after a reboot the valve can restate `warmUpDisabled` without the mode having changed (live).

**Writing the mode** (live):
- After a `warmup` 200, `gcs-state` still returns the old mode until about 3 s later, and the valve's `GCS_WARM_STS` echo arrives about 3.4 s after the write, so a read-back has to retry.
- The cloud accepts a write (200) that the valve ignores while warm-up is disabled at the fixture itself (inferred from the read-back).
- The mode survives a power cycle; the valve re-announces it with `GCS_WARM_STS` about 4 s after boot. It doesn't announce it when a client connects: across 74 captures the first `GCS_WARM_STS` came between 137 s and 7 h in, so read the mode over REST.
- A valve can report no mode at all — no `warmUpState` in `gcs-state` and no `GCS_WARM_STS` — as both valves of the controller-free K-28210 pair did. A write to such a valve returns 200 and may be ignored.

**How a warm-up runs** (live, 31 natural endings):
- With warm-up enabled, any write that opens an outlet first runs the warm-up set. The valve's first report comes 1.1–2.1 s after the REST call returns and already carries `warmUpStatus`. The longest warm-up measured was 69 s.
- **While warming up, the valve reports the warm-up's outlets** — every outlet of its warm-up set (`0x07`, `0x03`) — not the shower's. A write during the warm-up moves the warm-up onto the outlets that write names, so a change built from the reported mask turns the warm-up into a shower on every warm-up outlet.
- When the water is warm the valve **pauses for two minutes**: `0x40` on both zones, outlets cleared. Outlets picked at the wall during the pause continue the session; otherwise it ends. The pause follows `warmUpStatus` clearing within 0.2 s, sometimes after one transitional report that still shows all or part of the warm-up set.
- A warm-up restarted from the wall showed as a plain `0x00` stop, with a new warm-up 14 s later (one observation).

## 6. Touch-panel UI config

Each touch interface has its own record. Records arrive in `gcsadvancestate` `setting.uiConfig[]`, keyed by `ui` (`UI1`, `UI2`), and are pushed as `READ_GCS_UI_CFG`, keyed by `UI` (app). The app merges a push only when it carries exactly one record, matched on that key (`db0/c.java` `Z0`). UI1 appears to belong to valve 1 and UI2 to valve 2 (inferred).

- **Settings fields:** `temperatureUnits`, `flowUnits`, `standbyLighting`, `timeFormat`, `demoMode`, `bathFillPresetId`, `delayStart`, `toggleOutlets`, `waterSavingMode`, `accessibilityMode`, `backLight`, `hapticFeedback`, `sounderVolume`, `defaultHomeScreen`, `proximitySensorMode`, `proximitySensorDistance`, `proximitySensorTime`, `notification`, `defaultMemory`, `language`, `languageRegion`.
- **Identity and status fields** (REST only): `status`, `firmware`, `bootloder` (sic), `installedDate`, `model`, `serialNo`. The record also carries a second spelling, `standByLighting`.

**What the app lets a user change.** Settings → **Control Panel** (`dc0/n.java`, view model `dc0/s.java`), reachable in both the old and the new control screens:

| Label | Fields | Read | Written |
|---|---|---|---|
| Panel Language | `language`, `languageRegion` | the pair | `en`/`US`, `en`/`GB`, `es`/`MX`, `fr`/`CA`, `zh`/`CN` |
| Panel Units | `temperatureUnits`, `flowUnits` | `F`+`Gallons` or `C`+`Liters`; a mixed pair selects neither, and Save waits for one | always the pair |
| Haptic Feedback, Accessible Display | `hapticFeedback`, `accessibilityMode` | `Enabled`/`Disabled` | `1`/`0` |
| Display Brightness | `backLight` | an integer | `1`–`10` |
| Default Screen / Wake Screen | `defaultHomeScreen` | Home 0, 64, 192; Favorites 2, 66, 130, 194; anything else the outlets | Outlets `1`, Home `0`, Favorites `2`, **plus 192 when valve 2 has outlets** |

The old control screens also have **Flow Control** (`ic0/p.java`), bit 2 (`0x04`) of `notification`; the new screens hide it. **`waterSavingMode`** is a low-flow mode set at setup: it forces selected-outlet warmup (§5), and the app's new screens then run one outlet at a time. The app never changes it. **`defaultMemory`** is the setup-complete flag: `1` once setup has finished, `255` before; anything but `1` makes the app demand the whole setup again.

**The write.** `POST /platform/api/v1/commands/gcs/writeuiconfig`, body `{deviceId, sku: "GCS", tenantId, gcsUIConfigModel: {…}}`: the **whole record**, every value a string, a null left out. The Control Panel's Save (`dc0/s.java` `M()`) sends **one POST per record**, in the read's order, each after the last succeeded, stopping at a failure. For each record:

- copied as read: `ui`, `standbyLighting`, `timeFormat`, `demoMode`, `bathFillPresetId`, `delayStart`, `toggleOutlets`, `sounderVolume`, `proximitySensorMode`, `proximitySensorDistance`, `notification`, `defaultMemory`;
- `waterSavingMode`: `1` only if it reads exactly `Enabled`, else `0`; left out when null;
- the shared settings, **taken from the first record** and written to every one: the units, `accessibilityMode`, `hapticFeedback`, `backLight`, `defaultHomeScreen` (normalised as above, which drops its 64/128 flags and turns 255 into 1), `language`, `languageRegion`;
- sent empty: `standByLighting`, `proximitySensorTime`, `status`, `firmware`, `bootloder`, `installedDate`, `model`, `serialNo`.

Save is refused while any outlet is open ("Settings cannot be saved while any outlet is running."), and enabled only when something differs from the first record. Switching Accessible Display on asks first. Nothing is re-read afterwards.

⚠️ **Why a careless write is dangerous.** A switch read in any form but `Enabled`/`Disabled` is written as `0`, which for `waterSavingMode` silently turns off a regulatory low-flow mode. A `defaultMemory` written as anything but `1` sends the owner back through setup. And a stale record undoes changes made at the panel since. The record shapes above are the app's; on 2026-10-29 an owner changed the language, units, wake screen, brightness, haptic feedback and accessible display from Home Assistant on an Anthem valve and the panel followed, so the records read and are written as described. Flow Control's `notification` bit has not been tried.

## 7. MQTT messages

Dispatch on `data.code` (see [platform §4.3](platform.md#43-message-envelope)).

| Code | Attributes | Notes |
|---|---|---|
| `GCS_SOLO_STS` | `primaryValve1`, `secondaryValve1` (16-hex words); `currentSystemState`; `presetOrExperienceId`; `warmUpStatus`; `totalFlow`, `totalVolume`; `firmwareUpdate`, `BLEPairing`, `BLEConnected`, `IoTActive`, `IoTProvision`; `configChangeIndent` | The live valve word (live). `presetOrExperienceId` latches for the session, is cleared by pause **and** stop, and is never set during warmup — a preset started during a warm-up never shows its id, since the warm-up's end pauses the valve (all 12 `warmUpInProgress` samples carried `0`). `IoTActive` read `Active` in 1020 of 1020 samples: a disconnected valve cannot publish, so it says nothing about reachability. The first report after a write arrives 1.1–2.1 s after the REST call returns |
| `GCS_PRESET_STS` | `presetId`, `name`, `time`, `volume`, `Valve1`…`Valve8` (3-byte preset words) | Pushed on every create, edit, rename and delete, and for all slots after a reboot. A delete arrives as an empty name (live). The valve keys are capitalised here, where `writepreset` sends them lowercase, and slots 3-8 carry stale junk (`0000c0`, `080020`) — read `Valve1` and `Valve2` only (live, kohler-anthem-plus) |
| `GCS_WARM_STS` | `warmup` (all lowercase, unlike REST's `warmUp`) | live |
| `READ_GCS_OUTLET_CONFIG_CFG` | One outlet record, MQTT spelling (§2.1) | Unprompted, about twice a session, one outlet per message (live) |
| `READ_GCS_UI_CFG` | One UI record (§6) | live (shape: app) |
| `READ_GCS_EXPERIENCE_STS` | — | The app matches it and does nothing |
| `DEVICE_REBOOT_STS` | `code` | The app only reacts during first-time setup ("re-enter your shower customization") |
| `GCS_DELETE_PROFILE_STS` | `status` (`"true"`) | After Remove Product (app) |
| `READ_DISPENSED_WATER_VOLUME_STS` | `volume` (string) | A running counter, requested by `bathfillervolume` (app) |
| `READ_ALL_INTERFACES_FIRMWARE_VERSION_STATUS_INFO`, `GCS_RECIEVED_STS` | — | Seen live; **not in the app at all** |

`currentSystemState` values:
- **Captured:** `normalOperation`, `showerInProgress`.
- **Acted on by the app, never captured:** `error` (case-insensitive) — a fault; `FirmwareUpdate` — an install is running.

The app's fault rule is `currentSystemState == error`, **or** `errorFlag` set on either valve word (app). On hardware only the fault log has fired so far: an active `Algorithm` error `37` on `Valve 1`, while the error flag was set in 0 of 992 captured words (live). See [platform §8](platform.md#8-diagnostics-fault-logs).

Idle words can carry transient flow bytes in recurring pairs (34.5 %/82.5 %, for example), with `totalFlow` dropping to 2 in the same message and back seconds later (live). An idle valve's steady flow byte is the default shower's (§3.4).

**Cloud reachability.** A valve can drop off Kohler's cloud while still working from the wall, and comes back only after a power cycle (live). Reachability is read from `connectionState` (§2, [platform §5](platform.md#5-device-connection-state)), not from any MQTT field.

## 8. Bath fill

A bath fill is a **preset** with a fill amount: `volume` (litres, as a string) above 0, `time` 1800, and words that open only the tub filler (type 21). Both the REST preset list and `GCS_PRESET_STS` carry `volume` and `time` (app).

- **Running one** is reachable in 3.0.6 from the Home and All Presets screens, which open a bath-fill page (Temp, Flow Rate, Fill Amount, Start/Stop) for any preset with `volume` above 0 (`gr0/j.java`). Start is `startpreset` there, or `controlpresetorexperience` from the new screens, which list it as an ordinary favorite. The app sets no timer and never watches the volume: ending the fill is left to the valve, presumably at the preset's `volume`, and in any case at its `time` (inferred; **live check**). The old control screen shows "Bathfill is running" while one runs.
- **Creating one** is only in the old control screens (`AnthemControlActivity`), which a remote-config flag hides by default. It needs a type-21 outlet, and calibrates by filling the tub once by hand:
  1. Start: `solowritesystem` opening only the tub filler(s) at the preset's temperature and flow, with **bit 7 set on both words**. Refused while any outlet is open.
  2. While it fills, and once after it stops, `POST bathfillervolume {index: "0"}`; the valve answers `READ_DISPENSED_WATER_VOLUME_STS {volume}`, read as litres.
  3. Stop when the user presses Stop: the same words, no outlets, pause and bit 7 set (byte 3 `0xC0`).
  4. The fill amount is the reading after, less the one before if the valve was paused; anything under 1 L becomes 4 L (`bathfillDefaultFillAmount`).
  5. `createpreset` with `time` `"1800"` and that `volume`.
- Editing a bath-fill preset from the new screens writes `volume` `"0"`, which turns it into an ordinary preset (inferred).

The units are the app's assumption, and none of this is live-verified.

## 9. Firmware

There are two parts:
- the valve: `firmware/gcs/{id}?releasetarget=Public`;
- its gateway: `firmware/gcs/gateway/{id}?releasetarget=Public`.

The shapes and install flow are in [platform §7](platform.md#7-firmware). The configuration's `about` lists three versions: interface (`uI2`), valve (`primaryValve`, plus `secondaryValve1`) and gateway. They are genuinely different numbers (2.2, 10 and 00.74 on one system, live), and two valves on one account can differ while the app shows one (live). `secondaryValve1.firmware` is a placeholder `0` on a single-valve system, and `assetsFirmware` is the artwork bundle (2.0 beside interface 2.2), not the interface firmware (live). A top-level `about.firmware` mapping is the gateway's version (it equals `about.gateway.firmware`) (live).

OTA records (`otaReportedProperties`, `firmwareUpdate`) carry `firmwareType`: `Application` is the firmware, `Assets` the artwork bundle with its own version (2.00 beside 2.20). `updatedVersion` is what runs now and `initialVersion` what ran before the update. Some accounts' records have no `firmwareType` (live, two K-28210 valves).

## 10. What this integration implements

| Capability | Status |
|---|---|
| Outlet switches, zone temperature (`COLD` to max) and flow, `custom_shower`, `send_valve_hex` | ✅ |
| Presets (start), warmup, outlet settings (12-key write) | ✅ |
| Default shower from preset 1's words, never activating or writing it (§3.4) | ✅ since 1.08 |
| Time-left attributes (zone clock against `maximumRunTime`, §4.1) | ✅ |
| Endless Shower (restart a shower the valve's timer ended) | removed 2026-10-08 |
| Experiences (start/stop via `controlpresetorexperience`) | ✅ start live (2026-10-22, `presetOrExperienceId` `17` reported), stop app-confirmed only |
| Restart (`valvereset`) | ✅ app-confirmed only, disabled by default |
| Firmware update entities (read-only) | ✅ |
| Fault: error flag plus `currentSystemState == error`, and the fault log's active entries (read at setup, on each reconnect and twice a day); the whole log in diagnostics | ✅ |
| Connection state (`Disconnected`) | ✅ |
| Touch-panel settings (§6): Panel Language, Panel Units, Wake Screen, Display Brightness, Haptic Feedback, Accessible Display, and Flow Control (off by default, as the new app hides it), written as the Control Panel writes them: every record, whole, from a fresh read; refused while water runs, and when a record reads in a form the app doesn't expect or isn't set up. Water-saving mode is a reading | ✅ live-verified on an Anthem valve (2026-10-29), except Flow Control: app-confirmed only, untested |
| Bath fill (§8): a preset's fill amount (`bath_fill_litres` on the Favorite select), and Bath Filling while one runs, on a valve with a tub filler. Started like any preset | ✅ app-confirmed only |
| Creating bath-fill presets (§8's calibration), experience management, factory reset | ❌ documented only |
