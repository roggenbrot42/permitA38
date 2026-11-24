"""Adapter between an issued invoice and the journal (docs/specs/accounting.md 7, 9.1, 11, 16).

:func:`build_snapshot` freezes the amounts of an invoice (the ``issued`` snapshot, spec 9.1); :class:`IssuePostingService`
turns the snapshot into the engine event (``core/accounting/postings_issue.py``, pure) and posts it through
:class:`~permita38.core.service.journal.JournalService` with the event key ``issue:<invoice id>``. Nothing posts before
the accounting is initialised (settings key ``accounting.start``) or for an invoice dated before the start. No SQL here.
"""

import dataclasses
import time
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any, cast, get_args

from permita38.core import models
from permita38.core.accounting.postings_credit import CreditState
from permita38.core.accounting.postings_issue import issue_entry, rate_str, snapshot_from_document
from permita38.core.accounting.postings_targets import apply_event, event_entry
from permita38.core.accounting.types import (
    Bases,
    Cents,
    IncomeBasis,
    InvoiceState,
    IssuedSnapshot,
    Line,
    PaymentEvent,
    RateGroup,
    Role,
    Treatment,
    VatBasis,
)
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service.journal import JournalService, JournalSource, PostLine
from permita38.core.service.locks import first_unlocked_from, vat_date_for
from permita38.core.settings import accounting_start

__all__ = ["IssuePostingService", "PaymentPostingService", "build_snapshot", "invoice_state_of", "stored_snapshot"]

SUPPORTED_TREATMENTS: tuple[str, ...] = get_args(Treatment)


def _fiscal_year(store: Repository, year: int) -> Record | None:
    rows = store.find("fiscal_years", year=year)
    return rows[0] if rows else None


def build_snapshot(
    store: Repository,
    doc: Mapping[str, Any],
    settings: Mapping[str, Any],
    invoice_date: date,
    *,
    clock: float | None = None,
) -> dict[str, Any]:
    """The ``issued`` snapshot of an invoice (spec 9.1), amounts in integer cents from ``core/money.py``.

    The regime comes from the invoice's fiscal year (``small_business``) or the settings (``kleinunternehmer``);
    a small-business year forces the treatment ``small_business`` and rate 0. ``rules_version`` is the one the fiscal
    year was opened with (``None`` when the year does not exist yet). Reserved treatments are refused with 422.
    """
    treatment = doc.get("tax_treatment") or "standard"
    if treatment not in SUPPORTED_TREATMENTS:
        raise ServiceError(422, "This tax treatment is not supported in this release")
    fy = _fiscal_year(store, invoice_date.year)
    eff = dict(settings)
    if fy is not None and fy.get("small_business"):
        eff["kleinunternehmer"] = True
    try:
        snap = snapshot_from_document(
            doc, eff, document_id=str(doc["id"]), treatment=cast(Treatment, treatment), invoice_date=invoice_date
        )
    except ValueError as exc:
        raise ServiceError(422, str(exc)) from exc
    customer: Mapping[str, Any] = (store.get("customers", doc["customer_id"]) if doc.get("customer_id") else None) or {}
    return {
        "regime": snap.regime,
        "treatment": snap.treatment,
        "rates": {rate_str(g.rate): {"net": int(g.net), "vat": int(g.vat), "gross": int(g.gross)} for g in snap.rates},
        "gross": int(snap.gross),
        "perf": snap.perf.isoformat(),
        "customer": {
            "id": customer.get("id"),
            "name": customer.get("name"),
            "vat_id": customer.get("vat_id"),
            "country": customer.get("country"),
        },
        "issued_at": int(time.time() if clock is None else clock),
        "rules_version": fy.get("rules_version") if fy is not None else None,
    }


def stored_snapshot(doc: Mapping[str, Any]) -> IssuedSnapshot | None:
    """The engine view of a stored ``issued`` snapshot; ``None`` for a draft or an invoice without one."""
    raw = doc.get("issued")
    if not isinstance(raw, dict) or not isinstance(raw.get("rates"), dict):
        return None
    groups = sorted(
        (RateGroup(Decimal(r), Cents(int(v["net"])), Cents(int(v["vat"]))) for r, v in raw["rates"].items()),
        key=lambda g: g.rate,
        reverse=True,
    )
    return IssuedSnapshot(
        document_id=str(doc["id"]),
        regime=raw["regime"],
        treatment=raw["treatment"],
        rates=tuple(groups),
        perf=date.fromisoformat(str(raw["perf"])),
    )


def post_line(ln: Line) -> PostLine:
    return PostLine(
        amount_cents=int(ln.amount_cents),
        role=str(ln.role),
        tax_key=ln.tax_key,
        tax_kind=ln.tax_kind,
        tax_rate=ln.tax_rate,
        tax_base_cents=None if ln.tax_base_cents is None else int(ln.tax_base_cents),
        vat_date=ln.vat_date,
        income_date=ln.income_date,
        open_item_id=ln.open_item_id,
    )


class IssuePostingService:
    def __init__(self, store: Repository, journal: JournalService | None = None):
        self.store = store
        self.journal = journal or JournalService(store)

    def post_issue(
        self, invoice: Mapping[str, Any], user: Mapping[str, Any] | None = None
    ) -> models.JournalEntry | None:
        """Post the issue entry of an invoice that carries its snapshot. Idempotent (an existing ``issue:<id>``
        entry is returned unchanged). ``None`` when nothing posts: accounting not initialised, invoice dated before the
        start, a credit-note voucher, or an engine result without lines. 409 when the invoice date lies in a locked
        month or the fiscal year is closed or missing (only when initialised)."""
        start = accounting_start(self.store.settings())
        if start is None or invoice.get("credit_kind"):
            return None
        invoice_date = date.fromisoformat(str(invoice["date"])[:10])
        if invoice_date < start:
            return None
        key = f"issue:{invoice['id']}"
        existing = self.journal.get_by_event_key(key)
        if existing is not None:
            return existing
        snap = stored_snapshot(invoice)
        if snap is None:
            return None
        fy = self.journal.ensure_year(invoice_date.year, str((user or {}).get("id") or "system"))
        first_open = first_unlocked_from(self.store.list("period_locks"))
        if fy.get("closed_at") is not None or (first_open is not None and invoice_date < first_open):
            raise ServiceError(409, "The invoice date lies in a locked period; use a date in an open month")
        bases = Bases(cast(IncomeBasis, fy["income_basis"]), cast(VatBasis, fy["vat_basis"]))
        entry = issue_entry(snap, bases, invoice_date)
        if entry is None:
            return None
        customer: Mapping[str, Any] = (invoice.get("issued") or {}).get("customer") or {}
        source = JournalSource(
            event="issue",
            created_by=str((user or {}).get("id") or "system"),
            source_coll="invoices",
            source_id=str(invoice["id"]),
            source_number=invoice.get("number"),
            counterparty_id=customer.get("id"),
            cp_vat_id=customer.get("vat_id"),
            cp_country=customer.get("country"),
        )
        text = f"Invoice {invoice.get('number') or invoice['id']}"[:200]
        return self.journal.post(
            entry.event_key, entry.document_date, text, source, [post_line(ln) for ln in entry.lines]
        )


def invoice_state_of(snap: IssuedSnapshot, credit_notes: Iterable[Mapping[str, Any]], kept_cents: int) -> InvoiceState:
    """The 9.2 state of an invoice: its snapshot, the credited net and VAT per rate group summed from the credit
    documents' own snapshots (``issued.rates``; a write-off reversal is stored negative) and the cash kept.
    Discounts and write-offs are tracked separately (``CreditState``) because they post to their own accounts."""
    index = {g.rate: i for i, g in enumerate(snap.rates)}
    credited = [[0, 0] for _ in snap.rates]
    disc = [[0, 0] for _ in snap.rates]
    wo = [[0, 0] for _ in snap.rates]
    for note in credit_notes:
        raw = note.get("issued")
        rates = raw.get("rates") if isinstance(raw, Mapping) else None
        if not isinstance(rates, Mapping):
            continue
        kind = note.get("credit_kind")
        for key, val in rates.items():
            i = index.get(Decimal(str(key)))
            if i is None:
                continue
            net, vat = int(val["net"]), int(val["vat"])
            targets = [credited[i]]
            if kind == "discount":
                targets.append(disc[i])
            if kind in ("write_off", "write_off_reversal"):
                targets.append(wo[i])
            for t in targets:
                t[0] += net
                t[1] += vat

    def groups(rows: list[list[int]]) -> tuple[RateGroup, ...]:
        return tuple(RateGroup(g.rate, Cents(r[0]), Cents(r[1])) for g, r in zip(snap.rates, rows, strict=True))

    if any(v for row in (*disc, *wo) for v in row):
        return CreditState(snap, groups(credited), Cents(kept_cents), groups(disc), groups(wo))
    return InvoiceState(snap, groups(credited), Cents(kept_cents))


class PaymentPostingService:
    """Posts the payment events of an issued invoice with the target rule (accounting 9.2, 11)."""

    def __init__(self, store: Repository, journal: JournalService | None = None):
        self.store = store
        self.journal = journal or JournalService(store)

    def post_payment(
        self, invoice: Mapping[str, Any], row: Mapping[str, Any], user: Mapping[str, Any] | None = None
    ) -> models.JournalEntry | None:
        """Post ``targets(after) - targets(before)`` for one stored ``payments`` row (a payment, refund or reversal;
        ``amount_cents`` signed). ``before`` = every other row of the invoice. ``None`` when nothing posts: accounting
        not initialised, the invoice is dated before the start or has no snapshot, or the delta has no lines.
        A tax line keeps a locked VAT date only when the VAT itself changes (drift case 2); others move."""
        start = accounting_start(self.store.settings())
        if start is None or invoice.get("credit_kind"):
            return None
        invoice_date = date.fromisoformat(str(invoice["date"])[:10])
        snap = stored_snapshot(invoice)
        if invoice_date < start or snap is None:
            return None
        uid = str((user or {}).get("id") or "system")
        fy = self.journal.ensure_year(invoice_date.year, uid)
        bases = Bases(cast(IncomeBasis, fy["income_basis"]), cast(VatBasis, fy["vat_basis"]))
        others = [r for r in self.store.find("payments", document_id=invoice["id"]) if r["id"] != row["id"]]
        notes = [n for n in self.store.find("invoices", corrects_id=invoice["id"]) if n.get("kind") == "credit_note"]
        before = invoice_state_of(snap, notes, sum(int(r["amount_cents"]) for r in others))
        day = date.fromisoformat(str(row["date"])[:10])
        role = Role("cash" if row.get("method") == "cash" else "bank")
        event = PaymentEvent(str(row["id"]), day, Cents(int(row["amount_cents"])), role)
        try:
            after = apply_event(before, event)
        except ValueError as exc:
            raise ServiceError(422, str(exc)) from exc
        entry = event_entry(before, after, bases, event)
        if entry is None:
            return None
        first_open = first_unlocked_from(self.store.list("period_locks"))
        lines: list[PostLine] = []
        for ln in entry.lines:
            post = post_line(ln)
            if post.vat_date is not None and first_open is not None:
                if ln.tax_kind == "tax":  # the VAT itself changes: keep the date, the journal flags the drift
                    vat_day, _ = vat_date_for("cash_event", post.vat_date, first_open)
                else:  # a statistical base never drifts
                    vat_day = max(post.vat_date, first_open)
                post = dataclasses.replace(post, vat_date=vat_day)
            lines.append(post)
        customer: Mapping[str, Any] = (invoice.get("issued") or {}).get("customer") or {}
        source = JournalSource(
            event="payment",
            created_by=uid,
            source_coll="payments",
            source_id=str(row["id"]),
            source_number=invoice.get("number"),
            counterparty_id=customer.get("id"),
            cp_vat_id=customer.get("vat_id"),
            cp_country=customer.get("country"),
        )
        label = {"refund": "Refund", "reversal": "Reversal of payment"}.get(str(row.get("kind")), "Payment")
        text = f"{label} {invoice.get('number') or invoice['id']}"[:200]
        return self.journal.post(entry.event_key, entry.document_date, text, source, lines)
