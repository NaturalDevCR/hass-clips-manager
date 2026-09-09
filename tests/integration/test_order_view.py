"""Home Assistant bridge tests for visual playback-order editing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from types import SimpleNamespace

import pytest

from custom_components.cinema_collections.const import DOMAIN, SUBENTRY_COLLECTION
from custom_components.cinema_collections.order_view import (
    ORDER_CAPABILITY_HEADER,
    ORDER_VIEW_URL,
    CinemaCollectionsOrderView,
    async_get_collection_order,
    async_save_collection_order,
)
from custom_components.cinema_collections.subentries import CollectionSubentryData


class ConfigEntries:
    """Minimal config-entry manager that applies subentry updates."""

    @staticmethod
    def async_update_subentry(entry, subentry, *, data, title):
        entry.subentries[subentry.subentry_id] = SimpleNamespace(
            subentry_id=subentry.subentry_id,
            subentry_type=subentry.subentry_type,
            data=data,
            title=title,
        )


class Coordinator:
    """Minimal coordinator refresh double."""

    def __init__(self) -> None:
        self.refreshes = 0

    async def async_request_refresh(self) -> None:
        self.refreshes += 1


def _hass(*, entry_count: int = 1):
    entries: dict[str, SimpleNamespace] = {}
    for index in range(entry_count):
        entry_id = f"entry-{index}"
        collection = CollectionSubentryData(
            collection_id="films",
            name="Films",
            source_directory="films",
            processing_profile_id="4k",
        )
        subentry = SimpleNamespace(
            subentry_id=f"subentry-{index}",
            subentry_type=SUBENTRY_COLLECTION,
            data=collection.as_dict(),
            title=collection.name,
        )
        entry = SimpleNamespace(
            entry_id=entry_id,
            data={"token": "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"},
            subentries={subentry.subentry_id: subentry},
        )
        entries[entry_id] = entry
    runtime = {
        entry_id: SimpleNamespace(entry=entry, coordinator=Coordinator())
        for entry_id, entry in entries.items()
    }
    return SimpleNamespace(
        data={DOMAIN: runtime},
        config_entries=ConfigEntries(),
    ), entries


def _capability() -> str:
    payload = f"{ORDER_VIEW_URL}|{int(time.time()) + 300}|test-nonce".encode()
    signature = hmac.new(
        b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFG", payload, hashlib.sha256
    ).digest()

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    return f"{encode(payload)}.{encode(signature)}"


@pytest.mark.asyncio
async def test_order_bridge_saves_custom_order_and_returns_it() -> None:
    hass, entries = _hass()

    saved = await async_save_collection_order(
        hass,
        collection_id="films",
        ordered_clip_ids=["clip-b", "clip-a"],
    )

    assert saved == {
        "entry_id": "entry-0",
        "collection_id": "films",
        "playback_mode": "custom",
        "ordered_clip_ids": ["clip-b", "clip-a"],
    }
    stored = next(iter(entries["entry-0"].subentries.values())).data
    assert stored["playback_mode"] == "custom"
    assert stored["ordered_clip_ids"] == ["clip-b", "clip-a"]

    current = await async_get_collection_order(hass, collection_id="films")
    assert current == saved


@pytest.mark.asyncio
async def test_order_bridge_rejects_duplicate_ids() -> None:
    hass, _ = _hass()

    with pytest.raises(ValueError, match="non-empty and unique"):
        await async_save_collection_order(
            hass,
            collection_id="films",
            ordered_clip_ids=["clip-a", "clip-a"],
        )


@pytest.mark.asyncio
async def test_order_bridge_requires_entry_id_when_multiple_entries_are_loaded() -> None:
    hass, _ = _hass(entry_count=2)

    with pytest.raises(ValueError, match="entry_id"):
        await async_get_collection_order(hass, collection_id="films")


@pytest.mark.asyncio
async def test_order_bridge_can_target_one_of_multiple_entries() -> None:
    hass, _ = _hass(entry_count=2)

    saved = await async_save_collection_order(
        hass,
        entry_id="entry-1",
        collection_id="films",
        ordered_clip_ids=["clip-a"],
    )

    assert saved["entry_id"] == "entry-1"


@pytest.mark.asyncio
async def test_order_http_view_get_and_post_use_json_contract() -> None:
    hass, _ = _hass()
    view = CinemaCollectionsOrderView()

    class Request:
        app = {"hass": hass}
        query = {"collection_id": "films"}
        headers = {ORDER_CAPABILITY_HEADER: _capability()}

        @staticmethod
        def get(key, default=None):
            return default

        async def json(self):
            return {"collection_id": "films", "ordered_clip_ids": ["clip-b", "clip-a"]}

    get_response = await view.get(Request())
    assert get_response.status == 200
    assert '"playback_mode":"random"' in get_response.text

    post_response = await view.post(Request())
    assert post_response.status == 200
    assert '"ordered_clip_ids":["clip-b","clip-a"]' in post_response.text


@pytest.mark.asyncio
async def test_order_http_view_rejects_missing_or_invalid_capability() -> None:
    hass, _ = _hass()
    view = CinemaCollectionsOrderView()

    class Request:
        app = {"hass": hass}
        query = {"collection_id": "films"}
        headers = {}

        @staticmethod
        def get(key, default=None):
            return default

    response = await view.get(Request())
    assert response.status == 401

    Request.headers = {ORDER_CAPABILITY_HEADER: "not-valid"}
    response = await view.get(Request())
    assert response.status == 401
