"""Unit tests of the plausibility rules. Expected values are worked out by hand in the comments, not by calling the
code under test."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from permita38.core import identifiers
from permita38.core.service import sanity_rules as r

TODAY = date(2026, 6, 15)  # a Monday


def codes(ws):
    return [w["code"] for w in ws]


# ---------------------------------------------------------------------------------------------- identifiers
def _usti_check_digit(first8: str) -> str:
    """Independent implementation of the BZSt method (ISO 7064 mod 11,10), written from the spec."""
    product = 10
    for ch in first8:
        s = (int(ch) + product) % 10
        s = 10 if s == 0 else s
        product = (2 * s) % 11
    c = 11 - product
    return "0" if c == 10 else str(c)


@pytest.mark.parametrize("first8", ["13669597", "12345678", "98765432", "11111111", "27123456"])
def test_german_vat_id_accepts_correct_check_digit_rejects_every_other(first8):
    good = _usti_check_digit(first8)
    assert identifiers.vat_id_error(f"DE{first8}{good}") is None
    for wrong in "0123456789":
        if wrong != good:
            assert identifiers.vat_id_error(f"DE{first8}{wrong}"), wrong


@pytest.mark.parametrize(
    "value, ok",
    [
        ("DE136695976", True),
        ("de 136 695 976", True),
        ("DE12345678", False),  # 8 digits
        ("DE1366959761", False),  # 10 digits
        ("DE036695976", False),  # a German id never starts with 0
        ("136695976", False),  # no country prefix
        ("ATU12345678", True),
        ("AT12345678", False),  # missing the U
        ("FR12345678901", True),
        ("NL123456789B01", True),
        ("NL123456789A01", False),
        ("ZZ123456789", False),
        ("NO923456783MVA", True),
        ("NO 923 456 783 MVA", True),
        ("NO923456783", False),  # MVA suffix missing
        ("NO92345678MVA", False),
        ("EL123456789", True),
        ("GR123456789", True),  # alias of EL
        ("GR12345678", False),
    ],
)
def test_vat_id_formats(value, ok):
    assert (identifiers.vat_id_error(value) is None) is ok


@pytest.mark.parametrize(
    "value, ok",
    [
        ("12/345/67890", True),  # 2+3+5 = 10 digits
        ("2181508150", True),  # 10
        ("12345678901", True),  # 11
        ("123/4567/89012", False),  # 3+4+5 = 12
        ("1234567890123", True),  # 13 (unified form)
        ("123456789", False),  # 9
        ("00000000000", False),  # 11 identical digits: placeholder
        ("12/345/6789A", False),  # letter
    ],
)
def test_tax_number_digit_count(value, ok):
    assert (identifiers.tax_number_error(value) is None) is ok


def test_iban_checksum_and_length():
    assert identifiers.iban_error("DE89 3704 0044 0532 0130 00") is None  # well-known documentation IBAN
    assert identifiers.iban_error("GB82WEST12345698765432") is None
    assert identifiers.iban_error("DE89370400440532013001")  # check digits
    assert identifiers.iban_error("DE8937040044053201300")  # 21 characters
    assert identifiers.iban_error("XX89370400440532013000")  # unknown country
    assert identifiers.bic_error("COBADEFFXXX") is None
    assert identifiers.bic_error("COBADEF") is not None


def test_greek_gr_prefix_gets_an_info_hint_el_does_not():
    ws = r.check_identifiers({"vat_id": "GR123456789"}, owner="customer")
    assert codes(ws) == ["customer.vat_id_prefix"] and ws[0]["severity"] == "info" and "EL" in ws[0]["message"]
    assert r.check_identifiers({"vat_id": "EL123456789"}, owner="customer") == []
    assert r.check_identifiers({"vat_id": "NO923456783MVA"}, owner="customer") == []


def test_check_identifiers_skips_blank_and_names_the_field():
    assert r.check_identifiers({"vat_id": "", "tax_number": "  "}, owner="customer") == []
    ws = r.check_identifiers({"vat_id": "DE136695977", "iban": "DE89370400440532013000"}, owner="customer")
    assert codes(ws) == ["customer.vat_id_invalid"]
    assert ws[0]["field"] == "vat_id" and ws[0]["severity"] == "warning" and ws[0]["confirmable"] is False


# ---------------------------------------------------------------------------------------------- working time
def entry(start, end, minutes=None, day="2026-06-10", **kw):
    if minutes is None:
        s, e = (int(start[:2]) * 60 + int(start[3:]), int(end[:2]) * 60 + int(end[3:]))
        minutes = (e - s) % 1440
    return {
        "id": kw.pop("id", "e" + start.replace(":", "") + end.replace(":", "")),
        "date": day,
        "start": start,
        "end": end,
        "minutes": minutes,
        **kw,
    }


def check(rec, same=(), prev=(), nxt=(), employee=True, **kw):
    return r.check_entry(
        rec, same_day=list(same), prev_day=list(prev), next_day=list(nxt), employee=employee, today=TODAY, **kw
    )


@pytest.mark.parametrize(
    "start, end, expect_break_warning",
    [
        ("08:00", "14:00", False),  # exactly 6 h: no break needed
        ("08:00", "14:01", True),  # 6 h 1 min without any break
        ("08:00", "17:00", True),  # 9 h
    ],
)
def test_break_needed_after_more_than_six_hours(start, end, expect_break_warning):
    assert ("time.break_missing" in codes(check(entry(start, end)))) is expect_break_warning


def test_break_thirty_after_six_and_forty_five_after_nine_hours():
    # 08:00-12:00 + 12:30-16:30 = 8 h work, a 30 min gap: enough for <= 9 h
    a, b = entry("08:00", "12:00"), entry("12:30", "16:30")
    assert "time.break_missing" not in codes(check(b, same=[a]))
    # 08:00-12:00 + 12:29-16:29: only 29 min break for 8 h
    b = entry("12:29", "16:29")
    ws = [w for w in check(b, same=[a]) if w["code"] == "time.break_missing"]
    assert ws and ws[0]["details"] == {"work_minutes": 480 + 0, "break_minutes": 29, "required_minutes": 30}


def test_pause_deducted_from_minutes_counts_as_break():
    assert "time.break_missing" not in codes(check(entry("08:00", "18:00", 540)))  # 1 h pause already deducted
    assert "time.break_missing" in codes(check(entry("08:00", "18:00", 600)))  # no pause
    # two entries with a real gap: unchanged
    a, b = entry("08:00", "12:00"), entry("12:29", "16:29")
    ws = [w for w in check(b, same=[a]) if w["code"] == "time.break_missing"]
    assert ws and ws[0]["details"]["break_minutes"] == 29


def test_nine_hours_exactly_needs_30_but_nine_hours_one_needs_45():
    # 4.5 h + 30 min break + 4.5 h = 9 h exactly -> 30 min are enough
    a, b = entry("08:00", "12:30"), entry("13:00", "17:30")
    assert "time.break_missing" not in codes(check(b, same=[a]))
    # 4.5 h + 30 min break + 4 h 31 min = 9 h 1 min -> 45 min needed
    b = entry("13:00", "17:31")
    ws = [w for w in check(b, same=[a]) if w["code"] == "time.break_missing"]
    assert ws and ws[0]["details"]["required_minutes"] == 45 and ws[0]["details"]["break_minutes"] == 30
    # with a 45 min gap it is fine
    b = entry("13:15", "17:46")
    assert "time.break_missing" not in codes(check(b, same=[a]))


def test_gap_shorter_than_15_minutes_is_not_a_break():
    a, b = entry("08:00", "11:00"), entry("11:14", "15:00")  # gap 14 -> one block of 7 h
    ws = [w for w in check(b, same=[a]) if w["code"] == "time.break_missing"]
    assert ws and ws[0]["details"]["break_minutes"] == 0
    b = entry("11:15", "15:00")  # gap 15 -> counts, but 3 h + 3 h 45 = 6 h 45 needs 30
    ws = [w for w in check(b, same=[a]) if w["code"] == "time.break_missing"]
    assert ws and ws[0]["details"]["break_minutes"] == 15


def test_break_rule_is_skipped_when_an_entry_has_no_clock_times():
    long_entry = {"id": "x", "date": "2026-06-10", "minutes": 600}
    other = entry("08:00", "12:00")
    assert "time.break_missing" not in codes(check(other, same=[long_entry]))
    assert "time.break_missing" not in codes(check(long_entry))


@pytest.mark.parametrize("minutes, warn", [(600, False), (601, True)])
def test_day_over_ten_hours_sums_all_entries_of_the_day(minutes, warn):
    other = {"id": "o", "date": "2026-06-10", "minutes": 300}
    rec = {"id": "n", "date": "2026-06-10", "minutes": minutes - 300}
    assert ("time.day_over_10h" in codes(check(rec, same=[other]))) is warn


def test_rest_period_exactly_eleven_hours_is_fine():
    prev = entry("14:00", "22:00", day="2026-06-09")
    ok = entry("09:00", "12:00")  # 22:00 -> 09:00 = 11 h
    assert "time.rest_short" not in codes(check(ok, prev=[prev]))
    short = entry("08:59", "12:00")  # 10 h 59
    ws = [w for w in check(short, prev=[prev]) if w["code"] == "time.rest_short"]
    assert ws and ws[0]["details"] == {"rest_minutes": 659, "other_day": "2026-06-09"}


def test_rest_period_against_next_day_and_night_shift():
    nxt = entry("06:00", "10:00", day="2026-06-11")
    late = entry("18:00", "20:00")  # 20:00 -> 06:00 next day = 10 h
    ws = [w for w in check(late, nxt=[nxt]) if w["code"] == "time.rest_short"]
    assert ws and ws[0]["details"]["rest_minutes"] == 600
    # a night shift 22:00-06:00 on the 9th leaves 3 h before 09:00 on the 10th
    night = entry("22:00", "06:00", day="2026-06-09")
    assert night["minutes"] == 480
    rec = entry("09:00", "10:00")
    ws = [w for w in check(rec, prev=[night]) if w["code"] == "time.rest_short"]
    assert ws and ws[0]["details"]["rest_minutes"] == 180


def test_employee_gets_warnings_boss_gets_info():
    rec = entry("06:00", "17:00")  # 11 h, no break
    for employee, sev in ((True, "warning"), (False, "info")):
        ws = check(rec, employee=employee)
        assert {w["severity"] for w in ws if w["code"] in ("time.day_over_10h", "time.break_missing")} == {sev}


def test_minutes_larger_than_the_clock_span():
    assert "entry.minutes_exceed_span" in codes(check(entry("08:00", "09:00", minutes=62)))
    assert "entry.minutes_exceed_span" not in codes(check(entry("08:00", "09:00", minutes=61)))  # one minute of slack
    assert "entry.minutes_exceed_span" not in codes(check(entry("08:00", "09:00", minutes=45)))  # a break inside


def test_entry_dates_and_sunday_holiday_only_when_date_changed():
    assert "entry.date_future" in codes(check(entry("08:00", "09:00", day="2026-06-16")))
    assert "entry.date_future" not in codes(check(entry("08:00", "09:00", day="2026-06-15")))
    old_day = (TODAY - timedelta(days=366)).isoformat()
    assert "entry.date_old" not in codes(check(entry("08:00", "09:00", day=old_day)))
    old_day = (TODAY - timedelta(days=367)).isoformat()
    rec = entry("08:00", "09:00", day=old_day)
    assert "entry.date_old" in codes(check(rec))
    assert "entry.date_old" not in codes(check(rec, old=dict(rec)))  # untouched date: no nagging
    sunday = entry("08:00", "09:00", day="2026-06-14")
    assert "time.sunday_holiday" in codes(check(sunday))
    assert "time.sunday_holiday" not in codes(check(sunday, employee=False))
    assert "time.sunday_holiday" in codes(check(entry("08:00", "09:00"), holiday="Fronleichnam"))


# ---------------------------------------------------------------------------------------------- documents
def doc(**kw):
    base = {
        "id": "d1",
        "customer_id": "c1",
        "date": TODAY.isoformat(),
        "lines": [{"description": "x", "qty": 2, "price": 50}],
    }
    return {**base, **kw}


def check_doc(coll, rec, **kw):
    kw.setdefault("old", None)
    kw.setdefault("settings", {"vat_rate": 19, "tax_number": "12/345/67890"})
    kw.setdefault("others", [])
    return r.check_document(coll, rec, today=TODAY, **kw)


def test_document_date_boundaries():
    ok_future = (TODAY + timedelta(days=31)).isoformat()
    bad_future = (TODAY + timedelta(days=32)).isoformat()
    ok_past = (TODAY - timedelta(days=366)).isoformat()
    bad_past = (TODAY - timedelta(days=367)).isoformat()
    assert check_doc("invoices", doc(date=ok_future)) == []
    assert codes(check_doc("invoices", doc(date=bad_future))) == ["invoice.date.far_future"]
    assert check_doc("offers", doc(date=ok_past)) == []
    assert codes(check_doc("offers", doc(date=bad_past))) == ["offer.date.far_past"]
    stored = doc(date=bad_past)
    assert check_doc("offers", stored, old=dict(stored)) == []  # date untouched


def test_date_order():
    ws = check_doc("invoices", doc(due_date="2026-06-14", paid_date="2026-06-01"))
    assert sorted(codes(ws)) == ["invoice.due_date_before_date", "invoice.paid_date_before_date"]
    assert check_doc("invoices", doc(due_date=TODAY.isoformat())) == []  # same day is fine
    assert codes(check_doc("offers", doc(valid_until="2026-06-14"))) == ["offer.valid_until_before_date"]
    assert codes(check_doc("invoices", doc(service_from="2026-06-16"))) == ["invoice.service_after_date"]
    # due_date means nothing on an offer
    assert check_doc("offers", doc(due_date="2026-01-01")) == []


def test_totals():
    assert codes(check_doc("invoices", doc(lines=[{"qty": 3, "price": 0}]))) == ["invoice.total_zero"]
    assert codes(check_doc("offers", doc(lines=[{"qty": 1, "price": -5}]))) == ["offer.total_negative"]
    ws = check_doc("invoices", doc(lines=[{"qty": 1, "price": -5}]))
    assert codes(ws) == ["invoice.total_negative"] and ws[0]["severity"] == "info"
    # a free extra line next to a priced one
    ws = check_doc("invoices", doc(lines=[{"qty": 1, "price": 10}, {"qty": 1, "price": 0}]))
    assert codes(ws) == ["invoice.line_free"]
    assert check_doc("invoices", doc(lines=[])) == []  # nothing to say about an empty draft


def test_vat_hints():
    zero = doc(lines=[{"qty": 1, "price": 10, "vat": 0}])
    assert codes(check_doc("invoices", zero)) == ["invoice.vat_zero_line"]
    klein = {"kleinunternehmer": True, "vat_rate": 19, "tax_number": "12/345/67890"}
    assert check_doc("invoices", zero, settings=klein) == []
    assert codes(check_doc("invoices", doc(lines=[{"qty": 1, "price": 10, "vat": 19}]), settings=klein)) == [
        "invoice.vat_ignored"
    ]


def test_duplicate_and_outlier_use_exact_money():
    other = doc(id="d0", number="RE-1")
    assert codes(check_doc("invoices", doc(), others=[other])) == ["invoice.possible_duplicate"]
    assert check_doc("invoices", doc(date="2026-06-14"), others=[other]) == []  # different date
    assert check_doc("invoices", doc(customer_id="c2"), others=[other]) == []
    assert check_doc("invoices", doc(id="d0"), others=[other]) == []  # a record is never its own duplicate
    # five earlier invoices of 100.00 net: 2 000.00 is exactly 20x -> fine, 2 000.01 is not
    hist = [doc(id=f"h{i}", customer_id="c9", date="2026-01-01", lines=[{"qty": 1, "price": 100}]) for i in range(5)]
    assert check_doc("invoices", doc(lines=[{"qty": 1, "price": 2000}]), others=hist) == []
    ws = check_doc("invoices", doc(lines=[{"qty": 1, "price": "2000.01"}]), others=hist)
    assert codes(ws) == ["invoice.total_outlier"]
    # too few samples, or below the 500 floor: silent
    assert check_doc("invoices", doc(lines=[{"qty": 1, "price": 2000.01}]), others=hist[:4]) == []
    small = [doc(id=f"s{i}", customer_id="c9", lines=[{"qty": 1, "price": 1}]) for i in range(5)]
    assert check_doc("invoices", doc(lines=[{"qty": 1, "price": 499}]), others=small) == []


def test_seller_tax_id_required_on_finalize_only():
    empty = {"vat_rate": 19, "tax_number": " ", "vat_id": ""}
    assert check_doc("invoices", doc(), settings=empty) == []
    assert codes(check_doc("invoices", doc(), settings=empty, finalize=True)) == ["invoice.seller_tax_id_missing"]
    assert check_doc("invoices", doc(), settings={**empty, "vat_id": "DE136695976"}, finalize=True) == []
    assert check_doc("offers", doc(), settings=empty, finalize=True) == []


# ---------------------------------------------------------------------------------------------- expenses
def exp(**kw):
    return {"id": "x1", "date": TODAY.isoformat(), "net": 100, "vat": 19, "vat_rate": 19, **kw}


def check_exp(rec, **kw):
    kw.setdefault("old", None)
    kw.setdefault("settings", {"vat_rate": 19})
    kw.setdefault("others", [])
    return r.check_expense(rec, today=TODAY, **kw)


def test_expense_vat_against_rate_to_the_cent():
    assert check_exp(exp()) == []
    # 19 % of 621.50 = 118.085 -> 118.09 (half-up); 118.08 is one cent off: tolerated, 118.07 is not
    assert check_exp(exp(net=621.5, vat=118.09)) == []
    assert check_exp(exp(net=621.5, vat=118.08)) == []
    assert codes(check_exp(exp(net=621.5, vat=118.07))) == ["expense.vat_mismatch"]
    assert codes(check_exp(exp(net=100, vat=190, vat_rate=19))) == ["expense.vat_exceeds_net", "expense.vat_mismatch"]


def test_expense_implied_rate_when_no_rate_given():
    assert check_exp(exp(vat_rate=None, vat=7, net=100)) == []
    assert check_exp(exp(vat_rate=None, vat=19.4, net=100)) == []  # within 0.5 pt of 19
    assert codes(check_exp(exp(vat_rate=None, vat=12, net=100))) == ["expense.vat_rate_implausible"]
    assert check_exp(exp(vat_rate=None, vat=0.5, net=5)) == []  # tiny amounts: rounding noise, ignored
    assert check_exp(exp(vat_rate=None, vat=0, net=100)) == []


def test_expense_dates_duplicate_and_outlier():
    assert check_exp(exp(date="2026-06-18")) == []  # 3 days ahead: tolerated
    assert codes(check_exp(exp(date="2026-06-19"))) == ["expense.date.far_future"]  # 4 days
    assert codes(check_exp(exp(date="2025-06-13"))) == ["expense.date.far_past"]  # 367 days
    assert check_exp(exp(date="2025-06-14")) == []  # 366 days
    a = exp(id="x0", vendor="Cafe Demo", net=10, vat=1.9)
    assert codes(check_exp(exp(vendor="cafe demo ", net=10, vat=1.9), others=[a])) == ["expense.possible_duplicate"]
    assert check_exp(exp(vendor="Other", net=10, vat=1.9), others=[a]) == []
    hist = [exp(id=f"h{i}", category="travel", net=20, vat=0, vat_rate=0, date="2026-01-01") for i in range(5)]
    # gross 20 x 20 = 400 < 500 floor; 600 > 20 x 20 and above the floor
    assert check_exp(exp(category="travel", net=400, vat=0, vat_rate=0), others=hist) == []
    assert codes(check_exp(exp(category="travel", net=600, vat=0, vat_rate=0), others=hist)) == [
        "expense.amount_outlier"
    ]
    assert check_exp(exp(category="meals", net=600, vat=0, vat_rate=0), others=hist) == []  # other category


def test_net_of_ignores_garbage():
    assert r.net_of({"lines": "x"}) == Decimal(0)
    assert r.net_of({"lines": [None, {"qty": "abc", "price": 5}, {"qty": 0.5, "price": 0.01}]}) == Decimal(
        "0.01"
    )  # 0.005 -> 0.01


HOTEL_LINES = [
    {"description": "Room", "gross": 160, "vat_rate": 7},
    {"description": "Breakfast", "gross": 30, "vat_rate": 19},
    {"description": "City tax", "gross": 6, "vat_rate": 0},
]


def test_itemised_expense_skips_the_single_rate_vat_checks_but_keeps_duplicate_and_outlier():
    hotel = exp(net=180.74, vat=15.26, vat_rate=None, lines=HOTEL_LINES, vendor="Hotel Demo")
    assert codes(check_exp(hotel)) == []
    # the same sums as a classic expense still warn (implied rate 8.4 % is not a usual rate)
    classic = {k: v for k, v in hotel.items() if k != "lines"}
    assert codes(check_exp(classic)) == ["expense.vat_rate_implausible"]
    assert codes(check_exp({**classic, "vat_rate": 19})) == ["expense.vat_mismatch"]
    assert codes(check_exp({**hotel, "vat": 300})) == []  # no per-rate check on the sums of a bill with lines
    # duplicate detection keeps using the summed top-level fields
    other = exp(id="x0", net=180.74, vat=15.26, vendor="hotel demo ")
    assert codes(check_exp(hotel, others=[other])) == ["expense.possible_duplicate"]
