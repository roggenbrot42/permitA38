"""CSV for humans: UTF-8 with BOM, ``;``, CRLF, formula-injection safe. Never read back by the import."""

import csv
import io
import json
from collections.abc import Iterable, Iterator
from typing import Any

BOM = "﻿"
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_text(s: str) -> str:
    """Prefix a single quote when a spreadsheet would read the text as a formula."""
    return "'" + s if s.lstrip(" ")[:1] in _DANGEROUS and s.lstrip(" ") else s


def cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return safe_text(value)
    return safe_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def header(columns: list[str], records: list[dict]) -> list[str]:
    known = set(columns)
    extra = sorted({k for r in records for k in r if k not in known})
    return columns + extra


def csv_chunks(columns: list[str], records: list[dict]) -> Iterator[bytes]:
    """The file as byte chunks (BOM first)."""
    cols = header(columns, records)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    buf.write(BOM)
    w.writerow([safe_text(c) for c in cols])
    n = 0
    for r in records:
        w.writerow([cell(r.get(c)) for c in cols])
        n += 1
        if n % 500 == 0:
            yield buf.getvalue().encode("utf-8")
            buf.seek(0)
            buf.truncate()
    yield buf.getvalue().encode("utf-8")


def csv_bytes(columns: list[str], records: Iterable[dict]) -> bytes:
    return b"".join(csv_chunks(columns, list(records)))
