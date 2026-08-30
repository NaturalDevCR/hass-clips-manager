from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from cinema_collections_worker.database import Database
from cinema_collections_worker.domain import CollectionCreate, ProfileCreate
from cinema_collections_worker.library_manager import DeleteTarget, LibraryManager, TrashTarget
from cinema_collections_worker.paths import RootKey, SafePathResolver
from cinema_collections_worker.profile_validation import ProcessingProfile
from cinema_collections_worker.repositories import CollectionRepository, ProfileRepository
from fastapi import UploadFile


def _manager(
    tmp_path: Path, *, max_upload_bytes: int = 16
) -> tuple[Database, LibraryManager, Path, Path]:
    db = Database.create(":memory:")
    ProfileRepository(db).create(
        ProfileCreate(id="default", name="Default", settings={}), actor="test", request_id="profile"
    )
    CollectionRepository(db).create(
        CollectionCreate(
            id="films", name="Films", source_directory="films", processing_profile_id="default"
        ),
        actor="test",
        request_id="collection",
    )
    source = tmp_path / "source"
    compiled = tmp_path / "compiled"
    temp = tmp_path / "temp"
    assets = tmp_path / "assets"
    for root in (source, compiled, temp, assets):
        root.mkdir()
    manager = LibraryManager(
        db,
        SafePathResolver(
            {
                RootKey.SOURCE: source,
                RootKey.COMPILED: compiled,
                RootKey.TEMP: temp,
                RootKey.ASSETS: assets,
            }
        ),
        max_upload_bytes=max_upload_bytes,
    )
    return db, manager, source, compiled


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(data))


def _clip_with_output(db: Database, manager: LibraryManager, compiled: Path) -> tuple[object, Path]:
    clip = manager.import_clip("films", _upload("one.mp4", b"source"))
    output = compiled / "films" / "one.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"output")
    with db.connection:
        db.connection.execute(
            "UPDATE clips SET relative_output_path=?, output_available=1 WHERE id=?",
            ("films/one.mp4", str(clip.id)),
        )
    return clip, output


def test_import_rejects_unsupported_or_oversized_uploads_without_writing(tmp_path: Path) -> None:
    _db, manager, source, _compiled = _manager(tmp_path)

    with pytest.raises(ValueError, match="extension"):
        manager.import_clip("films", _upload("not-video.txt", b"clip"))
    with pytest.raises(ValueError, match="size"):
        manager.import_clip("films", _upload("too-big.mp4", b"more than sixteen"))

    assert not (source / "films").exists()


def test_import_and_move_preserve_worker_generated_clip_id_inside_collection_root(
    tmp_path: Path,
) -> None:
    _db, manager, source, _compiled = _manager(tmp_path)

    clip = manager.import_clip("films", _upload("original.mp4", b"clip"))
    moved = manager.rename_or_move(clip.id, "films/organized/renamed.mp4")

    assert moved.id == clip.id
    assert moved.relative_source_path == "films/organized/renamed.mp4"
    assert not (source / "films" / "original.mp4").exists()
    assert (source / "films" / "organized" / "renamed.mp4").read_bytes() == b"clip"
    with pytest.raises(ValueError, match="collection source directory"):
        manager.rename_or_move(clip.id, "outside.mp4")


def test_clip_tags_notes_and_scan_recompile_requests_are_audited(tmp_path: Path) -> None:
    db, manager, _source, _compiled = _manager(tmp_path)
    clip = manager.import_clip("films", _upload("one.mp4", b"clip"))

    updated = manager.update_tags_and_notes(clip.id, ["night", "featured"], "Ready for review")
    scan = manager.request_scan(clip.id)
    recompile = manager.request_recompile(clip.id)

    assert updated.metadata["tags"] == ["night", "featured"]
    assert updated.metadata["notes"] == "Ready for review"
    assert scan.action_type == "library.scan_requested"
    assert recompile.action_type == "library.recompile_requested"
    assert db.connection.execute("SELECT kind FROM jobs").fetchone()[0] == "scan"
    assert db.connection.execute("SELECT count(*) FROM audit_events").fetchone()[0] >= 4


def test_trash_restore_is_recoverable_and_never_automatically_purged(tmp_path: Path) -> None:
    db, manager, source, _compiled = _manager(tmp_path)
    clip = manager.import_clip("films", _upload("one.mp4", b"clip"))

    event = manager.move_to_trash(clip.id, TrashTarget.SOURCE)
    trash_id = event.details["trash_id"]

    assert not (source / "films" / "one.mp4").exists()
    assert manager.list_trash()[0].id == trash_id
    assert manager.purge_expired_trash() == 0
    restored = manager.restore(trash_id)

    assert restored.id == clip.id
    assert (source / "films" / "one.mp4").read_bytes() == b"clip"
    assert db.connection.execute(
        "SELECT restored_at FROM trash_records WHERE id=?", (trash_id,)
    ).fetchone()[0]


def test_trash_move_failure_compensates_files_and_retains_pending_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, manager, source, compiled = _manager(tmp_path)
    clip, output = _clip_with_output(db, manager, compiled)
    original_move = manager._move_exact

    def fail_output_move(source_path: Path, destination: Path) -> None:
        if destination.name == "output.mp4":
            raise OSError("injected output move failure")
        original_move(source_path, destination)

    monkeypatch.setattr(manager, "_move_exact", fail_output_move)

    with pytest.raises(OSError, match="injected output move failure"):
        manager.move_to_trash(str(clip.id), TrashTarget.BOTH)

    record = db.connection.execute(
        "SELECT status, failure FROM trash_records WHERE clip_id=?", (str(clip.id),)
    ).fetchone()
    assert (source / "films" / "one.mp4").read_bytes() == b"source"
    assert output.read_bytes() == b"output"
    assert record["status"] == "failed" and "injected" in record["failure"]
    assert (
        db.connection.execute(
            "SELECT count(*) FROM audit_events WHERE action_type='library.trash_pending'"
        ).fetchone()[0]
        == 1
    )


def test_restore_failure_rolls_back_all_files_and_leaves_trash_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, manager, source, compiled = _manager(tmp_path)
    clip, output = _clip_with_output(db, manager, compiled)
    trash_id = manager.move_to_trash(str(clip.id), TrashTarget.BOTH).details["trash_id"]
    original_move = manager._move_exact

    def fail_output_restore(source_path: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("injected output restore failure")
        original_move(source_path, destination)

    monkeypatch.setattr(manager, "_move_exact", fail_output_restore)

    with pytest.raises(OSError, match="injected output restore failure"):
        manager.restore(trash_id)

    record = db.connection.execute(
        "SELECT status, restored_at FROM trash_records WHERE id=?", (trash_id,)
    ).fetchone()
    assert not (source / "films" / "one.mp4").exists()
    assert not output.exists()
    assert (manager.trash_root / trash_id / "source.mp4").read_bytes() == b"source"
    assert (manager.trash_root / trash_id / "output.mp4").read_bytes() == b"output"
    assert record["status"] == "active" and record["restored_at"] is None


def test_permanent_delete_cleanup_failure_keeps_only_inaccessible_staged_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, manager, _source, compiled = _manager(tmp_path)
    clip, output = _clip_with_output(db, manager, compiled)
    original_unlink = Path.unlink

    def fail_output_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.name == "output.mp4" and ".permanent-delete" in path.parts:
            raise OSError("injected output unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_output_unlink)
    token = manager.delete_confirmation_token(clip.id, DeleteTarget.BOTH)

    event = manager.permanently_delete(str(clip.id), DeleteTarget.BOTH, token)

    request = db.connection.execute(
        "SELECT state, failure FROM lifecycle_requests WHERE clip_id=?", (str(clip.id),)
    ).fetchone()
    assert request["state"] == "completed_with_residue" and "injected" in request["failure"]
    assert event.details["cleanup_pending"] is True
    assert not output.exists()
    assert (
        db.connection.execute(
            "SELECT count(*) FROM audit_events WHERE action_type='library.permanent_delete.pending'"
        ).fetchone()[0]
        == 1
    )


def test_permanent_delete_both_compensates_if_second_target_cannot_be_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, manager, source, compiled = _manager(tmp_path)
    clip, output = _clip_with_output(db, manager, compiled)
    source_path = source / "films" / "one.mp4"
    original_move = manager._move_exact
    staged_moves = 0

    def fail_second_stage(source_file: Path, destination: Path) -> None:
        nonlocal staged_moves
        if ".permanent-delete" in destination.parts:
            staged_moves += 1
            if staged_moves == 2:
                raise OSError("injected second staging failure")
        original_move(source_file, destination)

    monkeypatch.setattr(manager, "_move_exact", fail_second_stage)
    token = manager.delete_confirmation_token(clip.id, DeleteTarget.BOTH)

    with pytest.raises(OSError, match="second staging failure"):
        manager.permanently_delete(str(clip.id), DeleteTarget.BOTH, token)

    assert source_path.read_bytes() == b"source"
    assert output.read_bytes() == b"output"
    row = db.connection.execute("SELECT state FROM clips WHERE id=?", (str(clip.id),)).fetchone()
    assert row["state"] == "discovered"


def test_import_asset_rejects_unsafe_filenames_and_disallowed_extensions(
    tmp_path: Path,
) -> None:
    _db, manager, _source, _compiled = _manager(tmp_path)
    assets = manager.resolver.roots[RootKey.ASSETS]

    with pytest.raises(ValueError):
        manager.import_asset(_upload("../intro.mp4", b"clip"))
    with pytest.raises(ValueError, match="extension"):
        manager.import_asset(_upload("notes.txt", b"clip"))

    assert manager.list_assets() == []
    assert list(assets.iterdir()) == []


def test_import_and_list_assets_are_flat_in_the_assets_root(tmp_path: Path) -> None:
    _db, manager, _source, _compiled = _manager(tmp_path)
    assets = manager.resolver.roots[RootKey.ASSETS]

    event = manager.import_asset(_upload("intro.mp4", b"clip"))
    manager.import_asset(_upload("outro.mp4", b"clip"))

    assert event.action_type == "library.asset_imported"
    assert event.details["filename"] == "intro.mp4"
    assert (assets / "intro.mp4").read_bytes() == b"clip"
    assert manager.list_assets() == ["intro.mp4", "outro.mp4"]
    with pytest.raises(ValueError, match="already exists"):
        manager.import_asset(_upload("intro.mp4", b"clip"))


def _profile_settings(reference_field: str, filename: str) -> str:
    settings = ProcessingProfile().model_dump(mode="json")
    settings[reference_field] = filename
    return json.dumps(settings, sort_keys=True)


def test_delete_asset_removes_file_and_audits_it(tmp_path: Path) -> None:
    db, manager, _source, _compiled = _manager(tmp_path)
    assets = manager.resolver.roots[RootKey.ASSETS]
    manager.import_asset(_upload("intro.mp4", b"clip"))

    event = manager.delete_asset("intro.mp4")

    assert event.action_type == "library.asset_deleted"
    assert event.details["filename"] == "intro.mp4"
    assert manager.list_assets() == []
    assert not (assets / "intro.mp4").exists()
    assert (
        db.connection.execute(
            "SELECT count(*) FROM audit_events WHERE action_type='library.asset_deleted'"
        ).fetchone()[0]
        == 1
    )


def test_delete_asset_refuses_missing_file_and_rejects_paths(tmp_path: Path) -> None:
    _db, manager, _source, _compiled = _manager(tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        manager.delete_asset("missing.mp4")
    with pytest.raises(ValueError, match="path component"):
        manager.delete_asset("nested/intro.mp4")
    with pytest.raises(ValueError, match="path component"):
        manager.delete_asset("../intro.mp4")

    assert manager.list_assets() == []


def test_delete_asset_refuses_asset_referenced_by_profile_intro_reference(
    tmp_path: Path,
) -> None:
    db, manager, _source, _compiled = _manager(tmp_path)
    manager.import_asset(_upload("intro.mp4", b"clip"))
    with db.connection:
        db.connection.execute(
            "UPDATE profiles SET settings=? WHERE id='default'",
            (_profile_settings("intro_reference", "intro.mp4"),),
        )

    with pytest.raises(ValueError, match="profile 'default'"):
        manager.delete_asset("intro.mp4")

    assert manager.list_assets() == ["intro.mp4"]


def test_delete_asset_refuses_asset_referenced_by_profile_outro_reference(
    tmp_path: Path,
) -> None:
    db, manager, _source, _compiled = _manager(tmp_path)
    manager.import_asset(_upload("outro.mp4", b"clip"))
    with db.connection:
        db.connection.execute(
            "UPDATE profiles SET settings=? WHERE id='default'",
            (_profile_settings("outro_reference", "outro.mp4"),),
        )

    with pytest.raises(ValueError, match="profile 'default'"):
        manager.delete_asset("outro.mp4")

    assert manager.list_assets() == ["outro.mp4"]


@pytest.mark.parametrize(
    ("target", "source_exists", "output_exists"),
    [
        (DeleteTarget.SOURCE, False, True),
        (DeleteTarget.OUTPUT, True, False),
        (DeleteTarget.BOTH, False, False),
    ],
)
def test_permanent_delete_targets_only_the_selected_exact_catalog_paths(
    tmp_path: Path, target: DeleteTarget, source_exists: bool, output_exists: bool
) -> None:
    db, manager, source, compiled = _manager(tmp_path)
    clip = manager.import_clip("films", _upload("one.mp4", b"clip"))
    output = compiled / "films" / "one.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"output")
    with db.connection:
        db.connection.execute(
            "UPDATE clips SET relative_output_path=?, output_available=1 WHERE id=?",
            ("films/one.mp4", str(clip.id)),
        )

    with pytest.raises(ValueError, match="confirmation"):
        manager.permanently_delete(clip.id, target, "wrong")
    event = manager.permanently_delete(
        clip.id, target, manager.delete_confirmation_token(clip.id, target)
    )

    assert event.action_type == "library.permanently_deleted"
    assert (source / "films" / "one.mp4").exists() is source_exists
    assert output.exists() is output_exists


def _staging_rows(db: Database) -> list[object]:
    return db.connection.execute("SELECT * FROM upload_sessions").fetchall()


def test_begin_upload_rejects_invalid_requests_before_creating_any_staging_file(
    tmp_path: Path,
) -> None:
    db, manager, _source, _compiled = _manager(tmp_path)
    staging_root = manager.resolver.roots[RootKey.TEMP] / "uploads"

    with pytest.raises(ValueError):
        manager.begin_upload("clip", "../one.mp4", "films")
    with pytest.raises(ValueError, match="extension"):
        manager.begin_upload("clip", "notes.txt", "films")
    with pytest.raises(ValueError, match="extension"):
        manager.begin_upload("asset", "notes.txt")
    with pytest.raises(KeyError):
        manager.begin_upload("clip", "one.mp4", "missing-collection")
    with pytest.raises(ValueError, match="kind"):
        manager.begin_upload("unknown-kind", "one.mp4", "films")

    assert not staging_root.exists() or list(staging_root.iterdir()) == []
    assert _staging_rows(db) == []


def test_append_chunk_accumulates_and_enforces_the_cap_on_the_running_total(
    tmp_path: Path,
) -> None:
    db, manager, _source, _compiled = _manager(tmp_path)
    upload_id = manager.begin_upload("clip", "one.mp4", "films")

    assert manager.append_chunk(upload_id, b"0123456789") == 10
    with pytest.raises(ValueError, match="size"):
        manager.append_chunk(upload_id, b"01234567")

    row = db.connection.execute(
        "SELECT bytes_received, staging_path FROM upload_sessions WHERE id=?", (upload_id,)
    ).fetchone()
    assert row["bytes_received"] == 10
    assert Path(str(row["staging_path"])).read_bytes() == b"0123456789"


def test_append_chunk_rejects_unknown_and_finished_upload_ids(tmp_path: Path) -> None:
    db, manager, _source, _compiled = _manager(tmp_path)
    upload_id = manager.begin_upload("clip", "one.mp4", "films")
    manager.append_chunk(upload_id, b"clip")
    manager.finish_upload(upload_id)

    with pytest.raises(KeyError):
        manager.append_chunk("never-existed", b"clip")
    with pytest.raises(KeyError):
        manager.append_chunk(upload_id, b"clip")
    assert _staging_rows(db) == []


def test_finish_upload_publishes_byte_identical_multi_chunk_content(tmp_path: Path) -> None:
    db, manager, source, _compiled = _manager(tmp_path)
    upload_id = manager.begin_upload("clip", "one.mp4", "films")
    manager.append_chunk(upload_id, b"fir")
    manager.append_chunk(upload_id, b"st-part")
    manager.append_chunk(upload_id, b"second")

    clip = manager.finish_upload(upload_id)

    assert clip.relative_source_path == "films/one.mp4"
    assert (source / "films" / "one.mp4").read_bytes() == b"first-partsecond"
    assert (
        db.connection.execute(
            "SELECT count(*) FROM audit_events WHERE action_type='library.imported'"
        ).fetchone()[0]
        == 1
    )
    assert _staging_rows(db) == []


def test_finish_upload_refuses_an_existing_destination_and_keeps_the_upload(
    tmp_path: Path,
) -> None:
    db, manager, source, _compiled = _manager(tmp_path)
    manager.import_clip("films", _upload("one.mp4", b"original"))
    upload_id = manager.begin_upload("clip", "one.mp4", "films")
    manager.append_chunk(upload_id, b"replacement")

    with pytest.raises(ValueError, match="already exists"):
        manager.finish_upload(upload_id)

    assert (source / "films" / "one.mp4").read_bytes() == b"original"
    assert len(_staging_rows(db)) == 1

    manager.abort_upload(upload_id)
    assert _staging_rows(db) == []


def test_finish_upload_publishes_assets_exactly_like_import_asset(tmp_path: Path) -> None:
    db, manager, _source, _compiled = _manager(tmp_path)
    assets = manager.resolver.roots[RootKey.ASSETS]
    upload_id = manager.begin_upload("asset", "intro.mp4")
    manager.append_chunk(upload_id, b"in")
    manager.append_chunk(upload_id, b"tro")

    event = manager.finish_upload(upload_id)

    assert event.action_type == "library.asset_imported"
    assert event.details["filename"] == "intro.mp4"
    assert (assets / "intro.mp4").read_bytes() == b"intro"
    assert manager.list_assets() == ["intro.mp4"]

    other = manager.begin_upload("asset", "intro.mp4")
    manager.append_chunk(other, b"again")
    with pytest.raises(ValueError, match="already exists"):
        manager.finish_upload(other)


def test_abort_upload_removes_the_staging_file_and_its_tracking_row(tmp_path: Path) -> None:
    db, manager, _source, _compiled = _manager(tmp_path)
    upload_id = manager.begin_upload("clip", "one.mp4", "films")
    manager.append_chunk(upload_id, b"clip")
    row = db.connection.execute(
        "SELECT staging_path FROM upload_sessions WHERE id=?", (upload_id,)
    ).fetchone()
    staging = Path(str(row["staging_path"]))
    assert staging.exists()

    manager.abort_upload(upload_id)

    assert not staging.exists()
    assert _staging_rows(db) == []
    with pytest.raises(KeyError):
        manager.abort_upload(upload_id)


def test_abandoned_upload_cleanup_removes_only_stale_tracked_uploads(tmp_path: Path) -> None:
    db, manager, _source, _compiled = _manager(tmp_path)
    stale_id = manager.begin_upload("clip", "stale.mp4", "films")
    manager.append_chunk(stale_id, b"old")
    fresh_id = manager.begin_upload("asset", "fresh.mp4")
    manager.append_chunk(fresh_id, b"new")
    rows = db.connection.execute("SELECT id, staging_path FROM upload_sessions").fetchall()
    paths = {str(row["id"]): Path(str(row["staging_path"])) for row in rows}
    stale_timestamp = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    with db.connection:
        db.connection.execute(
            "UPDATE upload_sessions SET updated_at=? WHERE id=?", (stale_timestamp, stale_id)
        )

    removed = manager.cleanup_abandoned_uploads()

    assert removed == 1
    assert not paths[stale_id].exists()
    assert paths[fresh_id].read_bytes() == b"new"
    remaining = db.connection.execute("SELECT id FROM upload_sessions").fetchall()
    assert [str(row["id"]) for row in remaining] == [fresh_id]
