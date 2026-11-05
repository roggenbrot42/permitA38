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
    p = _project(c)
    app.state.services.store.save("allocations", {"project_id": p["id"], "user_id": _uid(c)})
    _entry(c, p["id"])
    _contract(c, p["id"])
    _offer(c, p["id"])
    _offer(c, p["id"])
    _invoice(c, p["id"])
    assert c.post("/api/timer/start", json={"project_id": p["id"]}).status_code == 200
    _refused(
        c,
        p["id"],
        "This project is still used by 1 allocation, 1 time entry, 1 contract, 2 offers, 1 invoice"
        " and 1 running timer; archive it instead.",
    )


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
def _plant_timer(app, c, project_id):
    import time

    store = app.state.services.store
    assert store.get("projects", project_id) is None
    store.save("timers", {"id": _uid(c), "running": True, "started": int(time.time()) - 600, "project_id": project_id})


def test_stop_timer_of_a_deleted_project_books_the_time_without_it(boss_client, app):
    c = boss_client
    _plant_timer(app, c, GHOST)
    r = c.post("/api/timer/stop", json={})
    assert r.status_code == 200, r.text
    entries = c.get("/api/entries").json()
    assert len(entries) == 1
    assert entries[0]["minutes"] == 10 and entries[0].get("project_id") is None
    assert entries[0]["user_id"] == _uid(c)
    assert c.get("/api/timer").json()["running"] is False


def test_start_timer_books_the_old_timer_of_a_deleted_project_without_it(boss_client, app):
    c = boss_client
    p = _project(c)
    _plant_timer(app, c, GHOST)
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
