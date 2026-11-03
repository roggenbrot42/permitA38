"""Rules for scanning old offers and invoices of the user (docs/specs/document-scan.md, "Reading" and "Reconcile and flags").

Pure functions, no I/O. ``parse`` turns the ``ReadResult`` of a reader into a ``DocExtraction``; ``evaluate`` turns that
into the flags and info notes. Customer matching and duplicates are the business of the service, not of this module.

The label rules of the receipt scan are reused (``textrules``: lines, amounts, dates, VAT table, totals); what differs
for a document *from* the user is the kind from the title, the recipient block, the sender check and the table of lines.
Everything read is outside input: every regex runs on lines cut to ``LINE_CAP`` characters and the number of lines and
words is bounded. ``parse`` never raises.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from ..money import line_amount, vat_amount
from . import textrules
from .einvoice import EInvoiceDoc, Party
from .extraction import Extraction, ReadResult, Word, limit_words
from .textrules import _VAT_ID, _VAT_ID_LABEL, _amounts, _dates, _Line, clean_text, group_lines, parse_amount

Kind = Literal["invoice", "offer", "credit_note", "correction", "cancellation"]

LINE_CAP = 400  # every regex sees at most this many characters of a line
MAX_LINES = 600
MAX_DOC_LINES = 200
TITLE_ZONE = 0.4  # the title is looked for in the top 40 % of page 1
WINDOW_X = 0.5  # the address window: the left half of page 1 ...
WINDOW_Y = 0.55  # ... above this height (top-left quarter plus the room a tall block needs)
MAX_BLOCK_LINES = 6
DEFAULT_DESCRIPTION = "Leistungen laut Originalbeleg"
ALLOWED_RATES = (Decimal(0), Decimal(7), Decimal(19))
HISTORIC_RATES = (Decimal(5), Decimal(16))  # 2020-07-01 .. 2020-12-31
HISTORIC_FROM, HISTORIC_TO = date(2020, 7, 1), date(2020, 12, 31)

UNSUPPORTED = ("credit_note", "correction", "cancellation")
INFO_LINES_SUMMARISED = "lines_summarised"

FLAGS: dict[str, str] = {
    "unreadable": "Could not read this document.",
    "pages_skipped": "Only part of this document was read.",
    "kind_unclear": "Offer or invoice? The title is not clear.",
    "kind_unsupported": "Credit notes, corrections and cancellations are not supported.",
    "no_number": "No document number found.",
    "ambiguous_number": "Several possible document numbers.",
    "no_date": "No date found.",
    "ambiguous_date": "Several possible dates.",
    "date_implausible": "Check the date.",
    "no_customer": "No customer address found.",
    "not_my_document": "This document does not come from you.",
    "foreign_currency": "Not in euros.",
    "several_vat_rates": "Several VAT rates; a document has one.",
    "vat_rate_unusual": "Unusual VAT rate.",
    "no_vat": "No VAT rate found.",
    "totals_mismatch": "Net, VAT and gross do not add up.",
    "ambiguous_total": "Several possible totals.",
}
NOTES: dict[str, str] = {INFO_LINES_SUMMARISED: "The lines could not be proven and were replaced by one summary line."}


@dataclass(frozen=True)
class OwnIdentity:
    """Who the user is, from Settings: any of these on page 1 makes the document the user's own."""

    company_name: str = ""
    owner_name: str = ""
    vat_id: str = ""
    tax_number: str = ""


@dataclass
class Recipient:
    name: str | None = None
    street: str | None = None
    zip: str | None = None
    city: str | None = None
    country: str | None = None
    vat_id: str | None = None
    email: str | None = None


@dataclass
class DocLine:
    description: str = ""
    quantity: Decimal | None = None
    unit: str | None = None
    price: Decimal | None = None
    amount: Decimal | None = None


@dataclass
class DocVat:
    rate: Decimal
    base: Decimal | None = None
    vat: Decimal | None = None


@dataclass
class DocExtraction:
    kind: Kind | None = None
    title_word: str | None = None
    number: str | None = None
    number_candidates: list[str] = field(default_factory=list)  # more than one: ambiguous_number
    date: str | None = None
    competing_dates: list[str] = field(default_factory=list)
    due_date: str | None = None  # printed, else date + due_days
    due_days: int | None = None
    valid_until: str | None = None
    service_from: str | None = None
    service_to: str | None = None
    recipient: Recipient | None = None
    sender_ok: bool = False
    lines: list[DocLine] = field(default_factory=list)
    lines_summarised: bool = False  # the printed lines were not proven; ``lines`` holds one summary line
    net: Decimal | None = None
    vat_groups: list[DocVat] = field(default_factory=list)
    vat_total: Decimal | None = None
    gross: Decimal | None = None
    competing_totals: list[Decimal] = field(default_factory=list)
    currency: str | None = None
    small_business: bool = False
    sources: dict[str, list[Word]] = field(default_factory=dict)
    engine: str = "none"
    error: str | None = None
    pages_read: int = 0
    pages_total: int = 0


# --------------------------------------------------------------------------------------------------------------------
# helpers

_KIND_RE = re.compile(
    r"(?<![\w])(rechnungskorrektur|storno[\s\-]*rechnung|stornorechnung|storno|gutschrift|credit\s+note|rechnung|invoice"
    r"|angebot|kostenvoranschlag|offer|quote)(?![\w])",
    re.IGNORECASE,
)
_KIND_OF = {
    "rechnungskorrektur": "correction",
    "storno": "cancellation",
    "stornorechnung": "cancellation",
    "gutschrift": "credit_note",
    "credit note": "credit_note",
    "rechnung": "invoice",
    "invoice": "invoice",
    "angebot": "offer",
    "kostenvoranschlag": "offer",
    "offer": "offer",
    "quote": "offer",
}

_NUMBER_VALUE = r"[\s:#.]*([A-Za-z0-9][A-Za-z0-9\-/_.]{0,49})"
_NUMBER_LABELS_INVOICE = (
    r"rechnung(?:s)?[\s\-]*(?:nr|nummer|no)\b\.?",
    r"invoice\s*(?:no|number|nr|num|#)\b\.?",
)
_NUMBER_LABELS_OFFER = (
    r"angebot(?:s)?[\s\-]*(?:nr|nummer|no)\b\.?",
    r"kostenvoranschlag(?:s)?[\s\-]*(?:nr|nummer)\b\.?",
    r"(?:offer|quote|quotation)\s*(?:no|number|nr|#)\b\.?",
)
_NUMBER_LABEL_PLAIN = r"(?<![\w\-])(?:nr|no)\b\.?"
_TITLE_NUMBER = re.compile(
    r"\s*(?:rechnung|angebot|kostenvoranschlag|invoice|offer|quote)\s+([A-Za-z0-9][A-Za-z0-9\-/_.]{0,49})\s*$", re.I
)
_NOT_MY_NUMBER = re.compile(r"kunde|bestell|auftrag|projekt|lieferschein|steuer|order|customer|vertrag|referenz", re.I)

_IGNORED_DATE = re.compile(
    r"bestell|order|auftrag|lieferschein|angebot\s+vom|ihr(?:e|en)?\s+(?:angebot|anfrage)|vertrag|kunde"
)
_VALID_DATE = re.compile(r"gültig|gueltig|bindefrist|bindung\s+bis|valid\s+(?:until|through|to)|validity")
_DUE_DATE = re.compile(
    r"fällig|faellig|zahlbar\s+bis|zahlungsziel|due\s+date|due\s+on|payable\s+(?:until|by)|zahlbar"
    r"|überweis|ueberweis|begleichen|bitte\s+zahlen|zahlen\s+sie"
)
_SERVICE_DATE = re.compile(
    r"zeitraum|leistungszeitraum|leistungsdatum|leistungstermin|lieferdatum|service\s+(?:date|period)|delivery\s+date|leistung"
)
_STRONG_DATE = re.compile(
    r"rechnungsdatum|angebotsdatum|invoice\s+date|offer\s+date|date\s+of\s+issue|ausstellungsdatum|datum\s+der\s+rechnung"
)
_WEAK_DATE = re.compile(r"datum|\bdate\b")
_RANGE_JOIN = re.compile(r"^[\s\W]*(?:bis|und|to|until|ende|end|-|–|—)?[\s\W]*$")
_DISCOUNT = re.compile(r"skonto|discount|rabatt")
_SENTENCE_END = re.compile(r"\.(?:\s|$)")
_DUE_DAYS = (
    re.compile(
        r"(?:zahlbar|fällig|faellig|zahlung\w*)\s+(?:innerhalb|binnen)(?:\s+von)?\s+(\d{1,3})\s+(?:kalender|werk)?tage?n?"
    ),
    re.compile(r"within\s+(\d{1,3})\s+days"),
    re.compile(
        r"(?:überweis|ueberweis|zahl|begleich)[^\n.]{0,100}?innerhalb(?:\s+von|\s+der\s+(?:nächsten|naechsten|folgenden))?"
        r"\s+(\d{1,3})\s+(?:kalender|werk)?tage?n?"
    ),
    re.compile(r"zahlungsziel\W{0,3}(\d{1,3})\s+tage?n?"),
    re.compile(r"(\d{1,3})\s+tage?n?\s+(?:netto|rein\s+netto|nach\s+rechnungs)"),
)

_NET_LABEL = re.compile(
    textrules.NET_TOTAL + r"|nettobetrag|nettosumme|netto-?\s?gesamt|net\s+amount|^\s*netto\b|zwischensumme|subtotal",
    re.IGNORECASE,
)
_STRONG_NET = re.compile(textrules.NET_TOTAL + r"|nettobetrag|nettosumme|net\s+amount|^\s*netto\b", re.IGNORECASE)
_SMALL_BUSINESS = re.compile(r"§\s?19(?:\s+abs\.?\s*\d+)?(?:\s+satz\s*\d+)?\s*ustg|kleinunternehmer", re.IGNORECASE)
_ZIP_LINE = re.compile(r"^(?:(?:[A-Z]{1,2})[\s\-]?\d{4,5}|\d{5})\s+([^\W\d_][^\n]{0,59})$")
_ZIP_PARTS = re.compile(r"^(?:([A-Z]{1,2})[\s\-]?)?(\d{4,5})\s+(.+)$")
_NOT_ADDRESS = re.compile(
    r"rechnung|angebot|datum|\bdate\b|kunden[\s\-]?(?:nr|nummer)|seite|\bnr\b|nummer|betreff|invoice|tel\b|telefon|fax|iban|\bbic\b|www\.|http",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+\-]{1,64}@[\w\-]{1,63}(?:\.[\w\-]{1,63}){1,4}")
_COUNTRIES = {
    "deutschland": "DE", "germany": "DE", "österreich": "AT", "oesterreich": "AT", "austria": "AT",
    "schweiz": "CH", "switzerland": "CH", "luxemburg": "LU", "luxembourg": "LU", "niederlande": "NL",
    "netherlands": "NL", "frankreich": "FR", "france": "FR", "italien": "IT", "italy": "IT", "polen": "PL",
}  # fmt: skip
_COUNTRY_PREFIX = {
    "D": "DE",
    "DE": "DE",
    "A": "AT",
    "AT": "AT",
    "CH": "CH",
    "L": "LU",
    "LU": "LU",
    "NL": "NL",
    "F": "FR",
}

_DESC_COL = re.compile(r"beschreibung|leistung|bezeichnung|description|artikel|tätigkeit")
_AMOUNT_COL = re.compile(r"gesamt|betrag|summe|amount|total|netto")
_TABLE_STOP = re.compile(
    r"\s*(?:zwischensumme|summe|nettobetrag|nettosumme|netto\b|gesamt|rechnungsbetrag|endbetrag|total|subtotal|zzgl|zuzüglich|"
    r"mwst|ust\b|umsatzsteuer|\d{1,2}\s?%\s?(?:mwst|ust)|übertrag|uebertrag|vielen dank|bankverbindung|iban|angebotssumme)"
)
_COLUMN_WORD = re.compile(
    r"\bmenge\b|\banzahl\b|\beinh\b|\beinheit\b|einzel|\bpreis\b|\bstk\b|\bqty\b|\bprice\b|\bpos\b"
)
_NUM_TOKEN = re.compile(r"^\d{1,9}(?:[.,]\d{1,4})?$")
_POS_TOKEN = re.compile(r"^\d{1,3}\.?$")
_CURRENCY_TOKENS = {"€", "eur", "euro"}


def _norm(text: str) -> str:
    return re.sub(r"[\s.\-/·,;:]+", "", text.casefold())


def _search(pattern: re.Pattern | str, text: str) -> bool:
    return re.search(pattern, text) is not None


def _lines(words: list[Word]) -> list[_Line]:
    lines = group_lines(limit_words(words)[0])[:MAX_LINES]
    for ln in lines:
        ln.text = ln.text[:LINE_CAP]
        ln.low = ln.low[:LINE_CAP]
    return lines


def _height(ln: _Line) -> float:
    return max((w.h for w in ln.words), default=0.0)


def _top(ln: _Line) -> float:
    return min((w.y for w in ln.words), default=0.0)


def _plus_days(iso: str, days: int) -> str | None:
    try:
        return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()
    except (ValueError, OverflowError):
        return None


def _is_sender_line(ln: _Line, median_h: float) -> bool:
    return bool(re.search(r"\s[·|•]\s|\s-\s.*\s-\s", ln.text)) or (median_h > 0 and _height(ln) < 0.8 * median_h)


# --------------------------------------------------------------------------------------------------------------------
# kind, number, dates


def _kind(lines: list[_Line], first_page: int, x: DocExtraction) -> None:
    found: list[tuple[float, str, str, list[Word]]] = []  # (height, kind, word, words)
    for ln in lines:
        if ln.page != first_page or _top(ln) >= TITLE_ZONE:
            continue
        for m in _KIND_RE.finditer(ln.text):
            key = re.sub(r"[\s\-]+", " ", m.group(1).lower())
            kind = _KIND_OF.get(key) or _KIND_OF.get(key.replace(" ", ""))
            if kind is None:
                continue
            if len(ln.words) > 7 and m.start() > 0:
                continue  # a word inside a sentence is not a heading
            words = ln.words_in(m.start(), m.end())
            found.append((max((w.h for w in words), default=0.0), kind, m.group(1), words))
    if not found:
        return
    tallest = max(f[0] for f in found)
    top = [f for f in found if f[0] >= 0.9 * tallest]
    kinds = {f[1] for f in top}
    unsupported = [k for k in kinds if k in UNSUPPORTED]
    if unsupported:
        kinds = {unsupported[0]}
    if len(kinds) == 1:
        pick = next(f for f in top if f[1] in kinds)
        x.kind = pick[1]  # type: ignore[assignment]
        x.title_word = clean_text(pick[2], 40)
        x.sources["kind"] = pick[3]
    else:
        x.title_word = None


def _number(lines: list[_Line], x: DocExtraction) -> None:
    specific: list[str] = []
    if x.kind in (None, "invoice", "credit_note", "correction", "cancellation"):
        specific += _NUMBER_LABELS_INVOICE
    if x.kind in (None, "offer"):
        specific += _NUMBER_LABELS_OFFER
    found: list[tuple[int, str, list[Word]]] = []
    for idx, ln in enumerate(lines):
        if len(ln.low) != len(ln.text):  # offsets of ``low`` would not fit ``text``
            continue
        for tier, labels in ((0, specific), (1, [_NUMBER_LABEL_PLAIN])):
            hit = False
            for label in labels:
                for m in re.finditer(label, ln.low):
                    if tier == 1 and _NOT_MY_NUMBER.search(ln.low[max(0, m.start() - 14) : m.start()]):
                        continue
                    tail = re.match(_NUMBER_VALUE, ln.low[m.end() :])
                    value_start = m.end() + (tail.start(1) if tail else 0)
                    value = ln.text[value_start : m.end() + tail.end(1)] if tail else ""
                    words = ln.words_in(value_start, value_start + len(value)) if value else []
                    value = value.rstrip(".-/_")
                    if not value or not _search(r"\d", value):
                        # the value may stand below the label
                        nxt = lines[idx + 1] if idx + 1 < len(lines) else None
                        first = nxt.words[0] if nxt and nxt.page == ln.page and nxt.words else None
                        label_words = ln.words_in(m.start(), m.end())
                        if first is not None and label_words and abs(first.x - label_words[0].x) < 0.1:
                            below = first.text[:50].rstrip(".-/_")
                            if _search(r"\d", below) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-/_.]*", below):
                                found.append((tier, below, [first]))
                                hit = True
                        continue
                    if len(value) <= 50:
                        found.append((tier, value, words))
                        hit = True
            if hit:
                break  # the specific label of this line wins over the plain "Nr."
    if not found:
        for ln in lines:  # a title line "Rechnung 2025-3-XYZ": the title word and a token with a digit
            tm = _TITLE_NUMBER.match(ln.text)
            if tm and _top(ln) < TITLE_ZONE:
                found.append((2, tm.group(1).rstrip(".-/_"), ln.words_in(tm.start(1), tm.end(1))))
                break
    if not found:
        return
    best = min(f[0] for f in found)
    chosen = [f for f in found if f[0] == best]
    values = list(dict.fromkeys(f[1] for f in chosen))
    x.number_candidates = values
    if len(values) == 1:
        x.number = values[0]
        x.sources["number"] = chosen[0][2]


def _dates_and_terms(lines: list[_Line], first_page: int, x: DocExtraction) -> None:
    labelled: list[tuple[int, str, list[Word]]] = []
    due: str | None = None
    valid: str | None = None
    service: list[tuple[str, list[Word]]] = []
    for idx, ln in enumerate(lines):
        last_service = False
        for iso, start, end, before in _dates(ln):
            label = before[: max(0, len(before) - (end - start))][-80:]
            words = ln.words_in(start, end)
            if (
                not re.sub(r"\b(?:bis|zum|spätestens|am)\b", "", label).strip(" :-–—")
                and idx
                and not _dates(lines[idx - 1])
                and lines[idx - 1].page == ln.page
            ):
                label = lines[idx - 1].low[-60:]  # label row above the value row
            if last_service and _RANGE_JOIN.match(label):
                service.append((iso, words))
                last_service = False
                continue
            last_service = False
            if _IGNORED_DATE.search(label):
                continue
            if _VALID_DATE.search(label):
                valid = valid or iso
                if "valid_until" not in x.sources:
                    x.sources["valid_until"] = words
            elif _DUE_DATE.search(label):
                if due is None:
                    due = iso
                    x.sources["due_date"] = words
            elif _SERVICE_DATE.search(label):
                service.append((iso, words))
                last_service = True
            elif _STRONG_DATE.search(label):
                labelled.append((0, iso, words))
            elif _WEAK_DATE.search(label):
                labelled.append((1, iso, words))
            elif (
                ln.page == first_page
                and _top(ln) < TITLE_ZONE
                and re.search(r"[^\W\d_]{3,}[^\d]{0,3},\s*(?:den\s+|am\s+)?$", label)
            ):
                labelled.append((2, iso, words))  # "Musterstadt, 05.03.2024"
    if labelled:
        best = min(t for t, _d, _w in labelled)
        chosen = [(d, w) for t, d, w in labelled if t == best]
        distinct = list(dict.fromkeys(d for d, _ in chosen))
        if len(distinct) > 1:
            x.competing_dates = distinct
        else:
            x.date = distinct[0]
            x.sources["date"] = chosen[0][1]
    x.valid_until = valid
    x.due_date = due
    if service:
        x.service_from = service[0][0]
        x.service_to = service[1][0] if len(service) > 1 else service[0][0]
        x.sources["service_from"] = service[0][1]
        if len(service) > 1:
            x.sources["service_to"] = service[1][1]
    low = "\n".join(ln.low for ln in lines)
    for pattern in _DUE_DAYS:
        for m in pattern.finditer(low):
            if "werktag" in m.group(0):  # working days are not converted; the Settings term applies
                continue
            breaks: list[int] = [
                low.rfind("\n", 0, m.start()),
                low.rfind(", ", 0, m.start()),
                low.rfind(";", 0, m.start()),
            ]
            breaks.extend([brk.end() - 1 for brk in _SENTENCE_END.finditer(low, 0, m.start())][-1:])
            cl_start = max(breaks)
            ends = [i for i in (low.find("\n", m.end()), low.find(", ", m.end()), low.find(";", m.end())) if i >= 0]
            sentence = _SENTENCE_END.search(low, m.end())
            if sentence:
                ends.append(sentence.start())
            clause = low[cl_start + 1 : min(ends) if ends else len(low)]
            if _DISCOUNT.search(clause):  # a Skonto period is never the due period
                continue
            x.due_days = int(m.group(1))
            break
        if x.due_days is not None:
            break
    if x.due_date is None and x.date and x.due_days is not None:
        x.due_date = _plus_days(x.date, x.due_days)


# --------------------------------------------------------------------------------------------------------------------
# sender and recipient


def _own_keys(own: OwnIdentity) -> list[str]:
    keys = [_norm(own.company_name), _norm(own.owner_name), _norm(own.vat_id), _norm(own.tax_number)]
    return [k for k in keys if len(k) >= 3]


def _is_own(text: str, keys: list[str]) -> bool:
    flat = _norm(text)
    return any(k in flat for k in keys)


def _column_edge(words: list[Word], first_page: int) -> float:
    """Right edge of the left column: the start of the widest empty vertical band (>= 0.05 wide) in the top of page 1
    between 0.25 and ``WINDOW_X`` (the user's own block may stand to the right of the recipient); else ``WINDOW_X``."""
    spans = sorted((w.x, w.x + w.w) for w in words if w.page == first_page and w.y < WINDOW_Y)
    edge, best, reach = WINDOW_X, 0.05, None
    for start, end in spans:
        if reach is not None and start - reach >= best and 0.25 <= reach <= WINDOW_X:
            edge, best = reach + (start - reach) / 2, start - reach
        reach = end if reach is None else max(reach, end)
    return edge


_LABEL_LINE = re.compile(r"(?:an|to)\s*:?", re.I)
_SALUTATION = re.compile(
    r"(?:herrn?|frau|z\.?\s?h\.?|zu\s+händen|der\s+geschäftsführung|geschäftsführung|firma)\b[\s.:]*(.*)", re.I
)
_HOUSE_NUMBER = re.compile(r"\d{1,4}\s?[A-Za-z]?(?:\s?[-/]\s?\d{1,4})?")


def _split_block(texts: list[str]) -> tuple[str | None, str | None]:
    """Name and street from the block lines between the label and the ZIP line (rule 4)."""
    texts = [t for t in texts if not _LABEL_LINE.fullmatch(t.strip())]
    street: str | None = None
    if len(texts) >= 2:
        street = texts.pop()
        if _HOUSE_NUMBER.fullmatch(street.strip()) and texts:
            street = texts.pop() + " " + street.strip()  # the house number alone on its line
    names = []
    for t in texts:
        m = _SALUTATION.fullmatch(t.strip())
        if m is None:
            names.append(t)
        elif m.group(1) and not t.strip().lower().startswith("firma"):
            names.append("\0" + m.group(1))  # a person after a salutation: only if no company-like line exists
        elif m.group(1):
            names.append(m.group(1))
    company = [
        n
        for n in names
        if not n.startswith("\0") and (textrules.LEGAL_LONG.search(n) or textrules.LEGAL_SHORT.search(n))
    ]
    plain = [n for n in names if not n.startswith("\0")]
    pick = (company or plain or [n[1:] for n in names] or [""])[0] or None
    return pick, street


def _recipient(
    lines: list[_Line], words: list[Word], first_page: int, keys: list[str], own_vat: str, x: DocExtraction
) -> None:
    left = [w for w in words if w.page == first_page and w.x < _column_edge(words, first_page) and w.y < WINDOW_Y]
    block_lines = [ln for ln in _lines(left) if ln.words]
    heights = sorted(_height(ln) for ln in block_lines)
    median_h = heights[len(heights) // 2] if heights else 0.0
    chosen: list[_Line] | None = None
    for i, zip_line in enumerate(block_lines):
        if not _ZIP_LINE.match(zip_line.text) or _is_own(zip_line.text, keys):
            continue
        block = [zip_line]
        valid = True
        j = i - 1
        while j >= 0 and len(block) < MAX_BLOCK_LINES:
            prev = block_lines[j]
            if _top(block[0]) - _top(prev) > 1.9 * max(_height(prev), 0.004):
                break  # an empty gap ends the block
            if _is_own(prev.text, keys):
                valid = _is_sender_line(prev, median_h)  # a sender line above the window is fine, the letterhead is not
                break
            if _search(_NOT_ADDRESS, prev.low) or _dates(prev):
                break
            block.insert(0, prev)
            j -= 1
        if valid and len(block) >= 2:
            chosen = block
            break
    if chosen is None:
        return
    rec = Recipient()
    texts = []
    for ln in chosen:
        em = _EMAIL.search(ln.text)
        if em:
            rec.email = clean_text(em.group(0))
            continue
        texts.append(ln.text)
    zip_line = chosen[-1]
    texts = [t for t in texts if t != zip_line.text]
    name, street = _split_block(texts)
    rec.name = clean_text(name)
    rec.street = clean_text(street)
    m = _ZIP_PARTS.match(zip_line.text)
    if m:
        rec.zip = m.group(2)
        rec.city = clean_text(m.group(3))
        rec.country = _COUNTRY_PREFIX.get(m.group(1) or "")
    idx = block_lines.index(zip_line)
    if idx + 1 < len(block_lines):
        nxt = block_lines[idx + 1]
        code = _COUNTRIES.get(nxt.low.strip(" ."))
        if code and _top(nxt) - _top(zip_line) <= 1.9 * max(_height(zip_line), 0.004):
            rec.country = code
            chosen = chosen + [nxt]
    if rec.name is None:
        return
    top, bottom = _top(chosen[0]), _top(chosen[-1])
    for ln in lines:
        if ln.page != first_page or not (top - 0.1 <= _top(ln) <= bottom + 0.25):
            continue
        m2 = _VAT_ID_LABEL.search(ln.text) or _VAT_ID.search(ln.text)
        if m2:
            vid = re.sub(r"\s", "", m2.group(1)).upper()
            if _norm(vid) != _norm(own_vat):
                rec.vat_id = vid
                x.sources["recipient_vat_id"] = ln.words_in(m2.start(1), m2.end(1))
                break
    x.recipient = rec
    x.sources["recipient"] = [w for ln in chosen for w in ln.words]


# --------------------------------------------------------------------------------------------------------------------
# lines (rule 7)


def _split_row(ln: _Line) -> DocLine | None:
    """One table row: ``[pos] description [qty [unit]] [price] amount``; ``None`` when the row has no amount."""
    tokens = [w.text for w in ln.words]
    while tokens and (tokens[-1].lower() in _CURRENCY_TOKENS or tokens[-1].endswith("%")):
        tokens.pop()
    if not tokens:
        return None
    amount = parse_amount(tokens[-1])
    if amount is None:
        return None
    tokens.pop()
    while tokens and tokens[-1].lower() in _CURRENCY_TOKENS:
        tokens.pop()
    price = parse_amount(tokens[-1]) if tokens else None
    if price is not None:
        tokens.pop()
        while tokens and tokens[-1].lower() in _CURRENCY_TOKENS:
            tokens.pop()
    qty: Decimal | None = None
    unit: str | None = None
    if (
        price is not None
        and len(tokens) >= 2
        and _NUM_TOKEN.match(tokens[-2])
        and re.fullmatch(r"[^\W\d_]{1,14}\.?", tokens[-1])
    ):
        qty, unit = Decimal(tokens[-2].replace(",", ".")), tokens[-1]
        del tokens[-2:]
    elif tokens and _NUM_TOKEN.match(tokens[-1]) and len(tokens) >= 2 and price is not None:
        qty = Decimal(tokens[-1].replace(",", "."))
        tokens.pop()
    if len(tokens) >= 2 and _POS_TOKEN.match(tokens[0]):
        tokens.pop(0)
    return DocLine(clean_text(" ".join(tokens), 2000) or "", qty, unit, price, amount)


def _table(lines: list[_Line], x: DocExtraction) -> list[DocLine]:
    out: list[DocLine] = []
    in_table = False
    prev: _Line | None = None
    pending: list[_Line] = []  # description rows above the row that carries the amount
    rows: list[_Line] = []
    for ln in lines:
        low = ln.low
        if _TABLE_STOP.match(low) and (in_table or not _search(_DESC_COL, low)):
            if in_table:
                break
            continue
        is_header = (
            _search(_DESC_COL, low)
            and (_search(_AMOUNT_COL, low) or _search(_COLUMN_WORD, low))
            and not _amounts(ln)
            and len(ln.words) <= 14
        )
        if is_header:
            in_table = True
            prev = None
            pending = []
            continue
        if not in_table:
            continue
        row = _split_row(ln)
        if row is not None:
            if len(out) < MAX_DOC_LINES:
                if pending and _top(ln) - _top(pending[-1]) <= 1.9 * max(_height(pending[-1]), 0.004):
                    above = " ".join(p.text for p in pending)
                    row.description = clean_text(above + " " + row.description, 2000) or ""
                    ln = _Line(ln.page, [w for p in pending for w in p.words] + ln.words)
                out.append(row)
                rows.append(ln)
            pending = []
            prev = ln
        elif out and prev is not None and _top(ln) - _top(prev) <= 1.9 * max(_height(prev), 0.004):
            joined = clean_text(out[-1].description + " " + ln.text, 2000) or ""  # a wrapped description
            out[-1].description = joined
            rows[-1] = _Line(prev.page, rows[-1].words + ln.words)
            prev = ln
        else:
            if pending and _top(ln) - _top(pending[-1]) > 1.9 * max(_height(pending[-1]), 0.004):
                pending = []
            pending.append(ln)
            prev = None
    for n, ln in enumerate(rows):
        x.sources[f"line:{n}"] = list(ln.words)
    return out


def _finalise_lines(x: DocExtraction, printed: list[DocLine], subject: str | None) -> None:
    """Rule 7: the printed lines only when they sum exactly to the net and every ``qty x price`` rounds to its amount."""
    proven = bool(printed) and x.net is not None
    if proven:
        filled: list[DocLine] = []
        for ln in printed:
            qty, price, amount = ln.quantity, ln.price, ln.amount
            if amount is None:
                proven = False
                break
            if qty is None and price is None:
                qty, price = Decimal(1), amount
            elif qty is None and price is not None and price == amount:
                qty = Decimal(1)
            elif qty is None or price is None:
                proven = False
                break
            if line_amount(qty, price) != amount:
                proven = False
                break
            filled.append(DocLine(ln.description or DEFAULT_DESCRIPTION, qty, ln.unit, price, amount))
        if proven and sum((ln.amount or Decimal(0) for ln in filled), Decimal(0)) != x.net:
            proven = False
        if proven:
            x.lines = filled
            return
    x.lines_summarised = True
    x.lines = [DocLine(subject or DEFAULT_DESCRIPTION, Decimal(1), None, x.net, x.net)]


# --------------------------------------------------------------------------------------------------------------------
# totals (rule 6, 8)


def _totals(lines: list[_Line], words: list[Word], x: DocExtraction) -> None:
    ex = textrules.parse(words)  # label rules of the receipt scan: gross, VAT table, VAT total, currency, signals
    x.currency = ex.currency
    flat = " ".join(ln.low for ln in lines)  # a note may be split over two lines
    x.small_business = "small_business" in ex.text_signals or _SMALL_BUSINESS.search(flat) is not None
    if x.small_business:
        ex.vat_groups, ex.vat_total = [], None  # rule 3: the § 19 note wins over any other mention of a rate
    x.gross = ex.total
    x.competing_totals = list(ex.competing_totals)
    if "total" in ex.sources:
        x.sources["gross"] = ex.sources["total"]
    x.vat_total = ex.vat_total
    for i, g in enumerate(ex.vat_groups):
        if g.rate is None:
            continue
        existing = next((d for d in x.vat_groups if d.rate == g.rate), None)
        if existing is None:
            x.vat_groups.append(DocVat(g.rate, g.net, g.vat))
        else:
            existing.base = existing.base if existing.base is not None else g.net
            existing.vat = existing.vat if existing.vat is not None else g.vat
        if f"vat_group:{i}" in ex.sources:
            x.sources.setdefault("vat", ex.sources[f"vat_group:{i}"])
    strong: list[tuple[Decimal, list[Word]]] = []
    weak: list[tuple[Decimal, list[Word]]] = []
    for ln in lines:
        if "%" in ln.text and _search(textrules.TAX_WORDS, ln.low):
            continue
        if not _NET_LABEL.search(ln.low):
            continue
        amounts = _amounts(ln)
        if not amounts:
            continue
        value, start, end = amounts[-1]
        (strong if _STRONG_NET.search(ln.low) else weak).append((value, ln.words_in(start, end)))
    pick = strong or weak
    if pick:
        x.net, x.sources["net"] = pick[-1]
    elif len(x.vat_groups) == 1 and x.vat_groups[0].base is not None:
        x.net = x.vat_groups[0].base
    if x.small_business and not x.vat_groups and (x.net is not None or x.gross is not None):
        base = x.net if x.net is not None else x.gross
        x.vat_groups = [DocVat(Decimal(0), base, Decimal(0))]
        x.vat_total = Decimal(0)
        x.net = base
        if x.gross is None:
            x.gross = base
    elif len(x.vat_groups) == 1 and x.vat_groups[0].vat is None and x.vat_total is not None:
        x.vat_groups[0].vat = x.vat_total
    if len(x.vat_groups) == 1 and x.vat_total is None:
        x.vat_total = x.vat_groups[0].vat


def _subject(lines: list[_Line]) -> str | None:
    for ln in lines:
        m = re.match(r"\s*(?:betreff|projekt|subject|re)\s*:\s*(.{3,200})", ln.text, re.IGNORECASE)
        if m:
            return clean_text(m.group(1))
    return None


# --------------------------------------------------------------------------------------------------------------------
# sender


def _sender_ok(lines: list[_Line], first_page: int, keys: list[str]) -> bool:
    if not keys:
        return False
    text = _norm(" ".join(ln.text for ln in lines if ln.page == first_page))
    return any(k in text for k in keys)


# --------------------------------------------------------------------------------------------------------------------
# e-invoice (rule 9)

_TYPE_KIND = {"380": "invoice", "381": "credit_note", "384": "correction"}


def _from_einvoice(
    read: ReadResult, ex: Extraction, doc: EInvoiceDoc | None, keys: list[str], x: DocExtraction
) -> None:
    if doc is not None:
        x.kind = _TYPE_KIND.get(doc.type_code or "")  # type: ignore[assignment]
        buyer: Party | None = doc.buyer
    else:
        x.kind = {"invoice": "invoice", "credit_note": "credit_note"}.get(ex.doc_type or "")  # type: ignore[assignment]
        buyer = None
    x.title_word = None
    x.number = ex.invoice_no
    x.date = ex.bill_date
    x.due_date = ex.due_date
    x.currency = ex.currency
    sums = ex.einvoice_sums
    x.net = sums.get("BT-109")
    x.vat_total = sums.get("BT-110")
    x.gross = sums.get("BT-112")
    x.vat_groups = [DocVat(g.rate, g.net, g.vat) for g in ex.vat_groups if g.rate is not None]
    seller = _norm(" ".join(filter(None, [ex.vendor, ex.vat_id])))
    x.sender_ok = bool(keys) and any(k in seller for k in keys)
    if buyer is not None and buyer.name:
        x.recipient = Recipient(
            clean_text(buyer.name), clean_text(buyer.street), clean_text(buyer.zip, 20), clean_text(buyer.city),
            clean_text(buyer.country, 20), clean_text(buyer.vat_id, 30), clean_text(buyer.email),
        )  # fmt: skip
    printed = [
        DocLine(clean_text(i.description, 2000) or "", i.quantity, None, i.unit_price, i.amount) for i in ex.items
    ]
    _finalise_lines(x, printed[:MAX_DOC_LINES] if len(printed) <= MAX_DOC_LINES else [], None)
    if "small_business" in ex.text_signals:
        x.small_business = True


# --------------------------------------------------------------------------------------------------------------------
# public


def parse(read: ReadResult, own: OwnIdentity, einvoice: EInvoiceDoc | None = None) -> DocExtraction:
    """Words (or the structured e-invoice) to a ``DocExtraction``. Never raises: odd input gives an empty result.

    ``einvoice`` is the result of ``einvoice.read_einvoice`` for an embedded e-invoice (it carries the buyer and the
    type code); without it an ``ReadResult.extraction`` is used as far as it goes (no buyer)."""
    x = DocExtraction(engine=read.engine, error=read.error, pages_read=read.pages_read, pages_total=read.pages_total)
    try:
        keys = _own_keys(own)
        lines = _lines(read.words)
        first_page = min((w.page for w in read.words), default=0)
        if read.extraction is not None or einvoice is not None:
            ex = einvoice.extraction if einvoice is not None else read.extraction
            assert ex is not None
            if einvoice is None and (
                ex.buyer is not None or ex.type_code
            ):  # the worker's JSON carries the buyer and the type code on the extraction
                einvoice = EInvoiceDoc(ex, ex.type_code, ex.buyer or Party())
            _from_einvoice(read, ex, einvoice, keys, x)
            if lines:  # the PDF text only adds what the XML cannot say (and the sender check)
                x.sender_ok = x.sender_ok or _sender_ok(lines, first_page, keys)
            return x
        words = limit_words(read.words)[0]
        _kind(lines, first_page, x)
        _number(lines, x)
        _dates_and_terms(lines, first_page, x)
        x.sender_ok = _sender_ok(lines, first_page, keys)
        _recipient(lines, words, first_page, keys, own.vat_id, x)
        _totals(lines, words, x)
        _finalise_lines(x, _table(lines, x), _subject(lines))
    except (ValueError, ArithmeticError, IndexError, KeyError, AttributeError, TypeError, re.error):
        x.error = x.error or "invalid_data"
    return x


def _allowed_rates(x: DocExtraction) -> tuple[Decimal, ...]:
    day = x.service_to or x.service_from or x.date
    try:
        d = date.fromisoformat(day) if day else None
    except ValueError:
        d = None
    if d is not None and HISTORIC_FROM <= d <= HISTORIC_TO:
        return ALLOWED_RATES + HISTORIC_RATES
    return ALLOWED_RATES


def evaluate(x: DocExtraction, today: date | None = None) -> tuple[list[str], list[str]]:
    """Flags (every reason found, in the order of ``FLAGS``) and info notes. A document is clean when both are empty
    of flags. Customer matching and duplicates are checked by the service."""
    found: set[str] = set()
    notes: list[str] = []
    today = today or date.today()
    if x.engine == "none" or x.error:
        return ["unreadable"], []
    if x.pages_total and x.pages_read < x.pages_total:
        found.add("pages_skipped")
    if x.kind is None:
        found.add("kind_unclear")
    elif x.kind in UNSUPPORTED:
        found.add("kind_unsupported")
    if x.number is None:
        found.add("ambiguous_number" if len(x.number_candidates) > 1 else "no_number")
    if x.date is None:
        found.add("ambiguous_date" if len(x.competing_dates) > 1 else "no_date")
    else:
        try:
            d = date.fromisoformat(x.date)
            if d > today or d.year < 2000:
                found.add("date_implausible")
        except ValueError:
            found.add("date_implausible")
    if x.recipient is None or not x.recipient.name:
        found.add("no_customer")
    if not x.sender_ok:
        found.add("not_my_document")
    if x.currency not in (None, "EUR"):
        found.add("foreign_currency")
    rates = {g.rate for g in x.vat_groups}
    if len(rates) > 1:
        found.add("several_vat_rates")
    elif not rates:
        found.add("no_vat")
    elif next(iter(rates)) not in _allowed_rates(x):
        found.add("vat_rate_unusual")
    if x.competing_totals:
        found.add("ambiguous_total")
    elif len(rates) <= 1:
        if not _totals_consistent(x):
            found.add("totals_mismatch")
    if x.lines_summarised:
        notes.append(INFO_LINES_SUMMARISED)
    return [f for f in FLAGS if f in found], notes


def _totals_consistent(x: DocExtraction) -> bool:
    """``round_half_up(net x rate) == vat`` and ``net + vat == gross``, all three printed."""
    if len(x.vat_groups) != 1 or x.net is None or x.gross is None:
        return False
    g = x.vat_groups[0]
    vat = g.vat if g.vat is not None else x.vat_total
    if vat is None or (g.base is not None and g.base != x.net):
        return False
    if x.vat_total is not None and x.vat_total != vat:
        return False
    try:
        return vat_amount(x.net, g.rate) == vat and x.net + vat == x.gross
    except ArithmeticError:
        return False
