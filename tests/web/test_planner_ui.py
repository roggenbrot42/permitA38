"""Browser tests of Planning -> Plan & vacation: drag to create vacation (mouse), over-capacity weeks turn red,
apply and discard, and the keyboard path (focus a bar, arrow keys move it). Real UI, real API, seeded data."""

from datetime import date, timedelta

import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402,F401  (shared harness)


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture
def seeded(srv):
    """A boss with 40 h/week, and a contract that books the whole week after next (week 2 of the horizon)."""
    import httpx

    h = {"Authorization": "Bearer " + srv.token}
    me = httpx.get(srv.base + "/api/auth/me", headers=h).json()
    start = monday(date.today()) + timedelta(days=7)
    httpx.post(srv.base + "/api/contracts", headers=h, json={
        "title": "Retainer", "start": start.isoformat(), "end": (start + timedelta(days=13)).isoformat(),
        "hours_per_week": 40, "rate": 90, "status": "confirmed", "user_ids": [me["id"]]})
    return dict(me=me, week2=start)


@pytest.fixture
def ui(browser, srv, seeded):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    def on_dialog(d):  # the Apply confirmation is expected; any other dialog is a problem
        if not d.message.startswith("Write "):
            problems.append("dialog: " + d.message)
        d.accept()

    pg.on("dialog", on_dialog)
    pg.on("console", lambda m: m.type == "error" and "status of" not in m.text and problems.append("console: " + m.text))
    pg.goto(srv.base + "/#/plan/vacation")
    pg.wait_for_selector("#plnWrap .pln-bar")
    yield pg
    assert problems == []
    ctx.close()


def cell_center(pg, day: str):
    box = pg.locator(f'[data-lane="time"] .pln-cell[data-day="{day}"]').first.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_drag_on_time_off_row_creates_vacation_and_over_capacity_turns_red(srv, ui, seeded):
    w2 = seeded["week2"]
    x1, y1 = cell_center(ui, w2.isoformat())
    x2, _ = cell_center(ui, (w2 + timedelta(days=4)).isoformat())
    ui.mouse.move(x1, y1)
    ui.mouse.down()
    ui.mouse.move(x2, y1, steps=8)
    ui.mouse.up()
    ui.wait_for_selector(".pln-bar.draft.abs")
    assert "1 draft change" in ui.inner_text(".pln-draftbar")
    # the 40 h contract now has no capacity in that week: the utilisation cell is over capacity
    ui.wait_for_selector(".pln-week.util.over", timeout=8000)
    assert ui.locator(".pln-week.util.over").count() >= 1
    # the real data is untouched until Apply
    assert srv.app.state.services.store.list("absences") == []


def test_apply_writes_the_draft_and_discard_drops_it(srv, ui, seeded):
    w2 = seeded["week2"]
    x1, y1 = cell_center(ui, (w2 + timedelta(days=7)).isoformat())
    x2, _ = cell_center(ui, (w2 + timedelta(days=8)).isoformat())
    ui.mouse.move(x1, y1)
    ui.mouse.down()
    ui.mouse.move(x2, y1, steps=4)
    ui.mouse.up()
    ui.wait_for_selector(".pln-bar.draft.abs")
    ui.click("[data-a=plnDiscard]")
    ui.wait_for_selector(".pln-bar.draft", state="detached")
    assert srv.app.state.services.store.list("absences") == []
    ui.mouse.move(x1, y1)
    ui.mouse.down()
    ui.mouse.move(x2, y1, steps=4)
    ui.mouse.up()
    ui.wait_for_selector(".pln-bar.draft.abs")
    ui.click("[data-a=plnApply]")
    ui.wait_for_function("() => document.querySelectorAll('.pln-bar.draft').length === 0", timeout=8000)
    absences = srv.app.state.services.store.list("absences")
    assert len(absences) == 1 and absences[0]["kind"] == "vacation"


def test_keyboard_moves_a_contract_bar_and_the_draft_is_visible(srv, ui, seeded):
    bar = ui.locator('.pln-bar.contract[data-kind="com"]').first
    bar.focus()
    ui.keyboard.press("Shift+ArrowRight")  # one week later
    ui.wait_for_selector(".pln-bar.contract.draft")
    assert "1 draft change" in ui.inner_text(".pln-draftbar")
    ui.click("[data-a=plnDiscard]")
    ui.wait_for_selector(".pln-bar.draft", state="detached")
