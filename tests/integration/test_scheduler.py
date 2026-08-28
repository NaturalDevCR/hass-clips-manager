"""Local scheduler persistence and idempotency coverage."""

from __future__ import annotations

from datetime import datetime, time

import pytest
from homeassistant.util import dt as dt_util

from custom_components.cinema_collections.scheduler import (
    CompilationSchedule,
    CompilationScheduler,
    InMemoryRunTokenStore,
)


class Client:
    """Records the Worker requests made by a scheduler."""

    def __init__(self) -> None:
        self.compiles: list[dict[str, object]] = []

    async def async_compile(self, collection_id: str, **kwargs: object) -> object:
        self.compiles.append({"collection_id": collection_id, **kwargs})
        return {"id": "job"}

    async def async_scan(self, **kwargs: object) -> object:
        raise AssertionError("scan-only was not scheduled")


@pytest.mark.asyncio
async def test_scheduler_persists_a_local_run_token_across_restart() -> None:
    client = Client()
    token_store = InMemoryRunTokenStore()
    schedule = CompilationSchedule(
        collection_id="films",
        enabled=True,
        weekdays=(3,),
        local_time=time(9, 30),
        strategy="compile_stale_only",
    )
    due = datetime(2026, 8, 27, 9, 30, tzinfo=dt_util.DEFAULT_TIME_ZONE)  # Thursday

    first = CompilationScheduler(client, [schedule], token_store)
    runs = await first.async_handle_due(due)
    restarted = CompilationScheduler(client, [schedule], token_store)

    assert [run.collection_id for run in runs] == ["films"]
    assert await restarted.async_handle_due(due) == []
    assert client.compiles == [
        {
            "collection_id": "films",
            "strategy": "compile_stale_only",
            "skip_if_processing": True,
            "idempotency_key": runs[0].token,
        }
    ]


@pytest.mark.asyncio
async def test_scheduler_retries_a_failed_pending_occurrence_with_the_same_token() -> None:
    class FailingClient(Client):
        def __init__(self) -> None:
            super().__init__()
            self.fail_once = True

        async def async_compile(self, collection_id: str, **kwargs: object) -> object:
            await super().async_compile(collection_id, **kwargs)
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("Worker unavailable")
            return {"id": "job"}

    client = FailingClient()
    tokens = InMemoryRunTokenStore()
    schedule = CompilationSchedule(
        collection_id="films",
        weekdays=(3,),
        local_time=time(9, 30),
    )
    due = datetime(2026, 8, 27, 9, 30, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    scheduler = CompilationScheduler(client, [schedule], tokens)

    with pytest.raises(RuntimeError, match="unavailable"):
        await scheduler.async_handle_due(due)
    runs = await scheduler.async_handle_due(due)

    assert len(runs) == 1
    assert client.compiles[0]["idempotency_key"] == client.compiles[1]["idempotency_key"]
    assert tokens.states[runs[0].token] == "succeeded"
