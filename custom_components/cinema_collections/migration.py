"""One-way migration of configuration from the config entry into the Worker.

Collections and processing profiles used to live in Home Assistant subentries
and were pushed to the Worker on every startup. The Worker owns them now, so
this runs once per entry: it pushes what the entry holds, reads it back, and
only then removes the subentries.

The read-back is the point. Deleting the only copy of a user's configuration on
the strength of a 2xx would lose it whenever the Worker accepted a write it did
not durably store.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api_client import WorkerApiClient, WorkerApiError
from .const import CONF_MIGRATED_TO_WORKER, SUBENTRY_COLLECTION, SUBENTRY_PROFILE

_LOGGER = logging.getLogger(__name__)

# The subentry field names, kept here so this module survives the deletion of
# the flows that wrote them.
_COLLECTION_FIELDS = (
    "name",
    "source_directory",
    "processing_profile_id",
    "enabled",
    "priority",
    "is_default",
    "allow_manual_override",
    "tags",
    "notes",
    "starts_at",
    "ends_at",
    "schedule",
    "playback_mode",
    "ordered_clip_ids",
)


def _collection_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    """Build the Worker payload for one stored collection subentry."""
    payload: dict[str, Any] = {
        "name": str(data.get("name") or ""),
        "source_directory": str(data.get("source_directory") or ""),
        "processing_profile_id": str(data.get("processing_profile_id") or ""),
        "enabled": bool(data.get("enabled", True)),
        "priority": int(data.get("priority") or 0),
        "is_default": bool(data.get("is_default", False)),
        "allow_manual_override": bool(data.get("allow_manual_override", True)),
        "tags": [str(tag) for tag in cast(list[Any], data.get("tags") or [])],
        "notes": data.get("notes"),
        "starts_at": data.get("starts_at"),
        "ends_at": data.get("ends_at"),
        "schedule": dict(cast(Mapping[str, Any], data.get("schedule") or {})),
        "playback_mode": str(data.get("playback_mode") or "random"),
        "ordered_clip_ids": [
            str(clip_id) for clip_id in cast(list[Any], data.get("ordered_clip_ids") or [])
        ],
    }
    return payload


def _matches(pushed: Mapping[str, Any], stored: Mapping[str, Any]) -> list[str]:
    """Return the field names the Worker did not store as they were sent."""
    differences: list[str] = []
    for field in _COLLECTION_FIELDS:
        if field not in stored:
            differences.append(field)
            continue
        expected = pushed[field]
        actual = stored[field]
        if isinstance(expected, list):
            actual = list(cast(list[Any], actual))
        if isinstance(expected, dict):
            actual = dict(cast(Mapping[str, Any], actual))
        if expected != actual:
            differences.append(field)
    return differences


async def _push_collection(
    client: WorkerApiClient, collection_id: str, payload: Mapping[str, Any]
) -> None:
    """Create the collection, or patch it when the Worker already knows it."""
    try:
        await client.async_create_collection(
            {"id": collection_id, **payload},
            idempotency_key=f"migrate:collection:{collection_id}:create",
        )
        return
    except WorkerApiError:
        # Already present: bring it up to date at its current revision instead.
        pass
    records = {record.id: record for record in await client.async_list_collections()}
    existing = records.get(collection_id)
    if existing is None:
        raise WorkerApiError(f"the Worker did not accept collection {collection_id}")
    await client.async_patch_collection(
        collection_id,
        existing.revision,
        dict(payload),
        idempotency_key=f"migrate:collection:{collection_id}:patch:{existing.revision}",
    )


async def async_migrate_subentries(
    hass: HomeAssistant, entry: ConfigEntry, client: WorkerApiClient
) -> bool:
    """Move this entry's configuration into the Worker, once.

    Returns True when the entry is migrated and its subentries are gone, and
    False when anything went wrong and nothing was removed.
    """
    if entry.data.get(CONF_MIGRATED_TO_WORKER):
        return True
    subentries = getattr(entry, "subentries", {})
    collections = {
        subentry_id: subentry
        for subentry_id, subentry in subentries.items()
        if subentry.subentry_type == SUBENTRY_COLLECTION
    }
    profiles = {
        subentry_id: subentry
        for subentry_id, subentry in subentries.items()
        if subentry.subentry_type == SUBENTRY_PROFILE
    }
    if not collections and not profiles:
        _mark_migrated(hass, entry)
        return True

    pushed_collections: dict[str, dict[str, Any]] = {}
    pushed_profiles: dict[str, Mapping[str, Any]] = {}
    try:
        # Profiles first: a collection references one, and the Worker refuses a
        # collection whose profile it does not know.
        for subentry in profiles.values():
            profile_id = str(subentry.data.get("profile_id") or subentry.unique_id or "")
            settings = cast(Mapping[str, Any], subentry.data.get("settings") or {})
            await _push_profile(client, profile_id, str(subentry.data.get("name") or ""), settings)
            pushed_profiles[profile_id] = settings
        for subentry in collections.values():
            collection_id = str(subentry.data.get("collection_id") or subentry.unique_id or "")
            payload = _collection_payload(subentry.data)
            # Log the whole payload before anything is deleted, so a user can
            # recover it from the log if the Worker later loses it.
            _LOGGER.info(
                "Cinema Collections is migrating collection %s into the Worker: %s",
                collection_id,
                payload,
            )
            await _push_collection(client, collection_id, payload)
            pushed_collections[collection_id] = payload

        stored_collections = {record.id: record for record in await client.async_list_collections()}
        stored_profiles = await client.async_list_profile_records()
    except (WorkerApiError, KeyError, TypeError, ValueError) as error:
        _LOGGER.error(
            "Cinema Collections kept its configuration in Home Assistant: "
            "the Worker did not accept the migration (%s)",
            error,
        )
        return False

    for collection_id, payload in pushed_collections.items():
        record = stored_collections.get(collection_id)
        if record is None:
            _LOGGER.error(
                "Cinema Collections kept its configuration: the Worker did not store collection %s",
                collection_id,
            )
            return False
        differences = _matches(payload, _as_mapping(record))
        if differences:
            _LOGGER.error(
                "Cinema Collections kept its configuration: the Worker stored collection %s "
                "with different %s",
                collection_id,
                ", ".join(differences),
            )
            return False

    for profile_id, settings in pushed_profiles.items():
        if profile_id not in stored_profiles:
            _LOGGER.error(
                "Cinema Collections kept its configuration: the Worker did not store profile %s",
                profile_id,
            )
            return False
        stored_settings = stored_profiles[profile_id].get("settings")
        if not isinstance(stored_settings, Mapping) or dict(
            cast(Mapping[str, Any], stored_settings)
        ) != dict(settings):
            _LOGGER.error(
                "Cinema Collections kept its configuration: the Worker stored profile %s "
                "with different settings",
                profile_id,
            )
            return False

    for subentry in list(collections.values()) + list(profiles.values()):
        hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    _mark_migrated(hass, entry)
    _LOGGER.info(
        "Cinema Collections moved %d collections and %d profiles into the Worker",
        len(pushed_collections),
        len(pushed_profiles),
    )
    return True


async def _push_profile(
    client: WorkerApiClient, profile_id: str, name: str, settings: Mapping[str, Any]
) -> None:
    payload = {"id": profile_id, "name": name, "settings": dict(settings)}
    try:
        await client.async_create_profile(
            payload, idempotency_key=f"migrate:profile:{profile_id}:create"
        )
        return
    except WorkerApiError:
        pass
    records = await client.async_list_profile_records()
    stored = records.get(profile_id)
    if stored is None:
        raise WorkerApiError(f"the Worker did not accept profile {profile_id}")
    revision = stored.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise WorkerApiError(f"the Worker reported no revision for profile {profile_id}")
    await client.async_patch_profile(
        profile_id,
        revision,
        {"name": name, "settings": dict(settings)},
        idempotency_key=f"migrate:profile:{profile_id}:patch:{revision}",
    )


def _as_mapping(record: Any) -> Mapping[str, Any]:
    """Read a WorkerCollection as the field mapping the comparison expects."""
    return {
        "name": record.name,
        "source_directory": getattr(record, "source_directory", None),
        "processing_profile_id": getattr(record, "processing_profile_id", None),
        "enabled": record.enabled,
        "priority": record.priority,
        "is_default": record.is_default,
        "allow_manual_override": record.allow_manual_override,
        "tags": list(getattr(record, "tags", ())),
        "notes": getattr(record, "notes", None),
        "starts_at": record.starts_at,
        "ends_at": record.ends_at,
        "schedule": dict(record.schedule),
        "playback_mode": record.playback_mode,
        "ordered_clip_ids": list(record.ordered_clip_ids),
    }


def _mark_migrated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_MIGRATED_TO_WORKER: True}
    )
