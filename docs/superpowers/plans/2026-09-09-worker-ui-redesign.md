# Library Manager UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Worker's Jinja-rendered Library Manager with a Vue 3 single-page application that has four sections, a searchable and filterable clip catalog with bulk actions and a detail drawer, and shared queue progress.

**Architecture:** The Worker keeps serving every existing `/manager/*` JSON route and gains three more (`/manager/clips`, `/manager/session`, `/manager/collections`). `GET /` stops rendering a template and serves the built SPA shell from `static/ui/`. The SPA is built by a Node stage in the App's Dockerfile and is never committed.

**Tech Stack:** Vue 3 (Composition API, `<script setup>`), TypeScript, Vite 6, Tailwind CSS v4, Vue Router 4 in hash mode, Vitest + `@vue/test-utils`. Backend stays FastAPI + SQLite.

## Global Constraints

- Home Assistant mounts the App under a random Ingress prefix. Every Worker URL used by the SPA must be **relative** (`manager/clips`, never `/manager/clips`). The only absolute URL is the Home Assistant order bridge at `/api/cinema_collections/order`, built against `window.location.origin`.
- Vite builds with `base: './'`. Vue Router runs in **hash mode**.
- Every mutating request carries the `X-CSRF-Token` header. It never carries the Worker bearer secret.
- `/api/v1/*` and `contract/openapi-v1.yaml` do not change. Manager routes stay out of the OpenAPI schema (`include_in_schema=False`).
- The `order_bridge_capability` expires after 300 seconds; re-fetch `manager/session` immediately before saving a playback order.
- Upload chunk size stays `8 * 1024 * 1024` bytes.
- Bulk fan-out concurrency limit: 4.
- Python: ruff format, ruff check, and pyright must pass. Line length follows the existing `pyproject.toml`.
- No new runtime Python dependencies. Node exists only in the Docker build stage.

---

### Task 1: Manager JSON read routes

**Files:**
- Modify: `app/src/cinema_collections_worker/manager_web.py`
- Test: `tests/worker/test_library_manager_web.py`

**Interfaces:**
- Consumes: `app.state.database`, `_initial_auth_is_valid`, `_valid_session`, `_order_bridge_capability`, `_worker_version`.
- Produces: `GET /manager/clips` → `list[dict]` with keys `id`, `collection_id`, `relative_source_path`, `relative_output_path`, `output_available`, `state`, `duration_seconds`, `sequential_rank`, `tags`, `notes`, `failed_reason`. `GET /manager/collections` → `list[dict]` with `id`, `name`. `GET /manager/session` → `dict` with `csrf`, `worker_version`, `order_bridge_capability`.
- Also produces: `_clip_payloads(database) -> list[dict[str, Any]]`, reused by the route.

- [ ] **Step 1: Write the failing tests**

Append to `tests/worker/test_library_manager_web.py`:

```python
def test_manager_clips_returns_catalog_json(manager_client):
    client, _ = manager_client
    response = client.get("/manager/clips")
    assert response.status_code == 200
    payload = response.json()
    assert payload
    first = payload[0]
    assert set(first) == {
        "id",
        "collection_id",
        "relative_source_path",
        "relative_output_path",
        "output_available",
        "state",
        "duration_seconds",
        "sequential_rank",
        "tags",
        "notes",
        "failed_reason",
    }
    assert isinstance(first["tags"], list)


def test_manager_session_returns_csrf_and_capability(manager_client):
    client, _ = manager_client
    response = client.get("/manager/session")
    assert response.status_code == 200
    payload = response.json()
    assert payload["csrf"]
    assert payload["worker_version"]
    assert "." in payload["order_bridge_capability"]


def test_manager_collections_lists_configured_collections(manager_client):
    client, _ = manager_client
    response = client.get("/manager/collections")
    assert response.status_code == 200
    assert all({"id", "name"} <= set(entry) for entry in response.json())
```

The existing fixture that authenticates a manager session is reused; match its
name and unpacking to the fixtures already in the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/worker/test_library_manager_web.py -k "manager_clips or manager_session or manager_collections" -v`
Expected: FAIL with 404 responses.

- [ ] **Step 3: Add `_clip_payloads` and the three routes**

In `manager_web.py`, add above `_render_manager`:

```python
def _clip_payloads(database: Any) -> list[dict[str, Any]]:
    """Return every live clip with the sequential rank the order editor needs."""
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
                "tags": list(metadata.get("tags") or []),
                "notes": str(metadata.get("notes") or ""),
                "failed_reason": (
                    str(failed_reason)
                    if failed_reason and state in {"failed", "invalid"}
                    else None
                ),
            }
        )
    return payloads


def _collection_payloads(database: Any) -> list[dict[str, str]]:
    rows = database.connection.execute(
        "SELECT id, name FROM collections ORDER BY name COLLATE NOCASE, id"
    ).fetchall()
    return [{"id": str(row["id"]), "name": str(row["name"])} for row in rows]
```

Inside `install_manager_routes`, next to the other read routes:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/worker/test_library_manager_web.py -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 5: Commit**

```bash
git add app/src/cinema_collections_worker/manager_web.py tests/worker/test_library_manager_web.py
git commit -m "feat(worker): serve the manager catalog as JSON"
```

---

### Task 2: UI workspace, build tooling, and design tokens

**Files:**
- Create: `ui/package.json`, `ui/vite.config.ts`, `ui/tsconfig.json`, `ui/index.html`, `ui/src/main.ts`, `ui/src/style.css`, `ui/src/App.vue`, `ui/src/router.ts`, `ui/vitest.config.ts`, `ui/.gitignore`
- Modify: `app/Dockerfile`, `scripts/verify.sh`, `.github/workflows/quality.yml`, `.gitignore`

**Interfaces:**
- Produces: `npm --prefix ui run build` writes `ui/dist` with `base: './'`. `npm --prefix ui run test:unit` runs Vitest. The router exports the four named routes `library`, `order`, `import`, `system`.

- [ ] **Step 1: Create the workspace**

`ui/package.json`:

```json
{
  "name": "cinema-collections-ui",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc --noEmit && vite build",
    "test:unit": "vitest run"
  },
  "dependencies": {
    "vue": "^3.5.13",
    "vue-router": "^4.5.0"
  },
  "devDependencies": {
    "@tailwindcss/vite": "^4.0.0",
    "@vitejs/plugin-vue": "^5.2.1",
    "@vue/test-utils": "^2.4.6",
    "happy-dom": "^16.5.3",
    "tailwindcss": "^4.0.0",
    "typescript": "^5.7.3",
    "vite": "^6.0.7",
    "vitest": "^3.0.2",
    "vue-tsc": "^2.2.0"
  }
}
```

`ui/vite.config.ts`:

```ts
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import tailwindcss from "@tailwindcss/vite";

// base must stay relative: Home Assistant serves the App under a random
// Ingress prefix, so absolute asset URLs would 404.
export default defineConfig({
  base: "./",
  plugins: [vue(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: { outDir: "dist", emptyOutDir: true },
  server: { proxy: { "/manager": "http://127.0.0.1:8099" } },
});
```

`ui/vitest.config.ts`:

```ts
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: { environment: "happy-dom", include: ["src/**/*.spec.ts"] },
});
```

`ui/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "jsx": "preserve",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "types": ["vite/client"],
    "skipLibCheck": true,
    "noEmit": true,
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["src/**/*.ts", "src/**/*.vue", "*.config.ts"]
}
```

`ui/index.html`:

```html
<!doctype html>
<html lang="en" class="dark">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Cinema Collections Library Manager</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

`ui/.gitignore`:

```
node_modules/
dist/
```

- [ ] **Step 2: Write the design tokens**

`ui/src/style.css` — Tailwind v4 is configured in CSS, so there is no
`tailwind.config.js`:

```css
@import "tailwindcss";

@theme {
  --color-ground: #0b0d12;
  --color-surface: #141821;
  --color-surface-raised: #1c212c;
  --color-surface-hover: #232936;
  --color-line: #2b3240;
  --color-ink: #e8eaf0;
  --color-ink-muted: #99a1b3;
  --color-accent: #e8a33d;
  --color-accent-ink: #1a1204;
  --color-ok: #4ba97a;
  --color-warn: #d99b2b;
  --color-danger: #d9564f;
  --radius-panel: 0.75rem;
}

html,
body,
#app {
  height: 100%;
}

body {
  margin: 0;
  background: var(--color-ground);
  color: var(--color-ink);
  font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
}

:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}
```

- [ ] **Step 3: Write the router and entry point**

`ui/src/router.ts`:

```ts
import { createRouter, createWebHashHistory } from "vue-router";

// Hash history: the Ingress prefix is unknown at build time, and history mode
// would 404 on reload behind it.
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/library" },
    { path: "/library", name: "library", component: () => import("@/views/LibraryView.vue") },
    { path: "/order", name: "order", component: () => import("@/views/OrderView.vue") },
    { path: "/import", name: "import", component: () => import("@/views/ImportView.vue") },
    { path: "/system", name: "system", component: () => import("@/views/SystemView.vue") },
  ],
});
```

`ui/src/main.ts`:

```ts
import { createApp } from "vue";
import App from "@/App.vue";
import { router } from "@/router";
import "@/style.css";

createApp(App).use(router).mount("#app");
```

`ui/src/App.vue` is written in Task 4, once the session and job composables
exist. For this task, create a placeholder that renders the router view so the
build succeeds:

```vue
<script setup lang="ts"></script>

<template>
  <RouterView />
</template>
```

Create the four views as minimal placeholders so the lazy imports resolve; each
is replaced in a later task. For example `ui/src/views/LibraryView.vue`:

```vue
<template>
  <section />
</template>
```

Create `ui/src/views/OrderView.vue`, `ui/src/views/ImportView.vue`, and
`ui/src/views/SystemView.vue` with the same body.

- [ ] **Step 4: Verify the build**

Run: `npm --prefix ui install && npm --prefix ui run build`
Expected: `ui/dist/index.html` exists and its asset URLs start with `./`.

Run: `grep -c 'src="./assets' ui/dist/index.html`
Expected: `1`

- [ ] **Step 5: Add the Docker build stage**

Replace the top of `app/Dockerfile`:

```dockerfile
ARG BUILD_ARCH=amd64

FROM node:22-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM ghcr.io/home-assistant/${BUILD_ARCH}-base:3.20
```

and after `COPY src /opt/cinema-collections-worker/src`:

```dockerfile
COPY --from=ui /ui/dist /opt/cinema-collections-worker/src/cinema_collections_worker/static/ui
```

The Docker build context is the repository root from now on, not `app/`, because
the Node stage needs `ui/`. Update every build invocation accordingly.

- [ ] **Step 6: Update the release gate**

In `scripts/verify.sh`, before the Docker block:

```bash
if command -v npm >/dev/null 2>&1; then
    npm --prefix ui ci
    npm --prefix ui run test:unit
    npm --prefix ui run build
else
    echo "npm is unavailable; skipped the UI tests and build (CI runs them)." >&2
fi
```

and change the Docker build to use the repository root as its context:

```bash
    docker build --file app/Dockerfile --tag cinema-collections-worker:verify .
```

In `.github/workflows/quality.yml`, add before the `uv sync` step:

```yaml
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
```

In `.github/workflows/worker-image.yml`, change the build context from `app` to
`.` wherever it is set, keeping `file: app/Dockerfile`.

Add to the repository root `.gitignore`:

```
ui/node_modules/
ui/dist/
app/src/cinema_collections_worker/static/ui/
```

- [ ] **Step 7: Commit**

```bash
git add ui .gitignore app/Dockerfile scripts/verify.sh .github/workflows
git commit -m "build(ui): add the Vue workspace and its Docker build stage"
```

---

### Task 3: Serve the SPA shell and retire the template

**Files:**
- Modify: `app/src/cinema_collections_worker/manager_web.py`
- Delete: `app/src/cinema_collections_worker/templates/manager.html`, `app/src/cinema_collections_worker/static/manager.css`
- Test: `tests/worker/test_library_manager_web.py`

**Interfaces:**
- Consumes: `_clip_payloads` from Task 1, `ui/dist` from Task 2.
- Produces: `GET /` returns the SPA shell with the session cookie and
  `X-CSRF-Token` header, or 503 when the bundle is absent.

- [ ] **Step 1: Rewrite the markup assertions as behavior assertions**

In `tests/worker/test_library_manager_web.py`, every assertion of the form
`assert "<td>...</td>" in response.text` is replaced by an assertion against
`GET /manager/clips`. Keep the same cases the markup tests covered:

```python
def test_clip_without_output_reports_no_output_targets(manager_client):
    client, _ = manager_client
    clip = next(item for item in client.get("/manager/clips").json() if not item["output_available"])
    assert clip["relative_output_path"] == ""


def test_failed_clip_exposes_its_failure_reason(manager_client, failed_clip_id):
    client, _ = manager_client
    clip = next(item for item in client.get("/manager/clips").json() if item["id"] == failed_clip_id)
    assert clip["state"] in {"failed", "invalid"}
    assert clip["failed_reason"]


def test_manager_page_serves_the_spa_shell(manager_client):
    client, _ = manager_client
    response = client.get("/")
    assert response.status_code in {200, 503}
    if response.status_code == 200:
        assert '<div id="app">' in response.text
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-CSRF-Token"]
```

The shell test tolerates 503 so the Python suite passes without a Node build;
CI always builds the bundle first, so it exercises the 200 path there.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/worker/test_library_manager_web.py -v`
Expected: FAIL — the old template still renders, so `<div id="app">` is absent.

- [ ] **Step 3: Replace the page route**

In `manager_web.py`, delete `_render_clip_row`, `_render_manager`, and
`_CLIP_TABLE_COLUMNS`, then replace the body of `manager_page`:

```python
    ui_root = Path(__file__).with_name("static") / "ui"

    @app.get("/", include_in_schema=False, response_class=HTMLResponse)
    def manager_page(request: Request) -> HTMLResponse:
        if not _initial_auth_is_valid(request, settings):
            raise HTTPException(status_code=401, detail="Library Manager authentication required")
        shell = ui_root / "index.html"
        if not shell.is_file():
            raise HTTPException(
                status_code=503,
                detail=(
                    "The Library Manager UI bundle is missing. Build it with "
                    "'npm --prefix ui ci && npm --prefix ui run build'."
                ),
            )
        record = _valid_session(request)
        if record is None:
            session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            _sessions(app)[session] = (csrf, time.monotonic() + _SESSION_SECONDS)
        else:
            session, csrf = record
        page = HTMLResponse(shell.read_text(encoding="utf-8"))
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
```

Delete the template and the old stylesheet:

```bash
git rm app/src/cinema_collections_worker/templates/manager.html
git rm app/src/cinema_collections_worker/static/manager.css
rmdir app/src/cinema_collections_worker/templates
```

Drop the now-unused `html` and `json` imports only if nothing else in the module
uses them; `_clip_payloads` still needs `json`.

- [ ] **Step 4: Run the full Python suite**

Run: `uv run pytest && uv run ruff check . && uv run pyright`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A app/src/cinema_collections_worker tests/worker/test_library_manager_web.py
git commit -m "refactor(worker): serve the single-page manager shell"
```

---

### Task 4: API client, session, jobs, and the app shell

**Files:**
- Create: `ui/src/composables/useApi.ts`, `ui/src/composables/useSession.ts`, `ui/src/composables/useJobs.ts`, `ui/src/components/AppSidebar.vue`, `ui/src/components/JobIndicator.vue`, `ui/src/types.ts`
- Modify: `ui/src/App.vue`
- Test: `ui/src/composables/useApi.spec.ts`

**Interfaces:**
- Produces:
  - `apiFetch<T>(path: string, options?: RequestInit): Promise<T>` — prefixes nothing, adds `X-CSRF-Token`, throws `Error` carrying the server's `message`.
  - `useSession()` → `{ csrf, workerVersion, collections, load(), orderCapability() }` where `orderCapability(): Promise<string>` re-fetches a fresh capability.
  - `useJobs()` → `{ jobs, active, start(), stop(), refresh() }`.
  - `types.ts` exports `Clip`, `Job`, `LogEntry`, `TrashEntry`, `CollectionSummary`.

- [ ] **Step 1: Write the types**

`ui/src/types.ts`:

```ts
export interface Clip {
  id: string;
  collection_id: string;
  relative_source_path: string;
  relative_output_path: string;
  output_available: boolean;
  state: string;
  duration_seconds: number;
  sequential_rank: number;
  tags: string[];
  notes: string;
  failed_reason: string | null;
}

export interface JobProgress {
  stage: string;
  percent: number;
  eta_seconds: number | null;
}

export interface Job {
  id: string;
  kind: string;
  state: string;
  created_at: string | null;
  finished_at: string | null;
  error: string | null;
  progress?: JobProgress;
}

export interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  job_id: string | null;
}

export interface TrashEntry {
  id: string;
  clip_id: string;
  target: string;
  created_at: string;
}

export interface CollectionSummary {
  id: string;
  name: string;
}
```

- [ ] **Step 2: Write the failing test for the API client**

`ui/src/composables/useApi.spec.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from "vitest";
import { apiFetch, setCsrfToken } from "@/composables/useApi";

describe("apiFetch", () => {
  beforeEach(() => setCsrfToken("token-1"));

  it("sends the CSRF header and returns the parsed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiFetch("manager/clips")).resolves.toEqual({ ok: true });
    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.get("X-CSRF-Token")).toBe("token-1");
  });

  it("throws the server's message on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ message: "nope" }), { status: 422 }),
      ),
    );
    await expect(apiFetch("manager/clips")).rejects.toThrow("nope");
  });

  it("keeps request paths relative so the Ingress prefix is preserved", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("null", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await apiFetch("manager/jobs");
    expect(fetchMock.mock.calls[0][0]).toBe("manager/jobs");
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `npm --prefix ui run test:unit`
Expected: FAIL — `useApi` does not exist.

- [ ] **Step 4: Implement the API client**

`ui/src/composables/useApi.ts`:

```ts
let csrfToken = "";

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

/**
 * Fetch a Worker route. `path` is always relative so the Home Assistant Ingress
 * prefix is preserved; an absolute path would escape it and 404.
 */
export async function apiFetch<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    const message =
      body && typeof body === "object" && "message" in body && typeof body.message === "string"
        ? body.message
        : `Request failed (HTTP ${response.status})`;
    throw new Error(message);
  }
  return body as T;
}

/** Run `task` over `items` with at most `limit` in flight. */
export async function mapLimit<T, R>(
  items: readonly T[],
  limit: number,
  task: (item: T) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = new Array(items.length);
  let cursor = 0;
  async function worker(): Promise<void> {
    for (;;) {
      const index = cursor++;
      if (index >= items.length) return;
      try {
        results[index] = { status: "fulfilled", value: await task(items[index]) };
      } catch (error) {
        results[index] = { status: "rejected", reason: error };
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}
```

- [ ] **Step 5: Implement the session composable**

`ui/src/composables/useSession.ts`:

```ts
import { shallowRef } from "vue";
import { apiFetch, setCsrfToken } from "@/composables/useApi";
import type { CollectionSummary } from "@/types";

interface SessionPayload {
  csrf: string;
  worker_version: string;
  order_bridge_capability: string;
}

const csrf = shallowRef("");
const workerVersion = shallowRef("");
const collections = shallowRef<CollectionSummary[]>([]);

export function useSession() {
  async function load(): Promise<void> {
    const payload = await apiFetch<SessionPayload>("manager/session");
    csrf.value = payload.csrf;
    workerVersion.value = payload.worker_version;
    setCsrfToken(payload.csrf);
    collections.value = await apiFetch<CollectionSummary[]>("manager/collections");
  }

  /**
   * The bridge capability expires after five minutes, so it is fetched fresh
   * at the moment it is used rather than cached from page load.
   */
  async function orderCapability(): Promise<string> {
    const payload = await apiFetch<SessionPayload>("manager/session");
    setCsrfToken(payload.csrf);
    csrf.value = payload.csrf;
    return payload.order_bridge_capability;
  }

  return { csrf, workerVersion, collections, load, orderCapability };
}
```

The initial CSRF token arrives on the `X-CSRF-Token` response header of `GET /`,
but the SPA cannot read its own document's headers, so `load()` calls
`manager/session`, which is session-cookie authenticated and needs no token.
`apiFetch` therefore tolerates an empty token on that first call.

- [ ] **Step 6: Implement the jobs composable**

`ui/src/composables/useJobs.ts`:

```ts
import { computed, shallowRef } from "vue";
import { apiFetch } from "@/composables/useApi";
import type { Job } from "@/types";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const jobs = shallowRef<Job[]>([]);
let timer: number | null = null;

export function useJobs() {
  const active = computed(() => jobs.value.filter((job) => !TERMINAL.has(job.state)));

  async function refresh(): Promise<void> {
    try {
      jobs.value = await apiFetch<Job[]>("manager/jobs");
    } catch {
      // A failed poll is not worth interrupting the view over; the next tick retries.
    }
  }

  function start(): void {
    if (timer !== null) return;
    void refresh();
    timer = window.setInterval(() => void refresh(), 2000);
  }

  function stop(): void {
    if (timer === null) return;
    window.clearInterval(timer);
    timer = null;
  }

  /** Poll one job to a terminal state, reporting progress on every tick. */
  async function follow(jobId: string, onUpdate: (job: Job) => void): Promise<Job | null> {
    for (;;) {
      let job: Job;
      try {
        job = await apiFetch<Job>(`manager/jobs/${jobId}`);
      } catch {
        return null;
      }
      onUpdate(job);
      if (TERMINAL.has(job.state)) return job;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  return { jobs, active, refresh, start, stop, follow };
}

export function jobIdFrom(details: unknown): string | null {
  if (!details || typeof details !== "object") return null;
  const record = details as Record<string, unknown>;
  if (typeof record.job_id === "string") return record.job_id;
  if (Array.isArray(record.job_ids) && typeof record.job_ids[0] === "string") {
    return record.job_ids[0];
  }
  return null;
}
```

- [ ] **Step 7: Write the shell**

`ui/src/components/AppSidebar.vue`:

```vue
<script setup lang="ts">
const sections = [
  { name: "library", label: "Library", icon: "▤" },
  { name: "order", label: "Playback order", icon: "↕" },
  { name: "import", label: "Import", icon: "↑" },
  { name: "system", label: "System", icon: "⚙" },
];
</script>

<template>
  <nav
    aria-label="Library Manager sections"
    class="flex shrink-0 gap-1 border-line bg-surface p-2 max-md:order-last max-md:justify-around max-md:border-t md:w-56 md:flex-col md:border-r md:p-3"
  >
    <RouterLink
      v-for="section in sections"
      :key="section.name"
      :to="{ name: section.name }"
      class="flex items-center gap-3 rounded-panel px-3 py-2 text-sm font-medium text-ink-muted transition hover:bg-surface-hover hover:text-ink max-md:flex-col max-md:gap-1 max-md:text-xs"
      active-class="bg-surface-raised text-accent"
    >
      <span aria-hidden="true">{{ section.icon }}</span>
      <span>{{ section.label }}</span>
    </RouterLink>
  </nav>
</template>
```

`ui/src/components/JobIndicator.vue`:

```vue
<script setup lang="ts">
import { ref } from "vue";
import { useJobs } from "@/composables/useJobs";

const { jobs, active } = useJobs();
const open = ref(false);
</script>

<template>
  <div class="relative">
    <button
      type="button"
      class="flex items-center gap-2 rounded-panel border border-line px-3 py-1.5 text-sm text-ink-muted hover:text-ink"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span
        class="size-2 rounded-full"
        :class="active.length ? 'animate-pulse bg-accent' : 'bg-ok'"
        aria-hidden="true"
      />
      {{ active.length ? `${active.length} running` : "Queue idle" }}
    </button>
    <div
      v-if="open"
      class="absolute right-0 z-20 mt-2 w-80 rounded-panel border border-line bg-surface-raised p-2 shadow-xl"
    >
      <p v-if="!jobs.length" class="p-2 text-sm text-ink-muted">No jobs yet.</p>
      <ul v-else class="max-h-80 space-y-1 overflow-y-auto">
        <li v-for="job in jobs.slice(0, 12)" :key="job.id" class="rounded px-2 py-1.5 text-sm">
          <span class="text-ink">{{ job.kind }}</span>
          <span class="ml-2 text-ink-muted">{{ job.state }}</span>
          <p v-if="job.error" class="text-xs text-danger">{{ job.error }}</p>
        </li>
      </ul>
    </div>
  </div>
</template>
```

`ui/src/App.vue`:

```vue
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import AppSidebar from "@/components/AppSidebar.vue";
import JobIndicator from "@/components/JobIndicator.vue";
import { useSession } from "@/composables/useSession";
import { useJobs } from "@/composables/useJobs";

const { workerVersion, load } = useSession();
const { start, stop } = useJobs();
const error = ref("");

onMounted(async () => {
  try {
    await load();
    start();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  }
});
onUnmounted(stop);
</script>

<template>
  <div class="flex h-full flex-col md:flex-row">
    <AppSidebar />
    <div class="flex min-w-0 flex-1 flex-col">
      <header class="flex items-center justify-between gap-4 border-b border-line px-5 py-3">
        <div>
          <h1 class="text-base font-semibold">Cinema Collections</h1>
          <p class="text-xs text-ink-muted">Library Manager {{ workerVersion }}</p>
        </div>
        <JobIndicator />
      </header>
      <p v-if="error" role="alert" class="m-5 rounded-panel bg-danger/15 p-3 text-sm text-danger">
        {{ error }}
      </p>
      <main class="min-h-0 flex-1 overflow-y-auto p-5">
        <RouterView />
      </main>
    </div>
  </div>
</template>
```

- [ ] **Step 8: Run the tests and the build**

Run: `npm --prefix ui run test:unit && npm --prefix ui run build`
Expected: PASS, build succeeds.

- [ ] **Step 9: Commit**

```bash
git add ui/src
git commit -m "feat(ui): add the app shell, API client, and shared job state"
```

---

### Task 5: Library view with search, filters, sort, and the card/table toggle

**Files:**
- Create: `ui/src/composables/useClips.ts`, `ui/src/components/ClipCard.vue`, `ui/src/components/ClipTable.vue`, `ui/src/components/StateBadge.vue`, `ui/src/lib/format.ts`
- Modify: `ui/src/views/LibraryView.vue`
- Test: `ui/src/composables/useClips.spec.ts`

**Interfaces:**
- Consumes: `apiFetch`, `Clip`.
- Produces: `useClips()` → `{ clips, load(), filtered, query, collectionFilter, stateFilter, sortKey, selected, toggle(id), clearSelection() }`; `formatDuration(seconds: number): string`; `sourceName(clip: Clip): string`.

- [ ] **Step 1: Write the failing test**

`ui/src/composables/useClips.spec.ts`:

```ts
import { describe, expect, it, beforeEach } from "vitest";
import { useClips } from "@/composables/useClips";
import type { Clip } from "@/types";

function clip(overrides: Partial<Clip>): Clip {
  return {
    id: "a",
    collection_id: "regular",
    relative_source_path: "regular/one.mp4",
    relative_output_path: "",
    output_available: false,
    state: "catalogued",
    duration_seconds: 30,
    sequential_rank: 0,
    tags: [],
    notes: "",
    failed_reason: null,
    ...overrides,
  };
}

describe("useClips", () => {
  const store = useClips();
  beforeEach(() => {
    store.clips.value = [
      clip({ id: "a", relative_source_path: "regular/alpha.mp4", duration_seconds: 30 }),
      clip({ id: "b", relative_source_path: "regular/beta.mp4", state: "failed", duration_seconds: 90 }),
      clip({ id: "c", collection_id: "holiday", relative_source_path: "holiday/gamma.mp4", duration_seconds: 10 }),
    ];
    store.query.value = "";
    store.collectionFilter.value = "";
    store.stateFilter.value = "";
    store.sortKey.value = "name";
    store.clearSelection();
  });

  it("filters by filename fragment, case-insensitively", () => {
    store.query.value = "BET";
    expect(store.filtered.value.map((entry) => entry.id)).toEqual(["b"]);
  });

  it("filters by collection and state together", () => {
    store.collectionFilter.value = "regular";
    store.stateFilter.value = "failed";
    expect(store.filtered.value.map((entry) => entry.id)).toEqual(["b"]);
  });

  it("sorts by duration when asked", () => {
    store.sortKey.value = "duration";
    expect(store.filtered.value.map((entry) => entry.id)).toEqual(["c", "a", "b"]);
  });

  it("tracks a selection that survives filtering", () => {
    store.toggle("a");
    store.toggle("b");
    store.query.value = "alpha";
    expect([...store.selected.value].sort()).toEqual(["a", "b"]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix ui run test:unit`
Expected: FAIL — `useClips` does not exist.

- [ ] **Step 3: Implement the formatter and the store**

`ui/src/lib/format.ts`:

```ts
import type { Clip } from "@/types";

export function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return "—";
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function sourceName(clip: Clip): string {
  const path = clip.relative_source_path;
  return path.split("/").pop() || path;
}
```

`ui/src/composables/useClips.ts`:

```ts
import { computed, ref, shallowRef } from "vue";
import { apiFetch } from "@/composables/useApi";
import { sourceName } from "@/lib/format";
import type { Clip } from "@/types";

export type SortKey = "name" | "duration" | "state";

const clips = shallowRef<Clip[]>([]);
const query = ref("");
const collectionFilter = ref("");
const stateFilter = ref("");
const sortKey = ref<SortKey>("name");
const selected = ref(new Set<string>());

export function useClips() {
  const filtered = computed(() => {
    const needle = query.value.trim().toLowerCase();
    const rows = clips.value.filter((clip) => {
      if (collectionFilter.value && clip.collection_id !== collectionFilter.value) return false;
      if (stateFilter.value && clip.state !== stateFilter.value) return false;
      if (needle && !sourceName(clip).toLowerCase().includes(needle)) return false;
      return true;
    });
    const sorted = [...rows];
    if (sortKey.value === "duration") {
      sorted.sort((a, b) => a.duration_seconds - b.duration_seconds);
    } else if (sortKey.value === "state") {
      sorted.sort((a, b) => a.state.localeCompare(b.state) || sourceName(a).localeCompare(sourceName(b)));
    } else {
      sorted.sort((a, b) => sourceName(a).localeCompare(sourceName(b)));
    }
    return sorted;
  });

  const states = computed(() => [...new Set(clips.value.map((clip) => clip.state))].sort());

  async function load(): Promise<void> {
    clips.value = await apiFetch<Clip[]>("manager/clips");
    const live = new Set(clips.value.map((clip) => clip.id));
    selected.value = new Set([...selected.value].filter((id) => live.has(id)));
  }

  function toggle(id: string): void {
    const next = new Set(selected.value);
    if (!next.delete(id)) next.add(id);
    selected.value = next;
  }

  function clearSelection(): void {
    selected.value = new Set();
  }

  return {
    clips,
    query,
    collectionFilter,
    stateFilter,
    sortKey,
    selected,
    filtered,
    states,
    load,
    toggle,
    clearSelection,
  };
}
```

- [ ] **Step 4: Write the presentation components**

`ui/src/components/StateBadge.vue`:

```vue
<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ state: string }>();
const tone = computed(() => {
  if (props.state === "compiled") return "bg-ok/15 text-ok";
  if (props.state === "failed" || props.state === "invalid") return "bg-danger/15 text-danger";
  return "bg-warn/15 text-warn";
});
</script>

<template>
  <span class="rounded-full px-2 py-0.5 text-xs font-medium" :class="tone">{{ state }}</span>
</template>
```

`ui/src/components/ClipCard.vue`:

```vue
<script setup lang="ts">
import StateBadge from "@/components/StateBadge.vue";
import { formatDuration, sourceName } from "@/lib/format";
import type { Clip } from "@/types";

defineProps<{ clip: Clip; selected: boolean }>();
defineEmits<{ open: [Clip]; toggle: [string] }>();
</script>

<template>
  <article
    class="flex flex-col gap-3 rounded-panel border bg-surface p-4 transition hover:border-accent/60 hover:bg-surface-hover"
    :class="selected ? 'border-accent' : 'border-line'"
  >
    <div class="flex items-start gap-3">
      <input
        type="checkbox"
        class="mt-1 size-4 accent-[var(--color-accent)]"
        :checked="selected"
        :aria-label="`Select ${sourceName(clip)}`"
        @change="$emit('toggle', clip.id)"
      />
      <button
        type="button"
        class="min-w-0 flex-1 text-left"
        @click="$emit('open', clip)"
      >
        <p class="truncate font-medium" :title="clip.relative_source_path">{{ sourceName(clip) }}</p>
        <p class="truncate text-xs text-ink-muted">{{ clip.collection_id }}</p>
      </button>
    </div>
    <p v-if="clip.failed_reason" class="text-xs text-danger">{{ clip.failed_reason }}</p>
    <div class="mt-auto flex flex-wrap items-center gap-2 text-xs text-ink-muted">
      <StateBadge :state="clip.state" />
      <span>{{ formatDuration(clip.duration_seconds) }}</span>
      <span v-if="clip.output_available" class="text-ok" :title="clip.relative_output_path">
        Output ready
      </span>
      <span v-for="tag in clip.tags" :key="tag" class="rounded bg-surface-raised px-1.5 py-0.5">
        {{ tag }}
      </span>
    </div>
  </article>
</template>
```

`ui/src/components/ClipTable.vue` renders the same data as a dense table with a
selection column, collection, source name, state badge, output, duration, tags,
and a button that emits `open`. It uses the same props and emits as `ClipCard`
but takes `clips: Clip[]` and `selected: Set<string>`.

```vue
<script setup lang="ts">
import StateBadge from "@/components/StateBadge.vue";
import { formatDuration, sourceName } from "@/lib/format";
import type { Clip } from "@/types";

defineProps<{ clips: Clip[]; selected: Set<string> }>();
defineEmits<{ open: [Clip]; toggle: [string] }>();
</script>

<template>
  <div class="overflow-x-auto rounded-panel border border-line">
    <table class="w-full min-w-[46rem] border-collapse text-sm">
      <thead class="bg-surface text-left text-xs uppercase tracking-wide text-ink-muted">
        <tr>
          <th class="w-10 px-3 py-2"><span class="sr-only">Select</span></th>
          <th class="px-3 py-2">Source</th>
          <th class="px-3 py-2">Collection</th>
          <th class="px-3 py-2">State</th>
          <th class="px-3 py-2">Output</th>
          <th class="px-3 py-2">Duration</th>
          <th class="px-3 py-2">Tags</th>
          <th class="w-24 px-3 py-2"></th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="clip in clips"
          :key="clip.id"
          class="border-t border-line hover:bg-surface-hover"
          :class="selected.has(clip.id) && 'bg-surface-raised'"
        >
          <td class="px-3 py-2">
            <input
              type="checkbox"
              class="size-4 accent-[var(--color-accent)]"
              :checked="selected.has(clip.id)"
              :aria-label="`Select ${sourceName(clip)}`"
              @change="$emit('toggle', clip.id)"
            />
          </td>
          <td class="max-w-[18rem] truncate px-3 py-2" :title="clip.relative_source_path">
            {{ sourceName(clip) }}
          </td>
          <td class="px-3 py-2 text-ink-muted">{{ clip.collection_id }}</td>
          <td class="px-3 py-2"><StateBadge :state="clip.state" /></td>
          <td class="px-3 py-2 text-ink-muted">{{ clip.output_available ? "Ready" : "—" }}</td>
          <td class="px-3 py-2 text-ink-muted">{{ formatDuration(clip.duration_seconds) }}</td>
          <td class="max-w-[12rem] truncate px-3 py-2 text-ink-muted">{{ clip.tags.join(", ") }}</td>
          <td class="px-3 py-2">
            <button
              type="button"
              class="rounded border border-line px-2 py-1 text-xs hover:bg-surface-hover"
              @click="$emit('open', clip)"
            >
              Details
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
```

- [ ] **Step 5: Write the view**

`ui/src/views/LibraryView.vue` holds the sticky toolbar (search input, collection
select, state select, sort select, layout toggle) and renders either the grid of
`ClipCard`s or `ClipTable`. The layout choice persists under the
`manager-layout` key in `localStorage`, wrapped in try/catch because storage can
throw. On mount it calls `useClips().load()` and `useSession().load()` is already
done by the shell. An empty catalog renders "No catalogued clips yet." and a
filtered-to-nothing catalog renders "No clips match these filters."

- [ ] **Step 6: Run the tests and the build**

Run: `npm --prefix ui run test:unit && npm --prefix ui run build`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ui/src
git commit -m "feat(ui): add the searchable clip library with a card and table layout"
```

---

### Task 6: Clip detail drawer

**Files:**
- Create: `ui/src/components/ClipDrawer.vue`
- Modify: `ui/src/views/LibraryView.vue`

**Interfaces:**
- Consumes: `apiFetch`, `useJobs().follow`, `jobIdFrom`, `Clip`.
- Produces: a drawer emitting `close` and `changed`; the view reloads the catalog on `changed`.

- [ ] **Step 1: Implement the drawer**

The drawer replaces the old in-row panel and covers every action it had:
`scan`, `recompile`, metadata edit (tags and notes), move, trash with a target
selector, and delete with the two-step confirmation. It mirrors the template's
rules exactly: when `output_available` is false the only trash and delete target
offered is `source`.

Delete keeps the existing two-step handshake:

```ts
async function remove(target: string) {
  const info = await apiFetch<{ warning: string; confirmation: string }>(
    `manager/clips/${props.clip.id}/delete-confirmation?target=${target}`,
  );
  const affected =
    target === "source"
      ? `Source: ${props.clip.relative_source_path}`
      : target === "output"
        ? `Compiled output: ${props.clip.relative_output_path || "(no compiled output)"}`
        : `Source: ${props.clip.relative_source_path}\nCompiled output: ${props.clip.relative_output_path || "(no compiled output)"}`;
  if (!window.confirm(`${info.warning}\n${affected}`)) return;
  await apiFetch(`manager/clips/${props.clip.id}/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target, confirmation: info.confirmation }),
  });
  emit("changed");
  emit("close");
}
```

Queued actions follow their job through `useJobs().follow` and show
`${stage} ${percent}%` inline instead of reloading the page.

Accessibility: the drawer is `role="dialog"` with `aria-modal="true"`, focuses
its close button on open, restores focus to the trigger on close, and closes on
`Escape`.

- [ ] **Step 2: Wire it into the view**

`LibraryView.vue` keeps `const openClip = ref<Clip | null>(null)`, renders
`<ClipDrawer v-if="openClip" :clip="openClip" @close="openClip = null" @changed="reload" />`,
and `reload` calls `useClips().load()` plus `useJobs().refresh()`.

- [ ] **Step 3: Verify**

Run: `npm --prefix ui run test:unit && npm --prefix ui run build`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add ui/src
git commit -m "feat(ui): add the clip detail drawer"
```

---

### Task 7: Bulk selection and bulk actions

**Files:**
- Create: `ui/src/components/BulkBar.vue`, `ui/src/lib/bulk.ts`
- Modify: `ui/src/views/LibraryView.vue`
- Test: `ui/src/lib/bulk.spec.ts`

**Interfaces:**
- Consumes: `mapLimit` from `useApi`.
- Produces: `runBulk(ids: string[], task: (id: string) => Promise<unknown>): Promise<{ ok: number; failures: string[] }>`.

- [ ] **Step 1: Write the failing test**

`ui/src/lib/bulk.spec.ts`:

```ts
import { describe, expect, it } from "vitest";
import { runBulk } from "@/lib/bulk";

describe("runBulk", () => {
  it("reports successes and failures without aborting the batch", async () => {
    const result = await runBulk(["a", "b", "c"], async (id) => {
      if (id === "b") throw new Error("boom");
      return id;
    });
    expect(result.ok).toBe(2);
    expect(result.failures).toEqual(["b: boom"]);
  });

  it("never runs more than four calls at once", async () => {
    let inFlight = 0;
    let peak = 0;
    await runBulk(["1", "2", "3", "4", "5", "6", "7", "8"], async () => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 5));
      inFlight -= 1;
    });
    expect(peak).toBeLessThanOrEqual(4);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix ui run test:unit`
Expected: FAIL — `runBulk` does not exist.

- [ ] **Step 3: Implement it**

`ui/src/lib/bulk.ts`:

```ts
import { mapLimit } from "@/composables/useApi";

export const BULK_CONCURRENCY = 4;

export async function runBulk(
  ids: readonly string[],
  task: (id: string) => Promise<unknown>,
): Promise<{ ok: number; failures: string[] }> {
  const results = await mapLimit(ids, BULK_CONCURRENCY, task);
  const failures: string[] = [];
  let ok = 0;
  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      ok += 1;
      return;
    }
    const reason = result.reason;
    failures.push(`${ids[index]}: ${reason instanceof Error ? reason.message : String(reason)}`);
  });
  return { ok, failures };
}
```

- [ ] **Step 4: Build the bar**

`ui/src/components/BulkBar.vue` is a floating bar shown when the selection is
non-empty. It offers Recompile (`POST manager/clips/{id}/recompile`), Tag (a
prompt-free inline input that PATCHes each clip's metadata by merging the new
tags into its existing ones), and Trash (`POST manager/clips/{id}/trash` with
`{target: "source"}`, or `both` when every selected clip has an output). It
reports `12 of 15 recompiled` with an expandable list of failures, and clears
the selection on success.

- [ ] **Step 5: Verify and commit**

Run: `npm --prefix ui run test:unit && npm --prefix ui run build`
Expected: PASS.

```bash
git add ui/src
git commit -m "feat(ui): add bulk selection and batch clip actions"
```

---

### Task 8: Playback order view

**Files:**
- Create: `ui/src/composables/useOrder.ts`, `ui/src/components/OrderRow.vue`
- Modify: `ui/src/views/OrderView.vue`
- Test: `ui/src/composables/useOrder.spec.ts`

**Interfaces:**
- Consumes: `useClips().clips`, `useSession().orderCapability`.
- Produces: `deterministicClipCompare(a: Clip, b: Clip): number`, `moveId(ids: string[], from: number, to: number): string[]`.

- [ ] **Step 1: Write the failing test**

`ui/src/composables/useOrder.spec.ts`:

```ts
import { describe, expect, it } from "vitest";
import { deterministicClipCompare, moveId } from "@/composables/useOrder";
import type { Clip } from "@/types";

function clip(id: string, rank: number, output: string): Clip {
  return {
    id,
    collection_id: "regular",
    relative_source_path: `regular/${id}.mp4`,
    relative_output_path: output,
    output_available: Boolean(output),
    state: "compiled",
    duration_seconds: 10,
    sequential_rank: rank,
    tags: [],
    notes: "",
    failed_reason: null,
  };
}

describe("playback order", () => {
  it("orders by sequential rank first", () => {
    const rows = [clip("b", 1, "B.mp4"), clip("a", 0, "A.mp4")].sort(deterministicClipCompare);
    expect(rows.map((row) => row.id)).toEqual(["a", "b"]);
  });

  it("breaks rank ties by casefolded path, then path, then id", () => {
    const rows = [clip("b", 0, "b.mp4"), clip("a", 0, "A.mp4")].sort(deterministicClipCompare);
    expect(rows.map((row) => row.id)).toEqual(["a", "b"]);
  });

  it("moves an id down without losing the others", () => {
    expect(moveId(["a", "b", "c"], 0, 2)).toEqual(["b", "c", "a"]);
  });

  it("moves an id up", () => {
    expect(moveId(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix ui run test:unit`
Expected: FAIL — `useOrder` does not exist.

- [ ] **Step 3: Implement the composable**

`deterministicClipCompare` ports the template's comparator exactly: sequential
rank, then the casefolded output-or-source path, then the raw path, then the id.
`moveId` returns a new array with the element moved.

The bridge calls keep every rule the template established:

- `orderBridgeUrl()` is `new URL("/api/cinema_collections/order", window.location.origin)` — the one absolute URL in the app, because the bridge lives at the domain root outside the Ingress prefix.
- The capability header is `X-Cinema-Collections-Order-Capability`, and its value comes from `useSession().orderCapability()` called immediately before each bridge request, not cached from page load.
- A GET that fails, or a non-`custom` playback mode, disables Save and leaves Copy IDs available with the same guidance text, including the "Multiple Cinema Collections entries" branch.
- A failed save never claims success: it reports the error and says the local arrangement is unchanged.

- [ ] **Step 4: Build the view**

`OrderView.vue` renders the collection select, the reorderable list of
`OrderRow`s, and the Save / Copy IDs / Reset buttons with an `aria-live` status
line. `OrderRow` keeps the drag handle as a real `<button>` with an
`aria-label` of `Reorder ${sourceName}`, and ArrowUp/ArrowDown reorder and move
focus with the moved row, exactly as the template did.

- [ ] **Step 5: Verify and commit**

Run: `npm --prefix ui run test:unit && npm --prefix ui run build`
Expected: PASS.

```bash
git add ui/src
git commit -m "feat(ui): port the playback order editor"
```

---

### Task 9: Import view

**Files:**
- Create: `ui/src/composables/useUpload.ts`, `ui/src/components/UploadPanel.vue`
- Modify: `ui/src/views/ImportView.vue`
- Test: `ui/src/composables/useUpload.spec.ts`

**Interfaces:**
- Produces: `uploadChunked(kind: "clip" | "asset", file: File, collection: string | null, onProgress: (fraction: number) => void): Promise<unknown>`, `COLLECTION_ID_PATTERN`.

- [ ] **Step 1: Write the failing test**

`ui/src/composables/useUpload.spec.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { COLLECTION_ID_PATTERN, uploadChunked } from "@/composables/useUpload";

describe("collection id validation", () => {
  it("accepts short slugs and rejects paths", () => {
    expect(COLLECTION_ID_PATTERN.test("regular")).toBe(true);
    expect(COLLECTION_ID_PATTERN.test("holiday-2026")).toBe(true);
    expect(COLLECTION_ID_PATTERN.test("/media/regular")).toBe(false);
  });
});

describe("uploadChunked", () => {
  it("aborts the staged upload when a chunk fails", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        if (url.includes("/chunk")) return new Response("{}", { status: 500 });
        return new Response(JSON.stringify({ upload_id: "u1" }), { status: 200 });
      }),
    );
    const file = new File([new Uint8Array(16)], "clip.mp4");
    await expect(uploadChunked("clip", file, "regular", () => {})).rejects.toThrow();
    expect(calls.some((url) => url.includes("/abort"))).toBe(true);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix ui run test:unit`
Expected: FAIL — `useUpload` does not exist.

- [ ] **Step 3: Port the chunked upload**

`uploadChunked` is a direct port of the template's function: begin at
`manager/uploads?kind=...&collection_id=...` with an `X-Filename` header, POST
each 8 MiB slice to `manager/uploads/{id}/chunk` as
`application/octet-stream`, best-effort `POST manager/uploads/{id}/abort` on
failure, then `POST manager/uploads/{id}/finish`.

`COLLECTION_ID_PATTERN` is `/^[a-z0-9]+(-[a-z0-9]+)*$/`, and both the scan form
and the folder form reject anything else with the same message the template
used: `That looks like a folder path, not a collection ID.`

- [ ] **Step 4: Build the view**

`ImportView.vue` has three panels: clip upload (multi-file, per-file progress,
no page reload — it refreshes the catalog instead), disk scan (validates the
collection id, follows the returned job, reports stage and percent), and asset
upload with the asset list and its delete confirmation.

- [ ] **Step 5: Verify and commit**

Run: `npm --prefix ui run test:unit && npm --prefix ui run build`
Expected: PASS.

```bash
git add ui/src
git commit -m "feat(ui): merge clip and asset importing into one view"
```

---

### Task 10: System view

**Files:**
- Modify: `ui/src/views/SystemView.vue`

**Interfaces:**
- Consumes: `apiFetch`, `useJobs().jobs`, `TrashEntry`, `LogEntry`.

- [ ] **Step 1: Build the view**

Four panels: create a collection folder
(`POST manager/collections/{id}/directories`), trash with restore
(`GET manager/trash`, `POST manager/trash/{id}/restore`), recent jobs from the
shared job store with no separate poller, and the worker log
(`GET manager/logs`) with error rows tinted and a refresh button. Log and job
tables scroll inside their own container so the page never scrolls sideways.

- [ ] **Step 2: Verify and commit**

Run: `npm --prefix ui run test:unit && npm --prefix ui run build`
Expected: PASS.

```bash
git add ui/src
git commit -m "feat(ui): combine maintenance and diagnostics into the system view"
```

---

### Task 11: Documentation and the release gate

**Files:**
- Modify: `docs/development.md`, `app/DOCS.md`, `README.md`

- [ ] **Step 1: Document the UI workflow**

In `docs/development.md`, add a section covering `npm --prefix ui install`,
`npm --prefix ui run dev` (which proxies `/manager` to a Worker on port 8099),
`npm --prefix ui run test:unit`, and the fact that a Worker started without a
built bundle answers `GET /` with 503 naming the build command.

In `app/DOCS.md` and `README.md`, update the Library Manager description to name
the four sections rather than the six tabs.

- [ ] **Step 2: Run the full gate**

Run: `scripts/verify.sh`
Expected: every check passes.

- [ ] **Step 3: Commit**

```bash
git add docs/development.md app/DOCS.md README.md
git commit -m "docs: describe the rebuilt Library Manager and its UI workflow"
```
