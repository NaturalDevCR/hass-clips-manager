"""Worker-backed next-clip selection coverage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from custom_components.cinema_collections.history import PlaybackHistoryStore
from custom_components.cinema_collections.selection import (
    ClipAvailability,
    SelectionService,
    SelectRequest,
)


class Clock:
    """Fixed integration-test clock."""

    def now(self) -> datetime:
        return datetime(2026, 8, 27, 20, tzinfo=UTC)


def choose_first(values: tuple[str, ...]) -> str:
    """Expose no-repeat behavior rather than random ordering in tests."""
    return values[0]


class Client:
    """Worker availability response double; it never exposes local files."""

    def __init__(self, clips: tuple[ClipAvailability, ...]) -> None:
        self._clips = clips

    async def async_list_clips(self) -> tuple[ClipAvailability, ...]:
        return self._clips


def clip(
    identifier: str,
    collection_id: str = "films",
    *,
    state: str = "ready",
    output_available: bool = True,
    output_path: str | None = None,
) -> ClipAvailability:
    """Create a full Worker clip availability record."""
    return ClipAvailability(
        id=identifier,
        collection_id=collection_id,
        state=state,
        relative_output_path=output_path or f"{collection_id}/{identifier}.mp4",
        duration_seconds=42.5,
        output_available=output_available,
    )


async def make_service(
    hass: object, clips: tuple[ClipAvailability, ...], key: str
) -> SelectionService:
    """Create a selection service backed by the durable production store."""
    history = PlaybackHistoryStore(
        hass,  # type: ignore[arg-type]
        storage_key=key,
        now=Clock().now,
        chooser=choose_first,
    )
    await history.async_setup()
    return SelectionService(
        history,
        Client(clips),
        media_uri_builder=lambda path: f"media-source://media_source/local/{path}",
    )


@pytest.mark.asyncio
async def test_service_uses_only_current_ready_worker_clips_and_returns_media_details(
    hass: object,
) -> None:
    """Using stale, unavailable, or another collection's clips would violate the Worker boundary."""
    service = await make_service(
        hass,
        (
            clip("stale", state="stale"),
            clip("missing-output", output_available=False),
            clip("other", collection_id="trailers"),
            clip("ready"),
        ),
        "selection-stale",
    )

    response = await service.async_select(SelectRequest(collection_id="films"))

    assert response.collection_id == "films"
    assert response.clip_id == "ready"
    assert response.relative_output_path == "films/ready.mp4"
    assert response.media_uri == "media-source://media_source/local/films/ready.mp4"
    assert response.duration_seconds == 42.5
    assert response.history_reset is False


@pytest.mark.asyncio
async def test_service_dry_run_returns_an_available_clip_without_mutating_history(
    hass: object,
) -> None:
    """A preview must not consume the clip chosen by the following real service call."""
    service = await make_service(hass, (clip("clip-a"), clip("clip-b")), "selection-dry-run")

    preview = await service.async_select(SelectRequest(collection_id="films", dry_run=True))
    actual = await service.async_select(SelectRequest(collection_id="films"))

    assert preview.clip_id == "clip-a"
    assert actual.clip_id == "clip-a"


@pytest.mark.asyncio
async def test_service_allows_the_outer_policy_layer_to_report_no_active_collection(
    hass: object,
) -> None:
    """Rejecting an omitted collection would turn a normal no-policy result into an error."""
    service = await make_service(hass, (clip("clip-a"),), "selection-no-collection")

    response = await service.async_select(SelectRequest())

    assert response.collection_id is None
    assert response.clip_id is None
    assert response.media_uri is None


@pytest.mark.asyncio
async def test_concurrent_service_calls_do_not_return_the_same_ready_clip(hass: object) -> None:
    """The history lock must cover service calls after Worker availability is resolved."""
    service = await make_service(hass, (clip("clip-a"), clip("clip-b")), "selection-lock")

    first, second = await asyncio.gather(
        service.async_select(SelectRequest(collection_id="films")),
        service.async_select(SelectRequest(collection_id="films")),
    )

    assert {first.clip_id, second.clip_id} == {"clip-a", "clip-b"}
