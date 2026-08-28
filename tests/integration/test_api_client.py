"""Tests for the Worker HTTP client without a Home Assistant runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from custom_components.cinema_collections.api_client import (
    WorkerApiAuthenticationError,
    WorkerApiClient,
    WorkerApiConnectionError,
    WorkerApiError,
    WorkerApiProtocolError,
)
from custom_components.cinema_collections.const import CONF_ENDPOINT, CONF_TOKEN, DOMAIN
from custom_components.cinema_collections.models import WorkerHealth, WorkerStatus


class FakeResponse:
    """Small aiohttp-response equivalent for client unit tests."""

    def __init__(self, status: int, body: Mapping[str, Any]) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self, *, content_type: object = None) -> Mapping[str, Any]:
        return self._body


class FakeSession:
    """Records a request and returns a configured response."""

    def __init__(self, response: FakeResponse | Exception | list[FakeResponse | Exception]) -> None:
        self.response = response if isinstance(response, list) else [response]
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        response = self.response.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


HEALTH = {
    "status": "ok",
    "component": "cinema-collections-worker",
    "worker_version": "1.2.3",
    "api_version": "1.0.0",
    "min_client_version": "1.0.0",
    "max_client_version": "1.x",
}

STATUS = {
    "queue_depth": 2,
    "current_job": None,
    "storage": {"available_bytes": 1024},
    "scans": {"running": False},
    "latest_errors": [],
}


@pytest.mark.asyncio
async def test_health_uses_bearer_auth_and_parses_contract() -> None:
    session = FakeSession(FakeResponse(200, HEALTH))  # type: ignore[arg-type]
    client = WorkerApiClient("http://worker.local/", "pairing-token", session)  # type: ignore[arg-type]

    health = await client.async_health()

    assert health == WorkerHealth.from_dict(HEALTH)
    assert session.calls[0]["url"] == "http://worker.local/api/v1/health"
    assert session.calls[0]["headers"] == {"Authorization": "Bearer pairing-token"}
    assert session.calls[0]["timeout"].total == 10.0


@pytest.mark.asyncio
async def test_status_parses_operational_contract() -> None:
    client = WorkerApiClient("http://worker.local", "token", FakeSession(FakeResponse(200, STATUS)))

    status = await client.async_status()

    assert status == WorkerStatus.from_dict(STATUS)


@pytest.mark.asyncio
async def test_health_rejects_invalid_token_without_exposing_credential() -> None:
    client = WorkerApiClient(
        "http://worker.local", "secret-token", FakeSession(FakeResponse(401, {}))
    )

    with pytest.raises(WorkerApiAuthenticationError) as error:
        await client.async_health()

    assert "secret-token" not in str(error.value)


@pytest.mark.asyncio
async def test_health_rejects_malformed_contract() -> None:
    client = WorkerApiClient(
        "http://worker.local", "token", FakeSession(FakeResponse(200, {"status": "ok"}))
    )

    with pytest.raises(WorkerApiProtocolError):
        await client.async_health()


@pytest.mark.asyncio
async def test_health_maps_timeout_to_connection_error() -> None:
    client = WorkerApiClient(
        "http://worker.local",
        "token",
        FakeSession([TimeoutError("request timed out")] * 3),
    )

    with pytest.raises(WorkerApiConnectionError):
        await client.async_health()


@pytest.mark.asyncio
async def test_health_retries_retryable_response_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import custom_components.cinema_collections.api_client as api_client

    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(api_client.asyncio, "sleep", record_sleep)
    session = FakeSession([FakeResponse(503, {}), FakeResponse(200, HEALTH)])
    client = WorkerApiClient("http://worker.local", "token", session)

    assert await client.async_health() == WorkerHealth.from_dict(HEALTH)
    assert len(session.calls) == 2
    assert delays == [0.1]


@pytest.mark.asyncio
async def test_health_reports_failure_after_bounded_retry_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import custom_components.cinema_collections.api_client as api_client

    async def no_wait(_delay: float) -> None:
        return None

    monkeypatch.setattr(api_client.asyncio, "sleep", no_wait)
    session = FakeSession([FakeResponse(429, {}), FakeResponse(429, {}), FakeResponse(429, {})])
    client = WorkerApiClient("http://worker.local", "token", session)

    with pytest.raises(WorkerApiError, match="HTTP status 429"):
        await client.async_health()

    assert len(session.calls) == 3


@pytest.mark.asyncio
async def test_health_does_not_retry_non_retryable_client_error() -> None:
    session = FakeSession([FakeResponse(400, {}), FakeResponse(200, HEALTH)])
    client = WorkerApiClient("http://worker.local", "token", session)  # type: ignore[arg-type]

    with pytest.raises(WorkerApiError, match="HTTP status 400"):
        await client.async_health()

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_entry_lifecycle_recreates_per_entry_runtime_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reloading an entry replaces only its in-memory Worker client."""
    import custom_components.cinema_collections as integration

    session = FakeSession(FakeResponse(200, HEALTH))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)

    class Entry:
        data = {CONF_ENDPOINT: "http://worker.local", CONF_TOKEN: "pairing-token"}
        entry_id = "test-entry"

    class Hass:
        data: dict[str, object] = {}

    hass = Hass()
    entry = Entry()
    assert await integration.async_setup_entry(hass, entry)
    first_client = hass.data[DOMAIN][entry.entry_id].client
    assert await integration.async_unload_entry(hass, entry)
    assert DOMAIN not in hass.data
    assert await integration.async_setup_entry(hass, entry)
    assert hass.data[DOMAIN][entry.entry_id].client is not first_client
