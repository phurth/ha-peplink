"""Base entity classes for Peplink Router integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_INSTANCE_NAME, DOMAIN
from .coordinator import PeplinkCoordinator
from .models import WanConnection


class PeplinkEntity(CoordinatorEntity[PeplinkCoordinator]):
    """Base entity. Device = one Peplink router instance."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PeplinkCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this router instance.

        Device name format matches Android plugin: 'Peplink Router ({instance_name})'.
        Model/sw_version/serial populated from diag poll once available.
        """
        instance_name = self._entry.data.get(CONF_INSTANCE_NAME, "Main")
        data = self.coordinator.data

        model = None
        sw_version = None
        serial = None
        if data is not None:
            if data.device_info is not None:
                model = data.device_info.model if data.device_info.model != "unknown" else None
                serial = data.device_info.serial_number if data.device_info.serial_number != "unknown" else None
            sw_version = data.firmware_version

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=f"Peplink Router ({instance_name})",
            manufacturer="Peplink",
            model=model,
            sw_version=sw_version,
            serial_number=serial,
        )


class PeplinkWanEntity(PeplinkEntity):
    """Base entity for per-WAN entities."""

    def __init__(
        self, coordinator: PeplinkCoordinator, entry: ConfigEntry, conn_id: int
    ) -> None:
        super().__init__(coordinator, entry)
        self._conn_id = conn_id

    @property
    def available(self) -> bool:
        """Unavailable if coordinator is unhealthy or WAN not in latest data."""
        if not super().available:
            return False
        if self.coordinator.data is None:
            return False
        return self._conn_id in self.coordinator.data.wan_connections

    @property
    def _wan(self) -> WanConnection | None:
        """Convenience accessor for the live WAN data."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.wan_connections.get(self._conn_id)
