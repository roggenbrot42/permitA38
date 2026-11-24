# ruff: noqa: F811
"""Write-offs, write-off reversals and settle-discount (docs/specs/credit-notes-payments.md 3, 4, P3; accounting.md 9.2,
9.3, 11, A9, A13).

Expected journal lines and amounts come from the hand-computed oracle ``fixtures/accounting_oracle.json`` (never edited).
The oracle's A10 is the trade-tax case and has no write-off; the Ist variant of A9 covers the Ist write-off side.
"""

from decimal import Decimal

import pytest

from permita38.core.errors import ServiceError

from .test_credit_notes import cn, money, run_events  # noqa: F401  (fixtures and helpers)
from .test_invoice_freeze import cust, issue, store  # noqa: F401  (fixtures and helpers)
from .test_issue_posting import (  # noqa: F401  (fixtures and helpers)
    ORACLE,
    draft,
    entries,
    entry_rows,
    init,
    line,
    lock_through,
    oracle_rows,
    repo,
    sales,
)
from .test_payments import BOSS, pay  # noqa: F401  (fixtures and helpers)
from .test_rbac import clients  # noqa: F401  (fixture)


def compare_all(repo, case, evs):
    wanted = [e for e in case["entries"] if e["event"] in range(len(evs))]
    posted = entries(repo)
    assert len(posted) == len(wanted), [e.event_key for e in posted]
    for got, exp in zip(posted, wanted, strict=True):
        assert sorted(entry_rows(got), key=repr) == sorted(oracle_rows(exp), key=repr), (exp["event"], exp.get("part"))
        assert got.booking_date == exp["booking_date"]
    return posted


def credits_of(repo, inv, kind):
    return [n for n in repo.find("invoices", corrects_id=inv["id"]) if n.get("credit_kind") == kind]


# ---------------------------------------------------------------- oracle: A9 (write-off), A13 (payment after it)
@pytest.mark.parametrize("case_id", ["A9-write-off-soll", "A9-write-off-ist"])
def test_a9_write_off_posts_the_oracle_entries_and_has_no_number(repo, sales, pay, cn, case_id):
    case, evs, inv, (note,) = run_events(repo, sales, pay, cn, case_id)
    posted = compare_all(repo, case, evs)
    assert note["credit_kind"] == "write_off" and "number" not in note and note["status"] == "sent"
    assert {r: (g["net"], g["vat"]) for r, g in note["issued"]["rates"].items()} == {
        r: (money(v["net"]), money(v["vat"])) for r, v in evs[2]["rates"].items()
    }
    entry = posted[-1]
    assert entry.event_key == f"credit:{note['id']}" and entry.source_id == note["id"] and entry.source_number is None
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert view["open_amount"] == "0.00" and view["credited_amount"] == "595.00"


def test_a13_payment_after_a_write_off_posts_the_reversal_record_then_the_payment(repo, sales, pay, cn):
    case, evs, inv, _notes = run_events(repo, sales, pay, cn, "A13-payment-after-write-off-soll")
    posted = compare_all(repo, case, evs)
    (rev,) = credits_of(repo, inv, "write_off_reversal")
    assert posted[-2].event_key == f"credit:{rev['id']}" and posted[-1].event_key.startswith("payment:")
    assert rev["date"] == "2026-11-15" and "number" not in rev and rev["kind"] == "credit_note"
    assert rev["issued"]["rates"]["19"] == {"net": -50000, "vat": -9500, "gross": -59500}
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert (view["open_amount"], view["credited_amount"], view["paid_amount"], view["status"]) == (
        "0.00",
        "0.00",
        "1190.00",
        "paid",
    )


def test_a_payment_that_adds_nothing_above_the_current_gross_does_not_reverse(repo, sales, pay, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    pay.record(inv["id"], date="2026-03-05", amount=Decimal("59.50"), user=BOSS)
    cn.create(inv["id"], credit_kind="write_off", date="2026-03-06", reason="Lost", user=BOSS)  # C = P = 59.50
    assert credits_of(repo, inv, "write_off_reversal") == []
    pay.record(inv["id"], date="2026-03-07", amount=Decimal("10.00"), user=BOSS)  # 10.00 above C: reversal of 10.00
    (rev,) = credits_of(repo, inv, "write_off_reversal")
    assert rev["issued"]["gross"] == -1000


def test_no_reversal_without_write_off_or_for_a_refund(repo, sales, pay, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    pay.record(inv["id"], date="2026-03-05", amount=Decimal("119.00"), user=BOSS)
    pay.record(inv["id"], date="2026-03-06", amount=Decimal("5.00"), kind="refund", user=BOSS)
    assert repo.find("invoices", corrects_id=inv["id"]) == []


def test_write_off_reversal_is_bounded_by_the_write_off(repo, sales, pay, cn):
    _case, _evs, inv, _ = run_events(repo, sales, pay, cn, "A9-write-off-soll")
    pay.record(inv["id"], date="2026-11-15", amount=Decimal("1000.00"), user=BOSS)  # far more than written off
    (rev,) = credits_of(repo, inv, "write_off_reversal")
    assert rev["issued"]["rates"]["19"] == {"net": -50000, "vat": -9500, "gross": -59500}


# ---------------------------------------------------------------- write-off rules
def test_write_off_only_when_open(repo, sales, pay, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    pay.record(inv["id"], date="2026-03-05", amount=Decimal("119.00"), user=BOSS)
    with pytest.raises(ServiceError) as exc:
        cn.create(inv["id"], credit_kind="write_off", date="2026-03-06", reason="Uncollectable", user=BOSS)
    assert exc.value.status == 422
    inv2 = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    cn.create(inv2["id"], credit_kind="write_off", date="2026-03-06", reason="Uncollectable", user=BOSS)
    with pytest.raises(ServiceError) as exc:  # open is now 0
        cn.create(inv2["id"], credit_kind="write_off", date="2026-03-07", reason="Again", user=BOSS)
    assert exc.value.status == 422


def test_write_off_does_not_consume_a_credit_note_number(repo, sales, pay, cn):
    init(repo)
    a = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    b = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    wo = cn.create(a["id"], credit_kind="write_off", date="2026-03-06", reason="Lost", user=BOSS)
    st = cn.create(b["id"], credit_kind="storno", date="2026-03-06", reason="Cancel", user=BOSS)
    assert "number" not in wo and st["number"] == "RK-2026-0001"


def test_write_off_per_rate_group_is_the_complement(repo, sales, pay, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("1000.00", "19"), line("500.00", "7")])["id"])
    pay.record(inv["id"], date="2026-03-05", amount=Decimal("500.01"), user=BOSS)  # odd cent over two groups
    note = cn.create(inv["id"], credit_kind="write_off", date="2026-03-06", reason="Lost", user=BOSS)
    assert note["issued"]["gross"] == "1224.99"  # 1725.00 - 500.01
    assert sum(Decimal(g["gross"]) for g in note["issued"]["rates"].values()) == Decimal("1224.99")
    assert sales.invoice_view(repo.get("invoices", inv["id"]))["open_amount"] == "0.00"


def test_write_off_in_a_locked_month_is_409_and_in_an_open_month_never_drifts(repo, sales, pay, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-01-05", [line("100.00")])["id"])
    lock_through(repo, 1)
    with pytest.raises(ServiceError) as exc:
        cn.create(inv["id"], credit_kind="write_off", date="2026-01-20", reason="Lost", user=BOSS)
    assert exc.value.status == 409
    cn.create(inv["id"], credit_kind="write_off", date="2026-02-02", reason="Lost", user=BOSS)
    assert not entries(repo)[-1].warnings


def written_off_then_locked(repo, sales, pay, cn, vat):
    init(repo, income="accrual", vat=vat)
    inv = sales.finalize("invoices", draft(repo, "2026-01-05", [line("1000.00")])["id"])
    pay.record(inv["id"], date="2026-01-10", amount=Decimal("595.00"), user=BOSS)
    cn.create(inv["id"], credit_kind="write_off", date="2026-02-02", reason="Lost", user=BOSS)
    lock_through(repo, 2)
    return inv


def test_soll_reversal_dated_in_a_locked_month_drifts(repo, sales, pay, cn):
    # Soll: the reversal makes the VAT of 95.00 due again at the payment date: drift case 2 (accounting 11)
    inv = written_off_then_locked(repo, sales, pay, cn, "soll")
    result = pay.record(inv["id"], date="2026-02-20", amount=Decimal("595.00"), user=BOSS)
    assert [w["code"] for w in result["warnings"]] == ["vat_drift"]
    (rev,) = credits_of(repo, inv, "write_off_reversal")
    entry = next(e for e in entries(repo) if e.event_key == f"credit:{rev['id']}")
    assert entry.warnings and entry.booking_date == "2026-03-01" and entry.document_date == "2026-02-20"
    tax = [ln for ln in entry.lines if ln.tax_kind == "tax"]
    assert [(cents_of(ln), str(ln.vat_date)) for ln in tax] == [(-9500, "2026-02-20")]


def test_ist_reversal_dated_in_a_locked_month_changes_no_vat_itself(repo, sales, pay, cn):
    # Ist: the write-off changed no VAT, so its reversal changes none either; the cash payment carries the VAT
    inv = written_off_then_locked(repo, sales, pay, cn, "ist")
    result = pay.record(inv["id"], date="2026-02-20", amount=Decimal("595.00"), user=BOSS)
    drifting = [e for e in entries(repo) if e.warnings]
    assert [e.event_key.split(":")[0] for e in drifting] == ["payment"]
    assert [w["code"] for w in result["warnings"]] == ["vat_drift"]
    (rev,) = credits_of(repo, inv, "write_off_reversal")
    entry = next(e for e in entries(repo) if e.event_key == f"credit:{rev['id']}")
    assert not entry.warnings and not [ln for ln in entry.lines if ln.tax_kind == "tax"]


def cents_of(ln) -> int:
    return int(Decimal(str(ln.amount)) * 100)


# ---------------------------------------------------------------- settle-discount
def test_p3_settle_discount(repo, sales, pay, cn):
    case = next(c for c in ORACLE["payment_cases"] if c["id"] == "P3")
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-02-01", [line("1000.00")])["id"])
    sd = case["settle_discount"]
    result = cn.settle_discount(
        inv["id"], date=sd["date"], amount_paid=Decimal(sd["amount_paid"]) / 100, note="Skonto", user=BOSS
    )
    credit = result["credit_note"]
    exp = case["discount_credit_note"]["19"]
    assert credit["credit_kind"] == "discount" and credit["number"] == "RK-2026-0001" and credit["date"] == sd["date"]
    assert credit["issued"]["rates"]["19"] == {
        "net": money(exp["net"]),
        "vat": money(exp["vat"]),
        "gross": money(exp["net"] + exp["vat"]),
    }
    assert result["payment"]["amount"] == money(sd["amount_paid"])
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert view["open_amount"] == money(case["open_amount"]) and view["status"] == "paid"
    posted = entries(repo)
    assert [e.event_key.split(":")[0] for e in posted] == ["issue", "credit", "payment"]
    assert ("cash_discount_granted", 2000) in [(r[0], r[1]) for r in entry_rows(posted[1])]


def test_settle_discount_leaves_exactly_zero_on_odd_cent_multi_rate_invoices(repo, sales, pay, cn):
    init(repo)
    for paid in ["1000.01", "1234.57", "0.01", "1561.32", "1500.33"]:
        inv = sales.finalize(
            "invoices",
            draft(repo, "2026-02-01", [line("999.99", "19"), line("333.33", "7"), line("12.34", "19")])["id"],
        )
        cn.settle_discount(inv["id"], date="2026-02-10", amount_paid=Decimal(paid), user=BOSS)
        view = sales.invoice_view(repo.get("invoices", inv["id"]))
        assert view["open_amount"] == "0.00", paid
        assert view["paid_amount"] == paid


def test_settle_discount_rejects_a_payment_that_covers_the_open_amount(repo, sales, pay, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-02-01", [line("100.00")])["id"])
    with pytest.raises(ServiceError) as exc:
        cn.settle_discount(inv["id"], date="2026-02-10", amount_paid=Decimal("119.00"), user=BOSS)
    assert exc.value.status == 422
    assert repo.find("payments", document_id=inv["id"]) == [] and repo.find("invoices", corrects_id=inv["id"]) == []


def test_settle_discount_is_atomic(repo, sales, pay, cn, monkeypatch):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-02-01", [line("100.00")])["id"])

    def boom(*a, **k):
        raise ServiceError(500, "boom")

    monkeypatch.setattr(pay.posting, "post_payment", boom)
    with pytest.raises(ServiceError):
        cn.settle_discount(inv["id"], date="2026-02-10", amount_paid=Decimal("100.00"), user=BOSS)
    assert repo.find("payments", document_id=inv["id"]) == []
    assert repo.find("invoices", corrects_id=inv["id"]) == [] and len(entries(repo)) == 1


# ---------------------------------------------------------------- API: routes, rights, no number, no PDF
def _url(inv, tail):
    return f"/api/invoices/{inv['id']}/{tail}"


def _write_off(c, inv, day="2026-02-05"):
    return c.post(_url(inv, "credit-notes"), json={"credit_kind": "write_off", "date": day, "reason": "Lost"})


def test_api_write_off_has_no_number_and_no_pdf(boss_client, cust):
    inv = issue(boss_client, cust)
    r = _write_off(boss_client, inv)
    assert r.status_code == 201, r.text
    note = r.json()
    assert not note.get("number") and note["credit_kind"] == "write_off"
    pdf = boss_client.get(f"/api/invoices/{note['id']}/pdf")
    assert pdf.status_code == 409, pdf.status_code
    assert _write_off(boss_client, inv, "2026-02-06").status_code == 422
    got = boss_client.get(f"/api/invoices/{inv['id']}").json()
    assert got["open_amount"] == "0.00" and [c["credit_kind"] for c in got["credit_notes"]] == ["write_off"]


def test_api_settle_discount(boss_client, cust):
    inv = issue(boss_client, cust)
    r = boss_client.post(
        _url(inv, "settle-discount"), json={"date": "2026-02-05", "amount_paid": "1000.00", "note": "Skonto"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["credit_note"]["credit_kind"] == "discount" and body["credit_note"]["number"]
    assert body["invoice"]["open_amount"] == "0.00" and body["payment"]["amount"] == "1000.00"
    again = boss_client.post(_url(inv, "settle-discount"), json={"date": "2026-02-05", "amount_paid": "1.00"})
    assert again.status_code == 422


def test_api_payment_after_a_write_off_reverses_it(boss_client, cust):
    inv = issue(boss_client, cust)
    assert _write_off(boss_client, inv).status_code == 201
    paid = boss_client.post(_url(inv, "payments"), json={"date": "2026-02-10", "amount": "1190.00"})
    assert paid.status_code == 201, paid.text
    assert paid.json()["invoice"]["open_amount"] == "0.00"
    notes = boss_client.get(_url(inv, "credit-notes")).json()
    assert [c["credit_kind"] for c in notes] == ["write_off", "write_off_reversal"]
    for note in notes:  # internal vouchers: no PDF for either
        assert boss_client.get(f"/api/invoices/{note['id']}/pdf").status_code == 409


def test_write_off_and_settle_discount_rights(app, clients, cust, boss_client):
    inv = issue(boss_client, cust)
    body = {"date": "2026-02-05", "amount_paid": "10.00"}
    assert clients["employee"].post(_url(inv, "settle-discount"), json=body).status_code == 403
    assert _write_off(clients["employee"], inv).status_code == 403
    assert _write_off(clients["accountant"], inv).status_code == 201
    inv2 = issue(boss_client, cust)
    assert clients["accountant"].post(_url(inv2, "settle-discount"), json=body).status_code == 201


# ---------------------------------------------------------------- treatments: statistical lines and base_cents
def cents_of_base(ln) -> int | None:
    return None if ln.tax_base is None else int(Decimal(str(ln.tax_base)) * 100)


def stat_rows(entry):
    """``(role, amount cents, tax_key, tax_kind, base cents, vat_date)`` of the lines that carry a tax key."""
    return [
        (ln.role, cents_of(ln), ln.tax_key, ln.tax_kind, cents_of_base(ln), str(ln.vat_date))
        for ln in entry.lines
        if ln.tax_key
    ]


def treatment_invoice(repo, sales, income, treatment):
    init(repo, income=income, vat="soll", small_business=treatment == "small_business")
    return sales.finalize("invoices", draft(repo, "2026-03-02", [line("1000.00", None)], tax_treatment=treatment)["id"])


STAT = {"eu_b2b_service": ("revenue_eu_svc", "EU_SVC"), "small_business": ("revenue_ku", "KU")}


@pytest.mark.parametrize("income", ["accrual", "eur"])
@pytest.mark.parametrize("treatment", ["eu_b2b_service", "small_business"])
def test_write_off_statistical_lines(repo, sales, pay, cn, income, treatment):
    inv = treatment_invoice(repo, sales, income, treatment)
    role, key = STAT[treatment]
    before = len(entries(repo))
    cn.create(inv["id"], credit_kind="write_off", date="2026-03-06", reason="Lost", user=BOSS)
    posted = entries(repo)[before:]
    if income == "eur" and treatment == "small_business":
        assert posted == []  # no cash moved and the KU statistic follows cash
        return
    (entry,) = posted
    assert entry.booking_date == "2026-03-06"
    assert stat_rows(entry) == [(role, 0, key, "base_only", -100000, "2026-03-06")]
    amounts = {ln.role: cents_of(ln) for ln in entry.lines}
    if income == "accrual":  # the net goes to bad_debts, receivables are reduced
        assert amounts == {"receivables": -100000, "bad_debts": 100000, role: 0}
    else:
        assert amounts == {role: 0}


@pytest.mark.parametrize("income", ["accrual", "eur"])
@pytest.mark.parametrize("treatment", ["eu_b2b_service", "small_business"])
def test_settle_discount_statistical_lines(repo, sales, pay, cn, income, treatment):
    inv = treatment_invoice(repo, sales, income, treatment)
    role, key = STAT[treatment]
    before = len(entries(repo))
    cn.settle_discount(inv["id"], date="2026-03-06", amount_paid=Decimal("980.00"), user=BOSS)
    posted = entries(repo)[before:]
    kinds = [e.event_key.split(":")[0] for e in posted]
    pay_entry = posted[-1]
    assert kinds[-1] == "payment"
    if income == "eur" and treatment == "small_business":
        assert kinds == ["payment"]  # the KU statistic follows cash: base = the cash 980.00
        assert stat_rows(pay_entry) == [(role, -98000, key, "base_only", 98000, "2026-03-06")]
        return
    assert kinds == ["credit", "payment"]
    credit = posted[0]
    assert stat_rows(credit) == [(role, 0, key, "base_only", -2000, "2026-03-06")]
    if income == "accrual":
        assert {ln.role: cents_of(ln) for ln in credit.lines} == {
            "receivables": -2000,
            "cash_discount_granted": 2000,
            role: 0,
        }
        assert {ln.role: cents_of(ln) for ln in pay_entry.lines} == {"bank": 98000, "receivables": -98000}
    else:  # EÜR/eu_b2b_service: the discount posts only the statistic, the revenue follows the cash
        assert {ln.role: cents_of(ln) for ln in credit.lines} == {role: 0}
        assert [(ln.role, cents_of(ln), ln.tax_key) for ln in pay_entry.lines] == [
            ("bank", 98000, None),
            (role, -98000, None),
        ]


# ---------------------------------------------------------------- recovery in steps, order of dates, atomicity
def test_partial_payment_then_second_payment_after_a_write_off(repo, sales, pay, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])  # 100.00 + 19.00
    cn.create(inv["id"], credit_kind="write_off", date="2026-03-06", reason="Lost", user=BOSS)
    pay.record(inv["id"], date="2026-03-10", amount=Decimal("50.00"), user=BOSS)
    (first,) = credits_of(repo, inv, "write_off_reversal")
    # VAT = half-up(1900 * 5000 / 11900) = 798 (7.983...), net 4202
    assert first["issued"]["rates"]["19"] == {"net": -4202, "vat": -798, "gross": -5000}
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert (view["open_amount"], view["credited_amount"], view["paid_amount"]) == ("0.00", "69.00", "50.00")
    pay.record(inv["id"], date="2026-03-11", amount=Decimal("69.00"), user=BOSS)
    both = sorted(credits_of(repo, inv, "write_off_reversal"), key=lambda n: n["date"])
    assert len(both) == 2
    # the rest of the written-off gross 69.00 takes the whole remaining VAT 19.00 - 7.98 = 11.02
    assert both[1]["issued"]["rates"]["19"] == {"net": -5798, "vat": -1102, "gross": -6900}
    assert sum(n["issued"]["rates"]["19"]["vat"] for n in both) == -1900  # a full recovery restores the VAT exactly
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert (view["open_amount"], view["credited_amount"], view["paid_amount"], view["status"]) == (
        "0.00",
        "0.00",
        "119.00",
        "paid",
    )
    keys = [e.event_key.split(":")[0] for e in entries(repo)]
    assert keys == ["issue", "credit", "credit", "payment", "credit", "payment"]


def test_payment_dated_before_the_write_off_still_reverses_it(repo, sales, pay, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    cn.create(inv["id"], credit_kind="write_off", date="2026-03-12", reason="Lost", user=BOSS)
    pay.record(inv["id"], date="2026-03-05", amount=Decimal("119.00"), user=BOSS)
    (rev,) = credits_of(repo, inv, "write_off_reversal")
    assert rev["date"] == "2026-03-05" and rev["issued"]["rates"]["19"] == {
        "net": -10000,
        "vat": -1900,
        "gross": -11900,
    }
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert (view["open_amount"], view["credited_amount"], view["status"]) == ("0.00", "0.00", "paid")


def test_reversal_record_uses_the_injected_clock(repo, sales, pay, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    cn.create(inv["id"], credit_kind="write_off", date="2026-03-06", reason="Lost", user=BOSS)
    pay.now = lambda: 1_700_000_000.0
    pay.record(inv["id"], date="2026-03-10", amount=Decimal("119.00"), user=BOSS)
    (rev,) = credits_of(repo, inv, "write_off_reversal")
    assert rev["issued"]["issued_at"] == 1_700_000_000


def test_a_refund_never_triggers_a_reversal_even_with_cash_above_the_gross(repo, sales, pay, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    pay.record(inv["id"], date="2026-03-03", amount=Decimal("119.00"), user=BOSS)
    cn.create(inv["id"], credit_kind="partial", date="2026-03-04", reason="Part", amounts={"19": "50.00"}, user=BOSS)
    # cash 119.00 > current gross 59.50: 59.50 is owed back; refunding part of it never reverses anything
    pay.record(inv["id"], date="2026-03-05", amount=Decimal("10.00"), kind="refund", user=BOSS)
    assert credits_of(repo, inv, "write_off_reversal") == []


def test_reversal_and_payment_are_atomic(repo, sales, pay, cn, monkeypatch):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")])["id"])
    cn.create(inv["id"], credit_kind="write_off", date="2026-03-06", reason="Lost", user=BOSS)
    n_entries = len(entries(repo))

    def boom(*a, **k):
        raise ServiceError(500, "boom")

    monkeypatch.setattr(pay.posting, "post_payment", boom)
    with pytest.raises(ServiceError):
        pay.record(inv["id"], date="2026-03-10", amount=Decimal("119.00"), user=BOSS)
    assert credits_of(repo, inv, "write_off_reversal") == []
    assert repo.find("payments", document_id=inv["id"]) == [] and len(entries(repo)) == n_entries


def test_a_fully_written_off_invoice_has_nothing_open_to_be_overdue(repo, sales, pay, cn):
    init(repo)
    inv = sales.finalize("invoices", draft(repo, "2026-03-02", [line("100.00")], due_date="2026-03-16")["id"])
    cn.create(inv["id"], credit_kind="write_off", date="2026-03-20", reason="Lost", user=BOSS)
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert view["open_amount"] == "0.00" and view["credited_amount"] == "119.00"


# ---------------------------------------------------------------- settle-discount in a locked month (accounting 11)
def test_settle_discount_in_a_locked_month_dates_the_credit_in_the_open_month_and_lets_the_payment_drift(
    repo, sales, pay, cn
):
    init(repo, vat="ist")
    inv = sales.finalize("invoices", draft(repo, "2026-01-05", [line("1000.00")])["id"])
    lock_through(repo, 1)
    result = cn.settle_discount(inv["id"], date="2026-01-20", amount_paid=Decimal("1000.00"), user=BOSS)
    credit = result["credit_note"]
    assert credit["date"] == "2026-02-01" and result["payment"]["date"] == "2026-01-20"
    assert [w["code"] for w in result["warnings"]] == ["vat_drift"]
    credit_entry = next(e for e in entries(repo) if e.event_key == f"credit:{credit['id']}")
    assert credit_entry.booking_date == "2026-02-01" and not credit_entry.warnings
    view = sales.invoice_view(repo.get("invoices", inv["id"]))
    assert view["open_amount"] == "0.00" and view["status"] == "paid"
    # the payment is still checked against the invoice date
    inv2 = sales.finalize("invoices", draft(repo, "2026-02-03", [line("100.00")])["id"])
    with pytest.raises(ServiceError) as exc:
        cn.settle_discount(inv2["id"], date="2026-02-01", amount_paid=Decimal("100.00"), user=BOSS)
    assert exc.value.status == 422
