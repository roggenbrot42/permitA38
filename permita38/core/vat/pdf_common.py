"""Shared helpers of the VAT PDFs (UStVA return, ZM): page, fonts, text cleaning. No I/O besides building bytes."""

import re
from typing import Any

from permita38.core import pdf as _pdf

# core/pdf.py is untyped; the strict module settings of core/vat/* would reject every call into it
Pdf: Any = _pdf.Pdf
text_width: Any = _pdf.text_width

BANNER = "Not confirmed by a tax adviser: the rules of this form year are still to be verified."
_M = 50.0
_W = 495.0
_BOTTOM = 770.0
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _text(value: object) -> str:
    return "" if value is None else str(value)


# The PDF fonts are WinAnsi (cp1252): dashes and quotes outside it (U+2212 in the box 83 label) print as ASCII.
_ASCII = str.maketrans(
    {
        "\u2212": "-", "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2015": "-",
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    }
)  # fmt: skip


def _clean(value: object) -> str:
    return _CTRL.sub(" ", _text(value)).translate(_ASCII)


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
