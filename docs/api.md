# Cinema Collections Worker API v1

The Worker API is a private, local HTTP API for the Home Assistant integration
and the Worker Library Manager. All routes are prefixed with `/api/v1` and are
described in [`contract/openapi-v1.yaml`](../contract/openapi-v1.yaml). The
Worker must not be exposed to the public Internet.

## Authentication

Every request uses an HTTPS (or private-network HTTP) bearer token:

```text
Authorization: Bearer <worker-secret>
```

The secret is a random 256-bit value configured out of band. It is never
returned in a response, logged, or included in diagnostics. Invalid or missing
credentials receive `401` with the standard error object.

## Compatibility and versioning

The URL prefix is versioned; v1 clients must use `/api/v1`. `GET /health`
returns `api_version`, `min_client_version`, and `max_client_version` in
addition to the Worker version. Pairing is safe only when the client version
falls within that advertised range. A client must report an incompatible
version as degraded and must not issue mutating requests.

## Idempotency and optimistic concurrency

Every `POST` and `PATCH` mutation requires a unique `Idempotency-Key` header.
Retrying a request with the same key returns the original result and does not
enqueue duplicate work. Reusing a key with a different request body is a
`409` conflict. Collection and profile updates also require the
`If-Match-Revision` header; a stale revision is rejected with `409`.

Mutating routes are `POST /collections`, `PATCH /collections/{collection_id}`,
`POST /profiles`, `PATCH /profiles/{profile_id}`, `POST /scan`,
`POST /compile`, `POST /jobs/{job_id}/cancel`, and
`POST /cleanup-temporaries`.

## Jobs, progress, and timeouts

Scan, compile, and temporary-cleanup requests return `202` and a job object.
Poll `GET /jobs/{job_id}` (or `GET /jobs`) for `state` and `progress`. Progress
has a stage, percentage, and estimated seconds remaining. The Worker runs one
FFmpeg job at a time; `429` means the caller should back off using
`Retry-After` when present.

FFmpeg and ffprobe operations have bounded Worker-side timeouts. A timeout
terminates the process group, removes only tracked temporary files, and marks
the job failed or retryable. It never publishes a partial output. Clients
should use a request timeout long enough to receive the `202` acknowledgement,
then poll the job instead of holding an HTTP request open.

## Cancellation and disconnects

`POST /jobs/{job_id}/cancel` requests cooperative cancellation and returns the
job with `cancelled` state. Cancellation never removes a previously successful
compiled output. If a client disconnects while polling or after submitting a
request, the Worker continues the accepted job; clients can reconnect and use
the idempotency key or job ID to retrieve its result. A disconnected request
does not cancel processing.

## Error responses

Errors use one JSON shape:

```json
{
  "code": "invalid_request",
  "message": "The request could not be validated.",
  "details": {"field": "collection_id"},
  "retryable": false,
  "request_id": "00000000-0000-0000-0000-000000000000"
}
```

`details` is optional and may contain bounded, non-secret context. Stable codes
include:

- `401` — `unauthorized`: bearer token missing or invalid.
- `404` — `not_found`: requested collection, profile, clip, or job is absent.
- `409` — `conflict`: stale revision, conflicting idempotency key, or job state.
- `422` — `invalid_request`: malformed or unsafe input.
- `429` — `busy`: queue capacity or one-at-a-time processing limit; retryable.
- `503` — `unavailable`: storage or required Worker dependency is unavailable;
  retryable according to the `retryable` field.

Error messages and details never disclose bearer secrets, host filesystem
paths, or raw FFmpeg command text.

## External Docker diagnostics

For an external Docker deployment, run this non-secret liveness check on the
private Docker network. Replace the address with the private Worker address;
do not publish this port publicly.

```sh
curl --fail --silent \
  -H 'Authorization: Bearer YOUR_CONFIGURED_SECRET' \
  http://cinema-collections-worker:8080/api/v1/health
```

The example uses a placeholder only; never commit or paste a real secret into
documentation, shell history, or issue reports.
