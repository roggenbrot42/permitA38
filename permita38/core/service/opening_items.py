"""Pre-start baseline and opening items (docs/specs/accounting.md 16 steps 4, 5, 5a), work package E23.

An invoice issued **before** the accounting start that is still open at the start date, and an expense dated before the
start but paid on or after it, need their balance-sheet part in the journal, because the cash that arrives later posts
``targets(after) - targets(before)`` and the "before" state already holds the earlier payments and credits.

* :class:`OpeningItemService` posts ONE opening entry per item, dated the start date, every line against
  ``opening_balance``, without ``vat_date`` and ``income_date`` (that VAT and income belong to returns outside the app).
  An invoice: the balance-sheet roles of the 9.2 targets (``postings_targets.targets``, not re-implemented) of its
  pre-start state: ``receivables`` (accrual), ``vat_not_due_r`` (accrual/Ist), ``vat_soll_clearing_r`` (EÜR/Soll);
  EÜR/Ist and a settled invoice give no line, so no entry. An expense: the open ``payables`` (accrual) or the
  ``input_vat_clearing`` (EÜR) of its bill entry. Event keys ``open:<year>:invoice:<id>`` / ``open:<year>:expense:<id>``.
* :class:`PrestartPaymentPosting` / :class:`PrestartCreditPosting` are the payment and credit posting services for
  an invoice dated before the start: they post the baseline first (lazily, idempotent: step 5a), then the event as
  the base service does, with the invoice seen as dated the start date (the first fiscal year of the accounting).
  An event dated before the start is part of the baseline and posts nothing.
* :class:`PrestartExpensePosting` posts the payment entry of an expense dated before the start and paid on/after it.

The opening entry is dated the start date even when that month is locked (it is an opening entry): the journal does
not refuse it, it books it on the first open day (``booking_date``) and keeps the start date as document date.
No SQL here.
"""

import dataclasses
from collections.abc import Mapping
from datetime import date
from typing import Any, cast

from permita38.core import models
from permita38.core.accounting.postings_expense import expense_bill_entry, expense_pay_entry
from permita38.core.accounting.postings_targets import targets
from permita38.core.accounting.types import Bases, Entry, IncomeBasis, InvoiceState, VatBasis
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service import invoice_state
from permita38.core.service.expense_postings import ExpensePostingService, Part
from permita38.core.service.journal import JournalService, JournalSource, PostLine
from permita38.core.service.locks import first_unlocked_from
from permita38.core.service.postings import (
    CreditPostingService,
    PaymentPostingService,
    invoice_state_of,
    post_line,
    stored_snapshot,
)
from permita38.core.settings import accounting_start

__all__ = [
    "OpeningItemService",
    "PrestartCreditPosting",
    "PrestartExpensePosting",
    "PrestartPaymentPosting",
]

OPENING_ROLE = "opening_balance"
EXPENSE_BALANCE_ROLES = ("payables", "input_vat_clearing")


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _balance_role(role: str) -> bool:
    """A role of the balance sheet part of an invoice's targets (everything else is revenue, VAT or cash)."""
    return role in ("receivables", "customer_credit") or role.startswith(("vat_not_due", "vat_soll_clearing"))


class OpeningItemService:
    def __init__(self, store: Repository, journal: JournalService | None = None):
        self.store = store
        self.journal = journal or JournalService(store)

    # ------------------------------------------------------------------ helpers
    def start(self) -> date | None:
        return accounting_start(self.store.settings())

    def is_prestart(self, invoice: Mapping[str, Any]) -> bool:
        """An invoice dated before the accounting start (and the accounting is initialised)."""
        start = self.start()
        day = _day(invoice.get("date"))
        return start is not None and day is not None and day < start and not invoice.get("credit_kind")

    def prestart_state(self, invoice: Mapping[str, Any], start: date) -> InvoiceState | None:
        """The 9.2 state of the invoice with the payments and credit notes dated before the start (``None`` without
        a snapshot). A legacy ``paid`` invoice without rows counts as paid in full on its legacy date."""
        snap = stored_snapshot(invoice)
        if snap is None:
            return None
        rows = self.store.find("payments", document_id=invoice["id"])
        if not rows and invoice.get("status") == "paid" and int(snap.gross) != 0:
            cash = int(snap.gross) if (_day(invoice_state.legacy_payment_date(invoice)) or start) < start else 0
        else:
            cash = sum(int(r["amount_cents"]) for r in rows if (_day(r.get("date")) or start) < start)
        notes = [
            n
            for n in self.store.find("invoices", corrects_id=invoice["id"])
            if n.get("kind") == "credit_note" and (_day(n.get("date")) or start) < start
        ]
        return invoice_state_of(snap, notes, max(cash, 0))

    @staticmethod
    def key(year: int, kind: str, ident: str) -> str:
        return f"open:{year}:{kind}:{ident}"

    @staticmethod
    def _counter(lines: list[PostLine]) -> list[PostLine]:
        total = sum(ln.amount_cents for ln in lines)
        return [*lines, PostLine(role=OPENING_ROLE, amount_cents=-total)] if total else lines

    # ------------------------------------------------------------------ invoices
    def baseline_lines(self, invoice: Mapping[str, Any], start: date, bases: Bases) -> list[PostLine]:
        """The balance-sheet lines of the pre-start baseline (no counter line), one per role, none for zero."""
        state = self.prestart_state(invoice, start)
        if state is None:
            return []
        sums: dict[str, int] = {}
        for tl in targets(state, bases).lines:
            if _balance_role(str(tl.role)):
                sums[str(tl.role)] = sums.get(str(tl.role), 0) + int(tl.amount_cents)
        return [
            PostLine(
                amount_cents=amount,
                role=role,
                open_item_id=str(invoice["id"]) if role == "receivables" else None,
            )
            for role, amount in sums.items()
            if amount
        ]

    def ensure_invoice_baseline(
        self, invoice: Mapping[str, Any], user: Mapping[str, Any] | None = None
    ) -> models.JournalEntry | None:
        """Post the pre-start baseline of an invoice issued before the start. Idempotent; ``None`` when the invoice is
        not a pre-start invoice, has no snapshot or its baseline has no lines (a settled invoice, EÜR/Ist)."""
        start = self.start()
        if start is None or not self.is_prestart(invoice):
            return None
        key = self.key(start.year, "invoice", str(invoice["id"]))
        existing = self.journal.get_by_event_key(key)
        if existing is not None:
            return existing
        uid = str((user or {}).get("id") or "system")
        fy = self.journal.ensure_year(start.year, uid)
        bases = Bases(cast(IncomeBasis, fy["income_basis"]), cast(VatBasis, fy["vat_basis"]))
        lines = self.baseline_lines(invoice, start, bases)
        if not lines:
            return None
        customer: Mapping[str, Any] = (invoice.get("issued") or {}).get("customer") or {}
        source = JournalSource(
            event="opening_invoice",
            created_by=uid,
            origin="opening",
            source_coll="invoices",
            source_id=str(invoice["id"]),
            source_number=invoice.get("number"),
            counterparty_id=customer.get("id"),
            cp_vat_id=customer.get("vat_id"),
            cp_country=customer.get("country"),
        )
        text = f"Opening item invoice {invoice.get('number') or invoice['id']}"[:200]
        return self.journal.post(key, start, text, source, self._counter(lines))


class PrestartPaymentPosting(PaymentPostingService):
    """Payments, refunds and reversals of an invoice dated before the start (baseline first, accounting 16 step 4/5a)."""

    def __init__(self, store: Repository, journal: JournalService | None = None):
        super().__init__(store, journal)
        self.opening = OpeningItemService(store, self.journal)

    def post_payment(
        self, invoice: Mapping[str, Any], row: Mapping[str, Any], user: Mapping[str, Any] | None = None
    ) -> models.JournalEntry | None:
        start = self.opening.start()
        if start is not None and self.opening.is_prestart(invoice):
            if (_day(row.get("date")) or start) < start:
                return None  # part of the baseline
            self.opening.ensure_invoice_baseline(invoice, user)
            invoice = {**invoice, "date": start.isoformat()}
        return super().post_payment(invoice, row, user)


class PrestartCreditPosting(CreditPostingService):
    """Credit notes, write-offs and write-off reversals of an invoice dated before the start (baseline first)."""

    def __init__(self, store: Repository, journal: JournalService | None = None):
        super().__init__(store, journal)
        self.opening = OpeningItemService(store, self.journal)

    def post_credit(
        self, invoice: Mapping[str, Any], note: Mapping[str, Any], user: Mapping[str, Any] | None = None
    ) -> models.JournalEntry | None:
        start = self.opening.start()
        if start is not None and self.opening.is_prestart(invoice):
            if (_day(note.get("date")) or start) < start:
                return None  # part of the baseline
            self.opening.ensure_invoice_baseline(invoice, user)
            invoice = {**invoice, "date": start.isoformat()}
        return super().post_credit(invoice, note, user)


class PrestartExpensePosting(ExpensePostingService):
    """The expense hook extended to an expense dated before the start and paid on/after it (step 5): its bill belongs
    to the opening entry, only the payment event posts (``expense:<id>:pay:r<n>``)."""

    def _prestart_open(self, rec: Mapping[str, Any]) -> date | None:
        """The start date when the expense is dated before it and paid on/after it, else ``None``."""
        start = accounting_start(self.store.settings())
        day, paid = _day(rec.get("date")), _day(rec.get("paid_on"))
        if start is not None and day is not None and paid is not None and day < start <= paid:
            return start
        return None

    def _entries(
        self,
        rec: Mapping[str, Any],
        rev: tuple[int, int],
        stored: Mapping[Part, Record | None] | None = None,
        year_closed: bool | None = None,
    ) -> dict[Part, Entry | None]:
        start = self._prestart_open(rec)
        if start is None:
            return super()._entries(rec, rev, stored, year_closed)
        fy = self.journal.ensure_year(start.year, "system")
        bases = Bases(cast(IncomeBasis, fy["income_basis"]), cast(VatBasis, fy["vat_basis"]))
        first_open = first_unlocked_from(self.store.list("period_locks"))
        engine = self._engine(rec, fy, rev)
        assert engine.paid_on is not None
        pay_first = self._engine_first(engine.paid_on, first_open, (stored or {}).get("pay"))
        closed = self._closed(engine.paid_on) if year_closed is None else year_closed
        pay = expense_pay_entry(engine, bases, pay_first, closed)
        return {"bill": None, "pay": self._dated(pay, pay_first)}

    def post_opening(self, rec: Mapping[str, Any], user: Mapping[str, Any] | None = None) -> models.JournalEntry | None:
        """The opening entry of an expense dated before the start and paid on/after it: accrual the open payable,
        EÜR the input VAT already deducted (``input_vat_clearing``), each against ``opening_balance`` (no dates).
        ``None`` for any other expense or when its bill holds no such line."""
        start = self._prestart_open(rec)
        if start is None:
            return None
        key = OpeningItemService.key(start.year, "expense", str(rec["id"]))
        existing = self.journal.get_by_event_key(key)
        if existing is not None:
            return existing
        uid = str((user or {}).get("id") or "system")
        fy = self.journal.ensure_year(start.year, uid)
        bases = Bases(cast(IncomeBasis, fy["income_basis"]), cast(VatBasis, fy["vat_basis"]))
        engine = self._engine(rec, fy, (1, 1))
        bill = expense_bill_entry(engine, bases, engine.date, False)
        lines: list[PostLine] = []
        if bill is not None:
            for ln in bill.lines:
                if str(ln.role) in EXPENSE_BALANCE_ROLES and ln.amount_cents:
                    plain = post_line(ln)
                    lines.append(
                        PostLine(amount_cents=plain.amount_cents, role=plain.role, open_item_id=plain.open_item_id)
                    )
        if not lines:
            return None
        total = sum(ln.amount_cents for ln in lines)
        lines.append(PostLine(role=OPENING_ROLE, amount_cents=-total))
        source = dataclasses.replace(self._source(rec, "bill", user, False), event="opening_expense", origin="opening")
        name = str(rec.get("vendor") or rec.get("description") or rec["id"])
        return self.journal.post(key, start, f"Opening item expense {name}"[:200], source, lines)
