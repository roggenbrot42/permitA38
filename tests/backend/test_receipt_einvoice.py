"""E-invoice reader: UBL and CII values, the BT sums copied exactly, and refused XML."""

from decimal import Decimal as D

import pytest

from permita38.core.receipts.einvoice import EInvoiceError, parse_einvoice
from tests.backend.receipt_helpers import CII, UBL


def test_ubl_header_and_sums():
    ex = parse_einvoice(UBL.encode())
    assert (ex.vendor, ex.vat_id, ex.invoice_no) == ("Musterhotel GmbH", "DE123456789", "RE-2026-0042")
    assert (ex.bill_date, ex.due_date, ex.currency, ex.doc_type) == ("2026-10-07", "2026-11-04", "EUR", "invoice")
    assert ex.einvoice_sums == {
        "BT-106": D("150.00"),
        "BT-107": D("1.00"),
        "BT-108": D("0.53"),
        "BT-109": D("149.53"),
        "BT-110": D("10.47"),
        "BT-112": D("160.00"),
        "BT-113": D("50.00"),
        "BT-114": D("0.00"),
        "BT-115": D("110.00"),
    }
    assert (ex.total, ex.prepaid, ex.amount_due) == (D("160.00"), D("50.00"), D("110.00"))
    assert ex.payment_signals == ["open", "prepaid"]
    assert "reverse_charge" not in ex.text_signals


def test_ubl_lines_and_vat_groups():
    ex = parse_einvoice(UBL.encode())
    assert [(i.description, i.quantity, i.unit_price, i.amount, i.vat_code) for i in ex.items] == [
        ("Zimmer", D("2"), D("50.00"), D("100.00"), "S7"),
        ("Fruehstueck", D("1"), D("50.00"), D("50.00"), "S7"),
    ]
    assert len(ex.vat_groups) == 1
    g = ex.vat_groups[0]
    assert (g.rate, g.code, g.net, g.vat) == (D("7"), "S7", D("149.53"), D("10.47"))


def test_cii_credit_note_reverse_charge():
    ex = parse_einvoice(CII.encode())
    assert (ex.vendor, ex.vat_id, ex.invoice_no) == ("Beispiel Consulting", "DE987654321", "F-77")
    assert (ex.bill_date, ex.due_date, ex.currency) == ("2026-10-12", "2026-11-11", "EUR")
    assert ex.doc_type == "credit_note"
    assert set(ex.text_signals) == {"credit_note", "reverse_charge"}
    assert ex.payment_signals == ["cash"]
    assert ex.einvoice_sums == {
        "BT-106": D("30.00"),
        "BT-109": D("30.00"),
        "BT-110": D("0.00"),
        "BT-112": D("30.00"),
        "BT-115": D("30.00"),
    }
    assert [(i.description, i.quantity, i.unit_price, i.amount, i.vat_code) for i in ex.items] == [
        ("Beratung", D("3"), D("10.00"), D("30.00"), "AE0")
    ]
    g = ex.vat_groups[0]
    assert (g.rate, g.code, g.net, g.vat) == (D("0"), "AE0", D("30.00"), D("0.00"))


def test_ubl_reverse_charge_category_ae():
    ex = parse_einvoice(UBL.replace("<cbc:ID>S</cbc:ID><cbc:Percent>7", "<cbc:ID>AE</cbc:ID><cbc:Percent>0").encode())
    assert "reverse_charge" in ex.text_signals


def test_printed_amounts_are_not_corrected():
    # the reader copies; a wrong sum is found by reconcile, not here
    ex = parse_einvoice(UBL.replace("160.00", "161.00").encode())
    assert ex.einvoice_sums["BT-112"] == D("161.00")


def test_garbage_amounts_become_missing():
    ex = parse_einvoice(UBL.replace(">149.53</cbc:TaxExclusiveAmount", ">NaN</cbc:TaxExclusiveAmount").encode())
    assert "BT-109" not in ex.einvoice_sums


DOCTYPE = '<?xml version="1.0"?><!DOCTYPE Invoice [<!ENTITY x "y">]><Invoice><ID>&x;</ID></Invoice>'


@pytest.mark.parametrize(
    "data",
    [
        DOCTYPE.encode(),
        DOCTYPE.encode("utf-16"),
        DOCTYPE.encode("utf-16-le"),
        DOCTYPE.encode("utf-16-be"),
        DOCTYPE.encode("utf-32"),
        DOCTYPE.encode("utf-8-sig"),
        b'<?xml version="1.0"?><!doctype Invoice><Invoice/>',
        b'<?xml version="1.0"?><Invoice><!ENTITY x "y"></Invoice>',
        b'<?xml version="1.0" encoding="cp037"?><Invoice/>',
    ],
    ids=["utf8", "utf16-bom", "utf16-le", "utf16-be", "utf32", "bom8", "lowercase", "entity-only", "ebcdic"],
)
def test_doctype_and_entities_are_refused(data):
    with pytest.raises(EInvoiceError):
        parse_einvoice(data)


def test_utf16_invoice_without_doctype_is_read():
    assert parse_einvoice(UBL.replace("UTF-8", "UTF-16").encode("utf-16")).invoice_no == "RE-2026-0042"


@pytest.mark.parametrize("data", [b"", b"<Invoice", b"<html/>", b"not xml"], ids=["empty", "broken", "other", "text"])
def test_not_an_invoice(data):
    with pytest.raises(EInvoiceError):
        parse_einvoice(data)


def test_absurd_exponents_are_refused_without_building_digits():
    from permita38.core.receipts.einvoice import _to_decimal

    assert _to_decimal("1E-999999999") is None
    assert _to_decimal("1E+20") is None
    assert _to_decimal("12.50") == D("12.50")
