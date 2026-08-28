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
