"""Capacity planning: golden weeks (hand-computed), binding offers, revenue basis, feasibility, suggestions,
vacation entitlement, what-if writes, scenario apply, project extension and warnings.

Calendar used throughout: 2026-02-02 is a Monday. Holiday-free weeks unless stated otherwise.
"""

import json
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from permita38.api.server import get_app
from permita38.core.config import Config
from permita38.core.database import CURRENT_REVISION, DIALECT_NAMES, MIGRATIONS_DIR
from permita38.core.repository import TABLES
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository
from permita38.core.service import planning as eng
from permita38.core.service import planning_warnings as pw
from permita38.core.service.plan_service import PlanningService

MON = date(2026, 2, 2)
TODAY = date(2026, 1, 15)


class FakeBusy:
    """Busy provider with fixed hours per (user, day)."""

    def __init__(self, hours):
        self.hours = hours  # {(user_id, date): hours}

    def busy_hours(self, user_id, start, end):
        return {d: h for (u, d), h in self.hours.items() if u == user_id and start <= d <= end}


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "data")
    r.startup()
    yield r
    r.close()


def add_user(repo, name="Ann", weekly=40, workdays=None, role="boss", **extra):
    return repo.save(
        "users",
        dict(
            username=name.lower(),
            name=name,
            role=role,
            weekly_hours=weekly,
            workdays=workdays or [0, 1, 2, 3, 4],
            active=True,
            **extra,
        ),
    )


def add_contract(repo, user, start, end, hpw=None, total=None, rate=100, **extra):
    return repo.save(
        "contracts",
        dict(
            title="C",
            start=start.isoformat(),
            end=end.isoformat(),
            hours_per_week=hpw,
            total_hours=total,
            rate=rate,
            status="confirmed",
            user_ids=[user["id"]],
            **extra,
        ),
    )


def add_offer(repo, user, start, end, hpw=None, total=None, valid=None, status="sent", **plan_extra):
    plan = dict(
        start=start.isoformat(),
        end=end.isoformat(),
        hours_per_week=hpw,
        total_hours=total,
        user_ids=[user["id"]],
        **plan_extra,
    )
    return repo.save(
        "offers",
        dict(
            number="AN-2026-%04d" % (len(repo.list("offers")) + 1),
            status=status,
            plan=plan,
            valid_until=valid.isoformat() if valid else None,
            lines=[],
        ),
    )


def week_of(out, monday):
    return next(w for w in out["weeks"] if w["week"] == monday.isoformat())


def svc(repo, busy=None):
    return PlanningService(repo, busy)


# ------------------------------------------------------------------ golden capacity
def test_capacity_is_weekly_hours_on_workdays(repo):
    boss = add_user(repo)
    out = svc(repo).forecast(2, None, True, today=MON)
    w = week_of(out, MON)
    assert w["capacity"] == 40 and w["confirmed"] == 0 and w["free_after_confirmed"] == 40
    assert w["status"] == "ok" and w["utilisation"] == 0.0
    assert boss["id"]


def test_public_holiday_removes_one_day_of_capacity(repo):
    add_user(repo)
    # 2026-01-06 (Heilige Drei Könige, Bavaria) is a Tuesday: the week of 2026-01-05 has 4 workdays
    repo.save_settings({**repo.settings(), "state": "BY"})
    out = svc(repo).forecast(1, None, True, today=date(2026, 1, 5))
    assert out["weeks"][0]["week"] == "2026-01-05"
    assert out["weeks"][0]["capacity"] == 32
    assert out["weeks"][0]["holiday_hours"] == 8


def test_vacation_removes_capacity_and_is_counted_in_the_week(repo):
    user = add_user(repo)
    repo.save("absences", dict(user_id=user["id"], kind="vacation", start="2026-02-16", end="2026-02-20"))
    out = svc(repo).forecast(3, None, True, today=MON)
    assert week_of(out, MON)["capacity"] == 40
    w = week_of(out, date(2026, 2, 16))
    assert w["capacity"] == 0 and w["vacation_hours"] == 40
    assert w["status"] == "ok"  # nothing committed


def test_busy_time_reduces_capacity_on_that_day(repo):
    user = add_user(repo)
    busy = FakeBusy({(user["id"], date(2026, 2, 4)): 3.0, (user["id"], date(2026, 2, 5)): 99.0})
    w = week_of(svc(repo, busy).forecast(1, None, True, today=MON), MON)
    # Wed loses 3 h (8 -> 5), Thu is capped at its 8 h target (busy never makes capacity negative)
    assert w["capacity"] == pytest.approx(40 - 3 - 8)
    assert w["busy_hours"] == pytest.approx(3 + 8)


def test_part_time_workdays_spread_the_weekly_hours(repo):
    user = add_user(repo, weekly=24, workdays=[0, 1, 2])  # Mon-Wed, 8 h a day
    add_contract(repo, user, MON, MON + timedelta(days=6), hpw=12)
    w = week_of(svc(repo).forecast(1, None, True, today=MON), MON)
    assert w["capacity"] == 24
    assert w["confirmed"] == pytest.approx(12)  # 12 h over the 3 workdays of the full week


def test_partial_week_gets_its_share_of_the_full_week(repo):
    user = add_user(repo)
    # Wed 4th to Sun 8th: three of the five workdays -> 3/5 of 25 h
    add_contract(repo, user, date(2026, 2, 4), date(2026, 2, 8), hpw=25)
    w = week_of(svc(repo).forecast(1, None, True, today=MON), MON)
    assert w["confirmed"] == pytest.approx(15)


def test_total_hours_are_spread_over_the_workdays_of_the_period(repo):
    user = add_user(repo)
    add_contract(repo, user, MON, MON + timedelta(days=13), total=50)  # two full weeks, 10 workdays
    out = svc(repo).forecast(2, None, True, today=MON)
    assert week_of(out, MON)["confirmed"] == pytest.approx(25)
    assert week_of(out, MON + timedelta(days=7))["confirmed"] == pytest.approx(25)


def test_binding_offers_are_worst_case_and_expired_or_accepted_ones_are_not(repo):
    user = add_user(repo)
    add_offer(repo, user, MON, MON + timedelta(days=4), hpw=20, valid=date(2026, 3, 1))
    add_offer(repo, user, MON, MON + timedelta(days=4), hpw=20, valid=date(2026, 3, 1))  # overlapping
    add_offer(repo, user, MON, MON + timedelta(days=4), hpw=20, valid=date(2026, 1, 10))  # expired
    accepted = add_offer(repo, user, MON, MON + timedelta(days=4), hpw=20, valid=date(2026, 3, 1))
    repo.save(
        "contracts",
        dict(
            title="from accepted",
            start=MON.isoformat(),
            end=(MON + timedelta(days=4)).isoformat(),
            hours_per_week=20,
            status="confirmed",
            user_ids=[user["id"]],
            offer_id=accepted["id"],
        ),
    )
    out = svc(repo).forecast(1, None, True, today=MON)
    w = out["weeks"][0]
    assert w["binding_offers"] == pytest.approx(40)  # two open offers, worst case
    assert w["confirmed"] == pytest.approx(20)  # the accepted one, as a contract
    assert w["free_worst_case"] == pytest.approx(40 - 60)
    assert w["status"] == "over"
    assert len(out["expired_offers"]) == 1  # the offer that expired on 2026-01-10 is reported, not counted


def test_utilisation_status_thresholds(repo):
    assert eng.status_for(40, 0) == ("ok", 0.0)
    assert eng.status_for(40, 36)[0] == "ok"
    assert eng.status_for(40, 37)[0] == "tight"
    assert eng.status_for(40, 40)[0] == "tight"
    assert eng.status_for(40, 41)[0] == "over"
    assert eng.status_for(0, 5) == ("over", None)


def test_revenue_basis_precedence(repo):
    user = add_user(repo)
    add_contract(repo, user, MON, MON + timedelta(days=4), hpw=10, rate=100)  # week 1: contract
    add_offer(repo, user, MON + timedelta(days=7), MON + timedelta(days=11), hpw=10, rate=50)  # week 2: offer
    out = svc(repo).forecast(3, None, True, today=MON)
    w1, w2, w3 = out["weeks"]
    assert w1["basis"] == "contract" and w1["revenue"] == 1000.0
    assert w2["basis"] == "offer-binding" and w2["revenue"] == 500.0
    assert w3["basis"] == "intended" and w3["revenue"] == pytest.approx(40 * 80)  # default rate 80
    assert out["totals"]["contracted_revenue"] == 1000.0
    assert out["totals"]["assumed_revenue"] == pytest.approx(40 * 80)


# ------------------------------------------------------------------ feasibility and simulation
def test_offer_that_does_not_fit_gets_a_precise_warning(repo):
    user = add_user(repo)
    repo.save("absences", dict(user_id=user["id"], kind="vacation", start="2026-02-16", end="2026-02-20"))
    add_offer(repo, user, date(2026, 2, 2), date(2026, 2, 6), hpw=20, valid=date(2026, 3, 30))  # 20 h in week 1
    draft = dict(
        plan=dict(start="2026-01-12", end="2026-02-28", total_hours=120, user_ids=[user["id"]]),
        lines=[],
        status="draft",
    )
    warnings = svc(repo).check_offer(draft, today=TODAY)
    cap = [w for w in warnings if w["code"] == "capacity.offer_over"]
    assert cap, warnings
    msg = cap[0]["message"]
    assert "You offer 120 h between 12.01.2026 and 28.02.2026" in msg and "weeks of" in msg
    details = cap[0]["details"]
    assert details["over_weeks"] and details["free_hours"] > 0
    assert details["options"] and {o["type"] for o in details["options"]} <= {"shorten", "move_start", "reduce_scope"}
    assert cap[0]["confirmable"] is True and cap[0]["field"] == "plan"
    assert "vacation" in " ".join(details["causes"]) or "time off" in " ".join(details["causes"])


def test_offer_that_fits_has_no_capacity_warning(repo):
    user = add_user(repo)
    draft = dict(plan=dict(start="2026-02-02", end="2026-02-27", total_hours=40, user_ids=[user["id"]]), lines=[])
    assert not [w for w in svc(repo).check_offer(draft, today=TODAY) if w["code"] == "capacity.offer_over"]


def test_simulate_without_changes_equals_forecast(repo):
    user = add_user(repo)
    add_contract(repo, user, MON, MON + timedelta(days=20), hpw=15)
    add_offer(repo, user, MON + timedelta(days=28), MON + timedelta(days=40), hpw=30, valid=date(2026, 6, 1))
    s = svc(repo)
    a = s.forecast(6, None, True, today=MON)
    b = s.simulate({}, 6, MON, None, today=MON)
    assert a["weeks"] == b["weeks"] and a["totals"] == b["totals"]


@settings(max_examples=25, deadline=None)
@given(hpw=st.floats(0, 40, allow_nan=False), days=st.integers(0, 40), weeks=st.integers(1, 6))
def test_simulate_equals_forecast_for_random_contracts(hpw, days, weeks):
    """Property: with no change set the what-if engine returns exactly what the forecast returns."""
    import tempfile
    from pathlib import Path

    from permita38.core.repository.sqlite import SQLiteRepository as R

    with tempfile.TemporaryDirectory() as tmp:
        r = R(Path(tmp) / "d")
        r.startup()
        try:
            user = add_user(r)
            add_contract(r, user, MON + timedelta(days=days // 2), MON + timedelta(days=days), hpw=round(hpw, 2))
            s = svc(r)
            assert (
                s.forecast(weeks, None, True, today=MON)["weeks"]
                == s.simulate({}, weeks, MON, None, today=MON)["weeks"]
            )
        finally:
            r.close()


def test_simulate_never_writes(repo):
    user = add_user(repo)
    c = add_contract(repo, user, MON, MON + timedelta(days=30), hpw=20)
    before = json.dumps({k: repo.list(k) for k in TABLES if k not in ("settings",)}, sort_keys=True, default=str)
    svc(repo).simulate(
        {
            "absences": [{"user_id": user["id"], "start": "2026-02-09", "end": "2026-02-13"}],
            "commitments": [{"kind": "contract", "id": c["id"], "start": "2026-03-01", "end": "2026-04-01"}],
        },
        8,
        MON,
        user["id"],
        today=MON,
    )
    after = json.dumps({k: repo.list(k) for k in TABLES if k not in ("settings",)}, sort_keys=True, default=str)
    assert before == after


def test_what_if_changes_are_applied_to_the_simulation(repo):
    user = add_user(repo)
    c = add_contract(repo, user, MON, MON + timedelta(days=4), hpw=40)
    out = svc(repo).simulate(
        {"commitments": [{"kind": "contract", "id": c["id"], "hours_per_week": 20}]}, 1, MON, None, today=MON
    )
    assert out["weeks"][0]["confirmed"] == pytest.approx(20)
    vac = svc(repo).simulate(
        {"absences": [{"user_id": user["id"], "start": "2026-02-02", "end": "2026-02-06"}]},
        1,
        MON,
        user["id"],
        today=MON,
    )
    assert vac["weeks"][0]["capacity"] == 0


def test_suggest_vacation_avoids_the_fully_booked_week(repo):
    user = add_user(repo)
    add_contract(repo, user, date(2026, 3, 2), date(2026, 3, 6), hpw=40)  # week of 2 March fully booked
    res = svc(repo).suggest_vacation(user["id"], 3, date(2026, 2, 23), date(2026, 3, 13), today=TODAY)
    best = res["options"][0]
    assert best["overload_hours"] == 0
    assert best["start"] < "2026-03-02" or best["start"] > "2026-03-06"


def test_suggest_vacation_prefers_the_bridge_day(repo):
    user = add_user(repo)
    # Thu 2026-02-05 + Fri 2026-02-06 as the 2 vacation days: Mon 2 Feb 2026 weekend bridge adds free days
    res = svc(repo).suggest_vacation(user["id"], 2, date(2026, 2, 2), date(2026, 2, 13), today=TODAY)
    assert res["options"][0]["overload_hours"] == 0
    assert res["options"][0]["bridge_days"] >= 2


def test_suggest_start_is_the_earliest_feasible_day(repo):
    user = add_user(repo)
    add_contract(repo, user, MON, MON + timedelta(days=11), hpw=40)  # two fully booked weeks
    res = svc(repo).suggest_start(50, date(2026, 2, 27), date(2026, 2, 2), [user["id"]], today=TODAY)
    # the week of 9 Feb is fully booked; from Saturday 14 Feb the 50 h fit into the free workdays from Mon 16 Feb
    assert res["feasible"] is True and res["start"] == "2026-02-14"


def test_suggest_start_reports_infeasible(repo):
    user = add_user(repo)
    res = svc(repo).suggest_start(500, date(2026, 2, 27), date(2026, 2, 2), [user["id"]], today=TODAY)
    assert res["feasible"] is False and res["start"] is None


def test_what_must_move_gives_a_trim_when_nothing_else_can_move(repo):
    user = add_user(repo)
    add_contract(repo, user, MON, MON + timedelta(days=4), hpw=40)
    res = svc(repo).what_must_move(
        dict(start="2026-02-02", end="2026-02-06", total_hours=10, user_ids=[user["id"]]), today=TODAY
    )
    assert res["fits"] is False
    assert any(s["type"] == "trim" and s["max_hours"] == 0 for s in res["suggestions"])


def test_what_must_move_shifts_an_open_offer_out_of_the_way(repo):
    user = add_user(repo)
    add_offer(repo, user, MON, MON + timedelta(days=4), hpw=40, valid=date(2026, 6, 1))
    res = svc(repo).what_must_move(
        dict(start="2026-02-02", end="2026-02-06", total_hours=10, user_ids=[user["id"]]), today=TODAY
    )
    moves = [s for s in res["suggestions"] if s["type"] == "move"]
    assert moves and abs(moves[0]["days"]) == 7  # the smallest shift that clears the week


# ------------------------------------------------------------------ vacation entitlement
def test_entitlement_counts_working_days_without_holidays(repo):
    user = add_user(repo, vacation_days_per_year=30, vacation_carry_over=2)
    # 2026-06-01..05 is Mon-Fri; Corpus Christi (Fronleichnam) 2026-06-04 is a holiday only in some states
    repo.save_settings({**repo.settings(), "state": "BY"})
    repo.save("absences", dict(user_id=user["id"], kind="vacation", start="2026-06-01", end="2026-06-05"))
    repo.save("absences", dict(user_id=user["id"], kind="sick", start="2026-06-08", end="2026-06-09"))
    ent = svc(repo).entitlement(user["id"], 2026, today=TODAY)
    assert ent["entitlement"] == 32
    assert ent["planned"] == 4  # Corpus Christi is not counted (Bavaria); sick days are not vacation
    assert ent["remaining"] == 28 and ent["over"] is False


def test_entitlement_warns_when_planned_beyond_it(repo):
    user = add_user(repo, vacation_days_per_year=3)
    repo.save("absences", dict(user_id=user["id"], kind="vacation", start="2026-07-06", end="2026-07-10"))
    ent = svc(repo).entitlement(user["id"], 2026, today=TODAY)
    assert ent["remaining"] == -2 and ent["over"] is True


# ------------------------------------------------------------------ writes
def test_apply_needs_confirmation_and_is_atomic(app_client):
    c = app_client
    user = c.get("/api/auth/me").json()
    body = {
        "changes": {
            "absences": [{"user_id": user["id"], "start": "2026-03-02", "end": "2026-03-03"}],
            "commitments": [{"kind": "contract", "id": "missing0000", "hours_per_week": 3}],
        },
        "confirm": True,
    }
    assert c.post("/api/plan/apply", json={**body, "confirm": False}).status_code == 400
    before = len(c.get("/api/absences").json())
    assert c.post("/api/plan/apply", json=body).status_code == 404  # unknown contract
    assert len(c.get("/api/absences").json()) == before  # the absence was rolled back with it


def test_apply_rejects_inverted_or_unknown_absences_and_rolls_back(app_client):
    c = app_client
    me = c.get("/api/auth/me").json()
    a = c.post(
        "/api/absences", json={"user_id": me["id"], "kind": "vacation", "start": "2026-03-02", "end": "2026-03-06"}
    )
    aid = a.json()["id"]
    # moving only the start behind the stored end would store an inverted range
    r = c.post("/api/plan/apply", json={"confirm": True, "changes": {"absences": [{"id": aid, "start": "2026-04-01"}]}})
    assert r.status_code == 422
    r = c.post(
        "/api/plan/apply",
        json={"confirm": True, "changes": {"absences": [{"user_id": "nobody123456", "start": "2026-04-01"}]}},
    )
    assert r.status_code == 422
    stored = [x for x in c.get("/api/absences").json() if x["id"] == aid][0]
    assert (stored["start"], stored["end"]) == ("2026-03-02", "2026-03-06")


def test_scenario_crud_and_apply(app_client):
    c = app_client
    me = c.get("/api/auth/me").json()
    s = c.post(
        "/api/plan/scenarios",
        json={
            "name": "Skiing",
            "changes": {"absences": [{"user_id": me["id"], "start": "2026-03-02", "end": "2026-03-06"}]},
        },
    ).json()
    assert [x["name"] for x in c.get("/api/plan/scenarios").json()] == ["Skiing"]
    assert c.post(f"/api/plan/scenarios/{s['id']}/apply", json={"confirm": False}).status_code == 400
    assert c.post(f"/api/plan/scenarios/{s['id']}/apply", json={"confirm": True}).status_code == 200
    assert any(a["start"] == "2026-03-02" for a in c.get("/api/absences").json())
    assert c.delete(f"/api/plan/scenarios/{s['id']}").status_code == 204


def test_manual_extension_records_history_and_only_extends(app_client):
    c = app_client
    cust = c.post("/api/customers", json={"name": "ACME"}).json()
    p = c.post(
        "/api/projects", json={"name": "Site", "customer_id": cust["id"], "start": "2026-01-01", "end": "2026-03-31"}
    ).json()
    r = c.post(f"/api/projects/{p['id']}/extend", json={"new_end": "2026-06-30", "reason": "phase 2"}).json()
    assert r["project"]["end"] == "2026-06-30"
    assert r["project"]["extensions"][0]["kind"] == "manual"
    assert r["project"]["extensions"][0]["from"] == "2026-03-31"
    assert r["project"]["extensions"][0]["to"] == "2026-06-30"
    assert c.post(f"/api/projects/{p['id']}/extend", json={"new_end": "2026-02-01"}).status_code == 422
    # a normal update keeps the history (it is server-managed)
    updated = c.put(
        f"/api/projects/{p['id']}",
        json={
            "name": "Site 2",
            "customer_id": cust["id"],
            "start": "2026-01-01",
            "end": "2026-06-30",
            "extensions": [],
        },
    ).json()
    assert len(updated["extensions"]) == 1


def test_project_period_must_not_end_before_it_starts(app_client):
    c = app_client
    assert c.post("/api/projects", json={"name": "X", "start": "2026-05-01", "end": "2026-04-01"}).status_code == 422


def test_accepting_an_offer_extends_its_project_and_records_it(app_client):
    c = app_client
    cust = c.post("/api/customers", json={"name": "ACME"}).json()
    p = c.post(
        "/api/projects", json={"name": "Site", "customer_id": cust["id"], "start": "2026-01-01", "end": "2026-03-31"}
    ).json()
    off = c.post(
        "/api/offers",
        json={
            "customer_id": cust["id"],
            "project_id": p["id"],
            "lines": [{"description": "Dev", "qty": 40, "unit": "h", "price": 90}],
            "plan": {"start": "2026-04-01", "end": "2026-05-29", "hours_per_week": 10},
        },
    ).json()
    c.post(f"/api/offers/{off['id']}/finalize")
    out = c.post(f"/api/offers/{off['id']}/accept").json()
    assert out["project"]["id"] == p["id"]
    assert out["project"]["end"] == "2026-05-29" and out["project"]["start"] == "2026-01-01"
    ext = out["project"]["extensions"][-1]
    assert ext["kind"] == "offer" and ext["offer_id"] == off["id"] and ext["to"] == "2026-05-29"


# ------------------------------------------------------------------ consistency warnings (pure)
def test_warnings_for_numbers_that_do_not_add_up():
    settings_ = {"planning_min_rate": 60}
    doc = dict(
        lines=[dict(qty=40, unit="h", price=50, amount=2000)],
        plan=dict(start="2026-01-01", end="2026-02-25", hours_per_week=10),
    )  # 8 weeks = 80 h
    codes = {w["code"] for w in pw.document_warnings(doc, kind="offers", settings=settings_, reference_rate=80)}
    assert {
        "offer.hours_mismatch",
        "rate.below_minimum",
        "rate.below_reference",
        "rate.effective_below_minimum",
    } <= codes
    inv = dict(lines=[dict(qty=12, unit="h", price=90)])
    codes = {
        w["code"]
        for w in pw.document_warnings(
            inv, kind="invoices", settings={}, tracked_hours=10, offer_budget_hours=10, billed_on_offer_hours=5
        )
    }
    assert {"invoice.hours_exceed_tracked", "invoice.exceeds_offer_hours"} <= codes


def test_project_warnings_outside_period_and_budget():
    proj = dict(id="p1", start="2026-01-01", end="2026-01-31", budget_hours=5)
    entries = [dict(date="2026-01-10", minutes=60), dict(date="2026-02-02", minutes=600)]
    codes = {w["code"] for w in pw.project_warnings(proj, entries, [], customer_linked=True)}
    assert "project.entries_outside_period" in codes
    assert [w["code"] for w in pw.budget_warning(proj, 11)] == ["project.over_budget"]
    nodates = {w["code"] for w in pw.project_warnings({"id": "p2"}, [], [], customer_linked=True)}
    assert nodates == {"project.no_dates"}


# ------------------------------------------------------------------ migration 0003 on existing data
def test_revision_two_database_is_upgraded_and_projects_get_their_period(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    raw = sqlite3.connect(data / DB_FILENAME)
    raw.executescript((MIGRATIONS_DIR / "sqlite" / "0001_initial.sql").read_text())
    raw.executescript((MIGRATIONS_DIR / "sqlite" / "0002_totp.sql").read_text())
    raw.execute("PRAGMA user_version = 2")
    raw.execute("INSERT INTO customers (id, name) VALUES ('cust00000001', 'ACME')")
    raw.execute(
        "INSERT INTO projects (id, name, customer_id, status) VALUES ('proj00000001', 'Site', 'cust00000001', 'active')"
    )
    raw.execute("INSERT INTO projects (id, name, status) VALUES ('proj00000002', 'Empty', 'active')")
    raw.execute(
        "INSERT INTO contracts (id, title, project_id, start, end, status) VALUES "
        "('contr0000001', 'C', 'proj00000001', '2026-02-01', '2026-03-01', 'confirmed')"
    )
    raw.execute(
        "INSERT INTO entries (id, project_id, date, minutes) VALUES ('entry0000001', 'proj00000001', '2026-01-20', 60)"
    )
    raw.execute("INSERT INTO projects (id, name, status) VALUES ('proj00000003', 'Late', 'active')")
    raw.execute(
        "INSERT INTO entries (id, project_id, date, minutes) VALUES ('entry0000002', 'proj00000001', '2026-03-15', 60)"
    )
    raw.execute(
        "INSERT INTO entries (id, project_id, date, minutes) VALUES ('entry0000003', 'proj00000003', '2026-05-04', 60)"
    )
    raw.commit()
    raw.close()
    repo = SQLiteRepository(data)
    repo.startup()
    assert repo.get_revision() == CURRENT_REVISION == 4
    p = repo.get("projects", "proj00000001")
    assert p["start"] == "2026-01-20" and p["end"] == "2026-03-15"  # entries widen the contract period
    late = repo.get("projects", "proj00000003")
    assert late["start"] == late["end"] == "2026-05-04"  # entries only
    assert repo.get("projects", "proj00000002").get("start") is None
    assert list((data / "backups").glob("pre-migration-r2-to-r4-*.sqlite3"))
    repo.close()


def test_migration_files_exist_for_every_dialect():
    for d in DIALECT_NAMES:
        assert (MIGRATIONS_DIR / d / "0003_planning.sql").exists(), d
        assert "plan_scenarios" in (MIGRATIONS_DIR / d / "0003_planning.sql").read_text()


# ------------------------------------------------------------------ fixtures for API tests
@pytest.fixture
def app_client(tmp_path):
    from tests.backend.conftest import PASSWORD, _login

    cfg = Config(data_dir=tmp_path / "data", secret_key="test-secret-key", web_dir=None)
    app = get_app(cfg)
    with TestClient(app) as anon:
        r = anon.post(
            "/api/auth/setup",
            json={"username": "boss", "name": "Test Boss", "password": PASSWORD, "company_name": "Demo Studio"},
        )
        assert r.status_code == 200, r.text
        c = _login(app, "boss")
        yield c
        c.close()
