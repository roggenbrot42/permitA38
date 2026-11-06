"""Service-level reference checks for projects (docs/specs/db-guards-v2.md, part B): the services refuse first,
a database trigger is only the backstop."""

import pytest

TODAY = "2026-03-02"
GHOST = "0" * 32  # well-formed id of a project that does not exist
LINES = [{"description": "Dev", "qty": 1, "unit": "h", "price": 90}]


def _uid(c):
    return c.get("/api/auth/me").json()["id"]


def _project(c, name="Website", **kw):
    r = c.post("/api/projects", json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _entry(c, pid, **kw):
    r = c.post("/api/entries", json={"user_id": _uid(c), "date": TODAY, "minutes": 60, "project_id": pid, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _contract(c, pid):
    r = c.post(
        "/api/contracts",
        json={"title": "Retainer", "start": "2026-01-01", "end": "2026-12-31", "hours_per_week": 5, "project_id": pid},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _offer(c, pid):
    r = c.post("/api/offers", json={"project_id": pid, "lines": LINES})
    assert r.status_code == 200, r.text
    return r.json()


def _invoice(c, pid):
    r = c.post("/api/invoices", json={"project_id": pid, "lines": LINES})
    assert r.status_code == 200, r.text
    return r.json()


def _refused(c, pid, message):
    r = c.delete(f"/api/projects/{pid}")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == message
    assert any(p["id"] == pid for p in c.get("/api/projects").json())


# ------------------------------------------------------------------ deleting a used project
def test_delete_refused_names_allocations(boss_client, app):
    c = boss_client
    p = _project(c)
    app.state.services.store.save("allocations", {"project_id": p["id"], "user_id": _uid(c)})
    _refused(c, p["id"], "This project is still used by 1 allocation; archive it instead.")


def test_delete_refused_time_entries_plural(boss_client):
    c = boss_client
    p = _project(c)
    for _ in range(12):
        _entry(c, p["id"])
    _refused(c, p["id"], "This project is still used by 12 time entries; archive it instead.")
    assert len(c.get("/api/entries").json()) == 12


def test_delete_refused_time_entries_and_invoice(boss_client):
    c = boss_client
    p = _project(c)
    _entry(c, p["id"])
    _entry(c, p["id"])
    _invoice(c, p["id"])
    _refused(c, p["id"], "This project is still used by 2 time entries and 1 invoice; archive it instead.")


def test_delete_refused_singular_contract_offer(boss_client):
    c = boss_client
    p = _project(c)
    _contract(c, p["id"])
    _offer(c, p["id"])
    _refused(c, p["id"], "This project is still used by 1 contract and 1 offer; archive it instead.")


def test_delete_refused_all_kinds_in_order(boss_client, app):
    c = boss_client
    store = app.state.services.store
    p = _project(c)
    store.save("allocations", {"project_id": p["id"], "user_id": _uid(c)})
    store.save("tasks", {"project_id": p["id"], "name": "Wire up"})
    _entry(c, p["id"])
    _contract(c, p["id"])
    _offer(c, p["id"])
    _offer(c, p["id"])
    _invoice(c, p["id"])
    store.save("expenses", {"project_id": p["id"], "date": TODAY})
    store.save("incoming_offers", {"project_id": p["id"]})
    store.save("trips", {"project_id": p["id"]})
    store.save("plan_scenarios", {"name": "What if", "user_id": _uid(c), "changes": _scenario_changes(p["id"])})
    assert c.post("/api/timer/start", json={"project_id": p["id"]}).status_code == 200
    _refused(
        c,
        p["id"],
        "This project is still used by 1 allocation, 1 task, 1 time entry, 1 contract, 2 offers, 1 invoice,"
        " 1 expense, 1 incoming offer, 1 trip, 1 planning scenario and 1 running timer; archive it instead.",
    )


def _scenario_changes(pid):
    return {"allocations": [{"user_id": "u0000001", "project_id": pid, "start": "2026-03-02", "end": "2026-03-20"}]}


def test_delete_refused_task(boss_client, app):
    c = boss_client
    p = _project(c)
    app.state.services.store.save("tasks", {"project_id": p["id"], "name": "Wire up"})
    _refused(c, p["id"], "This project is still used by 1 task; archive it instead.")


def test_delete_refused_expenses_plural(boss_client):
    c = boss_client
    p = _project(c)
    for _ in range(2):
        r = c.post("/api/expenses", json={"date": TODAY, "net": 10, "project_id": p["id"]})
        assert r.status_code == 200, r.text
    _refused(c, p["id"], "This project is still used by 2 expenses; archive it instead.")


def test_delete_refused_incoming_offer_and_trip(boss_client, app):
    c = boss_client
    store = app.state.services.store
    p = _project(c)
    store.save("incoming_offers", {"project_id": p["id"]})
    _refused(c, p["id"], "This project is still used by 1 incoming offer; archive it instead.")
    store.save("trips", {"project_id": p["id"]})
    _refused(c, p["id"], "This project is still used by 1 incoming offer and 1 trip; archive it instead.")


def test_delete_refused_planning_scenario(boss_client, app):
    c = boss_client
    p = _project(c)
    other = _project(c, "Other")
    store = app.state.services.store
    store.save("plan_scenarios", {"name": "On other", "user_id": _uid(c), "changes": _scenario_changes(other["id"])})
    assert c.delete(f"/api/projects/{p['id']}").status_code == 204  # a scenario on ANOTHER project does not block
    p = _project(c)
    store.save("plan_scenarios", {"name": "What if", "user_id": _uid(c), "changes": _scenario_changes(p["id"])})
    _refused(c, p["id"], "This project is still used by 1 planning scenario; archive it instead.")


def test_delete_clears_a_legacy_stopped_timer_but_a_running_one_blocks(boss_client, app):
    c = boss_client
    store = app.state.services.store
    p = _project(c)
    store.save("timers", {"id": _uid(c), "running": False, "project_id": p["id"], "task_id": "task0001"})
    assert c.delete(f"/api/projects/{p['id']}").status_code == 204
    timer = store.get("timers", _uid(c))
    assert timer["running"] is False and "project_id" not in timer and "task_id" not in timer
    p2 = _project(c, "Second")
    store.save("timers", {"id": _uid(c), "running": True, "started": 1, "project_id": p2["id"]})
    _refused(c, p2["id"], "This project is still used by 1 running timer; archive it instead.")
    assert store.get("timers", _uid(c))["project_id"] == p2["id"]


def test_task_and_expense_with_unknown_project_are_refused(boss_client):
    c = boss_client
    p = _project(c)
    body = {"date": TODAY, "net": 10}
    r = c.post("/api/expenses", json={**body, "project_id": GHOST})
    assert (r.status_code, r.json()["detail"]) == (422, "Unknown project")
    ok = c.post("/api/expenses", json={**body, "project_id": p["id"]})
    assert ok.status_code == 200, ok.text
    r = c.put(f"/api/expenses/{ok.json()['id']}", json={**body, "project_id": GHOST})
    assert (r.status_code, r.json()["detail"]) == (422, "Unknown project")
    assert c.get(f"/api/expenses/{ok.json()['id']}").json()["project_id"] == p["id"]
    r = c.post("/api/tasks", json={"name": "Wire up", "project_id": GHOST})
    assert (r.status_code, r.json()["detail"]) == (422, "Unknown project")
    t = c.post("/api/tasks", json={"name": "Wire up", "project_id": p["id"]})
    assert t.status_code == 200, t.text
    r = c.put(f"/api/tasks/{t.json()['id']}", json={"name": "Wire up", "project_id": GHOST})
    assert (r.status_code, r.json()["detail"]) == (422, "Unknown project")
    assert c.get("/api/tasks").json()[0]["project_id"] == p["id"]


def test_scan_booking_goes_through_the_project_check(boss_client, app):
    from permita38.core.errors import ServiceError

    svc = app.state.services
    me = svc.store.get("users", _uid(boss_client))
    with pytest.raises(ServiceError) as err:
        svc.records.create_scanned(me, {"date": TODAY, "net": 10, "project_id": GHOST}, "scan0001")
    assert (err.value.status, err.value.message) == (422, "Unknown project")
    assert svc.store.list("expenses") == []


def test_adopt_vendors_cannot_carry_a_new_project_so_a_legacy_ghost_stays_and_the_expense_is_adopted(
    boss_client, app, unguarded
):
    """adopt-vendors only sets the supplier: the project id is the unchanged legacy one, so it is not re-checked."""
    c = boss_client
    store = app.state.services.store
    legacy = unguarded(
        store, "expenses", {"date": TODAY, "net": 10, "vendor": "demo ghost shop", "project_id": GHOST}, "ref.dangling"
    )
    sup = c.post("/api/suppliers", json={"name": "Demo Ghost Shop GmbH", "kind": "company"}).json()
    body = {"dry_run": False, "items": [{"vendors": ["demo ghost shop"], "supplier_id": sup["id"]}]}
    r = c.post("/api/suppliers/adopt-vendors", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["expenses_changed"] == 1
    row = store.get("expenses", legacy["id"])
    assert row["supplier_id"] == sup["id"] and row["project_id"] == GHOST


def _alloc(pid):
    return {"user_id": "u0000001", "project_id": pid, "start": "2026-03-02", "end": "2026-03-20", "hours_per_week": 10}


def test_delete_refused_scenario_with_the_project_in_its_second_allocation(boss_client, app):
    c = boss_client
    p = _project(c)
    other = _project(c, "Other")
    store = app.state.services.store
    changes = {"allocations": [_alloc(other["id"]), _alloc(p["id"])]}
    store.save("plan_scenarios", {"name": "Second", "user_id": _uid(c), "changes": changes})
    _refused(c, p["id"], "This project is still used by 1 planning scenario; archive it instead.")


def test_scenario_naming_the_project_twice_counts_once(boss_client, app):
    c = boss_client
    p = _project(c)
    store = app.state.services.store
    changes = {"allocations": [_alloc(p["id"]), _alloc(p["id"])]}
    store.save("plan_scenarios", {"name": "Twice", "user_id": _uid(c), "changes": changes})
    _refused(c, p["id"], "This project is still used by 1 planning scenario; archive it instead.")


def test_scenario_with_malformed_allocations_does_not_block_or_crash(boss_client, app):
    c = boss_client
    p = _project(c)
    store = app.state.services.store
    store.save(
        "plan_scenarios",
        {"name": "Odd", "user_id": _uid(c), "changes": {"allocations": [None, "x", {"project_id": 5}]}},
    )
    assert c.delete(f"/api/projects/{p['id']}").status_code == 204


def test_refused_delete_leaves_a_legacy_stopped_timer_untouched(boss_client, app):
    c = boss_client
    store = app.state.services.store
    p = _project(c)
    t = c.post("/api/tasks", json={"name": "Wire up", "project_id": p["id"]})
    assert t.status_code == 200, t.text
    store.save("timers", {"id": _uid(c), "running": False, "project_id": p["id"], "task_id": t.json()["id"]})
    _refused(c, p["id"], "This project is still used by 1 task; archive it instead.")
    timer = store.get("timers", _uid(c))
    assert timer["project_id"] == p["id"] and timer["task_id"] == t.json()["id"]


def test_grandfathered_dangling_task_and_expense_can_be_resaved_but_not_moved_to_another_ghost(
    boss_client, app, unguarded
):
    c = boss_client
    store = app.state.services.store
    other_ghost = "1" * 32
    task = unguarded(store, "tasks", {"name": "Old task", "project_id": GHOST}, "ref.dangling")
    exp = unguarded(store, "expenses", {"date": TODAY, "net": 10, "project_id": GHOST}, "ref.dangling")
    r = c.put(f"/api/tasks/{task['id']}", json={"name": "Old task", "project_id": GHOST})
    assert r.status_code == 200, r.text
    r = c.put(f"/api/tasks/{task['id']}", json={"name": "Renamed task", "project_id": GHOST})
    assert (r.status_code, r.json()["name"], r.json()["project_id"]) == (200, "Renamed task", GHOST)
    r = c.put(f"/api/tasks/{task['id']}", json={"name": "Renamed task", "project_id": other_ghost})
    assert (r.status_code, r.json()["detail"]) == (422, "Unknown project")
    body = {"date": TODAY, "net": 10, "project_id": GHOST}
    r = c.put(f"/api/expenses/{exp['id']}", json=body)
    assert r.status_code == 200, r.text
    r = c.put(f"/api/expenses/{exp['id']}", json={**body, "net": 12})
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == GHOST
    r = c.put(f"/api/expenses/{exp['id']}", json={**body, "project_id": other_ghost})
    assert (r.status_code, r.json()["detail"]) == (422, "Unknown project")


def test_saving_a_scenario_checks_its_projects(boss_client):
    c = boss_client
    p = _project(c)
    r = c.post("/api/plan/scenarios", json={"name": "Ghost", "changes": {"allocations": [_alloc(GHOST)]}})
    assert (r.status_code, r.json()["detail"]) == (422, "Unknown project")
    r = c.post("/api/plan/scenarios", json={"name": "Real", "changes": {"allocations": [_alloc(p["id"])]}})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    r = c.put(f"/api/plan/scenarios/{sid}", json={"name": "Real", "changes": {"allocations": [_alloc(GHOST)]}})
    assert (r.status_code, r.json()["detail"]) == (422, "Unknown project")


def test_scenario_update_keeps_a_legacy_dangling_project_but_refuses_a_new_ghost(boss_client, app):
    c = boss_client
    store = app.state.services.store
    sc = store.save("plan_scenarios", {"name": "Old", "user_id": _uid(c), "changes": {"allocations": [_alloc(GHOST)]}})
    r = c.put(f"/api/plan/scenarios/{sc['id']}", json={"name": "Renamed", "changes": {"allocations": [_alloc(GHOST)]}})
    assert (r.status_code, r.json()["name"]) == (200, "Renamed")
    r = c.put(
        f"/api/plan/scenarios/{sc['id']}", json={"name": "Renamed", "changes": {"allocations": [_alloc("1" * 32)]}}
    )
    assert (r.status_code, r.json()["detail"]) == (422, "Unknown project")


def test_delete_refused_running_timer_only(boss_client):
    c = boss_client
    p = _project(c)
    assert c.post("/api/timer/start", json={"project_id": p["id"]}).status_code == 200
    _refused(c, p["id"], "This project is still used by 1 running timer; archive it instead.")
    assert c.get("/api/timer").json()["running"] is True


def test_stopped_timer_does_not_block_delete(boss_client):
    c = boss_client
    p = _project(c)
    c.post("/api/timer/start", json={"project_id": p["id"]})
    stop = c.post("/api/timer/stop", json={})
    assert stop.status_code == 200
    entry_id = stop.json()["entry"]["id"]
    assert c.delete(f"/api/entries/{entry_id}").status_code == 204
    assert c.delete(f"/api/projects/{p['id']}").status_code == 204


def test_unused_project_deletes(boss_client):
    c = boss_client
    p = _project(c)
    other = _project(c, "Other")
    _entry(c, other["id"])  # a record on ANOTHER project does not block
    assert c.delete(f"/api/projects/{p['id']}").status_code == 204
    assert [x["id"] for x in c.get("/api/projects").json()] == [other["id"]]


def test_archived_unused_project_deletes_archived_used_does_not(boss_client):
    c = boss_client
    unused = _project(c, "Old", status="archived")
    used = _project(c, "Used", status="archived")
    _entry(c, used["id"])
    assert c.delete(f"/api/projects/{unused['id']}").status_code == 204
    _refused(c, used["id"], "This project is still used by 1 time entry; archive it instead.")


# ------------------------------------------------------------------ writing an unknown project
@pytest.mark.parametrize(
    "path,body",
    [
        ("entries", {"date": TODAY, "minutes": 30}),
        ("contracts", {"title": "R", "start": "2026-01-01", "end": "2026-12-31", "hours_per_week": 5}),
        ("offers", {"lines": LINES}),
        ("invoices", {"lines": LINES}),
    ],
)
def test_create_with_unknown_project_refused(boss_client, path, body):
    c = boss_client
    if path == "entries":
        body = {**body, "user_id": _uid(c)}
    r = c.post(f"/api/{path}", json={**body, "project_id": GHOST})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "Unknown project"
    assert c.get(f"/api/{path}").json() == []


def test_update_to_unknown_project_refused_known_accepted(boss_client):
    c = boss_client
    p, q = _project(c), _project(c, "Second")
    e = _entry(c, p["id"])
    r = c.put(f"/api/entries/{e['id']}", json={**e, "project_id": GHOST})
    assert r.status_code == 422 and r.json()["detail"] == "Unknown project"
    assert c.get(f"/api/entries/{e['id']}").json()["project_id"] == p["id"]
    r = c.put(f"/api/entries/{e['id']}", json={**e, "project_id": q["id"]})
    assert r.status_code == 200 and r.json()["project_id"] == q["id"]


def test_entry_without_project_still_allowed(boss_client):
    r = boss_client.post("/api/entries", json={"user_id": _uid(boss_client), "date": TODAY, "minutes": 15})
    assert r.status_code == 200


def test_unchanged_legacy_dangling_project_can_be_resaved(boss_client, app, unguarded):
    c = boss_client
    store = app.state.services.store
    legacy = unguarded(
        store, "entries", {"user_id": _uid(c), "date": TODAY, "minutes": 20, "project_id": GHOST}, "ref.dangling"
    )
    r = c.put(f"/api/entries/{legacy['id']}", json={**legacy, "minutes": 25})
    assert r.status_code == 200, r.text
    assert r.json()["minutes"] == 25 and r.json()["project_id"] == GHOST


# ------------------------------------------------------------------ timer
def test_timer_unknown_project_refused(boss_client):
    c = boss_client
    r = c.post("/api/timer/start", json={"project_id": GHOST})
    assert r.status_code == 422
    assert r.json()["detail"] == "Unknown project"
    assert c.get("/api/timer").json()["running"] is False


def test_timer_unknown_project_keeps_running_timer(boss_client):
    c = boss_client
    p = _project(c)
    assert c.post("/api/timer/start", json={"project_id": p["id"]}).status_code == 200
    assert c.post("/api/timer/start", json={"project_id": GHOST}).status_code == 422
    t = c.get("/api/timer").json()
    assert t["running"] is True and t["project_id"] == p["id"]


def test_timer_on_existing_project_starts(boss_client):
    c = boss_client
    p = _project(c)
    r = c.post("/api/timer/start", json={"project_id": p["id"]})
    assert r.status_code == 200 and r.json()["running"] is True and r.json()["project_id"] == p["id"]
    assert c.post("/api/timer/start", json={}).status_code == 200  # no project is fine


# ------------------------------------------------------------------ offer to invoice
MSG = "The offer's project no longer exists; set another project on the offer first."


def test_offer_to_invoice_with_vanished_project_refused(boss_client, app, unguarded):
    c = boss_client
    p = _project(c)
    o = _offer(c, p["id"])
    store = app.state.services.store
    unguarded(store, "offers", {**store.get("offers", o["id"]), "project_id": GHOST}, "ref.dangling")  # legacy data
    r = c.post(f"/api/offers/{o['id']}/to-invoice")
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == MSG
    assert c.get("/api/invoices").json() == []


def test_offer_to_invoice_with_existing_or_no_project_works(boss_client):
    c = boss_client
    p = _project(c)
    r = c.post(f"/api/offers/{_offer(c, p['id'])['id']}/to-invoice")
    assert r.status_code == 200 and r.json()["project_id"] == p["id"]
    off = c.post("/api/offers", json={"lines": LINES}).json()
    assert c.post(f"/api/offers/{off['id']}/to-invoice").status_code == 200


# ------------------------------------------------------------------ legacy timer without its project
def _plant_timer(app, c, project_id, unguarded):
    import time

    store = app.state.services.store
    assert store.get("projects", project_id) is None
    row = {"id": _uid(c), "running": True, "started": int(time.time()) - 600, "project_id": project_id}
    unguarded(store, "timers", row, "ref.dangling")


def test_stop_timer_of_a_deleted_project_books_the_time_without_it(boss_client, app, unguarded):
    c = boss_client
    _plant_timer(app, c, GHOST, unguarded)
    r = c.post("/api/timer/stop", json={})
    assert r.status_code == 200, r.text
    entries = c.get("/api/entries").json()
    assert len(entries) == 1
    assert entries[0]["minutes"] == 10 and entries[0].get("project_id") is None
    assert entries[0]["user_id"] == _uid(c)
    assert c.get("/api/timer").json()["running"] is False


def test_start_timer_books_the_old_timer_of_a_deleted_project_without_it(boss_client, app, unguarded):
    c = boss_client
    p = _project(c)
    _plant_timer(app, c, GHOST, unguarded)
    r = c.post("/api/timer/start", json={"project_id": p["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["running"] is True and r.json()["project_id"] == p["id"]
    entries = c.get("/api/entries").json()
    assert len(entries) == 1
    assert entries[0]["minutes"] == 10 and entries[0].get("project_id") is None


def test_entry_with_a_malformed_project_id_is_refused(boss_client):
    c = boss_client
    r = c.post("/api/entries", json={"user_id": _uid(c), "date": TODAY, "minutes": 15, "project_id": "bad id!"})
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "project_id"]
    assert r.json()["detail"][0]["msg"] == "String should match pattern '^[0-9a-z]{6,32}$'"
    assert c.get("/api/entries").json() == []
