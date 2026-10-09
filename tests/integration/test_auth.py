"""Keeping a Kohler token alive, against the fake B2C token endpoint.

B2C rotates the refresh token on every use, so a rotation that is not persisted strands the
account. And how a token failure is classified decides whether the owner is asked to sign
in again: a reauth card on a Kohler outage is as wrong as silence on a revoked account.
`docs/protocol/platform.md` §1 is the reference.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp import ClientError
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kohler_konnect.konnect import (
    AuthError,
    AuthUnavailable,
    InvalidCredentials,
    KohlerAuth,
    KohlerClient,
    SignInBlocked,
    credential_is_dead,
)
from custom_components.kohler_konnect.konnect.const import (
    B2C_SCOPE,
    B2C_TOKEN_URL,
    CLIENT_ID,
    TOKEN_EXPIRY_MARGIN_SECONDS,
)

from .conftest import DEVICE_ID, TENANT_ID, FakeKohler, make_jwt

EXPIRED_GRANT = (
    400,
    {
        "error": "invalid_grant",
        "error_description": "AADB2C90080: The provided grant has expired.",
    },
)
HTML_OUTAGE = (503, "<html><body><h1>503 Service Unavailable</h1></body></html>")
# RFC 6749 §5.2's own word for "try again later", with the status to match.
JSON_OUTAGE = (503, {"error": "temporarily_unavailable"})


@pytest.fixture
def auth(hass: HomeAssistant, kohler: FakeKohler) -> KohlerAuth:
    return KohlerAuth(async_get_clientsession(hass), "refresh-0")


def _tokens(refresh: str | None = "refresh-9", expires_in: object = 3600) -> tuple:
    body: dict[str, object] = {
        "access_token": make_jwt({"oid": TENANT_ID, "n": 99}),
        "expires_in": expires_in,
    }
    if refresh is not None:
        body["refresh_token"] = refresh
    return (200, body)


# --------------------------------------------------------------------------- #
# Refresh and rotation
# --------------------------------------------------------------------------- #
async def test_a_refresh_redeems_the_stored_token_for_the_signin_policy_scope(
    auth: KohlerAuth, kohler: FakeKohler
) -> None:
    """Only a `B2C_1A_signin` token is accepted for Anthem writes."""
    token = await auth.async_get_access_token()

    assert kohler.token_requests == [
        {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": "refresh-0",
            "scope": B2C_SCOPE,
        }
    ]
    _, url, _, headers = kohler.mocker.mock_calls[-1]
    assert str(url) == B2C_TOKEN_URL
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert token == make_jwt({"oid": TENANT_ID, "n": 1})
    assert auth.tenant_id == TENANT_ID


async def test_each_rotation_is_persisted_and_the_retired_token_never_reused(
    auth: KohlerAuth, kohler: FakeKohler
) -> None:
    """Persistence is a property of rotation, not something each caller must remember."""
    persisted: list[str] = []
    auth.on_token_rotated = persisted.append

    await auth.async_get_access_token()
    auth.invalidate_access_token()
    await auth.async_get_access_token()

    assert persisted == ["refresh-1", "refresh-2"]
    assert [r["refresh_token"] for r in kohler.token_requests] == [
        "refresh-0",
        "refresh-1",
    ]
    assert auth.refresh_token == "refresh-2"


@pytest.mark.parametrize("refresh", [None, "refresh-0"], ids=["omitted", "unchanged"])
async def test_a_reply_that_does_not_rotate_keeps_the_token_and_persists_nothing(
    auth: KohlerAuth, kohler: FakeKohler, refresh: str | None
) -> None:
    persisted: list[str] = []
    auth.on_token_rotated = persisted.append
    kohler.token_queue.append(_tokens(refresh))

    await auth.async_get_access_token()

    assert auth.refresh_token == "refresh-0"
    assert persisted == []


async def test_a_valid_access_token_is_reused_until_it_enters_the_expiry_margin(
    auth: KohlerAuth, kohler: FakeKohler
) -> None:
    """Refreshing is a network round trip; doing it per request would double every call."""
    kohler.token_queue.append(_tokens("refresh-1", TOKEN_EXPIRY_MARGIN_SECONDS + 60))
    await auth.async_get_access_token()
    await auth.async_get_access_token()
    assert len(kohler.token_requests) == 1
    assert auth.access_token_expires_at == pytest.approx(
        time.time() + TOKEN_EXPIRY_MARGIN_SECONDS + 60, abs=5
    )

    # A token that lands already inside the margin is renewed on the very next use.
    auth.invalidate_access_token()
    kohler.token_queue.append(
        _tokens("refresh-2", str(TOKEN_EXPIRY_MARGIN_SECONDS - 1))
    )
    await auth.async_get_access_token()
    await auth.async_get_access_token()
    assert [r["refresh_token"] for r in kohler.token_requests] == [
        "refresh-0",
        "refresh-1",
        "refresh-2",
    ]


async def test_invalidating_the_access_token_keeps_the_refresh_token(
    auth: KohlerAuth, kohler: FakeKohler
) -> None:
    """The 401 path: the access token was refused, the refresh token was not."""
    await auth.async_get_access_token()
    auth.invalidate_access_token()
    assert auth.access_token_expires_at is None
    assert auth.tenant_id is None
    assert auth.refresh_token == "refresh-1"
    assert auth.has_credentials


async def test_with_no_refresh_token_nothing_is_sent_and_the_owner_must_sign_in(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    auth = KohlerAuth(async_get_clientsession(hass))
    assert not auth.has_credentials
    with pytest.raises(AuthError, match="sign in again") as err:
        await auth.async_get_access_token()
    assert credential_is_dead(err.value)
    assert kohler.token_requests == []


class _SlowTokenEndpoint:
    """The real session, with each token request held open for a moment.

    The aiohttp mock answers without ever suspending, so concurrent callers would simply
    run one after another and the refresh lock would never be contended. A real token
    round trip takes hundreds of milliseconds; this restores that window.
    """

    def __init__(self, session) -> None:
        self._session = session

    def post(self, *args, **kwargs):
        return _Delayed(self._session.post(*args, **kwargs))


class _Delayed:
    def __init__(self, request) -> None:
        self._request = request

    async def __aenter__(self):
        await asyncio.sleep(0.01)
        return await self._request.__aenter__()

    async def __aexit__(self, *exc):
        return await self._request.__aexit__(*exc)


async def test_simultaneous_expiries_redeem_the_refresh_token_only_once(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    """B2C retires a token on redemption; the loser of a double redemption looks dead.

    Three callers find the token rejected at once — the coordinator's tasks firing
    together after an expiry. The first redeems; the others wait and reuse its result.
    """
    auth = KohlerAuth(_SlowTokenEndpoint(async_get_clientsession(hass)), "refresh-0")

    async def renew() -> str:
        auth.invalidate_access_token()
        return await auth.async_get_access_token()

    tokens = await asyncio.gather(renew(), renew(), renew())

    assert [r["refresh_token"] for r in kohler.token_requests] == ["refresh-0"]
    assert len(set(tokens)) == 1
    assert auth.refresh_token == "refresh-1"


async def test_a_401_during_a_request_retries_once_with_a_fresh_token(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    """Through the refresh lock, so the retry cannot double-redeem the refresh token."""
    session = async_get_clientsession(hass)
    client = KohlerClient(session, KohlerAuth(session, "refresh-0"))
    await client.async_get_faucet_state(DEVICE_ID)
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", (401, {}))

    await client.async_get_faucet_state(DEVICE_ID)

    bearers = [
        headers["Authorization"]
        for _, url, _, headers in kohler.mocker.mock_calls
        if "/faucet-state/" in str(url)
    ]
    assert bearers == [
        f"Bearer {make_jwt({'oid': TENANT_ID, 'n': 1})}",
        f"Bearer {make_jwt({'oid': TENANT_ID, 'n': 1})}",  # refused
        f"Bearer {make_jwt({'oid': TENANT_ID, 'n': 2})}",
    ]
    assert [r["refresh_token"] for r in kohler.token_requests] == [
        "refresh-0",
        "refresh-1",
    ]


# --------------------------------------------------------------------------- #
# Classifying a failed refresh
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("reply", "expected", "match", "dead"),
    [
        (EXPIRED_GRANT, SignInBlocked, "AADB2C90080", True),
        ((400, {"error": "invalid_grant"}), AuthError, "invalid_grant", True),
        (
            (400, {"error_description": "AADB2C90053: bad credentials"}),
            InvalidCredentials,
            "rejected",
            True,
        ),
        (
            (400, {"error_description": "AADB2C90006: redirect uri"}),
            AuthError,
            "redirect URI",
            True,
        ),
        ((400, {}), AuthError, "HTTP 400", True),
        ((200, {"refresh_token": "refresh-9"}), AuthError, "usable token pair", True),
        # Kohler unreachable: not the credential's fault, and not the owner's to fix.
        (ClientError("connection reset"), AuthUnavailable, "connection reset", False),
    ],
)
async def test_a_failed_refresh_says_whether_the_credential_is_dead(
    auth: KohlerAuth,
    kohler: FakeKohler,
    reply: object,
    expected: type[AuthError],
    match: str,
    dead: bool,
) -> None:
    kohler.token_queue.append(reply)
    with pytest.raises(AuthError, match=match) as err:
        await auth.async_get_access_token()
    assert type(err.value) is expected
    assert credential_is_dead(err.value) is dead


async def test_a_failed_refresh_keeps_the_refresh_token_for_the_next_attempt(
    auth: KohlerAuth, kohler: FakeKohler
) -> None:
    """Dropping it on a blip would turn a transient outage into a forced re-sign-in."""
    kohler.token_queue.append(ClientError("connection reset"))
    with pytest.raises(AuthUnavailable):
        await auth.async_get_access_token()
    assert auth.refresh_token == "refresh-0"

    await auth.async_get_access_token()
    assert [r["refresh_token"] for r in kohler.token_requests] == [
        "refresh-0",
        "refresh-0",
    ]


async def test_a_token_endpoint_timeout_is_kohler_unavailable(
    auth: KohlerAuth, kohler: FakeKohler
) -> None:
    kohler.token_queue.append(TimeoutError())
    with pytest.raises(AuthUnavailable):
        await auth.async_get_access_token()


async def test_a_non_json_token_reply_is_kohler_unavailable(
    auth: KohlerAuth, kohler: FakeKohler
) -> None:
    kohler.token_queue.append(HTML_OUTAGE)
    with pytest.raises(AuthUnavailable):
        await auth.async_get_access_token()


async def test_a_token_endpoint_server_error_does_not_kill_the_credential(
    auth: KohlerAuth, kohler: FakeKohler
) -> None:
    kohler.token_queue.append(JSON_OUTAGE)
    with pytest.raises(AuthError) as err:
        await auth.async_get_access_token()
    assert not credential_is_dead(err.value)


# --------------------------------------------------------------------------- #
# What the owner sees when the token fails at startup
# --------------------------------------------------------------------------- #
def _reauth_flows(hass: HomeAssistant) -> list[dict]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_a_rejected_refresh_token_at_startup_asks_the_owner_to_sign_in(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.token_queue.append(EXPIRED_GRANT)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(ClientError("connection reset"), id="network-error"),
        pytest.param(TimeoutError(), id="timeout"),
        pytest.param(HTML_OUTAGE, id="html-503"),
        pytest.param(JSON_OUTAGE, id="json-503"),
    ],
)
async def test_kohler_sign_in_being_down_at_startup_retries_without_a_reauth_prompt(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    reply: object,
) -> None:
    kohler.token_queue.append(reply)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert _reauth_flows(hass) == []
