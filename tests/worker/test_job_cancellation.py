# ruff: noqa: E501
import signal

from cinema_collections_worker.jobs import CompileRequest, JobState, JobWorker
from cinema_collections_worker.probe import MediaProbeResult
from test_queue import _configured_service


class _RunningProcess:
    pid = 4242
    returncode = None

    def communicate(self, timeout):
        raise TimeoutError

    def poll(self):
        return None


class _ValidProbe:
    def probe(self, _path):
        return MediaProbeResult(valid=True, duration_seconds=1)


class _NeverFinishesProcess:
    pid = 5151
    returncode = None


def test_cancelling_running_job_terminates_process_group_and_removes_temp(tmp_path, monkeypatch):
    db, resolver, service = _configured_service(tmp_path)
    job = service.enqueue_compile(CompileRequest(collection_id="films"))[0]
    temp_dir = resolver.roots["temp"] / job.id

    def cancelling_process_factory(*_args, **_kwargs):
        (temp_dir / "partial.mp4").write_bytes(b"partial")
        service.cancel(job.id)
        return _RunningProcess()

    worker = JobWorker(
        db, resolver, probe_client=_ValidProbe(), process_factory=cancelling_process_factory
    )
    killed = []
    monkeypatch.setattr(
        "cinema_collections_worker.jobs.os.killpg", lambda pid, sig: killed.append((pid, sig))
    )

    result = worker.run_once()

    assert result is not None and result.job.state is JobState.CANCELLED
    assert killed == [(4242, signal.SIGTERM)]
    assert not temp_dir.exists()


def test_timeout_terminates_process_and_stops_after_retry_cap(tmp_path, monkeypatch):
    db, resolver, service = _configured_service(tmp_path)
    service.enqueue_compile(CompileRequest(collection_id="films", max_attempts=1))
    monotonic_values = iter((0.0, 301.0))
    monkeypatch.setattr(
        "cinema_collections_worker.jobs.time.monotonic", lambda: next(monotonic_values)
    )
    killed = []
    monkeypatch.setattr(
        "cinema_collections_worker.jobs.os.killpg", lambda pid, sig: killed.append((pid, sig))
    )

    result = JobWorker(
        db,
        resolver,
        probe_client=_ValidProbe(),
        process_factory=lambda *_args, **_kwargs: _NeverFinishesProcess(),
    ).run_once()

    assert result is not None and result.job.state is JobState.FAILED
    assert not result.retry_scheduled
    assert killed == [(5151, signal.SIGTERM)]
