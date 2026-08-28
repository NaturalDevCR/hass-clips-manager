from cinema_collections_worker.database import Database
from cinema_collections_worker.domain import CollectionCreate, ProfileCreate
from cinema_collections_worker.repositories import CollectionRepository, ProfileRepository


def test_collections_are_persistent_and_ids_are_unique(tmp_path):
    path = str(tmp_path / "worker.sqlite3")
    db = Database.create(path)
    repo = CollectionRepository(db)
    created = repo.create(
        CollectionCreate(
            id="films", name="Films", source_directory="films", processing_profile_id="default"
        ),
        actor="test",
        request_id="r1",
    )
    assert created.id == "films"
    assert created.revision == 1
    assert created.compiled_output_prefix == "films"
    try:
        repo.create(
            CollectionCreate(
                id="films", name="Again", source_directory="x", processing_profile_id="default"
            ),
            actor="test",
            request_id="r2",
        )
    except Exception as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate collection ID accepted")
    db.close()
    reopened = Database.create(path)
    assert CollectionRepository(reopened).get("films").name == "Films"


def test_only_one_default_collection_is_allowed(tmp_path):
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    repo = CollectionRepository(db)
    repo.create(
        CollectionCreate(
            id="one", name="One", source_directory="one", processing_profile_id="p", is_default=True
        ),
        actor="a",
        request_id="1",
    )
    second = repo.create(
        CollectionCreate(
            id="two", name="Two", source_directory="two", processing_profile_id="p", is_default=True
        ),
        actor="a",
        request_id="2",
    )
    assert second.is_default is True
    assert repo.get("one").is_default is False


def test_profile_version_increments_on_patch(tmp_path):
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    repo = ProfileRepository(db)
    profile = repo.create(
        ProfileCreate(id="p", name="P", settings={"x": 1}), actor="a", request_id="1"
    )
    updated = repo.patch("p", profile.revision, {"settings": {"x": 2}}, actor="a", request_id="2")
    assert updated.version == 2
    assert updated.revision == 2
