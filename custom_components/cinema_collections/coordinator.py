"""Worker polling coordinator with local policy preserved during outages."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Protocol, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api_client import WorkerApiError
from .const import CONF_OVERRIDE_COLLECTION_ID, CONF_OVERRIDE_MODE, DOMAIN, PlaybackMode
from .history import PlaybackHistoryStore
from .models import WorkerClip, WorkerCollection, WorkerHealth, WorkerStatus
from .resolver import (
    CollectionPolicy,
    OverrideKind,
    OverrideMode,
    SelectionResult,
    resolve_active_collection,
)
from .scheduler import CompilationSchedule, schedules_from_mapping

_LOGGER = logging.getLogger(__name__)
_BASE_UPDATE_INTERVAL = timedelta(seconds=30)
_MAX_UPDATE_INTERVAL = timedelta(minutes=5)


class CoordinatorWorker(Protocol):
    """The bounded Worker read surface needed by the coordinator."""

    async def async_health(self) -> WorkerHealth:
        """Return compatible Worker health information."""
        ...

    async def async_status(self) -> WorkerStatus:
        """Return Worker operational status."""
        ...

    async def async_list_clips(self) -> Sequence[WorkerClip]:
        """Return the Worker catalog with live output availability."""
        ...


@dataclass(frozen=True, slots=True)
class CoordinatorSnapshot:
    """Local selection policy plus the latest safely-read Worker state."""

    active_collection_id: str | None
    reason: str
    override_rejected: bool
    status: WorkerStatus | None
    health: WorkerHealth | None
    available: bool
    error: str | None
    next_schedule: datetime | None = None
    priorities: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    clip_states: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    history: Mapping[str, Mapping[str, Any]] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def queue_depth(self) -> int | None:
        """Expose Worker queue depth without leaking storage paths."""
        return self.status.queue_depth if self.status is not None else None

    @property
    def progress(self) -> Mapping[str, object] | None:
        """Expose current job progress, if a Worker job is active."""
        if self.status is None or self.status.current_job is None:
            return None
        progress = self.status.current_job.get("progress")
        if not isinstance(progress, Mapping):
            return None
        return MappingProxyType(dict(cast(Mapping[str, object], progress)))

    @property
    def compatibility(self) -> Mapping[str, str] | None:
        """Expose only version compatibility data, never credentials."""
        if self.health is None:
            return None
        return MappingProxyType(
            {
                "worker_version": self.health.worker_version,
                "api_version": self.health.api_version,
                "min_client_version": self.health.min_client_version,
                "max_client_version": self.health.max_client_version,
            }
        )

    @property
    def latest_error(self) -> str | None:
        """Return a bounded Worker error message appropriate for state attributes."""
        if self.error:
            return self.error
        if self.status is not None and self.status.latest_errors:
            return self.status.latest_errors[0].message
        return None

    @property
    def compilation_summary(self) -> str:
        """Return a compact ready/total summary for native dashboards."""

        total = sum(self.clip_states.values())
        return f"{self.clip_states.get('ready', 0)}/{total} ready"


class CinemaCollectionsCoordinator(DataUpdateCoordinator[CoordinatorSnapshot]):
    """Poll bounded Worker status while retaining local schedule policy on disconnect."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: CoordinatorWorker,
        *,
        collections: Callable[[], Sequence[CollectionPolicy]] | None = None,
        override: Callable[[], OverrideMode],
        schedules: Callable[[], Sequence[CompilationSchedule]] | None = None,
        history: Callable[[], PlaybackHistoryStore | None] | None = None,
        entry: ConfigEntry | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=_BASE_UPDATE_INTERVAL,
            update_method=self.async_update_data,
        )
        self.client = client
        self._collections = collections
        # Policy fetched from the Worker, retained in memory across a
        # disconnect so a dropped poll does not reshuffle selection. It is
        # never written to the config entry: the Worker owns it.
        self.policies: tuple[CollectionPolicy, ...] = ()
        self.worker_collections: tuple[WorkerCollection, ...] = ()
        self._override = override
        self._schedules_source = schedules
        self._history = history
        self._now = now or (lambda: datetime.now(UTC))
        self._failure_count = 0

    async def async_update_data(self) -> CoordinatorSnapshot:
        """Read Worker state with bounded backoff, without losing local resolution."""
        now = self._now()
        history_snapshot = _history_snapshot(self._history)
        try:
            health, status, clips, collections = await asyncio.gather(
                self.client.async_health(),
                self.client.async_status(),
                _clips_request(self.client),
                _collections_request(self.client),
            )
        except WorkerApiError as error:
            self._failure_count += 1
            self.update_interval = min(
                _BASE_UPDATE_INTERVAL * (2**self._failure_count), _MAX_UPDATE_INTERVAL
            )
            selection = self._resolve(now)
            return CoordinatorSnapshot(
                active_collection_id=selection.id,
                reason=selection.reason.value,
                override_rejected=selection.override_rejected,
                status=None,
                health=None,
                available=False,
                error=str(error),
                next_schedule=_next_schedule(self.schedules(), now),
                priorities=self._priorities(),
                history=history_snapshot,
            )

        if collections:
            self.worker_collections = tuple(collections)
            self.policies = policies_from_collections(self.worker_collections)
        selection = self._resolve(now)
        self._failure_count = 0
        self.update_interval = _BASE_UPDATE_INTERVAL
        return CoordinatorSnapshot(
            active_collection_id=selection.id,
            reason=selection.reason.value,
            override_rejected=selection.override_rejected,
            status=status,
            health=health,
            available=True,
            error=None,
            next_schedule=_next_schedule(self.schedules(), now),
            priorities=self._priorities(),
            clip_states=_clip_state_counts(clips),
            history=history_snapshot,
        )

    def _resolve(self, now: datetime) -> SelectionResult:
        return resolve_active_collection(self.current_policies(), self._override(), now)

    def _priorities(self) -> Mapping[str, int]:
        return MappingProxyType({item.id: item.priority for item in self.current_policies()})

    def current_policies(self) -> tuple[CollectionPolicy, ...]:
        """Policy from the Worker, or from the caller-supplied source in tests."""
        if self._collections is not None:
            return tuple(self._collections())
        return self.policies

    def schedules(self) -> tuple[CompilationSchedule, ...]:
        """Compilation schedules the Worker stores, dispatched by Home Assistant."""
        if self._schedules_source is not None:
            return tuple(self._schedules_source())
        return tuple(
            schedule
            for record in self.worker_collections
            for schedule in schedules_from_mapping(
                {"collection_id": record.id, "schedule": dict(record.schedule)}
            )
        )


async def _clips_request(client: CoordinatorWorker) -> Sequence[WorkerClip]:
    """Return the Worker clip catalog when the client supports it."""
    method = getattr(client, "async_list_clips", None)
    if callable(method):
        return await cast("Callable[[], Awaitable[Sequence[WorkerClip]]]", method)()
    return ()


def _clip_state_counts(clips: Sequence[WorkerClip]) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for clip in clips:
        counts[clip.state] = counts.get(clip.state, 0) + 1
    return MappingProxyType(dict(sorted(counts.items())))


def _history_snapshot(
    history: Callable[[], PlaybackHistoryStore | None] | None,
) -> Mapping[str, Mapping[str, Any]]:
    """Read playback history without mutating it; a missing store yields {}."""
    if history is None:
        return MappingProxyType({})
    store = history()
    if store is None:
        return MappingProxyType({})
    return store.snapshot()


def _next_schedule(schedules: Sequence[CompilationSchedule], now: datetime) -> datetime | None:
    """Resolve the next enabled local occurrence within one weekly cycle."""

    local_now = dt_util.as_local(now)
    candidates: list[datetime] = []
    for schedule in schedules:
        if not schedule.enabled:
            continue
        for offset in range(8):
            day = local_now.date() + timedelta(days=offset)
            if day.weekday() not in schedule.weekdays:
                continue
            candidate = datetime.combine(day, schedule.local_time, tzinfo=local_now.tzinfo)
            if candidate >= local_now:
                candidates.append(candidate)
                break
    return min(candidates, default=None)


def policies_from_collections(
    records: Sequence[WorkerCollection],
) -> tuple[CollectionPolicy, ...]:
    """Turn Worker collections into the policy the resolver and scheduler read."""
    policies: list[CollectionPolicy] = []
    for record in records:
        try:
            policies.append(
                CollectionPolicy(
                    id=record.id,
                    enabled=record.enabled,
                    priority=record.priority,
                    starts_at=_policy_datetime(record.starts_at),
                    ends_at=_policy_datetime(record.ends_at),
                    is_default=record.is_default,
                    allow_manual_override=record.allow_manual_override,
                    playback_mode=PlaybackMode(record.playback_mode),
                    ordered_clip_ids=record.ordered_clip_ids,
                )
            )
        except ValueError:
            # One unusable collection must not blind the integration to the rest.
            _LOGGER.warning(
                "Cinema Collections ignored collection %s with invalid policy", record.id
            )
    return tuple(policies)


def _policy_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


async def _collections_request(client: CoordinatorWorker) -> Sequence[WorkerCollection]:
    """Return Worker collections when the client supports listing them."""
    method = getattr(client, "async_list_collections", None)
    if callable(method):
        return await cast("Callable[[], Awaitable[Sequence[WorkerCollection]]]", method)()
    return ()


def override_for_entry(entry: ConfigEntry) -> OverrideMode:
    """Read the durable UI override, falling back safely to automatic mode."""
    try:
        options = getattr(entry, "options", {})
        kind = OverrideKind(options.get(CONF_OVERRIDE_MODE, OverrideKind.AUTOMATIC.value))
        collection_id = options.get(CONF_OVERRIDE_COLLECTION_ID)
        if kind is OverrideKind.EXPLICIT:
            return OverrideMode.explicit(str(collection_id))
        if kind is OverrideKind.DEFAULT:
            return OverrideMode.default()
    except (TypeError, ValueError):
        _LOGGER.warning("Cinema Collections ignored an invalid persisted override")
    return OverrideMode.automatic()
