# Kohler PerfectFill: what Konnect 3.0.6 knows

**Source:** Kohler Konnect for Android **3.0.6** (version code 260), decompiled
with jadx 1.5.6, plus the string tables of all twelve `classes*.dex` files in the
same APK.

**Caveat.** Everything here comes from the app's side. Nobody on this project owns
a PerfectFill, so **nothing in this chapter is live-verified**; facts are **app** or
**inferred** in the sense of the [README](README.md#confidence). "Not found in the
decompile" means just that; the cloud may still serve PerfectFill to older app
versions or to the bath's own panel.

**Finding things in the decompile.** Code paths are relative to jadx's `sources/`
root; `strings.xml:N` and `arrays.xml:N` are under jadx's `resources/res/values/`,
`drawable/…` under `resources/res/`, and `assets/…` under `resources/`. Obfuscated
packages (`st0/`, `gr0/` …) change between builds, so every citation also names a
string to grep for.

> **Verdict: nothing cloud-reachable.** Konnect 3.0.6 cannot add, list as its own
> product, open, update or command a PerfectFill. There is no PerfectFill endpoint,
> no fill or drain command, no MQTT code and no screen. What remains is leftovers:
> unused strings and drawables, a reserved product name, an unused local database
> table, and three JSON keys in shared models. The Anthem's bath-fill feature is
> unrelated: it is a GCS valve feature with no drain control. **An integration
> should not create PerfectFill entities.**

---

## 1. The SKU, and how the app recognises it

**It doesn't.** The uppercase string `PFC` does not occur anywhere in the app. Every
entry of the `string_ids` table of all twelve `classes*.dex` files was checked:
`IBH` is there (three times), but `PFC` is not, so no code can compare a SKU with
`"PFC"`. The lowercase `pfc` occurs only as a JSON key (below). **The SKU `PFC` is
an inference from those key names, not something the app proves.**

Places a supported product must be wired in, all without PerfectFill:

| Where | What 3.0.6 has | Evidence |
|---|---|---|
| Add-product list | Keeps only 15 SKUs from the server's catalogue: `SEN DTV GCS VRD SET NUM MOX MOXAI PP2 PC1 BLD HUB SFC EVO IBH` | `st0/r.java:231` (`C(SkuResponse)`) |
| Product tile factory | Same 15; anything else throws `"Invalid sku"` | `tt0/w1.java:105`; the SFC flow's copy `zn0/i.java:645` |
| Dashboard tap | Routes `BLD DTV EVO GCS HUB IBH MOX NUM PC1 PP2 SFC VRD MOXAI`, plus `SEN`/`SET` through `y3()` in a block jadx could not restore; no default branch | `fr0/f.java:1075` (`E3()`) |
| Firmware check | Maps `BLD DTV EVO GCS HUB NUM SEN SET SFC` to a type; anything else is `"Invalid SKU …"` | `com/kohler/hermoth/ui/ota/f.java:141-205` (`b()`) |
| Wi-Fi setup soft-AP prefixes | `SEN- SET- DTV- VRD- NUM- GCS-` | `ht0/q.java:151` |
| Legacy-OS allow-list (enum of SKUs) | `SEN DTV GCS VRD SET AQF NUM MOX MOXAI PP2 PC1 BLD HUB SFC` | `k90/a.java:11-25` |
| Product package | `products/` has no PerfectFill package; the README's list (`anthem`, `anthemhub`, `faucet`, `dtv`, `evocycle`, `moxie`, `icebathcontrol`, `sfc`, `verdera`) is complete | `com/kohler/hermoth/products/` |

The server's catalogue is `GET /devices/api/v1/device-management/sku?SortBy=Sku&OrderBy=Asc&ApplicationSource=Kohler Konnect&IsMobile=true&SkuCountry=US`
(`DeviceApiCall.java:222-223`, `getSkuListNew`; arguments at `st0/r.java:148`; `US`
is MSAL's `AbstractDevicePopManager.CertificateProperties.COUNTRY`). It returns
`{skuList: [{sku}]}` (`com/utils/network/retrofit/proxy/device/model/SkuResponse.java:14-23`).
Even if the server still lists a PerfectFill SKU, the app drops it.

If an account still carries a PerfectFill from an older app version, 3.0.6 would
show a card with the generic fallback icon (`fr0/a.java:120-130`, `I()`, the default
branch of the SKU switch), and a tap would reach the firmware check, which refuses
the SKU (above). This is inferred from the code paths; no PerfectFill account has
been seen.

---

## 2. What the app does with it

**Nothing a user can reach.** Every trace:

| Trace | Where | Used? |
|---|---|---|
| Reserved names "PerfectFill", "Perfect Fill" | `arrays.xml:76-77` (`anthem_hub_create_favorite`, from `:62`), `arrays.xml:211-212` (`devices_name`, from `:197`) | Yes, but only to **reject** these as preset or device names (see [sensate_faucet.md](sensate_faucet.md) §4.4) |
| `pfcPresetsExperiences` (list of `PresetModel`) | `com/utils/network/retrofit/proxy/platform/model/preset/AllPresetResponse.java:41-42`, part of `customer-experience/{tenantId}` | Read: the home screen groups the entries by `deviceLogicalName` (`gr0/w.java:1699`, `a1()`, called from `e1()` at `:1759-1766`) and counts them in an empty-list check (`gr0/j.java:221`). **Starting one does nothing**: the start router handles `DTV`, `SEN`/`SET`, `GCS`, `HUB`, `SFC` and `EVO` and silently returns for any other SKU (`gr0/w.java:1799`, `n0()`) |
| `pfcsts` (String) | `com/utils/network/retrofit/proxy/device/model/configuration/Parts.java:43-44`, beside `rainpanel`, `steam`, `valve1`… in the DTV+-era `parts` block | Never read (`getPfcsts` has no call site) |
| `pfc` (Object) | `com/kohler/hermoth/products/sfc/controls/data/model/About.java:75-76`, a copy of a cloud-wide `about` schema | Never read |
| Icon `KKIconName.PerfectFill` (`"perfectfill"`) | `com/kohler/kkuicomponent/kkthemes/kkicons/KKIconName.java:1597-1602` | Never referenced |
| Drawables `device_perfect_fill`, `device_perfect_fill_warning`, `perfectfill`, `tab_perfect_fill(_disable)`, `bg_pfc_oval_cornar`, `ic_pfc_up_arrow(_disabled)`, `ic_pfc_warmup_button(_disable/_pressed)`, `selector_pfc_*`, `selector_perfect_fill_enable_disable` | `drawable/…` | The `ic_`/`tab_` files are referenced only by the `selector_` files, and nothing references those or any other: no `R.drawable` use, no layout, and not their numeric ids either |
| Local table `pfc_session (is_do_not_show int, device_id text)` | `assets/kohlerkonnect_db_1.sql:41-46`, run when the app's database is created (`mx0/a.java:53`, `".sql"`) | Created; never read or written (no `pfc_session` string in any dex) |
| Strings (§3) | `strings.xml` | All unused, except `text_pfc_firmware_up_to_date` ("Up to date"), reused as a generic label (`ma0/il.java:1557`, Smart Fan settings; `fg0/t.java:1199`) |

---

## 3. What the leftovers say PerfectFill was

These strings have **no** reference in code or layouts, so this section describes
a removed feature, not 3.0.6 behaviour (inferred throughout):

| Topic | Strings |
|---|---|
| Own product with Wi-Fi setup and Alexa | `text_my_perfect_fill` "My PerfectFill" (`:2302`); `text_provision_step_three_pfc` (`:2569`); `text_setup_instruction`, about 20 minutes end to end (`:2703`); `text_alexa_turn_on_pfc` / `text_alexa_turn_off_pfc` (`:1401`, `:1406`); `text_pfc_link_alexa_description` (`:2480`) |
| Commissioning | `text_commissioning_dialog` ("must be commissioned before you can use it", `:1635`); `text_re_commissioning_dialog` (`:2589`); `text_button_recommission`, `text_recommission_product` (`:1560`, `:2595`); `text_commissioning_failed(_desc)`, "power cycle your controller" (`:1636-1637`) |
| Fill level | `text_set_max_fill_level`, `text_set_max_fill_level_instruction` ("Use the buttons below to fill the bath to your desired Max Fill Level", `:2680-2681`), `text_reset_your_max_fill` (`:2631`), `text_default_water_level` (`:1724`), `text_water_level` (`:2940`) |
| Scheduling and top-up | `text_daily_bathfill` (`:1699`); `text_set_timer_description` ("how often your PerfectFill will run water to increase the bath", `:2687`); `text_auto_warmup_description` ("automatically warmup the bath at a set time interval", `:1495`); `text_delta_temp_desc` (warmer or cooler than the original fill, `:1735`) |
| Drain | `text_auto_drain`, `text_auto_drain_min`, `text_auto_drain_minutes` (`:1485-1487`) |
| Safety | `text_pfc_idle_state_alert` "You cannot recommission until the bath is empty." (`:2479`) |
| Firmware | `text_pfc_update_*`, `text_pfc_updating(_note)`, `text_pfc_product_offline_firmware` (`:2481-2486`); `text_ota_estimated_time_pfc` "Up to 10 min" (`:2379`) |

So PerfectFill had its own controller, a commissioning step, a max fill level set by
filling to it with up and down buttons, a default level, a daily fill, periodic
warm-up top-ups and an auto-drain timer (inferred). None of the request shapes
survive.

The "Drain Motor" fault entries (`yw0/d.java:71-73`, codes 1651, 1653, 1655) belong
to the EvoCycle fault table (ozone generator, pumps, float, shower-base filter in
the same list), not to PerfectFill.

---

## 4. The Anthem bath-fill hypothesis

Tested and rejected. The only fill-related cloud command in the app is the Anthem
valve's, and it is GCS-only:

- `POST /platform/api/{version}/commands/gcs/bathfillervolume` (`ApiConstant.java:55`,
  `ANTHEM_READ_BATH_FILLER_VOLUME_API`; `PlatformApiCall.java:126-127`,
  `anthemReadBathFillVolume`). Body `AnthemReadBathFillVolumeRequestModel
  {deviceId, sku, tenantId, index}`
  (`com/utils/network/retrofit/proxy/platform/model/anthem/AnthemReadBathFillVolumeRequestModel.java`),
  built with the **GCS device's own** `deviceId`/`sku` and `index: "0"`
  (`qb0/m0.java:337-345`, `H()`). It is called from
  `products/anthem/presentation/control/presets/bathfill/AnthemCreateBathFillPresetStartFragment`
  (`qb0/i0.java:827-828`). The answer is MQTT `READ_DISPENSED_WATER_VOLUME_STS`
  (`qb0/i0.java:887`).
- Filling itself is a `solowritesystem` valve word with byte 3 bit 7 set on the tub
  filler's outlet (outlet type 21); stopping is byte 3 = `0xC0`. See
  [gcs_valve.md](gcs_valve.md) §3.2 and §8. That needs an Anthem valve with a
  "Bathfiller" outlet, not a PerfectFill.

**There is no drain command anywhere.** The command families in the dex are
`blade`, `dtvplus`, `evo`, `faucet`, `gcs`, `hub`, `numi`, `sfc`, `zero` and the
factory-reset pattern `{name}/{endpoint}`. No path contains `drain`, `pfc`, `tub` or
`fill` other than `gcs/bathfillervolume`. The only `drain` strings belong to the
Azure IoT SDK's AMQP flow frame (`d21/l.java:128`), the EvoCycle fault table and the
shower-base cleaning pages. The Anthem's own bath-fill screen tells the user to
close the drain by hand: "Please close the drain and make sure nothing is in your
tub so your bath can fill." (`strings.xml:1503`, `text_bath_fill_preset_start_note`,
in `layout/fragment_anthem_create_bathfiller_presets_start.xml`).

---

## 5. Fill and drain request bodies

**None exist for PerfectFill** in 3.0.6. The nearest, the Anthem's, are in §4 and
gcs_valve.md §8.

## 6. The app's safety rules

**None are enforced for PerfectFill**, because no code path exists. The leftover
text records one former rule: no recommissioning until the bath is empty (§3). For
the Anthem bath fill, the app relies on instructions (close the drain, nothing in the
tub) and on the valve's run-time and bath-fill logic in gcs_valve.md. It has no
water-level sensing.

---

## 7. What `kohler_konnect` does with it

**Nothing PerfectFill-specific**, since nothing can be read or written through
anything the app knows.

- A PerfectFill on an account, like any SKU the integration doesn't support, gets no
  entities; the setup summary lists it among the ignored devices
  (`Customer.describe`). No product is inferred from a device id's prefix
  (platform.md §3).
- `customer-experience.pfcPresetsExperiences` is not read: the app itself cannot
  start those presets.
- No bath fill stands in for it. The Anthem's bath fill is different hardware and has
  no drain.

---

## 8. Open questions (live checks)

All need a PerfectFill owner or a willing account; every probe below is a **read**.

1. **Catalogue.** Does `GET …/device-management/sku?…` (§1) still return a
   PerfectFill SKU, and is it `PFC`?
2. **Account record.** On an account that has one, what does `customer-device` show
   (`sku`, `deviceId` prefix)? Does `customer-experience` return
   `pfcPresetsExperiences`?
3. **Server families.** Do `pfc-state/{id}` or `pfc-configuration/{id}` (by analogy
   with the other `<product>-state` and `-configuration` reads) or
   `/platform/api/v1/firmware/pfc/{id}` answer? None is in the app. A 404 would
   settle it; a bare 400 would not, because the API answers malformed requests that
   way too (platform.md §6).
4. **MQTT.** Does a PerfectFill account receive any `data.code` on the IoT Hub
   stream? None is handled by 3.0.6.
