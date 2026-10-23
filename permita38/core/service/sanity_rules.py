"""Plausibility rules ("soft warnings"): suspicious but possible input.

Pure functions: they receive the record and the few related records they need and return a list of warnings in the
format shared with the planner (:func:`permita38.core.service.planning_warnings.warning`). They never touch the store
and never block a save. Hard limits (impossible values) live in :mod:`permita38.core.validation`.

Working-time rules follow the German Arbeitszeitgesetz (ArbZG) and are plausibility aids, not legal advice. For an
employee they are ``warning``; for the boss (self-employed, not covered by the act) the same finding is ``info``.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from permita38.core import identifiers
from permita38.core.money import ZERO, dec, line_amount, q2, vat_amount
from permita38.core.validation import ALLOWED_VAT_RATES

from .planning_warnings import warning

DAY = 1440

#: thresholds (minutes unless noted); the legal ones are fixed, the others are conservative defaults
MAX_DAY_MINUTES = 10 * 60  # §3 ArbZG: 8 h, extendable to 10 h
BREAK_AFTER_6H = 30  # §4 ArbZG: more than 6 h of work
BREAK_AFTER_9H = 45  # §4 ArbZG: more than 9 h of work
MIN_BREAK_PART = 15  # a pause counts as a break from 15 minutes on
MIN_REST = 11 * 60  # §5 ArbZG
FUTURE_DOCUMENT_DAYS = 31
OLD_DAYS = 366
OUTLIER_FACTOR = 20
OUTLIER_MIN_SAMPLES = 5
OUTLIER_MIN_AMOUNT = Decimal(500)
VAT_TOLERANCE = Decimal("0.01")

#: a bill may be dated a few days ahead (post-dated, time zones)
EXPENSE_FUTURE_DAYS = 3


def _w(code: str, severity: str, field: str | None, message: str, details: dict | None = None) -> dict:
    return warning(code, severity, field, message, details, confirmable=False)  # advisory: there is nothing to confirm


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _clock(value: Any) -> int | None:
    if isinstance(value, str) and len(value) == 5 and value[2] == ":" and value[:2].isdigit() and value[3:].isdigit():
        h, m = int(value[:2]), int(value[3:])
        if h < 24 and m < 60:
            return h * 60 + m
    return None


def _changed(old: dict | None, rec: dict, key: str) -> bool:
    return old is None or old.get(key) != rec.get(key)


def _minutes(e: dict) -> int:
    try:
        return max(0, int(e.get("minutes") or 0))
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------------------------------------
def _date_age(
    code: str, what: str, value: str | None, today: date, future_days: int, past_days: int = OLD_DAYS
) -> list[dict]:
    d = _date(value)
    if d is None:
        return []
    if d > today + timedelta(days=future_days):
        return [
            _w(
                f"{code}.far_future",
                "warning",
                "date",
                f"{what} {d.isoformat()} is {(d - today).days} days in the future.",
            )
        ]
    if d < today - timedelta(days=past_days):
        return [_w(f"{code}.far_past", "warning", "date", f"{what} {d.isoformat()} is {(today - d).days} days ago.")]
    return []


# --------------------------------------------------------------------------------------------------------
# time entries (ArbZG)
# --------------------------------------------------------------------------------------------------------
def _span(e: dict) -> tuple[int, int] | None:
    """(start, end) in minutes since midnight of the entry's own day; a night shift ends after 1440."""
    s, t = _clock(e.get("start")), _clock(e.get("end"))
    if s is None or t is None or s == t:
        return None
    return (s, t if t > s else t + DAY)


def break_analysis(spans: list[tuple[int, int]]) -> tuple[int, int, int]:
    """(work minutes, break minutes, longest block) of one day. Gaps of at least 15 minutes are breaks; shorter gaps
    and overlaps count as continuous work."""
    work = breaks = longest = 0
    blocks: list[list[int]] = []
    for a, b in sorted(spans):
        if blocks and a - blocks[-1][1] < MIN_BREAK_PART:
            blocks[-1][1] = max(blocks[-1][1], b)
        else:
            if blocks:
                breaks += a - blocks[-1][1]
            blocks.append([a, b])
    for a, b in blocks:
        work += b - a
        longest = max(longest, b - a)
    return work, breaks, longest


def check_entry(
    rec: dict,
    *,
    same_day: list[dict],
    prev_day: list[dict],
    next_day: list[dict],
    employee: bool,
    today: date,
    holiday: str | None = None,
    old: dict | None = None,
) -> list[dict]:
    """``same_day``/``prev_day``/``next_day`` are the *other* entries of the same person."""
    out: list[dict] = []
    day = _date(rec.get("date"))
    sev = "warning" if employee else "info"
    if day is None:
        return out
    if _changed(old, rec, "date"):
        if day > today:
            out.append(_w("entry.date_future", "info", "date", f"{day.isoformat()} is in the future."))
        elif day < today - timedelta(days=OLD_DAYS):
            out.append(_w("entry.date_old", "info", "date", f"{day.isoformat()} is {(today - day).days} days ago."))
        if employee and (holiday or day.weekday() == 6):
            what = f"a public holiday ({holiday})" if holiday else "a Sunday"
            out.append(
                _w(
                    "time.sunday_holiday",
                    "info",
                    "date",
                    f"{day.isoformat()} is {what}; work is restricted (§9 ArbZG).",
                )
            )

    total = _minutes(rec) + sum(_minutes(e) for e in same_day)
    if total > MAX_DAY_MINUTES:
        out.append(
            _w(
                "time.day_over_10h",
                sev,
                "minutes",
                f"{total / 60:.1f} hours on {day.isoformat()}; the maximum is 10 hours (§3 ArbZG).",
                {"hours": round(total / 60, 2)},
            )
        )

    span = _span(rec)
    if span and _minutes(rec) > span[1] - span[0] + 1:
        out.append(
            _w(
                "entry.minutes_exceed_span",
                "warning",
                "minutes",
                f"{_minutes(rec)} minutes do not fit between {rec.get('start')} and {rec.get('end')}.",
            )
        )

    spans = [_span(e) for e in [rec, *same_day]]
    if span and all(spans):
        work, breaks, longest = break_analysis([s for s in spans if s])
        # a pause already deducted from the minutes of an entry with clock times counts as break time
        deducted = sum(max(0, sp[1] - sp[0] - _minutes(e)) for sp, e in zip(spans, [rec, *same_day]) if sp)
        breaks += deducted
        need = BREAK_AFTER_9H if work > 9 * 60 else BREAK_AFTER_6H if work > 6 * 60 else 0
        if need and breaks < need:
            out.append(
                _w(
                    "time.break_missing", sev, "end",
                    f"{work / 60:.1f} hours of work with {breaks} minutes of break; {need} are required (§4 ArbZG).",
                    {"work_minutes": work, "break_minutes": breaks, "required_minutes": need},
                )
            )  # fmt: skip
        elif longest > 6 * 60 and deducted < MIN_BREAK_PART:
            out.append(
                _w(
                    "time.break_missing",
                    sev,
                    "end",
                    f"{longest / 60:.1f} hours in a row without a break of 15 minutes (§4 ArbZG).",
                    {"work_minutes": work, "break_minutes": breaks},
                )
            )

    # rest: first start of this day against the last end of the day before, last end against the next day's first start
    mine = [s for s in spans if s]
    if mine:
        prev_ends = [s[1] - DAY for e in prev_day if (s := _span(e))]
        next_starts = [s[0] + DAY for e in next_day if (s := _span(e))]
        first, last = min(a for a, _ in mine), max(b for _, b in mine)
        for gap, other_day in (
            (first - max(prev_ends) if prev_ends else None, day - timedelta(days=1)),
            (min(next_starts) - last if next_starts else None, day + timedelta(days=1)),
        ):
            if gap is not None and gap < MIN_REST:
                out.append(
                    _w(
                        "time.rest_short", sev, "start",
                        f"Only {max(gap, 0) / 60:.1f} hours of rest between {day.isoformat()} and {other_day.isoformat()}; 11 are required (§5 ArbZG).",
                        {"rest_minutes": gap, "other_day": other_day.isoformat()},
                    )
                )  # fmt: skip
                break
    return out


# --------------------------------------------------------------------------------------------------------
# offers and invoices
# --------------------------------------------------------------------------------------------------------
def net_of(doc: dict) -> Decimal:
    """Exact net total from qty x price (never from a stored, possibly stale ``amount``)."""
    lines = doc.get("lines")
    return (
        sum((line_amount(ln.get("qty"), ln.get("price")) for ln in lines if isinstance(ln, dict)), ZERO)
        if isinstance(lines, list)
        else ZERO
    )


def _outlier(amount: Decimal, history: list[Decimal]) -> Decimal | None:
    """The median of ``history`` when ``amount`` is far above it, else None."""
    hist = [h for h in history if h > 0]
    if amount < OUTLIER_MIN_AMOUNT or len(hist) < OUTLIER_MIN_SAMPLES:
        return None
    med = statistics.median(hist)
    return med if med > 0 and amount > OUTLIER_FACTOR * med else None


def check_document(
    coll: str,
    rec: dict,
    *,
    old: dict | None,
    settings: dict,
    others: list[dict],
    today: date,
    finalize: bool = False,
) -> list[dict]:
    out: list[dict] = []
    kind = "invoice" if coll == "invoices" else "offer"
    d = _date(rec.get("date"))
    if _changed(old, rec, "date"):
        out += _date_age(f"{kind}.date", f"The {kind} date", rec.get("date"), today, FUTURE_DOCUMENT_DAYS)
    for key, label, later in (
        ("due_date", "due date", "invoice"),
        ("paid_date", "payment date", "invoice"),
        ("valid_until", "validity", "offer"),
    ):
        other = _date(rec.get(key))
        if d and other and other < d and kind == later:
            out.append(
                _w(
                    f"{kind}.{key}_before_date",
                    "warning",
                    key,
                    f"The {label} {other.isoformat()} is before the {kind} date {d.isoformat()}.",
                )
            )
    sf = _date(rec.get("service_from"))
    if kind == "invoice" and d and sf and sf > d:
        out.append(
            _w(
                "invoice.service_after_date",
                "info",
                "service_from",
                f"The service starts on {sf.isoformat()}, after the invoice date; that is an advance invoice.",
            )
        )

    lines = [ln for ln in rec.get("lines") or [] if isinstance(ln, dict)]
    net = net_of(rec)
    if lines:
        if net == 0:
            out.append(_w(f"{kind}.total_zero", "warning", "lines", f"The {kind} total is 0.00."))
        elif net < 0 and kind == "offer":
            out.append(_w("offer.total_negative", "warning", "lines", "The offer total is negative."))
        elif net < 0:
            out.append(_w("invoice.total_negative", "info", "lines", "The invoice total is negative (a credit note)."))
        for i, ln in enumerate(lines, 1):
            if dec(ln.get("qty")) != 0 and dec(ln.get("price")) == 0 and net != 0:
                out.append(_w(f"{kind}.line_free", "info", "lines", f"Line {i} has a quantity but no price."))
        klein = bool(settings.get("kleinunternehmer"))
        default_vat = dec(
            rec.get("vat_rate") if rec.get("vat_rate") not in (None, "") else settings.get("vat_rate", 19)
        )
        for i, ln in enumerate(lines, 1):
            raw = ln.get("vat")
            if klein and dec(raw) > 0:
                out.append(
                    _w(
                        f"{kind}.vat_ignored",
                        "info",
                        "lines",
                        f"Line {i} has VAT, but as a small business (§19 UStG) no VAT is charged.",
                    )
                )
            elif not klein and default_vat > 0 and raw not in (None, "") and dec(raw) == 0:
                out.append(
                    _w(
                        f"{kind}.vat_zero_line",
                        "info",
                        "lines",
                        f"Line {i} has 0 % VAT; tax-exempt or reverse-charge services need a note on the document.",
                    )
                )

    same_kind = [o for o in others if o.get("id") != rec.get("id")]
    if net > 0 and rec.get("customer_id"):
        for o in same_kind:
            if o.get("customer_id") == rec["customer_id"] and o.get("date") == rec.get("date") and net_of(o) == net:
                out.append(
                    _w(
                        f"{kind}.possible_duplicate",
                        "warning",
                        None,
                        f"Another {kind} for this customer, with the same date and total, exists ({o.get('number') or 'draft'}).",
                        {"other_id": o.get("id")},
                    )
                )
                break
    med = _outlier(net, [net_of(o) for o in same_kind])
    if med is not None and (_changed(old, rec, "lines") or old is None):
        out.append(
            _w(
                f"{kind}.total_outlier",
                "warning",
                "lines",
                f"{net:,.2f} is more than {OUTLIER_FACTOR} times your usual {kind} ({med:,.2f}). A typo?",
                {"median": float(med)},
            )
        )

    if (
        finalize
        and kind == "invoice"
        and not (str(settings.get("tax_number") or "").strip() or str(settings.get("vat_id") or "").strip())
    ):
        out.append(
            _w(
                "invoice.seller_tax_id_missing",
                "warning",
                None,
                "An invoice must show your tax number or VAT id (§14 UStG). Enter it in Settings.",
            )
        )
    return out


# --------------------------------------------------------------------------------------------------------
# expenses
# --------------------------------------------------------------------------------------------------------
def check_expense(rec: dict, *, old: dict | None, settings: dict, others: list[dict], today: date) -> list[dict]:
    out: list[dict] = []
    if _changed(old, rec, "date"):
        out += _date_age("expense.date", "The expense date", rec.get("date"), today, EXPENSE_FUTURE_DAYS)
    net, vat = dec(rec.get("net")), dec(rec.get("vat"))
    if net > 0 and vat > net:
        out.append(
            _w(
                "expense.vat_exceeds_net",
                "warning",
                "vat",
                f"The VAT {vat:,.2f} is larger than the net amount {net:,.2f}.",
            )
        )
    rate = rec.get("vat_rate")
    if rate not in (None, "") and net > 0:
        expected = vat_amount(net, rate)
        if abs(vat - expected) > VAT_TOLERANCE:
            out.append(
                _w(
                    "expense.vat_mismatch",
                    "warning",
                    "vat",
                    f"{dec(rate):g} % of {net:,.2f} is {expected:,.2f}, but the VAT is {vat:,.2f}.",
                    {"expected": float(expected), "entered": float(vat)},
                )
            )
    elif net >= 10 and vat > 0 and vat <= net:
        allowed = {Decimal(r) for r in ALLOWED_VAT_RATES} | {dec(settings.get("vat_rate", 19))}
        implied = vat / net * 100
        if min(abs(implied - r) for r in allowed) > Decimal("0.5"):
            out.append(
                _w(
                    "expense.vat_rate_implausible",
                    "info",
                    "vat",
                    f"VAT {vat:,.2f} on {net:,.2f} is {implied:.1f} %, which is not a usual rate.",
                )
            )

    gross = q2(net + vat)
    cat = rec.get("category")
    if cat:
        hist = [
            q2(dec(o.get("net")) + dec(o.get("vat")))
            for o in others
            if o.get("category") == cat and o.get("id") != rec.get("id")
        ]
        med = _outlier(gross, hist)
        if med is not None and (old is None or _changed(old, rec, "net") or _changed(old, rec, "vat")):
            out.append(
                _w(
                    "expense.amount_outlier",
                    "warning",
                    "net",
                    f"{gross:,.2f} is more than {OUTLIER_FACTOR} times your usual {med:,.2f} for '{cat}'. An extra zero?",
                    {"median": float(med)},
                )
            )
    vendor = str(rec.get("vendor") or "").strip().lower()
    if vendor and net > 0:
        for o in others:
            if (
                o.get("id") != rec.get("id")
                and str(o.get("vendor") or "").strip().lower() == vendor
                and o.get("date") == rec.get("date")
                and dec(o.get("net")) == net
            ):
                out.append(
                    _w(
                        "expense.possible_duplicate",
                        "warning",
                        None,
                        "An expense from the same vendor, date and amount already exists.",
                        {"other_id": o.get("id")},
                    )
                )
                break
    return out


# --------------------------------------------------------------------------------------------------------
# customers and the company's own data
# --------------------------------------------------------------------------------------------------------
def check_identifiers(rec: dict, *, owner: str) -> list[dict]:
    out: list[dict] = []
    for key, label, fn in (
        ("vat_id", "VAT id (USt-IdNr)", identifiers.vat_id_error),
        ("tax_number", "tax number (Steuernummer)", identifiers.tax_number_error),
        ("iban", "IBAN", identifiers.iban_error),
        ("bic", "BIC", identifiers.bic_error),
    ):
        value = rec.get(key)
        if isinstance(value, str) and value.strip():
            err = fn(value)
            if err:
                out.append(_w(f"{owner}.{key}_invalid", "warning", key, f"The {label} looks wrong: {err}."))
            elif key == "vat_id" and (hint := identifiers.vat_id_hint(value)):
                out.append(_w(f"{owner}.vat_id_prefix", "info", key, hint))
    return out
