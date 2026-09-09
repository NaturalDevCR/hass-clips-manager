"""Per-call playback preferences never change the collection configuration."""

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError

from custom_components.cinema_collections.history import PlaybackHistoryStore
from custom_components.cinema_collections.resolver import CollectionPolicy
from custom_components.cinema_collections.selection import ClipAvailability
from custom_components.cinema_collections.services import (
    _SELECT_NEXT_SCHEMA,
    async_select_next_clip,
)


class Client:
    async def async_list_clips(self):
        return tuple(
            ClipAvailability(identifier, "films", "ready", path, 10, True)
            for identifier, path in (("b", "films/B.mp4"), ("a", "films/A.mp4"))
        )


@pytest.mark.asyncio
async def test_per_call_custom_order_and_sequential_share_history(hass):
    history = PlaybackHistoryStore(hass, storage_key="call-order", chooser=lambda ids: ids[-1])
    policy = CollectionPolicy("films", is_default=True)

    async def select(data):
        return await async_select_next_clip(
            history=history, client=Client(), collections=(policy,), data=data
        )

    preview = await select(
        {"playback_mode": "custom", "ordered_clip_ids": ["b", "a"], "dry_run": True}
    )
    actual = await select({"playback_mode": "custom", "ordered_clip_ids": ["b", "a"]})
    second = await select({"playback_mode": "sequential"})
    assert preview["clip_id"] == actual["clip_id"] == "b"
    assert second["clip_id"] == "a"
    assert policy.playback_mode == "random"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"playback_mode": "unknown"},
        {"playback_mode": "custom", "ordered_clip_ids": []},
        {"playback_mode": "custom", "ordered_clip_ids": ["a", "a"]},
        {"playback_mode": "random", "ordered_clip_ids": ["a"]},
        {"ordered_clip_ids": ["a"]},
        {"playback_mode": "custom", "ordered_clip_ids": "ab"},
        {"playback_mode": "custom", "ordered_clip_ids": [42]},
    ],
)
async def test_invalid_call_preferences_do_not_consume_history(hass, data):
    history = PlaybackHistoryStore(hass, storage_key="invalid-call-order")
    with pytest.raises((HomeAssistantError, vol.Invalid, ValueError)):
        await async_select_next_clip(
            history=history,
            client=Client(),
            collections=(CollectionPolicy("films", is_default=True),),
            data=data,
        )
    assert history.snapshot() == {}


def test_action_schema_accepts_ordering_and_rejects_unknown_mode():
    assert _SELECT_NEXT_SCHEMA({"playback_mode": "sequential"})["playback_mode"] == "sequential"
    assert _SELECT_NEXT_SCHEMA({"playback_mode": "custom", "ordered_clip_ids": ["b", "a"]})[
        "ordered_clip_ids"
    ] == ["b", "a"]
    with pytest.raises(vol.Invalid):
        _SELECT_NEXT_SCHEMA({"playback_mode": "invalid"})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [("sequential", "a"), ("random", "a"), ("custom", "b")])
async def test_call_override_of_custom_collection(hass, mode, expected):
    from custom_components.cinema_collections.const import PlaybackMode

    history = PlaybackHistoryStore(hass, storage_key="custom-override", chooser=lambda ids: ids[-1])
    policy = CollectionPolicy(
        "films", is_default=True, playback_mode=PlaybackMode.CUSTOM, ordered_clip_ids=("b", "a")
    )
    result = await async_select_next_clip(
        history=history, client=Client(), collections=(policy,), data={"playback_mode": mode}
    )
    assert result["clip_id"] == expected
    assert policy.ordered_clip_ids == ("b", "a")
    assert policy.playback_mode is PlaybackMode.CUSTOM


@pytest.mark.asyncio
async def test_collection_default_and_explicit_collection_policy(hass):
    from custom_components.cinema_collections.const import PlaybackMode

    policy = CollectionPolicy(
        "films", playback_mode=PlaybackMode.CUSTOM, ordered_clip_ids=("b", "a")
    )
    result = await async_select_next_clip(
        history=PlaybackHistoryStore(hass, storage_key="explicit-order"),
        client=Client(),
        collections=(policy,),
        data={"collection_id": "films"},
    )
    assert result["clip_id"] == "b"


@pytest.mark.asyncio
async def test_no_active_collection_keeps_empty_response(hass):
    result = await async_select_next_clip(
        history=PlaybackHistoryStore(hass, storage_key="no-order"),
        client=Client(),
        collections=(),
        data={},
    )
    assert result["clip_id"] is None
