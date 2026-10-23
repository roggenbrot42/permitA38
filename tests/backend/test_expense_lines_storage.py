"""Storage of itemised expenses: the ``lines:json`` column in the repository and the migration that adds it.

The previous head is ``ab46b196`` (0004-expense_vat_deductible). The new migration is its direct successor; its slug
must contain ``lines`` (suggested: ``0005-expense_lines``). Its SQL adds a nullable column, so rows without lines
keep working.
"""

import shutil
import sqlite3

import pytest

from permita38.core import models
from permita38.core.database import DIALECT_NAMES, MIGRATIONS_DIR, list_migrations, validate_dialects
from permita38.core.repository import TABLES
from permita38.core.repository import sql as sql_module
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository

from .repository_registry import REGISTRY, registry_params


def _previous_head() -> str:
    """The id of the migration right before the expense-lines migration, taken from the chain (not a constant)."""
    chain = list_migrations("sqlite")
    index = next(i for i, m in enumerate(chain) if "lines" in m.file_name)
    return chain[index - 1].id


PREVIOUS_HEAD = _previous_head()
LINES = [
    {"description": "Room “2 nights” €", "gross": 160.0, "vat_rate": 7, "deductible": 100, "net": 149.53, "vat": 10.47},
    {"description": "Lunch", "gross": 100.0, "vat_rate": 19, "deductible": 70, "vat_deductible": 100, "category": "Meals"},
    {"description": "future", "gross": 0, "vat_rate": 0, "unknown": {"nested": [1, None, True, 2.5]}},
]  # fmt: skip


@pytest.fixture(params=registry_params())
def repo(request, tmp_path):
    r = REGISTRY[request.param](tmp_path)
    r.startup()
    yield r
    r.close()


# ------------------------------------------------------------------ repository
def test_schema_has_a_json_lines_column_on_expenses():
    col = next((c for c in TABLES["expenses"].columns if c.name == "lines"), None)
    assert col is not None and col.type == "json"
    assert "lines" in models.Expense.model_fields and "lines" in models.ExpenseIn.model_fields


def test_lines_round_trip_exactly_including_unknown_line_fields(repo):
    saved = repo.save("expenses", {"date": "2026-03-10", "net": 1.0, "lines": LINES})
    assert saved["lines"] == LINES
    assert repo.get("expenses", saved["id"])["lines"] == LINES
    assert next(x for x in repo.list("expenses") if x["id"] == saved["id"])["lines"] == LINES


def test_lines_are_replaced_and_can_be_removed(repo):
    a = repo.save("expenses", {"date": "2026-03-10", "net": 1.0, "lines": LINES})
    b = repo.save("expenses", {"id": a["id"], "date": "2026-03-10", "net": 1.0, "lines": LINES[:1]})
    assert repo.get("expenses", a["id"])["lines"] == LINES[:1] == b["lines"]
    c = repo.save("expenses", {"id": a["id"], "date": "2026-03-10", "net": 1.0})
    assert "lines" not in c and "lines" not in repo.get("expenses", a["id"])


def test_none_means_no_lines_and_an_empty_list_is_kept(repo):
    a = repo.save("expenses", {"date": "2026-03-10", "net": 1.0, "lines": None})
    assert "lines" not in a and "lines" not in repo.get("expenses", a["id"])
    b = repo.save("expenses", {"date": "2026-03-10", "net": 1.0, "lines": []})
    assert repo.get("expenses", b["id"])["lines"] == []


def test_expenses_without_lines_are_unaffected(repo):
    rec = {"date": "2026-03-10", "net": 84.03, "vat": 15.97, "vat_rate": 19, "deductible": 50, "vendor": "Müller"}
    saved = repo.save("expenses", dict(rec))
    got = repo.get("expenses", saved["id"])
    assert all(got[k] == v for k, v in rec.items()) and "lines" not in got


# ------------------------------------------------------------------ migration
def test_new_migration_follows_the_previous_head_in_every_dialect():
    chains = validate_dialects()
    for d in DIALECT_NAMES:
        chain = chains[d]
        ids = [m.id for m in chain]
        assert PREVIOUS_HEAD in ids and ids.index(PREVIOUS_HEAD) < len(ids) - 1, f"{d}: no migration after 0004"
        nxt = chain[ids.index(PREVIOUS_HEAD) + 1]
        assert nxt.requires == PREVIOUS_HEAD and "lines" in nxt.file_name
        assert "lines" in nxt.sql().lower() and "expenses" in nxt.sql().lower()


def test_database_at_the_previous_head_upgrades_and_old_expenses_keep_working(tmp_path, monkeypatch):
    chain = list_migrations("sqlite")
    keep = [m.file_name for m in chain[: [m.id for m in chain].index(PREVIOUS_HEAD) + 1]]
    root = tmp_path / "old_chain"
    for d in DIALECT_NAMES:
        shutil.copytree(MIGRATIONS_DIR / d, root / d)
        for f in (root / d).glob("*.sql"):
            if f.name not in keep:
                f.unlink()
    monkeypatch.setattr(sql_module, "list_migrations", lambda dialect: list_migrations(dialect, root))

    data = tmp_path / "data"
    old = SQLiteRepository(data)
    old.startup()
    assert old.get_revision() == PREVIOUS_HEAD
    assert "lines" not in [r[1] for r in old.conn.execute('PRAGMA table_info("expenses")')]
    rec = old.save("expenses", {"date": "2026-03-10", "net": 84.03, "vat": 15.97, "vat_rate": 19, "vendor": "Old"})
    old.close()
    monkeypatch.undo()  # back to the real chain

    new = SQLiteRepository(data)
    new.startup()
    assert new.get_revision() != PREVIOUS_HEAD and new.get_revision() == list_migrations("sqlite")[-1].id
    assert "lines" in [r[1] for r in new.conn.execute('PRAGMA table_info("expenses")')]
    kept = new.get("expenses", rec["id"])
    assert kept["vendor"] == "Old" and kept["net"] == 84.03 and "lines" not in kept
    again = new.save("expenses", {**kept, "lines": LINES})
    assert new.get("expenses", again["id"])["lines"] == LINES
    new.close()
    raw = sqlite3.connect(data / DB_FILENAME)
    assert raw.execute("SELECT lines FROM expenses WHERE id = ?", (rec["id"],)).fetchone()[0] is not None
    raw.close()
