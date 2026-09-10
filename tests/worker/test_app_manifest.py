from __future__ import annotations

from pathlib import Path

import yaml
from cinema_collections_worker.api import WORKER_VERSION

ROOT = Path(__file__).parents[2]
APP = ROOT / "app"


def test_app_manifest_uses_private_ingress_and_only_required_mounts() -> None:
    config = yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))

    assert config["ingress"] is True
    assert config["ingress_port"] == 8099
    assert config["ports"] == {}
    assert config["map"] == ["data:rw", "addon_config:rw", "media:rw"]
    assert config["options"]["source_root"].startswith("/media/")
    assert config["options"]["compiled_root"].startswith("/media/")
    assert not (APP / "build.yaml").exists()


def test_app_dockerfile_is_multi_arch_pinned_and_runs_the_ingress_worker() -> None:
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (APP / "rootfs/usr/bin/cinema-collections-worker").read_text(encoding="utf-8")

    assert "BUILD_ARCH" in dockerfile
    assert "home-assistant" in dockerfile and ":3." in dockerfile
    assert "ffmpeg" in dockerfile and "python3" in dockerfile
    assert "USER abc" not in dockerfile
    assert "/data/options.json" in dockerfile
    assert "cinema_collections_worker.main" in entrypoint
    assert (APP / "DOCS.md").exists()


def test_app_image_builds_the_library_manager_interface() -> None:
    # The interface is a Vue single-page application. Node belongs to a build
    # stage only, so the published image carries the bundle and no toolchain.
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM node:" in dockerfile
    assert "AS ui" in dockerfile
    assert "npm ci" in dockerfile
    assert "npm run build" in dockerfile
    assert (
        "COPY --from=ui /ui/dist "
        "/opt/cinema-collections-worker/src/cinema_collections_worker/static/ui"
    ) in dockerfile
    assert (APP / "ui/package.json").is_file()
    assert (APP / "ui/src/main.ts").is_file()


def test_app_version_matches_the_version_the_worker_reports() -> None:
    # The App version and WORKER_VERSION are separate declarations of one fact.
    # Nothing else compares them, so a bump that misses one would ship a Worker
    # whose /api/v1/health disagrees with the App the Supervisor installed.
    config = yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))

    assert str(config["version"]) == WORKER_VERSION


def test_app_dockerfile_copies_only_paths_inside_the_addon_build_context() -> None:
    # The Supervisor builds a local add-on from the add-on folder itself:
    # `docker buildx build . --file Dockerfile` with that folder as the working
    # directory. A COPY source outside it resolves to nothing and fails the
    # install with "not found", however well the same Dockerfile builds from
    # the repository root in CI.
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")

    missing: list[str] = []
    for line in dockerfile.splitlines():
        if not line.startswith("COPY "):
            continue
        arguments = line.split()[1:]
        if arguments[0].startswith("--from="):
            continue
        missing.extend(source for source in arguments[:-1] if not (APP / source).exists())

    assert missing == []


def test_app_build_context_excludes_host_ui_artifacts() -> None:
    # The UI stage runs `npm ci` and then copies the source over it. Without
    # this exclusion a developer's host node_modules would land on top of the
    # container's, replacing Linux binaries with the host platform's.
    ignored = (APP / ".dockerignore").read_text(encoding="utf-8").split()

    assert "ui/node_modules" in ignored
    assert "ui/dist" in ignored
