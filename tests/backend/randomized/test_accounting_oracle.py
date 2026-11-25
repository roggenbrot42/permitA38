"""Independent oracle for the accounting engine (docs/specs/accounting.md 9.1 - 9.3, test plan section 20).

``accounting_model.py`` implements the target rule with ``fractions.Fraction`` and its own rounding and allocation,
written from the spec alone. Hypothesis generates invoices and event sequences; the engine's public API and the model
run side by side and must agree after every event. The model itself is checked against the hand-computed fixture.
"""

import json
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from permita38.core.accounting import postings_issue as pi
from permita38.core.accounting import postings_targets as pt
from permita38.core.accounting import postings_writeoff as pc
from permita38.core.accounting.types import (
    Bases,
    Cents,
    CreditEvent,
    Entry,
    InvoiceState,
    IssuedSnapshot,
    PaymentEvent,
    RateGroup,
)
from tests.backend.randomized import accounting_model as am

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "accounting_oracle.json").read_text("utf-8"))
JOURNAL_CASES = {c["id"]: c for c in FIXTURE["journal_cases"]}
SETTINGS = settings(
    max_examples=300,
    deadline=None,
    derandomize=not os.environ.get("PERMITA38_ORACLE_RANDOM"),
    print_blob=True,
    suppress_health_check=list(HealthCheck),
)


# ---- engine adapter --------------------------------------------------------------------------------------------
def _key(k) -> str:
    return str(getattr(k, "value", k))


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _rg(rates: dict[int, tuple[int, int]], sign: int = 1) -> tuple[RateGroup, ...]:
    return tuple(
        RateGroup(Decimal(r), Cents(sign * n), Cents(sign * v)) for r, (n, v) in sorted(rates.items(), reverse=True)
    )


def _nonzero(groups) -> dict[int, tuple[int, int]]:
    return {int(g.rate): (int(g.net), int(g.vat)) for g in groups if g.net or g.vat}


class Engine:
    """Drives the public engine functions and accumulates the lines they post."""

    def __init__(self, bases: Bases, snapshot: IssuedSnapshot, invoice_date: date):
        self.bases = bases
        self.state = InvoiceState.issued(snapshot)
        self.roles: dict[str, int] = {}
        self.vat: dict[tuple[str, str], list[int]] = {}
        self.entries: list[Entry] = []
        self._book(pi.issue_entry(snapshot, bases, invoice_date))

    def _book(self, entry: Entry | None) -> int:
        if entry is None:
            return 0
        assert entry.balance == 0, f"entry does not balance: {entry}"
        for ln in entry.lines:
            self.roles[str(ln.role)] = self.roles.get(str(ln.role), 0) + int(ln.amount_cents)
            if ln.tax_key is None or ln.tax_kind is None or _key(ln.tax_key) == "NONE" or ln.tax_kind == "input":
                continue
            cur = self.vat.setdefault((_key(ln.tax_key), _month(ln.vat_date)), [0, 0])
            cur[0] += int(ln.tax_base_cents or 0)
            if ln.tax_kind == "tax":
                cur[1] += -int(ln.amount_cents)
        self.entries.append(entry)
        return 1

    def apply(self, event) -> None:
        after = pt.apply_event(self.state, event)
        self._book(pt.event_entry(self.state, after, self.bases, event))
        self.state = after

    def nonzero_roles(self) -> dict[str, int]:
        return {k: v for k, v in self.roles.items() if v}

    def nonzero_vat(self) -> dict[tuple[str, str], list[int]]:
        return {k: v for k, v in self.vat.items() if v != [0, 0]}


# ---- scenario ---------------------------------------------------------------------------------------------------
OPS = [
    "pay_part", "pay_open", "pay_over", "pay_recover", "refund", "refund_all",
    "partial", "storno", "discount", "write_off", "write_off", "pay_recover", "partial", "pay_open",
]  # fmt: skip

invoice_st = st.fixed_dictionaries(
    {
        "income": st.sampled_from(["eur", "accrual"]),
        "vat": st.sampled_from(["soll", "ist"]),
        "treatment": st.sampled_from(["standard", "standard", "eu_b2b_service"]),
        "rates": st.lists(st.sampled_from([19, 7, 16, 0]), min_size=1, max_size=3, unique=True),
        "nets": st.lists(st.one_of(st.integers(1, 5_000_000), st.integers(1, 2000)), min_size=3, max_size=3),
        "day": st.integers(0, 60),
        "perf_back": st.integers(0, 20),
    }
)
step_st = st.tuples(st.sampled_from(OPS), st.integers(0, 1000), st.integers(0, 1000), st.integers(0, 25))


def _build(inv: dict):
    treatment = inv["treatment"]
    rates = [0] if treatment == "eu_b2b_service" else inv["rates"]
    groups = {}
    for r, net in zip(rates, inv["nets"], strict=False):
        groups[r] = (net, am.q2(am.Fraction(net * r, 100)))
    invoice_date = date(2026, 1, 1) + timedelta(days=inv["day"])
    perf = invoice_date - timedelta(days=inv["perf_back"])
    model = am.InvoiceModel(inv["income"], inv["vat"], treatment, groups, invoice_date, perf)
    snapshot = IssuedSnapshot(
        document_id="inv",
        regime="regular",
        treatment=treatment,
        rates=_rg(groups),
        perf=perf,
    )
    return model, snapshot, Bases(inv["income"], inv["vat"]), invoice_date


def _concrete(model: am.InvoiceModel, op: str, a: int, b: int):
    """Resolve a random op against the model state: a list of ('payment', amount) / ('credit', kind, rates)."""
    c, p = model.current, model.kept
    open_ = max(c - p, 0)
    if op == "pay_part":
        return [("payment", 1 + (max(open_, model.gross // 10 + 1) * a) // 1000)]
    if op == "pay_open":
        return [("payment", open_)] if open_ > 0 else []
    if op == "pay_over":
        return [("payment", open_ + 1 + b % 500)]
    if op == "pay_recover":
        wo = model.written_off_gross
        return [("payment", max(1, wo * max(a, 1) // 1000))] if wo > 0 else []
    if op == "refund":
        return [("payment", -(1 + (p - 1) * a // 1000))] if p > 0 else []
    if op == "refund_all":
        return [("payment", -p)] if p > 0 else []
    if op == "partial":
        rates = model.partial_amounts([(a * (i + 1) + b * i) % 1001 for i in range(len(model.groups))])
        return [("credit", "partial", rates)] if rates else []
    if op == "storno":
        rates = model.storno_amounts()
        return [("credit", "storno", rates)] if rates else []
    if op == "write_off":
        rates = model.write_off_amounts()
        return [("credit", "write_off", rates)] if rates else []
    if op == "discount":
        if c - p < 2:
            return []
        paid = 1 + (c - p - 2) * a // 1000
        rates = model.discount_amounts(paid)
        return [("credit", "discount", rates), ("payment", paid)] if rates else []
    raise AssertionError(op)


def _check_state(model: am.InvoiceModel, eng: Engine, where: str) -> None:
    tg = pt.targets(eng.state, eng.bases)
    assert (int(tg.kept), int(tg.settled), int(tg.overpayment)) == (model.kept, model.settled, model.overpayment), where
    assert eng.nonzero_roles() == model.roles, (
        f"{where}: per-role sums differ\nengine {eng.nonzero_roles()}\nmodel  {model.roles}"
    )
    exp_vat = {k: v for k, v in model.vat_sums.items() if v != [0, 0]}
    assert eng.nonzero_vat() == exp_vat, (
        f"{where}: VAT by (key, month) differs\nengine {eng.nonzero_vat()}\nmodel  {exp_vat}"
    )
    gt = model.group_targets()
    for g in tg.groups:
        m = gt[int(g.rate)]
        taxed = model.treatment == "standard" and int(g.rate) > 0  # B_r is only a VAT base for taxed groups
        assert (int(g.cash), int(g.cash_vat), int(g.cash_net), int(g.due_vat), int(g.base) if taxed else 0) == (
            m.t, m.tv, m.tn, m.d, m.b if taxed else 0,
        ), f"{where}: group {g.rate}"  # fmt: skip
    # at full settlement the posted VAT per tax key equals the current document
    if model.current > 0 and model.kept == model.current:
        expect: dict[str, tuple[int, int]] = {}
        for g in model.groups:
            if model.treatment == "standard" and g.rate > 0:
                key = am.tax_key_of(g.rate)
                b, t = expect.get(key, (0, 0))
                expect[key] = (b + g.cn, t + g.cv)
        if model.treatment == "eu_b2b_service":
            expect["EU_SVC"] = (sum(g.cn for g in model.groups), 0)
        posted: dict[str, tuple[int, int]] = {}
        for (key, _), (b, t) in eng.nonzero_vat().items():
            pb, ptx = posted.get(key, (0, 0))
            posted[key] = (pb + b, ptx + t)
        posted = {k: v for k, v in posted.items() if v != (0, 0)}
        expect = {k: v for k, v in expect.items() if v != (0, 0)}
        assert posted == expect, f"{where}: settled invoice VAT {posted} != document {expect}"


@given(invoice_st, st.lists(step_st, min_size=1, max_size=14))
@SETTINGS
def test_engine_matches_independent_model(inv, steps):
    model, snapshot, bases, invoice_date = _build(inv)
    eng = Engine(bases, snapshot, invoice_date)
    d0 = model.issue()
    assert sum(d0.roles.values()) == 0
    _check_state(model, eng, "issue")
    today = invoice_date
    n = 0
    for op, a, b, days in steps:
        today = today + timedelta(days=days)
        for item in _concrete(model, op, a, b):
            n += 1
            if item[0] == "credit":
                _, kind, rates = item
                event(f"credit {kind}")
                delta = model.credit(kind, rates, today)
                assert sum(delta.roles.values()) == 0
                eng.apply(CreditEvent(f"c{n}", today, kind, _rg(rates)))
                _check_state(model, eng, f"{op}/{kind}")
            else:
                amount = item[1]
                if amount == 0 or model.kept + amount < 0:
                    continue
                before_state = eng.state
                t0, o0, k0 = model.settled, model.overpayment, model.kept
                helper = (
                    _nonzero(pc.write_off_reversal_amounts(before_state, PaymentEvent(f"p{n}", today, Cents(amount))))
                    if amount > 0
                    else {}
                )
                bank_before = eng.roles.get("bank", 0)
                parts = model.payment(amount, today)
                for kind, delta, rates in parts:
                    assert sum(delta.roles.values()) == 0
                    event(f"{kind} {inv['income']}/{inv['vat']}")
                    if kind == "write_off_reversal":
                        eng.apply(CreditEvent(f"r{n}", today, kind, _rg(rates, -1)))
                    else:
                        eng.apply(PaymentEvent(f"p{n}", today, Cents(amount)))
                _check_state(model, eng, f"{op}/payment")
                if model.kept > model.settled:
                    event("overpayment")
                if model.settled > model.current:
                    event("T > C")
                # a cash event moves exactly its amount through T and P - T
                assert (model.settled - t0) + (model.overpayment - o0) == amount == model.kept - k0
                assert eng.roles.get("bank", 0) - bank_before == amount
                # engine helper agrees with the model's reversal (negative amounts, per rate group of the invoice)
                expected = {
                    r: (-nn, -v)
                    for kind, _, rates in parts
                    if kind == "write_off_reversal"
                    for r, (nn, v) in rates.items()
                }
                assert helper == expected, f"write-off reversal: helper {helper} != model {expected}"  # noqa: E501


@given(invoice_st, st.integers(0, 1000), st.integers(0, 1000))
@SETTINGS
def test_credit_amount_helpers_match_model(inv, a, b):
    """The engine's document-amount helpers (9.3) give the model's discount / write-off amounts."""
    model, snapshot, bases, invoice_date = _build(inv)
    eng = Engine(bases, snapshot, invoice_date)
    model.issue()
    pay = max(1, model.gross * a // 1000)
    if pay >= model.gross:
        pay = model.gross - 1
    if pay > 0:
        model.payment(pay, invoice_date)
        eng.apply(PaymentEvent("p0", invoice_date, Cents(pay)))
    assert _nonzero(pc.write_off_amounts(eng.state)) == model.write_off_amounts()
    paid = 1 + (model.current - model.kept - 2) * b // 1000 if model.current - model.kept >= 2 else 0
    if paid > 0:
        assert _nonzero(pc.discount_amounts(eng.state, Cents(paid))) == model.discount_amounts(paid)


# ---- the model against the hand-computed fixture ----------------------------------------------------------------------
def _is_invoice_case(case: dict) -> bool:
    events = case.get("events")
    if events is None:
        return False
    if isinstance(events, str):
        events = JOURNAL_CASES[events.removeprefix("same as ")]["events"]
    return all(e["type"] in ("issue", "payment", "credit_note", "lock") for e in events)


INVOICE_CASES = [c for c in FIXTURE["journal_cases"] if _is_invoice_case(c)]


@pytest.mark.parametrize("case", INVOICE_CASES, ids=[c["id"] for c in INVOICE_CASES])
def test_model_reproduces_fixture(case):
    per_event, sums = am.replay_fixture_case(case, JOURNAL_CASES)
    expected: dict[int, dict[str, int]] = {}
    for entry in case.get("entries", []):
        roles = expected.setdefault(entry["event"], {})
        for line in entry["lines"]:
            roles[line[0]] = roles.get(line[0], 0) + line[1]
        assert sum(line[1] for line in entry["lines"]) == 0, "fixture entry does not balance"
    for i, got in per_event.items() if "entries" in case else ():
        exp = {k: v for k, v in expected.get(i, {}).items() if v}
        assert got == exp, f"{case['id']} event {i}: model {got} != fixture {exp}"
    exp_vat = {
        m: {
            k: {"base": v.get("base", 0), "tax": v.get("tax", 0)}
            for k, v in keys.items()
            if v.get("base") or v.get("tax")
        }
        for m, keys in case["vat"].items()
    }
    exp_vat = {m: keys for m, keys in exp_vat.items() if keys}
    assert sums == exp_vat


def test_issue_none_table_matches_fixture():
    case = JOURNAL_CASES["I6-none-table"]
    none = {(x["income_basis"], x["vat_basis"], x["treatment"]) for x in case["no_issue_entry"]}
    for income in ("eur", "accrual"):
        for vat in ("soll", "ist"):
            for treatment in ("standard", "eu_b2b_service", "small_business"):
                assert am.issue_posts_entry(income, vat, treatment) == ((income, vat, treatment) not in none)
                # the engine agrees (an invoice with non-zero amounts)
                rates = (RateGroup(Decimal(0), Cents(10000), Cents(0)),)
                if treatment == "standard":
                    rates = (RateGroup(Decimal(19), Cents(10000), Cents(1900)),)
                snap = IssuedSnapshot(
                    "x",
                    "small_business" if treatment == "small_business" else "regular",
                    treatment,
                    rates,
                    date(2026, 3, 1),
                )
                entry = pi.issue_entry(snap, Bases(income, vat), date(2026, 3, 1))
                assert (entry is not None) == am.issue_posts_entry(income, vat, treatment), (income, vat, treatment)


def test_allocation_and_rounding_helpers():
    """The model's own helpers on the worked numbers of A2 / A11."""
    assert am.allocate(100000, {19: 119000, 7: 53500}) == {19: 68986, 7: 31014}
    assert am.q2(am.Fraction(19000 * 68986, 119000)) == 11015
    assert am.q2(am.Fraction(9500 * 20500, 59500)) == 3273
    assert am.q2(am.Fraction(5, 2)) == 3 and am.q2(am.Fraction(-5, 2)) == -3
    assert am.allocate(1, {19: 1, 7: 1}) == {19: 1, 7: 0}  # tie goes to the larger rate
