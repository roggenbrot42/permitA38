"""Fixtures for the random end-to-end tests: a real uvicorn server on a free port, a Chromium page with error
collectors and an httpx client for reading the ground truth."""

import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import uvicorn

from permita38.api.server import get_app
from permita38.core.config import Config, default_web_dir

PASSWORD = "correct horse battery"


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e_random: seeded random user simulations (browser and API)")


# ---------------------------------------------------------------- server
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, data_dir: Path):
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.config = Config(data_dir=data_dir, secret_key="e2e-secret-key", web_dir=default_web_dir())
        self.app = get_app(self.config)
        self._server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self):
        self._thread.start()
        deadline = time.time() + 15
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.02)

    def stop(self):
        self._server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def live_server(tmp_path):
    srv = LiveServer(tmp_path / "data")
    srv.start()
    yield srv
    srv.stop()


class Api:
    """Thin authenticated httpx wrapper used as the source of ground truth."""

    def __init__(self, base_url: str):
        self.http = httpx.Client(base_url=base_url + "/api", timeout=15)

    def login(self, username: str, password: str = PASSWORD) -> httpx.Response:
        r = self.http.post("/auth/login", json={"username": username, "password": password})
        if r.status_code == 200:
            self.http.headers["Authorization"] = "Bearer " + r.json()["token"]
        return r

    def setup_boss(self, username="boss", name="Boss Test", password=PASSWORD):
        r = self.http.post("/auth/setup", json={"username": username, "name": name, "password": password})
        assert r.status_code == 200, r.text
        assert self.login(username, password).status_code == 200

    def get(self, path, **params):
        r = self.http.get(path, params=params)
        assert r.status_code == 200, f"GET {path}: {r.status_code} {r.text}"
        return r.json()

    def post(self, path, body=None):
        r = self.http.post(path, json=body if body is not None else {})
        assert r.status_code < 300, f"POST {path}: {r.status_code} {r.text}"
        return r.json() if r.content else None

    def close(self):
        self.http.close()


@pytest.fixture
def api(live_server):
    a = Api(live_server.url)
    yield a
    a.close()


# -------------------------------------------------------------- browser


@dataclass
class Watch:
    """Collects everything that must never happen during a test."""

    problems: list[str] = field(default_factory=list)
    http_4xx: list[str] = field(default_factory=list)
    writes: int = 0  # successful POST/PUT/DELETE calls (proves the simulated user really changed data)
    xss_dialogs: list[str] = field(default_factory=list)
    accept_confirm: float = 1.0  # probability of accepting window.confirm() (the app's delete prompts)
    rng: object = None
    ignore: list = field(default_factory=list)  # regexes of known console errors (each has a xfail regression)

    def check(self):
        assert not self.problems, "browser problems:\n  " + "\n  ".join(self.problems)
        assert not self.xss_dialogs, "injected script ran: " + repr(self.xss_dialogs)


@pytest.fixture
def make_page(browser, live_server):
    """Factory: new isolated browser context + page wired to a Watch."""
    contexts = []

    def factory(token: str | None = None):
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-GB")
        ctx.set_default_timeout(8000)
        if token:
            ctx.add_init_script(f"try{{localStorage.setItem('permita38.token','{token}')}}catch(e){{}}")
        page = ctx.new_page()
        watch = Watch()

        def on_console(msg):
            if msg.type != "error":
                return
            text = msg.text
            # the browser logs every failed fetch; 4xx are legitimate API answers (validation, auth)
            if "Failed to load resource" in text and "status of 4" in text:
                return
            if any(rx.search(text) for rx in watch.ignore):
                return
            watch.problems.append("console.error: " + text[:300])

        def on_response(resp):
            if resp.status >= 500:
                watch.problems.append(f"HTTP {resp.status} {resp.request.method} {resp.url}")
            elif resp.status < 400 and resp.request.method != "GET" and "/api/" in resp.url:
                watch.writes += 1
            elif resp.status >= 400:
                watch.http_4xx.append(f"{resp.status} {resp.request.method} {resp.url.replace(live_server.url, '')}")

        def on_dialog(dlg):
            if "XSS_MARK" in dlg.message or dlg.type == "alert":
                watch.xss_dialogs.append(f"{dlg.type}: {dlg.message[:100]}")
                dlg.dismiss()
            elif dlg.type == "confirm":
                accept = watch.rng.random() < watch.accept_confirm if watch.rng else True
                dlg.accept() if accept else dlg.dismiss()
            else:
                dlg.accept()

        page.on("pageerror", lambda e: watch.problems.append("pageerror: " + str(e)[:300]))
        page.on("console", on_console)
        page.on("response", on_response)
        page.on("dialog", on_dialog)
        contexts.append(ctx)
        return page, watch

    yield factory
    for c in contexts:
        c.close()
