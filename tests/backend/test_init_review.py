# ruff: noqa: F811
"""The initialisation review list and its resolutions (docs/specs/accounting.md 16 steps 5a, 5b, 6, 8, 9;
credit-notes-payments.md 2.3), work package E24.

One folder holds every review case; the expected figures are hand-computed: an invoice of 100.00 net + 19 % is 119.00
gross (11900 cents), a reverse-charge bill of 200.00 net has 19 % = 38.00 self-assessed VAT.
"""

import shutil
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from permita38.core import charts, models
from permita38.core.errors import ServiceError
from permita38.core.service.accounting_init import AccountingInitService
from permita38.core.service.journal import JournalService
from permita38.core.service.locks import LockService
from permita38.core.settings import ACCOUNTING_START_KEY

from .test_invoice_freeze import cust, issue, store  # noqa: F401  (fixtures)
from .test_issue_posting import FIXTURES, customer, line, repo, sales  # noqa: F401  (fixtures and helpers)
from .test_payments import BOSS, pay  # noqa: F401  (fixtures)
from .test_rbac import clients  # noqa: F401  (fixture)

TODAY = date(2026, 6, 10)  # the months January..May 2026 have ended
PARAMS = models.AccountingInitIn(chart="skr03", income_basis="accrual", vat_basis="soll", accounting_start="2026-01-01")


@pytest.fixture(autouse=True)
def answer_external_periods(monkeypatch):
    """Every ended period needs an answer (step 5b). Tests that do not care for it get "none filed" for all periods;
    a test that passes any ``external_returns`` itself is left alone."""
    real = AccountingInitService.run

    def run(self, params, user=None, today=None):
        if not params.external_returns:
            try:
                periods = self.external_periods(params, today)
            except ServiceError:  # an invalid start: the run itself refuses
                periods = []
            none = [models.ExternalReturnIn(year=p.year, kind=p.kind, no=p.no, none_filed=True) for p in periods]
            params = params.model_copy(update={"external_returns": none})
        return real(self, params, user, today)

    monkeypatch.setattr(AccountingInitService, "run", run)


def res(key: str, action: str, **extra) -> models.ReviewResolutionIn:
    return models.ReviewResolutionIn(key=key, action=action, **extra)


def folder(repo) -> None:
    """Every review case once, plus the automatic one (an unpaid legacy cancelled invoice)."""
    cu = customer(repo)
    base = dict(customer_id=cu["id"], lines=[line("100", "19")])
    repo.save(
        "invoices", dict(id="solldec1", date="2026-01-05", number="R-1", status="sent", service_to="2025-12-20", **base)
    )
    repo.save(
        "invoices",
        dict(id="cxlpaid1", date="2026-02-02", number="R-2", status="cancelled", paid_date="2026-02-10", **base),
    )
    repo.save(
        "invoices",
        dict(id="cxlpaid2", date="2026-02-04", number="R-3", status="cancelled", paid_date="2026-02-05", **base),
    )
    repo.save("invoices", dict(id="cxlunpd1", date="2026-02-03", number="R-4", status="cancelled", **base))
    repo.save("invoices", dict(id="paidnodt1", date="2026-03-01", number="R-5", status="paid", **base))
    repo.save("invoices", dict(id="paidnodt2", date="2026-03-02", number="R-6", status="paid", **base))
    repo.save(
        "invoices", dict(id="paidbefr1", date="2026-03-10", number="R-7", status="paid", paid_date="2026-03-10", **base)
    )
    repo.save(
        "invoices", dict(id="paidbefr2", date="2026-03-12", number="R-8", status="paid", paid_date="2026-03-11", **base)
    )
    sup = repo.save("suppliers", {"name": "Demo Supplier", "kind": "goods", "vat_mode": "eu_reverse_charge"})
    rc = {"date": "2026-03-01", "net": 200.0, "vat": 0.0, "category": "software", "reverse_charge": True}
    repo.save("expenses", {"id": "rcnosup1", **rc})
    repo.save("expenses", {"id": "rctyped1", **rc, "vat": 5.0, "supplier_id": sup["id"]})
    pp = {"date": "2026-02-01", "net": 10.0, "vat": 1.9, "vat_rate": 19.0, "category": "software"}
    repo.save("expenses", {"id": "prepaid1", **pp, "paid_on": "2025-12-30"})


ALL = {
    ("solldec1", "vat_before_start"): ("confirm",),
    ("cxlpaid1", "legacy_cancelled_paid"): ("credit_note", "confirm"),
    ("cxlpaid2", "legacy_cancelled_paid"): ("credit_note", "confirm"),
    ("paidnodt1", "legacy_paid_no_date"): ("confirm", "date"),
    ("paidnodt2", "legacy_paid_no_date"): ("confirm", "date"),
    ("paidbefr2", "legacy_paid_before_invoice"): ("confirm", "date"),
    ("rcnosup1", "rc_without_supplier"): ("treatment",),
    ("rctyped1", "rc_stored_vat"): ("confirm", "treatment"),
    ("prepaid1", "expense_prepaid"): ("confirm",),
}


def answers(repo) -> list[models.ReviewResolutionIn]:
    keys = {r.id: r.key for r in AccountingInitService(repo).check(PARAMS, TODAY)}
    return [
        res(keys["solldec1"], "confirm"),
        res(keys["cxlpaid1"], "credit_note"),
        res(keys["cxlpaid2"], "confirm"),
        res(keys["paidnodt1"], "date", date="2026-03-05"),
        res(keys["paidnodt2"], "confirm"),
        res(keys["paidbefr2"], "confirm"),
        res(keys["rcnosup1"], "treatment", treatment="reverse_charge_third_country"),
        res(keys["rctyped1"], "confirm"),
        res(keys["prepaid1"], "confirm"),
    ]


def test_check_lists_every_case_with_its_resolutions(repo):
    folder(repo)
    svc = AccountingInitService(repo)
    review = svc.check(PARAMS, TODAY)
    assert {(r.id, r.code): r.resolutions for r in review} == ALL
    assert len({r.key for r in review}) == len(review)  # stable, unique keys
    assert svc.check(PARAMS, TODAY) == review
    row = next(r for r in review if r.id == "rcnosup1").as_dict()
    assert row["key"] == "expenses:rcnosup1:rc_without_supplier" and row["collection"] == "expenses"
    assert row["message"] and row["resolutions"] == ["treatment"]
    assert row["treatments"] == [
        "standard", "no_input_vat", "reverse_charge_eu_service", "reverse_charge_third_country",
    ]  # fmt: skip
    assert next(r for r in review if r.id == "solldec1").as_dict()["treatments"] == []
    # the unpaid cancelled invoice is no row: its storno is automatic
    assert "cxlunpd1" not in {r.id for r in review}


def test_external_periods_are_the_ended_months_from_the_start(repo):
    svc = AccountingInitService(repo)
    got = [(p.year, p.kind, p.no, p.to_date.isoformat()) for p in svc.external_periods(PARAMS, TODAY)]
    assert got == [(2026, "month", n, d) for n, d in enumerate(
        ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31"], 1)]  # fmt: skip
    quarter = PARAMS.model_copy(update={"frequency": "quarter"})
    assert [(p.no, p.to_date.isoformat()) for p in svc.external_periods(quarter, TODAY)] == [(1, "2026-03-31")]
    assert svc.external_periods(PARAMS.model_copy(update={"frequency": "none"}), TODAY) == []
    small = PARAMS.model_copy(update={"small_business": True})  # quarterly
    assert [p.no for p in svc.external_periods(small, TODAY)] == [1]


def test_run_without_resolutions_is_409_with_the_keys_and_writes_nothing(repo):
    folder(repo)
    with pytest.raises(ServiceError) as err:
        AccountingInitService(repo).run(PARAMS, BOSS, TODAY)
    assert err.value.status == 409
    keys = {r.key for r in AccountingInitService(repo).check(PARAMS, TODAY)}
    assert set(err.value.body["unresolved"]) == keys and len(keys) == 9
    assert {r["key"] for r in err.value.body["review"]} == keys
    assert all(r["resolutions"] for r in err.value.body["review"])
    # a partial answer lists only the rest
    first = sorted(keys)[0]
    part = PARAMS.model_copy(update={"review_resolutions": [r for r in answers(repo) if r.key == first]})
    with pytest.raises(ServiceError) as err:
        AccountingInitService(repo).run(part, BOSS, TODAY)
    assert set(err.value.body["unresolved"]) == keys - {first}
    assert repo.find("fiscal_years") == [] and ACCOUNTING_START_KEY not in repo.settings()
    assert repo.find("payments") == [] and not any(i.get("kind") == "credit_note" for i in repo.list("invoices"))


def test_wrong_answers_are_422(repo):
    folder(repo)
    keys = {r.id: r.key for r in AccountingInitService(repo).check(PARAMS, TODAY)}
    for bad in (
        res("invoices:nope:vat_before_start", "confirm"),  # unknown key
        res(keys["solldec1"], "credit_note"),  # not allowed for the code
        res(keys["rcnosup1"], "confirm"),  # a missing supplier needs a treatment
        res(keys["paidnodt1"], "date", date="2026-02-28"),  # before the invoice date
    ):
        params = PARAMS.model_copy(update={"review_resolutions": [bad]})
        with pytest.raises(ServiceError) as err:
            AccountingInitService(repo).run(params, BOSS, TODAY)
        assert err.value.status == 422, bad
    assert repo.find("fiscal_years") == []


def test_resolution_models_are_checked():
    with pytest.raises(ValidationError):
        res("k", "treatment")  # treatment missing
    with pytest.raises(ValidationError):
        res("k", "confirm", treatment="standard")
    with pytest.raises(ValidationError):
        res("k", "date")
    with pytest.raises(ValidationError):
        res("k", "treatment", treatment="made_up")
    with pytest.raises(ValidationError):
        models.AccountingInitIn(chart="skr03", income_basis="eur", review_resolutions=[res("k", "confirm")] * 2)
    base = dict(year=2026, kind="month", no=1)
    for bad in (
        dict(base),  # neither none_filed nor figures
        dict(base, kz83="1", none_filed=True, filed_on="2026-02-01"),
        dict(base, kz83="1", filed_on="2026-02-01", kz39="-1"),
        dict(base, kz83="1", filed_on="2026-02-01", no=13),
        dict(year=2026, kind="quarter", no=5, kz83="1", filed_on="2026-02-01"),
        dict(base, kz83="1.234", filed_on="2026-02-01"),
    ):
        with pytest.raises(ValidationError):
            models.ExternalReturnIn(**bad)
    with pytest.raises(ValidationError):  # the same period twice
        models.AccountingInitIn(
            chart="skr03", income_basis="eur",
            external_returns=[dict(base, none_filed=True), dict(base, kz83="1", filed_on="2026-02-01")],
        )  # fmt: skip


def test_all_resolutions_apply_and_every_effect_is_stored(repo):
    folder(repo)
    filed = [
        models.ExternalReturnIn(year=2026, kind="month", no=1, kz83="123.45", filed_on="2026-02-10"),
        models.ExternalReturnIn(year=2026, kind="month", no=2, kz83="-50.00", kz39="10.00", filed_on="2026-03-09"),
        models.ExternalReturnIn(year=2026, kind="month", no=3, none_filed=True),
        models.ExternalReturnIn(year=2026, kind="month", no=4, none_filed=True),
        models.ExternalReturnIn(year=2026, kind="month", no=5, none_filed=True),
    ]
    params = PARAMS.model_copy(update={"review_resolutions": answers(repo), "external_returns": filed})
    result = AccountingInitService(repo).run(params, BOSS, TODAY)
    assert result.counts["migrated_cancellation"] == 1 and result.counts["credit_note"] == 1
    assert result.counts["treatment"] == 2 and result.counts["external_returns"] == 2
    assert repo.settings()[ACCOUNTING_START_KEY] == "2026-01-01"

    # treatments: the chosen one, and the supplier's with the self-assessed VAT recomputed (200.00 x 19 % = 38.00)
    assert repo.get("expenses", "rcnosup1")["tax_treatment"] == "reverse_charge_third_country"
    typed = repo.get("expenses", "rctyped1")
    assert typed["tax_treatment"] == "reverse_charge_eu_service" and typed["vat"] == 38.0

    # prepaid before the start: the record is untouched (its payment is the opening item ``...:prepaid``)
    pre = repo.get("expenses", "prepaid1")
    assert pre["paid_on"] == "2025-12-30" and "paid_on_original" not in pre

    # legacy paid dates: given, the invoice date, kept as stored (before the invoice date)
    assert repo.get("invoices", "paidnodt1")["paid_date"] == "2026-03-05"
    assert repo.get("invoices", "paidnodt2")["paid_date"] == "2026-03-02"
    assert repo.get("invoices", "paidbefr2")["paid_date"] == "2026-03-11"
    pays = {r["date"] for r in repo.find("payments", document_id="paidnodt1")}
    assert pays == {"2026-03-05"}

    # paid then cancelled: storno + refund of the cash kept (dated the payment day) ...
    notes = repo.find("invoices", corrects_id="cxlpaid1")
    assert [(n["credit_kind"], n["date"], n["reason"]) for n in notes] == [
        ("storno", "2026-02-10", "migrated cancellation")
    ]
    assert notes[0]["number"] and notes[0]["issued"]["gross"] == 11900
    cash = repo.find("payments", document_id="cxlpaid1")
    assert sorted(r["amount_cents"] for r in cash) == [-11900, 11900] and sum(r["amount_cents"] for r in cash) == 0
    # ... or kept as paid
    assert repo.find("invoices", corrects_id="cxlpaid2") == [] and repo.get("invoices", "cxlpaid2")["status"] == "paid"
    assert [r["amount_cents"] for r in repo.find("payments", document_id="cxlpaid2")] == [11900]

    # the unpaid cancelled invoice: storno dated the invoice date, posted by the replay
    auto = repo.find("invoices", corrects_id="cxlunpd1")
    assert [(n["credit_kind"], n["date"], n["reason"]) for n in auto] == [
        ("storno", "2026-02-03", "migrated cancellation")
    ]
    assert auto[0]["number"] and auto[0]["issued"]["gross"] == 11900
    keys = {e.event_key for e in JournalService(repo).entries(2026, limit=500)}
    assert any(k.startswith("credit:") and auto[0]["id"] in k for k in keys)

    # "declared outside the app": posts normally and keeps its vat_date before the start
    sol = [
        e
        for e in JournalService(repo).entries(2026, limit=500)
        if e.event_key.endswith(":solldec1") or "solldec1" in e.event_key
    ]
    dates = {ln.vat_date for e in sol for ln in e.lines if ln.vat_date}
    assert dates and all(str(d) < "2026-01-01" for d in dates)

    # returns filed outside the app: stored, final, external, the months locked; none_filed stores nothing
    rows = sorted(repo.find("vat_returns"), key=lambda r: r["period_no"])
    assert [(r["period_no"], r["status"], r["external"], r["filed_on"], r["result_cents"]) for r in rows] == [
        (1, "final", True, "2026-02-10", 12345),
        (2, "final", True, "2026-03-09", -5000),
    ]
    assert rows[1]["special_prepayment_credit_cents"] == 1000
    assert LockService(repo, today=lambda: TODAY).locked_months(2026) == [1, 2]


def test_unpaid_cancelled_invoice_gets_its_storno_without_a_question(repo):
    cu = customer(repo)
    repo.save("invoices", dict(id="cxlunpd1", customer_id=cu["id"], date="2026-02-03", number="R-4", status="cancelled",
                               lines=[line("100", "19")]))  # fmt: skip
    svc = AccountingInitService(repo)
    assert svc.check(PARAMS, TODAY) == []
    assert svc.run(PARAMS, BOSS, TODAY).counts["migrated_cancellation"] == 1
    (note,) = repo.find("invoices", corrects_id="cxlunpd1")
    assert (note["credit_kind"], note["date"], note["reason"]) == ("storno", "2026-02-03", "migrated cancellation")
    entries = JournalService(repo).entries(2026, limit=500)
    credit = [e for e in entries if e.event_key.startswith("credit:")]
    assert len(credit) == 1 and str(credit[0].document_date) == "2026-02-03"
    # the cancelled invoice is not open any more: the issue and the storno net out to zero in the receivables
    receivables = sum(Decimal(str(ln.amount)) for e in entries for ln in e.lines if ln.role == "receivables")
    assert receivables == 0


def test_a_return_outside_the_ended_periods_is_422(repo):
    for year, no in ((2026, 6), (2025, 12)):  # June has not ended; December lies before the start
        r = models.ExternalReturnIn(year=year, kind="month", no=no, kz83="1.00", filed_on="2026-06-01")
        with pytest.raises(ServiceError) as err:
            AccountingInitService(repo).run(PARAMS.model_copy(update={"external_returns": [r]}), BOSS, TODAY)
        assert err.value.status == 422
    assert repo.find("fiscal_years") == [] and repo.find("vat_returns") == []


def test_api_review_resolutions_and_external_returns(app, clients, tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURES / "chart_synthetic.json", charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    boss, acc = clients["boss"], clients["accountant"]
    store = app.state.services.store
    cu = store.save("customers", dict(name="Demo Client GmbH", country="DE"))
    store.save(
        "expenses",
        {
            "id": "rcnosup1",
            "date": "2026-03-01",
            "net": 100.0,
            "vat": 0.0,
            "category": "software",
            "reverse_charge": True,
        },
    )
    store.save("invoices", dict(id="paidnodt1", customer_id=cu["id"], date="2026-03-01", number="R-5", status="paid", lines=[line("100", "19")]))  # fmt: skip
    q = {"accounting_start": "2026-01-01"}
    got = acc.get("/api/accounting/init/review", params=q).json()
    assert {(r["id"], r["code"]) for r in got["review"]} == {
        ("rcnosup1", "rc_without_supplier"),
        ("paidnodt1", "legacy_paid_no_date"),
    }
    assert all(r["resolutions"] for r in got["review"]) and got["external_returns"][0] == {
        "year": 2026, "kind": "month", "no": 1, "from": "2026-01-01", "to": "2026-01-31",
    }  # fmt: skip
    body = {"chart": "skr03", "income_basis": "eur", "accounting_start": "2026-01-01"}
    r = boss.post("/api/accounting/init", json=body)
    assert r.status_code == 409 and set(r.json()["unresolved"]) == {x["key"] for x in got["review"]}
    body["review_resolutions"] = [
        {"key": "expenses:rcnosup1:rc_without_supplier", "action": "treatment", "treatment": "standard"},
        {"key": "invoices:paidnodt1:legacy_paid_no_date", "action": "confirm"},
    ]
    body["external_returns"] = [{"year": 2026, "kind": "month", "no": 1, "kz83": "10.50", "filed_on": "2026-02-12"}] + [
        {"year": p["year"], "kind": p["kind"], "no": p["no"], "none_filed": True} for p in got["external_returns"][1:]
    ]  # every other ended period (the real today in the test is not pinned)
    bad = boss.post(
        "/api/accounting/init", json={**body, "external_returns": [{"year": 2026, "kind": "month", "no": 1}]}
    )
    assert bad.status_code == 422
    r = boss.post("/api/accounting/init", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["external_returns"] == 1
    assert store.get("expenses", "rcnosup1")["tax_treatment"] == "standard"
    assert store.get("invoices", "paidnodt1")["paid_date"] == "2026-03-01"


def _cents_of(ln) -> int:
    return int(round(Decimal(str(ln.amount)) * 100))


def _roles(entries, key_part):
    """role -> summed cents over the entries whose key contains ``key_part``."""
    out: dict[str, int] = {}
    for e in entries:
        if key_part in e.event_key:
            for ln in e.lines:
                out[str(ln.role)] = out.get(str(ln.role), 0) + _cents_of(ln)
    return out


def _prepaid_run(repo, basis: str):
    """A bill of 10.00 net + 19 % = 11.90 gross dated 2026-02-01, paid 2025-12-30 (before the start)."""
    repo.save(
        "expenses",
        {"id": "prepaid1", "date": "2026-02-01", "net": 10.0, "vat": 1.9, "vat_rate": 19.0, "category": "software",
         "paid_on": "2025-12-30"},
    )  # fmt: skip
    svc = AccountingInitService(repo)
    params = PARAMS.model_copy(update={"income_basis": basis})
    key = next(r.key for r in svc.check(params, TODAY) if r.id == "prepaid1")
    params = params.model_copy(update={"review_resolutions": [res(key, "confirm")]})
    svc.run(params, BOSS, TODAY)
    return JournalService(repo).entries(2026, limit=500)


def test_prepaid_expense_accrual_posts_an_opening_item_and_keeps_paid_on(repo):
    entries = _prepaid_run(repo, "accrual")
    # opening item: the payment's payables line (D 1190) against opening_balance (C 1190)
    assert _roles(entries, "open:2026:expense:prepaid1:prepaid") == {"payables": 1190, "opening_balance": -1190}
    # the bill posts normally: C payables 1190; no payment entry. Payables: -1190 (bill) + 1190 (opening) = 0
    assert _roles(entries, "expense:prepaid1:bill").get("payables") == -1190
    assert not any(":pay:" in e.event_key for e in entries)
    assert sum(_cents_of(ln) for e in entries for ln in e.lines if ln.role == "payables") == 0
    rec = repo.get("expenses", "prepaid1")
    assert rec["paid_on"] == "2025-12-30" and "paid_on_original" not in rec


def test_prepaid_expense_eur_has_no_cost_and_the_clearing_nets_out(repo):
    entries = _prepaid_run(repo, "eur")
    # EÜR: bill D input VAT 190 / C input_vat_clearing 190; the opening item D input_vat_clearing 190 / C opening 190
    assert _roles(entries, "open:2026:expense:prepaid1:prepaid") == {"input_vat_clearing": 190, "opening_balance": -190}
    bill = _roles(entries, "expense:prepaid1:bill")
    assert bill["input_vat_clearing"] == -190 and sum(bill.values()) == 0
    assert sum(_cents_of(ln) for e in entries for ln in e.lines if ln.role == "input_vat_clearing") == 0
    # no payment entry, so no cost line and no bank line (the cost belongs to the year before the start)
    assert not any(":pay:" in e.event_key for e in entries)
    assert not any(ln.role == "bank" for e in entries for ln in e.lines)


def _external_answers(repo, upto: int):
    periods = AccountingInitService(repo).external_periods(PARAMS, TODAY)
    return [
        models.ExternalReturnIn(year=p.year, kind=p.kind, no=p.no, none_filed=True) for p in periods if p.no <= upto
    ]


def test_an_unanswered_external_period_blocks_with_409_and_none_filed_for_all_passes(repo):
    svc = AccountingInitService(repo)
    partial = PARAMS.model_copy(update={"external_returns": _external_answers(repo, 4)})  # May (5) is missing
    with pytest.raises(ServiceError) as err:
        svc.run(partial, BOSS, TODAY)
    assert err.value.status == 409
    assert err.value.body["unresolved"] == ["external_return:2026:month:5"]
    assert err.value.body["external_returns"] == [
        {"year": 2026, "kind": "month", "no": 5, "from": "2026-05-01", "to": "2026-05-31"}
    ]
    assert repo.find("fiscal_years") == [] and ACCOUNTING_START_KEY not in repo.settings()
    full = PARAMS.model_copy(update={"external_returns": _external_answers(repo, 5)})
    result = svc.run(full, BOSS, TODAY)
    assert "external_returns" not in result.counts  # none filed stores nothing
    assert repo.settings()[ACCOUNTING_START_KEY] == "2026-01-01"
