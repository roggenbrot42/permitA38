"""FX-A: write, action and planner responses send money as decimal strings, warning details included
(docs/specs/decimal-money-api.md rules 3 and 4). Fake data only."""

import re
from datetime import date, timedelta

DECIMAL = re.compile(r"^-?[0-9]+\.[0-9]{2,6}$")
TODAY = date.today()
START = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)
END = START + timedelta(days=27)  # 4 weeks


def _is_decimal(value) -> bool:
    return isinstance(value, str) and bool(DECIMAL.match(value))


def _warning(warnings: list[dict], code: str) -> dict:
    found = [w for w in warnings if w["code"] == code]
    assert found, (code, [w["code"] for w in warnings])
    return found[0]


def _setup(c, *, price=90, hours=40, rate=100):
    c.put("/api/settings", json={"planning_min_rate": 120})
    cust = c.post("/api/customers", json={"name": "Demo Client GmbH (Demo)", "rate": rate}).json()
    proj = c.post("/api/projects", json={"name": "Demo project", "customer_id": cust["id"], "rate": rate}).json()
    off = c.post(
        "/api/offers",
        json={
            "customer_id": cust["id"],
            "project_id": proj["id"],
            "lines": [{"description": "Dev", "qty": hours, "unit": "h", "price": price}],
            "plan": {"start": START.isoformat(), "end": END.isoformat(), "hours_per_week": 8, "rate": 95},
        },
    ).json()
    return cust, proj, off


def test_offer_accept_money_strings(boss_client):
    c = boss_client
    cust, proj, off = _setup(c)
    assert c.post(f"/api/offers/{off['id']}/finalize").status_code == 200
    r = c.post(f"/api/offers/{off['id']}/accept")
    assert r.status_code == 200, r.text
    body = r.json()
    line = body["offer"]["lines"][0]
    assert line["price"] == "90.00" and _is_decimal(line["amount"])
    assert _is_decimal(body["project"]["rate"])
    assert body["contract"] is not None
    assert _is_decimal(body["contract"]["rate"])
    # the shortfall warning carries hours only: they stay numbers
    short = _warning(body["planning_warnings"], "contract.shortfall")
    assert isinstance(short["details"]["shortfall"], int | float)


def test_finalize_offer_warning_details_are_strings(boss_client):
    c = boss_client
    _, _, off = _setup(c)
    r = c.post(f"/api/offers/{off['id']}/finalize")
    assert r.status_code == 200, r.text
    below = _warning(r.json()["planning_warnings"], "rate.below_minimum")
    assert below["details"]["price"] == "90.00" and below["details"]["minimum"] == "120.00"
    eff = _warning(r.json()["planning_warnings"], "rate.effective_below_minimum")
    assert _is_decimal(eff["details"]["effective_rate"])


def test_hours_warning_details_stay_numbers(boss_client):
    c = boss_client
    _, _, off = _setup(c, hours=10)  # lines bill 10 h, the plan promises 32 h
    r = c.post(f"/api/offers/{off['id']}/finalize")
    mismatch = _warning(r.json()["planning_warnings"], "offer.hours_mismatch")
    for key in ("line_hours", "plan_hours"):
        v = mismatch["details"][key]
        assert isinstance(v, int | float) and not isinstance(v, bool), (key, v)
    assert mismatch["details"]["line_hours"] == 10


def test_check_offer_warnings(boss_client):
    c = boss_client
    cust, proj, _ = _setup(c)
    draft = {
        "customer_id": cust["id"],
        "project_id": proj["id"],
        "lines": [{"description": "Dev", "qty": 10, "unit": "h", "price": 90}],
        "plan": {"start": START.isoformat(), "end": END.isoformat(), "total_hours": 10},
    }
    r = c.post("/api/plan/check-offer", json=draft)
    assert r.status_code == 200, r.text
    below = _warning(r.json(), "rate.below_minimum")
    assert below["details"]["price"] == "90.00" and below["details"]["minimum"] == "120.00"
    ref = _warning(r.json(), "rate.below_reference")
    assert ref["details"]["reference"] == "100.00"


def test_extend_and_project_planning(boss_client):
    c = boss_client
    _, proj, _ = _setup(c, rate=100)
    new_end = (END + timedelta(days=14)).isoformat()
    c.put(f"/api/projects/{proj['id']}", json={**proj, "start": START.isoformat(), "end": END.isoformat()})
    r = c.post(f"/api/projects/{proj['id']}/extend", json={"new_end": new_end, "reason": "more work"})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["rate"] == "100.00"
    t = c.get(f"/api/projects/{proj['id']}/planning")
    assert t.status_code == 200, t.text
    assert t.json()["project"]["rate"] == "100.00"


def test_bootstrap_me_rates(boss_client):
    c = boss_client
    me = c.get("/api/auth/me").json()
    r = c.put(
        f"/api/users/{me['id']}",
        json={
            "username": me["username"],
            "name": me["name"],
            "role": me["role"],
            "active": True,
            "default_rate": 95,
            "cost_rate": 60.5,
        },
    )
    assert r.status_code == 200, r.text
    me2 = c.get("/api/bootstrap").json()["me"]
    assert me2["default_rate"] == "95.00"
    assert me2["cost_rate"] == "60.50"
