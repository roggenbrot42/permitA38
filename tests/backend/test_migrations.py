import hashlib
import shutil
import sqlite3

import pytest

from permita38.core import database, models
from permita38.core.database import (
    DIALECT_NAMES,
    LEGACY_IDS,
    MIGRATIONS_DIR,
    create_migration,
    head_id,
    list_migrations,
    validate_dialects,
)
from permita38.core.repository import TABLES, DatabaseRevisionError
from permita38.core.repository import sql as sql_module
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository

CHAIN = list_migrations("sqlite")
HEAD = CHAIN[-1].id


# ------------------------------------------------------------------ helpers
def write_migration(root, file_name, mid, requires, body="", dialects=DIALECT_NAMES, header=None):
    for d in dialects:
        (root / d).mkdir(parents=True, exist_ok=True)
        text = header if header is not None else f"-- migration: {mid}\n-- requires: {requires}\n"
        (root / d / file_name).write_text(text + body)


def synthetic(tmp_path, specs):
    """specs: [(file_name, id, requires)] written to every dialect folder."""
    root = tmp_path / "m"
    for f, i, r in specs:
        write_migration(root, f, i, r)
    return root


def use_chain(monkeypatch, root):
    monkeypatch.setattr(sql_module, "list_migrations", lambda dialect: database.list_migrations(dialect, root))


def real_chain_copy(tmp_path):
    root = tmp_path / "migrations"
    for d in DIALECT_NAMES:
        shutil.copytree(MIGRATIONS_DIR / d, root / d)
    return root


def legacy_db(data, revision):
    """A database as written by alpha.1 and earlier: the first `revision` migration files, integer user_version."""
    data.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(data / DB_FILENAME)
    for m in CHAIN[:revision]:
        raw.executescript(m.sql())
    raw.execute(f"PRAGMA user_version = {revision}")
    raw.commit()
    return raw


def user_version(data):
    raw = sqlite3.connect(data / DB_FILENAME)
    try:
        return raw.execute("PRAGMA user_version").fetchone()[0]
    finally:
        raw.close()


def table_ids(data):
    raw = sqlite3.connect(data / DB_FILENAME)
    try:
        return [r[0] for r in raw.execute("SELECT id FROM schema_migrations ORDER BY applied_at, rowid")]
    finally:
        raw.close()


# ------------------------------------------------------------------ 1. chain validation (synthetic folders)
def test_valid_synthetic_chain_is_ordered_by_requires_not_by_file_name(tmp_path):
    root = synthetic(
        tmp_path,
        [
            ("0001-a.sql", "aaaaaaaa", "none"),
            ("0003-c.sql", "cccccccc", "bbbbbbbb"),
            ("0002-b.sql", "bbbbbbbb", "aaaaaaaa"),
        ],
    )
    assert [m.id for m in validate_dialects(root)["sqlite"]] == ["aaaaaaaa", "bbbbbbbb", "cccccccc"]
    assert head_id(root) == "cccccccc"


def test_equal_ordinals_are_allowed(tmp_path):
    root = synthetic(tmp_path, [("0004-x.sql", "aaaaaaaa", "none"), ("0004-y.sql", "bbbbbbbb", "aaaaaaaa")])
    assert [m.id for m in list_migrations("sqlite", root)] == ["aaaaaaaa", "bbbbbbbb"]


@pytest.mark.parametrize(
    "specs, message",
    [
        ([("0001-a.sql", "aaaaaaaa", "none"), ("0002-b.sql", "aaaaaaaa", "aaaaaaaa")], "duplicate migration id"),
        ([("0001-a.sql", "aaaaaaaa", "none"), ("0002-b.sql", "bbbbbbbb", "cccccccc")], "unknown migration"),
        ([("0001-a.sql", "aaaaaaaa", "none"), ("0002-b.sql", "bbbbbbbb", "none")], "exactly one migration"),
        ([("0001-a.sql", "aaaaaaaa", "bbbbbbbb"), ("0002-b.sql", "bbbbbbbb", "aaaaaaaa")], "exactly one migration"),
        (
            [
                ("0001-a.sql", "aaaaaaaa", "none"),
                ("0002-b.sql", "bbbbbbbb", "aaaaaaaa"),
                ("0003-c.sql", "cccccccc", "aaaaaaaa"),
            ],
            "fork",
        ),
        (
            [
                ("0001-a.sql", "aaaaaaaa", "none"),
                ("0002-b.sql", "bbbbbbbb", "cccccccc"),
                ("0003-c.sql", "cccccccc", "bbbbbbbb"),
            ],
            "not reachable",
        ),
        ([("0002-a.sql", "aaaaaaaa", "none"), ("0001-b.sql", "bbbbbbbb", "aaaaaaaa")], "ordinal decreases"),
        ([("0001_a.sql", "aaaaaaaa", "none")], "bad migration file name"),
        ([("0001-A.sql", "aaaaaaaa", "none")], "bad migration file name"),
        ([("0001-a.sql", "NOTHEX!!", "none")], "bad migration id"),
        ([("0001-a.sql", "aaaaaaaa", "xyz")], "bad requires"),
    ],
)
def test_chain_rule_violations_are_rejected(tmp_path, specs, message):
    root = synthetic(tmp_path, specs)
    with pytest.raises(ValueError, match=message):
        list_migrations("sqlite", root)


def test_missing_or_wrong_header_is_rejected(tmp_path):
    root = tmp_path / "m"
    write_migration(root, "0001-a.sql", None, None, header="CREATE TABLE x (y TEXT);\n")
    with pytest.raises(ValueError, match="bad migration header"):
        list_migrations("sqlite", root)
    write_migration(root, "0001-a.sql", None, None, header="-- requires: none\n-- migration: aaaaaaaa\n")  # swapped
    with pytest.raises(ValueError, match="bad migration header"):
        list_migrations("sqlite", root)
    write_migration(root, "0001-a.sql", None, None, header="")  # empty file
    with pytest.raises(ValueError, match="bad migration header"):
        list_migrations("sqlite", root)


def test_dialect_drift_is_rejected(tmp_path):
    root = synthetic(tmp_path, [("0001-a.sql", "aaaaaaaa", "none")])
    write_migration(root, "0002-b.sql", "bbbbbbbb", "aaaaaaaa", dialects=("sqlite", "postgresql"))  # mariadb missing
    with pytest.raises(ValueError, match="dialect folders differ"):
        validate_dialects(root)
    write_migration(root, "0002-b.sql", "bbbbbbbb", "aaaaaaaa", dialects=("mariadb",))
    validate_dialects(root)  # complete again
    write_migration(root, "0002-b.sql", "cccccccc", "aaaaaaaa", dialects=("mariadb",))  # same file name, other id
    with pytest.raises(ValueError, match="dialect folders differ"):
        validate_dialects(root)


# ------------------------------------------------------------------ 2. the real folders
def test_real_folders_form_a_valid_chain_identical_across_dialects():
    chains = validate_dialects()
    sig = {d: [(m.id, m.name, m.requires, m.ordinal) for m in c] for d, c in chains.items()}
    assert sig["sqlite"] == sig["postgresql"] == sig["mariadb"]
    assert [m.id for m in chains["sqlite"]][: len(LEGACY_IDS)] == list(LEGACY_IDS)
    assert chains["sqlite"][0].requires is None
    assert head_id() == chains["sqlite"][-1].id


def test_legacy_ids_and_names_are_frozen():
    assert [(m.id, m.file_name) for m in CHAIN[:3]] == [
        ("00000001", "0001-initial.sql"),
        ("00000002", "0002-totp.sql"),
        ("00000003", "0003-planning.sql"),
    ]


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


# ------------------------------------------------------------------ 3. legacy upgrade
@pytest.mark.parametrize("revision", [1, 2, 3])
def test_legacy_database_is_upgraded_and_keeps_its_data(tmp_path, revision):
    data = tmp_path / "data"
    raw = legacy_db(data, revision)
    raw.execute("INSERT INTO customers (id, name) VALUES ('cust00000001', 'ACME')")
    raw.commit()
    raw.close()

    repo = SQLiteRepository(data)
    assert repo.get_revision() == LEGACY_IDS[revision - 1]  # readable before anything is written
    repo.startup()
    assert repo.get_revision() == HEAD
    assert repo.applied_ids() == [m.id for m in CHAIN]
    assert repo.get("customers", "cust00000001")["name"] == "ACME"
    assert table_ids(data) == [m.id for m in CHAIN]
    assert user_version(data) == len(CHAIN)
    if revision < len(CHAIN):
        assert len(list((data / "backups").glob(f"pre-migration-*-before-{HEAD}.sqlite3"))) == 1
    # the migrated schema equals schema.py
    for name, table in TABLES.items():
        cols = [r[1] for r in repo.conn.execute(f'PRAGMA table_info("{name}")')]
        expected = table.column_names + ([table.extra_column] if table.extra_column else [])
        assert sorted(cols) == sorted(expected), name
    repo.close()


def test_legacy_revision_three_only_gets_bookkeeping_rows(tmp_path):
    data = tmp_path / "data"
    legacy_db(data, 3).close()
    repo = SQLiteRepository(data)
    repo.startup()
    # the legacy migrations are adopted as bookkeeping rows (their SQL never runs again); newer ones are applied
    assert table_ids(data)[: len(LEGACY_IDS)] == list(LEGACY_IDS)
    assert table_ids(data)[-1] == HEAD
    assert repo.migrate() == (HEAD, HEAD, 0)
    repo.close()


# ------------------------------------------------------------------ 4. empty database, idempotence
def test_fresh_database_reaches_the_head_and_a_second_run_is_a_noop(tmp_path):
    repo = SQLiteRepository(tmp_path)
    assert repo.get_revision() is None
    repo.startup()
    assert repo.get_revision() == HEAD
    assert not (tmp_path / "backups").exists()  # nothing to back up on a fresh database
    assert repo.migrate() == (HEAD, HEAD, 0)
    repo.close()
    again = SQLiteRepository(tmp_path)
    again.startup()
    assert again.migrate() == (HEAD, HEAD, 0)
    again.close()
    assert table_ids(tmp_path) == [m.id for m in CHAIN]  # no duplicates


def test_health_reports_ids_and_count(tmp_path):
    repo = SQLiteRepository(tmp_path)
    repo.startup()
    h = repo.health()
    assert h["revision"] == HEAD == h["current_revision"] and h["applied"] == len(CHAIN)
    repo.close()


def test_upgrade_applies_pending_migrations_and_backs_up_first(tmp_path, monkeypatch):
    data = tmp_path / "data"
    old = SQLiteRepository(data)
    rec = old.save("customers", {"name": "Keep me"})
    old.close()
    root = real_chain_copy(tmp_path)
    new_id = "7ecb5a30"
    write_migration(
        root, "0004-add_fax.sql", new_id, HEAD, 'ALTER TABLE "customers" ADD COLUMN "fax" TEXT;\n', dialects=("sqlite",)
    )
    for d in ("postgresql", "mariadb"):
        write_migration(root, "0004-add_fax.sql", new_id, HEAD, "", dialects=(d,))
    use_chain(monkeypatch, root)

    new = SQLiteRepository(data)
    new.startup()
    assert new.get_revision() == new_id
    assert new.get("customers", rec["id"])["name"] == "Keep me"
    assert "fax" in [r[1] for r in new.conn.execute('PRAGMA table_info("customers")')]
    backups = list((data / "backups").glob(f"pre-migration-*-before-{new_id}.sqlite3"))
    assert len(backups) == 1
    snap = sqlite3.connect(backups[0])
    assert snap.execute("PRAGMA user_version").fetchone()[0] == len(CHAIN)  # the backup is the OLD state
    assert snap.execute("SELECT id FROM schema_migrations ORDER BY rowid DESC").fetchone()[0] == HEAD
    assert snap.execute("SELECT name FROM customers").fetchone()[0] == "Keep me"
    snap.close()
    new.close()


# ------------------------------------------------------------------ 5. unknown id and holes
def test_unknown_applied_id_is_refused_without_touching_the_database(tmp_path):
    data = tmp_path / "data"
    repo = SQLiteRepository(data)
    repo.save("customers", {"name": "A"})
    repo.close()
    raw = sqlite3.connect(data / DB_FILENAME)
    raw.execute("INSERT INTO schema_migrations (id, name, applied_at) VALUES ('ffffffff', 'future', 'x')")
    raw.commit()
    raw.close()
    before = (data / DB_FILENAME).read_bytes()
    again = SQLiteRepository(data)
    with pytest.raises(DatabaseRevisionError, match="refusing to start"):
        again.startup()
    with pytest.raises(DatabaseRevisionError):
        again.list("customers")  # every entry point re-checks; nothing is written
    with pytest.raises(DatabaseRevisionError, match="ffffffff"):
        again.migrate()
    assert (data / DB_FILENAME).read_bytes() == before


def test_legacy_revision_above_the_known_ones_is_refused(tmp_path):
    data = tmp_path / "data"
    legacy_db(data, 3).execute("PRAGMA user_version = 7")
    with pytest.raises(DatabaseRevisionError, match="refusing to start"):
        SQLiteRepository(data).startup()


def test_hole_in_the_applied_ids_is_refused(tmp_path):
    data = tmp_path / "data"
    SQLiteRepository(data).startup()
    raw = sqlite3.connect(data / DB_FILENAME)
    raw.execute("DELETE FROM schema_migrations WHERE id = ?", (LEGACY_IDS[1],))
    raw.commit()
    raw.close()
    with pytest.raises(DatabaseRevisionError, match="not a prefix"):
        SQLiteRepository(data).startup()


def test_app_refuses_to_start_on_newer_database(tmp_path):
    from fastapi.testclient import TestClient

    from permita38.api.server import get_app
    from permita38.core.config import Config

    cfg = Config(data_dir=tmp_path / "data", secret_key="s" * 16, web_dir=None)
    SQLiteRepository(cfg.data_dir).startup()
    raw = sqlite3.connect(cfg.data_dir / DB_FILENAME)
    raw.execute("INSERT INTO schema_migrations (id, name, applied_at) VALUES ('ffffffff', 'future', 'x')")
    raw.commit()
    raw.close()
    with pytest.raises(DatabaseRevisionError):
        with TestClient(get_app(cfg)):
            pass


def test_auto_migrate_off_requires_explicit_migrate(tmp_path):
    data = tmp_path / "data"
    r = SQLiteRepository(data, auto_migrate=False)
    with pytest.raises(DatabaseRevisionError, match="permita38 migrate"):
        r.startup()
    assert r.migrate() == (None, HEAD, len(CHAIN))
    r.startup()
    r.close()


# ------------------------------------------------------------------ 6. failing migration
def test_failed_migration_is_rolled_back_and_reported_with_id_and_name(tmp_path, monkeypatch):
    data = tmp_path / "data"
    SQLiteRepository(data).startup()
    root = real_chain_copy(tmp_path)
    for d in DIALECT_NAMES:
        write_migration(
            root,
            "0004-bad.sql",
            "badbad01",
            HEAD,
            "CREATE TABLE half_done (x TEXT);\nTHIS IS NOT SQL;\n",
            dialects=(d,),
        )
    use_chain(monkeypatch, root)
    repo = SQLiteRepository(data)
    with pytest.raises(RuntimeError, match=r"badbad01 \(0004-bad.sql\).*rolled back"):
        repo.startup()
    assert repo.get_revision() == HEAD
    assert [r[0] for r in repo.conn.execute("SELECT name FROM sqlite_master WHERE name='half_done'")] == []
    assert "badbad01" not in table_ids(data)
    assert user_version(data) == len(CHAIN)
    repo.close()


# ------------------------------------------------------------------ 7. user_version
def test_user_version_equals_the_number_of_applied_migrations_after_every_run(tmp_path, monkeypatch):
    data = tmp_path / "data"
    repo = SQLiteRepository(data)
    repo.startup()
    repo.close()
    assert user_version(data) == len(CHAIN)
    root = real_chain_copy(tmp_path)
    prev = HEAD
    for n, mid in enumerate(("11111111", "22222222"), start=4):
        for d in DIALECT_NAMES:
            write_migration(root, f"{n:04d}-step{n}.sql", mid, prev, f"CREATE TABLE step{n} (x TEXT);\n", dialects=(d,))
        prev = mid
    use_chain(monkeypatch, root)
    again = SQLiteRepository(data)
    again.startup()
    again.close()
    assert user_version(data) == len(CHAIN) + 2 == len(table_ids(data))
    third = SQLiteRepository(data)
    third.startup()  # no-op run keeps it
    third.close()
    assert user_version(data) == len(CHAIN) + 2


def test_old_binary_would_refuse_an_upgraded_database(tmp_path):
    """alpha.1 knows revisions up to 3 and refuses a higher PRAGMA user_version; a legacy upgrade never lowers it."""
    data = tmp_path / "data"
    legacy_db(data, 1).close()
    SQLiteRepository(data).startup()
    assert user_version(data) == len(CHAIN) >= 3


# ------------------------------------------------------------------ 8. merge simulation
def test_two_branches_adding_a_migration_on_the_same_head_is_reported_as_a_fork(tmp_path):
    root = real_chain_copy(tmp_path)
    for d in DIALECT_NAMES:
        write_migration(root, "0004-calendars.sql", "aaaa0001", HEAD, dialects=(d,))
        write_migration(root, "0004-numbering.sql", "bbbb0002", HEAD, dialects=(d,))
    with pytest.raises(ValueError, match="fork"):
        validate_dialects(root)
    # the fix named in the message: the second branch re-points its first migration onto the new head
    for d in DIALECT_NAMES:
        write_migration(root, "0004-numbering.sql", "bbbb0002", "aaaa0001", dialects=(d,))
    chain = validate_dialects(root)["sqlite"]
    assert [m.id for m in chain][-2:] == ["aaaa0001", "bbbb0002"]


# ------------------------------------------------------------------ create-migration
def test_create_migration_writes_header_and_requires_in_every_dialect(tmp_path):
    root = synthetic(tmp_path, [("0001-initial.sql", "00000001", "none")])
    created = create_migration("Add Fax number!", root)
    assert sorted(p.parent.name for p in created) == sorted(DIALECT_NAMES)
    assert all(p.name == "0002-add_fax_number.sql" for p in created)
    first = created[0].read_text().splitlines()
    new_id = first[0].removeprefix("-- migration: ")
    assert len(new_id) == 8 and first[1] == "-- requires: 00000001"
    second = create_migration("again", root)
    assert second[0].name == "0003-again.sql"
    assert second[0].read_text().splitlines()[1] == f"-- requires: {new_id}"
    assert [m.name for m in validate_dialects(root)["sqlite"]] == ["initial", "add_fax_number", "again"]
    with pytest.raises(ValueError):
        create_migration("!!!", root)


# ------------------------------------------------------------------ read-only paths
def test_crlf_migration_files_parse_and_execute_without_carriage_returns(tmp_path):
    root = tmp_path / "crlf"
    body = b"CREATE TABLE crlf_probe (a INTEGER);\r\nCREATE TABLE crlf_probe2 (b INTEGER);\r\n"
    for d in DIALECT_NAMES:
        (root / d).mkdir(parents=True)
        (root / d / "0001-first.sql").write_bytes(b"-- migration: 7ecb5a30\r\n-- requires: none\r\n" + body)
    mig = list_migrations("sqlite", root)
    assert [(m.id, m.requires, m.name) for m in mig] == [("7ecb5a30", None, "first")]
    text = mig[0].sql()
    assert "\r" not in text and text.splitlines()[0] == "-- migration: 7ecb5a30"
    raw = sqlite3.connect(":memory:")
    raw.executescript(text)
    assert {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} == {
        "crlf_probe",
        "crlf_probe2",
    }


def test_read_only_calls_leave_an_alpha1_database_byte_for_byte_unchanged(tmp_path):
    data = tmp_path / "data"
    legacy_db(data, 3).close()  # alpha.1 style: user_version = 3, no schema_migrations table
    path = data / DB_FILENAME
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    repo = SQLiteRepository(data, auto_migrate=False)
    assert repo.applied_ids() == [m.id for m in CHAIN[:3]]
    assert repo.get_revision() == CHAIN[2].id
    h = repo.health()
    assert h["revision"] == CHAIN[2].id and h["applied"] == 3 and h["current_revision"] == HEAD
    assert list_migrations("sqlite")  # the status listing reads the chain only
    repo.close()

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not (data / (DB_FILENAME + "-journal")).exists()
    raw = sqlite3.connect(path)
    try:
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        raw.close()
    assert "schema_migrations" not in tables
