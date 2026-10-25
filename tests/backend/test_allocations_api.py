"""Employee allocations and the planner-per-project routes over HTTP (docs/contracts/planner-per-project.md):
CRUD, validation bounds, rights, error codes, bootstrap, day table, forecast/simulate parameters, apply."""

import pytest
from fastapi.testclient import TestClient

from permita38.core.service.plan_service import MAX_DAY_TABLE_DAYS

START, END = "2026-03-02", "2026-03-27"


@pytest.fixture
def store(app, boss_client):
    return app.state.services.store


@pytest.fixture
def boss_id(store):
    return next(u["id"] for u in store.list("users") if u["role"] == "boss")


@pytest.fixture
def project(boss_client):
    r = boss_client.post("/api/projects", json={"name": "Demo project P"})
    assert r.status_code == 200, r.text
    return r.json()


def body(boss_id, project, **kw):
    return {
        "user_id": boss_id,
        "project_id": project["id"],
        "start": START,
        "end": END,
        "hours_per_week": 25.0,
        **kw,
    }


def read_only_client(app, boss_client):
    r = boss_client.post("/api/tokens", json={"label": "ro", "read_only": True})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    return c


# ------------------------------------------------------------------ CRUD
def test_create_list_update_delete(boss_client, boss_id, project, store):
    r = boss_client.post("/api/allocations", json=body(boss_id, project))
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["kind"] == "employee" and row["status"] == "active" and row["planning_warnings"] == []
    assert row["project_name"] == "Demo project P" and row["user_name"] == "Test Boss"
    assert row["hours_per_week"] == 25.0 and row["planned_hours"] == 100.0  # 4 weeks x 25 h, no holiday
    assert "supplier_id" not in row and "cancelled_from" not in row
    stored = store.get("allocations", row["id"])
    assert stored["user_id"] == boss_id and "supplier_id" not in stored

    listing = boss_client.get("/api/allocations").json()
    assert [x["id"] for x in listing] == [row["id"]] and "planning_warnings" not in listing[0]

    r = boss_client.put(
        f"/api/allocations/{row['id']}", json=body(boss_id, project, hours_per_week=30.0, end="2026-03-13")
    )
    assert r.status_code == 200, r.text
    assert r.json()["hours_per_week"] == 30.0 and r.json()["planned_hours"] == 60.0 and r.json()["id"] == row["id"]
    assert r.json()["created"] == row["created"]

    assert boss_client.delete(f"/api/allocations/{row['id']}").status_code == 204
    assert boss_client.get("/api/allocations").json() == []
    assert boss_client.delete(f"/api/allocations/{row['id']}").status_code == 404


def test_planned_hours_skip_holidays_and_absence_days(boss_client, boss_id, project, store):
    store.save("absences", {"user_id": boss_id, "kind": "vacation", "start": "2026-03-04", "end": "2026-03-04"})
    r = boss_client.post("/api/allocations", json=body(boss_id, project))
    assert r.json()["planned_hours"] == 95.0  # 19 available days x 5 h; the vacation day carries no hours


def test_list_filters_and_order(boss_client, boss_id, project):
    other = boss_client.post("/api/projects", json={"name": "Demo project Q"}).json()
    a = boss_client.post("/api/allocations", json=body(boss_id, project, start="2026-03-02")).json()
    b = boss_client.post("/api/allocations", json=body(boss_id, other, start="2026-04-06", end="2026-04-17")).json()
    assert [x["id"] for x in boss_client.get("/api/allocations").json()] == [b["id"], a["id"]]  # start descending
    assert [x["id"] for x in boss_client.get("/api/allocations", params={"project_id": project["id"]}).json()] == [
        a["id"]
    ]
    assert boss_client.get("/api/allocations", params={"user_id": "nobody0001"}).json() == []
    assert len(boss_client.get("/api/allocations", params={"status": "active"}).json()) == 2
    assert boss_client.get("/api/allocations", params={"status": "cancelled"}).json() == []
    assert boss_client.get("/api/allocations", params={"status": "bogus"}).status_code == 422
    assert boss_client.get("/api/allocations", params={"project_id": "unassigned!"}).status_code == 422


def test_warnings_come_with_the_saved_row(boss_client, boss_id, project):
    boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=30.0))
    r = boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=20.0, end="2026-03-13"))
    assert r.status_code == 201
    codes = [(w["code"], w["details"]["week"]) for w in r.json()["planning_warnings"]]
    assert codes == [
        ("allocation.employee_over_allocated", "2026-03-02"),
        ("allocation.employee_over_allocated", "2026-03-09"),
        ("allocation.overlap_same_project", "2026-03-02"),
        ("allocation.overlap_same_project", "2026-03-09"),
    ]
    over = r.json()["planning_warnings"][0]
    assert over["details"]["allocated"] == 50.0 and over["details"]["over_by"] == 10.0 and over["confirmable"] is True
    assert len(r.json()["planning_warnings"][2]["details"]["allocation_ids"]) == 2


def test_warnings_are_capped_at_ten_weeks(boss_client, boss_id, project):
    boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=40.0, end="2026-12-31"))
    r = boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=10.0, end="2026-12-31"))
    over = [w for w in r.json()["planning_warnings"] if w["code"] == "allocation.employee_over_allocated"]
    assert len(over) == 10 and over[0]["details"]["week"] == "2026-03-02"


# ------------------------------------------------------------------ errors
def test_error_codes(boss_client, boss_id, project, store):
    assert boss_client.post("/api/allocations", json=body("nobody0001", project)).status_code == 404
    assert boss_client.post("/api/allocations", json=body(boss_id, {"id": "nothing0001"})).json() == {
        "detail": "Project not found"
    }
    r = boss_client.post("/api/allocations", json=body(boss_id, project, start="2026-03-10", end="2026-03-02"))
    assert r.status_code == 409 and r.json() == {"detail": "The end date is before the start date"}
    for status in ("done", "archived"):
        closed = store.save("projects", {"name": "Closed", "status": status})
        r = boss_client.post("/api/allocations", json=body(boss_id, closed))
        assert r.status_code == 409 and r.json() == {"detail": "The project is closed"}
    paused = store.save("projects", {"name": "Paused", "status": "paused"})
    assert boss_client.post("/api/allocations", json=body(boss_id, paused)).status_code == 201
    gone = store.save("users", {"username": "gone", "name": "Gone", "role": "employee", "active": False})
    r = boss_client.post("/api/allocations", json=body(gone["id"], project))
    assert r.status_code == 409 and r.json() == {"detail": "The user is not active"}
    assert boss_client.put("/api/allocations/unknown0001", json=body(boss_id, project)).status_code == 404
    assert boss_client.put("/api/allocations/BAD!", json=body(boss_id, project)).status_code == 422


def test_closed_project_allocations_can_still_be_deleted(boss_client, boss_id, project, store):
    row = boss_client.post("/api/allocations", json=body(boss_id, project)).json()
    store.save("projects", {**store.get("projects", project["id"]), "status": "done"})
    assert boss_client.put(f"/api/allocations/{row['id']}", json=body(boss_id, project)).status_code == 409
    assert boss_client.delete(f"/api/allocations/{row['id']}").status_code == 204


def test_external_rows_cannot_be_changed_here(boss_client, boss_id, project, store):
    ext = store.save(
        "allocations",
        {"supplier_id": "supp0001", "project_id": project["id"], "start": START, "end": END, "status": "active"},
    )
    r = boss_client.put(f"/api/allocations/{ext['id']}", json=body(boss_id, project))
    assert r.status_code == 409 and r.json() == {"detail": "This allocation cannot be changed here"}
    assert boss_client.delete(f"/api/allocations/{ext['id']}").status_code == 409
    assert boss_client.get("/api/allocations").json() == []  # a caller without `procurement` does not see them
    assert store.get("allocations", ext["id"]) is not None


@pytest.mark.parametrize(
    "patch",
    [
        {"hours_per_week": 0.24},
        {"hours_per_week": 80.01},
        {"hours_per_week": -1},
        {"hours_per_week": "many"},
        {"hours_per_week": None},
        {"start": "02.03.2026"},
        {"end": "2026-02-30"},
        {"user_id": "x"},
        {"user_id": ""},
        {"project_id": "UPPER-case"},
        {"end": "2032-01-01"},  # longer than 1830 days
        {"unknown": 1},
        {"status": "cancelled"},
        {"kind": "external"},
    ],
)
def test_body_validation_is_strict(boss_client, boss_id, project, patch):
    r = boss_client.post("/api/allocations", json={**body(boss_id, project), **patch})
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], list)
    assert boss_client.get("/api/allocations").json() == []


def test_body_bounds_are_inclusive(boss_client, boss_id, project):
    for hpw in (0.25, 80):
        assert boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=hpw)).status_code == 201
    ok = body(boss_id, project, start="2026-01-01", end="2030-12-31")  # exactly 1825 days
    assert boss_client.post("/api/allocations", json=ok).status_code == 201
    for missing in ("user_id", "project_id", "start", "end", "hours_per_week"):
        b = body(boss_id, project)
        b.pop(missing)
        assert boss_client.post("/api/allocations", json=b).status_code == 422
    assert boss_client.post("/api/allocations", json=body(boss_id, {"id": "unassigned"})).status_code == 422
    assert (
        boss_client.post("/api/allocations", content=b"[1]", headers={"content-type": "application/json"}).status_code
        == 422
    )


# ------------------------------------------------------------------ rights
ROUTES = [
    ("GET", "/api/allocations", None),
    ("POST", "/api/allocations", "body"),
    ("PUT", "/api/allocations/abcdef12", "body"),
    ("DELETE", "/api/allocations/abcdef12", None),
    ("POST", "/api/allocations/bootstrap", {"confirm": False}),
    ("GET", "/api/plan/day-table?from=2026-03-02&to=2026-03-03", None),
]


def test_employee_and_anonymous_get_nothing(client, member_client, boss_id, project):
    for method, path, payload in ROUTES:
        payload = body(boss_id, project) if payload == "body" else payload
        r = member_client.request(method, path, json=payload)
        assert r.status_code == 403, (method, path, r.text)
        assert client.request(method, path, json=payload).status_code == 401, (method, path)


def test_read_only_token_reads_but_never_writes(app, boss_client, boss_id, project):
    ro = read_only_client(app, boss_client)
    assert ro.get("/api/allocations").status_code == 200
    assert ro.get("/api/plan/day-table", params={"from": START, "to": START}).status_code == 200
    assert ro.get("/api/forecast").status_code == 200
    assert ro.post("/api/plan/simulate", json={}).status_code == 403  # existing guard: read-only tokens never POST
    row = boss_client.post("/api/allocations", json=body(boss_id, project)).json()
    for method, path, payload in [
        ("POST", "/api/allocations", body(boss_id, project)),
        ("PUT", f"/api/allocations/{row['id']}", body(boss_id, project)),
        ("DELETE", f"/api/allocations/{row['id']}", None),
        ("POST", "/api/allocations/bootstrap", {"confirm": True}),
    ]:
        assert ro.request(method, path, json=payload).status_code == 403, (method, path)
    assert len(boss_client.get("/api/allocations").json()) == 1


# ------------------------------------------------------------------ bootstrap
def add_contract(store, boss_id, project, hpw, **kw):
    return store.save(
        "contracts",
        {
            "title": "Contract C_P",
            "project_id": project["id"] if project else None,
            "start": START,
            "end": END,
            "hours_per_week": hpw,
            "rate": 100,
            "status": "confirmed",
            "user_ids": [boss_id],
            **kw,
        },
    )


def test_bootstrap_preview_equals_the_write_and_a_second_run_creates_nothing(boss_client, boss_id, project, store):
    add_contract(store, boss_id, project, 30)
    add_contract(store, boss_id, None, 8, title="N")
    pre = boss_client.post("/api/allocations/bootstrap", json={}).json()
    assert pre["confirm"] is False and pre["created"] is False and pre["skipped"] == [] and pre["warnings"] == []
    assert len(pre["allocations"]) == 1 and pre["allocations"][0]["hours_per_week"] == 30.0
    assert store.list("allocations") == []
    done = boss_client.post("/api/allocations/bootstrap", json={"confirm": True})
    assert done.status_code == 200
    made = done.json()
    assert made["created"] is True and made["confirm"] is True
    assert [{k: v for k, v in a.items() if k not in ("id", "kind")} for a in made["allocations"]] == pre["allocations"]
    assert made["allocations"][0]["kind"] == "employee"
    row = store.get("allocations", made["allocations"][0]["id"])
    assert row["hours_per_week"] == 30.0 and row["start"] == START and "contract_id" not in row
    again = boss_client.post("/api/allocations/bootstrap", json={"confirm": True}).json()
    assert again["allocations"] == [] and [s["reason"] for s in again["skipped"]] == ["already_allocated"]
    assert len(store.list("allocations")) == 1
    assert boss_client.post("/api/allocations/bootstrap", json={"confirm": "yes please"}).status_code == 422
    assert boss_client.post("/api/allocations/bootstrap", json={"other": 1}).status_code == 422


def test_bootstrap_skips_closed_projects_done_contracts_and_long_periods(boss_client, boss_id, project, store):
    closed = store.save("projects", {"name": "Old", "status": "archived"})
    add_contract(store, boss_id, closed, 10)
    add_contract(store, boss_id, project, 10, status="done")
    add_contract(store, boss_id, project, 10, end="2032-01-01", title="Long")
    res = boss_client.post("/api/allocations/bootstrap", json={"confirm": True}).json()
    assert res["allocations"] == []
    assert [s["reason"] for s in res["skipped"]] == ["period_too_long"]
    assert store.list("allocations") == []


def test_bootstrap_over_500_proposals_is_a_409_and_writes_nothing(boss_client, boss_id, project, store):
    for _ in range(501):
        add_contract(store, boss_id, project, 1)
    r = boss_client.post("/api/allocations/bootstrap", json={"confirm": True})
    assert r.status_code == 409 and r.json() == {"detail": "Too many allocations to create at once (max 500)"}
    assert store.list("allocations") == []


# ------------------------------------------------------------------ planner routes
def test_forecast_parameters(boss_client, boss_id, project, store):
    add_contract(store, boss_id, project, 30)
    r = boss_client.get("/api/forecast", params={"weeks": 2})
    assert r.status_code == 200
    out = r.json()
    assert all("projects" in w and "gap" in w for w in out["weeks"])
    assert out["totals"]["gap_by_project"] == [] and out["totals"]["gap"] == 0.0  # gaps start with the next Monday
    assert out["contracts"][0]["project_id"] == project["id"]
    flat = boss_client.get("/api/forecast", params={"weeks": 2, "by_project": 0}).json()
    assert all("projects" not in w for w in flat["weeks"])
    one = boss_client.get("/api/forecast", params={"weeks": 2, "project_id": project["id"]}).json()
    assert [p["project_id"] for p in one["weeks"][0]["projects"]] == [project["id"]]
    assert boss_client.get("/api/forecast", params={"project_id": "unassigned"}).status_code == 200
    r = boss_client.get("/api/forecast", params={"project_id": "nothing0001"})
    assert r.status_code == 404 and r.json() == {"detail": "Project not found"}
    assert boss_client.get("/api/forecast", params={"by_project": 2}).status_code == 422
    assert boss_client.get("/api/forecast", params={"project_id": "Bad Id"}).status_code == 422


def test_day_table_route(boss_client, boss_id, project):
    boss_client.post("/api/allocations", json=body(boss_id, project))
    r = boss_client.get("/api/plan/day-table", params={"from": "2026-03-02", "to": "2026-03-08"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["from"] == "2026-03-02" and out["to"] == "2026-03-08" and len(out["days"]) == 7
    mon, sat = out["days"][0], out["days"][5]
    assert mon["cap"] == 8.0 and mon["scopes"][project["id"]]["internal"]["cap"] == 5.0
    assert mon["scopes"]["unassigned"]["internal"]["cap"] == 3.0
    assert out["scope_names"] == {project["id"]: "Demo project P", "unassigned": "Unassigned"}
    assert sat["cap"] == 0.0 and sat["scopes"] == {}
    only = boss_client.get(
        "/api/plan/day-table", params={"from": "2026-03-02", "to": "2026-03-02", "project_id": "unassigned"}
    ).json()
    assert list(only["days"][0]["scopes"]) == ["unassigned"]
    user = boss_client.get("/api/plan/day-table", params={"from": "2026-03-02", "to": "2026-03-02", "user_id": boss_id})
    assert user.status_code == 200


def test_day_table_errors(boss_client):
    def get(**p):
        return boss_client.get("/api/plan/day-table", params=p)

    assert get(**{"from": "2026-03-02"}).status_code == 422
    assert get(**{"from": "2026-13-02", "to": "2026-03-03"}).status_code == 422
    r = get(**{"from": "2026-03-05", "to": "2026-03-02"})
    assert r.status_code == 400 and r.json() == {"detail": "'to' is before 'from'"}
    assert get(**{"from": "2026-01-01", "to": "2026-07-09"}).status_code == 200  # exactly 190 days
    r = get(**{"from": "2026-01-01", "to": "2026-07-10"})
    assert r.status_code == 400 and r.json() == {"detail": f"The range may cover at most {MAX_DAY_TABLE_DAYS} days"}
    assert get(**{"from": "2026-03-02", "to": "2026-03-03", "user_id": "nobody0001"}).json() == {
        "detail": "User not found"
    }
    assert get(**{"from": "2026-03-02", "to": "2026-03-03", "project_id": "nobody0001"}).json() == {
        "detail": "Project not found"
    }


def test_simulate_with_allocation_changes_and_delta(boss_client, boss_id, project, store):
    add_contract(store, boss_id, project, 30)
    a = boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=20.0)).json()
    base = {"weeks": 4, "first": START}
    sim = boss_client.post(
        "/api/plan/simulate", json={**base, "changes": {"allocations": [{"id": a["id"], "hours_per_week": 30.0}]}}
    )
    assert sim.status_code == 200, sim.text
    out = sim.json()
    assert out["delta"]["gap"] == -40.0  # gap 10 h/week for 4 weeks -> 0
    assert out["delta"]["projects"] == [
        {"project_id": project["id"], "name": "Demo project P", "base_gap": 40.0, "gap": 0.0, "delta": -40.0}
    ]
    assert out["weeks"][0]["delta"] == -10.0 and out["weeks"][0]["projects"][0]["delta"] == -10.0
    new = {"user_id": boss_id, "project_id": project["id"], "start": START, "end": END, "hours_per_week": 5.0}
    assert boss_client.post("/api/plan/simulate", json={**base, "changes": {"allocations": [new]}}).status_code == 200
    assert len(store.list("allocations")) == 1  # simulate writes nothing

    # errors
    def sim_err(change):
        return boss_client.post("/api/plan/simulate", json={**base, "changes": {"allocations": [change]}})

    assert sim_err({"id": "nothing0001", "hours_per_week": 5}).json() == {"detail": "Allocation not found"}
    ext = store.save(
        "allocations", {"supplier_id": "supp0001", "project_id": project["id"], "start": START, "end": END}
    )
    assert sim_err({"id": ext["id"], "hours_per_week": 5}).status_code == 409
    assert sim_err({**new, "user_id": "nobody0001"}).json() == {"detail": "User not found"}
    assert sim_err({**new, "project_id": "nobody0001"}).json() == {"detail": "Project not found"}
    assert sim_err({"delete": True}).status_code == 422
    assert sim_err({"user_id": boss_id}).status_code == 422  # a new allocation needs all fields
    assert sim_err({**new, "hours_per_week": 81}).status_code == 422
    assert sim_err({**new, "end": "2020-01-01"}).status_code == 422
    assert boss_client.post("/api/plan/simulate", json={**base, "project_id": "nothing0001"}).status_code == 404


def test_apply_writes_allocation_rows_with_confirm(boss_client, boss_id, project, store):
    a = boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=30.0)).json()
    gone = boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=5.0)).json()
    new = {
        "user_id": boss_id,
        "project_id": project["id"],
        "start": "2026-04-06",
        "end": "2026-04-10",
        "hours_per_week": 20.0,
    }
    changes = {"allocations": [new, {"id": a["id"], "hours_per_week": 35.0}, {"id": gone["id"], "delete": True}]}
    r = boss_client.post("/api/plan/apply", json={"changes": changes, "confirm": False})
    assert r.status_code == 400 and len(store.list("allocations")) == 2
    r = boss_client.post("/api/plan/apply", json={"changes": changes, "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 3 and isinstance(r.json()["allocation_warnings"], list)
    rows = {x["id"]: x for x in store.list("allocations")}
    assert gone["id"] not in rows and rows[a["id"]]["hours_per_week"] == 35.0 and len(rows) == 2
    # one bad change rolls everything back
    bad = {"allocations": [{"id": a["id"], "hours_per_week": 10.0}, {"id": "nothing0001", "hours_per_week": 1}]}
    assert boss_client.post("/api/plan/apply", json={"changes": bad, "confirm": True}).status_code == 404
    assert store.get("allocations", a["id"])["hours_per_week"] == 35.0


def test_scenarios_keep_allocation_changes(boss_client, boss_id, project):
    ch = {
        "allocations": [
            {"user_id": boss_id, "project_id": project["id"], "start": START, "end": END, "hours_per_week": 8}
        ]
    }
    r = boss_client.post("/api/plan/scenarios", json={"name": "What if", "changes": ch})
    assert r.status_code == 200 and r.json()["changes"]["allocations"][0]["hours_per_week"] == 8
    listed = boss_client.get("/api/plan/scenarios").json()
    assert listed[0]["changes"]["allocations"][0]["project_id"] == project["id"]
    ap = boss_client.post(f"/api/plan/scenarios/{r.json()['id']}/apply", json={"confirm": True})
    assert ap.status_code == 200 and len(boss_client.get("/api/allocations").json()) == 1


def test_check_offer_with_a_project_reports_the_project_gap(boss_client, boss_id, project):
    plan = {"start": START, "end": END, "hours_per_week": 10, "user_ids": [boss_id]}
    r = boss_client.post("/api/plan/check-offer", json={"project_id": project["id"], "plan": plan, "lines": []})
    assert r.status_code == 200, r.text
    gaps = [w for w in r.json() if w["code"] == "offer.project_gap"]
    assert len(gaps) == 4 and gaps[0]["details"]["capacity"] == 0.0 and gaps[0]["details"]["gap"] == 10.0
    boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=10.0))
    r = boss_client.post("/api/plan/check-offer", json={"project_id": project["id"], "plan": plan, "lines": []})
    assert [w for w in r.json() if w["code"] in ("offer.project_gap", "capacity.offer_over")] == []
    r = boss_client.post("/api/plan/check-offer", json={"plan": plan, "lines": []})
    assert [w["code"] for w in r.json() if w["code"].startswith(("offer.", "capacity."))] == []  # unassigned: 30 h free


def test_suggest_start_per_project(boss_client, boss_id, project):
    payload = {"hours": 40, "deadline": "2026-03-27", "earliest": "2026-03-02", "user_ids": [boss_id]}
    free = boss_client.post("/api/plan/suggest-start", json=payload).json()
    assert free["feasible"] is True and free["start"] == "2026-03-02"
    proj = boss_client.post("/api/plan/suggest-start", json={**payload, "project_id": project["id"]}).json()
    assert proj["feasible"] is False  # the project has no allocation: unassigned hours are not its hours
    boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=20.0))
    proj = boss_client.post("/api/plan/suggest-start", json={**payload, "project_id": project["id"]}).json()
    assert proj["feasible"] is True and proj["start"] == "2026-03-02"
    assert boss_client.post("/api/plan/suggest-start", json={**payload, "project_id": "nothing0001"}).status_code == 404


def test_cancelled_rows_and_referenced_deletes_are_409(boss_client, boss_id, project, store):
    row = store.save(
        "allocations",
        {
            "user_id": boss_id,
            "project_id": project["id"],
            "start": START,
            "end": END,
            "hours_per_week": 5,
            "status": "cancelled",
        },
    )
    assert boss_client.put(f"/api/allocations/{row['id']}", json=body(boss_id, project)).status_code == 409
    assert boss_client.delete(f"/api/allocations/{row['id']}").status_code == 409
    listed = boss_client.get("/api/allocations").json()  # no status filter: every status
    assert [x["status"] for x in listed] == ["cancelled"] and listed[0]["user_name"] == "Test Boss"
    r = boss_client.delete(f"/api/projects/{project['id']}")
    assert r.status_code == 409 and store.get("projects", project["id"]) is not None
    emp = boss_client.post(
        "/api/users", json={"username": "eva", "name": "Eva", "role": "employee", "password": "correct horse battery"}
    ).json()
    boss_client.post("/api/allocations", json=body(emp["id"], project))
    assert boss_client.delete(f"/api/users/{emp['id']}").status_code == 409
    assert store.get("users", emp["id"]) is not None


def test_deactivating_a_user_keeps_allocations_but_blocks_editing(boss_client, boss_id, project, store):
    emp = store.save("users", {"username": "eva", "name": "Eva", "role": "employee", "active": True})
    row = boss_client.post("/api/allocations", json=body(emp["id"], project)).json()
    store.save("users", {**emp, "active": False})
    assert boss_client.get("/api/allocations").json()[0]["id"] == row["id"]
    r = boss_client.put(f"/api/allocations/{row['id']}", json=body(emp["id"], project, hours_per_week=9.0))
    assert r.status_code == 409 and r.json() == {"detail": "The user is not active"}


def test_simulate_scopes_its_base_by_project(boss_client, boss_id, project, store):
    other = boss_client.post("/api/projects", json={"name": "Demo project Q"}).json()
    add_contract(store, boss_id, project, 30)
    add_contract(store, boss_id, other, 10, title="Q")
    out = boss_client.post("/api/plan/simulate", json={"weeks": 1, "first": START, "project_id": other["id"]}).json()
    assert [p["project_id"] for p in out["weeks"][0]["projects"]] == [other["id"]]
    assert out["weeks"][0]["gap"] == 10.0 and out["weeks"][0]["delta"] == 0.0
    assert out["delta"] == {
        "gap": 0.0,
        "projects": [
            {"project_id": other["id"], "name": "Demo project Q", "base_gap": 10.0, "gap": 10.0, "delta": 0.0}
        ],
    }
