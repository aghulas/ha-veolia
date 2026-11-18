"""AWS Cognito session of the eau.veolia.fr web app.

Policy (CLAUDE.md, rule 1):

- an access token that is still valid is reused;
- otherwise the refresh token is tried once;
- if Cognito refuses the refresh token, ONE full login with the password is made;
- if Cognito refuses the password, `VeoliaAuthRefused` is raised; if it answers with a challenge
  (a code sent by e-mail or SMS), `VeoliaChallenge`, a refusal that a person can answer once with
  `answer_challenge()`. Either way the session is locked: no further request is sent to
  Cognito until `set_password()` is called by a reauth flow. A network error is not a refusal
  and does not lock anything.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import COGNITO_CLIENT_ID, COGNITO_URL, REQUEST_TIMEOUT_S, TOKEN_MARGIN_S
from .errors import VeoliaApiError, VeoliaAuthRefused, VeoliaChallenge, VeoliaConnectionError

# Cognito error types that mean "these credentials will not work": never retried.
REFUSALS = frozenset(
    {
        "NotAuthorizedException",
        "UserNotFoundException",
        "UserNotConfirmedException",
        "PasswordResetRequiredException",
        "CodeMismatchException",
        "ExpiredCodeException",
    }
)


# Rate limiting says nothing about the credentials: transient, does not lock the session.
THROTTLED = frozenset({"TooManyRequestsException"})

# Name of the field that carries the code in RespondToAuthChallenge, per challenge type.
CHALLENGE_ANSWER_KEYS = {
    "EMAIL_OTP": "EMAIL_OTP_CODE",
    "SMS_OTP": "SMS_OTP_CODE",
    "SMS_MFA": "SMS_MFA_CODE",
    "SOFTWARE_TOKEN_MFA": "SOFTWARE_TOKEN_MFA_CODE",
    "CUSTOM_CHALLENGE": "ANSWER",
}


@dataclass(frozen=True)
class Tokens:
    """What is worth persisting between restarts (never log it)."""

    access_token: str
    expires_at: float
    refresh_token: str | None

    def __repr__(self) -> str:
        return f"Tokens(expires_at={self.expires_at:.0f}, refresh={self.refresh_token is not None})"


class CognitoSession:
    """Keeps a valid access token, following the policy above."""

    def __init__(
        self,
        http: aiohttp.ClientSession,
        username: str,
        password: str,
        tokens: Tokens | None = None,
        on_tokens: Callable[[Tokens], None] | None = None,
        clock: Callable[[], float] = time.time,
        refused: str | None = None,
    ) -> None:
        self._http = http
        self._username = username
        self._password = password
        self._tokens = tokens
        self._on_tokens = on_tokens
        self._clock = clock
        self._refused = refused  # a refusal remembered from before a restart keeps the lock
        self._lock = asyncio.Lock()

    @property
    def refused(self) -> str | None:
        """Cognito code of the refusal that locked the session, if any."""
        return self._refused

    def set_password(self, password: str) -> None:
        """Reauth: a new password given by the user unlocks the session."""
        self._password = password
        self._tokens = None
        self._refused = None

    def forget_access_token(self) -> None:
        """The backend answered 401 before the announced expiry: force a renewal."""
        if self._tokens is not None:
            self._store(Tokens(self._tokens.access_token, 0.0, self._tokens.refresh_token))

    async def token(self) -> str:
        """Return a valid access token."""
        async with self._lock:
            if self._refused is not None:
                raise VeoliaAuthRefused(self._refused)
            t = self._tokens
            if t is not None and self._clock() < t.expires_at - TOKEN_MARGIN_S:
                return t.access_token
            if t is not None and t.refresh_token:
                try:
                    return await self._refresh(t.refresh_token)
                except VeoliaAuthRefused:
                    pass  # refresh token no longer accepted: one full login below
            return await self._login()

    async def _login(self) -> str:
        try:
            result = await self._initiate(
                "USER_PASSWORD_AUTH", {"USERNAME": self._username, "PASSWORD": self._password}
            )
        except VeoliaAuthRefused as err:
            self._refused = err.code
            self._tokens = None
            raise
        except VeoliaApiError as err:
            # Any other 4xx answer to a password login (unknown error type, blocked request…)
            # is treated as a refusal too: when in doubt, the password is not sent again.
            if 400 <= err.status < 500 and err.code not in THROTTLED:
                self._refused = err.code or f"HTTP {err.status}"
                self._tokens = None
                raise VeoliaAuthRefused(self._refused) from None
            raise
        return self._accept(result, None)

    async def _refresh(self, refresh_token: str) -> str:
        result = await self._initiate("REFRESH_TOKEN_AUTH", {"REFRESH_TOKEN": refresh_token})
        return self._accept(result, refresh_token)

    def _accept(self, result: dict[str, Any], previous_refresh: str | None) -> str:
        access = result.get("AccessToken")
        if not isinstance(access, str) or not access:
            raise VeoliaApiError(200, "cognito", "no access token")
        expires_in = float(result.get("ExpiresIn") or 0)
        refresh = result.get("RefreshToken") or previous_refresh
        self._store(Tokens(access, self._clock() + expires_in, refresh))
        return access

    def _store(self, tokens: Tokens) -> None:
        self._tokens = tokens
        if self._on_tokens is not None:
            self._on_tokens(tokens)

    async def answer_challenge(self, challenge: VeoliaChallenge, code: str) -> str:
        """Answer a second-factor challenge with the code the user received. One call.

        A wrong or expired code raises `VeoliaAuthRefused`; the session stays locked either way
        until a call succeeds or `set_password()` is called.
        """
        async with self._lock:
            key = CHALLENGE_ANSWER_KEYS.get(challenge.code, "ANSWER")
            data = await self._call(
                "RespondToAuthChallenge",
                {
                    "ChallengeName": challenge.code,
                    "ClientId": COGNITO_CLIENT_ID,
                    "Session": challenge.session,
                    "ChallengeResponses": {"USERNAME": challenge.username, key: code.strip()},
                },
            )
            access = self._accept(self._result(data), None)
            self._refused = None
            return access

    async def _initiate(self, flow: str, parameters: dict[str, str]) -> dict[str, Any]:
        """One InitiateAuth call."""
        body = {"AuthFlow": flow, "ClientId": COGNITO_CLIENT_ID, "AuthParameters": parameters}
        return self._result(await self._call("InitiateAuth", body))

    def _result(self, data: dict[str, Any]) -> dict[str, Any]:
        challenge = data.get("ChallengeName")
        if challenge:  # needs a person: never retried
            parameters = data.get("ChallengeParameters")
            username = parameters.get("USERNAME") if isinstance(parameters, dict) else None
            raise VeoliaChallenge(
                str(challenge), str(data.get("Session") or ""), str(username or self._username)
            )
        result = data.get("AuthenticationResult")
        if not isinstance(result, dict):
            raise VeoliaApiError(200, "cognito", "no authentication result")
        return result

    async def _call(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        """One Cognito call. Never logs or raises what it sends."""
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": f"AWSCognitoIdentityProviderService.{action}",
        }
        try:
            async with self._http.request(
                "POST",
                COGNITO_URL,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
            ) as response:
                status = response.status
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise VeoliaConnectionError(f"cognito unreachable ({type(err).__name__})") from None
        except ValueError:
            raise VeoliaApiError(status, "cognito", "not json") from None
        if not isinstance(data, dict):
            raise VeoliaApiError(status, "cognito", "unexpected body")
        if status != 200:
            code = str(data.get("__type", "")).rsplit("#", 1)[-1]
            if code in REFUSALS:
                raise VeoliaAuthRefused(code)
            raise VeoliaApiError(status, "cognito", code)
        return data
