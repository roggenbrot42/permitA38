"""Journal, accounts and report routes (docs/specs/accounting.md 14, 18) on a small posted scenario.

The synthetic chart (fake account numbers) is copied in as ``skr03-2026``. Entries are posted through
``JournalService``; all expected figures are hand-computed in the comments (cents in the journal, euros in the API)."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from permita38.core import charts
from permita38.core.service.journal import JournalSource, PostLine

from .conftest import PASSWORD, _login

BASE = "/api/accounting"
# synthetic accounts: bank 9001, receivables 9003, payables 9004, revenue_s19 9007, vat_out_s19 9013,
# input_vat_clearing 9031, expense_software 9035, expense_other 9039


@pytest.fixture
def clients(app, boss_client):
    out = {"boss": boss_client}
    for role in ("accountant", "employee"):
        r = boss_client.post(
            "/api/users", json={"username": role[:3] + "x", "name": role.title(), "role": role, "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        out[role] = _login(app, role[:3] + "x")
    return out


def _post(app, key, day, text, event, coll, sid, number, lines):
    source = JournalSource(event=event, created_by="u1x000", source_coll=coll, source_id=sid, source_number=number)
    app.state.services.journal.post(key, day, text, source, lines)


def get(c, path, **params):
    return c.get(f"{BASE}/{path}", params=params)


# ---------------------------------------------------------------- journal
def test_journal_lists_entries_with_lines_and_decimal_strings(scenario):
    r = get(scenario, "journal")
    assert r.status_code == 200, r.text
    entries = r.json()
    assert [e["entry_no"] for e in entries] == [1, 2, 3, 4]
    first = entries[0]
    assert first["booking_date"] == "2026-01-10" and len(first["hash"]) == 64
    assert [(ln["role"], ln["account"], ln["amount"]) for ln in first["lines"]] == [
        ("receivables", "9003", "119.00"),
        ("revenue_s19", "9007", "-100.00"),
        ("vat_out_s19", "9013", "-19.00"),
    ]
    assert first["lines"][2]["tax_base"] == "100.00"


def test_journal_filters(scenario):
    def nos(**p):
        r = get(scenario, "journal", **p)
        assert r.status_code == 200, r.text
        return [e["entry_no"] for e in r.json()]

    assert nos(account="9013") == [1]  # only the invoice has a VAT line
    assert nos(account="9001") == [2, 3]  # bank
    assert nos(tax_key="S19") == [1]
    assert nos(source="expenses") == [3, 4]
    assert nos(source="invoices") == [1, 2]
    assert nos(**{"from": "2026-02-01"}) == [3, 4]
    assert nos(to="2026-01-31") == [1, 2]
    assert nos(**{"from": "2026-01-20", "to": "2026-02-05"}) == [2, 3]
    assert nos(account="9001", source="expenses") == [3]  # filters combine
    assert nos(account="9999") == []


def test_journal_paging(scenario):
    def nos(**p):
        return [e["entry_no"] for e in get(scenario, "journal", **p).json()]

    assert nos(limit=2) == [1, 2]
    assert nos(limit=2, offset=1) == [2, 3]
    assert nos(limit=2, offset=3) == [4]
    assert nos(offset=10) == []
    assert nos(limit=1, offset=0, account="9001") == [2]  # paging applies after filtering
    assert nos(limit=1, offset=1, account="9001") == [3]
    assert get(scenario, "journal", limit=500).status_code == 200
    assert get(scenario, "journal", limit=501).status_code == 422
    assert get(scenario, "journal", limit=0).status_code == 422
    assert get(scenario, "journal", offset=-1).status_code == 422
    assert get(scenario, "journal", **{"from": "yesterday"}).status_code == 422


# ---------------------------------------------------------------- accounts
def test_accounts_lists_the_chart_of_the_year(scenario):
    r = get(scenario, "accounts", year=2026)
    assert r.status_code == 200, r.text
    rows = {a["account"]: a for a in r.json()}
    assert [a["account"] for a in r.json()] == sorted(rows)
    assert (rows["9001"]["role"], rows["9001"]["account_class"], rows["9001"]["eur_line"]) == ("bank", "balance", None)
    assert rows["9007"]["eur_line"] == "Line revenue_s19" and rows["9007"]["deductible"] is True
    assert rows["9037"]["deductible"] is False
    assert rows["9900"]["role"] is None
    assert get(scenario, "accounts").json() == r.json()  # default: the latest fiscal year


def test_unknown_year_is_404_everywhere(scenario):
    for path in (
        "accounts", "reports/trial-balance", "reports/eur", "reports/pnl", "reports/balance-sheet",
        "reports/open-items",
    ):  # fmt: skip
        assert get(scenario, path, year=2030).status_code == 404, path
    assert get(scenario, "reports/ledger", year=2030, role="bank").status_code == 404


def test_without_any_fiscal_year_the_reports_are_404(boss_client):
    assert get(boss_client, "reports/eur").status_code == 404
    assert get(boss_client, "journal").json() == []


# ---------------------------------------------------------------- reports
def test_trial_balance_sums_to_zero(scenario):
    r = get(scenario, "reports/trial-balance", year=2026)
    assert r.status_code == 200, r.text
    t = r.json()
    assert (t["date_from"], t["date_to"]) == ("2026-01-01", "2026-12-31")
    # account: debit / credit / balance
    #   9001 bank        50.00 / 35.70 / 14.30      9003 receivables 119.00 / 50.00 / 69.00
    #   9004 payables     0.00 / 20.00 / -20.00     9007 revenue       0.00 / 100.00 / -100.00
    #   9013 vat out      0.00 / 19.00 / -19.00     9031 input VAT     5.70 / 0.00 / 5.70
    #   9035 software   30.00 /  0.00 / 30.00       9039 other        20.00 / 0.00 / 20.00
    assert [(x["account"], x["debit"], x["credit"], x["balance"]) for x in t["rows"]] == [
        ("9001", "50.00", "35.70", "14.30"),
        ("9003", "119.00", "50.00", "69.00"),
        ("9004", "0.00", "20.00", "-20.00"),
        ("9007", "0.00", "100.00", "-100.00"),
        ("9013", "0.00", "19.00", "-19.00"),
        ("9031", "5.70", "0.00", "5.70"),
        ("9035", "30.00", "0.00", "30.00"),
        ("9039", "20.00", "0.00", "20.00"),
    ]
    # debit 50 + 119 + 5.70 + 30 + 20 = 224.70 = credit 35.70 + 50 + 20 + 100 + 19
    assert (t["debit_total"], t["credit_total"], t["balance_total"]) == ("224.70", "224.70", "0.00")
    assert t["note"]


def test_trial_balance_period(scenario):
    t = get(scenario, "reports/trial-balance", **{"from": "2026-02-01", "to": "2026-02-28"}).json()
    # entries 3 and 4 only: debit 30 + 5.70 + 20 = 55.70, credit 35.70 + 20
    assert (t["debit_total"], t["credit_total"], t["balance_total"]) == ("55.70", "55.70", "0.00")
    assert get(scenario, "reports/trial-balance", **{"from": "2026-03-01", "to": "2026-02-01"}).status_code == 422


def test_account_posted_without_a_role_is_in_the_reports(app, scenario):
    source = JournalSource(event="manual", created_by="u1x000", origin="manual")
    lines = [PostLine(account="9900", amount_cents=100), PostLine(account="9001", amount_cents=-100)]
    app.state.services.journal.post("m1", date(2026, 3, 1), "Manual", source, lines)
    t = get(scenario, "reports/trial-balance").json()
    assert ("9900", "1.00", "0.00") in [(x["account"], x["debit"], x["credit"]) for x in t["rows"]]
    assert t["balance_total"] == "0.00"
    ledger = get(scenario, "reports/ledger", account="9001").json()
    assert ledger["rows"][-1]["debit"] == "0.00" and ledger["rows"][-1]["credit"] == "1.00"


def test_pnl_equals_the_balance_sheet_result(scenario):
    p = get(scenario, "reports/pnl", year=2026).json()
    # revenue 100.00 - software 30.00 - other 20.00 (VAT is not in the P&L)
    assert [(x["account"], x["amount"]) for x in p["income"]] == [("9007", "100.00")]
    assert [(x["account"], x["amount"]) for x in p["expenses"]] == [("9035", "-30.00"), ("9039", "-20.00")]
    assert (p["total_income"], p["total_expense"], p["profit"]) == ("100.00", "50.00", "50.00")
    b = get(scenario, "reports/balance-sheet", year=2026).json()
    assert b["as_of"] == "2026-12-31"
    # debit: bank 14.30 + receivables 69.00 + input VAT 5.70 = 89.00; credit: payables 20 + VAT out 19 = 39.00
    assert [(x["account"], x["balance"]) for x in b["debit_side"]] == [
        ("9001", "14.30"), ("9003", "69.00"), ("9031", "5.70")
    ]  # fmt: skip
    assert [(x["account"], x["balance"]) for x in b["credit_side"]] == [("9004", "-20.00"), ("9013", "-19.00")]
    assert (b["debit_total"], b["credit_total"], b["result"], b["difference"]) == ("89.00", "39.00", "50.00", "0.00")
    assert b["result"] == p["profit"]  # 89 - 39 = 50
    assert (b["earlier_years_result"], b["result"]) == ("0.00", "50.00")


def test_balance_sheet_as_of(scenario):
    b = get(scenario, "reports/balance-sheet", to="2026-01-31").json()
    # after entries 1 and 2: bank 50.00 + receivables 69.00 = 119.00 debit; VAT out 19.00 credit; result 100.00
    assert (b["debit_total"], b["credit_total"], b["result"], b["difference"]) == ("119.00", "19.00", "100.00", "0.00")
    assert get(scenario, "reports/balance-sheet", to="not-a-date").status_code == 422


def test_open_items(scenario):
    o = get(scenario, "reports/open-items", year=2026).json()
    assert o["as_of"] == "2026-12-31"
    # inv1: 119.00 - 50.00 = 69.00 since 2026-01-10 (355 days to 31 Dec); exp1: 20.00 since 2026-02-10 (324 days)
    got = [
        (i["open_item_id"], i["kind"], i["account"], i["open_amount"], i["since"], i["age_days"]) for i in o["items"]
    ]
    assert got == [
        ("inv1", "receivable", "9003", "69.00", "2026-01-10", 355),
        ("exp1", "payable", "9004", "20.00", "2026-02-10", 324),
    ]
    assert (o["items"][0]["source_coll"], o["items"][0]["source_number"]) == ("invoices", "R-1")
    assert (o["receivables_total"], o["payables_total"]) == ("69.00", "20.00")
    early = get(scenario, "reports/open-items", to="2026-01-31").json()
    assert [(i["open_item_id"], i["open_amount"], i["age_days"]) for i in early["items"]] == [("inv1", "69.00", 21)]


def test_a_settled_item_drops_out_of_the_open_items(app, scenario):
    lines = [
        PostLine(role="bank", amount_cents=6900),
        PostLine(role="receivables", amount_cents=-6900, open_item_id="inv1"),
    ]
    _post(app, "e5", date(2026, 3, 1), "Rest", "payment", "invoices", "inv1", "R-1", lines)
    o = get(scenario, "reports/open-items").json()
    assert [i["open_item_id"] for i in o["items"]] == ["exp1"] and o["receivables_total"] == "0.00"


def test_ledger(scenario):
    r = get(scenario, "reports/ledger", year=2026, role="bank")
    assert r.status_code == 200, r.text
    led = r.json()
    assert (led["account"], led["name"], led["opening"], led["closing"]) == ("9001", "Synthetic bank", "0.00", "14.30")
    # entry 2: +50.00 -> 50.00; entry 3: -35.70 -> 14.30
    assert [(x["entry_no"], x["debit"], x["credit"], x["balance"]) for x in led["rows"]] == [
        (2, "50.00", "0.00", "50.00"),
        (3, "0.00", "35.70", "14.30"),
    ]
    assert get(scenario, "reports/ledger", account="9001").json() == led
    part = get(scenario, "reports/ledger", account="9001", **{"from": "2026-02-01"}).json()
    assert part["opening"] == "50.00" and [x["entry_no"] for x in part["rows"]] == [3] and part["closing"] == "14.30"


def test_ledger_needs_an_account_or_a_role(scenario):
    assert get(scenario, "reports/ledger").status_code == 422
    assert get(scenario, "reports/ledger", role="no_such_role").status_code == 422
    assert get(scenario, "reports/ledger", account="1").status_code == 422
    assert get(scenario, "reports/ledger", role="bank", account="9003").status_code == 422


# ---------------------------------------------------------------- bases, income year, 14.5
def test_eur_filters_by_income_year(eur_scenario):
    e25 = get(eur_scenario, "reports/eur", year=2025).json()
    # revenue 100.00, software -30.00; the recurring 20.00 (booked 2025-12-30, income_date 2026-01-05) is not in 2025
    assert [(x["line"], x["amount"]) for x in e25["lines"]] == [
        ("Line revenue_s19", "100.00"),
        ("Line expense_software", "-30.00"),
    ]
    assert (e25["vat_received"], e25["input_vat_paid"], e25["vat_paid"]) == ("19.00", "5.70", "0.00")
    # income 100 + 19 = 119.00; expense 30 + 5.70 = 35.70; profit 83.30
    assert (e25["total_income"], e25["total_expense"], e25["profit"]) == ("119.00", "35.70", "83.30")
    e26 = get(eur_scenario, "reports/eur", year=2026).json()
    # the 20.00 booked in 2025 belongs to 2026 (first, entry 2025/3), then software 10.00 of 2026/1
    assert [(x["line"], x["amount"]) for x in e26["lines"]] == [
        ("Line expense_other", "-20.00"),
        ("Line expense_software", "-10.00"),
    ]
    assert (e26["total_income"], e26["total_expense"], e26["profit"]) == ("0.00", "30.00", "-30.00")


def test_eur_report_reads_the_income_date_range_only(app, eur_scenario, monkeypatch):
    store = app.state.services.accounting_reports.store
    calls = []
    real = store.journal_lines_by_income_date
    monkeypatch.setattr(store, "journal_lines_by_income_date", lambda a, b: calls.append((a, b)) or real(a, b))

    def no_journal(*_a, **_k):
        raise AssertionError("the EÜR must not read the whole journal")

    monkeypatch.setattr(store, "list_journal_entries", no_journal)
    assert get(eur_scenario, "reports/eur", year=2025).status_code == 200
    assert calls == [("2025-01-01", "2025-12-31")]


def test_per_basis_422(scenario):
    assert get(scenario, "reports/eur", year=2026).status_code == 422  # 2026 is an accrual year
    r = scenario.post(f"{BASE}/years", json={"year": 2025, "chart": "skr03", "income_basis": "eur"})
    assert r.status_code == 201, r.text
    for path in ("reports/pnl", "reports/balance-sheet"):
        assert get(scenario, path, year=2025).status_code == 422, path
    assert get(scenario, "reports/eur", year=2025).status_code == 200
    for path in ("reports/trial-balance", "reports/open-items"):  # not tied to a basis
        assert get(scenario, path, year=2025).status_code == 200, path


def _year(client, year, basis):
    r = client.post(f"{BASE}/years", json={"year": year, "chart": "skr03", "income_basis": basis})
    assert r.status_code == 201, r.text


def test_pnl_filters_by_income_year(app, scenario):
    _year(scenario, 2027, "accrual")
    # a cost booked on 30 Dec 2026 whose income_date lies in 2027: not in the P&L of 2026, in the one of 2027
    lines = [
        PostLine(role="expense_other", amount_cents=1000, income_date=date(2027, 1, 2)),
        PostLine(role="payables", amount_cents=-1000, open_item_id="exp2"),
    ]
    _post(app, "e9", date(2026, 12, 30), "Late", "expense", "expenses", "exp2", None, lines)
    p26 = get(scenario, "reports/pnl", year=2026).json()
    assert [(x["account"], x["amount"]) for x in p26["expenses"]] == [("9035", "-30.00"), ("9039", "-20.00")]
    assert p26["profit"] == "50.00"
    p27 = get(scenario, "reports/pnl", year=2027).json()
    assert [(x["account"], x["amount"]) for x in p27["expenses"]] == [("9039", "-10.00")]
    assert (p27["income"], p27["profit"]) == ([], "-10.00")
    # the balance sheet of 2026 cumulates by booking date: result 50.00 - 10.00, and it still balances
    b = get(scenario, "reports/balance-sheet", year=2026).json()
    assert (b["result"], b["earlier_years_result"], b["difference"]) == ("40.00", "0.00", "0.00")


def test_balance_sheet_shows_the_results_of_earlier_years(app, scenario):
    _year(scenario, 2025, "accrual")
    lines = [
        PostLine(role="expense_other", amount_cents=5000, income_date=date(2025, 12, 15)),
        PostLine(role="bank", amount_cents=-5000),
    ]
    _post(app, "p1", date(2025, 12, 15), "Prior year cost", "expense", "expenses", "exp5", None, lines)
    b = get(scenario, "reports/balance-sheet", year=2026).json()
    # bank 14.30 - 50.00 = -35.70 moves to the credit side; debit: receivables 69.00 + input VAT 5.70 = 74.70;
    # credit: bank 35.70 + payables 20.00 + VAT out 19.00 = 74.70; result of 2026 50.00, earlier years -50.00
    assert [(x["account"], x["balance"]) for x in b["credit_side"]] == [
        ("9001", "-35.70"), ("9004", "-20.00"), ("9013", "-19.00")
    ]  # fmt: skip
    assert (b["debit_total"], b["credit_total"]) == ("74.70", "74.70")
    assert (b["result"], b["earlier_years_result"], b["difference"]) == ("50.00", "-50.00", "0.00")
    # the balance sheet of 2025 itself: only the cost of 2025, nothing earlier
    b25 = get(scenario, "reports/balance-sheet", year=2025).json()
    assert (b25["result"], b25["earlier_years_result"], b25["difference"]) == ("-50.00", "0.00", "0.00")
    # as of a day in 2025 the 2026 sheet has no result of its own yet
    early = get(scenario, "reports/balance-sheet", year=2026, to="2025-12-31").json()
    assert (early["result"], early["earlier_years_result"]) == ("0.00", "-50.00")


def _rewrite_chart(edit):
    path = charts._DIR / "skr03-2026.json"
    data = json.loads(path.read_text("utf-8"))
    edit(data)
    path.write_text(json.dumps(data), "utf-8")
    charts._load.cache_clear()


def _post_stale(app, role_a, account_a):
    """Store entry 5 of 2026 whose line carries a role the chart of the year does not have (written by another
    edition of the app), next to a normal bank line."""
    store = app.state.services.accounting_reports.store
    entry = dict(store.list_journal_entries(2026, None, None, 1, 0)[0])
    entry.pop("lines")
    entry.update(id=None, entry_no=5, event_key="stale1", booking_date="2026-03-01", hash="b" * 64, prev_hash="a" * 64)
    line = {"line_no": 1, "role": role_a, "account": account_a, "amount_cents": 700}
    bank = {"line_no": 2, "role": "bank", "account": "9001", "amount_cents": -700}
    store.insert_journal_entry(entry, [line, bank])


def test_a_line_with_a_stale_role_is_shown_by_account(app, scenario):
    _post_stale(app, "retired_role", "9900")  # account 9900 is in the chart, without a role
    r = get(scenario, "reports/trial-balance", year=2026)
    assert r.status_code == 200, r.text
    rows = {x["account"]: x for x in r.json()["rows"]}
    assert (rows["9900"]["debit"], rows["9900"]["credit"]) == ("7.00", "0.00")
    assert r.json()["balance_total"] == "0.00"
    for path in ("reports/pnl", "reports/balance-sheet", "reports/open-items"):
        assert get(scenario, path, year=2026).status_code == 200, path
    led = get(scenario, "reports/ledger", year=2026, account="9900")
    assert led.status_code == 200 and [x["debit"] for x in led.json()["rows"]] == ["7.00"]


def test_a_stale_role_on_an_account_the_chart_does_not_know(app, scenario):
    _post_stale(app, "retired_role", "9777")
    r = get(scenario, "reports/trial-balance", year=2026)
    assert r.status_code == 200, r.text
    assert ("9777", "7.00") in [(x["account"], x["debit"]) for x in r.json()["rows"]]
    b = get(scenario, "reports/balance-sheet", year=2026)
    assert b.status_code == 200 and b.json()["difference"] == "0.00"
    assert ("9777", "7.00") in [(x["account"], x["balance"]) for x in b.json()["debit_side"]]


def test_missing_chart_is_409(scenario):
    (charts._DIR / "skr03-2026.json").unlink()
    charts._load.cache_clear()
    for path in (
        "accounts", "reports/trial-balance", "reports/pnl", "reports/balance-sheet",
        "reports/open-items",
    ):  # fmt: skip
        assert get(scenario, path, year=2026).status_code == 409
    assert get(scenario, "reports/ledger", year=2026, account="9001").status_code == 409, path


def test_unparseable_dates_are_422(scenario):
    for path in ("reports/trial-balance", "reports/balance-sheet", "reports/open-items", "reports/ledger?role=bank"):
        assert get(scenario, path, to="31.12.2026").status_code == 422, path
    assert get(scenario, "reports/trial-balance", **{"from": "x"}).status_code == 422


def test_reports_read_only_the_rows_they_need(app, scenario, monkeypatch):
    store = app.state.services.accounting_reports.store
    seen = []
    real = store.list_journal_entries

    def spy(year=None, date_from=None, date_to=None, limit=100, offset=0):
        seen.append((date_from, date_to))
        return real(year, date_from, date_to, limit, offset)

    monkeypatch.setattr(store, "list_journal_entries", spy)
    assert get(scenario, "reports/balance-sheet", to="2026-01-31").status_code == 200
    assert get(scenario, "reports/open-items", to="2026-01-31").status_code == 200
    assert seen == [(None, "2026-01-31")] * 2
    seen.clear()
    assert get(scenario, "reports/trial-balance", **{"from": "2026-02-01", "to": "2026-02-28"}).status_code == 200
    r = get(scenario, "reports/ledger", account="9001", **{"from": "2026-02-01"})
    assert r.status_code == 200 and r.json()["opening"] == "50.00"  # the opening comes from a sum, not from rows
    assert seen == [("2026-02-01", "2026-02-28"), ("2026-02-01", "2026-12-31")]


# ---------------------------------------------------------------- rights
REPORT_PATHS = [
    "journal", "accounts", "reports/trial-balance", "reports/pnl", "reports/balance-sheet",
    "reports/open-items", "reports/ledger?role=bank",
]  # fmt: skip


def test_rights_per_role(scenario, clients):
    for path in REPORT_PATHS:
        for role, c in clients.items():
            r = c.get(f"{BASE}/{path}")
            assert (r.status_code == 403) == (role == "employee"), (path, role, r.status_code)
            if role != "employee":
                assert r.status_code == 200, (path, role, r.text)


def test_read_only_token_reads_the_reports(app, scenario):
    t = scenario.post("/api/tokens", json={"label": "t", "read_only": True})
    assert t.status_code in (200, 201), t.text
    secret = t.json()["token"]
    with TestClient(app) as anon:
        for path in REPORT_PATHS:
            r = anon.get(f"{BASE}/{path}", headers={"Authorization": f"Bearer {secret}"})
            assert r.status_code == 200, (path, r.text)


def test_eur_rights_per_role(eur_scenario, clients):
    for role, c in clients.items():
        r = c.get(f"{BASE}/reports/eur", params={"year": 2025})
        assert r.status_code == (403 if role == "employee" else 200), (role, r.status_code)
