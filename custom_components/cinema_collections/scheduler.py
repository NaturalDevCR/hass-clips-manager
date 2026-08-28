"""Durable local compilation schedule dispatching."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from typing import Protocol, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api_client import WorkerApiClient
from .const import CONF_SCHEDULE_RUN_TOKENS


class RunTokenStore(Protocol):
    """A durable occurrence state store."""

    async def async_begin(self, token: str) -> bool:
        """Mark an occurrence pending unless it has already succeeded."""
        ...

    async def async_succeed(self, token: str) -> None:
        """Record Worker acceptance for an occurrence."""
        ...

    async def async_fail(self, token: str) -> None:
        """Record a failed dispatch that may safely be retried."""
        ...


@dataclass(slots=True)
class InMemoryRunTokenStore:
    """Simple token store useful for tests and ephemeral callers."""

    states: dict[str, str]

    def __init__(self, tokens: set[str] | None = None) -> None:
        self.states = {token: "succeeded" for token in tokens or set()}

    async def async_begin(self, token: str) -> bool:
        if self.states.get(token) == "succeeded":
            return False
        self.states[token] = "pending"
        return True

    async def async_succeed(self, token: str) -> None:
        self.states[token] = "succeeded"

    async def async_fail(self, token: str) -> None:
        self.states[token] = "failed"


class ConfigEntryRunTokenStore:
    """Store occurrence claims in config-entry options across Home Assistant restarts."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    def _states(self) -> dict[str, str]:
        configured: object = self._entry.options.get(CONF_SCHEDULE_RUN_TOKENS, ())
        # Version one stored a list of completed tokens. Preserve those claims
        # while migrating in place to explicit pending/failed/succeeded states.
        if isinstance(configured, Mapping):
            return {
                str(key): str(value)
                for key, value in cast(Mapping[object, object], configured).items()
                if isinstance(key, str) and value in {"pending", "failed", "succeeded"}
            }
        if isinstance(configured, (list, tuple, set)) and all(
            isinstance(value, str) for value in cast(Sequence[object], configured)
        ):
            return {str(value): "succeeded" for value in cast(Sequence[object], configured)}
        return {}

    def _persist(self, states: Mapping[str, str]) -> None:
        self._hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_SCHEDULE_RUN_TOKENS: dict(sorted(states.items()))},
        )

    async def async_begin(self, token: str) -> bool:
        states = self._states()
        if states.get(token) == "succeeded":
            return False
        states[token] = "pending"
        self._persist(states)
        return True

    async def async_succeed(self, token: str) -> None:
        states = self._states()
        states[token] = "succeeded"
        self._persist(states)

    async def async_fail(self, token: str) -> None:
        states = self._states()
        states[token] = "failed"
        self._persist(states)


@dataclass(frozen=True, slots=True)
class CompilationSchedule:
    """One collection's local recurring compilation schedule."""

    collection_id: str
    enabled: bool = True
    weekdays: tuple[int, ...] = ()
    local_time: time = time(0, 0)
    strategy: str = "scan_and_compile_changed_or_missing"
    skip_if_processing: bool = True

    def __post_init__(self) -> None:
        if not self.collection_id:
            raise ValueError("schedule requires a collection ID")
        if any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("schedule weekdays must use Python weekday values 0 through 6")
        if self.strategy not in {
            "scan_and_compile_changed_or_missing",
            "compile_stale_only",
            "scan_only",
        }:
            raise ValueError("unsupported compilation strategy")


type ScheduleSource = Sequence[CompilationSchedule] | Callable[[], Sequence[CompilationSchedule]]


@dataclass(frozen=True, slots=True)
class ScheduledRun:
    """A locally claimed Worker schedule occurrence."""

    collection_id: str
    token: str
    strategy: str


class CompilationScheduler:
    """Dispatch due schedules once using durable local occurrence tokens."""

    def __init__(
        self,
        client: WorkerApiClient,
        schedules: ScheduleSource,
        token_store: RunTokenStore,
    ) -> None:
        self._client = client
        self._schedules = schedules
        self._token_store = token_store

    def _current_schedules(self) -> tuple[CompilationSchedule, ...]:
        source = self._schedules
        return tuple(source() if callable(source) else source)

    @staticmethod
    def _token(schedule: CompilationSchedule, now: datetime) -> str:
        return f"{schedule.collection_id}:{now.strftime('%Y-%m-%dT%H:%M%z')}"

    async def async_handle_due(self, now: datetime) -> list[ScheduledRun]:
        """Submit every currently due occurrence that has not been claimed before."""
        if now.tzinfo is None:
            raise ValueError("scheduler requires a timezone-aware timestamp")
        local_now = dt_util.as_local(now)
        runs: list[ScheduledRun] = []
        for schedule in sorted(self._current_schedules(), key=lambda item: item.collection_id):
            if (
                not schedule.enabled
                or local_now.weekday() not in schedule.weekdays
                or (local_now.hour, local_now.minute)
                != (schedule.local_time.hour, schedule.local_time.minute)
            ):
                continue
            token = self._token(schedule, local_now)
            if not await self._token_store.async_begin(token):
                continue
            try:
                if schedule.strategy == "scan_only":
                    await self._client.async_scan(
                        collection_ids=[schedule.collection_id], idempotency_key=token
                    )
                else:
                    await self._client.async_compile(
                        schedule.collection_id,
                        strategy=schedule.strategy,
                        skip_if_processing=schedule.skip_if_processing,
                        idempotency_key=token,
                    )
            except Exception:
                await self._token_store.async_fail(token)
                raise
            await self._token_store.async_succeed(token)
            runs.append(ScheduledRun(schedule.collection_id, token, schedule.strategy))
        return runs


def schedules_from_mapping(values: Mapping[str, object]) -> tuple[CompilationSchedule, ...]:
    """Deserialize schedule values stored in a collection config subentry."""
    raw_schedule: object = values.get("schedule")
    if not isinstance(raw_schedule, Mapping):
        return ()
    raw = cast(Mapping[str, object], raw_schedule)
    local_time: object = raw.get("local_time")
    if not isinstance(local_time, str):
        return ()
    try:
        parsed_time = time.fromisoformat(local_time)
    except ValueError:
        return ()
    weekdays: object = raw.get("weekdays", ())
    if not isinstance(weekdays, (list, tuple)) or any(
        not isinstance(day, int) for day in cast(Sequence[object], weekdays)
    ):
        return ()
    weekday_values = tuple(cast(int, day) for day in cast(Sequence[object], weekdays))
    collection_id: object = values.get("collection_id")
    if not isinstance(collection_id, str):
        return ()
    return (
        CompilationSchedule(
            collection_id=collection_id,
            enabled=bool(raw.get("enabled", False)),
            weekdays=weekday_values,
            local_time=parsed_time,
            strategy=str(raw.get("strategy", "scan_and_compile_changed_or_missing")),
            skip_if_processing=bool(raw.get("skip_if_processing", True)),
        ),
    )
