"""Veolia Eau (pro) — water consumption of Veolia smart meters (eau.veolia.fr), read-only."""

from __future__ import annotations

import random
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_change

from .api import CognitoSession, Tokens, VeoliaClient
from .const import (
    CONF_FETCH_HOUR,
    CONF_REFRESH_TOKEN,
    DEFAULT_FETCH_HOUR,
    PLATFORMS,
    STARTUP_RETRY_S,
)
from .coordinator import VeoliaCoordinator

type VeoliaConfigEntry = ConfigEntry[VeoliaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: VeoliaConfigEntry) -> bool:
    """Set up one Veolia account."""

    @callback
    def save_refresh_token(tokens: Tokens) -> None:
        if tokens.refresh_token and tokens.refresh_token != entry.data.get(CONF_REFRESH_TOKEN):
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_REFRESH_TOKEN: tokens.refresh_token}
            )

    http = async_get_clientsession(hass)
    stored = entry.data.get(CONF_REFRESH_TOKEN)
    session = CognitoSession(
        http,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        tokens=Tokens("", 0.0, stored) if stored else None,
        on_tokens=save_refresh_token,
    )
    coordinator = VeoliaCoordinator(hass, entry, VeoliaClient(http, session))
    entry.runtime_data = coordinator
    # A failed first fetch must not make Home Assistant retry the set-up in a loop (it would poll
    # Veolia every minute or so): the entry loads with unavailable entities instead. A refused
    # login has already started a reauth flow; anything else gets ONE delayed retry, then waits
    # for the daily fetch.
    await coordinator.async_refresh()
    if not coordinator.last_update_success and not isinstance(
        coordinator.last_exception, ConfigEntryAuthFailed
    ):

        async def retry_once(now: datetime) -> None:
            if not coordinator.last_update_success:
                await coordinator.async_refresh()

        entry.async_on_unload(async_call_later(hass, STARTUP_RETRY_S, retry_once))

    async def daily_refresh(now: datetime) -> None:
        await coordinator.async_refresh()

    # Once a day after publication; minute and second are drawn per start-up (jitter).
    entry.async_on_unload(
        async_track_time_change(
            hass,
            daily_refresh,
            hour=int(entry.options.get(CONF_FETCH_HOUR, DEFAULT_FETCH_HOUR)),
            minute=random.randrange(60),
            second=random.randrange(60),
        )
    )
    options = dict(entry.options)

    async def reload_on_options_change(hass: HomeAssistant, changed: ConfigEntry) -> None:
        if dict(changed.options) != options:  # data updates (refresh token) must not reload
            await hass.config_entries.async_reload(changed.entry_id)

    entry.async_on_unload(entry.add_update_listener(reload_on_options_change))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VeoliaConfigEntry) -> bool:
    """Unload one Veolia account."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
