"""Safe support diagnostics for the Cinema Collections integration."""

from __future__ import annotations

import re
from collections.abc import Mapping
from itertools import islice
from typing import cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import CoordinatorSnapshot

_OMIT = object()
_MAX_DEPTH = 6
_MAX_ITEMS = 20
_MAX_EXCERPTS = 3
_MAX_TEXT_LENGTH = 500
_SENSITIVE_KEY_PARTS = frozenset(
    {"authorization", "auth", "apikey", "credential", "password", "secret", "token"}
)
_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_\]])(?:/[^\s'\"<>]+|[A-Za-z]:[\\/][^\s'\"<>]+)")
_CREDENTIAL_PATTERN = re.compile(
    r"""(?ix)
    ["']?(?:authorization|auth[_-]?header|access[_-]?token|api[_-]?key)["']?\s*[:=]\s*["']?
    (?:bearer\s+)?[^\s,;'"\]\}\r\n]+(?:["'])?
    """
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, object]:
    """Return a bounded, redacted view of one integration entry's state."""
    snapshot = _snapshot_for_entry(hass, entry)
    roots = _root_paths(snapshot)
    secrets = _entry_secrets(entry)
    return {
        "entry": {
            "entry_id": entry.entry_id,
            "title": _sanitize_value(entry.title, roots, secrets),
            "data": _sanitize_value(dict(entry.data), roots, secrets),
            "options": _sanitize_value(dict(entry.options), roots, secrets),
            "subentries": _subentries(entry, roots, secrets),
        },
        "worker": _worker_snapshot(snapshot, roots, secrets),
    }


def _snapshot_for_entry(hass: HomeAssistant, entry: ConfigEntry) -> CoordinatorSnapshot | None:
    """Read an already-loaded coordinator without making a Worker request."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    coordinator = getattr(runtime, "coordinator", None)
    snapshot = getattr(coordinator, "data", None)
    return snapshot if isinstance(snapshot, CoordinatorSnapshot) else None


def _entry_secrets(entry: ConfigEntry) -> tuple[str, ...]:
    """Collect configured secret values so error text cannot echo them back."""
    values: list[str] = []
    for key, value in entry.data.items():
        if _is_sensitive_key(key) and isinstance(value, str) and value:
            values.append(value)
    for key, value in entry.options.items():
        if _is_sensitive_key(key) and isinstance(value, str) and value:
            values.append(value)
    return tuple(values)


def _root_paths(snapshot: CoordinatorSnapshot | None) -> dict[str, str]:
    """Identify Worker-provided roots for useful relative-path labels."""
    if snapshot is None or snapshot.status is None:
        return {}
    roots: dict[str, str] = {}
    for key, value in snapshot.status.storage.items():
        if isinstance(value, str) and _is_absolute_path(value):
            roots[str(key)] = value.rstrip("/") or "/"
    return roots


def _subentries(
    entry: ConfigEntry, roots: Mapping[str, str], secrets: tuple[str, ...]
) -> list[dict[str, object]]:
    """Keep local policy metadata while avoiding unbounded config internals."""
    subentries: object = getattr(entry, "subentries", {})
    values: tuple[object, ...] = (
        tuple(cast(Mapping[object, object], subentries).values())
        if isinstance(subentries, Mapping)
        else ()
    )
    result: list[dict[str, object]] = []
    for subentry in values[:_MAX_ITEMS]:
        data: object = getattr(subentry, "data", {})
        result.append(
            {
                "id": _sanitize_value(getattr(subentry, "subentry_id", None), roots, secrets),
                "type": _sanitize_value(getattr(subentry, "subentry_type", None), roots, secrets),
                "title": _sanitize_value(getattr(subentry, "title", None), roots, secrets),
                "data": _sanitize_value(data, roots, secrets),
            }
        )
    return result


def _worker_snapshot(
    snapshot: CoordinatorSnapshot | None,
    roots: Mapping[str, str],
    secrets: tuple[str, ...],
) -> dict[str, object]:
    """Expose support-relevant health, selection, and bounded job excerpts."""
    if snapshot is None:
        return {"loaded": False}

    worker: dict[str, object] = {
        "loaded": True,
        "available": snapshot.available,
        "active_collection_id": _sanitize_value(snapshot.active_collection_id, roots, secrets),
        "selection_reason": _sanitize_value(snapshot.reason, roots, secrets),
        "override_rejected": snapshot.override_rejected,
        "queue_depth": snapshot.queue_depth,
        "compatibility": _sanitize_value(snapshot.compatibility, roots, secrets),
        "current_job": _sanitize_value(
            snapshot.status.current_job if snapshot.status is not None else None,
            roots,
            secrets,
        ),
        "storage": _sanitize_value(
            snapshot.status.storage if snapshot.status is not None else None,
            roots,
            secrets,
        ),
        "latest_error": _sanitize_value(snapshot.latest_error, roots, secrets),
    }
    if snapshot.status is not None:
        worker["latest_errors"] = [
            {
                "code": _sanitize_value(error.code, roots, secrets),
                "message": _sanitize_value(error.message, roots, secrets),
                "retryable": error.retryable,
                "request_id": _sanitize_value(error.request_id, roots, secrets),
            }
            for error in snapshot.status.latest_errors[:_MAX_EXCERPTS]
        ]
    return worker


def _sanitize_value(
    value: object,
    roots: Mapping[str, str],
    secrets: tuple[str, ...],
    depth: int = 0,
) -> object:
    """Recursively remove credentials and turn absolute paths into safe labels."""
    if depth >= _MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, str):
        return _sanitize_text(value, roots, secrets)
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        mapping = cast(Mapping[object, object], value)
        for key, item in islice(mapping.items(), _MAX_ITEMS):
            string_key = str(key)
            if _is_sensitive_key(string_key):
                continue
            item_value = _sanitize_value(item, roots, secrets, depth + 1)
            if item_value is not _OMIT:
                sanitized[string_key] = item_value
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        sequence = cast(list[object] | tuple[object, ...] | set[object] | frozenset[object], value)
        sanitized_items: list[object] = []
        for item in islice(sequence, _MAX_ITEMS):
            sanitized_item = _sanitize_value(item, roots, secrets, depth + 1)
            if sanitized_item is not _OMIT:
                sanitized_items.append(sanitized_item)
        return sanitized_items
    if value is None or isinstance(value, bool | int | float):
        return value
    return _sanitize_text(str(value), roots, secrets)


def _sanitize_text(value: str, roots: Mapping[str, str], secrets: tuple[str, ...]) -> str:
    """Redact known credentials and all absolute paths inside arbitrary messages."""
    redacted = value
    for secret in secrets:
        redacted = redacted.replace(secret, "[redacted]")
    redacted = _CREDENTIAL_PATTERN.sub("[redacted credential]", redacted)
    for label, root in sorted(roots.items(), key=lambda item: len(item[1]), reverse=True):
        pattern = re.compile(rf"{re.escape(root)}(?=$|[/\\])")
        redacted = pattern.sub(f"[{label}]", redacted)
    return _PATH_PATTERN.sub("[redacted-path]", redacted)[:_MAX_TEXT_LENGTH]


def _is_absolute_path(value: str) -> bool:
    """Recognize POSIX and drive-prefixed absolute paths without filesystem access."""
    return value.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", value))


def _is_sensitive_key(key: object) -> bool:
    """Recognize common credential-key variants without retaining their values."""
    normalized = "".join(character for character in str(key).lower() if character.isalnum())
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)
