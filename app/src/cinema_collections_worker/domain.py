"""Persistence-facing domain objects for the Worker API."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .paths import validate_collection_id, validate_relative_path
from .profile_validation import ProcessingProfile


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlaybackMode(StrEnum):
    """How the integration picks the next clip from a collection."""

    RANDOM = "random"
    SEQUENTIAL = "sequential"
    CUSTOM = "custom"


class CollectionCreate(_Strict):
    id: str
    name: str = Field(min_length=1)
    source_directory: str = Field(min_length=1)
    processing_profile_id: str = Field(min_length=1)
    enabled: bool = True
    is_default: bool = False
    worker_secret: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    schedule: dict[str, Any] = Field(default_factory=dict)
    playback_mode: PlaybackMode = PlaybackMode.RANDOM
    ordered_clip_ids: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return validate_collection_id(value)

    @field_validator("source_directory")
    @classmethod
    def valid_source_directory(cls, value: str) -> str:
        return validate_relative_path(value)

    @model_validator(mode="after")
    def custom_playback_has_an_order(self) -> CollectionCreate:
        if self.playback_mode is PlaybackMode.CUSTOM and not self.ordered_clip_ids:
            raise ValueError("custom playback requires ordered clip IDs")
        return self


class CollectionPatch(_Strict):
    name: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    priority: int | None = None
    source_directory: str | None = Field(default=None, min_length=1)
    processing_profile_id: str | None = Field(default=None, min_length=1)
    is_default: bool | None = None
    allow_manual_override: bool | None = None
    tags: list[str] | None = None
    notes: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    schedule: dict[str, Any] | None = None
    playback_mode: PlaybackMode | None = None
    ordered_clip_ids: list[str] | None = None

    @field_validator("source_directory")
    @classmethod
    def valid_source_directory(cls, value: str | None) -> str | None:
        return None if value is None else validate_relative_path(value)

    @model_validator(mode="after")
    def custom_playback_has_an_order(self) -> CollectionPatch:
        # A patch that turns custom playback on must carry the order with it:
        # the Worker never has to guess an order it was not given.
        if self.playback_mode is PlaybackMode.CUSTOM and not self.ordered_clip_ids:
            raise ValueError("custom playback requires ordered clip IDs")
        return self


class CollectionRecord(_Strict):
    id: str
    name: str
    enabled: bool
    priority: int
    source_directory: str
    compiled_output_prefix: str
    processing_profile_id: str
    is_default: bool
    allow_manual_override: bool
    tags: list[str]
    notes: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    schedule: dict[str, Any] = Field(default_factory=dict)
    playback_mode: PlaybackMode = PlaybackMode.RANDOM
    ordered_clip_ids: list[str] = Field(default_factory=list)
    revision: int = Field(ge=1)


class ProfileCreate(_Strict):
    id: str
    name: str = Field(min_length=1)
    settings: dict[str, Any]
    asset_secret: str | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return validate_collection_id(value)

    @field_validator("settings", mode="before")
    @classmethod
    def valid_settings(cls, value: object) -> dict[str, Any]:
        return ProcessingProfile.model_validate(value).model_dump(mode="json")


class ProfilePatch(_Strict):
    name: str | None = Field(default=None, min_length=1)
    settings: dict[str, Any] | None = None

    @field_validator("settings", mode="before")
    @classmethod
    def valid_settings(cls, value: object) -> dict[str, Any] | None:
        if value is None:
            return None
        return ProcessingProfile.model_validate(value).model_dump(mode="json")


class ProfileRecord(_Strict):
    id: str
    name: str
    version: int = Field(ge=1)
    settings: dict[str, Any]
    revision: int = Field(ge=1)
