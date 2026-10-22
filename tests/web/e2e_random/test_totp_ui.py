"""Two-factor authentication through the real browser UI: enrol, sign in with a code, recovery code, wrong code.

The TOTP codes are computed here with an independent RFC 6238 implementation (not the app's code)."""

import base64
import hashlib
import hmac
import re
import struct
import time

import pytest

from .conftest import PASSWORD

pytestmark = [pytest.mark.web, pytest.mark.e2e_random]


def totp(secret_b32: str, step: int) -> str:
    key = base64.b32decode(secret_b32.replace(" ", "").upper())
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    value = (struct.unpack(">I", mac[off : off + 4])[0] & 0x7FFFFFFF) % 10**6
    return f"{value:06d}"


def sign_in(page, url, user="boss", password=PASSWORD):
    page.goto(url)
    page.fill("#l_user", user)
    page.fill("#l_pw", password)
    page.click("#l_go")


def test_enrol_sign_in_with_code_and_recovery_code(live_server, api, make_page):
    api.setup_boss()
    page, watch = make_page()

    # --- enrol through the account dialog
    sign_in(page, live_server.url)
    page.wait_for_selector("[data-a=twoFactor]")
    page.click("[data-a=twoFactor]")
    page.fill("#tf_pw", PASSWORD)
    page.click("#tf-go")
    page.wait_for_selector("#tf-secret")
    assert page.locator("img[alt^='QR code']").is_visible()
    secret = page.inner_text("#tf-secret")
    now_step = int(time.time()) // 30
    page.fill("#tf_code", totp(secret, now_step))
    page.click("#tf-go")
    page.wait_for_selector("#tf-codes")
    codes = re.findall(r"\S+", page.inner_text("#tf-codes"))
    assert len(codes) == 10 and len(set(codes)) == 10
    assert page.locator("#tf-done").is_disabled()  # must confirm the codes were stored
    page.check("#tf-ok")
    page.click("#tf-done")
    # enabling 2FA ends all older sessions, including the API helper token: it must now be refused
    assert api.http.get("/auth/totp/status").status_code == 401

    # --- sign out, password alone is no longer enough
    page.click("[data-a=logout]")
    sign_in(page, live_server.url)
    page.wait_for_selector("#m_code")
    assert not page.locator("[data-a=twoFactor]").count()

    # --- wrong code is rejected and shows an error, the user stays on the code screen
    page.fill("#m_code", "000000" if totp(secret, now_step + 1) != "000000" else "111111")
    page.wait_for_selector("#auth-err:not([hidden])")
    assert page.locator("#m_code").is_visible()

    # --- a correct code for the next time step is accepted (the enrolment code's step is replay-protected)
    page.fill("#m_code", totp(secret, int(time.time()) // 30 + 1))
    page.wait_for_selector("[data-a=twoFactor]")

    # --- recovery code: works once
    page.click("[data-a=logout]")
    sign_in(page, live_server.url)
    page.wait_for_selector("#m_code")
    page.click("#m_switch")
    page.fill("#m_code", codes[0])
    page.press("#m_code", "Enter")
    page.wait_for_selector("[data-a=twoFactor]")
    page.click("[data-a=logout]")
    sign_in(page, live_server.url)
    page.wait_for_selector("#m_code")
    page.click("#m_switch")
    page.fill("#m_code", codes[0])
    page.press("#m_code", "Enter")
    page.wait_for_selector("#auth-err:not([hidden])")
    assert not page.locator("[data-a=twoFactor]").count()

    watch.check()
