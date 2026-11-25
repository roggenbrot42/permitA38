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
* A payment or credit note dated before the start entered after the initialisation, or a pre-start expense whose
  ``paid_on`` is set or changed, posts an **opening adjustment** ``open:<year>:<kind>:<id>:r<n>`` (dated the start date,
  against ``opening_balance``, balance-sheet roles only): the difference between the new and the previous baseline.
  Never a normal event. The base posting services (``postings.py``, ``expense_postings.py``) call
  :meth:`OpeningItemService.sync_invoice` / :meth:`OpeningItemService.sync` for this.

The opening entry is dated the start date even when that month is locked (it is an opening entry): the journal does
not refuse it, it books it on the first open day (``booking_date``) and keeps the start date as document date.
No SQL here.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, cast

from permita38.core import models
from permita38.core.accounting.postings_targets import targets
from permita38.core.accounting.types import Bases, IncomeBasis, InvoiceState, VatBasis
from permita38.core.repository import Repository
from permita38.core.service import invoice_state
from permita38.core.service.expense_postings import ExpensePostingService
from permita38.core.service.journal import JournalService, JournalSource, PostLine
from permita38.core.service.postings import (
    CreditPostingService,
    PaymentPostingService,
    invoice_state_of,
    stored_snapshot,
)
from permita38.core.settings import accounting_start

__all__ = [
    "OpeningItemService",
    "PrestartCreditPosting",
    "PrestartExpensePosting",
    "PrestartPaymentPosting",
]

# Thin names kept for the importers; the pre-start handling lives in the base posting services.
PrestartPaymentPosting = PaymentPostingService
PrestartCreditPosting = CreditPostingService
PrestartExpensePosting = ExpensePostingService

OPENING_ROLE = "opening_balance"
EXPENSE_BALANCE_ROLES = ("payables", "input_vat_clearing")
OPEN_ITEM_ROLES = ("receivables", "payables")


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

    def sync(
        self, kind: str, ident: str, desired: Sequence[PostLine], source: JournalSource, text: str
    ) -> models.JournalEntry | None:
        """Bring the opening items of ``kind`` / ``ident`` to the ``desired`` balance-sheet lines: the first call posts
        ``open:<year>:<kind>:<id>``, a later difference posts the adjustment ``...:r<n>`` (new minus previous baseline,
        each against ``opening_balance``). Idempotent: no difference posts nothing (the base entry is returned)."""
        start = self.start()
        if start is None:
            return None
        base = self.key(start.year, kind, ident)
        posted: dict[str, int] = {}
        count = 0
        while True:
            row = self.store.get_journal_entry_by_event_key(base if count == 0 else f"{base}:r{count}")
            if row is None:
                break
            count += 1
            for ln in row["lines"]:
                if ln.get("role") != OPENING_ROLE:
                    posted[str(ln["role"])] = posted.get(str(ln["role"]), 0) + int(ln["amount_cents"])
        want: dict[str, int] = {}
        for ln in desired:
            if ln.role != OPENING_ROLE:
                want[str(ln.role)] = want.get(str(ln.role), 0) + ln.amount_cents
        delta = {r: want.get(r, 0) - posted.get(r, 0) for r in (*want, *(r for r in posted if r not in want))}
        delta = {r: v for r, v in delta.items() if v}
        if not delta:
            return self.journal.get_by_event_key(base) if count else None
        lines = [
            PostLine(role=r, amount_cents=v, open_item_id=ident.split(":")[0] if r in OPEN_ITEM_ROLES else None)
            for r, v in delta.items()
        ]
        key = base if count == 0 else f"{base}:r{count}"
        return self.journal.post(key, start, text, source, self._counter(lines))

    def sync_invoice(
        self, invoice: Mapping[str, Any], user: Mapping[str, Any] | None = None
    ) -> models.JournalEntry | None:
        """Post the pre-start baseline of an invoice issued before the start, or the adjustment for a pre-start payment
        or credit note entered since (idempotent). ``None`` when the invoice is not a pre-start invoice, has no snapshot
        or nothing was ever to post (a settled invoice, EÜR/Ist)."""
        start = self.start()
        if start is None or not self.is_prestart(invoice):
            return None
        uid = str((user or {}).get("id") or "system")
        fy = self.journal.ensure_year(start.year, uid)
        bases = Bases(cast(IncomeBasis, fy["income_basis"]), cast(VatBasis, fy["vat_basis"]))
        lines = self.baseline_lines(invoice, start, bases)
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
        return self.sync("invoice", str(invoice["id"]), lines, source, text)

    ensure_invoice_baseline = sync_invoice
