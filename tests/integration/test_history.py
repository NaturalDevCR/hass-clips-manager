"""Persistent no-repeat playback history coverage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta

import pytest
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from custom_components.cinema_collections.const import HistoryResetMode
from custom_components.cinema_collections.history import PlaybackHistoryStore


class Clock:
    """A mutable, timezone-aware clock for local history tests."""

    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def choose_first(values: tuple[str, ...]) -> str:
    """Make selection order observable without duplicating selection logic."""
    return values[0]


def make_history(
    hass: object,
    clock: Clock,
    key: str,
    *,
    reset_mode: HistoryResetMode = HistoryResetMode.ON_EXHAUSTION,
) -> PlaybackHistoryStore:
    """Build one durable history store with deterministic test selection."""
    return PlaybackHistoryStore(
        hass,  # type: ignore[arg-type]
        storage_key=key,
        now=clock.now,
        chooser=choose_first,
        reset_mode=reset_mode,
    )


@pytest.mark.asyncio
async def test_history_keeps_collection_rounds_independent(hass: object) -> None:
    """A clip played in one collection must not affect another collection."""
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    history = make_history(hass, clock, "history-independent")
    await history.async_setup()

    film = await history.async_select("films", ("film-a", "film-b"), dry_run=False)
    trailer = await history.async_select("trailers", ("film-a",), dry_run=False)
    next_film = await history.async_select("films", ("film-a", "film-b"), dry_run=False)

    assert film.clip_id == "film-a"
    assert trailer.clip_id == "film-a"
    assert next_film.clip_id == "film-b"
    assert next_film.round_number == 1


@pytest.mark.asyncio
async def test_history_starts_a_new_round_only_after_current_ready_clips_are_exhausted(
    hass: object,
) -> None:
    """Removing the exhaustion reset would repeat a clip or leave no next clip."""
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    history = make_history(hass, clock, "history-round", reset_mode=HistoryResetMode.ON_EXHAUSTION)
    await history.async_setup()

    first = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)
    second = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)
    third = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    assert (first.clip_id, second.clip_id) == ("clip-a", "clip-b")
    assert third.clip_id == "clip-a"
    assert third.round_number == 2
    assert third.history_reset is True


@pytest.mark.asyncio
async def test_daily_reconciliation_clears_previous_local_day_before_selection(
    hass: object,
) -> None:
    """A missed local midnight must not carry the old day's used clips forward."""
    clock = Clock(datetime(2026, 8, 27, 23, 59, tzinfo=dt_util.DEFAULT_TIME_ZONE))
    key = "history-reconcile"
    first_process = make_history(hass, clock, key, reset_mode=HistoryResetMode.DAILY)
    await first_process.async_setup()
    first_selection = await first_process.async_select("films", ("clip-a", "clip-b"), dry_run=False)
    assert first_selection.clip_id == "clip-a"

    clock.value += timedelta(minutes=2)
    restarted = make_history(hass, clock, key, reset_mode=HistoryResetMode.DAILY)
    await restarted.async_setup()
    selected = await restarted.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    assert selected.clip_id == "clip-a"
    assert selected.history_reset is True


@pytest.mark.asyncio
async def test_daily_reset_handler_starts_a_new_round_at_local_midnight(hass: object) -> None:
    """Removing the registered daily handler would retain yesterday's played clips."""
    clock = Clock(datetime(2026, 8, 27, 23, 59, tzinfo=dt_util.DEFAULT_TIME_ZONE))
    history = make_history(hass, clock, "history-daily-handler", reset_mode=HistoryResetMode.DAILY)
    await history.async_setup()
    await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    clock.value += timedelta(minutes=2)
    result = await history.async_handle_daily_reset(clock.now())
    selected = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    assert result.collection_ids == ("films",)
    assert selected.clip_id == "clip-a"
    assert selected.history_reset is True


@pytest.mark.asyncio
async def test_configured_daily_reset_uses_its_local_boundary(hass: object) -> None:
    clock = Clock(datetime(2026, 8, 27, 5, 30, tzinfo=dt_util.DEFAULT_TIME_ZONE))
    history = PlaybackHistoryStore(
        hass,  # type: ignore[arg-type]
        storage_key="custom-reset",
        now=clock.now,
        chooser=choose_first,
        reset_time=time(6, 0),
        reset_mode=HistoryResetMode.DAILY,
    )
    await history.async_setup()
    await history.async_select("films", ("clip-a",), dry_run=False)

    clock.value = datetime(2026, 8, 27, 6, 0, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    result = await history.async_handle_daily_reset(clock.now())

    assert result.collection_ids == ("films",)


@pytest.mark.asyncio
async def test_explicit_per_collection_and_global_resets_are_scoped(hass: object) -> None:
    """A collection reset must not clear others; only None requests a global reset."""
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    history = make_history(hass, clock, "history-reset")
    await history.async_setup()
    await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)
    await history.async_select("trailers", ("clip-a", "clip-b"), dry_run=False)

    reset = await history.async_reset("films")
    after_films = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)
    after_trailers = await history.async_select("trailers", ("clip-a", "clip-b"), dry_run=False)
    global_reset = await history.async_reset(None)

    assert reset.collection_ids == ("films",)
    assert after_films.clip_id == "clip-a"
    assert after_films.history_reset is True
    assert after_trailers.clip_id == "clip-b"
    assert global_reset.collection_ids == ("films", "trailers")


@pytest.mark.asyncio
async def test_dry_run_does_not_persist_a_played_clip(hass: object) -> None:
    """Changing dry-run persistence would make a preview consume a real clip."""
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    history = make_history(hass, clock, "history-dry-run")
    await history.async_setup()

    preview = await history.async_select("films", ("clip-a", "clip-b"), dry_run=True)
    actual = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    assert preview.clip_id == "clip-a"
    assert actual.clip_id == "clip-a"


@pytest.mark.asyncio
async def test_dry_run_does_not_persist_a_missed_daily_reconciliation(hass: object) -> None:
    """A preview after restart must not clear history or consume the reset flag."""
    clock = Clock(datetime(2026, 8, 27, 23, 59, tzinfo=dt_util.DEFAULT_TIME_ZONE))
    key = "history-dry-run-reconcile"
    previous = make_history(hass, clock, key, reset_mode=HistoryResetMode.DAILY)
    await previous.async_setup()
    await previous.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    clock.value += timedelta(minutes=2)
    preview_process = make_history(hass, clock, key, reset_mode=HistoryResetMode.DAILY)
    preview = await preview_process.async_select("films", ("clip-a", "clip-b"), dry_run=True)
    persisted = await Store(hass, 1, key).async_load()  # type: ignore[arg-type]

    assert preview.clip_id == "clip-a"
    assert persisted["collections"]["films"]["period_start"] == "2026-08-27"


@pytest.mark.asyncio
async def test_selection_prunes_missing_clip_ids_from_persisted_history(hass: object) -> None:
    """Keeping deleted IDs forever would make durable history grow without bound."""
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    key = "history-stale-pruning"
    history = make_history(hass, clock, key)
    await history.async_setup()
    await history.async_select("films", ("deleted", "ready"), dry_run=False)
    selected = await history.async_select("films", ("ready",), dry_run=False)
    persisted = await Store(hass, 1, key).async_load()  # type: ignore[arg-type]

    assert selected.clip_id == "ready"
    assert persisted["collections"]["films"]["played_clip_ids"] == ["ready"]


@pytest.mark.asyncio
async def test_lock_prevents_concurrent_selections_from_repeating_a_clip(hass: object) -> None:
    """Dropping the selection lock lets concurrent callers select the same ready clip."""
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    history = make_history(hass, clock, "history-lock")
    await history.async_setup()

    first, second = await asyncio.gather(
        history.async_select("films", ("clip-a", "clip-b"), dry_run=False),
        history.async_select("films", ("clip-a", "clip-b"), dry_run=False),
    )

    assert {first.clip_id, second.clip_id} == {"clip-a", "clip-b"}


@pytest.mark.asyncio
async def test_snapshot_is_empty_before_any_selection(hass: object) -> None:
    """An unused store exposes no history without inventing collection records."""
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    history = make_history(hass, clock, "history-snapshot-empty")
    await history.async_setup()

    assert history.snapshot() == {}


@pytest.mark.asyncio
async def test_snapshot_reports_one_selection_summary(hass: object) -> None:
    """The snapshot exposes counts, never the unbounded played-clip ID list."""
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    history = make_history(hass, clock, "history-snapshot-one")
    await history.async_setup()
    await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    assert history.snapshot() == {
        "films": {
            "round_number": 1,
            "played_count": 1,
            "last_selected_clip_id": "clip-a",
            "last_reset_at": None,
        }
    }


@pytest.mark.asyncio
async def test_snapshot_reflects_reset_round_and_played_count(hass: object) -> None:
    """A reset starts a new round and clears the played count in the snapshot."""
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    history = make_history(hass, clock, "history-snapshot-reset")
    await history.async_setup()
    await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)
    await history.async_reset("films")

    snapshot = history.snapshot()
    assert snapshot["films"]["round_number"] == 2
    assert snapshot["films"]["played_count"] == 0
    assert snapshot["films"]["last_selected_clip_id"] is None
    assert snapshot["films"]["last_reset_at"] == dt_util.as_local(clock.value).isoformat()


@pytest.mark.asyncio
async def test_on_exhaustion_mode_ignores_the_configured_daily_reset_time(hass: object) -> None:
    """Crossing the configured local time must not wipe history in exhaustion mode."""
    clock = Clock(datetime(2026, 8, 27, 5, 30, tzinfo=dt_util.DEFAULT_TIME_ZONE))
    history = PlaybackHistoryStore(
        hass,  # type: ignore[arg-type]
        storage_key="history-no-daily",
        now=clock.now,
        chooser=choose_first,
        reset_time=time(6, 0),
        reset_mode=HistoryResetMode.ON_EXHAUSTION,
    )
    await history.async_setup()
    first = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    clock.value = datetime(2026, 8, 27, 6, 0, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    result = await history.async_handle_daily_reset(clock.now())
    selected = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    assert first.clip_id == "clip-a"
    assert result.collection_ids == ()
    assert selected.clip_id == "clip-b"
    assert selected.round_number == 1
    assert selected.history_reset is False


@pytest.mark.asyncio
async def test_on_exhaustion_mode_still_rolls_the_round_over(hass: object) -> None:
    """The no-repeat guarantee must survive without a daily wipe."""
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    history = make_history(
        hass, clock, "history-exhaustion-only", reset_mode=HistoryResetMode.ON_EXHAUSTION
    )
    await history.async_setup()

    first = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)
    second = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)
    third = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    assert (first.clip_id, second.clip_id) == ("clip-a", "clip-b")
    assert third.clip_id == "clip-a"
    assert third.round_number == 2
    assert third.history_reset is True


@pytest.mark.asyncio
async def test_switching_to_on_exhaustion_mode_does_not_wipe_daily_records(hass: object) -> None:
    """A record persisted under daily mode must not trigger a spurious wipe on switch."""
    clock = Clock(datetime(2026, 8, 27, 23, 59, tzinfo=dt_util.DEFAULT_TIME_ZONE))
    key = "history-mode-switch"
    daily_store = make_history(hass, clock, key, reset_mode=HistoryResetMode.DAILY)
    await daily_store.async_setup()
    first = await daily_store.async_select("films", ("clip-a", "clip-b"), dry_run=False)
    assert first.clip_id == "clip-a"

    clock.value += timedelta(minutes=2)
    switched = make_history(hass, clock, key, reset_mode=HistoryResetMode.ON_EXHAUSTION)
    await switched.async_setup()
    selected = await switched.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    assert selected.clip_id == "clip-b"
    assert selected.round_number == 1
    assert selected.history_reset is False


@pytest.mark.asyncio
async def test_explicit_reset_works_in_on_exhaustion_mode(hass: object) -> None:
    """The manual reset service path must stay independent of the automatic policy."""
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    history = make_history(
        hass, clock, "history-explicit-exhaustion", reset_mode=HistoryResetMode.ON_EXHAUSTION
    )
    await history.async_setup()
    first = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)
    second = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    reset = await history.async_reset("films")
    selected = await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    assert (first.clip_id, second.clip_id) == ("clip-a", "clip-b")
    assert reset.collection_ids == ("films",)
    assert selected.clip_id == "clip-a"
    assert selected.round_number == 2
    assert selected.history_reset is True


@pytest.mark.asyncio
async def test_on_exhaustion_mode_registers_no_time_listener(hass: object, monkeypatch) -> None:
    """The store must not schedule a daily wipe it will never run."""
    import custom_components.cinema_collections.history as history_module

    registered: list[tuple[object, object]] = []
    monkeypatch.setattr(
        history_module,
        "async_track_time_change",
        lambda *_args, **_kwargs: registered.append((_args, _kwargs)) or (lambda: None),
    )
    clock = Clock(datetime(2026, 8, 27, 20, tzinfo=UTC))
    exhausted = make_history(hass, clock, "history-listener-none")
    await exhausted.async_setup()
    assert exhausted._cancel_daily_reset is None
    assert registered == []

    daily = make_history(hass, clock, "history-listener-daily", reset_mode=HistoryResetMode.DAILY)
    await daily.async_setup()
    assert daily._cancel_daily_reset is not None
    assert len(registered) == 1
