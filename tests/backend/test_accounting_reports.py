"""Reports over the journal (accounting.md section 14): hand-computed numbers on the oracle journal cases.

All amounts are integer cents; the synthetic chart (fake account numbers) maps roles to accounts."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from permita38.core.accounting.reports import (
    VAT_ROUNDING_NOTE,
    EurLineTotal,
    PostedLine,
    balance_sheet,
    eur_report,
    ledger,
    pnl,
    trial_balance,
)
from permita38.core.accounting.types import AccountNo, Cents, Line, Role
from permita38.core.charts import Chart, load_chart

FIXTURES = Path(__file__).parent / "fixtures"
CASES: dict[str, dict[str, Any]] = {
    c["id"]: c for c in json.loads((FIXTURES / "accounting_oracle.json").read_text("utf-8"))["journal_cases"]
}
CHART: Chart = load_chart(FIXTURES / "chart_synthetic.json")
BANK, RECEIVABLES, REVENUE, VAT_OUT = (AccountNo(a) for a in ("9001", "9003", "9007", "9013"))


def _d(text: str) -> date:
    return date.fromisoformat(text)


def posted_line(
    role: str, amount: int, booking: str, no: int, income_date: str | None = None, vat_date: str | None = None
) -> PostedLine:
    line = Line(
        Role(role),
        Cents(amount),
        income_date=_d(income_date) if income_date else None,
        vat_date=_d(vat_date) if vat_date else None,
    )
    return PostedLine(line, _d(booking), no)


def case_lines(case_id: str, first_no: int = 1) -> list[PostedLine]:
    """The entries of an oracle case as posted lines; entry numbers count up from ``first_no``."""
    out: list[PostedLine] = []
    for i, entry in enumerate(CASES[case_id]["entries"]):
        for raw in entry["lines"]:
            meta: dict[str, Any] = raw[2] if len(raw) > 2 else {}
            out.append(
                posted_line(
                    raw[0], raw[1], entry["booking_date"], first_no + i, meta.get("income_date"), meta.get("vat_date")
                )
            )
    return out


A1 = "A1-accrual-soll"


def test_trial_balance_of_a1_accrual_soll_balances_and_receivables_net_to_zero():
    tb = trial_balance(case_lines(A1), CHART, _d("2026-01-01"), _d("2026-12-31"))
    rows = {r.account: r for r in tb.rows}
    # Entry 1: D receivables 1190.00 / C revenue 1000.00, C vat_out 190.00; entry 2: D bank 1190.00 / C receivables 1190.00
    assert rows[BANK].debit == 119000 and rows[BANK].credit == 0 and rows[BANK].balance == 119000
    assert rows[RECEIVABLES].debit == 119000 and rows[RECEIVABLES].credit == 119000  # paid in full
    assert rows[RECEIVABLES].balance == 0
    assert rows[REVENUE].credit == 100000 and rows[REVENUE].balance == -100000
    assert rows[VAT_OUT].credit == 19000
    assert [r.account for r in tb.rows] == ["9001", "9003", "9007", "9013"]  # ascending account numbers
    assert tb.debit_total == 238000 and tb.credit_total == 238000  # 119000 + 119000 = 100000 + 19000 + 119000
    assert tb.balance_total == 0
    assert tb.note == VAT_ROUNDING_NOTE


def test_trial_balance_period_filter_is_by_booking_date_inclusive():
    january = trial_balance(case_lines(A1), CHART, _d("2026-01-01"), _d("2026-01-31"))
    # only entry 1: receivables 1190.00 debit, revenue 1000.00 and VAT 190.00 credit
    assert {r.account: (r.debit, r.credit) for r in january.rows} == {
        "9003": (119000, 0),
        "9007": (0, 100000),
        "9013": (0, 19000),
    }
    assert january.debit_total == january.credit_total == 119000
    on_the_day = trial_balance(case_lines(A1), CHART, _d("2026-02-03"), _d("2026-02-03"))
    assert {r.account for r in on_the_day.rows} == {"9001", "9003"}
    empty = trial_balance(case_lines(A1), CHART, _d("2027-01-01"), None)
    assert empty.rows == () and empty.balance_total == 0


def test_unknown_role_is_reported():
    with pytest.raises(KeyError, match="no_such_role"):
        trial_balance([posted_line("no_such_role", 1, "2026-01-01", 1)], CHART, None, None)


def test_pnl_of_a1_accrual_soll():
    report = pnl(case_lines(A1), CHART, 2026)
    # only the P&L account revenue_s19 carries a balance: 1000.00; no expenses -> profit 1000.00
    assert [(r.account, r.amount) for r in report.income] == [("9007", 100000)]
    assert report.expenses == ()
    assert report.total_income == 100000 and report.total_expense == 0 and report.profit == 100000
    assert pnl(case_lines(A1), CHART, 2025).profit == 0


def test_pnl_with_an_expense_and_the_year_taken_from_income_date():
    meal = case_lines("A6-meal", 3)  # meals_deductible 70.00 + meals_nondeductible 30.00, income date 2026-02-10
    report = pnl(case_lines(A1) + meal, CHART, 2026)
    # profit = 1000.00 - 70.00 - 30.00 = 900.00; the VAT (balance accounts) never enters the P&L
    assert report.total_income == 100000 and report.total_expense == 10000 and report.profit == 90000
    assert {r.account: r.amount for r in report.expenses} == {"9036": -7000, "9037": -3000}
    # a cost whose income date lies in the next year belongs to that year's P&L
    later = [posted_line("expense_other", 5000, "2026-12-30", 9, income_date="2027-01-02")]
    assert pnl(later, CHART, 2026).profit == 0 and pnl(later, CHART, 2027).profit == -5000


def test_balance_sheet_of_a1_accrual_soll():
    lines = case_lines(A1)
    jan = balance_sheet(lines, CHART, _d("2026-01-31"))
    # receivables 1190.00 on the debit side; VAT 190.00 on the credit side; result 1000.00
    assert [(r.account, r.balance) for r in jan.debit_side] == [("9003", 119000)]
    assert [(r.account, r.balance) for r in jan.credit_side] == [("9013", -19000)]
    assert jan.result == 100000
    assert jan.debit_total == 119000 and jan.credit_total == 19000 and jan.difference == 0  # 1190 = 190 + 1000
    feb = balance_sheet(lines, CHART, _d("2026-02-28"))
    # the receivable is paid: bank 1190.00 (receivables 0 is left out)
    assert [(r.account, r.balance) for r in feb.debit_side] == [("9001", 119000)]
    assert feb.debit_total == 119000 and feb.credit_total == 19000 and feb.result == 100000 and feb.difference == 0
    assert balance_sheet(lines, CHART, _d("2025-12-31")).debit_side == ()


def _bank_lines() -> list[PostedLine]:
    lines = case_lines(A1)  # entries 1, 2: bank +1190.00 on 2026-02-03
    lines += case_lines("A14-rc-eu-small-business-eur", 3)  # entry 4 pays 200.50 from the bank on 2026-06-10
    lines += [  # entry 5: VAT payment 38.10 on 2026-06-20
        posted_line("vat_prepayments", 3810, "2026-06-20", 5, income_date="2026-06-20"),
        posted_line("bank", -3810, "2026-06-20", 5),
    ]
    return lines


def test_ledger_of_bank_has_a_running_balance():
    report = ledger(_bank_lines(), CHART, BANK, None, None)
    assert [(r.booking_date.isoformat(), r.entry_no, r.debit, r.credit, r.balance) for r in report.rows] == [
        ("2026-02-03", 2, 119000, 0, 119000),
        ("2026-06-10", 4, 0, 20050, 98950),  # 1190.00 - 200.50
        ("2026-06-20", 5, 0, 3810, 95140),  # 989.50 - 38.10
    ]
    assert report.opening == 0 and report.closing == 95140
    assert all(r.role == "bank" for r in report.rows)


def test_ledger_period_starts_with_the_opening_balance():
    report = ledger(_bank_lines(), CHART, BANK, _d("2026-03-01"), _d("2026-06-15"))
    assert report.opening == 119000  # everything booked before 2026-03-01
    assert [r.balance for r in report.rows] == [98950]
    assert report.closing == 98950
    quiet = ledger(_bank_lines(), CHART, BANK, _d("2026-07-01"), None)
    assert quiet.rows == () and quiet.opening == 95140 and quiet.closing == 95140


def test_ledger_orders_by_booking_date_and_entry_number():
    lines = [
        posted_line("bank", 300, "2026-03-02", 2),
        posted_line("bank", 100, "2026-03-01", 3),
        posted_line("bank", -50, "2026-03-02", 1),
    ]
    report = ledger(lines, CHART, BANK, None, None)
    assert [(r.entry_no, r.balance) for r in report.rows] == [(3, 100), (1, 50), (2, 350)]


def test_ledger_is_per_account_number_not_per_role():
    # cash (9002) and bank (9001) are different accounts
    lines = [posted_line("cash", 70, "2026-01-05", 1), posted_line("bank", 30, "2026-01-05", 1)]
    assert ledger(lines, CHART, AccountNo("9002"), None, None).closing == 70


def test_eur_of_a6_meal_splits_deductible_and_added_back():
    # A6 is an accrual case; under EÜR the payment entry debits input_vat_clearing with the income date (spec 9.4)
    lines = case_lines("A6-meal") + [posted_line("input_vat_clearing", 700, "2026-02-10", 2, income_date="2026-02-10")]
    report = eur_report(lines, CHART, 2026)
    assert report.lines == (EurLineTotal("Line meals_deductible", Cents(-7000)),)  # 100.00 * 70 % = 70.00
    assert report.non_deductible == (EurLineTotal("Line meals_nondeductible", Cents(-3000)),)  # 100.00 - 70.00
    assert report.input_vat_paid == 700  # 7 % of 100.00
    assert report.total_expense == 7700 and report.total_income == 0
    assert report.profit == -7700  # -(70.00 + 7.00); the 30.00 is added back below the profit, not in it
    assert eur_report(lines, CHART, 2025).profit == 0


def test_eur_of_a10_trade_tax_is_added_back_and_income_tax_is_absent():
    report = eur_report(case_lines("A10-taxes-eur"), CHART, 2026)
    # trade tax 250.00 paid 2026-02-15: its own non-deductible line; income tax 800.00 is private_withdrawal
    assert report.non_deductible == (EurLineTotal("Line trade_tax_expense", Cents(-25000)),)
    assert report.lines == () and report.profit == 0
    assert report.total_expense == 0 and report.vat_paid == 0


def test_eur_tax_refunds_are_non_taxable_below_the_profit():
    refund = [posted_line("tax_refunds", -4000, "2026-05-02", 1, income_date="2026-05-02")]
    report = eur_report(refund, CHART, 2026)
    assert report.non_deductible == (EurLineTotal("Line tax_refunds", Cents(4000)),)
    assert report.profit == 0


def test_eur_of_a14_with_the_vat_payment_expense_equals_the_cash_paid():
    vat_payment = [
        posted_line("vat_prepayments", 3810, "2026-06-20", 3, income_date="2026-06-20"),
        posted_line("bank", -3810, "2026-06-20", 3),
    ]
    lines = case_lines("A14-rc-eu-small-business-eur") + vat_payment
    report = eur_report(lines, CHART, 2026)
    # software 200.50 (net, paid 2026-06-10) + VAT paid with the May return 38.10 = 238.60
    assert report.lines == (EurLineTotal("Line expense_software", Cents(-20050)),)
    assert report.vat_paid == 3810 and report.vat_refunded == 0
    assert report.total_expense == 23860 and report.profit == -23860
    # the same sum leaves the bank: 200.50 + 38.10
    bank = next(r for r in trial_balance(lines, CHART, None, None).rows if r.account == BANK)
    assert bank.credit == 23860 and bank.balance == -23860


def test_eur_of_a17_counts_the_business_half_of_the_vat_only():
    # the return nets 38.10 output tax - 19.05 input tax = 19.05 payable, paid on 2026-07-10
    payment = [
        posted_line("vat_prepayments", 1905, "2026-07-10", 3, income_date="2026-07-10"),
        posted_line("bank", -1905, "2026-07-10", 3),
    ]
    report = eur_report(case_lines("A17-rc-eu-private-share-eur") + payment, CHART, 2026)
    # VAT paid 19.05 - private share 9.52 (vat_paid_private) = 9.53; software 200.50 * 50 % = 100.25
    assert report.vat_paid == 953
    assert report.lines == (EurLineTotal("Line expense_software", Cents(-10025)),)
    assert report.total_expense == 10978 and report.profit == -10978  # 100.25 + 9.53 = 109.78
    assert all(t.line != "private_withdrawal" for t in report.lines + report.non_deductible)


def test_eur_vat_refund_is_income_and_special_prepayment_counts_as_vat_paid():
    lines = [
        posted_line("vat_prepayments", -5000, "2026-07-10", 1, income_date="2026-07-10"),  # refund 50.00
        posted_line("bank", 5000, "2026-07-10", 1),
        posted_line("vat_special_prepayment", 8000, "2026-01-10", 2, income_date="2026-01-10"),  # paid 80.00
        posted_line("bank", -8000, "2026-01-10", 2),
        # December: the credit moves to vat_prepayments without income_date and is ignored
        posted_line("vat_prepayments", 8000, "2026-12-10", 3),
        posted_line("vat_special_prepayment", -8000, "2026-12-10", 3),
    ]
    report = eur_report(lines, CHART, 2026)
    assert report.vat_refunded == 5000 and report.vat_paid == 8000
    assert report.total_income == 5000 and report.total_expense == 8000 and report.profit == -3000  # 50.00 - 80.00


def test_eur_gross_form_counts_vat_received_on_eur_soll_and_eur_ist():
    # A1-eur-soll: revenue 1000.00 and VAT 190.00 are income when paid (2026-02-03)
    soll = eur_report(case_lines("A1-eur-soll"), CHART, 2026)
    assert soll.lines == (EurLineTotal("Line revenue_s19", Cents(100000)),)
    assert soll.vat_received == 19000 and soll.total_income == 119000 and soll.profit == 119000
    # A1-eur-ist posts the VAT as vat_out_s19 with the income date
    ist = eur_report(case_lines("A1-eur-ist"), CHART, 2026)
    assert ist.vat_received == 19000 and ist.profit == 119000
    assert eur_report(case_lines("A1-eur-soll"), CHART, 2027).profit == 0
