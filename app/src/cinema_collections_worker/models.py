"""Typed domain models shared by Worker services."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# These imports are intentionally kept as public compatibility exports.
from .domain import (  # noqa: E402, F401
    CollectionCreate,
    CollectionPatch,
    CollectionRecord,
    ProfileCreate,
    ProfilePatch,
    ProfileRecord,
)
from .paths import validate_collection_id

__all__ = [
    "ClipRecord", "ClipState", "CollectionCreate", "CollectionPatch", "CollectionRecord",
    "CollectionRef", "ProfileCreate", "ProfilePatch", "ProfileRecord",
]



class ClipState(StrEnum):
    DISCOVERED = "discovered"
    INVALID = "invalid"
    PENDING = "pending"
    COMPILING = "compiling"
    READY = "ready"
    FAILED = "failed"
    STALE = "stale"


class CollectionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return validate_collection_id(value)


class ClipRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    collection_id: str
    state: ClipState
    relative_source_path: str
    relative_output_path: str | None = None
    duration_seconds: float = Field(ge=0)
    output_available: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None

    @field_validator("collection_id")
    @classmethod
    def validate_collection(cls, value: str) -> str:
        return validate_collection_id(value)
