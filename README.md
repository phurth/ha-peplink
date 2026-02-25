# ha-peplink

Home Assistant HACS integration for **Peplink / Pepwave routers**.

Polls the router's local REST API to provide real-time WAN status, cellular signal, bandwidth, usage, GPS location, and PepVPN monitoring — plus controls for WAN priority and modem reset.

## Features

- **Multi-WAN monitoring**: status, IP, uptime, priority, and status LED for every WAN connection
- **Cellular signal**: RSRP/RSRQ dBm, signal bars, carrier, network type, and band info
- **Bandwidth**: real-time download and upload rates per WAN
- **Data usage**: monthly usage with rollover cycle support; per-SIM usage on multi-SIM modems
- **Carrier aggregation** binary sensor per cellular WAN
- **WAN priority control**: change failover priority (1–4 or Disabled) from HA
- **Modem reset** button per cellular WAN
- **Hardware rediscovery** button to detect WAN changes without removing the integration
- **PepVPN monitoring** (optional): connection status per VPN profile
- **GPS / device tracker** (optional): live lat/lon with altitude, speed, heading, and accuracy attributes
- **Diagnostics**: firmware version, model, serial number, connected devices, fan speed/status, system temperature
- **Multi-cadence polling**: fast WAN status updates with slower polls for usage, diagnostics, VPN, and GPS

## Requirements

- Home Assistant 2024.1+
- Peplink / Pepwave router with the local REST API accessible from HA (same network or VPN)
- Router admin credentials (username/password) or an API token (client ID + secret)

## Installation

### HACS (recommended)

1. In HA, open **HACS → Integrations → ⋮ → Custom Repositories**
2. Add `https://github.com/phurth/ha-peplink` as an **Integration**
3. Search for **Peplink Router** and install it
4. Restart Home Assistant

### Manual

1. Copy the `custom_components/peplink/` folder to your HA `custom_components/` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Peplink Router**
3. Enter the router's local URL (e.g. `http://192.168.50.1`)
4. Give it an instance name (e.g. `Rockwood Peplink`) — used to name entities
5. Choose auth mode:
   - **Username / Password** — standard admin credentials
   - **API Token** — client ID and secret from the router's API token settings
6. Enter credentials and finish

## Options

After setup, click the gear icon on the integration to configure:

| Option | Default | Description |
|--------|---------|-------------|
| Status interval | 10 s | How often to poll WAN status (fastest cadence) |
| Usage interval | 60 s | How often to poll monthly data usage |
| Diagnostics interval | 30 s | How often to poll bandwidth, fans, temperature |
| Enable VPN | Off | Poll and create entities for PepVPN profiles |
| VPN interval | 60 s | How often to poll VPN status |
| Enable GPS | Off | Poll GPS and create a device tracker entity |
| GPS interval | 120 s | How often to poll GPS location |

> Saving options reloads the integration — entities will be briefly unavailable.

## Entities

Entities are named `{Device} {Entity}` where Device = `Peplink Router ({instance name})`.

### Per WAN

| Entity | Type | Notes |
|--------|------|-------|
| `{WAN} Status` | Sensor | Connected / Connecting / Disconnected / etc. |
| `{WAN} IP Address` | Sensor | Current WAN IP |
| `{WAN} Uptime` | Sensor | Formatted uptime string |
| `{WAN} Priority` | Sensor | Current priority level |
| `{WAN} Status LED` | Sensor | green / yellow / red / gray / flash |
| `{WAN} Download Rate` | Sensor | Mbps |
| `{WAN} Upload Rate` | Sensor | Mbps |
| `{WAN} Priority Control` | Select | Change priority (1–4 or Disabled) |

### Per cellular WAN

| Entity | Type | Notes |
|--------|------|-------|
| `{WAN} Signal` | Sensor | Signal bars (0–5) |
| `{WAN} Signal dBm` | Sensor | RSRP in dBm |
| `{WAN} Carrier` | Sensor | Carrier name |
| `{WAN} Network Type` | Sensor | LTE / 5G / etc. |
| `{WAN} Bands` | Sensor | Active band(s) |
| `{WAN} Carrier Aggregation` | Binary Sensor | On when CA is active |
| `{WAN} Reset Modem` | Button | Restarts the cellular modem |

### Usage

| Entity | Notes |
|--------|-------|
| `{WAN}` | Monthly usage (single-SIM) |
| `{WAN} SIM A / SIM B / …` | Per-SIM usage (multi-SIM modems) |

### Global

| Entity | Type | Notes |
|--------|------|-------|
| Firmware Version | Sensor | |
| Connected Devices | Sensor | LAN client count |
| Model | Sensor | Router model string |
| Serial Number | Sensor | |
| System Temperature | Sensor | °C — disabled by default |
| Temperature Threshold | Sensor | °C — disabled by default |
| Fan 1–3 Speed / Status | Sensor | Disabled by default |
| API Connected | Binary Sensor (diag) | |
| Authenticated | Binary Sensor (diag) | |
| Data Healthy | Binary Sensor (diag) | |
| Rediscover Hardware | Button | Re-scans WANs without removing the integration |

### Optional (when enabled in options)

| Entity | Type | Notes |
|--------|------|-------|
| `VPN: {profile}` | Sensor | PepVPN profile status |
| Location | Device Tracker | GPS — lat/lon with altitude, speed, heading |
| GPS Speed | Sensor | km/h |
| GPS Altitude | Sensor | metres |
| GPS Heading | Sensor | degrees |
| GPS Coordinates | Sensor | `lat, lon` string |

## Troubleshooting

Enable debug logging by adding to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.peplink: debug
```

Then restart HA and check **Settings → System → Logs**.

## License

MIT — see [LICENSE](LICENSE)
