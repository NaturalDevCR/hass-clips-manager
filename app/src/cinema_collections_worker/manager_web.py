"""Authenticated, CSRF-protected Library Manager HTTP surface."""
# pyright: reportUnusedFunction=false

from __future__ import annotations

import hmac
import html
import json
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


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    minutes, remainder = divmod(total, 60)
    return f"{minutes}:{remainder:02d}"


def _render_clip_row(row: Any) -> str:
    metadata = json.loads(row["metadata"] or "{}")
    tags = ", ".join(metadata.get("tags") or [])
    notes = str(metadata.get("notes") or "")
    state = str(row["state"])
    output_value = row["relative_output_path"] or ""
    output_cell = (
        html.escape(str(output_value), quote=True) if bool(row["output_available"]) else "—"
    )
    duration_seconds = float(row["duration_seconds"] or 0)
    duration_cell = _format_duration(duration_seconds) if duration_seconds > 0 else "—"
    failure = ""
    if state in {"failed", "invalid"} and metadata.get("failed_reason"):
        failure = f'<div class="clip-failure">{html.escape(str(metadata["failed_reason"]))}</div>'
    # An unavailable output cannot be trashed or deleted, so disable those
    # options in both target selectors.
    target_option = (
        '<option value="source">Source</option><option value="output">Output</option>'
        '<option value="both">Both</option>'
        if bool(row["output_available"])
        else '<option value="source">Source</option>'
    )
    return (
        '<tr data-clip-id="{id}" data-output-path="{output}">'
        "<td>{collection}</td><td>{source}{failure}</td><td>{state}</td><td><code>{id}</code></td>"
        "<td>{output_cell}</td><td>{duration_cell}</td><td>{tags}</td>"
        '<td class="actions">'
        '<button data-action="scan">Scan</button> '
        '<button data-action="recompile">Recompile</button> '
        '<select class="trash-target" title="Trash target">{targets}</select> '
        '<button data-action="trash">Trash</button> '
        '<select class="delete-target" title="Delete target">{targets}</select> '
        '<button data-action="delete">Delete</button> '
        '<button data-action="edit-toggle">Edit</button> '
        '<button data-action="move-toggle">Move</button>'
        '<form class="row-form edit-form" hidden>'
        '<input name="tags" value="{tags}" placeholder="tags, comma, separated">'
        '<textarea name="notes" placeholder="Notes">{notes}</textarea>'
        '<button type="submit">Save</button></form>'
        '<form class="row-form move-form" hidden>'
        '<input name="destination" value="{source}" required>'
        '<button type="submit">Move</button></form>'
        "</td></tr>".format(
            id=html.escape(str(row["id"]), quote=True),
            collection=html.escape(str(row["collection_id"])),
            source=html.escape(str(row["relative_source_path"]), quote=True),
            failure=failure,
            state=html.escape(state),
            output=html.escape(str(output_value), quote=True),
            output_cell=output_cell,
            duration_cell=duration_cell,
            tags=html.escape(tags, quote=True),
            notes=html.escape(notes),
            targets=target_option,
        )
    )


def _render_manager(request: Request, csrf: str) -> str:
    database = request.app.state.database
    rows = database.connection.execute(
        "SELECT id, collection_id, relative_source_path, relative_output_path, state, "
        "output_available, duration_seconds, metadata FROM clips "
        "WHERE state <> 'deleted' ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    clip_rows = (
        "".join(_render_clip_row(row) for row in rows)
        or '<tr><td colspan="8">No catalogued clips yet.</td></tr>'
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

    @app.post("/manager/upload-asset", status_code=201, include_in_schema=False)
    async def upload_asset(
        request: Request,
        filename: Annotated[str, Header(alias="X-Filename")],
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        manager = _require_action(request, csrf)
        content = await request.body()
        uploaded = UploadFile(file=BytesIO(content), filename=filename)
        return _dump(manager.import_asset(uploaded))

    @app.get("/manager/assets", include_in_schema=False)
    def list_assets(request: Request) -> list[str]:
        if not _initial_auth_is_valid(request, settings):
            raise HTTPException(status_code=401, detail="Library Manager authentication required")
        return request.app.state.library_manager.list_assets()

    @app.post("/manager/assets/{filename}/delete", include_in_schema=False)
    def delete_asset(
        request: Request,
        filename: str,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        manager = _require_action(request, csrf)
        try:
            return _dump(manager.delete_asset(filename))
        except ValueError as exc:
            # Surface the refusal reason (e.g. a profile still references the
            # asset) verbatim so the UI can tell the user what to change first.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/manager/logs", include_in_schema=False)
    def list_logs(request: Request) -> list[dict[str, Any]]:
        if not _initial_auth_is_valid(request, settings):
            raise HTTPException(status_code=401, detail="Library Manager authentication required")
        rows = request.app.state.database.connection.execute(
            "SELECT timestamp,level,message,job_id FROM worker_logs ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return [
            {
                "timestamp": row["timestamp"],
                "level": str(row["level"]),
                "message": str(row["message"]),
                "job_id": row["job_id"],
            }
            for row in rows
        ]

    @app.get("/manager/jobs", include_in_schema=False)
    def list_manager_jobs(request: Request) -> list[dict[str, Any]]:
        if not _initial_auth_is_valid(request, settings):
            raise HTTPException(status_code=401, detail="Library Manager authentication required")
        rows = request.app.state.database.connection.execute(
            "SELECT id,kind,state,created_at,finished_at,error FROM jobs "
            "ORDER BY created_at DESC, id DESC LIMIT 50"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": str(row["kind"]),
                "state": str(row["state"]),
                "created_at": row["created_at"],
                "finished_at": row["finished_at"],
                "error": row["error"],
            }
            for row in rows
        ]

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
