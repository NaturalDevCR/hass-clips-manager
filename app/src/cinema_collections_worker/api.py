"""Authenticated FastAPI adapter for the local Cinema Collections Worker."""
# ruff: noqa: E501
# pyright: reportUnusedFunction=false

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .auth import require_bearer_token, require_idempotency_key
from .catalog import CatalogService
from .database import Database
from .default_profiles import seed_builtin_profiles
from .domain import (
    CollectionCreate,
    CollectionPatch,
    CollectionRecord,
    ProfileCreate,
    ProfilePatch,
    ProfileRecord,
)
from .jobs import CompileRequest as InternalCompileRequest
from .jobs import JobProgress, JobRecord, JobService, JobStage, JobWorker
from .library_manager import LibraryManager
from .manager_web import install_manager_routes
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

WORKER_VERSION = "1.4.0"
API_VERSION = "1.0.0"
MIN_CLIENT_VERSION = "1.0.0"
MAX_CLIENT_VERSION = "1.x"
_ACTOR = "worker-api"


class Error(BaseModel):
    """Stable error document declared in the public OpenAPI contract."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: Any | None = None
    retryable: bool
    request_id: str


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    component: Literal["cinema-collections-worker"] = "cinema-collections-worker"
    worker_version: str = WORKER_VERSION
    api_version: str = API_VERSION
    min_client_version: str = MIN_CLIENT_VERSION
    max_client_version: str = MAX_CLIENT_VERSION


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


DEFAULT_SCAN_REQUEST = ScanRequest()


class Collection(CollectionRecord):
    """Public collection representation."""


class Profile(ProfileRecord):
    """Public profile representation."""


class Clip(ClipRecord):
    """Public catalog clip representation."""


class Progress(BaseModel):
    stage: str
    percent: float = Field(ge=0, le=100)
    eta_seconds: float | None = Field(default=None, ge=0)


class Job(BaseModel):
    id: str
    kind: Literal["scan", "compile", "cleanup"]
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: Progress
    created_at: datetime
    finished_at: datetime | None = None
    error: Error | None = None


class CancelledJob(Job):
    """Public job representation returned after cancellation."""


class Status(BaseModel):
    queue_depth: int = Field(ge=0)
    current_job: Job | None = None
    storage: dict[str, Any]
    scans: dict[str, Any]
    latest_errors: list[Error]


class CollectionsPage(Page):
    items: list[Collection]


class ProfilesPage(Page):
    items: list[Profile]


class ClipsPage(Page):
    items: list[Clip]


class JobsPage(Page):
    items: list[Job]


class LogEntry(BaseModel):
    timestamp: datetime
    level: str
    message: str
    job_id: UUID | None = None


class LogsPage(Page):
    items: list[LogEntry]


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
    payload = Error(
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


def _serialize_job(job: JobRecord, request: Request) -> Job:
    error = (
        Error(
            code="job_failed",
            message=job.error,
            retryable=False,
            request_id=_request_id(request),
        )
        if job.error
        else None
    )
    return Job(
        id=job.id,
        kind=cast(
            Literal["scan", "compile", "cleanup"],
            job.kind if job.kind in {"scan", "compile", "cleanup"} else "compile",
        ),
        state=job.state.value,
        progress=Progress.model_validate(job.progress.model_dump(mode="json")),
        created_at=job.created_at,
        finished_at=job.finished_at,
        error=error,
    )


def _job_from_database(queue: PersistentJobQueue, job_id: str, request: Request) -> Job:
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

RouteResponses = dict[int | str, dict[str, Any]]
_UNAUTHORIZED_RESPONSE: RouteResponses = {401: {"model": Error}}
_MUTATION_RESPONSES: RouteResponses = {
    401: {"model": Error},
    409: {"model": Error},
    422: {"model": Error},
    429: {"model": Error},
    503: {"model": Error},
}
_NOT_FOUND_RESPONSES: RouteResponses = {401: {"model": Error}, 404: {"model": Error}}


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
    with database.transaction():
        row = database.connection.execute(
            "SELECT actor, operation, fingerprint, response FROM idempotency_records "
            "WHERE request_id=?",
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
    seed_builtin_profiles(database)
    resolver = SafePathResolver({key.value: root for key, root in settings.roots.items()})
    collections = CollectionRepository(database)
    profiles = ProfileRepository(database)
    queue = PersistentJobQueue(database)

    @asynccontextmanager
    async def lifespan(running_app: FastAPI):
        worker: JobWorker = running_app.state.job_worker
        database.connection.execute(
            "UPDATE jobs SET state='queued', started_at=NULL, error='interrupted by Worker restart', "
            "progress=? WHERE state='running' AND attempt < max_attempts",
            (json.dumps({"stage": "queued", "percent": 0, "eta_seconds": None}),),
        )
        database.connection.execute(
            "UPDATE jobs SET state='failed', finished_at=?, error='interrupted after retry cap', "
            "progress=? WHERE state='running' AND attempt >= max_attempts",
            (
                datetime.now(UTC).isoformat(),
                json.dumps({"stage": "failed", "percent": 0, "eta_seconds": None}),
            ),
        )
        database.connection.commit()
        stopping = asyncio.Event()

        async def consume() -> None:
            while not stopping.is_set():
                result = await asyncio.to_thread(worker.run_once)
                if result is not None:
                    continue
                with suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=0.05)

        task = asyncio.create_task(consume(), name="cinema-collections-job-consumer")
        running_app.state.consumer_task = task
        try:
            yield
        finally:
            stopping.set()
            running = database.connection.execute(
                "SELECT id FROM jobs WHERE state='running' LIMIT 1"
            ).fetchone()
            if running is not None:
                queue.request_cancel(str(running["id"]))
            await task
            database.close()

    app = FastAPI(
        title="Cinema Collections Worker API",
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.resolver = resolver
    app.state.collections = collections
    app.state.profiles = profiles
    app.state.queue = queue
    app.state.catalog = CatalogService(database, resolver)
    app.state.jobs = JobService(
        database,
        resolver,
        disk_reserve_bytes=settings.disk_reserve_bytes,
        queue=queue,
        catalog=app.state.catalog,
    )
    app.state.library_manager = LibraryManager(
        database,
        resolver,
        trash_root=settings.data_dir / "library-trash",
        queue=queue,
        jobs=app.state.jobs,
    )
    app.state.job_worker = JobWorker(
        database,
        resolver,
        queue=queue,
        catalog=app.state.catalog,
        library_manager=app.state.library_manager,
    )
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).with_name("static")),
        name="library-manager-static",
    )
    install_manager_routes(app, settings)

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
        "/api/v1/health",
        response_model=Health,
        dependencies=[Depends(require_bearer_token)],
        responses=_UNAUTHORIZED_RESPONSE,
        operation_id="getHealth",
    )
    def get_health() -> Health:
        return Health()

    @app.get(
        "/api/v1/status",
        response_model=Status,
        dependencies=[Depends(require_bearer_token)],
        responses=_UNAUTHORIZED_RESPONSE,
        operation_id="getStatus",
    )
    def get_status(request: Request) -> Status:
        running = database.connection.execute(
            "SELECT id FROM jobs WHERE state='running' LIMIT 1"
        ).fetchone()
        current = _job_from_database(queue, running["id"], request) if running else None
        queued = database.connection.execute(
            "SELECT count(*) FROM jobs WHERE state='queued'"
        ).fetchone()[0]
        storage: dict[str, Any] = {}
        for key, root in resolver.roots.items():
            with suppress(OSError):
                storage[key.value] = {"free_bytes": shutil.disk_usage(root).free}
        scans = {
            str(row["key"]): json.loads(row["value"])
            for row in database.connection.execute(
                "SELECT key,value FROM worker_status ORDER BY key"
            ).fetchall()
        }
        latest_errors = [
            Error(
                code="worker_error",
                message=str(row["message"]),
                retryable=False,
                request_id=_request_id(request),
            )
            for row in database.connection.execute(
                "SELECT message FROM worker_logs WHERE level='error' ORDER BY id DESC LIMIT 10"
            ).fetchall()
        ]
        return Status(
            queue_depth=queued,
            current_job=current,
            storage=storage,
            scans=scans,
            latest_errors=latest_errors,
        )

    @app.get(
        "/api/v1/collections",
        response_model=CollectionsPage,
        dependencies=[Depends(require_bearer_token)],
        responses=_UNAUTHORIZED_RESPONSE,
        operation_id="listCollections",
    )
    def list_collections(page: PageNumber = 1, page_size: PageSize = 25) -> CollectionsPage:
        rows = database.connection.execute("SELECT id FROM collections ORDER BY id").fetchall()
        return CollectionsPage.model_validate(
            _page(
                [collections.get(row["id"]).model_dump(mode="json") for row in rows],
                page,
                page_size,
            ).model_dump()
        )

    @app.post(
        "/api/v1/collections",
        response_model=Collection,
        status_code=201,
        dependencies=MutationPreconditions,
        responses=_MUTATION_RESPONSES,
        operation_id="createCollection",
    )
    def create_collection(payload: CollectionCreate, key: IdempotencyKey) -> CollectionRecord:
        return collections.create(payload, actor=_ACTOR, request_id=key)

    @app.patch(
        "/api/v1/collections/{collection_id}",
        response_model=Collection,
        dependencies=MutationPreconditions,
        responses=_MUTATION_RESPONSES,
        operation_id="patchCollection",
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

    @app.get(
        "/api/v1/profiles",
        response_model=ProfilesPage,
        dependencies=[Depends(require_bearer_token)],
        responses=_UNAUTHORIZED_RESPONSE,
        operation_id="listProfiles",
    )
    def list_profiles(page: PageNumber = 1, page_size: PageSize = 25) -> ProfilesPage:
        rows = database.connection.execute("SELECT id FROM profiles ORDER BY id").fetchall()
        return ProfilesPage.model_validate(
            _page(
                [profiles.get(row["id"]).model_dump(mode="json") for row in rows], page, page_size
            ).model_dump()
        )

    @app.post(
        "/api/v1/profiles",
        response_model=Profile,
        status_code=201,
        dependencies=MutationPreconditions,
        responses=_MUTATION_RESPONSES,
        operation_id="createProfile",
    )
    def create_profile(payload: ProfileCreate, key: IdempotencyKey) -> ProfileRecord:
        return profiles.create(payload, actor=_ACTOR, request_id=key)

    @app.patch(
        "/api/v1/profiles/{profile_id}",
        response_model=Profile,
        dependencies=MutationPreconditions,
        responses=_MUTATION_RESPONSES,
        operation_id="patchProfile",
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

    @app.get(
        "/api/v1/clips",
        response_model=ClipsPage,
        dependencies=[Depends(require_bearer_token)],
        responses=_UNAUTHORIZED_RESPONSE,
        operation_id="listClips",
    )
    def list_clips(page: PageNumber = 1, page_size: PageSize = 25) -> ClipsPage:
        rows = database.connection.execute(
            "SELECT * FROM clips WHERE state <> 'deleted' ORDER BY id"
        ).fetchall()
        return ClipsPage.model_validate(
            _page(
                [_live_clip(database, resolver, row).model_dump(mode="json") for row in rows],
                page,
                page_size,
            ).model_dump()
        )

    @app.get(
        "/api/v1/clips/{clip_id}",
        response_model=Clip,
        dependencies=[Depends(require_bearer_token)],
        responses=_NOT_FOUND_RESPONSES,
        operation_id="getClip",
    )
    def get_clip(clip_id: str) -> ClipRecord:
        row = database.connection.execute(
            "SELECT * FROM clips WHERE id=? AND state <> 'deleted'", (clip_id,)
        ).fetchone()
        if row is None:
            raise KeyError(clip_id)
        return _live_clip(database, resolver, row)

    @app.post(
        "/api/v1/scan",
        response_model=Job,
        status_code=202,
        dependencies=MutationPreconditions,
        responses=_MUTATION_RESPONSES,
        operation_id="startScan",
    )
    def start_scan(
        request: Request,
        key: IdempotencyKey,
        payload: Annotated[ScanRequest, Body()] = DEFAULT_SCAN_REQUEST,
    ) -> Job:
        body = payload.model_dump(mode="json")
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
        response_model=Job,
        status_code=202,
        dependencies=MutationPreconditions,
        responses=_MUTATION_RESPONSES,
        operation_id="startCompile",
    )
    def start_compile(request: Request, payload: CompileRequest, key: IdempotencyKey) -> Job:
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

    @app.get(
        "/api/v1/jobs",
        response_model=JobsPage,
        dependencies=[Depends(require_bearer_token)],
        responses=_UNAUTHORIZED_RESPONSE,
        operation_id="listJobs",
    )
    def list_jobs(request: Request, page: PageNumber = 1, page_size: PageSize = 25) -> JobsPage:
        rows = database.connection.execute(
            "SELECT id FROM jobs ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return JobsPage.model_validate(
            _page(
                [
                    _job_from_database(queue, row["id"], request).model_dump(mode="json")
                    for row in rows
                ],
                page,
                page_size,
            ).model_dump()
        )

    @app.get(
        "/api/v1/jobs/{job_id}",
        response_model=Job,
        dependencies=[Depends(require_bearer_token)],
        responses=_NOT_FOUND_RESPONSES,
        operation_id="getJob",
    )
    def get_job(request: Request, job_id: str) -> Job:
        return _job_from_database(queue, job_id, request)

    @app.post(
        "/api/v1/jobs/{job_id}/cancel",
        response_model=CancelledJob,
        dependencies=MutationPreconditions,
        responses=_MUTATION_RESPONSES,
        operation_id="cancelJob",
    )
    def cancel_job(request: Request, job_id: str, key: IdempotencyKey) -> CancelledJob:
        job = _replay_or_remember_job(
            database,
            queue,
            key=key,
            operation="job.cancel",
            payload={"job_id": job_id},
            create=lambda: app.state.jobs.cancel(job_id),
        )
        return CancelledJob.model_validate(_serialize_job(job, request).model_dump(mode="json"))

    @app.get(
        "/api/v1/logs",
        response_model=LogsPage,
        dependencies=[Depends(require_bearer_token)],
        responses=_UNAUTHORIZED_RESPONSE,
        operation_id="listLogs",
    )
    def list_logs(page: PageNumber = 1, page_size: PageSize = 25) -> LogsPage:
        rows = database.connection.execute(
            "SELECT timestamp,level,message,job_id FROM worker_logs ORDER BY id DESC"
        ).fetchall()
        entries = [
            LogEntry(
                timestamp=row["timestamp"],
                level=str(row["level"]),
                message=str(row["message"]),
                job_id=row["job_id"],
            ).model_dump(mode="json")
            for row in rows
        ]
        return LogsPage.model_validate(_page(entries, page, page_size).model_dump())

    @app.get(
        "/api/v1/assets",
        response_model=list[str],
        dependencies=[Depends(require_bearer_token)],
        responses=_UNAUTHORIZED_RESPONSE,
        operation_id="listAssets",
    )
    def list_assets(request: Request) -> list[str]:
        return app.state.library_manager.list_assets()

    @app.post(
        "/api/v1/cleanup-temporaries",
        response_model=Job,
        status_code=202,
        dependencies=MutationPreconditions,
        responses=_MUTATION_RESPONSES,
        operation_id="cleanupTemporaries",
    )
    def cleanup_temporaries(request: Request, key: IdempotencyKey) -> Job:
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


def _live_clip(database: Database, resolver: SafePathResolver, row: Any) -> ClipRecord:
    """Reconcile the persisted availability bit with one exact compiled path."""

    if bool(row["output_available"]) and row["relative_output_path"]:
        output = resolver.resolve("compiled", str(row["relative_output_path"]))
        if output.is_symlink() or not output.is_file():
            with database.transaction():
                database.connection.execute(
                    "UPDATE clips SET state='stale', output_available=0, updated_at=? WHERE id=?",
                    (datetime.now(UTC).isoformat(), row["id"]),
                )
            row = database.connection.execute(
                "SELECT * FROM clips WHERE id=?", (row["id"],)
            ).fetchone()
    return _clip_from_row(row)
