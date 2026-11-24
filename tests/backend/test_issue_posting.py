"""Issue snapshot and issue posting (docs/specs/accounting.md 9.1, 11, 16; credit-notes-payments.md 1, 5).

The expected entries are read from the hand-computed oracle ``fixtures/accounting_oracle.json`` (never edited here).
"""

import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from permita38.core import calc, charts
from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.repository import create_repository
from permita38.core.service.journal import JournalService
from permita38.core.service.locks import LockService
from permita38.core.service.records import RecordService
from permita38.core.service.sales import SalesService
from permita38.core.settings import ACCOUNTING_START_KEY

FIXTURES = Path(__file__).parent / "fixtures"
ORACLE: dict[str, Any] = json.loads((FIXTURES / "accounting_oracle.json").read_text(encoding="utf-8"))
CASES: dict[str, dict[str, Any]] = {c["id"]: c for c in ORACLE["journal_cases"]}


def fiscal_year(year: int, income: str = "accrual", vat: str = "soll", small_business: bool = False) -> dict:
    return {
        "year": year, "chart": "skr03", "income_basis": income, "vat_basis": vat, "vat_frequency": "month",
        "dauerfrist": False, "small_business": small_business, "rules_version": "skr03-2026/vat-2026",
        "opened_at": 1, "opened_by": "u1x000",
    }  # fmt: skip


@pytest.fixture
def repo(tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURES / "chart_synthetic.json", charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    r = create_repository(Config(data_dir=tmp_path / "data", secret_key="x" * 16))
    r.startup()
    yield r
    r.close()


@pytest.fixture
def sales(repo):
    return SalesService(repo, RecordService(repo))


def init(repo, start: str | None = "2026-01-01", **fy) -> None:
    repo.save("fiscal_years", fiscal_year(2026, **fy))
    if start:
        repo.save_settings({ACCOUNTING_START_KEY: start})


def customer(repo, **extra) -> dict:
    return repo.save("customers", dict(name="Demo Client GmbH", vat_id="FR12345678901", country="FR", **extra))


def draft(repo, day: str, lines: list[dict], **extra) -> dict:
    cust = extra.pop("customer", None) or customer(repo)
    return repo.save("invoices", dict(customer_id=cust["id"], date=day, lines=lines, **extra))


def line(net_eur: str, vat: str | None = "19") -> dict:
    out = dict(description="Work", qty=1, price=net_eur)
    if vat is not None:
        out["vat"] = vat
    return out


def entries(repo) -> list:
    return JournalService(repo).entries(2026)


def cents(value: Any) -> int:
    return int(Decimal(str(value)) * 100)


def entry_rows(entry) -> list[tuple[Any, ...]]:
    return [
        (
            ln.role,
            cents(ln.amount),
            ln.tax_key,
            ln.tax_kind,
            None if ln.tax_base is None else cents(ln.tax_base),
            ln.vat_date,
            ln.income_date,
        )
        for ln in entry.lines
    ]


def oracle_rows(entry: dict[str, Any]) -> list[tuple[Any, ...]]:
    rows = []
    for row in entry["lines"]:
        o = row[2] if len(row) > 2 else {}
        rows.append(
            (
                row[0],
                row[1],
                o.get("tax_key"),
                o.get("tax_kind"),
                o.get("tax_base"),
                o.get("vat_date"),
                o.get("income_date"),
            )
        )
    return rows


def events(case: dict[str, Any]) -> list[dict[str, Any]]:
    ev = case["events"]
    return list(CASES[ev.removeprefix("same as ").strip()]["events"]) if isinstance(ev, str) else list(ev)


def invoice_from_event(repo, ev: dict[str, Any]) -> dict:
    """A draft whose live totals equal the oracle's rate groups (one line per rate group unless the case has lines)."""
    if "lines" in ev:
        lines = [dict(description="x", qty=ln["qty"], price=ln["price"], vat=ln["rate"]) for ln in ev["lines"]]
    else:
        lines = [
            dict(description="x", qty=1, price=str(Decimal(v["net"]) / 100), vat=r) for r, v in ev["rates"].items()
        ]
    extra = {k: ev[k] for k in ("service_to", "service_from") if k in ev}
    if ev["treatment"] != "standard":
        extra["tax_treatment"] = ev["treatment"]
    return draft(repo, ev["date"], lines, **extra)


# cases whose first event is an issue; the oracle lists the issue entry or marks event 0 as "no entry"
ISSUE_CASES = [
    "A1-accrual-soll",
    "A1-accrual-ist",
    "A1-eur-soll",
    "A1-eur-ist",
    "A2-eur-ist",
    "A3-accrual-soll",
    "A7-eu-b2b-ist-unpaid",
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
def test_finalize_posts_the_oracle_issue_entry(repo, sales, case_id):
    case = CASES[case_id]
    b = case["bases"]
    init(repo, income=b["income_basis"], vat=b["vat_basis"], small_business=b.get("small_business", False))
    ev = events(case)[0]
    inv = invoice_from_event(repo, ev)
    sales.finalize("invoices", inv["id"])
    posted = entries(repo)
    if 0 in case.get("no_entry_events", []):
        assert posted == []
        return
    expected = next(e for e in case["entries"] if e["event"] == 0)
    assert len(posted) == 1
    assert entry_rows(posted[0]) == oracle_rows(expected)
    assert posted[0].booking_date == expected["booking_date"]
    assert posted[0].event_key == f"issue:{inv['id']}"
    assert posted[0].document_date == ev["date"]
    assert posted[0].source_coll == "invoices" and posted[0].source_id == inv["id"]
    assert [ln.open_item_id for ln in posted[0].lines if ln.role == "receivables"] in ([inv["id"]], [])


@pytest.mark.parametrize("income", ["eur", "accrual"])
@pytest.mark.parametrize("vat", ["soll", "ist"])
@pytest.mark.parametrize("treatment", ["standard", "small_business", "eu_b2b_service"])
def test_i6_matrix_rows(repo, sales, income, vat, treatment):
    """I6: exactly the three listed (basis, treatment) combinations post nothing; every other one posts an entry."""
    table = CASES["I6-none-table"]
    silent = {(r["income_basis"], r["vat_basis"], r["treatment"]) for r in table["no_issue_entry"]}
    init(repo, income=income, vat=vat, small_business=treatment == "small_business")
    extra = {} if treatment == "standard" else {"tax_treatment": treatment}
    inv = draft(repo, "2026-03-10", [line("100.00", "19" if treatment == "standard" else None)], **extra)
    sales.finalize("invoices", inv["id"])
    if (income, vat, treatment) in silent:
        assert entries(repo) == []
    else:
        # EÜR/Ist/eu_b2b_service posts the statistical line; every other row posts a real entry
        assert len(entries(repo)) == 1


# ---------------------------------------------------------------- snapshot
def test_snapshot_content(repo, sales):
    init(repo)
    cust = customer(repo)
    inv = draft(
        repo, "2026-03-10", [line("100.00", "19"), line("50.00", "7")], customer=cust, service_from="2026-03-01",
        service_to="2026-03-05",
    )  # fmt: skip
    out = sales.finalize("invoices", inv["id"])
    snap = repo.get("invoices", inv["id"])["issued"]
    assert out["issued"] == snap
    assert snap["regime"] == "regular" and snap["treatment"] == "standard"
    assert snap["rates"] == {
        "19": {"net": 10000, "vat": 1900, "gross": 11900},
        "7": {"net": 5000, "vat": 350, "gross": 5350},
    }
    assert snap["gross"] == 17250
    assert snap["perf"] == "2026-03-05"  # service_to wins
    assert snap["customer"] == {
        "id": cust["id"],
        "name": "Demo Client GmbH",
        "vat_id": "FR12345678901",
        "country": "FR",
    }
    assert isinstance(snap["issued_at"], int) and snap["rules_version"] == "skr03-2026/vat-2026"


def test_perf_falls_back_to_service_from_then_the_invoice_date(repo, sales):
    init(repo, start=None)
    a = sales.finalize("invoices", draft(repo, "2026-03-10", [line("1.00")], service_from="2026-03-02")["id"])
    b = sales.finalize("invoices", draft(repo, "2026-03-11", [line("1.00")])["id"])
    assert a["issued"]["perf"] == "2026-03-02" and b["issued"]["perf"] == "2026-03-11"


def test_snapshot_is_frozen_and_doc_totals_uses_it(repo, sales):
    init(repo)
    inv = draft(repo, "2026-03-10", [line("100.00", "19")])
    live = calc.doc_totals(dict(repo.get("invoices", inv["id"])), repo.settings())
    assert live["gross"] == 119.0
    sales.finalize("invoices", inv["id"])
    first = repo.get("invoices", inv["id"])
    repo.save_settings({"vat_rate": 7, "kleinunternehmer": True})
    t = calc.doc_totals(dict(first), {"vat_rate": 7, "kleinunternehmer": True})
    assert (t["net"], t["vat_total"], t["gross"]) == (100.0, 19.0, 119.0)
    assert t["vat"] == {19.0: 19.0} and t["by_rate"] == {19.0: 100.0}
    sales.finalize("invoices", inv["id"])  # re-finalize never rewrites the snapshot
    assert repo.get("invoices", inv["id"])["issued"] == first["issued"]
    draft_totals = calc.doc_totals({"lines": [line("100.00", "19")]}, {"kleinunternehmer": True})
    assert draft_totals["gross"] == 100.0  # drafts stay live


def test_small_business_year_forces_treatment_and_rate_zero(repo, sales):
    init(repo, income="accrual", vat="ist", small_business=True)
    inv = draft(repo, "2026-03-10", [line("100.00", "19")])
    snap = sales.finalize("invoices", inv["id"])["issued"]
    assert snap["regime"] == "small_business" and snap["treatment"] == "small_business"
    assert snap["rates"] == {"0": {"net": 10000, "vat": 0, "gross": 10000}} and snap["gross"] == 10000
    assert [e.lines[1].role for e in entries(repo)] == ["revenue_ku"]


def test_snapshot_written_even_before_initialisation(repo, sales):
    inv = draft(repo, "2026-03-10", [line("100.00")])
    out = sales.finalize("invoices", inv["id"])
    assert out["issued"]["gross"] == 11900 and out["issued"]["rules_version"] is None


@pytest.mark.parametrize(
    "value",
    [
        "reverse_charge_domestic",
        "eu_b2b_goods_exempt",
        "export_exempt",
        "non_taxable_other",
        "exempt_with_deduction",
        "exempt_no_deduction",
    ],
)
def test_reserved_treatment_is_refused(repo, value):
    """The storage guard already refuses these values; the snapshot builder refuses them too (422)."""
    from datetime import date

    from permita38.core.service.postings import build_snapshot

    doc = {"id": "inv1abc", "lines": [line("100.00")], "tax_treatment": value}
    with pytest.raises(ServiceError) as exc:
        build_snapshot(repo, doc, repo.settings(), date(2026, 3, 10))
    assert exc.value.status == 422


def test_mixed_sign_invoice_is_refused(repo, sales):
    init(repo)
    inv = draft(repo, "2026-03-10", [line("100.00", "19"), line("-50.00", "7")])
    with pytest.raises(ServiceError) as exc:
        sales.finalize("invoices", inv["id"])
    assert exc.value.status == 422


# ---------------------------------------------------------------- when nothing posts
def test_nothing_posts_before_initialisation(repo, sales):
    repo.save("fiscal_years", fiscal_year(2026))
    sales.finalize("invoices", draft(repo, "2026-03-10", [line("100.00")])["id"])
    assert entries(repo) == []


def test_invoice_before_the_start_does_not_post(repo, sales):
    init(repo, start="2026-04-01")
    before = sales.finalize("invoices", draft(repo, "2026-03-31", [line("100.00")])["id"])
    assert entries(repo) == [] and before["issued"]["gross"] == 11900
    sales.finalize("invoices", draft(repo, "2026-04-01", [line("100.00")])["id"])
    assert len(entries(repo)) == 1


def test_refinalize_is_idempotent(repo, sales):
    init(repo)
    inv = draft(repo, "2026-03-10", [line("100.00")])
    first = sales.finalize("invoices", inv["id"])
    again = sales.finalize("invoices", inv["id"])
    assert again["number"] == first["number"] and again["issued"] == first["issued"]
    assert len(entries(repo)) == 1


# ---------------------------------------------------------------- locks
def lock_through(repo, month: int) -> None:
    from datetime import date

    LockService(repo, today=lambda: date(2026, 6, 15)).lock_up_to(2026, month, "manual", "u1x000")


def test_back_dated_issue_into_a_locked_month_is_409(repo, sales):
    init(repo)
    lock_through(repo, 1)
    inv = draft(repo, "2026-01-20", [line("100.00")])
    with pytest.raises(ServiceError) as exc:
        sales.finalize("invoices", inv["id"])
    assert exc.value.status == 409
    stored = repo.get("invoices", inv["id"])  # rolled back: still a draft without number or snapshot
    assert stored.get("number") is None and not stored.get("issued") and stored.get("status", "draft") == "draft"
    assert entries(repo) == []
    ok = sales.finalize("invoices", draft(repo, "2026-02-01", [line("100.00")])["id"])
    assert ok["number"]


def test_back_dated_issue_without_initialisation_is_allowed(repo, sales):
    repo.save("fiscal_years", fiscal_year(2026))
    lock_through(repo, 1)
    assert sales.finalize("invoices", draft(repo, "2026-01-20", [line("100.00")])["id"])["number"]


def test_soll_issue_with_performance_in_a_locked_month_drifts(repo, sales):
    init(repo)
    lock_through(repo, 1)
    inv = draft(repo, "2026-02-10", [line("100.00")], service_to="2026-01-20")
    sales.finalize("invoices", inv["id"])
    (entry,) = entries(repo)
    assert entry.booking_date == "2026-02-10"
    vat_line = next(ln for ln in entry.lines if ln.role == "vat_out_s19")
    assert vat_line.vat_date == "2026-01-20"  # keeps the locked date
    assert len(entry.warnings) == 1 and "drift" in entry.warnings[0]


# ---------------------------------------------------------------- review fixes
USER = {"id": "u7x000", "username": "mia"}


def test_finalize_posts_with_the_acting_user(repo, sales):
    init(repo)
    inv = draft(repo, "2026-03-10", [line("100.00")])
    sales.finalize("invoices", inv["id"], USER)
    assert [e.created_by for e in entries(repo)] == ["u7x000"]


def test_finalize_route_passes_the_user_to_the_journal(app, boss_client, tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURES / "chart_synthetic.json", charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    store = app.state.services.store
    init(store)
    cust = customer(store)
    inv = draft(store, "2026-03-10", [line("100.00")], customer=cust)
    me = boss_client.get("/api/auth/me").json()
    r = boss_client.post(f"/api/invoices/{inv['id']}/finalize")
    assert r.status_code == 200, r.text
    assert [e.created_by for e in entries(store)] == [me["id"]]


def test_first_january_invoice_opens_the_new_year_in_the_same_transaction(repo, sales):
    init(repo, income="accrual", vat="ist")
    fy26 = repo.find("fiscal_years", year=2026)[0]
    repo.save("fiscal_years", {**fy26, "dauerfrist": True, "vat_frequency": "quarter"})
    inv = draft(repo, "2027-01-05", [line("100.00")])
    sales.finalize("invoices", inv["id"], USER)
    fy = repo.find("fiscal_years", year=2027)[0]
    assert (fy["chart"], fy["income_basis"], fy["vat_basis"]) == ("skr03", "accrual", "ist")
    assert (fy["vat_frequency"], fy["dauerfrist"], fy["small_business"]) == ("quarter", True, False)
    assert fy["opened_by"] == "u7x000"
    posted = JournalService(repo).entries(2027)
    assert len(posted) == 1 and posted[0].created_by == "u7x000"
    assert repo.get("invoices", inv["id"])["issued"]["rules_version"] == fy["rules_version"]


def test_new_year_copies_small_business(repo, sales):
    init(repo, small_business=True)
    inv = draft(repo, "2027-01-05", [line("100.00", "19")])
    out = sales.finalize("invoices", inv["id"], USER)
    assert repo.find("fiscal_years", year=2027)[0]["small_business"] is True
    assert out["issued"]["regime"] == "small_business"


def test_year_gap_is_409_and_rolls_back(repo, sales):
    init(repo)
    inv = draft(repo, "2028-01-05", [line("100.00")])
    with pytest.raises(ServiceError) as exc:
        sales.finalize("invoices", inv["id"], USER)
    assert exc.value.status == 409
    assert repo.find("fiscal_years", year=2028) == [] and repo.find("fiscal_years", year=2027) == []
    assert not repo.get("invoices", inv["id"]).get("number")


def test_year_before_the_earliest_is_409(repo, sales):
    init(repo, start="2025-01-01")
    inv = draft(repo, "2025-12-30", [line("100.00")])
    with pytest.raises(ServiceError) as exc:
        sales.finalize("invoices", inv["id"], USER)
    assert exc.value.status == 409


def test_finalize_stores_the_date_it_posted_with(repo, sales):
    init(repo)
    inv = repo.save("invoices", dict(customer_id=customer(repo)["id"], lines=[line("100.00")]))
    assert not inv.get("date")
    out = sales.finalize("invoices", inv["id"], USER)
    today = date.today().isoformat()
    assert out["date"] == repo.get("invoices", inv["id"])["date"] == today


def test_group_vat_is_rounded_once_not_per_line(repo, sales):
    """Three 0.03 lines at 19 %: group net 9 ct, VAT 9 * 0.19 = 1.71 -> 2 ct (per line 0.0057 -> 1 ct each = 3 ct)."""
    init(repo)
    inv = draft(repo, "2026-03-10", [line("0.03")] * 3)
    snap = sales.finalize("invoices", inv["id"], USER)["issued"]
    assert snap["rates"] == {"19": {"net": 9, "vat": 2, "gross": 11}} and snap["gross"] == 11
    live = calc.doc_totals(dict(repo.get("invoices", inv["id"])), repo.settings())
    assert (live["net"], live["vat_total"], live["gross"]) == (0.09, 0.02, 0.11)


def test_line_amounts_round_half_up_before_the_group_sum(repo, sales):
    """Two lines 0.5 x 0.05 = 0.025 -> 0.03 each (half-up): net 6 ct, VAT 6 * 0.19 = 1.14 -> 1 ct."""
    init(repo)
    inv = draft(repo, "2026-03-10", [dict(description="x", qty="0.5", price="0.05", vat="19")] * 2)
    snap = sales.finalize("invoices", inv["id"], USER)["issued"]
    assert snap["rates"] == {"19": {"net": 6, "vat": 1, "gross": 7}}


def _pdf_texts(repo, monkeypatch, doc) -> str:
    from permita38.core import documents

    seen: list[str] = []
    orig = documents.Pdf.text

    def spy(self, x, y, s, *a, **k):
        seen.append(str(s))
        return orig(self, x, y, s, *a, **k)

    monkeypatch.setattr(documents.Pdf, "text", spy)
    documents.render(repo, "invoice", doc)
    return " ".join(seen)


def test_pdf_note_follows_the_snapshot_not_the_setting(repo, sales, monkeypatch):
    from permita38.core.documents import LABELS

    note = LABELS["de"]["klein"].split()[0]
    init(repo, small_business=True)
    inv = draft(repo, "2026-03-10", [line("100.00", "19")], language="de")
    sales.finalize("invoices", inv["id"], USER)
    repo.save_settings({"kleinunternehmer": False})
    assert note in _pdf_texts(repo, monkeypatch, repo.get("invoices", inv["id"]))


def test_pdf_vat_invoice_never_prints_the_note_after_the_setting_changes(repo, sales, monkeypatch):
    from permita38.core.documents import LABELS

    note = LABELS["de"]["klein"].split()[0]
    init(repo)
    inv = draft(repo, "2026-03-10", [line("100.00", "19")], language="de")
    sales.finalize("invoices", inv["id"], USER)
    repo.save_settings({"kleinunternehmer": True})
    doc = repo.get("invoices", inv["id"])
    assert note not in _pdf_texts(repo, monkeypatch, doc)
    assert note in _pdf_texts(repo, monkeypatch, {k: v for k, v in doc.items() if k != "issued"})  # a draft is live
