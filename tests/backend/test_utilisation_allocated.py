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


def test_allocation_without_demand_under_capacity_is_ok_at_37_5_percent(w):
    # 15 h allocated to A, no demand: used 15 of 40 h = 37.5 %, status ok, no capacity.over warning.
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


def test_e4_vacation_inside_allocations_is_125_percent_for_each_project_and_the_team(w):
    # Spec E4: P and Q allocated 20 h/week each, vacation Wednesday 2026-03-04, contract of 20 h/week on P.
    # Real capacity is 8 h on four days, so each allocation keeps 16 h (scaled); used: P max(20, 20) = 20, Q 20.
    w.alloc(w.a, 20.0)
    w.alloc(w.b, 20.0)
    w.contract(w.a, 20.0)
    w.repo.save("absences", dict(user_id=w.boss["id"], kind="vacation", start="2026-03-04", end="2026-03-04"))
    week = w.forecast()["weeks"][0]
    assert (week["capacity"], week["allocated"], week["confirmed"]) == (32.0, 40.0, 20.0)
    assert (week["used"], week["utilisation"], week["status"]) == (40.0, 125.0, "over")
    p, q = w.scope(week, w.a["id"]), w.scope(week, w.b["id"])
    assert (p["capacity"], p["allocated"], p["utilisation"]) == (16.0, 20.0, 125.0)
    assert (q["capacity"], q["allocated"], q["confirmed"], q["utilisation"]) == (16.0, 20.0, 0.0, 125.0)
    # the next week has no vacation: P and Q keep 20 h each, used 20 of 20 = 100.0 % (tight)
    nxt = w.forecast(weeks=2)["weeks"][1]
    assert (nxt["capacity"], nxt["utilisation"], nxt["status"]) == (40.0, 100.0, "tight")


def test_user_filter_counts_only_that_persons_allocations(w, repo):
    erika = repo.save(
        "users",
        dict(
            username="erika",
            name="Erika Beispiel",
            role="employee",
            weekly_hours=32,
            workdays=[0, 1, 2, 3],
            active=True,
        ),
    )
    repo.save(
        "allocations",
        dict(user_id=erika["id"], project_id=w.a["id"], start=START, end=END, hours_per_week=16.0, status="active"),
    )
    repo.save(
        "contracts",
        dict(
            title="E",
            start=START,
            end=END,
            rate=100,
            status="confirmed",
            user_ids=[erika["id"]],
            hours_per_week=8.0,
            project_id=w.a["id"],
        ),
    )
    # Erika: 32 h over Mon-Thu, 16 h allocated (4 h a day), 8 h of contract (2 h a day): used 16 of 32 = 50.0 %
    e = w.forecast(user_id=erika["id"])["weeks"][0]
    assert (e["capacity"], e["allocated"], e["confirmed"], e["used"]) == (32.0, 16.0, 8.0, 16.0)
    assert (e["utilisation"], e["status"]) == (50.0, "ok")
    # the boss has no allocation and no contract in this world: nothing of Erika's counts for the boss
    boss = w.forecast(user_id=w.boss["id"])["weeks"][0]
    assert (boss["capacity"], boss["allocated"], boss["utilisation"]) == (40.0, 0.0, 0.0)


def test_binding_offer_counts_as_demand_next_to_allocations(w):
    # 20 h/week allocated to A and an open (sent) offer of 30 h/week on A: worst case used = max(30, 20) = 30.
    w.alloc(w.a, 20.0)
    w.repo.save(
        "offers",
        dict(
            number="AN-1",
            status="sent",
            lines=[],
            project_id=w.a["id"],
            valid_until="2026-12-31",
            plan=dict(start=START, end=END, hours_per_week=30.0, user_ids=[w.boss["id"]], win_probability=50),
        ),
    )
    week = w.forecast()["weeks"][0]
    assert (week["confirmed"], week["binding_offers"], week["allocated"]) == (0.0, 30.0, 20.0)
    assert (week["utilisation"], week["status"], week["gap"]) == (75.0, "over", 10.0)
    a = w.scope(week, w.a["id"])
    assert (a["capacity"], a["utilisation"], a["status"]) == (20.0, 150.0, "over")


def test_unassigned_demand_next_to_an_allocation(w):
    # 20 h/week allocated to A leaves 20 h unassigned; a contract without project of 30 h/week draws on it.
    w.alloc(w.a, 20.0)
    w.contract(None, 30.0)
    week = w.forecast()["weeks"][0]
    un, a = w.scope(week, None), w.scope(week, w.a["id"])
    assert (un["capacity"], un["confirmed"], un["allocated"]) == (20.0, 30.0, 0.0)
    assert (un["utilisation"], un["status"], un["gap"]) == (150.0, "over", 10.0)
    # an idle internal allocation counts as used (decision D4): A uses its 20 h of 20 h = 100.0 %, tight
    assert (a["capacity"], a["allocated"], a["utilisation"], a["status"]) == (20.0, 20.0, 100.0, "tight")
    # team: unassigned 30 h + A 20 h = 50 h used of 40 h capacity
    assert (week["utilisation"], week["used"]) == (125.0, 50.0)


def test_what_if_allocation_change_moves_the_utilisation(w):
    # Draft: the allocation to A goes from 20 h to 40 h. Used: max(30, 40) = 40 of 40 h = 100.0 %, tight, no gap.
    alloc = w.alloc(w.a, 20.0)
    w.contract(w.a, 30.0)
    assert w.forecast()["weeks"][0]["utilisation"] == 75.0
    sim = w.svc.simulate({"allocations": [{"id": alloc["id"], "hours_per_week": 40.0}]}, 1, None, None, today=TODAY)
    week = sim["weeks"][0]
    assert (week["allocated"], week["capacity"], week["utilisation"], week["status"]) == (40.0, 40.0, 100.0, "tight")
    assert week["gap"] == 0.0


def test_by_project_zero_warns_when_allocations_overload_and_names_no_negative_hours(w):
    # Spec E1 without the project dimension: used 30 + 15 = 45 of 40 h. The warning must say why and by how much,
    # never "over by -0 h" (the day maxima exceed the weekly commitments here).
    w.alloc(w.a, 25.0)
    w.alloc(w.b, 15.0)
    w.contract(w.a, 30.0)
    w.contract(w.b, 10.0)
    out = w.forecast(weeks=4, by_project=False)
    warnings = [x for x in out["warnings"] if x["code"] == "capacity.over"]
    assert len(warnings) == 4
    first = warnings[0]
    assert first["details"]["over_by"] == 5.0
    assert first["message"] == (
        "Week of 2026-03-02: the days' allocations and commitments exceed capacity: 45 h used for 40 h of capacity "
        "(over by 5 h)."
    )
    assert all(x["details"]["over_by"] > 0 for x in warnings)
    assert "-0 h" not in first["message"] and "by -" not in first["message"]


def test_by_project_zero_without_allocations_keeps_the_old_rule(w):
    # Regression (no allocations): 38 h committed of 40 h is 95.0 % tight, so no capacity.over warning.
    w.contract(None, 38.0)
    out = w.forecast(by_project=False)
    assert out["weeks"][0]["utilisation"] == 95.0
    assert not [x for x in out["warnings"] if x["code"] == "capacity.over"]


def test_rounding_boundary_at_100_04_percent(w):
    # 40.016 h allocated over 40 h capacity: 100.04 % unrounded. The row shows 100.0 but the status is over (the
    # unrounded rule of status_for, also used by the warning), and the warning says "over by 0.02 h".
    w.alloc(w.a, 40.016)
    week = w.forecast(weeks=4, by_project=False)["weeks"][0]
    assert (week["capacity"], week["used"], week["utilisation"], week["status"]) == (40.0, 40.016, 100.0, "over")
    warnings = [x for x in w.forecast(weeks=4, by_project=False)["warnings"] if x["code"] == "capacity.over"]
    assert len(warnings) == 4 and warnings[0]["details"]["over_by"] == 0.02
    assert warnings[0]["message"] == (
        "Week of 2026-03-02: 40.02 h allocated and 0 h committed for 40 h of capacity (over by 0.02 h)."
    )


def test_rounding_boundary_at_exactly_100_percent_is_not_over(w):
    w.alloc(w.a, 40.0)
    week = w.forecast(by_project=False)["weeks"][0]
    assert (week["utilisation"], week["status"]) == (100.0, "tight")
    assert not [x for x in w.forecast(weeks=4, by_project=False)["warnings"] if x["code"] == "capacity.over"]
