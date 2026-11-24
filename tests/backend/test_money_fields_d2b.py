"""D2b: decimal-string money in the responses of allocations, suppliers, users, settings and the personal tax
profile (docs/specs/decimal-money-api.md); percentages, hours and ratios stay numbers."""

from datetime import date, timedelta

START = (date.today() + timedelta(days=7)).isoformat()
END = (date.today() + timedelta(days=35)).isoformat()
PT = "/api/settings/personal-tax"


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _same(a, b):
    return {k: x for k, x in a.items() if k != "updated"} == {k: x for k, x in b.items() if k != "updated"}


def test_allocation_and_supplier_money_are_strings(boss_client):
    c = boss_client
    sup = c.post(
        "/api/suppliers",
        json={
            "name": "Sam Example (Demo)",
            "kind": "person-capacity",
            "weekly_hours": 24,
            "workdays": [0, 1, 2, 3],
            "rate": 70.5,
        },
    ).json()
    got = c.get(f"/api/suppliers/{sup['id']}").json()
    assert got["rate"] == "70.50"
    assert c.get("/api/suppliers").json()[0]["rate"] == "70.50"

    proj = c.post("/api/projects", json={"name": "Demo project"}).json()
    r = c.post(
        "/api/allocations",
        json={
            "supplier_id": sup["id"],
            "project_id": proj["id"],
            "start": START,
            "end": END,
            "total_hours": 24,
            "rate": 90,
        },
    )
    assert r.status_code == 201, r.text
    al = c.get(f"/api/allocations/{r.json()['id']}").json()
    assert al["rate"] == "90.00"
    for k in ("planned_net", "invoiced_net", "open_net"):
        assert al.get(k) is None or isinstance(al[k], str), k
    assert al["planned_net"] == "2160.00"
    assert _is_number(al["total_hours"]) and _is_number(al["planned_hours"])
    assert c.get("/api/allocations").json()[0]["rate"] == "90.00"

    # AllocationIn is strict (no response-only fields): send back the editable fields, the rate as the returned string
    editable = {k: al[k] for k in ("supplier_id", "project_id", "start", "end", "total_hours", "rate")}
    put = c.put(f"/api/allocations/{al['id']}", json=editable)
    assert put.status_code == 200, put.text
    assert _same(c.get(f"/api/allocations/{al['id']}").json(), al)

    sup_put = c.put(f"/api/suppliers/{sup['id']}", json=got)
    assert sup_put.status_code == 200, sup_put.text
    assert _same(c.get(f"/api/suppliers/{sup['id']}").json(), got)


def test_user_rates_are_strings(boss_client):
    c = boss_client
    r = c.post(
        "/api/users",
        json={
            "username": "demo1",
            "name": "Demo One",
            "role": "member",
            "password": "demo-pass-1234-x",
            "default_rate": 65.5,
            "cost_rate": 40,
            "weekly_hours": 32,
        },
    )
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    assert r.json()["default_rate"] == "65.50"
    got = next(u for u in c.get("/api/users").json() if u["id"] == uid)
    assert got["default_rate"] == "65.50" and got["cost_rate"] == "40.00"
    assert _is_number(got["weekly_hours"]) and got["weekly_hours"] == 32
    me = c.get("/api/auth/me").json()
    for k in ("default_rate", "cost_rate"):
        assert me.get(k) is None or isinstance(me[k], str), k
    put = c.put(f"/api/users/{uid}", json=got)
    assert put.status_code == 200, put.text
    assert _same(next(u for u in c.get("/api/users").json() if u["id"] == uid), got)


def test_settings_rates_are_strings(boss_client):
    c = boss_client
    s = c.get("/api/settings").json()
    assert s["default_rate"] == "80.00" and s["planning_min_rate"] == "0.00"
    for k in ("vat_rate", "hebesatz", "payment_days", "customer_dominance_percent"):
        assert _is_number(s[k]), k
    put = c.put("/api/settings", json={**s, "default_rate": "85.25", "planning_min_rate": 50})
    assert put.status_code == 200, put.text
    s2 = c.get("/api/settings").json()
    assert s2["default_rate"] == "85.25" and s2["planning_min_rate"] == "50.00"
    assert c.put("/api/settings", json=s2).status_code == 200
    assert _same(c.get("/api/settings").json(), s2)


def test_personal_tax_money_are_strings(boss_client):
    c = boss_client
    d = c.get(PT).json()
    assert d["other_income_year"] == "0.00" and d["health"]["pkv_monthly"] == "0.00"
    assert d["pension"]["fixed_monthly"] == "0.00"
    body = {
        "assessment": "joint",
        "church_tax_percent": 9,
        "other_income_year": 1200.5,
        "other_deductions_year": 300,
        "other_insurance_year": 80.25,
        "health": {
            "mode": "pkv",
            "pkv_monthly": 450.5,
            "pkv_basis_monthly": 300,
            "pkv_care_monthly": 60,
            "pkv_employer_subsidy_monthly": 100,
        },
        "pension": {"mode": "fixed", "fixed_monthly": 250},
        "employment": {
            "gross_year": 48000,
            "bav_employee_year": 1200,
            "bav_employer_year": 600,
            "vwl_employer_year": 120,
            "werbungskosten_year": 1230,
            "withheld_year": 7000.5,
            "steuerklasse": 1,
        },
    }
    r = c.put(PT, json=body)
    assert r.status_code == 200, r.text
    got = c.get(PT).json()
    assert got["other_income_year"] == "1200.50" and got["other_deductions_year"] == "300.00"
    assert got["other_insurance_year"] == "80.25"
    h = got["health"]
    assert (h["pkv_monthly"], h["pkv_basis_monthly"], h["pkv_care_monthly"], h["pkv_employer_subsidy_monthly"]) == (
        "450.50",
        "300.00",
        "60.00",
        "100.00",
    )
    assert got["pension"]["fixed_monthly"] == "250.00"
    e = got["employment"]
    assert (
        e["gross_year"],
        e["bav_employee_year"],
        e["bav_employer_year"],
        e["vwl_employer_year"],
        e["werbungskosten_year"],
        e["withheld_year"],
    ) == ("48000.00", "1200.00", "600.00", "120.00", "1230.00", "7000.50")
    assert got["church_tax_percent"] == 9 and _is_number(got["church_tax_percent"])
    assert _is_number(e["steuerklasse"])
    put = c.put(PT, json=got)
    assert put.status_code == 200, put.text
    assert c.get(PT).json() == got
