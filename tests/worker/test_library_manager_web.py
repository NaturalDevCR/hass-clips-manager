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
    assert "Cinema Collections Library Manager" in response.text
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


def test_manager_page_only_uses_ingress_safe_relative_action_urls(tmp_path: Path) -> None:
    # Home Assistant Ingress serves this page under a per-install path prefix
    # (e.g. /api/hassio_ingress/<token>/). A client-side fetch() call to an
    # absolute path (leading "/") resolves against the domain root instead of
    # that prefix and never reaches this app. Every action URL the page's
    # script builds must therefore be relative, matching the working
    # stylesheet <link> reference.
    client = TestClient(_app(tmp_path))
    csrf = _manager_session(client)
    assert csrf

    html = client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/session"}).text

    assert "/manager/upload" not in html
    assert "/manager/clips" not in html
    assert "`manager/upload?collection_id=" in html
    assert "`manager/clips/${id}/${action}`" in html


def test_manager_page_supports_multi_file_upload_with_cache_busted_stylesheet(
    tmp_path: Path,
) -> None:
    client = TestClient(_app(tmp_path))
    _manager_session(client)

    html = client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/session"}).text

    assert 'type="file" accept="video/*" multiple required' in html
    assert 'href="static/manager.css?v=' in html
    assert 'id="upload-progress"' in html
    assert 'id="scan-form"' in html
    assert "/manager/scan" not in html
    assert "'manager/scan'" in html
    assert "Collection ID" in html
    assert "not a folder path" in html


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


def test_external_manager_requires_bearer_before_issuing_session(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, mode=WorkerMode.EXTERNAL))

    assert client.get("/", headers={"X-Ingress-Path": "/forged"}).status_code == 401
    assert _manager_session(client, ingress=False)


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
