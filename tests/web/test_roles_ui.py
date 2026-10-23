"""Browser tests of roles: what the navigation shows, which pages are refused, read-only pages for the accountant."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402,F401  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    h = {"Authorization": "Bearer " + s.token}
    cust = httpx.post(s.base + "/api/customers", headers=h, json={"name": "Demo GmbH"}).json()
    httpx.post(
        s.base + "/api/invoices",
        headers=h,
        json={"customer_id": cust["id"], "lines": [{"description": "Work", "qty": 1, "price": 100}]},
    )
    for role in ("employee", "accountant"):
        r = httpx.post(
            s.base + "/api/users",
            headers=h,
            json={"username": role, "name": role.title(), "role": role, "password": PASSWORD},
        )
        assert r.status_code == 200, r.text
    yield s
    s.stop()


def open_as(browser, srv, username):
    token = httpx.post(srv.base + "/api/auth/login", json={"username": username, "password": PASSWORD}).json()["token"]
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: (problems.append("dialog: " + d.message), d.dismiss()))
    return ctx, pg, problems


def nav(pg):
    pg.wait_for_selector("nav.side a.nav")
    return [t.strip() for t in pg.locator("nav.side a[href^='#/']").all_inner_texts()]


def test_accountant_nav_and_read_only_pages(browser, srv):
    ctx, pg, problems = open_as(browser, srv, "accountant")
    pg.goto(srv.base + "/")
    labels = nav(pg)
    joined = " ".join(labels)
    for shown in ("Customers", "Projects", "Offers & Invoices", "Expenses", "Money & Taxes", "Settings"):
        assert shown in joined, labels
    for hidden in ("Today", "Timesheet", "Planning", "Team", "Time off"):
        assert hidden not in joined, labels
    assert pg.url.endswith("#/customers")  # landing page = first allowed page
    assert pg.locator("button[data-a=editCustomer]").count() == 0  # read-only
    assert "Read-only" in pg.inner_text("#main")
    # expenses are writable
    pg.goto(srv.base + "/#/expenses")
    pg.wait_for_selector("button[data-a=editExpense]")
    # a forbidden page is refused and sends the user back to an allowed one
    pg.goto(srv.base + "/#/team")
    pg.wait_for_selector("#toast.on")
    assert "#/team" not in pg.url
    # the invoice opens read-only
    pg.goto(srv.base + "/#/sales")
    pg.wait_for_selector("tr[data-href]")
    pg.locator("tr[data-href]").first.click()
    pg.wait_for_selector("#main input")
    assert pg.locator("#main input").first.is_disabled()
    assert pg.locator("button[data-a=docSave]").count() == 0
    assert problems == []
    ctx.close()


def test_employee_nav_is_unchanged(browser, srv):
    ctx, pg, problems = open_as(browser, srv, "employee")
    pg.goto(srv.base + "/")
    labels = " ".join(nav(pg))
    assert labels.split() == ["⏱", "Today", "🗓", "Timesheet", "🏖", "Time", "off"]
    pg.goto(srv.base + "/#/customers")
    pg.wait_for_selector("#toast.on")
    assert "#/customers" not in pg.url
    pg.goto(srv.base + "/#/projects")
    pg.wait_for_selector("#toast.on")
    assert problems == []
    ctx.close()


def test_boss_sees_everything_and_can_choose_roles(browser, srv):
    ctx, pg, problems = open_as(browser, srv, "boss")
    pg.goto(srv.base + "/")
    labels = " ".join(nav(pg))
    for shown in ("Today", "Timesheet", "Planning", "Customers", "Projects", "Offers & Invoices", "Expenses", "Team"):
        assert shown in labels
    pg.goto(srv.base + "/#/team")
    pg.wait_for_selector("button[data-a=editUser]")
    pg.click("button[data-a=editUser]")
    pg.wait_for_selector("#f_role")
    options = pg.locator("#f_role option").evaluate_all("els => els.map(e => e.value)")
    assert options == ["employee", "accountant", "boss"]
    assert problems == []
    ctx.close()
