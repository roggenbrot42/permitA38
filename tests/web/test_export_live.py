"""Data export and import against the real server (demo data only). Skipped until the backend routes exist
(branch feat/data-export); the integrator removes the probe skip after the backend has merged."""

import io
import json
import zipfile

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_impressum_prefill import PASSWORD, Server  # noqa: E402  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    h = {"Authorization": "Bearer " + s.token}
    probe = httpx.get(s.base + "/api/data/export/info", headers=h)
    if probe.status_code == 404:
        s.stop()
        pytest.skip("GET /api/data/export/info does not exist yet: the backend of feat/data-export is not merged")
    r = httpx.post(
        s.base + "/api/users",
        headers=h,
        json={"username": "employee", "name": "Employee", "role": "employee", "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    r = httpx.post(s.base + "/api/customers", headers=h, json={"name": "Demo GmbH", "city": "Köln"})
    assert r.status_code == 200, r.text
    yield s
    s.stop()


def open_as(browser, srv, username):
    r = httpx.post(srv.base + "/api/auth/login", json={"username": username, "password": PASSWORD})
    token = r.json()["token"]
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    ctx.add_cookies([{"name": "authorization", "value": token, "url": srv.base}])
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: d.accept())
    return ctx, pg, problems


def test_boss_downloads_a_real_archive_and_it_is_not_importable_here(browser, srv, tmp_path):
    ctx, pg, problems = open_as(browser, srv, "boss")
    pg.goto(srv.base + "/#/dataexport")
    pg.wait_for_selector("#dx_export")
    expect(pg.locator("#dx_import [role=alert]")).to_contain_text("already contains data")
    with pg.expect_download() as dl:
        pg.get_by_role("button", name="Download full archive").click()
    target = tmp_path / "out.zip"
    dl.value.save_as(target)
    with zipfile.ZipFile(target) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["format"] == "permita38-export"
        assert "data/customers.json" in z.namelist()
        assert "Demo GmbH" in z.read("data/customers.json").decode()
        assert b"password_hash" not in z.read("data/users.json")
    expect(pg.locator("#dx_transfers")).to_contain_text("Export")
    # the same archive uploaded to this non-empty instance: refused with the clear reason
    r = httpx.post(
        srv.base + "/api/data/import/preview",
        headers={"Authorization": "Bearer " + srv.token},
        files={"file": ("out.zip", io.BytesIO(target.read_bytes()), "application/zip")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False
    assert problems == []
    ctx.close()


def test_employee_is_denied(browser, srv):
    ctx, pg, problems = open_as(browser, srv, "employee")
    pg.goto(srv.base + "/#/dataexport")
    pg.wait_for_selector("#toast.on")
    assert "dataexport" not in pg.url
    r = httpx.post(srv.base + "/api/auth/login", json={"username": "employee", "password": PASSWORD})
    res = httpx.get(srv.base + "/api/data/export/info", headers={"Authorization": "Bearer " + r.json()["token"]})
    assert res.status_code == 403
    assert problems == []
    ctx.close()
