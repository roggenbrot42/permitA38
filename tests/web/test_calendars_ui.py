"""Browser tests of Settings → Calendars: real UI, real API, fake calendar server (the fetcher injection point)."""

import glob
import os
import socket
import threading
import time
from datetime import date, datetime

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
import uvicorn  # noqa: E402

from permita38.api.server import get_app  # noqa: E402
from permita38.core.config import Config, default_web_dir  # noqa: E402
from permita38.core.netsafe import FetchError  # noqa: E402
from tests.backend.calendar_helpers import SECRET_TOKEN, SECRET_URL, FakeFetcher, ics, next_weekday, timed  # noqa: E402

PASSWORD = "correct horse battery"


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
    def __init__(self, tmp_path, fetcher):
        self.fetcher = fetcher
        config = Config(data_dir=tmp_path / "data", secret_key="test-secret-key-0123456789", web_dir=default_web_dir())
        self.app = get_app(config, calendar_fetcher=fetcher)
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
        self.token = httpx.post(self.base + "/api/auth/login", json={"username": "boss", "password": PASSWORD}).json()[
            "token"
        ]

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)


@pytest.fixture
def fetcher():
    return FakeFetcher(ics())


@pytest.fixture
def srv(tmp_path, fetcher):
    s = Server(tmp_path, fetcher)
    yield s
    s.stop()


@pytest.fixture
def ui(browser, srv):
    ctx = browser.new_context(viewport={"width": 1200, "height": 1000})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on(
        "console", lambda m: m.type == "error" and "status of" not in m.text and problems.append("console: " + m.text)
    )
    pg.on("dialog", lambda d: d.accept())
    pg.goto(srv.base + "/#/settings")
    pg.wait_for_selector("#cal_link")
    yield pg
    assert problems == [], problems
    ctx.close()


def connect_link(pg, name="Work", link=SECRET_URL):
    pg.fill("#cal_name", name)
    pg.fill("#cal_link", link)
    pg.click("[data-a=calConnect]")


def test_connect_by_link_shows_only_the_masked_host(ui):
    connect_link(ui)
    ui.wait_for_selector("#calendars table td b:has-text('Work')")
    body = ui.content()
    assert "calendar.example.test/…/****" in body
    assert SECRET_TOKEN not in body  # neither the list nor the form keeps the link
    assert ui.input_value("#cal_link") == ""


def test_upload_and_preview_show_busy_time_but_no_titles(ui, tmp_path):
    monday = next_weekday(date.today(), 0)  # a working day inside the 14-day preview
    event = timed("ui1@example.test", "Private dentist", _dt(monday, 10), _dt(monday, 11))
    path = tmp_path / "export.ics"
    path.write_bytes(ics(event))
    ui.fill("#cal_name", "Exported")
    ui.set_input_files("#cal_file", str(path))
    ui.click("[data-a=calUpload]")
    ui.wait_for_selector("#calendars table td b:has-text('Exported')")
    ui.click("[data-a=calPreview]")
    ui.wait_for_selector("#cal-busy table")
    assert "1 h" in ui.inner_text("#cal-busy")
    preview = ui.inner_text("#cal-preview")
    assert "Busy (title not stored)" in preview and "Private dentist" not in ui.content()


def test_blocked_address_shows_an_error_and_never_the_link(ui, fetcher):
    fetcher.error = FetchError("blocked_address", "This address is not allowed (private or internal network).")
    connect_link(ui)
    ui.wait_for_selector("#cal-err:not([hidden])")
    assert "not allowed" in ui.inner_text("#cal-err")
    assert SECRET_TOKEN not in ui.content()
    assert ui.input_value("#cal_link") == SECRET_URL  # a failed attempt keeps the pasted link for correcting it


def test_invalid_file_is_reported(ui, tmp_path):
    path = tmp_path / "page.ics"
    path.write_bytes(b"<html>not a calendar</html>")
    ui.fill("#cal_name", "Broken")
    ui.set_input_files("#cal_file", str(path))
    ui.click("[data-a=calUpload]")
    ui.wait_for_selector("#cal-err:not([hidden])")
    assert "iCalendar" in ui.inner_text("#cal-err")


def test_delete_asks_first_and_then_removes_the_calendar(ui):
    connect_link(ui, name="Temporary")
    ui.wait_for_selector("#calendars table td b:has-text('Temporary')")
    ui.click("[data-a=calDelete]")
    ui.wait_for_selector("#calendars table td b:has-text('Temporary')", state="detached")
    assert "No calendar connected yet" in ui.inner_text("#calendars")


def _dt(day: date, hour: int) -> datetime:
    return datetime.combine(day, datetime.min.time()).replace(hour=hour)
