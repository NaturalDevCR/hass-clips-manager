"""Authentication and request preconditions for the local Worker API."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(scheme_name="bearerAuth", bearerFormat="WorkerToken", auto_error=False)


def require_bearer_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> None:
    """Require the configured bearer secret without exposing comparison details."""

    expected = request.app.state.settings.bearer_secret.get_secret_value()
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(credentials.credentials, expected)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")


def require_idempotency_key(
    request: Request,
    value: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> str:
    """Return a bounded idempotency key for every state-changing request."""

    if not value or len(value) > 128:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Idempotency-Key is required and must be at most 128 characters",
        )
    return value
