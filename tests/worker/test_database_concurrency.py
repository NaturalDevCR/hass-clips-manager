from concurrent.futures import ThreadPoolExecutor

from cinema_collections_worker.database import Database


def test_database_uses_one_sqlite_connection_per_thread(tmp_path) -> None:
    db = Database.create(str(tmp_path / "worker.sqlite3"))
    main_connection = db.connection

    with ThreadPoolExecutor(max_workers=2) as executor:
        worker_connections = list(executor.map(lambda _: db.connection, range(2)))

    assert all(connection is not main_connection for connection in worker_connections)
