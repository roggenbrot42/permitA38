"""The seam between the readers and the rules (docs/specs/receipt-scan.md).

A reader (e-invoice, PDF text, OCR, later a model) produces a ``ReadResult``; ``textrules.parse`` turns its words into
an ``Extraction``, and an e-invoice reader fills ``ReadResult.extraction`` directly. Everything here is plain, untrusted
data: money is ``Decimal``, dates are ISO strings, positions are fractions of the page from the top left.
"""

from dataclasses import asdict, dataclass, field, fields, replace
from decimal import Decimal
from typing import Any, Literal

Engine = Literal["einvoice", "pdf_text", "ocr", "vision", "none"]
DocType = Literal["invoice", "receipt", "credit_note"]


@dataclass(frozen=True)
class Word:
    text: str
    page: int
    x: float
    y: float
    w: float
    h: float


MAX_WORD_CHARS = 64
MAX_WORDS = 8000
MAX_LINE_WORDS = 200


def limit_words(words: list[Word]) -> tuple[list[Word], bool]:
    """Untrusted words bounded for the text rules: each word cut to ``MAX_WORD_CHARS``, at most ``MAX_WORDS`` words.
    The flag says that words were left out (the caller must then count the bill as only partly read)."""
    capped = len(words) > MAX_WORDS
    out = []
    for w in words[:MAX_WORDS]:
        out.append(replace(w, text=w.text[:MAX_WORD_CHARS]) if len(w.text) > MAX_WORD_CHARS else w)
    return out, capped


@dataclass
class Item:
    description: str = ""
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    vat_code: str | None = None  # the letter or digit printed on the line, resolved through VatGroup.code


@dataclass
class VatGroup:
    rate: Decimal | None = None
    code: str | None = None
    net: Decimal | None = None
    vat: Decimal | None = None
    gross: Decimal | None = None


@dataclass
class Adjustment:
    kind: Literal["discount", "charge", "tip", "rounding"]
    amount: Decimal  # signed, as it changes the total


@dataclass
class Code:
    """A QR code found in a raster: its text and its box (fractions of the page)."""

    text: str
    page: int
    x: float
    y: float
    w: float
    h: float


@dataclass
class Qr:
    """What a QR code on the bill says (``qrcodes.parse_code``)."""

    kind: Literal["tse", "epc"]
    groups: list[tuple[Decimal, Decimal]] = field(default_factory=list)  # (rate, gross) with gross != 0
    payments: list[tuple[Decimal, str]] = field(default_factory=list)  # (amount, "Bar" | "Unbar")
    date: str | None = None
    number: str | None = None  # "<serial first 8>-<transaction>"
    tse_id: str | None = None  # "<whole serial>-<transaction>", the identity of the till receipt
    epc_amount: Decimal | None = None
    printed_total: Decimal | None = None  # the total the text rules read before the code replaced it
    printed_from_ocr: bool = False


@dataclass
class Extraction:
    vendor: str | None = None
    vat_id: str | None = None
    tax_no: str | None = None  # "Steuernummer" (national tax number), the other way to identify the supplier
    invoice_no: str | None = None
    number_weak: bool = False  # the number comes from a till or receipt label, not from an invoice label
    bill_date: str | None = None
    due_date: str | None = None
    paid_date: str | None = None
    currency: str | None = None
    doc_type: DocType | None = None
    payment_signals: list[str] = field(default_factory=list)  # "cash", "card", "open", "prepaid", "paid"
    text_signals: list[str] = field(default_factory=list)  # "reverse_charge", "small_business", "credit_note", "meal"
    items: list[Item] = field(default_factory=list)
    vat_groups: list[VatGroup] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    subtotal: Decimal | None = None
    total: Decimal | None = None
    prepaid: Decimal | None = None
    amount_due: Decimal | None = None
    tendered: Decimal | None = None
    change: Decimal | None = None
    card_amount: Decimal | None = None
    net_total: Decimal | None = None  # "Nettoumsatz": printed sum of the nets of all VAT groups
    vat_total: Decimal | None = None  # "Steuersumme": printed sum of the VAT of all VAT groups
    competing_totals: list[Decimal] = field(default_factory=list)  # more than one value: ambiguous_total
    competing_dates: list[str] = field(default_factory=list)  # more than one value: ambiguous_date
    qr: Qr | None = None
    einvoice_sums: dict[str, Decimal] = field(default_factory=dict)  # EN 16931 terms by id: "BT-106", "BT-109", ...
    # source words of a value: "total", "vendor", "item:3", "vat_group:0", ... The overlay boxes are built from these.
    sources: dict[str, list[Word]] = field(default_factory=dict)


@dataclass
class ReadResult:
    engine: Engine = "none"
    words: list[Word] = field(default_factory=list)  # in reading order
    extraction: Extraction | None = None  # only from a reader that needs no text rules
    pages_read: int = 0
    pages_total: int = 0
    error: str | None = None  # a fixed code such as "timeout", never file content
    alternatives: list[list[Word]] = field(default_factory=list)  # other OCR page modes, best guess first
    codes: list[Code] = field(default_factory=list)  # QR codes found on the pages
    rotation: int = 0  # degrees clockwise the photo was turned to be read (EXIF included): 0, 90, 180 or 270


def to_jsonable(value: Any) -> Any:
    """Dataclasses to plain JSON values; a ``Decimal`` becomes its exact string."""
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


_MONEY = {f.name for cls in (Item, VatGroup, Extraction) for f in fields(cls) if "Decimal" in str(f.type)}


def _money(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _record(cls: type, data: dict) -> Any:
    known = {f.name for f in fields(cls)}
    return cls(**{k: _money(v) if k in _MONEY else v for k, v in data.items() if k in known})


def extraction_from_json(data: dict) -> Extraction:
    plain = {
        k: v
        for k, v in data.items()
        if k not in ("items", "vat_groups", "adjustments", "competing_totals", "sources", "qr")
    }
    out: Extraction = _record(Extraction, {k: v for k, v in plain.items() if k != "einvoice_sums"})
    out.items = [_record(Item, x) for x in data.get("items") or []]
    out.vat_groups = [_record(VatGroup, x) for x in data.get("vat_groups") or []]
    out.adjustments = [Adjustment(x["kind"], Decimal(str(x["amount"]))) for x in data.get("adjustments") or []]
    out.competing_totals = [Decimal(str(x)) for x in data.get("competing_totals") or []]
    out.einvoice_sums = {k: Decimal(str(v)) for k, v in (data.get("einvoice_sums") or {}).items()}
    q = data.get("qr")
    if q:
        out.qr = Qr(
            kind=q["kind"],
            groups=[(Decimal(str(r)), Decimal(str(g))) for r, g in q.get("groups") or []],
            payments=[(Decimal(str(a)), k) for a, k in q.get("payments") or []],
            date=q.get("date"),
            number=q.get("number"),
            tse_id=q.get("tse_id"),
            epc_amount=_money(q.get("epc_amount")),
            printed_total=_money(q.get("printed_total")),
            printed_from_ocr=bool(q.get("printed_from_ocr")),
        )
    out.sources = {k: [Word(**w) for w in v] for k, v in (data.get("sources") or {}).items()}
    return out


def read_result_from_json(data: dict) -> ReadResult:
    extraction = data.get("extraction")
    return ReadResult(
        engine=data.get("engine", "none"),
        words=[Word(**w) for w in data.get("words") or []],
        alternatives=[[Word(**w) for w in words] for words in data.get("alternatives") or []],
        extraction=extraction_from_json(extraction) if extraction else None,
        pages_read=int(data.get("pages_read") or 0),
        pages_total=int(data.get("pages_total") or 0),
        error=data.get("error"),
        codes=[Code(**c) for c in data.get("codes") or []],
        rotation=int(data.get("rotation") or 0) if data.get("rotation") in (0, 90, 180, 270) else 0,
    )


def cap_words(result: ReadResult) -> ReadResult:
    """Each word cut to ``MAX_WORD_CHARS`` and at most ``MAX_WORDS`` words. Words left out of the main reading make
    ``pages_read`` smaller than ``pages_total``, so the bill gets the flag ``pages_skipped`` and is never clean."""
    result.words, capped = limit_words(result.words)
    if capped and result.extraction is None:  # an e-invoice takes its values from the XML, not from the words
        result.pages_read = min(result.pages_read, result.words[-1].page)  # the page of the cut is not fully read
    kept = []
    for alt in result.alternatives:
        cut, alt_capped = limit_words(alt)
        if not alt_capped:
            kept.append(cut)
    result.alternatives = kept
    return result
