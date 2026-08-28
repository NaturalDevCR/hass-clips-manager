"""Strict Worker application settings and safe container defaults."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .paths import RootKey


class WorkerMode(StrEnum):
    APP = "app"
    EXTERNAL = "external"


class HardwareAcceleration(StrEnum):
    NONE = "none"


class AppOptions(BaseModel):
    """Input options accepted from an App options file."""

    model_config = ConfigDict(extra="forbid")

    mode: WorkerMode = WorkerMode.APP
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8099, ge=1, le=65535)
    data_dir: Path = Path("/data")
    bearer_secret: SecretStr
    disk_reserve_bytes: int = Field(default=1_073_741_824, ge=0)
    hardware_acceleration: HardwareAcceleration = HardwareAcceleration.NONE
    source_root: Path = Path("/media/source")
    compiled_root: Path = Path("/media/compiled")
    media_root: Path | None = None
    temp_root: Path | None = None
    assets_root: Path | None = None

    @field_validator("bind_host")
    @classmethod
    def _valid_bind_host(cls, value: str) -> str:
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("bind_host must be a non-empty safe host name")
        return value

    @field_validator("bearer_secret")
    @classmethod
    def _valid_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("bearer_secret must not be empty")
        return value

    @model_validator(mode="after")
    def _media_roots_are_contained(self) -> AppOptions:
        """Keep all user media below the one trusted container media mount."""

        if self.mode is WorkerMode.APP:
            media_root = Path("/media")
        else:
            if self.media_root is None:
                raise ValueError("external mode requires an explicit media_root")
            media_root = self.media_root
        canonical_media_root = self._canonical_root(media_root, "media_root")
        if canonical_media_root == Path("/"):
            raise ValueError("media_root must not be the filesystem root")
        self._require_descendant(self.source_root, canonical_media_root, "source_root")
        self._require_descendant(self.compiled_root, canonical_media_root, "compiled_root")
        return self

    @staticmethod
    def _canonical_root(path: Path, name: str) -> Path:
        if not path.is_absolute():
            raise ValueError(f"{name} must be absolute")
        return path.resolve(strict=False)

    @classmethod
    def _require_descendant(cls, path: Path, root: Path, name: str) -> None:
        candidate = cls._canonical_root(path, name)
        if candidate == root:
            raise ValueError(f"{name} must be a descendant of media_root")
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{name} must be contained by media_root") from exc


class WorkerSettings(BaseModel):
    """Validated, runtime-ready Worker settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: WorkerMode = WorkerMode.APP
    bind_host: str = "127.0.0.1"
    bind_port: int = 8099
    data_dir: Path = Path("/data")
    bearer_secret: SecretStr
    disk_reserve_bytes: int = 1_073_741_824
    hardware_acceleration: HardwareAcceleration = HardwareAcceleration.NONE
    roots: dict[RootKey, Path]
    database_path: Path
    log_dir: Path
    temp_dir: Path

    @classmethod
    def from_options(cls, options: AppOptions) -> WorkerSettings:
        data_dir = options.data_dir
        temp_dir = options.temp_root or data_dir / "tmp"
        assets = options.assets_root or data_dir / "assets"
        roots = {
            RootKey.SOURCE: options.source_root,
            RootKey.COMPILED: options.compiled_root,
            RootKey.TEMP: temp_dir,
            RootKey.ASSETS: assets,
        }
        return cls(
            mode=options.mode,
            bind_host=options.bind_host,
            bind_port=options.bind_port,
            data_dir=data_dir,
            bearer_secret=options.bearer_secret,
            disk_reserve_bytes=options.disk_reserve_bytes,
            hardware_acceleration=options.hardware_acceleration,
            roots=roots,
            database_path=data_dir / "worker.sqlite3",
            log_dir=data_dir / "logs",
            temp_dir=temp_dir,
        )


class Settings:
    """Loader facade retained as the public settings API."""

    @staticmethod
    def load(options_path: Path) -> WorkerSettings:
        path = Path(options_path)
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"unable to read Worker options: {path.name}") from exc
        try:
            raw: Any = (
                json.loads(raw_text) if path.suffix.lower() == ".json" else yaml.safe_load(raw_text)
            )
            options = AppOptions.model_validate(raw or {})
        except Exception as exc:
            raise ValueError("invalid Worker options") from exc
        return WorkerSettings.from_options(options)
