"""GET /api/accounting/datev (docs/specs/datev-export.md "API and UI"): a small posted scenario exported as a zip.

Account numbers of ``fixtures/chart_synthetic.json``: bank 9001, receivables 9003, payables 9004, revenue_s19 9007,
meals_deductible 9036, meals_nondeductible 9037.
"""

import io
import shutil
import zipfile
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from permita38.core import charts
from permita38.core.service.journal import JournalService, JournalSource, PostLine
from permita38.core.service.locks import LockService

from .test_rbac import PASSWORD, _login

FIXTURE = Path(__file__).parent / "fixtures" / "chart_synthetic.json"
URL = "/api/accounting/datev"
RANGE = {"from": "2026-01-01", "to": "2026-03-31"}
CSV_Q1 = "EXTF_Buchungsstapel_20260101_20260331.csv"
SRC = JournalSource(event="test", created_by="u1x000")


@pytest.fixture(autouse=True)
def synthetic_chart(tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURE, charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)


@pytest.fixture
def store(app):
    return app.state.services.store


def set_numbers(store, **extra):
    store.save_settings({**store.settings(), "datev_consultant_no": 1001, "datev_client_no": 12345, **extra})


def post_scenario(store):
    """Invoice issue (accrual/Soll, 1.000,00 net + 19 %), a partial payment of 500,00, a meal (70 % deductible)."""
    store.save(
        "fiscal_years",
        {"year": 2026, "chart": "skr03", "income_basis": "accrual", "vat_basis": "soll", "vat_frequency": "month",
         "dauerfrist": False, "small_business": False, "rules_version": "r1", "opened_at": 1, "opened_by": "u1x000"},
    )  # fmt: skip
    journal = JournalService(store)
    journal.post(
        "issue:1", date(2026, 1, 14), "Invoice 1", SRC,
        [
            PostLine(role="receivables", amount_cents=119000),
            PostLine(role="revenue_s19", amount_cents=-100000, income_date=date(2026, 1, 14)),
            PostLine(role="vat_out_s19", amount_cents=-19000, tax_key="S19", tax_kind="tax", tax_rate="19",
                     tax_base_cents=100000, vat_date=date(2026, 1, 14)),
        ],
    )  # fmt: skip
    journal.post(
        "pay:1", date(2026, 2, 3), "Payment 1", SRC,
        [PostLine(role="bank", amount_cents=50000), PostLine(role="receivables", amount_cents=-50000)],
    )  # fmt: skip
    journal.post(
        "exp:1", date(2026, 2, 10), "Meal", SRC,
        [
            PostLine(role="meals_deductible", amount_cents=7000, income_date=date(2026, 2, 10)),
            PostLine(role="meals_nondeductible", amount_cents=3000, income_date=date(2026, 2, 10)),
            PostLine(role="input_vat_s7", amount_cents=700, tax_key="VST", tax_kind="input", tax_rate="7",
                     vat_date=date(2026, 2, 10)),
            PostLine(role="payables", amount_cents=-10700),
        ],
    )  # fmt: skip


def lock_up_to(store, month: int):
    LockService(store, today=lambda: date(2027, 1, 1)).lock_up_to(2026, month, "manual", "u1x000")


def unzip(resp) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def csv_lines(data: bytes) -> list[list[str]]:
    return [line.split(";") for line in data.decode("cp1252").split("\r\n") if line]


def festschreibung_column(lines: list[list[str]]) -> int:
    return lines[1].index("Festschreibung")


def test_export_of_the_scenario(boss_client, store):
    set_numbers(store, datev_label="Q1")
    post_scenario(store)
    r = boss_client.get(URL, params=RANGE)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == 'attachment; filename="datev-export_2026-01-01_2026-03-31.zip"'
    files = unzip(r)
    assert sorted(files) == [CSV_Q1, "export-report.txt"]
    lines = csv_lines(files[CSV_Q1])
    header = lines[0]
    assert header[:5] == ['"EXTF"', "700", "21", '"Buchungsstapel"', "13"]
    assert header[10:16] == ["1001", "12345", "20260101", "4", "20260101", "20260331"]
    assert header[16] == '"Q1"' and header[20] == "0"  # nothing locked: Festschreibung 0
    rows = lines[2:]
    assert len(rows) == 4  # issue, payment, meal folded, meal reclassification
    assert [r[0] for r in rows] == ["1190,00", "500,00", "107,00", "30,00"]
    assert [r[6] for r in rows] == ["9003", "9001", "9036", "9037"]
    assert [r[7] for r in rows] == ["9007", "9003", "9004", "9036"]
    assert {r[festschreibung_column(lines)] for r in rows} == {"0"}
    report = files["export-report.txt"].decode("utf-8")
    assert "Entries: 3" in report and "Rows: 4" in report and "Result: OK" in report
    account_lines = [ln.split(";") for ln in report.splitlines() if ln[:1].isdigit() and ln.count(";") == 3]
    assert len(account_lines) >= 6
    assert all(parts[3] == "0,00" for parts in account_lines)  # no difference per account


def test_range_limits_the_entries(boss_client, store):
    set_numbers(store)
    post_scenario(store)
    r = boss_client.get(URL, params={"from": "2026-02-01", "to": "2026-02-28"})
    assert r.status_code == 200
    files = unzip(r)
    rows = csv_lines(files["EXTF_Buchungsstapel_20260201_20260228.csv"])[2:]
    assert [r[0] for r in rows] == ["500,00", "107,00", "30,00"]
    assert "Result: OK" in files["export-report.txt"].decode()


def test_all_months_locked_is_one_file_with_festschreibung(boss_client, store):
    set_numbers(store)
    post_scenario(store)
    lock_up_to(store, 3)
    files = unzip(boss_client.get(URL, params=RANGE))
    assert sorted(files) == [CSV_Q1, "export-report.txt"]
    lines = csv_lines(files[CSV_Q1])
    assert lines[0][20] == "1"
    assert {r[festschreibung_column(lines)] for r in lines[2:]} == {"1"}


def test_mixed_locked_and_unlocked_months_give_two_files(boss_client, store):
    set_numbers(store)
    post_scenario(store)
    lock_up_to(store, 1)
    files = unzip(boss_client.get(URL, params=RANGE))
    locked = "EXTF_Buchungsstapel_20260101_20260331_locked.csv"
    unlocked = "EXTF_Buchungsstapel_20260101_20260331_unlocked.csv"
    assert sorted(files) == [locked, unlocked, "export-report.txt"]
    lo, un = csv_lines(files[locked]), csv_lines(files[unlocked])
    assert lo[0][20] == "1" and un[0][20] == "0"
    assert [r[0] for r in lo[2:]] == ["1190,00"]
    assert {r[festschreibung_column(lo)] for r in lo[2:]} == {"1"}
    assert [r[0] for r in un[2:]] == ["500,00", "107,00", "30,00"]
    assert {r[festschreibung_column(un)] for r in un[2:]} == {"0"}
    report = files["export-report.txt"].decode()
    assert "Rows without Festschreibung (unlocked months): 3" in report
    assert "one file each" in report and unlocked in report and locked in report
    assert "Rows: 4" in report and "Result: OK" in report


def test_empty_range_is_one_empty_file(boss_client, store):
    set_numbers(store)
    post_scenario(store)
    files = unzip(boss_client.get(URL, params={"from": "2026-06-01", "to": "2026-06-30"}))
    lines = csv_lines(files["EXTF_Buchungsstapel_20260601_20260630.csv"])
    assert len(lines) == 2  # header and column header only


def test_range_across_years_is_422(boss_client, store):
    set_numbers(store)
    post_scenario(store)
    r = boss_client.get(URL, params={"from": "2025-12-01", "to": "2026-01-31"})
    assert r.status_code == 422 and "one fiscal year" in r.json()["detail"]


def test_from_after_to_and_bad_dates_are_422(boss_client, store):
    set_numbers(store)
    post_scenario(store)
    assert boss_client.get(URL, params={"from": "2026-03-01", "to": "2026-02-01"}).status_code == 422
    assert boss_client.get(URL).status_code == 422
    assert boss_client.get(URL, params={"from": "2026-13-01", "to": "2026-12-31"}).status_code == 422


def test_unknown_fiscal_year_is_404(boss_client, store):
    set_numbers(store)
    post_scenario(store)
    assert boss_client.get(URL, params={"from": "2030-01-01", "to": "2030-01-31"}).status_code == 404


def test_missing_numbers_are_422_and_listed(boss_client, store):
    post_scenario(store)
    r = boss_client.get(URL, params=RANGE)
    assert r.status_code == 422
    assert "datev_consultant_no" in r.json()["detail"] and "datev_client_no" in r.json()["detail"]
    store.save_settings({**store.settings(), "datev_consultant_no": 1001})
    r = boss_client.get(URL, params=RANGE)
    assert r.status_code == 422
    assert "datev_client_no" in r.json()["detail"] and "datev_consultant_no" not in r.json()["detail"]


@pytest.mark.parametrize(
    "key, value",
    [
        ("datev_consultant_no", 1000),
        ("datev_client_no", 100000),
        ("datev_account_length", 9),
        ("datev_label", "x" * 31),
    ],
)
def test_invalid_settings_are_422(boss_client, store, key, value):
    post_scenario(store)
    set_numbers(store, **{key: value})
    r = boss_client.get(URL, params=RANGE)
    assert r.status_code == 422 and key in r.json()["detail"]


def test_account_length_setting_goes_into_the_header(boss_client, store):
    set_numbers(store, datev_account_length=6)
    post_scenario(store)
    lines = csv_lines(unzip(boss_client.get(URL, params=RANGE))[CSV_Q1])
    assert lines[0][13] == "6"


def test_rights_per_role_and_token(app, boss_client, store):
    set_numbers(store)
    post_scenario(store)
    clients = {"boss": boss_client}
    for role in ("accountant", "employee"):
        r = boss_client.post(
            "/api/users", json={"username": role[:3] + "x", "name": role.title(), "role": role, "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        clients[role] = _login(app, role[:3] + "x")
    assert clients["boss"].get(URL, params=RANGE).status_code == 200
    assert clients["accountant"].get(URL, params=RANGE).status_code == 200
    assert clients["employee"].get(URL, params=RANGE).status_code == 403
    tok = boss_client.post("/api/tokens", json={"label": "t", "read_only": True})
    assert tok.status_code == 201
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {tok.json()['token']}"
    assert api.get(URL, params=RANGE).status_code == 200
    assert TestClient(app).get(URL, params=RANGE).status_code == 401


# ---------- rates (spec "Rates"), report check, paging, locks, settings


def _fiscal_year(store):
    if any(r.get("year") == 2026 for r in store.list("fiscal_years")):
        return
    store.save(
        "fiscal_years",
        {"year": 2026, "chart": "skr03", "income_basis": "accrual", "vat_basis": "soll", "vat_frequency": "month",
         "dauerfrist": False, "small_business": False, "rules_version": "r1", "opened_at": 1, "opened_by": "u1x000"},
    )  # fmt: skip


def _post_13b(store, day=date(2026, 6, 2), key="rc:1", rate="19"):
    """Spec D2: § 13b EU service, 200,50 net, 19 % = 38,10 out and in."""
    _fiscal_year(store)
    JournalService(store).post(
        key, day, "13b", SRC,
        [
            PostLine(role="rc_vat_out_eu", amount_cents=-3810, tax_key="RC_EU_SVC", tax_kind="tax", tax_rate=rate,
                     tax_base_cents=20050, vat_date=date(2026, 5, 20)),
            PostLine(role="rc_input_vat", amount_cents=3810, tax_key="RC_EU_SVC", tax_kind="input", tax_rate=rate,
                     vat_date=date(2026, 5, 20)),
            PostLine(role="expense_software", amount_cents=20050, income_date=day),
            PostLine(role="payables", amount_cents=-20050),
        ],
    )  # fmt: skip


def _post_s7(store, key="s7:1", rate="7", day=date(2026, 3, 5)):
    """Net 100,00 at 7 %: the line carries ``rate`` (``None``: the box rate of the rules applies)."""
    _fiscal_year(store)
    JournalService(store).post(
        key, day, "S7", SRC,
        [
            PostLine(role="receivables", amount_cents=10700),
            PostLine(role="revenue_s7", amount_cents=-10000, income_date=day),
            PostLine(role="vat_out_s7", amount_cents=-700, tax_key="S7", tax_kind="tax", tax_rate=rate,
                     tax_base_cents=10000, vat_date=day),
        ],
    )  # fmt: skip


def _report(resp) -> str:
    return unzip(resp)["export-report.txt"].decode("utf-8")


def test_13b_expense_with_the_spec_d2_numbers(boss_client, store):
    set_numbers(store)
    _post_13b(store)
    r = boss_client.get(URL, params={"from": "2026-06-01", "to": "2026-06-30"})
    assert r.status_code == 200, r.text
    files = unzip(r)
    rows = csv_lines(files["EXTF_Buchungsstapel_20260601_20260630.csv"])[2:]
    assert [(x[0], x[6], x[7]) for x in rows] == [("200,50", "9035", "9004"), ("38,10", "9027", "9025")]
    report = files["export-report.txt"].decode("utf-8")
    assert "Result: OK" in report and "DIFFERENCES" not in report


def test_13b_line_without_a_rate_is_422_listing_the_entry(boss_client, store):
    set_numbers(store)
    _post_13b(store, key="rc:norate", rate=None)
    r = boss_client.get(URL, params={"from": "2026-06-01", "to": "2026-06-30"})
    assert r.status_code == 422 and "rc:norate" in r.json()["detail"]


def test_s7_revenue_with_and_without_a_line_rate_has_no_differences(boss_client, store):
    set_numbers(store)
    _post_s7(store, key="s7:rated", rate="7")
    _post_s7(store, key="s7:box", rate=None, day=date(2026, 3, 6))  # the box rate of Kz 86 (7) applies
    r = boss_client.get(URL, params=RANGE)
    assert r.status_code == 200, r.text
    rows = csv_lines(unzip(r)[CSV_Q1])[2:]
    assert [x[0] for x in rows] == ["107,00", "107,00"]
    assert "Result: OK" in _report(r)


def test_a_forced_difference_is_reported(boss_client, store, monkeypatch):
    from permita38.core.service import datev_export

    set_numbers(store)
    post_scenario(store)
    original = datev_export.self_check_report

    def wrong_box_rate(entries, rows, chart, rules, rates, **kwargs):
        return original(entries, rows, chart, rules, {**rates, "S19": Decimal(7)}, **kwargs)

    monkeypatch.setattr(datev_export, "self_check_report", wrong_box_rate)
    r = boss_client.get(URL, params=RANGE)
    assert r.status_code == 200, r.text
    report = _report(r)
    assert "Result: DIFFERENCES" in report and "Result: OK" not in report


def test_booking_on_the_last_day_of_the_range_is_included(boss_client, store):
    set_numbers(store)
    _post_s7(store, key="s7:last", day=date(2026, 3, 31))
    rows = csv_lines(unzip(boss_client.get(URL, params=RANGE))[CSV_Q1])[2:]
    assert [x[0] for x in rows] == ["107,00"]


def test_more_entries_than_one_page_are_all_exported(boss_client, store, monkeypatch):
    from permita38.core.service import datev_export

    set_numbers(store)
    for i in range(5):
        _post_s7(store, key=f"s7:{i}", day=date(2026, 3, 1 + i))
    monkeypatch.setattr(datev_export, "PAGE", 2)
    r = boss_client.get(URL, params=RANGE)
    rows = csv_lines(unzip(r)[CSV_Q1])[2:]
    assert len(rows) == 5 and "Entries: 5" in _report(r)


def test_empty_range_with_all_months_locked_has_festschreibung_1(boss_client, store):
    set_numbers(store)
    post_scenario(store)
    lock_up_to(store, 6)
    june = unzip(boss_client.get(URL, params={"from": "2026-06-01", "to": "2026-06-30"}))
    lines = csv_lines(june["EXTF_Buchungsstapel_20260601_20260630.csv"])
    assert len(lines) == 2 and lines[0][20] == "1"
    july = unzip(boss_client.get(URL, params={"from": "2026-07-01", "to": "2026-07-31"}))
    assert csv_lines(july["EXTF_Buchungsstapel_20260701_20260731.csv"])[0][20] == "0"  # July is not locked


def test_role_fallback_with_two_roles_on_one_account_is_422(boss_client, store, monkeypatch):
    from permita38.core.service import datev_export

    set_numbers(store)
    post_scenario(store)
    chart = charts.chart_for("skr03", 2026)
    shared = next(acc for role, acc in chart.roles if role == "revenue_s19")
    roles = tuple((role, shared if role == "revenue_s7" else acc) for role, acc in chart.roles)
    monkeypatch.setattr(datev_export, "chart_for", lambda *_: replace(chart, roles=roles))
    original = store.list_journal_entries

    def without_roles(*args, **kwargs):
        rows = original(*args, **kwargs)
        for row in rows:
            for ln in row["lines"]:
                ln["role"] = None
        return rows

    monkeypatch.setattr(store, "list_journal_entries", without_roles)
    r = boss_client.get(URL, params=RANGE)
    assert r.status_code == 422 and "shared by the roles" in r.json()["detail"]


@pytest.mark.parametrize(
    "field, good, bad",
    [
        ("datev_consultant_no", [1001, 9999999], [1000, 10000000, 1.5, True]),
        ("datev_client_no", [1, 99999], [0, 100000, 1.5]),
        ("datev_account_length", [4, 8], [3, 9, 4.5]),
        ("datev_label", ["", "x" * 30], ["x" * 31, 5]),
    ],
)
def test_put_settings_datev_fields_are_bounded(boss_client, field, good, bad):
    for value in good:
        r = boss_client.put("/api/settings", json={field: value})
        assert r.status_code == 200, (field, value, r.text)
        assert r.json()[field] == value
        assert boss_client.get("/api/settings").json()[field] == value
    for value in bad:
        r = boss_client.put("/api/settings", json={field: value})
        assert r.status_code == 422, (field, value, r.text)


def test_legacy_string_numbers_in_the_settings_do_not_break_the_export(boss_client, store):
    set_numbers(store, datev_consultant_no="1001", datev_client_no="12345")
    post_scenario(store)
    assert boss_client.get(URL, params=RANGE).status_code == 200
    set_numbers(store, datev_client_no="12a")
    r = boss_client.get(URL, params=RANGE)
    assert r.status_code == 422 and "datev_client_no" in r.json()["detail"]
    assert boss_client.get("/api/settings").status_code == 200
