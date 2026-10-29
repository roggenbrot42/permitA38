"""Cleaning of text and results before they reach a model (docs/specs/mcp-server.md, "Prompt-injection stance").

Plain functions, no SDK. Everything a tool returns is untrusted data: control, zero-width and bidi characters are
removed, free text is truncated and every result carries ``untrusted_data: true``. This reduces the risk, it does not
remove it.
"""

import json
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

TEXT_MAX = 500  # free-text fields (notes, title, intro ...)
ITEMS_MAX = 50  # items per list result
BYTES_MAX = 64 * 1024  # hard cut of a whole result
MARK = "… [truncated]"

#: categories removed from text: Cc control (but not \n and \t), Cf format (zero-width, bidi, BOM), Co private use,
#: Cs surrogates, Zl/Zp line and paragraph separators
_DROP = {"Cc", "Cf", "Co", "Cs", "Zl", "Zp", "Cn"}
_KEEP = {"\n", "\t"}


def strip_chars(value: str) -> str:
    """The text without control, zero-width and bidi characters (newline and tab stay)."""
    return "".join(ch for ch in value if ch in _KEEP or unicodedata.category(ch) not in _DROP)


def clean_text(value: Any, limit: int = TEXT_MAX) -> str | None:
    """A free-text field as a safe short string; ``None`` stays ``None``; a longer text ends in the truncation mark."""
    if value is None:
        return None
    text = strip_chars(value if isinstance(value, str) else str(value))
    if len(text) > limit:
        text = text[:limit].rstrip() + MARK
    return text


def decimal_str(value: Any, places: int | None = None) -> str | None:
    """A number as a decimal string (never a float repr like 1e-05); ``places`` fixes the decimals (half-up)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    if places is not None:
        number = number.quantize(Decimal(1).scaleb(-places))
    return format(number, "f")


def envelope(items: list[dict], total: int, limit: int = ITEMS_MAX, max_bytes: int = BYTES_MAX) -> dict:
    """``{"untrusted_data": true, "items": [...], "truncated": bool, "total": int}``, cut to ``limit`` items and
    ``max_bytes`` of JSON."""
    limit = max(1, min(limit, ITEMS_MAX))
    kept = list(items[:limit])
    truncated = total > len(kept) or len(items) > len(kept)

    def build() -> dict:
        return {"untrusted_data": True, "items": kept, "truncated": truncated, "total": total}

    while kept and len(json.dumps(build(), ensure_ascii=False).encode("utf-8")) > max_bytes:
        kept.pop()
        truncated = True
    return build()


def wrap(data: dict) -> dict:
    """A single (non-list) result inside the untrusted-data envelope."""
    return {"untrusted_data": True, **data}
