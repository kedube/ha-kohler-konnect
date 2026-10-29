# Kohler EvoCycle: cloud protocol (app-side)

**Source:** Kohler Konnect for Android **3.0.6** (version code 260), decompiled
with jadx 1.5.6. Covers SKU `EVO`, the EvoCycle recirculating shower: a digital
valve, a basin pump loop with filter, an ozone generator for cleaning, and
digital interface panels.

**Caveat.** Everything here comes from the app's side: what the 3.0.6 app sends,
what it expects back, and how it reacts. It does not describe what the shower or
the Kohler cloud actually does. Nobody on this project owns an EvoCycle, so
**nothing in this chapter is live-verified**; every fact is **app** or
**inferred** in the sense of the [README](README.md#confidence), and unmarked
statements are **app**. Field names and request shapes are exact, because they
come from Gson `@SerializedName` models and Retrofit annotations. Units, ranges
and meanings come from app logic and UI code, and the server may accept more or
less than the app sends. Items marked **live check** need a real EvoCycle. "Not
found in the decompile" means just that; the behaviour may still exist
server-side. Two of the writes run water and one runs ozonated water through the
shower, so treat them with the same caution as the Anthem valve's.

Shared mechanics (auth, API base and APIM key, `customer-device`, mobile
registration, the IoT Hub MQTT transport, the response-body `statusCode` table,
and the general firmware, usage and diagnostics patterns) are in
[platform.md](platform.md). This file covers what is EvoCycle-specific, and flags
where EvoCycle departs from those shared patterns.

**Finding things in the decompile.** Code paths are relative to jadx's `sources/`
root; `strings.xml:N` is `resources/res/values/strings.xml`. Short package names
(`uf0/`, `la0/` …) are obfuscated and change between builds, so every citation
also gives a string, class, field or method name you can grep for.

| Where | What |
|---|---|
| `com/kohler/hermoth/products/evocycle/data/model/`, `…/data/dto/` | Response models and request bodies. `EvoCycleFactoryResetRequest` and `EvoCycleRemoveProductRequest` sit in `…/presentation/settings/`. |
| `com/kohler/hermoth/products/evocycle/data/services/EvoCycleApiServices.java` | **Every EvoCycle endpoint.** `ApiConstant.java` has no EvoCycle constant. The service is built on the app's shared Retrofit for `https://api-kohler-us.kohler.io/` (`na0/g.java`, `na0/o.java`, `na0/b.java:147`), so headers and auth are as in platform.md. |
| `pf0/*` → `mf0/*` → `of0/*` → `la0/b.java` | Use case → repository interface → implementation → request wrapper. Tables in §2.1 and §3.1. |
| `uf0/c.java` | Control-screen view model: builds `shower` and `clean` |
| `tf0/l.java` | Control-screen state, MQTT intake |
| `eg0/p0.java`, `eg0/d2.java` | Settings: shared state, `showersetting` sender, remove product |
| `og0/l.java`, `ig0/c.java`, `pg0/i.java`, `hg0/c.java` | Default shower mode, temperature, flow, maximum temperatures |
| `gg0/n.java`, `mg0/a0.java`, `qg0/q.java` | Clean reminders, interface settings, water-savings goal |
| `sg0/z.java` + `sg0/w.java` | Water Savings screen (`EvoCycleWaterSavingView.kt`) |
| `rg0/t0.java` + `rg0/e0.java` | Water Usage screen, shared with the other products |
| `np0/q.java` + `op0/j.java` | Firmware update |
| `gr0/w.java`, `gr0/j.java`, `kr0/c.java` | Home screen (dashboard) |
| `uf0/c1.java` | The control screen itself (`FavoriteDetailsView.kt` in its trace strings) |
| `yw0/d.java` | The app's built-in EvoCycle fault table |
| `com/kohler/hermoth/products/evocycle/presentation/control/EvoCycleControlActivity.java`, `…/presentation/settings/EvoCycleSettingsActivity.java` | The two activities; both receive MQTT |
| `com/utils/network/retrofit/proxy/device/model/` | Shared `DevicesModel.java`, `WaterSavingGoal.java` |
| `com/utils/network/retrofit/proxy/platform/model/preset/`, `…/platform/model/common/` | Shared `PresetModel.java`, `AllPresetResponse.java`; `FirmwareUpdateRequestModel.java`, `FirmwareUpdateResponseModel.java` |
| `com/kohler/hermoth/products/sfc/provision/data/model/` | `AddYourProductRequest.java`, `WaterSavingGoalModel.java` (the `customer-device` PATCH body) |
| `com/kohler/hermoth/data/network/DeviceApiCall.java`; `com/utils/network/utils/models/ErrorResponseModel.java`; `com/kohler/hermoth/ui/ota/`; `com/kohler/hermoth/common/splash/SplashActivity.java` | Shared API, error body, OTA screens, notification routing |

Bare model file names below (`StandardModeShower.java` …) are in the EvoCycle
`data/model/` or `data/dto/` folders unless this table places them elsewhere.

`uf0/c1.java` decompiles badly. jadx skipped two lambdas (`Method dump skipped`
at `uf0/c1.java:5967`, which is `MainControlScreen`, `FavoriteDetailsView.kt:997`,
and at `:7305`) and garbled the per-card enable logic in `S()`
(`BottomSheetScreen`). The rules in §7.2 were read from a second jadx pass in
`simple` mode on that one class.

---

## 1. Device type (SKU) and recognition

| SKU | What it is | Evidence |
|---|---|---|
| `EVO` | EvoCycle shower: "Evocycle" on the product tile, "EvoCycle" elsewhere | product tile `rt0/d.java:30` (`productModel.x("EVO")`, `text_evo_cycle`, strings.xml:1867); dashboard routing `fr0/f.java:1114` (`sku.equals("EVO")` → `x3()` → `EvoCycleControlActivity` at `:1841`); `text_device_evo_cycle` (strings.xml:1754) |

No other EvoCycle SKU appears in the decompile.

**Recognition in the account read.** A `customerHome[].devices[]` entry of
`customer-device/{tenantId}` (platform.md §3) with `sku == "EVO"`. The app mostly
compares case-sensitively; the home screen ignores case (`gr0/w.java:1901`,
`kr0/c.java:107`). The device-id shape is not found in the
decompile; branch on `sku`, as platform.md says.

EvoCycle uses three device-record fields that other chapters do not
(`DevicesModel.java:70,73,82`):

| Field | Type | Meaning |
|---|---|---|
| `showOnBoardingScreen` | Boolean | Show the four onboarding pages (§8) |
| `showWaterSavingGoalScreen` | Boolean | Prompt for a goal |
| `waterSavingGoal` | `{goalPercentage: Integer, isEnableWaterSave: Boolean}` (`WaterSavingGoal.java:14,17`) | Monthly water-savings goal (§6.4) |

Where else the app branches on `EVO`:

- **Home screen.** Preset tiles come from `customer-experience.evoPresets` (§2.8).
  A tap sends `onoffshower` (`gr0/w.java:1901-1905` → `U0()` at `:1531`). The tile
  is disabled while `isCleanOn` (`kr0/c.java:107-110`). Any MQTT message with
  `sku == "EVO"` refreshes the tiles (§4.4).
- **Water usage.** `rg0/t0.java:267` picks `evo-usage`; EVO is one of the SKUs
  shown with decimals (`rg0/e0.java:100`, the set `{"SEN","SET","GCS","HUB","EVO"}`).
- **Firmware.** Account firmware list `ba0/j.java:272`
  (`evoCycleCheckFirmwareStateUseCase`); the shared OTA screen hands EVO back to
  `EvoCycleControlActivity` (`ui/ota/OTAFirmwareUpdateActivity.java:386`).
- **Notifications.** `common/splash/SplashActivity.java:451,459` (§6.6).

**The `sku` inside command bodies is not always the device's.** The app hard-codes
`"EVO"` for `shower`, `onoffshower`, `clean` and `uisetting` (`uf0/c.java:470,693`,
`gr0/w.java:910`, `mg0/a0.java:350`). It copies `sku` from the `evo-configuration`
response for `showersetting` and `cleansetting` (`og0/l.java:95`, `gg0/n.java:330`),
and from the device record for `productreset` and the remove PATCH
(`eg0/d2.java:837,849`). The faucet's builders, by contrast, always use the device
record. All of these should be `"EVO"` (**inferred**); send the device's own `sku`.

---

## 2. Reads

### 2.1 Summary

| # | Method | Path | Service method (`EvoCycleApiServices.java`) → `la0/b.java` | Response model | App use |
|---|---|---|---|---|---|
| R1 | GET | `/devices/api/v1/device-management/evo-state/{deviceid}` | `getEvoCycleState` `:111` → `E1` `:12512` | `EvoCycleStateResponse` | yes |
| R2 | GET | `/devices/api/v1/device-management/evo-configuration/{deviceid}` | `getEvoCycleConfiguration` `:99` → `C1` `:12472` | `EvoCycleConfigurationResponse` | yes |
| R3 | GET | `/devices/api/v1/device-management/evo-dashboard/{deviceid}?CurrentDate&TimeZone` | `callDashboardWeeklyConsumption` `:51` → `G` `:12544` | `EvoCycleDashboardWeeklyConsumptionResponse` | yes |
| R4 | GET | `/devices/api/v1/device-management/evo-dashboard/waterusage/{deviceid}?FromDate&ToDate&TimeZone&Interval` | `callDashboardWaterUsage` `:45` → `F` `:12524` | `WaterUsageResponse` | yes |
| R5 | GET | `/devices/api/v1/device-management/evo-usage/{deviceid}?FromDate&ToDate&TimeZone&Interval` | `callWaterUsage` `:81` → `i0` `:13080` | `WaterUsageDataResponse` | yes |
| R6 | GET | `/devices/api/v1/device-management/evo-diagnostics/{deviceid}` | `getEvoCycleComponentError` `:93` | Anthem `DiagnosticModel` | **declared, never called** |
| R6′ | GET | `/devices/api/v1/device-management/gcs-diagnostics/{deviceId}` | `DeviceApiCall.getAnthemDiagnosticApis` via `la0/b.java:12848` `V0` | `DiagnosticModel` | **what the EvoCycle Diagnostics screen reads** (§2.6) |
| R7 | DELETE | `/devices/api/v1/device-management/evo-diagnostics/{deviceid}` | `deleteEvoCycleDiagnostic` `:87` → `A0` `:12428` | `CommonResponse` | yes ("Clear") |
| F1 | GET | `/platform/api/v1/firmware/evo/{deviceid}?releasetarget=Public` | `callFirmwareStateApi` `:63` → `M(id, "Public")` `:12664` | `FirmwareUpdateResponseModel` | yes (§6.5) |
| A1 | GET | `/devices/api/v1/device-management/customer-device/{tenantId}` | `getEvoCycleCustomerDeviceData` `:105` → `D1` `:12492` | `DeviceDataResponse` | yes (platform.md §3) |
| A2 | GET | `/devices/api/v1/device-management/customer-experience/{tenantid}` | shared `getAllPresetApi` | `AllPresetResponse.evoPresets` | home screen (§2.8) |

Use-case chains: R1 `pf0/n`→`of0/n`, R2 `pf0/e`→`of0/e`, R3 `pf0/g`→`of0/h`,
R4 `pf0/f`→`of0/g`, R5 `pf0/s`→`of0/r`, R7 `pf0/d`→`of0/d`, F1 `pf0/a`→`of0/a`
(`of0/a.java:101` passes `"Public"`), A1 `pf0/j`→`of0/f`.

**Query parameters of R3–R5.** All PascalCase, like the other usage reads
(platform.md §6):

- `FromDate`, `ToDate`, `CurrentDate`: `MM-dd-yyyy` (`sg0/z.java:529,531`,
  `sg0/w.java:1058`; R5 through the shared `rg0/t0.java` `b0()`).
- `Interval`: `Day`, or `Month` on the Year tab (`sg0/z.java:533`, `rg0/t0.java:1466`).
- `TimeZone`: **EvoCycle-only, and odd.** The repositories add it themselves
  (`of0/g.java:143`, `of0/h.java:130`, `of0/r.java:143`) from the phone's zone
  through `zn0/h.java:678` `l()`, which maps the zone's **raw offset in seconds**:

  | Raw offset | Sent |
  |---|---|
  | +10800 (UTC+3) | `NST` |
  | +14400 (UTC+4) | `AST` |
  | +18000 (UTC+5) | `EST` |
  | +19800 (UTC+5:30) | `IST` |
  | +21600 (UTC+6) | `CST` |
  | +25200 (UTC+7) | `MST` |
  | +28800 (UTC+8) | `PST` |
  | anything else | `UTC` |

  Only positive offsets match, so **every US time zone sends `UTC`**, and a phone
  at UTC+5 sends `EST`. It looks like a sign bug (**inferred**). What the server
  does with the value is not found in the decompile (**live check**). Sending
  `UTC` reproduces what a US phone sends.

### 2.2 R1 `evo-state`

Model `products/evocycle/data/model/EvoCycleStateResponse.java`, with `state`
in `State.java`.

Top level:

| Field | Type | App usage |
|---|---|---|
| `connectionState` | String | Exactly `"Disconnected"` sends the control screen to the offline page (`uf0/c1.java:3180`). `fa0/o.java:1071-1087` `t()` treats any on-flag as off while disconnected (case-insensitive). A missing value counts as online (`String.valueOf(null)` is `"null"`). |
| `recycledWaterUsed` | Integer | **Lifetime water saved, litres.** "Total Water Savings" on the Water Savings screen; gallons = round(L × 0.264172) (`sg0/w.java:2245-2249`, `:4491-4499`). |
| `state` | `State` | below |
| `lastConnected`, `createdTimestamp`, `updatedTimestamp`, `lastOTANotificationSentTime` | Integer | not read |
| `lastDisConnected`, `applicationSource` | Object | not read |
| `id`, `deviceId`, `sku`, `tenantId`, `deletedTenantId`, `lastUpdatedSource`, `deviceConnectionEventSequenceNumber`, `ioTHub` | String | not read. `ioTHub` is a connection detail: never log it. |
| `scheduleOTAConfiguration` | `{isScheduleOTA: Boolean, otaTimeFrameFrom: Object, otaTimeFrameTo: Object}` | not read |

`state`:

| Field | Type | App usage |
|---|---|---|
| `shower.standardModeShower` | `{showerOn: Integer, showerMode: Integer, temperature: Double, flow: Integer, warmup: Integer, lastRunTimestamp: Integer}` (`StandardModeShower.java:16-31`) | `showerOn == 1` means Standard mode is running. `temperature` (tenths of °C) and `flow` (percent) are shown on the card and **sent back unchanged when the app starts or stops this mode** (§3.2). `showerMode`, `warmup`, `lastRunTimestamp`: not read. |
| `shower.reCycleModeShower` | the same fields, but `lastRunTimestamp` is Object (`ReCycleModeShower.java:16-31`) | the same, for Cycle mode |
| `shower.currentShowerMode` | Object | not read |
| `clean.rapidClean` | `{cleanOn: Integer, cleanMode: Integer, lastCleanDate: Long, lastCleanDuration: Integer}` (`RapidClean.java:16-25`) | `cleanOn == 1` means a Rapid Clean is running. `lastCleanDate` is epoch **seconds**, shown `MMM dd, yyyy` (`qf0/b.java:498-499`; `by0/a.java:350-353` `n()` multiplies by 1000). `lastCleanDuration`: not read. |
| `clean.deepClean` | the same, but `lastCleanDuration` is Object (`DeepClean.java:16-25`) | the same, for Deep Clean |
| `clean.currentCleanMode` | Object | not read |
| `deviceErrorState` | `{currentDeviceState: String, currentErrorCode: Integer, affectedOperations: String}` (`DeviceErrorStateModel.java:15-21`) | When `currentDeviceState` is `"error"` (any case) and `currentErrorCode > -1`, the code goes through the fault table (§5.3) and drives the same fault UI as MQTT does (`uf0/c1.java:3194-3214`). `affectedOperations`: not read. |
| `gateway` | `{rssi, freeMemory, runTime}`, all String (`GatewayModel.java:15-21`) | not read |
| `ready` | String | not read (the DTV models have a field of the same name) |
| `rebootStatus` | Boolean | not read |

"Running", in the app's sense, is any of the four flags `showerOn`/`cleanOn`
equal to 1 (`qf0/b.java:150-168` `G()`). `cleanOn` can also be 2 over MQTT
(cancelling, §4.2); `G()` does not count 2.

**When the app reads R1.** There is **no periodic poll**:

- the control screen, on every `ON_RESUME` (`uf0/c1.java:3578-3583`, `tf0/l.java:703` `P()`);
- the Water Savings screen, when it opens (`sg0/w.java:1055`);
- before every settings save, product removal and firmware install, to refuse
  while running (§7.4; `eg0/p0.java:1184` `W()`).

Between reads the control screen is driven by MQTT (§4).

### 2.3 R2 `evo-configuration`

Model `EvoCycleConfigurationResponse.java:15-30`:
`{createdTime: Integer, deviceId, sku, tenantId, evoAboutModel, firmwareUpdate}`.
**The settings sit under the key `evoAboutModel`**, not `configuration` as for the
other products (**live check**).

`evoAboutModel` (`EvoAboutModel.java:16-31`):

| Block | Fields (types) | App usage |
|---|---|---|
| `showerConfiguration` (`ShowerConfiguration.java:15-30`) | `defaultShowerMode: Integer` (0 Standard, 1 Cycle); `defaultTemperature`, `defaultStandardMaxTemperature`, `defaultCycleMaxTemperature: Double` (tenths of °C); `defaultFlow: Double` (percent); `defaultWarmupMode: Integer` (0/1) | Default Settings screens. Every `showersetting` write is rebuilt from this block (§3.5). |
| `cleanConfiguration` (`CleanConfiguration.java:15-21`) | `enableRapidClean`, `enableDeepClean`, `showerFilterCleanEnable`: Integer 0/1 | Clean Cycle Reminder screen. **A missing value is shown as on** (`qf0/b.java:353-355` `i()`). |
| `uiConfigurations[]` (`UiConfigurationsItem.java:15-63`) | `name`, `model`, `status`, `appVersion`, `firmware`, `assetVersion`, `language`, `languageRegion`, `backLight` (String); `temperatureUnits`, `flowUnits`, `hapticFeedback` (Integer); `backlight`, `displayBrightness`, `panelLanguage`, `panelUnits`, `serialNumber` (Object) | Interface settings use element **[0]** (`mg0/x.java:750`). About lists the `name` and `appVersion` of [0] and [1] (`kg0/i.java:457-463`), so expect two digital interfaces (**inferred**). |
| `productConfiguration` (`ProductConfiguration.java:14-26`) | `model`, `serialNumber`, `appVersion`, `hardwareVersion`, `productSKU` | About → Controller |
| `valveConfiguration` (`ValveConfiguration.java:14-26`) | `name`, `model`, `serialNumber`, `appVersion`, `status` | About → Valve |
| `gatewayConfiguration` (`GatewayConfiguration.java:15-69`) | `appVersion`, `appFwpn`, `hardwareVersion`, `serialNumber`, `connected`, `mac`, `ssid`, `bssId`, `iotHub`, `tenantId`, `x509Issuer`, `x509ValidFrom`, `x509ValidTo`, `logLevel`, `rebootReason` (String); `dpsAt`, `rebootAt`, `rebootCount`, `heartBeatInterval` (Integer) | About → Gateway shows only `appVersion` (`kg0/i.java:546`). **Never log this block**: it holds the MAC, the Wi-Fi SSID and BSSID, the IoT Hub and certificate details. |

Two quirks on the About page: Status is the fixed text "Connected", not data
(`kg0/i.java:510,526`), and the model and serial rows read `productConfiguration`
when the Valve section is open and `valveConfiguration` otherwise
(`kg0/i.java:516,520`), which looks swapped (**inferred**).

`firmwareUpdate` (`products/evocycle/data/model/FirmwareUpdate.java:14-41`):
`status: String`; `isAutoUpdate`, `isSingleFirmwareUpdate: Boolean`; `progress`,
`progressPercent`, `version`, `firmwareType`, `interface`, `lastUpdated`,
`startTimestamp`: Object. The firmware flow polls `progress` (§6.5).

The model declares no top-level `iot` block. Other products' configuration
replies carry one (platform.md §2.2), so treat an unexpected one the same way
(**inferred**).

**When read:** when Settings opens (`eg0/a2.java:302` → `eg0/p0.java:1165` `T()`),
when Digital Interface Settings opens (`mg0/x.java:265`), and every 10 s during a
firmware install (§6.5).

### 2.4 R3 `evo-dashboard` (this week)

`EvoCycleDashboardWeeklyConsumptionResponse.java:14-20`:
`{deviceId, currentWeekUsageData, previousWeekUsageData}`. Each is
`EvoCycleDashboardWeeklyConsumption` `{waterSaved, waterUsage, totalConsumption, duration}`,
**all Integer** (`EvoCycleDashboardWeeklyConsumption.java:15-24`).

The app reads only `currentWeekUsageData.waterSaved` and `.waterUsage` (§6.3);
`previousWeekUsageData` has no reader. `CurrentDate` is today
(`sg0/w.java:1058-1060`). It is read when the Water Savings screen opens.

### 2.5 R4 `evo-dashboard/waterusage` and R5 `evo-usage`

**R4** returns `WaterUsageResponse.java:16` `{waterUsageData: [WaterUsageListModel]}`.
Every entry field is a **String** (`WaterUsageListModel.java:15-36`): `deviceId`,
`intervalKey`, `waterSaved`, `waterUsage`, `totalConsumption`, `duration`,
`reCycleWaterPercentage`, `standardWaterPercentage`. The Water Savings charts use
`waterSaved` and the two percentages (§6.3).

**R5** returns `WaterUsageDataResponse.java:17-59`:

- summary: `deviceId`, `interval` (String); `avg`/`min`/`max` × `Duration`,
  `WaterUsage`, `WaterSaved`, `TotalWaterConsumption` (Double; e.g.
  `avgWaterSaved`, `maxTotalWaterConsumption`);
- buckets: `evoUsageDataDetailsList[]` =
  `{intervalKey: String, waterUsage, waterSave, totalWaterConsumption, duration: Double}`
  (`EvoUsageDataDetailsListItem.java:15-27`).

The bucket key is **`waterSave`**, while the summary keys say `WaterSaved`. R5
feeds the Water Usage screen (§6.2). Ranges and timing are in §6.1.

### 2.6 R6, R6′ and R7: diagnostics

- **The Diagnostics screen reads `gcs-diagnostics`, not `evo-diagnostics`.** Its
  view model `jg0/m.java` fetches through `hb0.j` (`evoCycleDiagnosticUseCase`,
  `jg0/m.java:34,400`). That class's only implementation, `gb0/i.java` `a()`,
  calls `la0/b.java:12848` `V0()` → `DeviceApiCall.getAnthemDiagnosticApis("v1", id)`
  → `GCS_DIAGNOSTIC_APIS` (`ApiConstant.java:119`). R6 has no caller.
- "Clear" asks "Are you sure you want to clear error logs?"
  (`text_message_clear_error_logs`, strings.xml:2247) and then sends R7
  (`jg0/m.java:213-216`).
- The list shows **every** `errorDetails[]` entry, without the `isActive` filter
  of platform.md §8, as "Error ‹errorCode›, ‹MMM dd, yyyy› at ‹hh:mm aa›", with
  `timestamp` in epoch seconds (`jg0/j.java:288-295`).

Whether `gcs-diagnostics` returns EvoCycle entries, and what `evo-diagnostics`
returns, are **live checks**.

### 2.7 F1 firmware check

See §6.5.

### 2.8 A2 home-screen tiles (`customer-experience` → `evoPresets`)

`AllPresetResponse.java:26` declares `evoPresets: [PresetModel]`. The EvoCycle
fields of `PresetModel` are `id`, `title`, `deviceId`, `deviceLogicalName`, `sku`,
`state` (compared case-insensitively with `"ON"`), `isCleanOn` (Boolean), and
`showerMode`, `temperature`, `flow` (Integer) (`PresetModel.java:18-88`). A tap
sends `onoffshower` built from them (§3.3). How the server creates these entries
is not found in the decompile. The app special-cases a title of `"Cycle Mode"`
(`gr0/w.java:926-935`), so expect one tile per shower mode (**inferred**).

---

## 3. Commands

### 3.1 Summary

All are `POST`.

| # | Path | Service method → `la0/b.java` | Body | Response | Sent from |
|---|---|---|---|---|---|
| C1 | `/platform/api/v1/commands/evo/shower` | `postEvoCycleShowerOnOff` `:141` → `T2` `:12816` | `EvoCycleShowerOnOffRequest` | `EvoCycleControlResponse` | control screen, Standard or Cycle Start/Stop (`uf0/c.java:818` `U()`) |
| C2 | `/platform/api/v1/commands/evo/onoffshower` | `postEvoCycleOnOffShower` `:135` → `S2` `:12796` | `EvoCycleShowerOnOffRequest` | `EvoCycleOnOffResponse` | home-screen tile (`gr0/w.java:1531` `U0()`) |
| C3 | `/platform/api/v1/commands/evo/clean` | `postEvoCycleCleanOnOff` `:117` → `P2` `:12736` | `EvoCycleCleanOnOffRequest` | `EvoCycleControlResponse` | control screen, Rapid or Deep (`uf0/c.java:812` `T()`) |
| C4 | `/platform/api/v1/commands/evo/showersetting` | `postEvoCycleDefaultSettings` `:129` → `R2` `:12776` | `EvoCycleDefaultSettingsRequest` | `EvoCycleControlResponse` | Default Settings screens (`eg0/p0.java:1299` `u0()`) |
| C5 | `/platform/api/v1/commands/evo/cleansetting` | `postEvoCycleCleanSettings` `:123` → `Q2` `:12756` | `EvoCycleCleanSettingsRequest` | `EvoCycleControlResponse` | Clean Cycle Reminder (`gg0/n.java` `K()`) |
| C6 | `/platform/api/v1/commands/evo/uisetting` | `postEvoCycleUiSettings` `:147` → `U2` `:12836` | `EvoCycleUiSettingsRequest` | `EvoCycleControlResponse` | Digital Interface Settings (`mg0/a0.java:344` `N()`) |
| C7 | `/platform/api/v1/commands/evo/productreset` | `callEvoCycleFactoryResetApi` `:57` → `K` `:12624` | `EvoCycleFactoryResetRequest` | `EvoCycleControlResponse` | remove product (`eg0/d2.java:831` `I()`) |

Use-case chains: C1 `pf0/m`→`of0/m`, C2 `pf0/k`→`of0/k`, C3 `pf0/b`→`of0/b`,
C4 `pf0/q`→`of0/p`, C5 `pf0/c`→`of0/c`, C6 `pf0/r`→`of0/q`, C7 `pf0/h`→`of0/i`.
Two account writes (§3.8) and the firmware install (§6.5) complete the list. No
other EvoCycle endpoint string exists in the decompile.

Responses:

- `EvoCycleControlResponse` `{correlationId: String, timestamp: Long}`
  (`EvoCycleControlResponse.java:14-17`): accepted for delivery, not applied
  (platform.md §2.1).
- `EvoCycleOnOffResponse`, C2 only, adds `statusCode` (**Integer**, not String),
  `message`, `detail` and `error: ErrorsDetail`
  `{area, errorCode, errorDescription, isActive: boolean, timestamp: Integer, type, valveId}`
  (`EvoCycleOnOffResponse.java:16-31`, `ErrorsDetail.java:14-32`). The app reads
  none of them on success (`gr0/w.java:754-770` forwards the object unread).

On success the control screen only hides its spinner (`uf0/c1.java:3254-3270`
`E1()`). The cards change when MQTT arrives (§4). The app does not re-read R1
after a command, and the EvoCycle screens never start the MQTT reply timer that
other products use: `yw0.c` is created (`tf0/l.java:837`) and only ever cancelled
(`EvoCycleControlActivity.java:650`).

### 3.2 C1 `shower`: start or stop a shower

`EvoCycleShowerOnOffRequest` (`data/dto/EvoCycleShowerOnOffRequest.java:14-32`).
The numbers are all Integer.

| Field | What the control screen sends (`uf0/c.java:683-697`) |
|---|---|
| `deviceId`, `tenantId` | device, account |
| `sku` | `"EVO"`, hard-coded |
| `showerOn` | **A toggle**: `0` if that mode is running now, else `1` (`!fa0.o.t(showerOn, connectionState) ? 1 : 0`) |
| `showerMode` | `0` = Standard: "Fresh water will flow from the handshower." `1` = Cycle: "Recirculated water will flow from the showerhead." (strings.xml:1320, 1318) |
| `temperature` | `round()` of the **current state's** `‹mode›.temperature`, tenths of °C (380 = 38.0 °C); `0` if missing |
| `flow` | the current state's `‹mode›.flow`, percent; `0` if missing |

Example, values **inferred**:
`{"deviceId":"<id>","tenantId":"<tenant>","sku":"EVO","showerOn":1,"showerMode":0,"temperature":380,"flow":80}`.
A stop is the same body with `showerOn: 0`.

Call sites: the Standard card `uf0/c1.java:1543-1599` (class `p0`, send at `:1596`),
the Cycle card `:1693-1772` (class `r0`, send at `:1770`), and the detail sheet
`:562-733` (class `g`, sends at `:649` and `:726`).

- **There is no live temperature or flow control.** The app never sends a
  user-chosen temperature or flow with C1; both come from state. The cards carry
  the note "This will run the action you have defined in the default settings.
  Changes made on the device will not be reflected below."
  (`text_cycle_mode_subheader`, strings.xml:1697; `qf0/b.java:341,348`), so the
  state values are presumably the defaults (**inferred**). To change them, send
  C4. The only sliders in EvoCycle code are on the settings screens (`pg0/h.java`,
  `ig0/b.java`, `hg0/b.java`, `qg0/n.java`).
- **Starting a shower has no confirmation dialog** (`uf0/c1.java:1574-1596`).
- Cycle mode, per its description: "Standard Mode will run until enough water is
  collected. Then, recycled water will flow from the showerhead."
  (`text_cycle_description`, strings.xml:1695; `qf0/b.java:547`).

### 3.3 C2 `onoffshower`: the home-screen tile

The body class is the same as C1's. It is built from the tile's `PresetModel`
(`gr0/w.java:901-944`):

| Field | Value |
|---|---|
| `deviceId` | `presetModel.deviceId`, or `""` |
| `tenantId`, `sku` | account; `"EVO"` |
| `showerOn` | `0` if the tile's `state` is `"ON"` (case-insensitive), else `1` |
| `showerMode` | `1` if the tile's `title` is `"Cycle Mode"` (case-insensitive), else `presetModel.showerMode` (default 0) |
| `temperature` | `round(presetModel.temperature)`, default 0 |
| `flow` | `presetModel.flow`, default 0 |

On an error the home screen parses the message text. An all-digit message other
than `900` is treated as a fault code and shown through the fault table; `900`
shows "Product is offline." (`gr0/j.java:257-300`). Which of C1 and C2 the cloud
prefers is not found in the decompile (**live check**). C1 is the path the
product's own screen uses.

### 3.4 C3 `clean`: Rapid or Deep Clean

`EvoCycleCleanOnOffRequest` (`data/dto/EvoCycleCleanOnOffRequest.java:14-26`),
built at `uf0/c.java:463-471`:

| Field | Value |
|---|---|
| `deviceId`, `tenantId` | device, account |
| `sku` | `"EVO"` |
| `cleanOn` | Integer, a toggle: `0` if that clean is running, else `1` |
| `cleanMode` | Integer: `0` = Rapid Clean, `1` = Deep Clean |

The durations are fixed in the app: Rapid "5 mins", Deep "15 mins"
(`qf0/b.java:552-560` `x()`). Both run "ozonated water to clean the shower system.
Please stay out of the shower during cleaning." (`text_rapid_description`,
`text_deep_description`, strings.xml:2588, 1706).

A clean start goes through a confirmation; a stop does not (§7.1). Call sites:
Rapid `uf0/c1.java:474-560` (class `f0`), Deep `:847-925` (class `h0`), the detail
sheet `:654-701`, the confirmation's Start `:6208-6240` `t()`. The app sends only
`0` or `1`; the value `2` exists only in MQTT (§4.2).

### 3.5 C4 `showersetting`: default shower settings

`EvoCycleDefaultSettingsRequest` (`data/dto/EvoCycleDefaultSettingsRequest.java:13-37`).
**Every write sends all nine fields.** The app rebuilds them from R2's
`showerConfiguration`, replaces the one edited value, and casts the Doubles with
`(int)`, which truncates.

| Field | Type | Source | Range in the app's UI |
|---|---|---|---|
| `deviceId`, `tenantId`, `sku` | String | R2 top level | |
| `showerMode` | Integer | `defaultShowerMode` | `0` Standard, `1` Cycle (`qf0/b.java:419-422` `n()`) |
| `temperature` | Integer | `defaultTemperature` | tenths of °C. Slider from 15 °C (59 °F) to the lower of the two maxima, or 48 °C (118 °F) when they are unknown; step 1 display unit (`ig0/b.java:1337-1345`) |
| `flow` | Integer | `defaultFlow` | percent. Slider **4–100, steps 4** (`pg0/h.java:1215`); the Settings list shows anything ≤ 4 as "4%" (`eg0/d2.java:1012`) |
| `warmupMode` | Integer | `defaultWarmupMode` | `0` off, `1` on (`eg0/a2.java:2011-2024` `z()`) |
| `standardModeMaxTemperature` | Integer | `defaultStandardMaxTemperature` | tenths of °C. Slider **15–48 °C / 59–118 °F** (`hg0/b.java:1518-1522`) |
| `cycleModeMaxTemperature` | Integer | `defaultCycleMaxTemperature` | tenths of °C. Slider **15–43 °C / 59–109 °F** (`hg0/b.java:1613-1617`) |

Builders: shower mode `og0/l.java:82`, temperature `ig0/c.java:82`, flow
`pg0/i.java:75`, maxima `hg0/c.java:92`, warmup `eg0/d2.java:857`.

**Units when saving** (`ig0/b.java:944`, `hg0/b.java:1114`):

- On a Celsius account the app sends °C × 10. "Celsius" means the account's
  `temperatureUnit` equals `Celsius` (`qf0/b.java:170` `H()`).
- On a Fahrenheit account it uses a lookup table, `mc0/n.java:233` `F()`, running
  from 59 °F → 150 to 122 °F → 500. The table is hand-written (60 °F → 156 is
  rounded, 100 °F → 377 is truncated), and **any °F value outside 59–122 maps
  to 0**.
- Display goes the other way: °C = `t / 10`, and °F = `round(t / 10 × 1.8 + 32)`,
  with a value that rounds to 0 °C shown as 0 (`qf0/b.java:280-285` `d()`).
- Saving new maxima also lowers `temperature` to the new Standard maximum when the
  default was above it (`hg0/c.java:116-117`). A maximum of `0` from the screen
  means "keep the stored value" (`hg0/c.java:127-141`).

What the defaults do, per the screens: Shower Mode — "Set your default shower
mode. The Simple Start button on Digital Interface activates this mode."; Temperature
— "Set your default temperature for Standard and Cycle modes."; Valve Flow — "Set
your default valve flow for Standard mode."; Warmup — "Warmup Mode flushes out
water to bring the shower up to desired temperature" (strings.xml:2691, 2692,
2693, 1319).

### 3.6 C5 `cleansetting`: clean reminders

`EvoCycleCleanSettingsRequest` (`data/dto/EvoCycleCleanSettingsRequest.java:13-28`),
built at `gg0/n.java:308-331`. All three flags are always sent.

| Field | Type | Meaning, per the screen |
|---|---|---|
| `rapidCleanEnable` | Integer 0/1 | "5 min Rapid Clean Cycle reminder after each shower" (strings.xml:2587) |
| `deepCleanEnable` | Integer 0/1 | "15 min Deep Clean Cycle reminder after 60 showers" (strings.xml:1705) |
| `showerFilterCleanEnable` | Integer 0/1 | "Filter clean reminder after every 25 showers" (strings.xml:2753) |
| `deviceId`, `tenantId`, `sku` | String | from R2 |

These turn **reminders** on and off (push notifications, §6.6), not automatic
cleans; the screen is "Clean Cycle Reminder", subtitled "Get reminders for Ozone
Clean Cycles." (strings.xml:1623, 2443) (**inferred**). The names differ between
the write (`rapidCleanEnable`) and the read (`enableRapidClean`).

### 3.7 C6 `uisetting`: Digital Interface Settings

`EvoCycleUiSettingsRequest` (`data/dto/EvoCycleUiSettingsRequest.java:14-41`),
built at `mg0/a0.java:344-360`:

| Field | Value |
|---|---|
| `deviceId`, `tenantId` | device, account |
| `sku` | `"EVO"` |
| `language` | `"en"`, `"es"` or `"fr"` (`eg0/p0.java:1075-1077`; chosen at `lg0/i.java:147,182`) |
| `languageRegion` | `"US"`, `"MX"` or `"CA"`, paired with the language |
| `temperatureUnits` | Integer: `0` = "US gal/°F", `1` = "L/°C" (`mg0/x.java:527-529,568-570`) |
| `flowUnits` | **the same value as `temperatureUnits`** (`mg0/a0.java:356`); the app offers one "Panel Units" choice |
| `hapticFeedback` | Integer 0/1 (`mg0/x.java:631-632`) |
| `backlight` | String `"0"`: the constructor default, never changed (`EvoCycleUiSettingsRequest.java:45`) |
| `isUpdateUI0Setting` | Boolean `true` (`mg0/a0.java:359`). The name suggests "interface 0" (**inferred**); the app cannot address a second interface. |

The language pair comes from the saved default configuration
(`uiDefaultConfiguration`); units and haptics come from the edited copy, which
starts from `uiConfigurations[0]`.

### 3.8 Account writes

Both go to the shared
`PATCH /devices/api/v1/device-management/customer-device/{tenantId}/{homeId}/device`
(`EvoCycleApiServices.java:75,153,159`).

- **Water-savings goal** (`qg0/q.java:897` `U()`): `AddYourProductRequest` with
  `deviceId`, `logicalname` (**lowercase key**), `sku: "EVO"`, `operation: "Update"`,
  `showOnBoardingScreen` (copied), `showWaterSavingGoalScreen: ‹enabled›` and
  `waterSavingGoal: {isEnableWaterSave, goalPercentage}`. "Skip" (`T()` at `:878`)
  sends the same without `showOnBoardingScreen`, with `showWaterSavingGoalScreen: false`
  and the old goal. The
  slider is `KKSliderConfig(min 10, max 100, steps 10)` (`qg0/n.java:2296`).
  Provisioning seeds `{goalPercentage: 50, isEnableWaterSave: false}`
  (`vf0/z.java:328-337`).
- **Remove product** (§7.5): C7, then this PATCH with `EvoCycleRemoveProductRequest`
  `{deviceId, sku, logicalName, operation: "Delete", keepDeviceData: !removeUsage}`
  (`eg0/d2.java:844-855`). Not for an integration.

### 3.9 C7 `productreset`

`EvoCycleFactoryResetRequest`
(`presentation/settings/EvoCycleFactoryResetRequest.java:13-31`):
`{deviceId, tenantId, sku, isFactoryReset: true, isRemoveFromApp: true}`. Both
flags default to true and are set true again (`eg0/d2.java:831-842`). The app
sends it only in the remove-product flow, followed by the PATCH. **Destructive:
do not use it.**

---

## 4. MQTT

### 4.1 No `_STS` code

**EvoCycle has no MQTT code.** None of the `_STS` or `_ALT` codes in the app is
EvoCycle's. Its screens neither dispatch on `data.code` nor read `attributes[]`.
They parse the whole message as `EvoCycleMqttResponse` and act on blocks under
`data`.

The envelope model (`EvoCycleMqttResponse.java:15-36`) has `data`, `type`, `sku`,
`timestamp` and `ver`, plus `deviceId`, `tenantId` and `messageId` in camelCase.
The app reads only `type` and `data`. It filters with a separate JSON read of
lowercase **`deviceid`** (`EvoCycleControlActivity.java:649`, case-sensitive
compare). Real messages therefore presumably carry the shared lowercase envelope
(platform.md §4.3), and the camelCase fields stay empty (**inferred**;
**live check**).

Handling (`EvoCycleControlActivity.java:648-662` `j1()`; the same in
`EvoCycleSettingsActivity.java:1005-1017`):

1. Drop messages whose `deviceid` is not the open device.
2. **Drop `type == "INF"`** (`EvoCycleControlActivity.java:653`). No other `type`
   value is found in the decompile.
3. Pass `data`, or an empty model if it is missing, to `tf0/l.java:840` `v0()`.
   The control screen merges it in `uf0/c1.java:3315` `F1()` (run from `b0` at
   `:282`); settings screens receive it through `eg0/p0.java:1103` `H0()`.

### 4.2 The `data` blocks

`EvoCycleMqttDataModel.java:14-23`: any of `shower`, `clean`, `product` and
`valve` may be present.

| Block | Fields (types) | How `F1()` applies it |
|---|---|---|
| `shower` (`ShowerModel.java:14-26`) | `showerOn: Integer`, `showerMode: Integer`, `temperature: String`, `flow: String`, `warmupStatus: String` | `showerOn 1` with `showerMode 0`: Standard on, Cycle off. `showerOn 1` with `showerMode 1`: Cycle on, Standard off. Anything else: both off (`uf0/c1.java:3422-3455`). `temperature`, `flow` and `warmupStatus` are **not read**. |
| `clean` (`CleanModel.java:14-26`) | `cleanOn: Integer`, `cleanMode: Integer`, `lastCleanDate: String`, `lastCleanDuration: String`, `errorCode: Integer` | By (`cleanOn`, `cleanMode`): **(1, 0)** Rapid on, Deep off; **(1, 1)** Deep on, Rapid off; **(2, 0)** and **(2, 1)** show "Canceling Rapid/Deep Clean Cycle". Anything else means the clean ended: a cancel in progress turns into "… Clean Cycle Canceled" (§7.1); the clean that was running gets `lastCleanDate`, parsed as epoch seconds (now, if missing or 0); both cleans go off (`uf0/c1.java:3457-3518`). `lastCleanDuration` and `errorCode` are not read. |
| `product` (`MqttProductModel.java:14-17`) | `errorCode: Integer`, `reset: Integer` | `errorCode == 0` clears the fault. Otherwise the code, **or `valve.errorCode` when present, which takes precedence**, goes through the fault table (§5.3), and its impact forces flags off: `a` all four, `c` Cycle and both cleans, `b` both cleans (`uf0/c1.java:3352-3420`). `reset` is not read. |
| `valve` | same model | Used only as the override above. A `valve` block **without** a `product` block is ignored. |

A non-zero code missing from the fault table changes nothing over MQTT: the
previous fault, if any, stays shown (`yw0/d.java:108-115` `c()` returns false, and
`F1()` has no else branch). The same unknown code arriving through R1 clears the
fault UI (`uf0/c1.java:3205-3214`).

Types differ from REST: `shower.temperature` and `flow` are Strings over MQTT but
Double and Integer in R1, and `lastCleanDate` is a String of seconds over MQTT
but a Long in R1.

### 4.3 On settings screens

While a settings screen is open, "running" comes from the latest MQTT data first:
`clean.cleanOn == 1`, else `shower.showerOn == 1`, else R1's `G()`
(`og0/k.java:354-372`). The save itself is still gated on a fresh R1 (§7.4).

### 4.4 On the home screen

A message whose top-level `sku` is exactly `"EVO"` (and that is not a `SENSATE_*`
or `START_PRESET_STS` message) makes the home screen reload its tiles 1.5 s
later (`gr0/j.java:1026-1033` → `gr0/w.java:1520-1528` `T0()` → `j0()`, which
reloads `AllPresetResponse`).

### 4.5 What the app sends

Nothing EvoCycle-specific. The connection and the `MOBILECONNECT` event come from
the shared code (platform.md §4; `EvoCycleControlActivity.java:303-316` `M3()`).
As for every product, the broker replays nothing, so read R1 after connecting.

---

## 5. Status codes and refusals

### 5.1 Codes from the cloud

Error bodies go through the shared parser. On HTTP 400 the app reads
`ErrorResponseModel` (`statusCode` as a String, `message`, `errorCode`, `key`,
`messageId`; `ErrorResponseModel.java:16-28`) and maps `statusCode` through
`oy0/a.java` and `oy0/b.java`; unknown codes become "Something went wrong, please
try again." (`ja0/b.java:166-185,232-234` `d()`; platform.md §2.1). The control screen
then reacts in `uf0/c1.java:3254-3305` `E1()`:

| Code | App text | Reaction |
|---|---|---|
| 900 | "Product is offline." (strings.xml:364) | opens the offline page (`EvoCycleSettingsActivity` with `from = "Offline"`) |
| 903 | "This operation cannot started due to firmware update in progress." (strings.xml:366) | an "Error" dialog with that text (`uf0/c1.java:3292-3294`, `:3011-3013`) |
| **919** | "Ozone generation error has occurred." (`error_msg_919`, strings.xml:383; `oy0/b.java:244`) | **EvoCycle-specific**: dialog "Error during cleaning cycle" / "Please wait while ozone dissipates." (`uf0/c1.java:3288-3290`, `:2984-2986`; strings.xml:361, 360) |
| others | as in platform.md | a network-error popup with the text (`zn0.h.e`) |

The same offline redirect follows R1's `Disconnected` (`uf0/c1.java:3180`). On the
home screen, C2's numeric error texts are handled as in §3.3.

### 5.2 Refusals the app makes itself

| Situation | Text | Where |
|---|---|---|
| A settings save (C4, C5, C6, the goal) while running | "Settings cannot be saved while product is running." (strings.xml:1843) | `og0/k.java:700-711`, `ig0/b.java:930-944`, `pg0/h.java:815-826`, `hg0/b.java:1104-1114`, `gg0/k.java:676-682`, `mg0/x.java:789-795`, `eg0/a2.java:2011-2024`, `qg0/n.java:1778,2397` |
| Remove product while running | "Product cannot be removed while its running." (strings.xml:1847) | `eg0/a2.java:1861-1871` |
| Firmware install while running | "Firmware cannot be updated while %1$s is running." (strings.xml:2375) | `op0/j.java:882-884` |
| Offline | the offline page | §5.1 |
| No phone network | "Please check your network connection and make sure your product is powered on." (strings.xml:1114) | every command handler, e.g. `uf0/c1.java:1574` |

### 5.3 Device faults: the app's own table

The app ships its own EvoCycle fault table, `yw0/d.java:20-86` `b()`, loaded by
`EvoCycleControlActivity.java:587`. Each row has a code, a component (`name`), an
instruction (`description`), an `actionRequired` integer **that nothing reads**,
and an `impact` class (`yw0/b.java`). Codes arrive from MQTT
`product`/`valve.errorCode` (§4.2), from R1 `deviceErrorState.currentErrorCode`
(§2.2), and possibly as C2 error texts. "No fault" is `-1` in the app's model
(`yw0/a.java:31-33`).

| Impact | Effect in the app | Codes: component, instruction |
|---|---|---|
| `a` | All four modes forced off (`uf0/c1.java:3368-3388`). No button-disable rule for `a` was found. | **Digital Valve**: 1640, 1643, 1644, 1647 "Please power cycle your controller and valve…"; 16411 hot-water supply; 16412 cold-water supply; 16450 water supply; 16453 "Please turn your water supply off and contact customer service."; 16454 "Recalibrate your shower outlets."; 1619 "Please check your valve connection…"; 1645, 1646, 1648, 16416, 16418, 16419, 16432, 16435–16437, 16440, 16445–16447, 16480, 16490, 164110–164112, 164115–164117 "Please contact customer service." **Plumbing**: 1616 shower outlets on the wrong ports; 1617 flow regulator. **Interface**: 1618 interface not detected. |
| `c` | Cycle, Rapid and Deep forced off and their buttons **disabled**; only Standard stays usable (`uf0/c1.java:3390-3406`, `:6939-6942`) | **Ozone Generator** 16141 (needs replacing), 16142. **Primary Pump** 1656 (power cycle), 1657, 1658 (replace). **Booster Pump** 1659, 16510, 16511. **Drain Motor** 1651, 1653, 1655. **Thermistor** 1663–1666. **Solenoid** 16512. **Float** 1671. **Filter** 1679 "Please clean the shower base filter…" |
| `b` | Rapid and Deep forced off and disabled (`uf0/c1.java:3408-3418`, `:6945-6948`) | **Ozone Generator** 16143 (needs replacing) |
| none | banner only | **Controller Memory** 1615 "Please power cycle your controller…" |

A card shows "‹component› error ‹code› detected. Click to see details." when the
code is at least 200 and the impact covers it (`uf0/c1.java:6917-6936`). While a
covering fault is active, the Rapid, Deep and Cycle buttons open the fault page
instead of sending (`uf0/c1.java:517-526`, `:890-899`, `:1732`). The fault page
(`EvoCycleSettingsActivity` with `from = "KeyMqttError"`) shows the component and
instruction (`rp0/g.java:801,1049`).

---

## 6. Usage, water saved, dashboard and firmware

### 6.1 Units, ranges and timing

- **Volumes are litres**, as for the other products. Imperial display multiplies
  by 0.264172 (`qf0/b.java:178-196` `J()`, `K()`); `cx0/a.java:311-322` `c()` formats
  "… L" or "… gal" to two decimals.
- **Durations are seconds**: the usage screen divides by 60 into "N min" or
  "H hr M min" (`cx0/a.java:301-309` `b()`).
- **Temperatures are tenths of °C** (§3.5). **Flow is a percent.**
- Water Savings tabs (`sg0/w.java:1249-1270`): Week runs Sunday to Saturday
  (`qf0/b.java:72-81` `A()`), Month the 1st to the last day (`:394-402` `l()`), Year
  Jan 1 to Dec 31 (`:110-118` `C()`). `Interval` is `Month` on Year, else `Day`.
  Unlike the shared usage screen (platform.md §6), this screen does not clamp the
  end date to today.
- `intervalKey` is `yyyy-MM-dd` for `Day` and `yyyy-MM` for `Month`; the Water Usage
  screen zero-fills missing buckets by that key (`rg0/t0.java` `z0()`).
- Nothing is polled. Each screen reads when it opens or changes tab.

### 6.2 Water Usage screen (R5)

This is the screen shared by all products (`rg0/e0.java`, `rg0/t0.java`;
EvoCycle branch at `rg0/t0.java:267`). For EvoCycle:

- the bars are each bucket's `waterUsage` (volume view) or `duration` (time view)
  (`rg0/e0.java:287-310,420-460`);
- the average and maximum labels are `avgTotalWaterConsumption` /
  `maxTotalWaterConsumption` and `avgDuration` / `maxDuration`
  (`rg0/e0.java:1405-1447`).

So the bars show `waterUsage` while the labels show total consumption. Other
products are mapped into the same model with their volume in `waterUsage`
(`rg0/t0.java:1513-1528`). How `waterUsage`, `waterSave` and
`totalWaterConsumption` relate is not found in the decompile; total = usage +
saved is the natural reading (**inferred**; **live check**).

### 6.3 Water Savings screen (R1, R3, R4)

`EvoCycleWaterSavingView.kt` (`sg0/w.java` with `sg0/z.java`). On open it reads
R1, A1, R3 and R4 (`sg0/w.java:1050-1061`).

| Element | Source | Notes |
|---|---|---|
| "Total Water Savings", "Savings to date:" | R1 `recycledWaterUsed` (L) | gallons = round(L × 0.264172) |
| The greeting line | `qf0/b.java:436-448` `p()` | Up to 4 gal / 18 L: "Hey %s, view your water savings progress below." Above that: "Hey %s, your total water savings equals filling a %s - great job conserving!", naming the largest of Bucket 5 gal / 19 L, Barrel 50 / 189, Large Aquarium 75 / 284, Hot Tub 500 / 1893, Koi Pond 1000 / 3785, Small Pool 2000 / 7571 and Tanker Truck 10000 / 37854 that the total reaches (`qf0/b.java:120-144` `D()`, `E()`; strings.xml:547-548) |
| "Weekly Consumption" donut | R3 `currentWeekUsageData` | `waterSaved` is **"Cycled Water"** and `waterUsage` is **"Fresh Water"** (`lf0/d.java`, `DeterminedWeeklyConsumptionModel(cycledWaterValue, freshWaterValue, …)`; `qf0/b.java:301-335` `f()`); the centre shows saved ÷ (saved + usage) |
| "Saved Water" bars | R4 `waterUsageData[].waterSaved` by `intervalKey` | string → float; gallons when imperial (`sg0/w.java:295-336`) |
| "Standard vs Cycle %" bars | R4 `reCycleWaterPercentage`, `standardWaterPercentage` | two values per bucket (`sg0/w.java:408-417`) |
| "Water Savings Goal" | A1 `waterSavingGoal.goalPercentage`, shown when `isEnableWaterSave` | "Set a monthly water savings goal and show it in the dashboard." (strings.xml:2942) |

**How "water saved" is computed: the app does not compute it.** Every saved
figure comes from the server: lifetime `recycledWaterUsed` (R1), weekly
`waterSaved` (R3), per-bucket `waterSaved` (R4) and `waterSave` (R5). The app
equates "saved" with "cycled" (recirculated) water and "usage" with fresh water.
Two of its own derived numbers are wrong, so compute from the raw fields:

- the donut's percentage divides **integers** before multiplying by 100
  (`qf0/b.java:320-325`): it reads 0 % unless no fresh water was used, and the code
  divides by zero when both values are 0;
- the goal marker is `goalPercentage / 100`, also integer division
  (`sg0/w.java:1235-1241` `q()`), so it is 0 for every goal below 100 %.

### 6.4 Water-savings goal

It is stored on the account's device record, not on the shower (§1, §3.8). The
range is 10–100 % (slider, steps 10). The editing screen labels the value "Cycle
Mode Usage" (strings.xml:1698; `qg0/n.java:2290`), which suggests a monthly target
for the cycle-mode share (**inferred**).

### 6.5 Firmware

1. **Check, F1.** `GET /platform/api/v1/firmware/evo/{id}?releasetarget=Public`
   returns `FirmwareUpdateResponseModel` (`FirmwareUpdateResponseModel.java:14-38`):
   `{currentFirmware, firmware, firmwareUpdateAvailable (boolean), mandatoryUpdate, otaStatus, skip, estimatedTimeForOTA, fileSizeInMb (String), url}`.
   The control screen checks after a state read that shows no fault
   (`uf0/c1.java:3217-3228` → `:385-397` → `uf0/c.java:762` `J()`). It opens the
   update screen when `otaStatus` is `Installing`, `Started` or `Downloading`, or
   when `firmwareUpdateAvailable` is true and `skip` is false
   (`uf0/c1.java:180-195`).
2. **Pre-checks** (`op0/j.java:874-893`): R1 `Disconnected` → offline page; anything
   running → refused (§5.2); no phone network → error.
3. **Install.** `POST /platform/api/v1/firmware/evo/{id}` with
   `FirmwareUpdateRequestModel` `{releaseTarget: "Public", tenantId}`
   (`np0/q.java:1700-1706` `W()`). **`firmwareNumber` is never set**, and the
   Retrofit Gson converter is the default one (`na0/b.java:147`,
   `GsonConverterFactory.create()`), which drops nulls. So, unlike platform.md §7,
   the body names no target version (**live check**).
4. **Progress.** The app re-reads R2 every **10 s** (2 s during provisioning) while
   `firmwareUpdate.progress` is `Started`, `Downloading` or `Installing`. It stops
   on `Completed` (success) or `Failed`; `NotStarted` also stops the poll
   (`np0/q.java:1562-1605` `C0()`; values `mp0/d.java:19-75`). No timeout was found;
   the screen says "Estimated Time: Upto 2 hours" (strings.xml:2378,
   `op0/j.java:1177`).
5. **Skip** uses the shared `PATCH /platform/api/v1/firmware/skip/{id}` with
   `{skip: true, sku, firmwareVersion}` (`np0/q.java:1686` `V()`).

**A discrepancy.** The shared OTA view model maps `EVO` to firmware type **`hub`**
(`ui/ota/f.java:158-160`: `W0(id, "hub")` → `/platform/api/v1/firmware/hub/{id}?releasetarget=Public`),
and its result-read and install switches have no `EVO` case. EvoCycle's own screens
use `evo`. The `hub` branch looks unreachable for EvoCycle (**inferred**).

### 6.6 Push notifications

These come through FCM, which an integration does not receive.
`SplashActivity.java:451-468` routes `OTANotification` (for EVO) to the firmware
flow, and these types to the control screen: `RapidCleanNotification` and
`DeepCleanNotification` (open that clean's card, `uf0/c1.java:2006-2010`),
`FilterCleanNotification` (opens the filter-cleaning steps, `:2011-2013`),
`ShowerReadyNotification` and `TempNotAchievedNotification` (no special handling
found). The names suggest the cloud tracks warm-up completion and failure to reach
temperature (**inferred**). No REST or MQTT field that the app reads carries
either.

---

## 7. The app's own rules

### 7.1 Confirmations and the clean lifecycle

- **Shower start and stop: no confirmation.** One tap sends C1 (§3.2).
- **Clean start: confirmation.** "Please stay out of shower!" / "This process runs
  ozonated water to clean the shower system.", with Start and Cancel
  (`uf0/c1.java:4630-4633`; strings.xml:1119, 3000). Start runs `t()`
  (`:6208-6240`). **Clean stop: immediate.**
- **Cancelling.** MQTT `cleanOn 2` shows "Canceling Rapid Clean Cycle" or
  "Canceling Deep Clean Cycle". When the clean then ends, the app shows "Rapid Clean
  Cycle Canceled" with "Please stay out of the shower for 5 minutes to allow any
  residual cleaning agents to safely dissipate.", or the Deep version with
  **15 minutes** (`uf0/c1.java:2872-2932`; strings.xml:1586-1589, 1704, 2586).
- A 919 refusal tells the user to wait while the ozone dissipates (§5.1).

### 7.2 Mode interlocks: which buttons are enabled

From `S()` (`BottomSheetScreen`, `uf0/c1.java:3815`; enable flags at `:4033` and
`:4517`, the button at `:7075-7082`), confirmed with the `simple`-mode pass:

- Nothing running: all four Start buttons are enabled.
- **A clean running: only that clean's button is enabled, to stop it.** Both
  shower buttons and the other clean are disabled.
- **A shower running (either mode): both shower buttons are enabled, both clean
  buttons disabled.** The other mode's button still reads Start, and pressing it
  sends `showerOn: 1` with the other `showerMode`. That is a mode switch; what the
  shower does with it is a **live check**.
- Each button reads Start or Stop from its own flag (`uf0/c1.java:4520-4551`, `D0()`).
- Fault impacts disable more (§5.3).
- Home-screen tile: disabled while `isCleanOn` (`kr0/c.java:107-110`).

All of this is client-side. Whether the cloud or the shower also refuses is not
found in the decompile (**live check**).

### 7.3 Online and network

- R1 `connectionState == "Disconnected"` shows the offline page instead of the
  controls (`uf0/c1.java:3180-3188`).
- `fa0.o.t()` treats every flag as off while disconnected, so a toggle sent then
  would be a start (`1`). The offline page prevents that in practice (**inferred**).
- Every command first checks the phone's own network.

### 7.4 Writes refused while running

Settings (C4, C5, C6) and the goal re-read R1 at Save and refuse when `G()` is
true (§5.2). So do product removal and firmware install. C1, C2 and C3 have no
such gate beyond the interlocks of §7.2.

### 7.5 Remove product

"Remove %1$s?" / "Do you want to remove this product? This may take up to 30
seconds to reflect on the control." (strings.xml:2612, 2607) → refused while
running → C7 → the `customer-device` PATCH with `Delete` (`eg0/a2.java:1861-1871`;
`eg0/d2.java` `L()`, then `M()`).

### 7.6 What this means for an integration (**inferred**)

- Treat C1, C2 and C3 like the valve's water commands: user-initiated only, never
  retried automatically. Refuse when the shower is offline, when a fault covers the
  mode (impact `a`; `c` also for Cycle and both cleans; `b` for the cleans), or
  when §7.2 would disable the button.
- Never send `temperature` or `flow` as 0 just because the state block is missing,
  as the app would. Use R2's defaults, or refuse.
- Starting a clean should require an explicit confirmation that nobody is in the
  shower. After a clean ends or is cancelled, expose the 5-minute (Rapid) or
  15-minute (Deep) stay-out window.
- Confirm through MQTT or a later R1 read; a 201 is not "applied" (platform.md §2.1).

---

## 8. What the screens show

- **Home tile**: "Evocycle", with tiles from `evoPresets` showing on/off; greyed out
  while cleaning (§2.8).
- **Control screen** (`EvoCycleControlActivity`, `uf0/c1.java`): four cards,
  Standard Mode, Cycle Mode, Rapid Clean Cycle and Deep Clean Cycle (`B0()` at
  `uf0/c1.java:2577-2740`; labels `qf0/b.java:337-560`).
  - Standard and Cycle: "Temperature" from `‹mode›.temperature` (°C =
    `int(t / 10)`, °F through `d()`), the flow value, the defaults note (§3.2), and
    the mode's description.
  - Rapid and Deep: "Last clean cycle" from `lastCleanDate` (`MMM dd, yyyy`, "-" when
    missing), "Duration" "5 mins" or "15 mins", and the ozonated-water description.
  - Each card has its Start/Stop button and the fault banner. A "Water Savings"
    entry sits below (`uf0/c1.java:3681`).
- **Water Savings** (§6.3) and **Water Usage** (§6.2).
- **Settings** (`eg0/a2.java`): Default Settings (Shower Mode, Temperature, Max
  Temperature, Valve Flow as "N%", the Shower Warmup Mode toggle), Clean Cycle
  Reminder (three toggles), Shower Filter Cleaning Steps, Water Savings Goal, Water
  Usage, Digital Interface Settings (Panel Language, Panel Units, Haptic Feedback),
  Diagnostics (Error Log, Clear), About (Interfaces, Controller, Valve and Gateway
  firmware), firmware, and Remove product.
- **Onboarding**: Welcome to EvoCycle, Standard Mode, Cycle Mode, Ozone Cleaning
  Mode (`qf0/b.java:208-232` `M()`).

---

## 9. What `kohler_konnect` does with it

Readings and controls are on, the controls flagged `untested`
(`product_base.UntestedControl`), but for the two ozone-clean starts, which are off until
an owner turns them on: the app asks before one (§7.1), and a Home Assistant button can't.
Code: `konnect/evocycle.py` (protocol) and `evocycle/` (entities).

**Reads.** R1 and R2 at setup, after every MQTT connect and every five minutes, every
fifteen once the stream has carried a message about the shower; F1 twice a day; R3, R4 and
R5 every half hour; the account's record of the shower, for its goal, hourly. A failed R2 keeps the last
one. A message's `data` blocks are merged as `F1()` merges them (§4.2), a null `shower` or
`clean` block in R1 taken as empty; a clean that ends without a `lastCleanDate` is dated
then, as the app dates it; `INF` messages are dropped. After a command nothing is read at
once: the shower is read again only if no message about it comes within 8 s.

| Entity | Kind | Source or command |
|---|---|---|
| Activity | sensor (enum: idle, standard, cycle, rapid_clean, deep_clean, cancelling) | R1 flags; MQTT `shower`, `clean` (`cleanOn 2` is cancelling) |
| Shower, Ozone clean | binary sensors (running) | either mode running; either clean running or cancelling |
| Problem | binary sensor, with `error_code` | As `F1()` reads them (§4.2): a `product.errorCode` of 0 clears the fault; otherwise the valve's code, else the product's, if the app's table (§5.3) has it; a code it lacks leaves the fault as it was. From R1 `deviceErrorState`, only a code in the table |
| Last Rapid Clean, Last Deep Clean | sensors (timestamp) | `lastCleanDate`, epoch seconds |
| Water saved | sensor (water, total increasing) | R1 `recycledWaterUsed`, in the account's unit |
| Cloud connection | binary sensor | `connectionState`; only `Disconnected` is off |
| Firmware status | update | F1; installing stays with the app |
| Shower | select, **untested**: off, standard, cycle | C1 with that mode's state temperature and flow, else the stored defaults; refused rather than sent with 0. The mode already running sends nothing |
| Start Rapid Clean, Start Deep Clean, Stop clean | buttons, **untested**; the starts off by default | C3 |
| Default shower mode, temperature, both maxima, flow; Warmup | select, numbers and a switch, **untested** | C4, all six settings from a fresh R2. The default temperature stops at the lower of the two maxima, and lowering the Standard maximum under it lowers it too (§3.5). Flow in steps of 4, temperatures of 0.5 °C, the nearest taken |
| Rapid, Deep and Filter clean reminders | switches, **untested** | C5, all three at once |
| Panel language, Panel units, Haptic feedback | two selects and a switch, **untested** | C6, all of them from a fresh R2's `uiConfigurations[0]`; units go out as both `temperatureUnits` and `flowUnits` |
| Water savings goal, Water savings target | switch and number (10–100 %, steps of 10), **untested** | The account's record of the shower (§3.8), read fresh before each write so the name it carries is current. Refused while the shower runs; the target is unavailable while the goal is off, as the app hides its slider; a null field is left out |
| Water used, Duration used: today, this week, this month, this year | sensors | R5, the fresh water (`waterUsage`) the app's Water Usage screen charts, sent with `TimeZone=UTC` |
| Cycled water, Fresh water, Cycled share: this week | sensors | R3 `currentWeekUsageData` for today, the Water Savings donut (§6.3). The share is computed from the litres, not as the app's integer division does |
| Water saved this month, this year; Cycle mode share this month | sensors | R4 for January to December by `Month`, the screen's Year tab: this month's `waterSaved` and `reCycleWaterPercentage`, and the year's sum. A failed read keeps the last |

Guards, beyond the app's (§7.2, §7.6): an ozone clean is refused while the shower or
another clean runs, so it never starts with someone showering; a shower is refused while a
clean runs or is being cancelled; nothing starts that a reported fault stops in the app,
by its impact class (§5.3: impact `a` stops everything, `c` all but Standard, `b` the
cleans, 1615 nothing). Settings are refused while anything runs, as a fresh R1 says, as
the app reads it before each save (§7.4); they go one at a time, and a value just written
is laid over R2 until R2 shows it, so a second change doesn't write the first one back. A
stop the state doesn't call for reads R1 again first, in case a message was missed, and
is sent if R1 says that part runs. Refusal 919 reads as the ozone error it is, with the
app's advice to stay out while the ozone clears. The panel asks before an ozone clean, as
the app's "Please stay out of shower!" sheet does; Home Assistant's own buttons can't ask.

Left out: the Water Savings screen's week and month tabs (the current month and week are
read; earlier periods aren't), C2 (the home-screen variant), C7 and removal, firmware
install, and diagnostics Clear.

## 10. Open questions (live check)

Ranked by how much they block an implementation.

1. **A captured R1 and R2.** Confirm tenths of °C, flow in percent, the
   `evoAboutModel` key, two `uiConfigurations`, and whether
   `‹mode›.temperature`/`flow` in R1 are the defaults or live values.
2. **The MQTT envelope.** Lowercase `deviceid`? `type` values besides `INF`? Which
   blocks arrive and when? Does a shower started at the panel (Simple Start) send
   `shower` messages? Does `product.errorCode 0` arrive routinely, clearing faults?
3. **C1.** Must `showerOn 0` carry the running `showerMode`? Does `showerOn 1` with
   the other mode switch modes? Does the cloud refuse a shower while a clean runs,
   and with which code?
4. **C1 against C2.** Any difference in effect, and in refusals
   (`EvoCycleOnOffResponse.statusCode` and `error`)?
5. **C3.** Is `cleanOn 0` a cancel that passes through `cleanOn 2`? How long is the
   cancelling phase? Is a running shower refused server-side?
6. **`TimeZone`.** What the server does with `UTC` against the other abbreviations,
   and whether ISO dates and `DAY`/`MONTH` work as for the other products.
7. **Diagnostics.** Does `gcs-diagnostics/{evoId}` return EvoCycle entries? What does
   `evo-diagnostics` return?
8. **Firmware install without `firmwareNumber`.** Is it accepted? Which `otaStatus`
   and `progress` values appear?
9. **Units.** Are R3's integers litres, and `recycledWaterUsed` litres? Is
   `totalWaterConsumption` = `waterUsage` + `waterSave`?
10. **Faults.** Values of `deviceErrorState.affectedOperations`; the meaning of
    `actionRequired`; codes outside the table.
11. **Auth.** Do the EvoCycle commands accept an ROPC token, or only
    `B2C_1A_signin` (platform.md §1, which found this per product)?
12. **`evoPresets`.** How entries are created, their `state` values and their
    `temperature` scale.
13. **The device id shape**, and whether `uisetting` reaches only interface 0.
14. **Fields the app ignores** that could help: `warmupStatus`, `ready`,
    `rebootStatus`, `gateway.rssi`, `scheduleOTAConfiguration`, `lastCleanDuration`.
