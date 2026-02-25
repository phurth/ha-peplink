"""Button platform for Peplink Router.

Entity names match the Android plugin MQTT discovery names exactly.
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, WAN_TYPE_CELLULAR
from .coordinator import PeplinkCoordinator
from .entity import PeplinkEntity, PeplinkWanEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    coordinator: PeplinkCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []

    # Per-WAN: cellular modem reset
    for conn_id, wan in coordinator.wan_connections.items():
        if wan.wan_type == WAN_TYPE_CELLULAR:
            entities.append(ResetModemButton(coordinator, entry, conn_id))

    # Global: rediscover hardware
    entities.append(RediscoverButton(coordinator, entry))

    async_add_entities(entities)


class ResetModemButton(PeplinkWanEntity, ButtonEntity):
    """Reset a cellular modem. Name: '{wan.name} Reset Modem'.

    No coordinator refresh after press — modem restart takes time and the
    next scheduled poll will reflect the new state.
    """

    _attr_icon = "mdi:restart"

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_reset_modem"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Reset Modem" if wan else f"WAN {self._conn_id} Reset Modem"

    async def async_press(self) -> None:
        """Send modem reset command to router. Port of handleCommand(reset) in PeplinkPlugin.kt."""
        try:
            await self.coordinator.api.reset_cellular_modem(self._conn_id)
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to reset modem for WAN {self._conn_id}: {err}"
            ) from err
        _LOGGER.info("Modem reset initiated for WAN %d", self._conn_id)
        # No immediate refresh — modem restart takes time


class RediscoverButton(PeplinkEntity, ButtonEntity):
    """Re-run hardware discovery and reload the config entry.

    Name: 'Rediscover Hardware'.
    Port of discoverHardware() triggered manually via PeplinkDiscovery in the plugin.
    Reload is required so that new/removed WAN entities are added/removed.
    """

    _attr_name = "Rediscover Hardware"
    _attr_icon = "mdi:router-wireless-settings"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_rediscover_hardware"

    async def async_press(self) -> None:
        """Re-discover WAN hardware then reload the config entry."""
        try:
            await self.coordinator.async_discover_hardware()
        except Exception as err:
            raise HomeAssistantError(f"Hardware rediscovery failed: {err}") from err
        # Reload entry so entity platform re-runs async_setup_entry with new wan_connections
        await self.hass.config_entries.async_reload(self._entry.entry_id)
