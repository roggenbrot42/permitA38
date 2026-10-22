"""Independent reference implementations used as the test oracle.

Nothing here imports permita38: the expectations are re-derived from the documented behaviour so that a
bug in the application code cannot hide behind the same bug in the checker.
"""

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
DEFAULT_VAT = 19.0


def easter(y: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(y, month, day + 1)


def nationwide_holidays(y: int) -> set[date]:
    e = easter(y)
    return {
        date(y, 1, 1),
        e - timedelta(days=2),
        e + timedelta(days=1),
        date(y, 5, 1),
        e + timedelta(days=39),
        e + timedelta(days=50),
        date(y, 10, 3),
        date(y, 12, 25),
        date(y, 12, 26),
    }


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_username(u: str) -> str | None:
    u = (u or "").strip().lower()
    return u if USERNAME_RE.match(u) else None


def line_amount(line: dict) -> float:
    return float(money(Decimal(str(line.get("qty", 0))) * Decimal(str(line.get("price", 0)))))


def doc_totals(doc: dict, klein: bool) -> dict:
    """net / vat / gross of an offer or invoice. Per-line VAT beats the document rate beats 19 %."""
    doc_rate = doc.get("vat_rate")
    doc_rate = DEFAULT_VAT if doc_rate is None else float(doc_rate)
    by_rate: dict[float, Decimal] = {}
    net = Decimal(0)
    for ln in doc.get("lines", []):
        amt = Decimal(str(line_amount(ln)))
        net += amt
        rate = 0.0 if klein else float(ln["vat"] if ln.get("vat") is not None else doc_rate)
        by_rate[rate] = by_rate.get(rate, Decimal(0)) + amt
    vat = sum((amt * Decimal(str(r)) / 100 for r, amt in by_rate.items()), Decimal(0))
    return {"net": float(net), "vat": float(vat), "gross": float(net + vat)}


def resolve_rate(entry: dict, tasks: dict, projects: dict, customers: dict, users: dict, default_rate: float) -> float:
    """entry override > task > project > customer > person's default rate > global default."""
    if entry.get("rate") is not None:
        return float(entry["rate"])
    task = tasks.get(entry.get("task_id"))
    proj = projects.get(entry.get("project_id"))
    cust = customers.get(proj.get("customer_id")) if proj else None
    for src in (task, proj, cust):
        if src and src.get("rate") is not None:
            return float(src["rate"])
    user = users.get(entry.get("user_id"))
    if user and user.get("default_rate") is not None:
        return float(user["default_rate"])
    return float(default_rate)


def week_target_and_worked(user: dict, entries: list[dict], absences: list[dict], today: date) -> tuple[float, float]:
    """Weekly goal (after public holidays / time off) and hours worked in the current Mon-Sun week."""
    ws = monday(today)
    days = [ws + timedelta(days=i) for i in range(7)]
    holidays = set()
    for y in {d.year for d in days}:
        holidays |= nationwide_holidays(y)
    off: set[date] = set()
    for a in absences:
        if a.get("user_id") != user["id"]:
            continue
        s = date.fromisoformat(a["start"])
        e = date.fromisoformat(a.get("end") or a["start"])
        off |= {s + timedelta(days=i) for i in range((e - s).days + 1)}
    workdays = user.get("workdays") or [0, 1, 2, 3, 4]
    per_day = float(user.get("weekly_hours", 40)) / len(workdays)
    target = sum(per_day for d in days if d.weekday() in workdays and d not in holidays and d not in off)
    iso = {d.isoformat() for d in days}
    worked = sum(e["minutes"] for e in entries if e.get("user_id") == user["id"] and e["date"] in iso) / 60
    return target, worked
