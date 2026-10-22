# Changelog

Notable changes for each tagged release. Versions correspond to git tags and to the
`version` field in `custom_components/kohler_konnect/manifest.json`. Add entries under
**Unreleased** as part of each change; the release workflow rotates that section into a
version heading and publishes it as the release's Highlights.

Every push to `main` that passes CI is released. To choose the version, set it in
`manifest.json` and it is released as written; leave it alone and the minor version is
bumped (1.0 → 1.01). Tags and release names are the bare version — `1.0`, never `v1.0`.

## Unreleased

## Unreleased

**Added**

- **`Duration Used Today`, `This Week`, `This Month` and `This Year`** for every Anthem valve —
  how long the water ran, in minutes, alongside the `Water Used` totals. They are the water
  totals' twins: the same `gcs-usage` series, the same periods (local-midnight today, a rolling
  seven days, the calendar month and year), the same refresh after a shower, and no extra API
  call — only the figure differs, the `onDuration` seconds each usage bucket already carries,
  published in minutes as the Konnect app charts it. `onDuration` and its seconds unit were
  verified against real `gcs-usage` replies from three live GCS valves (reverse-engineered
  alongside the app's own chart, which divides by 60); the sensors themselves are covered by
  the unit and integration suites. A `duration` device class, so the Energy dashboard's water
  picker leaves them out; `TOTAL` with the same `last_reset` discipline as their water twins,
  so long-term statistics record each period's restart rather than negative time.

## 1.10 — 2026-10-22

**Fixed**

- **A Report Log with an unreadable saved name no longer starts a new file at every
  restart.** It starts one new file, as before, and now remembers it, so later restarts
  carry on in that file.
- **An Anthem Plus controller's zone `Temperature` reads correctly on a Celsius account.**
  It was labelled in the account's unit, so a controller reporting °F showed, for example,
  102 °C. It now reads right whether the controller reports °F or °C there; Fahrenheit
  accounts are unchanged.

**Documentation**

- **`Shower on` matches Kohler's own controls.** On a real valve, Simple Start in the
  Konnect app, the touchscreen and `Shower on` all started exactly the same default shower:
  the same outlets, temperature and flow.
- **Starting a valve experience from Home Assistant is confirmed to work** on a real valve.
  Stopping one from the `Experience` dropdown hasn't been tried yet.

## 1.09 — 2026-10-22

**Added**

- **Diagnostics show the valve's default shower.** Each valve's report has a
  `default_shower` section: the outlets, temperature and flow `Shower on` would start, or why
  it would refuse. 1.08's reports only counted it among the presets, so they couldn't say
  whether `Shower on` would work.

## 1.08 — 2026-10-21

**Fixed**

- **`Shower on` no longer changes a setting on your valve.** Setup used to rewrite the hidden
  run time of the valve's default shower to 60 minutes, without asking, whenever it was
  anything else. That was because `Shower on` ran the default shower as a favorite, so its
  hidden 30 minutes could end the shower early. `Shower on` now starts the default shower the
  way Simple Start in the Konnect app does: the default outlets at the default temperature
  and flow, in one command, with nothing written to the valve. Only `Max Shower Duration`
  ends it, and `Preset Active` stays off while it runs. If the valve hasn't reported its
  defaults, the switch says so and sends nothing. A run time an earlier version raised is
  left as it is: nothing uses it, and the Konnect app sets its own 30 minutes again the next
  time you change your default settings.

## 1.07 — 2026-10-17

**Documentation**

- **The Energy dashboard.** `Water Used Today`, `This Month` and `This Year` are now tested
  against Home Assistant's own Energy dashboard checks and statistics, for valves and faucets
  alike, through a local midnight: each litre is counted once. The user guide says which to
  pick (one per device) and to leave out `This Week`, whose rolling window would show
  negative water.

## 1.05 — 2026-10-17

**Added**

- **`Clear Error Log`** on each Anthem valve, a configuration button that clears the valve's
  error log as the Konnect app's Diagnostics screen does. Its `Problem` sensor follows,
  though a fault the valve still has shows again. Anthem Plus controllers get the same
  button: Kohler's API has the call for them too, but the app wasn't found to offer it and
  it hasn't been tried on one.
- **`Gateway firmware`** on each Sensate faucet, a diagnostic sensor with the firmware
  version the Konnect app shows under that name.

## 1.04 — 2026-10-17

**Fixed — safety**

- **The faucet's water safety limit never gives up.** In 1.03 it stopped after ten failed
  attempts and forgot the limit, so water could keep running once Kohler's cloud came back.
  It now retries after 1, 2, 5 and 10 minutes, then every 15 minutes, across restarts and
  reloads, until the water is known to be off; an off command Kohler accepts must be
  confirmed by a reading — the `Water` switch's own off too, which used to end the limit
  the moment Kohler took it, faucet running or not; and after the third failed try a repair
  says the water may still be running. A reading taken before Kohler's copy of the faucet
  has caught up with Home Assistant's on command can still say "off", or "running" from a
  run that ended just before it. Once the water has shown as running, such a reading now
  counts only a minute after the command, or straight after the `Water` switch turns the
  water off, and the live stream's word at once. That applies
  to the deadline's own check too: with a 1-minute limit and the cloud 45 s behind, a stale
  "off" used to drop the limit with the water running.
- **The faucet's limit no longer cuts off someone using the faucet by hand**: it is dropped
  when water Home Assistant turned on never shows as running, or stopped while Home
  Assistant was restarting or the stream was down. It is armed before the on command is
  sent, so a reload during that command keeps it. Setting the limit to `0` drops one left
  standing from before, and clears it from storage. Turning the limit back on later can no
  longer bring the old deadline back and cut off whoever is using the faucet by then.
- **A Stop can no longer be undone by a late valve report** (1.03). For about 5 s after a
  command, a report that arrives in between can close an outlet or keep a pause, but never
  reopen one. Nor by a stale read: one sent seconds after the valve reported keeps what the
  valve said, and a report whose zone 2 word is garbled still delivers zone 1's Stop. That
  rule goes by when a report arrived: one held at startup no longer keeps a zone stopped at
  the wall since shown running. It also keeps only what the report carried. A Stop that
  fails during a warm-up, just as the warm-up ends, no longer leaves every warm-up outlet
  open for the next command.
- **A Stop goes first.** Valve commands asked for before it and not yet sent are dropped
  ("superseded by a Stop").
- **A slow read-back can no longer loosen the scald limit** (1.03). A read that has not
  caught up never replaces a value written from Home Assistant, and each outlet setting has
  its own "may not have been applied" repair, so verifying one no longer clears another.
  Nothing is written if any outlet's record would be refused. A setting the valve still
  hasn't confirmed is checked before another is sent, and if the valve reports something
  else — it was changed in the app, say — changing any other outlet setting is refused until
  that one is set again, rather than re-sending a looser scald limit over it. That check, the
  read-back and a reconnect's read now go by the valve's own outlet announcements over the
  cloud's copy, which can lag them by half a minute. Before, a lagging read put back the
  higher limit the app had just lowered, and the next write sent it to every outlet. A change
  the valve announces soon after one made here is taken as the app's, unless it shows the old
  value (a late echo), and the next reading checks that guess. Two settings in dispute no
  longer lock every outlet setting: setting either one again sends the other as the valve
  reports it. While the MQTT connection is down, an outlet setting reads the valve's settings
  first. For half a minute after the connection comes back, outlet settings are refused, so
  the cloud's copy can catch up with anything changed in the app meanwhile. A first
  connection that comes long after startup, because the first attempt failed, re-reads the
  outlet settings instead of trusting ones the app may have changed meanwhile.
- **Changes during a warm-up no longer turn it into a shower on every warm-up outlet.** They
  keep the outlets of the shower Home Assistant started; if the warm-up was started
  elsewhere they are refused until it ends. Stop always goes through. A zone stopped or
  paused at the wall meanwhile stays that way, and a command that fails during the warm-up
  doesn't lift the refusal. A warm-up mode read back from the cloud no longer clears the
  valve's own "warming up".
- **A cancelled switch or select command** — a script stopped, an automation restarted — no
  longer leaves the entity showing a position the valve never reached.
- **An unreadable zone-2 outlet count no longer turns a two-zone valve into a one-zone
  one**, which since 1.03 deleted its zone devices and left zone 2 out of reach. The
  confirmed model is kept, or setup asks.
- **The default shower is never rewritten from a preset holding a bad valve word**, which
  could remove zone 2 from it.
- **A faucet's "Dispense preset" no longer pours a deleted preset's amount** after a single
  failed read of its presets.

**Fixed — regressions in 1.03**

- **`Water Used This Week` keeps long-term statistics again.** 1.03 removed its state
  class to stop a rolling window's daily change ever reading as negative use, and Home
  Assistant then reported that it "no longer has a state class". If you saw that, leave it:
  after updating it clears itself and the old statistics carry on (nothing was recorded
  while 1.03 ran). Use `Water Used Today` for the Energy dashboard.
- **A read after a reconnect no longer throws away newer updates** — a stopped shower
  showing open, a warm-up change lost — and no longer ends the protection of a command
  just sent.
- **Entities on every device recover** after a failed refresh, not only the next device to
  send a message.
- **Home Assistant stopping during setup** no longer leaves the MQTT connection running.
- **A read that failed at startup is tried again when the MQTT connection comes up**, as
  1.03 did: outlet settings, the Favorite list and a controller's accessories no longer
  stay unavailable until the next reconnect.
- **Unloading or reloading during a token refresh** no longer sets the entry up again,
  reloads it twice, or logs an error on removal. While an entry unloads, actions
  (`send_valve_hex`, `custom_shower`, `dispense`) no longer reach its devices. An action
  that names no device is no longer sent to another account's only valve or faucet instead.
  A `custom_shower` keep-on watcher no longer outlives the unload.
- **A reconnect during the read that follows a reconnect** now reads again once that read
  ends, rather than skipping its own catch-up.

**Fixed**

- **`Water Used Today`** no longer counts an evening's water twice after a restart on
  accounts whose daily totals Kohler files by UTC date; east of UTC, water used between
  local and UTC midnight is counted on the right day.
- **`Water Used This Year`** reads unknown while Kohler's monthly history cannot be read,
  rather than a partial year that statistics would record as a large drop and then a jump;
  a month that has ended no longer dips at the turn of the month.
- **Celsius sliders stop at 47 °C under a 47.7 °C scald limit**, never above it, and never
  offer a value below an outlet's minimum.
- **Adding an account no longer leads straight to a sign-in prompt**, and a reload during a
  token refresh no longer forces one.
- **Re-authentication** keeps its own form, with an Email field: a changed Kohler email is
  accepted when it is the same account (and carried into the entry's title, unless you
  renamed it), and entries damaged by the reauth bug fixed in 1.03 can sign in again. A
  sign-in error Kohler sends as a bare number is read as its code, so a blocked sign-in no
  longer reads as a wrong password.
- **The MQTT connection** can no longer stop reconnecting for good after a badly timed
  reconnect, and unloading no longer waits up to a minute behind a stuck connection; a
  reconnect waits for an abandoned attempt to end, so it can't knock its replacement off.
  It never starts a second connection beside one still trying, and logs a warning every two
  minutes while it waits, so a lookup that hangs no longer leaves the stream down silently.
  The `MQTT Connection` sensor now shows a gap while it lasts.
- **Problem sensors:** a valve's fault log is read twice a day as well as at startup, and a
  controller whose error read fails keeps the errors already known instead of reading OK.
  Diagnostics report an unreadable fault log as an error, not as "no faults".
- **`Cloud Connection`** is no longer turned off by a `Disconnected` answer older than the
  valve's latest message.
- **Warmup Auto-Restore** finishes a restore that was waiting for a shower to end, even
  across a restart; keeps an Off chosen in Home Assistant even when it learns of it late;
  and leaves the old delayed-start modes alone (with one warning) instead of failing to
  restore them.
- **Controllers:** a degraded configuration reply no longer hides Music, Light and Steam
  until a reload; a layout learned after setup reloads the entry once, as valves do; a
  favorite switch sent as one message shows the new favorite; an outlet sent as `"1.0"`
  reads as on.
- **Malformed replies** — `NaN` or `Infinity` in a number, a sign-in reply that is not an
  object, an error code sent as a number — no longer break setup, a later command or a
  sensor.
- **Capture files** opened before a sign-in that fails are closed, rather than a new one
  left open on every retry.
- **Faucets:** a re-read a message asked for that fails is retried after a pause, and alone,
  rather than at every poll with every other read. So is a first read of the configuration
  or the presets that failed. A configuration Kohler wouldn't serve used to be read again,
  with both preset lists, every 5 s while the water ran. A re-read failing for days no
  longer stops the faucet updating. A faucet whose presets were all deleted stays without
  after a restart, rather than taking the account's old list. Any other refusal after a
  restart still falls back to that list instead of leaving the presets empty, but only with
  the presets the faucet's own list last held.
- **Outlet-setting repair notices** clear when Home Assistant restarts, along with what they
  were about. One said no other setting could be changed, which was no longer true.
- **`custom_shower`** encodes flow against each zone's own ceiling, like every other
  command (the same on every valve seen so far).

**Changed — efficiency**

- **Each device is read once at startup**, not twice: the MQTT connection now starts
  alongside the startup reads, which also takes a round trip off setup.
- **A faucet remembers that instant updates work for it**, so after a restart it polls
  every 5 minutes instead of every 30 s until it is next used (about 12 reads an hour
  rather than 120), and the startup no longer mistakes a change made before the stream was
  up for one it missed.

## 1.03 — 2026-10-16

**Fixed — safety**

- **Changing a temperature or flow no longer starts water on a paused zone.** Every command
  re-sends both zones, and the pause the valve holds after a warm-up (or an app or panel
  pause) was dropped, re-opening the paused outlet. An outlet switched on in a paused zone
  now opens only that outlet.
- **A second outlet setting no longer undoes the first.** Setting Max Temperature and then
  Max Shower Duration within about 30 s put the old Max Temperature back, because each
  write echoed values the cloud had not caught up with. Writes are queued, each builds on the
  last, and a Max/Default Temperature pair the valve would refuse is caught before anything
  is sent.
- **"No pausing warm-up" no longer restarts a shower someone stopped.** The Konnect app and
  the first-generation touchscreen stop a shower with the same pause a warm-up ends in. The
  shower is now re-sent only when the valve reports at temperature as the warm-up ends;
  otherwise it stays paused and the log says why.
- **The faucet's water safety limit no longer turns off someone else's use.** It is dropped
  when the stream sees the water run and stop, or when the water was turned off while Home
  Assistant was down; no off command goes to a faucet already known to be off; failed
  attempts stop after ten; nothing is sent after unload.

**Fixed**

- **Valve commands sent close together all take effect.** Several outlet switches in one
  action, or an outlet followed quickly by a temperature change, used to leave only the last
  one standing ([issue #1](https://github.com/kedube/ha-kohler-konnect/issues/1)). With the
  valve's warm-up on, `custom_shower` is still the way to send a whole shower.
- **Temperature changes and outlet switches keep the Flow you set** instead of resetting it
  to 100 %. `Flow 2` reads zone 2's own outlets, and an idle Flow shows the value chosen.
- **Fahrenheit temperatures match the Konnect app** (38.6 °C is 101 °F, not 102), and a
  Celsius slider no longer reaches past a fractional Max Temperature.
- **Valve faults the Konnect app shows now reach `Problem`.** Active entries in the valve's
  fault log turn it on, listed in `active_faults`.
- **`Water Used This Week` no longer records negative use.** It has no state class now — a
  rolling window falls as old days leave it. Home Assistant raises a repair for its old
  statistics; point an Energy water source at `Water Used Today` instead.
- **`Water Used Today` no longer counts an evening's water twice** on accounts whose daily
  totals Kohler files by UTC date.
- **`Cloud Connection` turns on as soon as a power-cycled valve speaks**, rather than up to
  three hours later.
- **Warmup Auto-Restore waits out a running shower** and restores afterwards, instead of
  giving up; an Off chosen during its one-minute wait is kept.
- **Re-authentication stays on its own form.** A mistyped password no longer drops to the
  full sign-in, which could attach a different Kohler account to the entry or change the
  valve model; the dialog closes at once and the reload follows.
- **The MQTT stream survives a malformed message** and no longer runs two reconnect loops at
  once, which tore down a healthy reconnection and reseeded twice. A connection that keeps
  dropping straight after connecting backs off.
- **Sign-in is sturdier:** several requests rejected together cause one token refresh, a
  refresh interrupted by a reload no longer loses the new token, and a timed-out token
  request no longer asks you to sign in again.
- **Faucets:** state no longer flickers back from a slow poll; water reports no longer
  trigger configuration and preset reads; status values seen only on the stream no longer
  break the sensors; a dispense ends on time even if an app preset ran meanwhile; the chosen
  preset survives a rename across restarts; a restored amount stays inside the unit's range.
- **Faucet diagnostics never include IoT Hub connection details.**
- **A valve whose layout is learned after setup reloads once** so its entities match, and
  entities for a zone or outlet that disappeared go unavailable instead of failing.
- **Startup is faster**: independent reads for each valve, controller and faucet now overlap.
- **Monthly usage and part details recover** on the next read after a failure at startup.
- **Less work per message:** an MQTT message updates only its own device's entities.
- **A malformed reply from Kohler** no longer fails setup or the setup flow, and a lowercase
  `connected` no longer hides controller accessories.

**Added**

- **Devices no longer on your Kohler account can be deleted** from their device page.
- **Diagnostics** carry each valve's cloud-connection details and active faults, and a
  faucet's page is labelled as that faucet.

## 1.01 — 2026-10-09

**Changed**

- **The integration page's button reads Add account**, not Add hub, in every language.

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

