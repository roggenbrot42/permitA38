"""DATEV EXTF file, self-check report and zip (docs/specs/datev-export.md, "File" and "Self-check").

Pure functions: no I/O, no clock, no float. :func:`extf_csv` writes the Buchungsstapel file (EXTF 700, category 21,
format version 13 from the rules file, Windows-1252, ``;`` separated, decimal comma, CRLF); :func:`self_check_report`
compares the journal with the DATEV simulation and lists the known differences; :func:`datev_zip` bundles both with
deterministic names and timestamps.
"""

import io
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from permita38.core.accounting.datev import DatevRow, journal_balances, known_differences, simulate
from permita38.core.accounting.types import Cents, Entry
from permita38.core.charts import Chart
from permita38.core.datev import DatevRules, ExtfHeader

__all__ = [
    "COLUMN_HEADER",
    "DatevHeaderParams",
    "datev_zip",
    "extf_csv",
    "extf_filename",
    "self_check_report",
    "unencodable_characters",
]

ENCODING = "cp1252"
REPORT_NAME = "export-report.txt"
_EOL = "\r\n"
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _beleginfo() -> list[str]:
    return [f"Beleginfo - {part} {n}" for n in range(1, 9) for part in ("Art", "Inhalt")]


def _zusatz() -> list[str]:
    names: list[str] = []
    for n in range(1, 21):
        names += [f"Zusatzinformation - Art {n}", f"Zusatzinformation- Inhalt {n}"]
    return names


COLUMN_HEADER: tuple[str, ...] = (
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
    "Postensperre",
    "Diverse Adressnummer",
    "Geschäftspartnerbank",
    "Sachverhalt",
    "Zinssperre",
    "Beleglink",
    *_beleginfo(),
    "KOST1 - Kostenstelle",
    "KOST2 - Kostenstelle",
    "Kost-Menge",
    "EU-Mitgliedstaat u. UStID (Bestimmung)",
    "EU-Steuersatz (Bestimmung)",
    "Abw. Versteuerungsart",
    "Sachverhalt L+L",
    "Funktionsergänzung L+L",
    "BU 49 Hauptfunktionstyp",
    "BU 49 Hauptfunktionsnummer",
    "BU 49 Funktionsergänzung",
    *_zusatz(),
    "Stück",
    "Gewicht",
    "Zahlweise",
    "Forderungsart",
    "Veranlagungsjahr",
    "Zugeordnete Fälligkeit",
    "Skontotyp",
    "Auftragsnummer",
    "Buchungstyp",
    "USt-Schlüssel (Anzahlungen)",
    "EU-Mitgliedstaat (Anzahlungen)",
    "Sachverhalt L+L (Anzahlungen)",
    "EU-Steuersatz (Anzahlungen)",
    "Erlöskonto (Anzahlungen)",
    "Herkunft-Kz",
    "Leerfeld",
    "KOST-Datum",
    "SEPA-Mandatsreferenz",
    "Skontosperre",
    "Gesellschaftername",
    "Beteiligtennummer",
    "Identifikationsnummer",
    "Zeichnernummer",
    "Postensperre bis",
    "Bezeichnung SoBil-Sachverhalt",
    "Kennzeichen SoBil-Buchung",
    "Festschreibung",
    "Leistungsdatum",
    "Datum Zuord.Steuerperiode",
    "Fälligkeit",
    "Generalumkehr (GU)",
    "Steuersatz",
    "Land",
    "Abrechnungsreferenz",
    "BVV-Position",
    "EU-Mitgliedstaat u. UStID (Ursprung)",
    "EU-Steuersatz (Ursprung)",
    "Abw. Skontokonto",
)
_COL = {name: i for i, name in enumerate(COLUMN_HEADER)}
_HEADER_FIELDS = 31


@dataclass(frozen=True)
class DatevHeaderParams:
    """Everything the EXTF header line needs besides the rules file's format numbers."""

    header: ExtfHeader
    created: datetime
    consultant_no: int
    client_no: int
    fiscal_year_start: date
    account_length: int
    date_from: date
    date_to: date
    label: str
    locked: bool = False


def _quote(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def _clean(text: str, limit: int) -> str:
    """Field text: line breaks become spaces and the text is cut to the field length."""
    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")[:limit]


def _comma(cents: int) -> str:
    """Whole cents as a decimal-comma amount without thousands separator, e.g. ``1234,50``."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole},{frac:02d}"


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _header_line(p: DatevHeaderParams) -> str:
    created = p.created.strftime("%Y%m%d%H%M%S") + f"{p.created.microsecond // 1000:03d}"
    fields = [""] * _HEADER_FIELDS
    values: dict[int, str] = {
        1: _quote("EXTF"),
        2: str(p.header.extf_version),
        3: str(p.header.category),
        4: _quote(p.header.category_name),
        5: str(p.header.format_version),
        6: created,
        8: _quote(""),
        9: _quote(""),
        10: _quote(""),
        11: str(p.consultant_no),
        12: str(p.client_no),
        13: _ymd(p.fiscal_year_start),
        14: str(p.account_length),
        15: _ymd(p.date_from),
        16: _ymd(p.date_to),
        17: _quote(_clean(p.label, 30)),
        18: _quote(""),
        19: "1",
        20: "0",
        21: "1" if p.locked else "0",
        22: _quote("EUR"),
    }
    for no, value in values.items():
        fields[no - 1] = value
    return ";".join(fields)


def _data_line(row: DatevRow) -> str:
    fields = [""] * len(COLUMN_HEADER)
    fields[_COL["Umsatz (ohne Soll/Haben-Kz)"]] = _comma(row.amount_cents)
    fields[_COL["Soll/Haben-Kennzeichen"]] = _quote(row.side)
    fields[_COL["WKZ Umsatz"]] = _quote("EUR")
    fields[_COL["Konto"]] = str(row.konto)
    fields[_COL["Gegenkonto (ohne BU-Schlüssel)"]] = str(row.gegenkonto)
    fields[_COL["BU-Schlüssel"]] = row.bu_key or ""
    fields[_COL["Belegdatum"]] = row.belegdatum
    fields[_COL["Belegfeld 1"]] = _quote(_clean(row.belegfeld1, 36))
    fields[_COL["Buchungstext"]] = _quote(_clean(row.text, 60))
    fields[_COL["Festschreibung"]] = "1" if row.festschreibung else "0"
    return ";".join(fields)


def _file_text(rows: Sequence[DatevRow], params: DatevHeaderParams) -> str:
    lines = [_header_line(params), ";".join(COLUMN_HEADER), *(_data_line(r) for r in rows)]
    return "".join(line + _EOL for line in lines)


def extf_csv(rows: Sequence[DatevRow], header_params: DatevHeaderParams) -> bytes:
    """The EXTF Buchungsstapel file; characters outside Windows-1252 are replaced by ``?``."""
    return _file_text(rows, header_params).encode(ENCODING, errors="replace")


def unencodable_characters(rows: Sequence[DatevRow], header_params: DatevHeaderParams) -> tuple[str, ...]:
    """The distinct characters :func:`extf_csv` had to replace, sorted (for the report)."""
    bad: set[str] = set()
    for ch in set(_file_text(rows, header_params)):
        try:
            ch.encode(ENCODING)
        except UnicodeEncodeError:
            bad.add(ch)
    return tuple(sorted(bad))


def extf_filename(date_from: date, date_to: date) -> str:
    return f"EXTF_Buchungsstapel_{_ymd(date_from)}_{_ymd(date_to)}.csv"


_BANNER = (
    "DATEV export of permitA38 - to be checked by your tax adviser.\n"
    "The VAT return of the app stays authoritative for what you file.\n"
)
_STANDING_DIFFERENCES = (
    "Kz 21 under EUeR: DATEV derives it from the revenue booking (payment date), the app from the performance date.",
    "EUeR clearing accounts: DATEV counts output VAT at issue and input VAT at the bill date and ignores "
    "vat_soll_clearing_* / input_vat_clearing; totals agree over a full cycle but differ at a year end with open items.",
    "Every datev_transfer row and every fallback row (unfolded VAT) is listed below.",
)


def self_check_report(
    entries: Sequence[Entry],
    rows: Sequence[DatevRow],
    chart: Chart,
    datev_rules: DatevRules,
    vat_rates: Mapping[str, Decimal],
    *,
    unencodable: Sequence[str] = (),
) -> str:
    """Counts, sums, per-account journal vs DATEV simulation and the known differences (``export-report.txt``)."""
    journal = journal_balances(entries, chart)
    simulated = simulate(rows, chart, datev_rules, vat_rates)
    accounts = sorted(set(journal) | set(simulated))
    zero = Cents(0)
    mismatches = [str(no) for no in accounts if journal.get(no, zero) != simulated.get(no, zero)]
    total = sum(r.amount_cents for r in rows)
    out = [_BANNER, f"Entries: {len(entries)}", f"Rows: {len(rows)}", f"Sum of row amounts: {_comma(total)}"]
    unlocked = sum(1 for r in rows if not r.festschreibung)
    if unlocked:
        out.append(f"Rows without Festschreibung (unlocked months): {unlocked}")
    out += ["", "Self-check: DATEV simulation against the journal per account (account; journal; DATEV; difference)"]
    for no in accounts:
        j, s = journal.get(no, zero), simulated.get(no, zero)
        out.append(f"{no};{_comma(j)};{_comma(s)};{_comma(s - j)}")
    out.append("Result: OK" if not mismatches else "Result: DIFFERENCES (bug): " + ", ".join(mismatches))
    out += ["", "Known differences (not bugs):", *(f"- {text}" for text in _STANDING_DIFFERENCES)]
    for diff in known_differences(entries, rows):
        out.append(f"- {diff.kind}: {diff.event_key}: {diff.detail}")
    if unencodable:
        out += ["", "Characters outside Windows-1252 replaced by '?': " + " ".join(unencodable)]
    return "\n".join(out) + "\n"


def datev_zip(csv: bytes, report: str, date_from: date, date_to: date) -> bytes:
    """Zip with the EXTF file and ``export-report.txt``; fixed names, timestamps and attributes (byte-identical)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in ((extf_filename(date_from, date_to), csv), (REPORT_NAME, report.encode("utf-8"))):
            info = zipfile.ZipInfo(name, _ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()
