"""Authenticated client for the Cinema Collections Worker API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import API_PREFIX, DEFAULT_REQUEST_TIMEOUT, MAX_REQUEST_ATTEMPTS, RETRY_BACKOFF_SECONDS
from .models import WorkerContractError, WorkerHealth, WorkerStatus


class WorkerApiError(RuntimeError):
    """Base error for safe, user-facing Worker client failures."""


class WorkerApiAuthenticationError(WorkerApiError):
    """The Worker rejected the configured bearer credential."""


class WorkerApiConnectionError(WorkerApiError):
    """The Worker was unavailable or did not respond in time."""


class WorkerApiProtocolError(WorkerApiError):
    """The Worker returned a response outside the API contract."""


class WorkerApiCompatibilityError(WorkerApiError):
    """The paired Worker does not support this integration version."""


class WorkerApiRetryableError(WorkerApiError):
    """The Worker returned a transient response after bounded retries."""


class WorkerApiValidationError(WorkerApiError):
    """The Worker rejected a user-supplied mutation payload."""


class WorkerApiConflictError(WorkerApiError):
    """The Worker rejected a stale revision or reused conflicting request key."""


def normalize_endpoint(value: object) -> str:
    """Validate and canonicalize a Worker base URL without retaining credentials."""
    if not isinstance(value, str):
        raise ValueError("Worker endpoint must be a string")
    endpoint = value.strip()
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Worker endpoint must be an HTTP(S) base URL without embedded credentials")
    if parsed.path not in {"", "/"}:
        raise ValueError("Worker endpoint must not include a path")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


class WorkerApiClient:
    """Per-entry Worker API client backed by Home Assistant's aiohttp session."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        session: ClientSession,
        *,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self._endpoint = normalize_endpoint(endpoint)
        if not token.strip():
            raise ValueError("Worker credential cannot be empty")
        self._token = token.strip()
        self._session = session
        self._timeout = ClientTimeout(total=timeout)

    async def async_health(self) -> WorkerHealth:
        """Get authenticated Worker health and compatibility metadata."""
        payload = await self._async_get("/health")
        try:
            return WorkerHealth.from_dict(payload)
        except WorkerContractError as error:
            raise WorkerApiProtocolError(
                "Worker health response did not match the API contract"
            ) from error

    async def async_status(self) -> WorkerStatus:
        """Get authenticated Worker operational state."""
        payload = await self._async_get("/status")
        try:
            return WorkerStatus.from_dict(payload)
        except WorkerContractError as error:
            raise WorkerApiProtocolError(
                "Worker status response did not match the API contract"
            ) from error

    async def async_create_collection(
        self, payload: Mapping[str, object], *, idempotency_key: str
    ) -> Mapping[str, Any]:
        """Create a Worker collection using the published mutation contract."""
        return await self._async_mutate("POST", "/collections", payload, idempotency_key)

    async def async_patch_collection(
        self,
        collection_id: str,
        revision: int,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        """Patch a Worker collection with its required optimistic revision."""
        return await self._async_mutate(
            "PATCH",
            f"/collections/{collection_id}",
            payload,
            idempotency_key,
            {"If-Match-Revision": str(revision)},
        )

    async def async_create_profile(
        self, payload: Mapping[str, object], *, idempotency_key: str
    ) -> Mapping[str, Any]:
        """Create a Worker processing profile."""
        return await self._async_mutate("POST", "/profiles", payload, idempotency_key)

    async def async_patch_profile(
        self,
        profile_id: str,
        revision: int,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        """Patch a Worker profile with its required optimistic revision."""
        return await self._async_mutate(
            "PATCH",
            f"/profiles/{profile_id}",
            payload,
            idempotency_key,
            {"If-Match-Revision": str(revision)},
        )

    async def async_compile(
        self,
        collection_id: str,
        *,
        strategy: str = "scan_and_compile_changed_or_missing",
        skip_if_processing: bool = True,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        """Request one idempotent collection compilation without local media work."""
        return await self._async_mutate(
            "POST",
            "/compile",
            {
                "collection_id": collection_id,
                "strategy": strategy,
                "skip_if_processing": skip_if_processing,
            },
            idempotency_key,
        )

    async def async_scan(
        self,
        *,
        collection_ids: list[str] | None = None,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        """Request an idempotent Worker scan for selected collections."""
        return await self._async_mutate(
            "POST", "/scan", {"collection_ids": collection_ids}, idempotency_key
        )

    async def _async_get(self, path: str) -> Mapping[str, Any]:
        """Request an idempotent GET endpoint with bounded retries and safe errors."""
        for attempt in range(MAX_REQUEST_ATTEMPTS):
            try:
                return await self._async_get_once(path)
            except (WorkerApiConnectionError, WorkerApiRetryableError):
                if attempt == MAX_REQUEST_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))

        raise AssertionError("bounded Worker retry loop must return or raise")

    async def _async_get_once(self, path: str) -> Mapping[str, Any]:
        """Perform one GET request; only GET operations are retried by this client."""
        try:
            async with self._session.get(
                f"{self._endpoint}{API_PREFIX}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            ) as response:
                if response.status == 401:
                    raise WorkerApiAuthenticationError("Worker rejected the configured credential")
                if response.status in {429, 503}:
                    raise WorkerApiRetryableError(
                        f"Worker request failed with HTTP status {response.status}"
                    )
                if response.status < 200 or response.status >= 300:
                    raise WorkerApiError(
                        f"Worker request failed with HTTP status {response.status}"
                    )
                payload = await response.json(content_type=None)
        except WorkerApiError:
            raise
        except (TimeoutError, ClientError, OSError) as error:
            raise WorkerApiConnectionError("Worker request could not be completed") from error

        if not isinstance(payload, Mapping):
            raise WorkerApiProtocolError("Worker response must be a JSON object")
        return cast(Mapping[str, Any], payload)

    async def _async_mutate(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        """Perform a single non-retried Worker mutation with mandatory safeguards."""
        if not idempotency_key:
            raise ValueError("Worker mutation requires an idempotency key")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Idempotency-Key": idempotency_key,
            "X-Client-Version": "1.0.0",
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            async with self._session.request(
                method,
                f"{self._endpoint}{API_PREFIX}{path}",
                headers=headers,
                json=dict(payload),
                timeout=self._timeout,
            ) as response:
                if response.status == 401:
                    raise WorkerApiAuthenticationError("Worker rejected the configured credential")
                if response.status == 409:
                    raise WorkerApiConflictError(
                        "Worker rejected the stale revision or request conflict"
                    )
                if response.status == 422:
                    raise WorkerApiValidationError("Worker rejected the submitted configuration")
                if response.status in {429, 503}:
                    raise WorkerApiRetryableError(
                        f"Worker request failed with HTTP status {response.status}"
                    )
                if response.status < 200 or response.status >= 300:
                    raise WorkerApiError(
                        f"Worker request failed with HTTP status {response.status}"
                    )
                response_payload = await response.json(content_type=None)
        except WorkerApiError:
            raise
        except (TimeoutError, ClientError, OSError) as error:
            raise WorkerApiConnectionError("Worker request could not be completed") from error
        if not isinstance(response_payload, Mapping):
            raise WorkerApiProtocolError("Worker response must be a JSON object")
        return cast(Mapping[str, Any], response_payload)
