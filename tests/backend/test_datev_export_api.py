"""GET /api/accounting/datev (docs/specs/datev-export.md "API and UI"): a small posted scenario exported as a zip.

Account numbers of ``fixtures/chart_synthetic.json``: bank 9001, receivables 9003, payables 9004, revenue_s19 9007,
meals_deductible 9036, meals_nondeductible 9037.
"""

import io
import shutil
import zipfile
from datetime import date
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
