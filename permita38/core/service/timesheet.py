"""Project time sheet: who booked how many hours on a project, per week or month (docs/specs/project-centric-planning.md).

Hours are computed from the exact minutes and rounded to two decimals only in the response. No money fields."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from permita38.core import rights
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository

MAX_DAYS = 400
GROUPS = ("week", "month")
MAX_PERIODS = {"week": 60, "month": 14}  # hard bound of the period walk for MAX_DAYS
DELETED_USER = "(deleted user)"


def _hours(minutes: int) -> float:
    return float((Decimal(minutes) / 60).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse(value: str | None, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ServiceError(422, f"'{field}' must be an ISO date (YYYY-MM-DD)") from None


def _lenient(value: str | None) -> date | None:
    """A stored project date: an invalid value is ignored (the entries' range is used instead)."""
    try:
        return date.fromisoformat(value) if value else None
    except (ValueError, TypeError):
        return None


def _add_days(d: date, days: int) -> date:
    try:
        return d + timedelta(days=days)
    except OverflowError:
        return date.max


def _period_start(d: date, group: str) -> date:
    if group == "week":
        return d - timedelta(days=d.weekday()) if d.toordinal() > 7 else date.min
    return d.replace(day=1)


def _next_period(d: date, group: str) -> date:
    if group == "week":
        return d + timedelta(days=7)
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


class TimesheetService:
    def __init__(self, store: Repository):
        self.store = store

    def timesheet(self, me: dict, project_id: str, start: str | None, end: str | None, group: str = "week") -> dict:
        project = self.store.get("projects", project_id)
        if not project:
            raise ServiceError(404, "Project not found")
        if group not in GROUPS:
            raise ServiceError(422, "'group' must be 'week' or 'month'")
        d_from, d_to = _parse(start, "from"), _parse(end, "to")
        if d_from and d_to and d_to < d_from:
            raise ServiceError(422, "'to' must not be before 'from'")
        if d_from and d_to and (d_to - d_from).days > MAX_DAYS:
            raise ServiceError(422, f"The range may span at most {MAX_DAYS} days")

        only_own = rights.scope_for(me, "time") != rights.Scope.ALL
        entries = [e for e in self.store.find("entries", project_id=project_id) if e.get("date")]
        if only_own:  # the default range must not reveal when other people worked
            entries = [e for e in entries if e.get("user_id") == me["id"]]
        dates = sorted(e["date"] for e in entries)
        explicit_from = d_from is not None
        if d_from is None:
            d_from = _lenient(project.get("start")) or _lenient(dates[0] if dates else None)
        if d_to is None:
            d_to = _lenient(project.get("end")) or _lenient(dates[-1] if dates else None)
        # a default range (project period or entries) is adjusted, never refused
        if d_from and d_to and d_to < d_from:
            if explicit_from:
                d_to = d_from
            else:
                d_from = d_to
        if d_from and d_to and (d_to - d_from).days > MAX_DAYS:
            if explicit_from:
                d_to = _add_days(d_from, MAX_DAYS)
            else:
                d_from = d_to - timedelta(days=MAX_DAYS)

        periods: list[date] = []
        if d_from and d_to:
            p = _period_start(d_from, group)
            limit = MAX_PERIODS[group]
            while p <= d_to:
                if len(periods) >= limit:
                    raise ServiceError(422, f"The range may span at most {MAX_DAYS} days")
                periods.append(p)
                try:
                    p = _next_period(p, group)
                except (OverflowError, ValueError):  # the last representable period (year 9999)
                    break
        index = {p.isoformat(): i for i, p in enumerate(periods)}

        # per person: [total, billable, unbilled] minutes and minutes per period
        sums: dict[str | None, list[int]] = defaultdict(lambda: [0, 0, 0])
        per: dict[str | None, list[int]] = defaultdict(lambda: [0] * len(periods))
        for e in entries:
            uid = e.get("user_id") or None
            try:
                d = date.fromisoformat(e["date"])
            except ValueError:
                continue
            if not (d_from and d_to and d_from <= d <= d_to):
                continue
            m = int(e.get("minutes") or 0)
            i = index[_period_start(d, group).isoformat()]
            s = sums[uid]
            s[0] += m
            if e.get("billable", True):
                s[1] += m
                if not e.get("invoice_id"):
                    s[2] += m
            per[uid][i] += m

        names = {u["id"]: u.get("name") or u.get("username") or u["id"] for u in self.store.list("users")}

        def row(uid: str | None) -> dict:
            s = sums[uid]
            return {
                "user_id": uid,
                "name": names.get(uid, DELETED_USER) if uid else DELETED_USER,
                "hours": _hours(s[0]),
                "billable_hours": _hours(s[1]),
                "unbilled_hours": _hours(s[2]),
                "by_period": [_hours(m) for m in per[uid]],
            }

        people = sorted((row(uid) for uid in sums), key=lambda r: (r["name"].lower(), r["user_id"] or ""))
        tot = [sum(s[k] for s in sums.values()) for k in range(3)]
        tot_per = [sum(m[i] for m in per.values()) for i in range(len(periods))]
        return {
            "project_id": project_id,
            "name": project.get("name"),
            "from": d_from.isoformat() if d_from else None,
            "to": d_to.isoformat() if d_to else None,
            "group": group,
            "periods": [p.isoformat() for p in periods],
            "people": people,
            "total": {
                "hours": _hours(tot[0]),
                "billable_hours": _hours(tot[1]),
                "unbilled_hours": _hours(tot[2]),
                "by_period": [_hours(m) for m in tot_per],
            },
        }
