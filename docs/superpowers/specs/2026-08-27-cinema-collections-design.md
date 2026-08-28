# Cinema Collections Design Specification

Status: architecture approved; specification review pending.

## Purpose

Cinema Collections is a monorepo with two independently installable Home Assistant components.

1. cinema_collections is a HACS custom integration that owns Home Assistant policy and automation-facing behavior.
2. cinema-collections-worker is a Home Assistant App and external Docker image that owns media filesystem work and FFmpeg processing.

The product manages user-defined themed video collections. It never controls playback, Cast, projectors, screens, music, or other physical devices. Existing playback automations retain that responsibility.

All product identifiers, source code, routes, configuration keys, documentation, logs, and default product strings are English. The integration supplies English source translations and Spanish translations.

## Goals and limits

Goals:

- Configure, schedule, scan, compile, catalog, and select clips from any number of collections.
- Run all FFmpeg and ffprobe work outside Home Assistant Core.
- Provide deterministic schedules, manual overrides, persistent no-repeat history, and restart recovery.
- Provide a native Home Assistant monitoring/control dashboard and a focused Worker Library Manager.
- Keep migration from existing automations observable and reversible.

Version-one limits:

- No custom Lovelace JavaScript card.
- No direct playback or device control.
- No arbitrary shell commands, raw FFmpeg filters, or arbitrary host filesystem access.
- No automatic deletion of source or compiled media.
- No public Internet exposure of the Worker API.

## Component boundaries

Home Assistant Core
  - cinema_collections integration
    - Config, option, and subentry flows
    - Collection schedule and override policy
    - Persistent playback history
    - Entities, services, diagnostics, Worker client
  - cinema-collections-worker App or external container
    - Catalog and persistent compilation queue
    - Library Manager Ingress UI
    - ffprobe and FFmpeg process manager
    - Profile validation and command generation
    - Authenticated versioned local HTTP API

The integration is authoritative for schedules, overrides, and playback history. The Worker is authoritative for operational data: clips, profile fingerprints, queue, logs, output validation, and files. The integration synchronizes validated collection/profile configuration to the Worker. It never runs FFmpeg.

## Installation and release model

- HACS installs custom_components/cinema_collections.
- Home Assistant App users install app/ as cinema-collections-worker.
- External Docker users run the documented image on a private Docker network shared with Home Assistant.
- Integration and Worker versions are independent. API compatibility determines whether pairing is safe.

The App uses Dockerfile and config.yaml. build.yaml is intentionally omitted because current Home Assistant App guidance has deprecated it.

## Collection policy

A collection contains:

- id: URL-safe immutable identifier
- name: display name
- enabled: boolean
- priority: integer
- source_directory: source-root-relative path
- compiled_output_prefix: generated unique namespace
- start_datetime and end_datetime: optional timezone-aware timestamps
- processing_profile_id: stable profile ID
- is_default and allow_manual_override: booleans
- tags, notes, and optimistic-concurrency revision

Collection IDs obey a strict lowercase URL-safe slug rule. Names may change; IDs and output namespaces do not. Every configured path is relative to an approved source root.

Schedule windows are half-open: start is inclusive, end is exclusive. The resolver uses Home Assistant local time and follows this order:

1. Valid explicit manual selection.
2. Manual default-collection mode.
3. Enabled scheduled collection with highest priority.
4. Equal priority resolved by ascending immutable collection ID.
5. Enabled default collection.
6. No collection, with a reported reason.

The Select entity exposes Automatic, Default collection, and permitted collection choices. Automatic clears the override.

## Compilation schedules

Each collection can optionally configure:

- enabled
- days of week
- local time
- strategy: scan_and_compile_changed_or_missing, compile_stale_only, or scan_only
- skip_if_processing

The integration owns schedule calculation and persists a run token so restart recovery cannot duplicate an occurrence. The Worker receives idempotent scan/compile requests and runs one job at a time. Users can alternatively call the provided services from their own Home Assistant automations.

## Playback history and selection

The integration persists history through Home Assistant's supported storage API. It never edits .storage or uses helper entities as state.

For every collection it stores period start, round number, played clip IDs, last selected clip ID, and last reset time.

Selection holds an async lock, requests currently ready Worker clips for the chosen collection, excludes stale/missing/invalid/used clips, then selects randomly. After exhausting a collection, it atomically creates a new round. The configured daily local reset clears every collection history; startup and selection reconcile missed reset events.

The select_next_clip service accepts an optional collection ID and dry_run. Dry runs make no history change. A normal response includes collection and clip identity, relative output path, media URI, duration, and history_reset.

## Worker design

The Worker stores schema-versioned SQLite state in its persistent data directory: operational collection mappings, profiles, clips, queue jobs, retries, output fingerprints, audit events, and bounded log references. Transactions protect job claims, idempotency, cancellation, and catalog updates.

Version one runs exactly one FFmpeg job at a time. Interrupted jobs recover as retryable or failed, never successful.

The scanner reads only configured source roots. It rejects traversal, out-of-root symlinks, unsafe filenames, unsupported extensions, and unreadable media. ffprobe records duration, dimensions, frame rate, streams, and size. Source and profile fingerprints drive recompilation.

Each clip has a stable Worker-generated UUID plus content fingerprint. Library Manager rename/move operations preserve the UUID and playback history. Generated output names contain the collection namespace and stable ID, optionally a sanitized display slug, preventing filename collision.

The compilation lifecycle is:

1. Validate source, profile, assets, paths, output, and estimated disk space.
2. Create a tracked job-specific temporary directory.
3. Run ffprobe and loudness-analysis passes with independent timeouts.
4. Build FFmpeg arguments only from typed profile fields.
5. Parse progress and retain bounded structured logs.
6. Validate temporary output with ffprobe.
7. Atomically publish only after success.
8. Update catalog and remove only tracked temporary files.

Cancellation terminates the active FFmpeg process, removes its temporary files, and records a cancelled state. It never deletes successful output.

## Processing profiles

Profiles are typed, validated, and versioned. They specify:

- Intro/outro asset reference, enablement, reuse behavior, and missing-audio policy.
- Video geometry, rate, codec, preset, quality mode, H.264 profile/level, pixel format, fast-start, container, scale/pad/SAR, timeout, and explicitly approved hardware modes.
- Known transition types, durations, fades, and short-clip validation.
- Audio codec, bitrate, layout, rate, resampling, timeline padding/trimming, loudness targets/method, and final-mix normalization.
- Retry, temporary directory, output reuse, source-modification, naming, and retention policy.

No raw shell syntax or raw FFmpeg filters are accepted.

The built-in editable Compatibility 4K Loudness Profile implements the supplied script behavior:

- 3840x2160, 24 fps, aspect-fit padding, SAR 1.
- libx264, fast preset, CRF 23, high profile, level 5.1, yuv420p.
- MP4 fast-start; AAC 192 kbps, stereo, 48000 Hz.
- Intro-to-clip and clip-to-outro fade transitions: one second.
- Final video/audio fade in: one second; fade out: 1.5 seconds.
- Two-pass EBU R128 for every segment: I -18 LUFS, TP -1.5 dBTP, LRA 11 LU.
- Final mixed-output loudness normalization.
- Source audio required; audio padded/trimmed to each video segment.
- 300 second per-pass timeout; decode errors detected.
- Temporary output and atomic finalization.

The Worker represents the script's video scaling/padding, crossfades, fades, loudnorm, resampling, audio format, padding, and trimming with structured settings. It reuses intro analysis when the intro is reused as outro. Decode errors are configurable as warn or fail.

## Worker HTTP API

All endpoints are under /api/v1. Authentication uses a random 256-bit bearer secret. Error responses include code, message, optional details, retryable, and request_id.

- GET /health: liveness, component and API versions, compatibility.
- GET /status: queue, current job, storage, scans, latest errors.
- GET/POST/PATCH /collections: operational collection mappings.
- GET/POST/PATCH /profiles: validated profiles.
- GET /clips and GET /clips/{clip_id}: catalog and live output availability.
- POST /scan: idempotently start/resume a scan.
- POST /compile: queue changed/missing/stale clips.
- GET /jobs and GET /jobs/{job_id}: state and progress.
- POST /jobs/{job_id}/cancel: cooperative cancellation.
- GET /logs: paginated sanitized logs.
- POST /cleanup-temporaries: explicit cleanup of tracked temporary files.

Every mutation accepts Idempotency-Key. The Worker verifies a compiled output still exists before the integration returns a media URI.

## Integration

Config Flow configures and validates worker endpoint, API compatibility, credential, and media URI mapping. Options Flow manages global settings, history reset, and synchronization. Config subentries provide no-YAML collection/profile editing.

A DataUpdateCoordinator supplies Worker state. The client uses bounded timeouts, structured errors, and backoff. Worker loss does not erase history/policy and is reported as a degraded state.

The integration provides requested sensors for active collection, processing status, queue counts, errors, compilation, and Worker version; a collection override Select entity; and buttons for scan, compile, retry, cancellation, temporary cleanup, and history reset.

Required services are select_next_clip, reset_history, scan_library, compile_collection, compile_all, retry_failed, cancel_processing, and set_collection_override. Diagnostics redact credentials and unnecessary paths.

## Dashboard and Library Manager

Native Lovelace documentation covers active collection/reason, override, next schedule, priorities, queue/progress, errors, controls, and clip state. No custom card is needed in version one.

The Worker App exposes a Library Manager through App Ingress because native cards cannot safely browse and organize files. It can:

- Upload/import a source clip to a selected collection.
- Create and organize approved collection directories.
- Rename/move source clips only inside allowlisted roots.
- Add tags/notes and inspect source/output metadata, errors, and logs.
- Request scans/recompiles for selected clips.
- Move source or generated output to recoverable Worker-managed trash.
- Restore trashed items.
- Permanently delete explicitly selected source, output, or both after a two-stage confirmation.

Permanent deletion resolves exactly one catalog item to exactly one validated path, records an audit event, and never uses broad recursive deletion, globs, or user-supplied filesystem paths. Trash is never automatically purged in version one. Deleting a source never automatically deletes compiled output.

## Security

- Supervisor installation maps only required persistent/app/media folders, never all Home Assistant configuration.
- Default supervised deployment uses internal App networking without a public host port.
- External Docker defaults to a private network with no published port. LAN access is explicit opt-in with private CIDR restriction and TLS guidance.
- Every filesystem action uses canonical allowlisted-root resolution.
- Logs and diagnostics redact secrets and sensitive paths.
- Disk-space checks happen before work. Partial outputs never become ready.
- Cleanup targets only tracked temporary files under the configured temporary root.

## Testing and quality

Use typed modern Python, Ruff, Pyright, pytest, semantic versioning, and CI.

Tests cover collection resolution, schedule windows, overrides, playback rounds/reset, profile validation, safe FFmpeg argument generation, containment, catalog reconciliation, deduplication, cancellation, atomic publishing, trash/delete guards, integration flows/entities/services/diagnostics, API authentication/idempotency/compatibility, and Worker disconnect recovery.

FFmpeg/ffprobe are mocked for unit tests. CI also uses small fixture videos. Contract tests validate the documented API schema against the Worker and integration client. Tests never start playback or control physical devices.

## Migration and rollback

Migration begins in observation mode: configure a new Worker namespace, scan/compile copies, validate catalog/profile behavior, dry-run selection, and compare generated media URIs. Existing automations/helpers remain untouched.

Only after separate explicit user approval may documentation demonstrate replacing the selection portion of a chosen playback automation. The automation retains Cast, projector, screen, music, and cleanup ownership. Rollback removes that one service call or disables the components; the original automation and helpers remain intact.

## Proposed repository layout

cinema-collections/
- README.md
- LICENSE
- pyproject.toml
- hacs.json
- repository.yaml
- .github/workflows/
- contract/openapi-v1.yaml
- custom_components/cinema_collections/
  - manifest.json
  - config_flow.py
  - coordinator.py
  - api_client.py
  - history.py
  - resolver.py
  - sensor.py
  - button.py
  - select.py
  - services.yaml
  - diagnostics.py
  - translations/en.json
  - translations/es.json
- app/
  - Dockerfile
  - config.yaml
  - DOCS.md
  - rootfs/usr/bin/cinema-collections-worker
  - src/cinema_collections_worker/
- docs/
  - architecture.md
  - api.md
  - installation.md
  - configuration.md
  - processing-profiles.md
  - migration.md
  - dashboard.md
  - development.md
  - security.md
  - superpowers/specs/2026-08-27-cinema-collections-design.md
- tests/integration/
- tests/worker/
- tests/contract/
- scripts/

