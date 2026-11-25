# ruff: noqa: F811
"""The xlsx/csv importer under the accounting rules (docs/specs/accounting.md 10, 11, 16, 22 E19)."""

from datetime import date

import pytest

from permita38.core.errors import ServiceError
from permita38.core.service.importer import ImportService
from permita38.core.service.journal import JournalService
from permita38.core.service.numbering import NumberingService
from permita38.core.service.sales import SalesService

from .test_issue_posting import init, lock_through, repo  # noqa: F401  (fixture and helpers)

HEAD = "Nummer;Datum;Kunde;Netto;USt;Brutto;bezahlt am;Status\n"
ROWS = (
    "A-1;05.02.2026;Acme;100;19;119;;sent\n"
    "A-2;06.02.2026;Acme;200;38;238;20.02.2026;paid\n"
    "A-3;07.02.2026;Acme;50;9,50;59,50;;cancelled\n"
)
EXP_HEAD = "Datum;Lieferant;Netto;USt;Kategorie;ID\n"
EXP = "10.03.2026;Demo Supplier;100,00;19,00;software;E-1\n"


@pytest.fixture
def svc(repo):
    return ImportService(repo, numbering=NumberingService(repo, clock=lambda: date(2026, 5, 5)))


def run(svc, text, kind="invoices", **kw):
    return svc.run(text.encode(), "x.csv", kind, **kw)


def keys(repo) -> list[str]:
    return [e.event_key for e in JournalService(repo).entries(2026)]


def by_number(repo) -> dict:
    return {i["number"]: i for i in repo.list("invoices")}


def test_invoices_are_issued_paid_and_cancelled_with_accounting_running(repo, svc):
    init(repo)
    report = run(svc, HEAD + ROWS)
    assert report["applied"] is True
    inv = by_number(repo)
    assert set(inv) == {"A-1", "A-2", "A-3"}  # the file's numbers are kept (finalize only allocates a missing one)
    assert all(i["issued"] for i in inv.values())  # snapshot written
    assert sorted(keys(repo)[:2] + keys(repo)[3:]) == sorted(f"issue:{inv[n]['id']}" for n in ("A-1", "A-2", "A-3"))
    pays = repo.list("payments")
    assert len(pays) == 1 and pays[0]["document_id"] == inv["A-2"]["id"]
    assert (pays[0]["date"], pays[0]["amount_cents"], pays[0]["kind"]) == ("2026-02-20", 23800, "payment")
    assert keys(repo)[2] == f"payment:{pays[0]['id']}"
    assert len(keys(repo)) == 4
    assert inv["A-3"]["status"] == "cancelled" and inv["A-3"]["review"] == "cancelled_import"
    assert "review" not in inv["A-1"] and "review" not in inv["A-2"]
    assert svc.numbering.preview("invoice", 1)  # the counter follows the imported numbers


def test_before_initialisation_paid_stays_virtual_and_undo_removes_the_invoice(repo, svc):
    report = run(svc, HEAD + ROWS)
    inv = by_number(repo)
    assert all(i["issued"] for i in inv.values())
    assert repo.list("payments") == []  # the stored paid + paid_date is the virtual payment (spec 2.3)
    assert inv["A-2"]["status"] == "paid" and inv["A-2"]["paid_date"][:10] == "2026-02-20"
    derived = svc.sales.payments._derive(inv["A-2"])
    assert (derived.status, derived.paid_cents, derived.paid_date) == ("paid", 23800, "2026-02-20")
    assert inv["A-3"]["review"] == "cancelled_import"
    assert JournalService(repo).entries(2026) == []
    assert svc.undo(report["batch_id"])["applied"] is True
    assert repo.list("invoices") == []


def test_invoice_dated_before_the_start_gets_a_snapshot_but_no_entry(repo, svc):
    init(repo, start="2026-03-01")
    run(svc, HEAD + ROWS)
    assert all(i["issued"] for i in repo.list("invoices"))
    assert keys(repo) == []


def test_row_in_a_locked_month_is_refused_and_the_batch_writes_nothing(repo, svc):
    init(repo)
    lock_through(repo, 1)
    text = HEAD + "A-1;20.01.2026;Acme;100;19;119;;sent\nA-2;06.02.2026;Acme;200;38;238;;sent\n"
    report = run(svc, text)
    assert report["applied"] is False  # the importer is all-or-nothing: one refused row fails the batch
    assert report["rows"][0]["action"] == "error" and "locked" in report["rows"][0]["messages"][0]
    assert report["rows"][1]["action"] == "create"
    assert repo.list("invoices") == [] and keys(repo) == []


def test_credit_note_number_is_a_conflict_not_a_skip(repo, svc):
    repo.save(
        "invoices",
        dict(customer_id="c", number="A-1", date="2026-01-01", lines=[], status="sent", kind="credit_note"),
    )
    report = svc.plan((HEAD + ROWS).encode(), "x.csv", "invoices")
    assert report["rows"][0]["action"] == "error" and "credit note" in report["rows"][0]["messages"][0]


def test_expenses_post_through_the_hook(repo, svc):
    init(repo)
    run(svc, EXP_HEAD + EXP, "expenses")
    exp = repo.list("expenses")[0]
    assert exp["bill_rev"] == 1
    assert keys(repo) == [f"expense:{exp['id']}:bill:r1"]


def test_expense_overwrite_reposts_r2(repo, svc):
    init(repo)
    run(svc, EXP_HEAD + EXP, "expenses")
    exp = repo.list("expenses")[0]
    changed = EXP_HEAD + "10.03.2026;Demo Supplier;200,00;38,00;software;E-1\n"
    report = run(svc, changed, "expenses", options={"on_duplicate": "overwrite"})
    assert report["applied"] is True and report["counts"]["overwrite"] == 1
    again = repo.list("expenses")
    assert len(again) == 1 and again[0]["id"] == exp["id"] and again[0]["bill_rev"] == 2
    assert keys(repo) == [
        f"expense:{exp['id']}:bill:r1",
        f"expense:{exp['id']}:bill:r1:rev",
        f"expense:{exp['id']}:bill:r2",
    ]


def test_undo_of_an_expense_only_batch_posts_reversals_and_removes_the_rows(repo, svc):
    init(repo)
    exp = run(svc, EXP_HEAD + EXP, "expenses")
    eid = repo.list("expenses")[0]["id"]
    assert svc.undo(exp["batch_id"])["applied"] is True
    assert repo.list("expenses") == []
    assert f"expense:{eid}:bill:r1:rev" in keys(repo)
    assert sum(ln.amount for e in JournalService(repo).entries(2026) for ln in e.lines) == 0


def test_undo_of_a_booked_invoice_is_refused_and_nothing_changes(repo, svc):
    init(repo)
    report = run(svc, HEAD + ROWS)  # A-2 is paid: payment row and entry exist
    assert len(repo.list("payments")) == 1
    before = keys(repo)
    with pytest.raises(ServiceError) as e:
        svc.undo(report["batch_id"])
    assert e.value.status == 409 and "issue credit notes instead" in e.value.message
    assert len(repo.list("invoices")) == 3 and len(repo.list("payments")) == 1 and keys(repo) == before
    assert all(b["status"] == "imported" for b in svc.batches())


def test_undo_after_a_lock_is_409_and_nothing_changes(repo, svc):
    init(repo)
    report = run(svc, HEAD + "A-1;05.02.2026;Acme;100;19;119;;sent\n")
    exp = run(svc, EXP_HEAD + EXP, "expenses")
    lock_through(repo, 3)
    before = keys(repo)
    for batch in (report["batch_id"], exp["batch_id"]):
        with pytest.raises(ServiceError) as e:
            svc.undo(batch)
        assert e.value.status == 409
    assert len(repo.list("invoices")) == 1 and len(repo.list("expenses")) == 1
    assert keys(repo) == before
    assert all(b["status"] == "imported" for b in svc.batches())


def test_row_in_a_closed_fiscal_year_is_refused(repo, svc):
    init(repo)
    fy = repo.find("fiscal_years", year=2026)[0]
    repo.save("fiscal_years", {**fy, "closed_at": 5, "closed_by": "u1x000"})
    report = run(svc, HEAD + "A-1;05.02.2026;Acme;100;19;119;;sent\n")
    assert report["applied"] is False and "locked" in report["rows"][0]["messages"][0]
    assert repo.list("invoices") == [] and keys(repo) == []


def test_a_failure_in_the_middle_of_the_batch_rolls_everything_back(repo, svc, monkeypatch):
    init(repo)
    counters = repo.settings()
    next_before = svc.numbering.preview("invoice", 1)
    real = SalesService.finalize
    calls = []

    def flaky(self, *a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(self, *a, **k)

    monkeypatch.setattr(SalesService, "finalize", flaky)
    with pytest.raises(RuntimeError):
        run(svc, HEAD + ROWS)
    assert len(calls) == 2
    assert repo.list("invoices") == [] and repo.list("customers") == [] and repo.list("import_batches") == []
    assert keys(repo) == [] and repo.list("payments") == []
    assert repo.settings() == counters and svc.numbering.preview("invoice", 1) == next_before


def test_invoice_overwrite_is_refused_once_the_import_is_posted(repo, svc):
    init(repo)
    run(svc, HEAD + "A-1;05.02.2026;Acme;100;19;119;;sent\n")
    again = HEAD + "A-1;05.02.2026;Acme;200;38;238;;sent\n"
    report = run(svc, again, options={"on_duplicate": "overwrite"})
    assert report["applied"] is False
    assert "posted to the journal" in " ".join(report["rows"][0]["messages"])
    assert len(repo.list("invoices")) == 1 and len(keys(repo)) == 1


def test_a_user_edited_expense_is_skipped_not_overwritten(repo, svc):
    init(repo)
    run(svc, EXP_HEAD + EXP, "expenses")
    exp = repo.list("expenses")[0]
    repo.save("expenses", {**exp, "description": "edited by the user"})
    changed = EXP_HEAD + "10.03.2026;Demo Supplier;200,00;38,00;software;E-1\n"
    report = run(svc, changed, "expenses", options={"on_duplicate": "overwrite"})
    assert report["rows"][0]["action"] == "skip" and report["counts"]["overwrite"] == 0
    stored = repo.list("expenses")
    assert len(stored) == 1 and stored[0]["description"] == "edited by the user" and stored[0]["bill_rev"] == 1
    assert len(keys(repo)) == 1


def test_expense_overwrite_into_a_locked_month_is_a_plan_error(repo, svc):
    init(repo)
    run(svc, EXP_HEAD + EXP, "expenses")
    lock_through(repo, 3)
    before = keys(repo)
    changed = EXP_HEAD + "10.03.2026;Demo Supplier;200,00;38,00;software;E-1\n"
    plan = svc.plan(changed.encode(), "x.csv", "expenses", options={"on_duplicate": "overwrite"})
    assert plan["rows"][0]["action"] == "error" and "locked" in " ".join(plan["rows"][0]["messages"])
    report = run(svc, changed, "expenses", options={"on_duplicate": "overwrite"})
    assert report["applied"] is False and keys(repo) == before and repo.list("expenses")[0]["bill_rev"] == 1


def test_a_number_in_the_credit_note_pattern_is_a_row_error_but_other_numbers_import(repo, svc):
    init(repo)
    row = "RK-2026-0001;05.02.2026;Acme;100;19;119;;sent\n"
    plan = svc.plan((HEAD + row).encode(), "x.csv", "invoices")
    assert plan["rows"][0]["action"] == "error" and "credit note" in " ".join(plan["rows"][0]["messages"])
    report = run(svc, HEAD + row)
    assert report["applied"] is False and not repo.list("invoices") and keys(repo) == []
    run(svc, HEAD + "RE-2026-0001;05.02.2026;Acme;100;19;119;;sent\n")
    assert "RE-2026-0001" in by_number(repo)


@pytest.mark.parametrize(
    "row, word",
    [
        ("A-1;05.02.2026;Acme;=1+1;19;119;;sent\n", "not an amount"),
        ("A-1;05.02.2026;Acme;abc;19;119;;sent\n", "not an amount"),
        ("A-1;05.02.2026;" + "x" * 5000 + ";100;19;119;;sent\n", "longer than"),
    ],
)
def test_formula_wrong_type_and_huge_cells_are_row_errors_and_nothing_is_written(repo, svc, row, word):
    init(repo)
    report = run(svc, HEAD + row)
    assert report["applied"] is False
    assert report["rows"][0]["action"] == "error" and word in " ".join(report["rows"][0]["messages"])
    assert repo.list("invoices") == [] and repo.list("customers") == [] and keys(repo) == []
