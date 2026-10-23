"""A small dependency-free XLSX (Office Open XML) writer.

Supports what the export needs: several sheets, typed cells (text, number, boolean, date, money with two
decimals), a bold frozen header row and sensible column widths. Strings are written inline (no shared string table),
text that a spreadsheet could execute as a formula is neutralised.
"""

import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .tabular import guard_formula

_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿\ud800-\udfff]")
_BAD_SHEET_CHARS = re.compile(r"[\[\]\*\?/\\:]")
_EXCEL_EPOCH = date(1899, 12, 30)
MAX_ROWS = 1_048_576
MAX_COLS = 16_384
MAX_CELL = 32_000  # Excel limit is 32,767 characters

# cell style indexes in styles.xml
S_DEFAULT, S_HEADER, S_MONEY, S_DATE, S_INT = 0, 1, 2, 3, 4


@dataclass
class Cell:
    value: Any
    kind: str = "auto"  # auto | text | number | money | date | bool | int


@dataclass
class Sheet:
    name: str
    header: list[str]
    rows: list[list[Cell | Any]] = field(default_factory=list)


def sheet_name(name: str, used: set[str]) -> str:
    base = _BAD_SHEET_CHARS.sub("_", name).strip("'")[:31] or "Sheet"
    candidate, n = base, 2
    while candidate.lower() in {u.lower() for u in used}:
        suffix = f"_{n}"
        candidate = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(candidate)
    return candidate


def clean_text(text: str) -> str:
    return _ILLEGAL_XML.sub("", text)[:MAX_CELL]


def col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def excel_serial(d: date | datetime) -> int:
    if isinstance(d, datetime):
        d = d.date()
    return (d - _EXCEL_EPOCH).days


def _cell_xml(ref: str, cell: Cell | Any) -> str:
    if not isinstance(cell, Cell):
        cell = Cell(cell)
    v, kind = cell.value, cell.kind
    if v is None or v == "":
        return ""
    if kind == "auto":
        if isinstance(v, bool):
            kind = "bool"
        elif isinstance(v, int):
            kind = "int"
        elif isinstance(v, (float, Decimal)):
            kind = "number"
        elif isinstance(v, (date, datetime)):
            kind = "date"
        else:
            kind = "text"
    if kind == "bool":
        return f'<c r="{ref}" t="b"><v>{1 if v else 0}</v></c>'
    if kind == "date":
        d = v if isinstance(v, (date, datetime)) else date.fromisoformat(str(v))
        return f'<c r="{ref}" s="{S_DATE}"><v>{excel_serial(d)}</v></c>'
    if kind in ("number", "money", "int"):
        dv = Decimal(str(v))
        style = S_MONEY if kind == "money" else S_INT if kind == "int" else S_DEFAULT
        text = format(dv.quantize(Decimal("0.01")) if kind == "money" else dv, "f")
        return f'<c r="{ref}" s="{style}"><v>{text}</v></c>'
    text = clean_text(guard_formula(str(v)))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _sheet_xml(sheet: Sheet) -> str:
    ncols = min(len(sheet.header), MAX_COLS)
    rows = sheet.rows[: MAX_ROWS - 1]
    widths = [max(8, min(60, len(str(h)) + 2)) for h in sheet.header[:ncols]]
    for r in rows[:500]:
        for i, c in enumerate(r[:ncols]):
            raw = c.value if isinstance(c, Cell) else c
            widths[i] = max(widths[i], min(60, len(str(raw)) + 2 if raw is not None else 0))
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
        'state="frozen"/><selection pane="bottomLeft"/></sheetView></sheetViews>',
        "<cols>"
        + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
        + "</cols>",
        "<sheetData>",
    ]
    head = "".join(
        f'<c r="{col_letter(i)}1" s="{S_HEADER}" t="inlineStr"><is><t>{escape(clean_text(str(h)))}</t></is></c>'
        for i, h in enumerate(sheet.header[:ncols])
    )
    parts.append(f'<row r="1">{head}</row>')
    for n, r in enumerate(rows, 2):
        cells = "".join(_cell_xml(f"{col_letter(i)}{n}", c) for i, c in enumerate(r[:ncols]))
        parts.append(f'<row r="{n}">{cells}</row>')
    parts.append("</sheetData>")
    if ncols:
        parts.append(f'<autoFilter ref="A1:{col_letter(ncols - 1)}{max(len(rows) + 1, 1)}"/>')
    parts.append("</worksheet>")
    return "".join(parts)


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="5">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="1" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)


def write_xlsx(path: Path, sheets: list[Sheet], title: str = "permitA38 export") -> list[str]:
    """Write the workbook; returns the final sheet names."""
    used: set[str] = set()
    names = [sheet_name(s.name, used) for s in sheets]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" '
            'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(len(sheets))
            )
            + "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
            'core-properties" Target="docProps/core.xml"/></Relationships>',
        )
        z.writestr(
            "docProps/core.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + escape(title) + "</dc:title>"
            "<dc:creator>permitA38</dc:creator></cp:coreProperties>",
        )
        z.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join(
                f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                for i, n in enumerate(names)
            )
            + "</sheets></workbook>",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                f'relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                for i in range(len(sheets))
            )
            + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/styles" Target="styles.xml"/></Relationships>',
        )
        z.writestr("xl/styles.xml", _STYLES)
        for i, s in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(s))
    return names
