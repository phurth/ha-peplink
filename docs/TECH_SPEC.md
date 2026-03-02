# Peplink Router HACS Integration — Technical Specification

## 1. Purpose and Scope

`ha_peplink` integrates Peplink/Pepwave routers via local REST API, exposing WAN, cellular, usage, diagnostics, VPN, and optional GPS telemetry with selected control actions.

## 2. Integration Snapshot

- **Domain:** `ha_peplink`
- **Primary runtime component:** `PeplinkCoordinator`
- **API client:** `PeplinkApiClient` (`aiohttp`)
- **Platforms:** `binary_sensor`, `button`, `device_tracker`, `select`, `sensor`
- **Transport:** HTTP REST
- **Coordinator mode:** multi-cadence polling aggregated into one dataset

## 3. Configuration and Entry Setup

- config flow collects base URL, auth mode, credentials, and instance name
- options flow controls poll intervals and optional VPN/GPS features
- setup sequence:
	1. hardware discovery
	2. first refresh
	3. platform forwarding
	4. update listener registration

## 4. Runtime Lifecycle

1. Coordinator starts with status interval as fastest cadence.
2. WAN status poll runs every coordinator update tick.
3. Usage/diagnostics/VPN/GPS polls execute conditionally by interval.
4. Cached slow-poll data is merged with fresh status data.
5. Unload closes API session and releases resources.

## 5. Protocol and Transport Model

### 5.1 Authentication model

- user/pass mode: login endpoint with managed cookie
- token mode: token grant with refresh window
- auto re-auth on HTTP 401 and API-level auth failure responses

Auth implementation specifics:

- userpass login path: `/api/login` (expects `pauth` cookie)
- token grant path: `/api/auth.token.grant`
- token validity window: `46h` (`TOKEN_REFRESH_SECS`)
- aiohttp timeouts: connect `10s`, read `30s`

### 5.2 Request behavior

- thread-safe auth locking
- typed exception classes (auth/connection/API)

Primary API endpoints used by integration:

- `/api/status.wan.connection`
- `/api/status.wan.connection.allowance`
- `/api/config.wan.connection.priority`
- `/api/cmd.cellularModule.reset`
- `/api/info.firmware`
- `/api/status.client`
- `/api/status.pepvpn`
- `/api/info.location`
- `/cgi-bin/MANGA/api.cgi?func=status.system.info&infoType=thermalSensor%20fanSpeed`
- `/cgi-bin/MANGA/api.cgi?func=status.system.info&infoType=device`
- `/cgi-bin/MANGA/api.cgi?func=status.traffic`
- parsed response mapping into dataclasses

## 6. State and Entity Model

- coordinator publishes `PeplinkData` with WAN, usage, diagnostics, traffic, VPN, and location state
- WAN/cellular entities are created from discovered topology
- optional VPN and GPS entities are enabled by options
- health entities expose API/auth/data status for automation resilience

## 7. Command and Control Surface

- WAN priority updates (including disable path)
- cellular modem reset operations
- hardware rediscovery trigger

Control writes route through authenticated API methods with response validation.

## 8. Reliability and Recovery

- auth/transport/API failures are handled as distinct categories
- non-critical slow poll failures do not drop all coordinator state
- re-auth retry logic handles session/token expiration
- integration reload on options change keeps topology and cadence consistent

Default cadence constants:

- status: `10s`
- usage: `60s`
- diagnostics: `30s`
- VPN: `60s`
- GPS: `120s`

Allowed interval range: `5..3600s`.

On partial failures, coordinator intentionally retains last successful slow-poll caches (usage/diag/vpn/gps) while continuing status polls.

## 9. Diagnostics and Observability

- global health flags: `api_connected`, `authenticated`, `data_healthy`
- diagnostics include firmware/device info/fans/temperature/client count
- per-WAN traffic and usage telemetry are retained for troubleshooting

Traffic normalization detail: bandwidth API values are interpreted as kbps and converted to Mbps (`/1000`) before entity publication.

## 10. Security and Safety Notes

- supports credentialed local API auth modes with scoped handling
- no cloud dependency required by integration runtime
- optional GPS is explicit opt-in via options flow

## 11. Evolution Notes (Commit History)

Recent trajectory includes:

- initial HA-native API polling implementation
- setup/reload hardening
- migration to `ha_peplink` domain naming
- HACS/CI and repository metadata improvements

## 12. Known Constraints

- endpoint shape/availability can vary by model and firmware
- optional VPN/GPS entities require both feature toggle and device support
- interval tuning is a freshness vs API-load tradeoff

## 13. Extension Guidelines

1. Add new endpoints in API client first with typed parse paths.
2. Integrate new signals into existing cadence buckets when possible.
3. Keep coordinator output strongly typed through dataclass models.
4. Preserve auth-expiry handling as cross-cutting request behavior.
5. Prefer additive optional entities for model-specific feature sets.
