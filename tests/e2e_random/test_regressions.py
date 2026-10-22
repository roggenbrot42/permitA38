"""Regressions for genuine bugs found by the random tests. Tests still marked strict xfail pin known backend
bugs: they flip to a failure as soon as the bug is fixed, so the marker gets removed then."""

import pytest

pytestmark = pytest.mark.e2e_random


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


@pytest.mark.xfail(
    strict=True,
    reason="permita38/core/calc.py doc_totals/finance use round() on binary floats: 19% of "
    "621.50 is booked as 118.08 although the editor shows 118,09 (half-up); use Decimal ROUND_HALF_UP",
)
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


@pytest.mark.xfail(
    strict=True,
    reason="permita38/core/models.py: dates and numbers are not validated (plain str/float), "
    "so one bad record makes /dashboard, /forecast or /finance answer 500 for everybody (and, via sync, "
    "on every device). Validate (datetime.date, bounded numbers) and make calc.py skip bad data.",
)
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
    if r.status_code >= 400:
        assert r.status_code < 500  # rejected up front: the right behaviour
        return
    # accepted -> the aggregate endpoints must still answer
    assert api.http.get(breaks).status_code < 500


def test_negative_expense_does_not_break_money_page(make_page, live_server, api):
    from datetime import date

    api.setup_boss()
    api.post("/expenses", {"date": date.today().isoformat(), "net": -5000.0, "vat": 0, "vendor": "Gutschrift"})
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
    page.click("[data-a=addLine]")
    page.click("[data-a=addLine]")
    assert page.locator("input[data-k=description]").count() == 3
    page.click("[data-a=delLine][data-i='1']")
    assert page.locator("input[data-k=description]").count() == 2
    watch.check()
