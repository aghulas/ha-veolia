"""Manual refresh (the integration otherwise fetches once a day)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import VeoliaCoordinator
from .entity import VeoliaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """One refresh button per subscription (it refreshes the whole account)."""
    coordinator: VeoliaCoordinator = entry.runtime_data
    async_add_entities(VeoliaRefreshButton(coordinator, sub) for sub in coordinator.subscriptions)


class VeoliaRefreshButton(VeoliaEntity, ButtonEntity):
    """Fetch now. After a refused password it sends nothing: the session stays locked."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VeoliaCoordinator, subscription: str) -> None:
        super().__init__(coordinator, subscription, "refresh")

    @property
    def available(self) -> bool:
        return True  # usable precisely when the last refresh failed

    async def async_press(self) -> None:
        # Debounced by the coordinator: several presses in a row are one fetch.
        await self.coordinator.async_request_refresh()
