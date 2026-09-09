"""Ordered playback survives restarts, rounds, and changing availability."""

import asyncio

import pytest

from custom_components.cinema_collections.const import PlaybackMode
from custom_components.cinema_collections.history import PlaybackHistoryStore
from custom_components.cinema_collections.selection import (
    ClipAvailability,
    SelectionService,
    SelectRequest,
)


class Catalog:
    def __init__(self):
        self.clips = [
            ClipAvailability("c", "films", "ready", "films/c.mp4", 10, True),
            ClipAvailability("b", "films", "ready", "films/b.mp4", 10, True),
            ClipAvailability("a", "films", "ready", "films/A.mp4", 10, True),
            ClipAvailability("missing", "films", "ready", "films/0.mp4", 10, False),
            ClipAvailability("deleted", "films", "deleted", "films/1.mp4", 10, True),
            ClipAvailability("stale", "films", "stale", "films/2.mp4", 10, True),
            ClipAvailability("other", "other", "ready", "other/0.mp4", 10, True),
        ]

    async def async_list_clips(self):
        return tuple(self.clips)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,ids,expected",
    [
        (PlaybackMode.SEQUENTIAL, (), ["a", "b", "c"]),
        (PlaybackMode.CUSTOM, ("missing", "deleted", "other", "c"), ["c", "a", "b"]),
    ],
)
async def test_ordered_round_preview_restart_and_reset(hass, mode, ids, expected):
    catalog = Catalog()
    history = PlaybackHistoryStore(hass, storage_key="ordered-round", chooser=lambda _: "invalid")
    service = SelectionService(history, catalog)
    request = SelectRequest("films", playback_mode=mode, ordered_clip_ids=ids)
    preview = await service.async_select(
        SelectRequest("films", dry_run=True, playback_mode=mode, ordered_clip_ids=ids)
    )
    assert preview.clip_id == expected[0]
    assert history.snapshot() == {}
    assert (await service.async_select(request)).clip_id == expected[0]
    await history.async_shutdown()
    restarted = PlaybackHistoryStore(hass, storage_key="ordered-round")
    service = SelectionService(restarted, catalog)
    claims = await asyncio.gather(service.async_select(request), service.async_select(request))
    assert [claim.clip_id for claim in claims] == expected[1:]
    new_round = await service.async_select(request)
    assert new_round.clip_id == expected[0]
    assert new_round.history_reset
    await restarted.async_reset("films")
    assert (await service.async_select(request)).clip_id == expected[0]


@pytest.mark.asyncio
async def test_new_order_and_new_clips_apply_to_remaining_history(hass):
    catalog = Catalog()
    history = PlaybackHistoryStore(hass, storage_key="edited-order")
    service = SelectionService(history, catalog)
    first = await service.async_select(
        SelectRequest("films", playback_mode="custom", ordered_clip_ids=("c", "b", "a"))
    )
    assert first.clip_id == "c"
    catalog.clips.append(ClipAvailability("new", "films", "ready", "films/0-new.mp4", 10, True))
    changed = SelectRequest("films", playback_mode="custom", ordered_clip_ids=("c", "a"))
    assert (await service.async_select(changed)).clip_id == "a"
    assert (await service.async_select(changed)).clip_id == "new"
    assert (await service.async_select(changed)).clip_id == "b"
    assert (
        await service.async_select(SelectRequest("other", playback_mode="sequential"))
    ).clip_id == "other"
    assert history.snapshot()["films"]["played_count"] == 4


@pytest.mark.asyncio
async def test_ordered_stale_fallback_and_path_ties(hass):
    catalog = Catalog()
    catalog.clips = [
        ClipAvailability("z", "films", "stale", "films/a.mp4", 10, True),
        ClipAvailability("b", "films", "stale", "films/A.mp4", 10, True),
        ClipAvailability("a", "films", "stale", "films/A.mp4", 10, True),
    ]
    service = SelectionService(PlaybackHistoryStore(hass, storage_key="ordered-stale"), catalog)
    results = [
        await service.async_select(SelectRequest("films", playback_mode="sequential"))
        for _ in range(3)
    ]
    assert [result.clip_id for result in results] == ["a", "b", "z"]
    assert all(result.output_is_stale for result in results)
