"""Authenticated, CSRF-protected Library Manager HTTP surface."""
# pyright: reportUnusedFunction=false

from __future__ import annotations

import base64
import hashlib
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

from .library_manager import DeleteTarget, LibraryManager, TrashTarget, UploadKind
from .settings import WorkerMode, WorkerSettings


# Local import avoids a circular import at module load time (api imports this module).
def _worker_version() -> str:
    from .api import WORKER_VERSION

    return WORKER_VERSION


_COOKIE = "cinema_collections_manager"
_SESSION_SECONDS = 60 * 60
_ORDER_CAPABILITY_HEADER = "X-Cinema-Collections-Order-Capability"
_ORDER_CAPABILITY_PATH = "/api/cinema_collections/order"
_ORDER_CAPABILITY_SECONDS = 5 * 60
# Column count of the clip table, used by both the colspan on the expandable
# panel row and the empty-state placeholder so neither can drift apart.
_CLIP_TABLE_COLUMNS = 8


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


def _render_clip_row(row: Any, *, sequential_rank: int) -> str:
    metadata = json.loads(row["metadata"] or "{}")
    tags = ", ".join(metadata.get("tags") or [])
    notes = str(metadata.get("notes") or "")
    state = str(row["state"])
    source_value = str(row["relative_source_path"] or "")
    source_name = source_value.rsplit("/", 1)[-1] if source_value else ""
    output_value = str(row["relative_output_path"] or "")
    output_available = bool(row["output_available"])
    if output_available and output_value:
        # Compiled outputs are named after the clip UUID, so the filename itself
        # tells a reader nothing and only crowds the row. Report availability and
        # keep the exact path reachable through the cell's tooltip.
        output_cell = (
            f'<td class="output-ready" title="{html.escape(output_value, quote=True)}">Ready</td>'
        )
    else:
        output_cell = "<td>—</td>"
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
        if output_available
        else '<option value="source">Source</option>'
    )
    # Both rows carry the clip identity and the paths the panel's handlers
    # read back off row.dataset (the source cell only exists on the data row).
    # The data row also carries the collection, state, and duration the
    # playback-order editor reads when it builds its catalog model.
    return (
        '<tr data-clip-id="{id}" data-output-path="{output}" '
        'data-source-path="{source_path}" data-collection="{collection}" '
        'data-state="{state}" data-duration-seconds="{duration_seconds}" '
        'data-sequential-rank="{sequential_rank}" title="{id}">'
        "<td>{collection}</td>"
        '<td title="{source_path}">{source_name}{failure}</td>'
        "<td>{state}</td>"
        "{output_cell}"
        "<td>{duration_cell}</td><td>{tags}</td>"
        '<td class="clip-id"><code>{id}</code> '
        '<button type="button" data-action="copy-id" '
        'aria-label="Copy clip ID {id}">Copy ID</button></td>'
        '<td class="actions">'
        '<button data-action="recompile">Recompile</button> '
        '<button data-action="manage-toggle" aria-expanded="false">Manage</button>'
        "</td></tr>"
        '<tr class="row-panel-row" data-clip-id="{id}" data-output-path="{output}" '
        'data-source-path="{source_path}" hidden>'
        '<td colspan="{colspan}">'
        '<div class="row-panel">'
        '<div class="panel-group"><span class="panel-label">Re-scan the source file</span>'
        '<button data-action="scan">Scan</button></div>'
        '<div class="panel-group"><span class="panel-label">Move to trash</span>'
        '<select class="trash-target" title="Trash target">{targets}</select> '
        '<button data-action="trash">Trash</button></div>'
        '<div class="panel-group"><span class="panel-label">Permanently delete</span>'
        '<select class="delete-target" title="Delete target">{targets}</select> '
        '<button data-action="delete">Delete</button></div>'
        '<div class="panel-group"><span class="panel-label">Edit metadata</span>'
        '<button data-action="edit-toggle">Edit</button> '
        '<form class="row-form edit-form" hidden>'
        '<input name="tags" value="{tags}" placeholder="tags, comma, separated">'
        '<textarea name="notes" placeholder="Notes">{notes}</textarea>'
        '<button type="submit">Save</button></form></div>'
        '<div class="panel-group"><span class="panel-label">Move the source file</span>'
        '<button data-action="move-toggle">Move</button> '
        '<form class="row-form move-form" hidden>'
        '<input name="destination" value="{source}" required>'
        '<button type="submit">Move</button></form></div>'
        "</div></td></tr>".format(
            id=html.escape(str(row["id"]), quote=True),
            collection=html.escape(str(row["collection_id"]), quote=True),
            source_path=html.escape(source_value, quote=True),
            source_name=html.escape(source_name, quote=True),
            failure=failure,
            state=html.escape(state, quote=True),
            output=html.escape(output_value, quote=True),
            output_cell=output_cell,
            duration_cell=duration_cell,
            duration_seconds=f"{duration_seconds:g}",
            sequential_rank=sequential_rank,
            tags=html.escape(tags, quote=True),
            notes=html.escape(notes),
            targets=target_option,
            source=html.escape(source_value, quote=True),
            colspan=_CLIP_TABLE_COLUMNS,
        )
    )


def _clip_payloads(database: Any) -> list[dict[str, Any]]:
    """Return every live clip, ranked the way the playback-order editor expects."""
    rows = database.connection.execute(
        "SELECT id, collection_id, relative_source_path, relative_output_path, state, "
        "output_available, duration_seconds, metadata FROM clips "
        "WHERE state <> 'deleted' ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    by_collection: dict[str, list[Any]] = {}
    for row in rows:
        by_collection.setdefault(str(row["collection_id"]), []).append(row)
    ranks: dict[str, int] = {}
    for collection_rows in by_collection.values():
        ordered = sorted(
            collection_rows,
            key=lambda row: (
                str(row["relative_output_path"] or "").casefold(),
                str(row["relative_output_path"] or ""),
                str(row["id"]),
            ),
        )
        ranks.update({str(row["id"]): rank for rank, row in enumerate(ordered)})
    payloads: list[dict[str, Any]] = []
    for row in rows:
        metadata = json.loads(row["metadata"] or "{}")
        state = str(row["state"])
        failed_reason = metadata.get("failed_reason")
        payloads.append(
            {
                "id": str(row["id"]),
                "collection_id": str(row["collection_id"]),
                "relative_source_path": str(row["relative_source_path"] or ""),
                "relative_output_path": str(row["relative_output_path"] or ""),
                "output_available": bool(row["output_available"]),
                "state": state,
                "duration_seconds": float(row["duration_seconds"] or 0),
                "sequential_rank": ranks[str(row["id"])],
                "tags": [str(tag) for tag in (metadata.get("tags") or [])],
                "notes": str(metadata.get("notes") or ""),
                "failed_reason": (
                    str(failed_reason) if failed_reason and state in {"failed", "invalid"} else None
                ),
            }
        )
    return payloads


def _collection_payloads(database: Any) -> list[dict[str, str]]:
    rows = database.connection.execute(
        "SELECT id, name FROM collections ORDER BY name COLLATE NOCASE, id"
    ).fetchall()
    return [{"id": str(row["id"]), "name": str(row["name"])} for row in rows]


def _order_bridge_capability(settings: WorkerSettings) -> str:
    """Create a short-lived capability for the HA order bridge.

    The browser must not receive the long-lived Worker bearer secret. The
    integration validates this scoped capability against the same secret it
    already stores for Worker API calls.
    """
    expires = int(time.time()) + _ORDER_CAPABILITY_SECONDS
    nonce = secrets.token_urlsafe(18)
    payload = f"{_ORDER_CAPABILITY_PATH}|{expires}|{nonce}".encode()
    signature = hmac.new(
        settings.bearer_secret.get_secret_value().encode(), payload, hashlib.sha256
    ).digest()
    encoded_payload = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{encoded_payload}.{encoded_signature}"


def _render_manager(request: Request, csrf: str, settings: WorkerSettings) -> str:
    database = request.app.state.database
    rows = database.connection.execute(
        "SELECT id, collection_id, relative_source_path, relative_output_path, state, "
        "output_available, duration_seconds, metadata FROM clips "
        "WHERE state <> 'deleted' ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    collection_rows = database.connection.execute(
        "SELECT id, name FROM collections ORDER BY name COLLATE NOCASE, id"
    ).fetchall()
    collection_options = "".join(
        f'<option value="{html.escape(str(row["id"]), quote=True)}">'
        f"{html.escape(str(row['name']))}</option>"
        for row in collection_rows
    )
    by_collection: dict[str, list[Any]] = {}
    for row in rows:
        by_collection.setdefault(str(row["collection_id"]), []).append(row)
    sequential_ranks: dict[str, int] = {}
    for collection_rows_for_rank in by_collection.values():
        ordered_rows = sorted(
            collection_rows_for_rank,
            key=lambda row: (
                str(row["relative_output_path"] or "").casefold(),
                str(row["relative_output_path"] or ""),
                str(row["id"]),
            ),
        )
        sequential_ranks.update({str(row["id"]): rank for rank, row in enumerate(ordered_rows)})
    clip_rows = (
        "".join(
            _render_clip_row(row, sequential_rank=sequential_ranks[str(row["id"])]) for row in rows
        )
        or f'<tr><td colspan="{_CLIP_TABLE_COLUMNS}">No catalogued clips yet.</td></tr>'
    )
    template = (
        Path(__file__).with_name("templates").joinpath("manager.html").read_text(encoding="utf-8")
    )
    return (
        template.replace("{{ clip_rows }}", clip_rows)
        .replace("{{ csrf_token }}", html.escape(csrf, quote=True))
        .replace(
            "{{ order_bridge_capability }}",
            html.escape(_order_bridge_capability(settings), quote=True),
        )
        .replace("{{ asset_version }}", html.escape(_worker_version(), quote=True))
        .replace("{{ collection_options }}", collection_options)
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
        page = HTMLResponse(_render_manager(request, csrf, settings))
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

    @app.get("/manager/clips", include_in_schema=False)
    def list_manager_clips(request: Request) -> list[dict[str, Any]]:
        if _valid_session(request) is None:
            raise HTTPException(status_code=401, detail="Library Manager session required")
        return _clip_payloads(request.app.state.database)

    @app.get("/manager/collections", include_in_schema=False)
    def list_manager_collections(request: Request) -> list[dict[str, str]]:
        if _valid_session(request) is None:
            raise HTTPException(status_code=401, detail="Library Manager session required")
        return _collection_payloads(request.app.state.database)

    @app.get("/manager/session", include_in_schema=False)
    def manager_session(request: Request) -> dict[str, str]:
        record = _valid_session(request)
        if record is None:
            raise HTTPException(status_code=401, detail="Library Manager session required")
        _, csrf = record
        return {
            "csrf": csrf,
            "worker_version": _worker_version(),
            "order_bridge_capability": _order_bridge_capability(settings),
        }

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

    @app.post("/manager/uploads", status_code=201, include_in_schema=False)
    def begin_upload(
        request: Request,
        kind: Annotated[UploadKind, Query()],
        filename: Annotated[str, Header(alias="X-Filename")],
        collection_id: Annotated[str | None, Query(min_length=1)] = None,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        manager = _require_action(request, csrf)
        return {"upload_id": manager.begin_upload(kind, filename, collection_id)}

    @app.post("/manager/uploads/{upload_id}/chunk", include_in_schema=False)
    async def append_chunk(
        request: Request,
        upload_id: str,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        manager = _require_action(request, csrf)
        received: int | None = None
        # Stream the body so even an oversized single chunk never sits in RAM.
        async for block in request.stream():
            received = manager.append_chunk(upload_id, block)
        if received is None:
            received = manager.append_chunk(upload_id, b"")
        return {"received": received}

    @app.post("/manager/uploads/{upload_id}/finish", status_code=201, include_in_schema=False)
    def finish_upload(
        request: Request,
        upload_id: str,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        return _dump(_require_action(request, csrf).finish_upload(upload_id))

    @app.post("/manager/uploads/{upload_id}/abort", include_in_schema=False)
    def abort_upload(
        request: Request,
        upload_id: str,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        _require_action(request, csrf).abort_upload(upload_id)
        return {"upload_id": upload_id}

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
