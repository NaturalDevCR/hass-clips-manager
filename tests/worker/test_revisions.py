import pytest
from cinema_collections_worker.database import Database
from cinema_collections_worker.domain import CollectionCreate, CollectionPatch
from cinema_collections_worker.repositories import (
    CollectionRepository,
    OptimisticConflict,
    ResourceNotFound,
)


def test_stale_revision_conflicts_and_mutations_are_audited(tmp_path):
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    repo = CollectionRepository(db)
    item = repo.create(
        CollectionCreate(
            id="films", name="Films", source_directory="films", processing_profile_id="p"
        ),
        actor="ha",
        request_id="req-1",
    )
    changed = repo.patch(
        "films", item.revision, CollectionPatch(name="New"), actor="ha", request_id="req-2"
    )
    assert changed.revision == 2
    with pytest.raises(OptimisticConflict):
        repo.patch(
            "films", item.revision, CollectionPatch(name="Stale"), actor="ha", request_id="req-3"
        )
    audit = db.connection.execute(
        "select action_type, target_id, summary from audit_events order by id"
    ).fetchall()
    assert [row[0] for row in audit] == ["collection.create", "collection.patch"]
    assert audit[0][1] == "films"
    assert "New" in audit[1][2]
    assert "bearer" not in audit[1][2].lower()


def test_missing_target_is_not_reported_as_stale_revision(tmp_path):
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    with pytest.raises(ResourceNotFound):
        CollectionRepository(db).patch(
            "missing", 1, CollectionPatch(name="x"), actor="ha", request_id="req"
        )


def test_nested_audit_secrets_are_redacted(tmp_path):
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    repo = CollectionRepository(db)
    repo.create(
        CollectionCreate(
            id="films",
            name="Films",
            source_directory="films",
            processing_profile_id="p",
            worker_secret="top-secret",
        ),
        actor="ha",
        request_id="create",
    )
    repo.patch(
        "films",
        1,
        {"notes": "ok"},
        actor="ha",
        request_id="nested",
    )
    # Exercise the redactor directly through the audit payload helper.
    from cinema_collections_worker.repositories import _summary

    rendered = _summary({"outer": [{"password": "bad", "headers": {"authorization": "bad"}}]})
    assert "bad" not in rendered
