"""Coordinator and entity behavior for Cinema Collections."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import orjson
import pytest
from homeassistant.util import dt as dt_util

from custom_components.cinema_collections.api_client import WorkerApiConnectionError
from custom_components.cinema_collections.coordinator import CinemaCollectionsCoordinator
from custom_components.cinema_collections.history import PlaybackHistoryStore
from custom_components.cinema_collections.models import WorkerHealth, WorkerStatus
from custom_components.cinema_collections.resolver import CollectionPolicy, OverrideMode
from custom_components.cinema_collections.sensor import SENSOR_DESCRIPTIONS, CinemaCollectionsSensor


class Worker:
    """Small Worker double exposing only the coordinator contract."""

    async def async_health(self) -> WorkerHealth:
        return WorkerHealth.from_dict(
            {
                "status": "ok",
                "component": "cinema-collections-worker",
                "worker_version": "1.2.3",
                "api_version": "1.0.0",
                "min_client_version": "1.0.0",
                "max_client_version": "1.x",
            }
        )

    async def async_status(self) -> WorkerStatus:
        return WorkerStatus.from_dict(
            {
                "queue_depth": 2,
                "current_job": {
                    "id": "job-1",
                    "state": "running",
                    "progress": {"stage": "encoding", "percent": 54.0, "eta_seconds": 12},
                },
                "storage": {},
                "scans": {},
                "latest_errors": [],
            }
        )


@pytest.mark.asyncio
async def test_coordinator_exposes_policy_queue_progress_and_compatibility(hass) -> None:
    """Dashboard entities need local policy state together with Worker status."""
    coordinator = CinemaCollectionsCoordinator(
        hass,
        Worker(),
        collections=lambda: (CollectionPolicy("films", is_default=True),),
        override=lambda: OverrideMode.automatic(),
        now=lambda: datetime(2026, 8, 27, tzinfo=UTC),
    )

    snapshot = await coordinator.async_update_data()

    assert snapshot.active_collection_id == "films"
    assert snapshot.reason == "schedule"
    assert snapshot.queue_depth == 2
    assert snapshot.progress == {"stage": "encoding", "percent": 54.0, "eta_seconds": 12}
    assert snapshot.compatibility["api_version"] == "1.0.0"
    assert snapshot.available is True


@pytest.mark.asyncio
async def test_coordinator_keeps_policy_visible_when_worker_disconnects(hass) -> None:
    """A Worker outage is a degraded state, never a coordinator crash."""

    class DisconnectedWorker(Worker):
        async def async_status(self) -> WorkerStatus:
            raise WorkerApiConnectionError("Worker request could not be completed")

    coordinator = CinemaCollectionsCoordinator(
        hass,
        DisconnectedWorker(),
        collections=lambda: (CollectionPolicy("films", is_default=True),),
        override=lambda: OverrideMode.automatic(),
    )

    snapshot = await coordinator.async_update_data()

    assert snapshot.available is False
    assert snapshot.active_collection_id == "films"
    assert snapshot.reason == "schedule"
    assert "could not be completed" in (snapshot.error or "")


def test_entity_modules_publish_required_stable_entity_keys() -> None:
    """Every monitoring/control entity is declared without a device entity."""
    from custom_components.cinema_collections.button import BUTTON_DESCRIPTIONS
    from custom_components.cinema_collections.select import SELECT_DESCRIPTION
    from custom_components.cinema_collections.sensor import SENSOR_DESCRIPTIONS

    assert {description.key for description in SENSOR_DESCRIPTIONS} == {
        "active_collection",
        "processing_status",
        "queue_depth",
        "latest_error",
        "current_job_progress",
        "worker_version",
        "next_schedule",
        "collection_priorities",
        "compilation_summary",
        "clip_states",
    }
    assert {description.key for description in BUTTON_DESCRIPTIONS} == {
        "scan_library",
        "compile_all",
        "retry_failed",
        "cancel_processing",
        "cancel_all_processing",
        "cleanup_temporaries",
        "reset_history",
    }
    assert SELECT_DESCRIPTION.key == "collection_override"


@pytest.mark.asyncio
async def test_buttons_dispatch_only_their_expected_validated_actions(hass, monkeypatch) -> None:
    """Buttons initiate Worker/history requests but never create a device-control path."""
    from custom_components.cinema_collections import button as button_platform

    dispatched: list[str] = []

    async def record_action(_hass, _entry, action: str, _data) -> None:
        dispatched.append(action)

    monkeypatch.setattr(button_platform, "async_run_action", record_action)
    entry = SimpleNamespace(entry_id="button-entry")
    for description in button_platform.BUTTON_DESCRIPTIONS:
        await button_platform.CinemaCollectionsButton(hass, entry, description).async_press()

    assert dispatched == [description.key for description in button_platform.BUTTON_DESCRIPTIONS]


@pytest.mark.asyncio
async def test_select_option_persists_explicit_override_mode(monkeypatch) -> None:
    """Selecting a stable collection ID stores an explicit OverrideMode, not its name."""
    from custom_components.cinema_collections import services
    from custom_components.cinema_collections.const import (
        CONF_OVERRIDE_COLLECTION_ID,
        CONF_OVERRIDE_MODE,
    )

    class ConfigEntries:
        @staticmethod
        def async_update_entry(entry, *, options) -> None:
            entry.options = options

    class Coordinator:
        async def async_request_refresh(self) -> None:
            return None

    entry = SimpleNamespace(options={})
    hass = SimpleNamespace(config_entries=ConfigEntries())
    monkeypatch.setattr(services, "policies_for_entry", lambda _entry: (CollectionPolicy("films"),))

    await services.async_set_collection_override(
        hass, entry, {"option": "films"}, coordinator=Coordinator()
    )

    assert entry.options[CONF_OVERRIDE_MODE] == "explicit"
    assert entry.options[CONF_OVERRIDE_COLLECTION_ID] == "films"


@pytest.mark.asyncio
async def test_platform_setup_with_home_assistant_assigns_stable_unique_ids(hass) -> None:
    """Each native platform adds its declared entities without creating a device entity."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.cinema_collections import button, select, sensor
    from custom_components.cinema_collections.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, entry_id="real-platform-entry")
    entry.add_to_hass(hass)
    coordinator = CinemaCollectionsCoordinator(
        hass,
        Worker(),
        collections=lambda: (CollectionPolicy("films"),),
        override=OverrideMode.automatic,
        entry=entry,
    )
    hass.data[DOMAIN] = {entry.entry_id: SimpleNamespace(coordinator=coordinator)}
    entities = []

    def add_entities(new_entities) -> None:
        entities.extend(new_entities)

    await sensor.async_setup_entry(hass, entry, add_entities)
    await button.async_setup_entry(hass, entry, add_entities)
    await select.async_setup_entry(hass, entry, add_entities)

    assert {entity.unique_id for entity in entities} == {
        f"{entry.entry_id}_{description.key}"
        for description in (*sensor.SENSOR_DESCRIPTIONS, *button.BUTTON_DESCRIPTIONS)
    } | {f"{entry.entry_id}_{select.SELECT_DESCRIPTION.key}"}


class Clock:
    """A fixed timezone-aware clock for playback-history tests."""

    def now(self) -> datetime:
        return datetime(2026, 8, 27, 20, tzinfo=UTC)


def choose_first(values: tuple[str, ...]) -> str:
    """Make selection order observable without duplicating selection logic."""
    return values[0]


async def make_history(hass, key: str) -> PlaybackHistoryStore:
    history = PlaybackHistoryStore(
        hass,  # type: ignore[arg-type]
        storage_key=key,
        now=Clock().now,
        chooser=choose_first,
    )
    await history.async_setup()
    return history


def make_coordinator(hass, history: PlaybackHistoryStore | None) -> CinemaCollectionsCoordinator:
    return CinemaCollectionsCoordinator(
        hass,
        Worker(),
        collections=lambda: (CollectionPolicy("films", is_default=True),),
        override=lambda: OverrideMode.automatic(),
        history=lambda: history,
        now=lambda: datetime(2026, 8, 27, tzinfo=UTC),
    )


def make_active_collection_sensor(
    coordinator: CinemaCollectionsCoordinator,
) -> CinemaCollectionsSensor:
    description = next(item for item in SENSOR_DESCRIPTIONS if item.key == "active_collection")
    return CinemaCollectionsSensor(coordinator, description, "history-entry")


@pytest.mark.asyncio
async def test_active_collection_sensor_exposes_playback_history_summary(hass) -> None:
    """Selections advance the history attribute without leaking clip ID lists."""
    history = await make_history(hass, "entity-history")
    await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)
    await history.async_reset("films")
    await history.async_select("films", ("clip-a", "clip-b"), dry_run=False)

    coordinator = make_coordinator(hass, history)
    await coordinator.async_refresh()
    sensor = make_active_collection_sensor(coordinator)

    assert coordinator.data.history["films"] == {
        "round_number": 2,
        "played_count": 1,
        "last_selected_clip_id": "clip-a",
        "last_reset_at": dt_util.as_local(datetime(2026, 8, 27, 20, tzinfo=UTC)).isoformat(),
    }
    assert sensor.extra_state_attributes["history"] == dict(coordinator.data.history)
    # Home Assistant serializes entity attributes with orjson (websocket API,
    # recorder, REST) before this repository's own tests ever see them; a
    # value that only fails at that layer (e.g. a nested MappingProxyType)
    # would pass a plain equality assertion but break in a real installation.
    orjson.dumps(sensor.extra_state_attributes)


@pytest.mark.asyncio
async def test_active_collection_sensor_history_omits_unplayed_collections(hass) -> None:
    """A collection with no history yet has no entry, matching the store's view."""
    history = await make_history(hass, "entity-history-unplayed")
    await history.async_select("films", ("clip-a",), dry_run=False)

    coordinator = make_coordinator(hass, history)
    await coordinator.async_refresh()

    assert coordinator.data.history["films"]["played_count"] == 1
    assert "trailers" not in coordinator.data.history


@pytest.mark.asyncio
async def test_active_collection_sensor_history_is_empty_when_store_is_unset(hass) -> None:
    """A runtime without a history store exposes an empty dict, never an error."""
    coordinator = make_coordinator(hass, None)
    await coordinator.async_refresh()
    sensor = make_active_collection_sensor(coordinator)

    assert coordinator.data.history == {}
    assert sensor.extra_state_attributes["history"] == {}
