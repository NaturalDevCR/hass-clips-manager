# Task 2 Report: Worker API v1 contract

## Status

Complete. The versioned contract, contract tests, and API behavior
documentation are implemented. All product identifiers and documentation are
English-only, and mutation operations require bearer authentication and an
`Idempotency-Key`.

## Deliverables

- `contract/openapi-v1.yaml`: OpenAPI 3.0.3 contract for all approved `/api/v1`
  routes, bearer auth, reusable domain/error schemas, compatibility metadata,
  mutation headers, and standard error responses.
- `tests/contract/test_openapi_schema.py`: route, authentication,
  idempotency, and response coverage checks.
- `tests/contract/test_api_error_schema.py`: stable error, compatibility, and
  write-only secret field checks.
- `docs/api.md`: authentication, compatibility, idempotency, concurrency,
  polling, cancellation, timeout, disconnect, errors, and external Docker
  diagnostics guidance.

The contract is serialized as formatted JSON in the `.yaml` file; JSON is a
valid YAML 1.2 subset and keeps the test environment dependency-free.

## Tests and output

```text
.venv/bin/pytest tests/contract -v
6 passed in 0.01s

.venv/bin/ruff check tests/contract
All checks passed!

.venv/bin/python -m json.tool contract/openapi-v1.yaml
exit 0

git diff --check
exit 0
```

Review-fix commit: `11b8d86c8ce53e674aecef36d8e7e77fe59f2d06`

## Round 2 review fix

- Added `openapi-spec-validator>=0.7.2` to the `dev` dependency group.
- Contract tests now call the maintained validator's `validate()` API against
  the loaded OpenAPI document, while retaining the typed-list and focused
  contract assertions.

### Round 2 verification

```text
.venv/bin/pytest tests/contract -v
7 passed in 0.11s

.venv/bin/ruff check tests/contract
All checks passed!

.venv/bin/python -m json.tool contract/openapi-v1.yaml
exit 0

git diff --check
exit 0
```

## Commit

`feat: define worker API v1 contract` (`65f5acbf05b902babb0db2b9dba44c21fdd786dd`)

## Self-review

- Confirmed all 17 approved route/method combinations are declared.
- Confirmed every mutation declares required `Idempotency-Key` and 401/409/422/429/503 responses.
- Confirmed health compatibility fields and stable error fields are reusable and documented.
- Confirmed bearer secrets are write-only and never included in the diagnostics example.
- Confirmed no files outside the task scope were modified.

## Concerns

- The current environment has no YAML/OpenAPI validator dependency. The
  contract tests use the Python standard library against JSON-compatible YAML;
  CI may additionally run a standards validator when one is available.

## Round 1 review fixes

- Replaced generic `Pagination.items` with typed `CollectionsPage`,
  `ProfilesPage`, `ClipsPage`, `JobsPage`, and `LogsPage` response schemas;
  added the bounded `LogEntry` schema while retaining shared page metadata.
- Added dependency-free structural OpenAPI checks for required top-level
  sections, operation IDs, methods, responses, parameters, path variables, and
  resolvable component references, plus typed-list assertions.
- Updated every route shown in `docs/api.md` to include the complete
  `/api/v1` prefix, including health, job polling, cancellation, and mutation
  examples.

### Round 1 verification

```text
.venv/bin/pytest tests/contract -v
7 passed in 0.01s

.venv/bin/ruff check tests/contract
All checks passed!

.venv/bin/python -m json.tool contract/openapi-v1.yaml
exit 0

git diff --check
exit 0
```
