# ruff: noqa: E501
"""Per-phase FFmpeg timeouts scale with the content each phase processes."""

import json
from pathlib import Path

from cinema_collections_worker.jobs import CompileRequest, JobState, JobWorker
from cinema_collections_worker.probe import MediaProbeResult
from cinema_collections_worker.profile_validation import ProcessingProfile
from test_queue import _configured_service


class _FinishedProcess:
    pid = 31337
    returncode = 0

    def communicate(self, timeout):
        return (
            'out_time_ms=10000000\nprogress=end\n{"input_i":"-19","input_tp":"-2","input_lra":"8","input_thresh":"-29","target_offset":"1"}',
            "",
        )


class _ValidProbe:
    def probe(self, path: Path):
        return MediaProbeResult(
            valid=True,
            duration_seconds=600 if Path(path).name == "intro.mp4" else 10,
            width=3840,
            height=2160,
            frame_rate=24,
            has_audio=True,
        )


def _process_factory(command, **_kwargs):
    if command[-1] != "-":
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"complete")
    return _FinishedProcess()


def _recorded_timeouts(worker: JobWorker, monkeypatch) -> list[float]:
    recorded: list[float] = []
    original = worker._run_process

    def spy(job, command, timeout_seconds):
        recorded.append(timeout_seconds)
        return original(job, command, timeout_seconds)

    monkeypatch.setattr(worker, "_run_process", spy)
    return recorded


def test_short_clip_keeps_the_fixed_floor_timeout_for_every_phase(tmp_path, monkeypatch):
    db, resolver, service = _configured_service(tmp_path)
    service.enqueue_compile(CompileRequest(collection_id="films"))
    worker = JobWorker(db, resolver, probe_client=_ValidProbe(), process_factory=_process_factory)
    timeouts = _recorded_timeouts(worker, monkeypatch)

    result = worker.run_once()

    assert result is not None and result.job.state is JobState.SUCCEEDED
    assert timeouts and all(timeout == 300 for timeout in timeouts)


def test_long_clip_scales_every_phase_allowance_beyond_the_floor(tmp_path, monkeypatch):
    """A ~12-minute clip (the thriller-mj-edit.mp4 failure) must get well over 300 s."""
    db, resolver, service = _configured_service(tmp_path)
    with db.connection:
        db.connection.execute("UPDATE clips SET duration_seconds=718")
    service.enqueue_compile(CompileRequest(collection_id="films"))
    worker = JobWorker(db, resolver, probe_client=_ValidProbe(), process_factory=_process_factory)
    timeouts = _recorded_timeouts(worker, monkeypatch)

    result = worker.run_once()

    assert result is not None and result.job.state is JobState.SUCCEEDED
    # ceil(718 / 60) = 12 minutes at the default 120 s per minute.
    assert timeouts and all(timeout == 1440 for timeout in timeouts)


def test_segment_analysis_uses_the_segments_own_duration(tmp_path, monkeypatch):
    db, resolver, service = _configured_service(tmp_path)
    profile = ProcessingProfile(intro_reference="intro.mp4").model_dump(mode="json")
    with db.connection:
        db.connection.execute("UPDATE profiles SET settings=?", (json.dumps(profile),))
    resolver.roots["assets"].mkdir(parents=True, exist_ok=True)
    (resolver.roots["assets"] / "intro.mp4").write_bytes(b"intro")
    service.enqueue_compile(CompileRequest(collection_id="films"))
    worker = JobWorker(db, resolver, probe_client=_ValidProbe(), process_factory=_process_factory)
    timeouts = _recorded_timeouts(worker, monkeypatch)

    result = worker.run_once()

    assert result is not None and result.job.state is JobState.SUCCEEDED
    # clip analysis (10 s -> floor), intro analysis (600 s -> 10 min * 120),
    # then composition, final-mix analysis and final normalization on the
    # composed 609 s total (10 + 600 - 1 transition -> ceil = 11 min * 120).
    assert timeouts == [300, 1200, 1320, 1320, 1320]
