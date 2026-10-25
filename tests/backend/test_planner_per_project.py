"""The planner per project: the hand-computed oracle cases E1 to E8 of docs/specs/planner-per-project.md.

Every number below is written down from the hand computation in the spec; nothing is recomputed with the code under
test. Calendar: today = Monday 2026-03-02, no public holiday between 2026-03-02 and 2026-03-27, Good Friday 2026-04-03
is a national holiday. Boss "Demo Boss": 40 h/week, Mon-Fri (8 h/day). "Erika Beispiel": 32 h/week, Mon-Thu.
"""

from datetime import date, timedelta

import pytest

from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service import planning as eng
from permita38.core.service.allocations import AllocationService
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
    """Fixture builder on a bare repository (no HTTP): users, projects, contracts, offers and allocations."""

    def __init__(self, repo):
        self.repo = repo
        self.boss = repo.save(
            "users",
            dict(
                username="boss", name="Demo Boss", role="boss", weekly_hours=40, workdays=[0, 1, 2, 3, 4], active=True
            ),
        )
        self.p = repo.save("projects", dict(name="Demo project P", status="active"))
        self.q = repo.save("projects", dict(name="Demo project Q", status="active"))
        self.svc = PlanningService(repo)

    def erika(self):
        return self.repo.save(
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

    def alloc(self, user, project, hpw, start=START, end=END):
        return self.repo.save(
            "allocations",
            dict(
                user_id=user["id"], project_id=project["id"], start=start, end=end, hours_per_week=hpw, status="active"
            ),
        )

    def contract(self, user, project, hpw=None, total=None, start=START, end=END, title="C"):
        rec = dict(
            title=title,
            start=start,
            end=end,
            rate=100,
            status="confirmed",
            user_ids=[user["id"]],
            hours_per_week=hpw,
            total_hours=total,
        )
        if project:
            rec["project_id"] = project["id"]
        return self.repo.save("contracts", rec)

    def offer(self, user, project, hpw, win=50, start=START, end=END):
        plan = dict(start=start, end=end, hours_per_week=hpw, user_ids=[user["id"]], win_probability=win)
        return self.repo.save(
            "offers",
            dict(number="AN-1", status="sent", plan=plan, project_id=project["id"], lines=[], valid_until="2026-12-31"),
        )

    def forecast(self, weeks=4, **kw):
        return self.svc.forecast(weeks, today=TODAY, **kw)

    def entry(self, out, week_idx, project):
        key = project["id"] if project else None
        return next(p for p in out["weeks"][week_idx]["projects"] if p["project_id"] == key)


@pytest.fixture
def w(repo):
    return World(repo)


def pick(d, *keys):
    return {k: d[k] for k in keys}


# ---------------------------------------------------------------------------------------------------- E1
def test_e1_two_projects_one_person_no_netting(w):
    w.alloc(w.boss, w.p, 25)
    w.alloc(w.boss, w.q, 15)
    w.contract(w.boss, w.p, hpw=30, title="Contract C_P")
    w.contract(w.boss, w.q, hpw=10, title="Contract C_Q")
    out = w.forecast()
    for i in range(4):
        week = out["weeks"][i]
        assert pick(week, "capacity", "confirmed", "binding_offers", "utilisation", "status", "gap") == dict(
            capacity=40.0, confirmed=40.0, binding_offers=0.0, utilisation=100.0, status="over", gap=5.0
        )
        assert week["gap_confirmed"] == 5.0 and week["gap_expected"] == 5.0
        assert week["free_after_confirmed"] == 0.0 and week["free_worst_case"] == 0.0
        p, q, un = w.entry(out, i, w.p), w.entry(out, i, w.q), w.entry(out, i, None)
        assert pick(p, "capacity", "confirmed", "free_worst_case", "utilisation", "status", "gap") == dict(
            capacity=25.0, confirmed=30.0, free_worst_case=-5.0, utilisation=120.0, status="over", gap=5.0
        )
        assert p["internal"] == dict(capacity=25.0, assigned=30.0, gap=5.0)
        assert p["external"] == dict(capacity=0.0, assigned=0.0, gap=0.0)
        assert pick(q, "capacity", "confirmed", "free_worst_case", "utilisation", "status", "gap") == dict(
            capacity=15.0, confirmed=10.0, free_worst_case=5.0, utilisation=66.7, status="ok", gap=0.0
        )
        assert pick(un, "capacity", "confirmed", "utilisation", "status", "gap") == dict(
            capacity=0.0, confirmed=0.0, utilisation=0.0, status="ok", gap=0.0
        )
        assert [x["name"] for x in week["projects"]] == ["Demo project P", "Demo project Q", "Unassigned"]
    assert out["totals"]["gap"] == 20.0 and out["totals"]["over_weeks"] == 4
    assert out["totals"]["gap_by_project"] == [dict(project_id=w.p["id"], name="Demo project P", gap=20.0)]
    gaps = [x for x in out["warnings"] if x["code"] == "capacity.gap"]
    assert len(gaps) == 4 and not [x for x in out["warnings"] if x["code"] == "allocation.no_demand"]
    d = gaps[0]["details"]
    assert pick(d, "project_id", "project_name", "week", "pool", "assigned", "capacity", "gap") == dict(
        project_id=w.p["id"],
        project_name="Demo project P",
        week="2026-03-02",
        pool="internal",
        assigned=30.0,
        capacity=25.0,
        gap=5.0,
    )
    assert [(c["title"], c["hours"], c["binding"]) for c in d["contributions"]] == [("Contract C_P", 30.0, False)]
    assert gaps[0]["message"] == (
        "Week of 2026-03-02, project Demo project P: 30 h are planned but only 25 h are allocated to it (gap 5 h). "
        "Other projects' free hours do not cover it."
    )


def test_e1_day_table(w):
    w.alloc(w.boss, w.p, 25)
    w.alloc(w.boss, w.q, 15)
    w.contract(w.boss, w.p, hpw=30)
    w.contract(w.boss, w.q, hpw=10)
    t = w.svc.day_table(date(2026, 3, 2), date(2026, 3, 2), today=TODAY)
    day = t["days"][0]
    assert pick(day, "cap", "confirmed", "binding", "expected") == dict(
        cap=8.0, confirmed=8.0, binding=0.0, expected=0.0
    )
    zero = dict(cap=0.0, assigned=0.0, confirmed=0.0, binding=0.0, expected=0.0, gap=0.0, gap_confirmed=0.0)
    assert day["scopes"][w.p["id"]] == {
        "internal": dict(cap=5.0, assigned=6.0, confirmed=6.0, binding=0.0, expected=0.0, gap=1.0, gap_confirmed=1.0),
        "external": zero,
    }
    assert day["scopes"][w.q["id"]]["internal"] == dict(
        cap=3.0, assigned=2.0, confirmed=2.0, binding=0.0, expected=0.0, gap=0.0, gap_confirmed=0.0
    )
    assert "unassigned" not in day["scopes"]
    assert t["scope_names"] == {w.p["id"]: "Demo project P", w.q["id"]: "Demo project Q"}
    only_q = w.svc.day_table(date(2026, 3, 2), date(2026, 3, 8), project_id=w.q["id"], today=TODAY)
    assert len(only_q["days"]) == 7 and all(set(d["scopes"]) <= {w.q["id"]} for d in only_q["days"])
    assert only_q["days"][5]["cap"] == 0.0  # weekend: no capacity


# ---------------------------------------------------------------------------------------------------- E2
def test_e2_existing_data_without_allocations_then_bootstrap(w):
    c_p = w.contract(w.boss, w.p, hpw=30, title="Contract C_P")
    w.contract(w.boss, None, hpw=8, title="Contract N")
    out = w.forecast()
    for i in range(4):
        week = out["weeks"][i]
        assert pick(
            week, "capacity", "confirmed", "free_after_confirmed", "free_worst_case", "utilisation", "status", "gap"
        ) == dict(
            capacity=40.0,
            confirmed=38.0,
            free_after_confirmed=2.0,
            free_worst_case=2.0,
            utilisation=95.0,
            status="over",
            gap=30.0,
        )
        p, un = w.entry(out, i, w.p), w.entry(out, i, None)
        assert pick(p, "capacity", "confirmed", "free_worst_case", "utilisation", "status", "gap") == dict(
            capacity=0.0, confirmed=30.0, free_worst_case=-30.0, utilisation=None, status="over", gap=30.0
        )
        assert pick(un, "capacity", "confirmed", "free_worst_case", "utilisation", "status", "gap") == dict(
            capacity=40.0, confirmed=8.0, free_worst_case=32.0, utilisation=20.0, status="ok", gap=0.0
        )
    assert out["totals"]["gap"] == 120.0 and out["totals"]["over_weeks"] == 4

    svc = AllocationService(w.repo)
    preview = svc.bootstrap(False, TODAY)
    assert preview["confirm"] is False and preview["created"] is False and preview["skipped"] == []
    assert preview["allocations"] == [
        dict(
            user_id=w.boss["id"],
            user_name="Demo Boss",
            project_id=w.p["id"],
            project_name="Demo project P",
            start=START,
            end=END,
            hours_per_week=30.0,
            source_contract_id=c_p["id"],
            source_contract_title="Contract C_P",
        )
    ]
    assert w.repo.list("allocations") == []  # a preview writes nothing
    done = svc.bootstrap(True, TODAY)
    assert done["created"] is True and done["confirm"] is True and len(done["allocations"]) == 1
    assert {k: v for k, v in done["allocations"][0].items() if k not in ("id",)} == {
        **preview["allocations"][0],
        "kind": "employee",
    }
    saved = w.repo.list("allocations")
    assert len(saved) == 1 and saved[0]["hours_per_week"] == 30.0 and saved[0]["user_id"] == w.boss["id"]
    again = svc.bootstrap(True, TODAY)
    assert again["allocations"] == [] and again["created"] is True
    assert [s["reason"] for s in again["skipped"]] == ["already_allocated"]
    assert len(w.repo.list("allocations")) == 1

    out = w.forecast()
    week = out["weeks"][0]
    assert pick(week, "capacity", "confirmed", "utilisation", "status", "gap") == dict(
        capacity=40.0, confirmed=38.0, utilisation=95.0, status="tight", gap=0.0
    )
    assert pick(w.entry(out, 0, w.p), "capacity", "confirmed", "utilisation", "status", "gap") == dict(
        capacity=30.0, confirmed=30.0, utilisation=100.0, status="tight", gap=0.0
    )
    assert pick(w.entry(out, 0, None), "capacity", "confirmed", "free_worst_case", "utilisation", "status") == dict(
        capacity=10.0, confirmed=8.0, free_worst_case=2.0, utilisation=80.0, status="ok"
    )
    assert out["totals"]["gap"] == 0.0 and not [x for x in out["warnings"] if x["code"] == "capacity.gap"]


def test_e2_bootstrap_rounds_the_peak_up_to_a_quarter_hour_and_skips_closed_projects(w):
    w.contract(w.boss, w.p, hpw=10.1)
    done = w.repo.save("projects", dict(name="Demo done", status="done"))
    w.contract(w.boss, done, hpw=5)
    w.contract(w.boss, None, hpw=5)
    res = AllocationService(w.repo).bootstrap(False, TODAY)
    assert [(a["project_id"], a["hours_per_week"]) for a in res["allocations"]] == [(w.p["id"], 10.25)]


# ---------------------------------------------------------------------------------------------------- E3
def test_e3_over_allocation_is_scaled_down_pro_rata(w):
    w.alloc(w.boss, w.p, 30)
    w.alloc(w.boss, w.q, 20)
    w.contract(w.boss, w.p, hpw=30)
    w.contract(w.boss, w.q, hpw=20)
    out = w.forecast()
    week = out["weeks"][0]
    assert pick(week, "capacity", "confirmed", "gap") == dict(capacity=40.0, confirmed=50.0, gap=10.0)
    p, q = w.entry(out, 0, w.p), w.entry(out, 0, w.q)
    assert pick(p, "capacity", "gap", "confirmed") == dict(capacity=24.0, gap=6.0, confirmed=30.0)
    assert pick(q, "capacity", "gap", "confirmed") == dict(capacity=16.0, gap=4.0, confirmed=20.0)
    assert p["capacity"] + q["capacity"] == week["capacity"]  # nothing invented
    over = [x for x in out["warnings"] if x["code"] == "allocation.employee_over_allocated"]
    assert len(over) == 4
    assert pick(over[0]["details"], "user_id", "week", "allocated", "weekly_hours", "over_by", "reason") == dict(
        user_id=w.boss["id"], week="2026-03-02", allocated=50.0, weekly_hours=40.0, over_by=10.0, reason="weekly_hours"
    )
    assert over[0]["confirmable"] is True and over[0]["severity"] == "warning"


def test_e3_saving_the_allocation_warns_but_saves(w):
    svc = AllocationService(w.repo)
    svc.create(dict(user_id=w.boss["id"], project_id=w.p["id"], start=START, end=END, hours_per_week=30))
    res = svc.create(dict(user_id=w.boss["id"], project_id=w.q["id"], start=START, end=END, hours_per_week=20))
    warns = [x for x in res["planning_warnings"] if x["code"] == "allocation.employee_over_allocated"]
    assert [x["details"]["week"] for x in warns] == ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23"]
    assert warns[0]["details"]["over_by"] == 10.0 and warns[0]["field"] == "allocations"
    assert len(w.repo.list("allocations")) == 2


# ---------------------------------------------------------------------------------------------------- E4
def test_e4_vacation_inside_allocations(w):
    w.alloc(w.boss, w.p, 20)
    w.alloc(w.boss, w.q, 20)
    w.repo.save("absences", dict(user_id=w.boss["id"], kind="vacation", start="2026-03-04", end="2026-03-04"))
    w.contract(w.boss, w.p, hpw=20)
    out = w.forecast()
    p, q = w.entry(out, 0, w.p), w.entry(out, 0, w.q)
    assert pick(p, "capacity", "confirmed", "gap", "status") == dict(
        capacity=16.0, confirmed=20.0, gap=4.0, status="over"
    )
    assert pick(q, "capacity", "confirmed", "free_worst_case") == dict(
        capacity=16.0, confirmed=0.0, free_worst_case=16.0
    )
    for i in (1, 2, 3):
        p = w.entry(out, i, w.p)
        assert pick(p, "capacity", "confirmed", "gap", "utilisation", "status") == dict(
            capacity=20.0, confirmed=20.0, gap=0.0, utilisation=100.0, status="tight"
        )
    no_demand = [x for x in out["warnings"] if x["code"] == "allocation.no_demand"]
    assert no_demand[0]["severity"] == "info" and no_demand[0]["details"]["project_id"] == w.q["id"]
    assert no_demand[0]["details"]["week"] == "2026-03-02" and no_demand[0]["details"]["capacity"] == 16.0
    over = [x for x in out["warnings"] if x["code"] == "allocation.employee_over_allocated"]
    assert len(over) == 1  # only the vacation week: 40 h allocated, 32 h real
    assert pick(over[0]["details"], "week", "allocated", "weekly_hours", "over_by", "reason") == dict(
        week="2026-03-02", allocated=40.0, weekly_hours=32.0, over_by=8.0, reason="time_off"
    )


# ---------------------------------------------------------------------------------------------------- E6
def test_e6_what_if_moving_a_contract_changes_only_its_project(w):
    w.alloc(w.boss, w.p, 25)
    w.alloc(w.boss, w.q, 15)
    cp = w.contract(w.boss, w.p, hpw=30, title="Contract C_P")
    w.contract(w.boss, w.q, hpw=10, title="Contract C_Q")
    before = list(w.repo.list("contracts"))
    changes = dict(commitments=[dict(kind="contract", id=cp["id"], start="2026-03-09", end="2026-04-03")])
    base = w.svc.simulate({}, 5, TODAY, None, today=TODAY)
    sim = w.svc.simulate(changes, 5, TODAY, None, today=TODAY)
    assert sim["delta"]["gap"] == 25.0
    assert sim["delta"]["projects"] == [
        dict(project_id=w.p["id"], name="Demo project P", base_gap=20.0, gap=45.0, delta=25.0)
    ]
    assert [p["gap"] for p in (w.entry(sim, i, w.p) for i in range(5))] == [0.0, 5.0, 5.0, 5.0, 30.0]
    assert [w.entry(sim, i, w.p)["delta"] for i in range(5)] == [-5.0, 0.0, 0.0, 0.0, 30.0]
    assert [x["delta"] for x in sim["weeks"]] == [-5.0, 0.0, 0.0, 0.0, 30.0]
    for i in range(4):  # Q (allocation until 03-27) is bit-identical to the base apart from the delta
        a, b = dict(w.entry(sim, i, w.q)), dict(w.entry(base, i, w.q))
        delta_a, delta_b = a.pop("delta"), b.pop("delta")
        assert delta_a == 0.0 and delta_b == 0.0 and a == b
    for i in range(4):
        assert sim["weeks"][i]["capacity"] == 40.0
    assert w.repo.list("contracts") == before  # nothing was written


# ---------------------------------------------------------------------------------------------------- E7
def test_e7_open_offer_in_the_same_project(w):
    w.alloc(w.boss, w.p, 25)
    w.alloc(w.boss, w.q, 15)
    w.contract(w.boss, w.p, hpw=30)
    w.contract(w.boss, w.q, hpw=10)
    o = w.offer(w.boss, w.p, 10, win=50)
    out = w.forecast()
    p, q = w.entry(out, 0, w.p), w.entry(out, 0, w.q)
    assert pick(p, "gap", "gap_confirmed", "gap_expected", "status", "binding_offers") == dict(
        gap=15.0, gap_confirmed=5.0, gap_expected=10.0, status="over", binding_offers=10.0
    )
    assert pick(q, "free_worst_case", "gap") == dict(free_worst_case=5.0, gap=0.0)
    draft = dict(plan=o["plan"], project_id=w.p["id"])
    warns = [x for x in w.svc.check_offer(draft, o["id"], today=TODAY) if x["code"] == "offer.project_gap"]
    assert [x["details"]["week"] for x in warns] == ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23"]
    assert pick(warns[0]["details"], "project_id", "pool", "assigned", "capacity", "gap", "options") == dict(
        project_id=w.p["id"],
        pool="internal",
        assigned=40.0,
        capacity=25.0,
        gap=15.0,
        options=["shorten", "start_later", "allocate_more", "reduce_scope"],
    )
    assert not [x for x in w.svc.check_offer(draft, o["id"], today=TODAY) if x["code"] == "capacity.offer_over"]
    q_plan = dict(start=START, end=END, hours_per_week=5, user_ids=[w.boss["id"]])
    fits = w.svc.check_offer(dict(plan=q_plan, project_id=w.q["id"]), None, today=TODAY)
    assert [x for x in fits if x["code"] in ("offer.project_gap", "capacity.offer_over")] == []


# ---------------------------------------------------------------------------------------------------- E8
def test_e8_part_time_total_hours_contract(w):
    erika = w.erika()
    w.alloc(erika, w.p, 16)
    w.contract(erika, w.p, total=80)
    out = w.forecast()
    for i in range(4):
        p = w.entry(out, i, w.p)
        assert pick(p, "capacity", "confirmed", "gap", "utilisation", "status") == dict(
            capacity=16.0, confirmed=20.0, gap=4.0, utilisation=125.0, status="over"
        )
    assert out["totals"]["gap_by_project"][0]["gap"] == 16.0


def test_e8_variant_vacation_on_tuesday(w):
    erika = w.erika()
    w.alloc(erika, w.p, 16)
    w.contract(erika, w.p, total=80)
    w.repo.save("absences", dict(user_id=erika["id"], kind="vacation", start="2026-03-10", end="2026-03-10"))
    out = w.forecast()
    gaps = [w.entry(out, i, w.p)["gap"] for i in range(4)]
    assert gaps == pytest.approx([5.333333, 0.0, 5.333333, 5.333333], abs=1e-5)
    assert sum(gaps) == pytest.approx(16.0, abs=1e-4)
    assert sum(w.entry(out, i, w.p)["confirmed"] for i in range(4)) == pytest.approx(80.0, abs=1e-4)
    day = next(d for d in w.svc.day_table(date(2026, 3, 10), date(2026, 3, 10), today=TODAY)["days"])
    assert w.p["id"] not in day["scopes"]  # no capacity and nothing booked on the vacation day
    assert day["vacation"] == 8.0


# ---------------------------------------------------------------------------------------------------- misc rules
def test_by_project_off_and_project_filter(w):
    w.alloc(w.boss, w.p, 25)
    w.alloc(w.boss, w.q, 15)
    w.contract(w.boss, w.p, hpw=30)
    w.contract(w.boss, w.q, hpw=10)
    full = w.forecast()
    flat = w.forecast(by_project=False)
    assert all("projects" not in x for x in flat["weeks"]) and "gap_by_project" not in flat["totals"]
    assert [{k: v for k, v in x.items() if k != "projects"} for x in full["weeks"]] == flat["weeks"]
    assert not [x for x in flat["warnings"] if x["code"] == "capacity.gap"]
    only = w.forecast(project_id=w.p["id"])
    assert [p["project_id"] for p in only["weeks"][0]["projects"]] == [w.p["id"]]
    assert pick(only["weeks"][0], "capacity", "confirmed", "gap") == dict(capacity=25.0, confirmed=30.0, gap=5.0)
    assert [x["details"]["project_id"] for x in only["warnings"] if x["code"] == "capacity.gap"] == [w.p["id"]] * 4
    un = w.forecast(project_id="unassigned")
    assert un["weeks"][0]["capacity"] == 0.0 and un["warnings"] == []


def test_a_project_less_contract_draws_only_on_unassigned_capacity(w):
    w.alloc(w.boss, w.p, 30)
    w.contract(w.boss, None, hpw=20)
    out = w.forecast(1)
    un = w.entry(out, 0, None)
    assert pick(un, "capacity", "confirmed", "gap", "utilisation") == dict(
        capacity=10.0, confirmed=20.0, gap=10.0, utilisation=200.0
    )
    over = [x for x in out["warnings"] if x["code"] == "capacity.over"]
    assert len(over) == 1 and over[0]["details"]["scope"] == "unassigned" and over[0]["details"]["over_by"] == 10.0


def test_ring_fence_unused_hours_are_never_lent(w):
    """Q has 20 idle hours, P is short by 10: the gap of P stays."""
    w.alloc(w.boss, w.p, 20)
    w.alloc(w.boss, w.q, 20)
    w.contract(w.boss, w.p, hpw=30)
    out = w.forecast(1)
    assert w.entry(out, 0, w.p)["gap"] == 10.0 and w.entry(out, 0, w.q)["free_worst_case"] == 20.0
    assert out["weeks"][0]["gap"] == 10.0 and out["weeks"][0]["status"] == "over"


def test_what_if_allocation_changes_move_hours_between_projects(w):
    a = w.alloc(w.boss, w.p, 20)
    w.alloc(w.boss, w.q, 20)
    w.contract(w.boss, w.p, hpw=30)
    base = w.svc.simulate({}, 1, TODAY, None, today=TODAY)
    assert w.entry(base, 0, w.p)["gap"] == 10.0
    sim = w.svc.simulate(dict(allocations=[dict(id=a["id"], hours_per_week=30.0)]), 1, TODAY, None, today=TODAY)
    # boss is now allocated 30 + 20 = 50 h of 40: scaled by 0.8 -> P 24 h, gap 6
    assert w.entry(sim, 0, w.p)["capacity"] == 24.0 and w.entry(sim, 0, w.p)["gap"] == 6.0
    assert sim["delta"]["projects"] == [
        dict(project_id=w.p["id"], name="Demo project P", base_gap=10.0, gap=6.0, delta=-4.0)
    ]
    new = w.svc.simulate(dict(allocations=[dict(id=a["id"], delete=True)]), 1, TODAY, None, today=TODAY)
    assert w.entry(new, 0, w.p)["capacity"] == 0.0 and w.entry(new, 0, w.p)["gap"] == 30.0
    assert [r["hours_per_week"] for r in w.repo.list("allocations")] == [20, 20]  # nothing written


def test_engine_scope_sums_equal_person_capacity(w):
    erika = w.erika()
    w.alloc(w.boss, w.p, 17.5)
    w.alloc(erika, w.q, 40)  # over-allocated: more than the 32 h
    world = eng.load(w.repo, TODAY)
    table = eng.day_table(world, TODAY, TODAY + timedelta(days=13))
    for d in table.days.values():
        assert sum(sd.cap for sd in d.scopes.values()) == pytest.approx(d.cap, abs=1e-9)


# ------------------------------------------------------------------------------- holiday weeks (hours spread)
HOL_WEEK = date(2026, 3, 30)  # Monday; Friday 2026-04-03 is Good Friday, Monday 2026-04-06 Easter Monday


def hol_forecast(w, weeks=1):
    return w.svc.forecast(weeks, today=HOL_WEEK)


@pytest.mark.parametrize(
    "hpw, util, status, gap",
    # one 40 h person, 4 available days = 32 h of capacity: 20/32 = 62.5, 32/32 = 100.0, 40/32 = 125.0
    # 40 h/week spread over 4 days = 10 h against 8 h: 2 h x 4 days = 8 h of gap
    [(20, 62.5, "ok", 0.0), (32, 100.0, "tight", 0.0), (40, 125.0, "over", 8.0)],
)
def test_holiday_week_without_allocations_keeps_the_old_weekly_numbers(w, hpw, util, status, gap):
    w.contract(w.boss, None, hpw=hpw, start="2026-03-30", end="2026-04-03")
    out = hol_forecast(w)
    week = out["weeks"][0]
    assert pick(week, "capacity", "confirmed", "utilisation", "status", "gap") == dict(
        capacity=32.0, confirmed=float(hpw), utilisation=util, status=status, gap=gap
    )
    warnings = [x["code"] for x in out["warnings"]]
    assert warnings == (["capacity.over"] if status == "over" else [])
    day = w.svc.day_table(date(2026, 4, 3), date(2026, 4, 3), today=HOL_WEEK)["days"][0]
    assert day["holiday"] == 8.0 and day["confirmed"] == 0.0  # nothing is booked on Good Friday


def test_bootstrap_in_holiday_weeks_leaves_no_gap(w):
    """30 h/week contract over two holiday weeks (Good Friday, Easter Monday): 7.5 h on four days each."""
    w.contract(w.boss, w.p, hpw=30, start="2026-03-30", end="2026-04-12")
    res = AllocationService(w.repo).bootstrap(True, HOL_WEEK)
    assert [a["hours_per_week"] for a in res["allocations"]] == [30.0]
    out = hol_forecast(w, 2)
    for i in range(2):
        p, un = w.entry(out, i, w.p), w.entry(out, i, None)
        # allocation 30 over 4 available days = 7.5 per day (real 8): capacity 30, the other 0.5 h x 4 stay unassigned
        assert pick(p, "capacity", "confirmed", "gap", "status", "utilisation") == dict(
            capacity=30.0, confirmed=30.0, gap=0.0, status="tight", utilisation=100.0
        )
        assert un["capacity"] == 2.0
        assert out["weeks"][i]["capacity"] == 32.0
    assert not [x for x in out["warnings"] if x["code"] in ("capacity.gap", "allocation.employee_over_allocated")]


def test_allocation_in_a_holiday_week_is_spread_over_the_available_days(w):
    w.alloc(w.boss, w.p, 20, start="2026-03-30", end="2026-04-03")
    w.contract(w.boss, w.p, hpw=30, start="2026-03-30", end="2026-04-03")
    out = hol_forecast(w)
    # cap 20/4 = 5 per day, demand 30/4 = 7.5 per day: gap 2.5 x 4 = 10
    assert pick(w.entry(out, 0, w.p), "capacity", "confirmed", "gap") == dict(capacity=20.0, confirmed=30.0, gap=10.0)
    assert w.entry(out, 0, None)["capacity"] == 12.0  # (8 - 5) x 4


# ------------------------------------------------------------------------------- over-allocation judged per day
def test_allocations_on_different_days_of_a_week_do_not_warn(w):
    w.repo.save(
        "allocations",
        dict(
            user_id=w.boss["id"],
            project_id=w.p["id"],
            start="2026-03-02",
            end="2026-03-03",
            hours_per_week=40,
            status="active",
        ),
    )
    w.repo.save(
        "allocations",
        dict(
            user_id=w.boss["id"],
            project_id=w.q["id"],
            start="2026-03-04",
            end="2026-03-06",
            hours_per_week=40,
            status="active",
        ),
    )
    out = w.forecast(1)
    assert [x for x in out["warnings"] if x["code"] == "allocation.employee_over_allocated"] == []
    svc = AllocationService(w.repo)
    # the 5 h/week row books 1 h on Wednesday, where Q already has 8 of the 8 h: 9 > 8, one hour is scaled away
    res = svc.create(
        dict(user_id=w.boss["id"], project_id=w.p["id"], start="2026-03-04", end="2026-03-04", hours_per_week=5)
    )
    hit = [x["details"] for x in res["planning_warnings"] if x["code"].endswith("over_allocated")]
    assert [(d["week"], d["allocated"], d["weekly_hours"], d["over_by"]) for d in hit] == [
        ("2026-03-02", 41.0, 40.0, 1.0)
    ]


# ------------------------------------------------------------------------------- E5, internal-only variant
def test_e5_internal_only_variant_and_a_supplier_row_does_not_leak(w):
    w.alloc(w.boss, w.p, 40)
    w.repo.save(
        "allocations",
        dict(
            supplier_id="supp0001",
            project_id=w.p["id"],
            start=START,
            end=END,
            hours_per_week=20,
            rate=80,
            status="active",
        ),
    )
    w.contract(w.boss, w.p, hpw=50)
    out = w.forecast()
    p = w.entry(out, 0, w.p)
    assert p["internal"] == dict(capacity=40.0, assigned=50.0, gap=10.0)
    assert p["external"] == dict(capacity=0.0, assigned=0.0, gap=0.0)
    assert pick(p, "capacity", "gap", "utilisation", "status") == dict(
        capacity=40.0, gap=10.0, utilisation=125.0, status="over"
    )
    assert out["weeks"][0]["capacity"] == 40.0


# ------------------------------------------------------------------------------- bootstrap rules
def bootstrap(w, confirm=False):
    return AllocationService(w.repo).bootstrap(confirm, TODAY)


def test_bootstrap_total_hours_contract_is_20_h_per_week(w):
    erika = w.erika()
    w.contract(erika, w.p, total=80)  # E8: 80 h over 16 available days = 5 per day, 20 per week
    res = bootstrap(w)
    assert [(a["user_id"], a["hours_per_week"]) for a in res["allocations"]] == [(erika["id"], 20.0)]


def test_bootstrap_two_assignees_get_one_allocation_each(w):
    erika = w.erika()
    rec = w.contract(w.boss, w.p, hpw=40)
    w.repo.save("contracts", {**rec, "user_ids": [w.boss["id"], erika["id"]]})
    res = bootstrap(w)
    # each has half: boss 20 h over 5 days, Erika 20 h over her 4 days
    assert sorted((a["user_name"], a["hours_per_week"]) for a in res["allocations"]) == [
        ("Demo Boss", 20.0),
        ("Erika Beispiel", 20.0),
    ]


def test_bootstrap_partial_coverage_still_proposes(w):
    w.alloc(w.boss, w.p, 30, start="2026-03-02", end="2026-03-13")  # does not contain the contract period
    w.contract(w.boss, w.p, hpw=30)
    res = bootstrap(w)
    assert len(res["allocations"]) == 1 and res["skipped"] == []
    codes = [x["code"] for x in res["warnings"]]
    assert (
        codes.count("allocation.overlap_same_project") == 2 and codes.count("allocation.employee_over_allocated") == 2
    )


def test_bootstrap_clamps_at_80_and_says_so(w):
    w.contract(w.boss, w.p, hpw=100)
    res = bootstrap(w)
    assert [a["hours_per_week"] for a in res["allocations"]] == [80.0]
    clamp = [x for x in res["warnings"] if x["code"] == "allocation.peak_clamped"]
    assert len(clamp) == 1 and clamp[0]["details"]["peak"] == 100.0 and clamp[0]["details"]["hours_per_week"] == 80.0


def test_bootstrap_inactive_assignee_falls_back_to_the_boss_like_the_planner(w):
    gone = w.repo.save(
        "users",
        dict(username="gone", name="Gone", role="employee", weekly_hours=40, workdays=[0, 1, 2, 3, 4], active=False),
    )
    w.contract(gone, w.p, hpw=10)
    assert [a["user_id"] for a in bootstrap(w)["allocations"]] == [w.boss["id"]]


def test_bootstrap_skips_contracts_that_are_over(w):
    w.contract(w.boss, w.p, hpw=10, start="2026-01-05", end="2026-02-27")  # ended before TODAY
    res = bootstrap(w)
    assert res["allocations"] == [] and res["skipped"] == []


def test_bootstrap_warnings_are_the_same_in_preview_and_confirm(w):
    w.contract(w.boss, w.p, hpw=30)
    w.contract(w.boss, w.q, hpw=30)
    pre = bootstrap(w)
    assert len(pre["allocations"]) == 2
    over = [x for x in pre["warnings"] if x["code"] == "allocation.employee_over_allocated"]
    assert [x["details"]["week"] for x in over] == ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23"]
    assert (over[0]["details"]["allocated"], over[0]["details"]["over_by"]) == (60.0, 20.0)
    done = bootstrap(w, True)
    assert done["warnings"] == pre["warnings"]


def test_bootstrap_warnings_are_bounded_to_ten_weeks(w):
    w.contract(w.boss, w.p, hpw=30, start="2026-03-02", end="2026-12-31")
    w.contract(w.boss, w.q, hpw=30, start="2026-03-02", end="2026-12-31")
    over = [x for x in bootstrap(w)["warnings"] if x["code"] == "allocation.employee_over_allocated"]
    assert len(over) == 10


# ------------------------------------------------------------------------------- damaged rows never break a view
@pytest.mark.parametrize(
    "row",
    [
        dict(user_id="u1", project_id="p1", status="active"),  # no period
        dict(user_id="u1", project_id="p1", start="garbage", end="2026-03-27", status="active"),
        dict(user_id="u1", start="2026-03-02", end="2026-03-27", status="active"),  # no project
        dict(user_id="u1", project_id="p1", start="2026-03-27", end="2026-03-02", status="active"),
        dict(user_id="u1", project_id="p1", start=5, end=6, status="active"),
    ],
)
def test_a_damaged_allocation_row_is_skipped_everywhere(w, row):
    w.repo.save("allocations", {**row, "user_id": w.boss["id"]})
    w.contract(w.boss, w.p, hpw=10)
    out = w.forecast()
    assert out["weeks"][0]["capacity"] == 40.0 and w.entry(out, 0, None)["capacity"] == 40.0
    assert w.svc.simulate({}, 2, TODAY, None, today=TODAY)["weeks"]
    assert w.svc.day_table(date(2026, 3, 2), date(2026, 3, 8), today=TODAY)["days"]
    listed = AllocationService(w.repo).list()
    assert len(listed) == 1
    assert AllocationService(w.repo).bootstrap(False, TODAY)["allocations"][0]["hours_per_week"] == 10.0


# ------------------------------------------------------------------------------- simulate = apply rules
def test_simulate_enforces_the_same_rules_as_apply(w):
    from permita38.core.errors import ServiceError

    base = dict(user_id=w.boss["id"], project_id=w.p["id"], start=START, end=END, hours_per_week=10)

    def sim(**kw):
        return w.svc.simulate(dict(allocations=[{**base, **kw}]), 1, TODAY, None, today=TODAY)

    sim()
    done = w.repo.save("projects", dict(name="Done", status="done"))
    gone = w.repo.save("users", dict(username="gone", name="Gone", role="employee", weekly_hours=40, active=False))
    for kw, status, msg in [
        (dict(project_id=done["id"]), 409, "The project is closed"),
        (dict(user_id=gone["id"]), 409, "The user is not active"),
        (dict(end="2032-01-01"), 422, "The period may be at most 1830 days"),
    ]:
        with pytest.raises(ServiceError) as exc:
            sim(**kw)
        assert (exc.value.status, exc.value.message) == (status, msg)
    with pytest.raises(ServiceError) as exc:
        w.svc.apply(dict(allocations=[{**base, "end": "2032-01-01"}]))
    assert exc.value.status == 422
    assert w.repo.list("allocations") == []
