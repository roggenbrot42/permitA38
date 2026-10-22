import shutil
import sqlite3

import pytest

from permita38.core import database, models
from permita38.core.database import CURRENT_REVISION, DIALECT_NAMES, MIGRATIONS_DIR, create_migration, list_migrations
from permita38.core.repository import TABLES, DatabaseRevisionError
from permita38.core.repository import sql as sql_module
from permita38.core.repository.dialect import initial_migration_sql
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository


# ------------------------------------------------------------------ repository consistency rules
def test_current_revision_is_the_highest_migration_in_every_dialect():
    for d in DIALECT_NAMES:
        assert list_migrations(d)[-1].version == CURRENT_REVISION, d


def test_every_migration_exists_for_every_dialect():
    sets = {d: [(m.version, m.name) for m in list_migrations(d)] for d in DIALECT_NAMES}
    assert sets["sqlite"] == sets["postgresql"] == sets["mariadb"]


def test_initial_migrations_match_the_generator():
    if CURRENT_REVISION > 1:
        pytest.skip("generator output only describes revision 1; later migrations are handwritten")
    for d in DIALECT_NAMES:
        assert (MIGRATIONS_DIR / d / "0001_initial.sql").read_text() == initial_migration_sql(d), d


def test_models_are_covered_by_the_schema():
    """Adding a field to a model without a migration + schema column fails here."""
    pairs = {
        "customers": models.Customer, "projects": models.Project, "tasks": models.Task, "entries": models.Entry,
        "contracts": models.Contract, "absences": models.Absence, "expenses": models.Expense,
        "offers": models.Document, "invoices": models.Document, "templates": models.Template,
    }  # fmt: skip
    for coll, model in pairs.items():
        cols = set(TABLES[coll].column_names)
        missing = set(model.model_fields) - cols
        assert not missing, f"{coll}: model fields without a column: {sorted(missing)}"
    user_cols = set(TABLES["users"].column_names)
    assert set(models.UserOut.model_fields) - {"has_password"} <= user_cols
    assert {"password_hash", "pw_changed"} <= user_cols


# ------------------------------------------------------------------ applying migrations
def test_fresh_database_reaches_current_revision(tmp_path):
    repo = SQLiteRepository(tmp_path)
    assert repo.get_revision() == 0
    repo.startup()
    assert repo.get_revision() == CURRENT_REVISION
    assert not (tmp_path / "backups").exists()  # nothing to back up on a fresh database
    repo.close()
    raw = sqlite3.connect(tmp_path / DB_FILENAME)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == CURRENT_REVISION
    raw.close()


def test_upgrade_applies_pending_migrations_and_backs_up_first(tmp_path, monkeypatch):
    data = tmp_path / "data"
    old = SQLiteRepository(data)  # the current revision (the real app)
    rec = old.save("customers", {"name": "Keep me"})
    old.close()
    root = tmp_path / "migrations"
    for d in DIALECT_NAMES:
        shutil.copytree(MIGRATIONS_DIR / d, root / d)
    nxt = CURRENT_REVISION + 1
    (root / "sqlite" / f"{nxt:04d}_add_fax.sql").write_text('ALTER TABLE "customers" ADD COLUMN "fax" TEXT;\n')
    monkeypatch.setattr(sql_module, "list_migrations", lambda dialect: database.list_migrations(dialect, root))
    monkeypatch.setattr(sql_module, "CURRENT_REVISION", nxt)

    new = SQLiteRepository(data)
    new.startup()
    assert new.get_revision() == nxt
    assert new.get("customers", rec["id"])["name"] == "Keep me"
    assert "fax" in [r[1] for r in new.conn.execute('PRAGMA table_info("customers")')]
    backups = list((data / "backups").glob(f"pre-migration-r{CURRENT_REVISION}-to-r{nxt}-*.sqlite3"))
    assert len(backups) == 1
    snap = sqlite3.connect(backups[0])
    assert snap.execute("PRAGMA user_version").fetchone()[0] == CURRENT_REVISION  # the backup is the OLD revision
    assert snap.execute("SELECT name FROM customers").fetchone()[0] == "Keep me"
    snap.close()
    new.close()


def test_failed_migration_is_rolled_back(tmp_path, monkeypatch):
    data = tmp_path / "data"
    SQLiteRepository(data).startup()
    root = tmp_path / "migrations"
    for d in DIALECT_NAMES:
        shutil.copytree(MIGRATIONS_DIR / d, root / d)
    nxt = CURRENT_REVISION + 1
    (root / "sqlite" / f"{nxt:04d}_bad.sql").write_text("CREATE TABLE half_done (x TEXT);\nTHIS IS NOT SQL;\n")
    monkeypatch.setattr(sql_module, "list_migrations", lambda dialect: database.list_migrations(dialect, root))
    monkeypatch.setattr(sql_module, "CURRENT_REVISION", nxt)
    repo = SQLiteRepository(data)
    with pytest.raises(RuntimeError, match="rolled back"):
        repo.startup()
    assert repo.get_revision() == CURRENT_REVISION
    tables = [r[0] for r in repo.conn.execute("SELECT name FROM sqlite_master WHERE name='half_done'")]
    assert tables == []  # the partial CREATE TABLE did not survive
    repo.close()


def test_database_newer_than_the_app_is_refused(tmp_path):
    data = tmp_path / "data"
    repo = SQLiteRepository(data)
    repo.save("customers", {"name": "A"})
    repo.close()
    raw = sqlite3.connect(data / DB_FILENAME)
    raw.execute(f"PRAGMA user_version = {CURRENT_REVISION + 5}")
    raw.close()
    again = SQLiteRepository(data)
    with pytest.raises(DatabaseRevisionError, match="refusing to start"):
        again.startup()
    with pytest.raises(DatabaseRevisionError):
        again.list("customers")  # every entry point re-checks; nothing is written
    raw = sqlite3.connect(data / DB_FILENAME)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == CURRENT_REVISION + 5
    raw.close()


def test_auto_migrate_off_requires_explicit_migrate(tmp_path):
    data = tmp_path / "data"
    r = SQLiteRepository(data, auto_migrate=False)
    with pytest.raises(DatabaseRevisionError, match="permita38 migrate"):
        r.startup()
    assert r.migrate() == (0, CURRENT_REVISION)
    r.startup()
    r.close()


def test_app_refuses_to_start_on_newer_database(tmp_path):
    from fastapi.testclient import TestClient

    from permita38.api.server import get_app
    from permita38.core.config import Config

    cfg = Config(data_dir=tmp_path / "data", secret_key="s" * 16, web_dir=None)
    SQLiteRepository(cfg.data_dir).startup()
    raw = sqlite3.connect(cfg.data_dir / DB_FILENAME)
    raw.execute("PRAGMA user_version = 99")
    raw.close()
    with pytest.raises(DatabaseRevisionError):
        with TestClient(get_app(cfg)):
            pass


# ------------------------------------------------------------------ create-migration
def test_create_migration_numbers_files_in_every_dialect(tmp_path):
    root = tmp_path / "m"
    for d in DIALECT_NAMES:
        (root / d).mkdir(parents=True)
        (root / d / "0001_initial.sql").write_text("-- x")
    created = create_migration("Add Fax number!", root)
    assert sorted(p.parent.name for p in created) == sorted(DIALECT_NAMES)
    assert all(p.name == "0002_add_fax_number.sql" for p in created)
    assert create_migration("again", root)[0].name == "0003_again.sql"
    with pytest.raises(ValueError):
        create_migration("!!!", root)


def test_list_migrations_rejects_gaps_and_bad_names(tmp_path):
    (tmp_path / "sqlite").mkdir()
    (tmp_path / "sqlite" / "0002_gap.sql").write_text("")
    with pytest.raises(ValueError, match="without gaps"):
        list_migrations("sqlite", tmp_path)
    (tmp_path / "sqlite" / "0002_gap.sql").rename(tmp_path / "sqlite" / "weird.sql")
    with pytest.raises(ValueError, match="bad migration file name"):
        list_migrations("sqlite", tmp_path)
