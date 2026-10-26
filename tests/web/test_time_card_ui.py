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
    api(srv, "POST", "projects", {"name": "Projekt Delta", "rate": 95})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    pg.click("[data-a=timeMode][data-mode=tracker]")
    pg.fill("#t_proj", "Projekt Delta")
    pg.dispatch_event("#t_proj", "change")
    clock = pg.locator("#clock")
    assert clock.inner_text().strip() == "0:00"  # idle
    clock.click()
    assert "€" in clock.inner_text()
    clock.click()
    assert clock.inner_text().strip() == "0:00"


def test_project_without_rate_shows_hint_and_click_does_nothing(pg, srv):
    api(srv, "POST", "projects", {"name": "Projekt Epsilon"})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    pg.click("[data-a=timeMode][data-mode=tracker]")
    pg.fill("#t_proj", "Projekt Epsilon")
    pg.dispatch_event("#t_proj", "change")
    assert pg.inner_text("#t_hint") == "no rate on this project"
    pg.click("#clock")
    pg.wait_for_timeout(200)  # a click that switched to money would have re-rendered the text by now
    assert "€" not in pg.locator("#clock").inner_text()
    assert pg.locator("#clock").inner_text().strip() == "0:00"
