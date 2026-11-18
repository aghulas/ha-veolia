"""Config flow: login, choice of subscriptions, reauth, options."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    CognitoSession,
    Tokens,
    VeoliaAuthRefused,
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

CREDENTIALS = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})


def _error(err: VeoliaError) -> str:
    if isinstance(err, VeoliaAuthRefused):
        return "invalid_auth"
    if isinstance(err, VeoliaConnectionError):
        return "cannot_connect"
    return "unknown"


class VeoliaConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per Veolia login."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._found: list[Subscription] = []

    async def _login(self, username: str, password: str) -> tuple[VeoliaClient, str | None]:
        """One login attempt (each one is a deliberate submission by the user)."""
        tokens: list[Tokens] = []
        http = async_get_clientsession(self.hass)
        session = CognitoSession(http, username, password, on_tokens=tokens.append)
        await session.token()
        return VeoliaClient(http, session), tokens[-1].refresh_token

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the login, then list the subscriptions."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()
            try:
                client, refresh = await self._login(username, user_input[CONF_PASSWORD])
                found = parse_subscriptions(await client.espace_client())
                if not found:
                    found = parse_subscriptions(await client.espace_client_pro())
            except VeoliaError as err:
                errors["base"] = _error(err)
            else:
                if not found:
                    return self.async_abort(reason="no_subscriptions")
                self._found = found
                self._data = {
                    CONF_USERNAME: username,
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_REFRESH_TOKEN: refresh,
                }
                if len(found) == 1:
                    return self._create([found[0].id])
                return await self.async_step_subscriptions()
        return self.async_show_form(step_id="user", data_schema=CREDENTIALS, errors=errors)

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
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                _, refresh = await self._login(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            except VeoliaError as err:
                errors["base"] = _error(err)
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REFRESH_TOKEN: refresh,
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

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
        weekdays = {day: day for day in WEEKDAYS}
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
                ): cv.multi_select(weekdays),
                vol.Required(
                    CONF_LEAK_THRESHOLD, default=get(CONF_LEAK_THRESHOLD, DEFAULT_THRESHOLD_L)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_LEAK_DAYS, default=get(CONF_LEAK_DAYS, DEFAULT_CONSECUTIVE)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
