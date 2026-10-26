"""Utilisation counts the allocations (decision D4 of docs/specs/planner-per-project.md).

Per day and scope: used = max(demand, allocation); the allocation is UNSCALED (hours per week / available days).
Team utilisation = sum of the used hours / team capacity; project utilisation = used / the project's scaled capacity.

Every expected number below is written down from the hand computation in the spec (oracle a to e); nothing is
recomputed with the code under test. Calendar: boss "Demo Boss", 40 h/week, Mon-Fri (8 h/day), no absences, no calendar.
Weeks from Monday 2026-03-02 (no public holiday). The holiday week is Monday 2026-11-16 in Saxony (SN): Wednesday
2026-11-18 is Buss- und Bettag, so that week has 32 h of real capacity.
"""

from datetime import date

import pytest

from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.plan_service import PlanningService

TODAY = date(2026, 3, 2)
START, END = "2026-03-02", "2026-03-27"


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "data")
    r.startup()
    yield r
    r.close()


class World:
    def __init__(self, repo):
        self.repo = repo
        self.boss = repo.save(
            "users",
            dict(
                username="boss", name="Demo Boss", role="boss", weekly_hours=40, workdays=[0, 1, 2, 3, 4], active=True
            ),
        )
        self.a = repo.save("projects", dict(name="Demo project A", status="active"))
        self.b = repo.save("projects", dict(name="Demo project B", status="active"))
        self.svc = PlanningService(repo)

    def alloc(self, project, hpw, start=START, end=END):
        return self.repo.save(
            "allocations",
            dict(
                user_id=self.boss["id"],
                project_id=project["id"],
                start=start,
                end=end,
                hours_per_week=hpw,
                status="active",
            ),
        )

    def contract(self, project, hpw, start=START, end=END):
        rec = dict(
            title="C",
            start=start,
            end=end,
            rate=100,
            status="confirmed",
            user_ids=[self.boss["id"]],
            hours_per_week=hpw,
        )
        if project:
            rec["project_id"] = project["id"]
        return self.repo.save("contracts", rec)

    def forecast(self, weeks=1, today=TODAY, **kw):
        return self.svc.forecast(weeks, today=today, **kw)

    @staticmethod
    def scope(week, project_id):
        return next(p for p in week["projects"] if p["project_id"] == project_id)


@pytest.fixture
def w(repo):
    return World(repo)


def test_oracle_a_over_allocation_counts_as_use(w):
    # 30 h/week to A and 25 h/week to B, no contracts. Allocated 55 of 40 h capacity.
    w.alloc(w.a, 30.0)
    w.alloc(w.b, 25.0)
    out = w.forecast()
    week = out["weeks"][0]
    assert (week["capacity"], week["allocated"], week["confirmed"], week["gap"]) == (40.0, 55.0, 0.0, 0.0)
    assert (week["utilisation"], week["status"]) == (137.5, "over")  # 55 / 40
    a, b, un = w.scope(week, w.a["id"]), w.scope(week, w.b["id"]), w.scope(week, None)
    # scaled capacity: 40 * 30 / 55 = 21.8 for A, 40 * 25 / 55 = 18.2 for B; used = the unscaled allocation
    assert a["allocated"] == 30.0 and a["capacity"] == pytest.approx(21.8, abs=0.05)
    assert b["allocated"] == 25.0 and b["capacity"] == pytest.approx(18.2, abs=0.05)
    assert (a["utilisation"], a["status"], a["gap"]) == (137.5, "over", 0.0)
    assert (b["utilisation"], b["status"], b["gap"]) == (137.5, "over", 0.0)
    assert (un["capacity"], un["allocated"], un["utilisation"], un["status"]) == (0.0, 0.0, 0.0, "ok")


def test_oracle_b_allocation_and_contract_on_one_project(w):
    # 20 h/week allocated to A, a contract of 30 h/week on A: used_A = max(30, 20) = 30 per week.
    w.alloc(w.a, 20.0)
    w.contract(w.a, 30.0)
    week = w.forecast()["weeks"][0]
    assert (week["capacity"], week["allocated"], week["confirmed"]) == (40.0, 20.0, 30.0)
    assert (week["utilisation"], week["status"]) == (75.0, "over")  # 30 / 40, over only by the gap
    assert week["gap"] == pytest.approx(10.0)  # 6 h a day demanded, 4 h a day allocated, five days
    a, un = w.scope(week, w.a["id"]), w.scope(week, None)
    assert (a["capacity"], a["allocated"], a["gap"]) == (20.0, 20.0, 10.0)
    assert (a["utilisation"], a["status"]) == (150.0, "over")  # used 30 of 20
    assert (un["capacity"], un["allocated"], un["confirmed"], un["utilisation"]) == (20.0, 0.0, 0.0, 0.0)


def test_oracle_c_no_allocation_keeps_the_old_utilisation(w):
    # Regression: a contract of 36 h/week without any allocation is 90.0 % (the numbers of the old rule).
    w.contract(None, 36.0)
    week = w.forecast()["weeks"][0]
    assert (week["capacity"], week["confirmed"], week["allocated"]) == (40.0, 36.0, 0.0)
    assert (week["utilisation"], week["status"], week["gap"]) == (90.0, "ok", 0.0)
    un = w.scope(week, None)
    assert (un["capacity"], un["utilisation"], un["status"]) == (40.0, 90.0, "ok")


def test_oracle_d_full_allocation_is_one_hundred_percent_not_over(w):
    # 40 h allocated to A, a contract of 10 h on A: used = max(10, 40) = 40 of 40.
    w.alloc(w.a, 40.0)
    w.contract(w.a, 10.0)
    week = w.forecast()["weeks"][0]
    assert (week["capacity"], week["allocated"], week["confirmed"], week["gap"]) == (40.0, 40.0, 10.0, 0.0)
    assert (week["utilisation"], week["status"]) == (100.0, "tight")
    a = w.scope(week, w.a["id"])
    assert (a["capacity"], a["utilisation"], a["status"], a["gap"]) == (40.0, 100.0, "tight", 0.0)


def test_holiday_week_allocation_spread_over_four_days(w):
    # Week of Monday 2026-11-16 in Saxony: Wednesday 2026-11-18 is a public holiday (Buss- und Bettag).
    # 40 h/week to A is spread over the four available days: 10 h each, unscaled. Real capacity 32 h (8 h x 4).
    w.repo.save_settings({**w.repo.settings(), "state": "SN"})
    w.alloc(w.a, 40.0, start="2026-11-16", end="2026-11-22")
    week = w.forecast(weeks=1, today=date(2026, 11, 16))["weeks"][0]
    assert week["week"] == "2026-11-16" and week["holiday_hours"] == 8.0
    assert (week["capacity"], week["allocated"]) == (32.0, 40.0)
    assert (week["utilisation"], week["status"]) == (125.0, "over")  # 40 / 32
    a = w.scope(week, w.a["id"])
    assert (a["capacity"], a["allocated"], a["utilisation"]) == (32.0, 40.0, 125.0)


def test_status_and_gap_of_the_week_are_still_the_same_rule(w):
    # Two allocations under capacity and no demand: the week is ok, nothing is allocated over the capacity.
    w.alloc(w.a, 15.0)
    week = w.forecast()["weeks"][0]
    assert (week["capacity"], week["allocated"], week["utilisation"], week["status"]) == (40.0, 15.0, 37.5, "ok")


def test_scope_filter_reports_the_scope_only(w):
    w.alloc(w.a, 30.0)
    w.alloc(w.b, 25.0)
    out = w.forecast(project_id=w.a["id"])
    week = out["weeks"][0]
    assert [p["project_id"] for p in week["projects"]] == [w.a["id"]]
    assert week["allocated"] == 30.0 and week["capacity"] == pytest.approx(40 * 30 / 55)
    assert week["utilisation"] == pytest.approx(137.5)


def test_flat_forecast_is_unchanged_in_the_gap_and_the_capacity(w):
    w.alloc(w.a, 30.0)
    w.alloc(w.b, 25.0)
    w.contract(w.a, 10.0)
    fine = w.forecast()["weeks"][0]
    flat = w.forecast(by_project=False)["weeks"][0]
    assert "projects" not in flat
    assert (flat["capacity"], flat["allocated"], flat["gap"], flat["utilisation"]) == (
        fine["capacity"],
        fine["allocated"],
        fine["gap"],
        fine["utilisation"],
    )
