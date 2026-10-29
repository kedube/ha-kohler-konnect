# Kohler Aquifer Refine: what Konnect 3.0.6 knows

**Source:** Kohler Konnect for Android **3.0.6** (version code 260), decompiled
with jadx 1.5.6.

**Caveat.** Everything here comes from the app's side. Nobody on this project owns
an Aquifer Refine, so **nothing in this chapter is live-verified**; facts are **app** or
**inferred** in the sense of the [README](README.md#confidence). "Not found in the
decompile" means just that; the cloud may still serve an Aquifer to older app versions.

**Finding things in the decompile.** Code paths are relative to jadx's `sources/`
root, and `strings.xml:N` is under jadx's `resources/res/values/`. Obfuscated packages
(`k90/`, `zn0/` …) change between builds, so every citation also names a string to grep
for.

> **Verdict: nothing cloud-reachable.** Konnect 3.0.6 cannot add, show, read or update
> an Aquifer Refine. It declares a firmware endpoint for one and never calls it, every
> Aquifer message in the app is unused, and the SKU `AQF` survives only in a list of
> products that can be set up on older Android versions. **An integration should not
> create Aquifer entities.**

---

## 1. The SKU, and where the app would need it

Every place a supported product must be wired in leaves it out, as for the PerfectFill
([perfectfill.md](perfectfill.md) §1):

| Where | What 3.0.6 has | Evidence |
|---|---|---|
| Add-product list | 15 SKUs, no `AQF` | `st0/r.java:231` (`C(SkuResponse)`) |
| Product tile factory | No `AQF`; anything else throws `"Invalid sku"` | `tt0/w1.java:105` |
| Dashboard tap | No `AQF` | `fr0/f.java:1075` (`E3()`) |
| Firmware check | No `AQF`; anything else is `"Invalid SKU …"` | `com/kohler/hermoth/ui/ota/f.java:141-205` (`b()`) |
| Legacy-OS allow-list (enum of SKUs) | `SEN DTV GCS VRD SET AQF NUM MOX MOXAI PP2 PC1 BLD HUB SFC` | `k90/a.java:11-25`, read by `SelectProductActivity.java:719` and `fr0/f.java:1337` to decide what can be set up below a given Android version |

That `AQF` is the Aquifer Refine is inferred from that list and the strings below;
nothing in the code ties the two together.

## 2. What is left of it

| Trace | Where | Used? |
|---|---|---|
| `AQUIFER_FIRMWARE_UPDATE_API = "/platform/api/v1/firmware/aquifer/{deviceid}"` | `com/utils/network/retrofit/proxy/ApiConstant.java:57` | Never referenced |
| "Your Aquifer Refine is offline. Please check your Wi-Fi connection…" (`text_aquifer_refine_offline`) and "Your Aquifer is offline. Please turn it on in order to download the latest firmware." (`text_aquifer_offline_firmware_error`) | `strings.xml:1449-1450` | Never referenced |
| `text_aquifer_authorization_retry_message`, `auth_req_aquifer`, the `shape_aquifer_line_*` drawables | `R.java` | Never referenced |
| "Unplug and plug your Aquifer Refine back in to start setup mode" (`text_power_cycle_aquifer_user_info`, `strings.xml:2496`), `text_authorize_aquifer_instruction`, `text_permission_note_aquifer` | The Smart Fan's setup (`zn0/i.java:499-516`, the `ProductModel` built for `"SFC"`), `rt0/d.java`, `rt0/o.java`, `ma0/oj.java` | Yes, but in other products' setup, never for an Aquifer |
| `TanklessROFilterResetRequest` | `com/utils/network/retrofit/proxy/platform/model/tanklessro/` | No Retrofit method takes it |
| `tanklessROUsageDataDetailsList` in `WaterUsageModel` | `com/utils/network/retrofit/proxy/device/model/WaterUsageModel.java:171` | Never read |
| `AQUIFER_AMAZON_ORDERING_URL`, a filter shop | `ApiConstant.java:56`, opened from `ft0/b.java` | Yes, but from the faucet's setup page |
| A `# Aquifer` section | `assets/deviceconfig.properties` | Never read |

The strings say an earlier app set an Aquifer Refine up over Wi-Fi and checked its
firmware through Kohler's cloud. 3.0.6 keeps the words and none of the code.

## 3. What `kohler_konnect` does with it

**Nothing Aquifer-specific.** An Aquifer on an account, like any SKU the integration
doesn't support, gets no entities, and the setup summary lists it among the ignored
devices (`Customer.describe`). The firmware endpoint is not called: nothing in the app
shows what it takes or returns, or which SKU an Aquifer carries on the account.

## 4. Open questions (live checks)

All need an Aquifer Refine owner; every probe below is a **read**.

1. **Account record.** Does `customer-device` list it, and with which `sku` and
   `deviceId` prefix?
2. **Firmware.** Does `GET /platform/api/v1/firmware/aquifer/{deviceid}` answer, and in
   the shape of the other products' firmware reads?
3. **MQTT.** Does it send anything on the account's IoT Hub stream?
