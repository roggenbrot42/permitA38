"""Browser test: every number the planner shows uses the same separator style (the app's de-DE convention: decimal
comma, as web/app.js num()). Seeds fractional hours, then checks the visible text and the title / aria-label
attributes of the planner: no decimal point may appear next to a unit, and the fractional values must read 12,5 etc.
Real UI, real API, seeded data."""

import re
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402,F401  (shared harness)

# A decimal point between digits that is followed by a planner unit ("12.5 h", "7.25 h", "50 %", "1.5 days").
# Dates (14.10.2026) are never followed by a unit, so they do not match.
DECIMAL_POINT = re.compile(r"(?<![\d.])\d+\.\d+(?=\s*(?:h\b|%|days?\b|€))")


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture
def seeded(srv):
    """A boss with 40 h/week, a contract of 12.5 h/week and a contract of 7.25 h in total (fractional hours)."""
    import httpx

    h = {"Authorization": "Bearer " + srv.token}
    me = httpx.get(srv.base + "/api/auth/me", headers=h).json()
    start = monday(date.today()) + timedelta(days=7)
    for body in (
        {
            "title": "Part-time retainer",
            "start": start.isoformat(),
            "end": (start + timedelta(days=13)).isoformat(),
            "hours_per_week": 12.5,
            "rate": 90,
            "status": "confirmed",
            "user_ids": [me["id"]],
        },
        {
            "title": "Short workshop",
            "start": (start + timedelta(days=21)).isoformat(),
            "end": (start + timedelta(days=27)).isoformat(),
            "total_hours": 7.25,
            "rate": 90,
            "status": "confirmed",
            "user_ids": [me["id"]],
        },
    ):
        r = httpx.post(srv.base + "/api/contracts", headers=h, json=body)
        assert r.status_code < 300, r.text
    return dict(me=me, week2=start)


@pytest.fixture
def ui(browser, srv, seeded):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: (problems.append("dialog: " + d.message), d.accept()))
    pg.on(
        "console", lambda m: m.type == "error" and "status of" not in m.text and problems.append("console: " + m.text)
    )
    pg.goto(srv.base + "/#/plan/vacation")
    pg.wait_for_selector("#plnWrap .pln-bar")
    yield pg
    assert problems == []
    ctx.close()


def planner_texts(pg) -> list[str]:
    """Everything the planner shows as text, plus its titles and aria-labels (tooltips). Input values are not
    displayed text, they are machine values for type=number, so they are not included."""
    visible = pg.inner_text("#pln")
    attrs = pg.eval_on_selector_all(
        "#pln [title], #pln [aria-label]",
        "els => els.flatMap(e => [e.getAttribute('title'), e.getAttribute('aria-label')]).filter(Boolean)",
    )
    return [visible, *attrs]


def test_planner_fractional_hours_use_one_decimal_separator(ui, seeded):
    texts = planner_texts(ui)
    joined = "\n".join(texts)
    # the fractional values are shown with the decimal comma, in the row label and in the bar's tooltip
    assert "12,5 h/week" in joined
    assert "7,25 h total" in joined
    # no text anywhere in the planner shows a decimal point next to a unit
    offenders = [m.group(0) for t in texts for m in DECIMAL_POINT.finditer(t)]
    assert offenders == []


def test_planner_tooltips_use_the_same_separator(ui, seeded):
    label = ui.locator('.pln-bar[aria-label*="Part-time retainer"]').first.get_attribute("aria-label")
    assert "12,5 h/week" in label
    # the dates in the tooltip are dd.mm.yyyy, the only dots allowed; no decimal point next to the hours
    assert not DECIMAL_POINT.search(label)
    assert not any(DECIMAL_POINT.search(t) for t in planner_texts(ui))
