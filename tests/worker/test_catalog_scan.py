from cinema_collections_worker.catalog import CatalogService
from cinema_collections_worker.database import Database
from cinema_collections_worker.domain import CollectionCreate, ProfileCreate
from cinema_collections_worker.models import ClipState
from cinema_collections_worker.paths import RootKey, SafePathResolver
from cinema_collections_worker.probe import MediaProbeResult
from cinema_collections_worker.repositories import CollectionRepository


class FakeProbe:
    def probe(self, path):
        if path.name.startswith("bad"):
            return MediaProbeResult(valid=False, error="malformed media")
        return MediaProbeResult(
            valid=True,
            duration_seconds=3,
            width=100,
            height=50,
            frame_rate=24,
            has_audio=path.name != "silent.mp4",
            size_bytes=path.stat().st_size,
        )


def setup(tmp_path):
    db = Database.create(":memory:")
    from cinema_collections_worker.repositories import ProfileRepository

    ProfileRepository(db).create(
        ProfileCreate(id="default", name="Default", settings={"quality": "source"}),
        actor="t",
        request_id="profile-1",
    )
    CollectionRepository(db).create(
        CollectionCreate(
            id="films", name="Films", source_directory="films", processing_profile_id="default"
        ),
        actor="t",
        request_id="1",
    )
    source = tmp_path / "source" / "films"
    source.mkdir(parents=True)
    resolver = SafePathResolver(
        {RootKey.SOURCE: tmp_path / "source", RootKey.COMPILED: tmp_path / "compiled"}
    )
    resolver.roots[RootKey.COMPILED].mkdir()
    return db, source, CatalogService(db, resolver, FakeProbe())


def test_scan_adds_invalid_no_audio_and_duplicate_name(tmp_path):
    db, source, service = setup(tmp_path)
    for name in ("one.mp4", "silent.mp4", "bad.mp4", "sub"):
        p = source / name
        if name == "sub":
            p.mkdir()
        else:
            p.write_bytes(name.encode())
    summary = service.scan()
    assert summary.added == 3 and summary.invalid == 1
    rows = db.connection.execute(
        "select state, metadata from clips order by relative_source_path"
    ).fetchall()
    assert any(r[0] == ClipState.INVALID for r in rows)
    assert any("has_audio" in r[1] for r in rows)


def test_scan_change_stales_and_missing_preserves_output(tmp_path):
    db, source, service = setup(tmp_path)
    p = source / "one.mp4"
    p.write_bytes(b"a")
    service.scan()
    row = db.connection.execute("select id from clips").fetchone()
    clip_id = row[0]
    db.connection.execute(
        "update clips set state='ready', output_available=1, relative_output_path='films/out.mp4'"
    )
    db.connection.commit()
    p.write_bytes(b"changed")
    service.scan()
    row = db.connection.execute(
        "select state, output_available, relative_output_path from clips where id=?", (clip_id,)
    ).fetchone()
    assert row[0] == ClipState.STALE and row[1] == 1 and row[2] == "films/out.mp4"
    p.unlink()
    service.scan()
    row = db.connection.execute(
        "select state, output_available from clips where id=?", (clip_id,)
    ).fetchone()
    assert row[0] == ClipState.DELETED and row[1] == 1


def test_scan_conservatively_preserves_id_on_unique_move(tmp_path):
    db, source, service = setup(tmp_path)
    old = source / "old.mp4"
    old.write_bytes(b"same")
    service.scan()
    clip_id = db.connection.execute("select id from clips").fetchone()[0]
    old.rename(source / "new.mp4")
    service.scan()
    row = db.connection.execute("select id, relative_source_path from clips").fetchone()
    assert row[0] == clip_id and row[1].endswith("new.mp4")


def test_profile_change_marks_ready_output_stale(tmp_path):
    db, source, service = setup(tmp_path)
    source_file = source / "one.mp4"
    source_file.write_bytes(b"same")
    service.scan()
    db.connection.execute("UPDATE clips SET state='ready', output_available=1")
    db.connection.execute(
        "UPDATE profiles SET settings=? WHERE id='default'", ('{"quality": "high"}',)
    )
    db.connection.commit()
    summary = service.scan()
    row = db.connection.execute("SELECT state FROM clips").fetchone()
    assert summary.modified == 1
    assert row[0] == ClipState.STALE


def test_missing_source_is_deleted_but_output_is_preserved(tmp_path):
    db, source, service = setup(tmp_path)
    source_file = source / "one.mp4"
    source_file.write_bytes(b"same")
    service.scan()
    db.connection.execute(
        "UPDATE clips SET state='ready', output_available=1, relative_output_path='films/out.mp4'"
    )
    db.connection.commit()
    source_file.unlink()
    summary = service.scan()
    row = db.connection.execute(
        "SELECT state, output_available, relative_output_path FROM clips"
    ).fetchone()
    assert summary.deleted == 1
    assert row[0] == ClipState.DELETED and row[1] == 1 and row[2] == "films/out.mp4"
