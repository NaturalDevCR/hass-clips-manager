"""Cinema Collections Home Assistant integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change

from .api_client import WorkerApiClient, WorkerApiError
from .const import (
    CONF_ENDPOINT,
    CONF_HISTORY_RESET_TIME,
    CONF_SYNC_ON_STARTUP,
    CONF_TOKEN,
    DEFAULT_HISTORY_RESET_TIME,
    DEFAULT_SYNC_ON_STARTUP,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import CinemaCollectionsCoordinator, override_for_entry, policies_for_entry
from .history import PlaybackHistoryStore
from .scheduler import CompilationScheduler, ConfigEntryRunTokenStore
from .services import async_register_services
from .subentries import (
    SUBENTRY_COLLECTION,
    SUBENTRY_PROFILE,
    CollectionSubentryData,
    ProfileSubentryData,
    WorkerValidationError,
    async_sync_collection,
    async_sync_profile,
    collection_subentries,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CinemaCollectionsRuntimeData:
    """In-memory state owned by exactly one config entry."""

    entry: ConfigEntry
    client: WorkerApiClient
    history: PlaybackHistoryStore | None = None
    scheduler: CompilationScheduler | None = None
    cancel_schedule: Callable[[], None] | None = None
    coordinator: CinemaCollectionsCoordinator | None = None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an authenticated Worker client for one config entry."""
    client = WorkerApiClient(
        entry.data[CONF_ENDPOINT],
        entry.data[CONF_TOKEN],
        async_get_clientsession(hass),
    )
    scheduler: CompilationScheduler | None = None
    cancel_schedule: Callable[[], None] | None = None
    history: PlaybackHistoryStore | None = None
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
    # History owns its own local-midnight listener. Set it up here, rather than
    # waiting for a service call, so a restart reconciles a missed reset early.
    if getattr(hass, "config", None) is not None:
        try:
            reset_time = time.fromisoformat(
                str(entry.options.get(CONF_HISTORY_RESET_TIME, DEFAULT_HISTORY_RESET_TIME))
            )
        except (AttributeError, TypeError, ValueError):
            reset_time = time(0, 0)
        history = PlaybackHistoryStore(
            hass,
            storage_key=f"{DOMAIN}.{entry.entry_id}.playback_history",
            reset_time=reset_time,
        )
        await history.async_setup()
    coordinator = CinemaCollectionsCoordinator(
        hass,
        client,
        entry=entry if hasattr(entry, "async_on_unload") else None,
        collections=lambda: policies_for_entry(entry),
        override=lambda: override_for_entry(entry),
        schedules=lambda: tuple(
            schedule
            for collection in collection_subentries(entry)
            for schedule in collection.schedules()
        ),
    )
    # Worker disconnects become a degraded snapshot, so setup remains available
    # for local policy/history and a later automatic refresh can reconnect.
    if hasattr(entry, "async_on_unload"):
        await coordinator.async_config_entry_first_refresh()
    else:
        await coordinator.async_refresh()
    if hasattr(entry, "async_on_unload") and bool(
        entry.options.get(CONF_SYNC_ON_STARTUP, DEFAULT_SYNC_ON_STARTUP)
    ):
        await _async_sync_subentries_on_startup(hass, entry, client)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = CinemaCollectionsRuntimeData(
        entry=entry,
        client=client,
        history=history,
        scheduler=scheduler,
        cancel_schedule=cancel_schedule,
        coordinator=coordinator,
    )
    if hasattr(entry, "async_on_unload"):
        await async_register_services(hass)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one entry without modifying Worker or media state."""
    unload_ok = True
    if hasattr(entry, "async_on_unload"):
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    runtime_data = hass.data.get(DOMAIN)
    if runtime_data is not None:
        runtime = runtime_data.pop(entry.entry_id, None)
        if runtime is not None and runtime.cancel_schedule is not None:
            runtime.cancel_schedule()
        if runtime is not None and runtime.history is not None:
            await runtime.history.async_shutdown()
        if not runtime_data:
            hass.data.pop(DOMAIN)
    return unload_ok


async def _async_sync_subentries_on_startup(
    hass: HomeAssistant, entry: ConfigEntry, client: WorkerApiClient
) -> None:
    """Reconcile persisted subentries with Worker revisions using their stable keys."""
    for subentry in tuple(entry.subentries.values()):
        try:
            if subentry.subentry_type == SUBENTRY_COLLECTION:
                collection = CollectionSubentryData.from_dict(subentry.data)
                synchronized = await async_sync_collection(client, collection)
                if synchronized.worker_revision != collection.worker_revision:
                    hass.config_entries.async_update_subentry(
                        entry, subentry, data=synchronized.as_dict(), title=synchronized.name
                    )
            elif subentry.subentry_type == SUBENTRY_PROFILE:
                profile = ProfileSubentryData.from_dict(subentry.data)
                synchronized = await async_sync_profile(client, profile)
                if synchronized.worker_revision != profile.worker_revision:
                    hass.config_entries.async_update_subentry(
                        entry, subentry, data=synchronized.as_dict(), title=synchronized.name
                    )
        except (WorkerApiError, WorkerValidationError, KeyError, TypeError, ValueError) as error:
            _LOGGER.warning(
                "Cinema Collections could not synchronize %s %s on startup: %s",
                subentry.subentry_type,
                subentry.unique_id,
                error,
            )
