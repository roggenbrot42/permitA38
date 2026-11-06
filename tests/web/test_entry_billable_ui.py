"""Internal projects are never billable: the time dialog locks the Billable box (fake demo data)."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server  # noqa: E402  (shared harness)
from tests.web.test_tokens_live import open_as_boss  # noqa: E402


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def api(srv, method, path, body=None):
    r = httpx.request(method, srv.base + "/api/" + path, json=body, headers={"Authorization": "Bearer " + srv.token})
    assert r.status_code < 300, r.text
    return r.json()


def test_dialog_locks_billable_for_internal_projects(browser, srv):
    customer = api(srv, "POST", "customers", {"name": "Kunde Alpha"})
    cust = api(srv, "POST", "projects", {"name": "Projekt Kunde", "customer_id": customer["id"]})
    internal = api(srv, "POST", "projects", {"name": "Projekt Intern"})
    ctx, pg, problems = open_as_boss(browser, srv)
    pg.goto(srv.base + "/#/today")
    pg.click("[data-a=addTime]")
    assert pg.is_checked("#f_billable") and pg.is_enabled("#f_billable")
    pg.select_option("#f_project_id", internal["id"])
    assert not pg.is_checked("#f_billable") and pg.is_disabled("#f_billable")
    assert pg.get_attribute("#f_billable", "title") == "Internal projects are never billable"
    pg.select_option("#f_project_id", cust["id"])
    assert pg.is_checked("#f_billable") and pg.is_enabled("#f_billable")
    pg.select_option("#f_project_id", internal["id"])
    pg.fill("#f_start", "09:00")
    pg.fill("#f_end", "10:00")
    pg.fill("#f_note", "internal work")
    pg.click("#m-save")
    pg.wait_for_selector("#modal-root .modal", state="detached")
    saved = [e for e in api(srv, "GET", "entries") if e.get("note") == "internal work"]
    assert len(saved) == 1 and saved[0]["billable"] is False
    # reopening the saved entry shows the locked box
    pg.goto(srv.base + "/#/time")
    pg.click(f"[data-a=editEntry][data-id='{saved[0]['id']}']")
    assert not pg.is_checked("#f_billable") and pg.is_disabled("#f_billable")
    assert not problems
    ctx.close()


def test_timer_card_manual_save_on_internal_project_is_not_billable(browser, srv):
    api(srv, "POST", "projects", {"name": "Projekt Intern"})
    ctx, pg, problems = open_as_boss(browser, srv)
    pg.goto(srv.base + "/#/today")
    pg.wait_for_selector("#t_proj")
    pg.fill("#t_start", "09:00")
    pg.dispatch_event("#t_start", "change")
    pg.fill("#t_end", "10:00")
    pg.dispatch_event("#t_end", "change")
    pg.fill("#t_note", "card internal")
    pg.fill("#t_proj", "Projekt Intern")
    pg.dispatch_event("#t_proj", "change")
    pg.click("[data-a=timerAdd]")
    pg.wait_for_function("() => document.querySelector('#t_note') && document.querySelector('#t_note').value === ''")
    saved = [e for e in api(srv, "GET", "entries") if e.get("note") == "card internal"]
    assert len(saved) == 1 and saved[0]["billable"] is False
    assert not problems
    ctx.close()
