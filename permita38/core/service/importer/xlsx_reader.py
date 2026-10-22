"""A small, self-contained XLSX reader (zipfile + ElementTree, no third-party packages).

Reads the cached values of the first (or named) worksheet. Formulas are never evaluated: only the stored result
(``<v>``) is used, and the formula text is ignored. Dates are returned as Excel serial numbers (floats).
Shared strings, inline strings, booleans and errors are supported. Documents with a DTD or entity declarations are
refused (entity-expansion attacks).
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_RNS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MAX_ENTRIES = 500
MAX_UNPACKED = 200 * 1024 * 1024
MAX_RATIO = 100


class XlsxError(ValueError):
    """The file is not a readable XLSX workbook (message is user-facing)."""


def open_guarded_zip(data: bytes) -> zipfile.ZipFile:
    """Open an archive after checking entry count, total unpacked size and compression ratio (zip bombs)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise XlsxError("The file is not a valid zip-based file") from None
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise XlsxError("The archive contains too many files")
    if sum(i.file_size for i in infos) > MAX_UNPACKED:
        raise XlsxError("The archive is too large when unpacked")
    for i in infos:
        if i.file_size > 1024 * 1024 and i.compress_size and i.file_size / i.compress_size > MAX_RATIO:
            raise XlsxError("The archive is suspiciously compressed")
    return zf


def _read_part(zf: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        raw = zf.read(name)
    except KeyError:
        return None
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise XlsxError("The workbook contains a DTD or entity declaration and was refused")
    return ET.fromstring(raw)


def _text(el: ET.Element) -> str:
    return "".join(t.text or "" for t in el.iter(f"{NS}t"))


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref)
    if not letters:
        raise XlsxError("bad cell reference")
    n = 0
    for ch in letters.group(0):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _sheet_target(zf: zipfile.ZipFile, sheet: str | None) -> tuple[str, str]:
    wb = _read_part(zf, "xl/workbook.xml")
    rels = _read_part(zf, "xl/_rels/workbook.xml.rels")
    if wb is None or rels is None:
        raise XlsxError("The workbook has no sheets")
    targets = {r.get("Id"): r.get("Target", "") for r in rels.iter(f"{PKG_RNS}Relationship")}
    sheets = wb.findall(f"{NS}sheets/{NS}sheet")
    if not sheets:
        raise XlsxError("The workbook has no sheets")
    chosen = sheets[0]
    if sheet is not None:
        match = [s for s in sheets if s.get("name") == sheet]
        if not match:
            raise XlsxError(f"No sheet named {sheet!r}")
        chosen = match[0]
    target = targets.get(chosen.get(f"{RNS}id"), "")
    target = target.lstrip("/")
    if not target.startswith("xl/"):
        target = "xl/" + target
    return chosen.get("name", ""), target


def read_xlsx(data: bytes, sheet: str | None = None) -> tuple[str, list[list[Any]]]:
    """Return (sheet name, rows); each row is a list of cell values (str, float, bool or None)."""
    zf = open_guarded_zip(data)
    name, target = _sheet_target(zf, sheet)
    shared: list[str] = []
    sst = _read_part(zf, "xl/sharedStrings.xml")
    if sst is not None:
        shared = [_text(si) for si in sst.findall(f"{NS}si")]
    root = _read_part(zf, target)
    if root is None:
        raise XlsxError("The sheet could not be found in the workbook")
    rows: list[list[Any]] = []
    for row in root.iter(f"{NS}row"):
        cells: list[Any] = []
        for c in row.findall(f"{NS}c"):
            idx = _col_index(c.get("r", "A1"))
            while len(cells) < idx:
                cells.append(None)
            cells.append(_cell_value(c, shared))
        while cells and cells[-1] is None:
            cells.pop()
        rows.append(cells)
    while rows and not rows[-1]:
        rows.pop()
    return name, rows


def _cell_value(c: ET.Element, shared: list[str]) -> Any:
    kind = c.get("t", "n")
    if kind == "inlineStr":
        el = c.find(f"{NS}is")
        return _text(el) if el is not None else None
    v = c.find(f"{NS}v")  # <f> (the formula) is deliberately ignored
    if v is None or v.text is None:
        return None
    raw = v.text
    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return None
    if kind in ("str", "d"):
        return raw
    if kind == "b":
        return raw.strip() in ("1", "true", "TRUE")
    if kind == "e":
        return None
    try:
        return float(raw)
    except ValueError:
        return None
