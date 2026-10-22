"""Reading the source file into a table of cells (CSV with dialect and encoding detection, XLSX, permitA38 JSON)."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any

from permita38.core.errors import ServiceError

from .xlsx_reader import XlsxError, read_xlsx

MAX_BYTES = 50 * 1024 * 1024
MAX_ROWS = 50_000
DELIMITERS = ";,\t|"


@dataclass
class Table:
    source: str  # "csv", "xlsx" or "json"
    headers: list[str]
    rows: list[list[Any]]
    info: dict = field(default_factory=dict)  # encoding, delimiter, sheet, ...


def check_size(data: bytes) -> None:
    if len(data) > MAX_BYTES:
        raise ServiceError(413, "The file is larger than 50 MB")


def decode_text(data: bytes) -> tuple[str, str]:
    """(text, encoding): UTF-8 with or without BOM, otherwise Windows-1252 (Latin-1 for undefined bytes)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def detect_delimiter(text: str) -> str:
    """The separator that appears the same number of times (at least once) on the first lines; ';' wins ties."""
    lines = [ln for ln in text.splitlines()[:20] if ln.strip()]
    if not lines:
        return ";"
    best, best_score = ";", -1
    for d in DELIMITERS:
        counts = [ln.count(d) for ln in lines]
        if min(counts) == 0:
            continue
        score = min(counts) * 10 - (max(counts) - min(counts))
        if score > best_score:
            best, best_score = d, score
    return best


def read_csv(data: bytes, delimiter: str | None = None) -> Table:
    text, encoding = decode_text(data)
    delim = delimiter or detect_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    rows = [[c.strip() for c in r] for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ServiceError(400, "The file is empty")
    headers, body = rows[0], rows[1:]
    if len(body) > MAX_ROWS:
        raise ServiceError(422, f"The file has more than {MAX_ROWS} rows")
    width = len(headers)
    body = [r + [""] * (width - len(r)) if len(r) < width else r for r in body]
    return Table("csv", headers, body, {"encoding": encoding, "delimiter": delim})


def read_json(data: bytes) -> Table:
    """A permitA38 JSON export: a list of records, or an object with one list per collection."""
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ServiceError(400, "The JSON file could not be read") from None
    if isinstance(obj, dict):
        lists = [v for v in obj.values() if isinstance(v, list)]
        obj = lists[0] if len(lists) == 1 else obj.get("records", [])
    if not isinstance(obj, list):
        raise ServiceError(400, "The JSON file must contain a list of records")
    if len(obj) > MAX_ROWS:
        raise ServiceError(422, f"The file has more than {MAX_ROWS} rows")
    records = [r for r in obj if isinstance(r, dict)]
    headers: list[str] = []
    for r in records:
        for k in r:
            if k not in headers:
                headers.append(k)
    rows = [[r.get(h) for h in headers] for r in records]
    return Table("json", headers, rows, {"encoding": "utf-8"})


def read_table(data: bytes, filename: str, sheet: str | None = None, delimiter: str | None = None) -> Table:
    check_size(data)
    name = (filename or "").lower()
    if name.endswith(".json"):
        return read_json(data)
    if name.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        try:
            sheet_name, rows = read_xlsx(data, sheet)
        except XlsxError as e:
            raise ServiceError(400, str(e)) from None
        if not rows:
            raise ServiceError(400, "The sheet is empty")
        headers = ["" if v is None else str(v).strip() for v in rows[0]]
        body = rows[1:]
        if len(body) > MAX_ROWS:
            raise ServiceError(422, f"The file has more than {MAX_ROWS} rows")
        width = len(headers)
        body = [r + [None] * (width - len(r)) if len(r) < width else r for r in body]
        return Table("xlsx", headers, body, {"sheet": sheet_name})
    return read_csv(data, delimiter)
