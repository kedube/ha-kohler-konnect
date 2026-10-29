# Kohler Smart Fan: cloud protocol (app-side)

**Source:** Kohler Konnect for Android **3.0.6** (version code 260), decompiled
with jadx 1.5.6. Covers SKU `SFC`, which the app calls "Smart Fan" and, in its
setup flow, "Atmo Fan".

**Caveat.** Everything here comes from the app's side: what the 3.0.6 app sends,
what it expects back, and how it reacts. It does not describe what the fan or the
Kohler cloud actually does. Nobody on this project owns a Smart Fan, so **nothing
in this chapter is live-verified**; every fact is **app** or **inferred** in the
sense of the [README](README.md#confidence). Field names and request shapes are
exact, because they come from Gson `@SerializedName` models and Retrofit
annotations. Value sets and ranges come from app logic, and the server may accept
more or less than the app sends. Items marked **live check** need a real fan.
"Not found in the decompile" means just that; the behaviour may still exist
server-side.

Shared mechanics (auth, API base and APIM key, `customer-device`, mobile
registration, the IoT Hub MQTT transport and envelope, the response-body
`statusCode` table, the general firmware pattern, rename and remove) are in
[platform.md](platform.md). This file covers only what is Smart-Fan-specific.

**Finding things in the decompile.** Code paths are relative to jadx's `sources/`
root; `strings.xml:N`, `styles.xml:N`, `plurals.xml:N` and `layout/…:N` are under
jadx's `resources/res/` (values or layout). Obfuscated packages (`qm0/`, `la0/` …)
change between builds, so every citation also names a string, field or method to
grep for. Abbreviations used below:

| Short form | Full path under `sources/` | What it is |
|---|---|---|
| `SmartFanControlAPIService.java` | `com/kohler/hermoth/products/sfc/controls/data/service/` | The Retrofit interface holding **every** Smart Fan endpoint. None of them is in `ApiConstant.java` |
| `ProvisionAPIService.java` | `com/kohler/hermoth/products/sfc/provision/data/service/` | `setrtccontrol` and local setup calls |
| `model/…` | `com/kohler/hermoth/products/sfc/controls/data/model/` | Response models; `model/dto/` holds request bodies |
| `SmartFanControlActivity.java` | `…/products/sfc/controls/presentation/control/` | Product shell: first read, MQTT dispatch, error routing |
| `SmartFanControlHomeFragment.java` | `…/presentation/control/home/` | The main screen |
| `SmartFanControlCreateEditFavoriteFragment.java` | `…/presentation/favorite/createEditFavorite/` | Favorite editor |
| `Smart*SettingsFragment.java`, `SmartFan*Fragment.java` | `…/presentation/settings/<screen>/` | Settings screens |
| `PermissionFragment.java` | `…/products/sfc/provision/presentation/commonFlow/permission/` | Last setup step (clock sync) |
| `qm0/l0.java` | | Home view model: builds every run/stop command, maps MQTT to state |
| `pm0/y.java`, `pm0/s.java` | | Activity view models: configuration read, MQTT relay; shared UI state and error classification |
| `tm0/o.java` | | Favorite detail view model (a second set of command builders) |
| `sm0/o.java`, `rm0/g.java`, `rm0/s.java` | | Favorite editor view model and its pickers |
| `ym0/h`, `zm0/h`, `an0/h`, `bn0/i`, `cn0/l`, `dn0/h`, `fn0/a`, `gn0/h`, `en0/k`, `vm0/c` (`.java`) | | Settings view models (§3.4) |
| `kn0/*.java` → `jn0/*.java` → `la0/b.java` | | Use cases → repositories → the shared Retrofit wrapper |
| `ui/ota/f.java`, `ui/ota/c.java` | `com/kohler/hermoth/ui/ota/` | Shared firmware flow |

**Borrowed constants.** R8 merged identical strings from unrelated libraries, so
several Smart Fan values appear under foreign names. Resolve them before reading
the code:

| Appears as | Value |
|---|---|
| `StepType.ENABLE` / `StepType.DISABLE` (Luciq SDK) | `"ENABLE"` / `"DISABLE"` (`ai/luciq/library/model/StepType.java:25,27`) |
| `PublicApiId.MULTIPLE_ACCOUNT_PCA_ACQUIRE_TOKEN_SILENT_WITH_PARAMETERS` | `"30"` (the default fan-timer duration) |
| `IOTAccountRequestUtils.TYPE_ADD_DEVICE_TYPD_ID` | `"LIGHT"` |
| `SessionParameter.DURATION` | `"duration"` (field name in `Fan`) |
| `DeviceCodeFlowAuthResultCommand.DEVICE_ID_CLAIM` | `"deviceid"` (path parameter and MQTT key) |
| `WebAppsGetTokenSubOperationRequest.FIELD_CORRELATION_ID` | `"correlationId"` |
| `SerializedNames.OPERATION` | `"operation"` |
| `PasskeyReplyChannel.DATA_KEY`, `NativeAuthConstants.GrantType.ATTRIBUTES` | `"data"`, `"attributes"` |
| `JWKSourceBuilder.DEFAULT_CACHE_TIME_TO_LIVE` | `300000` (the SFC firmware timeout, §6) |
| `ProgressIndicatorKt.SecondLineTailDelay` (material3) | `900` (`DEVICE_OFFLINE_900` in `sy0/a.java:10`) |
| `CacheKeyValueDelegate.CACHE_VALUE_SEPARATOR` | `"-"` |

---

## 1. Device type (SKU) and how the app recognizes it

| Item | Value | Evidence |
|---|---|---|
| SKU | `SFC` — the only Smart Fan SKU in the decompile | Product tile `rt0/o.java:30` (`productModel.x("SFC")`, name `text_smart_fan`); product factory `zn0/i.java:486` and its `switch` at `:629`; firmware type `ui/ota/f.java:183` |
| Dashboard → control screen | `sku.equals("SFC")` opens `SmartFanControlActivity` | `fr0/f.java:1162` → `F3()` at `:1189` |
| Names | Tile and About model name "Smart Fan" (`text_smart_fan`, strings.xml:2779; About hard-codes the literal `"Smart Fan"`, `wm0/i.java:1253`); setup's suggested product name "Atmo Fan" (`text_my_smart_fan`, strings.xml:2304) | |
| Variant flag | **`isFanWithLight`** (Boolean) on the device entry in `customer-device` → `customerHome[].devices[]` | `com/utils/network/retrofit/proxy/device/model/DevicesModel.java:33` (`@SerializedName("isFanWithLight")`) |

There are two variants, and **the user declares which one during setup**; the app
does not detect it from the hardware. The setup screen offers "Fan with Light" and
"Fan without Light" (`text_fan_with_light` / `text_fan_without_light`,
strings.xml:1905-1906; `xn0/d.java:70` `F()`), passes the choice as `fan_variant`
(`SelectVariantFragment.java:373`) and stores it with the product
(`un0/g.java:553`, `setFanWithLight(this.fanVariant)`; `AddYourProductRequest.java:32`).
Whether the cloud cross-checks it is not found in the decompile.

The flag decides:
- the dashboard icon (`fr0/a.java:401`);
- whether Settings shows the "Main Light" defaults (`en0/k.java:541`);
- which favorite types can be created (§7);
- what counts as "running" for the Stop-all button (`qm0/l0.java:3854` `r0()`);
- the favorites limit (§7).

The home screen also **overwrites** `sfc-state`'s own `state.isFanWithLight` with the
account flag after every favorites read (`SmartFanControlHomeFragment.java:865`),
so the account record wins.

There is **no night-light variant in 3.0.6**. Settings' night-light flag is
hard-wired `FALSE` (`en0/k.java:542`, `isFanWithNightLights`), and the home
screen's `isNightLightFeatureEnabled` is declared but never assigned
(`SmartFanControlHomeFragment.java:121`). The night-light API is fully modelled
(state, MQTT, commands, settings), but the screens hide it (§8).

**Device id.** About shows the serial number as the part of `deviceId` after the
first `-` (`wm0/i.java:1273`, `CACHE_VALUE_SEPARATOR`). So ids look like
`<prefix>-<serial>` (inferred; the setup SSID starts `sfc-`,
`text_provision_step_two_sfc`, strings.xml:2573). As platform.md §3 says, branch on
`sku`, never on the id's shape.

**Setup (brief, not needed by an integration).** The fan's soft-AP is `sfc-…`, and
the device answers at `10.123.45.1` (`ApiConstant.java:264`,
`SFC_DEVICE_SECURE_CONTENT_SSID`) with `http://{deviceName}/api/v1/device/key` and
`/ping` (`ProvisionAPIService.java:26-30`). Setup mode is "Fan" + "+" on the
remote (strings.xml:2780); authorizing is "-" + "+" (strings.xml:3335). After the
cloud claim the app syncs the clock (§3.5, §4).

---

## 2. Reads

### 2.1 Endpoint summary (reads and writes)

Every path is hard-coded with `v1` in `SmartFanControlAPIService.java`; there is no
`{version}` substitution. The Retrofit instance is
`https://api-kohler-us.kohler.io/` with a default `GsonConverterFactory.create()`
(`na0/b.java:147`; service created at `:157-161`). A default `Gson` does not
serialize nulls, so **null fields and null map values are left out of every
request body** (Gson library behaviour). The call chain is screen view model →
`kn0/*` use case → `jn0/*` repository → `la0/b.java` wrapper.

| # | Method | Path | Retrofit method (`SmartFanControlAPIService.java:N`), `la0/b.java` wrapper | Body → response |
|---|---|---|---|---|
| R1 | GET | `/devices/api/v1/device-management/sfc-state/{deviceid}` | `getSmartFanControlState` (`:41`), `h2()` `:13072` | – → `SmartFanControlStateResponse` |
| R2 | GET | `/devices/api/v1/device-management/sfc-configuration/{deviceid}` | `getSmartFanConfiguration` (`:35`), `f2()` `:13040`. The firmware flow reads the same URL through the generic `GET_DEVICE_CONFIGURATIONS_V` with `{deviceType}`=`sfc` (`ApiConstant.java:145`; `z1()` `:13412`; `ui/ota/f.java:242-243`) | – → `SmartFanConfigurationResponse` |
| R3 | GET | `/devices/api/v1/device-management/sfc-experience/{deviceid}` | `getSmartFanControlExperiencesList` (`:38`), `g2()` `:13056` | – → `SmartFanControlFavoriteResponse` |
| R4 | GET | `/devices/api/v1/device-management/sfc-experience/{deviceid}/load` | `loadSmartFanControlExperiences` (`:44`), `z2()` `:13416` | – → `Object` (ignored) |
| R5 | GET | `/devices/api/v1/device-management/customer-experience/{tenantid}` (shared) | see platform.md §3 | – → `AllPresetResponse.sfcExperiences` |
| C1 | POST | `/platform/api/v1/commands/sfc/fancontrol` | `postSmartFanControl` (`:47`), `Y2()` `:12916` | map → `SmartFanControlResponse` |
| C2 | POST | `/platform/api/v1/commands/sfc/fanfavoritecontrol` | `postSmartFanFavoriteControl` (`:56`), `b3()` `:12976` | map → same |
| C3 | POST | `/platform/api/v1/commands/sfc/lightcontrol` | `postSmartFanLightControl` (`:59`), `c3()` `:12996` | map → same |
| C4 | POST | `/platform/api/v1/commands/sfc/lightfavoritecontrol` | `postSmartFanLightFavoriteControl` (`:62`), `d3()` `:13012` | map → same |
| C5 | POST | `/platform/api/v1/commands/sfc/nightlight` | `postSmartFanNightLightControl` (`:65`), `e3()` `:13028` | map → same |
| C6 | POST | `/platform/api/v1/commands/sfc/nightlightfavorite` | `postSmartFanNightLightFavoriteControl` (`:68`), `f3()` `:13044` | map → same |
| C7 | POST | `/platform/api/v1/commands/sfc/stopallcontrol` | `postSmartFanStopAllControl` (`:71`), `g3()` `:13060` | map → same |
| C8 | POST | `/platform/api/v1/commands/sfc/updatedefaultsettings` | `postSmartFanDefaultSettings` (`:53`), `a3()` `:12956` | `SmartFanControlDefaultSettingsRequest` → same |
| C9 | POST | `/platform/api/v1/commands/sfc/setrtccontrol` | `postSfcRtcValue` (`ProvisionAPIService.java:35`), `X2()` `:12896` | `SmartFanRtcValueRequest` → `BaseResponseModel` |
| C10 | POST | `/platform/api/v1/commands/sfc/factoryreset` | `callSfcFactoryResetApi` (`:29`), `f0()` `:13032` | map → `SmartFanControlResponse` |
| P1 | PATCH | `/devices/api/v1/device-management/sfc-experience/{deviceid}` | `postSmartFanControlExperiences` (`:50`), `Z2()` `:12936` | `SmartFanControlCreateFavoriteRequest` → `SmartFanControlFavoriteResponse` |
| A1 | PATCH | `/devices/api/v1/device-management/customer-device/{tenantid}/{homeid}/device` | rename `callUpdateDeviceConfigurationApi` (`:32`, `h0()` `:13064`); remove `callRemoveProductApi` (`:26`, `W()` `:12864`) | map → `CustomerHomeModel` / `SmartFanControlResponse` (shared, platform.md §3) |
| F1 | GET | `/platform/api/v1/firmware/sfc/{deviceid}` (no `releasetarget`) | shared `getFirmwareState` (`PlatformApiCall.java:284`, `ApiConstant.java:114`), `I1()` `la0/b.java:12592` | – → `FaucetFirmwareUpdateResponseModel` (§6) |
| F2 | POST | `/platform/api/v1/firmware/sfc/{deviceid}` | shared `postFirmwareUpdate` (`PlatformApiCall.java:308`), `V2()` `la0/b.java:12856` | shared body (§6) |

`{deviceid}` resolves through `DEVICE_ID_CLAIM` = `"deviceid"`.

### 2.2 R1 `sfc-state`

Models: `model/SmartFanControlStateResponse.java:14-54` and `model/State.java:14-60`.

| Field | Type | App usage |
|---|---|---|
| `connectionState` | String | `"Connected"` → the home screen; `"Disconnected"` → the Offline screen; **any other value, including a missing one, does neither** and the screen stays on its spinner (`SmartFanControlActivity.java:471-478` `F3()`; the same two-branch test gates every save, e.g. `SmartFanAutomationFragment.java:743` `p3()`, and the home re-read, `SmartFanControlHomeFragment.java:1172` `k3()`). The firmware pre-check tests `"Disconnected"` (`ui/ota/f.java:530-535`). |
| `deviceId`, `tenantId`, `sku` | String | **The home screen builds every command's identity from these** (`qm0/l0.java:2812` `F()`; the state arrives as `state_model`, `:2733`). |
| `id`, `ioTHub`, `lastUpdatedSource`, `deletedTenantId`, `deviceConnectionEventSequenceNumber` | String | Not used. |
| `createdTimestamp`, `lastConnected`, `lastOTANotificationSentTime`, `updatedTimestamp` | **Integer** (32-bit) | Not used. The type implies epoch **seconds**: an epoch-ms value would not parse into an `Integer` (inferred). |
| `state` | `State` | Below. |

`state` (all String unless noted):

| Field | App usage |
|---|---|
| `fanStatus` | `"ON"` means the fan runs (`model/mapper/MapperExtentionKt.java:23` `isFanRunningState`, exact match; `qm0/l0.java:3854` `r0()` ignores case). No other value is tested; `"OFF"` is inferred. |
| `fanSpeed` | Not displayed. The app compares it with fan-favorite `speed` (`LOW`/`MEDIUM`/`HIGH`) to guess the running favorite (`qm0/l0.java:3944` `w0()`), so the same vocabulary is inferred. |
| `lightStatus` | `"ON"` = main light on (`MapperExtentionKt.java:27`). |
| `lightColor` | `"None"` is explicitly skipped (`qm0/l0.java:2879` `A1()`, `:4036` `z1()`); otherwise compared with favorite colours `WARM`, `WARMWHITE`, `COOL`, `COOLWHITE`, `DAYLIGHT` (`E0()` at `:2961`). Same vocabulary inferred. |
| `lightBrightnessLevel` | `"0"` is skipped; otherwise compared with favorite brightness (`"10"`…`"100"`). |
| `nightLightStatus` | `"ON"` = night light on (`MapperExtentionKt.java:31`). |
| `nightLightBrightnessLevel` | `"0"` is skipped. |
| `runningFanFavorite` | Id of the running fan favorite: `"DEFAULT-FAN"` or a user favorite's id. Drives the main Fan button and tile selection (`qm0/l0.java:3330` `a1()`; `SmartFanControlHomeFragment.java:1734` `W3()`). |
| `runningLightFavorite` | Id of the running light **or night-light** favorite: `"DEFAULT-LIGHT"`, `"DEFAULT-NIGHTLIGHT"` or a user id. One slot covers both lights, which suggests the main light and night light are mutually exclusive (inferred, **live check**). |
| `isFanWithLight` | Boolean. Overwritten by the account flag (§1). |
| `temperature`, `humidity`, `co2value`, `vocvalue` | Environmental readings. **Never displayed**; only copied in from MQTT (`qm0/l0.java:3541-3544`). Units and formats are not found in the decompile. |
| `errorState` (Boolean), `favoriteId` | Never read. |

### 2.3 R2 `sfc-configuration`

`model/SmartFanConfigurationResponse.java:15-31`: `id`, `deviceId`, `sku`,
`tenantId` (String), `applicationSource` (Object), `configuration`
(`model/Configuration.java:15-52`):

| Field | Type | Notes |
|---|---|---|
| `sfcSettings` | `SfcSettings` | The stored defaults, schedules and automation. Table below. |
| `about` | `About` | Typed parts: `firmware` (`Firmware`: `version`, `progress` String), `gateway` (`Gateway`: `firmware`, `installedDate`, `model`, `name`, `serialNo`, `status` String), `sfc` (`Sfc`: `firmware`, `model`, `serialNo`, `status` String). Everything else in `About`, `Gateway` and `Sfc` is declared `Object` (`model/About.java:18-145`, `model/Gateway.java:19-107`, `model/Sfc.java:15-46`). |
| `firmwareUpdate` | `FirmwareUpdate` | `firmwareType`, `interface`, `progress`, `status`, `version` (String); `isSingleFirmwareUpdate` (Boolean); `lastUpdated`, `startTimestamp` (Integer) (`model/FirmwareUpdate.java:15-37`). **Inside `configuration` for SFC**, unlike the faucet's top-level block; the firmware flow has an SFC-only branch for it (§6). |
| `lightModuleType`, `otaInProgress`, `parts`, `steamSettings`, `systemConfiguration`, `systemSettings`, `unit`, `userPreset`, `valve1Settings`, `valve2Settings` | Object | Declared, never read. |

The model has no `iot` block. platform.md §2.2 says configuration replies carry one;
if it appears, never log it.

`configuration.sfcSettings` (`model/SfcSettings.java:18-118`; all String unless
noted). The last column is the name the **write** uses (§3.4); four of them differ:

| Field | Values the app uses | Written by (screen) | Name in C8 |
|---|---|---|---|
| `fanSpeed` | `LOW`, `MEDIUM`, `HIGH` | Default Fan | `fanSpeed` |
| `fanTimer` | `ENABLE`, `DISABLE` | Default Fan | `fanTimer` |
| `fanTime` | `"10"`…`"60"`, step 10 (minutes) | Default Fan | `fanTime` |
| `lightColor` | `WARM`, `WARMWHITE`, `COOL`, `COOLWHITE`, `DAYLIGHT` | Default Main Light | **`color`** |
| `lightBrightnessLevel` | `"10"`…`"100"`, step 10 | Default Main Light | `lightBrightnessLevel` |
| `circadianMode` | `ENABLE`, `DISABLE` | Default Main Light | `circadianMode` |
| `nightLightBrightness` | `"10"`…`"100"` | Default Nightlight (hidden) | **`nightLightBrightnessLevel`** |
| `buzzerSetting` | `ENABLE`, `DISABLE` | Settings → Audible Feedback | `buzzerSetting` |
| `humidityThreshold` | `ENABLE`, `DISABLE` — **the on/off switch** of Humidity Sensitivity | Fan Automation | `humidityThreshold` |
| `humidity` | `LOW`, `MEDIUM`, `HIGH` — the sensitivity **level** | Fan Automation | `humidity` |
| `airQuality` | `ENABLE`, `DISABLE` (hidden; never changed by 3.0.6) | – | `airQuality` |
| `fsSchedule` | `ENABLE`, `DISABLE` — fan schedule on/off | Fan Schedule | `fsSchedule` |
| `fsSetting` | `CUSTOM`, `EVERYDAY`, `ONCE` | Fan Schedule | `fsSetting` |
| `fsDays` | Day bitmask as a decimal string; `"0"` unless `CUSTOM` (§3.4) | Fan Schedule | `fsDays` |
| `fsStartTimeHr`, `fsStartTimeMin`, `fsStartTimeAmPm` | 12-hour parts (§3.4) | Fan Schedule | same names |
| `fsEndTimeHr`, `fsEndTimeMin`, `fsEndTimeAmPm` | 12-hour parts | Fan Schedule | **`fsEndTimeHour`**, `fsEndTimeMin`, `fsEndTimeAmPm` |
| `fsFanSpeed` | `LOW`, `MEDIUM`, `HIGH` | Fan Schedule | `fsFanSpeed` |
| `nlSchedule` | `ENABLE`, `DISABLE` | Nightlight Schedule (hidden) | `nlSchedule` |
| `nlsSetting`, `nlsDays`, `nlsStartTimeHr`, `nlsStartTimeMin`, `nlsStartTimeAmPm`, `nlsEndTimeHr`, `nlsEndTimeMin`, `nlsEndTimeAmPm` | As for the fan schedule | Nightlight Schedule (hidden) | same names |
| `nlsLightBrightness` | `"10"`…`"100"` | Nightlight Schedule (hidden) | **`nlsBrightness`** |
| `fanScheduleCreated`, `nlScheduleCreated` | Boolean. Once true, Settings opens the schedule summary instead of the editor (`SmartFanControlSettingsFragment.java:1119-1123` `q3()`) | read-only | not sent |
| `tzTimeSpanDiff` | Object, never read | – | not sent |

### 2.4 R3 `sfc-experience` (favorites)

`model/SmartFanControlFavoriteResponse.java:15-37`: `id`, `deviceId`, `sku`,
`tenantId`, `applicationSource` (String), `createdTimestamp`, `updatedTimestamp`
(Integer), `experiences` (List of `Experience`).

`Experience` (`model/Experience.java:15-50`):

| Field | Type | Meaning |
|---|---|---|
| `id` | String | `"DEFAULT-FAN"`, `"DEFAULT-LIGHT"`, `"DEFAULT-NIGHTLIGHT"` for the built-ins (`tm0/o.java:1923` `g0()` switches on these), or a server-assigned id |
| `title` | String | Display name. The light tile special-cases the title `"Main Light"` (`ma0/j9.java:215`), so that is presumably the built-in light's title (inferred). |
| `favType` | String | `"FAN"`, `"LIGHT"`, `"NIGHTLIGHT"` — the upper-cased `text_sfc_fav_type_fan/light/nightlight` (strings.xml:2719-2722). The app compares case-insensitively. |
| `isDefault` | Boolean | `true` for the three built-ins; the editor writes `false` |
| `fan` | `{ duration, speed, timer }` (String) | FAN favorites (`model/Fan.java:16-23`) |
| `light` | `{ brightness, circadianEnabled, color }` (String) | LIGHT favorites (`model/Light.java:15-22`) |
| `nightlight` | `{ brightness }` (String) | NIGHTLIGHT favorites (`model/Nightlight.java:15-16`) |
| `state`, `logicalName` (String), `isSent` (Boolean), `lastUsedTimeStamp`, `timeStamp` (Integer) | | Never read |

`isSelected` (`Experience.java:26`) is a UI field and is not serialized.

**Order matters to the app.** It treats `experiences[0]` as the built-in fan: the
main Fan button sends `experiences[0].id` (`SmartFanControlHomeFragment.java:1972`
`y4()`), and the tile list is `experiences` minus its first element
(`SmartFanControlHomeFragment.java:1293` `p3()` and the redraw coroutine at
`:127`). Presumably the server always returns `DEFAULT-FAN` first (inferred;
**live check**).

### 2.5 R4 `sfc-experience/{deviceid}/load`

If the favorites read fails, or returns an empty `experiences` list, the home
screen calls R4 once (flag `isExperienceLoaded`), ignores its body, and reads R3
again (`qm0/l0.java:460` in coroutine `c`, `:530` in `d`; `h1()` at `:3405`;
`i1()` at `:3478`). It is a GET with an apparent side effect: presumably it seeds
the built-in favorites (inferred; **live check**).

### 2.6 R5 `customer-experience` (the account-wide Presets tab)

`AllPresetResponse.sfcExperiences` is a list of `PresetModel`
(`com/utils/network/retrofit/proxy/platform/model/preset/AllPresetResponse.java:47`),
and SFC entries with a `lastUsedTime` qualify for "Recent" (`:104`). For SFC the
tab uses `id`, `deviceId`, `sku`, `state`, `favType`, `isDefault` and `title`
(`PresetModel.java`). Starting and stopping from there is in §3.2.

### 2.7 When the app reads

| Moment | Reads | Evidence |
|---|---|---|
| Opening the product | R1, which picks home or Offline | `SmartFanControlActivity.java:891` → `i4()` `:799-801` → `qm0/l0.java:3216` `T0()` |
| Home screen resumes: on open, back from any sub-screen, after a favorite is saved | R1 then R3 (plus R4 and R3 again when needed) | `SmartFanControlHomeFragment.java:1927-1929` `onResume()` → `qm0/l0.java:3807` `p0(false)`; save path `SmartFanControlCreateEditFavoriteFragment.java:335` `Z2()` → `pm0/y.java:490` `N()` → home `Z2()` `:945-947` |
| No MQTT reply after a command | R1 only, at each retry tick (8 s, 16 s) | `SmartFanControlHomeFragment.java:1037` `d3()` → `p0(true)`; timer below |
| Before saving a setting or a favorite, or deleting a favorite | R1 (the gate in §7) | `vm0/c.java:683` `K()`; `sm0/o.java` `W()`; `qm0/l0.java:3216` `T0()` |
| Opening a settings screen | R2 | `vm0/c.java:673` `I()`, e.g. `SmartFanControlDefaultFanSettingsFragment.java:820` |
| About | F1, plus the R2 already held | `wm0/i.java:440`; `SmartFanAboutFragment.java:987-1001` |
| Dismissing a "Favorite does not exist" dialog | R1 | `SmartFanControlActivity.java:147-149` |

**There is no timer polling** on any Smart Fan screen; `postDelayed` appears only in
the setup flow. The settings screens' only delay is 1.5 s after a successful save,
before "Settings saved" and going back (`SmartFanControlDefaultFanSettingsFragment.java:98-112`);
they do not re-read.

**After a command** the home screen arms the shared MQTT reply timer with an 8 s
tick (`SmartFanControlHomeFragment.java:1277-1281`, `cVar.o(8000L)`; class
`yw0/c.java`, 2 retries at `:388`). Each tick without a reply re-reads R1. jadx
lost part of that branch of `p0()`, but the surviving bytecode note points at
`P0()`, the state read (`qm0/l0.java:225-330`, coroutine `a`). The re-read only
refreshes the stored state; the screen redraws on the next MQTT message or
favorites read. After the last tick it just hides the spinner
(`SmartFanControlHomeFragment.java:1110` `g3()`); no error is shown. **Any** MQTT
message for this device cancels the timer, whatever its code
(`SmartFanControlActivity.java:817` → `pm0/s.java:241` `h0()` → home
`h3()` `:1115`). The faucet uses the same class with 5 s ticks.

---

## 3. Commands

### 3.1 Body conventions and traps

1. **Every run, stop and favorite command body is a `Map<String, String>`**, so all
   values are strings, including brightness (`"50"`) and minutes (`"30"`)
   (`SmartFanControlAPIService.java:47-72`).
2. **Identity keys** are `deviceId`, `tenantId` and `sku`. The home screen takes them
   from the R1 response that opened it (`qm0/l0.java:2812` `F()`); the favorite
   detail screen takes them from R3, falling back to R1 (`tm0/o.java:1319-1321`);
   the Presets tab takes the preset's `deviceId` and `sku` and the account's
   tenant (`gr0/w.java:1243`). Send the device's own `sku`.
3. **The home screen reuses one mutable map for every command.** `F()` builds
   `{deviceId, tenantId, sku}` once (lazy `request`), and each command
   `putAll`s its own keys into the same map (`m1`, `q1`, `r1`, `s1`, `t1`, `v1` at
   `qm0/l0.java:3493-3939`). Keys from earlier commands therefore ride along: after
   a fan favorite starts, a later `lightcontrol` from the same screen also carries
   `speed`, `timer` and `time`, and `stopallcontrol` carries whatever the last
   command left. The server evidently ignores extra keys (inferred, since the app
   works). **Send only the keys listed below.**
4. **Nulls are dropped** (Gson default). For example, the detail screen's
   night-light-favorite body lists `circadianMode` and `color`, but both come from
   the (null) `light` block, so they are never sent (`tm0/o.java:1903` `e0()`).
5. **Response** `SmartFanControlResponse { correlationId (String), timestamp (Long) }`
   (`model/SmartFanControlResponse.java:14-18`). Success means "accepted for
   delivery", not "applied" (platform.md §2.1). Confirm through MQTT (§4) or R1.
6. The auth policy these commands need is not found in the decompile. The app uses
   its `B2C_1A_signin` token for everything (platform.md §1); **live check** whether
   an ROPC token is refused, as it is for the Anthem.

### 3.2 Run and stop

Every row's body is `{deviceId, tenantId, sku}` plus the keys shown. `status` is
`"ON"` or `"OFF"` (upper case).

| Action | Endpoint | Extra keys | Sent by |
|---|---|---|---|
| Built-in fan on/off | C1 `fancontrol` | `id` = `experiences[0].id` (`"DEFAULT-FAN"`), `status` | Main Fan button (`SmartFanControlHomeFragment.java:1972` `y4()` → `qm0/l0.java:3493` `m1()`); detail screen for `DEFAULT-FAN` (`tm0/o.java:1850` `Z()`); Presets tab for a default FAN (or unknown default type) (`gr0/w.java:1100,1117`) |
| Fan favorite on/off | C2 `fanfavoritecontrol` | `id`, `status`, `speed` (`LOW`/`MEDIUM`/`HIGH`), `timer` (`ENABLE`/`DISABLE`), **`time`** (the favorite's `fan.duration`, minutes) | Home tile (`qm0/l0.java:3849` `q1()`, chosen in `p1()` `:3811` for FAN with `isDefault` false); detail screen (`tm0/o.java:1857` `a0()`) |
| Built-in light on/off | C3 `lightcontrol` | `id` (`"DEFAULT-LIGHT"`), `status` | Home tile (`qm0/l0.java:3861` `r1()`, via `p1()` for LIGHT with `isDefault` true); detail (`tm0/o.java:1873` `b0()`) |
| Light favorite on/off | C4 `lightfavoritecontrol` | `id`, `status`, **`circadianMode`** (from `light.circadianEnabled`), `color`, **`brightnessLevel`** (from `light.brightness`) | Home tile (`qm0/l0.java:3871` `s1()`); detail (`tm0/o.java:1880` `c0()`) |
| Built-in night light on/off | C5 `nightlight` | `id` (`"DEFAULT-NIGHTLIGHT"`), `status` | `qm0/l0.java:3924` `t1()`; `tm0/o.java:1896` `d0()`. Unreachable from 3.0.6's screens (§8), except perhaps through the Presets tab |
| Night-light favorite on/off | C6 `nightlightfavorite` | `id`, `status`, `brightnessLevel` | `qm0/l0.java:3939` `v1()`; `tm0/o.java:1903` `e0()`. Unreachable, since such favorites can't be created (§7) |
| Stop everything | C7 `stopallcontrol` | none (stale keys aside, §3.1) | "Stop all" (`SmartFanControlHomeFragment.java:1455` `E3()` → `qm0/l0.java:3967` `w1()`) |

How `status` is chosen:
- a home tile sends the switch's new position (`SmartFanControlHomeFragment.java:1018` `c3()` → `p1(experience, isChecked)`);
- the detail screen sends `"OFF"` if the favorite is currently selected, otherwise `"ON"` (`tm0/o.java:1853` and siblings);
- the Presets tab sends `"OFF"` if the preset's `state` equals `"on"` (ignoring case), otherwise `"ON"` (`gr0/w.java:1940` `s0()`).

**No command sets the speed, colour or brightness of whatever is already running.**
The built-in fan and light run whatever is stored in `sfcSettings`. The detail
screen describes a built-in as "This will run the action you have defined for your
%1$s in settings." (`text_sfc_fav_detail_desc`, strings.xml:2717). A favorite runs
the values it carries.

**The Presets tab sends less.** The account-wide Presets tab starts and stops SFC
favorites with only `{deviceId, tenantId, sku, id, status}`, using the same
endpoint the home screen would pick from `isDefault` and `favType`
(`gr0/w.java:1243`; dispatch `:1100-1163`). So either the server looks a favorite's
values up from its `id`, or that tab doesn't work (inferred; **live check**). If the
server does the lookup, the extra keys the home screen sends are redundant, and
sending a favorite's `id` with *different* values may or may not override them
(**live check**).

### 3.3 Favorites: create, edit, delete (P1)

All three are `PATCH sfc-experience/{deviceId}` with
`SmartFanControlCreateFavoriteRequest { deviceId, experiences: [ExperienceRequest], sku, tenantId }`
(`model/dto/SmartFanControlCreateFavoriteRequest.java:14-31`), one experience per
request. There is no POST: unlike faucets, creation is also a PATCH. The envelope
comes from the R3 response (`sm0/o.java:543` `A()`).

`ExperienceRequest` (`model/dto/ExperienceRequest.java:15-37`): `id`, `title`,
`favType`, `isDefault`, `operation`, `fan`, `light`, `nightlight`.

| Action | Experience sent | Evidence |
|---|---|---|
| Create | `{operation: "Insert", isDefault: false, favType: "FAN" or "LIGHT", title, fan or light}`, no `id` | `rm0/g.java:144-146`, `:308-310`; title trimmed in `sm0/o.java:872` `f0()` |
| Edit | The same with `operation: "Update"` and the favorite's `id` | `sm0/o.java:872-880` `f0()` |
| Delete | `{id, operation: "Delete"}` only | `qm0/l0.java:2821-2832` `G()`, `:3498` `n1()` |

Only one of `fan`, `light` and `nightlight` is non-null (`rm0/g.java` `W()`/`X()`/`Y()`).
The editor's values (`rm0/s.java`):

| Type | Field | Values | Default | Evidence |
|---|---|---|---|---|
| FAN | `speed` | `LOW`, `MEDIUM`, `HIGH` (upper-cased radio labels "Low/Medium/High", strings.xml:1915-1917) | `MEDIUM` | `:356` `X()`, `:660-664` `h0()`, `:438` |
| FAN | `timer` | `ENABLE`, `DISABLE` | `DISABLE` | `:336` `W()`, `:450` |
| FAN | `duration` | `"10"`…`"60"`, step 10, minutes ("%d mins", plurals.xml:27-30) | `"30"`; switching the timer off resets it to 30 | `:306`, `:700`, `:349`, `:429` |
| LIGHT | `brightness` | `"10"`…`"100"`, step 10 | `"50"` | `:244`, `:737`, `:524` |
| LIGHT | `circadianEnabled` | `ENABLE`, `DISABLE`; ENABLE hides the colour picker ("Circadian Lighting", strings.xml:1924) | `DISABLE` | `:379` `Y()`, `:533` |
| LIGHT | `color` | `WARM`, `WARMWHITE`, `COOL`, `COOLWHITE`, `DAYLIGHT` (labels "Warm", "Warm White", "Cool", "Cool White", "Daylight", strings.xml:1929-1933) | `WARMWHITE` | `:79-92` `H()`, `:542` |
| NIGHTLIGHT | `brightness` | `"10"`…`"100"` | `"50"` | `:774`, `:621` |

Name rules (`sm0/o.java:831` `e0()`):
- not blank (`text_error_no_whitespaces`);
- at most two words (`text_error_no_more_than_two_words`);
- not one of the reserved `devices_name` product names (`text_error_product_name_exists`);
- letters and spaces only: `[^a-z0-9 ]` (case-insensitive) is refused (`text_error_no_special_character`), and so are digits (`text_error_no_numbers`) (`hy0/c.java:24,34`);
- unique among this device's favorites, ignoring case and surrounding spaces (`sm0/o.java:561` `I()`, `text_error_name_exists`);
- at most 18 characters (`TextInputEditTextStyle`, `styles.xml:6107`).

The note under the field says names are used for voice commands
(`text_favorite_title_description`, strings.xml:1937).

Results:
- **Success.** "Favorite saved." (`text_favorite_saved`) and back to home, which re-reads.
- **Name taken.** If a create fails and the error text equals "Favorite with name '<title>' already exists" (`text_error_favorite_name_exists`, strings.xml:1832; `sm0/o.java` `b0()`), the app shows "This name is already in use. Try entering another name." under the field (`SmartFanControlCreateEditFavoriteFragment.java:335` `Z2()`). Only codes 917 and 918 pass the server's text through on a 400 (`oy0/a.java`), so the server presumably sends that message with one of them (inferred).
- **Delete.** "Favorite removed." (`text_favorite_removed`, `SmartFanControlHomeFragment.java:1293` `p3()`).

### 3.4 Default settings (C8 `updatedefaultsettings`)

**This is a full-document write.** Every settings screen sends all 34 fields: it
copies the unchanged ones from its last R2 `sfcSettings` and overwrites only its
own. Body `SmartFanControlDefaultSettingsRequest`
(`model/dto/SmartFanControlDefaultSettingsRequest.java:17-117`, all String;
constructor order at `:566-602`): `deviceId`, `sku`, `tenantId` (from R2) plus the
31 settings in §2.3's last column.

**Four names differ from R2:** `color` ← `lightColor`, `nightLightBrightnessLevel`
← `nightLightBrightness`, `nlsBrightness` ← `nlsLightBrightness`, `fsEndTimeHour`
← `fsEndTimeHr`. (`fsStartTimeHr`, `nlsStartTimeHr` and `nlsEndTimeHr` keep `Hr`.)
A client that copies R2 field for field sends those four under names the request
model doesn't use; what the server does with them is not found (**live check**).

| Screen | Fields it sets | Values | Builder |
|---|---|---|---|
| Default Fan | `fanSpeed`, `fanTimer`, `fanTime` | `LOW`/`MEDIUM`/`HIGH` (upper-cased `speed_*`, strings.xml:1295-1301); `ENABLE`/`DISABLE`; `"10"`…`"60"` step 10 (reset to 30 when the timer is toggled) | `ym0/h.java:123` `B()`, `:228` `N()`, `:257` `O()`, `:209` `K()` |
| Default Main Light | `color`, `lightBrightnessLevel`, `circadianMode` | `WARM`/`WARMWHITE`/`COOL`/`COOLWHITE`/`DAYLIGHT` (`color_*_caps`, strings.xml:238-245); `"10"`…`"100"` step 10; `ENABLE`/`DISABLE` | `zm0/h.java:147` `C()`, `:256` `P()`, `:219` `L()`, `:238` `O()` |
| Default Nightlight (hidden) | `nightLightBrightnessLevel` | `"10"`…`"100"` | `an0/h.java:129` `B()`, `:191` `I()` |
| Fan Automation | `humidityThreshold` (switch), `humidity` (level) | `ENABLE`/`DISABLE`; `LOW`/`MEDIUM`/`HIGH` (upper-cased `speed_low/medium/high`; labelled "Low/Medium/High Sensitivity", strings.xml:1296-1303) | `bn0/i.java:188` `A()`, `:378` `L()`, `:370` `K()`; radios `ma0/kl.java:167-197` |
| Settings → Audible Feedback ("Play a beep sound to remote and app actions", strings.xml:1574-1575) | `buzzerSetting` | `ENABLE`/`DISABLE` | `en0/k.java:545` `E()`, `:646` `Q()` |
| Fan Schedule (editor) | `fsSchedule` = `"ENABLE"`, `fsSetting`, `fsDays`, `fsStartTimeHr/Min/AmPm`, `fsEndTimeHour/Min/AmPm`, `fsFanSpeed` | `CUSTOM`/`EVERYDAY`/`ONCE` (upper-cased "Custom/Everyday/Once", strings.xml:2712,2714,2733); mask or `"0"`; times; `LOW`/`MEDIUM`/`HIGH` | `cn0/l.java:109-138` `A()`, `:344` `M()`, `:374` `N()`, `:316` `L()` |
| Fan Schedule (summary switch) | `fsSchedule` | `ENABLE`/`DISABLE` | `dn0/h.java:87` `A()`, `:160` `I()` |
| Nightlight Schedule (hidden) | `nlSchedule` = `"ENABLE"`, `nlsSetting`, `nlsDays`, `nlsStart*`, `nlsEnd*`, `nlsBrightness` | as the fan schedule; brightness `"10"`…`"100"` | `fn0/a.java:258-287` `A()`, `:487` `R()`, `:439` `N()` |
| Nightlight Schedule summary (hidden) | `nlSchedule` | `ENABLE`/`DISABLE` | `gn0/h.java:144` `A()`, `:217` `I()` |

Schedule encodings:
- **Days** are a 7-bit mask sent as a decimal string, with bit 0 = Monday … bit 6 = Sunday (decode `zn0/h.java:483` `M()`, labels "Mon"…"Sun" in `:488` `N()`, encode `:504` `O()`). So weekdays are 31 and every day is 127.
  - **Masks below 10 go out with a leading space** (`" 1"`…`" 9"`): the encoder pads to two characters with Kotlin's default pad character `' '` (`kotlin/text/StringsKt__StringsKt.java:1134`).
  - The app's own decoder (`toUInt(…, 10)`) would throw on that space, so the server presumably trims it before storing (inferred; **live check**).
  - `EVERYDAY` and `ONCE` send `"0"`.
- **Times** are 12-hour parts sent as strings: hour `"1"`…`"12"` unpadded, minute, and `"AM"`/`"PM"` (`zn0/h.java:468-481` `J()`/`K()`/`L()`; `zn0/m.java:303-313`).
  - On a phone set to 12-hour time the minute is zero-padded (`"05"`; `text_time_am`/`text_time_pm`, strings.xml:2846-2848, via `zn0/m.java:116` `f()`).
  - On a 24-hour phone it is not: `zn0/m.java:26` `a()` turns `"19:05"` into `"7:5 PM"`.
  - Treat the minute as an integer (inferred).
- **Start equal to end is refused** with "Please set the duration for less than 24 hours." (`text_timer_validation`, strings.xml:2850; `SmartFanScheduleFragment.java:847` `A3()`).
- **Saving a schedule always switches it on** (`fsSchedule`/`nlSchedule` = `"ENABLE"`, `cn0/l.java:138`, `fn0/a.java:287`).

App bugs to avoid when copying fields:
- **Fan Automation corrupts the night-light schedule.** It sends the stored `nlsEndTimeAmPm` in the `nlsStartTimeAmPm` slot: `getNlsStartTimeAmPm()` is never called in `bn0/i.java`, and `:292` feeds `:312`. So saving automation from the app can flip the night-light start AM/PM. Copy the real value.
- **Air Quality is dead.** Its switch exists in the view model (`bn0/i.java:361` `J()`), but the layout is `visibility="gone"` (`layout/fragment_smart_fan_automation.xml:233`), and every request copies `airQuality` from R2. Nothing in 3.0.6 changes it.

On success a settings screen waits 1.5 s, shows "Settings saved"
(`text_default_fan_setting_saved`, strings.xml:1709) and goes back. There is no
MQTT wait and no re-read (`SmartFanControlDefaultFanSettingsFragment.java:98-112`).
Save is enabled only when a value differs from R2 (`ym0/h.java:195` `J()` and its
siblings).

### 3.5 Destructive and setup-only commands

- **C10 `factoryreset`.**
  - Body `{deviceId, tenantId, sku}` (`en0/k.java:592` `H()`).
  - Settings → Remove Product sends it after a confirmation ("Remove %1$s?" / "Do you want to remove this product?", strings.xml:2614-2615), then the shared remove PATCH `{deviceId, logicalName, sku, operation: "Delete"}` (`en0/k.java:598` `I()`; `SmartFanControlSettingsFragment.java:1024-1031`, then `:869-873`).
  - The Offline screen's Remove Product skips the reset and sends only the PATCH (`um0/j.java:501-505`, `:536`).
  - **Never send either.**
- **Rename.** The shared PATCH with `{deviceId, logicalName, sku, operation: "Update"}` (`wm0/i.java:1142-1148` `o0()`); see platform.md §3.
- **C9 `setrtccontrol`.**
  - Body `{deviceId, sku, tenantId, timeZone}` (`provision/data/model/SmartFanRtcValueRequest.java:13-23`). `timeZone` is the phone's IANA zone id, `TimeZone.getDefault().getID()`, e.g. `"America/Chicago"` (`un0/g.java:530-540` `E()`; also `sp0/y.java:4574` `m1()` in the shared setup view model).
  - It is sent once during setup. The app then waits on its 8 s timer for MQTT `SFC_GET_RTC_STS` before showing "connected" (`PermissionFragment.java:571`, `:588`, `:715`).
  - No other caller exists, so **the app never re-syncs the fan's clock or time zone after setup** (for example at a DST change). Schedules presumably run on the fan's own clock (inferred).

---

## 4. MQTT

Transport, acknowledgement and envelope are in platform.md §4. The Smart Fan's
envelope model is `model/SmartFanControlFavoriteMqttResponse.java:15-58`
(`correlationid, data, deviceid, durable, internalid, messageid, protocol, simulated, sku, sysid, tenantid, timestamp, ttl, type, ver`).
`data` is `model/Data.java:15-22` `{attributes, code, type}`.

| `data.code` | Attributes | Meaning and app reaction | Handler |
|---|---|---|---|
| `SFC_STS` | `attributes[0]` is an `Attribute` (`model/Attribute.java:13-47`): `fanstatus`, `fanspeed`, `lightstatus`, **`lightcolour`** (British spelling), `lightbrightnesslevel`, `nightlightstatus`, `nightlightbrightnesslevel`, `favoriteid`, `temperature`, `humidity`, `co2`, `voc`. All String; there is no `code` inside the attribute. | **A full state snapshot.** The app overwrites every state field from it, so a field missing from a message becomes null (`qm0/l0.java:3524-3549`), which implies the device always sends them all (inferred). The home screen then redraws the Fan button, tiles and Stop-all (`SmartFanControlHomeFragment.java:814` `U2()`). | `SmartFanControlActivity.java:811-822` `j1()` → `pm0/y.java:480` `L()` (replay-1 flow, `:443-444`) → home `U2()` → `qm0/l0.java:3524` `o0()` |
| `SFC_GET_RTC_STS` | `attributes[0]` is an `Attributes` (`model/Attributes.java:13-38`): `ampm, day, hour, min, month, second, unix, weekday, year` (String) | The fan's clock, reported after `setrtccontrol` during setup. Stored and never read; its arrival completes pairing. | `PermissionFragment.java:704-720` `j1()`, stored at `:127` |

Attribute → state (`qm0/l0.java:3539-3549`): `fanstatus`→`fanStatus`,
`fanspeed`→`fanSpeed`, `lightstatus`→`lightStatus`, `lightcolour`→`lightColor`,
`lightbrightnesslevel`→`lightBrightnessLevel`, `nightlightstatus`→`nightLightStatus`,
`nightlightbrightnesslevel`→`nightLightBrightnessLevel`, `temperature`→`temperature`,
`humidity`→`humidity`, `voc`→`vocvalue`, `co2`→`co2value`.

`favoriteid` is resolved into `runningFanFavorite` / `runningLightFavorite`
(`qm0/l0.java:3550-3805`):

| `favoriteid` | Result |
|---|---|
| `"DEFAULT-FAN"` | `runningFanFavorite` = `"DEFAULT-FAN"` |
| `"DEFAULT-LIGHT"` | `runningLightFavorite` = `"DEFAULT-NIGHTLIGHT"` if `nightlightstatus` is `"ON"` (ignoring case), else `"DEFAULT-LIGHT"` |
| `"DEFAULT-NIGHTLIGHT"` | `runningLightFavorite` = `"DEFAULT-NIGHTLIGHT"` |
| `"0"` | **No favorite named.** The app guesses: if `fanstatus` is `"ON"`, the selected FAN favorite whose `speed` equals `fanspeed`, else `"DEFAULT-FAN"`; if `lightstatus` is `"ON"`, the selected LIGHT favorite whose colour and brightness match, else `"DEFAULT-LIGHT"`; if `nightlightstatus` is `"ON"`, the matching NIGHTLIGHT favorite, else `"DEFAULT-NIGHTLIGHT"`. Presumably `"0"` is what the fan sends after remote-control or wall use (inferred; **live check**). |
| any other id | Looked up in R3 by `id`. FAN → `runningFanFavorite`; LIGHT → `runningLightFavorite` (or `"DEFAULT-NIGHTLIGHT"` when the night light is on); NIGHTLIGHT → `runningLightFavorite`. |

Notes:
- The app never clears the running-favorite ids. When the fan stops, the old id stays, and only `fanstatus` `"OFF"` makes it non-running (`qm0/l0.java:3384` `g1()` requires `"ON"`). **Always gate on the status fields.**
- One message names one favorite, so a fan favorite and a light favorite running together can't both be identified from a single message (inferred).
- The activity drops messages whose `deviceid` differs from the open device (exact compare, `SmartFanControlActivity.java:816`).
- The account Presets tab also listens. For `SFC_STS` it refreshes R5 unless `data.favoriteid` and `data.experienceid` are both `"0"` (`gr0/j.java:1047-1050`). Those two keys are read from `data`, beside `attributes`, so the message may carry `favoriteid` at both levels (inferred; **live check**).
- **No other Smart Fan codes are in the decompile.** There is no firmware code (the faucet's `INSTALL_FIRMWARE_STS` handlers are not wired for SFC), no fault, filter or alert code, and the Smart Fan screens publish nothing.

---

## 5. Status codes and refusals

How an error becomes a message:
1. **HTTP 400.** The body's `statusCode` (`com/utils/network/utils/models/ErrorResponseModel.java:24`) is mapped to text by the shared table (`ja0/b.java:232-234` → `d()` `:166-184` → `oy0/a.java`). Other non-2xx statuses use the HTTP reason phrase (`ja0/b.java:236-243`).
2. **`zn0/h.java:633` `e()`.** Texts containing "internal server error", "is offline or not found" or "default_error_code", or equal to "timeout", "ssl_exception" or "not found", become "This operation could not be completed, Please try again." (strings.xml:2782). "network" becomes the lost-Wi-Fi message (strings.xml:1049).
3. **The activity classifies the result** (`pm0/s.java:134` `L()` → `SmartFanControlActivity.java:930` `r4()`):

| Classified as | App reaction |
|---|---|
| "Product is offline." (code 900's text) or the literal `"900"` | Opens the Offline screen (`r4()` `:932`) |
| The literal `"903"` | Dialog with the 903 text "This operation cannot started due to firmware update in progress." (`:942`). A real 903 arrives already translated, so it actually lands in the last row, which shows the same text (inferred). |
| "This operation could not be completed, Please try again." | Dialog |
| The literal `"404"` | Treated as "product removed by another user" (`PRODUCT_REMOVE_FROM_REMOTE`, `:950`) |
| Anything else | Dialog with the text. Dismissing "Not Found" closes the product; dismissing "Favorite does not exist" (`text_fav_does_not_exist`, strings.xml:1912) re-reads R1 (`SmartFanControlActivity.java:145-149`). Presumably the server answers a stale favorite id that way (inferred). |

The codes that matter here (texts in strings.xml:364-383; table `oy0/b.java`):

| Code | Text | Smart Fan relevance |
|---|---|---|
| 900 | "Product is offline." | Offline screen |
| 901, 902 | "Favorite cannot be updated when product is running." | Matches the app's own rule (§7) |
| 903 | Firmware update in progress | Dialog |
| 904, 909, 911, 918 | Product error; resolve and retry (918 shows the server's text) | Dialog |
| 915 | "Your favorites have reached the maximum limit." (`error_msg_915_favorite`, `oy0/b.java:183-189`, used by `oy0/a.java:80`) | Same text as the client-side limit |
| 916 | "This name already exists. Please use different name." | Favorite or product name |
| 917 | The server's message | e.g. a duplicate favorite name (inferred) |

There is no Smart-Fan-specific code. The faucet's 906, the toilet's 913/914 and
EvoCycle's 919 don't apply.

**Client-side refusals** (no request is sent):

| Situation | Text | Evidence |
|---|---|---|
| A setting saved while its output runs | "Settings cannot be saved while product is running." (strings.xml:1843) | §7 |
| A favorite saved while its output runs | "Favorites cannot be saved while product is running." (strings.xml:1837) | `SmartFanControlCreateEditFavoriteFragment.java:676-694` |
| A favorite removed while anything runs | "Favorite cannot be removed while product is running." (strings.xml:1838) | `SmartFanControlHomeFragment.java:1530-1557` `G4()` |
| Too many favorites | "Your favorites have reached the maximum limit." (strings.xml:1681) | `SmartFanControlHomeFragment.java:1687` `M4()` |
| Phone offline | "Please check your Wi-Fi connection and make sure your product is powered on." (strings.xml:1049) | every action |

The Offline screen shows "Product is offline" / "Please check your Wi-Fi connection
and make sure that your product is powered on." (strings.xml:1237, 1235) and offers
About, Remove Product and the customer-service number.

---

## 6. Firmware and usage

**Firmware** follows the shared flow (platform.md §7) with `<type>` = `sfc`:

1. **Check, F1.** `GET /platform/api/v1/firmware/sfc/{id}`, with no `releasetarget` (`ui/ota/f.java:183-184`). About decides "update available" from `firmwareUpdateAvailable` (`wm0/i.java:988-1007` `U()`).
2. **Pre-checks.**
   - The app refuses while `fanStatus`, `lightStatus` or `nightLightStatus` is `"ON"` (`ui/ota/f.java:764-775` `u()`, dispatched from `j()` at `:589-605`).
   - It also refuses when `connectionState` is `"Disconnected"` (`:530-535`).
3. **Install, F2.** `POST /platform/api/v1/firmware/sfc/{id}` with the shared body (`ui/ota/f.java:305-306`).
4. **Progress.**
   - The app polls R2 through the generic configuration URL and reads `configuration.firmwareUpdate.progress` (an SFC-only branch, `ui/ota/c.java:1758-1765`). The shared evaluation that follows compares it with `Completed`, `Started`, `Downloading`, `Installing`, `signatureverified`, `ConnectedDeviceOTAInProgress`, `Failed` and `TimeoutFailed`, each as written and in lower case (`Completed` and `Failed` also in upper case) (`:1800-1880`).
   - It reads the running version from `configuration.about.firmware.version` (`:1389-1393`).
   - The overall timeout is 300 000 ms = 5 min (`ui/ota/f.java:571`).
   - The estimate shown is "5 mins" (`:112`). The string "Estimated Time: Up to 10 min" (`text_ota_estimated_time_sfc`, strings.xml:2380) is never referenced.
5. **About** treats `configuration.firmwareUpdate.progress` = `"Started"` (ignoring case) as an update in progress (`wm0/i.java:1254-1262`). It shows "Gateway" firmware from `about.gateway.firmware` and "Controller" firmware from `about.sfc.firmware` (`xm0/c.java:48,50`), and the install date from `about.gateway.installedDate` (`wm0/i.java:1264`).

No MQTT firmware message is handled for SFC (not found).

**Usage: none found.** There is no `sfc-usage` endpoint, no SFC branch in the shared
usage view model, no `sfc-diagnostics` (fault log), and no Smart Fan push
notification type.

---

## 7. The app's own rules

1. **Every save is gated on a fresh R1 read.** `"Connected"` is required; `"Disconnected"` opens the Offline screen. What counts as "running" depends on the screen:

   | Screen | Refused while | Evidence |
   |---|---|---|
   | Default Fan | `fanStatus` = `"ON"` | `SmartFanControlDefaultFanSettingsFragment.java:731-738` |
   | Fan Automation | `fanStatus` = `"ON"` | `SmartFanAutomationFragment.java:743-761` |
   | Fan Schedule (editor and summary) | `fanStatus` = `"ON"` | `SmartFanScheduleFragment.java:607-613`; `SmartFanScheduleDetailsFragment.java:726-733` |
   | Default Main Light | `lightStatus` = `"ON"` | `SmartFanControlDefaultMainLightSettingsFragment.java:755-762` |
   | Default Nightlight, Nightlight Schedule (hidden) | `nightLightStatus` = `"ON"` | `SmartFanControlDefaultNightlightSettingsFragment.java:755-762`; `SmartFanNightLightScheduleFragment.java:625-631`; `SmartFanNightLightScheduleDetailsFragment.java:682-689` |
   | Audible Feedback | any of the three | `SmartFanControlSettingsFragment.java:976-994` `x3()` |

   Whether the cloud enforces the same rule (perhaps as 901/902) is unknown (**live check**).
2. **Favorites.**
   - **Saving** (create or edit) is refused when the favorite's own output runs: FAN while the fan is on, LIGHT while the light is on (`SmartFanControlCreateEditFavoriteFragment.java:684-694` `s3()`). The night-light test compares `favType` with `"NIGHT_LIGHT"`, but the type is `"NIGHTLIGHT"`, so it never fires (`:691`). That is moot, because such favorites can't be created.
   - **Deleting** is refused while anything runs. A confirmation comes first: "Remove %1$s?" / "Do you want to remove this favorite?" (`F4()` `SmartFanControlHomeFragment.java:1512`; strings.xml:2613-2615).
   - Each tile's **edit/remove menu is disabled** while the product runs (`ma0/j9.java:286`; `rm0/t.java:200` `R()`).
   - **Types offered:** fan-only → FAN; fan with light → FAN and LIGHT; NIGHTLIGHT never (`SmartFanControlCreateEditFavoriteFragment.java:584-588`, `dropLast`).
   - **Limit.** "Add a Favorite" refuses once the tile list holds 6 (fan only) or 7 (fan with light). The tile list is the favorites minus `experiences[0]`, minus night-light ones. The constant 8 for night light is unreachable (`SmartFanControlHomeFragment.java:1455-1476` `E3()`, `:1687` `M4()`). On a fan with light the built-in light is one of those tiles, so both variants presumably allow **6 user favorites**, provided a fan-only unit's list carries no built-in light (inferred; **live check**).
3. **Stop all.** While anything runs, "Add a Favorite" turns into "Stop all" (strings.xml:2813). "Anything" means fan, light or night light on a fan with light, and just the fan otherwise (`qm0/l0.java:3854` `r0()`; `SmartFanControlHomeFragment.java:1568` `H4()`, `:1625` `K4()`). Start, stop and Stop all need no confirmation.
4. **The main Fan button** shows "Fan..." (on) only when `runningFanFavorite` is `"DEFAULT-FAN"` and `fanStatus` is `"ON"` (`qm0/l0.java:3330-3360` `a1()`, `SmartFanControlHomeFragment.java:1734` `W3()`). While a fan favorite runs it shows "Fan" (off) and the favorite's tile is lit. Pressing it then sends `DEFAULT-FAN` `"ON"`, presumably switching to the built-in fan (inferred).
5. **No pre-check before commands.** Offline handling happens only on R1 reads; commands themselves aren't pre-checked. The app sends one command at a time behind a spinner and waits up to three 8 s ticks for any MQTT message (§2.7).
6. **Ordering:**
   - R4 before favorites when the list is missing or empty;
   - factory reset before removal;
   - `setrtccontrol` before `SFC_GET_RTC_STS` during setup.

   No other command sequencing, interlock or quiet-hours rule was found.

---

## 8. What the app's screens show

**Home** (`layout/fragment_smart_fan_control_home.xml`):
- **Fan button** (`buttonFanStartStop`): toggles the built-in fan. Labelled "Fan", or "Fan..." while the built-in fan runs (strings.xml:2719-2720), with a running animation.
- **Fan card** (`cardViewFanControl`): opens the built-in fan's detail sheet.
- **Favorite tiles** (`recyclerViewFavorite`): one per favorite except `experiences[0]` and night-light ones. Each shows its title, a start/stop switch, and a fan or light animation; its menu gives Edit / Remove (`SmartFanControlHomeFragment.java` `x4()`, popup `E4()`). With no favorites the screen shows "No Favorites" / "To add a new favorite tap on the plus sign." (strings.xml:1236, 1254).
- **"Add a Favorite" / "Stop all" button.**
- The **fan speed, colour, brightness and sensor values are not shown**.

**Favorite detail sheet** (`layout/fragment_smart_fan_control_favorite_detail.xml`;
binding `ma0/ul.java`):
- type label and Start/Stop button (strings.xml:2715-2716);
- for built-ins, the "…defined for your %1$s in settings" text;
- for FAN favorites, speed (e.g. "Medium") and timer (minutes, or "OFF", strings.xml:1921);
- for LIGHT favorites, brightness % and colour, or "Circadian Lighting";
- for NIGHTLIGHT favorites, brightness %.

**Create/Edit Favorite:** type choice, name, then the pickers in §3.3.

**Settings** (`layout/fragment_smart_fan_control_settings.xml`; binding `ma0/am.java`):

| Section | Items |
|---|---|
| Default Settings | **Fan**; **Main Light** (only with the light); **Nightlight** (always hidden); **Audible Feedback** switch |
| Manage | **Fan Automation**; **Fan Schedule**; **Nightlight Schedule** (always hidden) |
| General | About; Product Literature; **Remove Product** |

- **Fan Automation:** Humidity Sensitivity switch plus Low / Medium / High Sensitivity ("Activates the fan when the air is very humid" … "Engages the fan at lower humidity levels", strings.xml:1296-1303); the Air Quality row is hidden.
- **Fan Schedule:** Once / Everyday / Custom with day toggles, start and end time, speed. The summary shows "%1$s:%2$s %3$s to …", "Speed: %s", "Schedule: %s" (strings.xml:2642-2646) and an on/off switch.

**About:**
- name (rename);
- model "Smart Fan";
- serial number (from the device id);
- install date;
- firmware with update status;
- technical information: Gateway and Controller firmware.

**Offline screen:** §5.

**Modelled but never shown:**
- `temperature`, `humidity`, `co2value`, `vocvalue`, `errorState`;
- the live `fanSpeed`, `lightColor` and brightness;
- `lastConnected`;
- the night light, its defaults and its schedule;
- Air Quality.

---

## 9. What `kohler_konnect` does with it

Readings and controls are on, the controls flagged `untested`,
because nothing here has been seen on hardware (`product_base.UntestedControl`). What the
app never shows (§8) is off until an owner turns it on: the air readings and the night
light with its default. Code:
`konnect/smart_fan.py` (protocol) and `smart_fan/` (entities).

**Reads.** R1, R2 and R3 at setup, after every MQTT connect (the broker replays nothing,
platform.md §4.2) and every five minutes in case a message was missed, every fifteen once
the stream has carried a message about the fan; F1 twice a day.
A failed R2 or R3 keeps the last answer: neither carries live state. `SFC_STS` updates the
state in place with no read; any other code reads R1 again. Only the fields a message
carries change (§4 says the app nulls the rest; a partial message seems likelier than a
cleared field). A `favoriteid` of `"0"` is read as the app reads it (`qm0/l0.java`
`o0()`): each part that is on keeps the favorite running it while the message still shows
that favorite's speed, or colour and brightness, and otherwise runs its built-in; a part
that is off keeps what it had. After a command nothing is read at once: as the app waits
8 s for a message (§2.7), the fan is read again only if none comes. A message that arrives
while a read is out is laid over that read's result.

| Entity | Kind | Source or command |
|---|---|---|
| Fan | sensor (enum: off, low, medium, high, on) | `fanStatus`, `fanSpeed`; `favorite` attribute from `runningFanFavorite`, gated on `fanStatus` |
| Light | binary sensor, with brightness, color and favorite attributes | `lightStatus`, `lightBrightnessLevel`, `lightColor`, `runningLightFavorite`. Only when the account record says `isFanWithLight` is true, as the app shows it: what R1 says doesn't count |
| Cloud connection | binary sensor | `connectionState`; only `"Disconnected"` is off |
| Gateway firmware, Controller firmware | sensors | `about.gateway.firmware`, `about.sfc.firmware` |
| Firmware status | update | F1 (`firmwareUpdateAvailable`); installing stays with the app |
| Temperature, Humidity, CO₂, VOC | sensors (diagnostic, off by default) | The raw `state` values; units unknown (§10 Q6) |
| Fan | fan, **untested** | On: C1 `DEFAULT-FAN`. Preset modes are the saved FAN favorites (C2 with their stored values). Off goes to what runs: C2 for a running favorite, else C1. No speed control: no command sets a running fan's speed (§3.2) |
| Light | light, **untested** | As the fan, with C3/C4 and LIGHT favorites as effects. On/off only |
| Stop all | button, **untested** | C7, identity only. Always available, though the app offers it only while something runs: a stop must not wait on a state that could be stale |
| Defaults | select, switch, number, **untested** | C8 for `fanSpeed`, `fanTimer`, `fanTime`, `lightColor`, `lightBrightnessLevel`, `nightLightBrightness`, `circadianMode`, `humidityThreshold`, `humidity`, `buzzerSetting`, `fsSchedule` (the last available once `fanScheduleCreated`). `nightLightBrightness` is off by default, with the night light. What the app hides is unavailable: `fanTime` unless `fanTimer` is `ENABLE` (`ma0/ol.java`), `humidity` unless `humidityThreshold` is (`ma0/kl.java`), `lightColor` while `circadianMode` is (`ma0/ql.java`). Switching `fanTimer` also writes `fanTime` `"30"`, as the app resets it (`ym0/h.java` `O()`) |
| Fan schedule | action `set_fan_schedule`, **untested** | C8 as the Fan Schedule editor saves it (§3.4): `fsSchedule` `ENABLE`, `fsSetting`, `fsDays` (the mask padded with a space, `"0"` but for `CUSTOM`), the 12-hour start and end parts, `fsFanSpeed`. Refused while the fan runs, and when start and end are the same, as the app refuses them. The schedule switch carries the schedule as attributes |
| Night light | light (configuration, off by default), **untested** | C5 with `DEFAULT-NIGHTLIGHT`: on at its stored brightness; on a fan with a light. The app has the controls but never shows them: `isNightLightFeatureEnabled` is never set, and Settings sets "fan with night lights" false (`en0/k.java`). So a fan may have none (Q7). A configuration entity, so turning on an area's lights doesn't send it |
| Sync clock | button, **untested** | C9 with Home Assistant's time zone. The app sends it only at setup, so this is the way to follow a move. Whether the fan follows daylight saving by itself is Q14 |

Every C8 re-reads R1 and R2 first, as the app's Save reads R1, and writes all 31 settings,
with the four renames of §3.4 and the true `nlsStartTimeAmPm`. It is refused, with nothing
sent, when the read finds no settings (written from nothing, one change would clear the
rest), while R1 says `"Disconnected"`, and while the part it sets up runs, by the app's
rules in §7, read from that fresh R1: the night-light default waits for the night light,
and Audible Feedback for anything running, the night light included. A change to what is
already stored sends nothing, as the app's Save stays off. Changes go one at a time, and a
value just written is laid over R2 until R2 shows it (R2 may lag a write), so a second
change doesn't write the first one back. The number defaults take the app's steps of 10;
a value between steps goes to the nearest. Commands are refused while R1 says
`"Disconnected"`.

Left out: night-light favorites (C6) and the night-light schedule, favorite create/edit/
delete, Air Quality, and factory reset and removal (C10, A1).

## 10. Open questions (live checks)

1. **Default fan.** Is `experiences[0].id` always `"DEFAULT-FAN"`, and are the three built-in ids exactly `DEFAULT-FAN`, `DEFAULT-LIGHT`, `DEFAULT-NIGHTLIGHT`? Does R4 `/load` create them?
2. **Turning off.** Does C1 `"OFF"` stop a running *fan favorite*, or only the built-in fan? Same question for C3 against a light favorite.
3. **Speed while running.** Is there any way to change the speed of a running fan without stop/start? Try C2 with a favorite's `id` but a different `speed`, C1 with an extra `speed` key, and C8 `fanSpeed` while running (does the cloud refuse with 901/902, and does the running fan change?).
4. **Favorite bodies.** Does the server honour favorites started with only `{id, status}`, as the Presets tab sends them? If so, does it ignore or apply differing `speed`/`color`/`brightnessLevel`?
5. **`SFC_STS` payload.** Capture one: are all twelve attributes always present? What are `fanspeed`'s values (`LOW`/`MEDIUM`/`HIGH`?) and `lightcolour`'s ("None" when off?)? Are `favoriteid`/`experienceid` also present at `data` level? Does a remote-control change arrive with `favoriteid` `"0"`?
6. **Sensors.** Units and formats of `temperature` (°C or °F?), `humidity` (%RH?), `co2`, `voc`. Do fans without those sensors send empty strings, `"0"` or nothing?
7. **Night light.** Does any hardware have one? Are the main light and night light mutually exclusive, as the single `runningLightFavorite` slot suggests?
8. **Colours.** The colour temperature in kelvin of `WARM`, `WARMWHITE`, `COOL`, `COOLWHITE`, `DAYLIGHT`. What does `circadianMode` `ENABLE` do to `lightColor` in R1 and MQTT?
9. **`updatedefaultsettings`.**
   - Is a partial body accepted, or does an omitted field get cleared? The app always sends all 34, so test with a full body first.
   - What happens if the R2 names (`lightColor`, `fsEndTimeHr`, …) are sent instead of the request names?
   - Does the server trim the leading space in `fsDays` values below 10?
10. **Timer semantics.** Does `fanTimer` `ENABLE` with `fanTime` 30 mean "auto-off after 30 min" for the built-in fan? Is the humidity automation's run time the same `fanTime`?
11. **Connection state.** Values other than `"Connected"` / `"Disconnected"` in R1, and how quickly it flips after a power cut.
12. **Timestamps.** Confirm that R1's `lastConnected` and `updatedTimestamp` are epoch seconds.
13. **Auth.** Do Smart Fan commands accept an ROPC token, or do they need `B2C_1A_signin` like the Anthem?
14. **Clock.** Does the fan follow DST without another `setrtccontrol`? Does R2's `tzTimeSpanDiff` carry the offset?
15. **Errors.** Which `statusCode`, if any, comes back when a command targets a deleted favorite ("Favorite does not exist") or when a setting is written while running.
16. **Firmware.** Does F1 return `firmwareUpdateAvailable` correctly for SFC? Does `configuration.firmwareUpdate.progress` follow `Started` → … → `Completed` during an OTA?
