"""TaxBookingService (docs/specs/accounting.md 7, 9.6, 9.7, 12, 16) with the SYNTHETIC chart.

Every expected line is written out by hand (cents); A10 is read line by line from the hand-computed oracle fixture.
"""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from permita38.core import charts
from permita38.core.charts import Role, chart_for
from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.repository import create_repository
from permita38.core.service.journal import JournalService
from permita38.core.service.tax_bookings import TaxBookingService
from permita38.core.settings import ACCOUNTING_START_KEY

from .test_journal_service import FIXTURE, fiscal_year, repo  # noqa: F401  (fixture, helper)

USER = {"id": "u1x000", "role": "boss"}
ORACLE = Path(__file__).parent / "fixtures" / "accounting_oracle.json"


def service(store, start: str = "2026-01-01") -> TaxBookingService:
    store.save_settings({ACCOUNTING_START_KEY: start})
    return TaxBookingService(store, JournalService(store, clock=lambda: 1_800_000_000))


@pytest.fixture
def eur(repo):  # noqa: F811  (the fixture year 2026 is EÜR)
    return service(repo)


@pytest.fixture
def accrual(tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURE, charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    r = create_repository(Config(data_dir=tmp_path / "data", secret_key="x" * 16))
    r.startup()
    r.save("fiscal_years", fiscal_year(2026, income_basis="accrual"))
    yield service(r)
    r.close()


def lines(entry) -> list[tuple[str, int, str | None]]:
    return [(ln.role or "", round(float(ln.amount) * 100), ln.income_date) for ln in entry.lines]


# ------------------------------------------------------------------ VAT (9.6)
def test_vat_payment_and_refund_lines(eur):
    e = eur.vat_payment("ret1", date(2026, 3, 10), 12345, "payment", USER)
    # 123,45 paid: D vat_prepayments 12345 (income_date = payment date) / C bank -12345
    assert e.event_key == "vat:ret1" and e.origin == "auto"
    assert lines(e) == [("vat_prepayments", 12345, "2026-03-10"), ("bank", -12345, None)]
    r = eur.vat_payment("ret2", date(2026, 4, 10), 5000, "refund", USER)
    # 50,00 refunded: D bank 5000 / C vat_prepayments -5000 (income_date = refund date)
    assert lines(r) == [("bank", 5000, None), ("vat_prepayments", -5000, "2026-04-10")]
    assert r.event_key == "vat:ret2"


def test_vat_payment_is_idempotent_and_different_content_is_409(eur):
    first = eur.vat_payment("ret1", date(2026, 3, 10), 12345, "payment", USER)
    again = eur.vat_payment("ret1", date(2026, 3, 10), 12345, "payment", USER)
    assert again.id == first.id and len(JournalService(eur.store).entries(2026)) == 1
    with pytest.raises(ServiceError) as e:
        eur.vat_payment("ret1", date(2026, 3, 10), 999, "payment", USER)
    assert e.value.status == 409


def test_vat_payment_input_rules(eur):
    for bad in (0, -5):
        with pytest.raises(ServiceError) as e:
            eur.vat_payment("r", date(2026, 3, 10), bad, "payment", USER)
        assert e.value.status == 422
    with pytest.raises(ServiceError) as e:
        eur.vat_payment("r", date(2025, 12, 31), 100, "payment", USER)  # before the accounting start 2026-01-01
    assert e.value.status == 422


def test_special_prepayment_and_credit(eur):
    p = eur.special_prepayment(2026, date(2026, 2, 10), 20000, USER)
    # 200,00 paid: D vat_special_prepayment 20000 (income_date = payment date) / C bank
    assert p.event_key == "vat:special:2026"
    assert lines(p) == [("vat_special_prepayment", 20000, "2026-02-10"), ("bank", -20000, None)]
    c = eur.special_prepayment_credit(2026, "dec26", 20000, USER)
    assert c is not None
    # Kz 39 = 200,00: D vat_prepayments 20000 / C vat_special_prepayment -20000, no income_date, 31 Dec
    assert c.event_key == "vat:dec26:credit" and str(c.document_date) == "2026-12-31"
    assert lines(c) == [("vat_prepayments", 20000, None), ("vat_special_prepayment", -20000, None)]
    again = eur.special_prepayment_credit(2026, "dec26", 20000, USER)
    assert again is not None and again.id == c.id
    assert eur.special_prepayment_credit(2026, "dec26b", 0, USER) is None  # Kz 39 = 0: nothing to credit
    assert len(JournalService(eur.store).entries(2026)) == 2


# ------------------------------------------------------------------ 9.7 oracle A10 and the other taxes
def test_a10_taxes_eur_from_the_oracle_fixture(eur):
    case = next(c for c in json.loads(ORACLE.read_text())["journal_cases"] if c["id"] == "A10-taxes-eur")
    assert case["bases"] == {"income_basis": "eur", "vat_basis": "ist"}
    posted = []
    for ev in case["events"]:
        day = date.fromisoformat(ev["date"])
        fn = {"trade_tax_payment": eur.trade_tax_payment, "income_tax_payment": eur.income_tax_payment}[ev["template"]]
        posted.append(fn(day, ev["amount"], "payment", USER))
    assert len(posted) == len(case["entries"]) == 2
    for exp in case["entries"]:
        got = posted[exp["event"]]
        assert str(got.booking_date) == exp["booking_date"]
        want = [(ln[0], ln[1], (ln[2] if len(ln) > 2 else {}).get("income_date")) for ln in exp["lines"]]
        assert lines(got) == want
    # by hand: 250,00 trade tax on 15 Feb, 800,00 income tax on 10 Mar (private withdrawal, no income_date)
    assert lines(posted[0]) == [("trade_tax_expense", 25000, "2026-02-15"), ("bank", -25000, None)]
    assert lines(posted[1]) == [("private_withdrawal", 80000, None), ("bank", -80000, None)]


def test_refunds_and_corporate_tax(eur):
    t = eur.trade_tax_payment(date(2026, 6, 1), 10000, "refund", USER)
    assert lines(t) == [("bank", 10000, None), ("tax_refunds", -10000, "2026-06-01")]
    i = eur.income_tax_payment(date(2026, 6, 2), 7000, "refund", USER)
    assert lines(i) == [("bank", 7000, None), ("private_withdrawal", -7000, None)]
    c = eur.corporate_tax_payment(date(2026, 6, 3), 30000, "payment", USER)
    assert lines(c) == [("corporate_tax_expense", 30000, "2026-06-03"), ("bank", -30000, None)]
    assert c.origin == "manual" and c.event_key is not None and c.event_key.startswith("manual:")


def test_explicit_entry_id_makes_a_payment_idempotent(eur):
    a = eur.trade_tax_payment(date(2026, 2, 15), 25000, "payment", USER, entry_id="fixed-1")
    b = eur.trade_tax_payment(date(2026, 2, 15), 25000, "payment", USER, entry_id="fixed-1")
    assert a.id == b.id and a.event_key == "manual:fixed-1"
    with pytest.raises(ServiceError) as e:
        eur.trade_tax_payment(date(2026, 2, 15), 26000, "payment", USER, entry_id="fixed-1")
    assert e.value.status == 409


# ------------------------------------------------------------------ provision (accrual only)
def test_provision_nets_prepayments_and_settlement_above(accrual):
    svc = accrual
    svc.trade_tax_payment(date(2026, 5, 10), 3000, "payment", USER)
    svc.trade_tax_payment(date(2026, 8, 10), 2000, "payment", USER)
    svc.trade_tax_payment(date(2026, 9, 1), 100, "refund", USER)  # a refund is not a prepayment
    booking = svc.trade_tax_provision(2026, 4500, USER)
    # prepayments expensed in 2026: 30,00 + 20,00 = 5000 cents; provision typed by the user (already net): 45,00
    assert booking.prepayments_expensed_cents == 5000
    assert booking.entry.event_key == "manual:trade_tax_provision:2026"
    assert lines(booking.entry) == [("trade_tax_expense", 4500, "2026-12-31"), ("trade_tax_provision", -4500, None)]
    assert svc.provision_balance("trade_tax") == 4500
    again = svc.trade_tax_provision(2026, 4500, USER)
    assert again.entry.id == booking.entry.id  # idempotent per year

    # above: 60,00 paid against 45,00: D provision 4500, C bank 6000, D expense 1500 (income_date = payment date)
    above = svc.settle_trade_tax_provision(date(2027, 3, 15), 6000, USER)
    assert lines(above) == [
        ("trade_tax_provision", 4500, None),
        ("bank", -6000, None),
        ("trade_tax_expense", 1500, "2027-03-15"),
    ]
    assert svc.provision_balance("trade_tax") == 0  # -(-4500 + 4500)
    with pytest.raises(ServiceError) as e:  # nothing left to settle
        svc.settle_trade_tax_provision(date(2027, 4, 1), 100, USER)
    assert e.value.status == 409


def test_settlement_below_the_provision_releases_the_rest(accrual):
    svc = accrual
    svc.trade_tax_provision(2026, 4500, USER)
    below = svc.settle_trade_tax_provision(date(2027, 3, 15), 3000, USER)
    # 30,00 paid against 45,00: D provision 4500, C bank 3000, C tax_refunds 1500 (income_date = payment date)
    assert lines(below) == [
        ("trade_tax_provision", 4500, None),
        ("bank", -3000, None),
        ("tax_refunds", -1500, "2027-03-15"),
    ]
    assert svc.provision_balance("trade_tax") == 0


def test_settlement_equal_to_the_provision_has_no_third_line(accrual):
    svc = accrual
    svc.trade_tax_provision(2026, 4500, USER)
    exact = svc.settle_trade_tax_provision(date(2027, 3, 15), 4500, USER)
    assert lines(exact) == [("trade_tax_provision", 4500, None), ("bank", -4500, None)]


def test_corporate_provision_and_settlement(accrual):
    svc = accrual
    b = svc.corporate_tax_provision(2026, 9000, USER)
    assert lines(b.entry) == [("corporate_tax_expense", 9000, "2026-12-31"), ("corporate_tax_provision", -9000, None)]
    s = svc.settle_corporate_tax_provision(date(2027, 2, 1), 8000, USER)
    assert lines(s) == [
        ("corporate_tax_provision", 9000, None),
        ("bank", -8000, None),
        ("tax_refunds", -1000, "2027-02-01"),
    ]


def test_provision_and_settlement_are_refused_under_eur(eur):
    with pytest.raises(ServiceError) as e:
        eur.trade_tax_provision(2026, 4500, USER)
    assert e.value.status == 422
    with pytest.raises(ServiceError) as e:
        eur.settle_trade_tax_provision(date(2026, 3, 15), 4500, USER)
    assert e.value.status == 422
    assert JournalService(eur.store).entries(2026) == []


# ------------------------------------------------------------------ opening balances (12, 16)
def test_opening_balances_balanced_against_opening_balance(accrual):
    svc = accrual
    e = svc.opening_balances(2026, [("bank", 10000), ("cash", 500), ("equity", -10500)], USER)
    # assets 100,00 + 5,00 = 10500 debit; equity 10500 credit; opening_balance: C 10500 (for the debits), D 10500
    assert e.event_key == "open:2026:balances" and e.origin == "opening" and str(e.document_date) == "2026-01-01"
    assert lines(e) == [
        ("bank", 10000, None),
        ("cash", 500, None),
        ("equity", -10500, None),
        ("opening_balance", -10500, None),
        ("opening_balance", 10500, None),
    ]
    assert sum(ln.amount for ln in e.lines) == 0
    again = svc.opening_balances(2026, [("bank", 10000), ("cash", 500), ("equity", -10500)], USER)
    assert again.id == e.id
    with pytest.raises(ServiceError) as err:  # one entry per year: other content is a conflict
        svc.opening_balances(2026, [("bank", 20000), ("equity", -20000)], USER)
    assert err.value.status == 409


def test_opening_balances_take_an_account_number_and_the_start_date(accrual):
    svc = accrual
    svc.store.save_settings({ACCOUNTING_START_KEY: "2026-04-01"})
    bank_account = str(chart_for("skr03", 2026).account(Role("bank")))
    e = svc.opening_balances(2026, [(bank_account, 700), ("equity", -700)], USER)
    assert str(e.document_date) == "2026-04-01"
    assert e.lines[0].account == bank_account and e.lines[0].role is None and e.lines[1].role == "equity"


def test_unbalanced_opening_balances_are_422(accrual):
    svc = accrual
    for bad in ([("bank", 10000), ("equity", -9000)], [], [("bank", 0), ("equity", -1)]):
        with pytest.raises(ServiceError) as e:
            svc.opening_balances(2026, bad, USER)
        assert e.value.status == 422
    with pytest.raises(ServiceError) as e:
        svc.opening_balances(2026, [("no_such_role", 100), ("equity", -100)], USER)
    assert e.value.status == 422
    assert JournalService(svc.store).entries(2026) == []


def test_opening_balances_are_refused_under_eur(eur):
    with pytest.raises(ServiceError) as e:
        eur.opening_balances(2026, [("bank", 100), ("equity", -100)], USER)
    assert e.value.status == 422


def test_opening_year_before_the_start_is_422(accrual):
    with pytest.raises(ServiceError) as e:
        accrual.opening_balances(2025, [("bank", 100), ("equity", -100)], USER)
    assert e.value.status == 422


# ------------------------------------------------------------------ before initialisation
def test_nothing_posts_before_initialisation(repo):  # noqa: F811
    svc = TaxBookingService(repo, JournalService(repo))  # no accounting.start
    calls = [
        lambda: svc.vat_payment("r", date(2026, 3, 1), 100, "payment", USER),
        lambda: svc.special_prepayment(2026, date(2026, 3, 1), 100, USER),
        lambda: svc.special_prepayment_credit(2026, "r", 100, USER),
        lambda: svc.trade_tax_payment(date(2026, 3, 1), 100, "payment", USER),
        lambda: svc.income_tax_payment(date(2026, 3, 1), 100, "payment", USER),
        lambda: svc.corporate_tax_payment(date(2026, 3, 1), 100, "payment", USER),
        lambda: svc.trade_tax_provision(2026, 100, USER),
        lambda: svc.settle_trade_tax_provision(date(2027, 3, 1), 100, USER),
        lambda: svc.opening_balances(2026, [("bank", 100), ("equity", -100)], USER),
    ]
    for call in calls:
        with pytest.raises(ServiceError) as e:
            call()
        assert e.value.status == 409
    assert JournalService(repo).entries(2026) == []
