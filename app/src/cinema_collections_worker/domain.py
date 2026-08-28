"""Persistence-facing domain objects for the Worker API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .paths import validate_collection_id, validate_relative_path
from .profile_validation import ProcessingProfile


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectionCreate(_Strict):
    id: str
    name: str = Field(min_length=1)
    source_directory: str = Field(min_length=1)
    processing_profile_id: str = Field(min_length=1)
    enabled: bool = True
    is_default: bool = False
    worker_secret: str | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return validate_collection_id(value)

    @field_validator("source_directory")
    @classmethod
    def valid_source_directory(cls, value: str) -> str:
        return validate_relative_path(value)


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

    @field_validator("source_directory")
    @classmethod
    def valid_source_directory(cls, value: str | None) -> str | None:
        return None if value is None else validate_relative_path(value)


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
