"""Decimal-string output of money: money.to_decimal_str, Money/UnitPrice and present() (decimal-money-api spec)."""

import pytest
from pydantic import BaseModel, TypeAdapter

from permita38.api.responses import present
from permita38.core import money
from permita38.core.validation import Money, UnitPrice

DECIMAL_SCHEMA = {"type": "string", "format": "decimal", "pattern": r"^-?[0-9]+\.[0-9]{2,6}$"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0.00"),
        (0.0, "0.00"),
        (118.09, "118.09"),
        (-57.1, "-57.10"),
        (1190, "1190.00"),
        (0.125, "0.125"),
        (0.1234567, "0.123457"),
        (-0.0, "0.00"),
        (-0.005, "-0.005"),
        (-0.004, "-0.004"),
        (-0.0000001, "0.00"),
        ("12.5", "12.50"),
        (1e7, "10000000.00"),
        (1_000_000_000, "1000000000.00"),
    ],
)
def test_to_decimal_str(value, expected):
    assert money.to_decimal_str(value) == expected


@pytest.mark.parametrize("bad", ["abc", None, float("nan")])
def test_to_decimal_str_rejects_non_numbers(bad):
    with pytest.raises(ValueError):
        money.to_decimal_str(bad)


class Inner(BaseModel):
    gross: Money = 0.0
    qty: float = 1


class Rec(BaseModel):
    net: Money = 0.0
    price: UnitPrice | None = None
    rate: Money | None = None
    lines: list[Inner] = []
    by_name: dict[str, Money] = {}
    inner: Inner | None = None
    qty: float = 0


def test_python_value_stays_float_and_json_is_string():
    r = Rec(net=118.09, price="0.125")
    assert r.net == 118.09 and isinstance(r.net, float)
    assert r.price == 0.125
    assert r.model_dump()["net"] == 118.09  # python mode unchanged
    assert r.model_dump(mode="json")["net"] == "118.09"
    assert r.model_dump(mode="json")["price"] == "0.125"
    assert '"net":"3.00"' in Rec(net=3).model_dump_json()


def test_lenient_input_accepts_float_int_and_decimal_string():
    assert Rec(net="12.5").net == 12.5
    assert Rec(net=7).net == 7.0
    assert Rec.model_validate({"net": "-0.10"}).net == -0.1


def test_json_schema_is_the_decimal_string():
    props = Rec.model_json_schema(mode="serialization")["properties"]
    assert props["net"] == {**DECIMAL_SCHEMA, "default": "0.00", "title": "Net"}
    assert props["by_name"]["additionalProperties"] == DECIMAL_SCHEMA
    assert DECIMAL_SCHEMA in props["price"]["anyOf"]
    assert TypeAdapter(Money).json_schema() == DECIMAL_SCHEMA


def test_present_converts_nested_and_leaves_the_rest():
    rec = {
        "net": 118.09,
        "price": 0.125,
        "qty": 2.5,
        "lines": [{"gross": 5, "qty": 3, "extra": 1.5}, {"gross": None}],
        "by_name": {"a": 1, "b": "2.5"},
        "inner": {"gross": 0},
        "unknown": 1.5,
        "rate": None,
    }
    out = present(Rec, rec)
    assert out == {
        "net": "118.09",
        "price": "0.125",
        "qty": 2.5,
        "lines": [{"gross": "5.00", "qty": 3, "extra": 1.5}, {"gross": None}],
        "by_name": {"a": "1.00", "b": "2.50"},
        "inner": {"gross": "0.00"},
        "unknown": 1.5,
        "rate": None,
    }
    assert rec["net"] == 118.09  # input untouched


@pytest.mark.parametrize("bad", ["abc", "", True, "1,5", [1], {"a": 1}])
def test_present_passes_unparsable_values_through(bad):
    out = present(Rec, {"net": bad, "lines": "oops", "inner": 5})
    assert out == {"net": bad, "lines": "oops", "inner": 5}


def test_present_nan_does_not_raise():
    assert present(Rec, {"net": float("nan")})["net"] != 0


def test_present_list_and_non_dict():
    assert present(Rec, [{"net": 1}, {"net": 2}]) == [{"net": "1.00"}, {"net": "2.00"}]
    assert present(Rec, None) is None
