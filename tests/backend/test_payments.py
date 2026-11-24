# ruff: noqa: F811
"""Payments, refunds and reversals (docs/specs/credit-notes-payments.md 2.2, 2.3, 3; accounting.md 9.2, 11).

The expected journal lines are read from the hand-computed oracle ``fixtures/accounting_oracle.json`` (never edited);
the P cases use its literal figures. Settle-discount and credit notes are other work packages.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from permita38.core.errors import ServiceError

from .test_invoice_freeze import cust, issue, make_invoice, put, store  # noqa: F401  (fixtures and helpers)
from .test_issue_posting import (  # noqa: F401  (fixtures and helpers)
    CASES,
    ORACLE,
    customer,
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
from .test_rbac import clients  # noqa: F401  (fixture)

TODAY = date(2026, 12, 31)
BOSS = {"id": "u1x000", "role": "boss"}


@pytest.fixture
def pay(sales):
    sales.payments.today = lambda: TODAY
    return sales.payments


def amount(cents: int) -> Decimal:
    return Decimal(cents) / 100


def apply_payment(pay, inv_id: str, ev: dict[str, Any]) -> dict[str, Any]:
    cents = ev["amount"]
    return pay.record(
        inv_id,
        date=ev["date"],
        amount=amount(abs(cents)),
        kind="payment" if cents > 0 else "refund",
        user=BOSS,
    )


def run_case(repo, sales, pay, case_id: str):
    case = CASES[case_id]
    b = case["bases"]
    init(repo, income=b["income_basis"], vat=b["vat_basis"])
    evs = events(case)
    inv = invoice_from_event(repo, evs[0])
    sales.finalize("invoices", inv["id"])
    return case, evs, inv


def compare(repo, case, inv, indexes: list[int]):
    """The posted entries equal the oracle entries of the given events, in order."""
    wanted = [e for e in case["entries"] if e["event"] in indexes]
    posted = entries(repo)
    assert len(posted) == len(wanted), [e.event_key for e in posted]
    for got, exp in zip(posted, wanted, strict=True):
        assert entry_rows(got) == oracle_rows(exp), exp["event"]
        assert got.booking_date == exp["booking_date"]
        if "document_date" in exp:
            assert got.document_date == exp["document_date"]


# ---------------------------------------------------------------- oracle journal cases (the payment events)
@pytest.mark.parametrize(
    "case_id",
    [
        "A1-accrual-soll",
        "A1-accrual-ist",
        "A1-eur-soll",
        "A1-eur-ist",
        "A2-eur-ist",
        "A8-overpayment",
    ],
)
def test_payment_events_post_the_oracle_entries(repo, sales, pay, case_id):
    case, evs, inv = run_case(repo, sales, pay, case_id)
    later = [i for i, e in enumerate(evs) if e["type"] == "payment"]
    for i in later:
        apply_payment(pay, inv["id"], evs[i])
    compare(repo, case, inv, [0, *later])
    keys = [e.event_key for e in entries(repo)]
    rows = repo.find("payments", document_id=inv["id"])
    assert all(f"payment:{r['id']}" in keys for r in rows) and len(rows) == len(later)


def test_a16_late_ist_payment_after_lock_drifts(repo, sales, pay):
    case, evs, inv = run_case(repo, sales, pay, "A16-late-ist-payment-after-lock")
    lock_through(repo, 1)
    result = apply_payment(pay, inv["id"], evs[2])
    compare(repo, case, inv, [0, 2])
    assert [w["code"] for w in result["warnings"]] == ["vat_drift"]
    (entry,) = [e for e in entries(repo) if e.event_key.startswith("payment:")]
    assert entry.warnings and entry.booking_date == "2026-02-01" and entry.document_date == "2026-01-30"


def test_soll_payment_in_a_locked_month_books_into_the_open_month_without_drift(repo, sales, pay):
    init(repo, income="accrual", vat="soll")
    inv = sales.finalize("invoices", draft(repo, "2026-01-05", [line("1000.00")])["id"])
    lock_through(repo, 1)
    result = pay.record(inv["id"], date="2026-01-30", amount=Decimal("1190.00"), user=BOSS)
    assert result["warnings"] == []
    entry = entries(repo)[-1]
    assert entry.booking_date == "2026-02-01" and not entry.warnings
    assert [(ln.role, cents_of(ln)) for ln in entry.lines] == [("bank", 119000), ("receivables", -119000)]


def cents_of(ln) -> int:
    return int(Decimal(str(ln.amount)) * 100)


def test_a18_payment_before_the_credit_note(repo, sales, pay):
    case, evs, inv = run_case(repo, sales, pay, "A18-credit-moves-cash-accrual-ist")
    apply_payment(pay, inv["id"], evs[1])
    compare(repo, case, inv, [0, 1])


def credit_note(repo, inv: dict, rates: dict[str, tuple[int, int]], day: str, kind: str = "partial") -> dict:
    """A stored credit note with its snapshot (the credit-note service is another work package)."""
    snap = {r: {"net": n, "vat": v, "gross": n + v} for r, (n, v) in rates.items()}
    return repo.save(
        "invoices",
        dict(
            kind="credit_note", credit_kind=kind, corrects_id=inv["id"], date=day, status="sent", number="RK-1",
            customer_id=inv["customer_id"], lines=[], issued={"rates": snap, "gross": sum(n + v for n, v in rates.values())},
        ),
    )  # fmt: skip


def test_a11_refunds_above_the_current_gross_eur_ist(repo, sales, pay):
    case, evs, inv = run_case(repo, sales, pay, "A11-credit-then-refunds-eur-ist")
    apply_payment(pay, inv["id"], evs[1])
    credit_note(repo, inv, {"19": (50000, 9500)}, "2026-04-01")
    apply_payment(pay, inv["id"], evs[3])
    apply_payment(pay, inv["id"], evs[4])
    compare(repo, case, inv, [1, 3, 4])


# ---------------------------------------------------------------- reversal, refunds, overpayment, nothing before init
def negated(rows):
    return [(r[0], -r[1], r[2], r[3], None if r[4] is None else -r[4], *r[5:]) for r in rows]


def test_reversal_posts_the_opposite_delta(repo, sales, pay):
    init(repo, income="accrual", vat="ist")
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("1000.00")])["id"])
    first = pay.record(inv["id"], date="2026-03-05", amount=Decimal("1190.00"), user=BOSS)
    out = pay.reverse(first["payment"]["id"], date="2026-03-05", note="entered twice", user=BOSS)
    assert out["payment"]["kind"] == "reversal" and out["payment"]["amount"] == "-1190.00"
    assert out["payment"]["reverses_id"] == first["payment"]["id"] and out["payment"]["note"] == "entered twice"
    _issue, paid, back = entries(repo)
    assert back.event_key == f"payment:{out['payment']['id']}" and paid.event_key == f"payment:{first['payment']['id']}"
    assert sorted(entry_rows(back)) == sorted(negated(entry_rows(paid)))
    derived = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert derived["open_amount"] == "1190.00" and derived["status"] == "sent" and derived["paid_date"] is None


def test_a_reversal_cannot_be_reversed_and_a_payment_only_once(repo, sales, pay):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    p = pay.record(inv["id"], date="2026-03-05", amount=Decimal("50.00"), user=BOSS)["payment"]
    rev = pay.reverse(p["id"], user=BOSS)["payment"]
    assert rev["date"] == "2026-03-05"  # defaults to the payment date
    for target in (rev["id"], p["id"]):
        with pytest.raises(ServiceError) as exc:
            pay.reverse(target, user=BOSS)
        assert exc.value.status == 409
    with pytest.raises(ServiceError) as exc:
        pay.reverse("nope", user=BOSS)
    assert exc.value.status == 404


def test_reversal_cannot_leave_negative_cash(repo, sales, pay):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    p = pay.record(inv["id"], date="2026-03-05", amount=Decimal("100.00"), user=BOSS)["payment"]
    pay.record(inv["id"], date="2026-03-06", amount=Decimal("40.00"), kind="refund", user=BOSS)
    with pytest.raises(ServiceError) as exc:
        pay.reverse(p["id"], user=BOSS)  # 60.00 kept < 100.00
    assert exc.value.status == 422


def test_refund_is_stored_negative_and_may_not_exceed_the_cash_kept(repo, sales, pay):
    init(repo, income="accrual", vat="ist")
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    with pytest.raises(ServiceError) as exc:  # nothing paid yet
        pay.record(inv["id"], date="2026-03-05", amount=Decimal("0.01"), kind="refund", user=BOSS)
    assert exc.value.status == 422
    pay.record(inv["id"], date="2026-03-05", amount=Decimal("50.00"), user=BOSS)
    with pytest.raises(ServiceError) as exc:
        pay.record(inv["id"], date="2026-03-06", amount=Decimal("50.01"), kind="refund", user=BOSS)
    assert exc.value.status == 422 and "50.00" in exc.value.message
    r = pay.record(inv["id"], date="2026-03-06", amount=Decimal("50.00"), kind="refund", user=BOSS)
    assert r["payment"]["amount"] == "-50.00" and r["payment"]["kind"] == "refund"
    assert sorted(x["amount_cents"] for x in repo.find("payments", document_id=inv["id"])) == [-5000, 5000]
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert view["paid_amount"] == "0.00" and view["open_amount"] == "119.00"


def test_overpayment_is_allowed_with_a_warning_and_a_negative_open_amount(repo, sales, pay):
    init(repo, income="accrual", vat="ist")
    inv = sales.finalize("invoices", draft(repo, "2026-06-01", [line("100.00")])["id"])
    r = pay.record(inv["id"], date="2026-06-05", amount=Decimal("120.00"), user=BOSS)
    assert [w["code"] for w in r["warnings"]] == ["overpayment"]
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert view["open_amount"] == "-1.00" and view["status"] == "paid" and view["paid_date"] == "2026-06-05"
    assert ("customer_credit", -100) in [(ln.role, cents_of(ln)) for ln in entries(repo)[-1].lines]


def test_nothing_posts_before_the_initialisation_or_for_an_invoice_before_the_start(repo, sales, pay):
    repo.save("fiscal_years", fiscal_year(2026))
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    pay.record(inv["id"], date="2026-03-05", amount=Decimal("119.00"), user=BOSS)
    assert entries(repo) == [] and len(repo.find("payments", document_id=inv["id"])) == 1
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert view["status"] == "paid" and view["open_amount"] == "0.00"
    repo.save_settings({"accounting.start": "2026-04-01"})
    pay.record(inv["id"], date="2026-03-06", amount=Decimal("1.00"), kind="refund", user=BOSS)
    assert entries(repo) == []  # the invoice is dated before the start


def test_a_legacy_paid_invoice_is_materialised_and_posted_before_the_refund(repo, sales, pay):
    init(repo, income="accrual", vat="ist")
    inv = sales.finalize("invoices", draft(repo, "2026-02-01", [line("100.00")])["id"])
    repo.save("invoices", {**repo.get("invoices", inv["id"]), "status": "paid", "paid_date": "2026-02-03"})
    pay.record(inv["id"], date="2026-02-10", amount=Decimal("10.00"), kind="refund", user=BOSS)
    rows = repo.find("payments", document_id=inv["id"])
    assert sorted((r["kind"], r["amount_cents"], r["date"]) for r in rows) == [
        ("payment", 11900, "2026-02-03"), ("refund", -1000, "2026-02-10"),
    ]  # fmt: skip
    keys = [e.event_key for e in entries(repo)]
    assert len(keys) == 3 and sum(k.startswith("payment:") for k in keys) == 2


def test_put_paid_now_posts(repo, sales, pay):
    init(repo, income="accrual", vat="ist")
    inv = sales.finalize("invoices", draft(repo, "2026-02-01", [line("100.00")])["id"])
    out = sales.update_invoice(BOSS, inv["id"], {"status": "paid", "paid_date": "2026-02-03"})
    assert out["status"] == "paid" and out["paid_date"] == "2026-02-03"
    keys = [e.event_key for e in entries(repo)]
    (row,) = repo.find("payments", document_id=inv["id"])
    assert keys[-1] == f"payment:{row['id']}" and row["note"] == "recorded as paid"
    sales.update_invoice(BOSS, inv["id"], {"status": "paid", "paid_date": "2026-02-03"})  # second PUT: no-op
    assert len(repo.find("payments", document_id=inv["id"])) == 1 and len(entries(repo)) == 2


# ---------------------------------------------------------------- validation through the API
def pay_url(inv) -> str:
    return f"/api/invoices/{inv['id']}/payments"


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"date": "2026-02-05", "amount": "0"}, "greater than 0"),
        ({"date": "2026-02-05", "amount": "-1.00"}, "greater than 0"),
        ({"date": "2026-02-05", "amount": "10000000.01"}, "at most"),
        ({"date": "2026-02-05", "amount": "1.005"}, "cents"),
        ({"date": "2026-02-05", "amount": "1.00", "kind": "gift"}, ""),
        ({"date": "2026-02-05", "amount": "1.00", "method": "card"}, ""),
        ({"date": "2026-02-05", "amount": "1.00", "note": "x" * 201}, ""),
        ({"date": "2026-02-05"}, ""),
        ({"date": "not-a-date", "amount": "1.00"}, ""),
    ],
)
def test_strict_body_validation(boss_client, cust, body, fragment):
    inv = issue(boss_client, cust)
    r = boss_client.post(pay_url(inv), json=body)
    assert r.status_code == 422, r.text
    assert fragment in r.text


def test_the_upper_amount_bound_is_accepted(boss_client, cust):
    inv = issue(boss_client, cust)
    r = boss_client.post(pay_url(inv), json={"date": "2026-02-05", "amount": "10000000.00"})
    assert r.status_code == 201, r.text
    assert any(w["code"] == "overpayment" for w in r.json()["warnings"])


def test_date_rules(boss_client, cust):
    inv = issue(boss_client, cust, day="2026-02-01")
    early = boss_client.post(pay_url(inv), json={"date": "2026-01-31", "amount": "1.00"})
    assert early.status_code == 422 and "advance payments are not supported" in early.json()["detail"]
    future = boss_client.post(pay_url(inv), json={"date": "2100-01-01", "amount": "1.00"})
    assert future.status_code == 422 and "future" in future.json()["detail"]
    same_day = boss_client.post(pay_url(inv), json={"date": "2026-02-01", "amount": "1.00"})
    assert same_day.status_code == 201


def test_only_issued_invoices_of_kind_invoice(boss_client, cust, store):
    drafted = make_invoice(boss_client, cust)
    r = boss_client.post(pay_url(drafted), json={"date": "2026-02-05", "amount": "1.00"})
    assert r.status_code == 409
    inv = issue(boss_client, cust)
    row = store.get("invoices", inv["id"])
    cn = store.save(
        "invoices",
        {k: v for k, v in row.items() if k not in ("id", "number")}
        | {"kind": "credit_note", "credit_kind": "partial", "corrects_id": inv["id"], "number": "RK-9"},
    )
    assert boss_client.post(pay_url(cn), json={"date": "2026-02-05", "amount": "1.00"}).status_code == 422
    assert boss_client.get(pay_url(inv)).status_code == 200
    assert boss_client.post(pay_url({"id": "nope"}), json={"date": "2026-02-05", "amount": "1.00"}).status_code == 404
    assert boss_client.get(pay_url({"id": "nope"})).status_code == 404


def test_note_is_stored_and_listed(boss_client, cust):
    inv = issue(boss_client, cust)
    r = boss_client.post(
        pay_url(inv), json={"date": "2026-02-05", "amount": "10.00", "method": "cash", "note": "<b>x</b>"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["payment"]["note"] == "<b>x</b>" and body["payment"]["method"] == "cash"
    assert body["invoice"]["paid_amount"] == "10.00" and body["invoice"]["open_amount"] == "1180.00"
    listed = boss_client.get(pay_url(inv)).json()
    assert [(p["amount"], p["kind"], p["method"]) for p in listed] == [("10.00", "payment", "cash")]


# ---------------------------------------------------------------- oracle payment cases P1, P2, P6 (API)
def test_p1_put_paid_creates_the_payment_and_a_second_put_changes_nothing(boss_client, cust):
    case = next(c for c in ORACLE["payment_cases"] if c["id"] == "P1")
    inv = issue(boss_client, cust)
    assert inv["issued"]["gross"] == "1190.00" and case["invoice_gross"] == 119000
    r = put(boss_client, inv, **case["put"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert [(p["date"], p["amount"]) for p in out["payments"]] == [("2026-02-03", "1190.00")]
    assert (out["open_amount"], out["status"], out["paid_date"]) == ("0.00", "paid", case["payments"][0]["date"])
    again = put(boss_client, out, **case["put"])
    assert again.status_code == 200 and len(again.json()["payments"]) == 1


def test_p2_two_payments_then_reversing_the_second(boss_client, cust):
    case = next(c for c in ORACLE["payment_cases"] if c["id"] == "P2")
    body = {
        "customer_id": cust["id"],
        "date": "2026-02-01",
        "lines": [
            {"description": "A", "qty": 1, "price": 1000, "vat": 19},
            {"description": "B", "qty": 1, "price": 500, "vat": 7},
        ],
    }
    inv = boss_client.post("/api/invoices", json=body).json()
    boss_client.post(f"/api/invoices/{inv['id']}/finalize")
    posted = []
    for p in case["payments"]:
        r = boss_client.post(pay_url(inv), json={"date": p["date"], "amount": f"{p['amount'] / 100:.2f}"})
        assert r.status_code == 201, r.text
        posted.append(r.json())
    assert posted[1]["invoice"]["paid_date"] == case["paid_date"] and posted[1]["invoice"]["open_amount"] == "0.00"
    r = boss_client.post(f"/api/payments/{posted[1]['payment']['id']}/reverse", json={})
    assert r.status_code == 201, r.text
    after = r.json()["invoice"]
    exp = case["after_reversing_second"]
    assert after["open_amount"] == f"{exp['open_amount'] / 100:.2f}"
    assert after["paid_date"] is None and exp["paid_date"] is None and after["status"] == exp["status"]
    assert boss_client.post(f"/api/payments/{r.json()['payment']['id']}/reverse", json={}).status_code == 409


def test_p6_issued_invoice_puts(boss_client, cust):
    case = next(c for c in ORACLE["payment_cases"] if c["id"] == "P6")
    inv = issue(boss_client, cust)
    codes = [c["expect_status_code"] for c in case["issued_invoice_put"]]
    lines = [{**inv["lines"][0], "qty": 2}]
    assert put(boss_client, inv, lines=lines).status_code == codes[0]
    assert put(boss_client, inv, notes="Thanks").status_code == codes[1]
    assert put(boss_client, inv, status="cancelled").status_code == codes[2]
    assert put(boss_client, inv, status="paid").status_code == codes[3]


def test_finalize_response_always_carries_paid_date(boss_client, cust):
    inv = make_invoice(boss_client, cust)
    out = boss_client.post(f"/api/invoices/{inv['id']}/finalize").json()
    assert "paid_date" in out and out["paid_date"] is None
    assert out["open_amount"] == "1190.00" and out["payments"] == []


def test_payment_with_an_employee_without_write_rights_is_refused(app, clients, store, cust, boss_client):
    inv = issue(boss_client, cust)
    r = clients["employee"].post(pay_url(inv), json={"date": "2026-02-05", "amount": "1.00"})
    assert r.status_code == 403
    assert clients["accountant"].post(pay_url(inv), json={"date": "2026-02-05", "amount": "1.00"}).status_code == 201


# ---------------------------------------------------------------- atomicity and exact cents
def _forced_failure(monkeypatch):
    from permita38.core.service.journal import JournalService

    def boom(*a, **k):
        raise ServiceError(409, "forced")

    monkeypatch.setattr(JournalService, "post", boom)


def test_a_failed_posting_leaves_no_payments_row(repo, sales, pay, monkeypatch):
    init(repo, income="accrual", vat="ist")
    inv = sales.finalize("invoices", draft(repo, "2026-02-01", [line("100.00")])["id"])
    ok = pay.record(inv["id"], date="2026-02-03", amount=Decimal("50.00"), user=BOSS)
    before = repo.find("payments", document_id=inv["id"])
    _forced_failure(monkeypatch)
    with pytest.raises(ServiceError):
        pay.record(inv["id"], date="2026-02-04", amount=Decimal("10.00"), user=BOSS)
    with pytest.raises(ServiceError):
        pay.reverse(ok["payment"]["id"], user=BOSS)
    assert repo.find("payments", document_id=inv["id"]) == before


def test_a_legacy_reversal_with_a_failed_posting_leaves_no_row(repo, sales, pay, monkeypatch):
    init(repo, income="accrual", vat="ist")
    inv = sales.finalize("invoices", draft(repo, "2026-02-01", [line("100.00")])["id"])
    repo.save("invoices", {**repo.get("invoices", inv["id"]), "status": "paid", "paid_date": "2026-02-03"})
    _forced_failure(monkeypatch)
    with pytest.raises(ServiceError):
        pay.reverse_legacy(inv["id"], BOSS)
    assert repo.find("payments", document_id=inv["id"]) == []  # the materialisation rolled back too


def test_more_than_two_decimals_is_refused_not_truncated(repo, sales, pay):
    inv = sales.finalize("invoices", draft(repo, "2026-02-01", [line("100.00")])["id"])
    with pytest.raises(ServiceError) as err:
        pay.record(inv["id"], date="2026-02-03", amount=Decimal("1.005"), user=BOSS)
    assert err.value.status == 422
    assert repo.find("payments", document_id=inv["id"]) == []
    out = pay.record(inv["id"], date="2026-02-03", amount=Decimal("1.50"), user=BOSS)
    assert out["payment"]["amount"] == "1.50"
