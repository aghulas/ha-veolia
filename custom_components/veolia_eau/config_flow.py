"""Config flow: login (with an optional code sent by Veolia), subscriptions, reauth, options."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_CODE, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from .api import (
    CognitoSession,
    Tokens,
    VeoliaAuthRefused,
    VeoliaChallenge,
    VeoliaClient,
    VeoliaConnectionError,
    VeoliaError,
)
from .const import (
    CONF_BACKFILL_MONTHS,
    CONF_CLOSED_WEEKDAYS,
    CONF_FETCH_HOUR,
    CONF_LEAK_DAYS,
    CONF_LEAK_THRESHOLD,
    CONF_REFRESH_TOKEN,
    CONF_SUBSCRIPTIONS,
    DEFAULT_BACKFILL_MONTHS,
    DEFAULT_CLOSED_WEEKDAYS,
    DEFAULT_FETCH_HOUR,
    DOMAIN,
    WEEKDAYS,
)
from .leak import DEFAULT_CONSECUTIVE, DEFAULT_THRESHOLD_L
from .models import Subscription, parse_subscriptions

_LOGGER = logging.getLogger(__name__)

CREDENTIALS = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})


CODE_ERRORS = {"CodeMismatchException", "ExpiredCodeException"}


def _error(err: VeoliaError) -> str:
    if isinstance(err, VeoliaAuthRefused):
        # The Cognito code tells a wrong password from a wrong or expired verification code.
        _LOGGER.warning("Veolia refused the login: %s", err.code)
        return "invalid_code" if err.code in CODE_ERRORS else "invalid_auth"
    if isinstance(err, VeoliaConnectionError):
        return "cannot_connect"
    return "unknown"


class VeoliaConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per Veolia login."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._found: list[Subscription] = []
        self._tokens: list[Tokens] = []
        self._session: CognitoSession | None = None
        self._challenge: VeoliaChallenge | None = None
        self._username = ""
        self._password = ""

    async def _login(self, username: str, password: str) -> None:
        """One login attempt (each one is a deliberate submission by the user)."""
        self._username, self._password, self._tokens = username, password, []
        self._session = CognitoSession(
            async_get_clientsession(self.hass), username, password, on_tokens=self._tokens.append
        )
        await self._session.token()

    async def _attempt(self, username: str, password: str) -> ConfigFlowResult | dict[str, str]:
        """Log in; continue the flow, ask for the code Veolia sent, or return the form errors."""
        try:
            await self._login(username, password)
        except VeoliaChallenge as challenge:
            _LOGGER.info("Veolia asks for a verification code (%s)", challenge.code)
            self._challenge = challenge
            return await self.async_step_code()
        except VeoliaError as err:
            return {"base": _error(err)}
        return await self._logged_in()

    async def _logged_in(self) -> ConfigFlowResult | dict[str, str]:
        """After a successful login: finish the reauth, or list the subscriptions."""
        assert self._session is not None
        refresh = self._tokens[-1].refresh_token
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates={CONF_PASSWORD: self._password, CONF_REFRESH_TOKEN: refresh},
            )
        client = VeoliaClient(async_get_clientsession(self.hass), self._session)
        try:
            found = parse_subscriptions(await client.espace_client())
            if not found:
                found = parse_subscriptions(await client.espace_client_pro())
        except VeoliaError as err:
            return {"base": _error(err)}
        if not found:
            return self.async_abort(reason="no_subscriptions")
        self._found = found
        self._data = {
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_REFRESH_TOKEN: refresh,
        }
        if len(found) == 1:
            return self._create([found[0].id])
        return await self.async_step_subscriptions()

    def _credentials_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        if self.source == SOURCE_REAUTH:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
                description_placeholders={"username": self._get_reauth_entry().data[CONF_USERNAME]},
                errors=errors,
            )
        return self.async_show_form(step_id="user", data_schema=CREDENTIALS, errors=errors)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the login, then list the subscriptions."""
        if user_input is None:
            return self._credentials_form({})
        username = user_input[CONF_USERNAME].strip()
        await self.async_set_unique_id(username.lower())
        self._abort_if_unique_id_configured()
        result = await self._attempt(username, user_input[CONF_PASSWORD])
        return self._credentials_form(result) if "type" not in result else result  # type: ignore[arg-type,return-value]

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Veolia sent a verification code (first login from a new place): ask for it."""
        assert self._session is not None and self._challenge is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._session.answer_challenge(self._challenge, user_input[CONF_CODE])
            except VeoliaChallenge as challenge:  # another step of the same verification
                self._challenge = challenge
            except VeoliaError as err:
                errors["base"] = _error(err)
            else:
                result = await self._logged_in()
                if "type" in result:
                    return result  # type: ignore[return-value]
                return self._credentials_form(result)  # type: ignore[arg-type]
        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            description_placeholders={"username": self._username},
            errors=errors,
        )

    async def async_step_subscriptions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one or several subscriptions."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_SUBSCRIPTIONS]:
                return self._create(list(user_input[CONF_SUBSCRIPTIONS]))
            errors["base"] = "none_selected"
        choices = {s.id: s.label for s in self._found}
        schema = vol.Schema(
            {vol.Required(CONF_SUBSCRIPTIONS, default=list(choices)): cv.multi_select(choices)}
        )
        return self.async_show_form(step_id="subscriptions", data_schema=schema, errors=errors)

    def _create(self, subscriptions: list[str]) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._data[CONF_USERNAME], data={**self._data, CONF_SUBSCRIPTIONS: subscriptions}
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Veolia refused the stored password: ask for a new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check the new password once, then reload the entry."""
        if user_input is None:
            return self._credentials_form({})
        username = self._get_reauth_entry().data[CONF_USERNAME]
        result = await self._attempt(username, user_input[CONF_PASSWORD])
        return self._credentials_form(result) if "type" not in result else result  # type: ignore[arg-type,return-value]

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> VeoliaOptionsFlow:
        """Options: fetch hour, backfill depth, leak detection."""
        return VeoliaOptionsFlow()


class VeoliaOptionsFlow(OptionsFlow):
    """Fetch hour, backfill depth and leak detection settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Single form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        get = self.config_entry.options.get
        weekdays = SelectSelector(
            SelectSelectorConfig(options=WEEKDAYS, multiple=True, translation_key="weekday")
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_FETCH_HOUR, default=get(CONF_FETCH_HOUR, DEFAULT_FETCH_HOUR)
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                vol.Required(
                    CONF_BACKFILL_MONTHS, default=get(CONF_BACKFILL_MONTHS, DEFAULT_BACKFILL_MONTHS)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
                vol.Required(
                    CONF_CLOSED_WEEKDAYS, default=get(CONF_CLOSED_WEEKDAYS, DEFAULT_CLOSED_WEEKDAYS)
                ): weekdays,
                vol.Required(
                    CONF_LEAK_THRESHOLD, default=get(CONF_LEAK_THRESHOLD, DEFAULT_THRESHOLD_L)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_LEAK_DAYS, default=get(CONF_LEAK_DAYS, DEFAULT_CONSECUTIVE)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
