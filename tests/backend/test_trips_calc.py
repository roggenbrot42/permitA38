"""Business trips: the calculation oracle (docs/specs/business-trips.md, "Calculation").

Every expected amount is written out by hand below (German rules, 2026 domestic values 14 / 28 EUR, mileage 0,30 EUR,
country values from the placeholder table in permita38/core/trip_rates/2026.json). Nothing here calls the code under
test to compute an expectation.
"""

from datetime import date
from decimal import Decimal

import pytest

from permita38.core import trips_calc
from permita38.core.trip_rates import load_rates

RATES_2026 = load_rates(2026)


def rates(year):
    """The real 2026 table; any other year is a synthetic table so that a trip can cross a year end."""
    if year == 2026:
        return RATES_2026
    return SYNTHETIC_2027


SYNTHETIC_2027 = {
    "year": 2027,
    "rules_version": "2027-SYNTH",
    "mileage_per_km": Decimal("0.30"),
    "fallback_country": "LU",
    "domestic": {"from8h": Decimal("15.00"), "h24": Decimal("30.00")},
    "countries": {
        "DE": {"name": "Germany", "from8h": Decimal("15.00"), "h24": Decimal("30.00")},
        "LU": {"name": "Luxembourg", "from8h": Decimal("31.00"), "h24": Decimal("46.00")},
    },
}


def trip(start, end, places=None, meals=None, mileage=None):
    out = {"start": start, "end": end}
    if places is not None:
        out["places"] = places
    if meals is not None:
        out["meals_provided"] = meals
    if mileage is not None:
        out["mileage"] = mileage
    return out


def day(result, iso):
    return next(d for d in result["days"] if d["date"] == iso)


def red(d):
    """Sum of the reduction rows of a day (each row is {meal, amount})."""
    return round(sum(x["amount"] for x in d["reductions"]), 2)


def calc(t):
    return trips_calc.compute(t, rates)


# ------------------------------------------------------------------ worked examples (exact cents)
def test_exactly_eight_hours_is_no_allowance():
    r = calc(trip("2026-11-09T07:00", "2026-11-09T15:00"))
    assert r["allowance_total"] == 0.0
    assert day(r, "2026-11-09")["absence_hours"] == 8.0
    assert day(r, "2026-11-09")["base"] == 0.0


def test_eight_hours_and_one_minute_is_fourteen_euro():
    r = calc(trip("2026-11-09T07:00", "2026-11-09T15:01"))
    assert r["allowance_total"] == 14.00
    assert day(r, "2026-11-09")["amount"] == 14.00


def test_two_days_with_overnight_stay_is_fourteen_plus_fourteen():
    r = calc(trip("2026-11-09T09:00", "2026-11-10T12:00"))
    assert [d["amount"] for d in r["days"]] == [14.00, 14.00]
    assert r["allowance_total"] == 28.00


def test_three_days_first_and_last_fourteen_middle_full_day():
    r = calc(trip("2026-11-09T08:00", "2026-11-11T18:00"))
    assert [d["base"] for d in r["days"]] == [14.00, 28.00, 14.00]
    assert r["allowance_total"] == 56.00


def test_breakfast_on_middle_day_is_twenty_percent_of_full_day():
    meals = [{"date": "2026-11-10", "breakfast": True, "lunch": False, "dinner": False}]
    r = calc(trip("2026-11-09T08:00", "2026-11-11T18:00", meals=meals))
    assert red(day(r, "2026-11-10")) == 5.60
    assert day(r, "2026-11-10")["amount"] == 22.40
    assert r["allowance_total"] == 50.40


def test_nine_hours_with_lunch_and_dinner_never_goes_below_zero():
    meals = [{"date": "2026-11-09", "breakfast": False, "lunch": True, "dinner": True}]
    r = calc(trip("2026-11-09T09:00", "2026-11-09T18:00", meals=meals))
    d = day(r, "2026-11-09")
    assert d["base"] == 14.00
    assert red(d) == 22.40  # 11,20 + 11,20
    assert [x["meal"] for x in d["reductions"]] == ["lunch", "dinner"]
    assert d["amount"] == 0.00
    assert r["allowance_total"] == 0.00


def test_nine_hours_with_lunch_only_leaves_two_euro_eighty():
    meals = [{"date": "2026-11-09", "breakfast": False, "lunch": True, "dinner": False}]
    r = calc(trip("2026-11-09T09:00", "2026-11-09T18:00", meals=meals))
    assert r["allowance_total"] == 2.80


def test_mileage_237_5_km_is_71_25_euro():
    r = calc(
        trip(
            "2026-11-09T08:00",
            "2026-11-09T18:00",
            mileage=[{"date": "2026-11-09", "from": "A", "to": "B", "km": 237.5}],
        )
    )
    assert r["mileage_km"] == 237.5
    assert r["mileage_total"] == 71.25


def test_mileage_is_rounded_once_per_trip_not_per_row():
    # synthetic rate 0,333 EUR/km (not a real value; it makes the rounding visible). Three rows of 0,1 km:
    # once per trip: 0,3 km x 0,333 = 0,0999 -> 0,10 ; per row would be 3 x round(0,0333) = 0,09
    odd = dict(RATES_2026, mileage_per_km=Decimal("0.333"))
    rows = [{"date": "2026-11-09", "from": "A", "to": "B", "km": 0.1}] * 3
    r = trips_calc.compute(trip("2026-11-09T08:00", "2026-11-09T18:00", mileage=rows), lambda y: odd)
    assert r["mileage_total"] == 0.10


def test_unknown_country_uses_luxembourg_row_with_note():
    places = [{"from_date": "2026-11-09", "country": "XX"}]
    r = calc(trip("2026-11-09T08:00", "2026-11-11T18:00", places=places))
    lu = RATES_2026["countries"]["LU"]  # placeholder values: 30,00 / 45,00
    assert [d["base"] for d in r["days"]] == [float(lu["from8h"]), float(lu["h24"]), float(lu["from8h"])]
    assert r["allowance_total"] == 30.00 + 45.00 + 30.00 == 105.00
    assert all("Country not in the rate table" in d["note"] for d in r["days"])
    assert all(d["country"] == "LU" for d in r["days"])  # the row that was used


def test_known_foreign_country_three_days_uses_its_row():
    places = [{"from_date": "2026-11-09", "country": "CH", "city": "Zurich"}]
    r = calc(trip("2026-11-09T08:00", "2026-11-11T18:00", places=places))
    assert r["allowance_total"] == 40.00 + 65.00 + 40.00 == 145.00
    assert day(r, "2026-11-09")["note"] == "Rate for CH is a placeholder: verify"
    assert day(r, "2026-11-09")["city"] == "Zurich"


def test_foreign_reductions_are_twenty_and_forty_percent_of_its_full_day_rate():
    places = [{"from_date": "2026-11-09", "country": "CH"}]
    meals = [{"date": "2026-11-10", "breakfast": True, "lunch": True, "dinner": False}]
    r = calc(trip("2026-11-09T08:00", "2026-11-11T18:00", places=places, meals=meals))
    # CH full day 65,00: breakfast 13,00 and lunch 26,00 -> 65 - 39 = 26
    assert red(day(r, "2026-11-10")) == 39.00
    assert day(r, "2026-11-10")["amount"] == 26.00
    assert r["allowance_total"] == 40.00 + 26.00 + 40.00 == 106.00


def test_no_places_means_domestic():
    r = calc(trip("2026-11-09T08:00", "2026-11-09T18:00"))
    assert day(r, "2026-11-09")["country"] == "DE"
    assert r["allowance_total"] == 14.00


# ------------------------------------------------------------------ boundaries
def test_trip_over_midnight_counts_both_days_as_arrival_and_departure():
    # 22:00 to 02:00: two calendar days of two hours each; a multi-day trip pays 14 on each of its first and last date
    r = calc(trip("2026-11-09T22:00", "2026-11-10T02:00"))
    assert [d["absence_hours"] for d in r["days"]] == [2.0, 2.0]
    assert r["allowance_total"] == 28.00


def test_end_at_midnight_is_read_as_24h_of_the_previous_day():
    # Mon 09:00 -> Tue 00:00 is a one-day trip on Monday: 15 h, more than 8 h -> 14,00 (no Tuesday)
    r = calc(trip("2026-11-09T09:00", "2026-11-10T00:00"))
    assert [d["date"] for d in r["days"]] == ["2026-11-09"]
    assert day(r, "2026-11-09")["absence_hours"] == 15.0
    assert r["allowance_total"] == 14.00


def test_end_at_midnight_two_days_later_is_a_two_day_trip():
    # Mon 09:00 -> Wed 00:00: Monday (first date, 15 h) and Tuesday (last date, 24 h): 14 + 14
    r = calc(trip("2026-11-09T09:00", "2026-11-11T00:00"))
    assert [d["date"] for d in r["days"]] == ["2026-11-09", "2026-11-10"]
    assert [d["base"] for d in r["days"]] == [14.00, 14.00]
    assert r["allowance_total"] == 28.00


def test_end_before_or_equal_start_is_rejected():
    with pytest.raises(ValueError):
        calc(trip("2026-11-09T09:00", "2026-11-09T09:00"))
    with pytest.raises(ValueError):
        calc(trip("2026-11-09T10:00", "2026-11-09T09:00"))


def test_reductions_never_below_zero_on_arrival_day_with_all_meals():
    meals = [{"date": "2026-11-09", "breakfast": True, "lunch": True, "dinner": True}]
    r = calc(trip("2026-11-09T20:00", "2026-11-10T12:00", meals=meals))
    assert day(r, "2026-11-09")["amount"] == 0.00  # 14 - 5,60 - 11,20 - 11,20 < 0
    assert day(r, "2026-11-10")["amount"] == 14.00


def test_year_change_inside_a_trip_uses_the_table_of_each_day():
    # 2026 days: 14 (first), 28 (full day) ; 2027 days (synthetic table): 30 (full day), 15 (last date)
    r = calc(trip("2026-12-30T08:00", "2027-01-02T18:00"))
    assert [d["amount"] for d in r["days"]] == [14.00, 28.00, 30.00, 15.00]
    assert r["allowance_total"] == 87.00
    assert r["rules_version"] == "2025-12-05, 2027-SYNTH"


def test_rounding_of_reductions_is_half_up_to_cents():
    odd = {
        "year": 2026,
        "rules_version": "odd",
        "mileage_per_km": Decimal("0.30"),
        "fallback_country": "LU",
        "domestic": {"from8h": Decimal("14.00"), "h24": Decimal("33.33")},
        "countries": {"DE": {"name": "Germany", "from8h": Decimal("14.00"), "h24": Decimal("33.33")}},
    }
    meals = [{"date": "2026-11-10", "breakfast": True, "lunch": True, "dinner": False}]
    t = trip("2026-11-09T08:00", "2026-11-11T18:00", meals=meals)
    r = trips_calc.compute(t, lambda y: odd)
    # 20 % of 33,33 = 6,666 -> 6,67 ; 40 % of 33,33 = 13,332 -> 13,33
    assert red(day(r, "2026-11-10")) == 20.00
    assert day(r, "2026-11-10")["amount"] == 13.33


def test_three_month_limit_pays_only_the_first_three_months_at_one_place():
    # start 31 Jan: last paid day is 30 Apr (April has no 31st). 31 Jan 14 + (1 Feb .. 30 Apr: 89 days x 28)
    # = 14 + 2492 = 2506; 1 May (last date) gets nothing.
    places = [{"from_date": "2026-01-31", "country": "DE", "city": "Hamburg"}]
    r = calc(trip("2026-01-31T08:00", "2026-05-01T18:00", places=places))
    assert day(r, "2026-04-30")["amount"] == 28.00
    assert day(r, "2026-05-01")["amount"] == 0.00
    assert "Three-month limit reached" in day(r, "2026-05-01")["note"]
    assert r["allowance_total"] == 2506.00


def test_plain_limit_boundary_from_1_january():
    # start 1 Jan: last paid day 31 Mar; 1 Apr gets nothing
    r = calc(trip("2026-01-01T08:00", "2026-04-01T18:00"))
    assert day(r, "2026-03-31")["amount"] == 28.00
    assert day(r, "2026-04-01")["amount"] == 0.00
    assert "Three-month limit reached" in day(r, "2026-04-01")["note"]


@pytest.mark.parametrize(
    "start,expected",
    [
        ("2026-01-01", "2026-03-31"),
        ("2026-01-31", "2026-04-30"),
        ("2026-11-30", "2027-02-28"),
        ("2026-08-31", "2026-11-30"),
    ],
)
def test_last_paid_day_rule(start, expected):
    assert trips_calc.last_paid_day(date.fromisoformat(start)).isoformat() == expected


def test_four_week_interruption_starts_a_new_three_month_period():
    # Hamburg 1-9 Jan, Berlin 10 Jan - 28 Feb, Hamburg again from 1 Mar (50 days away): new period, no limit in April
    places = [
        {"from_date": "2026-01-01", "country": "DE", "city": "Hamburg"},
        {"from_date": "2026-01-10", "country": "DE", "city": "Berlin"},
        {"from_date": "2026-03-01", "country": "DE", "city": "Hamburg"},
    ]
    r = calc(trip("2026-01-01T08:00", "2026-04-20T18:00", places=places))
    assert day(r, "2026-04-15")["amount"] == 28.00
    assert "Three-month" not in day(r, "2026-04-15")["note"]


def test_a_shorter_interruption_does_not_reset_the_limit():
    # Hamburg 1 Jan - 9 Jan, Berlin from 10 Jan, Hamburg again from 1 Mar is the reset case; here Berlin only
    # interrupts for 20 days (Hamburg from 30 Jan), so the Hamburg period started on 1 Jan continues: limit 1 Apr
    places = [
        {"from_date": "2026-01-01", "country": "DE", "city": "Hamburg"},
        {"from_date": "2026-01-10", "country": "DE", "city": "Berlin"},
        {"from_date": "2026-01-30", "country": "DE", "city": "Hamburg"},
    ]
    r = calc(trip("2026-01-01T08:00", "2026-04-20T18:00", places=places))
    assert day(r, "2026-04-15")["amount"] == 0.00
    assert "Three-month limit reached" in day(r, "2026-04-15")["note"]


# ------------------------------------------------------------------ review round 2: DST, placeholders, rounding, places
def synthetic(**over):
    base = {
        "year": 2026,
        "rules_version": "synthetic",
        "mileage_per_km": Decimal("0.30"),
        "fallback_country": "LU",
        "domestic": {"from8h": Decimal("14.00"), "h24": Decimal("28.00")},
        "countries": {
            "DE": {"name": "Germany", "from8h": Decimal("14.00"), "h24": Decimal("28.00"), "placeholder": False}
        },
    }
    base.update(over)
    return base


def test_dst_start_day_counts_elapsed_time_in_berlin():
    # 29 Mar 2026: clocks jump 02:00 -> 03:00. 00:00-08:30 wall clock is 7.5 h elapsed -> no allowance
    r = calc(trip("2026-03-29T00:00", "2026-03-29T08:30"))
    assert day(r, "2026-03-29")["absence_hours"] == 7.5
    assert day(r, "2026-03-29")["amount"] == 0.00
    # 00:00-09:00 wall clock is 8 h elapsed: not more than 8 h -> 0
    r = calc(trip("2026-03-29T00:00", "2026-03-29T09:00"))
    assert day(r, "2026-03-29")["absence_hours"] == 8.0
    assert r["allowance_total"] == 0.00


def test_dst_start_full_day_is_23_hours_and_pays_the_one_day_rate():
    r = calc(trip("2026-03-29T00:00", "2026-03-30T00:00"))
    assert [d["date"] for d in r["days"]] == ["2026-03-29"]
    assert day(r, "2026-03-29")["absence_hours"] == 23.0
    assert r["allowance_total"] == 14.00


def test_dst_end_day_has_25_hours_elapsed_before_the_change():
    # 25 Oct 2026: clocks go back 03:00 -> 02:00. 00:00-07:30 wall clock is 8.5 h elapsed -> 14
    r = calc(trip("2026-10-25T00:00", "2026-10-25T07:30"))
    assert day(r, "2026-10-25")["absence_hours"] == 8.5
    assert r["allowance_total"] == 14.00


def test_placeholder_row_adds_a_verify_note_to_each_affected_day():
    r = calc(trip("2026-11-09T08:00", "2026-11-11T18:00", places=[{"from_date": "2026-11-09", "country": "AT"}]))
    for d in r["days"]:
        assert d["note"] == "Rate for AT is a placeholder: verify"
    assert day(r, "2026-11-09")["country"] == "AT"


def test_domestic_days_have_no_placeholder_note():
    r = calc(trip("2026-11-09T08:00", "2026-11-09T18:00"))
    assert day(r, "2026-11-09")["note"] == ""


def test_luxembourg_fallback_row_is_itself_a_placeholder_and_says_so():
    r = calc(trip("2026-11-09T08:00", "2026-11-09T18:00", places=[{"from_date": "2026-11-09", "country": "XX"}]))
    assert day(r, "2026-11-09")["note"] == (
        "Country not in the rate table: Luxembourg rate used; Rate for LU is a placeholder: verify"
    )


def test_half_up_not_half_even_on_an_exact_half_cent_reduction():
    # 20 % of 0,025 = 0,005 exactly: half-up gives 0,01 (half-even would give 0,00)
    t = synthetic(domestic={"from8h": Decimal("14.00"), "h24": Decimal("0.025")})
    t["countries"]["DE"] = {
        "name": "Germany",
        "from8h": Decimal("14.00"),
        "h24": Decimal("0.025"),
        "placeholder": False,
    }
    meals = [{"date": "2026-11-09", "breakfast": True}]
    r = trips_calc.compute(trip("2026-11-09T08:00", "2026-11-10T18:00", meals=meals), lambda y: t)
    d = day(r, "2026-11-09")
    assert d["reductions"] == [{"meal": "breakfast", "amount": 0.01}]
    assert d["amount"] == 13.99


def test_middle_full_day_shows_24_hours_absence():
    r = calc(trip("2026-11-09T08:00", "2026-11-11T18:00"))
    assert day(r, "2026-11-10")["absence_hours"] == 24.0


def test_mid_trip_place_switch_gives_per_day_rows():
    # DE on 9 Nov (domestic 14), AT from 10 Nov (placeholder 30 / 45), last date 11 Nov in AT: 14 + 45 + 30 = 89
    places = [{"from_date": "2026-11-09", "country": "DE"}, {"from_date": "2026-11-10", "country": "AT"}]
    r = calc(trip("2026-11-09T08:00", "2026-11-11T18:00", places=places))
    assert [(d["country"], d["amount"]) for d in r["days"]] == [("DE", 14.00), ("AT", 45.00), ("AT", 30.00)]
    assert r["allowance_total"] == 89.00


def test_one_day_trip_abroad_uses_the_last_place():
    places = [
        {"from_date": "2026-11-09", "country": "DE"},
        {"from_date": "2026-11-09", "country": "CH", "city": "Zurich"},
    ]
    r = calc(trip("2026-11-09T08:00", "2026-11-09T18:00", places=places))
    assert day(r, "2026-11-09")["country"] == "CH"
    assert day(r, "2026-11-09")["city"] == "Zurich"
    assert r["allowance_total"] == 40.00  # CH placeholder from8h


def test_meal_reduction_on_the_last_day():
    meals = [{"date": "2026-11-11", "lunch": True}]
    r = calc(trip("2026-11-09T08:00", "2026-11-11T18:00", meals=meals))
    assert day(r, "2026-11-11")["amount"] == 2.80  # 14 - 11,20
    assert r["allowance_total"] == 14.00 + 28.00 + 2.80


def test_trip_across_a_year_mileage_uses_the_start_year():
    r = calc(trip("2026-12-30T08:00", "2027-01-02T18:00", mileage=[{"date": "2026-12-30", "km": 10}]))
    assert r["mileage_total"] == 3.00


# ------------------------------------------------------------------ review round 3: limit key and reset boundary
def _limit_trip(places, end="2026-04-10T18:00"):
    """A 96-day trip (too long for the API, fine for the pure calculation): 5 Jan to 10 Apr 2026."""
    return trip("2026-01-05T08:00", end, places=places)


def test_city_name_is_compared_case_folded_and_trimmed():
    # 'Hamburg' and ' hamburg ' are one place: no gap, so the period started on 5 Jan continues and is over on 5 Apr
    places = [
        {"from_date": "2026-01-05", "country": "DE", "city": "Hamburg"},
        {"from_date": "2026-02-01", "country": "DE", "city": " hamburg "},
    ]
    r = calc(_limit_trip(places))
    assert day(r, "2026-04-04")["amount"] == 28.00
    assert day(r, "2026-04-05")["amount"] == 0.00
    assert "Three-month limit reached" in day(r, "2026-04-05")["note"]


def test_same_city_in_two_countries_is_two_periods_de_at():
    # DE Hamburg 5 Jan - 31 Jan, AT Hamburg 1 Feb - 10 Mar, DE Hamburg again from 11 Mar: the DE gap is 38 days, so
    # the DE period restarts on 11 Mar and 5 Apr is paid. Keyed on the city alone, 5 Apr would be over the limit.
    places = [
        {"from_date": "2026-01-05", "country": "DE", "city": "Hamburg"},
        {"from_date": "2026-02-01", "country": "AT", "city": "Hamburg"},
        {"from_date": "2026-03-11", "country": "DE", "city": "Hamburg"},
    ]
    r = calc(_limit_trip(places))
    assert day(r, "2026-02-01")["country"] == "AT"
    assert day(r, "2026-04-05")["amount"] == 28.00
    assert "Three-month" not in day(r, "2026-04-05")["note"]


def test_two_unknown_countries_falling_back_to_lu_are_still_two_periods():
    # XX and YY both use the Luxembourg row, but the limit is keyed on the ORIGINAL code: the XX period restarts
    places = [
        {"from_date": "2026-01-05", "country": "XX", "city": "Hamburg"},
        {"from_date": "2026-02-01", "country": "YY", "city": "Hamburg"},
        {"from_date": "2026-03-11", "country": "XX", "city": "Hamburg"},
    ]
    r = calc(_limit_trip(places))
    assert day(r, "2026-04-05")["country"] == "LU"
    assert day(r, "2026-04-05")["amount"] == 45.00  # Luxembourg 24-hour row (placeholder), middle day
    assert "Three-month" not in day(r, "2026-04-05")["note"]


@pytest.mark.parametrize(
    "absence_days,continues",
    [(27, True), (28, False)],
)
def test_four_week_reset_boundary_27_continues_28_resets(absence_days, continues):
    # Hamburg 5-10 Jan, then Berlin for `absence_days` days, then Hamburg again. The period started on 5 Jan ends
    # with 4 Apr; a short gap keeps it (5 Apr over the limit), 28 days away starts a new period (5 Apr paid).
    from datetime import timedelta

    back = date(2026, 1, 11) + timedelta(days=absence_days)  # first day back in Hamburg
    places = [
        {"from_date": "2026-01-05", "country": "DE", "city": "Hamburg"},
        {"from_date": "2026-01-11", "country": "DE", "city": "Berlin"},
        {"from_date": back.isoformat(), "country": "DE", "city": "Hamburg"},
    ]
    r = calc(_limit_trip(places))
    assert day(r, "2026-04-04")["amount"] == 28.00
    if continues:
        assert day(r, "2026-04-05")["amount"] == 0.00
        assert "Three-month limit reached" in day(r, "2026-04-05")["note"]
        assert day(r, "2026-04-10")["amount"] == 0.00
    else:
        assert day(r, "2026-04-05")["amount"] == 28.00
        assert day(r, "2026-04-10")["amount"] == 14.00  # last date: from8h, no limit note
        assert "Three-month" not in day(r, "2026-04-10")["note"]
