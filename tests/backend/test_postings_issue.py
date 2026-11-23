"""Issue postings (accounting.md 9.1) against the hand-computed oracle ``fixtures/accounting_oracle.json``."""

import io
import json
import tokenize
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from permita38.core import calc
from permita38.core.accounting import postings_issue
from permita38.core.accounting.postings_issue import issue_entry, snapshot_from_document
from permita38.core.accounting.types import Bases, Cents, Entry, IssuedSnapshot, RateGroup, Treatment
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
        perf=_d(event.get("service_to")) or _d(event.get("service_from")) or date.fromisoformat(event["date"]),
    )


def _run(case: dict[str, Any]) -> Entry | None:
    event = _events(case)[0]
    snap = _snapshot(event)
    inv = date.fromisoformat(event["date"])
    bases = Bases(case["bases"]["income_basis"], case["bases"]["vat_basis"])
    return issue_entry(snap, bases, inv)


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
                opts.get("tax_rate"),
            )
        )
    return out


def _actual_lines(entry: Entry, oracle: list[tuple[Any, ...]] | None = None) -> list[tuple[Any, ...]]:
    """Lines as tuples; the oracle lists ``tax_rate`` only where the case is about it, so elsewhere it is not compared."""
    rows = [
        (
            ln.role,
            ln.amount_cents,
            ln.tax_key,
            ln.tax_kind,
            ln.tax_base_cents,
            ln.vat_date.isoformat() if ln.vat_date else None,
            ln.income_date.isoformat() if ln.income_date else None,
            ln.tax_rate,
        )
        for ln in entry.lines
    ]
    if oracle is not None:
        rows = [r if o[7] is not None else (*r[:7], None) for r, o in zip(rows, oracle, strict=True)]
    return rows


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
    "I1-mixed-rates-accrual-soll",
    "I1-mixed-rates-eur-soll",
    "I2-other-rate-accrual-soll",
    "I2-other-rate-accrual-ist",
    "I3-negative-invoice-accrual-soll",
    "I4-eu-b2b-accrual-soll",
    "I4-eu-b2b-accrual-ist",
    "I5-small-business-accrual",
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
    oracle = _oracle_lines(expected)
    assert _actual_lines(entry, oracle) == oracle
    invoice_id = _events(case)[0]["id"]
    for ln in entry.lines:
        assert ln.open_item_id == (invoice_id if ln.role == "receivables" else None), (case_id, ln.role)
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


NO_ENTRY = [
    ("eur", "ist", "standard"),
    ("eur", "soll", "small_business"),
    ("eur", "ist", "small_business"),
]


def test_i6_none_table_matches_the_fixture() -> None:
    case = CASES["I6-none-table"]
    assert {(r["income_basis"], r["vat_basis"], r["treatment"]) for r in case["no_issue_entry"]} == set(NO_ENTRY)
    assert case["every_other_combination_posts_an_entry"] is True


def _snap_for(treatment: Treatment) -> IssuedSnapshot:
    rates = (
        (RateGroup(Decimal(19), Cents(33333), Cents(6333)), RateGroup(Decimal(7), Cents(1001), Cents(70)))
        if treatment == "standard"
        else (RateGroup(Decimal(0), Cents(12345), Cents(0)),)
    )
    regime = "small_business" if treatment == "small_business" else "regular"
    return IssuedSnapshot("b1", regime, treatment, rates, date(2026, 1, 1))


@pytest.mark.parametrize("income", ["accrual", "eur"])
@pytest.mark.parametrize("vat", ["soll", "ist"])
@pytest.mark.parametrize("treatment", ["standard", "eu_b2b_service", "small_business"])
def test_none_exactly_in_the_none_table_and_every_entry_balances(income: str, vat: str, treatment: Treatment) -> None:
    entry = issue_entry(_snap_for(treatment), Bases(income, vat), date(2026, 1, 2))  # type: ignore[arg-type]
    if (income, vat, treatment) in NO_ENTRY:
        assert entry is None
    else:
        assert entry is not None
        assert entry.lines
        assert entry.balance == 0
        assert entry.event_key == "issue:b1"


def test_treatment_and_perf_come_from_the_snapshot() -> None:
    snap = IssuedSnapshot(
        "p1", "regular", "eu_b2b_service", (RateGroup(Decimal(0), Cents(1000), Cents(0)),), date(2026, 3, 10)
    )
    entry = issue_entry(snap, Bases("accrual", "ist"), date(2026, 3, 15))
    assert entry is not None
    assert [ln.role for ln in entry.lines] == ["receivables", "revenue_eu_svc"]
    assert entry.lines[1].vat_date == date(2026, 3, 10)


@pytest.mark.parametrize("treatment", ["standard", "eu_b2b_service", "small_business"])
@pytest.mark.parametrize("income", ["accrual", "eur"])
@pytest.mark.parametrize("vat", ["soll", "ist"])
def test_zero_amount_invoice_posts_nothing(income: str, vat: str, treatment: Treatment) -> None:
    snap = IssuedSnapshot("zero", "regular", treatment, (RateGroup(Decimal(19), Cents(0), Cents(0)),), date(2026, 1, 1))
    assert issue_entry(snap, Bases(income, vat), date(2026, 1, 2)) is None  # type: ignore[arg-type]
    empty = IssuedSnapshot("zero", "regular", treatment, (), date(2026, 1, 1))
    assert issue_entry(empty, Bases(income, vat), date(2026, 1, 2)) is None  # type: ignore[arg-type]


def test_i1_through_snapshot_from_document_rounds_per_rate_group() -> None:
    """I1 built from document lines: perf = service_from (no service_to), VAT rounded once per rate group."""
    event = _events(CASES["I1-mixed-rates-accrual-soll"])[0]
    doc = {
        "service_from": event["service_from"],
        "lines": [{"qty": ln["qty"], "price": ln["price"], "vat": ln["rate"]} for ln in event["lines"]],
    }
    inv = date.fromisoformat(event["date"])
    snap = snapshot_from_document(doc, {}, document_id=event["id"], treatment="standard", invoice_date=inv)
    assert snap.perf == date(2026, 3, 1)
    expected = sorted(
        ((Decimal(r), v["net"], v["vat"]) for r, v in event["rates"].items()), key=lambda t: t[0], reverse=True
    )
    assert [(g.rate, g.net, g.vat) for g in snap.rates] == expected
    for income in ("accrual", "eur"):
        case = CASES["I1-mixed-rates-" + income + "-soll"]
        entry = issue_entry(snap, Bases(income, "soll"), inv)  # type: ignore[arg-type]
        assert entry is not None
        oracle = _oracle_lines(next(e for e in case["entries"] if e["event"] == 0))
        assert _actual_lines(entry, oracle) == oracle


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
    """No ``float`` name anywhere under the accounting package (comments and docstrings are not code)."""
    folder = Path(postings_issue.__file__).parent
    files = sorted(folder.rglob("*.py"))
    assert files
    for path in files:
        source = path.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.NAME:
                assert tok.string != "float" and "to_float" not in tok.string, (path.name, tok.start)
            elif tok.type == tokenize.NUMBER:
                assert not (
                    ("." in tok.string or "e" in tok.string.lower()) and not tok.string.lower().startswith("0x")
                ), (
                    path.name,
                    tok.start,
                )
