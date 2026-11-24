"""Exports of a VAT return (docs/specs/ustva.md "API", paragraph Exports): CSV, JSON and PDF. Pure functions on the
return as the API shows it (``models.VatReturn`` dumped to JSON) and the source lines per box (the ``lines`` of
``VatReturnService.lines``). No ELSTER XML: the figures are entered by hand in Mein ELSTER."""

import csv
import io
import re
from collections.abc import Mapping, Sequence
from typing import Any

from permita38.core import pdf as _pdf
from permita38.core.service.transfer.csvout import BOM, safe_text
from permita38.core.vat_rules import VatRules, VatRulesMissing, vat_rules_for

# core/pdf.py is untyped; the strict module settings of core/vat/* would reject every call into it
Pdf: Any = _pdf.Pdf
text_width: Any = _pdf.text_width

Lines = Mapping[str, Sequence[Mapping[str, Any]]]

BOX_COLUMNS = ["kz", "label", "base", "tax"]
LINE_COLUMNS = ["kz", "entry_no", "booking_date", "document", "base", "tax"]
FOOTER = "Not a filing. Enter these figures in Mein ELSTER."
BANNER = "Not confirmed by a tax adviser: the rules of this form year are still to be verified."
MAX_PDF_LINES = 400  # per return; the rest is in the lines CSV

_M = 50.0
_W = 495.0
_BOTTOM = 770.0
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _comma(value: object) -> str:
    """A decimal string (``-5.70``) with a decimal comma; None is empty."""
    return "" if value is None else str(value).replace(".", ",")


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _csv(columns: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    buf.write(BOM)
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(columns)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _rules(year: int) -> VatRules | None:
    try:
        return vat_rules_for(year)
    except VatRulesMissing:
        return None


def rules_unconfirmed(year: int) -> bool:
    """True while the year's VAT table is ``to_verify`` (the adviser banner) or missing."""
    rules = _rules(year)
    return rules is None or rules.status == "to_verify"


def _labels(year: int) -> dict[str, tuple[str, int]]:
    rules = _rules(year)
    return {b.kz: (b.label_en, i) for i, b in enumerate(rules.boxes)} if rules else {}


def _ordered(ret: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    order = _labels(int(ret["year"]))
    boxes = ret.get("boxes") or {}
    return sorted(boxes.items(), key=lambda kv: (order.get(kv[0], ("", 10_000))[1], kv[0]))


def _box_cells(box: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(base, tax) of a box; an amount box (input VAT, result) has its figure in the tax column."""
    tax = box.get("tax") if box.get("tax") is not None else box.get("amount")
    return box.get("base"), tax


def return_csv(ret: Mapping[str, Any], lines: Lines | None = None) -> bytes:
    """``kz;label;base;tax`` per box, or with ``lines`` the file ``export-lines.csv``
    (``kz;entry_no;booking_date;document;base;tax``). UTF-8 with BOM, ``;``, CRLF, decimal comma; free text goes
    through the formula-injection guard of ``transfer/csvout``."""
    if lines is not None:
        rows = [
            [
                kz,
                _text(ln.get("entry_no")),
                _text(ln.get("booking_date")),
                safe_text(_text(ln.get("document"))),
                _comma(ln.get("base")),
                _comma(ln.get("tax")),
            ]
            for kz, group in lines.items()
            for ln in group
        ]
        return _csv(LINE_COLUMNS, rows)
    labels = _labels(int(ret["year"]))
    box_rows = []
    for kz, box in _ordered(ret):
        base, tax = _box_cells(box)
        box_rows.append([kz, safe_text(labels.get(kz, ("", 0))[0]), _comma(base), _comma(tax)])
    return _csv(BOX_COLUMNS, box_rows)


def return_json(ret: Mapping[str, Any]) -> dict[str, Any]:
    """The return as the API returns it (the route serialises it)."""
    return dict(ret)


# ------------------------------------------------------------------ PDF
def _clean(value: object) -> str:
    return _CTRL.sub(" ", _text(value))


def _fit(text: str, width: float, size: float, bold: bool = False) -> str:
    if text_width(text, size, bold) <= width:
        return text
    while text and text_width(text + "...", size, bold) > width:
        text = text[:-1]
    return text + "..."


class _Page:
    def __init__(self) -> None:
        self.pdf = Pdf()
        self.y = 50.0

    def ensure(self, need: float) -> None:
        if self.y + need > _BOTTOM:
            self.pdf.new_page()
            self.y = 50.0

    def line(self, text: str, size: float = 10, bold: bool = False, gap: float = 14, color: str = "#000000") -> None:
        for part in self.pdf.wrap(_clean(text), _W, size, bold):
            self.ensure(gap)
            self.pdf.text(_M, self.y, part, size=size, bold=bold, color=color)
            self.y += gap

    def row(self, cells: list[tuple[float, float, str, str]], size: float = 9, bold: bool = False) -> None:
        self.ensure(12.0)
        for x, width, text, align in cells:
            cut = _fit(_clean(text), width - 4, size, bold)
            if align == "r":
                self.pdf.text(_M + x + width - 2, self.y, cut, size=size, bold=bold, align="r")
            else:
                self.pdf.text(_M + x + 2, self.y, cut, size=size, bold=bold)
        self.y += 12.0


_BOX_COLS = [(0.0, 34.0), (34.0, 231.0), (265.0, 70.0), (335.0, 70.0), (405.0, 90.0)]
_LINE_COLS = [(0.0, 34.0), (34.0, 40.0), (74.0, 70.0), (144.0, 191.0), (335.0, 70.0), (405.0, 90.0)]


def return_pdf(
    ret: Mapping[str, Any],
    lines: Lines,
    *,
    business: str = "",
    tax_number: str = "",
    unconfirmed: bool = True,
) -> bytes:
    """The return as an A4 PDF: header, box table (form and booked figures, rounding line), the adviser banner
    while the rules are unconfirmed, the source lines per box, the footer and the rules version on every page."""
    labels = _labels(int(ret["year"]))
    p = _Page()
    pdf = p.pdf
    pdf.text(_M, 50, "VAT return (Umsatzsteuer-Voranmeldung)", size=15, bold=True)
    p.y = 72
    if unconfirmed:
        p.line(BANNER, size=10, bold=True, color="#b00020")
    period = f"{_text(ret.get('from_date'))} to {_text(ret.get('to_date'))}"
    p.line(f"Business: {business}", gap=13)
    p.line(f"Tax number: {tax_number}", gap=13)
    p.line(f"Period: {period} ({_text(ret.get('period_kind'))} {_text(ret.get('period_no'))})", gap=13)
    p.line(f"Basis: {_text(ret.get('basis'))}   Status: {_text(ret.get('status'))}", gap=13)
    p.line(f"Due date: {_text(ret.get('due_date'))}", gap=13)
    p.y += 8

    heads = ["Kz", "Box", "Base", "Tax", "Booked tax"]
    p.row(
        [(x, w, h, "l" if i < 2 else "r") for i, ((x, w), h) in enumerate(zip(_BOX_COLS, heads, strict=True))],
        bold=True,
    )
    pdf.line(_M, p.y - 9, _M + _W, p.y - 9)
    c = _BOX_COLS
    for kz, box in _ordered(ret):
        base, tax = _box_cells(box)
        booked = box.get("booked_tax")
        parts = pdf.wrap(_clean(labels.get(kz, ("", 0))[0]), c[1][1] - 4, 9) or [""]
        p.row(
            [(c[0][0], c[0][1], kz, "l"), (c[1][0], c[1][1], parts[0], "l"), (c[2][0], c[2][1], _text(base), "r"),
             (c[3][0], c[3][1], _text(tax), "r"), (c[4][0], c[4][1], _text(booked), "r")]
        )  # fmt: skip
        for part in parts[1:]:
            p.row([(c[1][0], c[1][1], part, "l")])
    rounding = ret.get("rounding_difference")
    p.y += 4
    p.line(f"Rounding difference (booked tax minus form tax): {_text(rounding) if rounding is not None else '0.00'}")
    if ret.get("result") is not None:
        p.line(f"Remaining prepayment / refund (Kz 83): {_text(ret.get('result'))}", bold=True)

    p.y += 8
    p.line("Source lines per box", size=11, bold=True, gap=16)
    printed = 0
    total = sum(len(g) for g in lines.values())
    lc = _LINE_COLS
    for kz, group in lines.items():
        if not group:
            continue
        p.line(f"Kz {kz}", size=9, bold=True, gap=12)
        for ln in group:
            if printed >= MAX_PDF_LINES:
                break
            p.row(
                [(lc[0][0], lc[0][1], kz, "l"), (lc[1][0], lc[1][1], _text(ln.get("entry_no")), "l"),
                 (lc[2][0], lc[2][1], _text(ln.get("booking_date")), "l"),
                 (lc[3][0], lc[3][1], _text(ln.get("document")), "l"),
                 (lc[4][0], lc[4][1], _text(ln.get("base")), "r"), (lc[5][0], lc[5][1], _text(ln.get("tax")), "r")],
                size=8,
            )  # fmt: skip
            printed += 1
    if total == 0:
        p.line("No source lines.", size=9, gap=12)
    elif printed < total:
        p.line(f"{total - printed} more lines: see the lines CSV (export.csv?lines=1).", size=9, gap=12)

    for page in pdf.pages:  # footer and rules version on every page
        pdf.cur = page
        pdf.text(_M, 810, FOOTER, size=8)
        pdf.text(_M + _W, 810, f"Rules: {_clean(ret.get('rules_version'))}", size=8, align="r")
    return bytes(pdf.output())
