import gc
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cinema_collections_worker.database import Database


def _churn(db: Database, count: int) -> None:
    """Run short-lived threads that each open their thread-local connection."""

    threads = [threading.Thread(target=lambda: db.connection) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    gc.collect()


def test_database_uses_one_sqlite_connection_per_thread(tmp_path) -> None:
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    main_connection = db.connection

    with ThreadPoolExecutor(max_workers=2) as executor:
        worker_connections = list(executor.map(lambda _: db.connection, range(2)))

    assert all(connection is not main_connection for connection in worker_connections)


def test_dead_threads_release_their_connections(tmp_path) -> None:
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    _ = db.connection  # the live main thread keeps exactly one connection

    _churn(db, 10)
    after_first_churn = len(db._connections)

    _churn(db, 50)
    # Dead threads must not pin connections: churning 5x more threads must not
    # grow the tracked set beyond the live-thread baseline.
    assert len(db._connections) <= after_first_churn


def test_live_threads_hold_distinct_connections(tmp_path) -> None:
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    barrier = threading.Barrier(2)
    connections: list[sqlite3.Connection] = []

    def grab() -> None:
        barrier.wait()
        connections.append(db.connection)

    threads = [threading.Thread(target=grab) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(connections) == 2
    assert connections[0] is not connections[1]


def test_in_memory_keeper_survives_creating_thread() -> None:
    created: list[Database] = []

    def construct() -> None:
        created.append(Database.create(":memory:"))

    thread = threading.Thread(target=construct)
    thread.start()
    thread.join()
    gc.collect()

    db = created[0]
    rows = db.connection.execute("SELECT version FROM schema_migrations").fetchall()
    assert len(rows) > 0


def test_close_after_thread_churn(tmp_path) -> None:
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    _ = db.connection

    _churn(db, 25)
    db.close()

    assert len(db._connections) == 0


@pytest.mark.skipif(not Path("/proc/self/fd").exists(), reason="requires procfs")
def test_dead_threads_do_not_leak_file_descriptors(tmp_path) -> None:
    def open_fd_count() -> int:
        return len(list(Path("/proc/self/fd").iterdir()))

    db = Database.create(str(tmp_path / "worker.sqlite3"))
    _ = db.connection
    gc.collect()
    baseline = open_fd_count()

    _churn(db, 30)

    # WAL mode holds several descriptors per connection; released connections
    # must return them to the process.
    assert open_fd_count() <= baseline + 3
