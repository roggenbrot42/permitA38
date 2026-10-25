"""Small UI defects of the alpha.3 click-through, against the real server (demo data only)."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

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
    yield s
    s.stop()


def open_as(browser, srv, username):
    token = httpx.post(srv.base + "/api/auth/login", json={"username": username, "password": PASSWORD}).json()["token"]
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("response", lambda r: problems.append(f"http {r.status} {r.url}") if r.status >= 400 else None)
    return ctx, pg, problems


@pytest.mark.parametrize("role", ["employee", "accountant"])
@pytest.mark.parametrize("page", ["import", "numbering"])
def test_boss_only_pages_deny_a_non_boss(browser, srv, role, page):
    ctx, pg, problems = open_as(browser, srv, role)
    pg.goto(srv.base + "/")
    pg.wait_for_selector("nav.side a.nav")
    # the router redirects away from the page ...
    pg.goto(srv.base + f"/#/{page}")
    pg.wait_for_timeout(500)
    assert "Import" not in pg.inner_text("main h1") and "Numbering" not in pg.inner_text("main h1")
    # ... and the page itself refuses with a message, not with a ReferenceError (denyMember was never defined)
    html = pg.evaluate(f"() => PAGES.{page}()")
    assert "only available to the boss" in html
    assert [p for p in problems if p.startswith("pageerror")] == []
    ctx.close()
