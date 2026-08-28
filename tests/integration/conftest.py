"""Fixtures shared by Home Assistant integration tests."""

from __future__ import annotations

import pytest
import pytest_asyncio
from pytest_homeassistant_custom_component.plugins import hass as plugin_hass

# The Home Assistant plugin exposes this fixture with pytest.fixture. Re-register
# it for pytest-asyncio strict mode so worker tests retain their existing mode.
hass = pytest_asyncio.fixture(plugin_hass.__wrapped__)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Make the repository's custom integration discoverable to Home Assistant."""


@pytest.fixture
def worker_health_payload() -> dict[str, str]:
    """A compatible, authenticated Worker health response."""

    return {
        "status": "ok",
        "component": "cinema-collections-worker",
        "worker_version": "1.0.0",
        "api_version": "1.0.0",
        "min_client_version": "1.0.0",
        "max_client_version": "1.x",
    }
