"""Authentication and request preconditions for the local Worker API."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status


def require_bearer_token(request: Request) -> None:
    """Require the configured bearer secret without exposing comparison details."""

    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    expected = request.app.state.settings.bearer_secret.get_secret_value()
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")


def require_idempotency_key(request: Request) -> str:
    """Return a bounded idempotency key for every state-changing request."""

    value = request.headers.get("Idempotency-Key", "")
    if not value or len(value) > 128:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Idempotency-Key is required and must be at most 128 characters",
        )
    return value
