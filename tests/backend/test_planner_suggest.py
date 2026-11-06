"""planner/suggest.py (WP4b P3) through PlannerService on a real SQLite store. Small invented worlds, numbers worked
out by hand (see the comment above each test); no personal data."""

from __future__ import annotations

import time
from datetime import date

import pytest

from permita38.core.errors import ServiceError
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.planner.service import PlannerService

D = date
ADA = "adaaaaaa01"
CUST, ORCH, PINE = "custaaaa01", "projorch01", "projpine01"
WORKDAYS = [0, 1, 2, 3, 4]


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "data")
    r.startup()
    r.save_settings({"state": "BY", "default_rate": 70})
    r.save("users", {"id": ADA, "username": "ada", "name": "Ada", "role": "member", "weekly_hours": 40,
                     "workdays": WORKDAYS, "active": True})  # fmt: skip
    r.save("customers", {"id": CUST, "name": "Orchard Demo", "rate": 100})
    r.save("projects", {"id": ORCH, "name": "Orchard", "customer_id": CUST, "kind": "customer", "status": "active"})
    r.save("projects", {"id": PINE, "name": "Pine", "customer_id": CUST, "kind": "customer", "status": "active"})
    yield r
    r.close()


@pytest.fixture
def svc(repo):
    return PlannerService(repo)


def alloc(repo, aid, hpw, start, end, owner=ADA, project=ORCH):
    return repo.save("allocations", {"id": aid, "user_id": owner, "project_id": project, "start": start, "end": end,
                                     "hours_per_week": hpw, "status": "active"})  # fmt: skip


def contract(repo, cid, start, end, hpw=None, total=None, project=ORCH):
    row = {"id": cid, "title": cid, "project_id": project, "customer_id": CUST, "start": start, "end": end,
           "status": "confirmed", "rate": 100}  # fmt: skip
    row["hours_per_week" if hpw is not None else "total_hours"] = hpw if hpw is not None else total
    return repo.save("contracts", row)


def offer(repo, oid, start, end, total, project=ORCH):
    return repo.save("offers", {"id": oid, "number": oid, "status": "sent", "project_id": project, "customer_id": CUST,
                                "lines": [], "plan": {"start": start, "end": end, "total_hours": total,
                                                      "win_probability": 100}})  # fmt: skip


# -- suggest_vacation ------------------------------------------------------------------------------------------
# Today Sunday 08.11.2026. Ada has 20 h/week on Orchard (the hours move to her other days when she is off, so only
# a whole week off leaves a shortfall) and a weekly contract of 20 h/week in the week 23.-27.11. only.
# Blocks of 5 working days: starting 16.11. -> 16.-20.11., no increase; bridge days: Sat+Sun before, Sat+Sun after = 4.
# Starting 17./18./19./20.11.: spans a weekend (2 inside), nothing around = 2, increase 0 (the contract week keeps at
# least one working day for the 20 h). Starting 23.11. takes the whole contract week: the 20 h are lost (increase 20)
# although its bridge days are 4. Starting 24.-27.11. leaves one day of the week: increase 0, bridge days 2.
@pytest.fixture
def vac(repo):
    alloc(repo, "allocada01", 20, "2026-11-09", "2026-12-18")
    contract(repo, "contrk1001", "2026-11-23", "2026-11-27", hpw=20)


def test_vacation_avoids_the_contract_week_and_bridge_days_break_ties(svc, vac):
    body = svc.suggest_vacation(ADA, 5, D(2026, 11, 16), D(2026, 11, 27), limit=10, today=D(2026, 11, 8))
    assert body["considered"] == 10
    got = [(o["start"], o["end"], o["overload_hours"], o["bridge_days"]) for o in body["options"]]
    assert got[:5] == [
        ("2026-11-16", "2026-11-20", 0.0, 4),
        ("2026-11-17", "2026-11-23", 0.0, 2),
        ("2026-11-18", "2026-11-24", 0.0, 2),
        ("2026-11-19", "2026-11-25", 0.0, 2),
        ("2026-11-20", "2026-11-26", 0.0, 2),
    ]
    assert got[-1] == ("2026-11-23", "2026-11-27", 20.0, 4)  # the overload outweighs the bridge days
    assert {g[0] for g in got[5:9]} == {"2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"}
    assert all(o["days"] == 5 for o in body["options"])


def test_vacation_default_limit_and_changes_as_base(svc, vac, repo):
    assert len(svc.suggest_vacation(ADA, 5, D(2026, 11, 16), D(2026, 11, 27), today=D(2026, 11, 8))["options"]) == 5
    # the what-if already sends Ada off in the week 16.-20.11.: those days are no candidates any more
    changes = {"absences": [{"user_id": ADA, "kind": "vacation", "start": "2026-11-16", "end": "2026-11-20"}]}
    body = svc.suggest_vacation(ADA, 5, D(2026, 11, 16), D(2026, 11, 27), changes=changes, limit=10,
                                today=D(2026, 11, 8))  # fmt: skip
    assert all(o["start"] >= "2026-11-23" for o in body["options"]) and body["considered"] == 5
    assert repo.list("absences") == []


def test_vacation_errors(svc, vac):
    with pytest.raises(ServiceError) as err:
        svc.suggest_vacation("nobody", 3, D(2026, 11, 16), D(2026, 11, 20), today=D(2026, 11, 8))
    assert err.value.status == 404
    with pytest.raises(ServiceError) as err:
        svc.suggest_vacation(ADA, 3, D(2026, 11, 20), D(2026, 11, 16), today=D(2026, 11, 8))
    assert err.value.status == 422
    with pytest.raises(ServiceError):
        svc.suggest_vacation(ADA, 3, D(2026, 1, 1), D(2026, 12, 31), today=D(2026, 11, 8))


# -- suggest_start ---------------------------------------------------------------------------------------------
# Ada has 10 h/week on Orchard from Mon 16.11. (2 h a day) and a weekly contract of 5 h/week to 31.12. The contract
# leaves 1 h a day (5 h a week) for an offer. 20 h need 20 working days: 16.11.-11.12. (4 full weeks).
@pytest.fixture
def start_world(repo):
    alloc(repo, "allocada01", 10, "2026-11-16", "2026-12-31")
    contract(repo, "contrk1001", "2026-11-16", "2026-12-31", hpw=5)


def test_suggest_start_first_day_with_supply(svc, start_world):
    # earliest Mon 09.11.: no Orchard hours before the assignment starts on 16.11.; from there 20 days x 2 h = 40 h
    body = svc.suggest_start(20, D(2026, 12, 11), D(2026, 11, 9), ORCH, today=D(2026, 11, 8))
    assert body == {"start": "2026-11-16", "feasible": True, "free_hours": 40.0}


def test_suggest_start_does_not_fit_gives_null(svc, start_world):
    # deadline Thu 10.12.: 19 working days from 16.11. leave 19 h < 20 h
    body = svc.suggest_start(20, D(2026, 12, 10), D(2026, 11, 9), ORCH, today=D(2026, 11, 8))
    assert body["start"] is None and body["feasible"] is False
    assert body["max_hours_from_earliest"] == 19.0 and body["free_hours"] == 19.0 and body["best_free"] == 19.0
    # a later earliest start (Wed 18.11.) leaves 18 days = 18 h even with the longer deadline
    body = svc.suggest_start(20, D(2026, 12, 11), D(2026, 11, 18), ORCH, today=D(2026, 11, 8))
    assert body["start"] is None and body["max_hours_from_earliest"] == 18.0


def test_suggest_start_other_scope_and_changes(svc, start_world):
    # Pine has no hours at all
    assert svc.suggest_start(1, D(2026, 12, 11), None, PINE, today=D(2026, 11, 8))["start"] is None
    # the team's unassigned time on 09.-13.11.: Ada 8 h a day (nothing assigned yet) = 40 h
    body = svc.suggest_start(30, D(2026, 11, 13), D(2026, 11, 9), None, today=D(2026, 11, 8))
    assert body == {"start": "2026-11-09", "feasible": True, "free_hours": 40.0}
    # a what-if allocation of 1 h a day on Pine from 23.11. gives Pine 10 h until 04.12.
    changes = {"allocations": [{"user_id": ADA, "project_id": PINE, "start": "2026-11-23", "end": "2026-12-04",
                                "hours_per_week": 5}]}  # fmt: skip
    body = svc.suggest_start(10, D(2026, 12, 4), D(2026, 11, 16), PINE, changes=changes, today=D(2026, 11, 8))
    assert body == {"start": "2026-11-23", "feasible": True, "free_hours": 10.0}


def test_suggest_start_errors(svc, start_world):
    with pytest.raises(ServiceError) as err:
        svc.suggest_start(5, D(2026, 11, 10), D(2026, 11, 12), ORCH, today=D(2026, 11, 8))
    assert err.value.status == 422
    with pytest.raises(ServiceError) as err:
        svc.suggest_start(5, D(2026, 11, 20), None, "nosuchproj", today=D(2026, 11, 8))
    assert err.value.status == 404


# -- what_must_move --------------------------------------------------------------------------------------------
# Today Sun 01.11.2026. Ada has 20 h/week (4 h a day) on Orchard in 09.-20.11. only = 40 h.
# O1: 30 h, 09.-20.11. ends before the draft, so it is covered first and takes the 20 h of week 09.11. and the first
# 10 h of week 16.11. (16., 17. = 8 h, 18. = 2 h). Draft: 20 h, 16.-27.11.: only the week of 16.11. has supply and
# 10 h of it are left -> short 10 h. Moving O1 by +7 days (16.-27.11.) gives it the same end as the draft and the same
# start; the tie goes to the id ("draft" < "offero1001"), so the draft takes the 20 h of week 16.11. -> fits (+7 is the
# smallest shift tried first; -7 would work as well). Trim: 20 h - 10 h short = 10 h.
@pytest.fixture
def move_world(repo):
    alloc(repo, "allocada01", 20, "2026-11-09", "2026-11-20")
    offer(repo, "offero1001", "2026-11-09", "2026-11-20", 30)


DRAFT = {"start": "2026-11-16", "end": "2026-11-27", "total_hours": 20, "project_id": ORCH}


def test_what_must_move_smallest_shift_and_trim(svc, move_world):
    body = svc.what_must_move(DRAFT, today=D(2026, 11, 1))
    assert body["fits"] is False
    assert body["suggestions"] == [
        {"type": "move", "kind": "offer", "id": "offero1001", "title": "Offer offero1001",
         "days": 7, "message": "Move Offer offero1001 by +7 days (to 16.11.2026 – 27.11.2026)."},
        {"type": "trim", "max_hours": 10.0, "message": "Or reduce the draft from 20 h to at most 10 h."},
    ]  # fmt: skip


def test_what_must_move_only_trim_when_no_move_helps(svc, repo):
    alloc(repo, "allocada01", 20, "2026-11-16", "2026-11-20")  # 20 h supply, no other offer
    body = svc.what_must_move(dict(DRAFT, total_hours=30), today=D(2026, 11, 1))
    assert body == {"fits": False, "suggestions": [
        {"type": "trim", "max_hours": 20.0, "message": "Or reduce the draft from 30 h to at most 20 h."}]}  # fmt: skip


def test_what_must_move_fits_and_bad_plan(svc, move_world):
    assert svc.what_must_move(dict(DRAFT, total_hours=10), today=D(2026, 11, 1)) == {"fits": True, "suggestions": []}
    with pytest.raises(ServiceError) as err:
        svc.what_must_move({"start": "2026-11-16"}, today=D(2026, 11, 1))
    assert err.value.status == 422


# -- performance -----------------------------------------------------------------------------------------------
def test_suggestions_are_fast_on_a_realistic_world(repo):
    users = [f"user{n:06d}" for n in range(10)]
    for n, u in enumerate(users):
        repo.save("users", {"id": u, "username": f"u{n}", "name": f"U{n}", "role": "member", "weekly_hours": 40,
                            "workdays": WORKDAYS, "active": True})  # fmt: skip
    projects = []
    for n in range(6):
        pid = f"projperf{n:03d}"
        projects.append(pid)
        repo.save("projects", {"id": pid, "name": f"P{n}", "customer_id": CUST, "kind": "customer", "status": "active"})
    for n, u in enumerate(users):
        alloc(repo, f"allocperf{n:03d}", 20, "2026-11-09", "2027-04-30", owner=u, project=projects[n % 6])
    for n in range(30):
        pid = projects[n % 6]
        if n % 3:
            contract(repo, f"contrperf{n:03d}", "2026-11-09", "2027-03-31", hpw=8, project=pid)
        else:
            offer(repo, f"offerperf{n:03d}", "2026-11-16", "2027-02-26", 120, project=pid)
    svc = PlannerService(repo)
    t0 = time.perf_counter()
    vac = svc.suggest_vacation(users[0], 5, D(2026, 12, 1), D(2027, 1, 29), today=D(2026, 11, 8))
    start = svc.suggest_start(40, D(2027, 2, 26), D(2026, 11, 9), projects[0], today=D(2026, 11, 8))
    move = svc.what_must_move({"start": "2026-11-16", "end": "2027-02-26", "total_hours": 900,
                               "project_id": projects[0]}, today=D(2026, 11, 8))  # fmt: skip
    elapsed = time.perf_counter() - t0
    assert vac["considered"] > 30 and start["start"] and move["fits"] is False
    assert elapsed < 3.0, elapsed
