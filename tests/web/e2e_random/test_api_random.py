"""Seeded random *API* sessions against a real server (no browser): a boss and a member fire random,
state-dependent requests; every response is compared with an independent model of what should have happened.

Replay a failing run with:  PERMITA38_E2E_SEED=<seed> pytest tests/web/e2e_random/test_api_random.py
"""

import base64
import hashlib
import hmac
import json
import random
import string
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

import httpx
import pytest

from .conftest import PASSWORD, Api
from .helpers import (
    company,
    MONEY_KEYS,
    dec,
    env_int,
    money,
    money_eq,
    money_in,
    pick_weighted,
    report_failure,
    seeds_from_env,
    week_start,
    week_target_minutes,
)

pytestmark = pytest.mark.e2e_random

SECRET = "e2e-secret-key"  # see conftest.LiveServer
COLLS = ["customers", "projects", "tasks", "entries", "expenses", "absences", "contracts"]


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_token(sub: str, secret: str = SECRET, exp_in: int = 3600, alg: str = "HS256") -> str:
    now = int(time.time())
    head = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = b64(json.dumps({"sub": sub, "iat": now, "exp": now + exp_in}).encode())
    sig = b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig if alg != 'none' else ''}"


def _as_float(v):
    try:
        return float(v)
    except ValueError:
        return v  # junk text that a fuzz payload stored; the date/number checks do not apply


def same(a, b, key: str = "") -> bool:
    if key in MONEY_KEYS:  # responses carry money as decimal strings; the model keeps what was sent
        return money_eq(a, b)
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9
    return a == b


class ApiSim:
    def __init__(self, boss: Api, member: Api, base_url: str, rng: random.Random):
        self.boss, self.member, self.base, self.rng = boss, member, base_url, rng
        self.boss_id = boss.get("/auth/me")["id"]
        self.member_id = member.get("/auth/me")["id"]
        self.model: dict[str, dict[str, dict]] = {c: {} for c in COLLS}
        self.docs: dict[str, dict[str, dict]] = {"offers": {}, "invoices": {}}
        self.numbers = {"offers": 0, "invoices": 0}
        self.credit_notes = 0  # numbered credit notes (storno, partial); they are documents in /invoices too
        self.timer_running = False
        self.log: list[str] = []
        self.n = 0
        self.today = date.today()

    def say(self, msg):
        self.log.append(f"{len(self.log) + 1:03d} {msg}")

    # ---------------------------------------------------------------- generic CRUD with model comparison
    def ids(self, coll):
        return list(self.model[coll])

    def body(self, coll) -> dict:
        r, self.n = self.rng, self.n + 1
        b: dict
        if coll == "customers":
            b = {
                "name": company(r, self.n),
                "city": r.choice(["Köln", "Bonn", None]),
                "rate": money_in(r, r.choice([None, 60.5, 100])),
            }
        elif coll == "projects":
            cid = r.choice(self.ids("customers") or [None])
            b = {
                "name": f"Projekt {self.n}",
                "customer_id": cid,
                "kind": "customer" if cid else "internal",
                "rate": money_in(r, r.choice([None, 75, 99.5])),
                "budget_hours": r.choice([None, 20, 100.5]),
            }
        elif coll == "tasks":
            b = {
                "project_id": r.choice(self.ids("projects")),
                "name": f"Aufgabe {self.n}",
                "rate": money_in(r, r.choice([None, 150])),
            }
        elif coll == "entries":
            d = week_start(self.today) - timedelta(days=r.randint(0, 6))
            room = self.free_minutes(d.isoformat())
            if room < 1:
                self.say(f"skip entry: {d.isoformat()} is full (max 24 h per person and day)")
                return
            b = {
                "user_id": self.boss_id,
                "date": d.isoformat(),
                "minutes": r.randint(1, min(600, room)),
                "project_id": r.choice(self.ids("projects") or [None]),
                "billable": r.random() < 0.8,
                "note": f"n{self.n} ünï",
            }
        elif coll == "expenses":
            b = {
                "date": self.today.isoformat(),
                "net": money_in(r, r.randint(1, 900) / 4),
                "vat": money_in(r, r.choice([0, 1.9])),
                "vendor": f"V{self.n}",
                "category": r.choice(["Other", "Travel"]),
                "deductible": r.choice([100, 70]),
            }
        elif coll == "absences":
            s = self.today + timedelta(days=r.randint(-3, 30))
            b = {
                "user_id": self.boss_id,
                "kind": r.choice(["vacation", "sick"]),
                "start": s.isoformat(),
                "end": (s + timedelta(days=r.randint(0, 5))).isoformat(),
            }
        else:  # contracts
            s = self.today + timedelta(days=r.randint(-10, 20))
            b = {
                "title": f"Vertrag {self.n}",
                "start": s.isoformat(),
                "end": (s + timedelta(days=40)).isoformat(),
                "project_id": r.choice(self.customer_projects()),
                **r.choice([{"hours_per_week": r.choice([4, 10.5])}, {"total_hours": r.choice([40, 120.5])}]),
            }
        if r.random() < 0.2:  # unknown fields must survive (files written by newer versions/other devices)
            b["x_future_field"] = {"nested": [1, "zwei", None]}
        return b

    def customer_projects(self) -> list[str]:
        return [i for i, p in self.model["projects"].items() if p.get("customer_id")]

    def can(self, coll) -> bool:
        need = {"projects": [], "tasks": ["projects"]}.get(coll, [])
        return all(self.model[c] for c in need) and (coll != "contracts" or bool(self.customer_projects()))

    DAY_LIMIT = 1380  # a person may log at most 24 h a day; stay 60 min below it so timers and member entries fit

    def free_minutes(self, day: str) -> int:
        used = sum(
            e["minutes"]
            for e in self.model["entries"].values()
            if e.get("date") == day and e.get("user_id") == self.boss_id
        )
        return self.DAY_LIMIT - used

    def entry_billable(self, b: dict) -> bool:
        """Entries on an internal project (no customer or kind internal) are always stored billable false."""
        proj = self.model["projects"].get(b.get("project_id") or "")
        if proj and (not proj.get("customer_id") or proj.get("kind") == "internal"):
            return False
        return bool(b.get("billable", True))

    def op_create(self):
        coll = self.rng.choice([c for c in COLLS if self.can(c)])
        b = self.body(coll)
        r = self.boss.http.post(f"/{coll}", json=b)
        assert r.status_code in (200, 201), (coll, b, r.status_code, r.text)
        rec = r.json()
        assert rec["id"] and rec["created"] and rec["updated"]
        expect = {k: v for k, v in b.items() if v is not None}
        if coll == "entries":
            expect["billable"] = self.entry_billable(b)
        self.model[coll][rec["id"]] = expect
        self.say(f"create {coll} {rec['id']}")
        self.check(coll)

    def op_update(self):
        coll = self.rng.choice([c for c in COLLS if self.model[c] and self.can(c)])
        rid = self.rng.choice(self.ids(coll))
        if coll == "entries" and self.model[coll][rid].get("invoice_id"):
            return
        b = self.body(coll)
        if coll == "projects" and any(c.get("project_id") == rid for c in self.model["contracts"].values()):
            b["customer_id"] = self.model["projects"][rid]["customer_id"]  # a contract needs a customer project
            b["kind"] = "customer"
        r = self.boss.http.put(f"/{coll}/{rid}", json=b)
        assert r.status_code == 200, (coll, b, r.status_code, r.text)
        assert r.json()["id"] == rid
        expect = {k: v for k, v in b.items() if v is not None}
        if coll == "entries":
            expect["billable"] = self.entry_billable(b)
        self.model[coll][rid] = expect
        self.say(f"update {coll} {rid}")
        self.check(coll)

    def op_delete(self):
        coll = self.rng.choice([c for c in COLLS if self.model[c]])
        rid = self.rng.choice(self.ids(coll))
        r = self.boss.http.delete(f"/{coll}/{rid}")
        if coll == "projects" and any(
            rec.get("project_id") == rid for c in COLLS for rec in self.model[c].values()
        ):  # a project is deleted only when nothing uses it
            assert r.status_code == 409, (coll, r.status_code, r.text)
            assert "archive it instead" in r.json()["detail"], r.text
            assert self.boss.http.get(f"/{coll}/{rid}").status_code == 200
            self.say(f"delete {coll} {rid} refused: still used")
            self.check(coll)
            return
        assert r.status_code == 204, (coll, r.status_code)
        del self.model[coll][rid]
        assert self.boss.http.get(f"/{coll}/{rid}").status_code == 404
        assert self.boss.http.delete(f"/{coll}/{rid}").status_code == 204  # idempotent
        self.say(f"delete {coll} {rid}")
        self.check(coll)

    def check(self, coll):
        listing = {r["id"]: r for r in self.boss.get(f"/{coll}")}
        assert set(listing) == set(self.model[coll]), (coll, set(listing) ^ set(self.model[coll]))
        for rid, exp in self.model[coll].items():
            rec = listing[rid]
            for k, v in exp.items():
                assert same(rec.get(k), v, k), (coll, rid, k, rec.get(k), v)
        if listing:  # single GET agrees with the listing
            rid = self.rng.choice(list(listing))
            assert self.boss.get(f"/{coll}/{rid}") == listing[rid]

    # ---------------------------------------------------------------- offers / invoices
    def doc_body(self, kind) -> dict:
        r = self.rng
        cid = r.choice(self.ids("customers"))
        lines = [
            {
                "description": f"Pos {i}",
                "qty": r.choice([1, 2, 2.5, 7.25]),
                "unit": "h",
                "price": money_in(r, r.choice([20, 33.33, 99.99, 150])),
            }
            for i in range(r.randint(1, 4))
        ]
        body = {"customer_id": cid, "title": f"{kind} {self.n}", "lines": lines, "status": "paid", "number": "HACK-1"}
        if kind == "invoices" and r.random() < 0.5:
            body["tax_treatment"] = "standard"  # the select on a draft; the reserved treatments are refused (below)
        return body

    def op_doc_create(self):
        kind = self.rng.choice(["offers", "invoices"])
        b = self.doc_body(kind)
        days = {date.today().isoformat()}  # the server stamps the date: accept the day before/after the call
        r = self.boss.http.post(f"/{kind}", json=b)
        days.add(date.today().isoformat())
        assert r.status_code in (200, 201), r.text
        d = r.json()
        assert d["status"] == "draft" and not d.get("number"), "a client must not choose status/number on create"
        assert d["date"] in days and d["template_id"] == "classic"
        for src, dst in zip(b["lines"], d["lines"], strict=True):
            exact = money(Decimal(str(src["qty"])) * Decimal(str(src["price"])))
            assert dst["amount"] == exact, (src, dst)
            assert money_eq(dst["price"], src["price"]), (src, dst)
        self.docs[kind][d["id"]] = {
            "lines": b["lines"],
            "status": "draft",
            "number": None,
            "customer": b["customer_id"],
        }
        self.say(f"{kind} draft {d['id']}")

    def op_doc_finalize(self):
        kind = self.rng.choice(["offers", "invoices"])
        drafts = [i for i, d in self.docs[kind].items() if d["status"] == "draft"]
        if not drafts:
            return
        rid = self.rng.choice(drafts)
        r = self.boss.http.post(f"/{kind}/{rid}/finalize")
        assert r.status_code == 200, r.text
        d = r.json()
        self.numbers[kind] += 1
        prefix = "AN" if kind == "offers" else "RE"
        assert d["number"] == f"{prefix}-{self.today.year}-{self.numbers[kind]:04d}", d["number"]
        assert d["status"] == "sent"
        assert d.get("due_date" if kind == "invoices" else "valid_until")
        again = self.boss.post(f"/{kind}/{rid}/finalize")
        assert again["number"] == d["number"], "finalize must be idempotent (gap-free numbering)"
        self.docs[kind][rid].update(status="sent", number=d["number"])
        pdf = self.boss.http.get(f"/{kind}/{rid}/pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and "pdf" in pdf.headers["content-type"]
        number = d["number"]
        if kind == "offers":
            # editing never changes the number; the client cannot rewrite it
            d["number"], d["title"] = "HACK-2", "geändert"
            r = self.boss.http.put(f"/{kind}/{rid}", json=d)
            assert r.status_code == 200 and r.json()["number"] == number
        else:
            self.check_frozen_invoice(rid, d)
        self.say(f"{kind} finalize {rid} -> {number}")

    def check_frozen_invoice(self, rid, d):
        """An issued invoice is frozen (credit-notes-payments.md 2.1): frozen fields and delete are 409, the allowed
        fields (due date, notes, reference, status label) still change, a whole-record PUT of unchanged values is fine."""
        number = d["number"]
        r = self.boss.http.put(f"/invoices/{rid}", json={**d, "number": "HACK-2", "title": "geändert"})
        assert r.status_code == 409, ("frozen title", r.status_code, r.text)
        r = self.boss.http.put(f"/invoices/{rid}", json={**d, "status": "cancelled"})
        assert r.status_code == 409, ("cancelled is derived", r.status_code, r.text)
        assert self.boss.http.delete(f"/invoices/{rid}").status_code == 409, "an issued invoice cannot be deleted"
        r = self.boss.http.put(f"/invoices/{rid}", json=d)
        assert r.status_code == 200 and r.json()["number"] == number, (r.status_code, r.text)
        r = self.boss.http.put(f"/invoices/{rid}", json={**d, "notes": f"Notiz {self.n}", "reference": f"PO-{self.n}"})
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["number"] == number and got["notes"] == f"Notiz {self.n}" and got["title"] == d["title"], got
        self.n += 1
        for k in ("paid_amount", "open_amount"):
            assert isinstance(got[k], str), (k, got[k])  # money as decimal strings

    def op_doc_pay(self):
        """Paid comes from recording payments (credit-notes-payments.md 3), never from a status select."""
        sent = [i for i, d in self.docs["invoices"].items() if d["status"] == "sent"]
        if not sent:
            return
        rid = self.rng.choice(sent)
        inv = self.boss.get(f"/invoices/{rid}")
        open_amt = Decimal(inv["open_amount"])
        assert inv["status"] == "sent" and open_amt > 0 and inv["paid_amount"] == "0.00", inv
        today = self.today.isoformat()
        if open_amt >= Decimal("0.02") and self.rng.random() < 0.3:  # a part first: the invoice stays open
            part = (open_amt / 2).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            r = self.boss.http.post(f"/invoices/{rid}/payments", json={"date": today, "amount": str(part)})
            assert r.status_code == 201, r.text
            mid = self.boss.get(f"/invoices/{rid}")
            assert mid["status"] == "sent" and mid["paid_amount"] == money(part), mid
            assert Decimal(mid["open_amount"]) == open_amt - part, mid
            open_amt -= part
        r = self.boss.http.post(
            f"/invoices/{rid}/payments", json={"date": today, "amount": money_in(self.rng, str(open_amt))}
        )
        assert r.status_code == 201, r.text
        done = self.boss.get(f"/invoices/{rid}")
        assert done["status"] == "paid" and done["open_amount"] == "0.00" and done["paid_date"] == today, done
        assert self.boss.http.delete(f"/invoices/{rid}").status_code == 409
        self.docs["invoices"][rid]["status"] = "paid"
        self.say(f"invoice {rid} paid by payment")
        self.check_finance()

    def op_doc_credit(self):
        """Credit notes on an unpaid issued invoice: storno, partial or write-off (credit-notes-payments.md 4)."""
        sent = [i for i, d in self.docs["invoices"].items() if d["status"] == "sent"]
        if not sent:
            return
        rid = self.rng.choice(sent)
        inv = self.boss.get(f"/invoices/{rid}")
        today = self.today.isoformat()
        url = f"/invoices/{rid}/credit-notes"
        # Finance note: /api/finance (calc.py) nets every credit note against revenue on its date, so a partial credit
        # (or write-off) of a still unpaid invoice shows negative revenue under the default cash (EÜR) basis. Reported
        # as an open question; here a partial credit is followed by the payment of the rest in the same step, and
        # write-offs are not exercised.
        kind = self.rng.choice(["storno", "partial"])
        if kind == "partial":
            rate, grp = next(iter(inv["issued"]["rates"].items()))
            half = (Decimal(grp["net"]) / 2).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            if half <= 0:
                return
            body = {"credit_kind": "partial", "date": today, "reason": "Teilgutschrift", "amounts": {rate: str(half)}}
            too_much = {**body, "amounts": {rate: money(Decimal(grp["net"]) * 2)}}
            assert self.boss.http.post(url, json=too_much).status_code == 422, "credit above the remaining net"
        else:
            body = {"credit_kind": kind, "date": today, "reason": "Kulanz"}
        assert self.boss.http.post(url, json={**body, "reason": ""}).status_code == 422, "reason is required"
        r = self.boss.http.post(url, json=body)
        assert r.status_code == 201, (kind, r.status_code, r.text)
        after = self.boss.get(f"/invoices/{rid}")
        assert isinstance(after["credited_amount"], str) and Decimal(after["credited_amount"]) > 0, after
        if kind == "storno":
            assert after["status"] == "cancelled" and after["open_amount"] == "0.00", after
            assert self.boss.http.post(url, json=body).status_code in (409, 422), "nothing left to credit"
            self.docs["invoices"][rid]["status"] = "cancelled"
        else:  # the invoice stays open with less to pay; the rest is paid right away (see the note on finance above)
            assert after["status"] == "sent", after
            r = self.boss.http.post(f"/invoices/{rid}/payments", json={"date": today, "amount": after["open_amount"]})
            assert r.status_code == 201, r.text
            done = self.boss.get(f"/invoices/{rid}")
            assert done["status"] == "paid" and done["open_amount"] == "0.00", done
            self.docs["invoices"][rid]["status"] = "paid"
            self.docs["invoices"][rid]["credited_net"] = half
        self.credit_notes += 1
        self.say(f"invoice {rid} credit {kind}")
        self.check_finance()

    def op_doc_negative(self):
        kind = self.rng.choice(["offers", "invoices"])
        empty = self.boss.post(f"/{kind}", {"customer_id": self.rng.choice(self.ids("customers")), "lines": []})
        assert self.boss.http.post(f"/{kind}/{empty['id']}/finalize").status_code == 400, "no lines"
        nocust = self.boss.post(f"/{kind}", {"lines": [{"description": "x", "qty": 1, "price": 1}]})
        assert self.boss.http.post(f"/{kind}/{nocust['id']}/finalize").status_code == 400, "no customer"
        for rid in (empty["id"], nocust["id"]):
            assert self.boss.http.delete(f"/{kind}/{rid}").status_code == 204
        for tt in ("export_exempt", "reverse_charge_domestic"):  # reserved treatments are refused
            body = {"customer_id": self.rng.choice(self.ids("customers")), "tax_treatment": tt, "lines": []}
            assert self.boss.http.post("/invoices", json=body).status_code == 422, tt
        self.say("finalize without lines / customer rejected")

    def check_finance(self):
        fin = self.boss.get("/finance", year=self.today.year)["totals"]
        rev = Decimal(0)
        groups = 0
        for rid, d in self.docs["invoices"].items():
            if d["status"] == "paid":
                rev += sum((dec(Decimal(str(ln["qty"])) * Decimal(str(ln["price"]))) for ln in d["lines"]), Decimal(0))
                rev -= d.get("credited_net", Decimal(0))
                groups += len(d["lines"])
        assert fin["revenue"] == money(rev), (fin["revenue"], rev, groups)
        cns = [x for x in self.boss.get("/invoices") if x.get("kind") == "credit_note"]
        assert len(cns) == self.credit_notes, (len(cns), self.credit_notes)
        net = sum(
            (
                Decimal(str(x["net"])) * Decimal(str(x.get("deductible", 100))) / 100
                for x in self.model["expenses"].values()
            ),
            Decimal(0),
        )
        assert abs(Decimal(fin["expenses"]) - net) <= Decimal("0.01") * max(len(self.model["expenses"]), 1), (
            fin["expenses"],
            net,
        )

    # ---------------------------------------------------------------- invoice from tracked time
    def op_from_time(self):
        r = self.rng
        if self.free_minutes(self.today.isoformat()) < 700:
            self.say("skip from-time scenario: not enough free time today")
            return
        cust = self.boss.post("/customers", {"name": f"Zeitkunde {self.n}", "rate": money_in(r, r.choice([None, 90]))})
        self.n += 1
        proj = self.boss.post(
            "/projects", {"name": f"ZP {self.n}", "customer_id": cust["id"], "rate": money_in(r, r.choice([None, 120]))}
        )
        task = self.boss.post(
            "/tasks", {"project_id": proj["id"], "name": "Spezial", "rate": money_in(r, r.choice([None, 175]))}
        )
        personal = self.boss.post("/projects", {"name": f"Intern {self.n}"})
        expect: dict[str, tuple[int, str]] = {}
        for _ in range(r.randint(2, 6)):
            use_task = r.random() < 0.4
            minutes = r.randint(5, 80)
            self.boss.post(
                "/entries",
                {
                    "user_id": self.boss_id,
                    "date": self.today.isoformat(),
                    "minutes": minutes,
                    "project_id": proj["id"],
                    "task_id": task["id"] if use_task else None,
                    "billable": True,
                },
            )
            rate = money((task.get("rate") if use_task else None) or proj.get("rate") or cust.get("rate") or 80)
            key = proj["name"] + (" – " + task["name"] if use_task else "") + f"@{rate}"
            m, _ = expect.get(key, (0, rate))
            expect[key] = (m + minutes, rate)
        self.boss.post(
            "/entries",
            {"user_id": self.boss_id, "date": self.today.isoformat(), "minutes": 99, "project_id": personal["id"]},
        )  # internal project (no customer): never billed
        self.boss.post(
            "/entries",
            {
                "user_id": self.boss_id,
                "date": self.today.isoformat(),
                "minutes": 77,
                "project_id": proj["id"],
                "billable": False,
            },
        )  # non-billable: never billed
        inv = self.boss.post("/invoices/from-time", {"customer_id": cust["id"]})
        got = {ln["description"] + f"@{money(ln['price'])}": (ln["qty"], ln["price"]) for ln in inv["lines"]}
        assert set(got) == set(expect), (got, expect)
        for key, (minutes, rate) in expect.items():
            assert abs(got[key][0] - round(minutes / 60, 2)) < 1e-9 and money_eq(got[key][1], rate), (key, got[key])
        billed = [e for e in self.boss.get("/entries") if e.get("invoice_id") == inv["id"]]
        assert sum(e["minutes"] for e in billed) == sum(m for m, _ in expect.values())
        again = self.boss.http.post("/invoices/from-time", json={"customer_id": cust["id"]})
        assert again.status_code == 400, "billing the same time twice must be impossible"
        assert self.boss.http.delete(f"/invoices/{inv['id']}").status_code == 204
        released = [e for e in self.boss.get("/entries") if e.get("invoice_id")]
        assert not released, "deleting an invoice must release its time entries"
        for e in self.boss.get("/entries"):
            if e.get("project_id") in (proj["id"], personal["id"]):
                r = self.boss.http.delete(f"/entries/{e['id']}")
                assert r.status_code == 204, (e["id"], r.status_code, r.text)
        for coll, rid in [
            ("tasks", task["id"]),
            ("projects", proj["id"]),
            ("projects", personal["id"]),
            ("customers", cust["id"]),
        ]:
            r = self.boss.http.delete(f"/{coll}/{rid}")
            assert r.status_code == 204, (coll, r.status_code, r.text)
        self.say(f"from-time invoice with {len(expect)} groups verified, released after delete")

    # ---------------------------------------------------------------- timer and dashboard
    def op_timer(self):
        if self.timer_running:
            before = len(self.boss.get("/entries"))
            r = self.boss.post("/timer/stop", {"note": "timer"})
            assert r["running"] is False and r["entry"]["minutes"] >= 1
            assert len(self.boss.get("/entries")) == before + 1
            self.model["entries"][r["entry"]["id"]] = {
                "minutes": r["entry"]["minutes"],
                "user_id": self.boss_id,
                "date": r["entry"]["date"],
                "billable": True,
            }
            self.timer_running = False
            self.say("timer stop -> 1 entry")
        else:
            assert self.boss.post("/timer/stop", {})["running"] is False  # stop without start is harmless
            assert self.boss.post("/timer/start", {"note": "x"})["running"] is True
            assert self.boss.get("/timer")["running"] is True
            self.timer_running = True
            self.say("timer start")

    def op_dashboard(self):
        ws = week_start(self.today)
        worked = sum(
            e["minutes"]
            for e in self.model["entries"].values()
            if e.get("user_id") == self.boss_id and ws <= date.fromisoformat(e["date"]) <= ws + timedelta(6)
        )
        absent = set()
        for a in self.model["absences"].values():
            d0, d1 = date.fromisoformat(a["start"]), date.fromisoformat(a["end"])
            absent |= {d0 + timedelta(i) for i in range((d1 - d0).days + 1)}
        dash = self.boss.get("/dashboard")
        target = week_target_minutes(self.today, 40, absent)
        assert abs(dash["worked"] * 60 - worked) <= 1, (dash["worked"] * 60, worked)
        assert abs(dash["target"] * 60 - target) <= 1, (dash["target"] * 60, target)
        assert abs(dash["remaining"] * 60 - max(target - worked, 0)) <= 1.5
        fc = self.boss.get("/plan", weeks=3, user_id=self.boss_id)["weeks"]
        # the plan counts only days after today, so compare the next (fully future) week
        nxt = week_target_minutes(self.today + timedelta(days=7), 40, absent)
        assert abs(fc[1]["capacity"] * 60 - nxt) <= 6, (fc[1], nxt)
        for w in fc:
            # per day assigned - capacity = over - unassigned, so the week sums agree up to rounding
            assigned = w["assigned_customer"] + w["assigned_internal"]
            assert abs((w["capacity"] - assigned) - (w["unassigned"] - w["over_assigned"])) < 0.06, w
            assert min(w["capacity"], w["unassigned"], w["over_assigned"]) >= 0, w
        self.say("dashboard + plan agree with the model")

    # ---------------------------------------------------------------- permissions
    def op_member(self):
        m, r = self.member.http, self.rng
        boss_entries = [e for e in self.boss.get("/entries") if e["user_id"] == self.boss_id]
        for path in (
            "/customers",
            "/invoices",
            "/offers",
            "/expenses",
            "/contracts",
            "/templates",
            "/settings",
            "/finance",
            "/plan",
            "/project-report",
            "/attachments/abcdef123456.pdf",
        ):
            assert m.get(path).status_code == 403, path
        assert m.post("/projects", json={"name": "nope"}).status_code == 403
        assert m.post("/customers", json={"name": "nope"}).status_code == 403
        assert (
            m.post("/users", json={"username": "evil", "name": "E", "role": "boss", "password": PASSWORD}).status_code
            == 403
        )
        users = m.get("/users").json()
        assert all(set(u) <= {"id", "name", "role"} for u in users), users  # nothing but names for members
        before = {x["id"] for x in m.get("/entries").json()}
        e = m.post("/entries", json={"user_id": self.boss_id, "date": self.today.isoformat(), "minutes": 15})
        assert e.status_code in (200, 201) and e.json()["user_id"] == self.member_id, "member can book for others"
        assert all(x["user_id"] == self.member_id for x in m.get("/entries").json()), "member sees others' time"
        if boss_entries:
            victim = r.choice(boss_entries)["id"]
            assert m.get(f"/entries/{victim}").status_code == 403
            assert m.put(f"/entries/{victim}", json={"date": self.today.isoformat(), "minutes": 1}).status_code == 403
            assert m.delete(f"/entries/{victim}").status_code == 403
        assert e.json()["id"] in {x["id"] for x in m.get("/entries").json()} and e.json()["id"] not in before
        assert m.delete(f"/entries/{e.json()['id']}").status_code == 204
        assert m.get("/dashboard", params={"user_id": self.boss_id}).status_code in (200, 403)
        self.say("member permission matrix verified")

    # ---------------------------------------------------------------- hostile input
    def op_fuzz(self):
        r = self.rng
        junk = [
            None,
            True,
            -1,
            0,
            1e308,
            2**70,
            "",
            "x" * 20000,
            [],
            {},
            [[]],
            {"a": {"b": [1, None]}},
            "💥",
            "\x00",
            "<script>alert(1)</script>",
            {"$ne": 1},
            "NaN",
            "9" * 400,
        ]
        for _ in range(12):
            coll = r.choice(COLLS + ["offers", "invoices", "users", "templates"])
            body = {
                k: r.choice(junk)
                for k in r.sample(
                    [
                        "name",
                        "minutes",
                        "date",
                        "start",
                        "end",
                        "lines",
                        "qty",
                        "net",
                        "customer_id",
                        "project_id",
                        "user_id",
                        "rate",
                        "status",
                        "id",
                        "username",
                        "password",
                        "role",
                        "plan",
                    ],
                    r.randint(0, 6),
                )
            }
            for method, path in (
                ("post", f"/{coll}"),
                ("put", f"/{coll}/{r.choice(self.ids('customers') or ['abcdef123456'])}"),
            ):
                try:
                    resp = getattr(self.boss.http, method)(path, json=body)
                except httpx.HTTPError as exc:  # pragma: no cover - the server must not drop the connection
                    pytest.fail(f"connection error on {method} {path} {str(body)[:200]}: {exc}")
                assert resp.status_code < 500, (method, path, str(body)[:300], resp.status_code, resp.text[:300])
                if resp.status_code >= 400:
                    assert "detail" in resp.json()
            junk_path = r.choice(
                [
                    "/../etc/passwd",
                    "/customers/%00",
                    "/customers/" + "a" * 500,
                    "/entries/%2e%2e",
                    "/attachments/..%2f..%2fsettings.json",
                    "/offers/x/pdf",
                    "/holidays?year=abc",
                    "/plan?weeks=-5",
                    "/plan?weeks=100000",
                    "/finance?year=0",
                    "/finance?year=99999999",
                    "/dashboard?user_id=nope",
                ]
            )
            resp = self.boss.http.get(junk_path)
            assert resp.status_code < 500, (junk_path, resp.status_code, resp.text[:200])
        removed = self.sanitize()
        self.say(f"fuzzed 12 payloads + path tricks without a 5xx (removed {removed} poisoned records)")
        for coll in COLLS:  # fuzzing must not have damaged the stored data or the model
            self.fix_model_after_fuzz(coll)

    DATE_FIELDS = ("date", "start", "end", "paid_date", "due_date", "valid_until", "service_from", "service_to")
    NUM_FIELDS = ("minutes", "rate", "net", "vat", "hours_per_week", "total_hours", "budget_hours", "deductible")

    def sanitize(self) -> int:
        """Safety net: the backend now rejects invalid dates/numbers, so there should be nothing to delete. Kept so a
        regression shows up as a count instead of as a poisoned session."""
        removed = 0
        for coll in COLLS + ["offers", "invoices"]:
            for rec in self.boss.get(f"/{coll}"):
                bad = False
                for k in self.DATE_FIELDS:
                    v = rec.get(k)
                    if v is not None:
                        try:
                            date.fromisoformat(v)
                        except (TypeError, ValueError):
                            bad = True
                for k in self.NUM_FIELDS:
                    v = rec.get(k)
                    if k in MONEY_KEYS and isinstance(v, str):
                        v = _as_float(v)  # money comes back as a decimal string
                    if isinstance(v, (int, float)) and abs(v) > 1e7:
                        bad = True
                lines = rec.get("lines")
                if isinstance(lines, list):  # extra="allow": a fuzzed record may carry any shape here
                    for ln in lines:
                        if not isinstance(ln, dict):
                            continue
                        for k in ("qty", "price"):
                            v = ln.get(k)
                            if k == "price" and isinstance(v, str):
                                v = _as_float(v)
                            if isinstance(v, (int, float)) and abs(v) > 1e7:
                                bad = True
                if bad:
                    self.boss.http.delete(f"/{coll}/{rec['id']}")
                    removed += 1
        return removed

    def fix_model_after_fuzz(self, coll):
        """Fuzz bodies may legitimately create or overwrite records (e.g. a customer named '💥'): re-sync the model
        from the server. What the fuzz asserts is only: no 5xx, JSON errors, and the data stays listable."""
        self.model[coll] = {
            r["id"]: {k: v for k, v in r.items() if k not in ("id", "created", "updated")}
            for r in self.boss.get(f"/{coll}")
        }

    def op_auth_fuzz(self):
        r = self.rng
        tokens = [
            "",
            "garbage",
            "a.b.c",
            make_token(self.boss_id, secret="wrong"),
            make_token(self.boss_id, exp_in=-10),
            make_token(self.boss_id, alg="none"),
            make_token("doesnotexist1"),
            make_token(self.boss_id) + "x",
            "".join(r.choices(string.ascii_letters + ".-_", k=r.randint(1, 300))),
        ]
        for t in tokens:
            h = {"Authorization": f"Bearer {t}"} if t else {}
            resp = httpx.get(self.base + "/api/customers", headers=h)
            assert resp.status_code == 401, (t[:40], resp.status_code)
            resp = httpx.get(self.base + "/api/customers", cookies={"authorization": t})
            assert resp.status_code == 401, ("cookie", t[:40], resp.status_code)
        good = make_token(self.boss_id)
        assert httpx.get(self.base + "/api/customers", headers={"Authorization": f"Bearer {good}"}).status_code == 200
        assert httpx.get(self.base + "/api/customers", cookies={"authorization": good}).status_code == 200
        self.say("forged/expired/alg-none/foreign tokens rejected, genuine accepted")

    def op_concurrent_writes(self):
        n = 24

        def post(i):
            c = httpx.Client(base_url=self.base + "/api", headers=self.boss.http.headers, timeout=20)
            r = c.post(
                "/entries",
                json={"user_id": self.boss_id, "date": self.today.isoformat(), "minutes": i + 1, "note": f"par{i}"},
            )
            c.close()
            return r.status_code, r.json().get("id")

        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(post, range(n)))
        assert all(s in (200, 201) for s, _ in res), res
        ids = [i for _, i in res]
        assert len(set(ids)) == n, "ids collided"
        for coll in ("entries",):
            self.fix_model_after_fuzz(coll)
        assert sum(1 for e in self.boss.get("/entries") if (e.get("note") or "").startswith("par")) >= n
        self.say(f"{n} parallel writes stored")

    OPS = [
        ("create", 14),
        ("update", 8),
        ("delete", 5),
        ("doc_create", 5),
        ("doc_finalize", 5),
        ("doc_pay", 4),
        ("doc_credit", 2),
        ("doc_negative", 1),
        ("from_time", 2),
        ("timer", 2),
        ("dashboard", 2),
        ("member", 2),
        ("fuzz", 2),
        ("auth_fuzz", 1),
        ("concurrent_writes", 1),
    ]

    def run(self, steps):
        for _ in range(steps):
            name = pick_weighted(self.rng, self.OPS)
            if name in ("doc_create", "doc_negative", "from_time") and not self.model["customers"]:
                name = "create"
            if name in ("update", "delete") and not any(self.model.values()):
                name = "create"
            if name in ("create",) and not any(self.can(c) for c in COLLS):
                continue
            getattr(self, "op_" + name)()
        self.check_finance()


@pytest.mark.parametrize("seed", seeds_from_env())
def test_random_api_session(seed, live_server, api):
    api.setup_boss()
    api.post("/users", {"username": "mia", "name": "Mia Member", "role": "member", "password": PASSWORD})
    member = Api(live_server.url)
    assert member.login("mia").status_code == 200
    rng = random.Random(seed)
    sim = ApiSim(api, member, live_server.url, rng)
    try:
        sim.run(env_int("PERMITA38_E2E_API_STEPS", 80))
    except Exception as exc:
        raise AssertionError(
            f"api seed {seed} failed after {len(sim.log)} ops: {exc}\n{report_failure('api', seed, sim.log)}"
        ) from exc
    member.close()
    print(f"api seed {seed}: {len(sim.log)} ops verified")
