"""Business trips against the real server: boss, employee and accountant (demo data only)."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402,F401  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    h = {"Authorization": "Bearer " + s.token}
    for role in ("employee", "accountant"):
        r = httpx.post(
            s.base + "/api/users",
            headers=h,
            json={"username": role, "name": role.title(), "role": role, "password": PASSWORD},
        )
        assert r.status_code == 200, r.text
    # the boss's own trip: nobody else may see it
    r = httpx.post(
        s.base + "/api/trips",
        headers=h,
        json={"purpose": "Boss only trip", "start": "2026-12-01T08:00", "end": "2026-12-01T20:00"},
    )
    assert r.status_code == 200, r.text
    yield s
    s.stop()


def open_as(browser, srv, username):
    r = httpx.post(srv.base + "/api/auth/login", json={"username": username, "password": PASSWORD})
    token = r.json()["token"]
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    ctx.add_cookies([{"name": "authorization", "value": token, "url": srv.base}])
    pg = ctx.new_page()
    problems: list[str] = []
    dialogs: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    return ctx, pg, problems, dialogs


def employee_trip_via_ui(pg, srv):
    pg.goto(srv.base + "/#/trips")
    pg.wait_for_selector("[data-a=newTrip]")
    expect(pg.locator("main")).not_to_contain_text("Boss only trip")  # the boss's trip is not visible
    pg.click("[data-a=newTrip]")
    pg.wait_for_selector("#t_purpose")
    pg.fill("#t_start", "2026-11-09T08:00")
    pg.fill("#t_end", "2026-11-11T18:00")
    pg.fill("#t_purpose", "Demo workshop")
    expect(pg.locator("#t_breakdown")).to_contain_text("56,00")
    pg.click("#t_km_add")
    pg.fill("#t_km [data-f=from]", "Berlin")
    pg.fill("#t_km [data-f=to]", "Hamburg")
    pg.fill("#t_km [data-f=km]", "237.5")
    expect(pg.locator("#t_breakdown")).to_contain_text("71,25")
    pg.check('tr[data-day="2026-11-10"] [data-f=breakfast]')
    expect(pg.locator("#t_breakdown")).to_contain_text("50,40")
    expect(pg.locator("#t_breakdown")).to_contain_text("71,25")
    pg.click("[data-a=tripSave]")
    pg.wait_for_selector("#t_checklist .check-item")


def test_trip_lifecycle_across_roles(browser, srv):
    # --- employee creates the trip and sees the live figures
    ctx, pg, problems, dialogs = open_as(browser, srv, "employee")
    pg.goto(srv.base + "/")
    employee_trip_via_ui(pg, srv)
    line = pg.locator("#t_checklist li.check-item", has_text="Transportation")
    expect(line).to_contain_text("Added")  # the mileage covers it
    accommodation = pg.locator("#t_checklist li.check-item", has_text="Hotel / accommodation")
    expect(accommodation).to_contain_text("Open")
    assert pg.locator("#modal-root [data-a=editExpense]").count() == 0  # employees have no expenses right
    ctx.close()

    # --- the boss adds the hotel bill to that trip through 'Add bill'
    ctx, pg, problems_b, _ = open_as(browser, srv, "boss")
    pg.goto(srv.base + "/#/trips")
    pg.wait_for_selector("button[data-a=editTrip]")
    expect(pg.locator("main")).to_contain_text("Demo workshop")
    pg.locator("tr", has_text="Demo workshop").locator("button[data-a=editTrip]").click()
    pg.wait_for_selector("#t_checklist .check-item")
    accommodation = pg.locator("#t_checklist li.check-item", has_text="Hotel / accommodation")
    accommodation.locator("button", has_text="Add bill").click()
    pg.wait_for_selector("#f_category")
    assert pg.input_value("#f_category") == "Accommodation"
    assert pg.input_value("#f_trip_id")
    pg.fill("#f_vendor", "Demo Hotel")
    pg.fill("#f_gross", "119")
    pg.click("#m-save")
    # the dialog returns to the trip and the line flipped
    accommodation = pg.locator("#t_checklist li.check-item", has_text="Hotel / accommodation")
    expect(accommodation).to_contain_text("Added")
    expect(pg.locator("#modal-root")).to_contain_text("Demo Hotel")
    ctx.close()

    # --- employee finalizes with open lines: 409 -> confirm -> force, then downloads the statement
    ctx, pg, problems, dialogs = open_as(browser, srv, "employee")
    pg.goto(srv.base + "/#/trips")
    pg.wait_for_selector("button[data-a=editTrip]")
    assert pg.locator("button[data-a=editTrip]").count() == 1  # only the own trip
    pg.click("button[data-a=editTrip]")
    pg.wait_for_selector("[data-a=tripFinalize]")
    pg.click("[data-a=tripFinalize]")
    expect(pg.locator("[data-a=tripFinalize]")).to_have_count(0)
    assert len(dialogs) == 1 and "Checklist has open items" in dialogs[0], dialogs
    expect(pg.locator("#modal-root .banner")).to_contain_text("final")
    href = pg.locator("#modal-root a", has_text="Statement (PDF)").evaluate("a => a.href")  # absolute
    res = ctx.request.get(href)
    assert res.status == 200
    assert res.headers["content-type"].startswith("application/pdf")
    assert res.body()[:4] == b"%PDF"
    assert problems == []
    ctx.close()

    # --- accountant: reads the employee's trip, nothing to write
    ctx, pg, problems, _ = open_as(browser, srv, "accountant")
    pg.goto(srv.base + "/#/trips")
    pg.wait_for_selector("button[data-a=editTrip]")
    expect(pg.locator("main")).to_contain_text("Demo workshop")
    expect(pg.locator("main")).to_contain_text("Boss only trip")
    expect(pg.locator("[data-a=newTrip]")).to_have_count(0)
    pg.locator("tr", has_text="Demo workshop").locator("button[data-a=editTrip]").click()
    pg.wait_for_selector("#t_purpose")
    assert pg.locator("#t_purpose").is_disabled()
    expect(pg.locator("#t_breakdown")).to_contain_text("50,40")
    for action in ("tripSave", "tripFinalize", "tripDelete", "tripReopen", "editExpense"):
        assert pg.locator(f"#modal-root [data-a={action}]").count() == 0, action
    assert pg.locator("#modal-root a", has_text="Statement (PDF)").count() == 1
    assert problems == []
    ctx.close()
