"""Target rule of accounting.md 9.2 (payments, refunds, credit notes) against the hand-computed oracle.

The fixture ``fixtures/accounting_oracle.json`` is never regenerated from the code under test. Entries are compared
in event order; the lines of one entry are compared as a sorted list because the fixture orders the lines of an entry
by hand and not by one rule (e.g. A4 storno lists ``vat_not_due`` before ``receivables``, A18 after it).
"""

import json
import random
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from permita38.core.accounting.postings_issue import _role, issue_entry
from permita38.core.accounting.postings_targets import (
    allocate,
    apply_event,
    div_half_up,
    event_entry,
    targets,
)
from permita38.core.accounting.types import (
    Bases,
    Cents,
    CreditEvent,
    Entry,
    Event,
    InvoiceState,
    IssuedSnapshot,
    IssueEvent,
    PaymentEvent,
    RateGroup,
    Treatment,
)

ORACLE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "accounting_oracle.json").read_text(encoding="utf-8")
)
CASES: dict[str, dict[str, Any]] = {c["id"]: c for c in ORACLE["journal_cases"]}

REPLAY_CASES = [
    "A1-accrual-soll",
    "A1-accrual-ist",
    "A1-eur-soll",
    "A1-eur-ist",
    "A2-eur-ist",
    "A3-accrual-soll",
    "A4-eur-ist",
    "A4-accrual-ist",
    "A8-overpayment",
    "A11-credit-then-refunds-eur-ist",
    "A12-storno-after-payment-soll",
    "A18-credit-moves-cash-accrual-ist",
    "A18-credit-moves-cash-eur-soll",
]


def _events(case: dict[str, Any]) -> list[dict[str, Any]]:
    events = case["events"]
    if isinstance(events, str):
        return list(CASES[events.removeprefix("same as ").strip()]["events"])
    return list(events)


def _d(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def _groups(rates: dict[str, Any]) -> tuple[RateGroup, ...]:
    return tuple(
        RateGroup(Decimal(r), Cents(v["net"]), Cents(v["vat"]))
        for r, v in sorted(rates.items(), key=lambda kv: Decimal(kv[0]), reverse=True)
    )


def _snapshot(event: dict[str, Any]) -> IssuedSnapshot:
    return IssuedSnapshot(
        document_id=event["id"],
        regime="small_business" if event["treatment"] == "small_business" else "regular",
        treatment=event["treatment"],
        rates=_groups(event["rates"]),
        perf=_d(event.get("service_to")) or _d(event.get("service_from")) or date.fromisoformat(event["date"]),
    )


def _bases(case: dict[str, Any]) -> Bases:
    return Bases(case["bases"]["income_basis"], case["bases"]["vat_basis"])


def _replay(case: dict[str, Any]) -> list[Entry | None]:
    """Run the fixture events through ``event_entry``; one result per event."""
    bases = _bases(case)
    invoices: dict[str, InvoiceState] = {}
    out: list[Entry | None] = []
    for ev in _events(case):
        when = date.fromisoformat(ev["date"])
        event: Event
        if ev["type"] == "issue":
            state = InvoiceState.issued(_snapshot(ev))
            out.append(event_entry(None, state, bases, IssueEvent(when)))
            invoices[ev["id"]] = state
            continue
        before = invoices[ev["invoice"]]
        if ev["type"] == "payment":
            event = PaymentEvent(f"pay{len(out)}", when, Cents(ev["amount"]))
        else:
            event = CreditEvent(ev["id"], when, ev["credit_kind"], _groups(ev["rates"]))
        after = apply_event(before, event)
        out.append(event_entry(before, after, bases, event))
        invoices[ev["invoice"]] = after
    return out


def _oracle_rows(entry: dict[str, Any], invoice: str) -> list[tuple[Any, ...]]:
    rows = []
    for row in entry["lines"]:
        opts = row[2] if len(row) > 2 else {}
        rows.append(
            (
                row[0],
                row[1],
                opts.get("tax_key") or "",
                opts.get("tax_kind") or "",
                opts.get("tax_base"),
                opts.get("vat_date") or "",
                opts.get("income_date") or "",
                invoice if row[0] == "receivables" else "",
                opts.get("tax_rate") or "",
            )
        )
    return sorted(rows, key=repr)


def _actual_rows(entry: Entry, with_rate: bool, invoice: str) -> list[tuple[Any, ...]]:
    rows = [
        (
            ln.role,
            ln.amount_cents,
            ln.tax_key or "",
            ln.tax_kind or "",
            ln.tax_base_cents,
            ln.vat_date.isoformat() if ln.vat_date else "",
            ln.income_date.isoformat() if ln.income_date else "",
            ln.open_item_id or "",
            (ln.tax_rate or "") if with_rate else "",
        )
        for ln in entry.lines
    ]
    assert all(ln.open_item_id in (None, invoice) for ln in entry.lines)
    return sorted(rows, key=repr)


def _vat_sums(entries: list[Entry]) -> dict[str, dict[str, dict[str, int]]]:
    sums: dict[str, dict[str, dict[str, int]]] = {}
    for entry in entries:
        for ln in entry.lines:
            if ln.tax_kind is None or ln.tax_key is None or ln.vat_date is None:
                continue
            slot = sums.setdefault(ln.vat_date.strftime("%Y-%m"), {}).setdefault(ln.tax_key, {"base": 0, "tax": 0})
            slot["base"] += ln.tax_base_cents or 0
            if ln.tax_kind == "tax":
                slot["tax"] += -ln.amount_cents
    return sums


@pytest.mark.parametrize("case_id", REPLAY_CASES)
def test_events_replay_to_the_oracle_entries(case_id: str) -> None:
    case = CASES[case_id]
    results = _replay(case)
    invoice = _events(case)[0]["id"]
    expected = {e["event"]: e for e in case["entries"]}
    assert set(case.get("no_entry_events", [])) | set(expected) == set(range(len(results)))
    for index, entry in enumerate(results):
        if index not in expected:
            assert entry is None, (case_id, index)
            continue
        assert entry is not None, (case_id, index)
        want = expected[index]
        oracle = _oracle_rows(want, invoice)
        with_rate = any(row[8] for row in oracle)
        assert _actual_rows(entry, with_rate, invoice) == oracle, (case_id, index)
        assert entry.booking_date.isoformat() == want["booking_date"]
        assert entry.document_date.isoformat() == want.get("document_date", want["booking_date"])
        assert entry.balance == 0
    booked = [e for e in results if e is not None]
    assert _vat_sums(booked) == case["vat"], case_id
    for e in booked:
        assert e.event_key.split(":")[0] in ("issue", "payment", "credit")


def test_a2_vat_sums_per_month_and_totals_equal_the_document() -> None:
    entries = [e for e in _replay(CASES["A2-eur-ist"]) if e is not None]
    sums = _vat_sums(entries)
    assert sums["2026-03"] == {"S19": {"base": 57971, "tax": 11015}, "S7": {"base": 28985, "tax": 2029}}
    assert sums["2026-04"] == {"S19": {"base": 42029, "tax": 7985}, "S7": {"base": 21015, "tax": 1471}}
    assert sum(v["S19"]["base"] for v in sums.values()) == 100000
    assert sum(v["S19"]["tax"] for v in sums.values()) == 19000
    assert sum(v["S7"]["base"] for v in sums.values()) == 50000
    assert sum(v["S7"]["tax"] for v in sums.values()) == 3500


def test_a2_allocation_and_vat_shares() -> None:
    snap = _snapshot(_events(CASES["A2-eur-ist"])[0])
    state = apply_event(InvoiceState.issued(snap), PaymentEvent("p1", date(2026, 3, 5), Cents(100000)))
    result = targets(state, Bases("eur", "ist"))
    assert [(g.rate, g.cash, g.cash_vat, g.cash_net) for g in result.groups] == [
        (Decimal(19), 68986, 11015, 57971),
        (Decimal(7), 31014, 2029, 28985),
    ]
    assert (result.kept, result.settled, result.overpayment) == (100000, 100000, 0)


def test_allocate_largest_remainder_ties_to_the_larger_rate() -> None:
    assert allocate(Cents(100000), [(Decimal(19), Cents(119000)), (Decimal(7), Cents(53500))]) == (68986, 31014)
    # equal remainders: 1 cent over two equal weights goes to the larger rate, whatever the order
    assert allocate(Cents(1), [(Decimal(7), Cents(100)), (Decimal(19), Cents(100))]) == (0, 1)
    assert allocate(Cents(1), [(Decimal(19), Cents(100)), (Decimal(7), Cents(100))]) == (1, 0)
    assert allocate(Cents(0), [(Decimal(19), Cents(100))]) == (0,)
    assert allocate(Cents(5), [(Decimal(19), Cents(0)), (Decimal(7), Cents(10))]) == (0, 5)
    with pytest.raises(ValueError):
        allocate(Cents(5), [(Decimal(19), Cents(0))])


def test_div_half_up_rounds_ties_away_from_zero() -> None:
    assert [div_half_up(n, 2) for n in (1, 3, -1, -3, 4)] == [1, 2, -1, -2, 2]
    assert div_half_up(19000 * 100000, 119000) == 15966  # 159.6639 -> 159.66
    assert div_half_up(1, -2) == -1


def test_a11_over_branch_values() -> None:
    """After the credit and the first refund: P = 800.00 > C = 595.00 (T > C branch)."""
    snap = _snapshot(_events(CASES["A11-credit-then-refunds-eur-ist"])[0])
    state = InvoiceState.issued(snap)
    for ev in (
        PaymentEvent("p", date(2026, 3, 5), Cents(100000)),
        CreditEvent("c", date(2026, 4, 1), "partial", (RateGroup(Decimal(19), Cents(50000), Cents(9500)),)),
        PaymentEvent("r1", date(2026, 5, 2), Cents(-20000)),
    ):
        state = apply_event(state, ev)
    result = targets(state, Bases("eur", "ist"))
    [grp] = result.groups
    assert (grp.cash, grp.cash_vat, grp.cash_net, grp.due_vat, grp.base) == (80000, 12773, 67227, 12773, 67227)


def test_a12_soll_keeps_the_vat_until_the_refund() -> None:
    snap = _snapshot(_events(CASES["A12-storno-after-payment-soll"])[0])
    bases = Bases("accrual", "soll")
    state = apply_event(InvoiceState.issued(snap), PaymentEvent("p", date(2026, 2, 10), Cents(119000)))
    storno = CreditEvent("c", date(2026, 3, 2), "storno", snap.rates)
    after = apply_event(state, storno)
    [grp] = targets(after, bases).groups
    assert (grp.due_vat, grp.base) == (19000, 100000)  # D = max(CV = 0, TV = 190.00), TN = 1,000.00
    refunded = apply_event(after, PaymentEvent("r", date(2026, 4, 15), Cents(-119000)))
    [grp] = targets(refunded, bases).groups
    assert (grp.due_vat, grp.base) == (0, 0)


@pytest.mark.parametrize("kind", ["discount", "write_off", "write_off_reversal"])
def test_other_credit_kinds_are_f7(kind: Any) -> None:
    snap = IssuedSnapshot(
        "x", "regular", "standard", (RateGroup(Decimal(19), Cents(1000), Cents(190)),), date(2026, 1, 1)
    )
    with pytest.raises(NotImplementedError):
        apply_event(InvoiceState.issued(snap), CreditEvent("c", date(2026, 1, 2), kind, snap.rates))


def test_invalid_events_are_refused() -> None:
    snap = IssuedSnapshot(
        "x", "regular", "standard", (RateGroup(Decimal(19), Cents(1000), Cents(190)),), date(2026, 1, 1)
    )
    state = InvoiceState.issued(snap)
    with pytest.raises(ValueError):
        apply_event(state, PaymentEvent("p", date(2026, 1, 2), Cents(-1)))
    too_much = (RateGroup(Decimal(19), Cents(1001), Cents(190)),)
    with pytest.raises(ValueError):
        apply_event(state, CreditEvent("c", date(2026, 1, 2), "partial", too_much))
    other_rate = (RateGroup(Decimal(7), Cents(10), Cents(1)),)
    with pytest.raises(ValueError):
        apply_event(state, CreditEvent("c", date(2026, 1, 2), "partial", other_rate))


def test_event_keys_and_dates() -> None:
    snap = _snapshot(_events(CASES["A12-storno-after-payment-soll"])[0])
    bases = Bases("accrual", "soll")
    state = InvoiceState.issued(snap)
    paid = PaymentEvent("pay-7", date(2026, 2, 10), Cents(119000))
    entry = event_entry(state, apply_event(state, paid), bases, paid)
    assert entry is not None and entry.event_key == "payment:pay-7"
    after = apply_event(state, paid)
    credit = CreditEvent("cn-9", date(2026, 3, 2), "storno", snap.rates)
    entry = event_entry(after, apply_event(after, credit), bases, credit)
    assert entry is not None and entry.event_key == "credit:cn-9"
    assert entry.booking_date == entry.document_date == date(2026, 3, 2)


# ---- the issue entry is the same rule from the empty state -------------------------------------------------------


def _line_key(entry: Entry) -> list[tuple[Any, ...]]:
    return sorted(
        (
            (
                ln.role,
                ln.amount_cents,
                ln.tax_key or "",
                ln.tax_kind or "",
                ln.tax_rate or "",
                ln.tax_base_cents,
                ln.vat_date.isoformat() if ln.vat_date else "",
                ln.income_date.isoformat() if ln.income_date else "",
                ln.open_item_id or "",
            )
            for ln in entry.lines
        ),
        key=repr,
    )


def _same_entry(a: Entry | None, b: Entry | None) -> None:
    assert (a is None) == (b is None)
    if a is None or b is None:
        return
    assert _line_key(a) == _line_key(b)
    assert (a.booking_date, a.document_date, a.event_key) == (b.booking_date, b.document_date, b.event_key)


ISSUE_FIXTURE_CASES = [
    c["id"]
    for c in ORACLE["journal_cases"]
    if "bases" in c and _events(c)[0].get("type") == "issue" and "rates" in _events(c)[0]
]


@pytest.mark.parametrize("case_id", ISSUE_FIXTURE_CASES)
def test_issue_is_the_transition_from_the_empty_state(case_id: str) -> None:
    case = CASES[case_id]
    bases = Bases(case["bases"]["income_basis"], case["bases"]["vat_basis"])
    ev = _events(case)[0]
    snap = _snapshot(ev)
    when = date.fromisoformat(ev["date"])
    _same_entry(event_entry(None, InvoiceState.issued(snap), bases, IssueEvent(when)), issue_entry(snap, bases, when))


def test_issue_cases_exist_in_the_fixture() -> None:
    assert len(ISSUE_FIXTURE_CASES) >= 25


@pytest.mark.parametrize("treatment", ["standard", "eu_b2b_service", "small_business"])
@pytest.mark.parametrize("income", ["accrual", "eur"])
@pytest.mark.parametrize("vat", ["soll", "ist"])
def test_issue_matches_f5_for_every_treatment_and_basis(income: str, vat: str, treatment: Treatment) -> None:
    rates = (
        (RateGroup(Decimal(19), Cents(33333), Cents(6333)), RateGroup(Decimal(7), Cents(1001), Cents(70)))
        if treatment == "standard"
        else (RateGroup(Decimal(0), Cents(12345), Cents(0)),)
    )
    snap = IssuedSnapshot(
        "b1", "small_business" if treatment == "small_business" else "regular", treatment, rates, date(2026, 1, 1)
    )
    bases = Bases(income, vat)  # type: ignore[arg-type]
    day = date(2026, 1, 2)
    _same_entry(event_entry(None, InvoiceState.issued(snap), bases, IssueEvent(day)), issue_entry(snap, bases, day))


# ---- treatments after the issue -----------------------------------------------------------------------------------


def test_eu_b2b_eur_payment_posts_revenue_without_vat_line() -> None:
    snap = IssuedSnapshot(
        "e1", "regular", "eu_b2b_service", (RateGroup(Decimal(0), Cents(400000), Cents(0)),), date(2026, 3, 10)
    )
    bases = Bases("eur", "ist")
    state = InvoiceState.issued(snap)
    pay = PaymentEvent("p", date(2026, 5, 4), Cents(400000))
    entry = event_entry(state, apply_event(state, pay), bases, pay)
    assert entry is not None
    assert [(ln.role, ln.amount_cents, ln.tax_key, ln.income_date) for ln in entry.lines] == [
        ("bank", 400000, None, None),
        ("revenue_eu_svc", -400000, None, date(2026, 5, 4)),
    ]


def test_eu_b2b_credit_note_accrual_moves_the_base_at_the_credit_date() -> None:
    snap = IssuedSnapshot(
        "e2", "regular", "eu_b2b_service", (RateGroup(Decimal(0), Cents(100000), Cents(0)),), date(2026, 3, 10)
    )
    bases = Bases("accrual", "soll")
    state = InvoiceState.issued(snap)
    credit = CreditEvent("c", date(2026, 4, 2), "partial", (RateGroup(Decimal(0), Cents(30000), Cents(0)),))
    entry = event_entry(state, apply_event(state, credit), bases, credit)
    assert entry is not None
    by_role = {ln.role: ln for ln in entry.lines}
    assert by_role["revenue_eu_svc"].amount_cents == 30000
    assert by_role["revenue_eu_svc"].tax_base_cents == -30000
    assert by_role["revenue_eu_svc"].vat_date == date(2026, 4, 2)
    assert by_role["receivables"].amount_cents == -30000
    assert entry.balance == 0


def test_small_business_eur_cash_events_post_revenue_ku_with_base() -> None:
    snap = IssuedSnapshot(
        "k1", "small_business", "small_business", (RateGroup(Decimal(0), Cents(50000), Cents(0)),), date(2026, 1, 5)
    )
    bases = Bases("eur", "ist")
    state = InvoiceState.issued(snap)
    pay = PaymentEvent("p", date(2026, 2, 3), Cents(20000))
    after = apply_event(state, pay)
    entry = event_entry(state, after, bases, pay)
    assert entry is not None
    ku = next(ln for ln in entry.lines if ln.role == "revenue_ku")
    assert (ku.amount_cents, ku.tax_key, ku.tax_kind, ku.tax_base_cents) == (-20000, "KU", "base_only", 20000)
    assert ku.vat_date == ku.income_date == date(2026, 2, 3)
    refund = PaymentEvent("r", date(2026, 3, 1), Cents(-20000))
    entry = event_entry(after, apply_event(after, refund), bases, refund)
    assert entry is not None
    ku = next(ln for ln in entry.lines if ln.role == "revenue_ku")
    assert (ku.amount_cents, ku.tax_base_cents) == (20000, -20000)


def test_overpayment_goes_to_customer_credit_and_back() -> None:
    snap = IssuedSnapshot(
        "o1", "regular", "standard", (RateGroup(Decimal(19), Cents(10000), Cents(1900)),), date(2026, 6, 1)
    )
    bases = Bases("eur", "soll")
    state = apply_event(InvoiceState.issued(snap), PaymentEvent("a", date(2026, 6, 2), Cents(11900)))
    over = PaymentEvent("b", date(2026, 6, 3), Cents(100))
    entry = event_entry(state, apply_event(state, over), bases, over)
    assert entry is not None
    assert [(ln.role, ln.amount_cents, ln.income_date) for ln in entry.lines] == [
        ("bank", 100, None),
        ("customer_credit", -100, None),
    ]


# ---- invariants on random event sequences ------------------------------------------------------------------------


ALL_BASES = [Bases("accrual", "soll"), Bases("accrual", "ist"), Bases("eur", "soll"), Bases("eur", "ist")]


def _vat(net: int, rate: int) -> int:
    return div_half_up(net * rate, 100)


def _random_snapshot(rng: random.Random) -> IssuedSnapshot:
    groups = []
    for rate in rng.sample([19, 7, 5], rng.randint(1, 3)):
        net = rng.randint(1, 500000)
        groups.append(RateGroup(Decimal(rate), Cents(net), Cents(_vat(net, rate))))
    groups.sort(key=lambda g: g.rate, reverse=True)
    return IssuedSnapshot("rnd", "regular", "standard", tuple(groups), date(2026, 1, 3))


def _random_credit(rng: random.Random, state: InvoiceState, when: date, n: int) -> CreditEvent | None:
    parts = []
    for grp, done in zip(state.snapshot.rates, state.credited, strict=True):
        left_net, left_vat = grp.net - done.net, grp.vat - done.vat
        if left_net <= 0 or rng.random() < 0.4:
            continue
        net = left_net if rng.random() < 0.3 else rng.randint(1, left_net)
        vat = left_vat if net == left_net else min(left_vat, _vat(net, int(grp.rate)))
        parts.append(RateGroup(grp.rate, Cents(net), Cents(vat)))
    if not parts:
        return None
    return CreditEvent(f"c{n}", when, "partial", tuple(parts))


def _random_run(seed: int, bases: Bases) -> tuple[InvoiceState, list[Entry]]:
    rng = random.Random(seed)
    snap = _random_snapshot(rng)
    state = InvoiceState.issued(snap)
    entries: list[Entry] = []
    first = event_entry(None, state, bases, IssueEvent(snap.perf))
    assert (first is None) == (bases == Bases("eur", "ist"))
    if first is not None:
        entries.append(first)
    day = 10
    for n in range(rng.randint(1, 12)):
        day += 1
        when = date(2026, 1 + day // 28, 1 + day % 28)
        event: Event | None
        roll = rng.random()
        if roll < 0.3:
            event = _random_credit(rng, state, when, n)
        elif roll < 0.55 and state.kept > 0:
            event = PaymentEvent(f"r{n}", when, Cents(-rng.randint(1, state.kept)))
        else:
            event = PaymentEvent(f"p{n}", when, Cents(rng.randint(1, max(1, state.snapshot.gross))))
        if event is None:
            continue
        after = apply_event(state, event)
        entry = event_entry(state, after, bases, event)
        if isinstance(event, PaymentEvent):
            assert targets(after, bases).kept - targets(state, bases).kept == event.amount
            delta_settled = targets(after, bases).settled - targets(state, bases).settled
            delta_over = targets(after, bases).overpayment - targets(state, bases).overpayment
            assert delta_settled + delta_over == event.amount  # dT + d(P - T) = p
            assert entry is not None
        if entry is not None:
            entries.append(entry)
        state = after
    return state, entries


@pytest.mark.parametrize("bases", ALL_BASES, ids=lambda b: f"{b.income}-{b.vat}")
@pytest.mark.parametrize("seed", range(150))
def test_random_sequences_keep_the_invariants(seed: int, bases: Bases) -> None:
    state, entries = _random_run(seed, bases)
    assert all(e.balance == 0 for e in entries)
    result = targets(state, bases)
    if bases.vat == "soll":
        assert sum(g.due_vat for g in result.groups) >= sum(g.cash_vat for g in result.groups)
    for g in result.groups:
        assert g.cash_vat + g.cash_net == g.cash
    assert sum(g.cash for g in result.groups) == result.settled
    # settle completely: kept cash = current gross; then the journal carries exactly the current document
    current = Cents(sum(g.gross - k.gross for g, k in zip(state.snapshot.rates, state.credited, strict=True)))
    settle = PaymentEvent("fin", date(2026, 12, 1), Cents(current - state.kept))
    final = apply_event(state, settle)
    last = event_entry(state, final, bases, settle)
    if last is not None:
        assert last.balance == 0
        entries.append(last)
    tgt = targets(final, bases)
    for grp, cred, tg in zip(final.snapshot.rates, final.credited, tgt.groups, strict=True):
        cur_vat, cur_net = grp.vat - cred.vat, grp.net - cred.net
        assert (tg.due_vat, tg.base) == (cur_vat, cur_net), (seed, bases, grp.rate)
        rate = grp.rate
        booked = sum(-ln.amount_cents for e in entries for ln in e.lines if ln.role == _role("vat_out", rate))
        base = sum(ln.tax_base_cents or 0 for e in entries for ln in e.lines if ln.role == _role("vat_out", rate))
        assert (booked, base) == (cur_vat, cur_net), (seed, bases, rate)
    assert tgt.overpayment == 0
