"""Issue postings (accounting.md 9.1) against the hand-computed oracle ``fixtures/accounting_oracle.json``."""

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from permita38.core import calc
from permita38.core.accounting import postings_issue
from permita38.core.accounting.postings_issue import issue_entry, snapshot_from_document
from permita38.core.accounting.types import Bases, Cents, Entry, IssuedSnapshot, RateGroup
from permita38.core.vat_rules import TaxKey

ORACLE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "accounting_oracle.json").read_text(encoding="utf-8")
)
CASES: dict[str, dict[str, Any]] = {c["id"]: c for c in ORACLE["journal_cases"]}


def _events(case: dict[str, Any]) -> list[dict[str, Any]]:
    events = case["events"]
    if isinstance(events, str):
        ref = events.removeprefix("same as ").strip()
        return list(CASES[ref]["events"])
    return list(events)


def _d(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def _snapshot(event: dict[str, Any]) -> IssuedSnapshot:
    groups = sorted(
        (RateGroup(Decimal(r), Cents(v["net"]), Cents(v["vat"])) for r, v in event["rates"].items()),
        key=lambda g: g.rate,
        reverse=True,
    )
    return IssuedSnapshot(
        document_id=event["id"],
        regime="small_business" if event["treatment"] == "small_business" else "regular",
        treatment=event["treatment"],
        rates=tuple(groups),
        perf=_d(event.get("service_to")) or date.fromisoformat(event["date"]),
    )


def _run(case: dict[str, Any]) -> Entry | None:
    event = _events(case)[0]
    snap = _snapshot(event)
    inv = date.fromisoformat(event["date"])
    bases = Bases(case["bases"]["income_basis"], case["bases"]["vat_basis"])
    return issue_entry(snap, bases, event["treatment"], snap.perf, inv)


def _oracle_lines(entry: dict[str, Any]) -> list[tuple[Any, ...]]:
    out = []
    for row in entry["lines"]:
        opts = row[2] if len(row) > 2 else {}
        out.append(
            (
                row[0],
                row[1],
                opts.get("tax_key"),
                opts.get("tax_kind"),
                opts.get("tax_base"),
                opts.get("vat_date"),
                opts.get("income_date"),
            )
        )
    return out


def _actual_lines(entry: Entry) -> list[tuple[Any, ...]]:
    return [
        (
            ln.role,
            ln.amount_cents,
            ln.tax_key,
            ln.tax_kind,
            ln.tax_base_cents,
            ln.vat_date.isoformat() if ln.vat_date else None,
            ln.income_date.isoformat() if ln.income_date else None,
        )
        for ln in entry.lines
    ]


ISSUE_CASES = [
    "A1-accrual-soll",
    "A1-accrual-ist",
    "A1-eur-soll",
    "A1-eur-ist",
    "A2-eur-ist",
    "A3-accrual-soll",
    "A4-eur-ist",
    "A4-accrual-ist",
    "A7-eu-b2b-ist-unpaid",
    "A8-overpayment",
    "A9-write-off-soll",
    "A9-write-off-ist",
    "A11-credit-then-refunds-eur-ist",
    "A12-storno-after-payment-soll",
    "A13-payment-after-write-off-soll",
    "A16-late-ist-payment-after-lock",
    "A18-credit-moves-cash-accrual-ist",
    "A18-credit-moves-cash-eur-soll",
]


@pytest.mark.parametrize("case_id", ISSUE_CASES)
def test_issue_entry_matches_oracle(case_id: str) -> None:
    case = CASES[case_id]
    assert _events(case)[0]["type"] == "issue"
    entry = _run(case)
    if 0 in case.get("no_entry_events", []):
        assert entry is None
        return
    expected = next(e for e in case["entries"] if e["event"] == 0)
    assert entry is not None
    assert _actual_lines(entry) == _oracle_lines(expected)
    assert entry.booking_date.isoformat() == expected["booking_date"]
    assert entry.document_date == entry.booking_date
    assert entry.balance == 0
    assert entry.event_key == "issue:" + _events(case)[0]["id"]


def test_a2_soll_vat_sums() -> None:
    """A2 (Soll variant) lists only VAT sums: the issue entry books them in the performance month."""
    case = CASES["A2-accrual-soll-vat-only"]
    entry = _run(case)
    assert entry is not None
    booked = {ln.tax_key: (ln.tax_base_cents, -ln.amount_cents) for ln in entry.lines if ln.tax_kind == "tax"}
    expected = case["vat"]["2026-03"]
    assert booked == {TaxKey(k): (v["base"], v["tax"]) for k, v in expected.items()}
    assert all(ln.vat_date == date(2026, 3, 2) for ln in entry.lines if ln.tax_kind == "tax")
    assert entry.balance == 0


def test_receivables_line_carries_the_open_item() -> None:
    entry = _run(CASES["A1-accrual-soll"])
    assert entry is not None
    assert [ln.open_item_id for ln in entry.lines] == ["inv1", None, None]


def test_tax_base_sign_is_positive_for_a_sale_independent_of_side() -> None:
    entry = _run(CASES["A1-accrual-soll"])
    assert entry is not None
    tax = [ln for ln in entry.lines if ln.tax_kind == "tax"]
    assert [ln.amount_cents for ln in tax] == [-19000]  # credit
    assert [ln.tax_base_cents for ln in tax] == [100000]  # base in the sign of the VAT effect
    assert -tax[0].amount_cents > 0  # box value of a tax line is -amount


def test_eu_b2b_eur_is_a_statistical_zero_line() -> None:
    entry = _run(CASES["A7-eu-b2b-ist-unpaid"])
    assert entry is not None
    assert len(entry.lines) == 1
    line = entry.lines[0]
    assert (line.amount_cents, line.tax_key, line.tax_kind, line.tax_base_cents) == (0, "EU_SVC", "base_only", 400000)
    assert line.vat_date == date(2026, 3, 10)
    assert line.income_date is None


@pytest.mark.parametrize("vat", ["soll", "ist"])
def test_eu_b2b_accrual_follows_performance_for_both_vat_bases(vat: str) -> None:
    snap = _snapshot(_events(CASES["A7-eu-b2b-ist-unpaid"])[0])
    entry = issue_entry(snap, Bases("accrual", vat), "eu_b2b_service", date(2026, 3, 10), date(2026, 3, 15))  # type: ignore[arg-type]
    assert entry is not None
    assert _actual_lines(entry) == [
        ("receivables", 400000, None, None, None, None, None),
        ("revenue_eu_svc", -400000, "EU_SVC", "base_only", 400000, "2026-03-10", "2026-03-15"),
    ]


def test_small_business() -> None:
    snap = IssuedSnapshot(
        "k1", "small_business", "small_business", (RateGroup(Decimal(0), Cents(5000), Cents(0)),), date(2026, 5, 1)
    )
    inv = date(2026, 5, 3)
    entry = issue_entry(snap, Bases("accrual", "ist"), "small_business", snap.perf, inv)
    assert entry is not None
    assert _actual_lines(entry) == [
        ("receivables", 5000, None, None, None, None, None),
        ("revenue_ku", -5000, "KU", "base_only", 5000, "2026-05-03", "2026-05-03"),
    ]
    assert entry.balance == 0
    assert issue_entry(snap, Bases("eur", "ist"), "small_business", snap.perf, inv) is None


def test_rate_zero_on_a_standard_invoice_posts_on_revenue_s0() -> None:
    snap = IssuedSnapshot(
        "z1",
        "regular",
        "standard",
        (
            RateGroup(Decimal(19), Cents(100000), Cents(19000)),
            RateGroup(Decimal(0), Cents(2000), Cents(0)),
        ),
        date(2026, 4, 1),
    )
    inv = date(2026, 4, 2)
    soll = issue_entry(snap, Bases("accrual", "soll"), "standard", snap.perf, inv)
    assert soll is not None
    assert _actual_lines(soll) == [
        ("receivables", 121000, None, None, None, None, None),
        ("revenue_s19", -100000, None, None, None, None, "2026-04-02"),
        ("vat_out_s19", -19000, "S19", "tax", 100000, "2026-04-01", None),
        ("revenue_s0", -2000, "NONE", None, None, None, "2026-04-02"),
    ]
    assert soll.balance == 0
    eur = issue_entry(snap, Bases("eur", "soll"), "standard", snap.perf, inv)
    assert eur is not None
    assert [ln.role for ln in eur.lines] == ["vat_soll_clearing_s19", "vat_out_s19"]
    assert issue_entry(snap, Bases("eur", "ist"), "standard", snap.perf, inv) is None


@pytest.mark.parametrize("income", ["accrual", "eur"])
@pytest.mark.parametrize("vat", ["soll", "ist"])
@pytest.mark.parametrize("treatment", ["standard", "eu_b2b_service", "small_business"])
def test_every_entry_balances(income: str, vat: str, treatment: str) -> None:
    rates = (
        (RateGroup(Decimal(19), Cents(33333), Cents(6333)), RateGroup(Decimal("7"), Cents(1001), Cents(70)))
        if treatment == "standard"
        else (RateGroup(Decimal(0), Cents(12345), Cents(0)),)
    )
    snap = IssuedSnapshot("b1", "regular", treatment, rates, date(2026, 1, 1))  # type: ignore[arg-type]
    entry = issue_entry(snap, Bases(income, vat), treatment, snap.perf, date(2026, 1, 2))  # type: ignore[arg-type]
    if entry is not None:
        assert entry.balance == 0


def test_snapshot_rounds_vat_per_rate_group_like_doc_totals() -> None:
    doc = {
        "date": "2026-03-02",
        "lines": [
            {"qty": 1, "price": 3.33, "vat": 19},
            {"qty": 1, "price": 3.33, "vat": 19},
            {"qty": 2, "price": 250, "vat": 7},
        ],
    }
    settings: dict[str, object] = {"vat_rate": 19}
    snap = snapshot_from_document(doc, settings, document_id="d1", treatment="standard", invoice_date=date(2026, 3, 2))
    assert [(g.rate, g.net, g.vat, g.gross) for g in snap.rates] == [
        (Decimal(19), 666, 127, 793),  # 6.66 * 19 % = 1.2654 -> 1.27 (per line would give 1.26)
        (Decimal(7), 50000, 3500, 53500),
    ]
    assert snap.gross == 54293
    totals = calc.doc_totals(json.loads(json.dumps(doc)), settings)
    assert snap.gross == round(totals["gross"] * 100)
    assert snap.perf == date(2026, 3, 2)
    assert snap.regime == "regular"


def test_snapshot_small_business_forces_rate_zero_and_perf_date() -> None:
    doc = {"service_from": "2026-02-01", "service_to": "2026-02-20", "lines": [{"qty": 2, "price": 100.5, "vat": 19}]}
    snap = snapshot_from_document(
        doc, {"kleinunternehmer": True}, document_id="d2", treatment="standard", invoice_date=date(2026, 3, 1)
    )
    assert snap.regime == "small_business"
    assert snap.treatment == "small_business"
    assert [(g.rate, g.net, g.vat) for g in snap.rates] == [(Decimal(0), 20100, 0)]
    assert snap.perf == date(2026, 2, 20)
    only_from = {"service_from": "2026-02-01", "lines": []}
    assert snapshot_from_document(
        only_from, {}, document_id="d3", treatment="standard", invoice_date=date(2026, 3, 1)
    ).perf == date(2026, 2, 1)


def test_accounting_modules_use_no_float() -> None:
    folder = Path(postings_issue.__file__).parent
    for path in folder.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\bfloat\b|to_float", text), path.name
