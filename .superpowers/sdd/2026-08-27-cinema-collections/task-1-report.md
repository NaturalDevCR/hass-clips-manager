# Task 1 implementation report

Status: DONE

## Implementation

- Added Python 3.13 project metadata and development dependency groups in
  `pyproject.toml`, including pytest markers, Ruff E/F/I/UP/B/SIM rules, and
  strict Pyright configuration for the worker and Home Assistant integration.
- Added the Cinema Collections README installation overview, Apache-2.0
  license notice, HACS metadata, App repository metadata, ignore rules, and
  development guide.
- Added quality, HACS integration validation, and multi-architecture worker
  image workflows. Pull requests build the worker image without publishing.
- Added metadata contract tests covering the HACS name, App repository type,
  and deliberate absence of `app/build.yaml`.

## Commit

- `36210dc95a7d2f103eee40abb3b9077b438762c3` — `chore: initialize cinema collections monorepo`

## Tests and checks

Commands were run with `uv run --group dev` because pytest is not installed in
the system Python environment.

### Metadata tests

```text
uv run --group dev pytest tests/test_repository_metadata.py -v
3 passed in 0.00s
```

### Formatting and linting

```text
uv run --group dev ruff check .
All checks passed!

uv run --group dev ruff format --check .
5 files already formatted
```

### Type checking

```text
uv run --group dev pyright
File or directory .../cinema_collections_worker does not exist.
File or directory .../custom_components/cinema_collections does not exist.
0 errors, 0 warnings, 0 informations
```

The two missing-directory notices are expected at this stage; those package
roots are produced by the other implementation tasks.

## Self-review

- Confirmed no `app/build.yaml` was created.
- Confirmed workflow triggers and action configuration are valid by inspection;
  the worker workflow uses Buildx for `linux/amd64` and `linux/arm64` and sets
  `push` false on pull requests.
- Confirmed `git diff --check` reports no whitespace errors.
- Scope is limited to Task 1 metadata, tooling, documentation, workflows, and
  tests.

## Concerns

- The worker image workflow expects the root `Dockerfile` that will be added by
  the worker implementation task.
- HACS validation expects the integration files and manifest that will be
  added by the integration implementation task.
- The repository URL in `repository.yaml` is the approved project placeholder
  and should be updated if the canonical GitHub organization changes.

