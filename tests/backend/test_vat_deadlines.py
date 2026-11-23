"""UStVA and ZM deadlines: literal dates of docs/specs/ustva.md and zm.md."""

from datetime import date

import pytest

from permita38.core import holidays
from permita38.core.vat.deadlines import PeriodKind, period_bounds, ustva_due, zm_due


@pytest.mark.parametrize(
    ("year", "kind", "no", "dauer", "expected"),
    [
        (2026, "quarter", 1, False, date(2026, 4, 10)),
        (2025, "month", 12, False, date(2026, 1, 12)),
        (2026, "quarter", 3, False, date(2026, 10, 12)),
        (2026, "quarter", 1, True, date(2026, 5, 11)),
        (2023, "quarter", 1, False, date(2023, 4, 11)),
        (2026, "month", 12, True, date(2027, 2, 10)),
        (2026, "month", 2, False, date(2026, 3, 10)),
    ],
)
def test_ustva_due_spec_dates(year, kind, no, dauer, expected):
    assert ustva_due(year, kind, no, dauer, "") == expected


@pytest.mark.parametrize(
    ("quarter", "expected"),
    [
        (1, date(2026, 4, 27)),
        (2, date(2026, 7, 27)),
        (3, date(2026, 10, 26)),
        (4, date(2027, 1, 25)),
    ],
)
def test_zm_due_spec_dates(quarter, expected):
    assert zm_due(2026, quarter, "") == expected


def test_next_working_day():
    assert holidays.next_working_day(date(2026, 4, 10), "") == date(2026, 4, 10)
    assert holidays.next_working_day(date(2026, 4, 4), "") == date(2026, 4, 7)  # Sat -> Easter Monday 6th -> Tue
    assert holidays.next_working_day(date(2026, 12, 24), "") == date(2026, 12, 24)
    assert holidays.next_working_day(date(2026, 12, 31), "") == date(2026, 12, 31)


def _all_periods():
    for year in range(2023, 2029):
        for no in range(1, 13):
            yield year, "month", no
        for no in range(1, 5):
            yield year, "quarter", no


@pytest.mark.parametrize("state", ["", "BY", "SN", "BE"])
def test_all_due_dates_are_working_days(state):
    for year, kind, no in _all_periods():
        end = period_bounds(year, kind, no)[1]
        for dauer in (False, True):
            due = ustva_due(year, kind, no, dauer, state)
            assert due.weekday() < 5
            assert due not in holidays.holidays(due.year, state)
            assert due > end
            # Dauerfrist moves by one month before the shift
            plain = ustva_due(year, kind, no, False, state)
            assert due >= plain
            assert (due - plain).days in range(20, 40) if dauer else due == plain
        if kind == "quarter":
            z = zm_due(year, no, state)
            assert z.weekday() < 5
            assert z not in holidays.holidays(z.year, state)
            assert 25 <= (z - end).days <= 29


def test_dauerfrist_is_one_month_before_shift():
    for year, kind, no in _all_periods():
        end = period_bounds(year, kind, no)[1]
        m = end.month + 1
        y = end.year + (m > 12)
        m = (m - 1) % 12 + 1
        raw = date(y, m, 10)
        m2 = m + 1
        y2 = y + (m2 > 12)
        m2 = (m2 - 1) % 12 + 1
        raw_d = date(y2, m2, 10)
        assert holidays.next_working_day(raw, "") == ustva_due(year, kind, no, False, "")
        assert holidays.next_working_day(raw_d, "") == ustva_due(year, kind, no, True, "")


def test_period_bounds():
    assert period_bounds(2028, "month", 2) == (date(2028, 2, 1), date(2028, 2, 29))
    assert period_bounds(2026, "month", 2) == (date(2026, 2, 1), date(2026, 2, 28))
    assert period_bounds(2028, "quarter", 1) == (date(2028, 1, 1), date(2028, 3, 31))
    assert period_bounds(2026, "quarter", 4) == (date(2026, 10, 1), date(2026, 12, 31))
    assert period_bounds(2026, "month", 12) == (date(2026, 12, 1), date(2026, 12, 31))


@pytest.mark.parametrize(("kind", "no"), [("month", 0), ("month", 13), ("quarter", 0), ("quarter", 5)])
def test_invalid_period(kind: PeriodKind, no):
    with pytest.raises(ValueError):
        period_bounds(2026, kind, no)
