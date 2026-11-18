"""Read-only client of the eau.veolia.fr backend: GET on allow-listed paths only."""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from .auth import CognitoSession
from .const import (
    ALLOWED_PATHS,
    BACKEND_URL,
    HABILITATION_ERROR,
    HABILITATION_PATH,
    ORIGIN,
    REQUEST_TIMEOUT_S,
)
from .errors import VeoliaApiError, VeoliaConnectionError, VeoliaForbiddenRoute


def check_path(path: str) -> None:
    """Refuse anything outside the allow-list (before any network access)."""
    if not any(pattern.fullmatch(path) for pattern in ALLOWED_PATHS):
        raise VeoliaForbiddenRoute(f"path not allowed by the read-only client: {path}")


class VeoliaClient:
    """Backend calls for one login. Subscription ids are passed to each method."""

    def __init__(self, http: aiohttp.ClientSession, session: CognitoSession) -> None:
        self._http = http
        self.session = session

    async def _send(self, path: str, params: dict[str, str] | None) -> tuple[int, str]:
        token = await self.session.token()
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Origin": ORIGIN,
        }
        try:
            async with self._http.request(
                "GET",
                BACKEND_URL + path,
                params=params,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
            ) as response:
                status = response.status
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise VeoliaConnectionError(f"backend unreachable ({type(err).__name__})") from None
        return status, text

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """GET an allow-listed path and return its JSON (None when the body is empty).

        - 403 ERR_HABILATION_MANQUANTE (business accounts): open the habilitation once, retry once.
        - 401: renew the access token once (the session picks refresh or login), retry once.
        """
        check_path(path)
        habilitated = renewed = False
        while True:
            status, text = await self._send(path, params)
            if status == 403 and HABILITATION_ERROR in text and not habilitated:
                habilitated = True
                await self._send(HABILITATION_PATH, None)
                continue
            if status == 401 and not renewed:
                renewed = True
                self.session.forget_access_token()
                continue
            if status >= 300:
                code = HABILITATION_ERROR if HABILITATION_ERROR in text else ""
                raise VeoliaApiError(status, path, code)
            if not text.strip():
                return None
            try:
                return json.loads(text)
            except ValueError:
                raise VeoliaApiError(status, path, "not json") from None

    # --- account -------------------------------------------------------------------------

    async def espace_client(self) -> Any:
        """Residential account description (contacts → tiers → abonnements)."""
        return await self.get("/espace-client", {"type-front": "WEB_ORDINATEUR"})

    async def espace_client_pro(self) -> Any:
        """Business account landing call (also opens the habilitation)."""
        return await self.get(HABILITATION_PATH)

    # --- one subscription ----------------------------------------------------------------

    async def facturation(self, subscription: str) -> dict[str, Any]:
        """Subscription record. Holds IBAN/BIC and the meter number: never log it."""
        data = await self.get(f"/abonnements/{subscription}/facturation")
        if not isinstance(data, dict):
            raise VeoliaApiError(200, "facturation", "unexpected body")
        return data

    async def daily(
        self, subscription: str, pds: str, start: str, year: int, month: int
    ) -> list[dict[str, Any]]:
        """Daily rows of one month. `pds` and `start` come from `facturation()`."""
        params = {
            "mois": str(month),
            "annee": str(year),
            "numero-pds": pds,
            "date-debut-abonnement": start,
        }
        return _rows(await self.get(f"/consommations/{subscription}/journalieres", params))

    async def monthly(
        self, subscription: str, pds: str, start: str, year: int
    ) -> list[dict[str, Any]]:
        """Monthly rows of one year."""
        params = {"annee": str(year), "numero-pds": pds, "date-debut-abonnement": start}
        return _rows(await self.get(f"/consommations/{subscription}/mensuelles", params))

    async def hourly(
        self, subscription: str, pds: str, start: str, day: str
    ) -> list[dict[str, Any]]:
        """Hourly rows of one day (often partial, see docs/api.md)."""
        params = {"jour": day, "numero-pds": pds, "date-debut-abonnement": start}
        return _rows(await self.get(f"/consommations/{subscription}/horaires", params))

    async def billing_readings(self, subscription: str) -> list[dict[str, Any]]:
        """Readings used for invoices."""
        return _rows(await self.get(f"/consommations/facturation/{subscription}"))

    async def invoices(self, subscription: str) -> list[dict[str, Any]]:
        """Invoice list (metadata only; PDFs are not downloaded)."""
        return _rows(await self.get(f"/documents-archive/abonnement/{subscription}/factures"))


def _rows(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if not isinstance(data, list):
        raise VeoliaApiError(200, "rows", "unexpected body")
    return [row for row in data if isinstance(row, dict)]
