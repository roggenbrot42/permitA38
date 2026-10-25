"""Planner per project against the real server: forecast banner, bootstrap, allocations dialog.
Skipped until the backend branch (feat/planner-per-project-backend) is part of this tree: it needs /api/allocations."""

from datetime import date, timedelta

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_impressum_prefill import Server  # noqa: E402  (shared harness)


def monday() -> date:
    d = date.today()
    return d - timedelta(days=d.weekday())


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    h = {"Authorization": "Bearer " + s.token}
    if httpx.get(s.base + "/api/allocations", headers=h).status_code == 404:
        s.stop()
        pytest.skip("the backend has no /api/allocations yet (planner per project)")
    yield s
    s.stop()


@pytest.fixture
def seeded(srv):
    """A boss (40 h/week) and one project with a contract of 30 h/week, but no allocation: example B of the contract."""
    h = {"Authorization": "Bearer " + srv.token}
    me = httpx.get(srv.base + "/api/auth/me", headers=h).json()
    cust = httpx.post(srv.base + "/api/customers", headers=h, json={"name": "Demo Customer GmbH"}).json()
    start, end = monday(), monday() + timedelta(days=27)
    proj = httpx.post(
        srv.base + "/api/projects",
        headers=h,
        json={"name": "Demo project P", "customer_id": cust["id"], "start": start.isoformat(), "end": end.isoformat()},
    )
    assert proj.status_code == 200, proj.text
    proj = proj.json()
    r = httpx.post(
        srv.base + "/api/contracts",
        headers=h,
        json={
            "title": "Contract C_P",
            "customer_id": cust["id"],
            "project_id": proj["id"],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "hours_per_week": 30,
            "rate": 90,
            "status": "confirmed",
            "user_ids": [me["id"]],
        },
    )
    assert r.status_code == 200, r.text
    return dict(me=me, project=proj, headers=h)


@pytest.fixture
def ui(browser, srv, seeded):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.dialogs = []
    pg.on("dialog", lambda d: (pg.dialogs.append(d.message), d.accept()))
    pg.on("console", lambda m: m.type == "error" and "status of" not in m.text and problems.append(m.text))
    yield pg
    assert problems == []
    ctx.close()


def test_banner_bootstrap_clears_the_gap(srv, seeded, ui):
    ui.goto(srv.base + "/#/plan/forecast")
    ui.wait_for_selector(".pp-banner")
    before = httpx.get(srv.base + "/api/forecast?weeks=4", headers=seeded["headers"]).json()
    assert before["totals"]["gap"] > 0
    ui.get_by_role("button", name="Allocate my capacity").click()
    dlg = ui.locator("#modal-root .modal")
    expect(dlg.locator(".al-preview")).to_contain_text("Contract C_P")
    assert httpx.get(srv.base + "/api/allocations", headers=seeded["headers"]).json() == []  # a preview writes nothing
    dlg.get_by_role("button", name="Create 1 allocation").click()
    expect(dlg.locator(".al-preview")).to_contain_text("Created 1 allocation")
    dlg.get_by_role("button", name="Close").click()
    ui.wait_for_selector(".pp-banner", state="detached")
    after = httpx.get(srv.base + "/api/forecast?weeks=4", headers=seeded["headers"]).json()
    assert after["totals"]["gap"] == 0
    rows = httpx.get(srv.base + "/api/allocations", headers=seeded["headers"]).json()
    assert [(r["project_id"], r["hours_per_week"]) for r in rows] == [(seeded["project"]["id"], 30.0)]


def test_dialog_creates_edits_and_deletes_an_allocation(srv, seeded, ui):
    pid = seeded["project"]["id"]
    ui.goto(srv.base + f"/#/project/{pid}")
    ui.locator(f"[data-a=allocations][data-project={pid}]").click()
    dlg = ui.locator("#modal-root .modal")
    dlg.get_by_role("button", name="+ Allocation").click()
    dlg.locator("#al_hours").fill("25")
    dlg.locator("form.al-form").get_by_role("button", name="Save").click()
    expect(dlg.locator("table")).to_contain_text("25 h/week")
    rows = httpx.get(srv.base + "/api/allocations", headers=seeded["headers"]).json()
    assert len(rows) == 1 and rows[0]["hours_per_week"] == 25.0
    dlg.locator("tr[data-a=alEdit]").first.click()
    dlg.locator("#al_hours").fill("90")  # outside 0.25..80: refused before any request
    dlg.locator("form.al-form").get_by_role("button", name="Save").click()
    expect(dlg.locator("[role=alert]")).to_contain_text("between 0.25 and 80")
    dlg.locator("#al_hours").fill("60")  # more than the 40 h contract: saved, with a warning
    dlg.locator("form.al-form").get_by_role("button", name="Save").click()
    expect(dlg.locator(".al-warn")).to_be_visible()
    dlg.locator("tr[data-a=alEdit]").first.click()
    dlg.get_by_role("button", name="Delete").click()
    expect(dlg).to_contain_text("No allocations yet")
    assert httpx.get(srv.base + "/api/allocations", headers=seeded["headers"]).json() == []
    assert ui.dialogs == ["Delete this allocation?"]
