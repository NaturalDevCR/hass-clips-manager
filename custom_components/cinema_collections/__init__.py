"""Cinema Collections Home Assistant integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change

from .api_client import WorkerApiClient, WorkerApiError
from .const import CONF_ENDPOINT, CONF_TOKEN, DOMAIN
from .scheduler import CompilationScheduler, ConfigEntryRunTokenStore
from .subentries import collection_subentries

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CinemaCollectionsRuntimeData:
    """In-memory state owned by exactly one config entry."""

    client: WorkerApiClient
    scheduler: CompilationScheduler | None = None
    cancel_schedule: Callable[[], None] | None = None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an authenticated Worker client for one config entry."""
    client = WorkerApiClient(
        entry.data[CONF_ENDPOINT],
        entry.data[CONF_TOKEN],
        async_get_clientsession(hass),
    )
    scheduler: CompilationScheduler | None = None
    cancel_schedule: Callable[[], None] | None = None
    # A small guard keeps the client-only lifecycle usable in isolated tests.
    if getattr(hass, "bus", None) is not None:
        scheduler = CompilationScheduler(
            client,
            lambda: tuple(
                schedule
                for collection in collection_subentries(entry)
                for schedule in collection.schedules()
            ),
            ConfigEntryRunTokenStore(hass, entry),
        )

        async def async_dispatch_due(now: datetime) -> None:
            try:
                await scheduler.async_handle_due(now)
            except WorkerApiError:
                _LOGGER.warning(
                    "Cinema Collections scheduled compilation could not reach the Worker"
                )

        cancel_schedule = async_track_time_change(hass, async_dispatch_due, second=0)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = CinemaCollectionsRuntimeData(
        client=client,
        scheduler=scheduler,
        cancel_schedule=cancel_schedule,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one entry without modifying Worker or media state."""
    runtime_data = hass.data.get(DOMAIN)
    if runtime_data is not None:
        runtime = runtime_data.pop(entry.entry_id, None)
        if runtime is not None and runtime.cancel_schedule is not None:
            runtime.cancel_schedule()
        if not runtime_data:
            hass.data.pop(DOMAIN)
    return True
