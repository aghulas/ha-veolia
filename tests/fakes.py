"""A scripted stand-in for aiohttp.ClientSession (synthetic data only)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from custom_components.veolia_eau.api.const import BACKEND_URL, COGNITO_URL


@dataclass
class Call:
    method: str
    url: str
    kwargs: dict[str, Any]

    @property
    def path(self) -> str:
        return self.url.removeprefix(BACKEND_URL)

    @property
    def flow(self) -> str:
        """AuthFlow of an InitiateAuth call, or the Cognito action for the other calls."""
        return self.kwargs["json"].get("AuthFlow") or self.action

    @property
    def action(self) -> str:
        return self.kwargs["headers"]["X-Amz-Target"].rsplit(".", 1)[-1]


class FakeResponse:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status = status
        self._text = body if isinstance(body, str) else ("" if body is None else json.dumps(body))

    async def text(self) -> str:
        return self._text

    async def json(self, content_type: str | None = None) -> Any:
        return json.loads(self._text)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


Handler = Callable[[Call], "FakeResponse | Exception"]


@dataclass
class FakeHttp:
    """Routes Cognito calls to `cognito` and backend calls to `backend`."""

    cognito: Handler
    backend: Handler = lambda call: FakeResponse(404)
    calls: list[Call] = field(default_factory=list)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        call = Call(method, url, kwargs)
        self.calls.append(call)
        answer = (self.cognito if url == COGNITO_URL else self.backend)(call)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def cognito_calls(self) -> list[Call]:
        return [c for c in self.calls if c.url == COGNITO_URL]

    @property
    def backend_calls(self) -> list[Call]:
        return [c for c in self.calls if c.url != COGNITO_URL]


def auth_ok(access: str = "access-1", refresh: str | None = "refresh-1") -> FakeResponse:
    result: dict[str, Any] = {"AccessToken": access, "ExpiresIn": 3600, "TokenType": "Bearer"}
    if refresh:
        result["RefreshToken"] = refresh
    return FakeResponse(200, {"AuthenticationResult": result})


def auth_error(code: str, status: int = 400) -> FakeResponse:
    return FakeResponse(status, {"__type": code, "message": "synthetic"})


def auth_challenge(name: str = "EMAIL_OTP", session: str = "cognito-session-1") -> FakeResponse:
    return FakeResponse(
        200, {"ChallengeName": name, "Session": session, "ChallengeParameters": {"USERNAME": "u-1"}}
    )
