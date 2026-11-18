"""Diagnostics, redacted: no credentials, tokens, subscription, meter or delivery point numbers."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import VeoliaCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Counts and non-identifying values only; nothing from the account description."""
    coordinator: VeoliaCoordinator = entry.runtime_data
    subscriptions = []
    for data in (coordinator.data or {}).values():
        subscriptions.append(
            {
                "reading_mode": data.info.reading_mode,
                "has_index": data.info.index_m3 is not None,
                "last_reading_date": data.info.index_date.isoformat()
                if data.info.index_date
                else None,
                "last_day": data.last_day.day.isoformat() if data.last_day else None,
                "last_day_reliability": data.last_day.reliability if data.last_day else None,
                "closed_days_in_window": len(data.baseline),
                "possible_leak": data.leak,
                "has_invoice": data.invoice is not None,
            }
        )
    return {
        "options": dict(entry.options),
        "subscription_count": len(coordinator.subscriptions),
        "last_update_success": coordinator.last_update_success,
        "auth_refused": coordinator.client.session.refused,
        "subscriptions": subscriptions,
    }
