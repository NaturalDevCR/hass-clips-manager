"""Constants for the Cinema Collections integration."""

from __future__ import annotations

DOMAIN = "cinema_collections"

CONF_ENDPOINT = "endpoint"
CONF_TOKEN = "token"

API_PREFIX = "/api/v1"
CLIENT_VERSION = "1.0.0"
DEFAULT_REQUEST_TIMEOUT = 10.0
MAX_REQUEST_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.1
EXPECTED_WORKER_COMPONENT = "cinema-collections-worker"

PLATFORMS: tuple[str, ...] = ()
