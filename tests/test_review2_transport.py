"""The transport, second pass, without Home Assistant: real paho-mqtt, and B2C's replies.

The stream's races with paho's own network thread cannot be shown with the integration
suite's `FakeMqttClient`, which calls back on the test's own thread. These run the real
client, against `tests/test_review_transport.py`'s `Broker` or a server that never answers.
Each fails on the code before the second 2026-10-16 review.
"""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import threading
import time
from collections.abc import Generator
from typing import Any, ClassVar
from unittest.mock import patch

import pytest

from custom_components.kohler_konnect.config_flow import KohlerKonnectConfigFlow
from custom_components.kohler_konnect.konnect import mqtt as stream_module
from custom_components.kohler_konnect.konnect.auth import (
    AuthError,
    InvalidCredentials,
    KohlerAuth,
    SignInBlocked,
)
from custom_components.kohler_konnect.konnect.client import KohlerError
from custom_components.kohler_konnect.konnect.mqtt import KonnectMqttStream
from tests.test_review_transport import (
    Broker,
    PlainClient,
    Registrar,
    _stream,
    _until,
)


# --------------------------------------------------------------------------- #
# A CONNACK that lands while the reconnect is replacing its client
# --------------------------------------------------------------------------- #
class HeldConnackBroker(Broker):
    """`Broker`, holding its first CONNACK back until `release` is set."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self._held = False
        super().__init__()

    def _send(self, conn: socket.socket, packet: bytes) -> None:
        if packet[:1] == b"\x20" and not self._held:
            self._held = True
            self.release.wait(10)
        super()._send(conn, packet)


class LingersBeforeSubscribing(PlainClient):
    """Real paho whose `subscribe` waits — 1.2 s at most — for the stream to let it go.

    Before the fix `_on_connect` subscribed after testing that its client was still the
    stream's and before recording the connection, so this holds that window open until the
    reconnect has swapped the client out. After it the connection is recorded first, under
    the lock the swap takes, and this merely waits out its 1.2 s.
    """

    stream: ClassVar[KonnectMqttStream | None] = None

    def subscribe(self, *args: Any, **kwargs: Any) -> Any:
        deadline = time.monotonic() + 1.2
        while (
            (stream := LingersBeforeSubscribing.stream) is not None
            and stream._mqtt is self
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        return super().subscribe(*args, **kwargs)


class SecondRegistrationFails(Registrar):
    async def async_register_mobile_device(self, mobile_device_id: str | None) -> dict:
        settings = await super().async_register_mobile_device(mobile_device_id)
        if self.calls == 2:
            raise KohlerError("Kohler answered 503")
        return settings


@pytest.fixture
def held_broker() -> Generator[HeldConnackBroker]:
    broker = HeldConnackBroker()
    with (
        patch.object(stream_module, "MQTT_PORT", broker.port),
        patch.object(stream_module, "RECONNECT_MIN_SECONDS", 0.5),
        patch.object(stream_module.mqtt, "Client", LingersBeforeSubscribing),
    ):
        yield broker
    broker.release.set()
    broker.close()


@pytest.mark.asyncio
async def test_a_connack_landing_during_the_swap_does_not_end_reconnecting(
    held_broker: HeldConnackBroker,
) -> None:
    """The watchdog gave up on a slow first connect; its CONNACK landed while the
    reconnect was replacing that client, and recorded `connected = True` for a client
    already let go. The next registration failed, the reconnect saw `connected` and
    stopped: no client, no retry, and a stream that went on reporting itself connected."""
    stream = _stream(SecondRegistrationFails())
    LingersBeforeSubscribing.stream = stream
    try:
        with patch.object(stream_module, "CONNECT_TIMEOUT_SECONDS", 0.05):
            await stream.async_start()
            # The watchdog's retry waits half a second before replacing the client; the
            # CONNACK it gave up on lands in that wait.
            await _until(lambda: stream._reconnect_task is not None, "the retry")
            held_broker.release.set()
            task = stream._reconnect_task
            assert task is not None
            await _until(task.done, "the retry to settle", timeout=8)
            await _until(lambda: stream.connected, "a connection")
        assert stream._mqtt is not None
        assert stream._mqtt.is_connected()
    finally:
        LingersBeforeSubscribing.stream = None
        await stream.async_stop()


# --------------------------------------------------------------------------- #
# Stopping during a TLS handshake
# --------------------------------------------------------------------------- #
class SilentServer:
    """Accepts TCP connections and never says a word, so a TLS handshake with it waits."""

    def __init__(self) -> None:
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port: int = self._server.getsockname()[1]
        self.connections: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            self.connections.append(conn)

    def close(self) -> None:
        self._server.close()
        for conn in self.connections:
            conn.close()


class WatchedCapture:
    """A raw capture that records whether paho's thread had ended when it was closed."""

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.closed_after_thread: bool | None = None

    def write(self, topic: str, payload: bytes, **_: Any) -> None:
        pass

    def close(self) -> None:
        assert self.thread is not None
        self.closed_after_thread = not self.thread.is_alive()


@pytest.mark.asyncio
async def test_stopping_does_not_wait_out_a_tls_handshake() -> None:
    """`loop_stop()` joined a thread blocked in the handshake until the keepalive ran out
    — a minute in production, two seconds here — and unload, reload and Home Assistant's
    shutdown waited with it."""
    silent = SilentServer()
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    raw = WatchedCapture()
    stream = KonnectMqttStream(
        Registrar(),  # type: ignore[arg-type]
        lambda envelope: None,
        # Its own identity, so its thread's name is its own among the other tests'.
        mobile_device_id="ha-tls-identity",
        ssl_context=context,
        raw_log=raw,  # type: ignore[arg-type]
    )
    try:
        with (
            patch.object(stream_module, "MQTT_PORT", silent.port),
            patch.object(stream_module, "KEEPALIVE_SECONDS", 2),
            patch.object(stream_module, "STOP_TIMEOUT_SECONDS", 0.2),
        ):
            await stream.async_start()
            await _until(lambda: bool(silent.connections), "the TCP connection")
            (raw.thread,) = (
                thread
                for thread in threading.enumerate()
                if thread.name == "paho-mqtt-client-mobile-ha-tls-identity"
            )
            started = time.monotonic()
            await stream.async_stop()
            took = time.monotonic() - started

            assert took < 1
            assert raw.thread.is_alive()
            # Closed once the thread has ended — not before, while it could still write.
            assert raw.closed_after_thread is None
            await _until(
                lambda: raw.closed_after_thread is not None, "the capture", timeout=10
            )
            assert raw.closed_after_thread is True
    finally:
        silent.close()


# --------------------------------------------------------------------------- #
# Signing in: a credential reply that is JSON but not an object
# --------------------------------------------------------------------------- #
class _Reply:
    """`session.post(...)`'s answer: B2C's reply to the credential step."""

    def __init__(self, body: str) -> None:
        self._body = body

    async def __aenter__(self) -> _Reply:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def text(self) -> str:
        return self._body


class _Session:
    def __init__(self, body: str) -> None:
        self._body = body

    def post(self, url: str, **kwargs: Any) -> _Reply:
        return _Reply(self._body)


async def _submit(body: str) -> None:
    auth = KohlerAuth(_Session(body))  # type: ignore[arg-type]
    await auth._async_submit_credentials(
        _Session(body),  # type: ignore[arg-type]
        "csrf",
        "transaction",
        "https://login.example/authorize",
        "owner@example.com",
        "password",
    )


@pytest.mark.parametrize(
    ("body", "raised"),
    [
        ("null", AuthError),
        ('[{"status": "200"}]', AuthError),
        ('"Try again later"', AuthError),
        ("7", AuthError),
        (json.dumps("AADB2C90053: The password is incorrect."), InvalidCredentials),
    ],
)
def test_a_credential_reply_that_is_not_an_object_is_a_sign_in_failure(
    body: str, raised: type[AuthError]
) -> None:
    """`.get` on it raised `AttributeError`, which the setup flow does not catch: adding
    the account failed with "Unknown error". B2C's own code still decides, as it does for
    a reply that is not JSON at all."""
    with pytest.raises(AuthError) as err:
        asyncio.run(_submit(body))
    assert type(err.value) is raised


@pytest.mark.parametrize(
    ("code", "raised"),
    [
        (90157, SignInBlocked),
        ("90157", SignInBlocked),
        (90053, InvalidCredentials),
        (True, InvalidCredentials),
    ],
)
def test_a_b2c_code_sent_as_a_bare_number_is_read_as_one(
    code: object, raised: type[AuthError]
) -> None:
    """Passed over, every code so sent read as a wrong password — a lockout too
    (2026-10-16)."""
    with pytest.raises(AuthError) as err:
        asyncio.run(_submit(json.dumps({"status": "400", "errorCode": code})))
    assert type(err.value) is raised


def test_the_setup_form_says_kohler_could_not_be_reached_for_one() -> None:
    errors = asyncio.run(KohlerKonnectConfigFlow._async_sign_in_errors(_submit("null")))
    assert errors == {"base": "cannot_connect"}
