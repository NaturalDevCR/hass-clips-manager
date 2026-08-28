# ruff: noqa: E501
import json
from pathlib import Path

from cinema_collections_worker.jobs import CompileRequest, JobState, JobWorker
from cinema_collections_worker.probe import MediaProbeResult
from test_queue import _configured_service


class _FinishedProcess:
    pid = 31337
    returncode = 0

    def communicate(self, timeout):
        return (
            'out_time_ms=10000000\nprogress=end\n{"input_i":"-19","input_tp":"-2","input_lra":"8","input_thresh":"-29","target_offset":"1"}',
            "",
        )


class _UnparseableProgressProcess(_FinishedProcess):
    def communicate(self, timeout):
        return (
            'transcoder reported an unknown status\n{"input_i":"-19","input_tp":"-2","input_lra":"8","input_thresh":"-29","target_offset":"1"}',
            "",
        )


class _ValidProbe:
    def probe(self, path: Path):
        return MediaProbeResult(
            valid=path.exists(),
            duration_seconds=10,
            width=3840,
            height=2160,
            frame_rate=24,
            has_audio=True,
        )


def test_successful_job_publishes_only_validated_temp_file_atomically(tmp_path):
    db, resolver, service = _configured_service(tmp_path)
    job = service.enqueue_compile(CompileRequest(collection_id="films"))[0]

    def process_factory(command, **_kwargs):
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"complete")
        return _FinishedProcess()

    result = JobWorker(
        db, resolver, probe_client=_ValidProbe(), process_factory=process_factory
    ).run_once()
    final = resolver.resolve("compiled", "films/example.mp4")

    assert result is not None and result.job.state is JobState.SUCCEEDED
    assert final.read_bytes() == b"complete"
    assert not (resolver.roots["temp"] / job.id).exists()


def test_two_pass_loudness_runs_analysis_then_feeds_measured_values_to_encode(tmp_path):
    db, resolver, service = _configured_service(tmp_path)
    service.enqueue_compile(CompileRequest(collection_id="films"))
    commands = []

    def process_factory(command, **_kwargs):
        commands.append(command)
        if command[-1] != "-":
            Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(command[-1]).write_bytes(b"complete")
        return _FinishedProcess()

    result = JobWorker(
        db, resolver, probe_client=_ValidProbe(), process_factory=process_factory
    ).run_once()

    assert result is not None and result.job.state is JobState.SUCCEEDED
    assert commands[0][commands[0].index("-i") + 1].endswith("example.mp4")
    assert commands[0][commands[0].index("-af") + 1].endswith("print_format=json")
    assert commands[2][commands[2].index("-i") + 1].endswith("composed.mp4")
    assert commands[2][commands[2].index("-af") + 1].endswith("print_format=json")
    assert commands[3][commands[3].index("-i") + 1].endswith("composed.mp4")
    final_filter = commands[3][commands[3].index("-af") + 1]
    assert "measured_I=-19" in final_filter and "offset=1" in final_filter


def test_completed_job_skips_disk_admission_when_output_is_still_available(tmp_path, monkeypatch):
    db, resolver, service = _configured_service(tmp_path)
    service.enqueue_compile(CompileRequest(collection_id="films"))

    def process_factory(command, **_kwargs):
        if command[-1] != "-":
            Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(command[-1]).write_bytes(b"complete")
        return _FinishedProcess()

    assert JobWorker(
        db, resolver, probe_client=_ValidProbe(), process_factory=process_factory
    ).run_once()
    monkeypatch.setattr(service, "available_disk_bytes", lambda _path: 0)
    assert service.enqueue_compile(CompileRequest(collection_id="films")) == []


def test_unparseable_progress_is_logged_without_failing_successful_output(tmp_path):
    db, resolver, service = _configured_service(tmp_path)
    service.enqueue_compile(CompileRequest(collection_id="films"))

    def process_factory(command, **_kwargs):
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"complete")
        return _UnparseableProgressProcess()

    result = JobWorker(
        db, resolver, probe_client=_ValidProbe(), process_factory=process_factory
    ).run_once()

    assert result is not None and result.job.state is JobState.SUCCEEDED
    assert result.job.logs == ["ffmpeg progress was unavailable"]


def test_success_persists_compiled_fingerprints_and_rejects_wrong_geometry(tmp_path):
    db, resolver, service = _configured_service(tmp_path)
    job = service.enqueue_compile(CompileRequest(collection_id="films"))[0]

    def process_factory(command, **_kwargs):
        if command[-1] != "-":
            Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(command[-1]).write_bytes(b"complete")
        return _FinishedProcess()

    class WrongProbe(_ValidProbe):
        def probe(self, path: Path):
            result = super().probe(path)
            if path.name.startswith("output"):
                return MediaProbeResult(
                    valid=True,
                    duration_seconds=10,
                    width=1920,
                    height=1080,
                    frame_rate=24,
                    has_audio=True,
                )
            return result

    failed = JobWorker(
        db, resolver, probe_client=WrongProbe(), process_factory=process_factory
    ).run_once()
    assert failed is not None and failed.job.state is JobState.QUEUED

    with db.connection:
        db.connection.execute("UPDATE jobs SET max_attempts=1 WHERE id=?", (job.id,))
    # Put a fresh eligible job through a valid output probe.
    with db.connection:
        db.connection.execute("UPDATE jobs SET state='failed' WHERE id=?", (job.id,))
        db.connection.execute("UPDATE clips SET state='failed'")
    succeeded_job = service.enqueue_compile(
        CompileRequest(collection_id="films", max_attempts=1, skip_if_processing=False)
    )[0]
    succeeded = JobWorker(
        db, resolver, probe_client=_ValidProbe(), process_factory=process_factory
    ).run_once()
    assert succeeded is not None and succeeded.job.id == succeeded_job.id
    metadata = json.loads(db.connection.execute("SELECT metadata FROM clips").fetchone()[0])
    assert metadata["source_fingerprint"] == succeeded_job.source_fingerprint
    assert metadata["profile_fingerprint"] == succeeded_job.profile_fingerprint
    assert metadata["output_fingerprint"]
