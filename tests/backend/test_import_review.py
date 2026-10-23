"""Import: follow-ups of the review (in-file duplicates, counter on undo, hostile files, overwrite safety, values)."""

import io
import json
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from permita38.core.errors import ServiceError
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.importer import ImportService
from permita38.core.service.importer import tables as T
from permita38.core.service.importer import values as V
from permita38.core.service.importer.tables import read_table
from permita38.core.service.importer.xlsx_reader import XlsxError, read_xlsx
from permita38.core.service.numbering import NumberingService

PDF = b"%PDF-1.4\n% fake original for tests\n%%EOF\n"
HEAD = "Nummer;Datum;Kunde;Netto;USt;Brutto\n"
NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


@pytest.fixture
def store(tmp_path):
    repo = SQLiteRepository(tmp_path / "data")
    repo.startup()
    yield repo
    repo.close()


@pytest.fixture
def svc(store):
    return ImportService(store, numbering=NumberingService(store, clock=lambda: date(2026, 5, 5)))


def _b(text: str) -> bytes:
    return text.encode("utf-8")


def _xlsx(sheet_xml: str, extra: dict[str, bytes] | None = None) -> bytes:
    files: dict[str, bytes | str] = {
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    files.update(extra or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _pdf_zip(*names: str, content: bytes = PDF) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(n, content)
    return buf.getvalue()


# ---------------------------------------------------------------- duplicates inside one file
def test_duplicate_number_in_one_file_is_a_row_error(store, svc):
    csv_text = HEAD + "A-1;01.01.2026;Acme;10;1,90;11,90\nA-1;02.01.2026;Acme;20;3,80;23,80\n"
    report = svc.plan(_b(csv_text), "x.csv", "invoices")
    assert [r["action"] for r in report["rows"]] == ["create", "error"]
    assert "duplicate number A-1" in report["rows"][1]["messages"][0]
    run = svc.run(_b(csv_text), "x.csv", "invoices")
    assert run["applied"] is False and store.list("invoices") == []


def test_duplicate_external_id_in_one_file_is_a_row_error(svc):
    csv_text = "Nummer;Datum;Kunde;Netto;Externe ID\nA-1;01.01.2026;Acme;10;X9\nA-2;01.01.2026;Acme;10;X9\n"
    report = svc.plan(_b(csv_text), "x.csv", "invoices")
    assert [r["action"] for r in report["rows"]] == ["create", "error"]


def test_identical_expense_rows_are_flagged_and_external_ids_tell_them_apart(svc):
    same = "Datum;Lieferant;Netto\n01.01.2026;Cafe;4\n01.01.2026;Cafe;4\n"
    report = svc.plan(_b(same), "e.csv", "expenses")
    assert [r["action"] for r in report["rows"]] == ["create", "error"]
    assert "external ID" in report["rows"][1]["messages"][0]
    ok = "Datum;Lieferant;Netto;Externe ID\n01.01.2026;Cafe;4;a\n01.01.2026;Cafe;4;b\n"
    assert svc.plan(_b(ok), "e.csv", "expenses")["counts"]["error"] == 0


def test_duplicate_customers_in_one_file_are_created_once(store, svc):
    csv_text = "Name;E-Mail\nAcme GmbH;a@acme.example\nACME GmbH;A@ACME.EXAMPLE\n"
    report = svc.run(_b(csv_text), "k.csv", "customers")
    assert report["counts"]["create"] == 1 and report["counts"]["skip"] == 1
    assert len(store.list("customers")) == 1


def test_shared_numbering_checks_offer_numbers_too(store, svc):
    store.save_settings({**store.settings(), "numbering_shared": True})
    store.save("offers", dict(customer_id="c", number="A-1", date="2026-01-01", lines=[], status="sent"))
    report = svc.plan(_b(HEAD + "A-1;01.01.2026;Acme;10;1,90;11,90\n"), "x.csv", "invoices")
    assert report["rows"][0]["action"] == "error" and "shared numbering" in report["rows"][0]["messages"][0]
    store.save_settings({**store.settings(), "numbering_shared": False})
    assert svc.plan(_b(HEAD + "A-1;01.01.2026;Acme;10;1,90;11,90\n"), "x.csv", "invoices")["counts"]["error"] == 0


# ---------------------------------------------------------------- counter and undo
def test_undo_does_not_reset_the_counter(store, svc):
    csv_text = HEAD + "RE-2026-1043;02.02.2026;Acme;100;19;119\n"
    run = svc.run(_b(csv_text), "x.csv", "invoices")
    assert run["next_number"] == "RE-2026-1044"
    svc.undo(run["batch_id"])
    assert store.list("invoices") == []
    assert svc.numbering.preview("invoice", 1) == ["RE-2026-1044"]  # documented: undo keeps the counter


def test_imported_number_with_a_foreign_year_is_noted_and_does_not_move_the_counter(store, svc):
    report = svc.run(_b(HEAD + "RE-2025-0900;02.01.2026;Acme;100;19;119\n"), "x.csv", "invoices")
    assert "date part of the number differs" in " ".join(report["rows"][0]["messages"])
    assert report["next_number"] == "RE-2026-0001"


def test_undo_twice_says_already_undone(svc):
    run = svc.run(_b(HEAD + "A-1;01.01.2026;Acme;10;1,90;11,90\n"), "x.csv", "invoices")
    svc.undo(run["batch_id"])
    with pytest.raises(ServiceError) as e:
        svc.undo(run["batch_id"])
    assert e.value.status == 409 and "already undone" in e.value.message


# ---------------------------------------------------------------- overwrite keeps user edits and the original PDF
def test_overwrite_refused_after_the_user_edited_the_imported_document(store, svc):
    csv_text = HEAD + "A-1;01.01.2026;Acme;100;19;119\n"
    svc.run(_b(csv_text), "x.csv", "invoices")
    inv = store.list("invoices")[0]
    store.save("invoices", {**inv, "status": "paid", "notes": "paid by transfer"})
    changed = HEAD + "A-1;01.01.2026;Acme;90;17,10;107,10\n"
    report = svc.run(_b(changed), "x.csv", "invoices", options={"on_duplicate": "overwrite"})
    assert report["applied"] is False
    assert "changed since its import" in report["rows"][0]["messages"][0]
    kept = store.list("invoices")[0]
    assert kept["status"] == "paid" and kept["notes"] == "paid by transfer"


def test_overwrite_keeps_the_original_pdf_and_can_be_repeated(store, svc):
    csv_text = HEAD + "A-1;01.01.2026;Acme;100;19;119\n"
    svc.run(_b(csv_text), "x.csv", "invoices", pdfs=_pdf_zip("A-1.pdf"))
    before = store.list("invoices")[0]
    for net, vat, gross in (("90", "17,10", "107,10"), ("80", "15,20", "95,20")):
        r = svc.run(
            _b(HEAD + f"A-1;01.01.2026;Acme;{net};{vat};{gross}\n"), "x.csv", "invoices",
            options={"on_duplicate": "overwrite"},
        )  # fmt: skip
        assert r["applied"] is True and r["counts"]["overwrite"] == 1
    after = store.list("invoices")[0]
    assert after["id"] == before["id"]
    assert after["original_pdf"] == before["original_pdf"]
    assert after["original_pdf_sha256"] == before["original_pdf_sha256"]
    assert after["lines"][0]["price"] == 80.0


def test_original_pdf_fields_survive_an_edit_through_the_api(boss_client):
    c = boss_client
    csv_text = "Nummer;Datum;Kunde;Netto\nX-1;01.01.2026;Demo GmbH;100\n"
    r = c.post(
        "/api/import/run", data={"type": "invoices"},
        files={"file": ("x.csv", csv_text.encode()), "pdfs": ("p.zip", _pdf_zip("X-1.pdf"), "application/zip")},
    )  # fmt: skip
    assert r.status_code == 200, r.text
    inv = c.get("/api/invoices").json()[0]
    assert inv["original_pdf"] and len(inv["original_pdf_sha256"]) == 64
    body = {"customer_id": inv["customer_id"], "status": "sent", "date": "2026-01-01", "lines": inv["lines"]}
    body["original_pdf"] = "attacker.pdf"  # a client cannot swap the legal original
    assert c.put(f"/api/invoices/{inv['id']}", json=body).status_code == 200
    after = c.get(f"/api/invoices/{inv['id']}").json()
    assert after["original_pdf"] == inv["original_pdf"] and after["original_pdf_sha256"] == inv["original_pdf_sha256"]
    pdf = c.get(f"/api/invoices/{inv['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content == PDF


# ---------------------------------------------------------------- PDFs
def test_pdf_names_matching_one_number_twice_are_ambiguous(store, svc):
    csv_text = HEAD + "RE-1;01.01.2026;Acme;100;19;119\n"
    report = svc.plan(_b(csv_text), "x.csv", "invoices", pdfs=_pdf_zip("a/RE-1.pdf", "RE 1.pdf"))
    assert report["pdf"]["matched"] == 0 and sorted(report["pdf"]["unmatched"]) == ["RE 1.pdf", "RE-1.pdf"]


def test_pdf_size_limit_and_bad_archives(svc, monkeypatch):
    from permita38.core.service.importer import service as S

    csv_text = HEAD + "RE-1;01.01.2026;Acme;100;19;119\n"
    monkeypatch.setattr(S, "MAX_PDF_BYTES", 10)
    with pytest.raises(ServiceError) as e:
        svc.plan(_b(csv_text), "x.csv", "invoices", pdfs=_pdf_zip("RE-1.pdf"))
    assert e.value.status == 422
    for blob in (b"this is not a zip", b"PK\x03\x04garbage"):
        with pytest.raises(ServiceError) as e:
            svc.plan(_b(csv_text), "x.csv", "invoices", pdfs=blob)
        assert e.value.status == 400


# ---------------------------------------------------------------- hostile files are 400, never 500
def test_huge_csv_field_is_a_clean_error():
    with pytest.raises(ServiceError) as e:
        read_table(b"a;b\n" + b"x" * 200_000 + b";1\n", "x.csv")
    assert e.value.status == 400


def test_deeply_nested_json_is_a_clean_error(svc):
    with pytest.raises(ServiceError) as e:
        read_table(b"[" * 200_000, "x.json")
    assert e.value.status == 400
    with pytest.raises(ServiceError) as e:
        svc.plan(_b(HEAD), "x.csv", "invoices", mapping="[" * 200_000)
    assert e.value.status == 400


def test_malformed_xlsx_parts_are_clean_errors():
    for sheet in ("<worksheet", f"<worksheet {NS}><sheetData><row><c></row></sheetData></worksheet>"):
        with pytest.raises(ServiceError) as e:
            read_table(_xlsx(sheet), "x.xlsx")
        assert e.value.status == 400


def test_xlsx_utf16_part_cannot_hide_a_dtd():
    evil = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "aaaa">]><worksheet/>'
    with pytest.raises(XlsxError, match="not UTF-8"):
        read_xlsx(_xlsx("", extra={"xl/worksheets/sheet1.xml": evil.encode("utf-16")}))
    declared = f'<?xml version="1.0" encoding="UTF-16"?><worksheet {NS}/>'
    with pytest.raises(XlsxError, match="not UTF-8"):
        read_xlsx(_xlsx("", extra={"xl/worksheets/sheet1.xml": declared.encode("utf-8")}))


def test_xlsx_doctype_guard_on_wellformed_namespaced_sheets_and_entity_bombs():
    ok = f'<?xml version="1.0" encoding="UTF-8"?><worksheet {NS}><sheetData/></worksheet>'
    assert read_xlsx(_xlsx(ok)) == ("S", [])
    with_dtd = f"<!DOCTYPE worksheet [<!ELEMENT worksheet ANY>]><worksheet {NS}><sheetData/></worksheet>"
    with pytest.raises(XlsxError, match="DTD"):
        read_xlsx(_xlsx(with_dtd))
    lol = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">'
        f'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;">]><worksheet {NS}><sheetData/></worksheet>'
    )
    with pytest.raises(XlsxError, match="DTD"):
        read_xlsx(_xlsx(lol))
    lower = with_dtd.replace("DOCTYPE", "doctype")
    with pytest.raises(XlsxError, match="DTD"):
        read_xlsx(_xlsx(lower))
    shared_bomb = f'<!DOCTYPE sst [<!ENTITY a "x">]><sst {NS}><si><t>&a;</t></si></sst>'
    with pytest.raises(XlsxError, match="DTD"):
        read_xlsx(_xlsx(ok, extra={"xl/sharedStrings.xml": shared_bomb}))


# ---------------------------------------------------------------- XLSX size guards
def test_xlsx_row_cap_stops_reading_early():
    rows = "".join(f'<row r="{i}"><c r="A{i}"><v>{i}</v></c></row>' for i in range(1, 501))
    _, got = read_xlsx(_xlsx(f"<worksheet {NS}><sheetData>{rows}</sheetData></worksheet>"), max_rows=10)
    assert len(got) == 10


def test_xlsx_row_limit_is_reported_by_the_table_reader(monkeypatch):
    monkeypatch.setattr(T, "MAX_ROWS", 5)
    rows = "".join(f'<row r="{i}"><c r="A{i}"><v>{i}</v></c></row>' for i in range(1, 50))
    with pytest.raises(ServiceError) as e:
        read_table(_xlsx(f"<worksheet {NS}><sheetData>{rows}</sheetData></worksheet>"), "x.xlsx")
    assert e.value.status == 422


def test_xlsx_wide_cells_are_not_padded_to_the_last_column():
    far = f'<worksheet {NS}><sheetData><row r="1"><c r="A1"><v>1</v></c><c r="XFD1"><v>2</v></c></row></sheetData></worksheet>'
    with pytest.raises(XlsxError, match="too many columns"):
        read_xlsx(_xlsx(far))
    styled = (
        f'<worksheet {NS}><sheetData><row r="1"><c r="A1"><v>1</v></c><c r="XFD1" s="3"/></row></sheetData></worksheet>'
    )
    assert read_xlsx(_xlsx(styled))[1] == [[1.0]]


def test_xlsx_cell_budget(monkeypatch):
    from permita38.core.service.importer import xlsx_reader as X

    monkeypatch.setattr(X, "MAX_CELLS", 20)
    rows = "".join(f'<row r="{i}"><c r="A{i}"><v>1</v></c><c r="B{i}"><v>2</v></c></row>' for i in range(1, 30))
    with pytest.raises(XlsxError, match="too many cells"):
        read_xlsx(_xlsx(f"<worksheet {NS}><sheetData>{rows}</sheetData></worksheet>"))


def test_xlsx_float_noise_does_not_fail_every_row(store, svc):
    sheet = (
        f"<worksheet {NS}><sheetData>"
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Nummer</t></is></c><c r="B1" t="inlineStr"><is><t>Datum</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>Kunde</t></is></c><c r="D1" t="inlineStr"><is><t>Netto</t></is></c>'
        '<c r="E1" t="inlineStr"><is><t>USt</t></is></c><c r="F1" t="inlineStr"><is><t>Brutto</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>N-1</t></is></c><c r="B2"><v>46023</v></c>'
        '<c r="C2" t="inlineStr"><is><t>Acme</t></is></c><c r="D2"><v>100.00000000000001</v></c>'
        '<c r="E2"><v>19.000000000000004</v></c><c r="F2"><v>118.99999999999999</v></c></row>'
        "</sheetData></worksheet>"
    )
    report = svc.run(_xlsx(sheet), "x.xlsx", "invoices")
    assert report["applied"] is True, report
    assert store.list("invoices")[0]["lines"][0]["price"] == 100.0


# ---------------------------------------------------------------- values
@pytest.mark.parametrize(
    "text,expected",
    [("12,340", None), ("12,345", None), ("1,234,56", None), ("1.23.456,00", None), ("1000000000,01", None),
     ("1.234.567,89", "1234567.89"), ("1234,5", "1234.50"), (100.00000000000001, "100.00"), (0.1 + 0.2, "0.30")],
)  # fmt: skip
def test_amount_edge_cases(text, expected):
    if expected is None:
        with pytest.raises(V.CellError):
            V.parse_amount(text)
    else:
        assert V.parse_amount(text) == Decimal(expected)


def test_amount_decimal_options_resolve_the_ambiguity():
    with pytest.raises(V.CellError, match="more than two"):
        V.parse_amount("12,345", "comma")
    assert V.parse_amount("12,340", "comma") == Decimal("12.34")
    assert V.parse_amount("12,345", "point") == Decimal("12345.00")


def test_two_digit_years():
    assert V.parse_date("03.04.26") == date(2026, 4, 3)
    assert V.parse_date("01.01.69") == date(2069, 1, 1)
    with pytest.raises(V.CellError, match="two-digit"):
        V.parse_date("01.01.99")


def test_text_limits_and_control_characters():
    assert V.text("x" * 200, 200, "name") == "x" * 200
    with pytest.raises(V.CellError, match="longer than 200"):
        V.text("x" * 201, 200, "name")
    with pytest.raises(V.CellError, match="control"):
        V.text("a\x00b", 200, "name")
    with pytest.raises(V.CellError, match="control"):
        V.text("a\x1b[31mb", 200, "name")
    assert V.text("a\tb", 200, "name") == "a\tb"


def test_201_char_customer_name_is_a_row_error(svc):
    report = svc.plan(_b(HEAD + f"A-1;01.01.2026;{'x' * 201};10;1,90;11,90\n"), "x.csv", "invoices")
    assert report["rows"][0]["action"] == "error" and "longer than 200" in report["rows"][0]["messages"][0]


@pytest.mark.parametrize(
    "word,status",
    [("draft", "draft"), ("Entwurf", "draft"), ("offen draft", "draft"), ("sent", "sent"), ("versendet", "sent"),
     ("verschickt", "sent"), ("offen", "sent"), ("open", "sent"), ("unbezahlt", "sent"), ("paid", "paid"),
     ("bezahlt", "paid"), ("beglichen", "paid"), ("cancelled", "cancelled"), ("canceled", "cancelled"),
     ("storniert", "cancelled"), ("storno", "cancelled"), ("accepted", "accepted"), ("angenommen", "accepted"),
     ("rejected", "rejected"), ("abgelehnt", "rejected")],
)  # fmt: skip
def test_full_status_map(word, status):
    assert V.STATUS_MAP[word.casefold()] == status
    assert V.parse_status(f"  {word.upper()} ", "sent") == status


def test_status_map_has_no_untested_entries():
    tested = {"draft", "entwurf", "offen draft", "sent", "versendet", "verschickt", "offen", "open", "unbezahlt",
              "paid", "bezahlt", "beglichen", "cancelled", "canceled", "storniert", "storno", "accepted",
              "angenommen", "rejected", "abgelehnt"}  # fmt: skip
    assert set(V.STATUS_MAP) == tested


# ---------------------------------------------------------------- money
def test_tiny_amount_vat_rate_is_inferred_from_the_cents(svc):
    # net 0.05: 19 % -> 0.01, 7 % -> 0.00; the stated VAT picks the rate that reproduces it
    seven = svc.plan(_b(HEAD + "A-1;01.01.2026;Acme;0,05;0,00;0,05\n"), "x.csv", "invoices")
    assert seven["counts"]["error"] == 0 and seven["rows"][0]["totals"]["vat"] == 0.0
    nineteen = svc.plan(_b(HEAD + "A-2;01.01.2026;Acme;0,05;0,01;0,06\n"), "x.csv", "invoices")
    assert nineteen["counts"]["error"] == 0


def test_gross_only_file_derives_net_at_the_default_rate(store, svc):
    csv_text = "Nummer;Datum;Kunde;Brutto\nG-1;01.01.2026;Acme;119,00\nG-2;01.01.2026;Acme;119,01\n"
    report = svc.plan(_b(csv_text), "x.csv", "invoices")
    assert report["rows"][0]["totals"] == {"net": 100.0, "vat": 19.0, "gross": 119.0}
    # a gross that is not a round 19 % amount still adds up: net 100.01 + VAT 19.00 = 119.01 (no false error)
    assert report["rows"][1]["totals"] == {"net": 100.01, "vat": 19.0, "gross": 119.01}


def test_mixed_vat_documents_are_reported_not_guessed(svc):
    csv_text = (
        "Nummer;Datum;Kunde;Position;Menge;Preis;Netto;USt;Brutto\n"
        "M-1;01.01.2026;Acme;Buch;1;100;200;26;226\n"
        "M-1;01.01.2026;Acme;Beratung;1;100;;;\n"
    )  # 100 at 7 % + 100 at 19 % = 26 VAT, which no single rate reproduces
    report = svc.plan(_b(csv_text), "x.csv", "invoices")
    assert report["rows"][0]["action"] == "error" and "VAT rate" in report["rows"][0]["messages"][0]


def test_customer_matching_is_case_insensitive_for_vat_id_and_email(store, svc):
    store.save("customers", dict(name="Müller GmbH", vat_id="DE123456789", email="kontakt@mueller.example"))
    csv_text = (
        "Nummer;Datum;Kunde;Ust-IdNr;E-Mail;Netto\n"
        "A-1;01.01.2026;Andere;de123456789;;10\n"
        "A-2;01.01.2026;Wieder andere;;KONTAKT@Mueller.Example;10\n"
    )
    report = svc.plan(_b(csv_text), "x.csv", "invoices")
    assert [r["customer"]["action"] for r in report["rows"]] == ["merge", "merge"]
    assert all(r["customer"]["score"] == 1.0 for r in report["rows"])


def test_json_import_round_trip_uses_the_given_numbers(store, svc):
    data = [{"number": "J-1", "date": "2026-01-01", "customer_name": "Acme", "net": 10, "vat": 1.9, "gross": 11.9}]
    report = svc.run(json.dumps(data).encode(), "x.json", "invoices")
    assert report["applied"] is True and store.list("invoices")[0]["number"] == "J-1"
