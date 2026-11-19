"""Config flow: login, subscriptions, reauth, options."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.veolia_eau.const import DOMAIN

from .conftest import PASSWORD, SUB, USER, Backend, account
from .fakes import FakeHttp, FakeResponse, auth_challenge, auth_error, auth_ok

CREDS = {"username": USER, "password": PASSWORD}


@pytest.fixture(autouse=True)
def no_setup(recorder_mock):
    with patch("custom_components.veolia_eau.async_setup_entry", return_value=True) as setup:
        yield setup


async def start(hass: HomeAssistant, creds: dict = CREDS):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], creds)


async def test_single_subscription_creates_the_entry(hass: HomeAssistant, http: FakeHttp) -> None:
    result = await start(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USER
    assert result["data"] == {**CREDS, "refresh_token": "refresh-1", "subscriptions": [SUB]}
    assert [c.path for c in http.backend_calls] == ["/espace-client"]


async def test_business_listing_is_used_when_the_other_is_empty(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    backend.account, backend.account_pro = account(), account("7654321")
    result = await start(hass)
    assert result["data"]["subscriptions"] == ["7654321"]


async def test_several_subscriptions_are_offered(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    backend.account = account(111, 222, 333)
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "subscriptions"
    none = await hass.config_entries.flow.async_configure(result["flow_id"], {"subscriptions": []})
    assert none["errors"] == {"base": "none_selected"}
    done = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"subscriptions": ["111", "333"]}
    )
    assert done["data"]["subscriptions"] == ["111", "333"]


async def test_no_subscription_aborts(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    backend.account = account()
    result = await start(hass)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_subscriptions"


async def test_refused_password_is_one_attempt_per_submission(
    hass: HomeAssistant, http: FakeHttp
) -> None:
    http.cognito = lambda c: auth_error("NotAuthorizedException")
    result = await start(hass)
    assert result["errors"] == {"base": "invalid_auth"}
    assert len(http.cognito_calls) == 1 and http.backend_calls == []


async def test_backend_error(hass: HomeAssistant, http: FakeHttp) -> None:
    http.backend = lambda c: FakeResponse(500)
    assert (await start(hass))["errors"] == {"base": "unknown"}


async def test_already_configured(hass: HomeAssistant, http: FakeHttp) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=USER, data=CREDS).add_to_hass(hass)
    result = await start(hass, {"username": " User@Example.org ", "password": PASSWORD})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    assert http.calls == []


async def test_reauth(hass: HomeAssistant, http: FakeHttp) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER,
        data={**CREDS, "subscriptions": [SUB], "refresh_token": "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    http.cognito = lambda c: auth_error("NotAuthorizedException")
    bad = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "wrong"})
    assert bad["errors"] == {"base": "invalid_auth"}
    http.cognito = lambda c: auth_ok("a", "refresh-new")
    done = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "new"})
    assert done["type"] is FlowResultType.ABORT and done["reason"] == "reauth_successful"
    assert entry.data["password"] == "new" and entry.data["refresh_token"] == "refresh-new"
    assert entry.data["subscriptions"] == [SUB] and entry.data["auth_refused"] is None
    assert len(http.cognito_calls) == 2


async def test_options(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=USER, data=CREDS)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    options = {
        "fetch_hour": 9,
        "backfill_months": 12,
        "closed_weekdays": ["sat", "sun"],
        "leak_threshold_l": 200.0,
        "leak_consecutive_days": 3,
        "leak_repair_issue": False,
        "hourly_statistic": True,
    }
    done = await hass.config_entries.options.async_configure(result["flow_id"], options)
    assert done["type"] is FlowResultType.CREATE_ENTRY and entry.options == options


async def test_holiday_calendar_option_can_be_set_and_emptied(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=USER, data=CREDS)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    done = await hass.config_entries.options.async_configure(
        result["flow_id"], {"holiday_calendar": "calendar.holidays"}
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["holiday_calendar"] == "calendar.holidays"
    assert entry.options["leak_repair_issue"] is True  # on by default
    assert entry.options["hourly_statistic"] is False  # off by default
    assert entry.options["closed_weekdays"] == ["sun"]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    suggested = {
        str(key): key.description for key in result["data_schema"].schema if key.description
    }
    assert suggested["holiday_calendar"] == {"suggested_value": "calendar.holidays"}
    await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert "holiday_calendar" not in entry.options


async def test_verification_code_step(hass: HomeAssistant, http: FakeHttp) -> None:
    answers = [auth_challenge(), auth_error("CodeMismatchException"), auth_ok("a", "refresh-otp")]
    http.cognito = lambda c: answers.pop(0)
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "code"
    assert result["description_placeholders"] == {"username": USER}
    assert http.backend_calls == []

    wrong = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "000000"})
    assert wrong["step_id"] == "code" and wrong["errors"] == {"base": "invalid_code"}

    done = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert done["data"] == {**CREDS, "refresh_token": "refresh-otp", "subscriptions": [SUB]}
    assert [c.flow for c in http.cognito_calls] == [
        "USER_PASSWORD_AUTH",
        "RespondToAuthChallenge",
        "RespondToAuthChallenge",
    ]


async def test_backend_error_after_the_code_returns_to_the_login_form(
    hass: HomeAssistant, http: FakeHttp
) -> None:
    answers = [auth_challenge(), auth_ok()]
    http.cognito = lambda c: answers.pop(0)
    http.backend = lambda c: FakeResponse(500)
    result = await start(hass)
    back = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
    assert back["step_id"] == "user" and back["errors"] == {"base": "unknown"}


async def test_second_challenge_asks_again(hass: HomeAssistant, http: FakeHttp) -> None:
    answers = [auth_challenge(), auth_challenge("SMS_MFA", "cognito-session-2"), auth_ok()]
    http.cognito = lambda c: answers.pop(0)
    result = await start(hass)
    again = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "1"})
    assert again["step_id"] == "code" and not again["errors"]
    done = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "2"})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert http.cognito_calls[2].kwargs["json"]["Session"] == "cognito-session-2"


async def test_reauth_with_verification_code(hass: HomeAssistant, http: FakeHttp) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER,
        data={**CREDS, "subscriptions": [SUB], "refresh_token": "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    answers = [auth_challenge(), auth_ok("a", "refresh-new")]
    http.cognito = lambda c: answers.pop(0)
    code = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "new"})
    assert code["step_id"] == "code"
    done = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
    assert done["type"] is FlowResultType.ABORT and done["reason"] == "reauth_successful"
    assert entry.data["password"] == "new" and entry.data["refresh_token"] == "refresh-new"


async def test_expired_verification_goes_back_to_the_login_form(
    hass: HomeAssistant, http: FakeHttp
) -> None:
    answers = [auth_challenge(), auth_error("NotAuthorizedException")]
    http.cognito = lambda c: answers.pop(0)
    result = await start(hass)
    back = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
    assert back["step_id"] == "user" and back["errors"] == {"base": "invalid_auth"}
