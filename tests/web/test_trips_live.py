"""Business trips against the real server: boss, employee and accountant (demo data only)."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_impressum_prefill import PASSWORD, Server  # noqa: E402  (shared harness)


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
    """The employee creates a trip in the new-trip dialog, adds the mileage on the expenses step and ticks breakfast
    on the review step. Returns the trip id."""
    pg.goto(srv.base + "/#/trips")
    pg.wait_for_selector("[data-a=newTrip]")
    expect(pg.locator("main")).not_to_contain_text("Boss only trip")  # the boss's trip is not visible
    pg.click("[data-a=newTrip]")
    pg.wait_for_selector("#f_purpose")
    pg.fill("#f_purpose", "Demo workshop")
    pg.fill("#f_start", "2026-11-09T08:00")
    pg.fill("#f_end", "2026-11-11T18:00")
    pg.fill("#f_origin", "Berlin")
    pg.fill("#f_destination", "Hamburg")
    pg.click("#m-save")
    pg.wait_for_selector("#t_step tr[data-row]")  # a new trip opens on the itinerary
    tid = pg.evaluate("location.hash").split("/")[2]
    assert pg.evaluate("location.hash").endswith("/itinerary")
    expect(pg.locator("#t_step tr[data-row]")).to_have_count(3)  # departure, stay, return
    pg.click("[data-a=tripMove][data-step=expenses]")
    pg.wait_for_selector("#t_add_item")
    pg.select_option("#t_add_item", "car")
    pg.fill("#f_km", "237.5")
    pg.click("#m-save")
    expect(pg.locator("#t_step")).to_contain_text("237,5 km")
    pg.click("[data-a=tripMove][data-step=review]")
    pg.wait_for_selector("#t_breakdown")
    expect(pg.locator("#t_breakdown")).to_contain_text("56,00")
    expect(pg.locator("#t_breakdown")).to_contain_text("71,25")
    pg.check('tr[data-day="2026-11-10"] [data-f=breakfast]')
    expect(pg.locator("#t_breakdown")).to_contain_text("50,40")
    expect(pg.locator("#t_breakdown")).to_contain_text("71,25")
    pg.click("[data-a=tripMove][data-step=expenses]")  # Back saves the review step
    pg.wait_for_selector("#t_add_item")
    return tid


def test_trip_lifecycle_across_roles(browser, srv):
    # --- employee creates the trip and sees the live figures
    ctx, pg, problems, dialogs = open_as(browser, srv, "employee")
    pg.goto(srv.base + "/")
    tid = employee_trip_via_ui(pg, srv)
    pg.goto(srv.base + f"/#/trips/{tid}/review")
    pg.wait_for_selector("#t_checklist .check-item")
    line = pg.locator("#t_checklist li.check-item", has_text="Transportation")
    expect(line).to_contain_text("Added")  # the mileage covers it
    accommodation = pg.locator("#t_checklist li.check-item", has_text="Hotel / accommodation")
    expect(accommodation).to_contain_text("Open")
    assert pg.locator("#t_step [data-a=editExpense]").count() == 0  # employees have no expenses right
    ctx.close()

    # --- the boss adds the hotel bill to that trip through 'Add bill' on the checklist
    ctx, pg, problems_b, _ = open_as(browser, srv, "boss")
    pg.goto(srv.base + "/#/trips")
    pg.wait_for_selector("a[href^='#/trips/']")
    expect(pg.locator("main")).to_contain_text("Demo workshop")
    pg.goto(srv.base + f"/#/trips/{tid}/review")
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
    ctx.close()

    # --- employee finalizes with open lines: 409 -> confirm -> force, then downloads the statement
    ctx, pg, problems, dialogs = open_as(browser, srv, "employee")
    pg.goto(srv.base + "/#/trips")
    pg.wait_for_selector("a[href^='#/trips/']")
    assert pg.locator("a[href^='#/trips/']").count() == 1  # only the own trip
    pg.goto(srv.base + f"/#/trips/{tid}/review")
    pg.wait_for_selector("[data-a=tripFinalize]")
    pg.click("[data-a=tripFinalize]")
    expect(pg.locator("[data-a=tripFinalize]")).to_have_count(0)
    assert len(dialogs) == 1 and "Checklist has open items" in dialogs[0], dialogs
    expect(pg.locator("main .banner.info")).to_contain_text("final")
    href = pg.locator("#t_step a", has_text="Statement (PDF)").evaluate("a => a.href")  # absolute
    res = ctx.request.get(href)
    assert res.status == 200
    assert res.headers["content-type"].startswith("application/pdf")
    assert res.body()[:4] == b"%PDF"
    assert problems == []
    ctx.close()

    # --- accountant: reads the employee's trip, nothing to write
    ctx, pg, problems, _ = open_as(browser, srv, "accountant")
    pg.goto(srv.base + "/#/trips")
    pg.wait_for_selector("a[href^='#/trips/']")
    expect(pg.locator("main")).to_contain_text("Demo workshop")
    expect(pg.locator("main")).to_contain_text("Boss only trip")
    expect(pg.locator("[data-a=newTrip]")).to_have_count(0)
    pg.locator("tr", has_text="Demo workshop").locator("a").first.click()
    pg.wait_for_selector("#t_step")
    pg.goto(srv.base + f"/#/trips/{tid}/review")
    pg.wait_for_selector("#t_breakdown")
    expect(pg.locator("#t_breakdown")).to_contain_text("50,40")
    for action in ("tripFinalize", "tripDelete", "tripReopen", "editExpense"):
        assert pg.locator(f"#t_step [data-a={action}]").count() == 0, action
    assert pg.locator("#t_step a", has_text="Statement (PDF)").count() == 1
    assert problems == []
    ctx.close()
