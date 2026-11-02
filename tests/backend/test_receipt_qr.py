"""QR codes on receipts: the TSE parser, the merge into the reading, the checks. All codes are invented."""

import warnings
from decimal import Decimal as D

import pytest

from permita38.core.receipts import qrcodes
from permita38.core.receipts.extraction import Code, Extraction, extraction_from_json, to_jsonable
from permita38.core.receipts.rules import evaluate
from permita38.core.receipts.textrules import parse
from tests.backend.receipt_helpers import bill, context, read

# invented: 9.50 at 19 %, 14.30 at 7 %, 2.20 tip at 0 %, 26.00 paid by card
TSE = "V0;AB12CD34EF56;Kassenbeleg-V1;Beleg^9.50_14.30_0.00_0.00_2.20^26.00:Unbar;4711;12;2026-10-17T10:07:12.000Z;2026-10-17T10:08:00.000Z;ecdsa;UTC;c2ln;a2V5"
TEXT = [
    "Cafe Beispiel GmbH", "Rechnung Nr: 38", "17.10.2026", "Tee 5,60 € A", "Kaffee 3,90 € A", "Kuchen 14,30 € B", "Summe Verkäufe 23,80 €",
    "Trinkgeld 2,20 €", "Total 26,00 €", "Umsatz 19% inkl. 9,50 € A", "enth. MwSt 19% 1,52 € A", "Umsatz 7% inkl. 14,30 € B", "EC-KARTE 26,00 €",
]  # fmt: skip


def code(text=TSE):
    return Code(text, 0, 0.1, 0.5, 0.2, 0.1)


def merged(lines=TEXT, codes=None, ocr=False):
    x = parse(bill(lines))
    qrcodes.apply_qr(x, codes if codes is not None else [code()], ocr=ocr)
    return x


def test_tse_code_is_parsed():
    q = qrcodes.parse_tse(TSE)
    assert q.groups == [(D(19), D("9.50")), (D(7), D("14.30")), (D(0), D("2.20"))]
    assert q.payments == [(D("26.00"), "Unbar")] and q.date == "2026-10-17" and q.number == "AB12CD34-4711"


@pytest.mark.parametrize(
    "text",
    [
        "https://example.invalid/pay",
        TSE.replace("Kassenbeleg-V1", "Bestellung-V1"),
        TSE.replace("9.50", "9,50"),
        TSE.replace("Unbar", "Karte"),
        TSE.replace("14.30", "1e30"),
        "V0;a;b",
        "x" * 3000,
    ],
)
def test_other_or_hostile_codes_are_ignored(text):
    assert qrcodes.parse_code(text) is None


def test_the_code_replaces_groups_total_and_payment_and_the_bill_is_clean():
    x = merged()
    assert [(g.rate, g.gross) for g in x.vat_groups] == [(D(19), D("9.50")), (D(7), D("14.30"))]
    assert x.total == D("26.00") and [a.amount for a in x.adjustments if a.kind == "tip"] == [D("2.20")]
    ev = evaluate(read(bill(TEXT)), x, context())
    assert ev.clean, ev.flags
    assert [(ln["gross"], ln["vat_rate"]) for ln in ev.draft["lines"]] == [(9.5, 19.0), (14.3, 7.0), (2.2, 0.0)]
    assert {c["name"] for c in ev.doc["checks"]} >= {"qr_payment", "qr_total"}
    assert any(b["field"] == "qr" for b in ev.doc["boxes"])


def test_ocr_total_that_differs_from_the_code_does_not_win_and_raises_no_flag():
    lines = [ln.replace("Total 26,00", "Total 217,00") for ln in TEXT]
    x = merged(lines, ocr=True)
    assert x.total == D("26.00") and x.qr.printed_total == D("217.00")
    ev = evaluate(read(bill(lines), engine="ocr"), x, context())
    assert ev.draft["gross"] == 26.0
    failed = [c for c in ev.doc["checks"] if not c["ok"]]
    assert [c["name"] for c in failed] == ["qr_total"] and "total_mismatch" not in {f["code"] for f in ev.flags}


def test_a_printed_pdf_total_that_differs_is_flagged_and_so_are_payments_that_do_not_add_up():
    lines = [ln.replace("Total 26,00", "Total 27,00") for ln in TEXT]
    ev = evaluate(read(bill(lines)), merged(lines), context())
    assert "total_mismatch" in {f["code"] for f in ev.flags}
    bad = TSE.replace("26.00:Unbar", "25.00:Unbar")
    ev = evaluate(read(bill(TEXT)), merged(codes=[code(bad)]), context())
    assert "total_mismatch" in {f["code"] for f in ev.flags}


def test_missing_invoice_number_comes_from_the_code_and_the_date_resolves_an_ambiguity():
    lines = [ln for ln in TEXT if "Rechnung" not in ln]
    x = merged(lines)
    assert x.invoice_no == "AB12CD34-4711"
    lines = [*lines, "18.10.2026"]
    x = merged(lines)
    assert x.bill_date == "2026-10-17"


def test_epc_code_gives_open_and_one_amount_and_other_codes_nothing():
    epc = "BCD\n002\n1\nSCT\n\nBeispiel GmbH\nDE00000000000000000000\nEUR26.00\n\n\nRE 1"
    x = merged(codes=[code(epc)])
    assert "open" in x.payment_signals and x.qr.epc_amount == D("26.00")
    ev = evaluate(read(bill(TEXT)), x, context())
    assert next(c for c in ev.doc["checks"] if c["name"] == "qr_total")["ok"]
    assert merged(codes=[code("https://example.invalid")]).qr is None


def test_json_roundtrip_keeps_the_code_result():
    x = merged()
    back = extraction_from_json(to_jsonable(x))
    assert back.qr == x.qr and isinstance(back, Extraction)


def test_a_real_qr_image_is_found_in_a_raster():
    zxingcpp = pytest.importorskip("zxingcpp")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        img = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, TSE, 300, 300, 8)
    h, w = img.shape[:2]
    found = qrcodes.find_codes(w, h, bytes(memoryview(img)))
    assert [c.text for c in found] == [TSE] and 0 <= found[0].x < 0.2 and 0.5 < found[0].w <= 1
