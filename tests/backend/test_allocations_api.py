"""Employee allocations and the planner-per-project routes over HTTP (docs/contracts/planner-per-project.md):
CRUD, validation bounds, rights, error codes, simulate/apply parameters. (The forecast, the day table and the
bootstrap were removed by the planner rework; ``GET /api/plan`` is covered by test_plan_api.py.)"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import permita38.core.service.allocations as allocations_module
import permita38.core.service.planner.service as plan_module
from permita38.core.holidays import holidays as de_holidays

# The hand numbers of this file are for a fixed calendar: the first Monday after ANCHOR from which eight weeks hold no
# national holiday. The anchor is a fixed date, so the file gives the same answers on every weekday.
ANCHOR = date(2026, 10, 26)


def _clean_monday(after: date) -> date:
    """The first Monday after ``after`` from which 8 weeks hold no national holiday (the hand numbers assume none)."""
    x = after + timedelta(days=(7 - after.weekday()) % 7 or 7)
    while any(calc_holidays(x + timedelta(days=k)) for k in range(0, 56)):
        x += timedelta(days=7)
    return x


def calc_holidays(x: date):
    return de_holidays(x.year, "").get(x)


MONDAY = _clean_monday(ANCHOR)  # 2027-01-04: Christmas falls into the weeks after the anchor


def day(offset: int) -> str:
    return (MONDAY + timedelta(days=offset)).isoformat()


START, END = day(0), day(25)


class _FrozenToday(date):
    """The service calls ``date.today()``; the file's week numbers assume today is the anchor Monday ``MONDAY``."""

    @classmethod
    def today(cls):
        return MONDAY


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setattr(plan_module, "date", _FrozenToday)  # forecast and day table start at the week of today
    monkeypatch.setattr(allocations_module, "date", _FrozenToday)  # bootstrap proposals start from today


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

    r = boss_client.put(f"/api/allocations/{row['id']}", json=body(boss_id, project, hours_per_week=30.0, end=day(11)))
    assert r.status_code == 200, r.text
    assert r.json()["hours_per_week"] == 30.0 and r.json()["planned_hours"] == 60.0 and r.json()["id"] == row["id"]
    assert r.json()["created"] == row["created"]

    deleted = boss_client.delete(f"/api/allocations/{row['id']}")
    assert deleted.status_code == 204
    assert boss_client.get("/api/allocations").json() == []
    deleted = boss_client.delete(f"/api/allocations/{row['id']}")
    assert deleted.status_code == 404


def test_planned_hours_skip_holidays_and_absence_days(boss_client, boss_id, project, store):
    store.save("absences", {"user_id": boss_id, "kind": "vacation", "start": day(2), "end": day(2)})
    r = boss_client.post("/api/allocations", json=body(boss_id, project))
    # S1: a week's hours are spread over its available days, so a vacation day moves hours, it does not remove them
    assert r.json()["planned_hours"] == 100.0
    r = boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=None, total_hours=38.0))
    assert r.json()["planned_hours"] == 38.0 and r.json()["total_hours"] == 38.0 and "hours_per_week" not in r.json()


def test_list_filters_and_order(boss_client, boss_id, project):
    other = boss_client.post("/api/projects", json={"name": "Demo project Q"}).json()
    a = boss_client.post("/api/allocations", json=body(boss_id, project, start=day(0))).json()
    b = boss_client.post("/api/allocations", json=body(boss_id, other, start=day(35), end=day(46))).json()
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
    r = boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=20.0, end=day(11)))
    assert r.status_code == 201
    codes = [(w["code"], w["details"]["week"]) for w in r.json()["planning_warnings"]]
    assert codes == [
        ("assignment.over_capacity", day(0)),
        ("assignment.over_capacity", day(7)),
        ("assignment.overlap", day(0)),
        ("assignment.overlap", day(7)),
    ]
    over = r.json()["planning_warnings"][0]
    assert over["details"]["assigned"] == 50.0 and over["details"]["over_by"] == 10.0 and over["confirmable"] is True
    assert len(r.json()["planning_warnings"][2]["details"]["allocation_ids"]) == 2


def test_warnings_are_capped_at_ten_weeks(boss_client, boss_id, project):
    boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=40.0, end=day(77)))  # 11 weeks
    r = boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=10.0, end=day(77)))
    over = [w for w in r.json()["planning_warnings"] if w["code"] == "assignment.over_capacity"]
    assert len(over) == 10 and over[0]["details"]["week"] == day(0)


# ------------------------------------------------------------------ errors
def test_error_codes(boss_client, boss_id, project, store):
    assert boss_client.post("/api/allocations", json=body("nobody0001", project)).status_code == 404
    assert boss_client.post("/api/allocations", json=body(boss_id, {"id": "nothing0001"})).json() == {
        "detail": "Project not found"
    }
    r = boss_client.post("/api/allocations", json=body(boss_id, project, start=day(8), end=day(0)))
    assert r.status_code == 409 and r.json() == {"detail": "The end date is before the start date"}
    for status in ("done", "archived"):
        closed = store.save("projects", {"name": "Closed", "status": status, "kind": "internal"})
        r = boss_client.post("/api/allocations", json=body(boss_id, closed))
        assert r.status_code == 409 and r.json() == {"detail": "The project is closed"}
    paused = store.save("projects", {"name": "Paused", "status": "paused", "kind": "internal"})
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
    deleted = boss_client.delete(f"/api/allocations/{row['id']}")
    assert deleted.status_code == 204


@pytest.mark.parametrize(
    "patch",
    [
        {"hours_per_week": 0.24},
        {"hours_per_week": 80.01},
        {"hours_per_week": -1},
        {"hours_per_week": "many"},
        {"hours_per_week": None},  # neither hour field
        {"total_hours": 10},  # both hour fields
        {"hours_per_week": None, "total_hours": 0},
        {"hours_per_week": None, "total_hours": 100001},
        {"rate": 80},  # an employee row has no rate
        {"supplier_id": "abcdef12"},  # both owners
        {"user_id": None},  # no owner
        {"start": "02.03.2026"},
        {"end": "2026-02-30"},
        {"user_id": "x"},
        {"user_id": ""},
        {"project_id": "UPPER-case"},
        {"end": day(1831)},  # longer than 1830 days
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
    ("GET", "/api/plan", None),
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
    assert ro.get("/api/plan").status_code == 200
    assert ro.post("/api/plan/simulate", json={}).status_code == 403  # read-write tokens only (fail closed)
    row = boss_client.post("/api/allocations", json=body(boss_id, project)).json()
    for method, path, payload in [
        ("POST", "/api/allocations", body(boss_id, project)),
        ("PUT", f"/api/allocations/{row['id']}", body(boss_id, project)),
        ("DELETE", f"/api/allocations/{row['id']}", None),
    ]:
        assert ro.request(method, path, json=payload).status_code == 403, (method, path)
    assert len(boss_client.get("/api/allocations").json()) == 1


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


# ------------------------------------------------------------------ planner routes
def test_simulate_with_allocation_changes_and_delta(boss_client, boss_id, project, store):
    add_contract(store, boss_id, project, 30)
    a = boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=20.0)).json()
    base = {"weeks": 4}
    sim = boss_client.post(
        "/api/plan/simulate", json={**base, "changes": {"allocations": [{"id": a["id"], "hours_per_week": 30.0}]}}
    )
    assert sim.status_code == 200, sim.text
    out = sim.json()
    # today is the anchor Monday and not supplied: 20 h/week gives 16 + 3 x 20 = 76 of the 120 h (short 44),
    # 30 h/week gives 24 + 3 x 30 = 114 h (short 6)
    assert out["delta"]["total"] == {"shortfall_before": 44.0, "shortfall_after": 6.0, "delta": -38.0}
    assert out["delta"]["projects"] == [
        {"project_id": project["id"], "shortfall_before": 44.0, "shortfall_after": 6.0, "delta": -38.0}
    ]
    new = {"user_id": boss_id, "project_id": project["id"], "start": START, "end": END, "hours_per_week": 5.0}
    assert boss_client.post("/api/plan/simulate", json={**base, "changes": {"allocations": [new]}}).status_code == 200
    assert len(store.list("allocations")) == 1  # simulate writes nothing

    # errors
    def sim_err(change):
        return boss_client.post("/api/plan/simulate", json={**base, "changes": {"allocations": [change]}})

    assert sim_err({"id": "nothing0001", "hours_per_week": 5}).json() == {"detail": "Allocation not found"}
    store.save("suppliers", {"id": "supp0001", "name": "Demo Supplier"})
    ext = store.save(
        "allocations",
        {
            "supplier_id": "supp0001",
            "project_id": project["id"],
            "start": START,
            "end": END,
            "hours_per_week": 5,
            "rate": 80,
        },
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
        "start": day(35),
        "end": day(39),
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


def test_suggest_start_per_project(boss_client, boss_id, project):
    payload = {"hours": 40, "deadline": day(25), "earliest": day(0), "user_ids": [boss_id]}
    free = boss_client.post("/api/plan/suggest-start", json=payload).json()
    assert free["feasible"] is True and free["start"] == day(1)  # the day after today (the anchor Monday)
    proj = boss_client.post("/api/plan/suggest-start", json={**payload, "project_id": project["id"]}).json()
    assert proj["feasible"] is False  # the project has no allocation: unassigned hours are not its hours
    boss_client.post("/api/allocations", json=body(boss_id, project, hours_per_week=20.0))
    proj = boss_client.post("/api/plan/suggest-start", json={**payload, "project_id": project["id"]}).json()
    assert proj["feasible"] is True and proj["start"] == day(1)
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
            "cancelled_from": START,
        },
    )
    assert boss_client.put(f"/api/allocations/{row['id']}", json=body(boss_id, project)).status_code == 409
    deleted = boss_client.delete(f"/api/allocations/{row['id']}")
    assert deleted.status_code == 409
    listed = boss_client.get("/api/allocations").json()  # no status filter: every status
    assert [x["status"] for x in listed] == ["cancelled"] and listed[0]["user_name"] == "Test Boss"
    r = boss_client.delete(f"/api/projects/{project['id']}")
    assert r.status_code == 409 and store.get("projects", project["id"]) is not None
    emp = boss_client.post(
        "/api/users", json={"username": "eva", "name": "Eva", "role": "employee", "password": "correct horse battery"}
    ).json()
    boss_client.post("/api/allocations", json=body(emp["id"], project))
    deleted = boss_client.delete(f"/api/users/{emp['id']}")
    assert deleted.status_code == 409
    assert store.get("users", emp["id"]) is not None


def test_deactivating_a_user_keeps_allocations_but_blocks_editing(boss_client, boss_id, project, store):
    emp = store.save("users", {"username": "eva", "name": "Eva", "role": "employee", "active": True})
    row = boss_client.post("/api/allocations", json=body(emp["id"], project)).json()
    store.save("users", {**emp, "active": False})
    assert boss_client.get("/api/allocations").json()[0]["id"] == row["id"]
    r = boss_client.put(f"/api/allocations/{row['id']}", json=body(emp["id"], project, hours_per_week=9.0))
    assert r.status_code == 409 and r.json() == {"detail": "The user is not active"}


def test_simulate_scopes_its_base_by_project(boss_client, boss_id, project, store):
    customer = boss_client.post("/api/customers", json={"name": "Demo Customer"}).json()
    other = boss_client.post("/api/projects", json={"name": "Demo project Q", "customer_id": customer["id"]}).json()
    add_contract(store, boss_id, project, 30)
    add_contract(store, boss_id, other, 10, title="Q")
    out = boss_client.post("/api/plan/simulate", json={"weeks": 4, "project_id": other["id"]}).json()
    assert [p["project_id"] for p in out["projects"]] == [other["id"]]
    assert out["projects"][0]["shortfall_contracts"] == 40.0  # 4 weeks x 10 h, nobody assigned
    assert out["delta"]["total"] == {"shortfall_before": 40.0, "shortfall_after": 40.0, "delta": 0.0}
    assert out["delta"]["projects"] == [
        {"project_id": other["id"], "shortfall_before": 40.0, "shortfall_after": 40.0, "delta": 0.0}
    ]


# ------------------------------------------------------------------ PUT keeps what it does not replace
def test_put_changes_person_and_project_and_keeps_unknown_fields(boss_client, boss_id, project, store):
    customer = boss_client.post("/api/customers", json={"name": "Demo Customer"}).json()
    other = boss_client.post("/api/projects", json={"name": "Demo project Q", "customer_id": customer["id"]}).json()
    emp = store.save(
        "users", {"username": "eva", "name": "Eva", "role": "employee", "active": True, "workdays": [0, 1, 2, 3]}
    )
    row = boss_client.post("/api/allocations", json=body(boss_id, project)).json()
    assert "customer_id" not in row
    stored = store.get("allocations", row["id"])
    store.save("allocations", {**stored, "x_future": {"a": [1]}})
    r = boss_client.put(f"/api/allocations/{row['id']}", json=body(emp["id"], other, hours_per_week=12.0))
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["user_id"], got["project_id"], got["hours_per_week"]) == (emp["id"], other["id"], 12.0)
    assert "customer_id" not in got  # no longer written
    assert got["x_future"] == {"a": [1]} and got["user_name"] == "Eva" and got["project_name"] == "Demo project Q"
    r = boss_client.put(
        f"/api/allocations/{row['id']}", json=body(boss_id, project, hours_per_week=None, total_hours=9)
    )
    assert r.json()["total_hours"] == 9 and "hours_per_week" not in store.get("allocations", row["id"])
    assert (
        boss_client.put(
            f"/api/allocations/{row['id']}", json={**body(boss_id, project), "status": "cancelled"}
        ).status_code
        == 422
    )
    assert store.get("allocations", row["id"])["status"] == "active"


# ------------------------------------------------------------------ damaged rows never give a 500
@pytest.mark.parametrize(
    "row, code",
    [
        ({"project_id": "{p}"}, "state.required"),
        (
            {"project_id": "{p}", "start": "garbage", "end": "2026-03-01"},
            ("format.date", "state.period_reversed"),  # "garbage" sorts after the end date
        ),
        ({"start": "2026-03-01", "end": "2026-03-05"}, "state.required"),
        ({"project_id": "{p}", "start": 5, "end": [1]}, "state.required"),  # not dates: kept in extra
    ],
)
def test_damaged_allocation_rows_do_not_break_the_views(boss_client, boss_id, project, store, row, code, unguarded):
    row = {k: (project["id"] if v == "{p}" else v) for k, v in row.items()}
    planted = {"user_id": boss_id, "status": "active", "hours_per_week": 10, **row}
    if code:
        unguarded(store, "allocations", planted, code)
    else:
        store.save("allocations", planted)  # damaged, but no guard refuses it
    add_contract(store, boss_id, project, 10)
    assert boss_client.get("/api/allocations").status_code == 200
    assert boss_client.get("/api/plan", params={"weeks": 4}).status_code == 200
    assert boss_client.post("/api/plan/simulate", json={"weeks": 2}).status_code == 200
    assert boss_client.post("/api/allocations/bootstrap", json={}).status_code in (404, 405)  # removed


# ------------------------------------------------------------------ empty filters, simulate rules over HTTP
def test_empty_project_and_user_filters_mean_no_filter(boss_client, boss_id, project):
    boss_client.post("/api/allocations", json=body(boss_id, project))
    a = boss_client.get("/api/plan").json()
    b = boss_client.get("/api/plan", params={"project_id": "", "user_id": ""}).json()
    assert a == b


def test_simulate_refuses_what_apply_would_refuse(boss_client, boss_id, project, store):
    closed = store.save("projects", {"name": "Closed", "status": "archived", "kind": "internal"})
    gone = store.save("users", {"username": "gone", "name": "Gone", "role": "employee", "active": False})
    new = {"user_id": boss_id, "project_id": project["id"], "start": START, "end": END, "hours_per_week": 5}

    def sim(**kw):
        return boss_client.post("/api/plan/simulate", json={"changes": {"allocations": [{**new, **kw}]}})

    assert sim().status_code == 200
    assert sim(project_id=closed["id"]).json() == {"detail": "The project is closed"}
    assert sim(user_id=gone["id"]).json() == {"detail": "The user is not active"}
    assert sim(end=day(2000)).status_code == 422
    # a change of an existing row that would exceed 1830 days through the merge
    row = boss_client.post("/api/allocations", json=body(boss_id, project)).json()
    r = boss_client.post("/api/plan/simulate", json={"changes": {"allocations": [{"id": row["id"], "end": day(2000)}]}})
    assert r.status_code == 422 and "1830" in r.json()["detail"]
    assert (
        boss_client.post(
            "/api/plan/apply", json={"confirm": True, "changes": {"allocations": [{"id": row["id"], "end": day(2000)}]}}
        ).status_code
        == 422
    )


# ------------------------------------------------------------------ subcontractor rows (P5, E6)
E6_START, E6_END = "2026-11-09", "2026-12-04"


@pytest.fixture
def sam(store):
    """A person-capacity subcontractor (invented), Monday to Thursday."""
    return store.save(
        "suppliers",
        {"name": "Sam Example (Demo)", "kind": "person-capacity", "workdays": [0, 1, 2, 3], "rate": 70, "active": True},
    )


def sup_body(sam, project, **kw):
    return {
        "supplier_id": sam["id"],
        "project_id": project["id"],
        "start": E6_START,
        "end": E6_END,
        "total_hours": 24.0,
        "rate": 90.0,
        **kw,
    }


@pytest.fixture
def offer_row(store, sam, project):
    """E6: an incoming offer of 24 h x 90.00 = 2,160.00 net and the assignment accepting it books."""
    offer = store.save(
        "incoming_offers",
        {
            "supplier_id": sam["id"],
            "project_id": project["id"],
            "status": "accepted",
            "hours": 24.0,
            "net_total": 2160.0,
        },
    )
    row = store.save(
        "allocations",
        {
            "supplier_id": sam["id"],
            "project_id": project["id"],
            "incoming_offer_id": offer["id"],
            "start": E6_START,
            "end": E6_END,
            "total_hours": 24.0,
            "rate": 90.0,
            "status": "active",
        },
    )
    return offer, row


def test_subcontractor_row_roundtrip_with_planned_figures(boss_client, sam, project, store):
    r = boss_client.post("/api/allocations", json=sup_body(sam, project))
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["kind"] == "external" and row["supplier_name"] == "Sam Example (Demo)"
    assert row["planned_hours"] == 24.0 and row["planned_net"] == 2160.0 and row["planning_warnings"] == []
    assert "user_id" not in store.get("allocations", row["id"])
    r = boss_client.put(
        f"/api/allocations/{row['id']}", json=sup_body(sam, project, total_hours=None, hours_per_week=8)
    )
    assert r.status_code == 200, r.text
    # 4 weeks (09.11.-04.12.) x 8 h at 90.00
    assert (r.json()["planned_hours"], r.json()["planned_net"]) == (32.0, 2880.0)
    assert "total_hours" not in store.get("allocations", row["id"])
    r = boss_client.delete(f"/api/allocations/{row['id']}")
    assert r.status_code == 204


@pytest.mark.parametrize(
    "patch",
    [
        {"rate": None},
        {"rate": -1},
        {"rate": 10001},
        {"user_id": "abcdef12"},
        {"total_hours": None},
        {"hours_per_week": 5},
    ],
)
def test_subcontractor_body_validation(boss_client, sam, project, patch):
    r = boss_client.post("/api/allocations", json={**sup_body(sam, project), **patch})
    assert r.status_code == 422, r.text


def test_subcontractor_owner_errors_and_no_change_of_kind(boss_client, boss_id, sam, project, store):
    assert boss_client.post("/api/allocations", json=sup_body({"id": "nobody000001"}, project)).status_code == 404
    store.save("suppliers", {**sam, "active": False})
    r = boss_client.post("/api/allocations", json=sup_body(sam, project))
    assert r.status_code == 409 and r.json() == {"detail": "The supplier is not active"}
    store.save("suppliers", {**sam, "active": True})
    row = boss_client.post("/api/allocations", json=sup_body(sam, project)).json()
    r = boss_client.put(f"/api/allocations/{row['id']}", json=body(boss_id, project))
    assert r.status_code == 409


def test_e6_edit_to_30_hours_warns_with_the_literal_text(boss_client, sam, project, offer_row):
    offer, row = offer_row
    r = boss_client.put(f"/api/allocations/{row['id']}", json=sup_body(sam, project))  # 24 h x 90.00 as booked
    assert r.status_code == 200, r.text
    assert (r.json()["planned_hours"], r.json()["planned_net"]) == (24.0, 2160.0)
    assert r.json()["planning_warnings"] == []
    r = boss_client.put(f"/api/allocations/{row['id']}", json=sup_body(sam, project, total_hours=30.0))
    assert r.status_code == 200, r.text
    assert (r.json()["planned_hours"], r.json()["planned_net"]) == (30.0, 2700.0)
    assert r.json()["incoming_offer_id"] == offer["id"]
    (w,) = r.json()["planning_warnings"]
    assert w["code"] == "assignment.differs_from_offer" and w["severity"] == "info"
    assert w["message"] == "Planned 30 h / 2,700.00 € differ from the offer's 24 h / 2,160.00 € (6 h / 540.00 € more)."
    r = boss_client.put(f"/api/allocations/{row['id']}", json=sup_body(sam, project, total_hours=20.0))
    assert r.json()["planning_warnings"][0]["message"] == (
        "Planned 20 h / 1,800.00 € differ from the offer's 24 h / 2,160.00 € (4 h / 360.00 € less)."
    )


def test_offer_linked_rows_can_be_cancelled_but_not_deleted(boss_client, offer_row, store):
    _, row = offer_row
    r = boss_client.delete(f"/api/allocations/{row['id']}")
    assert r.status_code == 409 and "cancel it instead" in r.json()["detail"]
    assert store.get("allocations", row["id"]) is not None
    r = boss_client.post(f"/api/allocations/{row['id']}/cancel", json={"effective_from": "2026-11-23"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    # 24 h over 16 days (Mon-Thu, 4 weeks) = 1.5 h a day; 8 lie before 23.11. -> 12 h, 12 x 90.00 = 1,080.00
    assert (r.json()["planned_hours"], r.json()["planned_net"]) == (12.0, 1080.0)
    r = boss_client.delete(f"/api/allocations/{row['id']}")
    assert r.status_code == 409  # cancelled rows stay


def test_overlap_warning_for_a_subcontractor(boss_client, sam, project):
    boss_client.post("/api/allocations", json=sup_body(sam, project))
    r = boss_client.post("/api/allocations", json=sup_body(sam, project, total_hours=8.0, end="2026-11-13"))
    (w,) = r.json()["planning_warnings"]
    assert w["code"] == "assignment.overlap" and w["details"]["week"] == E6_START and w["severity"] == "info"


@pytest.fixture
def accountant_client(app, boss_client):
    pw = "correct horse battery"
    r = boss_client.post(
        "/api/users", json={"username": "acc", "name": "Alex Accountant", "role": "accountant", "password": pw}
    )
    assert r.status_code == 200, r.text
    c = TestClient(app)
    token = c.post("/api/auth/login", json={"username": "acc", "password": pw}).json()["token"]
    c.headers["Authorization"] = "Bearer " + token
    return c


def test_rights_follow_the_kind_of_the_row(
    monkeypatch, accountant_client, boss_client, boss_id, sam, project, offer_row
):
    from permita38.core import rights

    emp_row = boss_client.post("/api/allocations", json=body(boss_id, project)).json()
    sup_row = boss_client.post("/api/allocations", json=sup_body(sam, project)).json()
    # the accountant has neither right: both kinds are refused
    for payload in (body(boss_id, project), sup_body(sam, project)):
        assert accountant_client.post("/api/allocations", json=payload).status_code == 403
    # planning write only: employee rows yes, subcontractor rows no
    monkeypatch.setitem(rights.MATRIX["accountant"], "planning", (rights.Scope.ALL, rights.Scope.ALL))
    assert accountant_client.put(f"/api/allocations/{emp_row['id']}", json=body(boss_id, project)).status_code == 200
    assert accountant_client.post("/api/allocations", json=body(boss_id, project, end=day(6))).status_code == 201
    assert accountant_client.post("/api/allocations", json=sup_body(sam, project)).status_code == 403
    assert accountant_client.put(f"/api/allocations/{sup_row['id']}", json=sup_body(sam, project)).status_code == 403
    r = accountant_client.delete(f"/api/allocations/{sup_row['id']}")
    assert r.status_code == 403
    r = accountant_client.delete(f"/api/allocations/{emp_row['id']}")
    assert r.status_code == 204
    # procurement write as well: subcontractor rows yes
    monkeypatch.setitem(rights.MATRIX["accountant"], "procurement", (rights.Scope.ALL, rights.Scope.ALL))
    assert accountant_client.put(f"/api/allocations/{sup_row['id']}", json=sup_body(sam, project)).status_code == 200
    assert accountant_client.post("/api/allocations", json=sup_body(sam, project, end="2026-11-13")).status_code == 201
    r = accountant_client.delete(f"/api/allocations/{sup_row['id']}")
    assert r.status_code == 204
    _, linked = offer_row
    r = accountant_client.delete(f"/api/allocations/{linked['id']}")
    assert r.status_code == 409


def test_apply_change_keeps_total_hours_rows_working(boss_client, boss_id, project, store):
    row = boss_client.post(
        "/api/allocations", json=body(boss_id, project, hours_per_week=None, total_hours=20.0)
    ).json()
    changes = {"allocations": [{"id": row["id"], "end": day(11)}]}
    r = boss_client.post("/api/plan/apply", json={"changes": changes, "confirm": True})
    assert r.status_code == 200, r.text
    stored = store.get("allocations", row["id"])
    assert stored["total_hours"] == 20.0 and stored["end"] == day(11) and "hours_per_week" not in stored
