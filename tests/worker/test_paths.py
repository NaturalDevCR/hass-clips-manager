from pathlib import Path

import pytest

from cinema_collections_worker.paths import (
    PathSafetyError,
    RootKey,
    SafePathResolver,
    validate_collection_id,
    validate_filename,
)


def test_resolves_root_relative_path(tmp_path: Path) -> None:
    resolver = SafePathResolver({RootKey.SOURCE: tmp_path})
    assert resolver.resolve("source", "nested/clip.mp4") == tmp_path / "nested/clip.mp4"


@pytest.mark.parametrize("value", ["../outside", "nested/../../outside", "/etc/passwd", ""])
def test_rejects_unsafe_relative_paths(tmp_path: Path, value: str) -> None:
    resolver = SafePathResolver({"source": tmp_path})
    with pytest.raises(PathSafetyError):
        resolver.resolve("source", value)


def test_rejects_control_character_filename() -> None:
    with pytest.raises(PathSafetyError):
        validate_filename("clip\u0000.mp4")


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    resolver = SafePathResolver({"source": tmp_path})
    with pytest.raises(PathSafetyError):
        resolver.resolve("source", "escape/file.mp4")


def test_valid_collection_id_and_filename() -> None:
    assert validate_collection_id("christmas") == "christmas"
    assert validate_filename("clip-01.mp4") == "clip-01.mp4"
