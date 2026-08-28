"""Deterministic, local-time collection policy resolution."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from homeassistant.util import dt as dt_util

_COLLECTION_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class OverrideKind(StrEnum):
    """The supported user-facing collection override modes."""

    AUTOMATIC = "automatic"
    DEFAULT = "default"
    EXPLICIT = "explicit"


@dataclass(frozen=True, slots=True)
class OverrideMode:
    """An automatic, default, or stable-ID manual collection override."""

    kind: OverrideKind = OverrideKind.AUTOMATIC
    collection_id: str | None = None

    AUTOMATIC: ClassVar[OverrideMode]
    DEFAULT: ClassVar[OverrideMode]

    def __post_init__(self) -> None:
        kind = OverrideKind(self.kind)
        object.__setattr__(self, "kind", kind)
        if kind is OverrideKind.EXPLICIT:
            if not self.collection_id or not _COLLECTION_ID.fullmatch(self.collection_id):
                raise ValueError("explicit override requires a stable collection ID")
        elif self.collection_id is not None:
            raise ValueError("only an explicit override can include a collection ID")

    @classmethod
    def automatic(cls) -> OverrideMode:
        """Return the policy-driven selection mode."""
        return cls.AUTOMATIC

    @classmethod
    def default(cls) -> OverrideMode:
        """Return the user request for the configured default collection."""
        return cls.DEFAULT

    @classmethod
    def explicit(cls, collection_id: str) -> OverrideMode:
        """Return an explicit manual selection by immutable collection ID."""
        return cls(OverrideKind.EXPLICIT, collection_id)


OverrideMode.AUTOMATIC = OverrideMode()
OverrideMode.DEFAULT = OverrideMode(OverrideKind.DEFAULT)


class SelectionReason(StrEnum):
    """Why a collection was selected (or why none could be selected)."""

    MANUAL = "manual"
    MANUAL_DEFAULT = "manual_default"
    SCHEDULE = "schedule"
    DEFAULT = "default"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class CollectionPolicy:
    """The integration-owned policy attributes for one Worker collection."""

    id: str
    enabled: bool = True
    priority: int = 0
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_default: bool = False
    allow_manual_override: bool = True

    def __post_init__(self) -> None:
        if not _COLLECTION_ID.fullmatch(self.id):
            raise ValueError("collection ID must be a lowercase URL-safe slug")
        for value in (self.starts_at, self.ends_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("collection policy datetimes must be timezone-aware")
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.starts_at > self.ends_at
        ):
            raise ValueError("collection policy window end must not precede its start")

    @property
    def collection_id(self) -> str:
        """Compatibility-friendly explicit name for the immutable identifier."""
        return self.id

    def is_scheduled_at(self, now: datetime) -> bool:
        """Return whether the local policy window contains ``now`` half-openly."""
        if not self.enabled:
            return False
        return (self.starts_at is None or now >= self.starts_at) and (
            self.ends_at is None or now < self.ends_at
        )


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """Collection-policy decision exposed to downstream entities and services."""

    collection: CollectionPolicy | None
    reason: SelectionReason
    override_rejected: bool = False

    @property
    def id(self) -> str | None:
        """Return the selected immutable ID, when one exists."""
        return self.collection.id if self.collection is not None else None


def _local_now(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("resolver requires a timezone-aware timestamp")
    return dt_util.as_local(now)


def _default_collection(collections: Sequence[CollectionPolicy]) -> CollectionPolicy | None:
    defaults = [
        collection for collection in collections if collection.enabled and collection.is_default
    ]
    return min(defaults, key=lambda collection: collection.id, default=None)


def resolve_active_collection(
    collections: Sequence[CollectionPolicy], override: OverrideMode, now: datetime
) -> SelectionResult:
    """Resolve the active collection with stable ordering and explicit reasons.

    Explicit and default overrides deliberately do not require the collection's
    schedule window to be active; a manual user choice is allowed outside a
    scheduled window. Disabled collections never participate in resolution.
    """
    local_now = _local_now(now)
    if override.kind is OverrideKind.EXPLICIT:
        selected = next((item for item in collections if item.id == override.collection_id), None)
        if selected is not None and selected.enabled and selected.allow_manual_override:
            return SelectionResult(selected, SelectionReason.MANUAL)
        fallback = _default_collection(collections)
        if fallback is not None:
            return SelectionResult(fallback, SelectionReason.DEFAULT, override_rejected=True)
        return SelectionResult(None, SelectionReason.NONE, override_rejected=True)

    default = _default_collection(collections)
    if override.kind is OverrideKind.DEFAULT:
        if default is not None:
            return SelectionResult(default, SelectionReason.MANUAL_DEFAULT)
        return SelectionResult(None, SelectionReason.NONE)

    scheduled = [item for item in collections if item.is_scheduled_at(local_now)]
    if scheduled:
        return SelectionResult(
            min(scheduled, key=lambda item: (-item.priority, item.id)), SelectionReason.SCHEDULE
        )
    if default is not None:
        return SelectionResult(default, SelectionReason.DEFAULT)
    return SelectionResult(None, SelectionReason.NONE)
