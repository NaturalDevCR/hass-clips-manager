import json
from pathlib import Path

from cinema_collections_worker.api import create_app
from cinema_collections_worker.paths import RootKey
from cinema_collections_worker.settings import WorkerSettings
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_openapi_schema import EXPECTED_ROUTES

CONTRACT_PATH = Path(__file__).parents[2] / "contract" / "openapi-v1.yaml"


def _reference_name(value: dict) -> str | None:
    reference = value.get("$ref")
    return reference.rsplit("/", maxsplit=1)[-1] if reference else None


def _parameter(schema: dict, value: dict) -> dict:
    name = _reference_name(value)
    return schema["components"]["parameters"][name] if name else value


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


def test_runtime_openapi_preserves_contract_security_bodies_and_responses(tmp_path: Path) -> None:
    settings = WorkerSettings(
        bearer_secret=SecretStr("test-token"),
        data_dir=tmp_path,
        database_path=tmp_path / "worker.sqlite3",
        log_dir=tmp_path / "logs",
        temp_dir=tmp_path / "tmp",
        roots={key: tmp_path / key.value for key in RootKey},
    )
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    runtime = create_app(settings).openapi()

    assert (
        runtime["components"]["securitySchemes"]["bearerAuth"]
        == contract["components"]["securitySchemes"]["bearerAuth"]
    )
    assert set(runtime["paths"]) == set(contract["paths"])
    for path, contract_item in contract["paths"].items():
        for method, contract_operation in contract_item.items():
            if method == "parameters":
                continue
            runtime_operation = runtime["paths"][path][method]
            assert runtime_operation["security"] == contract_operation["security"]
            if method in {"post", "patch"}:
                headers = [
                    _parameter(runtime, parameter)
                    for parameter in runtime_operation.get("parameters", [])
                    if _parameter(runtime, parameter)["in"] == "header"
                ]
                assert any(parameter["name"] == "Idempotency-Key" for parameter in headers)
            if "requestBody" in contract_operation:
                expected = _reference_name(
                    contract_operation["requestBody"]["content"]["application/json"]["schema"]
                )
                actual = _reference_name(
                    runtime_operation["requestBody"]["content"]["application/json"]["schema"]
                )
                assert actual == expected
            for status_code, contract_response in contract_operation["responses"].items():
                if "content" not in contract_response:
                    continue
                expected = _reference_name(
                    contract_response["content"]["application/json"]["schema"]
                )
                actual_response = runtime_operation["responses"].get(status_code, {})
                actual = _reference_name(
                    actual_response.get("content", {}).get("application/json", {}).get("schema", {})
                )
                assert actual == expected

    compile_schema = runtime["components"]["schemas"]["CompileRequest"]
    assert set(compile_schema["properties"]) == {
        "collection_id",
        "clip_ids",
        "strategy",
        "skip_if_processing",
    }
    assert compile_schema["properties"]["strategy"]["enum"] == [
        "scan_and_compile_changed_or_missing",
        "compile_stale_only",
        "scan_only",
    ]
