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


def test_employee_books_only_on_assigned_projects(boss_client, member_client, app):
    proj = _project(boss_client)
    body = {"date": "2026-03-02", "minutes": 60, "project_id": proj["id"]}
    r = member_client.post("/api/entries", json=body)
    assert r.status_code == 403 and r.json()["detail"] == "You are not assigned to this project"
    alloc = _assign(boss_client, member_client, proj)
    e = member_client.post("/api/entries", json=body)
    assert e.status_code == 200, e.text
    # a cancelled assignment still grants access
    store = app.state.services.store
    store.save(
        "allocations", {**store.get("allocations", alloc["id"]), "status": "cancelled", "cancelled_from": "2026-03-15"}
    )
    assert member_client.post("/api/entries", json=body).status_code == 200


def test_entry_stays_editable_after_access_is_lost(boss_client, member_client):
    proj = _project(boss_client)
    other = _project(boss_client)
    alloc = _assign(boss_client, member_client, proj)
    e = member_client.post("/api/entries", json={"date": "2026-03-02", "minutes": 60, "project_id": proj["id"]}).json()
    assert boss_client.delete(f"/api/allocations/{alloc['id']}").status_code == 204
    body = {k: v for k, v in e.items() if k not in ("created", "updated")} | {"minutes": 90}
    r = member_client.put(f"/api/entries/{e['id']}", json=body)
    assert r.status_code == 200 and r.json()["minutes"] == 90
    r = member_client.put(f"/api/entries/{e['id']}", json=body | {"project_id": other["id"]})
    assert r.status_code == 403
