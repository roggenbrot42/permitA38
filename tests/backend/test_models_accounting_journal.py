"""Models of work package E3b: fiscal years, journal, manual entries, locks, VAT returns, ZM, initialisation
(docs/specs/accounting.md 4, 7a, 8.1, 9.8, 11, 16; ustva.md; zm.md). Sign convention: debit positive, credit negative."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from permita38.core.models import (
    AccountingInitIn,
    EntryReverseIn,
    FiscalYear,
    FiscalYearIn,
    JournalEntry,
    JournalLine,
    ManualEntryIn,
    PeriodLockIn,
    SpecialPrepaymentIn,
    VatReturn,
    VatReturnCreateIn,
    VatReturnFiledIn,
    VatReturnPaymentIn,
    ZmCreateIn,
    ZmFiledIn,
    ZmReport,
)


def bad(model, **payload):
    with pytest.raises(ValidationError):
        model(**payload)


# ---------------------------------------------------------------- fiscal year

FY = {"year": 2026, "chart": "skr03", "income_basis": "eur"}


def test_fiscal_year_defaults_soll_month():
    f = FiscalYearIn(**FY)
    assert (f.vat_basis, f.vat_frequency, f.dauerfrist, f.small_business) == ("soll", "month", False, False)


@pytest.mark.parametrize("freq", ["month", "quarter", "none"])
def test_fiscal_year_frequencies(freq):
    assert FiscalYearIn(**FY, vat_frequency=freq).vat_frequency == freq


@pytest.mark.parametrize(
    "patch",
    [
        {"year": 1999},
        {"year": 2101},
        {"year": "2026"},
        {"chart": "skr05"},
        {"income_basis": "cash"},
        {"vat_basis": "plan"},
        {"vat_frequency": "year"},
        {"dauerfrist": "yes"},
        {"vat_regular_from": "2026-06-01"},  # without small_business
        {"small_business": True, "vat_regular_from": "2027-01-01"},  # other year
        {"small_business": True, "vat_regular_from": "2026-13-01"},
    ],
)
def test_fiscal_year_refuses(patch):
    bad(FiscalYearIn, **{**FY, **patch})


def test_fiscal_year_regular_from_ok_and_unknown_field_kept():
    f = FiscalYearIn(**FY, small_business=True, vat_regular_from="2026-06-01")
    assert f.vat_regular_from == "2026-06-01"


def test_fiscal_year_response_round_trips_unknown():
    row = {"id": "a", "year": 2026, "chart": "skr04", "closed_at": None, "novel": [1]}
    assert FiscalYear(**row).model_dump()["novel"] == [1]


# ---------------------------------------------------------------- manual entry

D = {"account": "1200", "amount": "119.00"}
C1 = {"account": "8400", "amount": "-100.00"}
C2 = {
    "account": "1776",
    "amount": "-19.00",
    "tax_key": "S19",
    "tax_kind": "tax",
    "tax_base": "100.00",
    "vat_date": "2026-03-05",
}
ENTRY = {"date": "2026-03-05", "text": "Hand booking", "lines": [D, C1, C2]}


@pytest.mark.parametrize("as_number", [False, True])
def test_manual_entry_accepts_string_and_number(as_number):
    lines = [
        {"account": "1200", "amount": 119 if as_number else "119"},
        {"role": "bank", "amount": -119.0 if as_number else "-119.00"},
    ]
    e = ManualEntryIn(date="2026-03-05", text="x", lines=lines)
    assert [line.amount for line in e.lines] == [Decimal("119.00"), Decimal("-119.00")]
    assert all(isinstance(line.amount, Decimal) for line in e.lines)


def test_manual_entry_full_valid():
    e = ManualEntryIn(**ENTRY)
    assert e.lines[2].tax_base == Decimal("100.00") and e.lines[2].tax_key == "S19"


def test_manual_entry_float_sum_is_exact():
    lines = [
        {"account": "1200", "amount": 0.1},
        {"account": "1200", "amount": 0.2},
        {"account": "8400", "amount": -0.3},
    ]
    assert ManualEntryIn(date="2026-03-05", text="x", lines=lines)  # 0.1 + 0.2 - 0.3 == 0 in decimals


@pytest.mark.parametrize(
    "patch",
    [
        {"lines": [D]},  # single line
        {"lines": []},
        {"lines": [D, C1]},  # 119 vs -100: unbalanced
        {"text": ""},
        {"text": "   "},
        {"text": "x" * 201},
        {"date": "2026-02-30"},
        {"lines": [D] * 201},
    ],
)
def test_manual_entry_refuses(patch):
    bad(ManualEntryIn, **{**ENTRY, **patch})


def test_manual_entry_missing_fields():
    bad(ManualEntryIn, date="2026-03-05", lines=ENTRY["lines"])
    bad(ManualEntryIn, text="x", lines=ENTRY["lines"])


def _entry_with(line):
    # a balancing counter line keeps the sum at 0 so only the line rule can fail
    amount = Decimal(str(line["amount"]))
    return {"date": "2026-03-05", "text": "x", "lines": [line, {"account": "1200", "amount": str(-amount)}]}


TAXED = {"account": "1776", "amount": "-19.00"}


@pytest.mark.parametrize(
    "extra",
    [
        {"tax_key": "S19", "tax_kind": "tax", "tax_base": "100.00"},  # no vat_date
        {"tax_key": "S19", "tax_kind": "tax", "vat_date": "2026-03-05"},  # no tax_base
        {"tax_key": "EU_SVC", "tax_kind": "base_only", "vat_date": "2026-03-05"},  # no tax_base
        {"tax_key": "S19", "vat_date": "2026-03-05", "tax_base": "100.00"},  # no tax_kind
        {"tax_kind": "tax"},  # kind without key
        {"tax_base": "1.00"},  # base without key
        {"vat_date": "2026-03-05"},  # date without key
        {"tax_key": "s19", "tax_kind": "tax", "vat_date": "2026-03-05", "tax_base": "1"},  # bad key
        {"tax_key": "S19", "tax_kind": "vat", "vat_date": "2026-03-05", "tax_base": "1"},
        {"account": None},  # neither account nor role
        {"role": "bank"},  # both
        {"account": "12 00"},
        {"account": "1" * 21},
        {"amount": "1.005"},
        {"amount": "1e3"},
        {"amount": True},
        {"amount": "1000000001"},
    ],
)
def test_manual_line_refuses(extra):
    line = {**TAXED, **extra}
    if line.get("account") is None:
        line.pop("account")
    bad_entry = {"date": "2026-03-05", "text": "x", "lines": [line, {"account": "1200", "amount": "19.00"}]}
    with pytest.raises(ValidationError):
        ManualEntryIn(**bad_entry)


def test_manual_line_input_vat_needs_no_base():
    line = {"account": "1576", "amount": "19.00", "tax_key": "VST", "tax_kind": "input", "vat_date": "2026-03-05"}
    e = ManualEntryIn(**_entry_with(line))
    assert e.lines[0].tax_base is None


def test_manual_line_tax_key_none_is_plain():
    line = {"account": "8000", "amount": "-5.00", "tax_key": "NONE"}
    assert ManualEntryIn(**_entry_with(line))


def test_manual_line_zero_amount_only_base_only():
    stat = {
        "account": "8336",
        "amount": 0,
        "tax_key": "EU_SVC",
        "tax_kind": "base_only",
        "tax_base": "500.00",
        "vat_date": "2026-03-05",
    }
    pair = [{"account": "1200", "amount": "5.00"}, {"account": "8400", "amount": "-5.00"}]
    assert ManualEntryIn(date="2026-03-05", text="x", lines=[stat, *pair])  # a statistical line balances trivially
    bad(ManualEntryIn, date="2026-03-05", text="x", lines=[{"account": "1200", "amount": "0"}, *pair])


def test_manual_line_unknown_field_is_kept_not_fatal():
    e = ManualEntryIn(**{**ENTRY, "lines": [{**D, "future": 1}, C1, C2]})
    assert e.lines[0].model_dump()["future"] == 1


# ---------------------------------------------------------------- reverse, lock


def test_reverse_in():
    assert EntryReverseIn().date is None
    assert EntryReverseIn(date="2026-04-01", text="oops").text == "oops"
    assert EntryReverseIn(date="").date is None
    bad(EntryReverseIn, date="2026-02-30")
    bad(EntryReverseIn, text="x" * 201)


@pytest.mark.parametrize("year,month", [(2026, 1), (2026, 12), (2000, 6)])
def test_lock_in_ok(year, month):
    assert PeriodLockIn(year=year, month=month).month == month


@pytest.mark.parametrize("patch", [{"month": 0}, {"month": 13}, {"year": 1999}, {"month": "3"}, {"month": 2.5}])
def test_lock_in_refuses(patch):
    bad(PeriodLockIn, **{"year": 2026, "month": 3, **patch})


# ---------------------------------------------------------------- VAT return in-models


@pytest.mark.parametrize("kind,no", [("month", 1), ("month", 12), ("quarter", 1), ("quarter", 4)])
def test_vat_create_ok(kind, no):
    assert VatReturnCreateIn(year=2026, kind=kind, no=no).amends_id is None


@pytest.mark.parametrize(
    "kind,no",
    [("month", 0), ("month", 13), ("quarter", 0), ("quarter", 5), ("year", 1), ("month", "1"), ("month", None)],
)
def test_vat_create_refuses(kind, no):
    bad(VatReturnCreateIn, year=2026, kind=kind, no=no)


def test_vat_create_amends_id_and_year():
    assert VatReturnCreateIn(year=2026, kind="month", no=3, amends_id="ab12cd").amends_id == "ab12cd"
    bad(VatReturnCreateIn, year=2026, kind="month", no=3, amends_id="bad id!")
    bad(VatReturnCreateIn, year=1990, kind="month", no=3)


def test_filed_in():
    f = VatReturnFiledIn(filed_on="2026-04-10")
    assert (f.reference, f.note) == (None, None)
    assert len(VatReturnFiledIn(filed_on="2026-04-10", reference="r" * 100, note="n" * 500).note) == 500
    bad(VatReturnFiledIn, filed_on="2026-04-10", reference="r" * 101)
    bad(VatReturnFiledIn, filed_on="2026-04-10", note="n" * 501)
    bad(VatReturnFiledIn, filed_on="nope")
    bad(VatReturnFiledIn)
    assert ZmFiledIn(filed_on="2026-04-10", reference="x").reference == "x"
    bad(ZmFiledIn, filed_on="2026-04-10", reference="r" * 101)


@pytest.mark.parametrize("amount", ["76.00", 76, 76.0, "0.01", "76"])
@pytest.mark.parametrize("kind", ["payment", "refund"])
def test_payment_in_ok(amount, kind):
    p = VatReturnPaymentIn(date="2026-04-10", amount=amount, kind=kind)
    assert isinstance(p.amount, Decimal) and p.amount > 0


@pytest.mark.parametrize("amount", [0, "0.00", -1, "1.005", "abc", True, None, "1000000001"])
def test_payment_in_refuses_amount(amount):
    bad(VatReturnPaymentIn, date="2026-04-10", amount=amount, kind="payment")


def test_payment_in_refuses_kind_and_date():
    bad(VatReturnPaymentIn, date="2026-04-10", amount="1.00", kind="reversal")
    bad(VatReturnPaymentIn, date="2026-04-10", amount="1.00")
    bad(VatReturnPaymentIn, date="x", amount="1.00", kind="payment")


@pytest.mark.parametrize("amount", ["0.00", 0, "1000.50", 1000.5])
def test_special_prepayment_ok(amount):
    s = SpecialPrepaymentIn(year=2026, amount=amount)
    assert isinstance(s.amount, Decimal) and s.paid_on is None
    assert SpecialPrepaymentIn(year=2026, amount=1, paid_on="2026-02-10").paid_on == "2026-02-10"


@pytest.mark.parametrize("amount", ["-0.01", -1, "1.001", True, "x", "1000000001"])
def test_special_prepayment_refuses(amount):
    bad(SpecialPrepaymentIn, year=2026, amount=amount)


def test_special_prepayment_refuses_year_and_paid_on():
    bad(SpecialPrepaymentIn, year=1999, amount=1)
    bad(SpecialPrepaymentIn, year=2026, amount=1, paid_on="2026-13-01")


# ---------------------------------------------------------------- ZM in-model


@pytest.mark.parametrize("q", [1, 4])
def test_zm_create_ok(q):
    assert ZmCreateIn(year=2026, quarter=q).amends_id is None


@pytest.mark.parametrize("q", [0, 5, "1", None])
def test_zm_create_refuses(q):
    bad(ZmCreateIn, year=2026, quarter=q)


# ---------------------------------------------------------------- init

INIT = {"chart": "skr04", "income_basis": "accrual"}


def test_init_defaults_and_full():
    i = AccountingInitIn(**INIT)
    assert (i.vat_basis, i.frequency, i.accounting_start, i.opening_balances, i.review_resolutions) == (
        "soll",
        "month",
        None,
        [],
        [],
    )
    full = AccountingInitIn(
        **INIT,
        vat_basis="ist",
        frequency="quarter",
        dauerfrist=True,
        small_business=False,
        accounting_start="2026-01-01",
        opening_balances=[{"role": "bank", "amount": "1000.00"}, {"account": "0800", "amount": -1000}],
        review_resolutions=[
            {"key": "expense:x", "action": "treatment", "treatment": "standard"},
            {"key": "y", "action": "confirm"},
        ],
    )
    assert full.opening_balances[1].amount == Decimal("-1000.00")
    assert full.review_resolutions[0].treatment == "standard"


@pytest.mark.parametrize(
    "patch",
    [
        {"chart": "x"},
        {"income_basis": "x"},
        {"vat_basis": "x"},
        {"frequency": "year"},
        {"accounting_start": "2026-02-30"},
        {"opening_balances": [{"amount": "1.00"}]},  # no account/role
        {"opening_balances": [{"account": "1200", "role": "bank", "amount": "1.00"}]},
        {"opening_balances": [{"account": "1200", "amount": "1.001"}]},
        {"opening_balances": [{"account": "1200", "amount": "1.00"}] * 201},
        {"review_resolutions": [{"key": "", "action": "confirm"}]},
        {"review_resolutions": [{"key": "k", "action": "guess"}]},
        {"review_resolutions": [{"key": "k", "action": "treatment"}]},  # treatment missing
        {"review_resolutions": [{"key": "k", "action": "confirm", "treatment": "standard"}]},
        {"review_resolutions": [{"key": "k" * 201, "action": "confirm"}]},
        {"review_resolutions": [{"key": "k", "action": "confirm"}] * 5001},
    ],
)
def test_init_refuses(patch):
    bad(AccountingInitIn, **{**INIT, **patch})


# ---------------------------------------------------------------- responses: money as decimal strings


def test_journal_line_money_from_cents_serialises_as_strings():
    line = JournalLine(id="l1", account="1776", amount_cents=-1900, tax_base_cents=10000, tax_key="S19")
    out = json.loads(line.model_dump_json())
    assert out["amount"] == "-19.00" and out["tax_base"] == "100.00"
    assert "amount_cents" not in out and "tax_base_cents" not in out
    assert json.loads(JournalLine(account="1200", amount_cents=0).model_dump_json())["tax_base"] is None


def test_journal_entry_response():
    row = {
        "id": "e1",
        "fiscal_year": 2026,
        "entry_no": 7,
        "booking_date": "2026-03-05",
        "document_date": "2026-03-04",
        "event": "manual",
        "event_key": "manual:u",
        "text": "t",
        "origin": "manual",
        "prev_hash": "0" * 64,
        "hash": "a" * 64,
        "novel": {"k": 1},
        "lines": [{"account": "1200", "amount_cents": 11900}, {"account": "8400", "amount_cents": -11900, "x": 2}],
    }
    out = json.loads(JournalEntry(**row).model_dump_json())
    assert [ln["amount"] for ln in out["lines"]] == ["119.00", "-119.00"]
    assert out["entry_no"] == 7 and out["hash"] == "a" * 64 and out["novel"] == {"k": 1}
    assert out["lines"][1]["x"] == 2


def test_vat_return_response_money():
    row = {
        "id": "v1",
        "year": 2026,
        "period_kind": "month",
        "period_no": 1,
        "status": "final",
        "boxes": {
            "81": {"base": "3500.00", "tax": "665.00", "booked_base": "3500.50", "booked_tax": "665.10"},
            "66": {"amount": "76.00"},
        },
        "result_cents": 58900,
        "special_prepayment_credit_cents": 0,
        "rounding_difference_cents": -10,
        "novel": 1,
    }
    out = json.loads(VatReturn(**row).model_dump_json(exclude_none=True))
    assert out["result"] == "589.00" and out["special_prepayment_credit"] == "0.00"
    assert out["rounding_difference"] == "-0.10"
    assert out["boxes"]["81"] == {"base": "3500.00", "tax": "665.00", "booked_base": "3500.50", "booked_tax": "665.10"}
    assert out["boxes"]["66"] == {"amount": "76.00"}
    assert out["novel"] == 1 and "result_cents" not in out


def test_vat_return_draft_without_figures():
    out = json.loads(VatReturn(year=2026).model_dump_json())
    assert out["result"] is None and out["boxes"] == {} and out["status"] == "draft"


def test_zm_response_money():
    row = {
        "id": "z1",
        "year": 2026,
        "quarter": 1,
        "lines": [{"vat_id": "FR123", "country": "FR", "amount": "1500.00", "kind": "S"}],
    }
    out = json.loads(ZmReport(**row).model_dump_json())
    assert out["lines"][0]["amount"] == "1500.00" and out["status"] == "draft"


# ---------------------------------------------------------------- review fixes


def _line(**kw):
    return {"account": "1776", "amount": "-19.00", **kw}


def test_manual_line_unknown_tax_key_and_kind():
    full = {"tax_key": "S19", "tax_kind": "tax", "tax_base": "100.00", "vat_date": "2026-03-05"}
    bad(ManualEntryIn, **{**ENTRY, "lines": [D, C1, _line(**{**full, "tax_key": "S99"})]})
    bad(ManualEntryIn, **{**ENTRY, "lines": [D, C1, _line(**{**full, "tax_key": "s19"})]})
    bad(ManualEntryIn, **{**ENTRY, "lines": [D, C1, _line(**{**full, "tax_kind": "bogus"})]})
    assert ManualEntryIn(**{**ENTRY, "lines": [D, C1, _line(**full)]})


@pytest.mark.parametrize(
    "extra",
    [
        {"tax_kind": "tax"},
        {"tax_base": "100.00"},
        {"tax_rate": "19"},
        {"vat_date": "2026-03-05"},
    ],
)
@pytest.mark.parametrize("key", [{"tax_key": "NONE"}, {}])
def test_manual_line_none_key_refuses_tax_fields(key, extra):
    bad(ManualEntryIn, **{**ENTRY, "lines": [D, C1, _line(**key, **extra)]})


def test_manual_line_none_key_plain_ok():
    assert ManualEntryIn(**{**ENTRY, "lines": [D, C1, _line(tax_key="NONE")]})


def test_init_refuses_duplicates():
    base = {"chart": "skr03", "income_basis": "eur"}
    rr = {"key": "a", "action": "confirm"}
    bad(AccountingInitIn, **base, review_resolutions=[rr, rr])
    bad(AccountingInitIn, **base, opening_balances=[{"account": "1200", "amount": "1"}] * 2)
    bad(AccountingInitIn, **base, opening_balances=[{"role": "bank", "amount": "1"}] * 2)
    ok = AccountingInitIn(
        **base,
        review_resolutions=[rr, {**rr, "key": "b"}],
        opening_balances=[{"account": "1200", "amount": "1"}, {"role": "bank", "amount": "-1"}],
    )
    assert len(ok.opening_balances) == 2


@pytest.mark.parametrize("stored, shown", [("58900", "589.00"), (58900.0, "589.00"), (-10, "-0.10"), ("1999", "19.99")])
def test_cents_numeric_string_and_float_kept(stored, shown):
    out = json.loads(VatReturn(year=2026, result_cents=stored).model_dump_json())
    assert out["result"] == shown


@pytest.mark.parametrize("stored", ["abc", None, True, float("nan")])
def test_cents_unparseable_is_none(stored):
    assert json.loads(VatReturn(year=2026, result_cents=stored).model_dump_json())["result"] is None


def test_vat_return_and_zm_skip_non_dict_entries():
    out = VatReturn(year=2026, boxes={"81": {"base": "1.00"}, "66": "junk", "67": None})
    assert list(out.boxes) == ["81"]
    z = ZmReport(
        year=2026,
        quarter=1,
        lines=[{"vat_id": "FR1", "country": "FR", "amount": "2.00"}, "junk", None],
    )
    assert len(z.lines) == 1
