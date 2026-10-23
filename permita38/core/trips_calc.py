"""Business-trip figures: meal allowance per day, mileage per trip (docs/specs/business-trips.md, "Calculation").

Pure functions, no storage: the service feeds a trip dict and a rate-table lookup per year. Money is exact
(:mod:`permita38.core.money`), every amount is rounded half-up to cents, and the mileage total is rounded once per trip.

Decisions where the spec is silent (listed in the report): a multi-day day with no absence (the trip ends exactly at
midnight) gets 0; a trip without ``places`` is domestic (DE); the four-week reset means an interruption of 28 days or
more (the days in between, not the calendar difference).
"""

import calendar
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from .money import ZERO, dec, q2, to_float

RatesFor = Callable[[int], dict[str, Any]]

HOURS_THRESHOLD_MINUTES = 8 * 60  # one-day trip: strictly more than 8 hours
BREAKFAST_SHARE = Decimal("0.20")
LUNCH_DINNER_SHARE = Decimal("0.40")
LIMIT_MONTHS = 3
RESET_DAYS = 28
DEFAULT_COUNTRY = "DE"

NOTE_FALLBACK = "Country not in the rate table: Luxembourg rate used"
NOTE_LIMIT = "Three-month limit reached: no allowance"
NOTE_NO_ABSENCE = "No absence on this day: no allowance"
NOTE_UNDER_8H = "Absence not more than 8 hours: no allowance"
MEALS = (("breakfast", BREAKFAST_SHARE), ("lunch", LUNCH_DINNER_SHARE), ("dinner", LUNCH_DINNER_SHARE))


def parse_local(value: str) -> datetime:
    """``YYYY-MM-DDTHH:MM`` (local time, no zone) -> naive datetime."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


def add_months(day: date, months: int) -> date:
    """Calendar months later; a day that does not exist in the target month is clamped to its last day."""
    idx = day.month - 1 + months
    year, month = day.year + idx // 12, idx % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _place_for(day: date, places: list[dict], one_day: bool) -> dict | None:
    """The destination of a day: the last entry of a one-day trip, else the entry with the greatest ``from_date``
    that is on or before the day (ties: the later entry); before the first entry, the first entry."""
    if not places:
        return None
    if one_day:
        return places[-1]
    best, best_day = None, None
    for p in places:
        since = date.fromisoformat(p["from_date"])
        if since <= day and (best_day is None or since >= best_day):
            best, best_day = p, since
    return best if best is not None else places[0]


def compute(trip: dict[str, Any], rates_for: RatesFor) -> dict[str, Any]:
    """Per-day allowance breakdown, allowance and mileage totals of a trip.

    ``trip`` needs ``start``, ``end`` (``YYYY-MM-DDTHH:MM``), and optionally ``places`` (``from_date``, ``country``,
    ``city``), ``meals_provided`` (``date``, ``breakfast``, ``lunch``, ``dinner``) and ``mileage`` (``km``).
    ``rates_for(year)`` returns the rate table of that year (see :mod:`permita38.core.trip_rates`).
    """
    start, end = parse_local(trip["start"]), parse_local(trip["end"])
    if end <= start:
        raise ValueError("end must be after start")
    first, last = start.date(), end.date()
    one_day = first == last
    places = list(trip.get("places") or [])
    meals = {m["date"]: m for m in trip.get("meals_provided") or []}
    tables: dict[int, dict[str, Any]] = {}

    def table(year: int) -> dict[str, Any]:
        if year not in tables:
            tables[year] = rates_for(year)
        return tables[year]

    limit_state: dict[tuple[str, str], dict[str, date]] = {}
    days: list[dict[str, Any]] = []
    allowance = ZERO
    used_versions: set[str] = {table(first.year)["rules_version"]}
    day = first
    while day <= last:
        day_start = max(start, datetime.combine(day, time(0)))
        day_end = min(end, datetime.combine(day + timedelta(days=1), time(0)))
        minutes = max(int((day_end - day_start).total_seconds() // 60), 0)
        place = _place_for(day, places, one_day)
        notes: list[str] = []

        rates = table(day.year)
        used_versions.add(rates["rules_version"])
        code = place["country"] if place else DEFAULT_COUNTRY
        if code == DEFAULT_COUNTRY:
            row = {"from8h": rates["domestic"]["from8h"], "h24": rates["domestic"]["h24"]}
        elif code in rates["countries"]:
            row = rates["countries"][code]
        else:
            code = rates["fallback_country"]  # the row that was used
            row = rates["countries"][code]
            notes.append(NOTE_FALLBACK)

        # base amount
        if minutes == 0:
            base = ZERO
            notes.append(NOTE_NO_ABSENCE)
        elif one_day:
            base = row["from8h"] if minutes > HOURS_THRESHOLD_MINUTES else ZERO
            if minutes <= HOURS_THRESHOLD_MINUTES:
                notes.append(NOTE_UNDER_8H)
        elif day in (first, last):
            base = row["from8h"]
        else:
            base = row["h24"]

        # meals provided: 20 % (breakfast) and 40 % (lunch, dinner) of the 24-hour rate, each rounded to cents
        meal = meals.get(day.isoformat(), {})
        reduction_rows = []
        reductions = ZERO
        for name, share in MEALS:
            if meal.get(name):
                cut = q2(row["h24"] * share)
                reductions += cut
                reduction_rows.append({"meal": name, "amount": to_float(cut)})
        amount = max(base - reductions, ZERO)

        # three-month limit per place of activity; an interruption of four weeks or more starts a new period
        if place is not None:
            key = (code, place.get("city") or "")
            state = limit_state.get(key)
            if state is None or (day - state["last"]).days - 1 >= RESET_DAYS:
                state = {"start": day, "last": day}
                limit_state[key] = state
            state["last"] = day
            if day >= add_months(state["start"], LIMIT_MONTHS):
                amount = ZERO
                notes.append(NOTE_LIMIT)

        allowance += amount
        entry = {
            "date": day.isoformat(),
            "country": code,
            "absence_hours": to_float(q2(Decimal(minutes) / 60)),
            "base": to_float(base),
            "reductions": reduction_rows,
            "amount": to_float(amount),
            "note": "; ".join(notes),
        }
        if place is not None and place.get("city"):
            entry["city"] = place["city"]
        days.append(entry)
        day += timedelta(days=1)

    mileage = list(trip.get("mileage") or [])
    km_total = sum((dec(m.get("km")) for m in mileage), ZERO)
    rate_per_km = table(first.year)["mileage_per_km"]
    mileage_total = q2(km_total * rate_per_km)
    return {
        "days": days,
        "allowance_total": to_float(allowance),
        "mileage_km": to_float(q2(km_total)),
        "mileage_rate": to_float(rate_per_km),
        "mileage_total": to_float(mileage_total),
        "rules_version": ", ".join(sorted(used_versions)),
    }
