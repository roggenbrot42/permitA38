"""Input validation (hard limits) and defensive aggregates for records that are already stored."""

from datetime import date, timedelta

import pytest

from .conftest import _login

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
        ("contracts", {"title": "x", "start": "2026-01-01", "end": "2026-02-01", "hours_per_week": 10000.01}),
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
    ):
        _422(c.put("/api/settings", json=bad))
    ok = c.put("/api/settings", json={"state": "BY", "hebesatz": 410})
    assert ok.status_code == 200
    # numbering changes go through the guarded numbering endpoint, which validates the pattern too
    pattern = c.put("/api/numbering", json={"type": "invoice", "pattern": "R{year}/{n:05d}"})
    assert pattern.status_code == 200 and pattern.json()["applied"] is True
    assert c.get("/api/settings").json()["invoice_pattern"] == "R{year}/{n:05d}"


LEGACY = ("social_monthly", "church_tax", "splitting", "other_income", "other_deductions")
PROFILE = {"assessment": "joint", "church_tax_percent": 9}


@pytest.mark.parametrize("key", LEGACY)
def test_legacy_tax_keys_are_refused_and_nothing_is_stored(app, boss_client, key):
    store = app.state.services.store
    store.save_settings({**store.settings(), "personal_tax": PROFILE})
    before = store.settings()
    detail = _422(boss_client.put("/api/settings", json={"state": "BY", key: 5}))
    assert key in str(detail) and "personal tax profile" in str(detail)
    assert store.settings() == before


def test_personal_tax_is_refused_in_put_settings(app, boss_client):
    store = app.state.services.store
    store.save_settings({**store.settings(), "personal_tax": PROFILE})
    r = boss_client.put("/api/settings", json={"state": "BY", "personal_tax": {"assessment": "single"}})
    assert r.status_code == 400 and "PUT /api/settings/personal-tax" in r.json()["detail"]
    assert store.settings()["personal_tax"] == PROFILE
    assert store.settings().get("state") != "BY"


def test_stored_legacy_keys_and_profile_are_hidden_and_deleted_lazily(app, boss_client):
    store = app.state.services.store
    hidden = ("personal_tax", *LEGACY)
    legacy = {"social_monthly": 500, "church_tax": 9, "splitting": True, "other_income": 1, "other_deductions": 2}
    store.save_settings({**store.settings(), **legacy, "personal_tax": PROFILE})
    accountant = boss_client.post(
        "/api/users", json={"username": "acc", "name": "Acc", "role": "accountant", "password": "correct horse battery"}
    )
    assert accountant.status_code == 200, accountant.text
    acc = _login(app, "acc", "correct horse battery")
    for c in (boss_client, acc):
        assert not set(hidden) & set(c.get("/api/bootstrap").json()["settings"])
    assert not set(hidden) & set(boss_client.get("/api/settings").json())
    assert not set(hidden) & set(boss_client.put("/api/settings", json={"state": "BY"}).json())
    raw = store.settings()
    assert not set(LEGACY) & set(raw)
    assert raw["personal_tax"] == PROFILE
    acc.close()


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
def _damage(app, plant):
    """Records as an older version, a bug or another synced device could have left in the database."""
    store = app.state.services.store
    uid = store.list("users")[0]["id"]

    cust = store.save("customers", {"name": "C", "rate": "abc"})
    proj = store.save(
        "projects", {"name": "P", "kind": "customer", "customer_id": cust["id"], "rate": float("nan") if False else "x"}
    )
    plant(store, "absences", {"start": "garbage", "user_id": uid}, "format.date")
    store.save("absences", {"start": "2026-10-19", "end": "2026-10-22"})  # no owner
    plant(
        store, "absences", {"start": "2026-10-22", "end": "2026-10-19", "user_id": uid}, "state.period_reversed"
    )  # reversed
    plant(
        store,
        "contracts",
        {"title": "bad", "start": "nope", "end": "2026-12-31", "hours_per_week": 5},
        ("format.date", "state.period_reversed"),
    )
    plant(
        store,
        "contracts",
        {"title": "text", "start": "2026-01-01", "end": "2026-12-31", "hours_per_week": "lots"},
        ("range.bounds", "state.exactly_one"),
    )
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
    plant(store, "expenses", {"date": "2026-13-45", "net": 1}, "format.date")
    store.save("expenses", {"date": TODAY.isoformat(), "net": "many", "vat": "x", "deductible": "y"})
    plant(store, "entries", {"user_id": uid, "date": "not-a-date", "minutes": 60}, "format.date")
    store.save("entries", {"user_id": uid, "date": TODAY.isoformat(), "minutes": "sixty", "project_id": proj["id"]})
    store.save("entries", {"user_id": uid, "date": TODAY.isoformat(), "minutes": 90, "project_id": proj["id"]})
    plant(
        store,
        "invoices",
        {
            "status": "paid",
            "paid_date": "never",
            "date": "x",
            "lines": [{"qty": "a", "price": None}, "junk", {"qty": 2, "price": 10}],
        },
        "format.date",
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


def test_damaged_stored_records_never_break_aggregate_endpoints(app, boss_client, unguarded):
    uid = _damage(app, unguarded)
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


def test_damaged_stored_records_do_not_break_the_odd_users_dashboard(app, boss_client, unguarded):
    _damage(app, unguarded)
    odd = next(u for u in boss_client.get("/api/users").json() if u["username"] == "odd")
    assert boss_client.get("/api/dashboard", params={"user_id": odd["id"]}).status_code == 200


# ------------------------------------------------------------------ project-centric planning: hours, kind, unassigned
_CONTRACT = {"title": "x", "start": "2026-01-01", "end": "2026-02-01"}


def _contract_body(c, **extra):
    return {**_CONTRACT, "project_id": _ids(c)[1], **extra}


@pytest.mark.parametrize(
    "field,value", [("hours_per_week", 0), ("hours_per_week", 10000.01), ("total_hours", 0), ("total_hours", 100000.01)]
)
def test_contract_hour_bounds_refused(boss_client, field, value):
    _422(boss_client.post("/api/contracts", json=_contract_body(boss_client, **{field: value})))


@pytest.mark.parametrize(
    "field,value",
    [("hours_per_week", 0.0001), ("hours_per_week", 10000), ("total_hours", 0.0001), ("total_hours", 100000)],
)
def test_contract_hour_bounds_accepted(boss_client, field, value):
    r = boss_client.post("/api/contracts", json=_contract_body(boss_client, **{field: value}))
    assert r.status_code == 200, r.text


def test_contract_needs_exactly_one_hour_field_and_a_project(boss_client):
    _422(boss_client.post("/api/contracts", json=_contract_body(boss_client, hours_per_week=5, total_hours=50)))
    _422(boss_client.post("/api/contracts", json=_contract_body(boss_client)))
    pid = _ids(boss_client)[1]
    _422(boss_client.post("/api/contracts", json={**_CONTRACT, "hours_per_week": 5}))
    _422(boss_client.post("/api/contracts", json={**_CONTRACT, "project_id": "", "hours_per_week": 5}))
    _422(boss_client.post("/api/contracts", json={**_CONTRACT, "project_id": "unassigned", "hours_per_week": 5}))
    assert (
        boss_client.post(
            "/api/contracts", json={**_CONTRACT, "project_id": pid, "hours_per_week": 5, "user_ids": []}
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "plan,code",
    [
        ({"hours_per_week": 0}, 422),
        ({"hours_per_week": 10000.01}, 422),
        ({"total_hours": 0}, 422),
        ({"total_hours": 100000.01}, 422),
        ({"hours_per_week": 5, "total_hours": 50}, 422),
        ({"hours_per_week": 10000}, 200),
        ({"total_hours": 100000}, 200),
        ({"total_hours": 0.0001}, 200),
        ({}, 200),
    ],
)
def test_offer_plan_hours(boss_client, plan, code):
    r = boss_client.post("/api/offers", json={"plan": {"start": "2026-01-01", "end": "2026-02-01", **plan}})
    assert r.status_code == code, r.text


def test_commitment_change_hours():
    from pydantic import ValidationError

    from permita38.core.models import CommitmentChange

    base = {"kind": "contract", "id": "abcdef123456"}
    for bad in (
        {"hours_per_week": 0},
        {"total_hours": 0},
        {"hours_per_week": 10000.01},
        {"total_hours": 100000.01},
        {"hours_per_week": 1, "total_hours": 1},
    ):
        with pytest.raises(ValidationError):
            CommitmentChange(**base, **bad)
    for good in ({}, {"hours_per_week": 10000}, {"total_hours": 100000}, {"total_hours": 0.0001}):
        CommitmentChange(**base, **good)


@pytest.mark.parametrize("hours,code", [(0, 422), (0.0001, 200), (100000, 200), (100000.01, 422)])
def test_project_budget_hours(boss_client, hours, code):
    assert boss_client.post("/api/projects", json={"name": "P", "budget_hours": hours}).status_code == code


@pytest.mark.parametrize("flag", [True, False])
def test_project_personal_is_refused(boss_client, flag):
    _422(boss_client.post("/api/projects", json={"name": "P", "personal": flag}))


def test_project_kind_rules(boss_client):
    cid = boss_client.post("/api/customers", json={"name": "ACME GmbH"}).json()["id"]
    post = lambda **b: boss_client.post("/api/projects", json={"name": "P", **b})  # noqa: E731
    assert post().json()["kind"] == "internal"
    assert post(customer_id=cid).json()["kind"] == "customer"
    assert post(kind="customer", customer_id=cid).status_code == 200
    assert post(kind="internal").status_code == 200
    _422(post(kind="customer"))
    _422(post(kind="internal", customer_id=cid))
    _422(post(kind="weird"))


def test_incoming_plan_hours_are_optional(boss_client):
    from permita38.core.models import IncomingPlanIn

    IncomingPlanIn(start="2026-01-01", end="2026-02-01")
    IncomingPlanIn(start="2026-01-01", end="2026-02-01", hours_per_week=5, total_hours=50)


def test_unassigned_project_id_is_refused_everywhere(boss_client):
    c = boss_client
    u = "unassigned"
    day = {"date": "2026-01-05"}
    for path, body in (
        ("contracts", {**_CONTRACT, "project_id": u, "hours_per_week": 5}),
        ("offers", {"project_id": u}),
        ("invoices", {"project_id": u}),
        ("tasks", {"project_id": u, "name": "t"}),
        ("entries", {**day, "minutes": 60, "project_id": u}),
        ("expenses", {**day, "net": 1, "project_id": u}),
    ):
        _422(c.post(f"/api/{path}", json=body))
