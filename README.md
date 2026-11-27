# ha-peplink

Home Assistant HACS integration for **Peplink / Pepwave routers**.

Polls the router's local REST API to provide real-time WAN status, cellular signal, bandwidth, usage, GPS location, and PepVPN monitoring — plus controls for WAN priority and modem reset.

> **Disclaimer:** This is an independent community integration and is not affiliated with, endorsed by, or supported by Peplink or any of its affiliates. Use it at your own risk.

## Features

- **Multi-WAN monitoring**: status, IP, uptime, priority, and status LED for every WAN connection
- **Cellular signal**: RSRP/RSRQ dBm, signal bars, carrier, network type, and band info
- **Cellular engineering data**: primary-carrier RSRP, RSRQ, SINR and RSSI; primary band with EARFCN, PCI and channel width; every carrier-aggregation component as attributes; cell ID/TAC/MCC/MNC; modem model and firmware
- **Cellular band selection**: read and set which bands each SIM may use (or Auto), with readback verification and an optional wait for the modem to reconnect — via the `ha_peplink.set_cellular_bands` and `ha_peplink.get_cellular_bands` actions
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
| `{WAN} Bands` | Sensor | Active band(s); attributes `primary`, `secondary`, `components` (per-carrier band, EARFCN, PCI, width, RSRP/RSRQ/SINR/RSSI) and `aggregated_bandwidth_mhz` (only when every carrier reports its width). A carrier the router reports as "Unknown" is named from its channel number using the router's band table, and flagged `band_from_channel: true` |
| `{WAN} Carrier Aggregation` | Binary Sensor | On when CA is active |
| `{WAN} RSRP` / `RSRQ` / `SINR` / `RSSI` | Sensor (diag) | Primary carrier, dBm / dB |
| `{WAN} Primary Band` | Sensor (diag) | Primary carrier band; attributes `earfcn`, `pci`, `bandwidth_mhz` |
| `{WAN} Cell ID` | Sensor (diag) | Serving cell; attributes `tac`, `mcc`, `mnc`, `pci`, `network_mode` |
| `{WAN} Modem` | Sensor (diag) | Modem model; attributes `manufacturer`, `firmware`, `hardware_revision` |
| `{WAN} Band Selection` | Sensor | `Auto` or `Custom (N bands)` for the active SIM; attributes list every SIM's setting and the bands the modem supports. Target of the band actions |
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

## Cellular band selection

Two actions work on one cellular modem at a time. Target the modem's `{WAN} Band Selection` sensor.

**`ha_peplink.get_cellular_bands`** returns each SIM's current setting (`auto` or a list of bands), the bands the modem supports, display names for them, and the live carrier data:

```yaml
action: ha_peplink.get_cellular_bands
data:
  entity_id: sensor.peplink_router_van_cellular_1_band_selection
response_variable: before
```

**`ha_peplink.set_cellular_bands`** sets one SIM's bands, reads the setting back to verify it, and fails if the router reports something else:

```yaml
action: ha_peplink.set_cellular_bands
data:
  entity_id: sensor.peplink_router_van_cellular_1_band_selection
  sim: SIM A                 # optional; defaults to the active SIM
  bands: [LTE_B2, LTE_B66]   # or "auto" / [] for Auto
  wait_for_connection: 180   # optional; seconds to wait for the modem to reconnect
response_variable: result    # previous, applied, verified, changed, link.connected, link.bands, ...
```

To restore a saved setting, pass it back: `bands: "{{ before.sims['SIM A'].bands }}"`.

- **Band names** are the router's own: `LTE_B2`, `LTE_B66`, `5G_NSA_B66`, `5G_SA_B41`, `WCDMA_850`. LTE shorthand (`B12`, `12`) also works. Bands the modem doesn't support are rejected before anything is changed.
- **Applying restarts that modem**, but not at once: on a MAX BR1 Pro the link dropped about 25–30 s after the change and was back about 15 s later. `wait_for_connection` waits for that drop and the reconnect (use 90 s or more), and the response says whether it came back (`link.connected`, `link.restart_seen`). Asking for the setting a SIM already has changes nothing and doesn't restart the modem (`changed: false`).
- **Supported bands** come from the router's band reference table, matched to the modem model, network mode and the SIM's LTE/3G setting. If the firmware doesn't return the table (it first appears in the 8.6 API documentation), the integration uses its bundled copy, taken from firmware 8.6.1; the sensor's `band_list_source` attribute says which.
- **Works with token (client ID/secret) authentication alone.** No admin login is needed. The API client needs read-write access.
- Tested on a MAX BR1 Pro (EM7690) and MAX Transit Duo Pro (EM7411/EM7511) on firmware 8.6.1, and on a MAX Transit Duo (EM7511) on 8.5.1, LTE only. 5G modems and routers managed by InControl2 are untested.
- **EARFCN on some modems:** the Sierra EM7511 reports LTE channel numbers above 65535 cut to 16 bits (Band 66's 67086 arrives as 1550). When the router names the band, the integration reports the corrected channel.

## Troubleshooting

Enable debug logging by adding to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.ha_peplink: debug
```

Then restart HA and check **Settings → System → Logs**.

## License

MIT — see [LICENSE](LICENSE)
