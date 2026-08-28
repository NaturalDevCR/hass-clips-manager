from pathlib import Path

from cinema_collections_worker.api import create_app
from cinema_collections_worker.paths import RootKey
from cinema_collections_worker.settings import WorkerSettings
from fastapi.testclient import TestClient
from pydantic import SecretStr

_TOKEN = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"


def _client(tmp_path: Path) -> TestClient:
    settings = WorkerSettings(
        bearer_secret=SecretStr(_TOKEN),
        data_dir=tmp_path,
        database_path=tmp_path / "worker.sqlite3",
        log_dir=tmp_path / "logs",
        temp_dir=tmp_path / "tmp",
        roots={
            RootKey.SOURCE: tmp_path / "source",
            RootKey.COMPILED: tmp_path / "compiled",
            RootKey.TEMP: tmp_path / "tmp",
            RootKey.ASSETS: tmp_path / "assets",
        },
    )
    return TestClient(create_app(settings))


def test_api_rejects_missing_or_wrong_bearer_token(tmp_path: Path) -> None:
    client = _client(tmp_path)

    missing = client.get("/api/v1/health")
    wrong = client.get("/api/v1/health", headers={"Authorization": "Bearer wrong"})

    for response in (missing, wrong):
        assert response.status_code == 401
        body = response.json()
        assert body["code"] == "unauthorized"
        assert body["retryable"] is False
        assert body["request_id"]


def test_health_reports_compatible_worker_versions_and_request_id(tmp_path: Path) -> None:
    response = _client(tmp_path).get(
        "/api/v1/health", headers={"Authorization": f"Bearer {_TOKEN}"}
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.json() == {
        "status": "ok",
        "component": "cinema-collections-worker",
        "worker_version": "1.0.0",
        "api_version": "1.0.0",
        "min_client_version": "1.0.0",
        "max_client_version": "1.x",
    }


def test_mutation_requires_nonempty_idempotency_key_and_supported_client(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = {"Authorization": f"Bearer {_TOKEN}"}
    body = {
        "id": "films",
        "name": "Films",
        "source_directory": "films",
        "processing_profile_id": "default",
    }

    missing = client.post("/api/v1/collections", headers=headers, json=body)
    unsupported = client.post(
        "/api/v1/collections",
        headers={**headers, "Idempotency-Key": "create", "X-Client-Version": "2.0.0"},
        json=body,
    )

    assert missing.status_code == 422
    assert missing.json()["code"] == "validation_error"
    assert unsupported.status_code == 422
    assert unsupported.json()["code"] == "unsupported_client_version"
