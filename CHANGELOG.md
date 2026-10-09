# Changelog

Notable changes for each tagged release. Versions correspond to git tags and to the
`version` field in `custom_components/kohler_konnect/manifest.json`. Add entries under
**Unreleased** as part of each change; the release workflow rotates that section into a
version heading and publishes it as the release's Highlights.

Every push to `main` that passes CI is released. To choose the version, set it in
`manifest.json` and it is released as written; leave it alone and the minor version is
bumped (1.0 → 1.01). Tags and release names are the bare version — `1.0`, never `v1.0`.

## Unreleased

## 1.0 — 2026-10-09

**Kohler Konnect 1.0** is the first release of one integration for every supported device
on a Kohler account — Anthem valves, Anthem Plus controllers and Sensate faucets — under one
sign-in and one MQTT connection, domain `kohler_konnect`. It replaces two integrations,
**Kohler Anthem** (`kohler_anthem`, last release 0.28) and **Kohler Sensate**
(`kohler_sensate`, last release 0.10.0), and keeps everything both did. What is new in
Kohler Konnect comes first, then the history of each, so the changes since whichever version
you ran are all here.

⚠️ **Breaking: it is a new integration.** Nothing carries over from `kohler_anthem` or
`kohler_sensate` by itself. Delete the old entries, remove the old integrations in HACS,
restart, then add Kohler Konnect. Deleted first, most entity ids come back as they were. A
Repairs card names any old entry still set up. See
[Upgrading](docs/user_guide.md#upgrading-from-kohler-anthem-or-kohler-sensate).

### New in Kohler Konnect

**Added**

- **Sensate faucets** (and the `SET` sibling the Konnect app treats identically): water on
  and off with a water safety limit, measured dispenses, Konnect presets, leak alerts with a
  clear button, handle and connection state, last dispense, firmware status, and the
  `kohler_konnect.dispense` action, which pours an amount or a preset by name at one faucet
  or several.
- **Faucet water used today, this week, this month and this year**, from the same sensors as
  the valves', in place of the old faucet integration's lifetime total and today.
- **Deleting the entry unregisters its MQTT identity** from the Kohler account, as the
  Konnect app does on sign-out. The old shower integration left it behind.
- **Translations** in Italian, Polish, Swedish and Latin American Spanish, carried over from
  the faucet integration, alongside the existing six.

**Changed**

- **The domain is `kohler_konnect`**, the actions `kohler_konnect.custom_shower`,
  `kohler_konnect.send_valve_hex` and `kohler_konnect.dispense`, and the bundled protocol
  library `konnect/`.
- **Faucets sign in like the showers**: one `B2C_1A_signin` refresh token for the account,
  never a stored password. The old faucet integration stored the password and used Kohler's
  ROPC policy.
- **Every faucet on the account is added**, each as its own device, as the valves are. The
  old faucet integration added one faucet per entry and asked which.
- **Faucet units follow the Konnect account's unit setting**, as the water sensors do; the
  separate metric/imperial option is gone. The water safety limit is one setting for the
  account, under **Configure**, shown only on an account with a faucet.
- **The setup flow skips the valve-model question** on an account with only faucets.
- **Outlet switches are identified by zone and position only**, never by fixture name, so a
  fixture type that arrives later renames the switch rather than adding a second one.
- **A faucet's monthly usage is read at startup and when the month turns**, as a valve's is,
  rather than on every daily refresh.
- **A faucet's firmware is checked twice a day**, as a valve's is, rather than every hour.
- **`paho-mqtt` 2.0 or later** is required, the version Home Assistant already ships.
- **`dispense` takes `amount` and `unit` only**; `amount_ml` and `amount_l` are gone.
- **The logo** is the Kohler wordmark.

**Fixed**

- **Water sensors on a metric Konnect account show litres.** They tested for `Liters`, which
  no account sends; the metric value is `Metric`.
- **The MQTT stream retries when its first connection fails.** A failed registration at
  startup left it down — and the showers frozen — until Home Assistant restarted.
- **A request that times out is reported as a Kohler error** rather than escaping as a bare
  `TimeoutError`.
- **Error messages and the debug request log no longer carry the account id** or device ids
  in paths.
- **A rejected sign-in asks you to sign in again wherever it happens**: during setup's first
  reads, on a shower or controller command, and in the background checks of outlet settings,
  firmware and warmup. Several of these reported a generic error and kept failing.
- **A favorite deleted from the controller leaves the `Favorite` list**, including the last
  one.
- **Changing an outlet setting twice in quick succession checks only the second change**,
  instead of reporting the first as lost.
- **A setup that fails part-way shuts down cleanly** rather than leaving the MQTT connection
  running.
- **`Cloud Connection` turns back on by itself** once a valve that dropped off the cloud is
  power-cycled: its first message prompts a fresh check. Before, it stayed off until an
  unrelated read happened by.
- **The Repairs card for the old integrations clears** as soon as the last old entry is
  deleted, without a restart.
- **Faucet messages no longer fill the warmup journal's traffic windows.**
- **Kohler's sign-in service being down no longer asks you to sign in again.** A timeout, a
  gateway error page or a `503` from Kohler's token service is retried, as a network error
  always was; only a refusal Kohler actually issues asks for a new sign-in.
- **Warmup auto-restore gives up after five restores that don't stick**, as documented.
  Each restore's own confirmation reset the count, so a fight with whatever kept disabling
  warmup went on once a minute for ever.
- **Warmup auto-restore puts back the mode most recently taken away.** A second disable
  during the one-minute wait restored the mode from before the first.
- **Setting warmup no longer warns that the valve ignored it** when Kohler's read-back
  simply didn't include the warmup mode.
- **Shower and outlet switches no longer flip back** when another device's message arrives
  before the valve answers — on an account with a controller, often. They now wait for
  their own device, as the dropdowns already did.
- **Choosing Off right after starting an experience stops it**, instead of being ignored
  until the valve had confirmed the start.
- **A valve firmware install shows as in progress** on its `Firmware Status` entity.
- **`Max Shower Duration`, `Max Temperature` and `Default Temperature` show the confirmed
  value** as soon as their check reads it back.
- **A controller whose configuration read comes back empty keeps the rest of its startup
  data** — favorites, experiences and faults — and setup on a controller-only account asks
  for the valve model instead of failing.
- **The diagnostics download no longer contains device ids** in the ids of open repair
  issues.
- **The warmup journal and Report Log no longer lose records when a file fills**, and the
  journal no longer opens files on Home Assistant's event loop.
- **No deprecation warnings on Home Assistant 2026.10.** Zone devices name their valve by
  its device-registry id, and the controller's settings-page link looks its device up
  within this entry, as 2026.10 asks. The old ways stop working in 2027.8; on 2026.3 they
  are still used, since the new ones are not there yet.

**Removed**

- The upgrade clean-up for `kohler_anthem` entries — retired settings, registry rows and
  Repairs cards from earlier versions. A new entry has none of them.
- The `hub_present` attribute on outlet switches.
- The faucet integration's **Reconfigure** step for changing the email or password. When
  Kohler rejects the saved sign-in, Kohler Konnect asks you to sign in again.

### From Kohler Anthem, 0.02 to 0.28

What the shower integration added and changed on the way to 0.28, the version Kohler Konnect
continues from. Version numbers are Kohler Anthem's. Much of 0.20 was built from a decompile
of the Kohler Konnect Android app, version 3.0.6.

**Added**

- **Firmware update entities** for the valve, its gateway and the Anthem Plus controller,
  named `Firmware Status` and `Gateway Firmware Status`: installed against latest version,
  checked twice a day, with a package icon that changes when an update is available.
  Read-only — install in the Konnect app. (0.20; renamed, with icons, in 0.22)
- **Anthem Plus `Steam` switch**: runs the controller's default steam program, as the app's
  *Steam start* card does. Refused while the controller is running the shower. (0.20)
- **`Experience` dropdowns** on the valve and the controller: start and stop Kohler's
  built-in programs. The valve's uses the same command as favorites. (0.20)
- **Valve `Restart` button** (disabled by default): the app's *Restart Product*. (0.20)
- **Anthem Plus `Problem` sensor**: faults, active errors, and accessories that are set up
  but have stopped responding ("Steam is disconnected", a missing SD card), which used to
  just disappear. (0.20)
- **Anthem Plus `Max Shower Duration` sensor**, read from Kohler's cloud. The shorter of it
  and the valve's own limit ends a shower. (0.20)
- **The Anthem Plus device page links to the controller's web settings page**, using the
  network address Kohler's cloud reports for it (Wi-Fi, or wired when there's no Wi-Fi
  address). Found the same way the Konnect app finds it, but not yet tried on a real
  controller. (0.22)
- **Zone & outlet grouping** under **Configure**, for valves with two zones (K-28211,
  K-28212), where every per-zone name used to end in its zone number. Switching modes
  renames entities in place without changing their unique ids, and zone devices left over
  from sub-device mode are removed. (0.24)
  - **Numbered** (default): one device per valve, named as before — `Showerhead 1`,
    `Temperature 1`, `Shower Active 1`.
  - **Sub-devices**: each zone is its own device (`Anthem Valve Zone 1`, `Anthem Valve Zone
    2`) under the valve, without the zone numbers — `Showerhead`, `Temperature`, `Zone
    Active`. Whole-valve entities stay on the valve.
  - **Outlet labels**: one device per valve. Outlet switches drop the zone number unless the
    same fixture type is in both zones, and per-zone controls and sensors are labelled with
    their zone's fixtures — `Temperature (Showerhead, Body Sprays)`.
- **More attributes**: `Water Used Today` and `This Week` gain `times_turned_on`, `Light`
  per-group state, `Steam` temperature, timers and `power_clean`, and outlet switches
  `outlet_variant` (Silk, Real Rain, Katalyst…). (0.20)

**Changed**

- **Zone temperature matches the app's slider**: **Cold**, then 59 °F up to the valve's
  current Max Temperature, instead of a fixed 92–118 °F. The bottom step sends full cold.
  `custom_shower` accepts 59–118 °F. (0.20)
- **Every outlet type has a name**, from the app's own list: 38 (Silk rainhead), 39 (Real
  Rain rainhead), 62 (foot sprays) and others that showed as `Outlet 1.2`. (0.20)
- **`System State` knows `error` and `FirmwareUpdate`**; `error` also turns `Problem` on.
  (0.20)
- **Cloud Connection reads `Disconnected` directly**, and an unfamiliar value no longer
  counts as an outage. This is the app's rule. (0.20)
- **Outlet settings are written with all twelve fields** the app sends, adding `maxVolume`
  and `purge`, rather than ten. (0.20)
- **Presets written by Home Assistant use the app's `"000000"` for unused valves.** A
  favorite saved at 120 °F in the app no longer has a valve dropped from it when the default
  preset's timer is synced. (0.20)
- **Kohler's in-body status codes are compared as strings** and explained in errors:
  firmware update in progress, favorites full, name taken. (0.20)
- **Diagnostics:** the `endless_shower` block is now `run_time`, holding the valve's
  run-time limits and how long each zone has been running. (0.22)
- **The Configure dialog is translated** into every language the integration ships, and
  says it only affects valves with two zones. (0.26)
- **`Water Used This Year` is the calendar year so far** — January 1 to today, matching the
  Konnect app's Year tab. Before, it was the last twelve complete months. (0.27,
  kedube/ha-kohler-anthem#4)
- **`Water Used This Month` and `This Year` update after every shower**, not only when Home
  Assistant restarts. (0.27)
- **`Water Used Today` and `This Week` check once more**, three minutes later, when Kohler
  hasn't recorded a shower 90 seconds after it ends. A shower that ends while the connection
  is down is still counted. `Today` turns over at local midnight and reads 0 until the
  day's first shower. (0.27)
- **Renamed from Kohler Anthem Plus (`kohler_anthem_plus`) to Kohler Anthem
  (`kohler_anthem`).** "Plus" named the second-generation hub hardware, which keeps that
  name; the integration always covered the base Anthem valve too. (0.02)
- **Home Assistant 2026.3 or later** is required, the version that added the brands proxy
  the integration's icon relies on. (0.02)
- **Versions are `x.y`**, with no third component. (0.02)

**Fixed**

- **K-28211 (4-outlet) valves: zone 2 reads its own outlets.** The valve numbers its outlets
  0, 1, 3, 4 — each valve body reserves three slots — where the integration assumed 0–3.
  Zone 2's fixture names, flow range and run-time limits came from the wrong outlet, and
  Max Shower Duration's attributes failed with `ValueError: K-28211 has outlets 1-4; got 5`.
  Thanks to @ejochman (kedube/ha-kohler-anthem#1). (0.17)
- **Multi-zone naming, sub-device and outlet-name modes:** an outlet switch first registered
  by position (`Outlet 1.2`) could come back as a duplicate, leaving the original orphaned
  with its automations. It's now moved onto its fixture name as intended, in every mode.
  (0.26)
- **Leaving sub-device mode no longer resets disabled entities.** Removing the zone devices
  also removed every disabled entity still attached to them — the `Hex` sensors by default —
  which then came back enabled and lost any rename. They're moved onto the valve first.
  (0.26)
- **`Water Used This Year` no longer drops when a month ends.** The month just ended fell
  back to the partial figure read at startup until the next shower. Each month now uses the
  higher of Kohler's monthly figure and its daily total. (0.28)
- **`This Month` and `This Year` read 0, not unknown,** on the 1st and on January 1 until
  the first shower. (0.28)
- **Long-term statistics no longer record the daily, monthly and yearly resets as negative
  water use.** `Today`, `This Month` and `This Year` now tell Home Assistant when each
  period starts. (0.28)

**Removed**

- **Endless Shower**, the switch that turned the water back on when the valve reached its
  Max Shower Duration. Running water without end isn't something the Konnect app offers.
  For a longer shower, raise the valve's `Max Shower Duration` (up to 60 minutes) and, with
  an Anthem Plus, the controller's own. Its repair notice for a mismatched controller limit
  (added in 0.20) and its `cutoff_*.jsonl` debug journal went with it. (0.22)
- **`kohler_anthem.probe_usage`.** It existed to work out how Kohler's usage-history
  endpoint wanted to be called; the app answered that. (0.20)

**Documentation**

- **[`docs/protocol/`](docs/protocol/README.md)**: a developer reference to the Kohler
  Konnect cloud protocol — sign-in, every endpoint and message, status codes, firmware — for
  the Anthem valve, the Anthem Plus controller and the Sensate faucet, written to be reused
  by other Kohler integrations. (0.20)
- **The user guide explains the three multi-zone naming choices**, with an example of each.
  (0.26)
- **`docs/` covers using the integration** — the user guide, the valve command word
  reference for `send_valve_hex` and how to capture diagnostics for a bug report — rather
  than the protocol research it was built from. (0.02)

### From Kohler Sensate, 0.1.0 to 0.10.0

What the faucet integration added and changed, in the terms Kohler Konnect uses: its
*instant updates* are the account's MQTT connection, its `kohler_sensate.dispense` action is
`kohler_konnect.dispense`, and its *Firmware* entity is `Firmware status`. Version numbers
are Kohler Sensate's.

**Added**

- **Water on and off** with the `Water` switch, and a **water safety limit** (default 10
  minutes, 0 = off): water turned on from Home Assistant is turned off after that long
  unless it was turned off first. The deadline survives reloads and restarts, and a failed
  attempt is retried every minute. (0.2.0)
- **Measured dispenses**: quick-dispense buttons, a `Dispense amount` setting with
  `Dispense set amount`, and the `dispense` action, which takes `amount` and `unit` (`ml`,
  `l`, `fl_oz`, `cup`, `qt`, `gal`) and `device_id` to choose a faucet (0.2.0), or `preset`
  to pour a preset saved in the Konnect app by name (0.7.0).
- **Konnect presets**, chosen from a `Preset` dropdown and poured with one `Dispense preset`
  button, so any number of presets fits in two entities (0.6.0). They come from the faucet's
  own preset list, as the app's faucet screen reads them, with the account-wide list as a
  fallback (0.10.0).
- **`Leak`, with a `Clear leak alert` button.** `Leak` is on for leak events you haven't
  cleared, and cleared events are remembered across restarts (0.2.0). It turns on as soon
  as Kohler sends its real-time leak alert, and has a `last_detected` attribute (0.10.0).
- **`Dispensing`** follows dispenses started from Home Assistant until the faucet reports
  the water off, and presets run from the Konnect app, with the preset's name. (0.4.0)
- **`Last dispensed`** records every dispense from Home Assistant and survives restarts.
  (0.2.0)
- **`Status` and `Handle`** sensors with translated states, `off`/`on` and `open`/`closed`
  (0.4.0), and a **`Connected`** diagnostic sensor: when Kohler reports the faucet offline,
  its live entities go unavailable and commands fail with a clear "faucet is offline" error
  (0.2.0).
- **A firmware update entity**, asking the firmware check the Konnect app uses whether
  newer firmware is available. Updates are still installed from the app. (0.4.0, 0.10.0)
- **Faucets with SKU `SET`** (probably the Setra), which the Konnect app handles like the
  Sensate. Commands send each faucet's own SKU instead of always `SEN`. (0.10.0)
- **Clear errors when Kohler won't carry out a command**: the faucet is offline, firmware is
  updating, water couldn't be dispensed, or commands are refused for this sign-in. (0.10.0)
- **Repair notices** when the faucet is no longer on the Kohler account, or when Kohler
  keeps rejecting requests (likely an API change). (0.2.0)
- **A diagnostics download**, with personal data redacted, listing every status, progress
  and handle value seen, the presets and the instant-updates state. (0.2.0)
- **Translations** in Dutch, French, German, Italian, Polish, Brazilian Portuguese, Swedish
  and Spanish (0.2.0), and Latin American Spanish, which calls the faucet a *llave* (0.5.0).

**Changed**

- **Instant updates are always on**, as in the Konnect app, with polling beside them as a
  safety net. On a Sensate on firmware 16.0, on/off and dispenses show up within a second
  or two. (0.3.0, 0.10.0)
- **Polling adapts**: every 30 seconds when idle and every 5 while water runs or just after
  a command. Once instant updates have proven they deliver, idle polling relaxes to 5
  minutes and active polling to 30 seconds; if a poll finds a change they never announced,
  polling returns to its usual pace and diagnostics count the miss. (0.2.0, 0.3.0)
- **Kohler's `Retry-After` is honored** when it throttles requests (30 seconds to 15
  minutes), and commands report how long to wait. (0.2.0)
- **Dispenses and presets can be up to 3 gallons (11.36 L)**, the Konnect app's limit,
  instead of 4 L. Larger dispenses count as dispensing for longer if the faucet never
  reports the water off. (0.10.0)
- **Turning the water on or dispensing is refused while the handle is closed** or the faucet
  is downloading firmware, as in the Konnect app. Turning the water off always works.
  (0.10.0)
- **`Dispense amount` and `Preset` are configuration entities**, the settings for `Dispense
  set amount` and `Dispense preset`, rather than controls. (0.8.0)
- **`Dispense progress` is `Firmware download`**, a diagnostic sensor disabled by default:
  Kohler's `progress` field follows firmware downloads, not the water. (0.4.0, 0.10.0)
- **`Clear leak alert` also clears a leak detected before it was pressed** that Kohler only
  lists later. `Leak`'s `latest` attribute is the most recently detected event, whatever
  order Kohler lists them in. (0.10.0)

**Fixed**

- **Konnect presets never appeared**: the integration asked Kohler for them at an address
  that doesn't exist. (0.4.0)
- **`Dispensing` never turned on**, because the Sensate doesn't report dispense progress.
  (0.4.0)
- **`Water used today` starts over at midnight**, instead of showing the previous day's
  figure for up to 30 minutes. (0.5.0)
- **The firmware entity shows Home Assistant's update icon** instead of the integration's
  logo. (0.5.0)
- **Spanish uses the words used in Spain**, such as *grifo*, instead of the English
  *faucet*. (0.5.0)
- **A network error or Kohler outage while renewing the session no longer asks for a new
  sign-in** and stops polling. Only a rejected sign-in does. (0.2.0)
- **The session is renewed before it expires**, an HTTP 401 renews it and retries once, and
  concurrent requests share one sign-in instead of racing. (0.2.0)
- **With several faucets, `dispense` no longer runs water at all of them.** (0.2.0)
- **The `dispense` action exists before a faucet finishes loading**, and gives a clear error
  instead of "unknown action". (0.2.0)
- **`Leak` shows unknown instead of "dry"** until the leak history has been read. (0.2.0)
- **Unrecognized faucet statuses** — an error or offline state, say — no longer show the
  water as on or trigger fast polling. (0.2.0)
- **Refreshes requested by commands run within a second**, instead of up to 10. (0.2.0)
- **A failed firmware or leak-history request no longer makes every entity unavailable.**
  (0.2.0)
- **Setup no longer crashes** if Kohler reports firmware as an object. (0.2.0)
- **`Dispense amount` restores correctly** after a restart. (0.2.0)
- **When the instant-updates connection drops, the faucet is re-read at once** and polling
  returns to every 30 seconds, instead of waiting up to 5 minutes. (0.3.0)
- **A firmware status in Kohler's `progress` field no longer makes `Water` and
  `Dispensing` show the water running** during a firmware update. (0.10.0)
- **A command Kohler answered with HTTP 200 but refused in the reply**, such as "water could
  not be dispensed", is no longer taken as carried out. (0.10.0)
- **Messages that don't name a device no longer count as proof** that instant updates
  report this faucet. (0.10.0)
- **An app preset reported as anything but `OFF` counts as running**, as in the Konnect
  app. (0.10.0)
- **The `dispense` action's out-of-range message** shows its limits without exponents,
  rounded so every amount it states is accepted. (0.10.0)

**Security**

- **Diagnostics redact the device ID, account email and account ID wherever they appear**,
  not just in known fields. (0.2.0, 0.2.1)
- **Debug logs leave out request payloads carrying the account ID**, and never log Kohler's
  account replies (home address, coordinates, Wi-Fi name). Error messages never include
  credentials or tokens. (0.2.0)

