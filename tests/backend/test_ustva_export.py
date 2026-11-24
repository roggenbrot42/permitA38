"""VAT return exports (docs/specs/ustva.md "API", Exports): CSV golden files written by hand, JSON equals the GET
response, PDF text, rights."""

import re
import shutil
import zlib
from datetime import date
from pathlib import Path

import pytest

from permita38.core import charts
from permita38.core.service.journal import JournalService, JournalSource, PostLine
from permita38.core.vat import ustva_export

from .test_rbac import PASSWORD, _login

YEAR = 2026
BOM = b"\xef\xbb\xbf"
EVIL = '=HYPERLINK("http://x.invalid","a;b")'


def _post_sale(journal, key, doc, base, tax, key_tax="S19", role="19"):
    src = JournalSource(
        event="issue", created_by="u1x000", source_coll="invoices", source_id="inv1abc", source_number=doc
    )
    revenue, vat_out = ("revenue_s19", "vat_out_s19") if key_tax == "S19" else ("revenue_s7", "vat_out_s7")
    journal.post(
        key, date(YEAR, 2, 15), key, src,
        [
            PostLine(role="bank", amount_cents=base + tax),
            PostLine(role=revenue, amount_cents=-base),
            PostLine(
                role=vat_out, amount_cents=-tax, tax_key=key_tax, tax_kind="tax", tax_rate=role,
                tax_base_cents=base, vat_date=date(YEAR, 2, 15),
            ),
        ],
    )  # fmt: skip


@pytest.fixture(autouse=True)
def synthetic_chart(tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(Path(__file__).parent / "fixtures" / "chart_synthetic.json", charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)


@pytest.fixture
def clients(app, boss_client):
    out = {"boss": boss_client}
    for role in ("accountant", "employee"):
        r = boss_client.post(
            "/api/users", json={"username": role[:3] + "x", "name": role.title(), "role": role, "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        out[role] = _login(app, role[:3] + "x")
    assert boss_client.put("/api/settings", json={"tax_number": "12/345/67890"}).status_code == 200
    year = boss_client.post("/api/accounting/years", json={"year": YEAR, "chart": "skr03", "income_basis": "eur"})
    assert year.status_code == 201, year.text
    journal = JournalService(app.state.services.store)
    _post_sale(journal, "s1", EVIL, 100000, 19000)
    _post_sale(journal, "s2", "R-2", 12345, 864, "S7", "7")
    return out


@pytest.fixture
def rid(clients):
    r = clients["boss"].post("/api/vat-returns", json={"year": YEAR, "kind": "month", "no": 2})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def pdf_text(data: bytes) -> list[str]:
    out = []
    for m in re.finditer(rb"stream\n(.*?)\nendstream", data, re.S):
        try:
            content = zlib.decompress(m.group(1))
        except zlib.error:
            continue
        for s in re.finditer(rb"\(((?:\\.|[^\\)])*)\) Tj", content):
            out.append(re.sub(rb"\\(.)", rb"\1", s.group(1)).decode("cp1252"))
    return out


# 81: base 1000 (whole euros), tax 190.00. 86: base 123.45 -> 123 whole euros, form tax 7 % of 123 = 8.61, booked 8.64.
# 83 = 190.00 + 8.61 = 198.61. Both lines are in the period (VAT date 2026-02-15).
GOLDEN_BOXES = (
    BOM
    + b"kz;label;base;tax\r\n"
    + b"81;Taxable sales at 19 % (base);1000,00;190,00\r\n"
    + b"86;Taxable sales at 7 % (base);123,00;8,61\r\n"
    + "83;Remaining prepayment (+) or refund (\u2212);;198,61\r\n".encode()
)


def test_boxes_csv_is_the_hand_written_golden(clients, rid):
    r = clients["boss"].get(f"/api/vat-returns/{rid}/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.content == GOLDEN_BOXES


GOLDEN_LINES = (
    BOM
    + b"kz;entry_no;booking_date;document;base;tax\r\n"
    + b'81;1;2026-02-15;"\'=HYPERLINK(""http://x.invalid"",""a;b"")";1000,00;190,00\r\n'
    + b"86;2;2026-02-15;R-2;123,45;8,64\r\n"
)


def test_lines_csv_neutralises_a_formula_in_the_document_number(clients, rid):
    r = clients["boss"].get(f"/api/vat-returns/{rid}/export.csv?lines=true")
    assert r.status_code == 200
    assert r.content == GOLDEN_LINES
    assert b"\r\n" in r.content and b"\n" not in r.content.replace(b"\r\n", b"")


def test_json_equals_the_get_response(clients, rid):
    boss = clients["boss"]
    for final in (False, True):  # a draft, then a final return
        got, exp = boss.get(f"/api/vat-returns/{rid}"), boss.get(f"/api/vat-returns/{rid}/export.json")
        assert exp.status_code == 200 and exp.json() == got.json()
        assert exp.json()["status"] == ("final" if final else "draft")
        if not final:
            assert boss.post(f"/api/vat-returns/{rid}/finalize?confirm_gap=true").status_code == 200


def test_pdf_has_figures_banner_footer_and_lines(clients, rid):
    r = clients["boss"].get(f"/api/vat-returns/{rid}/export.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    text = pdf_text(r.content)
    joined = "\n".join(text)
    for want in (
        "1000.00",
        "190.00",
        "123.00",
        "8.61",
        "8.64",
        "198.61",
        "Kz 81",
        "R-2",
        "Demo Studio",
        "12/345/67890",
    ):
        assert want in text or want in joined, want
    assert ustva_export.BANNER in joined
    assert ustva_export.FOOTER in joined
    assert "Rules: " in joined and "/vat-2026" in joined
    assert "2026-02-01 to 2026-02-28" in joined
    assert EVIL in joined  # escaped, printed as text


def test_pdf_without_banner_when_the_rules_are_confirmed():
    ret = {"year": 2026, "boxes": {"83": {"amount": "0.00"}}, "rules_version": "x/vat-2026", "result": "0.00"}
    on = "\n".join(pdf_text(ustva_export.return_pdf(ret, {}, unconfirmed=True)))
    off = "\n".join(pdf_text(ustva_export.return_pdf(ret, {}, unconfirmed=False)))
    assert ustva_export.BANNER in on and ustva_export.BANNER not in off
    assert ustva_export.FOOTER in off and "Rules: x/vat-2026" in off


def test_pdf_escapes_parentheses_and_backslashes_in_text():
    ret = {"year": 2026, "boxes": {}, "rules_version": "v"}
    line = {"entry_no": 1, "booking_date": "2026-01-02", "document": r"A) \ (B", "base": "1.00", "tax": "0.19"}
    data = ustva_export.return_pdf(ret, {"81": [line]}, business="Bad (Co) \\ x")
    joined = "\n".join(pdf_text(data))
    assert r"A) \ (B" in joined and r"Bad (Co) \ x" in joined


def test_exports_follow_the_vat_read_right(app, clients, rid):
    for ext in ("pdf", "csv", "json"):
        url = f"/api/vat-returns/{rid}/export.{ext}"
        assert clients["boss"].get(url).status_code == 200
        assert clients["accountant"].get(url).status_code == 200
        assert clients["employee"].get(url).status_code == 403
        assert clients["boss"].get(f"/api/vat-returns/nope00/export.{ext}").status_code == 404
    token = clients["boss"].post("/api/tokens", json={"label": "t", "read_only": True}).json()["token"]
    from fastapi.testclient import TestClient

    tok = TestClient(app)
    tok.headers["Authorization"] = f"Bearer {token}"
    assert tok.get(f"/api/vat-returns/{rid}/export.csv").status_code == 200
