"""Postings of trips, VAT payments and income-related taxes (docs/specs/accounting.md 9.5-9.7). Pure, integer cents."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from permita38.core.accounting.types import Cents, Entry, Line, Role

__all__ = [
    "TaxTemplate",
    "TripEvent",
    "special_prepayment_credit_entry",
    "special_prepayment_payment_entry",
    "tax_payment_entry",
    "tax_provision_entry",
    "tax_refund_entry",
    "trip_entry",
    "trip_reversal_entry",
    "vat_payment_entry",
    "vat_refund_entry",
]

_BANK = Role("bank")

TaxTemplate = Literal["trade_tax", "income_tax", "corporate_tax"]


@dataclass(frozen=True)
class TripEvent:
    """A finalised trip: allowance and mileage in cents, ``end_date`` becomes the ``income_date``."""

    trip_id: str
    revision: int
    end_date: date
    allowance: Cents
    mileage: Cents


def _entry(booking: date, document: date, key: str, *lines: Line) -> Entry:
    return Entry(lines=tuple(lines), booking_date=booking, document_date=document, event_key=key)


def _positive(amount: Cents) -> Cents:
    if amount <= 0:
        raise ValueError("amount must be positive")
    return amount


def trip_entry(
    trip: TripEvent,
    payee: Literal["owner", "employee"],
    *,
    booking_date: date | None = None,
) -> Entry | None:
    """D travel_costs (allowance + mileage) / C private_deposit (owner) or payables_employee; no VAT (9.5)."""
    if trip.allowance < 0 or trip.mileage < 0:
        raise ValueError("allowance and mileage must not be negative")
    total = Cents(trip.allowance + trip.mileage)
    if total == 0:
        return None
    credit = Role("private_deposit" if payee == "owner" else "payables_employee")
    return _entry(
        booking_date or trip.end_date,
        trip.end_date,
        f"trip:{trip.trip_id}:r{trip.revision}",
        Line(Role("travel_costs"), total, income_date=trip.end_date),
        Line(credit, Cents(-total)),
    )


def trip_reversal_entry(original: Entry, *, booking_date: date | None = None) -> Entry:
    """Reopening a trip: the same lines with every sign flipped (same ``income_date`` so the EÜR nets to 0)."""
    lines = tuple(
        Line(ln.role, Cents(-ln.amount_cents), income_date=ln.income_date, open_item_id=ln.open_item_id)
        for ln in original.lines
    )
    return Entry(
        lines=lines,
        booking_date=booking_date or original.booking_date,
        document_date=original.document_date,
        event_key=f"{original.event_key}:reversal",
    )


def _vat_move(
    event_id: str, day: date, amount: Cents, debit: str, credit: str, *, income: bool, booking_date: date | None
) -> Entry:
    _positive(amount)
    return _entry(
        booking_date or day,
        day,
        f"vat:{event_id}",
        Line(Role(debit), amount, income_date=day if income else None),
        Line(Role(credit), Cents(-amount)),
    )


def vat_payment_entry(event_id: str, pay_date: date, amount: Cents, *, booking_date: date | None = None) -> Entry:
    """VAT paid to the tax office: D vat_prepayments / C bank, ``income_date`` = payment date (9.6)."""
    return _vat_move(event_id, pay_date, amount, "vat_prepayments", "bank", income=True, booking_date=booking_date)


def vat_refund_entry(event_id: str, refund_date: date, amount: Cents, *, booking_date: date | None = None) -> Entry:
    """VAT refunded by the tax office: D bank / C vat_prepayments, ``income_date`` = refund date (9.6)."""
    _positive(amount)
    return _entry(
        booking_date or refund_date,
        refund_date,
        f"vat:{event_id}",
        Line(_BANK, amount),
        Line(Role("vat_prepayments"), Cents(-amount), income_date=refund_date),
    )


def special_prepayment_payment_entry(
    event_id: str, pay_date: date, amount: Cents, *, booking_date: date | None = None
) -> Entry:
    """Sondervorauszahlung paid: D vat_special_prepayment / C bank, ``income_date`` = payment date (9.6)."""
    return _vat_move(
        event_id, pay_date, amount, "vat_special_prepayment", "bank", income=True, booking_date=booking_date
    )


def special_prepayment_credit_entry(
    event_id: str, return_date: date, kz39: Cents, *, booking_date: date | None = None
) -> Entry:
    """December/Q4 return finalised: D vat_prepayments / C vat_special_prepayment with the Kz 39 amount (9.6)."""
    return _vat_move(
        event_id,
        return_date,
        kz39,
        "vat_prepayments",
        "vat_special_prepayment",
        income=False,
        booking_date=booking_date,
    )


_EXPENSE: dict[TaxTemplate, str] = {
    "trade_tax": "trade_tax_expense",
    "income_tax": "private_withdrawal",
    "corporate_tax": "corporate_tax_expense",
}
_PROVISION: dict[TaxTemplate, str] = {
    "trade_tax": "trade_tax_provision",
    "corporate_tax": "corporate_tax_provision",
}


def tax_payment_entry(
    kind: TaxTemplate,
    manual_id: str,
    pay_date: date,
    amount: Cents,
    *,
    from_provision: bool = False,
    booking_date: date | None = None,
) -> Entry:
    """Prepayment or final payment (9.7): D expense (private_withdrawal for income tax) / C bank.

    ``income_date`` = payment date for the expense roles of trade and corporate tax; income tax is a private
    withdrawal and carries none. ``from_provision`` pays last year's provision instead: D provision / C bank.
    """
    _positive(amount)
    if from_provision:
        if kind not in _PROVISION:
            raise ValueError("income tax has no provision")
        debit = Line(Role(_PROVISION[kind]), amount)
    else:
        debit = Line(Role(_EXPENSE[kind]), amount, income_date=None if kind == "income_tax" else pay_date)
    return _entry(booking_date or pay_date, pay_date, f"manual:{manual_id}", debit, Line(_BANK, Cents(-amount)))


def tax_provision_entry(
    kind: Literal["trade_tax", "corporate_tax"],
    manual_id: str,
    closing_date: date,
    amount: Cents,
    *,
    booking_date: date | None = None,
) -> Entry:
    """Accrual at year closing: D expense / C provision; no ``income_date`` (accrual only)."""
    _positive(amount)
    return _entry(
        booking_date or closing_date,
        closing_date,
        f"manual:{manual_id}",
        Line(Role(_EXPENSE[kind]), amount),
        Line(Role(_PROVISION[kind]), Cents(-amount)),
    )


def tax_refund_entry(
    kind: TaxTemplate,
    manual_id: str,
    refund_date: date,
    amount: Cents,
    *,
    booking_date: date | None = None,
) -> Entry:
    """The reverse lines of a payment: D bank / C ``tax_refunds`` (trade tax), private_withdrawal or corporate expense."""
    _positive(amount)
    credit_role = "tax_refunds" if kind == "trade_tax" else _EXPENSE[kind]
    return _entry(
        booking_date or refund_date,
        refund_date,
        f"manual:{manual_id}",
        Line(_BANK, amount),
        Line(Role(credit_role), Cents(-amount), income_date=None if kind == "income_tax" else refund_date),
    )
