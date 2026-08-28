"""Config-subentry persistence and Worker revision synchronization."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Any, cast
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigSubentry,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api_client import WorkerApiClient, WorkerApiError
from .const import CONF_ENDPOINT, CONF_TOKEN, SUBENTRY_COLLECTION, SUBENTRY_PROFILE
from .resolver import CollectionPolicy
from .scheduler import CompilationSchedule, schedules_from_mapping

_COLLECTION_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class WorkerValidationError(ValueError):
    """A Worker validation or optimistic-concurrency error safe for config flows."""


def _check_identifier(value: str, label: str) -> str:
    if not _COLLECTION_ID.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase URL-safe slug")
    return value


def _optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        raise ValueError("schedule window timestamps must be timezone-aware ISO 8601 values")
    return parsed


@dataclass(frozen=True, slots=True)
class CollectionSubentryData:
    """Durable integration configuration for a stable Worker collection ID."""

    collection_id: str
    name: str
    source_directory: str
    processing_profile_id: str
    enabled: bool = True
    priority: int = 0
    starts_at: str | None = None
    ends_at: str | None = None
    is_default: bool = False
    allow_manual_override: bool = True
    tags: tuple[str, ...] = ()
    notes: str | None = None
    schedule: Mapping[str, object] = field(default_factory=lambda: {})
    worker_revision: int | None = None

    def __post_init__(self) -> None:
        _check_identifier(self.collection_id, "collection ID")
        _check_identifier(self.processing_profile_id, "processing profile ID")
        if not self.name.strip() or not self.source_directory.strip():
            raise ValueError("collection name and source directory are required")
        start = _optional_datetime(self.starts_at)
        end = _optional_datetime(self.ends_at)
        if start is not None and end is not None and start > end:
            raise ValueError("schedule window end must not precede its start")
        if self.worker_revision is not None and self.worker_revision < 1:
            raise ValueError("Worker revision must be positive")

    def with_updates(self, **changes: object) -> CollectionSubentryData:
        """Return changed immutable policy data while preserving its stable ID."""
        if "collection_id" in changes and changes["collection_id"] != self.collection_id:
            raise ValueError("collection IDs are immutable")
        return replace(self, **changes)

    def as_dict(self) -> dict[str, object]:
        """Serialize only durable, JSON-compatible config-subentry fields."""
        return {
            "collection_id": self.collection_id,
            "name": self.name,
            "source_directory": self.source_directory,
            "processing_profile_id": self.processing_profile_id,
            "enabled": self.enabled,
            "priority": self.priority,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "is_default": self.is_default,
            "allow_manual_override": self.allow_manual_override,
            "tags": list(self.tags),
            "notes": self.notes,
            "schedule": dict(self.schedule),
            "worker_revision": self.worker_revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> CollectionSubentryData:
        """Deserialize a persisted collection subentry."""
        raw_tags: object = data.get("tags", ())
        raw_schedule: object = data.get("schedule", {})
        if not isinstance(raw_tags, (list, tuple)) or not all(
            isinstance(tag, str) for tag in cast(tuple[object, ...] | list[object], raw_tags)
        ):
            raise ValueError("collection tags must be strings")
        if not isinstance(raw_schedule, Mapping):
            raise ValueError("collection schedule must be an object")
        tags = tuple(cast(str, tag) for tag in cast(tuple[object, ...] | list[object], raw_tags))
        schedule = dict(cast(Mapping[str, object], raw_schedule))
        starts_at = data.get("starts_at")
        ends_at = data.get("ends_at")
        notes = data.get("notes")
        revision = data.get("worker_revision")
        return cls(
            collection_id=str(data["collection_id"]),
            name=str(data["name"]),
            source_directory=str(data["source_directory"]),
            processing_profile_id=str(data["processing_profile_id"]),
            enabled=bool(data.get("enabled", True)),
            priority=int(str(data.get("priority", 0))),
            starts_at=starts_at if isinstance(starts_at, str) else None,
            ends_at=ends_at if isinstance(ends_at, str) else None,
            is_default=bool(data.get("is_default", False)),
            allow_manual_override=bool(data.get("allow_manual_override", True)),
            tags=tags,
            notes=notes if isinstance(notes, str) else None,
            schedule=schedule,
            worker_revision=revision if isinstance(revision, int) else None,
        )

    def to_policy(self) -> CollectionPolicy:
        """Build the resolver's local collection policy."""
        return CollectionPolicy(
            id=self.collection_id,
            enabled=self.enabled,
            priority=self.priority,
            starts_at=_optional_datetime(self.starts_at),
            ends_at=_optional_datetime(self.ends_at),
            is_default=self.is_default,
            allow_manual_override=self.allow_manual_override,
        )

    def schedules(self) -> tuple[CompilationSchedule, ...]:
        """Build an optional local schedule from this collection policy."""
        return schedules_from_mapping(self.as_dict())

    def worker_create_payload(self) -> dict[str, object]:
        """Return only fields accepted by the Worker collection-create contract."""
        return {
            "id": self.collection_id,
            "name": self.name,
            "source_directory": self.source_directory,
            "processing_profile_id": self.processing_profile_id,
            "enabled": self.enabled,
            "is_default": self.is_default,
        }

    def worker_patch_payload(self) -> dict[str, object]:
        """Return mutable Worker collection fields, excluding local-only policy."""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "priority": self.priority,
            "source_directory": self.source_directory,
            "processing_profile_id": self.processing_profile_id,
            "is_default": self.is_default,
            "allow_manual_override": self.allow_manual_override,
            "tags": list(self.tags),
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class ProfileSubentryData:
    """Durable integration configuration for a Worker processing profile."""

    profile_id: str
    name: str
    settings: Mapping[str, object]
    worker_revision: int | None = None

    def __post_init__(self) -> None:
        _check_identifier(self.profile_id, "profile ID")
        if not self.name.strip():
            raise ValueError("profile name and settings are required")
        if self.worker_revision is not None and self.worker_revision < 1:
            raise ValueError("Worker revision must be positive")

    def with_updates(self, **changes: object) -> ProfileSubentryData:
        if "profile_id" in changes and changes["profile_id"] != self.profile_id:
            raise ValueError("profile IDs are immutable")
        return replace(self, **changes)

    def as_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "settings": dict(self.settings),
            "worker_revision": self.worker_revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ProfileSubentryData:
        raw_settings: object = data.get("settings")
        if not isinstance(raw_settings, Mapping):
            raise ValueError("profile settings must be an object")
        settings = dict(cast(Mapping[str, object], raw_settings))
        revision = data.get("worker_revision")
        return cls(
            profile_id=str(data["profile_id"]),
            name=str(data["name"]),
            settings=settings,
            worker_revision=revision if isinstance(revision, int) else None,
        )


def _revision(response: Mapping[str, object]) -> int:
    revision = response.get("revision")
    if isinstance(revision, int) and revision >= 1:
        return revision
    raise WorkerValidationError("Worker response did not include a valid revision")


async def async_sync_collection(
    client: WorkerApiClient, collection: CollectionSubentryData
) -> CollectionSubentryData:
    """Create or revision-patch a Worker collection before saving local policy."""
    key = str(uuid4())
    try:
        if collection.worker_revision is None:
            response = await client.async_create_collection(
                collection.worker_create_payload(), idempotency_key=key
            )
            revision = _revision(response)
            # The Worker create contract intentionally has a small safe surface.
            # Persist any remaining Worker-owned settings through its revision API.
            if (
                collection.priority != 0
                or not collection.allow_manual_override
                or collection.tags
                or collection.notes is not None
            ):
                response = await client.async_patch_collection(
                    collection.collection_id,
                    revision,
                    collection.worker_patch_payload(),
                    idempotency_key=str(uuid4()),
                )
        else:
            response = await client.async_patch_collection(
                collection.collection_id,
                collection.worker_revision,
                collection.worker_patch_payload(),
                idempotency_key=key,
            )
    except (WorkerApiError, WorkerValidationError) as error:
        raise WorkerValidationError(str(error)) from error
    return collection.with_updates(worker_revision=_revision(response))


async def async_sync_profile(
    client: WorkerApiClient, profile: ProfileSubentryData
) -> ProfileSubentryData:
    """Create or revision-patch a Worker profile before saving local policy."""
    key = str(uuid4())
    try:
        if profile.worker_revision is None:
            response = await client.async_create_profile(
                {
                    "id": profile.profile_id,
                    "name": profile.name,
                    "settings": dict(profile.settings),
                },
                idempotency_key=key,
            )
        else:
            response = await client.async_patch_profile(
                profile.profile_id,
                profile.worker_revision,
                {"name": profile.name, "settings": dict(profile.settings)},
                idempotency_key=key,
            )
    except (WorkerApiError, WorkerValidationError) as error:
        raise WorkerValidationError(str(error)) from error
    return profile.with_updates(worker_revision=_revision(response))


def collection_subentries(entry: ConfigEntry) -> tuple[CollectionSubentryData, ...]:
    """Read all durable collection policies from Home Assistant subentries."""
    return tuple(
        CollectionSubentryData.from_dict(subentry.data)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_COLLECTION
    )


def profile_subentries(entry: ConfigEntry) -> tuple[ProfileSubentryData, ...]:
    """Read all durable profile configuration from Home Assistant subentries."""
    return tuple(
        ProfileSubentryData.from_dict(subentry.data)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_PROFILE
    )


def worker_client_for_entry(hass: HomeAssistant, entry: ConfigEntry) -> WorkerApiClient:
    """Reuse runtime client when loaded, otherwise make a short-lived flow client."""
    runtime = hass.data.get("cinema_collections", {}).get(entry.entry_id)
    if runtime is not None:
        return runtime.client
    return WorkerApiClient(
        str(entry.data[CONF_ENDPOINT]), str(entry.data[CONF_TOKEN]), async_get_clientsession(hass)
    )


def async_add_collection_subentry(
    hass: HomeAssistant, entry: ConfigEntry, collection: CollectionSubentryData
) -> None:
    """Persist a worker-synchronized collection policy under a stable unique ID."""
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType(collection.as_dict()),
            subentry_type=SUBENTRY_COLLECTION,
            title=collection.name,
            unique_id=collection.collection_id,
        ),
    )


def async_update_collection_subentry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry: ConfigSubentry,
    collection: CollectionSubentryData,
) -> None:
    """Persist a revision-checked collection policy update."""
    hass.config_entries.async_update_subentry(
        entry, subentry, data=collection.as_dict(), title=collection.name
    )


def parse_profile_settings(value: str) -> Mapping[str, object]:
    """Parse the flow's JSON profile editor without accepting arbitrary YAML."""
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise WorkerValidationError("profile settings must be valid JSON") from error
    if not isinstance(parsed, Mapping):
        raise WorkerValidationError("profile settings must be a JSON object")
    return dict(cast(Mapping[str, object], parsed))


class CollectionSubentryFlow(config_entries.ConfigSubentryFlow):
    """Create or update a collection policy and synchronize its Worker revision."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Create a collection subentry from native UI fields."""
        return await self._async_step_configure(user_input, existing=None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Revision-patch the selected collection subentry."""
        existing = CollectionSubentryData.from_dict(self._get_reconfigure_subentry().data)
        return await self._async_step_configure(user_input, existing=existing)

    async def _async_step_configure(
        self, user_input: dict[str, Any] | None, existing: CollectionSubentryData | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                candidate = _collection_from_flow_input(user_input, existing)
                synchronized = await async_sync_collection(
                    worker_client_for_entry(self.hass, self._get_entry()), candidate
                )
            except WorkerValidationError:
                errors["base"] = "worker_validation"
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_collection"
            else:
                if existing is None:
                    return self.async_create_entry(
                        title=synchronized.name,
                        data=synchronized.as_dict(),
                        unique_id=synchronized.collection_id,
                    )
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    title=synchronized.name,
                    data=synchronized.as_dict(),
                )
        return self.async_show_form(
            step_id="reconfigure" if existing is not None else "user",
            data_schema=_collection_schema(existing),
            errors=errors,
        )


class ProfileSubentryFlow(config_entries.ConfigSubentryFlow):
    """Create or update a Worker processing-profile subentry."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_step_configure(user_input, existing=None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        existing = ProfileSubentryData.from_dict(self._get_reconfigure_subentry().data)
        return await self._async_step_configure(user_input, existing=existing)

    async def _async_step_configure(
        self, user_input: dict[str, Any] | None, existing: ProfileSubentryData | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                candidate = _profile_from_flow_input(user_input, existing)
                synchronized = await async_sync_profile(
                    worker_client_for_entry(self.hass, self._get_entry()), candidate
                )
            except WorkerValidationError:
                errors["base"] = "worker_validation"
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_profile"
            else:
                if existing is None:
                    return self.async_create_entry(
                        title=synchronized.name,
                        data=synchronized.as_dict(),
                        unique_id=synchronized.profile_id,
                    )
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    title=synchronized.name,
                    data=synchronized.as_dict(),
                )
        return self.async_show_form(
            step_id="reconfigure" if existing is not None else "user",
            data_schema=_profile_schema(existing),
            errors=errors,
        )


def _collection_schema(existing: CollectionSubentryData | None) -> vol.Schema:
    values = existing.as_dict() if existing else {}
    raw_schedule: object = values.get("schedule", {})
    schedule: dict[str, object] = (
        dict(cast(Mapping[str, object], raw_schedule)) if isinstance(raw_schedule, Mapping) else {}
    )
    raw_tags: object = values.get("tags", ())
    tags: tuple[object, ...] = (
        tuple(cast(list[object] | tuple[object, ...], raw_tags))
        if isinstance(raw_tags, (list, tuple))
        else ()
    )
    return vol.Schema(
        {
            vol.Required("collection_id", default=values.get("collection_id", "")): str,
            vol.Required("name", default=values.get("name", "")): str,
            vol.Required("source_directory", default=values.get("source_directory", "")): str,
            vol.Required(
                "processing_profile_id", default=values.get("processing_profile_id", "")
            ): str,
            vol.Required("enabled", default=values.get("enabled", True)): bool,
            vol.Required("priority", default=values.get("priority", 0)): vol.Coerce(int),
            vol.Optional("starts_at", default=values.get("starts_at") or ""): str,
            vol.Optional("ends_at", default=values.get("ends_at") or ""): str,
            vol.Required("is_default", default=values.get("is_default", False)): bool,
            vol.Required(
                "allow_manual_override", default=values.get("allow_manual_override", True)
            ): bool,
            vol.Optional("tags", default=", ".join(str(tag) for tag in tags)): str,
            vol.Optional("notes", default=values.get("notes") or ""): str,
            vol.Required("schedule_enabled", default=schedule.get("enabled", False)): bool,
            vol.Optional(
                "schedule_weekdays",
                default=", ".join(str(day) for day in _weekday_values(schedule.get("weekdays"))),
            ): str,
            vol.Optional("schedule_time", default=schedule.get("local_time", "00:00")): str,
            vol.Required(
                "schedule_strategy",
                default=schedule.get("strategy", "scan_and_compile_changed_or_missing"),
            ): vol.In(["scan_and_compile_changed_or_missing", "compile_stale_only", "scan_only"]),
            vol.Required(
                "schedule_skip_if_processing", default=schedule.get("skip_if_processing", True)
            ): bool,
        }
    )


def _weekday_values(value: object) -> tuple[int, ...]:
    """Return a safe weekday tuple for a config-flow field default."""
    if not isinstance(value, (list, tuple)):
        return ()
    values = cast(list[object] | tuple[object, ...], value)
    if not all(isinstance(day, int) for day in values):
        return ()
    return tuple(cast(int, day) for day in values)


def _collection_from_flow_input(
    user_input: Mapping[str, object], existing: CollectionSubentryData | None
) -> CollectionSubentryData:
    collection_id = str(user_input["collection_id"])
    if existing is not None and collection_id != existing.collection_id:
        raise ValueError("collection IDs are immutable")
    weekdays = tuple(
        int(value.strip())
        for value in str(user_input.get("schedule_weekdays", "")).split(",")
        if value.strip()
    )
    schedule = {
        "enabled": bool(user_input["schedule_enabled"]),
        "weekdays": list(weekdays),
        "local_time": str(user_input.get("schedule_time", "00:00")),
        "strategy": str(user_input["schedule_strategy"]),
        "skip_if_processing": bool(user_input["schedule_skip_if_processing"]),
    }
    # Validate schedule shape now, returning errors in the same flow that supplied it.
    CompilationSchedule(
        collection_id=collection_id,
        enabled=bool(schedule["enabled"]),
        weekdays=weekdays,
        local_time=datetime.strptime(str(schedule["local_time"]), "%H:%M").time(),
        strategy=str(schedule["strategy"]),
        skip_if_processing=bool(schedule["skip_if_processing"]),
    )
    return CollectionSubentryData(
        collection_id=collection_id,
        name=str(user_input["name"]),
        source_directory=str(user_input["source_directory"]),
        processing_profile_id=str(user_input["processing_profile_id"]),
        enabled=bool(user_input["enabled"]),
        priority=int(str(user_input["priority"])),
        starts_at=str(user_input.get("starts_at") or "") or None,
        ends_at=str(user_input.get("ends_at") or "") or None,
        is_default=bool(user_input["is_default"]),
        allow_manual_override=bool(user_input["allow_manual_override"]),
        tags=tuple(
            tag.strip() for tag in str(user_input.get("tags", "")).split(",") if tag.strip()
        ),
        notes=str(user_input.get("notes") or "") or None,
        schedule=schedule,
        worker_revision=existing.worker_revision if existing else None,
    )


def _profile_schema(existing: ProfileSubentryData | None) -> vol.Schema:
    values = existing.as_dict() if existing else {}
    return vol.Schema(
        {
            vol.Required("profile_id", default=values.get("profile_id", "")): str,
            vol.Required("name", default=values.get("name", "")): str,
            vol.Required(
                "settings", default=json.dumps(values.get("settings", {}), sort_keys=True)
            ): str,
        }
    )


def _profile_from_flow_input(
    user_input: Mapping[str, object], existing: ProfileSubentryData | None
) -> ProfileSubentryData:
    profile_id = str(user_input["profile_id"])
    if existing is not None and profile_id != existing.profile_id:
        raise ValueError("profile IDs are immutable")
    return ProfileSubentryData(
        profile_id=profile_id,
        name=str(user_input["name"]),
        settings=parse_profile_settings(str(user_input["settings"])),
        worker_revision=existing.worker_revision if existing else None,
    )
