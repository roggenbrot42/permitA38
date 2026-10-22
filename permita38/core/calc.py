"""Business logic: rates, document totals, capacity, forecast, finance."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from . import tax
from .holidays import holidays as de_holidays

WORKDAYS_DEFAULT = [0, 1, 2, 3, 4]


def d(s):
    return datetime.strptime(s, "%Y-%m-%d").date() if isinstance(s, str) and s else None


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
        return float(entry["rate"])
    task = ctx["tasks"].get(entry.get("task_id"))
    proj = ctx["projects"].get(entry.get("project_id"))
    cust = ctx["customers"].get((proj or {}).get("customer_id"))
    user = ctx["users"].get(entry.get("user_id"))
    for src in (task, proj, cust, user):
        if src and src.get("rate") not in (None, ""):
            return float(src["rate"])
        if src is user and src and src.get("default_rate") not in (None, ""):
            return float(src["default_rate"])
    return float(ctx["settings"].get("default_rate") or 0)


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
    klein = bool(settings.get("kleinunternehmer"))
    default_vat = 0 if klein else float(doc.get("vat_rate", settings.get("vat_rate", 19)) or 0)
    net = 0.0
    by_rate: dict[float, float] = defaultdict(float)
    for ln in doc.get("lines", []):
        amt = round(float(ln.get("qty") or 0) * float(ln.get("price") or 0), 2)
        ln["amount"] = amt
        net += amt
        rate = 0 if klein else float(ln.get("vat") if ln.get("vat") not in (None, "") else default_vat)
        by_rate[rate] += amt
    vat = {r: round(a * r / 100, 2) for r, a in by_rate.items()}
    net = round(net, 2)
    return dict(
        net=net,
        vat=vat,
        vat_total=round(sum(vat.values()), 2),
        gross=round(net + sum(vat.values()), 2),
        by_rate=dict(by_rate),
    )


# -- capacity ----------------------------------------------------------------
class Capacity:
    """Target hours per day per user, honouring workdays, holidays, absences."""

    def __init__(self, store, ctx=None):
        self.ctx = ctx or context(store)
        self.state = self.ctx["settings"].get("state", "")
        self._hol = {}
        self.absences = defaultdict(set)  # user -> {date}
        self.absence_info: dict[str, dict] = defaultdict(dict)
        for a in store.list("absences"):
            s, e = d(a.get("start")), d(a.get("end") or a.get("start"))
            if s and e:
                for day in daterange(s, min(e, s + timedelta(days=400))):
                    self.absences[a["user_id"]].add(day)
                    self.absence_info[a["user_id"]][day] = a.get("kind", "vacation")

    def holiday(self, day):
        if day.year not in self._hol:
            self._hol[day.year] = de_holidays(day.year, self.state)
        return self._hol[day.year].get(day)

    def is_workday(self, user, day):
        wd = user.get("workdays") or WORKDAYS_DEFAULT
        return day.weekday() in wd

    def target(self, user, day):
        wd = user.get("workdays") or WORKDAYS_DEFAULT
        if day.weekday() not in wd or self.holiday(day) or day in self.absences[user["id"]]:
            return 0.0
        return float(user.get("weekly_hours") or 0) / len(wd)

    def week_target(self, user, week_start):
        return sum(self.target(user, week_start + timedelta(days=i)) for i in range(7))


def entry_hours(entries, key=lambda e: e["date"]):
    out: dict[Any, float] = defaultdict(float)
    for e in entries:
        out[key(e)] += (e.get("minutes") or 0) / 60.0
    return out


def dashboard(store, user, today=None):
    today = today or date.today()
    cap = Capacity(store)
    ws = monday(today)
    entries = [e for e in store.list("entries") if e.get("user_id") == user["id"]]
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
        wrk = sum(h for k, h in by_day.items() if start <= d(k) < today)
        balance = round(wrk - tgt, 2)
    days = []
    for x in week_days:
        days.append(
            dict(
                date=iso(x),
                target=round(cap.target(user, x), 2),
                worked=round(by_day.get(iso(x), 0), 2),
                holiday=cap.holiday(x),
                absence=cap.absence_info[user["id"]].get(x),
            )
        )
    left = max(target - worked, 0)
    return dict(
        week_start=iso(ws),
        target=round(target, 2),
        worked=round(worked, 2),
        remaining=round(left, 2),
        overtime=round(max(worked - target, 0), 2),
        per_day_needed=round(left / len(remaining_days), 2) if remaining_days else 0,
        remaining_days=len(remaining_days),
        days=days,
        balance=balance,
        today_worked=round(by_day.get(iso(today), 0), 2),
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
                rows[i]["events"].append("%s: %d day(s) off" % (u["name"], n_abs))

    details = []
    for c, tentative in plans:
        s, e = d(c.get("start")), d(c.get("end"))
        if not s or not e or e < s:
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
        rate = float(rate)
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
        if c.get("hours_per_week"):
            hpw = float(c["hours_per_week"])
            hours = {w: hpw * wd / 5.0 for w, (wd, _) in per_week.items()}
        elif c.get("total_hours"):
            # spread over capacity of the whole contract (also outside the visible window)
            full = sum(
                cap.target(u, x)
                for u in ctx["users"].values()
                if u["id"] in assigned
                for x in daterange(max(s, today - timedelta(days=0)), e)
            )
            full = full or 1
            hours = {w: float(c["total_hours"]) * wc / full for w, (_, wc) in per_week.items()}
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
                hours_visible=round(total_visible, 1),
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
            r[fld] = round(r[fld], 1 if fld not in ("revenue", "tentative_revenue") else 0)
    return dict(weeks=rows, contracts=details)


# -- finance -----------------------------------------------------------------
def invoice_amounts(inv, settings):
    t = doc_totals(dict(inv), settings)
    return t


def finance(store, year, today=None):
    today = today or date.today()
    s = store.settings()
    klein = bool(s.get("kleinunternehmer"))
    accrual = s.get("accounting", "eur") == "accrual"
    months = [dict(month=m, revenue=0.0, vat_out=0.0, expenses=0.0, vat_in=0.0, profit=0.0) for m in range(1, 13)]
    open_receivables = 0.0
    for inv in store.list("invoices"):
        if inv.get("status") in ("draft", "cancelled"):
            continue
        t = doc_totals(dict(inv), s)
        when = d(inv.get("date")) if accrual else d(inv.get("paid_date"))
        if inv.get("status") != "paid" and not accrual:
            open_receivables += t["gross"]
            continue
        if inv.get("status") != "paid":
            open_receivables += t["gross"]
        if when and when.year == year:
            m = months[when.month - 1]
            m["revenue"] += t["net"]
            m["vat_out"] += t["vat_total"]
    cats: dict[str, float] = defaultdict(float)
    for x in store.list("expenses"):
        when = d(x.get("date"))
        if not when or when.year != year:
            continue
        pct = float(x.get("deductible", 100) or 0) / 100
        net = float(x.get("net") or 0)
        vat = 0.0 if klein else float(x.get("vat") or 0)
        m = months[when.month - 1]
        # Kleinunternehmer cannot reclaim input VAT, so the gross amount is the cost
        cost = (net + float(x.get("vat") or 0)) * pct if klein else net * pct
        m["expenses"] += cost
        m["vat_in"] += vat * pct
        cats[x.get("category") or "Other"] += cost
    for m in months:
        m["profit"] = m["revenue"] - m["expenses"]
        m["vat_due"] = 0.0 if klein else m["vat_out"] - m["vat_in"]

    tot = {k: sum(m[k] for m in months) for k in ("revenue", "vat_out", "expenses", "vat_in", "profit", "vat_due")}
    # annualise the current year for the set-aside rate
    if year == today.year:
        frac = max((today - date(year, 1, 1)).days + 1, 30) / 365.0
        projected = max(tot["profit"] / min(frac, 1.0), 0)
    else:
        projected = max(tot["profit"], 0)
    proj_tax = tax.estimate(projected, year, s)
    actual_tax = tax.estimate(tot["profit"], year, s)
    rate = proj_tax["rate"]
    social_m = float(s.get("social_monthly") or 0) if s.get("company_type") != "gmbh" else 0
    # social insurance paid so far (full year for past years, none for future ones)
    social_months = 12 if year < today.year else today.month if year == today.year else 0
    for m in months:
        m["tax_set_aside"] = round(max(m["profit"], 0) * rate, 2)
        m["social"] = social_m
        m["vat_set_aside"] = round(m["vat_due"], 2)
        m["take_home"] = round(m["profit"] - m["tax_set_aside"] - social_m, 2)
        for k in ("revenue", "vat_out", "expenses", "vat_in", "profit", "vat_due"):
            m[k] = round(m[k], 2)
    warnings = []
    if klein and tot["revenue"] > tax.KLEIN_LIMIT_PREV:
        warnings.append("Kleinunternehmer: revenue exceeds 25,000 EUR (prior-year limit). Check §19 UStG.")
    return dict(
        year=year,
        months=months,
        totals={k: round(v, 2) for k, v in tot.items()},
        tax_actual=actual_tax,
        tax_projected=proj_tax,
        projected_profit=round(projected, 2),
        effective_rate=round(rate, 4),
        social_year=round(social_m * social_months, 2),
        take_home=round(tot["profit"] - actual_tax["total"] - social_m * social_months, 2),
        categories={k: round(v, 2) for k, v in sorted(cats.items(), key=lambda kv: -kv[1])},
        open_receivables=round(open_receivables, 2),
        warnings=warnings,
        kleinunternehmer=klein,
        company_type=s.get("company_type", "freiberufler"),
    )


def project_report(store):
    ctx = context(store)
    rows = {}
    for p in ctx["projects"].values():
        rows[p["id"]] = dict(
            id=p["id"], hours=0.0, revenue=0.0, cost=0.0, expenses=0.0, unbilled_hours=0.0, unbilled_revenue=0.0
        )
    for e in store.list("entries"):
        r = rows.get(e.get("project_id"))
        if not r:
            continue
        h = (e.get("minutes") or 0) / 60
        r["hours"] += h
        u = ctx["users"].get(e.get("user_id")) or {}
        r["cost"] += h * float(u.get("cost_rate") or 0)
        if e.get("billable", True):
            rev = h * rate_for(e, ctx)
            r["revenue"] += rev
            if not e.get("invoice_id"):
                r["unbilled_hours"] += h
                r["unbilled_revenue"] += rev
    for x in store.list("expenses"):
        r = rows.get(x.get("project_id"))
        if r:
            r["expenses"] += float(x.get("net") or 0)
    for r in rows.values():
        r["margin"] = r["revenue"] - r["cost"] - r["expenses"]
        for k in list(r):
            if isinstance(r[k], float):
                r[k] = round(r[k], 2)
    return rows
