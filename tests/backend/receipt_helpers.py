"""Fake receipts for the scan tests. All data is invented."""

from datetime import date

from permita38.core.pdf import Pdf
from permita38.core.receipts.extraction import ReadResult, Word
from permita38.core.receipts.rules import Context

UBL = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>RE-2026-0042</cbc:ID>
  <cbc:IssueDate>2026-10-07</cbc:IssueDate>
  <cbc:DueDate>2026-11-04</cbc:DueDate>
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyTaxScheme><cbc:CompanyID>DE123456789</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>
    <cac:PartyLegalEntity><cbc:RegistrationName>Musterhotel GmbH</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:PaymentMeans><cbc:PaymentMeansCode>58</cbc:PaymentMeansCode></cac:PaymentMeans>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="EUR">10.47</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">149.53</cbc:TaxableAmount><cbc:TaxAmount currencyID="EUR">10.47</cbc:TaxAmount>
      <cac:TaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>7</cbc:Percent></cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="EUR">150.00</cbc:LineExtensionAmount>
    <cbc:AllowanceTotalAmount currencyID="EUR">1.00</cbc:AllowanceTotalAmount>
    <cbc:ChargeTotalAmount currencyID="EUR">0.53</cbc:ChargeTotalAmount>
    <cbc:TaxExclusiveAmount currencyID="EUR">149.53</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">160.00</cbc:TaxInclusiveAmount>
    <cbc:PrepaidAmount currencyID="EUR">50.00</cbc:PrepaidAmount>
    <cbc:PayableRoundingAmount currencyID="EUR">0.00</cbc:PayableRoundingAmount>
    <cbc:PayableAmount currencyID="EUR">110.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID><cbc:InvoicedQuantity unitCode="C62">2</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">100.00</cbc:LineExtensionAmount>
    <cac:Item><cbc:Name>Zimmer</cbc:Name><cac:ClassifiedTaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>7</cbc:Percent></cac:ClassifiedTaxCategory></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="EUR">50.00</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>
  <cac:InvoiceLine>
    <cbc:ID>2</cbc:ID><cbc:InvoicedQuantity unitCode="C62">1</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">50.00</cbc:LineExtensionAmount>
    <cac:Item><cbc:Name>Fruehstueck</cbc:Name><cac:ClassifiedTaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>7</cbc:Percent></cac:ClassifiedTaxCategory></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="EUR">50.00</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>
</Invoice>
"""

CII = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument>
    <ram:ID>F-77</ram:ID><ram:TypeCode>381</ram:TypeCode>
    <ram:IssueDateTime><udt:DateTimeString format="102">20261012</udt:DateTimeString></ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:SpecifiedTradeProduct><ram:Name>Beratung</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeAgreement><ram:NetPriceProductTradePrice><ram:ChargeAmount>10.00</ram:ChargeAmount></ram:NetPriceProductTradePrice></ram:SpecifiedLineTradeAgreement>
      <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="HUR">3</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeSettlement>
        <ram:ApplicableTradeTax><ram:TypeCode>VAT</ram:TypeCode><ram:CategoryCode>AE</ram:CategoryCode><ram:RateApplicablePercent>0</ram:RateApplicablePercent></ram:ApplicableTradeTax>
        <ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>30.00</ram:LineTotalAmount></ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty><ram:Name>Beispiel Consulting</ram:Name>
        <ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">DE987654321</ram:ID></ram:SpecifiedTaxRegistration>
      </ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradeSettlementPaymentMeans><ram:TypeCode>10</ram:TypeCode></ram:SpecifiedTradeSettlementPaymentMeans>
      <ram:ApplicableTradeTax>
        <ram:CalculatedAmount>0.00</ram:CalculatedAmount><ram:TypeCode>VAT</ram:TypeCode><ram:BasisAmount>30.00</ram:BasisAmount>
        <ram:CategoryCode>AE</ram:CategoryCode><ram:RateApplicablePercent>0</ram:RateApplicablePercent>
      </ram:ApplicableTradeTax>
      <ram:SpecifiedTradePaymentTerms><ram:DueDateDateTime><udt:DateTimeString format="102">20261111</udt:DateTimeString></ram:DueDateDateTime></ram:SpecifiedTradePaymentTerms>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:LineTotalAmount>30.00</ram:LineTotalAmount>
        <ram:TaxBasisTotalAmount>30.00</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount currencyID="EUR">0.00</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>30.00</ram:GrandTotalAmount>
        <ram:DuePayableAmount>30.00</ram:DuePayableAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""


def pdf_with_attachment(name: str, payload: bytes) -> bytes:
    """A minimal one-page PDF with one embedded file (as in a ZUGFeRD / Factur-X PDF)."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << /Names [(" + name.encode() + b") 4 0 R] >> >> >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
        b"<< /Type /Filespec /F (" + name.encode() + b") /UF (" + name.encode() + b") /EF << /F 5 0 R >> >>",
        b"<< /Type /EmbeddedFile /Subtype /text#2Fxml /Length %d >>\nstream\n" % len(payload)
        + payload
        + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


# (text, x pt from left, baseline y pt from top, size)
RECEIPT_LINES = [
    ("Musterhotel Beispielstadt GmbH", 60, 80, 14),
    ("Rechnung Nr. RE-2026-0042 vom 07.10.2026", 60, 130, 11),
    ("Uebernachtung 2 x 80,00 EUR", 60, 400, 11),
    ("Gesamt 160,00 EUR", 300, 700, 12),
]


def receipt_pdf(pages: int = 1, lines=RECEIPT_LINES) -> bytes:
    pdf = Pdf()
    for page in range(pages):
        if page:
            pdf.new_page()
        for text, x, y, size in lines:
            pdf.text(x, y, text, size=size)
    return pdf.output()


def bill(lines: list[str], page: int = 0, step: float = 0.03, top: float = 0.04) -> list[Word]:
    """One word per token; line ``i`` sits at ``y = top + i * step``, tokens left to right by their length."""
    out: list[Word] = []
    for i, line in enumerate(lines):
        x = 0.05
        for token in line.split():
            w = round(0.012 * len(token), 4)
            out.append(Word(token, page, round(x, 4), round(top + i * step, 4), w, 0.02))
            x += w + 0.012
    return out


def read(words: list[Word], engine: str = "pdf_text") -> ReadResult:
    return ReadResult(engine=engine, words=words, pages_read=1, pages_total=1)  # type: ignore[arg-type]


def context(history: str | None = None, duplicate: bool = False, today: date = date(2026, 11, 2)) -> Context:
    return Context(today=today, category_of_vendor=lambda v: history, possible_duplicate=lambda d: duplicate)
