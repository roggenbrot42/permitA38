"""Text rules and evaluate(): the oracles of docs/specs/receipt-scan.md as literal numbers. All data is fake."""

import copy
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from permita38.core.money import split_gross
from permita38.core.receipts import categories
from permita38.core.receipts.extraction import Adjustment, Extraction, Item, ReadResult, VatGroup, Word
from permita38.core.receipts.flags import FLAGS
from permita38.core.receipts.rules import evaluate, payment_kind
from permita38.core.receipts.textrules import clean_text, parse, parse_amount
from tests.backend.receipt_helpers import bill, context, read

TODAY = date(2026, 11, 2)


def base(**over) -> Extraction:
    """A clean single-rate bill: 11,90 at 19 %, printed VAT 1,90."""
    x = Extraction(
        vendor="Beispiel Handel GmbH",
        invoice_no="2026-77",
        bill_date="2026-10-12",
        total=D("11.90"),
        vat_groups=[VatGroup(rate=D("19"), vat=D("1.90"))],
        payment_signals=["open"],
    )
    for k, v in over.items():
        setattr(x, k, v)
    return x


def hotel(**over) -> Extraction:
    x = Extraction(
        vendor="Beispiel Hotel GmbH",
        invoice_no="2026-1042",
        bill_date="2026-10-12",
        total=D("196.00"),
        text_signals=[],
        payment_signals=["cash"],
        vat_groups=[
            VatGroup(D("7"), None, None, D("10.47"), D("160.00")),
            VatGroup(D("19"), None, None, D("4.79"), D("30.00")),
            VatGroup(D("0"), None, None, None, D("6.00")),
        ],
    )
    for k, v in over.items():
        setattr(x, k, v)
    return x


def till(**over) -> Extraction:
    x = Extraction(
        vendor="Beispiel Markt GmbH",
        invoice_no="8841",
        bill_date="2026-10-12",
        total=D("17.70"),
        tendered=D("20.00"),
        change=D("2.30"),
        payment_signals=["cash"],
        items=[
            Item("Kaffee", D("2"), D("3.40"), D("6.80"), "A"),
            Item("Brot", None, None, D("11.90"), "B"),
            Item("Rabatt", None, None, D("-1.00"), "B"),
        ],
        vat_groups=[
            VatGroup(D("7"), "A", D("6.36"), D("0.44"), D("6.80")),
            VatGroup(D("19"), "B", D("9.16"), D("1.74"), D("10.90")),
        ],
    )
    for k, v in over.items():
        setattr(x, k, v)
    return x


def run(x: Extraction, **ctx):
    return evaluate(read([Word("x", 0, 0, 0, 0.1, 0.1)]), x, context(**ctx))


def codes(ev) -> list[str]:
    return [f["code"] for f in ev.flags]


# ---------------------------------------------------------------------------------------------------- oracles


def test_hotel_oracle_booked_clean():
    ev = run(hotel())
    assert codes(ev) == [] and ev.clean
    d = ev.draft
    assert d["gross"] == 196.0 and d["vat_rate"] is None
    assert [(ln["gross"], ln["vat_rate"]) for ln in d["lines"]] == [(160.0, 7.0), (30.0, 19.0), (6.0, 0.0)]
    parts = split_gross([(D("160.00"), D("7")), (D("30.00"), D("19")), (D("6.00"), D("0"))])
    assert sum(n for n, _ in parts) == D("180.74") and sum(v for _, v in parts) == D("15.26")
    assert d["category"] == "Accommodation"


def test_single_rate_oracle_classic_expense():
    ev = run(base())
    assert codes(ev) == []
    assert ev.draft["gross"] == 11.9 and ev.draft["vat_rate"] == 19.0 and "lines" not in ev.draft
    ((net, vat),) = split_gross([(D("11.90"), D("19"))])
    assert (net, vat) == (D("10.00"), D("1.90"))


def test_till_receipt_oracle():
    ev = run(till())
    assert codes(ev) == []
    assert ev.draft["paid_on"] == "2026-10-12"
    assert [(ln["gross"], ln["vat_rate"]) for ln in ev.draft["lines"]] == [(6.8, 7.0), (10.9, 19.0)]
    parts = split_gross([(D("6.80"), D("7")), (D("10.90"), D("19"))])
    assert parts == [(D("6.36"), D("0.44")), (D("9.16"), D("1.74"))]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda x: setattr(x.items[0], "amount", D("6.90")), ["items_mismatch"]),
        (lambda x: setattr(x, "change", D("2.20")), ["payment_mismatch"]),
        (
            lambda x: (setattr(x.vat_groups[0], "vat", D("0.43")), setattr(x.vat_groups[0], "net", D("6.37"))),
            ["vat_mismatch"],
        ),
        (
            lambda x: setattr(x, "total", D("17.80")),
            ["total_mismatch", "items_mismatch", "payment_mismatch", "not_verifiable"],
        ),
    ],
)
def test_till_failures(mutate, expected):
    x = till()
    mutate(x)
    ev = run(x)
    assert codes(ev) == expected and not ev.clean


def test_hotel_with_deposit():
    ev = run(hotel(prepaid=D("50.00"), amount_due=D("146.00"), payment_signals=["prepaid"]))
    assert codes(ev) == ["payment_unclear"]
    ev = run(hotel(prepaid=D("50.00"), amount_due=D("151.00"), payment_signals=["prepaid"]))
    assert codes(ev) == ["payment_mismatch", "payment_unclear"]


def test_business_meal_oracle():
    x = Extraction(
        vendor="Beispiel Restaurant", invoice_no="55", bill_date="2026-10-12", total=D("100.00"),
        text_signals=["meal"], vat_groups=[VatGroup(rate=D("19"), vat=D("15.97"))],
    )  # fmt: skip
    ev = run(x)
    assert codes(ev) == []
    d = ev.draft
    assert (d["category"], d["deductible"], d["vat_deductible"], d["gross"]) == ("Business meals", 70, 100, 100.0)
    ((net, vat),) = split_gross([(D("100.00"), D("19"))])
    assert (net, vat) == (D("84.03"), D("15.97"))
    assert (net * D("0.7")).quantize(D("0.01")) == D("58.82")


def test_business_meal_lines_carry_the_shares():
    x = hotel(vendor="Gaststätte Beispiel")
    ev = run(x)
    assert ev.draft["category"] == "Business meals"
    assert all((ln["deductible"], ln["vat_deductible"]) == (70, 100) for ln in ev.draft["lines"])


def test_other_categories_are_fully_deductible():
    ev = run(base())
    assert (ev.draft["deductible"], ev.draft["vat_deductible"]) == (100, None)


def test_not_verifiable_oracle():
    x = base(total=D("23.80"), vat_groups=[VatGroup(rate=D("19"))])
    ev = run(x)
    assert codes(ev) == ["not_verifiable"] and not ev.clean


def test_zero_group_beside_a_taxed_group_is_not_vat_free():
    x = base(
        total=D("21.90"),
        items=[Item("Brot", None, None, D("10.00"), None), Item("Kaffee", None, None, D("11.90"), None)],
        vat_groups=[VatGroup(rate=D("0")), VatGroup(rate=D("19"), vat=D("1.90"))],
    )
    ev = run(x)
    assert "not_verifiable" in codes(ev) and not ev.clean


# ---------------------------------------------------------------------------------------------------- payment kind


@pytest.mark.parametrize(
    "over, kind, paid_on",
    [
        ({"payment_signals": ["cash"], "text_signals": []}, "cash", "2026-10-12"),
        ({"payment_signals": ["card"], "text_signals": []}, "cash", "2026-10-12"),
        ({"payment_signals": [], "text_signals": ["meal"]}, "cash", "2026-10-12"),  # restaurant, no payment word
        ({"payment_signals": ["paid"], "paid_date": "2026-10-23"}, "stated", "2026-10-23"),
        ({"paid_date": "2026-10-23", "payment_signals": []}, "stated", "2026-10-23"),
        ({"payment_signals": ["open"]}, "invoice", None),
        ({"payment_signals": [], "due_date": "2026-10-28"}, "invoice", None),
        ({"payment_signals": [], "due_date": "2026-10-12"}, "unknown", None),  # not after the bill date
        ({"payment_signals": [], "amount_due": D("11.90")}, "invoice", None),
        ({"payment_signals": []}, "unknown", None),
        ({"payment_signals": ["cash", "open"]}, "conflict", None),
        ({"payment_signals": ["prepaid"]}, "conflict", None),
        ({"payment_signals": [], "prepaid": D("5.00"), "amount_due": D("6.90")}, "conflict", None),
    ],
)
def test_payment_kind(over, kind, paid_on):
    x = base(**over)
    assert payment_kind(x) == kind
    ev = run(x)
    assert ev.doc["payment"]["kind"] == kind and ev.draft["paid_on"] == paid_on
    assert ("payment_unclear" in codes(ev)) == (kind == "conflict")
    if kind in ("invoice", "unknown"):
        assert codes(ev) == []


# ---------------------------------------------------------------------------------------------------- invoice number


@pytest.mark.parametrize(
    "total, number, flagged",
    [
        (D("250.00"), None, False),
        (D("250.01"), None, True),
        (D("0.01"), None, False),
        (None, None, True),
        (D("250.01"), "R-1", False),
    ],
)
def test_invoice_number_boundary(total, number, flagged):
    x = base(
        total=total,
        invoice_no=number,
        vat_groups=[VatGroup(rate=D("19"), vat=None)]
        if total is None
        else [VatGroup(rate=D("19"), vat=((total * 19 / 119).quantize(D("0.01"))))],
    )
    assert ("no_invoice_number" in codes(run(x))) == flagged


@pytest.mark.parametrize(
    "lines, expected",
    [
        (["Rechnungsnummer: 2026-1042"], "2026-1042"),
        (["Rechnung Nr. R-77"], "R-77"),
        (["Rechnungs-Nr 4711"], "4711"),
        (["Invoice no. INV-5"], "INV-5"),
        (["Invoice number: 12345"], "12345"),
        (["Beleg-Nr 8841"], "8841"),
        (["Bon-Nr: 0042"], "0042"),
        (["Kassenbon-Nr. 17"], "17"),
        (["TSE Transaktion 998877"], "998877"),
        (["Transaktionsnummer: 555"], "555"),
        (["Beleg-Nr 8841", "Rechnungsnummer A-1"], "A-1"),  # an invoice label beats a till label
    ],
)
def test_invoice_number_labels(lines, expected):
    assert parse(bill(["Beispiel Laden"] + lines)).invoice_no == expected


def test_invoice_key():
    a = run(base(vat_id="DE123456789", vendor="A"))
    b = run(base(vat_id="de123456789", vendor="B"))
    c = run(base(vat_id=None, vendor="Beispiel Handel GmbH"))
    assert a.invoice_key == b.invoice_key and len(a.invoice_key) == 64
    assert c.invoice_key and c.invoice_key != a.invoice_key
    assert run(base(invoice_no=None)).invoice_key is None


# ---------------------------------------------------------------------------------------------------- category


def test_category_order():
    # history beats the word table, the word table beats Other, a new vendor raises no flag
    x = base(vendor="Hotel Beispiel")
    assert run(x, history="Training").draft["category"] == "Training"
    ev = run(x)
    assert ev.draft["category"] == "Accommodation" and codes(ev) == []
    assert run(base(vendor="Beispiel Handel GmbH")).draft["category"] == "Other"
    # the vendor is looked at before the items
    y = base(vendor="Hotel Beispiel", items=[Item("Restaurant Abendessen", None, None, D("11.90"), None)])
    assert run(y).draft["category"] == "Accommodation"
    z = base(vendor="Beispiel GmbH", items=[Item("Taxi zum Bahnhof", None, None, D("11.90"), None)])
    assert run(z).draft["category"] == "Transportation"
    t = base(vendor="Beispiel GmbH", adjustments=[Adjustment("tip", D("1.00"))])
    assert run(t).draft["category"] == "Business meals"


@pytest.mark.parametrize(
    "vendor, category",
    [
        ("Pension Muster", "Accommodation"),
        ("Gasthaus Muster Übernachtung", "Accommodation"),
        ("Beispiel Tankstelle", "Transportation"),
        ("Deutsche Bahn", "Transportation"),
        ("Straßenbahn Muster", "Other"),  # a word matches only at the start of a word
        ("Parkhaus Mitte", "Transportation"),
        ("Café Beispiel", "Business meals"),
        ("Restaurant Muster", "Business meals"),
    ],
)
def test_category_words(vendor, category):
    assert (categories.category_from_text(vendor) or "Other") == category
    assert (categories.category_from_text(vendor) or "Other") == category


def test_every_category_target_is_in_exp_cats():
    js = (Path(__file__).parents[2] / "web" / "app2.js").read_text(encoding="utf-8")
    block = re.search(r"const EXP_CATS = \[(.*?)\];", js).group(1)
    exp = set(re.findall(r"'([^']+)'", block))
    assert categories.CATEGORY_TARGETS <= exp


# ---------------------------------------------------------------------------------------------------- flags


def _unreadable() -> ReadResult:
    return ReadResult(engine="none")


def flag_cases():
    yield "no_total", base(total=None, vat_groups=[VatGroup(rate=D("19"))]), {}
    yield "ambiguous_total", base(total=None, competing_totals=[D("11.90"), D("12.90")]), {}
    yield "no_date", base(bill_date=None), {}
    yield "ambiguous_date", base(bill_date=None, competing_dates=["2026-10-12", "2026-10-13"]), {}
    yield "date_implausible", base(bill_date="2026-11-03"), {}
    yield "date_implausible", base(bill_date="2024-12-31"), {}
    yield "no_vendor", base(vendor=None), {}
    yield (
        "no_invoice_number",
        base(invoice_no=None, total=D("300.00"), vat_groups=[VatGroup(rate=D("19"), vat=D("47.90"))]),
        {},
    )
    yield "no_vat", base(vat_groups=[]), {}
    yield "vat_rate_unusual", base(vat_groups=[VatGroup(rate=D("5"), vat=D("0.57"))]), {}
    yield "vat_mismatch", base(vat_groups=[VatGroup(rate=D("19"), vat=D("1.91"))]), {}
    yield "total_mismatch", base(subtotal=D("10.50"), total=D("11.90")), {}  # 10.50 is neither gross nor net
    yield "items_mismatch", base(items=[Item("a", None, None, D("11.00"), None)]), {}
    yield "payment_mismatch", base(card_amount=D("11.00")), {}
    yield "not_verifiable", base(vat_groups=[VatGroup(rate=D("19"))]), {}
    yield "payment_unclear", base(payment_signals=["cash", "open"]), {}
    yield "foreign_currency", base(currency="USD"), {}
    yield "credit_note", base(text_signals=["credit_note"]), {}
    yield "credit_note", base(total=D("-11.90"), vat_groups=[VatGroup(rate=D("19"), vat=D("-1.90"))]), {}
    yield "reverse_charge", base(text_signals=["reverse_charge"]), {}
    yield "possible_duplicate", base(), {"duplicate": True}


@pytest.mark.parametrize("code, x, ctx", list(flag_cases()), ids=lambda v: v if isinstance(v, str) else "")
def test_each_flag_once(code, x, ctx):
    ev = run(x, **ctx)
    assert code in codes(ev), codes(ev)
    assert not ev.clean
    assert {f["code"]: f["message"] for f in ev.flags}[code] == FLAGS[code]


def test_unreadable_pages_skipped_and_model_read():
    ev = evaluate(_unreadable(), Extraction(), context())
    assert codes(ev) == ["unreadable"] and not ev.clean
    ev = evaluate(ReadResult(engine="ocr", error="timeout"), base(), context())
    assert codes(ev) == ["unreadable"]
    ev = evaluate(ReadResult(engine="pdf_text", pages_read=50, pages_total=51), base(), context())
    assert codes(ev) == ["pages_skipped"]
    ev = evaluate(ReadResult(engine="vision", pages_read=1, pages_total=1), base(), context())
    assert codes(ev) == ["model_read"] and not ev.clean


def test_flag_messages_are_the_spec_text():
    assert FLAGS["no_invoice_number"] == "No invoice number found. Enter it, or confirm that the bill has none."
    assert FLAGS["not_verifiable"] == "Nothing on the bill confirms this total."
    assert FLAGS["items_mismatch"] == "The items do not add up."
    assert len(FLAGS) == 24


def test_the_closed_set_is_complete():
    spec = (Path(__file__).parents[2] / "docs" / "specs" / "receipt-scan.md").read_text(encoding="utf-8")
    table = spec.split("### Flags (closed set", 1)[1].split("### Duplicates", 1)[0]
    rows = re.findall(r"^\| `(\w+)` \|.*\| (.+?) \|$", table, re.M)
    assert {c for c, _ in rows} == set(FLAGS)
    for code, message in rows:
        assert FLAGS[code] == message


def test_a_new_vendor_and_clean_paths_raise_no_flag():
    for x in (base(), hotel(), till(), base(vat_groups=[], text_signals=["small_business"], items=[Item("Heft", None, None, D("5.90"), None), Item("Stift", None, None, D("6.00"), None)]),
              base(vat_groups=[VatGroup(rate=D("0"), gross=D("11.90"))], tendered=D("20.00"), change=D("8.10"))):  # fmt: skip
        ev = run(x)
        assert codes(ev) == [] and ev.clean, x


def test_possible_duplicate_probe_carries_vendor_date_and_gross():
    seen = []
    ctx = context()
    ctx.possible_duplicate = lambda d: seen.append(d) or False
    evaluate(read([Word("x", 0, 0, 0, 0.1, 0.1)]), base(), ctx)
    assert seen == [
        {
            "vendor": "Beispiel Handel GmbH",
            "date": "2026-10-12",
            "gross": 11.9,
            "invoice_no": "2026-77",
            "invoice_ocr": False,
        }
    ]


# ---------------------------------------------------------------------------------------------------- cleaning


def test_vendor_and_item_text_are_cleaned_and_bounded():
    hostile = "Beispiel‮evil​\x00GmbH\n" + "x" * 500
    ev = run(base(vendor=hostile, items=[Item("a⁦b\x07c " + "y" * 500, None, None, D("11.90"), None)]))
    v = ev.draft["vendor"]
    assert len(v) <= 200 and "‮" not in v and "​" not in v and "\x00" not in v and "\n" not in v
    assert ev.doc["items"][0]["description"].startswith("abc yyy") and len(ev.doc["items"][0]["description"]) <= 200


def test_clean_text():
    assert clean_text("  a\tb ‮ c\x00 ") == "a b c"
    assert clean_text("") is None and clean_text(None) is None
    assert len(clean_text("z" * 300)) == 200


def test_evaluation_does_not_modify_the_extraction():
    x = base(vendor="A‮B")
    before = copy.deepcopy(x)
    run(x)
    assert x == before


def test_doc_limits():
    items = [Item(f"i{n}", None, None, D("1.00"), None) for n in range(250)]
    x = base(items=items)
    x.sources = {f"item:{n}": [Word("w", 0, 0.1, 0.1, 0.1, 0.01)] for n in range(250)}
    ev = run(x)
    assert len(ev.doc["items"]) == 200
    assert len(ev.doc["boxes"]) <= 200 and len(ev.doc["boxes"]) == 200


# ---------------------------------------------------------------------------------------------------- text rules


@pytest.mark.parametrize(
    "text, value",
    [
        ("1.234,56", D("1234.56")),
        ("1,234.56", D("1234.56")),
        ("12,50", D("12.50")),
        ("12.50", D("12.50")),
        ("€ 12,50", D("12.50")),
        ("EUR 12,50", D("12.50")),
        ("-1,00", D("-1.00")),
        ("1,00-", D("-1.00")),
        ("1.234.567,89", D("1234567.89")),
        ("12", None),
        ("1.234", None),
        ("abc", None),
    ],
)
def test_amount_formats(text, value):
    assert parse_amount(text) == value


def _total(lines):
    return parse(bill(["Beispiel Laden", *lines]))


@pytest.mark.parametrize(
    "label",
    [
        "Gesamt",
        "Summe",
        "Rechnungsbetrag",
        "Zu zahlen",
        "Total",
        "Amount due",
        "Gesamtbetrag",
        "Endbetrag",
        "SUMME EUR",
    ],
)
def test_total_labels(label):
    x = _total([f"{label} 23,80"])
    assert x.total == D("23.80") and "total" in x.sources and x.competing_totals == []


def test_total_source_words_are_the_amount_only():
    x = _total(["Gesamt 23,80"])
    assert [w.text for w in x.sources["total"]] == ["23,80"]


@pytest.mark.parametrize(
    "extra",
    [
        "Zwischensumme 20,00",
        "Subtotal 20,00",
        "Gegeben 50,00",
        "Rückgeld 26,20",
        "Trinkgeld 2,00",
        "Übertrag 88,00",
        "Summe netto 20,00",
        "Gesamt MwSt 3,80",
    ],
)
def test_never_the_total(extra):
    x = _total([extra, "Gesamt 23,80"])
    assert x.total == D("23.80")
    assert _total([extra]).total is None


def test_two_totals_under_the_strongest_label_are_ambiguous():
    x = _total(["Gesamtbetrag 23,80", "Gesamtbetrag 24,80", "Summe 1,00"])
    assert x.total is None and x.competing_totals == [D("23.80"), D("24.80")]
    assert (
        run(
            parse(
                bill(
                    [
                        "Beispiel Laden",
                        "Rechnungsnummer A1",
                        "Datum 12.10.2026",
                        "Gesamtbetrag 23,80",
                        "Gesamtbetrag 24,80",
                    ]
                )
            )
        ).flags[0]["code"]
        == "ambiguous_total"
    )
    same = _total(["Summe 23,80", "Gesamt 23,80"])
    assert same.total == D("23.80")
    assert _total(["Summe 23,80", "Rechnungsbetrag 24,70"]).total == D("24.70")  # the strongest label wins


def test_prepayment_zu_zahlen_is_the_amount_due():
    x = _total(["Gesamtbetrag 196,00", "Anzahlung 50,00", "Zu zahlen 146,00"])
    assert (x.total, x.prepaid, x.amount_due) == (D("196.00"), D("50.00"), D("146.00"))
    assert "prepaid" in x.payment_signals


@pytest.mark.parametrize(
    "line, iso",
    [
        ("Datum: 12.10.2026", "2026-10-12"),
        ("Datum 12.10.26", "2026-10-12"),
        ("Rechnungsdatum: 2026-10-12", "2026-10-12"),
        ("Date 3. Oktober 2026", "2026-10-03"),
        ("Invoice date 12 October 2026", "2026-10-12"),
        ("12.10.2026 14:32", "2026-10-12"),
        ("01.02.2026", "2026-02-01"),  # day first, never month first
    ],
)
def test_date_formats(line, iso):
    assert _total([line]).bill_date == iso


def test_slash_dates_are_never_guessed():
    x = _total(["Date 10/12/2026"])
    assert x.bill_date is None and x.competing_dates == []


def test_labelled_date_is_preferred_and_two_labelled_are_ambiguous():
    assert _total(["Geliefert 07.10.2026", "Rechnungsdatum 12.10.2026"]).bill_date == "2026-10-12"
    assert (
        _total(["Datum 12.10.2026", "Rechnungsdatum 13.10.2026"]).bill_date == "2026-10-13"
    )  # spec: strong label wins
    x = _total(["Datum 12.10.2026", "Kassendatum: 13.10.2026"])
    assert x.bill_date is None and x.competing_dates == ["2026-10-12", "2026-10-13"]
    assert _total(["Datum 12.10.2026", "Rechnungsdatum 12.10.2026"]).bill_date == "2026-10-12"


def test_due_and_paid_dates_are_not_the_bill_date():
    x = _total(
        ["Rechnungsdatum 12.10.2026 Zahlbar bis 28.10.2026", "Bezahlt am 14.10.2026", "Leistungsdatum 07.10.2026"]
    )
    assert (x.bill_date, x.due_date, x.paid_date) == ("2026-10-12", "2026-10-28", "2026-10-14")
    assert {"open", "paid"} <= set(x.payment_signals)


def test_vat_table_forms():
    x = _total(["MwSt 19 % 1,90"])
    assert [(g.rate, g.vat, g.net, g.gross) for g in x.vat_groups] == [(D("19"), D("1.90"), None, None)]
    x = _total(["A 19,00% 10,00 1,90 11,90"])
    g = x.vat_groups[0]
    assert (g.code, g.rate, g.net, g.vat, g.gross) == ("A", D("19"), D("10.00"), D("1.90"), D("11.90"))
    x = _total(["MwSt 7 %", "inkl. 19 % MwSt"])
    assert [g.rate for g in x.vat_groups] == [D("7"), D("19")]
    assert _total(["Rabatt 10 % -1,00"]).vat_groups == []


def test_other_fields():
    x = _total(["USt-IdNr. DE 123456789", "Kartenzahlung 17,70", "Reverse Charge", "Gutschrift"])
    assert x.vat_id == "DE123456789" and x.card_amount == D("17.70")
    assert {"reverse_charge", "credit_note"} <= set(x.text_signals) and "card" in x.payment_signals
    assert _total(["Betrag in USD 5,00"]).currency == "USD"
    assert _total(["Summe 5,00 EUR"]).currency == "EUR"
    assert "small_business" in _total(["Kleinunternehmer gemäß § 19 UStG"]).text_signals


def test_vendor_is_the_first_plausible_header_line():
    lines = ["Rechnung", "Musterstraße 5", "Beispiel Hotel GmbH", "Tel. 030 123456", "Datum 12.10.2026"]
    x = parse(bill(lines))
    assert x.vendor == "Beispiel Hotel GmbH" and [w.text for w in x.sources["vendor"]] == ["Beispiel", "Hotel", "GmbH"]
    assert parse(bill(["12345 Berlin", "www.beispiel.example"])).vendor is None
    hostile = parse(bill(["Beispiel‮evil Laden"]))
    assert hostile.vendor == "Beispielevil Laden"


def test_items_with_quantity_codes_and_negative_lines():
    x = parse(
        bill(
            [
                "Beispiel Markt",
                "Kaffee 2 x 3,40 6,80 A",
                "Pfand zurück -1,00 B",
                "Übertrag 5,80",
                "Brot 2,50",
                "SUMME 8,30",
            ]
        )
    )
    assert [(i.description, i.quantity, i.unit_price, i.amount, i.vat_code) for i in x.items] == [
        ("Kaffee", D("2"), D("3.40"), D("6.80"), "A"),
        ("Pfand zurück", None, None, D("-1.00"), "B"),
        ("Brot", None, None, D("2.50"), None),
    ]
    assert x.total == D("8.30")


def test_discount_without_a_vat_code_is_an_adjustment():
    x = parse(bill(["Beispiel Markt", "Kaffee 5,00 A", "Rabatt -1,00", "SUMME 4,00"]))
    assert [i.description for i in x.items] == ["Kaffee"]
    assert [(a.kind, a.amount) for a in x.adjustments] == [("discount", D("-1.00"))]


def test_total_on_page_12_of_a_scan_raises_pages_skipped():
    words = bill(["Beispiel Laden", "Datum 12.10.2026", "Beleg-Nr 12"]) + bill(["Summe 11,90"], page=11)
    r = ReadResult(engine="ocr", words=words, pages_read=10, pages_total=12)
    ev = evaluate(r, parse(words), context())
    assert "pages_skipped" in codes(ev)
    assert parse(words).total == D("11.90")


# ---------------------------------------------------------------------------------------------------- full texts


HOTEL_TEXT = [
    "Beispiel Hotel GmbH", "Musterstraße 1", "10115 Berlin", "USt-IdNr. DE123456789", "Rechnungsnummer 2026-1042",
    "Rechnungsdatum: 12.10.2026", "Zimmer 160,00 B", "Frühstück 30,00 A", "Kurtaxe 6,00 C",
    "B 7,00% 149,53 10,47 160,00", "A 19,00% 25,21 4,79 30,00", "C 0,00% 6,00 0,00 6,00", "Gesamtbetrag 196,00", "Bar bezahlt",
]  # fmt: skip


def test_hotel_text_to_booked_draft():
    words = bill(HOTEL_TEXT)
    ev = evaluate(read(words), parse(words), context())
    assert codes(ev) == [], ev.doc["checks"]
    assert ev.draft["category"] == "Accommodation" and ev.draft["invoice_no"] == "2026-1042"
    assert [(ln["gross"], ln["vat_rate"]) for ln in ev.draft["lines"]] == [(160.0, 7.0), (30.0, 19.0), (6.0, 0.0)]
    assert ev.draft["paid_on"] == "2026-10-12"


def test_single_rate_text_is_clean_without_a_number_under_250():
    words = bill(
        ["Beispiel Handel GmbH", "Datum 12.10.2026", "Heft 11,90", "MwSt 19 % 1,90", "Summe 11,90", "Bar 11,90"]
    )
    ev = evaluate(read(words), parse(words), context())
    assert codes(ev) == []  # the printed VAT 1,90 confirms the total of 11,90 at 19 %
    assert ev.draft["paid_on"] == "2026-10-12" and ev.draft["vat_rate"] == 19.0
    no_vat = bill(["Beispiel Handel GmbH", "Datum 12.10.2026", "MwSt 19 %", "Summe 11,90", "Bar 11,90"])
    assert codes(evaluate(read(no_vat), parse(no_vat), context())) == ["not_verifiable"]


def test_text_failures_through_the_whole_chain():
    lines = ["Beispiel Markt GmbH", "Datum 12.10.2026", "Beleg-Nr 8841", "Kaffee 2 x 3,40 6,90 A", "Brot 11,90 B", "Rabatt -1,00 B",
             "SUMME 17,70", "Bar gegeben 20,00", "Rückgeld 2,30", "A 7,00% 6,36 0,44 6,80", "B 19,00% 9,16 1,74 10,90"]  # fmt: skip
    words = bill(lines)
    ev = evaluate(read(words), parse(words), context())
    assert "items_mismatch" in codes(ev)
    ok = bill([ln.replace("6,90", "6,80") for ln in lines])
    assert codes(evaluate(read(ok), parse(ok), context())) == []


# ---------------------------------------------------------------------------------------------------- boxes


def test_boxes_have_fractions_and_check_values():
    words = bill(["Beispiel Markt GmbH", "Datum 12.10.2026", "Beleg-Nr 8841", "Kaffee 2 x 3,40 6,80 A", "Brot 11,90 B", "Rabatt -1,00 B",
                  "SUMME 17,70", "Bar gegeben 20,00", "Rückgeld 2,20", "A 7,00% 6,36 0,44 6,80", "B 19,00% 9,16 1,74 10,90"])  # fmt: skip
    ev = evaluate(read(words), parse(words), context())
    boxes = ev.doc["boxes"]
    by = {}
    for b in boxes:
        by.setdefault(b["field"], []).append(b)
    assert set(by) == {"vendor", "date", "invoice_no", "total", "tendered", "change", "item", "vat_group"}
    for b in boxes:
        assert set(b) == {"field", "page", "x", "y", "w", "h", "check", "value"}
        assert 0 <= b["x"] <= 1 and 0 <= b["y"] <= 1 and b["x"] + b["w"] <= 1.0001 and b["y"] + b["h"] <= 1.0001
        assert b["check"] in ("ok", "failed", "none")
    # SUMME is line 6, y = 0.04 + 6 * 0.03; the amount "17,70" follows the 5 letters of the label
    total = by["total"][0]
    assert (total["page"], total["y"], total["h"]) == (0, 0.22, 0.02)
    assert total["x"] == 0.05 + 0.06 + 0.012 and total["w"] == 0.06
    assert total["check"] == "failed"  # tendered - change = 17,80 is not the total
    assert by["change"][0]["check"] == "failed" and by["tendered"][0]["check"] == "failed"
    assert by["vendor"][0]["check"] == "none" and by["date"][0]["check"] == "none"
    assert {b["check"] for b in by["vat_group"]} == {"ok"}
    assert [b["check"] for b in by["item"]] == ["ok", "ok", "ok"]
    assert by["item"][0]["x"] == 0.05 and by["item"][0]["y"] == 0.13


def test_boxes_are_clamped_and_merged_per_page():
    x = base()
    x.sources = {"total": [Word("a", 0, 0.9, 0.95, 0.3, 0.2), Word("b", 0, 0.8, 0.9, 0.05, 0.02), Word("c", 1, 0.1, 0.1, 0.1, 0.1)],
                 "card": [Word("d", 0, 0.1, 0.1, 0.1, 0.1)]}  # fmt: skip
    boxes = run(x).doc["boxes"]
    assert boxes == [
        {"field": "total", "page": 0, "x": 0.8, "y": 0.9, "w": 0.2, "h": 0.1, "check": "none", "value": "11.90"},
        {"field": "total", "page": 1, "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1, "check": "none", "value": "11.90"},
    ]  # the card amount has no box field


def test_a_value_read_twice_keeps_its_source_words():
    x = parse(bill(["Beispiel Laden", "Summe 11,90", "Gesamt 11,90"]))
    assert x.total == D("11.90") and len(x.sources["total"]) == 2
    assert {w.y for w in x.sources["total"]} == {0.07, 0.1}


def test_line_grouping_by_position():
    words = [Word("Gesamt", 0, 0.1, 0.5, 0.1, 0.02), Word("11,90", 0, 0.6, 0.505, 0.1, 0.02), Word("Beispiel", 0, 0.1, 0.1, 0.1, 0.02),
             Word("Laden", 0, 0.25, 0.1, 0.1, 0.02)]  # fmt: skip
    x = parse(words)
    assert x.total == D("11.90") and x.vendor == "Beispiel Laden"
    assert replace(x).total == x.total


# ------------------------------------------------------------------ quantity x unit price without a separate amount
def test_quantity_times_unit_price_without_an_amount_reads_the_product():
    x = parse(bill(["Musterhotel GmbH", "Uebernachtung 2 x 80,00 EUR", "Gesamt 160,00 EUR"]))
    (item,) = x.items
    assert (item.description, item.quantity, item.unit_price, item.amount) == (
        "Uebernachtung",
        D(2),
        D("80.00"),
        D("160.00"),
    )


def test_quantity_unit_price_and_amount_keep_all_three_and_are_checked():
    x = parse(bill(["Musterhotel GmbH", "Uebernachtung 2 x 80,00 160,00", "Gesamt 160,00 EUR"]))
    (item,) = x.items
    assert (item.quantity, item.unit_price, item.amount) == (D(2), D("80.00"), D("160.00"))
    bad = parse(bill(["Musterhotel GmbH", "Uebernachtung 2 x 80,00 155,00", "Gesamt 155,00 EUR"]))
    from permita38.core.receipts.reconcile import reconcile

    failed = [c for c in reconcile(bad) if not c.ok]
    assert [(c.name, c.flag) for c in failed] == [("item_line", "items_mismatch")]


def test_the_hotel_line_with_a_total_raises_no_items_mismatch():
    from permita38.core.receipts.reconcile import reconcile

    x = parse(bill(["Musterhotel GmbH", "Uebernachtung 2 x 80,00 EUR", "Gesamt 160,00 EUR"]))
    assert all(c.ok for c in reconcile(x) if c.flag == "items_mismatch")


# ---------------------------------------------------------------- hostile input stays fast
def _timed(words) -> float:
    import time

    t = time.monotonic()
    parse(words)
    return time.monotonic() - t


def test_parse_huge_digit_word_is_fast():
    assert _timed([Word("1" * 200_000, 0, 0.1, 0.1, 0.1, 0.01)]) < 1.0


def test_parse_many_dotted_ones_is_fast():
    assert _timed([Word("1.1" * 13_000, 0, 0.1, 0.1, 0.1, 0.01)]) < 1.0
    assert _timed([Word("1.1 " * 10_000, 0, 0.1, 0.1, 0.1, 0.01)]) < 1.0


def test_parse_ten_thousand_tiny_words_on_one_line_is_fast():
    words = [Word("a", 0, i * 0.00005, 0.5, 0.00004, 0.01) for i in range(10_000)]
    assert _timed(words) < 1.0
