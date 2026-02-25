"""Constants for the Peplink Router integration."""

DOMAIN = "peplink"

# Config entry keys
CONF_BASE_URL = "base_url"
CONF_AUTH_MODE = "auth_mode"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_INSTANCE_NAME = "instance_name"
CONF_ENABLE_GPS = "enable_gps"
CONF_ENABLE_VPN = "enable_vpn"
CONF_STATUS_INTERVAL = "status_interval"
CONF_USAGE_INTERVAL = "usage_interval"
CONF_DIAG_INTERVAL = "diag_interval"
CONF_GPS_INTERVAL = "gps_interval"
CONF_VPN_INTERVAL = "vpn_interval"

# Auth mode values (match Android plugin config)
AUTH_MODE_USERPASS = "userpass"
AUTH_MODE_TOKEN = "token"

# API paths (match PeplinkApiClient.kt companion constants)
PATH_LOGIN = "/api/login"
PATH_TOKEN_GRANT = "/api/auth.token.grant"
PATH_WAN_STATUS = "/api/status.wan.connection"
PATH_WAN_USAGE = "/api/status.wan.connection.allowance"
PATH_WAN_PRIORITY = "/api/config.wan.connection.priority"
PATH_CELLULAR_RESET = "/api/cmd.cellularModule.reset"
PATH_INFO_FIRMWARE = "/api/info.firmware"
PATH_STATUS_CLIENT = "/api/status.client"
PATH_STATUS_PEPVPN = "/api/status.pepvpn"
PATH_INFO_LOCATION = "/api/info.location"
PATH_SYSTEM_INFO = "/cgi-bin/MANGA/api.cgi"

# Default polling intervals (seconds) — match Android plugin defaults
DEFAULT_STATUS_INTERVAL = 10
DEFAULT_USAGE_INTERVAL = 60
DEFAULT_DIAG_INTERVAL = 30
DEFAULT_VPN_INTERVAL = 60
DEFAULT_GPS_INTERVAL = 120
MIN_INTERVAL = 5
MAX_INTERVAL = 3600

# WAN type strings (match WanType enum in PeplinkModels.kt)
WAN_TYPE_CELLULAR = "cellular"
WAN_TYPE_ETHERNET = "ethernet"
WAN_TYPE_WIFI = "wifi"
WAN_TYPE_VWAN = "vwan"
WAN_TYPE_UNKNOWN = "unknown"

# SIM slot names (match getSimSlotName() in PeplinkPlugin.kt)
SIM_SLOT_NAMES = {
    1: "SIM A",
    2: "SIM B",
    3: "RemoteSIM",
    4: "FusionSIM",
    5: "Peplink eSIM",
}
MAX_SIM_SLOTS = 5

# WAN IDs to probe during discovery (match PeplinkDiscovery.kt)
WAN_DISCOVERY_IDS = list(range(1, 11))

# Token lifetime in seconds (46 hours, match TOKEN_REFRESH_INTERVAL_MS in PeplinkApiClient.kt)
TOKEN_REFRESH_SECS = 46 * 60 * 60
