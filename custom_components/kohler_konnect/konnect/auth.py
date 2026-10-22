"""Server-side Azure AD B2C sign-in for Kohler Konnect.

Kohler's B2C app registration accepts only a custom-scheme redirect URI
(``msauth.com.kohler.hermoth://auth``), so no browser round trip can return the code.
This module drives the B2C custom policy in-process instead, as the sign-in page's own
JavaScript does:

1. ``GET /authorize`` with PKCE: session cookies, plus ``csrf`` and ``transId`` from the
   page's ``SETTINGS`` blob.
2. ``POST /SelfAsserted`` with the email and password: a small JSON status.
3. ``GET /api/CombinedSigninAndSignup/confirmed`` without following redirects: the code
   is read from the ``302 Location`` header.
4. ``POST /token``: the code and PKCE verifier for tokens.

See `docs/protocol/platform.md` §1.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import math
import re
import secrets
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import (
    B2C_AUTHORIZE_URL,
    B2C_CONFIRMED_URL,
    B2C_REDIRECT_URI,
    B2C_SCOPE,
    B2C_SELF_ASSERTED_URL,
    B2C_SIGNIN_POLICY,
    B2C_TOKEN_URL,
    CLIENT_ID,
    ERROR_BAD_CREDENTIALS,
    ERROR_REDIRECT_NOT_REGISTERED,
    TOKEN_EXPIRY_MARGIN_SECONDS,
)

#: ⚠️ Never log a token, password or username from this module.
_LOGGER = logging.getLogger(__name__)


# The sign-in page embeds its configuration as `var SETTINGS = {...};`.
_SETTINGS = re.compile(r"var SETTINGS = (\{.*?\});", re.S)
_B2C_ERROR_CODE = re.compile(r"AADB2C\d+")

#: Failing to reach Kohler at all. aiohttp's total timeout raises a bare `TimeoutError`,
#: which is not a `ClientError`.
_UNREACHABLE = (aiohttp.ClientError, TimeoutError)

#: OAuth error codes meaning "try later" (RFC 6749 §4.1.2.1), not "this grant is dead".
_OUTAGE_ERRORS = frozenset({"temporarily_unavailable", "server_error"})

#: Assumed when a token reply carries no usable ``expires_in``: B2C's default access-token
#: lifetime.
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600.0


class AuthError(Exception):
    """Base class for sign-in and token failures."""


class AuthUnavailable(AuthError):
    """Kohler's auth service could not be reached; the credential is not implicated.

    An outage fixes itself, so it must never start a reauth flow. See
    :func:`credential_is_dead`.
    """


def credential_is_dead(err: Exception) -> bool:
    """True when `err` means the stored credential was rejected, not that Kohler was down.

    ⚠️ This decides whether to prompt for reauth: a rejection must surface as reauth, an
    outage must not. `AuthUnavailable` is the only `AuthError` raised without Kohler
    rejecting anything, and a `KohlerError` is not an auth failure at all.
    """
    return isinstance(err, AuthError) and not isinstance(err, AuthUnavailable)


class InvalidCredentials(AuthError):
    """The email or password was rejected by Kohler."""


class SignInBlocked(AuthError):
    """Sign-in needs something this client cannot do (MFA, lockout, consent)."""


@dataclass
class TokenSet:
    """An access token plus the rotating refresh token that produced it."""

    access_token: str
    refresh_token: str
    expires_at: float
    #: Seconds before `expires_at` to renew. Capped at half the token's life for a
    #: short-lived token, which would otherwise count as expired on arrival and make every
    #: call refresh.
    refresh_margin: float = TOKEN_EXPIRY_MARGIN_SECONDS

    @property
    def expired(self) -> bool:
        """True when the access token is at or near expiry."""
        return time.time() >= self.expires_at - self.refresh_margin

    @property
    def tenant_id(self) -> str | None:
        """The account id Kohler keys every device call on.

        Carried as the ``oid`` claim (falling back to ``sub``) of the access token.
        """
        return decode_tenant_id(self.access_token)


def decode_tenant_id(access_token: str | None) -> str | None:
    """Extract the tenant/customer id from a B2C access token, or None."""
    if not access_token:
        return None
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
        return None
    return claims.get("oid") or claims.get("sub")


def _token_lifetime(expires_in: Any) -> float:
    """Seconds a new access token lives, from the token reply's ``expires_in``.

    B2C sends a number, sometimes as a string. Anything unusable falls back to
    `DEFAULT_TOKEN_LIFETIME_SECONDS` rather than raising: B2C has already rotated the
    refresh token by then, and failing would lose the new one.
    """
    if isinstance(expires_in, bool):
        return DEFAULT_TOKEN_LIFETIME_SECONDS
    try:
        seconds = float(expires_in)
    except (TypeError, ValueError):
        return DEFAULT_TOKEN_LIFETIME_SECONDS
    if not math.isfinite(seconds) or seconds <= 0:
        return DEFAULT_TOKEN_LIFETIME_SECONDS
    return seconds


def _retrieve_result(task: asyncio.Task[Any]) -> None:
    """Mark a refresh's outcome as seen, so an abandoned failure is not logged as lost."""
    if not task.cancelled():
        task.exception()


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) per RFC 7636 S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _b2c_error_code(value: object) -> object:
    """A reply's `errorCode`, given the ``AADB2C`` prefix if it was sent as a bare number.

    Without the prefix `_raise_for_b2c_error` would not recognise it.
    """
    if (isinstance(value, int) and not isinstance(value, bool)) or (
        isinstance(value, str) and value.strip().isdigit()
    ):
        return f"AADB2C{int(value)}"
    return value


def _raise_for_b2c_error(*texts: object) -> None:
    """Raise for the first B2C error code (``AADB2C…``) found in ``texts``, if any.

    Each of ``texts`` may be any value; it is searched as text. Bad credentials raise
    `InvalidCredentials`, an unregistered redirect URI `AuthError`, anything else
    `SignInBlocked`.
    """
    for text in texts:
        if match := _B2C_ERROR_CODE.search(str(text or "")):
            break
    else:
        return
    code = match.group(0)
    if code == ERROR_BAD_CREDENTIALS:
        raise InvalidCredentials(
            "Kohler rejected that email or password. Check them in the Konnect app."
        )
    if code == ERROR_REDIRECT_NOT_REGISTERED:
        raise AuthError(
            f"Kohler no longer accepts this app's redirect URI ({code}). The Konnect "
            "app registration has changed and this integration needs updating."
        )
    raise SignInBlocked(f"Kohler sign-in failed ({code}).")


class KohlerAuth:
    """Signs in to Kohler Konnect and keeps a usable access token.

    Holds the rotating refresh token in memory and hands each new one to
    :attr:`on_token_rotated` for persisting, so a restart does not need the password again.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str | None = None,
        *,
        refresh_token_source: Callable[[], str | None] | None = None,
    ) -> None:
        self._session = session
        self._tokens: TokenSet | None = None
        self._refresh_token = refresh_token
        # Where a newer refresh token than the held one may be stored (the config entry);
        # see `_adopt_newer_refresh_token`. It must answer None once its owner is unloaded,
        # or that owner would redeem the token its successor holds.
        self._refresh_token_source = refresh_token_source
        # Serialises refreshes; see `async_get_access_token`.
        self._refresh_lock = asyncio.Lock()
        # The refresh in flight, run as its own task; see `async_get_access_token`.
        self._refresh_task: asyncio.Task[TokenSet] | None = None
        #: Called with each new refresh token B2C issues, from `_async_token_request`.
        #: ⚠️ It must persist the token at once: the previous one is already dead.
        self.on_token_rotated: Callable[[str], None] | None = None

    @property
    def has_credentials(self) -> bool:
        """Whether a refresh token is held at all.

        False means the password is needed again. True does not mean Kohler still accepts
        the token; only a real request shows that.
        """
        return bool(self.refresh_token)

    @property
    def access_token_expires_at(self) -> float | None:
        """Unix time the current access token lapses, or None if none is held.

        Expiry is routine: the next request that needs a token refreshes it.
        """
        return self._tokens.expires_at if self._tokens else None

    @property
    def refresh_token(self) -> str | None:
        """The current refresh token. B2C rotates this — always persist the latest."""
        return self._tokens.refresh_token if self._tokens else self._refresh_token

    @property
    def tenant_id(self) -> str | None:
        """The signed-in account's tenant id, once a token exists."""
        return self._tokens.tenant_id if self._tokens else None

    async def async_sign_in(self, username: str, password: str) -> TokenSet:
        """Sign in with an email and password, entirely server-side.

        Raises `InvalidCredentials`, `SignInBlocked`, `AuthUnavailable` or `AuthError`.

        Runs on its own session, keeping the B2C transaction cookies out of the shared one.
        ⚠️ Its jar must use ``quote_cookie=False``: aiohttp's default re-quotes Microsoft's
        ``x-ms-cpim-*`` cookies, and B2C then refuses the credential POST with a bare 400.
        """
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(quote_cookie=False)
        ) as flow_session:
            csrf, trans_id, referer = await self._async_begin(
                flow_session, challenge, state
            )
            await self._async_submit_credentials(
                flow_session, csrf, trans_id, referer, username, password
            )
            code = await self._async_collect_code(flow_session, csrf, trans_id)
        # The token exchange carries no cookies, so it uses the caller's session.
        return await self._async_exchange_code(code, verifier)

    async def _async_begin(
        self, session: aiohttp.ClientSession, challenge: str, state: str
    ) -> tuple[str, str, str]:
        """Open the sign-in transaction; return (csrf, transId, referer)."""
        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": B2C_REDIRECT_URI,
            "scope": B2C_SCOPE,
            "state": state,
            "nonce": secrets.token_urlsafe(16),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_mode": "query",
            "prompt": "login",
        }
        try:
            async with session.get(
                B2C_AUTHORIZE_URL, params=params, timeout=aiohttp.ClientTimeout(30)
            ) as resp:
                body = await resp.text()
                referer = str(resp.url)
        except _UNREACHABLE as err:
            raise AuthUnavailable(f"Could not reach Kohler sign-in: {err!r}") from err

        _raise_for_b2c_error(body)
        match = _SETTINGS.search(body)
        if not match:
            raise AuthError(
                "Kohler's sign-in page did not look as expected; the flow may have "
                "changed."
            )
        try:
            settings: dict[str, Any] = json.loads(match.group(1))
            return settings["csrf"], settings["transId"], referer
        except (json.JSONDecodeError, KeyError) as err:
            raise AuthError(
                f"Could not read Kohler's sign-in parameters: {err}"
            ) from err

    async def _async_submit_credentials(
        self,
        session: aiohttp.ClientSession,
        csrf: str,
        trans_id: str,
        referer: str,
        username: str,
        password: str,
    ) -> None:
        """POST the credentials. Raises InvalidCredentials on rejection."""
        try:
            async with session.post(
                B2C_SELF_ASSERTED_URL,
                params={"tx": trans_id, "p": B2C_SIGNIN_POLICY},
                data={
                    "request_type": "RESPONSE",
                    "signInName": username,
                    "password": password,
                },
                headers={
                    "X-CSRF-TOKEN": csrf,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": referer,
                },
                timeout=aiohttp.ClientTimeout(30),
            ) as resp:
                body = await resp.text()
        except _UNREACHABLE as err:
            raise AuthUnavailable(f"Network error during sign-in: {err!r}") from err

        # This endpoint answers 200 with a JSON body whose "status" carries the result.
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as err:
            _raise_for_b2c_error(body)
            # No B2C code found. Chained to keep the decode failure in the traceback.
            raise AuthError("Unexpected response from Kohler during sign-in.") from err
        if not isinstance(payload, dict):
            # JSON but not an object: handled like a body that does not parse.
            _raise_for_b2c_error(body)
            raise AuthError("Unexpected response from Kohler during sign-in.")
        if str(payload.get("status")) != "200":
            _raise_for_b2c_error(_b2c_error_code(payload.get("errorCode")), body)
            raise InvalidCredentials(
                payload.get("message") or "Kohler rejected those credentials."
            )

    async def _async_collect_code(
        self, session: aiohttp.ClientSession, csrf: str, trans_id: str
    ) -> str:
        """Read the authorization code from B2C's redirect, without following it.

        ``Location`` points at the app's custom scheme, which nothing resolves; only its
        query string is read.
        """
        try:
            async with session.get(
                B2C_CONFIRMED_URL,
                params={
                    "csrf_token": csrf,
                    "tx": trans_id,
                    "p": B2C_SIGNIN_POLICY,
                },
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(30),
            ) as resp:
                location = resp.headers.get("Location", "")
                if not location:
                    _raise_for_b2c_error(await resp.text())
                    raise AuthError("Kohler did not return a sign-in redirect.")
        except _UNREACHABLE as err:
            raise AuthUnavailable(f"Network error completing sign-in: {err!r}") from err

        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        if "error" in query:
            description = query.get("error_description", [""])[0]
            _raise_for_b2c_error(description)
            raise SignInBlocked(description or query["error"][0])
        code = query.get("code", [""])[0]
        if not code:
            raise AuthError("Kohler's sign-in redirect carried no authorization code.")
        return code

    async def _async_exchange_code(self, code: str, verifier: str) -> TokenSet:
        """Trade the authorization code for tokens."""
        return await self._async_token_request(
            {
                "client_id": CLIENT_ID,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": B2C_REDIRECT_URI,
                "code_verifier": verifier,
                "scope": B2C_SCOPE,
            }
        )

    async def async_refresh(self, refresh_token: str | None = None) -> TokenSet:
        """Mint a fresh access token from the refresh token.

        Unserialised; prefer `async_get_access_token`. B2C rotates the refresh token on
        every refresh, and `_async_token_request` stores and reports the new one.
        """
        token = refresh_token or self.refresh_token
        if not token:
            raise AuthError("No refresh token available; sign in again.")
        return await self._async_token_request(
            {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": token,
                "scope": B2C_SCOPE,
            }
        )

    async def _async_token_request(
        self, data: dict[str, str], *, adopt_newer: bool = True
    ) -> TokenSet:
        """POST to the token endpoint, store the resulting `TokenSet`, report a rotation.

        Raises `AuthUnavailable` for an outage and `AuthError` (or a subclass) for a
        refusal. ``adopt_newer`` allows the one retry in `_adopt_newer_refresh_token`.
        """
        try:
            async with self._session.post(
                B2C_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=aiohttp.ClientTimeout(30),
            ) as resp:
                status = resp.status
                try:
                    payload = await resp.json(content_type=None)
                except ValueError as err:
                    # B2C answers in JSON, refusals included; anything else (a gateway's
                    # HTML error page) came from something in front of it.
                    raise AuthUnavailable(
                        f"Kohler's token endpoint sent a non-JSON reply (HTTP {status})"
                    ) from err
        except _UNREACHABLE as err:
            raise AuthUnavailable(
                f"Network error during token request: {err!r}"
            ) from err
        if not isinstance(payload, dict):
            raise AuthUnavailable(
                f"Kohler's token endpoint sent {type(payload).__name__}"
            )
        if status != 200:
            error = str(payload.get("error") or "")
            detail = payload.get("error_description") or error or f"HTTP {status}"
            # An outage says nothing about the credential and must not prompt reauth.
            # 408 and 429 count as outages, as in `KohlerError.rejected`.
            if status >= 500 or status in (408, 429) or error in _OUTAGE_ERRORS:
                raise AuthUnavailable(f"Kohler token request failed: {detail}")
            if (
                error == "invalid_grant"
                and adopt_newer
                and (newer := self._adopt_newer_refresh_token(data)) is not None
            ):
                return await self._async_token_request(
                    {**data, "refresh_token": newer}, adopt_newer=False
                )
            _raise_for_b2c_error(str(detail))
            raise AuthError(f"Kohler token request failed: {detail}")

        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token") or self.refresh_token
        if not access_token or not refresh_token:
            raise AuthError("Kohler did not return a usable token pair.")

        rotated = refresh_token != self._refresh_token
        lifetime = _token_lifetime(payload.get("expires_in"))
        self._tokens = TokenSet(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + lifetime,
            # A short-lived token is still used for half its life before renewal.
            refresh_margin=min(TOKEN_EXPIRY_MARGIN_SECONDS, lifetime / 2),
        )
        self._refresh_token = refresh_token

        # ⚠️ Persist every rotation here, at once: B2C has already retired the old token, so
        # an unpersisted rotation strands the account at the next start. A failure to
        # persist must not fail this request; the token in memory still works for this run.
        if rotated and self.on_token_rotated is not None:
            try:
                self.on_token_rotated(refresh_token)
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Could not persist the rotated Kohler refresh token")
        return self._tokens

    def _adopt_newer_refresh_token(self, data: dict[str, str]) -> str | None:
        """Install a newer refresh token from `refresh_token_source`, if it holds one.

        For a refresh B2C refused as ``invalid_grant``, which is also its answer for a token
        already redeemed. That happens on a reload with a refresh in flight: the unloading
        coordinator's shielded refresh redeems the token, and its successor sends the same
        token again. The winner persists what it was issued, so the loser takes that up
        here and retries once with it.

        Returns the newer token, already installed, or None (never the refused token).
        The retry runs with ``adopt_newer=False``, so this happens at most once per refresh.
        """
        refused = data.get("refresh_token")
        if (
            data.get("grant_type") != "refresh_token"
            or not refused
            or self._refresh_token_source is None
        ):
            return None
        try:
            newer = self._refresh_token_source()
        except Exception:
            _LOGGER.exception("Could not read the stored Kohler refresh token")
            return None
        if not newer or newer == refused:
            return None
        _LOGGER.info(
            "Kohler refused the refresh token held here; retrying once with the newer "
            "one stored since"
        )
        self._tokens = None
        self._refresh_token = newer
        return newer

    def invalidate_access_token(self, rejected: str | None = None) -> None:
        """Drop the cached access token after a 401, keeping the refresh token.

        The next `async_get_access_token` then mints a new one under the refresh lock.
        Given ``rejected`` (the token the server refused), the cache is dropped only while
        it still holds that token, so parallel 401s on one token cause a single refresh.
        """
        if rejected is not None and (
            self._tokens is None or self._tokens.access_token != rejected
        ):
            return
        self._tokens = None

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing it if needed.

        ⚠️ Refreshes are serialised because B2C rotates the refresh token on every use: two
        concurrent refreshes would redeem the same token, and the loser's rejection would
        read as a dead credential (`credential_is_dead`) and prompt a needless reauth. A
        caller that gets the lock after another refreshed finds the new token and makes no
        request.

        The refresh runs as its own task, awaited through `asyncio.shield`, so cancelling
        the caller cannot lose a token B2C has already rotated: the task still stores and
        persists it. A later caller waits for a refresh in flight rather than starting
        another.
        """
        if self._tokens is not None and not self._tokens.expired:
            return self._tokens.access_token
        async with self._refresh_lock:
            if self._tokens is None or self._tokens.expired:
                task = self._refresh_task
                if task is None or task.done():
                    # Started eagerly: it runs up to its first await before this line
                    # returns, as a direct call would.
                    task = asyncio.Task(
                        self.async_refresh(),
                        loop=asyncio.get_running_loop(),
                        eager_start=True,
                    )
                    task.add_done_callback(_retrieve_result)
                    self._refresh_task = task
                await asyncio.shield(task)
            if self._tokens is None:
                # Unreachable: `async_refresh` returns tokens or raises. Raised rather
                # than asserted so `python -O` cannot skip it.
                raise AuthError("Sign-in succeeded but produced no access token.")
            return self._tokens.access_token
