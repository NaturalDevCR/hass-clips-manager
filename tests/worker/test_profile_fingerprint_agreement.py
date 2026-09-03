"""The catalog and the job runner must agree on a profile's fingerprint."""

from __future__ import annotations

import json
from pathlib import Path

from cinema_collections_worker.catalog import CatalogService
from cinema_collections_worker.database import Database
from cinema_collections_worker.jobs import JobService
from cinema_collections_worker.paths import RootKey, SafePathResolver

_SETTINGS = {"intro_reference": "intro.mp4", "outro_reference": "intro.mp4"}


def _services(tmp_path: Path) -> tuple[CatalogService, JobService, SafePathResolver]:
    for key in RootKey:
        (tmp_path / key.value).mkdir(parents=True, exist_ok=True)
    resolver = SafePathResolver({key.value: tmp_path / key.value for key in RootKey})
    database = Database.create(str(tmp_path / "worker.sqlite3"))
    (resolver.roots[RootKey.ASSETS] / "intro.mp4").write_bytes(b"intro-bytes")
    with database.connection:
        database.connection.execute(
            "INSERT INTO profiles(id,name,settings,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("p", "Profile", json.dumps(_SETTINGS), "now", "now"),
        )
        database.connection.execute(
            "INSERT INTO collections("
            "id,name,enabled,source_directory,compiled_output_prefix,processing_profile_id,"
            "is_default,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            ("films", "Films", 1, "films", "films", "p", 0, "now", "now"),
        )
    return (
        CatalogService(database, resolver),
        JobService(database, resolver, disk_reserve_bytes=0),
        resolver,
    )


def test_catalog_and_jobs_fingerprint_a_profile_with_assets_identically(tmp_path: Path) -> None:
    """A disagreement here recompiles every clip of that collection forever.

    A profile's fingerprint folds in the fingerprints of its intro and outro
    assets. The catalog and the job runner each carried their own copy of the
    file-fingerprint helper and the two had drifted — one appended the byte
    size, the other did not — so any profile referencing an asset produced a
    different value depending on which asked. A clip compiled, recorded one
    value, and the very next scan compared it against the other, marked it
    stale, and queued it again. On a live install 39 clips sat permanently
    stale with perfectly good compiled output.
    """
    catalog, jobs, _ = _services(tmp_path)

    _, catalog_fingerprint = catalog._profile_details(_SETTINGS)
    _, jobs_fingerprint = jobs._profile("films")

    assert catalog_fingerprint == jobs_fingerprint


def test_the_two_file_fingerprint_helpers_return_one_answer(tmp_path: Path) -> None:
    """Two copies is how they drifted apart; assert there is only one answer."""
    catalog, jobs, _ = _services(tmp_path)
    target = tmp_path / "sample.bin"
    target.write_bytes(b"some bytes")

    assert catalog._fingerprint(target) == jobs._file_fingerprint(target)
