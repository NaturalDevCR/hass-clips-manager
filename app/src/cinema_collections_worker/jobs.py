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
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .database import Database
from .ffmpeg import FfmpegCommandBuilder
from .paths import RootKey, SafePathResolver, validate_collection_id, validate_relative_path
from .probe import MediaProbeResult, ProbeClient
from .profile_validation import ProcessingProfile, profile_fingerprint, validate_profile
from .queue import PersistentJobQueue


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
    strategy: str = "scan_and_compile_changed_or_missing"
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
    ) -> None:
        self.db = db
        self.resolver = resolver
        self.disk_reserve_bytes = disk_reserve_bytes
        self.queue = queue or PersistentJobQueue(db)

    @staticmethod
    def available_disk_bytes(path: Path) -> int:
        return shutil.disk_usage(path).free

    def _profile(self, collection_id: str) -> tuple[dict[str, Any], str]:
        row = self.db.connection.execute(
            "SELECT p.settings FROM collections c JOIN profiles p ON p.id=c.processing_profile_id WHERE c.id=? AND c.enabled=1",
            (collection_id,),
        ).fetchone()
        if row is None:
            raise KeyError(collection_id)
        settings = json.loads(row["settings"])
        profile = validate_profile(ProcessingProfile.model_validate(settings))
        return profile.model_dump(mode="json"), profile_fingerprint(profile, {})

    def _eligible_clips(self, request: CompileRequest) -> list[Any]:
        sql = "SELECT * FROM clips WHERE collection_id=? AND state NOT IN ('invalid','deleted')"
        args: list[Any] = [request.collection_id]
        if request.clip_ids is not None:
            if not request.clip_ids:
                return []
            placeholders = ",".join("?" for _ in request.clip_ids)
            sql += f" AND id IN ({placeholders})"
            args.extend(request.clip_ids)
        return self.db.connection.execute(sql, args).fetchall()

    def enqueue_compile(self, request: CompileRequest) -> list[JobRecord]:
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
            output_relative = str(
                clip["relative_output_path"] or f"{request.collection_id}/{clip['id']}.mp4"
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
            jobs.append(self.queue.enqueue(job))
        return jobs

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
    ) -> None:
        self.db = db
        self.resolver = resolver
        self.command_builder = command_builder or FfmpegCommandBuilder()
        self.probe_client = probe_client or ProbeClient()
        self.process_factory = process_factory
        self.queue = queue or PersistentJobQueue(db)

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

        def asset_duration(path: Path | None) -> float:
            if path is None:
                return 0
            result = self.probe_client.probe(path)
            if not result.valid:
                raise ValueError("referenced asset could not be probed")
            return result.duration_seconds

        return job.model_copy(
            update={
                "source_path": source,
                "temporary_output_path": temporary_output,
                "intro_path": intro,
                "outro_path": outro,
                "intro_duration_seconds": asset_duration(intro),
                "outro_duration_seconds": asset_duration(outro),
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
    def _terminate_process_group(process: Any) -> None:
        pid = getattr(process, "pid", None)
        if isinstance(pid, int) and pid > 0:
            try:
                os.killpg(pid, signal.SIGTERM)
                return
            except (OSError, ProcessLookupError):
                pass
        terminate = getattr(process, "terminate", None)
        if callable(terminate):
            terminate()

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
                self._terminate_process_group(process)
                return False, True, captured
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate_process_group(process)
                return False, False, captured + "\nFFmpeg timed out"
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
        if retry:
            return self.queue.update(
                job.model_copy(
                    update={
                        "state": JobState.QUEUED,
                        "progress": JobProgress(stage=JobStage.QUEUED, percent=0),
                        "error": error,
                        "started_at": None,
                    }
                )
            )
        stage = {
            JobState.SUCCEEDED: JobStage.COMPLETE,
            JobState.CANCELLED: JobStage.CANCELLED,
            JobState.FAILED: JobStage.FAILED,
        }[state]
        return self.queue.update(
            job.model_copy(
                update={
                    "state": state,
                    "progress": JobProgress(
                        stage=stage, percent=100 if state is JobState.SUCCEEDED else 0
                    ),
                    "error": error,
                    "finished_at": _now(),
                    "cancel_requested": state is JobState.CANCELLED,
                }
            )
        )

    def run_once(self) -> JobRunResult | None:
        job = self.claim_next()
        if job is None:
            return None
        temp_dir = self._temporary_directory(job.id)
        try:
            temp_dir.mkdir(parents=True, exist_ok=False)
            runtime_job = self._runtime_job(job, temp_dir)
            profile = ProcessingProfile.model_validate(job.profile_settings)
            timeout = profile.timeout_seconds
            measured_loudness: dict[str, float] | None = None
            if profile.loudness.mode == "two_pass":
                composed_mix = temp_dir / f"composed.{profile.output.extension}"
                composed_job = runtime_job.model_copy(
                    update={"temporary_output_path": composed_mix}
                )
                composition_ok, composition_cancelled, composition_output = self._run_process(
                    composed_job,
                    self.command_builder.build(composed_job, include_final_loudness=False),
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
            successful, cancelled, output = self._run_process(runtime_job, encode_command, timeout)
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
            if not probe.valid:
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
            with self.db.connection:
                self.db.connection.execute(
                    "UPDATE clips SET state='ready', output_available=1, updated_at=? WHERE id=?",
                    (_now().isoformat(), job.clip_id),
                )
            finished = self._finish(job, JobState.SUCCEEDED)
            return JobRunResult(job=finished)
        except Exception as exc:
            retry = job.attempt < job.max_attempts and not self._cancelled(job.id)
            finished = self._finish(job, JobState.FAILED, str(exc)[:1000], retry=retry)
            return JobRunResult(job=finished, retry_scheduled=retry)
        finally:
            self._cleanup(temp_dir)
