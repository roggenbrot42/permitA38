"""Browser tests of the Team timeline (web only). No backend: every /api call is answered by page.route() from fixtures
taken from docs/contracts/team-timeline.md (the examples there, plus people added to test clash days). Names are fake."""

import json
import mimetypes
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright.sync_api")

WEB = Path(__file__).resolve().parents[2] / "web"
BASE = "http://permita.test"
AREAS = [
    "timer",
    "time",
    "absences",
    "projects",
    "customers",
    "contracts",
    "planning",
    "offers",
    "invoices",
    "expenses",
    "finance",
    "templates",
    "settings",
    "attachments",
    "team",
    "team_timeline",
]
BOSS_PERMS = {a: {"read": "all", "write": "all"} for a in AREAS}
EMPLOYEE_PERMS = {a: {"read": "none", "write": "none"} for a in AREAS} | {
    "timer": {"read": "limited", "write": "limited"},
    "time": {"read": "limited", "write": "limited"},
    "absences": {"read": "limited", "write": "limited"},
    "team_timeline": {"read": "limited", "write": "none"},
}
ACCOUNTANT_PERMS = {a: {"read": "none", "write": "none"} for a in AREAS} | {
    "customers": {"read": "limited", "write": "none"},
    "expenses": {"read": "all", "write": "all"},
    "team_timeline": {"read": "limited", "write": "none"},
}
BOSS = {"id": "u1a2b3c4d5", "name": "Boss Example", "role": "boss", "permissions": BOSS_PERMS}
ALEX = {"id": "u9z8y7x6w5", "name": "Alex Example", "role": "employee", "permissions": EMPLOYEE_PERMS}
ACCOUNTANT = {"id": "u0acc0unt1", "name": "Accountant Example", "role": "accountant", "permissions": ACCOUNTANT_PERMS}
SETTINGS = {
    "company_name": "Example Studio",
    "state": "BY",
    "vat_rate": 19,
    "default_rate": 0,
    "team_timeline_visibility": {"employee": "away", "accountant": "off"},
}
HOLIDAYS = {"2026-12-25": "1. Weihnachtstag", "2026-12-26": "2. Weihnachtstag"}


def example_full() -> dict:
    """The full-level example of docs/contracts/team-timeline.md, verbatim (boss viewer)."""
    return {
        "level": "full",
        "start": "2026-11-02",
        "end": "2026-12-27",
        "holidays": {"2026-12-25": "1. Weihnachtstag", "2026-12-26": "2. Weihnachtstag"},
        "people": [
            {
                "id": "u1a2b3c4d5",
                "name": "Boss Example",
                "color": "#3366cc",
                "weekly_hours": 40,
                "absences": [],
                "contracts": [
                    {
                        "id": "c5d6e7f8g9",
                        "title": "Website relaunch",
                        "customer": "Example GmbH",
                        "start": "2026-10-01",
                        "end": "2026-12-31",
                        "hours_per_week": 20,
                    }
                ],
            },
            {
                "id": "u9z8y7x6w5",
                "name": "Alex Example",
                "color": "#cc6633",
                "weekly_hours": 32,
                "absences": [
                    {"start": "2026-11-09", "end": "2026-11-13", "kind": "vacation", "label": "Autumn break"},
                    {"start": "2026-11-18", "end": "2026-11-18", "kind": "sick", "label": None},
                ],
                "contracts": [],
            },
        ],
    }


def clash_fixture() -> dict:
    """The example plus three people, so that some days have exactly 2, 3 or 4 people away (fake names).
    Boss: 6-8 Nov (Fri-Sun) and the holiday 25 Dec. Nov 6 (Fri): 3 away. Nov 7/8 (weekend): 3 away.
    Nov 9 and 11: 2 away. Nov 10: 3 away. Dec 24: 2 away. Dec 25 (holiday): 3 away."""
    f = example_full()
    boss, alex = f["people"]
    boss["absences"] = [
        {"start": "2026-11-06", "end": "2026-11-08", "kind": "other", "label": None},
        {"start": "2026-12-25", "end": "2026-12-25", "kind": "holiday", "label": None},
    ]
    sam = {
        "id": "u5s4m3p2l1",
        "name": "Sam Example",
        "color": "#2f855a",
        "weekly_hours": 40,
        "contracts": [],
        "absences": [
            {"start": "2026-11-06", "end": "2026-11-08", "kind": "vacation", "label": "Long weekend"},
            {"start": "2026-11-09", "end": "2026-11-11", "kind": "vacation", "label": "City trip"},
            {"start": "2026-12-24", "end": "2026-12-25", "kind": "vacation", "label": "Christmas"},
        ],
    }
    kim = {
        "id": "u7k6i5m4j3",
        "name": "Kim Example",
        "color": None,
        "weekly_hours": None,
        "contracts": [],
        "absences": [
            {"start": "2026-11-06", "end": "2026-11-08", "kind": "other", "label": None},
            {"start": "2026-11-10", "end": "2026-11-10", "kind": "sick", "label": None},
            {"start": "2026-12-24", "end": "2026-12-25", "kind": "sick", "label": None},
        ],
    }
    f["people"] = [boss, alex, sam, kim]
    return f


def oracle_clash(people, holidays, start, end, n) -> set[str]:
    """Independent oracle: a clash is a Monday-Friday that is no public holiday with more than n distinct people away."""
    out = set()
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        iso = d.isoformat()
        away = {
            p["id"] for p in people if any(a["start"] <= iso <= (a.get("end") or a["start"]) for a in p["absences"])
        }
        if d.weekday() < 5 and iso not in holidays and len(away) > n:
            out.add(iso)
        d += timedelta(days=1)
    return out


class Api:
    """Answers the app's /api calls. Records what the page sent (timeline queries, settings PUT bodies)."""

    def __init__(self, me, timeline=None, timeline_status=200, settings=None):
        self.me = me
        self.timeline = timeline
        self.timeline_status = timeline_status
        self.settings = dict(SETTINGS if settings is None else settings)
        self.queries: list[dict] = []
        self.puts: list[dict] = []

    def answer(self, request) -> dict:
        url = urlparse(request.url)
        path = url.path[len("/api/") :]
        js = lambda status, obj: {"status": status, "content_type": "application/json", "body": json.dumps(obj)}  # noqa: E731
        if path == "bootstrap":
            return js(
                200,
                {
                    "me": self.me,
                    "settings": self.settings,
                    "timer": {"running": False},
                    "states": {"BY": "Bayern"},
                    "conflicts": [],
                    "templates": [],
                    "data_dir": "/data",
                    "features": {},
                },
            )
        if path == "auth/status":
            return js(200, {"setup_needed": False, "version": "test"})
        if path == "auth/me":
            return js(200, self.me)
        if path == "timer":
            return js(200, {"running": False})
        if path == "team/timeline":
            self.queries.append({k: v[0] for k, v in parse_qs(url.query).items()})
            if self.timeline_status != 200:
                return js(self.timeline_status, {"detail": "Forbidden"})
            return js(200, self.timeline)
        if path == "settings":
            if request.method == "PUT":
                body = json.loads(request.post_data or "{}")
                self.puts.append(body)
                self.settings = {**self.settings, **body}
            return js(200, self.settings)
        if request.method == "GET":
            return js(200, [])  # collections (customers, users, calendars ...): empty
        return js(200, {})


def serve_static(route, path: str) -> None:
    name = "index.html" if path in ("", "/") else path.lstrip("/")
    f = WEB / name
    if "/" in name or not f.is_file():
        return route.fulfill(status=404, body="not found")
    ctype = mimetypes.guess_type(name)[0] or "text/plain"
    route.fulfill(status=200, content_type=ctype, body=f.read_bytes())


@pytest.fixture
def open_app(browser):
    """open_app(api, width=1280) -> (page, problems). Routes the whole origin: the static app from web/, the API from `api`."""
    contexts = []

    def open_(api: Api, width: int = 1280):
        ctx = browser.new_context(viewport={"width": width, "height": 900})
        contexts.append(ctx)
        pg = ctx.new_page()
        problems: list[str] = []
        pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
        pg.on("console", lambda m: problems.append("console: " + m.text) if m.type == "error" else None)

        def handle(route, request):
            if urlparse(request.url).path.startswith("/api/"):
                return route.fulfill(**api.answer(request))
            return serve_static(route, urlparse(request.url).path)

        pg.route(BASE + "/**", handle)
        return pg, problems

    yield open_
    for c in contexts:
        c.close()


def open_timeline(open_app, api, width=1280):
    pg, problems = open_app(api, width)
    pg.goto(BASE + "/#/timeline")
    pg.wait_for_function(
        "() => document.querySelector('#tlWrap .tl-bar') || document.querySelector('#main .empty')"
        " || /switched off|Nobody has time off|Cannot show/.test(document.body.innerText)",
        timeout=10000,
    )
    return pg, problems


def bar_labels(pg) -> list[str]:
    return pg.eval_on_selector_all("#tlWrap .tl-bar", "els => els.map(e => e.getAttribute('aria-label'))")


def clash_days_ui(pg) -> set[str]:
    return set(pg.eval_on_selector_all("#tlWrap [data-clash='1']", "els => els.map(e => e.dataset.day)"))


def set_threshold(pg, n: int) -> None:
    pg.fill("#tlN", str(n))
    pg.eval_on_selector("#tlN", "e => e.dispatchEvent(new Event('change', { bubbles: true }))")
    pg.wait_for_selector(f"#tlN[value='{n}']")


def bar_left_width(pg, label: str) -> tuple[int, int]:
    style = pg.get_attribute(f"#tlWrap .tl-bar[aria-label='{label}']", "style")
    parts = dict(p.split(":") for p in style.split(";") if ":" in p)
    return int(parts["left"].strip().rstrip("px")), int(parts["width"].strip().rstrip("px"))


def test_renders_the_example_bars_with_text_labels(open_app):
    api = Api(BOSS, timeline=example_full())
    pg, problems = open_timeline(open_app, api)
    labels = bar_labels(pg)
    assert "Alex Example, vacation: Autumn break, 9 to 13 November" in labels
    assert "Alex Example, sick, 18 November" in labels
    assert "Website relaunch, Example GmbH, 20 hours per week, 1 October to 31 December" in labels
    assert pg.locator("#tlWrap .tl-bar").count() == 3
    # the bars land on the right days: window starts 2 Nov (day 0), week zoom is 24 px per day
    assert bar_left_width(pg, "Alex Example, vacation: Autumn break, 9 to 13 November") == (7 * 24, 5 * 24 - 2)
    assert bar_left_width(pg, "Alex Example, sick, 18 November") == (16 * 24, 24 - 2)
    assert bar_left_width(pg, "Website relaunch, Example GmbH, 20 hours per week, 1 October to 31 December")[0] == 0
    assert "KW 45" in pg.inner_text("#tlWrap")
    assert pg.locator("#tlWrap [data-day='2026-12-25'].hol").count() >= 1
    assert pg.locator("#tlWrap [data-day='2026-11-07'].wk").count() >= 1
    assert problems == []


def test_bar_details_are_text_and_keyboard_moves_between_bars(open_app):
    api = Api(BOSS, timeline=example_full())
    pg, problems = open_timeline(open_app, api)
    pg.focus("#tlWrap .tl-bar[aria-label^='Website relaunch']")
    assert "Website relaunch" in pg.inner_text("#tlDetail")
    pg.keyboard.press("ArrowDown")  # from the contract row to Alex's row, nearest start day
    assert (
        pg.evaluate("document.activeElement.getAttribute('aria-label')")
        == "Alex Example, vacation: Autumn break, 9 to 13 November"
    )
    assert "Autumn break" in pg.inner_text("#tlDetail")
    pg.keyboard.press("ArrowRight")
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Alex Example, sick, 18 November"
    pg.keyboard.press("ArrowLeft")
    assert (
        pg.evaluate("document.activeElement.getAttribute('aria-label')")
        == "Alex Example, vacation: Autumn break, 9 to 13 November"
    )
    assert problems == []


def test_clash_days_at_the_threshold_boundary(open_app):
    fixture = clash_fixture()
    pg, problems = open_timeline(open_app, Api(BOSS, timeline=fixture))
    for n in (2, 1, 3):
        expected = oracle_clash(fixture["people"], fixture["holidays"], fixture["start"], fixture["end"], n)
        set_threshold(pg, n)
        assert clash_days_ui(pg) == expected, n
        listed = set(pg.eval_on_selector_all(".tl-clashlist li", "els => els.map(e => e.dataset.day)"))
        assert listed == expected, n
    # N = 2: three people away is a clash, exactly two is not (the boundary)
    set_threshold(pg, 2)
    assert clash_days_ui(pg) == {"2026-11-06", "2026-11-10"}
    assert "2026-11-09" not in clash_days_ui(pg) and "2026-11-11" not in clash_days_ui(pg)
    # N = 1: two people away is already a clash
    set_threshold(pg, 1)
    assert {"2026-11-09", "2026-11-11", "2026-12-24"} <= clash_days_ui(pg)
    assert problems == []


def test_weekends_and_public_holidays_never_clash(open_app):
    fixture = clash_fixture()
    pg, problems = open_timeline(open_app, Api(BOSS, timeline=fixture))
    set_threshold(pg, 2)
    days = clash_days_ui(pg)
    # three people are away on Sat 7 and Sun 8 Nov and on the holiday Fri 25 Dec: none of them is a clash day
    assert "2026-11-07" not in days and "2026-11-08" not in days and "2026-12-25" not in days
    assert "2026-11-06" in days  # a Friday with three away is a clash
    assert pg.locator(".tl-clashlist li[data-day='2026-12-25']").count() == 0
    assert problems == []


def test_levels_hide_the_details_others_are_away(open_app):
    """Level away (employee): others' reasons and contracts are not sent; own entries stay full."""
    away = {
        "level": "away",
        "start": "2026-11-02",
        "end": "2026-12-27",
        "holidays": HOLIDAYS,
        "people": [
            {
                "id": "u9z8y7x6w5",
                "name": "Alex Example",
                "color": "#cc6633",
                "weekly_hours": 32,
                "absences": [
                    {"start": "2026-11-09", "end": "2026-11-13", "kind": "vacation", "label": "Autumn break"},
                    {"start": "2026-11-18", "end": "2026-11-18", "kind": "sick", "label": None},
                ],
                "contracts": [],
            },
            {
                "id": "u1a2b3c4d5",
                "name": "Boss Example",
                "color": "#3366cc",
                "weekly_hours": None,
                "absences": [{"start": "2026-11-20", "end": "2026-11-22", "kind": None, "label": None}],
                "contracts": [],
            },
        ],
    }
    pg, problems = open_timeline(open_app, Api(ALEX, timeline=away))
    labels = bar_labels(pg)
    assert "Boss Example, away, 20 to 22 November" in labels
    assert "Alex Example, vacation: Autumn break, 9 to 13 November" in labels  # own entries are full
    assert "Alex Example, sick, 18 November" in labels
    text = pg.inner_text("#main")
    assert "You see away only" in text
    assert "hours hidden at your level" in text
    assert "Skiing" not in pg.content()  # never sent at this level, so never drawn
    assert "Website relaunch" not in pg.content()
    assert "Contract bars are shown at level Full only." in text
    assert problems == []


def test_time_off_level_shows_kind_and_label_but_sick_days_only_as_away(open_app):
    time_off = {
        "level": "time-off",
        "start": "2026-11-02",
        "end": "2026-12-27",
        "holidays": HOLIDAYS,
        "people": [
            {
                "id": "u9z8y7x6w5",
                "name": "Alex Example",
                "color": "#cc6633",
                "weekly_hours": 32,
                "absences": [{"start": "2026-11-09", "end": "2026-11-13", "kind": "vacation", "label": "Autumn break"}],
                "contracts": [],
            },
            {
                "id": "u1a2b3c4d5",
                "name": "Boss Example",
                "color": "#3366cc",
                "weekly_hours": None,
                "absences": [
                    {"start": "2026-11-20", "end": "2026-11-22", "kind": "vacation", "label": "Skiing trip"},
                    {"start": "2026-11-25", "end": "2026-11-25", "kind": None, "label": None},
                ],
                "contracts": [],
            },
        ],
    }
    pg, problems = open_timeline(open_app, Api(ALEX, timeline=time_off))
    labels = bar_labels(pg)
    assert "Boss Example, vacation: Skiing trip, 20 to 22 November" in labels
    assert "Boss Example, away, 25 November" in labels
    assert not any("Boss Example, sick" in x for x in labels)
    assert problems == []


def test_forbidden_shows_the_switched_off_state(open_app):
    pg, problems = open_timeline(open_app, Api(ACCOUNTANT, timeline=None, timeline_status=403))
    assert "switched off for your role" in pg.inner_text("#main")
    assert pg.locator("#tlWrap .tl-bar").count() == 0
    assert pg.locator("#toast.err").count() == 0
    # the browser logs the failed request itself; nothing else may be wrong
    assert [p for p in problems if "Failed to load resource" not in p] == []


def test_boss_settings_form_sends_the_contract_body(open_app):
    api = Api(BOSS, timeline=example_full())
    pg, problems = open_app(api)
    pg.goto(BASE + "/#/settings")
    pg.wait_for_selector("#f_tl_employee")
    assert pg.input_value("#f_tl_employee") == "away"
    assert pg.input_value("#f_tl_accountant") == "off"
    pg.select_option("#f_tl_employee", "time-off")
    with pg.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/api/settings")):
        pg.click("button[data-a=saveSettings]")
    assert api.puts[-1]["team_timeline_visibility"] == {"employee": "time-off", "accountant": "off"}
    assert problems == []


def test_empty_period_says_nobody_is_away(open_app):
    empty = example_full()
    empty["people"][0]["contracts"] = []
    empty["people"][1]["absences"] = []
    pg, problems = open_timeline(open_app, Api(BOSS, timeline=empty))
    assert "Nobody has time off in this period." in pg.inner_text("#main")
    assert problems == []


def test_names_are_escaped(open_app):
    f = example_full()
    f["people"][1]["name"] = '<img src="x" onerror="window.__pwned=1">Eve'
    f["people"][1]["absences"] = [{"start": "2026-11-09", "end": "2026-11-09", "kind": "other", "label": "<b>x</b>"}]
    pg, problems = open_timeline(open_app, Api(BOSS, timeline=f))
    assert pg.locator("#main img").count() == 0
    assert pg.evaluate("window.__pwned") is None
    assert any("<img" in label for label in bar_labels(pg))
    assert problems == []


def test_phone_width_has_no_page_scroll(open_app):
    pg, problems = open_timeline(open_app, Api(BOSS, timeline=clash_fixture()), width=375)
    assert pg.evaluate("document.documentElement.scrollWidth") <= 375
    assert problems == []
