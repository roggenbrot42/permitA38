# ruff: noqa: F811
"""Accounting initialisation and backfill (docs/specs/accounting.md 16-18), work package E22.

The core property: initialising a data folder that already holds the events posts exactly what posting each event live
would have produced (compared with a second folder where the accounting was initialised first).
"""

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from permita38.core import charts, models
from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.repository import create_repository
from permita38.core.service.accounting_init import AccountingInitService
from permita38.core.service.credit_notes import CreditNoteService
from permita38.core.service.journal import JournalService
from permita38.core.service.records import RecordService
from permita38.core.service.sales import SalesService
from permita38.core.service.trips import TripService
from permita38.core.settings import ACCOUNTING_START_KEY

from .test_invoice_freeze import cust, issue, store  # noqa: F401  (fixtures)
from .test_issue_posting import customer, init, line, repo, sales  # noqa: F401  (fixtures and helpers)
from .test_payments import BOSS, TODAY, pay  # noqa: F401  (fixtures)
from .test_rbac import clients  # noqa: F401  (fixture)
from .test_trip_postings import body as trip_body
from .test_trip_postings import boss as trip_boss

PARAMS = models.AccountingInitIn(chart="skr03", income_basis="accrual", vat_basis="soll", accounting_start="2026-01-01")
EUR = PARAMS.model_copy(update={"income_basis": "eur"})
RC_RAW = {"date": "2026-03-01", "net": 100.0, "vat": 0.0, "category": "software", "reverse_charge": True}


def all_entries(repo) -> list:
    out: list = []
    for year in (2025, 2026, 2027):
        out += JournalService(repo).entries(year, limit=500)
    return out


def canon(repo, skip_open: bool = True) -> list[tuple]:
    """Entries by what they say, not by generated ids: payment and credit keys are anchored on the invoice number."""
    rows = []
    for e in all_entries(repo):
        key = e.event_key
        if skip_open and key.startswith("open:"):
            continue
        kind = key.split(":")[0]  # ids and credit-note numbers depend on the order of creation: date and lines identify
        anchor = e.source_number if kind == "payment" else "" if kind == "credit" else key
        if kind in ("expense", "trip"):  # generated id in the middle of the key
            anchor = ":".join(key.split(":")[2:])
        lines = tuple((ln.role, str(ln.amount), ln.tax_key, ln.tax_kind, ln.vat_date, ln.income_date) for ln in e.lines)
        rows.append((key.split(":")[0], anchor, str(e.document_date), str(e.booking_date), lines))
    return sorted(rows, key=repr)


class World:
    """One data folder with its services; the methods enter the same data in a chosen order."""

    def __init__(self, repo, sales, pay):
        self.repo, self.sales, self.pay = repo, sales, pay
        self.cn = CreditNoteService(repo, sales.postings.journal, pay)
        self.rs = RecordService(repo)
        self.me = trip_boss(repo)
        self.cust = customer(repo)

    def invoice(self, ident: str, day: str, net: str, vat: str, number: str) -> dict:
        self.repo.save(
            "invoices", dict(id=ident, customer_id=self.cust["id"], date=day, number=number, lines=[line(net, vat)])
        )
        return self.sales.finalize("invoices", ident)

    def a(self):  # 1190.00
        self.invoice("inva00a", "2026-02-10", "1000", "19", "R-A")

    def a_pay(self):
        self.pay.record("inva00a", date="2026-03-05", amount=Decimal("500"), user=BOSS)
        self.pay.record("inva00a", date="2026-03-20", amount=Decimal("690"), user=BOSS)

    def b(self):  # 535.00
        self.invoice("inva00b", "2026-03-01", "500", "7", "R-B")

    def b_events(self):
        amounts = {"7": "100.00"}
        self.cn.create("inva00b", credit_kind="partial", date="2026-03-15", reason="x", amounts=amounts, user=BOSS)
        self.pay.record("inva00b", date="2026-04-02", amount=Decimal("428.00"), user=BOSS)

    def c(self, legacy: bool):  # 119.00 paid on 10 April
        self.invoice("inva00c", "2026-04-01", "100", "19", "R-C")
        if legacy:
            inv = self.repo.get("invoices", "inva00c")
            self.repo.save("invoices", {**inv, "status": "paid", "paid_date": "2026-04-10"})
        else:
            self.pay.record("inva00c", date="2026-04-10", amount=Decimal("119.00"), user=BOSS)

    def d(self):  # storno
        self.invoice("inva00d", "2026-05-03", "200", "19", "R-D")
        self.cn.create("inva00d", credit_kind="storno", date="2026-05-20", reason="x", user=BOSS)

    def expenses(self):
        base = {"category": "software", "vendor": "Demo Supplier Ltd"}
        standard = {"id": "exp001", "date": "2026-03-10", "net": 100.0, "vat": 19.0, "vat_rate": 19.0}
        self.rs.create(BOSS, "expenses", {**base, **standard})
        rc = {
            "id": "exp002",
            "date": "2026-06-01",
            "net": 200.0,
            "vat_rate": 19.0,
            "tax_treatment": "reverse_charge_eu_service",
            "paid_on": "2026-06-15",
        }
        self.rs.create(BOSS, "expenses", {**base, **rc})

    def trip(self):
        TripService(self.repo).create(self.me, trip_body())

    def drafts(self):
        self.repo.save(
            "invoices", dict(id="draft01", customer_id=self.cust["id"], date="2026-07-01", lines=[line("50", "19")])
        )


def chronological(w: World):
    """Entered in date order, the way a live accounting sees it."""
    w.a()
    w.expenses()
    w.b()
    w.a_pay()
    w.b_events()
    w.c(legacy=False)
    w.d()
    w.trip()
    w.drafts()


def scrambled(w: World):
    """Entered in another order and with a legacy paid invoice: later invoices first, expenses and trip last."""
    w.d()
    w.c(legacy=True)
    w.b()
    w.a()
    w.b_events()
    w.a_pay()
    w.drafts()
    w.expenses()
    w.trip()


def test_backfill_equals_live_posting(tmp_path, repo, sales, pay):
    a = World(repo, sales, pay)
    scrambled(a)
    assert all_entries(repo) == []  # nothing posts before the initialisation
    result = AccountingInitService(repo).run(PARAMS, BOSS)
    assert result.year == 2026 and result.counts["issue"] == 4 and result.counts["legacy_payment"] == 1

    repo_b = create_repository(Config(data_dir=tmp_path / "live", secret_key="x" * 16))
    repo_b.startup()
    try:
        sales_b = SalesService(repo_b, RecordService(repo_b))
        sales_b.payments.today = lambda: TODAY
        b = World(repo_b, sales_b, sales_b.payments)
        init(repo_b, income="accrual", vat="soll")
        chronological(b)
        got, want = canon(repo), canon(repo_b)
        assert got == want
        assert {k for k, *_ in got} >= {"issue", "payment", "credit", "expense", "trip"}
        assert len(got) >= 14
    finally:
        repo_b.close()


def test_journal_is_in_date_order_and_chain_is_valid(repo, sales, pay):
    scrambled(World(repo, sales, pay))
    AccountingInitService(repo).run(PARAMS, BOSS)
    entries = JournalService(repo).entries(2026, limit=500)
    issues = {e.event_key: e.entry_no for e in entries if e.event_key.startswith("issue:")}
    first_payment = min(e.entry_no for e in entries if e.event_key.startswith("payment:"))
    assert issues["issue:inva00a"] < issues["issue:inva00b"] < issues["issue:inva00c"] < issues["issue:inva00d"]
    assert issues["issue:inva00a"] < first_payment
    assert JournalService(repo).verify_chain(2026) == []


def test_run_sets_start_year_snapshots_and_opening_entry(repo, sales, pay):
    World(repo, sales, pay).a()
    inv = repo.get("invoices", "inva00a")
    repo.save("invoices", {k: v for k, v in inv.items() if k != "issued"})
    params = PARAMS.model_copy(
        update={
            "opening_balances": [
                models.OpeningBalanceIn(role="bank", amount="1000.00"),
                models.OpeningBalanceIn(role="equity", amount="-1000.00"),
            ],
            "frequency": "quarter",
            "dauerfrist": True,
        }
    )
    result = AccountingInitService(repo).run(params, BOSS)
    assert result.counts["opening_entry"] == 1 and result.counts["snapshots"] == 1
    assert repo.settings()[ACCOUNTING_START_KEY] == "2026-01-01"
    (fy,) = repo.find("fiscal_years", year=2026)
    assert (fy["chart"], fy["income_basis"], fy["vat_basis"], fy["vat_frequency"], fy["dauerfrist"]) == (
        "skr03", "accrual", "soll", "quarter", True,
    )  # fmt: skip
    assert repo.get("invoices", "inva00a")["issued"]["gross"] == 119000
    opening = JournalService(repo).get_by_event_key("open:2026:balances")
    assert opening is not None and str(opening.document_date) == "2026-01-01"
    assert JournalService(repo).get_by_event_key("issue:inva00a") is not None


def test_second_run_is_409_and_changes_nothing(repo, sales, pay):
    World(repo, sales, pay).a()
    svc = AccountingInitService(repo)
    svc.run(PARAMS, BOSS)
    before = canon(repo, skip_open=False)
    with pytest.raises(ServiceError) as err:
        svc.run(PARAMS, BOSS)
    assert err.value.status == 409
    assert canon(repo, skip_open=False) == before


def test_gmbh_must_use_accrual_and_nothing_is_written(repo, sales, pay):
    World(repo, sales, pay).a()
    repo.save("invoices", {k: v for k, v in repo.get("invoices", "inva00a").items() if k != "issued"})
    repo.save_settings({"company_type": "gmbh"})
    with pytest.raises(ServiceError) as err:
        AccountingInitService(repo).run(EUR, BOSS)
    assert err.value.status == 422
    assert repo.find("fiscal_years") == [] and ACCOUNTING_START_KEY not in repo.settings()
    assert "issued" not in repo.get("invoices", "inva00a")


def test_start_must_be_first_of_month(repo):
    with pytest.raises(ServiceError) as err:
        AccountingInitService(repo).run(PARAMS.model_copy(update={"accounting_start": "2026-01-15"}), BOSS)
    assert err.value.status == 422


def test_opening_balances_refused_under_eur(repo):
    balance = models.OpeningBalanceIn(role="bank", amount="1.00")
    with pytest.raises(ServiceError) as err:
        AccountingInitService(repo).run(EUR.model_copy(update={"opening_balances": [balance]}), BOSS)
    assert err.value.status == 422


def test_review_cases_refuse_with_the_list_and_write_nothing(repo, sales, pay):
    cu = customer(repo)
    keep = dict(customer_id=cu["id"], lines=[line("100", "19")], status="sent")
    repo.save("invoices", dict(id="preopen1", date="2025-12-10", number="R-PRE", **keep))
    repo.save("invoices", dict(id="oldcancel", date="2026-02-02", number="R-CAN", **{**keep, "status": "cancelled"}))
    sup = repo.save("suppliers", {"name": "Demo Supplier", "kind": "goods", "vat_mode": "eu_reverse_charge"})
    repo.save("expenses", {"id": "rcnosup1", **RC_RAW})
    repo.save("expenses", {"id": "rctyped1", **RC_RAW, "vat": 19.0, "supplier_id": sup["id"]})
    ok = {"id": "okexp001", "date": "2026-03-03", "net": 100.0, "vat": 19.0, "vat_rate": 19.0, "category": "software"}
    repo.save("expenses", ok)
    World(repo, sales, pay).a()
    with pytest.raises(ServiceError) as err:
        AccountingInitService(repo).run(PARAMS, BOSS)
    assert err.value.status == 409
    review = err.value.body["review"]
    assert {(r["id"], r["code"]) for r in review} == {
        ("preopen1", "prestart_open_invoice"),
        ("oldcancel", "legacy_cancelled"),
        ("rcnosup1", "rc_without_supplier"),
        ("rctyped1", "rc_stored_vat"),
    }
    assert all(r["key"] and r["message"].endswith("coming in a later step") for r in review)
    assert all_entries(repo) == []
    assert repo.find("fiscal_years") == [] and ACCOUNTING_START_KEY not in repo.settings()
    assert "issued" not in repo.get("invoices", "preopen1") and "issued" not in repo.get("invoices", "oldcancel")


def test_vat_date_before_start_and_prepaid_are_listed(repo):
    cu = customer(repo)
    repo.save(
        "invoices",
        dict(id="solldec1", customer_id=cu["id"], date="2026-01-05", number="R-1", status="sent",
             service_to="2025-12-20", lines=[line("100", "19")]),
    )  # fmt: skip
    pp = {"id": "prepaid1", "date": "2026-02-01", "net": 10.0, "vat": 1.9, "vat_rate": 19.0, "category": "software"}
    repo.save("expenses", {**pp, "paid_on": "2025-12-30"})
    got = {(r.id, r.code) for r in AccountingInitService(repo).check(PARAMS)}
    assert got == {("solldec1", "vat_before_start"), ("prepaid1", "expense_prepaid")}
    ist = PARAMS.model_copy(update={"vat_basis": "ist"})
    assert {(r.id, r.code) for r in AccountingInitService(repo).check(ist)} == {("prepaid1", "expense_prepaid")}


def test_a_fully_settled_pre_start_invoice_is_no_review_case(repo):
    cu = customer(repo)
    repo.save(
        "invoices",
        dict(id="oldinv01", customer_id=cu["id"], date="2025-11-03", number="R-O", status="paid", paid_date="2025-12-01",
             lines=[line("100", "19")]),
    )  # fmt: skip
    assert AccountingInitService(repo).check(PARAMS) == []
    AccountingInitService(repo).run(PARAMS, BOSS)
    assert all_entries(repo) == []
    assert repo.get("invoices", "oldinv01")["issued"]["gross"] == 11900  # step 3: the snapshot only


# ---------------------------------------------------------------- CLI
def _cli(tmp_path, *words: str) -> None:
    from permita38.cli import main as cli

    cli.main(["--data", str(tmp_path / "data"), "--config", str(tmp_path / "cfg.yaml"), *words])


def test_cli_init_prints_the_result(tmp_path, capsys):
    cmd = ["accounting", "init", "--chart", "skr03", "--income-basis", "eur", "--start", "2026-01-01"]
    _cli(tmp_path, *cmd)
    assert "Accounting initialised: fiscal year 2026, start 2026-01-01." in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_:  # already initialised
        _cli(tmp_path, *cmd)
    assert "already initialised" in str(exit_.value.code)


def test_cli_init_with_review_cases_exits_1_and_lists_them(tmp_path, capsys):
    r = create_repository(Config(data_dir=tmp_path / "data", secret_key="x" * 16))
    r.startup()
    r.save("expenses", {"id": "rcnosup1", **RC_RAW})
    r.close()
    cmd = ["accounting", "init", "--chart", "skr03", "--income-basis", "eur", "--start", "2026-01-01"]
    with pytest.raises(SystemExit) as exit_:
        _cli(tmp_path, *cmd)
    assert exit_.value.code == 1
    assert "expenses:rcnosup1:rc_without_supplier" in capsys.readouterr().err


def test_cli_opening_file(tmp_path, capsys, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(Path(__file__).parent / "fixtures" / "chart_synthetic.json", charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    f = tmp_path / "open.json"
    f.write_text(json.dumps([{"role": "bank", "amount": "100.00"}, {"role": "equity", "amount": "-100.00"}]))
    _cli(tmp_path, "accounting", "init", "--chart", "skr03", "--income-basis", "accrual", "--start", "2026-01-01",
         "--opening-file", str(f))  # fmt: skip
    assert "opening_entry: 1" in capsys.readouterr().out


# ---------------------------------------------------------------- API and rights
def test_api_init_review_and_rights(clients):
    boss, acc, emp = clients["boss"], clients["accountant"], clients["employee"]
    body = {"chart": "skr03", "income_basis": "eur", "accounting_start": "2026-01-01"}
    assert acc.post("/api/accounting/init", json=body).status_code == 403
    assert emp.post("/api/accounting/init", json=body).status_code == 403
    assert emp.get("/api/accounting/init/review").status_code == 403
    assert acc.get("/api/accounting/init/review").json() == []
    r = boss.post("/api/accounting/init", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["accounting_start"] == "2026-01-01" and r.json()["year"] == 2026
    assert boss.post("/api/accounting/init", json=body).status_code == 409


def test_api_refusal_carries_the_review_list(app, clients):
    app.state.services.store.save("expenses", {"id": "rcnosup1", **RC_RAW})
    body = {"chart": "skr03", "income_basis": "eur", "accounting_start": "2026-01-01"}
    r = clients["boss"].post("/api/accounting/init", json=body)
    assert r.status_code == 409
    assert [x["code"] for x in r.json()["review"]] == ["rc_without_supplier"]
    got = clients["accountant"].get("/api/accounting/init/review", params={"accounting_start": "2026-01-01"}).json()
    assert [x["id"] for x in got] == ["rcnosup1"]
