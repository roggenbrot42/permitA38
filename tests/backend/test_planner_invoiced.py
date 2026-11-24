# ruff: noqa: F811
"""Planner "invoiced" nets credit notes like finance() (credit-notes-payments.md 4): a partial credit note lowers it,
a storno'd invoice and its storno count 0."""

from decimal import Decimal

from .test_planner_load import CUST, P1, TODAY, contract, load, repo  # noqa: F401  (fixture and helpers)


def invoice(repo, iid, price, day="2026-11-10"):
    repo.save(
        "invoices",
        {"id": iid, "status": "sent", "date": day, "project_id": P1, "customer_id": CUST, "vat_rate": 19,
         "lines": [{"description": "x", "qty": 1, "price": price}]},
    )  # fmt: skip


def note(repo, nid, corrects, price, kind, gross_cents, day="2026-11-12"):
    repo.save(
        "invoices",
        {"id": nid, "status": "sent", "kind": "credit_note", "credit_kind": kind, "corrects_id": corrects,
         "date": day, "project_id": P1, "customer_id": CUST, "vat_rate": 19,
         "issued": {"gross": gross_cents}, "lines": [{"description": "x", "qty": 1, "price": price}]},
    )  # fmt: skip


def test_a_partial_credit_note_lowers_invoiced(repo):
    contract(repo, "contr00001", start="2026-11-01", end="2026-11-30")
    invoice(repo, "inv0000001", 100)
    note(repo, "crn0000001", "inv0000001", 40, "partial", 4760)
    assert load(repo, TODAY).invoiced == {"contr00001": Decimal("60.00")}


def test_a_storno_invoice_and_its_credit_note_count_zero(repo):
    contract(repo, "contr00001", start="2026-11-01", end="2026-11-30")
    invoice(repo, "inv0000001", 100)
    note(repo, "crn0000001", "inv0000001", 100, "storno", 11900)
    invoice(repo, "inv0000002", 50)
    assert load(repo, TODAY).invoiced == {"contr00001": Decimal("50.00")}


def test_a_credit_note_of_an_uncounted_invoice_is_ignored(repo):
    contract(repo, "contr00001", start="2026-11-01", end="2026-11-30")
    invoice(repo, "inv0000001", 100, day="2026-09-10")  # no contract contains it
    note(repo, "crn0000001", "inv0000001", 40, "partial", 4760)
    assert load(repo, TODAY).invoiced == {}
