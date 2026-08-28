"""Safe, Worker-owned lifecycle operations for catalogued library clips."""

from __future__ import annotations

import hmac
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .database import Database
from .jobs import CompileRequest, JobProgress, JobRecord, JobService, JobStage
from .models import ClipRecord, ClipState
from .paths import RootKey, SafePathResolver, validate_filename, validate_relative_path
from .queue import PersistentJobQueue

_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".ts"}


class TrashTarget(StrEnum):
    SOURCE = "source"
    OUTPUT = "output"
    BOTH = "both"


class DeleteTarget(StrEnum):
    SOURCE = "source"
    OUTPUT = "output"
    BOTH = "both"


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
        request_id = str(uuid.uuid4())
        occurred_at = self._now()
        summary = json.dumps(details, sort_keys=True, separators=(",", ":"))
        with self.db.connection:
            cursor = self.db.connection.execute(
                "INSERT INTO audit_events("
                "action_type,target_id,actor,request_id,summary,occurred_at"
                ") VALUES(?,?,?,?,?,?)",
                (action_type, target_id, self.actor, request_id, summary, occurred_at),
            )
        return AuditEvent(
            id=int(cursor.lastrowid),
            action_type=action_type,
            target_id=target_id,
            actor=self.actor,
            request_id=request_id,
            summary=summary,
            occurred_at=occurred_at,
            details=details,
        )

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

    def import_clip(self, collection_id: str, upload: UploadFile) -> ClipRecord:
        """Store an allowed upload below its collection's configured source directory."""

        name = validate_filename(upload.filename)
        extension = Path(name).suffix.lower()
        if extension not in _VIDEO_EXTENSIONS:
            raise ValueError("unsupported upload extension")
        collection = self.db.connection.execute(
            "SELECT compiled_output_prefix FROM collections WHERE id=?", (collection_id,)
        ).fetchone()
        if collection is None:
            raise KeyError(collection_id)
        collection_root = self._collection_source_root(collection_id)
        destination = collection_root / name
        destination = self.resolver.resolve(
            RootKey.SOURCE.value,
            destination.relative_to(self.resolver.roots[RootKey.SOURCE]).as_posix(),
        )
        if destination.exists() or destination.is_symlink():
            raise ValueError("destination already exists")
        # Receive into the Worker temp root first.  A rejected upload must not
        # create a partially imported file (or collection subdirectory).
        staging_root = self.resolver.roots.get(RootKey.TEMP, self.trash_root) / "uploads"
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
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._move_exact(staging, destination)
        except Exception:
            if staging.exists():
                staging.unlink()
            raise
        clip_id = str(uuid.uuid4())
        relative_source = destination.relative_to(self.resolver.roots[RootKey.SOURCE]).as_posix()
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
        if any(not isinstance(tag, str) or not tag.strip() for tag in tags):
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
        if source and trash_source:
            self._move_exact(source, trash_source)
        if output and trash_output:
            self._move_exact(output, trash_output)
        now = self._now()
        with self.db.connection:
            self.db.connection.execute(
                "INSERT INTO trash_records("
                "id,clip_id,target,original_source_path,original_output_path,"
                "source_trash_path,output_trash_path,created_at,restored_at"
                ") VALUES(?,?,?,?,?,?,?,?,NULL)",
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
            self.db.connection.execute(
                "UPDATE clips SET state=?, output_available=?, updated_at=? WHERE id=?",
                (
                    ClipState.DELETED.value if source else row["state"],
                    0 if output else int(bool(row["output_available"])),
                    now,
                    str(clip_id),
                ),
            )
        return self._audit(
            "library.trashed", str(clip_id), {"trash_id": trash_id, "target": target.value}
        )

    def list_trash(self) -> list[TrashRecord]:
        rows = self.db.connection.execute(
            "SELECT id,clip_id,target,created_at,restored_at FROM trash_records "
            "WHERE restored_at IS NULL ORDER BY created_at"
        ).fetchall()
        return [TrashRecord.model_validate(dict(row)) for row in rows]

    def purge_expired_trash(self) -> int:
        """Version one intentionally never performs automatic trash purges."""

        return 0

    def restore(self, trash_id: str) -> ClipRecord:
        record = self.db.connection.execute(
            "SELECT * FROM trash_records WHERE id=? AND restored_at IS NULL", (trash_id,)
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
        if source and source_destination:
            self._move_exact(source, source_destination)
        if output and output_destination:
            self._move_exact(output, output_destination)
        now = self._now()
        state = (
            ClipState.READY.value
            if bool(row["output_available"]) or output
            else ClipState.DISCOVERED.value
        )
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE trash_records SET restored_at=? WHERE id=?", (now, trash_id)
            )
            self.db.connection.execute(
                "UPDATE clips SET state=?, output_available=?, updated_at=? WHERE id=?",
                (state, int(bool(row["output_available"]) or output is not None), now, row["id"]),
            )
        self._audit("library.restored", str(row["id"]), {"trash_id": trash_id})
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
        # Each unlink is an exact resolved file selected from this catalog row.
        if source is not None:
            source.unlink()
        if output is not None:
            output.unlink()
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
        return self._audit("library.permanently_deleted", str(clip_id), {"target": target.value})
