"""Validated Home Assistant automation services for Cinema Collections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import uuid4

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.json import JsonValueType

from .const import (
    CONF_MEDIA_URI_PREFIX,
    CONF_OVERRIDE_COLLECTION_ID,
    CONF_OVERRIDE_MODE,
    DEFAULT_MEDIA_URI_PREFIX,
    DOMAIN,
)
from .coordinator import CinemaCollectionsCoordinator, override_for_entry, policies_for_entry
from .history import PlaybackHistoryStore
from .resolver import CollectionPolicy, OverrideKind, OverrideMode, resolve_active_collection
from .selection import ClipAvailabilityClient, SelectionService, SelectRequest, build_media_uri

SERVICE_SELECT_NEXT_CLIP = "select_next_clip"
SERVICE_RESET_HISTORY = "reset_history"
SERVICE_SCAN_LIBRARY = "scan_library"
SERVICE_COMPILE_COLLECTION = "compile_collection"
SERVICE_COMPILE_ALL = "compile_all"
SERVICE_RETRY_FAILED = "retry_failed"
SERVICE_CANCEL_PROCESSING = "cancel_processing"
SERVICE_CANCEL_ALL_PROCESSING = "cancel_all_processing"
SERVICE_SET_COLLECTION_OVERRIDE = "set_collection_override"

_COLLECTION_ID = vol.All(str, vol.Length(min=1, max=100))
_ENTRY_ID = vol.All(str, vol.Length(min=1, max=200))
_BASE_SCHEMA = {vol.Optional("entry_id"): _ENTRY_ID}
_SELECT_NEXT_SCHEMA = vol.Schema(
    {
        **_BASE_SCHEMA,
        vol.Optional("collection_id"): _COLLECTION_ID,
        vol.Optional("dry_run", default=False): bool,
    }
)
_RESET_SCHEMA = vol.Schema({**_BASE_SCHEMA, vol.Optional("collection_id"): _COLLECTION_ID})
_SCAN_SCHEMA = vol.Schema(
    {**_BASE_SCHEMA, vol.Optional("collection_ids"): vol.All([_COLLECTION_ID], vol.Length(max=100))}
)
_COMPILE_SCHEMA = vol.Schema(
    {
        **_BASE_SCHEMA,
        vol.Required("collection_id"): _COLLECTION_ID,
        vol.Optional("strategy", default="scan_and_compile_changed_or_missing"): vol.In(
            {
                "scan_and_compile_changed_or_missing",
                "compile_stale_only",
                "scan_only",
            }
        ),
        vol.Optional("skip_if_processing", default=True): bool,
    }
)
_COMPILE_ALL_SCHEMA = vol.Schema(
    {
        **_BASE_SCHEMA,
        vol.Optional("strategy", default="scan_and_compile_changed_or_missing"): vol.In(
            {
                "scan_and_compile_changed_or_missing",
                "compile_stale_only",
                "scan_only",
            }
        ),
        vol.Optional("skip_if_processing", default=True): bool,
    }
)
_RETRY_SCHEMA = vol.Schema({**_BASE_SCHEMA, vol.Optional("collection_id"): _COLLECTION_ID})
_CANCEL_SCHEMA = vol.Schema({**_BASE_SCHEMA, vol.Optional("job_id"): _COLLECTION_ID})
_CANCEL_ALL_SCHEMA = vol.Schema({**_BASE_SCHEMA})
_OVERRIDE_SCHEMA = vol.Schema(
    {
        **_BASE_SCHEMA,
        vol.Required("mode"): vol.In([kind.value for kind in OverrideKind]),
        vol.Optional("collection_id"): _COLLECTION_ID,
    }
)


def service_supports_response(name: str) -> SupportsResponse:
    """Return the Home Assistant response behavior for a service name."""
    return SupportsResponse.ONLY if name == SERVICE_SELECT_NEXT_CLIP else SupportsResponse.NONE


async def async_register_services(hass: HomeAssistant) -> None:
    """Register domain-wide handlers once; the request chooses a loaded entry."""
    registrations = (
        (SERVICE_SELECT_NEXT_CLIP, _SELECT_NEXT_SCHEMA),
        (SERVICE_RESET_HISTORY, _RESET_SCHEMA),
        (SERVICE_SCAN_LIBRARY, _SCAN_SCHEMA),
        (SERVICE_COMPILE_COLLECTION, _COMPILE_SCHEMA),
        (SERVICE_COMPILE_ALL, _COMPILE_ALL_SCHEMA),
        (SERVICE_RETRY_FAILED, _RETRY_SCHEMA),
        (SERVICE_CANCEL_PROCESSING, _CANCEL_SCHEMA),
        (SERVICE_CANCEL_ALL_PROCESSING, _CANCEL_ALL_SCHEMA),
        (SERVICE_SET_COLLECTION_OVERRIDE, _OVERRIDE_SCHEMA),
    )
    for name, schema in registrations:
        if hass.services.has_service(DOMAIN, name):
            continue

        async def async_handler(call: ServiceCall, service_name: str = name) -> ServiceResponse:
            entry = _entry_for_service(hass, call.data)
            return await async_run_action(hass, entry, service_name, call.data)

        hass.services.async_register(
            DOMAIN,
            name,
            async_handler,
            schema=schema,
            supports_response=service_supports_response(name),
        )


def _entry_for_service(hass: HomeAssistant, data: Mapping[str, object]) -> ConfigEntry:
    """Resolve exactly one loaded entry, avoiding ambiguous global actions."""
    runtimes = hass.data.get(DOMAIN, {})
    requested = data.get("entry_id")
    if isinstance(requested, str):
        runtime = runtimes.get(requested)
        if runtime is None:
            raise HomeAssistantError("Cinema Collections config entry is not loaded")
        return runtime.entry
    if len(runtimes) == 1:
        return next(iter(runtimes.values())).entry
    if not runtimes:
        raise HomeAssistantError("No Cinema Collections config entry is loaded")
    raise HomeAssistantError("Multiple Cinema Collections entries are loaded; provide entry_id")


def _runtime(hass: HomeAssistant, entry: ConfigEntry) -> Any:
    """Get only an already loaded runtime; services never construct a second client."""
    try:
        return hass.data[DOMAIN][entry.entry_id]
    except KeyError as error:
        raise HomeAssistantError("Cinema Collections config entry is not loaded") from error


def _collection_or_error(
    collections: Sequence[CollectionPolicy], collection_id: object
) -> CollectionPolicy:
    """Reject empty, unknown, and disabled collection selections descriptively."""
    if not isinstance(collection_id, str) or not collection_id:
        raise HomeAssistantError("A non-empty collection_id is required")
    collection = next((item for item in collections if item.id == collection_id), None)
    if collection is None:
        raise HomeAssistantError(f"Unknown Cinema Collections collection: {collection_id}")
    if not collection.enabled:
        raise HomeAssistantError(f"Cinema Collections collection is disabled: {collection_id}")
    return collection


def _override_collection_or_error(
    collections: Sequence[CollectionPolicy], collection_id: object
) -> CollectionPolicy:
    """Require the same eligibility for explicit service and Select overrides."""
    collection = _collection_or_error(collections, collection_id)
    if not collection.allow_manual_override:
        raise HomeAssistantError(
            f"Cinema Collections collection does not allow manual override: {collection.id}"
        )
    return collection


async def async_select_next_clip(
    *,
    history: PlaybackHistoryStore,
    client: ClipAvailabilityClient,
    collections: Sequence[CollectionPolicy],
    data: Mapping[str, object],
    override: OverrideMode | None = None,
    media_uri_prefix: str = DEFAULT_MEDIA_URI_PREFIX,
) -> dict[str, JsonValueType]:
    """Select a safe Worker-backed clip and format a Home Assistant response."""
    requested = data.get("collection_id")
    if requested is not None:
        collection_id = _collection_or_error(collections, requested).id
    else:
        selected = resolve_active_collection(
            collections, override or OverrideMode.automatic(), _utc_now()
        )
        collection_id = selected.id
    response = await SelectionService(
        history,
        client,
        media_uri_builder=lambda path: build_media_uri(media_uri_prefix, path),
    ).async_select(
        SelectRequest(collection_id=collection_id, dry_run=bool(data.get("dry_run", False)))
    )
    return {
        "collection_id": response.collection_id,
        "clip_id": response.clip_id,
        "relative_output_path": response.relative_output_path,
        "media_content_id": response.media_uri,
        "media_content_type": "video",
        "duration_seconds": response.duration_seconds,
        "history_reset": response.history_reset,
    }


def _worker_is_busy(runtime: Any) -> bool:
    """Report whether the Worker had compilation work under way already.

    Read from the last coordinator snapshot rather than asking the Worker: this
    only decides whether to start a batch, and a snapshot that is one poll old
    cannot cause lost or duplicated work, because every request that follows
    carries its own idempotency key.
    """

    data = getattr(runtime.coordinator, "data", None)
    status = getattr(data, "status", None) if data is not None else None
    if status is None:
        return False
    if getattr(status, "current_job", None) is not None:
        return True
    return int(getattr(status, "queue_depth", 0) or 0) > 0


async def async_run_action(
    hass: HomeAssistant,
    entry: ConfigEntry,
    action: str,
    data: Mapping[str, object],
) -> ServiceResponse:
    """Run a service or button action through one validated, non-device-control path."""
    runtime = _runtime(hass, entry)
    collections = policies_for_entry(entry)
    if action == SERVICE_SELECT_NEXT_CLIP:
        if runtime.history is None:
            raise HomeAssistantError("Cinema Collections playback history is not ready")
        return await async_select_next_clip(
            history=runtime.history,
            client=runtime.client,
            collections=collections,
            data=data,
            override=override_for_entry(entry),
            media_uri_prefix=str(entry.data.get(CONF_MEDIA_URI_PREFIX, DEFAULT_MEDIA_URI_PREFIX)),
        )
    if action == SERVICE_RESET_HISTORY or action == "reset_history":
        if runtime.history is None:
            raise HomeAssistantError("Cinema Collections playback history is not ready")
        collection_id = data.get("collection_id")
        if collection_id is not None:
            _collection_or_error(collections, collection_id)
        await runtime.history.async_reset(cast(str | None, collection_id))
    elif action == SERVICE_SCAN_LIBRARY or action == "scan_library":
        raw_ids = data.get("collection_ids")
        collection_ids = None
        if raw_ids is not None:
            if not isinstance(raw_ids, list):
                raise HomeAssistantError(
                    "collection_ids must be a list of configured collection IDs"
                )
            collection_ids = [
                _collection_or_error(collections, item).id for item in cast(list[object], raw_ids)
            ]
        await runtime.client.async_scan(collection_ids=collection_ids, idempotency_key=_key("scan"))
    elif action == SERVICE_COMPILE_COLLECTION:
        collection = _collection_or_error(collections, data.get("collection_id"))
        await runtime.client.async_compile(
            collection.id,
            strategy=cast(str, data.get("strategy", "scan_and_compile_changed_or_missing")),
            skip_if_processing=bool(data.get("skip_if_processing", True)),
            idempotency_key=_key("compile"),
        )
    elif action == SERVICE_COMPILE_ALL or action == "compile_all":
        enabled = tuple(item for item in collections if item.enabled)
        if not enabled:
            raise HomeAssistantError(
                "No enabled Cinema Collections collection is available to compile"
            )
        # The Worker refuses to queue anything while a compile job is already
        # queued or running, so passing skip_if_processing through on every
        # request made this loop compile the first collection and silently skip
        # all the rest: the first request is what made the Worker busy. The
        # guard asks whether work was already under way *before* this service
        # call, so it is answered once, here, and the per-collection requests
        # then go through regardless of the work this call is itself creating.
        if bool(data.get("skip_if_processing", True)) and _worker_is_busy(runtime):
            return None
        for collection in enabled:
            await runtime.client.async_compile(
                collection.id,
                strategy=cast(str, data.get("strategy", "scan_and_compile_changed_or_missing")),
                skip_if_processing=False,
                idempotency_key=_key(f"compile-{collection.id}"),
            )
    elif action == SERVICE_RETRY_FAILED or action == "retry_failed":
        requested = data.get("collection_id")
        targets = (
            (_collection_or_error(collections, requested),)
            if requested is not None
            else tuple(item for item in collections if item.enabled)
        )
        if not targets:
            raise HomeAssistantError(
                "No enabled Cinema Collections collection is available to retry"
            )
        for collection in targets:
            await runtime.client.async_compile(
                collection.id,
                strategy="compile_stale_only",
                skip_if_processing=False,
                idempotency_key=_key(f"retry-{collection.id}"),
            )
    elif action == SERVICE_CANCEL_PROCESSING or action == "cancel_processing":
        job_id = data.get("job_id")
        if job_id is None and runtime.coordinator.data is not None:
            status = runtime.coordinator.data.status
            if status is not None and status.current_job is not None:
                job_id = status.current_job.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise HomeAssistantError("No active Worker job is available to cancel")
        await runtime.client.async_cancel_job(job_id, idempotency_key=_key("cancel"))
    elif action == SERVICE_CANCEL_ALL_PROCESSING or action == "cancel_all_processing":
        await runtime.client.async_cancel_all_jobs(idempotency_key=_key("cancel-all"))
    elif action == "cleanup_temporaries":
        await runtime.client.async_cleanup_temporaries(idempotency_key=_key("cleanup"))
    elif action == SERVICE_SET_COLLECTION_OVERRIDE:
        await async_set_collection_override(hass, entry, data, coordinator=runtime.coordinator)
    else:
        raise HomeAssistantError(f"Unknown Cinema Collections action: {action}")

    if runtime.coordinator is not None:
        await runtime.coordinator.async_request_refresh()
    return None


async def async_set_collection_override(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: Mapping[str, object],
    *,
    coordinator: CinemaCollectionsCoordinator | None = None,
) -> None:
    """Persist a Select option or service mode as a valid OverrideMode."""
    option = data.get("option")
    if isinstance(option, str):
        if option == OverrideKind.AUTOMATIC.value:
            mode, collection_id = OverrideKind.AUTOMATIC, None
        elif option == OverrideKind.DEFAULT.value:
            mode, collection_id = OverrideKind.DEFAULT, None
        else:
            _override_collection_or_error(policies_for_entry(entry), option)
            mode, collection_id = OverrideKind.EXPLICIT, option
    else:
        try:
            mode = OverrideKind(data["mode"])
        except (KeyError, TypeError, ValueError) as error:
            raise HomeAssistantError(
                "Override mode must be automatic, default, or explicit"
            ) from error
        collection_id = data.get("collection_id")
        if mode is OverrideKind.EXPLICIT:
            _override_collection_or_error(policies_for_entry(entry), collection_id)
        elif collection_id is not None:
            raise HomeAssistantError("collection_id is allowed only for an explicit override")

    options = {
        **entry.options,
        CONF_OVERRIDE_MODE: mode.value,
        CONF_OVERRIDE_COLLECTION_ID: collection_id if mode is OverrideKind.EXPLICIT else None,
    }
    hass.config_entries.async_update_entry(entry, options=options)
    active_coordinator = coordinator or _runtime(hass, entry).coordinator
    await active_coordinator.async_request_refresh()


def _key(prefix: str) -> str:
    """Generate a bounded idempotency key for one explicit user action."""
    return f"{prefix}:{uuid4()}"


def _utc_now():
    """Keep service policy resolution timezone-aware without importing HA globals."""
    from datetime import UTC, datetime

    return datetime.now(UTC)
