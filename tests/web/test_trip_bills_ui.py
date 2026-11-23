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
    h = {"Authorization": "Bearer " + srv.token}
    t2 = srv.trip_ids[1]
    r = httpx.post(
        srv.base + "/api/expenses",
        headers=h,
        json={"date": "2026-11-09", "vendor": "Demo Cafe", "category": "Other", "net": 10, "vat": 0, "trip_id": t2},
    )
    assert r.status_code == 200, r.text
    bill_id = r.json()["id"]
    ctx, pg, _ = open_app(browser, srv)
    try:
        pg.goto(srv.base + "/#/expenses")
        pg.click("[data-a=editExpense]")
        pg.wait_for_selector("#f_trip_id")
        expect(pg.locator("#f_trip_id")).to_be_enabled()
        pg.keyboard.press("Escape")
        pg.wait_for_selector("#f_trip_id", state="detached")
        pg.locator(f"[data-a=editExpense][data-id='{bill_id}']").click()
        pg.wait_for_selector("#f_trip_id")
        assert pg.input_value("#f_trip_id") == t2
        expect(pg.locator("#f_trip_id")).to_be_enabled()
    finally:
        ctx.close()


def test_trip_option_labels_are_unique(browser, srv):
    ctx, pg, _ = open_app(browser, srv)
    try:
        pg.goto(srv.base + "/#/expenses")
        pg.click("[data-a=editExpense]")
        pg.wait_for_selector("#f_trip_id")
        labels = pg.locator("#f_trip_id option").all_inner_texts()
        assert len(labels) == len(set(labels)) >= 2
        assert any("02.11.2026–03.11.2026" in x and "Demo trip one" in x and "Germany" in x for x in labels)
    finally:
        ctx.close()


def test_final_trip_shows_no_bill_buttons(browser, srv):
    h = {"Authorization": "Bearer " + srv.token}
    tid = srv.trip_ids[1]
    t = httpx.get(srv.base + f"/api/trips/{tid}", headers=h).json()
    body = {k: t[k] for k in ("purpose", "start", "end", "items") if k in t}
    r = httpx.put(srv.base + f"/api/trips/{tid}?force=true", headers=h, json={**body, "status": "final"})
    assert r.status_code == 200, r.text
    ctx, pg, _ = open_app(browser, srv)
    try:
        pg.goto(srv.base + f"/#/trips/{tid}/expenses")
        pg.wait_for_selector("#t_step tr[data-item]")
        expect(pg.locator("#t_step [data-a=editExpense]")).to_have_count(0)
    finally:
        ctx.close()


def test_superseded_trip_render_does_not_hijack_the_open_trip(browser, srv):
    """A slow render of trip one that finishes after trip two is shown must not make trip one the open trip page."""
    t1, t2 = srv.trip_ids
    ctx, pg, problems = open_app(browser, srv)
    try:
        open_expenses_step(pg, srv, t2)
        held = []
        pg.route(f"**/api/trips/{t1}", lambda route: held.append(route))   # hold the GET of trip one until trip two is shown
        pg.evaluate(f"location.hash = '#/trips/{t1}/expenses'")
        pg.wait_for_timeout(200)
        with pg.expect_response(f"**/api/trips/{t2}"):   # trip two is rendered again, after trip one's render started
            pg.evaluate(f"location.hash = '#/trips/{t2}/expenses'")
        pg.wait_for_timeout(300)
        assert len(held) == 1
        with pg.expect_response(f"**/api/trips/{t1}"):
            held[0].continue_()
        pg.wait_for_timeout(500)   # the slow trip one render has finished by now
        # an inline edit writes through the open trip page state: it must hit trip two, not trip one
        pg.fill("[data-item-exp=ithotel1]", "250")
        pg.press("[data-item-exp=ithotel1]", "Tab")
        h = {"Authorization": "Bearer " + srv.token}
        for _ in range(30):
            if httpx.get(srv.base + f"/api/trips/{t2}", headers=h).json()["items"][0]["expected"] != 100:
                break
            pg.wait_for_timeout(100)
        exp = {t: httpx.get(srv.base + f"/api/trips/{t}", headers=h).json()["items"][0]["expected"] for t in (t1, t2)}
        assert exp == {t1: 100, t2: 250}
        btn = pg.locator("#t_step tr[data-item] [data-a=editExpense]").first
        expect(btn).to_have_attribute("data-trip", t2)
        btn.click()
        pg.wait_for_selector("#f_trip_id")
        assert pg.input_value("#f_trip_id") == t2
        pg.keyboard.press("Escape")
        pg.wait_for_selector("#f_trip_id", state="detached")
        assert not problems
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
