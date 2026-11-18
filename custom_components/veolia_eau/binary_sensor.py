"""Possible leak: the closed-day baseline stays above the threshold."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import VeoliaCoordinator
from .entity import VeoliaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """One leak sensor per subscription."""
    coordinator: VeoliaCoordinator = entry.runtime_data
    async_add_entities(VeoliaLeakSensor(coordinator, sub) for sub in coordinator.subscriptions)


class VeoliaLeakSensor(VeoliaEntity, BinarySensorEntity):
    """On when the last closed days are all above the threshold."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: VeoliaCoordinator, subscription: str) -> None:
        super().__init__(coordinator, subscription, "possible_leak")

    @property
    def is_on(self) -> bool | None:
        return self.sub.leak if self.sub else None
