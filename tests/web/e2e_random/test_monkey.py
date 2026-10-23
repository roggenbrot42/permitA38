"""Gremlin monkey: seeded random clicking, typing, key presses and navigation against a seeded data set.

Invariants after every step: no uncaught page error, no console error except answered-with-4xx API calls, no
HTTP 5xx, no script executed from injected markup, injected HTML rendered as text, the shell never empty, the
modal can always be closed and the session survives navigation. A second run does the same as a *member* and
asserts that no boss-only data is ever shown.

Replay: PERMITA38_E2E_SEED=<seed> pytest tests/web/e2e_random/test_monkey.py
"""

import random
import re
from datetime import date, timedelta

import pytest

from .conftest import PASSWORD, Api
from .helpers import env_int, norm, seeds_from_env, week_start

pytestmark = pytest.mark.e2e_random

MONKEY_SEEDS = [7, 99]
XSS_MARK = "XSS_MARK"
PAYLOADS = [
    "",
    " ",
    "0",
    "-1",
    "-0.5",
    "1e9",
    "99999999999",
    "NaN",
    "Infinity",
    "1,5",
    "1.5.5",
    "0:00",
    "25:99",
    "-3:30",
    "ß ü ä ö 😀 漢字 עברית",
    f"<script>alert('{XSS_MARK}')</script>",
    "<img src=x onerror=window.__xss=1>",
    '"><svg onload=window.__xss=1>',
    "'; DROP TABLE users;--",
    "{{7*7}}",
    "${7*7}",
    "%s %n",
    "A" * 5000,
    chr(0x202E) + " rtl override",
    "2026-02-30",
    "9999-12-31",
    "0000-01-01",
    "../../etc/passwd",
    "<b>bold</b>",
    "javascript:alert(1)",
    "null",
]
CLICKABLE = "[data-a], a[href^='#'], button, tr.click, tr[data-href], select, input[type=checkbox], summary"
FORBIDDEN = "[data-a=logout], [data-a=changePassword]"  # ends the session on purpose; covered by the journeys
ROUTES = [
    "today",
    "time",
    "time/2026-01-05",
    "plan",
    "plan/contracts",
    "plan/timeoff",
    "plan/vacation",
    "customers",
    "projects",
    "sales",
    "sales/offers",
    "expenses",
    "money",
    "money/2025",
    "money/1999",
    "team",
    "settings",
    "numbering",
    "import",
    "timeoff",
    "customer/doesnotexist",
    "project/doesnotexist",
    "doc/invoices/doesnotexist",
    "doc/offers/new",
    "doc/invoices/new",
    "doc/offers/new-nope",
    "nonexistent",
    "",
    "login",
    "money/abc",
    "time/not-a-date",
]

LIST_VISIBLE_JS = """(sel) => {
  const out = [];
  document.querySelectorAll(sel).forEach((el, i) => {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && !el.disabled && !el.matches(arguments[1]))
      out.push(i);
  });
  return out;
}"""
FIELDS_JS = """() => {
  const out = [];
  document.querySelectorAll('main input, main textarea, main select, #modal-root input, #modal-root textarea, #modal-root select')
    .forEach((el, i) => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && !el.disabled && !['file', 'hidden', 'color'].includes(el.type)) out.push(i);
    });
  return out;
}"""
FIELDS = "main input, main textarea, main select, #modal-root input, #modal-root textarea, #modal-root select"
INJECTED_JS = """() => document.querySelectorAll(
  '#app script, #modal-root script, #app [onerror], #modal-root [onerror], #app [onload], #modal-root [onload],'
  + '#app img[src="x"], #modal-root img[src="x"]').length"""


def seed_dataset(api: Api, rng: random.Random) -> dict:
    """Realistic data incl. names that are XSS payloads, created through the API."""
    ws = week_start(date.today())
    c1 = api.post(
        "/customers",
        {
            "name": "Sicherheit & Sohn <b>GmbH</b>",
            "tax_number": "12/345/67890",
            "contact": "Frau Prüfer",
            "email": "pruefer@example.com",
            "rate": 110,
        },
    )
    c2 = api.post("/customers", {"name": "<img src=x onerror=window.__xss=1>", "city": "Köln"})
    api.post("/customers", {"name": f"\"><script>alert('{XSS_MARK}')</script>", "vat_id": "DE123456789"})
    projects = [
        api.post("/projects", {"name": "Website Relaunch", "customer_id": c1["id"], "budget_hours": 40}),
        api.post("/projects", {"name": "<svg onload=window.__xss=1>", "customer_id": c2["id"], "rate": 99}),
        api.post("/projects", {"name": "Eigenes Lernen", "personal": True}),
    ]
    api.post("/tasks", {"project_id": projects[0]["id"], "name": "Beratung", "rate": 140})
    me = api.get("/auth/me")
    for i in range(6):
        api.post(
            "/entries",
            {
                "user_id": me["id"],
                "date": (ws + timedelta(days=i % 5)).isoformat(),
                "minutes": rng.choice([30, 90, 135, 240]),
                "project_id": projects[i % 3]["id"],
                "note": f"Arbeit {i}",
                "billable": i % 4 != 3,
            },
        )
    api.post(
        "/contracts",
        {
            "title": "Wartung",
            "customer_id": c1["id"],
            "start": ws.isoformat(),
            "end": (ws + timedelta(days=90)).isoformat(),
            "hours_per_week": 8,
            "rate": 100,
            "user_ids": [me["id"]],
        },
    )
    api.post(
        "/absences",
        {
            "user_id": me["id"],
            "kind": "vacation",
            "start": (ws + timedelta(days=21)).isoformat(),
            "end": (ws + timedelta(days=25)).isoformat(),
            "note": "Sommerurlaub",
        },
    )
    offer = api.post(
        "/offers",
        {
            "customer_id": c1["id"],
            "title": "Angebot Geheim",
            "lines": [{"description": "Beratung", "qty": 10, "unit": "h", "price": 100}],
            "plan": {"start": ws.isoformat(), "end": (ws + timedelta(days=60)).isoformat(), "hours_per_week": 5},
        },
    )
    offer = api.post(f"/offers/{offer['id']}/finalize")
    inv = api.post(
        "/invoices",
        {
            "customer_id": c1["id"],
            "title": "Rechnung Geheim",
            "lines": [{"description": "Design", "qty": 7, "unit": "h", "price": 123}],
        },
    )
    inv = api.post(f"/invoices/{inv['id']}/finalize")
    api.post(
        "/expenses",
        {"date": date.today().isoformat(), "net": 421.0, "vat": 80.0, "vendor": "Geheimlieferant", "category": "Other"},
    )
    return {
        "secrets": [
            "Sicherheit",
            "Geheimlieferant",
            "Angebot Geheim",
            "Rechnung Geheim",
            "12/345/67890",
            "DE123456789",
            "pruefer@example.com",
            offer["number"],
            inv["number"],
            "Frau Prüfer",
            "Wartung",
        ],
        "ids": {"customer": c1["id"], "project": projects[0]["id"], "invoice": inv["id"], "offer": offer["id"]},
    }


class Monkey:
    def __init__(self, page, watch, rng, base_url: str, member_secrets: list[str] | None = None):
        self.page, self.watch, self.rng, self.base = page, watch, rng, base_url
        self.log: list[str] = []
        self.secrets = member_secrets  # not None => running as a member: nothing sensitive may ever be visible
        watch.rng = rng
        watch.accept_confirm = 0.3
        if member_secrets is not None:  # boss-only pages answer 403; the app logs it and shows an error card
            watch.ignore.append(re.compile(r"Error: Forbidden"))

    def say(self, msg: str):
        self.log.append(f"{len(self.log) + 1:03d} {msg}")

    # ---- gremlin moves
    def click_random(self):
        idx = self.page.evaluate(LIST_VISIBLE_JS.replace("arguments[1]", f"'{FORBIDDEN}'"), CLICKABLE)
        if not idx:
            return self.say("click: nothing clickable")
        i = self.rng.choice(idx)
        loc = self.page.locator(CLICKABLE).nth(i)
        try:
            desc = loc.evaluate(
                "e => e.tagName + (e.dataset.a ? '[' + e.dataset.a + ']' : '') + ' ' + "
                "(e.innerText || e.value || '').trim().slice(0, 30)"
            )
        except Exception:
            return self.say("click: element vanished")
        self.say(f"click {desc}")
        try:
            if loc.evaluate("e => e.tagName") == "SELECT":
                n = loc.evaluate("e => e.options.length")
                loc.select_option(index=self.rng.randrange(n), timeout=700)
            else:
                loc.click(timeout=700, no_wait_after=True)
        except Exception as e:  # covered by a modal, detached by a re-render, ... – not an application error
            self.say(f"  (skipped: {str(e).splitlines()[0][:60]})")

    def type_random(self):
        idx = self.page.evaluate(FIELDS_JS)
        if not idx:
            return self.say("type: no fields")
        loc = self.page.locator(FIELDS).nth(self.rng.choice(idx))
        value = self.rng.choice(PAYLOADS)
        try:
            tag = loc.evaluate("e => e.tagName")
            if tag == "SELECT":
                n = loc.evaluate("e => e.options.length")
                loc.select_option(index=self.rng.randrange(n), timeout=700)
                self.say("select random option")
            else:
                loc.fill(value, timeout=700)
                self.say(f"fill {value[:30]!r}{'…' if len(value) > 30 else ''}")
        except Exception as e:
            self.say(f"  (skipped fill: {str(e).splitlines()[0][:60]})")

    def press_key(self):
        key = self.rng.choice(["Escape", "Enter", "Tab", "Shift+Tab", "ArrowDown", "Space"])
        self.say(f"key {key}")
        try:
            self.page.keyboard.press(key)
        except Exception as e:
            self.say(f"  (skipped: {str(e)[:50]})")

    def modal_action(self):
        if self.page.evaluate("() => document.querySelector('#modal-root').children.length > 0"):
            btn = self.rng.choice(["#m-save", "#m-cancel", "#m-cancel"])
            self.say(f"modal {btn}")
            try:
                self.page.click(btn, timeout=700, no_wait_after=True)
            except Exception:
                pass
        else:
            self.click_random()

    def go_route(self):
        r = self.rng.choice(ROUTES)
        self.say(f"route #/{r}")
        self.page.evaluate("h => { location.hash = h; }", f"#/{r}")

    def history(self):
        d = self.rng.choice(["back", "forward", "reload"])
        self.say(d)
        try:
            {"back": self.page.go_back, "forward": self.page.go_forward, "reload": self.page.reload}[d](timeout=5000)
        except Exception as e:
            self.say(f"  (skipped: {str(e)[:50]})")

    # ---- purposeful episodes: a user opens a form, fills it (plausibly or hostilely) and submits
    def _plausible(self, kind: str) -> str:
        r = self.rng
        today = date.today()
        if kind == "date":
            return (today + timedelta(days=r.randint(-60, 60))).isoformat()
        if kind == "time":
            return f"{r.randint(0, 23):02d}:{r.choice([0, 15, 30, 45]):02d}"
        if kind == "number":
            return r.choice(["0", "1", "2.5", "40", "80", "120", "-5", "1000000", "0.01"])
        return r.choice(["Hallo", "Müller & Co", "1:30", "90m", "2h 15m", "Notiz", "42", "Berlin"])

    def _fill_container(self, scope: str):
        fields = self.page.evaluate(
            """(scope) => [...document.querySelectorAll(scope + ' input, ' + scope + ' textarea, ' + scope + ' select')]
               .map((e, i) => ({i, tag: e.tagName, type: e.type, ok: !e.disabled && e.getBoundingClientRect().width > 0
                   && !['file', 'hidden', 'color'].includes(e.type), n: e.options ? e.options.length : 0}))
               .filter(f => f.ok)""",
            scope,
        )
        loc = self.page.locator(f"{scope} input, {scope} textarea, {scope} select")
        for f in fields:
            try:
                el = loc.nth(f["i"])
                if f["tag"] == "SELECT":
                    el.select_option(index=self.rng.randrange(f["n"]), timeout=500)
                elif f["type"] == "checkbox":
                    el.set_checked(self.rng.random() < 0.5, timeout=500)
                else:
                    hostile = self.rng.random() < 0.3
                    el.fill(self.rng.choice(PAYLOADS) if hostile else self._plausible(f["type"]), timeout=500)
            except Exception:
                pass  # e.g. a re-render replaced the field or a hostile value is invalid for the input type
        return len(fields)

    OPENERS = "[data-a^='edit'], [data-a=addTime], [data-a=billTime], [data-a=changePasswordX]"

    def form_episode(self):
        idx = self.page.evaluate(LIST_VISIBLE_JS.replace("arguments[1]", f"'{FORBIDDEN}'"), self.OPENERS)
        if not idx:
            top = self.rng.choice(
                ["customers", "projects", "time", "expenses", "plan/contracts", "plan/timeoff", "plan/vacation", "team", "sales"]
            )
            self.say(f"episode: no form opener here, going to #/{top}")
            self.page.evaluate("h => { location.hash = h; }", f"#/{top}")
            return
        loc = self.page.locator(self.OPENERS).nth(self.rng.choice(idx))
        try:
            what = loc.evaluate("e => (e.dataset.a || e.tagName) + ' ' + (e.innerText || '').trim().slice(0, 25)")
            loc.click(timeout=700, no_wait_after=True)
            self.page.wait_for_selector("#modal-root > *", timeout=700)
        except Exception:
            return self.say("episode: opener did not open a form")
        n = self._fill_container("#modal-root")
        action = self.rng.choices(["#m-save", "#m-cancel", "#m-del"], [80, 12, 8])[0]
        self.say(f"episode: opened {what!r}, filled {n} fields, {action}")
        try:
            if self.page.query_selector(action):
                self.page.click(action, timeout=700, no_wait_after=True)
        except Exception:
            pass

    DOC_BUTTONS = (
        "[data-a=docSave], [data-a=addLine], [data-a=delLine], [data-a=docFinalize], [data-a=docAccept], "
        "[data-a=docToInvoice], [data-a=docPdf]"
    )

    def doc_episode(self):
        if not self.page.evaluate("() => location.hash.startsWith('#/doc/')"):
            h = self.rng.choice(["#/doc/offers/new", "#/doc/invoices/new"])
            self.say(f"episode: opening {h}")
            self.page.evaluate("h => { location.hash = h; }", h)
            return
        try:
            self.page.wait_for_selector("#d_totals", timeout=2000)
        except Exception:
            return self.say("episode: doc page not ready")
        n = self._fill_container("main")
        idx = self.page.evaluate(LIST_VISIBLE_JS.replace("arguments[1]", "'x-none'"), self.DOC_BUTTONS)
        if not idx:
            return
        loc = self.page.locator(self.DOC_BUTTONS).nth(self.rng.choice(idx))
        try:
            what = loc.evaluate("e => e.dataset.a")
            self.say(f"episode: doc editor, filled {n} fields, {what}")
            loc.click(timeout=700, no_wait_after=True)
        except Exception:
            pass

    MOVES = [
        ("click_random", 25),
        ("form_episode", 30),
        ("doc_episode", 8),
        ("type_random", 12),
        ("modal_action", 8),
        ("press_key", 5),
        ("go_route", 8),
        ("history", 4),
    ]

    # ---- invariants
    def settle(self):
        """Wait until the shell is rendered (it is replaced asynchronously after navigation)."""
        self.page.wait_for_selector("#app .side, #app .auth-wrap", timeout=8000)
        self.page.wait_for_timeout(30)

    def check(self, step_no: int):
        self.settle()
        self.watch.check()
        p = self.page
        assert p.evaluate("() => document.querySelector('#app').innerHTML.trim().length") > 0, "#app is empty"
        assert not p.evaluate("() => !!window.__xss"), "injected markup executed (window.__xss set)"
        assert p.evaluate(INJECTED_JS) == 0, "injected markup became real DOM elements (not escaped)"
        assert p.query_selector("#app .side"), "session lost: the login screen appeared during navigation"
        assert p.evaluate("() => !!localStorage.getItem('permita38.token')")
        text = norm(p.inner_text("main")) if p.query_selector("main") else ""
        if self.secrets is None:
            assert "Something went wrong" not in text, f"error page shown to the boss: {text[:200]}"
        for bad in ("[object Object]", "undefined"):
            assert bad not in text, f"{bad!r} rendered on the page: {text[:200]}"
        if self.secrets is not None:
            for s in self.secrets:
                assert s not in norm(p.inner_text("#app")) + norm(p.inner_text("#modal-root")), (
                    f"member can see boss-only data {s!r}"
                )
        if step_no % 12 == 0 and p.evaluate("() => document.querySelector('#modal-root').children.length > 0"):
            if p.query_selector("#m-cancel"):
                p.click("#m-cancel", timeout=3000)
                assert p.evaluate("() => document.querySelector('#modal-root').children.length === 0"), (
                    "modal does not close on Cancel"
                )

    def run(self, steps: int):
        names = [n for n, _ in self.MOVES]
        weights = [w for _, w in self.MOVES]
        self.page.wait_for_selector("#app .side")
        for i in range(1, steps + 1):
            getattr(self, self.rng.choices(names, weights)[0])()
            self.check(i)


def _run_monkey(seed, make_page, live_server, api, steps, member=False):
    rng = random.Random(seed)
    data = seed_dataset(api, rng)
    if member:
        r = api.http.post(
            "/users", json={"username": "mia", "name": "Mia Member", "role": "member", "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        m_api = Api(live_server.url)
        assert m_api.login("mia").status_code == 200
        token = m_api.http.headers["Authorization"].split()[1]
        page, watch = make_page(token=token)
    else:
        page, watch = make_page(token=api.http.headers["Authorization"].split()[1])
    page.context.on("page", lambda popup: popup.close())  # e.g. "Preview PDF" opens a tab
    page.goto(live_server.url + "/")
    monkey = Monkey(page, watch, rng, live_server.url, data["secrets"] if member else None)
    try:
        if member:
            # direct visits to boss routes must show nothing sensitive and offer no boss navigation
            monkey.page.wait_for_selector("#app .side")
            for link in ("customers", "plan", "sales", "expenses", "money", "team", "settings"):
                assert not page.query_selector(f'a.nav[href="#/{link}"]'), f"member sees nav entry {link}"
            ids = data["ids"]
            for r in [
                "customers",
                "plan",
                "plan/contracts",
                "projects",
                "sales",
                "sales/offers",
                "expenses",
                "money",
                "team",
                "settings",
                f"customer/{ids['customer']}",
                f"project/{ids['project']}",
                f"doc/invoices/{ids['invoice']}",
                f"doc/offers/{ids['offer']}",
            ]:
                page.evaluate("h => { location.hash = h; }", f"#/{r}")
                monkey.check(1)
                body = norm(page.inner_text("#app"))
                assert not any(s in body for s in data["secrets"]), f"member sees boss data on #/{r}: {body[:300]}"
        monkey.run(steps)
        print(
            f"monkey seed {seed}: {len(monkey.log)} moves, {watch.writes} successful writes, "
            f"{len(watch.http_4xx)} 4xx answers (validation/auth)"
        )
        if not member:
            assert watch.writes >= 3, "the monkey never managed to save anything - the test is not exercising the UI"
        # the backend survived: every collection still answers
        for coll in (
            "customers",
            "projects",
            "tasks",
            "entries",
            "contracts",
            "absences",
            "offers",
            "invoices",
            "expenses",
            "users",
            "templates",
        ):
            api.get(f"/{coll}")
        api.get("/finance")
        api.get("/forecast")
    except Exception as exc:
        print(f"\n=== monkey seed={seed} member={member} failed; replay: PERMITA38_E2E_SEED={seed} ===")
        print("\n".join(monkey.log[-60:]))
        try:
            page.screenshot(path=f"/tmp/e2e_monkey_seed{seed}{'_member' if member else ''}.png")
        except Exception:
            pass
        raise AssertionError(
            f"monkey seed {seed} (member={member}) failed after {len(monkey.log)} moves: {exc}\n"
            + "\n".join(monkey.log[-40:])
        ) from exc


@pytest.mark.parametrize("seed", seeds_from_env(MONKEY_SEEDS))
def test_monkey_as_boss(seed, make_page, live_server, api):
    api.setup_boss()
    _run_monkey(seed, make_page, live_server, api, env_int("PERMITA38_E2E_MONKEY_STEPS", 110))


def test_monkey_as_member(make_page, live_server, api):
    api.setup_boss()
    _run_monkey(
        seeds_from_env(MONKEY_SEEDS)[0],
        make_page,
        live_server,
        api,
        env_int("PERMITA38_E2E_MONKEY_STEPS", 110) // 2,
        member=True,
    )
