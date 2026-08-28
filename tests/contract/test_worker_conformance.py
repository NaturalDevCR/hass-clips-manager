from pathlib import Path

from cinema_collections_worker.api import create_app
from cinema_collections_worker.paths import RootKey
from cinema_collections_worker.settings import WorkerSettings
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_openapi_schema import EXPECTED_ROUTES


def test_fastapi_routes_conform_to_declared_worker_contract(tmp_path: Path) -> None:
    settings = WorkerSettings(
        bearer_secret=SecretStr("test-token"),
        data_dir=tmp_path,
        database_path=tmp_path / "worker.sqlite3",
        log_dir=tmp_path / "logs",
        temp_dir=tmp_path / "tmp",
        roots={key: tmp_path / key.value for key in RootKey},
    )
    app = create_app(settings)
    client = TestClient(app)

    actual = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
        if method in {"GET", "POST", "PATCH"} and route.path.startswith("/api/v1/")
    }
    assert actual == EXPECTED_ROUTES

    health = client.get("/api/v1/health", headers={"Authorization": "Bearer test-token"})
    assert health.status_code == 200
    status = client.get("/api/v1/status", headers={"Authorization": "Bearer test-token"})
    assert status.status_code == 200
    assert {"queue_depth", "current_job", "storage", "scans", "latest_errors"} <= set(status.json())
