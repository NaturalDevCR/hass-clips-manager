"""Source catalog discovery and conservative reconciliation."""
# ruff: noqa: E501
# Profile settings are user-authored JSON and validated at runtime.
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .database import Database
from .paths import PathSafetyError, RootKey, SafePathResolver, validate_relative_path
from .probe import MediaProbeResult, ProbeClient
from .profile_validation import AssetFingerprints, ProcessingProfile, profile_fingerprint


class _Probe(Protocol):
    def probe(self, path: Path) -> MediaProbeResult: ...


@dataclass(frozen=True)
class ScanSummary:
    added: int = 0
    modified: int = 0
    deleted: int = 0
    invalid: int = 0
    unchanged: int = 0


_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".ts"}


class CatalogService:
    def __init__(
        self, db: Database, resolver: SafePathResolver, probe_client: _Probe | None = None
    ) -> None:
        self.db = db
        self.resolver = resolver
        self.probe_client = probe_client or ProbeClient()

    @staticmethod
    def _fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        stat = path.stat()
        return f"{digest.hexdigest()}:{stat.st_size}"

    def _files(self, root: Path) -> list[tuple[str, Path]]:
        found: list[tuple[str, Path]] = []
        if not root.is_dir():
            return found
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
            for name in names:
                candidate = Path(directory) / name
                if candidate.is_symlink() or candidate.suffix.lower() not in _EXTENSIONS:
                    continue
                relative = candidate.relative_to(self.resolver.roots[RootKey.SOURCE]).as_posix()
                try:
                    validate_relative_path(relative)
                    self.resolver.resolve(RootKey.SOURCE.value, relative)
                except PathSafetyError:
                    continue
                found.append((relative, candidate))
        return found

    @staticmethod
    def _profile_fingerprint(settings: object) -> str:
        """Fingerprint validated profile settings, tolerating incomplete legacy rows."""
        try:
            if isinstance(settings, dict):
                assets = settings.get("assets", {})
                profile = ProcessingProfile.model_validate(settings)
                return profile_fingerprint(
                    profile, assets if isinstance(assets, dict) else AssetFingerprints()
                )
        except Exception:
            pass
        canonical = json.dumps(settings, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def scan(self, collection_ids: set[str] | None = None) -> ScanSummary:
        selected = collection_ids
        rows = self.db.connection.execute("SELECT * FROM collections WHERE enabled=1").fetchall()
        if selected is not None:
            rows = [row for row in rows if row["id"] in selected]
        added = modified = deleted = invalid = unchanged = 0
        seen: set[tuple[str, str]] = set()
        now = datetime.now(UTC).isoformat()
        with self.db.connection:
            for collection in rows:
                collection_id = collection["id"]
                profile_row = self.db.connection.execute(
                    "SELECT settings FROM profiles WHERE id=?",
                    (collection["processing_profile_id"],),
                ).fetchone()
                profile_settings = json.loads(profile_row["settings"]) if profile_row else {}
                current_profile_fingerprint = self._profile_fingerprint(profile_settings)
                root = self.resolver.resolve(RootKey.SOURCE.value, collection["source_directory"])
                for relative, path in self._files(root):
                    key = (collection_id, relative)
                    seen.add(key)
                    fingerprint = self._fingerprint(path)
                    existing = self.db.connection.execute(
                        "SELECT * FROM clips WHERE collection_id=? AND relative_source_path=?", key
                    ).fetchone()
                    try:
                        probe = self.probe_client.probe(path)
                    except Exception:
                        probe = MediaProbeResult(valid=False, error="media probe failed")
                    metadata = {
                        "source_fingerprint": fingerprint,
                        "profile_fingerprint": current_profile_fingerprint,
                        "has_audio": probe.has_audio,
                        "width": probe.width,
                        "height": probe.height,
                        "frame_rate": probe.frame_rate,
                        "streams": probe.streams,
                        "size_bytes": probe.size_bytes,
                    }
                    state = "discovered" if probe.valid else "invalid"
                    if not probe.valid:
                        invalid += 1
                    if existing is None:
                        # A scan may infer a Library Manager move only when exactly
                        # one prior record has the same content and its old path is
                        # absent. This avoids treating duplicate media as a move.
                        candidates = self.db.connection.execute(
                            "SELECT * FROM clips WHERE collection_id=? AND json_extract(metadata, '$.source_fingerprint')=?",
                            (collection_id, fingerprint),
                        ).fetchall()
                        missing_candidates = [
                            candidate
                            for candidate in candidates
                            if not (
                                self.resolver.roots[RootKey.SOURCE]
                                / candidate["relative_source_path"]
                            ).exists()
                        ]
                        if len(missing_candidates) == 1:
                            existing = missing_candidates[0]
                            self.db.connection.execute(
                                "UPDATE clips SET relative_source_path=?, state=?, duration_seconds=?, metadata=?, updated_at=? WHERE id=?",
                                (
                                    relative,
                                    state,
                                    probe.duration_seconds,
                                    json.dumps(metadata, sort_keys=True),
                                    now,
                                    existing["id"],
                                ),
                            )
                            modified += 1
                            continue
                        clip_id = str(uuid.uuid4())
                        output = (
                            f"{collection['compiled_output_prefix']}/{clip_id}{path.suffix.lower()}"
                        )
                        self.db.connection.execute(
                            "INSERT INTO clips(id, collection_id, state, relative_source_path, relative_output_path, "
                            "duration_seconds, output_available, metadata, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                            (
                                clip_id,
                                collection_id,
                                state,
                                relative,
                                output,
                                probe.duration_seconds,
                                0,
                                json.dumps(metadata, sort_keys=True),
                                now,
                            ),
                        )
                        added += 1
                    else:
                        old_meta = json.loads(existing["metadata"])
                        changed = old_meta.get("source_fingerprint") != fingerprint
                        profile_changed = (
                            old_meta.get("profile_fingerprint") != current_profile_fingerprint
                        )
                        if changed or profile_changed:
                            state = "stale" if existing["output_available"] else state
                            modified += 1
                        else:
                            state = existing["state"]
                            unchanged += 1
                        self.db.connection.execute(
                            "UPDATE clips SET state=?, duration_seconds=?, metadata=?, updated_at=? WHERE id=?",
                            (
                                state,
                                probe.duration_seconds,
                                json.dumps(metadata, sort_keys=True),
                                now,
                                existing["id"],
                            ),
                        )
                missing = self.db.connection.execute(
                    "SELECT * FROM clips WHERE collection_id=?", (collection_id,)
                ).fetchall()
                for clip in missing:
                    if (collection_id, clip["relative_source_path"]) not in seen and clip[
                        "state"
                    ] != "deleted":
                        self.db.connection.execute(
                            "UPDATE clips SET state=?, updated_at=? WHERE id=?",
                            ("deleted", now, clip["id"]),
                        )
                        deleted += 1
            # Missing files and their compiled outputs are deliberately retained.
        return ScanSummary(added, modified, deleted, invalid, unchanged)
