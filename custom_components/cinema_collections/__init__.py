"""Cinema Collections Home Assistant integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import WorkerApiClient
from .const import CONF_ENDPOINT, CONF_TOKEN, DOMAIN


@dataclass(slots=True)
class CinemaCollectionsRuntimeData:
    """In-memory state owned by exactly one config entry."""

    client: WorkerApiClient


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an authenticated Worker client for one config entry."""
    client = WorkerApiClient(
        entry.data[CONF_ENDPOINT],
        entry.data[CONF_TOKEN],
        async_get_clientsession(hass),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = CinemaCollectionsRuntimeData(client=client)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one entry without modifying Worker or media state."""
    runtime_data = hass.data.get(DOMAIN)
    if runtime_data is not None:
        runtime_data.pop(entry.entry_id, None)
        if not runtime_data:
            hass.data.pop(DOMAIN)
    return True
