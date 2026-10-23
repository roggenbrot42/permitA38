"""Team timeline against the REAL server (no page.route fixtures): seeded fake users and absences, the real visibility
levels, and the Settings card that changes the accountant level. Complements test_timeline_ui.py (routed fixtures).

Window: first day 2026-11-02 (Monday), default 8 weeks, so it ends 2026-12-27.
Seed (all names fake):
* Alex Example (employee, the viewer) has no absences of their own.
* Bea Example (accountant): vacation 9-13 Nov "Autumn break", sick 18 Nov "Flu".
* Boss: other 10 Nov "Dentist".
Away per weekday: 10 Nov has 2 people (Bea, boss), every other day at most 1, so threshold 1 gives exactly one clash day.
"""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import PASSWORD, Server  # noqa: E402,F401  (shared live-server harness)

WINDOW_START = "2026-11-02"
CLASH_DAY = "2026-11-10"


@pytest.fixture
def live(tmp_path):
    srv = Server(tmp_path)
    yield srv
    srv.stop()


def _auth(token):
    return {"Authorization": "Bearer " + token}


def _seed(srv):
    """Users and absences through the real API, as the boss. Returns the tokens and ids."""
    boss = srv.token
    me = httpx.get(srv.base + "/api/auth/me", headers=_auth(boss)).json()

    def post(path, body):
        r = httpx.post(srv.base + "/api" + path, json=body, headers=_auth(boss))
        assert r.status_code == 200, f"POST {path}: {r.status_code} {r.text}"
        return r.json()

    alex = post("/users", {"username": "alex", "name": "Alex Example", "role": "employee", "password": PASSWORD})
    bea = post(
        "/users",
        {"username": "bea", "name": "Bea Example", "role": "accountant", "password": PASSWORD, "color": "#2f855a"},
    )
    post(
        "/absences",
        {"user_id": bea["id"], "kind": "vacation", "start": "2026-11-09", "end": "2026-11-13", "note": "Autumn break"},
    )
    post("/absences", {"user_id": bea["id"], "kind": "sick", "start": "2026-11-18", "end": "2026-11-18", "note": "Flu"})
    post("/absences", {"user_id": me["id"], "kind": "other", "start": CLASH_DAY, "end": CLASH_DAY, "note": "Dentist"})

    def login(username):
        r = httpx.post(srv.base + "/api/auth/login", json={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    return {"boss": boss, "alex": login("alex"), "bea": login("bea"), "alex_id": alex["id"]}


def _open(browser, srv, token, route):
    ctx = browser.new_context(viewport={"width": 1200, "height": 900}, locale="en-GB")
    ctx.set_default_timeout(8000)
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on(
        "console", lambda m: m.type == "error" and "status of" not in m.text and problems.append("console: " + m.text)
    )
    pg.on("dialog", lambda d: (problems.append("dialog: " + d.message), d.dismiss()))
    pg.goto(srv.base + "/#/" + route)
    return ctx, pg, problems


def _first_day(pg, day=WINDOW_START):
    """Move the window to the seeded period with the page's own 'First day' control."""
    pg.fill("#tlFrom", day)
    pg.dispatch_event("#tlFrom", "change")
    pg.wait_for_selector(f'#tlWrap [data-day="{day}"]')


def test_team_timeline_live(browser, live):
    tok = _seed(live)

    # --- the accountant is switched off by default: the page answers 403 and shows the switched-off state
    ctx_bea, bea, bea_problems = _open(browser, live, tok["bea"], "timeline")
    bea.wait_for_selector("text=Not available")
    assert "switched off" in bea.inner_text("#main")
    assert bea.locator("#tlWrap .tl-bar").count() == 0
    ctx_bea.close()

    # --- the boss sees everything: bars with reasons, the clash day only at threshold 1
    ctx_boss, boss, boss_problems = _open(browser, live, tok["boss"], "timeline")
    boss.wait_for_selector("#tlWrap")
    _first_day(boss)
    assert boss.locator("#tlWrap .tl-bar").count() == 3  # Bea vacation, Bea sick, boss other
    assert boss.locator('#tlWrap .tl-bar[aria-label*="Autumn break"]').count() == 1
    assert boss.locator('#tlWrap .tl-bar[aria-label*="Dentist"]').count() == 1
    assert boss.locator("text=No clash days").count() == 1  # default threshold 2: two people is not more than two
    boss.fill("#tlN", "1")
    boss.dispatch_event("#tlN", "change")
    boss.wait_for_selector('.tl-clashlist li[data-day="2026-11-10"]')
    clash_items = boss.locator(".tl-clashlist li")
    assert clash_items.count() == 1
    assert "2 people away" in clash_items.first.inner_text()
    assert boss.locator('#tlWrap [data-clash="1"]').count() == 1
    assert boss.locator('#tlWrap [data-clash="1"]').get_attribute("data-day") == CLASH_DAY
    assert boss_problems == [], boss_problems
    ctx_boss.close()

    # --- the employee (level away by default) sees who is away, but no reasons or kinds of other people's entries
    ctx_alex, alex, alex_problems = _open(browser, live, tok["alex"], "timeline")
    alex.wait_for_selector("#tlWrap")
    _first_day(alex)
    assert alex.locator("#tlWrap .tl-bar").count() == 3
    bar_texts = alex.locator("#tlWrap .tl-bar-t").all_inner_texts()
    assert bar_texts == ["away", "away", "away"], bar_texts
    main_text = alex.inner_text("#main")
    assert "away only" in main_text
    for hidden in ("Autumn break", "Flu", "Dentist", "vacation", "sick", "Reason"):
        assert hidden not in main_text, f"{hidden!r} visible at level away"
    alex.locator("#tlWrap .tl-bar").first.click()
    detail = alex.inner_text("#tlDetail")
    assert "Reason not shown at your level" in detail
    assert "Type:" not in detail and "Note:" not in detail
    assert alex_problems == [], alex_problems
    ctx_alex.close()

    # --- the boss opens Settings, Roles card, and sets the accountant to time off
    ctx_boss, boss, boss_problems = _open(browser, live, tok["boss"], "settings")
    boss.wait_for_selector("#f_tl_accountant")
    boss.select_option("#f_tl_accountant", "time-off")
    with boss.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/api/settings")) as put:
        boss.click("[data-a=saveSettings]")
    assert put.value.status == 200
    vis = httpx.get(live.base + "/api/settings", headers=_auth(tok["boss"])).json()["team_timeline_visibility"]
    assert vis == {"employee": "away", "accountant": "time-off"}, vis
    assert boss_problems == [], boss_problems
    ctx_boss.close()

    # --- the accountant now sees the page at time off: vacation labels, but not other people's notes
    ctx_bea, bea, bea_problems = _open(browser, live, tok["bea"], "timeline")
    bea.wait_for_selector("#tlWrap")
    _first_day(bea)
    assert bea.locator('#tlWrap .tl-bar[aria-label*="Autumn break"]').count() == 1
    assert "time off with kind and vacation labels" in bea.inner_text("#main")
    assert "Dentist" not in bea.inner_text("#main")  # the boss's 'other' entry has no label at time off
    assert bea_problems == [], bea_problems
    ctx_bea.close()
