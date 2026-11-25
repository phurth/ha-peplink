"""Actions for cellular band selection.

ha_peplink.get_cellular_bands  — read each SIM's band selection and the supported bands.
ha_peplink.set_cellular_bands  — set one SIM's bands (or Auto), verify by reading back,
                                 optionally wait for the modem to reconnect.

Both target a '<WAN> Band Selection' sensor, which identifies the router and modem.
They use the router's token/REST API only; no web-admin session is needed.
"""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .api import PeplinkApiError, PeplinkAuthError, PeplinkConnectionError
from .const import DOMAIN
from .coordinator import BandSelectionError, PeplinkCoordinator

SERVICE_GET_BANDS = "get_cellular_bands"
SERVICE_SET_BANDS = "set_cellular_bands"

ATTR_SIM = "sim"
ATTR_BANDS = "bands"
ATTR_WAIT = "wait_for_connection"

_UNIQUE_ID_RE = re.compile(r"_wan(\d+)_band_selection$")


def _bands_list(value: Any) -> list[str]:
    """Accept a list or a comma/space separated string ('auto', 'LTE_B2, LTE_B4')."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in re.split(r"[,\s]+", value) if part]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    raise vol.Invalid("bands must be a list of band names or a comma-separated string")


GET_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_id})

SET_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional(ATTR_SIM): vol.Any(None, cv.string, int),
        vol.Required(ATTR_BANDS): _bands_list,
        vol.Optional(ATTR_WAIT, default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=900)),
    }
)


def _resolve(hass: HomeAssistant, entity_id: str) -> tuple[PeplinkCoordinator, int]:
    entry = er.async_get(hass).async_get(entity_id)
    match = _UNIQUE_ID_RE.search(entry.unique_id) if entry and entry.platform == DOMAIN else None
    if entry is None or match is None:
        raise ServiceValidationError(
            f"{entity_id} is not a Peplink '<cellular WAN> Band Selection' sensor"
        )
    coordinator = hass.data.get(DOMAIN, {}).get(entry.config_entry_id)
    if coordinator is None:
        raise ServiceValidationError(f"The Peplink router for {entity_id} is not loaded")
    return coordinator, int(match.group(1))


async def _run(coro) -> dict[str, Any]:
    try:
        return await coro
    except BandSelectionError as err:
        raise ServiceValidationError(str(err)) from err
    except (PeplinkApiError, PeplinkAuthError, PeplinkConnectionError) as err:
        raise HomeAssistantError(str(err)) from err


async def _get_bands(call: ServiceCall) -> ServiceResponse:
    coordinator, conn_id = _resolve(call.hass, call.data["entity_id"])
    return await _run(coordinator.async_get_bands(conn_id))


async def _set_bands(call: ServiceCall) -> ServiceResponse:
    coordinator, conn_id = _resolve(call.hass, call.data["entity_id"])
    sim = call.data.get(ATTR_SIM)
    return await _run(
        coordinator.async_set_bands(
            conn_id,
            None if sim is None else str(sim),
            call.data[ATTR_BANDS],
            call.data[ATTR_WAIT],
        )
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the band-selection actions (once per Home Assistant run)."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_BANDS):
        return
    hass.services.async_register(
        DOMAIN, SERVICE_GET_BANDS, _get_bands, schema=GET_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_BANDS, _set_bands, schema=SET_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
