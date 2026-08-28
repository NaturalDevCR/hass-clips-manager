# ruff: noqa: E501

import json
from datetime import UTC, datetime
from pathlib import Path

from cinema_collections_worker.api import create_app
from cinema_collections_worker.paths import RootKey
from cinema_collections_worker.profile_validation import ProcessingProfile
from cinema_collections_worker.settings import WorkerSettings
from fastapi.testclient import TestClient
from pydantic import SecretStr


def _client(tmp_path: Path) -> TestClient:
    settings = WorkerSettings(
        bearer_secret=SecretStr("test-token"),
        data_dir=tmp_path,
        database_path=tmp_path / "worker.sqlite3",
        log_dir=tmp_path / "logs",
        temp_dir=tmp_path / "tmp",
        roots={key: tmp_path / key.value for key in RootKey},
    )
    return TestClient(create_app(settings))


def _headers(key: str = "request-1") -> dict[str, str]:
    return {"Authorization": "Bearer test-token", "Idempotency-Key": key}


def _create_profile_and_collection(client: TestClient) -> None:
    profile = client.post(
        "/api/v1/profiles",
        headers=_headers("profile"),
        json={
            "id": "default",
            "name": "Default",
            "settings": ProcessingProfile().model_dump(mode="json"),
        },
    )
    assert profile.status_code == 201
    collection = client.post(
        "/api/v1/collections",
        headers=_headers("collection"),
        json={
            "id": "films",
            "name": "Films",
            "source_directory": "films",
            "processing_profile_id": "default",
        },
    )
    assert collection.status_code == 201


def test_create_replays_idempotently_and_patch_rejects_stale_revision(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _create_profile_and_collection(client)
    payload = {
        "id": "shows",
        "name": "Shows",
        "source_directory": "shows",
        "processing_profile_id": "default",
    }

    first = client.post("/api/v1/collections", headers=_headers("shows"), json=payload)
    replay = client.post("/api/v1/collections", headers=_headers("shows"), json=payload)
    stale = client.patch(
        "/api/v1/collections/shows",
        headers={**_headers("stale"), "If-Match-Revision": "99"},
        json={"name": "Stale"},
    )

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert stale.status_code == 409
    assert stale.json()["code"] == "conflict"


def test_validation_response_has_contract_shape(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/v1/collections",
        headers=_headers(),
        json={"id": "not valid", "name": "", "source_directory": "../escape"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.json()["retryable"] is False
    assert isinstance(response.json()["details"], list)


def test_scan_compile_and_cancellation_are_accepted_as_jobs(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _create_profile_and_collection(client)
    source = tmp_path / "source" / "films"
    source.mkdir(parents=True)
    (source / "clip.mp4").write_bytes(b"clip")
    scan = client.post("/api/v1/scan", headers=_headers("scan"), json={"collection_ids": ["films"]})
    assert scan.status_code == 202
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "INSERT INTO clips(id, collection_id, state, relative_source_path, relative_output_path, "
            "duration_seconds, output_available, metadata, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "00000000-0000-0000-0000-000000000003",
                "films",
                "discovered",
                "films/clip.mp4",
                "films/clip.mp4",
                3.0,
                0,
                json.dumps({"source_fingerprint": "source", "size_bytes": 4}),
                "now",
            ),
        )
    compile_response = client.post(
        "/api/v1/compile", headers=_headers("compile"), json={"collection_id": "films"}
    )
    assert compile_response.status_code == 202
    job_id = compile_response.json()["id"]

    cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=_headers("cancel"))
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"


def test_compile_api_rejects_undocumented_fields_and_strategies(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _create_profile_and_collection(client)

    max_attempts = client.post(
        "/api/v1/compile",
        headers=_headers("max-attempts"),
        json={"collection_id": "films", "max_attempts": 9},
    )
    strategy = client.post(
        "/api/v1/compile",
        headers=_headers("bad-strategy"),
        json={"collection_id": "films", "strategy": "anything"},
    )

    assert max_attempts.status_code == 422
    assert max_attempts.json()["code"] == "validation_error"
    assert any(error["type"] == "extra_forbidden" for error in max_attempts.json()["details"])
    assert strategy.status_code == 422
    assert strategy.json()["code"] == "validation_error"
    assert any(error["type"] == "literal_error" for error in strategy.json()["details"])


def test_list_endpoints_reject_out_of_contract_pagination(tmp_path: Path) -> None:
    client = _client(tmp_path)

    for path in ("collections", "profiles", "clips", "jobs", "logs"):
        response = client.get(f"/api/v1/{path}?page=0&page_size=101", headers=_headers())
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"


def test_clip_output_availability_and_paginated_lookups(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _create_profile_and_collection(client)
    clip_id = "00000000-0000-0000-0000-000000000001"
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "INSERT INTO clips(id, collection_id, state, relative_source_path, relative_output_path, "
            "duration_seconds, output_available, metadata, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                clip_id,
                "films",
                "ready",
                "films/in.mp4",
                "films/out.mp4",
                3.0,
                1,
                json.dumps({}),
                datetime.now(UTC).isoformat(),
            ),
        )

    page = client.get("/api/v1/clips?page=1&page_size=25", headers=_headers())
    found = client.get(f"/api/v1/clips/{clip_id}", headers=_headers())
    missing = client.get("/api/v1/clips/00000000-0000-0000-0000-000000000002", headers=_headers())

    assert page.status_code == 200 and page.json()["total"] == 1
    assert found.status_code == 200 and found.json()["output_available"] is True
    assert missing.status_code == 404 and missing.json()["code"] == "not_found"
