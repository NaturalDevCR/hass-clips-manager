"""Authenticated client for the Cinema Collections Worker API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import API_PREFIX, DEFAULT_REQUEST_TIMEOUT, MAX_REQUEST_ATTEMPTS, RETRY_BACKOFF_SECONDS
from .models import (
    WorkerClip,
    WorkerContractError,
    WorkerHealth,
    WorkerJob,
    WorkerProfileSummary,
    WorkerStatus,
)


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

    async def async_list_clips(self) -> tuple[WorkerClip, ...]:
        """Return every Worker catalog clip with its live output availability."""
        page = 1
        clips: list[WorkerClip] = []
        while True:
            payload = await self._async_get(f"/clips?page={page}&page_size=100")
            try:
                total = payload.get("total")
                raw_items = payload.get("items")
                if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                    raise WorkerContractError(
                        "Worker clips response field 'total' must be an integer"
                    )
                if not isinstance(raw_items, list):
                    raise WorkerContractError(
                        "Worker clips response field 'items' must be an array"
                    )
                items = cast(list[object], raw_items)
                if not all(isinstance(item, Mapping) for item in items):
                    raise WorkerContractError("Worker clips response items must be objects")
                clips.extend(WorkerClip.from_dict(cast(Mapping[str, Any], item)) for item in items)
            except WorkerContractError as error:
                raise WorkerApiProtocolError(
                    "Worker clips response did not match the API contract"
                ) from error
            if len(clips) >= total:
                return tuple(clips[:total])
            if not items:
                raise WorkerApiProtocolError(
                    "Worker clips response ended before its declared total"
                )
            page += 1

    async def async_list_profiles(self) -> tuple[WorkerProfileSummary, ...]:
        """Return every Worker processing profile's stable ID and display name."""
        page = 1
        profiles: list[WorkerProfileSummary] = []
        while True:
            payload = await self._async_get(f"/profiles?page={page}&page_size=100")
            try:
                total = payload.get("total")
                raw_items = payload.get("items")
                if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                    raise WorkerContractError(
                        "Worker profiles response field 'total' must be an integer"
                    )
                if not isinstance(raw_items, list):
                    raise WorkerContractError(
                        "Worker profiles response field 'items' must be an array"
                    )
                items = cast(list[object], raw_items)
                if not all(isinstance(item, Mapping) for item in items):
                    raise WorkerContractError("Worker profiles response items must be objects")
                profiles.extend(
                    WorkerProfileSummary.from_dict(cast(Mapping[str, Any], item)) for item in items
                )
            except WorkerContractError as error:
                raise WorkerApiProtocolError(
                    "Worker profiles response did not match the API contract"
                ) from error
            if len(profiles) >= total:
                return tuple(profiles[:total])
            if not items:
                raise WorkerApiProtocolError(
                    "Worker profiles response ended before its declared total"
                )
            page += 1

    async def async_list_assets(self) -> tuple[str, ...]:
        """Return the Worker's uploaded intro/outro asset filenames."""
        payload = await self._async_get_list("/assets")
        if not all(isinstance(item, str) for item in payload):
            raise WorkerApiProtocolError("Worker assets response did not match the API contract")
        return tuple(payload)

    async def async_list_jobs(self) -> tuple[WorkerJob, ...]:
        """Return all public Worker jobs for safe integration observability."""
        page = 1
        jobs: list[WorkerJob] = []
        while True:
            payload = await self._async_get(f"/jobs?page={page}&page_size=100")
            try:
                total = payload.get("total")
                raw_items = payload.get("items")
                if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                    raise WorkerContractError(
                        "Worker jobs response field 'total' must be an integer"
                    )
                if not isinstance(raw_items, list):
                    raise WorkerContractError("Worker jobs response field 'items' must be an array")
                items = cast(list[object], raw_items)
                if not all(isinstance(item, Mapping) for item in items):
                    raise WorkerContractError("Worker jobs response items must be objects")
                jobs.extend(WorkerJob.from_dict(cast(Mapping[str, Any], item)) for item in items)
            except WorkerContractError as error:
                raise WorkerApiProtocolError(
                    "Worker jobs response did not match the API contract"
                ) from error
            if len(jobs) >= total:
                return tuple(jobs[:total])
            if not items:
                raise WorkerApiProtocolError("Worker jobs response ended before its declared total")
            page += 1

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

    async def async_cancel_job(self, job_id: str, *, idempotency_key: str) -> Mapping[str, Any]:
        """Request cooperative cancellation of one known Worker job."""
        if not job_id:
            raise ValueError("Worker cancellation requires a job ID")
        return await self._async_mutate("POST", f"/jobs/{job_id}/cancel", {}, idempotency_key)

    async def async_cleanup_temporaries(self, *, idempotency_key: str) -> Mapping[str, Any]:
        """Request Worker-tracked temporary cleanup without touching local storage."""
        return await self._async_mutate("POST", "/cleanup-temporaries", {}, idempotency_key)

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

    async def _async_get_list(self, path: str) -> list[Any]:
        """Request a JSON-array GET endpoint with the same bounded retries."""
        for attempt in range(MAX_REQUEST_ATTEMPTS):
            try:
                return await self._async_get_list_once(path)
            except (WorkerApiConnectionError, WorkerApiRetryableError):
                if attempt == MAX_REQUEST_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))

        raise AssertionError("bounded Worker retry loop must return or raise")

    async def _async_get_once(self, path: str) -> Mapping[str, Any]:
        """Perform one GET request; only GET operations are retried by this client."""
        payload = await self._async_get_json_once(path)
        if not isinstance(payload, Mapping):
            raise WorkerApiProtocolError("Worker response must be a JSON object")
        return cast(Mapping[str, Any], payload)

    async def _async_get_list_once(self, path: str) -> list[Any]:
        """Perform one GET request that must answer with a JSON array."""
        payload = await self._async_get_json_once(path)
        if not isinstance(payload, list):
            raise WorkerApiProtocolError("Worker response must be a JSON array")
        return cast(list[Any], payload)

    async def _async_get_json_once(self, path: str) -> Any:
        """Perform one GET request, mapping HTTP failures to safe client errors."""
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
                return await response.json(content_type=None)
        except WorkerApiError:
            raise
        except (TimeoutError, ClientError, OSError) as error:
            raise WorkerApiConnectionError("Worker request could not be completed") from error

    async def _async_mutate(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        """Retry one idempotent mutation safely with its stable request key."""
        if not idempotency_key:
            raise ValueError("Worker mutation requires an idempotency key")
        for attempt in range(MAX_REQUEST_ATTEMPTS):
            try:
                return await self._async_mutate_once(
                    method, path, payload, idempotency_key, extra_headers
                )
            except (WorkerApiConnectionError, WorkerApiRetryableError):
                if attempt == MAX_REQUEST_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
        raise AssertionError("bounded Worker mutation retry loop must return or raise")

    async def _async_mutate_once(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        """Perform one mutation attempt; callers retain its idempotency key."""
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
