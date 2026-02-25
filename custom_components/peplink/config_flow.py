"""Config flow for Peplink Router integration.

3-step wizard:
  Step 1: base_url + instance_name + auth_mode
  Step 2a (userpass): username + password
  Step 2b (token): client_id + client_secret

Options flow (gear icon): polling intervals, enable_vpn, enable_gps.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import PeplinkApiClient, PeplinkAuthError, PeplinkConnectionError
from .const import (
    AUTH_MODE_TOKEN,
    AUTH_MODE_USERPASS,
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
    MAX_INTERVAL,
    MIN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL): str,
        vol.Required(CONF_INSTANCE_NAME, default="Main"): str,
        vol.Required(CONF_AUTH_MODE, default=AUTH_MODE_USERPASS): vol.In(
            [AUTH_MODE_USERPASS, AUTH_MODE_TOKEN]
        ),
    }
)

STEP_USERPASS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_CLIENT_SECRET): str,
    }
)


class PeplinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Multi-step config flow for Peplink Router."""

    VERSION = 1

    def __init__(self) -> None:
        self._connection_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 — connection details and auth mode selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")
            user_input[CONF_BASE_URL] = base_url

            # Quick reachability check before asking for credentials
            try:
                import aiohttp
                timeout = aiohttp.ClientTimeout(connect=5)
                conn = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(timeout=timeout, connector=conn) as sess:
                    async with sess.get(base_url) as resp:
                        _ = resp.status   # Just checking reachability
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                self._connection_data = dict(user_input)
                auth_mode = user_input[CONF_AUTH_MODE]
                if auth_mode == AUTH_MODE_USERPASS:
                    return await self.async_step_userpass()
                return await self.async_step_token()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_CONNECTION_SCHEMA,
            errors=errors,
        )

    async def async_step_userpass(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2a — username/password credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = PeplinkApiClient(
                base_url=self._connection_data[CONF_BASE_URL],
                auth_mode=AUTH_MODE_USERPASS,
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
            )
            try:
                await client.test_connection()
            except PeplinkAuthError:
                errors["base"] = "invalid_auth"
            except PeplinkConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during credential validation")
                errors["base"] = "unknown"
            finally:
                await client.close()

            if not errors:
                entry_data = {
                    **self._connection_data,
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                }
                instance_name = self._connection_data[CONF_INSTANCE_NAME]
                await self.async_set_unique_id(
                    f"{DOMAIN}_{instance_name.lower().replace(' ', '_')}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Peplink Router ({instance_name})",
                    data=entry_data,
                )

        return self.async_show_form(
            step_id="userpass",
            data_schema=STEP_USERPASS_SCHEMA,
            errors=errors,
        )

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2b — client ID / secret credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = PeplinkApiClient(
                base_url=self._connection_data[CONF_BASE_URL],
                auth_mode=AUTH_MODE_TOKEN,
                client_id=user_input[CONF_CLIENT_ID],
                client_secret=user_input[CONF_CLIENT_SECRET],
            )
            try:
                await client.test_connection()
            except PeplinkAuthError:
                errors["base"] = "invalid_auth"
            except PeplinkConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during credential validation")
                errors["base"] = "unknown"
            finally:
                await client.close()

            if not errors:
                entry_data = {
                    **self._connection_data,
                    CONF_CLIENT_ID: user_input[CONF_CLIENT_ID],
                    CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET],
                }
                instance_name = self._connection_data[CONF_INSTANCE_NAME]
                await self.async_set_unique_id(
                    f"{DOMAIN}_{instance_name.lower().replace(' ', '_')}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Peplink Router ({instance_name})",
                    data=entry_data,
                )

        return self.async_show_form(
            step_id="token",
            data_schema=STEP_TOKEN_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PeplinkOptionsFlow:
        """Return the options flow handler."""
        return PeplinkOptionsFlow(config_entry)


class PeplinkOptionsFlow(OptionsFlow):
    """Options flow — polling intervals and optional feature toggles."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show options form."""
        opts = self._config_entry.options

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        interval_schema = vol.Schema(
            {
                vol.Required(
                    CONF_STATUS_INTERVAL,
                    default=int(opts.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL)),
                ): vol.All(int, vol.Range(min=MIN_INTERVAL, max=MAX_INTERVAL)),
                vol.Required(
                    CONF_USAGE_INTERVAL,
                    default=int(opts.get(CONF_USAGE_INTERVAL, DEFAULT_USAGE_INTERVAL)),
                ): vol.All(int, vol.Range(min=MIN_INTERVAL, max=MAX_INTERVAL)),
                vol.Required(
                    CONF_DIAG_INTERVAL,
                    default=int(opts.get(CONF_DIAG_INTERVAL, DEFAULT_DIAG_INTERVAL)),
                ): vol.All(int, vol.Range(min=MIN_INTERVAL, max=MAX_INTERVAL)),
                vol.Required(
                    CONF_ENABLE_VPN,
                    default=bool(opts.get(CONF_ENABLE_VPN, False)),
                ): bool,
                vol.Required(
                    CONF_VPN_INTERVAL,
                    default=int(opts.get(CONF_VPN_INTERVAL, DEFAULT_VPN_INTERVAL)),
                ): vol.All(int, vol.Range(min=MIN_INTERVAL, max=MAX_INTERVAL)),
                vol.Required(
                    CONF_ENABLE_GPS,
                    default=bool(opts.get(CONF_ENABLE_GPS, False)),
                ): bool,
                vol.Required(
                    CONF_GPS_INTERVAL,
                    default=int(opts.get(CONF_GPS_INTERVAL, DEFAULT_GPS_INTERVAL)),
                ): vol.All(int, vol.Range(min=MIN_INTERVAL, max=MAX_INTERVAL)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=interval_schema)
