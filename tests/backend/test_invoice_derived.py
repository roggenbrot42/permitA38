"""Derived payment fields of an issued invoice (docs/specs/credit-notes-payments.md 2.3, examples P1, P2, P4).

Pure tests of ``core/service/invoice_state.py`` with literal numbers, plus the API responses (GET one and list).
"""

import pytest

from permita38.core.service import invoice_state as st
from permita38.core.service.sales import record_payment_row

from .test_invoice_freeze import cust, issue, make_invoice, store  # noqa: F401  (fixtures and helpers)


def pay(amount, day, kind="payment", **extra):
    return {"id": "p" + day[-2:], "date": day, "amount_cents": amount, "kind": kind, "method": "bank", **extra}


def credit(gross, kind="partial", day="2026-03-01", number="RK-1"):
    return {"id": "c1", "number": number, "date": day, "credit_kind": kind, "issued": {"gross": gross}}


def inv(status="sent", **extra):
    return {"id": "i1", "date": "2026-02-01", "status": status, **extra}


def test_cents_str():
    assert [st.cents_str(c) for c in (0, 5, -5, 119000, -31000, 100)] == [
        "0.00", "0.05", "-0.05", "1190.00", "-310.00", "1.00"
    ]  # fmt: skip


def test_open_invoice():
    d = st.derive(inv(), 119000, [], [])
    assert d.fields == {
        "paid_amount": "0.00", "credited_amount": "0.00", "open_amount": "1190.00",
        "payments": [], "credit_notes": [], "status": "sent",
    }  # fmt: skip
    assert d.paid_date is None and d.virtual is None and d.flags == ()


def test_p1_full_payment():
    d = st.derive(inv(), 119000, [pay(119000, "2026-02-03")], [])
    assert (d.status, d.paid_date, d.fields["open_amount"], d.fields["paid_amount"]) == (
        "paid", "2026-02-03", "0.00", "1190.00"
    )  # fmt: skip
    assert d.fields["paid_date"] == "2026-02-03"


def test_p2_two_payments_and_reversal():
    rows = [pay(100000, "2026-02-03"), pay(72500, "2026-02-10")]
    d = st.derive(inv(), 172500, rows, [])
    assert (d.status, d.paid_date, d.fields["paid_amount"]) == ("paid", "2026-02-10", "1725.00")
    rows.append(pay(-72500, "2026-02-12", kind="reversal", reverses_id="p10"))
    d = st.derive(inv(), 172500, rows, [])
    assert (d.status, d.paid_date, d.fields["open_amount"], d.fields["paid_amount"]) == (
        "sent", None, "725.00", "1000.00"
    )  # fmt: skip
    assert "paid_date" not in d.fields
    assert [p["amount"] for p in d.fields["payments"]] == ["1000.00", "725.00", "-725.00"]


def test_partial_payment_is_not_paid():
    d = st.derive(inv("accepted"), 119000, [pay(50000, "2026-02-03")], [])
    assert (d.status, d.paid_date, d.fields["open_amount"]) == ("accepted", None, "690.00")


def test_refund_reopens():
    rows = [pay(119000, "2026-02-03"), pay(-19000, "2026-02-05", kind="refund")]
    d = st.derive(inv(), 119000, rows, [])
    assert (d.status, d.fields["open_amount"], d.fields["paid_amount"]) == ("sent", "190.00", "1000.00")


def test_open_amount_negative_is_a_customer_credit():
    d = st.derive(inv(), 119000, [pay(150000, "2026-02-03")], [])
    assert d.fields["open_amount"] == "-310.00" and d.status == "paid" and d.paid_date == "2026-02-03"


def test_partial_credit_note_reduces_the_open_amount():
    d = st.derive(inv(), 200000 + 38000, [], [credit(59500)])
    assert (d.fields["credited_amount"], d.fields["open_amount"], d.status) == ("595.00", "1785.00", "sent")
    assert d.fields["credit_notes"] == [
        {"id": "c1", "number": "RK-1", "date": "2026-03-01", "credit_kind": "partial", "gross": "595.00"}
    ]


def test_credit_note_settling_the_rest_with_cash_makes_it_paid():
    d = st.derive(inv(), 119000, [pay(116620, "2026-02-03")], [credit(2380, "discount")])
    assert (d.status, d.fields["open_amount"], d.paid_date) == ("paid", "0.00", "2026-02-03")


def test_credit_note_alone_without_cash_is_not_paid():
    d = st.derive(inv(), 119000, [], [credit(119000, "write_off")])
    assert (d.status, d.fields["open_amount"], d.paid_date) == ("sent", "0.00", None)


def test_p4_storno_cancels_even_after_a_payment():
    notes = [credit(59500, "partial", "2026-03-01", "RK-1"), credit(178500, "storno", "2026-03-05", "RK-2")]
    notes[1]["id"] = "c2"
    d = st.derive(inv(), 238000, [], notes)
    assert (d.status, d.fields["credited_amount"], d.fields["open_amount"]) == ("cancelled", "2380.00", "0.00")
    d = st.derive(inv(), 238000, [pay(238000, "2026-02-03")], notes)
    assert (d.status, d.fields["open_amount"], d.paid_date) == ("cancelled", "-2380.00", "2026-02-03")


def test_status_order_cancelled_before_paid_before_label():
    full = [pay(119000, "2026-02-03")]
    assert st.derive(inv("rejected"), 119000, full, []).status == "paid"
    assert st.derive(inv("rejected"), 119000, [], []).status == "rejected"
    assert st.derive(inv("rejected"), 119000, full, [credit(119000, "storno")]).status == "cancelled"
    assert st.derive(inv("cancelled"), 119000, [], []).status == "cancelled"  # a stored legacy label stays
    # a storno that does not credit everything (partial storno rows cannot exist, but the rule is "everything")
    assert st.derive(inv(), 119000, [], [credit(50000, "storno")]).status == "sent"


def test_stored_paid_backed_by_nothing_reads_as_sent():
    assert st.derive(inv("paid"), 0, [], []).status == "sent"
    rows = [pay(119000, "2026-02-03"), pay(-119000, "2026-02-04", kind="reversal")]
    assert st.derive(inv("paid"), 119000, rows, []).status == "sent"


def test_legacy_virtual_payment_with_paid_date():
    d = st.derive(inv("paid", paid_date="2026-02-10"), 119000, [], [])
    assert d.virtual == {
        "id": None, "date": "2026-02-10", "amount": "1190.00", "kind": "payment", "method": "bank", "note": "migrated"
    }  # fmt: skip
    assert (d.status, d.paid_date, d.fields["open_amount"], d.flags) == ("paid", "2026-02-10", "0.00", ())


def test_legacy_virtual_payment_without_paid_date_is_flagged():
    d = st.derive(inv("paid"), 119000, [], [])
    assert d.virtual is not None and d.virtual["date"] == "2026-02-01" and d.paid_date == "2026-02-01"
    assert d.flags == (st.FLAG_NO_PAID_DATE,)
    d = st.derive(inv("paid", paid_date="garbage"), 119000, [], [])
    assert d.flags == (st.FLAG_NO_PAID_DATE,)


def test_legacy_paid_date_before_the_invoice_date_is_flagged_and_kept():
    d = st.derive(inv("paid", paid_date="2026-01-15"), 119000, [], [])
    assert d.flags == (st.FLAG_PAID_BEFORE_INVOICE,) and d.paid_date == "2026-01-15"


def test_virtual_payment_only_without_payment_rows():
    d = st.derive(inv("paid", paid_date="2026-02-10"), 119000, [pay(50000, "2026-02-11")], [])
    assert d.virtual is None and d.fields["paid_amount"] == "500.00" and d.status == "sent"


# -- API ----------------------------------------------------------------------------------------------------------


def test_get_and_list_carry_the_derived_fields_as_decimal_strings(boss_client, cust, store):  # noqa: F811
    a = issue(boss_client, cust)
    b = issue(boss_client, cust, day="2026-02-02")
    draft = make_invoice(boss_client, cust, day="2026-02-03")
    record_payment_row(store, a["id"], date="2026-02-03", amount_cents=100000)
    got = boss_client.get(f"/api/invoices/{a['id']}").json()
    assert (got["paid_amount"], got["credited_amount"], got["open_amount"]) == ("1000.00", "0.00", "190.00")
    assert got["payments"][0]["amount"] == "1000.00" and got["status"] == "sent" and "paid_date" not in got
    rows = {r["id"]: r for r in boss_client.get("/api/invoices").json()}
    assert rows[a["id"]]["open_amount"] == "190.00" and rows[b["id"]]["open_amount"] == "1190.00"
    assert "open_amount" not in rows[draft["id"]] and rows[draft["id"]]["status"] == "draft"
    record_payment_row(store, a["id"], date="2026-02-05", amount_cents=19000)
    got = boss_client.get(f"/api/invoices/{a['id']}").json()
    assert (got["status"], got["paid_date"], got["open_amount"]) == ("paid", "2026-02-05", "0.00")
    assert boss_client.get("/api/invoices").json()  # the list still serialises


def test_overpayment_shows_a_negative_open_amount(boss_client, cust, store):  # noqa: F811
    a = issue(boss_client, cust)
    record_payment_row(store, a["id"], date="2026-02-03", amount_cents=150000)
    got = boss_client.get(f"/api/invoices/{a['id']}").json()
    assert (got["open_amount"], got["status"]) == ("-310.00", "paid")


def test_storno_row_cancels_the_invoice_in_the_api(boss_client, cust, store):  # noqa: F811
    a = issue(boss_client, cust)
    record_payment_row(store, a["id"], date="2026-02-03", amount_cents=119000)
    store.save(
        "invoices",
        {
            "customer_id": cust["id"], "date": "2026-02-09", "status": "sent", "kind": "credit_note",
            "corrects_id": a["id"], "credit_kind": "storno", "number": "RK-2026-0001", "lines": [],
            "issued": {"rates": {}, "gross": 119000},
        },
    )  # fmt: skip
    got = boss_client.get(f"/api/invoices/{a['id']}").json()
    assert (got["status"], got["credited_amount"], got["open_amount"]) == ("cancelled", "1190.00", "-1190.00")
    assert got["credit_notes"][0]["number"] == "RK-2026-0001"
    assert boss_client.put(f"/api/invoices/{a['id']}", json={"notes": "x"}).status_code == 200


def test_finance_counts_an_invoice_by_its_derived_payment_state(boss_client, cust, store):  # noqa: F811
    from datetime import date

    from permita38.core import calc

    def fig():
        f = calc.finance(store, 2026, date(2026, 6, 1))
        return f["months"][1]["revenue"], f["open_receivables"]

    a = issue(boss_client, cust)
    assert fig() == (0, 1190.0)  # EUR basis: open until paid
    r = boss_client.put(f"/api/invoices/{a['id']}", json={"status": "paid", "paid_date": "2026-02-03"})
    assert r.status_code == 200
    assert fig() == (1000.0, 0)  # the payment row, not a stored status, makes it revenue of February
    legacy = store.save(
        "invoices",
        {"customer_id": cust["id"], "date": "2026-02-01", "status": "paid", "paid_date": "2026-02-10", "lines": [
            {"description": "Old", "qty": 1, "price": 500, "vat": 19}]},
    )  # fmt: skip
    assert fig() == (1500.0, 0)  # a legacy paid invoice (virtual payment) counts on its paid date
    assert legacy["id"]


@pytest.mark.parametrize("bad", ["status", "paid_date"])
def test_response_only_derived_fields_sent_back_are_ignored(boss_client, cust, bad):  # noqa: F811
    a = issue(boss_client, cust)
    r = boss_client.put(f"/api/invoices/{a['id']}", json={"paid_amount": "5.00", "open_amount": "1.00", "notes": bad})
    assert r.status_code == 200
    assert r.json()["open_amount"] == "1190.00" and r.json()["paid_amount"] == "0.00"
