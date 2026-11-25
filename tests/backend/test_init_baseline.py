# ruff: noqa: F811
"""Pre-start baseline and opening items (docs/specs/accounting.md 16 steps 4, 5, 5a), work package E23.

The expected numbers are computed by hand (an invoice of 1190.00 = 1000.00 + 19 % issued in December 2025 with 500.00
paid in December and 690.00 paid in February; the VAT share of the 500.00 is 190 x 500 / 1190 = 79.83, so 110.17 of
the VAT is still open at the start). Live-versus-backfill equality does not apply to pre-start items (the live path
has no pre-start events), so the trial balance after the initialisation is compared with the hand figures.
"""

from collections import defaultdict
from decimal import Decimal

import pytest

from permita38.core import models
from permita38.core.service.accounting_init import AccountingInitService
from permita38.core.service.credit_notes import CreditNoteService
from permita38.core.service.journal import JournalService
from permita38.core.service.opening_items import OpeningItemService

from .test_expense_postings import rs  # noqa: F401  (fixture)
from .test_invoice_freeze import cust, issue, store  # noqa: F401  (fixtures)
from .test_issue_posting import customer, line, lock_through, repo, sales  # noqa: F401  (fixtures and helpers)
from .test_payments import BOSS, pay  # noqa: F401  (fixtures)

BASES = {
    "accrual_soll": ("accrual", "soll"),
    "accrual_ist": ("accrual", "ist"),
    "eur_soll": ("eur", "soll"),
    "eur_ist": ("eur", "ist"),
}


def params(income: str, vat: str) -> models.AccountingInitIn:
    return models.AccountingInitIn(chart="skr03", income_basis=income, vat_basis=vat, accounting_start="2026-01-01")


def cents(value) -> int:
    return int(Decimal(str(value)) * 100)


def all_entries(repo) -> list:
    out: list = []
    for year in (2025, 2026, 2027):
        out += JournalService(repo).entries(year, limit=500)
    return out


def trial(repo) -> dict[str, int]:
    """Role -> signed cents over the whole journal (zero balances dropped)."""
    sums: dict[str, int] = defaultdict(int)
    for e in all_entries(repo):
        for ln in e.lines:
            sums[str(ln.role)] += cents(ln.amount)
    return {k: v for k, v in sums.items() if v}


def entry(repo, key: str):
    return JournalService(repo).get_by_event_key(key)


def rows(e) -> list[tuple[str, int]]:
    return [(str(ln.role), cents(ln.amount)) for ln in e.lines]


def prestart_invoice(repo, ident="oldinv01", day="2025-12-10", number="R-OLD"):
    cu = customer(repo)
    repo.save(
        "invoices",
        dict(id=ident, customer_id=cu["id"], date=day, number=number, status="sent", lines=[line("1000", "19")]),
    )


def pay_on(pay, amount="500.00", day="2025-12-20", ident="oldinv01"):
    return pay.record(ident, date=day, amount=Decimal(amount), user=BOSS)


# per basis: the baseline entry (None = no entry) and the trial balance after the February payment of 690.00
EXPECTED = {
    "accrual_soll": ([("receivables", 69000), ("opening_balance", -69000)], {"bank": 69000, "opening_balance": -69000}),
    "accrual_ist": (
        [("receivables", 69000), ("vat_not_due_s19", -11017), ("opening_balance", -57983)],
        {"bank": 69000, "vat_out_s19": -11017, "opening_balance": -57983},
    ),
    "eur_soll": (
        [("vat_soll_clearing_s19", 11017), ("opening_balance", -11017)],
        {"bank": 69000, "revenue_s19": -57983, "opening_balance": -11017},
    ),
    "eur_ist": (None, {"bank": 69000, "revenue_s19": -57983, "vat_out_s19": -11017}),
}


@pytest.mark.parametrize("mode", ["backfill", "live"])
@pytest.mark.parametrize("basis", list(BASES))
def test_open_invoice_baseline_then_february_payment(repo, sales, pay, basis, mode):
    income, vat = BASES[basis]
    prestart_invoice(repo)
    pay_on(pay)
    if mode == "backfill":
        pay_on(pay, "690.00", "2026-02-10")
    result = AccountingInitService(repo).run(params(income, vat), BOSS)  # no longer refused
    baseline, final = EXPECTED[basis]
    e = entry(repo, "open:2026:invoice:oldinv01")
    if baseline is None:
        assert e is None and "opening_invoice" not in result.counts
    else:
        assert e is not None and result.counts["opening_invoice"] == 1
        assert rows(e) == baseline
        assert str(e.document_date) == "2026-01-01" and e.origin == "opening"
        assert all(ln.vat_date is None and ln.income_date is None for ln in e.lines)
    if mode == "live":
        pay_on(pay, "690.00", "2026-02-10")
    assert trial(repo) == final  # receivables, vat_not_due and vat_soll_clearing all reach 0
    assert JournalService(repo).verify_chain(2026) == []


def test_open_amounts_at_the_start_and_the_february_vat_line(repo, sales, pay):
    prestart_invoice(repo)
    pay_on(pay)
    AccountingInitService(repo).run(params("accrual", "ist"), BOSS)
    assert trial(repo) == {"receivables": 69000, "vat_not_due_s19": -11017, "opening_balance": -57983}
    pay_on(pay, "690.00", "2026-02-10")
    e = next(x for x in all_entries(repo) if x.event_key.startswith("payment:"))
    assert sorted(r for r in rows(e) if r[0].startswith("vat")) == [("vat_not_due_s19", 11017), ("vat_out_s19", -11017)]
    vat_out = next(ln for ln in e.lines if ln.role == "vat_out_s19")
    assert (vat_out.tax_key, cents(vat_out.tax_base), str(vat_out.vat_date)) == ("S19", 57983, "2026-02-10")


def test_settled_pre_start_invoice_posts_nothing_at_init(repo, sales, pay):
    prestart_invoice(repo)
    pay_on(pay, "1190.00")
    result = AccountingInitService(repo).run(params("accrual", "soll"), BOSS)
    assert all_entries(repo) == [] and "opening_invoice" not in result.counts


# ---------------------------------------------------------------- expenses
EXPENSE = {
    "id": "oldexp01",
    "date": "2025-12-10",
    "paid_on": "2026-02-10",
    "net": 100.0,
    "vat": 19.0,
    "vat_rate": 19.0,
    "category": "software",
    "vendor": "Demo Supplier Ltd",
}


def test_expense_dated_in_december_paid_in_february_accrual(repo):
    repo.save("expenses", dict(EXPENSE))
    result = AccountingInitService(repo).run(params("accrual", "soll"), BOSS)
    e = entry(repo, "open:2026:expense:oldexp01")
    assert rows(e) == [("payables", -11900), ("opening_balance", 11900)]
    assert all(ln.vat_date is None and ln.income_date is None for ln in e.lines)
    pay_entry = entry(repo, "expense:oldexp01:pay:r1")
    assert str(pay_entry.document_date) == "2026-02-10"
    assert rows(pay_entry) == [("payables", 11900), ("bank", -11900)]
    assert entry(repo, "expense:oldexp01:bill:r1") is None  # the bill belongs to the earlier year
    assert result.counts["opening_expense"] == 1 and result.counts["expense_payment"] == 1
    assert trial(repo) == {"bank": -11900, "opening_balance": 11900}  # the payable is paid: 0


def test_expense_dated_in_december_paid_in_february_eur(repo):
    repo.save("expenses", dict(EXPENSE))
    AccountingInitService(repo).run(params("eur", "soll"), BOSS)
    e = entry(repo, "open:2026:expense:oldexp01")
    assert rows(e) == [("input_vat_clearing", -1900), ("opening_balance", 1900)]
    pay_entry = entry(repo, "expense:oldexp01:pay:r1")
    assert sorted(rows(pay_entry)) == sorted(
        [("expense_software", 10000), ("input_vat_clearing", 1900), ("bank", -11900)]
    )
    cost = next(ln for ln in pay_entry.lines if ln.role == "expense_software")
    assert str(cost.income_date) == "2026-02-10"
    assert trial(repo) == {"expense_software": 10000, "bank": -11900, "opening_balance": 1900}  # clearing: 0


def test_expense_paid_before_the_start_posts_no_opening_entry(repo):
    repo.save("expenses", {**EXPENSE, "id": "oldexp02", "paid_on": "2025-12-20"})
    AccountingInitService(repo).run(params("accrual", "soll"), BOSS)
    assert all_entries(repo) == []


# ---------------------------------------------------------------- the lazy baseline (step 5a)
def test_credit_note_on_a_fully_paid_pre_start_invoice(repo, sales, pay):
    prestart_invoice(repo)
    pay_on(pay, "1190.00")
    AccountingInitService(repo).run(params("accrual", "soll"), BOSS)
    assert all_entries(repo) == []
    cn = CreditNoteService(repo, sales.postings.journal, pay)
    cn.create("oldinv01", credit_kind="storno", date="2026-02-10", reason="Demo", user=BOSS)
    assert entry(repo, "open:2026:invoice:oldinv01") is None  # a settled invoice has no balance-sheet baseline
    assert trial(repo) == {"revenue_s19": 100000, "vat_not_due_s19": 19000, "receivables": -119000}


def test_refund_after_a_pre_start_credit_posts_the_baseline_lazily(repo, sales, pay):
    prestart_invoice(repo)
    pay_on(pay, "1190.00")
    cn = CreditNoteService(repo, sales.postings.journal, pay)
    cn.create("oldinv01", credit_kind="partial", date="2025-12-28", reason="Demo", amounts={"19": "100.00"}, user=BOSS)
    AccountingInitService(repo).run(params("accrual", "soll"), BOSS)
    assert all_entries(repo) == []  # not open at the start: no baseline yet
    lock_through(repo, 1)  # the start month is locked: the opening entry keeps the start date, books on 1 February
    pay.record("oldinv01", date="2026-02-10", amount=Decimal("119.00"), kind="refund", user=BOSS)
    e = entry(repo, "open:2026:invoice:oldinv01")
    assert rows(e) == [("receivables", -11900), ("vat_not_due_s19", 1900), ("opening_balance", 10000)]
    assert (str(e.document_date), str(e.booking_date)) == ("2026-01-01", "2026-02-01")
    assert trial(repo) == {"vat_out_s19": 1900, "bank": -11900, "opening_balance": 10000}
    assert JournalService(repo).verify_chain(2026) == []


# ---------------------------------------------------------------- opening adjustments (step 5a) and unpaid expenses
def test_prestart_payment_entered_after_init_posts_an_opening_adjustment(repo, sales, pay):
    """Accrual/Ist, 1190.00 invoice (190.00 VAT): 500.00 dated before the start entered after the initialisation
    moves the baseline by receivables -500.00 and vat_not_due +79.83 (190 x 500 / 1190), against opening_balance."""
    prestart_invoice(repo)
    AccountingInitService(repo).run(params("accrual", "ist"), BOSS)
    assert rows(entry(repo, "open:2026:invoice:oldinv01")) == [
        ("receivables", 119000), ("vat_not_due_s19", -19000), ("opening_balance", -100000)
    ]  # fmt: skip
    pay_on(pay)  # 500.00 on 2025-12-20, after the initialisation
    adj = entry(repo, "open:2026:invoice:oldinv01:r1")
    assert rows(adj) == [("receivables", -50000), ("vat_not_due_s19", 7983), ("opening_balance", 42017)]
    assert str(adj.document_date) == "2026-01-01" and adj.origin == "opening"
    assert all(ln.vat_date is None and ln.income_date is None for ln in adj.lines)
    assert not any(e.event_key.startswith("payment:") for e in all_entries(repo))  # never a normal event
    pay_on(pay, "690.00", "2026-02-10")
    assert trial(repo) == EXPECTED["accrual_ist"][1]  # receivables and vat_not_due end at 0
    assert JournalService(repo).verify_chain(2026) == []


def test_ensure_invoice_baseline_twice_posts_one_entry(repo, sales, pay):
    prestart_invoice(repo)
    AccountingInitService(repo).run(params("accrual", "soll"), BOSS)
    svc = OpeningItemService(repo)
    inv = repo.get("invoices", "oldinv01")
    first, second = svc.ensure_invoice_baseline(inv, BOSS), svc.ensure_invoice_baseline(inv, BOSS)
    assert first is not None and second is not None and first.id == second.id
    assert [e.event_key for e in all_entries(repo)] == ["open:2026:invoice:oldinv01"]


def test_credit_note_after_init_on_an_open_pre_start_invoice(repo, sales, pay):
    prestart_invoice(repo)
    AccountingInitService(repo).run(params("accrual", "soll"), BOSS)
    CreditNoteService(repo, sales.postings.journal, pay).create(
        "oldinv01", credit_kind="storno", date="2026-02-10", reason="Demo", user=BOSS
    )
    assert trial(repo) == {"revenue_s19": 100000, "vat_out_s19": 19000, "opening_balance": -119000}  # receivables 0
    assert [e.event_key for e in all_entries(repo)][0] == "open:2026:invoice:oldinv01"


UNPAID = {k: v for k, v in EXPENSE.items() if k != "paid_on"}


def update_expense(rs, **fields):
    body = {k: v for k, v in {**UNPAID, **fields}.items() if k != "id"}
    return rs.update(BOSS, "expenses", "oldexp01", body)


@pytest.mark.parametrize(
    ("income", "vat", "opening", "final"),
    [
        ("accrual", "soll", [("payables", -11900), ("opening_balance", 11900)], {"bank": -11900, "opening_balance": 11900}),
        (
            "eur", "soll", [("input_vat_clearing", -1900), ("opening_balance", 1900)],
            {"expense_software": 10000, "bank": -11900, "opening_balance": 1900},
        ),
    ],
)  # fmt: skip
def test_unpaid_prestart_expense_gets_an_opening_item_cleared_by_the_later_payment(
    repo, rs, income, vat, opening, final
):
    repo.save("expenses", dict(UNPAID))
    result = AccountingInitService(repo).run(params(income, vat), BOSS)
    assert rows(entry(repo, "open:2026:expense:oldexp01")) == opening and result.counts["opening_expense"] == 1
    assert entry(repo, "expense:oldexp01:pay:r1") is None
    update_expense(rs, paid_on="2026-02-10")  # the live hook posts the payment
    assert trial(repo) == final  # payables / input_vat_clearing are cleared


def test_prestart_expense_paid_on_changed_after_init_posts_an_opening_adjustment(repo, rs):
    repo.save("expenses", dict(EXPENSE))  # paid on 2026-02-10
    AccountingInitService(repo).run(params("accrual", "soll"), BOSS)
    assert trial(repo) == {"bank": -11900, "opening_balance": 11900}
    update_expense(rs, paid_on="2025-12-20")  # now paid before the start: the payable is no longer open
    adj = entry(repo, "open:2026:expense:oldexp01:r1")
    assert rows(adj) == [("payables", 11900), ("opening_balance", -11900)] and adj.origin == "opening"
    assert entry(repo, "expense:oldexp01:pay:r1:rev") is not None  # the February payment is reversed
    assert trial(repo) == {}  # payables, bank and opening_balance all 0
    update_expense(rs, paid_on="2026-03-10")  # and back to open
    assert rows(entry(repo, "open:2026:expense:oldexp01:r2")) == [("payables", -11900), ("opening_balance", 11900)]
    assert trial(repo) == {"bank": -11900, "opening_balance": 11900}
