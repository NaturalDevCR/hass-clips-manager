"""Compilation job models, orchestration, and bounded FFmpeg process execution."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

if TYPE_CHECKING:
    from .library_manager import LibraryManager

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .catalog import CatalogService
from .database import Database
from .ffmpeg import FfmpegCommandBuilder
from .paths import RootKey, SafePathResolver, validate_collection_id, validate_relative_path
from .probe import MediaProbeResult, ProbeClient
from .profile_validation import ProcessingProfile, profile_fingerprint, validate_profile
from .queue import PersistentJobQueue
from .sanitization import sanitize_message


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(StrEnum):
    QUEUED = "queued"
    ENCODING = "encoding"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"


class JobProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: JobStage = JobStage.QUEUED
    percent: float = Field(default=0, ge=0, le=100)
    eta_seconds: float | None = Field(default=None, ge=0)


class CompileRequest(BaseModel):
    """A scoped request to queue eligible clips from one collection."""

    model_config = ConfigDict(extra="forbid")

    collection_id: str
    clip_ids: list[str] | None = None
    strategy: Literal["scan_and_compile_changed_or_missing", "compile_stale_only", "scan_only"] = (
        "scan_and_compile_changed_or_missing"
    )
    skip_if_processing: bool = True
    max_attempts: int = Field(default=3, ge=1, le=10)

    @field_validator("collection_id")
    @classmethod
    def _collection_id(cls, value: str) -> str:
        return validate_collection_id(value)


class JobRecord(BaseModel):
    """A durable immutable work snapshot plus mutable execution state."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str = "compile"
    state: JobState = JobState.QUEUED
    progress: JobProgress = Field(default_factory=JobProgress)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    collection_id: str
    clip_id: str
    source_relative_path: str
    output_relative_path: str
    source_fingerprint: str
    profile_fingerprint: str
    fingerprint: str = ""
    profile_settings: dict[str, Any]
    duration_seconds: float = Field(ge=0)
    intro_duration_seconds: float = Field(default=0, ge=0)
    outro_duration_seconds: float = Field(default=0, ge=0)
    has_audio: bool = True
    intro_has_audio: bool = True
    outro_has_audio: bool = True
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    cancel_requested: bool = False
    logs: list[str] = Field(default_factory=list)
    source_path: Path | None = None
    temporary_output_path: Path | None = None
    intro_path: Path | None = None
    outro_path: Path | None = None

    @field_validator("collection_id")
    @classmethod
    def _valid_collection_id(cls, value: str) -> str:
        return validate_collection_id(value)

    @field_validator("source_relative_path", "output_relative_path")
    @classmethod
    def _valid_relative_path(cls, value: str) -> str:
        return validate_relative_path(value)

    def model_post_init(self, __context: Any) -> None:
        if not self.fingerprint:
            material = "\x00".join(
                (self.clip_id, self.source_fingerprint, self.profile_fingerprint)
            ).encode()
            object.__setattr__(self, "fingerprint", hashlib.sha256(material).hexdigest())


class JobRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: JobRecord
    retry_scheduled: bool = False


class _Probe(Protocol):
    def probe(self, path: Path) -> MediaProbeResult: ...


def _now() -> datetime:
    return datetime.now(UTC)


class JobService:
    """Creates durable compilation snapshots and handles cancellation requests."""

    def __init__(
        self,
        db: Database,
        resolver: SafePathResolver,
        *,
        disk_reserve_bytes: int = 1_073_741_824,
        queue: PersistentJobQueue | None = None,
        catalog: CatalogService | None = None,
    ) -> None:
        self.db = db
        self.resolver = resolver
        self.disk_reserve_bytes = disk_reserve_bytes
        self.queue = queue or PersistentJobQueue(db)
        self.catalog = catalog

    @staticmethod
    def available_disk_bytes(path: Path) -> int:
        return shutil.disk_usage(path).free

    @staticmethod
    def _file_fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _profile(self, collection_id: str) -> tuple[dict[str, Any], str]:
        row = self.db.connection.execute(
            "SELECT p.settings FROM collections c JOIN profiles p ON p.id=c.processing_profile_id WHERE c.id=? AND c.enabled=1",
            (collection_id,),
        ).fetchone()
        if row is None:
            raise KeyError(collection_id)
        settings = json.loads(row["settings"])
        profile = validate_profile(ProcessingProfile.model_validate(settings))
        intro = (
            self._file_fingerprint(
                self.resolver.resolve(RootKey.ASSETS.value, profile.intro_reference)
            )
            if profile.intro_reference
            else None
        )
        outro = (
            intro
            if profile.outro_reference == profile.intro_reference
            else (
                self._file_fingerprint(
                    self.resolver.resolve(RootKey.ASSETS.value, profile.outro_reference)
                )
                if profile.outro_reference
                else None
            )
        )
        return profile.model_dump(mode="json"), profile_fingerprint(
            profile, {"intro_fingerprint": intro, "outro_fingerprint": outro}
        )

    def _eligible_clips(self, request: CompileRequest) -> list[Any]:
        eligible_states = (
            ("stale", "failed")
            if request.strategy == "compile_stale_only"
            else ("discovered", "stale", "failed")
        )
        if not request.skip_if_processing:
            eligible_states = (*eligible_states, "pending", "compiling")
        placeholders = ",".join("?" for _ in eligible_states)
        sql = f"SELECT * FROM clips WHERE collection_id=? AND state IN ({placeholders})"
        args: list[Any] = [request.collection_id, *eligible_states]
        if request.clip_ids is not None:
            if not request.clip_ids:
                return []
            placeholders = ",".join("?" for _ in request.clip_ids)
            sql += f" AND id IN ({placeholders})"
            args.extend(request.clip_ids)
        return self.db.connection.execute(sql, args).fetchall()

    def enqueue_compile(self, request: CompileRequest) -> list[JobRecord]:
        if request.strategy == "scan_only":
            job_id = str(uuid.uuid4())
            scan_job = JobRecord(
                id=job_id,
                kind="scan",
                collection_id="system",
                clip_id=job_id,
                source_relative_path=f"scan/{job_id}.request",
                output_relative_path=f"scan/{job_id}.result",
                source_fingerprint="compile-request",
                profile_fingerprint="compile-request",
                profile_settings={"collection_ids": [request.collection_id]},
                duration_seconds=0,
            )
            return [self.queue.enqueue(scan_job)]
        active = self.db.connection.execute(
            "SELECT id FROM jobs WHERE kind='compile' AND state IN ('queued','running') "
            "ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        if active is not None and request.skip_if_processing:
            return [self.queue.get(str(active["id"]))]
        if request.strategy == "scan_and_compile_changed_or_missing" and self.catalog is not None:
            self.catalog.scan({request.collection_id})
        profile_settings, current_profile_fingerprint = self._profile(request.collection_id)
        compiled_root = self.resolver.roots[RootKey.COMPILED]
        try:
            available = self.available_disk_bytes(compiled_root)
        except OSError as exc:
            raise ValueError("disk space cannot be checked") from exc
        jobs: list[JobRecord] = []
        remaining_bytes = available
        for clip in self._eligible_clips(request):
            metadata = json.loads(clip["metadata"] or "{}")
            source_fingerprint = str(metadata.get("source_fingerprint", ""))
            # The profile snapshot is authoritative here.  Catalog metadata is
            # historical and may deliberately lag a configuration update.
            profile_fingerprint_value = current_profile_fingerprint
            estimated_bytes = int(metadata.get("size_bytes") or 0)
            source_relative = str(clip["relative_source_path"])
            profile = ProcessingProfile.model_validate(profile_settings)
            output_relative = str(
                clip["relative_output_path"]
                or f"{request.collection_id}/{clip['id']}.{profile.output.extension}"
            )
            # Resolve once at enqueue time so traversal and out-of-root symlinks
            # cannot be persisted as executable work.
            self.resolver.resolve(RootKey.SOURCE.value, source_relative)
            self.resolver.resolve(RootKey.COMPILED.value, output_relative)
            job = JobRecord(
                id=str(uuid.uuid4()),
                collection_id=request.collection_id,
                clip_id=str(clip["id"]),
                source_relative_path=source_relative,
                output_relative_path=output_relative,
                source_fingerprint=source_fingerprint,
                profile_fingerprint=profile_fingerprint_value,
                profile_settings=profile_settings,
                duration_seconds=float(clip["duration_seconds"]),
                has_audio=bool(metadata.get("has_audio", True)),
                max_attempts=request.max_attempts,
            )
            output = self.resolver.resolve(RootKey.COMPILED.value, output_relative)
            if (
                bool(clip["output_available"])
                and output.is_file()
                and self.queue.has_succeeded(job.fingerprint)
            ):
                continue
            if remaining_bytes - estimated_bytes < self.disk_reserve_bytes:
                raise ValueError("insufficient disk space for compilation")
            remaining_bytes -= estimated_bytes
            jobs.append(job)
        return self.queue.enqueue_many(jobs)

    def cancel(self, job_id: str) -> JobRecord:
        return self.queue.request_cancel(job_id)


class JobWorker:
    """Runs one claimed job with timeout/cancellation and atomic publication."""

    def __init__(
        self,
        db: Database,
        resolver: SafePathResolver,
        *,
        command_builder: FfmpegCommandBuilder | None = None,
        probe_client: _Probe | None = None,
        process_factory: Callable[..., Any] = subprocess.Popen,
        queue: PersistentJobQueue | None = None,
        catalog: CatalogService | None = None,
        library_manager: LibraryManager | None = None,
    ) -> None:
        self.db = db
        self.resolver = resolver
        self.command_builder = command_builder or FfmpegCommandBuilder()
        self.probe_client = probe_client or ProbeClient()
        self.process_factory = process_factory
        self.queue = queue or PersistentJobQueue(db)
        self.catalog = catalog or CatalogService(db, resolver)
        self.library_manager = library_manager

    def claim_next(self) -> JobRecord | None:
        return self.queue.claim_next()

    def _runtime_job(self, job: JobRecord, temp_dir: Path) -> JobRecord:
        profile = validate_profile(ProcessingProfile.model_validate(job.profile_settings))
        extension = profile.output.extension
        source = self.resolver.resolve(RootKey.SOURCE.value, job.source_relative_path)
        temporary_output = temp_dir / f"output.{extension}"
        intro = (
            self.resolver.resolve(RootKey.ASSETS.value, profile.intro_reference)
            if profile.intro_reference
            else None
        )
        outro = (
            self.resolver.resolve(RootKey.ASSETS.value, profile.outro_reference)
            if profile.outro_reference
            else None
        )

        def asset_probe(path: Path | None) -> tuple[float, bool]:
            if path is None:
                return 0, True
            result = self.probe_client.probe(path)
            if not result.valid:
                raise ValueError("referenced asset could not be probed")
            if profile.audio.missing_policy.mode == "required" and not result.has_audio:
                raise ValueError("referenced asset audio is required by this processing profile")
            return result.duration_seconds, result.has_audio

        intro_duration, intro_has_audio = asset_probe(intro)
        outro_duration, outro_has_audio = (
            (intro_duration, intro_has_audio)
            if outro is not None and intro is not None and outro == intro
            else asset_probe(outro)
        )

        return job.model_copy(
            update={
                "source_path": source,
                "temporary_output_path": temporary_output,
                "intro_path": intro,
                "outro_path": outro,
                "intro_duration_seconds": intro_duration,
                "outro_duration_seconds": outro_duration,
                "intro_has_audio": intro_has_audio,
                "outro_has_audio": outro_has_audio,
            }
        )

    @staticmethod
    def _parse_progress(raw: str | bytes | None, duration_seconds: float) -> JobProgress | None:
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        out_time_us: int | None = None
        ended = False
        for line in raw.splitlines():
            key, separator, value = line.partition("=")
            if not separator:
                continue
            if key in {"out_time_us", "out_time_ms"}:
                try:
                    out_time_us = int(value)
                except ValueError:
                    continue
            elif key == "progress" and value == "end":
                ended = True
        if ended:
            return JobProgress(stage=JobStage.ENCODING, percent=100, eta_seconds=0)
        if out_time_us is None or duration_seconds <= 0:
            return None
        # FFmpeg's historical out_time_ms key is microseconds despite its name.
        elapsed = out_time_us / 1_000_000
        percent = min(99.0, max(0.0, elapsed / duration_seconds * 100))
        eta = max(0.0, duration_seconds - elapsed)
        return JobProgress(stage=JobStage.ENCODING, percent=percent, eta_seconds=eta)

    @staticmethod
    def _parse_loudnorm(raw: str) -> dict[str, float] | None:
        start, end = raw.rfind("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(raw[start : end + 1])
            keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
            measured = {key: float(payload[key]) for key in keys}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return measured

    def _cancelled(self, job_id: str) -> bool:
        try:
            return self.queue.get(job_id).cancel_requested
        except KeyError:
            return True

    @staticmethod
    def _signal_process_group(process: Any, sig: signal.Signals) -> None:
        pid = getattr(process, "pid", None)
        if isinstance(pid, int) and pid > 0:
            try:
                os.killpg(pid, sig)
                return
            except (OSError, ProcessLookupError):
                pass
        method = getattr(process, "terminate" if sig is signal.SIGTERM else "kill", None)
        if callable(method):
            method()

    def _stop_process(self, process: Any) -> str:
        """TERM, wait, escalate to KILL, and drain bounded process output."""

        self._signal_process_group(process, signal.SIGTERM)
        captured = ""
        try:
            stdout, stderr = process.communicate(timeout=2.0)
            captured = (stdout or "") + "\n" + (stderr or "")
            return captured[-16_000:]
        except (subprocess.TimeoutExpired, TimeoutError):
            self._signal_process_group(process, signal.SIGKILL)
        try:
            stdout, stderr = process.communicate(timeout=2.0)
            captured = (stdout or "") + "\n" + (stderr or "")
        except (subprocess.TimeoutExpired, TimeoutError):
            pass
        return captured[-16_000:]

    def _run_process(
        self, job: JobRecord, command: list[str], timeout_seconds: float
    ) -> tuple[bool, bool, str]:
        """Return success, cancelled, bounded decoded process output."""

        process = self.process_factory(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout_seconds
        captured = ""
        while True:
            if self._cancelled(job.id):
                captured += self._stop_process(process)
                return False, True, captured[-16_000:]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                captured += self._stop_process(process)
                return False, False, (captured + "\nFFmpeg timed out")[-16_000:]
            try:
                stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                captured = (stdout or "") + "\n" + (stderr or "")
                progress = self._parse_progress(stdout, job.duration_seconds)
                if progress is not None:
                    self.queue.update(job.model_copy(update={"progress": progress}))
                return getattr(process, "returncode", 1) == 0, False, captured[-16_000:]
            except subprocess.TimeoutExpired as exc:
                partial = getattr(exc, "output", None)
                if partial:
                    captured += (
                        partial.decode(errors="replace")
                        if isinstance(partial, bytes)
                        else str(partial)
                    )
                    progress = self._parse_progress(partial, job.duration_seconds)
                    if progress is not None:
                        self.queue.update(job.model_copy(update={"progress": progress}))
            except TimeoutError:
                # Small process fakes may use the standard-library timeout type.
                continue

    def _cleanup(self, temp_dir: Path) -> None:
        root = self.resolver.roots[RootKey.TEMP].resolve(strict=False)
        candidate = temp_dir.resolve(strict=False)
        try:
            candidate.relative_to(root)
            uuid.UUID(candidate.name)
        except (ValueError, AttributeError) as exc:
            raise ValueError("temporary job directory is not a root-contained UUID") from exc
        if candidate.parent != root:
            raise ValueError("temporary job directory is not directly under the temp root")
        if candidate.exists():
            shutil.rmtree(candidate)

    def _temporary_directory(self, job_id: str) -> Path:
        try:
            uuid.UUID(job_id)
        except ValueError as exc:
            raise ValueError("job ID must be a UUID for temporary work") from exc
        return self.resolver.roots[RootKey.TEMP] / job_id

    def _publish(self, temporary_output: Path, final_output: Path) -> None:
        compiled_root = self.resolver.roots[RootKey.COMPILED]
        final_output = final_output.resolve(strict=False)
        try:
            final_output.relative_to(compiled_root)
        except ValueError as exc:
            raise ValueError("final output escapes compiled root") from exc
        final_output.parent.mkdir(parents=True, exist_ok=True)
        # os.replace is deliberately used only between paths in compiled_root.
        staging = final_output.parent / f".{final_output.name}.{uuid.uuid4().hex}.publishing"
        shutil.copyfile(temporary_output, staging)
        try:
            os.replace(staging, final_output)
        finally:
            if staging.exists():
                staging.unlink()

    def _finish(
        self, job: JobRecord, state: JobState, error: str | None = None, *, retry: bool = False
    ) -> JobRecord:
        safe_error = self._safe_message(error) if error is not None else None
        if safe_error is not None:
            self._record_log("warning" if retry else "error", safe_error, job.id)
        if retry:
            return self.queue.update(
                job.model_copy(
                    update={
                        "state": JobState.QUEUED,
                        "progress": JobProgress(stage=JobStage.QUEUED, percent=0),
                        "error": safe_error,
                        "started_at": None,
                    }
                )
            )
        stage = {
            JobState.SUCCEEDED: JobStage.COMPLETE,
            JobState.CANCELLED: JobStage.CANCELLED,
            JobState.FAILED: JobStage.FAILED,
        }[state]
        updated = self.queue.update(
            job.model_copy(
                update={
                    "state": state,
                    "progress": JobProgress(
                        stage=stage, percent=100 if state is JobState.SUCCEEDED else 0
                    ),
                    "error": safe_error,
                    "finished_at": _now(),
                    "cancel_requested": state is JobState.CANCELLED,
                }
            )
        )
        if state is JobState.FAILED and job.kind == "compile":
            self._record_clip_failure(job, safe_error)
        return updated

    def _safe_message(self, value: object) -> str:
        return sanitize_message(value, roots=self.resolver.roots.values(), limit=1000)

    def _record_clip_failure(self, job: JobRecord, error: str | None) -> None:
        """Persist a bounded, already-sanitized failure reason on a failed clip."""
        if error is None:
            return
        row = self.db.connection.execute(
            "SELECT metadata FROM clips WHERE id=?", (job.clip_id,)
        ).fetchone()
        if row is None:
            return
        metadata = json.loads(str(row["metadata"]) or "{}")
        metadata["failed_reason"] = error[:500]
        with self.db.transaction():
            self.db.connection.execute(
                "UPDATE clips SET metadata=?, updated_at=? WHERE id=?",
                (json.dumps(metadata, sort_keys=True), _now().isoformat(), job.clip_id),
            )

    def _record_log(self, level: str, message: object, job_id: str | None = None) -> None:
        safe = self._safe_message(message)
        with self.db.transaction():
            self.db.connection.execute(
                "INSERT INTO worker_logs(timestamp,level,message,job_id) VALUES(?,?,?,?)",
                (_now().isoformat(), level, safe, job_id),
            )
            self.db.connection.execute(
                "DELETE FROM worker_logs WHERE id NOT IN "
                "(SELECT id FROM worker_logs ORDER BY id DESC LIMIT 500)"
            )

    def _run_scan_job(self, job: JobRecord) -> JobRunResult:
        """Run catalog discovery for a queued maintenance request."""

        try:
            raw_ids: object = job.profile_settings.get("collection_ids")
            collection_ids: set[str] | None = None
            if raw_ids is not None:
                if not isinstance(raw_ids, list):
                    raise ValueError("scan request has invalid collection IDs")
                collection_ids = set()
                for value in cast(list[object], raw_ids):
                    if not isinstance(value, str):
                        raise ValueError("scan request has invalid collection IDs")
                    collection_ids.add(value)
            summary = self.catalog.scan(collection_ids)
            scan_status = {
                "collection_ids": sorted(collection_ids) if collection_ids is not None else None,
                "added": summary.added,
                "modified": summary.modified,
                "deleted": summary.deleted,
                "invalid": summary.invalid,
                "unchanged": summary.unchanged,
                "completed_at": _now().isoformat(),
            }
            with self.db.transaction():
                self.db.connection.execute(
                    "INSERT INTO worker_status(key,value,updated_at) VALUES('last_scan',?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (json.dumps(scan_status, sort_keys=True), _now().isoformat()),
                )
            completed = job.model_copy(
                update={
                    "logs": (
                        job.logs
                        + [
                            "scan complete "
                            f"added={summary.added} modified={summary.modified} "
                            f"deleted={summary.deleted} invalid={summary.invalid}"
                        ]
                    )[-100:]
                }
            )
            self._record_log("info", completed.logs[-1], job.id)
            return JobRunResult(job=self._finish(completed, JobState.SUCCEEDED))
        except Exception as exc:
            return JobRunResult(job=self._finish(job, JobState.FAILED, str(exc)[:1000]))

    def _cleanup_worker_temporaries(self) -> int:
        """Remove only direct temp directories tracked by inactive persisted jobs,
        plus tracked upload staging files abandoned past their inactivity bound."""

        root = self.resolver.roots[RootKey.TEMP]
        removed = 0
        for candidate in root.iterdir():
            try:
                uuid.UUID(candidate.name)
            except ValueError:
                continue
            if not candidate.is_dir():
                continue
            tracked = self.db.connection.execute(
                "SELECT state FROM jobs WHERE id=?", (candidate.name,)
            ).fetchone()
            if tracked is None or tracked["state"] == "running":
                continue
            self._cleanup(candidate)
            removed += 1
        if self.library_manager is not None:
            removed += self.library_manager.cleanup_abandoned_uploads()
        return removed

    def _run_cleanup_job(self, job: JobRecord) -> JobRunResult:
        try:
            removed = self._cleanup_worker_temporaries()
            completed = job.model_copy(
                update={"logs": (job.logs + [f"temporary cleanup removed={removed}"])[-100:]}
            )
            self._record_log("info", completed.logs[-1], job.id)
            return JobRunResult(job=self._finish(completed, JobState.SUCCEEDED))
        except Exception as exc:
            return JobRunResult(job=self._finish(job, JobState.FAILED, str(exc)[:1000]))

    def run_once(self) -> JobRunResult | None:
        job = self.claim_next()
        if job is None:
            return None
        if job.kind == "scan":
            return self._run_scan_job(job)
        if job.kind == "cleanup":
            return self._run_cleanup_job(job)
        if job.kind != "compile":
            return JobRunResult(
                job=self._finish(job, JobState.FAILED, f"unsupported job kind: {job.kind}")
            )
        temp_dir = self._temporary_directory(job.id)
        try:
            temp_dir.mkdir(parents=True, exist_ok=False)
            runtime_job = self._runtime_job(job, temp_dir)
            profile = ProcessingProfile.model_validate(job.profile_settings)
            timeout = profile.timeout_seconds
            measured_loudness: dict[str, float] | None = None
            successful = False
            cancelled = False
            output = ""
            if profile.loudness.mode == "two_pass":
                measured_segments: dict[str, dict[str, float]] = {}
                measured_by_path: dict[Path, dict[str, float]] = {}
                segment_paths = {
                    "clip": runtime_job.source_path,
                    "intro": runtime_job.intro_path,
                    "outro": runtime_job.outro_path,
                }
                segment_audio = {
                    "clip": runtime_job.has_audio,
                    "intro": runtime_job.intro_has_audio,
                    "outro": runtime_job.outro_has_audio,
                }
                for name, path in segment_paths.items():
                    if path is None or not segment_audio[name]:
                        continue
                    if path in measured_by_path:
                        measured_segments[name] = measured_by_path[path]
                        continue
                    analysis_command = self.command_builder.build_named_segment_loudness_analysis(
                        runtime_job, name
                    )
                    analysis_ok, analysis_cancelled, analysis_output = self._run_process(
                        runtime_job, analysis_command, timeout
                    )
                    if analysis_cancelled:
                        finished = self._finish(job, JobState.CANCELLED, "cancelled")
                        return JobRunResult(job=finished)
                    measured = self._parse_loudnorm(analysis_output)
                    if not analysis_ok or measured is None:
                        retry = job.attempt < job.max_attempts
                        finished = self._finish(
                            job,
                            JobState.FAILED,
                            f"{name} loudness analysis failed",
                            retry=retry,
                        )
                        return JobRunResult(job=finished, retry_scheduled=retry)
                    measured_by_path[path] = measured
                    measured_segments[name] = measured
                composed_mix = temp_dir / f"composed.{profile.output.extension}"
                composed_job = runtime_job.model_copy(
                    update={"temporary_output_path": composed_mix}
                )
                composition_ok, composition_cancelled, composition_output = self._run_process(
                    composed_job,
                    self.command_builder.build(
                        composed_job,
                        include_final_loudness=False,
                        segment_loudness=measured_segments,
                    ),
                    timeout,
                )
                if composition_cancelled:
                    finished = self._finish(job, JobState.CANCELLED, "cancelled")
                    return JobRunResult(job=finished)
                if not composition_ok:
                    retry = job.attempt < job.max_attempts
                    finished = self._finish(
                        job,
                        JobState.FAILED,
                        composition_output[-1000:] or "composition failed",
                        retry=retry,
                    )
                    return JobRunResult(job=finished, retry_scheduled=retry)
                if not profile.loudness.final_mix_normalization:
                    runtime_job = composed_job
                    successful, cancelled, output = True, False, composition_output
                    encode_command = None
                else:
                    analysis = self.command_builder.build_final_loudness_analysis(
                        runtime_job, composed_mix
                    )
                    analysis_ok, analysis_cancelled, analysis_output = self._run_process(
                        runtime_job, analysis, timeout
                    )
                    if analysis_cancelled:
                        finished = self._finish(job, JobState.CANCELLED, "cancelled")
                        return JobRunResult(job=finished)
                    measured_loudness = self._parse_loudnorm(analysis_output)
                    if not analysis_ok or measured_loudness is None:
                        retry = job.attempt < job.max_attempts
                        finished = self._finish(
                            job, JobState.FAILED, "final-mix loudness analysis failed", retry=retry
                        )
                        return JobRunResult(job=finished, retry_scheduled=retry)
                    encode_command = self.command_builder.build_final_normalization(
                        runtime_job, composed_mix, measured_loudness
                    )
            else:
                encode_command = self.command_builder.build(runtime_job)
            if encode_command is not None:
                successful, cancelled, output = self._run_process(
                    runtime_job, encode_command, timeout
                )
            parsed = self._parse_progress(output, job.duration_seconds)
            if parsed is None:
                job = job.model_copy(
                    update={"logs": (job.logs + ["ffmpeg progress was unavailable"])[-100:]}
                )
                self.queue.update(job)
            if cancelled:
                finished = self._finish(job, JobState.CANCELLED, "cancelled")
                return JobRunResult(job=finished)
            if not successful:
                retry = job.attempt < job.max_attempts
                finished = self._finish(
                    job, JobState.FAILED, output[-1000:] or "FFmpeg failed", retry=retry
                )
                return JobRunResult(job=finished, retry_scheduled=retry)
            assert runtime_job.temporary_output_path is not None
            validating = job.model_copy(
                update={"progress": JobProgress(stage=JobStage.VALIDATING, percent=100)}
            )
            self.queue.update(validating)
            probe = self.probe_client.probe(runtime_job.temporary_output_path)
            if not self._valid_output(probe, profile):
                retry = job.attempt < job.max_attempts
                finished = self._finish(
                    job, JobState.FAILED, "temporary output validation failed", retry=retry
                )
                return JobRunResult(job=finished, retry_scheduled=retry)
            final = self.resolver.resolve(RootKey.COMPILED.value, job.output_relative_path)
            self.queue.update(
                job.model_copy(
                    update={"progress": JobProgress(stage=JobStage.PUBLISHING, percent=100)}
                )
            )
            self._publish(runtime_job.temporary_output_path, final)
            metadata_row = self.db.connection.execute(
                "SELECT metadata FROM clips WHERE id=?", (job.clip_id,)
            ).fetchone()
            metadata: dict[str, Any] = (
                json.loads(str(metadata_row["metadata"]))
                if metadata_row and metadata_row["metadata"]
                else {}
            )
            metadata.update(
                {
                    "source_fingerprint": job.source_fingerprint,
                    "profile_fingerprint": job.profile_fingerprint,
                    "output_fingerprint": self._file_fingerprint(final),
                }
            )
            with self.db.transaction():
                self.db.connection.execute(
                    "UPDATE clips SET state='ready', output_available=1, metadata=?, updated_at=? "
                    "WHERE id=?",
                    (json.dumps(metadata, sort_keys=True), _now().isoformat(), job.clip_id),
                )
            finished = self._finish(job, JobState.SUCCEEDED)
            self._record_log("info", "compilation completed", job.id)
            return JobRunResult(job=finished)
        except Exception as exc:
            retry = job.attempt < job.max_attempts and not self._cancelled(job.id)
            finished = self._finish(job, JobState.FAILED, str(exc)[:1000], retry=retry)
            return JobRunResult(job=finished, retry_scheduled=retry)
        finally:
            self._cleanup(temp_dir)

    @staticmethod
    def _file_fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _valid_output(probe: MediaProbeResult, profile: ProcessingProfile) -> bool:
        return bool(
            probe.valid
            and probe.duration_seconds > 0
            and probe.width == profile.video.width
            and probe.height == profile.video.height
            and probe.frame_rate is not None
            and abs(probe.frame_rate - profile.video.fps) < 0.05
            and probe.has_audio
        )
