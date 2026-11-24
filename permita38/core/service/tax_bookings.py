"""VAT payments, income-related taxes and opening balances (docs/specs/accounting.md 7, 9.6, 9.7, 12, 16).

:class:`TaxBookingService` turns one user action into one journal entry with the pure engine
``core/accounting/postings_other.py`` and posts it through :class:`JournalService` (event keys: ``vat:<id>`` for VAT
payments, refunds and the special prepayment (several per return, origin manual, corrected by reversal),
``vat:<return id>:credit`` for the automatic December credit, ``manual:<id>`` for the taxes of 9.7 (provisions
``manual:<kind>_provision:<year>:r<n>``), ``open:<year>:balances`` for the opening entry). Amounts are integer cents (the routes convert at the edge).

* Nothing posts before the accounting is initialised (settings ``accounting.start``): 409; a date before the start: 422.
* The provisions of 9.7 and the opening entry are accrual only: under EÜR they are refused with 422.
* Replaying an event key with the same content returns the stored entry; different content is a 409 (journal).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, cast

from permita38.core import models
from permita38.core.accounting.postings_other import (
    TaxTemplate,
    special_prepayment_credit_entry,
    special_prepayment_payment_entry,
    tax_payment_entry,
    tax_provision_entry,
    tax_provision_settlement_entry,
    tax_refund_entry,
    vat_payment_entry,
    vat_refund_entry,
)
from permita38.core.accounting.types import Cents, Entry
from permita38.core.charts import ChartName, chart_for
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service.journal import PAGE, JournalService, JournalSource, PostLine
from permita38.core.service.postings import post_line
from permita38.core.settings import accounting_start, effective_settings

__all__ = ["ProvisionBooking", "TaxBookingService"]

Kind = Literal["payment", "refund"]
ProvisionKind = Literal["trade_tax", "corporate_tax"]
Origin = Literal["auto", "manual", "opening"]
_EXPENSE_ROLE = {"trade_tax": "trade_tax_expense", "corporate_tax": "corporate_tax_expense"}
_PROVISION_ROLE = {"trade_tax": "trade_tax_provision", "corporate_tax": "corporate_tax_provision"}
_LABEL = {"trade_tax": "Trade tax", "income_tax": "Income tax", "corporate_tax": "Corporate tax"}


@dataclass(frozen=True)
class ProvisionBooking:
    """The posted provision and the sum of the prepayments of that tax expensed in the year (read from the journal),
    so the caller can show and confirm what the amount was netted against."""

    entry: models.JournalEntry
    prepayments_expensed_cents: int


def _check_amount(amount: int) -> None:
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise ServiceError(422, "The amount must be a positive number of cents")


def _user_id(user: Mapping[str, Any]) -> str:
    return str(user.get("id") or "system")


class TaxBookingService:
    def __init__(self, store: Repository, journal: JournalService | None = None):
        self.store = store
        self.journal = journal or JournalService(store)

    # ---------- helpers
    def _start(self) -> date:
        start = accounting_start(self.store.settings())
        if start is None:
            raise ServiceError(409, "Accounting is not set up yet")
        return start

    def _day(self, day: date) -> None:
        if day < self._start():
            raise ServiceError(422, "The date lies before the accounting start")

    def _company(self, kind: TaxTemplate) -> None:
        gmbh = effective_settings(self.store.settings()).get("company_type") == "gmbh"
        if kind == "income_tax" and gmbh:
            raise ServiceError(422, "A GmbH pays no income tax; book corporate tax instead")
        if kind == "corporate_tax" and not gmbh:
            raise ServiceError(422, "Corporate tax exists only for a GmbH")

    def _accrual(self, year: int, user: Mapping[str, Any], what: str) -> Record:
        row = self.journal.ensure_year(year, _user_id(user))
        if row.get("income_basis") != "accrual":
            raise ServiceError(422, f"{what} exists only under the accrual basis (EÜR books taxes when paid)")
        return row

    def _post(
        self,
        entry: Entry,
        user: Mapping[str, Any],
        event: str,
        text: str,
        origin: Origin = "manual",
        source_coll: str | None = None,
        source_id: str | None = None,
        source_number: str | None = None,
    ) -> models.JournalEntry:
        source = JournalSource(
            event=event,
            created_by=_user_id(user),
            origin=origin,
            source_coll=source_coll,
            source_id=source_id,
            source_number=source_number,
        )
        return self.journal.post(
            entry.event_key, entry.document_date, text, source, [post_line(ln) for ln in entry.lines]
        )

    # ---------- VAT (9.6)
    def vat_payment(
        self,
        return_id: str,
        day: date,
        amount: int,
        kind: Kind,
        user: Mapping[str, Any],
        entry_id: str | None = None,
    ) -> models.JournalEntry:
        """D vat_prepayments / C bank (refund: the reverse), ``income_date`` = ``day``; key ``vat:<uuid>``, origin
        manual (reverse it through the manual entries), the return is the source. Several per return are fine."""
        _check_amount(amount)
        self._day(day)
        event_id = entry_id or str(uuid.uuid4())
        if kind == "payment":
            entry = vat_payment_entry(event_id, day, Cents(amount))
        elif kind == "refund":
            entry = vat_refund_entry(event_id, day, Cents(amount))
        else:
            raise ServiceError(422, "kind must be payment or refund")
        return self._post(
            entry, user, f"vat_{kind}", f"VAT {kind}", "manual", source_coll="vat_returns", source_id=return_id
        )

    def special_prepayment(
        self, year: int, day: date, amount: int, user: Mapping[str, Any], entry_id: str | None = None
    ) -> models.JournalEntry:
        """Sondervorauszahlung: D vat_special_prepayment / C bank; key ``vat:<uuid>``, origin manual, the year is the
        source. Several per year are fine."""
        _check_amount(amount)
        self._day(day)
        entry = special_prepayment_payment_entry(entry_id or str(uuid.uuid4()), day, Cents(amount))
        return self._post(
            entry,
            user,
            "special_prepayment",
            f"VAT special prepayment {year}",
            "manual",
            source_coll="fiscal_years",
            source_number=str(year),
        )

    def special_prepayment_credit(
        self, year: int, return_id: str, amount: int, user: Mapping[str, Any]
    ) -> models.JournalEntry | None:
        """December/Q4 return finalised: D vat_prepayments / C vat_special_prepayment with the Kz 39 amount, dated
        31 December of ``year``; key ``vat:<return_id>:credit`` (idempotent), origin auto. ``None`` for Kz 39 = 0."""
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ServiceError(422, "The Kz 39 amount must be a number of cents, 0 or more")
        self._day(date(year, 12, 31))
        entry = special_prepayment_credit_entry(f"{return_id}:credit", date(year, 12, 31), Cents(amount))
        if entry is None:
            return None
        return self._post(
            entry,
            user,
            "special_prepayment_credit",
            f"VAT special prepayment credit {year}",
            "auto",
            source_coll="vat_returns",
            source_id=return_id,
        )

    def special_prepayment_credit_reverse(
        self, year: int, return_id: str, user: Mapping[str, Any]
    ) -> models.JournalEntry | None:
        """The return is reopened: reverse the credit ``vat:<return_id>:credit`` (key ``...:rev``, idempotent).
        ``None`` when there was no credit."""
        key = f"vat:{return_id}:credit"
        orig = self.store.get_journal_entry_by_event_key(key)
        if orig is None:
            return None
        lines = [
            PostLine(role=ln.get("role"), account=ln["account"], amount_cents=-int(ln["amount_cents"]))
            for ln in sorted(orig["lines"], key=lambda r: int(r["line_no"]))
        ]
        source = JournalSource(
            event="special_prepayment_credit_reversal",
            created_by=_user_id(user),
            origin="auto",
            source_coll="vat_returns",
            source_id=return_id,
            reverses_id=str(orig["id"]),
        )
        day = date.fromisoformat(str(orig["document_date"]))
        return self.journal.post(key + ":rev", day, f"Reversal: VAT special prepayment credit {year}", source, lines)

    # ---------- income-related taxes (9.7)
    def _payment(
        self,
        kind: TaxTemplate,
        day: date,
        amount: int,
        mode: Kind,
        user: Mapping[str, Any],
        entry_id: str | None,
    ) -> models.JournalEntry:
        _check_amount(amount)
        self._day(day)
        self._company(kind)
        manual_id = entry_id or str(uuid.uuid4())
        if mode == "payment":
            entry = tax_payment_entry(kind, manual_id, day, Cents(amount))
        elif mode == "refund":
            entry = tax_refund_entry(kind, manual_id, day, Cents(amount))
        else:
            raise ServiceError(422, "kind must be payment or refund")
        return self._post(entry, user, f"{kind}_{mode}", f"{_LABEL[kind]} {mode}")

    def trade_tax_payment(
        self, day: date, amount: int, kind: Kind, user: Mapping[str, Any], entry_id: str | None = None
    ) -> models.JournalEntry:
        """Prepayment / final payment D trade_tax_expense / C bank (``income_date`` = ``day``); a refund is the
        reverse with C tax_refunds."""
        return self._payment("trade_tax", day, amount, kind, user, entry_id)

    def income_tax_payment(
        self, day: date, amount: int, kind: Kind, user: Mapping[str, Any], entry_id: str | None = None
    ) -> models.JournalEntry:
        """Income tax, Soli, church tax of a sole proprietor: D private_withdrawal / C bank (never an expense)."""
        return self._payment("income_tax", day, amount, kind, user, entry_id)

    def corporate_tax_payment(
        self, day: date, amount: int, kind: Kind, user: Mapping[str, Any], entry_id: str | None = None
    ) -> models.JournalEntry:
        """GmbH: D corporate_tax_expense / C bank; a refund is the reverse."""
        return self._payment("corporate_tax", day, amount, kind, user, entry_id)

    # ---------- provisions (accrual only)
    def _entries(self, year: int | None) -> list[Record]:
        out: list[Record] = []
        offset = 0
        while True:
            page = self.store.list_journal_entries(year=year, limit=PAGE, offset=offset)
            out.extend(page)
            if len(page) < PAGE:
                return out
            offset += PAGE

    def prepayments_expensed(self, kind: ProvisionKind, year: int) -> int:
        """Cents of the ``<kind>_payment`` entries booked in fiscal ``year`` on the expense role and not reversed: the
        sum a provision is netted against (9.7)."""
        total = 0
        for e in self._entries(year):
            if e.get("event") != f"{kind}_payment":
                continue
            if self.store.get_journal_entry_by_event_key(f"{e['event_key']}:rev") is not None:
                continue
            total += sum(int(ln["amount_cents"]) for ln in e["lines"] if ln.get("role") == _EXPENSE_ROLE[kind])
        return total

    def provision_balance(self, kind: ProvisionKind) -> int:
        """Credit balance (cents, positive) of the provision account over all years: what is still to be settled."""
        role = _PROVISION_ROLE[kind]
        return -sum(int(ln["amount_cents"]) for e in self._entries(None) for ln in e["lines"] if ln.get("role") == role)

    def _reversed(self, entry: Mapping[str, Any]) -> bool:
        return self.store.get_journal_entry_by_event_key(f"{entry['event_key']}:rev") is not None

    def _live(self, prefix: str) -> list[Record]:
        """The not reversed entries whose key starts with ``prefix``."""
        return [
            e
            for e in self._entries(None)
            if str(e["event_key"]).startswith(prefix)
            and not str(e["event_key"]).endswith(":rev")
            and not self._reversed(e)
        ]

    def _provision(self, kind: ProvisionKind, year: int, amount: int, user: Mapping[str, Any]) -> ProvisionBooking:
        _check_amount(amount)
        self._start()
        self._company(kind)
        self._accrual(year, user, "A tax provision")
        closing = date(year, 12, 31)
        self._day(closing)
        base = f"{kind}_provision:{year}"
        revisions = [
            int(str(e["event_key"]).rsplit(":r", 1)[1])
            for e in self._entries(None)
            if str(e["event_key"]).startswith(f"manual:{base}:r") and not str(e["event_key"]).endswith(":rev")
        ]
        revision = max(revisions, default=1)
        latest = self.store.get_journal_entry_by_event_key(f"manual:{base}:r{revision}")
        if latest is not None and self._reversed(latest):
            revision += 1  # a reversed provision is booked again as the next revision
        entry = tax_provision_entry(kind, f"{base}:r{revision}", closing, Cents(amount))
        posted = self._post(
            entry, user, f"{kind}_provision", f"{_LABEL[kind]} provision {year}", source_coll="fiscal_years",
            source_number=str(year),
        )  # fmt: skip
        return ProvisionBooking(posted, self.prepayments_expensed(kind, year))

    def trade_tax_provision(self, year: int, amount: int, user: Mapping[str, Any]) -> ProvisionBooking:
        """Accrual at year closing: D trade_tax_expense (``income_date`` 31 Dec) / C trade_tax_provision. ``amount``
        is the estimate already net of the prepayments expensed in ``year``; their sum comes back for the caller to
        confirm. Key ``manual:trade_tax_provision:<year>:r<n>`` (a reversed provision is booked again as r<n+1>).
        EÜR: 422."""
        return self._provision("trade_tax", year, amount, user)

    def corporate_tax_provision(self, year: int, amount: int, user: Mapping[str, Any]) -> ProvisionBooking:
        return self._provision("corporate_tax", year, amount, user)

    def year_provision(self, kind: ProvisionKind, year: int) -> int:
        """Cents of the provision of ``year`` still to be settled (booked and not reversed, minus the settlements of
        that year and kind not reversed)."""
        role = _PROVISION_ROLE[kind]
        booked = -sum(
            int(ln["amount_cents"])
            for e in self._live(f"manual:{kind}_provision:{year}:r")
            for ln in e["lines"]
            if ln.get("role") == role
        )
        settled = sum(
            int(ln["amount_cents"])
            for e in self._live(f"manual:{kind}_settlement:{year}:")
            for ln in e["lines"]
            if ln.get("role") == role
        )
        return booked - settled

    def _settle(
        self,
        kind: ProvisionKind,
        year: int,
        day: date,
        amount: int,
        user: Mapping[str, Any],
        entry_id: str | None,
    ) -> models.JournalEntry:
        _check_amount(amount)
        self._day(day)
        self._company(kind)
        self._accrual(day.year, user, "A tax provision")
        provision = self.year_provision(kind, year)
        if provision <= 0:
            raise ServiceError(
                409, f"There is no {_LABEL[kind].lower()} provision of {year} to settle; book a payment instead"
            )
        entry = tax_provision_settlement_entry(
            kind, f"{kind}_settlement:{year}:{entry_id or uuid.uuid4()}", Cents(provision), Cents(amount), day
        )
        return self._post(
            entry, user, f"{kind}_settlement", f"{_LABEL[kind]} payment against the provision {year}",
            source_coll="fiscal_years", source_number=str(year),
        )  # fmt: skip

    def settle_trade_tax_provision(
        self, year: int, day: date, amount: int, user: Mapping[str, Any], entry_id: str | None = None
    ) -> models.JournalEntry:
        """Payment against the open provision of ``year`` only: D provision (all of it) / C bank; above it the
        difference is D trade_tax_expense, below it the rest is released C tax_refunds (both ``income_date`` =
        ``day``)."""
        return self._settle("trade_tax", year, day, amount, user, entry_id)

    def settle_corporate_tax_provision(
        self, year: int, day: date, amount: int, user: Mapping[str, Any], entry_id: str | None = None
    ) -> models.JournalEntry:
        return self._settle("corporate_tax", year, day, amount, user, entry_id)

    # ---------- opening balances (12, 16)
    def opening_balances(
        self, year: int, balances: Sequence[tuple[str, int]], user: Mapping[str, Any]
    ) -> models.JournalEntry:
        """The opening entry of ``year``: ``balances`` are ``(role or account, signed cents)`` with debit positive
        (assets) and credit negative (equity, liabilities). They must add up to 0 (assets = equity + liabilities);
        the debit total is credited and the credit total debited on ``opening_balance``, so that account nets to 0.
        Dated the accounting start in its own year, else 1 January. One entry per year, key ``open:<year>:balances``
        (idempotent). Accrual only (422 under EÜR)."""
        start = self._start()
        if year < start.year:
            raise ServiceError(422, "The year lies before the accounting start")
        row = self._accrual(year, user, "An opening entry")
        if not balances:
            raise ServiceError(422, "At least one balance is needed")
        chart = chart_for(cast(ChartName, row["chart"]), year)
        lines: list[PostLine] = []
        for key, cents in balances:
            if isinstance(cents, bool) or not isinstance(cents, int) or cents == 0:
                raise ServiceError(422, f"The balance of '{key}' must be a number of cents other than 0")
            known = {str(a.account): a for a in (*(a for _, a in chart.roles), *chart.accounts)}
            by_role = {str(r): a for r, a in chart.roles}
            acc = by_role.get(key) or known.get(key)
            if acc is not None and acc.account_class != "balance":
                raise ServiceError(
                    422, f"'{key}' is a profit and loss account; opening balances take balance sheet accounts"
                )
            lines.append(
                PostLine(role=key, amount_cents=cents) if key in by_role else PostLine(account=key, amount_cents=cents)
            )
        total = sum(ln.amount_cents for ln in lines)
        if total != 0:
            raise ServiceError(422, f"The opening balances are not balanced (they add up to {total} cents, not 0)")
        debits = sum(ln.amount_cents for ln in lines if ln.amount_cents > 0)
        lines.append(PostLine(role="opening_balance", amount_cents=-debits))
        lines.append(PostLine(role="opening_balance", amount_cents=debits))
        day = start if start.year == year else date(year, 1, 1)
        source = JournalSource(event="opening_balance", created_by=_user_id(user), origin="opening")
        return self.journal.post(f"open:{year}:balances", day, f"Opening balances {year}", source, lines)
