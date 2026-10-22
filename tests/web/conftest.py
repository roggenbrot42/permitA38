"""Everything under tests/web drives the web UI (or the live HTTP server) and carries the `web` marker."""

import glob
import os

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if "/tests/web/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.web)


def _chromium_path() -> str | None:
    env = os.environ.get("PERMITA38_CHROMIUM")
    if env and os.path.exists(env):
        return env
    for pattern in ("/opt/pw-browsers/chromium*/chrome-linux/chrome", "/opt/pw-browsers/chromium"):
        for cand in sorted(glob.glob(pattern)):
            if os.path.isfile(cand):
                return cand
    return None


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    exe = _chromium_path()
    pw = sync_api.sync_playwright().start()
    try:
        kwargs = {"headless": os.environ.get("PERMITA38_E2E_HEADED") != "1"}
        if exe:
            kwargs["executable_path"] = exe
        try:
            b = pw.chromium.launch(**kwargs)
        except Exception as e:  # browser missing
            pytest.skip(f"no usable Chromium for Playwright: {e}")
        yield b
        b.close()
    finally:
        pw.stop()
