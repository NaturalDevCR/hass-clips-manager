"""Config-flow and config-entry lifecycle coverage."""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.cinema_collections.const import CONF_ENDPOINT, CONF_TOKEN, DOMAIN


@pytest.mark.asyncio
async def test_user_flow_pairs_compatible_worker(
    hass, aioclient_mock, worker_health_payload
) -> None:
    aioclient_mock.get("http://worker.local/api/v1/health", json=worker_health_payload)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_ENDPOINT: "http://worker.local", CONF_TOKEN: "pairing-token"},
    )

    assert result["type"] == "create_entry"
    assert result["title"] == "Cinema Collections Worker"
    assert result["data"] == {
        CONF_ENDPOINT: "http://worker.local",
        CONF_TOKEN: "pairing-token",
    }


@pytest.mark.asyncio
async def test_user_flow_aborts_duplicate_worker(
    hass, aioclient_mock, worker_health_payload
) -> None:
    aioclient_mock.get("http://worker.local/api/v1/health", json=worker_health_payload)
    paired = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_ENDPOINT: "http://worker.local", CONF_TOKEN: "existing-token"},
    )
    assert paired["type"] == "create_entry"
    assert hass.config_entries.async_entries(DOMAIN)[0].unique_id == "http://worker.local"

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_ENDPOINT: "http://worker.local", CONF_TOKEN: "new-token"},
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_user_flow_reports_invalid_token(hass, aioclient_mock) -> None:
    aioclient_mock.get("http://worker.local/api/v1/health", status=401)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_ENDPOINT: "http://worker.local", CONF_TOKEN: "bad-token"},
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_user_flow_reports_incompatible_worker(
    hass, aioclient_mock, worker_health_payload
) -> None:
    worker_health_payload["min_client_version"] = "2.0.0"
    worker_health_payload["max_client_version"] = "2.x"
    aioclient_mock.get("http://worker.local/api/v1/health", json=worker_health_payload)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_ENDPOINT: "http://worker.local", CONF_TOKEN: "pairing-token"},
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "incompatible_worker"}


@pytest.mark.asyncio
async def test_user_flow_rejects_wrong_worker_component(
    hass, aioclient_mock, worker_health_payload
) -> None:
    worker_health_payload["component"] = "another-worker"
    aioclient_mock.get("http://worker.local/api/v1/health", json=worker_health_payload)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_ENDPOINT: "http://worker.local", CONF_TOKEN: "pairing-token"},
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "incompatible_worker"}


@pytest.mark.asyncio
async def test_user_flow_reports_timeout(hass, aioclient_mock) -> None:
    aioclient_mock.get("http://worker.local/api/v1/health", exc=TimeoutError())

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_ENDPOINT: "http://worker.local", CONF_TOKEN: "pairing-token"},
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}
