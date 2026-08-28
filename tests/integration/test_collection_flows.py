"""Durable subentry mapping and Worker revision synchronization coverage."""

from __future__ import annotations

import pytest

from custom_components.cinema_collections.const import (
    CONF_ENDPOINT,
    CONF_OVERRIDE_COLLECTION_ID,
    CONF_OVERRIDE_MODE,
    CONF_TOKEN,
    DOMAIN,
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
        },
    )

    assert result["type"] == "create_entry"
    assert entry.options[CONF_OVERRIDE_MODE] == "explicit"
    assert entry.options[CONF_OVERRIDE_COLLECTION_ID] == "films"
