"""Durable, per-collection no-repeat playback history."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from random import SystemRandom
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, HistoryResetMode

_STORAGE_VERSION = 1
_STORAGE_KEY = f"{DOMAIN}.playback_history"


@dataclass(frozen=True, slots=True)
class HistorySelection:
    """The clip claim (if any) selected from one collection's current round."""

    collection_id: str
    clip_id: str | None
    round_number: int
    history_reset: bool


@dataclass(frozen=True, slots=True)
class ResetResult:
    """The collection histories cleared by an explicit or scheduled reset."""

    collection_ids: tuple[str, ...]
    reset_at: datetime


class PlaybackHistoryStore:
    """Use Home Assistant storage to atomically claim no-repeat clip selections."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        storage_key: str = _STORAGE_KEY,
        now: Callable[[], datetime] = dt_util.now,
        chooser: Callable[[tuple[str, ...]], str] | None = None,
        reset_time: time = time(0, 0),
        reset_mode: HistoryResetMode = HistoryResetMode.ON_EXHAUSTION,
    ) -> None:
        self._hass = hass
        self._now = now
        self._chooser = chooser or SystemRandom().choice
        self._reset_time = reset_time
        self._reset_mode = reset_mode
        self._store: Store[dict[str, Any]] = Store(
            hass, _STORAGE_VERSION, storage_key, atomic_writes=True
        )
        self._lock = asyncio.Lock()
        self._data: dict[str, dict[str, dict[str, Any]]] = {"collections": {}}
        self._initialized = False
        self._cancel_daily_reset: Callable[[], None] | None = None

    async def async_setup(self, *, reconcile: bool = True) -> ResetResult:
        """Load history and, in daily mode, reconcile and schedule the local reset."""
        async with self._lock:
            if self._initialized:
                return ResetResult((), self._local_now())
            loaded = await self._store.async_load()
            self._data = self._normalize(loaded)
            self._initialized = True
            now = self._local_now()
            reset_ids = self._reconcile(now) if reconcile else ()
            if reset_ids:
                await self._store.async_save(self._serialized())
            if (
                self._reset_mode is HistoryResetMode.DAILY
                and getattr(self._hass, "bus", None) is not None
                and self._cancel_daily_reset is None
            ):
                self._cancel_daily_reset = async_track_time_change(
                    self._hass,
                    self._async_daily_reset,
                    hour=self._reset_time.hour,
                    minute=self._reset_time.minute,
                    second=0,
                )
            return ResetResult(reset_ids, now)

    async def async_shutdown(self) -> None:
        """Cancel only this store's Home Assistant listener."""
        if self._cancel_daily_reset is not None:
            self._cancel_daily_reset()
            self._cancel_daily_reset = None

    async def async_select(
        self, collection_id: str, eligible_clip_ids: Sequence[str], dry_run: bool
    ) -> HistorySelection:
        """Select an eligible clip and persist its claim before returning it."""
        await self.async_setup(reconcile=not dry_run)
        eligible = tuple(dict.fromkeys(value for value in eligible_clip_ids if value))
        async with self._lock:
            now = self._local_now()
            if dry_run:
                data = self._copy_data()
                reset_ids = self._reconcile(now, data)
            else:
                data = self._data
                reset_ids = self._reconcile(now, data)

            collections = data["collections"]
            record = collections.get(collection_id)
            if record is None:
                round_number = 1
                history_reset = False
                played_ids: list[str] = []
                played: set[str] = set()
            else:
                round_number = cast(int, record["round_number"])
                history_reset = collection_id in reset_ids or bool(record["reset_pending"])
                played_ids = [
                    clip_id
                    for clip_id in cast(list[str], record["played_clip_ids"])
                    if clip_id in eligible
                ]
                played = set(played_ids)

            remaining = tuple(clip_id for clip_id in eligible if clip_id not in played)
            if eligible and not remaining:
                round_number += 1
                history_reset = True
                played_ids = []
                played.clear()
                remaining = eligible

            if not remaining:
                if reset_ids and not dry_run:
                    await self._store.async_save(self._serialized())
                return HistorySelection(collection_id, None, round_number, history_reset)

            clip_id = self._chooser(remaining)
            if clip_id not in remaining:
                raise ValueError("history chooser must return an eligible clip ID")
            if dry_run:
                return HistorySelection(collection_id, clip_id, round_number, history_reset)

            collections[collection_id] = {
                "period_start": self._period_start(now),
                "round_number": round_number,
                "played_clip_ids": [*played_ids, clip_id],
                "last_selected_clip_id": clip_id,
                "last_reset_at": record["last_reset_at"] if record is not None else None,
                "reset_pending": False,
            }
            await self._store.async_save(self._serialized())
            return HistorySelection(collection_id, clip_id, round_number, history_reset)

    async def async_reset(self, collection_id: str | None) -> ResetResult:
        """Reset exactly one collection, or every known collection when explicitly requested."""
        await self.async_setup()
        async with self._lock:
            now = self._local_now()
            collections = self._data["collections"]
            collection_ids = (
                tuple(sorted(collections)) if collection_id is None else (collection_id,)
            )
            reset_ids = tuple(
                identifier for identifier in collection_ids if identifier in collections
            )
            for identifier in reset_ids:
                collections[identifier] = self._reset_record(collections[identifier], now)
            if reset_ids:
                await self._store.async_save(self._serialized())
            return ResetResult(reset_ids, now)

    async def async_handle_daily_reset(self, now: datetime) -> ResetResult:
        """Run the daily reconciliation for a supplied timestamp; inert outside daily mode."""
        if now.tzinfo is None:
            raise ValueError("daily playback reset requires a timezone-aware timestamp")
        await self.async_setup()
        async with self._lock:
            local_now = dt_util.as_local(now)
            reset_ids = self._reconcile(local_now)
            if reset_ids:
                await self._store.async_save(self._serialized())
            return ResetResult(reset_ids, local_now)

    def snapshot(self) -> Mapping[str, Mapping[str, Any]]:
        """Return a fresh per-collection history summary without mutating state.

        The snapshot exposes the played-clip count instead of the full clip-ID
        list so entity attributes stay bounded regardless of round size. Every
        level is a plain dict (never MappingProxyType): Home Assistant's state
        serializer (orjson) rejects mappingproxy values outright, including
        nested ones, and this snapshot is consumed directly as sensor entity
        attributes.
        """
        return {
            collection_id: {
                "round_number": record["round_number"],
                "played_count": len(record["played_clip_ids"]),
                "last_selected_clip_id": record["last_selected_clip_id"],
                "last_reset_at": record["last_reset_at"],
            }
            for collection_id, record in self._data["collections"].items()
        }

    async def _async_daily_reset(self, now: datetime) -> None:
        """Reconcile the local-date boundary registered with Home Assistant."""
        await self.async_handle_daily_reset(now)

    def _local_now(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("playback history requires a timezone-aware clock")
        return dt_util.as_local(now)

    @staticmethod
    def _normalize(loaded: object) -> dict[str, dict[str, dict[str, Any]]]:
        if not isinstance(loaded, Mapping):
            return {"collections": {}}
        payload = cast(Mapping[str, object], loaded)
        raw_collections = payload.get("collections")
        if not isinstance(raw_collections, Mapping):
            return {"collections": {}}
        collections: dict[str, dict[str, Any]] = {}
        for collection_id, raw_record in cast(Mapping[object, object], raw_collections).items():
            if (
                not isinstance(collection_id, str)
                or not collection_id
                or not isinstance(raw_record, Mapping)
            ):
                continue
            record = cast(Mapping[str, object], raw_record)
            period_start = record.get("period_start")
            round_number = record.get("round_number")
            played = record.get("played_clip_ids")
            if (
                not isinstance(period_start, str)
                or isinstance(round_number, bool)
                or not isinstance(round_number, int)
                or round_number < 1
                or not isinstance(played, list)
                or not all(
                    isinstance(clip_id, str) and clip_id for clip_id in cast(list[object], played)
                )
            ):
                continue
            played_ids = [cast(str, clip_id) for clip_id in cast(list[object], played)]
            last_selected = record.get("last_selected_clip_id")
            last_reset = record.get("last_reset_at")
            if last_selected is not None and not isinstance(last_selected, str):
                continue
            if last_reset is not None and not isinstance(last_reset, str):
                continue
            collections[collection_id] = {
                "period_start": period_start,
                "round_number": round_number,
                "played_clip_ids": list(dict.fromkeys(played_ids)),
                "last_selected_clip_id": last_selected,
                "last_reset_at": last_reset,
                "reset_pending": bool(record.get("reset_pending", False)),
            }
        return {"collections": collections}

    def _reconcile(
        self, now: datetime, data: dict[str, dict[str, dict[str, Any]]] | None = None
    ) -> tuple[str, ...]:
        if self._reset_mode is not HistoryResetMode.DAILY:
            return ()
        target = self._data if data is None else data
        expected_period = self._period_start(now)
        collections = target["collections"]
        reset_ids = tuple(
            collection_id
            for collection_id, record in sorted(collections.items())
            if record["period_start"] != expected_period
        )
        for collection_id in reset_ids:
            collections[collection_id] = self._reset_record(collections[collection_id], now)
        return reset_ids

    def _period_start(self, now: datetime) -> str:
        boundary = datetime.combine(now.date(), self._reset_time, tzinfo=now.tzinfo)
        return (now.date() if now >= boundary else now.date() - timedelta(days=1)).isoformat()

    def _reset_record(self, record: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        return {
            "period_start": self._period_start(now),
            "round_number": cast(int, record["round_number"]) + 1,
            "played_clip_ids": [],
            "last_selected_clip_id": None,
            "last_reset_at": now.isoformat(),
            "reset_pending": True,
        }

    def _copy_data(self) -> dict[str, dict[str, dict[str, Any]]]:
        return {
            "collections": {
                collection_id: {
                    **record,
                    "played_clip_ids": list(cast(list[str], record["played_clip_ids"])),
                }
                for collection_id, record in self._data["collections"].items()
            }
        }

    def _serialized(self) -> dict[str, Any]:
        return {
            "collections": {
                collection_id: dict(record)
                for collection_id, record in sorted(self._data["collections"].items())
            }
        }
