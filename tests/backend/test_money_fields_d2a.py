"""D2a: decimal-string money in the stored-record responses of customers, projects, tasks, entries, contracts and
documents (rate / price / amount); percentages (``vat``) and quantities stay numbers."""

from datetime import date

import pytest

TODAY = date.today().isoformat()


def _setup(c):
    me = c.get("/api/auth/me").json()
    cust = c.post("/api/customers", json={"name": "ACME GmbH", "rate": 80}).json()
    proj = c.post("/api/projects", json={"name": "Website", "customer_id": cust["id"], "rate": 12.5}).json()
    task = c.post("/api/tasks", json={"name": "Design", "project_id": proj["id"], "rate": 95}).json()
    entry = c.post(
        "/api/entries",
        json={"user_id": me["id"], "date": TODAY, "minutes": 60, "project_id": proj["id"], "rate": 70},
    ).json()
    contract = c.post(
        "/api/contracts",
        json={
            "title": "Retainer",
            "customer_id": cust["id"],
            "project_id": proj["id"],
            "start": TODAY,
            "end": "2099-01-01",
            "hours_per_week": 8,
            "rate": 85,
        },
    ).json()
    offer = c.post(
        "/api/offers",
        json={
            "customer_id": cust["id"],
            "lines": [{"description": "Dev", "qty": 2.5, "unit": "h", "price": 12.5, "vat": 19}],
            "plan": {"start": TODAY, "end": "2099-01-01", "hours_per_week": 10, "rate": 80},
        },
    ).json()
    return cust, proj, task, entry, contract, offer


def test_records_return_decimal_strings(boss_client):
    cust, proj, task, entry, contract, offer = _setup(boss_client)
    c = boss_client
    assert c.get(f"/api/customers/{cust['id']}").json()["rate"] == "80.00"
    assert c.get(f"/api/projects/{proj['id']}").json()["rate"] == "12.50"
    assert c.get(f"/api/tasks/{task['id']}").json()["rate"] == "95.00"
    assert c.get(f"/api/entries/{entry['id']}").json()["rate"] == "70.00"
    assert c.get(f"/api/contracts/{contract['id']}").json()["rate"] == "85.00"
    got = c.get(f"/api/offers/{offer['id']}").json()
    line = got["lines"][0]
    assert line["price"] == "12.50"
    assert line["amount"] == "31.25"
    assert line["vat"] == 19 and not isinstance(line["vat"], str)
    assert line["qty"] == 2.5 and not isinstance(line["qty"], str)
    assert got["plan"]["rate"] == "80.00"
    # lists use the same presentation
    assert c.get("/api/customers").json()[0]["rate"] == "80.00"


def test_invoice_lines_are_strings(boss_client):
    cust, *_ = _setup(boss_client)
    inv = boss_client.post("/api/invoices/from-time", json={"customer_id": cust["id"]}).json()
    got = boss_client.get(f"/api/invoices/{inv['id']}").json()
    line = got["lines"][0]
    assert isinstance(line["price"], str) and isinstance(line["amount"], str)


@pytest.mark.parametrize(
    "path,idx",
    [("customers", 0), ("projects", 1), ("tasks", 2), ("entries", 3), ("contracts", 4), ("offers", 5)],
)
def test_put_of_returned_record_round_trips(boss_client, path, idx):
    rec = _setup(boss_client)[idx]
    c = boss_client
    got = c.get(f"/api/{path}/{rec['id']}").json()
    put = c.put(f"/api/{path}/{rec['id']}", json=got)
    assert put.status_code == 200, put.text
    again = c.get(f"/api/{path}/{rec['id']}").json()
    # the server stamps `updated` on every write
    assert {k: x for k, x in again.items() if k != "updated"} == {k: x for k, x in got.items() if k != "updated"}
