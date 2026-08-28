# Task 3 report: Worker settings, typed models, and path safety

## Status

Complete. Implemented the planned `app/src/cinema_collections_worker` package,
checkout import compatibility, strict settings loading, typed domain models, and
safe path/ID/filename validation.

## Changes

- Added Pydantic `AppOptions` and immutable `WorkerSettings` models.
- Added `Settings.load(Path)` for YAML and JSON options files.
- Added safe App defaults: loopback bind, `/data` persistent state/log/temp
  locations, required non-empty bearer secret, one-GB disk reserve, and no
  hardware acceleration.
- Added `RootKey` enum and `SafePathResolver`, including canonical root and
  candidate resolution, absolute/traversal/control-character rejection, and
  out-of-root symlink rejection.
- Added typed `ClipState`, `CollectionRef`, and `ClipRecord` models.
- Added focused worker settings/path tests.
- Kept the existing top-level package importable and configured Hatch to package
  the planned `app/src` package.

## Tests and output

- `.venv/bin/pytest -q`: **21 passed**.
- `.venv/bin/pytest tests/worker/test_settings.py tests/worker/test_paths.py -v`:
  **11 passed**.
- `.venv/bin/ruff check app/src`: **All checks passed**.
- `.venv/bin/pyright app/src/cinema_collections_worker`: **0 errors, 0 warnings**.
- `git diff --check`: passed.

## Self-review

- No raw shell execution, Home Assistant changes, personal paths, collection
  names, or arbitrary caller-supplied root keys are introduced.
- Resolver roots must be absolute approved roots, while candidate paths must be
  relative and remain inside their canonical root.
- Secrets are represented as Pydantic `SecretStr` and are not included in
  settings repr output.
- Options reject unknown keys to avoid silently accepting unsafe configuration.

## Concerns

- The default source/compiled roots are conventional container mount points
  (`/media/source` and `/media/compiled`); deployments should explicitly mount
  and configure approved roots as later app wiring is added.
- Later tasks may extend the domain model set; this task intentionally keeps
  models limited to shared collection/clip primitives.

## Commit

`feat(worker): add validated settings and paths`

## Round 1 follow-up

- Added a Pydantic validator for `ClipRecord.collection_id`, matching the
  existing `CollectionRef.id` collection-ID enforcement.
- Added explicit filename tests for empty names, traversal components, and
  slash/backslash path components.
- `.venv/bin/pytest tests/worker/test_settings.py tests/worker/test_paths.py -v`:
  **16 passed**.
- `.venv/bin/ruff check app/src`: **All checks passed**.
- `.venv/bin/pyright app/src/cinema_collections_worker`: **0 errors, 0 warnings**.
- `git diff --check`: passed.
