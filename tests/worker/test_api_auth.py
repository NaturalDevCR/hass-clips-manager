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
        "worker_version": "1.6.1",
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


def test_rejected_request_explains_the_cause_without_leaking_paths_or_token(
    tmp_path: Path,
) -> None:
    """A rejection has to name its cause; "request could not be processed" is a dead end.

    A real compile returned 422 for one collection and 202 for another, and
    neither the response, the container log nor the Home Assistant log said
    which field was at fault, so the failure could not be diagnosed at all from
    outside the container. The detail must never carry an absolute worker path
    or the bearer token.
    """
    client = _client(tmp_path)
    headers = {"Authorization": f"Bearer {_TOKEN}", "Idempotency-Key": "create"}

    response = client.post(
        "/api/v1/collections",
        headers=headers,
        json={
            "id": "films",
            "name": "Films",
            "source_directory": "films",
            "processing_profile_id": "default",
            "priority": "not-a-number",
        },
    )

    body = response.json()
    assert response.status_code == 422
    assert body["code"] == "validation_error"
    # FastAPI reports body-shape errors as its own structured list; a handler
    # that raises reports the redacted string this module builds. Either way the
    # cause has to be named and neither may carry a secret or a worker path.
    detail = str(body["details"])
    assert body["details"], "a rejected request must say why"
    assert "priority" in detail, detail
    assert _TOKEN not in detail
    assert str(tmp_path) not in detail


def test_missing_resource_names_what_was_not_found(tmp_path: Path) -> None:
    """A 404 that does not name the missing id is as opaque as the old 422."""
    client = _client(tmp_path)

    response = client.post(
        "/api/v1/compile",
        headers={"Authorization": f"Bearer {_TOKEN}", "Idempotency-Key": "compile"},
        json={"collection_id": "nope"},
    )

    body = response.json()
    assert response.status_code == 404
    assert "nope" in (body["details"] or "")
    assert _TOKEN not in (body["details"] or "")
