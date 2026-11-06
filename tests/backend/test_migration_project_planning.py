"""Migration project_planning (docs/specs/project-centric-planning.md, example E8): `projects.kind`, data fixes, the
removal of `personal` and `member_ids` (no project is inserted) and the planner's guards. All data is invented."""

import sqlite3

import pytest

from permita38.core.repository import ConstraintViolation
from permita38.core.repository.schema import GUARDS
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository
from permita38.core.service.integrity import IntegrityService

from .test_migrations import DIALECT_NAMES, HEAD, real_chain_copy, use_chain

NAME = "project_planning"


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A database at the previous head (this migration removed from a copy of the real chain) with the rows of E8."""
    root = real_chain_copy(tmp_path)
    for d in DIALECT_NAMES:
        for f in (root / d).glob(f"*-{NAME}.sql"):
            f.unlink()
    data = tmp_path / "data"
    with monkeypatch.context() as m:
        use_chain(m, root)
        old = SQLiteRepository(data)
        old.startup()
        assert old.get_revision() != HEAD
        old.close()
    raw = sqlite3.connect(data / DB_FILENAME)
    # the reference guards (0017) refuse a row that points at a missing target: create the targets first
    for table, rid in (
        ("customers", "cust0001"),
        ("users", "user0001"),
        ("users", "user0002"),
        ("suppliers", "supp0001"),
        ("incoming_offers", "inoff001"),
    ):
        raw.execute(f'INSERT INTO "{table}" ("id") VALUES (?)', (rid,))
    proj = 'INSERT INTO "projects" ("id", "name", "customer_id", "personal", "status", "created", "updated") VALUES (?, ?, ?, ?, ?, 1, 1)'
    raw.executemany(
        proj,
        [
            ("projecta", "A personal", None, 1, "active"),
            ("projectb", "B plain", None, 0, "active"),
            ("projectc", "C personal with customer", "cust0001", 1, "active"),
            ("projectd", "D with a contract", "", 0, "active"),
            ("projecte", "E no status", None, 0, None),
            ("projectf", "F empty customer, no documents", "", 0, "active"),
        ],
    )
    con = 'INSERT INTO "contracts" ("id", "title", "project_id", "start", "end", "hours_per_week", "total_hours", "status") VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
    raw.executemany(
        con,
        [
            ("contractd", "Contract D", "projectd", "2026-01-01", "2026-06-30", 10, None, "confirmed"),
            ("contractf", "Contract F", "projectd", "2026-01-01", "2026-06-30", 12.5, 200, None),
            ("contractg", "Contract G", "projectd", "2026-01-01", "2026-06-30", None, 0, "confirmed"),
        ],
    )
    raw.execute(
        'INSERT INTO "allocations" ("id", "user_id", "incoming_offer_id", "project_id", "start", "end", '
        '"hours_per_week", "total_hours", "status") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        ("allocath", "user0001", "inoff001", "projecta", "2026-01-01", "2026-03-01", 8, 100, None),
    )
    raw.commit()
    raw.close()
    return data


@pytest.fixture
def repo(old_db):
    r = SQLiteRepository(old_db)
    r.startup()  # applies the migration
    yield r
    r.close()


def refused(code, fn, *args):
    with pytest.raises(ConstraintViolation) as e:
        fn(*args)
    assert e.value.code == code


def test_e8_data_fixes(repo):
    assert repo.get_revision() == HEAD
    kinds = {p: repo.get("projects", p)["kind"] for p in ("projecta", "projectb", "projectc", "projectd", "projecte")}
    assert kinds == {
        "projecta": "internal",
        "projectb": "internal",
        "projectc": "customer",
        "projectd": "customer",
        "projecte": "internal",
    }
    assert repo.get("projects", "projecte")["status"] == "active"
    f, g = repo.get("contracts", "contractf"), repo.get("contracts", "contractg")
    assert "total_hours" not in f and f["hours_per_week"] == 12.5 and f["status"] == "confirmed"
    assert g["total_hours"] == 0 and "hours_per_week" not in g
    h = repo.get("allocations", "allocath")
    assert h["incoming_offer_id"] == "inoff001" and h["status"] == "active"
    assert "total_hours" not in h and h["hours_per_week"] == 8
    assert "personal" not in {r[1] for r in repo.conn.execute('PRAGMA table_info("projects")')}


def test_empty_customer_id_without_documents_becomes_internal(repo):
    assert repo.get("projects", "projectf")["kind"] == "internal"


def test_an_offer_or_invoice_makes_a_project_a_customer_project(tmp_path, monkeypatch):
    """An offer or an invoice that refers to a project also makes it a customer project (an offer alone, too)."""
    root = real_chain_copy(tmp_path)
    for d in DIALECT_NAMES:
        for f in (root / d).glob(f"*-{NAME}.sql"):
            f.unlink()
    data = tmp_path / "data"
    with monkeypatch.context() as m:
        use_chain(m, root)
        old = SQLiteRepository(data)
        old.startup()
        old.close()
    raw = sqlite3.connect(data / DB_FILENAME)
    raw.execute(
        'INSERT INTO "projects" ("id", "name", "personal", "status") VALUES (\'projecto\', \'O\', 1, \'active\')'
    )
    raw.execute('INSERT INTO "offers" ("id", "project_id") VALUES (\'offr0001\', \'projecto\')')
    raw.execute(
        'INSERT INTO "projects" ("id", "name", "personal", "status") VALUES (\'projecti\', \'I\', 1, \'active\')'
    )
    raw.execute('INSERT INTO "invoices" ("id", "project_id") VALUES (\'invc0001\', \'projecti\')')
    raw.commit()
    raw.close()
    r = SQLiteRepository(data)
    r.startup()
    try:
        got = [r.get("projects", p) for p in ("projecto", "projecti")]
        assert [x["kind"] for x in got if x] == ["customer", "customer"]
    finally:
        r.close()


def test_no_project_is_inserted_and_a_second_run_changes_nothing(old_db):
    def snapshot():
        raw = sqlite3.connect(old_db / DB_FILENAME)
        try:
            return raw.execute('SELECT * FROM "projects" ORDER BY "id"').fetchall()
        finally:
            raw.close()

    r = SQLiteRepository(old_db)
    r.startup()
    r.close()
    first = snapshot()
    assert [row[0] for row in first] == ["projecta", "projectb", "projectc", "projectd", "projecte", "projectf"]
    r = SQLiteRepository(old_db)
    r.startup()
    r.close()
    assert snapshot() == first


def test_member_ids_and_personal_are_dropped_and_other_values_survive(repo):
    cols = {r[1] for r in repo.conn.execute('PRAGMA table_info("projects")')}
    assert "member_ids" not in cols and "personal" not in cols
    c = repo.get("projects", "projectc")
    assert c["name"] == "C personal with customer" and c["customer_id"] == "cust0001"
    assert c["status"] == "active" and c["created"] == 1 and c["updated"] == 1
    assert "member_ids" not in c and "personal" not in c
    assert repo.get("projects", "internaladmin") is None


def test_grandfathered_rows_can_be_resaved_and_are_reported(repo):
    g = repo.get("contracts", "contractg")
    repo.save("contracts", g)  # unchanged
    repo.save("contracts", {**g, "title": "Contract G renamed"})  # another field
    refused("range.bounds", repo.save, "contracts", {**g, "total_hours": 100001})  # another violation
    h = repo.get("allocations", "allocath")
    repo.save("allocations", h)
    repo.save("allocations", {**h, "end": "2026-03-15"})
    findings = IntegrityService(repo).run(
        only=("range.bounds", "state.implies:guard_allocations_user_no_incoming_offer_implies")
    )
    got = sorted((f.code, f.collection, f.id) for f in findings)
    assert ("range.bounds", "contracts", "contractg") in got
    assert ("state.implies:guard_allocations_user_no_incoming_offer_implies", "allocations", "allocath") in got


def test_a_new_contract_with_zero_total_hours_is_refused(repo):
    row = {"title": "New", "project_id": "projectb", "start": "2026-01-01", "end": "2026-02-01", "status": "confirmed"}
    refused("range.bounds", repo.save, "contracts", {**row, "total_hours": 0})
    assert repo.save("contracts", {**row, "total_hours": 5})["total_hours"] == 5


CONTRACT = {
    "title": "C",
    "project_id": "projectb",
    "start": "2026-01-01",
    "end": "2026-02-01",
    "hours_per_week": 10,
    "status": "confirmed",
}
ALLOC = {
    "user_id": "user0002",
    "project_id": "projectb",
    "start": "2026-01-01",
    "end": "2026-02-01",
    "hours_per_week": 10,
    "status": "active",
}
ALLOC_T = {**{k: v for k, v in ALLOC.items() if k != "hours_per_week"}, "total_hours": 50}
CONTRACT_T = {**{k: v for k, v in CONTRACT.items() if k != "hours_per_week"}, "total_hours": 50}
SUPPLIER = {**{k: v for k, v in ALLOC.items() if k != "user_id"}, "supplier_id": "supp0001", "rate": 80}
PROJECT = {"name": "P", "kind": "internal", "status": "active"}

# (table, valid base row, changed fields, expected code): one refused new row per new guard
REFUSED: list[tuple[str, dict, dict, str]] = [
    ("projects", PROJECT, {"kind": None}, "state.required"),
    ("projects", PROJECT, {"kind": "personal"}, "vocab.unknown"),
    ("projects", PROJECT, {"status": "finished"}, "vocab.unknown"),
    ("projects", PROJECT, {"name": "  "}, "state.required"),
    ("projects", PROJECT, {"customer_id": "cust0009"}, "state.implies:guard_projects_internal_customer_implies"),
    ("projects", PROJECT, {"budget_hours": 0}, "range.bounds"),
    ("projects", PROJECT, {"budget_hours": 100001}, "range.bounds"),
    ("allocations", ALLOC, {"project_id": None}, "state.required"),
    ("allocations", ALLOC, {"start": None}, "state.required"),
    ("allocations", ALLOC, {"end": None}, "state.required"),
    ("allocations", ALLOC, {"status": "paused"}, "vocab.unknown"),
    ("allocations", ALLOC, {"total_hours": 50}, "state.exactly_one"),
    ("allocations", ALLOC, {"hours_per_week": None}, "state.exactly_one"),
    ("allocations", ALLOC, {"hours_per_week": 80.5}, "range.bounds"),
    ("allocations", ALLOC, {"hours_per_week": 0}, "range.bounds"),
    ("allocations", ALLOC_T, {"total_hours": 100001}, "range.bounds"),
    ("allocations", ALLOC_T, {"total_hours": 0}, "range.bounds"),
    ("allocations", ALLOC, {"end": "2031-01-10"}, "range.too_long"),
    ("allocations", ALLOC, {"status": "cancelled"}, "state.implies:guard_allocations_cancelled_from_implies"),
    (
        "allocations",
        ALLOC,
        {"cancelled_from": "2026-01-15"},
        "state.implies:guard_allocations_active_no_cancelled_from_implies",
    ),
    (
        "allocations",
        SUPPLIER,
        {"rate": None},
        "state.implies:guard_allocations_supplier_rate_implies",
    ),
    ("allocations", ALLOC, {"rate": 10001}, "range.bounds"),
    (
        "allocations",
        ALLOC,
        {"incoming_offer_id": "inoff002"},
        "state.implies:guard_allocations_user_no_incoming_offer_implies",
    ),
    ("contracts", CONTRACT, {"title": ""}, "state.required"),
    ("contracts", CONTRACT, {"start": None}, "state.required"),
    ("contracts", CONTRACT, {"end": None}, "state.required"),
    ("contracts", CONTRACT, {"status": "open"}, "vocab.unknown"),
    ("contracts", CONTRACT, {"total_hours": 5}, "state.exactly_one"),
    ("contracts", CONTRACT, {"hours_per_week": None}, "state.exactly_one"),
    ("contracts", CONTRACT, {"hours_per_week": 10001}, "range.bounds"),
    ("contracts", CONTRACT, {"hours_per_week": 0}, "range.bounds"),
    ("contracts", CONTRACT_T, {"total_hours": 100001}, "range.bounds"),
    ("contracts", CONTRACT, {"rate": 10001}, "range.bounds"),
]


@pytest.mark.parametrize(("table", "base", "change", "code"), REFUSED)
def test_each_new_guard_refuses_a_new_row(repo, table, base, change, code):
    repo.save(table, dict(base))  # the base row is valid
    refused(code, repo.save, table, {**base, **change})


def test_the_refused_rows_cover_every_new_guard(repo):
    """Every guard of this migration is named by a refused row above (implies by its code, others by table/kind)."""
    new = [g for g in GUARDS if g.name in _new_guard_names()]
    assert len(new) == 27
    covered = set()
    for table, _base, change, code in REFUSED:
        for g in new:
            if g.table != table:
                continue
            if g.kind == "implies":
                if code == g.code:
                    covered.add(g.name)
            elif code == g.code and set(change) & set(g.columns):
                covered.add(g.name)
    assert covered == {g.name for g in new}


def _new_guard_names():
    from permita38.core import database

    sql = next(m for m in database.list_migrations("sqlite") if m.name == NAME).sql()
    return {g.name for g in GUARDS if f'"{g.name}_ins"' in sql}
