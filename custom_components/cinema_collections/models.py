"""Typed, dependency-free representations of Worker API responses."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast


class WorkerContractError(ValueError):
    """Raised when a Worker response does not satisfy the published contract."""


def _required_string(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise WorkerContractError(f"Worker response field {name!r} must be a non-empty string")
    return value


def _required_mapping(value: Any, name: str) -> Mapping[str, Any]:
    """Validate an object-shaped response field while preserving typed values."""
    if not isinstance(value, Mapping):
        raise WorkerContractError(f"Worker response field {name!r} must be an object")
    return cast(Mapping[str, Any], value)


@dataclass(frozen=True, slots=True)
class WorkerClip:
    """The Worker-owned live output availability needed for clip selection."""

    id: str
    collection_id: str
    state: str
    relative_output_path: str | None
    duration_seconds: float
    output_available: bool

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WorkerClip:
        """Parse the public subset of a Worker catalog clip used by the integration."""
        relative_output_path = payload.get("relative_output_path")
        if relative_output_path is not None and not isinstance(relative_output_path, str):
            raise WorkerContractError(
                "Worker response field 'relative_output_path' must be a string or null"
            )
        duration_seconds = payload.get("duration_seconds")
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
            or duration_seconds < 0
        ):
            raise WorkerContractError(
                "Worker response field 'duration_seconds' must be a non-negative number"
            )
        output_available = payload.get("output_available")
        if not isinstance(output_available, bool):
            raise WorkerContractError("Worker response field 'output_available' must be boolean")
        return cls(
            id=_required_string(payload, "id"),
            collection_id=_required_string(payload, "collection_id"),
            state=_required_string(payload, "state"),
            relative_output_path=relative_output_path,
            duration_seconds=float(duration_seconds),
            output_available=output_available,
        )


@dataclass(frozen=True, slots=True)
class WorkerProfileSummary:
    """The minimal Worker processing-profile identity needed for a picker."""

    id: str
    name: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WorkerProfileSummary:
        """Parse the public subset of a Worker profile used to populate a selector."""
        return cls(id=_required_string(payload, "id"), name=_required_string(payload, "name"))


@dataclass(frozen=True, slots=True)
class WorkerJob:
    """The safe public job progress fields exposed by the Worker queue."""

    id: str
    kind: str
    state: str
    progress_stage: str
    progress_percent: float

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WorkerJob:
        """Parse the public job fields needed for integration observability."""
        progress = _required_mapping(payload.get("progress"), "progress")
        percent = progress.get("percent")
        if (
            isinstance(percent, bool)
            or not isinstance(percent, (int, float))
            or not 0 <= percent <= 100
        ):
            raise WorkerContractError(
                "Worker job response field 'progress.percent' must be a percentage"
            )
        return cls(
            id=_required_string(payload, "id"),
            kind=_required_string(payload, "kind"),
            state=_required_string(payload, "state"),
            progress_stage=_required_string(progress, "stage"),
            progress_percent=float(percent),
        )


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    """Worker liveness and client-compatibility information."""

    status: str
    component: str
    worker_version: str
    api_version: str
    min_client_version: str
    max_client_version: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WorkerHealth:
        """Parse a `/health` response according to the Worker API contract."""
        status = _required_string(payload, "status")
        if status != "ok":
            raise WorkerContractError("Worker health response status must be 'ok'")
        return cls(
            status=status,
            component=_required_string(payload, "component"),
            worker_version=_required_string(payload, "worker_version"),
            api_version=_required_string(payload, "api_version"),
            min_client_version=_required_string(payload, "min_client_version"),
            max_client_version=_required_string(payload, "max_client_version"),
        )


@dataclass(frozen=True, slots=True)
class WorkerError:
    """A sanitized error object returned by the Worker."""

    code: str
    message: str
    retryable: bool
    request_id: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WorkerError:
        """Parse the stable fields in a Worker API error response."""
        retryable = payload.get("retryable")
        if not isinstance(retryable, bool):
            raise WorkerContractError("Worker error response field 'retryable' must be boolean")
        return cls(
            code=_required_string(payload, "code"),
            message=_required_string(payload, "message"),
            retryable=retryable,
            request_id=_required_string(payload, "request_id"),
        )


@dataclass(frozen=True, slots=True)
class WorkerStatus:
    """Operational state exposed by the Worker `/status` endpoint."""

    queue_depth: int
    current_job: Mapping[str, Any] | None
    storage: Mapping[str, Any]
    scans: Mapping[str, Any]
    latest_errors: tuple[WorkerError, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WorkerStatus:
        """Parse a `/status` response according to the Worker API contract."""
        queue_depth = payload.get("queue_depth")
        if isinstance(queue_depth, bool) or not isinstance(queue_depth, int) or queue_depth < 0:
            raise WorkerContractError(
                "Worker response field 'queue_depth' must be a non-negative integer"
            )

        current_job = payload.get("current_job")
        if current_job is not None:
            current_job = _required_mapping(current_job, "current_job")

        storage = _required_mapping(payload.get("storage"), "storage")
        scans = _required_mapping(payload.get("scans"), "scans")
        latest_errors = payload.get("latest_errors")
        if not isinstance(latest_errors, list):
            raise WorkerContractError(
                "Worker response field 'latest_errors' must be an array of objects"
            )
        errors = tuple(
            WorkerError.from_dict(_required_mapping(error, "latest_errors item"))
            for error in cast(list[Any], latest_errors)
        )

        return cls(
            queue_depth=queue_depth,
            current_job=(MappingProxyType(dict(current_job)) if current_job is not None else None),
            storage=MappingProxyType(dict(storage)),
            scans=MappingProxyType(dict(scans)),
            latest_errors=errors,
        )
