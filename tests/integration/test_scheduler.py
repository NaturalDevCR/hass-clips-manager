"""Local scheduler persistence and idempotency coverage."""

from __future__ import annotations

from datetime import datetime, time

import pytest
from homeassistant.util import dt as dt_util

from custom_components.cinema_collections.scheduler import (
    CompilationSchedule,
    CompilationScheduler,
    InMemoryRunTokenStore,
    _prune_token_states,
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


def test_durable_schedule_tokens_are_bounded_and_keep_newest_occurrences() -> None:
    states = {f"films:2025-01-{day:02d}T09:30+0000": "succeeded" for day in range(1, 29)}
    states.update(
        {
            f"films:2026-{month:02d}-{day:02d}T09:30+0000": "succeeded"
            for month in range(1, 13)
            for day in range(1, 29)
        }
    )

    pruned = _prune_token_states(states, limit=128)

    assert len(pruned) == 128
    assert "films:2025-01-01T09:30+0000" not in pruned
    assert "films:2026-12-28T09:30+0000" in pruned


@pytest.mark.asyncio
async def test_config_entry_token_store_reconstructs_durable_claims_across_restart(
    hass,
) -> None:
    """A claimed occurrence survives an HA restart because tokens persist in options."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.cinema_collections.const import (
        CONF_SCHEDULE_RUN_TOKENS,
        DOMAIN,
    )
    from custom_components.cinema_collections.scheduler import ConfigEntryRunTokenStore

    entry = MockConfigEntry(domain=DOMAIN, entry_id="durable-tokens")
    entry.add_to_hass(hass)
    client = Client()
    schedule = CompilationSchedule(collection_id="films", weekdays=(3,), local_time=time(9, 30))
    due = datetime(2026, 8, 27, 9, 30, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    runs = await CompilationScheduler(
        client, [schedule], ConfigEntryRunTokenStore(hass, entry)
    ).async_handle_due(due)

    assert len(runs) == 1
    persisted = entry.options[CONF_SCHEDULE_RUN_TOKENS]
    assert persisted[runs[0].token] == "succeeded"
    restarted = CompilationScheduler(client, [schedule], ConfigEntryRunTokenStore(hass, entry))
    assert await restarted.async_handle_due(due) == []


@pytest.mark.asyncio
async def test_legacy_list_run_tokens_are_preserved_and_not_redispatched(hass) -> None:
    """Entries that stored completed tokens as a list keep those durable claims."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.cinema_collections.const import (
        CONF_SCHEDULE_RUN_TOKENS,
        DOMAIN,
    )
    from custom_components.cinema_collections.scheduler import ConfigEntryRunTokenStore

    legacy_token = "films:2026-08-20T09:30-0700"
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="legacy-tokens",
        options={CONF_SCHEDULE_RUN_TOKENS: [legacy_token]},
    )
    entry.add_to_hass(hass)

    assert await ConfigEntryRunTokenStore(hass, entry).async_begin(legacy_token) is False
