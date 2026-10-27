"""Trip statement (Reisekostenabrechnung) as PDF: header, day table, mileage, linked bills, totals, rules, signature.

Uses the same low-level PDF writer as the invoices (:class:`permita38.core.pdf.Pdf`). Money is exact (``Decimal`` via
:mod:`permita38.core.money`, never floats). Every table cell is cut to its column width so that no text runs into a
neighbouring column or past the right margin; the full note of a day is printed under its row. All text goes through
the writer's escaping.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from .money import ZERO, dec, q2
from .pdf import Pdf, text_width

M = 50  # left margin in points
CONTENT_W = 495.0  # A4 width minus both margins
BOTTOM = 800  # y where a new page starts


def _de(x: Any) -> str:
    """German number format without a currency sign: Decimal('1234.5') -> 1.234,50."""
    value = q2(dec(x))
    return "{:,.2f}".format(value).replace(",", "X").replace(".", ",").replace("X", ".")


def _date(s: str | None) -> str:
    if not s:
        return ""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return str(s)


def _fit(text: str, width: float, size: float, bold: bool) -> str:
    """The text, cut with '...' so that it is no wider than ``width`` points."""
    if text_width(text, size, bold) <= width:
        return text
    cut = text
    while cut and text_width(cut + "...", size, bold) > width:
        cut = cut[:-1]
    return cut + "..."


class _Writer:
    def __init__(self) -> None:
        self.pdf = Pdf()
        self.y = 50.0

    def ensure(self, need: float) -> None:
        if self.y + need > BOTTOM:
            self.pdf.new_page()
            self.y = 50.0

    def line(self, text: str, size: float = 10, bold: bool = False, gap: float = 14) -> None:
        for part in self.pdf.wrap(text, CONTENT_W, size, bold):
            self.ensure(gap)
            self.pdf.text(M, self.y, part, size=size, bold=bold)
            self.y += gap

    def row(self, cells: list[tuple[float, float, str, str]], size: float = 9, bold: bool = False) -> None:
        """One table row. A cell is (x offset, column width, text, "l" or "r"). Text is cut to the column width;
        "r" cells are right-aligned to the column's right edge."""
        self.ensure(12.0)
        for x, width, text, align in cells:
            cut = _fit(text, width - 4, size, bold)
            if align == "r":
                self.pdf.text(M + x + width - 2, self.y, cut, size=size, bold=bold, align="r")
            else:
                self.pdf.text(M + x + 2, self.y, cut, size=size, bold=bold)
        self.y += 12.0

    def note(self, text: str) -> None:
        """A day note printed in full (wrapped, small type, indented)."""
        for part in self.pdf.wrap(text, CONTENT_W - 60, 7):
            self.ensure(9)
            self.pdf.text(M + 60, self.y, part, size=7)
            self.y += 9


def _sum(values) -> Decimal:
    return sum((dec(v) for v in values), ZERO)


def render(*, company: str, person: str, trip: dict, figures: dict, bills: list[dict]) -> bytes:
    w = _Writer()
    pdf = w.pdf
    status = "Final" if trip.get("status") == "final" else "Draft"
    pdf.text(M, 50, "Reisekostenabrechnung / Trip statement", size=15, bold=True)
    w.y = 72
    period = f"{_date(trip.get('start'))} - {_date(trip.get('end'))}"
    destinations: list[str] = []
    for p in trip.get("places") or []:
        label = " ".join(x for x in (p.get("city"), p.get("country")) if x)
        if label and label not in destinations:
            destinations.append(label)
    w.line(f"Company: {company}", gap=13)
    w.line(f"Person: {person}", gap=13)
    w.line(f"Title: {trip.get('title') or ''}", gap=13)
    w.line(f"Purpose: {trip.get('purpose') or ''}", gap=13)
    w.line(f"Period: {period} (local time)", gap=13)
    w.line(f"Destination: {', '.join(destinations) or 'Germany (no destination entered)'}", gap=13)
    w.line(f"Status: {status}", gap=13)
    w.y += 8

    # itinerary (only when the trip has legs): date and time, from, to, mode; planned expenses are not printed
    legs = trip.get("itinerary") or []
    if legs:
        w.line("Itinerary", size=11, bold=True, gap=16)
        w.row(
            [
                (0, 90, "Date / time", "l"),
                (90, 150, "From", "l"),
                (240, 150, "To", "l"),
                (390, 105, "Mode", "l"),
            ],
            bold=True,
        )
        for leg in legs:
            depart = str(leg.get("depart") or "")
            w.row(
                [
                    (0, 90, f"{_date(depart)} {depart[11:16]}".strip(), "l"),
                    (90, 150, str(leg.get("from") or ""), "l"),
                    (240, 150, str(leg.get("to") or ""), "l"),
                    (390, 105, str(leg.get("mode") or ""), "l"),
                ]
            )
        w.y += 10

    # day table: date, place, hours, base, reductions, amount
    w.line("Meal allowance per day", size=11, bold=True, gap=16)
    w.row(
        [
            (0, 62, "Date", "l"),
            (62, 128, "Place", "l"),
            (190, 52, "Hours", "r"),
            (242, 56, "Base", "r"),
            (298, 62, "Reduct.", "r"),
            (360, 70, "Amount", "r"),
        ],
        bold=True,
    )
    for d in figures.get("days") or []:
        reductions = _sum(x["amount"] for x in d.get("reductions") or [])
        w.row(
            [
                (0, 62, _date(d["date"]), "l"),
                (62, 128, str(d.get("city") or d["country"]), "l"),
                (190, 52, _de(d["absence_hours"]), "r"),
                (242, 56, _de(d["base"]), "r"),
                (298, 62, _de(reductions), "r"),
                (360, 70, _de(d["amount"]), "r"),
            ]
        )
        if d.get("note"):
            w.note(str(d["note"]))
    w.row([(242, 118, "Total allowance", "r"), (360, 70, _de(figures.get("allowance_total") or 0), "r")], bold=True)
    w.y += 10

    # mileage: date, from, to, purpose, km
    w.line("Mileage (private car, 0.30 EUR per km)", size=11, bold=True, gap=16)
    w.row(
        [
            (0, 60, "Date", "l"),
            (60, 110, "From", "l"),
            (170, 110, "To", "l"),
            (280, 130, "Purpose", "l"),
            (410, 85, "km", "r"),
        ],
        bold=True,
    )
    for m in trip.get("mileage") or []:
        w.row(
            [
                (0, 60, _date(m.get("date")), "l"),
                (60, 110, str(m.get("from") or ""), "l"),
                (170, 110, str(m.get("to") or ""), "l"),
                (280, 130, str(m.get("purpose") or ""), "l"),
                (410, 85, _de(m.get("km") or 0), "r"),
            ]
        )
    w.row([(280, 130, "Total km", "l"), (410, 85, _de(figures.get("mileage_km") or 0), "r")], bold=True)
    w.row([(280, 130, "Mileage amount", "l"), (410, 85, _de(figures.get("mileage_total") or 0), "r")], bold=True)
    w.y += 10

    # linked bills
    w.line("Linked bills", size=11, bold=True, gap=16)
    w.row(
        [
            (0, 60, "Date", "l"),
            (60, 150, "Vendor / description", "l"),
            (210, 110, "Category", "l"),
            (320, 85, "Net", "r"),
            (405, 90, "VAT", "r"),
        ],
        bold=True,
    )
    net_sum = _sum(b.get("net") for b in bills)
    vat_sum = _sum(b.get("vat") for b in bills)
    for b in bills:
        w.row(
            [
                (0, 60, _date(b.get("date")), "l"),
                (60, 150, str(b.get("vendor") or b.get("description") or ""), "l"),
                (210, 110, str(b.get("category") or ""), "l"),
                (320, 85, _de(b.get("net") or 0), "r"),
                (405, 90, _de(b.get("vat") or 0), "r"),
            ]
        )
    w.row([(210, 110, "Bills total", "l"), (320, 85, _de(net_sum), "r"), (405, 90, _de(vat_sum), "r")], bold=True)
    w.y += 10

    w.line("Totals", size=11, bold=True, gap=16)
    w.row([(0, 300, "Meal allowance", "l"), (320, 175, _de(figures.get("allowance_total") or 0), "r")])
    w.row([(0, 300, "Mileage", "l"), (320, 175, _de(figures.get("mileage_total") or 0), "r")])
    w.row([(0, 300, "Bills (net)", "l"), (320, 175, _de(net_sum), "r")])
    w.y += 10

    w.line(
        f"Rules version: {figures.get('rules_version') or '-'} (BMF letter; a note 'placeholder: verify' marks a "
        "country rate that is not yet from the BMF table)",
        size=8,
        gap=11,
    )
    w.line(
        "Records: date, destination, purpose, start and end time, mileage with route and purpose. No receipts are "
        "needed for the meal allowance.",
        size=8,
        gap=11,
    )
    w.y += 24
    w.ensure(40)
    pdf.line(M, w.y, M + 260, w.y)
    pdf.text(M, w.y + 12, "Date, signature", size=8)
    return pdf.output()
