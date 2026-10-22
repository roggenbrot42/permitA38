"""Backups (manual, automatic, retention) and the migration/backup CLI commands."""

import sqlite3
import time

from permita38.cli.main import main
from permita38.core.database import CURRENT_REVISION
from permita38.core.repository import auto_backup
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository


def test_backup_is_a_consistent_openable_copy(tmp_path):
    repo = SQLiteRepository(tmp_path / "d")
    repo.save("customers", {"name": "A"})
    dest = repo.backup(tmp_path / "out" / "copy.sqlite3")
    repo.save("customers", {"name": "B"})  # later writes are not in the copy
    db = sqlite3.connect(dest)
    assert [r[0] for r in db.execute("SELECT name FROM customers")] == ["A"]
    assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_REVISION
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db.close()
    assert not list((tmp_path / "out").glob("*.tmp"))
    repo.close()


def test_default_backup_location_and_name(tmp_path):
    repo = SQLiteRepository(tmp_path / "d")
    p = repo.backup()
    assert p.parent == (tmp_path / "d").resolve() / "backups" and p.name.startswith("permita38-")
    repo.close()


def test_auto_backup_once_per_day_and_prunes(tmp_path):
    repo = SQLiteRepository(tmp_path / "d")
    repo.startup()
    folder = repo.backup_dir
    folder.mkdir()
    for day in range(1, 21):  # 20 old daily backups
        (folder / f"permita38-202601{day:02d}-030000.sqlite3").write_bytes(b"old")
    (folder / "pre-migration-r1-to-r2-20260101-000000.sqlite3").write_bytes(b"keep")  # never pruned
    made = auto_backup(repo, keep=5)
    assert made is not None and made.name.startswith("permita38-" + time.strftime("%Y%m%d"))
    regular = sorted(p.name for p in folder.glob("permita38-*.sqlite3"))
    assert len(regular) == 5 and regular[-1] == made.name  # newest 5 kept
    assert (folder / "pre-migration-r1-to-r2-20260101-000000.sqlite3").exists()
    assert auto_backup(repo, keep=5) is None  # already one today
    assert len(list(folder.glob("permita38-*.sqlite3"))) == 5
    repo.close()


def test_startup_of_the_app_takes_the_daily_backup(tmp_path):
    from fastapi.testclient import TestClient

    from permita38.api.server import get_app
    from permita38.core.config import Config

    cfg = Config(data_dir=tmp_path / "d", secret_key="s" * 16, web_dir=None)
    with TestClient(get_app(cfg)):
        pass
    assert len(list((tmp_path / "d" / "backups").glob("permita38-*.sqlite3"))) == 1
    cfg2 = Config(data_dir=tmp_path / "e", secret_key="s" * 16, web_dir=None)
    cfg2.database.auto_backup = False
    with TestClient(get_app(cfg2)):
        pass
    assert not (tmp_path / "e" / "backups").exists()


def test_cli_backup_and_migrate(tmp_path, capsys):
    cfg = ["-c", str(tmp_path / "cfg.yaml"), "--data", str(tmp_path / "d")]
    main([*cfg, "migrate"])
    assert f"Migrated revision 0 -> {CURRENT_REVISION}" in capsys.readouterr().out
    main([*cfg, "migrate"])
    assert f"Database is at revision {CURRENT_REVISION}" in capsys.readouterr().out
    target = tmp_path / "snap.sqlite3"
    main([*cfg, "backup", "--dest", str(target)])
    assert "Backup written to" in capsys.readouterr().out and target.is_file()
    assert (tmp_path / "d" / DB_FILENAME).is_file()


def test_cli_create_migration_and_dump_ddl(tmp_path, capsys, monkeypatch):
    from permita38.core import database

    root = tmp_path / "m"
    for d in database.DIALECT_NAMES:
        (root / d).mkdir(parents=True)
        (root / d / "0001_initial.sql").write_text("-- x")
    monkeypatch.setattr(database, "MIGRATIONS_DIR", root)
    main(["create-migration", "add thing"])
    assert all((root / d / "0002_add_thing.sql").is_file() for d in database.DIALECT_NAMES)
    capsys.readouterr()
    main(["dump-ddl", "--dialect", "postgresql"])
    assert 'CREATE TABLE "customers"' in capsys.readouterr().out


def test_cli_refuses_newer_database(tmp_path):
    import pytest

    cfg = ["-c", str(tmp_path / "cfg.yaml"), "--data", str(tmp_path / "d"), "--no-browser"]
    main([*cfg, "migrate"])
    raw = sqlite3.connect(tmp_path / "d" / DB_FILENAME)
    raw.execute("PRAGMA user_version = 99")
    raw.close()
    with pytest.raises(SystemExit) as e:
        main([*cfg, "serve"])
    assert "cannot use this database" in str(e.value)
