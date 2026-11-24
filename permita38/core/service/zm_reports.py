"""ZM records (docs/specs/zm.md "Records, status, drift", accounting.md 8.1): ZmReportService.

A report is a row of ``zm_reports``. A **draft** is computed live from the journal on every read. **Finalising** stores
the list (``lines``: vat_id, country, whole-euro ``amount``, ``kind``, exact ``cents``), the ids of the ``EU_SVC`` source
lines and the rules version; it takes no period lock of its own (the UStVA lock covers the months). A **correction**
(``amends_id``) carries the full corrected list of a filed quarter, with 0-rows for customers that dropped to 0.
**Drift** of a final report: ``EU_SVC`` lines of the quarter that are not among the stored source lines. No SQL here.
"""

import builtins
import time
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from permita38.core.accounting.types import Cents, Line, Role, TaxKey
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.settings import effective_settings
from permita38.core.vat import zm as zm_calc
from permita38.core.vat.deadlines import period_bounds, zm_due
from permita38.core.vat_rules import VatRulesMissing, vat_rules_for

COLLECTION = "zm_reports"
DUE_SOON_DAYS = 7
_EU_SVC = "EU_SVC"


class ZmListItem(BaseModel):
    """One quarter row of the year list (``GET /zm?year=``)."""

    model_config = ConfigDict(extra="forbid")
    year: int
    quarter: int
    from_date: str
    to_date: str
    due_date: str
    id: str | None = None
    total: int | None = None  # whole euros, sum of the listed amounts (the live list for a draft or an open quarter)
    rows: int | None = None
    drift: int = 0  # number of entries with late source lines of a final report
    state: str


class _Src:
    """An ``EU_SVC`` journal line with its entry (the VAT id is the issue snapshot of the entry)."""

    def __init__(self, row: Mapping[str, Any], line: Line, vat_id: str):
        self.row = row
        self.line = line
        self.vat_id = vat_id

    @property
    def line_id(self) -> str:
        return str(self.row.get("id") or "")


def _line_of(row: Mapping[str, Any]) -> Line:
    vd = row.get("vat_date")
    return Line(
        Role(str(row.get("role") or "")),
        Cents(int(row.get("amount_cents") or 0)),
        TaxKey(str(row["tax_key"])) if row.get("tax_key") else None,
        row.get("tax_kind"),
        row.get("tax_rate"),
        Cents(int(row["tax_base_cents"])) if row.get("tax_base_cents") is not None else None,
        date.fromisoformat(str(vd)) if vd else None,
    )


class ZmReportService:
    def __init__(
        self, store: Repository, today: Callable[[], date] = date.today, clock: Callable[[], float] = time.time
    ):
        self.store = store
        self.today = today
        self.clock = clock

    # ---------- helpers
    def _year(self, year: int) -> Record:
        rows = self.store.find("fiscal_years", year=year)
        if not rows:
            raise ServiceError(422, f"Accounting is not initialised for {year}")
        return rows[0]

    def _row(self, rid: str) -> Record:
        row = self.store.get(COLLECTION, rid)
        if row is None:
            raise ServiceError(404, "ZM report not found")
        return row

    def _due(self, year: int, quarter: int) -> date:
        state = str(effective_settings(self.store.settings()).get("state") or "")
        return zm_due(year, quarter, state)

    @staticmethod
    def _rules_version(fy: Record, year: int) -> str:
        try:
            form_year = vat_rules_for(year).form_year
        except VatRulesMissing as e:
            raise ServiceError(422, f"No VAT rules for {year}: {e}") from None
        chart_part = str(fy.get("rules_version") or "").split("/")[0] or f"{fy.get('chart')}-none"
        return f"{chart_part}/vat-{form_year}"

    def _sources(self, year: int, quarter: int) -> list[_Src]:
        """The ``EU_SVC`` base lines whose ``vat_date`` lies in the quarter (the journal is the only source)."""
        lo, hi = period_bounds(year, "quarter", quarter)
        entries: dict[str, Record | None] = {}
        out: list[_Src] = []
        for row in self.store.journal_lines_by_vat_date(lo.isoformat(), hi.isoformat()):
            if row.get("tax_key") != _EU_SVC or row.get("tax_kind") != "base_only" or row.get("tax_base_cents") is None:
                continue
            eid = str(row.get("entry_id"))
            if eid not in entries:
                entries[eid] = self.store.get("journal_entries", eid)
            entry = entries[eid] or {}
            out.append(_Src(row, _line_of(row), str(entry.get("cp_vat_id") or "")))
        return out

    def _compute(
        self, year: int, quarter: int, *, corrected: bool, previous: list[dict[str, Any]] | None = None
    ) -> tuple[list[dict[str, Any]], list[_Src]]:
        """The list rows (dicts) and the eligible source lines. A correction lists every customer of the quarter, also
        those at 0 EUR, and a 0-row for each customer of the corrected report that has no line left."""
        srcs = self._sources(year, quarter)
        by_line = {id(s.line): s for s in srcs}
        try:
            zl = zm_calc.zm_lines(
                (s.line for s in srcs),
                year,
                quarter,
                vat_id_of=lambda ln: by_line[id(ln)].vat_id,
                corrected=corrected,
            )
        except ValueError as e:
            raise ServiceError(422, str(e)) from None
        rows = [
            {
                "vat_id": z.vat_id,
                "country": z.country,
                "amount": z.amount,
                "kind": z.kind,
                "cents": sum(int(s.tax_base_cents or 0) for s in z.source),
            }
            for z in zl
        ]
        if corrected and previous:
            have = {r["vat_id"] for r in rows}
            for old in previous:
                vid = zm_calc.normalize_vat_id(str(old.get("vat_id") or ""))
                if vid and vid not in have:
                    have.add(vid)
                    rows.append({"vat_id": vid, "country": vid[:2], "amount": 0, "kind": "S", "cents": 0})
            rows.sort(key=lambda r: str(r["vat_id"]))
        return rows, srcs

    def _previous(self, row: Record) -> list[dict[str, Any]] | None:
        if not row.get("amends_id"):
            return None
        orig = self.store.get(COLLECTION, str(row["amends_id"]))
        stored = orig.get("lines") if orig else None
        return [x for x in stored if isinstance(x, dict)] if isinstance(stored, list) else None

    def _view(self, row: Record) -> Record:
        fy = self._year(int(row["year"]))
        lo, hi = period_bounds(int(row["year"]), "quarter", int(row["quarter"]))
        return {
            **row,
            "small_business": bool(fy.get("small_business")),
            "from_date": lo.isoformat(),
            "to_date": hi.isoformat(),
        }

    def _live(self, row: Record) -> Record:
        """A draft as the API shows it: the stored row with the list computed now."""
        rows, srcs = self._compute(
            int(row["year"]), int(row["quarter"]), corrected=bool(row.get("amends_id")), previous=self._previous(row)
        )
        return {**self._view(row), "lines": rows, "source_lines": [s.line_id for s in srcs]}

    def _drift(self, row: Record) -> Record:
        """Lines of the quarter that are not among the stored source lines (a late invoice for a service of this
        quarter). Nothing is changed automatically; the correction is a new report with ``amends_id``."""
        known = {str(i) for i in (row.get("source_lines") or [])}
        late = [s for s in self._sources(int(row["year"]), int(row["quarter"])) if s.line_id not in known]
        late.sort(key=lambda s: (str(s.row.get("booking_date")), int(s.row.get("entry_no") or 0)))
        lines = [
            {
                "line_id": s.line_id,
                "entry_id": s.row.get("entry_id"),
                "entry_no": s.row.get("entry_no"),
                "booking_date": s.row.get("booking_date"),
                "document": s.row.get("source_number"),
                "vat_id": zm_calc.normalize_vat_id(s.vat_id),
                "vat_date": s.row.get("vat_date"),
                "base_cents": int(s.line.tax_base_cents or 0),
            }
            for s in late
        ]
        return {"entries": len({str(s.row.get("entry_id")) for s in late}), "lines": lines}

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
        rows = self.store.find(COLLECTION, year=year)
        amended = {str(r["amends_id"]) for r in rows if r.get("amends_id")}
        latest = {int(r["quarter"]): r for r in rows if str(r["id"]) not in amended}
        out: list[dict[str, Any]] = []
        for q in range(1, 5):
            lo, hi = period_bounds(year, "quarter", q)
            due = self._due(year, q)
            row = latest.get(q)
            item: dict[str, Any] = {
                "year": year,
                "quarter": q,
                "from_date": lo.isoformat(),
                "to_date": hi.isoformat(),
                "due_date": due.isoformat(),
                "id": row["id"] if row else None,
            }
            if row is not None and row.get("status") == "final":
                lines = [x for x in (row.get("lines") or []) if isinstance(x, dict)]
                item["rows"] = len(lines)
                item["total"] = sum(int(x.get("amount") or 0) for x in lines)
                item["drift"] = self._drift(row)["entries"]
            else:
                live, _s = self._compute(
                    year,
                    q,
                    corrected=bool(row and row.get("amends_id")),
                    previous=self._previous(row) if row else None,
                )
                item["rows"] = len(live)
                item["total"] = sum(int(x["amount"]) for x in live)
            if row is None and (small or not item["rows"]):
                item["state"] = "not_required"
            else:
                item["state"] = self._state(row, due)
            out.append(ZmListItem(**item).model_dump())
        return out

    def preview(self, year: int, quarter: int) -> Record:
        fy = self._year(year)
        lo, hi = period_bounds(year, "quarter", quarter)
        rows, srcs = self._compute(year, quarter, corrected=False)
        return {
            "id": None,
            "year": year,
            "quarter": quarter,
            "from_date": lo.isoformat(),
            "to_date": hi.isoformat(),
            "due_date": self._due(year, quarter).isoformat(),
            "status": "draft",
            "small_business": bool(fy.get("small_business")),
            "required": not fy.get("small_business") and bool(rows),
            "lines": rows,
            "source_lines": [s.line_id for s in srcs],
        }

    def get(self, rid: str) -> Record:
        row = self._row(rid)
        if row.get("status") != "final":
            return self._live(row)
        return {**self._view(row), "drift": self._drift(row)}

    # ---------- writes
    def create_draft(self, year: int, quarter: int, amends_id: str | None = None) -> Record:
        fy = self._year(year)
        if fy.get("small_business"):
            raise ServiceError(422, "A small business files no ZM")
        with self.store.transaction():
            if amends_id:
                orig = self.store.get(COLLECTION, amends_id)
                if orig is None:
                    raise ServiceError(404, "The report to correct was not found")
                if (int(orig["year"]), int(orig["quarter"])) != (year, quarter):
                    raise ServiceError(409, "A correction must be for the same quarter as the original")
                if orig.get("status") != "final" or not orig.get("filed_on"):
                    raise ServiceError(409, "Only a filed report can be corrected")
                if orig.get("replaced_by") or self.store.find(COLLECTION, amends_id=amends_id):
                    raise ServiceError(409, "The report has been corrected already")
            elif self.store.find(COLLECTION, year=year, quarter=quarter, amends_key=""):
                raise ServiceError(409, f"A ZM for Q{quarter} {year} already exists")
            row = self.store.save(
                COLLECTION,
                {
                    "year": year,
                    "quarter": quarter,
                    "status": "draft",
                    "due_date": self._due(year, quarter).isoformat(),
                    "amends_id": amends_id or None,
                    "amends_key": amends_id or "",
                },
            )
        return self._live(row)

    def finalize(self, rid: str, user: Mapping[str, Any]) -> Record:
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "draft":
                raise ServiceError(409, "The report is already final")
            year, quarter = int(row["year"]), int(row["quarter"])
            fy = self._year(year)
            if fy.get("small_business"):
                raise ServiceError(422, "A small business files no ZM")
            orig: Record | None = None
            if row.get("amends_id"):
                orig = self._row(str(row["amends_id"]))
                if orig.get("replaced_by") or not orig.get("filed_on"):
                    raise ServiceError(409, "The report to correct is not a filed, current report any more")
            rows, srcs = self._compute(
                year, quarter, corrected=bool(row.get("amends_id")), previous=self._previous(row)
            )
            if not rows:
                raise ServiceError(422, "No EU services in this quarter: nothing to report")
            saved = self.store.save(
                COLLECTION,
                {
                    **row,
                    "status": "final",
                    "due_date": self._due(year, quarter).isoformat(),
                    "lines": rows,
                    "source_lines": [s.line_id for s in srcs],
                    "rules_version": self._rules_version(fy, year),
                    "finalised_at": int(self.clock()),
                    "finalised_by": user.get("id"),
                },
            )
            if orig is not None:
                self.store.save(COLLECTION, {**orig, "replaced_by": rid})
            return {**self._view(saved), "drift": self._drift(saved)}

    def reopen(self, rid: str) -> Record:
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "final":
                raise ServiceError(409, "Only a final report can be reopened")
            if row.get("filed_on"):
                raise ServiceError(409, "A filed report cannot be reopened; file a correction")
            saved = self.store.save(
                COLLECTION,
                {
                    **row,
                    "status": "draft",
                    "lines": None,
                    "source_lines": None,
                    "finalised_at": None,
                    "finalised_by": None,
                },
            )
            if row.get("amends_id"):  # the original is current again
                orig = self.store.get(COLLECTION, str(row["amends_id"]))
                if orig is not None and orig.get("replaced_by") == rid:
                    self.store.save(COLLECTION, {**orig, "replaced_by": None})
        return self._live(saved)

    def filed(self, rid: str, filed_on: str, reference: str | None, note: str | None) -> Record:
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "final":
                raise ServiceError(409, "Only a final report can be marked as filed")
            if row.get("filed_on"):
                raise ServiceError(409, "The report is already marked as filed")
            saved = self.store.save(
                COLLECTION, {**row, "filed_on": filed_on, "reference": reference or None, "note": note or None}
            )
            return {**self._view(saved), "drift": self._drift(saved)}

    def delete(self, rid: str) -> None:
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "draft":
                raise ServiceError(409, "Only a draft can be deleted")
            self.store.delete(COLLECTION, rid)

    # ---------- exports
    def export_lines(self, rid: str) -> tuple[Record, "builtins.list[zm_calc.ZmLine]"]:
        """The report as the API shows it and its rows as ``ZmLine`` (the exact cents travel as one synthetic source)."""
        rep = self.get(rid)
        out: builtins.list[zm_calc.ZmLine] = []
        for x in rep.get("lines") or []:
            if not isinstance(x, dict):
                continue
            cents = int(x["cents"]) if x.get("cents") is not None else int(x.get("amount") or 0) * 100
            src = Line(Role(""), Cents(0), TaxKey(_EU_SVC), "base_only", None, Cents(cents), None)
            out.append(zm_calc.ZmLine(str(x["vat_id"]), str(x.get("country") or ""), int(x["amount"]), "S", (src,)))
        return rep, out
