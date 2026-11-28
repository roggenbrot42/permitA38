"""The amounts of write-offs, cash discounts and write-off reversals (docs/specs/accounting.md 9.2 and 9.3).

Pure, integer cents. Split from ``postings_credit.py`` (the state) because these helpers use the target rule of
``postings_targets.py``, which itself builds on ``CreditState``.
"""

from permita38.core.accounting.postings_credit import _current, _groups, credit_split
from permita38.core.accounting.postings_targets import _group_targets, allocate, apply_event, div_half_up
from permita38.core.accounting.types import Bases, Cents, InvoiceState, PaymentEvent, RateGroup

__all__ = ["discount_amounts", "write_off_amounts", "write_off_reversal_amounts"]

_ANY_BASES = Bases("accrual", "soll")  # the cash allocation does not depend on the bases


def write_off_amounts(state: InvoiceState) -> tuple[RateGroup, ...]:
    """The open amount ``C_r - T_r`` (gross) per rate group with the VAT complement ``CV_r - TV_r`` (9.3).

    One ``RateGroup`` (net, VAT) per group of the snapshot, all zero when nothing is open (``T >= C``).
    """
    cur, cur_vat = _current(state)
    groups, _, _, c_total = _group_targets(state, _ANY_BASES)
    rates = tuple(g.rate for g in state.snapshot.rates)
    if sum(g.cash for g in groups) >= c_total:
        return _groups(rates, [Cents(0)] * len(rates), [Cents(0)] * len(rates))
    gross = [Cents(c - g.cash) for c, g in zip(cur, groups, strict=True)]
    vat = [Cents(cv - g.cash_vat) for cv, g in zip(cur_vat, groups, strict=True)]
    return _groups(rates, gross, vat)


def discount_amounts(state: InvoiceState, amount_paid: Cents) -> tuple[RateGroup, ...]:
    """The discount ``C_r - T'_r`` that lets ``amount_paid`` settle the invoice exactly (9.3).

    ``T'`` is the cash kept after that payment (``kept + amount_paid``, at most ``C``) allocated over the current
    ``C_r``; the VAT is the complement ``CV_r - TV'_r`` with ``TV'_r = q2(CV_r * T'_r / C_r)``.
    """
    if amount_paid < 0:
        raise ValueError("a discount settles a payment, the amount paid must not be negative")
    cur, cur_vat = _current(state)
    rates = tuple(g.rate for g in state.snapshot.rates)
    c_total = sum(cur)
    t_new = min(state.kept + amount_paid, c_total)
    alloc = (
        allocate(Cents(t_new), list(zip(rates, cur, strict=True))) if c_total > 0 else tuple(Cents(0) for _ in rates)
    )
    vat_cash = [Cents(div_half_up(cv * t, c) if c != 0 else 0) for cv, t, c in zip(cur_vat, alloc, cur, strict=True)]
    gross = [Cents(c - t) for c, t in zip(cur, alloc, strict=True)]
    vat = [Cents(cv - tv) for cv, tv in zip(cur_vat, vat_cash, strict=True)]
    return _groups(rates, gross, vat)


def write_off_reversal_amounts(before: InvoiceState, payment: PaymentEvent) -> tuple[RateGroup, ...]:
    """The (negative) write-off reversal ``payment`` triggers, given the state *before* it (9.2).

    The reversal is ``min(T_after - max(C, T_before), remaining written-off gross)``: only what this payment adds above
    the current gross ``C`` (cash owed back after a credit note never reverses a write-off), at most the payment itself.
    It is split over the rate groups by largest remainder on the written-off gross (ties to the larger rate); the VAT of
    a group is its written-off VAT in proportion, so a full recovery gives every group exactly its written-off net and
    VAT back. Zeros when no reversal is due (also for a payment that is not positive).
    """
    rates = tuple(g.rate for g in before.snapshot.rates)
    zeros = _groups(rates, [Cents(0)] * len(rates), [Cents(0)] * len(rates))
    if payment.amount <= 0:
        return zeros
    after = apply_event(before, payment)
    _, written = credit_split(before)
    wo_gross = [g.gross for g in written]
    cur, _ = _current(before)
    total_gross = before.snapshot.gross

    def settled(state: InvoiceState) -> int:
        return min(state.kept, total_gross) if total_gross > 0 else 0

    amount = min(settled(after) - max(sum(cur), settled(before)), sum(wo_gross), payment.amount)
    if amount <= 0:
        return zeros
    gross = allocate(Cents(amount), list(zip(rates, wo_gross, strict=True)))
    vat = [
        Cents(div_half_up(w.vat * g, wg) if wg != 0 else 0) for w, g, wg in zip(written, gross, wo_gross, strict=True)
    ]
    return _groups(rates, list(gross), vat, sign=-1)
