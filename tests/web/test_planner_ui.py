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
    httpx.post(
        srv.base + "/api/contracts",
        headers=h,
        json={
            "title": "Retainer",
            "start": start.isoformat(),
            "end": (start + timedelta(days=13)).isoformat(),
            "hours_per_week": 40,
            "rate": 90,
            "status": "confirmed",
            "user_ids": [me["id"]],
        },
    )
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
    pg.on(
        "console", lambda m: m.type == "error" and "status of" not in m.text and problems.append("console: " + m.text)
    )
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
    ui.keyboard.press("PageDown")  # one week later
    ui.wait_for_selector(".pln-bar.contract.draft")
    assert "1 draft change" in ui.inner_text(".pln-draftbar")
    ui.click("[data-a=plnDiscard]")
    ui.wait_for_selector(".pln-bar.draft", state="detached")


def drag_vacation(ui, first: str, last: str):
    x1, y1 = cell_center(ui, first)
    x2, _ = cell_center(ui, last)
    ui.mouse.move(x1, y1)
    ui.mouse.down()
    ui.mouse.move(x2, y1, steps=4)
    ui.mouse.up()
    ui.wait_for_selector(".pln-bar.draft.abs")


def abs_range(ui):
    return ui.locator(".pln-bar.abs").first.get_attribute("aria-label")


def test_keyboard_moves_and_resizes_a_time_off_draft(srv, ui, seeded):
    w2 = seeded["week2"]
    drag_vacation(ui, w2.isoformat(), (w2 + timedelta(days=1)).isoformat())
    before = abs_range(ui)
    bar = ui.locator(".pln-bar.abs.draft").first
    bar.focus()
    ui.keyboard.press("ArrowRight")
    ui.wait_for_function("(b) => document.querySelector('.pln-bar.abs').getAttribute('aria-label') !== b", arg=before)
    moved = abs_range(ui)
    assert moved != before
    assert ui.evaluate("() => document.activeElement.classList.contains('pln-bar')")  # focus survives the redraw
    ui.keyboard.press("Shift+ArrowRight")  # extend the end by a day (Alt+arrows are the browser's Back/Forward)
    ui.wait_for_function("(b) => document.querySelector('.pln-bar.abs').getAttribute('aria-label') !== b", arg=moved)
    assert "1 draft change" in ui.inner_text(".pln-draftbar")


def test_single_time_off_items_can_be_discarded(srv, ui, seeded):
    w2 = seeded["week2"]
    drag_vacation(ui, w2.isoformat(), (w2 + timedelta(days=1)).isoformat())
    drag_vacation(ui, (w2 + timedelta(days=7)).isoformat(), (w2 + timedelta(days=8)).isoformat())
    ui.wait_for_function("() => document.querySelectorAll('.pln-bar.abs.draft').length === 2")
    ui.locator(".pln-bar.abs.draft").first.focus()
    ui.keyboard.press("Delete")
    ui.wait_for_function("() => document.querySelectorAll('.pln-bar.abs.draft').length === 1")
    assert "1 draft change" in ui.inner_text(".pln-draftbar")


def test_keyboard_resizes_the_start_and_keeps_focus_for_repeated_presses(srv, ui, seeded):
    w2 = seeded["week2"]
    drag_vacation(ui, (w2 + timedelta(days=1)).isoformat(), (w2 + timedelta(days=2)).isoformat())
    ui.locator(".pln-bar.abs.draft").first.focus()
    for _ in range(3):  # three presses in a row: each one needs the focus to survive the previous redraw
        before = abs_range(ui)
        ui.keyboard.press("Control+ArrowLeft")
        ui.wait_for_function(
            "(b) => document.querySelector('.pln-bar.abs').getAttribute('aria-label') !== b", arg=before
        )
    start = (w2 + timedelta(days=1) - timedelta(days=3)).strftime("%d.%m.")
    assert start in abs_range(ui)


def test_each_draft_change_can_be_discarded_on_its_own(srv, ui, seeded):
    w2 = seeded["week2"]
    drag_vacation(ui, w2.isoformat(), (w2 + timedelta(days=1)).isoformat())
    drag_vacation(ui, (w2 + timedelta(days=7)).isoformat(), (w2 + timedelta(days=8)).isoformat())
    ui.wait_for_function("() => document.querySelectorAll('.pln-draftlist li').length === 2")
    ui.locator("[data-a=plnDiscardOne]").first.click()
    ui.wait_for_function("() => document.querySelectorAll('.pln-draftlist li').length === 1")
    assert ui.locator(".pln-bar.abs.draft").count() == 1
    assert "1 draft change" in ui.inner_text(".pln-draftbar")
    # Escape on a focused bar drops that item too
    ui.locator(".pln-bar.abs.draft").first.focus()
    ui.keyboard.press("Escape")
    ui.wait_for_selector(".pln-bar.draft", state="detached")
    assert srv.app.state.services.store.list("absences") == []


def test_a_marked_for_deletion_time_off_can_be_restored_from_the_list(srv, ui, seeded):
    import httpx

    w2 = seeded["week2"]
    h = {"Authorization": "Bearer " + srv.token}
    httpx.post(
        srv.base + "/api/absences",
        headers=h,
        json={
            "user_id": seeded["me"]["id"],
            "kind": "vacation",
            "start": (w2 + timedelta(days=14)).isoformat(),
            "end": (w2 + timedelta(days=15)).isoformat(),
        },
    ).raise_for_status()
    ui.reload()
    ui.wait_for_selector(".pln-bar.abs")
    ui.locator(".pln-bar.abs").first.focus()
    ui.keyboard.press("Delete")
    ui.wait_for_selector(".pln-bar.abs", state="detached")
    ui.wait_for_selector(".pln-draftlist li")
    ui.click("[data-a=plnDiscardOne]")
    ui.wait_for_selector(".pln-bar.abs")
    assert "No draft changes" in ui.inner_text(".pln-draftbar")
    assert len(srv.app.state.services.store.list("absences")) == 1


def test_clicking_a_bar_selects_and_focuses_it_and_the_keyboard_works_right_away(srv, ui, seeded):
    w2 = seeded["week2"]
    drag_vacation(ui, w2.isoformat(), (w2 + timedelta(days=1)).isoformat())
    before = abs_range(ui)
    box = ui.locator(".pln-bar.abs.draft").first.bounding_box()
    ui.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    assert ui.evaluate("() => document.activeElement.classList.contains('pln-bar')")
    assert ui.locator(".pln-bar.sel").count() == 1  # a visible selection, also in browsers without :focus-visible on click
    ui.keyboard.press("ArrowRight")
    ui.wait_for_function("(b) => document.querySelector('.pln-bar.abs').getAttribute('aria-label') !== b", arg=before)


def test_dragging_a_bar_moves_it_and_its_edge_resizes_it(srv, ui, seeded):
    w2 = seeded["week2"]
    drag_vacation(ui, w2.isoformat(), (w2 + timedelta(days=1)).isoformat())
    before = abs_range(ui)
    box = ui.locator(".pln-bar.abs.draft").first.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    ui.mouse.move(x, y)
    ui.mouse.down()
    ui.mouse.move(x + 3 * 12, y, steps=6)  # three days to the right (12 px per day)
    ui.mouse.up()
    ui.wait_for_function("(b) => document.querySelector('.pln-bar.abs').getAttribute('aria-label') !== b", arg=before)
    moved = abs_range(ui)
    assert (w2 + timedelta(days=3)).strftime("%d.%m.") in moved
    box = ui.locator(".pln-bar.abs.draft").first.bounding_box()
    hx, y = box["x"] + box["width"] - 3, box["y"] + box["height"] / 2  # the right-hand edge handle
    ui.mouse.move(hx, y)
    ui.mouse.down()
    ui.mouse.move(hx + 2 * 12, y, steps=4)
    ui.mouse.up()
    ui.wait_for_function("(b) => document.querySelector('.pln-bar.abs').getAttribute('aria-label') !== b", arg=moved)
    assert (w2 + timedelta(days=6)).strftime("%d.%m.") in abs_range(ui)
    assert "1 draft change" in ui.inner_text(".pln-draftbar")
