"""API: advisory warnings are returned next to saved records (``sanity_warnings``), never stored, never block."""

from datetime import date, timedelta

from permita38.core.service.sanity_rules import OLD_DAYS


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def codes(body):
    return [w["code"] for w in body.get("sanity_warnings") or []]


def last_weekday(wd: int, back_from: int = 3) -> str:
    """A recent date with the given weekday (0 = Monday) that is not a Sunday-adjacent surprise."""
    d = date.today() - timedelta(days=back_from)
    while d.weekday() != wd:
        d -= timedelta(days=1)
    return d.isoformat()


def post_entry(client, **kw):
    body = {"date": last_weekday(1), "minutes": 60, **kw}
    r = client.post("/api/entries", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_long_day_without_break_warns_but_saves(boss_client, member_client):
    d = last_weekday(2)
    r = member_client.post("/api/entries", json={"date": d, "start": "07:00", "end": "18:00", "minutes": 660})
    assert r.status_code == 200
    body = r.json()
    assert sorted(codes(body)) == ["time.break_missing", "time.day_over_10h"]
    assert {w["severity"] for w in body["sanity_warnings"]} == {"warning"}  # an employee
    # saved regardless
    assert member_client.get(f"/api/entries/{body['id']}").json()["minutes"] == 660
    assert "sanity_warnings" not in member_client.get(f"/api/entries/{body['id']}").json()  # never stored
    assert all("sanity_warnings" not in e for e in member_client.get("/api/entries").json())


def test_same_finding_is_only_info_for_the_boss(boss_client):
    me = boss_client.get("/api/auth/me").json()["id"]
    body = post_entry(boss_client, user_id=me, date=last_weekday(2), start="07:00", end="18:00", minutes=660)
    assert body["user_id"] == me
    assert {w["severity"] for w in body["sanity_warnings"]} == {"info"}


def test_second_entry_of_the_day_sees_the_first(member_client):
    d = last_weekday(3)
    first = member_client.post(
        "/api/entries", json={"date": d, "start": "08:00", "end": "12:00", "minutes": 240}
    ).json()
    assert codes(first) == []
    # 12:10-17:00: 10 min gap is no break, so 9 h of continuous work
    second = member_client.post(
        "/api/entries", json={"date": d, "start": "12:10", "end": "17:00", "minutes": 290}
    ).json()
    assert codes(second) == ["time.break_missing"]
    assert second["sanity_warnings"][0]["details"]["break_minutes"] == 0
    # editing the second to leave a 30 minute gap clears it (the first entry is read from the store, itself excluded)
    fixed = member_client.put(
        f"/api/entries/{second['id']}", json={"date": d, "start": "12:30", "end": "16:30", "minutes": 240}
    ).json()
    assert codes(fixed) == []


def test_rest_period_between_days(member_client):
    d2 = last_weekday(1)  # a Tuesday
    d1 = (date.fromisoformat(d2) - timedelta(days=1)).isoformat()  # the Monday before
    member_client.post("/api/entries", json={"date": d1, "start": "16:00", "end": "22:00", "minutes": 360})
    r = member_client.post("/api/entries", json={"date": d2, "start": "08:00", "end": "09:00", "minutes": 60}).json()
    assert codes(r) == ["time.rest_short"]  # 22:00 -> 08:00 = 10 h
    assert r["sanity_warnings"][0]["details"] == {"rest_minutes": 600, "other_day": d1}
    # moving the entry to 09:00 gives exactly 11 h of rest: fine
    ok = member_client.put(
        f"/api/entries/{r['id']}", json={"date": d2, "start": "09:00", "end": "10:00", "minutes": 60}
    ).json()
    assert codes(ok) == []


def test_timer_stop_returns_warnings_only_with_an_entry(boss_client):
    boss_client.post("/api/timer/start", json={})
    out = boss_client.post("/api/timer/stop", json={}).json()
    assert out["entry"]["minutes"] == 1
    assert out["sanity_warnings"] == []
    assert "sanity_warnings" not in boss_client.post("/api/timer/stop", json={}).json()  # nothing was running


def test_customer_tax_ids(boss_client):
    r = boss_client.post("/api/customers", json={"name": "Demo GmbH", "vat_id": "DE136695977", "tax_number": "12345"})
    assert r.status_code == 200
    assert sorted(codes(r.json())) == ["customer.tax_number_invalid", "customer.vat_id_invalid"]
    fields = {w["field"] for w in r.json()["sanity_warnings"]}
    assert fields == {"vat_id", "tax_number"}
    ok = boss_client.put(
        f"/api/customers/{r.json()['id']}",
        json={"name": "Demo GmbH", "vat_id": "DE136695976", "tax_number": "12/345/67890"},
    )
    assert ok.json()["sanity_warnings"] == []


def test_settings_warn_about_iban_and_ids_and_still_save(boss_client):
    r = boss_client.put(
        "/api/settings", json={"iban": "DE89370400440532013001", "vat_id": "DE136695976", "bic": "COBADEFFXXX"}
    )
    assert r.status_code == 200
    assert codes(r.json()) == ["settings.iban_invalid"]
    assert boss_client.get("/api/settings").json()["iban"] == "DE89370400440532013001"
    assert "sanity_warnings" not in boss_client.get("/api/settings").json()


def test_invoice_dates_totals_and_finalize(boss_client):
    c = boss_client.post("/api/customers", json={"name": "Demo GmbH"}).json()
    body = {
        "customer_id": c["id"],
        "date": day(60),
        "due_date": day(30),
        "lines": [{"description": "Work", "qty": 1, "price": 0}],
    }
    r = boss_client.post("/api/invoices", json=body)
    assert r.status_code == 200
    assert sorted(codes(r.json())) == ["invoice.date.far_future", "invoice.due_date_before_date", "invoice.total_zero"]
    inv = r.json()
    # re-saving without touching the date does not repeat the date warning
    again = boss_client.put(f"/api/invoices/{inv['id']}", json={**body, "notes": "x"})
    assert "invoice.date.far_future" not in codes(again.json())
    assert "invoice.total_zero" in codes(again.json())
    # finalize: no tax number or VAT id in the settings yet
    fin = boss_client.post(f"/api/invoices/{inv['id']}/finalize")
    assert fin.status_code == 200
    assert "invoice.seller_tax_id_missing" in codes(fin.json())
    boss_client.put("/api/settings", json={"tax_number": "12/345/67890"})
    fin2 = boss_client.post(f"/api/invoices/{inv['id']}/finalize")
    assert "invoice.seller_tax_id_missing" not in codes(fin2.json())


def test_clean_invoice_has_no_warnings(boss_client):
    boss_client.put("/api/settings", json={"tax_number": "12/345/67890"})
    c = boss_client.post("/api/customers", json={"name": "Demo GmbH"}).json()
    r = boss_client.post(
        "/api/invoices",
        json={"customer_id": c["id"], "date": day(0), "lines": [{"description": "Work", "qty": 2, "price": 50}]},
    )
    assert r.json()["sanity_warnings"] == []


def test_expense_warnings(boss_client):
    r = boss_client.post(
        "/api/expenses", json={"date": day(10), "net": 100, "vat": 5, "vat_rate": 19, "vendor": "Cafe Demo"}
    )
    assert r.status_code == 200
    assert sorted(codes(r.json())) == ["expense.date.far_future", "expense.vat_mismatch"]
    old = boss_client.post("/api/expenses", json={"date": day(-OLD_DAYS - 1), "net": 10, "vat": 1.9, "vat_rate": 19})
    assert codes(old.json()) == ["expense.date.far_past"]


def test_a_failing_rule_never_breaks_a_save(boss_client, app, monkeypatch):
    from permita38.core.service import sanity_rules

    def boom(*a, **k):
        raise RuntimeError("bug in a rule")

    monkeypatch.setattr(sanity_rules, "check_expense", boom)
    r = boss_client.post("/api/expenses", json={"date": day(0), "net": 10, "vat": 1.9, "vat_rate": 19})
    assert r.status_code == 200 and r.json()["sanity_warnings"] == []


# ---------------------------------------------------------------------------------------------- response-only fields
FAKE = [{"code": "stale.warning", "severity": "warning", "message": "old"}]


def _raw(app, coll, rid):
    return app.state.services.store.get(coll, rid)


def _no_warning_keys(row):
    assert "sanity_warnings" not in row and "planning_warnings" not in row, row


def test_warning_fields_in_a_request_are_never_stored(app, boss_client):
    junk = {"sanity_warnings": FAKE, "planning_warnings": FAKE}
    cust = boss_client.post("/api/customers", json={"name": "Demo GmbH"}).json()
    bodies = {
        "entries": {"date": last_weekday(1), "minutes": 60},
        "expenses": {"date": last_weekday(1), "vendor": "Demo", "net": 10, "vat": 1.9, "vat_rate": 19},
        "offers": {"customer_id": cust["id"], "lines": [{"description": "x", "qty": 1, "unit": "h", "price": 10}]},
        "invoices": {"customer_id": cust["id"], "lines": [{"description": "x", "qty": 1, "unit": "h", "price": 10}]},
    }
    for coll, body in bodies.items():
        created = boss_client.post(f"/api/{coll}", json={**body, **junk})
        assert created.status_code == 200, created.text
        rid = created.json()["id"]
        _no_warning_keys(_raw(app, coll, rid))
        updated = boss_client.put(f"/api/{coll}/{rid}", json={**body, **junk})
        assert updated.status_code == 200, updated.text
        _no_warning_keys(_raw(app, coll, rid))
        got = boss_client.get(f"/api/{coll}/{rid}").json()
        _no_warning_keys(got)
        assert "stale.warning" not in [w["code"] for w in updated.json().get("sanity_warnings") or []]


def test_warning_fields_in_a_settings_put_are_never_stored(app, boss_client):
    r = boss_client.put("/api/settings", json={"company_name": "Demo", "sanity_warnings": FAKE})
    assert r.status_code == 200, r.text
    _no_warning_keys(app.state.services.store.settings())
    assert "stale.warning" not in [w["code"] for w in r.json().get("sanity_warnings") or []]
    again = boss_client.put("/api/settings", json={"company_name": "Demo 2"})
    _no_warning_keys(app.state.services.store.settings())
    assert "stale.warning" not in [w["code"] for w in again.json().get("sanity_warnings") or []]


def test_itemised_expense_has_no_vat_warnings_and_stores_none(boss_client):
    body = {
        "date": day(0),
        "vendor": "Hotel Demo",
        "lines": [
            {"description": "Room", "gross": 160, "vat_rate": 7},
            {"description": "Breakfast", "gross": 30, "vat_rate": 19},
            {"description": "City tax", "gross": 6, "vat_rate": 0},
        ],
    }
    r = boss_client.post("/api/expenses", json=body)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["vat_rate"] is None and saved["lines"] and codes(saved) == []
    fetched = boss_client.get(f"/api/expenses/{saved['id']}").json()
    assert "sanity_warnings" not in fetched
    assert all("sanity_warnings" not in e for e in boss_client.get("/api/expenses").json())
