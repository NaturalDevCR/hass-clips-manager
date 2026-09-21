# Quick clip editing, preview, and readable dates

## Problem

The Library Manager lets a user browse, tag, move, trash, and recompile clips,
but cannot touch the clip content itself. Fixing a clip that runs long or has
the wrong framing means downloading it, editing it elsewhere, and
re-uploading — for what is often a five-second trim or a crop.

Separately, the System view (`SystemView.vue`) prints raw ISO-8601 timestamps
for trash entries, jobs, and worker log lines. They are correct but not
readable at a glance, and carry no timezone context, which matters for a user
in Costa Rica correlating a job time against something that happened locally.

(Filename search already exists in `LibraryView.vue` and needs no change.)

## Goals

- Trim (cut start/end) and crop a clip's source video from the Library
  Manager, with a live video preview, without leaving the browser.
- Show trash/job/log timestamps in a human-readable form, in Costa Rica time
  by default, with the timezone overridable per browser.

## Non-goals

- Editing the compiled *output* (only the source is edited; recompilation
  remains a separate, existing action).
- Multi-clip batch trim/crop.
- Frame-accurate trim scrubbing beyond what the browser's `<video>` element
  and a numeric seconds input provide.
- Any change to the compile pipeline, processing profiles, or playback order.

## Design

### 1. Readable dates

Add `formatDateTime(iso: string, timeZone?: string): string` to
`app/ui/src/lib/format.ts`, built on `Intl.DateTimeFormat` (locale `es-CR`,
`dateStyle: "medium"`, `timeStyle: "short"`). It replaces the raw string in
three places in `SystemView.vue`: `TrashEntry.created_at`,
`Job.created_at` / `Job.finished_at`, and `LogEntry.timestamp`. A `null`
timestamp still renders as `—`.

The timezone defaults to `America/Costa_Rica` and is stored under a
`manager-timezone` `localStorage` key, read the same way the existing
`manager-layout` preference is (best-effort, wrapped in try/catch, silently
falls back to the default when storage is unavailable). `SystemView.vue`
gets a small `<select>` of common zones (Costa Rica, UTC, and the browser's
own detected zone) to override it. This is a per-browser preference, not
worker configuration — nothing is persisted server-side.

### 2. Serving the source file for preview

`manager_web.py` gets one new read-only route:

```
GET /manager/clips/{clip_id}/source
```

Authorization reuses `_valid_session` (the existing manager cookie) — no
bearer/CSRF, since it's a GET with no side effect, the same trust boundary
`GET /manager/clips` already uses. It resolves the clip's absolute path via
`LibraryManager._source_path` and returns it as a `FileResponse`, which
Starlette serves with `Accept-Ranges`/`Range` support out of the box, so the
browser's `<video>` element can seek without downloading the whole file.
404s if the clip or its file is gone, matching the existing clip lookup
errors elsewhere in this module.

### 3. The edit operation (backend)

A new endpoint, following the shape of the existing `scan`/`recompile`
actions:

```
POST /manager/clips/{clip_id}/edit
{
  "trim_start_seconds": 0.0,
  "trim_end_seconds": 12.5,
  "crop": { "x": 0, "y": 0, "width": 1080, "height": 1920 } | null
}
```

CSRF-protected and session-gated like the other mutating clip routes.
Validation (in a new `_EditBody` model, `manager_web.py`):

- `trim_end_seconds > trim_start_seconds`, both within `[0, clip.duration_seconds]`.
- `crop`, when present, fits inside the clip's probed frame dimensions, and
  `width`/`height` are each rounded down to the nearest even number (libx264
  requires even dimensions for 4:2:0 chroma).

`LibraryManager.request_edit(clip_id, trim_start, trim_end, crop)` mirrors
`request_recompile`: it loads the clip row, builds a `JobRecord` with
`kind="edit"`, and enqueues it. Like the existing `scan` job, most of
`JobRecord`'s compile-pipeline fields (`source_fingerprint`,
`profile_fingerprint`, `duration_seconds`, ...) take inert placeholder
values — only `profile_settings` carries the real payload:
`{"trim_start_seconds": ..., "trim_end_seconds": ..., "crop": {...} | null}`.

`jobs.py` gets `_run_edit_job`, dispatched from `run_once()` alongside the
existing `kind == "scan"` / `kind == "cleanup"` branches (added *before* the
`kind != "compile"` rejection). It:

1. Resolves the clip's current source path.
2. Builds a single-pass ffmpeg command: `-ss {trim_start} -to {trim_end} -i
   {source} [-vf crop={w}:{h}:{x}:{y}] -c:v libx264 -preset veryfast -crf 18
   -c:a aac -movflags +faststart {temp_output}` — placing `-ss`/`-to` before
   `-i` for exact-seek re-encoding (the "precise" trim already agreed on),
   trading some speed for a cut that lands exactly on the requested second
   rather than the nearest keyframe.
3. On success, atomically swaps the encoded temp file into the clip's
   existing source path with `os.replace` (same primitive
   `LibraryManager._move_exact` already uses), so the on-disk path and the
   clip's `relative_source_path` never change — only the bytes behind them
   do. Only the ffmpeg step reads the pre-edit file; nothing unlinks it
   before the replacement file exists on disk, so a failed encode never
   loses the original. (This is deliberately *not* routed through the
   Trash/Restore table: trash entries model "this file is gone from the
   catalog," and `restore()` never rewrites `relative_source_path` — it
   assumes the path was merely vacated, not repointed at different bytes.
   Reusing it here would silently corrupt a later restore. The edit is the
   destructive, replace-in-place operation already agreed on; there is no
   undo beyond re-editing.)
4. Updates the clip row: new `duration_seconds` (from probing the edited
   file), `state="discovered"` (eligible for recompile, same as a fresh
   scan result), clears `failed_reason`.
5. Records an audit event (`library.edited`), matching the pattern of
   `library.moved` / `library.trashed`.

Progress reporting reuses `JobProgress` (`stage`, `percent`) the same way a
compile job does, so the existing `useJobs().follow()` polling on the
frontend needs no changes.

### 4. The editor UI (frontend)

`ClipDrawer.vue` gets a "Trim / Crop" button (next to "Re-scan source" /
"Recompile") that opens a new `ClipEditor.vue` panel (a modal, consistent
with the drawer's own overlay style) instead of a separate route — this is
meant to be a quick, in-context action, not a destination.

`ClipEditor.vue`:

- A `<video>` element with `src` pointing at
  `manager/clips/{id}/source`, muted, with a custom-built (not native)
  scrubber so the same time value drives both playback preview and the trim
  handles.
- A dual-handle range slider under the video for trim start/end, bounded by
  `[0, clip.duration_seconds]`. Dragging either handle seeks the video to
  that handle's time so the user sees the exact frame.
- A crop overlay: an absolutely-positioned draggable/resizable rectangle
  drawn on top of the video element. Aspect-ratio buttons (`16:9`, `9:16`,
  `1:1`, `4:3`, `Free`) constrain the rectangle's proportions while resizing;
  "Free" removes the constraint. A "No crop" toggle hides the rectangle
  entirely and sends `crop: null`.
- Coordinates convert from the rectangle's on-screen pixels to source pixels
  using the ratio between the video element's rendered size
  (`getBoundingClientRect`) and its natural size (`videoWidth`/`videoHeight`)
  before submitting, so the crop lands correctly regardless of how large the
  preview is drawn on screen.
- "Apply" calls `POST manager/clips/{id}/edit`, then follows the returned
  job exactly as `queued()` in `ClipDrawer.vue` already does for
  scan/recompile: status text updates from job progress, a failure surfaces
  as an error banner, and success emits `changed` so the parent reloads the
  clip list and the drawer/editor reflect the new duration and state.
- After a successful apply, the `<video>` reloads from the same URL with a
  cache-busting query parameter (the relative path is unchanged, only the
  bytes behind it), so the preview reflects the trimmed/cropped result
  without a manual refresh.

## Error handling

- Backend validation errors (bad trim range, out-of-bounds crop) return 422
  with a message the editor shows inline, before any job is queued.
- ffmpeg failures fail the job the same way a failed compile does today:
  `state="failed"`, `error` populated, clip's `failed_reason` set, visible in
  the drawer and in `SystemView`'s job table. The original source is only
  ever read, never unlinked, until the re-encoded replacement already exists
  on disk, so a failed edit never loses the clip.
- Streaming endpoint 404s cleanly if the source file is missing on disk,
  which `ClipEditor.vue` shows as a "preview unavailable" message rather than
  a broken video element.

## Testing

- Worker: unit tests for `_EditBody` validation (trim bounds, crop bounds,
  even-dimension rounding), `LibraryManager.request_edit` job construction,
  and `_run_edit_job` against a fixture clip (trim-only, crop-only, both,
  and an ffmpeg-failure path verifying the original source survives).
- Worker: a contract/integration test for `GET /manager/clips/{id}/source`
  covering a full read, a ranged read, and the session-required 401.
- UI: unit tests for the screen-to-source coordinate conversion (the one
  genuinely fiddly piece of pure logic) and for the trim slider's bounds
  clamping, following the existing `*.spec.ts` colocated style
  (`OrderEditor.spec.ts` is the closest precedent).
- UI: a `format.spec.ts` addition for `formatDateTime`, covering the CR
  default, an override zone, and a `null` input.
