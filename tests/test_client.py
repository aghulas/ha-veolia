"""Backend client: allow-list, GET only, habilitation retry, 401 renewal."""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest

from custom_components.veolia_eau.api import (
    CognitoSession,
    VeoliaApiError,
    VeoliaClient,
    VeoliaConnectionError,
    VeoliaForbiddenRoute,
)
from custom_components.veolia_eau.api.client import check_path

from .fakes import Call, FakeHttp, FakeResponse, auth_ok

SUB = "1234567"
HABILITATION = FakeResponse(403, {"message": "ERR_HABILATION_MANQUANTE"})


def make(backend: Any, cognito: Any = None) -> tuple[FakeHttp, VeoliaClient]:
    http = FakeHttp(cognito=cognito or (lambda c: auth_ok()), backend=backend)
    session = CognitoSession(http, "user@example.org", "synthetic-password")  # type: ignore[arg-type]
    return http, VeoliaClient(http, session)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "path",
    [
        "/espace-client",
        "/espace-client/pro",
        f"/abonnements/{SUB}/facturation",
        f"/consommations/{SUB}/journalieres",
        f"/consommations/{SUB}/mensuelles",
        f"/consommations/{SUB}/horaires",
        f"/consommations/facturation/{SUB}",
        f"/documents-archive/abonnement/{SUB}/factures",
    ],
)
def test_allowed_paths(path: str) -> None:
    check_path(path)


@pytest.mark.parametrize(
    "path",
    [
        f"/abonnements/{SUB}/facturation/mensualisation/plan",
        f"/abonnements/{SUB}/mettre-a-jour-pas-horaire",
        f"/abonnements/{SUB}",
        "/alertes/123",
        "/espace-client/pro/contacts",
        "/documents-archive/abcdefghij1234",
        f"/consommations/{SUB}/journalieres/../../x",
        f"/consommations/{SUB}/journalieres\n",
        "https://example.org/espace-client",
        "",
    ],
)
async def test_forbidden_paths_send_nothing(path: str) -> None:
    http, client = make(lambda c: FakeResponse(200, {}))
    with pytest.raises(VeoliaForbiddenRoute):
        check_path(path)
    with pytest.raises(VeoliaForbiddenRoute):
        await client.get(path)
    assert http.calls == []


async def test_get_sends_bearer_get_only() -> None:
    http, client = make(lambda c: FakeResponse(200, {"numero_pds": "PDS-TEST"}))
    assert await client.facturation(SUB) == {"numero_pds": "PDS-TEST"}
    (call,) = http.backend_calls
    assert call.method == "GET"
    assert call.path == f"/abonnements/{SUB}/facturation"
    assert call.kwargs["headers"]["Authorization"] == "Bearer access-1"
    assert call.kwargs["headers"]["Origin"] == "https://www.eau.veolia.fr"
    assert call.kwargs["allow_redirects"] is False
    assert {c.method for c in http.backend_calls} == {"GET"}


async def test_habilitation_is_opened_once_then_retried() -> None:
    answers = [HABILITATION, FakeResponse(200, {}), FakeResponse(200, {"solde": 0})]
    http, client = make(lambda c: answers.pop(0))
    assert await client.facturation(SUB) == {"solde": 0}
    assert [c.path for c in http.backend_calls] == [
        f"/abonnements/{SUB}/facturation",
        "/espace-client/pro",
        f"/abonnements/{SUB}/facturation",
    ]


async def test_habilitation_still_missing_fails_without_looping() -> None:
    http, client = make(lambda c: HABILITATION)
    with pytest.raises(VeoliaApiError) as err:
        await client.facturation(SUB)
    assert err.value.status == 403 and err.value.code == "ERR_HABILATION_MANQUANTE"
    assert len(http.backend_calls) == 3


async def test_401_renews_the_token_once() -> None:
    answers = [FakeResponse(401), FakeResponse(200, [])]
    cognito = {"USER_PASSWORD_AUTH": auth_ok(), "REFRESH_TOKEN_AUTH": auth_ok("access-2", None)}
    http, client = make(lambda c: answers.pop(0), lambda c: cognito[c.flow])
    assert await client.invoices(SUB) == []
    assert [c.flow for c in http.cognito_calls] == ["USER_PASSWORD_AUTH", "REFRESH_TOKEN_AUTH"]
    assert http.backend_calls[1].kwargs["headers"]["Authorization"] == "Bearer access-2"


async def test_401_twice_fails() -> None:
    http, client = make(lambda c: FakeResponse(401))
    with pytest.raises(VeoliaApiError) as err:
        await client.billing_readings(SUB)
    assert err.value.status == 401
    assert len(http.backend_calls) == 2


async def test_errors_do_not_leak_the_body_or_query() -> None:
    http, client = make(lambda c: FakeResponse(500, {"iban": "SYNTHETIC-IBAN"}))
    with pytest.raises(VeoliaApiError) as err:
        await client.daily(SUB, "PDS-TEST", "2024-11-01", 2026, 10)
    assert "SYNTHETIC" not in str(err.value) and "PDS-TEST" not in str(err.value)


async def test_consumption_parameters() -> None:
    http, client = make(lambda c: FakeResponse(200, [{"x": 1}, "junk"]))
    assert await client.daily(SUB, "PDS-TEST", "2024-11-01", 2026, 10) == [{"x": 1}]
    await client.monthly(SUB, "PDS-TEST", "2024-11-01", 2026)
    await client.hourly(SUB, "PDS-TEST", "2024-11-01", "2026-10-04")
    base = {"numero-pds": "PDS-TEST", "date-debut-abonnement": "2024-11-01"}
    assert [(c.path.rsplit("/", 1)[-1], c.kwargs["params"]) for c in http.backend_calls] == [
        ("journalieres", {"mois": "10", "annee": "2026", **base}),
        ("mensuelles", {"annee": "2026", **base}),
        ("horaires", {"jour": "2026-10-04", **base}),
    ]


async def test_account_calls() -> None:
    http, client = make(lambda c: FakeResponse(200, {"contacts": []}))
    assert await client.espace_client() == {"contacts": []}
    await client.espace_client_pro()
    assert http.backend_calls[0].kwargs["params"] == {"type-front": "WEB_ORDINATEUR"}
    assert http.backend_calls[1].path == "/espace-client/pro"


async def test_empty_and_malformed_bodies() -> None:
    answers = [
        FakeResponse(204),
        FakeResponse(200, "<html>"),
        FakeResponse(200, {"not": "rows"}),
        FakeResponse(200, []),
    ]
    http, client = make(lambda c: answers.pop(0))
    assert await client.invoices(SUB) == []
    with pytest.raises(VeoliaApiError):
        await client.invoices(SUB)
    with pytest.raises(VeoliaApiError):
        await client.invoices(SUB)
    with pytest.raises(VeoliaApiError):
        await client.facturation(SUB)


async def test_network_error() -> None:
    def backend(call: Call) -> Exception:
        return aiohttp.ClientConnectionError("x")

    http, client = make(backend)
    with pytest.raises(VeoliaConnectionError):
        await client.invoices(SUB)
