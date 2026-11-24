"""Adapter between an expense and the journal (docs/specs/accounting.md 7, 9.4, 10, 11, 16; credit-notes-payments.md 5).

:class:`ExpensePostingService` is the hook of ``RecordService`` for the ``expenses`` collection:

* :meth:`~ExpensePostingService.prepare` runs before the save: resolves the tax treatment (explicit, else mapped from
  the old ``reverse_charge`` field and the supplier) and computes the § 13b tax of a reverse-charge expense on the
  server (a client-sent ``vat`` is ignored with a warning);
* :meth:`~ExpensePostingService.sync` runs after the save: posts the bill entry ``expense:<id>:bill:r<n>`` and the
  payment entry ``expense:<id>:pay:r<n>`` through :class:`~permita38.core.service.journal.JournalService` with the pure
  engine ``core/accounting/postings_expense.py``; a change reverses the live entry (key + ``:rev``) and posts revision
  ``n + 1``; the revisions are stored on the record (``bill_rev`` / ``pay_rev`` = the highest revision posted);
* :meth:`~ExpensePostingService.unpost` runs before a delete: the reversals only.

Nothing posts before the accounting is initialised (settings key ``accounting.start``) or for an expense dated before
the start. A reversal of a bill entry whose booking date lies in a locked month is refused with 409; a payment entry
carries no VAT line, so its reversal is allowed and booked in the first open month (spec 9.4). No SQL here.
"""

import dataclasses
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any, Literal, cast

from permita38.core import calc
from permita38.core.accounting.postings_expense import (
    Expense,
    ExpenseLine,
    ExpenseTreatment,
    expense_bill_entry,
    expense_pay_entry,
)
from permita38.core.accounting.types import Bases, Cents, Entry, IncomeBasis, Line, Role, TaxKey, VatBasis
from permita38.core.charts import ChartMissing, ChartName, chart_for
from permita38.core.errors import ServiceError
from permita38.core.money import dec, q2, to_float
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service.journal import JournalService, JournalSource, PostLine
from permita38.core.service.locks import first_unlocked_from, vat_date_for
from permita38.core.service.planning_warnings import warning
from permita38.core.service.postings import post_line
from permita38.core.settings import accounting_start

__all__ = ["ExpensePostingService", "WARNINGS_KEY"]

WARNINGS_KEY = "accounting_warnings"  # response-only list on a saved expense (never stored)
RC_TREATMENTS = ("reverse_charge_eu_service", "reverse_charge_third_country")
DEFAULT_RC_RATE = Decimal(19)
LOCKED = "The period is locked; book a correction"
Part = Literal["bill", "pay"]
PARTS: tuple[Part, Part] = ("bill", "pay")


def _cents(value: Any) -> Cents:
    return Cents(int(q2(dec(value)) * 100))


def _day(value: Any) -> date | None:
    return date.fromisoformat(str(value)[:10]) if value else None


def _rev(rec: Mapping[str, Any], part: Part) -> int:
    return int(rec.get(f"{part}_rev") or 0)


def _tax(net: Decimal, rate: Decimal) -> Decimal:
    return q2(net * rate / Decimal(100))


class ExpensePostingService:
    def __init__(self, store: Repository, journal: JournalService | None = None):
        self.store = store
        self.journal = journal or JournalService(store)

    # ---------- treatment and server-side § 13b tax (before the save)
    def resolve_treatment(self, rec: Mapping[str, Any]) -> ExpenseTreatment:
        """Spec 16 step 6 / 10: an explicit ``tax_treatment``; else ``reverse_charge`` with the supplier's ``vat_mode``
        (``third_country_reverse_charge`` -> third country, anything else EU); else a small-business supplier gives
        ``no_input_vat``; else ``standard``."""
        explicit = rec.get("tax_treatment")
        if explicit:
            return cast(ExpenseTreatment, explicit)
        mode = self._supplier_mode(rec)
        if rec.get("reverse_charge"):
            return (
                "reverse_charge_third_country"
                if mode == "third_country_reverse_charge"
                else "reverse_charge_eu_service"
            )
        return "no_input_vat" if mode == "small_business" else "standard"

    def _supplier(self, rec: Mapping[str, Any]) -> Record | None:
        sid = rec.get("supplier_id")
        return self.store.get("suppliers", str(sid)) if sid else None

    def _supplier_mode(self, rec: Mapping[str, Any]) -> str | None:
        supplier = self._supplier(rec)
        return str(supplier["vat_mode"]) if supplier and supplier.get("vat_mode") else None

    def prepare(self, data: dict[str, Any], old: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        """Normalise the treatment fields of an expense about to be saved and return response warnings."""
        for key in ("bill_rev", "pay_rev"):  # server-owned: kept from the stored row, never from the client
            data.pop(key, None)
            if old is not None and old.get(key) is not None:
                data[key] = old[key]
        treatment = data.get("tax_treatment")
        if not treatment and data.get("reverse_charge"):  # an old client: map the flag (spec 16 step 10)
            treatment = self.resolve_treatment({**data, "tax_treatment": None})
            data["tax_treatment"] = treatment
        if treatment in RC_TREATMENTS:
            data["reverse_charge"] = True
        else:
            data.pop("reverse_charge", None)  # derived output: true only for the two reverse-charge treatments
        if treatment not in RC_TREATMENTS:
            return []
        return self._self_assess(data)

    @staticmethod
    def _self_assess(data: dict[str, Any]) -> list[dict[str, Any]]:
        """The stored ``vat`` of a reverse-charge bill is the self-assessed tax: ``q2(net x rate / 100)`` per rate
        group. The printed amount of an itemised line is its net (the supplier charged no VAT)."""
        raw_lines = data.get("lines")
        if raw_lines:
            nets: dict[Decimal, Decimal] = {}
            lines = []
            for ln in raw_lines:
                net = dec(ln.get("gross"))
                rate = dec(ln["vat_rate"]) if ln.get("vat_rate") is not None else DEFAULT_RC_RATE
                nets[rate] = nets.get(rate, Decimal(0)) + net
                lines.append({**ln, "net": to_float(net), "vat": 0.0})
            data["lines"] = lines
            data["net"] = to_float(sum(nets.values(), Decimal(0)))
            data["vat"] = to_float(sum((_tax(n, r) for r, n in nets.items()), Decimal(0)))
            return []
        rate = dec(data["vat_rate"]) if data.get("vat_rate") is not None else DEFAULT_RC_RATE
        computed = _tax(dec(data.get("net")), rate)
        sent = dec(data.get("vat"))
        data["vat"] = to_float(computed)
        if sent != 0 and sent != computed:
            return [
                warning(
                    "expense.rc_vat_ignored",
                    "warning",
                    "vat",
                    "The VAT of a reverse-charge bill is computed by the server from the net amount and the rate; "
                    "the sent value was ignored.",
                )
            ]
        return []

    # ---------- engine input
    def _fiscal_year(self, year: int) -> Record | None:
        rows = self.store.find("fiscal_years", year=year)
        return rows[0] if rows else None

    def _closed(self, day: date) -> bool:
        fy = self._fiscal_year(day.year)
        return fy is not None and fy.get("closed_at") is not None

    def _engine(self, rec: Mapping[str, Any], fy: Record, rev: tuple[int, int]) -> Expense:
        day = cast(date, _day(rec["date"]))
        treatment = self.resolve_treatment(rec)
        rc = treatment in RC_TREATMENTS
        try:
            chart = chart_for(cast(ChartName, fy["chart"]), day.year)
        except ChartMissing as exc:
            raise ServiceError(409, f"No chart of accounts for fiscal year {day.year}: {exc}") from None
        raw = [ln for ln in (rec.get("lines") or []) if isinstance(ln, dict)]
        views: list[Any] = calc.expense_lines(dict(rec))
        lines: list[ExpenseLine] = []
        for i, view in enumerate(views):
            net, vat = Decimal(view["net"]), Decimal(view["vat"])
            given = raw[i].get("vat_rate") if raw else rec.get("vat_rate")
            category = str(view.get("category") or "")
            lines.append(
                ExpenseLine(
                    net=_cents(net),
                    vat=_cents(vat),
                    rate=_rate(net, vat, given, rc),
                    pct=Decimal(view["pct"]),
                    vat_pct=Decimal(view["vat_pct"]),
                    category=category,
                    role=Role(str(chart.role_for_category(category))),
                )
            )
        return Expense(
            id=str(rec["id"]),
            date=day,
            treatment=treatment,
            lines=tuple(lines),
            paid_on=_day(rec.get("paid_on")),
            service_date=_day(rec.get("service_date")),
            small_business=bool(fy.get("small_business")),
            bill_rev=rev[0],
            pay_rev=rev[1],
        )

    def _engine_first(self, day: date, first_open: date | None, stored: Mapping[str, Any] | None) -> date:
        """The ``first_open_day`` handed to the pure engine for a document dated ``day``, so that its booking date
        and booking-date-dependent fields (``vat_paid_private`` income date, input-VAT ``vat_date``) follow the
        journal's rule incl. the closing-date exception (spec 7.8). With a ``stored`` entry: the state it was posted
        in (for change detection, so a lock set afterwards does not look like an edit)."""
        if stored is not None:
            return date.fromisoformat(str(stored["booking_date"]))
        # TODO: make JournalService.booking_date public; called through the instance for now
        booking = self.journal._booking_date(day)
        return booking if booking > day else (first_open or date.min)

    def _entries(
        self,
        rec: Mapping[str, Any],
        rev: tuple[int, int],
        stored: Mapping[Part, Record | None] | None = None,
        year_closed: bool | None = None,
    ) -> dict[Part, Entry | None]:
        """The bill and payment entries the engine gives for the expense now; ``None`` entries post nothing. Empty
        before the accounting start (and nothing when the fiscal year does not exist yet and cannot be opened).
        ``stored`` / ``year_closed`` are for change detection only (see :meth:`_engine_first`)."""
        start = accounting_start(self.store.settings())
        day = _day(rec.get("date"))
        if start is None or day is None or day < start:
            return {"bill": None, "pay": None}
        fy = self.journal.ensure_year(day.year, "system")
        bases = Bases(cast(IncomeBasis, fy["income_basis"]), cast(VatBasis, fy["vat_basis"]))
        first_open = first_unlocked_from(self.store.list("period_locks"))
        engine = self._engine(rec, fy, rev)
        stored = stored or {}
        paid = engine.paid_on or (engine.date if bases.income == "eur" else None)
        bill_first = self._engine_first(engine.date, first_open, stored.get("bill"))
        bill_closed = self._closed(engine.date) if year_closed is None else year_closed
        bill = expense_bill_entry(engine, bases, bill_first, bill_closed)
        pay = None
        pay_first = bill_first
        if paid is not None:
            pay_first = self._engine_first(paid, first_open, stored.get("pay"))
            pay_closed = self._closed(paid) if year_closed is None else year_closed
            pay = expense_pay_entry(engine, bases, pay_first, pay_closed)
        return {"bill": self._dated(bill, bill_first), "pay": self._dated(pay, pay_first)}

    @staticmethod
    def _dated(entry: Entry | None, first_open: date | None) -> Entry | None:
        """Section 11: input VAT never drifts (``input_vat``), the § 13b tax date may (``rc_tax_date``)."""
        if entry is None or first_open is None:
            return entry
        lines: list[Line] = []
        for ln in entry.lines:
            if ln.vat_date is not None and ln.tax_key is not None:
                kind = "input_vat" if ln.tax_key == TaxKey("VST") else "rc_tax_date"
                ln = dataclasses.replace(ln, vat_date=vat_date_for(kind, ln.vat_date, first_open)[0])
            lines.append(ln)
        return dataclasses.replace(entry, lines=tuple(lines))

    # ---------- journal access
    def _key(self, expense_id: str, part: Part, rev: int) -> str:
        return f"expense:{expense_id}:{part}:r{rev}"

    def _live(self, expense_id: str, part: Part, rev: int) -> Record | None:
        """The stored entry of revision ``rev`` unless it was reversed already."""
        if rev <= 0:
            return None
        key = self._key(expense_id, part, rev)
        entry = self.store.get_journal_entry_by_event_key(key)
        if entry is None or self.store.get_journal_entry_by_event_key(key + ":rev") is not None:
            return None
        return entry

    def _source(
        self, rec: Mapping[str, Any], part: Part, user: Mapping[str, Any] | None, reversal: bool
    ) -> JournalSource:
        supplier = self._supplier(rec) or {}
        return JournalSource(
            event=f"expense_{part}_reversal" if reversal else f"expense_{part}",
            created_by=str((user or {}).get("id") or "system"),
            source_coll="expenses",
            source_id=str(rec["id"]),
            source_number=rec.get("invoice_no"),
            counterparty_id=supplier.get("id"),
            cp_vat_id=supplier.get("vat_id"),
            cp_country=supplier.get("country"),
        )

    @staticmethod
    def _text(rec: Mapping[str, Any], part: Part, reversal: bool = False) -> str:
        name = str(rec.get("vendor") or rec.get("description") or rec["id"])
        label = {"bill": "Expense", "pay": "Payment of expense"}[part]
        return f"{'Reversal: ' if reversal else ''}{label} {name}"[:200]

    def _reverse(self, rec: Mapping[str, Any], part: Part, stored: Record, user: Mapping[str, Any] | None) -> None:
        """Post the reversal of a stored entry: every amount flipped, the tax fields and dates kept."""
        if part == "bill":
            try:
                self.journal.check_reversible(stored)
            except ServiceError:
                raise ServiceError(409, LOCKED) from None
        first_open = first_unlocked_from(self.store.list("period_locks"))
        document = cast(date, _day(stored["document_date"]))
        closed = self._closed(document)
        lines: list[PostLine] = []
        for ln in stored["lines"]:
            income = _day(ln.get("income_date"))
            if income is not None and closed:  # a closed year: the income date becomes the booking date
                income = max(document, first_open or document)
            base = ln.get("tax_base_cents")
            lines.append(
                PostLine(
                    amount_cents=-int(ln["amount_cents"]),
                    role=ln.get("role"),
                    account=None if ln.get("role") else ln.get("account"),
                    tax_key=ln.get("tax_key"),
                    tax_kind=ln.get("tax_kind"),
                    tax_rate=ln.get("tax_rate"),
                    tax_base_cents=None if base is None else -int(base),
                    vat_date=_day(ln.get("vat_date")),
                    income_date=income,
                    open_item_id=ln.get("open_item_id"),
                    treatment=ln.get("treatment"),
                )
            )
        source = dataclasses.replace(self._source(rec, part, user, True), reverses_id=str(stored["id"]))
        self.journal.post(str(stored["event_key"]) + ":rev", document, self._text(rec, part, True), source, lines)

    # ---------- the hooks
    def sync(
        self, saved: dict[str, Any], old: Mapping[str, Any] | None, user: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Bring the journal in line with a saved expense (inside the caller's transaction); returns the record
        with its revisions. 409 when a bill entry to be reversed lies in a locked month."""
        revs: dict[Part, int] = {"bill": _rev(saved, "bill"), "pay": _rev(saved, "pay")}
        if accounting_start(self.store.settings()) is None and not any(revs.values()):
            return saved
        eid = str(saved["id"])
        new = self._entries(saved, (revs["bill"] + 1, revs["pay"] + 1))
        lives: dict[Part, Record | None] = {part: self._live(eid, part, revs[part]) for part in PARTS}
        compare = self._compare_entries(saved, lives)
        for part in PARTS:
            live = lives[part]
            now = new[part]
            if live is not None and now is not None and self._unchanged(saved, part, live, compare[part], user):
                continue  # nothing this event posts has changed (e.g. only a description was edited)
            if live is None and now is None:
                continue
            if live is not None:
                self._reverse(saved, part, live, user)
            if now is not None:
                revs[part] += 1
                self.journal.post(
                    self._key(eid, part, revs[part]),
                    now.document_date,
                    self._text(saved, part),
                    self._source(saved, part, user, False),
                    [post_line(ln) for ln in now.lines],
                )
            elif live is not None:
                revs[part] += 1  # reversed and nothing new: the revision still moves on
        if (revs["bill"], revs["pay"]) == (_rev(saved, "bill"), _rev(saved, "pay")):
            return saved
        out = {**saved}
        for part in PARTS:
            if revs[part]:
                out[f"{part}_rev"] = revs[part]
        return dict(self.store.save("expenses", out))

    def _compare_entries(
        self, rec: Mapping[str, Any], lives: Mapping[Part, Record | None]
    ) -> dict[Part, list[Entry | None]]:
        """The would-be entries in the state the stored ones were posted in (both closed-year readings: whether the
        year was closed at posting time is not stored)."""
        if not any(lives.values()):
            return {"bill": [], "pay": []}
        out: dict[Part, list[Entry | None]] = {"bill": [], "pay": []}
        for closed in (False, True):
            got = self._entries(rec, (1, 1), lives, closed)
            for part in PARTS:
                out[part].append(got[part])
        return out

    def _unchanged(
        self, rec: Mapping[str, Any], part: Part, live: Record, candidates: list[Entry | None], user: Any
    ) -> bool:
        """Spec 9.4: the would-be entry equals the stored one in lines, document date, source number and counterparty
        fields (the free text alone never reposts)."""
        source = self._source(rec, part, user, False)
        for cand in candidates:
            if cand is None or cand.document_date.isoformat() != str(live["document_date"])[:10]:
                continue
            if (
                (source.source_number or None) != (live.get("source_number") or None)
                or (source.counterparty_id or None) != (live.get("counterparty_id") or None)
                or (source.cp_vat_id or None) != (live.get("cp_vat_id") or None)
                or (source.cp_country or None) != (live.get("cp_country") or None)
            ):
                return False
            mine = [_line_key(dataclasses.asdict(post_line(ln))) for ln in cand.lines]
            if mine == [_line_key(ln) for ln in live["lines"]]:
                return True
        return False

    def unpost(self, expense: Mapping[str, Any], user: Mapping[str, Any] | None = None) -> None:
        """Before a delete: the reversals of the live entries only (409 for a bill entry in a locked month)."""
        lives = {part: self._live(str(expense["id"]), part, _rev(expense, part)) for part in PARTS}
        for part in PARTS:  # check first, so a refusal posts nothing at all
            stored = lives[part]
            if part == "bill" and stored is not None:
                try:
                    self.journal.check_reversible(stored)
                except ServiceError:
                    raise ServiceError(409, LOCKED) from None
        for part in PARTS:
            stored = lives[part]
            if stored is not None:
                self._reverse(expense, part, stored, user)


def _line_key(ln: Mapping[str, Any]) -> tuple[Any, ...]:
    def iso(v: Any) -> str | None:
        return str(v)[:10] if v else None

    return (
        ln.get("role"),
        int(ln["amount_cents"]),
        ln.get("tax_key") or None,
        ln.get("tax_kind") or None,
        ln.get("tax_rate") or None,
        ln.get("tax_base_cents"),
        iso(ln.get("vat_date")),
        iso(ln.get("income_date")),
        ln.get("open_item_id") or None,
    )


def _rate(net: Decimal, vat: Decimal, given: Any, rc: bool) -> Decimal:
    """The VAT rate of a line: the stated one; else 19 for a reverse-charge bill (no VAT printed), else derived from
    the amounts (19 / 7 when within half a point)."""
    if given is not None:
        return dec(given).normalize()
    if rc:
        return DEFAULT_RC_RATE
    if net <= 0 or vat == 0:
        return Decimal(0)
    rate = q2(vat * 100 / net)
    for standard in (Decimal(19), Decimal(7)):
        if abs(rate - standard) <= Decimal("0.5"):
            return standard
    return rate.normalize()
