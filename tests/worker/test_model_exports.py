from cinema_collections_worker.models import CollectionCreate, CollectionPatch, ProfileCreate


def test_persistence_models_remain_public_from_models_module():
    assert CollectionCreate and CollectionPatch and ProfileCreate
