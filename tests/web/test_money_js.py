"""The browser's money maths (web/money.js) must equal the server's (permita38/core/calc.py), cent for cent.

Random documents are totalled by the Python backend and by money.js running in Node; the results must be identical.
Skipped when Node.js is not installed."""

import json
import random
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from permita38.core import calc
from permita38.core.money import dec, q2, split_gross

NODE = shutil.which("node")
MONEY_JS = Path(__file__).resolve().parents[2] / "web" / "money.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

RUNNER = """
const fs = require('fs');
const Money = new Function(fs.readFileSync(process.argv[1], 'utf8') + '; return Money;')();
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = cases.map(c => {
  const t = Money.totals(c.doc, c.settings);
  const vat = {}; for (const [r, v] of Object.entries(t.vat)) vat[r] = Money.toNumber(v);
  const split = c.split ? Money.splitGross(c.split[0], c.split[1]) : null;
  const itemised = c.bill ? Money.splitLines(c.bill) : null;
  const totals = c.bill ? Money.lineTotals(c.bill) : null;
  return { net: Money.toNumber(t.net), vat, vatTotal: Money.toNumber(t.vatTotal), gross: Money.toNumber(t.gross),
           lines: t.lines.map(Money.toNumber), split, itemised, totals };
});
process.stdout.write(JSON.stringify(out));
"""


def _cases(n: int, seed: int):
    rnd = random.Random(seed)
    for _ in range(n):
        lines = []
        for _ in range(rnd.randint(0, 8)):
            line = {
                "qty": rnd.choice([1, 0.5, 1.5, 2.675, 1.005, rnd.randint(-20, 200) / rnd.choice([1, 4, 8, 1000])]),
                "price": rnd.choice([95, 621.5, 0.005, 19.995, rnd.randint(-5000, 90000) / rnd.choice([1, 100, 1000])]),
            }
            if rnd.random() < 0.4:
                line["vat"] = rnd.choice([0, 7, 19])
            lines.append(line)
        yield {
            "doc": {"vat_rate": rnd.choice([0, 7, 19]), "lines": lines},
            "settings": {"kleinunternehmer": rnd.random() < 0.15},
            "split": [rnd.randint(1, 900000) / 100, rnd.choice([0, 7, 19])],
        }


def _bill_cases(n: int, seed: int):
    """Random itemised bills: gross with up to 3 decimals (exact on both sides), rates from the UI and a few others."""
    rnd = random.Random(seed)
    for _ in range(n):
        bill = [
            [rnd.randint(0, 40000) / rnd.choice([100, 1000]), rnd.choice([19, 7, 0, 5.5])]
            for _ in range(rnd.randint(0, 12))
        ]
        yield bill


# Hand-checked line sets (docs/specs/itemised-expenses.md); the expected values are literals, not computed.
TABLE = [
    ([[160, 7], [30, 19], [6, 0]], [(149.53, 10.47), (25.21, 4.79), (6, 0)]),  # hotel: 7 % VAT is 10.47 in total
    ([[0.5, 7], [0.5, 7]], [(0.47, 0.03), (0.46, 0.04)]),  # group VAT 0,07, the later line gets the cent
    ([[0.04, 19]] * 5, [(0.04, 0), (0.04, 0), (0.03, 0.01), (0.03, 0.01), (0.03, 0.01)]),  # never negative
    ([[0.01, 7], [2.52, 7], [0, 7]], [(0.01, 0), (2.35, 0.17), (0, 0)]),  # per-line rounding would give 0.16
    ([[100, 19]], [(84.03, 15.97)]),  # business lunch
    ([], []),
]


def test_money_js_equals_the_python_backend():
    cases = list(_cases(400, seed=20261022))
    done = subprocess.run(
        [NODE, "-e", RUNNER, str(MONEY_JS)], input=json.dumps(cases), capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    assert len(got) == len(cases)
    for case, js in zip(cases, got):
        py = calc.doc_totals(json.loads(json.dumps(case["doc"])), case["settings"])
        assert js["net"] == py["net"], case
        assert js["vatTotal"] == py["vat_total"], case
        assert js["gross"] == py["gross"], case
        assert {float(k): v for k, v in js["vat"].items()} == py["vat"], case
        gross, rate = case["split"]
        net = q2(dec(gross) * 100 / (100 + dec(rate)))
        assert js["split"] == {"net": float(net), "vat": float(q2(dec(gross) - net))}, (case["split"], js["split"])


def test_money_js_itemised_split_equals_python_split_gross():
    bills = [b for b, _ in TABLE] + list(_bill_cases(300, seed=20261023))
    done = subprocess.run(
        [NODE, "-e", RUNNER, str(MONEY_JS)],
        input=json.dumps([{"doc": {}, "settings": {}, "bill": b} for b in bills]),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    for bill, js in zip(bills, json.loads(done.stdout)):
        py = split_gross([(dec(str(g)), dec(str(r))) for g, r in bill])
        assert js["itemised"] == [{"net": float(n), "vat": float(v)} for n, v in py], bill
        assert js["totals"]["vat"] == float(sum((v for _, v in py), dec(0))), bill
        gross_total = sum((dec(str(g)) for g, _ in bill), dec(0))
        assert js["totals"]["gross"] == float(gross_total), bill
        per_rate: dict[str, Decimal] = {}
        for (_, rate), (_, v) in zip(bill, py):
            per_rate[str(dec(str(rate)))] = per_rate.get(str(dec(str(rate))), dec(0)) + v
        assert js["totals"]["byRate"] == {k: float(v) for k, v in per_rate.items()}, bill


@pytest.mark.parametrize("bill, expected", TABLE)
def test_money_js_itemised_table_is_hand_checked(bill, expected):
    run = subprocess.run(
        [NODE, "-e", RUNNER, str(MONEY_JS)],
        input=json.dumps([{"doc": {}, "settings": {}, "bill": bill}]),
        capture_output=True,
        text=True,
        timeout=30,
    )
    got = [(x["net"], x["vat"]) for x in json.loads(run.stdout)[0]["itemised"]]
    assert got == expected


def test_money_js_rounding_examples():
    doc = {"vat_rate": 19, "lines": [{"qty": 1, "price": 621.5}]}
    run = subprocess.run(
        [NODE, "-e", RUNNER, str(MONEY_JS)],
        input=json.dumps([{"doc": doc, "settings": {}}]),
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = json.loads(run.stdout)[0]
    assert out["vat"] == {"19": 118.09} and out["gross"] == 739.59


# ---- decimal-string helpers (docs/specs/decimal-money-api.md rule 6); expected values are literals ----

HELPER_RUNNER = """
const fs = require('fs');
const Money = new Function(fs.readFileSync(process.argv[1], 'utf8') + '; return Money;')();
const calls = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = calls.map(([fn, ...args]) => { const r = Money[fn](...args); return typeof r === 'bigint' ? r.toString() : r; });
process.stdout.write(JSON.stringify(out));
"""


def _helpers(calls):
    done = subprocess.run(
        [NODE, "-e", HELPER_RUNNER, str(MONEY_JS)], input=json.dumps(calls), capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_money_js_input_parser_accepts_one_separator():
    typed = ["12.50", "12,50", "7", "-3,5", ".5", "0", " 12.5 ", "1234567890.123456", "0,000001"]
    got = _helpers([["parseInput", t] for t in typed])
    assert got == [
        {"ok": True, "value": "12.50"},
        {"ok": True, "value": "12.50"},
        {"ok": True, "value": "7"},
        {"ok": True, "value": "-3.5"},
        {"ok": True, "value": "0.5"},
        {"ok": True, "value": "0"},
        {"ok": True, "value": "12.5"},
        {"ok": True, "value": "1234567890.123456"},
        {"ok": True, "value": "0.000001"},
    ]


def test_money_js_input_parser_empty_field_is_null_not_an_error():
    assert _helpers([["parseInput", ""], ["parseInput", None]]) == [{"ok": True, "value": None}] * 2


@pytest.mark.parametrize(
    "typed",
    [
        "1.234,56",
        "1,234.56",
        "1,2,3",
        "1.2.3",
        " ",
        "\t",
        "abc",
        "12abc",
        "1e3",
        "--1",
        "+5",
        "12 50",
        ",",
        "-",
        "12345678901",
        "1.1234567",
    ],
)
def test_money_js_input_parser_refuses(typed):
    got = _helpers([["parseInput", typed]])[0]
    assert got["ok"] is False and got["error"]


def test_money_js_string_pitfalls():
    got = _helpers(
        [
            ["fmt", "0.00"],
            ["fmt", None],
            ["fmt", ""],
            ["fmt", "1234.505"],  # half-up to cents, not float rounding
            ["fmt", "-0.004"],
            ["sum", ["0.10", "0.20", "19.99"]],  # `+` on strings would concatenate; floats give 0.30000000000000004
            ["sum", []],
            ["sum", ["0.1", 0.2, None]],
            ["cmp", "9.00", "10.00"],  # text order would say 9.00 > 10.00
            ["cmp", "-5.00", 0],
            ["cmp", "1.10", "1.1"],
            ["isNeg", "-0.01"],
            ["isNeg", "0.00"],
            ["isZero", "0.00"],  # "0.00" is truthy in JS: callers must use isZero
            ["abs", "-12.5"],
            ["str", 12.5],
            ["str", "0.125"],
            ["str", None],
            ["cents", "1.005"],
            ["fromCents", "-1205"],
            ["pct", "10.00", 70],
            ["pct", "0.05", 50],
        ]
    )
    nbsp = " "
    assert got == [
        f"0,00{nbsp}€",
        f"0,00{nbsp}€",
        f"0,00{nbsp}€",
        f"1.234,51{nbsp}€",
        f"0,00{nbsp}€",
        "20.29",
        "0.00",
        "0.30",
        -1,
        -1,
        0,
        True,
        False,
        True,
        "12.50",
        "12.50",
        "0.125",
        "0.00",
        "101",
        "-12.05",
        "700",
        "3",
    ]


def test_money_js_doc_gross_string_matches_the_totals():
    doc = {"vat_rate": 19, "lines": [{"qty": 1, "price": "621.50"}]}
    assert _helpers([["docGrossStr", doc, {}]]) == ["739.59"]
    assert _helpers([["splitGrossStr", "119.00", 19]]) == [{"net": "100.00", "vat": "19.00"}]


@pytest.mark.parametrize(
    ("doc", "settings"),
    [
        ({"vat_rate": "", "lines": [{"qty": 1, "price": "100.00"}]}, {}),  # '' = default 19 %, not 0 %
        ({"vat_rate": "", "lines": [{"qty": 1, "price": "100.00"}]}, {"vat_rate": 7}),  # default = the settings rate
        ({"vat_rate": None, "lines": [{"qty": 1, "price": "100.00"}]}, {"vat_rate": 7}),
        ({"vat_rate": 7, "lines": [{"qty": 1, "price": "100.00", "vat": ""}]}, {}),  # line '' = the document rate
        ({"vat_rate": "", "lines": [{"qty": 1, "price": "100.00", "vat": ""}, {"qty": 2, "price": 0.125, "vat": 0}]}, {}),
        ({"vat_rate": 0, "lines": [{"qty": 1, "price": "100.00"}]}, {}),  # a real 0 stays 0 %
        ({"vat_rate": "", "lines": [{"qty": 1, "price": "100.00", "vat": ""}]}, {"kleinunternehmer": True}),
    ],
)
def test_money_js_empty_vat_means_the_default_like_the_server(doc, settings):
    py = calc.doc_totals(json.loads(json.dumps(doc)), settings)
    got = _helpers([["docGrossStr", doc, settings]])[0]
    assert got == format(q2(dec(py["gross"])), "f")


def test_money_js_empty_vat_hand_numbers():
    doc = {"vat_rate": "", "lines": [{"qty": 1, "price": "100.00"}]}
    assert _helpers([["docGrossStr", doc, {}]]) == ["119.00"]  # not 100.00 (0 %)
    assert _helpers([["docGrossStr", doc, {"vat_rate": 7}]]) == ["107.00"]
