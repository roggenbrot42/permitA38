"""ZM CSV writers (docs/specs/zm.md "API"); golden files are written by hand."""

from datetime import date

import pytest

from permita38.core.accounting.types import Cents, Line, Role
from permita38.core.vat.zm import ZmLine, normalize_vat_id
from permita38.core.vat.zm_export import ZmExportError, bzst_csv, readable_csv


def _src(cents: int) -> tuple[Line, ...]:
    return (
        Line(
            role=Role("REVENUE"),
            amount_cents=Cents(0),
            tax_key="EU_SVC",
            tax_kind="base_only",
            tax_base_cents=Cents(cents),
            vat_date=date(2026, 1, 15),
        ),
    )


def _z(vat_id: str, amount: int, cents: int | None = None) -> ZmLine:
    return ZmLine(vat_id, vat_id[:2], amount, "S", _src(amount * 100 if cents is None else cents))


LINES = [
    _z("ATU12345678", 3001, 300110),
    _z("EL123456789", -500),
    _z("FR12345678901", 2000, 200070),
    _z("DE1234567", 0, -99),
]


def test_bzst_first_report_golden() -> None:
    expected = (
        b"#v1.0\r\n"
        b"Laenderkennzeichen,USt-IdNr.,Betrag(EUR),Art der Leistung\r\n"
        b"AT,U12345678,3001,S\r\n"
        b"EL,123456789,-500,S\r\n"
        b"FR,12345678901,2000,S\r\n"
    )
    assert bzst_csv(LINES, correction=False) == expected


def test_bzst_correction_writes_zero_row() -> None:
    expected = (
        b"#v1.0\r\n"
        b"Laenderkennzeichen,USt-IdNr.,Betrag(EUR),Art der Leistung\r\n"
        b"AT,U12345678,3001,S\r\n"
        b"EL,123456789,-500,S\r\n"
        b"FR,12345678901,2000,S\r\n"
        b"DE,1234567,0,S\r\n"
    )
    assert bzst_csv(LINES, correction=True) == expected


def test_bzst_aggregates_duplicates_and_normalises() -> None:
    lines = [_z("fr 123.456.789 01", 10), _z("FR12345678901", 5)]
    assert bzst_csv(lines, correction=False).endswith(b"\r\nFR,12345678901,15,S\r\n")
    assert bzst_csv(lines, correction=False).count(b"FR,") == 1


def test_bzst_encoding_and_line_ends() -> None:
    data = bzst_csv(LINES, correction=False)
    assert data.endswith(b"\r\n")
    assert data.count(b"\r\n") == len(data.splitlines()) == 5
    assert data.count(b"\n") == 5  # no bare LF
    data.decode("iso-8859-15")


def test_bzst_too_many_rows() -> None:
    lines = [_z(f"AT{n:08d}", 1) for n in range(1001)]
    with pytest.raises(ZmExportError) as exc:
        bzst_csv(lines, correction=False)
    assert "1001" in exc.value.rows[0].reason
    assert len(bzst_csv(lines[:1000], correction=False).split(b"\r\n")) == 1003


def test_bzst_malformed_vat_id_lists_row() -> None:
    lines = [_z("ATU12345678", 1), _z("X1234", 2), _z("FR1234", 3)]
    with pytest.raises(ZmExportError) as exc:
        bzst_csv(lines, correction=False)
    assert [r.vat_id for r in exc.value.rows] == ["X1234", "FR1234"]


def test_bzst_empty_first_report_is_error() -> None:
    with pytest.raises(ZmExportError):
        bzst_csv([], correction=False)
    with pytest.raises(ZmExportError):
        bzst_csv([_z("DE1234567", 0, -99)], correction=False)


def test_readable_csv_golden() -> None:
    expected = (
        "﻿Land;USt-IdNr.;Betrag (EUR);Summe exakt (EUR);Art der Leistung\r\n"
        "AT;U12345678;3001,00;3001,10;S\r\n"
        "EL;123456789;-500,00;-500,00;S\r\n"
        "FR;12345678901;2000,00;2000,70;S\r\n"
        "DE;1234567;0,00;-0,99;S\r\n"
    ).encode()
    assert readable_csv(LINES, correction=True) == expected


def test_bzst_empty_correction_is_error() -> None:
    with pytest.raises(ZmExportError):
        bzst_csv([], correction=True)


def test_bzst_two_sixty_cent_lines_make_one_euro() -> None:
    lines = [_z("DE1234567", 0, 60), _z("de1234567", 0, 60)]
    assert bzst_csv(lines, correction=False).endswith(b"DE,1234567,1,S\r\n")


def test_bzst_greece_gr_becomes_el() -> None:
    assert bzst_csv([_z("GR123456789", 7)], correction=False).endswith(b"\r\nEL,123456789,7,S\r\n")


def test_normalise_leaves_valid_id_unchanged_and_accepts_dashes() -> None:
    assert normalize_vat_id("ATU12345678") == "ATU12345678"
    assert bzst_csv([_z("AT-U1234.5678", 4)], correction=False).endswith(b"AT,U12345678,4,S\r\n")


def test_non_ascii_vat_id_refused() -> None:
    with pytest.raises(ZmExportError) as exc:
        bzst_csv([_z("ATU1234567ß", 1)], correction=False)
    assert [r.vat_id for r in exc.value.rows] == ["ATU1234567ß"]
    with pytest.raises(ZmExportError):
        readable_csv([_z("ＡＴU12345678", 1)], correction=True)
