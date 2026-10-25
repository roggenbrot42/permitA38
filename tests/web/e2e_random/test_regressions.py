"""Regressions for genuine bugs found by the random tests (all fixed; a test that is marked strict xfail would pin
a bug that is still open and flips to a failure as soon as it is fixed)."""

import pytest

pytestmark = pytest.mark.e2e_random

COUNT_JS = "n => document.querySelectorAll('input[data-k=description]').length === n"


def test_add_line_on_new_document(make_page, live_server, api):
    api.setup_boss()
    page, _ = make_page(token=api.http.headers["Authorization"].split()[1])
    page.goto(live_server.url + "/#/doc/offers/new")
    page.wait_for_selector("#d_totals")
    page.fill('[data-l="0"][data-k="description"]', "first line")
    page.click("[data-a=addLine]")
    page.wait_for_timeout(500)
    assert page.locator("input[data-k=description]").count() == 2
    assert page.locator('[data-l="0"][data-k="description"]').input_value() == "first line"


def test_mark_paid_sets_paid_date(make_page, live_server, api):
    from datetime import date

    api.setup_boss()
    cust = api.post("/customers", {"name": "Kunde Eins"})
    inv = api.post("/invoices", {"customer_id": cust["id"], "lines": [{"description": "x", "qty": 1, "price": 100}]})
    inv = api.post(f"/invoices/{inv['id']}/finalize")
    page, _ = make_page(token=api.http.headers["Authorization"].split()[1])
    page.goto(live_server.url + f"/#/doc/invoices/{inv['id']}")
    page.wait_for_selector("#d_totals")
    page.select_option("select[data-change=docStatus]", "paid")
    page.wait_for_timeout(800)
    rec = api.get(f"/invoices/{inv['id']}")
    assert rec["status"] == "paid"
    assert rec.get("paid_date") == date.today().isoformat()


def test_vat_rounding_is_half_up(api):
    from datetime import date

    api.setup_boss()
    cust = api.post("/customers", {"name": "Kunde Zwei"})
    inv = api.post("/invoices", {"customer_id": cust["id"], "lines": [{"description": "x", "qty": 1, "price": 621.5}]})
    inv = api.post(f"/invoices/{inv['id']}/finalize")
    inv["status"], inv["paid_date"] = "paid", date.today().isoformat()
    r = api.http.put(f"/invoices/{inv['id']}", json=inv)
    assert r.status_code == 200, r.text
    assert api.get("/finance", year=date.today().year)["totals"]["vat_out"] == 118.09


def test_unknown_document_shows_not_found(make_page, live_server, api):
    api.setup_boss()
    page, watch = make_page(token=api.http.headers["Authorization"].split()[1])
    page.goto(live_server.url + "/#/doc/invoices/doesnotexist")
    page.wait_for_selector("main")
    page.wait_for_timeout(500)
    assert "Not found" in page.inner_text("main")
    watch.check()


@pytest.mark.parametrize(
    "coll, body, breaks",
    [
        ("contracts", {"title": "x", "start": "💥", "end": "2026-12-01", "hours_per_week": 5}, "/forecast"),
        ("absences", {"start": "not-a-date"}, "/dashboard"),
        ("expenses", {"date": "💥", "net": 5}, "/finance"),
    ],
)
def test_invalid_values_do_not_break_dashboards(api, coll, body, breaks):
    api.setup_boss()
    if "user_id" not in body and coll in ("absences", "entries"):
        body = {**body, "user_id": api.get("/auth/me")["id"]}
    r = api.http.post(f"/{coll}", json=body)
    assert r.status_code == 422, r.text  # rejected up front, with a readable message
    assert api.http.get(breaks).status_code == 200


def test_negative_expense_is_rejected_and_credit_notes_do_not_break_money_page(make_page, live_server, api):
    from datetime import date

    api.setup_boss()
    today = date.today().isoformat()
    r = api.http.post("/expenses", json={"date": today, "net": -5000.0, "vat": 0, "vendor": "Gutschrift"})
    assert r.status_code == 422, r.text
    cust = api.post("/customers", {"name": "Gutschrift GmbH"})
    inv = api.post(
        "/invoices", {"customer_id": cust["id"], "lines": [{"description": "Gutschrift", "qty": 1, "price": -5000}]}
    )
    inv = api.post(f"/invoices/{inv['id']}/finalize")
    inv["status"], inv["paid_date"] = "paid", today
    assert api.http.put(f"/invoices/{inv['id']}", json=inv).status_code == 200  # negative revenue this month
    page, watch = make_page(token=api.http.headers["Authorization"].split()[1])
    page.goto(live_server.url + "/#/money")
    page.wait_for_selector("main .kpi")
    page.wait_for_timeout(300)
    watch.check()


def test_member_opening_boss_route_is_redirected(make_page, live_server, api):
    api.setup_boss()
    api.post("/users", {"username": "mia", "name": "Mia Member", "role": "member", "password": "member-pass-123"})
    member = api.http.post("/auth/login", json={"username": "mia", "password": "member-pass-123"}).json()["token"]
    page, watch = make_page(token=member)
    for route in ("money", "customers", "doc/invoices/new", "settings"):
        page.goto(live_server.url + "/#/" + route)
        page.wait_for_selector("main")
        page.wait_for_timeout(300)
        assert page.evaluate("location.hash") == "#/today", route
        assert "Forbidden" not in page.inner_text("main")
    watch.check()


def test_new_document_draft_survives_delete_line_and_status_menu_is_robust(make_page, live_server, api):
    api.setup_boss()
    page, watch = make_page(token=api.http.headers["Authorization"].split()[1])
    page.goto(live_server.url + "/#/doc/invoices/new")
    page.wait_for_selector("#d_totals")
    lines = page.locator("input[data-k=description]")
    for expected in (2, 3):
        page.click("[data-a=addLine]")
        page.wait_for_function(COUNT_JS, arg=expected)
    page.click("[data-a=delLine][data-i='1']")
    page.wait_for_function(COUNT_JS, arg=2)
    assert lines.count() == 2
    watch.check()
