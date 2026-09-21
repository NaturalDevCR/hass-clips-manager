# Clip trim/crop editing, preview, and readable dates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user trim and crop a catalogued clip's source video from the Library Manager with a live preview, and make System-view timestamps human-readable in a configurable timezone (default Costa Rica).

**Architecture:** A new read-only `GET /manager/clips/{id}/source` route streams the source file (Range-aware) for a `<video>` preview. A new `POST /manager/clips/{id}/edit` route validates a trim/crop request and enqueues a `kind="edit"` job on the existing persistent job queue, following the same shape as the existing `scan`/`compile` jobs. A new `_run_edit_job` in `jobs.py` re-encodes the requested range with `ffmpeg` and atomically replaces the source file in place (`os.replace`), exactly the way compile jobs atomically publish output. The frontend gets a new `ClipEditor.vue` modal (video + trim slider + draggable crop rectangle) opened from `ClipDrawer.vue`, reusing the existing job-polling composable. Separately, `SystemView.vue`'s raw ISO timestamps are rendered through a new `formatDateTime` helper with a per-browser timezone preference.

**Tech Stack:** FastAPI + Pydantic v2 + SQLite (`cinema_collections_worker`), `ffmpeg`/`ffprobe` subprocesses, Vue 3 `<script setup>` + TypeScript + Tailwind, Vitest + pytest.

## Global Constraints

- Python 3.13, must pass `uv run ruff format --check .`, `uv run ruff check .`, and `uv run pyright` with zero new warnings.
- No new frontend dependencies — implement crop dragging with native Pointer Events, no library.
- Every mutating manager route requires the existing session cookie + `X-CSRF-Token` pattern (`_require_action`); every read-only manager route requires only the session cookie (`_valid_session`). Follow both exactly as the existing routes in `manager_web.py` do.
- A `ValueError`/`KeyError` raised by a manager-layer method (e.g. `LibraryManager.request_edit`) must be caught and re-raised as `HTTPException(422/404, detail=str(exc))` in the route if its message should reach the client's `message` field — the app's global `ValueError`/`KeyError` handlers otherwise produce a generic message and bury the real reason in `details`. `delete_asset` in `manager_web.py` is the existing precedent; follow it.
- libx264 requires even width/height — any crop dimension must be rounded down to the nearest even number before it reaches `ffmpeg`.
- The full local gate is `scripts/verify.sh`; run the narrower commands named in each task while iterating.

---

## Task 1: `formatDateTime` — human-readable, timezone-aware timestamps

**Files:**
- Modify: `app/ui/src/lib/format.ts`
- Test: `app/ui/src/lib/format.spec.ts`

**Interfaces:**
- Produces: `formatDateTime(iso: string | null, timeZone?: string): string` and `DEFAULT_TIME_ZONE: string` (`"America/Costa_Rica"`), both exported from `@/lib/format`. Task 2 imports both.

- [ ] **Step 1: Write the failing tests**

Append to `app/ui/src/lib/format.spec.ts`:

```ts
describe("formatDateTime", () => {
  // 2026-01-15T04:30:00Z is 2026-01-14 22:30 in America/Costa_Rica (UTC-6).
  const timestamp = "2026-01-15T04:30:00Z";

  it("renders in Costa Rica time by default", () => {
    const result = formatDateTime(timestamp);
    expect(result).toContain("14");
    expect(result).toContain("2026");
    expect(result).toContain("10:30");
  });

  it("renders in an overridden zone", () => {
    const result = formatDateTime(timestamp, "UTC");
    expect(result).toContain("15");
    expect(result).toContain("4:30");
  });

  it("shows a dash for a missing timestamp", () => {
    expect(formatDateTime(null)).toBe("—");
  });

  it("falls back to the raw string for an unparseable timestamp", () => {
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });
});
```

Change the existing import line at the top of the file:

```ts
import { jobTarget } from "@/lib/format";
```

to:

```ts
import { formatDateTime, jobTarget } from "@/lib/format";
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix app/ui run test:unit -- format`
Expected: FAIL — `formatDateTime is not a function` (or a TypeScript error naming it undefined).

- [ ] **Step 3: Implement `formatDateTime`**

Add to `app/ui/src/lib/format.ts` (near the top, after the imports):

```ts
export const DEFAULT_TIME_ZONE = "America/Costa_Rica";

export function formatDateTime(iso: string | null, timeZone: string = DEFAULT_TIME_ZONE): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("es-CR", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone,
  }).format(date);
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm --prefix app/ui run test:unit -- format`
Expected: PASS (all `formatDateTime` and existing `jobTarget` tests green).

- [ ] **Step 5: Commit**

```bash
git add app/ui/src/lib/format.ts app/ui/src/lib/format.spec.ts
git commit -m "$(cat <<'EOF'
feat(ui): add formatDateTime for readable, timezone-aware timestamps

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Wire readable dates and a timezone picker into SystemView

**Files:**
- Modify: `app/ui/src/views/SystemView.vue`

**Interfaces:**
- Consumes: `formatDateTime(iso, timeZone?)`, `DEFAULT_TIME_ZONE` from Task 1 (`@/lib/format`).

This view has no existing `*.spec.ts` (it's a page-level component, like `ClipDrawer.vue`, `LibraryView.vue`); verify it manually with the dev server rather than a unit test, matching how those sibling views are already covered (or not) in this codebase.

- [ ] **Step 1: Add the timezone preference and wire the three timestamp spots**

In `app/ui/src/views/SystemView.vue`, change the import line:

```ts
import { onMounted, ref } from "vue";
```

to:

```ts
import { computed, onMounted, ref } from "vue";
```

Add to the imports block:

```ts
import { DEFAULT_TIME_ZONE, formatDateTime, jobTarget } from "@/lib/format";
```

(replacing the existing `import { jobTarget } from "@/lib/format";` line.)

Add, near the other `ref` declarations at the top of `<script setup>`:

```ts
const TIMEZONE_KEY = "manager-timezone";
const ZONE_OPTIONS = computed(() => {
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const options = [
    { value: "America/Costa_Rica", label: "Costa Rica" },
    { value: "UTC", label: "UTC" },
    { value: browserZone, label: `Browser (${browserZone})` },
  ];
  return options.filter(
    (option, index) => options.findIndex((candidate) => candidate.value === option.value) === index,
  );
});
const timezone = ref(DEFAULT_TIME_ZONE);

function rememberTimezone(next: string): void {
  timezone.value = next;
  try {
    localStorage.setItem(TIMEZONE_KEY, next);
  } catch {
    // Private windows and blocked site data make storage throw; the choice
    // simply does not survive a reload.
  }
}
```

In the existing `onMounted(async () => { ... })`, add before `await Promise.all(...)`:

```ts
  try {
    const stored = localStorage.getItem(TIMEZONE_KEY);
    if (stored) timezone.value = stored;
  } catch {
    // Same fallback as above: keep the default when storage is unavailable.
  }
```

Add a timezone picker to the template, inside the "Recent jobs" `<article>`'s header row (next to the existing "Refresh" button), so it reads:

```html
    <article class="panel flex flex-col gap-3">
      <div class="flex items-center justify-between gap-3">
        <h2 class="font-semibold">Recent jobs</h2>
        <div class="flex items-center gap-2">
          <label class="flex items-center gap-2 text-xs text-muted">
            <span>Timezone</span>
            <select
              class="field w-auto"
              :value="timezone"
              @change="rememberTimezone(($event.target as HTMLSelectElement).value)"
            >
              <option v-for="zone in ZONE_OPTIONS" :key="zone.value" :value="zone.value">
                {{ zone.label }}
              </option>
            </select>
          </label>
          <button type="button" class="btn px-2 py-1 text-xs" @click="refreshJobs">Refresh</button>
        </div>
      </div>
```

Replace the three raw-timestamp interpolations:

```html
          <span class="text-xs text-muted">{{ entry.created_at }}</span>
```
→
```html
          <span class="text-xs text-muted">{{ formatDateTime(entry.created_at, timezone) }}</span>
```

```html
              <td class="py-2 pr-3 text-xs text-muted">{{ job.created_at || "—" }}</td>
              <td class="py-2 pr-3 text-xs text-muted">{{ job.finished_at || "—" }}</td>
```
→
```html
              <td class="py-2 pr-3 text-xs text-muted">{{ formatDateTime(job.created_at, timezone) }}</td>
              <td class="py-2 pr-3 text-xs text-muted">{{ formatDateTime(job.finished_at, timezone) }}</td>
```

```html
              <td class="py-2 pr-3 text-xs whitespace-nowrap text-muted">{{ entry.timestamp }}</td>
```
→
```html
              <td class="py-2 pr-3 text-xs whitespace-nowrap text-muted">
                {{ formatDateTime(entry.timestamp, timezone) }}
              </td>
```

- [ ] **Step 2: Verify manually**

Run:
```bash
npm --prefix app/ui run dev
```
Open the Worker's System view in the browser. Confirm: trash/job/log timestamps render as `14 ene 2026, 10:30 p. m.`-style text instead of raw ISO strings; the timezone `<select>` shows Costa Rica/UTC/Browser; switching it re-renders every timestamp immediately; reloading the page keeps the picked zone (persisted in `localStorage`).

- [ ] **Step 3: Typecheck**

Run: `npm --prefix app/ui run build`
Expected: PASS (`vue-tsc --noEmit` succeeds, then the Vite build completes).

- [ ] **Step 4: Commit**

```bash
git add app/ui/src/views/SystemView.vue
git commit -m "$(cat <<'EOF'
feat(ui): show System-view timestamps in readable, configurable local time

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Stream a clip's source file for preview

**Files:**
- Modify: `app/src/cinema_collections_worker/manager_web.py`
- Test: `tests/worker/test_library_manager_web.py`

**Interfaces:**
- Produces: `GET /manager/clips/{clip_id}/source` — 200/206 with the raw file bytes (session-gated, Range-aware), 404 when the clip or its file is missing.
- Consumes: `LibraryManager._clip_row(clip_id)` and `LibraryManager._source_path(row)` (existing private methods; `delete_confirmation` in this same file already calls `_clip_row` directly, so this follows an established in-module precedent rather than introducing a new one).

- [ ] **Step 1: Write the failing tests**

Add to `tests/worker/test_library_manager_web.py`:

```python
def test_manager_clip_source_route_requires_a_session(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    clip_id = _seed_catalogued_clip(client)

    response = TestClient(_app(tmp_path)).get(f"/manager/clips/{clip_id}/source")

    assert response.status_code == 401


def test_manager_clip_source_route_streams_the_file_with_range_support(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    clip_id = _seed_catalogued_clip(client)

    full = client.get(f"/manager/clips/{clip_id}/source")
    assert full.status_code == 200
    assert full.content == b"clip-bytes"

    ranged = client.get(f"/manager/clips/{clip_id}/source", headers={"Range": "bytes=0-3"})
    assert ranged.status_code == 206
    assert ranged.content == b"clip"
    assert ranged.headers["content-range"] == "bytes 0-3/10"


def test_manager_clip_source_route_404s_for_an_unknown_clip(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _manager_session(client)

    assert client.get("/manager/clips/not-a-real-clip/source").status_code == 404
```

(`_seed_catalogued_clip` already establishes a session cookie on `client` as a side effect of calling `_manager_session` internally — see its existing definition later in the same file — so the two authenticated tests above need no separate `_manager_session(client)` call, matching `test_manager_clips_route_returns_the_catalog_as_json`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/worker/test_library_manager_web.py -k clip_source -v`
Expected: FAIL — 404 "Not Found" (no such route yet) on all three.

- [ ] **Step 3: Implement the route**

In `app/src/cinema_collections_worker/manager_web.py`, change:

```python
from fastapi.responses import HTMLResponse
```

to:

```python
from fastapi.responses import FileResponse, HTMLResponse
```

Add, immediately after the existing `list_manager_clips` route (before `list_manager_collections`):

```python
    @app.get("/manager/clips/{clip_id}/source", include_in_schema=False)
    def clip_source(request: Request, clip_id: str) -> FileResponse:
        if _valid_session(request) is None:
            raise HTTPException(status_code=401, detail="Library Manager session required")
        manager: LibraryManager = request.app.state.library_manager
        try:
            row = manager._clip_row(clip_id)
            path = manager._source_path(row)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="clip source is unavailable") from exc
        return FileResponse(path, filename=path.name)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/worker/test_library_manager_web.py -k clip_source -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full worker test file to check for regressions**

Run: `uv run pytest tests/worker/test_library_manager_web.py -v`
Expected: PASS (all tests, old and new).

- [ ] **Step 6: Commit**

```bash
git add app/src/cinema_collections_worker/manager_web.py tests/worker/test_library_manager_web.py
git commit -m "$(cat <<'EOF'
feat(worker): stream a clip's source file for browser preview

GET /manager/clips/{id}/source is session-gated and Range-aware
(Starlette's FileResponse already implements Range), so a <video>
element can seek without downloading the whole file.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `_EditBody` validation and `LibraryManager.request_edit`

**Files:**
- Modify: `app/src/cinema_collections_worker/manager_web.py`
- Modify: `app/src/cinema_collections_worker/library_manager.py`
- Test: `tests/worker/test_library_manager_web.py`

**Interfaces:**
- Produces: `LibraryManager.request_edit(clip_id: str | UUID, trim_start_seconds: float, trim_end_seconds: float, crop: dict[str, int] | None) -> AuditEvent`. Raises `KeyError` for an unknown clip, `ValueError` for an invalid trim range or an out-of-bounds/unscanned crop. Enqueues a `JobRecord(kind="edit", ...)` whose `profile_settings` is `{"trim_start_seconds": float, "trim_end_seconds": float, "crop": {"x": int, "y": int, "width": int, "height": int} | None}` — Task 5 consumes exactly this shape.
- Produces: `_EditBody` and `_CropBody` Pydantic models in `manager_web.py` (structural validation only: trim ordering, non-negative/positive fields). Task 6 consumes `_EditBody`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/worker/test_library_manager_web.py`:

```python
def test_request_edit_rejects_a_trim_range_outside_the_clip_duration(tmp_path: Path) -> None:
    from cinema_collections_worker.library_manager import LibraryManager
    from cinema_collections_worker.paths import SafePathResolver
    from cinema_collections_worker.database import Database

    db = Database.create(str(tmp_path / "worker.sqlite3"))
    resolver = SafePathResolver(
        {
            RootKey.SOURCE: tmp_path / "source",
            RootKey.COMPILED: tmp_path / "compiled",
            RootKey.TEMP: tmp_path / "temp",
            RootKey.ASSETS: tmp_path / "assets",
        }
    )
    for root in resolver.roots.values():
        root.mkdir(parents=True, exist_ok=True)
    with db.connection:
        db.connection.execute(
            "INSERT INTO clips(id,collection_id,state,relative_source_path,relative_output_path,"
            "duration_seconds,output_available,metadata,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "77777777-7777-7777-7777-777777777777",
                "films",
                "ready",
                "films/feature.mp4",
                "films/feature.mp4",
                10.0,
                0,
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
    manager = LibraryManager(db, resolver)

    import pytest

    with pytest.raises(ValueError, match="duration"):
        manager.request_edit("77777777-7777-7777-7777-777777777777", 0.0, 20.0, None)
    with pytest.raises(ValueError, match="after"):
        manager.request_edit("77777777-7777-7777-7777-777777777777", 5.0, 1.0, None)
    with pytest.raises(ValueError, match="scanned"):
        manager.request_edit(
            "77777777-7777-7777-7777-777777777777",
            0.0,
            5.0,
            {"x": 0, "y": 0, "width": 100, "height": 100},
        )


def test_manager_edit_route_queues_a_trim_and_crop_job(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "INSERT INTO clips(id,collection_id,state,relative_source_path,relative_output_path,"
            "duration_seconds,output_available,metadata,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "66666666-6666-6666-6666-666666666666",
                "films",
                "ready",
                "films/feature.mp4",
                "films/66666666-6666-6666-6666-666666666666.mp4",
                20.0,
                1,
                json.dumps({"width": 1920, "height": 1080}),
                "2026-01-01T00:00:00+00:00",
            ),
        )
    csrf = _manager_session(client)

    queued = client.post(
        "/manager/clips/66666666-6666-6666-6666-666666666666/edit",
        headers={"X-CSRF-Token": csrf},
        json={
            "trim_start_seconds": 2,
            "trim_end_seconds": 10,
            "crop": {"x": 0, "y": 0, "width": 1281, "height": 721},
        },
    )

    assert queued.status_code == 202
    job_id = queued.json()["details"]["job_id"]
    job = client.get(f"/manager/jobs/{job_id}").json()
    assert job["state"] in {"queued", "running"}


def test_manager_edit_route_requires_the_clip_to_be_scanned_before_cropping(tmp_path: Path) -> None:
    # duration_seconds is set to 10 (not 0, as a bare upload would leave it) so
    # the trim range passes before the crop/scan check is what actually fires.
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    with client.app.state.database.connection:
        client.app.state.database.connection.execute(
            "INSERT INTO clips(id,collection_id,state,relative_source_path,relative_output_path,"
            "duration_seconds,output_available,metadata,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "88888888-8888-8888-8888-888888888888",
                "films",
                "discovered",
                "films/unscanned.mp4",
                "films/unscanned.mp4",
                10.0,
                0,
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
    csrf = _manager_session(client)

    rejected = client.post(
        "/manager/clips/88888888-8888-8888-8888-888888888888/edit",
        headers={"X-CSRF-Token": csrf},
        json={
            "trim_start_seconds": 0,
            "trim_end_seconds": 1,
            "crop": {"x": 0, "y": 0, "width": 100, "height": 100},
        },
    )

    assert rejected.status_code == 422
    assert "scanned" in rejected.json()["message"]


def test_manager_edit_route_rejects_trim_end_before_trim_start(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    clip_id = _seed_catalogued_clip(client)
    csrf = _manager_session(client)

    rejected = client.post(
        f"/manager/clips/{clip_id}/edit",
        headers={"X-CSRF-Token": csrf},
        json={"trim_start_seconds": 5, "trim_end_seconds": 1},
    )

    assert rejected.status_code == 422


def test_manager_edit_route_requires_csrf(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_collection(client)
    clip_id = _seed_catalogued_clip(client)

    rejected = client.post(
        f"/manager/clips/{clip_id}/edit",
        json={"trim_start_seconds": 0, "trim_end_seconds": 1},
    )

    assert rejected.status_code == 403


def test_manager_edit_route_404s_for_an_unknown_clip(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    csrf = _manager_session(client)

    rejected = client.post(
        "/manager/clips/not-a-real-clip/edit",
        headers={"X-CSRF-Token": csrf},
        json={"trim_start_seconds": 0, "trim_end_seconds": 1},
    )

    assert rejected.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/worker/test_library_manager_web.py -k "request_edit or manager_edit_route" -v`
Expected: FAIL — `AttributeError: 'LibraryManager' object has no attribute 'request_edit'` and 404s for the not-yet-existing `/manager/clips/{id}/edit` route.

- [ ] **Step 3: Implement `LibraryManager.request_edit`**

In `app/src/cinema_collections_worker/library_manager.py`, add after `request_recompile` (before `move_to_trash`):

```python
    def request_edit(
        self,
        clip_id: str | UUID,
        trim_start_seconds: float,
        trim_end_seconds: float,
        crop: dict[str, int] | None,
    ) -> AuditEvent:
        row = self._clip_row(clip_id)
        duration = float(row["duration_seconds"] or 0)
        if trim_end_seconds <= trim_start_seconds:
            raise ValueError("trim end must come after trim start")
        if trim_start_seconds < 0 or trim_end_seconds > duration:
            raise ValueError("trim range must stay within the clip's duration")
        if crop is not None:
            metadata = json.loads(row["metadata"] or "{}")
            width, height = metadata.get("width"), metadata.get("height")
            if not isinstance(width, int) or not isinstance(height, int):
                raise ValueError("clip must be scanned before it can be cropped")
            if (
                crop["x"] < 0
                or crop["y"] < 0
                or crop["x"] + crop["width"] > width
                or crop["y"] + crop["height"] > height
            ):
                raise ValueError("crop rectangle must stay within the source frame")
            crop = {
                "x": crop["x"],
                "y": crop["y"],
                "width": crop["width"] - (crop["width"] % 2),
                "height": crop["height"] - (crop["height"] % 2),
            }
            if crop["width"] <= 0 or crop["height"] <= 0:
                raise ValueError("crop rectangle is too small")
        job_id = str(uuid.uuid4())
        job = self.queue.enqueue(
            JobRecord(
                id=job_id,
                kind="edit",
                collection_id=str(row["collection_id"]),
                clip_id=str(clip_id),
                source_relative_path=str(row["relative_source_path"]),
                output_relative_path=str(row["relative_output_path"] or f"edit/{job_id}.request"),
                source_fingerprint="library-request",
                profile_fingerprint="library-request",
                profile_settings={
                    "trim_start_seconds": trim_start_seconds,
                    "trim_end_seconds": trim_end_seconds,
                    "crop": crop,
                },
                duration_seconds=duration,
                progress=JobProgress(stage=JobStage.QUEUED, percent=0, eta_seconds=None),
            )
        )
        return self._audit(
            "library.edit_requested",
            str(clip_id),
            {"collection_id": row["collection_id"], "job_id": job.id},
        )
```

`JobStage` is already imported in this file's existing `from .jobs import CompileRequest, JobProgress, JobRecord, JobService, JobStage` line (used by `request_scan`/`request_library_scan`), so no import change is needed here.

- [ ] **Step 4: Implement `_EditBody`/`_CropBody` and the route**

In `app/src/cinema_collections_worker/manager_web.py`, change:

```python
from pydantic import BaseModel, ConfigDict, Field
```

to:

```python
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
```

Add, after `_TargetBody`:

```python
class _CropBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class _EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trim_start_seconds: float = Field(ge=0)
    trim_end_seconds: float = Field(gt=0)
    crop: _CropBody | None = None

    @field_validator("trim_end_seconds")
    @classmethod
    def _trim_end_after_start(cls, value: float, info: ValidationInfo) -> float:
        start = info.data.get("trim_start_seconds")
        if start is not None and value <= start:
            raise ValueError("trim_end_seconds must be greater than trim_start_seconds")
        return value
```

Add the route immediately after `recompile` (before `trash`):

```python
    @app.post("/manager/clips/{clip_id}/edit", status_code=202, include_in_schema=False)
    def edit_clip(
        request: Request,
        clip_id: str,
        payload: _EditBody,
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Any:
        manager = _require_action(request, csrf)
        crop = payload.crop.model_dump() if payload.crop else None
        try:
            return _dump(
                manager.request_edit(
                    clip_id, payload.trim_start_seconds, payload.trim_end_seconds, crop
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="clip not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/worker/test_library_manager_web.py -k "request_edit or manager_edit_route" -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Run the full worker test suite to check for regressions**

Run: `uv run pytest tests/worker -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/src/cinema_collections_worker/manager_web.py app/src/cinema_collections_worker/library_manager.py tests/worker/test_library_manager_web.py
git commit -m "$(cat <<'EOF'
feat(worker): validate and queue trim/crop edit requests

POST /manager/clips/{id}/edit validates the trim range against the
clip's duration and the crop rectangle against its last-scanned frame
size (rounding to even dimensions for libx264), then enqueues a
kind="edit" job. Execution lands in a follow-up commit.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Execute the `edit` job — trim/crop and replace the source in place

**Files:**
- Modify: `app/src/cinema_collections_worker/jobs.py`
- Test: `tests/worker/test_edit_job.py` (new)

**Interfaces:**
- Consumes: `JobRecord.profile_settings` shaped `{"trim_start_seconds": float, "trim_end_seconds": float, "crop": {"x", "y", "width", "height"} | None}` (from Task 4).
- Consumes: `JobWorker._run_process(job, command, timeout_seconds) -> tuple[bool, bool, str]`, `JobWorker._temporary_directory(job_id) -> Path`, `JobWorker._cleanup(temp_dir) -> None`, `JobWorker._finish(job, state, error=None) -> JobRecord`, module-level `_failure_reason(output, fallback) -> str` and `_now() -> datetime` (all pre-existing in this file).
- Produces: `JobWorker._run_edit_job(job: JobRecord) -> JobRunResult`, dispatched from `run_once()` for `job.kind == "edit"`. On success: the clip's source file is replaced in place, `clips.state = "discovered"`, `clips.duration_seconds` updated from the re-probed file, `clips.metadata.width/height` updated, any prior `failed_reason` cleared. On failure: `clips.state = "failed"`, `clips.metadata.failed_reason` set, and the original source file is untouched.

- [ ] **Step 1: Write the failing tests**

Create `tests/worker/test_edit_job.py`:

```python
"""Execution tests for the "edit" job kind: trim/crop the source, in place."""

import json
from pathlib import Path

from cinema_collections_worker.jobs import JobProgress, JobRecord, JobStage, JobState, JobWorker
from cinema_collections_worker.paths import RootKey
from cinema_collections_worker.probe import MediaProbeResult
from cinema_collections_worker.queue import PersistentJobQueue
from test_queue import _configured_service


class _EditingProcess:
    pid = 4242
    returncode = 0

    def __init__(self, output_path: Path, content: bytes = b"edited") -> None:
        self._output_path = output_path
        self._content = content

    def communicate(self, timeout):
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_path.write_bytes(self._content)
        return ("out_time_ms=5000000\nprogress=end\n", "")


class _FailingProcess:
    pid = 4243
    returncode = 1

    def communicate(self, timeout):
        return ("", "ffmpeg: invalid trim range")


class _EditedProbe:
    def probe(self, path: Path) -> MediaProbeResult:
        return MediaProbeResult(
            valid=True, duration_seconds=5.0, width=1080, height=1920, frame_rate=30, has_audio=True
        )


def _enqueue_edit(db, *, trim_start: float = 0.0, trim_end: float = 5.0, crop=None) -> JobRecord:
    job = JobRecord(
        id="10000000-0000-0000-0000-000000000001",
        kind="edit",
        collection_id="films",
        clip_id="00000000-0000-0000-0000-000000000001",
        source_relative_path="films/example.mp4",
        output_relative_path="films/example.mp4",
        source_fingerprint="library-request",
        profile_fingerprint="library-request",
        profile_settings={
            "trim_start_seconds": trim_start,
            "trim_end_seconds": trim_end,
            "crop": crop,
        },
        duration_seconds=10,
        progress=JobProgress(stage=JobStage.QUEUED, percent=0),
    )
    return PersistentJobQueue(db).enqueue(job)


def test_edit_job_trims_and_replaces_the_source_in_place(tmp_path):
    db, resolver, _service = _configured_service(tmp_path)
    job = _enqueue_edit(db)

    def process_factory(command, **_kwargs):
        return _EditingProcess(Path(command[-1]))

    result = JobWorker(
        db, resolver, probe_client=_EditedProbe(), process_factory=process_factory
    ).run_once()

    source = resolver.resolve(RootKey.SOURCE.value, "films/example.mp4")
    assert result is not None and result.job.state is JobState.SUCCEEDED
    assert source.read_bytes() == b"edited"
    assert not (resolver.roots[RootKey.TEMP] / job.id).exists()

    row = db.connection.execute(
        "SELECT state, duration_seconds, metadata FROM clips WHERE id=?", (job.clip_id,)
    ).fetchone()
    assert row["state"] == "discovered"
    assert row["duration_seconds"] == 5.0
    metadata = json.loads(row["metadata"])
    assert metadata["width"] == 1080
    assert metadata["height"] == 1920


def test_edit_job_passes_trim_and_crop_to_ffmpeg(tmp_path):
    db, resolver, _service = _configured_service(tmp_path)
    _enqueue_edit(db, trim_start=1.5, trim_end=6.5, crop={"x": 10, "y": 20, "width": 800, "height": 600})
    commands = []

    def process_factory(command, **_kwargs):
        commands.append(command)
        return _EditingProcess(Path(command[-1]))

    JobWorker(db, resolver, probe_client=_EditedProbe(), process_factory=process_factory).run_once()

    command = commands[0]
    assert command[command.index("-ss") + 1] == "1.500"
    assert command[command.index("-to") + 1] == "6.500"
    assert command[command.index("-vf") + 1] == "crop=800:600:10:20"


def test_edit_job_failure_leaves_the_original_source_untouched(tmp_path):
    db, resolver, _service = _configured_service(tmp_path)
    job = _enqueue_edit(db)
    source = resolver.resolve(RootKey.SOURCE.value, "films/example.mp4")
    original_bytes = source.read_bytes()

    result = JobWorker(
        db,
        resolver,
        probe_client=_EditedProbe(),
        process_factory=lambda command, **_kwargs: _FailingProcess(),
    ).run_once()

    assert result is not None and result.job.state is JobState.FAILED
    assert source.read_bytes() == original_bytes
    row = db.connection.execute(
        "SELECT state, metadata FROM clips WHERE id=?", (job.clip_id,)
    ).fetchone()
    assert row["state"] == "failed"
    assert "failed_reason" in json.loads(row["metadata"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/worker/test_edit_job.py -v`
Expected: FAIL — `result.job.state` is `FAILED` with "unsupported job kind: edit" (the dispatch branch doesn't exist yet).

- [ ] **Step 3: Implement `_run_edit_job` and wire dispatch**

In `app/src/cinema_collections_worker/jobs.py`, change:

```python
from .paths import RootKey, SafePathResolver, validate_collection_id, validate_relative_path
```

to:

```python
from .models import ClipState
from .paths import RootKey, SafePathResolver, validate_collection_id, validate_relative_path
```

Add these methods to `JobWorker`, after `_publish` (before `run_once`):

```python
    def _edit_command(
        self,
        source: Path,
        output: Path,
        trim_start: float,
        trim_end: float,
        crop: dict[str, int] | None,
    ) -> list[str]:
        command = [
            self.command_builder.executable,
            "-y",
            "-ss",
            f"{trim_start:.3f}",
            "-to",
            f"{trim_end:.3f}",
            "-i",
            str(source),
            "-progress",
            "pipe:1",
            "-nostats",
        ]
        if crop is not None:
            command += ["-vf", f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}"]
        command += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output),
        ]
        return command

    def _publish_edit(self, temporary_output: Path, source: Path) -> None:
        source_root = self.resolver.roots[RootKey.SOURCE]
        resolved_source = source.resolve(strict=False)
        try:
            resolved_source.relative_to(source_root)
        except ValueError as exc:
            raise ValueError("edited clip escapes the source root") from exc
        staging = resolved_source.parent / f".{resolved_source.name}.{uuid.uuid4().hex}.editing"
        shutil.copyfile(temporary_output, staging)
        try:
            os.replace(staging, resolved_source)
        finally:
            if staging.exists():
                staging.unlink()

    def _record_edit_failure(self, clip_id: str, error: str | None) -> None:
        if error is None:
            return
        row = self.db.connection.execute(
            "SELECT metadata FROM clips WHERE id=?", (clip_id,)
        ).fetchone()
        if row is None:
            return
        metadata = json.loads(str(row["metadata"]) or "{}")
        metadata["failed_reason"] = error[:500]
        with self.db.transaction():
            self.db.connection.execute(
                "UPDATE clips SET state=?, metadata=?, updated_at=? WHERE id=?",
                (ClipState.FAILED.value, json.dumps(metadata, sort_keys=True), _now().isoformat(), clip_id),
            )

    def _run_edit_job(self, job: JobRecord) -> JobRunResult:
        settings = job.profile_settings
        trim_start = float(settings.get("trim_start_seconds", 0.0))
        trim_end = float(settings.get("trim_end_seconds", 0.0))
        crop = settings.get("crop")
        temp_dir = self._temporary_directory(job.id)
        try:
            temp_dir.mkdir(parents=True, exist_ok=False)
            source = self.resolver.resolve(RootKey.SOURCE.value, job.source_relative_path)
            if not source.is_file():
                raise ValueError("clip source is unavailable")
            temporary_output = temp_dir / f"edited{source.suffix}"
            command = self._edit_command(source, temporary_output, trim_start, trim_end, crop)
            timeout_seconds = max(60.0, (trim_end - trim_start) * 4)
            running = self.queue.update(
                job.model_copy(
                    update={
                        "duration_seconds": max(0.0, trim_end - trim_start),
                        "progress": JobProgress(stage=JobStage.ENCODING, percent=0),
                    }
                )
            )
            success, cancelled, output = self._run_process(running, command, timeout_seconds)
            if cancelled:
                return JobRunResult(job=self._finish(job, JobState.CANCELLED))
            if not success or not temporary_output.is_file():
                raise ValueError(_failure_reason(output, "clip edit failed"))
            probe = self.probe_client.probe(temporary_output)
            if not probe.valid:
                raise ValueError("edited clip failed validation")
            self._publish_edit(temporary_output, source)
            row = self.db.connection.execute(
                "SELECT metadata FROM clips WHERE id=?", (job.clip_id,)
            ).fetchone()
            metadata = json.loads(str(row["metadata"])) if row is not None else {}
            metadata.pop("failed_reason", None)
            metadata["width"] = probe.width
            metadata["height"] = probe.height
            with self.db.transaction():
                self.db.connection.execute(
                    "UPDATE clips SET state=?, duration_seconds=?, metadata=?, updated_at=? WHERE id=?",
                    (
                        ClipState.DISCOVERED.value,
                        probe.duration_seconds,
                        json.dumps(metadata, sort_keys=True),
                        _now().isoformat(),
                        job.clip_id,
                    ),
                )
            self._record_log(
                "info", f"edit complete duration={probe.duration_seconds:.2f}", job.id
            )
            return JobRunResult(job=self._finish(job, JobState.SUCCEEDED))
        except Exception as exc:
            finished = self._finish(job, JobState.FAILED, str(exc)[:1000])
            self._record_edit_failure(job.clip_id, finished.error)
            return JobRunResult(job=finished)
        finally:
            self._cleanup(temp_dir)
```

In `run_once`, change:

```python
        if job.kind == "scan":
            return self._run_scan_job(job)
        if job.kind == "cleanup":
            return self._run_cleanup_job(job)
        if job.kind != "compile":
```

to:

```python
        if job.kind == "scan":
            return self._run_scan_job(job)
        if job.kind == "cleanup":
            return self._run_cleanup_job(job)
        if job.kind == "edit":
            return self._run_edit_job(job)
        if job.kind != "compile":
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/worker/test_edit_job.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full worker test suite to check for regressions**

Run: `uv run pytest tests/worker -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/src/cinema_collections_worker/jobs.py tests/worker/test_edit_job.py
git commit -m "$(cat <<'EOF'
feat(worker): execute trim/crop edit jobs and replace the source in place

ffmpeg re-encodes the requested range (and crop, if any) to a temp
file first; only once that succeeds does os.replace swap it into the
clip's existing source path, so a failed encode never loses the clip.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `cropMath` — pure geometry for the crop overlay

**Files:**
- Create: `app/ui/src/lib/cropMath.ts`
- Test: `app/ui/src/lib/cropMath.spec.ts`

**Interfaces:**
- Produces: `interface CropRect { x: number; y: number; width: number; height: number }`, `interface Size { width: number; height: number }`, `toSourceRect(displayRect: CropRect, displaySize: Size, naturalSize: Size): CropRect`, `roundToEven(rect: CropRect): CropRect`, `clampToBounds(rect: CropRect, bounds: Size): CropRect`, `applyAspectRatio(rect: CropRect, ratio: number, bounds: Size): CropRect`. Task 8 (`ClipEditor.vue`) imports all of these.

- [ ] **Step 1: Write the failing tests**

Create `app/ui/src/lib/cropMath.spec.ts`:

```ts
import { describe, expect, it } from "vitest";
import { applyAspectRatio, clampToBounds, toSourceRect } from "@/lib/cropMath";

describe("toSourceRect", () => {
  it("scales a rectangle drawn on a shrunk preview up to the source video's pixels", () => {
    const result = toSourceRect(
      { x: 10, y: 10, width: 100, height: 50 },
      { width: 200, height: 100 },
      { width: 1920, height: 960 },
    );
    expect(result).toEqual({ x: 96, y: 96, width: 960, height: 480 });
  });

  it("rounds odd dimensions down to even, as libx264 requires", () => {
    const result = toSourceRect(
      { x: 0, y: 0, width: 101, height: 51 },
      { width: 200, height: 100 },
      { width: 200, height: 100 },
    );
    expect(result.width % 2).toBe(0);
    expect(result.height % 2).toBe(0);
  });
});

describe("clampToBounds", () => {
  it("pulls a rectangle back inside its container instead of letting it overflow", () => {
    const result = clampToBounds(
      { x: 150, y: 150, width: 100, height: 100 },
      { width: 200, height: 200 },
    );
    expect(result).toEqual({ x: 100, y: 100, width: 100, height: 100 });
  });
});

describe("applyAspectRatio", () => {
  it("keeps a rectangle that already fits its aspect ratio", () => {
    const result = applyAspectRatio(
      { x: 0, y: 0, width: 160, height: 200 },
      16 / 9,
      { width: 400, height: 400 },
    );
    expect(result.width).toBe(160);
    expect(result.height).toBeCloseTo(90);
  });

  it("shrinks height when the ratio would push the rectangle past the bottom edge", () => {
    const result = applyAspectRatio(
      { x: 0, y: 320, width: 160, height: 90 },
      16 / 9,
      { width: 400, height: 400 },
    );
    expect(result.height).toBeCloseTo(80);
    expect(result.width).toBeCloseTo(142.22, 1);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix app/ui run test:unit -- cropMath`
Expected: FAIL — `Cannot find module '@/lib/cropMath'`.

- [ ] **Step 3: Implement `cropMath.ts`**

Create `app/ui/src/lib/cropMath.ts`:

```ts
export interface CropRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Size {
  width: number;
  height: number;
}

/** libx264 requires even width/height for 4:2:0 chroma subsampling. */
export function roundToEven(rect: CropRect): CropRect {
  return {
    x: rect.x,
    y: rect.y,
    width: rect.width - (rect.width % 2),
    height: rect.height - (rect.height % 2),
  };
}

/** Convert a crop rectangle drawn in on-screen pixels to source-video pixels. */
export function toSourceRect(displayRect: CropRect, displaySize: Size, naturalSize: Size): CropRect {
  const scaleX = naturalSize.width / displaySize.width;
  const scaleY = naturalSize.height / displaySize.height;
  return roundToEven({
    x: Math.round(displayRect.x * scaleX),
    y: Math.round(displayRect.y * scaleY),
    width: Math.round(displayRect.width * scaleX),
    height: Math.round(displayRect.height * scaleY),
  });
}

/** Clamp a rectangle so it never extends past its containing size. */
export function clampToBounds(rect: CropRect, bounds: Size): CropRect {
  const width = Math.min(rect.width, bounds.width);
  const height = Math.min(rect.height, bounds.height);
  const x = Math.min(Math.max(rect.x, 0), bounds.width - width);
  const y = Math.min(Math.max(rect.y, 0), bounds.height - height);
  return { x, y, width, height };
}

/**
 * Resize a rectangle to a width/height ratio, anchored at its top-left,
 * shrinking as needed to stay inside `bounds`.
 */
export function applyAspectRatio(rect: CropRect, ratio: number, bounds: Size): CropRect {
  let width = rect.width;
  let height = width / ratio;
  if (height > bounds.height - rect.y) {
    height = bounds.height - rect.y;
    width = height * ratio;
  }
  if (rect.x + width > bounds.width) {
    width = bounds.width - rect.x;
    height = width / ratio;
  }
  return clampToBounds({ x: rect.x, y: rect.y, width, height }, bounds);
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm --prefix app/ui run test:unit -- cropMath`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/ui/src/lib/cropMath.ts app/ui/src/lib/cropMath.spec.ts
git commit -m "$(cat <<'EOF'
feat(ui): add pure crop-rectangle geometry helpers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `ClipEditor.vue` — trim/crop UI with live preview

**Files:**
- Create: `app/ui/src/components/ClipEditor.vue`
- Modify: `app/ui/src/components/ClipDrawer.vue`

**Interfaces:**
- Consumes: `toSourceRect`, `clampToBounds`, `applyAspectRatio`, `CropRect`, `Size` (Task 6, `@/lib/cropMath`); `formatDateTime`/`formatDuration`/`sourceName` (existing `@/lib/format`); `apiFetch` (`@/composables/useApi`); `jobIdFrom`, `useJobs` (`@/composables/useJobs`); `Clip`, `ActionResult` (`@/types`); the `GET manager/clips/{id}/source` and `POST manager/clips/{id}/edit` routes from Tasks 3 and 4.
- Produces: `ClipEditor.vue` — `defineProps<{ clip: Clip }>()`, `defineEmits<{ close: []; changed: [] }>()`, matching `ClipDrawer.vue`'s existing shape exactly so `ClipDrawer.vue` can open it the same way it is itself opened from `LibraryView.vue`.

No `*.spec.ts` precedent exists for `ClipDrawer.vue` or other drawer/panel-level components in this codebase (they're verified manually via the dev server); `ClipEditor.vue` follows that same precedent, on top of the already-unit-tested `cropMath.ts`.

- [ ] **Step 1: Create `ClipEditor.vue`**

Create `app/ui/src/components/ClipEditor.vue`:

```vue
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { apiFetch } from "@/composables/useApi";
import { jobIdFrom, useJobs } from "@/composables/useJobs";
import { applyAspectRatio, clampToBounds, toSourceRect } from "@/lib/cropMath";
import type { CropRect, Size } from "@/lib/cropMath";
import { formatDuration, sourceName } from "@/lib/format";
import type { ActionResult, Clip } from "@/types";

const props = defineProps<{ clip: Clip }>();
const emit = defineEmits<{ close: []; changed: [] }>();

const { follow } = useJobs();

const ASPECT_PRESETS: { label: string; ratio: number | null }[] = [
  { label: "Free", ratio: null },
  { label: "16:9", ratio: 16 / 9 },
  { label: "9:16", ratio: 9 / 16 },
  { label: "1:1", ratio: 1 },
  { label: "4:3", ratio: 4 / 3 },
];

const video = ref<HTMLVideoElement | null>(null);
const closeButton = ref<HTMLButtonElement | null>(null);

const duration = computed(() => props.clip.duration_seconds || 0);
const trimStart = ref(0);
const trimEnd = ref(duration.value);
const cropEnabled = ref(false);
const aspectRatio = ref<number | null>(null);
const displaySize = ref<Size>({ width: 0, height: 0 });
const naturalSize = ref<Size>({ width: 0, height: 0 });
const cropRect = ref<CropRect>({ x: 0, y: 0, width: 0, height: 0 });

const status = ref("");
const failure = ref("");
const busy = ref(false);
const previewVersion = ref(0);

const sourceUrl = computed(() => `manager/clips/${props.clip.id}/source?v=${previewVersion.value}`);

function resetCropRect(): void {
  const { width, height } = displaySize.value;
  cropRect.value = clampToBounds(
    { x: width * 0.1, y: height * 0.1, width: width * 0.8, height: height * 0.8 },
    { width, height },
  );
}

function updateDisplaySize(): void {
  const element = video.value;
  if (!element) return;
  const rect = element.getBoundingClientRect();
  displaySize.value = { width: rect.width, height: rect.height };
  if (cropRect.value.width === 0) resetCropRect();
}

function onLoadedMetadata(): void {
  const element = video.value;
  if (!element) return;
  naturalSize.value = { width: element.videoWidth, height: element.videoHeight };
  updateDisplaySize();
}

function toggleCrop(next: boolean): void {
  cropEnabled.value = next;
  if (next && cropRect.value.width === 0) resetCropRect();
}

function pickAspect(ratio: number | null): void {
  aspectRatio.value = ratio;
  if (ratio !== null) {
    cropRect.value = applyAspectRatio(cropRect.value, ratio, displaySize.value);
  }
}

function seekTo(seconds: number): void {
  if (video.value) video.value.currentTime = seconds;
}

function onTrimStartInput(value: number): void {
  trimStart.value = Math.min(value, trimEnd.value - 0.1);
  seekTo(trimStart.value);
}

function onTrimEndInput(value: number): void {
  trimEnd.value = Math.max(value, trimStart.value + 0.1);
  seekTo(trimEnd.value);
}

type DragMode = "move" | "resize";
let dragMode: DragMode | null = null;
let dragOrigin = { x: 0, y: 0 };
let dragStartRect: CropRect = { x: 0, y: 0, width: 0, height: 0 };

function startDrag(event: PointerEvent, mode: DragMode): void {
  dragMode = mode;
  dragOrigin = { x: event.clientX, y: event.clientY };
  dragStartRect = { ...cropRect.value };
  (event.target as HTMLElement).setPointerCapture(event.pointerId);
}

function onDragMove(event: PointerEvent): void {
  if (dragMode === null) return;
  const deltaX = event.clientX - dragOrigin.x;
  const deltaY = event.clientY - dragOrigin.y;
  if (dragMode === "move") {
    cropRect.value = clampToBounds(
      { ...dragStartRect, x: dragStartRect.x + deltaX, y: dragStartRect.y + deltaY },
      displaySize.value,
    );
    return;
  }
  const proposed: CropRect = {
    ...dragStartRect,
    width: Math.max(20, dragStartRect.width + deltaX),
    height: Math.max(20, dragStartRect.height + deltaY),
  };
  cropRect.value =
    aspectRatio.value !== null
      ? applyAspectRatio(proposed, aspectRatio.value, displaySize.value)
      : clampToBounds(proposed, displaySize.value);
}

function endDrag(): void {
  dragMode = null;
}

async function apply(): Promise<void> {
  busy.value = true;
  failure.value = "";
  status.value = "Encoding…";
  try {
    const crop = cropEnabled.value
      ? toSourceRect(cropRect.value, displaySize.value, naturalSize.value)
      : null;
    const response = await apiFetch<ActionResult>(`manager/clips/${props.clip.id}/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        trim_start_seconds: trimStart.value,
        trim_end_seconds: trimEnd.value,
        crop,
      }),
    });
    const jobId = jobIdFrom(response?.details);
    if (jobId) {
      const job = await follow(jobId, (update) => {
        const percent = Math.round(update.progress?.percent ?? 0);
        status.value = `${update.progress?.stage ?? update.state} ${percent}%`;
      });
      if (job?.state === "failed") throw new Error(job.error || "Edit failed.");
    }
    previewVersion.value += 1;
    status.value = "Clip updated.";
    emit("changed");
  } catch (cause) {
    failure.value = cause instanceof Error ? cause.message : String(cause);
    status.value = "";
  } finally {
    busy.value = false;
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}

let resizeObserver: ResizeObserver | null = null;

onMounted(async () => {
  document.addEventListener("keydown", onKeydown);
  await nextTick();
  closeButton.value?.focus();
  if (video.value) {
    resizeObserver = new ResizeObserver(() => updateDisplaySize());
    resizeObserver.observe(video.value);
  }
});

onUnmounted(() => {
  document.removeEventListener("keydown", onKeydown);
  resizeObserver?.disconnect();
});

watch(
  () => props.clip.id,
  () => {
    trimStart.value = 0;
    trimEnd.value = duration.value;
    cropEnabled.value = false;
    aspectRatio.value = null;
    previewVersion.value = 0;
  },
);
</script>

<template>
  <div class="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4">
    <div
      role="dialog"
      aria-modal="true"
      :aria-label="`Trim and crop ${sourceName(clip)}`"
      class="flex max-h-full w-full max-w-3xl flex-col gap-4 overflow-y-auto rounded-panel border border-line bg-surface p-4"
    >
      <header class="flex items-start justify-between gap-3">
        <div>
          <h2 class="font-semibold">Trim / Crop — {{ sourceName(clip) }}</h2>
          <p class="text-xs text-muted">{{ formatDuration(duration) }} original length</p>
        </div>
        <button ref="closeButton" type="button" class="btn px-2 py-1" @click="emit('close')">
          Close
        </button>
      </header>

      <p v-if="failure" role="alert" class="rounded-lg bg-danger/15 p-3 text-sm text-danger">
        {{ failure }}
      </p>
      <p v-else-if="status" role="status" class="text-sm text-muted">{{ status }}</p>

      <div
        class="relative mx-auto w-full max-w-xl select-none"
        @pointermove="onDragMove"
        @pointerup="endDrag"
        @pointercancel="endDrag"
      >
        <video
          ref="video"
          :src="sourceUrl"
          class="w-full rounded-lg bg-black"
          controls
          muted
          @loadedmetadata="onLoadedMetadata"
        />
        <div
          v-if="cropEnabled"
          class="absolute border-2 border-accent bg-accent/10"
          :style="{
            left: `${cropRect.x}px`,
            top: `${cropRect.y}px`,
            width: `${cropRect.width}px`,
            height: `${cropRect.height}px`,
          }"
          @pointerdown="startDrag($event, 'move')"
        >
          <div
            class="absolute right-0 bottom-0 size-4 translate-x-1/2 translate-y-1/2 cursor-nwse-resize rounded-full bg-accent"
            @pointerdown.stop="startDrag($event, 'resize')"
          />
        </div>
      </div>

      <section class="flex flex-col gap-2">
        <h3 class="text-xs tracking-widest text-muted uppercase">Trim</h3>
        <label class="flex items-center gap-2 text-sm">
          <span class="w-12 text-muted">Start</span>
          <input
            type="range"
            min="0"
            :max="duration"
            step="0.1"
            :value="trimStart"
            class="flex-1"
            @input="onTrimStartInput(Number(($event.target as HTMLInputElement).value))"
          />
          <span class="w-14 text-right text-xs text-muted">{{ formatDuration(trimStart) }}</span>
        </label>
        <label class="flex items-center gap-2 text-sm">
          <span class="w-12 text-muted">End</span>
          <input
            type="range"
            min="0"
            :max="duration"
            step="0.1"
            :value="trimEnd"
            class="flex-1"
            @input="onTrimEndInput(Number(($event.target as HTMLInputElement).value))"
          />
          <span class="w-14 text-right text-xs text-muted">{{ formatDuration(trimEnd) }}</span>
        </label>
      </section>

      <section class="flex flex-col gap-2">
        <h3 class="text-xs tracking-widest text-muted uppercase">Crop</h3>
        <label class="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            :checked="cropEnabled"
            @change="toggleCrop(($event.target as HTMLInputElement).checked)"
          />
          Crop this clip
        </label>
        <div v-if="cropEnabled" class="flex flex-wrap gap-2">
          <button
            v-for="preset in ASPECT_PRESETS"
            :key="preset.label"
            type="button"
            class="btn px-2 py-1 text-xs"
            :class="aspectRatio === preset.ratio && 'border-accent text-accent'"
            @click="pickAspect(preset.ratio)"
          >
            {{ preset.label }}
          </button>
        </div>
      </section>

      <div class="flex justify-end gap-2">
        <button type="button" class="btn" :disabled="busy" @click="emit('close')">Cancel</button>
        <button type="button" class="btn-primary" :disabled="busy" @click="apply">Apply</button>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 2: Wire the "Trim / Crop" button into `ClipDrawer.vue`**

In `app/ui/src/components/ClipDrawer.vue`, change:

```ts
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import StateBadge from "@/components/StateBadge.vue";
```

to:

```ts
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import ClipEditor from "@/components/ClipEditor.vue";
import StateBadge from "@/components/StateBadge.vue";
```

Add near the other `ref` declarations (after `const deleteTarget = ref("source");`):

```ts
const editorOpen = ref(false);
```

Change the "Processing" section's button row:

```html
          <div class="flex gap-2">
            <button type="button" class="btn" :disabled="busy" @click="queued('scan')">
              Re-scan source
            </button>
            <button type="button" class="btn-primary" :disabled="busy" @click="queued('recompile')">
              Recompile
            </button>
          </div>
```

to:

```html
          <div class="flex gap-2">
            <button type="button" class="btn" :disabled="busy" @click="queued('scan')">
              Re-scan source
            </button>
            <button type="button" class="btn-primary" :disabled="busy" @click="queued('recompile')">
              Recompile
            </button>
            <button type="button" class="btn" :disabled="busy" @click="editorOpen = true">
              Trim / Crop
            </button>
          </div>
```

Add, right after the closing `</aside>` tag's sibling position — i.e. change the end of the template:

```html
    </aside>
  </div>
</template>
```

to:

```html
    </aside>
    <ClipEditor
      v-if="editorOpen"
      :clip="clip"
      @close="editorOpen = false"
      @changed="
        () => {
          editorOpen = false;
          emit('changed');
        }
      "
    />
  </div>
</template>
```

- [ ] **Step 3: Typecheck**

Run: `npm --prefix app/ui run build`
Expected: PASS.

- [ ] **Step 4: Verify manually**

Run:
```bash
npm --prefix app/ui run dev
```
In the Library Manager: open a catalogued clip's drawer, click "Trim / Crop". Confirm: the video preview loads and plays; dragging the Start/End sliders seeks the preview; checking "Crop this clip" shows a draggable, resizable rectangle over the video; dragging its body moves it, dragging its corner handle resizes it, and it never extends past the video frame; clicking an aspect-ratio button snaps its proportions; clicking Apply shows encoding progress and, on completion, the drawer's duration updates and the preview reloads the trimmed/cropped result. Trigger a failure (e.g. stop the Worker mid-encode, or submit an out-of-range trim by editing the request in devtools) and confirm the original file is unaffected and the clip does not disappear from the catalog.

- [ ] **Step 5: Commit**

```bash
git add app/ui/src/components/ClipEditor.vue app/ui/src/components/ClipDrawer.vue
git commit -m "$(cat <<'EOF'
feat(ui): add a trim/crop editor with live preview to the clip drawer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Full verification gate

**Files:** none (verification only).

- [ ] **Step 1: Run the full local gate**

Run: `scripts/verify.sh`
Expected: PASS — ruff format/check, pyright, the full pytest suite, the OpenAPI contract check, translation JSON checks, the UI Vitest suite, the UI production build, and (if Docker is available) the Worker image build all succeed.

- [ ] **Step 2: Fix any regressions surfaced by the full gate**

If a step fails, fix it in the file it points to and re-run `scripts/verify.sh` from the top — do not skip ahead. This step has no fixed diff; it closes only when the gate is fully green.

- [ ] **Step 3: Commit, if the previous step made any changes**

```bash
git add -A
git commit -m "$(cat <<'EOF'
fix: address full verification gate findings

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
