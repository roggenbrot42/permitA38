"""Accounting initialisation and backfill (docs/specs/accounting.md 16, 17, 18), work package E22.

:meth:`AccountingInitService.run` initialises the accounting of an existing data folder in one transaction: it creates
the fiscal year, sets ``accounting.start`` once, posts the opening entry (accrual), writes the missing ``issued``
snapshots (step 3) and then replays every event on or after the start in date order (document date, then created
order) through the SAME posting services the live app uses: issue, payments (legacy ``paid`` materialised), credit
notes, expenses (the expense hook), trips. There is no posting code here. A posting service reads "every other
payment / credit note" of an invoice, so the replay hands them a view of the store in which the events that have not
been replayed yet are hidden: each delta is then what posting the event live, at that moment, would have produced.

Pre-start items (E23, ``opening_items.py``): an invoice issued before the start and open at it gets its baseline, an
expense dated before the start and paid on/after it its opening entry, before the replay; the events on/after the
start of those invoices then replay like any other.

Cases that need a decision of the user (vat dates before the start, paid-then-cancelled invoices, reverse-charge
expenses that cannot be classified, legacy paid dates, ...) are NOT guessed: :meth:`check` lists them with their
allowed resolutions, :meth:`run` applies the answers (``review_resolutions``, ``init_review.py``) and refuses with 409
and the unanswered rows, writing nothing, while one is missing. Legacy cancelled invoices that were never paid get
their storno credit note automatically; the returns filed outside the app (``external_returns``) are stored and their
months locked after the replay. No SQL here.
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
from permita38.core.service.credit_notes import CreditNoteService
from permita38.core.service.expense_postings import RC_TREATMENTS, ExpensePostingService
from permita38.core.service.fiscal_years import FiscalYearService
from permita38.core.service.init_review import (
    ExternalPeriod,
    ReviewApplier,
    ReviewItem,
    external_periods,
)
from permita38.core.service.journal import JournalService
from permita38.core.service.locks import LockService
from permita38.core.service.opening_items import OpeningItemService
from permita38.core.service.payments import PaymentService, gross_cents_of
from permita38.core.service.postings import (
    CreditPostingService,
    IssuePostingService,
    PaymentPostingService,
    build_snapshot,
)
from permita38.core.service.tax_bookings import TaxBookingService
from permita38.core.service.trip_postings import TripPostingService
from permita38.core.service.vat_returns import VatReturnService
from permita38.core.settings import ACCOUNTING_START_KEY, accounting_start, effective_settings

__all__ = ["AccountingInitService", "InitResult", "ReviewItem"]

SYSTEM_USER: dict[str, Any] = {"id": "system", "role": "boss"}

# the order of events of the same day: an issue first, everything else after it in creation order (``seq``, see
# :func:`_row_order`); the only rank is issue-before-cash
RANK_ISSUE, RANK_OTHER = 0, 1


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
    group: int  # 0 = no seq (legacy row, expense, trip), 1 = has a seq: legacy rows come first
    n: int  # the seq, or the creation time of a legacy row
    tie: int
    ident: str  # the final sort key: the record id
    kind: str
    apply: Callable[[], Any]

    @property
    def order(self) -> tuple[str, int, int, int, int, str]:
        return (self.day, self.rank, self.group, self.n, self.tie, self.ident)


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


def _row_order(row: Mapping[str, Any], created: Any, tie: int) -> tuple[int, int, int]:
    """(group, n, tie) of a payments row or credit note: its ``seq`` when it has one, else (created before this
    release) its creation time and, within one second, ``tie``: credit notes 0, a write-off reversal 1 (after its
    write-off, directly before the payment that triggered it), payments 2."""
    seq = _num(row.get("seq"))
    if seq > 0:
        return (1, seq, 0)
    return (0, _num(created), tie)


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
        self.opening_items = OpeningItemService(store, self.journal)
        self.trip_posting = TripPostingService(store, self.journal)

    def _applier(self, today: date | None) -> ReviewApplier:
        clock = (lambda: today) if today is not None else date.today
        payments = PaymentService(self.store, self.journal, today=clock)
        return ReviewApplier(
            self.store, self.expense_posting, CreditNoteService(self.store, self.journal, payments), payments, clock
        )

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
        """The records the backfill would need a decision for (step 5a, 6, 8; E24 resolves them)."""
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
            rows = payments.get(str(inv["id"]), [])
            if inv.get("status") == "paid" and not rows and gross_cents_of(inv, st) != 0:  # a legacy paid invoice
                if not inv.get("paid_date"):
                    item("legacy_paid_no_date", "invoices", inv, "Marked paid without a date; the invoice date is used")
                elif (_day(inv["paid_date"]) or day) < day:
                    item("legacy_paid_before_invoice", "invoices", inv, "Paid before the invoice date; kept as stored")
            if day >= start:
                if inv.get("status") == "cancelled" and not any(
                    n.get("credit_kind") == "storno" for n in notes.get(str(inv["id"]), [])
                ):
                    if rows or inv.get("paid_date"):  # an unpaid one gets its storno automatically (step 8)
                        item(
                            "legacy_cancelled_paid",
                            "invoices",
                            inv,
                            "Cancelled after payment, before credit notes existed: issue a storno credit note and "
                            "refund, or keep it as paid",
                        )
                    continue
                perf = self._perf(inv, st, day)
                if (
                    perf is not None
                    and perf < start
                    and not params.small_business
                    and ((params.vat_basis == "soll" and self._carries_vat(inv, st, day)) or self._b2b_service(inv))
                ):
                    item(
                        "vat_before_start",
                        "invoices",
                        inv,
                        "VAT date before the accounting start: declared outside the app",
                    )
        for exp in self.store.list("expenses"):
            self._check_expense(exp, start, item)
        return out

    @staticmethod
    def _b2b_service(inv: Mapping[str, Any]) -> bool:
        return inv.get("tax_treatment") == "eu_b2b_service"

    def _carries_vat(self, inv: Mapping[str, Any], st: Mapping[str, Any], day: date) -> bool:
        """Does the invoice declare any VAT (a 0 % or small-business invoice has no return to declare)?"""
        snap = inv.get("issued")
        if not isinstance(snap, Mapping) or not isinstance(snap.get("rates"), Mapping):
            try:
                snap = build_snapshot(self.store, dict(inv), dict(st), day)
            except ServiceError:
                return True
        return any(_num(g.get("vat")) for g in snap["rates"].values())

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
        if inv.get("status") == "cancelled":  # a cancelled invoice is no open item (it is a legacy_cancelled case)
            return False
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
        if day < start:  # an expense dated before the start posts an opening entry (E23), never a review case
            return
        if paid_on is not None and paid_on < start:
            item("expense_prepaid", "expenses", exp, "Paid before the accounting start: confirm")
        if not exp.get("tax_treatment") and exp.get("reverse_charge"):
            sid = exp.get("supplier_id")
            if not sid or not self.store.get("suppliers", str(sid)):
                item(
                    "rc_without_supplier",
                    "expenses",
                    exp,
                    "Reverse charge without a supplier: choose the tax treatment",
                )
            elif q2(dec(exp.get("vat"))) != 0:
                item(
                    "rc_stored_vat",
                    "expenses",
                    exp,
                    "Reverse charge with a typed VAT: recompute the self-assessed VAT, or choose another treatment",
                )
        treatment = self.expense_posting.resolve_treatment(exp)
        service = _day(exp.get("service_date"))
        if treatment in RC_TREATMENTS and service is not None and service < start:
            item("vat_before_start", "expenses", exp, "VAT date before the accounting start: declared outside the app")

    # ------------------------------------------------------------------ the initialisation
    def run(
        self, params: models.AccountingInitIn, user: Mapping[str, Any] | None = None, today: date | None = None
    ) -> InitResult:
        user = user or SYSTEM_USER
        with self.store.transaction():
            if accounting_start(self.store.settings()) is not None or self.store.settings().get(ACCOUNTING_START_KEY):
                raise ServiceError(409, "Accounting is already initialised")
            start = self.start_of(params, today)
            review = self.check(params, today)
            answers = {r.key: r for r in params.review_resolutions}
            applier = self._applier(today)
            applier.validate(review, answers)
            open_rows = [r for r in review if r.key not in answers]
            periods = self.external_periods(params, today)
            filed = applier.returns_to_store(params.external_returns, periods)  # 422 for a period that is no answer
            answered = {(r.year, r.kind, r.no) for r in params.external_returns}
            open_periods = [p for p in periods if (p.year, p.kind, p.no) not in answered]
            if open_rows or open_periods:
                raise ServiceError(
                    409,
                    f"{len(open_rows) + len(open_periods)} item(s) need a decision before the accounting can start",
                    body={
                        "review": [r.as_dict() for r in open_rows],
                        "external_returns": [p.as_dict() for p in open_periods],
                        "unresolved": [r.key for r in open_rows] + [p.key for p in open_periods],
                    },
                )
            balances = self._balances(params)
            if balances and params.income_basis != "accrual":
                raise ServiceError(422, "Opening balances exist only under the accrual basis")
            # the answers change records and issue credit notes while accounting is not initialised yet: recorded,
            # not posted; the replay posts them
            counts: dict[str, int] = dict(applier.apply(review, answers, user, start))
            cancelled = self._unpaid_cancelled(start, review)
            if cancelled:
                counts["migrated_cancellation"] = applier.storno_unpaid(cancelled, user)
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
            if balances:
                self.tax_bookings.opening_balances(start.year, balances, user)
                counts["opening_entry"] = 1
            self._open_years(start, max((r.year for r in filed), default=start.year))
            counts["snapshots"] = self._snapshots(start)
            counts.update(self._opening_items(start, user))
            for kind, n in self._replay(start, user).items():
                counts[kind] = n
            if filed:  # last: it locks the months up to the filed periods
                locks = LockService(self.store, today=(lambda: today) if today is not None else date.today)
                service = VatReturnService(self.store, locks, bookings=self.tax_bookings)
                counts["external_returns"] = applier.store_returns(service, filed, user)
            return InitResult(start.year, start.isoformat(), counts)

    @staticmethod
    def _balances(params: models.AccountingInitIn) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for b in params.opening_balances:
            key = b.account or b.role
            assert key is not None
            out.append((str(key), int(q2(dec(b.amount)) * 100)))
        return out

    def external_periods(self, params: models.AccountingInitIn, today: date | None = None) -> list[ExternalPeriod]:
        """Step 5b: the ended VAT periods on/after the start, for which a return filed outside the app may be entered
        (``external_returns``; every period needs an answer, ``none_filed`` included). A small business files quarterly."""
        kind = "quarter" if params.small_business else params.frequency
        return external_periods(self.start_of(params, today), today or date.today(), kind)

    def _unpaid_cancelled(self, start: date, review: list[ReviewItem]) -> list[Record]:
        """Step 8: the legacy cancelled invoices on/after the start that were never paid and have no storno yet."""
        asked = {r.id for r in review if r.code == "legacy_cancelled_paid"}
        notes = invoice_state.group_by(
            [r for r in self.store.list("invoices") if r.get("kind") == "credit_note"], "corrects_id"
        )
        out = []
        for inv in self.store.list("invoices"):
            day = _day(inv.get("date"))
            if (
                _issued(inv)
                and inv.get("status") == "cancelled"
                and day is not None
                and day >= start
                and str(inv["id"]) not in asked
                and not any(n.get("credit_kind") == "storno" for n in notes.get(str(inv["id"]), []))
            ):
                out.append(inv)
        return out

    def _open_years(self, start: date, until: int) -> None:
        """Open every fiscal year up to the last year of an event or of a filed return (each copied from its
        predecessor)."""
        last = max(start.year, until)
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

    # ------------------------------------------------------------------ steps 4, 5: pre-start items
    def _opening_items(self, start: date, user: Mapping[str, Any]) -> dict[str, int]:
        """The baseline of every invoice issued before the start and open at it, the opening entry of every expense
        dated before the start and paid on/after it (their events on/after the start replay afterwards)."""
        st = effective_settings(self.store.settings())
        counts: dict[str, int] = {}
        for inv in self.store.list("invoices"):
            day = _day(inv.get("date"))
            if not _issued(inv) or day is None or day >= start:
                continue
            pays = self.store.find("payments", document_id=inv["id"])
            notes = [n for n in self.store.find("invoices", corrects_id=inv["id"]) if n.get("kind") == "credit_note"]
            if self._open_at_start(inv, st, start, pays, notes) and self.opening_items.ensure_invoice_baseline(
                inv, user
            ):
                counts["opening_invoice"] = counts.get("opening_invoice", 0) + 1
        for exp in self.store.list("expenses"):
            data = dict(exp)
            self.expense_posting.prepare(data, exp)  # the treatment, like a save does
            if self.expense_posting.post_opening(data, user) is not None:
                counts["opening_expense"] = counts.get("opening_expense", 0) + 1
        return counts

    # ------------------------------------------------------------------ steps 1, 5, 7: the replay
    def _replay(self, start: date, user: Mapping[str, Any]) -> dict[str, int]:
        view = _ReplayStore(self.store)
        pay_post = PaymentPostingService(view, self.journal)  # type: ignore[arg-type]
        credit_post = CreditPostingService(view, self.journal)  # type: ignore[arg-type]
        pay_service = PaymentService(view, self.journal)  # type: ignore[arg-type]
        events: list[_Event] = []

        def add(
            day: str, rank: int, order: tuple[int, int, int], ident: str, kind: str, apply: Callable[[], Any]
        ) -> None:
            events.append(_Event(day, rank, order[0], order[1], order[2], ident, kind, apply))

        invoices = {str(r["id"]): r for r in self.store.list("invoices") if _issued(r)}
        late = {k: v for k, v in invoices.items() if (_day(v["date"]) or start) >= start}
        # issued before the start: their baseline is posted; only the events on/after the start replay
        early = {k: v for k, v in invoices.items() if k not in late and v.get("status") != "cancelled"}
        replayed = {**late, **early}

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
            add(str(inv["date"])[:10], RANK_ISSUE, (0, 0, 0), inv_id, "issue", partial(issue, inv_id))
            has_rows = bool(self.store.find("payments", document_id=inv_id))
            if not has_rows and inv.get("status") == "paid" and gross_cents_of(inv, settings) != 0:
                paid_day = invoice_state.legacy_payment_date(inv)
                add(paid_day, RANK_OTHER, (0, 0, 1), inv_id, "legacy_payment", partial(legacy, inv_id))
        for inv_id, inv in early.items():
            paid_day = invoice_state.legacy_payment_date(inv)
            if (
                not self.store.find("payments", document_id=inv_id)
                and inv.get("status") == "paid"
                and gross_cents_of(inv, settings) != 0
                and (_day(paid_day) or start) >= start
            ):
                add(paid_day, RANK_OTHER, (0, 0, 1), inv_id, "legacy_payment", partial(legacy, inv_id))
        for pay in self.store.list("payments"):
            if str(pay.get("document_id")) in replayed and (
                str(pay.get("document_id")) in late or (_day(pay.get("date")) or start) >= start
            ):
                view.hidden["payments"].add(str(pay["id"]))
                order = _row_order(pay, pay.get("created_at"), 2)
                add(str(pay["date"])[:10], RANK_OTHER, order, str(pay["id"]), "payment", partial(payment, pay))
        for note in self.store.list("invoices"):
            if note.get("kind") != "credit_note" or str(note.get("corrects_id")) not in replayed:
                continue
            if str(note.get("corrects_id")) in early and (_day(note.get("date")) or start) < start:
                continue  # part of the baseline
            view.hidden["invoices"].add(str(note["id"]))
            reversal = note.get("credit_kind") == "write_off_reversal"
            order = _row_order(note, (note.get("issued") or {}).get("issued_at"), 1 if reversal else 0)
            add(str(note["date"])[:10], RANK_OTHER, order, str(note["id"]), "credit", partial(credit, note))
        for exp in self.store.list("expenses"):
            day = _day(exp.get("date"))
            if day is not None and day >= start:
                add(
                    day.isoformat(), RANK_OTHER, (0, 0, 0), str(exp["id"]), "expense", partial(self._expense, exp, user)
                )
            elif day is not None and (paid := _day(exp.get("paid_on"))) is not None and paid >= start:
                add(  # dated before the start: only its payment posts (the bill is in the opening entry)
                    paid.isoformat(),
                    RANK_OTHER,
                    (0, 0, 0),
                    str(exp["id"]),
                    "expense_payment",
                    partial(self._expense, exp, user),
                )
        for trip in self.store.list("trips"):
            end = _day(trip.get("end"))
            if trip.get("status") == "final" and end is not None and end >= start:
                add(
                    end.isoformat(),
                    RANK_OTHER,
                    (0, 0, 0),
                    str(trip["id"]),
                    "trip",
                    partial(self.trip_posting.sync, dict(trip), user),
                )
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
