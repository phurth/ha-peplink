"""Peplink Router DataUpdateCoordinator.

Single coordinator with multi-cadence polling via timestamp dispatch.
Mirrors PeplinkPollingManager.kt + PeplinkPlugin.kt poll logic.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PeplinkApiClient, PeplinkAuthError, PeplinkConnectionError, PeplinkApiError
from .bands import (
    AUTO,
    SIM_NAMES,
    BandTable,
    load_bundled_table,
    parse_band_info,
    resolve_bands,
    same_selection,
    sim_key,
    supported_bands,
)
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
    CONF_VERIFY_SSL,
    CONF_VPN_INTERVAL,
    DEFAULT_DIAG_INTERVAL,
    DEFAULT_GPS_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_USAGE_INTERVAL,
    DEFAULT_VPN_INTERVAL,
    DOMAIN,
    MAX_SIM_SLOTS,
    WAN_DISCOVERY_IDS,
    WAN_TYPE_CELLULAR,
)
from .models import (
    CellularBandConfig,
    PeplinkData,
    VpnProfile,
    WanConnection,
)

_LOGGER = logging.getLogger(__name__)

# A cellular link counts as up when it reports one of these and has carrier components.
_LINK_UP_STATES = ("connected", "standby")
# The modem restarts ~25-30 s after a band change is applied (BR1 Pro, fw 8.6.1) and is back
# ~15 s later. If no drop shows within this time, the link is taken as already up.
_RESTART_GRACE_S = 60
_LINK_POLL_S = 2


class BandSelectionError(Exception):
    """Invalid band-selection request (bad SIM, unsupported band, ...)."""


def _link_up(conn: WanConnection | None) -> bool:
    return bool(
        conn is not None
        and conn.cellular is not None
        and conn.cellular.components
        and (conn.message or "").strip().lower() in _LINK_UP_STATES
    )


def _selection_out(bands: list[str] | None) -> list[str] | str:
    """Band selection as returned by the actions: a list, or 'auto'."""
    return list(bands) if bands else AUTO


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
            verify_ssl=opts.get(CONF_VERIFY_SSL, True),
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
        self._sfc = None
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

        # --- Cellular band selection ---
        # Supported-band table: from the router at discovery, else the bundled copy.
        self.band_table: BandTable | None = None
        self._band_config: dict[int, CellularBandConfig] = {}
        self._band_lock = asyncio.Lock()

    @staticmethod
    def _build_discovery_id_query() -> str:
        """Build the WAN ID probe list used during discovery/polling."""
        return " ".join(str(conn_id) for conn_id in WAN_DISCOVERY_IDS)

    # ===== HARDWARE DISCOVERY =====

    async def async_discover_hardware(self) -> None:
        """Probe configured WAN IDs, keep responding ones, and enrich cellular SIM slots.

        Port of PeplinkDiscovery.discoverHardware().
        Must be called during async_setup_entry before platforms are set up.
        Raises ConfigEntryNotReady on failure.
        """
        _LOGGER.info("Starting Peplink hardware discovery")
        probe_ids = self._build_discovery_id_query()
        try:
            wan_connections = await self.api.get_wan_status(probe_ids)
        except (PeplinkAuthError, PeplinkConnectionError, PeplinkApiError) as err:
            raise ConfigEntryNotReady(f"Hardware discovery failed: {err}") from err

        # Some firmware variants expose virtual WAN IDs outside the default probe
        # range. If we found no WANs, retry once without an explicit id filter.
        if not wan_connections:
            try:
                wan_connections = await self.api.get_wan_status()
                if wan_connections:
                    _LOGGER.info(
                        "Hardware discovery fallback succeeded without ID filter (%d WANs)",
                        len(wan_connections),
                    )
            except (PeplinkAuthError, PeplinkConnectionError, PeplinkApiError):
                # Keep the original empty result and proceed to existing error path.
                pass

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

        cellular_ids = sorted(
            cid for cid, c in wan_connections.items() if c.wan_type == WAN_TYPE_CELLULAR
        )
        if cellular_ids:
            await self._async_load_band_table(cellular_ids[0])
            try:
                self._band_config = await self.api.get_cellular_band_config(cellular_ids)
            except (PeplinkAuthError, PeplinkConnectionError, PeplinkApiError) as err:
                _LOGGER.warning("Cellular band config read failed (non-fatal): %s", err)

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
            if self.wan_connections:
                conn_ids = " ".join(str(conn_id) for conn_id in sorted(self.wan_connections))
            else:
                conn_ids = self._build_discovery_id_query()
            new_wan = await self.api.get_wan_status(conn_ids)
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
            sfc=self._sfc,
            band_config=dict(self._band_config),
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

        # SpeedFusion Connect quota (web-admin vars; needs admin credentials)
        try:
            self._sfc = await self.api.get_sfc_quota()
        except Exception as err:
            _LOGGER.warning("SFC quota poll failed: %s", err)

        # Cellular band selection per SIM (token API; small response)
        cellular_ids = sorted(
            cid for cid, c in self.wan_connections.items() if c.wan_type == WAN_TYPE_CELLULAR
        )
        if cellular_ids and not self._band_lock.locked():
            try:
                self._band_config = await self.api.get_cellular_band_config(cellular_ids)
            except Exception as err:
                _LOGGER.warning("Cellular band config poll failed: %s", err)

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

    # ===== CELLULAR BAND SELECTION =====

    async def _async_load_band_table(self, conn_id: int) -> None:
        """Load the supported-band table: the router's own, else the bundled copy."""
        table: BandTable | None = None
        try:
            table = parse_band_info(await self.api.get_band_reference(conn_id))
        except (PeplinkAuthError, PeplinkConnectionError, PeplinkApiError) as err:
            _LOGGER.debug("Band reference request failed: %s", err)
        if table is None:
            table = await self.hass.async_add_executor_job(load_bundled_table)
            _LOGGER.info("Router returned no band reference table; using the bundled copy")
        else:
            _LOGGER.debug("Band reference table from router: %d modem models", len(table.capability))
        self.band_table = table

    def _live_cellular(self, conn_id: int):
        conn = self.data.wan_connections.get(conn_id) if self.data else None
        return conn.cellular if conn is not None else None

    def band_support(self, conn_id: int, sim: str) -> tuple[list[str] | None, bool]:
        """Supported bands for one SIM of one modem: (tokens or None, exact model match)."""
        cell = self._live_cellular(conn_id)
        cfg = self._band_config.get(conn_id)
        return supported_bands(
            self.band_table,
            cell.modem_model if cell else None,
            cell.network_mode if cell else None,
            cfg.mobile_types.get(sim) if cfg else None,
        )

    def band_config(self, conn_id: int) -> CellularBandConfig | None:
        return self._band_config.get(conn_id)

    def _wan_name(self, conn_id: int) -> str:
        conn = self.wan_connections.get(conn_id)
        return conn.name if conn else f"WAN {conn_id}"

    async def _async_read_band_config(self, conn_id: int) -> CellularBandConfig:
        try:
            cfg = (await self.api.get_cellular_band_config([conn_id])).get(conn_id)
        except (PeplinkAuthError, PeplinkConnectionError, PeplinkApiError) as err:
            raise BandSelectionError(f"Could not read the band settings: {err}") from err
        if cfg is None or not cfg.sims:
            raise BandSelectionError(f"{self._wan_name(conn_id)} reports no SIM band settings")
        self._band_config[conn_id] = cfg
        return cfg

    def _sim_for(self, conn_id: int, cfg: CellularBandConfig, sim: str | None) -> str:
        if sim is None or str(sim).strip() == "":
            cell = self._live_cellular(conn_id)
            key = cell.active_sim if cell and cell.active_sim else "1"
        else:
            try:
                key = sim_key(sim)
            except ValueError as err:
                raise BandSelectionError(str(err)) from err
        if key not in cfg.sims:
            available = ", ".join(SIM_NAMES.get(k, k) for k in cfg.sims)
            raise BandSelectionError(
                f"{SIM_NAMES.get(key, key)} is not available on {self._wan_name(conn_id)} "
                f"(available: {available})"
            )
        return key

    async def async_get_bands(self, conn_id: int) -> dict[str, Any]:
        """Current band selection per SIM, supported bands and live carrier data."""
        cfg = await self._async_read_band_config(conn_id)
        cell = self._live_cellular(conn_id)
        sims: dict[str, Any] = {}
        names: dict[str, str] = {}
        exact = False
        for key, bands in cfg.sims.items():
            supported, exact = self.band_support(conn_id, key)
            sims[SIM_NAMES.get(key, key)] = {
                "bands": _selection_out(bands),
                "supported": supported,
            }
            if self.band_table and supported:
                names.update({b: self.band_table.names.get(b, b) for b in supported})
        return {
            "wan": self._wan_name(conn_id),
            "conn_id": conn_id,
            "modem": cell.modem_model if cell else None,
            "active_sim": SIM_NAMES.get(cell.active_sim, cell.active_sim) if cell and cell.active_sim else None,
            "band_list_source": self.band_table.source if self.band_table else None,
            "band_list_exact": exact,
            "sims": sims,
            "band_names": names,
            "carriers": [dataclasses.asdict(c) for c in cell.components] if cell else [],
        }

    async def async_set_bands(
        self, conn_id: int, sim: str | None, bands: list[str], wait_seconds: int = 0
    ) -> dict[str, Any]:
        """Set one SIM's band selection, read it back, optionally wait for the link."""
        async with self._band_lock:
            cfg = await self._async_read_band_config(conn_id)
            key = self._sim_for(conn_id, cfg, sim)
            supported, _exact = self.band_support(conn_id, key)
            try:
                target = resolve_bands(bands, supported)
            except ValueError as err:
                raise BandSelectionError(str(err)) from err
            previous = cfg.sims.get(key)

            if same_selection(previous, target):
                # Nothing to change: no write, so no modem restart to wait for.
                return {
                    "wan": self._wan_name(conn_id),
                    "conn_id": conn_id,
                    "sim": SIM_NAMES.get(key, key),
                    "previous": _selection_out(previous),
                    "applied": _selection_out(previous),
                    "verified": True,
                    "changed": False,
                }

            _LOGGER.info(
                "%s %s: setting bands %s (was %s)",
                self._wan_name(conn_id), SIM_NAMES.get(key, key),
                _selection_out(target), _selection_out(previous),
            )
            await self.api.set_band_selection(conn_id, key, target)

            readback = (await self._async_read_band_config(conn_id)).sims.get(key)
            if not same_selection(readback, target):
                raise BandSelectionError(
                    f"The router accepted the change but reports {_selection_out(readback)} "
                    f"instead of {_selection_out(target)}"
                )

        if self.data is not None:
            self.async_set_updated_data(
                dataclasses.replace(self.data, band_config=dict(self._band_config))
            )

        result: dict[str, Any] = {
            "wan": self._wan_name(conn_id),
            "conn_id": conn_id,
            "sim": SIM_NAMES.get(key, key),
            "previous": _selection_out(previous),
            "applied": _selection_out(readback),
            "verified": True,
            "changed": True,
        }
        if wait_seconds > 0:
            result["link"] = await self._async_wait_for_link(conn_id, wait_seconds)
        return result

    async def _async_wait_for_link(self, conn_id: int, timeout: int) -> dict[str, Any]:
        """Wait for the modem to come back after a band change.

        Applying bands restarts the modem, but not at once: the link drops about
        25-30 s after the write. Waits to see it drop, then come back up (connected
        or standby with carrier data). If no drop shows within the grace period,
        the link is taken as already up.
        """
        start = time.monotonic()
        seen_down = False
        conn: WanConnection | None = None
        while True:
            try:
                conn = (await self.api.get_wan_status(str(conn_id))).get(conn_id)
            except (PeplinkAuthError, PeplinkConnectionError, PeplinkApiError):
                conn = None
            elapsed = time.monotonic() - start
            up = _link_up(conn)
            if not up:
                seen_down = True
            done = up and (seen_down or elapsed >= min(_RESTART_GRACE_S, timeout))
            if done or elapsed >= timeout:
                break
            await asyncio.sleep(_LINK_POLL_S)
        await self.async_request_refresh()
        return {
            "connected": up,
            "status": conn.message if conn else None,
            "seconds": round(time.monotonic() - start),
            "restart_seen": seen_down,
            "bands": [c.name for c in conn.cellular.components] if conn and conn.cellular else [],
        }
