"""Durable subentry mapping and Worker revision synchronization coverage."""

from __future__ import annotations

import pytest

from custom_components.cinema_collections.const import (
    CONF_ENDPOINT,
    CONF_OVERRIDE_COLLECTION_ID,
    CONF_OVERRIDE_MODE,
    CONF_TOKEN,
    DOMAIN,
    SUBENTRY_COLLECTION,
    SUBENTRY_PROFILE,
)
from custom_components.cinema_collections.subentries import (
    CollectionSubentryData,
    WorkerValidationError,
    async_sync_collection,
)


class Client:
    """A minimal Worker client that records optimistic revision updates."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int | None, dict[str, object]]] = []

    async def async_create_collection(
        self, payload: dict[str, object], **_kwargs: object
    ) -> dict[str, object]:
        self.calls.append(("create", str(payload["id"]), None, payload))
        return {**payload, "revision": 1}

    async def async_patch_collection(
        self, collection_id: str, revision: int, payload: dict[str, object], **_kwargs: object
    ) -> dict[str, object]:
        self.calls.append(("patch", collection_id, revision, payload))
        return {"revision": revision + 1}


@pytest.mark.asyncio
async def test_collection_subentry_syncs_stable_id_and_revision() -> None:
    client = Client()
    collection = CollectionSubentryData(
        collection_id="films",
        name="Films",
        source_directory="films",
        processing_profile_id="4k",
    )

    created = await async_sync_collection(client, collection)
    updated = await async_sync_collection(client, created.with_updates(name="New Films"))

    assert created.worker_revision == 1
    assert updated.worker_revision == 2
    assert client.calls[0][0:3] == ("create", "films", None)
    assert client.calls[1][0:3] == ("patch", "films", 1)


@pytest.mark.asyncio
async def test_new_collection_syncs_non_default_worker_policy_values() -> None:
    client = Client()

    synced = await async_sync_collection(
        client,
        CollectionSubentryData(
            collection_id="films",
            name="Films",
            source_directory="films",
            processing_profile_id="4k",
            priority=10,
            allow_manual_override=False,
        ),
    )

    assert synced.worker_revision == 2
    assert client.calls[1][0:3] == ("patch", "films", 1)
    assert client.calls[1][3]["priority"] == 10


@pytest.mark.asyncio
async def test_collection_sync_recovers_after_patch_failure_without_duplicate_create_conflict() -> (
    None
):
    class RecoveringClient(Client):
        def __init__(self) -> None:
            super().__init__()
            self.created: dict[str, object] | None = None
            self.patch_failed = False

        async def async_create_collection(
            self, payload: dict[str, object], **kwargs: object
        ) -> dict[str, object]:
            self.calls.append(("create", str(payload["id"]), None, payload))
            if self.created is None:
                self.created = {**payload, "revision": 1}
            assert kwargs["idempotency_key"] == "collection:films:create"
            return self.created

        async def async_patch_collection(
            self, collection_id: str, revision: int, payload: dict[str, object], **kwargs: object
        ) -> dict[str, object]:
            self.calls.append(("patch", collection_id, revision, payload))
            assert kwargs["idempotency_key"] == "collection:films:patch:1"
            if not self.patch_failed:
                self.patch_failed = True
                raise WorkerValidationError("temporary Worker failure")
            return {"revision": 2}

    client = RecoveringClient()
    collection = CollectionSubentryData(
        collection_id="films",
        name="Films",
        source_directory="films",
        processing_profile_id="4k",
        priority=10,
    )

    with pytest.raises(WorkerValidationError, match="temporary"):
        await async_sync_collection(client, collection)
    recovered = await async_sync_collection(client, collection)

    assert recovered.worker_revision == 2
    assert [call[0] for call in client.calls] == ["create", "patch", "create", "patch"]


@pytest.mark.asyncio
async def test_collection_subentry_surfaces_worker_validation() -> None:
    class InvalidClient(Client):
        async def async_create_collection(
            self, *_args: object, **_kwargs: object
        ) -> dict[str, object]:
            raise WorkerValidationError("source directory is invalid")

    with pytest.raises(WorkerValidationError, match="source directory"):
        await async_sync_collection(
            InvalidClient(),
            CollectionSubentryData(
                collection_id="films",
                name="Films",
                source_directory="../films",
                processing_profile_id="4k",
            ),
        )


@pytest.mark.asyncio
async def test_options_flow_persists_explicit_override(
    hass, aioclient_mock, worker_health_payload
) -> None:
    aioclient_mock.get("http://worker.local/api/v1/health", json=worker_health_payload)
    paired = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_ENDPOINT: "http://worker.local", CONF_TOKEN: "pairing-token"},
    )
    assert paired["type"] == "create_entry"
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={
            CONF_OVERRIDE_MODE: "explicit",
            CONF_OVERRIDE_COLLECTION_ID: "films",
            "history_reset_time": "00:00",
            "sync_on_startup": True,
        },
    )

    assert result["type"] == "create_entry"
    assert entry.options[CONF_OVERRIDE_MODE] == "explicit"
    assert entry.options[CONF_OVERRIDE_COLLECTION_ID] == "films"


async def _pair(hass, aioclient_mock, worker_health_payload):
    aioclient_mock.get("http://worker.local/api/v1/health", json=worker_health_payload)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_ENDPOINT: "http://worker.local", CONF_TOKEN: "pairing-token"},
    )
    assert result["type"] == "create_entry"
    return hass.config_entries.async_entries(DOMAIN)[0]


@pytest.mark.asyncio
async def test_collection_subentry_flow_creates_and_reconfigures_with_worker_revision(
    hass, aioclient_mock, worker_health_payload
) -> None:
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    aioclient_mock.post("http://worker.local/api/v1/collections", json={"revision": 1})
    create = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_COLLECTION),
        context={"source": "user"},
        data={
            "collection_id": "films",
            "name": "Films",
            "source_directory": "films",
            "processing_profile_id": "4k",
            "enabled": True,
            "priority": 0,
            "is_default": False,
            "allow_manual_override": True,
            "schedule_enabled": False,
            "schedule_strategy": "scan_and_compile_changed_or_missing",
            "schedule_skip_if_processing": True,
        },
    )

    assert create["type"] == "create_entry"
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_COLLECTION
    )
    assert subentry.data["collection_id"] == "films"
    aioclient_mock.patch("http://worker.local/api/v1/collections/films", json={"revision": 2})
    reconfigure = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_COLLECTION),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
        data={
            "collection_id": "films",
            "name": "New Films",
            "source_directory": "films",
            "processing_profile_id": "4k",
            "enabled": True,
            "priority": 0,
            "is_default": False,
            "allow_manual_override": True,
            "schedule_enabled": False,
            "schedule_strategy": "scan_and_compile_changed_or_missing",
            "schedule_skip_if_processing": True,
        },
    )

    assert reconfigure["type"] == "abort"
    assert entry.subentries[subentry.subentry_id].data["name"] == "New Films"


@pytest.mark.asyncio
async def test_profile_subentry_flow_creates_and_reports_worker_validation(
    hass, aioclient_mock, monkeypatch, worker_health_payload
) -> None:
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    aioclient_mock.post("http://worker.local/api/v1/profiles", json={"revision": 1})
    created = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROFILE),
        context={"source": "user"},
        data={"profile_id": "4k", "name": "4K", "settings": "{}"},
    )
    assert created["type"] == "create_entry"
    assert any(item.subentry_type == SUBENTRY_PROFILE for item in entry.subentries.values())

    class RejectingProfileClient:
        async def async_create_profile(
            self, *_args: object, **_kwargs: object
        ) -> dict[str, object]:
            raise WorkerValidationError("Worker rejected the submitted configuration")

    import custom_components.cinema_collections.subentries as subentries

    monkeypatch.setattr(
        subentries, "worker_client_for_entry", lambda *_args: RejectingProfileClient()
    )
    invalid = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROFILE),
        context={"source": "user"},
        data={"profile_id": "hd", "name": "HD", "settings": "{}"},
    )
    assert invalid["type"] == "form"
    assert invalid["errors"] == {"base": "worker_validation"}


@pytest.mark.asyncio
async def test_profile_subentry_flow_reconfigures_with_optimistic_revision(
    hass, aioclient_mock, worker_health_payload
) -> None:
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    aioclient_mock.post("http://worker.local/api/v1/profiles", json={"revision": 1})
    created = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROFILE),
        context={"source": "user"},
        data={"profile_id": "4k", "name": "4K", "settings": "{}"},
    )
    assert created["type"] == "create_entry"
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_PROFILE
    )
    aioclient_mock.patch("http://worker.local/api/v1/profiles/4k", json={"revision": 2})

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROFILE),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
        data={"profile_id": "4k", "name": "Cinema 4K", "settings": "{}"},
    )

    assert result["type"] == "abort"
    assert entry.subentries[subentry.subentry_id].data["name"] == "Cinema 4K"
