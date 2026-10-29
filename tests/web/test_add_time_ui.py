"""Adding time manually (issues #64, #65, #66) against the real server, demo data only."""

import datetime as dt

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_impressum_prefill import Server  # noqa: E402  (shared harness)
from tests.web.test_tokens_live import open_as_boss  # noqa: E402


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def test_manual_time_is_saved_and_listed(browser, srv):
    ctx, pg, problems = open_as_boss(browser, srv)
    pg.goto(srv.base + "/#/today")
    pg.click("[data-a=addTime]")
    box = pg.locator("#f_date").bounding_box()
    assert box and box["width"] >= 150  # #65: date is readable
    pg.fill("#f_start", "09:00")
    pg.fill("#f_end", "10:30")
    pg.fill("#f_note", "manual entry")
    pg.click("#m-save")
    expect(pg.locator("#modal-root .modal")).to_have_count(0)
    expect(pg.locator("body")).to_contain_text("manual entry")  # #64
    assert not problems


def test_future_date_is_refused(browser, srv):
    ctx, pg, problems = open_as_boss(browser, srv)
    pg.goto(srv.base + "/#/today")
    pg.click("[data-a=addTime]")
    assert pg.get_attribute("#f_date", "max") == dt.date.today().isoformat()
    pg.evaluate("document.querySelector('#f_date').value = '2999-01-01'")
    pg.fill("#f_start", "09:00")
    pg.fill("#f_end", "10:00")
    pg.click("#m-save")
    expect(pg.locator("#modal-root .modal")).to_have_count(1)
    assert not problems


def test_past_day_entry_jumps_to_its_week(browser, srv):
    ctx, pg, problems = open_as_boss(browser, srv)
    pg.goto(srv.base + "/#/today")
    pg.click("[data-a=addTime]")
    pg.fill("#f_date", (dt.date.today() - dt.timedelta(days=10)).isoformat())
    pg.fill("#f_start", "09:00")
    pg.fill("#f_end", "10:00")
    pg.fill("#f_note", "older entry")
    pg.click("#m-save")
    expect(pg.locator("body")).to_contain_text("older entry")
    assert "#/time/" in pg.url
    assert not problems
