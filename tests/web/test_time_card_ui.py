"""Browser tests of the time tracking row on the Today page (docs/specs/time-tracking-element.md). Live server, fake names."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server  # noqa: E402,F401  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def api(srv, method, path, body=None):
    r = httpx.request(method, srv.base + "/api/" + path, json=body, headers={"Authorization": "Bearer " + srv.token})
    assert r.status_code < 300, r.text
    return r.json()


@pytest.fixture
def pg(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    page = ctx.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    page.on("dialog", lambda d: (problems.append("dialog: " + d.message), d.dismiss()))
    page.goto(srv.base + "/#/today")
    page.wait_for_selector("#t_proj")
    yield page
    assert problems == []
    ctx.close()


def test_default_row_is_manual_with_description_project_task_and_add(pg, srv):
    assert pg.is_visible("#t_note") and pg.is_visible("#t_proj") and pg.is_visible("#t_task")
    assert pg.get_attribute("[data-a=timeMode][data-mode=manual]", "aria-pressed") == "true"
    assert pg.get_attribute("[data-a=timeMode][data-mode=tracker]", "aria-pressed") == "false"
    for sel in ("#t_date", "#t_start", "#t_end", "#t_dur"):
        assert pg.is_visible(sel), sel
    assert pg.is_visible("[data-a=timerAdd]")
    assert pg.locator("[data-a=timerStart]").count() == 0  # Start belongs to tracker mode


def test_project_picker_lists_non_archived_projects_and_matches_typed_name(pg, srv):
    api(srv, "POST", "projects", {"name": "Projekt Alpha", "rate": 95})
    api(srv, "POST", "projects", {"name": "Projekt Archiv", "status": "archived"})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    options = pg.locator("#t_proj_list option").evaluate_all("els => els.map(e => e.value)")
    assert "Projekt Alpha" in options
    assert "Projekt Archiv" not in options


def test_manual_add_computes_duration_and_saves_entry(pg, srv):
    api(srv, "POST", "projects", {"name": "Projekt Beta"})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    pg.fill("#t_start", "09:00")
    pg.dispatch_event("#t_start", "change")
    pg.fill("#t_end", "10:30")
    pg.dispatch_event("#t_end", "change")
    assert pg.input_value("#t_dur") == "1:30"  # hm() format, as elsewhere in the app
    pg.fill("#t_note", "Beispiel Kick-off")
    pg.fill("#t_proj", "Projekt Beta")
    pg.dispatch_event("#t_proj", "change")
    pg.click("[data-a=timerAdd]")
    pg.wait_for_function("() => document.querySelector('#t_note') && document.querySelector('#t_note').value === ''")
    entries = api(srv, "GET", "entries")
    saved = [e for e in entries if e.get("note") == "Beispiel Kick-off"]
    assert len(saved) == 1
    assert saved[0]["minutes"] == 90
    assert saved[0]["start"] == "09:00" and saved[0]["end"] == "10:30"
    assert saved[0]["project_id"] is not None


def test_tracker_mode_starts_and_stops_a_timer_on_the_project(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Gamma"})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    pg.click("[data-a=timeMode][data-mode=tracker]")
    assert pg.get_attribute("[data-a=timeMode][data-mode=tracker]", "aria-pressed") == "true"
    assert pg.locator("#t_start").count() == 0
    pg.fill("#t_note", "Beispiel Tracker")
    pg.fill("#t_proj", "Projekt Gamma")
    pg.dispatch_event("#t_proj", "change")
    pg.click("[data-a=timerStart]")
    pg.wait_for_selector("[data-a=timerStop]")
    timer = api(srv, "GET", "timer")
    assert timer["running"] is True and timer["project_id"] == project["id"]
    pg.click("[data-a=timerStop]")
    pg.wait_for_selector("[data-a=timerStart]")
    assert api(srv, "GET", "timer")["running"] is False
    assert any(
        e.get("note") == "Beispiel Tracker" and e.get("project_id") == project["id"] for e in api(srv, "GET", "entries")
    )


def test_clock_toggles_to_money_on_a_project_with_a_rate(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Delta", "rate": 95})
    started = api(srv, "POST", "timer/start", {"project_id": project["id"], "note": "Beispiel Geld"})["started"]
    pg.reload()
    pg.wait_for_selector("#clock")
    # the browser clock is pinned one hour after the start: elapsed is exactly 3600 s
    pg.clock.set_fixed_time(started + 3600)  # seconds
    pg.wait_for_function("() => document.querySelector('#clock').textContent === '01:00:00'")
    clock = pg.locator("#clock")
    clock.click()
    # 95 EUR/h x 3600 s = 95,00 EUR (de-DE format, non-breaking space before the euro sign)
    assert clock.inner_text().strip() == "95,00\u00a0€"
    clock.click()
    assert clock.inner_text().strip() == "01:00:00"


def test_running_timer_in_manual_mode_shows_clock_and_stops(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Zeta"})
    api(srv, "POST", "timer/start", {"project_id": project["id"], "note": "Beispiel Laufend"})
    pg.reload()
    pg.wait_for_selector("#clock")
    assert pg.get_attribute("[data-a=timeMode][data-mode=manual]", "aria-pressed") == "true"
    assert pg.is_visible("#clock")
    assert pg.is_visible("[data-a=timerStop]")
    assert pg.locator("[data-a=timerStart]").count() == 0
    assert pg.locator("#t_proj").input_value() == "Projekt Zeta"
    pg.click("[data-a=timerStop]")
    pg.wait_for_selector("[data-a=timerAdd]")
    assert api(srv, "GET", "timer")["running"] is False
    saved = [e for e in api(srv, "GET", "entries") if e.get("note") == "Beispiel Laufend"]
    assert len(saved) == 1 and saved[0]["project_id"] == project["id"]


def test_double_click_on_add_creates_one_entry(pg, srv):
    pg.fill("#t_start", "08:00")
    pg.dispatch_event("#t_start", "change")
    pg.fill("#t_end", "09:00")
    pg.dispatch_event("#t_end", "change")
    pg.fill("#t_note", "Beispiel Doppelklick")
    held = []  # hold the POSTs so both clicks land while the first save is still pending
    pg.route("**/api/entries", lambda route, req: held.append(route) if req.method == "POST" else route.continue_())
    pg.dblclick("[data-a=timerAdd]")
    pg.wait_for_timeout(300)
    assert len(held) == 1, f"{len(held)} saves sent for one double click"
    held[0].continue_()
    pg.wait_for_function("() => document.querySelector('#t_note') && document.querySelector('#t_note').value === ''")
    pg.unroute("**/api/entries")
    saved = [e for e in api(srv, "GET", "entries") if e.get("note") == "Beispiel Doppelklick"]
    assert len(saved) == 1


def test_failed_add_keeps_the_typed_inputs(pg, srv):
    api(srv, "POST", "projects", {"name": "Projekt Eta"})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    pg.fill("#t_date", "2026-10-05")
    pg.dispatch_event("#t_date", "change")
    pg.fill("#t_start", "13:00")
    pg.dispatch_event("#t_start", "change")
    pg.fill("#t_end", "14:15")
    pg.dispatch_event("#t_end", "change")
    pg.fill("#t_note", "Beispiel Fehler")
    pg.fill("#t_proj", "Projekt Eta")
    pg.dispatch_event("#t_proj", "change")
    pg.route(
        "**/api/entries",
        lambda route, req: (
            route.fulfill(status=400, content_type="application/json", body='{"detail": "Entry rejected"}')
            if req.method == "POST"
            else route.continue_()
        ),
    )
    pg.click("[data-a=timerAdd]")
    pg.wait_for_function("() => document.querySelector('#toast').classList.contains('err')")
    pg.wait_for_timeout(1000)  # a page re-render after the failure would wipe the form within this time
    pg.unroute("**/api/entries")
    assert pg.input_value("#t_note") == "Beispiel Fehler"
    assert pg.input_value("#t_date") == "2026-10-05"
    assert pg.input_value("#t_start") == "13:00"
    assert pg.input_value("#t_end") == "14:15"
    assert pg.input_value("#t_dur") == "1:15"
    assert pg.input_value("#t_proj") == "Projekt Eta"
    assert [e for e in api(srv, "GET", "entries") if e.get("note") == "Beispiel Fehler"] == []


def test_project_and_task_are_locked_while_a_timer_runs(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Theta"})
    api(srv, "POST", "timer/start", {"project_id": project["id"], "note": "Beispiel Gesperrt"})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    assert pg.is_disabled("#t_proj")
    assert pg.is_disabled("#t_task")
    assert pg.input_value("#t_proj") == "Projekt Theta"
    pg.click("[data-a=timerStop]")
    pg.wait_for_selector("[data-a=timerAdd]")  # stopped: back to the manual form
    assert not pg.is_disabled("#t_proj")
    assert not pg.is_disabled("#t_task")


def strip_default_rate(pg):
    """The settings always carry a default rate (80); for the 'no rate anywhere' case the bootstrap payload is
    served without it. Fake test data, the server itself is unchanged."""

    def handler(route):
        resp = route.fetch()
        data = resp.json()
        data["settings"].pop("default_rate", None)
        route.fulfill(response=resp, json=data)

    pg.route("**/api/bootstrap", handler)


def test_project_without_any_rate_shows_hint_and_click_does_nothing(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Epsilon"})  # no project, customer, user or default rate
    api(srv, "POST", "timer/start", {"project_id": project["id"], "note": "Beispiel ohne Satz"})
    strip_default_rate(pg)
    pg.reload()
    pg.wait_for_selector("#clock")
    assert pg.inner_text("#t_hint") == "no rate"
    pg.locator("#clock").click()
    pg.wait_for_timeout(200)  # a click that switched to money would have re-rendered the text by now
    assert "€" not in pg.locator("#clock").inner_text()
    pg.unroute("**/api/bootstrap")


def money_amount(text):
    """'1,23 €' (de-DE, non-breaking space) -> 1.23"""
    return float(text.replace("\u00a0€", "").replace(".", "").replace(",", "."))


def test_money_uses_the_customer_rate_and_grows_while_the_timer_runs(pg, srv):
    customer = api(srv, "POST", "customers", {"name": "Kunde Iota", "rate": 80})
    project = api(srv, "POST", "projects", {"name": "Projekt Iota", "customer_id": customer["id"]})  # no own rate
    api(srv, "POST", "timer/start", {"project_id": project["id"], "note": "Beispiel Kunde"})
    pg.reload()
    pg.wait_for_selector("#clock")
    clock = pg.locator("#clock")
    clock.click()  # time -> money
    first = clock.inner_text().strip()
    assert "€" in first, first
    pg.wait_for_timeout(2500)  # the running clock keeps ticking the money text, no second click
    second = clock.inner_text().strip()
    assert "€" in second, second
    assert money_amount(second) > money_amount(first), (first, second)
    assert pg.inner_text("#t_hint") == ""
    clock.click()  # money -> time again
    assert clock.inner_text().strip().count(":") == 2
