"""Repository metadata contract tests."""

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_hacs_metadata_names_cinema_collections() -> None:
    metadata = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert metadata["name"] == "Cinema Collections"


def test_repository_metadata_identifies_an_app_repository() -> None:
    metadata = (ROOT / "repository.yaml").read_text(encoding="utf-8")
    assert "name: Cinema Collections" in metadata
    assert "type: app" in metadata


def test_app_build_manifest_is_not_present() -> None:
    assert not (ROOT / "app" / "build.yaml").exists()
