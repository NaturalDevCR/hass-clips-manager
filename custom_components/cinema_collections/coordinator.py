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
from .const import CONF_OVERRIDE_COLLECTION_ID, CONF_OVERRIDE_MODE, DOMAIN
from .history import PlaybackHistoryStore
from .models import WorkerClip, WorkerHealth, WorkerStatus
from .resolver import (
    CollectionPolicy,
    OverrideKind,
    OverrideMode,
    resolve_active_collection,
)
from .scheduler import CompilationSchedule
from .subentries import collection_subentries

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
        collections: Callable[[], Sequence[CollectionPolicy]],
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
        self._override = override
        self._schedules = schedules or (lambda: ())
        self._history = history
        self._now = now or (lambda: datetime.now(UTC))
        self._failure_count = 0

    async def async_update_data(self) -> CoordinatorSnapshot:
        """Read Worker state with bounded backoff, without losing local resolution."""
        now = self._now()
        policies = tuple(self._collections())
        selection = resolve_active_collection(policies, self._override(), now)
        priorities = MappingProxyType({item.id: item.priority for item in policies})
        next_schedule = _next_schedule(tuple(self._schedules()), now)
        history_snapshot = _history_snapshot(self._history)
        try:
            health, status, clips = await asyncio.gather(
                self.client.async_health(),
                self.client.async_status(),
                _clips_request(self.client),
            )
        except WorkerApiError as error:
            self._failure_count += 1
            self.update_interval = min(
                _BASE_UPDATE_INTERVAL * (2**self._failure_count), _MAX_UPDATE_INTERVAL
            )
            return CoordinatorSnapshot(
                active_collection_id=selection.id,
                reason=selection.reason.value,
                override_rejected=selection.override_rejected,
                status=None,
                health=None,
                available=False,
                error=str(error),
                next_schedule=next_schedule,
                priorities=priorities,
                history=history_snapshot,
            )

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
            next_schedule=next_schedule,
            priorities=priorities,
            clip_states=_clip_state_counts(clips),
            history=history_snapshot,
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


def policies_for_entry(entry: ConfigEntry) -> tuple[CollectionPolicy, ...]:
    """Read immutable local policies from this entry's collection subentries."""
    if not hasattr(entry, "subentries"):
        return ()
    return tuple(collection.to_policy() for collection in collection_subentries(entry))


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
