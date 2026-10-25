"""Suppliers and incoming offers against the real server (demo data only).

Skipped while the server has no suppliers route (the backend of docs/contracts/subcontractors.md is built in parallel).
Every number the UI shows is compared with what the server returned, never with a value computed here."""

from datetime import timedelta

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_impressum_prefill import PASSWORD, Server  # noqa: E402  (shared harness)
from tests.web.test_planner_per_project_live import monday  # noqa: E402


def auth(token):
    return {"Authorization": "Bearer " + token}


def de(x):
    """The app's money format: 6.400,00"""
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    h = auth(s.token)
    if httpx.get(s.base + "/api/suppliers", headers=h).status_code == 404:
        s.stop()
        pytest.skip("the backend has no suppliers route yet")
    for role in ("employee", "accountant"):
        r = httpx.post(
            s.base + "/api/users",
            headers=h,
            json={"username": role, "name": role.title(), "role": role, "password": PASSWORD},
        )
        assert r.status_code == 200, r.text
    cust = httpx.post(s.base + "/api/customers", headers=h, json={"name": "Demo Customer GmbH"}).json()
    start, end = monday(), monday() + timedelta(days=27)
    proj = httpx.post(
        s.base + "/api/projects",
        headers=h,
        json={"name": "Demo project P", "customer_id": cust["id"], "start": start.isoformat(), "end": end.isoformat()},
    ).json()
    sub = httpx.post(
        s.base + "/api/suppliers",
        headers=h,
        json={
            "name": "Example Freelance Studio (Demo)",
            "supplier_no": "L-0003",
            "kind": "person-capacity",
            "tags": ["frontend"],
            "weekly_hours": 24,
            "workdays": [0, 1, 2, 3],
            "rate": 80,
            "iban": "DE02120300000000202051",
            "email": "studio@example.invalid",
        },
    )
    assert sub.status_code == 201, sub.text
    s.supplier = sub.json()
    s.project = proj
    yield s
    s.stop()


def login_ui(browser, srv, username):
    r = httpx.post(srv.base + "/api/auth/login", json={"username": username, "password": PASSWORD})
    token = r.json()["token"]
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    ctx.add_cookies([{"name": "authorization", "value": token, "url": srv.base}])
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: d.accept())
    return ctx, pg, problems


def test_employee_sees_the_limited_supplier_view(browser, srv):
    ctx, pg, problems = login_ui(browser, srv, "employee")
    pg.goto(srv.base + "/#/suppliers")
    expect(pg.locator("main")).to_contain_text("Example Freelance Studio (Demo)")
    text = pg.locator("main").inner_text()
    assert "DE02120300000000202051" not in text  # no IBAN for an employee
    assert "studio@example.invalid" not in text
    assert problems == []
    ctx.close()


def test_boss_accepts_an_offer_and_sees_the_server_numbers(browser, srv):
    h = auth(srv.token)
    offer = httpx.post(
        srv.base + "/api/incoming-offers",
        headers=h,
        json={
            "supplier_id": srv.supplier["id"],
            "reference": "ANG-LIVE-1",
            "title": "Frontend capacity",
            "received_on": monday().isoformat(),
            "lines": [{"description": "Frontend development", "quantity": 80, "unit": "h", "unit_price": 80}],
            "project_id": srv.project["id"],
            "plan": {"start": monday().isoformat(), "end": (monday() + timedelta(days=27)).isoformat(), "hours_per_week": 20},
        },
    )
    assert offer.status_code == 201, offer.text
    oid = offer.json()["id"]
    ctx, pg, problems = login_ui(browser, srv, "boss")
    pg.goto(srv.base + "/#/incoming/" + oid)
    expect(pg.locator("h1")).to_contain_text("ANG-LIVE-1")
    pg.click("[data-a=offAccept]")
    dlg = pg.locator("#modal-root .modal")
    expect(dlg).to_contain_text("Accept offer ANG-LIVE-1")
    pg.click("#ac_submit")
    expect(pg.locator("#ac_result")).to_be_visible()
    server = httpx.get(srv.base + "/api/incoming-offers/" + oid, headers=h).json()
    alloc = httpx.get(srv.base + "/api/allocations/" + server["allocation_id"], headers=h).json()
    expect(pg.locator("#ac_result")).to_contain_text(de(alloc["planned_net"]))
    pg.keyboard.press("Escape")
    pg.goto(srv.base + "/#/incoming/" + oid)
    expect(pg.locator("main")).to_contain_text("Planned cost (net)")
    expect(pg.locator("main")).to_contain_text(de(alloc["planned_net"]))
    assert problems == []
    ctx.close()


def test_accountant_reads_offers_but_not_accepts(browser, srv):
    h = auth(srv.token)
    oid = httpx.post(
        srv.base + "/api/incoming-offers",
        headers=h,
        json={
            "supplier_id": srv.supplier["id"],
            "reference": "ANG-LIVE-2",
            "received_on": monday().isoformat(),
            "lines": [{"description": "Review", "quantity": 4, "unit": "h", "unit_price": 60}],
        },
    ).json()["id"]
    ctx, pg, problems = login_ui(browser, srv, "accountant")
    pg.goto(srv.base + "/#/incoming/" + oid)
    expect(pg.locator("h1")).to_contain_text("ANG-LIVE-2")
    assert pg.locator("[data-a=offAccept], [data-a=offReject], [data-a=offEdit], [data-a=offDelete]").count() == 0
    assert problems == []
    ctx.close()
