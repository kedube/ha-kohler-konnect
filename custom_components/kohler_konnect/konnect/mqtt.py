"""Azure IoT Hub status stream for every Kohler Konnect device on an account.

Kohler pushes status as IoT Hub direct methods on one account-level topic,
``$iothub/methods/POST/#``, so one connection carries every device on the account and
callers filter by ``sku`` and ``device_id``. Each message must be answered on
``$iothub/methods/res/200/?$rid=N``. The connection is held open and the caller's
registered identity reused: a connect-per-command client receives nothing. See
`docs/protocol/platform.md` §4.

paho-mqtt runs its own network thread; callbacks are marshalled onto the owning event
loop. Reconnecting is this class's job, never paho's: every connect needs a freshly
registered SAS password, which paho cannot fetch.
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

# Reconnect backoff. Every reconnect re-registers for a fresh SAS password.
RECONNECT_MIN_SECONDS = 5
RECONNECT_MAX_SECONDS = 300
# How long a connection must last before the backoff resets, so a broker that accepts
# and then drops the client at once does not drive a reconnect every few seconds. IoT Hub
# does that to the older of two connections sharing a client id.
STABLE_CONNECTION_SECONDS = 60
# Backstop for a connection attempt that ends without paho reporting it; see
# `_check_connected`. Longer than paho's own limits: TLS and CONNACK each wait up to the
# keepalive, and DNS adds its own wait.
CONNECT_TIMEOUT_SECONDS = 120
KEEPALIVE_SECONDS = 60
# How long `async_stop` waits for paho's network thread. A thread inside a connection
# attempt cannot be cut short (DNS has no timeout, TLS waits up to the keepalive), so past
# this it is left to finish: callbacks ignore a released client, and everything once the
# stream is stopping.
STOP_TIMEOUT_SECONDS = 5

# ⚠️ Never build an SSL context on the event loop: it reads the CA bundle from disk, as
# paho's `tls_set()` does. Build it once in a worker thread and pass it to
# `tls_set_context()`.
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

    paho-mqtt 2.x re-raises a callback's exception, which ends the network thread with no
    ``on_disconnect``: the stream would stay "connected" and never reconnect.
    ``suppress_exceptions`` is also set, as a backstop.
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
    #: Arrival on the monotonic clock, for comparing with other monotonic times;
    #: `received_at` is wall-clock and shifts with clock steps.
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
        # The broker replays nothing on connect (`docs/protocol/platform.md` §4.2), so
        # the consumer re-reads state over REST here.
        self._on_connect_cb = on_connect
        # Called on the owning loop when an established connection drops, so a consumer
        # that polls slowly while the stream delivers (the faucets) can speed back up.
        self._on_disconnect_cb = on_disconnect
        # Called on the owning loop when a reconnect fails because the credential was
        # rejected, not merely unreachable. ⚠️ In a push-only setup nothing else exercises
        # the credential, so without this a dead refresh token never prompts reauth.
        self._on_auth_error_cb = on_auth_error
        # Latched so one outage produces one notification rather than one per retry.
        self._auth_error_reported = False
        self._loop = loop
        self._ssl_context = ssl_context
        self._mqtt: mqtt.Client | None = None
        # Guards `_mqtt` together with `connected` and `_connected_at`, so paho's thread
        # cannot record a connect or drop for a client the loop has just swapped out.
        # ⚠️ Hold it for those assignments only, never across a call into paho:
        # `disconnect()` can call `_on_disconnect` on the same thread, which would deadlock.
        self._client_lock = threading.Lock()
        self._closing = False
        self._reconnect_task: asyncio.Task[None] | None = None
        self._connect_watchdog: asyncio.TimerHandle | None = None
        self._backoff = RECONNECT_MIN_SECONDS
        self._connected_at: float | None = None
        # The persisted identity to reuse; None registers a throwaway one per connect.
        self._mobile_device_id = mobile_device_id
        # Raw MQTT capture (`raw_log.py`); None disables it.
        self._raw_log = raw_log
        # The user-facing Report Log capture (`report_log.py`), fed at the same point.
        self._report_log = report_log
        # Only a newly registered identity can need warm-up; see `warming_up`.
        self._warmup_pending = expect_warmup
        self.connected = False
        self.last_message_at: float | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def async_start(self) -> None:
        """Register and start connecting; the subscription follows from paho's thread.

        Returns once the connection is started, not up; `on_connect` reports that. A
        failure is raised for the caller to report and also retried in the background on
        the reconnect backoff.
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
        # Closing the captures is blocking I/O, and must follow the end of paho's thread (a
        # message in hand would reopen them), so one worker does both, in that order, even
        # when stopping outlasts the wait.
        if (
            client is not None
            or self._raw_log is not None
            or self._report_log is not None
        ):
            await self._async_stop_network(client, then=self._close_captures)

    def _close_captures(self) -> None:
        """Release the capture files. Blocking: run it in a worker thread."""
        if self._raw_log is not None:
            self._raw_log.close()
        # Releases the handle only; the Report Log episode continues after a reload.
        if self._report_log is not None:
            self._report_log.close()

    async def _async_stop_network(
        self,
        client: mqtt.Client | None,
        *,
        then: Callable[[], None] | None = None,
        timeout: float | None = None,
    ) -> asyncio.Future[None]:
        """Stop `client`'s network thread, then run `then`, in one worker thread.

        Waits at most ``timeout`` seconds (default `STOP_TIMEOUT_SECONDS`) and warns if
        that runs out. The worker carries on, so `then` still runs after the thread ends,
        never before, and even if stopping raised. Failures are logged, not raised.
        Returns the worker's future.
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

        Do not infer device state from silence while this is True. Only a freshly
        registered identity that has received nothing warms up; the first message ends it
        for good. `MQTT_WARMUP_SECONDS` is a conservative cap, not a measurement
        (`docs/protocol/platform.md` §4.2).
        """
        if not self._warmup_pending:
            return False
        if self._connected_at is None:
            return True
        return (time.monotonic() - self._connected_at) < MQTT_WARMUP_SECONDS

    async def _async_connect(self) -> None:
        """Obtain fresh credentials and bring up the connection."""
        # Fresh credentials on every connect (the SAS password is short-lived), for the
        # same identity when one was supplied.
        settings = await self._client.async_register_mobile_device(
            self._mobile_device_id
        )
        host = settings["ioTHub"]
        device_id = settings["deviceId"]

        # Callback API version 1, which the callback signatures below follow; moving to
        # version 2 means changing `_on_connect`, `_on_disconnect` and `_on_connect_fail`
        # together.
        #
        # `reconnect_on_failure=False`: reconnecting is `_async_reconnect`'s job alone, as
        # paho's own would reuse the expired SAS password and race it. A drop then ends
        # paho's thread; a connect failing before CONNACK reports only through
        # `on_connect_fail`, and a non-`OSError` failure reports nothing (hence the watchdog
        # below).
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
        # Ignore a CONNACK for a client that is no longer current: one can arrive after
        # `async_stop` (a `disconnect()` during an attempt does not stop its CONNACK) or
        # after a swap. Tested and recorded in one step under `_client_lock`; the
        # subscribe, a paho call, follows outside it.
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
        # The backoff resets only once a connection lasts; see `STABLE_CONNECTION_SECONDS`.
        # Credentials work again: re-arm the auth-error report for the next outage.
        self._auth_error_reported = False
        # Mention warm-up only when it can apply: a reused identity has none.
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
        # Captured before decoding, so payloads that fail to parse are kept too.
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
        # Anything but a list counts as no attributes.
        if not isinstance(attributes, list):
            attributes = []
        envelope = Envelope(
            sku=str(payload.get("sku") or ""),
            # `deviceid` on the wire (`docs/protocol/platform.md` §4.3); `deviceId` is
            # accepted too, defensively.
            device_id=str(payload.get("deviceid") or payload.get("deviceId") or ""),
            code=str(data.get("code") or ""),
            attributes=[a for a in attributes if isinstance(a, dict)],
            received_at=time.time(),
            raw=payload,
            received_monotonic=time.monotonic(),
        )
        self.last_message_at = envelope.received_at
        # Proof the identity is provisioned: ends warm-up for good, across reconnects too.
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
        # Hand-offs queued before `async_stop` can run while the consumer is being torn
        # down; the same applies to the two below.
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

        Runs on the owning loop `CONNECT_TIMEOUT_SECONDS` after a connect starts. Ordinary
        failures report through `on_disconnect` or `on_connect_fail` well before then;
        this catches paho's thread ending silently on a non-`OSError` while opening the
        socket.
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
                    # The current client came up during the wait (a first connect slower
                    # than the watchdog allowed); keep it.
                    return
                old, self._mqtt = self._mqtt, None
                # Its state goes with it; callbacks ignore a client that is not `_mqtt`.
                self.connected = False
                self._connected_at = None
            if old is not None:
                try:
                    old.disconnect()
                except Exception:
                    _LOGGER.debug("Error tearing down old MQTT client", exc_info=True)
                # ⚠️ Wait for the old thread to end before the next client starts, unlike
                # `async_stop`: an attempt stuck in TLS or DNS can still send its CONNECT
                # with the same client id later, and IoT Hub would drop the replacement.
                # A long wait is warned of every `CONNECT_TIMEOUT_SECONDS`.
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
                # Report a rejected credential (not `AuthUnavailable`, an outage), but keep
                # retrying at the backoff ceiling so a reauth or reload heals the stream
                # without a restart.
                if not isinstance(err, AuthUnavailable):
                    self._report_auth_error(err)
                _LOGGER.warning("Kohler MQTT reconnect failed: %s", err)
            except Exception as err:
                _LOGGER.warning("Kohler MQTT reconnect failed: %s", err)
