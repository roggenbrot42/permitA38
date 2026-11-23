"""Bills opened from a trip page (#109 preselected trip, #110 edit a bill booked on a planned item). Real server, demo data."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_impressum_prefill import Server  # noqa: E402  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    h = {"Authorization": "Bearer " + s.token}
    ids = []
    for purpose, start, end in (
        ("Demo trip one", "2026-11-02T08:00", "2026-11-03T18:00"),
        ("Demo trip two", "2026-11-09T08:00", "2026-11-10T18:00"),
    ):
        r = httpx.post(s.base + "/api/trips", headers=h, json={"purpose": purpose, "start": start, "end": end})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    # trip two plans a hotel (item id fixed so the test can find it)
    for n, tid in enumerate(ids):
        r = httpx.get(s.base + f"/api/trips/{tid}", headers=h)
        body = {k: v for k, v in r.json().items() if k in ("purpose", "start", "end")}
        day = r.json()["start"][:10]
        body["items"] = [{"id": f"ithotel{n}", "date": day, "category": "hotel", "text": "Demo hotel", "expected": 100}]
        r = httpx.put(s.base + f"/api/trips/{tid}", headers=h, json=body)
        assert r.status_code == 200, r.text
    s.trip_ids = ids
    yield s
    s.stop()


def open_app(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    ctx.add_cookies([{"name": "authorization", "value": srv.token, "url": srv.base}])
    pg = ctx.new_page()
    problems = []
    pg.on("pageerror", lambda e: problems.append(str(e)))
    return ctx, pg, problems


def open_expenses_step(pg, srv, tid):
    pg.goto(srv.base + f"/#/trips/{tid}/expenses")
    pg.wait_for_selector("#t_add_item")


@pytest.mark.parametrize("n", [0, 1])
def test_add_bill_buttons_preselect_the_open_trip(browser, srv, n):
    ctx, pg, problems = open_app(browser, srv)
    try:
        tid = srv.trip_ids[n]
        open_expenses_step(pg, srv, tid)
        pg.locator("#t_step tr[data-item] [data-a=editExpense]").first.click()
        pg.wait_for_selector("#f_trip_id")
        assert pg.input_value("#f_trip_id") == tid
        assert pg.input_value("#f_trip_item_id") == f"ithotel{n}"
        expect(pg.locator("#f_trip_id")).to_be_disabled()
        expect(pg.locator("#f_trip_item_id")).to_be_enabled()
        pg.keyboard.press("Escape")
        pg.wait_for_selector("#f_trip_id", state="detached")
        pg.goto(srv.base + f"/#/trips/{tid}/review")
        pg.wait_for_selector("#t_checklist .trip-check")
        pg.locator(".trip-check [data-a=editExpense]").first.click()
        pg.wait_for_selector("#f_trip_id")
        assert pg.input_value("#f_trip_id") == tid
        expect(pg.locator("#f_trip_id")).to_be_disabled()
        assert not problems
    finally:
        ctx.close()


def test_expenses_page_keeps_the_trip_select_free(browser, srv):
    ctx, pg, _ = open_app(browser, srv)
    try:
        pg.goto(srv.base + "/#/expenses")
        pg.click("[data-a=editExpense]")
        pg.wait_for_selector("#f_trip_id")
        expect(pg.locator("#f_trip_id")).to_be_enabled()
    finally:
        ctx.close()


def test_bill_on_a_planned_item_can_be_edited_again(browser, srv):
    ctx, pg, problems = open_app(browser, srv)
    try:
        t2 = srv.trip_ids[1]
        open_expenses_step(pg, srv, t2)
        pg.locator("#t_step tr[data-item] [data-a=editExpense]").first.click()
        pg.wait_for_selector("#f_trip_id")
        pg.fill("#f_vendor", "Demo Hotel")
        pg.fill("#f_gross", "119")
        pg.click("#m-save")
        edit = pg.locator("#t_step tr[data-item='ithotel1'] [data-a=editExpense][data-id]")
        expect(edit).to_have_count(1)
        expect(edit).to_have_attribute("aria-label", "Edit bill Demo Hotel")
        edit.click()
        pg.wait_for_selector("#f_vendor")
        assert pg.input_value("#f_vendor") == "Demo Hotel"
        assert pg.input_value("#f_trip_id") == t2
        expect(pg.locator("#f_trip_id")).to_be_disabled()
        pg.fill("#f_vendor", "Demo Inn")
        pg.click("#m-save")
        expect(edit).to_have_attribute("aria-label", "Edit bill Demo Inn")
        assert not problems
    finally:
        ctx.close()
