# ruff: noqa: F811
"""The xlsx/csv importer under the accounting rules (docs/specs/accounting.md 10, 11, 16, 22 E19)."""

from datetime import date

import pytest

from permita38.core.errors import ServiceError
from permita38.core.service.importer import ImportService
from permita38.core.service.journal import JournalService
from permita38.core.service.numbering import NumberingService

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


def test_before_initialisation_nothing_posts_but_snapshots_and_payments_are_written(repo, svc):
    run(svc, HEAD + ROWS)
    inv = by_number(repo)
    assert all(i["issued"] for i in inv.values())
    assert len(repo.list("payments")) == 1
    assert repo.list("payments")[0]["amount_cents"] == 23800
    assert inv["A-3"]["review"] == "cancelled_import"
    assert JournalService(repo).entries(2026) == []


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


def test_undo_while_open_posts_reversals_and_removes_the_rows(repo, svc):
    init(repo)
    report = run(svc, HEAD + "A-1;05.02.2026;Acme;100;19;119;;sent\n")
    exp = run(svc, EXP_HEAD + EXP, "expenses")
    inv = repo.list("invoices")[0]
    eid = repo.list("expenses")[0]["id"]
    assert svc.undo(report["batch_id"])["applied"] is True
    assert svc.undo(exp["batch_id"])["applied"] is True
    assert repo.list("invoices") == [] and repo.list("expenses") == []
    posted = keys(repo)
    assert f"issue:{inv['id']}:rev" in posted and f"expense:{eid}:bill:r1:rev" in posted
    rows = JournalService(repo).entries(2026)
    assert sum(ln.amount for e in rows for ln in e.lines) == 0


def test_undo_of_a_paid_invoice_is_refused_because_payments_are_append_only(repo, svc):
    init(repo)
    report = run(svc, HEAD + ROWS)
    before = len(keys(repo))
    with pytest.raises(ServiceError) as e:
        svc.undo(report["batch_id"])
    assert e.value.status == 409
    assert len(repo.list("invoices")) == 3 and len(keys(repo)) == before


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
