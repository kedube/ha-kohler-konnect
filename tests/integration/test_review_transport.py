"""The transport when things go wrong: the MQTT stream, tokens, the REST client, reauth.

Each test pins one fix from the 2026-10-16 review of `konnect/mqtt.py`, `konnect/auth.py`,
`konnect/client.py` and the reauth flow, and fails on the code before it. The stream's
reconnect rules are checked against real paho-mqtt, over a socket, in
`tests/test_review_transport.py`; here paho is `FakeMqttClient`, as everywhere else.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp import ClientError
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.kohler_konnect.const import (
    CONF_MOBILE_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_TEMPERATURE_UNIT,
    CONF_TENANT_ID,
    CONF_VALVE_MODEL,
    CONF_WATER_UNITS,
    CONF_ZONE_OUTLETS,
    DOMAIN,
)
from custom_components.kohler_konnect.konnect import (
    AuthError,
    AuthUnavailable,
    Envelope,
    InvalidCredentials,
    KohlerAuth,
    KohlerClient,
    KohlerError,
    KonnectMqttStream,
    TokenSet,
    UnexpectedResponse,
    credential_is_dead,
)
from custom_components.kohler_konnect.konnect.const import (
    API_BASE,
    TOKEN_EXPIRY_MARGIN_SECONDS,
)

from .conftest import (
    DEVICE_ID,
    MOBILE_ID,
    TENANT_ID,
    USERNAME,
    VALVE,
    VALVE_ID,
    FakeKohler,
    FakeMqttClient,
    Message,
    account,
    make_jwt,
    unload,
    wait_for,
)

MQTT = "custom_components.kohler_konnect.konnect.mqtt"
PASSWORD = "correct-horse-battery-staple"
TOPIC = "$iothub/methods/POST/ExecuteControlCommand/?$rid={rid}"
# paho is faked and never looks at the context, so there is no need to build a real one.
CONTEXT: Any = object()
EXPIRED_GRANT = (
    400,
    {
        "error": "invalid_grant",
        "error_description": "AADB2C90080: The provided grant has expired.",
    },
)


# --------------------------------------------------------------------------- #
# Stand-ins
# --------------------------------------------------------------------------- #
class Registrar:
    """Stands in for `KohlerClient.async_register_mobile_device`, recording each call."""

    def __init__(self) -> None:
        self.backoffs: list[float] = []
        self.stream: KonnectMqttStream | None = None

    async def async_register_mobile_device(self, mobile_device_id: str | None) -> dict:
        if self.stream is not None:
            self.backoffs.append(self.stream._backoff)
        return {
            "ioTHub": "hub.example.net",
            "deviceId": f"mobile-{mobile_device_id}",
            "username": "hub.example.net/user",
            "password": f"SharedAccessSignature sig-{len(self.backoffs)}",
        }


class Capture:
    """A raw or report log that records the thread it was closed on."""

    def __init__(self, fail_writes: int = 0) -> None:
        self.payloads: list[bytes] = []
        self.fail_writes = fail_writes
        self.closed_on: int | None = None

    def write(self, topic: str, payload: bytes, **_: Any) -> None:
        if self.fail_writes:
            self.fail_writes -= 1
            raise RuntimeError("capture bug")
        self.payloads.append(payload)

    def close(self) -> None:
        self.closed_on = threading.get_ident()


class Unreachable(FakeMqttClient):
    """paho's thread when DNS, TCP or TLS fails: `on_connect_fail`, and nothing more.

    Without that handler set, paho on its defaults retried the same host by itself for
    ever and the stream heard nothing at all — which is what this does then too.
    """

    def loop_start(self) -> None:
        on_connect_fail = getattr(self, "on_connect_fail", None)
        if on_connect_fail is not None:
            on_connect_fail(self, None)


class Silent(FakeMqttClient):
    """paho's thread ending on an exception that is not an `OSError`: no callback at all."""

    def loop_start(self) -> None:
        pass


def clients(*kinds: type[FakeMqttClient]) -> Callable[..., FakeMqttClient]:
    """A stand-in for `mqtt.Client` that builds `kinds` in turn, then ordinary fakes."""
    queue = list(kinds)

    def build(**kwargs: Any) -> FakeMqttClient:
        return (queue.pop(0) if queue else FakeMqttClient)(**kwargs)

    return build


def deliver_bytes(client: FakeMqttClient, payload: bytes, rid: str = "1") -> None:
    client.on_message(client, None, Message(TOPIC.format(rid=rid), payload))


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


# --------------------------------------------------------------------------- #
# Messages: nothing a message contains may end the stream
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("attributes", [7, True, 1.5, "text", {"code": "X"}])
async def test_attributes_that_are_not_a_list_do_not_end_the_stream(
    hass: HomeAssistant, make_stream, attributes: object
) -> None:
    """A number raised `TypeError` on paho's thread, which then simply stopped."""
    received: list[Envelope] = []
    stream = make_stream(Registrar(), on_envelope=received.append)
    await stream.async_start()
    client = FakeMqttClient.instances[-1]

    client.deliver(
        {"deviceid": VALVE_ID, "data": {"code": "BAD", "attributes": attributes}}
    )
    client.deliver({"deviceid": VALVE_ID, "data": {"code": "GOOD", "attributes": [{}]}})
    await hass.async_block_till_done()

    assert [(e.code, e.attributes) for e in received] == [("BAD", []), ("GOOD", [{}])]
    assert stream.connected


async def test_a_callback_that_raises_is_logged_and_the_next_message_handled(
    hass: HomeAssistant, make_stream, caplog: pytest.LogCaptureFixture
) -> None:
    """paho re-raises a callback's exception and its thread dies of it, silently.

    Every callback contains its own, and paho is told not to re-raise as a backstop.
    """
    received: list[Envelope] = []
    stream = make_stream(
        Registrar(), on_envelope=received.append, raw_log=Capture(fail_writes=1)
    )
    await stream.async_start()
    client = FakeMqttClient.instances[-1]
    assert client.suppress_exceptions is True

    client.deliver({"data": {"code": "LOST"}}, rid="1")
    client.deliver({"data": {"code": "KEPT"}}, rid="2")
    await hass.async_block_till_done()

    assert "Kohler MQTT message handler raised" in caplog.text
    assert [e.code for e in received] == ["KEPT"]
    assert stream.connected


# --------------------------------------------------------------------------- #
# Reconnecting: one mechanism, and nothing that ends without a retry
# --------------------------------------------------------------------------- #
async def test_paho_is_never_left_to_reconnect_by_itself(
    hass: HomeAssistant, make_stream
) -> None:
    """Its own reconnect reuses the old SAS password and races `_async_reconnect`.

    `tests/test_review_transport.py` shows what that did, against real paho.
    """
    stream = make_stream(Registrar())
    await stream.async_start()
    FakeMqttClient.instances[-1].drop()
    await wait_for(hass, lambda: stream.connected, "the reconnection")
    assert [c.kwargs["reconnect_on_failure"] for c in FakeMqttClient.instances] == [
        False,
        False,
    ]


async def test_a_connection_that_never_opens_is_retried_with_fresh_credentials(
    hass: HomeAssistant, make_stream
) -> None:
    """With paho's own reconnect off, `on_connect_fail` is the only word of it."""
    registrar = Registrar()
    stream = make_stream(registrar)
    with patch(f"{MQTT}.mqtt.Client", clients(Unreachable)):
        await stream.async_start()
        await wait_for(hass, lambda: stream.connected, "the retry to connect")

    first, second = FakeMqttClient.instances
    assert isinstance(first, Unreachable) and first.stopped
    assert second.password == "SharedAccessSignature sig-2"


async def test_a_connection_attempt_that_ends_silently_is_retried(
    hass: HomeAssistant, make_stream, caplog: pytest.LogCaptureFixture
) -> None:
    """The watchdog: without it, a thread that died unreported left the stream down."""
    stream = make_stream(Registrar())
    with (
        patch(f"{MQTT}.CONNECT_TIMEOUT_SECONDS", 0.05),
        patch(f"{MQTT}.mqtt.Client", clients(Silent)),
    ):
        await stream.async_start()
        await wait_for(hass, lambda: stream.connected, "the watchdog's retry")

    assert len(FakeMqttClient.instances) == 2
    assert "did not connect within" in caplog.text


async def test_a_slow_first_connect_is_kept_when_it_lands_during_the_retry_wait(
    hass: HomeAssistant, make_stream
) -> None:
    """The watchdog gave up on it, but it connected after all: keep it."""
    with (
        patch(f"{MQTT}.CONNECT_TIMEOUT_SECONDS", 0.01),
        patch(f"{MQTT}.RECONNECT_MIN_SECONDS", 1),
        patch(f"{MQTT}.mqtt.Client", clients(Silent)),
    ):
        stream = make_stream(Registrar())
        await stream.async_start()
        (slow,) = FakeMqttClient.instances
        await wait_for(hass, lambda: stream._reconnect_task is not None, "the retry")
        # The CONNACK lands during the retry's one-second wait.
        slow.on_connect(slow, None, None, 0)
        assert stream.connected
        await asyncio.wait_for(stream._reconnect_task, 5)

    assert FakeMqttClient.instances == [slow]
    assert not slow.stopped
    assert stream.connected


async def test_a_replaced_client_reporting_its_teardown_changes_nothing(
    hass: HomeAssistant, make_stream
) -> None:
    """paho reports the teardown of a client this stream already replaced.

    It used to count as this stream's own disconnect: `connected` went False under a
    healthy connection, and a reconnect tore that connection down too.
    """
    drops: list[str] = []
    stream = make_stream(Registrar(), on_disconnect=lambda: drops.append("drop"))
    await stream.async_start()
    old = FakeMqttClient.instances[-1]
    old.drop()
    await wait_for(hass, lambda: stream.connected, "the reconnection")

    old.on_disconnect(old, None, 0)
    old.on_connect(old, None, None, 0)
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()

    assert stream.connected
    assert len(FakeMqttClient.instances) == 2
    assert drops == ["drop"]


async def test_a_broker_that_drops_each_connection_at_once_is_backed_off(
    hass: HomeAssistant, make_stream
) -> None:
    """Two Home Assistants sharing one identity knock each other off IoT Hub.

    The backoff reset on every CONNACK, so each reconnected within moments, re-registered
    and reseeded, for ever. The conftest shrinks the bounds to 0.01 s and 0.05 s.
    """
    registrar = Registrar()
    stream = make_stream(registrar)
    await stream.async_start()
    for attempt in range(3):
        FakeMqttClient.instances[-1].drop()
        await wait_for(
            hass,
            lambda attempt=attempt: (
                len(registrar.backoffs) == attempt + 2 and stream.connected
            ),
            "the reconnection",
        )
    assert registrar.backoffs == [0.01, 0.02, 0.04, 0.05]


async def test_paho_callbacks_after_stopping_do_nothing(
    hass: HomeAssistant, make_stream
) -> None:
    """Unloading: hand-offs already queued, and paho's last word, reach nobody."""
    calls: list[str] = []
    raw = Capture()
    stream = make_stream(
        Registrar(),
        on_envelope=lambda envelope: calls.append(envelope.code),
        on_connect=lambda: calls.append("connect"),
        on_disconnect=lambda: calls.append("disconnect"),
        raw_log=raw,
    )
    # Connecting queues the connect hand-off and the message queues its envelope. Both
    # run while `async_stop` waits for paho's thread — after the consumer has gone.
    await stream.async_start()
    client = FakeMqttClient.instances[-1]
    client.deliver({"data": {"code": "LATE"}})
    await stream.async_stop()

    # A CONNACK paho was already waiting for, and a message after it.
    client.on_connect(client, None, None, 0)
    client.deliver({"data": {"code": "LATER"}}, rid="2")
    await hass.async_block_till_done()

    assert calls == []
    assert not stream.connected
    assert raw.payloads == [b'{"data": {"code": "LATE"}}']
    assert len(client.published) == 1


async def test_captures_are_closed_off_the_event_loop(
    hass: HomeAssistant, make_stream
) -> None:
    """Closing flushes a file: blocking I/O, which stalls every task on the loop."""
    raw, report = Capture(), Capture()
    stream = make_stream(Registrar(), raw_log=raw, report_log=report)
    await stream.async_start()
    await stream.async_stop()
    loop_thread = threading.get_ident()
    assert raw.closed_on is not None and raw.closed_on != loop_thread
    assert report.closed_on is not None and report.closed_on != loop_thread


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
class _LateReplies:
    """The real session, with the first replies held back: by a delay, or until an event.

    The request reaches Kohler (the fake) at once, carrying the token it was sent with;
    only its answer comes late.
    """

    def __init__(self, session: Any, *delays: float | asyncio.Event) -> None:
        self._session = session
        self._delays = list(delays)

    def request(self, *args: Any, **kwargs: Any) -> _Late:
        return _Late(self._session.request(*args, **kwargs), self._next())

    def post(self, *args: Any, **kwargs: Any) -> _Late:
        return _Late(self._session.post(*args, **kwargs), self._next())

    def _next(self) -> float | asyncio.Event:
        return self._delays.pop(0) if self._delays else 0


class _Late:
    def __init__(self, request: Any, delay: float | asyncio.Event) -> None:
        self._request = request
        self._delay = delay

    async def __aenter__(self) -> Any:
        response = await self._request.__aenter__()
        if isinstance(self._delay, asyncio.Event):
            await self._delay.wait()
        else:
            await asyncio.sleep(self._delay)
        return response

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._request.__aexit__(*exc)


async def test_parallel_401s_for_one_token_refresh_it_once(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    """Every request sent with the token gets a 401, and each used to discard the token
    the first had just minted — a refresh, a rotation and an entry write apiece."""
    session = async_get_clientsession(hass)
    auth = KohlerAuth(session, "refresh-0")
    persisted: list[str] = []
    auth.on_token_rotated = persisted.append
    await auth.async_get_access_token()
    # Three reads in flight on the same token; their refusals arrive one after another,
    # the later ones after the first has already refreshed.
    client = KohlerClient(_LateReplies(session, 0.01, 0.05, 0.09), auth)
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", (401, {}), (401, {}), (401, {}))

    await asyncio.gather(*(client.async_get_faucet_state(DEVICE_ID) for _ in range(3)))

    assert [r["refresh_token"] for r in kohler.token_requests] == [
        "refresh-0",
        "refresh-1",
    ]
    assert persisted == ["refresh-1", "refresh-2"]


async def test_a_refresh_outlives_a_caller_cancelled_while_it_is_in_flight(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    """Cancelled after B2C has rotated the token but before the reply is read.

    The rotated token used to be lost with the caller, leaving the stored one already
    retired. The next caller now waits for that refresh instead of redeeming it again.
    """
    reply = asyncio.Event()
    auth = KohlerAuth(_LateReplies(async_get_clientsession(hass), reply), "refresh-0")
    persisted: list[str] = []
    auth.on_token_rotated = persisted.append

    doomed = asyncio.create_task(auth.async_get_access_token())
    await wait_for(hass, lambda: len(kohler.token_requests) == 1, "the token request")
    # B2C has redeemed refresh-0 and issued refresh-1; the reply is still on its way.
    doomed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await doomed

    follower = asyncio.create_task(auth.async_get_access_token())
    await asyncio.sleep(0.05)
    assert len(kohler.token_requests) == 1  # waiting, not redeeming refresh-0 again
    reply.set()
    token = await follower

    assert [r["refresh_token"] for r in kohler.token_requests] == ["refresh-0"]
    assert token == make_jwt({"oid": TENANT_ID, "n": 1})
    assert auth.refresh_token == "refresh-1"
    assert persisted == ["refresh-1"]


async def test_a_token_endpoint_408_is_kohler_unavailable(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    """A request timeout is a transient, as `KohlerError.rejected` already says."""
    auth = KohlerAuth(async_get_clientsession(hass), "refresh-0")
    kohler.token_queue.append((408, {"error": "request_timeout"}))
    with pytest.raises(AuthUnavailable) as err:
        await auth.async_get_access_token()
    assert not credential_is_dead(err.value)
    assert auth.refresh_token == "refresh-0"


@pytest.mark.parametrize("expires_in", [None, "soon", -5, 0, True])
async def test_a_token_reply_without_a_usable_lifetime_is_still_kept(
    hass: HomeAssistant, kohler: FakeKohler, expires_in: object
) -> None:
    """`float(None)` raised after B2C had rotated the token, so the new one was lost."""
    auth = KohlerAuth(async_get_clientsession(hass), "refresh-0")
    persisted: list[str] = []
    auth.on_token_rotated = persisted.append
    kohler.token_queue.append(
        (
            200,
            {
                "access_token": make_jwt({"oid": TENANT_ID}),
                "refresh_token": "refresh-9",
                "expires_in": expires_in,
            },
        )
    )

    await auth.async_get_access_token()
    await auth.async_get_access_token()

    assert persisted == ["refresh-9"]
    assert len(kohler.token_requests) == 1
    assert auth.access_token_expires_at == pytest.approx(time.time() + 3600, abs=5)


async def test_a_short_lived_token_is_used_for_half_its_life(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    """Shorter than the margin, it counted as expired on arrival: a refresh per call."""
    auth = KohlerAuth(async_get_clientsession(hass), "refresh-0")
    lifetime = TOKEN_EXPIRY_MARGIN_SECONDS - 100
    kohler.token_queue.append(
        (
            200,
            {
                "access_token": make_jwt({"oid": TENANT_ID}),
                "refresh_token": "refresh-1",
                "expires_in": str(lifetime),
            },
        )
    )
    await auth.async_get_access_token()
    await auth.async_get_access_token()
    assert len(kohler.token_requests) == 1

    # Past half its life it is renewed like any other.
    assert auth._tokens is not None
    auth._tokens.expires_at = time.time() + lifetime / 2 - 1
    await auth.async_get_access_token()
    assert len(kohler.token_requests) == 2


# --------------------------------------------------------------------------- #
# The REST client
# --------------------------------------------------------------------------- #
async def test_a_reply_that_is_not_utf8_is_a_kohler_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A gateway's error page in another charset raised `UnicodeDecodeError`, past
    every caller's `except KohlerError`."""
    aioclient_mock.get(
        re.compile(re.escape(API_BASE)),
        status=502,
        content="<html>Passerelle indisponible, réessayez</html>".encode("latin-1"),
        headers={"Content-Type": "text/html"},
    )
    session = async_get_clientsession(hass)
    auth = KohlerAuth(session)
    auth._tokens = TokenSet(
        make_jwt({"oid": TENANT_ID}), "refresh-0", time.time() + 3600
    )
    client = KohlerClient(session, auth)
    with pytest.raises(KohlerError, match="HTTP 502"):
        await client.async_get_faucet_state(DEVICE_ID)


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(
            {"ioTHubSettings": {"ioTHub": "h", "deviceId": "d"}}, id="no-login"
        ),
        pytest.param(
            {"ioTHubSettings": {"ioTHub": "h", "username": "u", "password": "p"}},
            id="no-device-id",
        ),
        pytest.param(
            {"ioTHubSettings": {"ioTHub": "h", "deviceId": 7, "username": "u"}},
            id="wrong-types",
        ),
        pytest.param(["not", "an", "object"], id="list"),
        pytest.param({"ioTHubSettings": "not an object"}, id="string-settings"),
    ],
)
async def test_unusable_iot_hub_settings_are_an_unexpected_response(
    hass: HomeAssistant, kohler: FakeKohler, reply: object
) -> None:
    """A `KeyError` or `AttributeError` here escaped every caller."""
    session = async_get_clientsession(hass)
    client = KohlerClient(session, KohlerAuth(session, "refresh-0"))
    kohler.fail_api("/mobile/settings", (200, reply))
    with pytest.raises(UnexpectedResponse):
        await client.async_register_mobile_device("ha-identity")


async def test_unusable_iot_hub_settings_at_startup_are_retried(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """It used to fail the whole setup, permanently, rather than retry the stream."""
    kohler.fail_api(
        "/mobile/settings",
        (200, {"ioTHubSettings": {"ioTHub": "h", "deviceId": "d", "username": "u"}}),
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.LOADED
    stream = account(hass, config_entry).stream
    await wait_for(hass, lambda: stream.connected, "the retry to connect")


@pytest.mark.parametrize(
    ("reply", "dead"),
    [
        pytest.param(ClientError("connection reset"), False, id="unreachable"),
        pytest.param(EXPIRED_GRANT, True, id="rejected"),
    ],
)
async def test_a_sign_in_failure_reading_the_fault_log_is_raised_not_answered_empty(
    hass: HomeAssistant, kohler: FakeKohler, reply: object, dead: bool
) -> None:
    """Both callers — the diagnostics report and the seed's fault-log read — catch it.

    Answered with `{}`, as release 1.03 did, it looked like any failed read: neither
    could say that the sign-in was what failed. (Changed 2026-10-16; this test asserted
    the `{}` until then.)
    """
    session = async_get_clientsession(hass)
    client = KohlerClient(session, KohlerAuth(session, "refresh-0"))
    kohler.token_queue.append(reply)
    with pytest.raises(AuthError) as err:
        await client.async_get_gcs_diagnostics(VALVE_ID)
    assert credential_is_dead(err.value) is dead


# --------------------------------------------------------------------------- #
# Signing in again
# --------------------------------------------------------------------------- #
@pytest.fixture
def sign_in() -> Generator[list[tuple[str, str]]]:
    """The server-side B2C sign-in, which drives Kohler's web pages, stood in for."""
    calls: list[tuple[str, str]] = []

    async def _sign_in(self: KohlerAuth, username: str, password: str) -> TokenSet:
        calls.append((username, password))
        if password != PASSWORD:
            raise InvalidCredentials("Kohler rejected that email or password.")
        self._tokens = TokenSet(
            access_token=make_jwt({"oid": TENANT_ID}),
            refresh_token="refresh-signed-in",
            expires_at=time.time() + 3600,
        )
        self._refresh_token = "refresh-signed-in"
        return self._tokens

    with patch.object(KohlerAuth, "async_sign_in", _sign_in):
        yield calls


def _reauth_flows(hass: HomeAssistant) -> list[dict]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_a_mistyped_password_when_signing_in_again_asks_for_it_again(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler, sign_in
) -> None:
    """The error used to land on the *add account* form, with an empty email field —
    and whatever account was typed there replaced this entry's."""
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}
    # The reauth form, its email filled in with the entry's own — since 2026-10-16 it can
    # be changed, for an account whose email has; `test_review2_transport.py` has that.
    email, password = result["data_schema"].schema
    assert (email, password) == (CONF_USERNAME, CONF_PASSWORD)
    assert email.default() == USERNAME

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert sign_in == [(USERNAME, "wrong"), (USERNAME, PASSWORD)]
    assert config_entry.data[CONF_USERNAME] == USERNAME


async def test_signing_in_again_refuses_an_entry_that_is_not_that_account(
    hass: HomeAssistant, kohler: FakeKohler, sign_in
) -> None:
    """The unique-id check that adding an account makes, which reauth used to skip."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="someone-else@example.com",
        data={CONF_USERNAME: USERNAME, CONF_REFRESH_TOKEN: "refresh-0"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_REFRESH_TOKEN] == "refresh-0"
    await unload(hass, entry)


@pytest.fixture
async def valve_entry(
    hass: HomeAssistant, kohler: FakeKohler
) -> AsyncGenerator[MockConfigEntry]:
    """A valve account whose model was chosen by hand, as when detection fails."""
    kohler.devices.append(dict(VALVE))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={
            CONF_USERNAME: USERNAME,
            CONF_REFRESH_TOKEN: "refresh-0",
            CONF_TENANT_ID: TENANT_ID,
            CONF_TEMPERATURE_UNIT: "Fahrenheit",
            CONF_WATER_UNITS: "Metric",
            CONF_MOBILE_DEVICE_ID: MOBILE_ID,
            CONF_VALVE_MODEL: "K-28212",
            CONF_ZONE_OUTLETS: [3, 3],
        },
    )
    entry.add_to_hass(hass)
    yield entry
    await unload(hass, entry)


async def test_signing_in_again_replaces_only_the_credential(
    hass: HomeAssistant, valve_entry: MockConfigEntry, kohler: FakeKohler, sign_in
) -> None:
    """Valve detection used to run again: failing, it showed the model picker, and its
    answer — and the account's units — overwrote the entry's."""
    kohler.valve_outlets = None  # detection would fail now
    kohler.water_units = "Standard"
    before = dict(valve_entry.data)

    result = await valve_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()

    changed = {k for k in valve_entry.data if valve_entry.data[k] != before.get(k)}
    assert changed <= {CONF_REFRESH_TOKEN}
    assert valve_entry.data[CONF_VALVE_MODEL] == "K-28212"
    assert valve_entry.data[CONF_ZONE_OUTLETS] == [3, 3]
    assert valve_entry.data[CONF_WATER_UNITS] == "Metric"


async def test_signing_in_again_does_not_wait_for_the_reload(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
    sign_in,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reload used to run inside the flow's step, and the dialog sat through all of
    it — every seed read and the stream's connect."""
    caplog.set_level(logging.WARNING)
    before = account(hass, setup_entry)
    result = await setup_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    # Still the old set-up, or on its way out; not yet the new one.
    assert hass.data.get(DOMAIN, {}).get(setup_entry.entry_id) in (before, None)

    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.LOADED
    assert account(hass, setup_entry) is not before
    assert account(hass, setup_entry).auth.refresh_token != "refresh-signed-in"
    # Nor reported as an update listener's entry reloading itself.
    assert "update listener" not in caplog.text


async def test_a_reload_that_fails_after_signing_in_again_asks_again(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler, sign_in
) -> None:
    """A credential refused again on reload raises a new prompt, not silence."""
    result = await config_entry.start_reauth_flow(hass)
    first = result["flow_id"]
    # The token signing in just issued is already refused when the reload uses it.
    kohler.token_queue.append(EXPIRED_GRANT)
    result = await hass.config_entries.flow.async_configure(
        first, {CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"

    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    (again,) = _reauth_flows(hass)
    assert again["flow_id"] != first


async def test_a_dead_credential_still_fails_signing_in_again_as_a_cannot_connect(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """Errors other than a wrong password stay on the same form too."""

    async def _down(self: KohlerAuth, username: str, password: str) -> TokenSet:
        raise AuthError("Kohler sign-in is down")

    with patch.object(KohlerAuth, "async_sign_in", _down):
        result = await config_entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: PASSWORD}
        )
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "cannot_connect"}
    assert config_entry.data[CONF_REFRESH_TOKEN] == "refresh-0"
