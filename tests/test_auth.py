"""Cognito session policy: reuse, refresh once, one full login, never retry a refusal."""

from __future__ import annotations

import aiohttp
import pytest

from custom_components.veolia_eau.api import (
    CognitoSession,
    Tokens,
    VeoliaApiError,
    VeoliaAuthRefused,
    VeoliaConnectionError,
)

from .fakes import Call, FakeHttp, FakeResponse, auth_error, auth_ok

PASSWORD = "synthetic-password"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def make(http: FakeHttp, tokens: Tokens | None = None, **kw: object) -> CognitoSession:
    return CognitoSession(http, "user@example.org", PASSWORD, tokens=tokens, **kw)  # type: ignore[arg-type]


async def test_login_then_reuse() -> None:
    http = FakeHttp(cognito=lambda c: auth_ok())
    saved: list[Tokens] = []
    session = make(http, on_tokens=saved.append)
    assert await session.token() == "access-1"
    assert await session.token() == "access-1"
    assert [c.flow for c in http.cognito_calls] == ["USER_PASSWORD_AUTH"]
    assert http.calls[0].method == "POST"
    assert http.calls[0].kwargs["json"]["AuthParameters"] == {
        "USERNAME": "user@example.org",
        "PASSWORD": PASSWORD,
    }
    assert saved[-1].refresh_token == "refresh-1"
    assert "access-1" not in repr(saved[-1]) and "refresh-1" not in repr(saved[-1])


async def test_expired_token_uses_refresh_and_keeps_refresh_token() -> None:
    clock = Clock()
    answers = {"USER_PASSWORD_AUTH": auth_ok(), "REFRESH_TOKEN_AUTH": auth_ok("access-2", None)}
    http = FakeHttp(cognito=lambda c: answers[c.flow])
    session = make(http, clock=clock)
    await session.token()
    clock.now += 3600
    assert await session.token() == "access-2"
    clock.now += 3600
    await session.token()
    flows = [c.flow for c in http.cognito_calls]
    assert flows == ["USER_PASSWORD_AUTH", "REFRESH_TOKEN_AUTH", "REFRESH_TOKEN_AUTH"]
    assert http.cognito_calls[2].kwargs["json"]["AuthParameters"] == {"REFRESH_TOKEN": "refresh-1"}


async def test_restored_tokens_avoid_a_password_login() -> None:
    clock = Clock()
    http = FakeHttp(cognito=lambda c: auth_ok("access-9", None))
    session = make(http, tokens=Tokens("old", clock.now - 10, "refresh-0"), clock=clock)
    assert await session.token() == "access-9"
    assert [c.flow for c in http.cognito_calls] == ["REFRESH_TOKEN_AUTH"]


async def test_refused_refresh_makes_one_full_login() -> None:
    clock = Clock()

    def cognito(call: Call) -> FakeResponse:
        if call.flow == "REFRESH_TOKEN_AUTH":
            return auth_error("NotAuthorizedException")
        return auth_ok("access-3", "refresh-3")

    http = FakeHttp(cognito=cognito)
    session = make(http, tokens=Tokens("old", 0.0, "refresh-0"), clock=clock)
    assert await session.token() == "access-3"
    assert [c.flow for c in http.cognito_calls] == ["REFRESH_TOKEN_AUTH", "USER_PASSWORD_AUTH"]


@pytest.mark.parametrize(
    "code",
    [
        "NotAuthorizedException",
        "UserNotFoundException",
        "UserNotConfirmedException",
        "PasswordResetRequiredException",
    ],
)
async def test_refused_password_is_tried_once_and_locks(code: str) -> None:
    http = FakeHttp(cognito=lambda c: auth_error(code))
    session = make(http)
    with pytest.raises(VeoliaAuthRefused) as err:
        await session.token()
    assert err.value.code == code
    assert PASSWORD not in str(err.value)
    for _ in range(3):
        with pytest.raises(VeoliaAuthRefused):
            await session.token()
    assert len(http.cognito_calls) == 1
    assert session.refused == code


async def test_refused_refresh_then_refused_password_is_two_calls_then_locked() -> None:
    http = FakeHttp(cognito=lambda c: auth_error("NotAuthorizedException"))
    session = make(http, tokens=Tokens("old", 0.0, "refresh-0"))
    for _ in range(2):
        with pytest.raises(VeoliaAuthRefused):
            await session.token()
    assert [c.flow for c in http.cognito_calls] == ["REFRESH_TOKEN_AUTH", "USER_PASSWORD_AUTH"]


async def test_challenge_is_a_refusal() -> None:
    http = FakeHttp(cognito=lambda c: FakeResponse(200, {"ChallengeName": "SMS_MFA"}))
    session = make(http)
    with pytest.raises(VeoliaAuthRefused) as err:
        await session.token()
    assert err.value.code == "SMS_MFA"
    with pytest.raises(VeoliaAuthRefused):
        await session.token()
    assert len(http.cognito_calls) == 1


async def test_set_password_unlocks() -> None:
    answers = [auth_error("NotAuthorizedException"), auth_ok("access-5")]
    http = FakeHttp(cognito=lambda c: answers.pop(0))
    session = make(http)
    with pytest.raises(VeoliaAuthRefused):
        await session.token()
    session.set_password("another-synthetic-password")
    assert session.refused is None
    assert await session.token() == "access-5"
    assert http.cognito_calls[1].kwargs["json"]["AuthParameters"]["PASSWORD"] != PASSWORD


@pytest.mark.parametrize("error", [aiohttp.ClientConnectionError("x"), TimeoutError()])
async def test_network_error_is_not_a_refusal(error: Exception) -> None:
    answers: list[FakeResponse | Exception] = [error, auth_ok()]
    http = FakeHttp(cognito=lambda c: answers.pop(0))
    session = make(http)
    with pytest.raises(VeoliaConnectionError):
        await session.token()
    assert session.refused is None
    assert await session.token() == "access-1"


@pytest.mark.parametrize(
    "response",
    [
        auth_error("TooManyRequestsException"),
        auth_error("InternalErrorException", 500),
        FakeResponse(200, {"AuthenticationResult": {}}),
        FakeResponse(200, {}),
        FakeResponse(200, []),
        FakeResponse(502, "<html>bad gateway</html>"),
    ],
)
async def test_other_cognito_answers_do_not_lock(response: FakeResponse) -> None:
    http = FakeHttp(cognito=lambda c: response)
    session = make(http)
    with pytest.raises(VeoliaApiError):
        await session.token()
    assert session.refused is None


async def test_forget_access_token_forces_a_renewal() -> None:
    answers = {"USER_PASSWORD_AUTH": auth_ok(), "REFRESH_TOKEN_AUTH": auth_ok("access-2", None)}
    http = FakeHttp(cognito=lambda c: answers[c.flow])
    session = make(http)
    session.forget_access_token()  # nothing to forget yet
    await session.token()
    session.forget_access_token()
    assert await session.token() == "access-2"
