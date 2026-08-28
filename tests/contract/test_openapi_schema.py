"""Contract-level checks for the versioned Worker API document."""

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).parents[2] / "contract" / "openapi-v1.yaml"

EXPECTED_ROUTES = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/status"),
    ("GET", "/api/v1/collections"),
    ("POST", "/api/v1/collections"),
    ("PATCH", "/api/v1/collections/{collection_id}"),
    ("GET", "/api/v1/profiles"),
    ("POST", "/api/v1/profiles"),
    ("PATCH", "/api/v1/profiles/{profile_id}"),
    ("GET", "/api/v1/clips"),
    ("GET", "/api/v1/clips/{clip_id}"),
    ("POST", "/api/v1/scan"),
    ("POST", "/api/v1/compile"),
    ("GET", "/api/v1/jobs"),
    ("GET", "/api/v1/jobs/{job_id}"),
    ("POST", "/api/v1/jobs/{job_id}/cancel"),
    ("GET", "/api/v1/logs"),
    ("POST", "/api/v1/cleanup-temporaries"),
}


def load_schema() -> dict:
    with SCHEMA_PATH.open(encoding="utf-8") as stream:
        return json.load(stream)


def resolve_parameter(schema: dict, parameter: dict) -> dict:
    if "$ref" not in parameter:
        return parameter
    name = parameter["$ref"].rsplit("/", maxsplit=1)[-1]
    return schema["components"]["parameters"][name]


def test_openapi_document_has_versioned_routes_and_bearer_auth() -> None:
    schema = load_schema()

    assert schema["openapi"].startswith("3.")
    assert schema["info"]["version"] == "1.0.0"
    assert set(schema["paths"]) == {path for _, path in EXPECTED_ROUTES}
    assert schema["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "WorkerToken",
    }

    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method.lower() in {"parameters", "summary", "description"}:
                continue
            assert operation["security"] == [{"bearerAuth": []}], path


def test_mutations_require_idempotency_key_and_standard_errors() -> None:
    schema = load_schema()
    mutations = {
        (method, path)
        for method, path in EXPECTED_ROUTES
        if method in {"POST", "PATCH"}
    }
    for method, path in mutations:
        operation = schema["paths"][path][method.lower()]
        parameters = operation["parameters"]
        assert any(
            resolve_parameter(schema, parameter)["name"] == "Idempotency-Key"
            and resolve_parameter(schema, parameter)["in"] == "header"
            and resolve_parameter(schema, parameter)["required"] is True
            for parameter in parameters
        ), (method, path)
        assert {"401", "409", "422", "429", "503"}.issubset(operation["responses"]), (
            method,
            path,
        )


def test_every_approved_route_is_declared() -> None:
    schema = load_schema()
    declared = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method.lower() in {"get", "post", "patch"}
    }
    assert declared == EXPECTED_ROUTES
