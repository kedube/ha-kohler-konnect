"""Azure IoT Hub status stream for every Kohler Konnect device on an account.

Kohler pushes device status as IoT Hub **direct methods**, all of them on one account-level
topic. A single connection therefore carries every device on the account — valves,
controllers and faucets alike — and callers filter by ``sku`` and ``device_id``.

Three behaviours drive the design, each learned the hard way:

* **Hold the connection, and reuse the identity.** A connect-per-command client receives
  nothing, ever. The identity is registered once and persisted by the caller rather than
  generated per connect, which previously left a dead registration behind on every restart.
  A *newly* registered identity may not receive for a short while; a reused one is already
  provisioned. See :attr:`KonnectMqttStream.warming_up` for how thin that evidence is.
* **One topic.** Only ``$iothub/methods/POST/#`` delivers. Device-scoped topics are
  acknowledged and stay silent.
* **Acknowledgement.** Direct methods must be answered on
  ``$iothub/methods/res/200/?$rid=N`` or the service will consider them unhandled.

The transport is paho-mqtt, which runs its own network thread. Callbacks are marshalled
onto the owning event loop so consumers never touch that thread. Reconnecting is this
class's job, never paho's: every connect needs a freshly registered SAS password, which
paho's own reconnect cannot fetch.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import paho.mqtt.client as mqtt

from .auth import AuthError, AuthUnavailable
from .client import KohlerClient
from .const import (
    MQTT_PORT,
    MQTT_RESPONSE_TOPIC,
    MQTT_SUBSCRIBE_TOPIC,
    MQTT_WARMUP_SECONDS,
)
from .raw_log import RawMqttLog
from .report_log import ReportLog

_LOGGER = logging.getLogger(__name__)

_RID = re.compile(r"\$rid=([^&]+)")

# Reconnect backoff. The SAS token from registration is short-lived, so a reconnect
# re-registers rather than reusing stale credentials.
RECONNECT_MIN_SECONDS = 5
RECONNECT_MAX_SECONDS = 300
# How long a connection must last before the backoff starts again from the bottom. Until
# 2026-10-16 every CONNACK reset it, so a broker that accepts the client and then drops it
# at once — IoT Hub does exactly that to the older of two connections sharing a client id,
# such as two Home Assistant instances holding the same persisted identity — drove a
# reconnect, a re-registration and a full reseed every few seconds, indefinitely.
STABLE_CONNECTION_SECONDS = 60
# Backstop for a connection attempt that ends without paho saying so; see
# `_check_connected`. Longer than anything paho reports on its own: a TLS handshake or a
# missing CONNACK each give up after the keepalive, and DNS can add its own wait.
CONNECT_TIMEOUT_SECONDS = 120
KEEPALIVE_SECONDS = 60
# How long stopping a client waits for paho's network thread to end. Told to stop, it ends
# within about a second — unless it is inside a connection attempt, which nothing can cut
# short: DNS has no timeout of its own, TCP gives up after paho's 5 s and a TLS handshake
# only after the keepalive. Until 2026-10-16 that wait was unbounded, and it held unload,
# reload and Home Assistant's shutdown for up to a minute. Past this the thread is left to
# finish by itself: the connection callbacks ignore a client this stream has let go of,
# and every callback ignores everything once the stream is stopping.
STOP_TIMEOUT_SECONDS = 5

# Building an SSL context reads the system CA bundle from disk. That is blocking file I/O,
# so it must never happen on an event loop — paho's `tls_set()` does exactly that
# internally, which stalls every other task while it runs. Build the context once in a
# worker thread and hand paho the finished object via `tls_set_context()` instead.
_ssl_context: ssl.SSLContext | None = None
_ssl_lock = asyncio.Lock()


async def async_default_ssl_context() -> ssl.SSLContext:
    """Return a shared TLS context, created off the event loop on first use."""
    global _ssl_context
    async with _ssl_lock:
        if _ssl_context is None:
            _ssl_context = await asyncio.to_thread(ssl.create_default_context)
        return _ssl_context


def _contained[**P](what: str) -> Callable[[Callable[P, None]], Callable[P, None]]:
    """Log, rather than raise, anything a paho callback throws.

    paho-mqtt 2.x re-raises a callback's exception (``suppress_exceptions`` defaults to
    False), and on the thread ``loop_start()`` runs that ends the thread outright: no
    ``on_disconnect``, so ``connected`` stayed True and no reconnect was ever scheduled.
    Until 2026-10-16 one malformed message — ``data.attributes`` sent as a number — left
    the stream dead until the entry was reloaded. Each callback now contains its own
    failure, and ``suppress_exceptions`` is set as well, as a backstop.
    """

    def decorate(callback: Callable[P, None]) -> Callable[P, None]:
        @functools.wraps(callback)
        def contained(*args: P.args, **kwargs: P.kwargs) -> None:
            try:
                callback(*args, **kwargs)
            except Exception:
                _LOGGER.exception("Kohler MQTT %s handler raised", what)

        return contained

    return decorate


def _log_stop_failure(stopping: asyncio.Future[None]) -> None:
    """Log a client stop that raised; see `KonnectMqttStream._async_stop_network`."""
    if not stopping.cancelled() and (err := stopping.exception()) is not None:
        _LOGGER.warning("Kohler MQTT did not stop cleanly", exc_info=err)


@dataclass(frozen=True)
class Envelope:
    """One decoded status message."""

    sku: str
    device_id: str
    code: str
    attributes: list[dict[str, Any]]
    received_at: float
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    #: When it arrived on the monotonic clock, for comparing with other monotonic times: a
    #: wall-clock age taken from `received_at` shifts with every clock step (2026-10-16).
    received_monotonic: float = field(
        # Looked up at each call, not bound at import, so a test's frozen clock reaches it.
        default_factory=lambda: time.monotonic(),
        repr=False,
        compare=False,
    )

    def attribute(self, code: str | None = None, **match: Any) -> dict[str, Any] | None:
        """First attribute matching ``code`` and any extra field equalities."""
        for item in self.attributes:
            if not isinstance(item, dict):
                continue
            if code is not None and item.get("code") != code:
                continue
            if all(item.get(k) == v for k, v in match.items()):
                return item
        return None


class KonnectMqttStream:
    """Holds one long-lived IoT Hub connection and dispatches decoded envelopes.

    Usage::

        stream = KonnectMqttStream(client, on_envelope)
        await stream.async_start()
        ...
        await stream.async_stop()
    """

    def __init__(
        self,
        client: KohlerClient,
        on_envelope: Callable[[Envelope], None],
        *,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_auth_error: Callable[[AuthError], None] | None = None,
        mobile_device_id: str | None = None,
        raw_log: RawMqttLog | None = None,
        report_log: ReportLog | None = None,
        expect_warmup: bool = True,
        loop: asyncio.AbstractEventLoop | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._client = client
        self._on_envelope = on_envelope
        # Called on the owning loop after every successful connect, including reconnects.
        #
        # This exists because **the broker replays nothing on connect**. Measured across 27
        # sessions: the first message is always a change event, never a state dump — 6
        # sessions received nothing at all, and the worst wait was 11.9 hours. Without a
        # read triggered from here, a consumer is blind from connect until somebody next
        # uses the shower.
        self._on_connect_cb = on_connect
        # Called on the owning loop when an established connection drops. A consumer that
        # relaxes its own polling while the stream delivers — the faucets do — uses it to
        # pick the pace back up at once instead of after its long interval.
        self._on_disconnect_cb = on_disconnect
        # Called on the owning loop when a reconnect fails because the stored credential was
        # *rejected* — not merely because Kohler was unreachable.
        #
        # Without this the reconnect loop is a silent trap: every connect needs a fresh SAS
        # token, minting one needs a valid access token, and a dead refresh token therefore
        # fails every attempt forever while logging at WARNING. Nothing else in the push-only
        # design ever exercises the credential, so there is no other moment at which a
        # consumer could notice and prompt the user.
        self._on_auth_error_cb = on_auth_error
        # Latched so one outage produces one notification rather than one per retry.
        self._auth_error_reported = False
        self._loop = loop
        self._ssl_context = ssl_context
        self._mqtt: mqtt.Client | None = None
        # Guards which client is current — `_mqtt` — together with that client's state,
        # `connected` and `_connected_at`. paho's thread tests "is this still my client?"
        # and then records a connect or a drop, while the owning loop swaps the client out,
        # and until 2026-10-16 the two could interleave: a CONNACK that passed the test
        # just before the swap recorded `connected = True` just after it, for a client
        # already let go. Nothing cleared that, so the next reconnect attempt took it for a
        # healthy connection and gave up — no client, no retry, and a stream reporting
        # itself connected. Held for those assignments only, never across a call into paho:
        # `disconnect()` can call `_on_disconnect` on the calling thread, which would then
        # wait for a lock its own thread holds.
        self._client_lock = threading.Lock()
        self._closing = False
        self._reconnect_task: asyncio.Task[None] | None = None
        self._connect_watchdog: asyncio.TimerHandle | None = None
        self._backoff = RECONNECT_MIN_SECONDS
        self._connected_at: float | None = None
        # Reuse one registered identity instead of a throwaway per connect. None falls back
        # to the old behaviour of generating a fresh one.
        self._mobile_device_id = mobile_device_id
        # RAW MQTT LOG: None disables capture entirely; the object itself is also a no-op
        # until switched on. See konnect/raw_log.py.
        self._raw_log = raw_log
        # REPORT LOG: the consumer-side capture, fed at the same pre-decode point so a
        # user's bug report holds exactly what the development capture would. Independent
        # switches, independent files — see konnect/report_log.py.
        self._report_log = report_log
        # Only a *newly registered* identity can plausibly need warm-up; one that has
        # connected before is already provisioned. Cleared for good by the first message —
        # data arriving is proof the channel works, whatever the clock says.
        self._warmup_pending = expect_warmup
        self.connected = False
        self.last_message_at: float | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def async_start(self) -> None:
        """Register and start connecting; the subscription follows from paho's thread.

        Returns once the connection has been started, not once it is up: `on_connect`
        reports that, from paho's network thread.

        A failure is raised for the caller to report, **and** retried in the background
        on the same backoff as a dropped connection. Until 2026-10-08 it was only raised:
        reconnects were scheduled from the disconnect callback alone, which a connection
        that never came up never reaches, so one failed registration at startup left the
        stream down — and a push-only shower frozen — until Home Assistant restarted.
        """
        self._closing = False
        self._loop = self._loop or asyncio.get_running_loop()
        try:
            await self._async_connect()
        except Exception:
            self._start_reconnect_task()
            raise

    async def async_stop(self) -> None:
        """Disconnect and stop reconnecting.

        Waits for paho's network thread at most `STOP_TIMEOUT_SECONDS`; see there.
        """
        self._closing = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._connect_watchdog is not None:
            self._connect_watchdog.cancel()
            self._connect_watchdog = None
        with self._client_lock:
            client, self._mqtt = self._mqtt, None
            self.connected = False
            self._connected_at = None
        if client is not None:
            client.disconnect()
        # Closing a capture flushes and closes a file, which is blocking I/O, so it runs in
        # a worker thread rather than on the event loop. Only once paho's thread has
        # stopped, too: a message it was still handling would reopen the file just closed.
        # So the same worker does both, in that order — and when stopping outlasts the
        # wait, it still closes the captures, as soon as the thread has ended.
        if (
            client is not None
            or self._raw_log is not None
            or self._report_log is not None
        ):
            await self._async_stop_network(client, then=self._close_captures)

    def _close_captures(self) -> None:
        """Release the capture files. Blocking: run it in a worker thread."""
        # RAW MQTT LOG: release the capture file on unload.
        if self._raw_log is not None:
            self._raw_log.close()
        # REPORT LOG: release the handle only — `close()` does not end the episode, so a
        # reload resumes the same file from the persisted name.
        if self._report_log is not None:
            self._report_log.close()

    async def _async_stop_network(
        self,
        client: mqtt.Client | None,
        *,
        then: Callable[[], None] | None = None,
        timeout: float | None = None,
    ) -> asyncio.Future[None]:
        """Stop `client`'s network thread and then run `then`, in one worker, in order.

        Waits ``timeout`` seconds at most — `STOP_TIMEOUT_SECONDS` unless given — and warns
        if that runs out. The worker carries on regardless, so `then` still runs once the
        thread has ended, and never before. It runs even when stopping raises, so a paho
        error cannot leave a capture open; a failure of either is logged rather than
        raised, because nothing may be waiting for it any more. Returns the worker, for a
        caller that must not go on before it is done.
        """

        def stop() -> None:
            try:
                if client is not None:
                    client.loop_stop()
            finally:
                if then is not None:
                    then()

        stopping = asyncio.get_running_loop().run_in_executor(None, stop)
        stopping.add_done_callback(_log_stop_failure)
        if timeout is None:
            timeout = STOP_TIMEOUT_SECONDS
        done, _ = await asyncio.wait({stopping}, timeout=timeout)
        if not done:
            _LOGGER.warning(
                "Kohler MQTT network thread did not stop within %ss; leaving it to finish",
                timeout,
            )
        return stopping

    @property
    def warming_up(self) -> bool:
        """True while the connection is too young to expect messages.

        Silence during this window means nothing — do not infer device state from it.

        Only ever true for a **freshly registered** identity that has not yet received
        anything, and the first message clears it permanently. A reused identity is already
        provisioned, so it never warms up.

        The 60 s figure is a conservative upper bound, not a measurement, and the evidence
        for it is thin: the original test confounded "too soon after connect" with
        "disconnected immediately after", and five capture sessions on fresh identities
        received their first message inside the window, the earliest at **37 s**. Treat it
        as a cap on how long silence stays uninformative, nothing more.
        """
        if not self._warmup_pending:
            return False
        if self._connected_at is None:
            return True
        return (time.monotonic() - self._connected_at) < MQTT_WARMUP_SECONDS

    async def _async_connect(self) -> None:
        """Obtain fresh credentials and bring up the connection."""
        # The SAS password is short-lived and single-use in practice, so every connect
        # fetches fresh credentials — but for the *same* identity when one was supplied.
        settings = await self._client.async_register_mobile_device(
            self._mobile_device_id
        )
        host = settings["ioTHub"]
        device_id = settings["deviceId"]

        # Callback API version 1: the signatures below. paho-mqtt 2.x still supports it (with
        # a DeprecationWarning when it has to assume it, hence naming it); moving to version
        # 2 means changing `_on_connect`, `_on_disconnect` and `_on_connect_fail` together.
        # Home Assistant pins 2.x, so the 1.x fallback that sat here could no longer run.
        #
        # `reconnect_on_failure=False`: reconnecting is `_async_reconnect`'s job alone. Left
        # at paho's default, its network thread also reconnected by itself about a second
        # after a drop — reusing the short-lived SAS password — while `_on_disconnect`
        # scheduled `_async_reconnect`, which then tore down whichever client existed. Two
        # loops: a healthy link killed moments after it came back, a spurious disconnect, a
        # second registration and a second full reseed; with an expired password, CONNACK
        # refusals every few seconds. Now a drop simply ends paho's thread. A first connect
        # that fails before any CONNACK (DNS, TCP, TLS) ends it too, reporting only through
        # `on_connect_fail` — and an exception that is not an `OSError` ends it reporting
        # nothing at all, which is what the watchdog armed below is for.
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=device_id,
            protocol=mqtt.MQTTv311,
            transport="tcp",
            reconnect_on_failure=False,
        )
        client.username_pw_set(settings["username"], settings["password"])
        # Never `tls_set()` here: it loads CA certificates from disk on the calling thread.
        context = self._ssl_context or await async_default_ssl_context()
        if self._closing:
            # Stopped while registering: connecting now would leave a client nothing owns.
            return
        client.tls_set_context(context)
        # A backstop: every callback contains its own exceptions — see `_contained`.
        client.suppress_exceptions = True
        client.on_connect = self._on_connect
        client.on_connect_fail = self._on_connect_fail
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        with self._client_lock:
            self._mqtt = client
        client.connect_async(host, MQTT_PORT, keepalive=KEEPALIVE_SECONDS)
        client.loop_start()
        if self._connect_watchdog is not None:
            self._connect_watchdog.cancel()
        self._connect_watchdog = asyncio.get_running_loop().call_later(
            CONNECT_TIMEOUT_SECONDS, self._check_connected, client
        )
        _LOGGER.debug("Kohler MQTT connecting to %s as %s", host, device_id[-16:])

    # ------------------------------------------------------------------ #
    # paho callbacks — these run on paho's network thread
    # ------------------------------------------------------------------ #
    @_contained("connect")
    def _on_connect(
        self, client: mqtt.Client, _userdata: Any, _flags: Any, rc: int, *_: Any
    ) -> None:
        # A CONNACK can arrive after `async_stop`: paho's `reconnect()` sets the state back
        # to CONNECTING, so a `disconnect()` made while an attempt is under way does not
        # stop the CONNACK that answers it. Nor is a client this stream has already
        # replaced its connection, whatever that client reports.
        #
        # Tested and recorded in one step, under the lock the swap takes: see
        # `_client_lock`. The subscription follows outside it — a call into paho.
        with self._client_lock:
            current = not self._closing and client is self._mqtt
            if current and rc == 0:
                self.connected = True
                self._connected_at = time.monotonic()
        if not current:
            return
        if rc != 0:
            _LOGGER.warning("Kohler MQTT connect refused: %s", mqtt.connack_string(rc))
            return
        client.subscribe(MQTT_SUBSCRIBE_TOPIC, qos=1)
        # The backoff is *not* reset here, only once a connection has lasted — see
        # `STABLE_CONNECTION_SECONDS` and `_on_disconnect`.
        # Credentials demonstrably work again; re-arm for the next outage.
        self._auth_error_reported = False
        # Only mention warm-up when one can actually apply. A reused identity is already
        # provisioned, so saying "expect no messages for 60s" would send anyone reading the
        # log looking for a delay that is not there.
        if self._warmup_pending:
            _LOGGER.info(
                "Kohler MQTT connected on a new identity; expect no messages for ~%ss "
                "while it provisions",
                MQTT_WARMUP_SECONDS,
            )
        else:
            _LOGGER.info(
                "Kohler MQTT connected (identity %s, already provisioned — no warm-up)",
                (self._mobile_device_id or "generated")[-8:],
            )
        self._dispatch_connected()

    @_contained("disconnect")
    def _on_disconnect(
        self, client: mqtt.Client, _userdata: Any, rc: int, *_: Any
    ) -> None:
        with self._client_lock:
            if client is not self._mqtt:
                # A client already replaced or stopped, reporting its own teardown.
                return
            stable = (
                self._connected_at is not None
                and time.monotonic() - self._connected_at >= STABLE_CONNECTION_SECONDS
            )
            self.connected = False
            self._connected_at = None
        if self._closing:
            return
        if stable:
            self._backoff = RECONNECT_MIN_SECONDS
        _LOGGER.warning("Kohler MQTT disconnected (rc=%s); reconnecting", rc)
        self._dispatch_disconnected()
        self._schedule_reconnect()

    @_contained("connect-failure")
    def _on_connect_fail(self, client: mqtt.Client, _userdata: Any) -> None:
        """The connection could not be opened at all: DNS, TCP or TLS, before a CONNACK.

        paho reports this nowhere else. With `reconnect_on_failure` off its thread then
        ends, so this is the one chance to retry.
        """
        if self._closing or client is not self._mqtt:
            return
        _LOGGER.warning("Kohler MQTT could not reach the IoT Hub; retrying")
        self._schedule_reconnect()

    @_contained("message")
    def _on_message(
        self, client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        if self._closing:
            # Unloading. The captures are about to be closed, and writing would reopen them.
            return
        # Acknowledge first: the service treats an unanswered direct method as unhandled,
        # and a decode failure should not suppress the acknowledgement.
        match = _RID.search(message.topic)
        if match:
            client.publish(
                MQTT_RESPONSE_TOPIC.format(rid=match.group(1)),
                b'{"status":"received"}',
                qos=1,
            )
        # --- RAW MQTT LOG call site — see konnect/raw_log.py -------------------
        # Before the decode, deliberately: everything below this point is lossy, and the
        # payloads that fail to parse are dropped entirely. Those are the ones worth having.
        if self._raw_log is not None:
            self._raw_log.write(
                message.topic,
                message.payload,
                qos=message.qos,
                retain=message.retain,
            )
        if self._report_log is not None:
            self._report_log.write(
                message.topic,
                message.payload,
                qos=message.qos,
                retain=message.retain,
            )
        # --- end RAW MQTT LOG call site -------------------------------------------

        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _LOGGER.debug("Ignoring non-JSON MQTT payload on %s", message.topic)
            return
        if not isinstance(payload, dict):
            return

        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        attributes = data.get("attributes")
        # A list, or nothing. This used to iterate `attributes or []` whatever it was, and a
        # number or a bool is not iterable: the `TypeError` ended paho's thread.
        if not isinstance(attributes, list):
            attributes = []
        envelope = Envelope(
            sku=str(payload.get("sku") or ""),
            # Lowercase in every capture. `deviceId` is read too, as the faucet integration
            # this merged with always did; nothing has shown that spelling, and it is free.
            device_id=str(payload.get("deviceid") or payload.get("deviceId") or ""),
            code=str(data.get("code") or ""),
            attributes=[a for a in attributes if isinstance(a, dict)],
            received_at=time.time(),
            raw=payload,
            received_monotonic=time.monotonic(),
        )
        self.last_message_at = envelope.received_at
        # Proof the channel delivers. Ends the warm-up for good, not just for this
        # connection: once an identity has received anything it is provisioned, and a
        # reconnect cannot un-provision it.
        self._warmup_pending = False
        self._dispatch(envelope)

    # ------------------------------------------------------------------ #
    # Thread hand-off
    # ------------------------------------------------------------------ #
    def _dispatch(self, envelope: Envelope) -> None:
        """Hand an envelope to the consumer on the owning event loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._safe_callback, envelope)

    def _safe_callback(self, envelope: Envelope) -> None:
        # Hand-offs queued before `async_stop` still run while it waits for paho's thread;
        # by then the consumer is being torn down. The same goes for the two below.
        if self._closing:
            return
        try:
            self._on_envelope(envelope)
        except Exception:
            _LOGGER.exception("Kohler MQTT consumer raised on %s", envelope.code)

    def _dispatch_connected(self) -> None:
        """Notify the consumer of a connect, on the owning loop.

        Runs on paho's network thread, so it hands off the same way envelopes do.
        """
        loop = self._loop
        if loop is None or loop.is_closed() or self._on_connect_cb is None:
            return
        loop.call_soon_threadsafe(self._safe_connected)

    def _safe_connected(self) -> None:
        if self._closing:
            return
        try:
            if self._on_connect_cb is not None:
                self._on_connect_cb()
        except Exception:
            _LOGGER.exception("Kohler MQTT connect consumer raised")

    def _dispatch_disconnected(self) -> None:
        """Notify the consumer of a dropped connection, on the owning loop."""
        loop = self._loop
        if loop is None or loop.is_closed() or self._on_disconnect_cb is None:
            return
        loop.call_soon_threadsafe(self._safe_disconnected)

    def _safe_disconnected(self) -> None:
        if self._closing:
            return
        try:
            if self._on_disconnect_cb is not None:
                self._on_disconnect_cb()
        except Exception:
            _LOGGER.exception("Kohler MQTT disconnect consumer raised")

    def _report_auth_error(self, err: AuthError) -> None:
        """Hand a rejected credential to the consumer, once per outage.

        No loop hop, unlike :meth:`_dispatch_connected`: the only caller is
        :meth:`_async_reconnect`, which is already a task on the owning loop.
        """
        if self._auth_error_reported or self._on_auth_error_cb is None:
            return
        self._auth_error_reported = True
        try:
            self._on_auth_error_cb(err)
        except Exception:
            _LOGGER.exception("Kohler MQTT auth-error consumer raised")

    def _schedule_reconnect(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or self._closing:
            return
        loop.call_soon_threadsafe(self._start_reconnect_task)

    def _start_reconnect_task(self) -> None:
        if self._closing or (
            self._reconnect_task is not None and not self._reconnect_task.done()
        ):
            return
        self._reconnect_task = asyncio.create_task(self._async_reconnect())

    def _check_connected(self, client: mqtt.Client) -> None:
        """Connect watchdog: retry an attempt that ended without paho reporting it.

        Runs on the owning loop `CONNECT_TIMEOUT_SECONDS` after a connection is started.
        Every ordinary failure reports itself well before then — refused or dropped through
        `on_disconnect`, never opened through `on_connect_fail` — and starts the reconnect.
        This is for the rest: paho's thread ends on any exception from opening the socket
        that is not an `OSError`, and reports nothing, which would leave the stream down
        until a reload.
        """
        self._connect_watchdog = None
        if self._closing or client is not self._mqtt or self.connected:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        _LOGGER.warning(
            "Kohler MQTT did not connect within %ss; retrying", CONNECT_TIMEOUT_SECONDS
        )
        self._start_reconnect_task()

    async def _async_reconnect(self) -> None:
        """Reconnect with exponential backoff, re-registering each attempt."""
        while not self._closing:
            delay = self._backoff
            self._backoff = min(self._backoff * 2, RECONNECT_MAX_SECONDS)
            await asyncio.sleep(delay)
            if self._closing:
                return
            # Under `_client_lock`, so a CONNACK cannot land between the test and the swap:
            # `connected` is then the current client's, and only ever true while it is.
            with self._client_lock:
                if self._mqtt is not None and self.connected:
                    # The current client came up during the wait after all — a first
                    # connect slower than the watchdog allowed. It is healthy; replacing it
                    # is not.
                    return
                old, self._mqtt = self._mqtt, None
                # Its state goes with it. A connect, drop or failure it reports from here on
                # is not heard: those callbacks ignore a client that is no longer `_mqtt`.
                self.connected = False
                self._connected_at = None
            if old is not None:
                try:
                    old.disconnect()
                except Exception:
                    _LOGGER.debug("Error tearing down old MQTT client", exc_info=True)
                # **Waited out, unlike `async_stop`'s** (2026-10-16). An attempt stuck in a
                # TLS handshake or a lookup can still finish after a wait gives up on it,
                # and send its CONNECT with the same IoT Hub client id after the
                # replacement is up — which kicks the replacement off. Going on after a
                # bound did that, and every later cycle left one more such client behind,
                # each blocking a worker. So the next client starts only once the old
                # thread has ended; a wait that goes on is warned of every
                # `CONNECT_TIMEOUT_SECONDS`, not silent. A stop while this waits cancels
                # the loop.
                stopping = await self._async_stop_network(
                    old, timeout=CONNECT_TIMEOUT_SECONDS
                )
                while not stopping.done():
                    await asyncio.wait({stopping}, timeout=CONNECT_TIMEOUT_SECONDS)
                    if not stopping.done():
                        _LOGGER.warning(
                            "Kohler MQTT is still waiting for an abandoned connection "
                            "attempt to end; it reconnects once that has"
                        )
            try:
                await self._async_connect()
                return
            except AuthError as err:
                # A rejected credential cannot be retried out of, so tell the consumer —
                # but keep retrying anyway, at the 300 s ceiling. Re-authenticating
                # elsewhere (or a reload) should heal this without a restart, and the cost
                # of an attempt every five minutes is nothing next to a stream that stays
                # down because nobody noticed. `AuthUnavailable` is excluded: that is
                # Kohler being unreachable, which is exactly what retrying is for.
                if not isinstance(err, AuthUnavailable):
                    self._report_auth_error(err)
                _LOGGER.warning("Kohler MQTT reconnect failed: %s", err)
            except Exception as err:
                _LOGGER.warning("Kohler MQTT reconnect failed: %s", err)
