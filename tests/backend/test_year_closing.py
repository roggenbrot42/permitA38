"""Year closing (accrual) and EÜR year end (docs/specs/accounting.md 7.6, 11, 12, 13, 17, 18) with the SYNTHETIC chart.

The scenario of the accrual year 2025 (cents, hand-computed, accounts of the synthetic chart in brackets):

    opening   bank +100000 [9001], equity -100000 [9049]
    sale      bank +11900, revenue_s19 -10000 [9007], vat_out_s19 -1900 [9013]
    cost      expense_other +2000 [9039], input_vat_s19 +380 [9022], bank -2380
    VAT paid  vat_prepayments +1000 [9028], bank -1000
    provision trade_tax_expense +500 [9042], trade_tax_provision -500 [9043]   (confirmed at the closing)

    bank = 100000 + 11900 - 2380 - 1000 = 108520
    VAT accounts: -1900 + 380 + 1000 = -520  -> vat_previous_year [9030] -520
    P&L: -10000 + 2000 + 500 = -7500          -> profit_carryforward [9052] -7500 (a profit of 75.00)
    closing balances: bank 108520, equity -100000, provision -500, vat_previous_year -520, carryforward -7500 (sum 0)
"""

import shutil
from datetime import date

import pytest

from permita38.core import charts
from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.repository import create_repository
from permita38.core.service.accounting_reports import AccountingReportService
from permita38.core.service.journal import JournalService, JournalSource, PostLine, entry_hash
from permita38.core.service.locks import LockService
from permita38.core.service.tax_bookings import TaxBookingService
from permita38.core.service.year_closing import ProvisionInput, YearClosingService
from permita38.core.settings import ACCOUNTING_START_KEY

from .test_journal_service import FIXTURE, fiscal_year, tamper
from .test_rbac import PASSWORD, _login

USER = {"id": "u1x000", "role": "boss"}
ACC = {
    "bank": "9001", "revenue": "9007", "vat_out": "9013", "input_vat": "9022", "prepay": "9028", "vat_prev": "9030",
    "expense": "9039", "tte": "9042", "provision": "9043", "equity": "9049", "opening": "9050", "closing": "9051",
    "carry": "9052",
}  # fmt: skip
TODAY = date(2026, 3, 1)
RETURN = {
    "year": 2025,
    "status": "draft",
    "from_date": "2025-01-01",
    "to_date": "2025-01-31",
    "due_date": "2025-02-10",
    "amends_key": "",
}


class World:
    def __init__(self, repo, basis: str):
        self.repo = repo
        self.clock = lambda: 1_800_000_000
        self.journal = JournalService(repo, clock=self.clock)
        self.locks = LockService(repo, today=lambda: TODAY, clock=self.clock)
        self.tax = TaxBookingService(repo, self.journal)
        self.closing = YearClosingService(repo, self.journal, self.locks, self.tax)
        self.basis = basis

    def post(self, key, day, text, lines):
        src = JournalSource(event="test", created_by="u1x000", source_coll="invoices", source_id="x")
        return self.journal.post(key, day, text, src, lines)

    def scenario(self):
        self.tax.opening_balances(2025, [("bank", 100000), ("equity", -100000)], USER)
        d = date(2025, 3, 10)
        self.post(
            "sale", d, "Sale",
            [
                PostLine(role="bank", amount_cents=11900),
                PostLine(role="revenue_s19", amount_cents=-10000, income_date=d),
                PostLine(
                    role="vat_out_s19", amount_cents=-1900, tax_key="S19", tax_kind="tax", tax_rate="19",
                    tax_base_cents=10000, vat_date=d,
                ),
            ],
        )  # fmt: skip
        e = date(2025, 4, 2)
        self.post(
            "cost", e, "Cost",
            [
                PostLine(role="expense_other", amount_cents=2000, income_date=e),
                PostLine(
                    role="input_vat_s19", amount_cents=380, tax_key="VST", tax_kind="tax", tax_rate="19",
                    tax_base_cents=2000, vat_date=e,
                ),
                PostLine(role="bank", amount_cents=-2380),
            ],
        )  # fmt: skip
        self.tax.vat_payment("ret", date(2025, 4, 10), 1000, "payment", USER, entry_id="pay1")

    def lock_november(self):
        self.locks.lock_up_to(2025, 11, "manual", "u1x000")

    def balances(self, year=None):
        """Cumulated sum per account over all entries (of ``year`` only when given)."""
        out: dict[str, int] = {}
        offset = 0
        while True:
            page = self.repo.list_journal_entries(year=year, limit=500, offset=offset)
            for e in page:
                for ln in e["lines"]:
                    out[ln["account"]] = out.get(ln["account"], 0) + int(ln["amount_cents"])
            if len(page) < 500:
                return {k: v for k, v in out.items() if v}
            offset += 500


def make_world(tmp_path, monkeypatch, basis):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURE, charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    repo = create_repository(Config(data_dir=tmp_path / "data", secret_key="x" * 16))
    repo.startup()
    repo.save("fiscal_years", fiscal_year(2025, income_basis=basis))
    repo.save_settings({ACCOUNTING_START_KEY: "2025-01-01", "company_type": "gewerbe"})
    return World(repo, basis)


@pytest.fixture
def accrual(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch, "accrual")
    yield w
    w.repo.close()


@pytest.fixture
def eur(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch, "eur")
    yield w
    w.repo.close()


def lines_of(entry) -> list[tuple[str, int]]:
    return [(ln.account, round(float(ln.amount) * 100)) for ln in entry.lines]


# ------------------------------------------------------------------ accrual closing
def test_accrual_closing_with_hand_computed_balances(accrual):
    w = accrual
    w.scenario()
    assert w.balances(2025)[ACC["bank"]] == 108520
    w.lock_november()

    prev = w.closing.preview(2025, [ProvisionInput("trade_tax", 500)])
    assert prev.blockers == ()
    assert [(p.kind, p.prepayments_expensed_cents, p.booked_cents) for p in prev.provisions] == [("trade_tax", 0, 0)]
    assert [e.event_key for e in prev.plan] == ["close:2025:vat", "close:2025:pnl", "close:2025:balance"]
    nothing_posted = w.repo.list("journal_entries")
    assert all(not str(e["event_key"]).startswith("close:") for e in nothing_posted)  # a preview posts nothing

    closed = w.closing.close(2025, [ProvisionInput("trade_tax", 500)], USER)
    assert closed["closed_at"] is not None and closed["closed_by"] == "u1x000"

    entries = {e.event_key: e for e in w.journal.entries(2025, limit=100)}
    assert lines_of(entries["manual:trade_tax_provision:2025:r1"]) == [(ACC["tte"], 500), (ACC["provision"], -500)]
    # VAT accounts -> vat_previous_year: -1900 + 380 + 1000 = -520
    assert lines_of(entries["close:2025:vat"]) == [
        (ACC["vat_out"], 1900), (ACC["input_vat"], -380), (ACC["prepay"], -1000), (ACC["vat_prev"], -520),
    ]  # fmt: skip
    # P&L -> profit_carryforward: revenue -10000, expense +2000, trade tax +500: result -7500 (profit 75.00)
    pnl = entries["close:2025:pnl"]
    assert lines_of(pnl) == [
        (ACC["revenue"], 10000),
        (ACC["expense"], -2000),
        (ACC["tte"], -500),
        (ACC["carry"], -7500),
    ]
    assert all(ln.income_date is None for ln in pnl.lines)  # the P&L report of the year stays as it was
    # every balance account -> closing_balance (debit total 108520, credit total as the second line)
    assert lines_of(entries["close:2025:balance"]) == [
        (ACC["bank"], -108520), (ACC["vat_prev"], 520), (ACC["provision"], 500), (ACC["equity"], 100000),
        (ACC["carry"], 7500), (ACC["closing"], 108520), (ACC["closing"], -108520),
    ]  # fmt: skip
    for e in entries.values():
        assert sum(c for _, c in lines_of(e)) == 0
        if e.event_key.startswith("close:"):
            assert e.booking_date == "2025-12-31" and e.document_date == "2025-12-31" and e.origin == "auto"

    # the year itself nets to 0 on every account; the next year carries exactly the closing balances
    assert w.balances(2025) == {}
    opening = w.journal.get_by_event_key("open:2026:balances")
    assert opening is not None and opening.fiscal_year == 2026 and opening.booking_date == "2026-01-01"
    assert lines_of(opening) == [
        (ACC["bank"], 108520), (ACC["vat_prev"], -520), (ACC["provision"], -500), (ACC["equity"], -100000),
        (ACC["carry"], -7500), (ACC["opening"], -108520), (ACC["opening"], 108520),
    ]  # fmt: skip
    # all years together: P&L and VAT accounts are 0 except vat_previous_year
    assert w.balances() == {
        ACC["bank"]: 108520, ACC["equity"]: -100000, ACC["provision"]: -500, ACC["vat_prev"]: -520,
        ACC["carry"]: -7500,
    }  # fmt: skip
    # the P&L of 2025 is a profit of 75.00 = minus profit_carryforward
    assert float(AccountingReportService(w.repo).pnl(2025).profit) == 75.0

    # December locked by the year closing, the year anchored, 2026 created from 2025
    assert w.locks.locked_months(2025) == list(range(1, 13))
    assert [r["reason"] for r in w.repo.list("period_locks") if r["month"] == 12] == ["year_close"]
    last = w.repo.list_journal_entries(year=2025, limit=500)[-1]
    assert (closed["closing_hash"], closed["closing_entry_no"]) == (last["hash"], last["entry_no"])
    assert closed["closing_entry_no"] == len(w.repo.list_journal_entries(year=2025, limit=500)) == 8
    nxt = w.repo.find("fiscal_years", year=2026)[0]
    assert (nxt["chart"], nxt["income_basis"], nxt.get("closed_at")) == ("skr03", "accrual", None)
    assert w.journal.verify_chain(2025) == [] and w.journal.verify_chain(2026) == []


def test_preview_proposes_the_prepayments_already_expensed(accrual):
    w = accrual
    w.scenario()
    w.tax.trade_tax_payment(date(2025, 6, 10), 300, "payment", USER, entry_id="gew1")
    w.lock_november()
    prev = w.closing.preview(2025)
    assert [(p.kind, p.prepayments_expensed_cents, p.booked_cents) for p in prev.provisions] == [("trade_tax", 300, 0)]
    # without a confirmed provision the 300 expense is in the result: -10000 + 2000 + 300
    pnl = next(e for e in prev.plan if e.event_key == "close:2025:pnl")
    assert [(ln.account, ln.amount_cents) for ln in pnl.lines][-1] == (ACC["carry"], -7700)


def test_closing_is_refused_while_a_month_before_december_is_unlocked(accrual):
    w = accrual
    w.scenario()
    w.locks.lock_up_to(2025, 10, "manual", "u1x000")  # November is open
    prev = w.closing.preview(2025)
    assert prev.blockers == ("2025-11 is not locked; lock the months up to November first",)
    before = len(w.repo.list("journal_entries"))
    with pytest.raises(ServiceError) as err:
        w.closing.close(2025, [ProvisionInput("trade_tax", 500)], USER)
    assert err.value.status == 409 and "2025-11" in err.value.message
    # nothing was posted (not even the provision) and no month was locked silently
    assert len(w.repo.list("journal_entries")) == before and w.locks.locked_months(2025) == list(range(1, 11))
    assert w.repo.find("fiscal_years", year=2025)[0].get("closed_at") is None


def test_closing_is_refused_with_a_draft_vat_return_before_december(accrual):
    w = accrual
    w.scenario()
    w.lock_november()
    w.repo.save("vat_returns", {**RETURN, "period_kind": "month", "period_no": 12})
    assert w.closing.preview(2025).blockers == ()  # the December return may stay a draft
    w.repo.save("vat_returns", {**RETURN, "period_kind": "quarter", "period_no": 2})
    assert w.closing.preview(2025).blockers == ("A VAT return of 2025 (quarter 2) is still a draft",)
    with pytest.raises(ServiceError) as err:
        w.closing.close(2025, [], USER)
    assert err.value.status == 409


def test_a_year_that_has_not_ended_cannot_be_closed(accrual):
    w = accrual
    w.locks.today = lambda: date(2025, 12, 31)
    w.locks.lock_up_to(2025, 11, "manual", "u1x000")
    assert w.closing.preview(2025).blockers == ("December 2025 has not ended yet",)


def test_closing_twice_is_409(accrual):
    w = accrual
    w.scenario()
    w.lock_november()
    w.closing.close(2025, [], USER)
    count = len(w.repo.list("journal_entries"))
    with pytest.raises(ServiceError) as err:
        w.closing.close(2025, [], USER)
    assert err.value.status == 409 and "closed already" in err.value.message
    assert len(w.repo.list("journal_entries")) == count


def test_unknown_year_is_404(accrual):
    with pytest.raises(ServiceError) as err:
        accrual.closing.close(2031, [], USER)
    assert err.value.status == 404


def test_eur_refuses_provisions(eur):
    eur.lock_november()
    with pytest.raises(ServiceError) as err:
        eur.closing.close(2025, [ProvisionInput("trade_tax", 500)], USER)
    assert err.value.status == 422


def test_late_documents_after_the_closing_go_to_the_next_year(accrual):
    w = accrual
    w.scenario()
    w.lock_november()
    w.closing.close(2025, [], USER)
    late = date(2025, 12, 20)
    e = w.post(
        "late", late, "Late bill",
        [
            PostLine(role="expense_other", amount_cents=1000, income_date=late),
            PostLine(role="bank", amount_cents=-1000),
        ],
    )  # fmt: skip
    # booked on the first open day of 2026; its income date (in a closed year) becomes the booking date
    assert (e.fiscal_year, e.booking_date, e.document_date) == (2026, "2026-01-01", "2025-12-20")
    assert e.lines[0].income_date == "2026-01-01"
    assert w.journal.verify_chain(2025) == []  # the closed year did not move


# ------------------------------------------------------------------ EÜR year end
def test_eur_year_end_locks_december_and_opens_the_next_year(eur):
    w = eur
    w.post(
        "e1", date(2025, 5, 5), "Cost",
        [
            PostLine(role="expense_other", amount_cents=500, income_date=date(2025, 5, 5)),
            PostLine(role="bank", amount_cents=-500),
        ],
    )  # fmt: skip
    w.lock_november()
    count = len(w.repo.list("journal_entries"))
    closed = w.closing.close(2025, [], USER)
    assert len(w.repo.list("journal_entries")) == count  # no closing entry, no opening entry
    assert closed["closed_at"] is not None and closed["closing_entry_no"] == 1
    assert closed["closing_hash"] == w.repo.list_journal_entries(year=2025)[0]["hash"]
    assert w.locks.locked_months(2025) == list(range(1, 13))
    nxt = w.repo.find("fiscal_years", year=2026)[0]
    assert (nxt["income_basis"], nxt["chart"]) == ("eur", "skr03")
    assert w.journal.verify_chain(2025) == []
    with pytest.raises(ServiceError) as err:
        w.closing.end_year(2025, USER)
    assert err.value.status == 409


def test_eur_year_end_needs_the_months_before_december_locked(eur):
    w = eur
    w.locks.lock_up_to(2025, 6, "manual", "u1x000")
    with pytest.raises(ServiceError) as err:
        w.closing.end_year(2025, USER)
    assert err.value.status == 409 and w.locks.locked_months(2025) == list(range(1, 7))


def test_eur_year_without_entries_anchors_the_empty_chain(eur):
    w = eur
    w.lock_november()
    closed = w.closing.end_year(2025, USER)
    assert (closed["closing_hash"], closed["closing_entry_no"]) == ("0" * 64, 0)
    assert w.journal.verify_chain(2025) == []


# ------------------------------------------------------------------ the chain anchor
def _closed_year(w):
    w.scenario()
    w.lock_november()
    w.closing.close(2025, [ProvisionInput("trade_tax", 500)], USER)
    return w


def test_a_tampered_closed_year_is_detected_through_closing_hash(accrual):
    w = _closed_year(accrual)
    last = w.repo.list_journal_entries(year=2025, limit=500)[-1]
    # change the last entry and recompute its own hash, so the per-entry checks pass and only the anchor can tell
    changed = {**last, "text": "Changed afterwards"}
    new_hash = entry_hash(str(last["prev_hash"]), changed, last["lines"])
    tamper(
        w.repo,
        "UPDATE journal_entries SET text = ?, hash = ? WHERE id = ?",
        ("Changed afterwards", new_hash, last["id"]),
    )
    assert [(f.code, f.entry_no) for f in w.journal.verify_chain(2025)] == [("journal.closing_hash", 8)]
    assert w.journal.verify_chain(2026) == []


def test_a_truncated_closed_year_is_detected_through_closing_entry_no(accrual):
    w = _closed_year(accrual)
    last = w.repo.list_journal_entries(year=2025, limit=500)[-1]
    with w.repo.transaction():
        for t in ("guard_journal_lines_append_only_del", "guard_journal_entries_append_only_del"):
            w.repo._exec(f'DROP TRIGGER IF EXISTS "{t}"')
        w.repo._exec("DELETE FROM journal_lines WHERE entry_id = ?", (last["id"],))
        w.repo._exec("DELETE FROM journal_entries WHERE id = ?", (last["id"],))
    codes = sorted(f.code for f in w.journal.verify_chain(2025))
    assert codes == ["journal.closing_count", "journal.closing_hash"]
    assert w.journal.verify_chain(2026) == []


def test_a_closed_year_without_its_anchor_is_a_finding(accrual):
    w = accrual
    w.repo.save("fiscal_years", fiscal_year(2024, closed_at=5, closed_by="u1x000"))
    assert [f.code for f in w.journal.verify_chain(2024)] == ["journal.closing_missing"]


# ------------------------------------------------------------------ the route
@pytest.fixture
def api(app, boss_client, chart_dir):
    r = boss_client.post("/api/accounting/years", json={"year": 2025, "chart": "skr03", "income_basis": "accrual"})
    assert r.status_code == 201, r.text
    app.state.services.store.save_settings({ACCOUNTING_START_KEY: "2025-01-01"})
    out = {"boss": boss_client}
    r = boss_client.post(
        "/api/users", json={"username": "accx", "name": "Accountant", "role": "accountant", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    out["accountant"] = _login(app, "accx")
    return out


def test_close_route_is_boss_only_and_closes(api, app):
    url = "/api/accounting/years/2025/close"
    assert api["accountant"].post(url).status_code == 403
    assert api["boss"].get("/api/accounting/years/2025").json()["closed_at"] is None
    assert api["boss"].post(url, json={"provisions": [{"kind": "trade_tax", "amount": "0"}]}).status_code == 422
    assert api["boss"].post(url, json={"provisions": [{"kind": "x", "amount": "1.00"}]}).status_code == 422
    r = api["boss"].post(url)
    assert r.status_code == 409 and "2025-01" in r.json()["detail"]  # months before December are open
    assert api["boss"].post("/api/accounting/locks", json={"year": 2025, "month": 11}).status_code == 201
    store = app.state.services.store
    r = api["boss"].post(url, json={"provisions": [{"kind": "trade_tax", "amount": "5.00"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["year"] == 2025 and body["closed_at"] and body["closing_entry_no"] == 3 and len(body["closing_hash"]) == 64
    )
    provision = JournalService(store).get_by_event_key("manual:trade_tax_provision:2025:r1")
    assert provision is not None and [ln.amount for ln in provision.lines] == [5.0, -5.0]
    assert api["boss"].get("/api/accounting/years/2026").status_code == 200
    again = api["boss"].post(url)
    assert again.status_code == 409
