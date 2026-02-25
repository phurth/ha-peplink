"""Peplink Router integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import PeplinkCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["binary_sensor", "button", "device_tracker", "select", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Peplink Router from a config entry.

    Order:
      1. Create coordinator
      2. Discover hardware (populates wan_connections + vpn_profiles_at_discovery)
      3. First coordinator refresh (populates coordinator.data with live values)
      4. Store coordinator
      5. Set up platforms (entities read wan_connections and initial data)

    device_tracker is always forwarded; its async_setup_entry returns early when
    GPS is disabled so the platform list is stable across option changes.
    """
    coordinator = PeplinkCoordinator(hass, entry)

    # Hardware discovery — raises ConfigEntryNotReady on failure
    await coordinator.async_discover_hardware()

    # Initial data fetch — raises ConfigEntryNotReady if router unreachable
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: PeplinkCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.api.close()

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
