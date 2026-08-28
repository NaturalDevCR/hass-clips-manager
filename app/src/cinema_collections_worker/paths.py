"""Filesystem boundary checks for Worker-managed paths."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path


class PathSafetyError(ValueError):
    """Raised when a path or identifier crosses a Worker safety boundary."""


class RootKey(StrEnum):
    SOURCE = "source"
    COMPILED = "compiled"
    TEMP = "temp"
    ASSETS = "assets"


_COLLECTION_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _has_unsafe_characters(value: str) -> bool:
    return any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)


def validate_collection_id(value: object) -> str:
    """Validate and return an immutable, URL-safe collection identifier."""

    if not isinstance(value, str) or not value or not _COLLECTION_ID.fullmatch(value):
        raise PathSafetyError("collection ID must be a lowercase URL-safe slug")
    return value


def validate_filename(value: object) -> str:
    """Validate one filename, never a path or a traversal component."""

    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise PathSafetyError("filename must not be empty or a traversal component")
    if Path(value).name != value or "/" in value or "\\" in value:
        raise PathSafetyError("filename must be a single path component")
    if _has_unsafe_characters(value):
        raise PathSafetyError("filename contains unsafe control characters")
    return value


class SafePathResolver:
    """Resolve paths only within canonical, explicitly configured roots."""

    def __init__(self, roots: Mapping[str | RootKey, Path]) -> None:
        if not roots:
            raise ValueError("at least one approved root is required")
        normalized: dict[RootKey, Path] = {}
        for key, root in roots.items():
            try:
                root_key = key if isinstance(key, RootKey) else RootKey(key)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown path root: {key!r}") from exc
            root_path = Path(root)
            if not root_path.is_absolute():
                raise ValueError("approved roots must be absolute paths")
            normalized[root_key] = root_path.resolve(strict=False)
        self._roots = normalized

    @property
    def roots(self) -> Mapping[RootKey, Path]:
        return self._roots

    def resolve(self, root_key: str, relative_path: object) -> Path:
        """Canonicalize and validate a root-relative candidate path."""

        try:
            key = RootKey(root_key)
            root = self._roots[key]
        except (KeyError, ValueError, TypeError) as exc:
            raise PathSafetyError(f"unknown path root: {root_key!r}") from exc
        if not isinstance(relative_path, str) or not relative_path:
            raise PathSafetyError("relative path must not be empty")
        if _has_unsafe_characters(relative_path):
            raise PathSafetyError("path contains unsafe control characters")
        candidate_path = Path(relative_path)
        if candidate_path.is_absolute():
            raise PathSafetyError("absolute paths are not permitted")
        if ".." in candidate_path.parts:
            raise PathSafetyError("dot traversal is not permitted")
        candidate = (root / candidate_path).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PathSafetyError("path escapes its approved root") from exc
        return candidate
