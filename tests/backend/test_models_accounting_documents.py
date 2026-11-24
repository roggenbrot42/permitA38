"""Models of the accounting work package E3a: strict inputs (payments, credit notes, treatments) and the lenient
response models (docs/specs/credit-notes-payments.md sections 2.3, 3, 4, 5; accounting.md 7a and 8.2)."""

import json
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from permita38.core import validation as v
from permita38.core.models import (
    CreditNoteIn,
    CreditNoteSummary,
    Document,
    DocumentIn,
    Expense,
    ExpenseIn,
    InvoiceIn,
    Payment,
    PaymentIn,
    PaymentReverseIn,
    SettleDiscountIn,
)

PAY = {"date": "2026-02-03", "amount": "1190.00"}


# ---------------------------------------------------------------- PaymentIn


@pytest.mark.parametrize("amount", ["1190.00", 1190, 1190.0, "1190", "0.01", 0.01, "10000000.00", 10_000_000])
def test_payment_accepts_string_and_number(amount):
    p = PaymentIn(date="2026-02-03", amount=amount)
    assert isinstance(p.amount, Decimal)
    assert p.kind == "payment" and p.method == "bank" and p.note is None


def test_payment_amount_is_exact_decimal_never_float():
    assert PaymentIn(date="2026-02-03", amount=1166.2).amount == Decimal("1166.20")
    assert PaymentIn(date="2026-02-03", amount="1166.2").amount == Decimal("1166.20")
    assert str(PaymentIn(date="2026-02-03", amount=0.1).amount) == "0.10"


def test_payment_full_body():
    p = PaymentIn(date="2026-02-03", amount="5.00", kind="refund", method="cash", note="x" * 200)
    assert (p.kind, p.method, len(p.note)) == ("refund", "cash", 200)


@pytest.mark.parametrize(
    "amount",
    [
        0,
        "0",
        "0.00",
        -1,
        "-5.00",
        "10000000.01",
        10_000_001,
        1.234,
        "1.234",
        "1.005",
        "1e3",
        "1,50",
        " 1.00",
        "1.00\n",
        True,
        None,
        "",
        "abc",
        float("inf"),
        float("nan"),
        [1],
        {"a": 1},
    ],
)
def test_payment_refuses_bad_amounts(amount):
    with pytest.raises(ValidationError):
        PaymentIn(date="2026-02-03", amount=amount)


@pytest.mark.parametrize(
    "patch",
    [
        {"kind": "reversal"},
        {"kind": "discount"},
        {"method": "card"},
        {"note": "x" * 201},
        {"date": "2026-13-45"},
        {"date": "03.02.2026"},
        {"date": None},
    ],
)
def test_payment_refuses_bad_fields(patch):
    with pytest.raises(ValidationError):
        PaymentIn(**{**PAY, **patch})


@pytest.mark.parametrize("missing", ["date", "amount"])
def test_payment_requires_date_and_amount(missing):
    body = {k: val for k, val in PAY.items() if k != missing}
    with pytest.raises(ValidationError):
        PaymentIn(**body)


def test_payment_is_a_checked_body_and_ignores_unknown_fields():
    assert issubclass(PaymentIn, v.CheckedBody)
    p = PaymentIn(**PAY, amount_cents=5, created_by="x")
    assert not hasattr(p, "amount_cents")
    with pytest.raises(ValidationError):
        PaymentIn(**PAY, note="bad\x00note")


def test_payment_json_schema_offers_number_or_string():
    amount = PaymentIn.model_json_schema()["properties"]["amount"]
    assert {a["type"] for a in amount["anyOf"]} == {"number", "string"}


# ---------------------------------------------------------------- PaymentReverseIn / SettleDiscountIn


def test_reverse_accepts_empty_and_full():
    assert PaymentReverseIn().date is None
    r = PaymentReverseIn(date="2026-03-01", note="n" * 200)
    assert r.date == "2026-03-01"
    assert PaymentReverseIn(date="").date is None  # forms send empty strings for "not set"


@pytest.mark.parametrize("body", [{"note": "n" * 201}, {"date": "2026-02-30"}, {"date": 5}])
def test_reverse_refuses(body):
    with pytest.raises(ValidationError):
        PaymentReverseIn(**body)


def test_settle_discount_accepts_string_and_number():
    a = SettleDiscountIn(date="2026-02-03", amount_paid="1166.20")
    b = SettleDiscountIn(date="2026-02-03", amount_paid=1166.2, note="Skonto")
    assert a.amount_paid == b.amount_paid == Decimal("1166.20")


@pytest.mark.parametrize("amount", [0, "0.00", -1, "1166.201", "10000000.01", True, "x"])
def test_settle_discount_refuses_bad_amounts(amount):
    with pytest.raises(ValidationError):
        SettleDiscountIn(date="2026-02-03", amount_paid=amount)


def test_settle_discount_requires_date_and_amount_and_bounds_note():
    with pytest.raises(ValidationError):
        SettleDiscountIn(amount_paid="1.00")
    with pytest.raises(ValidationError):
        SettleDiscountIn(date="2026-02-03")
    with pytest.raises(ValidationError):
        SettleDiscountIn(date="2026-02-03", amount_paid="1.00", note="n" * 201)


# ---------------------------------------------------------------- CreditNoteIn

CN = {"credit_kind": "storno", "date": "2026-03-01", "reason": "wrong rate"}


@pytest.mark.parametrize("kind", ["storno", "write_off"])
def test_credit_note_kinds_without_amounts(kind):
    c = CreditNoteIn(**{**CN, "credit_kind": kind})
    assert c.credit_kind == kind and c.amounts is None


def test_credit_note_partial_amounts_are_decimals():
    c = CreditNoteIn(**{**CN, "credit_kind": "partial", "amounts": {"19": "500.00", "7": 100.5, "0": 3}})
    assert c.amounts == {"19": Decimal("500.00"), "7": Decimal("100.50"), "0": Decimal("3.00")}
    assert all(isinstance(x, Decimal) for x in c.amounts.values())


@pytest.mark.parametrize("kind", ["discount", "write_off_reversal", "credit", "", None, 1])
def test_credit_note_refuses_bad_kind(kind):
    # discount only through settle-discount; write_off_reversal is internal
    with pytest.raises(ValidationError):
        CreditNoteIn(**{**CN, "credit_kind": kind})


def test_credit_note_rate_keys_are_normalised_and_unique():
    def amounts(d):
        return CreditNoteIn(**{**CN, "credit_kind": "partial", "amounts": d}).amounts

    assert list(amounts({"19.00": "1"})) == ["19"]
    assert list(amounts({"19.0": "1"})) == ["19"]
    assert list(amounts({"10.70": "1"})) == ["10.7"]
    assert list(amounts({"10.7": "1"})) == ["10.7"]
    assert list(amounts({"0.0": "1"})) == ["0"]
    assert list(amounts({"10": "1"})) == ["10"]
    for dup in ({"19": "1", "19.00": "2"}, {"19.0": "1", "19.00": "2"}, {"7": "1", "07": "2"}):
        with pytest.raises(ValidationError):
            amounts(dup)


def test_credit_note_amounts_bound_is_20():
    ok = {str(i): "1" for i in range(20)}
    assert len(CreditNoteIn(**{**CN, "credit_kind": "partial", "amounts": ok}).amounts) == 20
    with pytest.raises(ValidationError):
        CreditNoteIn(**{**CN, "credit_kind": "partial", "amounts": {**ok, "20": "1"}})


@pytest.mark.parametrize("key", ["100", "19.", "-1", "1e1", "abc", "", "-19", "19%", "1.234", " 19", 19])
def test_credit_note_refuses_bad_rate_key(key):
    with pytest.raises(ValidationError):
        CreditNoteIn(**{**CN, "credit_kind": "partial", "amounts": {key: "1.00"}})


@pytest.mark.parametrize(
    "amount", [0, "0.00", -5, "-5.00", "1.234", 1.005, "1,5", "1e2", True, None, "", "1000000000.01"]
)
def test_credit_note_refuses_bad_amount_values(amount):
    with pytest.raises(ValidationError):
        CreditNoteIn(**{**CN, "credit_kind": "partial", "amounts": {"19": amount}})


def test_credit_note_partial_needs_amounts():
    for amounts in (None, {}):
        with pytest.raises(ValidationError):
            CreditNoteIn(**{**CN, "credit_kind": "partial", "amounts": amounts})


@pytest.mark.parametrize("reason", ["", " ", "   \n", "x" * 501, None, 5])
def test_credit_note_refuses_bad_reason(reason):
    with pytest.raises(ValidationError):
        CreditNoteIn(**{**CN, "reason": reason})


def test_credit_note_reason_bounds_are_inclusive():
    assert CreditNoteIn(**{**CN, "reason": "x"}).reason == "x"
    assert len(CreditNoteIn(**{**CN, "reason": "x" * 500}).reason) == 500


@pytest.mark.parametrize("missing", ["credit_kind", "date", "reason"])
def test_credit_note_requires_fields(missing):
    with pytest.raises(ValidationError):
        CreditNoteIn(**{k: val for k, val in CN.items() if k != missing})


def test_credit_note_refuses_bad_date():
    with pytest.raises(ValidationError):
        CreditNoteIn(**{**CN, "date": "2026-02-30"})


# ---------------------------------------------------------------- treatments, DocumentIn / ExpenseIn


@pytest.mark.parametrize("value", ["standard", "small_business", "eu_b2b_service", None])
def test_document_in_accepts_sales_treatment(value):
    assert InvoiceIn(tax_treatment=value).tax_treatment == value


RESERVED_SALES = [
    "reverse_charge_domestic",
    "eu_b2b_goods_exempt",
    "export_exempt",
    "non_taxable_other",
    "exempt_with_deduction",
    "exempt_no_deduction",
]
RESERVED_EXPENSE = ["reverse_charge_domestic", "import_vat"]


def test_reserved_treatment_constants_match_the_contract():
    assert list(v.RESERVED_SALES_TREATMENTS) == RESERVED_SALES
    assert list(v.RESERVED_EXPENSE_TREATMENTS) == RESERVED_EXPENSE


@pytest.mark.parametrize("value", RESERVED_SALES)
def test_document_in_reserved_sales_treatment_message(value):
    with pytest.raises(ValidationError, match="not supported in this release"):
        InvoiceIn(tax_treatment=value)


@pytest.mark.parametrize("value", ["no_input_vat", "reverse_charge_eu_service", "Standard", "", 1])
def test_document_in_refuses_other_sales_treatments(value):
    with pytest.raises(ValidationError) as e:
        InvoiceIn(tax_treatment=value)
    assert "not supported in this release" not in str(e.value)


EXP = {"date": "2026-02-03", "net": 10}


@pytest.mark.parametrize(
    "value", ["standard", "no_input_vat", "reverse_charge_eu_service", "reverse_charge_third_country", None]
)
def test_expense_in_accepts_expense_treatment(value):
    assert ExpenseIn(**EXP, tax_treatment=value).tax_treatment == value


@pytest.mark.parametrize("value", RESERVED_EXPENSE)
def test_expense_in_reserved_treatment_message(value):
    with pytest.raises(ValidationError, match="not supported in this release"):
        ExpenseIn(**EXP, tax_treatment=value)


@pytest.mark.parametrize("value", ["small_business", "eu_b2b_service", ""])
def test_expense_in_refuses_other_expense_treatments(value):
    with pytest.raises(ValidationError) as e:
        ExpenseIn(**EXP, tax_treatment=value)
    assert "not supported in this release" not in str(e.value)


def test_expense_service_date_bounds():
    assert ExpenseIn(**EXP, service_date="2026-02-03").service_date == "2026-02-03"
    assert ExpenseIn(**EXP, service_date="").service_date is None
    assert ExpenseIn(**EXP, service_date="2027-02-04").service_date == "2027-02-04"  # date + 366 days
    for bad in ("2027-02-05", "2026-13-01", "03.02.2026"):
        with pytest.raises(ValidationError):
            ExpenseIn(**EXP, service_date=bad)
    assert ExpenseIn(**EXP, service_date="2020-01-01").service_date == "2020-01-01"  # earlier is fine


def test_internal_fields_of_a_client_are_dropped_not_stored():
    """Revisions and the issue snapshot belong to the server. They are dropped silently (a client PUTs whole records
    back), they never reach the stored record; other unknown fields still round-trip."""
    e = ExpenseIn(**EXP, bill_rev=7, pay_rev=3, future_field="kept")
    dumped = e.model_dump()
    assert "bill_rev" not in dumped and "pay_rev" not in dumped
    assert dumped["future_field"] == "kept"
    internal = {
        "issued": {"gross": "1.00"},
        "kind": "credit_note",
        "credit_kind": "storno",
        "corrects_id": "abc123",
        "paid_amount": "1.00",
        "credited_amount": "1.00",
        "open_amount": "0.00",
        "payments": [],
        "credit_notes": [],
    }
    dumped = DocumentIn(**internal, future_field={"a": 1}).model_dump()
    for name in internal:
        assert name not in dumped
    assert dumped["future_field"] == {"a": 1}


def test_internal_fields_are_not_declared_on_the_in_models():
    for name in ("bill_rev", "pay_rev", "post_rev"):
        assert name not in ExpenseIn.model_fields
    for name in ("issued", "kind", "corrects_id", "paid_amount", "open_amount", "payments"):
        assert name not in DocumentIn.model_fields


def test_new_fields_default_to_none():
    assert InvoiceIn().tax_treatment is None
    e = ExpenseIn(**EXP)
    assert e.tax_treatment is None and e.service_date is None


# ---------------------------------------------------------------- MoneyIn primitive


def test_money_in_primitive_yields_decimal_and_allows_negative():
    class M(BaseModel):
        x: v.MoneyIn

    assert M(x="-57.10").x == Decimal("-57.10")
    assert M(x=-57.1).x == Decimal("-57.10")
    assert M(x=0).x == Decimal("0.00")
    for bad in ("1.001", "1000000000.01", "-1000000000.01", True, "1e2"):
        with pytest.raises(ValidationError):
            M(x=bad)


# ---------------------------------------------------------------- lenient response models


def test_document_serialises_money_as_decimal_strings_in_json():
    doc = Document(
        id="a1b2c3",
        kind="invoice",
        tax_treatment="standard",
        issued={"gross": "1190.00"},
        paid_amount=1000,
        credited_amount=0,
        open_amount=190.5,
        payments=[{"id": "p1", "date": "2026-02-03", "amount": 1000, "kind": "payment", "method": "bank"}],
        credit_notes=[
            {"id": "c1", "number": "RK-2026-0001", "date": "2026-03-01", "credit_kind": "partial", "gross": 595}
        ],
    )
    out = json.loads(doc.model_dump_json())
    assert out["paid_amount"] == "1000.00"
    assert out["credited_amount"] == "0.00"
    assert out["open_amount"] == "190.50"
    assert out["payments"][0]["amount"] == "1000.00"
    assert out["credit_notes"][0]["gross"] == "595.00"
    assert out["credit_notes"][0]["number"] == "RK-2026-0001"
    assert out["issued"] == {"gross": "1190.00"}
    # python mode keeps the stored type for internal callers
    assert doc.model_dump()["paid_amount"] == 1000


def test_document_accepts_decimal_strings_and_negative_open_amount():
    doc = Document(open_amount="-5.00", paid_amount="5")
    assert json.loads(doc.model_dump_json())["open_amount"] == "-5.00"


def test_document_derived_fields_default_to_none():
    out = json.loads(Document().model_dump_json())
    for name in (
        "kind",
        "credit_kind",
        "corrects_id",
        "tax_treatment",
        "issued",
        "paid_amount",
        "credited_amount",
        "open_amount",
        "payments",
        "credit_notes",
    ):
        assert out[name] is None


def test_payment_model_signs_and_virtual_payment():
    p = Payment(id=None, date="2026-02-03", amount=-23.8, kind="refund", note="migrated")
    out = json.loads(p.model_dump_json())
    assert out["id"] is None and out["amount"] == "-23.80" and out["kind"] == "refund" and out["method"] == "bank"
    assert json.loads(Payment(amount="0").model_dump_json())["amount"] == "0.00"


def test_payment_model_never_fails_on_malformed_stored_values():
    p = Payment(date=["x"], amount="abc", created="soon", kind="payment")
    out = json.loads(p.model_dump_json())
    assert out["date"] is None and out["amount"] == "0.00" and out["created"] is None
    doc = Document(payments=[{"date": 5, "amount": {"a": 1}, "created": "x"}])
    assert doc.payments[0].date is None and doc.payments[0].amount == 0


def test_credit_note_summary_write_off_has_no_number():
    c = CreditNoteSummary(id="c1", credit_kind="write_off", date="2026-03-01", gross="23.8")
    out = json.loads(c.model_dump_json())
    assert out["number"] is None and out["gross"] == "23.80"


def test_expense_response_has_treatment_and_service_date():
    e = Expense(date="2026-02-03", net=10, tax_treatment="reverse_charge_eu_service", service_date="2026-01-31")
    out = json.loads(e.model_dump_json())
    assert out["tax_treatment"] == "reverse_charge_eu_service" and out["service_date"] == "2026-01-31"
    assert out["net"] == "10.00"


def test_unknown_fields_round_trip_on_lenient_models():
    doc = Document(future="x", payments=[{"future_p": 1}], credit_notes=[{"future_c": 2}])
    out = doc.model_dump()
    assert out["future"] == "x"
    assert out["payments"][0]["future_p"] == 1 and out["credit_notes"][0]["future_c"] == 2
    assert Expense(date="2026-01-01", net=1, future=3).model_dump()["future"] == 3
    assert Payment(future=1).model_dump()["future"] == 1


def test_offer_body_ignores_credit_kind_and_tax_treatment():
    """Invoice-only fields (docs/specs/accounting.md 8): the server-owned ones are dropped, the treatment is dropped too."""
    assert "credit_kind" not in DocumentIn(credit_kind="storno").model_dump()
    assert "tax_treatment" not in DocumentIn.model_fields
    assert "tax_treatment" not in DocumentIn(tax_treatment="standard").model_dump()
    assert InvoiceIn(tax_treatment="standard").tax_treatment == "standard"
