"""Authenticated FastAPI adapter for the local Cinema Collections Worker."""
# ruff: noqa: E501
# pyright: reportUnusedFunction=false

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import require_bearer_token, require_idempotency_key
from .catalog import CatalogService
from .database import Database
from .domain import (
    CollectionCreate,
    CollectionPatch,
    CollectionRecord,
    ProfileCreate,
    ProfilePatch,
    ProfileRecord,
)
from .jobs import CompileRequest as InternalCompileRequest
from .jobs import JobProgress, JobRecord, JobService, JobStage
from .models import ClipRecord
from .paths import SafePathResolver
from .queue import PersistentJobQueue
from .repositories import (
    CollectionRepository,
    IdempotencyConflict,
    OptimisticConflict,
    ProfileRepository,
    ResourceNotFound,
)
from .settings import WorkerSettings

WORKER_VERSION = "0.1.0"
API_VERSION = "1.0.0"
MIN_CLIENT_VERSION = "1.0.0"
MAX_CLIENT_VERSION = "1.x"
_ACTOR = "worker-api"
_CONTRACT_PATH = Path(__file__).parents[3] / "contract" / "openapi-v1.yaml"


class ErrorPayload(BaseModel):
    """Stable error document declared in the public OpenAPI contract."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: Any | None = None
    retryable: bool
    request_id: str


class HealthPayload(BaseModel):
    status: Literal["ok"] = "ok"
    component: Literal["cinema-collections-worker"] = "cinema-collections-worker"
    worker_version: str = WORKER_VERSION
    api_version: str = API_VERSION
    min_client_version: str = MIN_CLIENT_VERSION
    max_client_version: str = MAX_CLIENT_VERSION


class StatusPayload(BaseModel):
    queue_depth: int = Field(ge=0)
    current_job: dict[str, Any] | None = None
    storage: dict[str, Any]
    scans: dict[str, Any]
    latest_errors: list[ErrorPayload]


class Page(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    items: list[Any]


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection_ids: list[str] | None = None
    resume: bool = True


class CompileRequest(BaseModel):
    """Public compile body: deliberately narrower than the internal job request."""

    model_config = ConfigDict(extra="forbid")

    collection_id: str
    clip_ids: list[UUID] | None = None
    strategy: Literal["scan_and_compile_changed_or_missing", "compile_stale_only", "scan_only"] = (
        "scan_and_compile_changed_or_missing"
    )
    skip_if_processing: bool = True

    def to_internal(self) -> InternalCompileRequest:
        return InternalCompileRequest(
            collection_id=self.collection_id,
            clip_ids=[str(clip_id) for clip_id in self.clip_ids] if self.clip_ids else None,
            strategy=self.strategy,
            skip_if_processing=self.skip_if_processing,
        )


class ApiJob(BaseModel):
    id: str
    kind: Literal["scan", "compile", "cleanup"]
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None = None
    error: ErrorPayload | None = None


Auth = Annotated[None, Depends(require_bearer_token)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else str(uuid.uuid4())


def _error(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    details: Any | None = None,
    retryable: bool = False,
) -> JSONResponse:
    payload = ErrorPayload(
        code=code,
        message=message,
        details=details,
        retryable=retryable,
        request_id=_request_id(request),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _exception_response(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, (IdempotencyConflict, OptimisticConflict)):
        return _error(request, 409, "conflict", "request conflicts with current state")
    if isinstance(exc, (KeyError, ResourceNotFound)):
        return _error(request, 404, "not_found", "requested resource was not found")
    if isinstance(exc, ValueError):
        return _error(request, 422, "validation_error", "request could not be processed")
    return _error(request, 503, "unavailable", "worker dependency is unavailable", retryable=True)


def _serialize_job(job: JobRecord, request: Request) -> ApiJob:
    error = (
        ErrorPayload(
            code="job_failed",
            message=job.error,
            retryable=False,
            request_id=_request_id(request),
        )
        if job.error
        else None
    )
    return ApiJob(
        id=job.id,
        kind=cast(
            Literal["scan", "compile", "cleanup"],
            job.kind if job.kind in {"scan", "compile", "cleanup"} else "compile",
        ),
        state=job.state.value,
        progress=job.progress.model_dump(mode="json"),
        created_at=job.created_at,
        finished_at=job.finished_at,
        error=error,
    )


def _job_from_database(queue: PersistentJobQueue, job_id: str, request: Request) -> ApiJob:
    return _serialize_job(queue.get(job_id), request)


def _page(items: list[Any], page: int, page_size: int) -> Page:
    total = len(items)
    start = (page - 1) * page_size
    return Page(page=page, page_size=page_size, total=total, items=items[start : start + page_size])


def _parse_client_version(value: str) -> tuple[int, int, int] | None:
    parts = value.split(".")
    if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    return tuple((numbers + [0, 0, 0])[:3])  # type: ignore[return-value]


def _require_supported_client(request: Request) -> None:
    """Reject explicitly declared incompatible clients before they can mutate state."""

    value = request.headers.get("X-Client-Version")
    if value is None:
        return
    parsed = _parse_client_version(value)
    if parsed is None or parsed[0] != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="unsupported client version",
        )


MutationPreconditions = [
    Depends(require_bearer_token),
    Depends(require_idempotency_key),
    Depends(_require_supported_client),
]


def _idempotency_fingerprint(operation: str, payload: Any) -> str:
    material = json.dumps(
        {"actor": _ACTOR, "operation": operation, "payload": payload},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _replay_or_remember_job(
    database: Database,
    queue: PersistentJobQueue,
    *,
    key: str,
    operation: str,
    payload: dict[str, Any],
    create: Callable[[], JobRecord],
) -> JobRecord:
    fingerprint = _idempotency_fingerprint(operation, payload)
    row = database.connection.execute(
        "SELECT actor, operation, fingerprint, response FROM idempotency_records WHERE request_id=?",
        (key,),
    ).fetchone()
    if row is not None:
        if (
            row["actor"] != _ACTOR
            or row["operation"] != operation
            or row["fingerprint"] != fingerprint
        ):
            raise IdempotencyConflict("idempotency key was already used for another request")
        try:
            return queue.get(str(json.loads(row["response"])["id"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("idempotent job response is unavailable") from exc
    job = create()
    with database.connection:
        database.connection.execute(
            "INSERT INTO idempotency_records(request_id,actor,operation,response,created_at,fingerprint) VALUES(?,?,?,?,?,?)",
            (
                key,
                _ACTOR,
                operation,
                json.dumps({"id": job.id}),
                datetime.now(UTC).isoformat(),
                fingerprint,
            ),
        )
    return job


def _queued_system_job(
    kind: Literal["scan", "cleanup"], payload: dict[str, Any] | None = None
) -> JobRecord:
    """Represent accepted maintenance work without starting it in an HTTP request."""

    job_id = str(uuid.uuid4())
    return JobRecord(
        id=job_id,
        kind=kind,
        collection_id="system",
        clip_id=job_id,
        source_relative_path=f"{kind}/{job_id}.request",
        output_relative_path=f"{kind}/{job_id}.result",
        source_fingerprint="api-request",
        profile_fingerprint="api-request",
        profile_settings=payload or {},
        duration_seconds=0,
        progress=JobProgress(stage=JobStage.QUEUED, percent=0, eta_seconds=None),
    )


def create_app(settings: WorkerSettings) -> FastAPI:
    """Build the dependency-injected, local-only Worker API application."""

    for path in {settings.data_dir, settings.log_dir, settings.temp_dir, *settings.roots.values()}:
        Path(path).mkdir(parents=True, exist_ok=True)
    database = Database.create(str(settings.database_path))
    resolver = SafePathResolver({key.value: root for key, root in settings.roots.items()})
    collections = CollectionRepository(database)
    profiles = ProfileRepository(database)
    queue = PersistentJobQueue(database)
    app = FastAPI(
        title="Cinema Collections Worker API", version=API_VERSION, docs_url=None, redoc_url=None
    )
    app.state.settings = settings
    app.state.database = database
    app.state.resolver = resolver
    app.state.collections = collections
    app.state.profiles = profiles
    app.state.queue = queue
    app.state.catalog = CatalogService(database, resolver)
    app.state.jobs = JobService(
        database, resolver, disk_reserve_bytes=settings.disk_reserve_bytes, queue=queue
    )

    def contract_openapi() -> dict[str, Any]:
        """Expose the versioned, reviewed contract rather than inferred internals."""

        if app.openapi_schema is None:
            app.openapi_schema = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
        return cast(dict[str, Any], app.openapi_schema)

    app.openapi = contract_openapi  # type: ignore[method-assign]

    @app.middleware("http")
    async def assign_request_id(request: Request, call_next: Callable[[Request], Any]) -> Response:
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        code = "unauthorized" if exc.status_code == 401 else "validation_error"
        if exc.detail == "unsupported client version":
            code = "unsupported_client_version"
        return _error(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_exception(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return _error(
            request,
            422,
            "validation_error",
            "request validation failed",
            details=details,
        )

    @app.exception_handler(OptimisticConflict)
    async def conflict_exception(request: Request, exc: OptimisticConflict) -> JSONResponse:
        return _exception_response(request, exc)

    @app.exception_handler(ResourceNotFound)
    async def missing_resource_exception(request: Request, exc: ResourceNotFound) -> JSONResponse:
        return _exception_response(request, exc)

    @app.exception_handler(KeyError)
    async def missing_key_exception(request: Request, exc: KeyError) -> JSONResponse:
        return _exception_response(request, exc)

    @app.exception_handler(ValueError)
    async def value_exception(request: Request, exc: ValueError) -> JSONResponse:
        return _exception_response(request, exc)

    @app.exception_handler(Exception)
    async def worker_exception(request: Request, exc: Exception) -> JSONResponse:
        return _exception_response(request, exc)

    @app.get(
        "/api/v1/health", response_model=HealthPayload, dependencies=[Depends(require_bearer_token)]
    )
    def get_health() -> HealthPayload:
        return HealthPayload()

    @app.get(
        "/api/v1/status", response_model=StatusPayload, dependencies=[Depends(require_bearer_token)]
    )
    def get_status(request: Request) -> StatusPayload:
        running = database.connection.execute(
            "SELECT id FROM jobs WHERE state='running' LIMIT 1"
        ).fetchone()
        current = (
            _job_from_database(queue, running["id"], request).model_dump(mode="json")
            if running
            else None
        )
        queued = database.connection.execute(
            "SELECT count(*) FROM jobs WHERE state='queued'"
        ).fetchone()[0]
        storage: dict[str, Any] = {}
        for key, root in resolver.roots.items():
            with suppress(OSError):
                storage[key.value] = {"path": str(root), "free_bytes": shutil.disk_usage(root).free}
        return StatusPayload(
            queue_depth=queued, current_job=current, storage=storage, scans={}, latest_errors=[]
        )

    @app.get(
        "/api/v1/collections", response_model=Page, dependencies=[Depends(require_bearer_token)]
    )
    def list_collections(page: PageNumber = 1, page_size: PageSize = 25) -> Page:
        rows = database.connection.execute("SELECT id FROM collections ORDER BY id").fetchall()
        return _page(
            [collections.get(row["id"]).model_dump(mode="json") for row in rows], page, page_size
        )

    @app.post(
        "/api/v1/collections",
        response_model=CollectionRecord,
        status_code=201,
        dependencies=MutationPreconditions,
    )
    def create_collection(payload: CollectionCreate, key: IdempotencyKey) -> CollectionRecord:
        return collections.create(payload, actor=_ACTOR, request_id=key)

    @app.patch(
        "/api/v1/collections/{collection_id}",
        response_model=CollectionRecord,
        dependencies=MutationPreconditions,
    )
    def patch_collection(
        collection_id: str,
        payload: CollectionPatch,
        revision: Annotated[int, Header(alias="If-Match-Revision", ge=1)],
        key: IdempotencyKey,
    ) -> CollectionRecord:
        if not payload.model_dump(exclude_none=True):
            raise ValueError("patch must not be empty")
        return collections.patch(collection_id, revision, payload, actor=_ACTOR, request_id=key)

    @app.get("/api/v1/profiles", response_model=Page, dependencies=[Depends(require_bearer_token)])
    def list_profiles(page: PageNumber = 1, page_size: PageSize = 25) -> Page:
        rows = database.connection.execute("SELECT id FROM profiles ORDER BY id").fetchall()
        return _page(
            [profiles.get(row["id"]).model_dump(mode="json") for row in rows], page, page_size
        )

    @app.post(
        "/api/v1/profiles",
        response_model=ProfileRecord,
        status_code=201,
        dependencies=MutationPreconditions,
    )
    def create_profile(payload: ProfileCreate, key: IdempotencyKey) -> ProfileRecord:
        return profiles.create(payload, actor=_ACTOR, request_id=key)

    @app.patch(
        "/api/v1/profiles/{profile_id}",
        response_model=ProfileRecord,
        dependencies=MutationPreconditions,
    )
    def patch_profile(
        profile_id: str,
        payload: ProfilePatch,
        revision: Annotated[int, Header(alias="If-Match-Revision", ge=1)],
        key: IdempotencyKey,
    ) -> ProfileRecord:
        if not payload.model_dump(exclude_none=True):
            raise ValueError("patch must not be empty")
        return profiles.patch(profile_id, revision, payload, actor=_ACTOR, request_id=key)

    @app.get("/api/v1/clips", response_model=Page, dependencies=[Depends(require_bearer_token)])
    def list_clips(page: PageNumber = 1, page_size: PageSize = 25) -> Page:
        rows = database.connection.execute(
            "SELECT * FROM clips WHERE state <> 'deleted' ORDER BY id"
        ).fetchall()
        return _page([_clip_from_row(row).model_dump(mode="json") for row in rows], page, page_size)

    @app.get(
        "/api/v1/clips/{clip_id}",
        response_model=ClipRecord,
        dependencies=[Depends(require_bearer_token)],
    )
    def get_clip(clip_id: str) -> ClipRecord:
        row = database.connection.execute(
            "SELECT * FROM clips WHERE id=? AND state <> 'deleted'", (clip_id,)
        ).fetchone()
        if row is None:
            raise KeyError(clip_id)
        return _clip_from_row(row)

    @app.post(
        "/api/v1/scan", response_model=ApiJob, status_code=202, dependencies=MutationPreconditions
    )
    def start_scan(
        request: Request, key: IdempotencyKey, payload: ScanRequest | None = None
    ) -> ApiJob:
        body = (payload or ScanRequest()).model_dump(mode="json")
        job = _replay_or_remember_job(
            database,
            queue,
            key=key,
            operation="scan.start",
            payload=body,
            create=lambda: queue.enqueue(_queued_system_job("scan", body)),
        )
        return _serialize_job(job, request)

    @app.post(
        "/api/v1/compile",
        response_model=ApiJob,
        status_code=202,
        dependencies=MutationPreconditions,
    )
    def start_compile(request: Request, payload: CompileRequest, key: IdempotencyKey) -> ApiJob:
        body = payload.model_dump(mode="json")
        internal_request = payload.to_internal()

        def enqueue() -> JobRecord:
            jobs = app.state.jobs.enqueue_compile(internal_request)
            if not jobs:
                raise ValueError("no eligible clips are available for compilation")
            return jobs[0]

        job = _replay_or_remember_job(
            database, queue, key=key, operation="compile.start", payload=body, create=enqueue
        )
        return _serialize_job(job, request)

    @app.get("/api/v1/jobs", response_model=Page, dependencies=[Depends(require_bearer_token)])
    def list_jobs(request: Request, page: PageNumber = 1, page_size: PageSize = 25) -> Page:
        rows = database.connection.execute(
            "SELECT id FROM jobs ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return _page(
            [_job_from_database(queue, row["id"], request).model_dump(mode="json") for row in rows],
            page,
            page_size,
        )

    @app.get(
        "/api/v1/jobs/{job_id}", response_model=ApiJob, dependencies=[Depends(require_bearer_token)]
    )
    def get_job(request: Request, job_id: str) -> ApiJob:
        return _job_from_database(queue, job_id, request)

    @app.post(
        "/api/v1/jobs/{job_id}/cancel", response_model=ApiJob, dependencies=MutationPreconditions
    )
    def cancel_job(request: Request, job_id: str, key: IdempotencyKey) -> ApiJob:
        job = _replay_or_remember_job(
            database,
            queue,
            key=key,
            operation="job.cancel",
            payload={"job_id": job_id},
            create=lambda: app.state.jobs.cancel(job_id),
        )
        return _serialize_job(job, request)

    @app.get("/api/v1/logs", response_model=Page, dependencies=[Depends(require_bearer_token)])
    def list_logs(page: PageNumber = 1, page_size: PageSize = 25) -> Page:
        return _page([], page, page_size)

    @app.post(
        "/api/v1/cleanup-temporaries",
        response_model=ApiJob,
        status_code=202,
        dependencies=MutationPreconditions,
    )
    def cleanup_temporaries(request: Request, key: IdempotencyKey) -> ApiJob:
        job = _replay_or_remember_job(
            database,
            queue,
            key=key,
            operation="cleanup.start",
            payload={},
            create=lambda: queue.enqueue(_queued_system_job("cleanup")),
        )
        return _serialize_job(job, request)

    return app


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
