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


def store_logo(srv, png=None):
    h = headers(srv)
    name = httpx.post(
        srv.base + "/api/attachments", headers=h, files={"file": ("old.png", png or make_png(), "image/png")}
    ).json()["name"]
    s = settings(srv)
    s["logo"] = name
    assert httpx.put(srv.base + "/api/settings", headers=h, json=s).status_code == 200
    return name


def preview_state(page):
    return page.evaluate(
        """() => { const i = document.querySelector('#logo_preview');
          return i ? { src: i.src, ok: i.complete && i.naturalWidth > 0 } : null; }"""
    )


def preview_pixels(page):
    """Draw the preview <img> to a canvas (blob URLs are same-origin) and sample alpha of both halves."""
    return page.evaluate(
        """() => { const i = document.querySelector('#logo_preview');
          const cv = document.createElement('canvas'); cv.width = i.naturalWidth; cv.height = i.naturalHeight;
          const cx = cv.getContext('2d'); cx.drawImage(i, 0, 0);
          const a = x => cx.getImageData(x, Math.floor(cv.height / 2), 1, 1).data[3];
          return { w: cv.width, left: a(Math.floor(cv.width / 4)), right: a(Math.floor(cv.width * 3 / 4)) }; }"""
    )


def test_remove_logo_is_applied_by_save_together_with_other_edits(page, srv):
    store_logo(srv)
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_preview")
    assert page.locator("#logo_hint").count() == 0  # a stored .png is printable
    page.fill("#f_company_name", "Demo Betrieb Neu")
    page.click('[data-a="removeLogo"]')
    page.wait_for_selector("#logo_preview", state="detached")
    assert "will be removed" in page.inner_text("#logo_note")
    assert settings(srv)["logo"]  # nothing saved yet
    save(page)
    s = settings(srv)
    assert s["company_name"] == "Demo Betrieb Neu"
    assert not s.get("logo")
    page.wait_for_selector('[data-a="removeLogo"]', state="detached")
    page.wait_for_selector("#logo_preview", state="detached")


def test_remove_then_choose_png_then_save_stores_the_new_png(page, srv):
    old = store_logo(srv)
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_preview")
    page.click('[data-a="removeLogo"]')
    page.wait_for_selector("#logo_preview", state="detached")
    page.set_input_files("#logo_file", [{"name": "new.png", "mimeType": "image/png", "buffer": make_png(60, 30)}])
    page.wait_for_selector("#logo_preview[src^='blob:']")
    save(page)
    logo = settings(srv)["logo"]
    assert logo and logo != old and logo.lower().endswith(".png")
    page.reload()
    page.wait_for_selector("#logo_preview")
    assert "attachments/" in page.get_attribute("#logo_preview", "src")
    assert page.eval_on_selector("#logo_preview", "e => e.complete && e.naturalWidth > 0")
    assert decode_pixels(page, logo)["w"] == 60


def test_cancelled_dialog_restores_stored_state(page, srv):
    store_logo(srv)
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_preview")
    stored = page.get_attribute("#logo_preview", "src")
    page.set_input_files("#logo_file", [{"name": "logo.png", "mimeType": "image/png", "buffer": make_png()}])
    page.wait_for_selector("#logo_preview[src^='blob:']")
    page.set_input_files("#logo_file", [])
    page.wait_for_selector("#logo_preview:not([src^='blob:'])")
    assert page.get_attribute("#logo_preview", "src") == stored
    assert "A logo is set" in page.inner_text("#logo_note")


def test_cancelled_dialog_without_stored_logo_shows_no_preview(page, srv):
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_file", state="attached")
    page.set_input_files("#logo_file", [{"name": "logo.png", "mimeType": "image/png", "buffer": make_png()}])
    page.wait_for_selector("#logo_preview")
    page.set_input_files("#logo_file", [])
    page.wait_for_selector("#logo_preview", state="detached")
    assert page.is_hidden("#logo_box")
    assert page.inner_text("#logo_note") == ""


def test_bad_file_after_valid_one_shows_stored_logo_not_a_broken_image(page, srv):
    store_logo(srv)
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_preview")
    page.set_input_files("#logo_file", [{"name": "logo.png", "mimeType": "image/png", "buffer": make_png()}])
    page.wait_for_selector("#logo_preview[src^='blob:']")
    page.set_input_files("#logo_file", [{"name": "bad.png", "mimeType": "image/png", "buffer": b"not an image"}])
    page.wait_for_selector("#logo_note:has-text('cannot be used as a logo')")
    st = preview_state(page)
    assert st is None or (st["ok"] and not st["src"].startswith("blob:"))


def test_save_after_bad_file_changes_nothing(page, srv):
    store_logo(srv)
    before = settings(srv)
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_preview")
    page.set_input_files("#logo_file", [{"name": "bad.png", "mimeType": "image/png", "buffer": b"not an image"}])
    page.wait_for_selector("#logo_note:has-text('cannot be used as a logo')")
    save(page)
    assert settings(srv) == before


def test_svg_with_only_a_viewbox_gets_the_viewbox_size(page, srv):
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80">'
        b'<rect x="0" y="0" width="100" height="80" fill="#2060c0"/></svg>'
    )
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_file", state="attached")
    page.set_input_files("#logo_file", [{"name": "vb.svg", "mimeType": "image/svg+xml", "buffer": svg}])
    page.wait_for_selector("#logo_preview")
    save(page)
    px = decode_pixels(page, settings(srv)["logo"])
    assert (px["w"], px["h"]) == (900, 360)
    assert px["right"][3] <= 10
    assert px["left"][3] >= 245


def test_svg_with_percent_size_and_viewbox_gets_the_viewbox_size(page, srv):
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" viewBox="0 0 200 80">'
        b'<rect x="0" y="0" width="100" height="80" fill="#2060c0"/></svg>'
    )
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_file", state="attached")
    page.set_input_files("#logo_file", [{"name": "pct.svg", "mimeType": "image/svg+xml", "buffer": svg}])
    page.wait_for_selector("#logo_preview")
    save(page)
    px = decode_pixels(page, settings(srv)["logo"])
    assert (px["w"], px["h"]) == (900, 360)
    assert px["right"][3] <= 10
    assert px["left"][3] >= 245


def test_preview_blob_is_transparent_before_saving(page, srv):
    page.goto(srv.base + "/#/settings")
    page.wait_for_selector("#logo_file", state="attached")
    page.set_input_files("#logo_file", [{"name": "logo.png", "mimeType": "image/png", "buffer": make_png()}])
    page.wait_for_selector("#logo_preview[src^='blob:']")
    px = preview_pixels(page)
    assert px["w"] == 40
    assert px["right"] <= 10
    assert px["left"] >= 245


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
