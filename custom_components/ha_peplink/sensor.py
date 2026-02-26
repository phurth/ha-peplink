"""Sensor platform for Peplink Router.

Entity names match the Android plugin MQTT discovery names exactly.
With has_entity_name=True and the device named "Peplink Router ({instance_name})",
HA generates entity_ids like:
  sensor.peplink_router_rockwood_peplink_cellular_2_priority
"""
from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfDataRate, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ENABLE_GPS,
    CONF_ENABLE_VPN,
    CONF_INSTANCE_NAME,
    DOMAIN,
    SIM_SLOT_NAMES,
    WAN_TYPE_CELLULAR,
)
from .coordinator import PeplinkCoordinator
from .entity import PeplinkEntity, PeplinkWanEntity
from .models import WanConnection, WanUsage

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from discovered WAN connections."""
    coordinator: PeplinkCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for conn_id, wan in coordinator.wan_connections.items():
        # Per-WAN sensors (all connection types)
        entities += [
            WanStatusSensor(coordinator, entry, conn_id),
            WanPrioritySensor(coordinator, entry, conn_id),
            WanIpSensor(coordinator, entry, conn_id),
            WanUptimeSensor(coordinator, entry, conn_id),
            WanStatusLedSensor(coordinator, entry, conn_id),
            WanDownloadRateSensor(coordinator, entry, conn_id),
            WanUploadRateSensor(coordinator, entry, conn_id),
        ]

        # Usage sensor — one per WAN for non-multi-SIM, or per-SIM for cellular multi-SIM
        if wan.sim_slot_count > 1:
            for slot_id in range(1, wan.sim_slot_count + 1):
                entities.append(SimUsageSensor(coordinator, entry, conn_id, slot_id))
        else:
            entities.append(WanUsageSensor(coordinator, entry, conn_id))

        # Cellular-specific sensors
        if wan.wan_type == WAN_TYPE_CELLULAR:
            entities += [
                WanSignalSensor(coordinator, entry, conn_id),
                WanSignalDbmSensor(coordinator, entry, conn_id),
                WanCarrierSensor(coordinator, entry, conn_id),
                WanNetworkSensor(coordinator, entry, conn_id),
                WanBandsSensor(coordinator, entry, conn_id),
            ]

    # Global diagnostic sensors
    entities += [
        FirmwareVersionSensor(coordinator, entry),
        ConnectedDevicesSensor(coordinator, entry),
        SystemTemperatureSensor(coordinator, entry),
        TemperatureThresholdSensor(coordinator, entry),
        SerialNumberSensor(coordinator, entry),
        ModelSensor(coordinator, entry),
    ]
    for fan_id in range(1, 4):  # Fans 1-3 (match plugin: "Add up to 3 fans")
        entities += [
            FanSpeedSensor(coordinator, entry, fan_id),
            FanStatusSensor(coordinator, entry, fan_id),
        ]

    # VPN sensors (when enabled; profiles discovered at setup)
    if entry.options.get(CONF_ENABLE_VPN, False):
        for profile in coordinator.vpn_profiles_at_discovery.values():
            entities.append(VpnStatusSensor(coordinator, entry, profile.profile_id, profile.name))

    # GPS sensors (when enabled)
    if entry.options.get(CONF_ENABLE_GPS, False):
        entities += [
            GpsSpeedSensor(coordinator, entry),
            GpsAltitudeSensor(coordinator, entry),
            GpsHeadingSensor(coordinator, entry),
            GpsCoordinatesSensor(coordinator, entry),
        ]

    async_add_entities(entities)


# ===== HELPERS =====

def _format_uptime(seconds: int | None) -> str | None:
    """Format uptime seconds as D:HH:MM. Port of formatUptime() in PeplinkPlugin.kt."""
    if seconds is None:
        return None
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}:{hours:02d}:{minutes:02d}"


def _format_signal(signal: int | None) -> str | None:
    """Format signal as 'X/5' (level) or 'X dBm' (raw). Port of publishWanState() logic."""
    if signal is None:
        return None
    if 1 <= signal <= 5:
        return f"{signal}/5"
    return f"{signal} dBm"


def _resolve_signal_dbm(cellular) -> int | None:
    """Choose the best dBm value. Port of signal_dbm logic in PeplinkPlugin.kt."""
    s = cellular.signal_strength
    if s is not None and not (1 <= s <= 5):
        return s   # Raw dBm stored in signal_strength
    return cellular.rsrp_dbm or cellular.rssi_dbm


def _parse_carrier_name(carrier: str | None) -> str:
    """Parse carrier JSON if present, else return raw string. Port of parseCarrierName()."""
    if not carrier:
        return ""
    try:
        import json
        obj = json.loads(carrier)
        if isinstance(obj, dict):
            return obj.get("name", carrier)
    except (ValueError, TypeError):
        pass
    return carrier


def _format_usage_gb(mb: int | None) -> str:
    """Format MB value as '0.00 GB'. Port of formatUsageGb()."""
    if mb is None:
        return "0 GB"
    return f"{mb / 1024:.2f} GB"


def _compute_usage_percent(usage_mb: int | None, limit_mb: int | None) -> int | None:
    """Compute usage % from MB values. Port of computeUsagePercent()."""
    if usage_mb is None or limit_mb is None or limit_mb <= 0:
        return None
    return round((usage_mb / limit_mb) * 100)


def _format_ordinal(day: int | None) -> str | None:
    """Format day as ordinal ('1st', '2nd', etc.). Port of formatOrdinalDay()."""
    if day is None:
        return None
    if 11 <= day <= 13:
        return f"{day}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _parse_start_day(start: str | None) -> int | None:
    """Parse billing start date to day number. Port of parseStartDay()."""
    if not start:
        return None
    try:
        day = int(start)
        return day if 1 <= day <= 31 else None
    except (ValueError, TypeError):
        return None


def _usage_attributes(usage: WanUsage | None) -> dict[str, Any]:
    """Build extra_state_attributes for a usage sensor. Port of MQTT usage_attributes JSON."""
    if usage is None:
        return {}
    percent = usage.percent or _compute_usage_percent(usage.usage_mb, usage.limit_mb) or 0
    start_ordinal = _format_ordinal(_parse_start_day(usage.start_date)) or "unknown"
    return {
        "usage": _format_usage_gb(usage.usage_mb),
        "allowance": _format_usage_gb(usage.limit_mb) if usage.limit_mb else "unlimited",
        "percent_used": percent,
        "start_day": start_ordinal,
    }


# ===== PER-WAN SENSORS =====

class WanStatusSensor(PeplinkWanEntity, SensorEntity):
    """Raw WAN status message. Name: '{wan.name} Status'."""

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_status"
        self._attr_icon = "mdi:lan"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Status" if wan else f"WAN {self._conn_id} Status"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        if wan is None:
            return None
        return wan.message or ("connected" if wan.enabled else "disabled")


class WanPrioritySensor(PeplinkWanEntity, SensorEntity):
    """WAN priority level (1-4) or empty string if disabled. Name: '{wan.name} Priority'."""

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_priority"
        self._attr_icon = "mdi:sort-numeric-ascending"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Priority" if wan else f"WAN {self._conn_id} Priority"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        if wan is None:
            return None
        return str(wan.priority) if wan.priority is not None else ""


class WanIpSensor(PeplinkWanEntity, SensorEntity):
    """WAN assigned IP address. Name: '{wan.name} IP'."""

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_ip"
        self._attr_icon = "mdi:ip-network"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} IP" if wan else f"WAN {self._conn_id} IP"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        return wan.ip if wan else None


class WanUptimeSensor(PeplinkWanEntity, SensorEntity):
    """WAN connection uptime formatted as D:HH:MM. Name: '{wan.name} Uptime'."""

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_uptime"
        self._attr_icon = "mdi:clock-outline"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Uptime" if wan else f"WAN {self._conn_id} Uptime"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        if wan is None:
            return None
        return _format_uptime(wan.uptime) or "0:00:00"


class WanStatusLedSensor(PeplinkWanEntity, SensorEntity):
    """WAN LED status indicator color. Name: '{wan.name} Status LED'."""

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_status_led"
        self._attr_icon = "mdi:led-outline"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Status LED" if wan else f"WAN {self._conn_id} Status LED"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        return wan.status_led if wan else None


class WanDownloadRateSensor(PeplinkWanEntity, SensorEntity):
    """WAN download bandwidth in Mbit/s. Name: '{wan.name} Download Rate'."""

    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_download_rate"
        self._attr_icon = "mdi:download"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Download Rate" if wan else f"WAN {self._conn_id} Download Rate"

    @property
    def native_value(self) -> float | None:
        wan = self._wan
        return round(wan.download_rate_mbps, 1) if wan and wan.download_rate_mbps is not None else None


class WanUploadRateSensor(PeplinkWanEntity, SensorEntity):
    """WAN upload bandwidth in Mbit/s. Name: '{wan.name} Upload Rate'."""

    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_upload_rate"
        self._attr_icon = "mdi:upload"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Upload Rate" if wan else f"WAN {self._conn_id} Upload Rate"

    @property
    def native_value(self) -> float | None:
        wan = self._wan
        return round(wan.upload_rate_mbps, 1) if wan and wan.upload_rate_mbps is not None else None


class WanUsageSensor(PeplinkWanEntity, SensorEntity):
    """WAN usage sensor for single-SIM / non-cellular WANs.

    Name: '{wan.name}' (just the WAN name — matches plugin where name=connection.name).
    State: 'Enabled' or 'Disabled'. Attributes carry usage/allowance/percent/start_day.
    """

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_usage"
        self._attr_icon = "mdi:gauge"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return wan.name if wan else f"WAN {self._conn_id}"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        usage = self.coordinator.data.wan_usage.get(self._conn_id)
        if usage is None:
            return None
        return "Enabled" if usage.enabled else "Disabled"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        usage = self.coordinator.data.wan_usage.get(self._conn_id)
        return _usage_attributes(usage)


class SimUsageSensor(PeplinkWanEntity, SensorEntity):
    """Per-SIM usage sensor for multi-SIM cellular connections.

    Name: '{wan.name} {slot_name}' (e.g. 'Cellular 2 SIM A').
    State: 'Enabled' or 'Disabled'. Attributes carry usage/allowance/percent/start_day.
    """

    def __init__(self, coordinator, entry, conn_id, slot_id: int):
        super().__init__(coordinator, entry, conn_id)
        self._slot_id = slot_id
        slot_name = SIM_SLOT_NAMES.get(slot_id, f"SIM {slot_id}")
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_sim{slot_id}_usage"
        self._attr_icon = "mdi:sim"
        self._slot_name = slot_name

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        base = wan.name if wan else f"WAN {self._conn_id}"
        return f"{base} {self._slot_name}"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        usage = self.coordinator.data.wan_usage.get(self._conn_id)
        if usage is None or usage.sim_slots is None:
            return "Disabled"
        slot = usage.sim_slots.get(self._slot_id)
        if slot is None:
            return "Disabled"
        return "Enabled" if slot.enabled else "Disabled"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        usage = self.coordinator.data.wan_usage.get(self._conn_id)
        if usage is None or usage.sim_slots is None:
            return {}
        slot = usage.sim_slots.get(self._slot_id)
        if slot is None or not slot.enabled or not slot.has_usage_tracking:
            return {}
        percent = slot.percent or _compute_usage_percent(slot.usage_mb, slot.limit_mb) or 0
        start = _format_ordinal(_parse_start_day(slot.start_date)) or "unknown"
        return {
            "usage": _format_usage_gb(slot.usage_mb),
            "allowance": _format_usage_gb(slot.limit_mb) if slot.limit_mb else "unlimited",
            "percent_used": percent,
            "start_day": start,
        }


# ===== CELLULAR-SPECIFIC SENSORS =====

class WanSignalSensor(PeplinkWanEntity, SensorEntity):
    """Formatted signal ('X/5' or 'X dBm'). Name: '{wan.name} Signal'."""

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_signal"
        self._attr_icon = "mdi:signal-cellular-3"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Signal" if wan else f"WAN {self._conn_id} Signal"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        if wan is None or wan.cellular is None:
            return None
        return _format_signal(wan.cellular.signal_strength)


class WanSignalDbmSensor(PeplinkWanEntity, SensorEntity):
    """Raw signal in dBm. Name: '{wan.name} Signal dBm'."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_signal_dbm"
        self._attr_icon = "mdi:signal"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Signal dBm" if wan else f"WAN {self._conn_id} Signal dBm"

    @property
    def native_value(self) -> int | None:
        wan = self._wan
        if wan is None or wan.cellular is None:
            return None
        return _resolve_signal_dbm(wan.cellular)


class WanCarrierSensor(PeplinkWanEntity, SensorEntity):
    """Cellular carrier name. Name: '{wan.name} Carrier'."""

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_carrier"
        self._attr_icon = "mdi:sim"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Carrier" if wan else f"WAN {self._conn_id} Carrier"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        if wan is None or wan.cellular is None:
            return None
        return _parse_carrier_name(wan.cellular.carrier) or None


class WanNetworkSensor(PeplinkWanEntity, SensorEntity):
    """Network type (LTE, 5G, etc.). Name: '{wan.name} Network'."""

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_network"
        self._attr_icon = "mdi:network"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Network" if wan else f"WAN {self._conn_id} Network"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        if wan is None or wan.cellular is None:
            return None
        return wan.cellular.network_type or None


class WanBandsSensor(PeplinkWanEntity, SensorEntity):
    """Active cellular bands joined as comma-separated string. Name: '{wan.name} Bands'."""

    def __init__(self, coordinator, entry, conn_id):
        super().__init__(coordinator, entry, conn_id)
        self._attr_unique_id = f"{entry.entry_id}_wan{conn_id}_bands"
        self._attr_icon = "mdi:radio-tower"

    @property
    def name(self) -> str:
        wan = self.coordinator.wan_connections.get(self._conn_id)
        return f"{wan.name} Bands" if wan else f"WAN {self._conn_id} Bands"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        if wan is None or wan.cellular is None:
            return None
        bands = wan.cellular.bands
        return ", ".join(bands) if bands else "unavailable"


# ===== GLOBAL DIAGNOSTIC SENSORS =====

class FirmwareVersionSensor(PeplinkEntity, SensorEntity):
    """Router firmware version. Name: 'Router Firmware Version'."""

    _attr_name = "Router Firmware Version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:update"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_firmware_version"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.firmware_version


class ConnectedDevicesSensor(PeplinkEntity, SensorEntity):
    """Number of connected client devices. Name: 'Connected Devices'."""

    _attr_name = "Connected Devices"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:devices"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_connected_devices"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.connected_devices


class SystemTemperatureSensor(PeplinkEntity, SensorEntity):
    """System temperature in °C. Name: 'System Temperature'."""

    _attr_name = "System Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_system_temperature"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None or self.coordinator.data.diagnostics is None:
            return None
        t = self.coordinator.data.diagnostics.temperature
        return round(t, 1) if t is not None else None


class TemperatureThresholdSensor(PeplinkEntity, SensorEntity):
    """Temperature alarm threshold in °C. Name: 'Temperature Threshold'."""

    _attr_name = "Temperature Threshold"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:alert-thermometer"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_temperature_threshold"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None or self.coordinator.data.diagnostics is None:
            return None
        t = self.coordinator.data.diagnostics.temperature_threshold
        return round(t, 0) if t is not None else None


class FanSpeedSensor(PeplinkEntity, SensorEntity):
    """Fan speed in RPM. Name: 'Fan N Speed'."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = "rpm"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, fan_id: int):
        super().__init__(coordinator, entry)
        self._fan_id = fan_id
        self._attr_unique_id = f"{entry.entry_id}_fan_{fan_id}_speed"
        self._attr_name = f"Fan {fan_id} Speed"
        self._attr_icon = "mdi:fan"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None or self.coordinator.data.diagnostics is None:
            return None
        for fan in self.coordinator.data.diagnostics.fans:
            if fan.fan_id == self._fan_id:
                return fan.speed_rpm
        return None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self.coordinator.data is None or self.coordinator.data.diagnostics is None:
            return False
        return any(
            f.fan_id == self._fan_id
            for f in self.coordinator.data.diagnostics.fans
        )


class FanStatusSensor(PeplinkEntity, SensorEntity):
    """Fan status string. Name: 'Fan N Status'."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry, fan_id: int):
        super().__init__(coordinator, entry)
        self._fan_id = fan_id
        self._attr_unique_id = f"{entry.entry_id}_fan_{fan_id}_status"
        self._attr_name = f"Fan {fan_id} Status"
        self._attr_icon = "mdi:fan-alert"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None or self.coordinator.data.diagnostics is None:
            return None
        for fan in self.coordinator.data.diagnostics.fans:
            if fan.fan_id == self._fan_id:
                return fan.status
        return None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self.coordinator.data is None or self.coordinator.data.diagnostics is None:
            return False
        return any(
            f.fan_id == self._fan_id
            for f in self.coordinator.data.diagnostics.fans
        )


class SerialNumberSensor(PeplinkEntity, SensorEntity):
    """Router serial number. Name: 'Serial Number'."""

    _attr_name = "Serial Number"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:numeric"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_serial_number"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None or self.coordinator.data.device_info is None:
            return None
        sn = self.coordinator.data.device_info.serial_number
        return sn if sn and sn != "unknown" else None


class ModelSensor(PeplinkEntity, SensorEntity):
    """Router model string. Name: 'Model'."""

    _attr_name = "Model"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:router-wireless"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_model"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None or self.coordinator.data.device_info is None:
            return None
        m = self.coordinator.data.device_info.model
        return m if m and m != "unknown" else None


# ===== VPN SENSORS =====

class VpnStatusSensor(PeplinkEntity, SensorEntity):
    """PepVPN profile status. Name: 'VPN: {profile.name}'."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:vpn"

    def __init__(self, coordinator, entry, profile_id: str, profile_name: str):
        super().__init__(coordinator, entry)
        self._profile_id = profile_id
        self._attr_unique_id = f"{entry.entry_id}_vpn_{profile_id}"
        self._attr_name = f"VPN: {profile_name}"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        profile = self.coordinator.data.vpn_profiles.get(self._profile_id)
        return profile.status if profile else None


# ===== GPS SENSORS =====

class GpsSpeedSensor(PeplinkEntity, SensorEntity):
    """GPS speed in m/s. Name: 'GPS Speed'."""

    _attr_name = "GPS Speed"
    _attr_device_class = SensorDeviceClass.SPEED
    _attr_native_unit_of_measurement = UnitOfSpeed.METERS_PER_SECOND
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_gps_speed"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None or self.coordinator.data.location is None:
            return None
        return self.coordinator.data.location.speed


class GpsAltitudeSensor(PeplinkEntity, SensorEntity):
    """GPS altitude in metres. Name: 'GPS Altitude'."""

    _attr_name = "GPS Altitude"
    _attr_native_unit_of_measurement = "m"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:altimeter"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_gps_altitude"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None or self.coordinator.data.location is None:
            return None
        return self.coordinator.data.location.altitude


class GpsHeadingSensor(PeplinkEntity, SensorEntity):
    """GPS heading in degrees. Name: 'GPS Heading'."""

    _attr_name = "GPS Heading"
    _attr_native_unit_of_measurement = "°"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:compass"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_gps_heading"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None or self.coordinator.data.location is None:
            return None
        return self.coordinator.data.location.heading


class GpsCoordinatesSensor(PeplinkEntity, SensorEntity):
    """GPS coordinates as readable 'lat, lon' string. Name: 'GPS Coordinates'."""

    _attr_name = "GPS Coordinates"
    _attr_icon = "mdi:map-marker"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_gps_coordinates"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None or self.coordinator.data.location is None:
            return None
        loc = self.coordinator.data.location
        if not loc.has_valid_fix:
            return None
        return f"{loc.latitude:.6f}, {loc.longitude:.6f}"
