"""Constants for the Cinema Collections integration."""

from __future__ import annotations

DOMAIN = "cinema_collections"

CONF_ENDPOINT = "endpoint"
CONF_TOKEN = "token"
CONF_MEDIA_URI_PREFIX = "media_uri_prefix"
CONF_OVERRIDE_MODE = "override_mode"
CONF_OVERRIDE_COLLECTION_ID = "override_collection_id"
CONF_HISTORY_RESET_TIME = "history_reset_time"
CONF_SYNC_ON_STARTUP = "sync_on_startup"
CONF_SCHEDULE_RUN_TOKENS = "schedule_run_tokens"

DEFAULT_MEDIA_URI_PREFIX = "media-source://media_source/local/cinema-collections/compiled"
DEFAULT_HISTORY_RESET_TIME = "00:00"
DEFAULT_SYNC_ON_STARTUP = True
MAX_SCHEDULE_RUN_TOKENS = 512

SUBENTRY_COLLECTION = "collection"
SUBENTRY_PROFILE = "profile"

API_PREFIX = "/api/v1"
CLIENT_VERSION = "1.0.0"
DEFAULT_REQUEST_TIMEOUT = 10.0
MAX_REQUEST_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.1
EXPECTED_WORKER_COMPONENT = "cinema-collections-worker"

PLATFORMS: tuple[str, ...] = ("sensor", "button", "select")
