"""The review list of the accounting initialisation and its resolutions (docs/specs/accounting.md 16 steps 5a, 5b, 6,
8, 9; credit-notes-payments.md 2.3), work package E24.

:class:`ReviewItem` is one row the initialisation cannot decide alone; ``RESOLUTIONS`` names, per code, the actions the
user may answer with. :class:`ReviewApplier` applies the answers: it changes the stored record (expense treatment,
legacy paid date or status) or issues the storno credit note (and the refund) of a cancelled invoice. It also issues
the storno of every legacy cancelled invoice that was never paid, without asking ("migrated cancellation"), and stores
the returns filed outside the app. Nothing is guessed: a row without an answer keeps the initialisation from running.
No SQL here.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from permita38.core import models
from permita38.core.errors import ServiceError
from permita38.core.money import dec, q2
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service.credit_notes import CreditNoteService
from permita38.core.service.expense_postings import ExpensePostingService
from permita38.core.service.payments import PaymentService
from permita38.core.service.vat_returns import VatReturnService

__all__ = [
    "MIGRATED_CANCELLATION",
    "RESOLUTIONS",
    "TREATMENTS",
    "ExternalPeriod",
    "ReviewApplier",
    "ReviewItem",
    "external_periods",
]

MIGRATED_CANCELLATION = "migrated cancellation"
TREATMENTS = ("standard", "no_input_vat", "reverse_charge_eu_service", "reverse_charge_third_country")

# code -> the actions a resolution may use (``confirm`` = accept the stated outcome)
RESOLUTIONS: dict[str, tuple[str, ...]] = {
    "vat_before_start": ("confirm",),  # "declared outside the app"
    "expense_prepaid": ("confirm",),
    "rc_without_supplier": ("treatment",),
    "rc_stored_vat": ("confirm", "treatment"),  # confirm = recompute the self-assessed VAT
    "legacy_cancelled_paid": ("credit_note", "confirm"),  # storno credit note and refund / keep as paid
    "legacy_paid_no_date": ("confirm", "date"),  # confirm = the invoice date
    "legacy_paid_before_invoice": ("confirm", "date"),
}


@dataclass(frozen=True)
class ReviewItem:
    """One record the initialisation cannot decide alone (accounting.md 16 step 9)."""

    key: str
    code: str
    collection: str
    id: str
    number: str | None
    message: str

    @property
    def resolutions(self) -> tuple[str, ...]:
        return RESOLUTIONS[self.code]

    @property
    def treatments(self) -> tuple[str, ...]:
        return TREATMENTS if "treatment" in self.resolutions else ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "code": self.code,
            "collection": self.collection,
            "id": self.id,
            "number": self.number,
            "message": self.message,
            "resolutions": list(self.resolutions),
            "treatments": list(self.treatments),
        }


@dataclass(frozen=True)
class ExternalPeriod:
    """A VAT period on/after the start that has ended and has no return of the app (step 5b)."""

    year: int
    kind: str
    no: int
    from_date: date
    to_date: date

    @property
    def key(self) -> str:
        return f"external_return:{self.year}:{self.kind}:{self.no}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "kind": self.kind,
            "no": self.no,
            "from": self.from_date.isoformat(),
            "to": self.to_date.isoformat(),
        }


def external_periods(start: date, today: date, kind: str) -> list[ExternalPeriod]:
    """The ended periods of ``kind`` (``month`` / ``quarter``) on/after ``start`` (a month start), before ``today``."""
    if kind not in ("month", "quarter"):
        return []
    step = 1 if kind == "month" else 3
    out: list[ExternalPeriod] = []
    year, month = start.year, start.month
    if kind == "quarter":
        month = (month - 1) // 3 * 3 + 1
    while True:
        ny, nm = (year, month + step) if month + step <= 12 else (year + 1, month + step - 12)
        lo, hi = date(year, month, 1), date(ny, nm, 1) - timedelta(days=1)
        if hi >= today:
            return out
        if hi >= start:
            no = month if kind == "month" else (month - 1) // 3 + 1
            out.append(ExternalPeriod(year, kind, no, lo, hi))
        year, month = ny, nm


def _cents(value: Any) -> int:
    return int(q2(dec(value)) * 100)


class ReviewApplier:
    """Applies the resolutions before the replay (accounting is not initialised yet: the credit notes and payments
    created here are recorded, they post when the replay reaches them)."""

    def __init__(
        self,
        store: Repository,
        expense_posting: ExpensePostingService,
        credit_notes: CreditNoteService,
        payments: PaymentService,
        today: Callable[[], date] = date.today,
    ):
        self.today = today
        self.store = store
        self.expense_posting = expense_posting
        self.credit_notes = credit_notes
        self.payments = payments

    # ------------------------------------------------------------------ the answers
    def validate(self, items: list[ReviewItem], answers: Mapping[str, models.ReviewResolutionIn]) -> None:
        by_key = {i.key: i for i in items}
        for key, ans in answers.items():
            item = by_key.get(key)
            if item is None:
                raise ServiceError(422, f"Unknown review key {key}")
            if ans.action not in item.resolutions:
                raise ServiceError(422, f"{key}: the action must be one of {', '.join(item.resolutions)}")
            if ans.action == "date":
                inv = self.store.get(item.collection, item.id) or {}
                if ans.date and str(ans.date)[:10] < str(inv.get("date") or "")[:10]:
                    raise ServiceError(422, f"{key}: the date lies before the invoice date")
                if ans.date and str(ans.date)[:10] > self.today().isoformat():
                    raise ServiceError(422, f"{key}: the date lies in the future")

    def apply(
        self,
        items: list[ReviewItem],
        answers: Mapping[str, models.ReviewResolutionIn],
        user: Mapping[str, Any],
        start: date,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}

        def bump(name: str) -> None:
            counts[name] = counts.get(name, 0) + 1

        for item in items:
            ans = answers[item.key]
            rec = self.store.get(item.collection, item.id)
            if rec is None:
                raise ServiceError(409, f"{item.key}: the record no longer exists")
            if item.code in ("rc_without_supplier", "rc_stored_vat"):
                self._expense_treatment(rec, ans)
                bump("treatment")
            elif item.code == "legacy_cancelled_paid":
                if ans.action == "credit_note":
                    self._storno_and_refund(rec, user)
                    bump("credit_note")
                else:
                    self._keep_as_paid(rec)
            elif item.code == "expense_prepaid":
                # nothing to change: the record keeps its paid_on; the posting turns the pre-start payment into the
                # opening item ``open:<year>:expense:<id>:prepaid``
                bump("prepaid")
            elif ans.action == "date":
                self.store.save("invoices", {**rec, "paid_date": ans.date})
                bump("paid_date")
            elif item.code == "legacy_paid_no_date":  # confirm: the invoice date
                self.store.save("invoices", {**rec, "paid_date": str(rec["date"])[:10]})
                bump("paid_date")
            # vat_before_start / legacy_paid_before_invoice confirm: nothing to change
        return counts

    def _expense_treatment(self, rec: Record, ans: models.ReviewResolutionIn) -> None:
        """Set the chosen treatment, or (confirm) the one the supplier gives; ``prepare`` then normalises the record
        like a save does: a reverse-charge treatment recomputes the self-assessed VAT from the net."""
        data = dict(rec)
        data["tax_treatment"] = ans.treatment or self.expense_posting.resolve_treatment(rec)
        self.expense_posting.prepare(data, rec)
        self.store.save("expenses", data)

    def _keep_as_paid(self, inv: Record) -> None:
        rows = self.store.find("payments", document_id=inv["id"])
        self.store.save("invoices", {**inv, "status": "sent" if rows else "paid"})

    def _storno_and_refund(self, inv: Record, user: Mapping[str, Any]) -> None:
        """Storno of the whole invoice and a refund of the cash kept, both dated the later of the invoice date and
        the last payment date (the cancel date is unknown)."""
        rows = self.store.find("payments", document_id=inv["id"])
        if not rows:  # legacy: the invoice was marked cancelled but carries a paid_date; record that payment first
            paid = str(inv.get("paid_date") or inv["date"])[:10]
            inv = self.payments.materialize(
                self.store.save("invoices", {**inv, "status": "paid", "paid_date": paid}), user
            )
            self.store.save("invoices", {**inv, "status": "cancelled"})
            rows = self.store.find("payments", document_id=inv["id"])
        day = max([str(inv["date"])[:10], *(str(r["date"])[:10] for r in rows)])
        self.credit_notes.create(
            str(inv["id"]), credit_kind="storno", date=day, reason=MIGRATED_CANCELLATION, user=user
        )
        kept = sum(int(r["amount_cents"]) for r in self.store.find("payments", document_id=inv["id"]))
        if kept > 0:
            self.payments.record(
                str(inv["id"]),
                date=day,
                amount=Decimal(kept) / 100,
                kind="refund",
                note=MIGRATED_CANCELLATION,
                user=user,
            )

    def storno_unpaid(self, invoices: list[Record], user: Mapping[str, Any]) -> int:
        """Step 8: a legacy cancelled invoice that was never paid gets its storno credit note, dated the invoice date
        (the cancel date is unknown)."""
        for inv in invoices:
            self.credit_notes.create(
                str(inv["id"]),
                credit_kind="storno",
                date=str(inv["date"])[:10],
                reason=MIGRATED_CANCELLATION,
                user=user,
            )
        return len(invoices)

    # ------------------------------------------------------------------ step 5b
    def returns_to_store(
        self, returns: list[models.ExternalReturnIn], periods: list[ExternalPeriod]
    ) -> list[models.ExternalReturnIn]:
        """The answers that carry a return (not ``none_filed``), each for a period of :func:`external_periods`, in
        period order."""
        allowed = {(p.year, p.kind, p.no): p for p in periods}
        for r in returns:
            period = allowed.get((r.year, r.kind, r.no))
            if period is None:
                raise ServiceError(
                    422, f"external_returns: {r.year} {r.kind} {r.no} is not an ended period on/after the start"
                )
            if r.filed_on is not None:
                filed = str(r.filed_on)[:10]
                if filed <= period.to_date.isoformat():
                    raise ServiceError(422, f"external_returns: {r.year} {r.kind} {r.no} was filed before it ended")
                if filed > self.today().isoformat():
                    raise ServiceError(422, f"external_returns: {r.year} {r.kind} {r.no} was filed in the future")
        return sorted((r for r in returns if not r.none_filed), key=lambda r: (r.year, r.no))

    @staticmethod
    def store_returns(
        service: VatReturnService, returns: list[models.ExternalReturnIn], user: Mapping[str, Any]
    ) -> int:
        """The returns filed outside the app: ``vat_returns`` rows (external, final), the months locked."""
        for r in returns:
            assert r.kz83 is not None and r.filed_on is not None
            service.create_external(r.year, r.kind, r.no, _cents(r.kz83), _cents(r.kz39), r.filed_on, user)
        return len(returns)
