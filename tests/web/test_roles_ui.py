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
    for hidden in ("Today", "Timesheet", "Planning", "Time off"):
        assert hidden not in joined, labels
    # the Team page (user management) stays hidden; only the Team timeline entry is offered
    assert any(x.endswith("Team timeline") for x in labels), labels
    assert not any(x.endswith("Team") for x in labels), labels
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
    # the accountant's default level for the team timeline is "off": the page says so
    pg.goto(srv.base + "/#/timeline")
    pg.wait_for_selector("text=switched off for your role")
    assert pg.url.endswith("#/timeline")
    # the invoice opens read-only
    pg.goto(srv.base + "/#/sales")
    pg.wait_for_selector("tr[data-href]")
    pg.locator("tr[data-href]").first.click()
    pg.wait_for_selector("#main input")
    assert pg.locator("#main input").first.is_disabled()
    assert pg.locator("button[data-a=docSave]").count() == 0
    assert problems == []
    ctx.close()


def test_slow_older_page_does_not_overwrite_newer_page(browser, srv):
    """A denied page redirects to the landing page; if that page loads slowly it must not replace a page opened meanwhile."""
    ctx, pg, problems = open_as(browser, srv, "accountant")
    pg.goto(srv.base + "/")
    pg.wait_for_selector("text=Read-only")
    held = []
    pg.route("**/api/customers*", lambda route: held.append(route))  # hold the landing page's data back
    pg.goto(srv.base + "/#/team")
    pg.wait_for_selector("#toast.on")
    pg.goto(srv.base + "/#/timeline")
    pg.wait_for_selector("text=switched off for your role")
    assert held, "the redirect to the landing page should have asked for customers"
    for route in held:
        route.continue_()  # the older page's data arrives last
    pg.wait_for_timeout(1500)
    assert "switched off for your role" in pg.inner_text("#main")
    ctx.close()


def test_employee_nav_is_unchanged(browser, srv):
    ctx, pg, problems = open_as(browser, srv, "employee")
    pg.goto(srv.base + "/")
    labels = " ".join(nav(pg))
    assert labels.split() == [
        "⏱",
        "Today",
        "🗓",
        "Timesheet",
        "🏖",
        "Time",
        "off",
        "🏭",  # suppliers: the employee's limited view (docs/contracts/subcontractors.md, rights)
        "Suppliers",
        "🧳",
        "Business",
        "trips",
        "📅",
        "Team",
        "timeline",
        "🔑",
        "API",
        "tokens",
    ]
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


def test_import_and_export_links_live_in_settings_not_suppliers(browser, srv):
    ctx, pg, problems = open_as(browser, srv, "boss")
    pg.goto(srv.base + "/#/suppliers")
    pg.wait_for_selector("h1:has-text('Suppliers')")
    assert pg.locator(".sup-actions a[href='#/import'], .sup-actions a[href='#/dataexport']").count() == 0
    pg.goto(srv.base + "/#/settings")
    pg.wait_for_selector("h2:has-text('Data & sync')")
    card = pg.locator(".card", has=pg.locator("h2:has-text('Data & sync')"))
    assert card.locator("a[href='#/dataexport']").count() == 1
    assert card.locator("a[href='#/import']").count() == 1
    assert "Data export and import" in card.locator("a[href='#/dataexport']").inner_text()
    assert "Import documents from another tool" in card.locator("a[href='#/import']").inner_text()
    # the whole page (not only the card) has exactly one entry of each: no duplicate in the Defaults card
    assert pg.locator("a[href='#/import']").count() == 1
    assert pg.locator("a[href='#/dataexport']").count() == 1
    assert problems == []
    ctx.close()


def test_data_transfer_links_hidden_without_settings_write(browser, srv):
    # the accountant may read Settings (all) but cannot write them: no Data transfer block, no links at all
    ctx, pg, problems = open_as(browser, srv, "accountant")
    pg.goto(srv.base + "/#/settings")
    pg.wait_for_selector("h2:has-text('Data & sync')")
    assert pg.locator("a[href='#/import']").count() == 0
    assert pg.locator("a[href='#/dataexport']").count() == 0
    assert problems == []
    ctx.close()
