"""Device tracker platform for Peplink Router GPS.

Only registered when enable_gps=True in options.
Entity name 'Location' matches the Android plugin MQTT discovery name.
"""
from __future__ import annotations

import logging

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PeplinkCoordinator
from .entity import PeplinkEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GPS device tracker entity."""
    coordinator: PeplinkCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RouterLocationTracker(coordinator, entry)])


class RouterLocationTracker(PeplinkEntity, TrackerEntity):
    """GPS location tracker. Name: 'Location'.

    Provides lat/lon/altitude/speed/heading/accuracy attributes so HA
    displays the router on the map. State is 'home'/'not_home' or 'unavailable'.

    Matches the Android plugin MQTT device_tracker discovery payload.
    """

    _attr_name = "Location"
    _attr_icon = "mdi:crosshairs-gps"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: PeplinkCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_location"

    @property
    def latitude(self) -> float | None:
        if self.coordinator.data is None or self.coordinator.data.location is None:
            return None
        return self.coordinator.data.location.latitude

    @property
    def longitude(self) -> float | None:
        if self.coordinator.data is None or self.coordinator.data.location is None:
            return None
        return self.coordinator.data.location.longitude

    @property
    def location_accuracy(self) -> int:
        if self.coordinator.data is None or self.coordinator.data.location is None:
            return 0
        acc = self.coordinator.data.location.accuracy
        return int(acc) if acc is not None else 0

    @property
    def extra_state_attributes(self) -> dict:
        """Additional GPS attributes for display in HA."""
        if self.coordinator.data is None or self.coordinator.data.location is None:
            return {}
        loc = self.coordinator.data.location
        attrs: dict = {}
        if loc.altitude is not None:
            attrs["altitude"] = loc.altitude
        if loc.speed is not None:
            attrs["speed"] = loc.speed
        if loc.heading is not None:
            attrs["heading"] = loc.heading
        if loc.accuracy is not None:
            attrs["gps_accuracy"] = loc.accuracy
        if loc.timestamp is not None:
            attrs["last_updated"] = loc.timestamp
        return attrs

    @property
    def available(self) -> bool:
        """Available when coordinator is healthy and GPS has a valid fix."""
        if not super().available:
            return False
        if self.coordinator.data is None or self.coordinator.data.location is None:
            return False
        return self.coordinator.data.location.has_valid_fix
