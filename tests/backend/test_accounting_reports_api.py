"""Journal, accounts and report routes (docs/specs/accounting.md 14, 18) on a small posted scenario.

The synthetic chart (fake account numbers) is copied in as ``skr03-2026``. Entries are posted through
``JournalService``; all expected figures are hand-computed in the comments (cents in the journal, euros in the API)."""

import shutil
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from permita38.core import charts
from permita38.core.service.journal import JournalSource, PostLine

from .test_rbac import PASSWORD, _login

FIXTURE = Path(__file__).parent / "fixtures" / "chart_synthetic.json"
BASE = "/api/accounting"
# synthetic accounts: bank 9001, receivables 9003, payables 9004, revenue_s19 9007, vat_out_s19 9013,
# input_vat_clearing 9031, expense_software 9035, expense_other 9039


@pytest.fixture
def chart_dir(tmp_path, monkeypatch):
    d = tmp_path / "charts"
    d.mkdir()
    shutil.copy(FIXTURE, d / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", d)
    return d


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


@pytest.fixture
def scenario(app, boss_client, chart_dir):
    """Four entries in 2026 (entry numbers 1..4):

    1  2026-01-10 invoice inv1: receivables +119.00, revenue -100.00, VAT -19.00 (tax key S19)
    2  2026-01-20 payment of 50.00 on inv1: bank +50.00, receivables -50.00
    3  2026-02-05 software expense: expense +30.00, input VAT +5.70, bank -35.70
    4  2026-02-10 other expense on credit exp1: expense +20.00, payables -20.00
    """
    r = boss_client.post(f"{BASE}/years", json={"year": 2026, "chart": "skr03", "income_basis": "accrual"})
    assert r.status_code == 201, r.text
    d = date
    vat = PostLine(
        role="vat_out_s19", amount_cents=-1900, tax_key="S19", tax_kind="tax", tax_rate="19", tax_base_cents=10000,
        vat_date=d(2026, 1, 10), income_date=d(2026, 1, 10),
    )  # fmt: skip
    _post(
        app, "e1", d(2026, 1, 10), "Invoice 1", "issue", "invoices", "inv1", "R-1",
        [
            PostLine(role="receivables", amount_cents=11900, open_item_id="inv1"),
            PostLine(role="revenue_s19", amount_cents=-10000, income_date=d(2026, 1, 10)),
            vat,
        ],
    )  # fmt: skip
    _post(
        app, "e2", d(2026, 1, 20), "Payment 1", "payment", "invoices", "inv1", "R-1",
        [
            PostLine(role="bank", amount_cents=5000),
            PostLine(role="receivables", amount_cents=-5000, open_item_id="inv1"),
        ],
    )  # fmt: skip
    _post(
        app, "e3", d(2026, 2, 5), "Software", "expense", "expenses", "exp0", None,
        [
            PostLine(role="expense_software", amount_cents=3000, income_date=d(2026, 2, 5)),
            PostLine(role="input_vat_clearing", amount_cents=570, income_date=d(2026, 2, 5)),
            PostLine(role="bank", amount_cents=-3570),
        ],
    )  # fmt: skip
    _post(
        app, "e4", d(2026, 2, 10), "Other", "expense", "expenses", "exp1", None,
        [
            PostLine(role="expense_other", amount_cents=2000, income_date=d(2026, 2, 10)),
            PostLine(role="payables", amount_cents=-2000, open_item_id="exp1"),
        ],
    )  # fmt: skip
    return boss_client


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


def test_eur(scenario):
    e = get(scenario, "reports/eur", year=2026).json()
    assert e["year"] == 2026
    # revenue 100.00; expenses software 30.00, other 20.00 (income-signed: negative)
    assert [(x["line"], x["amount"]) for x in e["lines"]] == [
        ("Line revenue_s19", "100.00"),
        ("Line expense_software", "-30.00"),
        ("Line expense_other", "-20.00"),
    ]
    assert (e["vat_received"], e["vat_refunded"], e["input_vat_paid"], e["vat_paid"]) == (
        "19.00", "0.00", "5.70", "0.00"
    )  # fmt: skip
    # income 100 + 19 = 119.00; expense 30 + 20 + 5.70 = 55.70; profit 63.30
    assert (e["total_income"], e["total_expense"], e["profit"]) == ("119.00", "55.70", "63.30")
    assert e["non_deductible"] == []


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
    # EÜR (gross) 63.30 differs from the P&L 50.00 by VAT received 19.00 - input VAT 5.70 = 13.30
    assert get(scenario, "reports/eur", year=2026).json()["profit"] == "63.30"


def test_balance_sheet_as_of(scenario):
    b = get(scenario, "reports/balance-sheet", to="2026-01-31").json()
    # after entries 1 and 2: bank 50.00 + receivables 69.00 = 119.00 debit; VAT out 19.00 credit; result 100.00
    assert (b["debit_total"], b["credit_total"], b["result"], b["difference"]) == ("119.00", "19.00", "100.00", "0.00")


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


# ---------------------------------------------------------------- rights
REPORT_PATHS = [
    "journal", "accounts", "reports/trial-balance", "reports/eur", "reports/pnl", "reports/balance-sheet",
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
