"""Cinema Collections Home Assistant integration."""

from __future__ import annotations

from dataclasses import dataclass

try:  # Home Assistant is available whenever the integration itself is loaded.
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
except ModuleNotFoundError:  # Allows the dependency-free API client to be unit tested in isolation.
    from typing import Any

    ConfigEntry = Any
    HomeAssistant = Any

    def async_get_clientsession(_hass: Any) -> Any:
        """Fail clearly if integration lifecycle is used outside Home Assistant."""
        raise RuntimeError("Home Assistant is required to set up Cinema Collections")


from .api_client import WorkerApiClient
from .const import CONF_ENDPOINT, CONF_TOKEN


@dataclass(slots=True)
class CinemaCollectionsRuntimeData:
    """In-memory state owned by exactly one config entry."""

    client: WorkerApiClient


type CinemaCollectionsConfigEntry = ConfigEntry[CinemaCollectionsRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: CinemaCollectionsConfigEntry) -> bool:
    """Set up an authenticated Worker client for one config entry."""
    client = WorkerApiClient(
        entry.data[CONF_ENDPOINT],
        entry.data[CONF_TOKEN],
        async_get_clientsession(hass),
    )
    entry.runtime_data = CinemaCollectionsRuntimeData(client=client)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CinemaCollectionsConfigEntry) -> bool:
    """Unload one entry without modifying Worker or media state."""
    del hass, entry
    return True
