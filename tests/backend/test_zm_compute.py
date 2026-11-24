"""ZM computation: oracle Z1-Z3 of docs/specs/zm.md (fixtures/accounting_oracle.json `zm_cases`)."""

import json
from datetime import date
from pathlib import Path
from typing import Any

from permita38.core.accounting.types import Cents, Line, Role, TaxKey
from permita38.core.vat.deadlines import zm_due
from permita38.core.vat.zm import kz21_by_month, zm_lines

ORACLE = json.loads((Path(__file__).parent / "fixtures" / "accounting_oracle.json").read_text(encoding="utf-8"))
CASES: dict[str, Any] = {c["id"]: c for c in ORACLE["zm_cases"]}
VAT_IDS = {"cust_A": "ATU00000001", "cust_B": "FR00000000001"}


def _line(case: dict[str, Any]) -> Line:
    return Line(
        role=Role(VAT_IDS[case["customer"]]),  # carries the customer for _vid
        amount_cents=Cents(0),
        tax_key=TaxKey("EU_SVC"),
        tax_kind="base_only",
        tax_base_cents=Cents(int(case["base"])),
        vat_date=date.fromisoformat(case["vat_date"]),
    )


def _vid(line: Line) -> str:
    return str(line.role)


def _z1() -> list[Line]:
    return [_line(c) for c in CASES["Z1"]["lines"]]


def _amounts(lines: list[Line], q: int, corrected: bool = False) -> dict[str, int]:
    return {z.vat_id: z.amount for z in zm_lines(lines, 2026, q, vat_id_of=_vid, corrected=corrected)}


def test_z1_quarter_and_kz21() -> None:
    lines = _z1()
    exp = CASES["Z1"]["zm"]["2026-Q1"]
    assert _amounts(lines, 1) == {VAT_IDS[k]: v for k, v in exp.items()}
    assert _amounts(lines, 1) == {"ATU00000001": 3001, "FR00000000001": 4000}
    res = zm_lines(lines, 2026, 1, vat_id_of=_vid)
    assert [(z.country, z.kind, len(z.source)) for z in res] == [("AT", "S", 2), ("FR", "S", 1)]
    kz = kz21_by_month(lines, 2026)
    assert kz == {1: 1000, 3: 6000}
    assert kz == {int(k[-2:]): v for k, v in CASES["Z1"]["kz21"].items()}
    assert _amounts(lines, 2) == {}


def test_z2_credit_in_quarter_of_change() -> None:
    lines = [*_z1(), _line(CASES["Z2"]["extra_line"])]
    assert _amounts(lines, 2) == {"ATU00000001": -500}
    assert _amounts(lines, 1) == {"ATU00000001": 3001, "FR00000000001": 4000}
    assert kz21_by_month(lines, 2026)[5] == -500 == CASES["Z2"]["kz21"]["2026-05"]


def test_z3_due_dates() -> None:
    for key, iso in CASES["Z3"]["due"].items():
        year, q = key.split("-Q")
        assert zm_due(int(year), int(q)) == date.fromisoformat(iso)
    assert zm_due(2026, 1) == date(2026, 4, 27)
    assert zm_due(2026, 2) == date(2026, 7, 27)
    assert zm_due(2026, 3) == date(2026, 10, 26)
    assert zm_due(2026, 4) == date(2027, 1, 25)


def test_zero_total_omitted_first_listed_when_corrected() -> None:
    lines = [*_z1(), _line({"customer": "cust_B", "base": -400000, "vat_date": "2026-03-20"})]
    assert _amounts(lines, 1) == {"ATU00000001": 3001}
    assert _amounts(lines, 1, corrected=True) == {"ATU00000001": 3001, "FR00000000001": 0}


def test_ignores_other_lines_and_years() -> None:
    d = date(2026, 2, 1)
    other = Line(
        Role("x"), Cents(0), tax_key=TaxKey("S19"), tax_kind="base_only", tax_base_cents=Cents(500000), vat_date=d
    )
    tax = Line(
        Role(VAT_IDS["cust_A"]),
        Cents(0),
        tax_key=TaxKey("EU_SVC"),
        tax_kind="tax",
        tax_base_cents=Cents(500000),
        vat_date=d,
    )
    late = _line({"customer": "cust_A", "base": 900000, "vat_date": "2027-01-02"})
    assert _amounts([*_z1(), other, tax, late], 1) == {"ATU00000001": 3001, "FR00000000001": 4000}


def test_negative_truncates_toward_zero() -> None:
    ln = _line({"customer": "cust_A", "base": -150, "vat_date": "2026-02-01"})
    assert _amounts([ln], 1) == {"ATU00000001": -1}


def test_quarter_sum_equals_three_kz21_bases_before_rounding() -> None:
    cents = [100040, 200070, 400000, -50000, 33, 99, 1]
    days = ["2026-01-15", "2026-03-02", "2026-03-10", "2026-02-05", "2026-01-31", "2026-03-31", "2026-02-28"]
    custs = ["cust_A", "cust_B", "cust_A", "cust_B", "cust_A", "cust_A", "cust_B"]
    lines = [_line({"customer": c, "base": b, "vat_date": d}) for c, b, d in zip(custs, cents, days, strict=True)]
    res = zm_lines(lines, 2026, 1, vat_id_of=_vid, corrected=True)
    zm_total = sum(s.tax_base_cents or 0 for z in res for s in z.source)
    kz_total = sum(ln.tax_base_cents or 0 for m in (1, 2, 3) for ln in lines if ln.vat_date and ln.vat_date.month == m)
    assert zm_total == kz_total == sum(cents)
