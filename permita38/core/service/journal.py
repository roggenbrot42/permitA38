"""The accounting journal's only writer (docs/specs/accounting.md 7, 7a, 8.1, 18).

``JournalService.post`` takes one *event* (a unique ``event_key``) with its lines and stores it as one entry:

* balanced (the amounts sum to 0), accounts and roles exist in the chart of the entry's fiscal year;
* idempotent: an existing ``event_key`` with identical content returns the existing entry, different content is a 409;
* the fiscal year (the year of the ``booking_date``) exists and is not closed;
* ``entry_no`` is gapless per year (counter ``journal`` in ``number_sequences``, same transaction as the insert);
* hash chain per fiscal year: ``hash = sha256(prev_hash || canonical_json)`` (see :func:`canonical_json`); the first
  entry of every year chains to 64 zeros, so a late entry in an earlier open year never forks a later year.

Entries are inserted only through ``Repository.insert_journal_entry`` (plain INSERT, never an upsert); the database
refuses UPDATE and DELETE (append-only guard). No SQL here.
"""

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal, cast, get_args

from permita38.core import models
from permita38.core.charts import Chart, ChartMissing, ChartName, Role, chart_for
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.vat_rules import TAX_KEYS, TaxKind

ZERO_HASH = "0" * 64
NUMBER_DOC_TYPE = "journal"
MAX_TEXT = 200
PAGE = 500

# The fields that go into the hash, besides ``hash`` itself; ids and storage timestamps are not part of it.
ENTRY_FIELDS = (
    "fiscal_year", "entry_no", "document_date", "booking_date", "event", "event_key", "source_coll", "source_id",
    "source_number", "reverses_id", "counterparty_id", "cp_vat_id", "cp_country", "text", "origin", "created_at",
    "created_by", "prev_hash",
)  # fmt: skip
LINE_FIELDS = (
    "line_no", "role", "account", "amount_cents", "tax_key", "tax_kind", "tax_rate", "tax_base_cents", "vat_date",
    "income_date", "open_item_id", "treatment",
)  # fmt: skip
_CONTENT_FIELDS = (
    "document_date", "event", "text", "origin", "source_coll", "source_id", "source_number", "reverses_id",
    "counterparty_id", "cp_vat_id", "cp_country",
)  # fmt: skip

Origin = Literal["auto", "manual", "opening"]


@dataclass(frozen=True)
class PostLine:
    """One line of an event. The account **or** the role names the account (both must agree if both are given).
    ``amount_cents`` positive = debit, negative = credit; 0 only for a statistical line."""

    amount_cents: int
    role: str | None = None
    account: str | None = None
    tax_key: str | None = None
    tax_kind: str | None = None
    tax_rate: str | None = None
    tax_base_cents: int | None = None
    vat_date: date | None = None
    income_date: date | None = None
    open_item_id: str | None = None
    treatment: str | None = None


@dataclass(frozen=True)
class JournalSource:
    """What the entry is about; ``event`` is the posting event name, ``created_by`` the acting user id."""

    event: str
    created_by: str = "system"
    origin: Origin = "auto"
    source_coll: str | None = None
    source_id: str | None = None
    source_number: str | None = None
    reverses_id: str | None = None
    counterparty_id: str | None = None
    cp_vat_id: str | None = None
    cp_country: str | None = None


@dataclass(frozen=True)
class ChainFinding:
    """A problem found by :meth:`JournalService.verify_chain`; ``severity`` is always ``error``."""

    code: str
    entry_no: int | None
    message: str
    severity: str = "error"


def _decimal_as_str(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not part of the canonical journal form")


def canonical_json(entry: Mapping[str, Any], lines: Sequence[Mapping[str, Any]]) -> str:
    """Sorted keys, no spaces, integers as integers, dates as ``YYYY-MM-DD`` strings, absent values as ``null``;
    the lines ordered by ``line_no``; ``Decimal`` values (e.g. ``tax_rate``) as strings. ASCII-escaped
    (``json.dumps`` defaults)."""
    body: dict[str, Any] = {k: entry.get(k) for k in ENTRY_FIELDS}
    body["lines"] = [{k: ln.get(k) for k in LINE_FIELDS} for ln in sorted(lines, key=lambda ln: int(ln["line_no"]))]
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=_decimal_as_str)


def entry_hash(prev_hash: str, entry: Mapping[str, Any], lines: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(entry, lines)).encode("utf-8")).hexdigest()


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class JournalService:
    def __init__(self, store: Repository, clock: Callable[[], float] = time.time):
        self.store = store
        self.clock = clock

    # ---------- helpers
    def _booking_date(self, document_date: date) -> date:
        """``max(document_date, first day of the first unlocked month)`` (spec 7.8). Locks (``period_locks``, E7)
        lock whole months in order, so the first unlocked month follows the latest locked one; without locks: the
        document date. Closing-date exception: when that date falls into a later fiscal year while the document's
        own year is not closed, the entry is booked on 31 December of the document's year."""
        locks = self.store.list("period_locks")
        if not locks:
            return document_date
        year, month = max((int(r["year"]), int(r["month"])) for r in locks)
        first_open = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        booking = max(document_date, first_open)
        if booking.year > document_date.year:
            rows = self.store.find("fiscal_years", year=document_date.year)
            if not rows or rows[0].get("closed_at") is None:
                return date(document_date.year, 12, 31)
        return booking

    @staticmethod
    def _chart(row: Record, year: int) -> Chart:
        try:
            return chart_for(cast(ChartName, row["chart"]), year)
        except ChartMissing as e:
            raise ServiceError(409, f"No chart of accounts for fiscal year {year}: {e}") from None

    @staticmethod
    def _validate_lines(lines: Sequence[PostLine]) -> None:
        if not lines:
            raise ServiceError(422, "An entry needs at least one line")
        for i, ln in enumerate(lines, start=1):
            where = f"Line {i}"
            if not _is_int(ln.amount_cents):
                raise ServiceError(422, f"{where}: amount_cents must be an integer")
            if ln.tax_base_cents is not None and not _is_int(ln.tax_base_cents):
                raise ServiceError(422, f"{where}: tax_base_cents must be an integer")
            if ln.role is None and ln.account is None:
                raise ServiceError(422, f"{where}: needs an account or a role")
            if ln.tax_key is not None and ln.tax_key not in TAX_KEYS:
                raise ServiceError(422, f"{where}: unknown tax key '{ln.tax_key}'")
            if ln.tax_kind is not None and ln.tax_kind not in get_args(TaxKind):
                raise ServiceError(422, f"{where}: unknown tax kind '{ln.tax_kind}'")
            if ln.tax_kind is not None and ln.tax_key is None:
                raise ServiceError(422, f"{where}: a tax kind needs a tax key")
            if ln.tax_key is not None and ln.tax_key != "NONE" and ln.vat_date is None:
                raise ServiceError(422, f"{where}: tax key '{ln.tax_key}' needs a vat_date")
        balance = sum(ln.amount_cents for ln in lines)
        if balance != 0:
            raise ServiceError(422, f"The entry is not balanced (the amounts add up to {balance} cents, not 0)")

    @staticmethod
    def _resolve(chart: Chart, lines: Sequence[PostLine]) -> list[Record]:
        """The line records (with ``line_no``) after checking every account and role against the chart."""
        known = {a.account for a in chart.accounts}
        out: list[Record] = []
        for i, ln in enumerate(lines, start=1):
            account = ln.account
            if ln.role is not None:
                try:
                    from_role = str(chart.account(Role(ln.role)))
                except KeyError:
                    raise ServiceError(422, f"Line {i}: unknown role '{ln.role}'") from None
                if account is not None and account != from_role:
                    raise ServiceError(422, f"Line {i}: role '{ln.role}' is account {from_role}, not {account}")
                account = from_role
            if account is None or account not in known:
                raise ServiceError(422, f"Line {i}: unknown account '{account}' in chart {chart.chart}")
            out.append(
                {
                    "line_no": i,
                    "role": ln.role,
                    "account": account,
                    "amount_cents": ln.amount_cents,
                    "tax_key": ln.tax_key,
                    "tax_kind": ln.tax_kind,
                    "tax_rate": str(ln.tax_rate) if ln.tax_rate is not None else None,
                    "tax_base_cents": ln.tax_base_cents,
                    "vat_date": _iso(ln.vat_date),
                    "income_date": _iso(ln.income_date),
                    "open_item_id": ln.open_item_id,
                    "treatment": ln.treatment,
                }
            )
        return out

    @staticmethod
    def _content(entry: Mapping[str, Any], lines: Sequence[Mapping[str, Any]]) -> object:
        """What identifies an event's content (not where or when it was stored: booking date, number, hashes)."""
        head = tuple(entry.get(k) for k in _CONTENT_FIELDS)
        keys = tuple(k for k in LINE_FIELDS if k != "role")  # a role and its account are the same line
        body = tuple(tuple(ln.get(k) for k in keys) for ln in sorted(lines, key=lambda ln: int(ln["line_no"])))
        return head, body

    def _same_event(self, existing: Record, entry: Record, lines: Sequence[PostLine]) -> bool:
        year = int(existing["fiscal_year"])
        rows = self.store.find("fiscal_years", year=year)
        if not rows:
            return False
        try:
            resolved = self._resolve(self._chart(rows[0], year), lines)
        except ServiceError:
            return False
        return self._content(existing, existing["lines"]) == self._content(entry, resolved)

    # ---------- write
    def post(
        self,
        event_key: str,
        document_date: date,
        text: str,
        source: JournalSource,
        lines: Sequence[PostLine],
    ) -> models.JournalEntry:
        """Post one event. See the module docstring. Errors: 422 unbalanced / unknown account or role / bad line,
        409 an existing ``event_key`` with different content, a missing or closed fiscal year."""
        if not event_key.strip():
            raise ServiceError(422, "event_key is required")
        if not text.strip() or len(text) > MAX_TEXT:
            raise ServiceError(422, f"text is required and at most {MAX_TEXT} characters")
        if not source.event.strip() or not source.created_by.strip():
            raise ServiceError(422, "event and created_by are required")
        if source.origin not in get_args(Origin):
            raise ServiceError(422, f"Unknown origin '{source.origin}'")
        self._validate_lines(lines)
        entry: Record = {
            "document_date": document_date.isoformat(),
            "event": source.event,
            "event_key": event_key,
            "source_coll": source.source_coll,
            "source_id": source.source_id,
            "source_number": source.source_number,
            "reverses_id": source.reverses_id,
            "counterparty_id": source.counterparty_id,
            "cp_vat_id": source.cp_vat_id,
            "cp_country": source.cp_country,
            "text": text,
            "origin": source.origin,
            "created_by": source.created_by,
        }
        with self.store.transaction():
            existing = self.store.get_journal_entry_by_event_key(event_key)
            if existing is not None:
                if not self._same_event(existing, entry, lines):
                    raise ServiceError(409, f"event_key '{event_key}' already exists with different content")
                return models.JournalEntry.model_validate(existing)
            booking = self._booking_date(document_date)
            year = booking.year
            rows = self.store.find("fiscal_years", year=year)
            if not rows:
                raise ServiceError(409, f"Fiscal year {year} is not open")
            if rows[0].get("closed_at") is not None:
                raise ServiceError(409, f"Fiscal year {year} is closed")
            resolved = self._resolve(self._chart(rows[0], year), lines)
            entry.update(
                fiscal_year=year,
                booking_date=booking.isoformat(),
                entry_no=self.store.next_number(NUMBER_DOC_TYPE, str(year)),
                created_at=int(self.clock()),
                prev_hash=self.store.last_journal_hash(year) or ZERO_HASH,
            )
            entry["hash"] = entry_hash(entry["prev_hash"], entry, resolved)
            return models.JournalEntry.model_validate(self.store.insert_journal_entry(entry, resolved))

    # ---------- read
    def get_by_event_key(self, event_key: str) -> models.JournalEntry | None:
        row = self.store.get_journal_entry_by_event_key(event_key)
        return models.JournalEntry.model_validate(row) if row is not None else None

    def entries(
        self,
        year: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[models.JournalEntry]:
        rows = self.store.list_journal_entries(year, _iso(date_from), _iso(date_to), limit, offset)
        return [models.JournalEntry.model_validate(r) for r in rows]

    def verify_chain(self, year: int) -> list[ChainFinding]:
        """Check only the entries of ``year`` (no cross-year comparison): balanced, numbered 1..n without gaps, ``prev_hash`` links and every hash
        recomputed from the stored content. A break means the journal changed outside the app or forked."""
        findings: list[ChainFinding] = []
        expected_prev = ZERO_HASH  # the chain is per fiscal year
        expected_no = 1
        offset = 0
        while True:
            page = self.store.list_journal_entries(year=year, limit=PAGE, offset=offset)
            for e in page:
                no = int(e["entry_no"])
                if no != expected_no:
                    findings.append(ChainFinding("journal.gap", no, f"entry number {no} follows {expected_no - 1}"))
                expected_no = no + 1
                if sum(int(ln["amount_cents"]) for ln in e["lines"]) != 0:
                    findings.append(ChainFinding("journal.unbalanced", no, f"entry {no} is not balanced"))
                if e.get("prev_hash") != expected_prev:
                    findings.append(
                        ChainFinding("journal.prev_hash", no, f"entry {no} does not follow the entry before")
                    )
                if e.get("hash") != entry_hash(str(e.get("prev_hash")), e, e["lines"]):
                    findings.append(
                        ChainFinding("journal.hash", no, f"entry {no} changed outside the app (hash mismatch)")
                    )
                expected_prev = str(e.get("hash"))
            if len(page) < PAGE:
                return findings
            offset += PAGE
