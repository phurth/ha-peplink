"""Peplink Router DataUpdateCoordinator.

Single coordinator with multi-cadence polling via timestamp dispatch.
Mirrors PeplinkPollingManager.kt + PeplinkPlugin.kt poll logic.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PeplinkApiClient, PeplinkAuthError, PeplinkConnectionError, PeplinkApiError
from .const import (
    CONF_AUTH_MODE,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_DIAG_INTERVAL,
    CONF_ENABLE_GPS,
    CONF_ENABLE_VPN,
    CONF_GPS_INTERVAL,
    CONF_INSTANCE_NAME,
    CONF_PASSWORD,
    CONF_STATUS_INTERVAL,
    CONF_USAGE_INTERVAL,
    CONF_USERNAME,
    CONF_VPN_INTERVAL,
    DEFAULT_DIAG_INTERVAL,
    DEFAULT_GPS_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_USAGE_INTERVAL,
    DEFAULT_VPN_INTERVAL,
    DOMAIN,
    MAX_SIM_SLOTS,
    WAN_TYPE_CELLULAR,
)
from .models import (
    PeplinkData,
    VpnProfile,
    WanConnection,
)

_LOGGER = logging.getLogger(__name__)


class PeplinkCoordinator(DataUpdateCoordinator[PeplinkData]):
    """Coordinator for all Peplink polling.

    Update interval = status_interval (default 10s, fastest cadence).
    Slower polls (usage/diag/vpn/gps) piggyback via timestamp dispatch.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        opts = {**entry.data, **entry.options}

        status_interval = int(opts.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL))

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=status_interval),
        )

        self.api = PeplinkApiClient(
            base_url=opts[CONF_BASE_URL],
            auth_mode=opts[CONF_AUTH_MODE],
            username=opts.get(CONF_USERNAME, ""),
            password=opts.get(CONF_PASSWORD, ""),
            client_id=opts.get(CONF_CLIENT_ID, ""),
            client_secret=opts.get(CONF_CLIENT_SECRET, ""),
        )

        # --- Polling intervals ---
        self._usage_interval = int(opts.get(CONF_USAGE_INTERVAL, DEFAULT_USAGE_INTERVAL))
        self._diag_interval = int(opts.get(CONF_DIAG_INTERVAL, DEFAULT_DIAG_INTERVAL))
        self._enable_vpn = bool(opts.get(CONF_ENABLE_VPN, False))
        self._vpn_interval = int(opts.get(CONF_VPN_INTERVAL, DEFAULT_VPN_INTERVAL))
        self._enable_gps = bool(opts.get(CONF_ENABLE_GPS, False))
        self._gps_interval = int(opts.get(CONF_GPS_INTERVAL, DEFAULT_GPS_INTERVAL))

        # --- Multi-cadence timestamps (time.monotonic()) ---
        self._last_usage_poll: float = 0.0
        self._last_diag_poll: float = 0.0
        self._last_vpn_poll: float = 0.0
        self._last_gps_poll: float = 0.0

        # --- Cached slow data (survives between fast polls) ---
        self._wan_usage: dict = {}
        self._diagnostics = None
        self._device_info = None
        self._firmware_version: str | None = None
        self._connected_devices: int | None = None
        self._traffic_stats: dict[int, tuple[float, float]] = {}
        self._vpn_profiles: dict[str, VpnProfile] = {}
        self._location = None

        # --- Health state ---
        self._api_connected: bool = True
        self._authenticated: bool = True
        self._data_healthy: bool = True

        # --- Discovered hardware (set by async_discover_hardware) ---
        # wan_connections: which WANs exist and their types (stable after discovery)
        self.wan_connections: dict[int, WanConnection] = {}
        # vpn_profiles from discovery (used to create entities at setup)
        self.vpn_profiles_at_discovery: dict[str, VpnProfile] = {}

    # ===== HARDWARE DISCOVERY =====

    async def async_discover_hardware(self) -> None:
        """Query WAN IDs 1-10, keep responding ones. Mark cellular sim_slot_count=5.

        Port of PeplinkDiscovery.discoverHardware().
        Must be called during async_setup_entry before platforms are set up.
        Raises ConfigEntryNotReady on failure.
        """
        _LOGGER.info("Starting Peplink hardware discovery")
        try:
            wan_connections = await self.api.get_wan_status("1 2 3 4 5 6 7 8 9 10")
        except (PeplinkAuthError, PeplinkConnectionError, PeplinkApiError) as err:
            raise ConfigEntryNotReady(f"Hardware discovery failed: {err}") from err

        # Mark all cellular connections with 5 SIM slots (per enrichCellularWithSimSlots)
        for conn_id, conn in wan_connections.items():
            if conn.wan_type == WAN_TYPE_CELLULAR:
                wan_connections[conn_id] = dataclasses.replace(conn, sim_slot_count=MAX_SIM_SLOTS)

        self.wan_connections = wan_connections
        _LOGGER.info(
            "Peplink hardware discovery complete: %d WAN connections found",
            len(wan_connections),
        )
        for conn_id, conn in sorted(wan_connections.items()):
            _LOGGER.info(
                "  WAN %d: %s (%s, enabled=%s, sim_slots=%d)",
                conn_id, conn.name, conn.wan_type, conn.enabled, conn.sim_slot_count,
            )

        # If VPN is enabled, also discover profiles now so entity platform can create entities
        if self._enable_vpn:
            try:
                raw = await self.api.get_pep_vpn_profiles()
                self.vpn_profiles_at_discovery = {
                    pid: VpnProfile(
                        profile_id=pid,
                        name=triple[0],
                        vpn_type=triple[1],
                        status=triple[2],
                    )
                    for pid, triple in raw.items()
                }
                _LOGGER.info(
                    "VPN profiles discovered: %d", len(self.vpn_profiles_at_discovery)
                )
            except (PeplinkAuthError, PeplinkConnectionError, PeplinkApiError) as err:
                _LOGGER.warning("VPN profile discovery failed (non-fatal): %s", err)

    # ===== COORDINATOR UPDATE =====

    async def _async_update_data(self) -> PeplinkData:
        """Fetch latest data. Called every status_interval seconds by coordinator.

        Multi-cadence dispatch: slower polls run only when their interval has elapsed.
        Port of the polling dispatch in PeplinkPlugin.kt + PeplinkPollingManager.kt.
        """
        now = time.monotonic()

        # --- Always: status poll (bandwidth comes from diag poll below) ---
        try:
            new_wan = await self.api.get_wan_status("1 2 3 4 5 6 7 8 9 10")
            self._api_connected = True
            self._authenticated = self.api.is_authenticated()
            self._data_healthy = len(new_wan) > 0
        except PeplinkAuthError as err:
            self._api_connected = True
            self._authenticated = False
            self._data_healthy = False
            raise UpdateFailed(f"Authentication error: {err}") from err
        except (PeplinkConnectionError, PeplinkApiError) as err:
            self._api_connected = False
            self._authenticated = False
            self._data_healthy = False
            raise UpdateFailed(f"Status poll failed: {err}") from err

        # Enrich with sim_slot_count from discovered hardware config
        for conn_id, conn in new_wan.items():
            discovered = self.wan_connections.get(conn_id)
            if discovered and discovered.sim_slot_count > conn.sim_slot_count:
                new_wan[conn_id] = dataclasses.replace(conn, sim_slot_count=discovered.sim_slot_count)

        # Merge in latest traffic stats (from previous diag poll)
        for conn_id, (dl, ul) in self._traffic_stats.items():
            if conn_id in new_wan:
                new_wan[conn_id] = dataclasses.replace(
                    new_wan[conn_id],
                    download_rate_mbps=dl,
                    upload_rate_mbps=ul,
                )

        # --- Conditionally: usage poll ---
        if now - self._last_usage_poll >= self._usage_interval:
            await self._poll_usage()
            self._last_usage_poll = now

        # --- Conditionally: diagnostics poll (includes traffic stats) ---
        if now - self._last_diag_poll >= self._diag_interval:
            await self._poll_diagnostics()
            self._last_diag_poll = now
            # Re-merge fresh traffic stats into the wan data just polled
            for conn_id, (dl, ul) in self._traffic_stats.items():
                if conn_id in new_wan:
                    new_wan[conn_id] = dataclasses.replace(
                        new_wan[conn_id],
                        download_rate_mbps=dl,
                        upload_rate_mbps=ul,
                    )

        # --- Conditionally: VPN poll ---
        if self._enable_vpn and now - self._last_vpn_poll >= self._vpn_interval:
            await self._poll_vpn()
            self._last_vpn_poll = now

        # --- Conditionally: GPS poll ---
        if self._enable_gps and now - self._last_gps_poll >= self._gps_interval:
            await self._poll_gps()
            self._last_gps_poll = now

        return PeplinkData(
            wan_connections=new_wan,
            wan_usage=self._wan_usage,
            diagnostics=self._diagnostics,
            device_info=self._device_info,
            firmware_version=self._firmware_version,
            connected_devices=self._connected_devices,
            traffic_stats=self._traffic_stats,
            vpn_profiles=self._vpn_profiles,
            location=self._location,
            api_connected=self._api_connected,
            authenticated=self._authenticated,
            data_healthy=self._data_healthy,
        )

    # ===== SLOW POLLS =====

    async def _poll_usage(self) -> None:
        """Usage poll — WAN allowance/billing cycle data."""
        try:
            self._wan_usage = await self.api.get_wan_usage()
            _LOGGER.debug("Usage poll successful (%d connections)", len(self._wan_usage))
        except (PeplinkAuthError, PeplinkConnectionError, PeplinkApiError) as err:
            _LOGGER.warning("Usage poll failed (non-fatal): %s", err)

    async def _poll_diagnostics(self) -> None:
        """Diagnostics poll — temperature, fans, device info, connected clients, bandwidth."""
        # System diagnostics (temperature + fans)
        try:
            self._diagnostics = await self.api.get_system_diagnostics()
        except Exception as err:
            _LOGGER.warning("System diagnostics poll failed: %s", err)

        # Device info (serial, model, hw version)
        try:
            self._device_info = await self.api.get_device_info()
        except Exception as err:
            _LOGGER.warning("Device info poll failed: %s", err)

        # Firmware version (fetched once and cached from diag poll)
        if self._firmware_version is None:
            try:
                self._firmware_version = await self.api.get_firmware_version()
            except Exception as err:
                _LOGGER.warning("Firmware version poll failed: %s", err)

        # Connected client count
        try:
            self._connected_devices = await self.api.get_connected_devices_count()
        except Exception as err:
            _LOGGER.warning("Connected devices poll failed: %s", err)

        # Traffic / bandwidth stats (per WAN, in Mbps)
        try:
            self._traffic_stats = await self.api.get_traffic_stats()
        except Exception as err:
            _LOGGER.warning("Traffic stats poll failed: %s", err)

        _LOGGER.debug("Diagnostics poll successful")

    async def _poll_vpn(self) -> None:
        """VPN poll — PepVPN profile statuses."""
        try:
            raw = await self.api.get_pep_vpn_profiles()
            self._vpn_profiles = {
                pid: VpnProfile(
                    profile_id=pid,
                    name=triple[0],
                    vpn_type=triple[1],
                    status=triple[2],
                )
                for pid, triple in raw.items()
            }
            _LOGGER.debug("VPN poll successful (%d profiles)", len(self._vpn_profiles))
        except (PeplinkAuthError, PeplinkConnectionError, PeplinkApiError) as err:
            _LOGGER.warning("VPN poll failed (non-fatal): %s", err)

    async def _poll_gps(self) -> None:
        """GPS poll — location data (privacy-sensitive, opt-in only)."""
        try:
            self._location = await self.api.get_location()
            _LOGGER.debug(
                "GPS poll successful - fix=%s",
                self._location.has_valid_fix if self._location else False,
            )
        except Exception as err:
            _LOGGER.warning("GPS poll failed: %s", err)
