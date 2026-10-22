"""Parsing of single cell values: dates, amounts, status, currency. Strict; every failure is a row error."""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from permita38.core.money import CENT, q2

MIN_DATE = date(2000, 1, 1)
MAX_DATE = date(2100, 12, 31)
MAX_AMOUNT = Decimal("1000000000")
EXCEL_EPOCH = date(1899, 12, 30)
ALLOWED_VAT_RATES = (0, 5, 7, 16, 19)

STATUS_MAP = {
    "draft": "draft", "entwurf": "draft", "offen draft": "draft",
    "sent": "sent", "versendet": "sent", "verschickt": "sent", "offen": "sent", "open": "sent", "unbezahlt": "sent",
    "paid": "paid", "bezahlt": "paid", "beglichen": "paid",
    "cancelled": "cancelled", "canceled": "cancelled", "storniert": "cancelled", "storno": "cancelled",
    "accepted": "accepted", "angenommen": "accepted",
    "rejected": "rejected", "abgelehnt": "rejected",
}  # fmt: skip
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$")
_DMY = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$")
_YMD_SLASH = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_DMY2 = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$")


class CellError(ValueError):
    """A cell could not be read; the message is shown next to the row."""


def is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def parse_date(value, date_order: str = "dmy") -> date | None:
    """ISO, German (dd.mm.yyyy), slash (dd/mm/yyyy; mm/dd/yyyy with date_order="mdy") or Excel serial. None if blank."""
    if is_blank(value):
        return None
    if isinstance(value, bool):
        raise CellError("is not a date")
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or not 1 <= value < 100000:
            raise CellError("is not a date")
        day = EXCEL_EPOCH + timedelta(days=int(value))
    else:
        text = str(value).strip()
        m = _ISO.match(text)
        if m:
            day = _make(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        elif m := _YMD_SLASH.match(text):
            day = _make(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        elif m := _DMY.match(text):
            a, b, y = (int(x) for x in m.groups())
            if date_order == "mdy":  # US order: month first
                day = _make(y, a, b)
            else:  # German order (default): day first
                day = _make(y, b, a)
        elif m := _DMY2.match(text):
            a, b, y = (int(x) for x in m.groups())
            day = _make(2000 + y, b, a)
        else:
            raise CellError("is not a date (use YYYY-MM-DD or DD.MM.YYYY)")
    if not MIN_DATE <= day <= MAX_DATE:
        raise CellError("is outside the range 2000 to 2100")
    return day


def _make(y: int, m: int, d: int) -> date:
    try:
        return date(y, m, d)
    except ValueError:
        raise CellError("is not a real calendar date") from None


def parse_amount(value, decimal: str = "auto") -> Decimal | None:
    """Money amount with at most two decimals. ``1.234,56`` and ``1,234.56`` both work in auto mode."""
    if is_blank(value):
        return None
    if isinstance(value, bool):
        raise CellError("is not an amount")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CellError("is not an amount")
        text = repr(value)
    elif isinstance(value, int):
        text = str(value)
    else:
        text = str(value).strip()
    text = re.sub(r"(?i)\s|€|eur\b| ", "", text)
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("-()")
    if not re.fullmatch(r"\d[\d.,]*", text or "x"):
        raise CellError("is not an amount")
    sep = _decimal_separator(text, decimal)
    if sep:
        whole, _, frac = text.rpartition(sep)
        whole = re.sub(r"[.,]", "", whole)
    else:
        whole, frac = re.sub(r"[.,]", "", text), ""
    if len(frac) > 2 and not frac.endswith("0" * (len(frac) - 2)):
        raise CellError("has more than two decimal places")
    try:
        out = Decimal(f"{whole or '0'}.{frac or '0'}")
    except InvalidOperation:
        raise CellError("is not an amount") from None
    if out > MAX_AMOUNT:
        raise CellError("is too large")
    return q2(-out if negative else out)


def _decimal_separator(text: str, mode: str) -> str:
    if mode == "comma":
        return "," if "," in text else ""
    if mode == "point":
        return "." if "." in text else ""
    has_c, has_p = "," in text, "." in text
    if has_c and has_p:
        return "," if text.rfind(",") > text.rfind(".") else "."
    for ch, present in ((",", has_c), (".", has_p)):
        if present:
            if text.count(ch) != 1:
                return ""  # repeated: thousands separators
            whole, _, frac = text.partition(ch)
            if len(frac) == 3 and whole.isdigit() and 1 <= len(whole) <= 3 and int(whole) > 0:
                raise CellError(f"is ambiguous ({text} could be a thousands separator); set the decimal option")
            return ch
    return ""


def parse_rate(value) -> Decimal | None:
    if is_blank(value):
        return None
    text = str(value).strip().replace("%", "").replace(",", ".").strip() if isinstance(value, str) else value
    try:
        rate = Decimal(str(text))
    except InvalidOperation:
        raise CellError("is not a VAT rate") from None
    if not rate.is_finite() or not 0 <= rate <= 100:
        raise CellError("is not a VAT rate between 0 and 100")
    return rate


def parse_status(value, default: str) -> str:
    if is_blank(value):
        return default
    key = str(value).strip().casefold()
    if key not in STATUS_MAP:
        raise CellError(f"has an unknown status {str(value).strip()[:30]!r}")
    return STATUS_MAP[key]


def parse_currency(value) -> None:
    if is_blank(value):
        return
    if str(value).strip().upper() not in ("EUR", "€"):
        raise CellError("is not EUR; only EUR documents can be imported for now")


def text(value, limit: int, what: str) -> str | None:
    """Trimmed text, or None if blank. Stored as text: a value like '=1+1' is data, never a formula."""
    if is_blank(value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    out = str(value).strip()
    if len(out) > limit:
        raise CellError(f"{what} is longer than {limit} characters")
    if any(ord(ch) < 32 and ch not in "\t" for ch in out):
        raise CellError(f"{what} contains control characters")
    return out


def money_float(amount: Decimal) -> float:
    return float(q2(amount))


__all__ = [
    "ALLOWED_VAT_RATES", "CENT", "CellError", "is_blank", "money_float", "parse_amount", "parse_currency",
    "parse_date", "parse_rate", "parse_status", "text",
]  # fmt: skip
