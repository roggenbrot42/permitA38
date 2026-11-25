"""Stateful model of issued invoices, payments, credit notes and period locks (accounting.md section 20, E20).

A hypothesis RuleBasedStateMachine drives the HTTP API of an app whose accounting is initialised for the current
fiscal year (synthetic chart, same mechanism as ``tests/backend/conftest.py``) and compares every answer with the
integer-cent reference model in ``accounting_state.py``. After every step the invariants of the journal and of every
invoice are checked: balanced entries, gapless numbers, intact hash chain, derived amounts and status, receivables per
invoice, no booking date in a locked month, locks in order, and refused operations leave the journal untouched.

Replay a failure with the printed ``@reproduce_failure`` blob or ``--hypothesis-seed`` (see README.md).
"""

import calendar
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule

from permita38.api.server import get_app
from permita38.core import charts
from permita38.core.config import Config

from .accounting_state import LABELS, Inv, fmt

pytestmark = pytest.mark.randomized

TODAY = date.today()
YEAR = TODAY.year
JAN1 = date(YEAR, 1, 1)
SPAN = (TODAY - JAN1).days
CHART = Path(__file__).parents[1] / "fixtures" / "chart_synthetic.json"
PASSWORD = "stateful-password-1"
NET = st.one_of(st.integers(1, 300), st.integers(1, 250_000))
RATES = (19, 7, 0)


def last_day(month: int) -> date:
    return date(YEAR, month, calendar.monthrange(YEAR, month)[1])


class AccountingMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.tmp = Path(tempfile.mkdtemp(prefix="p38-acc-stateful-"))
        chart_dir = self.tmp / "charts"
        chart_dir.mkdir()
        shutil.copy(CHART, chart_dir / f"skr03-{YEAR}.json")
        self._real_dir = charts._DIR
        charts._DIR = chart_dir
        cfg = Config(data_dir=self.tmp / "data", secret_key="stateful-secret-key", web_dir=None)
        self.app = get_app(cfg)
        self.http = TestClient(self.app)
        self.http.__enter__()
        self.store = self.app.state.services.store
        self.journal = self.app.state.services.journal
        r = self.http.post("/api/auth/setup", json={"username": "boss", "name": "Bea Boss", "password": PASSWORD})
        assert r.status_code == 200, r.text
        r = self.http.post("/api/auth/login", json={"username": "boss", "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.http.headers["Authorization"] = "Bearer " + r.json()["token"]
        self.customers = [
            self.req("POST", "/customers", json={"name": f"Demo Client {i}"}).json()["id"] for i in (1, 2)
        ]
        self.basis = "accrual"
        self.inv: dict[str, Inv] = {}
        self.credit_docs: list[str] = []
        self.locked = 0  # months 1..locked of YEAR are locked
        self.seen: set[str] = set()  # journal entries already checked against the locks

    def teardown(self):
        charts._DIR = self._real_dir
        self.http.__exit__(None, None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ plumbing
    @initialize(basis=st.sampled_from(["accrual", "accrual", "eur"]), vat=st.sampled_from(["soll", "ist"]))
    def open_accounting(self, basis, vat):
        self.basis = basis
        r = self.req(
            "POST", "/accounting/years", json={"year": YEAR, "chart": "skr03", "income_basis": basis, "vat_basis": vat}
        )
        assert r.status_code == 201, r.text
        self.store.save_settings({"accounting.start": JAN1.isoformat()})

    def req(self, method, path, **kw):
        r = self.http.request(method, "/api" + path, **kw)
        assert r.status_code < 500, f"{method} {path} -> {r.status_code}: {r.text[:300]}"
        return r

    def entries(self) -> list[dict]:
        return list(self.store.list_journal_entries(year=YEAR, limit=5000))

    @property
    def first_open(self) -> date:
        return date(YEAR, self.locked + 1, 1) if self.locked else date.min

    def day(self, data, lo: date = JAN1) -> date:
        lo_off = max((lo - JAN1).days, 0)
        return JAN1 + timedelta(days=data.draw(st.integers(lo_off, SPAN)))

    def pick(self, data, issued: bool = True) -> Inv:
        """An invoice; with ``issued=False`` a draft is sometimes chosen (the refusals of spec 3 and 4)."""
        pool = [m for m in self.inv.values() if not m.draft]
        if not issued and (not pool or data.draw(st.integers(0, 9)) == 0):
            pool = list(self.inv.values())
        return data.draw(st.sampled_from(pool))

    def refused(self, r, code: int, before: int, what: str = ""):
        assert r.status_code == code, f"{what}: expected {code}, got {r.status_code} {r.text[:200]}"
        assert len(self.entries()) == before, f"{what}: a refused operation changed the journal"

    def check_inv(self, m: Inv, j: dict):
        assert j["status"] == m.status, (m.id, j["status"], m.status)
        assert j["paid_amount"] == fmt(m.P), m.id
        assert j["credited_amount"] == fmt(m.K), m.id
        assert j["open_amount"] == fmt(m.open), m.id
        assert j["paid_date"] == m.paid_date, m.id
        got = sorted((p["id"], p["date"], p["amount"], p["kind"]) for p in j["payments"])
        want = sorted((p.id, p.day.isoformat(), fmt(p.cents), p.kind) for p in m.rows)
        assert got == want, (m.id, got, want)
        got_c = sorted((c["credit_kind"], c["gross"]) for c in j["credit_notes"])
        want_c = sorted((k, fmt(g)) for _i, k, g in m.credits)
        assert got_c == want_c, (m.id, got_c, want_c)
        for key, value in (("notes", m.notes), ("reference", m.reference), ("due_date", m.due_date)):
            if value is not None:
                assert j.get(key) == value, (m.id, key)

    def get(self, m: Inv) -> dict:
        r = self.req("GET", f"/invoices/{m.id}")
        assert r.status_code == 200, r.text
        return r.json()

    # ------------------------------------------------------------------ invoices
    @precondition(lambda self: len(self.inv) < 7)
    @rule(data=st.data(), issue_now=st.sampled_from([True, True, True, False]))
    def create_invoice(self, data, issue_now):
        rates = data.draw(st.lists(st.sampled_from(RATES), min_size=1, max_size=3, unique=True))
        nets = {r: data.draw(NET) for r in rates}
        day = self.day(data)
        cust = data.draw(st.sampled_from(self.customers))
        lines = [dict(description="Work", qty=1, price=fmt(n), vat=r) for r, n in nets.items()]
        r = self.req("POST", "/invoices", json={"customer_id": cust, "date": day.isoformat(), "lines": lines})
        assert r.status_code == 200, r.text
        self.inv[r.json()["id"]] = Inv(r.json()["id"], day, nets, cust)
        if issue_now:
            self.issue(self.inv[r.json()["id"]])

    @precondition(lambda self: any(m.draft for m in self.inv.values()))
    @rule(data=st.data())
    def finalize(self, data):
        self.issue(data.draw(st.sampled_from([m for m in self.inv.values() if m.draft])))

    def issue(self, m: Inv):
        before = len(self.entries())
        r = self.req("POST", f"/invoices/{m.id}/finalize")
        if m.day < self.first_open:
            self.refused(r, 409, before, "finalize in a locked month")
            assert self.get(m)["status"] == "draft"
            return
        assert r.status_code == 200, r.text
        m.issue()
        j = r.json()
        assert j["status"] == "sent"
        m.due_date = j["due_date"]
        issued = j["issued"]
        assert issued["gross"] == fmt(m.G), (issued["gross"], m.G)
        for rate, (net, vat) in m.groups.items():
            g = issued["rates"][str(rate)]
            assert (g["net"], g["vat"], g["gross"]) == (fmt(net), fmt(vat), fmt(net + vat)), (rate, g)

    @precondition(lambda self: any(not m.draft for m in self.inv.values()))
    @rule(
        data=st.data(),
        notes=st.one_of(st.none(), st.sampled_from(["n1", "n2 x", "remark"])),
        reference=st.one_of(st.none(), st.sampled_from(["PO-1", "PO-2"])),
        due=st.one_of(st.none(), st.integers(0, SPAN)),
        label=st.one_of(st.none(), st.sampled_from(LABELS)),
    )
    def put_allowed_fields(self, data, notes, reference, due, label):
        """PUT of the whole record with a change in the fields spec 2.1 allows (notes, reference, due_date, label)."""
        m = self.pick(data)
        got = self.get(m)
        body = dict(got)
        if notes is not None:
            body["notes"] = notes
        if reference is not None:
            body["reference"] = reference
        if due is not None:
            body["due_date"] = (JAN1 + timedelta(days=due)).isoformat()
        if label is not None:
            body["status"] = label
        before = len(self.entries())
        r = self.req("PUT", f"/invoices/{m.id}", json=body)
        if label is not None and label != m.status and m.status in ("paid", "cancelled"):
            self.refused(r, 409, before, "label change on a paid or cancelled invoice")
            assert self.get(m) == got, "a refused PUT changed the invoice"
            return
        assert r.status_code == 200, r.text
        if notes is not None:
            m.notes = notes
        if reference is not None:
            m.reference = reference
        if due is not None:
            m.due_date = body["due_date"]
        if label is not None:
            m.label = label
        assert len(self.entries()) == before, "an allowed PUT booked something"

    @precondition(lambda self: any(not m.draft for m in self.inv.values()))
    @rule(
        data=st.data(),
        field=st.sampled_from(["title", "intro", "outro", "lines", "date", "customer_id", "service_from"]),
    )
    def put_frozen_field(self, data, field):
        m = self.pick(data)
        got = self.get(m)
        body = dict(got)
        if field in ("title", "intro", "outro"):
            body[field] = str(got.get(field) or "") + "x"
        elif field == "lines":
            lines = [dict(ln) for ln in got["lines"]]
            lines[0]["price"] = "0.01" if lines[0]["price"] != "0.01" else "0.02"
            body["lines"] = lines
        elif field == "date":
            body["date"] = (m.day + timedelta(days=1)).isoformat()
        elif field == "customer_id":
            body["customer_id"] = next(c for c in self.customers if c != m.customer)
        else:
            body["service_from"] = "2000-01-01"
        before = len(self.entries())
        r = self.req("PUT", f"/invoices/{m.id}", json=body)
        self.refused(r, 409, before, f"frozen field {field}")
        assert self.get(m) == got, "a refused PUT changed the invoice"

    @precondition(lambda self: any(not m.draft for m in self.inv.values()))
    @rule(data=st.data(), credit_doc=st.booleans())
    def delete_issued(self, data, credit_doc):
        rid = data.draw(st.sampled_from(self.credit_docs)) if credit_doc and self.credit_docs else self.pick(data).id
        before = len(self.entries())
        r = self.req("DELETE", f"/invoices/{rid}")
        self.refused(r, 409, before, "delete of an issued document")

    @precondition(lambda self: any(not m.draft for m in self.inv.values()))
    @rule(data=st.data(), pay_day=st.integers(0, SPAN))
    def put_status_paid(self, data, pay_day):
        """The legacy `paid` translation (spec 2.2): a payment of the open amount dated paid_date."""
        m = self.pick(data)
        got = self.get(m)
        day = JAN1 + timedelta(days=pay_day)
        before = len(self.entries())
        r = self.req("PUT", f"/invoices/{m.id}", json={**got, "status": "paid", "paid_date": day.isoformat()})
        if m.status == "paid":
            if day.isoformat() != m.paid_date:
                self.refused(r, 409, before, "paid_date of a paid invoice")
                return
            assert r.status_code == 200, r.text
            return
        assert r.status_code in (200, 422), r.text
        if m.open <= 0:
            assert r.status_code == 200, r.text
            assert len(self.entries()) == before
            return
        if day < m.day:
            self.refused(r, 422, before, "paid before the invoice date")
            return
        assert r.status_code == 200, r.text
        pays = {p["id"] for p in self.get(m)["payments"]}
        known = {p.id for p in m.rows}
        (new_id,) = pays - known
        m.pay(new_id, day, m.open)

    # ------------------------------------------------------------------ payments
    @precondition(lambda self: self.inv)
    @rule(
        data=st.data(),
        kind=st.sampled_from(["payment", "payment", "payment", "refund"]),
        when=st.sampled_from(["ok", "ok", "ok", "before", "future"]),
        mode=st.sampled_from(["open", "part", "over", "any"]),
    )
    def payment(self, data, kind, when, mode):
        m = self.pick(data, issued=False)
        base = max(m.open, 0) if kind == "payment" else m.P
        cents = {
            "open": base or 1,
            "part": data.draw(st.integers(1, max(base, 1))),
            "over": base + data.draw(st.integers(1, 5000)),
            "any": data.draw(st.integers(1, 400_000)),
        }[mode]
        day = {
            "ok": lambda: self.day(data, m.day),
            "before": lambda: m.day - timedelta(days=1),
            "future": lambda: TODAY + timedelta(days=data.draw(st.integers(1, 30))),
        }[when]()
        before = len(self.entries())
        r = self.req(
            "POST", f"/invoices/{m.id}/payments", json={"date": day.isoformat(), "amount": fmt(cents), "kind": kind}
        )
        if m.draft:
            self.refused(r, 409, before, "payment on a draft")
        elif day < m.day or day > TODAY:
            self.refused(r, 422, before, f"payment dated {day}")
        elif kind == "refund" and cents > m.P:
            self.refused(r, 422, before, "refund above the cash kept")
        else:
            assert r.status_code == 201, r.text
            j = r.json()
            m.pay(j["payment"]["id"], day, cents, kind)
            codes = {w["code"] for w in j["warnings"]}
            assert ("overpayment" in codes) == (kind == "payment" and m.open < 0), (codes, m.open)
            self.check_inv(m, j["invoice"])

    @precondition(lambda self: any(m.rows for m in self.inv.values()))
    @rule(data=st.data(), when=st.sampled_from(["default", "default", "ok", "before", "future"]))
    def reverse_payment(self, data, when):
        m = data.draw(st.sampled_from([m for m in self.inv.values() if m.rows]))
        row = data.draw(st.sampled_from(m.rows))
        day = {
            "default": lambda: None,
            "ok": lambda: self.day(data, m.day),
            "before": lambda: m.day - timedelta(days=1),
            "future": lambda: TODAY + timedelta(days=1),
        }[when]()
        body = {} if day is None else {"date": day.isoformat()}
        before = len(self.entries())
        r = self.req("POST", f"/payments/{row.id}/reverse", json=body)
        if row.kind == "reversal":
            self.refused(r, 409, before, "reverse a reversal")
        elif any(x.reverses == row.id for x in m.rows):
            self.refused(r, 409, before, "reverse twice")
        elif day is not None and (day < m.day or day > TODAY):
            self.refused(r, 422, before, f"reversal dated {day}")
        elif m.P - row.cents < 0:
            self.refused(r, 422, before, "reversal would make the cash kept negative")
        else:
            assert r.status_code == 201, r.text
            j = r.json()
            m.reverse(j["payment"]["id"], day or row.day, row)
            assert j["payment"]["kind"] == "reversal" and j["payment"]["reverses_id"] == row.id
            self.check_inv(m, j["invoice"])

    # ------------------------------------------------------------------ credit notes
    @precondition(lambda self: self.inv)
    @rule(
        data=st.data(),
        kind=st.sampled_from(["storno", "partial", "partial", "partial", "partial", "write_off"]),
        when=st.sampled_from(["ok", "ok", "ok", "before", "future", "locked"]),
    )
    def credit_note(self, data, kind, when):
        m = self.pick(data, issued=False)
        amounts: dict[int, int] = {}
        if kind == "partial" and m.draft:
            amounts = {19: 100}
        elif kind == "partial":
            if not m.exact:
                return  # group amounts are no longer known to the model (write-off, discount, reversal)
            for rate in data.draw(st.lists(st.sampled_from(sorted(m.rem)), min_size=1, max_size=3, unique=True)):
                amounts[rate] = data.draw(st.integers(1, max(m.rem[rate][0], 1) + 2))
        lo = max(m.day, self.first_open)
        if when == "locked" and m.day < self.first_open:
            day = JAN1 + timedelta(
                days=data.draw(st.integers(max((m.day - JAN1).days, 0), (self.first_open - JAN1).days - 1))
            )
        elif when == "before":
            day = m.day - timedelta(days=1)
        elif when == "future":
            day = TODAY + timedelta(days=1)
        else:
            day = self.day(data, lo)
        body = {"credit_kind": kind, "date": day.isoformat(), "reason": "Reason"}
        if kind == "partial":
            body["amounts"] = {str(rate): fmt(net) for rate, net in amounts.items()}
        before = len(self.entries())
        r = self.req("POST", f"/invoices/{m.id}/credit-notes", json=body)
        if m.draft:
            self.refused(r, 409, before, "credit note on a draft")
        elif day < m.day or day > TODAY:
            self.refused(r, 422, before, f"credit note dated {day}")
        elif day < self.first_open:
            self.refused(r, 409, before, "credit note in a locked month")
        elif kind == "storno" and m.C == 0:
            self.refused(r, 422, before, "storno with nothing left")
        elif kind == "write_off" and m.open <= 0:
            self.refused(r, 422, before, "write-off with nothing open")
        elif kind == "partial" and any(net > m.rem[rate][0] for rate, net in amounts.items()):
            self.refused(r, 422, before, "partial credit above the remaining net")
        else:
            assert r.status_code == 201, r.text
            j = r.json()
            if kind == "storno":
                gross = m.storno_gross()
                m.apply_storno()
            elif kind == "partial":
                gross = m.apply_partial(amounts)
            else:
                gross = m.open
            m.credit(j["id"], kind, gross)
            assert j["issued"]["gross"] == fmt(gross), (kind, j["issued"]["gross"], gross)
            if kind != "write_off":
                assert j.get("number")
            self.credit_docs.append(j["id"])
            self.check_inv(m, self.get(m))

    @precondition(lambda self: self.inv)
    @rule(
        data=st.data(),
        when=st.sampled_from(["ok", "ok", "ok", "before", "future"]),
        mode=st.sampled_from(["part", "any", "near"]),
    )
    def settle_discount(self, data, when, mode):
        m = self.pick(data, issued=False)
        top = max(m.open, 1)
        cents = {
            "part": data.draw(st.integers(1, top)),
            "any": data.draw(st.integers(1, top + 500)),
            "near": max(top - data.draw(st.integers(0, 3)), 1),
        }[mode]
        day = {
            "ok": lambda: self.day(data, m.day),
            "before": lambda: m.day - timedelta(days=1),
            "future": lambda: TODAY + timedelta(days=1),
        }[when]()
        before = len(self.entries())
        r = self.req(
            "POST", f"/invoices/{m.id}/settle-discount", json={"date": day.isoformat(), "amount_paid": fmt(cents)}
        )
        if m.draft:
            self.refused(r, 409, before, "settle a draft")
        elif day < m.day or day > TODAY:
            self.refused(r, 422, before, f"settle dated {day}")
        elif m.open <= 0 or cents >= m.open:
            self.refused(r, 422, before, "amount paid covers the open amount")
        else:
            assert r.status_code == 201, r.text
            j = r.json()
            discount = m.open - cents
            assert j["credit_note"]["issued"]["gross"] == fmt(discount), (j["credit_note"]["issued"]["gross"], discount)
            m.credit(j["credit_note"]["id"], "discount", discount)
            m.pay(j["payment"]["id"], day, cents)
            self.credit_docs.append(j["credit_note"]["id"])
            assert m.open == 0
            self.check_inv(m, j["invoice"])

    # ------------------------------------------------------------------ locks
    @rule(month=st.integers(1, 12))
    def lock_month(self, month):
        before = len(self.entries())
        r = self.req("POST", "/accounting/locks", json={"year": YEAR, "month": month})
        if last_day(month) >= TODAY:
            self.refused(r, 422, before, f"lock of month {month}, which has not ended")
            return
        assert r.status_code == 201, r.text
        new = list(range(self.locked + 1, month + 1))
        assert [row["month"] for row in r.json()] == new, (r.json(), new)
        self.locked = max(self.locked, month)
        assert len(self.entries()) == before, "locking booked something"

    # ------------------------------------------------------------------ invariants
    @invariant()
    def journal_is_sound(self):
        entries = self.entries()
        assert [e["entry_no"] for e in entries] == list(range(1, len(entries) + 1)), "entry numbers are not gapless"
        for e in entries:
            assert sum(int(ln["amount_cents"]) for ln in e["lines"]) == 0, f"entry {e['entry_no']} does not balance"
            if e["id"] not in self.seen:
                booked = date.fromisoformat(str(e["booking_date"])[:10])
                assert booked >= self.first_open, f"entry {e['entry_no']} booked into locked month on {booked}"
                self.seen.add(e["id"])
        assert self.journal.verify_chain(YEAR) == []

    @invariant()
    def invoices_match_model(self):
        entries = self.entries()
        for m in self.inv.values():
            if m.draft:
                continue
            self.check_inv(m, self.get(m))
            if self.basis == "accrual":
                bal = sum(
                    int(ln["amount_cents"])
                    for e in entries
                    for ln in e["lines"]
                    if ln.get("role") == "receivables" and ln.get("open_item_id") == m.id
                )
                assert bal == m.receivables, f"receivables of {m.id}: journal {bal}, model {m.receivables}"

    @invariant()
    def locks_are_in_order(self):
        r = self.req("GET", "/accounting/locks")
        assert [(row["year"], row["month"]) for row in r.json()] == [(YEAR, mo) for mo in range(1, self.locked + 1)]


SHARDS = 2


def _shard(n: int):
    machine = type(f"AccountingMachineShard{n}", (AccountingMachine,), {})
    base = settings.default
    machine.TestCase.settings = settings(base, max_examples=-(-base.max_examples // SHARDS))
    return type(f"TestAccountingStatefulShard{n}", (machine.TestCase,), {})


TestAccountingStatefulShard1, TestAccountingStatefulShard2 = (_shard(i) for i in range(1, SHARDS + 1))
