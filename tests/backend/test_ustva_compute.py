"""UStVA box computation against the hand-computed oracle (accounting_oracle.json, ustva_cases)."""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from permita38.core.accounting.types import Cents, Line, Role
from permita38.core.vat import ustva
from permita38.core.vat.ustva import compute_boxes, special_prepayment
from permita38.core.vat_rules import TaxKey, vat_rules_for

_ORACLE = json.loads((Path(__file__).parent / "fixtures" / "accounting_oracle.json").read_text(encoding="utf-8"))
_CASES = {c["id"]: c for c in _ORACLE["ustva_cases"]}
RULES = vat_rules_for(2026)


def _period(text: str) -> tuple[date, date]:
    year = int(text[:4])
    tail = text[5:]
    if tail.startswith("Q"):
        q = int(tail[1:])
        first = date(year, 3 * q - 2, 1)
        nxt = date(year + (q == 4), 1 if q == 4 else 3 * q + 1, 1)
    else:
        m = int(tail)
        first = date(year, m, 1)
        nxt = date(year + (m == 12), 1 if m == 12 else m + 1, 1)
    return first, nxt - timedelta(days=1)


def _lines(spec: list[dict[str, Any]], vat_date: date) -> list[Line]:
    out: list[Line] = []
    for s in spec:
        key = TaxKey(s["tax_key"])
        d = date.fromisoformat(s["vat_date"]) if "vat_date" in s else vat_date
        rate = s.get("rate")
        if "input" in s:
            out.append(Line(Role("vat_in"), Cents(s["input"]), key, "input", rate, None, d))
        elif "tax" in s:
            out.append(Line(Role("vat_out"), Cents(-s["tax"]), key, "tax", rate, Cents(s["base"]), d))
        else:
            out.append(Line(Role("sales"), Cents(0), key, "base_only", rate, Cents(s["base"]), d))
    return out


def _check(lines_spec: list[dict[str, Any]], period: str, expected: dict[str, Any], **kw: Any) -> ustva.UstvaResult:
    start, end = _period(period)
    res = compute_boxes(_lines(lines_spec, start + timedelta(days=14)), start, end, RULES, **kw)
    for kz, want in expected.items():
        f = res.box(kz)
        if kz == "83":
            assert res.result_cents == want
            continue
        assert f is not None, f"box {kz} missing"
        if isinstance(want, dict):
            for field, value in want.items():
                assert getattr(f, field) == value, (kz, field)
        elif kz in ("35", "21", "46", "84"):
            assert f.base == want, kz
        elif kz in ("36", "47", "85"):
            assert f.tax == want, kz
        else:
            assert f.amount == want, kz
    return res


SIMPLE = ["U1", "U2", "U3", "U5", "U6", "U6-50", "U7", "U8", "U9", "U11", "U12", "U12-alone", "U14"]


@pytest.mark.parametrize("cid", SIMPLE)
def test_simple_cases(cid: str) -> None:
    c = _CASES[cid]
    res = _check(c["lines"], c["period"], c["boxes"])
    if "rounding_difference" in c:
        assert res.rounding_difference == c["rounding_difference"]
    if cid == "U3":
        box = res.box("81")
        assert box is not None and box.booked_tax != c["never"]


def test_u4_periods() -> None:
    for cid in ("U4-ist", "U4-soll"):
        for period, p in _CASES[cid]["periods"].items():
            _check(p["lines"], period, p["boxes"])


def test_u10_december_with_special_prepayment() -> None:
    c = _CASES["U10-december"]
    credit = c["special_prepayment"]
    _check(c["lines"], c["period"], c["boxes"], special_prepayment_credit_cents=credit)
    lines = [dict(s, input=c["variant"]["VST input"]) if "input" in s else s for s in c["lines"]]
    _check(lines, c["period"], {"83": c["variant"]["83"]}, special_prepayment_credit_cents=credit)


def test_u10_special_prepayment() -> None:
    for case in _CASES["U10-special-prepayment"]["cases"]:
        got = special_prepayment(case["previous_kz83_sum"], case["previous_kz39"], case["months"])
        assert got == case["amount"], case


def test_u13_small_business() -> None:
    ps = _CASES["U13"]["periods"]
    start, end = _period("2026-04")
    res = compute_boxes(_lines(ps["2026-04"]["lines"], start), start, end, RULES, small_business=True)
    assert res.required is False
    assert res.result_cents == 0
    q2 = ps["2026-Q2"]
    res = _check(q2["lines"], "2026-Q2", q2["boxes"], small_business=True)
    assert res.required is True
    for kz in q2["no_box"]:
        assert res.box(kz) is None


def test_small_business_ignores_input_lines() -> None:
    start, end = _period("2026-05")
    res = compute_boxes(_lines(_CASES["U6"]["lines"], start), start, end, RULES, small_business=True)
    assert res.box("67") is None
    assert res.result_cents == 3810


def test_u15_partial_payments() -> None:
    for period, p in _CASES["U15"]["periods"].items():
        _check(p["lines"], period, p["boxes"])


def test_u16_amended_and_drift() -> None:
    c = _CASES["U16"]
    start, end = _period(c["period"])
    u1 = _CASES["U1"]["lines"]
    filed = compute_boxes(_lines(u1, start), start, end, RULES)
    assert filed.result_cents == c["filed"]["83"]
    late = _lines([c["late_line"]], date.fromisoformat(c["late_line"]["vat_date"]))
    amended = compute_boxes([*_lines(u1, start), *late], start, end, RULES)
    box81 = amended.box("81")
    box66 = amended.box("66")
    filed81 = filed.box("81")
    assert box81 is not None and box66 is not None and filed81 is not None
    assert (box81.base, box81.tax) == (c["amended"]["81"]["base"], c["amended"]["81"]["tax"])
    assert box66.amount == c["amended"]["66"]
    assert amended.result_cents == c["amended"]["83"]
    assert box81.booked_tax is not None and filed81.booked_tax is not None
    assert box81.booked_tax - filed81.booked_tax == c["drift_tax"]


def test_negative_base_truncates_toward_zero() -> None:
    start, end = _period("2026-04")
    ln = Line(Role("v"), Cents(5050), TaxKey("S19"), "tax", None, Cents(-30050), start)
    box = compute_boxes([ln], start, end, RULES).box("81")
    assert box is not None and box.base == -300 and box.booked_base == -30050


def test_period_boundaries_inclusive() -> None:
    start, end = _period("2026-03")

    def at(d: date) -> Line:
        return Line(Role("v"), Cents(-100), TaxKey("S19"), "tax", None, Cents(10000), d)

    lines = [at(end), at(start), at(start - timedelta(days=1)), at(end + timedelta(days=1))]
    box = compute_boxes(lines, start, end, RULES).box("81")
    assert box is not None and box.booked_base == 20000 and box.booked_tax == 200


def test_none_ku_and_undated_lines_ignored() -> None:
    start, end = _period("2026-03")
    lines = [
        Line(Role("a"), Cents(0), TaxKey("KU"), "base_only", None, Cents(500000), start),
        Line(Role("b"), Cents(-100), TaxKey("NONE"), None, None, None, start),
        Line(Role("c"), Cents(-100), TaxKey("S19"), "tax", None, Cents(10000), None),
    ]
    res = compute_boxes(lines, start, end, RULES)
    assert res.boxes.keys() == {"83"} and res.result_cents == 0


def test_special_prepayment_rejects_bad_months() -> None:
    with pytest.raises(ValueError):
        special_prepayment(100, 0, 0)
