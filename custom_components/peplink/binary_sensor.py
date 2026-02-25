"""Binary sensor platform for Peplink Router.

Entity names match the Android plugin MQTT discovery names exactly.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, WAN_TYPE_CELLULAR
from .coordinator import PeplinkCoordinator
from .entity import PeplinkEntity, PeplinkWanEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: PeplinkCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []

    # Per-WAN: carrier aggregation (cellular only)
    for conn_id, wan in coordinator.wan_connections.items():
        if wan.wan_type == WAN_TYPE_CELLULAR:
            entities.append(CarrierAggregationSensor(coordinator, entry, conn_id))

    # Global diagnostic binary sensors
    entities += [
        ApiConnectedSensor(coordinator, entry),
        AuthenticatedSensor(coordinator, entry),
        DataHealthySensor(coordinator, entry),
    ]

    async_add_entities(entities)


class CarrierAggregationSensor(PeplinkWanEntity, BinarySensorEntity):
    """Carrier aggregation active. Name: '{wan.name} Carrier Aggregation'."""

    _attr_icon = "mdi:signal-variant"

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_carrier_aggregation"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Carrier Aggregation" if wan else f"WAN {self._conn_id} Carrier Aggregation"

    @property
    def is_on(self) -> bool | None:
        wan = self._wan
        if wan is None or wan.cellular is None:
            return None
        return wan.cellular.carrier_aggregation


class ApiConnectedSensor(PeplinkEntity, BinarySensorEntity):
    """API reachable. Name: 'API Connected'."""

    _attr_name = "API Connected"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:api"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_api_connected"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.api_connected


class AuthenticatedSensor(PeplinkEntity, BinarySensorEntity):
    """Valid auth credential. Name: 'Authenticated'."""

    _attr_name = "Authenticated"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:shield-check"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_authenticated"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.authenticated


class DataHealthySensor(PeplinkEntity, BinarySensorEntity):
    """Last poll returned non-empty WAN data. Name: 'Data Healthy'."""

    _attr_name = "Data Healthy"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:database-check"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_data_healthy"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.data_healthy
