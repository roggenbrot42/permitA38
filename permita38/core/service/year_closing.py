"""Year closing (accrual) and year end (EÜR) (docs/specs/accounting.md 7.6, 11, 12, 13, 17).

:meth:`YearClosingService.preview` answers what closing would do and what stops it; :meth:`close` does it in **one
transaction** (boss only, the route enforces that; 409 when the year is closed already):

Accrual
    1. the provisions the user confirmed (``TaxBookingService``, 9.7; keys ``manual:<kind>_provision:<year>:r<n>``);
    2. ``close:<year>:vat`` - the balances of the VAT accounts (``vat_out_*``, ``input_vat_*``, ``rc_*``,
       ``vat_prepayments``, ``vat_special_prepayment``) move to ``vat_previous_year`` (settled by the annual return);
    3. ``close:<year>:pnl`` - every profit and loss account to ``profit_carryforward`` (lines **without**
       ``income_date``, so the year's P&L report is unchanged);
    4. ``close:<year>:balance`` - every balance account to ``closing_balance`` (the debit total and the credit total
       as two lines, so that account nets to 0);
    5. December is locked (``year_close``), the year gets ``closed_at/closed_by`` and the last hash and the number of
       its entries (``closing_hash`` / ``closing_entry_no``, set once; ``JournalService.verify_chain`` checks them);
    6. year + 1 is created when missing and gets its opening entry ``open:<year+1>:balances`` (the closing balances).

EÜR (:meth:`end_year`): no entry; December is locked, the year is closed with its chain anchor, year + 1 is created.
Open items stay open.

Both need every month before December locked (a closing never locks months silently) and December over. Nothing here
uses SQL: balances are summed from the journal entries of the year.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from permita38.core.charts import Chart, ChartMissing, ChartName, chart_for
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service.journal import PAGE, JournalService, JournalSource, PostLine
from permita38.core.service.locks import REASON_YEAR_CLOSE, LockService
from permita38.core.service.tax_bookings import ProvisionKind, TaxBookingService
from permita38.core.settings import accounting_start, effective_settings

__all__ = [
    "PlanLine",
    "PlannedEntry",
    "ProvisionInput",
    "ProvisionProposal",
    "YearClosingPreview",
    "YearClosingService",
]

COLLECTION = "fiscal_years"
# section 13: vat_out_*, input_vat_*, rc_*, vat_prepayments, vat_special_prepayment (not the clearing roles)
VAT_ROLES = (
    "vat_out_s19", "vat_out_s7", "vat_out_sx",
    "input_vat_s19", "input_vat_s7", "input_vat_sx",
    "rc_vat_out_eu", "rc_vat_out_3c", "rc_input_vat",
    "vat_prepayments", "vat_special_prepayment",
)  # fmt: skip
_EXPENSE_ROLE = {"trade_tax": "trade_tax_expense", "corporate_tax": "corporate_tax_expense"}
_PROVISION_ROLE = {"trade_tax": "trade_tax_provision", "corporate_tax": "corporate_tax_provision"}
# nets to 0 by construction; never carried or closed as a balance
_COUNTER_ROLES = ("closing_balance", "opening_balance")
_DRAFT_OK = {("month", 12), ("quarter", 4)}  # a draft return of the period containing December does not block


@dataclass(frozen=True)
class PlanLine:
    account: str
    amount_cents: int  # debit positive, credit negative


@dataclass(frozen=True)
class PlannedEntry:
    event_key: str
    event: str
    text: str
    lines: tuple[PlanLine, ...]


@dataclass(frozen=True)
class ProvisionInput:
    """A provision the user confirms; ``amount_cents`` is the estimate net of the prepayments expensed."""

    kind: ProvisionKind
    amount_cents: int


@dataclass(frozen=True)
class ProvisionProposal:
    """What the user is asked to confirm for a tax: the prepayments already expensed in the year (read from the
    journal, the estimate is netted against them) and the provision of the year that is booked already."""

    kind: ProvisionKind
    prepayments_expensed_cents: int
    booked_cents: int


@dataclass(frozen=True)
class YearClosingPreview:
    year: int
    income_basis: str
    blockers: tuple[str, ...]
    provisions: tuple[ProvisionProposal, ...]
    plan: tuple[PlannedEntry, ...]  # the entries close() would post after the provisions (empty for EÜR)
    next_year_opening: tuple[PlanLine, ...]  # the balances the next year starts with


def _user_id(user: Mapping[str, Any]) -> str:
    return str(user.get("id") or "system")


class YearClosingService:
    def __init__(
        self,
        store: Repository,
        journal: JournalService | None = None,
        locks: LockService | None = None,
        tax_bookings: TaxBookingService | None = None,
    ):
        self.store = store
        self.journal = journal or JournalService(store)
        self.locks = locks or LockService(store)
        self.tax_bookings = tax_bookings or TaxBookingService(store, self.journal)

    # ---------- helpers
    def _row(self, year: int) -> Record:
        rows = self.store.find(COLLECTION, year=year)
        if not rows:
            raise ServiceError(404, "Fiscal year not found")
        return rows[0]

    @staticmethod
    def _chart(row: Record) -> Chart:
        year = int(row["year"])
        try:
            return chart_for(cast(ChartName, row["chart"]), year)
        except ChartMissing as e:
            raise ServiceError(409, f"No chart of accounts for fiscal year {year}: {e}") from None

    def _start(self) -> date:
        start = accounting_start(self.store.settings())
        if start is None:
            raise ServiceError(409, "Accounting is not set up yet")
        return start

    def _balances(self, year: int) -> dict[str, int]:
        """Sum of the lines of the entries of fiscal ``year`` per account number (debit positive)."""
        out: dict[str, int] = {}
        offset = 0
        while True:
            page = self.store.list_journal_entries(year=year, limit=PAGE, offset=offset)
            for e in page:
                for ln in e["lines"]:
                    acc = str(ln["account"])
                    out[acc] = out.get(acc, 0) + int(ln["amount_cents"])
            if len(page) < PAGE:
                return out
            offset += PAGE

    def _blockers(self, year: int, row: Record) -> list[str]:
        out: list[str] = []
        if row.get("closed_at") is not None:
            out.append(f"Fiscal year {year} is closed already")
            return out
        prev = self.store.find(COLLECTION, year=year - 1)
        if prev and prev[0].get("closed_at") is None:
            out.append(f"Fiscal year {year - 1} is not closed")
        start = self._start()
        locked = {(int(r["year"]), int(r["month"])) for r in self.store.list("period_locks")}
        for month in range(1, 12):
            if (year, month) >= (start.year, start.month) and (year, month) not in locked:
                out.append(f"{year}-{month:02d} is not locked; lock the months up to November first")
        if (year, 12) not in locked and self.locks.today() <= date(year, 12, 31):
            out.append(f"December {year} has not ended yet")
        for r in self.store.find("vat_returns", year=year, status="draft"):
            if (r.get("period_kind"), int(r.get("period_no") or 0)) not in _DRAFT_OK:
                out.append(f"A VAT return of {year} ({r.get('period_kind')} {r.get('period_no')}) is still a draft")
        nxt = self.store.find(COLLECTION, year=year + 1)
        if nxt and nxt[0].get("chart") != row.get("chart") and row.get("income_basis") == "accrual":
            out.append(f"Fiscal year {year + 1} uses another chart; its opening balances need a manual entry")
        return out

    def _opening_conflict(self, year: int, carried: Mapping[str, int]) -> str | None:
        """A blocker when ``year`` + 1 holds an opening entry whose content differs from the closing balances."""
        existing = self.store.get_journal_entry_by_event_key(f"open:{year + 1}:balances")
        if existing is None:
            return None
        stored: dict[str, int] = {}
        for ln in existing["lines"]:
            acc = str(ln["account"])
            stored[acc] = stored.get(acc, 0) + int(ln["amount_cents"])
        stored = {a: c for a, c in stored.items() if c}
        if stored != {a: c for a, c in carried.items() if c}:
            return f"Fiscal year {year + 1} already holds an opening entry with different balances"
        return None

    def _kinds(self) -> list[ProvisionKind]:
        company = effective_settings(self.store.settings()).get("company_type")
        if company == "gmbh":
            return ["trade_tax", "corporate_tax"]
        if company == "gewerbe":
            return ["trade_tax"]
        return []

    # ---------- the posting plan (pure over balances)
    @staticmethod
    def _plan(year: int, chart: Chart, balances: dict[str, int]) -> tuple[list[PlannedEntry], dict[str, int]]:
        """The three closing entries for ``balances`` (account -> cents) and the balances the next year opens with
        (after the VAT transfer and the P&L transfer, before the balance closing)."""
        klass = {str(a.account): a.account_class for _, a in chart.roles}
        klass.update({str(a.account): a.account_class for a in chart.accounts})
        role_acc = {str(r): str(a.account) for r, a in chart.roles}
        bal = {k: v for k, v in balances.items() if v}
        entries: list[PlannedEntry] = []
        prev_year = role_acc["vat_previous_year"]
        carry = role_acc["profit_carryforward"]
        closing = role_acc["closing_balance"]
        counters = {role_acc[r] for r in _COUNTER_ROLES}

        vat_accounts = sorted({role_acc[r] for r in VAT_ROLES} - {prev_year})
        moved = {a: bal[a] for a in vat_accounts if a in bal}
        if moved:
            lines = [PlanLine(a, -b) for a, b in moved.items()]
            total = sum(moved.values())
            if total:
                lines.append(PlanLine(prev_year, total))
            entries.append(
                PlannedEntry(f"close:{year}:vat", "year_close_vat", f"Year closing {year}: VAT", tuple(lines))
            )
            for a in moved:
                del bal[a]
            bal[prev_year] = bal.get(prev_year, 0) + total
            if not bal[prev_year]:
                del bal[prev_year]

        pnl = {a: b for a, b in bal.items() if klass.get(a) == "pl"}
        if pnl:
            lines = [PlanLine(a, -b) for a, b in sorted(pnl.items())]
            result = sum(pnl.values())
            if result:
                lines.append(PlanLine(carry, result))
            entries.append(
                PlannedEntry(f"close:{year}:pnl", "year_close_pnl", f"Year closing {year}: result", tuple(lines))
            )
            for a in pnl:
                del bal[a]
            bal[carry] = bal.get(carry, 0) + result
            if not bal[carry]:
                del bal[carry]

        carried = {a: b for a, b in bal.items() if a not in counters}
        if carried:
            if sum(carried.values()) != 0:
                raise ServiceError(409, f"The balances of {year} do not net to 0; check the journal")
            lines = [PlanLine(a, -b) for a, b in sorted(carried.items())]
            debits = sum(b for b in carried.values() if b > 0)
            lines += [PlanLine(closing, debits), PlanLine(closing, -debits)]
            entries.append(
                PlannedEntry(
                    f"close:{year}:balance", "year_close_balance", f"Year closing {year}: balances", tuple(lines)
                )
            )
        return entries, carried

    # ---------- preview
    def preview(self, year: int, provisions: Sequence[ProvisionInput] = ()) -> YearClosingPreview:
        """What closing ``year`` would do. ``provisions`` are the amounts the user is about to confirm (accrual); the
        plan includes their effect on the result."""
        row = self._row(year)
        accrual = row.get("income_basis") == "accrual"
        blockers = self._blockers(year, row)
        proposals = tuple(
            ProvisionProposal(
                kind,
                self.tax_bookings.prepayments_expensed(kind, year),
                self.tax_bookings.year_provision(kind, year),
            )
            for kind in (self._kinds() if accrual else [])
        )
        if not accrual:
            if provisions:
                raise ServiceError(422, "Tax provisions exist only under the accrual basis")
            return YearClosingPreview(year, "eur", tuple(blockers), (), (), ())
        chart = self._chart(row)
        balances = self._balances(year)
        role_acc = {str(r): str(a.account) for r, a in chart.roles}
        for p in provisions:
            if p.kind not in _EXPENSE_ROLE:
                raise ServiceError(422, f"Unknown provision kind '{p.kind}'")
            balances[role_acc[_EXPENSE_ROLE[p.kind]]] = (
                balances.get(role_acc[_EXPENSE_ROLE[p.kind]], 0) + p.amount_cents
            )
            balances[role_acc[_PROVISION_ROLE[p.kind]]] = (
                balances.get(role_acc[_PROVISION_ROLE[p.kind]], 0) - p.amount_cents
            )
        plan, carried = self._plan(year, chart, balances)
        conflict = self._opening_conflict(year, carried)
        if conflict and row.get("closed_at") is None:
            blockers.append(conflict)
        opening = tuple(PlanLine(a, b) for a, b in sorted(carried.items()))
        return YearClosingPreview(year, "accrual", tuple(blockers), proposals, tuple(plan), opening)

    # ---------- close
    def _post_plan(self, row: Record, entry: PlannedEntry, user: Mapping[str, Any]) -> None:
        year = int(row["year"])
        source = JournalSource(
            event=entry.event, created_by=_user_id(user), origin="auto", source_coll=COLLECTION, source_number=str(year)
        )
        self.journal.post(
            entry.event_key,
            date(year, 12, 31),
            entry.text,
            source,
            [PostLine(account=ln.account, amount_cents=ln.amount_cents) for ln in entry.lines],
        )

    def _seal(self, year: int, user: Mapping[str, Any]) -> Record:
        """Lock December, then set ``closed_at/closed_by`` and the chain anchor on the year row."""
        self.locks.lock_up_to(year, 12, REASON_YEAR_CLOSE, _user_id(user))
        last_hash, count = self.journal.chain_tail(year)
        row = self._row(year)
        return self.store.save(
            COLLECTION,
            {
                **row,
                "closed_at": int(time.time()),
                "closed_by": _user_id(user),
                "closing_hash": last_hash,
                "closing_entry_no": count,
            },
        )

    def close(self, year: int, confirmed_provisions: Sequence[ProvisionInput], user: Mapping[str, Any]) -> Record:
        """Close ``year`` (the route allows the boss only). 404 unknown year, 409 closed already or blocked, 422
        provisions under EÜR or a kind that does not fit the company type. Returns the closed fiscal year row."""
        with self.store.transaction():
            row = self._row(year)
            if row.get("closed_at") is not None:
                raise ServiceError(409, f"Fiscal year {year} is closed already")
            if row.get("income_basis") != "accrual":
                if confirmed_provisions:
                    raise ServiceError(422, "Tax provisions exist only under the accrual basis")
                return self.end_year(year, user)
            blockers = self._blockers(year, row)
            if blockers:
                raise ServiceError(409, "; ".join(blockers))
            kinds = [p.kind for p in confirmed_provisions]
            if len(set(kinds)) != len(kinds):
                raise ServiceError(422, "At most one provision per kind")
            for p in confirmed_provisions:
                if p.kind == "trade_tax":
                    self.tax_bookings.trade_tax_provision(year, p.amount_cents, user)
                elif p.kind == "corporate_tax":
                    self.tax_bookings.corporate_tax_provision(year, p.amount_cents, user)
                else:
                    raise ServiceError(422, f"Unknown provision kind '{p.kind}'")
            chart = self._chart(row)
            plan, carried = self._plan(year, chart, self._balances(year))
            conflict = self._opening_conflict(year, carried)
            if conflict:
                raise ServiceError(409, conflict)
            for entry in plan:
                self._post_plan(row, entry, user)
            closed = self._seal(year, user)
            self._open_next(year, carried, user)
            return closed

    def end_year(self, year: int, user: Mapping[str, Any]) -> Record:
        """EÜR year end: no entry; December is locked, the year is closed (chain anchor set) and year + 1 created.
        Open items stay open."""
        with self.store.transaction():
            row = self._row(year)
            if row.get("closed_at") is not None:
                raise ServiceError(409, f"Fiscal year {year} is closed already")
            if row.get("income_basis") == "accrual":
                raise ServiceError(422, "An accrual year is closed with the year closing")
            blockers = self._blockers(year, row)
            if blockers:
                raise ServiceError(409, "; ".join(blockers))
            closed = self._seal(year, user)
            self.journal.ensure_year(year + 1, _user_id(user))
            return closed

    def _open_next(self, year: int, carried: dict[str, int], user: Mapping[str, Any]) -> None:
        """Year + 1 (created from this year's bases when missing) and its opening entry from the closing balances."""
        self.journal.ensure_year(year + 1, _user_id(user))
        if carried:
            self.tax_bookings.opening_balances(year + 1, [(a, b) for a, b in sorted(carried.items())], user)
