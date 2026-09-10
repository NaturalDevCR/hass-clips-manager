# Library Manager UI redesign

## Problem

The Worker's Library Manager is a single 971-line Jinja template with inline
JavaScript and a 539-line stylesheet. Clip rows are built as HTML strings in
Python (`_render_clip_row`). Six tabs group screens by accident of history, the
active tab lives in `sessionStorage` rather than the URL, every mutation calls
`location.reload()`, and the clip table offers no search, filter, sort, or
multi-select. The result is hard to navigate and hard to extend.

## Goals

- Replace the server-rendered manager with a Vue 3 single-page application.
- Regroup six tabs into four coherent sections.
- Give the clip catalog search, filters, sorting, bulk actions, and a detail
  drawer.
- Show queue progress from any section instead of polling per row.
- Keep the `/api/v1/*` contract with the Home Assistant integration untouched.

## Non-goals

- Clip thumbnails. Cards carry no poster image in this round.
- Any change to selection, compilation, or playback behavior.
- Refactoring `jobs.py`, `library_manager.py`, or the integration.

## Build and delivery

Vue 3 + Vite + Tailwind v4, TypeScript, sources under `ui/`. The built bundle is
never committed.

`app/Dockerfile` gains a Node build stage:

```dockerfile
FROM node:22-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM ghcr.io/home-assistant/${BUILD_ARCH}-base:3.20
...
COPY --from=ui /ui/dist /opt/cinema-collections-worker/src/cinema_collections_worker/static/ui
```

Node exists only in the build stage, so the runtime image gains no weight.

### Serving under Ingress

Home Assistant mounts the App under a random prefix
(`/api/hassio_ingress/<token>/`), so nothing may assume an absolute base path.

- Vite builds with `base: './'`.
- Vue Router runs in **hash mode** (`#/library`). History mode would 404 on
  reload behind the Ingress prefix.
- `GET /` serves `static/ui/index.html` as a static file while keeping the
  current session cookie, `X-CSRF-Token` header, and `Cache-Control: no-store`.
  When the bundle is absent it returns 503 naming the build command.
- Hashed assets under `/static/ui/` are cached normally.

## Backend contract

Most `/manager/*` routes already return JSON and are reused unchanged. Three
gaps close:

| Route | Purpose |
| --- | --- |
| `GET /manager/clips` | Catalog rows as JSON: `id`, `collection_id`, `relative_source_path`, `relative_output_path`, `output_available`, `state`, `duration_seconds`, `sequential_rank`, `tags`, `notes`, `failed_reason`. Replaces `_render_clip_row`. |
| `GET /manager/session` | `csrf`, `worker_version`, `order_bridge_capability`, `collections`. Replaces the injected `<meta>` tags and `{{ collection_options }}`. |
| `GET /manager/collections` | Collection identifiers and labels, previously embedded in the template. |

Deleted: `templates/manager.html`, `static/manager.css`, `_render_clip_row`,
`_render_manager`.

`/api/v1/*` and `contract/openapi-v1.yaml` do not change. The manager routes stay
out of the OpenAPI schema, as they are today.

### Order bridge capability

`_order_bridge_capability` mints a capability that expires after five minutes.
The template injects it once at page load, so a tab left open longer than that
fails to save an order. The SPA re-fetches `GET /manager/session` immediately
before saving the playback order and uses the fresh capability.

### Bulk actions

No new endpoints. The client fans out the existing per-clip calls with a
concurrency limit of four and reports partial results, including which clips
failed and why. The per-clip routes are already idempotent.

## Screens

| Section | Contents | Replaces |
| --- | --- | --- |
| Library | Catalog, search, filters, bulk actions, detail drawer | Library |
| Playback order | Per-collection order editor | Playback order |
| Import | Clip upload, disk scan, intro/outro asset upload | Add clips + Assets |
| System | Create folder, trash, recent jobs, worker log | Maintenance + Diagnostics |

Navigation is a vertical sidebar that collapses to a bottom bar below 768px. The
active section lives in the URL hash, making it linkable and reload-safe.

### Library

- Sticky toolbar: filename search, collection and state filter chips, sort by
  name, duration, or state. All client-side over the loaded catalog.
- Card grid by default with a toggle to a dense table; the choice persists in
  `localStorage`. The table is kept deliberately — it remains the better tool at
  several hundred clips.
- A card shows filename, colored state badge, duration, tags, and an
  output-ready marker. No poster image.
- Per-item checkboxes reveal a floating bulk bar: recompile, tag, trash.
- A side drawer replaces the in-row expandable panel: metadata, tags, notes,
  move, scan, recompile, trash, delete. The list does not reflow when it opens.
- No `location.reload()`. Mutations update the store and the view.

### Queue progress

One poller against `GET /manager/jobs` feeds a shared store. A topbar indicator
shows active jobs from any section and expands into the job list. This replaces
the per-row job polling in the current template.

## Visual language

Dark-first, in the idiom of a media manager rather than a Home Assistant panel:
a deep near-neutral ground, surfaces raised by luminance rather than borders, a
warm projector accent, and semantic state badges — green compiled, amber
pending, red failed. Design tokens are CSS custom properties; Tailwind v4 is
configured in CSS with no `tailwind.config.js`.

## State

Composables over `shallowRef`: `useSession`, `useClips`, `useJobs`, `useApi`.
Four screens and three stores do not justify adding Pinia to an App image.

## Accessibility

The current template supports reordering from a focused drag handle with the
arrow keys and reports action results through `aria-live`. Both carry over.
Tab-equivalent navigation keeps its roles, and the drawer traps focus and
restores it on close.

## Testing

- The markup assertions in `tests/worker/test_library_manager_web.py` are
  rewritten as JSON contract assertions against `GET /manager/clips`, keeping
  the same edge cases: an unavailable output exposes only the `source` target,
  and `failed_reason` is present for `failed` and `invalid` states.
- Behavioral coverage in that file — auth, CSRF, sessions, chunked uploads,
  trash, delete confirmation — is kept as is.
- Vitest with `@vue/test-utils` covers filtering, bulk selection, and keyboard
  reordering.
- `scripts/verify.sh` runs `npm ci`, `npm run test:unit`, and `npm run build` in
  `ui/`, skipping with a notice when Node is absent locally, mirroring how it
  already treats Docker. The Quality workflow adds `actions/setup-node`.

## Layout

```
ui/
  index.html  vite.config.ts  package.json  tsconfig.json
  src/
    main.ts  App.vue  router.ts  style.css
    composables/  useSession  useClips  useJobs  useApi
    views/        LibraryView  OrderView  ImportView  SystemView
    components/   ClipCard  ClipTable  ClipDrawer  JobIndicator  BulkBar  UploadDrop
    test/
```

## Accepted risk

Without the Node build, the Worker has no UI. `GET /` returns 503 naming the
command to run, and `docs/development.md` documents the workflow.
