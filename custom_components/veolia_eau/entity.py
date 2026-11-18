"""Base entity: one device per subscription."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SubscriptionData, VeoliaCoordinator


class VeoliaEntity(CoordinatorEntity[VeoliaCoordinator]):
    """Entity of one subscription."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VeoliaCoordinator, subscription: str, key: str) -> None:
        super().__init__(coordinator)
        self._subscription = subscription
        self._attr_translation_key = key
        self._attr_unique_id = f"{subscription}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subscription)},
            name=f"Veolia {subscription}",
            manufacturer="Veolia",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def sub(self) -> SubscriptionData | None:
        """Data of this subscription, if the last refresh returned it."""
        return (self.coordinator.data or {}).get(self._subscription)

    @property
    def available(self) -> bool:
        return super().available and self.sub is not None
