"""Layouts from real PDF invoices and till receipts, rebuilt with invented vendors and amounts."""

from decimal import Decimal as D

from permita38.core.receipts.rules import evaluate
from permita38.core.receipts.textrules import parse
from tests.backend.receipt_helpers import bill, context, read


def run(lines, **kw):
    x = parse(bill(lines), **kw)
    return x, evaluate(read(bill(lines)), x, context())


def codes(ev):
    return {f["code"] for f in ev.flags}


ONLINE = [
    "Invoice",
    "Invoice number QQ11AA22-0001",
    "Date of issue October 14, 2026",
    "Date due October 14, 2026",
    "Fjord Software Norway, Limited Bill to",
    "1 Example Quay Max Muster",
    "Beispielstadt 7 Musterweg 1",
    "Prepaid credits, Team plan 1 50,00 19% 50,00",
    "Subtotal 50,00",
    "Total excluding tax 50,00",
    "VAT - Germany (19% on 50,00) 9,50",
    "Total 59,50",
    "Amount due 59,50",
]


def test_english_invoice_is_clean_with_net_subtotal_and_prepaid_word_in_an_item():
    x, ev = run(ONLINE)
    assert ev.clean, codes(ev)
    assert (x.bill_date, x.due_date) == ("2026-10-14", "2026-10-14")
    assert x.vendor == "Fjord Software Norway, Limited"
    assert x.prepaid is None and x.total == D("59.50")
    assert ev.draft["gross"] == 59.5
    assert ev.draft["paid_on"] is None


def test_prepaid_amount_of_zero_is_none_and_labelled_prepayment_counts():
    x, _ = run([*ONLINE[:-2], "Anzahlung 0,00", "Total 59,50"])
    assert x.prepaid is None
    x, ev = run([*ONLINE[:-2], "Anzahlung 10,00", "Total 59,50", "Restbetrag 49,50"])
    assert x.prepaid == D("10.00") and "payment_unclear" in codes(ev)


def test_receipt_of_the_same_invoice_has_the_same_key_and_takes_the_paid_date():
    lines = [ln for ln in ONLINE if not ln.startswith(("Date", "Amount"))]
    lines[0] = "Receipt"
    lines.insert(2, "Receipt number 7777-0001")
    lines.insert(3, "Date paid October 16, 2026")
    x, ev = run(lines)
    _, first = run(ONLINE)
    assert x.bill_date == "2026-10-16" and ev.invoice_key == first.invoice_key and ev.invoice_key


def test_invoice_date_beats_datum_and_order_dates_are_ignored():
    x, ev = run(
        ["Beispiel Druck GmbH", "Rechnungsnummer: 55-1", "Bestelldatum: 07.10.2026", "Rechnungsdatum: 09.10.2026"]
    )
    assert x.bill_date == "2026-10-09" and not x.competing_dates
    x, _ = run(["Beispiel Druck GmbH", "Rechnungsdatum", "/Lieferdatum 03.10.2026", "Bestelldatum 07.10.2026"])
    assert x.bill_date == "2026-10-03"


def test_net_and_gross_labels_and_the_company_vendor_in_a_footer():
    lines = [
        "Rechnung",
        "Rechnungsnummer: 99-1",
        "Rechnungsdatum: 09.10.2026",
        "Artikel: Flyer 12,00 EUR",
        "Rechnungsbetrag netto: 12,00 EUR",
        "zzgl. 19 % MwSt. 2,28 EUR",
        "Rechnungsbetrag brutto: 14,28 EUR",
        "Druckerei Beispiel GmbH Geschäftsführer: A. B.",
    ]
    x, ev = run(lines)
    assert ev.clean, codes(ev)
    assert x.vendor == "Druckerei Beispiel GmbH" and ev.draft["gross"] == 14.28


def test_own_company_and_labels_are_not_the_vendor():
    x, _ = run(
        ["QR-Code", "Zahlungsreferenznummer 12AB", "Mein Büro GmbH", "Anbieter Muster AG"], exclude=("Mein Büro GmbH",)
    )
    assert x.vendor and "Muster" in x.vendor


def test_two_line_company_name_and_marketplace_brand():
    x, _ = run(["Merci pour votre course", "Lux Beispiel", "Services S.A.R.L", "Rue 1", "L-1234 Stadt"])
    assert x.vendor == "Lux Beispiel Services S.A.R.L"
    x, _ = run(["Rechnung", "Umsatzsteuer erklärt durch Amazon Services Europe S.a.r.L.", "Zahlbetrag 5,00 €"])
    assert x.vendor == "Amazon"


def test_table_layout_with_percent_first_and_net_rows():
    x, _ = run(["Rechnung 1", "19% 5,00 € 0,95 €", "Zahlbetrag 5,95 €"])
    g = x.vat_groups[0]
    assert (g.rate, g.net, g.vat) == (D("19"), D("5.00"), D("0.95"))
    x, _ = run(["Netto MWST. 19% 10,00", "MWST. 19% 1,90", "Netto MWST. 7% 20,00", "MWST. 7% 1,40"])
    assert [(g.rate, g.net, g.vat) for g in x.vat_groups] == [
        (D("19"), D("10.00"), D("1.90")),
        (D("7"), D("20.00"), D("1.40")),
    ]


def test_total_from_the_card_amount_when_the_total_was_not_read():
    lines = [
        "Cafe Beispiel GmbH",
        "Netto MWST. 19% 10,00",
        "MWST. 19% 1,90",
        "Netto MWST. 7% 20,00",
        "MWST. 7% 1,40",
        "Kredit Karte 33,30",
    ]
    x, ev = run(lines)
    assert x.total == D("33.30") and "no_total" not in codes(ev)
    x, ev = run(lines[:-1] + ["Kredit Karte 33,40"])
    assert x.total is None and "no_total" in codes(ev)


def test_till_row_that_lost_its_code_letter_still_maps_by_sums_and_a_wrong_sum_is_flagged():
    base = [
        "Cafe Beispiel GmbH",
        "Rechnung Nr: 11",
        "07.10.2026",
        "Tee 3,00 € A",
        "Kuchen 4,00 € B",
        "Summe Verkäufe 7,00 €",
        "Total 7,00 €",
        "Ums. inkl. 19% MwSt 3,00 € A",
        "enth. MwSt 19% 0,48 € A",
        "Ums. inkl. 7% MwSt 4,00 €",
        "EC-KARTE 7,00 €",
    ]
    x, ev = run(base)
    assert ev.clean, codes(ev)
    bad = [ln.replace("Kuchen 4,00", "Kuchen 4,50") for ln in base]
    assert "items_mismatch" in codes(run(bad)[1])


def test_tip_split_by_ocr_and_missread_vat_word():
    lines = [
        "Cafe Beispiel GmbH",
        "Rechnung Nr: 12",
        "07.10.2026",
        "Tee 3,00 € A",
        "Summe Verkäufe 3,00 €",
        "Total 3,50 €",
        "Umsatz 19% inkl. 3,00 € A",
        "MuSt 19% 0,48",
        "Trinkgel der etc. 0,50 €",
        "EC-KARTE 3,50 €",
    ]
    x, ev = run(lines)
    assert ev.clean, codes(ev)
    assert [(ln["gross"], ln["vat_rate"]) for ln in ev.draft["lines"]] == [(3.0, 19.0), (0.5, 0.0)]


def test_currency_needs_an_amount_and_euro_amounts_win_over_noise():
    x, ev = run([*ONLINE[:7], "Zeitschrift $ ~ x", "Total 59,50 €"])
    assert x.currency == "EUR"
    x, ev = run(["Fjord Inc.", "Total $23.80 USD", "Subtotal $20.00", "Tax $3.80"])
    assert "foreign_currency" in codes(ev)


def test_cancellation_terms_in_a_footer_are_not_a_credit_note():
    lines = ["Hotel Beispiel GmbH", "Rechnung Nr 5", "Datum: 07.10.2026", "Zimmer 100,00 €", "Total 100,00 €"]
    footer = [
        *lines,
        "Bei Absage kurz vor der Anreise berechnen wir 60 % des Preises, eine Gutschrift entfällt.",
    ]
    assert "credit_note" not in codes(run(footer)[1])
    assert "credit_note" in codes(run(["Gutschrift", *lines])[1])


def test_parking_and_subscriptions_get_a_category():
    _, ev = run(["Flughafen Beispiel GmbH", "Rechnung Nr 1", "Parken im Flughafen", "Gesamtsumme 20,00 €"])
    assert ev.draft["category"] == "Transportation"
    _, ev = run(["Beispiel Cloud GmbH", "Rechnung Nr 1", "Max plan monatlich", "Gesamtsumme 20,00 €"])
    assert ev.draft["category"] == "Software & subscriptions"


NUMBER_TABLE = [
    "Nordlicht Handel GmbH Hafenweg 5 - 20457 Hamburg Rechnung",
    "Rechnungs-Nr. 5550001112 / 03.09.2026",
    "Beispiel Test Kunde Auftrags-Nr. 777 / 03.09.2026",
    "Artikel-Nr. Bezeichnung Menge Preis in EUR Gesamtpreis MwSt",
    "Nova Speicher",
    "Modul 16 GB",
    "200012345 3 40,11 120,33 A",
    "Zubehoer-Set",
    "Hersteller-Artikel-Nr. : XY123",
    "Versandkosten",
    "999111 1 8,71 8,71 A",
    "Netto-Summe EUR 129,04",
    "Mehrwertsteuer(A): 19 % auf 129,04 EUR 24,51",
    "Rechnungsendbetrag in EUR 153,55",
    "Nordlicht Handel GmbH - Hafenweg 5 - 20457 Hamburg",
    "USt-IDNr.: DE 987 654 321",
]


def test_number_table_rows_take_the_name_from_above_and_net_items_match_the_net_sum():
    x, ev = run(NUMBER_TABLE)
    assert [(i.description, i.quantity, i.amount, i.vat_code) for i in x.items] == [
        ("Nova Speicher Modul 16 GB", D(3), D("120.33"), "A"),
        ("Versandkosten", D(1), D("8.71"), "A"),
    ]  # the article number 200012345 is never the quantity
    assert x.vat_id == "DE987654321" and x.net_total == D("129.04")
    assert (x.vat_groups[0].code, x.vat_groups[0].net, x.vat_groups[0].vat) == ("A", D("129.04"), D("24.51"))
    assert ev.clean, codes(ev)
    assert ev.draft["gross"] == 153.55


def test_vat_rounded_per_net_row_is_exact_but_a_wrong_vat_stays_flagged():
    # per row 22,86 + 1,65 = 24,51 (the printed VAT); of the sum 129,04 it is 24,52; 24,50 is neither
    lines = [ln.replace("24,51", "24,50") for ln in NUMBER_TABLE]
    x, ev = run(lines)
    assert "vat_mismatch" in codes(ev) and not ev.clean


def test_net_price_label_lets_a_single_net_item_match_the_net():
    lines = [
        "Blauwerk GmbH Seestraße 3 31139 Beispielstadt",
        "Rechnung",
        "Rechnungsnr.: 4711x 12.10.2026",
        "Produkt Einzelpreis Anzahl Preis",
        "900123 Beratungspaket EUR 50,00 1 EUR 50,00",
        "Nettopreis: EUR 50,00",
        "MwSt (19%): EUR 9,50",
        "Rechnungsbetrag: EUR 59,50",
        "Umsatzsteuer-Id für die EU: DE111222333",
    ]
    x, ev = run(lines)
    assert x.net_total == D("50.00") and x.vat_id == "DE111222333"
    assert "items_mismatch" not in codes(ev) and ev.draft["gross"] == 59.5
