# ruff: noqa: F401, F811  (fixtures imported from test_vat_returns)
"""VAT return payments and the Sondervorauszahlung (docs/specs/ustva.md "Sondervorauszahlung", accounting.md 9.6).
The figures of U10 come from fixtures/accounting_oracle.json."""

from datetime import date
from typing import Any

import pytest

from permita38.core.errors import ServiceError
from permita38.core.service.special_prepayment import SpecialPrepaymentService
from permita38.core.service.tax_bookings import TaxBookingService
from permita38.core.settings import ACCOUNTING_START_KEY

from .test_vat_returns import (  # noqa: F401  (fixtures)
    CASES,
    USER,
    clients,
    fiscal_year,
    journal,
    make_service,
    money,
    post_case,
    repo,
)
from .test_zm_reports import synthetic_charts  # noqa: F401  (fixture)

U10 = CASES["U10-december"]
U10_SP = CASES["U10-special-prepayment"]
LATE = date(2027, 1, 15)  # December 2026 has ended


@pytest.fixture
def books(repo):
    repo.save_settings({ACCOUNTING_START_KEY: "2026-01-01"})
    return TaxBookingService(repo)


@pytest.fixture
def sp(repo, books):
    return SpecialPrepaymentService(repo, books)


def december_service(repo, books):
    """A year 2026 with Dauerfrist and a service that finalises December."""
    row = repo.find("fiscal_years", year=2026)[0]
    repo.save("fiscal_years", {**row, "dauerfrist": True})
    return make_service(repo, today=LATE)


def credits(repo) -> list[tuple[str, int]]:
    """(event key, amount of the vat_prepayments line) of every credit entry, reversals included."""
    out = []
    for e in repo.list_journal_entries(year=2026, limit=1000):
        if str(e["event_key"]).startswith("vat:") and ":credit" in str(e["event_key"]):
            out.append(
                (e["event_key"], sum(int(x["amount_cents"]) for x in e["lines"] if x["role"] == "vat_prepayments"))
            )
    return out


# ------------------------------------------------------------------ proposal (U10)
def previous_year(repo, kz83_sum: int, kz39: int, months: int) -> None:
    """Final monthly returns of 2025 whose Kz 83 add up to ``kz83_sum``; Kz 39 on the last one."""
    repo.save("fiscal_years", fiscal_year(2025))
    for no in range(1, months + 1):
        last = no == months
        repo.save(
            "vat_returns",
            {
                "year": 2025, "period_kind": "month", "period_no": no, "from_date": f"2025-{no:02d}-01",
                "to_date": f"2025-{no:02d}-28", "due_date": f"2025-{no:02d}-28", "status": "final", "amends_key": "",
                "finalised_at": 1, "finalised_by": "u1x000", "boxes": {}, "source_lines": [], "rounding_difference_cents": 0,
                "result_cents": kz83_sum if last else 0, "special_prepayment_credit_cents": kz39 if last else 0,
            },
        )  # fmt: skip


@pytest.mark.parametrize("case", U10_SP["cases"], ids=lambda c: f"{c['previous_kz83_sum']}-{c['months']}")
def test_u10_proposal_from_the_previous_year(sp, repo, case):
    previous_year(repo, case["previous_kz83_sum"], case["previous_kz39"], case["months"])
    got = sp.get(2026)
    assert got["proposal"] == money(case["amount"]) and got["months"] == case["months"]
    assert got["due_date"] == U10_SP["due"]["2026"] and got["amount"] is None and got["payment"] is None


def test_proposal_without_returns_is_zero_and_amended_returns_replace_originals(sp, repo):
    assert sp.get(2026)["proposal"] == "0.00" and sp.get(2026)["months"] is None
    previous_year(repo, 660000, 0, 12)
    last = [r for r in repo.find("vat_returns", year=2025) if r["period_no"] == 12][0]
    new = repo.save(
        "vat_returns", {**last, "id": None, "amends_key": last["id"], "amends_id": last["id"], "result_cents": 330000}
    )
    repo.save("vat_returns", {**last, "replaced_by": new["id"]})
    assert sp.get(2026)["proposal"] == "300.00"  # 3,300.00 / 11


def test_put_stores_the_value_and_the_payment_once(sp, repo, books):
    repo.save("fiscal_years", fiscal_year(2027))
    sp2 = sp
    got = sp2.put(2026, 600.0, None, USER)
    assert got["amount"] == "600.00" and got["payment"] is None
    paid = sp2.put(2026, 600.0, "2026-02-10", USER)
    assert paid["payment"]["paid_on"] == "2026-02-10" and paid["payment"]["reversed"] is False
    sp2.put(2026, 600.0, "2026-02-10", USER)  # replay: still one entry
    entries = [e for e in repo.list_journal_entries(year=2026, limit=100) if e["event"] == "special_prepayment"]
    assert len(entries) == 1
    assert [(x["role"], x["amount_cents"]) for x in entries[0]["lines"]] == [
        ("vat_special_prepayment", 60000),
        ("bank", -60000),
    ]
    with pytest.raises(ServiceError) as err:
        sp2.put(2026, 500.0, "2026-02-10", USER)  # other content under the same entry
    assert err.value.status == 409
    with pytest.raises(ServiceError) as err:
        sp2.put(2030, 1.0, None, USER)
    assert err.value.status == 422
    with pytest.raises(ServiceError) as err:
        sp2.put(2026, 1.005, None, USER)
    assert err.value.status == 422


# ------------------------------------------------------------------ Kz 39 in the December return (U10-december)
def test_u10_december_credit_and_reopen(repo, books, sp, journal):
    svc = december_service(repo, books)
    sp.put(2026, U10["special_prepayment"] / 100, None, USER)
    post_case(journal, U10, date(2026, 12, 15))
    pre = svc.preview(2026, "month", 12)
    assert pre["special_prepayment_credit_cents"] == 60000 and pre["boxes"]["39"] == {"amount": "600.00"}
    d = svc.create_draft(2026, "month", 12)
    fin = svc.finalize(d["id"], USER, confirm_gap=True)
    assert fin["result_cents"] == U10["boxes"]["83"] == 0 and fin["special_prepayment_credit_cents"] == 60000
    assert fin["boxes"]["81"]["tax"] == "997.69" and fin["boxes"]["86"]["tax"] == "2.31"
    assert credits(repo) == [(f"vat:{d['id']}:credit", 60000)]
    # reopen: the credit is reversed, Kz 39 shows again in the draft; finalising again books it once more
    again = svc.reopen(d["id"], USER)
    assert again["special_prepayment_credit_cents"] == 60000
    assert credits(repo) == [(f"vat:{d['id']}:credit", 60000), (f"vat:{d['id']}:credit:rev", -60000)]
    svc.finalize(d["id"], USER, confirm_gap=True)
    assert sum(a for _k, a in credits(repo)) == 60000 and len(credits(repo)) == 3
    svc.reopen(d["id"], USER)
    assert sum(a for _k, a in credits(repo)) == 0


def test_u10_december_variant_is_a_refund_of_150(repo, books, sp, journal):
    svc = december_service(repo, books)
    sp.put(2026, 600.0, None, USER)
    case = {**U10, "lines": [dict(s, input=U10["variant"]["VST input"]) if "input" in s else s for s in U10["lines"]]}
    post_case(journal, case, date(2026, 12, 15))
    fin = svc.finalize(svc.create_draft(2026, "month", 12)["id"], USER, confirm_gap=True)
    assert fin["result_cents"] == U10["variant"]["83"] == -15000
    fin = svc.get(fin["id"])
    assert fin["payment_state"] == "open" and fin["open"] == "-150.00"


def test_no_credit_without_dauerfrist_in_other_periods_or_amended(repo, books, sp, journal):
    sp.put(2026, 600.0, None, USER)
    svc = make_service(repo, today=LATE)  # no Dauerfrist
    assert svc.preview(2026, "month", 12)["special_prepayment_credit_cents"] == 0
    svc = december_service(repo, books)
    assert svc.preview(2026, "month", 11)["special_prepayment_credit_cents"] == 0
    d = svc.finalize(svc.create_draft(2026, "month", 12)["id"], USER, confirm_gap=True)
    svc.filed(d["id"], "2027-01-08", None, None)
    am = svc.create_draft(2026, "month", 12, amends_id=d["id"])
    assert am["special_prepayment_credit_cents"] == 60000
    svc.finalize(am["id"], USER)
    assert credits(repo) == [(f"vat:{d['id']}:credit", 60000)]  # the amended return does not book it again


# ------------------------------------------------------------------ payments
def final_return(repo, books, journal, case=None):
    svc = make_service(repo, today=date(2026, 6, 15))
    post_case(journal, case or CASES["U1"], date(2026, 1, 15))
    d = svc.create_draft(2026, "month", 1)
    return svc, svc.finalize(d["id"], USER)


def test_payment_refund_and_state_on_a_final_return(repo, books, journal):
    svc, fin = final_return(repo, books, journal)
    rid, due = fin["id"], fin["result_cents"]  # Kz 83 of U1: 589.00
    assert due == 58900
    assert svc.get(rid)["payment_state"] == "open" and svc.get(rid)["open"] == "589.00"
    one = svc.pay(rid, date(2026, 2, 10), 20000, "payment", USER)
    assert [(p["date"], p["kind"], p["amount"]) for p in one["payments"]] == [("2026-02-10", "payment", "200.00")]
    assert one["paid"] == "200.00" and one["open"] == "389.00" and one["payment_state"] == "open"
    two = svc.pay(rid, date(2026, 2, 11), 38900, "payment", USER)
    assert len(two["payments"]) == 2 and two["paid"] == "589.00" and two["open"] == "0.00"
    assert two["payment_state"] == "paid"
    three = svc.pay(rid, date(2026, 3, 1), 1000, "refund", USER)  # a refund of 10.00 lowers the paid amount
    assert three["paid"] == "579.00" and three["open"] == "10.00" and three["payments"][-1]["kind"] == "refund"
    over = svc.pay(rid, date(2026, 3, 2), 5000, "payment", USER)
    assert over["open"] == "-40.00" and over["payment_state"] == "overpaid"
    rows = [e for e in repo.list_journal_entries(year=2026, limit=100) if e["event"] in ("vat_payment", "vat_refund")]
    assert len(rows) == 4 and all(e["origin"] == "manual" and e["source_id"] == rid for e in rows)


def test_payment_on_a_draft_is_409_and_bad_amounts_422(repo, books, journal):
    svc = make_service(repo)
    d = svc.create_draft(2026, "month", 1)
    with pytest.raises(ServiceError) as err:
        svc.pay(d["id"], date(2026, 2, 10), 100, "payment", USER)
    assert err.value.status == 409 and not repo.list_journal_entries(year=2026, limit=10)
    fin = svc.finalize(d["id"], USER)
    for bad in (0, -5):
        with pytest.raises(ServiceError) as err:
            svc.pay(fin["id"], date(2026, 2, 10), bad, "payment", USER)
        assert err.value.status == 422
    with pytest.raises(ServiceError) as err:
        svc.pay(fin["id"], date(2025, 12, 31), 100, "payment", USER)  # before the accounting start
    assert err.value.status == 422


def test_reversed_payment_does_not_count(repo, books, journal):
    svc, fin = final_return(repo, books, journal)
    svc.pay(fin["id"], date(2026, 2, 10), 20000, "payment", USER)
    entry = [e for e in repo.list_journal_entries(year=2026, limit=100) if e["event"] == "vat_payment"][0]
    from permita38.core.service.manual_entries import ManualEntryService

    ManualEntryService(repo, books.journal).reverse(entry["id"], None, None, USER)
    got = svc.get(fin["id"])
    assert got["paid"] == "0.00" and got["payments"][0]["reversed"] is True


# ------------------------------------------------------------------ API and rights
def test_api_payment_special_prepayment_and_rights(clients, app: Any, synthetic_charts):
    boss, acc, emp = clients["boss"], clients["accountant"], clients["employee"]
    app.state.services.store.save_settings({ACCOUNTING_START_KEY: "2024-01-01"})
    rid = boss.post("/api/vat-returns", json={"year": 2024, "kind": "month", "no": 1}).json()["id"]
    body = {"date": "2024-02-10", "amount": "12.50", "kind": "payment"}
    assert acc.post(f"/api/vat-returns/{rid}/payment", json=body).status_code == 409  # draft
    assert boss.post(f"/api/vat-returns/{rid}/finalize").status_code == 200
    r = acc.post(f"/api/vat-returns/{rid}/payment", json=body)
    assert r.status_code == 201, r.text
    got = r.json()
    assert got["paid"] == "12.50" and got["open"] == "-12.50" and got["payments"][0]["amount"] == "12.50"
    assert boss.get(f"/api/vat-returns/{rid}").json()["payments"][0]["kind"] == "payment"
    assert emp.post(f"/api/vat-returns/{rid}/payment", json=body).status_code == 403
    assert boss.post(f"/api/vat-returns/{rid}/payment", json={**body, "amount": "0.00"}).status_code == 422
    assert boss.post(f"/api/vat-returns/{rid}/payment", json={**body, "amount": "1.005"}).status_code == 422

    assert emp.get("/api/vat/special-prepayment?year=2024").status_code == 403
    assert emp.put("/api/vat/special-prepayment", json={"year": 2024, "amount": "1.00"}).status_code == 403
    got = acc.get("/api/vat/special-prepayment?year=2024").json()
    assert got["proposal"] == "0.00" and got["amount"] is None
    put = boss.put("/api/vat/special-prepayment", json={"year": 2024, "amount": "600.00", "paid_on": "2024-02-09"})
    assert put.status_code == 200, put.text
    assert put.json()["amount"] == "600.00" and put.json()["payment"]["paid_on"] == "2024-02-09"
    assert boss.get("/api/vat/special-prepayment?year=2024").json()["amount"] == "600.00"
    assert boss.get("/api/vat/special-prepayment?year=2035").status_code == 422
