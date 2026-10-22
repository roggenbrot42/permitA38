"""Input validation (hard limits) and defensive aggregates for records that are already stored."""

from datetime import date, timedelta

import pytest

TODAY = date.today()


def _ids(c):
    cust = c.post("/api/customers", json={"name": "ACME GmbH"}).json()
    proj = c.post("/api/projects", json={"name": "Site", "customer_id": cust["id"]}).json()
    me = c.get("/api/auth/me").json()
    return cust["id"], proj["id"], me["id"]


def _422(r):
    assert r.status_code == 422, (r.status_code, r.text[:300])
    body = r.json()
    assert isinstance(body["detail"], (list, str))
    if isinstance(body["detail"], list):  # readable, and the offending input is never echoed back
        for e in body["detail"]:
            assert set(e) == {"loc", "msg", "type"}
    return body["detail"]


# ------------------------------------------------------------------ dates
@pytest.mark.parametrize(
    "bad", ["2026-13-45", "2026-02-30", "garbage", "2026-1-5", "26-01-01", "1999-12-31", "2101-01-01", ""]
)
def test_entry_dates_must_be_real_and_plausible(boss_client, bad):
    _422(boss_client.post("/api/entries", json={"date": bad, "minutes": 60}))


@pytest.mark.parametrize(
    "coll,body",
    [
        ("expenses", {"date": "2026-13-45", "net": 1}),
        ("contracts", {"title": "x", "start": "nope", "end": "2026-12-31", "hours_per_week": 5}),
        ("absences", {"start": "garbage"}),
        ("invoices", {"date": "2026-02-30", "lines": []}),
        ("offers", {"valid_until": "soon", "lines": []}),
        ("invoices", {"service_from": "2026-05-02", "service_to": "2026-05-01"}),
        ("absences", {"start": "2026-05-02", "end": "2026-05-01"}),
        ("contracts", {"title": "x", "start": "2026-05-02", "end": "2026-05-01"}),
    ],
)
def test_invalid_dates_and_periods_are_rejected_everywhere(boss_client, coll, body):
    _422(boss_client.post(f"/api/{coll}", json=body))


def test_blank_optional_fields_from_the_web_forms_are_accepted(boss_client):
    c = boss_client
    cust, proj, me = _ids(c)
    r = c.post(
        "/api/entries",
        json={"date": "2026-03-02", "minutes": 30, "start": "", "end": "", "project_id": "", "task_id": ""},
    )
    assert r.status_code == 200, r.text
    r = c.post(
        "/api/invoices", json={"customer_id": cust, "project_id": "", "due_date": "", "service_from": "", "lines": []}
    )
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ numbers
@pytest.mark.parametrize("minutes", [0, -5, 1441, 10**400, 90.5, "abc", None])
def test_entry_minutes(boss_client, minutes):
    _422(boss_client.post("/api/entries", json={"date": "2026-03-02", "minutes": minutes}))


@pytest.mark.parametrize(
    "coll,body",
    [
        ("expenses", {"date": "2026-03-02", "net": -5}),
        ("expenses", {"date": "2026-03-02", "net": 1e12}),
        ("expenses", {"date": "2026-03-02", "net": 1, "deductible": 150}),
        ("customers", {"name": "x", "rate": -1}),
        ("customers", {"name": "x", "rate": 10**6}),
        ("customers", {"name": ""}),
        ("customers", {"name": "x" * 201}),
        ("customers", {"name": "x", "notes": "n" * 5001}),
        ("projects", {"name": "x", "status": "weird"}),
        ("projects", {"name": "x", "color": "red"}),
        ("projects", {"name": "x", "budget_hours": -1}),
        ("contracts", {"title": "x", "start": "2026-01-01", "end": "2026-02-01", "hours_per_week": 169}),
        ("absences", {"start": "2026-01-01", "kind": "nap"}),
        ("invoices", {"status": "teapot"}),
        ("invoices", {"lines": [{"qty": 1e9, "price": 1}]}),
        ("invoices", {"lines": [{"qty": 1, "price": 1e10}]}),
        ("invoices", {"lines": [{"description": "d" * 2001}]}),
        ("invoices", {"lines": [{"qty": 1}] * 501}),
        ("invoices", {"vat_rate": 190}),
        ("templates", {"name": "x", "accent": "blue"}),
        ("templates", {"name": "x", "layout": "fancy"}),
    ],
)
def test_out_of_range_values_are_rejected(boss_client, coll, body):
    _422(boss_client.post(f"/api/{coll}", json=body))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"date":"2026-01-01","minutes":NaN}',
        b'{"date":"2026-01-01","minutes":Infinity}',
        b'{"name":"\\ud83d"}',
        b'{"name":"ok","extra":"\\ud83d"}',
        b'{"name":"ok","extra":' + b"1" + b"0" * 30 + b"}",
        b'{"name":"ok","extra":' + b"[" * 12 + b"]" * 12 + b"}",
        b'{"name":"a\\u0000b"}',
    ],
)
def test_hostile_json_is_a_clean_422_or_accepted_never_a_500(boss_client, raw):
    for coll in ("customers", "entries"):
        r = boss_client.post(f"/api/{coll}", content=raw, headers={"Content-Type": "application/json"})
        assert r.status_code in (200, 400, 422), (coll, r.status_code, r.text[:200])
    assert boss_client.get("/api/customers").status_code == 200


def test_lone_surrogate_stores_nothing(boss_client):
    r = boss_client.post("/api/customers", content=b'{"name": "\\ud83d"}', headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert boss_client.get("/api/customers").json() == []


def test_valid_extremes_are_accepted(boss_client):
    c = boss_client
    assert c.post("/api/entries", json={"date": "2000-01-01", "minutes": 1440}).status_code == 200
    assert c.post("/api/entries", json={"date": "2100-12-31", "minutes": 1}).status_code == 200
    assert c.post("/api/customers", json={"name": "ü" * 200, "rate": 10000}).status_code == 200
    assert c.post("/api/expenses", json={"date": "2026-03-02", "net": 0}).status_code == 200
    assert c.post("/api/invoices", json={"lines": [{"qty": -3, "price": -2}]}).status_code == 200  # credit-note maths


def test_validation_errors_name_the_field_and_do_not_echo_input(boss_client):
    detail = _422(boss_client.post("/api/entries", json={"date": "2026-13-45", "minutes": 60, "note": "secret"}))
    assert detail[0]["loc"] == ["body", "date"]
    assert "calendar" in detail[0]["msg"] or "date" in detail[0]["msg"]
    assert "2026-13-45" not in str(detail) and "secret" not in str(detail)


# ------------------------------------------------------------------ rules that need stored data or settings
def test_a_person_cannot_work_more_than_24_hours_a_day(boss_client):
    c = boss_client
    _, _, me = _ids(c)
    day = "2026-03-02"
    assert c.post("/api/entries", json={"user_id": me, "date": day, "minutes": 1000}).status_code == 200
    detail = _422(c.post("/api/entries", json={"user_id": me, "date": day, "minutes": 500}))
    assert "24" in str(detail)
    other = c.post("/api/entries", json={"user_id": me, "date": "2026-03-03", "minutes": 500})
    assert other.status_code == 200  # another day is fine
    # editing the first entry to fill the day exactly is fine
    first = c.get("/api/entries").json()[0]
    assert c.put(f"/api/entries/{first['id']}", json={**first, "minutes": 1440}).status_code == 200


def test_vat_rates_are_limited_to_real_german_rates_plus_the_configured_one(boss_client):
    c = boss_client
    assert (
        c.post("/api/invoices", json={"vat_rate": 19, "lines": [{"qty": 1, "price": 1, "vat": 7}]}).status_code == 200
    )
    detail = _422(c.post("/api/invoices", json={"lines": [{"qty": 1, "price": 1, "vat": 12.5}]}))
    assert "12.5" in str(detail)
    _422(c.post("/api/expenses", json={"date": "2026-03-02", "net": 1, "vat_rate": 21}))
    assert c.put("/api/settings", json={"vat_rate": 21}).status_code == 200  # an own rate becomes allowed
    assert c.post("/api/expenses", json={"date": "2026-03-02", "net": 1, "vat_rate": 21}).status_code == 200


def test_settings_validation(boss_client):
    c = boss_client
    for bad in (
        {"vat_rate": 150},
        {"company_type": "ag"},
        {"state": "XX"},
        {"hebesatz": -1},
        {"payment_days": 1000},
        {"invoice_pattern": "{year.__class__}-{n}"},
        {"invoice_pattern": "RE-{year}"},  # needs {n}
        {"offer_pattern": "{0}-{n}"},
        {"default_template": "../../etc"},
        {"social_monthly": -5},
    ):
        _422(c.put("/api/settings", json=bad))
    ok = c.put("/api/settings", json={"invoice_pattern": "R{year}/{n:05d}", "state": "BY", "hebesatz": 410})
    assert ok.status_code == 200 and ok.json()["invoice_pattern"] == "R{year}/{n:05d}"


def test_user_numbers_and_workdays(boss_client):
    c = boss_client
    base = {"username": "mia", "name": "Mia", "role": "member", "password": "correct horse battery"}
    for bad in (
        {"weekly_hours": 200},
        {"weekly_hours": -1},
        {"workdays": []},
        {"workdays": [0, 0]},
        {"workdays": [7]},
        {"default_rate": -3},
        {"tracking_start": "yesterday"},
        {"color": "red"},
        {"role": "owner"},
    ):
        _422(c.post("/api/users", json={**base, **bad}))
    assert c.post("/api/users", json={**base, "weekly_hours": 20, "workdays": [3, 1, 2]}).json()["workdays"] == [
        1,
        2,
        3,
    ]


def test_year_query_parameters_are_range_checked(boss_client):
    for year in (-1, 0, 1999, 2101, 10000, 99999):
        assert boss_client.get("/api/holidays", params={"year": year}).status_code == 422
        assert boss_client.get("/api/finance", params={"year": year}).status_code == 422
    assert boss_client.get("/api/holidays", params={"year": 2026}).status_code == 200


# ------------------------------------------------------------------ already-stored damaged records
def _damage(app):
    """Records as an older version, a bug or another synced device could have left in the database."""
    store = app.state.services.store
    uid = store.list("users")[0]["id"]
    cust = store.save("customers", {"name": "C", "rate": "abc"})
    proj = store.save("projects", {"name": "P", "customer_id": cust["id"], "rate": float("nan") if False else "x"})
    store.save("absences", {"start": "garbage", "user_id": uid})
    store.save("absences", {"start": "2026-10-19", "end": "2026-10-22"})  # no owner
    store.save("absences", {"start": "2026-10-22", "end": "2026-10-19", "user_id": uid})  # reversed
    store.save("contracts", {"title": "bad", "start": "nope", "end": "2026-12-31", "hours_per_week": 5})
    store.save("contracts", {"title": "text", "start": "2026-01-01", "end": "2026-12-31", "hours_per_week": "lots"})
    store.save(
        "contracts",
        {
            "title": "ok",
            "start": TODAY.isoformat(),
            "end": (TODAY + timedelta(days=60)).isoformat(),
            "hours_per_week": 10,
            "user_ids": [uid],
        },
    )
    store.save("expenses", {"date": "2026-13-45", "net": 1})
    store.save("expenses", {"date": TODAY.isoformat(), "net": "many", "vat": "x", "deductible": "y"})
    store.save("entries", {"user_id": uid, "date": "not-a-date", "minutes": 60})
    store.save("entries", {"user_id": uid, "date": TODAY.isoformat(), "minutes": "sixty", "project_id": proj["id"]})
    store.save("entries", {"user_id": uid, "date": TODAY.isoformat(), "minutes": 90, "project_id": proj["id"]})
    store.save(
        "invoices",
        {
            "status": "paid",
            "paid_date": "never",
            "date": "x",
            "lines": [{"qty": "a", "price": None}, "junk", {"qty": 2, "price": 10}],
        },
    )
    store.save("invoices", {"status": "sent", "date": TODAY.isoformat(), "lines": "not-a-list"})
    store.save("offers", {"status": "sent", "plan": {"start": "bad", "end": "worse"}})
    store.save(
        "users",
        {
            "username": "odd",
            "name": "Odd",
            "role": "member",
            "active": True,
            "weekly_hours": "forty",
            "workdays": ["mon", 99],
            "tracking_start": "bad",
            "password_hash": None,
        },
    )
    return uid


def test_damaged_stored_records_never_break_aggregate_endpoints(app, boss_client):
    uid = _damage(app)
    c = boss_client
    assert c.get("/api/forecast").status_code == 200
    assert c.get("/api/forecast", params={"weeks": 156}).status_code == 200
    assert c.get("/api/finance", params={"year": TODAY.year}).status_code == 200
    assert c.get("/api/project-report").status_code == 200
    dash = c.get("/api/dashboard", params={"user_id": uid})
    assert dash.status_code == 200
    # the valid entry (90 min) still counts; the damaged ones are ignored
    assert dash.json()["worked"] == 1.5
    fc = c.get("/api/forecast").json()
    assert any(w["confirmed"] > 0 for w in fc["weeks"]), "the valid contract is still planned"


def test_damaged_stored_records_do_not_break_the_odd_users_dashboard(app, boss_client):
    _damage(app)
    odd = next(u for u in boss_client.get("/api/users").json() if u["username"] == "odd")
    assert boss_client.get("/api/dashboard", params={"user_id": odd["id"]}).status_code == 200
