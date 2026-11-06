"""Project time sheet (docs/specs/project-centric-planning.md, "Project time sheet"). Invented data; every expected
number is written out by hand.

Fixture (project "Orchard", internal, no period; 2026-11-09 and 2026-11-16 are Mondays):
  Ada  2026-11-09  90 min  billable, not invoiced      Bob  2026-11-12  60 min  billable, not invoiced
  Ada  2026-11-10  30 min  billable, invoiced          Bob  2026-12-02 120 min  billable, not invoiced
  Ada  2026-11-17  45 min  not billable                (and 500 min of Ada on another project: never counted)
"""

import pytest

from .conftest import PASSWORD, _login


@pytest.fixture
def world(app, boss_client):
    boss = boss_client
    ids = {}
    for key, username, name in (("ada", "adax", "Ada Example"), ("bob", "bobx", "Bob Example")):
        r = boss.post("/api/users", json={"username": username, "name": name, "role": "employee", "password": PASSWORD})
        assert r.status_code == 200, r.text
        ids[key] = r.json()["id"]
    ids["p"] = boss.post("/api/projects", json={"name": "Orchard", "kind": "internal"}).json()["id"]
    ids["other"] = boss.post("/api/projects", json={"name": "Other", "kind": "internal"}).json()["id"]

    def entry(user, day, minutes, project="p", **extra):
        r = boss.post(
            "/api/entries",
            json={"user_id": ids[user], "date": day, "minutes": minutes, "project_id": ids[project], **extra},
        )
        assert r.status_code == 200, r.text

    entry("ada", "2026-11-09", 90)
    entry("ada", "2026-11-10", 30, invoice_id="inv001")
    entry("ada", "2026-11-17", 45, billable=False)
    entry("bob", "2026-11-12", 60)
    entry("bob", "2026-12-02", 120)
    entry("ada", "2026-11-11", 500, project="other")
    return {"ids": ids, "boss": boss, "ada": _login(app, "adax"), "app": app, "entry": entry}


def url(world, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return f"/api/projects/{world['ids']['p']}/timesheet" + (f"?{qs}" if qs else "")


def person(body, name):
    return next(p for p in body["people"] if p["name"] == name)


def test_weeks_with_explicit_range(world):
    r = world["boss"].get(url(world, **{"from": "2026-11-09", "to": "2026-11-22"}))
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["name"], b["from"], b["to"], b["group"]) == ("Orchard", "2026-11-09", "2026-11-22", "week")
    assert b["periods"] == ["2026-11-09", "2026-11-16"]
    assert [p["name"] for p in b["people"]] == ["Ada Example", "Bob Example"]
    # Ada: 90 + 30 + 45 = 165 min = 2.75 h; billable 90 + 30 = 120 min = 2.0 h; unbilled 90 min = 1.5 h
    assert person(b, "Ada Example") == {
        "user_id": world["ids"]["ada"], "name": "Ada Example", "hours": 2.75, "billable_hours": 2.0,
        "unbilled_hours": 1.5, "by_period": [2.0, 0.75],
    }  # fmt: skip
    # Bob: 60 min in week 1; his 120 min on 2026-12-02 lie outside the range
    bob = person(b, "Bob Example")
    assert (bob["hours"], bob["billable_hours"], bob["unbilled_hours"], bob["by_period"]) == (1.0, 1.0, 1.0, [1.0, 0.0])
    assert b["total"] == {
        "hours": 3.75, "billable_hours": 3.0, "unbilled_hours": 2.5, "by_period": [3.0, 0.75],
    }  # fmt: skip


def test_default_range_is_first_to_last_entry_and_other_projects_are_ignored(world):
    b = world["boss"].get(url(world)).json()
    assert (b["from"], b["to"]) == ("2026-11-09", "2026-12-02")
    assert b["periods"] == ["2026-11-09", "2026-11-16", "2026-11-23", "2026-11-30"]
    # Ada 165 min = 2.75 h (her 500 minutes on the other project are not counted); Bob 180 min = 3.0 h
    assert person(b, "Ada Example")["hours"] == 2.75
    assert person(b, "Ada Example")["by_period"] == [2.0, 0.75, 0.0, 0.0]
    assert person(b, "Bob Example")["by_period"] == [1.0, 0.0, 0.0, 2.0]
    assert b["total"]["hours"] == 5.75
    assert b["total"]["by_period"] == [3.0, 0.75, 0.0, 2.0]


def test_group_by_month(world):
    b = world["boss"].get(url(world, group="month")).json()
    assert b["group"] == "month"
    assert b["periods"] == ["2026-11-01", "2026-12-01"]
    assert person(b, "Ada Example")["by_period"] == [2.75, 0.0]
    assert person(b, "Bob Example")["by_period"] == [1.0, 2.0]
    assert b["total"] == {"hours": 5.75, "billable_hours": 5.0, "unbilled_hours": 4.5, "by_period": [3.75, 2.0]}


def test_default_range_is_the_project_period(world):
    boss = world["boss"]
    body = {"name": "Orchard", "kind": "internal", "start": "2026-11-01", "end": "2026-11-30"}
    assert boss.put(f"/api/projects/{world['ids']['p']}", json=body).status_code == 200
    b = boss.get(url(world, group="month")).json()
    assert (b["from"], b["to"]) == ("2026-11-01", "2026-11-30")
    assert b["periods"] == ["2026-11-01"]
    assert b["total"]["hours"] == 3.75  # Bob's December entry is outside the period


def test_no_entries_and_no_period_gives_an_empty_sheet(world):
    p = world["boss"].post("/api/projects", json={"name": "Empty", "kind": "internal"}).json()["id"]
    b = world["boss"].get(f"/api/projects/{p}/timesheet").json()
    assert (b["from"], b["to"], b["periods"], b["people"]) == (None, None, [], [])
    assert b["total"] == {"hours": 0.0, "billable_hours": 0.0, "unbilled_hours": 0.0, "by_period": []}


def test_limited_user_sees_only_their_own_row_and_total(world):
    b = world["ada"].get(url(world)).json()
    assert [p["name"] for p in b["people"]] == ["Ada Example"]
    assert b["total"]["hours"] == 2.75
    # the default range comes from her own entries only (2026-11-09 .. 2026-11-17), not from Bob's December entry
    assert (b["from"], b["to"], b["periods"]) == ("2026-11-09", "2026-11-17", ["2026-11-09", "2026-11-16"])
    assert b["total"]["by_period"] == [2.0, 0.75]
    assert "Bob" not in str(b)


def test_deleted_user_is_shown_as_such(world):
    store = world["app"].state.services.store
    row = {"user_id": "gone-user", "date": "2026-11-11", "minutes": 15, "project_id": world["ids"]["p"]}
    store.save("entries", row)
    b = world["boss"].get(url(world, **{"from": "2026-11-09", "to": "2026-11-15"})).json()
    gone = person(b, "(deleted user)")
    assert (gone["hours"], gone["by_period"]) == (0.25, [0.25])


def test_hours_are_summed_from_minutes_then_rounded(world):
    for _ in range(3):  # 3 x 20 min = 60 min = 1.0 h, although 20 min alone is 0.33 h
        world["entry"]("bob", "2026-11-13", 20)
    b = world["boss"].get(url(world, **{"from": "2026-11-09", "to": "2026-11-15"})).json()
    assert person(b, "Bob Example")["hours"] == 2.0  # 60 + 60 min


def test_errors(world):
    boss = world["boss"]
    assert boss.get("/api/projects/nope/timesheet").status_code == 404
    assert boss.get(url(world, **{"from": "2026-11-10", "to": "2026-11-09"})).status_code == 422
    assert boss.get(url(world, **{"from": "2026-01-01", "to": "2027-02-06"})).status_code == 422  # 401 days
    assert boss.get(url(world, **{"from": "2026-01-01", "to": "2027-02-05"})).status_code == 200  # 400 days
    assert boss.get(url(world, group="day")).status_code == 422
    assert boss.get(url(world, **{"from": "not-a-date"})).status_code == 422


def _fresh_project(world, *entries, **project):
    boss = world["boss"]
    p = boss.post("/api/projects", json={"name": "Fresh", "kind": "internal", **project}).json()["id"]
    for day in entries:
        r = boss.post(
            "/api/entries", json={"user_id": world["ids"]["ada"], "date": day, "minutes": 60, "project_id": p}
        )
        assert r.status_code == 200, r.text
    return p


def test_default_range_is_clamped_to_400_days(world):
    # 2025-01-01 .. 2026-11-06 is far more than 400 days: from = to - 400 days = 2025-10-02
    p = _fresh_project(world, "2025-01-01", "2026-11-06")
    r = world["boss"].get(f"/api/projects/{p}/timesheet")
    assert r.status_code == 200, r.text
    assert (r.json()["from"], r.json()["to"]) == ("2025-10-02", "2026-11-06")
    # explicit from, default to (last entry) more than 400 days later: to = from + 400 days = 2026-02-05
    r = world["boss"].get(f"/api/projects/{p}/timesheet?from=2025-01-01")
    assert r.status_code == 200, r.text
    assert (r.json()["from"], r.json()["to"]) == ("2025-01-01", "2026-02-05")


def test_year_boundary(world):
    p = _fresh_project(world, "2026-12-31", "2027-01-01")  # Thursday and Friday of one week
    week = world["boss"].get(f"/api/projects/{p}/timesheet?group=week").json()
    assert week["periods"] == ["2026-12-28"]
    assert week["total"]["by_period"] == [2.0]
    month = world["boss"].get(f"/api/projects/{p}/timesheet?group=month").json()
    assert month["periods"] == ["2026-12-01", "2027-01-01"]
    assert month["total"]["by_period"] == [1.0, 1.0]


def test_dates_near_the_end_of_the_calendar_do_not_crash(world):
    boss = world["boss"]
    for group, last in (("week", "9999-12-27"), ("month", "9999-12-01")):
        r = boss.get(url(world, **{"from": "9999-12-01", "to": "9999-12-31", "group": group}))
        assert r.status_code == 200, r.text
        assert r.json()["periods"][-1] == last
    assert boss.get(url(world, **{"from": "9999-06-01"})).status_code == 200  # default to lies before from
    for project in ({"start": "9999-06-01", "end": "9999-12-31"}, {"end": "9999-12-31"}):
        for group in ("week", "month"):
            assert _stub_sheet({"id": "p", "name": "X", **project}, ["9999-12-31"], group)["periods"]


class _StubStore:
    """Rows that the strict storage layer would refuse (outside the validated date range), read-only."""

    def __init__(self, project, days):
        self.project, self.days = project, days

    def get(self, coll, rid):
        return self.project

    def find(self, coll, **kw):
        return [{"user_id": "u1", "date": d, "minutes": 60, "project_id": "p"} for d in self.days]

    def list(self, coll):
        return [{"id": "u1", "name": "", "username": ""}]


def _stub_sheet(project, days, group="week"):
    from permita38.core.service.timesheet import TimesheetService

    me = {"id": "u1", "role": "boss"}
    return TimesheetService(_StubStore(project, days)).timesheet(me, "p", None, None, group)


def test_invalid_stored_project_dates_fall_back_to_the_entries():
    b = _stub_sheet({"id": "p", "name": "X", "start": "2026-13-45", "end": "garbage"}, ["2026-11-09", "2026-11-18"])
    assert (b["from"], b["to"]) == ("2026-11-09", "2026-11-18")


def test_user_without_name_shows_the_id_missing_user_is_deleted(world):
    store = world["app"].state.services.store
    user = store.get("users", world["ids"]["bob"])
    user["name"], user["username"] = "", ""
    store.save("users", user)
    b = world["boss"].get(url(world)).json()
    assert person(b, world["ids"]["bob"])["user_id"] == world["ids"]["bob"]
    store.save("entries", {"user_id": None, "date": "2026-11-11", "minutes": 15, "project_id": world["ids"]["p"]})
    b = world["boss"].get(url(world)).json()
    assert person(b, "(deleted user)")["user_id"] is None


def test_api_tokens_follow_the_owners_rights(world):
    from .test_api_tokens import bearer, new_token

    app = world["app"]
    own = bearer(app, new_token(world["ada"])["token"]).get(url(world))
    assert own.status_code == 200, own.text
    assert [p["name"] for p in own.json()["people"]] == ["Ada Example"]
    assert own.json()["total"]["hours"] == 2.75
    full = bearer(app, new_token(world["boss"])["token"]).get(url(world))  # read-only by default
    assert full.status_code == 200, full.text
    assert [p["name"] for p in full.json()["people"]] == ["Ada Example", "Bob Example"]
    assert full.json()["total"]["hours"] == 5.75
