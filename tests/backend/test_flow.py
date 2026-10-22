from datetime import date, timedelta


def test_full_business_flow(boss_client):
    c = boss_client
    me = c.get("/api/auth/me").json()
    cust = c.post(
        "/api/customers",
        json={"name": "ACME GmbH", "street": "Teststr. 1", "zip": "10115", "city": "Berlin", "rate": 100},
    ).json()
    proj = c.post("/api/projects", json={"name": "Website", "customer_id": cust["id"]}).json()
    today = date.today().isoformat()
    c.post("/api/entries", json={"user_id": me["id"], "date": today, "minutes": 150, "project_id": proj["id"]})

    dash = c.get("/api/dashboard").json()
    assert dash["target"] > 0
    assert abs(dash["worked"] - 2.5) < 1e-9

    inv = c.post("/api/invoices/from-time", json={"customer_id": cust["id"]}).json()
    assert inv["lines"][0]["price"] == 100.0 and inv["lines"][0]["amount"] == 250.0
    fin = c.post(f"/api/invoices/{inv['id']}/finalize").json()
    assert fin["number"].startswith("RE-") and fin["number"].endswith("-0001")
    pdf = c.get(f"/api/invoices/{inv['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-1.4") and pdf.content.rstrip().endswith(b"%%EOF")
    assert pdf.headers["content-type"] == "application/pdf"

    # billed entries cannot be billed twice
    assert c.post("/api/invoices/from-time", json={"customer_id": cust["id"]}).status_code == 400

    fin.update(status="paid", paid_date=today)
    assert c.put(f"/api/invoices/{inv['id']}", json=fin).status_code == 200
    c.post("/api/expenses", json={"date": today, "net": 50, "vat": 9.5, "category": "Software"})
    f = c.get("/api/finance").json()
    assert abs(f["totals"]["revenue"] - 250) < 0.01
    assert abs(f["totals"]["profit"] - 200) < 0.01
    assert abs(f["totals"]["vat_due"] - (47.5 - 9.5)) < 0.01

    off = c.post(
        "/api/offers",
        json={
            "customer_id": cust["id"],
            "lines": [{"description": "Dev", "qty": 40, "unit": "h", "price": 90}],
            "plan": {"start": today, "end": "2099-01-01", "hours_per_week": 10},
        },
    ).json()
    assert c.post(f"/api/offers/{off['id']}/finalize").json()["status"] == "sent"
    fc = c.get("/api/forecast", params={"weeks": 4}).json()
    assert fc["weeks"][1]["tentative"] > 0 and fc["weeks"][1]["confirmed"] == 0
    accepted = c.post(f"/api/offers/{off['id']}/accept").json()
    assert accepted["project"]["id"] and accepted["contract"]["id"]
    fc = c.get("/api/forecast", params={"weeks": 4}).json()
    assert fc["weeks"][1]["confirmed"] > 0 and fc["weeks"][1]["tentative"] == 0
    assert c.get(f"/api/offers/{off['id']}/pdf").status_code == 200
    back = c.post(f"/api/offers/{off['id']}/to-invoice").json()
    assert back["status"] == "draft" and back["lines"][0]["description"] == "Dev"


def test_crud_roundtrip_and_204(boss_client):
    c = boss_client
    created = c.post("/api/customers", json={"name": "A", "future_field": "keep me"}).json()
    assert created["id"] and created["future_field"] == "keep me"
    created["name"] = "B"
    assert c.put(f"/api/customers/{created['id']}", json=created).json()["name"] == "B"
    assert c.get(f"/api/customers/{created['id']}").json()["future_field"] == "keep me"
    assert c.delete(f"/api/customers/{created['id']}").status_code == 204
    assert c.get(f"/api/customers/{created['id']}").status_code == 404
    assert c.get("/api/customers/doesnotexist").status_code == 404


def test_deleting_invoice_releases_time(boss_client):
    c = boss_client
    me = c.get("/api/auth/me").json()
    cust = c.post("/api/customers", json={"name": "ACME", "rate": 80}).json()
    proj = c.post("/api/projects", json={"name": "P", "customer_id": cust["id"]}).json()
    c.post(
        "/api/entries",
        json={"user_id": me["id"], "date": date.today().isoformat(), "minutes": 60, "project_id": proj["id"]},
    )
    inv = c.post("/api/invoices/from-time", json={}).json()
    assert c.delete(f"/api/invoices/{inv['id']}").status_code == 204
    assert c.post("/api/invoices/from-time", json={}).status_code == 200


def test_timer_start_stop(boss_client):
    c = boss_client
    assert c.get("/api/timer").json()["running"] is False
    assert c.post("/api/timer/start", json={"note": "focus"}).json()["running"] is True
    assert c.get("/api/timer").json()["running"] is True
    r = c.post("/api/timer/stop", json={}).json()
    assert r["running"] is False and r["entry"]["minutes"] >= 1


def test_settings_holidays_and_report(boss_client):
    c = boss_client
    s = c.get("/api/settings").json()
    s.update(state="BY", company_type="gewerbe")
    assert c.put("/api/settings", json=s).json()["state"] == "BY"
    h = c.get("/api/holidays", params={"year": 2026}).json()
    assert h["2026-01-06"] == "Heilige Drei Könige"
    assert isinstance(c.get("/api/project-report").json(), dict)


def test_absence_reduces_capacity(boss_client):
    c = boss_client
    me = c.get("/api/auth/me").json()
    monday = date.today() - timedelta(days=date.today().weekday())
    nxt = monday + timedelta(weeks=1)
    base = c.get("/api/forecast", params={"weeks": 3}).json()["weeks"][1]["capacity"]
    c.post(
        "/api/absences",
        json={
            "user_id": me["id"],
            "kind": "vacation",
            "start": nxt.isoformat(),
            "end": (nxt + timedelta(days=4)).isoformat(),
        },
    )
    off = c.get("/api/forecast", params={"weeks": 3}).json()["weeks"][1]["capacity"]
    assert off < base


def test_attachment_upload_and_download(boss_client):
    c = boss_client
    r = c.post("/api/attachments", files={"file": ("bill.pdf", b"%PDF-1.4 demo", "application/pdf")})
    assert r.status_code == 200
    name = r.json()["name"]
    assert c.get(f"/api/attachments/{name}").content == b"%PDF-1.4 demo"
    assert c.get("/api/attachments/..%2F..%2Fsettings.json").status_code in (400, 404)


def test_openapi_in_sync(app):
    import json
    from pathlib import Path

    committed = json.loads((Path(__file__).parents[2] / "api" / "openapi.json").read_text(encoding="utf-8"))
    assert app.openapi() == committed, "api/openapi.json is stale: run `make generate-openapi`"
