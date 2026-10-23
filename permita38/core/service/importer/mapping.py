"""Column mapping: canonical fields, German and English header synonyms, auto-detection and mapping files.

Detection compares normalised header names exactly (case, umlauts, punctuation and spacing are ignored; ``ä`` is
``ae``). A header is never used for two fields. Unknown headers are reported, never guessed. Community mapping
presets belong in ``permita38/core/service/importer/presets/`` as JSON (``{"columns": {...}, "options": {...}}``)
and are loaded with ``--mapping``; no vendor-specific preset is shipped.
"""

from __future__ import annotations

import json
import re
from typing import Any

from permita38.core.errors import ServiceError

DOCUMENT_FIELDS = (
    "number", "date", "due_date", "paid_date", "status", "customer_name", "customer_no", "vat_id", "tax_number",
    "email", "net", "vat", "vat_rate", "gross", "currency", "title", "reference", "external_id",
    "line_description", "line_qty", "line_price",
)  # fmt: skip
CUSTOMER_FIELDS = (
    "name", "customer_no", "vat_id", "tax_number", "email", "phone", "street", "zip", "city", "country", "contact",
    "notes",
)  # fmt: skip
EXPENSE_FIELDS = ("date", "vendor", "description", "net", "vat", "vat_rate", "gross", "category", "currency", "external_id")  # fmt: skip

SYNONYMS: dict[str, tuple[str, ...]] = {
    "number": (
        "number",
        "rechnungsnummer",
        "rechnungsnr",
        "rechnung nr",
        "rechnung",
        "belegnummer",
        "beleg nr",
        "belegnr",
        "invoice number",
        "invoice no",
        "invoice nr",
        "nummer",
        "nr",
        "re nr",
    ),  # fmt: skip
    "date": ("date", "datum", "rechnungsdatum", "belegdatum", "invoice date", "rechnungs datum"),
    "due_date": (
        "due date",
        "due",
        "faellig",
        "faelligkeit",
        "faelligkeitsdatum",
        "zahlbar bis",
        "valid until",
        "gueltig bis",
        "due on",
    ),  # fmt: skip
    "paid_date": (
        "paid date",
        "paid on",
        "paid",
        "bezahlt am",
        "bezahlt",
        "zahlungseingang",
        "zahlungsdatum",
        "payment date",
    ),  # fmt: skip
    "status": ("status",),
    "customer_name": (
        "customer",
        "customer name",
        "client",
        "kunde",
        "kundenname",
        "kunde name",
        "firma",
        "firmenname",
        "name",
    ),  # fmt: skip
    "customer_no": ("customer number", "customer no", "kundennummer", "kundennr", "kunden nr"),
    "vat_id": (
        "vat id",
        "vat number",
        "ust id",
        "ustid",
        "ust idnr",
        "ust id nr",
        "umsatzsteuer id",
        "umsatzsteuer identifikationsnummer",
    ),  # fmt: skip
    "tax_number": ("tax number", "tax id", "steuernummer", "steuer nr"),
    "email": ("email", "e mail", "mail", "kunden email"),
    "net": ("net", "net amount", "netto", "nettobetrag", "netto betrag", "summe netto", "netto summe"),
    "vat": (
        "vat",
        "vat amount",
        "ust",
        "ust betrag",
        "umsatzsteuer",
        "mwst",
        "mwst betrag",
        "steuerbetrag",
        "tax amount",
    ),  # fmt: skip
    "vat_rate": ("vat rate", "tax rate", "ust satz", "umsatzsteuersatz", "steuersatz", "mwst satz"),
    "gross": (
        "gross",
        "gross amount",
        "brutto",
        "bruttobetrag",
        "brutto betrag",
        "summe brutto",
        "total",
        "gesamt",
        "gesamtbetrag",
    ),  # fmt: skip
    "currency": ("currency", "waehrung"),
    "title": ("title", "subject", "betreff", "titel", "bezeichnung"),
    "reference": ("reference", "order number", "referenz", "ihre referenz", "bestellnummer"),
    "external_id": ("external id", "externe id", "id"),
    "line_description": (
        "line description",
        "position",
        "positionsbezeichnung",
        "leistung",
        "artikel",
        "artikelbezeichnung",
        "item",
    ),  # fmt: skip
    "line_qty": ("line qty", "menge", "anzahl", "quantity", "qty"),
    "line_price": ("line price", "einzelpreis", "stueckpreis", "unit price", "preis", "price"),
    "vendor": ("vendor", "supplier", "lieferant", "lieferantenname"),
    "description": ("description", "beschreibung", "verwendungszweck", "text"),
    "category": ("category", "kategorie"),
    "name": ("name", "kunde", "kundenname", "customer", "firma"),
    "phone": ("phone", "telefon", "tel", "telefonnummer"),
    "street": ("street", "strasse", "straße", "adresse", "address"),
    "zip": ("zip", "plz", "postleitzahl", "postcode"),
    "city": ("city", "ort", "stadt"),
    "country": ("country", "land"),
    "contact": ("contact", "ansprechpartner", "kontakt"),
    "notes": ("notes", "notizen", "bemerkung", "bemerkungen"),
}
OPTIONS = {"decimal": ("auto", "comma", "point"), "date_order": ("dmy", "mdy"),
           "on_duplicate": ("skip", "overwrite"), "accept_unverified": (True, False)}  # fmt: skip

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ß": "ss"})


def normalize_header(text: str) -> str:
    s = str(text or "").translate(_UMLAUTS).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def canonical_fields(doc_type: str) -> tuple[str, ...]:
    return {"customers": CUSTOMER_FIELDS, "expenses": EXPENSE_FIELDS}.get(doc_type, DOCUMENT_FIELDS)


def auto_map(headers: list[str], doc_type: str) -> tuple[dict[str, str], list[str]]:
    """({field: header}, unmapped headers). Fields are tried in the order of :func:`canonical_fields`."""
    normalized = {h: normalize_header(h) for h in headers}
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for field_name in canonical_fields(doc_type):
        wanted = {normalize_header(s) for s in SYNONYMS.get(field_name, ())} | {normalize_header(field_name)}
        for h in headers:
            if h in used or not normalized[h]:
                continue
            if normalized[h] in wanted:
                mapping[field_name] = h
                used.add(h)
                break
    return mapping, [h for h in headers if h not in used and h]


def parse_mapping_file(raw: str | dict | None) -> tuple[dict[str, str], dict[str, Any]]:
    """Accepts ``{"columns": {...}, "options": {...}}`` or a flat ``{field: header}`` object."""
    if raw in (None, ""):
        return {}, {}
    obj = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(obj, dict):
        raise ServiceError(400, "The mapping must be a JSON object")
    columns = obj.get("columns", {k: v for k, v in obj.items() if k != "options"})
    options = obj.get("options", {})
    if not isinstance(columns, dict) or not isinstance(options, dict):
        raise ServiceError(400, "The mapping must contain 'columns' and 'options' objects")
    for k, v in columns.items():
        if not isinstance(v, str) or len(v) > 200:
            raise ServiceError(400, "Mapping values must be header names (text, max 200 characters)")
    options = {k: v for k, v in options.items() if k in OPTIONS}
    for k, v in options.items():
        if v not in OPTIONS[k]:
            raise ServiceError(400, f"Option {k!r} must be one of {', '.join(map(str, OPTIONS[k]))}")
    known = set(DOCUMENT_FIELDS + CUSTOMER_FIELDS + EXPENSE_FIELDS)
    return {k: v for k, v in columns.items() if k in known}, options
