"""Constants for the Cinema Collections integration."""

from __future__ import annotations

from enum import StrEnum
from typing import cast

DOMAIN = "cinema_collections"

CONF_ENDPOINT = "endpoint"
CONF_TOKEN = "token"
CONF_MEDIA_URI_PREFIX = "media_uri_prefix"
CONF_OVERRIDE_MODE = "override_mode"
CONF_OVERRIDE_COLLECTION_ID = "override_collection_id"
CONF_HISTORY_RESET_TIME = "history_reset_time"
CONF_HISTORY_RESET_MODE = "history_reset_mode"
# Set once this entry's collections and profiles live in the Worker.
CONF_MIGRATED_TO_WORKER = "migrated_to_worker"
# The first Worker that owns collections and processing profiles. An older
# Worker rejects the policy fields outright rather than dropping them.
MINIMUM_WORKER_VERSION = "1.8.0"
CONF_SCHEDULE_RUN_TOKENS = "schedule_run_tokens"


class HistoryResetMode(StrEnum):
    """When a collection's no-repeat playback history rolls over."""

    ON_EXHAUSTION = "on_exhaustion"
    DAILY = "daily"


class PlaybackMode(StrEnum):
    """Per-collection clip selection order."""

    RANDOM = "random"
    SEQUENTIAL = "sequential"
    CUSTOM = "custom"


def normalize_clip_order(value: object) -> tuple[str, ...]:
    """Validate ordered stable IDs at configuration and service boundaries."""
    if not isinstance(value, (list, tuple)):
        raise ValueError("ordered clip IDs must be a list of strings")
    values = cast(list[object] | tuple[object, ...], value)
    if any(not isinstance(item, str) for item in values):
        raise ValueError("ordered clip IDs must be strings")
    ordered = tuple(cast(str, item).strip() for item in values)
    if any(not item for item in ordered) or len(ordered) != len(set(ordered)):
        raise ValueError("ordered clip IDs must be non-empty and unique")
    return ordered


DEFAULT_MEDIA_URI_PREFIX = "media-source://media_source/local/cinema-collections/compiled"
DEFAULT_HISTORY_RESET_TIME = "00:00"
DEFAULT_HISTORY_RESET_MODE = HistoryResetMode.ON_EXHAUSTION.value
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
