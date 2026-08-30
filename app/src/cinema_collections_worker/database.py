"""SQLite connection and schema migrations owned by the Worker."""
# SQL migration statements are intentionally kept readable.
# ruff: noqa: E501

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, url: str) -> None:
        self._local = threading.local()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        self._uri = url == ":memory:"
        self._dsn = (
            f"file:cinema-collections-{uuid.uuid4().hex}?mode=memory&cache=shared"
            if self._uri
            else url
        )
        # A shared in-memory database survives as long as this keeper remains
        # open. The creating thread uses it as its own thread-local connection.
        if self._uri:
            keeper = self._new_connection()
            self._local.connection = keeper

    @classmethod
    def create(cls, url: str) -> Database:
        if url != ":memory:":
            Path(url).parent.mkdir(parents=True, exist_ok=True)
        db = cls(url)
        db._migrate()
        return db

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._dsn,
            uri=self._uri,
            timeout=30,
            # Connections are never shared for queries, but allowing the owner
            # to close all thread-local handles enables graceful app shutdown.
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        if not self._uri:
            connection.execute("PRAGMA journal_mode = WAL")
        with self._connections_lock:
            self._connections.add(connection)
        return connection

    @property
    def connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._new_connection()
            self._local.connection = connection
        return connection

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """Serialize a transaction per thread and preserve nested atomicity."""

        connection = self.connection
        depth = int(getattr(self._local, "transaction_depth", 0))
        if depth == 0:
            connection.execute("BEGIN IMMEDIATE")
            self._local.rollback_only = False
        self._local.transaction_depth = depth + 1
        try:
            yield connection
        except Exception:
            self._local.rollback_only = True
            raise
        finally:
            self._local.transaction_depth = depth
            if depth == 0:
                try:
                    if bool(getattr(self._local, "rollback_only", False)):
                        connection.rollback()
                    else:
                        connection.commit()
                finally:
                    self._local.rollback_only = False

    def _migrate(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        if not self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=1"
        ).fetchone():
            self.connection.executescript("""
                CREATE TABLE collections (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0, source_directory TEXT NOT NULL,
                    compiled_output_prefix TEXT NOT NULL UNIQUE, processing_profile_id TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 0, allow_manual_override INTEGER NOT NULL DEFAULT 0,
                    tags TEXT NOT NULL DEFAULT '[]', notes TEXT, revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX one_default_collection ON collections(is_default) WHERE is_default=1;
                CREATE TABLE profiles (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    settings TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE clips (id TEXT PRIMARY KEY, collection_id TEXT NOT NULL, state TEXT NOT NULL,
                    relative_source_path TEXT NOT NULL, relative_output_path TEXT, duration_seconds REAL NOT NULL,
                    output_available INTEGER NOT NULL DEFAULT 0, metadata TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
                    FOREIGN KEY(collection_id) REFERENCES collections(id));
                CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, progress TEXT NOT NULL,
                    created_at TEXT NOT NULL, finished_at TEXT, error TEXT);
                CREATE TABLE job_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT);
                CREATE TABLE audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, action_type TEXT NOT NULL,
                    target_id TEXT NOT NULL, actor TEXT NOT NULL, request_id TEXT NOT NULL, summary TEXT NOT NULL,
                    occurred_at TEXT NOT NULL);
                CREATE TABLE idempotency_records (request_id TEXT PRIMARY KEY, actor TEXT NOT NULL,
                    operation TEXT NOT NULL, response TEXT, created_at TEXT NOT NULL);
            """)
            self.connection.execute("INSERT INTO schema_migrations VALUES (1, datetime('now'))")
            self.connection.commit()
        if not self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=2"
        ).fetchone():
            self.connection.execute(
                "ALTER TABLE idempotency_records ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''"
            )
            self.connection.execute("INSERT INTO schema_migrations VALUES (2, datetime('now'))")
            self.connection.commit()
        if not self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=3"
        ).fetchone():
            # A job stores an immutable compilation snapshot.  This makes a
            # queued/retried process independent of later collection/profile edits.
            self.connection.executescript("""
                ALTER TABLE jobs ADD COLUMN collection_id TEXT;
                ALTER TABLE jobs ADD COLUMN clip_id TEXT;
                ALTER TABLE jobs ADD COLUMN fingerprint TEXT;
                ALTER TABLE jobs ADD COLUMN payload TEXT NOT NULL DEFAULT '{}';
                ALTER TABLE jobs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
                ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE jobs ADD COLUMN started_at TEXT;
                CREATE INDEX jobs_claim_index ON jobs(state, created_at);
                CREATE INDEX jobs_deduplicate_index ON jobs(fingerprint, state);
            """)
            self.connection.execute("INSERT INTO schema_migrations VALUES (3, datetime('now'))")
            self.connection.commit()
        if not self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=4"
        ).fetchone():
            # Trash records deliberately retain only catalog-derived paths.
            # They make restore explicit and ensure no background task can
            # accidentally purge user media.
            self.connection.executescript("""
                CREATE TABLE trash_records (
                    id TEXT PRIMARY KEY,
                    clip_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    original_source_path TEXT,
                    original_output_path TEXT,
                    source_trash_path TEXT,
                    output_trash_path TEXT,
                    created_at TEXT NOT NULL,
                    restored_at TEXT,
                    FOREIGN KEY(clip_id) REFERENCES clips(id)
                );
                CREATE INDEX trash_records_clip_index ON trash_records(clip_id, created_at);
            """)
            self.connection.execute("INSERT INTO schema_migrations VALUES (4, datetime('now'))")
            self.connection.commit()
        if not self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=5"
        ).fetchone():
            # Pending records are persisted before a multi-file move or an
            # irreversible unlink, so restart/recovery tooling has evidence of
            # every operation even when the filesystem reports an error.
            self.connection.executescript("""
                ALTER TABLE trash_records ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
                ALTER TABLE trash_records ADD COLUMN failure TEXT;
                CREATE TABLE lifecycle_requests (
                    id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    clip_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    state TEXT NOT NULL,
                    details TEXT NOT NULL,
                    failure TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(clip_id) REFERENCES clips(id)
                );
                CREATE INDEX lifecycle_requests_clip_index ON lifecycle_requests(clip_id, created_at);
            """)
            self.connection.execute("INSERT INTO schema_migrations VALUES (5, datetime('now'))")
            self.connection.commit()
        if not self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=6"
        ).fetchone():
            self.connection.executescript("""
                CREATE TABLE worker_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    job_id TEXT
                );
                CREATE INDEX worker_logs_timestamp_index ON worker_logs(timestamp DESC, id DESC);
                CREATE TABLE worker_status (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            self.connection.execute("INSERT INTO schema_migrations VALUES (6, datetime('now'))")
            self.connection.commit()
        if not self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=7"
        ).fetchone():
            # In-flight chunked uploads are persisted so a Worker restart
            # mid-upload leaves recoverable state (and a cleanup target)
            # instead of an orphaned temp file with no owner.
            self.connection.executescript("""
                CREATE TABLE upload_sessions (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    collection_id TEXT,
                    staging_path TEXT NOT NULL,
                    bytes_received INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            self.connection.execute("INSERT INTO schema_migrations VALUES (7, datetime('now'))")
            self.connection.commit()

    def close(self) -> None:
        with self._connections_lock:
            connections = tuple(self._connections)
            self._connections.clear()
        for connection in connections:
            connection.close()

    @property
    def conn(self) -> sqlite3.Connection:
        """Compatibility alias used by small Worker adapters and tests."""
        return self.connection
