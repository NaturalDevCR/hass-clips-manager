"""SQLite-backed, single-consumer queue primitives for compilation jobs."""
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .database import Database

if TYPE_CHECKING:
    from .jobs import JobRecord


def utc_now() -> str:
    """Return an ISO-8601 timestamp suitable for persisted worker records."""

    return datetime.now(UTC).isoformat()


class PersistentJobQueue:
    """Persist jobs and atomically claim at most one running job."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _record(row: sqlite3.Row) -> JobRecord:
        # Delayed import avoids a queue/jobs import cycle while keeping the
        # durable conversion in one place.
        from .jobs import JobRecord

        payload = json.loads(row["payload"] or "{}")
        payload.update(
            {
                "id": row["id"],
                "kind": row["kind"],
                "state": row["state"],
                "progress": json.loads(row["progress"]),
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error": row["error"],
                "collection_id": row["collection_id"],
                "clip_id": row["clip_id"],
                "fingerprint": row["fingerprint"],
                "attempt": row["attempt"],
                "max_attempts": row["max_attempts"],
                "cancel_requested": bool(row["cancel_requested"]),
            }
        )
        return JobRecord.model_validate(payload)

    def get(self, job_id: str) -> JobRecord:
        row = self.db.connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._record(row)

    def enqueue(self, job: JobRecord) -> JobRecord:
        """Return an existing active duplicate, otherwise persist ``job``."""

        with self.db.connection:
            duplicate = self.db.connection.execute(
                "SELECT * FROM jobs WHERE fingerprint=? AND state IN ('queued','running') "
                "ORDER BY created_at LIMIT 1",
                (job.fingerprint,),
            ).fetchone()
            if duplicate is not None:
                return self._record(duplicate)
            payload = job.model_dump(
                mode="json",
                exclude={
                    "id",
                    "kind",
                    "state",
                    "progress",
                    "created_at",
                    "started_at",
                    "finished_at",
                    "error",
                    "collection_id",
                    "clip_id",
                    "fingerprint",
                    "attempt",
                    "max_attempts",
                    "cancel_requested",
                },
            )
            self.db.connection.execute(
                "INSERT INTO jobs(id,kind,state,progress,created_at,finished_at,error,collection_id,clip_id,"
                "fingerprint,payload,attempt,max_attempts,cancel_requested,started_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job.id,
                    job.kind,
                    job.state,
                    json.dumps(job.progress.model_dump(mode="json"), separators=(",", ":")),
                    job.created_at.isoformat(),
                    job.finished_at.isoformat() if job.finished_at else None,
                    job.error,
                    job.collection_id,
                    job.clip_id,
                    job.fingerprint,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    job.attempt,
                    job.max_attempts,
                    int(job.cancel_requested),
                    job.started_at.isoformat() if job.started_at else None,
                ),
            )
        return job

    def has_succeeded(self, fingerprint: str) -> bool:
        return (
            self.db.connection.execute(
                "SELECT 1 FROM jobs WHERE fingerprint=? AND state='succeeded' LIMIT 1",
                (fingerprint,),
            ).fetchone()
            is not None
        )

    def claim_next(self) -> JobRecord | None:
        """Claim one queued job only when no job is already running."""

        with self.db.connection:
            candidate = self.db.connection.execute(
                "SELECT id FROM jobs WHERE state='queued' ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if candidate is None:
                return None
            started = utc_now()
            claimed = self.db.connection.execute(
                "UPDATE jobs SET state='running', started_at=?, attempt=attempt+1, "
                "progress=? WHERE id=? AND state='queued' "
                "AND NOT EXISTS (SELECT 1 FROM jobs WHERE state='running')",
                (
                    started,
                    json.dumps({"stage": "encoding", "percent": 0, "eta_seconds": None}),
                    candidate["id"],
                ),
            )
            if claimed.rowcount != 1:
                return None
            self.db.connection.execute(
                "INSERT INTO job_attempts(job_id,attempt,state,started_at,finished_at) VALUES(?,?,?,?,NULL)",
                (
                    candidate["id"],
                    self.db.connection.execute(
                        "SELECT attempt FROM jobs WHERE id=?", (candidate["id"],)
                    ).fetchone()["attempt"],
                    "running",
                    started,
                ),
            )
            row = self.db.connection.execute(
                "SELECT * FROM jobs WHERE id=?", (candidate["id"],)
            ).fetchone()
        return self._record(row)

    def update(self, job: JobRecord) -> JobRecord:
        """Persist mutable execution state without changing immutable payload."""

        with self.db.connection:
            payload_row = self.db.connection.execute(
                "SELECT payload FROM jobs WHERE id=?", (job.id,)
            ).fetchone()
            if payload_row is None:
                raise KeyError(job.id)
            payload = json.loads(payload_row["payload"] or "{}")
            payload["logs"] = job.logs[-100:]
            self.db.connection.execute(
                "UPDATE jobs SET state=?, progress=?, finished_at=?, error=?, attempt=?, "
                "cancel_requested=?, started_at=?, payload=? WHERE id=?",
                (
                    job.state,
                    json.dumps(job.progress.model_dump(mode="json"), separators=(",", ":")),
                    job.finished_at.isoformat() if job.finished_at else None,
                    job.error,
                    job.attempt,
                    int(job.cancel_requested),
                    job.started_at.isoformat() if job.started_at else None,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    job.id,
                ),
            )
            if job.state in {"queued", "succeeded", "failed", "cancelled"}:
                self.db.connection.execute(
                    "UPDATE job_attempts SET state=?, finished_at=? WHERE job_id=? AND finished_at IS NULL",
                    (job.state, utc_now(), job.id),
                )
        return self.get(job.id)

    def request_cancel(self, job_id: str) -> JobRecord:
        with self.db.connection:
            row = self.db.connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] in {"succeeded", "failed", "cancelled"}:
                return self._record(row)
            if row["state"] == "queued":
                self.db.connection.execute(
                    "UPDATE jobs SET state='cancelled', cancel_requested=1, finished_at=?, "
                    "progress=? WHERE id=?",
                    (
                        utc_now(),
                        json.dumps({"stage": "cancelled", "percent": 0, "eta_seconds": None}),
                        job_id,
                    ),
                )
            else:
                self.db.connection.execute(
                    "UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,)
                )
        return self.get(job_id)
