# ruff: noqa: F811
"""Credit notes, storno and partial (docs/specs/credit-notes-payments.md 2.3, 4, P4, P5; accounting.md 7, 9.2, 11).

The expected journal lines are read from the hand-computed oracle ``fixtures/accounting_oracle.json`` (never edited).
Write-off and discount credit notes (A9, A10, A13, P3) are another work package.
"""

from decimal import Decimal
from typing import Any

import pytest

from permita38.core.errors import ServiceError
from permita38.core.service.credit_notes import CreditNoteService

from .test_invoice_freeze import cust, issue, store  # noqa: F401  (fixtures and helpers)
from .test_issue_posting import (  # noqa: F401  (fixtures and helpers)
    CASES,
    ORACLE,
    draft,
    entries,
    entry_rows,
    events,
    fiscal_year,
    init,
    invoice_from_event,
    line,
    lock_through,
    oracle_rows,
    repo,
    sales,
)
from .test_payments import BOSS, TODAY, apply_payment, pay  # noqa: F401  (fixtures and helpers)
from .test_rbac import clients  # noqa: F401  (fixture)


@pytest.fixture
def cn(repo, sales, pay):
    return CreditNoteService(repo, sales.postings.journal, pay)


def money(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f}"


def apply_credit(cn, inv_id: str, ev: dict[str, Any]) -> dict[str, Any]:
    kind = ev["credit_kind"]
    amounts = None if kind == "storno" else {r: money(v["net"]) for r, v in ev["rates"].items()}
    return cn.create(inv_id, credit_kind=kind, date=ev["date"], reason="Demo", amounts=amounts, user=BOSS)


def run_events(repo, sales, pay, cn, case_id: str):
    case = CASES[case_id]
    b = case["bases"]
    init(repo, income=b["income_basis"], vat=b["vat_basis"])
    evs = events(case)
    inv = invoice_from_event(repo, evs[0])
    sales.finalize("invoices", inv["id"])
    notes = []
    for ev in evs[1:]:
        if ev["type"] == "payment":
            apply_payment(pay, inv["id"], ev)
        else:
            notes.append(apply_credit(cn, inv["id"], ev))
    return case, evs, inv, notes


# ---------------------------------------------------------------- oracle journal cases (the credit-note events)
@pytest.mark.parametrize(
    "case_id",
    [
        "A3-accrual-soll",
        "A4-eur-ist",
        "A11-credit-then-refunds-eur-ist",
        "A12-storno-after-payment-soll",
        "A18-credit-moves-cash-accrual-ist",
        "A18-credit-moves-cash-eur-soll",
    ],
)
def test_credit_events_post_the_oracle_entries(repo, sales, pay, cn, case_id):
    case, evs, inv, notes = run_events(repo, sales, pay, cn, case_id)
    wanted = [e for e in case["entries"] if e["event"] in range(len(evs))]
    posted = entries(repo)
    assert len(posted) == len(wanted), [e.event_key for e in posted]
    for got, exp in zip(posted, wanted, strict=True):
        assert sorted(entry_rows(got), key=repr) == sorted(oracle_rows(exp), key=repr), exp[
            "event"
        ]  # line order is free
        assert got.booking_date == exp["booking_date"]
    keys = {e.event_key for e in posted}
    credit_events = [e for e in evs if e["type"] == "credit_note"]
    for ev, note in zip(credit_events, notes, strict=True):
        index = evs.index(ev)
        assert (f"credit:{note['id']}" in keys) == (index not in case.get("no_entry_events", []))
        assert note["issued"]["gross"] == money(sum(v["net"] + v["vat"] for v in ev["rates"].values()))
        assert {r: (g["net"], g["vat"]) for r, g in note["issued"]["rates"].items()} == {
            r: (money(v["net"]), money(v["vat"])) for r, v in ev["rates"].items()
        }


def test_credit_entry_source_fields(repo, sales, pay, cn):
    _case, _evs, inv, (note,) = run_events(repo, sales, pay, cn, "A3-accrual-soll")
    entry = entries(repo)[-1]
    assert entry.event_key == f"credit:{note['id']}" and entry.event == "credit"
    assert entry.source_coll == "invoices" and entry.source_id == note["id"] and entry.source_number == note["number"]
    assert entry.document_date == "2026-03-12"


# ---------------------------------------------------------------- the stored credit note
def two_group_invoice(repo, sales, day="2026-03-02"):
    inv = draft(repo, day, [line("1000.00", "19"), line("500.00", "7")])
    return sales.finalize("invoices", inv["id"])


def test_storno_copies_the_lines_and_credits_everything(repo, sales, cn):
    init(repo)
    inv = two_group_invoice(repo, sales)
    note = cn.create(inv["id"], credit_kind="storno", date="2026-03-10", reason="Wrong customer", user=BOSS)
    assert note["kind"] == "credit_note" and note["credit_kind"] == "storno" and note["corrects_id"] == inv["id"]
    assert note["number"] == "RK-2026-0001" and note["status"] == "sent" and note["reason"] == "Wrong customer"
    assert note["customer_id"] == inv["customer_id"] and note["date"] == "2026-03-10"
    assert [(ln["qty"], ln["price"], ln["vat"]) for ln in note["lines"]] == [
        (ln["qty"], ln["price"], ln["vat"]) for ln in inv["lines"]
    ]
    assert note["issued"]["gross"] == "1725.00"
    assert note["issued"]["rates"] == {
        "19": {"net": "1000.00", "vat": "190.00", "gross": "1190.00"},
        "7": {"net": "500.00", "vat": "35.00", "gross": "535.00"},
    }
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert (view["status"], view["credited_amount"], view["open_amount"]) == ("cancelled", "1725.00", "0.00")
    assert view["credit_notes"] == [
        {"id": note["id"], "number": "RK-2026-0001", "date": "2026-03-10", "credit_kind": "storno", "gross": "1725.00"}
    ]


def test_credit_note_inherits_treatment_customer_and_project(repo, sales, cn):
    init(repo)
    project = repo.save("projects", dict(name="Demo project", kind="internal", status="active"))
    inv = draft(repo, "2026-03-02", [line("100.00", None)], tax_treatment="eu_b2b_service", project_id=project["id"])
    sales.finalize("invoices", inv["id"])
    note = cn.create(inv["id"], credit_kind="storno", date="2026-03-10", reason="Demo", user=BOSS)
    stored = repo.get("invoices", note["id"])
    assert stored["tax_treatment"] == "eu_b2b_service" and stored["project_id"] == project["id"]
    assert stored["customer_id"] == inv["customer_id"] and stored["issued"]["treatment"] == "eu_b2b_service"
    assert stored["issued"]["customer"] == repo.get("invoices", inv["id"])["issued"]["customer"]


def test_p4_storno_after_a_partial_credits_only_the_rest(repo, sales, cn):
    case = next(c for c in ORACLE["payment_cases"] if c["id"] == "P4")
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("2000.00")])["id"])
    part = case["partial_credit"]["19"]
    first = cn.create(
        inv["id"], credit_kind="partial", date="2026-03-05", reason="Discount", amounts={"19": money(part["net"])}
    )
    assert first["number"] == "RK-2026-0001" and first["issued"]["gross"] == money(part["net"] + part["vat"])
    assert [(ln["description"], ln["price"], ln["vat"]) for ln in first["lines"]] == [("Discount", "500.00", "19")]
    second = cn.create(inv["id"], credit_kind="storno", date="2026-03-06", reason="Cancelled")
    rest = case["storno_credits"]["19"]
    assert second["number"] == "RK-2026-0002"
    assert second["issued"]["rates"]["19"] == {
        "net": money(rest["net"]),
        "vat": money(rest["vat"]),
        "gross": money(rest["net"] + rest["vat"]),
    }
    assert [(ln["price"], ln["vat"]) for ln in second["lines"]] == [("1500.00", "19")]
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert view["status"] == case["status"] and view["open_amount"] == "0.00" and view["credited_amount"] == "2380.00"
    with pytest.raises(ServiceError) as exc:  # a second storno has nothing left
        cn.create(inv["id"], credit_kind="storno", date="2026-03-07", reason="Again")
    assert exc.value.status == 422
    assert [n["number"] for n in cn.list(inv["id"])] == ["RK-2026-0001", "RK-2026-0002"]


def test_partial_per_rate_group_and_the_vat_residue(repo, sales, cn):
    init(repo)
    inv = two_group_invoice(repo, sales)
    note = cn.create(
        inv["id"], credit_kind="partial", date="2026-03-10", reason="Part", amounts={"7": "100.00", "19": "250.50"}
    )
    assert note["issued"]["rates"] == {
        "19": {"net": "250.50", "vat": "47.60", "gross": "298.10"},  # 47.595 half up
        "7": {"net": "100.00", "vat": "7.00", "gross": "107.00"},
    }
    assert [(ln["price"], ln["vat"]) for ln in note["lines"]] == [("250.50", "19"), ("100.00", "7")]
    rest = cn.create(inv["id"], credit_kind="partial", date="2026-03-11", reason="Rest", amounts={"19": "749.50"})
    assert rest["issued"]["rates"]["19"] == {"net": "749.50", "vat": "142.40", "gross": "891.90"}  # 190.00 - 47.60
    with pytest.raises(ServiceError) as exc:
        cn.create(inv["id"], credit_kind="partial", date="2026-03-12", reason="More", amounts={"19": "0.01"})
    assert exc.value.status == 422


def test_p5_over_credit_is_422_per_rate_group(repo, sales, cn):
    case = next(c for c in ORACLE["payment_cases"] if c["id"] == "P5")
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("2000.00")])["id"])
    with pytest.raises(ServiceError) as exc:
        cn.create(inv["id"], credit_kind="partial", date="2026-03-05", reason="x", amounts={"19": "2100.00"})
    assert exc.value.status == case["expect_status_code"] and "2000.00" in exc.value.message
    two = two_group_invoice(repo, sales)
    for amounts in ({"7": "500.01"}, {"19": "100.00", "7": "500.01"}):  # one group over, the other fine
        with pytest.raises(ServiceError) as exc:
            cn.create(two["id"], credit_kind="partial", date="2026-03-05", reason="x", amounts=amounts)
        assert exc.value.status == 422
    for amounts in ({"10": "1.00"}, {"19": "1.001"}):  # a rate the invoice does not have, sub-cent
        with pytest.raises(ServiceError) as exc:
            cn.create(two["id"], credit_kind="partial", date="2026-03-05", reason="x", amounts=amounts)
        assert exc.value.status == 422
    assert cn.list(two["id"]) == [] and cn.list(inv["id"]) == []  # nothing stored, no number used
    ok = cn.create(two["id"], credit_kind="partial", date="2026-03-05", reason="x", amounts={"7": "500.00"})
    assert ok["number"] == "RK-2026-0001"


def test_a_locked_month_date_is_409_and_gives_the_number_back(repo, sales, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-01-05", [line("100.00")])["id"])
    lock_through(repo, 3)
    with pytest.raises(ServiceError) as exc:
        cn.create(inv["id"], credit_kind="storno", date="2026-03-31", reason="x", user=BOSS)
    assert exc.value.status == 409 and cn.list(inv["id"]) == [] and len(entries(repo)) == 1
    ok = cn.create(inv["id"], credit_kind="storno", date="2026-04-01", reason="x", user=BOSS)
    assert ok["number"] == "RK-2026-0001"


def test_nothing_posts_before_the_initialisation_but_the_credit_note_counts(repo, sales, cn):
    repo.save("fiscal_years", fiscal_year(2026))  # no accounting.start
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    note = cn.create(inv["id"], credit_kind="partial", date="2026-03-05", reason="x", amounts={"19": "40.00"})
    assert entries(repo) == [] and note["number"] == "RK-2026-0001"
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert (view["credited_amount"], view["open_amount"], view["status"]) == ("47.60", "71.40", "sent")


def test_the_numbers_follow_the_settings_with_defaults(repo, sales, cn):
    init(repo)
    inv = two_group_invoice(repo, sales)
    other = two_group_invoice(repo, sales, "2027-01-04")
    cn.create(inv["id"], credit_kind="partial", date="2026-03-05", reason="x", amounts={"7": "1.00"})
    repo.save_settings({"credit_note_pattern": "G-{year}/{n:03d}", "credit_note_reset": "never"})
    a = cn.create(inv["id"], credit_kind="partial", date="2026-03-06", reason="x", amounts={"7": "1.00"})
    b = cn.create(other["id"], credit_kind="partial", date="2027-01-06", reason="x", amounts={"7": "1.00"})
    assert (a["number"], b["number"]) == ("G-2026/001", "G-2027/002")
    assert repo.find("number_sequences", doc_type="credit_note")  # its own counter group, apart from the invoices


def test_a_legacy_paid_invoice_is_materialised_before_the_credit_note(repo, sales, cn):
    init(repo, income="accrual", vat="soll")
    inv = sales.finalize("invoices", draft(repo, "2026-02-01", [line("100.00")])["id"])
    repo.save("invoices", {**repo.get("invoices", inv["id"]), "status": "paid", "paid_date": "2026-02-03"})
    cn.create(inv["id"], credit_kind="storno", date="2026-03-02", reason="x", user=BOSS)
    (row,) = repo.find("payments", document_id=inv["id"])
    assert row["note"] == "migrated" and row["amount_cents"] == 11900
    keys = [e.event_key for e in entries(repo)]
    assert len(keys) == 3 and keys[1] == f"payment:{row['id']}" and keys[2].startswith("credit:")
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert view["status"] == "cancelled" and view["open_amount"] == "-119.00"  # owed back to the customer


def test_only_issued_invoices_and_not_credit_notes_and_not_write_off(repo, sales, cn):
    init(repo)
    d = draft(repo, "2026-03-02", [line("100.00")])
    with pytest.raises(ServiceError) as exc:
        cn.create(d["id"], credit_kind="storno", date="2026-03-05", reason="x")
    assert exc.value.status == 409
    inv = sales.finalize("invoices", d["id"])
    note = cn.create(inv["id"], credit_kind="storno", date="2026-03-05", reason="x")
    with pytest.raises(ServiceError) as exc:
        cn.create(note["id"], credit_kind="storno", date="2026-03-06", reason="x")
    assert exc.value.status == 422
    with pytest.raises(ServiceError) as exc:
        cn.create("nope", credit_kind="storno", date="2026-03-06", reason="x")
    assert exc.value.status == 404
    with pytest.raises(ServiceError) as exc:
        cn.create(inv["id"], credit_kind="write_off", date="2026-03-06", reason="x")
    assert exc.value.status == 501


# ---------------------------------------------------------------- API
def url(inv) -> str:
    return f"/api/invoices/{inv['id']}/credit-notes"


def test_api_create_list_and_derived_invoice(boss_client, cust):
    inv = issue(boss_client, cust)
    r = boss_client.post(
        url(inv), json={"credit_kind": "partial", "date": "2026-02-05", "reason": "Part", "amounts": {"19": "100.00"}}
    )
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["kind"] == "credit_note" and note["issued"]["gross"] == "119.00"
    listed = boss_client.get(url(inv)).json()
    assert [n["id"] for n in listed] == [note["id"]]
    got = boss_client.get(f"/api/invoices/{inv['id']}").json()
    assert got["credited_amount"] == "119.00" and got["open_amount"] == "1071.00"
    s = boss_client.post(url(inv), json={"credit_kind": "storno", "date": "2026-02-06", "reason": "Cancel"})
    assert s.status_code == 201 and s.json()["issued"]["gross"] == "1071.00"
    after = boss_client.get(f"/api/invoices/{inv['id']}").json()
    assert after["status"] == "cancelled" and after["open_amount"] == "0.00"
    assert boss_client.delete(f"/api/invoices/{note['id']}").status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"credit_kind": "storno", "date": "2026-02-05", "reason": ""},
        {"credit_kind": "storno", "date": "2026-02-05", "reason": "x" * 501},
        {"credit_kind": "storno", "date": "2026-02-05"},
        {"credit_kind": "partial", "date": "2026-02-05", "reason": "x"},
        {"credit_kind": "partial", "date": "2026-02-05", "reason": "x", "amounts": {"19": "0"}},
        {"credit_kind": "discount", "date": "2026-02-05", "reason": "x"},
        {"credit_kind": "storno", "date": "nope", "reason": "x"},
    ],
)
def test_api_validation(boss_client, cust, body):
    inv = issue(boss_client, cust)
    assert boss_client.post(url(inv), json=body).status_code == 422


def test_api_errors(boss_client, cust):
    inv = issue(boss_client, cust)
    over = boss_client.post(
        url(inv), json={"credit_kind": "partial", "date": "2026-02-05", "reason": "x", "amounts": {"19": "1000.01"}}
    )
    assert over.status_code == 422
    assert (
        boss_client.post(
            url({"id": "nope"}), json={"credit_kind": "storno", "date": "2026-02-05", "reason": "x"}
        ).status_code
        == 404
    )
    assert boss_client.get(url({"id": "nope"})).status_code == 404


def test_api_rights(clients, cust, boss_client):
    inv = issue(boss_client, cust)
    body = {"credit_kind": "storno", "date": "2026-02-05", "reason": "x"}
    assert clients["employee"].post(url(inv), json=body).status_code == 403
    assert clients["accountant"].get(url(inv)).status_code == 200
    r = clients["accountant"].post(url(inv), json=body)
    assert r.status_code == 201, r.text
    assert boss_client.get(url(inv)).json()[0]["id"] == r.json()["id"]
