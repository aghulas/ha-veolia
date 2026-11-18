"""Sensors of a subscription. The daily history itself lives in the external statistic."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SubscriptionData, VeoliaCoordinator
from .entity import VeoliaEntity


@dataclass(frozen=True, kw_only=True)
class VeoliaSensorDescription(SensorEntityDescription):
    """Sensor with its value (and optional attributes) taken from SubscriptionData."""

    value: Callable[[SubscriptionData], float | date | str | None]
    attributes: Callable[[SubscriptionData], dict[str, Any]] | None = None


SENSORS: tuple[VeoliaSensorDescription, ...] = (
    VeoliaSensorDescription(
        key="last_daily_volume",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=0,
        value=lambda d: d.last_day.litres if d.last_day else None,
        attributes=lambda d: (
            {"day": d.last_day.day.isoformat(), "reliability": d.last_day.reliability}
            if d.last_day
            else {}
        ),
    ),
    VeoliaSensorDescription(
        key="closed_day_baseline",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=0,
        value=lambda d: d.baseline[-1][1] if d.baseline else None,
        attributes=lambda d: (
            {
                "day": d.baseline[-1][0].isoformat(),
                "recent": {day.isoformat(): litres for day, litres in d.baseline[-5:]},
            }
            if d.baseline
            else {}
        ),
    ),
    VeoliaSensorDescription(
        key="meter_index",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: d.info.index_m3,
    ),
    VeoliaSensorDescription(
        key="last_reading_date",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: d.info.index_date,
    ),
    VeoliaSensorDescription(
        key="reading_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: d.info.reading_mode,
    ),
    VeoliaSensorDescription(
        key="amount_due",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        value=lambda d: d.info.balance,
    ),
    VeoliaSensorDescription(
        key="last_invoice_amount",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        value=lambda d: d.invoice.amount if d.invoice else None,
    ),
    VeoliaSensorDescription(
        key="last_invoice_date",
        device_class=SensorDeviceClass.DATE,
        value=lambda d: d.invoice.day if d.invoice else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """One set of sensors per subscription."""
    coordinator: VeoliaCoordinator = entry.runtime_data
    async_add_entities(
        VeoliaSensor(coordinator, sub, description)
        for sub in coordinator.subscriptions
        for description in SENSORS
    )


class VeoliaSensor(VeoliaEntity, SensorEntity):
    """A value of SubscriptionData."""

    entity_description: VeoliaSensorDescription

    def __init__(
        self,
        coordinator: VeoliaCoordinator,
        subscription: str,
        description: VeoliaSensorDescription,
    ) -> None:
        super().__init__(coordinator, subscription, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | date | str | None:
        return self.entity_description.value(self.sub) if self.sub else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.sub is None or self.entity_description.attributes is None:
            return None
        return self.entity_description.attributes(self.sub)
