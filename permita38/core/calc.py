"""Business logic: rates, document totals, capacity, forecast, finance.

Money is computed with :mod:`permita38.core.money` (exact decimals, half-up cents). Stored records can come from
older versions or from another device through file sync, so every function here treats damaged values (bad dates,
non-numbers) defensively: the record is skipped or the value counts as 0, a warning is logged, and an aggregate view
never fails because of one bad record.
"""

import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from . import tax
from .holidays import holidays as de_holidays
from .money import ZERO, dec, line_amount, q2, split_gross, to_float, vat_amount
from .service.personal_tax import NOT_SAVED_WARNING, PersonalTaxService
from .tax_rules import rules_for
from .values import d, fnum  # noqa: F401  (re-exported: callers use calc.d and calc.fnum)

log = logging.getLogger(__name__)

WORKDAYS_DEFAULT = [0, 1, 2, 3, 4]


def iso(x):
    return x.isoformat()


def monday(x):
    return x - timedelta(days=x.weekday())


def daterange(a, b):
    while a <= b:
        yield a
        a += timedelta(days=1)


# -- rates -----------------------------------------------------------------
def rate_for(entry, ctx):
    """task > project > customer > user default > global default."""
    if entry.get("rate") not in (None, ""):
        return fnum(entry["rate"])
    task = ctx["tasks"].get(entry.get("task_id"))
    proj = ctx["projects"].get(entry.get("project_id"))
    cust = ctx["customers"].get((proj or {}).get("customer_id"))
    user = ctx["users"].get(entry.get("user_id"))
    for src in (task, proj, cust, user):
        if src and src.get("rate") not in (None, ""):
            return fnum(src["rate"])
        if src is user and src and src.get("default_rate") not in (None, ""):
            return fnum(src["default_rate"])
    return fnum(ctx["settings"].get("default_rate"))


def context(store):
    return dict(
        settings=store.settings(),
        users={u["id"]: u for u in store.list("users")},
        customers={c["id"]: c for c in store.list("customers")},
        projects={p["id"]: p for p in store.list("projects")},
        tasks={t["id"]: t for t in store.list("tasks")},
    )


# -- offers / invoices -------------------------------------------------------
def doc_totals(doc, settings):
    """Net, VAT per rate and gross of an offer/invoice, in exact cents (rule: see :mod:`permita38.core.money`).

    Also fills ``amount`` on every line. Returned amounts are floats with at most 2 decimals; ``vat`` and
    ``by_rate`` are keyed by the VAT rate.
    """
    klein = bool(settings.get("kleinunternehmer"))
    if klein:
        default_vat = ZERO
    else:
        raw = doc.get("vat_rate")
        default_vat = dec(raw if raw not in (None, "") else settings.get("vat_rate", 19))
    net = ZERO
    by_rate: dict[Decimal, Decimal] = defaultdict(lambda: ZERO)
    lines = doc.get("lines")
    for ln in lines if isinstance(lines, list) else []:
        if not isinstance(ln, dict):
            continue
        amt = line_amount(ln.get("qty"), ln.get("price"))
        ln["amount"] = to_float(amt)
        net += amt
        raw_vat = ln.get("vat")
        rate = ZERO if klein else (dec(raw_vat) if raw_vat not in (None, "") else default_vat)
        by_rate[rate] += amt
    vat = {r: vat_amount(a, r) for r, a in by_rate.items()}
    vat_total = sum(vat.values(), ZERO)
    return dict(
        net=to_float(net),
        vat={float(r): to_float(v) for r, v in vat.items()},
        vat_total=to_float(vat_total),
        gross=to_float(net + vat_total),
        by_rate={float(r): to_float(a) for r, a in by_rate.items()},
    )


# -- capacity ----------------------------------------------------------------
def r2(x) -> float:
    """Round an hour/number value half-up to 2 decimals (no binary-float surprises)."""
    return to_float(dec(x))


def rnd(x, places: int) -> float:
    """Round half-up to ``places`` decimals."""
    return float(dec(x).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def _workdays(user) -> list[int]:
    wd = user.get("workdays")
    if isinstance(wd, list):
        wd = sorted({x for x in wd if isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 6})
        if wd:
            return wd
    return WORKDAYS_DEFAULT


class Capacity:
    """Target hours per day per user, honouring workdays, holidays, absences."""

    def __init__(self, store, ctx=None):
        self.ctx = ctx or context(store)
        self.state = self.ctx["settings"].get("state", "")
        self._hol = {}
        self.absences = defaultdict(set)  # user -> {date}
        self.absence_info: dict[str, dict] = defaultdict(dict)
        for a in store.list("absences"):
            uid, s, e = a.get("user_id"), d(a.get("start")), d(a.get("end") or a.get("start"))
            if not uid or not s or not e:
                log.warning("skipping incomplete absence %s", a.get("id"))
                continue
            for day in daterange(s, min(e, s + timedelta(days=400))):
                self.absences[uid].add(day)
                self.absence_info[uid][day] = a.get("kind", "vacation")

    def holiday(self, day):
        if day.year not in self._hol:
            self._hol[day.year] = de_holidays(day.year, self.state)
        return self._hol[day.year].get(day)

    def is_workday(self, user, day):
        return day.weekday() in _workdays(user)

    def target(self, user, day):
        wd = _workdays(user)
        if day.weekday() not in wd or self.holiday(day) or day in self.absences[user["id"]]:
            return 0.0
        return fnum(user.get("weekly_hours")) / len(wd)

    def week_target(self, user, week_start):
        return sum(self.target(user, week_start + timedelta(days=i)) for i in range(7))


def entry_hours(entries, key=lambda e: e["date"]):
    out: dict[Any, float] = defaultdict(float)
    for e in entries:
        out[key(e)] += fnum(e.get("minutes")) / 60.0
    return out


def dashboard(store, user, today=None):
    today = today or date.today()
    cap = Capacity(store)
    ws = monday(today)
    entries = [e for e in store.list("entries") if e.get("user_id") == user["id"] and d(e.get("date"))]
    by_day = entry_hours(entries)
    week_days = [ws + timedelta(days=i) for i in range(7)]
    target = cap.week_target(user, ws)
    worked = sum(by_day.get(iso(x), 0) for x in week_days)
    remaining_days = [x for x in week_days if x >= today and cap.target(user, x) > 0]
    # balance since tracking start (flex-time account)
    start = d(user.get("tracking_start"))
    balance = None
    if start and start < today:
        tgt = sum(cap.target(user, x) for x in daterange(start, today - timedelta(days=1)))
        wrk = sum(h for k, h in by_day.items() if start <= d(k) < today)  # type: ignore[operator]
        balance = r2(wrk - tgt)
    days = []
    for x in week_days:
        days.append(
            dict(
                date=iso(x),
                target=r2(cap.target(user, x)),
                worked=r2(by_day.get(iso(x), 0)),
                holiday=cap.holiday(x),
                absence=cap.absence_info[user["id"]].get(x),
            )
        )
    left = max(target - worked, 0)
    return dict(
        week_start=iso(ws),
        target=r2(target),
        worked=r2(worked),
        remaining=r2(left),
        overtime=r2(max(worked - target, 0)),
        per_day_needed=r2(left / len(remaining_days)) if remaining_days else 0,
        remaining_days=len(remaining_days),
        days=days,
        balance=balance,
        today_worked=r2(by_day.get(iso(today), 0)),
    )


# -- forecast ----------------------------------------------------------------
def forecast(store, weeks=26, user_id=None, include_offers=True, today=None):
    """Week-by-week capacity against commitments. The rules live in :mod:`permita38.core.service.planning`."""
    from .service.plan_service import PlanningService  # lazy: the planning engine builds on this module

    return PlanningService(store).forecast(weeks, user_id, include_offers, today)


# -- finance -----------------------------------------------------------------
def invoice_amounts(inv, settings):
    t = doc_totals(dict(inv), settings)
    return t


_MONEY_KEYS = ("revenue", "vat_out", "expenses", "vat_in")


def _line_view(net, vat, deductible, vat_deductible) -> dict[str, Decimal | None]:
    """One effective expense line with exact values; percentages are Decimal percents (70 means 70 %)."""
    pct = dec(deductible if deductible is not None else 0)
    return {
        "net": dec(net),
        "vat": dec(vat),
        "pct": pct,
        "vat_pct": pct if vat_deductible is None else dec(vat_deductible),
    }


def expense_lines(x: dict) -> list[dict[str, Any]]:
    """The effective lines of an expense: its stored ``lines``, or one implicit line built from the top-level fields
    for a classic single-amount record. Each line has ``net``, ``vat``, ``pct`` (deductible share), ``vat_pct`` (share
    of the input VAT that is reclaimed; same as ``pct`` unless set) and ``category`` (line or expense category)."""
    raw = x.get("lines")
    if isinstance(raw, list) and raw:
        out = []
        for ln in raw:
            if not isinstance(ln, dict):
                continue
            view = _line_view(ln.get("net"), ln.get("vat"), ln.get("deductible", 100), ln.get("vat_deductible"))
            view["category"] = ln.get("category") or x.get("category")
            out.append(view)
        return out
    view = _line_view(x.get("net"), x.get("vat"), x.get("deductible", 100), x.get("vat_deductible"))
    view["category"] = x.get("category")
    return [view]


def itemise_expense(rec: dict) -> dict:
    """Server-side derivation for an expense with ``lines`` (docs/specs/itemised-expenses.md, API section).

    Each line gets its ``net``/``vat`` (per rate group, see ``money.split_gross``); the top-level ``net``, ``vat``,
    ``vat_rate`` (common rate or None when mixed) and ``deductible`` (net-weighted share, 100 when the net sum is 0)
    are overwritten. A record without lines (absent or empty) is returned as a classic expense.
    """
    lines = rec.get("lines")
    if not lines:
        rec.pop("lines", None)
        return rec
    gross_rates = [(dec(ln.get("gross")), dec(ln.get("vat_rate"))) for ln in lines]
    split = split_gross(gross_rates)
    out_lines = []
    for ln, (net, vat) in zip(lines, split, strict=True):
        out_lines.append({**ln, "net": to_float(net), "vat": to_float(vat)})
    net_sum = sum((n for n, _ in split), ZERO)
    vat_sum = sum((v for _, v in split), ZERO)
    rates = {r for _, r in gross_rates}
    weighted = sum((n * dec(ln.get("deductible", 100)) for (n, _), ln in zip(split, lines, strict=True)), ZERO)
    deductible = 100.0 if net_sum == 0 else to_float(q2(weighted / net_sum))
    return {
        **rec,
        "lines": out_lines,
        "net": to_float(net_sum),
        "vat": to_float(vat_sum),
        "vat_rate": float(rates.pop()) if len(rates) == 1 else None,
        "deductible": deductible,
    }


def trip_amounts(trip: dict) -> list[dict[str, Any]]:
    """The finance lines of a business trip: its meal allowance per day and its mileage (dated at the trip start).

    The single place where trips feed the finance report (docs/specs/business-trips.md). Both are deductible at 100 %
    and carry no VAT. Only a FINAL trip counts: drafts are excluded until they are finalised. The figures are the ones
    stored with the trip, so a final trip never changes here.
    """
    if trip.get("status") != "final":
        return []
    out: list[dict[str, Any]] = [
        {"date": x.get("date"), "category": "Meal allowance", "amount": x.get("amount")}
        for x in trip.get("days") or []
        if isinstance(x, dict)
    ]
    if trip.get("mileage_total"):
        out.append({"date": str(trip.get("start") or "")[:10], "category": "Mileage", "amount": trip["mileage_total"]})
    return out


def _pos(x: Decimal) -> Decimal:
    return x if x > 0 else ZERO


def _floats(x: Any) -> Any:
    """Decimals inside a result become cents-exact floats (the response boundary)."""
    if isinstance(x, Decimal):
        return to_float(x)
    if isinstance(x, dict):
        return {k: _floats(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_floats(v) for v in x]
    return x


_PUBLIC_TOP = (
    "year",
    "months",
    "totals",
    "categories",
    "open_receivables",
    "kleinunternehmer",
    "company_type",
    "projected_profit",
    "extrapolated",
)
_PUBLIC_MONTH = ("month", "revenue", "vat_out", "expenses", "vat_in", "profit", "vat_due", "vat_set_aside")
_PUBLIC_TAX = ("gewst", "kst", "kst_soli")
_PUBLIC_OWED = ("as_of", "gewst", "kst", "kst_soli")


def _pick(src: dict, keys: tuple[str, ...]) -> dict:
    return {k: src[k] for k in keys if k in src}


def redact_tax(result: dict) -> dict:
    """The /finance result for a caller who may see business figures only (accountant, any API token).

    Built from allowlists at every level (docs/specs/tax-burden-and-settings.md, "/finance redaction"): a key added
    to ``finance`` later stays hidden until it is listed here.
    """
    out = _pick(result, _PUBLIC_TOP)
    out["months"] = [_pick(m, _PUBLIC_MONTH) for m in result.get("months") or []]
    for k in ("tax_actual", "tax_projected"):
        out[k] = _pick(result.get(k) or {}, _PUBLIC_TAX) | {"redacted": True}
    owed = result.get("tax_owed")
    out["tax_owed"] = None if owed is None else _pick(owed, _PUBLIC_OWED) | {"redacted": True}
    out["reserve"] = {"redacted": True}
    out["warnings"] = [w for w in result.get("warnings") or [] if w != NOT_SAVED_WARNING]
    out["redacted"] = True
    return out


def finance(store, year, today=None):
    today = today or date.today()
    s = raw = store.settings()
    klein = bool(s.get("kleinunternehmer"))
    accrual = s.get("accounting", "eur") == "accrual"
    months: list[dict[str, Any]] = [dict(month=m, **{k: ZERO for k in _MONEY_KEYS}) for m in range(1, 13)]
    open_receivables = ZERO
    for inv in store.list("invoices"):
        if inv.get("status") in ("draft", "cancelled"):
            continue
        t = doc_totals(dict(inv), s)
        when = d(inv.get("date")) if accrual else d(inv.get("paid_date"))
        if inv.get("status") != "paid" and not accrual:
            open_receivables += dec(t["gross"])
            continue
        if inv.get("status") != "paid":
            open_receivables += dec(t["gross"])
        if when and when.year == year:
            m = months[when.month - 1]
            m["revenue"] += dec(t["net"])
            m["vat_out"] += dec(t["vat_total"])
    cats: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for x in store.list("expenses"):
        # docs/specs/receipt-scan.md: the cost counts on the paid date under `eur` (null/damaged = the bill date) and on
        # the bill date under `accrual`; the input VAT always counts on the bill date. Each picks its own month/year.
        bill = d(x.get("date"))
        cost_when = (d(x.get("paid_on")) or bill) if not accrual else bill
        cost_m = months[cost_when.month - 1] if cost_when and cost_when.year == year else None
        vat_m = months[bill.month - 1] if bill and bill.year == year else None
        if cost_m is None and vat_m is None:
            continue
        for ln in expense_lines(x):
            # per line: a business meal is 70 % deductible as an expense while its input VAT is reclaimed in full
            # (§ 15 Abs. 1a UStG), so the VAT share is set apart from the cost share
            pct = ln["pct"] / 100
            vat_pct = ln["vat_pct"] / 100
            net, vat_raw = ln["net"], ln["vat"]
            # Kleinunternehmer cannot reclaim input VAT, so the gross amount is the cost
            cost = q2((net + vat_raw) * pct) if klein else q2(net * pct)
            if cost_m is not None:
                cost_m["expenses"] += cost
                cats[ln["category"] or "Other"] += cost
            if not klein and vat_m is not None:
                vat_m["vat_in"] += q2(vat_raw * vat_pct)
    for t in store.list("trips"):
        for item in trip_amounts(t):
            when = d(item["date"])
            if not when or when.year != year:
                continue
            amount = dec(item["amount"])
            months[when.month - 1]["expenses"] += amount  # deductible 100 %, no VAT (also in Kleinunternehmer mode)
            cats[item["category"]] += amount
    for m in months:
        m["profit"] = m["revenue"] - m["expenses"]
        m["vat_due"] = ZERO if klein else m["vat_out"] - m["vat_in"]

    keys = (*_MONEY_KEYS, "profit", "vat_due")
    tot = {k: sum((m[k] for m in months), ZERO) for k in keys}
    profit_ytd = tot["profit"]
    profile = PersonalTaxService(store).effective()
    days = (today - date(year, 1, 1)).days + 1
    extrapolated = year == today.year and days >= 60
    if extrapolated:
        projected = _pos(q2(profit_ytd * 365 / min(days, 365)))
    else:
        projected = _pos(profit_ytd)
    proj = tax.estimate(projected, year, s, profile)
    actual = tax.estimate(profit_ytd, year, s, profile)

    # currently owed (no projection) and the monthly reserve, see docs/specs/tax-burden-and-settings.md
    tax_owed: dict[str, Any] | None = None
    owed_now = ZERO
    if year <= today.year:
        owed = actual["tax_due"] - actual["withheld"]
        owed_now = _pos(owed)
        tax_owed = dict(
            as_of=today.isoformat() if year == today.year else f"{year}-12-31",
            profit_ytd=profit_ytd,
            est=actual["est"],
            soli=actual["soli"],
            church=actual["church"],
            gewst=actual["gewst"],
            kst=actual["kst"],
            kst_soli=actual["kst_soli"],
            tax_due=actual["tax_due"],
            withheld=actual["withheld"],
            owed=owed,
        )
    due_year = _pos(proj["tax_due"] - proj["withheld"])
    if year < today.year:
        months_left, remaining, monthly = 0, ZERO, ZERO
    elif year == today.year:
        months_left = 13 - today.month
        remaining = _pos(due_year - owed_now)
        monthly = q2(remaining / months_left)
    else:
        months_left, remaining = 12, due_year
        monthly = q2(remaining / 12)
    reserve = dict(
        due_year=due_year,
        withheld=proj["withheld"],
        owed_now=owed_now,
        remaining=remaining,
        months_left=months_left,
        monthly=monthly,
        balance=proj["balance"],
    )
    # months that have begun (c) and the month the set-aside starts in (s), see the spec "Months"
    counted = 12 if year < today.year else today.month if year == today.year else 0
    start = 12 if year < today.year else today.month if year == today.year else 1
    self_social = sum((proj["insurance"][k] for k in ("kv", "pv", "rv", "alv")), ZERO) - proj["insurance"]["subsidy"]
    social_m = q2(self_social / 12)
    take_home = ZERO
    for m in months:
        n = m["month"]
        aside = ZERO if n < start else owed_now + monthly if n == start else monthly
        m["tax_set_aside"] = to_float(aside)
        m["vat_set_aside"] = to_float(m["vat_due"])
        if n <= counted:
            m["social"] = to_float(social_m)
            th = m["profit"] - aside - social_m
            take_home += th
            m["take_home"] = to_float(th)
        else:
            m["social"] = 0.0
            m["take_home"] = None
        for k in keys:
            m[k] = to_float(m[k])
    warnings = []
    if klein and tot["revenue"] > Decimal(str(tax.KLEIN_LIMIT_PREV)):
        warnings.append("Kleinunternehmer: revenue exceeds 25,000 EUR (prior-year limit). Check §19 UStG.")
    if raw.get("personal_tax") is None:
        warnings.append(NOT_SAVED_WARNING)
    if rules_for(year).to_verify:
        warnings.append(f"Some rates for {year} are not yet confirmed.")
    for w in (*actual["warnings"], *proj["warnings"]):
        if w not in warnings:
            warnings.append(w)
    return dict(
        year=year,
        months=months,
        totals={k: to_float(v) for k, v in tot.items()},
        tax_actual=_floats(actual),
        tax_projected=_floats(proj),
        tax_owed=_floats(tax_owed),
        reserve=_floats(reserve),
        projected_profit=to_float(projected),
        extrapolated=extrapolated,
        effective_rate=rnd(proj["rate"], 4),
        social_year=to_float(social_m * counted),
        take_home=to_float(take_home),
        categories={k: to_float(v) for k, v in sorted(cats.items(), key=lambda kv: -kv[1])},
        open_receivables=to_float(open_receivables),
        warnings=warnings,
        kleinunternehmer=klein,
        company_type=s.get("company_type", "freiberufler"),
    )


def project_report(store):
    ctx = context(store)
    rows: dict[str, dict[str, Any]] = {}
    for p in ctx["projects"].values():
        rows[p["id"]] = {
            k: ZERO for k in ("hours", "revenue", "cost", "expenses", "unbilled_hours", "unbilled_revenue")
        }
        rows[p["id"]]["id"] = p["id"]
    for e in store.list("entries"):
        r = rows.get(e.get("project_id"))
        if not r:
            continue
        minutes = dec(e.get("minutes"))
        h = minutes / 60
        r["hours"] += h
        u = ctx["users"].get(e.get("user_id")) or {}
        r["cost"] += h * dec(u.get("cost_rate"))
        if e.get("billable", True):
            rev = h * dec(rate_for(e, ctx))
            r["revenue"] += rev
            if not e.get("invoice_id"):
                r["unbilled_hours"] += h
                r["unbilled_revenue"] += rev
    for x in store.list("expenses"):
        r = rows.get(x.get("project_id"))
        if r:
            r["expenses"] += dec(x.get("net"))
    for r in rows.values():
        r["margin"] = r["revenue"] - r["cost"] - r["expenses"]
        for k in list(r):
            if isinstance(r[k], Decimal):
                r[k] = to_float(r[k])
    return rows
