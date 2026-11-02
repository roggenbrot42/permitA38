"""The closed set of receipt flags (docs/specs/receipt-scan.md, "Flags"). Any flag stops automatic booking."""

FLAGS: dict[str, str] = {
    "unreadable": "Could not read this receipt.",
    "pages_skipped": "Only part of this bill was read.",
    "no_total": "No total found.",
    "ambiguous_total": "Several possible totals.",
    "no_date": "No date found.",
    "ambiguous_date": "Several possible dates.",
    "date_implausible": "Check the date.",
    "no_vendor": "No vendor found.",
    "no_invoice_number": "No invoice number found. Enter it, or confirm that the bill has none.",
    "no_vat": "No VAT rate found.",
    "no_supplier_tax_id": "No supplier tax number or VAT id found.",
    "vat_rate_unusual": "Unusual VAT rate.",
    "vat_mismatch": "Printed VAT does not match the amounts.",
    "total_mismatch": "The amounts do not add up to the total.",
    "items_mismatch": "The items do not add up.",
    "payment_mismatch": "The payment does not match the total.",
    "not_verifiable": "Nothing on the bill confirms this total.",
    "payment_unclear": "Check how and when this was paid.",
    "foreign_currency": "Not in euros.",
    "credit_note": "Looks like a refund or credit note.",
    "reverse_charge": "Reverse charge bill.",
    "possible_duplicate": "Maybe already booked.",
    "model_read": "Read by a model. Please check.",
    "invalid_data": "Some values could not be used.",
}


def flag(code: str) -> dict[str, str]:
    """``{"code", "message"}`` of one flag; an unknown code is a programming error."""
    return {"code": code, "message": FLAGS[code]}


def ordered(codes: set[str] | list[str]) -> list[dict[str, str]]:
    """The given codes as flag dicts, once each, in the order of the table above."""
    wanted = set(codes)
    return [flag(c) for c in FLAGS if c in wanted]
