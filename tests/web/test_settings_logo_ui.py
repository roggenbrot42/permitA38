"""Settings logo upload in a real browser against the real server: the chosen image becomes a transparent PNG (fake demo
data only; fixtures are built with zlib/struct)."""

import struct
import zlib

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server  # noqa: E402  (shared harness)


def make_png(w: int = 40, h: int = 20) -> bytes:
    """RGBA PNG: opaque colour (200, 32, 32) left half, fully transparent right half."""
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


SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80">'
    b'<rect x="0" y="0" width="100" height="80" fill="#2060c0"/></svg>'
)


def decode_pixels(page, name):
    """Fetch the stored attachment in the page, decode it and sample RGBA of one pixel per half."""
    return page.evaluate(
        """async name => {
          const tok = localStorage.getItem('permita38.token');
          const r = await fetch('/api/attachments/' + name, { headers: { Authorization: 'Bearer ' + tok } });
          const bmp = await createImageBitmap(await r.blob());
          const cv = document.createElement('canvas'); cv.width = bmp.width; cv.height = bmp.height;
          const cx = cv.getContext('2d'); cx.drawImage(bmp, 0, 0);
          const px = (x, y) => Array.from(cx.getImageData(x, y, 1, 1).data);
          const out = { w: bmp.width, h: bmp.height, left: px(Math.floor(bmp.width / 4), Math.floor(bmp.height / 2)),
                        right: px(Math.floor(bmp.width * 3 / 4), Math.floor(bmp.height / 2)) };
          bmp.close(); return out;
        }""",
        name,
    )


def save(page):
    page.click('[data-a="saveSettings"]')
    page.wait_for_selector("#toast:has-text('Settings saved')")


def test_png_logo_stays_transparent_and_is_previewed(page, srv):
    h = headers(srv)
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_file", state="attached")
    page.set_input_files("#logo_file", [{"name": "logo.png", "mimeType": "image/png", "buffer": make_png()}])
    page.wait_for_selector("#logo_preview")
    assert page.get_attribute("#logo_preview", "src").startswith("blob:")
    assert "Not saved yet" in page.inner_text("#logo_note")
    save(page)

    logo = settings(srv)["logo"]
    assert logo.lower().endswith(".png")
    px = decode_pixels(page, logo)
    assert (px["w"], px["h"]) == (40, 20)
    assert px["right"][3] <= 10  # transparent half stays transparent
    assert all(abs(c - e) <= 12 for c, e in zip(px["left"][:3], (200, 32, 32)))
    assert px["left"][3] >= 245

    page.reload()
    page.wait_for_selector("#logo_preview")
    assert "attachments/" in page.get_attribute("#logo_preview", "src")
    assert page.eval_on_selector("#logo_preview", "e => e.complete && e.naturalWidth > 0")

    cust = httpx.post(srv.base + "/api/customers", headers=h, json={"name": "Demo Kunde GmbH"}).json()
    inv = httpx.post(
        srv.base + "/api/invoices",
        headers=h,
        json={"customer_id": cust["id"], "lines": [{"description": "Work", "qty": 1, "unit": "h", "price": 10}]},
    ).json()
    pdf = httpx.get(srv.base + f"/api/invoices/{inv['id']}/pdf", headers=h)
    assert pdf.status_code == 200
    assert b"/SMask" in pdf.content  # needs pdf.png


def test_svg_logo_becomes_a_transparent_png(page, srv):
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_file", state="attached")
    page.set_input_files("#logo_file", [{"name": "logo.svg", "mimeType": "image/svg+xml", "buffer": SVG}])
    page.wait_for_selector("#logo_preview")
    save(page)
    logo = settings(srv)["logo"]
    assert logo.lower().endswith(".png")
    px = decode_pixels(page, logo)
    assert (px["w"], px["h"]) == (900, 360)
    assert px["right"][3] <= 10
    assert all(abs(c - e) <= 12 for c, e in zip(px["left"][:3], (0x20, 0x60, 0xC0)))
    assert px["left"][3] >= 245


def test_large_logo_is_scaled_down(page, srv):
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_file", state="attached")
    page.set_input_files("#logo_file", [{"name": "big.png", "mimeType": "image/png", "buffer": make_png(1800, 100)}])
    page.wait_for_selector("#logo_preview")
    save(page)
    px = decode_pixels(page, settings(srv)["logo"])
    assert (px["w"], px["h"]) == (900, 50)


def test_non_image_logo_shows_error_and_changes_nothing(page, srv):
    before = settings(srv)
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_file", state="attached")
    page.set_input_files("#logo_file", [{"name": "logo.png", "mimeType": "image/png", "buffer": b"not an image"}])
    page.wait_for_selector("#logo_note:has-text('cannot be used as a logo')")
    assert page.locator("#logo_preview").count() == 0
    assert settings(srv) == before


def test_remove_logo_clears_it_and_the_preview(page, srv):
    h = headers(srv)
    name = httpx.post(
        srv.base + "/api/attachments", headers=h, files={"file": ("old.png", make_png(), "image/png")}
    ).json()["name"]
    s = settings(srv)
    s["logo"] = name
    assert httpx.put(srv.base + "/api/settings", headers=h, json=s).status_code == 200
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_preview")
    assert page.locator("#logo_hint").count() == 0  # a stored .png is printable
    page.click('[data-a="removeLogo"]')
    page.wait_for_selector("#logo_preview", state="detached")
    assert not settings(srv).get("logo")
    assert page.locator('[data-a="removeLogo"]').count() == 0


def test_hint_for_unprintable_stored_logo(page, srv):
    h = headers(srv)
    gif = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"
    name = httpx.post(srv.base + "/api/attachments", headers=h, files={"file": ("old.gif", gif, "image/gif")}).json()[
        "name"
    ]
    assert name.lower().endswith(".gif")
    s = settings(srv)
    s["logo"] = name
    assert httpx.put(srv.base + "/api/settings", headers=h, json=s).status_code == 200
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_hint")
    assert "cannot be printed" in page.inner_text("#logo_hint")
