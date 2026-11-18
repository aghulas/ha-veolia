"""Errors raised by the Veolia client.

None of their messages contains a token, a password, a response body or a query string.
"""

from __future__ import annotations


class VeoliaError(Exception):
    """Base class."""


class VeoliaAuthRefused(VeoliaError):
    """Cognito refused the credentials (or asked for something we cannot do, e.g. MFA).

    Never retried: the caller must stop polling and ask the user (reauth).
    `code` is the Cognito error type or challenge name.
    """

    def __init__(self, code: str) -> None:
        super().__init__(f"authentication refused by Veolia ({code})")
        self.code = code


class VeoliaConnectionError(VeoliaError):
    """Network failure or timeout: transient, says nothing about the credentials."""


class VeoliaApiError(VeoliaError):
    """Unexpected answer from Cognito or the backend."""

    def __init__(self, status: int, where: str, code: str = "") -> None:
        super().__init__(f"HTTP {status} on {where}" + (f" ({code})" if code else ""))
        self.status = status
        self.code = code


class VeoliaForbiddenRoute(VeoliaError):
    """The path is not in the read-only allow-list: nothing was sent."""
