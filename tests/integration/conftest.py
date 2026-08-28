"""Fixtures shared by Home Assistant integration tests."""

from __future__ import annotations

import pytest


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
