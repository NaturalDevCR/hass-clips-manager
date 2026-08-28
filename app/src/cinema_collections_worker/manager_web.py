"""Authenticated, CSRF-protected Library Manager HTTP surface."""
# pyright: reportUnusedFunction=false

from __future__ import annotations

import hmac
import html
import secrets
import time
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .library_manager import DeleteTarget, LibraryManager, TrashTarget
from .settings import WorkerMode, WorkerSettings


# Local import avoids a circular import at module load time (api imports this module).
def _worker_version() -> str:
    from .api import WORKER_VERSION

    return WORKER_VERSION


_COOKIE = "cinema_collections_manager"
_SESSION_SECONDS = 60 * 60


class _MetadataBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tags: list[str] = Field(default_factory=list)
    notes: str | None = None


class _MoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_relative_path: str


class _DirectoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str


class _TargetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: TrashTarget


class _DeleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: DeleteTarget
    confirmation: str


def _sessions(app: FastAPI) -> dict[str, tuple[str, float]]:
    return app.state.manager_sessions


def _valid_session(request: Request) -> tuple[str, str] | None:
    session = request.cookies.get(_COOKIE)
    if not session:
        return None
    record = _sessions(request.app).get(session)
    if record is None or record[1] <= time.monotonic():
        _sessions(request.app).pop(session, None)
        return None
    return session, record[0]


def _bearer_is_valid(request: Request, settings: WorkerSettings) -> bool:
    scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(
        supplied, settings.bearer_secret.get_secret_value()
    )


def _initial_auth_is_valid(request: Request, settings: WorkerSettings) -> bool:
    if _valid_session(request) is not None:
        return True
    if _bearer_is_valid(request, settings):
        return True
    # The App port is private to the Supervisor and Ingress injects this header.
    return settings.mode is WorkerMode.APP and bool(request.headers.get("X-Ingress-Path"))


def _require_action(request: Request, csrf: str | None) -> LibraryManager:
    record = _valid_session(request)
    if record is None:
        raise HTTPException(status_code=401, detail="Library Manager authentication required")
    if csrf is None or not hmac.compare_digest(csrf, record[1]):
        raise HTTPException(status_code=403, detail="invalid CSRF token")
    return request.app.state.library_manager


def _render_manager(request: Request, csrf: str) -> str:
    database = request.app.state.database
    rows = database.connection.execute(
        "SELECT id, collection_id, relative_source_path, state FROM clips "
        "WHERE state <> 'deleted' ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    clip_rows = (
        "".join(
            '<tr data-clip-id="{}"><td>{}</td><td>{}</td><td>{}</td>'
            '<td><code>{}</code></td><td class="actions">'
            '<button data-action="scan">Scan</button> '
            '<button data-action="recompile">Recompile</button> '
            '<button data-action="trash">Trash source</button></td></tr>'.format(
                html.escape(str(row["id"]), quote=True),
                html.escape(str(row["collection_id"])),
                html.escape(str(row["relative_source_path"])),
                html.escape(str(row["state"])),
                html.escape(str(row["id"])),
            )
            for row in rows
        )
        or '<tr><td colspan="5">No catalogued clips yet.</td></tr>'
    )
    template = (
        Path(__file__).with_name("templates").joinpath("manager.html").read_text(encoding="utf-8")
    )
    return (
        template.replace("{{ clip_rows }}", clip_rows)
        .replace("{{ csrf_token }}", html.escape(csrf, quote=True))
        .replace("{{ asset_version }}", html.escape(_worker_version(), quote=True))
    )


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def install_manager_routes(app: FastAPI, settings: WorkerSettings) -> None:
    """Install manager routes after its Worker-owned services are initialized."""

    app.state.manager_sessions = {}

    @app.get("/", include_in_schema=False, response_class=HTMLResponse)
    def manager_page(request: Request) -> HTMLResponse:
        if not _initial_auth_is_valid(request, settings):
            raise HTTPException(status_code=401, detail="Library Manager authentication required")
        record = _valid_session(request)
        if record is None:
            session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            _sessions(app)[session] = (csrf, time.monotonic() + _SESSION_SECONDS)
        else:
            session, csrf = record
        page = HTMLResponse(_render_manager(request, csrf))
        page.set_cookie(
            _COOKIE,
            session,
            max_age=_SESSION_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        page.headers["X-CSRF-Token"] = csrf
        page.headers["Cache-Control"] = "no-store"
        return page

    @app.get("/manager/jobs/{job_id}", include_in_schema=False)
    def job_status(request: Request, job_id: str) -> Any:
        """Bounded polling target for scan/recompile progress in the manager UI."""
        if _valid_session(request) is None:
            raise HTTPException(status_code=401, detail="Library Manager authentication required")
        job = request.app.state.queue.get(job_id)
        return {
            "id": job.id,
            "state": job.state.value,
            "progress": job.progress.model_dump(mode="json"),
            "error": job.error,
        }

    @app.post("/manager/scan", status_code=202, include_in_schema=False)
    def scan_library(
        request: Request,
        collection_id: Annotated[str | None, Query(min_length=1)] = None,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        return _dump(_require_action(request, csrf).request_library_scan(collection_id))

    @app.post("/manager/upload", status_code=201, include_in_schema=False)
    async def upload(
        request: Request,
        collection_id: Annotated[str, Query(min_length=1)],
        filename: Annotated[str, Header(alias="X-Filename")],
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        manager = _require_action(request, csrf)
        content = await request.body()
        uploaded = UploadFile(file=BytesIO(content), filename=filename)
        return _dump(manager.import_clip(collection_id, uploaded))

    @app.post(
        "/manager/collections/{collection_id}/directories",
        status_code=201,
        include_in_schema=False,
    )
    def create_directory(
        request: Request,
        collection_id: str,
        payload: _DirectoryBody,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        return _dump(
            _require_action(request, csrf).create_collection_directory(
                collection_id, payload.relative_path
            )
        )

    @app.post("/manager/clips/{clip_id}/metadata", include_in_schema=False)
    def metadata(
        request: Request,
        clip_id: str,
        payload: _MetadataBody,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        return _dump(
            _require_action(request, csrf).update_tags_and_notes(
                clip_id, payload.tags, payload.notes
            )
        )

    @app.post("/manager/clips/{clip_id}/move", include_in_schema=False)
    def move(
        request: Request,
        clip_id: str,
        payload: _MoveBody,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        return _dump(
            _require_action(request, csrf).rename_or_move(
                clip_id, payload.destination_relative_path
            )
        )

    @app.post("/manager/clips/{clip_id}/scan", status_code=202, include_in_schema=False)
    def scan(
        request: Request,
        clip_id: str,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        return _dump(_require_action(request, csrf).request_scan(clip_id))

    @app.post("/manager/clips/{clip_id}/recompile", status_code=202, include_in_schema=False)
    def recompile(
        request: Request,
        clip_id: str,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        return _dump(_require_action(request, csrf).request_recompile(clip_id))

    @app.post("/manager/clips/{clip_id}/trash", include_in_schema=False)
    def trash(
        request: Request,
        clip_id: str,
        payload: _TargetBody,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        return _dump(_require_action(request, csrf).move_to_trash(clip_id, payload.target))

    @app.get("/manager/trash", include_in_schema=False)
    def list_trash(request: Request) -> list[Any]:
        if not _initial_auth_is_valid(request, settings):
            raise HTTPException(status_code=401, detail="Library Manager authentication required")
        return [_dump(item) for item in request.app.state.library_manager.list_trash()]

    @app.post("/manager/trash/{trash_id}/restore", include_in_schema=False)
    def restore(
        request: Request,
        trash_id: str,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        return _dump(_require_action(request, csrf).restore(trash_id))

    @app.get("/manager/clips/{clip_id}/delete-confirmation", include_in_schema=False)
    def delete_confirmation(
        request: Request,
        clip_id: str,
        target: Annotated[DeleteTarget, Query()],
    ) -> dict[str, str]:
        if not _initial_auth_is_valid(request, settings):
            raise HTTPException(status_code=401, detail="Library Manager authentication required")
        # Resolve the record now so a confirmation is never issued for an arbitrary UUID.
        request.app.state.library_manager._clip_row(clip_id)
        return {
            "confirmation": LibraryManager.delete_confirmation_token(clip_id, target),
            "warning": "Permanent deletion cannot be undone.",
        }

    @app.post("/manager/clips/{clip_id}/delete", include_in_schema=False)
    def permanent_delete(
        request: Request,
        clip_id: str,
        payload: Annotated[_DeleteBody, Body()],
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        return _dump(
            _require_action(request, csrf).permanently_delete(
                clip_id, payload.target, payload.confirmation
            )
        )
