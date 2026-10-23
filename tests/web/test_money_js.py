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
