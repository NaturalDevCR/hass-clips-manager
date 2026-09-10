import json
from pathlib import Path

from cinema_collections_worker.api import create_app
from cinema_collections_worker.paths import RootKey
from cinema_collections_worker.settings import WorkerMode, WorkerSettings
from fastapi.testclient import TestClient
from pydantic import SecretStr

_TOKEN = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"


def _app(tmp_path: Path, *, mode: WorkerMode = WorkerMode.APP):
    data = tmp_path / "data"
    return create_app(
        WorkerSettings(
            mode=mode,
            bearer_secret=SecretStr(_TOKEN),
            data_dir=data,
            database_path=data / "worker.sqlite3",
            log_dir=data / "logs",
            temp_dir=data / "temp",
            roots={
                RootKey.SOURCE: tmp_path / "media" / "source",
                RootKey.COMPILED: tmp_path / "media" / "compiled",
                RootKey.TEMP: data / "temp",
                RootKey.ASSETS: data / "assets",
            },
            disk_reserve_bytes=0,
        )
    )


def _api_headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_TOKEN}",
        "Idempotency-Key": key,
    }


def _seed_collection(client: TestClient) -> None:
    response = client.post(
        "/api/v1/collections",
        headers=_api_headers("manager-collection"),
        json={
            "id": "films",
            "name": "Films",
            "source_directory": "films",
            "processing_profile_id": "compatibility-4k-loudness",
        },
    )
    assert response.status_code == 201


def _manager_session(client: TestClient, *, ingress: bool = True) -> str:
    headers = (
        {"X-Ingress-Path": "/api/hassio_ingress/session"}
        if ingress
        else {"Authorization": f"Bearer {_TOKEN}"}
    )
    response = client.get("/", headers=headers)
    assert response.status_code == 200
    assert '<div id="app">' in response.text
    assert client.cookies.get("cinema_collections_manager")
    return response.headers["X-CSRF-Token"]


def test_manager_requires_ingress_or_bearer_and_rejects_missing_csrf(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    assert client.get("/").status_code == 401
    csrf = _manager_session(client)
    rejected = client.post(
        "/manager/upload?collection_id=films",
        headers={"X-Filename": "clip.mp4"},
        content=b"clip",
    )

    assert rejected.status_code == 403
    assert csrf


def test_manager_scan_route_queues_a_library_wide_or_scoped_scan_job(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    csrf = _manager_session(client)
    headers = {"X-CSRF-Token": csrf}

    library_wide = client.post("/manager/scan", headers=headers)
    scoped = client.post("/manager/scan?collection_id=films", headers=headers)

    assert library_wide.status_code == scoped.status_code == 202
    assert library_wide.json()["details"]["collection_id"] is None
    assert scoped.json()["details"]["collection_id"] == "films"


def test_manager_job_status_route_backs_row_progress_polling(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    csrf = _manager_session(client)
    headers = {"X-CSRF-Token": csrf}
    job_id = client.post("/manager/scan?collection_id=films", headers=headers).json()["details"][
        "job_id"
    ]

    status = client.get(f"/manager/jobs/{job_id}")

    assert status.status_code == 200
    body = status.json()
    assert body["id"] == job_id
    assert body["state"] in {"queued", "running", "succeeded", "failed"}
    assert body["progress"]["stage"]
    assert 0 <= body["progress"]["percent"] <= 100

    assert TestClient(_app(tmp_path)).get(f"/manager/jobs/{job_id}").status_code == 401
    assert client.get("/manager/jobs/not-a-real-job").status_code == 404


def test_external_manager_requires_bearer_before_issuing_session(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, mode=WorkerMode.EXTERNAL))

    assert client.get("/", headers={"X-Ingress-Path": "/forged"}).status_code == 401
    assert _manager_session(client, ingress=False)


def test_manager_asset_upload_and_listing_round_trip(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    csrf = _manager_session(client)
    headers = {"X-CSRF-Token": csrf}

    assert client.get("/manager/assets").json() == []

    uploaded = client.post(
        "/manager/upload-asset",
        headers={**headers, "X-Filename": "intro.mp4"},
        content=b"clip",
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["details"]["filename"] == "intro.mp4"

    listed = client.get("/manager/assets")
    assert listed.status_code == 200
    assert listed.json() == ["intro.mp4"]

    rejected = client.post(
        "/manager/upload-asset",
        headers={"X-Filename": "notes.txt"},
        content=b"clip",
    )
    assert rejected.status_code == 403


def test_manager_routes_complete_exact_clip_lifecycle(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    csrf = _manager_session(client)
    action_headers = {"X-CSRF-Token": csrf}

    uploaded = client.post(
        "/manager/upload?collection_id=films",
        headers={**action_headers, "X-Filename": "clip.mp4"},
        content=b"clip",
    )
    assert uploaded.status_code == 201
    clip_id = uploaded.json()["id"]

    directory = client.post(
        "/manager/collections/films/directories",
        headers=action_headers,
        json={"relative_path": "organized"},
    )
    assert directory.status_code == 201

    metadata = client.post(
        f"/manager/clips/{clip_id}/metadata",
        headers=action_headers,
        json={"tags": ["featured"], "notes": "Ready"},
    )
    moved = client.post(
        f"/manager/clips/{clip_id}/move",
        headers=action_headers,
        json={"destination_relative_path": "films/organized/renamed.mp4"},
    )
    scan = client.post(f"/manager/clips/{clip_id}/scan", headers=action_headers)
    recompile = client.post(f"/manager/clips/{clip_id}/recompile", headers=action_headers)

    assert metadata.json()["metadata"]["tags"] == ["featured"]
    assert moved.json()["relative_source_path"] == "films/organized/renamed.mp4"
    assert scan.status_code == recompile.status_code == 202

    trashed = client.post(
        f"/manager/clips/{clip_id}/trash",
        headers=action_headers,
        json={"target": "source"},
    )
    trash_id = trashed.json()["details"]["trash_id"]
    restored = client.post(f"/manager/trash/{trash_id}/restore", headers=action_headers)
    assert restored.json()["id"] == clip_id

    confirmation = client.get(f"/manager/clips/{clip_id}/delete-confirmation?target=source").json()[
        "confirmation"
    ]
    deleted = client.post(
        f"/manager/clips/{clip_id}/delete",
        headers=action_headers,
        json={"target": "source", "confirmation": confirmation},
    )

    assert deleted.status_code == 200
    assert deleted.json()["action_type"] == "library.permanently_deleted"


def test_manager_logs_route_requires_auth_and_returns_written_entries(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    assert client.get("/manager/logs").status_code == 401

    csrf = _manager_session(client)
    assert csrf
    assert client.get("/manager/logs").json() == []

    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "INSERT INTO worker_logs(timestamp,level,message,job_id) VALUES(?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", "error", "boom", "job-1"),
        )

    body = client.get("/manager/logs").json()

    assert body == [
        {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "level": "error",
            "message": "boom",
            "job_id": "job-1",
        }
    ]


def test_manager_logs_route_is_capped_at_200_entries_newest_first(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _manager_session(client)
    with client.app.state.database.connection:
        for index in range(250):
            client.app.state.database.connection.execute(
                "INSERT INTO worker_logs(timestamp,level,message,job_id) VALUES(?,?,?,?)",
                (f"2026-01-01T00:00:{index:02d}+00:00", "info", f"message-{index}", None),
            )

    body = client.get("/manager/logs").json()

    assert len(body) == 200
    assert body[0]["message"] == "message-249"
    assert body[-1]["message"] == "message-50"


def test_manager_jobs_route_lists_recent_jobs_and_single_job_route_still_works(
    tmp_path: Path,
) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    csrf = _manager_session(client)
    headers = {"X-CSRF-Token": csrf}
    job_id = client.post("/manager/scan?collection_id=films", headers=headers).json()["details"][
        "job_id"
    ]

    jobs = client.get("/manager/jobs")

    assert jobs.status_code == 200
    assert jobs.json()[0]["id"] == job_id
    assert jobs.json()[0]["kind"] == "scan"
    assert set(jobs.json()[0]) == {
        "id",
        "kind",
        "state",
        "created_at",
        "finished_at",
        "error",
        "clip_id",
        "target",
    }

    single = client.get(f"/manager/jobs/{job_id}")

    assert single.status_code == 200
    assert single.json()["id"] == job_id


def test_manager_asset_delete_round_trip_and_requires_csrf(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    csrf = _manager_session(client)
    headers = {"X-CSRF-Token": csrf}
    uploaded = client.post(
        "/manager/upload-asset",
        headers={**headers, "X-Filename": "intro.mp4"},
        content=b"clip",
    )
    assert uploaded.status_code == 201
    assert client.get("/manager/assets").json() == ["intro.mp4"]

    deleted = client.post("/manager/assets/intro.mp4/delete", headers=headers)

    assert deleted.status_code == 200
    assert deleted.json()["action_type"] == "library.asset_deleted"
    assert deleted.json()["details"]["filename"] == "intro.mp4"
    assert client.get("/manager/assets").json() == []

    client.post(
        "/manager/upload-asset",
        headers={**headers, "X-Filename": "again.mp4"},
        content=b"clip",
    )
    rejected = client.post("/manager/assets/again.mp4/delete")

    assert rejected.status_code == 403


def test_manager_asset_delete_surfaces_profile_reference_refusal(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    csrf = _manager_session(client)
    headers = {"X-CSRF-Token": csrf}
    client.post(
        "/manager/upload-asset",
        headers={**headers, "X-Filename": "intro.mp4"},
        content=b"clip",
    )
    profile = client.get("/api/v1/profiles", headers={"Authorization": f"Bearer {_TOKEN}"}).json()[
        "items"
    ][0]
    settings = profile["settings"]
    settings["intro_reference"] = "intro.mp4"
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "UPDATE profiles SET settings=? WHERE id=?",
            (json.dumps(settings, sort_keys=True), profile["id"]),
        )

    refused = client.post("/manager/assets/intro.mp4/delete", headers=headers)

    assert refused.status_code == 422
    assert "still referenced" in refused.json()["message"]
    assert profile["id"] in refused.json()["message"]
    assert client.get("/manager/assets").json() == ["intro.mp4"]


def test_failed_clip_reports_its_failure_reason(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    _manager_session(client)
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "INSERT INTO clips(id,collection_id,state,relative_source_path,relative_output_path,"
            "duration_seconds,output_available,metadata,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "11111111-1111-1111-1111-111111111111",
                "films",
                "failed",
                "films/broken.mp4",
                "films/11111111-1111-1111-1111-111111111111.mp4",
                0.0,
                0,
                '{"failed_reason": "loudness analysis failed"}',
                "2026-01-01T00:00:00+00:00",
            ),
        )

    clip = client.get("/manager/clips").json()[0]

    assert clip["state"] == "failed"
    assert clip["failed_reason"] == "loudness analysis failed"


def test_failed_compile_persists_sanitized_clip_failure_reason(tmp_path: Path) -> None:
    from cinema_collections_worker.jobs import CompileRequest, JobWorker
    from cinema_collections_worker.profile_validation import ProcessingProfile

    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    csrf = _manager_session(client)
    uploaded = client.post(
        "/manager/upload?collection_id=films",
        headers={"X-CSRF-Token": csrf, "X-Filename": "clip.mp4"},
        content=b"clip",
    )
    assert uploaded.status_code == 201
    source_root = str(client.app.state.resolver.roots[RootKey.SOURCE])
    disabled = ProcessingProfile(loudness={"mode": "disabled"}).model_dump(mode="json")
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "UPDATE profiles SET settings=? WHERE id='compatibility-4k-loudness'",
            (json.dumps(disabled, sort_keys=True),),
        )
        client.app.state.database.connection.execute("UPDATE clips SET state='failed'")

    class _FailedProcess:
        pid = 31337
        returncode = 1

        def communicate(self, timeout):
            return ("", f"ffmpeg exploded: bearer SECRET at {source_root}")

    job = client.app.state.jobs.enqueue_compile(
        CompileRequest(collection_id="films", strategy="compile_stale_only", max_attempts=1)
    )[0]
    worker = JobWorker(
        client.app.state.database,
        client.app.state.resolver,
        process_factory=lambda command, **_kwargs: _FailedProcess(),
    )
    result = worker.run_once()

    assert result is not None and result.job.state.value == "failed"
    row = client.app.state.database.connection.execute(
        "SELECT metadata FROM clips WHERE id=?", (job.clip_id,)
    ).fetchone()
    metadata = json.loads(row["metadata"])
    assert "failed_reason" in metadata
    assert "ffmpeg exploded" in metadata["failed_reason"]
    assert "Bearer [REDACTED]" in metadata["failed_reason"]
    assert "SECRET" not in metadata["failed_reason"]
    assert "[WORKER_ROOT]" in metadata["failed_reason"]


def test_manager_clips_report_output_duration_and_tags(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    _manager_session(client)
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "INSERT INTO clips(id,collection_id,state,relative_source_path,relative_output_path,"
            "duration_seconds,output_available,metadata,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "22222222-2222-2222-2222-222222222222",
                "films",
                "ready",
                "films/ready.mp4",
                "films/22222222-2222-2222-2222-222222222222.mp4",
                125.5,
                1,
                '{"tags": ["night", "featured"]}',
                "2026-01-01T00:00:00+00:00",
            ),
        )

    clip = client.get("/manager/clips").json()[0]

    assert clip["relative_output_path"] == "films/22222222-2222-2222-2222-222222222222.mp4"
    assert clip["output_available"] is True
    assert clip["duration_seconds"] == 125.5
    assert clip["tags"] == ["night", "featured"]


def test_manager_chunked_clip_upload_round_trip(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    csrf = _manager_session(client)
    headers = {"X-CSRF-Token": csrf}

    begun = client.post(
        "/manager/uploads?kind=clip&collection_id=films",
        headers={**headers, "X-Filename": "clip.mp4"},
    )
    assert begun.status_code == 201
    upload_id = begun.json()["upload_id"]

    first = client.post(f"/manager/uploads/{upload_id}/chunk", headers=headers, content=b"cl")
    second = client.post(f"/manager/uploads/{upload_id}/chunk", headers=headers, content=b"ip")
    assert first.status_code == second.status_code == 200
    assert first.json() == {"received": 2}
    assert second.json() == {"received": 4}

    finished = client.post(f"/manager/uploads/{upload_id}/finish", headers=headers)
    assert finished.status_code == 201
    assert finished.json()["relative_source_path"] == "films/clip.mp4"
    source = client.app.state.resolver.roots[RootKey.SOURCE]
    assert (source / "films" / "clip.mp4").read_bytes() == b"clip"

    assert client.post(f"/manager/uploads/{upload_id}/chunk", headers=headers).status_code == 404


def test_manager_chunked_asset_upload_round_trip(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    csrf = _manager_session(client)
    headers = {"X-CSRF-Token": csrf}

    begun = client.post(
        "/manager/uploads?kind=asset",
        headers={**headers, "X-Filename": "intro.mp4"},
    )
    assert begun.status_code == 201
    upload_id = begun.json()["upload_id"]

    assert client.post(
        f"/manager/uploads/{upload_id}/chunk", headers=headers, content=b"in"
    ).json() == {"received": 2}
    assert client.post(
        f"/manager/uploads/{upload_id}/chunk", headers=headers, content=b"tro"
    ).json() == {"received": 5}

    finished = client.post(f"/manager/uploads/{upload_id}/finish", headers=headers)
    assert finished.status_code == 201
    assert finished.json()["details"]["filename"] == "intro.mp4"
    assert client.get("/manager/assets").json() == ["intro.mp4"]


def test_manager_chunked_upload_abort_removes_staging_and_requires_csrf(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    csrf = _manager_session(client)
    headers = {"X-CSRF-Token": csrf}

    unauthenticated = client.post(
        "/manager/uploads?kind=clip&collection_id=films",
        headers={"X-Filename": "clip.mp4"},
    )
    assert unauthenticated.status_code == 403

    begun = client.post(
        "/manager/uploads?kind=clip&collection_id=films",
        headers={**headers, "X-Filename": "clip.mp4"},
    )
    upload_id = begun.json()["upload_id"]
    client.post(f"/manager/uploads/{upload_id}/chunk", headers=headers, content=b"clip")
    staging = client.app.state.library_manager._upload_staging_root()
    assert any(staging.iterdir())

    aborted = client.post(f"/manager/uploads/{upload_id}/abort", headers=headers)
    assert aborted.status_code == 200
    assert list(staging.iterdir()) == []
    assert client.post(f"/manager/uploads/{upload_id}/finish", headers=headers).status_code == 404


def test_manager_clips_carry_the_order_editor_fields(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    _manager_session(client)
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "INSERT INTO clips(id,collection_id,state,relative_source_path,relative_output_path,"
            "duration_seconds,output_available,metadata,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "55555555-5555-5555-5555-555555555555",
                "films",
                "ready",
                "films/feature.mp4",
                "films/55555555-5555-5555-5555-555555555555.mp4",
                125.5,
                1,
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )

    clip = client.get("/manager/clips").json()[0]

    assert clip["collection_id"] == "films"
    assert clip["state"] == "ready"
    assert clip["duration_seconds"] == 125.5
    assert clip["relative_source_path"] == "films/feature.mp4"
    assert clip["sequential_rank"] == 0


def test_manager_collections_include_collections_without_clips(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    _manager_session(client)

    assert client.get("/manager/collections").json() == [{"id": "films", "name": "Films"}]


def _seed_catalogued_clip(client: TestClient, name: str = "clip.mp4") -> str:
    response = client.post(
        "/manager/upload?collection_id=films",
        headers={"X-Filename": name, "X-CSRF-Token": _manager_session(client)},
        content=b"clip-bytes",
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_manager_clips_route_returns_the_catalog_as_json(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    clip_id = _seed_catalogued_clip(client)

    payload = client.get("/manager/clips").json()

    assert [entry["id"] for entry in payload] == [clip_id]
    assert set(payload[0]) == {
        "id",
        "collection_id",
        "relative_source_path",
        "relative_output_path",
        "output_available",
        "state",
        "duration_seconds",
        "sequential_rank",
        "tags",
        "notes",
        "failed_reason",
    }
    assert payload[0]["collection_id"] == "films"
    assert payload[0]["tags"] == []
    assert payload[0]["failed_reason"] is None


def test_manager_clips_route_requires_a_session(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    assert client.get("/manager/clips").status_code == 401
    assert client.get("/manager/collections").status_code == 401
    assert client.get("/manager/session").status_code == 401


def test_manager_session_route_mints_a_fresh_order_capability(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    csrf = _manager_session(client)

    payload = client.get("/manager/session").json()

    assert payload["csrf"] == csrf
    assert payload["worker_version"]
    assert "." in payload["order_bridge_capability"]


def test_manager_collections_route_lists_configured_collections(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    _manager_session(client)

    assert client.get("/manager/collections").json() == [{"id": "films", "name": "Films"}]


def test_manager_page_serves_the_single_page_shell(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    response = client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/session"})

    assert response.status_code == 200
    assert '<div id="app">' in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-CSRF-Token"]
    assert client.cookies.get("cinema_collections_manager")


def test_manager_shell_never_uses_ingress_breaking_absolute_asset_urls(
    tmp_path: Path, manager_ui_shell: Path
) -> None:
    # Home Assistant Ingress serves this page under a per-install path prefix.
    # An asset URL with a leading "/" resolves against the domain root instead
    # of that prefix and never reaches this app, so the built shell must only
    # reference its bundle relatively.
    client = TestClient(_app(tmp_path))

    body = client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/session"}).text

    assert 'src="/' not in body
    assert 'href="/' not in body
    assert manager_ui_shell.is_file()


def test_manager_page_reports_an_unbuilt_interface(tmp_path: Path, manager_ui_shell: Path) -> None:
    client = TestClient(_app(tmp_path))
    manager_ui_shell.unlink()

    response = client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/session"})

    assert response.status_code == 503
    assert "npm --prefix ui" in response.json()["message"]


def test_manager_jobs_report_what_each_job_acts_on(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    csrf = _manager_session(client)
    assert (
        client.post("/manager/scan?collection_id=films", headers={"X-CSRF-Token": csrf}).status_code
        == 202
    )
    # A compile job is seeded directly: the route reports what the jobs table
    # holds, and driving a real compilation would need a catalogued clip and
    # FFmpeg without testing anything more about this route.
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "INSERT INTO jobs(id,kind,state,progress,created_at,collection_id,clip_id,payload) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                "33333333-3333-3333-3333-333333333333",
                "compile",
                "succeeded",
                "{}",
                "2026-01-01T00:00:00+00:00",
                "films",
                "44444444-4444-4444-4444-444444444444",
                '{"source_relative_path": "films/feature.mp4"}',
            ),
        )

    jobs = client.get("/manager/jobs").json()

    compile_job = next(job for job in jobs if job["kind"] == "compile")
    assert compile_job["target"] == "films/feature.mp4"
    assert compile_job["clip_id"] == "44444444-4444-4444-4444-444444444444"
    scan_job = next(job for job in jobs if job["kind"] == "scan")
    assert scan_job["target"] == "films"
    assert scan_job["clip_id"] is None


def test_manager_jobs_report_a_library_wide_scan_with_no_target(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    csrf = _manager_session(client)
    assert client.post("/manager/scan", headers={"X-CSRF-Token": csrf}).status_code == 202

    scan_job = next(job for job in client.get("/manager/jobs").json() if job["kind"] == "scan")

    assert scan_job["target"] == ""
