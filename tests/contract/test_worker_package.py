"""Smoke-test the built Worker wheel without repository-level contract files."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_built_worker_wheel_imports_and_generates_openapi_outside_repository(
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        check=True,
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
    )
    wheel = next(dist_dir.glob("cinema_collections-*.whl"))
    site_dir = tmp_path / "site"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--no-deps",
            "--target",
            str(site_dir),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    script = """
from pathlib import Path
from pydantic import SecretStr
from cinema_collections_worker.api import create_app
from cinema_collections_worker.paths import RootKey
from cinema_collections_worker.settings import WorkerSettings

root = Path.cwd() / 'runtime'
settings = WorkerSettings(
    bearer_secret=SecretStr('token'), data_dir=root, database_path=root / 'worker.sqlite3',
    log_dir=root / 'logs', temp_dir=root / 'tmp',
    roots={key: root / key.value for key in RootKey},
)
app = create_app(settings)
assert '/api/v1/health' in app.openapi()['paths']
"""
    environment = {**os.environ, "PYTHONPATH": str(site_dir)}
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
