# ruff: noqa: E501
from pathlib import Path

from cinema_collections_worker.jobs import CompileRequest, JobState, JobWorker
from cinema_collections_worker.probe import MediaProbeResult
from test_queue import _configured_service


class _FinishedProcess:
    pid = 31337
    returncode = 0

    def communicate(self, timeout):
        return "out_time_ms=10000000\nprogress=end\n", ""


class _UnparseableProgressProcess(_FinishedProcess):
    def communicate(self, timeout):
        return "transcoder reported an unknown status", ""


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
