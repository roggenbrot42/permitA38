"""Trip statement (Reisekostenabrechnung) as PDF: header, day table, mileage, linked bills, totals, rules, signature.

Uses the same low-level PDF writer as the invoices (:class:`permita38.core.pdf.Pdf`); every user text goes through
the writer's escaping, and the layout wraps long text instead of running off the page.
"""

from datetime import datetime

from .pdf import Pdf

M = 50  # left/right margin in points
BOTTOM = 800  # y where a new page starts


def _de(x: float) -> str:
    """German number format without a currency sign: 1234.5 -> 1.234,50 (the writer has no euro glyph)."""
    return "{:,.2f}".format(x).replace(",", "X").replace(".", ",").replace("X", ".")


def _date(s: str | None) -> str:
    if not s:
        return ""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return str(s)


class _Writer:
    def __init__(self) -> None:
        self.pdf = Pdf()
        self.y = 50.0

    def ensure(self, need: float) -> None:
        if self.y + need > BOTTOM:
            self.pdf.new_page()
            self.y = 50.0

    def line(self, text: str, size: float = 10, bold: bool = False, gap: float = 14) -> None:
        for part in self.pdf.wrap(text, Pdf.W - 2 * M, size, bold):
            self.ensure(gap)
            self.pdf.text(M, self.y, part, size=size, bold=bold)
            self.y += gap

    def row(self, cols: list[tuple[float, str]], size: float = 9, bold: bool = False) -> None:
        """One table row; ``cols`` = (x offset from the margin, text). Cells are cut to fit their column width
        (the full text stays in the trip), so a long note never runs off the page."""
        self.ensure(12.0)
        for x, text in cols:
            cut = text if len(text) <= 48 else text[:45] + "..."
            self.pdf.text(M + x, self.y, cut, size=size, bold=bold)
        self.y += 12.0


def render(
    *,
    company: str,
    person: str,
    trip: dict,
    figures: dict,
    bills: list[dict],
) -> bytes:
    w = _Writer()
    pdf = w.pdf
    pdf.text(M, 50, "Reisekostenabrechnung / Trip statement", size=15, bold=True)
    w.y = 72
    period = f"{_date(trip.get('start'))} - {_date(trip.get('end'))}"
    destinations = []
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
    w.line(f"Status: {'Final' if trip.get('status') == 'final' else 'Draft'}", gap=13)
    w.y += 8

    w.line("Meal allowance per day", size=11, bold=True, gap=16)
    w.row([(0, "Date"), (62, "Place"), (200, "Hours"), (250, "Base"), (300, "Reduct."), (355, "Amount"), (400, "Note")],
          bold=True)
    for d in figures.get("days") or []:
        w.row([(0, _date(d["date"])), (62, str(d.get("city") or d["country"])), (200, _de(d["absence_hours"])), (250, _de(d["base"])),
               (300, _de(sum(x["amount"] for x in d.get("reductions") or []))), (355, _de(d["amount"])), (400, str(d.get("note") or ""))])
    w.row([(250, "Total allowance"), (355, _de(figures.get("allowance_total") or 0))], bold=True)
    w.y += 10

    w.line("Mileage (private car, 0.30 EUR per km)", size=11, bold=True, gap=16)
    w.row([(0, "Date"), (62, "From"), (160, "To"), (260, "Purpose"), (400, "km")], bold=True)
    for m in trip.get("mileage") or []:
        w.row([(0, _date(m.get("date"))), (62, str(m.get("from") or "")), (160, str(m.get("to") or "")),
               (260, str(m.get("purpose") or "")), (400, _de(m.get("km") or 0))])
    km = figures.get("mileage_km") or 0
    w.row([(260, "Total km / mileage"), (400, _de(km))], bold=True)
    w.row([(260, "Mileage amount"), (400, _de(figures.get("mileage_total") or 0))], bold=True)
    w.y += 10

    w.line("Linked bills", size=11, bold=True, gap=16)
    w.row([(0, "Date"), (62, "Vendor / description"), (240, "Category"), (340, "Net"), (400, "VAT")], bold=True)
    net_sum = vat_sum = 0.0
    for b in bills:
        net, vat = float(b.get("net") or 0), float(b.get("vat") or 0)
        net_sum += net
        vat_sum += vat
        w.row([(0, _date(b.get("date"))), (62, str(b.get("vendor") or b.get("description") or "")),
               (240, str(b.get("category") or "")), (340, _de(net)), (400, _de(vat))])
    w.row([(240, "Bills total"), (340, _de(net_sum)), (400, _de(vat_sum))], bold=True)
    w.y += 10

    w.line("Totals", size=11, bold=True, gap=16)
    w.row([(0, "Meal allowance"), (340, _de(figures.get("allowance_total") or 0))])
    w.row([(0, "Mileage"), (340, _de(figures.get("mileage_total") or 0))])
    w.row([(0, "Bills (net)"), (340, _de(net_sum))])
    w.y += 10

    w.line(f"Rules version: {figures.get('rules_version') or '-'} (BMF letter; country table placeholder where marked)",
           size=8, gap=11)
    w.line("Records: date, destination, purpose, start and end time, mileage with route and purpose. No receipts are "
           "needed for the meal allowance.", size=8, gap=11)
    w.y += 24
    w.ensure(40)
    pdf.line(M, w.y, M + 260, w.y)
    pdf.text(M, w.y + 12, "Date, signature", size=8)
    return pdf.output()
