"""Business-trip figures: meal allowance per day, mileage per trip (docs/specs/business-trips.md, "Calculation").

Pure functions, no storage: the service feeds a trip dict and a rate-table lookup per year. Money is exact
(:mod:`permita38.core.money`), every amount is rounded half-up to cents, and the mileage total is rounded once per trip.

Decisions (architect, final): an ``end`` of exactly 00:00 on a later date is read as 24:00 of the previous day, before
the days are classified. Absence is elapsed time in Europe/Berlin (a DST change day has 23 or 25 hours). The
three-month limit is per trip, keyed on the original country code and the normalised city; the last paid day is
start + 3 months - 1 day, or the last day of the end month when the start day does not exist there.
"""

import calendar
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .money import ZERO, dec, q2, to_float

RatesFor = Callable[[int], dict[str, Any]]

BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc
HOURS_THRESHOLD_SECONDS = 8 * 3600  # one-day trip: strictly more than 8 hours
BREAKFAST_SHARE = Decimal("0.20")
LUNCH_DINNER_SHARE = Decimal("0.40")
MEALS = (("breakfast", BREAKFAST_SHARE), ("lunch", LUNCH_DINNER_SHARE), ("dinner", LUNCH_DINNER_SHARE))
LIMIT_MONTHS = 3
RESET_DAYS = 28
DEFAULT_COUNTRY = "DE"

NOTE_FALLBACK = "Country not in the rate table: Luxembourg rate used"
NOTE_LIMIT = "Three-month limit reached: no allowance"
NOTE_NO_ABSENCE = "No absence on this day: no allowance"
NOTE_UNDER_8H = "Absence not more than 8 hours: no allowance"


def parse_local(value: str) -> datetime:
    """``YYYY-MM-DDTHH:MM`` (local time, no zone) -> naive datetime."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


def effective_last(start: datetime, end: datetime) -> date:
    """The last calendar day of the trip: an end of exactly 00:00 on a later date belongs to the day before."""
    last = end.date()
    if end.time() == time(0) and last > start.date():
        last -= timedelta(days=1)
    return last


def last_paid_day(start: date) -> date:
    """Last day the allowance is paid at a place whose period started on ``start`` (three months, per trip).

    start + 3 months - 1 day; if the start day does not exist in the end month, the last day of that month.
    Jan 1 -> Mar 31, Jan 31 -> Apr 30, Nov 30 -> Feb 28, Aug 31 -> Nov 30.
    """
    idx = start.month - 1 + LIMIT_MONTHS
    year, month = start.year + idx // 12, idx % 12 + 1
    last_of_month = calendar.monthrange(year, month)[1]
    if start.day > last_of_month:
        return date(year, month, last_of_month)
    return date(year, month, start.day) - timedelta(days=1)


def _utc(naive: datetime) -> datetime:
    """Local Berlin wall time -> aware UTC instant. Differences of aware UTC values are elapsed time."""
    return naive.replace(tzinfo=BERLIN).astimezone(UTC)


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


def _limit_key(code: str, place: dict | None) -> tuple[str, str]:
    city = (place.get("city") or "") if place else ""
    return code, " ".join(city.split()).casefold()


def compute(trip: dict[str, Any], rates_for: RatesFor) -> dict[str, Any]:
    """Per-day allowance breakdown, allowance and mileage totals of a trip.

    ``trip`` needs ``start``, ``end`` (``YYYY-MM-DDTHH:MM``), and optionally ``places`` (``from_date``, ``country``,
    ``city``), ``meals_provided`` (``date``, ``breakfast``, ``lunch``, ``dinner``) and ``mileage`` (``km``).
    ``rates_for(year)`` returns the rate table of that year (see :mod:`permita38.core.trip_rates`).
    """
    start_n, end_n = parse_local(trip["start"]), parse_local(trip["end"])
    if end_n <= start_n:
        raise ValueError("end must be after start")
    first, last = start_n.date(), effective_last(start_n, end_n)
    one_day = first == last
    start_u, end_u = _utc(start_n), _utc(end_n)
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
        lo = max(start_u, _utc(datetime.combine(day, time(0))))
        hi = min(end_u, _utc(datetime.combine(day + timedelta(days=1), time(0))))
        seconds = max(int((hi - lo).total_seconds()), 0)
        place = _place_for(day, places, one_day)
        notes: list[str] = []

        rates = table(day.year)
        used_versions.add(rates["rules_version"])
        orig_code = place["country"] if place else DEFAULT_COUNTRY
        code = orig_code
        if code == DEFAULT_COUNTRY:
            row = {"from8h": rates["domestic"]["from8h"], "h24": rates["domestic"]["h24"], "placeholder": False}
        elif code in rates["countries"]:
            row = rates["countries"][code]
        else:
            code = rates["fallback_country"]  # the row that was used
            row = rates["countries"][code]
            notes.append(NOTE_FALLBACK)
        if row.get("placeholder"):
            notes.append(f"Rate for {code} is a placeholder: verify")

        # base amount (absence is elapsed time; "more than 8 hours" is strict)
        if seconds == 0:
            base = ZERO
            notes.append(NOTE_NO_ABSENCE)
        elif one_day:
            base = row["from8h"] if seconds > HOURS_THRESHOLD_SECONDS else ZERO
            if seconds <= HOURS_THRESHOLD_SECONDS:
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

        # three-month limit: per trip, per original country and normalised city; four weeks away starts a new period
        key = _limit_key(orig_code, place)
        state = limit_state.get(key)
        if state is None or (day - state["last"]).days - 1 >= RESET_DAYS:
            state = {"start": day, "last": day}
            limit_state[key] = state
        state["last"] = day
        if day > last_paid_day(state["start"]):
            amount = ZERO
            notes.append(NOTE_LIMIT)

        allowance += amount
        entry = {
            "date": day.isoformat(),
            "country": code,
            "absence_hours": to_float(q2(Decimal(seconds) / 3600)),
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
    rate_per_km = table(first.year)["mileage_per_km"]  # booked at the trip start, with the start year's rate
    mileage_total = q2(km_total * rate_per_km)
    return {
        "days": days,
        "allowance_total": to_float(allowance),
        "mileage_km": to_float(q2(km_total)),
        "mileage_rate": to_float(rate_per_km),
        "mileage_total": to_float(mileage_total),
        "rules_version": ", ".join(sorted(used_versions)),
    }
