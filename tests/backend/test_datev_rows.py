"""DATEV rows and the self-check (docs/specs/datev-export.md "From journal entries to rows", "Self-check", D1 to D3).

Expected rows are written by hand (account numbers of ``fixtures/chart_synthetic.json``: bank 9001, receivables 9003,
payables 9004, revenue_s19 9007, vat_out_s19 9013, vat_not_due_s19 9016, vat_soll_clearing_s19 9019, input_vat_s7
9023, rc_vat_out_eu 9025, rc_input_vat 9027, expense_software 9035, meals_deductible 9036, meals_nondeductible 9037).
"""

import json
import random
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from permita38.core.accounting.datev import (
    DatevBatchError,
    DatevRow,
    batch_rows,
    entry_rows,
    journal_balances,
    known_differences,
    simulate,
)
from permita38.core.accounting.types import AccountNo, Cents, Entry, Line, Role
from permita38.core.charts import Chart, ChartAccount, load_chart
from permita38.core.datev import DatevRules, datev_rules_for
from permita38.core.vat_rules import TaxKey

FIXTURES = Path(__file__).parent / "fixtures"
ORACLE = json.loads((FIXTURES / "accounting_oracle.json").read_text("utf-8"))
# The 2026 VAT rates per tax key, as the production caller passes them (no default inside the module).
RATES = {"S19": Decimal(19), "S7": Decimal(7), "RC_EU_SVC": Decimal(19), "RC_3C_SVC": Decimal(19)}
AUTOMATIC = {"revenue_s19": "S19", "revenue_s7": "S7"}


def _chart(keep_tax_automatic: bool = False) -> Chart:
    """The synthetic chart with the automatic accounts where DATEV has them: the revenue accounts.

    The fixture flags the VAT accounts instead; ``keep_tax_automatic`` leaves that as it is.
    """
    raw = load_chart(FIXTURES / "chart_synthetic.json")

    def fix(role: str, acc: ChartAccount) -> ChartAccount:
        if role in AUTOMATIC:
            return replace(acc, automatic=AUTOMATIC[role])
        if not keep_tax_automatic and acc.automatic is not None:
            return replace(acc, automatic=None)
        return acc

    roles = tuple((r, fix(r, a)) for r, a in raw.roles)
    by_no = {a.account: a for _, a in roles}
    accounts = tuple(by_no.get(a.account, a) for a in raw.accounts)
    return replace(raw, roles=roles, accounts=accounts)


@pytest.fixture()
def chart() -> Chart:
    return _chart()


@pytest.fixture()
def rules() -> DatevRules:
    return datev_rules_for(2026)


def line(role: str, cents: int, **kw: Any) -> Line:
    return Line(role=Role(role), amount_cents=Cents(cents), **kw)


def entry(*lines: Line, day: str = "2026-03-10", key: str = "test:1") -> Entry:
    d = date.fromisoformat(day)
    return Entry(lines=tuple(lines), booking_date=d, document_date=d, event_key=key)


def tax(role: str, cents: int, key: str, base: int) -> Line:
    return line(role, cents, tax_key=TaxKey(key), tax_kind="tax", tax_base_cents=Cents(base))


def simple(rows: list[DatevRow]) -> list[tuple[int, str, str, str, str | None]]:
    return [(r.amount_cents, r.side, r.konto, r.gegenkonto, r.bu_key) for r in rows]


def check(e: Entry, chart: Chart, rules: DatevRules) -> list[DatevRow]:
    rows = entry_rows(e, chart, rules, RATES)
    assert simulate(rows, chart, rules, RATES) == journal_balances([e], chart), e.event_key
    return rows


def oracle_entries(case: dict[str, Any]) -> list[Entry]:
    out: list[Entry] = []
    for i, raw in enumerate(case["entries"]):
        lines = []
        for item in raw["lines"]:
            meta = item[2] if len(item) > 2 else {}
            lines.append(
                Line(
                    role=Role(item[0]),
                    amount_cents=Cents(item[1]),
                    tax_key=TaxKey(meta["tax_key"]) if "tax_key" in meta else None,
                    tax_kind=meta.get("tax_kind"),
                    tax_rate=meta.get("tax_rate"),
                    tax_base_cents=Cents(meta["tax_base"]) if "tax_base" in meta else None,
                )
            )
        d = date.fromisoformat(raw["booking_date"])
        out.append(Entry(tuple(lines), d, d, f"{case['id']}:{i}"))
    return out


def case_entries(case_id: str) -> list[Entry]:
    return oracle_entries(next(c for c in ORACLE["journal_cases"] if c["id"] == case_id))


# --- D1 ---------------------------------------------------------------------------------------------------------


def test_d1_accrual_soll_issue_is_one_gross_row_without_bu_key(chart: Chart, rules: DatevRules) -> None:
    issue, pay = case_entries("A1-accrual-soll")
    rows = check(issue, chart, rules)
    assert simple(rows) == [(119000, "S", "9003", "9007", None)]
    assert (rows[0].belegdatum, rows[0].belegfeld1, rows[0].festschreibung, rows[0].note) == (
        "1401",
        "A1-accrual-soll:0",
        False,
        None,
    )
    assert simple(check(pay, chart, rules)) == [(119000, "S", "9001", "9003", None)]


def test_d1_without_automatic_account_uses_the_bu_key(rules: DatevRules) -> None:
    raw = load_chart(FIXTURES / "chart_synthetic.json")
    plain = replace(raw, roles=tuple((r, replace(a, automatic=None)) for r, a in raw.roles))
    issue = case_entries("A1-accrual-soll")[0]
    rows = entry_rows(issue, plain, rules, RATES)
    assert simple(rows) == [(119000, "S", "9003", "9007", "3")]
    assert simulate(rows, plain, rules, RATES) == journal_balances([issue], plain)


def test_automatic_flag_on_tax_accounts_is_ignored(rules: DatevRules) -> None:
    """The synthetic file flags vat_out / input_vat accounts automatic; DATEV has that on the partner account."""
    chart = _chart(keep_tax_automatic=True)
    for case in ("A1-accrual-ist", "A1-eur-soll", "A5-rc-eu-50"):
        for e in case_entries(case):
            check(e, chart, rules)


def test_a1_other_bases(chart: Chart, rules: DatevRules) -> None:
    ist_issue, ist_pay = case_entries("A1-accrual-ist")
    assert simple(check(ist_issue, chart, rules)) == [
        (100000, "S", "9003", "9007", "40"),
        (19000, "S", "9003", "9016", None),
    ]
    assert simple(check(ist_pay, chart, rules)) == [
        (119000, "S", "9001", "9003", None),
        (19000, "S", "9016", "9013", None),
    ]
    soll_issue, soll_pay = case_entries("A1-eur-soll")
    assert simple(check(soll_issue, chart, rules)) == [(19000, "S", "9019", "9013", None)]
    assert simple(check(soll_pay, chart, rules)) == [
        (100000, "S", "9001", "9007", "40"),
        (19000, "S", "9001", "9019", None),
    ]
    (ist_eur,) = case_entries("A1-eur-ist")
    assert simple(check(ist_eur, chart, rules)) == [(119000, "S", "9001", "9007", None)]


# --- D2 ---------------------------------------------------------------------------------------------------------


def test_d2_null_13b_key_uses_the_unfolded_form(chart: Chart, rules: DatevRules) -> None:
    assert rules.bu_key("RC_EU_SVC") is None
    (full,) = case_entries("A5-rc-eu-100")
    rows = check(full, chart, rules)
    assert simple(rows) == [(20050, "S", "9035", "9004", None), (3810, "S", "9027", "9025", None)]
    assert {r.note for r in rows} == {"fallback"}
    (half,) = case_entries("A5-rc-eu-50")
    rows = check(half, chart, rules)
    assert simple(rows) == [
        (20050, "S", "9035", "9004", None),
        (1905, "S", "9027", "9025", None),
        (1905, "S", "9035", "9025", None),
    ]
    assert {r.note for r in rows} == {"fallback"}


def test_d2_with_a_13b_key_the_net_row_is_folded(chart: Chart, rules: DatevRules) -> None:
    # "94" is a hypothetical placeholder key for this test only; the production data keeps the 13b key null until
    # the tax adviser confirms the real key.
    keyed = replace(rules, bu_keys={**rules.bu_keys, "RC_EU_SVC": "94"})
    (full,) = case_entries("A5-rc-eu-100")
    rows = entry_rows(full, chart, keyed, RATES)
    assert simple(rows) == [(20050, "S", "9035", "9004", "94")]
    assert simulate(rows, chart, keyed, RATES) == journal_balances([full], chart)
    (half,) = case_entries("A5-rc-eu-50")
    rows = entry_rows(half, chart, keyed, RATES)
    # row 200.50 with the key (DATEV: 38.10 out, 38.10 in), then the reclassification 19.05 of the spec
    assert simple(rows) == [(20050, "S", "9035", "9004", "94"), (1905, "S", "9035", "9027", None)]
    assert all(r.note is None for r in rows)
    sim = simulate(rows, chart, keyed, RATES)
    assert sim == journal_balances([half], chart)
    assert (sim[AccountNo("9025")], sim[AccountNo("9027")], sim[AccountNo("9035")], sim[AccountNo("9004")]) == (
        -3810,
        1905,
        21955,
        -20050,
    )


# --- D3 ---------------------------------------------------------------------------------------------------------


def test_d3_meal_is_one_gross_row_then_the_reclassification(chart: Chart, rules: DatevRules) -> None:
    (meal,) = case_entries("A6-meal")
    rows = check(meal, chart, rules)
    assert simple(rows) == [(10700, "S", "9036", "9004", "8"), (3000, "S", "9037", "9036", None)]
    sim = simulate(rows, chart, rules, RATES)
    assert [sim[AccountNo(a)] for a in ("9036", "9037", "9023", "9004")] == [7000, 3000, 700, -10700]


# --- folding proof and fallback -----------------------------------------------------------------------------------


def test_folded_issue_rows_reproduce_the_document_vat(chart: Chart, rules: DatevRules) -> None:
    rnd = random.Random(20261124)
    for _ in range(1000):
        net = rnd.randint(1, 5_000_000)
        rate, key, rev, vat_role, rev_no, vat_no = rnd.choice(
            [
                (19, "S19", "revenue_s19", "vat_out_s19", "9007", "9013"),
                (7, "S7", "revenue_s7", "vat_out_s7", "9008", "9014"),
            ]
        )
        vat = (net * rate * 2 + 100) // 200  # q2(net * rate / 100), half up
        e = entry(line("receivables", net + vat), line(rev, -net), tax(vat_role, -vat, key, net))
        rows = entry_rows(e, chart, rules, RATES)
        assert simple(rows) == [(net + vat, "S", "9003", rev_no, None)], (net, rate)
        assert rows[0].note is None
        assert simulate(rows, chart, rules, RATES)[AccountNo(vat_no)] == -vat, (net, rate)


def test_ist_partial_payment_falls_back_to_unfolded_rows(chart: Chart, rules: DatevRules) -> None:
    # invoice net 333.33 + VAT 63.33 = 396.66; 100.00 paid: TV 15.97, TN 84.03; the rest 296.66 carries
    # dV = 63.33 - 15.97 = 47.36 and dN = 333.33 - 84.03 = 249.30; DATEV would derive q2(296.66 * 19 / 119) = 47.37
    e = entry(
        line("bank", 29666),
        line("revenue_s19", -24930, income_date=date(2026, 4, 10)),
        tax("vat_out_s19", -4736, "S19", 24930),
    )
    rows = check(e, chart, rules)
    assert simple(rows) == [(24930, "S", "9001", "9007", "40"), (4736, "S", "9001", "9013", None)]
    assert {r.note for r in rows} == {"fallback"}
    # the folded row alone would be wrong by one cent
    folded = [DatevRow(Cents(29666), "S", AccountNo("9001"), AccountNo("9007"), None, "1004", "x", "x")]
    assert simulate(folded, chart, rules, RATES)[AccountNo("9013")] == -4737
    # and the report lists it
    assert [(d.kind, d.event_key) for d in known_differences([e], rows)] == [("fallback", "test:1")]


def test_a2_partial_payments_are_exact_either_way(chart: Chart, rules: DatevRules) -> None:
    for e in case_entries("A2-eur-ist"):
        check(e, chart, rules)


# --- no datev_transfer where the spec has a form -------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_id",
    [
        "A1-accrual-soll",
        "A1-accrual-ist",
        "A1-eur-soll",
        "A1-eur-ist",
        "A3-accrual-soll",
        "A6-meal",
        "A9-write-off-soll",
        "A9-write-off-ist",
        "A14-rc-eu-small-business-eur",
        "A17-rc-eu-private-share-eur",
        "I1-mixed-rates-accrual-soll",
        "I1-mixed-rates-eur-soll",
        "I2-other-rate-accrual-soll",
        "I3-negative-invoice-accrual-soll",
    ],
)
def test_no_transfer_rows(case_id: str, chart: Chart, rules: DatevRules) -> None:
    for e in case_entries(case_id):
        assert all(r.note != "transfer" for r in check(e, chart, rules)), e.event_key


# --- every journal case of the oracle ------------------------------------------------------------------------------

# entries that cannot be matched to one anchor go through datev_transfer; hand-checked list
TRANSFER_ENTRIES = {
    "A18-credit-moves-cash-eur-soll:2": "clearing lines of two rate groups without money or open item",
}


def _journal_cases() -> list[dict[str, Any]]:
    return [c for c in ORACLE["journal_cases"] if isinstance(c.get("entries"), list)]


def test_the_expected_transfer_entries_are_the_only_ones(chart: Chart, rules: DatevRules) -> None:
    found: set[str] = set()
    for case in _journal_cases():
        for e in oracle_entries(case):
            if any(r.note == "transfer" for r in entry_rows(e, chart, rules, RATES)):
                found.add(e.event_key)
    assert found == set(TRANSFER_ENTRIES)


@pytest.mark.parametrize("keep_tax_automatic", [False, True])
def test_simulation_equals_the_journal_for_every_oracle_case(keep_tax_automatic: bool, rules: DatevRules) -> None:
    chart = _chart(keep_tax_automatic)
    cases = _journal_cases()
    assert len(cases) >= 30
    for case in cases:
        entries = oracle_entries(case)
        rows = batch_rows(entries, chart, rules, RATES)
        assert simulate(rows, chart, rules, RATES) == journal_balances(entries, chart), case["id"]
        for e in entries:
            check(e, chart, rules)


def test_row_fields_are_bounded(chart: Chart, rules: DatevRules) -> None:
    long = entry(line("bank", 100), line("revenue_s0", -100), key="payment:" + "x" * 100)
    rows = entry_rows(long, chart, rules, RATES)
    assert rows
    for r in rows:
        assert r.amount_cents > 0 and r.side == "S"
        assert len(r.belegfeld1) <= 36 and len(r.text) <= 60 and len(r.belegdatum) == 4
        assert not r.festschreibung
    assert all(r.festschreibung for r in entry_rows(long, chart, rules, RATES, festschreibung=True))


def test_long_event_keys_keep_distinct_belegfeld1_values(chart: Chart, rules: DatevRules) -> None:
    base = "expense:" + "0123456789abcdef" * 3  # one long expense id
    keys = [f"{base}:bill", f"{base}:payment:1", f"{base}:revision:2"]
    values = []
    for k in keys:
        (row,) = entry_rows(entry(line("bank", 100), line("revenue_s0", -100), key=k), chart, rules, RATES)
        values.append(row.belegfeld1)
    assert all(len(v) <= 36 for v in values)
    assert len(set(values)) == 3
    assert all(v[27] == "~" for v in values)
    assert (
        entry_rows(entry(line("bank", 1), line("revenue_s0", -1), key="short:1"), chart, rules, RATES)[0].belegfeld1
        == "short:1"
    )


def _gross(net: int, rate: int) -> tuple[int, int]:
    return net, (net * rate * 2 + 100) // 200


@settings(max_examples=300, deadline=None)
@given(
    net=st.integers(1, 5_000_000),
    rate=st.sampled_from([19, 7]),
    share=st.integers(1, 10_000),
)
def test_random_ist_partial_payments_simulate_to_the_journal(net: int, rate: int, share: int) -> None:
    chart, rules = _chart(), datev_rules_for(2026)
    net, vat = _gross(net, rate)
    gross = net + vat
    paid = max(1, gross * share // 10_000)  # a random partial amount of the gross, at least one cent
    tv = (vat * paid * 2 + gross) // (2 * gross)  # VAT share of the payment, half up
    if tv == 0 or tv == paid:
        return
    key, rev, vat_role = ("S19", "revenue_s19", "vat_out_s19") if rate == 19 else ("S7", "revenue_s7", "vat_out_s7")
    e = entry(line("bank", paid), line(rev, -(paid - tv)), tax(vat_role, -tv, key, paid - tv))
    rows = entry_rows(e, chart, rules, RATES)
    assert simulate(rows, chart, rules, RATES) == journal_balances([e], chart), (net, rate, paid)
    assert {r.note for r in rows} <= {None, "fallback"}


# --- hand-written expected rows (accounts of fixtures/chart_synthetic.json) -----------------------------------------


def test_i1_expected_rows_by_hand(chart: Chart, rules: DatevRules) -> None:
    # 19 %: net 3150 + vat 599 = 3749 on 9007; 7 %: 123499 + 8645 = 132144 on 9008; 0 %: 600 on 9010.
    (e,) = case_entries("I1-mixed-rates-accrual-soll")
    assert simple(entry_rows(e, chart, rules, RATES)) == [
        (3749, "S", "9003", "9007", None),
        (132144, "S", "9003", "9008", None),
        (600, "S", "9003", "9010", None),
    ]


def test_i2_expected_rows_by_hand(chart: Chart, rules: DatevRules) -> None:
    # SX has no BU key: net 100000 on 9009 and vat 16000 on 9015, both unfolded (fallback).
    (e,) = case_entries("I2-other-rate-accrual-soll")
    rows = entry_rows(e, chart, rules, RATES)
    assert simple(rows) == [(100000, "S", "9003", "9009", None), (16000, "S", "9003", "9015", None)]
    assert {r.note for r in rows} == {"fallback"}


def test_a9_expected_rows_by_hand(chart: Chart, rules: DatevRules) -> None:
    # issue 100000 + 19000 = 119000 gross on 9007; payment 59500 bank/receivables;
    # write-off: bad debts 50000 + vat 9500 = 59500 gross on 9040 against receivables, BU key 3 (S19).
    issue, payment, write_off = case_entries("A9-write-off-soll")
    assert simple(entry_rows(issue, chart, rules, RATES)) == [(119000, "S", "9003", "9007", None)]
    assert simple(entry_rows(payment, chart, rules, RATES)) == [(59500, "S", "9001", "9003", None)]
    assert simple(entry_rows(write_off, chart, rules, RATES)) == [(59500, "S", "9040", "9003", "3")]


# --- statistical lines, batches, report ---------------------------------------------------------------------------


def test_statistical_line_is_not_exported_and_is_listed(chart: Chart, rules: DatevRules) -> None:
    stat = case_entries("A7-eu-b2b-ist-unpaid")[0]
    assert entry_rows(stat, chart, rules, RATES) == []
    assert [(d.kind, d.event_key) for d in known_differences([stat])] == [("statistical", stat.event_key)]


def test_eur_clearing_entries_are_listed() -> None:
    issue = case_entries("A1-eur-soll")[0]
    assert [d.kind for d in known_differences([issue])] == ["eur_clearing"]
    assert known_differences(case_entries("A1-accrual-soll")) == []


def test_transfer_rows_are_listed(chart: Chart, rules: DatevRules) -> None:
    e = case_entries("A18-credit-moves-cash-eur-soll")[2]
    rows = entry_rows(e, chart, rules, RATES)
    assert {r.note for r in rows} == {"transfer"}
    assert all("9034" in (r.konto, r.gegenkonto) for r in rows)
    assert "transfer" in {d.kind for d in known_differences([e], rows)}


def test_batch_is_ordered_by_booking_date_and_refuses_two_years(chart: Chart, rules: DatevRules) -> None:
    early = entry(line("bank", 500), line("revenue_s0", -500), day="2026-01-05", key="e:early")
    late = entry(line("bank", 700), line("revenue_s0", -700), day="2026-02-05", key="e:late")
    rows = batch_rows([late, early], chart, rules, RATES)
    assert [r.event_key for r in rows] == ["e:early", "e:late"]
    other_year = entry(line("bank", 1), line("revenue_s0", -1), day="2027-01-02", key="e:next")
    with pytest.raises(DatevBatchError):
        batch_rows([early, other_year], chart, rules, RATES)
