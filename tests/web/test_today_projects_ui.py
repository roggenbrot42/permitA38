"""Browser test: the Today page card "Your projects this week" (docs/specs/project-centric-planning.md). Live server,
invented names; numbers use the de-DE format of the app."""

from datetime import date, timedelta

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
    yield page
    assert problems == []
    ctx.close()


def test_empty_state(pg, srv):
    pg.goto(srv.base + "/#/today")
    pg.wait_for_selector("#proj-hours")
    assert "No assignments this week" in pg.inner_text("#proj-hours")
    assert pg.locator("#proj-hours table").count() == 0


def test_rows_and_total(pg, srv):
    me = api(srv, "GET", "auth/me")
    monday = date.today() - timedelta(days=date.today().weekday())
    a = api(srv, "POST", "projects", {"name": "Projekt <Alpha>"})
    b = api(srv, "POST", "projects", {"name": "Projekt Intern", "kind": "internal"})
    for p, hours in ((a, 10), (b, 4)):
        body = {
            "user_id": me["id"],
            "project_id": p["id"],
            "start": monday.isoformat(),
            "end": (monday + timedelta(days=27)).isoformat(),
            "hours_per_week": hours,
        }
        api(srv, "POST", "allocations", body)
    entry = {"user_id": me["id"], "date": date.today().isoformat(), "minutes": 90, "project_id": a["id"]}
    api(srv, "POST", "entries", entry)
    pg.goto(srv.base + "/#/today")
    pg.wait_for_selector("#proj-hours table")
    rows = pg.locator("#proj-hours tr").evaluate_all("rs => rs.map(r => [...r.children].map(c => c.innerText.trim()))")
    assert rows[0][0].lower() == "project" and len(rows[0]) == 7
    assert rows[1][0] == "Projekt <Alpha>" and rows[1][2:6] == ["1,5", "10", "1,5", "8,5"]
    assert rows[2][0] == "Projekt Intern" and rows[2][2:6] == ["0", "4", "0", "4"]
    assert rows[3][0] == "Total" and rows[3][2:6] == ["1,5", "14", "1,5", "12,5"]


def test_play_buttons_start_the_timer_on_project_and_task(pg, srv):
    me = api(srv, "GET", "auth/me")
    monday = date.today() - timedelta(days=date.today().weekday())
    a = api(srv, "POST", "projects", {"name": "Projekt <Alpha>"})
    task = api(srv, "POST", "tasks", {"project_id": a["id"], "name": "Aufgabe <Eins>"})
    b = api(srv, "POST", "projects", {"name": "Projekt Intern", "kind": "internal"})
    for p in (a, b):
        body = {
            "user_id": me["id"],
            "project_id": p["id"],
            "start": monday.isoformat(),
            "end": (monday + timedelta(days=27)).isoformat(),
            "hours_per_week": 5,
        }
        api(srv, "POST", "allocations", body)
    entry = {"user_id": me["id"], "date": date.today().isoformat(), "minutes": 30, "project_id": a["id"]}
    api(srv, "POST", "entries", {**entry, "task_id": task["id"]})
    pg.goto(srv.base + "/#/today")
    pg.wait_for_selector("#proj-hours table")
    row = pg.locator("#proj-hours tr.task-row")
    assert row.count() == 1
    cells = row.evaluate("r => [...r.children].map(c => c.innerText.trim())")
    assert cells[0] == "Aufgabe <Eins>" and cells[4] == "0,5"
    sent = []

    def on_request(r):
        if r.method == "POST" and r.url.endswith("/api/timer/start"):
            sent.append(r.post_data_json)

    pg.on("request", on_request)
    pg.click("[aria-label='Start timer on Projekt <Alpha>']")
    pg.wait_for_selector("[data-a=timerStop]")
    assert sent[-1] == {"project_id": a["id"]}
    assert api(srv, "GET", "timer")["project_id"] == a["id"]
    pg.click("[aria-label='Start timer on Projekt <Alpha> / Aufgabe <Eins>']")
    pg.wait_for_function("() => document.querySelector('#t_task').value !== ''")
    assert sent[-1] == {"project_id": a["id"], "task_id": task["id"]}
    timer = api(srv, "GET", "timer")
    assert timer["running"] and timer["task_id"] == task["id"]
    pg.click("[aria-label='Start timer on Projekt Intern']")
    pg.wait_for_function("() => document.querySelector('#t_task').value === ''")
    assert sent[-1] == {"project_id": b["id"]}


def test_today_entry_row_has_a_play_button(pg, srv):
    me = api(srv, "GET", "auth/me")
    a = api(srv, "POST", "projects", {"name": "Projekt <Beta>"})
    task = api(srv, "POST", "tasks", {"project_id": a["id"], "name": "Aufgabe <Zwei>"})
    entry = {"user_id": me["id"], "date": date.today().isoformat(), "minutes": 30, "project_id": a["id"]}
    api(srv, "POST", "entries", {**entry, "task_id": task["id"]})
    api(srv, "POST", "entries", {**entry, "project_id": None, "note": "ohne Projekt"})
    pg.goto(srv.base + "/#/today")
    pg.wait_for_selector("tr.click")
    assert pg.locator("tr.click").count() == 2
    assert pg.locator("tr.click [data-a=timerPlay]").count() == 1
    sent = []
    pg.on(
        "request",
        lambda r: sent.append(r.post_data_json) if r.method == "POST" and r.url.endswith("/api/timer/start") else None,
    )
    pg.click("tr.click [aria-label='Start timer on Projekt <Beta> / Aufgabe <Zwei>']")
    pg.wait_for_selector("[data-a=timerStop]")
    assert sent == [{"project_id": a["id"], "task_id": task["id"]}]
    assert pg.locator("#modal-root [data-a], .modal").count() == 0 or not pg.is_visible("#m-del")
