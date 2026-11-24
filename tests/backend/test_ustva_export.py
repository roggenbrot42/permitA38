"""VAT return exports (docs/specs/ustva.md "API", Exports): CSV golden files written by hand, JSON equals the GET
response, PDF text, rights."""

import dataclasses
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


def _csv_text(data: bytes) -> str:
    return data.decode("utf-8-sig")


def test_pdf_prints_the_kz_83_label_with_an_ascii_minus(clients, rid):
    joined = "\n".join(pdf_text(clients["boss"].get(f"/api/vat-returns/{rid}/export.pdf").content))
    assert "Remaining prepayment (+) or refund (-)" in joined
    assert "−" not in joined


def test_banner_follows_the_rules_status_on_the_route(app, clients, rid, monkeypatch):
    url = f"/api/vat-returns/{rid}/export.pdf"
    on = "\n".join(pdf_text(clients["boss"].get(url).content))
    assert ustva_export.BANNER in on
    real = ustva_export._rules
    monkeypatch.setattr(ustva_export, "_rules", lambda year: dataclasses.replace(real(year), status="confirmed"))
    assert not ustva_export.rules_unconfirmed(YEAR)
    off = "\n".join(pdf_text(clients["boss"].get(url).content))
    assert ustva_export.BANNER not in off and ustva_export.FOOTER in off


def _mk(n):
    return [
        {"entry_no": i, "booking_date": "2026-01-02", "document": f"D{i}", "base": "1.00", "tax": "0.19"}
        for i in range(n)
    ]


def test_pdf_cap_stops_before_empty_headings_and_prints_one_note(monkeypatch):
    monkeypatch.setattr(ustva_export, "MAX_PDF_LINES", 3)
    ret = {"year": 2026, "boxes": {}, "rules_version": "v"}
    joined = "\n".join(pdf_text(ustva_export.return_pdf(ret, {"81": _mk(2), "86": _mk(2), "66": _mk(2)})))
    assert "Kz 81" in joined and "Kz 86" in joined  # 86 holds the third line
    assert "Kz 66" not in joined  # cap reached: no empty heading
    assert joined.count("more lines in the lines CSV") == 1 and "3 more lines" in joined
    joined = "\n".join(pdf_text(ustva_export.return_pdf(ret, {"81": _mk(3), "86": _mk(2)})))  # cap ends a box
    assert "Kz 86" not in joined and "2 more lines in the lines CSV" in joined


def test_a_label_starting_with_equals_is_neutralised_in_the_boxes_csv(monkeypatch):
    real = ustva_export._rules

    def evil(year):
        rules = real(year)
        boxes = tuple(dataclasses.replace(b, label_en="=1+1") if b.kz == "81" else b for b in rules.boxes)
        return dataclasses.replace(rules, boxes=boxes)

    monkeypatch.setattr(ustva_export, "_rules", evil)
    ret = {"year": 2026, "boxes": {"81": {"base": "100.00", "tax": "19.00"}}}
    assert _csv_text(ustva_export.return_csv(ret)) == "kz;label;base;tax\r\n81;'=1+1;100,00;19,00\r\n"


def test_negative_amount_and_amount_only_box_in_the_csv(app, clients):
    journal = JournalService(app.state.services.store)
    day = date(YEAR, 3, 12)
    src = JournalSource(
        event="issue", created_by="u1x000", source_coll="invoices", source_id="inv1abc", source_number="CN-1"
    )
    journal.post(
        "cn", day, "cn", src,
        [
            PostLine(role="bank", amount_cents=-23800),
            PostLine(role="revenue_s19", amount_cents=20000),
            PostLine(
                role="vat_out_s19", amount_cents=3800, tax_key="S19", tax_kind="tax", tax_rate="19",
                tax_base_cents=-20000, vat_date=day,
            ),
        ],
    )  # fmt: skip
    journal.post(
        "exp", day, "exp", src,
        [
            PostLine(role="expense_other", amount_cents=10000),
            PostLine(role="input_vat_s19", amount_cents=2600, tax_key="VST", tax_kind="input", vat_date=day),
            PostLine(role="bank", amount_cents=-12600),
        ],
    )  # fmt: skip
    boss = clients["boss"]
    r = boss.post("/api/vat-returns", json={"year": YEAR, "kind": "month", "no": 3})
    assert r.status_code == 201, r.text
    got = boss.get(f"/api/vat-returns/{r.json()['id']}/export.csv")
    # 81: base -200 whole euros, tax -38.00; 66 has only an amount (tax column); 83 = -38.00 - 26.00
    assert got.content == (
        BOM
        + b"kz;label;base;tax\r\n"
        + b"81;Taxable sales at 19 % (base);-200,00;-38,00\r\n"
        + b"66;Input VAT from invoices of other businesses;;26,00\r\n"
        + "83;Remaining prepayment (+) or refund (−);;-64,00\r\n".encode()
    )


def test_a_final_return_export_ignores_a_later_drift_line(app, clients, rid):
    boss = clients["boss"]
    url = f"/api/vat-returns/{rid}/export"
    assert boss.post(f"/api/vat-returns/{rid}/finalize?confirm_gap=true").status_code == 200
    before = [boss.get(f"{url}.csv").content, boss.get(f"{url}.csv?lines=true").content]
    pdf_before = "\n".join(pdf_text(boss.get(f"{url}.pdf").content))
    _post_sale(JournalService(app.state.services.store), "late", "LATE-9", 5000, 950)
    after = [boss.get(f"{url}.csv").content, boss.get(f"{url}.csv?lines=true").content]
    pdf_after = "\n".join(pdf_text(boss.get(f"{url}.pdf").content))
    assert after == before
    assert b"LATE-9" not in after[1] and "LATE-9" not in pdf_after
    assert pdf_after == pdf_before
