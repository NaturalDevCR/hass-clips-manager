# ruff: noqa: E501
"""POST /api/v1/jobs/cancel-all behaviour for the Worker API."""

import json

from cinema_collections_worker.jobs import JobState
from fastapi.testclient import TestClient
from test_api_endpoints import _TOKEN, _client, _create_profile_and_collection, _headers


def _insert_clip(client: TestClient, clip_id: str, *, output_available: bool = False) -> None:
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "INSERT INTO clips(id, collection_id, state, relative_source_path, relative_output_path, "
            "duration_seconds, output_available, metadata, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                clip_id,
                "films",
                "stale",
                f"films/{clip_id}.mp4",
                f"films/{clip_id}.mp4",
                3.0,
                int(output_available),
                json.dumps({"source_fingerprint": clip_id, "size_bytes": 4}),
                "now",
            ),
        )


def _enqueue_compile(client: TestClient) -> list[str]:
    response = client.post(
        "/api/v1/compile",
        headers=_headers("cancel-all-setup"),
        json={"collection_id": "films", "strategy": "compile_stale_only"},
    )
    assert response.status_code == 202
    rows = client.app.state.database.connection.execute(
        "SELECT id FROM jobs ORDER BY created_at, id"
    ).fetchall()
    return [str(row["id"]) for row in rows]


def _job_state(client: TestClient, job_id: str) -> dict[str, object]:
    return dict(
        client.app.state.database.connection.execute(
            "SELECT state, cancel_requested FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
    )


def _clip_state(client: TestClient, clip_id: str) -> str:
    return str(
        client.app.state.database.connection.execute(
            "SELECT state FROM clips WHERE id=?", (clip_id,)
        ).fetchone()[0]
    )


def test_cancel_all_cancels_running_and_queued_jobs_and_restores_clips(tmp_path) -> None:
    client = _client(tmp_path)
    _create_profile_and_collection(client)
    _insert_clip(client, "00000000-0000-0000-0000-000000000001")
    _insert_clip(client, "00000000-0000-0000-0000-000000000002")
    _insert_clip(client, "00000000-0000-0000-0000-000000000003", output_available=True)
    job_ids = _enqueue_compile(client)
    assert len(job_ids) == 3

    running_id = job_ids[0]
    running_clip = str(
        client.app.state.database.connection.execute(
            "SELECT clip_id FROM jobs WHERE id=?", (running_id,)
        ).fetchone()["clip_id"]
    )
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "UPDATE jobs SET state='running', started_at='2026-01-01T00:00:00+00:00' WHERE id=?",
            (running_id,),
        )
        client.app.state.database.connection.execute(
            "UPDATE clips SET state='compiling' WHERE id=?", (running_clip,)
        )

    response = client.post("/api/v1/jobs/cancel-all", headers=_headers("cancel-all"))

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert set(body["job_ids"]) == set(job_ids)
    for job_id in job_ids[1:]:
        assert _job_state(client, job_id) == {"state": "cancelled", "cancel_requested": 1}
    assert _clip_state(client, "00000000-0000-0000-0000-000000000002") == "discovered"
    assert _clip_state(client, "00000000-0000-0000-0000-000000000003") == "stale"
    assert _job_state(client, running_id) == {"state": "running", "cancel_requested": 1}
    assert _clip_state(client, running_clip) == "compiling"

    finished = client.app.state.queue.update(
        client.app.state.queue.get(running_id).model_copy(
            update={"state": JobState.CANCELLED, "cancel_requested": True}
        )
    )
    assert finished.state is JobState.CANCELLED
    assert _clip_state(client, running_clip) == "discovered"


def test_cancel_all_with_no_active_jobs_reports_zero_without_error(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.post("/api/v1/jobs/cancel-all", headers=_headers("cancel-all-empty"))

    assert response.status_code == 200
    assert response.json() == {"count": 0, "job_ids": []}


def test_cancel_all_leaves_finished_jobs_untouched(tmp_path) -> None:
    client = _client(tmp_path)
    _create_profile_and_collection(client)
    _insert_clip(client, "00000000-0000-0000-0000-000000000001")
    _insert_clip(client, "00000000-0000-0000-0000-000000000002")
    _insert_clip(client, "00000000-0000-0000-0000-000000000003")
    job_ids = _enqueue_compile(client)
    expected = {"succeeded", "failed", "cancelled"}
    with client.app.state.database.connection:
        for job_id, state in zip(job_ids, expected, strict=True):
            client.app.state.database.connection.execute(
                "UPDATE jobs SET state=?, finished_at='now' WHERE id=?", (state, job_id)
            )

    response = client.post("/api/v1/jobs/cancel-all", headers=_headers("cancel-all-finished"))

    assert response.status_code == 200
    assert response.json() == {"count": 0, "job_ids": []}
    assert {
        str(
            client.app.state.database.connection.execute(
                "SELECT state FROM jobs WHERE id=?", (job_id,)
            ).fetchone()[0]
        )
        for job_id in job_ids
    } == expected


def test_replaying_cancel_all_key_returns_first_result_without_cancelling_again(tmp_path) -> None:
    client = _client(tmp_path)
    _create_profile_and_collection(client)
    _insert_clip(client, "00000000-0000-0000-0000-000000000001")
    _insert_clip(client, "00000000-0000-0000-0000-000000000002")
    job_ids = _enqueue_compile(client)

    first = client.post("/api/v1/jobs/cancel-all", headers=_headers("cancel-all-replay"))
    assert first.status_code == 200
    assert first.json()["count"] == 2

    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "UPDATE jobs SET state='queued', cancel_requested=0, finished_at=NULL WHERE id=?",
            (job_ids[0],),
        )
        client.app.state.database.connection.execute(
            "UPDATE clips SET state='pending' WHERE id='00000000-0000-0000-0000-000000000001'"
        )

    replay = client.post("/api/v1/jobs/cancel-all", headers=_headers("cancel-all-replay"))

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert _job_state(client, job_ids[0]) == {"state": "queued", "cancel_requested": 0}


def test_cancel_all_enforces_bearer_and_mutation_preconditions(tmp_path) -> None:
    client = _client(tmp_path)

    assert client.post("/api/v1/jobs/cancel-all").status_code == 401
    bearer = {"Authorization": f"Bearer {_TOKEN}"}
    missing_key = client.post("/api/v1/jobs/cancel-all", headers=bearer)
    assert missing_key.status_code == 422
    assert missing_key.json()["code"] == "validation_error"
    unsupported = client.post(
        "/api/v1/jobs/cancel-all",
        headers={**bearer, "Idempotency-Key": "cancel-all", "X-Client-Version": "2.0.0"},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["code"] == "unsupported_client_version"
