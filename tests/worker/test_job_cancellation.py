# ruff: noqa: E501
import signal
import uuid

import pytest
from cinema_collections_worker.catalog import ScanSummary
from cinema_collections_worker.jobs import CompileRequest, JobRecord, JobState, JobWorker
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

    def communicate(self, timeout):
        raise TimeoutError


class _RecordingCatalog:
    def __init__(self) -> None:
        self.requests: list[set[str] | None] = []

    def scan(self, collection_ids: set[str] | None = None) -> ScanSummary:
        self.requests.append(collection_ids)
        return ScanSummary(added=2, modified=1)


def _maintenance_job(kind: str, *, collection_ids: list[str] | None = None) -> JobRecord:
    job_id = str(uuid.uuid4())
    return JobRecord(
        id=job_id,
        kind=kind,
        collection_id="system",
        clip_id=job_id,
        source_relative_path=f"{kind}/{job_id}.request",
        output_relative_path=f"{kind}/{job_id}.result",
        source_fingerprint="request",
        profile_fingerprint="request",
        profile_settings={"collection_ids": collection_ids} if collection_ids else {},
        duration_seconds=0,
    )


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
    assert killed == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
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

    try:
        result = JobWorker(
            db,
            resolver,
            probe_client=_ValidProbe(),
            process_factory=lambda *_args, **_kwargs: _NeverFinishesProcess(),
        ).run_once()
    finally:
        # The Home Assistant test plugin calls time.monotonic during teardown.
        # Restore this module-level patch before its cleanup fixture runs.
        monkeypatch.undo()

    assert result is not None and result.job.state is JobState.FAILED
    assert not result.retry_scheduled
    assert killed == [(5151, signal.SIGTERM), (5151, signal.SIGKILL)]


def test_cleanup_rejects_non_uuid_or_out_of_root_temporary_directory(tmp_path):
    db, resolver, _service = _configured_service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    worker = JobWorker(db, resolver)

    with pytest.raises(ValueError, match="root-contained UUID"):
        worker._cleanup(resolver.roots["temp"] / "../outside")

    assert outside.exists()


def test_scan_job_dispatches_catalog_scan_without_entering_compile_execution(tmp_path) -> None:
    db, resolver, _service = _configured_service(tmp_path)
    catalog = _RecordingCatalog()
    job = _maintenance_job("scan", collection_ids=["films"])
    from cinema_collections_worker.queue import PersistentJobQueue

    PersistentJobQueue(db).enqueue(job)
    worker = JobWorker(
        db,
        resolver,
        catalog=catalog,
        process_factory=lambda *_args, **_kwargs: pytest.fail("scan must not run FFmpeg"),
    )

    result = worker.run_once()

    assert result is not None and result.job.state is JobState.SUCCEEDED
    assert catalog.requests == [{"films"}]
    assert "added=2" in result.job.logs[-1]


def test_cleanup_job_removes_only_tracked_inactive_job_directories(tmp_path) -> None:
    db, resolver, _service = _configured_service(tmp_path)
    tracked_id = str(uuid.uuid4())
    stale = resolver.roots["temp"] / tracked_id
    stale.mkdir()
    (stale / "partial.mp4").write_bytes(b"partial")
    tracked = _maintenance_job("cleanup")
    tracked = tracked.model_copy(update={"id": tracked_id, "clip_id": tracked_id})
    from cinema_collections_worker.queue import PersistentJobQueue

    PersistentJobQueue(db).enqueue(tracked)
    with db.connection:
        db.connection.execute("UPDATE jobs SET state='failed' WHERE id=?", (tracked_id,))
    untracked = resolver.roots["temp"] / str(uuid.uuid4())
    untracked.mkdir()
    preserved = resolver.roots["temp"] / "not-a-job"
    preserved.mkdir()
    job = _maintenance_job("cleanup")
    PersistentJobQueue(db).enqueue(job)
    worker = JobWorker(
        db,
        resolver,
        process_factory=lambda *_args, **_kwargs: pytest.fail("cleanup must not run FFmpeg"),
    )

    result = worker.run_once()

    assert result is not None and result.job.state is JobState.SUCCEEDED
    assert not stale.exists()
    assert untracked.exists()
    assert preserved.exists()


def test_failed_process_output_is_bounded_and_redacts_roots_and_bearer_tokens(tmp_path) -> None:
    db, resolver, service = _configured_service(tmp_path)
    service.enqueue_compile(CompileRequest(collection_id="films", max_attempts=1))

    class FailedProcess:
        pid = 0
        returncode = 1

        def communicate(self, timeout):
            return "", f"{resolver.roots['source']}/films/example.mp4 Bearer leaked-token"

    result = JobWorker(
        db,
        resolver,
        probe_client=_ValidProbe(),
        process_factory=lambda *_args, **_kwargs: FailedProcess(),
    ).run_once()

    assert result is not None and result.job.state is JobState.FAILED
    assert str(resolver.roots["source"]) not in str(result.job.error)
    assert "leaked-token" not in str(result.job.error)
    assert len(str(result.job.error)) <= 1000
