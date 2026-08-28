# ruff: noqa: E501
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
        return MediaProbeResult(valid=path.exists(), duration_seconds=10)


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
    assert commands[1][commands[1].index("-i") + 1].endswith("composed.mp4")
    assert commands[1][commands[1].index("-af") + 1].endswith("print_format=json")
    assert commands[2][commands[2].index("-i") + 1].endswith("composed.mp4")
    final_filter = commands[2][commands[2].index("-af") + 1]
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
