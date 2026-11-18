"""Shared fixtures. All data in the tests is synthetic."""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from .fakes import Call, FakeHttp, FakeResponse, auth_ok

SUB = "1234567"
USER = "user@example.org"
PASSWORD = "synthetic-password"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock: Any, enable_custom_integrations: None) -> None:
    """Allow loading the custom integration; the recorder must be set up before hass."""


def account(*subs: Any) -> dict[str, Any]:
    rows = [
        {"id_abonnement": s, "numero_compteur": f"M{s}", "adresse_de_branchement": "1 RUE TEST"}
        for s in subs
    ]
    return {"contacts": [{"tiers": [{"abonnements": rows}]}]}


class Backend:
    """A synthetic Veolia backend: daily volumes are `litres(day)`."""

    def __init__(self) -> None:
        self.account: dict[str, Any] = account(int(SUB))
        self.account_pro: dict[str, Any] = account()
        self.start = "2026-08-15"
        self.daily_start: str | None = None  # daily data may begin after the subscription
        self.monthly_m3 = 100.0
        self.daily_refused_before: str | None = None  # "YYYY-MM": older months answer 422
        self.monthly_refused_before: int | None = None  # older years answer 422
        self.litres = lambda day: 60.0 if day.weekday() == 6 else 5000.0
        self.last_day = date(2026, 11, 17)
        self.habilitated = False

    def __call__(self, call: Call) -> FakeResponse:
        path = call.path
        if path == "/espace-client":
            return FakeResponse(200, self.account)
        if path == "/espace-client/pro":
            self.habilitated = True
            return FakeResponse(200, self.account_pro)
        if not self.habilitated:
            return FakeResponse(403, {"message": "ERR_HABILATION_MANQUANTE"})
        if path.endswith("/facturation"):
            return FakeResponse(
                200,
                {
                    "numero_pds": "PDS-TEST",
                    "date_debut_abonnement": self.start,
                    "mode_releve": "TELERELEVE",
                    "dernier_index_releve": 1234.5,
                    "date_index_releve": "2026-11-17",
                    "solde": 42.5,
                    "iban": "SYNTHETIC-IBAN",
                },
            )
        if path.endswith("/journalieres"):
            year, month = int(call.kwargs["params"]["annee"]), int(call.kwargs["params"]["mois"])
            if self.daily_refused_before and f"{year}-{month:02d}" < self.daily_refused_before:
                return FakeResponse(422, {"message": "synthetic"})
            day, rows = date(year, month, 1), []
            while day.month == month and day <= self.last_day:
                if day.isoformat() >= (self.daily_start or self.start):
                    rows.append(
                        {
                            "date_releve": day.isoformat(),
                            "consommation": {"litre": self.litres(day)},
                            "index": {"m3": 1000},
                            "fiabilite_index": "M",
                        }
                    )
                day += timedelta(days=1)
            return FakeResponse(200, rows)
        if path.endswith("/mensuelles"):
            year = int(call.kwargs["params"]["annee"])
            if self.monthly_refused_before and year < self.monthly_refused_before:
                return FakeResponse(422, {"message": "synthetic"})
            rows = [
                {
                    "annee": year,
                    "mois": m,
                    "consommation": {"m3": self.monthly_m3},
                    "fiabilite_conso": "M",
                }
                for m in range(1, 13)
                if self.start[:7] <= f"{year}-{m:02d}" <= self.last_day.isoformat()[:7]
            ]
            return FakeResponse(200, rows)
        if "/consommations/facturation/" in path:
            return FakeResponse(
                200,
                [
                    {
                        "date_facturation_precedente": "2026-03-31",
                        "date_facturation": "2026-06-30",
                        "volume_facture_eau": {"m3": 250},
                        "index_facture": 1100,
                    },
                    {
                        "date_facturation_precedente": "2026-06-30",
                        "date_facturation": "2026-09-30",
                        "volume_facture_eau": {"m3": 131.5},
                        "index_facture": 1231.5,
                    },
                    {"date_facturation": None},
                ],
            )
        if path.endswith("/factures"):
            return FakeResponse(
                200,
                [
                    {
                        "code_document": "CFAEAU",
                        "date_emission": "2026-06-30",
                        "montant_facture_ttc": 800,
                    },
                    {
                        "code_document": "CFAEAU",
                        "date_emission": "2026-09-30",
                        "montant_facture_ttc": 912.3,
                    },
                    {
                        "code_document": "CREEAU",
                        "date_emission": "2026-10-20",
                        "montant_facture_ttc": 912.3,
                    },
                ],
            )
        return FakeResponse(404)


@pytest.fixture
def backend() -> Backend:
    return Backend()


@pytest.fixture
def http(backend: Backend) -> Generator[FakeHttp]:
    """Replace the aiohttp session Home Assistant gives to the integration."""
    fake = FakeHttp(cognito=lambda c: auth_ok(), backend=backend)
    with (
        patch("custom_components.veolia_eau.async_get_clientsession", return_value=fake),
        patch(
            "custom_components.veolia_eau.config_flow.async_get_clientsession", return_value=fake
        ),
    ):
        yield fake
