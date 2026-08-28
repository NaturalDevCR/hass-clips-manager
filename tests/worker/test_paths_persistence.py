import pytest

from cinema_collections_worker.database import Database
from cinema_collections_worker.domain import CollectionCreate
from cinema_collections_worker.repositories import CollectionRepository


@pytest.mark.parametrize("source", ["/etc", "../outside", "films/../../outside"])
def test_collection_source_directory_rejects_unsafe_paths(tmp_path, source):
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    with pytest.raises(ValueError):
        CollectionRepository(db).create(
            CollectionCreate(id="films", name="Films", source_directory=source, processing_profile_id="p"),
            actor="ha", request_id=source,
        )
