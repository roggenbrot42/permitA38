"""SQLite-specific behaviour of the repository."""

import sqlite3

import pytest

from permita38.core.config import Config
from permita38.core.database import CURRENT_REVISION
from permita38.core.repository import TABLES, create_repository
from permita38.core.repository.dialect import SQLiteDialect
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository


def make(tmp_path, **kw) -> SQLiteRepository:
    return SQLiteRepository(tmp_path / "data", **kw)


def test_factory_default_is_sqlite_in_data_dir(tmp_path):
    repo = create_repository(Config(data_dir=tmp_path / "d", secret_key="s"))
    assert isinstance(repo, SQLiteRepository) and repo.db_path == (tmp_path / "d").resolve() / DB_FILENAME


def test_server_backends_are_prepared_but_not_implemented(tmp_path):
    from permita38.core.config import DatabaseConfig

    cfg = Config(data_dir=tmp_path, secret_key="s", database=DatabaseConfig(backend="postgresql", url="x"))
    with pytest.raises(NotImplementedError, match="porting-to-postgres"):
        create_repository(cfg)


def test_lazy_open_creates_nothing_until_first_use(tmp_path):
    repo = make(tmp_path)
    assert not (tmp_path / "data").exists()
    repo.list("customers")
    assert (tmp_path / "data" / DB_FILENAME).is_file()
    repo.close()


def test_single_file_and_no_json_records(tmp_path):
    repo = make(tmp_path)
    repo.save("customers", {"name": "A"})
    repo.save_settings({"a": 1})
    repo.close()
    names = sorted(p.name for p in (tmp_path / "data").iterdir())
    assert names == ["attachments", DB_FILENAME]  # no customers/ folder, no *.json, no -wal/-shm/-journal
    assert not list((tmp_path / "data").rglob("*.json"))


def test_pragmas_are_sync_friendly(tmp_path):
    repo = make(tmp_path)
    repo.startup()
    c = repo.conn
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert c.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    repo.close()


def test_wal_is_opt_in_and_checkpointed_on_close(tmp_path):
    repo = make(tmp_path, wal=True)
    repo.save("customers", {"name": "A"})
    assert repo.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    repo.close()
    assert not list((tmp_path / "data").glob("*-wal"))  # checkpointed and removed on clean close


def test_data_survives_close_and_reopen(tmp_path):
    repo = make(tmp_path)
    a = repo.save("customers", {"name": "A", "future": [1, 2]})
    repo.save_settings({"k": "v"})
    repo.close()
    again = make(tmp_path)
    assert again.get("customers", a["id"]) == a
    assert again.settings() == {"k": "v"}
    again.close()


def test_tables_match_the_declarative_schema(tmp_path):
    repo = make(tmp_path)
    repo.startup()
    d = SQLiteDialect()
    for name, table in TABLES.items():
        info = repo.conn.execute(f'PRAGMA table_info("{name}")').fetchall()
        got = {row[1]: row[2] for row in info}
        want = {c.name: d.sql_type(c) for c in table.columns}
        if table.extra_column:
            want[table.extra_column] = d.types["json"]
        assert got == want, name
    idx = {r[1] for r in repo.conn.execute("SELECT * FROM sqlite_master WHERE type='index'").fetchall()}
    for table in TABLES.values():
        assert {i.name for i in table.indexes} <= idx
    assert repo.get_revision() == CURRENT_REVISION
    repo.close()


def test_unknown_fields_live_in_the_extra_column_as_json(tmp_path):
    repo = make(tmp_path)
    a = repo.save("customers", {"name": "A", "novel": {"x": 1}})
    repo.close()
    db = sqlite3.connect(tmp_path / "data" / DB_FILENAME)
    assert db.execute("SELECT extra FROM customers WHERE id=?", (a["id"],)).fetchone()[0] == '{"novel": {"x": 1}}'
    assert db.execute("SELECT extra FROM customers WHERE name='A'").fetchone()
    db.close()


def test_typed_columns_hold_real_values(tmp_path):
    repo = make(tmp_path)
    e = repo.save("entries", {"date": "2026-02-03", "minutes": 90, "billable": False, "rate": 95})
    repo.close()
    db = sqlite3.connect(tmp_path / "data" / DB_FILENAME)
    row = db.execute(
        "SELECT minutes, billable, rate, typeof(minutes), typeof(rate) FROM entries WHERE id=?", (e["id"],)
    )
    assert row.fetchone() == (90, 0, 95.0, "integer", "real")
    db.close()


def test_usernames_are_unique_in_the_database(tmp_path):
    repo = make(tmp_path)
    repo.save("users", {"username": "boss", "name": "A"})
    with pytest.raises(sqlite3.IntegrityError):
        repo.save("users", {"username": "boss", "name": "B"})
    repo.close()


def test_conflict_copies_of_the_database_are_reported(tmp_path):
    repo = make(tmp_path)
    repo.startup()
    data = tmp_path / "data"
    (data / "permita38.sync-conflict-20260101-120000-ABCDEFG.sqlite3").write_bytes(b"x")  # Syncthing
    (data / "permita38.sqlite3 (conflicted copy 2026-01-01)").write_bytes(b"x")  # Nextcloud/Dropbox style
    (data / "backups").mkdir()
    (data / "backups" / "x.sync-conflict-1.sqlite3").write_bytes(b"x")
    (data / "unrelated.txt").write_text("hi")
    found = repo.conflicts()
    assert len(found) == 3 and all("conflict" in f for f in found)
    repo.close()
