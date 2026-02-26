"""Peplink Router API client.

Python/aiohttp port of PeplinkApiClient.kt.
Preserves auth logic exactly: cookie-based (userpass) and token-based auth,
automatic re-authentication on HTTP 401 and API-level stat=fail/code=401.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import aiohttp

from .const import (
    AUTH_MODE_TOKEN,
    AUTH_MODE_USERPASS,
    TOKEN_REFRESH_SECS,
)
from .models import (
    CellularInfo,
    DeviceInfo,
    FanInfo,
    LocationInfo,
    SimSlotInfo,
    SystemDiagnostics,
    WanConnection,
    WanUsage,
    WifiInfo,
)
from .const import WAN_TYPE_CELLULAR, WAN_TYPE_ETHERNET, WAN_TYPE_WIFI, WAN_TYPE_VWAN, WAN_TYPE_UNKNOWN

_LOGGER = logging.getLogger(__name__)


class PeplinkAuthError(Exception):
    """Authentication failed (wrong credentials or token)."""


class PeplinkConnectionError(Exception):
    """Network error or router unreachable."""


class PeplinkApiError(Exception):
    """Router returned an API-level error."""


class PeplinkApiClient:
    """HTTP client for the Peplink Router REST API.

    Thread-safe via asyncio.Lock for concurrent auth operations.
    Mirrors the Kotlin OkHttpClient timeouts: 10s connect, 30s read.
    """

    def __init__(
        self,
        base_url: str,
        auth_mode: str,
        username: str = "",
        password: str = "",
        client_id: str = "",
        client_secret: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_mode = auth_mode
        self._username = username
        self._password = password
        self._client_id = client_id
        self._client_secret = client_secret

        self._timeout = aiohttp.ClientTimeout(connect=10, sock_read=30, sock_connect=10)
        self._session: aiohttp.ClientSession | None = None

        # USERPASS auth state
        self._auth_cookie: str | None = None
        self._is_connected: bool = False

        # TOKEN auth state
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0   # time.monotonic() seconds

        self._auth_lock = asyncio.Lock()

    # ===== SESSION MANAGEMENT =====

    def _session_obj(self) -> aiohttp.ClientSession:
        """Return (or create) the underlying aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar(),  # Manage cookies manually
                timeout=self._timeout,
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session and release resources."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # ===== AUTH STATE =====

    def is_authenticated(self) -> bool:
        """Return True if we currently have a valid auth credential."""
        if self._auth_mode == AUTH_MODE_USERPASS:
            return self._is_connected and self._auth_cookie is not None
        # token mode
        return self._access_token is not None and time.monotonic() < self._token_expires_at

    def _clear_auth_state(self) -> None:
        self._auth_cookie = None
        self._is_connected = False
        self._access_token = None
        self._token_expires_at = 0.0

    # ===== AUTHENTICATION =====

    async def _ensure_connected(self, force: bool = False) -> None:
        """Ensure we have valid auth credentials. Thread-safe via lock."""
        async with self._auth_lock:
            if self._auth_mode == AUTH_MODE_USERPASS:
                if self._is_connected and self._auth_cookie is not None and not force:
                    return
                await self._login()
            else:
                if self._access_token is not None and time.monotonic() < self._token_expires_at and not force:
                    return
                await self._grant_token()

    async def _login(self) -> None:
        """POST /api/login, extract pauth cookie. Port of PeplinkApiClient.login()."""
        url = f"{self._base_url}/api/login"
        payload = {
            "username": self._username,
            "password": self._password,
            "challenge": "challenge",   # Required by Peplink API
        }
        try:
            async with self._session_obj().post(url, json=payload) as resp:
                text = await resp.text()

                if resp.status == 401:
                    self._is_connected = False
                    raise PeplinkAuthError("Authentication failed: invalid username or password")

                if not resp.ok:
                    self._is_connected = False
                    raise PeplinkConnectionError(f"Login failed: HTTP {resp.status}")

                try:
                    body = json.loads(text)
                except ValueError as err:
                    raise PeplinkConnectionError("Login failed: non-JSON response") from err

                if body.get("stat") != "ok":
                    self._is_connected = False
                    raise PeplinkAuthError(f"Login failed: {body.get('message', 'unknown error')}")

                # Extract pauth cookie from Set-Cookie response header
                # Format: "pauth=VALUE; HttpOnly; SameSite=Strict"
                pauth: str | None = None
                for hdr in resp.headers.getall("Set-Cookie", []):
                    for part in hdr.split(";"):
                        part = part.strip()
                        if part.startswith("pauth="):
                            pauth = part[6:]
                            break
                    if pauth:
                        break

                if not pauth:
                    self._is_connected = False
                    raise PeplinkAuthError("Login failed: no pauth cookie in response")

                self._auth_cookie = pauth
                self._is_connected = True
                _LOGGER.debug("Login successful (cookie obtained)")

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            self._is_connected = False
            self._auth_cookie = None
            raise PeplinkConnectionError(f"Login connection error: {err}") from err

    async def _grant_token(self) -> None:
        """POST /api/auth.token.grant. Port of PeplinkApiClient.grantToken()."""
        if not self._client_id or not self._client_secret:
            raise PeplinkAuthError("Token auth requires client_id and client_secret")

        url = f"{self._base_url}/api/auth.token.grant"
        payload = {
            "clientId": self._client_id,
            "clientSecret": self._client_secret,
            "scope": "api",
        }
        try:
            async with self._session_obj().post(url, json=payload) as resp:
                text = await resp.text()

                if resp.status == 401:
                    self._clear_auth_state()
                    raise PeplinkAuthError("Authentication failed: invalid client ID/secret")

                if not resp.ok:
                    self._clear_auth_state()
                    raise PeplinkConnectionError(f"Token grant failed: HTTP {resp.status}")

                try:
                    body = json.loads(text)
                except ValueError as err:
                    raise PeplinkConnectionError("Token grant failed: non-JSON response") from err

                if body.get("stat") != "ok":
                    self._clear_auth_state()
                    raise PeplinkAuthError(f"Token grant failed: {body.get('message', 'unknown error')}")

                # Try response object first, then top-level (match Kotlin fallback)
                resp_obj = body.get("response") or {}
                token = resp_obj.get("accessToken") or body.get("accessToken")

                if not token:
                    self._clear_auth_state()
                    raise PeplinkAuthError("Token grant failed: no accessToken in response")

                self._access_token = token
                # Use hardcoded 46h (matches TOKEN_REFRESH_INTERVAL_MS in PeplinkApiClient.kt)
                self._token_expires_at = time.monotonic() + TOKEN_REFRESH_SECS
                _LOGGER.debug("Token grant successful (valid for ~46h)")

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            self._clear_auth_state()
            raise PeplinkConnectionError(f"Token grant connection error: {err}") from err

    # ===== REQUEST DISPATCH =====

    def _build_url(self, path: str) -> str:
        """Append accessToken query param for token mode."""
        url = f"{self._base_url}{path}"
        if self._auth_mode == AUTH_MODE_TOKEN and self._access_token:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}accessToken={self._access_token}"
        return url

    def _auth_headers(self) -> dict[str, str]:
        """Return auth headers for userpass mode (cookie injection)."""
        if self._auth_mode == AUTH_MODE_USERPASS and self._auth_cookie:
            return {"Cookie": f"pauth={self._auth_cookie}"}
        return {}

    async def _request(
        self, method: str, path: str, body: dict | None = None
    ) -> Any:
        """Authenticated request with one auto-reauth retry on 401.

        Handles both HTTP 401 and API-level {"stat":"fail","code":401}.
        Port of makeAuthenticatedRequest() in PeplinkApiClient.kt.
        """
        await self._ensure_connected()

        for _attempt in range(2):
            url = self._build_url(path)
            headers = self._auth_headers()
            kwargs: dict = {}
            if body is not None:
                kwargs["json"] = body

            try:
                async with self._session_obj().request(
                    method, url, headers=headers, **kwargs
                ) as resp:
                    text = await resp.text()

                    # HTTP 401 — session/token expired, re-auth once
                    if resp.status == 401:
                        _LOGGER.debug("HTTP 401, re-authenticating")
                        self._clear_auth_state()
                        await self._ensure_connected(force=True)
                        continue

                    if not resp.ok:
                        raise PeplinkConnectionError(f"HTTP {resp.status}")

                    # Parse JSON response
                    try:
                        data = json.loads(text)
                    except ValueError:
                        return text   # Non-JSON response (shouldn't happen normally)

                    # API-level 401: {"stat":"fail","code":401} on HTTP 200
                    if (
                        isinstance(data, dict)
                        and data.get("stat") == "fail"
                        and data.get("code") == 401
                    ):
                        _LOGGER.debug("API-level 401, re-authenticating")
                        self._clear_auth_state()
                        await self._ensure_connected(force=True)
                        continue

                    return data

            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise PeplinkConnectionError(f"Request failed: {err}") from err

        raise PeplinkAuthError("Authentication failed after retry")

    # ===== API METHODS =====

    async def get_wan_status(
        self, conn_ids: str = "1 2 3 4 5 6 7 8 9 10"
    ) -> dict[int, WanConnection]:
        """GET /api/status.wan.connection?id=...  Port of getWanStatus()."""
        path = f"/api/status.wan.connection?id={conn_ids}"
        data = await self._request("GET", path)

        if data.get("stat") != "ok":
            raise PeplinkApiError(f"WAN status error: {data.get('message')}")

        response = data.get("response", {})
        connections: dict[int, WanConnection] = {}
        for key, conn_data in response.items():
            conn_id = _to_int(key)
            if conn_id is not None and isinstance(conn_data, dict):
                connections[conn_id] = _parse_wan_connection(conn_id, conn_data)

        return connections

    async def get_wan_usage(self) -> dict[int, WanUsage]:
        """GET /api/status.wan.connection.allowance  Port of getWanUsage()."""
        data = await self._request("GET", "/api/status.wan.connection.allowance")

        if data.get("stat") != "ok":
            raise PeplinkApiError(f"WAN usage error: {data.get('message')}")

        response = data.get("response", {})
        usage: dict[int, WanUsage] = {}
        for key, conn_data in response.items():
            conn_id = _to_int(key)
            if conn_id is not None and isinstance(conn_data, dict):
                usage[conn_id] = _parse_wan_usage(conn_id, conn_data)

        return usage

    async def set_wan_priority(self, conn_id: int, priority: int | None) -> None:
        """POST /api/config.wan.connection.priority  Port of setWanPriority().

        priority=None disables the WAN connection (payload uses enable:false).
        priority=1-4 sets the priority level.
        """
        item: dict[str, Any] = {"connId": conn_id}
        if priority is not None:
            item["priority"] = priority
        else:
            item["enable"] = False

        payload = {"instantActive": True, "list": [item]}
        data = await self._request("POST", "/api/config.wan.connection.priority", body=payload)

        if data.get("stat") != "ok":
            raise PeplinkApiError(f"Set priority error: {data.get('message')}")

    async def reset_cellular_modem(self, conn_id: int) -> None:
        """POST /api/cmd.cellularModule.reset  Port of resetCellularModem().

        Note: connId is sent as a STRING per the Kotlin source.
        """
        payload = {"connId": str(conn_id)}
        data = await self._request("POST", "/api/cmd.cellularModule.reset", body=payload)

        if data.get("stat") != "ok":
            raise PeplinkApiError(f"Modem reset error: {data.get('message')}")

    async def get_firmware_version(self) -> str:
        """GET /api/info.firmware  Port of getFirmwareVersion().

        Returns the version string for the firmware entry with inUse=true,
        or falls back to entry "1".
        """
        data = await self._request("GET", "/api/info.firmware")

        if data.get("stat") != "ok":
            raise PeplinkApiError(f"Firmware info error: {data.get('message')}")

        response = data.get("response", {})
        version: str | None = None
        for _key, entry in response.items():
            if isinstance(entry, dict) and entry.get("inUse", False):
                version = entry.get("version")
                break
        if not version:
            first = response.get("1", {})
            version = first.get("version") if isinstance(first, dict) else None

        return version or "unknown"

    async def get_connected_devices_count(self) -> int:
        """GET /api/status.client  Port of getConnectedDevicesCount()."""
        data = await self._request("GET", "/api/status.client")

        if data.get("stat") != "ok":
            raise PeplinkApiError(f"Client status error: {data.get('message')}")

        resp = data.get("response", {})
        lst = resp.get("list", []) if isinstance(resp, dict) else []
        return len(lst) if isinstance(lst, list) else 0

    async def get_pep_vpn_profiles(self) -> dict[str, tuple[str, str, str]]:
        """GET /api/status.pepvpn  Port of getPepVpnProfiles().

        Returns {profile_id: (name, type, status)}.
        """
        data = await self._request("GET", "/api/status.pepvpn")

        if data.get("stat") != "ok":
            raise PeplinkApiError(f"PepVPN status error: {data.get('message')}")

        resp = data.get("response", {})
        profiles_obj = resp.get("profile", {}) if isinstance(resp, dict) else {}
        result: dict[str, tuple[str, str, str]] = {}
        if isinstance(profiles_obj, dict):
            for key, obj in profiles_obj.items():
                if not isinstance(obj, dict):
                    continue
                name = obj.get("name", key)
                vpn_type = obj.get("type", "")
                status = obj.get("status", "unknown")
                result[key] = (name, vpn_type, status)

        return result

    async def get_system_diagnostics(self) -> SystemDiagnostics:
        """GET /cgi-bin/MANGA/api.cgi?func=status.system.info&infoType=thermalSensor+fanSpeed.

        Port of getSystemDiagnostics(). Returns partial data on error (not None)
        to match the Kotlin behaviour of returning an empty SystemDiagnostics on failure.
        """
        path = "/cgi-bin/MANGA/api.cgi?func=status.system.info&infoType=thermalSensor%20fanSpeed"
        try:
            data = await self._request("GET", path)
        except (PeplinkConnectionError, PeplinkApiError):
            return SystemDiagnostics(temperature=None, temperature_threshold=None)

        if not isinstance(data, dict) or data.get("stat") != "ok":
            return SystemDiagnostics(temperature=None, temperature_threshold=None)

        resp = data.get("response") or data

        temperature: float | None = None
        temperature_threshold: float | None = None
        thermal_array = resp.get("thermalSensor", [])
        if isinstance(thermal_array, list) and thermal_array:
            obj = thermal_array[0]
            if isinstance(obj, dict):
                t = obj.get("temperature")
                temperature = float(t) if t is not None else None
                thr = obj.get("threshold")
                temperature_threshold = float(thr) if thr is not None else None

        fans: list[FanInfo] = []
        fan_array = resp.get("fanSpeed", [])
        if isinstance(fan_array, list):
            for i, fan_obj in enumerate(fan_array):
                if not isinstance(fan_obj, dict):
                    continue
                speed_rpm_raw = fan_obj.get("value")
                speed_rpm = int(speed_rpm_raw) if speed_rpm_raw and int(speed_rpm_raw) > 0 else None
                speed_pct_raw = fan_obj.get("percentage")
                speed_pct = int(speed_pct_raw) if speed_pct_raw and int(speed_pct_raw) > 0 else None
                active = bool(fan_obj.get("active", False))
                fans.append(FanInfo(
                    fan_id=i + 1,
                    name=f"Fan {i + 1}",
                    speed_rpm=speed_rpm,
                    speed_percent=speed_pct,
                    status="normal" if active else "off",
                ))

        return SystemDiagnostics(
            temperature=temperature,
            temperature_threshold=temperature_threshold,
            fans=fans,
        )

    async def get_device_info(self) -> DeviceInfo:
        """GET /cgi-bin/MANGA/api.cgi?func=status.system.info&infoType=device.

        Port of getDeviceInfo(). Returns partial data on error.
        """
        path = "/cgi-bin/MANGA/api.cgi?func=status.system.info&infoType=device"
        try:
            data = await self._request("GET", path)
        except (PeplinkConnectionError, PeplinkApiError):
            return DeviceInfo(serial_number="unknown", model="unknown")

        if not isinstance(data, dict) or data.get("stat") != "ok":
            return DeviceInfo(serial_number="unknown", model="unknown")

        resp = data.get("response") or data
        device_obj = resp.get("device") if isinstance(resp, dict) else None
        if not isinstance(device_obj, dict):
            return DeviceInfo(serial_number="unknown", model="unknown")

        return DeviceInfo(
            serial_number=device_obj.get("serialNumber", "unknown"),
            model=device_obj.get("model", "unknown"),
            hardware_version=device_obj.get("hardwareRevision") or device_obj.get("hardwareVersion"),
        )

    async def get_traffic_stats(self) -> dict[int, tuple[float, float]]:
        """GET /cgi-bin/MANGA/api.cgi?func=status.traffic  Port of getTrafficStats().

        Returns {connId: (download_mbps, upload_mbps)}.
        Source: kbps values from API converted to Mbps (÷1000).
        """
        path = "/cgi-bin/MANGA/api.cgi?func=status.traffic"
        try:
            data = await self._request("GET", path)
        except (PeplinkConnectionError, PeplinkApiError):
            return {}

        if not isinstance(data, dict) or data.get("stat") != "ok":
            return {}

        resp = data.get("response") or data
        if not isinstance(resp, dict):
            return {}

        bandwidth = resp.get("bandwidth", {})
        if not isinstance(bandwidth, dict):
            return {}

        result: dict[int, tuple[float, float]] = {}
        for key, conn_data in bandwidth.items():
            conn_id = _to_int(key)
            if conn_id is None or not isinstance(conn_data, dict):
                continue
            overall = conn_data.get("overall", {})
            if not isinstance(overall, dict):
                continue
            # API returns kbps, plugin converts to Mbps by dividing by 1000
            dl_mbps = (overall.get("download") or 0) / 1000.0
            ul_mbps = (overall.get("upload") or 0) / 1000.0
            result[conn_id] = (dl_mbps, ul_mbps)

        return result

    async def get_location(self) -> LocationInfo | None:
        """GET /api/info.location  Port of getLocation().

        Returns None if GPS is unavailable or has no fix (not an error).
        """
        try:
            data = await self._request("GET", "/api/info.location")
        except (PeplinkConnectionError, PeplinkApiError):
            return None

        if not isinstance(data, dict) or data.get("stat") != "ok":
            return None

        resp = data.get("response")
        if not isinstance(resp, dict):
            return None

        # Try nested "location" object, fall back to response root
        loc_obj = resp.get("location") or resp
        if not isinstance(loc_obj, dict):
            return None

        if "latitude" not in loc_obj and "longitude" not in loc_obj:
            return None

        def _opt_float(d: dict, key: str) -> float | None:
            val = d.get(key)
            if val is None:
                return None
            try:
                f = float(val)
                return None if (f != f) else f   # NaN check
            except (TypeError, ValueError):
                return None

        return LocationInfo(
            latitude=_opt_float(loc_obj, "latitude"),
            longitude=_opt_float(loc_obj, "longitude"),
            altitude=_opt_float(loc_obj, "altitude"),
            speed=_opt_float(loc_obj, "speed"),
            heading=_opt_float(loc_obj, "heading"),
            accuracy=_opt_float(loc_obj, "accuracy"),
            timestamp=loc_obj.get("timestamp"),
        )

    async def test_connection(self) -> None:
        """Attempt to authenticate. Raises PeplinkAuthError or PeplinkConnectionError on failure.

        Used by config_flow to validate credentials before saving the entry.
        """
        self._clear_auth_state()
        await self._ensure_connected(force=True)


# ===== PARSING HELPERS =====

def _to_int(value: Any) -> int | None:
    """Convert a string or int to int, returning None on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_wan_connection(conn_id: int, data: dict) -> WanConnection:
    """Parse a single WAN connection from the API response dict.

    Port of WanConnection.fromJson() in PeplinkModels.kt.
    """
    wan_type = _determine_wan_type(data)
    enabled = bool(data.get("enable", False))
    message = data.get("message") or data.get("status")

    cellular: CellularInfo | None = None
    if wan_type == WAN_TYPE_CELLULAR and isinstance(data.get("cellular"), dict):
        cellular = _parse_cellular_info(data["cellular"])

    wifi: WifiInfo | None = None
    if wan_type == WAN_TYPE_WIFI and isinstance(data.get("wifi"), dict):
        wifi = _parse_wifi_info(data["wifi"])

    return WanConnection(
        conn_id=conn_id,
        name=data.get("name") or f"WAN {conn_id}",
        wan_type=wan_type,
        enabled=enabled,
        message=message,
        priority=_to_int(data.get("priority")) if "priority" in data else None,
        uptime=_to_int(data.get("uptime")) if "uptime" in data else None,
        ip=data.get("ip") or None,
        status_led=data.get("statusLed") or None,
        cellular=cellular,
        wifi=wifi,
    )


def _determine_wan_type(data: dict) -> str:
    """Port of WanConnection.determineWanType()."""
    if "cellular" in data:
        return WAN_TYPE_CELLULAR
    if "wifi" in data:
        return WAN_TYPE_WIFI
    name = (data.get("name") or "").lower()
    if "vwan" in name:
        return WAN_TYPE_VWAN
    if "ethernet" in name:
        return WAN_TYPE_ETHERNET
    return WAN_TYPE_UNKNOWN


def _parse_cellular_info(data: dict) -> CellularInfo:
    """Port of CellularInfo.fromJson()."""
    # Signal: prefer signalStrength, fall back to signalLevel, then signal.level
    signal: int | None = None
    if "signalStrength" in data:
        signal = _to_int(data["signalStrength"])
    elif "signalLevel" in data:
        signal = _to_int(data["signalLevel"])
    elif isinstance(data.get("signal"), dict):
        signal = _to_int(data["signal"].get("level"))

    # Carrier: may be a dict {"name": ...} or plain string
    carrier_raw = data.get("carrier")
    if isinstance(carrier_raw, dict):
        carrier = carrier_raw.get("name")
    else:
        carrier = carrier_raw or None

    network = data.get("networkType") or data.get("mobileType") or None

    # Bands and signal metrics from rat[].band[] structure
    bands: list[str] = []
    rssi: int | None = None
    rsrp: int | None = None
    for rat in (data.get("rat") or []):
        if not isinstance(rat, dict):
            continue
        for band in (rat.get("band") or []):
            if not isinstance(band, dict):
                continue
            band_name = band.get("name")
            if band_name:
                bands.append(band_name)
            sig_obj = band.get("signal") or {}
            if isinstance(sig_obj, dict):
                if rssi is None and "rssi" in sig_obj:
                    rssi = _to_int(sig_obj["rssi"])
                if rsrp is None and "rsrp" in sig_obj:
                    rsrp = _to_int(sig_obj["rsrp"])

    return CellularInfo(
        module_name=data.get("moduleName") or "Cellular Modem",
        signal_strength=signal,
        signal_quality=_to_int(data.get("signalQuality")) if "signalQuality" in data else None,
        carrier=carrier,
        network_type=network,
        band=data.get("band") or None,
        carrier_aggregation=bool(data.get("carrierAggregation", False)),
        bands=bands,
        rssi_dbm=rssi,
        rsrp_dbm=rsrp,
    )


def _parse_wifi_info(data: dict) -> WifiInfo:
    """Port of WifiInfo.fromJson()."""
    signal: int | None = None
    if "signalStrength" in data:
        signal = _to_int(data["signalStrength"])
    elif isinstance(data.get("signal"), dict):
        signal = _to_int(data["signal"].get("level"))

    return WifiInfo(
        ssid=data.get("ssid") or None,
        frequency=data.get("frequency") or None,
        signal_strength=signal,
        channel=_to_int(data.get("channel")) if "channel" in data else None,
    )


def _parse_wan_usage(conn_id: int, data: dict) -> WanUsage:
    """Port of WanUsage.fromJson().

    Detects multi-SIM structure by checking for nested integer keys.
    """
    has_nested_sims = any(_to_int(k) is not None for k in data)

    sim_slots: dict[int, SimSlotInfo] | None = None
    if has_nested_sims:
        sim_slots = {}
        for key, slot_data in data.items():
            slot_id = _to_int(key)
            if slot_id is not None and isinstance(slot_data, dict):
                sim_slots[slot_id] = _parse_sim_slot(slot_id, slot_data)

    return WanUsage(
        conn_id=conn_id,
        enabled=bool(data.get("enable", False)),
        usage_mb=_to_int(data.get("usage")) if not has_nested_sims and "usage" in data else None,
        limit_mb=_to_int(data.get("limit")) if not has_nested_sims and "limit" in data else None,
        percent=_to_int(data.get("percent")) if not has_nested_sims and "percent" in data else None,
        unit=data.get("unit") if not has_nested_sims else None,
        start_date=data.get("start") if not has_nested_sims else None,
        sim_slots=sim_slots,
    )


def _parse_sim_slot(slot_id: int, data: dict) -> SimSlotInfo:
    """Port of SimSlotInfo.fromJson()."""
    return SimSlotInfo(
        slot_id=slot_id,
        enabled=bool(data.get("enable", False)),
        has_usage_tracking="usage" in data,
        usage_mb=_to_int(data.get("usage")) if "usage" in data else None,
        limit_mb=_to_int(data.get("limit")) if "limit" in data else None,
        percent=_to_int(data.get("percent")) if "percent" in data else None,
        start_date=data.get("start") or None,
    )
