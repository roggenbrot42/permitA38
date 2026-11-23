"""Write-offs, discounts and write-off reversals (accounting.md 9.2, 9.3) against the hand-computed oracle.

The expected entries of A9 and A13 come only from ``fixtures/accounting_oracle.json``; the discount and the two-rate
write-off cases below are computed by hand (the arithmetic is in the comments).
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from permita38.core.accounting.postings_credit import (
    CreditState,
    credit_split,
    discount_amounts,
    write_off_amounts,
    write_off_reversal_amounts,
)
from permita38.core.accounting.postings_targets import apply_event, event_entry, targets
from permita38.core.accounting.types import (
    Bases,
    Cents,
    CreditEvent,
    Entry,
    Event,
    InvoiceState,
    IssuedSnapshot,
    IssueEvent,
    PaymentEvent,
    RateGroup,
)
from tests.backend.test_postings_targets import (
    CASES,
    _actual_rows,
    _bases,
    _events,
    _groups,
    _oracle_rows,
    _snapshot,
    _vat_sums,
)

D19 = Decimal(19)
D7 = Decimal(7)
DAY = date(2026, 3, 1)
PAY_DAY = date(2026, 3, 5)


def _rg(rate: Decimal, net: int, vat: int) -> RateGroup:
    return RateGroup(rate, Cents(net), Cents(vat))


def _replay_credit(case: dict[str, Any]) -> list[tuple[int, str | None, Entry | None]]:
    """Replay the fixture events; a payment that lifts ``P`` above ``C`` first posts its write-off reversal."""
    bases = _bases(case)
    invoices: dict[str, InvoiceState] = {}
    out: list[tuple[int, str | None, Entry | None]] = []
    for index, ev in enumerate(_events(case)):
        when = date.fromisoformat(ev["date"])
        if ev["type"] == "issue":
            state = InvoiceState.issued(_snapshot(ev))
            out.append((index, None, event_entry(None, state, bases, IssueEvent(when))))
            invoices[ev["id"]] = state
            continue
        before = invoices[ev["invoice"]]
        event: Event
        if ev["type"] == "payment":
            event = PaymentEvent(f"pay{index}", when, Cents(ev["amount"]))
            after = apply_event(before, event)
            reversal = write_off_reversal_amounts(after)
            if any(g.net or g.vat for g in reversal):
                rev = CreditEvent(f"wor{index}", when, "write_off_reversal", reversal)
                mid = apply_event(before, rev)
                after = apply_event(mid, event)
                out.append((index, "wo_reversal", event_entry(before, mid, bases, rev)))
                out.append((index, "payment", event_entry(mid, after, bases, event)))
            else:
                out.append((index, None, event_entry(before, after, bases, event)))
        else:
            if ev["credit_kind"] == "write_off":  # the document amounts are the ones the fixture lists
                assert write_off_amounts(before) == _groups(ev["rates"])
            event = CreditEvent(ev["id"], when, ev["credit_kind"], _groups(ev["rates"]))
            after = apply_event(before, event)
            out.append((index, None, event_entry(before, after, bases, event)))
        invoices[ev["invoice"]] = after
    return out


@pytest.mark.parametrize("case_id", ["A9-write-off-soll", "A9-write-off-ist", "A13-payment-after-write-off-soll"])
def test_write_off_cases_replay_to_the_oracle_entries(case_id: str) -> None:
    case = CASES[case_id]
    invoice = _events(case)[0]["id"]
    results = _replay_credit(case)
    expected = case["entries"]
    assert [(i, part) for i, part, _ in results] == [(e["event"], e.get("part")) for e in expected]
    for (_, _, entry), want in zip(results, expected, strict=True):
        assert entry is not None
        oracle = _oracle_rows(want, invoice)
        assert _actual_rows(entry, any(r[8] for r in oracle), invoice) == oracle, want
        assert entry.booking_date.isoformat() == want["booking_date"]
        assert entry.balance == 0
    assert _vat_sums([e for _, _, e in results if e is not None]) == case["vat"]


def _single(rates: tuple[RateGroup, ...]) -> InvoiceState:
    return InvoiceState.issued(IssuedSnapshot("inv", "regular", "standard", rates, date(2026, 1, 1)))


def _lines(entry: Entry | None) -> list[tuple[Any, ...]]:
    assert entry is not None
    return sorted(
        (
            ln.role,
            ln.amount_cents,
            ln.tax_key or "",
            ln.tax_base_cents,
            ln.vat_date.isoformat() if ln.vat_date else "",
            ln.income_date.isoformat() if ln.income_date else "",
        )
        for ln in entry.lines
    )


def _slots(state: InvoiceState, bases: Bases) -> dict[tuple[str, str], int]:
    return {(t.role, t.part): t.amount_cents for t in targets(state, bases).lines}


# ---- settle with discount: 1,190.00 @ 19 % paid with 1,166.20 -----------------------------------------------------


def test_discount_amounts_are_the_complement_of_the_cash() -> None:
    state = _single((_rg(D19, 100000, 19000),))
    # T' = 116,620; TV' = 190.00 * 1166.20 / 1190.00 = 186.20; discount gross 23.80 = net 20.00 + VAT 3.80
    assert discount_amounts(state, Cents(116620)) == (_rg(D19, 2000, 380),)
    assert discount_amounts(state, Cents(119000)) == (_rg(D19, 0, 0),)
    paid = apply_event(state, PaymentEvent("p0", DAY, Cents(50000)))
    assert discount_amounts(paid, Cents(66620)) == (_rg(D19, 2000, 380),)  # 500.00 + 666.20 = 1,166.20 again
    with pytest.raises(ValueError):
        discount_amounts(state, Cents(-1))


@pytest.mark.parametrize("vat", ["soll", "ist"])
def test_settle_with_discount_then_payment_settles_exactly(vat: str) -> None:
    bases = Bases("accrual", vat)  # type: ignore[arg-type]
    state = _single((_rg(D19, 100000, 19000),))
    credit = CreditEvent("disc1", DAY, "discount", discount_amounts(state, Cents(116620)))
    after_credit = apply_event(state, credit)
    assert credit_split(after_credit)[0] == (_rg(D19, 2000, 380),)
    entry = event_entry(state, after_credit, bases, credit)
    pay = PaymentEvent("pay1", PAY_DAY, Cents(116620))
    settled = apply_event(after_credit, pay)
    pay_entry = event_entry(after_credit, settled, bases, pay)
    d, p = DAY.isoformat(), PAY_DAY.isoformat()
    if vat == "soll":
        want_credit = [
            ("cash_discount_granted", 2000, "", None, "", d),
            ("vat_out_s19", 380, "S19", -2000, d, ""),
            ("receivables", -2380, "", None, "", ""),
        ]
        want_pay = [("bank", 116620, "", None, "", ""), ("receivables", -116620, "", None, "", "")]
    else:
        want_credit = [
            ("cash_discount_granted", 2000, "", None, "", d),
            ("vat_not_due_s19", 380, "", None, "", ""),
            ("receivables", -2380, "", None, "", ""),
        ]
        want_pay = [
            ("bank", 116620, "", None, "", ""),
            ("receivables", -116620, "", None, "", ""),
            ("vat_not_due_s19", 18620, "", None, "", ""),
            ("vat_out_s19", -18620, "S19", 98000, p, ""),
        ]
    assert _lines(entry) == sorted(want_credit)
    assert _lines(pay_entry) == sorted(want_pay)
    assert entry is not None and pay_entry is not None and entry.balance == 0 and pay_entry.balance == 0
    final = _slots(settled, bases)
    assert final[("receivables", "")] == 0
    assert final[("cash_discount_granted", "")] == 2000
    assert final[("revenue_s19", "")] == -100000


# ---- two rates: the complement split leaves no cent on vat_not_due -----------------------------------------------

TWO_RATES = (_rg(D19, 33333, 6333), _rg(D7, 1001, 70))  # gross 396.66 and 10.71, total 407.37


def test_two_rate_write_off_uses_the_vat_complement() -> None:
    state = apply_event(_single(TWO_RATES), PaymentEvent("p", DAY, Cents(10000)))
    # T = 100.00 over C_r = (39666, 1071): floors 9737 / 262, 1 cent left; remainders 1277 vs 12302 -> the 7 % group
    # gets it: T_r = (9737, 263). TV_19 = 6333 * 9737 / 39666 = 1554.59 -> 1555, TV_7 = 70 * 263 / 1071 = 17.19 -> 17.
    # write-off gross 29929 / 808, VAT 4778 / 53 (the formula gross * 19 / 119 would give 4779: one cent too much).
    amounts = write_off_amounts(state)
    assert amounts == (_rg(D19, 25151, 4778), _rg(D7, 755, 53))
    assert 29929 * 19 / 119 > 4778.5
    for bases in (Bases("accrual", "soll"), Bases("accrual", "ist")):
        credit = CreditEvent("wo", PAY_DAY, "write_off", amounts)
        after = apply_event(state, credit)
        lines = _slots(after, bases)
        assert lines[("receivables", "")] == 0
        assert lines[("vat_not_due_s19", "")] == 0
        assert lines[("vat_not_due_s7", "")] == 0
        assert lines[("bad_debts", "")] == 25151 + 755
        entry = event_entry(state, after, bases, credit)
        assert entry is not None and entry.balance == 0
        assert [g.cash_vat for g in targets(after, bases).groups] == [1555, 17]
        assert write_off_amounts(after) == (_rg(D19, 0, 0), _rg(D7, 0, 0))


def test_partial_recovery_splits_the_reversal_over_the_written_off_amounts() -> None:
    state = apply_event(_single(TWO_RATES), PaymentEvent("p", DAY, Cents(10000)))
    state = apply_event(state, CreditEvent("wo", DAY, "write_off", write_off_amounts(state)))
    after = apply_event(state, PaymentEvent("p2", PAY_DAY, Cents(5000)))
    # R = min(T - C, W) = min(5000, 30737) = 5000 over wo gross (29929, 808): floors 4868 / 131, remainders 17284 vs
    # 13453 -> 4869 / 131; VAT 4778 * 4869 / 29929 = 777.3 -> 777, 53 * 131 / 808 = 8.59 -> 9
    reversal = write_off_reversal_amounts(after)
    assert reversal == (_rg(D19, -4092, -777), _rg(D7, -122, -9))
    assert -sum(g.gross for g in reversal) == 5000
    mid = apply_event(state, CreditEvent("wor", PAY_DAY, "write_off_reversal", reversal))
    settled = apply_event(mid, PaymentEvent("p2", PAY_DAY, Cents(5000)))
    assert [g.gross for g in credit_split(settled)[1]] == [29929 - 4869, 808 - 131]
    bases = Bases("accrual", "soll")
    assert targets(settled, bases).overpayment == 0
    assert _slots(settled, bases)[("receivables", "")] == 0  # C = 100.00 + 50.00 = P


def test_reversal_is_capped_by_the_written_off_amount_and_validated() -> None:
    state = apply_event(_single((_rg(D19, 100000, 19000),)), PaymentEvent("p", DAY, Cents(59500)))
    state = apply_event(state, CreditEvent("wo", DAY, "write_off", write_off_amounts(state)))
    # pay 1,000.00 more: T - C = 1,000.00 but only 595.00 was written off; the rest is an overpayment
    after = apply_event(state, PaymentEvent("p2", PAY_DAY, Cents(100000)))
    assert write_off_reversal_amounts(after) == (_rg(D19, -50000, -9500),)
    assert write_off_reversal_amounts(state) == (_rg(D19, 0, 0),)  # no cash above C
    with pytest.raises(ValueError):
        apply_event(state, CreditEvent("x", PAY_DAY, "write_off_reversal", (_rg(D19, -50001, -9500),)))
    with pytest.raises(ValueError):
        apply_event(state, CreditEvent("x", PAY_DAY, "write_off_reversal", (_rg(D19, 1, 0),)))
    with pytest.raises(ValueError):
        apply_event(state, CreditEvent("x", PAY_DAY, "partial", (_rg(D19, -1, 0),)))


def test_reversing_the_triggering_payment_does_not_restore_the_write_off() -> None:
    bases = Bases("accrual", "soll")
    state = _single((_rg(D19, 100000, 19000),))
    state = apply_event(state, PaymentEvent("p1", DAY, Cents(59500)))
    state = apply_event(state, CreditEvent("wo", DAY, "write_off", write_off_amounts(state)))
    pay = PaymentEvent("p2", PAY_DAY, Cents(59500))
    after = apply_event(state, pay)
    state = apply_event(state, CreditEvent("wor", PAY_DAY, "write_off_reversal", write_off_reversal_amounts(after)))
    state = apply_event(state, pay)
    assert isinstance(state, CreditState) and not any(g.net or g.vat for g in state.written_off)
    reopened = apply_event(state, PaymentEvent("p2-reversal", date(2026, 3, 9), Cents(-59500)))
    assert not any(g.net or g.vat for g in credit_split(reopened)[1])
    assert write_off_reversal_amounts(reopened) == (_rg(D19, 0, 0),)
    lines = _slots(reopened, bases)
    assert lines[("receivables", "")] == 59500  # open again
    assert ("bad_debts", "") not in lines
    # the user may write it off again: the open amount is the full 595.00
    assert write_off_amounts(reopened) == (_rg(D19, 50000, 9500),)
