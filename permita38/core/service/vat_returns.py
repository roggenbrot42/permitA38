"""VAT return records (docs/specs/ustva.md "Status, finalise, filed, reopen", "Data model", accounting.md 8.1, 11).

A return is a row of ``vat_returns``. A **draft** is computed live from the journal on every read (nothing but the period
and the snapshot of the year's bases is stored). **Finalising** stores the boxes, the booked figures, the source line ids
and the rules version, and locks the months of the period (reason ``vat_return:<id>``) in the same transaction.
Reopening (final and not filed only) clears the stored figures; the months stay locked. The computation itself is the
pure :mod:`permita38.core.vat.ustva`. Amended returns, drift, external returns and payments are not part of this module
yet. No SQL here.
"""

import time
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Any

from permita38.core.accounting.types import Cents, Line, Role, TaxKey
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service.locks import LockService
from permita38.core.settings import effective_settings
from permita38.core.vat import ustva
from permita38.core.vat.deadlines import PeriodKind, period_bounds, ustva_due
from permita38.core.vat_rules import VatRules, VatRulesMissing, vat_rules_for

COLLECTION = "vat_returns"
JOURNAL_PAGE = 500
MAX_SOURCE_LINES = 20_000
DUE_SOON_DAYS = 7
IGNORED_KEYS = frozenset({"NONE", "KU"})


def money(cents: int) -> str:
    """Integer cents as a decimal string with two places (``-5.70``)."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(int(cents)), 100)
    return f"{sign}{whole}.{frac:02d}"


def _iso(day: date) -> str:
    return day.isoformat()


def _box_json(fig: ustva.BoxFigure) -> dict[str, str]:
    out: dict[str, str] = {}
    if fig.base is not None:
        out["base"] = money(fig.base * 100)
    if fig.booked_base is not None:
        out["booked_base"] = money(fig.booked_base)
    if fig.tax is not None:
        out["tax"] = money(fig.tax)
    if fig.booked_tax is not None:
        out["booked_tax"] = money(fig.booked_tax)
    if fig.amount is not None:
        out["amount"] = money(fig.amount)
    return out


class _Source:
    """A journal line with the entry it belongs to (for ids and the drill-down)."""

    def __init__(self, entry: Mapping[str, Any], row: Mapping[str, Any]):
        self.entry = entry
        self.row = row
        vd = row.get("vat_date")
        self.vat_date: date | None = date.fromisoformat(str(vd)) if vd else None
        self.line = Line(
            Role(str(row.get("role") or "")),
            Cents(int(row.get("amount_cents") or 0)),
            TaxKey(str(row["tax_key"])) if row.get("tax_key") else None,
            row.get("tax_kind"),
            row.get("tax_rate"),
            Cents(int(row["tax_base_cents"])) if row.get("tax_base_cents") is not None else None,
            self.vat_date,
        )

    @property
    def line_id(self) -> str:
        return str(self.row.get("id") or "")


class VatReturnService:
    def __init__(
        self,
        store: Repository,
        locks: LockService,
        today: Callable[[], date] = date.today,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.locks = locks
        self.today = today
        self.clock = clock

    # ---------- helpers
    def _year(self, year: int) -> Record:
        rows = self.store.find("fiscal_years", year=year)
        if not rows:
            raise ServiceError(422, f"Accounting is not initialised for {year}")
        return rows[0]

    @staticmethod
    def _rules(year: int) -> VatRules:
        try:
            return vat_rules_for(year)
        except VatRulesMissing as e:
            raise ServiceError(422, f"No VAT rules for {year}: {e}") from None

    def _check_period(self, fy: Record, kind: str, no: int) -> None:
        if kind not in ("month", "quarter"):
            raise ServiceError(422, "kind must be month or quarter")
        if no < 1 or no > (4 if kind == "quarter" else 12):
            raise ServiceError(422, f"period number {no} is out of range for a {kind}")
        if fy.get("small_business"):
            if kind != "quarter":
                raise ServiceError(422, "A small business files per quarter (only when it has § 13b lines)")
        elif fy.get("vat_frequency") != kind:
            raise ServiceError(422, f"The VAT frequency of {fy['year']} is {fy.get('vat_frequency')}, not {kind}")

    def _due(self, fy: Record, kind: str, no: int) -> date:
        state = str(effective_settings(self.store.settings()).get("state") or "")
        return ustva_due(int(fy["year"]), kind, no, bool(fy.get("dauerfrist")), state)  # type: ignore[arg-type]

    def _sources(self) -> list[_Source]:
        """Every journal line (the journal is the only source). A line's ``vat_date`` is independent of the entry's
        booking date, so the whole journal is scanned."""
        out: list[_Source] = []
        offset = 0
        while True:
            page = self.store.list_journal_entries(limit=JOURNAL_PAGE, offset=offset)
            for entry in page:
                for row in entry.get("lines") or []:
                    out.append(_Source(entry, row))
            if len(page) < JOURNAL_PAGE:
                return out
            offset += JOURNAL_PAGE

    @staticmethod
    def _in_period(src: _Source, lo: date, hi: date) -> bool:
        ln = src.line
        return (
            ln.tax_key is not None
            and ln.tax_key not in IGNORED_KEYS
            and ln.tax_kind is not None
            and src.vat_date is not None
            and lo <= src.vat_date <= hi
        )

    def _compute(
        self, fy: Record, kind: str, no: int
    ) -> tuple[ustva.UstvaResult, VatRules, list[_Source], tuple[date, date]]:
        year = int(fy["year"])
        lo, hi = period_bounds(year, kind, no)  # type: ignore[arg-type]
        rules = self._rules(year)
        sources = self._sources()
        try:
            result = ustva.compute_boxes(
                (s.line for s in sources),
                lo,
                hi,
                rules,
                small_business=bool(fy.get("small_business")),
            )
        except ValueError as e:
            raise ServiceError(422, str(e)) from None
        used = [s for s in sources if self._in_period(s, lo, hi)]
        if fy.get("small_business"):
            used = [s for s in used if s.line.tax_kind != "input"]
        return result, rules, used, (lo, hi)

    def _snapshot(self, fy: Record) -> Record:
        return {
            "basis": fy.get("vat_basis"),
            "frequency": fy.get("vat_frequency"),
            "dauerfrist": bool(fy.get("dauerfrist")),
            "small_business": bool(fy.get("small_business")),
            "rules_version": fy.get("rules_version"),
        }

    def _row(self, rid: str) -> Record:
        row = self.store.get(COLLECTION, rid)
        if row is None:
            raise ServiceError(404, "VAT return not found")
        return row

    def _live(self, row: Record) -> Record:
        """A draft as the API shows it: the stored row with the boxes computed now."""
        fy = self._year(int(row["year"]))
        result, _rules, used, _bounds = self._compute(fy, str(row["period_kind"]), int(row["period_no"]))
        return {
            **row,
            **self._snapshot(fy),
            "boxes": {kz: _box_json(f) for kz, f in result.boxes.items()},
            "source_lines": [s.line_id for s in used][:MAX_SOURCE_LINES],
            "result_cents": result.result_cents,
            "special_prepayment_credit_cents": 0,
            "rounding_difference_cents": result.rounding_difference,
        }

    def _state(self, row: Record | None, due: date) -> str:
        if row is not None:
            if row.get("filed_on"):
                return "filed"
            if row.get("status") == "final":
                return "final"
        today = self.today()
        if today > due:
            return "overdue"
        if today >= due - timedelta(days=DUE_SOON_DAYS):
            return "due soon"
        return "open"

    # ---------- reads
    def list(self, year: int) -> list[dict[str, Any]]:
        fy = self._year(year)
        small = bool(fy.get("small_business"))
        frequency = fy.get("vat_frequency")
        kind: PeriodKind
        if small:
            kind = "quarter"
        elif frequency in ("month", "quarter"):
            kind = frequency
        else:
            return []
        existing = {
            (r["period_kind"], int(r["period_no"])): r
            for r in self.store.find(COLLECTION, year=year)
            if not r.get("amends_id")
        }
        out: list[dict[str, Any]] = []
        for no in range(1, (4 if kind == "quarter" else 12) + 1):
            lo, hi = period_bounds(year, kind, no)
            due = self._due(fy, kind, no)
            row = existing.get((kind, no))
            item: dict[str, Any] = {
                "year": year,
                "kind": kind,
                "no": no,
                "from_date": _iso(lo),
                "to_date": _iso(hi),
                "due_date": _iso(due),
                "id": row["id"] if row else None,
                "result": None,
                "lines": None,
            }
            if row is not None and row.get("status") == "final":
                item["result"] = money(int(row.get("result_cents") or 0))
                item["lines"] = len(row.get("source_lines") or [])
            if row is None and small:
                res, _r, used, _b = self._compute(fy, kind, no)
                if not res.required:
                    item["state"] = "not_required"
                    out.append(item)
                    continue
            item["state"] = self._state(row, due)
            out.append(item)
        return out

    def preview(self, year: int, kind: str, no: int) -> Record:
        fy = self._year(year)
        self._check_period(fy, kind, no)
        result, _rules, used, (lo, hi) = self._compute(fy, kind, no)
        if not result.required:
            raise ServiceError(422, "No return is required for this period (small business without § 13b lines)")
        return {
            "id": None,
            "year": year,
            "period_kind": kind,
            "period_no": no,
            "from_date": _iso(lo),
            "to_date": _iso(hi),
            "due_date": _iso(self._due(fy, kind, no)),
            "status": "draft",
            **self._snapshot(fy),
            "boxes": {kz: _box_json(f) for kz, f in result.boxes.items()},
            "source_lines": [s.line_id for s in used][:MAX_SOURCE_LINES],
            "result_cents": result.result_cents,
            "special_prepayment_credit_cents": 0,
            "rounding_difference_cents": result.rounding_difference,
        }

    def get(self, rid: str) -> Record:
        row = self._row(rid)
        return row if row.get("status") == "final" else self._live(row)

    def lines(self, rid: str, kz: str) -> dict[str, Any]:
        """The journal lines behind box ``kz`` of return ``rid`` (a final return: the lines it stored)."""
        row = self._row(rid)
        year = int(row["year"])
        rules = self._rules(year)
        if kz not in {b.kz for b in rules.boxes}:
            raise ServiceError(404, f"Unknown box {kz}")
        lo, hi = date.fromisoformat(str(row["from_date"])), date.fromisoformat(str(row["to_date"]))
        stored = set(row.get("source_lines") or []) if row.get("status") == "final" else None
        mapping = {(m.tax_key, m.tax_kind): m for m in rules.map}
        out: list[dict[str, Any]] = []
        for src in self._sources():
            ln = src.line
            if not self._in_period(src, lo, hi):
                continue
            if stored and src.line_id not in stored:
                continue
            m = mapping.get((ln.tax_key, ln.tax_kind)) if ln.tax_key and ln.tax_kind else None
            if m is None or kz not in (m.base_kz, m.tax_kz):
                continue
            booked = -ln.amount_cents if ln.tax_kind == "tax" else ln.amount_cents
            out.append(
                {
                    "line_id": src.line_id,
                    "entry_id": src.entry.get("id"),
                    "entry_no": src.entry.get("entry_no"),
                    "booking_date": src.entry.get("booking_date"),
                    "document": src.entry.get("source_number"),
                    "tax_key": ln.tax_key,
                    "tax_kind": ln.tax_kind,
                    "vat_date": _iso(src.vat_date) if src.vat_date else None,
                    "base": money(ln.tax_base_cents) if ln.tax_base_cents is not None else None,
                    "tax": money(booked) if ln.tax_kind != "base_only" else None,
                }
            )
        out.sort(key=lambda r: (str(r["booking_date"]), int(r["entry_no"] or 0)))
        return {"kz": kz, "lines": out}

    # ---------- writes
    def create_draft(self, year: int, kind: str, no: int, amends_id: str | None = None) -> Record:
        if amends_id:
            raise ServiceError(422, "Amended returns are not available yet")
        fy = self._year(year)
        self._check_period(fy, kind, no)
        with self.store.transaction():
            if self.store.find(COLLECTION, year=year, period_kind=kind, period_no=no, amends_key=""):
                raise ServiceError(409, f"A return for {kind} {no} of {year} already exists")
            result, _rules, _used, (lo, hi) = self._compute(fy, kind, no)
            if not result.required:
                raise ServiceError(422, "No return is required for this period (small business without § 13b lines)")
            row = self.store.save(
                COLLECTION,
                {
                    "year": year,
                    "period_kind": kind,
                    "period_no": no,
                    "from_date": _iso(lo),
                    "to_date": _iso(hi),
                    "due_date": _iso(self._due(fy, kind, no)),
                    "status": "draft",
                    "amends_key": "",
                    "external": False,
                    **self._snapshot(fy),
                },
            )
        return self._live(row)

    def finalize(self, rid: str, user: Mapping[str, Any], confirm_gap: bool = False) -> Record:
        """Freeze the figures and lock the months up to the end of the period, in one transaction. Unlocked earlier
        months (a gap) are listed in a 409 (``months``) unless ``confirm_gap``."""
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "draft":
                raise ServiceError(409, "The return is already final")
            fy = self._year(int(row["year"]))
            kind, no = str(row["period_kind"]), int(row["period_no"])
            result, rules, used, (lo, hi) = self._compute(fy, kind, no)
            to_lock = self.locks.preview_lock_up_to(hi.year, hi.month)  # 422: not ended / before the start
            gap = [f"{y}-{m:02d}" for y, m in to_lock if (y, m) < (lo.year, lo.month)]
            if gap and not confirm_gap:
                raise ServiceError(
                    409,
                    "Earlier months are not locked yet: " + ", ".join(gap),
                    body={"months": gap},
                )
            ids = [s.line_id for s in used]
            now = int(self.clock())
            saved = self.store.save(
                COLLECTION,
                {
                    **row,
                    **self._snapshot(fy),
                    "rules_version": row.get("rules_version") or fy.get("rules_version"),
                    "status": "final",
                    "boxes": {kz: _box_json(f) for kz, f in result.boxes.items()},
                    "source_lines": ids if len(ids) <= MAX_SOURCE_LINES else None,
                    "result_cents": int(result.result_cents),
                    "special_prepayment_credit_cents": 0,
                    "rounding_difference_cents": int(result.rounding_difference),
                    "finalised_at": now,
                    "finalised_by": user.get("id"),
                },
            )
            self.locks.lock_up_to(hi.year, hi.month, f"vat_return:{rid}", str(user.get("id")))
            return saved

    def reopen(self, rid: str) -> Record:
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "final":
                raise ServiceError(409, "Only a final return can be reopened")
            if row.get("filed_on"):
                raise ServiceError(409, "A filed return cannot be reopened; file an amended return")
            saved = self.store.save(
                COLLECTION,
                {
                    **row,
                    "status": "draft",
                    "boxes": {},
                    "source_lines": None,
                    "finalised_at": None,
                    "finalised_by": None,
                },
            )
        return self._live(saved)

    def filed(self, rid: str, filed_on: str, reference: str | None, note: str | None) -> Record:
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "final":
                raise ServiceError(409, "Only a final return can be marked as filed")
            if row.get("filed_on"):
                raise ServiceError(409, "The return is already marked as filed")
            return self.store.save(
                COLLECTION, {**row, "filed_on": filed_on, "reference": reference or None, "note": note or None}
            )

    def delete(self, rid: str) -> None:
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "draft":
                raise ServiceError(409, "Only a draft can be deleted")
            self.store.delete(COLLECTION, rid)
