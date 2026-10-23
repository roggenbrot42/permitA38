"""Turning records into table cells (CSV and XLSX share this): exact decimals, locale, spreadsheet-injection guard."""

import csv
import io
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from permita38.core.money import dec, line_amount, q2

from .formats import DATE_FIELDS, LINE_COLUMNS, MONEY_FIELDS, TIMESTAMP_FIELDS, columns_for, split_extra

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class Locale:
    name: str
    delimiter: str
    decimal: str
    date_format: str  # strftime-like for ISO dates
    true: str
    false: str


ISO = Locale("iso", ",", ".", "%Y-%m-%d", "true", "false")
DE = Locale("de", ";", ",", "%d.%m.%Y", "ja", "nein")
LOCALES = {"iso": ISO, "de": DE}


def guard_formula(text: str) -> str:
    """CSV/XLSX injection: a text cell that a spreadsheet would run as a formula is prefixed with an apostrophe."""
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def money_text(value: Any, locale: Locale) -> str:
    d = q2(dec(value))
    return format(d, "f").replace(".", locale.decimal)


def number_text(value: float | int, locale: Locale) -> str:
    d = dec(value)
    text = format(d.normalize() if d != d.to_integral() else d.quantize(Decimal(1)), "f")
    return text.replace(".", locale.decimal)


def date_text(value: str, locale: Locale) -> str:
    try:
        return date.fromisoformat(value).strftime(locale.date_format)
    except ValueError:
        return value


def cell_text(coll: str, field: str, value: Any, locale: Locale, guard: bool = True) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return locale.true if value else locale.false
    if field in TIMESTAMP_FIELDS and isinstance(value, int):
        return str(value)
    if field in MONEY_FIELDS.get(coll, ()) and isinstance(value, (int, float)):
        return money_text(value, locale)
    if isinstance(value, (int, float)):
        return number_text(value, locale)
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return guard_formula(text) if guard else text
    text = str(value)
    if field in DATE_FIELDS.get(coll, ()):
        return date_text(text, locale)
    return guard_formula(text) if guard else text


def table_rows(coll: str, records: list[dict], include_secrets: bool) -> tuple[list[str], list[dict]]:
    """Column names and per-record dicts (with unknown fields folded into `extra`)."""
    cols = columns_for(coll, records, include_secrets)
    rows = []
    for rec in records:
        base, extra = split_extra(coll, rec)
        row = {c: base.get(c) for c in cols if c != "extra"}
        if "extra" in cols:
            row["extra"] = extra or None
        rows.append(row)
    return cols, rows


def line_rows(doc: dict) -> list[dict]:
    out = []
    for pos, ln in enumerate(doc.get("lines") or [], 1):
        qty, price = dec(ln.get("qty")), dec(ln.get("price"))
        out.append(
            {
                "document_id": doc.get("id"),
                "document_number": doc.get("number"),
                "position": pos,
                "description": ln.get("description"),
                "qty": qty,
                "unit": ln.get("unit"),
                "price": price,
                "vat": ln.get("vat") if ln.get("vat") is not None else doc.get("vat_rate"),
                "amount": line_amount(qty, price),
            }
        )
    return out


def line_cell_text(field: str, value: Any, locale: Locale) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return money_text(value, locale) if field in ("price", "amount") else number_text(value, locale)
    if isinstance(value, (int, float)):
        return number_text(value, locale)
    return guard_formula(str(value))


def csv_bytes(header: list[str], rows: list[list[str]], locale: Locale, bom: bool) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=locale.delimiter, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(header)
    w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def records_csv(coll: str, records: list[dict], locale: Locale, bom: bool, include_secrets: bool) -> bytes:
    cols, rows = table_rows(coll, records, include_secrets)
    body = [[cell_text(coll, c, r.get(c), locale) for c in cols] for r in rows]
    return csv_bytes(cols, body, locale, bom)


def lines_csv(docs: list[dict], locale: Locale, bom: bool) -> bytes:
    body = []
    for doc in docs:
        for row in line_rows(doc):
            body.append([line_cell_text(c, row[c], locale) for c in LINE_COLUMNS])
    return csv_bytes(LINE_COLUMNS, body, locale, bom)
