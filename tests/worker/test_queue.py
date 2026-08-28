# ruff: noqa: E501
import json
from pathlib import Path

import pytest
from cinema_collections_worker.database import Database
from cinema_collections_worker.jobs import CompileRequest, JobService, JobState, JobWorker
from cinema_collections_worker.paths import RootKey, SafePathResolver
from cinema_collections_worker.profile_validation import ProcessingProfile


def _configured_service(tmp_path: Path):
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    resolver = SafePathResolver(
        {
            RootKey.SOURCE: tmp_path / "source",
            RootKey.COMPILED: tmp_path / "compiled",
            RootKey.TEMP: tmp_path / "tmp",
            RootKey.ASSETS: tmp_path / "assets",
        }
    )
    for root in resolver.roots.values():
        root.mkdir(parents=True, exist_ok=True)
    profile = ProcessingProfile().model_dump(mode="json")
    with db.connection:
        db.connection.execute(
            "INSERT INTO profiles(id,name,settings,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("profile", "Profile", json.dumps(profile), "now", "now"),
        )
        db.connection.execute(
            "INSERT INTO collections(id,name,enabled,source_directory,compiled_output_prefix,processing_profile_id,is_default,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("films", "Films", 1, "films", "films", "profile", 0, "now", "now"),
        )
        db.connection.execute(
            "INSERT INTO clips(id,collection_id,state,relative_source_path,relative_output_path,duration_seconds,output_available,metadata,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "00000000-0000-0000-0000-000000000001",
                "films",
                "discovered",
                "films/example.mp4",
                "films/example.mp4",
                10,
                0,
                json.dumps(
                    {
                        "source_fingerprint": "source-v1",
                        "profile_fingerprint": "profile-v1",
                        "size_bytes": 10,
                    }
                ),
                "now",
            ),
        )
    source = resolver.resolve(RootKey.SOURCE.value, "films/example.mp4")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    return db, resolver, JobService(db, resolver, disk_reserve_bytes=0)


def test_enqueue_deduplicates_same_clip_source_and_profile_fingerprint(tmp_path):
    _db, _resolver, service = _configured_service(tmp_path)
    request = CompileRequest(collection_id="films")

    first = service.enqueue_compile(request)
    second = service.enqueue_compile(request)

    assert len(first) == len(second) == 1
    assert first[0].id == second[0].id
    assert first[0].state is JobState.QUEUED


def test_profile_change_creates_new_job_instead_of_deduplicating_old_snapshot(tmp_path):
    db, _resolver, service = _configured_service(tmp_path)
    first = service.enqueue_compile(CompileRequest(collection_id="films", skip_if_processing=False))
    changed = ProcessingProfile(video={"width": 1920, "height": 1080}).model_dump(mode="json")
    with db.connection:
        db.connection.execute(
            "UPDATE profiles SET settings=? WHERE id='profile'", (json.dumps(changed),)
        )

    second = service.enqueue_compile(
        CompileRequest(collection_id="films", skip_if_processing=False)
    )

    assert first[0].id != second[0].id


def test_only_one_worker_can_claim_a_queued_job(tmp_path):
    db, resolver, service = _configured_service(tmp_path)
    service.enqueue_compile(CompileRequest(collection_id="films"))
    worker_a = JobWorker(db, resolver)
    worker_b = JobWorker(db, resolver)

    first = worker_a.claim_next()
    second = worker_b.claim_next()

    assert first is not None
    assert second is None
    assert first.state is JobState.RUNNING


def test_enqueue_rejects_when_available_disk_space_is_below_reserve(tmp_path, monkeypatch):
    _db, _resolver, service = _configured_service(tmp_path)
    monkeypatch.setattr(service, "available_disk_bytes", lambda _path: 9)
    service.disk_reserve_bytes = 10

    with pytest.raises(ValueError, match="disk space"):
        service.enqueue_compile(CompileRequest(collection_id="films"))


def test_enqueue_accounts_for_total_estimated_bytes_across_clips(tmp_path, monkeypatch):
    db, _resolver, service = _configured_service(tmp_path)
    with db.connection:
        db.connection.execute(
            "INSERT INTO clips(id,collection_id,state,relative_source_path,relative_output_path,duration_seconds,output_available,metadata,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "00000000-0000-0000-0000-000000000002",
                "films",
                "discovered",
                "films/two.mp4",
                "films/two.mp4",
                10,
                0,
                json.dumps({"source_fingerprint": "source-v2", "size_bytes": 7}),
                "now",
            ),
        )
    monkeypatch.setattr(service, "available_disk_bytes", lambda _path: 15)
    service.disk_reserve_bytes = 2

    with pytest.raises(ValueError, match="disk space"):
        service.enqueue_compile(CompileRequest(collection_id="films"))

    assert db.connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    states = [row[0] for row in db.connection.execute("SELECT state FROM clips").fetchall()]
    assert states == ["discovered", "discovered"]


def test_compile_strategies_filter_states_and_busy_skip(tmp_path):
    db, _resolver, service = _configured_service(tmp_path)
    first = service.enqueue_compile(CompileRequest(collection_id="films"))[0]
    state = db.connection.execute("SELECT state FROM clips").fetchone()[0]
    assert state == "pending"

    skipped = service.enqueue_compile(
        CompileRequest(collection_id="films", skip_if_processing=True)
    )
    assert [job.id for job in skipped] == [first.id]

    with db.connection:
        db.connection.execute("UPDATE jobs SET state='failed' WHERE id=?", (first.id,))
        db.connection.execute("UPDATE clips SET state='failed'")
        db.connection.execute("UPDATE clips SET state='ready', output_available=0")
    assert (
        service.enqueue_compile(
            CompileRequest(collection_id="films", strategy="compile_stale_only")
        )
        == []
    )
    with db.connection:
        db.connection.execute("UPDATE clips SET state='failed'")
    retried = service.enqueue_compile(
        CompileRequest(collection_id="films", strategy="compile_stale_only")
    )
    assert len(retried) == 1


def test_scan_only_strategy_queues_no_ffmpeg_job(tmp_path):
    db, _resolver, service = _configured_service(tmp_path)

    jobs = service.enqueue_compile(CompileRequest(collection_id="films", strategy="scan_only"))

    assert len(jobs) == 1 and jobs[0].kind == "scan"
    assert (
        db.connection.execute("SELECT count(*) FROM jobs WHERE kind='compile'").fetchone()[0] == 0
    )


def test_asset_changes_materially_change_the_compile_fingerprint(tmp_path):
    db, resolver, service = _configured_service(tmp_path)
    asset = resolver.resolve("assets", "intro.mp4")
    asset.write_bytes(b"intro-v1")
    profile = ProcessingProfile(intro_reference="intro.mp4").model_dump(mode="json")
    with db.connection:
        db.connection.execute("UPDATE profiles SET settings=?", (json.dumps(profile),))

    first = service.enqueue_compile(
        CompileRequest(collection_id="films", skip_if_processing=False)
    )[0]
    with db.connection:
        db.connection.execute("UPDATE jobs SET state='failed' WHERE id=?", (first.id,))
        db.connection.execute("UPDATE clips SET state='failed'")
    asset.write_bytes(b"intro-v2")
    second = service.enqueue_compile(
        CompileRequest(collection_id="films", skip_if_processing=False)
    )[0]

    assert first.profile_fingerprint != second.profile_fingerprint
    assert first.fingerprint != second.fingerprint
