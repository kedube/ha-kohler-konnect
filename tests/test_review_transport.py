"""The MQTT stream against real paho-mqtt, over a real socket.

The integration suite stands `FakeMqttClient` in for paho, which cannot show what paho's own
network thread does — and the 2026-10-16 review found the stream's worst failures there:
paho reconnecting by itself behind the stream's back, a first connect that failed with no
retry at all, and one malformed message ending the thread for good. These run the real
client against `Broker`, just enough of an MQTT 3.1.1 broker on localhost, with TLS left
out (`PlainClient`).
"""

from __future__ import annotations

import asyncio
import socket
import struct
import threading
import time
from collections.abc import Callable, Generator
from typing import Any, ClassVar
from unittest.mock import patch

import paho.mqtt.client as paho
import pytest

from custom_components.kohler_konnect.konnect import mqtt as stream_module
from custom_components.kohler_konnect.konnect.mqtt import Envelope, KonnectMqttStream

TOPIC = b"$iothub/methods/POST/ExecuteControlCommand/?$rid=7"


def _read_exactly(conn: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


def _read_length(conn: socket.socket) -> int:
    length, shift = 0, 0
    while True:
        byte = _read_exactly(conn, 1)[0]
        length += (byte & 0x7F) << shift
        if not byte & 0x80:
            return length
        shift += 7


def _encode_length(length: int) -> bytes:
    out = bytearray()
    while True:
        byte, length = length % 128, length // 128
        out.append(byte | (0x80 if length else 0))
        if not length:
            return bytes(out)


def _string(body: bytes, at: int) -> tuple[str, int]:
    (size,) = struct.unpack_from("!H", body, at)
    return body[at + 2 : at + 2 + size].decode(), at + 2 + size


def _password(connect: bytes) -> str:
    """The password field of a CONNECT packet's body."""
    _, at = _string(connect, 0)  # protocol name
    flags = connect[at + 1]
    at += 4  # level, flags, keepalive
    _, at = _string(connect, at)  # client id
    if flags & 0x04:  # will topic and message
        _, at = _string(connect, at)
        _, at = _string(connect, at)
    if flags & 0x80:
        _, at = _string(connect, at)
    return _string(connect, at)[0] if flags & 0x40 else ""


class Broker:
    """Just enough MQTT 3.1.1: CONNACK, SUBACK, PUBACK, PINGRESP, and a PUBLISH on demand.

    Records the password each connection presents, which is the point: every connection
    the stream makes must carry a freshly registered SAS password.
    """

    def __init__(self) -> None:
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port: int = self._server.getsockname()[1]
        self.passwords: list[str] = []
        self.refuse: set[str] = set()
        self.acks = 0
        self._lock = threading.Lock()
        self._connections: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _send(self, conn: socket.socket, packet: bytes) -> None:
        with self._lock:
            conn.sendall(packet)

    def _serve(self, conn: socket.socket) -> None:
        try:
            while True:
                header = _read_exactly(conn, 1)[0]
                body = _read_exactly(conn, _read_length(conn))
                kind = header & 0xF0
                if kind == 0x10:  # CONNECT
                    password = _password(body)
                    with self._lock:
                        self.passwords.append(password)
                    if password in self.refuse:
                        # CONNACK 5, "not authorised": an expired SAS password.
                        self._send(conn, b"\x20\x02\x00\x05")
                        return
                    with self._lock:
                        self._connections.append(conn)
                    self._send(conn, b"\x20\x02\x00\x00")
                elif kind == 0x80:  # SUBSCRIBE
                    self._send(conn, b"\x90\x03" + body[:2] + b"\x01")
                elif kind == 0x30:  # PUBLISH: the stream acknowledging a direct method
                    if (header >> 1) & 0x03:
                        (size,) = struct.unpack_from("!H", body, 0)
                        self._send(conn, b"\x40\x02" + body[2 + size : 4 + size])
                    with self._lock:
                        self.acks += 1
                elif kind == 0xC0:  # PINGREQ
                    self._send(conn, b"\xd0\x00")
                elif kind == 0xE0:  # DISCONNECT
                    return
        except OSError:
            pass
        finally:
            conn.close()

    def drop(self) -> None:
        """Close the newest connection from the broker's side."""
        with self._lock:
            conn = self._connections.pop()
        conn.shutdown(socket.SHUT_RDWR)

    def publish(self, payload: bytes) -> None:
        """Send a direct method, at QoS 0, on the newest connection."""
        with self._lock:
            conn = self._connections[-1]
        body = struct.pack("!H", len(TOPIC)) + TOPIC + payload
        self._send(conn, b"\x30" + _encode_length(len(body)) + body)

    def close(self) -> None:
        self._server.close()
        with self._lock:
            connections, self._connections = self._connections, []
        for conn in connections:
            conn.close()


class PlainClient(paho.Client):
    """Real paho without TLS, which `Broker` does not speak.

    Its own reconnect delay is cut to 0.1 s, so a reconnect paho made by itself — which
    it must not — would show well inside the stream's own backoff.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reconnect_delay_set(0.1, 0.1)  # type: ignore[arg-type]

    def tls_set_context(self, context: Any = None) -> None:
        pass


class Registrar:
    """Stands in for `KohlerClient.async_register_mobile_device`.

    Each registration issues a new password, `sig-1`, `sig-2`, …, and can point the next
    connection at a different port.
    """

    def __init__(self, *ports: int) -> None:
        self.ports = list(ports)
        self.calls = 0

    async def async_register_mobile_device(self, mobile_device_id: str | None) -> dict:
        self.calls += 1
        if self.ports:
            stream_module.MQTT_PORT = self.ports.pop(0)
        return {
            "ioTHub": "127.0.0.1",
            "deviceId": f"mobile-{mobile_device_id}",
            "username": "user",
            "password": f"sig-{self.calls}",
        }


def _closed_port() -> int:
    """A localhost port nothing listens on, so connecting is refused at once."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def _until(condition: Callable[[], bool], what: str, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


@pytest.fixture
def broker() -> Generator[Broker]:
    broker = Broker()
    with (
        patch.object(stream_module, "MQTT_PORT", broker.port),
        patch.object(stream_module, "RECONNECT_MIN_SECONDS", 0.5),
        patch.object(stream_module.mqtt, "Client", PlainClient),
    ):
        yield broker
    broker.close()


def _stream(registrar: Registrar, **kwargs: Any) -> KonnectMqttStream:
    kwargs.setdefault("on_envelope", lambda envelope: None)
    return KonnectMqttStream(
        registrar,  # type: ignore[arg-type]
        mobile_device_id="ha-identity",
        ssl_context=object(),  # type: ignore[arg-type]
        **kwargs,
    )


@pytest.mark.asyncio
async def test_after_a_drop_only_the_stream_reconnects_with_fresh_credentials(
    broker: Broker,
) -> None:
    """paho, left on its defaults, reconnected by itself with the old SAS password.

    The stream's own reconnect then tore that connection down and made another: a
    spurious disconnect, a second registration and a second full reseed per drop.
    """
    stream = _stream(Registrar())
    try:
        await stream.async_start()
        await _until(lambda: stream.connected, "the connection")
        broker.drop()
        await _until(lambda: not stream.connected, "the drop to be noticed")
        await _until(lambda: stream.connected, "the reconnection")
        await asyncio.sleep(0.3)
    finally:
        await stream.async_stop()
    assert broker.passwords == ["sig-1", "sig-2"]


@pytest.mark.asyncio
async def test_a_refused_password_is_replaced_not_retried(broker: Broker) -> None:
    """An expired SAS password earned a CONNACK 5 from every one of paho's own retries."""
    broker.refuse.add("sig-1")
    stream = _stream(Registrar())
    try:
        await stream.async_start()
        await _until(lambda: stream.connected, "the connection")
        await asyncio.sleep(0.3)
    finally:
        await stream.async_stop()
    assert broker.passwords == ["sig-1", "sig-2"]


@pytest.mark.asyncio
async def test_a_first_connect_that_cannot_open_is_retried(broker: Broker) -> None:
    """Refused at the TCP level, so no CONNACK and no disconnect: only `on_connect_fail`.

    paho on its defaults retried the same port by itself, for ever, and the stream never
    heard; with its reconnect off and nothing listening for that, the thread would just
    end. Either way the stream stayed down until a reload.
    """
    registrar = Registrar(_closed_port(), broker.port)
    stream = _stream(registrar)
    try:
        await stream.async_start()
        await _until(lambda: stream.connected, "the retry to connect")
    finally:
        await stream.async_stop()
    assert registrar.calls == 2
    assert broker.passwords == ["sig-2"]


class BrokenFirstSocket(PlainClient):
    """The first connect fails with something that is not an `OSError`.

    paho catches only `OSError` there; anything else ends its thread with no callback of
    any kind — the case the connect watchdog exists for.
    """

    broken: ClassVar[list[bool]] = []

    def _create_socket(self) -> Any:
        if not BrokenFirstSocket.broken:
            BrokenFirstSocket.broken.append(True)
            raise ValueError("not an OSError")
        return super()._create_socket()


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
async def test_a_first_connect_that_dies_unreported_is_retried(broker: Broker) -> None:
    """The thread's own exception is expected here, and ignored; the retry is the point."""
    BrokenFirstSocket.broken.clear()
    registrar = Registrar()
    stream = _stream(registrar)
    with (
        patch.object(stream_module.mqtt, "Client", BrokenFirstSocket),
        patch.object(stream_module, "CONNECT_TIMEOUT_SECONDS", 0.3),
    ):
        try:
            await stream.async_start()
            await _until(lambda: stream.connected, "the watchdog's retry")
        finally:
            await stream.async_stop()
    assert registrar.calls == 2
    assert broker.passwords == ["sig-2"]


@pytest.mark.asyncio
async def test_a_malformed_message_does_not_end_the_network_thread(
    broker: Broker,
) -> None:
    """`attributes` as a number raised in `on_message`; paho re-raised it, and its thread
    — the only thing reading the socket — ended without a word."""
    received: list[Envelope] = []
    stream = _stream(Registrar(), on_envelope=received.append)
    try:
        await stream.async_start()
        await _until(lambda: stream.connected, "the connection")
        broker.publish(b'{"data": {"code": "BAD", "attributes": 7}}')
        broker.publish(b'{"data": {"code": "GOOD", "attributes": [{"a": 1}]}}')
        await _until(lambda: len(received) == 2, "both messages")
        await _until(lambda: broker.acks == 2, "both acknowledgements")
    finally:
        await stream.async_stop()
    assert [(e.code, e.attributes) for e in received] == [
        ("BAD", []),
        ("GOOD", [{"a": 1}]),
    ]
