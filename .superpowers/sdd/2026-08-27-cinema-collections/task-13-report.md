# Task 13 report: coordinator, entities, and automation services

## Status

Complete.

## Implementation

- Added a bounded `CinemaCollectionsCoordinator` snapshot that combines local
  collection-policy resolution with Worker health/status polling. Worker
  connection failures remain a degraded snapshot and increase the refresh
  interval exponentially up to five minutes; policy and playback history are
  not changed.
- Added Home Assistant sensors for active collection, processing state, queue
  depth, latest error, current-job progress, and Worker version. State
  attributes expose resolution reason, queue depth, progress, compatibility,
  and a sanitized Worker error. No device entities or playback controls were
  created.
- Added the durable automatic/default/explicit collection override Select and
  explicit scan, compile, retry, cancel, temporary-cleanup, and history-reset
  buttons. Retry uses the published compile API with `compile_stale_only`; it
  does not invent a Worker endpoint.
- Added validated domain services: `select_next_clip`, `reset_history`,
  `scan_library`, `compile_collection`, `compile_all`, `retry_failed`,
  `cancel_processing`, and `set_collection_override`.
- `select_next_clip` is a Home Assistant response-only service and returns
  structured media details including `media_content_id` and
  `media_content_type` for automations. The integration never controls a
  player or reads media files directly.
- Extended the existing Worker client only with published cancellation and
  Worker-tracked temporary-cleanup operations.

## Tests

Red-state verification was run before implementation:

```text
.venv/bin/pytest tests/integration/test_entities.py tests/integration/test_services.py -v
2 collection errors: coordinator.py and services.py were absent.
```

Fresh final verification:

```text
.venv/bin/pytest tests/integration/test_entities.py tests/integration/test_services.py -v
8 passed

.venv/bin/pytest -q
160 passed

.venv/bin/ruff check custom_components/cinema_collections tests/integration/test_entities.py tests/integration/test_services.py
All checks passed

.venv/bin/ruff format --check custom_components/cinema_collections tests/integration/test_entities.py tests/integration/test_services.py
18 files already formatted

.venv/bin/pyright custom_components/cinema_collections
0 errors, 0 warnings, 0 informations

git diff --check
exit 0
```

Repository-wide Ruff/format checks still report unrelated pre-existing Worker
test/style files; they were left outside this task's scope. The full pytest
suite passes.

## Self-review

- All service and entity identifiers are English and stable-ID based.
- Coordinator failures are bounded and graceful; no Worker disconnect removes
  local policy or history.
- No FFmpeg work, storage edits, direct media reads, playback, or physical
  device control was introduced.
- Services validate missing/unknown/disabled collections and avoid ambiguous
  calls when multiple config entries are loaded.
