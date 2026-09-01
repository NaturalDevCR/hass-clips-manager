"""Contract-level checks for the versioned Worker API document."""

import json
from pathlib import Path

from openapi_spec_validator import validate

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
    ("POST", "/api/v1/jobs/cancel-all"),
    ("GET", "/api/v1/logs"),
    ("GET", "/api/v1/assets"),
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


def assert_valid_openapi_structure(schema: dict) -> None:
    """Perform dependency-free structural validation of this OpenAPI 3 document."""
    assert schema["openapi"].startswith("3.")
    assert isinstance(schema["info"]["title"], str) and schema["info"]["title"]
    assert isinstance(schema["info"]["version"], str) and schema["info"]["version"]
    assert schema["servers"] and all(isinstance(server["url"], str) for server in schema["servers"])
    assert schema["paths"]
    components = schema["components"]
    assert components["securitySchemes"] and components["schemas"]

    valid_methods = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
    operation_ids: set[str] = set()
    for path, path_item in schema["paths"].items():
        assert path.startswith("/"), path
        assert isinstance(path_item, dict)
        for method, operation in path_item.items():
            if method == "parameters":
                continue
            assert method in valid_methods, (path, method)
            assert isinstance(operation.get("operationId"), str)
            assert operation["operationId"] not in operation_ids
            operation_ids.add(operation["operationId"])
            assert operation.get("responses")
            for status, response in operation["responses"].items():
                assert status.isdigit() or status.startswith("x-"), (path, status)
                assert "$ref" in response or response.get("description"), (path, status)
            for parameter in path_item.get("parameters", []) + operation.get("parameters", []):
                resolved = resolve_parameter(schema, parameter)
                assert resolved["in"] in {"path", "query", "header", "cookie"}
                assert isinstance(resolved["name"], str)
                if resolved["in"] == "path":
                    assert resolved["required"] is True
                    assert "{" + resolved["name"] + "}" in path

    def check_refs(value: object) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                assert ref.startswith("#/components/")
                section, name = ref.split("/")[2:]
                assert name in components[section], ref
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)

    check_refs(schema)


def test_openapi_document_has_versioned_routes_and_bearer_auth() -> None:
    schema = load_schema()

    validate(schema)
    assert_valid_openapi_structure(schema)
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
    mutations = {(method, path) for method, path in EXPECTED_ROUTES if method in {"POST", "PATCH"}}
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


def test_list_routes_return_typed_paginated_items() -> None:
    schema = load_schema()
    expected = {
        "/api/v1/collections": "CollectionsPage",
        "/api/v1/profiles": "ProfilesPage",
        "/api/v1/clips": "ClipsPage",
        "/api/v1/jobs": "JobsPage",
        "/api/v1/logs": "LogsPage",
    }
    for path, schema_name in expected.items():
        response_schema = schema["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema == {"$ref": f"#/components/schemas/{schema_name}"}
        page_schema = schema["components"]["schemas"][schema_name]
        item_schema = page_schema["properties"]["items"]["items"]
        assert item_schema["$ref"].startswith("#/components/schemas/")
