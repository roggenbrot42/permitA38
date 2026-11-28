"""Pure CSV writers for the ZM (docs/specs/zm.md "API"): the BZSt portal import file and a readable CSV.

No I/O, no floats: amounts are integer euros (``ZmLine.amount``) or integer cents (sum of the source lines).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from permita38.core.vat.pdf_common import _M, _W, BANNER, _clean, _Page, _text
from permita38.core.vat.zm import ZmLine, normalize_vat_id

__all__ = [
    "BZST_HEADER",
    "MAX_ROWS",
    "ZM_CSV_VERSION",
    "ZmExportError",
    "ZmExportRow",
    "bzst_csv",
    "normalize_vat_id",
    "readable_csv",
    "zm_pdf",
]

ZM_CSV_VERSION = "#v1.0"
BZST_HEADER = "Laenderkennzeichen,USt-IdNr.,Betrag(EUR),Art der Leistung"
MAX_ROWS = 1000
_VAT_ID = re.compile(r"[A-Z]{2}[A-Z0-9]{5,12}", re.ASCII)
ZM_FOOTER = "Not a filing. Enter or upload these figures in the BZSt online portal."
_READABLE_HEADER = "Land;USt-IdNr.;Betrag (EUR);Summe exakt (EUR);Art der Leistung"


@dataclass(frozen=True)
class ZmExportRow:
    """One offending row of an export error: the VAT id as given (``""`` for a whole-file problem) and why."""

    vat_id: str
    reason: str


class ZmExportError(ValueError):
    """The list cannot be exported; ``rows`` names the offending rows (for a 422 response)."""

    def __init__(self, rows: Sequence[ZmExportRow]) -> None:
        self.rows: tuple[ZmExportRow, ...] = tuple(rows)
        super().__init__("; ".join(f"{r.vat_id or '-'}: {r.reason}" for r in self.rows))


def _truncate(cents: int) -> int:
    """Whole euros, toward zero (the only ``base_rounding`` mode of the rules data)."""
    return -(-cents // 100) if cents < 0 else cents // 100


def _aggregate(lines: Sequence[ZmLine]) -> dict[str, tuple[int, int]]:
    """Per normalised VAT id (country + id): (whole euros, exact cents of the source lines), in first-seen order."""
    bad: list[ZmExportRow] = []
    exact: dict[str, int] = {}
    for ln in lines:
        vid = normalize_vat_id(ln.vat_id)
        if not vid.isascii() or not _VAT_ID.fullmatch(vid):
            bad.append(ZmExportRow(ln.vat_id, "VAT id does not match the pattern"))
            continue
        exact[vid] = exact.get(vid, 0) + sum(int(s.tax_base_cents or 0) for s in ln.source)
    if bad:
        raise ZmExportError(bad)
    return {vid: (_truncate(c), c) for vid, c in exact.items()}


def bzst_csv(lines: Sequence[ZmLine], *, correction: bool) -> bytes:
    """The BZSt portal import file: ``#v1.0``, skipped header, ``country,id,euros,S`` rows; ISO-8859-15, every line (the last too) ends with CRLF.

    Rows are aggregated per country + VAT id. Rows of 0 EUR are omitted in a first report and written in a
    correction. Raises ``ZmExportError`` for a malformed VAT id, no row to write (also in a correction) or more than 1000 rows.
    """
    rows = _aggregate(lines)
    if not correction:
        rows = {k: v for k, v in rows.items() if v[0] != 0}
    if not rows:
        raise ZmExportError([ZmExportRow("", "no rows to report")])
    if len(rows) > MAX_ROWS:
        raise ZmExportError([ZmExportRow("", f"{len(rows)} rows, at most {MAX_ROWS} allowed")])
    out = [ZM_CSV_VERSION, BZST_HEADER]
    out.extend(f"{vid[:2]},{vid[2:]},{euros},S" for vid, (euros, _) in rows.items())
    return ("\r\n".join(out) + "\r\n").encode("iso-8859-15")


def _decimal_comma(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole},{frac:02d}"


def readable_csv(lines: Sequence[ZmLine], *, correction: bool) -> bytes:
    """A human-readable CSV: UTF-8 with BOM, ``;``, decimal comma, the whole-euro amount and the exact cent sum.

    Same aggregation (exact cents summed per id, truncated once) and 0-row rule as :func:`bzst_csv`, but an empty list is allowed and no row limit applies.
    """
    rows = _aggregate(lines)
    if not correction:
        rows = {k: v for k, v in rows.items() if v[0] != 0}
    out = [_READABLE_HEADER]
    for vid, (euros, exact) in rows.items():
        out.append(f"{vid[:2]};{vid[2:]};{_decimal_comma(euros * 100)};{_decimal_comma(exact)};S")
    return ("\r\n".join(out) + "\r\n").encode("utf-8-sig")


_COLS = [(0.0, 40.0), (40.0, 180.0), (220.0, 90.0), (310.0, 40.0)]


def zm_pdf(report: Mapping[str, Any], *, business: str = "", unconfirmed: bool = True) -> bytes:
    """The ZM as an A4 PDF: header, the list (country, VAT id, whole euros, kind), the adviser banner while the rules
    are unconfirmed and the footer ``ZM_FOOTER`` on every page. ``report`` is the report as the API shows it."""
    p = _Page()
    pdf = p.pdf
    correction = bool(report.get("amends_id"))
    pdf.text(
        _M, 50, "EC sales list (Zusammenfassende Meldung)" + (" - correction" if correction else ""), size=15, bold=True
    )
    p.y = 72
    if unconfirmed:
        p.line(BANNER, size=10, bold=True, color="#b00020")
    p.line(f"Business: {business}", gap=13)
    p.line(f"Quarter: {_text(report.get('year'))} Q{_text(report.get('quarter'))}", gap=13)
    p.line(f"Period: {_text(report.get('from_date'))} to {_text(report.get('to_date'))}", gap=13)
    p.line(f"Status: {_text(report.get('status'))}   Due date: {_text(report.get('due_date'))}", gap=13)
    p.y += 8
    heads = ["Country", "VAT id", "Amount (EUR)", "Kind"]
    p.row(
        [(x, w, h, "r" if i == 2 else "l") for i, ((x, w), h) in enumerate(zip(_COLS, heads, strict=True))], bold=True
    )
    pdf.line(_M, p.y - 9, _M + _W, p.y - 9)
    total = 0
    rows = [x for x in report.get("lines") or [] if isinstance(x, Mapping)]
    for x in rows:
        vid = normalize_vat_id(str(x.get("vat_id") or ""))
        euros = int(Decimal(str(x.get("amount") or 0)))
        total += euros
        cells = [_text(x.get("country") or vid[:2]), vid[2:], str(euros), _text(x.get("kind") or "S")]
        p.row([(c[0], c[1], t, "r" if i == 2 else "l") for i, (c, t) in enumerate(zip(_COLS, cells, strict=True))])
    if not rows:
        p.line("No rows.", size=9, gap=12)
    p.y += 4
    p.line(f"Total: {total} EUR in {len(rows)} rows", bold=True)
    for page in pdf.pages:
        pdf.cur = page
        pdf.text(_M, 810, ZM_FOOTER, size=8)
        pdf.text(_M + _W, 810, f"Rules: {_clean(report.get('rules_version'))}", size=8, align="r")
    return bytes(pdf.output())
