"""The protocol layer with no Home Assistant and no network: sign-in, errors, decoding.

Sign-in is driven through a scripted stand-in for aiohttp, so each test can say exactly what
B2C answered at each of the four steps in `docs/protocol/platform.md` §1 and check what was
sent back. The rest are the pure pieces every request and entity leans on: how a failure is
classified, how a stored preset is echoed, and how an outlet split becomes a valve model.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from custom_components.kohler_konnect.konnect import auth as auth_module
from custom_components.kohler_konnect.konnect.auth import (
    AuthError,
    AuthUnavailable,
    InvalidCredentials,
    KohlerAuth,
    SignInBlocked,
    TokenSet,
    _pkce_pair,
    _raise_for_b2c_error,
    credential_is_dead,
    decode_tenant_id,
)
from custom_components.kohler_konnect.konnect.client import (
    Customer,
    Device,
    DeviceOffline,
    DeviceRunning,
    KohlerClient,
    KohlerError,
    UnexpectedResponse,
    _redact_payload,
    _retry_after,
    status_code,
)
from custom_components.kohler_konnect.konnect.const import (
    B2C_AUTHORIZE_URL,
    B2C_CONFIRMED_URL,
    B2C_REDIRECT_URI,
    B2C_SCOPE,
    B2C_SELF_ASSERTED_URL,
    B2C_SIGNIN_POLICY,
    B2C_TOKEN_URL,
    CLIENT_ID,
    TOKEN_EXPIRY_MARGIN_SECONDS,
)
from custom_components.kohler_konnect.konnect.gcs import plan_preset_timer
from custom_components.kohler_konnect.konnect.hub import (
    HubCapabilities,
    HubDevice,
    HubSettings,
    outlet_flags,
    zone_number,
    zone_outlet_flags,
)
from custom_components.kohler_konnect.konnect.models import (
    VALVE_MODELS,
    OutletStateSource,
    get_valve_model,
    model_for_topology,
    resolve_outlet_source,
)
from custom_components.kohler_konnect.konnect.topology import (
    describe,
    topology_from_hub_configuration,
    topology_from_valve_settings,
)

TENANT = "0f0f0f0f-1111-2222-3333-444444444444"
USERNAME = "owner@example.com"
PASSWORD = "correct horse battery staple"
CSRF = "csrf-token-value"
TRANS_ID = "StateProperties=eyJUSUQiOiJ0eCJ9"
REFERER = f"{B2C_AUTHORIZE_URL}?client_id={CLIENT_ID}&tx=1"


def _jwt(claims: dict[str, Any]) -> str:
    def b64(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(claims)}.signature"


# --------------------------------------------------------------------------- #
# A scripted stand-in for aiohttp, for the sign-in flow
# --------------------------------------------------------------------------- #
class _Reply:
    """One HTTP answer: what `resp.text()`, `resp.json()`, `.status` and `.headers` give."""

    def __init__(
        self,
        body: Any = "",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        url: str = REFERER,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.url = url
        self._text = body if isinstance(body, str) else json.dumps(body)

    async def text(self) -> str:
        return self._text

    async def json(self, content_type: str | None = None) -> Any:
        return json.loads(self._text)


class _Pending:
    """What `session.get(...)` returns: the request happens on `async with`."""

    def __init__(self, item: Any) -> None:
        self._item = item

    async def __aenter__(self) -> _Reply:
        if isinstance(self._item, BaseException):
            raise self._item
        return self._item

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Wire:
    """The replies each URL gives, in order, and every request that was made."""

    def __init__(self, **overrides: Any) -> None:
        self.replies: dict[str, list[Any]] = {
            B2C_AUTHORIZE_URL: [
                _Reply(
                    "<html><script>var SETTINGS = "
                    f'{{"csrf":"{CSRF}","transId":"{TRANS_ID}","hosts":{{}}}};'
                    "</script></html>"
                )
            ],
            B2C_SELF_ASSERTED_URL: [_Reply({"status": "200"})],
            B2C_CONFIRMED_URL: [
                _Reply(
                    status=302,
                    headers={"Location": f"{B2C_REDIRECT_URI}/?state=s&code=auth-code"},
                )
            ],
            B2C_TOKEN_URL: [
                _Reply(
                    {
                        "access_token": _jwt({"oid": TENANT}),
                        "refresh_token": "refresh-1",
                        "expires_in": 3600,
                    }
                )
            ],
        }
        for url, reply in overrides.items():
            self.replies[url] = [reply]
        self.calls: list[SimpleNamespace] = []

    def request(self, session: str, method: str, url: str, **kwargs: Any) -> _Pending:
        self.calls.append(
            SimpleNamespace(session=session, method=method, url=url, **kwargs)
        )
        return _Pending(self.replies[url].pop(0))

    def call(self, url: str) -> SimpleNamespace:
        return next(c for c in self.calls if c.url == url)


class _Session:
    def __init__(self, name: str, wire: _Wire, **kwargs: Any) -> None:
        self.name = name
        self.wire = wire
        self.kwargs = kwargs
        self.closed = False

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.closed = True
        return False

    def get(self, url: str, **kwargs: Any) -> _Pending:
        return self.wire.request(self.name, "GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> _Pending:
        return self.wire.request(self.name, "POST", url, **kwargs)


@pytest.fixture
def b2c(monkeypatch: pytest.MonkeyPatch):
    """Run a sign-in against a scripted B2C; returns `(auth, wire, flow sessions, jars)`."""
    flows: list[_Session] = []
    jars: list[dict[str, Any]] = []

    def run(wire: _Wire) -> tuple[KohlerAuth, list[_Session], list[dict[str, Any]]]:
        def make_flow_session(**kwargs: Any) -> _Session:
            session = _Session("flow", wire, **kwargs)
            flows.append(session)
            return session

        def make_jar(**kwargs: Any) -> dict[str, Any]:
            jar = {"jar": True, **kwargs}
            jars.append(jar)
            return jar

        monkeypatch.setattr(auth_module.aiohttp, "ClientSession", make_flow_session)
        monkeypatch.setattr(auth_module.aiohttp, "CookieJar", make_jar)
        return KohlerAuth(_Session("caller", wire)), flows, jars

    return run


def _sign_in(auth: KohlerAuth) -> TokenSet:
    return asyncio.run(auth.async_sign_in(USERNAME, PASSWORD))


# --------------------------------------------------------------------------- #
# Sign-in: the four B2C steps
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", ["200", 200])
def test_sign_in_drives_the_four_b2c_steps_in_order_with_the_values_each_needs(
    b2c, status
):
    """Each step feeds the next: csrf and transId from the page, the code from the 302."""
    wire = _Wire(**{B2C_SELF_ASSERTED_URL: _Reply({"status": status})})
    auth, _, _ = b2c(wire)

    _sign_in(auth)

    assert [(c.session, c.method, c.url) for c in wire.calls] == [
        ("flow", "GET", B2C_AUTHORIZE_URL),
        ("flow", "POST", B2C_SELF_ASSERTED_URL),
        ("flow", "GET", B2C_CONFIRMED_URL),
        ("caller", "POST", B2C_TOKEN_URL),
    ]
    authorize = wire.call(B2C_AUTHORIZE_URL)
    assert {
        k: authorize.params[k]
        for k in (
            "client_id",
            "response_type",
            "redirect_uri",
            "scope",
            "code_challenge_method",
            "response_mode",
            "prompt",
        )
    } == {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": B2C_REDIRECT_URI,
        "scope": B2C_SCOPE,
        "code_challenge_method": "S256",
        "response_mode": "query",
        "prompt": "login",
    }
    assert authorize.params["state"] and authorize.params["nonce"]

    credentials = wire.call(B2C_SELF_ASSERTED_URL)
    assert credentials.params == {"tx": TRANS_ID, "p": B2C_SIGNIN_POLICY}
    assert credentials.data == {
        "request_type": "RESPONSE",
        "signInName": USERNAME,
        "password": PASSWORD,
    }
    assert credentials.headers == {
        "X-CSRF-TOKEN": CSRF,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": REFERER,
    }

    confirmed = wire.call(B2C_CONFIRMED_URL)
    # The redirect points at the app's custom scheme; following it cannot work.
    assert confirmed.allow_redirects is False
    assert confirmed.params == {
        "csrf_token": CSRF,
        "tx": TRANS_ID,
        "p": B2C_SIGNIN_POLICY,
    }


def test_the_code_is_exchanged_with_the_verifier_whose_challenge_opened_the_flow(b2c):
    """PKCE: B2C refuses the exchange unless sha256(verifier) is the challenge it saw."""
    wire = _Wire()
    auth, _, _ = b2c(wire)

    tokens = _sign_in(auth)

    exchange = wire.call(B2C_TOKEN_URL).data
    verifier = exchange.pop("code_verifier")
    assert exchange == {
        "client_id": CLIENT_ID,
        "grant_type": "authorization_code",
        "code": "auth-code",
        "redirect_uri": B2C_REDIRECT_URI,
        "scope": B2C_SCOPE,
    }
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert wire.call(B2C_AUTHORIZE_URL).params["code_challenge"] == challenge
    assert tokens.refresh_token == "refresh-1"
    assert tokens.tenant_id == TENANT


def test_a_completed_sign_in_holds_and_persists_the_new_refresh_token(b2c):
    """The first refresh token is a rotation like any other: it must reach storage."""
    auth, _, _ = b2c(_Wire())
    persisted: list[str] = []
    auth.on_token_rotated = persisted.append
    assert not auth.has_credentials
    assert auth.tenant_id is None and auth.access_token_expires_at is None

    _sign_in(auth)

    assert persisted == ["refresh-1"]
    assert auth.refresh_token == "refresh-1"
    assert auth.has_credentials
    assert auth.tenant_id == TENANT
    assert auth.access_token_expires_at == pytest.approx(time.time() + 3600, abs=5)


def test_the_sign_in_transaction_runs_on_its_own_session_with_an_unquoted_cookie_jar(
    b2c,
):
    """aiohttp's default jar re-quotes B2C's `x-ms-cpim-*` cookies and the POST gets a bare 400.

    The transaction cookies also stay out of the shared session: only the cookie-free
    token exchange runs there, after the flow session is closed.
    """
    wire = _Wire()
    auth, flows, jars = b2c(wire)

    _sign_in(auth)

    assert jars == [{"jar": True, "quote_cookie": False}]
    assert len(flows) == 1
    assert flows[0].kwargs == {"cookie_jar": jars[0]}
    assert flows[0].closed
    assert {c.session for c in wire.calls if c.url != B2C_TOKEN_URL} == {"flow"}


def test_a_rejected_password_is_invalid_credentials_and_never_echoes_the_password(b2c):
    """`AADB2C90053` is B2C's "wrong email or password"; the form shows `invalid_auth`."""
    wire = _Wire(
        **{
            B2C_SELF_ASSERTED_URL: _Reply(
                {
                    "status": "400",
                    "errorCode": "AADB2C90053",
                    "message": "Your password is incorrect.",
                }
            )
        }
    )
    auth, _, _ = b2c(wire)

    with pytest.raises(InvalidCredentials) as err:
        _sign_in(auth)
    assert PASSWORD not in str(err.value)
    assert USERNAME not in str(err.value)
    # Nothing past the rejected step was attempted.
    assert [c.url for c in wire.calls] == [B2C_AUTHORIZE_URL, B2C_SELF_ASSERTED_URL]


@pytest.mark.parametrize(
    ("reply", "expected", "match"),
    [
        # A rejection with no code: B2C's own message is the best explanation there is.
        (
            _Reply({"status": "400", "message": "Account locked."}),
            InvalidCredentials,
            "Account locked",
        ),
        # Any other B2C code means a step this client cannot perform (MFA, consent...).
        (
            _Reply({"status": "400", "errorCode": "AADB2C90157"}),
            SignInBlocked,
            "AADB2C90157",
        ),
        # A non-JSON page that still names the bad-credentials code.
        (_Reply("<html>AADB2C90053</html>"), InvalidCredentials, "rejected"),
        # A non-JSON page naming nothing: the flow changed, not the password.
        (_Reply("<html>Oops</html>"), AuthError, "Unexpected response"),
    ],
)
def test_the_credential_step_classifies_what_b2c_says(b2c, reply, expected, match):
    auth, _, _ = b2c(_Wire(**{B2C_SELF_ASSERTED_URL: reply}))
    with pytest.raises(AuthError, match=match) as err:
        _sign_in(auth)
    assert type(err.value) is expected


def test_an_unregistered_redirect_uri_is_an_integration_fault_not_a_bad_password(b2c):
    """`AADB2C90006`: Kohler changed the app registration; re-typing the password cannot help."""
    auth, _, _ = b2c(
        _Wire(**{B2C_AUTHORIZE_URL: _Reply("<html>AADB2C90006: redirect</html>")})
    )
    with pytest.raises(AuthError, match="redirect URI") as err:
        _sign_in(auth)
    assert type(err.value) is AuthError


@pytest.mark.parametrize(
    ("page", "match"),
    [
        ("<html>no settings blob here</html>", "did not look as expected"),
        ('<script>var SETTINGS = {"transId":"t"};</script>', "sign-in parameters"),
        ("<script>var SETTINGS = {not json};</script>", "sign-in parameters"),
    ],
)
def test_a_changed_sign_in_page_is_reported_rather_than_guessed_at(b2c, page, match):
    auth, _, _ = b2c(_Wire(**{B2C_AUTHORIZE_URL: _Reply(page)}))
    with pytest.raises(AuthError, match=match):
        _sign_in(auth)


@pytest.mark.parametrize(
    ("reply", "expected", "match"),
    [
        # B2C put an error on the redirect instead of a code.
        (
            _Reply(
                status=302,
                headers={
                    "Location": f"{B2C_REDIRECT_URI}/?error=access_denied"
                    "&error_description=AADB2C90118%3A+forgot+password"
                },
            ),
            SignInBlocked,
            "AADB2C90118",
        ),
        (
            _Reply(
                status=302,
                headers={"Location": f"{B2C_REDIRECT_URI}/?error=server_error"},
            ),
            SignInBlocked,
            "server_error",
        ),
        (
            _Reply(
                status=302,
                headers={
                    "Location": f"{B2C_REDIRECT_URI}/?error=access_denied"
                    "&error_description=AADB2C90053"
                },
            ),
            InvalidCredentials,
            "rejected",
        ),
        # No redirect at all; the page names a code, or nothing.
        (_Reply("AADB2C90157", status=200), SignInBlocked, "AADB2C90157"),
        (_Reply("<html/>", status=200), AuthError, "did not return a sign-in redirect"),
        # A redirect with neither error nor code.
        (
            _Reply(status=302, headers={"Location": f"{B2C_REDIRECT_URI}/?state=s"}),
            AuthError,
            "no authorization code",
        ),
    ],
)
def test_the_redirect_step_reads_the_code_or_the_reason_there_is_none(
    b2c, reply, expected, match
):
    wire = _Wire(**{B2C_CONFIRMED_URL: reply})
    auth, _, _ = b2c(wire)
    with pytest.raises(AuthError, match=match) as err:
        _sign_in(auth)
    assert type(err.value) is expected
    assert B2C_TOKEN_URL not in [c.url for c in wire.calls]


@pytest.mark.parametrize(
    "step", [B2C_AUTHORIZE_URL, B2C_SELF_ASSERTED_URL, B2C_CONFIRMED_URL]
)
@pytest.mark.parametrize(
    "failure",
    [aiohttp.ClientConnectionError("reset"), TimeoutError()],
    ids=["reset", "timeout"],
)
def test_a_network_failure_during_sign_in_is_never_mistaken_for_a_bad_password(
    b2c, step, failure
):
    """The setup form maps it to "cannot connect", not `invalid_auth`.

    aiohttp's total timeout raises a bare `TimeoutError`, not a `ClientError`.
    """
    auth, _, _ = b2c(_Wire(**{step: failure}))
    with pytest.raises(AuthUnavailable) as err:
        _sign_in(auth)
    assert not isinstance(err.value, InvalidCredentials)
    assert not credential_is_dead(err.value)


def test_sign_in_logs_nothing_that_identifies_the_account(b2c, caplog):
    """This module handles the password; nothing it does may write it, or a token, to a log."""
    caplog.set_level(logging.DEBUG)
    auth, _, _ = b2c(_Wire())
    tokens = _sign_in(auth)
    for secret in (PASSWORD, USERNAME, tokens.access_token, tokens.refresh_token, CSRF):
        assert secret not in caplog.text


# --------------------------------------------------------------------------- #
# Classifying auth failures
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("err", "dead"),
    [
        (InvalidCredentials("x"), True),
        (SignInBlocked("x"), True),
        (AuthError("x"), True),
        # Kohler unreachable: prompting for reauth on a WAN blip would be wrong.
        (AuthUnavailable("x"), False),
        # An API failure is not an auth failure at all.
        (KohlerError("x", status=401), False),
        (DeviceOffline("x"), False),
        (ValueError("x"), False),
    ],
)
def test_only_a_credential_kohler_actually_rejected_counts_as_dead(err, dead):
    assert credential_is_dead(err) is dead


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Error AADB2C90053: bad password", InvalidCredentials),
        ("AADB2C90006", AuthError),
        ("Something AADB2C99999 happened", SignInBlocked),
    ],
)
def test_a_b2c_code_anywhere_in_a_reply_decides_the_exception(text, expected):
    with pytest.raises(AuthError) as err:
        _raise_for_b2c_error(text)
    assert type(err.value) is expected


@pytest.mark.parametrize("text", ["", None, "HTTP 500", "AADB2C"])
def test_a_reply_with_no_b2c_code_raises_nothing_by_itself(text):
    assert _raise_for_b2c_error(text) is None


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("token", "tenant"),
    [
        (_jwt({"oid": TENANT, "sub": "other"}), TENANT),
        (_jwt({"sub": TENANT}), TENANT),
        (_jwt({"name": "Owner"}), None),
        (None, None),
        ("", None),
        ("not-a-jwt", None),
        ("a.!!!not-base64!!!.c", None),
        ("a." + base64.urlsafe_b64encode(b"not json").decode() + ".c", None),
    ],
)
def test_the_tenant_id_is_the_tokens_oid_claim_or_nothing(token, tenant):
    """Every device call is keyed on it; an unreadable token must not raise."""
    assert decode_tenant_id(token) == tenant


def test_pkce_challenge_is_the_unpadded_sha256_of_an_rfc_length_verifier():
    verifier, challenge = _pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert "=" not in verifier and "=" not in challenge
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert _pkce_pair()[0] != verifier


def test_an_access_token_counts_as_expired_inside_the_safety_margin():
    """Refreshing early means a request never sets off with a token that lapses mid-flight."""
    now = time.time()
    margin = TOKEN_EXPIRY_MARGIN_SECONDS
    assert TokenSet("a", "r", now + margin + 30).expired is False
    assert TokenSet("a", "r", now + margin - 30).expired is True
    assert TokenSet("a", "r", now - 1).expired is True


# --------------------------------------------------------------------------- #
# Client: errors, throttling, redaction
# --------------------------------------------------------------------------- #
def test_retry_after_reads_seconds_or_an_http_date_and_only_when_throttled():
    later = format_datetime(datetime.now(UTC) + timedelta(seconds=120), usegmt=True)
    earlier = format_datetime(datetime.now(UTC) - timedelta(hours=1), usegmt=True)
    assert _retry_after(429, "30") == 30.0
    assert _retry_after(503, "2.5") == 2.5
    assert _retry_after(503, later) == pytest.approx(120, abs=3)
    # A date already past, or a negative count, means "now", never a negative wait.
    assert _retry_after(429, earlier) == 0.0
    assert _retry_after(429, "-5") == 0.0
    assert _retry_after(429, "soon") is None
    assert _retry_after(429, None) is None
    # The header means nothing on any other status.
    assert _retry_after(500, "30") is None
    assert _retry_after(200, "30") is None


@pytest.mark.parametrize(
    ("status", "rejected"),
    [
        (400, True),
        (403, True),
        (404, True),
        # A stale token, a timeout and throttling all fix themselves.
        (401, False),
        (408, False),
        (429, False),
        (500, False),
        (503, False),
        (None, False),
    ],
)
def test_only_a_4xx_refusal_counts_as_rejected(status, rejected):
    """A run of rejections raises a repair issue; outages must never do that."""
    assert KohlerError("x", status=status).rejected is rejected


def test_an_unreadable_reply_is_always_a_rejection_and_offline_never_is():
    assert UnexpectedResponse("x").rejected is True
    assert DeviceOffline("x", {"statusCode": "900"}).rejected is False


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"statusCode": 900}, "900"),
        ({"statusCode": " 902 "}, "902"),
        ({"statusCode": ""}, None),
        ({"statusCode": True}, None),
        ({"statusCode": None}, None),
        ({}, None),
        ("statusCode 900", None),
        (None, None),
    ],
)
def test_the_in_body_status_code_is_read_as_text(payload, code):
    assert status_code(payload) == code
    assert KohlerError("x", payload).code == code


def test_a_running_device_is_refused_even_when_the_http_status_says_ok():
    """901/902 arrive in the body; HTTP 200 alone does not mean the edit happened."""
    with pytest.raises(DeviceRunning) as err:
        KohlerClient._raise_for_payload(200, "/x", {"statusCode": "902"})
    assert isinstance(err.value, KohlerError)
    assert err.value.code == "902"


def test_a_harmless_reply_raises_nothing():
    KohlerClient._raise_for_payload(200, "/x", {"statusCode": "200"})
    KohlerClient._raise_for_payload(201, "/x", {"correlationId": "c"})
    KohlerClient._raise_for_payload(204, "/x", None)


def test_an_http_failure_carries_its_status_and_an_id_free_path():
    path = "/devices/api/v1/device-management/gcs-state/gcs-secret0001"
    with pytest.raises(KohlerError) as err:
        KohlerClient._raise_for_payload(
            404,
            path,
            {"message": f"no device for {TENANT}"},
            tenant_id=TENANT,
        )
    assert err.value.status == 404
    assert err.value.rejected
    assert "gcs-state/<id> failed with HTTP 404" in str(err.value)
    assert "gcs-secret0001" not in str(err.value)
    assert TENANT not in str(err.value)


def test_redaction_keeps_every_key_and_hides_only_secret_values():
    """The probe log exists to show which fields Kohler sends, so structure survives."""
    payload = {
        "ioTHubSettings": {
            "ioTHub": "hub.example.net",
            "deviceId": "mobile-1",
            "password": "SharedAccessSignature sr=hub&sig=abc",
            "sasToken": "t0k3n",
            # A whole secret under a key that names none.
            "connectionString": "HostName=hub;DeviceId=d;SharedAccessKey=s3cr3t",
        },
        "devices": [{"deviceId": "gcs-1", "refresh_token": "r"}],
        "Authorization": "Bearer x",
        "count": 3,
    }
    redacted = _redact_payload(payload)
    settings = redacted["ioTHubSettings"]
    assert set(settings) == set(payload["ioTHubSettings"])
    assert settings["ioTHub"] == "hub.example.net"
    assert settings["deviceId"] == "mobile-1"
    for key in ("password", "sasToken", "connectionString"):
        assert settings[key] == "**REDACTED**", key
    assert redacted["devices"] == [
        {"deviceId": "gcs-1", "refresh_token": "**REDACTED**"}
    ]
    assert redacted["Authorization"] == "**REDACTED**"
    assert redacted["count"] == 3
    # A copy: the payload callers go on to use is untouched.
    assert payload["ioTHubSettings"]["sasToken"] == "t0k3n"


def test_redaction_stops_at_a_depth_limit_instead_of_recursing_forever():
    deep: dict[str, Any] = {"leaf": "value"}
    for _ in range(30):
        deep = {"next": deep}
    redacted = _redact_payload(deep)
    for _ in range(13):
        redacted = redacted["next"]
    assert redacted == "<too deep>"


# --------------------------------------------------------------------------- #
# Client: the account record
# --------------------------------------------------------------------------- #
def test_the_account_record_finds_devices_under_either_home_key_and_skips_junk():
    customer = Customer(
        {
            "homes": [
                "not a home",
                {
                    "devices": [
                        {"deviceid": "gcs-1", "sku": "GCS"},
                        {"deviceId": "hub-1", "sku": "HUB", "name": "Plus"},
                        {"deviceId": "set-1", "sku": "set", "logicalName": "Bar"},
                        "not a device",
                    ]
                },
                {"devices": None},
            ]
        }
    )
    assert [d.device_id for d in customer.devices] == ["gcs-1", "hub-1", "set-1"]
    # The name falls back from logicalName to name to the id.
    assert [d.name for d in customer.devices] == ["gcs-1", "Plus", "Bar"]
    # Faucet SKUs compare case-insensitively.
    assert customer.has_faucet and customer.faucet_devices[0].device_id == "set-1"
    assert customer.device("HUB").device_id == "hub-1"
    assert customer.device("DTV") is None
    # Units default to the app's when the account omits them.
    assert customer.temperature_unit == "Fahrenheit"
    assert customer.water_units == "Standard"
    assert customer.describe() == (
        "1 Anthem valve(s), 1 Anthem Plus controller(s) and 1 faucet(s)"
    )


def test_an_account_with_nothing_supported_says_so():
    customer = Customer(
        {"customerHome": [{"devices": [{"deviceId": "dtv-1", "sku": "DTV"}]}]}
    )
    assert customer.supported_devices == []
    assert customer.describe() == "no supported Kohler devices found on this account"
    assert "DTV" in repr(customer.devices[0])
    assert Device({}).device_id == "" and Device({}).sku == ""


# --------------------------------------------------------------------------- #
# Presets: deciding what a timer sync writes back
# --------------------------------------------------------------------------- #
def _presets(*records: Any) -> dict[str, Any]:
    return {"gcsPresetExperienceDetails": list(records)}


def test_a_preset_timer_rewrite_echoes_name_volume_and_valid_words_exactly():
    """`writepreset` replaces the record, so everything but `time` must go back as read."""
    payload = _presets(
        "junk",
        {"presetId": "not-a-number"},
        {
            "presetId": "2",
            "logicalName": "ignored",
            "name": "Morning",
            "time": "1800",
            "volume": "12",
            "valveDetails": [
                {"valveIndex": "Valve1", "hexString": "1190C8"},
                {"valveIndex": "Valve2", "hexString": "000000"},  # unused
                {"valveIndex": "Valve3", "hexString": ""},  # unused
                {"valveIndex": "Zone1", "hexString": "1190C8"},  # not a valve
                {"valveIndex": "Valvex", "hexString": "1190C8"},  # no number
                "junk",
            ],
        },
    )
    plan = plan_preset_timer(payload, 2, 900)
    assert plan.needed
    assert (plan.reason, plan.name, plan.volume, plan.previous) == (
        "rewrite",
        "Morning",
        "12",
        1800,
    )
    assert plan.valves == {1: "1190c8"}


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (None, "absent"),
        ({"gcsPresetExperienceDetails": "nope"}, "absent"),
        (_presets({"presetId": 3, "title": "Other"}), "absent"),
        # A blank slot is left blank rather than half-created.
        (_presets({"presetId": 2, "title": " ", "valveDetails": []}), "empty"),
        (_presets({"presetId": 2, "title": "Morning", "time": 900}), "already"),
    ],
)
def test_a_preset_timer_is_left_alone_unless_a_real_preset_differs(payload, reason):
    plan = plan_preset_timer(payload, 2, 900)
    assert plan.reason == reason
    assert not plan.needed


def test_a_preset_with_an_unreadable_time_is_rewritten_with_volume_defaulted():
    plan = plan_preset_timer(
        _presets({"presetId": 2, "title": "Morning", "time": "soon"}), 2, 900
    )
    assert (plan.reason, plan.previous, plan.volume) == ("rewrite", None, "0")


# --------------------------------------------------------------------------- #
# Models and topology: from a detected split to a valve model
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("split", "sku"),
    [
        ((2, 0), "K-28209"),
        ((3, 0), "K-28210"),
        ((2, 2), "K-28211"),
        ((3, 3), "K-28212"),
    ],
)
def test_a_catalogue_split_resolves_to_its_named_model(split, sku):
    assert model_for_topology(*split) is VALVE_MODELS[sku]


@pytest.mark.parametrize(
    ("split", "name", "outlet_ids"),
    [
        ((3, 2), "Detected valve (3+2 outlets)", [0, 1, 2, 3, 4]),
        ((1, 1), "Detected valve (1+1 outlets)", [0, 3]),
        ((1, 0), "Detected valve (1 outlets)", [0]),
    ],
)
def test_an_uncatalogued_split_still_produces_a_usable_model(split, name, outlet_ids):
    """Real installs need not match Kohler's four models; refusing would strand them."""
    model = model_for_topology(*split)
    assert model.sku == "detected"
    assert model.name == name
    assert model.total_outlets == sum(split)
    assert model.zones == ([1, 2] if split[1] else [1])
    ids = [
        model.outlet_id(zone, outlet)
        for zone in model.zones
        for outlet in range(1, model.outlets_in_zone(zone) + 1)
    ]
    assert ids == outlet_ids
    # Global numbering round-trips through the hardware's ids.
    assert [model.outlet_from_id(i) for i in ids] == list(
        range(1, model.total_outlets + 1)
    )


def test_a_four_outlet_valve_maps_its_hardware_ids_and_refuses_the_gaps():
    """K-28211 reports 0, 1, 3, 4: zone 2 keeps ids 3 and 4 (gcs_valve.md §1.1)."""
    model = get_valve_model("K-28211")
    assert [model.outlet_from_id(i) for i in range(-1, 7)] == [
        None,
        1,
        2,
        None,
        3,
        4,
        None,
        None,
    ]
    assert model.outlet_location(3) == (2, 0)
    assert model.split_outlets([True, False, False, True]) == (
        [True, False],
        [False, True],
    )


def test_out_of_range_outlets_and_zones_are_errors_not_guesses():
    model = get_valve_model("K-28210")
    # A single-zone valve's zone 2 exists in the protocol and simply has no outlets.
    assert model.outlets_in_zone(2) == 0
    with pytest.raises(ValueError, match=r"zones \[1\]; got 3"):
        model.outlets_in_zone(3)
    with pytest.raises(ValueError, match="outlets 1-3"):
        model.outlet_location(4)
    with pytest.raises(ValueError, match="zone 1 has outlets 1-3"):
        model.outlet_id(1, 4)
    with pytest.raises(ValueError, match="3 outlets, got 2"):
        model.split_outlets([True, False])


def test_valve_models_are_looked_up_forgivingly_and_unknown_ones_named():
    assert get_valve_model(" k-28211 ") is VALVE_MODELS["K-28211"]
    with pytest.raises(ValueError, match="K-28209"):
        get_valve_model("K-99999")


def test_the_valve_word_is_the_outlet_source_whenever_a_valve_exists():
    """The HUB trails and skips valve-driven sessions; only a HUB-only account uses it."""
    assert resolve_outlet_source(True, True) is OutletStateSource.GCS_VALVE_HEX
    assert resolve_outlet_source(True, False) is OutletStateSource.GCS_VALVE_HEX
    assert resolve_outlet_source(False, True) is OutletStateSource.HUB_MQTT
    assert resolve_outlet_source(False, False) is None


def test_the_valves_own_settings_give_the_split_whatever_their_spelling():
    setting = {
        "outlets": None,  # null on the tested system; deliberately ignored
        "valveSettings": [
            "junk",
            {"valve": "Valve1", "noOfOutlets": " 2 "},
            {"valve": "valve2", "noOfOutlets": 2},
            {"valve": "Valve3", "noOfOutlets": 3},  # slots 3-8 are not zones
            {"valve": "gateway", "noOfOutlets": 9},
        ],
    }
    assert topology_from_valve_settings(setting) == (2, 2)


@pytest.mark.parametrize(
    "setting",
    [
        None,
        {},
        {"valveSettings": "nope"},
        {"valveSettings": [{"valve": "Valve1", "noOfOutlets": 0}]},
        {"valveSettings": [{"valve": "Valve1", "noOfOutlets": "n/a"}]},
        {"valveSettings": [{"valve": "Valve2", "noOfOutlets": 3}]},
    ],
)
def test_valve_settings_without_a_populated_first_valve_detect_nothing(setting):
    """Detection failing must fall back to asking, never to a zero-outlet model."""
    assert topology_from_valve_settings(setting) is None


def test_a_controller_reports_the_split_through_configured_outlets_not_parts():
    """`parts.valve2: NotConnected` is normal on a single-body 3+3 K-28212."""
    configuration = {
        "parts": {"valve1": "Connected", "valve2": "NotConnected"},
        "zoneone": {"configuredoutlets": "3"},
        "zonetwo": {"configuredoutlets": 3},
    }
    assert topology_from_hub_configuration(configuration) == (3, 3)
    assert topology_from_hub_configuration({"zoneone": {"configuredoutlets": 2}}) == (
        2,
        0,
    )
    assert topology_from_hub_configuration({"zoneone": None}) is None
    assert topology_from_hub_configuration(None) is None


@pytest.mark.parametrize(
    ("split", "text"),
    [
        ((1, 0), "1 zone, 1 outlet"),
        ((3, 0), "1 zone, 3 outlets"),
        ((2, 2), "2 zones, 2 + 2 outlets"),
    ],
)
def test_a_detected_split_is_described_in_words(split, text):
    assert describe(split) == text


# --------------------------------------------------------------------------- #
# Anthem Plus: decoding its payloads and building its bodies
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("attribute", "zone"),
    [
        ({"zone": "1"}, 1),
        ({"zone": 2}, 2),
        ({"component": "valve2"}, 2),
        ({"valveIndex": "Valve1"}, 1),
        ({"zoneIndex": "zone 2"}, 2),
        ({"zone": None, "component": "Valve1"}, 1),
        ({"zone": "3"}, None),
        ({"component": "amplifier"}, None),
        ({}, None),
    ],
)
def test_every_spelling_of_a_zone_resolves_to_the_same_number(attribute, zone):
    assert zone_number(attribute) == zone


def test_padded_outlet_arrays_become_flags_for_the_outlets_that_exist():
    """Every zone array has six slots; trailing ones are padding, never extra outlets."""
    assert outlet_flags([1, 0, 1, 0, 0, 0]) == [True, False, True]
    assert outlet_flags([1], 3) == [True, False, False]
    assert outlet_flags(None, 2) == [False, False]
    k28211 = get_valve_model("K-28211")
    assert zone_outlet_flags(k28211, [0, 1, 0, 0, 0, 0], [1, 0, 0, 0, 0, 0]) == [
        False,
        True,
        True,
        False,
    ]
    # A single-zone valve ignores whatever zone 2 says.
    k28210 = get_valve_model("K-28210")
    assert zone_outlet_flags(k28210, [1, 0, 0], [1, 1, 1]) == [True, False, False]


def test_capabilities_come_from_parts_and_unread_is_distinct_from_empty():
    caps = HubCapabilities.from_configuration(
        {
            "parts": {
                "valve1": "Connected",
                "valve2": "NotConnected",
                "amplifier": "NotConnected",
                "lightBridge": "Connected",
                "steam": None,
            }
        }
    )
    assert (caps.water, caps.music, caps.light, caps.steam, caps.known) == (
        True,
        False,
        True,
        False,
        True,
    )
    assert caps.describe() == "water, light"
    empty = HubCapabilities.from_configuration(None)
    assert empty.known and empty.describe() == "no accessories detected"
    assert HubCapabilities().known is False


def test_controller_settings_read_units_flow_lights_and_an_empty_sd_card():
    settings = HubSettings.from_configuration(
        {
            "systemSettings": {"temperatureUnit": "Celsius", "flowRateEnable": "1"},
            "parts": {"amplifier": "connected", "light": "Connected"},
            "amplifierSettings": {
                "monoVolume": 30,
                "sdCard": "present",
                "music": "unknown",
            },
            "lightSettings": [{"name": "groupA"}, {"name": ""}, "junk"],
            "about": {"hub": "junk"},
        }
    )
    assert settings.temperature_unit == "Celsius"
    assert settings.flow_rate_enabled is True
    assert settings.light_groups == ("groupA",)
    # `connected` in any case is connected (the app's comparison); only the card is missing.
    assert settings.disconnected == ("sd_card_empty",)
    assert settings.lan_ip is None and settings.web_url is None
    assert HubSettings.from_configuration("junk") == HubSettings()


def test_global_outlet_flags_split_into_zero_based_positions_per_zone():
    """Favorites write positions, not flags — and a zone the valve lacks is left out."""
    zones = HubDevice.zones_for(
        get_valve_model("K-28211"), [True, False, False, True], 104
    )
    assert zones == {
        "zone1": {"temperature": 104, "flowrate": 100, "outlets": [0]},
        "zone2": {"temperature": 104, "flowrate": 100, "outlets": [1]},
    }
    zones = HubDevice.zones_for(
        get_valve_model("K-28210"), [False, True, True], 100, 60
    )
    assert zones == {
        "zone1": {"temperature": 100, "flowrate": 60, "outlets": [1, 2]},
        "zone2": None,
    }
    assert HubDevice.music("SdCard", 40) == {
        "source": "SdCard",
        "songID": "",
        "musicRepeat": "",
        "volume": 40,
    }


def test_full_cold_stays_zero_on_the_wire_for_a_celsius_account():
    """0 is the COLD stop; converting it would send 32 °F, a real (if chilly) setpoint."""
    hub = HubDevice(SimpleNamespace(tenant_id=TENANT), "hub-1", "Celsius")
    assert hub.to_wire_temperature(0) == 0
    assert hub.to_wire_temperature(43) == 109
    assert HubDevice(None, "hub-1", "Fahrenheit").to_wire_temperature(101.6) == 102
