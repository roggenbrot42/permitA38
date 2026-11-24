# ruff: noqa: F401, F811  (fixtures imported from test_vat_returns)
"""ZM records (docs/specs/zm.md "Records, status, drift", "API"): ZmReportService and its routes. The figures of the
oracle cases Z1-Z4 come from fixtures/accounting_oracle.json (`zm_cases`)."""

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from permita38.core import charts
from permita38.core.errors import ServiceError
from permita38.core.service.journal import JournalService, JournalSource, PostLine
from permita38.core.service.zm_reports import ZmReportService
from permita38.core.vat import zm_export

from .test_vat_returns import USER, clients, fiscal_year, journal, repo

ORACLE = json.loads((Path(__file__).parent / "fixtures" / "accounting_oracle.json").read_text(encoding="utf-8"))
Z = {c["id"]: c for c in ORACLE["zm_cases"]}
VAT_IDS = {"cust_A": "ATU00000001", "cust_B": "FR00000000001"}
TODAY = date(2026, 7, 15)  # Q1 and Q2 have ended, Q3 has not
BZST_HEAD = b"#v1.0\r\nLaenderkennzeichen,USt-IdNr.,Betrag(EUR),Art der Leistung\r\n"


@pytest.fixture
def svc(repo):
    return ZmReportService(repo, today=lambda: TODAY, clock=lambda: 1_800_000_000)


_n = {"i": 0}


def post_eu(journal: JournalService, vat_id: str | None, base: int, vat_day: date, booking: date | None = None) -> None:
    """One EU service invoice line: bank against revenue plus the statistical ``EU_SVC`` base line."""
    _n["i"] += 1
    key = f"eu:{_n['i']}"
    src = JournalSource(
        event="issue", created_by="u1x000", source_coll="invoices", source_id=f"inv{_n['i']:04d}",
        source_number=f"R-{_n['i']}", cp_vat_id=vat_id, cp_country=(vat_id or "")[:2] or None,
    )  # fmt: skip
    journal.post(
        key, booking or vat_day, key, src,
        [
            PostLine(role="bank", amount_cents=base),
            PostLine(role="revenue_eu_svc", amount_cents=-base),
            PostLine(role="revenue_eu_svc", amount_cents=0, tax_key="EU_SVC", tax_kind="base_only",
                     tax_base_cents=base, vat_date=vat_day),
        ],
    )  # fmt: skip


def post_case(journal: JournalService, case: dict[str, Any]) -> None:
    for ln in case["lines"]:
        post_eu(journal, VAT_IDS[ln["customer"]], ln["base"], date.fromisoformat(ln["vat_date"]))


def post_extra(journal: JournalService) -> None:
    ex = Z["Z2"]["extra_line"]
    post_eu(journal, VAT_IDS[ex["customer"]], ex["base"], date.fromisoformat(ex["vat_date"]))


def amounts(report: dict[str, Any]) -> dict[str, int]:
    return {x["vat_id"]: x["amount"] for x in report["lines"]}


def oracle_q(case_id: str, key: str) -> dict[str, int]:
    return {VAT_IDS[c]: v for c, v in Z[case_id]["zm"][key].items()}


def file_q1(svc, journal) -> str:
    post_case(journal, Z["Z1"])
    rid = svc.create_draft(2026, 1)["id"]
    svc.finalize(rid, USER)
    svc.filed(rid, "2026-04-20", "ref-1", None)
    return rid


# ------------------------------------------------------------------ oracle Z1-Z4
def test_z1_finalize_stores_the_oracle_lines_and_the_bzst_csv(svc, journal):
    post_case(journal, Z["Z1"])
    prev = svc.preview(2026, 1)
    assert amounts(prev) == oracle_q("Z1", "2026-Q1") and prev["required"] is True
    draft = svc.create_draft(2026, 1)
    assert draft["status"] == "draft" and amounts(draft) == amounts(prev)
    fin = svc.finalize(draft["id"], USER)
    assert fin["status"] == "final" and fin["finalised_at"] == 1_800_000_000 and fin["finalised_by"] == "u1x000"
    assert fin["rules_version"].endswith("/vat-2026")
    assert [(x["vat_id"], x["country"], x["amount"], x["kind"], x["cents"]) for x in fin["lines"]] == [
        ("ATU00000001", "AT", 3001, "S", 300110),
        ("FR00000000001", "FR", 4000, "S", 400000),
    ]
    stored = svc.store.get("zm_reports", draft["id"])
    assert stored is not None and [x["amount"] for x in stored["lines"]] == [3001, 4000]
    assert len(stored["source_lines"]) == 3
    rep, rows = svc.export_lines(draft["id"])
    assert zm_export.bzst_csv(rows, correction=False) == (
        BZST_HEAD + b"AT,U00000001,3001,S\r\nFR,00000000001,4000,S\r\n"
    )
    readable = zm_export.readable_csv(rows, correction=False).decode("utf-8-sig")
    assert "AT;U00000001;3001,00;3001,10;S" in readable
    assert rep["due_date"] == "2026-04-27"


def test_z2_credit_note_lands_in_the_quarter_of_the_change_without_drift(svc, journal):
    rid = file_q1(svc, journal)
    post_extra(journal)
    assert amounts(svc.preview(2026, 2)) == oracle_q("Z2", "2026-Q2") == {"ATU00000001": -500}
    assert amounts(svc.get(rid)) == oracle_q("Z2", "2026-Q1")
    assert svc.get(rid)["drift"] == {"entries": 0, "lines": []} and Z["Z2"]["q1_drift"] is False
    q2 = svc.create_draft(2026, 2)
    fin = svc.finalize(q2["id"], USER)
    _rep, rows = svc.export_lines(fin["id"])
    assert zm_export.bzst_csv(rows, correction=False) == BZST_HEAD + b"AT,U00000001,-500,S\r\n"


def test_z3_due_dates(svc):
    assert {f"2026-Q{x['quarter']}": x["due_date"] for x in svc.list(2026)} == Z["Z3"]["due"]


def test_z4_a_line_without_a_customer_vat_id_is_a_422(svc, journal):
    post_eu(journal, None, 100000, date(2026, 2, 3))
    with pytest.raises(ServiceError) as err:
        svc.preview(2026, 1)
    assert err.value.status == 422 and "VAT id" in err.value.message
    with pytest.raises(ServiceError) as err2:
        svc.create_draft(2026, 1)
    assert err2.value.status == 422


# ------------------------------------------------------------------ corrections and drift
def test_drift_after_finalising_and_correction_with_a_zero_row(svc, journal):
    rid = file_q1(svc, journal)
    assert svc.get(rid)["drift"]["entries"] == 0
    post_eu(journal, VAT_IDS["cust_B"], -400000, date(2026, 3, 20))  # late credit in Q1: B drops to 0
    post_eu(journal, VAT_IDS["cust_A"], 50000, date(2026, 2, 1))  # late invoice for Q1
    drift = svc.get(rid)["drift"]
    assert drift["entries"] == 2
    assert sorted((x["vat_id"], x["base_cents"]) for x in drift["lines"]) == [
        ("ATU00000001", 50000),
        ("FR00000000001", -400000),
    ]
    assert svc.list(2026)[0]["drift"] == 2 and svc.list(2026)[0]["state"] == "filed"
    assert amounts(svc.get(rid)) == {"ATU00000001": 3001, "FR00000000001": 4000}  # the stored snapshot is unchanged
    corr = svc.create_draft(2026, 1, amends_id=rid)
    assert corr["amends_id"] == rid and amounts(corr) == {"ATU00000001": 3501, "FR00000000001": 0}
    fin = svc.finalize(corr["id"], USER)
    assert fin["drift"]["entries"] == 0
    assert svc.store.get("zm_reports", rid)["replaced_by"] == corr["id"]  # type: ignore[index]
    assert [x["id"] for x in svc.list(2026)][0] == corr["id"]
    _rep, rows = svc.export_lines(corr["id"])
    assert zm_export.bzst_csv(rows, correction=True) == (BZST_HEAD + b"AT,U00000001,3501,S\r\nFR,00000000001,0,S\r\n")
    assert zm_export.bzst_csv(rows, correction=False) == BZST_HEAD + b"AT,U00000001,3501,S\r\n"
    svc.reopen(corr["id"])
    assert svc.store.get("zm_reports", rid).get("replaced_by") is None  # type: ignore[union-attr]


def test_amend_rules(svc, journal):
    rid = file_q1(svc, journal)
    with pytest.raises(ServiceError) as e1:
        svc.create_draft(2026, 1, amends_id="nope")
    assert e1.value.status == 404
    with pytest.raises(ServiceError) as e2:
        svc.create_draft(2026, 2, amends_id=rid)
    assert e2.value.status == 409
    with pytest.raises(ServiceError) as e3:
        svc.create_draft(2026, 1)  # the quarter has a report already
    assert e3.value.status == 409
    unfiled = svc.create_draft(2026, 2)
    post_extra(journal)
    svc.finalize(unfiled["id"], USER)
    with pytest.raises(ServiceError) as e4:
        svc.create_draft(2026, 2, amends_id=unfiled["id"])
    assert e4.value.status == 409 and "filed" in e4.value.message
    svc.create_draft(2026, 1, amends_id=rid)
    with pytest.raises(ServiceError) as e5:
        svc.create_draft(2026, 1, amends_id=rid)
    assert e5.value.status == 409


# ------------------------------------------------------------------ status rules
def test_status_cycle(svc, journal):
    post_case(journal, Z["Z1"])
    rid = svc.create_draft(2026, 1)["id"]
    with pytest.raises(ServiceError) as e0:
        svc.reopen(rid)
    assert e0.value.status == 409
    with pytest.raises(ServiceError) as e0b:
        svc.filed(rid, "2026-04-20", None, None)
    assert e0b.value.status == 409
    svc.finalize(rid, USER)
    with pytest.raises(ServiceError) as e1:
        svc.finalize(rid, USER)
    assert e1.value.status == 409
    with pytest.raises(ServiceError) as e2:
        svc.delete(rid)
    assert e2.value.status == 409
    assert svc.reopen(rid)["status"] == "draft"
    svc.finalize(rid, USER)
    filed = svc.filed(rid, "2026-04-20", "R-1", "note")
    assert (filed["filed_on"], filed["reference"], filed["note"]) == ("2026-04-20", "R-1", "note")
    for call in (lambda: svc.reopen(rid), lambda: svc.filed(rid, "2026-04-21", None, None)):
        with pytest.raises(ServiceError) as e3:
            call()
        assert e3.value.status == 409
    other = svc.create_draft(2026, 2)["id"]
    svc.delete(other)
    with pytest.raises(ServiceError) as e4:
        svc.get(other)
    assert e4.value.status == 404


def test_finalize_without_rows_is_refused_and_the_list_marks_quarters(svc, journal):
    assert [x["state"] for x in svc.list(2026)] == ["not_required"] * 4
    rid = svc.create_draft(2026, 3)["id"]
    with pytest.raises(ServiceError) as err:
        svc.finalize(rid, USER)
    assert err.value.status == 422 and "has not ended" in err.value.message
    post_case(journal, Z["Z1"])
    li = svc.list(2026)
    assert (li[0]["state"], li[0]["total"], li[0]["rows"]) == ("overdue", 7001, 2)  # today 2026-07-15, due 04-27
    assert li[1]["state"] == "not_required" and li[2]["state"] == "open"


def test_small_business_files_no_zm(repo, journal):
    repo.save("fiscal_years", fiscal_year(2027, small_business=True))
    svc = ZmReportService(repo, today=lambda: TODAY)
    post_eu(journal, VAT_IDS["cust_A"], 100000, date(2027, 2, 3))
    assert [x["state"] for x in svc.list(2027)] == ["not_required"] * 4
    prev = svc.preview(2027, 1)
    assert prev["required"] is False and amounts(prev) == {"ATU00000001": 1000}  # shown for information
    with pytest.raises(ServiceError) as err:
        svc.create_draft(2027, 1)
    assert err.value.status == 422
    with pytest.raises(ServiceError) as err2:
        svc.preview(2030, 1)  # not initialised
    assert err2.value.status == 422


# ------------------------------------------------------------------ API and rights
ZM_ROUTES = [
    ("GET", "/api/zm?year=2024"),
    ("GET", "/api/zm/preview?year=2024&quarter=1"),
    ("POST", "/api/zm"),
    ("GET", "/api/zm/xxxxxx"),
    ("POST", "/api/zm/xxxxxx/finalize"),
    ("POST", "/api/zm/xxxxxx/reopen"),
    ("POST", "/api/zm/xxxxxx/filed"),
    ("DELETE", "/api/zm/xxxxxx"),
    ("GET", "/api/zm/xxxxxx/export.csv"),
    ("GET", "/api/zm/xxxxxx/export.pdf"),
]


def _body(method: str, path: str) -> dict[str, Any] | None:
    if path == "/api/zm":
        return {"year": 2024, "quarter": 1}
    return {"filed_on": "2024-04-20"} if path.endswith("/filed") else None


def test_api_cycle_exports_and_rights(clients):
    boss, acc, emp = clients["boss"], clients["accountant"], clients["employee"]
    assert [x["state"] for x in boss.get("/api/zm?year=2024").json()] == ["not_required"] * 4
    pv = boss.get("/api/zm/preview?year=2024&quarter=2").json()
    assert pv["required"] is False and pv["lines"] == [] and pv["due_date"] == "2024-07-25"
    created = acc.post("/api/zm", json={"year": 2024, "quarter": 1})
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert boss.post("/api/zm", json={"year": 2024, "quarter": 1}).status_code == 409
    assert boss.post(f"/api/zm/{rid}/finalize").status_code == 422  # nothing to report
    csv_ = boss.get(f"/api/zm/{rid}/export.csv")
    assert csv_.status_code == 422 and csv_.json()["rows"] == [{"vat_id": "", "reason": "no rows to report"}]
    readable = boss.get(f"/api/zm/{rid}/export.csv?readable=1")
    assert readable.status_code == 200 and readable.content.startswith(b"\xef\xbb\xbfLand;USt-IdNr.")
    pdf = boss.get(f"/api/zm/{rid}/export.pdf")
    assert (
        pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"
    )
    assert boss.delete(f"/api/zm/{rid}").status_code == 204
    assert boss.get(f"/api/zm/{rid}").status_code == 404
    for method, path in ZM_ROUTES:
        r = emp.request(method, path, **({} if _body(method, path) is None else {"json": _body(method, path)}))
        assert r.status_code == 403, (method, path, r.status_code)
        r = acc.request(method, path, **({} if _body(method, path) is None else {"json": _body(method, path)}))
        assert r.status_code != 403, (method, path)


def test_zm_pdf_has_the_footer_and_the_rows(svc, journal):
    post_case(journal, Z["Z1"])
    rid = svc.create_draft(2026, 1)["id"]
    rep, _rows = svc.export_lines(rid)
    data = zm_export.zm_pdf(rep, business="Demo Studio", unconfirmed=True)
    assert data.startswith(b"%PDF") and len(data) > 800
    assert zm_export.ZM_FOOTER == "Not a filing. Enter or upload these figures in the BZSt online portal."


# ------------------------------------------------------------------ review fixes
def test_finalize_refuses_a_quarter_that_has_not_ended(svc, journal):
    post_eu(journal, VAT_IDS["cust_A"], 100000, date(2026, 8, 3))
    rid = svc.create_draft(2026, 3)["id"]  # today is 2026-07-15
    assert amounts(svc.get(rid)) == {"ATU00000001": 1000}
    with pytest.raises(ServiceError) as err:
        svc.finalize(rid, USER)
    assert err.value.status == 422 and "Q3 2026 has not ended" in err.value.message
    assert svc.get(rid)["status"] == "draft"
    last_day = ZmReportService(svc.store, today=lambda: date(2026, 9, 30))
    with pytest.raises(ServiceError):  # the last day itself is not over yet
        last_day.finalize(rid, USER)
    next_day = ZmReportService(svc.store, today=lambda: date(2026, 10, 1))
    assert next_day.finalize(rid, USER)["status"] == "final"


def test_list_survives_a_line_without_a_vat_id_in_one_quarter(svc, journal):
    post_case(journal, Z["Z1"])  # Q1 fine
    post_eu(journal, None, 100000, date(2026, 5, 3))  # Q2: an EU service without a customer VAT id
    li = svc.list(2026)
    assert [x["state"] for x in li] == ["overdue", "error", "not_required", "not_required"]
    assert li[0]["total"] == 7001 and li[0]["rows"] == 2 and li[0]["error"] is None
    assert li[1]["total"] is None and li[1]["rows"] is None and "VAT id" in li[1]["error"] and "R-" in li[1]["error"]
    for call in (
        lambda: svc.preview(2026, 2),
        lambda: svc.create_draft(2026, 2),
    ):
        with pytest.raises(ServiceError) as err:
            call()
        assert err.value.status == 422 and "R-" in err.value.message
        assert [r["vat_id"] for r in err.value.body["rows"]] == [""] and "R-" in err.value.body["rows"][0]["reason"]


def test_finalize_of_a_quarter_with_a_bad_line_is_422_listing_the_rows(svc, journal):
    post_eu(journal, VAT_IDS["cust_A"], 100000, date(2026, 5, 3))
    rid = svc.create_draft(2026, 2)["id"]
    post_eu(journal, "  ", 5000, date(2026, 6, 3))  # the bad line arrives after the draft
    with pytest.raises(ServiceError) as err:
        svc.finalize(rid, USER)
    assert err.value.status == 422 and len(err.value.body["rows"]) == 1
    assert "R-" in err.value.body["rows"][0]["reason"]
    assert svc.store.get("zm_reports", rid)["status"] == "draft"  # type: ignore[index]


def test_a_correction_lists_zero_rows_only_for_reported_customers(svc, journal):
    rid = file_q1(svc, journal)  # reports A (3001) and B (4000)
    post_eu(journal, "DE00000009", 40, date(2026, 3, 5))  # a third customer at 0.40 EUR: never reported, rounds to 0
    post_eu(journal, VAT_IDS["cust_B"], -400000, date(2026, 3, 20))  # B drops to 0: reported before
    corr = svc.create_draft(2026, 1, amends_id=rid)
    assert amounts(corr) == {"ATU00000001": 3001, "FR00000000001": 0}  # DE00000009 is not listed
    fin = svc.finalize(corr["id"], USER)
    assert [x["vat_id"] for x in fin["lines"]] == ["ATU00000001", "FR00000000001"]


def test_a_correction_where_every_row_drops_to_zero_is_exported(svc, journal):
    rid = file_q1(svc, journal)
    post_eu(journal, VAT_IDS["cust_A"], -300110, date(2026, 3, 1))
    post_eu(journal, VAT_IDS["cust_B"], -400000, date(2026, 3, 2))
    corr = svc.create_draft(2026, 1, amends_id=rid)
    assert amounts(corr) == {"ATU00000001": 0, "FR00000000001": 0}
    fin = svc.finalize(corr["id"], USER)
    assert [x["amount"] for x in fin["lines"]] == [0, 0]
    svc.filed(corr["id"], "2026-07-16", None, None)
    _rep, rows = svc.export_lines(corr["id"])
    assert zm_export.bzst_csv(rows, correction=True) == BZST_HEAD + b"AT,U00000001,0,S\r\nFR,00000000001,0,S\r\n"
    with pytest.raises(zm_export.ZmExportError):  # a first report would have nothing to write
        zm_export.bzst_csv(rows, correction=False)


@pytest.fixture
def synthetic_charts(tmp_path, monkeypatch):
    """The API app of a test has no chart files; point the chart loader at the synthetic SKR03 of the fixtures."""
    d = tmp_path / "charts"
    d.mkdir()
    shutil.copy(Path(__file__).parent / "fixtures" / "chart_synthetic.json", d / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", d)


def test_responses_carry_money_as_decimal_strings_only(clients, app, synthetic_charts):
    """The report, the list and the drift of the API: no raw cents and no integer totals (decimal-money-api.md)."""
    from permita38.core.service.journal import JournalService

    boss = clients["boss"]
    store = app.state.services.store
    journal = JournalService(store, clock=lambda: 1_800_000_000)
    post_eu(journal, "ATU00000001", 300110, date(2024, 2, 5))
    rid = boss.post("/api/zm", json={"year": 2024, "quarter": 1}).json()["id"]
    assert boss.post(f"/api/zm/{rid}/finalize").status_code == 200
    assert boss.post(f"/api/zm/{rid}/filed", json={"filed_on": "2024-04-20"}).status_code == 200
    post_eu(journal, "ATU00000001", 50000, date(2024, 3, 5))  # late line: drift
    rep = boss.get(f"/api/zm/{rid}").json()
    assert rep["lines"] == [{"vat_id": "ATU00000001", "country": "AT", "amount": "3001.00", "kind": "S"}]
    assert rep["drift"]["entries"] == 1 and rep["drift"]["lines"][0]["base"] == "500.00"
    assert "base_cents" not in rep["drift"]["lines"][0]
    q1 = boss.get("/api/zm?year=2024").json()[0]
    assert (q1["total"], q1["rows"], q1["drift"], q1["state"]) == ("3001.00", 1, 1, "filed")
    corr = boss.post("/api/zm", json={"year": 2024, "quarter": 1, "amends_id": rid}).json()
    assert corr["lines"][0]["amount"] == "3501.00" and "cents" not in corr["lines"][0]
    assert boss.get("/api/zm/preview?year=2024&quarter=1").json()["lines"][0]["amount"] == "3501.00"


def test_tokens_cannot_call_the_zm_write_routes(app, clients):
    from fastapi.testclient import TestClient

    boss = clients["boss"]
    for read_only in (True, False):
        tok = boss.post("/api/tokens", json={"label": "zm", "read_only": read_only})
        assert tok.status_code == 201, tok.text
        t = TestClient(app)
        t.headers["Authorization"] = f"Bearer {tok.json()['token']}"
        for method, path in ZM_ROUTES:
            if method == "GET":
                continue
            body = _body(method, path)
            r = t.request(method, path, **({} if body is None else {"json": body}))
            assert r.status_code == 403, (read_only, method, path, r.status_code)
            assert r.json() == {"detail": "This route cannot be used with an API token"}
        assert t.get("/api/zm?year=2024").status_code == 200  # reading stays possible
