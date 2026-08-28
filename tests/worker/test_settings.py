from pathlib import Path

import pytest

from cinema_collections_worker.paths import RootKey
from cinema_collections_worker.settings import HardwareAcceleration, Settings, WorkerMode


def test_load_defaults_and_persistent_paths(tmp_path: Path) -> None:
    options = tmp_path / "options.yaml"
    options.write_text("bearer_secret: test-secret\n", encoding="utf-8")
    settings = Settings.load(options)
    assert settings.mode is WorkerMode.APP
    assert settings.bind_host == "127.0.0.1"
    assert settings.hardware_acceleration is HardwareAcceleration.NONE
    assert settings.database_path == Path("/data/worker.sqlite3")
    assert settings.log_dir == Path("/data/logs")
    assert settings.temp_dir == Path("/data/tmp")
    assert RootKey.TEMP in settings.roots


def test_bearer_secret_is_required(tmp_path: Path) -> None:
    options = tmp_path / "options.yaml"
    options.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Settings.load(options)


def test_extra_options_are_rejected(tmp_path: Path) -> None:
    options = tmp_path / "options.yaml"
    options.write_text("bearer_secret: secret\nunknown: value\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Settings.load(options)
