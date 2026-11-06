"""planner/service.py (WP4b P2): PlannerService through a real SQLite store. Spec example E1/E7 built as rows;
invented data only."""

from __future__ import annotations

from datetime import date

import pytest

from permita38.core.errors import ServiceError
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.planner.service import PlannerService

D = date
TODAY = D(2026, 11, 8)  # Sunday, E1
ADA, BEN = "adaaaaaa01", "benaaaaa01"
SAM = "samaaaaa01"
CUST, ORCH, ADM, PINE = "custaaaa01", "projorch01", "projadm001", "projpine01"
ME = {"id": ADA}


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "data")
    r.startup()
    r.save_settings({"state": "BY", "default_rate": 70, "planning_default_win_probability": 50})
    r.save("users", {"id": ADA, "username": "ada", "name": "Ada", "role": "member", "weekly_hours": 40,
                     "workdays": [0, 1, 2, 3, 4], "active": True})  # fmt: skip
    r.save("users", {"id": BEN, "username": "ben", "name": "Ben", "role": "member", "weekly_hours": 40,
                     "workdays": [0, 1, 2, 3, 4], "active": True})  # fmt: skip
    r.save("suppliers", {"id": SAM, "name": "Sam", "kind": "person-capacity", "active": True,
                         "workdays": [0, 1, 2, 3, 4], "rate": 90})  # fmt: skip
    r.save("customers", {"id": CUST, "name": "Orchard Demo", "rate": 120})
    r.save("projects", {"id": ORCH, "name": "Orchard", "customer_id": CUST, "kind": "customer", "status": "active"})
    r.save("projects", {"id": ADM, "name": "Administration", "kind": "internal", "status": "active"})
    r.save("projects", {"id": PINE, "name": "Pine", "customer_id": CUST, "kind": "customer", "status": "active"})
    yield r
    r.close()


@pytest.fixture
def svc(repo):
    return PlannerService(repo)


def alloc(repo, aid, owner, project, hpw=None, total=None, start="2026-11-09", end="2026-12-18", **kw):
    row = {"id": aid, "project_id": project, "start": start, "end": end, "status": "active",
           ("supplier_id" if owner == SAM else "user_id"): owner}  # fmt: skip
    row["hours_per_week" if hpw is not None else "total_hours"] = hpw if hpw is not None else total
    row.update(kw)
    return repo.save("allocations", row)


def contract(repo, cid, hpw=None, total=None, start="2026-11-09", end="2026-12-18", project=ORCH, **kw):
    row = {"id": cid, "title": cid, "project_id": project, "customer_id": CUST, "start": start, "end": end,
           "status": "confirmed", "rate": 120}  # fmt: skip
    row["hours_per_week" if hpw is not None else "total_hours"] = hpw if hpw is not None else total
    row.update(kw)
    return repo.save("contracts", row)


@pytest.fixture
def e1(repo):
    alloc(repo, "allocadm01", ADA, ADM, hpw=30)
    alloc(repo, "allocada01", ADA, ORCH, hpw=4)
    alloc(repo, "allocsam01", SAM, ORCH, total=24, end="2026-12-04", rate=90)
    contract(repo, "contrk1001", hpw=4)
    contract(repo, "contrk2001", total=50, end="2026-12-04")


def project(body, pid):
    return next(p for p in body["projects"] if p["project_id"] == pid)


def item(body, pid, iid):
    return next(i for i in project(body, pid)["items"] if i["id"] == iid)


def codes(body):
    return [w["code"] for w in body["warnings"]]


# -- plan ------------------------------------------------------------------------------------------------------
def test_plan_e1_numbers(svc, e1):
    body = svc.plan(6, today=TODAY)
    o = project(body, ORCH)
    assert o["supply"] == {"employees": 24.0, "subcontractors": 24.0, "total": 48.0}
    assert (o["remaining_contracts"], o["shortfall_contracts"], o["status"]) == (74.0, 26.0, "short")
    assert (o["planned_subcontractor_cost"], o["contract_value"], o["margin"]) == (2160.0, 8880.0, 6720.0)
    k2 = item(body, ORCH, "contrk2001")
    assert (k2["remaining"], k2["covered"], k2["shortfall"]) == (50.0, 24.0, 26.0)
    assert item(body, ORCH, "contrk1001")["shortfall"] == 0.0
    assert {(x["user_id"], x["hours"]) for x in k2["suggestions"]} >= {(ADA, 24.0)}
    w = next(w for w in body["weeks"] if w["week"] == "2026-11-09")
    assert (w["capacity"], w["assigned_customer"], w["assigned_internal"]) == (80.0, 4.0, 30.0)  # Ada and Ben
    assert (w["subcontractor_hours"], w["over_assigned"]) == (6.0, 0.0)
    assert "contract.shortfall" in codes(body)
    assert project(body, ADM)["kind"] == "internal"
    assert body["skipped"] == []


def test_plan_week_row_of_ada_only(svc, e1):
    body = svc.plan(6, user_id=ADA, today=TODAY)
    w = next(w for w in body["weeks"] if w["week"] == "2026-11-09")
    assert (w["capacity"], w["unassigned"], w["billable_share"], w["subcontractor_hours"]) == (40.0, 6.0, 10.0, 0.0)
    assert [p["user_id"] for p in body["people"]] == [ADA]
    assert len(body["weeks"]) == 6


def test_plan_project_filter(svc, e1):
    body = svc.plan(6, project_id=ORCH, today=TODAY)
    assert [p["project_id"] for p in body["projects"]] == [ORCH]
    other = svc.plan(6, project_id=PINE, today=TODAY)
    assert other["projects"] == [] and "contract.shortfall" not in codes(other)
    with pytest.raises(ServiceError) as err:
        svc.plan(6, project_id="nosuchproj", today=TODAY)
    assert err.value.status == 404


def test_plan_unassigned_scope(svc, e1, repo):
    repo.save("contracts", {"id": "contrfree01", "title": "Free", "customer_id": CUST, "start": "2026-11-09",
                            "end": "2026-11-20", "hours_per_week": 5, "status": "confirmed", "rate": 100})  # fmt: skip
    body = svc.plan(6, project_id="unassigned", today=TODAY)
    assert [p["project_id"] for p in body["projects"]] == ["unassigned"]
    assert "contract.no_project" in codes(body)


# -- simulate --------------------------------------------------------------------------------------------------
def test_simulate_added_allocation_reduces_shortfall(svc, e1, repo):
    changes = {"allocations": [{"user_id": ADA, "project_id": ORCH, "start": "2026-11-09", "end": "2026-12-04",
                                "hours_per_week": 6}]}  # fmt: skip
    body = svc.simulate(changes, 6, today=TODAY)
    o = project(body, ORCH)
    # Ada +6 h/week over the 4 weeks to 04.12. = +24 h more supply for K2
    assert o["shortfall_contracts"] == 2.0
    assert body["delta"]["projects"] == [
        {"project_id": ORCH, "shortfall_before": 26.0, "shortfall_after": 2.0, "delta": -24.0}
    ]
    assert body["delta"]["total"] == {"shortfall_before": 26.0, "shortfall_after": 2.0, "delta": -24.0}
    assert len(repo.list("allocations")) == 3  # nothing written


def test_simulate_without_changes_has_zero_delta(svc, e1):
    d = svc.simulate({}, 6, today=TODAY)["delta"]
    assert d["total"]["delta"] == 0.0 and d["projects"][0]["delta"] == 0.0


# -- apply and scenarios ---------------------------------------------------------------------------------------
def test_apply_writes_allocation_and_absence(svc, e1, repo):
    changes = {
        "allocations": [{"user_id": ADA, "project_id": PINE, "start": "2026-11-09", "end": "2026-11-13",
                         "hours_per_week": 2}],
        "absences": [{"user_id": ADA, "kind": "vacation", "start": "2026-12-07", "end": "2026-12-08"}],
    }  # fmt: skip
    res = svc.apply(changes, TODAY)
    assert res["applied"] == 2 and isinstance(res["warnings"], list)
    assert len(repo.list("allocations")) == 4 and len(repo.list("absences")) == 1


def test_apply_error_rolls_back(svc, e1, repo):
    bad = {"absences": [{"user_id": ADA, "start": "2026-12-07"}], "commitments": [{"kind": "contract", "id": "zzz"}]}
    with pytest.raises(ServiceError) as err:
        svc.apply(bad, TODAY)
    assert err.value.status == 404
    assert repo.list("absences") == []


def test_apply_moves_a_contract(svc, e1, repo):
    svc.apply({"commitments": [{"kind": "contract", "id": "contrk2001", "end": "2026-12-11"}]}, TODAY)
    assert repo.get("contracts", "contrk2001")["end"] == "2026-12-11"


def test_scenarios_save_list_apply_delete(svc, e1, repo):
    changes = {"absences": [{"user_id": ADA, "kind": "vacation", "start": "2026-12-07", "end": "2026-12-07"}]}
    s = svc.save_scenario(ME, None, "Holiday", changes)
    assert [x["name"] for x in svc.list_scenarios(ME)] == ["Holiday"]
    assert svc.list_scenarios({"id": BEN}) == []
    with pytest.raises(ServiceError):
        svc.get_scenario({"id": BEN}, s["id"])
    unknown = {"allocations": [{"user_id": ADA, "project_id": "nosuchproj", "start": "2026-11-09",
                                "end": "2026-11-10", "hours_per_week": 1}]}  # fmt: skip
    with pytest.raises(ServiceError) as err:
        svc.save_scenario(ME, None, "Bad", unknown)
    assert err.value.status == 422
    assert svc.apply_scenario(ME, s["id"])["applied"] == 1
    assert len(repo.list("absences")) == 1
    svc.delete_scenario(ME, s["id"])
    assert svc.list_scenarios(ME) == []


# -- offers and warnings ---------------------------------------------------------------------------------------
def test_check_offer_e7_shortfall(svc, e1):
    draft = {"project_id": ORCH, "customer_id": CUST, "lines": [],
             "plan": {"start": "2026-11-16", "end": "2026-12-04", "total_hours": 10, "win_probability": 50}}  # fmt: skip
    w = [x for x in svc.check_offer(draft, today=TODAY) if x["code"] == "offer.shortfall"]
    assert len(w) == 1 and w[0]["details"]["shortfall"] == 10.0


def test_saved_offer_e7_project_numbers_and_warning(svc, e1, repo):
    repo.save("offers", {"id": "offero1001", "number": "A-001", "status": "sent", "project_id": ORCH,
                         "customer_id": CUST, "date": "2026-11-02", "valid_until": "2026-12-31", "lines": [],
                         "plan": {"start": "2026-11-16", "end": "2026-12-04", "total_hours": 10,
                                  "win_probability": 50}})  # fmt: skip
    o = project(svc.plan(6, today=TODAY), ORCH)
    assert (o["shortfall_contracts"], o["shortfall_worst_case"], o["shortfall_expected"]) == (26.0, 36.0, 31.0)
    rec = repo.get("offers", "offero1001")
    assert "offer.shortfall" in [x["code"] for x in svc.warnings_for("offers", rec, TODAY)]
    # the saved offer is replaced by the draft, not counted twice
    assert [x["code"] for x in svc.check_offer(rec, exclude_id=rec["id"], today=TODAY)].count("offer.shortfall") == 1


def test_check_offer_without_shortfall(svc, e1, repo):
    alloc(repo, "allocpine1", BEN, PINE, hpw=10, end="2026-11-27")  # 30 h in 3 weeks
    draft = {"project_id": PINE, "customer_id": CUST, "lines": [],
             "plan": {"start": "2026-11-09", "end": "2026-11-27", "total_hours": 30}}  # fmt: skip
    assert "offer.shortfall" not in [x["code"] for x in svc.check_offer(draft, today=TODAY)]
    draft["plan"]["total_hours"] = 31
    w = [x for x in svc.check_offer(draft, today=TODAY) if x["code"] == "offer.shortfall"]
    assert len(w) == 1 and w[0]["details"]["shortfall"] == 1.0


def test_warnings_for_contract(svc, e1, repo):
    w = svc.warnings_for("contracts", repo.get("contracts", "contrk2001"), TODAY)
    assert [x["code"] for x in w] == ["contract.shortfall"] and w[0]["details"]["shortfall"] == 26.0
    assert svc.warnings_for("contracts", repo.get("contracts", "contrk1001"), TODAY) == []


# -- internal budget, no demand --------------------------------------------------------------------------------
def test_internal_over_budget(svc, repo):
    repo.save("projects", {"id": ADM, "name": "Administration", "kind": "internal", "status": "active",
                           "budget_hours": 100})  # fmt: skip
    alloc(repo, "allocadm01", ADA, ADM, hpw=30, end="2026-11-27")  # 3 weeks x 30 = 90 h
    body = svc.plan(4, today=TODAY)
    assert project(body, ADM)["status"] == "ok" and "internal.over_budget" not in codes(body)
    repo.save("entries", {"id": "ent0000001", "user_id": ADA, "date": "2026-11-06", "minutes": 15 * 60,
                          "project_id": ADM})  # fmt: skip
    body = svc.plan(4, today=TODAY)
    w = next(w for w in body["warnings"] if w["code"] == "internal.over_budget")
    assert project(body, ADM)["status"] == "over_budget"
    assert w["severity"] == "warning" and "15 h booked + 90 h assigned = 105 h" in w["message"]


def test_assignment_no_demand(svc, repo):
    alloc(repo, "allocsam01", SAM, ORCH, total=24, end="2026-12-04", rate=90)
    body = svc.plan(6, today=TODAY)
    w = [x for x in body["warnings"] if x["code"] == "assignment.no_demand"]
    assert len(w) == 1 and w[0]["severity"] == "info" and w[0]["details"]["assignment_id"] == "allocsam01"
    contract(repo, "contrk2001", total=50, end="2026-12-04")
    assert "assignment.no_demand" not in codes(svc.plan(6, today=TODAY))


# -- entitlement, extension, cover_plan ---------------------------------------------------------------------------
def test_entitlement_vacation_year(svc, repo):
    repo.save("users", {**repo.get("users", ADA), "vacation_days_per_year": 30, "vacation_carry_over": 2})
    # 02.-06.11. past (5 working days), 09.-13.11. future (5), 24.-28.12.: Thu 24. and Mon 28. count, 25.12. is a holiday
    for a, b in (("2026-11-02", "2026-11-06"), ("2026-11-09", "2026-11-13"), ("2026-12-24", "2026-12-28")):
        repo.save("absences", {"user_id": ADA, "kind": "vacation", "start": a, "end": b})
    repo.save("absences", {"user_id": ADA, "kind": "sick", "start": "2026-11-16", "end": "2026-11-17"})
    e = svc.entitlement(ADA, 2026, TODAY)
    assert (e["entitlement"], e["used"], e["planned"], e["remaining"], e["over"]) == (32.0, 5, 7, 20.0, False)
    with pytest.raises(ServiceError):
        svc.entitlement("nobody0001", 2026, TODAY)


def test_extend_project_and_cover_plan(svc, repo):
    repo.save("projects", {**repo.get("projects", ORCH), "start": "2026-11-01", "end": "2026-12-01"})
    with pytest.raises(ServiceError):
        svc.extend_project(ORCH, "2026-12-01", None, ME)
    p = svc.extend_project(ORCH, "2026-12-15", "more work", ME)
    assert p["end"] == "2026-12-15" and p["extensions"][-1]["from"] == "2026-12-01"
    assert svc.project_timeline(ORCH)["extensions"] == p["extensions"]
    widened = PlannerService.cover_plan(p, {"start": "2026-10-20", "end": "2027-01-10"}, "offer1", now=5)
    assert (widened["start"], widened["end"], widened["extensions"][-1]["kind"]) == (
        "2026-10-20",
        "2027-01-10",
        "offer",
    )
    assert PlannerService.cover_plan(p, {"start": "2026-11-05", "end": "2026-12-10"}, "offer1") is p
