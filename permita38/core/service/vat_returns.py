"""VAT return records (docs/specs/ustva.md "Status, finalise, filed, reopen", "Data model", accounting.md 8.1, 11).

A return is a row of ``vat_returns``. A **draft** is computed live from the journal on every read (nothing but the period
and the snapshot of the year's bases is stored). **Finalising** stores the boxes, the booked figures, the source line ids
and the rules version, and locks the months of the period (reason ``vat_return:<id>``) in the same transaction.
Reopening (final and not filed only) clears the stored figures; the months stay locked. The computation itself is the
pure :mod:`permita38.core.vat.ustva`. Amended returns, drift, external returns and payments are not part of this module
yet. No SQL here.
"""

import hashlib
import time
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

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
MAX_SOURCE_LINES = 20_000
DUE_SOON_DAYS = 7
IGNORED_KEYS = frozenset({"NONE", "KU"})


class VatReturnListItem(BaseModel):
    """One period row of the year list (``GET /vat/returns?year=``)."""

    model_config = ConfigDict(extra="forbid")
    year: int
    kind: str
    no: int
    from_date: str
    to_date: str
    due_date: str
    id: str | None = None
    result: str | None = None  # Kz 83 as a decimal string, only of a final return
    lines: int | None = None  # number of source lines, only of a final return
    state: str


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

    def __init__(self, row: Mapping[str, Any]):
        self.entry = {
            "id": row.get("entry_id"),
            "entry_no": row.get("entry_no"),
            "booking_date": row.get("booking_date"),
            "source_number": row.get("source_number"),
            "created_at": row.get("entry_created_at"),
        }
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

    def _sources(self, lo: date, hi: date) -> list[_Source]:
        """The journal lines whose ``vat_date`` is in ``[lo, hi]`` (the journal is the only source; the repository
        reads them by the ``vat_date`` index, never by paging the whole journal)."""
        return [_Source(r) for r in self.store.journal_lines_by_vat_date(_iso(lo), _iso(hi))]

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
        sources = self._sources(lo, hi)
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

    @staticmethod
    def _digest(ids: list[str]) -> str:
        return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()

    @classmethod
    def _sources_json(cls, ids: list[str]) -> Record:
        """What a draft / preview shows (docs/specs/ustva.md "Data model"): up to ``MAX_SOURCE_LINES`` ids; above
        that the first ``MAX_SOURCE_LINES`` and ``source_lines_truncated`` with the full count and a hash of the id
        list. Never a silent cut."""
        n = len(ids)
        if n <= MAX_SOURCE_LINES:
            return {"source_lines": ids, "source_lines_truncated": False, "source_lines_count": n}
        return {
            "source_lines": ids[:MAX_SOURCE_LINES],
            "source_lines_truncated": True,
            "source_lines_count": n,
            "source_lines_hash": cls._digest(ids),
        }

    @classmethod
    def _stored_sources(cls, ids: list[str]) -> list[str] | dict[str, Any]:
        """What a final return stores in ``source_lines``: the ids, or above ``MAX_SOURCE_LINES`` an object
        ``{"truncated": true, "count": n, "sha256": hash of the sorted id list}`` (the per-box sums are in
        ``boxes``; the drill-down then queries the journal by period)."""
        if len(ids) <= MAX_SOURCE_LINES:
            return ids
        return {"truncated": True, "count": len(ids), "sha256": cls._digest(ids)}

    @staticmethod
    def _view(row: Record) -> Record:
        """A stored final row as the API shows it (``source_lines`` is always a list; the truncation flags are
        added when the ids were not stored)."""
        stored = row.get("source_lines")
        if isinstance(stored, dict):
            return {
                **row,
                "source_lines": [],
                "source_lines_truncated": True,
                "source_lines_count": stored.get("count"),
                "source_lines_hash": stored.get("sha256"),
            }
        if isinstance(stored, list):
            return {**row, "source_lines_truncated": False, "source_lines_count": len(stored)}
        return row

    @staticmethod
    def _rules_version(fy: Record, rules: VatRules) -> str:
        """The chart part of the year's snapshot plus the VAT table actually used by the computation."""
        chart_part = str(fy.get("rules_version") or "").split("/")[0] or f"{fy.get('chart')}-none"
        return f"{chart_part}/vat-{rules.form_year}"

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
            **self._sources_json([s.line_id for s in used]),
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
                item["lines"] = int(self._view(row).get("source_lines_count") or 0)
            if row is None and small:
                res, _r, used, _b = self._compute(fy, kind, no)
                if not res.required:
                    item["state"] = "not_required"
                    out.append(VatReturnListItem(**item).model_dump())
                    continue
            item["state"] = self._state(row, due)
            out.append(VatReturnListItem(**item).model_dump())
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
            **self._sources_json([s.line_id for s in used]),
            "result_cents": result.result_cents,
            "special_prepayment_credit_cents": 0,
            "rounding_difference_cents": result.rounding_difference,
        }

    def get(self, rid: str) -> Record:
        row = self._row(rid)
        return self._view(row) if row.get("status") == "final" else self._live(row)

    def lines(self, rid: str, kz: str) -> dict[str, Any]:
        """The journal lines behind box ``kz`` of return ``rid`` (a final return: the lines it stored)."""
        row = self._row(rid)
        year = int(row["year"])
        rules = self._rules(year)
        if kz not in {b.kz for b in rules.boxes}:
            raise ServiceError(404, f"Unknown box {kz}")
        lo, hi = date.fromisoformat(str(row["from_date"])), date.fromisoformat(str(row["to_date"]))
        final = row.get("status") == "final"
        stored: set[str] | None = None
        if final and not isinstance(row.get("source_lines"), dict):
            stored = set(row.get("source_lines") or [])  # may be empty: a final return without lines shows none
        small = bool(row.get("small_business")) if final else bool(self._year(year).get("small_business"))
        mapping = {(m.tax_key, m.tax_kind): m for m in rules.map}
        out: list[dict[str, Any]] = []
        for src in self._sources(lo, hi):
            ln = src.line
            if not self._in_period(src, lo, hi):
                continue
            if small and ln.tax_kind == "input":
                continue  # a small business has no input VAT (as in _compute)
            if stored is not None and src.line_id not in stored:
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
                    "rules_version": self._rules_version(fy, rules),
                    "due_date": _iso(self._due(fy, kind, no)),
                    "status": "final",
                    "boxes": {kz: _box_json(f) for kz, f in result.boxes.items()},
                    "source_lines": self._stored_sources(ids),
                    "result_cents": int(result.result_cents),
                    "special_prepayment_credit_cents": 0,
                    "rounding_difference_cents": int(result.rounding_difference),
                    "finalised_at": now,
                    "finalised_by": user.get("id"),
                },
            )
            self.locks.lock_up_to(hi.year, hi.month, f"vat_return:{rid}", str(user.get("id")))
            return self._view(saved)

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
            return self._view(
                self.store.save(
                    COLLECTION, {**row, "filed_on": filed_on, "reference": reference or None, "note": note or None}
                )
            )

    def delete(self, rid: str) -> None:
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "draft":
                raise ServiceError(409, "Only a draft can be deleted")
            self.store.delete(COLLECTION, rid)
