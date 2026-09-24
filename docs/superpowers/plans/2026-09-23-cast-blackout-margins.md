# Cast-safe compiled clip margins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable black/silent lead-in and tail-out to compiled clips and expose final-file timing metadata to Home Assistant.

**Architecture:** The Worker owns two global margin settings, snapshots them into compile jobs, and includes them in the output fingerprint. FFmpeg composes non-overlapping black/silence segments around existing processed audio/video; final output timing is persisted with the output metadata. The integration forwards optional timing metadata through `select_next_clip` while preserving `duration_seconds` and zero-margin behavior for older files.

**Tech Stack:** Python 3.13, Pydantic v2, FastAPI, SQLite, ffmpeg/ffprobe, Home Assistant integration, OpenAPI YAML, pytest.

**Spec:** [docs/superpowers/specs/2026-09-23-cast-blackout-margins-design.md](../specs/2026-09-23-cast-blackout-margins-design.md)

## Global Constraints

- Margins default to 2 seconds and are configurable as non-negative finite seconds.
- Margins must not cut content or add a transition over content.
- The reported total duration comes from the final compiled file; metadata describes the exact published output.
- Preserve `select_next_clip.duration_seconds` for existing automations.
- Old outputs with no margin metadata use zero margins and content bounds `[0, duration]`.
- Cinema Collections prepares media and metadata only; Home Assistant coordinates the projector.

## Review Focus

- Zero lead-in or tail-out must be valid and produce coincident content/file boundaries; exercise in FFmpeg argument tests.
- Fractional and invalid (negative, NaN, infinity) settings must be handled deterministically; exercise in settings tests.
- A queued compile must use the margin values captured for its fingerprint; exercise enqueue/restart snapshot behavior.
- Audio and video can round pad lengths differently at non-integral frame rates; exercise generated fixture stream durations and synchronization.
- Old or malformed stored metadata must not produce impossible bounds or invented margins; exercise output recovery and selection serialization.

---

### Task 1: Worker settings and compile identity

**Files:**
- Modify: `app/src/cinema_collections_worker/settings.py`
- Modify: `app/src/cinema_collections_worker/api.py`
- Modify: `app/src/cinema_collections_worker/jobs.py`
- Modify: `app/config.yaml`
- Test: `tests/worker/test_settings.py`
- Test: `tests/worker/test_profile_fingerprint_agreement.py`

**Interfaces:**
- `AppOptions` and `WorkerSettings` produce `lead_in_duration: float = 2.0` and `tail_out_duration: float = 2.0`.
- `JobService` consumes both settings, hashes both values into the compile profile fingerprint, and stores `lead_in_duration_seconds` / `tail_out_duration_seconds` in each `JobRecord` snapshot.

- [x] **Step 1: Add failing settings tests** for default values, a fractional pair from the options file, zero, and rejection of negative/non-finite values.
- [x] **Step 2: Run the focused settings tests** and confirm the new assertions fail before implementation.
- [x] **Step 3: Add strict options and schema fields.** Extend `AppOptions`, `WorkerSettings`, and `from_options`; add add-on defaults and a non-negative numeric schema in `app/config.yaml`. Reject non-finite values with a Pydantic field validator.
- [x] **Step 4: Add a fingerprint regression test.** Build otherwise identical `JobService` snapshots with different lead-in or tail-out values and assert their profile/job fingerprints differ.
- [x] **Step 5: Snapshot the settings into each compile job.** Extend `JobRecord`; inject values from `settings` in `create_app`; incorporate canonical JSON for both values into the profile fingerprint before enqueuing.
- [x] **Step 6: Run focused settings/fingerprint tests** and confirm defaults, validation, and fingerprint changes pass.

### Task 2: FFmpeg black and silence composition

**Files:**
- Modify: `app/src/cinema_collections_worker/ffmpeg.py`
- Modify: `app/src/cinema_collections_worker/jobs.py`
- Test: `tests/worker/test_ffmpeg_arguments.py`
- Test: `tests/worker/test_timeout_scaling.py`

**Interfaces:**
- `JobRecord.lead_in_duration_seconds` and `JobRecord.tail_out_duration_seconds` are the sole pad inputs to `FfmpegCommandBuilder`.
- The builder maps one final video label and one final audio label to the existing encoder settings.

- [x] **Step 1: Add failing FFmpeg graph tests** for default two-second pads, zero pads, and fractional values. Assert leading/trailing black `tpad`, synchronized audio delay/padding, and final padded labels.
- [x] **Step 2: Run the FFmpeg argument tests** to confirm the graph assertions fail before implementation.
- [x] **Step 3: Add timestamp-aligned A/V pads after profile processing.** Use one video `tpad` filter with optional black start and stop durations; delay audio by the frame-aligned lead-in in sample units and extend its tail with `apad`. Round each configured margin up to the output frame boundary and use that same aligned duration for the audio pad. Skip zero-length options; add no crossfade.
- [x] **Step 4: Include requested margins in runtime timeout budgeting** so the existing bounded process timeout covers the longer output.
- [x] **Step 5: Run focused FFmpeg argument tests** and confirm the filter graph retains the processed content labels, pads audio/video at matching boundaries, and applies no margin transition.

### Task 3: Measure, persist, and recover output timing

**Files:**
- Modify: `app/src/cinema_collections_worker/jobs.py`
- Modify: `app/src/cinema_collections_worker/api.py`
- Modify: `app/src/cinema_collections_worker/models.py`
- Modify: `app/src/cinema_collections_worker/probe.py`
- Test: `tests/worker/test_fixture_pipeline.py`
- Test: `tests/worker/test_api_endpoints.py`
- Test: `tests/worker/test_probe.py`

**Interfaces:**
- Persisted metadata keys are `content_duration_seconds`, `lead_in_duration_seconds`, `tail_out_duration_seconds`, `content_start_offset_seconds`, and `content_end_offset_seconds`.
- `ClipRecord` and `/api/v1/clips` expose those timing values as optional numeric fields derived from the compiled output metadata.
- Legacy output recovery yields measured total duration, full-file content, and zero margins.

- [x] **Step 1: Add timing tests** for a generated output, legacy output with no timing keys, malformed/out-of-range metadata, and an output whose audio/video stream lengths differ beyond the chosen tolerance.
- [x] **Step 2: Run the focused fixture/API tests** and confirm new timing expectations fail before implementation.
- [x] **Step 3: Probe the final media timeline.** Capture actual container and stream durations from the completed temporary output; derive pad boundaries on the encoded frame/audio timeline, clamp/validate offsets against actual duration, and reject materially unsynchronized output before publication.
- [x] **Step 4: Publish atomically and persist timing with the output fingerprint and measured `output_duration_seconds`.** Keep all values in the same metadata update that marks the clip ready.
- [x] **Step 5: Extend output recovery and live serialization.** When old rows have no margin metadata, probe the exact available compiled file and synthesize the zero-margin interpretation. Validate stored numeric bounds before exposing them; do not expose stale timing after the output path becomes unavailable.
- [x] **Step 6: Run focused fixture/API tests** and confirm metadata reports final-file duration and legacy files report full-file content.

### Task 4: Propagate timing through Home Assistant selection

**Files:**
- Modify: `custom_components/cinema_collections/models.py`
- Modify: `custom_components/cinema_collections/selection.py`
- Modify: `custom_components/cinema_collections/services.py`
- Modify: `custom_components/cinema_collections/services.yaml`
- Test: `tests/integration/test_selection.py`
- Test: `tests/integration/test_services.py`
- Test: `tests/integration/test_api_client.py`

**Interfaces:**
- `WorkerClip` and `ClipAvailability` carry optional compiled timing numbers.
- `SelectResponse` retains `duration_seconds` and adds `duration`, `content_duration`, `lead_in_duration`, `tail_out_duration`, `content_start_offset`, and `content_end_offset`.
- `select_next_clip` serializes the six new response fields when a compiled output duration is known.

- [x] **Step 1: Add failing integration tests** for fractional timing values, absent timing values with measured duration, and unknown duration. Assert `duration_seconds` remains present and unchanged.
- [x] **Step 2: Run focused integration tests** to confirm the new response fields are missing.
- [x] **Step 3: Parse optional Worker fields safely.** Validate booleans are not accepted as numbers, reject negative/non-finite values, and treat absent legacy fields as zero margins with full-file content bounds.
- [x] **Step 4: Propagate the values through selection.** For a known duration, preserve the current `duration_seconds` while serializing the new metadata fields; for unknown duration, leave timing fields null/unknown rather than inventing values.
- [x] **Step 5: Document all new service response fields** in `services.yaml` with units and legacy behavior.
- [x] **Step 6: Run focused selection, service, and API-client tests** for new Worker responses and old Worker responses.

### Task 5: Contract and user documentation

**Files:**
- Modify: `contract/openapi-v1.yaml`
- Modify: `docs/api.md`
- Modify: `docs/configuration.md`
- Modify: `docs/architecture.md`
- Test: `tests/contract/test_openapi_schema.py`

**Interfaces:**
- OpenAPI `Clip` documents optional compiled timing fields with seconds as units.
- Home Assistant service documentation lists the six additional timing properties while retaining `duration_seconds`.

- [x] **Step 1: Add contract assertions** that the clip timing properties are optional numbers and preserve the existing required-field set.
- [x] **Step 2: Update OpenAPI and API docs.** Describe each field, final-file duration semantics, zero-margin fallback, and compatibility behavior.
- [x] **Step 3: Update configuration and architecture docs.** Explain the Worker margin options and state that only Home Assistant coordinates the projector.
- [x] **Step 4: Run focused contract/documentation checks** and ensure examples use seconds and permit fractional JSON numbers.

### Task 6: Version metadata and publish both releases

**Files:**
- Modify: `app/config.yaml`
- Modify: `app/src/cinema_collections_worker/api.py`
- Modify: `custom_components/cinema_collections/manifest.json`
- Modify: `README.md` or release notes file if the repository's release process requires a release summary.

**Interfaces:**
- Worker and integration versions follow the repository's separate `worker-vX.Y.Z` and `integration-vX.Y.Z` release series.
- The additive v1 contract remains compatible with the current API version and old Worker clients.

- [x] **Step 1: Review the compatibility behavior.** Confirm new integration parsing tolerates old Worker payloads and existing integration consumers still receive `duration_seconds`.
- [x] **Step 2: Increment Worker and integration minor versions** in `app/config.yaml`, `api.py`, and `manifest.json` according to current release numbers.
- [ ] **Step 3: Run the repository's documented release gate** `scripts/verify.sh`, plus any required architecture image build.
- [ ] **Step 4: Commit implementation and release metadata** with conventional commit messages following repository history.
- [ ] **Step 5: Publish the `worker-vX.Y.Z` and `integration-vX.Y.Z` GitHub releases/tags** and confirm the release artifacts are visible. Do not publish either component before the other is ready.

---

## Plan self-review

- **Spec coverage:** Configuration, fingerprint snapshots, FFmpeg composition, final probing, synchronized timing metadata, legacy recovery, Home Assistant serialization, contract, docs, compatibility, and release are each assigned above.
- **Placeholder scan:** No TODO/TBD steps; every task names its files, interfaces, ordered actions, and focused verification.
- **Type consistency:** Worker metadata is optional on the API/catalog edge and converted to concrete zero-margin bounds only when output duration is known. Selection retains `duration_seconds` and represents new durations as seconds.
- **Review Focus:** Each of the five listed input/failure classes is assigned a focused test in the task owning that code.
