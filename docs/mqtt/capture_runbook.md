# Capturing diagnostics

How to gather evidence for a bug report, or to see exactly what Kohler's cloud is sending.
Four tools cover this, from quickest to most detailed.

## Download diagnostics

Every device page and the integration card have a **Download diagnostics** button. It
produces one JSON report describing the whole installation — model and outlet split as
detected, what each device is reporting, configured limits — with credentials, account
identity, and device serial numbers redacted.

This is the single most useful thing to attach to an issue, especially on hardware other
than a K-28212.

## The Report Log switch

The quickest way to capture live MQTT traffic for a bug report. A **Report Log** switch sits
on the valve and controller device pages, in the diagnostic section. It records every message
on the account, faucets' included, so a faucet-only problem is captured from there too:

* **Switch on** → a new capture file starts, recording every raw MQTT message as received,
  before any decoding.
* **Restart Home Assistant mid-capture** → the same file continues, so a restart never
  splits the evidence.
* **Switch off** → the capture ends. The next switch-on starts a fresh file.

Files land in `custom_components/kohler_konnect/reports/` (a `README.txt` there explains the
format), one per capture, capped at 8 MB with continuation parts.

**Check the file before sharing it** — it contains your device identifiers and shows when
water was used. The folder lives inside the integration itself, so **updating or
reinstalling the integration deletes it**; move files you want to keep first.

## The REST debug logger

For a lower-level view of the REST half — every call to Kohler's API, with the full response
body, including fields the integration doesn't read — turn on the client's debug logger from
**Developer Tools → Actions**, `logger.set_level`, in YAML mode:

```yaml
action: logger.set_level
data:
  # Every REST call: endpoint, status, and the full response body.
  custom_components.kohler_konnect.konnect.client: debug
```

It is off by default and doesn't survive a restart. Set it back to `info` to stop. Read the
results in **Settings → System → Logs**, or in `home-assistant.log`.

Use it to answer questions the diagnostics report can't — whether a field exists at all,
what an undocumented value looks like on your hardware, or what an endpoint returns on a
system unlike the reference install.

**Credentials are redacted** before anything is written: the mobile-settings call returns a
short-lived IoT Hub password, and any key or value that looks like a password, token, key or
secret is replaced.

Everything else is logged in full — including device ids and serial numbers, which the
diagnostics report redacts but a raw debug log does not. **Skim a log before attaching it to
an issue.** A Kohler device id is not merely an identifier: it is the address the cloud uses
to reach your device.

The ordinary `home-assistant.log` stays free of device ids at INFO level and above; turning
the debug logger on puts ids and serials in that file deliberately, and they stay there
until it rotates.

## The raw MQTT capture

For MQTT traffic over a longer stretch than one bug report — every payload exactly as it
arrived, before any decoding — the raw capture writes `mqtt_raw_*.jsonl` files to
`/config/kohler_konnect_raw/`, beside a `README.txt` explaining the format. It is off by
default. A logger level is its switch, though nothing goes to Home Assistant's log:

```yaml
action: logger.set_level
data:
  custom_components.kohler_konnect.konnect.raw_log: debug
```

Set it back to `info` to stop; a restart stops it too. Each file is capped at 8 MB and rolls
over to a new one, and nothing is ever pruned, so turn it off when you're done. The
`Start new MQTT capture` button — on the first valve's device page, or the first controller's
on an account without a valve — starts a fresh file, which helps before a deliberate
experiment.

The **warmup journal** works the same way: `custom_components.kohler_konnect.konnect.journal`
at `debug` writes `warmup_*.jsonl` to the same folder, recording every change to a valve's
warmup mode with the MQTT traffic either side.

Like the Report Log, these files carry device ids. Check them before sharing.
