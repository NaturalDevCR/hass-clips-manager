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


def _make_generated_fixture(path: Path, rate: int = 10, seconds: int = 1) -> None:
    """Generate a short color-and-tone clip; no downloaded media is used.

    ``rate`` matters: a source's timebase follows its frame rate, so passing
    different rates produces the mismatched timebases that xfade rejects.
    """
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
            f"testsrc=size=64x64:rate={rate}",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000",
            "-t",
            str(seconds),
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


def test_intro_and_outro_at_a_different_frame_rate_still_compile(tmp_path: Path) -> None:
    """Crossfading segments whose sources differ in frame rate must not fail.

    A source's timebase follows its frame rate, and xfade refuses inputs whose
    timebases differ, so an intro recorded at a different rate than the clip
    aborted the whole compile with "do not match the corresponding second input
    link". The encoder's own -r runs after the filter graph and cannot prevent
    it. Every fixture above shares one rate, so nothing here caught it.
    """
    resolver = SafePathResolver({key: tmp_path / key.value for key in RootKey})
    for root in resolver.roots.values():
        root.mkdir(parents=True)
    database = Database.create(str(tmp_path / "worker.sqlite3"))

    intro = resolver.resolve("assets", "intro.mp4")
    outro = resolver.resolve("assets", "outro.mp4")
    source = resolver.resolve("source", "films/generated.mp4")
    _make_generated_fixture(source, rate=24, seconds=3)
    _make_generated_fixture(intro, rate=25, seconds=2)
    _make_generated_fixture(outro, rate=30, seconds=2)

    profile = ProcessingProfile(
        video={
            "width": 64,
            "height": 64,
            "fps": 24,
            "preset": "ultrafast",
            "scaling": {"strategy": "aspect_fit", "width": 64, "height": 64},
        },
        loudness={"mode": "disabled"},
        transitions=[{"type": "fade", "duration_seconds": 0.5}],
        intro_reference="intro.mp4",
        outro_reference="outro.mp4",
        fade_in_seconds=0,
        fade_out_seconds=0,
    ).model_dump(mode="json")

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
                "00000000-0000-0000-0000-000000000017",
                "films",
                "discovered",
                "films/generated.mp4",
                "films/generated.mp4",
                3,
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

    assert result is not None, "the compile job did not run"
    assert result.job.state is JobState.SUCCEEDED, f"compile failed: {result.job.error}"
    output = resolver.resolve("compiled", "films/generated.mp4")
    assert output.is_file() and output.stat().st_size > 0
