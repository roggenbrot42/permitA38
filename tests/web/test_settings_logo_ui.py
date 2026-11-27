"""Settings logo upload in a real browser against the real server: the chosen image is converted to JPEG (fake demo
data only; the PNG fixture is built with zlib/struct)."""

import struct
import zlib

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server  # noqa: E402  (shared harness)


def make_png(w: int = 40, h: int = 20) -> bytes:
    """RGBA PNG: opaque red left half, fully transparent right half."""
    rows = b""
    for _y in range(h):
        rows += b"\x00" + b"".join(b"\xc8\x20\x20\xff" if x < w // 2 else b"\x00\x00\x00\x00" for x in range(w))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture
def page(browser, srv):
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    ctx.add_cookies([{"name": "authorization", "value": srv.token, "url": srv.base}])
    pg = ctx.new_page()
    pg.set_default_timeout(20000)
    yield pg
    ctx.close()


def headers(srv):
    return {"Authorization": "Bearer " + srv.token}


def settings(srv):
    return httpx.get(srv.base + "/api/settings", headers=headers(srv)).json()


def test_png_logo_is_converted_to_jpeg_and_printed(page, srv):
    h = headers(srv)
    # a PNG attachment stored as the logo earlier: the hint shows
    name = httpx.post(
        srv.base + "/api/attachments", headers=h, files={"file": ("old.png", make_png(), "image/png")}
    ).json()["name"]
    s = settings(srv)
    s["logo"] = name
    assert httpx.put(srv.base + "/api/settings", headers=h, json=s).status_code == 200

    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_file", state="attached")
    assert page.locator("#logo_hint").is_visible()
    assert "not printed" in page.inner_text("#logo_hint")

    page.set_input_files("#logo_file", [{"name": "logo.png", "mimeType": "image/png", "buffer": make_png()}])
    page.click('[data-a="saveSettings"]')
    page.wait_for_selector("#logo_hint", state="detached")

    logo = settings(srv)["logo"]
    assert logo.lower().endswith(".jpg")
    blob = httpx.get(srv.base + "/api/attachments/" + logo, headers=h)
    assert blob.status_code == 200
    assert blob.content[:2] == b"\xff\xd8"

    cust = httpx.post(srv.base + "/api/customers", headers=h, json={"name": "Demo Kunde GmbH"}).json()
    inv = httpx.post(
        srv.base + "/api/invoices",
        headers=h,
        json={"customer_id": cust["id"], "lines": [{"description": "Work", "qty": 1, "unit": "h", "price": 10}]},
    ).json()
    pdf = httpx.get(srv.base + f"/api/invoices/{inv['id']}/pdf", headers=h)
    assert pdf.status_code == 200
    assert b"/DeviceRGB" in pdf.content


def test_non_image_logo_shows_error_and_changes_nothing(page, srv):
    before = settings(srv)
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_file", state="attached")
    page.fill("#f_company_name", "Changed GmbH")
    page.set_input_files("#logo_file", [{"name": "logo.png", "mimeType": "image/png", "buffer": b"not an image"}])
    page.click('[data-a="saveSettings"]')
    page.wait_for_selector("#toast.err")
    assert "cannot be used as a logo" in page.inner_text("#toast")
    after = settings(srv)
    assert after.get("logo") == before.get("logo")
    assert after.get("company_name") == before.get("company_name")
