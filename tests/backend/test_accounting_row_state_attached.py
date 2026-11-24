"""The row-state guards of migration accounting_row_state (docs/specs/accounting.md 8.1, db-constraints.md) on a
migrated SQLite database, driven through the real repository save / delete path. Invented data."""

import pytest

from permita38.core.repository import ConstraintViolation
from permita38.core.repository.sqlite import SQLiteRepository

FROZEN = "state.frozen"
ONCE = "state.set_once"
BOXES = {"81": {"base_cents": 100000, "tax_cents": 19000}, "66": {"tax_cents": 3000}, "list": [1, 2.5, "ä"]}
LINES = [{"entry": "e1", "amount_cents": 119000}, {"entry": "e2", "amount_cents": -3000}]
CLEARED = ("finalised_at", "finalised_by", "source_lines")


def vat(**kw):
    row = {
        "id": "v1x000", "year": 2026, "period_kind": "month", "period_no": 1, "from_date": "2026-01-01",
        "to_date": "2026-01-31", "due_date": "2026-02-10", "status": "final", "amends_key": "",
        "boxes": BOXES, "source_lines": LINES, "result_cents": 16000, "finalised_at": 1760000000,
        "finalised_by": "u1x000", "external": False, "note": "n", "flag": {"unknown": [1, 2]},
    }  # fmt: skip
    return {**row, **kw}


def zm(**kw):
    row = {
        "id": "z1x000", "year": 2026, "quarter": 1, "status": "final", "due_date": "2026-04-25", "amends_key": "",
        "lines": LINES, "source_lines": LINES, "finalised_at": 1760000000, "finalised_by": "u1x000",
    }  # fmt: skip
    return {**row, **kw}


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "data")
    r.startup()
    yield r
    r.close()


def refused(code, fn, *args):
    with pytest.raises(ConstraintViolation) as e:
        fn(*args)
    assert e.value.code == code


@pytest.mark.parametrize("table, make, key", [("vat_returns", vat, "boxes"), ("zm_reports", zm, "lines")])
class TestReturns:
    def test_unchanged_final_row_saves_again(self, repo, table, make, key):
        first = repo.save(table, make())
        repo.save(table, repo.get(table, first["id"]))  # read back and save: deterministic JSON and extra
        repo.save(table, make())  # the original dict, same key order
        again = repo.get(table, first["id"])
        assert {k: v for k, v in again.items() if k != "updated"} == {k: v for k, v in first.items() if k != "updated"}

    def test_frozen_columns_refused_mutable_pass(self, repo, table, make, key):
        r = repo.save(table, make())
        refused(FROZEN, repo.save, table, {**r, "due_date": "2026-05-01"})
        refused(FROZEN, repo.save, table, {**r, "year": 2027})
        refused(FROZEN, repo.save, table, {**r, "source_lines": LINES[:1]})
        refused(FROZEN, repo.save, table, {**r, "flag": {"unknown": [3]}})  # the extra JSON is locked too
        refused(FROZEN, repo.save, table, {**r, key: LINES[:1] if key == "lines" else {"81": {"tax_cents": 1}}})
        ok = repo.save(table, {**r, "reference": "R-1", "note": "filed"})
        assert ok["reference"] == "R-1"
        assert repo.get(table, r["id"])[key] == r[key]  # the refused saves wrote nothing

    def test_filed_on_is_set_once(self, repo, table, make, key):
        r = repo.save(table, make())
        r = repo.save(table, {**r, "filed_on": "2026-02-09"})
        assert repo.get(table, r["id"])["filed_on"] == "2026-02-09"
        refused(ONCE, repo.save, table, {k: v for k, v in r.items() if k != "filed_on"})  # cleared
        refused(ONCE, repo.save, table, {**r, "filed_on": "2026-02-10"})  # changed

    def test_reopen_while_unfiled_clears_the_results(self, repo, table, make, key):
        r = repo.save(table, make())
        gone = (key, *CLEARED)
        repo.save(table, {**{k: v for k, v in r.items() if k not in gone}, "status": "draft"})
        got = repo.get(table, r["id"])
        assert got["status"] == "draft" and not set(gone) & set(got)

    def test_reopen_after_filing_refused(self, repo, table, make, key):
        r = repo.save(table, make())
        r = repo.save(table, {**r, "filed_on": "2026-02-09"})
        refused(FROZEN, repo.save, table, {**r, "status": "draft"})
        gone = (key, *CLEARED)
        refused(FROZEN, repo.save, table, {**{k: v for k, v in r.items() if k not in gone}, "status": "draft"})

    def test_delete_final_refused_draft_allowed(self, repo, table, make, key):
        r = repo.save(table, make())
        refused(FROZEN, repo.delete, table, r["id"])
        d = repo.save(
            table, make(id="d1x000", status="draft", period_no=2, quarter=2, finalised_at=None, finalised_by=None)
        )
        assert repo.delete(table, d["id"])


def test_external_vat_return_editable_and_deletable_until_replaced(repo):
    r = repo.save("vat_returns", vat(external=True))
    r = repo.save("vat_returns", {**r, "boxes": {"81": {"base_cents": 1}}, "due_date": "2026-02-12"})
    r = repo.save("vat_returns", {**r, "filed_on": "2026-02-09"})
    r = repo.save("vat_returns", {**r, "boxes": {"81": {"base_cents": 3}}})  # still exempt after filing
    assert repo.delete("vat_returns", "v1x000")
    r = repo.save("vat_returns", vat(external=True))
    repo.save("vat_returns", vat(id="v2x000", amends_id="v1x000", amends_key="v1x000"))
    r = repo.save("vat_returns", {**r, "replaced_by": "v2x000"})  # replaced_by itself is mutable
    refused(FROZEN, repo.save, "vat_returns", {**r, "boxes": {"81": {"base_cents": 2}}})
    refused(FROZEN, repo.delete, "vat_returns", "v1x000")


def test_internal_final_return_not_exempt(repo):
    r = repo.save("vat_returns", vat(external=False))
    refused(FROZEN, repo.save, "vat_returns", {**r, "boxes": {}})


def test_zm_report_has_no_exemption(repo):
    r = repo.save("zm_reports", zm(external=True))  # an unknown field "external" lives in extra and exempts nothing
    refused(FROZEN, repo.save, "zm_reports", {**r, "lines": []})
    refused(FROZEN, repo.delete, "zm_reports", "z1x000")


def test_fiscal_year_closed_at_and_by_set_once(repo):
    y = repo.save("fiscal_years", {
        "id": "f1x000", "year": 2026, "chart": "skr03", "income_basis": "eur", "vat_basis": "soll", "vat_frequency": "month",
        "dauerfrist": False, "small_business": False, "rules_version": "r1", "opened_at": 1, "opened_by": "u1x000",
    })  # fmt: skip
    closed = repo.save("fiscal_years", {**y, "closed_at": 100, "closed_by": "u1x000"})
    repo.save("fiscal_years", {**closed, "rules_version": "r2"})  # not frozen otherwise
    refused(ONCE, repo.save, "fiscal_years", {**closed, "closed_at": 101})
    refused(ONCE, repo.save, "fiscal_years", {**closed, "closed_by": "u2x000"})
    with pytest.raises(ConstraintViolation):  # also the implies guard (closed_by needs closed_at) refuses this
        repo.save("fiscal_years", {k: v for k, v in closed.items() if k != "closed_at"})
    both = {k: v for k, v in closed.items() if k not in ("closed_at", "closed_by")}
    refused(ONCE, repo.save, "fiscal_years", both)
