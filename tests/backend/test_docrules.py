"""Document rules (docs/specs/document-scan.md): synthetic word lists of invented offers and invoices.

Oracle numbers are computed by hand in the comments. All names, addresses and numbers are invented."""

from datetime import date
from decimal import Decimal as D

import pytest

from permita38.core.receipts.docrules import DocExtraction, DocLine, DocVat, OwnIdentity, Recipient, evaluate, parse
from permita38.core.receipts.einvoice import read_einvoice
from permita38.core.receipts.extraction import ReadResult, Word
from tests.backend.receipt_helpers import UBL

OWN = OwnIdentity("Max Muster IT-Beratung", "Max Muster", "DE123456789", "12/345/67890")
TODAY = date(2026, 11, 3)

SENDER = ("Max Muster IT-Beratung · Musterweg 1 · 12345 Musterstadt", 0.012)
CUSTOMER = ["Beispiel Kunde GmbH", "Kundenweg 5", "54321 Kundenstadt"]


def doc(rows, page: int = 0, step: float = 0.03, top: float = 0.04) -> list[Word]:
    """Rows to words. A row is ``text``, ``(text, height)`` or ``None`` (an empty row); row ``i`` sits at
    ``y = top + i * step``; tokens left to right by their length."""
    out: list[Word] = []
    for i, row in enumerate(rows):
        if row is None:
            continue
        text, h = (row, 0.02) if isinstance(row, str) else row
        x = 0.05
        for token in text.split():
            w = round(0.012 * len(token), 4)
            out.append(Word(token, page, round(x, 4), round(top + i * step, 4), w, h))
            x += w + 0.012
    return out


def read(words: list[Word], engine: str = "pdf_text") -> ReadResult:
    return ReadResult(engine=engine, words=words, pages_read=1, pages_total=1)  # type: ignore[arg-type]


def scan(rows, own: OwnIdentity = OWN) -> DocExtraction:
    return parse(read(doc(rows)), own)


# Net 750,00 = 10 x 50,00 (500,00) + 4 x 62,50 (250,00); VAT 19 % of 750,00 = 142,50; gross 892,50.
INVOICE = [
    SENDER,
    None,
    *CUSTOMER,
    None,
    ("Rechnung", 0.04),
    "Rechnungsnummer: RE-2024-007",
    "Rechnungsdatum: 05.03.2024",
    "Leistungszeitraum: 01.02.2024 - 29.02.2024",
    "USt-IdNr. DE123456789",
    None,
    "Pos Beschreibung Menge Einzelpreis Gesamt",
    "1 Beratung 10 Std 50,00 500,00",
    "2 Entwicklung Modul A 4 Std 62,50 250,00",
    "Phase zwei",
    "Nettobetrag 750,00 EUR",
    "zzgl. 19% MwSt 142,50 EUR",
    "Gesamtbetrag 892,50 EUR",
    "Zahlbar innerhalb von 14 Tagen ohne Abzug",
]


def swap(rows, old: str, new):
    """Rows with the one that starts with ``old`` replaced by ``new`` (a row, a list of rows, or ``None``)."""
    out = []
    hit = False
    for r in rows:
        text = r if isinstance(r, str) else (r[0] if r else "")
        if text.startswith(old):
            hit = True
            out.extend(new if isinstance(new, list) else [new])
        else:
            out.append(r)
    assert hit, old
    return out


def flags_of(x: DocExtraction) -> list[str]:
    return evaluate(x, TODAY)[0]


# ---- a clean invoice ---------------------------------------------------------------------------------------------


def test_clean_invoice():
    x = scan(INVOICE)
    assert (x.kind, x.title_word, x.number, x.date) == ("invoice", "Rechnung", "RE-2024-007", "2024-03-05")
    assert (x.service_from, x.service_to) == ("2024-02-01", "2024-02-29")
    assert (x.due_days, x.due_date) == (14, "2024-03-19")  # 05.03. + 14 days
    assert x.sender_ok
    assert x.recipient == Recipient("Beispiel Kunde GmbH", "Kundenweg 5", "54321", "Kundenstadt", None, None, None)
    assert (x.net, x.vat_total, x.gross, x.currency) == (D("750.00"), D("142.50"), D("892.50"), "EUR")
    assert x.vat_groups == [DocVat(D("19"), None, D("142.50"))]
    assert x.lines == [
        DocLine("Beratung", D("10"), "Std", D("50.00"), D("500.00")),
        DocLine("Entwicklung Modul A Phase zwei", D("4"), "Std", D("62.50"), D("250.00")),  # wrapped description
    ]
    assert not x.lines_summarised
    assert evaluate(x, TODAY) == ([], [])
    assert {"kind", "number", "date", "recipient", "net", "gross", "line:0", "line:1"} <= set(x.sources)


# ---- kind from the title -----------------------------------------------------------------------------------------

OFFER_BODY = [
    SENDER,
    None,
    *CUSTOMER,
    None,
    ("Angebot", 0.04),
    "Angebotsnummer: A-2024-012",
    "Angebotsdatum: 01.04.2024",
    "Angebot gültig bis 30.04.2024",
    "Nach Annahme des Angebots erhalten Sie von uns die Rechnung per E-Mail.",
    None,
    None,
    "Pos Beschreibung Menge Einzelpreis Gesamt",
    "1 Konzept 1 Pausch 100,00 100,00",
    "Nettobetrag 100,00 EUR",
    "zzgl. 19% MwSt 19,00 EUR",
    "Gesamtbetrag 119,00 EUR",
]


def test_offer_with_rechnung_in_the_body_is_an_offer():
    x = scan(OFFER_BODY)
    assert (x.kind, x.number, x.date, x.valid_until) == ("offer", "A-2024-012", "2024-04-01", "2024-04-30")
    assert x.lines == [DocLine("Konzept", D("1"), "Pausch", D("100.00"), D("100.00"))]
    assert evaluate(x, TODAY) == ([], [])  # 19 % of 100,00 = 19,00; 119,00


def test_short_word_rechnung_next_to_a_taller_title_does_not_decide():
    rows = swap(OFFER_BODY, "Nach Annahme", "Rechnung folgt nach Abschluss")
    assert scan(rows).kind == "offer"


def test_title_zone_boundary():
    # TITLE_ZONE = 0.4 of the page height: a title word at y 0.39 counts, one at 0.41 does not
    def at(y: float) -> DocExtraction:
        words = [Word("Rechnung", 0, 0.05, y, 0.1, 0.04), *doc(["Nummer: 1"], top=0.6)]
        return parse(read(words), OWN)

    assert at(0.39).kind == "invoice"
    assert at(0.41).kind is None


def test_rechnung_only_on_page_two_is_unclear():
    words = [*doc(["Beispiel Text"]), Word("Rechnung", 1, 0.05, 0.05, 0.1, 0.04)]
    r = ReadResult(engine="pdf_text", words=words, pages_read=2, pages_total=2)  # type: ignore[arg-type]
    x = parse(r, OWN)
    assert x.kind is None and "kind_unclear" in flags_of(x)


def test_word_rechnung_below_the_title_zone_does_not_decide():
    rows = swap(OFFER_BODY, "Nach Annahme", None) + [None] * 4 + ["Rechnung"]
    assert scan(rows).kind == "offer"


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("Angebot", "offer"),
        ("Kostenvoranschlag", "offer"),
        ("Quote", "offer"),
        ("Offer", "offer"),
        ("Invoice", "invoice"),
        ("Rechnung", "invoice"),
    ],
)
def test_title_words(title, kind):
    assert scan(swap(INVOICE, "Rechnung", (title, 0.04))).kind == kind


@pytest.mark.parametrize(
    "title", ["Gutschrift", "Rechnungskorrektur", "Stornorechnung", "Storno-Rechnung", "Credit note"]
)
def test_unsupported_kinds_are_flagged(title):
    x = scan(swap(INVOICE, "Rechnung", (title, 0.04)))
    assert x.kind in ("credit_note", "correction", "cancellation")
    assert "kind_unsupported" in flags_of(x)
    assert "kind_unclear" not in flags_of(x)


def test_no_title_and_two_titles_are_unclear():
    none = scan(swap(INVOICE, "Rechnung", None))
    assert none.kind is None and "kind_unclear" in flags_of(none)
    both = scan(swap(INVOICE, "Rechnung", [("Rechnung", 0.04), ("Angebot", 0.04)]))
    assert both.kind is None and "kind_unclear" in flags_of(both)


# ---- number labels -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "number"),
    [
        ("Rechnungsnummer: RE-1", "RE-1"),
        ("Rechnungs-Nr.: 2024/15", "2024/15"),
        ("Rechnung Nr. 15", "15"),
        ("Invoice No. INV-9", "INV-9"),
        ("Rechnungsnummer 2024 - 7", "2024"),  # the value stops at the space
        ("Nr.: 77", "77"),
    ],
)
def test_invoice_number_labels(row, number):
    assert scan(swap(INVOICE, "Rechnungsnummer", row)).number == number


@pytest.mark.parametrize(
    ("row", "number"),
    [
        ("Angebotsnummer: A-1", "A-1"),
        ("Angebots-Nr.: 12", "12"),
        ("Angebot Nr. 33", "33"),
        ("Nr. 44", "44"),
    ],
)
def test_offer_number_labels(row, number):
    assert scan(swap(OFFER_BODY, "Angebotsnummer", row)).number == number


def test_number_below_the_label():
    x = scan(swap(INVOICE, "Rechnungsnummer", ["Rechnungsnummer:", "RE-2024-99"]))
    assert x.number == "RE-2024-99"


def test_plain_nr_of_a_customer_does_not_count_and_specific_label_wins():
    rows = swap(INVOICE, "USt-IdNr", ["Kunden Nr. 4711", "USt-IdNr. DE123456789"])
    assert scan(rows).number == "RE-2024-007"


def test_line_whose_lowercase_changes_length_is_skipped_safely():
    rows = swap(INVOICE, "Rechnungsnummer", ["İİİ Rechnungsnummer: X-1", "Rechnungsnummer: RE-2024-007"])
    x = scan(rows)
    assert x.number == "RE-2024-007"


def test_two_different_numbers_are_ambiguous_and_none_is_flagged():
    two = scan(swap(INVOICE, "Rechnungsnummer", ["Rechnungsnummer: RE-1", "Rechnung Nr. RE-2"]))
    assert two.number is None and two.number_candidates == ["RE-1", "RE-2"]
    assert "ambiguous_number" in flags_of(two)
    same = scan(swap(INVOICE, "Rechnungsnummer", ["Rechnungsnummer: RE-1", "Rechnung Nr. RE-1"]))
    assert same.number == "RE-1"
    none = scan(swap(INVOICE, "Rechnungsnummer", None))
    assert none.number is None and "no_number" in flags_of(none)
    no_digit = scan(swap(INVOICE, "Rechnungsnummer", "Rechnungsnummer: folgt"))
    assert no_digit.number is None and "no_number" in flags_of(no_digit)


# ---- dates -------------------------------------------------------------------------------------------------------


def test_due_date_labels():
    for label in ("Fällig am", "Zahlbar bis", "Zahlungsziel:"):
        x = scan(swap(INVOICE, "Zahlbar innerhalb", f"{label} 20.03.2024"))
        assert x.due_date == "2024-03-20", label
        assert x.date == "2024-03-05"


def test_due_in_n_days_variants():
    # 05.03.2024 + 30 days = 04.04.2024 (March has 31 days)
    assert scan(swap(INVOICE, "Zahlbar innerhalb", "Payable within 30 days")).due_date == "2024-04-04"
    x = scan(swap(INVOICE, "Zahlbar innerhalb", "zahlbar innerhalb von 30 Tagen"))
    assert (x.due_days, x.due_date) == (30, "2024-04-04")


@pytest.mark.parametrize(
    ("row", "days"),
    [
        ("Bei Zahlung innerhalb von 8 Tagen 2 % Skonto, zahlbar innerhalb von 30 Tagen netto", 30),
        ("Zahlbar innerhalb von 30 Tagen netto, bei Zahlung innerhalb von 8 Tagen 2 % Skonto", 30),
        ("Bei Zahlung innerhalb von 8 Tagen 2 % Skonto", None),
        ("Alle Preise inkl. 19 % MwSt. Zahlbar innerhalb von 14 Tagen netto.", 14),
        ("Zahlbar innerhalb von 14 Tagen netto. Bei Zahlung innerhalb von 7 Tagen 2 % Skonto.", 14),
        ("Zahlung innerhalb von 10 Werktagen", None),
        ("Zahlbar innerhalb von 14 Kalendertagen", 14),
    ],
)
def test_due_days_ignore_skonto_and_working_days(row, days):
    x = scan(swap(INVOICE, "Zahlbar innerhalb", row))
    assert x.due_days == days
    assert x.due_date == (None if days is None else _plus(x.date, days))


def _plus(iso: str, n: int) -> str:
    from datetime import timedelta

    return (date.fromisoformat(iso) + timedelta(days=n)).isoformat()


def test_printed_due_date_wins_over_days():
    x = scan(swap(INVOICE, "Zahlbar innerhalb", ["Fällig am 20.03.2024", "zahlbar innerhalb von 30 Tagen"]))
    assert (x.due_days, x.due_date) == (30, "2024-03-20")


def test_service_single_date_and_period():
    one = scan(swap(INVOICE, "Leistungszeitraum", "Leistungsdatum: 15.02.2024"))
    assert (one.service_from, one.service_to) == ("2024-02-15", "2024-02-15")
    delivery = scan(swap(INVOICE, "Leistungszeitraum", "Lieferdatum 16.02.2024"))
    assert (delivery.service_from, delivery.service_to) == ("2024-02-16", "2024-02-16")


def test_date_labels_and_place_date():
    assert scan(swap(INVOICE, "Rechnungsdatum", "Datum: 06.03.2024")).date == "2024-03-06"
    assert scan(swap(INVOICE, "Rechnungsdatum", "Date: 2024-03-07")).date == "2024-03-07"
    assert scan(swap(INVOICE, "Rechnungsdatum", "Musterstadt, 08.03.2024")).date == "2024-03-08"
    # an order date is not the document date
    ordered = swap(INVOICE, "Rechnungsdatum", ["Bestelldatum: 01.01.2024", "Rechnungsdatum: 06.03.2024"])
    assert scan(ordered).date == "2024-03-06"


def test_implausible_and_missing_and_competing_dates():
    assert "date_implausible" in flags_of(scan(swap(INVOICE, "Rechnungsdatum", "Rechnungsdatum: 05.03.2031")))
    assert "date_implausible" in flags_of(scan(swap(INVOICE, "Rechnungsdatum", "Rechnungsdatum: 05.03.1999")))
    assert "no_date" in flags_of(scan(swap(INVOICE, "Rechnungsdatum", None)))
    two = scan(swap(INVOICE, "Rechnungsdatum", ["Rechnungsdatum: 05.03.2024", "Datum: 06.03.2024"]))
    assert two.date == "2024-03-05"  # the stronger label wins
    twice = scan(swap(INVOICE, "Rechnungsdatum", "Rechnungsdatum: 05.03.2024 Rechnungsdatum: 06.03.2024"))
    assert twice.date is None and "ambiguous_date" in flags_of(twice)


# ---- recipient and sender ----------------------------------------------------------------------------------------


def test_recipient_without_sender_line():
    x = scan(INVOICE[2:])
    assert x.recipient is not None and x.recipient.name == "Beispiel Kunde GmbH"
    assert x.sender_ok  # the VAT id on the page


def test_letterhead_of_the_user_is_not_the_recipient():
    rows = [
        "Max Muster IT-Beratung",
        "Musterweg 1",
        "12345 Musterstadt",
        None,
        *CUSTOMER,
        None,
        *INVOICE[6:],
    ]
    x = scan(rows)
    assert x.recipient is not None
    assert (x.recipient.name, x.recipient.zip, x.recipient.city) == ("Beispiel Kunde GmbH", "54321", "Kundenstadt")
    assert evaluate(x, TODAY) == ([], [])


def test_recipient_with_country_email_and_vat_id():
    rows = swap(
        INVOICE,
        "54321 Kundenstadt",
        ["kunde@example.invalid", "A-1010 Wien", "Österreich"],
    )
    rows = swap(rows, "USt-IdNr.", ["USt-IdNr. DE123456789", "Kunden USt-IdNr. ATU12345678"])
    x = scan(rows)
    assert x.recipient is not None
    assert (x.recipient.zip, x.recipient.city, x.recipient.country, x.recipient.email) == (
        "1010",
        "Wien",
        "AT",
        "kunde@example.invalid",
    )
    assert (x.recipient.name, x.recipient.street) == ("Beispiel Kunde GmbH", "Kundenweg 5")
    assert x.recipient.vat_id == "ATU12345678"


def test_no_recipient_is_flagged():
    x = scan(INVOICE[:1] + [None] + INVOICE[5:])
    assert x.recipient is None and "no_customer" in flags_of(x)


def test_not_my_document():
    other = OwnIdentity("Erika Beispiel Studio", "Erika Beispiel", "DE999999999", "98/765/43210")
    x = scan(INVOICE, other)
    assert not x.sender_ok and "not_my_document" in flags_of(x)
    assert not scan(INVOICE, OwnIdentity()).sender_ok  # empty settings prove nothing


@pytest.mark.parametrize(
    "own",
    [
        OwnIdentity(company_name="max  muster it-beratung"),
        OwnIdentity(owner_name="MAX MUSTER"),
        OwnIdentity(vat_id="DE 123 456 789"),
        OwnIdentity(tax_number="12 345 67890"),
    ],
)
def test_sender_matches_by_any_identity_ignoring_case_and_spacing(own):
    rows = [*INVOICE[:1], *INVOICE[1:]]
    rows = swap(rows, "USt-IdNr.", "Steuernummer 12/345/67890 USt-IdNr. DE123456789")
    assert scan(rows, own).sender_ok


# ---- totals, lines, VAT ------------------------------------------------------------------------------------------


def test_lines_that_do_not_sum_to_the_net_become_one_summary_line():
    rows = swap(INVOICE, "2 Entwicklung", "2 Entwicklung Modul A 4 Std 62,50 240,00")  # 500 + 240 != 750
    x = scan(rows)
    assert x.lines_summarised
    assert x.lines == [DocLine("Leistungen laut Originalbeleg", D("1"), None, D("750.00"), D("750.00"))]
    assert evaluate(x, TODAY) == ([], ["lines_summarised"])  # a note, not a flag


def test_line_quantity_times_price_must_round_to_the_amount():
    # 3 x 33,33 = 99,99 but 100,00 is printed: the lines are not proven although they sum to the net
    rows = swap(INVOICE, "1 Beratung", "1 Beratung 3 Std 33,33 100,00")
    rows = swap(rows, "2 Entwicklung", "2 Entwicklung 1 Std 650,00 650,00")
    rows = swap(rows, "Phase zwei", None)
    x = scan(rows)
    assert x.lines_summarised and len(x.lines) == 1


def test_half_up_line_amount_is_accepted():
    # 3 x 0,335 would be 1,005 -> 1,01 half-up; prices have two decimals, so use 1,5 x 0,67 = 1,005 -> 1,01
    rows = [
        *INVOICE[:11],
        "Pos Beschreibung Menge Einzelpreis Gesamt",
        "1 Material 1,5 kg 0,67 1,01",
        "Nettobetrag 1,01 EUR",
        "zzgl. 19% MwSt 0,19 EUR",
        "Gesamtbetrag 1,20 EUR",
    ]
    x = scan(rows)
    assert not x.lines_summarised and x.lines[0].amount == D("1.01")
    assert evaluate(x, TODAY) == ([], [])  # 19 % of 1,01 = 0,1919 -> 0,19; 1,01 + 0,19 = 1,20


def test_line_without_quantity_and_price_uses_the_amount():
    rows = swap(INVOICE, "1 Beratung", "1 Beratung pauschal 500,00")
    x = scan(rows)
    assert x.lines[0] == DocLine("Beratung pauschal", D("1"), None, D("500.00"), D("500.00"))


def test_totals_vat_rounds_half_up():
    # net 10,50 x 19 % = 1,995 -> 2,00; gross 12,50
    rows = [
        *INVOICE[:11],
        "Pos Beschreibung Menge Einzelpreis Gesamt",
        "1 Teil 1 Stk 10,50 10,50",
        "Nettobetrag 10,50 EUR",
        "zzgl. 19% MwSt 2,00 EUR",
        "Gesamtbetrag 12,50 EUR",
    ]
    assert flags_of(scan(rows)) == []
    wrong = swap(rows, "zzgl.", "zzgl. 19% MwSt 1,99 EUR")
    wrong = swap(wrong, "Gesamtbetrag", "Gesamtbetrag 12,49 EUR")
    assert flags_of(scan(wrong)) == ["totals_mismatch"]  # 1,99 is not the half-up VAT


def test_totals_vat_tie_discriminates_half_up_from_half_even():
    # net 1,50 x 7 % = 0,105 -> 0,11 half-up (half-even would give 0,10); gross 1,61
    rows = [
        *INVOICE[:11],
        "Pos Beschreibung Menge Einzelpreis Gesamt",
        "1 Teil 1 Stk 1,50 1,50",
        "Nettobetrag 1,50 EUR",
        "zzgl. 7% MwSt 0,11 EUR",
        "Gesamtbetrag 1,61 EUR",
    ]
    assert flags_of(scan(rows)) == []
    half_even = swap(swap(rows, "zzgl.", "zzgl. 7% MwSt 0,10 EUR"), "Gesamtbetrag", "Gesamtbetrag 1,60 EUR")
    assert flags_of(scan(half_even)) == ["totals_mismatch"]


def test_gross_not_net_plus_vat_is_a_mismatch():
    assert flags_of(scan(swap(INVOICE, "Gesamtbetrag", "Gesamtbetrag 892,51 EUR"))) == ["totals_mismatch"]


def test_missing_totals_are_a_mismatch():
    assert "totals_mismatch" in flags_of(scan(swap(INVOICE, "Nettobetrag", None)))
    assert "totals_mismatch" in flags_of(scan(swap(INVOICE, "Gesamtbetrag", None)))


def test_net_label_variants():
    for label in ("Summe netto", "Zwischensumme", "Nettosumme"):
        x = scan(swap(INVOICE, "Nettobetrag", f"{label} 750,00 EUR"))
        assert x.net == D("750.00"), label


def test_competing_totals_are_ambiguous():
    rows = swap(INVOICE, "Gesamtbetrag", ["Gesamtbetrag 892,50 EUR", "Rechnungsbetrag 900,00 EUR"])
    x = scan(rows)
    assert x.competing_totals and "ambiguous_total" in flags_of(x)
    assert "totals_mismatch" not in flags_of(x)


def test_small_business_has_rate_zero_and_no_vat_flag():
    rows = [
        *INVOICE[:11],
        "Pos Beschreibung Menge Einzelpreis Gesamt",
        "1 Beratung 10 Std 50,00 500,00",
        "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.",
        "Gesamtbetrag 500,00 EUR",
    ]
    x = scan(rows)
    assert x.small_business
    assert [(g.rate, g.vat) for g in x.vat_groups] == [(D("0"), D("0"))]
    assert (x.net, x.gross) == (D("500.00"), D("500.00"))
    assert evaluate(x, TODAY) == ([], [])


def test_several_vat_rates():
    rows = swap(
        INVOICE,
        "zzgl. 19%",
        ["zzgl. 19% MwSt auf 500,00 EUR 95,00 EUR", "zzgl. 7% MwSt auf 250,00 EUR 17,50 EUR"],
    )
    rows = swap(rows, "Gesamtbetrag", "Gesamtbetrag 862,50 EUR")
    x = scan(rows)
    assert {g.rate for g in x.vat_groups} == {D("19"), D("7")}
    assert "several_vat_rates" in flags_of(x)
    assert "totals_mismatch" not in flags_of(x)


def test_foreign_currency():
    rows = swap(INVOICE, "Gesamtbetrag", "Gesamtbetrag 892,50 USD")
    rows = swap(rows, "Nettobetrag", "Nettobetrag 750,00 USD")
    rows = swap(rows, "zzgl.", "zzgl. 19% MwSt 142,50 USD")
    x = scan(rows)
    assert x.currency == "USD" and "foreign_currency" in flags_of(x)


def _group_doc(rate: str, net: str, vat: str, gross: str, day: str, service: str | None = None) -> list:
    """``service`` is the text after "Leistungszeitraum:"; None keeps the service of the day (no service line)."""
    return [
        *INVOICE[:8],
        f"Rechnungsdatum: {day}",
        f"Leistungsdatum: {service or day}",
        INVOICE[10],
        "Pos Beschreibung Menge Einzelpreis Gesamt",
        f"1 Leistung 1 Stk {net} {net}",
        f"Nettobetrag {net} EUR",
        f"zzgl. {rate}% MwSt {vat} EUR",
        f"Gesamtbetrag {gross} EUR",
    ]


def test_allowed_and_historic_rates():
    # 16 % of 100,00 = 16,00 (only 2020-07-01 .. 2020-12-31); 5 % of 100,00 = 5,00
    assert flags_of(scan(_group_doc("16", "100,00", "16,00", "116,00", "01.09.2020"))) == []
    assert flags_of(scan(_group_doc("5", "100,00", "5,00", "105,00", "31.12.2020"))) == []
    assert flags_of(scan(_group_doc("16", "100,00", "16,00", "116,00", "30.06.2020"))) == ["vat_rate_unusual"]
    assert flags_of(scan(_group_doc("16", "100,00", "16,00", "116,00", "05.03.2024"))) == ["vat_rate_unusual"]
    assert flags_of(scan(_group_doc("7", "100,00", "7,00", "107,00", "05.03.2024"))) == []
    assert flags_of(scan(_group_doc("0", "100,00", "0,00", "100,00", "05.03.2024"))) == []
    assert flags_of(scan(_group_doc("13", "100,00", "13,00", "113,00", "05.03.2024"))) == ["vat_rate_unusual"]


@pytest.mark.parametrize(
    ("service", "day", "unusual"),
    [
        ("30.11.2020", "10.01.2021", False),  # service in the window, invoice after it
        ("30.06.2020", "02.07.2020", True),  # invoice in the window, service before it
        ("01.07.2020", "01.07.2020", False),  # first day of the window
        ("30.06.2020", "30.06.2020", True),  # day before
        ("31.12.2020", "31.12.2020", False),  # last day
        ("01.01.2021", "01.01.2021", True),  # day after
    ],
)
def test_historic_rate_follows_the_service_date(service, day, unusual):
    x = scan(_group_doc("16", "100,00", "16,00", "116,00", day, service))
    assert flags_of(x) == (["vat_rate_unusual"] if unusual else [])


def test_historic_rate_service_period_end_decides_over_start():
    x = scan(_group_doc("16", "100,00", "16,00", "116,00", "10.01.2021", "01.12.2020 - 31.12.2020"))
    assert (x.service_from, x.service_to) == ("2020-12-01", "2020-12-31") and flags_of(x) == []
    y = scan(_group_doc("16", "100,00", "16,00", "116,00", "10.01.2021", "01.12.2020 - 05.01.2021"))
    assert flags_of(y) == ["vat_rate_unusual"]


def test_no_vat_rate_found():
    rows = swap(INVOICE, "zzgl.", None)
    assert "no_vat" in flags_of(scan(rows))


# ---- e-invoice ---------------------------------------------------------------------------------------------------

OWN_UBL = (
    UBL.replace("Musterhotel GmbH", "Max Muster IT-Beratung")
    .replace("DE123456789", "DE123456789")
    .replace(
        "</cac:AccountingSupplierParty>",
        "</cac:AccountingSupplierParty>"
        "<cac:AccountingCustomerParty><cac:Party>"
        "<cac:PostalAddress><cbc:StreetName>Kundenweg 5</cbc:StreetName><cbc:CityName>Kundenstadt</cbc:CityName>"
        "<cbc:PostalZone>54321</cbc:PostalZone><cac:Country><cbc:IdentificationCode>DE</cbc:IdentificationCode></cac:Country>"
        "</cac:PostalAddress>"
        "<cac:PartyTaxScheme><cbc:CompanyID>DE555666777</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>"
        "<cac:PartyLegalEntity><cbc:RegistrationName>Beispiel Kunde GmbH</cbc:RegistrationName></cac:PartyLegalEntity>"
        "<cac:Contact><cbc:ElectronicMail>kunde@example.invalid</cbc:ElectronicMail></cac:Contact>"
        "</cac:Party></cac:AccountingCustomerParty>",
    )
)


def einv(xml: str, words: list[Word] | None = None) -> DocExtraction:
    d = read_einvoice(xml.encode())
    r = ReadResult(engine="einvoice", words=words or [], extraction=d.extraction, pages_read=1, pages_total=1)
    return parse(r, OWN, d)


def test_einvoice_seller_buyer_and_summarised_lines():
    x = einv(OWN_UBL)
    assert (x.kind, x.number, x.date, x.due_date, x.currency) == (
        "invoice",
        "RE-2026-0042",
        "2026-10-07",
        "2026-11-04",
        "EUR",
    )
    assert x.sender_ok
    assert x.recipient == Recipient(
        "Beispiel Kunde GmbH", "Kundenweg 5", "54321", "Kundenstadt", "DE", "DE555666777", "kunde@example.invalid"
    )
    # BT-109 149,53 differs from the line sum 150,00 (allowance and charge): one summary line
    assert (x.net, x.vat_total, x.gross) == (D("149.53"), D("10.47"), D("160.00"))
    assert x.lines_summarised and x.lines[0].price == D("149.53")
    # 7 % of 149,53 = 10,4671 -> 10,47; 149,53 + 10,47 = 160,00: consistent, only the lines are not proven
    assert evaluate(x, TODAY) == ([], ["lines_summarised"])


def test_einvoice_credit_note_and_foreign_seller():
    credit = einv(OWN_UBL.replace("<cbc:InvoiceTypeCode>380<", "<cbc:InvoiceTypeCode>381<"))
    assert credit.kind == "credit_note" and "kind_unsupported" in flags_of(credit)
    correction = einv(OWN_UBL.replace("<cbc:InvoiceTypeCode>380<", "<cbc:InvoiceTypeCode>384<"))
    assert correction.kind == "correction" and "kind_unsupported" in flags_of(correction)
    foreign = einv(
        OWN_UBL.replace("Max Muster IT-Beratung", "Anderer Anbieter GmbH").replace("DE123456789", "DE111222333")
    )
    assert not foreign.sender_ok and "not_my_document" in flags_of(foreign)
    # the seller can also be proven by the PDF text of page 1
    assert (
        einv(OWN_UBL.replace("DE123456789", "DE111222333").replace("Max Muster IT-Beratung", "X Y")).sender_ok is False
    )
    words = doc(["Max Muster IT-Beratung"])
    assert einv(OWN_UBL.replace("DE123456789", "DE111222333").replace("Max Muster IT-Beratung", "X Y"), words).sender_ok


def test_einvoice_buyer_is_cut_by_the_caps():
    long = "A" * 5000
    xml = OWN_UBL.replace("<cbc:RegistrationName>Beispiel Kunde GmbH<", f"<cbc:RegistrationName>{long}<")
    xml = xml.replace("<cbc:StreetName>Kundenweg 5<", "<cbc:StreetName>Weg\t\x7f 1<")
    r = einv(xml).recipient
    assert r is not None and 0 < len(r.name) <= 200
    assert r.street is not None and not any(ord(c) < 32 for c in r.street)


def test_einvoice_clean_lines_are_kept():
    # 2 x 50,00 = 100,00 net; 19 % = 19,00; gross 119,00
    xml = (
        OWN_UBL.replace('currencyID="EUR">10.47', 'currencyID="EUR">19.00')
        .replace(">149.53<", ">100.00<")
        .replace(">150.00<", ">100.00<")
        .replace(">160.00<", ">119.00<")
        .replace("<cbc:Percent>7<", "<cbc:Percent>19<")
        .replace('<cbc:AllowanceTotalAmount currencyID="EUR">1.00</cbc:AllowanceTotalAmount>', "")
        .replace('<cbc:ChargeTotalAmount currencyID="EUR">0.53</cbc:ChargeTotalAmount>', "")
    )
    # drop the second invoice line (50,00) so that the single line 2 x 50,00 = 100,00 proves the net
    start = xml.index("<cac:InvoiceLine>", xml.index("<cac:InvoiceLine>") + 1)
    xml = xml[:start] + "</Invoice>\n"
    x = einv(xml)
    assert x.lines == [DocLine("Zimmer", D("2"), None, D("50.00"), D("100.00"))] and not x.lines_summarised
    assert (x.net, x.vat_total, x.gross) == (D("100.00"), D("19.00"), D("119.00"))
    assert evaluate(x, TODAY) == ([], [])


def test_einvoice_without_buyer_object_has_no_customer():
    d = read_einvoice(OWN_UBL.encode())
    x = parse(ReadResult(engine="einvoice", extraction=d.extraction, pages_read=1, pages_total=1), OWN)
    assert x.recipient is None and x.kind == "invoice" and "no_customer" in flags_of(x)


# ---- a real PDF through the worker -------------------------------------------------------------------------------


def test_pdf_generated_in_the_test(tmp_path):
    from permita38.core.config import ScanConfig
    from permita38.core.pdf import Pdf
    from permita38.core.receipts import runner

    # (text, x pt, baseline y pt from top, size); net 100,00 + 19 % (19,00) = 119,00
    lines = [
        ("Max Muster IT-Beratung - Musterweg 1 - 12345 Musterstadt", 60, 100, 7),
        ("Beispiel Kunde GmbH", 60, 130, 11),
        ("Kundenweg 5", 60, 144, 11),
        ("54321 Kundenstadt", 60, 158, 11),
        ("Angebot", 60, 220, 22),
        ("Angebotsnummer: A-2024-031", 60, 250, 11),
        ("Angebotsdatum: 02.05.2024", 60, 264, 11),
        ("Die Rechnung stellen wir nach Abschluss.", 60, 278, 11),
        ("Pos Beschreibung Menge Einzelpreis Gesamt", 60, 400, 11),
        ("1 Beratung 2 Std 50,00 100,00", 60, 416, 11),
        ("Nettobetrag 100,00 EUR", 60, 520, 11),
        ("zzgl. 19% MwSt 19,00 EUR", 60, 534, 11),
        ("Gesamtbetrag 119,00 EUR", 60, 548, 11),
    ]
    pdf = Pdf()
    for text, x, y, size in lines:
        pdf.text(x, y, text, size=size)
    path = tmp_path / "offer.pdf"
    path.write_bytes(pdf.output())
    res = runner.read_file(str(path), ScanConfig(timeout_s=10, tesseract_path="/nonexistent/tesseract"))
    assert res.engine == "pdf_text"
    x = parse(res, OWN)
    assert (x.kind, x.number, x.date) == ("offer", "A-2024-031", "2024-05-02")
    assert x.recipient is not None and x.recipient.name == "Beispiel Kunde GmbH"
    assert (x.net, x.vat_total, x.gross) == (D("100.00"), D("19.00"), D("119.00"))
    assert x.lines == [DocLine("Beratung", D("2"), "Std", D("50.00"), D("100.00"))]
    assert x.sender_ok


# ---- robustness --------------------------------------------------------------------------------------------------


def test_unreadable_and_empty_input():
    assert evaluate(parse(ReadResult(), OWN)) == (["unreadable"], [])
    assert evaluate(parse(ReadResult(engine="pdf_text", error="timeout"), OWN)) == (["unreadable"], [])
    empty = parse(read([]), OWN)
    flags = flags_of(empty)
    assert {"kind_unclear", "no_number", "no_date", "no_customer", "not_my_document"} <= set(flags)
    assert evaluate(DocExtraction(engine="pdf_text"), TODAY)[0]  # never raises on an empty extraction


def test_partly_read_document_is_flagged():
    r = read(doc(INVOICE))
    r.pages_total = 3
    assert "pages_skipped" in flags_of(parse(r, OWN))


def test_odd_input_never_raises():
    junk = [
        "Rechnung " * 300,
        "Rechnungsnummer: " + "9" * 500,
        "Nettobetrag " + "9" * 60 + ",99",
        "1 Beratung 99999999999999999999999 Std 99999999999999999999,99 99999999999999999999,99",
        "zzgl. 99% MwSt 0,00 EUR",
        "Datum: 31.02.2024 Datum: 99.99.9999",
        "NaN inf -0,00 € € € %",
        "‮\x00\ud800 Rechnung",
        "zahlbar innerhalb von 999 Tagen",
    ]
    rows = [(r, 0.04) if i == 0 else r for i, r in enumerate(junk * 3)]
    x = parse(read(doc(rows)), OWN)
    evaluate(x, TODAY)
    # a huge word list is bounded
    many = [Word("Rechnung", 0, 0.1, 0.1, 0.05, 0.02)] * 20000
    evaluate(parse(read(many), OWN), TODAY)
    evaluate(parse(read(doc(["Rechnung 01.01.2024"] * 700)), OWN), TODAY)


def test_multi_page_table_continues_and_stops_at_the_totals():
    page1 = doc([*INVOICE[:13], "1 Beratung 10 Std 50,00 500,00"], page=0)
    page2 = doc(
        [
            "Pos Beschreibung Menge Einzelpreis Gesamt",
            "2 Entwicklung Modul A 4 Std 62,50 250,00",
            "Nettobetrag 750,00 EUR",
            "zzgl. 19% MwSt 142,50 EUR",
            "Gesamtbetrag 892,50 EUR",
        ],
        page=1,
    )
    r = ReadResult(engine="pdf_text", words=page1 + page2, pages_read=2, pages_total=2)
    x = parse(r, OWN)
    assert [ln.amount for ln in x.lines] == [D("500.00"), D("250.00")]
    assert evaluate(x, TODAY) == ([], [])


def test_parse_uses_buyer_and_type_code_of_the_extraction():
    from permita38.core.receipts.extraction import Extraction, Party, ReadResult, read_result_from_json, to_jsonable

    ex = Extraction(vendor="Muster GmbH", type_code="381", buyer=Party(name="Kunde AG", city="Köln", vat_id="DE1"))
    back = read_result_from_json(to_jsonable(ReadResult("einvoice", extraction=ex, pages_read=1, pages_total=1)))
    assert back.extraction.buyer == ex.buyer and back.extraction.type_code == "381"
    x = parse(back, OwnIdentity())
    assert x.kind == "credit_note"
    assert x.recipient is not None and x.recipient.name == "Kunde AG" and x.recipient.city == "Köln"


# ---- layouts of old documents (invented values, structure only) --------------------------------------------------


def _head(*extra):
    return [
        SENDER,
        None,
        *CUSTOMER,
        None,
        ("Rechnung", 0.04),
        "Rechnungsnummer: RE-9",
        "Rechnungsdatum: 02.04.2025",
        *extra,
    ]


def _tail(net: str = "3.800,00"):
    return ["Nettobetrag " + net + " EUR", "Gesamtbetrag " + net + " EUR"]


def _small(rows):
    return scan([*rows, "Gemäß § 19 UStG wird keine Umsatzsteuer ausgewiesen."])


def test_label_line_an_is_not_the_customer():
    for label in ("An", "An:", "To"):
        x = scan(
            [SENDER, None, label, "Beispiel Kunde GmbH", "Kundenweg 5", "54321 Kundenstadt", None, ("Rechnung", 0.04)]
        )
        assert x.recipient == Recipient("Beispiel Kunde GmbH", "Kundenweg 5", "54321", "Kundenstadt", None, None, None)


def _words(text: str, x: float, y: float, h: float = 0.02) -> list[Word]:
    out = []
    for token in text.split():
        w = round(0.012 * len(token), 4)
        out.append(Word(token, 0, round(x, 4), y, w, h))
        x += w + 0.012
    return out


def test_two_columns_interleaved_take_only_the_left_one():
    left = ["Kunde Testwerk GmbH", "Herrn", "Gartenweg 4", "13579 Teststadt", "Deutschland"]
    right = ["Erika Beispiel", "Beispielweg 2", "54321 Beispielstadt"]
    words = _words(SENDER[0], 0.05, 0.04, 0.012)
    for i, t in enumerate(left):
        words += _words(t, 0.05, 0.12 + i * 0.03)
    for i, t in enumerate(right):  # rows between the rows of the left block
        words += _words(t, 0.55, 0.135 + i * 0.03)
    words += doc([("Rechnung", 0.04)], top=0.4)
    own = OwnIdentity("Max Muster IT-Beratung", "Erika Beispiel", "DE123456789", "")
    x = parse(read(words), own)
    assert x.recipient == Recipient("Kunde Testwerk GmbH", "Gartenweg 4", "13579", "Teststadt", "DE", None, None)


def test_contact_line_is_not_the_name_and_house_number_joins_the_street():
    rows = [
        SENDER,
        None,
        "Frau",
        "Beispiel Handel AG",
        "Lindenallee",
        "17",
        "24680 Lindenstadt",
        None,
        ("Rechnung", 0.04),
    ]
    r = scan(rows).recipient
    assert r is not None and (r.name, r.street, r.zip) == ("Beispiel Handel AG", "Lindenallee 17", "24680")


@pytest.mark.parametrize(
    "note",
    [
        ["Gemäß §19 Abs. 1 UstG wird keine Umsatzsteuer ausgewiesen."],
        ["Gemäß § 19 UStG enthält der Rechnungsbetrag", "keine Umsatzsteuer."],
        ["Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.", "Bei einem Wechsel (dann 19 %) erhalten Sie Nachricht."],
    ],
)
def test_small_business_variants_win_over_a_rate_mention(note):
    x = scan([*_head(), None, "Pos Beschreibung Betrag", "1 Beratung 3.800,00", *_tail(), *note])
    assert x.small_business and [g.rate for g in x.vat_groups] == [D("0")]
    assert evaluate(x, TODAY) == ([], [])  # net = gross = 3.800,00, VAT 0


def test_small_business_with_gross_label_only():
    x = _small([*_head(), None, "Pos Beschreibung Betrag", "1 Beratung 3.800,00", "Summe 3.800,00 EUR"])
    assert (x.net, x.vat_total, x.gross) == (D("3800.00"), D("0"), D("3800.00"))
    assert "totals_mismatch" not in flags_of(x)


@pytest.mark.parametrize(
    "text, value",
    [("1250,-", "1250.00"), ("2.250,-", "2250.00"), ("480,00 Euro", "480.00"), ("480,00 EUR", "480.00")],
)
def test_amount_forms_as_total_and_as_line_amount(text, value):
    x = scan(
        [
            *_head(),
            None,
            "Pos Beschreibung Betrag",
            "1 Beratung " + text,
            "Nettobetrag " + text,
            "Gesamtbetrag " + text,
            "Gemäß § 19 UStG keine Umsatzsteuer.",
        ]
    )
    assert x.gross == D(value) and x.net == D(value)
    assert x.lines == [DocLine("Beratung", D("1"), None, D(value), D(value))]
    assert evaluate(x, TODAY) == ([], [])


def test_number_from_the_title_line_only_without_a_label():
    rows = [SENDER, None, *CUSTOMER, None, ("Rechnung 2025-3-XYZ", 0.04), "Rechnungsdatum: 02.04.2025"]
    assert scan(rows).number == "2025-3-XYZ"
    labelled = swap(rows, "Rechnungsdatum", ["Rechnungsnummer: RE-9", "Rechnungsdatum: 02.04.2025"])
    assert scan(labelled).number == "RE-9"


def test_due_date_in_a_payment_sentence_and_due_days_forms():
    x = scan([*_head(), "Bitte überweisen Sie den Betrag bis zum 16.05.2025 auf das Konto."])
    assert x.due_date == "2025-05-16"
    for sentence in (
        "Bitte überweisen Sie den Betrag innerhalb der nächsten 21 Tage.",
        "Zahlung bitte innerhalb von 21 Tagen.",
        "Bitte überweisen Sie innerhalb 21 Tagen.",
    ):
        y = scan([*_head(), sentence])
        assert (y.due_days, y.due_date) == (21, "2025-04-23"), sentence  # 02.04. + 21 days


def test_service_period_label_variants():
    x = scan([*_head("Leistungszeit: 03.02.2025-28.02.2025")])
    assert (x.service_from, x.service_to) == ("2025-02-03", "2025-02-28")
    y = scan([*_head("Zeitraum: Beginn 06.01.2025, Ende 21.03.2025")])
    assert (y.service_from, y.service_to) == ("2025-01-06", "2025-03-21")


def test_split_header_and_description_wrapped_over_rows():
    rows = [
        *_head(),
        None,
        "€ Gesamt €",
        "Pos. Bezeichnung Menge Einh. Einzel",
        "1 Beratung und 40 Std 95,00 3.800,00",  # 40 x 95,00 = 3.800,00
        "Konzept der Umstellung",
        "Begleitung der Teams",
        "Abstimmung mit Partnern",
        "Dokumentation",
        *_tail(),
        "Gemäß § 19 UStG keine Umsatzsteuer.",
    ]
    x = scan(rows)
    assert len(x.lines) == 1 and not x.lines_summarised
    ln = x.lines[0]
    assert (ln.quantity, ln.unit, ln.price, ln.amount) == (D("40"), "Std", D("95.00"), D("3800.00"))
    assert "Dokumentation" in ln.description


def test_bullet_description_above_the_single_amount():
    rows = [
        *_head(),
        None,
        "Pos Bezeichnung Betrag €",
        "1 Webseite erstellen",
        "- Entwurf",
        "- Umsetzung",
        "1250,-",
        "Nettobetrag 1250,- EUR",
        "Gesamtbetrag 1250,- EUR",
        "Gemäß § 19 UStG keine Umsatzsteuer.",
    ]
    x = scan(rows)
    assert not x.lines_summarised and len(x.lines) == 1
    ln = x.lines[0]
    assert (ln.quantity, ln.price, ln.amount) == (D("1"), D("1250.00"), D("1250.00"))
    assert "Umsetzung" in ln.description
