"""Diagnostics and localization coverage for Cinema Collections."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from custom_components.cinema_collections.const import DOMAIN
from custom_components.cinema_collections.coordinator import CoordinatorSnapshot
from custom_components.cinema_collections.diagnostics import async_get_config_entry_diagnostics
from custom_components.cinema_collections.models import WorkerError, WorkerHealth, WorkerStatus
from custom_components.cinema_collections.options_flow import OVERRIDE_MODE_SELECTOR
from custom_components.cinema_collections.subentries import (
    _collection_schema,
    _profile_audio_schema,
    _profile_output_schema,
    _profile_timing_schema,
    _profile_video_schema,
)


@pytest.mark.asyncio
async def test_diagnostics_redacts_credentials_and_absolute_media_paths() -> None:
    """Diagnostics keep useful compatibility state without sensitive media details."""
    token = "diagnostics-token-must-not-leak"
    source_path = "/media/cinema-collections/source/holiday/secret.mp4"
    compiled_path = "/media/cinema-collections/compiled/holiday/secret.mp4"
    unknown_path = "/private/media/unshared.mp4"
    unconfigured_token = "unconfigured-secret-must-not-leak"
    entry = SimpleNamespace(
        entry_id="entry-id",
        title="Cinema Collections Worker",
        data={"endpoint": "http://worker.local:8099", "token": token},
        options={"Authorization": f"Bearer {token}"},
        subentries={},
    )
    status = WorkerStatus(
        queue_depth=1,
        current_job={
            "input": source_path,
            "output": compiled_path,
            "progress": {"percent": 50},
            "auth_header": f"Bearer {unconfigured_token}",
            "api_key": unconfigured_token,
            "note": f"Authorization: Bearer {unconfigured_token}",
            "serialized_header": f'"Authorization": "Bearer {unconfigured_token}"',
        },
        storage={
            "source_root": "/media/cinema-collections/source",
            "compiled_root": "/media/cinema-collections/compiled",
        },
        scans={},
        latest_errors=(
            WorkerError(
                code="decode_failed",
                message=f"Could not open {source_path} or {unknown_path}",
                retryable=False,
                request_id="request-1",
            ),
        ),
    )
    snapshot = CoordinatorSnapshot(
        active_collection_id="holiday",
        reason="default",
        override_rejected=False,
        status=status,
        health=WorkerHealth(
            status="ok",
            component="cinema-collections-worker",
            worker_version="1.0.0",
            api_version="1.0.0",
            min_client_version="1.0.0",
            max_client_version="1.x",
        ),
        available=True,
        error=None,
    )
    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: SimpleNamespace(coordinator=SimpleNamespace(data=snapshot))}}
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    rendered = json.dumps(diagnostics)
    assert token not in rendered
    assert "Authorization" not in rendered
    assert "auth_header" not in rendered
    assert "api_key" not in rendered
    assert unconfigured_token not in rendered
    assert source_path not in rendered
    assert compiled_path not in rendered
    assert unknown_path not in rendered
    assert diagnostics["worker"]["compatibility"] == {
        "worker_version": "1.0.0",
        "api_version": "1.0.0",
        "min_client_version": "1.0.0",
        "max_client_version": "1.x",
    }
    assert diagnostics["worker"]["current_job"] == {
        "input": "[source_root]/holiday/secret.mp4",
        "output": "[compiled_root]/holiday/secret.mp4",
        "progress": {"percent": 50},
        "note": "[redacted credential]",
        "serialized_header": "[redacted credential]",
    }


def _leaf_paths(payload: dict[str, Any], prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """Return each translation leaf path to ensure locale coverage is exact."""
    paths: set[tuple[str, ...]] = set()
    for key, value in payload.items():
        path = (*prefix, key)
        if isinstance(value, dict):
            paths.update(_leaf_paths(value, path))
        else:
            paths.add(path)
    return paths


def test_translation_files_are_complete_and_cover_the_integration_surface() -> None:
    """English source translations and Spanish counterparts expose every UI surface."""
    translations = Path(__file__).parents[2] / "custom_components/cinema_collections/translations"
    with (translations / "en.json").open(encoding="utf-8") as file:
        english = json.load(file)
    with (translations / "es.json").open(encoding="utf-8") as file:
        spanish = json.load(file)

    assert _leaf_paths(english) == _leaf_paths(spanish)
    assert set(english) >= {"config", "options", "entity", "services", "selector"}
    assert set(english["config"]) >= {"step", "error", "abort"}
    assert set(english["options"]) >= {"step", "error"}
    assert set(english["config_subentries"]["collection"]["error"]) >= {
        "worker_validation",
        "invalid_collection",
    }
    assert set(english["config_subentries"]["profile"]["error"]) >= {
        "worker_validation",
        "invalid_profile",
    }
    for subentry_type, schema in {
        "collection": _collection_schema(None),
    }.items():
        fields = {str(getattr(field, "schema", field)) for field in schema.schema}
        for step in ("user", "reconfigure"):
            assert set(english["config_subentries"][subentry_type]["step"][step]["data"]) == fields
    profile_steps = {
        "video": _profile_video_schema(None),
        "audio": _profile_audio_schema(None),
        "timing": _profile_timing_schema(None),
        "output": _profile_output_schema(None),
    }
    for step_id, schema in profile_steps.items():
        fields = {str(getattr(field, "schema", field)) for field in schema.schema}
        step = english["config_subentries"]["profile"]["step"][step_id]
        assert set(step["data"]) == fields
        assert step["title"]
        assert step["description"]
    assert set(english["entity"]) >= {"sensor", "button", "select"}
    assert "collection_override" in english["entity"]["select"]
    assert set(english["entity"]["select"]["collection_override"]["state"]) == {
        "automatic",
        "default",
    }
    assert set(english["services"]) >= {
        "select_next_clip",
        "reset_history",
        "scan_library",
        "compile_collection",
        "compile_all",
        "retry_failed",
        "cancel_processing",
        "set_collection_override",
    }
    assert OVERRIDE_MODE_SELECTOR.config["translation_key"] == "override_mode"
    assert set(english["selector"]["override_mode"]["options"]) == set(
        OVERRIDE_MODE_SELECTOR.config["options"]
    )

    with (Path(__file__).parents[2] / "custom_components/cinema_collections/services.yaml").open(
        encoding="utf-8"
    ) as file:
        services = yaml.safe_load(file)
    for service in ("compile_collection", "compile_all"):
        assert (
            services[service]["fields"]["strategy"]["selector"]["select"]["translation_key"]
            == "compile_strategy"
        )
        assert set(services[service]["fields"]["strategy"]["selector"]["select"]["options"]) == set(
            english["selector"]["compile_strategy"]["options"]
        )
    assert (
        services["set_collection_override"]["fields"]["mode"]["selector"]["select"][
            "translation_key"
        ]
        == "override_mode"
    )
    assert set(
        services["set_collection_override"]["fields"]["mode"]["selector"]["select"]["options"]
    ) == set(english["selector"]["override_mode"]["options"])
