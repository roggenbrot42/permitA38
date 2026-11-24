"""Credit notes of issued invoices: storno and partial (docs/specs/credit-notes-payments.md 4, accounting.md 9.2, 9.3, 11).

A credit note is a document in ``invoices`` with ``kind = credit_note``, positive lines, ``corrects_id`` = the invoice
and its own ``issued`` snapshot (``rates`` with net, VAT and gross in cents per rate group). The snapshot is the single
source of the credited amounts: ``invoice_state.derive`` (credited amount, cancelled) and
``postings.invoice_state_of`` (the 9.2 target rule) read it, nothing else. A credit note is issued on creation: number
from the sequence ``credit_note``, snapshot, posting through :class:`~permita38.core.service.postings.CreditPostingService`
(event key ``credit:<id>``, once the accounting is initialised), all in one transaction. Write-off and discount are
not here (E12).
"""

import builtins
import copy
import time
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from permita38.core.accounting.postings_issue import rate_str
from permita38.core.accounting.types import IssuedSnapshot
from permita38.core.errors import ServiceError
from permita38.core.money import vat_amount
from permita38.core.numbering import period_key
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service import invoice_state
from permita38.core.service.journal import JournalService
from permita38.core.service.locks import first_unlocked_from
from permita38.core.service.numbering import NumberConfig, NumberingService
from permita38.core.service.payments import PaymentService
from permita38.core.service.postings import CreditPostingService, invoice_state_of, stored_snapshot
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
        self.payments = payments or PaymentService(store, journal)
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

    def _check_date(self, day: str) -> None:
        first_open = first_unlocked_from(self.store.list("period_locks"))
        if first_open is not None and date.fromisoformat(day[:10]) < first_open:
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
            rate = Decimal(key)
            if rate not in by_rate:
                raise ServiceError(422, f"The invoice has no rate group {key}")
            if rate in seen:
                raise ServiceError(422, f"The rate {key} is given twice")
            seen.add(rate)
            net = _cents(Decimal(str(amount)))
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
        raw = inv["issued"]
        return {
            "regime": raw["regime"],
            "treatment": raw["treatment"],
            "rates": {rate_str(r): {"net": n, "vat": v, "gross": n + v} for r, n, v in groups},
            "gross": sum(n + v for _r, n, v in groups),
            "perf": day,
            "customer": copy.deepcopy(raw.get("customer")),
            "issued_at": int(self.now()),
            "rules_version": raw.get("rules_version"),
        }

    # ---------- writes
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
        """Issue a ``storno`` (the whole remaining gross) or ``partial`` (the given net per rate group) credit note.
        422 when it exceeds the remaining net of a rate group, 409 when the date lies in a locked month."""
        if credit_kind not in ("storno", "partial"):
            raise ServiceError(501, "not implemented yet")
        with self.store.transaction():
            inv = self.payments.materialize(self._invoice(invoice_id), user)
            self._check_date(date)
            snap = stored_snapshot(inv)
            if snap is None:
                raise ServiceError(409, "The invoice has no issued snapshot")
            notes = self._notes(inv["id"])
            kept = sum(int(r["amount_cents"]) for r in self.store.find("payments", document_id=inv["id"]))
            state = invoice_state_of(snap, notes, kept)
            groups = self._amounts(self._remaining(snap, state.credited), credit_kind, amounts)
            if credit_kind == "storno" and not notes and inv.get("lines"):
                lines = copy.deepcopy(inv["lines"])  # the whole invoice: the same lines, quantities and prices
            else:
                lines = [self._line(r, n, reason) for r, n, _v in groups]
            day = date[:10]
            record: dict[str, Any] = {
                "kind": "credit_note",
                "credit_kind": credit_kind,
                "corrects_id": inv["id"],
                "customer_id": inv.get("customer_id"),
                "date": day,
                "status": "sent",
                "lines": lines,
                "reason": reason,
                "issued": self._snapshot(inv, groups, day),
                "number": self.numbering.allocate(DOC_TYPE, date_of(day)),
            }
            for key in ("project_id", "tax_treatment"):
                if inv.get(key):
                    record[key] = inv[key]
            note = self.store.save("invoices", record)
            self.posting.post_credit(inv, note, user)
            return self._out(note)

    # ---------- reads (last: the name shadows the builtin inside the class body)
    def list(self, invoice_id: str) -> builtins.list[dict[str, Any]]:
        if not self.store.get("invoices", invoice_id):
            raise ServiceError(404, "Not found")
        rows = sorted(self._notes(invoice_id), key=lambda n: (str(n.get("date") or ""), str(n.get("number") or "")))
        return [self._out(n) for n in rows]


def date_of(day: str) -> date:
    return date.fromisoformat(day[:10])
