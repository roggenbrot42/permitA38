"""DATEV EXTF file, report and zip (docs/specs/datev-export.md "File", "Self-check", D1 and D3).

The golden file is written by hand: header of 31 fields, column header of 125 columns, rows with every field.
Account numbers of ``fixtures/chart_synthetic.json``: receivables 9003, revenue_s19 9007, payables 9004,
meals_deductible 9036, meals_nondeductible 9037.
"""

import io
import zipfile
from dataclasses import replace
from datetime import date, datetime

import pytest

from permita38.core.accounting.datev import DatevRow, batch_rows
from permita38.core.accounting.datev_file import (
    COLUMN_HEADER,
    DatevHeaderParams,
    datev_zip,
    extf_csv,
    extf_filename,
    self_check_report,
    unencodable_characters,
)
from permita38.core.accounting.types import AccountNo, Cents
from permita38.core.datev import datev_rules_for
from tests.backend.test_datev_rows import RATES, _chart, case_entries

RULES = datev_rules_for(2026)
PARAMS = DatevHeaderParams(
    header=RULES.header,
    created=datetime(2026, 4, 2, 13, 45, 59, 123000),
    consultant_no=1001,
    client_no=12345,
    fiscal_year_start=date(2026, 1, 1),
    account_length=4,
    date_from=date(2026, 1, 1),
    date_to=date(2026, 3, 31),
    label="Q1 2026",
)

HEADER_LINE = (
    '"EXTF";700;21;"Buchungsstapel";13;20260402134559123;;"";"";"";1001;12345;20260101;4;20260101;20260331;'
    '"Q1 2026";"";1;0;0;"EUR";;;;;;;;;'
)
COLUMN_LINE = ";".join(
    [
        "Umsatz (ohne Soll/Haben-Kz)",
        "Soll/Haben-Kennzeichen",
        "WKZ Umsatz",
        "Kurs",
        "Basisumsatz",
        "WKZ Basisumsatz",
        "Konto",
        "Gegenkonto (ohne BU-Schlüssel)",
        "BU-Schlüssel",
        "Belegdatum",
        "Belegfeld 1",
        "Belegfeld 2",
        "Skonto",
        "Buchungstext",
    ]
)


def row(
    cents: int,
    konto: str,
    gegen: str,
    bu: str | None,
    beleg: str,
    text: str,
    *,
    key: str = "D",
    festschreibung: bool = False,
) -> DatevRow:
    return DatevRow(
        amount_cents=Cents(cents),
        side="S",
        konto=AccountNo(konto),
        gegenkonto=AccountNo(gegen),
        bu_key=bu,
        belegdatum=beleg,
        belegfeld1=key,
        text=text,
        festschreibung=festschreibung,
    )


def data_line(first14: str, festschreibung: str) -> str:
    # fields 15 to 113 are empty, 114 is Festschreibung, 115 to 125 are empty
    return first14 + ";" * 100 + festschreibung + ";" * 11


GOLDEN_ROWS = [
    row(119000, "9003", "9007", None, "1401", "Rechnung D1", key="D1"),
    row(10700, "9036", "9004", "8", "1003", "Bewirtung", key="D3"),
    row(3000, "9037", "9036", None, "1003", "Bewirtung", key="D3"),
]
GOLDEN_LINES = [
    HEADER_LINE,
    ";".join(COLUMN_HEADER),
    data_line('1190,00;"S";"EUR";;;;9003;9007;;1401;"D1";;;"Rechnung D1"', "0"),
    data_line('107,00;"S";"EUR";;;;9036;9004;8;1003;"D3";;;"Bewirtung"', "0"),
    data_line('30,00;"S";"EUR";;;;9037;9036;;1003;"D3";;;"Bewirtung"', "0"),
]


def test_golden_file_is_exact_bytes() -> None:
    expected = "".join(line + "\r\n" for line in GOLDEN_LINES).encode("cp1252")
    assert extf_csv(GOLDEN_ROWS, PARAMS) == expected


def test_golden_structure_counts() -> None:
    lines = extf_csv(GOLDEN_ROWS, PARAMS).decode("cp1252").split("\r\n")
    assert lines[-1] == ""
    assert len(lines) == 6
    assert lines[0].count(";") + 1 == 31
    assert len(COLUMN_HEADER) == 125 and COLUMN_HEADER[113] == "Festschreibung"
    assert lines[1].startswith(COLUMN_LINE + ";Postensperre;")
    for data in lines[2:5]:
        assert data.count(";") + 1 == 125


def test_header_follows_the_rules_file_and_locked_flag() -> None:
    assert (RULES.header.extf_version, RULES.header.category, RULES.header.format_version) == (700, 21, 13)
    locked = [row(100, "9001", "9003", None, "0101", "x", festschreibung=True)]
    first = extf_csv(locked, PARAMS).decode("cp1252").split("\r\n")[0]
    assert first == HEADER_LINE.replace(';"Q1 2026";"";1;0;0;', ';"Q1 2026";"";1;0;1;')
    assert extf_csv([], PARAMS).decode("cp1252").count("\r\n") == 2


def header_flag(rows: list[DatevRow]) -> str:
    return extf_csv(rows, PARAMS).decode("cp1252").split("\r\n")[0].split(";")[20]


def test_header_festschreibung_is_derived_from_rows() -> None:
    on = row(100, "9001", "9003", None, "0101", "x", festschreibung=True)
    off = row(100, "9001", "9003", None, "0101", "x")
    assert header_flag([on, on]) == "1"
    assert header_flag([off, off]) == "0"
    assert header_flag([]) == "0"
    with pytest.raises(ValueError, match="separate"):
        extf_csv([on, off], PARAMS)


def test_negative_amount_is_refused() -> None:
    with pytest.raises(ValueError, match="unsigned"):
        extf_csv([row(-1, "9001", "9003", None, "0101", "x")], PARAMS)


def text_field(text: str, **kw: str) -> str:
    r = row(100, "9001", "9003", None, "0101", text, **kw)
    return extf_csv([r], PARAMS).decode("cp1252").split("\r\n")[2].split(";")[13]


@pytest.mark.parametrize("ch", ["\t", "\x00", "\x85", "\u2028", "\u2029", "\x0b", "\x7f"])
def test_control_and_separator_characters_become_a_space(ch: str) -> None:
    assert text_field(f"a{ch}b") == '"a b"'


def test_formula_characters_stay_raw_inside_quotes() -> None:
    assert text_field("=1+1") == '"=1+1"'
    assert text_field("@x") == '"@x"'


def test_label_is_cut_to_30_and_quotes_doubled() -> None:
    p = DatevHeaderParams(**{**PARAMS.__dict__, "label": 'x"' * 20})
    fields = extf_csv([], p).decode("cp1252").split("\r\n")[0].split(";")
    assert fields[16] == '"' + 'x""' * 15 + '"'


def test_c1_control_and_emoji_are_replaced_and_reported() -> None:
    r = row(100, "9001", "9003", None, "0101", "a\x81b\U0001f600c")
    assert text_field("a\x81b\U0001f600c") == '"a?b?c"'
    assert unencodable_characters([r], PARAMS) == ("\x81", "\U0001f600")
    chart = _chart()
    report = self_check_report([], [r], chart, RULES, RATES)
    assert "replaced by '?': \x81 \U0001f600" in report


def test_euro_sign_is_byte_0x80() -> None:
    out = extf_csv([row(100, "9001", "9003", None, "0101", "5 €")], PARAMS)
    assert b'"5 \x80"' in out
    assert unencodable_characters([row(100, "9001", "9003", None, "0101", "5 €")], PARAMS) == ()


def test_festschreibung_column_is_one_for_locked_rows() -> None:
    locked = row(100, "9001", "9003", None, "0101", "x", festschreibung=True)
    line = extf_csv([locked], PARAMS).decode("cp1252").split("\r\n")[2]
    assert line == data_line('1,00;"S";"EUR";;;;9001;9003;;0101;"D";;;"x"', "1")


def test_special_characters_round_trip_and_quoting() -> None:
    text = 'Büro; "Größe" Straße'
    r = row(100, "9001", "9003", None, "0101", text, key="ä;ö")
    out = extf_csv([r], PARAMS)
    line = out.decode("cp1252").split("\r\n")[2]
    assert '"ä;ö";;;"Büro; ""Größe"" Straße"' in line
    assert 'Büro; "Größe" Straße'.encode("cp1252") in out.replace(b'""', b'"')
    assert unencodable_characters([r], PARAMS) == ()


def test_unencodable_characters_are_replaced_and_listed() -> None:
    r = row(100, "9001", "9003", None, "0101", "Kaffee ☕ 中")
    assert b'"Kaffee ? ?"' in extf_csv([r], PARAMS)
    assert unencodable_characters([r], PARAMS) == ("☕", "中")


def test_text_is_cut_and_line_breaks_removed() -> None:
    r = row(100, "9001", "9003", None, "0101", "a\r\nb\nc" + "x" * 80, key="k" * 50)
    line = extf_csv([r], PARAMS).decode("cp1252").split("\r\n")[2]
    assert '"' + "k" * 36 + '"' in line and "k" * 37 not in line
    assert '"a b c' + "x" * 55 + '"' in line
    assert len(line.split("\r\n")) == 1


@pytest.mark.parametrize(
    ("cents", "text"),
    [
        (1, "0,01"),
        (5, "0,05"),
        (100, "1,00"),
        (123450, "1234,50"),
        (1234567890, "12345678,90"),
        (29, "0,29"),
        (1000000, "10000,00"),
    ],
)
def test_amount_formatting(cents: int, text: str) -> None:
    line = extf_csv([row(cents, "9001", "9003", None, "0101", "x")], PARAMS).decode("cp1252").split("\r\n")[2]
    assert line.split(";")[0] == text
    assert "." not in line.split(";")[0]


def test_zip_names_and_determinism() -> None:
    csv = extf_csv(GOLDEN_ROWS, PARAMS)
    first = datev_zip(csv, "report\n", date(2026, 1, 1), date(2026, 3, 31))
    second = datev_zip(csv, "report\n", date(2026, 1, 1), date(2026, 3, 31))
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as zf:
        assert zf.namelist() == ["EXTF_Buchungsstapel_20260101_20260331.csv", "export-report.txt"]
        assert zf.read("EXTF_Buchungsstapel_20260101_20260331.csv") == csv
        assert zf.read("export-report.txt") == b"report\n"
        assert {i.date_time for i in zf.infolist()} == {(1980, 1, 1, 0, 0, 0)}
    assert extf_filename(date(2026, 1, 1), date(2026, 3, 31)) == "EXTF_Buchungsstapel_20260101_20260331.csv"


def test_report_for_d1_lists_balances_and_ok() -> None:
    chart = _chart()
    entries = case_entries("A1-accrual-soll")
    rows = batch_rows(entries, chart, RULES, RATES)
    report = self_check_report(entries, rows, chart, RULES, RATES)
    assert "Entries: 2\nRows: 2\nSum of row amounts: 2380,00\n" in report
    assert "Result: OK" in report
    assert "9007;-1000,00;-1000,00;0,00" in report
    assert "9013;-190,00;-190,00;0,00" in report
    assert "Rows without Festschreibung (unlocked months): 2" in report
    assert "Known differences (not bugs):" in report and "Kz 21 under EUeR" in report
    assert report.endswith("\n") and "Windows-1252" not in report


def test_report_flags_a_row_that_breaks_the_simulation() -> None:
    chart = _chart()
    entries = case_entries("A1-accrual-soll")
    rows = batch_rows(entries, chart, RULES, RATES)
    broken = [*rows, row(1, "9001", "9003", None, "0302", "x")]
    report = self_check_report(entries, broken, chart, RULES, RATES)
    assert "Result: DIFFERENCES (bug): 9001, 9003" in report
    assert "9001;1190,00;1190,01;0,01" in report
    assert "replaced by" not in report


def test_report_lists_unencodable_characters_after_cutting() -> None:
    chart = _chart()
    inside = row(100, "9001", "9003", None, "0101", "Kaffee ☕")
    beyond = row(100, "9001", "9003", None, "0101", "x" * 60 + "中")
    report = self_check_report([], [inside, beyond], chart, RULES, RATES)
    assert "replaced by '?': ☕" in report
    assert "中" not in report


def test_report_includes_label_characters_with_header_params() -> None:
    p = DatevHeaderParams(**{**PARAMS.__dict__, "label": "Q1 中"})
    report = self_check_report([], [], _chart(), RULES, RATES, header_params=p)
    assert "replaced by '?': 中" in report


def test_report_lists_datev_transfer_rows_as_known_differences() -> None:
    chart = _chart()
    entries = case_entries("A1-accrual-soll")
    rows = batch_rows(entries, chart, RULES, RATES)
    flagged = [replace(rows[0], note="transfer", event_key="payment:T1"), replace(rows[1], note="fallback")]
    report = self_check_report(entries, [*flagged, *rows[2:]], chart, RULES, RATES)
    assert "- transfer: payment:T1: booked through datev_transfer" in report
    assert f"- fallback: {rows[1].event_key}: unfolded VAT rows" in report
