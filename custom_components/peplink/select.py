"""Select platform for Peplink Router — WAN priority control.

Entity names match the Android plugin MQTT discovery names exactly.
"""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PeplinkCoordinator
from .entity import PeplinkWanEntity

_LOGGER = logging.getLogger(__name__)

PRIORITY_OPTIONS = ["1", "2", "3", "4", "Disabled"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities — one priority control per WAN."""
    coordinator: PeplinkCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        WanPrioritySelect(coordinator, entry, conn_id)
        for conn_id in coordinator.wan_connections
    ]
    async_add_entities(entities)


class WanPrioritySelect(PeplinkWanEntity, SelectEntity):
    """WAN priority selector. Name: '{wan.name} Priority Control'.

    Calls api.set_wan_priority() then requests coordinator refresh.
    Priority 1-4 or 'Disabled' (→ null/enable:false in API payload).
    """

    _attr_options = PRIORITY_OPTIONS
    _attr_icon = "mdi:priority-high"

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_priority_control"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Priority Control" if wan else f"WAN {self._conn_id} Priority Control"

    @property
    def current_option(self) -> str | None:
        """Current priority string sourced from WAN status data."""
        wan = self._wan
        if wan is None:
            return None
        if wan.priority is None:
            return "Disabled"
        p = str(wan.priority)
        return p if p in PRIORITY_OPTIONS else "Disabled"

    async def async_select_option(self, option: str) -> None:
        """Send priority change to router. Port of handleCommand(priority) in PeplinkPlugin.kt."""
        priority = None if option == "Disabled" else int(option)
        try:
            await self.coordinator.api.set_wan_priority(self._conn_id, priority)
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to set WAN {self._conn_id} priority to {option}: {err}"
            ) from err
        # Refresh coordinator so UI reflects new priority without waiting for next poll
        await self.coordinator.async_request_refresh()
