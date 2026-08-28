"""SQLite connection and schema migrations owned by the Worker."""
# SQL migration statements are intentionally kept readable.
# ruff: noqa: E501

from __future__ import annotations

import sqlite3
from pathlib import Path


class Database:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @classmethod
    def create(cls, url: str) -> Database:
        if url != ":memory:":
            Path(url).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(url)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        db = cls(conn)
        db._migrate()
        return db

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

    def close(self) -> None:
        self.connection.close()

    @property
    def conn(self) -> sqlite3.Connection:
        """Compatibility alias used by small Worker adapters and tests."""
        return self.connection
