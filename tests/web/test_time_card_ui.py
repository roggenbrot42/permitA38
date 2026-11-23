"""Browser tests of the time tracking row on the Today page (docs/specs/time-tracking-element.md). Live server, fake names."""

import json

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server  # noqa: E402,F401  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def api(srv, method, path, body=None):
    r = httpx.request(method, srv.base + "/api/" + path, json=body, headers={"Authorization": "Bearer " + srv.token})
    assert r.status_code < 300, r.text
    return r.json()


@pytest.fixture
def pg(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    page = ctx.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    asked: list[str] = []
    answer = {"accept": False}

    def on_dialog(d):
        if d.message.startswith("Less than a minute tracked"):
            asked.append(d.message)
            d.accept() if answer["accept"] else d.dismiss()
        else:
            problems.append("dialog: " + d.message)
            d.dismiss()

    page.on("dialog", on_dialog)
    page.asked, page.answer = asked, answer  # type: ignore[attr-defined]
    page.goto(srv.base + "/#/today")
    page.wait_for_selector("#t_proj")
    yield page
    assert problems == []
    ctx.close()


def test_default_row_is_manual_with_description_project_task_and_add(pg, srv):
    assert pg.is_visible("#t_note") and pg.is_visible("#t_proj") and pg.is_visible("#t_task")
    assert pg.get_attribute("[data-a=timeMode][data-mode=manual]", "aria-pressed") == "true"
    assert pg.get_attribute("[data-a=timeMode][data-mode=tracker]", "aria-pressed") == "false"
    for sel in ("#t_date", "#t_start", "#t_end", "#t_dur"):
        assert pg.is_visible(sel), sel
    assert pg.is_visible("[data-a=timerAdd]")
    assert pg.locator("[data-a=timerStart]").count() == 0  # Start belongs to tracker mode


def test_project_picker_lists_non_archived_projects_and_matches_typed_name(pg, srv):
    customer = api(srv, "POST", "customers", {"name": "Kunde Alpha"})
    api(srv, "POST", "projects", {"name": "Projekt Alpha", "rate": 95, "customer_id": customer["id"]})
    api(srv, "POST", "projects", {"name": "Projekt Intern", "rate": 95})  # no customer: internal, labelled as such
    api(srv, "POST", "projects", {"name": "Projekt Archiv", "status": "archived"})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    options = pg.locator("#t_proj_list option").evaluate_all("els => els.map(e => e.value)")
    assert "Projekt Alpha" in options
    assert "Projekt Intern (internal)" in options
    assert "Projekt Archiv" not in options


def test_manual_add_computes_duration_and_saves_entry(pg, srv):
    api(srv, "POST", "projects", {"name": "Projekt Beta"})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    pg.fill("#t_start", "09:00")
    pg.dispatch_event("#t_start", "change")
    pg.fill("#t_end", "10:30")
    pg.dispatch_event("#t_end", "change")
    assert pg.input_value("#t_dur") == "1:30"  # hm() format, as elsewhere in the app
    pg.fill("#t_note", "Beispiel Kick-off")
    pg.fill("#t_proj", "Projekt Beta")
    pg.dispatch_event("#t_proj", "change")
    pg.click("[data-a=timerAdd]")
    pg.wait_for_function("() => document.querySelector('#t_note') && document.querySelector('#t_note').value === ''")
    entries = api(srv, "GET", "entries")
    saved = [e for e in entries if e.get("note") == "Beispiel Kick-off"]
    assert len(saved) == 1
    assert saved[0]["minutes"] == 90
    assert saved[0]["start"] == "09:00" and saved[0]["end"] == "10:30"
    assert saved[0]["project_id"] is not None
    assert saved[0]["user_id"] == api(srv, "GET", "auth/me")["id"]  # an entry without a person shows in no timesheet
    pg.wait_for_selector("tr[data-a=editEntry]:has-text('Beispiel Kick-off')")  # Today lists it
    pg.goto(srv.base + "/#/time")
    pg.wait_for_selector("tr[data-a=editEntry]:has-text('Beispiel Kick-off')")  # and so does the timesheet


def test_tracker_mode_starts_and_stops_a_timer_on_the_project(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Gamma"})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    pg.click("[data-a=timeMode][data-mode=tracker]")
    assert pg.get_attribute("[data-a=timeMode][data-mode=tracker]", "aria-pressed") == "true"
    assert pg.locator("#t_start").count() == 0
    pg.fill("#t_note", "Beispiel Tracker")
    pg.fill("#t_proj", "Projekt Gamma")
    pg.dispatch_event("#t_proj", "change")
    pg.click("[data-a=timerStart]")
    pg.wait_for_selector("[data-a=timerStop]")
    timer = api(srv, "GET", "timer")
    assert timer["running"] is True and timer["project_id"] == project["id"]
    pg.click("[data-a=timerStop]")
    pg.wait_for_selector("[data-a=timerStart]")
    assert api(srv, "GET", "timer")["running"] is False
    assert any(
        e.get("note") == "Beispiel Tracker" and e.get("project_id") == project["id"] for e in api(srv, "GET", "entries")
    )


def test_clock_toggles_to_money_on_a_project_with_a_rate(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Delta", "rate": 95})
    started = api(srv, "POST", "timer/start", {"project_id": project["id"], "note": "Beispiel Geld"})["started"]
    pg.reload()
    pg.wait_for_selector("#clock")
    # the browser clock is pinned one hour after the start: elapsed is exactly 3600 s
    pg.clock.set_fixed_time(started + 3600)  # seconds
    pg.wait_for_function("() => document.querySelector('#clock').textContent === '01:00:00'")
    clock = pg.locator("#clock")
    clock.click()
    # 95 EUR/h x 3600 s = 95,00 EUR (de-DE format, non-breaking space before the euro sign)
    assert clock.inner_text().strip() == "95,00\u00a0€"
    clock.click()
    assert clock.inner_text().strip() == "01:00:00"


def test_running_timer_in_manual_mode_shows_clock_and_stops(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Zeta"})
    api(srv, "POST", "timer/start", {"project_id": project["id"], "note": "Beispiel Laufend"})
    pg.reload()
    pg.wait_for_selector("#clock")
    assert pg.get_attribute("[data-a=timeMode][data-mode=manual]", "aria-pressed") == "true"
    assert pg.is_visible("#clock")
    assert pg.is_visible("[data-a=timerStop]")
    assert pg.locator("[data-a=timerStart]").count() == 0
    assert pg.locator("#t_proj").input_value() == "Projekt Zeta (internal)"
    pg.click("[data-a=timerStop]")
    pg.wait_for_selector("[data-a=timerAdd]")
    assert api(srv, "GET", "timer")["running"] is False
    saved = [e for e in api(srv, "GET", "entries") if e.get("note") == "Beispiel Laufend"]
    assert len(saved) == 1 and saved[0]["project_id"] == project["id"]


def test_double_click_on_add_creates_one_entry(pg, srv):
    pg.fill("#t_start", "08:00")
    pg.dispatch_event("#t_start", "change")
    pg.fill("#t_end", "09:00")
    pg.dispatch_event("#t_end", "change")
    pg.fill("#t_note", "Beispiel Doppelklick")
    held = []  # hold the POSTs so both clicks land while the first save is still pending
    pg.route("**/api/entries", lambda route, req: held.append(route) if req.method == "POST" else route.continue_())
    pg.dblclick("[data-a=timerAdd]")
    pg.wait_for_timeout(300)
    assert len(held) == 1, f"{len(held)} saves sent for one double click"
    held[0].continue_()
    pg.wait_for_function("() => document.querySelector('#t_note') && document.querySelector('#t_note').value === ''")
    pg.unroute("**/api/entries")
    saved = [e for e in api(srv, "GET", "entries") if e.get("note") == "Beispiel Doppelklick"]
    assert len(saved) == 1


def test_failed_add_keeps_the_typed_inputs(pg, srv):
    api(srv, "POST", "projects", {"name": "Projekt Eta"})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    pg.fill("#t_date", "2026-10-05")
    pg.dispatch_event("#t_date", "change")
    pg.fill("#t_start", "13:00")
    pg.dispatch_event("#t_start", "change")
    pg.fill("#t_end", "14:15")
    pg.dispatch_event("#t_end", "change")
    pg.fill("#t_note", "Beispiel Fehler")
    pg.fill("#t_proj", "Projekt Eta")
    pg.dispatch_event("#t_proj", "change")
    pg.route(
        "**/api/entries",
        lambda route, req: (
            route.fulfill(status=400, content_type="application/json", body='{"detail": "Entry rejected"}')
            if req.method == "POST"
            else route.continue_()
        ),
    )
    pg.click("[data-a=timerAdd]")
    pg.wait_for_function("() => document.querySelector('#toast').classList.contains('err')")
    pg.wait_for_timeout(1000)  # a page re-render after the failure would wipe the form within this time
    pg.unroute("**/api/entries")
    assert pg.input_value("#t_note") == "Beispiel Fehler"
    assert pg.input_value("#t_date") == "2026-10-05"
    assert pg.input_value("#t_start") == "13:00"
    assert pg.input_value("#t_end") == "14:15"
    assert pg.input_value("#t_dur") == "1:15"
    assert pg.input_value("#t_proj") == "Projekt Eta"
    assert [e for e in api(srv, "GET", "entries") if e.get("note") == "Beispiel Fehler"] == []


def test_project_and_task_stay_editable_while_a_timer_runs_and_a_change_patches(pg, srv):
    one = api(srv, "POST", "projects", {"name": "Projekt Theta"})
    two = api(srv, "POST", "projects", {"name": "Projekt Kappa"})
    task = api(srv, "POST", "tasks", {"project_id": two["id"], "name": "Aufgabe Eins"})
    timer = api(srv, "POST", "timer/start", {"project_id": one["id"], "note": "Beispiel Wechsel"})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    assert not pg.is_disabled("#t_proj") and not pg.is_disabled("#t_task")
    assert pg.input_value("#t_proj") == "Projekt Theta (internal)"
    sent = []

    def fake(route, req):
        if req.method != "PATCH":
            route.continue_()
            return
        sent.append(req.post_data_json)
        body = {**timer, "project_id": req.post_data_json["project_id"], "task_id": req.post_data_json["task_id"]}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    pg.route("**/api/timer", fake)
    pg.fill("#t_note", "Beispiel Notiz")
    pg.fill("#t_proj", "Projekt Kappa (internal)")
    pg.dispatch_event("#t_proj", "change")
    pg.wait_for_function("() => document.querySelector('#t_task').options.length > 1")
    assert sent == [{"project_id": two["id"], "task_id": None, "note": "Beispiel Notiz"}]
    pg.select_option("#t_task", task["id"])
    pg.wait_for_timeout(300)
    assert sent[1] == {"project_id": two["id"], "task_id": task["id"], "note": "Beispiel Notiz"}
    # no restart: the clock still counts from the original start and Stop is still there
    assert pg.evaluate("() => S.timer.started") == timer["started"]
    assert pg.is_visible("[data-a=timerStop]") and pg.is_visible("#clock")
    pg.unroute("**/api/timer")


def test_failed_project_change_while_running_is_a_toast(pg, srv):
    one = api(srv, "POST", "projects", {"name": "Projekt Lambda"})
    api(srv, "POST", "projects", {"name": "Projekt My"})
    api(srv, "POST", "timer/start", {"project_id": one["id"]})
    pg.reload()
    pg.wait_for_selector("#t_proj")
    pg.route(
        "**/api/timer",
        lambda route, req: (
            route.fulfill(status=400, content_type="application/json", body='{"detail": "Timer rejected"}')
            if req.method == "PATCH"
            else route.continue_()
        ),
    )
    pg.fill("#t_proj", "Projekt My (internal)")
    pg.dispatch_event("#t_proj", "change")
    pg.wait_for_function("() => document.querySelector('#toast').classList.contains('err')")
    pg.unroute("**/api/timer")


def strip_default_rate(pg):
    """The settings always carry a default rate (80); for the 'no rate anywhere' case the bootstrap payload is
    served without it. Fake test data, the server itself is unchanged."""

    def handler(route):
        resp = route.fetch()
        data = resp.json()
        data["settings"].pop("default_rate", None)
        route.fulfill(response=resp, json=data)

    pg.route("**/api/bootstrap", handler)


def test_project_without_any_rate_shows_hint_and_click_does_nothing(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Epsilon"})  # no project, customer, user or default rate
    api(srv, "POST", "timer/start", {"project_id": project["id"], "note": "Beispiel ohne Satz"})
    strip_default_rate(pg)
    pg.reload()
    pg.wait_for_selector("#clock")
    assert pg.inner_text("#t_hint") == "no rate"
    pg.locator("#clock").click()
    pg.wait_for_timeout(200)  # a click that switched to money would have re-rendered the text by now
    assert "€" not in pg.locator("#clock").inner_text()
    pg.unroute("**/api/bootstrap")


def money_amount(text):
    """'1,23 €' (de-DE, non-breaking space) -> 1.23"""
    return float(text.replace("\u00a0€", "").replace(".", "").replace(",", "."))


def test_money_uses_the_customer_rate_and_grows_while_the_timer_runs(pg, srv):
    customer = api(srv, "POST", "customers", {"name": "Kunde Iota", "rate": 80})
    project = api(srv, "POST", "projects", {"name": "Projekt Iota", "customer_id": customer["id"]})  # no own rate
    api(srv, "POST", "timer/start", {"project_id": project["id"], "note": "Beispiel Kunde"})
    pg.reload()
    pg.wait_for_selector("#clock")
    clock = pg.locator("#clock")
    clock.click()  # time -> money
    first = clock.inner_text().strip()
    assert "€" in first, first
    pg.wait_for_timeout(2500)  # the running clock keeps ticking the money text, no second click
    second = clock.inner_text().strip()
    assert "€" in second, second
    assert money_amount(second) > money_amount(first), (first, second)
    assert pg.inner_text("#t_hint") == ""
    clock.click()  # money -> time again
    assert clock.inner_text().strip().count(":") == 2


def _set(pg, sel, value):
    pg.fill(sel, value)
    pg.dispatch_event(sel, "change")


def _vals(pg):
    return {k: pg.input_value("#t_" + k) for k in ("date", "start", "end_date", "end", "dur")}


def test_duration_after_start_sets_stop_on_the_next_day(pg, srv):  # #121 example 1
    _set(pg, "#t_date", "2026-11-20")
    _set(pg, "#t_start", "22:00")
    _set(pg, "#t_dur", "8:00")
    v = _vals(pg)
    assert (v["end_date"], v["end"], v["dur"]) == ("2026-11-21", "06:00", "8:00")


def test_start_and_stop_give_the_duration(pg, srv):  # #121 example 2
    _set(pg, "#t_date", "2026-11-20")
    _set(pg, "#t_start", "09:00")
    _set(pg, "#t_end", "12:30")
    assert _vals(pg)["dur"] == "3:30"


def test_stop_and_duration_give_the_start(pg, srv):  # #121 example 3
    _set(pg, "#t_date", "2026-11-20")
    _set(pg, "#t_dur", "1:30")
    assert pg.input_value("#t_start") == "" and pg.input_value("#t_end") == ""  # nothing is guessed
    _set(pg, "#t_end", "17:00")
    v = _vals(pg)
    assert (v["date"], v["start"], v["dur"]) == ("2026-11-20", "15:30", "1:30")


def test_overnight_stop_date_gives_the_duration(pg, srv):  # #121 example 4
    _set(pg, "#t_date", "2026-11-20")
    _set(pg, "#t_start", "23:30")
    _set(pg, "#t_end_date", "2026-11-21")
    _set(pg, "#t_end", "00:15")
    assert _vals(pg)["dur"] == "0:45"


def test_stop_before_start_is_invalid_and_add_is_refused(pg, srv):  # #121 example 5
    _set(pg, "#t_date", "2026-11-20")
    _set(pg, "#t_start", "10:00")
    _set(pg, "#t_end", "09:00")
    assert pg.input_value("#t_dur") == ""
    assert pg.get_attribute("#t_end", "aria-invalid") == "true"
    pg.click("[data-a=timerAdd]")
    assert "Stop must be after start" in pg.inner_text("#toast")
    assert api(srv, "GET", "entries") == []


def test_more_than_24_hours_is_invalid(pg, srv):
    _set(pg, "#t_date", "2026-11-20")
    _set(pg, "#t_start", "08:00")
    _set(pg, "#t_end_date", "2026-11-21")
    _set(pg, "#t_end", "09:00")
    assert pg.input_value("#t_dur") == "" and pg.get_attribute("#t_end", "aria-invalid") == "true"
    assert pg.get_attribute("#t_end_date", "max") == "2026-11-21" and pg.get_attribute("#t_end_date", "min") == "2026-11-20"


def test_moving_the_start_date_moves_the_stop_date(pg, srv):  # #121 example 6
    _set(pg, "#t_date", "2026-11-20")
    _set(pg, "#t_start", "08:00")
    _set(pg, "#t_end", "10:00")
    _set(pg, "#t_date", "2026-11-19")
    v = _vals(pg)
    assert (v["date"], v["end_date"], v["start"], v["end"], v["dur"]) == ("2026-11-19", "2026-11-19", "08:00", "10:00", "2:00")


def test_overnight_entry_is_saved_with_end_before_start(pg, srv):
    _set(pg, "#t_date", "2026-11-20")
    _set(pg, "#t_start", "22:00")
    _set(pg, "#t_dur", "8:00")
    pg.click("[data-a=timerAdd]")
    pg.wait_for_function("() => document.querySelector('#toast').textContent === 'Saved'")
    (e,) = api(srv, "GET", "entries")
    assert (e["date"], e["start"], e["end"], e["minutes"]) == ("2026-11-20", "22:00", "06:00", 480)


def test_duration_only_entry_is_saved_without_times(pg, srv):
    _set(pg, "#t_date", "2026-11-20")
    _set(pg, "#t_dur", "1:30")
    pg.click("[data-a=timerAdd]")
    pg.wait_for_function("() => document.querySelector('#toast').textContent === 'Saved'")
    (e,) = api(srv, "GET", "entries")
    assert (e["date"], e.get("start"), e.get("end"), e["minutes"]) == ("2026-11-20", None, None, 90)


@pytest.mark.xfail(strict=True, reason="WP2 review: at 1400 px the mode buttons' top is 131.5 but the description field's top is 122.5 (input top 145.4, button 39.75 high, input 34.8); needs a CSS decision")
def test_card_is_one_row_in_both_modes(browser, srv):
    api(srv, "POST", "timer/start", {})
    api(srv, "POST", "timer/stop", {"discard": True})
    for width in (1400, 1920):
        for running in (False, True):
            ctx = browser.new_context(viewport={"width": width, "height": 900})
            ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
            page = ctx.new_page()
            page.goto(srv.base + "/#/today")
            page.wait_for_selector("#t_proj")
            if running:
                page.click("[data-a=timeMode][data-mode=tracker]")
                page.click("[data-a=timerStart]")
                page.wait_for_selector("[data-a=timerStop]")
            heights = []
            for mode in ("manual", "tracker", "manual"):
                page.click(f"[data-a=timeMode][data-mode={mode}]")
                where = (width, running, mode)
                note_top = page.locator(".t-note").bounding_box()["y"]  # the field box: label and input tops differ by design
                for m in ("manual", "tracker"):
                    top = page.locator(f"[data-a=timeMode][data-mode={m}]").bounding_box()["y"]
                    assert abs(top - note_top) <= 2, (where, m, top, note_top)
                heights.append(page.locator(".timer").first.evaluate("e => e.closest('.card').getBoundingClientRect().height"))
                if mode == "manual":
                    assert page.evaluate("['#t_date', '#t_end_date', '#t_start', '#t_end', '#t_dur'].every(q => { const e = document.querySelector(q); return e.scrollWidth <= e.clientWidth; })"), (where, "input clipped")
                assert page.locator("#t_note").bounding_box()["width"] >= 100, where
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert max(heights) - min(heights) <= 2, (width, running, heights)
            if running:
                page.click("[data-a=timeMode][data-mode=tracker]")
                page.click("[data-a=timerStop]")
                page.wait_for_selector("[data-a=timerStart]")
            ctx.close()


def test_add_after_typing_duration_over_start_and_stop_counts_as_edit(pg, srv):
    _set(pg, "#t_date", "2026-11-20")
    _set(pg, "#t_start", "09:00")
    _set(pg, "#t_end", "12:30")
    pg.fill("#t_dur", "1:00")  # no change event
    pg.click("[data-a=timerAdd]")
    pg.wait_for_function("() => document.querySelector('#toast').textContent === 'Saved'")
    (e,) = api(srv, "GET", "entries")
    assert (e["start"], e["end"], e["minutes"]) == ("09:00", "10:00", 60)


def test_add_after_typing_stop_with_start_set_counts_as_edit(pg, srv):
    _set(pg, "#t_date", "2026-11-20")
    _set(pg, "#t_start", "09:00")
    _set(pg, "#t_end", "12:30")
    pg.fill("#t_end", "11:00")  # no change event
    pg.click("[data-a=timerAdd]")
    pg.wait_for_function("() => document.querySelector('#toast').textContent === 'Saved'")
    (e,) = api(srv, "GET", "entries")
    assert (e["start"], e["end"], e["minutes"]) == ("09:00", "11:00", 120)


def _prefill(pg, started_local, now_local):
    """timerPrefill() with S.timer.started and the clock set to the given local times (ISO strings)."""
    return pg.evaluate(
        """([a, n]) => {
            const RealDate = Date, fixed = new RealDate(n).getTime();
            globalThis.Date = class extends RealDate { constructor(...x) { super(...(x.length ? x : [fixed])); } static now() { return fixed; } };
            try { S.timer = { running: true, started: new RealDate(a).getTime() / 1000 }; return timerPrefill(); }
            finally { globalThis.Date = RealDate; }
        }""",
        [started_local, now_local],
    )


def test_timer_prefill_across_midnight(pg):
    v = _prefill(pg, "2026-11-20T22:00:00", "2026-11-21T01:30:00")
    assert (v["date"], v["start"], v["endDate"], v["end"], v["dur"]) == ("2026-11-20", "22:00", "2026-11-21", "01:30", "3:30")


def test_timer_prefill_over_24_hours_stops_at_2359_of_the_start_day(pg):
    v = _prefill(pg, "2026-11-20T09:00:00", "2026-11-22T10:00:00")
    assert (v["date"], v["start"], v["endDate"], v["end"]) == ("2026-11-20", "09:00", "2026-11-20", "23:59")


def _stop_requests(pg):
    sent = []
    pg.on(
        "request",
        lambda r: sent.append(r.post_data_json) if r.method == "POST" and r.url.endswith("/timer/stop") else None,
    )
    return sent


def test_stop_under_a_minute_asks_and_ok_discards(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Omega"})
    api(srv, "POST", "timer/start", {"project_id": project["id"]})
    pg.reload()
    pg.wait_for_selector("[data-a=timerStop]")
    sent = _stop_requests(pg)
    pg.answer["accept"] = True
    pg.click("[data-a=timerStop]")
    pg.wait_for_selector("[data-a=timerAdd]")
    assert pg.asked == ["Less than a minute tracked. Delete it instead of booking 1 min?"]
    assert sent[0].get("discard") is True


def test_stop_under_a_minute_cancel_stops_without_discard(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Sigma"})
    api(srv, "POST", "timer/start", {"project_id": project["id"]})
    pg.reload()
    pg.wait_for_selector("[data-a=timerStop]")
    sent = _stop_requests(pg)
    pg.click("[data-a=timerStop]")
    pg.wait_for_selector("[data-a=timerAdd]")
    assert len(pg.asked) == 1
    assert "discard" not in sent[0]


def test_stop_from_a_minute_does_not_ask(pg, srv):
    project = api(srv, "POST", "projects", {"name": "Projekt Tau"})
    api(srv, "POST", "timer/start", {"project_id": project["id"]})
    pg.reload()
    pg.wait_for_selector("[data-a=timerStop]")
    pg.evaluate("() => { S.timer.started = Date.now() / 1000 - 61; }")
    sent = _stop_requests(pg)
    pg.click("[data-a=timerStop]")
    pg.wait_for_selector("[data-a=timerAdd]")
    assert pg.asked == [] and "discard" not in sent[0]
