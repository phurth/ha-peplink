"""Data models for Peplink Router integration.

Direct Python port of PeplinkModels.kt — all fields nullable where
the Kotlin source uses null/?.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ===== WAN CONNECTION =====

@dataclass
class CellularInfo:
    """Cellular modem information from WAN status API."""
    module_name: str
    signal_strength: int | None       # dBm or 1-5 level depending on API field
    signal_quality: int | None
    carrier: str | None               # May be JSON string {"name": "..."}
    network_type: str | None          # "LTE", "5G", etc.
    band: str | None                  # Legacy single-band field
    carrier_aggregation: bool
    bands: list[str]                  # Active band names from rat[].band[]
    rssi_dbm: int | None              # Raw RSSI from first active band
    rsrp_dbm: int | None              # Raw RSRP from first active band


@dataclass
class WifiInfo:
    """WiFi WAN information."""
    ssid: str | None
    frequency: str | None             # "2.4GHz" or "5GHz"
    signal_strength: int | None       # dBm
    channel: int | None


@dataclass
class SimSlotInfo:
    """Per-SIM slot usage information."""
    slot_id: int
    enabled: bool
    has_usage_tracking: bool
    usage_mb: int | None
    limit_mb: int | None
    percent: int | None
    start_date: str | None            # Billing cycle start (day-of-month string)


@dataclass
class WanConnection:
    """WAN connection status from /api/status.wan.connection."""
    conn_id: int
    name: str                         # Friendly name from API
    wan_type: str                     # WAN_TYPE_* constant
    enabled: bool
    message: str | None               # Raw status message ("Connected", "Disconnected", etc.)
    priority: int | None              # 1–4, or None if disabled
    uptime: int | None                # Seconds
    ip: str | None
    status_led: str | None
    cellular: CellularInfo | None = None
    wifi: WifiInfo | None = None
    sim_slot_count: int = 0           # Set by discovery (always 5 for cellular)
    download_rate_mbps: float | None = None   # From traffic stats API (diag poll)
    upload_rate_mbps: float | None = None


@dataclass
class WanUsage:
    """WAN usage/allowance from /api/status.wan.connection.allowance."""
    conn_id: int
    enabled: bool
    usage_mb: int | None
    limit_mb: int | None
    percent: int | None
    unit: str | None
    start_date: str | None
    sim_slots: dict[int, SimSlotInfo] | None = None   # Populated for multi-SIM cellular


# ===== DIAGNOSTICS =====

@dataclass
class FanInfo:
    """Fan speed information from system diagnostics."""
    fan_id: int
    name: str
    speed_rpm: int | None = None
    speed_percent: int | None = None
    status: str = "normal"            # "normal", "warning", "critical", "off"


@dataclass
class SystemDiagnostics:
    """System diagnostics from /cgi-bin/MANGA/api.cgi."""
    temperature: float | None
    temperature_threshold: float | None
    fans: list[FanInfo] = field(default_factory=list)


@dataclass
class DeviceInfo:
    """Device hardware information."""
    serial_number: str
    model: str
    hardware_version: str | None = None


# ===== VPN =====

@dataclass
class VpnProfile:
    """PepVPN profile status from /api/status.pepvpn."""
    profile_id: str
    name: str
    vpn_type: str
    status: str


# ===== GPS =====

@dataclass
class LocationInfo:
    """GPS location from /api/info.location."""
    latitude: float | None
    longitude: float | None
    altitude: float | None            # Metres above sea level
    speed: float | None               # m/s
    heading: float | None             # Degrees 0-360, 0=North
    accuracy: float | None            # Horizontal accuracy metres
    timestamp: int | None             # Unix timestamp seconds

    @property
    def has_valid_fix(self) -> bool:
        """True if we have a usable lat/lon fix."""
        return self.latitude is not None and self.longitude is not None


# ===== AGGREGATED COORDINATOR DATA =====

@dataclass
class PeplinkData:
    """All polled data combined by the coordinator on each cycle."""
    wan_connections: dict[int, WanConnection]           # Status poll (10s)
    wan_usage: dict[int, WanUsage]                      # Usage poll (60s)
    diagnostics: SystemDiagnostics | None               # Diag poll (30s)
    device_info: DeviceInfo | None                      # Diag poll (30s)
    firmware_version: str | None                        # Diag poll (30s)
    connected_devices: int | None                       # Diag poll (30s)
    traffic_stats: dict[int, tuple[float, float]]       # Diag poll: connId->(dl_mbps, ul_mbps)
    vpn_profiles: dict[str, VpnProfile]                 # VPN poll (60s, opt-in)
    location: LocationInfo | None                       # GPS poll (120s, opt-in)
    api_connected: bool = True
    authenticated: bool = True
    data_healthy: bool = True
