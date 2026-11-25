"""VAT return records (docs/specs/ustva.md "Status, finalise, filed, reopen", "Data model", accounting.md 8.1, 11).

A return is a row of ``vat_returns``. A **draft** is computed live from the journal on every read (nothing but the period
and the snapshot of the year's bases is stored). **Finalising** stores the boxes, the booked figures, the source line ids
and the rules version, and locks the months of the period (reason ``vat_return:<id>``) in the same transaction.
Reopening (final and not filed only) clears the stored figures; the months stay locked. The computation itself is the
pure :mod:`permita38.core.vat.ustva`. An **amended return** (``amends_id``, Kz 10 = 1) amends a filed return of the same
period; finalising it sets ``replaced_by`` of the original. A final return's detail carries **drift** (lines created after
finalising, tax difference). **External returns** (filed outside the app, accounting.md 16 step 5b) are final rows with
the entered Kz 83/39 that stay editable and deletable until replaced. Payments are not part of this module yet. No SQL
here.
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
from permita38.core.service.special_prepayment import stored_cents
from permita38.core.service.tax_bookings import TaxBookingService
from permita38.core.settings import effective_settings
from permita38.core.vat import ustva
from permita38.core.vat.deadlines import PeriodKind, period_bounds, ustva_due
from permita38.core.vat_rules import VatRules, VatRulesMissing, vat_rules_for

COLLECTION = "vat_returns"
MAX_SOURCE_LINES = 20_000
EXTERNAL_MAX_CENTS = 10**13  # bound of the entered Kz 83 (absolute) and Kz 39
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
        bookings: TaxBookingService | None = None,
    ):
        self.store = store
        self.locks = locks
        self.bookings = bookings or TaxBookingService(store)
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
        self, fy: Record, kind: str, no: int, credit_cents: int = 0
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
                special_prepayment_credit_cents=credit_cents,
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
        credit = self._credit(row, fy)
        result, _rules, used, _bounds = self._compute(fy, str(row["period_kind"]), int(row["period_no"]), credit)
        return {
            **row,
            **self._snapshot(fy),
            "boxes": self._boxes(result, bool(row.get("amends_id"))),
            **self._sources_json([s.line_id for s in used]),
            "result_cents": result.result_cents,
            "special_prepayment_credit_cents": credit,
            "rounding_difference_cents": result.rounding_difference,
        }

    @staticmethod
    def _special_credit(fy: Record, kind: str, no: int) -> int:
        """Kz 39: the year's stored special prepayment, only in the December return of a monthly filer with
        Dauerfrist (quarterly filers pay none; never for a small business)."""
        last = kind == "month" and no == 12
        if not last or not fy.get("dauerfrist") or fy.get("small_business") or fy.get("vat_frequency") != "month":
            return 0
        return stored_cents(fy)

    def _credit(self, row: Record, fy: Record) -> int:
        """The Kz 39 credit: the stored special prepayment for the last period of the year; an amended return keeps
        the one of the return it amends (already posted, never posted again)."""
        if row.get("amends_id"):
            orig = self.store.get(COLLECTION, str(row["amends_id"]))
            return int(orig.get("special_prepayment_credit_cents") or 0) if orig else 0
        return self._special_credit(fy, str(row["period_kind"]), int(row["period_no"]))

    def _credit_slot(self, rid: str) -> tuple[int, bool]:
        """The revision ``n`` of the Kz 39 credit entry (``vat:<id>:credit:r<n>``) of this return and whether that entry
        is live. A reopen reverses the entry; finalising again needs a fresh key, so the next revision is used."""
        n = 1
        while True:
            key = f"vat:{rid}:credit:r{n}"
            if self.store.get_journal_entry_by_event_key(key) is None:
                return n, False
            if self.store.get_journal_entry_by_event_key(key + ":rev") is None:
                return n, True
            n += 1

    @staticmethod
    def _boxes(result: ustva.UstvaResult, amended: bool) -> dict[str, dict[str, Any]]:
        boxes: dict[str, dict[str, Any]] = {kz: _box_json(f) for kz, f in result.boxes.items()}
        if amended:
            boxes["10"] = {"flag": 1}  # Kz 10: amended return, a marker (the integer 1), no amount
        return boxes

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
        rows = self.store.find(COLLECTION, year=year)
        amended = {str(r["amends_id"]) for r in rows if r.get("amends_id")}
        existing = {  # the latest return of each period: the one no other return amends
            (r["period_kind"], int(r["period_no"])): r for r in rows if str(r["id"]) not in amended
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
        credit = self._special_credit(fy, kind, no)
        result, _rules, used, (lo, hi) = self._compute(fy, kind, no, credit)
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
            "special_prepayment_credit_cents": credit,
            "rounding_difference_cents": result.rounding_difference,
        }

    def get(self, rid: str) -> Record:
        row = self._row(rid)
        if row.get("status") != "final":
            return self._live(row)
        return {**self._view(row), "drift": self._drift(row), **self._payments(row)}

    # ---------- payments (accounting.md 9.6: manual entries ``vat:<uuid>``, several per return)
    def _payments(self, row: Record) -> Record:
        """The payments and refunds of a final return, read from the journal by source (reversed ones are listed,
        flagged, and do not count), with the net paid amount and what is still open of Kz 83."""
        out: list[Record] = []
        paid = 0
        for e in self.store.find("journal_entries", source_coll=COLLECTION, source_id=str(row["id"])):
            if e.get("event") not in ("vat_payment", "vat_refund"):
                continue
            full = self.store.get_journal_entry_by_event_key(str(e["event_key"])) or e
            cents = sum(int(ln["amount_cents"]) for ln in full.get("lines", []) if ln.get("role") == "vat_prepayments")
            reversed_ = self.store.get_journal_entry_by_event_key(f"{e['event_key']}:rev") is not None
            if not reversed_:
                paid += cents
            out.append(
                {
                    "entry_id": e["id"],
                    "event_key": e["event_key"],
                    "date": str(e.get("document_date")),
                    "kind": "refund" if cents < 0 else "payment",
                    "amount": money(abs(cents)),
                    "reversed": reversed_,
                }
            )
        out.sort(key=lambda p: (p["date"], p["event_key"]))
        result = int(row.get("result_cents") or 0)
        open_cents = result - paid
        if not out and result == 0:
            state = "none"
        elif open_cents == 0:
            state = "paid"
        elif result != 0 and (open_cents > 0) == (result > 0):
            state = "open"
        else:
            state = "overpaid"
        return {"payments": out, "paid": money(paid), "open": money(open_cents), "payment_state": state}

    def pay(self, rid: str, day: date, amount_cents: int, kind: str, user: Mapping[str, Any]) -> Record:
        """Record a payment to / refund from the tax office for a final return."""
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "final":
                raise ServiceError(409, "Only a final return can be paid")
            self.bookings.vat_payment(rid, day, amount_cents, kind, user)  # type: ignore[arg-type]
        return self.get(rid)

    def _drift(self, row: Record) -> Record:
        """Journal changes a final return does not contain (ustva.md "Drift"). A normal return: the lines of the period
        that are not among the stored source lines (by ``created_at`` only if the list was truncated). An external return: the journal is compared with the entered
        figures, so every period line is listed when the result differs. ``tax`` is the Kz 83 the journal gives now minus
        the stored Kz 83, a decimal string. Nothing is moved automatically."""
        fy = self._year(int(row["year"]))
        result, _rules, used, _bounds = self._compute(fy, str(row["period_kind"]), int(row["period_no"]))
        credit = int(row.get("special_prepayment_credit_cents") or 0)
        diff = int(result.result_cents) - credit - int(row.get("result_cents") or 0)
        stored = row.get("source_lines")
        if row.get("external"):
            late = used if diff != 0 else []
        elif isinstance(stored, dict):  # truncated source list: only then the creation time decides
            cutoff = int(row.get("finalised_at") or 0)
            late = [s for s in used if int(s.entry.get("created_at") or 0) > cutoff]
        else:
            known = set(stored or [])
            late = [s for s in used if s.line_id not in known]
        late = sorted(late, key=lambda s: (str(s.entry.get("booking_date")), int(s.entry.get("entry_no") or 0)))
        lines = [
            {
                "line_id": s.line_id,
                "entry_id": s.entry.get("id"),
                "entry_no": s.entry.get("entry_no"),
                "booking_date": s.entry.get("booking_date"),
                "document": s.entry.get("source_number"),
                "tax_key": s.line.tax_key,
                "tax_kind": s.line.tax_kind,
                "vat_date": _iso(s.vat_date) if s.vat_date else None,
                "base": money(s.line.tax_base_cents) if s.line.tax_base_cents is not None else None,
                "tax": money(-s.line.amount_cents if s.line.tax_kind == "tax" else s.line.amount_cents)
                if s.line.tax_kind != "base_only"
                else None,
            }
            for s in late
        ]
        return {
            "entries": len({str(s.entry.get("id")) for s in late}),
            "tax_cents": diff,
            "tax": money(diff),
            "lines": lines,
        }

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
        cutoff: int | None = None
        if final and isinstance(row.get("source_lines"), dict) and row.get("finalised_at") is not None:
            cutoff = int(row["finalised_at"])  # truncated: entries created later are drift, not source
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
            if cutoff is not None and int(src.entry.get("created_at") or 0) > cutoff:
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
        fy = self._year(year)
        self._check_period(fy, kind, no)
        with self.store.transaction():
            if amends_id:
                self._check_amendable(amends_id, year, kind, no)
            elif self.store.find(COLLECTION, year=year, period_kind=kind, period_no=no, amends_key=""):
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
                    "amends_id": amends_id or None,
                    "amends_key": amends_id or "",
                    "external": False,
                    **self._snapshot(fy),
                },
            )
        return self._live(row)

    def _check_amendable(self, amends_id: str, year: int, kind: str, no: int) -> Record:
        orig = self.store.get(COLLECTION, amends_id)
        if orig is None:
            raise ServiceError(404, "The return to amend was not found")
        if (int(orig["year"]), orig["period_kind"], int(orig["period_no"])) != (year, kind, no):
            raise ServiceError(409, "An amended return must be for the same period as the original")
        if orig.get("status") != "final" or not orig.get("filed_on"):
            raise ServiceError(409, "Only a filed return can be amended")
        if orig.get("replaced_by") or self.store.find(COLLECTION, amends_id=amends_id):
            raise ServiceError(409, "The return has been amended already")
        return orig

    def finalize(self, rid: str, user: Mapping[str, Any], confirm_gap: bool = False) -> Record:
        """Freeze the figures and lock the months up to the end of the period, in one transaction. Unlocked earlier
        months (a gap) are listed in a 409 (``months``) unless ``confirm_gap``."""
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "draft":
                raise ServiceError(409, "The return is already final")
            fy = self._year(int(row["year"]))
            kind, no = str(row["period_kind"]), int(row["period_no"])
            credit = self._credit(row, fy)
            result, rules, used, (lo, hi) = self._compute(fy, kind, no, credit)
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
            orig: Record | None = None
            if row.get("amends_id"):
                orig = self._row(str(row["amends_id"]))
                if orig.get("replaced_by") or not orig.get("filed_on"):
                    raise ServiceError(409, "The return to amend is not a filed, current return any more")
            saved = self.store.save(
                COLLECTION,
                {
                    **row,
                    **self._snapshot(fy),
                    "rules_version": self._rules_version(fy, rules),
                    "due_date": _iso(self._due(fy, kind, no)),
                    "status": "final",
                    "boxes": self._boxes(result, bool(row.get("amends_id"))),
                    "source_lines": self._stored_sources(ids),
                    "result_cents": int(result.result_cents),
                    "special_prepayment_credit_cents": credit,
                    "rounding_difference_cents": int(result.rounding_difference),
                    "finalised_at": now,
                    "finalised_by": user.get("id"),
                },
            )
            if credit and orig is None:  # an amended return keeps the credit of the original, booked already
                self.bookings.special_prepayment_credit(
                    int(row["year"]), rid, credit, user, revision=self._credit_slot(rid)[0]
                )
            self.locks.lock_up_to(hi.year, hi.month, f"vat_return:{rid}", str(user.get("id")))
            if orig is not None:
                self.store.save(COLLECTION, {**orig, "replaced_by": rid})
            return self._view(saved)

    def reopen(self, rid: str, user: Mapping[str, Any] | None = None) -> Record:
        with self.store.transaction():
            row = self._row(rid)
            if row.get("status") != "final":
                raise ServiceError(409, "Only a final return can be reopened")
            if row.get("filed_on"):
                raise ServiceError(409, "A filed return cannot be reopened; file an amended return")
            slot, live = self._credit_slot(rid)
            if live and not row.get("amends_id"):
                self.bookings.special_prepayment_credit_reverse(int(row["year"]), rid, user or {}, revision=slot)
            saved = self.store.save(
                COLLECTION,
                {
                    **row,
                    "status": "draft",
                    "boxes": {},
                    "source_lines": None,
                    "result_cents": None,
                    "special_prepayment_credit_cents": None,
                    "rounding_difference_cents": None,
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
            if row.get("status") != "draft" and not self._editable_external(row):
                raise ServiceError(409, "Only a draft or an external return that is not replaced can be deleted")
            self.store.delete(COLLECTION, rid)

    # ---------- external returns (filed outside the app, accounting.md 16 step 5b)
    def _editable_external(self, row: Record) -> bool:
        """Editable and deletable until an amended return (draft or final) points at it (accounting 8.1)."""
        return (
            bool(row.get("external"))
            and row.get("status") == "final"
            and not row.get("replaced_by")
            and not self.store.find(COLLECTION, amends_id=str(row["id"]))
        )

    @staticmethod
    def _external_boxes(kz83_cents: int, kz39_cents: int) -> dict[str, dict[str, str]]:
        boxes = {"83": {"amount": money(kz83_cents)}}
        if kz39_cents:
            boxes["39"] = {"amount": money(kz39_cents)}
        return boxes

    @staticmethod
    def _check_external_amounts(kz83_cents: int, kz39_cents: int) -> None:
        if abs(kz83_cents) > EXTERNAL_MAX_CENTS:
            raise ServiceError(422, "Kz 83 is out of range")
        if not 0 <= kz39_cents <= EXTERNAL_MAX_CENTS:
            raise ServiceError(422, "Kz 39 must be between 0 and 10^13 cents")

    def create_external(
        self, year: int, kind: str, no: int, kz83_cents: int, kz39_cents: int, filed_on: str, user: Mapping[str, Any]
    ) -> Record:
        """Record a return filed outside the app: final and filed, ``external``, the entered Kz 83 (and Kz 39). The
        months up to the period are locked (reason ``vat_return:<id>``); differences to the journal show as drift."""
        fy = self._year(year)
        self._check_period(fy, kind, no)
        self._check_external_amounts(kz83_cents, kz39_cents)
        try:
            date.fromisoformat(filed_on)
        except ValueError:
            raise ServiceError(422, "filed_on must be a date (YYYY-MM-DD)") from None
        lo, hi = period_bounds(year, kind, no)  # type: ignore[arg-type]
        rules = self._rules(year)
        with self.store.transaction():
            if self.store.find(COLLECTION, year=year, period_kind=kind, period_no=no, amends_key=""):
                raise ServiceError(409, f"A return for {kind} {no} of {year} already exists")
            row = self.store.save(
                COLLECTION,
                {
                    "year": year,
                    "period_kind": kind,
                    "period_no": no,
                    "from_date": _iso(lo),
                    "to_date": _iso(hi),
                    "due_date": _iso(self._due(fy, kind, no)),
                    "status": "final",
                    "amends_key": "",
                    "external": True,
                    **self._snapshot(fy),
                    "rules_version": self._rules_version(fy, rules),
                    "boxes": self._external_boxes(kz83_cents, kz39_cents),
                    "source_lines": [],
                    "result_cents": int(kz83_cents),
                    "special_prepayment_credit_cents": int(kz39_cents),
                    "rounding_difference_cents": 0,
                    "filed_on": filed_on,
                    "finalised_at": int(self.clock()),
                    "finalised_by": user.get("id"),
                },
            )
            self.locks.lock_up_to(hi.year, hi.month, f"vat_return:{row['id']}", str(user.get("id")))
            return self.get(str(row["id"]))

    def update_external(self, rid: str, kz83_cents: int, kz39_cents: int) -> Record:
        """Correct the entered figures of an external return until an amended return replaces it. ``filed_on`` is
        set once (database guard); to change it, delete and enter the return again."""
        self._check_external_amounts(kz83_cents, kz39_cents)
        with self.store.transaction():
            row = self._row(rid)
            if not self._editable_external(row):
                raise ServiceError(409, "Only an external return that is not replaced can be edited")
            self.store.save(
                COLLECTION,
                {
                    **row,
                    "boxes": self._external_boxes(kz83_cents, kz39_cents),
                    "result_cents": int(kz83_cents),
                    "special_prepayment_credit_cents": int(kz39_cents),
                },
            )
            return self.get(rid)
