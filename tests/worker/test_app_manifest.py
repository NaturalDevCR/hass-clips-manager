from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
APP = ROOT / "app"


def test_app_manifest_uses_private_ingress_and_only_required_mounts() -> None:
    config = yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))

    assert config["ingress"] is True
    assert config["ingress_port"] == 8099
    assert config["ports"] == {}
    assert config["map"] == ["data:rw", "addon_config:rw", "media:rw"]
    assert not (APP / "build.yaml").exists()


def test_app_dockerfile_is_multi_arch_pinned_and_runs_the_ingress_worker() -> None:
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (APP / "rootfs/usr/bin/cinema-collections-worker").read_text(encoding="utf-8")

    assert "BUILD_ARCH" in dockerfile
    assert "home-assistant" in dockerfile and ":3." in dockerfile
    assert "ffmpeg" in dockerfile and "python3" in dockerfile
    assert "USER abc" in dockerfile
    assert "cinema_collections_worker.main" in entrypoint
    assert (APP / "DOCS.md").exists()
    assert (APP / "src/cinema_collections_worker/templates/manager.html").exists()
    assert (APP / "src/cinema_collections_worker/static/manager.css").exists()
