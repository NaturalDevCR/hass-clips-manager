"""Real FFmpeg fixture pipeline coverage in an isolated temporary media tree."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from cinema_collections_worker.database import Database
from cinema_collections_worker.jobs import CompileRequest, JobState, JobWorker
from cinema_collections_worker.paths import RootKey, SafePathResolver
from cinema_collections_worker.profile_validation import ProcessingProfile

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="fixture pipeline requires locally installed FFmpeg and ffprobe",
)


def _make_generated_fixture(path: Path) -> None:
    """Generate a one-second color-and-tone clip; no downloaded media is used."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x64:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_generated_fixture_compiles_to_an_atomic_ready_output_in_temp_media_roots(
    tmp_path: Path,
) -> None:
    """The real pipeline publishes only a validated output from its isolated fixture."""
    resolver = SafePathResolver({key: tmp_path / key.value for key in RootKey})
    for root in resolver.roots.values():
        root.mkdir(parents=True)
    database = Database.create(str(tmp_path / "worker.sqlite3"))
    profile = ProcessingProfile(
        video={
            "width": 64,
            "height": 64,
            "fps": 10,
            "preset": "ultrafast",
            "scaling": {"strategy": "aspect_fit", "width": 64, "height": 64},
        },
        loudness={"mode": "disabled"},
        transitions=[],
        fade_in_seconds=0,
        fade_out_seconds=0,
    ).model_dump(mode="json")
    source = resolver.resolve("source", "films/generated.mp4")
    _make_generated_fixture(source)
    with database.connection:
        database.connection.execute(
            "INSERT INTO profiles(id,name,settings,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("fixture", "Fixture", json.dumps(profile), "now", "now"),
        )
        database.connection.execute(
            "INSERT INTO collections("
            "id,name,enabled,source_directory,compiled_output_prefix,processing_profile_id,"
            "is_default,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            ("films", "Films", 1, "films", "films", "fixture", 0, "now", "now"),
        )
        database.connection.execute(
            "INSERT INTO clips("
            "id,collection_id,state,relative_source_path,relative_output_path,duration_seconds,"
            "output_available,metadata,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "00000000-0000-0000-0000-000000000016",
                "films",
                "discovered",
                "films/generated.mp4",
                "films/generated.mp4",
                1,
                0,
                json.dumps(
                    {"source_fingerprint": "generated-v1", "size_bytes": source.stat().st_size}
                ),
                "now",
            ),
        )

    from cinema_collections_worker.jobs import JobService

    service = JobService(database, resolver, disk_reserve_bytes=0)
    service.enqueue_compile(CompileRequest(collection_id="films"))
    result = JobWorker(database, resolver).run_once()

    output = resolver.resolve("compiled", "films/generated.mp4")
    assert result is not None and result.job.state is JobState.SUCCEEDED
    assert output.is_file() and output.stat().st_size > 0
    assert source.is_file()
    assert not list(output.parent.glob("*.publishing"))
    assert not list(resolver.roots[RootKey.TEMP].iterdir())
