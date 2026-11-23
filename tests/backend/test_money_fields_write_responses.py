"""D2c: the responses of POST/PUT carry the same decimal-string money as a following GET (decimal-money-api.md rule 3)."""

from datetime import date, timedelta

START = (date.today() + timedelta(days=7)).isoformat()
END = (date.today() + timedelta(days=35)).isoformat()


def _same(a, b):
    skip = {"updated", "planning_warnings", "sanity_warnings"}
    return {k: x for k, x in a.items() if k not in skip} == {k: x for k, x in b.items() if k not in skip}


def _check(c, path, create_body, update_body, money, compare=True):
    r = c.post(path, json=create_body)
    assert r.status_code in (200, 201), r.text
    made = r.json()
    for k, want in money.items():
        assert made[k] == want, (path, k, made[k])
    got = c.get(f"{path}/{made['id']}").json()
    if compare:
        assert _same(made, got)
    for k, want in money.items():
        assert got[k] == want, (path, k, got[k])
    u = c.put(f"{path}/{made['id']}", json=update_body)
    assert u.status_code == 200, u.text
    upd = u.json()
    for k in money:
        assert isinstance(upd[k], str), (path, k, upd[k])
    after = c.get(f"{path}/{made['id']}").json()
    for k in money:
        assert upd[k] == after[k], (path, k)
    if compare:
        assert _same(upd, after)
    return made


def test_customer_project_contract_write_responses(boss_client):
    c = boss_client
    cust = _check(c, "/api/customers", {"name": "Demo GmbH"}, {"name": "Demo GmbH 2"}, {})
    proj = _check(
        c,
        "/api/projects",
        {"name": "Demo project", "customer_id": cust["id"], "rate": 85.5},
        {"name": "Demo project", "customer_id": cust["id"], "rate": 90},
        {"rate": "85.50"},
    )
    base = {"title": "Demo contract", "project_id": proj["id"], "start": START, "end": END, "hours_per_week": 10}
    _check(c, "/api/contracts", {**base, "rate": 70}, {**base, "rate": 72.25}, {"rate": "70.00"})


def test_invoice_line_write_responses(boss_client):
    c = boss_client
    cust = c.post("/api/customers", json={"name": "Demo GmbH"}).json()
    body = {"customer_id": cust["id"], "lines": [{"description": "Work", "qty": 2, "price": 19.5}]}
    r = c.post("/api/invoices", json=body)
    assert r.status_code in (200, 201), r.text
    made = r.json()
    assert made["lines"][0]["price"] == "19.50"
    assert c.get(f"/api/invoices/{made['id']}").json()["lines"][0]["price"] == "19.50"
    body["lines"][0]["price"] = 20
    u = c.put(f"/api/invoices/{made['id']}", json=body)
    assert u.status_code == 200, u.text
    assert u.json()["lines"][0]["price"] == "20.00"
    assert c.get(f"/api/invoices/{made['id']}").json()["lines"][0]["price"] == "20.00"


def test_allocation_supplier_user_write_responses(boss_client):
    c = boss_client
    sup_body = {"name": "Sam Example (Demo)", "kind": "person-capacity", "weekly_hours": 24, "workdays": [0, 1, 2, 3]}
    sup = _check(c, "/api/suppliers", {**sup_body, "rate": 70.5}, {**sup_body, "rate": 71}, {"rate": "70.50"})
    proj = c.post("/api/projects", json={"name": "Demo project"}).json()
    al_body = {"supplier_id": sup["id"], "project_id": proj["id"], "start": START, "end": END, "total_hours": 24}
    al = _check(
        c, "/api/allocations", {**al_body, "rate": 90}, {**al_body, "rate": 91.5}, {"rate": "90.00"}, compare=False
    )
    assert al["planned_net"] == "2160.00"

    user_body = {
        "username": "demo1",
        "name": "Demo One",
        "role": "member",
        "password": "demo-pass-1234-x",
        "weekly_hours": 32,
    }
    made = c.post("/api/users", json={**user_body, "default_rate": 65.5, "cost_rate": 40}).json()
    assert made["default_rate"] == "65.50" and made["cost_rate"] == "40.00"
    listed = next(u for u in c.get("/api/users").json() if u["id"] == made["id"])
    assert listed["default_rate"] == "65.50"
    u = c.put(f"/api/users/{made['id']}", json={**user_body, "default_rate": 66, "cost_rate": 41.5})
    assert u.status_code == 200, u.text
    assert u.json()["default_rate"] == "66.00" and u.json()["cost_rate"] == "41.50"
