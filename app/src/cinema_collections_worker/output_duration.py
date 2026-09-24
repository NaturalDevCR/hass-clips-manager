"""Recover durations of existing compiled files without re-encoding them."""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from .database import Database
from .paths import SafePathResolver
from .probe import ProbeClient


class OutputDurations:
    """Cache successful probes in the catalog and briefly throttle failed probes."""

    def __init__(self, database: Database, resolver: SafePathResolver) -> None:
        self.database = database
        self.resolver = resolver
        self._lock = threading.Lock()
        self._retry_after: dict[str, float] = {}

    def ensure_many(self, rows: list[Any]) -> list[Any]:
        """Spend at most two seconds probing per catalog request."""
        deadline = time.monotonic() + 2
        return [self.ensure(row, deadline=deadline) for row in rows]

    def ensure(self, row: Any, *, deadline: float | None = None) -> Any:
        if not row["output_available"] or not row["relative_output_path"]:
            return row
        if row["output_duration_seconds"] is not None:
            return row
        if deadline is None:
            deadline = time.monotonic() + 3
        if time.monotonic() >= deadline or not self._lock.acquire(blocking=False):
            return row
        try:
            identifier = str(row["id"])
            current = self.database.connection.execute(
                "SELECT * FROM clips WHERE id=?", (identifier,)
            ).fetchone()
            if current is None or current["output_duration_seconds"] is not None:
                return current if current is not None else row
            if not current["output_available"] or not current["relative_output_path"]:
                return current
            if self._retry_after.get(identifier, 0) > time.monotonic():
                return current
            self._retry_after[identifier] = time.monotonic() + 60
            try:
                path = self.resolver.resolve("compiled", str(current["relative_output_path"]))
                if path.is_symlink() or not path.is_file():
                    return current
                before = path.stat()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return current
                probe = ProbeClient(timeout_seconds=remaining).probe(path)
                after = path.stat()
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    return current
                if not probe.valid or not math.isfinite(probe.duration_seconds):
                    return current
                if probe.duration_seconds <= 0:
                    return current
            except (OSError, ValueError):
                return current
            with self.database.transaction():
                self.database.connection.execute(
                    "UPDATE clips SET output_duration_seconds=? WHERE id=? "
                    "AND output_duration_seconds IS NULL AND output_available=1 "
                    "AND relative_output_path=? AND updated_at=?",
                    (
                        probe.duration_seconds,
                        identifier,
                        current["relative_output_path"],
                        current["updated_at"],
                    ),
                )
            self._retry_after.pop(identifier, None)
            return self.database.connection.execute(
                "SELECT * FROM clips WHERE id=?", (identifier,)
            ).fetchone()
        finally:
            self._lock.release()
