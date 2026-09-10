"""The one-way migration of configuration from the config entry into the Worker."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cinema_collections.api_client import WorkerApiError
from custom_components.cinema_collections.const import (
    CONF_MIGRATED_TO_WORKER,
    DOMAIN,
    SUBENTRY_COLLECTION,
    SUBENTRY_PROFILE,
)
from custom_components.cinema_collections.migration import async_migrate_subentries
from custom_components.cinema_collections.models import WorkerCollection

_SETTINGS: dict[str, Any] = {"profile_version": 1, "hardware_acceleration": False}

_COLLECTION_DATA: dict[str, Any] = {
    "collection_id": "films",
    "name": "Films",
    "source_directory": "films",
    "processing_profile_id": "cinema",
    "enabled": True,
    "priority": 3,
    "is_default": True,
    "allow_manual_override": False,
    "tags": ["evening"],
    "notes": "the good ones",
    "starts_at": "2026-01-01T00:00:00+00:00",
    "ends_at": None,
    "schedule": {"enabled": True, "local_time": "02:30", "weekdays": [0, 1]},
    "playback_mode": "custom",
    "ordered_clip_ids": ["11111111-1111-1111-1111-111111111111"],
}


def _stored_collection(**overrides: Any) -> WorkerCollection:
    payload: dict[str, Any] = {
        "id": "films",
        "name": "Films",
        "source_directory": "films",
        "processing_profile_id": "cinema",
        "enabled": True,
        "priority": 3,
        "is_default": True,
        "allow_manual_override": False,
        "tags": ["evening"],
        "notes": "the good ones",
        "starts_at": "2026-01-01T00:00:00+00:00",
        "ends_at": None,
        "schedule": {"enabled": True, "local_time": "02:30", "weekdays": [0, 1]},
        "playback_mode": "custom",
        "ordered_clip_ids": ["11111111-1111-1111-1111-111111111111"],
        "revision": 1,
    }
    payload.update(overrides)
    return WorkerCollection.from_dict(payload)


class _FakeClient:
    """A Worker that accepts every push and reports what it stored."""

    def __init__(
        self,
        *,
        stored: WorkerCollection | None = None,
        settings: dict[str, Any] | None = None,
        fail: bool = False,
    ) -> None:
        self.stored = stored if stored is not None else _stored_collection()
        self.settings = _SETTINGS if settings is None else settings
        self.fail = fail
        self.calls: list[str] = []

    async def async_create_collection(self, payload: Any, *, idempotency_key: str) -> Any:
        if self.fail:
            raise WorkerApiError("the Worker is unreachable")
        self.calls.append("create_collection")
        return {"revision": 1}

    async def async_patch_collection(
        self, collection_id: str, revision: int, payload: Any, *, idempotency_key: str
    ) -> Any:
        self.calls.append("patch_collection")
        return {"revision": revision + 1}

    async def async_create_profile(self, payload: Any, *, idempotency_key: str) -> Any:
        if self.fail:
            raise WorkerApiError("the Worker is unreachable")
        self.calls.append("create_profile")
        return {"revision": 1}

    async def async_patch_profile(
        self, profile_id: str, revision: int, payload: Any, *, idempotency_key: str
    ) -> Any:
        self.calls.append("patch_profile")
        return {"revision": revision + 1}

    async def async_list_collections(self) -> tuple[WorkerCollection, ...]:
        if self.fail:
            raise WorkerApiError("the Worker is unreachable")
        return (self.stored,)

    async def async_list_profile_records(self) -> dict[str, Any]:
        if self.fail:
            raise WorkerApiError("the Worker is unreachable")
        return {
            "cinema": {"id": "cinema", "name": "Cinema", "revision": 1, "settings": self.settings}
        }


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "worker.local"},
        subentries_data=[
            ConfigSubentryData(
                data=_COLLECTION_DATA,
                subentry_type=SUBENTRY_COLLECTION,
                title="Films",
                unique_id="films",
            ),
            ConfigSubentryData(
                data={"profile_id": "cinema", "name": "Cinema", "settings": _SETTINGS},
                subentry_type=SUBENTRY_PROFILE,
                title="Cinema",
                unique_id="cinema",
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_migration_removes_subentries_only_after_reading_them_back(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    client = _FakeClient()

    migrated = await async_migrate_subentries(hass, entry, client)  # type: ignore[arg-type]

    assert migrated is True
    assert entry.subentries == {}
    assert entry.data[CONF_MIGRATED_TO_WORKER] is True


@pytest.mark.asyncio
async def test_migration_keeps_subentries_when_the_read_back_disagrees(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    # The Worker accepted every write but stored a different order.
    client = _FakeClient(stored=_stored_collection(ordered_clip_ids=[]))

    migrated = await async_migrate_subentries(hass, entry, client)  # type: ignore[arg-type]

    assert migrated is False
    assert len(entry.subentries) == 2
    assert CONF_MIGRATED_TO_WORKER not in entry.data


@pytest.mark.asyncio
async def test_migration_keeps_subentries_when_a_profile_did_not_survive(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    client = _FakeClient(settings={"profile_version": 1, "hardware_acceleration": True})

    migrated = await async_migrate_subentries(hass, entry, client)  # type: ignore[arg-type]

    assert migrated is False
    assert len(entry.subentries) == 2


@pytest.mark.asyncio
async def test_migration_keeps_subentries_when_the_worker_is_unreachable(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    client = _FakeClient(fail=True)

    migrated = await async_migrate_subentries(hass, entry, client)  # type: ignore[arg-type]

    assert migrated is False
    assert len(entry.subentries) == 2


@pytest.mark.asyncio
async def test_migration_is_a_no_op_once_the_entry_is_marked(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    client = _FakeClient()
    assert await async_migrate_subentries(hass, entry, client) is True  # type: ignore[arg-type]
    client.calls.clear()

    assert await async_migrate_subentries(hass, entry, client) is True  # type: ignore[arg-type]

    assert client.calls == []


@pytest.mark.asyncio
async def test_migration_marks_an_entry_that_never_had_subentries(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "worker.local"})
    entry.add_to_hass(hass)
    client = _FakeClient()

    assert await async_migrate_subentries(hass, entry, client) is True  # type: ignore[arg-type]

    assert entry.data[CONF_MIGRATED_TO_WORKER] is True
    assert client.calls == []


def test_setup_refuses_a_worker_that_cannot_own_the_configuration() -> None:
    from types import SimpleNamespace

    import pytest as _pytest
    from homeassistant.exceptions import ConfigEntryNotReady

    from custom_components.cinema_collections import _require_supported_worker
    from custom_components.cinema_collections.const import MINIMUM_WORKER_VERSION

    old = SimpleNamespace(data=SimpleNamespace(health=SimpleNamespace(worker_version="1.7.2")))
    with _pytest.raises(ConfigEntryNotReady, match=MINIMUM_WORKER_VERSION):
        _require_supported_worker(old)  # type: ignore[arg-type]

    current = SimpleNamespace(data=SimpleNamespace(health=SimpleNamespace(worker_version="1.8.0")))
    _require_supported_worker(current)  # type: ignore[arg-type]
