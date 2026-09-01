"""Automation service behavior for Cinema Collections."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError

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
        "media_content_id": (
            "media-source://media_source/local/cinema-collections/compiled/films/clip-1.mp4"
        ),
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

        async def async_cancel_all_jobs(self, **kwargs) -> None:
            self.calls.append(("cancel-all", kwargs))

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
    await services.async_run_action(hass, entry, "cancel_all_processing", {})
    await services.async_run_action(hass, entry, "cleanup_temporaries", {})

    assert [name for name, _data in client.calls] == [
        "scan",
        "compile",
        "compile",
        "cancel",
        "cancel-all",
        "cleanup",
    ]
    assert client.calls[2][1]["strategy"] == "compile_stale_only"
    assert client.calls[3][1]["job_id"] == "job-7"


@pytest.mark.asyncio
async def test_cancel_processing_still_cancels_only_one_job(hass, monkeypatch) -> None:
    """The single-job cancel service keeps its exact behaviour; cancel-all is separate."""
    from custom_components.cinema_collections import services
    from custom_components.cinema_collections.const import DOMAIN

    class SingleCancelClient:
        def __init__(self) -> None:
            self.cancelled: list[object] = []

        async def async_cancel_job(self, job_id: str, **kwargs) -> None:
            self.cancelled.append((job_id, kwargs))

        async def async_cancel_all_jobs(self, **kwargs) -> None:
            raise AssertionError("cancel_processing must not dispatch cancel-all")

    class Coordinator:
        data = SimpleNamespace(status=SimpleNamespace(current_job={"id": "job-7"}))

        async def async_request_refresh(self) -> None:
            return None

    entry = SimpleNamespace(entry_id="single-cancel-entry")
    client = SingleCancelClient()
    hass.data[DOMAIN] = {
        entry.entry_id: SimpleNamespace(
            client=client, history=None, coordinator=Coordinator(), entry=entry
        )
    }
    monkeypatch.setattr(services, "policies_for_entry", lambda _entry: (CollectionPolicy("films"),))

    await services.async_run_action(hass, entry, services.SERVICE_CANCEL_PROCESSING, {})

    assert len(client.cancelled) == 1
    job_id, kwargs = client.cancelled[0]
    assert job_id == "job-7"
    assert "idempotency_key" in kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("collection", "raises"),
    [
        (CollectionPolicy("disabled", enabled=False), "disabled"),
        (
            CollectionPolicy("blocked", allow_manual_override=False),
            "does not allow manual override",
        ),
        (CollectionPolicy("allowed"), None),
    ],
)
async def test_explicit_override_rejects_collections_not_available_to_the_select(
    monkeypatch, collection, raises
) -> None:
    """Service and Select paths share enabled/manual-override eligibility rules."""
    from custom_components.cinema_collections import services
    from custom_components.cinema_collections.const import DOMAIN

    class ConfigEntries:
        @staticmethod
        def async_update_entry(entry, *, options) -> None:
            entry.options = options

    class Coordinator:
        async def async_request_refresh(self) -> None:
            return None

    entry = SimpleNamespace(entry_id="override-entry", options={})
    hass = SimpleNamespace(config_entries=ConfigEntries(), data={DOMAIN: {}})
    monkeypatch.setattr(services, "policies_for_entry", lambda _entry: (collection,))

    if raises is not None:
        with pytest.raises(HomeAssistantError, match=raises):
            await services.async_set_collection_override(
                hass,
                entry,
                {"mode": "explicit", "collection_id": collection.id},
                coordinator=Coordinator(),
            )
    else:
        await services.async_set_collection_override(
            hass,
            entry,
            {"mode": "explicit", "collection_id": collection.id},
            coordinator=Coordinator(),
        )
        assert entry.options["override_mode"] == "explicit"
        assert entry.options["override_collection_id"] == "allowed"


@pytest.mark.asyncio
async def test_home_assistant_registers_all_service_schemas(hass) -> None:
    """Real HA registration exposes every documented service with its matching schema."""
    from custom_components.cinema_collections.const import DOMAIN
    from custom_components.cinema_collections.services import (
        SERVICE_CANCEL_ALL_PROCESSING,
        SERVICE_CANCEL_PROCESSING,
        SERVICE_COMPILE_ALL,
        SERVICE_COMPILE_COLLECTION,
        SERVICE_RESET_HISTORY,
        SERVICE_RETRY_FAILED,
        SERVICE_SCAN_LIBRARY,
        SERVICE_SET_COLLECTION_OVERRIDE,
        async_register_services,
    )

    await async_register_services(hass)
    registered = hass.services.async_services()[DOMAIN]

    assert set(registered) == {
        SERVICE_SELECT_NEXT_CLIP,
        SERVICE_RESET_HISTORY,
        SERVICE_SCAN_LIBRARY,
        SERVICE_COMPILE_COLLECTION,
        SERVICE_COMPILE_ALL,
        SERVICE_RETRY_FAILED,
        SERVICE_CANCEL_PROCESSING,
        SERVICE_CANCEL_ALL_PROCESSING,
        SERVICE_SET_COLLECTION_OVERRIDE,
    }
    with pytest.raises(vol.Invalid):
        registered[SERVICE_COMPILE_COLLECTION].schema({})
    assert (
        registered[SERVICE_SET_COLLECTION_OVERRIDE].schema({"mode": "automatic"})["mode"]
        == "automatic"
    )
    assert registered[SERVICE_SCAN_LIBRARY].schema({"collection_ids": ["films"]})[
        "collection_ids"
    ] == ["films"]


def test_services_yaml_documents_every_registered_service_input() -> None:
    """Home Assistant service UI documentation cannot drift from validation schemas."""
    import yaml

    document = yaml.safe_load(
        (
            Path(__file__).parents[2] / "custom_components/cinema_collections/services.yaml"
        ).read_text()
    )

    assert {name: set(spec.get("fields", {})) for name, spec in document.items()} == {
        "select_next_clip": {"entry_id", "collection_id", "dry_run"},
        "reset_history": {"entry_id", "collection_id"},
        "scan_library": {"entry_id", "collection_ids"},
        "compile_collection": {"entry_id", "collection_id", "strategy", "skip_if_processing"},
        "compile_all": {"entry_id", "strategy", "skip_if_processing"},
        "retry_failed": {"entry_id", "collection_id"},
        "cancel_processing": {"entry_id", "job_id"},
        "cancel_all_processing": {"entry_id"},
        "set_collection_override": {"entry_id", "mode", "collection_id"},
    }
    for specification in document.values():
        for field in specification.get("fields", {}).values():
            assert field["description"]
            assert "selector" in field
