"""A time entry always belongs to a person: a full-access write without ``user_id`` gets the writer (new entry) or
keeps the owner (update). Without one the time counted for the project and as unbilled work but showed up in nobody's
timesheet, Today list or weekly hours (Today page, manual mode)."""

from datetime import date

import pytest


def _project(c):
    cust = c.post("/api/customers", json={"name": "Demo Customer"}).json()
    return c.post("/api/projects", json={"name": "Demo Project", "customer_id": cust["id"], "rate": 100}).json()


def _assign(boss, emp, proj):
    r = boss.post(
        "/api/allocations",
        json={
            "user_id": emp.user_id,
            "project_id": proj["id"],
            "start": "2026-03-02",
            "end": "2026-03-31",
            "hours_per_week": 8,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize("owner", [{}, {"user_id": None}])
def test_boss_entry_without_user_gets_the_boss(boss_client, owner):
    me = boss_client.get("/api/auth/me").json()
    proj = _project(boss_client)
    r = boss_client.post("/api/entries", json={"date": "2026-03-02", "minutes": 90, "project_id": proj["id"]} | owner)
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == me["id"]
    stored = [e for e in boss_client.get("/api/entries").json() if e["id"] == r.json()["id"]]
    assert stored and stored[0]["user_id"] == me["id"]


def test_entry_without_user_counts_in_the_writers_day(boss_client):
    proj = _project(boss_client)
    today = date.today().isoformat()
    r = boss_client.post("/api/entries", json={"date": today, "minutes": 90, "project_id": proj["id"]})
    assert r.status_code == 200, r.text
    assert boss_client.get("/api/dashboard").json()["today_worked"] == 1.5


def test_boss_update_without_user_keeps_the_owner(boss_client, member_client):
    proj = _project(boss_client)
    _assign(boss_client, member_client, proj)
    e = member_client.post("/api/entries", json={"date": "2026-03-02", "minutes": 60, "project_id": proj["id"]}).json()
    assert e["user_id"] == member_client.user_id
    body = {k: v for k, v in e.items() if k not in ("user_id", "created", "updated")} | {"minutes": 75}
    r = boss_client.put(f"/api/entries/{e['id']}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == member_client.user_id and r.json()["minutes"] == 75


def test_boss_can_still_book_for_someone_else(boss_client, member_client):
    proj = _project(boss_client)
    body = {"user_id": member_client.user_id, "date": "2026-03-02", "minutes": 30, "project_id": proj["id"]}
    r = boss_client.post("/api/entries", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == member_client.user_id


def test_employee_without_assignment_may_book_on_any_project(boss_client, member_client):
    proj = _project(boss_client)
    body = {"date": "2026-03-02", "minutes": 60, "project_id": proj["id"]}
    r = member_client.post("/api/entries", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == proj["id"]
    other = _project(boss_client)
    body = {k: v for k, v in r.json().items() if k not in ("created", "updated")} | {"project_id": other["id"]}
    assert member_client.put(f"/api/entries/{r.json()['id']}", json=body).status_code == 200
