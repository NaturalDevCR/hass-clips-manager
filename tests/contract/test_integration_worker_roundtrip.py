"""End-to-end contract coverage between the integration client and Worker app.

The in-process transport deliberately uses only a temporary Worker application.
It never starts Home Assistant, touches a configured media directory, or invokes
any device-control service.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from cinema_collections_worker.api import create_app
from cinema_collections_worker.jobs import JobState, JobWorker
from cinema_collections_worker.paths import RootKey
from cinema_collections_worker.profile_validation import ProcessingProfile
from cinema_collections_worker.settings import WorkerSettings
from fastapi.testclient import TestClient
from pydantic import SecretStr

from custom_components.cinema_collections.api_client import (
    WorkerApiClient,
    WorkerApiCompatibilityError,
    WorkerApiConnectionError,
)
from custom_components.cinema_collections.config_flow import _validate_compatibility
from custom_components.cinema_collections.history import HistorySelection
from custom_components.cinema_collections.resolver import CollectionPolicy
from custom_components.cinema_collections.services import async_select_next_clip


class _Response:
    """aiohttp-shaped response backed by the real in-process FastAPI app."""

    def __init__(self, response: Any, *, incompatible_health: bool = False) -> None:
        self.status = response.status_code
        self._body = response.json()
        if incompatible_health and self.status == 200:
            self._body["api_version"] = "2.0.0"

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self, *, content_type: object = None) -> Mapping[str, Any]:
        assert isinstance(self._body, Mapping)
        return self._body


class _InProcessSession:
    """Transport adapter that faults only the requested Worker read once."""

    def __init__(
        self, app: Any, *, fail_first_health: bool = False, incompatible: bool = False
    ) -> None:
        self._client = TestClient(app)
        self._fail_first_health = fail_first_health
        self._incompatible = incompatible

    @staticmethod
    def _path(url: str) -> str:
        return "/" + url.split("/api/v1/", maxsplit=1)[1]

    def get(self, url: str, **kwargs: Any) -> _Response:
        path = self._path(url)
        if path == "/health" and self._fail_first_health:
            self._fail_first_health = False
            raise OSError("temporary Worker disconnect")
        response = self._client.get(path and f"/api/v1{path}", headers=kwargs["headers"])
        return _Response(response, incompatible_health=path == "/health" and self._incompatible)

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        path = self._path(url)
        response = self._client.request(
            method,
            f"/api/v1{path}",
            headers=kwargs["headers"],
            json=kwargs.get("json"),
        )
        return _Response(response)


class _FinishedProcess:
    """Safe FFmpeg stand-in that only writes its Worker-owned temporary output."""

    pid = 0
    returncode = 0

    def communicate(self, timeout: float) -> tuple[str, str]:
        return "out_time_ms=10000000\nprogress=end", ""


class _ValidProbe:
    def probe(self, path: Path) -> Any:
        from cinema_collections_worker.probe import MediaProbeResult

        return MediaProbeResult(
            valid=path.is_file(),
            duration_seconds=10,
            width=3840,
            height=2160,
            frame_rate=24,
            has_audio=True,
        )


class _MemoryHistory:
    """A deterministic history seam so this contract test needs no HA runtime."""

    def __init__(self) -> None:
        self.claimed: list[str] = []

    async def async_select(
        self, collection_id: str, eligible_clip_ids: tuple[str, ...], dry_run: bool
    ) -> HistorySelection:
        selected = eligible_clip_ids[0] if eligible_clip_ids else None
        if selected is not None and not dry_run:
            self.claimed.append(selected)
        return HistorySelection(collection_id, selected, 1, False)


_STRONG_TOKEN = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"


def _settings(tmp_path: Path) -> WorkerSettings:
    data_dir = tmp_path / "data"
    return WorkerSettings(
        bearer_secret=SecretStr(_STRONG_TOKEN),
        data_dir=data_dir,
        database_path=data_dir / "worker.sqlite3",
        log_dir=data_dir / "logs",
        temp_dir=data_dir / "media" / RootKey.TEMP.value,
        roots={key: data_dir / "media" / key.value for key in RootKey},
        disk_reserve_bytes=0,
    )


async def _seed_collection(client: WorkerApiClient) -> None:
    profile = ProcessingProfile(loudness={"mode": "disabled"}, transitions=[]).model_dump(
        mode="json"
    )
    created_profile = await client.async_create_profile(
        {"id": "default", "name": "Default", "settings": profile}, idempotency_key="profile"
    )
    assert created_profile["id"] == "default"
    created_collection = await client.async_create_collection(
        {
            "id": "films",
            "name": "Films",
            "source_directory": "films",
            "processing_profile_id": "default",
        },
        idempotency_key="collection",
    )
    assert created_collection["id"] == "films"


@pytest.mark.asyncio
async def test_integration_client_recovers_reads_worker_state_and_selects_ready_clip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reconnect can observe one Worker job through its safe ready output."""
    app = create_app(_settings(tmp_path))
    session = _InProcessSession(app, fail_first_health=True)
    client = WorkerApiClient("http://worker.test", _STRONG_TOKEN, session)  # type: ignore[arg-type]

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr("custom_components.cinema_collections.api_client.asyncio.sleep", no_wait)
    health = await client.async_health()
    status = await client.async_status()
    assert health.api_version == "1.0.0"
    assert status.queue_depth == 0

    await _seed_collection(client)
    clip_id = "00000000-0000-0000-0000-000000000015"
    source = app.state.resolver.resolve("source", "films/fixture.mp4")
    source.parent.mkdir(parents=True)
    source.write_bytes(b"generated-test-fixture")
    with app.state.database.connection:
        app.state.database.connection.execute(
            "INSERT INTO clips("
            "id, collection_id, state, relative_source_path, relative_output_path, "
            "duration_seconds, output_available, metadata, updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                clip_id,
                "films",
                "discovered",
                "films/fixture.mp4",
                "films/fixture.mp4",
                10,
                0,
                json.dumps({"source_fingerprint": "fixture-v1", "size_bytes": 1}),
                "now",
            ),
        )

    app.state.catalog.probe_client = _ValidProbe()
    accepted = await client.async_compile("films", idempotency_key="compile")
    queued_jobs = await client.async_list_jobs()
    assert accepted["id"] == queued_jobs[0].id
    assert queued_jobs[0].state == "queued"
    assert queued_jobs[0].progress_stage == "queued"

    def process_factory(command: list[str], **_kwargs: object) -> _FinishedProcess:
        temporary_output = Path(command[-1])
        temporary_output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output.write_bytes(b"compiled-fixture")
        return _FinishedProcess()

    result = JobWorker(
        app.state.database,
        app.state.resolver,
        probe_client=_ValidProbe(),
        process_factory=process_factory,
        queue=app.state.queue,
        catalog=app.state.catalog,
    ).run_once()
    assert result is not None and result.job.state is JobState.SUCCEEDED

    completed_jobs = await client.async_list_jobs()
    clips = await client.async_list_clips()
    assert completed_jobs[0].state == "succeeded"
    assert completed_jobs[0].progress_stage == "complete"
    assert completed_jobs[0].progress_percent == 100
    assert clips[0].id == clip_id and clips[0].output_available is True

    history = _MemoryHistory()
    dry_run = await async_select_next_clip(
        history=history,  # type: ignore[arg-type]
        client=client,
        collections=(CollectionPolicy("films", is_default=True),),
        data={"dry_run": True},
    )
    selected = await async_select_next_clip(
        history=history,  # type: ignore[arg-type]
        client=client,
        collections=(CollectionPolicy("films", is_default=True),),
        data={},
    )
    assert dry_run["clip_id"] == selected["clip_id"] == clip_id
    assert history.claimed == [clip_id]
    assert selected["media_content_id"] == (
        "media-source://media_source/local/cinema-collections/compiled/films/fixture.mp4"
    )


@pytest.mark.asyncio
async def test_incompatible_worker_health_is_rejected_after_the_transport_recovers(
    tmp_path: Path,
) -> None:
    """Compatibility checks reject a valid-but-unsupported Worker contract response."""
    client = WorkerApiClient(
        "http://worker.test",
        _STRONG_TOKEN,
        _InProcessSession(create_app(_settings(tmp_path)), incompatible=True),  # type: ignore[arg-type]
    )

    with pytest.raises(WorkerApiCompatibilityError, match="API major"):
        _validate_compatibility(await client.async_health())


@pytest.mark.asyncio
async def test_transport_disconnect_without_recovery_surfaces_no_device_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Worker outage is observable as a connection error and cannot select media."""
    session = _InProcessSession(create_app(_settings(tmp_path)), fail_first_health=True)
    client = WorkerApiClient("http://worker.test", _STRONG_TOKEN, session)  # type: ignore[arg-type]

    def always_fail(url: str, **kwargs: Any) -> _Response:
        raise OSError("Worker remains disconnected")

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(session, "get", always_fail)
    monkeypatch.setattr("custom_components.cinema_collections.api_client.asyncio.sleep", no_wait)
    with pytest.raises(WorkerApiConnectionError):
        await client.async_health()
