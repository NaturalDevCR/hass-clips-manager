from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from cinema_collections_worker.database import Database
from cinema_collections_worker.domain import CollectionCreate, ProfileCreate
from cinema_collections_worker.library_manager import DeleteTarget, LibraryManager, TrashTarget
from cinema_collections_worker.paths import RootKey, SafePathResolver
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
    for root in (source, compiled, temp):
        root.mkdir()
    manager = LibraryManager(
        db,
        SafePathResolver({RootKey.SOURCE: source, RootKey.COMPILED: compiled, RootKey.TEMP: temp}),
        max_upload_bytes=max_upload_bytes,
    )
    return db, manager, source, compiled


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(data))


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
