"""The MQTT stream on its own: acknowledging, decoding, reconnecting, reporting.

`KonnectMqttStream` is driven here directly, with `FakeMqttClient` standing in for paho (the
conftest patches it in for every test) and a stand-in for the one `KohlerClient` call it
makes, registering for credentials. The account-level tests in `test_mqtt.py` show the same
stream through a whole entry; these pin what it does when things go wrong underneath.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kohler_konnect.konnect import (
    AuthError,
    AuthUnavailable,
    Envelope,
    KohlerError,
    KonnectMqttStream,
)

from .conftest import (
    MOBILE_ID,
    FakeKohler,
    FakeMqttClient,
    Message,
    account,
    wait_for,
)

TOPIC = "$iothub/methods/POST/ExecuteControlCommand/?$rid={rid}"
ACK = "$iothub/methods/res/200/?$rid={rid}"
# paho is faked and never looks at the context, so there is no need to build a real one
# (which reads the CA bundle from disk).
CONTEXT: Any = object()


class Registrar:
    """Stands in for `KohlerClient.async_register_mobile_device`.

    Hands out IoT Hub credentials, or raises the next queued failure. Records the identity
    asked for and the stream's backoff at each attempt.
    """

    def __init__(self, *failures: BaseException) -> None:
        self.failures = list(failures)
        self.identities: list[str | None] = []
        self.backoffs: list[float] = []
        self.stream: KonnectMqttStream | None = None

    async def async_register_mobile_device(self, mobile_device_id: str | None) -> dict:
        self.identities.append(mobile_device_id)
        if self.stream is not None:
            self.backoffs.append(self.stream._backoff)
        if self.failures:
            raise self.failures.pop(0)
        return {
            "ioTHub": "hub.example.net",
            "deviceId": f"mobile-{mobile_device_id}",
            "username": "hub.example.net/user",
            "password": f"SharedAccessSignature sig-{len(self.identities)}",
        }


class Capture:
    """A raw or report log: records what it was handed, and whether it was closed."""

    def __init__(self) -> None:
        self.payloads: list[bytes] = []
        self.closed = False

    def write(self, topic: str, payload: bytes, **_: Any) -> None:
        self.payloads.append(payload)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
async def make_stream() -> AsyncGenerator[Callable[..., KonnectMqttStream]]:
    """Build streams on the test's loop, and stop every one of them afterwards."""
    made: list[KonnectMqttStream] = []

    def build(registrar: Registrar, **kwargs: Any) -> KonnectMqttStream:
        kwargs.setdefault("on_envelope", lambda envelope: None)
        kwargs.setdefault("mobile_device_id", MOBILE_ID)
        stream = KonnectMqttStream(registrar, ssl_context=CONTEXT, **kwargs)  # type: ignore[arg-type]
        registrar.stream = stream
        made.append(stream)
        return stream

    yield build
    for stream in made:
        await stream.async_stop()


async def settle(hass: HomeAssistant, condition: Callable[[], bool], what: str) -> None:
    await wait_for(hass, condition, what)


def deliver_bytes(client: FakeMqttClient, payload: bytes, rid: str) -> None:
    client.on_message(client, None, Message(TOPIC.format(rid=rid), payload))


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #


async def test_every_message_is_acknowledged_even_one_that_cannot_be_decoded(
    hass: HomeAssistant, make_stream
) -> None:
    """An unanswered direct method counts as unhandled — and a bad payload is the one
    worth keeping, so it reaches the captures before the decode drops it."""
    received: list[Envelope] = []
    raw, report = Capture(), Capture()
    stream = make_stream(
        Registrar(), on_envelope=received.append, raw_log=raw, report_log=report
    )
    await stream.async_start()
    client = FakeMqttClient.instances[-1]

    deliver_bytes(client, b"\xff\xfe not json", rid="1")
    deliver_bytes(client, b"[1, 2, 3]", rid="2")
    deliver_bytes(client, b'"just a string"', rid="3")
    await hass.async_block_till_done()

    assert client.published == [
        (ACK.format(rid=rid), b'{"status":"received"}') for rid in ("1", "2", "3")
    ]
    assert received == []
    assert (
        raw.payloads
        == report.payloads
        == [
            b"\xff\xfe not json",
            b"[1, 2, 3]",
            b'"just a string"',
        ]
    )
    # Nothing decodable has arrived, so nothing says when.
    assert stream.last_message_at is None


async def test_a_message_becomes_an_envelope_for_the_device_it_names(
    hass: HomeAssistant, make_stream
) -> None:
    """Every consumer routes by `device_id`; a wrong or missing one is a message lost."""
    received: list[Envelope] = []
    stream = make_stream(Registrar(), on_envelope=received.append)
    await stream.async_start()
    client = FakeMqttClient.instances[-1]

    client.deliver(
        {
            "sku": "GCS",
            # The camel-case spelling the faucet integration read is honoured too.
            "deviceId": "gcs-shower0001",
            "data": {
                "code": "GCS_SOLO_STS",
                "attributes": [{"primaryValve1": "0184C801"}, "noise", 7],
            },
        }
    )
    client.deliver({"sku": "HUB", "deviceid": "hub-1", "data": "not an object"})
    await hass.async_block_till_done()

    first, second = received
    assert (first.sku, first.device_id, first.code) == (
        "GCS",
        "gcs-shower0001",
        "GCS_SOLO_STS",
    )
    # Only objects survive as attributes, so no state handler meets a bare string.
    assert first.attributes == [{"primaryValve1": "0184C801"}]
    assert first.raw["data"]["code"] == "GCS_SOLO_STS"
    assert (second.code, second.attributes) == ("", [])
    assert stream.last_message_at is not None


async def test_a_consumer_that_raises_does_not_stop_the_next_message(
    hass: HomeAssistant, make_stream
) -> None:
    """The consumer runs on Home Assistant's loop; an exception there must stay there."""
    seen: list[str] = []

    def consumer(envelope: Envelope) -> None:
        seen.append(envelope.code)
        if envelope.code == "BAD":
            raise ValueError("consumer bug")

    stream = make_stream(Registrar(), on_envelope=consumer)
    await stream.async_start()
    client = FakeMqttClient.instances[-1]
    client.deliver({"data": {"code": "BAD"}}, rid="1")
    client.deliver({"data": {"code": "GOOD"}}, rid="2")
    await hass.async_block_till_done()
    assert seen == ["BAD", "GOOD"]
    assert stream.connected


async def test_a_fresh_identity_warms_up_until_its_first_message(
    hass: HomeAssistant, make_stream
) -> None:
    """Silence from a newly registered identity means nothing; data proves the channel."""
    stream = make_stream(Registrar(), expect_warmup=True)
    assert stream.warming_up  # not even connected yet
    await stream.async_start()
    assert stream.warming_up  # connected, but inside the window

    # Past the window, silence starts to mean something again.
    with patch("custom_components.kohler_konnect.konnect.mqtt.MQTT_WARMUP_SECONDS", 0):
        assert not stream.warming_up
    assert stream.warming_up

    FakeMqttClient.instances[-1].deliver({"data": {"code": "X"}})
    assert not stream.warming_up
    # For good: a reconnect cannot un-provision an identity that has received.
    FakeMqttClient.instances[-1].drop()
    assert not stream.warming_up


async def test_a_reused_identity_never_warms_up(
    hass: HomeAssistant, make_stream
) -> None:
    """It is already provisioned, so silence from it is real silence."""
    stream = make_stream(Registrar(), expect_warmup=False)
    assert not stream.warming_up
    await stream.async_start()
    assert not stream.warming_up


# --------------------------------------------------------------------------- #
# Reconnecting
# --------------------------------------------------------------------------- #


async def test_reconnects_back_off_up_to_the_ceiling_and_reset_once_connected(
    hass: HomeAssistant, make_stream
) -> None:
    """The conftest shrinks the bounds to 0.01 s and 0.05 s; the shape is what counts."""
    registrar = Registrar()
    stream = make_stream(registrar)
    await stream.async_start()
    registrar.failures = [KohlerError("Kohler is down")] * 4

    FakeMqttClient.instances[-1].drop()
    await settle(hass, lambda: stream.connected, "the reconnection")

    # Doubling, then held at the ceiling; the first entry is the initial connect.
    assert registrar.backoffs == [0.01, 0.02, 0.04, 0.05, 0.05, 0.05]
    assert stream._backoff == 0.01
    # The same identity every time — a fresh one per attempt leaves dead registrations.
    assert set(registrar.identities) == {MOBILE_ID}


async def test_a_second_disconnect_during_an_outage_does_not_start_a_second_loop(
    hass: HomeAssistant, make_stream
) -> None:
    """Two loops would each register and connect, leaving two live clients."""
    stream = make_stream(Registrar())
    await stream.async_start()
    first = FakeMqttClient.instances[-1]
    first.drop()
    first.drop()
    await settle(hass, lambda: stream.connected, "the reconnection")
    await asyncio.sleep(0.1)
    assert len(FakeMqttClient.instances) == 2
    assert first.stopped


async def test_a_rejected_credential_is_reported_once_per_outage(
    hass: HomeAssistant, make_stream
) -> None:
    """A dead refresh token fails every attempt; one prompt per outage, not one per try.

    Retrying carries on regardless, so signing in again elsewhere heals it.
    """
    registrar = Registrar()
    reports: list[AuthError] = []
    stream = make_stream(registrar, on_auth_error=reports.append)
    await stream.async_start()

    registrar.failures = [AuthError("rejected")] * 3
    FakeMqttClient.instances[-1].drop()
    await settle(hass, lambda: stream.connected, "the reconnection")
    assert [str(err) for err in reports] == ["rejected"]

    # Connecting re-arms it, so the next outage is reported in its turn.
    registrar.failures = [AuthError("rejected again")]
    FakeMqttClient.instances[-1].drop()
    await settle(hass, lambda: stream.connected, "the second reconnection")
    assert [str(err) for err in reports] == ["rejected", "rejected again"]


@pytest.mark.parametrize(
    "failure",
    [
        AuthUnavailable("token endpoint unreachable"),
        KohlerError("HTTP 503"),
        RuntimeError("something unexpected"),
    ],
)
async def test_kohler_being_unreachable_is_retried_without_a_sign_in_prompt(
    hass: HomeAssistant, make_stream, failure: BaseException
) -> None:
    """Only a rejected credential needs a person; an outage is what retrying is for."""
    registrar = Registrar()
    reports: list[AuthError] = []
    stream = make_stream(registrar, on_auth_error=reports.append)
    await stream.async_start()
    registrar.failures = [failure, failure]
    FakeMqttClient.instances[-1].drop()
    await settle(hass, lambda: stream.connected, "the reconnection")
    assert reports == []


async def test_a_sign_in_prompt_that_raises_does_not_end_the_retrying(
    hass: HomeAssistant, make_stream
) -> None:
    """The prompt is a consumer callback; a bug there must not strand the stream."""

    def broken(err: AuthError) -> None:
        raise RuntimeError("consumer bug")

    registrar = Registrar()
    stream = make_stream(registrar, on_auth_error=broken)
    await stream.async_start()
    registrar.failures = [AuthError("rejected")]
    FakeMqttClient.instances[-1].drop()
    await settle(hass, lambda: stream.connected, "the reconnection")


async def test_connect_and_disconnect_hooks_that_raise_are_contained(
    hass: HomeAssistant, make_stream
) -> None:
    """They run on Home Assistant's loop from paho's thread; a raise must not kill the stream."""
    calls: list[str] = []

    def on_connect() -> None:
        calls.append("connect")
        raise RuntimeError("connect consumer bug")

    def on_disconnect() -> None:
        calls.append("disconnect")
        raise RuntimeError("disconnect consumer bug")

    stream = make_stream(
        Registrar(), on_connect=on_connect, on_disconnect=on_disconnect
    )
    await stream.async_start()
    FakeMqttClient.instances[-1].drop()
    await settle(hass, lambda: calls.count("connect") == 2, "the reconnection")
    assert calls == ["connect", "disconnect", "connect"]
    assert stream.connected


async def test_a_broken_old_client_does_not_stop_the_reconnect(
    hass: HomeAssistant, make_stream
) -> None:
    """Tearing down a dead connection can fail too; that is no reason to stay down."""
    stream = make_stream(Registrar())
    await stream.async_start()
    first = FakeMqttClient.instances[-1]

    def broken_disconnect() -> None:
        raise OSError("socket already gone")

    first.disconnect = broken_disconnect  # type: ignore[method-assign]
    first.drop()
    await settle(hass, lambda: len(FakeMqttClient.instances) == 2, "a new client")
    await settle(hass, lambda: stream.connected, "the reconnection")


async def test_stopping_ends_reconnects_and_releases_the_captures(
    hass: HomeAssistant, make_stream
) -> None:
    """Unload during an outage: nothing may reconnect behind Home Assistant's back."""
    raw, report = Capture(), Capture()
    registrar = Registrar()
    stream = make_stream(registrar, raw_log=raw, report_log=report)
    await stream.async_start()
    client = FakeMqttClient.instances[-1]

    registrar.failures = [KohlerError("down")] * 50
    client.drop()
    await stream.async_stop()
    attempts = len(registrar.identities)
    # A disconnect paho reports while stopping is expected, and schedules nothing.
    client.on_disconnect(client, None, 0)
    await asyncio.sleep(0.15)

    assert len(registrar.identities) == attempts
    assert stream._reconnect_task is None
    assert not stream.connected
    assert client.stopped
    assert raw.closed and report.closed


async def test_a_stop_during_the_backoff_sleep_does_not_connect_afterwards(
    hass: HomeAssistant, make_stream
) -> None:
    """A reload during an outage must not leave a connection behind."""
    registrar = Registrar()
    stream = make_stream(registrar)
    await stream.async_start()
    FakeMqttClient.instances[-1].drop()
    # Let the reconnect task start its sleep, then stop before it wakes.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await stream.async_stop()
    await asyncio.sleep(0.1)
    assert registrar.identities == [MOBILE_ID]
    assert len(FakeMqttClient.instances) == 1


async def test_callbacks_after_the_loop_has_closed_are_dropped_quietly(
    hass: HomeAssistant,
) -> None:
    """paho's thread can outlive Home Assistant's loop by a moment at shutdown.

    Handing work to a closed loop raises; the stream must simply drop it.
    """
    closed = asyncio.new_event_loop()
    closed.close()
    received: list[Envelope] = []
    stream = KonnectMqttStream(
        Registrar(),  # type: ignore[arg-type]
        received.append,
        on_connect=lambda: received.append("connect"),  # type: ignore[arg-type]
        on_disconnect=lambda: received.append("disconnect"),  # type: ignore[arg-type]
        loop=closed,
        ssl_context=CONTEXT,
    )
    # Never started, so these are paho's callbacks invoked as its thread would.
    client = FakeMqttClient()
    stream._on_connect(client, None, None, 0)
    payload = json.dumps({"data": {"code": "X"}}).encode()
    stream._on_message(client, None, Message(TOPIC.format(rid="9"), payload))
    stream._on_disconnect(client, None, 7)

    assert received == []
    # Still acknowledged: that happens on paho's side, before any hand-off.
    assert client.published == [(ACK.format(rid="9"), b'{"status":"received"}')]
    assert stream._reconnect_task is None


# --------------------------------------------------------------------------- #
# Through a whole entry
# --------------------------------------------------------------------------- #


async def test_a_credential_rejected_while_reconnecting_asks_to_sign_in_again(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """Every connect needs a fresh SAS token, so a dead refresh token fails every one.

    Push-only means nothing else exercises the credential: without this, the stream would
    retry for ever at WARNING while the entry looked healthy.
    """
    coordinator = account(hass, setup_entry)
    coordinator.auth.invalidate_access_token()
    kohler.token_queue.append(
        (400, {"error": "invalid_grant", "error_description": "AADB2C90080: expired"})
    )
    FakeMqttClient.instances[-1].drop()

    await settle(
        hass,
        lambda: (
            [f["context"]["source"] for f in hass.config_entries.flow.async_progress()]
            == [SOURCE_REAUTH]
        ),
        "the sign-in prompt",
    )
    # It kept retrying, and once Kohler issues tokens again the stream is back.
    await settle(hass, lambda: coordinator.stream.connected, "the reconnection")
