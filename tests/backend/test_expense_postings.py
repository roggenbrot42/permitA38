# ruff: noqa: F811
"""Expense postings through the generic records path (docs/specs/accounting.md 7, 9.4, 10, 11, 16;
credit-notes-payments.md 5).

The expected entries are read from the hand-computed oracle ``fixtures/accounting_oracle.json`` (never edited here).
"""

from datetime import date
from typing import Any

import pytest

from permita38.core.errors import ServiceError
from permita38.core.service.journal import JournalService
from permita38.core.service.locks import LockService
from permita38.core.service.records import RecordService

from .test_issue_posting import (  # noqa: F401  (fixtures and helpers)
    CASES,
    entry_rows,
    init,
    lock_through,
    oracle_rows,
    repo,
)

BOSS = {"id": "u1x000", "role": "boss"}
CASE_IDS = [
    "A5-rc-eu-100",
    "A5-rc-eu-50",
    "A5-rc-3c",
    "A14-rc-eu-small-business-eur",
    "A6-meal",
    "A15-late-bill-after-lock",
    "A17-rc-eu-private-share-eur",
]


@pytest.fixture
def rs(repo):
    return RecordService(repo)


def expense_data(event: dict[str, Any]) -> dict[str, Any]:
    """The request body of an oracle expense event (a classic expense for one line, else itemised)."""
    rc = event["treatment"].startswith("reverse_charge")
    lines = event["lines"]
    data: dict[str, Any] = {"date": event["date"], "category": lines[0]["category"], "vendor": "Demo Supplier Ltd"}
    if event["treatment"] != "standard":
        data["tax_treatment"] = event["treatment"]
    for key in ("service_date", "paid_on"):
        if key in event:
            data[key] = event[key]
    if len(lines) == 1:
        ln = lines[0]
        data.update(
            net=ln["net"] / 100,
            vat=ln["vat"] / 100,
            vat_rate=float(ln["rate"]),
            deductible=ln["pct"],
            vat_deductible=ln["vat_pct"],
        )
    else:
        data["lines"] = [
            {
                "description": "x",
                "gross": (ln["net"] + (0 if rc else ln["vat"])) / 100,
                "vat_rate": float(ln["rate"]),
                "deductible": ln["pct"],
                "vat_deductible": ln["vat_pct"],
                "category": ln["category"],
            }
            for ln in lines
        ]
    return data


def entries(repo) -> list:
    return JournalService(repo).entries(2026)


def keys(repo) -> list[str]:
    return [e.event_key for e in entries(repo)]


def make(repo, rs, **fields) -> dict:
    data = {"date": "2026-03-10", "net": 100.0, "vat": 19.0, "vat_rate": 19.0, "category": "software"}
    return rs.create(BOSS, "expenses", {**data, **fields})


def put(rs, rec: dict, **fields) -> dict:
    data = {k: v for k, v in rec.items() if k not in ("id", "created", "updated", "bill_rev", "pay_rev")}
    return rs.update(BOSS, "expenses", rec["id"], {**data, **fields})


# ---------------------------------------------------------------- the oracle
@pytest.mark.parametrize("case_id", CASE_IDS)
def test_expense_entries_match_the_oracle(repo, rs, case_id):
    case = CASES[case_id]
    b = case["bases"]
    init(repo, income=b["income_basis"], vat=b["vat_basis"], small_business=b.get("small_business", False))
    for month in [m for m in case.get("locked_months", [])]:
        lock_through(repo, int(month[-2:]))
    event = case["events"][0]
    rec = rs.create(BOSS, "expenses", expense_data(event))
    posted = entries(repo)
    wanted = [e for e in case["entries"] if e["event"] == 0]
    assert [e.event_key for e in posted] == [f"expense:{rec['id']}:{e.get('part', 'bill')}:r1" for e in wanted]
    for got, exp in zip(posted, wanted, strict=True):
        assert entry_rows(got) == oracle_rows(exp), exp.get("part", "bill")
        assert got.booking_date == exp["booking_date"]
        day = event["paid_on"] if exp.get("part") == "pay" else event["date"]
        assert got.document_date == exp.get("document_date", day)
        assert {ln.open_item_id for ln in got.lines if ln.role == "payables"} <= {rec["id"]}
        assert got.source_coll == "expenses" and got.source_id == rec["id"]
    assert rec["bill_rev"] == 1 and rec.get("pay_rev") == (1 if "paid_on" in event else None)


def test_a5_stored_vat_is_the_self_assessed_tax(repo, rs):
    init(repo)
    rec = rs.create(BOSS, "expenses", expense_data(CASES["A5-rc-eu-100"]["events"][0]))
    assert rec["vat"] == 38.10 and rec["reverse_charge"] is True
    assert "accounting_warnings" not in rec


# ---------------------------------------------------------------- repost, delete
def test_change_reverses_r1_and_posts_r2(repo, rs):
    init(repo)
    rec = make(repo, rs)
    eid = rec["id"]
    assert keys(repo) == [f"expense:{eid}:bill:r1"]
    new = put(rs, rec, net=200.0, vat=38.0)
    assert new["bill_rev"] == 2
    posted = entries(repo)
    assert [e.event_key for e in posted] == [
        f"expense:{eid}:bill:r1",
        f"expense:{eid}:bill:r1:rev",
        f"expense:{eid}:bill:r2",
    ]
    first, rev, second = posted
    assert rev.reverses_id == first.id
    assert [(ln.role, ln.amount) for ln in rev.lines] == [(ln.role, -ln.amount) for ln in first.lines]
    assert [(ln.tax_key, ln.vat_date) for ln in rev.lines] == [(ln.tax_key, ln.vat_date) for ln in first.lines]
    assert [ln.role for ln in second.lines] == ["expense_software", "input_vat_s19", "payables"]
    assert second.lines[0].amount == 200 and second.lines[2].amount == -238


def test_description_only_edit_posts_nothing(repo, rs):
    init(repo)
    rec = make(repo, rs)
    new = put(rs, rec, description="Hosting for March")
    assert new["bill_rev"] == 1 and len(entries(repo)) == 1


def test_a_stored_record_keeps_its_revisions_over_a_put(repo, rs):
    init(repo)
    rec = make(repo, rs)
    rec = put(rs, rec, net=200.0, vat=38.0)
    again = put(rs, rec, description="x")
    assert again["bill_rev"] == 2
    assert rs.get(BOSS, "expenses", rec["id"])["bill_rev"] == 2


def test_paid_on_change_reposts_the_payment_alone(repo, rs):
    init(repo)
    rec = make(repo, rs)
    rec = put(rs, rec, paid_on="2026-03-12")
    eid = rec["id"]
    assert keys(repo) == [f"expense:{eid}:bill:r1", f"expense:{eid}:pay:r1"]
    rec = put(rs, rec, paid_on="2026-03-15")
    assert rec["bill_rev"] == 1 and rec["pay_rev"] == 2
    assert keys(repo)[2:] == [f"expense:{eid}:pay:r1:rev", f"expense:{eid}:pay:r2"]
    assert entries(repo)[-1].document_date == "2026-03-15"
    rec = put(rs, rec, paid_on=None)  # no longer paid: the payment is reversed, nothing new
    assert keys(repo)[-1] == f"expense:{eid}:pay:r2:rev" and rec["pay_rev"] == 3
    rec = put(rs, rec, paid_on="2026-03-20")  # paid again: revision 4, never an existing key
    assert keys(repo)[-1] == f"expense:{eid}:pay:r4" and rec["pay_rev"] == 4


def test_delete_posts_the_reversals_only(repo, rs):
    init(repo)
    rec = make(repo, rs, paid_on="2026-03-12")
    eid = rec["id"]
    rs.delete(BOSS, "expenses", eid)
    assert rs.store.get("expenses", eid) is None
    assert keys(repo) == [
        f"expense:{eid}:bill:r1",
        f"expense:{eid}:pay:r1",
        f"expense:{eid}:bill:r1:rev",
        f"expense:{eid}:pay:r1:rev",
    ]
    for e in entries(repo):
        assert sum(ln.amount for ln in e.lines) == 0


# ---------------------------------------------------------------- locked months
def test_changing_a_bill_in_a_locked_month_is_409_and_changes_nothing(repo, rs):
    init(repo)
    rec = make(repo, rs)  # 2026-03-10
    lock_through(repo, 3)
    before = len(entries(repo))
    with pytest.raises(ServiceError) as exc:
        put(rs, rec, net=200.0, vat=38.0)
    assert exc.value.status == 409 and "book a correction" in exc.value.message
    assert rs.get(BOSS, "expenses", rec["id"])["net"] == 100.0
    assert len(entries(repo)) == before
    with pytest.raises(ServiceError) as exc:
        rs.delete(BOSS, "expenses", rec["id"])
    assert exc.value.status == 409
    assert rs.store.get("expenses", rec["id"]) is not None and len(entries(repo)) == before


def test_an_edit_that_changes_no_posting_is_allowed_in_a_locked_month(repo, rs):
    init(repo)
    rec = make(repo, rs)
    lock_through(repo, 3)
    put(rs, rec, description="note added later")
    assert len(entries(repo)) == 1


def test_a_payment_in_a_locked_month_can_be_reposted_into_the_open_month(repo, rs):
    """Payment entries carry no VAT line: spec 9.4 allows the reversal; it and the new entry book in the open month."""
    init(repo, income="eur", vat="ist")
    rec = make(repo, rs, date="2026-01-20", tax_treatment="no_input_vat", vat=0.0, vat_rate=0.0, net=50.0)
    eid = rec["id"]
    assert keys(repo) == [f"expense:{eid}:pay:r1"]  # EÜR without input VAT: no bill entry
    lock_through(repo, 1)
    rec = put(rs, rec, paid_on="2026-02-15")
    posted = entries(repo)
    assert [e.event_key for e in posted] == [
        f"expense:{eid}:pay:r1",
        f"expense:{eid}:pay:r1:rev",
        f"expense:{eid}:pay:r2",
    ]
    assert posted[1].booking_date == "2026-02-01" and posted[2].booking_date == "2026-02-15"
    assert rec["pay_rev"] == 2 and "bill_rev" not in rec


def test_late_input_vat_moves_to_the_first_open_month(repo, rs):
    """A15 shape: input VAT never drifts; the entry and its vat_date go to the first open month."""
    init(repo)
    lock_through(repo, 1)
    rec = make(repo, rs, date="2026-01-28", net=100.0, vat=19.0)
    (entry,) = entries(repo)
    assert entry.booking_date == "2026-02-01" and entry.document_date == "2026-01-28"
    vst = next(ln for ln in entry.lines if ln.tax_key == "VST")
    assert vst.vat_date == "2026-02-01" and not entry.warnings
    assert rec["bill_rev"] == 1


def test_13b_tax_date_may_drift_into_a_locked_month(repo, rs):
    init(repo)
    lock_through(repo, 5)
    make(repo, rs, date="2026-06-02", service_date="2026-05-20", tax_treatment="reverse_charge_eu_service",
         net=200.5, vat=0.0, vat_rate=19.0)  # fmt: skip
    (entry,) = entries(repo)
    out = next(ln for ln in entry.lines if ln.role == "rc_vat_out_eu")
    assert out.vat_date == "2026-05-20" and entry.warnings


# ---------------------------------------------------------------- before the start
def test_nothing_posts_before_the_initialisation(repo, rs):
    rec = make(repo, rs, paid_on="2026-03-12")
    assert entries(repo) == [] and "bill_rev" not in rec
    put(rs, rec, net=5.0, vat=0.95)
    rs.delete(BOSS, "expenses", rec["id"])
    assert entries(repo) == []


def test_an_expense_dated_before_the_start_posts_no_bill(repo, rs):
    init(repo, start="2026-03-01")
    rec = make(repo, rs, date="2026-02-20", paid_on="2026-03-05")
    eid = rec["id"]  # the bill is an opening item, only the payment is a normal event
    assert keys(repo) == [f"open:2026:expense:{eid}", f"expense:{eid}:pay:r1"] and "bill_rev" not in rec


def test_moving_the_date_before_the_start_reverses_the_entries(repo, rs):
    init(repo, start="2026-03-01")
    rec = make(repo, rs, date="2026-03-10")
    rec = put(rs, rec, date="2026-02-20")
    eid = rec["id"]
    assert keys(repo) == [f"expense:{eid}:bill:r1", f"open:2026:expense:{eid}", f"expense:{eid}:bill:r1:rev"]
    assert rec["bill_rev"] == 2


# ---------------------------------------------------------------- treatment mapping and the server-side tax
def supplier(repo, mode: str) -> str:
    return repo.save("suppliers", {"name": "Demo Supplier", "kind": "goods", "vat_mode": mode, "active": True})["id"]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("eu_reverse_charge", "reverse_charge_eu_service"),
        ("third_country_reverse_charge", "reverse_charge_third_country"),
        ("domestic", "reverse_charge_eu_service"),
    ],
)
def test_old_reverse_charge_flag_maps_through_the_supplier(repo, rs, mode, expected):
    rec = make(repo, rs, reverse_charge=True, supplier_id=supplier(repo, mode), vat=0.0)
    assert rec["tax_treatment"] == expected and rec["reverse_charge"] is True
    assert rec["vat"] == 19.0  # 100.00 at the stated 19 %


def test_legacy_reverse_charge_shape_with_rate_zero_gets_the_regular_rate(repo, rs):
    rec = make(repo, rs, reverse_charge=True, tax_treatment=None, vat=0.0, vat_rate=0, net=1000.0)
    assert rec["tax_treatment"] == "reverse_charge_eu_service"
    assert rec["vat"] == 190.0 and rec["vat_rate"] == 19.0 and rec["net"] == 1000.0


def test_reverse_charge_without_a_rate_stores_the_regular_rate(repo, rs):
    data = {"date": "2026-03-10", "net": 1000.0, "category": "software", "tax_treatment": "reverse_charge_eu_service"}
    rec = rs.create(BOSS, "expenses", data)
    assert rec["vat"] == 190.0 and rec["vat_rate"] == 19.0


def test_no_input_vat_stores_vat_zero_and_net_equals_gross(repo, rs):
    rec = make(repo, rs, tax_treatment="no_input_vat", net=100.0, vat=19.0, vat_rate=19.0)
    assert rec["vat"] == 0.0 and rec["net"] == 119.0


def test_old_reverse_charge_flag_without_a_supplier_is_eu(repo, rs):
    assert make(repo, rs, reverse_charge=True)["tax_treatment"] == "reverse_charge_eu_service"


def test_explicit_treatment_wins_over_the_flag(repo, rs):
    rec = make(repo, rs, reverse_charge=True, tax_treatment="standard", supplier_id=supplier(repo, "eu_reverse_charge"))
    assert rec["tax_treatment"] == "standard" and "reverse_charge" not in rec and rec["vat"] == 19.0


def test_small_business_supplier_posts_without_input_vat(repo, rs):
    init(repo)
    rec = make(repo, rs, supplier_id=supplier(repo, "small_business"))
    (entry,) = entries(repo)
    assert [(ln.role, ln.amount) for ln in entry.lines] == [("expense_software", 119), ("payables", -119)]
    assert "tax_treatment" not in rec  # derived, not stored


def test_client_sent_vat_of_a_reverse_charge_bill_is_ignored_with_a_warning(repo, rs):
    rec = make(repo, rs, tax_treatment="reverse_charge_eu_service", net=200.5, vat=5.0, vat_rate=19.0)
    assert rec["vat"] == 38.10
    assert [w["code"] for w in rec["accounting_warnings"]] == ["expense.rc_vat_ignored"]
    assert "accounting_warnings" not in rs.get(BOSS, "expenses", rec["id"])
    zero = make(repo, rs, tax_treatment="reverse_charge_eu_service", net=200.5, vat=0.0, vat_rate=19.0)
    assert zero["vat"] == 38.10 and "accounting_warnings" not in zero


def test_itemised_reverse_charge_bill_has_the_printed_amount_as_net(repo, rs):
    init(repo)
    lines = [
        {"description": "a", "gross": 100.0, "vat_rate": 19.0, "category": "software"},
        {"description": "b", "gross": 100.5, "vat_rate": 19.0, "category": "software"},
    ]
    rec = rs.create(BOSS, "expenses", {"date": "2026-06-02", "net": 0, "tax_treatment": "reverse_charge_eu_service",
                                       "lines": lines})  # fmt: skip
    assert rec["net"] == 200.5 and rec["vat"] == 38.10 and [ln["vat"] for ln in rec["lines"]] == [0.0, 0.0]
    (entry,) = entries(repo)
    assert [(ln.role, ln.amount) for ln in entry.lines if ln.role.startswith("rc_")] == [
        ("rc_vat_out_eu", -38.10),
        ("rc_input_vat", 38.10),
    ]


def test_revision_fields_cannot_be_set_by_a_client(repo, rs):
    rec = make(repo, rs, bill_rev=7, pay_rev=9)
    assert "bill_rev" not in rec and "pay_rev" not in rec


# ---------------------------------------------------------------- the API
def test_api_reserved_treatment_and_service_date_bound(boss_client):
    body = {"date": "2026-03-10", "net": 100, "vat": 19, "vat_rate": 19, "category": "software"}
    r = boss_client.post("/api/expenses", json={**body, "tax_treatment": "import_vat"})
    assert r.status_code == 422
    r = boss_client.post("/api/expenses", json={**body, "service_date": "2027-03-12"})  # 367 days after
    assert r.status_code == 422
    r = boss_client.post("/api/expenses", json={**body, "service_date": "2027-03-11"})  # 366 days: allowed
    assert r.status_code in (200, 201), r.text


def test_api_reverse_charge_response_carries_the_warning(boss_client):
    body = {"date": "2026-06-02", "net": 200.5, "vat": 5, "vat_rate": 19, "category": "software",
            "tax_treatment": "reverse_charge_eu_service"}  # fmt: skip
    r = boss_client.post("/api/expenses", json=body)
    assert r.status_code in (200, 201), r.text
    out = r.json()
    assert float(out["vat"]) == 38.10
    assert out["accounting_warnings"][0]["code"] == "expense.rc_vat_ignored"
    assert "bill_rev" not in boss_client.post("/api/expenses", json={**body, "bill_rev": 5}).json()


# ---------------------------------------------------------------- change detection against the stored entry (9.4)
def test_invoice_no_only_edit_reposts_while_open(repo, rs):
    init(repo)
    rec = make(repo, rs, invoice_no="R-1")
    rec = put(rs, rec, invoice_no="R-2")
    posted = entries(repo)
    eid = rec["id"]
    assert [e.event_key for e in posted] == [
        f"expense:{eid}:bill:r1",
        f"expense:{eid}:bill:r1:rev",
        f"expense:{eid}:bill:r2",
    ]
    assert posted[2].source_number == "R-2" and rec["bill_rev"] == 2


def test_supplier_change_reposts_while_open_and_is_409_when_the_bill_is_locked(repo, rs):
    init(repo)
    a = repo.save("suppliers", {"name": "Demo A", "kind": "goods", "vat_id": "DE111111111", "country": "DE"})
    b = repo.save("suppliers", {"name": "Demo B", "kind": "goods", "vat_id": "FR22222222222", "country": "FR"})
    rec = make(repo, rs, supplier_id=a["id"], vendor="Demo A")
    rec = put(rs, rec, supplier_id=b["id"], vendor="Demo A")
    assert rec["bill_rev"] == 2
    assert entries(repo)[-1].counterparty_id == b["id"] and entries(repo)[-1].cp_vat_id == "FR22222222222"
    lock_through(repo, 3)
    before = len(entries(repo))
    with pytest.raises(ServiceError) as exc:
        put(rs, rec, supplier_id=a["id"], vendor="Demo A")
    assert exc.value.status == 409 and len(entries(repo)) == before
    with pytest.raises(ServiceError) as exc:
        put(rs, rec, invoice_no="R-9")
    assert exc.value.status == 409 and len(entries(repo)) == before


def test_treatment_change_in_a_locked_month_is_409(repo, rs):
    init(repo)
    rec = make(repo, rs)
    lock_through(repo, 3)
    with pytest.raises(ServiceError) as exc:
        put(rs, rec, tax_treatment="no_input_vat")
    assert exc.value.status == 409
    assert "tax_treatment" not in rs.store.get("expenses", rec["id"]) and len(entries(repo)) == 1


def test_a_bill_409_with_a_payment_change_rolls_back_the_whole_save(repo, rs):
    init(repo)
    rec = make(repo, rs, paid_on="2026-03-12")
    lock_through(repo, 3)
    before = keys(repo)
    with pytest.raises(ServiceError) as exc:
        put(rs, rec, net=200.0, vat=38.0, paid_on="2026-04-02")
    assert exc.value.status == 409 and keys(repo) == before
    stored = rs.store.get("expenses", rec["id"])
    assert stored["net"] == 100.0 and stored["paid_on"] == "2026-03-12" and stored["pay_rev"] == 1


# ---------------------------------------------------------------- booking-date rule of the engine input
def test_13b_private_share_income_date_follows_the_closing_date_exception(repo, rs):
    """Spec 7.8: December locked, 2026 open: the entry books on 31 December and so does the income date."""
    init(repo, income="eur", vat="ist")
    LockService(repo, today=lambda: date(2027, 1, 15)).lock_up_to(2026, 12, "manual", "u1x000")
    make(
        repo, rs, date="2026-10-15", tax_treatment="reverse_charge_eu_service", net=100.0, vat=0.0, vat_rate=19.0,
        deductible=50,
    )  # fmt: skip
    bill = next(e for e in entries(repo) if ":bill:" in e.event_key)
    assert bill.booking_date == "2026-12-31"
    private = next(ln for ln in bill.lines if ln.role == "vat_paid_private")
    assert private.income_date == "2026-12-31"


def test_eur_13b_nondeductible_tax_carries_no_income_date(repo, rs):
    """Spec 9.4: it becomes an expense when paid with the return (9.6), so the bill must not count it as well."""
    init(repo, income="eur", vat="ist")
    make(repo, rs, date="2026-10-15", tax_treatment="reverse_charge_eu_service", net=100.0, vat=0.0, vat_rate=19.0,
         deductible=50)  # fmt: skip
    bill = next(e for e in entries(repo) if ":bill:" in e.event_key)
    nd = [ln for ln in bill.lines if ln.role == "rc_vat_nondeductible"]
    assert len(nd) == 1 and nd[0].income_date is None


def test_itemised_reverse_charge_line_without_a_rate_defaults_to_19(repo, rs):
    init(repo)
    lines = [{"description": "a", "gross": 100.0, "category": "software"}]
    rec = rs.create(BOSS, "expenses", {"date": "2026-06-02", "net": 0, "tax_treatment": "reverse_charge_eu_service",
                                       "lines": lines})  # fmt: skip
    assert rec["vat"] == 19.0
    (entry,) = entries(repo)
    assert next(ln for ln in entry.lines if ln.role == "rc_vat_out_eu").amount == -19.0


# ---------------------------------------------------------------- other creation paths post
def test_scanned_and_adopted_expenses_post(repo, rs):
    init(repo)
    scanned = {"date": "2026-03-10", "net": 100.0, "vat": 19.0, "vat_rate": 19.0, "category": "software",
               "vendor": "Demo Shop"}  # fmt: skip
    rec = rs.create_scanned(BOSS, scanned, "scan0001")
    assert rec["bill_rev"] == 1
    plain = repo.save("expenses", {**scanned, "date": "2026-03-11", "net": 50.0, "vat": 9.5})
    adopted = rs.adopt_expense(BOSS, plain, supplier(repo, "domestic"))
    assert adopted["bill_rev"] == 1
    assert len(keys(repo)) == 2
