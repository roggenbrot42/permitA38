# ruff: noqa: F811
"""The Money page from the journal (docs/specs/accounting.md 14, work package O12).

Scenario 1 holds documents the old computation represents correctly: it must equal the journal per month, on every
basis. Scenario 2 holds partial payments and a credit note: the journal figures are the correct ones (hand-computed
below) and the old code shows its documented differences. The expected numbers are literals.
"""

from datetime import date
from decimal import Decimal

import pytest

from permita38.core import calc
from permita38.core.service import finance_journal
from permita38.core.service.credit_notes import CreditNoteService
from permita38.core.service.records import RecordService
from permita38.core.settings import ACCOUNTING_START_KEY

from .test_issue_posting import customer, init, line, repo, sales  # noqa: F401  (fixtures and helpers)
from .test_payments import BOSS, pay  # noqa: F401  (fixtures)

TODAY = date(2026, 12, 31)
D = Decimal
BASES = [("eur", "soll"), ("accrual", "soll"), ("eur", "ist")]


def invoice(repo, sales, ident, day, net, vat, number):
    cust = customer(repo)
    repo.save("invoices", dict(id=ident, customer_id=cust["id"], date=day, number=number, lines=[line(net, vat)]))
    return sales.finalize("invoices", ident)


def expense(repo, **raw):
    RecordService(repo).create(BOSS, "expenses", {"category": "software", "vendor": "Demo Supplier Ltd", **raw})


def old(repo, year=2026):
    s = repo.settings()
    return calc._legacy_figures(repo, year, s, bool(s.get("kleinunternehmer")), s.get("accounting") == "accrual")


def setup(repo, income, vat):
    repo.save_settings({"accounting": income})
    init(repo, income=income, vat=vat)


def by_month(months, key):
    return {m["month"]: D(m[key]) for m in months if D(m[key])}


@pytest.mark.parametrize(("income", "vat"), BASES)
def test_reconciliation_old_equals_journal_for_representable_documents(repo, sales, pay, income, vat):
    setup(repo, income, vat)
    invoice(repo, sales, "inva00a", "2026-02-10", "1000", "19", "R-A")
    pay.record("inva00a", date="2026-02-20", amount=D("1190.00"), user=BOSS)
    invoice(repo, sales, "inva00b", "2026-03-05", "500", "7", "R-B")
    pay.record("inva00b", date="2026-03-25", amount=D("535.00"), user=BOSS)
    expense(repo, id="exp001", date="2026-03-10", net=100.0, vat=19.0, vat_rate=19.0)
    expense(repo, id="exp002", date="2026-05-02", net=50.0, vat=0.0, vat_rate=0.0, category="other")
    got, cats, receivables = finance_journal.figures(repo, 2026)
    want, want_cats, want_receivables = old(repo)
    for key in ("revenue", "vat_out", "expenses", "vat_in"):
        assert by_month(got, key) == by_month(want, key), key
    assert by_month(got, "revenue") == {2: D("1000.00"), 3: D("500.00")}
    assert by_month(got, "vat_out") == {2: D("190.00"), 3: D("35.00")}
    assert by_month(got, "expenses") == {3: D("100.00"), 5: D("50.00")}
    assert by_month(got, "vat_in") == {3: D("19.00")}
    # the chart has no category "other": the journal books it on expense_other, shown as "Other" (the old default)
    assert cats == {"software": D("100.00"), "Other": D("50.00")}
    assert want_cats == {"software": D("100.00"), "other": D("50.00")}
    assert receivables == want_receivables == 0


def test_partial_payment_and_credit_note_journal_is_correct_old_code_differs(repo, sales, pay):
    """EÜR / Ist. Invoice C: net 1,000.00 @ 19 % (gross 1,190.00) of 2026-04-01; payment 600.00 on 04-20;
    credit note (partial, net 100.00 @ 19 % = gross 119.00) on 05-10; invoice D of 2026-06-01, net 200.00 @ 19 %.

    Hand computation of the journal (EÜR/Ist: a part payment is split pro rata, VAT on the cash date):
      April: 600.00 / 1.19 = 504.2016 -> revenue 504.20, VAT 95.80 (504.20 + 95.80 = 600.00).
      May: the credit note lowers the invoice to 1,071.00; 600.00 of it is paid, 471.00 open, so no cash is
           returned and nothing is netted: revenue 0, VAT 0 in May.
      Open receivables (EÜR: from the documents) = 1,071.00 - 600.00 + 238.00 (invoice D, unpaid) = 709.00.
    The old code shows revenue 0 in April (the invoice is not fully paid), nets the credit note in May
    (-100.00 / -19.00) although nothing was refunded, and owes 1,190.00 - 119.00 + 238.00 = 1,309.00 (it ignores the
    part payment)."""
    setup(repo, "eur", "ist")
    invoice(repo, sales, "invc00c", "2026-04-01", "1000", "19", "R-C")
    pay.record("invc00c", date="2026-04-20", amount=D("600.00"), user=BOSS)
    CreditNoteService(repo, sales.postings.journal, pay).create(
        "invc00c", credit_kind="partial", date="2026-05-10", reason="x", amounts={"19": "100.00"}, user=BOSS
    )
    invoice(repo, sales, "invd00d", "2026-06-01", "200", "19", "R-D")
    got, _cats, receivables = finance_journal.figures(repo, 2026)
    assert by_month(got, "revenue") == {4: D("504.20")}
    assert by_month(got, "vat_out") == {4: D("95.80")}
    assert receivables == D("709.00")
    want, _c, want_receivables = old(repo)
    assert by_month(want, "revenue") == {5: D("-100.00")}
    assert by_month(want, "vat_out") == {5: D("-19.00")}
    assert want_receivables == D("1309.00")


def test_finance_switch_and_approximate_flag(repo, sales, pay):
    repo.save_settings({"accounting": "eur"})
    invoice(repo, sales, "inva00a", "2026-02-10", "1000", "19", "R-A")
    before = calc.finance(repo, 2026, today=TODAY)
    assert before["approximate"] is True
    assert finance_journal.figures(repo, 2026) is None
    init(repo, income="eur", vat="soll")
    pay.record("inva00a", date="2026-02-20", amount=D("1190.00"), user=BOSS)
    after = calc.finance(repo, 2026, today=TODAY)
    assert after["approximate"] is False and after["months"][1]["revenue"] == 1000.0
    assert set(after) == set(before)  # the response shape is unchanged
    assert finance_journal.figures(repo, 2025) is None  # no fiscal year row for 2025


def test_year_that_began_before_the_accounting_start_stays_approximate(repo):
    repo.save_settings({"accounting": "eur"})
    init(repo, income="eur", vat="soll", start="2026-07-01")
    assert repo.settings()[ACCOUNTING_START_KEY] == "2026-07-01"
    assert calc.finance(repo, 2026, today=TODAY)["approximate"] is True


def test_accrual_year_unpaid_invoice_is_an_open_receivable_end_to_end(repo, sales):
    """Accrual: net 1,000.00 @ 19 % issued 2026-03-01, unpaid -> receivable 1,190.00 through ``calc.finance``."""
    setup(repo, "accrual", "soll")
    invoice(repo, sales, "inva00a", "2026-03-01", "1000", "19", "R-A")
    got = finance_journal.figures(repo, 2026)
    assert got is not None and isinstance(got[2], D) and got[2] == D("1190.00")
    out = calc.finance(repo, 2026, today=TODAY)
    assert out["approximate"] is False and out["open_receivables"] == 1190.0


def test_documents_receivable_counts_only_positive_open_amounts(repo, sales, pay):
    """A paid invoice adds nothing; the unpaid one (gross 119.00) adds 119.00."""
    repo.save_settings({"accounting": "eur"})
    invoice(repo, sales, "inva00a", "2026-02-10", "100", "19", "R-A")
    invoice(repo, sales, "inva00b", "2026-02-11", "100", "19", "R-B")
    pay.record("inva00b", date="2026-02-20", amount=D("119.00"), user=BOSS)
    assert calc.open_receivables_of_documents(repo) == D("119.00")


def test_13b_tax_is_output_and_input_vat_with_the_deductible_share(repo):
    """Par. 13b EU service, net 1,000.00, 19 % -> tax 190.00 (vat_out); at 50 % share input VAT 95.00. A VST expense
    of net 100.00 adds 19.00 input VAT: vat_in 114.00, vat_due = 190.00 - 114.00 = 76.00 (= Kz 83 minus the VST)."""
    setup(repo, "accrual", "soll")
    expense(repo, id="exprc1", date="2026-05-20", service_date="2026-05-20", net=1000.0, vat=0.0, vat_rate=19.0,
            tax_treatment="reverse_charge_eu_service", vat_deductible=50)  # fmt: skip
    expense(repo, id="expvst", date="2026-05-25", net=100.0, vat=19.0, vat_rate=19.0)
    months, _, _ = finance_journal.figures(repo, 2026)
    assert by_month(months, "vat_out") == {5: D("190.00")}
    assert by_month(months, "vat_in") == {5: D("114.00")}
    out = calc.finance(repo, 2026, today=TODAY)
    assert out["months"][4]["vat_due"] == 76.0 and out["totals"]["vat_due"] == 76.0


def test_small_business_keeps_its_13b_tax(repo):
    """A small business owes the par. 13b tax (no input VAT): net 1,000.00 at 19 % -> vat_due 190.00."""
    repo.save_settings({"accounting": "accrual", "kleinunternehmer": True})
    init(repo, income="accrual", vat="soll", small_business=True)
    expense(repo, id="exprc1", date="2026-05-20", service_date="2026-05-20", net=1000.0, vat=0.0, vat_rate=19.0,
            tax_treatment="reverse_charge_eu_service")  # fmt: skip
    out = calc.finance(repo, 2026, today=TODAY)
    assert out["totals"]["vat_due"] == 190.0 and out["approximate"] is False
