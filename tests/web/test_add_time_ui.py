"""Adding time manually (issues #64, #65, #66) against the real server, demo data only."""

import datetime as dt

import httpx

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


def _set(pg, sel, value):
    pg.fill(sel, value)
    pg.dispatch_event(sel, "change")


def _vals(pg):
    return {k: pg.input_value("#f_" + k) for k in ("date", "start", "end_date", "end", "dur")}


def _open(pg, srv):
    pg.goto(srv.base + "/#/today")
    pg.click("[data-a=addTime]")
    pg.wait_for_selector("#f_end_date")


def test_dialog_duration_after_start_sets_stop_on_the_next_day(browser, srv):  # #121 example 1
    ctx, pg, problems = open_as_boss(browser, srv)
    _open(pg, srv)
    _set(pg, "#f_date", "2026-11-20")
    _set(pg, "#f_start", "22:00")
    _set(pg, "#f_dur", "8:00")
    v = _vals(pg)
    assert (v["end_date"], v["end"], v["dur"]) == ("2026-11-21", "06:00", "8:00")
    assert not problems


def test_dialog_start_and_stop_give_the_duration(browser, srv):  # #121 examples 2 and 4
    ctx, pg, problems = open_as_boss(browser, srv)
    _open(pg, srv)
    _set(pg, "#f_date", "2026-11-20")
    _set(pg, "#f_start", "09:00")
    _set(pg, "#f_end", "12:30")
    assert _vals(pg)["dur"] == "3:30"
    _set(pg, "#f_start", "23:30")
    _set(pg, "#f_end_date", "2026-11-21")
    _set(pg, "#f_end", "00:15")
    assert _vals(pg)["dur"] == "0:45"
    assert not problems


def test_dialog_stop_and_duration_give_the_start(browser, srv):  # #121 example 3
    ctx, pg, problems = open_as_boss(browser, srv)
    _open(pg, srv)
    _set(pg, "#f_date", "2026-11-20")
    _set(pg, "#f_dur", "1:30")
    assert pg.input_value("#f_start") == "" and pg.input_value("#f_end") == ""
    _set(pg, "#f_end", "17:00")
    v = _vals(pg)
    assert (v["date"], v["start"], v["dur"]) == ("2026-11-20", "15:30", "1:30")
    assert not problems


def test_dialog_stop_before_start_is_invalid_and_not_saved(browser, srv):  # #121 example 5
    ctx, pg, problems = open_as_boss(browser, srv)
    _open(pg, srv)
    _set(pg, "#f_date", "2026-11-20")
    _set(pg, "#f_start", "10:00")
    _set(pg, "#f_end", "09:00")
    assert pg.input_value("#f_dur") == ""
    assert pg.get_attribute("#f_end", "aria-invalid") == "true"
    pg.click("#m-save")
    expect(pg.locator("#modal-root .modal")).to_have_count(1)
    assert srv_entries(srv) == []
    assert not problems


def test_dialog_moving_the_start_date_moves_the_stop_date(browser, srv):  # #121 example 6
    ctx, pg, problems = open_as_boss(browser, srv)
    _open(pg, srv)
    _set(pg, "#f_date", "2026-11-20")
    _set(pg, "#f_start", "08:00")
    _set(pg, "#f_end", "10:00")
    _set(pg, "#f_date", "2026-11-19")
    v = _vals(pg)
    assert (v["end_date"], v["dur"]) == ("2026-11-19", "2:00")
    assert not problems


def srv_entries(srv):
    return httpx.get(srv.base + "/api/entries", headers={"Authorization": "Bearer " + srv.token}).json()


def test_dialog_overnight_entry_is_stored_and_edited_with_its_stop_date(browser, srv):
    ctx, pg, problems = open_as_boss(browser, srv)
    _open(pg, srv)
    _set(pg, "#f_date", "2026-11-20")
    _set(pg, "#f_start", "22:00")
    _set(pg, "#f_dur", "8:00")
    pg.fill("#f_note", "night shift")
    pg.click("#m-save")
    expect(pg.locator("#modal-root .modal")).to_have_count(0)
    (e,) = srv_entries(srv)
    assert (e["date"], e["start"], e["end"], e["minutes"]) == ("2026-11-20", "22:00", "06:00", 480)
    assert "end_date" not in e
    pg.goto(srv.base + "/#/time/2026-11-16")
    pg.click(f'tr[data-a=editEntry][data-id="{e["id"]}"]')
    pg.wait_for_selector("#f_end_date")
    v = _vals(pg)
    assert (v["date"], v["end_date"], v["start"], v["end"], v["dur"]) == ("2026-11-20", "2026-11-21", "22:00", "06:00", "8:00")
    assert not problems
