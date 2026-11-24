# ruff: noqa: F811
"""Credit-note numbering (credit-notes-payments.md 4, accounting.md 22 E11b): the sequence ``credit_note``, its
settings, the numbering API and the kind-aware totals of ``finance()`` and ``procurement.hints``."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from permita38.core.calc import finance
from permita38.core.errors import ServiceError
from permita38.core.models import SettingsIn
from permita38.core.service.numbering import NumberingService
from permita38.core.service.procurement import ProcurementService

from .test_credit_notes import cn  # noqa: F401  (fixture)
from .test_invoice_freeze import store  # noqa: F401  (fixture)
from .test_issue_posting import customer, draft, init, line, repo, sales  # noqa: F401  (fixtures and helpers)
from .test_payments import BOSS, pay  # noqa: F401  (fixture)
from .test_rbac import clients  # noqa: F401  (fixture)


def issue(repo, sales, day, net, vat="19", **extra):
    inv = draft(repo, day, [line(net, vat)], **extra)
    return sales.finalize("invoices", inv["id"])


def set_settings(repo, **values):
    repo.save_settings({**repo.settings(), **values})


# ---------------------------------------------------------------- settings
def test_defaults_and_bounds():
    s = SettingsIn()
    assert (s.credit_note_pattern, s.credit_note_reset) == ("RK-{year}-{n:04d}", "yearly")
    assert SettingsIn(credit_note_pattern="GS-{n}", credit_note_reset="never").credit_note_reset == "never"
    for bad in (
        {"credit_note_pattern": "x" * 101},
        {"credit_note_pattern": "no counter"},
        {"credit_note_reset": "daily"},
    ):
        with pytest.raises(ValidationError):
            SettingsIn(**bad)


def test_the_settings_endpoint_lists_the_defaults_and_guards_the_keys(boss_client):
    got = boss_client.get("/api/settings").json()
    assert got["credit_note_pattern"] == "RK-{year}-{n:04d}" and got["credit_note_reset"] == "yearly"
    body = {**got, "credit_note_pattern": "XX-{n}"}
    assert boss_client.put("/api/settings", json=body).status_code == 400  # only through the numbering service


# ---------------------------------------------------------------- numbering service
def test_rk_numbers_use_their_own_counter_and_reset_yearly(repo, sales, cn):
    init(repo)
    inv = issue(repo, sales, "2026-03-02", "1000.00")
    assert inv["number"] == "RE-2026-0001"
    a = cn.create(inv["id"], credit_kind="partial", date="2026-03-05", reason="x", amounts={"19": "100.00"}, user=BOSS)
    b = cn.create(inv["id"], credit_kind="partial", date="2026-03-06", reason="x", amounts={"19": "100.00"}, user=BOSS)
    assert (a["number"], b["number"]) == ("RK-2026-0001", "RK-2026-0002")
    numbering = NumberingService(repo, clock=lambda: date(2026, 3, 7))
    with repo.transaction():
        assert numbering.allocate("credit_note", date(2027, 1, 4)) == "RK-2027-0001"  # a new year restarts
        assert numbering.allocate("credit_note", date(2026, 4, 1)) == "RK-2026-0003"
    assert numbering.preview("invoice", 1) == ["RE-2026-0002"]  # the invoice counter is untouched


def test_pattern_and_reset_come_from_the_settings(repo):
    set_settings(repo, credit_note_pattern="GS-{year}{month}-{n:03d}", credit_note_reset="monthly")
    numbering = NumberingService(repo, clock=lambda: date(2026, 3, 7))
    with repo.transaction():
        assert numbering.allocate("credit_note", date(2026, 3, 1)) == "GS-202603-001"
        assert numbering.allocate("credit_note", date(2026, 3, 9)) == "GS-202603-002"
        assert numbering.allocate("credit_note", date(2026, 4, 1)) == "GS-202604-001"
    assert numbering.status("credit_note")["reset"] == "monthly"


def test_shared_numbering_never_pulls_credit_notes_in(repo, sales, cn):
    init(repo)
    set_settings(repo, numbering_shared=True)
    inv = issue(repo, sales, "2026-03-02", "1000.00")
    note = cn.create(inv["id"], credit_kind="storno", date="2026-03-05", reason="x", user=BOSS)
    assert note["number"] == "RK-2026-0001"
    numbering = NumberingService(repo, clock=lambda: date(2026, 3, 7))
    assert numbering.status("credit_note")["highest_in_period"] == 1
    assert numbering.status("invoice")["highest_in_period"] == 1  # the RK row is not counted for the invoices


def test_unknown_type_is_rejected(repo):
    with pytest.raises(ServiceError):
        NumberingService(repo).preview("receipt")


# ---------------------------------------------------------------- numbering API
def test_the_numbering_api_lists_credit_notes(boss_client):
    r = boss_client.get("/api/numbering/status", params={"type": "credit_note"})
    assert r.status_code == 200
    body = r.json()
    assert (body["type"], body["pattern"], body["reset"]) == ("credit_note", "RK-{year}-{n:04d}", "yearly")
    assert body["next"][0].startswith("RK-") and body["next"][0].endswith("-0001")
    preview = boss_client.get("/api/numbering/preview", params={"type": "credit_note"}).json()
    assert preview["next"][0] == body["next"][0]
    r = boss_client.put(
        "/api/numbering", json={"type": "credit_note", "pattern": "GS-{n:04d}", "reset": "never", "confirm": True}
    )
    assert r.status_code == 200 and r.json()["applied"] is True
    assert boss_client.get("/api/settings").json()["credit_note_pattern"] == "GS-{n:04d}"
    assert boss_client.get("/api/numbering/status", params={"type": "credit_note"}).json()["next"][0] == "GS-0001"
    assert boss_client.get("/api/numbering/status", params={"type": "invoice"}).json()["pattern"] == "RE-{year}-{n:04d}"
    assert boss_client.get("/api/numbering/log", params={"type": "credit_note"}).json()[0]["doc_type"] == "credit_note"
    assert boss_client.get("/api/numbering/status", params={"type": "bogus"}).status_code == 422


# ---------------------------------------------------------------- finance()
def month(result, m):
    return next(x for x in result["months"] if x["month"] == m)


def test_finance_nets_a_partial_credit_note_on_its_own_date_accrual(repo, sales, cn):
    init(repo)
    set_settings(repo, accounting="accrual")
    inv = issue(repo, sales, "2026-03-02", "1000.00")
    cn.create(inv["id"], credit_kind="partial", date="2026-04-05", reason="x", amounts={"19": "100.00"}, user=BOSS)
    res = finance(repo, 2026, today=date(2026, 6, 1))
    assert (month(res, 3)["revenue"], month(res, 3)["vat_out"]) == (Decimal("1000.00"), Decimal("190.00"))
    assert (month(res, 4)["revenue"], month(res, 4)["vat_out"]) == (Decimal("-100.00"), Decimal("-19.00"))
    assert res["totals"]["revenue"] == 900


def test_finance_nets_an_unpaid_partial_credit_note_in_the_cash_basis(repo, sales, cn):
    init(repo)
    inv = issue(repo, sales, "2026-03-02", "1000.00")
    cn.create(inv["id"], credit_kind="partial", date="2026-04-05", reason="x", amounts={"19": "100.00"}, user=BOSS)
    res = finance(repo, 2026, today=date(2026, 6, 1))
    assert month(res, 3)["revenue"] == 0 and month(res, 4)["revenue"] == Decimal("-100.00")
    assert res["open_receivables"] == 1071  # 1190.00 gross less the credited 119.00


def test_a_storno_leaves_no_revenue_in_either_basis(repo, sales, cn):
    init(repo)
    inv = issue(repo, sales, "2026-03-02", "1000.00")
    cn.create(inv["id"], credit_kind="storno", date="2026-04-05", reason="x", user=BOSS)
    for basis in ("eur", "accrual"):
        set_settings(repo, accounting=basis)
        res = finance(repo, 2026, today=date(2026, 6, 1))
        assert res["totals"]["revenue"] == 0 and res["totals"]["vat_out"] == 0, basis


# ---------------------------------------------------------------- procurement.hints
def test_hints_net_the_credit_note_against_the_customers_revenue(repo, sales, cn):
    init(repo)
    set_settings(repo, accounting="accrual", customer_dominance_percent=90)
    big = issue(repo, sales, "2026-03-02", "1000.00", customer=customer(repo))
    issue(repo, sales, "2026-03-03", "100.00", customer=customer(repo))
    svc = ProcurementService(repo)
    today = date(2026, 6, 1)
    assert [h["details"]["revenue_customer"] for h in svc.hints(today)] == [1000.0]  # 1000 / 1100 = 90.9 %
    cn.create(big["id"], credit_kind="partial", date="2026-04-05", reason="x", amounts={"19": "300.00"}, user=BOSS)
    assert svc.hints(today) == []  # 700 / 800 = 87.5 %; the credit note is not counted as revenue
