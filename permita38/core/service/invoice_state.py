"""Derived payment state of an issued invoice (docs/specs/credit-notes-payments.md 2.3). Pure: no storage, no clock.

``derive`` takes the stored invoice, its gross in cents (the ``issued`` snapshot, or the current totals for a legacy
invoice that has none), its ``payments`` rows and the credit notes that correct it, and returns the fields a response
adds: ``paid_amount``, ``credited_amount``, ``open_amount``, ``payments``, ``credit_notes``, ``paid_date`` and the
derived ``status``. All amounts are integer cents inside and decimal strings (``"1190.00"``) in the result.

A legacy invoice (stored ``status = paid``, no ``payments`` rows) is read as having one **virtual payment** of its
gross (``id`` null, note ``migrated``) on its ``paid_date``, or on the invoice date when that is missing; the flags
tell the caller about the two suspicious cases (they go to the review list at the accounting initialisation).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

__all__ = [
    "FLAG_NO_PAID_DATE",
    "FLAG_PAID_BEFORE_INVOICE",
    "LEGACY_NOTE",
    "Derived",
    "cents_str",
    "credit_note_gross",
    "credit_notes_of",
    "derive",
    "group_by",
    "legacy_payment_date",
]

LEGACY_NOTE = "migrated"
FLAG_NO_PAID_DATE = "legacy_paid_without_date"
FLAG_PAID_BEFORE_INVOICE = "legacy_paid_before_invoice_date"


@dataclass(frozen=True)
class Derived:
    """The derived state. ``fields`` is what a response adds; the rest is for the services that act on it."""

    fields: dict[str, Any]
    status: str
    gross_cents: int
    paid_cents: int
    credited_cents: int
    open_cents: int
    paid_date: str | None
    virtual: dict[str, Any] | None  # the legacy virtual payment (a payments-row shape with id None), else None
    flags: tuple[str, ...]


def cents_str(cents: int) -> str:
    """Integer cents as a decimal string with two decimals (``119000`` -> ``"1190.00"``, ``-5`` -> ``"-0.05"``)."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(int(cents)), 100)
    return f"{sign}{whole}.{frac:02d}"


def _iso(value: Any) -> str | None:
    """A stored value as an ISO date string, ``None`` when it is not one."""
    text = str(value or "")[:10]
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def legacy_payment_date(doc: Mapping[str, Any]) -> str:
    """The date of the virtual payment of a legacy paid invoice: ``paid_date``, else the invoice date."""
    return _iso(doc.get("paid_date")) or _iso(doc.get("date")) or ""


def credit_note_gross(row: Mapping[str, Any]) -> int:
    """The credited gross of a credit-note row in cents (from its snapshot; stored positive)."""
    issued = row.get("issued")
    raw = issued.get("gross") if isinstance(issued, Mapping) else None
    if raw is None:
        return 0
    try:
        return abs(int(raw))
    except (TypeError, ValueError):
        return 0


def _payment_out(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.get("id"),
        "date": row.get("date"),
        "amount": cents_str(int(row.get("amount_cents") or 0)),
        "kind": row.get("kind") or "payment",
        "method": row.get("method") or "bank",
    }
    for key in ("note", "reverses_id"):
        if row.get(key):
            out[key] = row[key]
    return out


def derive(
    doc: Mapping[str, Any],
    gross_cents: int,
    payments: Iterable[Mapping[str, Any]],
    credit_notes: Iterable[Mapping[str, Any]],
) -> Derived:
    """The derived fields of one issued invoice (see the module docstring)."""
    rows = sorted(payments, key=lambda r: (str(r.get("date") or ""), int(r.get("created_at") or 0)))
    notes = sorted(credit_notes, key=lambda r: (str(r.get("date") or ""), str(r.get("number") or "")))
    stored_status = str(doc.get("status") or "sent")

    virtual: dict[str, Any] | None = None
    flags: list[str] = []
    cash: list[dict[str, Any]] = [_payment_out(r) for r in rows]
    cash_cents = [int(r.get("amount_cents") or 0) for r in rows]
    if not rows and stored_status == "paid" and gross_cents != 0:
        when = legacy_payment_date(doc)
        virtual = {
            "id": None,
            "date": when,
            "amount": cents_str(gross_cents),
            "kind": "payment",
            "method": "bank",
            "note": LEGACY_NOTE,
        }
        cash, cash_cents = [virtual], [gross_cents]
        if not _iso(doc.get("paid_date")):
            flags.append(FLAG_NO_PAID_DATE)
        elif when < str(doc.get("date") or "")[:10]:
            flags.append(FLAG_PAID_BEFORE_INVOICE)

    paid = sum(cash_cents)
    credited = sum(credit_note_gross(n) for n in notes)
    open_cents = gross_cents - credited - paid
    storno = any(n.get("credit_kind") == "storno" for n in notes)
    paid_date: str | None = None
    if open_cents <= 0 and paid > 0:
        paid_date = max((str(p["date"]) for p in cash if p.get("date")), default=None)

    if storno and credited >= gross_cents:
        status = "cancelled"
    elif open_cents <= 0 and paid > 0:
        status = "paid"
    elif stored_status == "paid":
        status = "sent"  # a stored `paid` that no payment backs (any more) reads as the plain label
    else:
        status = stored_status

    fields: dict[str, Any] = {
        "paid_amount": cents_str(paid),
        "credited_amount": cents_str(credited),
        "open_amount": cents_str(open_cents),
        "payments": cash,
        "credit_notes": [
            {
                "id": n.get("id"),
                "number": n.get("number"),
                "date": n.get("date"),
                "credit_kind": n.get("credit_kind"),
                "gross": cents_str(credit_note_gross(n)),
            }
            for n in notes
        ],
        "status": status,
    }
    if paid_date:
        fields["paid_date"] = paid_date
    return Derived(fields, status, gross_cents, paid, credited, open_cents, paid_date, virtual, tuple(flags))


def group_by(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, list[Mapping[str, Any]]]:
    """Rows grouped by one column (the list route loads payments and credit notes once)."""
    out: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row.get(key):
            out.setdefault(str(row[key]), []).append(row)
    return out


def credit_notes_of(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The rows that are credit notes (``kind = credit_note``)."""
    return [r for r in rows if r.get("kind") == "credit_note"]
