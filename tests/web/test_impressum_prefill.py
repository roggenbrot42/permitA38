"""Browser test of the customer form 'Prefill from website Impressum' (real UI, real API, fake website)."""

import glob
import os
import socket
import threading
import time

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
import uvicorn  # noqa: E402

from permita38.api.server import get_app  # noqa: E402
from permita38.core.config import Config  # noqa: E402
from permita38.core.netsafe import FetchError  # noqa: E402
from tests.backend.impressum_helpers import FakeFetcher, page  # noqa: E402

PASSWORD = "correct horse battery"
URL = "https://beispiel.example/impressum"
EVIL = (
    b"<html><head><title>Impressum</title></head><body><h1>Impressum</h1><p>Angaben gem\xc3\xa4\xc3\x9f \xc2\xa7 5 DDG</p>"
    b"<p><img src=x onerror=alert(1)>Evil GmbH<br>Weg 1<br>12345 <script>alert(2)</script>Stadt</p>"
    b"<p>E-Mail: a@example.com</p></body></html>"
)


def chromium() -> str | None:
    env = os.environ.get("PERMITA38_CHROMIUM")
    if env and os.path.exists(env):
        return env
    hits = glob.glob("/opt/pw-browsers/chromium*/chrome-linux/chrome") + glob.glob("/opt/pw-browsers/chromium")
    return next((h for h in hits if os.path.isfile(h)), None)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, tmp_path, **cfg):
        self.config = Config(data_dir=tmp_path / "data", secret_key="test-secret-key-0123456789", web_dir=None, **cfg)
        from permita38.core.config import default_web_dir

        self.config.web_dir = default_web_dir()
        self.app = get_app(self.config)
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 15
        while not self.server.started:
            assert time.time() < deadline, "server did not start"
            time.sleep(0.05)
        self.base = f"http://127.0.0.1:{self.port}"
        httpx.post(self.base + "/api/auth/setup", json={"username": "boss", "name": "Boss", "password": PASSWORD})
        r = httpx.post(self.base + "/api/auth/login", json={"username": "boss", "password": PASSWORD})
        self.token = r.json()["token"]

    def fake(self, routes):
        self.app.state.services.impressum.fetcher = FakeFetcher(routes)

    def customers(self):
        return httpx.get(self.base + "/api/customers", headers={"Authorization": "Bearer " + self.token}).json()

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture
def ui(browser, srv):
    ctx = browser.new_context(viewport={"width": 1200, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: (problems.append("dialog: " + d.message), d.dismiss()))
    pg.on(
        "console", lambda m: m.type == "error" and "status of" not in m.text and problems.append("console: " + m.text)
    )
    pg.goto(srv.base + "/#/customers")
    pg.wait_for_selector("[data-a=editCustomer]")
    pg.click("[data-a=editCustomer]")
    pg.wait_for_selector("#imp_url")
    yield pg
    assert problems == []
    ctx.close()


def lookup(pg, url=URL):
    pg.fill("#imp_url", url)
    pg.press("#imp_url", "Enter")  # Enter must run the lookup and must NOT submit the customer form
    pg.wait_for_selector("#imp_out .imp-summary, #imp_out .imp-error", timeout=10000)


def test_prefill_fills_empty_fields_with_badges_and_does_not_submit(srv, ui):
    srv.fake({URL: page(URL, "gmbh_register")})
    lookup(ui)
    assert ui.input_value("#f_name") == "Beispiel Software GmbH"
    assert ui.input_value("#f_street") == "Musterstraße 12"
    assert (ui.input_value("#f_zip"), ui.input_value("#f_city")) == ("80331", "München")
    assert ui.input_value("#f_email") == "info@example.com" and ui.input_value("#f_phone") == "+49891234567"
    assert ui.input_value("#f_vat_id") == "DE123456788"
    notes = ui.input_value("#f_notes")
    assert "Handelsregister: HRB 123456, Amtsgericht München" in notes and "Geschäftsführer: Erika Mustermann" in notes
    badge = ui.locator("label[for=f_name] .imp-badge")
    assert badge.count() == 1 and badge.inner_text() == "from Impressum"
    assert "Confidence" in (badge.get_attribute("title") or "")
    assert ui.locator("#imp_out a").get_attribute("href") == URL  # source link
    assert ui.locator("#m-save").is_visible()  # the modal is still open: Enter did not save
    assert srv.customers() == []  # and nothing was stored by the lookup


def test_existing_input_is_kept_and_replacing_needs_a_click(srv, ui):
    srv.fake({URL: page(URL, "gmbh_register")})
    ui.fill("#f_name", "Mein eigener Name")
    ui.fill("#f_city", "Hamburg")
    lookup(ui)
    assert ui.input_value("#f_name") == "Mein eigener Name" and ui.input_value("#f_city") == "Hamburg"
    assert ui.input_value("#f_street") == "Musterstraße 12"  # empty fields were filled
    use = ui.locator("#imp_out button[data-a=impUse][data-k=name]")
    assert use.count() == 1
    use.click()
    assert ui.input_value("#f_name") == "Beispiel Software GmbH" and ui.input_value("#f_city") == "Hamburg"
    assert ui.locator("label[for=f_name] .imp-badge").count() == 1


def test_saving_stores_the_confirmed_values(srv, ui):
    srv.fake({URL: page(URL, "gmbh_register")})
    lookup(ui)
    ui.click("#m-save")
    deadline = time.time() + 10
    while not srv.customers() and time.time() < deadline:
        time.sleep(0.1)
    (customer,) = srv.customers()
    assert customer["name"] == "Beispiel Software GmbH" and customer["vat_id"] == "DE123456788"
    assert customer["zip"] == "80331" and "Handelsregister" in customer["notes"]


def test_hostile_page_content_is_rendered_as_text_only(srv, ui):
    srv.fake({URL: page(URL, body=EVIL)})
    lookup(ui)
    assert ui.locator("#modal-root img, #modal-root script").count() == 0
    assert "<" not in ui.input_value("#f_name") and "Evil GmbH" in ui.input_value("#f_name")
    # (the fixture 'ui' fails the test on any alert() dialog or page error)


def test_friendly_errors(srv, ui):
    srv.fake({URL: FetchError("timeout", "The website did not answer in time.")})
    lookup(ui)
    assert "did not answer in time" in ui.locator("#imp_out .imp-error").inner_text()
    assert ui.input_value("#f_name") == ""
    lookup(ui, "http://127.0.0.1/")  # the real service refuses internal addresses (fake fetcher is bypassed by parse)
    srv.fake({})
    lookup(ui, "https://leer.example/")
    assert ui.locator("#imp_out .imp-error").count() == 1


def test_row_is_hidden_when_the_feature_is_switched_off(browser, tmp_path):
    s = Server(tmp_path, features={"impressum_lookup": False})
    try:
        ctx = browser.new_context()
        ctx.add_init_script(f"localStorage.setItem('permita38.token', '{s.token}')")
        pg = ctx.new_page()
        pg.goto(s.base + "/#/customers")
        pg.wait_for_selector("[data-a=editCustomer]")
        pg.click("[data-a=editCustomer]")
        pg.wait_for_selector("#f_name")
        assert pg.locator("#imp_url").count() == 0
        ctx.close()
    finally:
        s.stop()
