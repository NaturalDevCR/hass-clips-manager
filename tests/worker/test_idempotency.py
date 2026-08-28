import pytest
from cinema_collections_worker.database import Database
from cinema_collections_worker.domain import CollectionCreate, CollectionPatch
from cinema_collections_worker.repositories import CollectionRepository, IdempotencyConflict


def test_replaying_request_returns_original_result_without_new_audit(tmp_path):
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    repo = CollectionRepository(db)
    payload = CollectionCreate(
        id="films", name="Films", source_directory="films", processing_profile_id="p"
    )
    first = repo.create(payload, actor="ha", request_id="same")
    replay = repo.create(payload, actor="ha", request_id="same")
    assert replay == first
    assert db.connection.execute("select count(*) from audit_events").fetchone()[0] == 1
    changed = repo.patch("films", 1, CollectionPatch(name="New"), actor="ha", request_id="patch")
    assert (
        repo.patch("films", 1, CollectionPatch(name="New"), actor="ha", request_id="patch")
        == changed
    )
    assert repo.get("films").name == "New"
    assert db.connection.execute("select count(*) from audit_events").fetchone()[0] == 2


def test_same_key_with_different_patch_is_a_conflict(tmp_path):
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    repo = CollectionRepository(db)
    repo.create(
        CollectionCreate(
            id="films", name="Films", source_directory="films", processing_profile_id="p"
        ),
        actor="ha",
        request_id="create",
    )
    repo.patch("films", 1, CollectionPatch(name="New"), actor="ha", request_id="patch")
    with pytest.raises(IdempotencyConflict):
        repo.patch("films", 1, CollectionPatch(name="Other"), actor="ha", request_id="patch")
