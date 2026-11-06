"""GET /api/dashboard `projects`: planned (S1) vs booked hours per project (docs/specs/project-centric-planning.md,
"Today page: what I need to do"). Invented data, fixed date."""

from datetime import date

import pytest

from permita38.core.service import time as time_module


def frozen(day: date):
    class _Frozen(date):
        @classmethod
        def today(cls):
            return day

    return _Frozen


@pytest.fixture
def store(app, boss_client):
    return app.state.services.store


@pytest.fixture
def boss_id(store):
    return next(u["id"] for u in store.list("users") if u["role"] == "boss")


def at(monkeypatch, day: date):
    monkeypatch.setattr(time_module, "date", frozen(day))


def alloc(store, uid, pid, start, end, **hours):
    return store.save(
        "allocations", {"user_id": uid, "project_id": pid, "start": start, "end": end, "status": "active", **hours}
    )


def book(store, uid, pid, day, minutes):
    store.save("entries", {"user_id": uid, "project_id": pid, "date": day, "minutes": minutes})


def by_name(dash):
    return {p["name"]: p for p in dash["projects"]}


def test_planned_and_booked_per_project(boss_client, store, boss_id, monkeypatch):
    at(monkeypatch, date(2026, 11, 4))  # Wednesday; week Mon 2 - Sun 8 November, no holiday
    a = store.save("projects", {"name": "Alpha", "kind": "customer"})
    b = store.save("projects", {"name": "Beta intern", "kind": "internal"})
    alloc(store, boss_id, a["id"], "2026-10-01", "2026-12-31", hours_per_week=10)
    alloc(store, boss_id, b["id"], "2026-10-01", "2026-12-31", hours_per_week=4)
    book(store, boss_id, a["id"], "2026-11-02", 180)
    book(store, boss_id, a["id"], "2026-11-04", 60)
    dash = boss_client.get("/api/dashboard").json()
    rows = by_name(dash)
    assert rows["Alpha"] == {
        "project_id": a["id"],
        "name": "Alpha",
        "kind": "customer",
        "planned_today": 2.0,
        "planned_week": 10.0,
        "booked_today": 1.0,
        "booked_week": 4.0,
        "left_week": 6.0,
    }
    beta = rows["Beta intern"]
    assert beta["kind"] == "internal"
    assert (beta["planned_today"], beta["planned_week"], beta["booked_today"], beta["booked_week"]) == (0.8, 4.0, 0, 0)
    assert beta["left_week"] == 4.0
    assert [p["name"] for p in dash["projects"]] == ["Alpha", "Beta intern"]  # 6 h left before 4 h left


def test_holiday_moves_the_hours(boss_client, store, boss_id, monkeypatch):
    at(monkeypatch, date(2026, 5, 13))  # Wednesday; Thursday 14 May 2026 is Ascension Day: 4 available days
    a = store.save("projects", {"name": "Alpha", "kind": "customer"})
    alloc(store, boss_id, a["id"], "2026-05-01", "2026-06-30", hours_per_week=10)
    row = by_name(boss_client.get("/api/dashboard").json())["Alpha"]
    assert row["planned_today"] == 2.5 and row["planned_week"] == 10.0 and row["left_week"] == 10.0


def test_booking_without_assignment_and_other_week(boss_client, store, boss_id, monkeypatch):
    at(monkeypatch, date(2026, 11, 4))
    c = store.save("projects", {"name": "Gamma", "kind": "customer"})
    d = store.save("projects", {"name": "Delta", "kind": "customer"})
    book(store, boss_id, c["id"], "2026-11-03", 90)
    book(store, boss_id, d["id"], "2026-10-28", 60)  # last week: not listed
    alloc(store, boss_id, d["id"], "2026-09-01", "2026-09-30", hours_per_week=5)  # ended: not listed
    rows = by_name(boss_client.get("/api/dashboard").json())
    assert set(rows) == {"Gamma"}
    assert rows["Gamma"]["planned_today"] == 0 and rows["Gamma"]["planned_week"] == 0
    assert rows["Gamma"]["booked_week"] == 1.5 and rows["Gamma"]["left_week"] == 0


def test_other_persons_dashboard(boss_client, store, boss_id, monkeypatch):
    at(monkeypatch, date(2026, 11, 4))
    mia = boss_client.post(
        "/api/users", json={"username": "mia", "name": "Mia Member", "role": "member", "password": "pw-demo-12345"}
    ).json()
    a = store.save("projects", {"name": "Alpha", "kind": "customer"})
    alloc(store, mia["id"], a["id"], "2026-10-01", "2026-12-31", hours_per_week=15)
    assert boss_client.get("/api/dashboard").json()["projects"] == []
    row = by_name(boss_client.get("/api/dashboard", params={"user_id": mia["id"]}).json())["Alpha"]
    assert row["planned_today"] == 3.0 and row["planned_week"] == 15.0


def test_no_assignments(boss_client):
    assert boss_client.get("/api/dashboard").json()["projects"] == []
