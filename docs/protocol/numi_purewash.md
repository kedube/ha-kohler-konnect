# Kohler Numi 2.0 toilet and Purewash E930 bidet seat: cloud protocol (app-side)

**Source:** Kohler Konnect for Android **3.0.6** (version code 260), decompiled
with jadx 1.5.6 (the same decompile as [sensate_faucet.md](sensate_faucet.md)).
Covers SKU `NUM` (Numi 2.0 intelligent toilet) and SKU `BLD` (Purewash E930 bidet
seat, whose code name throughout the app is "Blade").

**Caveat.** Everything here comes from the app's side: what the 3.0.6 app sends,
what it expects back, and how it reacts. It does not describe what the toilet, the
seat or the Kohler cloud actually does. Nobody on this project owns either product,
so **nothing in this chapter is live-verified**: every fact is **app** in the sense
of the [README](README.md#confidence) unless it is marked **inferred**. Field names
and request shapes are exact, because they come from Gson `@SerializedName` models
and Retrofit annotations. Value vocabularies come from the enum the app serializes
(§2.3) and ranges from its sliders; the server may accept more or less. Items marked
**live check** are collected in §10. "Not found in the decompile" means just that;
the behaviour may still exist server-side. These screens contain several app bugs
(§4.3); where the app's behaviour and its evident intent differ, both are given.

Shared mechanics (auth, API base and APIM key, `customer-device`, mobile
registration, the IoT Hub MQTT transport and envelope, the response-body
`statusCode` table, the general firmware and water-usage patterns) are in
[platform.md](platform.md). This file covers only what is specific to the two
products.

**One protocol, two products.** The Purewash is a Numi in protocol terms. Both use
the same verbs, the same request classes (the Blade interface imports the Numi
models), the same state and settings shapes, the same value strings and the same
`NUMI_*` MQTT codes. Only the path segment (`numi` versus `blade`), a few state
fields, and the feature set differ (§1.3). A shared "toilet" base parameterized by
SKU fits the code.

**Finding things in the decompile.** Code paths are relative to jadx's `sources/`
root; `strings.xml:N`, `arrays.xml:N` and `layout/…:N` are under jadx's
`resources/res/`. Obfuscated packages change between builds, so every citation also
names a string, field or method to grep for. Abbreviations:

| Short form | Full path under `sources/` | What it is |
|---|---|---|
| `NumiApi` | `com/kohler/hermoth/data/network/NumiDeviceApiCall.java` | Retrofit interface, Numi |
| `BladeApi` | `com/kohler/hermoth/data/network/BladeDeviceApiCall.java` | Retrofit interface, Purewash |
| `DeviceApiCall.java`, `PlatformApiCall.java` | `com/kohler/hermoth/data/network/` | Shared interfaces (configuration, usage, firmware, RTC) |
| `ApiConstant.java` | `com/utils/network/retrofit/proxy/` | Endpoint constants |
| `numi/model/…`, `blade/model/…` | `com/utils/network/retrofit/proxy/device/numi/model/`, `…/device/blade/model/` | Request and response models |
| `ui-model/…` | `com/kohler/hermoth/ui/numi/model/` | State and settings sub-objects; `ui-model/mqtt/` holds the MQTT models |
| `NumiControlActivity.java` | `com/kohler/hermoth/ui/numi/control/` | Numi control shell: MQTT dispatch |
| `BladeControlActivity.java` | `com/kohler/hermoth/ui/numi/blade/control/` | Purewash control shell: MQTT dispatch |
| `NumiSettingActivity.java` | `com/kohler/hermoth/ui/numi/settings/` | Settings shell for **both** SKUs |
| `la0/b.java` | | Repository wrapper; every call passes `"v1"` for `{version}` |
| `cs0/l0.java`, `fs0/z.java`, `es0/c0.java` | | Numi activity, home and dial view models |
| `lr0/w.java`, `or0/o.java`, `nr0/d0.java` | | Purewash activity, home and dial view models |
| `qs0/m.java`, `os0/o.java`, `ts0/s.java`, `ns0/y.java`, `bs0/b0.java`, `us0/n.java` | | Settings view models (wash, dryer, seat, Numi auto config, Purewash auto config, settings home), shared by both SKUs except the two auto-config ones |
| `ks0/b.java`, `ks0/a.java` | | The value enums (§2.3) |
| `vs0/b.java`, `vs0/a.java` | | Numi and Purewash value converters (dial position to string and back) |

---

## 1. SKUs and the shared base

### 1.1 SKUs

| SKU | Product | Names in the app | Evidence |
|---|---|---|---|
| `NUM` | Numi 2.0 intelligent toilet | "Numi 2.0" (`text_device_numi`, strings.xml:1763); "Numi" (`text_my_numi`, :2301) | product tile `rt0/h.java:30` (`productModel.x("NUM")`); product list `zn0/i.java:247` |
| `BLD` | Purewash E930 bidet seat | "Blade" (`text_device_blade`, :1748); "Purewash" (`text_my_blade`, :2295; `text_blade_model`, :1519); "Purewash E930" (`txt_blade_name`, :3088) | product tile `rt0/i.java:30` (`productModel.x("BLD")`); product list `zn0/i.java:283` |

No other SKU for either product appears in the decompile, and nothing in the app
distinguishes model variants or model numbers. One provisioning check accepts
`"NUM"` or `"Numi"` case-insensitively as the Numi SKU (`dr0/e.java:26,77`,
set `{"SFC","GCS","NUM","Numi"}`); nothing suggests the cloud ever reports `Numi`.
That same check reads `configuration.about.model` for a Numi
(`dr0/e.java:77-80`), so the model number is probably in the configuration read
(§2.5; **inferred**).

### 1.2 How the app recognizes each one

- **`customer-device`** gives each device's `sku`. The app compares it with
  `String.equals`, so case-sensitively: the dashboard routes `BLD` to
  `BladeControlActivity` (`fr0/f.java:1102`, then `:1747-1750`) and `NUM` to
  `NumiControlActivity` (`fr0/f.java:1142`, then `:1023-1026`).
- Shared screens branch the same way: settings (`NumiSettingActivity.java:1106`,
  `:1213`), firmware (`ui/ota/f.java:149-175`), usage (`rg0/t0.java:247,297`;
  `uq0/z.java:727-731`), About (`ep0/i0.java:1094,1122`).
- **Every request takes `sku` from the device record** (`getSku()`, e.g.
  `es0/c0.java:551`, `nr0/d0.java:1306`, `fs0/z.java:609`). The one exception is
  the time-zone request, which hard-codes `"NUM"` (`cs0/l0.java:627`).
- The MQTT envelope's `sku` is `NUM` or `BLD`; the settings screen compares it
  case-sensitively (`NumiSettingActivity.java:1242`).
- Platform §3 still applies: branch on `sku`, not on the device id. (Provisioning
  does test whether an id contains `NUM`, `BLD` or `KOH`, `tr0/v.java:1154`.)
- Setup is out of scope here. The Numi's soft-AP SSID prefix is `NUM-`
  (`ht0/q.java:123-124`); that table has no `BLD-` entry, and the Purewash has
  its own setup flow under `ui/numi/blade/provision/`.

### 1.3 What the two share and where they differ

| | Numi 2.0 (`NUM`) | Purewash E930 (`BLD`) |
|---|---|---|
| Retrofit interface | `NumiApi` | `BladeApi`, which reuses the Numi request classes (`BladeApi:11-19`, imports) |
| State, settings | `numi-state`, `numi-setting` | `blade-state`, `blade-setting` |
| Commands | `/platform/api/v1/commands/numi/…` | `/platform/api/v1/commands/blade/…`, same verbs and bodies |
| State model | `NumiStateModel` | `BladeStateModel`: same keys; `seat` and `light` differ (§2.2) |
| Settings model | `NumiDefaultSettingsModel` | the same class |
| Value strings | enum `ks0/b` | enum `ks0/a`: the same constants minus `Enable`/`Disable` |
| MQTT codes | `NUMI_*_STS` | the same `NUMI_*_STS` codes; no `BLADE_*` code exists |
| Washes | front, rear | front, rear, boost ("defecation"), kid |
| Dryer | temperature and fan speed | temperature; fan speed only in the stored default (§3.3) |
| Flush | full and eco | code present, buttons hidden (§3.6) |
| Cover (`lid`), seat ring (`ring`), seat heat, UV clean, wand, power save | yes | yes |
| Auto settings | night light, auto flush, auto-cover distance, auto clean (deodorizer and bowl mist hidden) | auto cover, deodorizer fan, bowl mist, long-sitting reminder, auto temperature, auto clean, night light Off/Low/High |
| Ambient light, circadian light, music, Alexa, microphone mute | yes | no |
| Time zone (`setrtccontrol`) | sent when the state lacks one | not sent by the control screens |
| Factory reset | `commands/numi/factory-reset` | `commands/blade/factoryreset` |
| Usage, firmware type | `numi-usage`, `numi` | `blade-usage`, `blade` |

---

## 2. Reads

### 2.1 Summary

| # | Method | Path | Retrofit method, constant | `la0/b.java` | Response |
|---|---|---|---|---|---|
| R1 | GET | `/devices/api/v1/device-management/numi-state/{deviceId}` | `getNumiStateData` (`NumiApi:71`), `GET_NUMI_STATE_API_V` (`ApiConstant.java:151`) | `:4853` | `numi/model/NumiStateResponse` |
| R2 | GET | `/devices/api/v1/device-management/blade-state/{deviceid}` | `getBladeStateData` (`BladeApi:61`), `GET_BLADE_STATE_API_V` (`:139`) | `:2914` | `blade/model/BladeStateResponse` |
| R3 | GET | `/devices/api/v1/device-management/numi-setting/{deviceid}` | `callNumiSettingsAPI` (`NumiApi:41`), `GET_NUMI_SETTINGS_API_V` (`:149`) | `:4393` | `numi/model/NumiSettingsResponse` |
| R4 | GET | `/devices/api/v1/device-management/blade-setting/{deviceid}` | `callBladeSettingsAPI` (`BladeApi:37`), `GET_BLADE_SETTINGS_API_V` (`:138`) | `:9327` | `blade/model/BladeSettingsResponse` |
| R5 | GET | `/devices/api/v1/device-management/numi-configuration/{deviceid}` | `getNumiConfiguration` (`DeviceApiCall.java:202`), `GET_NUMI_CONFIGURATION_SETTING_API_V` (`:147`) | `:3439`; **also for Purewash** at `:1925` (§2.5) | `DeviceConfigurationResponse` |
| R6 | GET | `devices/api/v1/device-management/{deviceType}-configuration/{deviceid}`, `{deviceType}` = `numi` or `blade` | `getDevicesConfiguration` (`DeviceApiCall.java:181`), `GET_DEVICE_CONFIGURATIONS_V` (`:145`) | via `z1(id, "numi")` and `z1(id, "blade")` (`ui/ota/f.java:234,214`) | `DeviceConfigurationResponse` |
| R7 | GET | `/devices/api/v1/device-management/numi-usage/{deviceId}?FromDate&ToDate&Interval` | `getNumiWaterUsageData` (`DeviceApiCall.java:205`), `NUMI_WATER_USAGE_API` (`:181`) | `:5343` | `WaterUsageModel` (§6.2) |
| R8 | GET | `/devices/api/v1/device-management/blade-usage/{deviceId}?FromDate&ToDate&Interval` | `getBladeWaterUsageData` (`DeviceApiCall.java:163`), `BLADE_WATER_USAGE_API` (`:64`) | `:3393` | `WaterUsageModel` (§6.2) |
| R9 | GET | `/platform/api/v1/firmware/numi/{deviceid}`, `…/firmware/blade/{deviceid}` | §6.1 | | |

**`{version}` is always `"v1"`.** Every Numi and Blade call in `la0/b.java` passes
the literal (for example `getNumiStateData("v1", …)` at `:4853`). The constant
`GET_BLADE_CONFIGURATION_SETTING_API_V` (`blade-configuration`,
`ApiConstant.java:137`) is never referenced.

### 2.2 State (R1, R2)

Top level, same keys in both (`numi/model/NumiStateResponse.java:14-47`,
`blade/model/BladeStateResponse.java:16-49`):

| Field | Type | App usage |
|---|---|---|
| `connectionState` | String | Only exactly `"Connected"` enables the controls (§7.1) |
| `lastConnected`, `createdTimestamp`, `updatedTimestamp` | **`int`** | Not read. As 32-bit `int`s they cannot hold epoch milliseconds, and Gson would then fail the whole read, so the server probably sends seconds or omits them (**inferred**, **live check**) |
| `id`, `deviceId`, `sku`, `tenantId`, `ioTHub`, `lastUpdatedSource`, `deviceConnectionEventSequenceNumber` | String | Not used by these screens |
| `state` | object | below |

`state` has the same keys in both (`ui-model/NumiStateModel.java:13-43`,
`ui-model/BladeStateModel.java:15-45`): `boostWash`, `clean`, `cleanStatus`,
`dryer`, `flush`, `frontWash`, `kidWash`, `light`, `progress`, `rearWash`, `seat`.
"Read" below means the model keeps a getter that app code calls (the build's code
shrinker removed the unused ones).

| Key | Values the app expects | Read | Meaning (from where the app shows it) |
|---|---|---|---|
| `rearWash`, `frontWash` `.state` (`ui-model/NumiWashModel.java:16-28`) | `On`, `Off` | yes | That wash is running (`ui/ota/f.java:741-749` `r()`) |
| …`.temperature` | `HeatOff` … `HeatHigh` (§2.3) | yes | Water temperature while running |
| …`.flow` | `FlowLow` … `FlowHigh` | yes | Pressure |
| …`.position` | `PositionNear` … `PositionFar` | yes | Wand position |
| …`.massage` | `ModeNormal`, `ModeOscillate`, `ModePulsate`, `ModeWave` | yes | Spray pattern. **Commands and MQTT call this `mode`** |
| `boostWash`, `kidWash` | wash shape | Purewash only | Boost and kid washes. The Numi model declares them; no Numi code reads them |
| `dryer.state`, `.temperature`, `.flow` (`ui-model/NumiDryerModel.java:16-22`) | `On`/`Off`; `Heat*`; `Flow*` | yes | Dryer running, temperature, fan speed |
| `seat.userStatus` | `UserSeated` (the only value tested); `UserLeave` (enum) | yes | Someone is seated. Gates spray and dryer (§7.2) |
| `seat.temperature` | `HeatOff` … `HeatHigh` (the icon map also accepts `Off`, `vs0/b.java` `I()`) | yes | Seat heat level |
| `seat.powerSave` | `On`, `Off` | yes | Power-save switch (`us0/j.java:855` `G3()`) |
| `seat.autoOpen`, Numi (`ui-model/NumiSeatAutoOpenModel.java:15-18`) | object `{state: On/Off, sensorDistance: RangeNear/RangeMid/RangeFar}` | yes | Auto cover and its sensor distance |
| `seat.autoOpen`, Purewash (`ui-model/BladeSeatModel.java:16`) | string `On`/`Off` | yes | Auto cover |
| `seat.autoSeatTemp`, `.autoWaterTemp`, `.longTimeSeatReminder`, Purewash (`BladeSeatModel.java:19-28`) | `On`, `Off` | yes | Auto settings (§3.10) |
| `seat.state`, `seat.heat.temperature` | | no | |
| `light.nightLight` | `On`, `Off` | yes | Night light setting |
| `light.ambientLight.{state, colorTheme, brightness}`, Numi (`ui-model/NumiAmbientLightModel.java:13-19`) | `On`/`Off`; a theme code (§3.11); `"1"` … `"10"` (**inferred**: the app shows it ×10, `gs0/v.java:877` `k0()`) | yes | Ambient (bowl) light |
| `light.circadianLight`, Numi | `On`, `Off` | yes | Circadian lighting (`gs0/v.java:709`) |
| `light.nightLightBrightness`, Purewash (`ui-model/BladeLightModel.java:15-18`) | `High`, `Low` (compared case-sensitively, `vs0/a.java` `m()`) | yes | Night light level |
| `clean.deo` (`ui-model/NumiCleanModel.java:15-24`) | `AirCleanOn`, `AirCleanOff` | yes | Deodorizer setting |
| `clean.preRinse` | `On`, `Off` | yes | Auto bowl mist setting |
| `clean.sanitizer` | `SanitizeOn`, `SanitizeOff` | yes | Auto clean setting |
| `clean.cleanMode` | | no | |
| `cleanStatus.manualClean` (`ui-model/NumiCleanStatusModel.java:16-28`) | `On`, `Off` | yes | Wand extended for manual cleaning |
| `cleanStatus.uvClean` | `SanitizeOn`, `SanitizeOff` | yes | Clean cycle running |
| `cleanStatus.timezone` | String (an IANA zone, **inferred**) | yes | A null value makes the Numi screen send the phone's zone (§3.12) |
| `cleanStatus.airClean`, `cleanStatus.rtcSentAfterReboot` (boolean) | default `AirCleanOff` | no | |
| `flush.auto` (`ui-model/NumiFlushModel.java:15-18`) | `On`, `Off` | yes | Auto flush setting |
| `flush.state` | | no | The enum's `FullFlushing`, `EcoFlushing`, `FlushingOff` are probably its values (**inferred**) |
| `progress` | | no | |

### 2.3 Value vocabulary

The wire strings are the names of the constants of `ks0/b.java` (Numi screens) and
its twin `ks0/a.java` (Purewash screens), sent with `toString()`. Neither enum
overrides `toString()`. The app converts dial positions with `vs0/b.java` (Numi)
and `vs0/a.java` (Purewash):

| Scale | Index and string | Labels the app shows | Converters |
|---|---|---|---|
| Temperature (water, dryer, seat) | 0 `HeatOff`, 1 `HeatLow`, 2 `HeatLowMid`, 3 `HeatMid`, 4 `HeatMidHigh`, 5 `HeatHigh` | Off, Lowest, Low, Medium, High, Highest | `vs0/b.java` `L()`/`K()`/`J()`; `vs0/a.java` `v()`/`u()`/`t()` |
| Pressure and fan speed | 1 `FlowLow`, 2 `FlowLowMid`, 3 `FlowMid`, 4 `FlowMidHigh`, 5 `FlowHigh` | Soft, Mild, Medium, Strong, Strongest | `G()`/`F()`; `q()`/`p()` |
| Position | 1 `PositionNear`, 2 `PositionNearMid`, 3 `PositionMid`, 4 `PositionMidFar`, 5 `PositionFar` | Nearest, Near, Middle, Far, Farthest | `E()`/`D()`; `o()`/`n()` |
| Spray pattern | 1 `ModeNormal`, 2 `ModeOscillate`, 3 `ModePulsate`, 4 `ModeWave` | Regular, Oscillate, Pulsate, Wave | `h()`/`B()`; `c()`/`l()` |
| Auto-cover distance (Numi) | `RangeNear`, `RangeMid`, `RangeFar` | Near, Medium, Far (plus OFF, which disables auto cover) | `i()`/`C()` |

Unknown strings map back to the middle setting (`HeatMid`, `FlowMid`,
`PositionMid`, `ModeNormal`). Booleans in configuration writes are the strings
`"true"` and `"false"`. The enum's other constants, and where they appear: `On`,
`Off`, `OFF`, `High`, `Low` (settings); `LowPower`, `DeviceRun`, `DeviceLowPower`,
`DeviceInitialize`, `DeviceDiagnostic`, `DeviceSleep`, `DeviceFirmwareUpgrade`
(MQTT `devicestate`, §4); `ErrorOccurred`, `NoError`, `DeviceNoError` (errors);
`AirCleanOn`/`Off`; `Spraying*`, `Drying*`, `UserLeave`, `UserSeated` (MQTT);
command verbs (§3); `NormalSpeed`, `SlowSpeed`, `FastSpeed` (light speed);
`Sleep`; `Connected`; `Enable`, `Disable` (power save, `ks0/b` only).

### 2.4 Settings (R3, R4): the stored defaults

`NumiSettingsResponse` and `BladeSettingsResponse` (`numi/model/…:14-29`,
`blade/model/…:14-29`) are identical: `createdTime`, `id`, `deviceId`,
`customerId`, `updatedTimestamp` (all String) and `setting`, a
`NumiDefaultSettingsModel` (`ui-model/NumiDefaultSettingsModel.java:13-43`):

- **Read:** `frontWash`, `rearWash`, `boostWash`, `kidWash` (wash shape), `dryer`
  and `seat` (only `seat.temperature` is used).
- **Declared, never read:** `boost` (Java field `kid`), `flush`, `lighting`,
  `audio` (`{state, volume, bass, bluetooth}`) and `clean`.

These are the "Default Settings". The dial shows them while nothing is running and
the live values while something is (`es0/c0.java:769` `R0()`). The settings screens
edit them (§3.5).

### 2.5 Configuration (R5, R6)

This is the shared `DeviceConfigurationResponse`; its fields are described in the
faucet chapter (§2.2, R2). For these products the app uses it only to finish a
firmware update, by comparing `configuration.about.firmware.version` with the
target (§6.1). Like every configuration reply it carries the `iot` block, which
must never be logged (platform §2.2).

**The Purewash reads both paths.** The settings firmware screen reads
**`numi-configuration`** for a Purewash too (`ps0/j.java:440,443` → use case
`ie0.a` → `he0/a.java:102` → `la0/b.java:13192` `o1()` →
`getNumiConfiguration` at `:1925`). The shared OTA screen reads
`blade-configuration` through R6 (`ui/ota/f.java:214`). Which one the server
answers for a `BLD` is a **live check**.

### 2.6 When the app reads

- **Numi screen:** R1 on resume (`NumiControlActivity.java:1873`), when the
  network comes back (`:1075`), after every start/stop reply (`:734-737` `S3()`)
  and when the MQTT reply timer runs out (`:191-200`). R3 follows the first R1
  (`cs0/l0.java:226-227` → `J()` at `:653`, log `callGetNumiSettingsData`).
- **Purewash screen:** R2 through `lr0/w.java:500` `J()` (log
  `callGetBladeStateData()`) and R4 through `:492` `I()`, on open and when the
  network comes back (`BladeControlActivity.java:978`).
- **Settings screens** read R3 or R4 when they open (`qs0/m.java:666`,
  `os0/o.java:661`, `ts0/s.java:675`); the Purewash auto-config screen reads R2
  (`bs0/b0.java:124`). The lighting sheet reads R1 (`gs0/v.java:577`).
- There is no polling. Between reads, the screens rely on MQTT (§4).

---

## 3. Commands

### 3.1 Summary

Commands are `POST /platform/api/v1/commands/<numi|blade>/<verb>`. The device id
travels in the body. Settings writes are `PATCH` on the settings document.

| # | Verb (after `commands/numi/` or `commands/blade/`) | Body model: fields | Retrofit, Numi / Purewash | `la0/b.java`, Numi / Purewash | Sent by |
|---|---|---|---|---|---|
| C1 | `spray-dryer` | `NumiControlStartStopRequest` / `BladeControlStartStopRequest`: `operation`, `deviceId`, `tenantId`, `sku` | `callWriteControlStartStopAPI` `NumiApi:65` / `BladeApi:55` | `:1338` / `:1823` | Dial Start/Stop (§3.2) |
| C2 | `{sprayType}`: `front-wash`, `rear-wash`, `dryer`, `heated-seat`; Purewash also `kids-control`, `defecation-control` | `NumiControlSprayDryerRequest` / `BladeControlSprayDryerRequest`: `flow`, `temperature`, `position`, `mode`, `deviceId`, `tenantId`, `sku` | `callWriteSprayDryerAPI` `NumiApi:68` / `BladeApi:58` | `:2333` / `:2822` | Dial changes (§3.3); seat heat sheet (§3.4) |
| C3 | `{type}`: `rear-wash`, `front-wash`; Purewash also `kids-control`, `defecation-control` | `NumiFrontRearWashPlatformRequest`: `deviceId`, `sku`, `tenantId`, `flow`, `temperature`, `position`, `mode` | `operateDefaultSettingAPI` `NumiApi:77` / `BladeApi:67` | `:1093` / `:11403` | Wash defaults (§3.5) |
| C4 | `dryer` | `NumiFrontRearWashPlatformRequest` | `operateDefaultDryerSettingAPI` `NumiApi:74` / `BladeApi:64` | `:602` / `:10954` | Dryer defaults (§3.5) |
| C5 | `heated-seat` | `NumiPlatformSeatRequest`: `deviceId`, `sku`, `temperature`, `tenantId` | `operateSeatHeatDefaultSettingAPI` `NumiApi:86` / `BladeApi:76` | `:1583` / `:11826` | Seat defaults (§3.5) |
| C6 | `flush` | `NumiOperateFlushRequest`: `flushType`, `deviceId`, `tenantId`, `sku` | `operateFlushAPI` `NumiApi:80` / `BladeApi:70` | `:12280` / `:7020` | Full and Eco Flush (§3.6) |
| C7 | `ring`, `lid` (the `{type}` route) | `IdSkuTenantIdRequest`: `deviceId`, `sku`, `tenantId` | `operateSeatAndCoverAPI` `NumiApi:83` / `BladeApi:73` | `:2078` / `:7496` | Seat and Cover (§3.7) |
| C8 | `sanitize`, `clean-wand` (the `{cleanType}` route) | `NumiControlStartStopRequest` / `BladeControlStartStopRequest` | `callWriteControlManualCleanWandSanitizeAPI` `NumiApi:62` / `BladeApi:52` | `:337` / `:843` | Clean and Manual Clean (§3.8) |
| C9 | `auto-power-save` | `PowerSaveEnableDisableRequest`: `deviceId`, `sku`, `state`, `tenantId` | `callNumiPowerSaveEnableDisableAPI` `NumiApi:38` / `callBladePowerSaveEnableDisableAPI` `BladeApi:34` | `:3240` / `:7970` | Power Save (§3.9) |
| C10 | `write-configuration` (Numi) | `NumiWriteConfigRequest` (§3.10) | `callWriteConfigAPI` `NumiApi:59` | `:12025` | Numi auto settings |
| C11 | `write-configuration` (Purewash) | `BladeWriteConfigRequest` (§3.10) | `callWriteBladeConfigAPI` `BladeApi:49` | `:11592` | Purewash auto settings |
| C12 | `lighting` (Numi) | `NumiLightingRequest`: `b`, `color`, `deviceId`, `g`, `luminance`, `msp`, `r`, `sku`, `tenantId` | `callSetLightingSettingAPI` `NumiApi:47` | `:8516` | Lighting (§3.11) |
| C13 | `multimedia-operation` (Numi) | `NumiMultimediaOperationRequest`: `deviceId`, `operation`, `sku`, `tenantId` | `callSetMultimediaOperationSettingAPI` `NumiApi:50` | `:8955` | Lighting on/off |
| C14 | `rhythm` (Numi) | `NumiMultimediaOperationRequest` | `callSetRhythmOperationSettingAPI` `NumiApi:53` | `:9373` | Circadian light |
| C15 | `setrtccontrol` (Numi path) | `NumiRtcValueRequest` (`device/model/numi/`): `deviceId`, `sku`, `tenantId`, `timeZone` | `callResponseNumiRTCTimeZoneAPI` (`PlatformApiCall.java:179`) | `:6607` | Automatic (§3.12) |
| S1 | `PATCH /devices/api/v1/device-management/numi-setting/{deviceid}/{washtype}` (Purewash: `blade-setting/…`), `{washtype}` = `rearwash`, `frontwash`; Purewash also `kidwash`, `boostwash` | `NumiFrontRearWashRequest`: `flow`, `temperature`, `position`, **`massage`** | `callFrontRearWashAPI` `NumiApi:35` / `BladeApi:40` | `:2771` / `:6561` | Wash defaults |
| S2 | `PATCH …-setting/{deviceid}/dryer` | `NumiDryerRequest`: `flow`, `temperature` | `callSetDryerSettingAPI` `NumiApi:44` / `BladeApi:43` | `:4209` / `:8470` | Dryer defaults |
| S3 | `PATCH …-setting/{deviceid}/seat` | `NumiSeatRequest`: `temperature` | `callSetSeatSettingAPI` `NumiApi:56` / `BladeApi:46` | `:3730` / `:8909` | Seat defaults |

Constants for the routes: `ApiConstant.java:182-230` and `:305-307`. Some Numi
constants lack the leading slash (`OPERATE_FLUSH_API_V` `:191`,
`POWER_SAVE_ENABLE_DISABLE_API_V` `:230`, `NUMI_RTC_TIME_ZONE` `:178`). Retrofit
resolves them against the base URL `https://api-kohler-us.kohler.io/`
(`na0/b.java:147`), so they are the same absolute paths.

Body and response conventions:

- **Serialization is plain Gson** (`GsonConverterFactory.create()`,
  `na0/b.java:147`). Nulls are left out and empty strings are sent.
  `NumiControlSprayDryerRequest` and the start/stop and flush requests start every
  field at `""` (`numi/model/NumiControlSprayDryerRequest.java:13-31`), so C2
  always carries all seven keys, with the unused ones empty.
  `NumiFrontRearWashPlatformRequest` starts at `FlowMid`, `HeatMid`, `PositionMid`
  and `ModeNormal` (`:23-32`).
- `tenantId` comes from the stored account record in most builders
  (`DeviceDataResponse.getTenantId()`, e.g. `es0/c0.java:550`). Power save, Numi
  write-configuration and factory reset take it from other user-record getters,
  presumably the same tenant (**inferred**).
- **Response:** `NumiCommonSuccessResponse` and `BladeCommonSuccessResponse` are
  empty classes. The app accepts any 2xx with a parseable body and treats an empty
  body as an error (`ja0/b.java:228-231`, "Response body is null"). The body is
  probably the usual `{correlationId, timestamp}` (**inferred**). As on every
  product, 2xx means "accepted", not "done" (platform §2.1).

### 3.2 Spray and dryer: start and stop (C1)

One button toggles. While something runs it sends `SprayAndDryerStop`; otherwise it
starts the open tab (`es0/c0.java:643` `M()`; `nr0/d0.java:730` `S()`):

| Tab | `operation` | Numi | Purewash |
|---|---|---|---|
| Front | `FrontSprayStart` | `es0/c0.java:658` | `nr0/d0.java:765` |
| Rear | `RearSprayStart` | `:664` | `:747` |
| Dryer | `DryerStart` | `:661` | `:759` |
| Boost | `DefecationSprayStart` | | `:753` |
| Kid | `KidsSprayStart` | | `:741` |
| Anything running | `SprayAndDryerStop` | `:649` | `:733` |

The enum also has `BoostSprayStart`; nothing sends it. The UI shows the button only
to a seated user (§7.2).

**The start sequence.** After a successful start, the app compares the dial with the
last known running parameters for that tab (`P()`, `es0/c0.java:689`; `V()` on the
Purewash). If they differ, it then sends C2 for the tab, and it marks the tab
running locally (`es0/c0.java:236-239`, `V0(true)` at `:947`; `nr0/d0.java:230-233`,
`f1()` and `e1(true)`). When idle, the dial holds the stored defaults (§2.4). So a
start from the app amounts to "start, then apply my default parameters". Whether
the device applies its stored defaults by itself is a **live check**. After any
start/stop reply, the Numi screen re-reads R1 (`NumiControlActivity.java:734-737`).
A "Starting your Numi." or "Stopping your Numi." banner shows for 12 s
(`es0/c0.java:499-502`; `CountDownTimer(12000L, 1000L)` at `:1203`, and
`nr0/d0.java:1606` for the Purewash).

### 3.3 Live wash and dryer parameters (C2)

The path segment is the tab (`es0/c0.java:340-345`; `nr0/d0.java:363-383`):

| Tab | `{sprayType}` |
|---|---|
| Front | `front-wash` |
| Rear | `rear-wash` |
| Dryer (and anything else) | `dryer` |
| Boost (Purewash) | `defecation-control` |
| Kid (Purewash) | `kids-control` |

Fields (`es0/c0.java:991` `W0()`; `nr0/d0.java:601` `f1()`):

| Field | Washes | Dryer, Numi | Dryer, Purewash |
|---|---|---|---|
| `temperature` | Temp dial, 0–5 (`HeatOff`…`HeatHigh`) | Temp dial, 0–5 | Temp dial, 0–5 |
| `flow` | Pressure dial, 1–5 (`FlowLow`…`FlowHigh`) | Fan-speed dial, 1–5 | **not sent** (stays `""`) |
| `position` | Position dial, 1–5 | `""` | `""` |
| `mode` | Massage dial, 1–4 (`ModeNormal`…`ModeWave`) | `""` | `""` |
| `deviceId`, `tenantId`, `sku` | from the device | | |

The dial ranges are `r4(0, 5)` for temperature and `r4(1, 5)` or `r4(1, 4)` for the
rest (`es0/y.java`; `I4(…)` in `nr0/z.java`). While something runs, every dial
change is sent at once (`es0/y.java:570` `H3()`). Confirmation comes back as MQTT
`NUMI_SET_*_PARAMETERS_STS` (§4.2).

### 3.4 Seat heat

- **Quick control** (the Seat Temp sheet): C2 with `{sprayType}` = `heated-seat`
  and only `temperature` set (`js0/l.java:143,257` `V()`; Purewash
  `qr0/j.java:143,333`). The sheet offers `HeatHigh` ("5 Highest"), `HeatMidHigh`,
  `HeatMid`, `HeatLowMid`, `HeatLow` ("1 Lowest") and `HeatOff` (bindings
  `ma0/h7.java:221-263` and `ma0/z6.java:201-243`). This is a device command only;
  the stored default is not touched.
- **Stored default:** C5 then S3 (§3.5).
- **Confirmation:** MQTT `NUMI_SET_RING_HEATER_PARAMETERS_STS`, and
  `state.seat.temperature`.

### 3.5 Default settings (C3–C5, then S1–S3)

Every Save sends the device command first and then, **2000 ms after it succeeds**,
the settings PATCH (`qs0/m.java:296-301` and `:385-390`; `os0/o.java:303-308` and
`:386-391`; `ts0/s.java:289-294` and `:564-569`).

| Screen | Device command | Then PATCH | Builder |
|---|---|---|---|
| Rear Wash, Front Wash (Numi) | C3 `rear-wash` / `front-wash` | S1 `rearwash` / `frontwash` | `qs0/m.java:955` `n0()`, then `:971` `o0()` |
| Rear, Front, Boost, Kid Wash (Purewash) | C3 `rear-wash` / `front-wash` / `defecation-control` / `kids-control` | S1 `rearwash` / `frontwash` / `boostwash` / `kidwash` | `qs0/m.java:918` `m0()`, then `o0()` |
| Dryer | C4 with `temperature` and `flow`; `position` and `mode` keep the model defaults `PositionMid` and `ModeNormal` | S2 `{flow, temperature}` | `os0/o.java:788` `i0()` (Numi), `:777` `h0()` (Purewash); then `:800` `j0()` |
| Seat | C5 `{deviceId, sku, temperature, tenantId}` | S3 `{temperature}` | `ts0/s.java:818` `k0()` (Numi), `:796` `i0()` (Purewash); then `:806` `j0()` |

Slider ranges: wash temperature 0–5 (`qs0/i.java:309-315`), position 1–5
(`:518-524`) and pressure 1–5 (`:542-548`), with four pattern choices; dryer
temperature 0–5 (`os0/j.java:481-487`) and fan speed 1–5 (`:598-604`, shown for
both SKUs); seat 1–5 (`ts0/n.java:640-646`) plus an on/off switch, where off is
`HeatOff` (`ts0/s.java:765` `e0()`).

**The device commands share URLs with the live writes.** C3 and C2 both POST to,
for example, `/commands/numi/rear-wash` with the same keys. C4 and the live dryer
write share `/commands/numi/dryer`. C5 and the quick seat heat share
`/commands/numi/heated-seat`. The device cannot tell them apart, so saving a
default presumably also applies it as the current setting (**inferred**,
**live check**). Note the spelling split: the device command says `mode`, the
settings PATCH says `massage`.

### 3.6 Flush (C6)

`flushType` is `FullFlushStart` (Full Flush) or `EcoFlushStart` (Eco Flush)
(`fs0/z.java:695,701`, sender `R()` at `:594`). The Purewash view model has the
same calls (`or0/o.java:353,358`, `L()` at `:384`), but its sheet keeps both
buttons at `visibility="gone"` (`layout/dialog_bottomsheet_blade_control.xml:51,94`)
and no code shows them. **The app never flushes a Purewash.** The view model sends
a flush with no confirmation and no seated test; whether the sheet greys the
buttons out while someone is seated was not resolved (§7.2). Nothing reads
`state.flush.state`.

### 3.7 Seat ring and cover (C7)

"Cover" sends `lid` (`fs0/z.java:689`; `or0/o.java:348`); "Seat" sends `ring`
(`fs0/z.java:734`; `or0/o.java:370`). The body has no direction, so each is a
toggle (**inferred**). The app shows only an animation on success. No lid or ring
position is read anywhere; one was not found in the decompile.

### 3.8 Cleaning: UV clean and wand (C8)

| Action | Verb | `operation` | Senders | App notes |
|---|---|---|---|---|
| Start Clean (UV sanitize) | `sanitize` | `SanitizeStart` | `ds0/k.java:212` (Numi), `mr0/g.java:198` (Purewash) | "Close the cover prior to starting the cleaning process"; the Purewash cycle "takes around 3 minutes" (strings.xml:1625-1626) |
| Stop Clean | `sanitize` | `SanitizeStop` | after "Are you sure you want to leave the cleaning process?" (`ds0/h.java:416` → `:117`; `mr0/d.java:477` → `:236`) | |
| Extend wand ("Extend to clean") | `clean-wand` | `CleanWandStart` | `hs0/k.java:219`; `pr0/g.java:181` | "Open cover and seat before extending wand … After 5 minutes the wand will automatically retract." (:2229-2230) |
| Retract wand | `clean-wand` | `CleanWandStop` | `hs0/k.java:217`; `pr0/g.java:179` | |

Progress shows in `cleanStatus.uvClean` and `cleanStatus.manualClean`, and in MQTT
`NUMI_SANITIZE_STS`. The `auto clean` setting is separate (§3.10).

### 3.9 Power save (C9)

`state` is `Enable` or `Disable` (`ks0.b.Enable`/`Disable`), the switch's new
position (`us0/n.java:744-750` `T()`; senders `e0()` at `:822` for the Numi and
`d0()` at `:812` for the Purewash). The setting reads back as `seat.powerSave`. The
app describes it as "Saves energy when toilet is not in use" (strings.xml:2509).
`EnterPowerSaveRequest` and the `enter-power-save`/`exit-power-save` constants
(`ApiConstant.java:107-108`) have no caller.

### 3.10 Auto settings: `write-configuration` (C10, C11)

The app always sends the **whole object**. It is seeded from the last state read,
and then the screen's switches overwrite their fields.

**Numi** (`numi/model/NumiWriteConfigRequest.java:15-42`). Seeding is at
`NumiSettingActivity.java:2070-2118`, sending at `ns0/y.java:293` `V()`:

| Field | Value | Screen row (seeded from) |
|---|---|---|
| `deviceId`, `sku`, `tenantId` | | `tenantId` from the user record (`NumiSettingActivity.java:2075`) |
| `nightLightEnable` | `"true"`/`"false"` | Nightlight (`light.nightLight`, `On` → `"true"`) |
| `autoFlushEnable` | `"true"`/`"false"` | Auto Flush ("flushes the toilet after the user stands up, lowers the lid, or approximately 60 seconds after…", strings.xml:3079) (`flush.auto`) |
| `autoLidEnable` | `"false"` only when the option is OFF (`ns0/y.java:236` `G()`) | Auto Cover Sensor Distance (`seat.autoOpen.state`) |
| `microWaveRange` | `RangeNear`/`RangeMid`/`RangeFar`; OFF sends `RangeMid` (`vs0/b.java` `i()`) | the same row (`seat.autoOpen.sensorDistance`) |
| `autoUVEnable` | `"true"`/`"false"` | Auto Clean (`clean.sanitizer`, `SanitizeOn` → `"true"`) |
| `autoDeodorizeEnable` | `"true"`/`"false"` | Deodorizer Fan, a hidden row (`clean.deo`, `AirCleanOn` → `"true"`) |
| `waterBowlPremistEnable` | `"true"`/`"false"` | Auto Bowl Mist, a hidden row (`clean.preRinse`) |

The hidden rows are `visibility="gone"` in `layout/fragment_setting_autoconfig.xml:191,245`.
The model's defaults are `"false"` and `RangeMid`.

**Purewash** (`blade/model/BladeWriteConfigRequest.java:15-54`). Seeded from R2 by
`bs0/b0.java:408` `h0()` and sent by `i0()` at `:474`:

| Field | Value | Screen row |
|---|---|---|
| `autoLidEnable` | `"true"`/`"false"` | Auto Cover ("will automatically open and close your seat cover", strings.xml:1483) |
| `autoOpen` | `"true"`/`"false"`, copied from `seat.autoOpen` and **not** updated by the switch (`h0()` `:459`) | none |
| `autoDeodorizeEnable` | `"true"`/`"false"` | Deodorizer Fan |
| `waterBowlPremistEnable` | `"true"`/`"false"` | Auto Bowl Mist |
| `longTimeSeatReminderEnable` | `"true"`/`"false"` | Long Sitting Reminder ("notified after 15 minutes of uninterrupted toilet use", strings.xml:2217) |
| `autoWaterTempEnable` | `"true"`/`"false"` | Auto Temperature ("Water & Seat temperature both will be automatically adjusted", strings.xml:129) |
| `autoSeatTemp` (no `Enable` suffix) | `"true"`/`"false"` | Auto Seat Temp, a hidden row |
| `autoUVEnable` | `"true"`/`"false"` | Auto Clean |
| `nightLightEnable`, `nightLightBrightness` | High → `"true"`, `"high"`; Low → `"true"`, `"low"`; Off → `"false"`, `"high"` | Auto Nightlight sheet (`bs0/e.java:62-78`). The brightness strings are **lower-case** resources (`text_night_light_high`/`_low`, strings.xml:2328-2329), while state reports `High`/`Low` |
| `autoFlushEnable` | copied from `flush.auto` | hidden row |

The hidden rows are in `layout/fragment_blade_setting_autoconfig.xml:29,85,142,249`
(night light switch, auto flush, sensor distance, auto seat temp).

### 3.11 Numi lighting (C12–C14)

- **On:** C12, then on success C13 with `operation` `MultimediaWakeUp`
  (`gs0/s.java:682` `H3()` → `gs0/v.java:913` `o0()` → `q0()` at `:945`).
- **Off:** C13 with `MultimediaSleep` (`gs0/s.java:698`).
- **Brightness or theme while on:** C12 (`gs0/v.java:890` `n0()`).
- **C12 body:** `color` is a theme code (below). `luminance` is the slider value
  (10–100 in steps of 10, `gs0/s.java:625-626` and step `:84`) with its last
  digit dropped, so `"1"` … `"10"` (`vs0/b.java` `l()`). `r`, `g` and `b` are
  always `"0"`, and `msp` is always `NormalSpeed`.
- **Theme codes** (`gs0/v.java:840-874` `j0()`): Sunrise `Sunrise`, Forest Breeze
  `ForestBreeze`, Ocean Deep `DeepOcean` (the default), Bokeh `Bokeh` (the
  fallback), Miami `MiamiClub`; Coral `Color1`, Red `Color2`, Magenta `Color3`,
  Blue `Color4`, Gold `Color5`, Yellow `Color6`, Green `Color7`. The first five are
  the "theme" list (`numi_light_theme`, arrays.xml:571-577) and the rest the
  solid colours (`numi_light_color`, :562-570).
- **Circadian ("natural rhythm"):** C14 with `operation` `Start` or `Stop`
  (`gs0/v.java:766` `e0()` → `p0()` at `:936`).
- **State:** `light.ambientLight.*` and `light.circadianLight`; MQTT
  `NUMI_SET_LIGHTING_PARAMETERS_STS`, `NUMI_MULTIMEDIA_STS`, `NUMI_CIRCADIAN_STS`.
  The ambient light evidently belongs to the multimedia module: "sleeping" means
  off (**inferred**).

### 3.12 Time zone (C15)

After R1, if the state, its `cleanStatus` or `cleanStatus.timezone` is null, the Numi
screen sends C15 with
`{deviceId, tenantId, sku: "NUM", timeZone: <the phone's TimeZone.getDefault().getID()>}`
(`cs0/l0.java:232-233` → `L()` at `:663`; body `H()` at `:621-630`). Setup also
sends it once, with the setup SKU (`sp0/y.java:4788` `t1()`, body `k1()`). The
Purewash control screens never send it. The time zone probably drives the
device's schedules, such as auto clean (**inferred**).

### 3.13 Not for an integration

- **Factory reset:** `commands/numi/factory-reset` or `commands/blade/factoryreset`
  via `DEVICE_FACTORY_RESET_API` (`ApiConstant.java:76`, `PlatformApiCall.java:158`;
  `us0/n.java:807-810` `c0()`, body `{deviceId, sku, tenantId}` from `J()` at
  `:644`). Destructive.
- **Alexa pairing:** `commands/numi/init-voice-module-pairing`
  (`ApiConstant.java:179`) with `NumiSecondApModeRequest` `{deviceId, tenantId, sku}`,
  sent during setup after the MQTT `ONBOARD_SUCCESS` (§4.2).
- **Music** is LinkPlay/WiiM on the LAN ("connected to the same network as Numi",
  `txt_numi_not_connected_with_same_wifi`, strings.xml:3242), not the cloud.
- **Constants with no caller:** `enter-power-save`, `exit-power-save`,
  `read-configuration`, `music`, `music-operation` (`ApiConstant.java:107-108,225-226,240`).
  The classes `NumiMusicSettingRequest`, `NumiMusicOperationRequest`,
  `EnterPowerSaveRequest` and `NumiLightResponseModel` are unreferenced.

---

## 4. MQTT

### 4.1 How messages reach the screens

The transport and envelope are as in platform §4. `qy0/a.java:68`
(`onMethodInvoked`) accepts only `ExecuteControlCommand`. It hands the payload to
`qy0/i.java:62` `b()` → `:93` `c()`, which calls `j1()` on whichever screen is
registered (`qy0/i.java:122`): `NumiControlActivity.java:1501`,
`BladeControlActivity.java:1191` or `NumiSettingActivity.java:1809`. Each drops
messages whose `deviceid` is not this device (case-sensitive) and dispatches on
`data.code`. **Only `JSONException` is caught** (`qy0/i.java:69`).

- The Gson-based handlers act only when `data.attributes` has exactly one entry.
  The lighting, multimedia and circadian handlers read the first entry with
  `org.json` instead.
- On any message for the device, the Numi screen sets its cached
  `connectionState` to `Connected` (`NumiControlActivity.java:1519`). The Purewash
  screen does not.
- The dashboard does not handle these codes; its MQTT code has no `NUMI_` case.
- The Purewash uses the same `NUMI_` codes, with envelope `sku` `BLD`.

### 4.2 Codes

Attribute names come from the models in `ui-model/mqtt/`. All values are strings.

| `data.code` | Attributes | Numi screen | Purewash screen | Settings screen | Effect |
|---|---|---|---|---|---|
| `NUMI_SPRAY_STS` | `status` (`MQTTNumiSprayDryerStatusModel.java:65-68`) | `i5()` `:1435` | `y4()` `:1670` | | `SprayingFront`, `SprayingRear`, `SprayingOff`; Purewash also `SprayingDefecation` (boost) and `SprayingKids` (kid). Sets that wash's `state` `On` and every other wash and the dryer `Off`; `SprayingOff` sets them all `Off` |
| `NUMI_DRYER_STS` | `status` | `h5()` `:1382` | `x4()` `:1627` | | `DryingOn`: dryer `On`, washes `Off`. `DryingOff`: all `Off` |
| `NUMI_SET_FRONT_PARAMETERS_STS`, `NUMI_SET_REAR_PARAMETERS_STS` | `flow`, `temperature`, `position`, `mode` (`MQTTNumiSprayDryerModel.java:65-77`) | `l5()` `:1690`, `m5()` `:1742` | `B4()` `:803`, `D4()` `:887` | | Copied into the wash; `mode` becomes `massage` |
| `NUMI_SET_DEFECATION_PARAMETERS_STS`, `NUMI_SET_KIDS_PARAMETERS_STS` | same | | `w4()` `:1585`, `C4()` `:845` | | `boostWash`, `kidWash` |
| `NUMI_SET_DRYER_PARAMETERS_STS` | `temperature`, `flow` | `k5()` `:1661` | `A4()` `:780` | | `dryer` |
| `NUMI_SET_RING_HEATER_PARAMETERS_STS` | `temperature` | `n5()` `:1789` | `E4()` `:929` | `s5()` `:1174` (bug, §4.3) | `seat.temperature` |
| `NUMI_USER_STS` | `status` | `o5()` `:1812` | `F4()` `:951` | | `seat.userStatus` (`UserSeated`, `UserLeave`). When a Purewash user sits down, the Clean and Manual Clean sheets close (`BladeControlActivity.java:1005-1018` `R3()`) |
| `NUMI_SANITIZE_STS` | `status`; also `hours`, `minutes`, `seconds`, never read (`MQTTNumiManualWandCleanModelModel.java:65-77`) | `j5()` `:1614` | `z4()` `:1694` | | `WandCleanOn`: `manualClean` `On`, `uvClean` `SanitizeOff`. `SanitizeOn`/`SanitizeOff`: `uvClean` = status, `manualClean` `Off` |
| `NUMI_DEVICE_STS` | `autopowersave`, `code`, `deviceerrorstatus` (never read), `devicestate`, `status` (`MqttNumiDeviceStatusDataModel.java:65-77`) | not handled | `G4()` `:982`: power save shown as on when `devicestate` is `DeviceLowPower` or `DeviceSleep` (`vs0/a.java` `f()`) | `y5()` `:1238`: on when `autopowersave` is `"1"` (`vs0/b.java` `n()`) | Power-save indicator |
| `NUMI_AUTO_POWER_SAVE_ENABLE_STS` | `autopowersave` | `e5()` `:870` (`n()`) | | `y5()` | Power-save switch; `"1"` = on |
| `NUMI_CONFIG_STS` | `autolidenable`, `autoflushenable`, `autodeodorizeenable`, `nightlightenable`, `autouvenable`, `waterbowlpremistenable` (`"true"`/`"false"`), and a sensor distance the app means to read as `microwaverange` (`MQTTNumiConfigModel.java:66-87`) | `g5()` `:1300` | not handled | `z5()` `:1254` (Numi), `x5()` `:2211` (Purewash) | Intended mapping: `light.nightLight`, `flush.auto`, `seat.autoOpen`(`.state`), `seat.autoOpen.sensorDistance`, `clean.deo` (`AirClean*`), `clean.preRinse`, `clean.sanitizer` (`Sanitize*`). **The app cannot parse it** (§4.3) |
| `NUMI_ERROR_STS` | control screens read `status`; settings reads `code` and `errorcode` (`MQTTNumiErrorModel.java:65-68`) | `Y4()` `:795` | `n4()` `:1318` | `s5()` (bug) | One second later, a dialog shows the `status` text itself (`zy0/j.java:1028-1034` `n3()`). The Purewash skips `NoError`; the Numi does not |
| `NUMI_SET_LIGHTING_PARAMETERS_STS` | `color`, `luminance` | `q5()` `:1942` | | | Lighting sheet |
| `NUMI_MULTIMEDIA_STS` | `multimediastatus` | `U4()` `:1174` | | | `MultimediaIsSleeping` → ambient light `Off`; anything else → `On` |
| `NUMI_CIRCADIAN_STS` | `startstop` | `f5()` `:1289` | | | `Start` → circadian on; anything else → off |
| `NUMI_REBOOT_STS` | none read | | `H4()` `:991` | | Marks the product **offline** (`lr0/s.java:305` `e0(true)` → `or0/j0.java:848`) until the next state read |

Dispatch tables: `NumiControlActivity.java:1525-1603`,
`BladeControlActivity.java:1219-1290`, `NumiSettingActivity.java:1836-1870`.

Setup only: an envelope with `type` `SPL` and `data.code` `ONBOARD_SUCCESS` for a
`NUM` starts the Alexa pairing step (`WifiProvisionActivity.java:1941`), and
`INSTALL_FIRMWARE_STS` reports a setup-time update (`:1949`). No Numi or Purewash
screen handles `INSTALL_FIRMWARE_STS` after setup.

### 4.3 App bugs worth knowing

1. **`NUMI_CONFIG_STS` cannot be parsed.** `MQTTNumiConfigModel.Attribute` maps two
   fields to `"autoflushenable"`: `autoflushenable` (`:69-70`) and
   `microwaverange` (`:81-82`). Gson resolves nested adapters eagerly and throws
   `IllegalArgumentException` ("declares multiple JSON fields named…") on a
   duplicate name (`com/google/gson/internal/bind/ReflectiveTypeAdapterFactory.java:251,315`).
   So every `fromJson(…, MQTTNumiConfigModel.class)` throws, before any field is
   read. Only `JSONException` is caught, so nothing updates. In practice the app
   learns auto settings only from REST reads. An integration should parse the
   message itself, reading the distance from `microwaverange` (**inferred**: that
   is the getter's name and the field's evident intent).
2. **Settings `NUMI_ERROR_STS` and `NUMI_SET_RING_HEATER_PARAMETERS_STS` throw.**
   `NumiSettingActivity.java:1174` `s5()` returns unless there is exactly one
   attribute, then reads `attributes.get(1)`, an `IndexOutOfBoundsException`.
3. **Power save means two things.** The Purewash control screen derives it from
   `devicestate` (the seat is in low power *now*), but settings derives it from
   `autopowersave` (the feature is *enabled*). Treat them as separate signals.
4. **The Numi shows `NoError` as an error** if the seat ever sends it, because only
   the Purewash handler filters it (`BladeControlActivity.java:1325`).

---

## 5. Status codes and refusals

The general table is in platform §2.1. For these products:

- **Path.** Every Numi and Purewash call goes through `la0/b.java` `j3()`/`k3()`
  into `ja0/b.java:188` `e()`. A 2xx is success. On **HTTP 400**, the body is parsed
  as `ErrorResponseModel`, whose `messageCode` is `@SerializedName("statusCode")`
  (`com/utils/network/utils/models/ErrorResponseModel.java:24`). The code is
  mapped to text by `oy0/a.java` `a()` (`ja0/b.java:166-183,232-235`). Any other
  status shows the HTTP reason. The view models show the text in a dialog (for
  example `es0/c0.java` failure → `es0/y.java` `V2()` → shared view model `z0()`).
- **913 and 914 are the toilet's codes:**

  | Code | Text | Where |
  |---|---|---|
  | **913** | "Please be seated to perform this action." | `error_msg_913` (strings.xml:376); class `oy0.b.n` (`oy0/b.java:167`); mapped at `oy0/a.java:68-71` |
  | **914** | "Please ensure no one is seated while performing this action." | `error_msg_914` (:377); class `oy0.b.o` (`oy0/b.java:178`); mapped at `oy0/a.java:72-75` |

  No Numi or Purewash code branches on either (their only references are in
  `oy0/a.java`). They are translated like every other code, which refines
  platform §2.1's "no code path references them". **Inferred:** 913 refuses a spray
  or dryer start when nobody is seated, and 914 refuses something that needs an
  empty seat, such as a clean cycle, the wand or the cover. The app's own
  interlocks (§7.2) hide most of these cases, which is why the codes rarely matter
  to it. The response shape is a **live check**: HTTP 400 with
  `{"statusCode": "913", "message": …}` is expected.
- **Other codes** that apply: 900 offline, 903 firmware update in progress, 904,
  909, 911, 918 product error, 917 (the server's message).
- **Device errors** arrive by MQTT `NUMI_ERROR_STS` (§4.2) as text, with no code
  table in the app.

---

## 6. Firmware and usage

### 6.1 Firmware

| Step | Request | Evidence |
|---|---|---|
| Check | `GET /platform/api/v1/firmware/numi/{deviceid}` or `…/blade/{deviceid}`, **without** `releasetarget`. Settings uses `getNumiFirmwareUpdateState`/`getBladeFirmwareUpdateState` (`PlatformApiCall.java:290,275`) → `FirmwareUpdateResponseModel` `{currentFirmware, estimatedTimeForOTA, fileSizeInMb, firmware, firmwareUpdateAvailable, mandatoryUpdate, otaStatus, skip, url}`. The shared OTA uses `getFirmwareState` with type `numi` or `blade` (`I1()`, `ui/ota/f.java:175,150`) | `ps0/j.java:738` `T()`, `:746` `U()` |
| Install | `POST` the same path with `NumiFirmwareUpdateRequest` `{firmwareNumber, releaseTarget: "Public", tenantId}` (`numi/model/NumiFirmwareUpdateRequest.java:13-19`) | `ps0/j.java:676` `O()`, `:850` `s0()`; `PlatformApiCall.java:341,338`; shared OTA `ui/ota/f.java:297,277` |
| Done | Poll the configuration every 10 s for up to 120 s until `configuration.about.firmware.version` equals the target | `ps0/j.java:416` (`CountDownTimer(120000L, BLEProfile.PERIOD_TIME)`), `:344`; shared OTA `ui/ota/f.java:456-470,547-564`, `ui/ota/c.java:1798` |
| Pre-checks | Refused while any wash or the dryer is `On`, or while `connectionState` is `Disconnected` | `ui/ota/f.java:736` `r()` (Numi), `:677` `n()` (Purewash, including boost and kid), `:519`/`:489` |

The skip call is the shared `PATCH firmware/skip/{id}` (platform §7). The firmware
types match platform §7 (`numi`, `blade`).

### 6.2 Usage (water used)

R7 and R8 take the shared query parameters (platform §6) and return
`WaterUsageModel` (`com/utils/network/retrofit/proxy/device/model/WaterUsageModel.java`).
The screen that shows a toilet's water, `uq0/z.java` `L0()`, routes `NUM` to R7 and `BLD`
to R8 (`:727-731`) with dates year first: `by0/a.java` `n("yyyy-MM-dd", …)` writes the
day unpadded (`2026-10-5`), the window a Sunday-to-Saturday week or a calendar month by
`Day`, or a year by `Month`, and no `TimeZone`. The shared Water Usage screen
(`rg0/t0.java`) sends `MM-dd-yyyy` for every SKU but can't show a toilet's reply: its
converter has no `NUM` or `BLD` branch ("SKU … not handled", `:1615`).

- **Buckets:** `numiUsageDataDetailsList[]` (`:165`), each
  `{dryerCount, ecoFlushCount, fullFlushCount, intervalKey, lidAbnormalCount, lidMotionCount, powerConsumption, quantity, ringAbnormalCount, ringMotionCount, sprayFrontCount, sprayRearCount, usageDuration, waterUsage}`.
  `intervalKey` is a String; everything else is Double (`:179-218`).
- **Summary:** `avg`/`min`/`max` × `WaterUsage`, `UsageDuration`, `Quantity`,
  `DryerCount`, `EcoFlushCount`, `FullFlushCount`, `LidAbnormalCount`,
  `LidMotionCount`, `PowerConsumption`, `RingAbnormalCount`, `RingMotionCount`,
  `SprayFrontCount`, `SprayRearCount` (and other products' counters).
- **What the app uses:** `avgWaterUsage` and `maxWaterUsage` for both SKUs
  (`uq0/z.java:1004` `N0()`). For the Numi only, it charts
  `numiUsageDataDetailsList[].waterUsage` by `intervalKey` (`uq0/z.java:1529-1553`
  `c0()`). Values are litres, ×0.264172 for a `Standard` account. No counter is
  shown anywhere. Whether a Purewash reply fills `numiUsageDataDetailsList` is a
  **live check**.
- **Entry points:** the settings Water Usage row opens only for `BLD`
  (`us0/j.java:684-693`), although the usage code serves both
  (`rg0/t0.java:247,297`; `uq0/z.java:727-731`).

---

## 7. The app's own rules

### 7.1 Online

- Controls are enabled only when `connectionState` is exactly `"Connected"`
  (`es0/y.java:2266`, `fs0/u0.java:1075`; Purewash `or0/j0.java:125`,
  `nr0/z.java:1137`). `"Disconnected"` (ignoring case) shows the offline state
  (`es0/y.java:3142` `M4()`). This is stricter than platform §5's "online unless
  `Disconnected`".
- The Numi screen also counts any MQTT message as proof of being connected (§4.1).
- The Purewash `NUMI_REBOOT_STS` marks it offline until the next read (§4.2).

### 7.2 Seated

- **The dial's single Start/Stop button is hidden unless `seat.userStatus` is
  `UserSeated`.** In its place the dial shows "Please be seated to use this
  function" (`text_user_must_seated`, strings.xml:2902). Numi: `es0/c0.java:450-462`
  `O()` and binding `ma0/ui.java:579-587`. Purewash: `nr0/d0.java:786-797` `U()`
  and `ma0/be.java:583-592`. Layout: `layout/fragment_numi_dialview.xml:303-317`.
  Because the same button stops, **the app cannot stop a spray for someone who is
  not seated** (**inferred**: the device stops by itself when the user stands).
- The see-more sheet warns "Some Numi 2.0 functions are not available while you
  are seated" (strings.xml:2344; Purewash :1520). It disables controls while seated
  and disables seat, cover and clean while a spray or the dryer runs (log label
  `SeatCoverCleanDisableCalled - from setDrawerFeaturesDisableWhenSprayDryerRunning`,
  `fs0/v.java:1011`, `or0/l.java:1029`). The exact per-button map lives in data
  binding (`ma0/f7.java`, `ma0/x6.java`) and was not resolved.
- Clean needs an empty seat: the Numi clean sheet disables Start while seated
  (`ds0/h.java:250`), and the Purewash closes the Clean and Manual Clean sheets
  when someone sits (`BladeControlActivity.java:1005-1018`).

### 7.3 Confirmations and instructions

- **Stopping a clean** asks "Are you sure you want to leave the cleaning process?"
  (strings.xml:2174; `ds0/h.java:416`, `mr0/d.java:477`).
- **Leaving a settings screen** with unsaved changes asks first
  (`text_leave_screen_message`; `os0/j.java:679`, `qs0/i.java:648`, `ts0/n.java:692`).
- **No confirmation** for flush, cover, seat ring, seat heat, power save, or spray
  and dryer start or stop.
- **Instructions:** "Close the cover prior to starting the cleaning process. Some
  features will be disabled while Clean is running." (:1625-1626); "Open cover and
  seat before extending wand" and the 5-minute auto-retract (:2229-2230).

### 7.4 Timing

- After a command, the screens wait for an MQTT reply with the shared timer (5 s
  ticks, 2 retries, platform §4.2), or 15 s while preparing the wand
  (`NumiControlActivity.java:1268-1279` `c5()`). When it expires, the Numi screen
  re-reads R1 (`:191-200`).
- The "Starting/Stopping your Numi." banner lasts 12 s (§3.2).
- Defaults: device command, then the settings PATCH 2 s later (§3.5).

### 7.5 Other

- Power save is not an interlock: no code disables controls because of it.
- Firmware installs are refused while running or offline (§6.1).
- The Numi time zone is pushed when the state lacks one (§3.12).

---

## 8. What each product's screens show

**Numi 2.0.** `NumiControlActivity`:

- **Home** (`fs0/u0.java`, `layout/fragment_numi_control_home.xml`) has Rear,
  Front and Dryer. Each opens the dial (`es0/y.java`): Temp, Pressure, Position and
  Massage tabs (Dryer: Temp and Fan speed), Start/Stop, and status lines
  ("Preparing the wand", "Retracting the wand", "Clean is running",
  strings.xml:3290,3302,1629).
- **"See more"** (`fs0/v.java`, `layout/dialog_bottomsheet_numi_control.xml`): Full
  Flush, Eco Flush, Seat Temp, Lighting, Seat, Cover, Music, Clean and Manual
  Clean.
- **Settings** (`NumiSettingActivity` → `us0/j.java`,
  `layout/fragment_numi_setting_home.xml`):
  - Default Settings: Rear Wash, Front Wash, Dryer, Seat.
  - Device Control Automation: Auto Config, Power Save.
  - General: About, Water Usage, Amazon Alexa, Mute Microphone, User Manual,
    Product Information, Remove Product.

**Purewash E930.** `BladeControlActivity`:

- **Home** (`or0/j0.java`, `layout/fragment_blade_control_home.xml:52-165`) has
  Rear, Front, Boost, Kid and Dryer, each with the same dial (`nr0/z.java`).
- **"See more"** (`or0/l.java`) has Seat Temp, Cover, Seat, Clean and Manual Clean.
  Flush is hidden (§3.6). The layout also lists Lighting and Music, but no click
  handler exists for them (`ma0/x6.java` click map; **inferred**: inert).
- **Settings** is the same screen with Boost and Kid defaults shown, Amazon Alexa
  and Mute Microphone hidden, and User Manual shown (`us0/j.java:816-873`; flag
  names from the view model's metadata: `isBoostAndKidVisible`,
  `isAmazonAlexaVisible`, `isMuteMicrophoneVisible`, `isUserMenualVisible`).
- **About** names the model "Purewash" or "Numi 2.0" (`ep0/i0.java:1088-1128`
  `X0()`).

---

## 9. What `kohler_konnect` does with it

One product, "toilet", with the path segment and the feature set taken from the SKU (§1.3).
Readings and controls are on, the controls flagged `untested`
(`product_base.UntestedControl`). Code: `konnect/toilet.py` (protocol) and `toilet/`
(entities).

**Reads.** R1 or R2 at setup, after every MQTT connect and every five minutes, every
fifteen once the stream has carried a message about the toilet; the firmware check (§6.1)
twice a day. Every §4.2 code is applied to the state as the app applies it,
`NUMI_CONFIG_STS` included, parsed here with its distance from `microwaverange` (§4.3).
`NUMI_ERROR_STS` keeps the last error, cleared by `NoError` or `DeviceNoError` from either
model. Any other code reads the state again.

| Entity | Kind | Source or command | Model |
|---|---|---|---|
| Occupied | binary sensor (occupancy) | `seat.userStatus` | both |
| Activity | sensor (enum: idle, each wash, drying) | wash and dryer `state` | both; boost and kid on the Purewash |
| Cleaning, Wand extended | binary sensors | `cleanStatus.uvClean`, `cleanStatus.manualClean` | both |
| Seat heat | sensor (enum: the app's six levels) | `seat.temperature` | both |
| Last error | sensor (diagnostic) | `NUMI_ERROR_STS` | both |
| Cloud connection | binary sensor | `connectionState`; only `Disconnected` is off | both |
| Low power | binary sensor (diagnostic) | `NUMI_DEVICE_STS` `devicestate` (§4.3 item 3) | both |
| Firmware status | update | §6.1 check; installing stays with the app | both |
| Start each wash, Start dryer, Stop | buttons, **untested** | C1. Starts are refused unless `UserSeated` (913's rule) and during the UV clean, when the app locks its Start (`es0/y.java` `h4()`, `nr0/z.java` `y4()`); Stop is always sent. Once a start succeeds, a part whose live settings differ from its stored defaults is sent them with C2, as the app's dial does (`es0/c0.java` → `W0()`, `nr0/d0.java` → `f1()`) | both |
| Full flush, Eco flush | buttons, **untested** | C6, with no seated test, as in the app | Numi |
| Cover, Seat ring | buttons, **untested** | C7 `lid`, `ring` (toggles); refused while seated | both |
| Start clean, Stop clean, Extend wand, Retract wand | buttons, **untested** | C8; starting and extending are refused while seated | both |
| Seat heat level | select, **untested** | C2 `heated-seat`, only `temperature` set | both |
| Power save | switch, **untested** | C9 | both |
| Night light, Auto flush, Auto clean; Auto cover (off, near, medium, far) | switches and a select, **untested** | C10, the whole object | Numi |
| Auto cover, Deodorizer fan, Bowl mist, Long sitting reminder, Auto temperature, Auto clean; Night light (off, low, high) | switches and a select, **untested** | C11, the whole object | Purewash |
| Ambient light | light, **untested**: on/off, brightness, the twelve themes as effects | C12 then C13 `MultimediaWakeUp`; off is C13 `MultimediaSleep` | Numi |
| Circadian light | switch, **untested** | C14 | Numi |
| Each wash's temperature (0–5), pressure (1–5) and position (1–5); its spray pattern | numbers and a select, **untested** | While that wash runs: C2 with its live settings, the default untouched (not during the UV clean). Otherwise the default: C3 with all four from a fresh R3/R4, a setting never stored taken as the dial's middle as the app fills it (`HeatMid`, `FlowMid`, `PositionMid`, `ModeNormal`), then S1 two seconds later | both; boost and kid on the Purewash |
| Dryer temperature (0–5), fan speed (1–5) | numbers, **untested** | As the washes: C2 while it runs, else C4 then S2. The Purewash's fan speed is a default only, so it waits until the dryer stops | both |
| Default seat heat (0–5) | number, **untested** | C5, then S3 | both |
| Water used, Duration used: today, this week, this month, this year | sensors | R7 or R8, read as a faucet's usage (platform §6) but with the toilet screen's dates, year first, and its windows: this year's months, and a month of days reaching back at least a week. Litres from `waterUsage` alone; `usageDuration` is taken as the faucet's seconds, which no toilet screen shows to confirm | both |
| Flushes, Washes, Dryer runs, Power used: today | sensors | Today's bucket of R7 or R8 (§6.2), matched on its key's date. Flushes and washes carry their parts (`full`, `eco`; `front`, `rear`). Power's unit is unknown, so it shows the bare number. Buckets dated in UTC put a US evening under tomorrow; these counts don't borrow it, as the water totals do, so such an evening counts on the next day | both |

Controls are available only while `connectionState` is exactly `Connected`, the app's own
test (§7.1). Every configuration write reads the state again first and sends every field,
and is refused, with nothing sent, when the read brings no state: written from nothing,
it would turn every other setting off. Settings changes go one at a time, each from its
fresh read through its last write, and a value just written is laid over the reads until
they show it, so a second change made meanwhile doesn't write the first one back. A
Purewash whose state gives no night-light brightness gets the app's `low`. A Numi that
reports no time zone, or no `cleanStatus`, is sent Home Assistant's once, as the app
sends the phone's (C15, §3.12). A spray, dryer or sanitize status the app doesn't act on
reads the state again rather than being guessed at. Refusals 913 and 914 read as the
app's "be seated" and "nobody seated" messages. After a command nothing is read at once:
the seat is read again only if no message about it comes within 8 s.

Left out: factory reset, Alexa pairing and music (§3.13).

## 10. Open questions (live check)

1. **SKUs:** confirm `NUM` and `BLD` in a real `customer-device` listing, and check
   for other Numi variants.
2. **Refusals:** the shape of the 913 and 914 replies (HTTP 400 with
   `statusCode`?), and which actions draw 914.
3. **Start semantics:** does C1 alone use the stored defaults, or the last
   parameters? Is the follow-up C2 needed?
4. **Defaults:** does a defaults command (C3–C5) also change the live setting,
   given that it shares a URL with C2?
5. **Purewash dryer fan speed:** the live write never sends `flow`. Is there a fan
   speed at all?
6. **Cover and ring:** toggle or open-only? Is any position reported (`seat.state`?
   `flush.state`?)
7. **`NUMI_CONFIG_STS`:** the real attribute for the sensor distance
   (`microwaverange`?) and full payloads of every `NUMI_*` code, particularly
   `NUMI_ERROR_STS` (`status` text versus `errorcode`).
8. **`devicestate` values** and how `NUMI_DEVICE_STS` relates to
   `NUMI_AUTO_POWER_SAVE_ENABLE_STS`.
9. **Purewash configuration:** does `numi-configuration` or `blade-configuration`
   answer for a `BLD` (§2.5)?
10. **Purewash write-configuration:** is `nightLightBrightness` case-sensitive
    (`"high"` sent, `High` reported), and which of `autoOpen` and `autoLidEnable`
    does the seat honour?
11. **Timestamps:** are `lastConnected`, `createdTimestamp` and `updatedTimestamp`
    seconds?
12. **Time zone:** what `cleanStatus.timezone` holds, and what C15 changes.
13. **Usage:** the units of `powerConsumption` and `usageDuration`, whether the
    counters are populated, and the Purewash bucket list's key.
14. **Auth:** do `B2C_1A_signin` tokens work for `numi`/`blade` commands? Platform
    §1 found the restriction is per product.
15. **Wand and clean:** the MQTT sequence for the wand (is there a `WandCleanOff`?)
    and what `hours`/`minutes`/`seconds` in `NUMI_SANITIZE_STS` count.
