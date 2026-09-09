"""Authenticated Home Assistant bridge for visual playback-order editing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from aiohttp import web
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.helpers.http import HomeAssistantView

from .const import DOMAIN, SUBENTRY_COLLECTION, PlaybackMode, normalize_clip_order
from .subentries import CollectionSubentryData, async_update_collection_subentry

ORDER_VIEW_URL = "/api/cinema_collections/order"


def _loaded_entries(hass: Any) -> Mapping[str, Any]:
    runtimes = getattr(hass, "data", {}).get(DOMAIN, {})
    if not isinstance(runtimes, Mapping):
        return {}
    return cast(Mapping[str, Any], runtimes)


def _entry_for(hass: Any, entry_id: str | None) -> tuple[ConfigEntry, Any]:
    runtimes = _loaded_entries(hass)
    if entry_id is not None:
        runtime = runtimes.get(entry_id)
        if runtime is None:
            raise ValueError(f"Unknown Cinema Collections entry_id: {entry_id}")
        return runtime.entry, runtime
    if len(runtimes) != 1:
        if not runtimes:
            raise ValueError("No Cinema Collections entry is loaded")
        raise ValueError("entry_id is required when multiple Cinema Collections entries are loaded")
    runtime = next(iter(runtimes.values()))
    return runtime.entry, runtime


def _collection_for(
    entry: ConfigEntry, collection_id: str
) -> tuple[ConfigSubentry, CollectionSubentryData]:
    if not collection_id.strip():
        raise ValueError("collection_id must not be empty")
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_COLLECTION:
            continue
        collection = CollectionSubentryData.from_dict(subentry.data)
        if collection.collection_id == collection_id:
            return subentry, collection
    raise ValueError(f"Unknown Cinema Collections collection: {collection_id}")


def _result(entry: ConfigEntry, collection: CollectionSubentryData) -> dict[str, object]:
    return {
        "entry_id": entry.entry_id,
        "collection_id": collection.collection_id,
        "playback_mode": collection.playback_mode.value,
        "ordered_clip_ids": list(collection.ordered_clip_ids),
    }


async def async_get_collection_order(
    hass: Any, *, collection_id: str, entry_id: str | None = None
) -> dict[str, object]:
    """Return the current durable playback policy for one collection."""
    entry, _runtime = _entry_for(hass, entry_id)
    _subentry, collection = _collection_for(entry, collection_id)
    return _result(entry, collection)


async def async_save_collection_order(
    hass: Any,
    *,
    collection_id: str,
    ordered_clip_ids: Sequence[str],
    entry_id: str | None = None,
) -> dict[str, object]:
    """Persist a custom order in the existing Home Assistant collection subentry."""
    ordered = normalize_clip_order(list(ordered_clip_ids))
    if not ordered:
        raise ValueError("custom playback requires at least one clip ID")
    entry, runtime = _entry_for(hass, entry_id)
    subentry, collection = _collection_for(entry, collection_id)
    updated = collection.with_updates(
        playback_mode=PlaybackMode.CUSTOM,
        ordered_clip_ids=ordered,
    )
    async_update_collection_subentry(hass, entry, subentry, updated)
    coordinator = getattr(runtime, "coordinator", None)
    if coordinator is not None:
        await coordinator.async_request_refresh()
    return _result(entry, updated)


class CinemaCollectionsOrderView(HomeAssistantView):
    """Authenticated same-origin API used by the Worker App's order editor."""

    url = ORDER_VIEW_URL
    name = "api:cinema_collections:order"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Return one collection's saved order."""
        try:
            result = await async_get_collection_order(
                request.app["hass"],
                collection_id=request.query.get("collection_id", ""),
                entry_id=request.query.get("entry_id"),
            )
        except ValueError as error:
            return self.json_message(str(error), status_code=400)
        return self.json(result)

    async def post(self, request: web.Request) -> web.Response:
        """Save one collection's custom order."""
        try:
            payload = await request.json()
            if not isinstance(payload, Mapping):
                raise ValueError("order payload must be an object")
            values = cast(Mapping[str, object], payload)
            raw_ids_value = values.get("ordered_clip_ids")
            if not isinstance(raw_ids_value, list):
                raise ValueError("ordered_clip_ids must be a list of strings")
            raw_ids = cast(list[object], raw_ids_value)
            if not all(isinstance(item, str) for item in raw_ids):
                raise ValueError("ordered_clip_ids must be a list of strings")
            collection_id = values.get("collection_id")
            if not isinstance(collection_id, str):
                raise ValueError("collection_id must be a string")
            raw_entry_id = values.get("entry_id")
            if raw_entry_id is not None and not isinstance(raw_entry_id, str):
                raise ValueError("entry_id must be a string")
            result = await async_save_collection_order(
                request.app["hass"],
                collection_id=collection_id,
                ordered_clip_ids=tuple(cast(str, item) for item in raw_ids),
                entry_id=raw_entry_id,
            )
        except (TypeError, ValueError, web.HTTPError) as error:
            return self.json_message(str(error), status_code=400)
        return self.json(result)
