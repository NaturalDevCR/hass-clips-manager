"""Automation service behavior for Cinema Collections."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.core import SupportsResponse

from custom_components.cinema_collections.history import PlaybackHistoryStore
from custom_components.cinema_collections.resolver import CollectionPolicy
from custom_components.cinema_collections.selection import ClipAvailability
from custom_components.cinema_collections.services import (
    SERVICE_SELECT_NEXT_CLIP,
    async_select_next_clip,
    service_supports_response,
)


class Client:
    """Selection-only Worker client double."""

    async def async_list_clips(self) -> tuple[ClipAvailability, ...]:
        return (
            ClipAvailability(
                id="clip-1",
                collection_id="films",
                state="ready",
                relative_output_path="films/clip-1.mp4",
                duration_seconds=9.5,
                output_available=True,
            ),
        )


@pytest.mark.asyncio
async def test_select_next_clip_returns_home_assistant_media_content_response(hass) -> None:
    """The service response is directly consumable by a playback automation."""
    history = PlaybackHistoryStore(hass, storage_key="services-response")
    await history.async_setup()

    response = await async_select_next_clip(
        history=history,
        client=Client(),
        collections=(CollectionPolicy("films", is_default=True),),
        data={},
    )

    assert response == {
        "collection_id": "films",
        "clip_id": "clip-1",
        "relative_output_path": "films/clip-1.mp4",
        "media_content_id": "media-source://media_source/local/films/clip-1.mp4",
        "media_content_type": "video",
        "duration_seconds": 9.5,
        "history_reset": False,
    }


def test_select_next_clip_is_registered_as_a_response_service() -> None:
    """Home Assistant must retain the structured result instead of discarding it."""
    assert SERVICE_SELECT_NEXT_CLIP == "select_next_clip"
    assert service_supports_response(SERVICE_SELECT_NEXT_CLIP) is SupportsResponse.ONLY


@pytest.mark.asyncio
async def test_operational_actions_dispatch_only_published_worker_requests(
    hass, monkeypatch
) -> None:
    """Scan, compile, retry, cancel, and cleanup map to published client methods."""
    from custom_components.cinema_collections import services
    from custom_components.cinema_collections.const import DOMAIN

    class OperationsClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        async def async_scan(self, **kwargs) -> None:
            self.calls.append(("scan", kwargs))

        async def async_compile(self, collection_id: str, **kwargs) -> None:
            self.calls.append(("compile", {"collection_id": collection_id, **kwargs}))

        async def async_cancel_job(self, job_id: str, **kwargs) -> None:
            self.calls.append(("cancel", {"job_id": job_id, **kwargs}))

        async def async_cleanup_temporaries(self, **kwargs) -> None:
            self.calls.append(("cleanup", kwargs))

    class Coordinator:
        data = SimpleNamespace(status=SimpleNamespace(current_job={"id": "job-7"}))

        async def async_request_refresh(self) -> None:
            return None

    entry = SimpleNamespace(entry_id="service-entry")
    client = OperationsClient()
    hass.data[DOMAIN] = {
        entry.entry_id: SimpleNamespace(
            client=client, history=None, coordinator=Coordinator(), entry=entry
        )
    }
    monkeypatch.setattr(services, "policies_for_entry", lambda _entry: (CollectionPolicy("films"),))

    await services.async_run_action(hass, entry, services.SERVICE_SCAN_LIBRARY, {})
    await services.async_run_action(hass, entry, services.SERVICE_COMPILE_ALL, {})
    await services.async_run_action(hass, entry, services.SERVICE_RETRY_FAILED, {})
    await services.async_run_action(hass, entry, "cancel_processing", {})
    await services.async_run_action(hass, entry, "cleanup_temporaries", {})

    assert [name for name, _data in client.calls] == [
        "scan",
        "compile",
        "compile",
        "cancel",
        "cleanup",
    ]
    assert client.calls[2][1]["strategy"] == "compile_stale_only"
    assert client.calls[3][1]["job_id"] == "job-7"
