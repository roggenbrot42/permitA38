"""E-invoice reader: XRechnung (UBL, CII) and ZUGFeRD / Factur-X XML into an ``Extraction`` (docs/specs/receipt-scan.md).

Exact values, no guessing and no arithmetic: the EN 16931 sums are only copied into ``Extraction.einvoice_sums``
(BT-106 ... BT-115); ``reconcile`` checks them. The XML is outside input: any DOCTYPE or ENTITY is refused before
parsing, in every encoding, and only the standard library parser is used.
"""

import codecs
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from xml.parsers import expat

from permita38.core.receipts.extraction import Adjustment, Extraction, Item, Party, VatGroup  # noqa: F401  (Party is re-exported)

MAX_XML_BYTES = 20_000_000
_LIMIT = Decimal("1e15")


class EInvoiceError(ValueError):
    """The XML is refused or is not a supported e-invoice. The message is a fixed code, never file content."""


# ---- safe parsing ----------------------------------------------------------------------------------------------

_DECL_ENCODING = re.compile(rb"""^<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""")
_ALLOWED = {"utf-8", "utf8", "us-ascii", "ascii", "iso-8859-1", "latin-1", "latin1", "utf-16", "utf-16le", "utf-16be"}
_FORBIDDEN = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def _decode_for_check(data: bytes) -> str:
    """Decode like the parser would, only to look for forbidden declarations. Unknown encodings are refused."""
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return data.decode("utf-32", errors="replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode("utf-8", errors="replace")
    head = data[:4]
    if len(head) == 4 and (head[:3] == b"\x00\x00\x00" or head[1:] == b"\x00\x00\x00"):
        return data.decode("utf-32-be" if head[0] == 0 else "utf-32-le", errors="replace")
    if len(data) >= 2 and (data[0] == 0 or data[1] == 0):
        return data.decode("utf-16-be" if data[0] == 0 else "utf-16-le", errors="replace")
    m = _DECL_ENCODING.match(data.lstrip())
    name = m.group(1).decode("ascii").lower() if m else "utf-8"
    if name not in _ALLOWED:
        raise EInvoiceError("unsupported_encoding")
    return data.decode("latin-1" if name in ("iso-8859-1", "latin-1", "latin1") else "utf-8", errors="replace")


def parse_xml_safely(data: bytes) -> ET.Element:
    if len(data) > MAX_XML_BYTES:
        raise EInvoiceError("too_large")
    # NUL bytes between letters would hide the keyword from a scan of the raw bytes, hence the decoded check.
    if _FORBIDDEN.search(_decode_for_check(data)) or re.search(rb"<\s*!\s*(DOCTYPE|ENTITY)", data, re.IGNORECASE):
        raise EInvoiceError("forbidden_declaration")

    def refuse(*_args: object) -> None:
        raise EInvoiceError("forbidden_declaration")

    # expat directly: a declaration handler that refuses is a second line of defence behind the check above
    tree = ET.TreeBuilder()
    parser = expat.ParserCreate()
    parser.buffer_text = True
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.StartElementHandler = lambda name, attrs: tree.start(
        name.rsplit(":", 1)[-1], {k.rsplit(":", 1)[-1]: v for k, v in attrs.items()}
    )
    parser.EndElementHandler = lambda name: tree.end(name.rsplit(":", 1)[-1])
    parser.CharacterDataHandler = tree.data
    try:
        parser.Parse(data, True)
        root = tree.close()
    except expat.ExpatError:
        raise EInvoiceError("bad_xml") from None
    return root


# ---- tiny tree helpers -----------------------------------------------------------------------------------------


def _kids(el: ET.Element | None, name: str) -> list[ET.Element]:
    return [] if el is None else [c for c in el if c.tag == name]


def _find(el: ET.Element | None, *path: str) -> ET.Element | None:
    for name in path:
        kids = _kids(el, name)
        el = kids[0] if kids else None
        if el is None:
            return None
    return el


def _text(el: ET.Element | None, *path: str) -> str | None:
    node = _find(el, *path)
    t = (node.text or "").strip() if node is not None else ""
    return t or None


def _dec(el: ET.Element | None, *path: str) -> Decimal | None:
    t = _text(el, *path)
    return _to_decimal(t)


def _to_decimal(t: str | None) -> Decimal | None:
    if not t:
        return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    if d.is_finite() and not -12 <= d.as_tuple().exponent <= 12:  # type: ignore[operator]
        return None  # "1E-999999999" would build a gigabyte of digits
    return d if d.is_finite() and abs(d) < _LIMIT else None


def _amount_currency(tax_total: ET.Element) -> str | None:
    node = _find(tax_total, "TaxAmount")
    return None if node is None else node.get("currencyID")


def _iso(value: str | None) -> str | None:
    """``20261101`` (CII format 102) or ``2026-11-01`` (UBL, may carry a zone) to ISO."""
    if not value:
        return None
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", value.strip())
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _code(category: str | None, rate: Decimal | None) -> str | None:
    if not category:
        return None
    return category if rate is None else f"{category}{rate.normalize():f}"


_PAYMENT_CODES = {"10": "cash", "48": "card", "54": "card", "55": "card"}
_OPEN_CODES = {"30", "31", "42", "58", "57", "59"}


def _payment_signal(code: str | None) -> str | None:
    if code is None:
        return None
    return _PAYMENT_CODES.get(code) or ("open" if code in _OPEN_CODES else None)


def _type(code: str | None, ex: Extraction) -> None:
    if code == "381":
        ex.doc_type = "credit_note"
        ex.text_signals.append("credit_note")
    else:
        ex.doc_type = "invoice"


def _finish(ex: Extraction, categories: list[str | None]) -> Extraction:
    if any(c == "AE" for c in categories) and "reverse_charge" not in ex.text_signals:
        ex.text_signals.append("reverse_charge")
    prepaid = ex.einvoice_sums.get("BT-113", Decimal(0))
    if prepaid > 0 and prepaid == ex.einvoice_sums.get("BT-112") and "paid" not in ex.payment_signals:
        ex.payment_signals.append("paid")  # everything was paid in advance: the bill is paid
    elif prepaid > 0 and "prepaid" not in ex.payment_signals:
        ex.payment_signals.append("prepaid")
    ex.total = ex.einvoice_sums.get("BT-112")
    ex.prepaid = ex.einvoice_sums.get("BT-113")
    ex.amount_due = ex.einvoice_sums.get("BT-115")
    return ex


# ---- UBL (Invoice, CreditNote) ---------------------------------------------------------------------------------


def _ubl(root: ET.Element) -> Extraction:
    ex = Extraction()
    cur = _text(root, "DocumentCurrencyCode")
    ex.currency = cur
    ex.invoice_no = _text(root, "ID")
    ex.bill_date = _iso(_text(root, "IssueDate"))
    ex.due_date = _iso(_text(root, "DueDate")) or _iso(_text(root, "PaymentMeans", "PaymentDueDate"))
    if root.tag == "CreditNote":
        _type("381", ex)
    else:
        _type(_text(root, "InvoiceTypeCode"), ex)
    party = _find(root, "AccountingSupplierParty", "Party")
    ex.vendor = _text(party, "PartyLegalEntity", "RegistrationName") or _text(party, "PartyName", "Name")
    for ts in _kids(party, "PartyTaxScheme"):
        if (_text(ts, "TaxScheme", "ID") or "VAT").upper() == "VAT":
            ex.vat_id = _text(ts, "CompanyID")
            break
    sig = _payment_signal(_text(root, "PaymentMeans", "PaymentMeansCode"))
    if sig:
        ex.payment_signals.append(sig)

    mon = _find(root, "LegalMonetaryTotal")
    for bt, name in (
        ("BT-106", "LineExtensionAmount"),
        ("BT-107", "AllowanceTotalAmount"),
        ("BT-108", "ChargeTotalAmount"),
        ("BT-109", "TaxExclusiveAmount"),
        ("BT-112", "TaxInclusiveAmount"),
        ("BT-113", "PrepaidAmount"),
        ("BT-114", "PayableRoundingAmount"),
        ("BT-115", "PayableAmount"),
    ):
        v = _dec(mon, name)
        if v is not None:
            ex.einvoice_sums[bt] = v

    # BT-110: the tax total in the document currency (a second one may be in the accounting currency)
    totals = _kids(root, "TaxTotal")
    chosen = next((t for t in totals if _amount_currency(t) == cur), None)
    v = _dec(chosen if chosen is not None else (totals[0] if totals else None), "TaxAmount")
    if v is not None:
        ex.einvoice_sums["BT-110"] = v

    categories: list[str | None] = []
    for sub in [s for t in totals for s in _kids(t, "TaxSubtotal")]:
        cat = _text(sub, "TaxCategory", "ID")
        rate = _dec(sub, "TaxCategory", "Percent")
        categories.append(cat)
        ex.vat_groups.append(
            VatGroup(rate=rate, code=_code(cat, rate), net=_dec(sub, "TaxableAmount"), vat=_dec(sub, "TaxAmount"))
        )
    for ac in _kids(root, "AllowanceCharge"):
        amount = _dec(ac, "Amount")
        if amount is None:
            continue
        charge = (_text(ac, "ChargeIndicator") or "").lower() == "true"
        ex.adjustments.append(Adjustment("charge" if charge else "discount", amount if charge else -amount))
        categories.append(_text(ac, "TaxCategory", "ID"))

    for line in _kids(root, "InvoiceLine") + _kids(root, "CreditNoteLine"):
        cat = _text(line, "Item", "ClassifiedTaxCategory", "ID")
        rate = _dec(line, "Item", "ClassifiedTaxCategory", "Percent")
        categories.append(cat)
        amount = _dec(line, "LineExtensionAmount")
        item = Item(
            description=_text(line, "Item", "Name") or _text(line, "Item", "Description") or "",
            quantity=_dec(line, "InvoicedQuantity") if root.tag == "Invoice" else _dec(line, "CreditedQuantity"),
            unit_price=_dec(line, "Price", "PriceAmount"),
            amount=amount,
            vat_code=_code(cat, rate),
        )
        ex.items.append(item)
    return _finish(ex, categories)


# ---- CII (XRechnung CII, ZUGFeRD, Factur-X) --------------------------------------------------------------------


def _cii_date(el: ET.Element | None, *path: str) -> str | None:
    return _iso(_text(el, *path, "DateTimeString"))


def _cii(root: ET.Element) -> Extraction:
    ex = Extraction()
    doc = _find(root, "ExchangedDocument")
    ex.invoice_no = _text(doc, "ID")
    _type(_text(doc, "TypeCode"), ex)
    ex.bill_date = _cii_date(doc, "IssueDateTime")
    trade = _find(root, "SupplyChainTradeTransaction")
    seller = _find(trade, "ApplicableHeaderTradeAgreement", "SellerTradeParty")
    ex.vendor = _text(seller, "Name")
    for reg in _kids(seller, "SpecifiedTaxRegistration"):
        node = _find(reg, "ID")
        if node is not None and (node.get("schemeID") or "VA").upper() == "VA":
            ex.vat_id = (node.text or "").strip() or None
            break
    st = _find(trade, "ApplicableHeaderTradeSettlement")
    cur = _text(st, "InvoiceCurrencyCode")
    ex.currency = cur
    ex.due_date = _cii_date(st, "SpecifiedTradePaymentTerms", "DueDateDateTime")
    sig = _payment_signal(_text(st, "SpecifiedTradeSettlementPaymentMeans", "TypeCode"))
    if sig:
        ex.payment_signals.append(sig)

    mon = _find(st, "SpecifiedTradeSettlementHeaderMonetarySummation")
    for bt, name in (
        ("BT-106", "LineTotalAmount"),
        ("BT-107", "AllowanceTotalAmount"),
        ("BT-108", "ChargeTotalAmount"),
        ("BT-109", "TaxBasisTotalAmount"),
        ("BT-112", "GrandTotalAmount"),
        ("BT-113", "TotalPrepaidAmount"),
        ("BT-114", "RoundingAmount"),
        ("BT-115", "DuePayableAmount"),
    ):
        v = _dec(mon, name)
        if v is not None:
            ex.einvoice_sums[bt] = v
    totals = _kids(mon, "TaxTotalAmount")  # several only when an accounting currency is added
    node = next((t for t in totals if t.get("currencyID") == cur), totals[0] if totals else None)
    v = _to_decimal((node.text or "").strip()) if node is not None else None
    if v is not None:
        ex.einvoice_sums["BT-110"] = v

    categories: list[str | None] = []
    for tax in _kids(st, "ApplicableTradeTax"):
        cat = _text(tax, "CategoryCode")
        rate = _dec(tax, "RateApplicablePercent")
        categories.append(cat)
        ex.vat_groups.append(
            VatGroup(rate=rate, code=_code(cat, rate), net=_dec(tax, "BasisAmount"), vat=_dec(tax, "CalculatedAmount"))
        )
    for ac in _kids(st, "SpecifiedTradeAllowanceCharge"):
        amount = _dec(ac, "ActualAmount")
        if amount is None:
            continue
        charge = (_text(ac, "ChargeIndicator", "Indicator") or "").lower() == "true"
        ex.adjustments.append(Adjustment("charge" if charge else "discount", amount if charge else -amount))
        categories.append(_text(ac, "CategoryTradeTax", "CategoryCode"))

    for line in _kids(trade, "IncludedSupplyChainTradeLineItem"):
        ls = _find(line, "SpecifiedLineTradeSettlement")
        cat = _text(ls, "ApplicableTradeTax", "CategoryCode")
        rate = _dec(ls, "ApplicableTradeTax", "RateApplicablePercent")
        categories.append(cat)
        ex.items.append(
            Item(
                description=_text(line, "SpecifiedTradeProduct", "Name") or "",
                quantity=_dec(line, "SpecifiedLineTradeDelivery", "BilledQuantity"),
                unit_price=_dec(line, "SpecifiedLineTradeAgreement", "NetPriceProductTradePrice", "ChargeAmount"),
                amount=_dec(ls, "SpecifiedTradeSettlementLineMonetarySummation", "LineTotalAmount"),
                vat_code=_code(cat, rate),
            )
        )
    return _finish(ex, categories)


def parse_einvoice(data: bytes) -> Extraction:
    """Read an XRechnung / ZUGFeRD XML. Raises ``EInvoiceError`` (fixed code) when refused or not an invoice."""
    root = parse_xml_safely(data)
    if root.tag in ("Invoice", "CreditNote"):
        return _ubl(root)
    if root.tag == "CrossIndustryInvoice":
        return _cii(root)
    raise EInvoiceError("not_an_invoice")


# ---- buyer and document type (docs/specs/document-scan.md, "Reading" rule 9) -------------------------------------

_PARTY_CAP = 200


@dataclass
class EInvoiceDoc:
    """``parse_einvoice`` plus what an offer or invoice scan needs: the UNTDID 1001 type code and the buyer."""

    extraction: Extraction
    type_code: str | None  # "380" invoice, "381" credit note, "384" correction; a UBL CreditNote without code is "381"
    buyer: Party


def _short(value: str | None) -> str | None:
    return value[:_PARTY_CAP] if value else None


def _ubl_buyer(root: ET.Element) -> Party:
    party = _find(root, "AccountingCustomerParty", "Party")
    vat = None
    for ts in _kids(party, "PartyTaxScheme"):
        if (_text(ts, "TaxScheme", "ID") or "VAT").upper() == "VAT":
            vat = _text(ts, "CompanyID")
            break
    return Party(
        name=_short(_text(party, "PartyLegalEntity", "RegistrationName") or _text(party, "PartyName", "Name")),
        street=_short(_text(party, "PostalAddress", "StreetName")),
        zip=_short(_text(party, "PostalAddress", "PostalZone")),
        city=_short(_text(party, "PostalAddress", "CityName")),
        country=_short(_text(party, "PostalAddress", "Country", "IdentificationCode")),
        vat_id=_short(vat),
        email=_short(_text(party, "Contact", "ElectronicMail")),
    )


def _cii_buyer(root: ET.Element) -> Party:
    trade = _find(root, "SupplyChainTradeTransaction")
    buyer = _find(trade, "ApplicableHeaderTradeAgreement", "BuyerTradeParty")
    vat = None
    for reg in _kids(buyer, "SpecifiedTaxRegistration"):
        node = _find(reg, "ID")
        if node is not None and (node.get("schemeID") or "VA").upper() == "VA":
            vat = (node.text or "").strip() or None
            break
    return Party(
        name=_short(_text(buyer, "Name")),
        street=_short(_text(buyer, "PostalTradeAddress", "LineOne")),
        zip=_short(_text(buyer, "PostalTradeAddress", "PostcodeCode")),
        city=_short(_text(buyer, "PostalTradeAddress", "CityName")),
        country=_short(_text(buyer, "PostalTradeAddress", "CountryID")),
        vat_id=_short(vat),
        email=_short(
            _text(buyer, "DefinedTradeContact", "EmailURIUniversalCommunication", "URIID")
            or _text(buyer, "URIUniversalCommunication", "URIID")
        ),
    )


def read_einvoice(data: bytes) -> EInvoiceDoc:
    """Like ``parse_einvoice`` (same ``Extraction``), and also the buyer party and the document type code."""
    root = parse_xml_safely(data)
    if root.tag in ("Invoice", "CreditNote"):
        code = "381" if root.tag == "CreditNote" else _text(root, "InvoiceTypeCode")
        return EInvoiceDoc(_ubl(root), code, _ubl_buyer(root))
    if root.tag == "CrossIndustryInvoice":
        return EInvoiceDoc(_cii(root), _text(_find(root, "ExchangedDocument"), "TypeCode"), _cii_buyer(root))
    raise EInvoiceError("not_an_invoice")
