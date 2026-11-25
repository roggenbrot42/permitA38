"""Accounting initialisation and backfill (docs/specs/accounting.md 16, 17, 18), work package E22.

:meth:`AccountingInitService.run` initialises the accounting of an existing data folder in one transaction: it creates
the fiscal year, sets ``accounting.start`` once, posts the opening entry (accrual), writes the missing ``issued``
snapshots (step 3) and then replays every event on or after the start in date order (document date, then created
order) through the SAME posting services the live app uses: issue, payments (legacy ``paid`` materialised), credit
notes, expenses (the expense hook), trips. There is no posting code here. A posting service reads "every other
payment / credit note" of an invoice, so the replay hands them a view of the store in which the events that have not
been replayed yet are hidden: each delta is then what posting the event live, at that moment, would have produced.

Cases that need a decision of the user (pre-start baseline, vat dates before the start, legacy cancelled invoices,
reverse-charge expenses that cannot be classified, ...) are NOT guessed in this step: :meth:`check` lists them and
:meth:`run` refuses with 409 and that list, writing nothing (E23/E24 resolve them). No SQL here.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from typing import Any

from permita38.core import models
from permita38.core.errors import ServiceError
from permita38.core.money import dec, q2
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service import invoice_state
from permita38.core.service.expense_postings import RC_TREATMENTS, ExpensePostingService
from permita38.core.service.fiscal_years import FiscalYearService
from permita38.core.service.journal import JournalService
from permita38.core.service.payments import PaymentService
from permita38.core.service.postings import (
    CreditPostingService,
    IssuePostingService,
    PaymentPostingService,
    build_snapshot,
)
from permita38.core.service.sales import gross_cents_of
from permita38.core.service.tax_bookings import TaxBookingService
from permita38.core.service.trip_postings import TripPostingService
from permita38.core.settings import ACCOUNTING_START_KEY, accounting_start, effective_settings

__all__ = ["AccountingInitService", "InitResult", "ReviewItem"]

REVIEW_MESSAGE = "needs the review list — coming in a later step"
SYSTEM_USER: dict[str, Any] = {"id": "system", "role": "boss"}

# the order of events of the same day: an issue first, then the bill-like events, a write-off reversal before the
# payment that triggers it, then payments and credit notes in the order they were created
RANK_ISSUE, RANK_BILL, RANK_REVERSAL, RANK_CASH = 0, 1, 2, 3


@dataclass(frozen=True)
class ReviewItem:
    """One record the initialisation cannot decide alone (accounting.md 16 step 9)."""

    key: str
    code: str
    collection: str
    id: str
    number: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "code": self.code,
            "collection": self.collection,
            "id": self.id,
            "number": self.number,
            "message": self.message,
        }


@dataclass
class InitResult:
    year: int
    start: str
    counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"year": self.year, "accounting_start": self.start, "counts": dict(self.counts)}


@dataclass
class _Event:
    day: str
    rank: int
    created: int
    index: int
    kind: str
    apply: Callable[[], Any]

    @property
    def order(self) -> tuple[str, int, int, int]:
        return (self.day, self.rank, self.created, self.index)


class _ReplayStore:
    """The store as the posting services see it during the replay: payments and credit notes that were not replayed
    yet are invisible. Everything else (writes, other collections, transactions) goes to the real store."""

    def __init__(self, store: Repository):
        self._store = store
        self.hidden: dict[str, set[str]] = {"payments": set(), "invoices": set()}

    def find(self, coll: str, **where: Any) -> list[Record]:
        rows = self._store.find(coll, **where)
        hidden = self.hidden.get(coll)
        return [r for r in rows if r.get("id") not in hidden] if hidden else rows

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _num(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_credit(row: Mapping[str, Any]) -> bool:
    return row.get("kind") == "credit_note" or bool(row.get("credit_kind"))


def _issued(row: Mapping[str, Any]) -> bool:
    """An invoice that was issued (not a draft, not a credit note)."""
    return not _is_credit(row) and str(row.get("status") or "draft") != "draft" and _day(row.get("date")) is not None


class AccountingInitService:
    def __init__(
        self,
        store: Repository,
        journal: JournalService | None = None,
        fiscal_years: FiscalYearService | None = None,
        tax_bookings: TaxBookingService | None = None,
    ):
        self.store = store
        self.journal = journal or JournalService(store)
        self.fiscal_years = fiscal_years or FiscalYearService(store)
        self.tax_bookings = tax_bookings or TaxBookingService(store, self.journal)
        self.issue_posting = IssuePostingService(store, self.journal)
        self.expense_posting = ExpensePostingService(store, self.journal)
        self.trip_posting = TripPostingService(store, self.journal)

    # ------------------------------------------------------------------ parameters
    @staticmethod
    def start_of(params: models.AccountingInitIn, today: date | None = None) -> date:
        raw = params.accounting_start
        day = _day(raw) if raw else date((today or date.today()).year, 1, 1)
        if day is None or day.day != 1:
            raise ServiceError(422, "The accounting start must be the first day of a month")
        return day

    # ------------------------------------------------------------------ the review list (read only)
    def check(self, params: models.AccountingInitIn, today: date | None = None) -> list[ReviewItem]:
        """The records the backfill would need a decision for (step 4, 5a, 6, 8; E23/E24 resolve them)."""
        start = self.start_of(params, today)
        st = effective_settings(self.store.settings())
        out: list[ReviewItem] = []
        payments = invoice_state.group_by(self.store.list("payments"), "document_id")
        notes = invoice_state.group_by(
            [r for r in self.store.list("invoices") if r.get("kind") == "credit_note"], "corrects_id"
        )

        def item(code: str, coll: str, rec: Mapping[str, Any], message: str) -> None:
            out.append(ReviewItem(f"{coll}:{rec['id']}:{code}", code, coll, str(rec["id"]), rec.get("number"), message))

        for inv in self.store.list("invoices"):
            if not _issued(inv):
                continue
            day = _day(inv.get("date"))
            assert day is not None
            if day >= start:
                if inv.get("status") == "cancelled" and not any(
                    n.get("credit_kind") == "storno" for n in notes.get(str(inv["id"]), [])
                ):
                    paid = bool(payments.get(str(inv["id"]))) or inv.get("paid_date")
                    item(
                        "legacy_cancelled",
                        "invoices",
                        inv,
                        "Cancelled before credit notes existed" + (" and paid" if paid else "") + "; " + REVIEW_MESSAGE,
                    )
                    continue
                perf = self._perf(inv, st, day)
                if perf is not None and perf < start and (params.vat_basis == "soll" or self._b2b_service(inv)):
                    item("vat_before_start", "invoices", inv, "VAT date before the accounting start; " + REVIEW_MESSAGE)
            elif self._open_at_start(inv, st, start, payments.get(str(inv["id"]), []), notes.get(str(inv["id"]), [])):
                item(
                    "prestart_open_invoice",
                    "invoices",
                    inv,
                    "Issued before the accounting start and still open at it; " + REVIEW_MESSAGE,
                )
        for exp in self.store.list("expenses"):
            self._check_expense(exp, start, item)
        return out

    @staticmethod
    def _b2b_service(inv: Mapping[str, Any]) -> bool:
        return inv.get("tax_treatment") == "eu_b2b_service"

    @staticmethod
    def _perf(inv: Mapping[str, Any], st: Mapping[str, Any], day: date) -> date | None:
        raw = inv.get("issued")
        if isinstance(raw, Mapping) and raw.get("perf"):
            return _day(raw["perf"])
        return _day(inv.get("service_to")) or _day(inv.get("service_from")) or day

    @staticmethod
    def _open_at_start(
        inv: Mapping[str, Any],
        st: Mapping[str, Any],
        start: date,
        pays: Iterable[Mapping[str, Any]],
        notes: Iterable[Mapping[str, Any]],
    ) -> bool:
        rows = list(pays)
        gross = gross_cents_of(inv, st)
        if not rows and inv.get("status") == "paid" and gross != 0:
            cash = gross if (_day(invoice_state.legacy_payment_date(inv)) or start) < start else 0
        else:
            cash = sum(_num(r.get("amount_cents")) for r in rows if (_day(r.get("date")) or start) < start)
        credited = sum(invoice_state.credit_note_gross(n) for n in notes if (_day(n.get("date")) or start) < start)
        return gross - cash - credited > 0

    def _check_expense(
        self, exp: Mapping[str, Any], start: date, item: Callable[[str, str, Mapping[str, Any], str], None]
    ) -> None:
        day = _day(exp.get("date"))
        if day is None:
            return
        paid_on = _day(exp.get("paid_on"))
        if day < start:
            if paid_on is not None and paid_on >= start:
                item(
                    "expense_prestart_open",
                    "expenses",
                    exp,
                    "Dated before the accounting start, paid after it; " + REVIEW_MESSAGE,
                )
            return
        if paid_on is not None and paid_on < start:
            item("expense_prepaid", "expenses", exp, "Paid before the accounting start; " + REVIEW_MESSAGE)
        if not exp.get("tax_treatment") and exp.get("reverse_charge"):
            sid = exp.get("supplier_id")
            if not sid or not self.store.get("suppliers", str(sid)):
                item("rc_without_supplier", "expenses", exp, "Reverse charge without a supplier; " + REVIEW_MESSAGE)
            elif q2(dec(exp.get("vat"))) != 0:
                item("rc_stored_vat", "expenses", exp, "Reverse charge with a typed VAT; " + REVIEW_MESSAGE)
        treatment = self.expense_posting.resolve_treatment(exp)
        service = _day(exp.get("service_date"))
        if treatment in RC_TREATMENTS and service is not None and service < start:
            item("vat_before_start", "expenses", exp, "VAT date before the accounting start; " + REVIEW_MESSAGE)

    # ------------------------------------------------------------------ the initialisation
    def run(
        self, params: models.AccountingInitIn, user: Mapping[str, Any] | None = None, today: date | None = None
    ) -> InitResult:
        user = user or SYSTEM_USER
        start = self.start_of(params, today)
        with self.store.transaction():
            if accounting_start(self.store.settings()) is not None or self.store.settings().get(ACCOUNTING_START_KEY):
                raise ServiceError(409, "Accounting is already initialised")
            review = self.check(params, today)
            if review:
                raise ServiceError(
                    409,
                    f"{len(review)} record(s) {REVIEW_MESSAGE}",
                    body={"review": [r.as_dict() for r in review]},
                )
            balances = self._balances(params)
            if balances and params.income_basis != "accrual":
                raise ServiceError(422, "Opening balances exist only under the accrual basis")
            self.fiscal_years.create(
                user,
                models.FiscalYearIn(
                    year=start.year,
                    chart=params.chart,
                    income_basis=params.income_basis,
                    vat_basis=params.vat_basis,
                    vat_frequency=params.frequency,
                    dauerfrist=params.dauerfrist,
                    small_business=params.small_business,
                ),
            )
            settings = dict(self.store.settings())
            settings[ACCOUNTING_START_KEY] = start.isoformat()
            self.store.save_settings(settings)
            counts: dict[str, int] = {}
            if balances:
                self.tax_bookings.opening_balances(start.year, balances, user)
                counts["opening_entry"] = 1
            self._open_years(start)
            counts["snapshots"] = self._snapshots(start)
            for kind, n in self._replay(start, user).items():
                counts[kind] = n
            return InitResult(start.year, start.isoformat(), counts)

    @staticmethod
    def _balances(params: models.AccountingInitIn) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for b in params.opening_balances:
            key = b.account or b.role
            assert key is not None
            out.append((str(key), int(q2(dec(b.amount)) * 100)))
        return out

    def _open_years(self, start: date) -> None:
        """Open every fiscal year up to the last year of an event (each copied from its predecessor)."""
        last = start.year
        for coll in ("invoices", "expenses"):
            for r in self.store.list(coll):
                day = _day(r.get("date"))
                if day is not None and day >= start:
                    last = max(last, day.year)
        for t in self.store.list("trips"):
            day = _day(t.get("end"))
            if day is not None and day >= start:
                last = max(last, day.year)
        for year in range(start.year + 1, last + 1):
            self.journal.ensure_year(year, "system")

    # ------------------------------------------------------------------ step 3: snapshots
    def _snapshots(self, start: date) -> int:
        st = effective_settings(self.store.settings())
        n = 0
        for inv in self.store.list("invoices"):
            day = _day(inv.get("date"))
            if not _issued(inv) or inv.get("issued") or day is None:
                continue
            self.store.save("invoices", {**inv, "issued": build_snapshot(self.store, inv, st, day)})
            n += 1
        return n

    # ------------------------------------------------------------------ steps 1, 5, 7: the replay
    def _replay(self, start: date, user: Mapping[str, Any]) -> dict[str, int]:
        view = _ReplayStore(self.store)
        pay_post = PaymentPostingService(view, self.journal)  # type: ignore[arg-type]
        credit_post = CreditPostingService(view, self.journal)  # type: ignore[arg-type]
        pay_service = PaymentService(view, self.journal)  # type: ignore[arg-type]
        events: list[_Event] = []
        idx = 0

        def add(day: str, rank: int, created: int, kind: str, apply: Callable[[], Any]) -> None:
            nonlocal idx
            events.append(_Event(day, rank, created, idx, kind, apply))
            idx += 1

        invoices = {str(r["id"]): r for r in self.store.list("invoices") if _issued(r)}
        late = {k: v for k, v in invoices.items() if (_day(v["date"]) or start) >= start}

        def stored(inv_id: str) -> Record:
            return self.store.get("invoices", inv_id) or {}

        def issue(inv_id: str) -> Any:
            return self.issue_posting.post_issue(stored(inv_id), user)

        def legacy(inv_id: str) -> Any:
            return pay_service.materialize(stored(inv_id), user)

        def payment(row: Record) -> Any:
            view.hidden["payments"].discard(str(row["id"]))
            return pay_post.post_payment(stored(str(row["document_id"])), row, user)

        def credit(note: Record) -> Any:
            view.hidden["invoices"].discard(str(note["id"]))
            return credit_post.post_credit(stored(str(note["corrects_id"])), note, user)

        settings = effective_settings(self.store.settings())
        for inv_id, inv in late.items():
            add(str(inv["date"])[:10], RANK_ISSUE, 0, "issue", partial(issue, inv_id))
            has_rows = bool(self.store.find("payments", document_id=inv_id))
            if not has_rows and inv.get("status") == "paid" and gross_cents_of(inv, settings) != 0:
                paid_day = invoice_state.legacy_payment_date(inv)
                add(paid_day, RANK_CASH, 0, "legacy_payment", partial(legacy, inv_id))
        for pay in self.store.list("payments"):
            if str(pay.get("document_id")) in late:
                view.hidden["payments"].add(str(pay["id"]))
                add(str(pay["date"])[:10], RANK_CASH, _num(pay.get("created_at")), "payment", partial(payment, pay))
        for note in self.store.list("invoices"):
            if note.get("kind") != "credit_note" or str(note.get("corrects_id")) not in late:
                continue
            view.hidden["invoices"].add(str(note["id"]))
            rank = RANK_REVERSAL if note.get("credit_kind") == "write_off_reversal" else RANK_CASH
            created = _num((note.get("issued") or {}).get("issued_at"))
            add(str(note["date"])[:10], rank, created, "credit", partial(credit, note))
        for exp in self.store.list("expenses"):
            day = _day(exp.get("date"))
            if day is not None and day >= start:
                add(day.isoformat(), RANK_BILL, 0, "expense", partial(self._expense, exp, user))
        for trip in self.store.list("trips"):
            end = _day(trip.get("end"))
            if trip.get("status") == "final" and end is not None and end >= start:
                add(end.isoformat(), RANK_BILL, 0, "trip", partial(self.trip_posting.sync, dict(trip), user))
        counts: dict[str, int] = {}
        for ev in sorted(events, key=lambda e: e.order):
            ev.apply()
            counts[ev.kind] = counts.get(ev.kind, 0) + 1
        return counts

    def _expense(self, exp: Record, user: Mapping[str, Any]) -> None:
        """Normalise the treatment like a save does (``prepare``), then post through the expense hook."""
        data = dict(exp)
        self.expense_posting.prepare(data, exp)
        saved = self.store.save("expenses", data) if data != exp else exp
        self.expense_posting.sync(dict(saved), None, user)
