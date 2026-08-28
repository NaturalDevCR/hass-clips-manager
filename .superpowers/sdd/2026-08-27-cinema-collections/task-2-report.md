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

## Commit

`feat: define worker API v1 contract` (`COMMIT_HASH_FILLED_AFTER_COMMIT`)

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
