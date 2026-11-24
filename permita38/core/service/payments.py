"""Payments, refunds and reversals of issued invoices (docs/specs/credit-notes-payments.md 2.3 and 3).

Every write materialises a legacy virtual payment first, inserts one append-only ``payments`` row (``amount_cents``
signed: payments positive, refunds and reversals negative) and, when the accounting is initialised and the invoice is on
or after the start, posts the 9.2 target delta through :class:`~permita38.core.service.postings.PaymentPostingService`
in the same transaction (event key ``payment:<payment id>``). Before the initialisation the rows are only stored.
A payment on a written-off invoice first stores and posts the write-off reversal record (spec 3, accounting 9.2).
"""

import builtins
import time
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from permita38.core import models
from permita38.core.accounting.postings_credit import write_off_reversal_amounts
from permita38.core.accounting.types import Cents, PaymentEvent
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service import invoice_state
from permita38.core.service.journal import JournalService
from permita38.core.service.postings import (
    CreditPostingService,
    PaymentPostingService,
    credit_snapshot,
    invoice_state_of,
    stored_snapshot,
)
from permita38.core.settings import effective_settings

__all__ = ["PaymentService"]

MAX_PAYMENT_CENTS = 10_000_000_00


def _insert_row(
    store: Repository,
    invoice_id: str,
    *,
    date: str,
    amount_cents: int,
    kind: str = "payment",
    method: str = "bank",
    note: str | None = None,
    reverses_id: str | None = None,
    user: Mapping[str, Any] | None = None,
) -> Record:
    """Private raw insert of one ``payments`` row: no validation and no posting. Only this module and the
    materialisation use it; every other write goes through :class:`PaymentService`."""
    row: Record = dict(
        document_id=invoice_id,
        date=date,
        amount_cents=int(amount_cents),
        method=method,
        kind=kind,
        created_at=int(time.time()),
        created_by=str((user or {}).get("id") or "system"),
    )
    if note:
        row["note"] = note
    if reverses_id:
        row["reverses_id"] = reverses_id
    return store.save("payments", row)


def _out(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row["id"],
        "date": row["date"],
        "amount": invoice_state.cents_str(int(row["amount_cents"])),
        "kind": row["kind"],
        "method": row["method"],
    }
    for key in ("note", "reverses_id"):
        if row.get(key):
            out[key] = row[key]
    return out


def _cents(amount: Decimal) -> int:
    scaled = amount * 100
    if scaled != scaled.to_integral_value():
        raise ServiceError(422, "The amount has at most 2 decimal places")
    return int(scaled)


class PaymentService:
    def __init__(
        self,
        store: Repository,
        journal: JournalService | None = None,
        today: Callable[[], date] = date.today,
    ):
        self.store = store
        self.today = today
        self.posting = PaymentPostingService(store, journal)
        self.credit_posting = CreditPostingService(store, journal)

    # ---------- helpers
    def _invoice(self, invoice_id: str) -> Record:
        inv = self.store.get("invoices", invoice_id)
        if not inv:
            raise ServiceError(404, "Not found")
        if inv.get("kind") == "credit_note":
            raise ServiceError(422, "A credit note is settled by a refund on its invoice")
        if inv.get("status", "draft") == "draft":
            raise ServiceError(409, "Only an issued invoice can receive payments")
        return inv

    def _derive(self, inv: Mapping[str, Any]) -> invoice_state.Derived:
        from permita38.core.service.sales import gross_cents_of  # sales imports this module

        st = effective_settings(self.store.settings())
        pays = self.store.find("payments", document_id=inv["id"])
        notes = invoice_state.credit_notes_of(self.store.find("invoices", corrects_id=inv["id"]))
        return invoice_state.derive(inv, gross_cents_of(inv, st), pays, notes)

    def _check_date(self, inv: Mapping[str, Any], day: str) -> None:
        if day < str(inv.get("date") or "")[:10]:
            raise ServiceError(422, "advance payments are not supported")
        if day > self.today().isoformat():
            raise ServiceError(422, "The payment date cannot be in the future")

    def materialize(self, inv: Record, user: Mapping[str, Any] | None = None) -> Record:
        """Spec 2.3: write the missing snapshot and turn a legacy virtual payment into a real row (posted like any
        payment). Idempotent; returns the invoice as stored afterwards."""
        from permita38.core.service.sales import materialize_legacy_payment

        st = effective_settings(self.store.settings())
        return materialize_legacy_payment(
            self.store, inv, st, user, post=lambda i, row: self.posting.post_payment(i, row, user)
        )

    def _warnings(self, entry: models.JournalEntry | None, open_cents: int, kind: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        if kind == "payment" and open_cents < 0:
            out.append(
                {
                    "code": "overpayment",
                    "message": f"More than the open amount was paid; {invoice_state.cents_str(-open_cents)} is "
                    "owed to the customer",
                }
            )
        if entry is not None and entry.warnings:
            out.append(self._drift_warning())
        return out

    @staticmethod
    def _drift_warning() -> dict[str, str]:
        return {
            "code": "vat_drift",
            "message": "The payment date lies in a locked month: the VAT changes that month's return",
        }

    def _write_off_reversal(
        self, inv: Record, day: str, cents: int, user: Mapping[str, Any] | None
    ) -> models.JournalEntry | None:
        """The write-off reversal record a payment of ``cents`` on ``day`` triggers (accounting 9.2): negative
        amounts, no number, dated the payment date, posted before the payment. ``None`` when nothing is written off
        or this payment adds nothing above the current gross; else its journal entry (``None`` before the
        initialisation)."""
        snap = stored_snapshot(inv)
        if snap is None:
            return None
        notes = invoice_state.credit_notes_of(self.store.find("invoices", corrects_id=inv["id"]))
        if not any(n.get("credit_kind") == "write_off" for n in notes):
            return None
        kept = sum(int(r["amount_cents"]) for r in self.store.find("payments", document_id=inv["id"]))
        before = invoice_state_of(snap, notes, kept)
        groups = write_off_reversal_amounts(before, PaymentEvent("pending", date.fromisoformat(day[:10]), Cents(cents)))
        if not any(g.net or g.vat for g in groups):
            return None
        record: dict[str, Any] = {
            "kind": "credit_note",
            "credit_kind": "write_off_reversal",
            "corrects_id": inv["id"],
            "customer_id": inv.get("customer_id"),
            "date": day[:10],
            "status": "sent",
            "lines": [],
            "reason": "Write-off reversed by a payment",
            "issued": credit_snapshot(inv, [(g.rate, int(g.net), int(g.vat)) for g in groups], day[:10], time.time()),
        }
        for key in ("project_id", "tax_treatment"):
            if inv.get(key):
                record[key] = inv[key]
        note = self.store.save("invoices", record)
        return self.credit_posting.post_credit(inv, note, user)

    def _insert(
        self,
        inv: Record,
        *,
        day: str,
        signed: int,
        kind: str,
        method: str,
        note: str | None,
        reverses_id: str | None,
        user: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        rows = self.store.find("payments", document_id=inv["id"])
        if sum(int(r["amount_cents"]) for r in rows) + signed < 0:
            raise ServiceError(422, "The cash kept for this invoice cannot become negative")
        row = _insert_row(
            self.store,
            inv["id"],
            date=day,
            amount_cents=signed,
            kind=kind,
            method=method,
            note=note,
            reverses_id=reverses_id,
            user=user,
        )
        entry = self.posting.post_payment(inv, row, user)
        d = self._derive(inv)
        return {
            "payment": _out(row),
            "invoice_id": inv["id"],
            "warnings": self._warnings(entry, d.open_cents, kind),
        }

    # ---------- writes
    def record(
        self,
        invoice_id: str,
        *,
        date: str,
        amount: Decimal,
        kind: str = "payment",
        method: str = "bank",
        note: str | None = None,
        user: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A payment or refund (spec 3). ``amount`` is positive; a refund is stored negative and may not exceed the
        cash kept (422). A payment above the open amount is allowed (warning ``overpayment``)."""
        cents = _cents(amount)
        if cents <= 0 or cents > MAX_PAYMENT_CENTS:
            raise ServiceError(422, "The amount must be greater than 0 and at most 10,000,000.00")
        if kind not in ("payment", "refund"):
            raise ServiceError(422, "kind must be payment or refund")
        with self.store.transaction():
            inv = self.materialize(self._invoice(invoice_id), user)
            self._check_date(inv, date)
            if kind == "refund":
                kept = sum(int(r["amount_cents"]) for r in self.store.find("payments", document_id=inv["id"]))
                if cents > kept:
                    raise ServiceError(422, f"A refund cannot exceed the cash kept ({invoice_state.cents_str(kept)})")
            recovered = self._write_off_reversal(inv, date, cents, user) if kind == "payment" else None
            result = self._insert(
                inv,
                day=date,
                signed=cents if kind == "payment" else -cents,
                kind=kind,
                method=method,
                note=note,
                reverses_id=None,
                user=user,
            )
            if (
                recovered is not None
                and recovered.warnings
                and not any(w["code"] == "vat_drift" for w in result["warnings"])
            ):
                result["warnings"].append(self._drift_warning())
            return result

    def reverse(
        self,
        payment_id: str,
        *,
        date: str | None = None,
        note: str | None = None,
        user: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Reverse a payment or refund entered by mistake: a row of kind ``reversal`` with the opposite sign. A
        reversal cannot be reversed and a row only once (409)."""
        with self.store.transaction():
            row = self.store.get("payments", payment_id)
            if not row:
                raise ServiceError(404, "Not found")
            inv = self.materialize(self._invoice(str(row["document_id"])), user)
            return self._reverse_row(inv, row, date or str(row["date"]), note, user, check_date=True)

    def _reverse_row(
        self,
        inv: Record,
        row: Mapping[str, Any],
        day: str,
        note: str | None,
        user: Mapping[str, Any] | None,
        *,
        check_date: bool,
    ) -> dict[str, Any]:
        if row.get("kind") == "reversal":
            raise ServiceError(409, "A reversal cannot be reversed")
        if any(r.get("reverses_id") == row["id"] for r in self.store.find("payments", document_id=inv["id"])):
            raise ServiceError(409, "The payment is already reversed")
        if check_date:
            self._check_date(inv, day)
        return self._insert(
            inv,
            day=day,
            signed=-int(row["amount_cents"]),
            kind="reversal",
            method=str(row.get("method") or "bank"),
            note=note,
            reverses_id=str(row["id"]),
            user=user,
        )

    def reverse_legacy(
        self,
        invoice_id: str,
        user: Mapping[str, Any] | None = None,
        date: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Spec 2.2/2.3: materialise the legacy virtual payment, then reverse exactly that row (the payment carrying
        the legacy note). 409 when there is none or it is already reversed. A legacy date may precede the invoice
        date, so the advance-payment rule does not apply to it; the date still cannot lie in the future."""
        with self.store.transaction():
            inv = self.materialize(self._invoice(invoice_id), user)
            rows = self.store.find("payments", document_id=inv["id"])
            legacy = [r for r in rows if r.get("kind") == "payment" and r.get("note") == invoice_state.LEGACY_NOTE]
            if not legacy:
                raise ServiceError(409, "The invoice has no legacy payment to reverse")
            row = legacy[0]
            day = date or str(row["date"])
            if day > self.today().isoformat():
                raise ServiceError(422, "The payment date cannot be in the future")
            return self._reverse_row(inv, row, day, note, user, check_date=False)

    # ---------- reads (last: the name shadows the builtin inside the class body)
    def list(self, invoice_id: str) -> builtins.list[dict[str, Any]]:
        inv = self.store.get("invoices", invoice_id)
        if not inv:
            raise ServiceError(404, "Not found")
        payments: builtins.list[dict[str, Any]] = self._derive(inv).fields["payments"]
        return payments
