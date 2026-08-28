# Cinema Collections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a HACS integration and separately installable Home Assistant App that safely manages, compiles, schedules, and selects themed video clips.

**Architecture:** The Home Assistant integration owns collection policy, schedules, overrides, history, Home Assistant entities, and services. The Worker owns its SQLite catalog, profile validation, ffprobe/FFmpeg work, queue, file lifecycle, and authenticated API. Both components communicate solely through the versioned contract in contract/openapi-v1.yaml.

**Tech Stack:** Python 3.13, Home Assistant integration APIs, aiohttp, FastAPI, Pydantic, SQLAlchemy asyncio with SQLite, Uvicorn, FFmpeg, ffprobe, pytest, pytest-homeassistant-custom-component, Ruff, Pyright, Docker Buildx, GitHub Actions.

**Spec:** docs/superpowers/specs/2026-08-27-cinema-collections-design.md

## Global Constraints

- Do not modify an existing Home Assistant installation, its automations, helpers, or physical devices.
- All implementation identifiers, configuration keys, default UI strings, routes, logs, and docs are English.
- Include English and Spanish integration translations.
- The integration must never execute FFmpeg or ffprobe.
- Do not accept raw shell commands, raw FFmpeg filters, absolute user paths, traversal paths, or arbitrary filesystem access.
- Worker filesystem mutations must resolve to an allowlisted root and be auditable.
- No automatic source/compiled deletion; permanent deletion requires explicit selected-item confirmation.
- Keep HACS runtime files inside custom_components/cinema_collections.
- App builds use Dockerfile and config.yaml. Do not add deprecated build.yaml.
- Each change is type-checked, linted, and tested before its commit.

---

## Repository map

| Area | Responsibility |
| --- | --- |
| contract/openapi-v1.yaml | Single API schema and error contract |
| app/src/cinema_collections_worker | Worker settings, catalog, queue, FFmpeg, HTTP API, Library Manager |
| app/rootfs | Container entry point |
| custom_components/cinema_collections | HACS runtime package |
| tests/worker | Worker unit/API tests |
| tests/integration | Home Assistant integration tests |
| tests/contract | OpenAPI and client/server compatibility tests |
| docs | User/admin/developer documentation |
| .github/workflows | CI, HACS validation, app build/release verification |

---

### Task 1: Establish the monorepo, tooling, and release metadata

**Files:**
- Create: pyproject.toml
- Create: README.md
- Create: LICENSE
- Create: hacs.json
- Create: repository.yaml
- Create: .gitignore
- Create: .github/workflows/quality.yml
- Create: .github/workflows/hacs.yml
- Create: .github/workflows/worker-image.yml
- Create: docs/development.md
- Test: tests/test_repository_metadata.py

**Interfaces:**
- Produces project scripts: pytest, ruff check, ruff format --check, pyright.
- Produces Worker package import root: cinema_collections_worker.
- Produces HACS integration root: custom_components/cinema_collections.

- [ ] **Step 1: Write metadata tests**

  Assert HACS metadata names Cinema Collections, repository.yaml identifies the App repository, and app/build.yaml does not exist.

- [ ] **Step 2: Run the metadata test to verify failure**

  Run: pytest tests/test_repository_metadata.py -v
  Expected: FAIL because metadata files do not exist.

- [ ] **Step 3: Add root metadata and development dependencies**

  Define Python 3.13, pytest markers, Ruff rules E/F/I/UP/B/SIM, Pyright strict checking for app and integration, and dedicated dependency groups for worker and Home Assistant tests. Add an Apache-2.0 LICENSE and English README installation overview.

- [ ] **Step 4: Add minimal CI workflows**

  Quality runs formatting, linting, typing, and all tests. HACS validation uses the integration category. Worker build uses Docker Buildx for amd64 and arm64 without publishing on pull requests.

- [ ] **Step 5: Verify tooling and commit**

  Run: pytest tests/test_repository_metadata.py -v && ruff check . && pyright
  Expected: PASS.
  Commit: chore: initialize cinema collections monorepo

---

### Task 2: Define the versioned HTTP contract first

**Files:**
- Create: contract/openapi-v1.yaml
- Create: tests/contract/test_openapi_schema.py
- Create: tests/contract/test_api_error_schema.py
- Create: docs/api.md

**Interfaces:**
- Defines API prefix: /api/v1.
- Defines error object: code, message, details, retryable, request_id.
- Defines compatibility fields: api_version, min_client_version, max_client_version.
- Defines every endpoint approved in the specification.

- [ ] **Step 1: Write failing contract tests**

  Assert the schema validates, declares bearer authentication, supplies Idempotency-Key on mutations, and includes every approved route.

- [ ] **Step 2: Run contract tests to verify failure**

  Run: pytest tests/contract/test_openapi_schema.py tests/contract/test_api_error_schema.py -v
  Expected: FAIL because the schema is absent.

- [ ] **Step 3: Write OpenAPI schemas**

  Define reusable objects for Health, Status, Collection, Profile, Clip, Job, Progress, Error, Pagination, ScanRequest, CompileRequest, and CancelledJob. Mark secret fields write-only and define 401, 409, 422, 429, and 503 responses where relevant.

- [ ] **Step 4: Document response behavior**

  In docs/api.md, document authentication, compatibility, idempotency, cancellation, timeout, polling progress, disconnect behavior, and error codes. Provide a non-secret curl example only for external Docker diagnostics.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/contract -v
  Expected: PASS.
  Commit: feat: define worker API v1 contract

---

### Task 3: Create Worker settings, typed models, and path safety

**Files:**
- Create: app/src/cinema_collections_worker/__init__.py
- Create: app/src/cinema_collections_worker/settings.py
- Create: app/src/cinema_collections_worker/models.py
- Create: app/src/cinema_collections_worker/paths.py
- Create: tests/worker/test_settings.py
- Create: tests/worker/test_paths.py

**Interfaces:**
- Settings.load(options_path: Path) -> WorkerSettings.
- SafePathResolver.resolve(root_key: str, relative_path: str) -> Path.
- validate_collection_id(value: str) -> str.
- validate_filename(value: str) -> str.

- [ ] **Step 1: Write failing path tests**

  Cover valid root-relative paths, dot traversal, absolute paths, empty names, unsafe Unicode control characters, symlinks that escape a root, and a valid collection ID such as christmas.

- [ ] **Step 2: Run failure tests**

  Run: pytest tests/worker/test_settings.py tests/worker/test_paths.py -v
  Expected: FAIL because Worker classes do not exist.

- [ ] **Step 3: Implement strict settings and resolution**

  Use Pydantic models for app options and a Path resolver that canonicalizes each root and candidate. Reject a candidate unless candidate.is_relative_to(canonical_root). Use enum values for roots rather than caller-supplied absolute paths.

- [ ] **Step 4: Add explicit defaults**

  Set safe default bind behavior for App mode, persistent database/log/temp locations under the Worker data area, a required bearer secret, disk reserve, and no hardware acceleration. Do not include personal paths or collection names.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/worker/test_settings.py tests/worker/test_paths.py -v && ruff check app/src
  Expected: PASS.
  Commit: feat(worker): add validated settings and paths

---

### Task 4: Implement Worker persistence and collection/profile repositories

**Files:**
- Create: app/src/cinema_collections_worker/database.py
- Create: app/src/cinema_collections_worker/repositories.py
- Create: app/src/cinema_collections_worker/domain.py
- Create: tests/worker/test_repositories.py
- Create: tests/worker/test_revisions.py

**Interfaces:**
- Database.create(url: str) -> Database.
- CollectionRepository.create(payload: CollectionCreate) -> CollectionRecord.
- CollectionRepository.patch(id: str, revision: int, patch: CollectionPatch) -> CollectionRecord.
- ProfileRepository.create(payload: ProfileCreate) -> ProfileRecord.
- OptimisticConflict exception maps to HTTP 409.

- [ ] **Step 1: Write repository tests**

  Test collection ID uniqueness, immutable ID, revision increment, stale revision conflict, one default collection, profile version increment, and persistence after reopening SQLite.

- [ ] **Step 2: Run tests to verify failure**

  Run: pytest tests/worker/test_repositories.py tests/worker/test_revisions.py -v
  Expected: FAIL because database/repositories are absent.

- [ ] **Step 3: Implement schema migrations and repositories**

  Add migration tracking and tables for collections, profiles, clips, jobs, job attempts, audit events, and idempotency records. Use UTC timestamps and JSON only for bounded structured metadata.

- [ ] **Step 4: Add audited mutation support**

  Require an actor and request ID for collection/profile writes; record action type, stable target ID, and redacted summary. Never store bearer tokens.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/worker/test_repositories.py tests/worker/test_revisions.py -v
  Expected: PASS.
  Commit: feat(worker): persist configuration and audit mutations

---

### Task 5: Implement profile validation and the compatibility baseline

**Files:**
- Create: app/src/cinema_collections_worker/profile_validation.py
- Create: app/src/cinema_collections_worker/default_profiles.py
- Create: tests/worker/test_profile_validation.py
- Create: tests/worker/test_compatibility_profile.py
- Create: docs/processing-profiles.md

**Interfaces:**
- validate_profile(profile: ProcessingProfile) -> ProcessingProfile.
- profile_fingerprint(profile: ProcessingProfile, assets: AssetFingerprints) -> str.
- compatibility_4k_loudness_profile() -> ProcessingProfile.

- [ ] **Step 1: Write failing profile tests**

  Test valid compatibility profile fields, invalid dimensions, invalid LUFS targets, unsafe extension, CRF and bitrate conflict, unsupported transition, bad fade arithmetic, audio-policy conflict, and disabled hardware acceleration by default.

- [ ] **Step 2: Run failure tests**

  Run: pytest tests/worker/test_profile_validation.py tests/worker/test_compatibility_profile.py -v
  Expected: FAIL because profile validation is absent.

- [ ] **Step 3: Implement typed profile sections**

  Use Pydantic discriminated unions for quality mode, scaling strategy, audio-missing policy, loudness mode, and transition settings. Validate intro/outro references with SafePathResolver only when a job is queued.

- [ ] **Step 4: Implement the baseline exactly**

  Set the approved 4K, libx264, AAC, fade, two-pass loudness, timing, and atomic-output values. Include a decode_error_policy field defaulting to warn and document warn versus fail.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/worker/test_profile_validation.py tests/worker/test_compatibility_profile.py -v
  Expected: PASS.
  Commit: feat(worker): validate processing profiles

---

### Task 6: Implement catalog scanning and ffprobe metadata collection

**Files:**
- Create: app/src/cinema_collections_worker/probe.py
- Create: app/src/cinema_collections_worker/catalog.py
- Create: tests/worker/test_probe.py
- Create: tests/worker/test_catalog_scan.py
- Create: tests/worker/fixtures/

**Interfaces:**
- ProbeClient.probe(path: Path) -> MediaProbeResult.
- CatalogService.scan(collection_ids: set[str] | None) -> ScanSummary.
- ClipRecord state: discovered, invalid, pending, compiling, ready, failed, stale.

- [ ] **Step 1: Write failing scanner tests**

  Test added, modified, deleted, invalid, no-audio, and duplicate-name media. Test that a changed source becomes stale and that a missing source does not delete prior compiled output.

- [ ] **Step 2: Run tests to verify failure**

  Run: pytest tests/worker/test_probe.py tests/worker/test_catalog_scan.py -v
  Expected: FAIL because scanner classes are absent.

- [ ] **Step 3: Implement subprocess-isolated ffprobe**

  Invoke ffprobe with an argument list, bounded timeout, no shell, and JSON output. Parse duration, geometry, rate, audio presence, streams, and size. Classify malformed media without leaking paths.

- [ ] **Step 4: Implement reconciliation**

  Scan only configured collection source directories. Use content/file fingerprints; preserve catalog UUID on a Library Manager move, and use conservative scan-only move detection. Mark changed profile/source outputs stale.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/worker/test_probe.py tests/worker/test_catalog_scan.py -v
  Expected: PASS.
  Commit: feat(worker): scan and catalog media clips

---

### Task 7: Implement persistent queue, process runner, and atomic output publishing

**Files:**
- Create: app/src/cinema_collections_worker/queue.py
- Create: app/src/cinema_collections_worker/ffmpeg.py
- Create: app/src/cinema_collections_worker/jobs.py
- Create: tests/worker/test_queue.py
- Create: tests/worker/test_ffmpeg_arguments.py
- Create: tests/worker/test_job_cancellation.py
- Create: tests/worker/test_atomic_publish.py

**Interfaces:**
- JobService.enqueue_compile(request: CompileRequest) -> list[JobRecord].
- JobWorker.run_once() -> JobRunResult | None.
- FfmpegCommandBuilder.build(job: JobRecord) -> list[str].
- JobService.cancel(job_id: str) -> JobRecord.

- [ ] **Step 1: Write failing queue/process tests**

  Cover one-at-a-time claim, deduplication by clip/source/profile fingerprint, disk-space rejection, timeout, cancellation, retry cap, unparseable progress, temporary cleanup, and atomic final destination behavior.

- [ ] **Step 2: Run tests to verify failure**

  Run: pytest tests/worker/test_queue.py tests/worker/test_ffmpeg_arguments.py tests/worker/test_job_cancellation.py tests/worker/test_atomic_publish.py -v
  Expected: FAIL because queue/runner modules are absent.

- [ ] **Step 3: Implement safe command construction**

  Build each FFmpeg argument as a list item. Generate filter graphs only from validated profile fields. Model segment loudnorm analysis, final-mix loudnorm analysis, intro reuse, audio timeline padding/trimming, xfade/acrossfade, and final fades. Never accept a user filter string.

- [ ] **Step 4: Implement runner state transitions**

  Create job temp folders under the configured temporary root. Parse FFmpeg progress, persist stages and ETA, terminate the process group for cancellation/timeout, validate temp output via ProbeClient, and use os.replace only within the compiled root.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/worker/test_queue.py tests/worker/test_ffmpeg_arguments.py tests/worker/test_job_cancellation.py tests/worker/test_atomic_publish.py -v
  Expected: PASS.
  Commit: feat(worker): process persistent compilation jobs

---

### Task 8: Implement the authenticated Worker API and contract tests

**Files:**
- Create: app/src/cinema_collections_worker/api.py
- Create: app/src/cinema_collections_worker/auth.py
- Create: app/src/cinema_collections_worker/main.py
- Create: tests/worker/test_api_auth.py
- Create: tests/worker/test_api_endpoints.py
- Create: tests/contract/test_worker_conformance.py

**Interfaces:**
- create_app(settings: WorkerSettings) -> FastAPI.
- require_bearer_token(request: Request) -> None.
- require_idempotency_key(request: Request) -> str.
- GET /api/v1/health and all routes defined in contract/openapi-v1.yaml.

- [ ] **Step 1: Write failing API tests**

  Test missing/wrong token, health compatibility payload, idempotent replay, collection revision conflict, validation response shape, scan/compile acceptance, job cancellation, and output availability lookup.

- [ ] **Step 2: Run tests to verify failure**

  Run: pytest tests/worker/test_api_auth.py tests/worker/test_api_endpoints.py tests/contract/test_worker_conformance.py -v
  Expected: FAIL because the FastAPI app is absent.

- [ ] **Step 3: Implement API routing and error mapping**

  Add dependency-injected services, request IDs, bearer auth, idempotency persistence, paginated list responses, and a single exception-to-contract mapping layer. Return 202 for queued work and 409 for optimistic/idempotency conflicts.

- [ ] **Step 4: Enforce compatibility**

  Make health/status report Worker version, API version, and supported integration version range. Reject unsupported clients before mutating operations.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/worker/test_api_auth.py tests/worker/test_api_endpoints.py tests/contract/test_worker_conformance.py -v
  Expected: PASS.
  Commit: feat(worker): expose authenticated API v1

---

### Task 9: Add the safe Worker Library Manager and App packaging

**Files:**
- Create: app/Dockerfile
- Create: app/config.yaml
- Create: app/DOCS.md
- Create: app/rootfs/usr/bin/cinema-collections-worker
- Create: app/src/cinema_collections_worker/library_manager.py
- Create: app/src/cinema_collections_worker/templates/
- Create: app/src/cinema_collections_worker/static/
- Create: tests/worker/test_library_manager.py
- Create: tests/worker/test_app_manifest.py

**Interfaces:**
- LibraryManager.import_clip(collection_id: str, upload: UploadFile) -> ClipRecord.
- LibraryManager.move_to_trash(clip_id: str, target: TrashTarget) -> AuditEvent.
- LibraryManager.restore(trash_id: str) -> ClipRecord.
- LibraryManager.permanently_delete(clip_id: str, target: DeleteTarget, confirmation: str) -> AuditEvent.

- [ ] **Step 1: Write failing lifecycle/UI tests**

  Test upload extension/size/root checks, rename/move preserving clip UUID, trash/restore, permanent delete refusing wrong confirmation, source-only/output-only/both selection, and no automatic trash purge.

- [ ] **Step 2: Run tests to verify failure**

  Run: pytest tests/worker/test_library_manager.py tests/worker/test_app_manifest.py -v
  Expected: FAIL because lifecycle manager and App files are absent.

- [ ] **Step 3: Implement lifecycle actions**

  Use a Worker-owned trash root with catalog records and audit events. Resolve one exact catalog target at a time. Permanent deletion must receive the exact generated confirmation token and must not use globbing or recursive user paths.

- [ ] **Step 4: Package the App securely**

  Use a pinned multi-architecture Home Assistant base image, install FFmpeg/ffprobe and Python runtime dependencies, run a non-root service where supported, map only data/addon_config/media as required, keep port private, and provide an Ingress entry point. Serve a minimal server-rendered manager; do not add a custom Lovelace card.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/worker/test_library_manager.py tests/worker/test_app_manifest.py -v
  Expected: PASS.
  Commit: feat(worker): add App library manager and packaging

---

### Task 10: Create integration foundation, Config Flow, and API client

**Files:**
- Create: custom_components/cinema_collections/__init__.py
- Create: custom_components/cinema_collections/const.py
- Create: custom_components/cinema_collections/manifest.json
- Create: custom_components/cinema_collections/config_flow.py
- Create: custom_components/cinema_collections/api_client.py
- Create: custom_components/cinema_collections/models.py
- Create: tests/integration/conftest.py
- Create: tests/integration/test_config_flow.py
- Create: tests/integration/test_api_client.py

**Interfaces:**
- WorkerApiClient.async_health() -> WorkerHealth.
- WorkerApiClient.async_status() -> WorkerStatus.
- ConfigFlow async_step_user validates endpoint, credential, and compatibility.
- DOMAIN equals cinema_collections.

- [ ] **Step 1: Write failing Home Assistant tests**

  Cover successful pairing, duplicate entry abort, invalid token, incompatible Worker, timeout, and setup unload/reload.

- [ ] **Step 2: Run tests to verify failure**

  Run: pytest tests/integration/test_config_flow.py tests/integration/test_api_client.py -v
  Expected: FAIL because integration files are absent.

- [ ] **Step 3: Implement the client and config entry**

  Use aiohttp through Home Assistant session helpers, explicit timeouts, typed contract parsing, no runtime dependency outside the integration directory, and credential redaction. Store immutable endpoint/credential data in config entry data.

- [ ] **Step 4: Add setup/unload lifecycle**

  Create per-entry runtime data, client session use, and coordinated unload. Do not access local media paths or invoke processing executables.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/integration/test_config_flow.py tests/integration/test_api_client.py -v
  Expected: PASS.
  Commit: feat(integration): add Worker pairing and API client

---

### Task 11: Implement collection policy, options/subentries, and scheduling

**Files:**
- Create: custom_components/cinema_collections/resolver.py
- Create: custom_components/cinema_collections/options_flow.py
- Create: custom_components/cinema_collections/subentries.py
- Create: custom_components/cinema_collections/scheduler.py
- Create: tests/integration/test_resolver.py
- Create: tests/integration/test_collection_flows.py
- Create: tests/integration/test_scheduler.py

**Interfaces:**
- resolve_active_collection(collections: Sequence[CollectionPolicy], override: OverrideMode, now: datetime) -> SelectionResult.
- CompilationScheduler.async_handle_due(now: datetime) -> list[ScheduledRun].
- Collection subentry and profile subentry update Worker revisions through WorkerApiClient.

- [ ] **Step 1: Write failing policy tests**

  Test bounded/unbounded windows, disabled collection, explicit override rejection, default override, priority, deterministic tie, no default, and local scheduled compilation run de-duplication after restart.

- [ ] **Step 2: Run tests to verify failure**

  Run: pytest tests/integration/test_resolver.py tests/integration/test_collection_flows.py tests/integration/test_scheduler.py -v
  Expected: FAIL because policy/flow/scheduler code is absent.

- [ ] **Step 3: Implement resolver and durable policy storage**

  Store policies with the config entry/subentry APIs and use Home Assistant timezone utilities. Use stable collection IDs; never use names as keys.

- [ ] **Step 4: Implement no-YAML management flows**

  Build Config Flow/Options Flow steps for global values, collection fields, schedule, and profile selection. Synchronize Worker changes with revision checks and surface validation errors back into the flow.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/integration/test_resolver.py tests/integration/test_collection_flows.py tests/integration/test_scheduler.py -v
  Expected: PASS.
  Commit: feat(integration): manage collection policy and schedules

---

### Task 12: Implement persistent history and next-clip selection

**Files:**
- Create: custom_components/cinema_collections/history.py
- Create: custom_components/cinema_collections/selection.py
- Create: tests/integration/test_history.py
- Create: tests/integration/test_selection.py

**Interfaces:**
- PlaybackHistoryStore.async_select(collection_id: str, eligible_clip_ids: Sequence[str], dry_run: bool) -> HistorySelection.
- PlaybackHistoryStore.async_reset(collection_id: str | None) -> ResetResult.
- SelectionService.async_select(request: SelectRequest) -> SelectResponse.

- [ ] **Step 1: Write failing selection tests**

  Test independent collections, no repeat until exhaustion, reset flag/new round, daily reset, missed reset after restart, stale clip ignored, dry run no mutation, and concurrent service calls.

- [ ] **Step 2: Run tests to verify failure**

  Run: pytest tests/integration/test_history.py tests/integration/test_selection.py -v
  Expected: FAIL because history/selection modules are absent.

- [ ] **Step 3: Implement Store-backed history**

  Use Home Assistant Store API with schema version and an async lock. Persist before returning a real selection. Use Worker clip availability response rather than reading media files in the integration.

- [ ] **Step 4: Implement reset scheduling**

  Register local daily reset and startup reconciliation. Use per-collection reset operations and globally reset only when explicitly requested.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/integration/test_history.py tests/integration/test_selection.py -v
  Expected: PASS.
  Commit: feat(integration): add no-repeat playback history

---

### Task 13: Add coordinator, sensors, buttons, Select entity, and services

**Files:**
- Create: custom_components/cinema_collections/coordinator.py
- Create: custom_components/cinema_collections/sensor.py
- Create: custom_components/cinema_collections/button.py
- Create: custom_components/cinema_collections/select.py
- Create: custom_components/cinema_collections/services.py
- Create: custom_components/cinema_collections/services.yaml
- Create: tests/integration/test_entities.py
- Create: tests/integration/test_services.py

**Interfaces:**
- CinemaCollectionsCoordinator async_update_data() -> CoordinatorSnapshot.
- Select entity sets OverrideMode.
- select_next_clip supports a Home Assistant service response.
- All required service names match the specification.

- [ ] **Step 1: Write failing entity and service tests**

  Assert every required entity exists, attributes include reason/queue/progress/compatibility, disconnected Worker handling is graceful, buttons dispatch expected API requests, and service response schema contains media_content_id.

- [ ] **Step 2: Run tests to verify failure**

  Run: pytest tests/integration/test_entities.py tests/integration/test_services.py -v
  Expected: FAIL because platforms/services are absent.

- [ ] **Step 3: Implement coordinator and platforms**

  Poll status with bounded update interval and exponential failure behavior. Create translated entity descriptions with stable unique IDs. Do not create or control device entities.

- [ ] **Step 4: Implement service registration**

  Validate service data with voluptuous or Home Assistant selectors. Register select_next_clip with response support, route reset/scan/compile/retry/cancel/override requests, and return descriptive errors for empty/invalid collections.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/integration/test_entities.py tests/integration/test_services.py -v
  Expected: PASS.
  Commit: feat(integration): expose entities and automation services

---

### Task 14: Add diagnostics, translations, documentation, and migration material

**Files:**
- Create: custom_components/cinema_collections/diagnostics.py
- Create: custom_components/cinema_collections/translations/en.json
- Create: custom_components/cinema_collections/translations/es.json
- Create: tests/integration/test_diagnostics.py
- Create: docs/architecture.md
- Create: docs/installation.md
- Create: docs/configuration.md
- Create: docs/migration.md
- Create: docs/dashboard.md
- Create: docs/security.md

**Interfaces:**
- async_get_config_entry_diagnostics(hass, entry) -> dict[str, object].
- Every English translation key has a Spanish counterpart.
- Documentation contains observation-mode and external-Docker instructions.

- [ ] **Step 1: Write failing diagnostics/translation tests**

  Assert secret, authorization header, and absolute media path are absent from diagnostics. Assert en.json/es.json are valid and cover config, options, entities, buttons, Select values, services, and errors.

- [ ] **Step 2: Run tests to verify failure**

  Run: pytest tests/integration/test_diagnostics.py -v
  Expected: FAIL because diagnostics/translations are absent.

- [ ] **Step 3: Implement diagnostics and localization**

  Sanitize snapshots recursively, replace allowed-root paths with root-relative redaction labels, retain only bounded error/log excerpts, and preserve useful API compatibility state. Write English first, then Spanish equivalents.

- [ ] **Step 4: Write operational documentation**

  Explain supervised App and external Docker installation, worker token setup, roots, profiles, dashboard YAML examples, observation migration, manual service-response adoption, rollback, backups, upgrades, and no-device-control guarantee.

- [ ] **Step 5: Verify and commit**

  Run: pytest tests/integration/test_diagnostics.py -v && python -m json.tool custom_components/cinema_collections/translations/en.json >/dev/null && python -m json.tool custom_components/cinema_collections/translations/es.json >/dev/null
  Expected: PASS.
  Commit: docs: add operations, migration, and localization

---

### Task 15: End-to-end verification, release checks, and final hardening

**Files:**
- Create: tests/contract/test_integration_worker_roundtrip.py
- Create: tests/worker/test_fixture_pipeline.py
- Create: scripts/verify.sh
- Modify: README.md
- Modify: docs/api.md
- Modify: docs/development.md
- Modify: .github/workflows/quality.yml

**Interfaces:**
- Contract roundtrip: integration client reads Worker health/status/clips/jobs and selects a validated clip.
- Fixture pipeline uses a generated/small licensed video fixture and never targets a real Home Assistant installation.

- [ ] **Step 1: Write final failing integration tests**

  Cover worker disconnect/recovery, incompatible API, successful dry-run and real next-clip service response, compilation state progress, fixture compile to an atomic ready output, and no real-device interaction.

- [ ] **Step 2: Run tests to verify failure**

  Run: pytest tests/contract/test_integration_worker_roundtrip.py tests/worker/test_fixture_pipeline.py -v
  Expected: FAIL until all components are wired.

- [ ] **Step 3: Wire test application and fixtures**

  Start FastAPI in-process with a temporary allowlisted media root; mock FFmpeg for most paths and use the fixture runner only where FFmpeg exists. Ensure tests use generated token/config and no machine-specific paths.

- [ ] **Step 4: Add release verification**

  scripts/verify.sh runs format check, lint, type check, full tests, OpenAPI validation, metadata checks, translation JSON checks, and Docker build. CI invokes it on pull requests.

- [ ] **Step 5: Verify and commit**

  Run: scripts/verify.sh
  Expected: PASS with all tests, lint, typing, and contract checks green.
  Commit: chore: harden release verification

---

## Plan self-review

- Spec coverage: Tasks 1-2 establish distribution and contract; 3-9 implement every Worker responsibility including safe Library Manager; 10-14 implement every integration responsibility; Task 15 validates the complete system.
- Safety coverage: path validation, authentication, API compatibility, atomic output, cancellation, bounded logs, diagnostics redaction, trash, permanent-delete confirmation, and observation migration are each explicit tasks.
- Scope: playback/device control remains out of scope in every task.
- Incomplete-work scan: each task names files, interfaces, test command, implementation objective, verification command, and commit.
