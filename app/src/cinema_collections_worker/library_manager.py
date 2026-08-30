"""Safe, Worker-owned lifecycle operations for catalogued library clips."""

from __future__ import annotations

import hmac
import json
import os
import shutil
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .database import Database
from .jobs import CompileRequest, JobProgress, JobRecord, JobService, JobStage
from .models import ClipRecord, ClipState
from .paths import RootKey, SafePathResolver, validate_filename, validate_relative_path
from .queue import PersistentJobQueue

_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".ts"}
_UPLOAD_MAX_AGE_SECONDS = 24 * 60 * 60


class TrashTarget(StrEnum):
    SOURCE = "source"
    OUTPUT = "output"
    BOTH = "both"


class DeleteTarget(StrEnum):
    SOURCE = "source"
    OUTPUT = "output"
    BOTH = "both"


class UploadKind(StrEnum):
    CLIP = "clip"
    ASSET = "asset"


class AuditEvent(BaseModel):
    """An immutable record of one manager-initiated action."""

    model_config = ConfigDict(extra="forbid")

    id: int
    action_type: str
    target_id: str
    actor: str
    request_id: str
    summary: str
    occurred_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class TrashRecord(BaseModel):
    """Recoverable single-clip trash entry owned by the Worker."""

    model_config = ConfigDict(extra="forbid")

    id: str
    clip_id: UUID
    target: TrashTarget
    created_at: datetime
    restored_at: datetime | None = None


class LibraryManager:
    """Perform exact, catalog-backed media mutations without cascade deletion."""

    def __init__(
        self,
        db: Database,
        resolver: SafePathResolver,
        *,
        trash_root: Path | None = None,
        max_upload_bytes: int = 2 * 1024 * 1024 * 1024,
        actor: str = "library-manager",
        queue: PersistentJobQueue | None = None,
        jobs: JobService | None = None,
    ) -> None:
        if max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        self.db = db
        self.resolver = resolver
        self.max_upload_bytes = max_upload_bytes
        self.actor = actor
        self.queue = queue or PersistentJobQueue(db)
        self.jobs = jobs
        default_root = resolver.roots.get(RootKey.TEMP)
        if default_root is None:
            default_root = resolver.roots[RootKey.SOURCE].parent / "cinema-collections-trash"
        candidate = Path(trash_root) if trash_root is not None else default_root / "library-trash"
        if not candidate.is_absolute():
            raise ValueError("trash root must be absolute")
        self.trash_root = candidate.resolve(strict=False)
        self.trash_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _clip_from_row(row: Any) -> ClipRecord:
        return ClipRecord.model_validate(
            {
                "id": row["id"],
                "collection_id": row["collection_id"],
                "state": row["state"],
                "relative_source_path": row["relative_source_path"],
                "relative_output_path": row["relative_output_path"],
                "duration_seconds": row["duration_seconds"],
                "output_available": bool(row["output_available"]),
                "metadata": json.loads(row["metadata"] or "{}"),
                "updated_at": row["updated_at"],
            }
        )

    def _clip_row(self, clip_id: str | UUID) -> Any:
        row = self.db.connection.execute(
            "SELECT * FROM clips WHERE id=?", (str(clip_id),)
        ).fetchone()
        if row is None:
            raise KeyError(str(clip_id))
        return row

    def _collection_source_root(self, collection_id: str) -> Path:
        row = self.db.connection.execute(
            "SELECT source_directory FROM collections WHERE id=?", (collection_id,)
        ).fetchone()
        if row is None:
            raise KeyError(collection_id)
        return self.resolver.resolve(RootKey.SOURCE.value, str(row["source_directory"]))

    @staticmethod
    def _require_regular_file(path: Path) -> None:
        if path.is_symlink() or not path.is_file():
            raise ValueError("selected catalog file is unavailable")

    def _source_path(self, row: Any) -> Path:
        path = self.resolver.resolve(RootKey.SOURCE.value, str(row["relative_source_path"]))
        self._require_regular_file(path)
        return path

    def _output_path(self, row: Any) -> Path:
        relative = row["relative_output_path"]
        if not bool(row["output_available"]) or not relative:
            raise ValueError("selected compiled output is unavailable")
        path = self.resolver.resolve(RootKey.COMPILED.value, str(relative))
        self._require_regular_file(path)
        return path

    @staticmethod
    def _targets(target: TrashTarget | DeleteTarget) -> tuple[bool, bool]:
        return (
            target
            in {TrashTarget.SOURCE, DeleteTarget.SOURCE, TrashTarget.BOTH, DeleteTarget.BOTH},
            target
            in {TrashTarget.OUTPUT, DeleteTarget.OUTPUT, TrashTarget.BOTH, DeleteTarget.BOTH},
        )

    def _audit(self, action_type: str, target_id: str, details: dict[str, Any]) -> AuditEvent:
        with self.db.connection:
            return self._insert_audit(action_type, target_id, details)

    def _insert_audit(
        self, action_type: str, target_id: str, details: dict[str, Any]
    ) -> AuditEvent:
        """Insert an audit row in the caller's transaction when one is open."""

        request_id = str(uuid.uuid4())
        occurred_at = self._now()
        summary = json.dumps(details, sort_keys=True, separators=(",", ":"))
        cursor = self.db.connection.execute(
            "INSERT INTO audit_events("
            "action_type,target_id,actor,request_id,summary,occurred_at"
            ") VALUES(?,?,?,?,?,?)",
            (action_type, target_id, self.actor, request_id, summary, occurred_at),
        )
        return AuditEvent(
            id=int(cursor.lastrowid or 0),
            action_type=action_type,
            target_id=target_id,
            actor=self.actor,
            request_id=request_id,
            summary=summary,
            occurred_at=datetime.fromisoformat(occurred_at),
            details=details,
        )

    def _mark_trash_failure(self, trash_id: str, failure: Exception) -> None:
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE trash_records SET status='failed', failure=? "
                "WHERE id=? AND status='pending'",
                (str(failure)[:1000], trash_id),
            )

    def _mark_restore_failure(self, trash_id: str, failure: Exception) -> None:
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE trash_records SET failure=? WHERE id=? AND status='active'",
                (str(failure)[:1000], trash_id),
            )

    def _mark_request_failed(self, request_id: str, failure: Exception) -> None:
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE lifecycle_requests SET state='failed', failure=?, finished_at=? "
                "WHERE id=? AND state='pending'",
                (str(failure)[:1000], self._now(), request_id),
            )

    @staticmethod
    @staticmethod
    def _raise_with_compensation(
        original: Exception, compensation_errors: list[Exception]
    ) -> NoReturn:
        if compensation_errors:
            message = "; ".join(str(error) for error in compensation_errors)
            raise RuntimeError(f"{original}; compensation failed: {message}") from original
        raise original

    def _compensate_moves(self, moves: list[tuple[Path, Path]]) -> list[Exception]:
        """Best-effort reverse only the exact files that already moved."""

        failures: list[Exception] = []
        for original, moved in reversed(moves):
            try:
                self._move_exact(moved, original)
            except Exception as exc:  # Keep attempting each independent exact file.
                failures.append(exc)
        return failures

    @staticmethod
    def _move_exact(source: Path, destination: Path) -> None:
        """Move exactly one already validated regular file, even across mounts."""

        if destination.exists() or destination.is_symlink():
            raise ValueError("destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source, destination)
        except OSError:
            # ``copy2`` operates on the one resolved file; it does not follow a
            # directory tree, glob, or accept a user-supplied filesystem path.
            shutil.copy2(source, destination, follow_symlinks=False)
            source.unlink()

    @staticmethod
    def _trash_filename(kind: str, path: Path) -> str:
        return f"{kind}{path.suffix.lower()}"

    def _trash_path(self, relative_path: str) -> Path:
        relative = validate_relative_path(relative_path)
        candidate = (self.trash_root / relative).resolve(strict=False)
        try:
            candidate.relative_to(self.trash_root)
        except ValueError as exc:
            raise ValueError("trash record escapes the Worker trash root") from exc
        return candidate

    def _upload_staging_root(self) -> Path:
        return self.resolver.roots.get(RootKey.TEMP, self.trash_root) / "uploads"

    def _upload_staging_path(self, value: str) -> Path:
        """Validate a persisted staging path stays under the upload staging root."""
        staging_root = self._upload_staging_root().resolve(strict=False)
        candidate = Path(value).resolve(strict=False)
        try:
            candidate.relative_to(staging_root)
        except ValueError as exc:
            raise ValueError("upload staging path escapes the Worker temp root") from exc
        return candidate

    def _clip_destination(self, collection_id: str, name: str) -> Path:
        collection_root = self._collection_source_root(collection_id)
        destination = collection_root / name
        return self.resolver.resolve(
            RootKey.SOURCE.value,
            destination.relative_to(self.resolver.roots[RootKey.SOURCE]).as_posix(),
        )

    def _publish_clip(self, collection_id: str, name: str, staging: Path) -> ClipRecord:
        """Atomically publish a staged clip file and catalog it, exactly as imports do."""
        collection = self.db.connection.execute(
            "SELECT compiled_output_prefix FROM collections WHERE id=?", (collection_id,)
        ).fetchone()
        if collection is None:
            raise KeyError(collection_id)
        destination = self._clip_destination(collection_id, name)
        if destination.exists() or destination.is_symlink():
            raise ValueError("destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._move_exact(staging, destination)
        clip_id = str(uuid.uuid4())
        relative_source = destination.relative_to(self.resolver.roots[RootKey.SOURCE]).as_posix()
        extension = Path(name).suffix.lower()
        relative_output = f"{collection['compiled_output_prefix']}/{clip_id}{extension}"
        now = self._now()
        with self.db.connection:
            self.db.connection.execute(
                "INSERT INTO clips("
                "id,collection_id,state,relative_source_path,relative_output_path,"
                "duration_seconds,output_available,metadata,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    clip_id,
                    collection_id,
                    ClipState.DISCOVERED.value,
                    relative_source,
                    relative_output,
                    0.0,
                    0,
                    "{}",
                    now,
                ),
            )
        self._audit("library.imported", clip_id, {"collection_id": collection_id, "filename": name})
        return self._clip_from_row(self._clip_row(clip_id))

    def _publish_asset(self, name: str, staging: Path) -> AuditEvent:
        """Atomically publish a staged asset file, exactly as asset imports do."""
        destination = self.resolver.resolve(RootKey.ASSETS.value, name)
        if destination.exists() or destination.is_symlink():
            raise ValueError("destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._move_exact(staging, destination)
        return self._audit("library.asset_imported", name, {"filename": name})

    def _stage_upload_file(self, upload: UploadFile) -> Path:
        """Receive one upload into the Worker temp root before any publishing."""
        staging_root = self._upload_staging_root()
        staging = staging_root / f"{uuid.uuid4().hex}.uploading"
        written = 0
        try:
            staging.parent.mkdir(parents=True, exist_ok=True)
            with staging.open("xb") as handle:
                while block := upload.file.read(1024 * 1024):
                    written += len(block)
                    if written > self.max_upload_bytes:
                        raise ValueError("upload exceeds configured size limit")
                    handle.write(block)
        except Exception:
            if staging.exists():
                staging.unlink()
            raise
        return staging

    def import_clip(self, collection_id: str, upload: UploadFile) -> ClipRecord:
        """Store an allowed upload below its collection's configured source directory."""

        name = validate_filename(upload.filename)
        if Path(name).suffix.lower() not in _VIDEO_EXTENSIONS:
            raise ValueError("unsupported upload extension")
        self._collection_source_root(collection_id)
        destination = self._clip_destination(collection_id, name)
        if destination.exists() or destination.is_symlink():
            raise ValueError("destination already exists")
        # Receive into the Worker temp root first.  A rejected upload must not
        # create a partially imported file (or collection subdirectory).
        staging = self._stage_upload_file(upload)
        try:
            return self._publish_clip(collection_id, name, staging)
        except Exception:
            if staging.exists():
                staging.unlink()
            raise

    def import_asset(self, upload: UploadFile) -> AuditEvent:
        """Store one intro/outro asset file flat in the Worker assets root."""

        name = validate_filename(upload.filename)
        if Path(name).suffix.lower() not in _VIDEO_EXTENSIONS:
            raise ValueError("unsupported upload extension")
        destination = self.resolver.resolve(RootKey.ASSETS.value, name)
        if destination.exists() or destination.is_symlink():
            raise ValueError("destination already exists")
        # Stage in the Worker temp root first, like clip uploads, so a rejected
        # upload never leaves a partially imported asset behind.
        staging = self._stage_upload_file(upload)
        try:
            return self._publish_asset(name, staging)
        except Exception:
            if staging.exists():
                staging.unlink()
            raise

    def begin_upload(
        self, kind: UploadKind | str, filename: str, collection_id: str | None = None
    ) -> str:
        """Validate and open a tracked chunked upload before any bytes arrive."""

        try:
            kind = UploadKind(kind)
        except ValueError as exc:
            raise ValueError("upload kind must be clip or asset") from exc
        name = validate_filename(filename)
        if Path(name).suffix.lower() not in _VIDEO_EXTENSIONS:
            raise ValueError("unsupported upload extension")
        if kind is UploadKind.CLIP:
            if collection_id is None:
                raise ValueError("clip uploads require a collection")
            self._collection_source_root(collection_id)
        upload_id = uuid.uuid4().hex
        staging_root = self._upload_staging_root()
        staging_root.mkdir(parents=True, exist_ok=True)
        staging = staging_root / f"{upload_id}.uploading"
        now = self._now()
        try:
            with staging.open("xb"):
                pass
            with self.db.connection:
                self.db.connection.execute(
                    "INSERT INTO upload_sessions("
                    "id,kind,filename,collection_id,staging_path,bytes_received,"
                    "created_at,updated_at"
                    ") VALUES(?,?,?,?,?,0,?,?)",
                    (upload_id, kind.value, name, collection_id, str(staging), now, now),
                )
        except Exception:
            if staging.exists():
                staging.unlink()
            raise
        return upload_id

    def append_chunk(self, upload_id: str, data: bytes) -> int:
        """Append one chunk to a tracked upload, enforcing the accumulated cap."""

        row = self.db.connection.execute(
            "SELECT * FROM upload_sessions WHERE id=?", (upload_id,)
        ).fetchone()
        if row is None:
            raise KeyError(upload_id)
        total = int(row["bytes_received"]) + len(data)
        if total > self.max_upload_bytes:
            raise ValueError("upload exceeds configured size limit")
        staging = self._upload_staging_path(str(row["staging_path"]))
        with staging.open("ab") as handle:
            handle.write(data)
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE upload_sessions SET bytes_received=?, updated_at=? WHERE id=?",
                (total, self._now(), upload_id),
            )
        return total

    def finish_upload(self, upload_id: str) -> ClipRecord | AuditEvent:
        """Publish a fully received staged upload exactly like a direct import."""

        row = self.db.connection.execute(
            "SELECT * FROM upload_sessions WHERE id=?", (upload_id,)
        ).fetchone()
        if row is None:
            raise KeyError(upload_id)
        staging = self._upload_staging_path(str(row["staging_path"]))
        self._require_regular_file(staging)
        if str(row["kind"]) == UploadKind.CLIP.value:
            result = self._publish_clip(str(row["collection_id"]), str(row["filename"]), staging)
        else:
            result = self._publish_asset(str(row["filename"]), staging)
        with self.db.connection:
            self.db.connection.execute("DELETE FROM upload_sessions WHERE id=?", (upload_id,))
        return result

    def abort_upload(self, upload_id: str) -> None:
        """Discard a tracked upload's staging file and tracking state."""

        row = self.db.connection.execute(
            "SELECT staging_path FROM upload_sessions WHERE id=?", (upload_id,)
        ).fetchone()
        if row is None:
            raise KeyError(upload_id)
        staging = self._upload_staging_path(str(row["staging_path"]))
        with self.db.connection:
            self.db.connection.execute("DELETE FROM upload_sessions WHERE id=?", (upload_id,))
        if staging.exists():
            staging.unlink()

    def cleanup_abandoned_uploads(self, max_age_seconds: float = _UPLOAD_MAX_AGE_SECONDS) -> int:
        """Remove only tracked upload staging files past the inactivity bound."""

        cutoff = (datetime.now(UTC) - timedelta(seconds=max_age_seconds)).isoformat()
        rows = self.db.connection.execute(
            "SELECT id, staging_path FROM upload_sessions WHERE updated_at < ?", (cutoff,)
        ).fetchall()
        removed = 0
        for row in rows:
            try:
                staging = self._upload_staging_path(str(row["staging_path"]))
            except ValueError:
                # A row whose file cannot be proven Worker-owned is left alone.
                continue
            with self.db.connection:
                self.db.connection.execute("DELETE FROM upload_sessions WHERE id=?", (row["id"],))
            if staging.exists():
                staging.unlink()
            removed += 1
        return removed

    def list_assets(self) -> list[str]:
        """List filenames currently present directly under the assets root."""

        root = self.resolver.roots.get(RootKey.ASSETS)
        if root is None or not root.is_dir():
            return []
        return sorted(
            path.name for path in root.iterdir() if path.is_file() and not path.is_symlink()
        )

    def _profiles_referencing_asset(self, filename: str) -> list[str]:
        """Return the IDs of profiles whose intro/outro reference this asset."""
        referencing: list[str] = []
        rows = self.db.connection.execute("SELECT id, settings FROM profiles").fetchall()
        for row in rows:
            settings = json.loads(row["settings"] or "{}")
            if (
                settings.get("intro_reference") == filename
                or settings.get("outro_reference") == filename
            ):
                referencing.append(str(row["id"]))
        return referencing

    def delete_asset(self, filename: str) -> AuditEvent:
        """Remove one stored asset unless a stored profile still references it."""
        name = validate_filename(filename)
        destination = self.resolver.resolve(RootKey.ASSETS.value, name)
        if destination.is_symlink() or not destination.is_file():
            raise ValueError("asset file does not exist")
        referencing = self._profiles_referencing_asset(name)
        if referencing:
            if len(referencing) == 1:
                message = f"asset is still referenced by profile '{referencing[0]}'"
            else:
                message = "asset is still referenced by profiles: " + ", ".join(
                    f"'{profile_id}'" for profile_id in sorted(referencing)
                )
            raise ValueError(message)
        destination.unlink()
        return self._audit("library.asset_deleted", name, {"filename": name})

    def create_collection_directory(self, collection_id: str, relative_path: str) -> AuditEvent:
        """Create one approved subdirectory inside a configured collection root."""

        relative = validate_relative_path(relative_path)
        collection_root = self._collection_source_root(collection_id)
        collection_relative = collection_root.relative_to(self.resolver.roots[RootKey.SOURCE])
        destination = self.resolver.resolve(
            RootKey.SOURCE.value, (collection_relative / relative).as_posix()
        )
        if destination.exists() or destination.is_symlink():
            raise ValueError("destination already exists")
        destination.mkdir(parents=True, exist_ok=False)
        try:
            return self._audit(
                "library.directory_created",
                collection_id,
                {"relative_path": relative},
            )
        except Exception:
            with suppress(OSError):
                destination.rmdir()
            raise

    def rename_or_move(self, clip_id: str | UUID, destination_relative_path: str) -> ClipRecord:
        """Move a source clip within its configured collection while retaining its UUID."""

        row = self._clip_row(clip_id)
        relative = validate_relative_path(destination_relative_path)
        destination = self.resolver.resolve(RootKey.SOURCE.value, relative)
        collection_root = self._collection_source_root(str(row["collection_id"]))
        try:
            destination.relative_to(collection_root)
        except ValueError as exc:
            raise ValueError(
                "destination must remain within the collection source directory"
            ) from exc
        if destination.suffix.lower() not in _VIDEO_EXTENSIONS:
            raise ValueError("unsupported destination extension")
        source = self._source_path(row)
        self._move_exact(source, destination)
        now = self._now()
        state = ClipState.STALE.value if row["output_available"] else ClipState.DISCOVERED.value
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE clips SET relative_source_path=?, state=?, updated_at=? WHERE id=?",
                (relative, state, now, str(clip_id)),
            )
        self._audit("library.moved", str(clip_id), {"relative_source_path": relative})
        return self._clip_from_row(self._clip_row(clip_id))

    move_clip = rename_or_move

    def update_tags_and_notes(
        self, clip_id: str | UUID, tags: list[str], notes: str | None
    ) -> ClipRecord:
        row = self._clip_row(clip_id)
        if any(not tag.strip() for tag in tags):
            raise ValueError("tags must be non-empty strings")
        metadata = json.loads(row["metadata"] or "{}")
        metadata["tags"] = list(dict.fromkeys(tag.strip() for tag in tags))
        metadata["notes"] = notes
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE clips SET metadata=?, updated_at=? WHERE id=?",
                (json.dumps(metadata, sort_keys=True), self._now(), str(clip_id)),
            )
        self._audit("library.metadata_updated", str(clip_id), {"tags": metadata["tags"]})
        return self._clip_from_row(self._clip_row(clip_id))

    def request_scan(self, clip_id: str | UUID) -> AuditEvent:
        row = self._clip_row(clip_id)
        job_id = str(uuid.uuid4())
        job = self.queue.enqueue(
            JobRecord(
                id=job_id,
                kind="scan",
                collection_id=str(row["collection_id"]),
                clip_id=job_id,
                source_relative_path=f"scan/{job_id}.request",
                output_relative_path=f"scan/{job_id}.result",
                source_fingerprint="library-request",
                profile_fingerprint="library-request",
                profile_settings={"collection_ids": [str(row["collection_id"])]},
                duration_seconds=0,
                progress=JobProgress(stage=JobStage.QUEUED, percent=0, eta_seconds=None),
            )
        )
        return self._audit(
            "library.scan_requested",
            str(clip_id),
            {"collection_id": row["collection_id"], "job_id": job.id},
        )

    def request_library_scan(self, collection_id: str | None = None) -> AuditEvent:
        """Discover new/changed/removed source files without an existing clip record.

        Scans every configured collection's source directory when
        collection_id is None, or only the named collection otherwise. This
        lets the Library Manager catalog files already placed under an
        allowlisted source root (e.g. copied there directly, or via Samba/
        the Home Assistant Files add-on) without requiring an HTTP upload.
        """
        job_id = str(uuid.uuid4())
        payload: dict[str, Any] = {"collection_ids": [collection_id]} if collection_id else {}
        job = self.queue.enqueue(
            JobRecord(
                id=job_id,
                kind="scan",
                collection_id=collection_id or "system",
                clip_id=job_id,
                source_relative_path=f"scan/{job_id}.request",
                output_relative_path=f"scan/{job_id}.result",
                source_fingerprint="library-request",
                profile_fingerprint="library-request",
                profile_settings=payload,
                duration_seconds=0,
                progress=JobProgress(stage=JobStage.QUEUED, percent=0, eta_seconds=None),
            )
        )
        return self._audit(
            "library.scan_requested",
            collection_id or "system",
            {"collection_id": collection_id, "job_id": job.id},
        )

    def request_recompile(self, clip_id: str | UUID) -> AuditEvent:
        row = self._clip_row(clip_id)
        if row["state"] != ClipState.DELETED.value:
            with self.db.connection:
                self.db.connection.execute(
                    "UPDATE clips SET state=?, updated_at=? WHERE id=?",
                    (ClipState.STALE.value, self._now(), str(clip_id)),
                )
        details: dict[str, Any] = {"collection_id": row["collection_id"]}
        if self.jobs is not None:
            jobs = self.jobs.enqueue_compile(
                CompileRequest(collection_id=str(row["collection_id"]), clip_ids=[str(clip_id)])
            )
            details["job_ids"] = [job.id for job in jobs]
        return self._audit("library.recompile_requested", str(clip_id), details)

    def move_to_trash(self, clip_id: str, target: TrashTarget) -> AuditEvent:
        target = TrashTarget(target)
        row = self._clip_row(clip_id)
        source_selected, output_selected = self._targets(target)
        source = self._source_path(row) if source_selected else None
        output = self._output_path(row) if output_selected else None
        trash_id = str(uuid.uuid4())
        trash_dir = self.trash_root / trash_id
        trash_source = trash_dir / self._trash_filename("source", source) if source else None
        trash_output = trash_dir / self._trash_filename("output", output) if output else None
        now = self._now()
        with self.db.connection:
            self.db.connection.execute(
                "INSERT INTO trash_records("
                "id,clip_id,target,original_source_path,original_output_path,"
                "source_trash_path,output_trash_path,created_at,restored_at,status,failure"
                ") VALUES(?,?,?,?,?,?,?,?,NULL,'pending',NULL)",
                (
                    trash_id,
                    str(clip_id),
                    target.value,
                    row["relative_source_path"] if source else None,
                    row["relative_output_path"] if output else None,
                    trash_source.relative_to(self.trash_root).as_posix() if trash_source else None,
                    trash_output.relative_to(self.trash_root).as_posix() if trash_output else None,
                    now,
                ),
            )
        try:
            self._audit(
                "library.trash_pending",
                str(clip_id),
                {"trash_id": trash_id, "target": target.value},
            )
        except Exception as exc:
            self._mark_trash_failure(trash_id, exc)
            raise

        moves: list[tuple[Path, Path]] = []
        try:
            if source and trash_source:
                self._move_exact(source, trash_source)
                moves.append((source, trash_source))
            if output and trash_output:
                self._move_exact(output, trash_output)
                moves.append((output, trash_output))
            with self.db.connection:
                self.db.connection.execute(
                    "UPDATE trash_records SET status='active', failure=NULL "
                    "WHERE id=? AND status='pending'",
                    (trash_id,),
                )
                self.db.connection.execute(
                    "UPDATE clips SET state=?, output_available=?, updated_at=? WHERE id=?",
                    (
                        ClipState.DELETED.value if source else row["state"],
                        0 if output else int(bool(row["output_available"])),
                        self._now(),
                        str(clip_id),
                    ),
                )
                return self._insert_audit(
                    "library.trashed", str(clip_id), {"trash_id": trash_id, "target": target.value}
                )
        except Exception as exc:
            compensation_errors = self._compensate_moves(moves)
            self._mark_trash_failure(trash_id, exc)
            with suppress(Exception):
                self._audit(
                    "library.trash_failed",
                    str(clip_id),
                    {"trash_id": trash_id, "failure": str(exc)[:1000]},
                )
            self._raise_with_compensation(exc, compensation_errors)

    def list_trash(self) -> list[TrashRecord]:
        rows = self.db.connection.execute(
            "SELECT id,clip_id,target,created_at,restored_at FROM trash_records "
            "WHERE status='active' AND restored_at IS NULL ORDER BY created_at"
        ).fetchall()
        return [TrashRecord.model_validate(dict(row)) for row in rows]

    def purge_expired_trash(self) -> int:
        """Version one intentionally never performs automatic trash purges."""

        return 0

    def restore(self, trash_id: str) -> ClipRecord:
        record = self.db.connection.execute(
            "SELECT * FROM trash_records WHERE id=? AND status='active' AND restored_at IS NULL",
            (trash_id,),
        ).fetchone()
        if record is None:
            raise KeyError(trash_id)
        row = self._clip_row(str(record["clip_id"]))
        source = (
            self._trash_path(str(record["source_trash_path"]))
            if record["source_trash_path"]
            else None
        )
        output = (
            self._trash_path(str(record["output_trash_path"]))
            if record["output_trash_path"]
            else None
        )
        source_destination = (
            self.resolver.resolve(RootKey.SOURCE.value, str(record["original_source_path"]))
            if record["original_source_path"]
            else None
        )
        output_destination = (
            self.resolver.resolve(RootKey.COMPILED.value, str(record["original_output_path"]))
            if record["original_output_path"]
            else None
        )
        if source:
            self._require_regular_file(source)
            if source_destination is None:
                raise ValueError("invalid source trash record")
        if output:
            self._require_regular_file(output)
            if output_destination is None:
                raise ValueError("invalid output trash record")
        moves: list[tuple[Path, Path]] = []
        try:
            self._audit("library.restore_pending", str(row["id"]), {"trash_id": trash_id})
            if source and source_destination:
                self._move_exact(source, source_destination)
                moves.append((source, source_destination))
            if output and output_destination:
                self._move_exact(output, output_destination)
                moves.append((output, output_destination))
            now = self._now()
            state = (
                ClipState.READY.value
                if bool(row["output_available"]) or output
                else ClipState.DISCOVERED.value
            )
            with self.db.connection:
                self.db.connection.execute(
                    "UPDATE trash_records SET restored_at=?, status='restored', failure=NULL "
                    "WHERE id=?",
                    (now, trash_id),
                )
                self.db.connection.execute(
                    "UPDATE clips SET state=?, output_available=?, updated_at=? WHERE id=?",
                    (
                        state,
                        int(bool(row["output_available"]) or output is not None),
                        now,
                        row["id"],
                    ),
                )
                self._insert_audit("library.restored", str(row["id"]), {"trash_id": trash_id})
        except Exception as exc:
            compensation_errors = self._compensate_moves(moves)
            self._mark_restore_failure(trash_id, exc)
            with suppress(Exception):
                self._audit(
                    "library.restore_failed",
                    str(row["id"]),
                    {"trash_id": trash_id, "failure": str(exc)[:1000]},
                )
            self._raise_with_compensation(exc, compensation_errors)
        return self._clip_from_row(self._clip_row(str(row["id"])))

    @staticmethod
    def delete_confirmation_token(clip_id: str | UUID, target: DeleteTarget) -> str:
        target = DeleteTarget(target)
        return f"DELETE {clip_id} {target.value}"

    confirmation_token = delete_confirmation_token
    permanent_delete_confirmation_token = delete_confirmation_token

    def permanently_delete(
        self, clip_id: str, target: DeleteTarget, confirmation: str
    ) -> AuditEvent:
        target = DeleteTarget(target)
        expected = self.delete_confirmation_token(clip_id, target)
        if not hmac.compare_digest(confirmation, expected):
            raise ValueError("permanent delete confirmation does not match")
        row = self._clip_row(clip_id)
        source_selected, output_selected = self._targets(target)
        source = self._source_path(row) if source_selected else None
        output = self._output_path(row) if output_selected else None
        request_id = str(uuid.uuid4())
        details = {"target": target.value}
        with self.db.connection:
            self.db.connection.execute(
                "INSERT INTO lifecycle_requests("
                "id,operation,clip_id,target,state,details,failure,created_at,finished_at"
                ") VALUES(?,?,?,?,'pending',?,NULL,?,NULL)",
                (
                    request_id,
                    "permanent_delete",
                    str(clip_id),
                    target.value,
                    json.dumps(details, sort_keys=True),
                    self._now(),
                ),
            )
        try:
            self._audit(
                "library.permanent_delete.pending",
                str(clip_id),
                {"request_id": request_id, **details},
            )
        except Exception as exc:
            self._mark_request_failed(request_id, exc)
            raise
        staging_dir = self.trash_root / ".permanent-delete" / request_id
        staged_source = (
            staging_dir / self._trash_filename("source", source) if source is not None else None
        )
        staged_output = (
            staging_dir / self._trash_filename("output", output) if output is not None else None
        )
        moves: list[tuple[Path, Path]] = []
        try:
            # Stage every exact catalog target before any irreversible unlink. If
            # the second move fails, compensation restores the first target.
            if source is not None and staged_source is not None:
                self._move_exact(source, staged_source)
                moves.append((source, staged_source))
            if output is not None and staged_output is not None:
                self._move_exact(output, staged_output)
                moves.append((output, staged_output))
        except Exception as exc:
            compensation_errors = self._compensate_moves(moves)
            self._mark_request_failed(request_id, exc)
            with suppress(Exception):
                self._audit(
                    "library.permanent_delete.failed",
                    str(clip_id),
                    {"request_id": request_id, "failure": str(exc)[:1000]},
                )
            self._raise_with_compensation(exc, compensation_errors)

        now = self._now()
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE clips SET state=?, output_available=?, updated_at=? WHERE id=?",
                (
                    ClipState.DELETED.value if source_selected else row["state"],
                    0 if output_selected else int(bool(row["output_available"])),
                    now,
                    str(clip_id),
                ),
            )
            self.db.connection.execute(
                "UPDATE lifecycle_requests SET state='staged', finished_at=? "
                "WHERE id=? AND state='pending'",
                (now, request_id),
            )

        cleanup_errors: list[str] = []
        for staged in (staged_source, staged_output):
            if staged is None:
                continue
            try:
                staged.unlink()
            except OSError as exc:
                cleanup_errors.append(str(exc)[:500])
        with suppress(OSError):
            staging_dir.rmdir()
            staging_dir.parent.rmdir()
        final_state = "completed_with_residue" if cleanup_errors else "completed"
        final_details = {
            "request_id": request_id,
            **details,
            "cleanup_pending": bool(cleanup_errors),
        }
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE lifecycle_requests SET state=?, failure=?, finished_at=? WHERE id=?",
                (
                    final_state,
                    "; ".join(cleanup_errors) if cleanup_errors else None,
                    self._now(),
                    request_id,
                ),
            )
            return self._insert_audit("library.permanently_deleted", str(clip_id), final_details)
