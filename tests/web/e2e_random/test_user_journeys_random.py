"""Seeded random *user sessions* driven through the real browser UI.

A model of what the user did (customers, projects, hours, offers, invoices, expenses, time off) is kept inside
the test, computed only from the random inputs. After every action the page is compared with the model and the
backend state (read through the API) is compared with the model, too.

Replay a failing run with:  PERMITA38_E2E_SEED=<seed> pytest tests/web/e2e_random/test_user_journeys_random.py
"""

import random
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import pytest

from .conftest import PASSWORD, Api
from .helpers import (
    company,
    env_int,
    eur,
    hm,
    money,
    norm,
    parse_duration,
    pick_weighted,
    report_failure,
    seeds_from_env,
    week_start,
    week_target_minutes,
)

pytestmark = pytest.mark.e2e_random

DEFAULT_RATE = 80
NAV_PAGES = [
    "today",
    "time",
    "plan",
    "plan/contracts",
    "plan/timeoff",
    "customers",
    "projects",
    "sales",
    "sales/offers",
    "expenses",
    "money",
    "team",
    "settings",
]


# ------------------------------------------------------------------ expected-state model
@dataclass
class MCustomer:
    name: str
    rate: int | None
    id: str | None = None


@dataclass
class MProject:
    name: str
    customer: MCustomer | None
    rate: int | None = None
    personal: bool = False
    id: str | None = None

    def effective_rate(self) -> int:
        """project rate > customer rate > default rate"""
        if self.rate:
            return self.rate
        if self.customer and self.customer.rate:
            return self.customer.rate
        return DEFAULT_RATE


@dataclass
class MEntry:
    tag: str
    minutes: int
    day: date
    project: MProject
    billable: bool = True
    invoiced: bool = False


@dataclass
class MDoc:
    kind: str  # offers | invoices
    customer: MCustomer
    lines: list[tuple[str, int, str, int, int | None]]  # description, qty, unit, price, explicit vat
    title: str = ""
    plan: bool = False
    number: str | None = None
    status: str = "draft"
    id: str | None = None
    project_name: str | None = None

    def totals(self):
        net = Decimal(0)
        by_rate: dict[int, Decimal] = {}
        for _, qty, _, price, vat in self.lines:
            amt = Decimal(str(qty)) * Decimal(str(price))
            net += amt
            r = 19 if vat is None else vat
            by_rate[r] = by_rate.get(r, Decimal(0)) + amt
        vat = {r: money(a * r / 100) for r, a in by_rate.items() if r}
        return money(net), vat, money(net + sum(vat.values(), Decimal(0)))


@dataclass
class MExpense:
    vendor: str
    net: int
    rate: int

    @property
    def gross(self) -> Decimal:
        return money(Decimal(self.net) * (100 + self.rate) / 100)

    @property
    def vat(self) -> Decimal:
        return self.gross - self.net


@dataclass
class Model:
    username: str = "boss"
    password: str = PASSWORD
    weekly_hours: float = 40
    customers: list[MCustomer] = field(default_factory=list)
    projects: list[MProject] = field(default_factory=list)
    entries: list[MEntry] = field(default_factory=list)
    docs: list[MDoc] = field(default_factory=list)
    expenses: list[MExpense] = field(default_factory=list)
    absences: list[tuple[date, date]] = field(default_factory=list)
    contracts: int = 0
    counters: dict = field(default_factory=lambda: {"tag": 0, "name": 0, "offers": 0, "invoices": 0})

    def absent_days(self) -> set[date]:
        out = set()
        for a, b in self.absences:
            d = a
            while d <= b:
                out.add(d)
                d += timedelta(days=1)
        return out

    def paid_invoices(self):
        return [d for d in self.docs if d.kind == "invoices" and d.status == "paid"]

    def unbilled(self):
        return [
            e
            for e in self.entries
            if e.billable and not e.invoiced and not e.project.personal and e.project.customer is not None
        ]


# ------------------------------------------------------------------ the browser driver
class Journey:
    def __init__(self, page, watch, live_server, api: Api, rng: random.Random, seed: int):
        self.page, self.watch, self.srv, self.api = page, watch, live_server, api
        self.rng, self.seed = rng, seed
        self.m = Model()
        self.log: list[str] = []
        self.today = date.today()
        self.year = self.today.year

    # ---- plumbing
    def say(self, msg: str):
        self.log.append(f"{len(self.log) + 1:02d} {msg}")

    def mark_old(self):
        self.page.evaluate("() => { const m = document.querySelector('main'); if (m) m.dataset.old = '1'; }")

    def wait_render(self):
        self.page.wait_for_selector("main:not([data-old])")

    def nav(self, h: str):
        """Go to '#/x' like a user following a link; waits for the new page to be rendered."""
        self.mark_old()
        same = self.page.evaluate("h => location.hash === h", h)
        if same:
            self.page.evaluate("() => render()")
        else:
            self.page.evaluate("h => { location.hash = h; }", h)
        self.wait_render()

    def click_render(self, selector: str):
        self.mark_old()
        self.page.click(selector)
        self.wait_render()

    def submit_modal(self, render_after=True):
        if render_after:
            self.mark_old()
        self.page.click("#m-save")
        try:
            self.page.wait_for_selector("#modal-root:empty", state="attached", timeout=6000)
        except Exception:
            toast = self.page.inner_text("#toast")
            raise AssertionError(f"modal did not close after saving; toast says {toast!r}") from None
        if render_after:
            self.wait_render()

    def fill_form(self, values: dict):
        for key, val in values.items():
            sel = f"#f_{key}"
            if isinstance(val, bool):
                self.page.set_checked(sel, val)
            elif key.endswith("_id") or key in ("vat_rate", "kind", "category"):
                self.page.select_option(sel, str(val))
            else:
                self.page.fill(sel, str(val))

    def main_text(self) -> str:
        return norm(self.page.inner_text("main"))

    def hash_id(self) -> str:
        return self.page.evaluate("() => location.hash.split('/').pop()")

    def kpi(self, label: str) -> str:
        return norm(self.page.locator(".kpi", has_text=label).first.locator(".v").inner_text())

    # ---- onboarding and authentication through the real screens
    def first_run(self):
        p = self.page
        p.goto(self.srv.url + "/")
        p.wait_for_selector("#setup-form")
        self.m.weekly_hours = self.rng.choice([20, 32, 37.5, 40])
        # validation messages of the setup screen
        p.fill("#s_name", "Test Chef")
        p.fill("#s_user", self.m.username)
        p.fill("#s_pw", "short")
        p.fill("#s_pw2", "short")
        p.click("#s_go")
        assert "at least 8" in p.inner_text("#auth-err")
        p.fill("#s_pw", self.m.password)
        p.fill("#s_pw2", self.m.password + "x")
        p.click("#s_go")
        assert "do not match" in p.inner_text("#auth-err")
        p.fill("#s_pw2", self.m.password)
        p.fill("#s_h", str(self.m.weekly_hours))
        p.fill("#s_co", "Zufall & Söhne Studio")
        p.click("#s_go")
        p.wait_for_selector(".side")
        self.say(f"setup boss (weekly_hours={self.m.weekly_hours})")
        assert self.api.login(self.m.username, self.m.password).status_code == 200
        me = self.api.get("/auth/me")
        assert me["name"] == "Test Chef" and me["role"] == "boss" and me["weekly_hours"] == self.m.weekly_hours
        assert self.api.get("/settings")["company_name"] == "Zufall & Söhne Studio"
        # the setup screen must be gone for good
        assert (
            self.api.http.post("/auth/setup", json={"username": "x1", "name": "X", "password": PASSWORD}).status_code
            == 400
        )

    def sign_in_ui(self, user: str, password: str):
        p = self.page
        p.wait_for_selector("#login-form")
        p.fill("#l_user", user)
        p.fill("#l_pw", password)
        p.click("#l_go")

    # ---- actions
    def a_add_customer(self):
        name = company(self.rng, self.m.counters["name"])
        self.m.counters["name"] += 1
        rate = self.rng.choice([None, 60, 95, 120, 150])
        self.nav("#/customers")
        self.page.click("[data-a=editCustomer]")
        vals = {"name": name, "city": self.rng.choice(["Berlin", "München", "Köln", "Dresden"])}
        if rate:
            vals["rate"] = rate
        if self.rng.random() < 0.5:
            vals["tax_number"] = (
                f"{self.rng.randint(10, 99)}/{self.rng.randint(100, 999)}/{self.rng.randint(10000, 99999)}"
            )
        self.fill_form(vals)
        self.mark_old()
        self.page.click("#m-save")
        self.wait_render()
        c = MCustomer(name, rate, self.hash_id())
        self.m.customers.append(c)
        self.say(f"customer {name!r} rate={rate}")
        assert norm(self.page.inner_text("main h1")) == name
        assert self.hash_id() == c.id

    def a_add_project(self):
        personal = not self.m.customers or self.rng.random() < 0.15
        cust = None if personal else self.rng.choice(self.m.customers)
        rate = None if personal else self.rng.choice([None, None, 70, 110, 130])
        name = f"Projekt {self.m.counters['name']} {self.rng.choice(['Relaunch', 'Beratung', 'App', 'Audit'])}"
        self.m.counters["name"] += 1
        self.nav("#/projects")
        self.page.click("[data-a=editProject]")
        vals: dict = {"name": name}
        if personal:
            vals["personal"] = True
        else:
            vals["customer_id"] = cust.id
            vals["start"], vals["end"] = "2026-01-01", "2027-12-31"  # customer projects need a period
            if rate:
                vals["rate"] = rate
        self.fill_form(vals)
        self.mark_old()
        self.page.click("#m-save")
        self.wait_render()
        p = MProject(name, cust, rate, personal, self.hash_id())
        self.m.projects.append(p)
        self.say(f"project {name!r} customer={cust.name if cust else None} rate={rate} personal={personal}")
        assert norm(self.page.inner_text("main h1")) == name

    def _new_tag(self) -> str:
        self.m.counters["tag"] += 1
        return f"e{self.m.counters['tag']}"

    def a_manual_time(self):
        proj = self.rng.choice(self.m.projects)
        minutes = self.rng.choice(range(10, 300, 5))
        style = self.rng.choice(["hmm", "dec", "min", "hm", "range"])
        if style == "dec":
            minutes = self.rng.randint(3, 40) * 6
            text = f"{minutes / 60:.1f}"
        elif style == "hmm":
            text = f"{minutes // 60}:{minutes % 60:02d}"
        elif style == "min":
            text = f"{minutes}m"
        elif style == "hm":
            minutes = max(minutes, 60) // 5 * 5
            text = f"{minutes // 60}h {minutes % 60}m"
        else:
            text = ""
            start = self.rng.randint(6, 14) * 60 + self.rng.choice([0, 15, 30, 45])
            minutes = self.rng.choice(range(15, 240, 15))
        if text:
            assert parse_duration(text) == minutes, (text, minutes)  # self-check of the spec
        ws = week_start(self.today)
        if self.rng.random() < 0.7:
            day = ws + timedelta(days=self.rng.randint(0, (self.today - ws).days))
        else:
            day = ws - timedelta(days=self.rng.randint(1, 7))
        billable = self.rng.random() < 0.85
        tag = self._new_tag()
        self.nav("#/time")
        self.page.click("[data-a=addTime]")
        vals = {"date": day.isoformat(), "project_id": proj.id, "note": f"{tag} manual", "billable": billable}
        if text:
            vals["dur"] = text
        else:
            vals["start"] = f"{start // 60:02d}:{start % 60:02d}"
            end = start + minutes
            vals["end"] = f"{end // 60:02d}:{end % 60:02d}"
        self.fill_form(vals)
        self.submit_modal()
        self.m.entries.append(MEntry(tag, minutes, day, proj, billable))
        self.say(
            f"time {tag} {text or vals['start'] + '-' + vals['end']} = {minutes}min on {day} {proj.name!r} "
            f"billable={billable}"
        )
        self.check_week_ui()

    def a_timer(self):
        proj = self.rng.choice(self.m.projects)
        tag = self._new_tag()
        self.nav("#/today")
        self.page.select_option("#t_proj", proj.id)
        self.page.fill("#t_note", f"{tag} timer")
        self.click_render("[data-a=timerStart]")
        self.page.wait_for_selector("#clock")
        time.sleep(1.2)
        assert self.page.inner_text("#clock") != "00:00:00", "the running timer does not tick"
        self.click_render("[data-a=timerStop]")
        self.m.entries.append(MEntry(tag, 1, self.today, proj, True))
        self.say(f"timer {tag} on {proj.name!r} -> 1min")
        self.check_week_ui()

    def _this_week_editable(self):
        ws = week_start(self.today)
        return [e for e in self.m.entries if e.day >= ws and not e.invoiced]

    def _entry_row(self, e: MEntry):
        rec = next(x for x in self.api.get("/entries") if (x.get("note") or "").startswith(e.tag + " "))
        self.nav("#/time")
        self.page.click(f'tr[data-a=editEntry][data-id="{rec["id"]}"]')
        self.page.wait_for_selector("#f_dur")

    def a_edit_entry(self):
        e = self.rng.choice(self._this_week_editable())
        minutes = self.rng.choice(range(10, 240, 5))
        self._entry_row(e)
        self.page.fill("#f_dur", f"{minutes}m")
        self.page.fill("#f_note", f"{e.tag} edited")
        self.submit_modal()
        self.say(f"edit {e.tag}: {e.minutes} -> {minutes}min")
        e.minutes = minutes
        self.check_week_ui()

    def a_delete_entry(self):
        e = self.rng.choice(self._this_week_editable())
        self._entry_row(e)
        self.mark_old()
        self.page.click("#m-del")
        self.wait_render()
        self.m.entries.remove(e)
        self.say(f"delete {e.tag}")
        self.check_week_ui()

    # -- offers and invoices
    def _random_lines(self):
        lines = []
        for i in range(self.rng.randint(1, 4)):
            vat = self.rng.choice([None, None, None, 7, 19, 0])
            lines.append(
                (
                    f"Leistung {i + 1} {self.rng.choice(['Konzept', 'Design', 'Umsetzung', 'Test'])}",
                    self.rng.choice([1, 2, 3, 4, 5, 8, 10]),
                    self.rng.choice(["h", "Tag", "Pauschal"]),
                    self.rng.randint(20, 200),
                    vat,
                )
            )
        return lines

    def _check_totals_box(self, doc: MDoc):
        net, vat, gross = doc.totals()
        box = norm(self.page.inner_text("#d_totals"))
        assert f"Net {eur(net)}" in box, (box, eur(net))
        for rate, amt in vat.items():
            assert f"VAT {rate}% {eur(amt)}" in box, (box, rate, eur(amt))
        assert f"Total {eur(gross)}" in box, (box, eur(gross))

    def a_create_offer(self):
        cust = self.rng.choice(self.m.customers)
        doc = MDoc(
            "offers",
            cust,
            self._random_lines(),
            title=f"Angebot {self.m.counters['name']} {cust.name}"[:60],
            plan=self.rng.random() < 0.6,
        )
        self.m.counters["name"] += 1
        self.nav("#/sales/offers")
        self.click_render("[data-a=newDoc][data-coll=offers]")
        self.page.select_option("#d_cust", cust.id)
        self.page.fill("#d_title", doc.title)
        self._fill_line(0, doc.lines[0])
        if doc.plan:
            start = self.today + timedelta(days=self.rng.randint(7, 40))
            self.page.fill("#p_s", start.isoformat())
            self.page.fill("#p_e", (start + timedelta(days=self.rng.randint(14, 90))).isoformat())
            self.page.fill("#p_hpw", str(self.rng.choice([5, 8, 10, 12])))
        self._save_new_doc(doc)
        # More lines are added on the saved draft: "+ Add line" on a *new* document resets it (see
        # test_regressions.py::test_add_line_on_new_document), so the journey avoids that path.
        for i, line in enumerate(doc.lines[1:], start=1):
            self.click_render("[data-a=addLine]")
            self._fill_line(i, line)
        self._check_totals_box(doc)
        if len(doc.lines) > 1:
            self.click_render("[data-a=docSave]")
            self._check_totals_box(doc)
        self.say(f"offer draft {doc.title!r} lines={len(doc.lines)} plan={doc.plan}")

    def _fill_line(self, i: int, line):
        desc, qty, unit, price, vat = line
        for k, v in (("description", desc), ("qty", qty), ("unit", unit), ("price", price)):
            self.page.fill(f'[data-l="{i}"][data-k="{k}"]', str(v))
        if vat is not None:
            self.page.fill(f'[data-l="{i}"][data-k="vat"]', str(vat))

    def _save_new_doc(self, doc: MDoc):
        self.mark_old()
        self.page.click("[data-a=docSave]")
        self.page.wait_for_function("() => location.hash.startsWith('#/doc/offers/') && !location.hash.endsWith('new')")
        self.wait_render()
        doc.id = self.hash_id()
        self.m.docs.append(doc)
        assert "draft" in norm(self.page.inner_text("main h1")).lower()

    def _open_doc(self, doc: MDoc):
        self.nav(f"#/sales/{doc.kind}")
        self.mark_old()
        self.page.click(f'tr[data-href="#/doc/{doc.kind}/{doc.id}"]')
        self.wait_render()
        self.page.wait_for_selector("#d_totals")
        self._check_totals_box(doc)

    def _finalize(self, doc: MDoc):
        self._open_doc(doc)
        n = self.m.counters[doc.kind] + 1
        self.m.counters[doc.kind] = n
        prefix = "AN" if doc.kind == "offers" else "RE"
        doc.number = f"{prefix}-{self.year}-{n:04d}"
        doc.status = "sent"
        self.click_render("[data-a=docFinalize]")
        h1 = norm(self.page.inner_text("main h1"))
        assert doc.number in h1 and "sent" in h1, h1
        self.say(f"finalize {doc.kind[:-1]} {doc.number}")

    def a_finalize_offer(self):
        self._finalize(self.rng.choice([d for d in self.m.docs if d.kind == "offers" and d.status == "draft"]))

    def a_accept_offer(self):
        doc = self.rng.choice([d for d in self.m.docs if d.kind == "offers" and d.status == "sent"])
        self._open_doc(doc)
        self.click_render("[data-a=docAccept]")
        assert self.page.evaluate("() => location.hash").startswith("#/project/")
        proj = MProject(doc.title, doc.customer, None, False, self.hash_id())
        self.m.projects.append(proj)
        doc.status = "accepted"
        if doc.plan:
            self.m.contracts += 1
        self.say(f"accept {doc.number} -> project {doc.title!r}, contract={doc.plan}")
        assert norm(self.page.inner_text("main h1")) == doc.title

    def a_invoice_from_time(self):
        cust = self.rng.choice(
            sorted({e.project.customer.id: e.project.customer for e in self.m.unbilled()}.values(), key=lambda c: c.id)
        )
        picked = [e for e in self.m.unbilled() if e.project.customer is cust]
        groups: dict[str, list[MEntry]] = {}
        for e in picked:
            groups.setdefault(e.project.id, []).append(e)
        lines = []
        for entries in groups.values():
            proj = entries[0].project
            qty = round(sum(e.minutes for e in entries) / 60, 2)  # hours, 2 decimals, as printed on the invoice
            lines.append((proj.name, qty, "h", proj.effective_rate(), None))
        doc = MDoc("invoices", cust, lines)
        self.nav("#/sales")
        self.page.click("[data-a=billTime]")
        self.page.select_option("#f_customer_id", cust.id)
        self.mark_old()
        self.page.click("#m-save")
        self.wait_render()
        self.page.wait_for_selector("#d_totals")
        doc.id = self.hash_id()
        assert self.page.evaluate("() => location.hash").startswith("#/doc/invoices/")
        # expected totals computed from the entries' minutes and the rate rule
        net = money(sum(Decimal(str(q)) * Decimal(p) for _, q, _, p, _ in lines))
        _, _, gross = doc.totals()
        box = norm(self.page.inner_text("#d_totals"))
        assert f"Net {eur(net)}" in box and f"Total {eur(gross)}" in box, (box, eur(net), eur(gross))
        assert self.page.locator("input[data-k=description]").count() == len(lines)
        for e in picked:
            e.invoiced = True
        self.m.docs.append(doc)
        self.say(f"invoice from time for {cust.name!r}: {len(picked)} entries, net {eur(net)}")

    def a_finalize_invoice(self):
        self._finalize(self.rng.choice([d for d in self.m.docs if d.kind == "invoices" and d.status == "draft"]))

    def a_mark_paid(self):
        doc = self.rng.choice([d for d in self.m.docs if d.kind == "invoices" and d.status == "sent"])
        self._open_doc(doc)
        # The user enters the "Paid on" date first: choosing "Paid" in the status menu alone leaves the date empty
        # (see test_regressions.py::test_mark_paid_sets_paid_date).
        self.page.fill("#d_paid", self.today.isoformat())
        self.mark_old()
        self.page.select_option("select[data-change=docStatus]", "paid")
        self.wait_render()
        doc.status = "paid"
        self.say(f"mark {doc.number} paid")
        assert "paid" in norm(self.page.inner_text("main h1"))

    # -- expenses, time off
    def a_add_expense(self):
        exp = MExpense(f"Lieferant {self.m.counters['name']}", self.rng.randint(10, 500), self.rng.choice([19, 7, 0]))
        self.m.counters["name"] += 1
        self.nav("#/expenses")
        self.page.click("[data-a=editExpense]")
        self.page.fill("#f_vendor", exp.vendor)
        self.page.fill("#f_gross", str(exp.gross))
        self.page.select_option("#f_vat_rate", str(exp.rate))
        self.submit_modal()
        self.m.expenses.append(exp)
        self.say(f"expense {exp.vendor!r} gross {exp.gross} @ {exp.rate}%")
        assert exp.vendor in self.main_text()

    def a_add_vacation(self):
        if self.rng.random() < 0.4:
            start = week_start(self.today) + timedelta(days=self.rng.randint(0, 4))
        else:
            start = self.today + timedelta(days=self.rng.randint(1, 45))
        end = start + timedelta(days=self.rng.randint(0, 6))
        self.nav("#/plan/timeoff")
        self.page.click("[data-a=editAbsence]")
        self.fill_form({"kind": "vacation", "start": start.isoformat(), "end": end.isoformat(), "note": "Urlaub"})
        self.submit_modal()
        self.m.absences.append((start, end))
        self.say(f"time off {start} .. {end}")
        assert "Urlaub" in self.main_text()
        self.check_week_ui()

    # -- navigation / money
    def a_navigate(self):
        page = self.rng.choice(NAV_PAGES)
        top = page.split("/")[0]
        if "/" not in page and self.page.locator(f'a.nav[href="#/{top}"]').count():
            self.mark_old()
            self.page.click(f'a.nav[href="#/{top}"]')
            self.wait_render()
        else:
            self.nav("#/" + page)
        text = self.main_text()
        assert "Something went wrong" not in text, text[:300]
        self.say(f"navigate #/{page}")
        if page == "money":
            self.check_money_ui()

    def check_money_ui(self):
        paid = self.m.paid_invoices()
        revenue = sum((d.totals()[0] for d in paid), Decimal(0))
        expenses = sum((Decimal(e.net) for e in self.m.expenses), Decimal(0))
        assert self.kpi("Revenue (net)") == eur(revenue), (self.kpi("Revenue (net)"), eur(revenue))
        assert self.kpi("Expenses (net)") == eur(expenses)
        assert self.kpi("Profit") == eur(revenue - expenses)

    def a_change_password(self):
        new = f"neues passwort {self.rng.randint(100, 999)}"
        self.page.click("[data-a=changePassword]")
        self.page.fill("#cp_old", self.m.password)
        self.page.fill("#cp_new", new)
        self.page.fill("#cp_new2", new)
        self.submit_modal(render_after=False)
        old = self.m.password
        self.m.password = new
        assert (
            self.api.http.post("/auth/login", json={"username": self.m.username, "password": old}).status_code == 401
        ), "old password still works"
        assert self.api.login(self.m.username, new).status_code == 200
        self.say("changed password")
        self.a_relogin(wrong_first=False)

    def a_relogin(self, wrong_first=True):
        self.page.click("[data-a=logout]")
        if wrong_first:
            self.sign_in_ui(self.m.username, "falsches-passwort")
            self.page.wait_for_selector("#auth-err:not([hidden])")
            assert "Wrong username or password" in self.page.inner_text("#auth-err")
            assert not self.page.evaluate("() => localStorage.getItem('permita38.token')")
        self.sign_in_ui(self.m.username, self.m.password)
        self.page.wait_for_selector(".side")
        self.say("signed out, " + ("wrong password rejected, " if wrong_first else "") + "signed in again")
        assert self.page.evaluate("() => localStorage.getItem('permita38.token')")

    # ---- verification
    def check_week_ui(self):
        """The Today page must show worked/target/remaining exactly as the model predicts."""
        self.nav("#/today")
        ws = week_start(self.today)
        worked = sum(e.minutes for e in self.m.entries if ws <= e.day <= ws + timedelta(days=6))
        target = week_target_minutes(self.today, self.m.weekly_hours, self.m.absent_days())
        text = self.main_text()
        assert f"{hm(worked)} of {hm(target)} h" in text, (hm(worked), hm(target), text[:400])
        remaining = max(target - worked, 0)
        if remaining:
            assert f"{hm(remaining)} h to go" in text, (hm(remaining), text[:400])
        else:
            assert "Weekly goal reached" in text
        today_min = sum(e.minutes for e in self.m.entries if e.day == self.today)
        assert f"Today · {hm(today_min)} h" in text, (hm(today_min), text[:600])

    def verify_backend(self):
        api, m = self.api, self.m
        customers = {c["name"]: c for c in api.get("/customers")}
        assert set(customers) == {c.name for c in m.customers}, (set(customers), [c.name for c in m.customers])
        for c in m.customers:
            assert customers[c.name].get("rate") == c.rate and customers[c.name]["id"] == c.id
        projects = {p["name"]: p for p in api.get("/projects")}
        assert set(projects) == {p.name for p in m.projects}
        for p in m.projects:
            rec = projects[p.name]
            assert rec["id"] == p.id and rec.get("rate") == p.rate and bool(rec.get("personal")) == p.personal
            assert rec.get("customer_id") == (p.customer.id if p.customer else None), rec
        entries = {(e.get("note") or "").split()[0]: e for e in api.get("/entries")}
        assert set(entries) == {e.tag for e in m.entries}, (set(entries), [e.tag for e in m.entries])
        for e in m.entries:
            rec = entries[e.tag]
            assert rec["minutes"] == e.minutes and rec["date"] == e.day.isoformat(), (e, rec)
            assert rec["project_id"] == e.project.id and rec.get("billable", True) == e.billable
            assert bool(rec.get("invoice_id")) == e.invoiced, (e, rec)
        for kind in ("offers", "invoices"):
            recs = {d["id"]: d for d in api.get(f"/{kind}")}
            mine = [d for d in m.docs if d.kind == kind]
            assert set(recs) == {d.id for d in mine}
            for d in mine:
                rec = recs[d.id]
                assert rec["status"] == d.status and rec.get("number") == d.number, (d, rec)
                assert rec["customer_id"] == d.customer.id
                net, vat, gross = d.totals()
                amounts = sum(
                    (money(Decimal(str(ln["qty"])) * Decimal(str(ln["price"]))) for ln in rec["lines"]), Decimal(0)
                )
                assert money(amounts) == net, (d, rec["lines"])
                assert all(
                    money(ln["amount"]) == money(Decimal(str(ln["qty"])) * Decimal(str(ln["price"])))
                    for ln in rec["lines"]
                )
                if d.number:
                    pdf = api.http.get(f"/{kind}/{d.id}/pdf")
                    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), pdf.status_code
        exps = {x["vendor"]: x for x in api.get("/expenses")}
        assert set(exps) == {e.vendor for e in m.expenses}
        for e in m.expenses:
            assert money(exps[e.vendor]["net"]) == money(e.net) and money(exps[e.vendor]["vat"]) == e.vat, exps[
                e.vendor
            ]
        assert len(api.get("/absences")) == len(m.absences)
        assert len(api.get("/contracts")) == m.contracts
        fin = api.get("/finance", year=self.year)["totals"]
        revenue = sum((d.totals()[0] for d in m.paid_invoices()), Decimal(0))
        vat_out = sum((sum(d.totals()[1].values(), Decimal(0)) for d in m.paid_invoices()), Decimal(0))
        expenses = sum((Decimal(e.net) for e in m.expenses), Decimal(0))
        # VAT on a half-cent tie (e.g. 19% of 621.50) is rounded differently by the backend (binary floats) than by
        # the editor/Decimal half-up: allow one cent per VAT group (see test_regressions.py::test_vat_rounding...).
        groups = sum(len(d.totals()[1]) for d in m.paid_invoices())
        assert money(fin["revenue"]) == revenue, (fin, revenue)
        assert abs(money(fin["vat_out"]) - vat_out) <= Decimal("0.01") * groups, (fin, vat_out)
        assert money(fin["expenses"]) == expenses and money(fin["profit"]) == revenue - expenses
        assert money(fin["vat_in"]) == sum((e.vat for e in m.expenses), Decimal(0))
        fc = api.get("/forecast", weeks=2)["weeks"][0]
        target = week_target_minutes(self.today, m.weekly_hours, m.absent_days())
        assert round(fc["capacity"] * 60) == pytest.approx(target, abs=4), (fc, target)

    # ---- driver
    def available(self) -> list[tuple[str, int]]:
        m = self.m
        acts: list[tuple[str, int]] = [
            ("navigate", 3),
            ("add_customer", 4),
            ("add_expense", 3),
            ("add_vacation", 2),
            ("relogin", 1),
            ("change_password", 1),
        ]
        if m.customers:
            acts += [("add_project", 4), ("create_offer", 4)]
        if m.projects:
            acts += [("manual_time", 9), ("timer", 2)]
        if self._this_week_editable():
            acts += [("edit_entry", 3), ("delete_entry", 2)]
        if any(d.kind == "offers" and d.status == "draft" for d in m.docs):
            acts.append(("finalize_offer", 4))
        if any(d.kind == "offers" and d.status == "sent" for d in m.docs):
            acts.append(("accept_offer", 3))
        if m.unbilled():
            acts.append(("invoice_from_time", 5))
        if any(d.kind == "invoices" and d.status == "draft" for d in m.docs):
            acts.append(("finalize_invoice", 5))
        if any(d.kind == "invoices" and d.status == "sent" for d in m.docs):
            acts.append(("mark_paid", 5))
        return acts

    def step(self):
        if not self.m.customers:
            name = "add_customer"
        elif not self.m.projects:
            name = "add_project"
        else:
            name = pick_weighted(self.rng, self.available())
        getattr(self, "a_" + name)()
        self.watch.check()
        self.verify_backend()

    def run(self, n_actions: int):
        self.first_run()
        self.watch.check()
        for _ in range(n_actions):
            self.step()
        self.nav("#/money")
        self.check_money_ui()
        self.say("final money page check")


@pytest.mark.parametrize("seed", seeds_from_env())
def test_random_user_journey(seed, make_page, live_server, api):
    rng = random.Random(seed)
    page, watch = make_page()
    watch.rng = rng
    j = Journey(page, watch, live_server, api, rng, seed)
    try:
        j.run(env_int("PERMITA38_E2E_ACTIONS", 20))
        # the only HTTP errors a well-behaved user may trigger: the logged-out bootstrap and wrong passwords
        unexpected = [x for x in watch.http_4xx if not x.startswith("401 ")]
        assert not unexpected, unexpected
    except Exception as exc:
        detail = report_failure("journey", seed, j.log)
        try:
            page.screenshot(path=f"/tmp/e2e_random_seed{seed}.png")
        except Exception:
            pass
        raise AssertionError(
            f"seed {seed} failed after {len(j.log)} actions: {exc}\n{detail}\n{traceback.format_exc(limit=3)}"
        ) from exc
    print(f"seed {seed}: {len(j.log)} actions OK\n" + "\n".join(j.log))
