"""Strict Worker application settings and safe container defaults."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .paths import RootKey

_KNOWN_WEAK_SECRETS = {
    "change-me-before-starting",
    "changeme",
    "password",
    "secret",
    "test-token",
}


def _validated_bearer_secret(value: SecretStr) -> SecretStr:
    secret = value.get_secret_value()
    if len(secret) < 43 or secret.lower() in _KNOWN_WEAK_SECRETS or len(set(secret)) < 16:
        raise ValueError("bearer_secret must be a random 256-bit token")
    return value


def _reject_overlapping_roots(roots: dict[RootKey, Path]) -> None:
    canonical = {key: path.resolve(strict=False) for key, path in roots.items()}
    for key, path in canonical.items():
        if not path.is_absolute() or path == Path("/"):
            raise ValueError(f"{key.value}_root must be an absolute non-root path")
    items = tuple(canonical.items())
    for index, (left_key, left) in enumerate(items):
        for right_key, right in items[index + 1 :]:
            if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError(
                    f"{left_key.value}_root and {right_key.value}_root must not overlap"
                )


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
        return _validated_bearer_secret(value)

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
        source = self.source_root.resolve(strict=False)
        compiled = self.compiled_root.resolve(strict=False)
        if source == compiled or source.is_relative_to(compiled) or compiled.is_relative_to(source):
            raise ValueError("source_root and compiled_root must not overlap")
        data_dir = self._canonical_root(self.data_dir, "data_dir")
        for path, name in (
            (self.temp_root or data_dir / "tmp", "temp_root"),
            (self.assets_root or data_dir / "assets", "assets_root"),
        ):
            self._require_descendant(path, data_dir, name)
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

    @field_validator("bearer_secret")
    @classmethod
    def _valid_runtime_secret(cls, value: SecretStr) -> SecretStr:
        return _validated_bearer_secret(value)

    @model_validator(mode="after")
    def _safe_runtime_paths(self) -> WorkerSettings:
        if set(self.roots) != set(RootKey):
            raise ValueError("all Worker roots must be configured")
        _reject_overlapping_roots(self.roots)
        data_dir = self.data_dir.resolve(strict=False)
        if not data_dir.is_absolute() or data_dir == Path("/"):
            raise ValueError("data_dir must be an absolute non-root path")
        for path, name in (
            (self.database_path, "database_path"),
            (self.log_dir, "log_dir"),
            (self.temp_dir, "temp_dir"),
        ):
            candidate = path.resolve(strict=False)
            if not candidate.is_relative_to(data_dir):
                raise ValueError(f"{name} must stay below data_dir")
        if self.temp_dir.resolve(strict=False) != self.roots[RootKey.TEMP].resolve(strict=False):
            raise ValueError("temp_dir must match the tracked temporary root")
        return self

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
