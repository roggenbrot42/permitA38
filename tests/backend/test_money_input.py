"""Money input: a JSON number or a decimal string, same bounds (decimal-money-api spec, rule 2)."""

import math

import pytest
from pydantic import TypeAdapter, ValidationError

from permita38.core.validation import (
    Amount,
    CheckedBody,
    MonthlyAmount,
    NonNegAmount,
    Rate,
    StrictAmount10k,
    StrictAmount100k,
    StrictMonthlyAmount,
    StrictNonNegAmount,
)

ALL = {
    "Amount": (Amount, -1e9, 1e9),
    "NonNegAmount": (NonNegAmount, 0, 1e9),
    "MonthlyAmount": (MonthlyAmount, 0, 100_000),
    "Rate": (Rate, 0, 10_000),
    "StrictNonNegAmount": (StrictNonNegAmount, 0, 1e9),
    "StrictMonthlyAmount": (StrictMonthlyAmount, 0, 100_000),
    "StrictAmount100k": (StrictAmount100k, 0, 100_000),
    "StrictAmount10k": (StrictAmount10k, 0, 10_000),
}
PARAMS = [pytest.param(*v, id=k) for k, v in ALL.items()]


@pytest.mark.parametrize(("tp", "lo", "hi"), PARAMS)
def test_number_and_string_give_the_same_float(tp, lo, hi):
    ta = TypeAdapter(tp)
    for number, text in [(12.5, "12.50"), (0, "0"), (3, "3.0"), (0.125, "0.125"), (1234.56, "1234.56")]:
        got_n, got_s = ta.validate_python(number), ta.validate_python(text)
        assert got_n == got_s == float(number)
        assert isinstance(got_s, float)
    assert ta.validate_json("12.5") == ta.validate_json('"12.50"') == 12.5


@pytest.mark.parametrize(("tp", "lo", "hi"), PARAMS)
@pytest.mark.parametrize(
    "bad",
    [
        "1,5",
        "1e3",
        "1E3",
        " 1.5",
        "1.5 ",
        "1 000.00",
        "1.000,00",
        "1,000.00",
        "",
        ".5",
        "5.",
        "+5",
        "abc",
        "NaN",
        "inf",
        "1.1234567",
        "12345678901",
        "--1",
        "12.50\n",
        "\n12.5",
        "1.5\n",
        "0x10",
        "1_000",
        "\u0661\u0662",
        "+1.00",
        True,
        False,
        None,
        [1],
        {},
    ],
)
def test_malformed_input_is_refused(tp, lo, hi, bad):
    with pytest.raises(ValidationError):
        TypeAdapter(tp).validate_python(bad)


@pytest.mark.parametrize(("tp", "lo", "hi"), PARAMS)
def test_bounds_apply_to_number_and_string(tp, lo, hi):
    ta = TypeAdapter(tp)
    assert ta.validate_python(hi) == hi
    assert ta.validate_python(str(int(hi))) == hi
    assert ta.validate_python(float(lo)) == lo
    assert ta.validate_python(str(int(lo))) == lo
    for over in (hi + 0.01, f"{int(hi)}.01", f"{int(hi) + 1}"):
        with pytest.raises(ValidationError):
            ta.validate_python(over)
    for under in (lo - 0.01, f"{int(lo) - 1}", f"-{abs(int(lo))}.01" if lo else "-0.01"):
        with pytest.raises(ValidationError):
            ta.validate_python(under)
    for nonfinite in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            ta.validate_python(nonfinite)


def test_negative_string_where_allowed():
    assert TypeAdapter(Amount).validate_python("-57.10") == -57.1
    assert TypeAdapter(Amount).validate_python("-0.000001") == -0.000001


@pytest.mark.parametrize(("tp", "lo", "hi"), PARAMS)
def test_json_schema_offers_number_or_string(tp, lo, hi):
    schema = TypeAdapter(tp).json_schema()
    assert {b["type"] for b in schema["anyOf"]} == {"number", "string"}
    string = next(b for b in schema["anyOf"] if b["type"] == "string")
    assert string["pattern"] == r"^-?[0-9]{1,10}(\.[0-9]{1,6})?$"
    number = next(b for b in schema["anyOf"] if b["type"] == "number")
    assert number["minimum"] == lo and number["maximum"] == hi


@pytest.mark.parametrize(("tp", "lo", "hi"), PARAMS)
def test_accepted_edge_forms(tp, lo, hi):
    ta = TypeAdapter(tp)
    assert ta.validate_python("0.000000") == 0.0
    assert ta.validate_python("007.5") == 7.5
    if lo == 0:
        zero = ta.validate_python("-0")
        assert zero == 0.0 and math.copysign(1, zero) == -1.0
    top = f"{int(hi)}.000000"
    assert ta.validate_python(top) == hi
    assert ta.validate_json(f'"{top}"') == hi
    assert ta.validate_json(f'"{int(hi)}"') == hi
    with pytest.raises(ValidationError):
        ta.validate_json(f'"{int(hi)}.000001"')


def test_minus_zero_string_where_negatives_are_allowed():
    zero = TypeAdapter(Amount).validate_python("-0")
    assert zero == 0.0 and math.copysign(1, zero) == -1.0


class Body(CheckedBody):
    amount: Amount
    note: str = ""


def test_checked_body_lets_money_strings_through():
    assert Body.model_validate({"amount": "12.50"}).amount == 12.5
    assert Body.model_validate({"amount": 12.5}).amount == 12.5
    with pytest.raises(ValidationError):
        Body.model_validate({"amount": "12,50"})
    with pytest.raises(ValidationError):  # the text checks still apply to every string
        Body.model_validate({"amount": "1", "note": "a\x00b"})


def test_money_in_the_openapi_of_an_app():
    from fastapi import FastAPI
    from pydantic import BaseModel

    from permita38.core.validation import Money, UnitPrice

    class Out(BaseModel):
        net: Money
        price: UnitPrice

    class In(BaseModel):
        amount: Amount

    app = FastAPI()

    @app.post("/x", response_model=Out)
    def x(body: In) -> Out:  # pragma: no cover - never called
        raise NotImplementedError

    schemas = app.openapi()["components"]["schemas"]
    for name in ("net", "price"):
        prop = schemas["Out"]["properties"][name]
        assert prop["type"] == "string" and prop["format"] == "decimal" and prop["pattern"] == r"^-?[0-9]+\.[0-9]{2,6}$"
    amount = schemas["In"]["properties"]["amount"]
    assert {b["type"] for b in amount["anyOf"]} == {"number", "string"}
