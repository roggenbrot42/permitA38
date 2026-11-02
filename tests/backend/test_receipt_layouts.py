"""Invented receipts in the layouts of real till and taxi receipts, as the words arrive: one word per token, columns
right-aligned on a slightly slanted baseline. All data is made up; hand-computed numbers
(docs/specs/receipt-scan.md, "Lessons from the first real receipts")."""

from decimal import Decimal as D

import pytest

from permita38.core.money import split_gross
from permita38.core.receipts.extraction import Word
from permita38.core.receipts.rules import evaluate
from permita38.core.receipts.textrules import group_lines, parse
from tests.backend.receipt_helpers import context, read

SLOPE = 0.004  # the right edge of the page sits 0.004 lower than the left edge


def scan(lines: list[str], slope: float = SLOPE, step: float = 0.012, h: float = 0.006) -> list[Word]:
    """``left<TAB>right``: the right part ends at x = 0.92. Every token is a word; y follows the slope."""
    out: list[Word] = []
    for i, line in enumerate(lines):
        left, _, right = line.partition("\t")
        for part, end in ((left, None), (right, 0.92)):
            tokens = part.split()
            widths = [round(0.011 * len(t), 4) for t in tokens]
            x = 0.05 if end is None else end - sum(widths) - 0.01 * (len(tokens) - 1)
            for t, w in zip(tokens, widths):
                out.append(Word(t, 0, round(x, 4), round(0.04 + i * step + slope * x, 4), w, h))
                x += w + 0.01
    return out


def run(lines: list[str], slope: float = SLOPE):
    words = scan(lines, slope)
    x = parse(words)
    return x, evaluate(read(words, "ocr"), x, context())


TSE = [
    "Datum und Zeit: 14. 10. 2026 18:21:07",
    "Seq.-Nr.: 90417 | S/N: 5310762",
    "Zertifikat-S/N 77aa",
    "Beginn 2025-01-03 Ende 2027-01-03",
    "Transaktion 55123 Signaturzähler 9021",
    "Bewirtungsaufwand-Angaben (Par.4 Abs.5 Ziff.2 EstG)",
    "Höhe der Aufwendungen: 23,80",
    "Ort / Datum: 16.10.2026 Unterschrift",
    "Amount: 26,00 Trace-number 123 Terminal ID 9 Belegnummer 5 Kartenname: VISA",
]

BISTRO = [
    "mustér Zzorlk Baidel",
    "Beispiel Bistro",
    "Musterstr. 8 / 80000 Beispielstadt",
    "Ihre Rechnung Nr: 41234\t14. 10. 2026",
    "1 x Testgericht à 11, 20 €\t11, 20 € B",
    "1 x Beilage à 3,10 €\t3,10 € B",
    "1 x Testgetränk à 5,20 €\t5,20 € A",
    "1 x Saft div. à 3,90 €\t3,90 € A",
    "Zusatz\t0,40 € A",
    "Summe Verkäufe\t23,80 €",
    "Total\t26,00 €",
    "Nettoumsatz\t21,34 €",
    "Ums. inkl. 19% MwSt\t9,50 € A",
    "enth. MwSt 19%\t1,52 € A",
    "Ums. inkl. 7% MwSt\t14,30 € B",
    "enth. MwSt 7%\t0,94 € B",
    "Steuersumme\t2,46 €",
    "Trinkgelder etc.\t2,20 €",
    "EC-KARTE\t26, 00 €",
    *TSE,
]


@pytest.mark.parametrize("slope", [0.0, 0.004, -0.004])
def test_bistro_receipt_is_read_exactly_and_booked(slope):
    x, ev = run(BISTRO, slope)
    assert (x.vendor, x.invoice_no, x.bill_date) == ("Beispiel Bistro", "41234", "2026-10-14")
    assert (x.total, x.subtotal, x.net_total, x.vat_total, x.card_amount) == (
        D("26.00"),
        D("23.80"),
        D("21.34"),
        D("2.46"),
        D("26.00"),
    )
    assert [(a.kind, a.amount) for a in x.adjustments] == [("tip", D("2.20"))]
    assert [(i.description, i.amount, i.vat_code) for i in x.items] == [
        ("Testgericht", D("11.20"), "B"),
        ("Beilage", D("3.10"), "B"),
        ("Testgetränk", D("5.20"), "A"),
        ("Saft div", D("3.90"), "A"),
        ("Zusatz", D("0.40"), "A"),
    ]
    assert [(g.rate, g.code, g.gross, g.vat) for g in x.vat_groups] == [
        (D("19"), "A", D("9.50"), D("1.52")),
        (D("7"), "B", D("14.30"), D("0.94")),
    ]
    assert ev.flags == [] and ev.clean
    assert ev.draft["category"] == "Business meals" and ev.draft["gross"] == 26.0
    assert ev.draft["deductible"] == 70 and ev.draft["vat_deductible"] == 100
    assert [(ln["gross"], ln["vat_rate"], ln["deductible"]) for ln in ev.draft["lines"]] == [
        (9.5, 19.0, 70),
        (14.3, 7.0, 70),
        (2.2, 0.0, 70),
    ]
    assert ev.draft["lines"][2]["description"] == "Tip"
    assert ev.doc["card_amount"] == "26.00" and ev.doc["prepaid"] is None and ev.doc["amount_due"] is None
    names = {c["name"]: c for c in ev.doc["checks"]}
    assert names["net_total"]["ok"] and names["vat_total"]["ok"] and names["groups_total"]["ok"]
    assert {b["field"] for b in ev.doc["boxes"]} >= {"net_total", "vat_total", "total", "vendor", "item"}
    assert all("value" in b for b in ev.doc["boxes"])
    total_box = next(b for b in ev.doc["boxes"] if b["field"] == "total")
    assert total_box["value"] == "26.00"


def test_bistro_booked_figures_match_the_oracle():
    _x, ev = run(BISTRO)
    parts = split_gross([(D(str(ln["gross"])), D(str(ln["vat_rate"]))) for ln in ev.draft["lines"]])
    assert sum(n for n, _ in parts) == D("23.54") and sum(v for _, v in parts) == D("2.46")
    assert sum(D(str(ln["gross"])) for ln in ev.draft["lines"]) == D("26.00")


def test_a_slanted_page_needs_the_slope_to_group_lines():
    words = scan(BISTRO, 0.004)
    lines = group_lines(words)
    amounts = [ln.text for ln in lines if "Testgericht" in ln.text]
    assert amounts == ["1 x Testgericht à 11, 20 € 11, 20 € B"]


PIZZA = [
    "Beispiel Gmbll",
    "Musterweg 3",
    "80000 Beispielstadt",
    "Rechnung Nr. 73 912\t12. 10. 2026",
    "Pizza Test\t14,20 € B",
    "Pasta Test\t18,60 € B",
    "Wein Test\t7,40 € A",
    "Wasser Test\t4,60 € A",
    "Trinkgeld\t2,20 €",
    "Total\t47,00 €",
    "Nettoumsatz\t40,73 €",
    "Steuersumme\t4,07 €",
    "Umsatz 19% inkl.\t12,00 € A",
    "MwSt 19%\t1,92 € A",
    "Umsatz 7% inkl.\t32,80 € B",
    "MwSt 7%\t2,15 € B",
    "Trinkgelder etc.\t2,20 €",
    "Visa\t47,00 €",
]


@pytest.mark.parametrize("slope", [0.0, 0.004])
def test_second_layout_is_booked_with_the_tip(slope):
    x, ev = run(PIZZA, slope)
    assert (x.vendor, x.invoice_no, x.bill_date, x.total) == ("Beispiel GmbH", "73912", "2026-10-12", D("47.00"))
    assert [(a.kind, a.amount) for a in x.adjustments] == [("tip", D("2.20"))]
    assert x.subtotal is None and x.card_amount == D("47.00")
    assert ev.flags == []
    assert ev.draft["category"] == "Business meals"
    assert [(ln["gross"], ln["vat_rate"]) for ln in ev.draft["lines"]] == [(12.0, 19.0), (32.8, 7.0), (2.2, 0.0)]
    parts = split_gross([(D(str(ln["gross"])), D(str(ln["vat_rate"]))) for ln in ev.draft["lines"]])
    assert (sum(n for n, _ in parts), sum(v for _, v in parts)) == (D("42.93"), D("4.07"))


TAXI = [
    "QUITTANCE NO. 6310",
    "Merci pour votre Course",
    "Beispiel Taxi Services S.A.R.L",
    "17, rue Exemple",
    "L-3852 Beispiel",
    "TVA: LU58214093",
    "09:20 - 09:31 07.10.26",
    "PRIX BASE\t€ 4,00",
    "PRIX DE COURSE\t€ 26,50",
    "3,0 % TVA\t€ 0,77",
]


@pytest.mark.parametrize("slope", [0.0, 0.004])
def test_french_taxi_receipt_only_has_an_unusual_rate(slope):
    x, ev = run(TAXI, slope)
    assert x.total == D("26.50") and x.invoice_no == "6310" and x.bill_date == "2026-10-07"
    assert x.vendor == "Beispiel Taxi Services S.A.R.L" and x.vat_id == "LU58214093"
    assert x.items == []
    assert [(g.rate, g.vat) for g in x.vat_groups] == [(D("3"), D("0.77"))]
    assert [f["code"] for f in ev.flags] == ["vat_rate_unusual"]
    assert ev.draft["category"] == "Transportation"


def test_card_slip_and_form_never_give_total_date_or_items():
    lines = ["Beispiel Cafe", "Strandweg 1", "Kaffee\t3,00 € A", "Total\t3,00 €", "Datum 03.10.2026", *TSE[1:]]
    x, _ev = run(lines)
    assert x.total == D("3.00") and x.bill_date == "2026-10-03"
    assert [i.description for i in x.items] == ["Kaffee"]


def test_net_and_vat_totals_catch_a_misread_figure():
    broken = [ln.replace("21,34", "21,35") for ln in BISTRO]
    _x, ev = run(broken)
    assert [f["code"] for f in ev.flags] == ["vat_mismatch"]
    broken = [ln.replace("Steuersumme\t2,46", "Steuersumme\t2,47") for ln in BISTRO]
    _x, ev = run(broken)
    assert [f["code"] for f in ev.flags] == ["vat_mismatch"]
    assert [c["name"] for c in ev.doc["checks"] if not c["ok"]] == ["vat_total"]
