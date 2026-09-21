# ruff: noqa: E501
"""Execution tests for the "edit" job kind: trim/crop the source, in place."""

import json
from pathlib import Path

from cinema_collections_worker.jobs import JobProgress, JobRecord, JobStage, JobState, JobWorker
from cinema_collections_worker.paths import RootKey
from cinema_collections_worker.probe import MediaProbeResult
from cinema_collections_worker.queue import PersistentJobQueue
from test_queue import _configured_service


class _EditingProcess:
    pid = 4242
    returncode = 0

    def __init__(self, output_path: Path, content: bytes = b"edited") -> None:
        self._output_path = output_path
        self._content = content

    def communicate(self, timeout):
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_path.write_bytes(self._content)
        return ("out_time_ms=5000000\nprogress=end\n", "")


class _FailingProcess:
    pid = 4243
    returncode = 1

    def communicate(self, timeout):
        return ("", "ffmpeg: invalid trim range")


class _EditedProbe:
    def probe(self, path: Path) -> MediaProbeResult:
        return MediaProbeResult(
            valid=True, duration_seconds=5.0, width=1080, height=1920, frame_rate=30, has_audio=True
        )


def _enqueue_edit(db, *, trim_start: float = 0.0, trim_end: float = 5.0, crop=None) -> JobRecord:
    job = JobRecord(
        id="10000000-0000-0000-0000-000000000001",
        kind="edit",
        collection_id="films",
        clip_id="00000000-0000-0000-0000-000000000001",
        source_relative_path="films/example.mp4",
        output_relative_path="films/example.mp4",
        source_fingerprint="library-request",
        profile_fingerprint="library-request",
        profile_settings={
            "trim_start_seconds": trim_start,
            "trim_end_seconds": trim_end,
            "crop": crop,
        },
        duration_seconds=10,
        progress=JobProgress(stage=JobStage.QUEUED, percent=0),
    )
    return PersistentJobQueue(db).enqueue(job)


def test_edit_job_trims_and_replaces_the_source_in_place(tmp_path):
    db, resolver, _service = _configured_service(tmp_path)
    job = _enqueue_edit(db)

    def process_factory(command, **_kwargs):
        return _EditingProcess(Path(command[-1]))

    result = JobWorker(
        db, resolver, probe_client=_EditedProbe(), process_factory=process_factory
    ).run_once()

    source = resolver.resolve(RootKey.SOURCE.value, "films/example.mp4")
    assert result is not None and result.job.state is JobState.SUCCEEDED
    assert source.read_bytes() == b"edited"
    assert not (resolver.roots[RootKey.TEMP] / job.id).exists()

    row = db.connection.execute(
        "SELECT state, duration_seconds, metadata FROM clips WHERE id=?", (job.clip_id,)
    ).fetchone()
    assert row["state"] == "discovered"
    assert row["duration_seconds"] == 5.0
    metadata = json.loads(row["metadata"])
    assert metadata["width"] == 1080
    assert metadata["height"] == 1920


def test_edit_job_passes_trim_and_crop_to_ffmpeg(tmp_path):
    db, resolver, _service = _configured_service(tmp_path)
    _enqueue_edit(
        db, trim_start=1.5, trim_end=6.5, crop={"x": 10, "y": 20, "width": 800, "height": 600}
    )
    commands = []

    def process_factory(command, **_kwargs):
        commands.append(command)
        return _EditingProcess(Path(command[-1]))

    JobWorker(db, resolver, probe_client=_EditedProbe(), process_factory=process_factory).run_once()

    command = commands[0]
    assert command[command.index("-ss") + 1] == "1.500"
    assert command[command.index("-to") + 1] == "6.500"
    assert command[command.index("-vf") + 1] == "crop=800:600:10:20"


def test_edit_job_computes_progress_against_the_trim_window_not_the_source_duration(
    tmp_path, monkeypatch
):
    db, resolver, _service = _configured_service(tmp_path)
    _enqueue_edit(db, trim_start=2.0, trim_end=7.0)
    captured: dict[str, float] = {}
    original_run_process = JobWorker._run_process

    def spy(self, job, command, timeout_seconds):
        captured["duration_seconds"] = job.duration_seconds
        return original_run_process(self, job, command, timeout_seconds)

    monkeypatch.setattr(JobWorker, "_run_process", spy)

    def process_factory(command, **_kwargs):
        return _EditingProcess(Path(command[-1]))

    JobWorker(db, resolver, probe_client=_EditedProbe(), process_factory=process_factory).run_once()

    assert captured["duration_seconds"] == 5.0


def test_edit_job_failure_leaves_the_original_source_untouched(tmp_path):
    db, resolver, _service = _configured_service(tmp_path)
    job = _enqueue_edit(db)
    source = resolver.resolve(RootKey.SOURCE.value, "films/example.mp4")
    original_bytes = source.read_bytes()

    result = JobWorker(
        db,
        resolver,
        probe_client=_EditedProbe(),
        process_factory=lambda command, **_kwargs: _FailingProcess(),
    ).run_once()

    assert result is not None and result.job.state is JobState.FAILED
    assert source.read_bytes() == original_bytes
    row = db.connection.execute(
        "SELECT state, metadata FROM clips WHERE id=?", (job.clip_id,)
    ).fetchone()
    assert row["state"] == "failed"
    assert "failed_reason" in json.loads(row["metadata"])
