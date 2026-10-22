"""Business logic: rates, document totals, capacity, forecast, finance.

Money is computed with :mod:`permita38.core.money` (exact decimals, half-up cents). Stored records can come from
older versions or from another device through file sync, so every function here treats damaged values (bad dates,
non-numbers) defensively: the record is skipped or the value counts as 0, a warning is logged, and an aggregate view
never fails because of one bad record.
"""

import logging
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from . import tax
from .holidays import holidays as de_holidays
from .money import ZERO, dec, line_amount, q2, to_float, vat_amount

log = logging.getLogger(__name__)

WORKDAYS_DEFAULT = [0, 1, 2, 3, 4]


def d(s):
    """Parse ``YYYY-MM-DD``; ``None`` for empty or invalid values (logged)."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        log.warning("ignoring invalid date %r", s)
        return None


def fnum(x, default: float = 0.0) -> float:
    """Float from a stored value; damaged values (text, NaN, infinity, overflow) count as ``default``."""
    if x is None or x == "":
        return default
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        log.warning("ignoring invalid number %r", x)
        return default
    return v if math.isfinite(v) else default


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
    today = today or date.today()
    ctx = context(store)
    cap = Capacity(store, ctx)
    users = [u for u in ctx["users"].values() if u.get("active", True)]
    if user_id:
        users = [u for u in users if u["id"] == user_id]
    uids = {u["id"] for u in users}
    boss = next((u["id"] for u in users if u.get("role") == "boss"), users[0]["id"] if users else None)
    start = monday(today)
    wk = [start + timedelta(weeks=i) for i in range(weeks)]
    wkset = {w: i for i, w in enumerate(wk)}

    plans = []
    for c in store.list("contracts"):
        if c.get("status") != "done":
            plans.append((c, False))
    if include_offers:
        converted = {c.get("offer_id") for c in store.list("contracts")}
        for o in store.list("offers"):
            if o.get("status") == "sent" and o.get("plan") and o["id"] not in converted:
                p = dict(o["plan"])
                p.update(
                    id=o["id"],
                    title="Offer %s" % (o.get("number") or "(draft)"),
                    customer_id=o.get("customer_id"),
                    rate=p.get("rate"),
                )
                plans.append((p, True))

    rows: list[dict[str, Any]] = [
        dict(week=iso(w), capacity=0.0, confirmed=0.0, tentative=0.0, revenue=0.0, tentative_revenue=0.0, events=[])
        for w in wk
    ]
    for u in users:
        for i, w in enumerate(wk):
            rows[i]["capacity"] += cap.week_target(u, w)
            for k in range(7):
                day = w + timedelta(days=k)
                if cap.holiday(day) and cap.is_workday(u, day):
                    ev = cap.holiday(day)
                    if ev not in rows[i]["events"]:
                        rows[i]["events"].append(ev)
            n_abs = sum(
                1
                for k in range(7)
                if (w + timedelta(days=k)) in cap.absences[u["id"]] and cap.is_workday(u, w + timedelta(days=k))
            )
            if n_abs:
                rows[i]["events"].append("%s: %d day(s) off" % (u.get("name", "?"), n_abs))

    details = []
    for c, tentative in plans:
        s, e = d(c.get("start")), d(c.get("end"))
        if not s or not e or e < s:
            log.warning("skipping contract/offer %s with an invalid period", c.get("id"))
            continue
        assigned = [x for x in (c.get("user_ids") or [boss]) if x in uids] or []
        if not assigned:
            continue
        rate = c.get("rate")
        if rate in (None, ""):
            rate = (
                (ctx["projects"].get(c.get("project_id")) or {}).get("rate")
                or (ctx["customers"].get(c.get("customer_id")) or {}).get("rate")
                or ctx["settings"].get("default_rate")
                or 0
            )
        rate = fnum(rate)
        # weekly weights
        per_week = {}
        for w in wk:
            lo, hi = max(w, s), min(w + timedelta(days=6), e)
            if lo > hi:
                continue
            workdays = sum(1 for x in daterange(lo, hi) if x.weekday() < 5)
            per_week[w] = (
                workdays,
                sum(cap.target(u, x) for u in users if u["id"] in assigned for x in daterange(lo, hi)),
            )
        if fnum(c.get("hours_per_week")):
            hpw = fnum(c["hours_per_week"])
            hours = {w: hpw * wd / 5.0 for w, (wd, _) in per_week.items()}
        elif fnum(c.get("total_hours")):
            # spread over capacity of the whole contract (also outside the visible window)
            full = sum(
                cap.target(u, x)
                for u in ctx["users"].values()
                if u["id"] in assigned
                for x in daterange(max(s, today - timedelta(days=0)), e)
            )
            full = full or 1
            hours = {w: fnum(c["total_hours"]) * wc / full for w, (_, wc) in per_week.items()}
        else:
            continue
        total_visible = 0.0
        for w, h in hours.items():
            i = wkset[w]
            key = "tentative" if tentative else "confirmed"
            rows[i][key] += h
            rows[i]["tentative_revenue" if tentative else "revenue"] += h * rate
            total_visible += h
        details.append(
            dict(
                id=c.get("id"),
                title=c.get("title") or "Contract",
                tentative=tentative,
                customer_id=c.get("customer_id"),
                start=c["start"],
                end=c["end"],
                hours_visible=rnd(total_visible, 1),
                rate=rate,
            )
        )
    for r in rows:
        r["free"] = r["capacity"] - r["confirmed"]
        r["free_incl_tentative"] = r["free"] - r["tentative"]
        for fld in (
            "capacity",
            "confirmed",
            "tentative",
            "revenue",
            "tentative_revenue",
            "free",
            "free_incl_tentative",
        ):
            r[fld] = rnd(r[fld], 1 if fld not in ("revenue", "tentative_revenue") else 0)
    return dict(weeks=rows, contracts=details)


# -- finance -----------------------------------------------------------------
def invoice_amounts(inv, settings):
    t = doc_totals(dict(inv), settings)
    return t


_MONEY_KEYS = ("revenue", "vat_out", "expenses", "vat_in")


def finance(store, year, today=None):
    today = today or date.today()
    s = store.settings()
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
        when = d(x.get("date"))
        if not when or when.year != year:
            continue
        raw_pct = x.get("deductible", 100)
        pct = dec(raw_pct if raw_pct is not None else 0) / 100
        net, vat_raw = dec(x.get("net")), dec(x.get("vat"))
        m = months[when.month - 1]
        # Kleinunternehmer cannot reclaim input VAT, so the gross amount is the cost
        cost = q2((net + vat_raw) * pct) if klein else q2(net * pct)
        m["expenses"] += cost
        if not klein:
            m["vat_in"] += q2(vat_raw * pct)
        cats[x.get("category") or "Other"] += cost
    for m in months:
        m["profit"] = m["revenue"] - m["expenses"]
        m["vat_due"] = ZERO if klein else m["vat_out"] - m["vat_in"]

    keys = (*_MONEY_KEYS, "profit", "vat_due")
    tot = {k: sum((m[k] for m in months), ZERO) for k in keys}
    profit_f = float(tot["profit"])
    # annualise the current year for the set-aside rate
    if year == today.year:
        frac = max((today - date(year, 1, 1)).days + 1, 30) / 365.0
        projected = max(profit_f / min(frac, 1.0), 0)
    else:
        projected = max(profit_f, 0)
    proj_tax = tax.estimate(projected, year, s)
    actual_tax = tax.estimate(profit_f, year, s)
    rate = dec(proj_tax["rate"])
    social_m = dec(s.get("social_monthly")) if s.get("company_type") != "gmbh" else ZERO
    # social insurance paid so far (full year for past years, none for future ones)
    social_months = 12 if year < today.year else today.month if year == today.year else 0
    for m in months:
        set_aside = q2(max(m["profit"], ZERO) * rate)
        m["tax_set_aside"] = to_float(set_aside)
        m["social"] = to_float(social_m)
        m["vat_set_aside"] = to_float(m["vat_due"])
        m["take_home"] = to_float(m["profit"] - set_aside - social_m)
        for k in keys:
            m[k] = to_float(m[k])
    warnings = []
    if klein and tot["revenue"] > Decimal(str(tax.KLEIN_LIMIT_PREV)):
        warnings.append("Kleinunternehmer: revenue exceeds 25,000 EUR (prior-year limit). Check §19 UStG.")
    return dict(
        year=year,
        months=months,
        totals={k: to_float(v) for k, v in tot.items()},
        tax_actual=actual_tax,
        tax_projected=proj_tax,
        projected_profit=r2(projected),
        effective_rate=rnd(proj_tax["rate"], 4),
        social_year=to_float(social_m * social_months),
        take_home=to_float(tot["profit"] - dec(actual_tax["total"]) - social_m * social_months),
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
        rows[p["id"]] = {k: ZERO for k in ("hours", "revenue", "cost", "expenses", "unbilled_hours", "unbilled_revenue")}
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
