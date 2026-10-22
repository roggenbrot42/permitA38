"""Import of existing documents and master data: readers, mapping, validation, dedupe, run, undo, API and CLI."""

import io
import json
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from permita38.core.errors import ServiceError
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.importer import ImportService
from permita38.core.service.importer.mapping import auto_map, parse_mapping_file
from permita38.core.service.importer.tables import decode_text, detect_delimiter, read_table
from permita38.core.service.importer.values import CellError, parse_amount, parse_date, parse_status
from permita38.core.service.importer.xlsx_reader import XlsxError, open_guarded_zip, read_xlsx
from permita38.core.service.numbering import NumberingService

PDF = b"%PDF-1.4\n% fake original for tests\n%%EOF\n"


class FakeClock:
    def __init__(self, day):
        self.day = day

    def __call__(self):
        return self.day


@pytest.fixture
def store(tmp_path):
    repo = SQLiteRepository(tmp_path / "data")
    repo.startup()
    yield repo
    repo.close()


@pytest.fixture
def svc(store):
    return ImportService(store, numbering=NumberingService(store, clock=FakeClock(date(2026, 5, 5))))


INVOICE_CSV = (
    "Rechnungsnummer;Rechnungsdatum;Kunde;Ust-IdNr;Netto;USt;Brutto;Fällig;bezahlt am;Status\n"
    "RE-2025-0001;03.04.2025;Müller GmbH;DE 123 456 789;1.000,00;190,00;1.190,00;03.05.2025;10.04.2025;bezahlt\n"
    "RE-2025-0002;05.04.2025;Müller GmbH;DE123456789;100,00;19,00;119,00;;;offen\n"
)


def _b(text: str, enc: str = "utf-8") -> bytes:
    return text.encode(enc)


# ---------------------------------------------------------------- readers
@pytest.mark.parametrize(
    "data,delim,enc",
    [
        (_b("a;b\n1;2\n"), ";", "utf-8"),
        (_b("a,b\n1,2\n"), ",", "utf-8"),
        (b"\xef\xbb\xbfa;b\n1;2\n", ";", "utf-8-sig"),
        (_b("a\tb\n1\t2\n"), "\t", "utf-8"),
        (_b("Müller;Straße\n1;2\n", "cp1252"), ";", "cp1252"),
        (_b('a,b\n"x, y";2\n'), ",", "utf-8"),
    ],
)
def test_csv_dialect_and_encoding_matrix(data, delim, enc):
    table = read_table(data, "t.csv")
    assert table.info["delimiter"] == delim
    assert table.info["encoding"] == enc
    assert len(table.rows[0]) == len(table.headers)


def test_decode_text_falls_back_to_latin1_for_undefined_bytes():
    assert decode_text(b"\x81x")[1] == "latin-1"


def test_detect_delimiter_prefers_consistent_separator():
    assert detect_delimiter("a;b;c\n1;2;3\n") == ";"
    assert detect_delimiter("a, b\n1, 2\n") == ","


def test_short_rows_are_padded_and_empty_file_is_refused():
    t = read_table(_b("a;b;c\n1;2\n"), "t.csv")
    assert t.rows == [["1", "2", ""]]
    with pytest.raises(ServiceError):
        read_table(_b("\n\n"), "t.csv")


def _xlsx(sheet_xml: str, shared: list[str] | None = None, extra: dict[str, str] | None = None) -> bytes:
    files = {
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Rechnungen" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    if shared is not None:
        items = "".join(f"<si><t>{s}</t></si>" for s in shared)
        files["xl/sharedStrings.xml"] = (
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{items}</sst>'
        )
    files.update(extra or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def test_xlsx_shared_and_inline_strings_dates_and_formulas():
    sheet = (
        f"<worksheet {NS}><sheetData>"
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>Netto</t></is></c>'
        '<c r="D1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>46000</v></c>'
        '<c r="C2"><f>SUM(1,2)</f><v>3</v></c><c r="D2" t="str"><f>"x"</f><v>text</v></c></row>'
        "</sheetData></worksheet>"
    )
    name, rows = read_xlsx(_xlsx(sheet, shared=["Nummer", "Datum", "RE-1"]))
    assert name == "Rechnungen"
    assert rows[0] == ["Nummer", "Netto", None, "Datum"]
    assert rows[1] == ["RE-1", 46000.0, 3.0, "text"]  # formula text ignored, cached value used
    assert parse_date(rows[1][1]) == date(2025, 12, 9)  # Excel serial


def test_xlsx_unknown_sheet_and_dtd_are_refused():
    sheet = f"<worksheet {NS}><sheetData/></worksheet>"
    with pytest.raises(XlsxError):
        read_xlsx(_xlsx(sheet), sheet="Nope")
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><worksheet>&a;</worksheet>'
    with pytest.raises(XlsxError):
        read_xlsx(_xlsx(evil))


def test_xlsx_zip_guards():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.xml", b"0" * (5 * 1024 * 1024))  # compresses far more than 100:1
    with pytest.raises(XlsxError):
        open_guarded_zip(buf.getvalue())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(501):
            zf.writestr(f"f{i}.txt", "x")
    with pytest.raises(XlsxError):
        open_guarded_zip(buf.getvalue())
    with pytest.raises(XlsxError):
        open_guarded_zip(b"not a zip")


# ---------------------------------------------------------------- values and mapping
def test_amount_formats_and_exact_cents():
    assert str(parse_amount("1.234,56")) == "1234.56"
    assert str(parse_amount("1,234.56")) == "1234.56"
    assert str(parse_amount("-12,5")) == "-12.50"
    assert str(parse_amount("(3,00)")) == "-3.00"
    assert str(parse_amount("€ 19,90")) == "19.90"
    assert parse_amount("") is None
    for bad in ("1,234", "abc", "nan", "1e9", True):
        with pytest.raises(CellError):
            parse_amount(bad)


def test_dates_and_status():
    assert parse_date("03.04.2026") == date(2026, 4, 3)
    assert parse_date("2026-04-03") == date(2026, 4, 3)
    assert parse_date("05/06/2026") == date(2026, 6, 5)
    assert parse_date("05/06/2026", "mdy") == date(2026, 5, 6)
    for bad in ("31.02.2026", "1999-01-01", "2101-01-01", "soon"):
        with pytest.raises(CellError):
            parse_date(bad)
    assert parse_status("Bezahlt", "sent") == "paid"
    assert parse_status(None, "sent") == "sent"
    with pytest.raises(CellError):
        parse_status("kaputt", "sent")


def test_auto_detection_of_german_and_english_headers():
    mapping, unmapped = auto_map(
        ["Rechnungsnummer", "Datum", "Kunde", "Netto", "USt", "Brutto", "Fällig", "bezahlt am", "Foo"], "invoices"
    )
    assert mapping == {
        "number": "Rechnungsnummer", "date": "Datum", "due_date": "Fällig", "paid_date": "bezahlt am",
        "customer_name": "Kunde", "net": "Netto", "vat": "USt", "gross": "Brutto",
    }  # fmt: skip
    assert unmapped == ["Foo"]
    mapping, _ = auto_map(["Invoice No", "Invoice Date", "Client", "Total"], "invoices")
    assert mapping["number"] == "Invoice No" and mapping["gross"] == "Total"


def test_mapping_file_formats_and_bad_options():
    cols, opts = parse_mapping_file({"columns": {"number": "Nr"}, "options": {"decimal": "comma"}})
    assert cols == {"number": "Nr"} and opts == {"decimal": "comma"}
    assert parse_mapping_file({"number": "Nr"})[0] == {"number": "Nr"}
    with pytest.raises(ServiceError):
        parse_mapping_file({"options": {"decimal": "weird"}})
    with pytest.raises(ServiceError):
        parse_mapping_file("[1, 2]")


# ---------------------------------------------------------------- invoices: plan, run, numbering, totals
def test_dry_run_equals_real_run_and_numbers_are_kept(store, svc):
    dry = svc.plan(_b(INVOICE_CSV), "rechnungen.csv", "invoices")
    real = svc.run(_b(INVOICE_CSV), "rechnungen.csv", "invoices")
    assert dry["counts"] == {k: real["counts"][k] for k in dry["counts"]}
    assert dry["counts"]["create"] == 2
    numbers = sorted(i["number"] for i in store.list("invoices"))
    assert numbers == ["RE-2025-0001", "RE-2025-0002"]  # never renumbered
    inv = next(i for i in store.list("invoices") if i["number"] == "RE-2025-0001")
    assert inv["imported"] is True and inv["original_number"] == "RE-2025-0001"
    assert inv["status"] == "paid" and inv["paid_date"] == "2025-04-10" and inv["due_date"] == "2025-05-03"
    assert inv["lines"][0]["description"] == "Imported total" and inv["vat_rate"] == 19
    assert store.get("customers", inv["customer_id"])["vat_id"] == "DE123456789"


def test_totals_must_match_exactly_to_the_cent(store, svc):
    bad = INVOICE_CSV.replace("119,00;", "119,01;")  # 100.00 + 19 % = 119.00
    report = svc.plan(_b(bad), "x.csv", "invoices")
    assert report["counts"]["error"] == 1
    assert "gross: file 119.01, computed 119.00" in report["rows"][1]["messages"][0]
    assert store.list("invoices") == []
    run = svc.run(_b(bad), "x.csv", "invoices")
    assert run["applied"] is False and run["counts"]["error"] == 1
    assert store.list("invoices") == [] and store.list("customers") == []


def test_run_refuses_all_or_nothing_and_reports_applied_false(store, svc):
    bad = INVOICE_CSV.replace("119,00;", "119,01;")
    report = svc.run(_b(bad), "x.csv", "invoices")
    assert report["applied"] is False and report["counts"]["error"] == 1
    assert store.list("invoices") == [] and store.list("customers") == []


def test_accept_unverified_marks_the_document(store, svc):
    bad = INVOICE_CSV.replace("119,00;", "119,01;")
    report = svc.run(_b(bad), "x.csv", "invoices", options={"accept_unverified": True})
    assert report["applied"] is True
    inv = next(i for i in store.list("invoices") if i["number"] == "RE-2025-0002")
    assert inv["totals_unverified"] is True and inv["given_totals"]["gross"] == 119.01


def test_multi_line_mode_groups_by_number_and_computes_totals(store, svc):
    csv_text = (
        "Nummer;Datum;Kunde;Position;Menge;Preis;Netto;USt;Brutto\n"
        "RE-2026-0100;01.02.2026;Acme AG;Beratung;2,5;80;219,99;41,80;261,79\n"
        "RE-2026-0100;01.02.2026;Acme AG;Material;1;19,99;;;\n"
    )
    report = svc.run(_b(csv_text), "x.csv", "invoices")
    assert report["applied"] is True
    inv = store.list("invoices")[0]
    assert [ln["description"] for ln in inv["lines"]] == ["Beratung", "Material"]
    assert inv["lines"][0]["price"] == 80.0 and inv["vat_rate"] == 19
    assert report["rows"][0]["totals"] == {"net": 219.99, "vat": 41.8, "gross": 261.79}


def test_customer_matching_by_vat_id_email_and_fuzzy_name(store, svc):
    store.save("customers", dict(name="Müller GmbH", vat_id="DE123456789", email="kontakt@mueller.example"))
    store.save("customers", dict(name="Schmidt Bau", email="info@schmidt.example"))
    csv_text = (
        "Nummer;Datum;Kunde;Ust-IdNr;E-Mail;Netto;USt;Brutto\n"
        "A-1;01.01.2026;Anders Name;DE 123 456 789;;10;1,90;11,90\n"  # by VAT ID
        "A-2;01.01.2026;Schmidt Bau;;INFO@SCHMIDT.EXAMPLE;10;1,90;11,90\n"  # by e-mail
        "A-3;01.01.2026;Schmit Bau;;;10;1,90;11,90\n"  # fuzzy name, >= 0.9: merged
        "A-4;01.01.2026;Völlig Neu;;;10;1,90;11,90\n"  # new
    )
    # fuzzy matches are only merged on request; by default they are created as new and flagged
    report = svc.plan(_b(csv_text), "x.csv", "invoices")
    assert [r["customer"]["action"] for r in report["rows"]] == ["merge", "merge", "create", "create"]
    assert report["rows"][2]["customer"]["score"] >= 0.9
    assert "merge_similar" in " ".join(report["rows"][2]["messages"])
    report = svc.plan(_b(csv_text), "x.csv", "invoices", options={"merge_similar": True})
    assert [r["customer"]["action"] for r in report["rows"]] == ["merge", "merge", "merge", "create"]
    assert report["counts"]["customers_created"] == 1
    svc.run(_b(csv_text), "x.csv", "invoices", options={"merge_similar": True})
    assert len(store.list("customers")) == 3  # no duplicate customer was created


def test_customers_differing_only_in_digits_are_never_merged(store, svc):
    store.save("customers", dict(name="Schmidt Bau 1"))
    csv_text = "Nummer;Datum;Kunde;Netto\nA-1;01.01.2026;Schmidt Bau 2;10\n"
    report = svc.plan(_b(csv_text), "x.csv", "invoices", options={"merge_similar": True})
    assert report["rows"][0]["customer"]["action"] == "create"


def test_ambiguous_thousands_amount_is_rejected_in_auto_mode():
    from permita38.core.service.importer import values as V

    for text in ("1.500", "1,500", "999.999"):
        with pytest.raises(V.CellError, match="ambiguous"):
            V.parse_amount(text)
    assert V.parse_amount("1.500", "comma") == Decimal("1500.00")
    assert V.parse_amount("1.500", "point") == Decimal("1.50")
    assert V.parse_amount("0.500") == Decimal("0.50")
    assert V.parse_amount("1.500,00") == Decimal("1500.00")


def test_xlsx_cell_reference_beyond_last_column_is_refused():
    from permita38.core.service.importer import xlsx_reader as X

    with pytest.raises(X.XlsxError):
        X._col_index("AAAAAAA1")
    assert X._col_index("XFD1") == 16383


def test_duplicate_numbers_are_skipped_by_default_and_reimport_is_idempotent(store, svc):
    svc.run(_b(INVOICE_CSV), "x.csv", "invoices")
    second = svc.run(_b(INVOICE_CSV), "x.csv", "invoices")
    assert second["counts"]["skip"] == 2 and second["counts"]["create"] == 0
    assert len(store.list("invoices")) == 2


def test_duplicate_number_of_a_manual_document_is_a_conflict_when_overwriting(store, svc):
    store.save(
        "invoices", dict(customer_id="cust0001", number="RE-2025-0001", date="2025-01-01", lines=[], status="sent")
    )
    report = svc.plan(_b(INVOICE_CSV), "x.csv", "invoices", options={"on_duplicate": "overwrite"})
    assert report["rows"][0]["action"] == "error"


def test_overwrite_replaces_earlier_import_but_undo_is_then_refused(store, svc):
    svc.run(_b(INVOICE_CSV), "x.csv", "invoices")
    changed = INVOICE_CSV.replace("1.000,00;190,00;1.190,00", "900,00;171,00;1.071,00")
    report = svc.run(_b(changed), "x.csv", "invoices", options={"on_duplicate": "overwrite"})
    assert report["counts"]["overwrite"] == 1 and report["applied"] is True
    assert sorted(i["number"] for i in store.list("invoices")) == ["RE-2025-0001", "RE-2025-0002"]
    with pytest.raises(ServiceError):
        svc.undo(report["batch_id"])


def test_imported_numbers_move_the_next_number_and_say_so(store, svc):
    csv_text = "Rechnungsnummer;Datum;Kunde;Netto;USt;Brutto\nRE-2026-1043;02.02.2026;Acme;100;19;119\n"
    report = svc.run(_b(csv_text), "x.csv", "invoices")
    assert report["next_number_before"] == "RE-2026-0001"
    assert report["next_number"] == "RE-2026-1044"


def test_currency_and_bad_status_are_row_errors(svc):
    csv_text = (
        "Nummer;Datum;Kunde;Netto;USt;Brutto;Währung;Status\n"
        "X-1;01.01.2026;Acme;10;1,90;11,90;USD;sent\n"
        "X-2;01.01.2026;Acme;10;1,90;11,90;EUR;kaputt\n"
    )
    report = svc.plan(_b(csv_text), "x.csv", "invoices")
    msgs = [r["messages"][0] for r in report["rows"]]
    assert "only EUR" in msgs[0] and "unknown status" in msgs[1]


def test_csv_injection_is_stored_as_text(store, svc):
    csv_text = 'Nummer;Datum;Kunde;Netto;Betreff;USt;Brutto\nQ-1;01.01.2026;=HYPERLINK("x");10;=1+1;1,90;11,90\n'
    svc.run(_b(csv_text), "x.csv", "invoices")
    inv = store.list("invoices")[0]
    assert inv["title"] == "=1+1"
    assert store.list("customers")[0]["name"] == '=HYPERLINK("x")'


def test_pdf_attachment_is_matched_and_served_as_original(store, svc):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("scans/RE-2025-0001.pdf", PDF)
        zf.writestr("RE 2025 0002.PDF", PDF)
        zf.writestr("unknown.pdf", PDF)
    report = svc.run(_b(INVOICE_CSV), "x.csv", "invoices", pdfs=buf.getvalue())
    assert report["pdf"]["matched"] == 2 and report["pdf"]["unmatched"] == ["unknown.pdf"]
    inv = next(i for i in store.list("invoices") if i["number"] == "RE-2025-0001")
    assert store.attachment_path(inv["original_pdf"]) is not None
    assert len(inv["original_pdf_sha256"]) == 64
    from permita38.core.service.records import RecordService
    from permita38.core.service.sales import SalesService

    sales = SalesService(store, RecordService(store))
    data, name = sales.pdf("invoices", inv["id"])
    assert data == PDF and name == "RE-2025-0001.pdf"


def test_pdf_zip_must_contain_real_pdfs(svc):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("RE-2025-0001.pdf", b"<html>not a pdf</html>")
    with pytest.raises(ServiceError):
        svc.plan(_b(INVOICE_CSV), "x.csv", "invoices", pdfs=buf.getvalue())


def test_row_limit_and_size_limit(store, svc, monkeypatch):
    from permita38.core.service.importer import tables

    monkeypatch.setattr(tables, "MAX_ROWS", 3)
    rows = "".join(f"N-{i};01.01.2026;Acme;1;0;1\n" for i in range(5))
    with pytest.raises(ServiceError) as e:
        svc.plan(_b("Nummer;Datum;Kunde;Netto;USt;Brutto\n" + rows), "x.csv", "invoices")
    assert e.value.status == 422
    monkeypatch.setattr(tables, "MAX_BYTES", 10)
    with pytest.raises(ServiceError) as e:
        svc.plan(b"x" * 11, "x.csv", "invoices")
    assert e.value.status == 413


def test_undo_refused_after_a_record_was_changed(store, svc):
    run = svc.run(_b(INVOICE_CSV), "x.csv", "invoices")
    inv = store.list("invoices")[0]
    store.save("invoices", {**inv, "status": "cancelled"})  # changed after the import
    with pytest.raises(ServiceError) as e:
        svc.undo(run["batch_id"])
    assert e.value.status == 409
    assert len(store.list("invoices")) == 2


def test_undo_removes_all_records_of_a_batch(store, svc):
    run = svc.run(_b(INVOICE_CSV), "x.csv", "invoices")
    assert svc.undo(run["batch_id"])["removed"] == 3  # two invoices and one shared customer
    assert store.list("invoices") == [] and store.list("customers") == []
    assert [b["status"] for b in svc.batches()] == ["undone"]
    with pytest.raises(ServiceError):
        svc.undo(run["batch_id"])


def test_undo_refused_when_an_imported_record_was_deleted(store, svc):
    run = svc.run(_b(INVOICE_CSV), "x.csv", "invoices")
    store.delete("invoices", store.list("invoices")[0]["id"])
    with pytest.raises(ServiceError) as e:
        svc.undo(run["batch_id"])
    assert e.value.status == 409


# ---------------------------------------------------------------- customers and expenses
def test_customer_import_and_dedupe(store, svc):
    store.save("customers", dict(name="Acme GmbH", vat_id="DE999"))
    csv_text = "Name;USt-IdNr;Ort;E-Mail\nAcme GmbH;DE 999;Berlin;a@acme.example\nNeu Kunde;;Hamburg;\n"
    report = svc.plan(_b(csv_text), "k.csv", "customers")
    assert [r["action"] for r in report["rows"]] == ["skip", "create"]
    assert report["rows"][0]["customer"]["action"] == "merge"
    svc.run(_b(csv_text), "k.csv", "customers")
    assert sorted(c["name"] for c in store.list("customers")) == ["Acme GmbH", "Neu Kunde"]
    bad = csv_text + "Bad;;;nicht-mail\n"
    result = svc.run(_b(bad), "k.csv", "customers")
    assert result["applied"] is False and result["rows"][-1]["messages"]
    assert len(store.list("customers")) == 2


def test_expense_import(store, svc):
    csv_text = "Datum;Lieferant;Beschreibung;Netto;USt;Kategorie\n01.03.2026;Büro AG;Papier;100,00;19,00;Büro\n"
    report = svc.run(_b(csv_text), "e.csv", "expenses")
    assert report["counts"]["create"] == 1
    exp = store.list("expenses")[0]
    assert exp["vendor"] == "Büro AG" and exp["net"] == 100.0 and exp["vat"] == 19.0 and exp["vat_rate"] == 19.0


def test_json_export_import_with_lines(store, svc):
    records = [
        {"number": "RE-2024-0009", "date": "2024-11-30", "customer_name": "Json Kunde", "status": "paid",
         "net": 50, "vat": 9.5, "gross": 59.5, "vat_rate": 19, "paid_date": "2024-12-05"},
    ]  # fmt: skip
    report = svc.run(json.dumps({"invoices": records}).encode(), "export.json", "invoices")
    assert report["counts"]["create"] == 1
    assert store.list("invoices")[0]["number"] == "RE-2024-0009"


def test_parse_status_default_and_missing_columns(svc):
    with pytest.raises(ServiceError):
        svc.plan(_b("Foo;Bar\n1;2\n"), "x.csv", "invoices")


# ---------------------------------------------------------------- API and CLI
def _upload(name: str, data: bytes):
    return (name, data, "text/csv")


def test_import_api_preview_run_and_undo(boss_client):
    files = {"file": _upload("rechnungen.csv", INVOICE_CSV.encode("utf-8"))}
    r = boss_client.post("/api/import/preview", data={"type": "invoices"}, files=files)
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["create"] == 2 and r.json()["applied"] is False
    bad = INVOICE_CSV.replace("119,00;", "119,01;")
    r = boss_client.post("/api/import/run", data={"type": "invoices"}, files={"file": _upload("b.csv", bad.encode())})
    assert r.status_code == 422 and r.json()["detail"]["applied"] is False
    r = boss_client.post("/api/import/run", data={"type": "invoices"}, files=files)
    assert r.status_code == 200 and r.json()["applied"] is True
    batch = r.json()["batch_id"]
    assert boss_client.get("/api/import/batches").json()[0]["id"] == batch
    r = boss_client.post(f"/api/import/batches/{batch}/undo")
    assert r.status_code == 200
    assert boss_client.post(f"/api/import/batches/{batch}/undo").status_code == 409
    r = boss_client.post("/api/import/run", data={"type": "weird"}, files=files)
    assert r.status_code == 422


def test_import_api_is_boss_only(member_client):
    r = member_client.post("/api/import/preview", data={"type": "invoices"}, files={"file": _upload("x.csv", b"a;b\n")})
    assert r.status_code == 403


def test_cli_dry_run_and_import(tmp_path, capsys):
    from permita38.cli.main import main

    f = tmp_path / "rechnungen.csv"
    f.write_text(INVOICE_CSV, encoding="utf-8")
    data = tmp_path / "data"
    main(["--data", str(data), "import-documents", str(f), "--type", "invoices", "--dry-run"])
    out = capsys.readouterr().out
    assert "create 2" in out and "Dry run" in out
    main(["--data", str(data), "import-documents", str(f), "--type", "invoices"])
    out = capsys.readouterr().out
    assert "Imported. Batch" in out


def test_accounting_hook_is_called_for_each_imported_document(store, svc, monkeypatch):
    from permita38.core.service.importer import service as importer_service

    seen = []
    monkeypatch.setattr(importer_service, "IMPORT_HOOKS", [lambda coll, rec: seen.append((coll, rec["number"]))])
    svc.run(_b(INVOICE_CSV), "x.csv", "invoices")
    assert seen == [("invoices", "RE-2025-0001"), ("invoices", "RE-2025-0002")]


def test_editing_an_imported_invoice_keeps_the_importer_fields(boss_client):
    c = boss_client
    cust = c.post("/api/customers", json={"name": "Demo GmbH"}).json()
    csv_text = "Nummer;Datum;Kunde;Netto\nX-1;01.01.2026;Demo GmbH;100\n"
    r = c.post("/api/import/run", data={"type": "invoices"}, files={"file": ("x.csv", csv_text.encode())})
    assert r.status_code == 200, r.text
    inv = next(i for i in c.get("/api/invoices").json() if i["number"] == "X-1")
    body = {"customer_id": inv["customer_id"], "status": "sent", "date": "2026-01-01", "lines": inv["lines"]}
    assert c.put(f"/api/invoices/{inv['id']}", json=body).status_code == 200
    after = c.get(f"/api/invoices/{inv['id']}").json()
    assert after["imported"] is True and after["original_number"] == "X-1" and after["external_id"]
    assert cust["id"]
