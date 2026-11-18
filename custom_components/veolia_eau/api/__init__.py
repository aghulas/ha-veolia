"""Home Assistant independent client of the eau.veolia.fr customer API (read-only)."""

from .auth import CognitoSession, Tokens
from .client import VeoliaClient
from .errors import (
    VeoliaApiError,
    VeoliaAuthRefused,
    VeoliaConnectionError,
    VeoliaError,
    VeoliaForbiddenRoute,
)

__all__ = [
    "CognitoSession",
    "Tokens",
    "VeoliaApiError",
    "VeoliaAuthRefused",
    "VeoliaClient",
    "VeoliaConnectionError",
    "VeoliaError",
    "VeoliaForbiddenRoute",
]
