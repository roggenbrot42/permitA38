"""Regression tests: ``total_hours`` is spread over the assignee's AVAILABLE days of the contract period
(workdays that are neither public holidays nor absence days). Fallback without any available day: the nominal
spread over all workdays. ``hours_per_week`` stays nominal. All expectations are computed by hand.

Calendar: 2026-02-02 is a Monday; 2026-10-23 is a Friday. Federal state BY, 40 h/week, Mon-Fri, 8 h a day.
"""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service import planning as eng
from permita38.core.service.plan_service import PlanningService

MON = date(2026, 2, 2)
TODAY = date(2026, 10, 23)  # Friday


def make_repo(path):
    r = SQLiteRepository(Path(path) / "data")
    r.startup()
    r.save_settings({**r.settings(), "state": "BY"})
    return r


@pytest.fixture
def repo(tmp_path):
    r = make_repo(tmp_path)
    yield r
    r.close()


def add_user(repo, name="Ann", role="boss"):
    return repo.save(
        "users",
        dict(username=name.lower(), name=name, role=role, weekly_hours=40, workdays=[0, 1, 2, 3, 4], active=True),
    )


def add_contract(repo, users, start, end, total=None, hpw=None):
    users = users if isinstance(users, list) else [users]
    return repo.save(
        "contracts",
        dict(
            title="C",
            start=start.isoformat(),
            end=end.isoformat(),
            hours_per_week=hpw,
            total_hours=total,
            rate=100,
            status="confirmed",
            user_ids=[u["id"] for u in users],
        ),
    )


def add_absence(repo, user, start, end, kind="vacation"):
    return repo.save("absences", dict(user_id=user["id"], kind=kind, start=start, end=end))


def week(out, monday):
    return next(w for w in out["weeks"] if w["week"] == monday.isoformat())


def forecast(repo, n, today):
    return PlanningService(repo).forecast(n, None, True, today=today)


def per_day(repo, contract, lo, hi, user=None, today=TODAY):
    world = eng.load(repo, today, None, None, True)
    table = eng.day_table(world, lo, hi, user["id"] if user else None)
    hours = table.item_hours.get(f"contract:{contract['id']}", {})
    return {lo + timedelta(days=i): hours.get(lo + timedelta(days=i), 0.0) for i in range((hi - lo).days + 1)}


# ---------------------------------------------------------------- 1. the maintainer's scenario
def test_two_contracts_around_a_vacation(repo):
    u = add_user(repo)
    add_contract(repo, u, date(2026, 10, 23), date(2026, 12, 15), total=220)  # A
    add_contract(repo, u, date(2026, 10, 23), date(2026, 12, 31), total=45)  # B
    add_absence(repo, u, "2026-12-15", "2027-01-10")
    # Available workdays (same set for A and B, 15 Dec onwards is vacation, no BY holiday before 25 Dec):
    #   Fri 23 Oct = 1, seven full weeks 26 Oct .. 11 Dec = 35, Mon 14 Dec = 1  ->  n = 37.
    # A: 220/37 = 5.9459 h a day, B: 45/37 = 1.2162 h a day, together 265/37 = 7.1622 h a day (< 8 h capacity).
    # A full week: 5 * 265/37 = 35.811 h of 40 h = 89.5 % -> below 90 % -> "ok" (the scenario fits).
    per_day_all = 265 / 37
    out = forecast(repo, 11, date(2026, 10, 19))

    w = week(out, date(2026, 10, 19))  # first partial week: only Fri 23 Oct
    assert w["capacity"] == 40
    assert w["confirmed"] == pytest.approx(per_day_all)
    assert w["status"] == "ok"

    w = week(out, date(2026, 11, 9))  # full middle week
    assert w["capacity"] == 40
    assert w["confirmed"] == pytest.approx(5 * per_day_all)  # 35.81
    assert w["status"] == "ok"

    w = week(out, date(2026, 12, 14))  # Mon 14 Dec is the only available day: 7.162 h of 8 h = 89.5 %
    assert w["capacity"] == 8
    assert w["confirmed"] == pytest.approx(per_day_all)
    assert w["status"] == "ok"

    for monday in (date(2026, 12, 21), date(2026, 12, 28)):  # vacation: nothing booked, no capacity
        w = week(out, monday)
        assert w["capacity"] == 0
        assert w["confirmed"] == pytest.approx(0)
        assert w["status"] == "ok"

    total = sum(w["confirmed"] for w in out["weeks"])
    assert total == pytest.approx(265)  # all hours are inside the 10 weeks shown


# ---------------------------------------------------------------- 2. no absence: nominal spread
def test_without_absence_the_spread_is_nominal(repo):
    u = add_user(repo)
    add_contract(repo, u, date(2026, 10, 23), date(2026, 12, 15), total=220)
    add_contract(repo, u, date(2026, 10, 23), date(2026, 12, 15), total=45)
    # workdays 23 Oct .. 15 Dec: 1 + 35 + Mon 14 + Tue 15 Dec = 38; per day (220+45)/38 = 6.9737; week 34.868 h
    out = forecast(repo, 9, date(2026, 10, 19))
    assert week(out, date(2026, 11, 9))["confirmed"] == pytest.approx(5 * 265 / 38)
    assert week(out, date(2026, 12, 14))["confirmed"] == pytest.approx(2 * 265 / 38)


# ---------------------------------------------------------------- 3. public holiday
def test_public_holiday_gets_no_hours(repo):
    u = add_user(repo)
    c = add_contract(repo, u, date(2026, 1, 5), date(2026, 1, 16), total=90)
    # Tue 6 Jan is a holiday in BY: 10 workdays - 1 = 9 available days, 10 h each (nominal would be 9 h)
    days = per_day(repo, c, date(2026, 1, 5), date(2026, 1, 16), today=date(2026, 1, 5))
    assert days[date(2026, 1, 6)] == 0
    assert days[date(2026, 1, 5)] == pytest.approx(10)
    assert days[date(2026, 1, 16)] == pytest.approx(10)
    out = forecast(repo, 2, date(2026, 1, 5))
    assert week(out, date(2026, 1, 5))["confirmed"] == pytest.approx(40)  # 4 days
    assert week(out, date(2026, 1, 12))["confirmed"] == pytest.approx(50)  # 5 days


# ---------------------------------------------------------------- 4. sick / other treated like vacation
def test_sick_and_other_absences_are_not_available_days(repo):
    u = add_user(repo)
    c = add_contract(repo, u, MON, date(2026, 2, 13), total=80)
    add_absence(repo, u, "2026-02-04", "2026-02-05", kind="sick")  # Wed, Thu
    add_absence(repo, u, "2026-02-09", "2026-02-09", kind="other")  # Mon
    # 10 workdays - 3 = 7 available days, 80/7 = 11.4286 h each
    days = per_day(repo, c, MON, date(2026, 2, 13), today=MON)
    for x in (date(2026, 2, 4), date(2026, 2, 5), date(2026, 2, 9)):
        assert days[x] == 0
    assert days[date(2026, 2, 2)] == pytest.approx(80 / 7)
    out = forecast(repo, 2, MON)
    assert week(out, MON)["confirmed"] == pytest.approx(3 * 80 / 7)  # Mon, Tue, Fri
    assert week(out, date(2026, 2, 9))["confirmed"] == pytest.approx(4 * 80 / 7)  # Tue-Fri


# ---------------------------------------------------------------- 5. two assignees
def test_each_assignee_spreads_his_share_over_his_own_available_days(repo):
    a, b = add_user(repo, "Ann"), add_user(repo, "Bob", role="staff")
    c = add_contract(repo, [a, b], MON, date(2026, 2, 27), total=100)
    add_absence(repo, b, "2026-02-16", "2026-02-27")  # Bob: second half off
    # 20 workdays. Ann: 50 h / 20 = 2.5 h a day. Bob: 50 h over his 10 available days (2-13 Feb) = 5 h a day.
    ann = per_day(repo, c, MON, date(2026, 2, 27), user=a, today=MON)
    bob = per_day(repo, c, MON, date(2026, 2, 27), user=b, today=MON)
    assert ann[date(2026, 2, 3)] == pytest.approx(2.5) and ann[date(2026, 2, 24)] == pytest.approx(2.5)
    assert bob[date(2026, 2, 3)] == pytest.approx(5.0)
    assert bob[date(2026, 2, 17)] == 0
    assert sum(ann.values()) == pytest.approx(50) and sum(bob.values()) == pytest.approx(50)
    out = PlanningService(repo).forecast(4, b["id"], True, today=MON)
    assert week(out, MON)["confirmed"] == pytest.approx(25)
    assert week(out, date(2026, 2, 16))["confirmed"] == pytest.approx(0)


# ---------------------------------------------------------------- 6. no available day: fallback
def test_period_fully_inside_a_vacation_falls_back_to_nominal(repo):
    u = add_user(repo)
    add_contract(repo, u, MON, date(2026, 2, 13), total=80)
    add_absence(repo, u, "2026-02-01", "2026-02-15")
    # No available day -> nominal spread over the 10 workdays: 8 h a day, 40 h a week, capacity 0 -> over
    out = forecast(repo, 2, MON)
    for monday in (MON, date(2026, 2, 9)):
        w = week(out, monday)
        assert w["confirmed"] == pytest.approx(40)
        assert w["capacity"] == 0
        assert w["status"] == "over"


# ---------------------------------------------------------------- 7. hours_per_week unchanged
def test_hours_per_week_ignores_absences(repo):
    u = add_user(repo)
    add_contract(repo, u, MON, date(2026, 2, 20), hpw=20)
    add_absence(repo, u, "2026-02-09", "2026-02-13")
    out = forecast(repo, 3, MON)
    assert week(out, MON)["confirmed"] == pytest.approx(20)
    w = week(out, date(2026, 2, 9))
    assert w["confirmed"] == pytest.approx(20) and w["capacity"] == 0 and w["status"] == "over"
    assert week(out, date(2026, 2, 16))["confirmed"] == pytest.approx(20)


# ---------------------------------------------------------------- 8. what-if
def test_simulated_vacation_moves_the_hours(repo):
    u = add_user(repo)
    add_contract(repo, u, MON, date(2026, 2, 27), total=80)  # 20 workdays: 4 h a day, 20 h a week
    s = PlanningService(repo)
    base = s.simulate({}, 4, MON, None, today=MON)
    assert [w["confirmed"] for w in base["weeks"]] == [pytest.approx(20)] * 4
    draft = {"absences": [{"user_id": u["id"], "start": "2026-02-16", "end": "2026-02-20"}]}
    sim = s.simulate(draft, 4, MON, None, today=MON)
    # 15 available days -> 80/15 = 5.3333 h a day: 26.667 h in each working week, none in the vacation week
    got = [w["confirmed"] for w in sim["weeks"]]
    assert got == [pytest.approx(80 / 3), pytest.approx(80 / 3), pytest.approx(0), pytest.approx(80 / 3)]
    assert sim["weeks"][2]["vacation_hours"] == 40
    assert sum(got) == pytest.approx(80)


# ---------------------------------------------------------------- 9. invariant
@settings(max_examples=40, deadline=None)
@given(
    start=st.integers(0, 20),
    length=st.integers(1, 30),
    total=st.integers(1, 500),
    ranges=st.lists(st.tuples(st.integers(0, 55), st.integers(0, 10)), max_size=3),
)
def test_spread_sums_to_total_and_skips_absences(start, length, total, ranges):
    # window 2 Feb .. 31 Mar 2026 has no public holiday in BY
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        try:
            u = add_user(repo)
            lo = MON + timedelta(days=start)
            hi = lo + timedelta(days=length - 1)
            c = add_contract(repo, u, lo, hi, total=total)
            absent = set()
            for off, ln in ranges:
                a = MON + timedelta(days=off)
                b = a + timedelta(days=ln)
                add_absence(repo, u, a.isoformat(), b.isoformat())
                absent |= {a + timedelta(days=i) for i in range(ln + 1)}
            period = [lo + timedelta(days=i) for i in range(length)]
            avail = [x for x in period if x.weekday() < 5 and x not in absent]
            if not avail:
                return  # fallback case, covered by its own test
            days = per_day(repo, c, MON, MON + timedelta(days=70), today=MON)
            assert sum(days.values()) == pytest.approx(total, abs=1e-9)
            assert all(days[x] == 0 for x in absent if x in days)
            assert all(days[x] == pytest.approx(total / len(avail)) for x in avail)
        finally:
            repo.close()


def test_a_window_narrower_than_the_period_keeps_the_per_day_load(repo):
    # contract 2-27 Feb 2026 (20 workdays), 80 h, vacation 11-13 Feb -> 17 available days -> 80/17 h each, whatever window
    # is asked for (the denominator is the whole period, not the window)
    u = add_user(repo)
    c = add_contract(repo, u, MON, date(2026, 2, 27), total=80)
    add_absence(repo, u, "2026-02-11", "2026-02-13")
    days = per_day(repo, c, date(2026, 2, 9), date(2026, 2, 15), today=MON)
    assert days[date(2026, 2, 9)] == pytest.approx(80 / 17)
    assert days[date(2026, 2, 10)] == pytest.approx(80 / 17)
    assert all(days.get(date(2026, 2, d), 0) == 0 for d in (11, 12, 13, 14, 15))
    assert sum(days.values()) == pytest.approx(2 * 80 / 17)
