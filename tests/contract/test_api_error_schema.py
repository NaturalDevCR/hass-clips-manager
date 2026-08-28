"""Assertions for the stable API error and compatibility shapes."""

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).parents[2] / "contract" / "openapi-v1.yaml"


def load_schema() -> dict:
    with SCHEMA_PATH.open(encoding="utf-8") as stream:
        return json.load(stream)


def test_error_schema_has_stable_fields_and_types() -> None:
    error = load_schema()["components"]["schemas"]["Error"]
    assert set(error["required"]) == {"code", "message", "retryable", "request_id"}
    assert error["properties"]["code"]["type"] == "string"
    assert error["properties"]["message"]["type"] == "string"
    assert error["properties"]["details"]["nullable"] is True
    assert error["properties"]["retryable"] == {"type": "boolean"}
    assert error["properties"]["request_id"] == {"type": "string", "format": "uuid"}


def test_compatibility_fields_are_present_on_health() -> None:
    schema = load_schema()
    health = schema["components"]["schemas"]["Health"]
    assert {
        "api_version",
        "min_client_version",
        "max_client_version",
    }.issubset(health["required"])
    for name in ("api_version", "min_client_version", "max_client_version"):
        assert health["properties"][name] == {"type": "string"}


def test_secret_fields_are_write_only() -> None:
    schemas = load_schema()["components"]["schemas"]
    secret_fields = [
        schemas["CollectionCreate"]["properties"]["worker_secret"],
        schemas["ProfileCreate"]["properties"]["asset_secret"],
    ]
    assert all(field.get("writeOnly") is True for field in secret_fields)
