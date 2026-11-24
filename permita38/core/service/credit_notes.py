"""Credit notes of issued invoices: storno, partial, write-off and discount (docs/specs/credit-notes-payments.md 4, accounting.md 9.2, 9.3, 11).

A credit note is a document in ``invoices`` with ``kind = credit_note``, positive lines, ``corrects_id`` = the invoice
and its own ``issued`` snapshot (``rates`` with net, VAT and gross in cents per rate group). The snapshot is the single
source of the credited amounts: ``invoice_state.derive`` (credited amount, cancelled) and
``postings.invoice_state_of`` (the 9.2 target rule) read it, nothing else. A credit note is issued on creation: number
from the sequence ``credit_note``, snapshot, posting through :class:`~permita38.core.service.postings.CreditPostingService`
(event key ``credit:<id>``, once the accounting is initialised), all in one transaction. A ``write_off`` is an internal voucher
(no number, no PDF); a ``discount`` is created by :meth:`CreditNoteService.settle_discount` only.
"""

import builtins
import copy
import time
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from permita38.core.accounting.postings_credit import discount_amounts, write_off_amounts
from permita38.core.accounting.postings_issue import rate_str
from permita38.core.accounting.types import Cents, InvoiceState, IssuedSnapshot, RateGroup
from permita38.core.errors import ServiceError
from permita38.core.money import vat_amount
from permita38.core.numbering import period_key
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service import invoice_state
from permita38.core.service.journal import JournalService
from permita38.core.service.locks import first_unlocked_from
from permita38.core.service.numbering import NumberConfig, NumberingService
from permita38.core.service.payments import MAX_PAYMENT_CENTS, PaymentService
from permita38.core.service.postings import CreditPostingService, credit_snapshot, invoice_state_of, stored_snapshot
from permita38.core.settings import effective_settings

__all__ = ["CREDIT_NOTE_PATTERN", "CREDIT_NOTE_RESET", "CreditNoteNumbering", "CreditNoteService"]

CREDIT_NOTE_PATTERN = "RK-{year}-{n:04d}"
CREDIT_NOTE_RESET = "yearly"
DOC_TYPE = "credit_note"
_NOW = Callable[[], float]


class CreditNoteNumbering(NumberingService):
    """The sequence ``credit_note`` on the generic numbering service: its own counter group, the settings
    ``credit_note_pattern`` / ``credit_note_reset`` (read with defaults until the settings page knows them, E11b), and
    the credit notes of the ``invoices`` collection as the documents in scope."""

    def config(self, doc_type: str = DOC_TYPE) -> NumberConfig:
        st = effective_settings(self.store.settings())
        return NumberConfig(
            DOC_TYPE,
            str(st.get("credit_note_pattern") or CREDIT_NOTE_PATTERN),
            str(st.get("credit_note_reset") or CREDIT_NOTE_RESET),
            False,
        )

    def _docs_in_scope(self, cfg: NumberConfig, period: str, reset: str) -> list[dict[str, Any]]:
        out = []
        for doc in self.store.list("invoices"):
            if doc.get("kind") != "credit_note":
                continue
            raw = str(doc.get("date") or "")[:10]
            try:
                day = date.fromisoformat(raw)
            except ValueError:
                continue
            if period_key(reset, day) == period:
                out.append(doc)
        return out


def _decimal(value: Any, what: str) -> Decimal:
    try:
        out = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ServiceError(422, f"{what} is not a number") from None
    if not out.is_finite():
        raise ServiceError(422, f"{what} is not a number")
    return out


def _cents(amount: Decimal) -> int:
    scaled = amount * 100
    if scaled != scaled.to_integral_value():
        raise ServiceError(422, "The amount has at most 2 decimal places")
    return int(scaled)


class CreditNoteService:
    def __init__(
        self,
        store: Repository,
        journal: JournalService | None = None,
        payments: PaymentService | None = None,
        numbering: NumberingService | None = None,
        now: _NOW = time.time,
    ):
        self.store = store
        self.payments = payments or PaymentService(store, journal, now=now)
        self.numbering = numbering or CreditNoteNumbering(store)
        self.posting = CreditPostingService(store, journal)
        self.now = now

    # ---------- helpers
    def _invoice(self, invoice_id: str) -> Record:
        inv = self.store.get("invoices", invoice_id)
        if not inv:
            raise ServiceError(404, "Not found")
        if inv.get("kind") == "credit_note":
            raise ServiceError(422, "A credit note cannot be credited")
        if inv.get("status", "draft") == "draft":
            raise ServiceError(409, "Only an issued invoice can be credited")
        return inv

    def _notes(self, invoice_id: str) -> builtins.list[Record]:
        return [n for n in self.store.find("invoices", corrects_id=invoice_id) if n.get("kind") == "credit_note"]

    @staticmethod
    def _out(note: Mapping[str, Any]) -> dict[str, Any]:
        out = dict(note)
        if isinstance(out.get("issued"), Mapping):
            out["issued"] = invoice_state.present_snapshot(out["issued"])
        return out

    @staticmethod
    def _parse_day(day: Any) -> date:
        try:
            return date.fromisoformat(str(day)[:10])
        except ValueError:
            raise ServiceError(422, "The credit note date is not a valid date") from None

    def _check_date(self, day: str, inv: Mapping[str, Any]) -> None:
        d = self._parse_day(day)
        if d < self._parse_day(inv.get("date")):
            raise ServiceError(422, "The credit note date lies before the invoice date")
        if d > self.payments.today():
            raise ServiceError(422, "The credit note date lies in the future")
        first_open = first_unlocked_from(self.store.list("period_locks"))
        if first_open is not None and d < first_open:
            raise ServiceError(409, "The credit note date lies in a locked period; use a date in an open month")

    @staticmethod
    def _remaining(snap: IssuedSnapshot, credited: Any) -> builtins.list[tuple[Decimal, int, int]]:
        """``(rate, net, vat)`` still creditable per rate group of the snapshot."""
        return [(g.rate, int(g.net) - int(k.net), int(g.vat) - int(k.vat)) for g, k in zip(snap.rates, credited)]

    @staticmethod
    def _line(rate: Decimal, net: int, text: str) -> dict[str, Any]:
        return {"description": text, "qty": 1, "price": invoice_state.cents_str(net), "vat": rate_str(rate)}

    def _amounts(
        self, remaining: builtins.list[tuple[Decimal, int, int]], kind: str, amounts: Mapping[str, Decimal | str] | None
    ) -> builtins.list[tuple[Decimal, int, int]]:
        """The credited ``(rate, net, vat)`` per rate group; 422 when it exceeds what is left."""
        if kind == "storno":
            out = [(r, n, v) for r, n, v in remaining if n > 0 or v > 0]
            if not out:
                raise ServiceError(422, "Nothing is left to credit")
            return out
        by_rate = {r: (n, v) for r, n, v in remaining}
        out = []
        seen: set[Decimal] = set()
        for key, amount in (amounts or {}).items():
            rate = _decimal(key, f"The rate {key}")
            if rate not in by_rate:
                raise ServiceError(422, f"The invoice has no rate group {key}")
            if rate in seen:
                raise ServiceError(422, f"The rate {key} is given twice")
            seen.add(rate)
            net = _cents(_decimal(amount, f"The amount for {key} %"))
            left_net, left_vat = by_rate[rate]
            if net <= 0:
                raise ServiceError(422, "The credited amount must be greater than 0")
            if net > left_net:
                raise ServiceError(
                    422,
                    f"The credited net for {key} % exceeds the remaining net {invoice_state.cents_str(left_net)}",
                )
            # the last cent of a group takes the VAT that is left, so a full credit leaves no residue
            vat = left_vat if net == left_net else min(int(vat_amount(Decimal(net) / 100, rate) * 100), left_vat)
            out.append((rate, net, vat))
        if not out:
            raise ServiceError(422, "amounts is required for a partial credit note")
        return sorted(out, key=lambda t: t[0], reverse=True)

    def _snapshot(
        self, inv: Mapping[str, Any], groups: builtins.list[tuple[Decimal, int, int]], day: str
    ) -> dict[str, Any]:
        return credit_snapshot(inv, groups, day, self.now())

    # ---------- writes
    def _issue(
        self,
        inv: Mapping[str, Any],
        *,
        credit_kind: str,
        day: str,
        reason: str,
        groups: builtins.list[tuple[Decimal, int, int]],
        lines: builtins.list[dict[str, Any]] | None,
        user: Mapping[str, Any] | None,
    ) -> Record:
        """Store and post one credit document. A write-off is an internal voucher: no number (its journal entry
        identifies it), so no PDF is ever rendered for it; every other kind takes the next number."""
        record: dict[str, Any] = {
            "kind": "credit_note",
            "credit_kind": credit_kind,
            "corrects_id": inv["id"],
            "customer_id": inv.get("customer_id"),
            "date": day,
            "status": "sent",
            "lines": lines if lines is not None else [self._line(r, n, reason) for r, n, _v in groups],
            "reason": reason,
            "issued": self._snapshot(inv, groups, day),
        }
        if credit_kind != "write_off":
            record["number"] = self.numbering.allocate(DOC_TYPE, date_of(day))
        for key in ("project_id", "tax_treatment"):
            if inv.get(key):
                record[key] = inv[key]
        note = self.store.save("invoices", record)
        self.posting.post_credit(inv, note, user)
        return note

    def _state(self, inv: Mapping[str, Any]) -> tuple[IssuedSnapshot, builtins.list[Record], InvoiceState]:
        snap = stored_snapshot(inv)
        if snap is None:
            raise ServiceError(409, "The invoice has no issued snapshot")
        notes = self._notes(inv["id"])
        kept = sum(int(r["amount_cents"]) for r in self.store.find("payments", document_id=inv["id"]))
        return snap, notes, invoice_state_of(snap, notes, kept)

    @staticmethod
    def _nonzero(groups: tuple[RateGroup, ...]) -> builtins.list[tuple[Decimal, int, int]]:
        return [(g.rate, int(g.net), int(g.vat)) for g in groups if g.net or g.vat]

    def create(
        self,
        invoice_id: str,
        *,
        credit_kind: str,
        date: str,
        reason: str,
        amounts: Mapping[str, Decimal | str] | None = None,
        user: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue a ``storno`` (the whole remaining gross), ``partial`` (the given net per rate group) or
        ``write_off`` (the open amount, only when it is > 0) credit note. 422 when it exceeds the remaining net of a
        rate group, 409 when the date lies in a locked month."""
        if credit_kind not in ("storno", "partial", "write_off"):
            raise ServiceError(422, "credit_kind must be storno, partial or write_off")
        with self.store.transaction():
            inv = self.payments.materialize(self._invoice(invoice_id), user)
            self._check_date(date, inv)
            snap, notes, state = self._state(inv)
            day = date[:10]
            if credit_kind == "write_off":
                groups = self._nonzero(write_off_amounts(state))
                if not groups:
                    raise ServiceError(422, "Nothing is open to write off")
                return self._out(
                    self._issue(
                        inv, credit_kind=credit_kind, day=day, reason=reason, groups=groups, lines=None, user=user
                    )
                )
            groups = self._amounts(self._remaining(snap, state.credited), credit_kind, amounts)
            lines = None
            if credit_kind == "storno" and not notes and inv.get("lines"):
                lines = copy.deepcopy(inv["lines"])  # the whole invoice: the same lines, quantities and prices
            note = self._issue(
                inv, credit_kind=credit_kind, day=day, reason=reason, groups=groups, lines=lines, user=user
            )
            return self._out(note)

    def settle_discount(
        self,
        invoice_id: str,
        *,
        date: str,
        amount_paid: Decimal,
        note: str | None = None,
        user: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Settle with a cash discount (spec 3, accounting 9.3): first a numbered ``discount`` credit note for the
        rest (the complement of the cumulative allocation, so the payment settles exactly), then the payment of
        ``amount_paid``, in one transaction. 422 when ``amount_paid`` already covers the open amount."""
        cents = _cents(amount_paid)
        if cents <= 0 or cents > MAX_PAYMENT_CENTS:
            raise ServiceError(422, "The amount must be greater than 0 and at most 10,000,000.00")
        with self.store.transaction():
            inv = self.payments.materialize(self._invoice(invoice_id), user)
            self.payments._check_date(inv, date)  # invoice date and today; a locked month is allowed for the payment
            _snap, _notes, state = self._state(inv)
            groups = self._nonzero(discount_amounts(state, Cents(cents)))
            if not groups:
                raise ServiceError(422, "The amount paid covers the open amount; record a payment instead")
            # the payment keeps its date (and may drift); the credit note is dated in an open month (accounting 11)
            first_open = first_unlocked_from(self.store.list("period_locks"))
            credit_day = max(self._parse_day(date), first_open) if first_open is not None else self._parse_day(date)
            credit = self._issue(
                inv,
                credit_kind="discount",
                day=credit_day.isoformat(),
                reason=note or "Cash discount",
                groups=groups,
                lines=None,
                user=user,
            )
            result = self.payments.record(
                invoice_id,
                date=date,
                amount=amount_paid,
                kind="payment",
                method="bank",
                note=note,
                user=user,
            )
            result["credit_note"] = self._out(credit)
            return result

    # ---------- reads (last: the name shadows the builtin inside the class body)
    def list(self, invoice_id: str) -> builtins.list[dict[str, Any]]:
        if not self.store.get("invoices", invoice_id):
            raise ServiceError(404, "Not found")
        rows = sorted(self._notes(invoice_id), key=lambda n: (str(n.get("date") or ""), str(n.get("number") or "")))
        return [self._out(n) for n in rows]


def date_of(day: str) -> date:
    return date.fromisoformat(day[:10])
