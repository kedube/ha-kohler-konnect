"""The transport, second pass: credentials kept current, stops that end, reauth that heals.

Each test pins one fix from the second 2026-10-16 review of `konnect/mqtt.py`,
`konnect/auth.py`, `konnect/client.py` and the config flow, and fails on the code before it
unless it says otherwise. The stream's lock against paho's own thread, and a stop during a
real TLS handshake, are checked against real paho-mqtt in `tests/test_review2_transport.py`;
here paho is `FakeMqttClient`, as everywhere else.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kohler_konnect.const import (
    CONF_MOBILE_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    CONF_VALVE_MODEL,
    DOMAIN,
)
from custom_components.kohler_konnect.konnect import (
    AuthError,
    AuthUnavailable,
    InvalidCredentials,
    KohlerAuth,
    KohlerClient,
    KonnectMqttStream,
    TokenSet,
    credential_is_dead,
)

from .conftest import (
    MOBILE_ID,
    TENANT_ID,
    USERNAME,
    VALVE,
    VALVE_ID,
    FakeKohler,
    FakeMqttClient,
    make_jwt,
    unload,
    wait_for,
)

MQTT = "custom_components.kohler_konnect.konnect.mqtt"
PASSWORD = "correct-horse-battery-staple"
# What B2C answers for a refresh token that has already been redeemed.
USED_GRANT = (
    400,
    {
        "error": "invalid_grant",
        "error_description": "AADB2C90129: The provided grant has been revoked.",
    },
)
# Other Kohler accounts. The email an account signs in with can change; its id cannot.
OTHER = "other@example.com"
OTHER_TENANT = "9e9e9e9e-5555-6666-7777-888888888888"
FIRST = "first@example.com"
FIRST_TENANT = "1a1a1a1a-2222-3333-4444-555555555555"
RENAMED = "Renamed@Example.com"


# --------------------------------------------------------------------------- #
# Stand-ins
# --------------------------------------------------------------------------- #
@pytest.fixture
def sign_in() -> Generator[list[tuple[str, str]]]:
    """B2C sign-in, stood in for: each email signs in as the Kohler account it belongs to.

    `RENAMED` is the owner's account under a new email: the same account id as `USERNAME`.
    """
    accounts = {
        USERNAME: TENANT_ID,
        RENAMED.lower(): TENANT_ID,
        OTHER: OTHER_TENANT,
        FIRST: FIRST_TENANT,
    }
    calls: list[tuple[str, str]] = []

    async def _sign_in(self: KohlerAuth, username: str, password: str) -> TokenSet:
        calls.append((username, password))
        tenant = accounts.get(username.strip().lower())
        if password != PASSWORD or tenant is None:
            raise InvalidCredentials("Kohler rejected that email or password.")
        self._tokens = TokenSet(
            access_token=make_jwt({"oid": tenant}),
            refresh_token="refresh-signed-in",
            expires_at=time.time() + 3600,
        )
        self._refresh_token = "refresh-signed-in"
        return self._tokens

    with patch.object(KohlerAuth, "async_sign_in", _sign_in):
        yield calls


class Registrar:
    """Stands in for `KohlerClient.async_register_mobile_device`."""

    calls = 0

    async def async_register_mobile_device(self, mobile_device_id: str | None) -> dict:
        self.calls += 1
        return {
            "ioTHub": "hub.example.net",
            "deviceId": f"mobile-{mobile_device_id}",
            "username": "hub.example.net/user",
            "password": f"SharedAccessSignature sig-{self.calls}",
        }


class Capture:
    """A raw or report log that records when it was closed."""

    def __init__(self) -> None:
        self.closed = threading.Event()

    def write(self, topic: str, payload: bytes, **_: Any) -> None:
        pass

    def close(self) -> None:
        self.closed.set()


def clients(*kinds: type[FakeMqttClient]) -> Callable[..., FakeMqttClient]:
    """A stand-in for `mqtt.Client` that builds `kinds` in turn, then ordinary fakes."""
    queue = list(kinds)

    def build(**kwargs: Any) -> FakeMqttClient:
        return (queue.pop(0) if queue else FakeMqttClient)(**kwargs)

    return build


def stuck_until(released: threading.Event) -> type[FakeMqttClient]:
    """paho inside a connection attempt — DNS, TCP, a TLS handshake — which nothing cuts
    short: `loop_stop()` waits until `released`."""

    class Stuck(FakeMqttClient):
        def loop_stop(self) -> None:
            released.wait(10)
            super().loop_stop()

    return Stuck


@pytest.fixture
async def make_stream() -> AsyncGenerator[Callable[..., KonnectMqttStream]]:
    """Build streams on the test's loop, and stop every one of them afterwards."""
    made: list[KonnectMqttStream] = []

    def build(**kwargs: Any) -> KonnectMqttStream:
        kwargs.setdefault("on_envelope", lambda envelope: None)
        stream = KonnectMqttStream(
            Registrar(),  # type: ignore[arg-type]
            mobile_device_id=MOBILE_ID,
            ssl_context=object(),  # type: ignore[arg-type]
            **kwargs,
        )
        made.append(stream)
        return stream

    yield build
    for stream in made:
        await stream.async_stop()


class _HeldReplies:
    """The real session, with the first reply held back until `release` is set.

    The request reaches Kohler (the fake) at once; only its answer comes late.
    """

    def __init__(self, session: Any, release: asyncio.Event) -> None:
        self._session = session
        self._release: asyncio.Event | None = release

    def post(self, *args: Any, **kwargs: Any) -> _Held:
        release, self._release = self._release, None
        return _Held(self._session.post(*args, **kwargs), release)


class _Held:
    def __init__(self, request: Any, release: asyncio.Event | None) -> None:
        self._request = request
        self._release = release

    async def __aenter__(self) -> Any:
        response = await self._request.__aenter__()
        if self._release is not None:
            await self._release.wait()
        return response

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._request.__aexit__(*exc)


# --------------------------------------------------------------------------- #
# 1. Adding an account stores the credential as it is at the end, not at sign-in
# --------------------------------------------------------------------------- #
async def _add_account(hass: HomeAssistant, username: str = USERNAME) -> Any:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: username, CONF_PASSWORD: PASSWORD}
    )


async def test_a_token_refreshed_while_adding_the_account_is_the_one_stored(
    hass: HomeAssistant, kohler: FakeKohler, sign_in
) -> None:
    """A 401 on the account read refreshes the token, and B2C retires the one sign-in
    issued. The entry stored that one anyway: its first start was refused, and the owner
    was asked to sign in again straight after adding the account."""
    kohler.fail_api(
        f"/customer-device/{TENANT_ID}", (401, {"message": "not yet valid"})
    )
    with patch("custom_components.kohler_konnect.async_setup_entry", return_value=True):
        result = await _add_account(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [r["refresh_token"] for r in kohler.token_requests] == ["refresh-signed-in"]
    assert result["data"][CONF_REFRESH_TOKEN] == "refresh-1"
    assert result["data"][CONF_TENANT_ID] == TENANT_ID


async def test_a_token_refreshed_before_the_valve_model_is_chosen_is_the_one_stored(
    hass: HomeAssistant, kohler: FakeKohler, sign_in
) -> None:
    """The entry is created a step later, once the owner has picked the model."""
    kohler.devices.append(dict(VALVE))
    kohler.valve_outlets = None  # detection fails, so the model is asked for
    kohler.fail_api(f"/gcs-state/gcsadvancestate/{VALVE_ID}", (401, {}))
    result = await _add_account(hass)
    assert result["step_id"] == "valve"

    with patch("custom_components.kohler_konnect.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_VALVE_MODEL: "K-28212"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [r["refresh_token"] for r in kohler.token_requests] == ["refresh-signed-in"]
    assert result["data"][CONF_REFRESH_TOKEN] == "refresh-1"


# --------------------------------------------------------------------------- #
# 3. Stopping waits for paho's thread, but not for ever
# --------------------------------------------------------------------------- #
async def test_stopping_does_not_wait_out_a_connection_attempt(
    hass: HomeAssistant, make_stream
) -> None:
    """`loop_stop()` joins a thread that can sit in a TLS handshake until the keepalive
    runs out: unload, reload and shutdown each waited up to a minute for it."""
    released = threading.Event()
    # Released after three seconds regardless, so the code before the fix fails on the
    # time it took rather than hanging for the ten-second backstop.
    timer = threading.Timer(3, released.set)
    raw, report = Capture(), Capture()
    stream = make_stream(raw_log=raw, report_log=report)
    with (
        patch(f"{MQTT}.mqtt.Client", clients(stuck_until(released))),
        patch(f"{MQTT}.STOP_TIMEOUT_SECONDS", 0.05),
    ):
        await stream.async_start()
        (client,) = FakeMqttClient.instances
        timer.start()
        try:
            started = time.monotonic()
            await stream.async_stop()
            took = time.monotonic() - started
            assert took < 1.5
            # The captures are closed once the thread has ended — a message it was still
            # handling would reopen a raw capture closed under it — and not before.
            assert not client.stopped
            assert not raw.closed.is_set() and not report.closed.is_set()
        finally:
            released.set()
            timer.cancel()
    await hass.async_add_executor_job(raw.closed.wait, 5)
    await hass.async_add_executor_job(report.closed.wait, 5)
    assert client.stopped
    assert not stream.connected


async def test_a_reconnect_waits_for_the_old_connection_attempt_to_end(
    hass: HomeAssistant, make_stream
) -> None:
    """Unlike a stop, a reconnect does wait out the old network thread (2026-10-16).

    An attempt abandoned mid-handshake could otherwise finish later and connect with the
    same IoT Hub client id after its replacement was up, which kicks the replacement off.
    So the next client starts only once the old thread has ended.
    """
    released = threading.Event()
    stream = make_stream()
    with (
        patch(f"{MQTT}.mqtt.Client", clients(stuck_until(released))),
        patch(f"{MQTT}.STOP_TIMEOUT_SECONDS", 0.05),
    ):
        await stream.async_start()
        (old,) = FakeMqttClient.instances
        try:
            old.drop()
            await asyncio.sleep(0.3)  # well past the stop's bound
            assert len(FakeMqttClient.instances) == 1, "a client started over the old"
        finally:
            released.set()
        await wait_for(
            hass,
            lambda: stream.connected and len(FakeMqttClient.instances) == 2,
            "the reconnection",
        )
        assert old.stopped
        assert stream._mqtt is FakeMqttClient.instances[1]


async def test_a_reconnect_waits_out_an_attempt_that_will_not_end_and_says_so(
    hass: HomeAssistant, make_stream, caplog: pytest.LogCaptureFixture
) -> None:
    """Never a second client beside one that can still connect with the same client id.

    Waited for in silence, a lookup that hung — DNS has no timeout of its own — kept the
    stream down with nothing logged; going on after a bound instead left one more stuck
    client behind at every cycle, each blocking a worker, all of them free to connect and
    knock the live one off when the hang cleared (2026-10-16). Now the wait is said, again
    and again, and the reconnect follows the old thread's end.
    """
    released = threading.Event()
    stream = make_stream()
    with (
        patch(f"{MQTT}.mqtt.Client", clients(stuck_until(released))),
        patch(f"{MQTT}.STOP_TIMEOUT_SECONDS", 0.05),
        patch(f"{MQTT}.CONNECT_TIMEOUT_SECONDS", 0.1),
    ):
        await stream.async_start()
        (old,) = FakeMqttClient.instances
        try:
            old.drop()
            await asyncio.sleep(0.5)  # several bounds
            assert len(FakeMqttClient.instances) == 1, "a client started over the old"
            assert "still waiting for an abandoned connection attempt" in caplog.text
        finally:
            released.set()
        await wait_for(
            hass,
            lambda: stream.connected and len(FakeMqttClient.instances) == 2,
            "the reconnection",
        )


# --------------------------------------------------------------------------- #
# 4. A refresh refused because another sign-in already redeemed the token
# --------------------------------------------------------------------------- #
async def test_a_reload_with_a_refresh_in_flight_does_not_cost_a_sign_in(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    """The unloaded coordinator's refresh, shielded, redeems the entry's token; the new
    coordinator, built from the entry before that refresh stored its result, sends the same
    token again and is refused. That refusal was taken for a dead credential."""
    entry = {"token": "refresh-0"}

    def store(token: str) -> None:
        entry["token"] = token

    session = async_get_clientsession(hass)
    reply = asyncio.Event()
    old = KohlerAuth(
        _HeldReplies(session, reply),
        entry["token"],
        refresh_token_source=lambda: entry["token"],
    )
    old.on_token_rotated = store
    in_flight = asyncio.create_task(old.async_get_access_token())
    await wait_for(hass, lambda: len(kohler.token_requests) == 1, "the old refresh")

    new = KohlerAuth(
        session, entry["token"], refresh_token_source=lambda: entry["token"]
    )
    new.on_token_rotated = store
    reply.set()
    await in_flight
    assert entry["token"] == "refresh-1"
    kohler.token_queue.append(USED_GRANT)  # B2C redeemed refresh-0 for the old one

    token = await new.async_get_access_token()

    assert [r["refresh_token"] for r in kohler.token_requests] == [
        "refresh-0",
        "refresh-0",
        "refresh-1",
    ]
    assert token == make_jwt({"oid": TENANT_ID, "n": 2})
    assert new.refresh_token == entry["token"] == "refresh-2"


async def test_a_refused_token_the_source_still_holds_is_not_sent_again(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    """Nobody has a newer one: the credential is dead, and that is still the verdict."""
    auth = KohlerAuth(
        async_get_clientsession(hass),
        "refresh-0",
        refresh_token_source=lambda: "refresh-0",
    )
    kohler.token_queue.append(USED_GRANT)
    with pytest.raises(AuthError) as err:
        await auth.async_get_access_token()
    assert credential_is_dead(err.value)
    assert [r["refresh_token"] for r in kohler.token_requests] == ["refresh-0"]


async def test_a_newer_token_is_tried_once_and_never_in_a_loop(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    """A source with a new answer every time still gets one retry per refresh."""
    answers = iter(f"refresh-stored-{n}" for n in range(1, 10))
    auth = KohlerAuth(
        async_get_clientsession(hass),
        "refresh-0",
        refresh_token_source=lambda: next(answers),
    )
    kohler.token_queue.extend([USED_GRANT, USED_GRANT])
    with pytest.raises(AuthError) as err:
        await auth.async_get_access_token()
    assert credential_is_dead(err.value)
    assert [r["refresh_token"] for r in kohler.token_requests] == [
        "refresh-0",
        "refresh-stored-1",
    ]
    # And the next refresh starts from the newer token, not the one first refused.
    await auth.async_get_access_token()
    assert kohler.token_requests[-1]["refresh_token"] == "refresh-stored-1"


@pytest.mark.parametrize(
    ("reply", "raised"),
    [
        pytest.param((503, {"error": "temporarily_unavailable"}), AuthUnavailable),
        pytest.param((400, {"error": "invalid_client"}), AuthError),
    ],
    ids=["outage", "not-a-grant-refusal"],
)
async def test_the_source_is_read_only_for_a_refused_grant(
    hass: HomeAssistant, kohler: FakeKohler, reply: tuple, raised: type[AuthError]
) -> None:
    reads: list[str] = []

    def source() -> str:
        reads.append("read")
        return "refresh-newer"

    auth = KohlerAuth(
        async_get_clientsession(hass), "refresh-0", refresh_token_source=source
    )
    kohler.token_queue.append(reply)
    with pytest.raises(raised):
        await auth.async_get_access_token()
    assert reads == []
    assert len(kohler.token_requests) == 1


@pytest.mark.parametrize("answer", [None, ""])
async def test_a_source_with_nothing_newer_changes_nothing(
    hass: HomeAssistant, kohler: FakeKohler, answer: str | None
) -> None:
    auth = KohlerAuth(
        async_get_clientsession(hass), "refresh-0", refresh_token_source=lambda: answer
    )
    kohler.token_queue.append(USED_GRANT)
    with pytest.raises(AuthError):
        await auth.async_get_access_token()
    assert len(kohler.token_requests) == 1
    assert auth.refresh_token == "refresh-0"


async def test_a_source_that_fails_is_a_refusal_as_before(
    hass: HomeAssistant, kohler: FakeKohler, caplog: pytest.LogCaptureFixture
) -> None:
    def source() -> str:
        raise RuntimeError("entry gone")

    auth = KohlerAuth(
        async_get_clientsession(hass), "refresh-0", refresh_token_source=source
    )
    kohler.token_queue.append(USED_GRANT)
    with pytest.raises(AuthError) as err:
        await auth.async_get_access_token()
    assert credential_is_dead(err.value)
    assert len(kohler.token_requests) == 1
    assert "Could not read the stored Kohler refresh token" in caplog.text


# --------------------------------------------------------------------------- #
# 5. Signing in again: the same account, whatever its email is now
# --------------------------------------------------------------------------- #
async def _sign_in_again(
    hass: HomeAssistant, entry: MockConfigEntry, **user_input: str
) -> Any:
    result = await entry.start_reauth_flow(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD, **user_input}
    )


@pytest.fixture
async def damaged_entry(
    hass: HomeAssistant, kohler: FakeKohler
) -> AsyncGenerator[MockConfigEntry]:
    """An entry the reauth bug fixed in 1.03 left half another account's.

    Set up for `FIRST`, and later signed in again as the owner: the owner's email, token
    and account id were merged in, under `FIRST`'s unique id.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Kohler Konnect ({FIRST})",
        unique_id=FIRST,
        data={
            CONF_USERNAME: USERNAME,
            CONF_REFRESH_TOKEN: "refresh-0",
            CONF_TENANT_ID: TENANT_ID,
            CONF_MOBILE_DEVICE_ID: MOBILE_ID,
        },
    )
    entry.add_to_hass(hass)
    yield entry
    await unload(hass, entry)


async def test_an_account_whose_email_changed_can_sign_in_again_with_the_new_one(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler, sign_in
) -> None:
    """The form asked only for a password, for an email Kohler no longer knew."""
    result = await config_entry.start_reauth_flow(hass)
    # A mistyped password keeps the email as typed, rather than the entry's.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: RENAMED, CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    email, _ = result["data_schema"].schema
    assert email.default() == RENAMED

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: RENAMED, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()

    assert config_entry.unique_id == RENAMED.lower()
    assert config_entry.data[CONF_USERNAME] == RENAMED
    assert config_entry.data[CONF_TENANT_ID] == TENANT_ID
    assert config_entry.data[CONF_REFRESH_TOKEN] != "refresh-0"
    assert config_entry.state is ConfigEntryState.LOADED
    # The title setup gave it follows the email.
    assert config_entry.title == f"Kohler Konnect ({RENAMED})"


async def test_a_title_the_owner_chose_is_kept_when_the_email_changes(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler, sign_in
) -> None:
    hass.config_entries.async_update_entry(config_entry, title="Upstairs shower")
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: RENAMED, CONF_PASSWORD: PASSWORD}
    )
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert config_entry.data[CONF_USERNAME] == RENAMED
    assert config_entry.title == "Upstairs shower"


async def test_an_entry_left_half_another_accounts_signs_in_again_as_that_account(
    hass: HomeAssistant, damaged_entry: MockConfigEntry, kohler: FakeKohler, sign_in
) -> None:
    """Every reauth asked for the owner's password, then refused the owner as "a
    different Kohler account" — the unique id was still `FIRST`'s. Only deleting the entry
    got out of it."""
    result = await _sign_in_again(hass, damaged_entry)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()

    assert sign_in == [(USERNAME, PASSWORD)]
    assert damaged_entry.unique_id == USERNAME
    assert damaged_entry.data[CONF_USERNAME] == USERNAME
    assert damaged_entry.data[CONF_REFRESH_TOKEN] != "refresh-0"
    assert damaged_entry.state is ConfigEntryState.LOADED


async def test_an_entry_left_half_another_accounts_can_go_back_to_its_own(
    hass: HomeAssistant, damaged_entry: MockConfigEntry, kohler: FakeKohler, sign_in
) -> None:
    """The login its unique id names is accepted too — and the entry is all its again."""
    result = await _sign_in_again(hass, damaged_entry, **{CONF_USERNAME: FIRST})
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()

    assert damaged_entry.unique_id == FIRST
    assert damaged_entry.data[CONF_USERNAME] == FIRST
    assert damaged_entry.data[CONF_TENANT_ID] == FIRST_TENANT


async def test_signing_in_again_as_an_unrelated_account_is_still_refused(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler, sign_in
) -> None:
    """Neither the login nor the account id: the hole 1.03 closed stays closed."""
    before = (config_entry.unique_id, dict(config_entry.data))
    result = await _sign_in_again(hass, config_entry, **{CONF_USERNAME: OTHER})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert (config_entry.unique_id, dict(config_entry.data)) == before


async def test_moving_an_entry_onto_an_account_another_entry_has_is_refused(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    damaged_entry: MockConfigEntry,
    kohler: FakeKohler,
    sign_in,
) -> None:
    """The owner's account has an entry of its own: two for one account would follow."""
    before = (damaged_entry.unique_id, dict(damaged_entry.data))
    result = await _sign_in_again(hass, damaged_entry)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert (damaged_entry.unique_id, dict(damaged_entry.data)) == before
    assert config_entry.unique_id == USERNAME


# --------------------------------------------------------------------------- #
# 7. The fault log: a failed read, a sign-in that failed, and a log, told apart
# --------------------------------------------------------------------------- #
async def test_the_fault_log_is_the_reply_as_sent_or_empty_when_the_read_fails(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    """The contract the seed's fault-log read relies on: `{}` has no `errorDetails`, so a
    failed read never passes for an empty log. (Held before this change too; the change
    is in `test_review_transport.py`, where a sign-in failure is now raised.)"""
    session = async_get_clientsession(hass)
    client = KohlerClient(session, KohlerAuth(session, "refresh-0"))
    log = {"deviceId": VALVE_ID, "errorDetails": []}
    kohler.fail_api(f"/gcs-diagnostics/{VALVE_ID}", (200, log), (503, {"message": "x"}))
    assert await client.async_get_gcs_diagnostics(VALVE_ID) == log
    assert await client.async_get_gcs_diagnostics(VALVE_ID) == {}
