# ruff: noqa: F811
"""Accounting integrity (docs/specs/accounting.md 7.1, 7.6, 7.9, 18; WP E25): chain and balance findings per fiscal
year, the sanity warnings, the synced-folder warning, `check-db` output and `GET /api/accounting/integrity`.

Every finding is provoked on a throwaway folder (tampering through raw SQL with the guard triggers dropped, or a row
saved past the services) and is absent on the clean folder."""

import json
import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from permita38.cli.main import main
from permita38.core.repository.sqlite import DB_FILENAME
from permita38.core.service import integrity
from permita38.core.service.credit_notes import CreditNoteService
from permita38.core.service.integrity import ACCOUNTING_RULES, IntegrityService
from permita38.core.service.journal import JournalService
from permita38.core.service.locks import LockService
from permita38.core.service.records import RecordService
from permita38.core.service.tax_bookings import TaxBookingService
from permita38.core.service.year_closing import YearClosingService

from .test_issue_posting import (  # noqa: F401  (fixtures and helpers)
    customer,
    draft,
    fiscal_year,
    init,
    line,
    repo,
    sales,
)
from .test_rbac import clients  # noqa: F401  (fixture)
from .test_vat_returns import USER, make_service, post_sale
from .test_vat_returns import fiscal_year as vat_fiscal_year

BOSS = {"id": "u1x000", "role": "boss"}
ACCOUNTING = ("journal.", "accounting.")


def codes(repo, *prefixes):
    return sorted((f.code, f.id) for f in IntegrityService(repo).run(prefixes or ACCOUNTING))


def tamper(repo, *statements):
    """Raw SQL on the database file with every guard trigger dropped (the app never does this)."""
    con = sqlite3.connect(repo.data_dir / DB_FILENAME)
    try:
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
            con.execute(f'DROP TRIGGER "{name}"')
        for sql in statements:
            con.execute(sql)
        con.commit()
    finally:
        con.close()


@pytest.fixture
def books(repo, sales):
    """Accounting on (2026, accrual), an issued invoice with a payment and an expense: entries 1..3, nothing to report."""
    init(repo)
    cust = customer(repo)
    inv = draft(repo, "2026-03-10", [line("100.00")], customer=cust)
    sales.finalize("invoices", inv["id"])
    inv = repo.get("invoices", inv["id"])
    sales.payments.today = lambda: date(2026, 12, 31)
    pay = sales.payments.record(inv["id"], date="2026-03-20", amount=Decimal("119"), kind="payment", user=BOSS)
    exp = RecordService(repo).create(
        BOSS, "expenses", {"date": "2026-03-11", "net": 100.0, "vat": 19.0, "vat_rate": 19.0, "category": "software"}
    )
    assert len(JournalService(repo).entries(2026)) == 3
    return {"inv": inv, "pay": pay, "exp": exp}


def test_a_clean_folder_has_no_accounting_findings(repo, books):
    assert codes(repo) == []


def test_without_accounting_nothing_is_reported(repo):
    repo.save("invoices", {"id": "inv0001", "number": "R-1", "status": "sent", "date": "2026-03-01", "lines": []})
    assert codes(repo) == []


# ---------------------------------------------------------------- chain findings (error)
def test_a_changed_entry_breaks_its_hash(repo, books):
    tamper(repo, "UPDATE journal_entries SET text = 'changed' WHERE entry_no = 2")
    assert codes(repo) == [("journal.hash", "2026/2")]
    assert {f.severity for f in IntegrityService(repo).run(ACCOUNTING)} == {"error"}


def test_a_changed_prev_hash(repo, books):
    tamper(repo, "UPDATE journal_entries SET prev_hash = '" + "1" * 64 + "' WHERE entry_no = 2")
    assert ("journal.prev_hash", "2026/2") in codes(repo)


def test_an_unbalanced_entry(repo, books):
    tamper(
        repo,
        "UPDATE journal_lines SET amount_cents = amount_cents + 1 WHERE id = (SELECT id FROM journal_lines "
        "WHERE entry_id IN (SELECT id FROM journal_entries WHERE entry_no = 1) LIMIT 1)",
    )
    assert ("journal.unbalanced", "2026/1") in codes(repo)


def test_a_deleted_middle_entry_is_a_gap(repo, books):
    tamper(
        repo,
        "DELETE FROM journal_lines WHERE entry_id IN (SELECT id FROM journal_entries WHERE entry_no = 2)",
        "DELETE FROM journal_entries WHERE entry_no = 2",
    )
    assert ("journal.gap", "2026/3") in codes(repo)


def test_a_deleted_last_entry_leaves_the_counter_ahead(repo, books):
    tamper(
        repo,
        "DELETE FROM journal_lines WHERE entry_id IN (SELECT id FROM journal_entries WHERE entry_no = 3)",
        "DELETE FROM journal_entries WHERE entry_no = 3",
    )
    assert ("journal.counter", "2026/2") in codes(repo)


def _sql(value):
    return "NULL" if value is None else f"'{value}'" if isinstance(value, str) else str(value)


def close(repo, **fields):
    row = repo.find("fiscal_years", year=2026)[0]
    tail_hash, count = JournalService(repo).chain_tail(2026)
    values = {"closed_at": 1, "closed_by": "u1x000", "closing_hash": tail_hash, "closing_entry_no": count, **fields}
    tamper(repo, *[f"UPDATE fiscal_years SET {k} = {_sql(v)} WHERE id = '{row['id']}'" for k, v in values.items()])


def test_a_closed_year_with_a_matching_anchor_is_clean(repo, books):
    close(repo)
    assert codes(repo) == []


def test_a_closed_year_anchor_mismatch(repo, books):
    close(repo, closing_hash="2" * 64, closing_entry_no=2)
    assert codes(repo) == [("journal.closing_count", "2026/3"), ("journal.closing_hash", "2026/3")]


def test_a_closed_year_without_an_anchor(repo, books):
    close(repo, closing_hash=None, closing_entry_no=None)
    assert codes(repo) == [("journal.closing_missing", "2026")]


def test_each_fiscal_year_has_its_own_chain(repo, books):
    repo.save("fiscal_years", fiscal_year(2027))
    assert codes(repo) == []  # an empty open year is fine
    tamper(repo, "UPDATE journal_entries SET text = 'x' WHERE entry_no = 1")
    assert codes(repo) == [("journal.hash", "2026/1")]


# ---------------------------------------------------------------- sanity warnings
def test_an_issued_invoice_without_a_snapshot(repo, books):
    repo.save(
        "invoices",
        {"id": "invnosnap", "number": "R-9", "status": "sent", "date": "2026-04-01", "lines": [], "customer_id": "x"},
    )
    assert codes(repo) == [("accounting.invoice_no_snapshot", "invnosnap")]


def test_an_invoice_before_the_start_is_not_reported(repo, books):
    repo.save("invoices", {"id": "invold01", "number": "R-0", "status": "sent", "date": "2025-12-31", "lines": []})
    assert codes(repo) == []


def test_an_invoice_with_a_snapshot_but_no_issue_entry(repo, sales):
    cust = customer(repo)
    inv = draft(repo, "2026-03-10", [line("100.00")], customer=cust)
    sales.finalize("invoices", inv["id"])  # accounting is not on yet: snapshot, no entry
    init(repo)
    assert codes(repo) == [("accounting.invoice_no_entry", inv["id"])]


def test_an_invoice_that_posts_nothing_is_not_reported(repo, sales):
    init(repo, income="eur", vat="soll", small_business=True)  # I6: no issue entry
    cust = customer(repo)
    inv = draft(repo, "2026-03-10", [line("100.00", None)], customer=cust, tax_treatment="small_business")
    sales.finalize("invoices", inv["id"])
    assert JournalService(repo).entries(2026) == []
    assert codes(repo) == []


def test_credit_notes_are_judged_apart_from_invoices(repo, books):
    repo.save(
        "invoices",
        {"id": "cnnosnap", "number": "G-1", "kind": "credit_note", "status": "sent", "date": "2026-04-01", "lines": []},
    )
    repo.save(
        "invoices",
        {"id": "cnok0001", "number": "G-2", "kind": "credit_note", "status": "sent", "date": "2026-04-02", "lines": [],
         "issued": {"rates": {"19": {"net": 1, "vat": 0, "gross": 1}}}},
    )  # fmt: skip
    # no snapshot -> its own code; with a snapshot a credit note needs no `issue:` entry (it posts `credit:<id>`)
    assert codes(repo) == [("accounting.credit_note_no_snapshot", "cnnosnap")]


def test_a_payment_without_its_entry(repo, books):
    row = repo.save(
        "payments",
        {"document_id": books["inv"]["id"], "date": "2026-04-01", "amount_cents": 100, "method": "bank",
         "kind": "payment", "created_at": 0, "created_by": "u1x000"},
    )  # fmt: skip
    assert codes(repo) == [("accounting.payment_no_entry", row["id"])]


def test_an_expense_whose_bill_entry_is_missing(repo, books):
    repo.save("expenses", {**repo.get("expenses", books["exp"]["id"]), "bill_rev": 4})
    assert codes(repo) == [("accounting.expense_no_entry", books["exp"]["id"])]


def test_a_final_vat_return_with_drift(repo):
    repo.save("fiscal_years", vat_fiscal_year(2026))
    journal = JournalService(repo, clock=lambda: 1_800_000_000)
    svc = make_service(repo)
    post_sale(journal, "a:1", date(2026, 2, 5), date(2026, 2, 5))
    rid = svc.create_draft(2026, "month", 2)["id"]
    svc.finalize(rid, USER, confirm_gap=True)
    repo.save_settings({"accounting.start": "2026-01-01"})
    assert codes(repo) == []
    post_sale(JournalService(repo, clock=lambda: 1_800_000_100), "a:2", date(2026, 3, 5), date(2026, 2, 20))
    assert codes(repo) == [("accounting.vat_drift", rid)]


def test_the_sync_conflict_warning_gets_the_accounting_wording_when_accounting_is_on(repo, books):
    (repo.data_dir / "data.sync-conflict-20260101-000000-ABC.db").write_text("x")
    (found,) = IntegrityService(repo).run(("file.sync_conflict",))
    assert "one device at a time" in found.message
    assert codes(repo) == []  # no separate accounting rule: the file rule reports it once


def test_a_conflict_copy_without_accounting_has_the_plain_wording(repo):
    (repo.data_dir / "data.sync-conflict-20260101-000000-ABC.db").write_text("x")
    assert codes(repo) == []
    (found,) = IntegrityService(repo).run(("file.sync_conflict",))
    assert "one device" not in found.message


def test_a_credit_note_before_the_accounting_start_is_not_reported(repo, books):
    repo.save(
        "invoices",
        {"id": "cnold001", "number": "G-0", "kind": "credit_note", "status": "sent", "date": "2025-12-31", "lines": []},
    )
    assert codes(repo) == []


def test_a_booked_then_deleted_expense_is_no_dangling_source(repo, books):
    repo.save("users", {"id": BOSS["id"], "username": "boss", "name": "Demo Boss", "role": "boss"})
    repo.delete("expenses", books["exp"]["id"])
    assert IntegrityService(repo).run(("ref.",)) == []


def test_a_dangling_created_by_is_still_reported_but_the_system_user_is_not(repo, books):
    repo.save("users", {"id": BOSS["id"], "username": "boss", "name": "Demo Boss", "role": "boss"})
    tamper(repo, "UPDATE payments SET created_by = 'ghost0'")
    assert [(f.code, f.collection) for f in IntegrityService(repo).run(("ref.",))] == [("ref.dangling", "payments")]
    tamper(repo, "UPDATE payments SET created_by = 'system'")
    assert IntegrityService(repo).run(("ref.",)) == []


def test_a_crashing_accounting_rule_is_reported_on_the_route_rules(repo, books, monkeypatch):
    def boom(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(integrity, "ACCOUNTING_RULES", [boom])
    found = IntegrityService(repo).run(rules=integrity.ACCOUNTING_RULES)
    assert [(f.code, f.severity) for f in found] == [("check.crashed", "error")]


def test_the_route_keeps_crashed_findings(app, clients, monkeypatch):
    import permita38.api.routers.accounting as route

    def boom(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(route, "ACCOUNTING_RULES", [boom])
    body = clients["boss"].get("/api/accounting/integrity").json()
    assert [f["code"] for f in body["findings"]] == ["check.crashed"]
    assert body["summary"]["errors"] == 1


def test_the_route_runs_only_the_accounting_rules(repo, books, monkeypatch):
    seen = []
    monkeypatch.setattr(integrity, "RULES", [lambda ctx: seen.append("all") or []])
    IntegrityService(repo).run(rules=ACCOUNTING_RULES)
    assert seen == []


def test_a_replaced_original_return_reports_no_drift(repo):
    repo.save("fiscal_years", vat_fiscal_year(2026))
    journal = JournalService(repo, clock=lambda: 1_800_000_000)
    svc = make_service(repo)
    post_sale(journal, "a:1", date(2026, 2, 5), date(2026, 2, 5))
    rid = svc.create_draft(2026, "month", 2)["id"]
    svc.finalize(rid, USER, confirm_gap=True)
    svc.filed(rid, "2026-03-09", None, None)
    repo.save_settings({"accounting.start": "2026-01-01"})
    post_sale(JournalService(repo, clock=lambda: 1_800_000_100), "a:2", date(2026, 3, 5), date(2026, 2, 20))
    assert codes(repo) == [("accounting.vat_drift", rid)]
    amend = svc.create_draft(2026, "month", 2, amends_id=rid)["id"]
    svc.finalize(amend, USER, confirm_gap=True)
    assert repo.get("vat_returns", rid)["replaced_by"] == amend
    assert codes(repo) == []  # the original is superseded; the amendment contains the late sale


# ---------------------------------------------------------------- clean folders: no findings of any kind
def whole(repo):
    """Every rule on the folder (with the boss the clean folder needs)."""
    repo.save("users", {"id": BOSS["id"], "username": "boss", "name": "Demo Boss", "role": "boss"})
    return IntegrityService(repo).run()


def test_a_closed_eur_year_is_clean(repo, sales):
    init(repo, income="eur", vat="ist")
    locks = LockService(repo, today=lambda: date(2027, 1, 10))
    locks.lock_up_to(2026, 11, "manual", "u1x000")
    journal = JournalService(repo)
    closing = YearClosingService(repo, journal, locks, TaxBookingService(repo, journal))
    closing.close(2026, [], BOSS)
    assert repo.find("fiscal_years", year=2026)[0]["closed_at"] is not None
    assert whole(repo) == []


def test_a_write_off_and_its_reversal_are_clean(repo, sales):
    init(repo)
    sales.payments.today = lambda: date(2026, 12, 31)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    cn = CreditNoteService(repo, sales.postings.journal, sales.payments)
    cn.create(inv["id"], credit_kind="write_off", date="2026-03-06", reason="Lost", user=BOSS)
    sales.payments.record(inv["id"], date="2026-03-10", amount=Decimal("119.00"), kind="payment", user=BOSS)
    assert sorted(n["credit_kind"] for n in repo.find("invoices", corrects_id=inv["id"])) == [
        "write_off",
        "write_off_reversal",
    ]
    assert whole(repo) == []


def test_a_trip_is_clean(repo, books):
    project = repo.save("projects", {"name": "Demo project", "kind": "internal", "status": "active"})
    repo.save("trips", {"project_id": project["id"]})
    assert whole(repo) == []


def test_an_external_vat_return_is_clean(repo):
    repo.save("fiscal_years", vat_fiscal_year(2026))
    repo.save_settings({"accounting.start": "2026-01-01"})
    make_service(repo).create_external(2026, "month", 2, 0, 0, "2026-03-09", USER)
    assert whole(repo) == []


# ---------------------------------------------------------------- check-db and the route
def cli(repo, capsys, *args):
    repo.close()
    with pytest.raises(SystemExit) as e:
        main(["-c", str(repo.data_dir.parent / "cfg.yaml"), "--data", str(repo.data_dir), "check-db", *args])
    return e.value.code, capsys.readouterr().out


def test_check_db_prints_the_chain_findings(repo, books, capsys):
    tamper(repo, "UPDATE journal_entries SET text = 'changed' WHERE entry_no = 2")
    code, out = cli(repo, capsys, "--json")
    assert code == 1
    found = [
        (f["code"], f["severity"], f["id"]) for f in json.loads(out)["findings"] if f["code"].startswith("journal.")
    ]
    assert found == [("journal.hash", "error", "2026/2")]


def test_check_db_prints_a_sanity_warning(repo, books, capsys):
    repo.save("users", {"id": "boss01", "username": "boss", "name": "Demo Boss", "role": "boss"})
    repo.save("invoices", {"id": "invnosnap", "number": "R-9", "status": "sent", "date": "2026-04-01", "lines": []})
    code, out = cli(repo, capsys)
    assert code == 0, out
    assert "WARNING" in out and "accounting.invoice_no_snapshot" in out


def test_the_integrity_route(clients):
    r = clients["boss"].get("/api/accounting/integrity")
    assert r.status_code == 200
    assert r.json() == {"findings": [], "summary": {"errors": 0, "warnings": 0, "info": 0}}
    assert clients["accountant"].get("/api/accounting/integrity").status_code == 200
    assert clients["employee"].get("/api/accounting/integrity").status_code == 403


def test_the_integrity_route_lists_findings(app, clients):
    store = app.state.services.store
    store.save("fiscal_years", fiscal_year(2026))
    store.save_settings({"accounting.start": "2026-01-01"})
    store.save("invoices", {"id": "invnosnap", "number": "R-9", "status": "sent", "date": "2026-04-01", "lines": []})
    body = clients["boss"].get("/api/accounting/integrity").json()
    assert body["summary"] == {"errors": 0, "warnings": 1, "info": 0}
    assert body["findings"][0]["code"] == "accounting.invoice_no_snapshot"
    assert body["findings"][0]["collection"] == "invoices"
