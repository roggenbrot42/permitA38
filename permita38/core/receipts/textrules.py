"""Text rules: words with positions -> ``Extraction`` (docs/specs/receipt-scan.md, "Text rules").

Pure functions, no I/O. German and English labels live in the tables at the top. The words are grouped into lines by
position; every value keeps its source words in ``Extraction.sources`` (keys: ``vendor``, ``date``, ``invoice_no``,
``vat_id``, ``subtotal``, ``total``, ``prepaid``, ``amount_due``, ``tendered``, ``change``, ``card``, ``due_date``,
``paid_date``, ``item:<n>``, ``vat_group:<n>``), so the overlay can mark where each value was read.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..money import q2
from .extraction import MAX_LINE_WORDS, Adjustment, Extraction, Item, VatGroup, Word, limit_words
from .reconcile import total_from_payment

# --------------------------------------------------------------------------------------------------------------------
# label tables (matched on the lower-cased line)

TOTAL_STRONG = (
    r"rechnungsbetrag|gesamtbetrag|endbetrag|gesamtsumme|grand total|total amount|zu zahlen|zahlbetrag|amount due"
    r"|total due|balance due|gesamt brutto|summe brutto|bruttobetrag|prix de course|prix total|montant total"
)
TOTAL_WEAK = r"gesamt|summe|total|brutto|betrag"
SUBTOTAL = r"zwischensumme|zwischen-summe|subtotal|sub-total|summe verk[äa]ufe|summe artikel"
NET_TOTAL = r"rechnungsbetrag\s*\(?netto|nettoumsatz|nettopreis|netto-?\s?summe|summe netto|gesamt\s?netto|netto gesamt|total net"
VAT_TOTAL = r"steuersumme|summe mwst|summe steuer|mwst\.? gesamt|gesamt mwst|total vat|total tax"
DUE_ONLY = r"restbetrag|offener betrag|noch zu zahlen|still to pay"
AMOUNT_DUE = r"restbetrag|offener betrag|noch zu zahlen|balance due|still to pay|zu zahlen|amount due|zahlbetrag"
NEVER_TOTAL = r"gegeben|rückgeld|rueckgeld|wechselgeld|trinkgel|übertrag|uebertrag|\btip\b|carried forward|\bchange\b"
NET_WORDS = r"netto|\bnet\b|exkl|excl\b|excluding|ohne mwst"
TAX_WORDS = r"m[wu]i?st|mehrwertsteuer|\bust\b|umsatzsteuer|\bvat\b|\btva\b|steuer|\btax\b"
GROSS_ROW = r"\bums\.|umsatz(?!steuer)"
VAT_ROW = r"enth"
TAX_INCLUDED = r"inkl|incl|brutto|including|gross"
TENDERED = r"gegeben|\btendered\b|cash given|^bar\b|bar gezahlt"
CHANGE = r"rückgeld|rueckgeld|wechselgeld|\bchange\b"
TIP = r"trinkgel|\btip\b|gratuity"
DISCOUNT = r"rabatt|discount|nachlass|gutschein"
ROUNDING = r"rundung|rounding"
CARD = r"girocard|ec-karte|ec karte|\bec\b|kartenzahlung|\bvisa\b|mastercard|\bkarte\b|\bcard\b|maestro|v pay"
CARRY = r"übertrag|uebertrag|carried forward|brought forward"
PREPAID = r"anzahlung|vorauszahlung|bereits bezahlt|abzüglich bezahlt|prepayment|prepaid|\bdeposit\b|down payment"

DATE_LABEL = r"datum|\bdate\b|ausgestellt|issued"
DATE_DUE = r"fällig|faellig|zahlbar bis|zahlungsziel|due date|date due|due on|payable until|payable by|pay by"
DATE_PAID = r"bezahlt am|zahlungsdatum|paid on|date paid|date of payment|paid at|zahlung am"
DATE_STRONG = r"rechnungsdatum|date of issue|invoice date|issue date|ausstellungsdatum"
DATE_OTHER = r"bestell|order date|leistungs|liefer|service date|delivery|anreise|abreise|arrival|departure|check-in|check-out|zeitraum|period"

INVOICE_LABELS = (
    r"rechnung(?:s)?[\s\-]*(?:nr|nummer|no)\b\.?",
    r"quittance[\s\-]*(?:no|nr|n°)\b\.?",
    r"facture[\s\-]*(?:no|nr|n°)\b\.?",
    r"invoice\s*(?:no|number|nr|num|#)\b\.?",
)
RECEIPT_LABELS = (
    r"kassenbon[\s\-]*(?:nr|nummer)\b\.?",
    r"beleg[\s\-]*(?:nr|nummer)\b\.?",
    r"bon[\s\-]*(?:nr|nummer)\b\.?",
    r"receipt\s*(?:no|number|nr|#)\b\.?",
)
TSE_LABELS = (r"transaktions?[\s\-]*(?:nr|nummer)?\b\.?", r"transaction\s*(?:no|number|nr|id)?\b\.?")

SIGNAL_CASH = (
    r"\bbar\b|gegeben|rückgeld|rueckgeld|wechselgeld|\bec\b|girocard|ec-karte|kartenzahlung|\bvisa\b|mastercard|kontaktlos"
    r"|contactless|\btse\b|kassenbon|bar gezahlt|\bcash\b"
)
SIGNAL_OPEN = (
    r"zahlbar bis|zahlbar innerhalb|zahlungsziel|bitte überweisen|bitte ueberweisen|überweisen sie|ueberweisen sie|lastschrift"
    r"|please pay|payment due|payable within|fälligkeit"
)
SIGNAL_PAID = DATE_PAID
CREDIT_NOTE = r"gutschrift|stornorechnung|storno-rechnung|credit note"
SIGNAL_TEXT = {
    "reverse_charge": r"steuerschuldnerschaft|reverse charge",
    "small_business": r"§\s?19\s?ustg|kleinunternehmer|small business",
    "credit_note": r"gutschrift|credit note",
    "parking": r"\bparken\b|parkschein|\bparking\b|parkplatz",
    "software": r"\bdomain\b|\bhosting\b|\bsubscription\b|\babonnement\b|\bmax plan\b",
    "transport": r"prix de course|votre course|chauffeur|\btaxi\b",
    "meal": r"restaurant|gaststätte|gaststaette|gasthaus|bewirtung|café|\bcafe\b|bistro|trattoria|pizzeria|imbiss|speisen",
}

NOT_ITEM = (
    r"netto|brutto|m[wu]i?st|\bust\b|steuer|\bvat\b|summe|total|gesamt|betrag|zwischen|gegeben|rückgeld|rueckgeld|wechselgeld"
    r"|trinkgel|übertrag|uebertrag|zahlung|\bbar\b|\bec\b|karte|\bcard\b|visa|mastercard|anzahlung|bezahlt|saldo|fällig"
    r"|iban|\bbic\b|tel\b|telefon|fax|datum|\bdate\b|rechnung|invoice|beleg|\bbon\b|kasse|\btse\b|transaktion|nummer"
    r"|restbetrag|zu zahlen|amount|subtotal|tendered|\bprix\b|\btva\b|montant|tarif"
)

# The summary after the items and the technical blocks (card slip, TSE, handwritten entertainment form) end the bill.
TECHNICAL = r"bewirtungsaufwand|seq\.?-?\s?nr|zertifikat|signaturz[äa]hler|trace-?\s?number|terminal[\s\-]?id|\bs/n\b"
LEGAL_LONG = re.compile(
    r"\b(?:gmbh|limited|plc|ltd|s\.\s?a\.\s?r\.\s?l|sarl|ohg|gbr|llc|inc)\b\.?|(?<!\w)e\.\s?k\.", re.IGNORECASE
)
LEGAL_SHORT = re.compile(r"\b(?:UG|AG|KG|SA|BV)\b")
MISREAD_LEGAL = re.compile(r"\b(?:ginbth|grnbh|gmbtl|gmbll|gmhh|gmbh\.|ginbh|gnbh)\b", re.IGNORECASE)
_STREET = re.compile(
    r"\b\d{5}\b|\bL-\d{4}\b|str\.|straße|strasse|\bweg\b|\bplatz\b|\ballee\b|\bgasse\b|\brue\b|\bstreet\b",
    re.IGNORECASE,
)

MONTHS = {
    "januar": 1, "jan": 1, "january": 1, "februar": 2, "feb": 2, "february": 2, "märz": 3, "maerz": 3, "mär": 3,
    "mar": 3, "march": 3, "april": 4, "apr": 4, "mai": 5, "may": 5, "juni": 6, "jun": 6, "june": 6, "juli": 7,
    "jul": 7, "july": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10, "okt": 10,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12, "december": 12, "dec": 12,
}  # fmt: skip

HEADER_LINES = 8  # the vendor is looked for in the first lines of the first page
SHORT_MAX = 200  # bound of ``v.Short``

# --------------------------------------------------------------------------------------------------------------------

_AMOUNT = re.compile(
    r"(?<![\d.,])(-\s?)?(\d{1,3}(?:[.,]\d{3})+[.,]\d{2}|\d+(?:[.,]|, )\d{2}|\d{1,3}(?:\.\d{3})+,-|\d+,-)(-(?=\s|$))?(?!\d|[.,]\d)"
)
_PERCENT = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
_DATE_NUM = re.compile(r"(?<![\d./])(\d{1,2})\. ?(\d{1,2})\. ?(\d{4}|\d{2})(?!\d)")
_DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_DATE_EN = re.compile(
    r"(?<![\w])("
    + "|".join(sorted(MONTHS, key=len, reverse=True))
    + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)",
    re.IGNORECASE,
)
_DATE_TEXT = re.compile(
    r"(?<!\d)(\d{1,2})[.\-]?\s*(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")[.\-]?,?\s*(\d{4})(?!\d)",
    re.IGNORECASE,
)
_VAT_ID = re.compile(r"\b(DE\s?\d{3}\s?\d{3}\s?\d{3})(?!\d)")
_TAX_NO = re.compile(r"(?:steuer[\s\-]*(?:nummer|nr)|st\.?[\s\-]*nr)\.?[\s:]*(\d[\d /]{5,18}\d)", re.IGNORECASE)
_VAT_ID_LABEL = re.compile(
    r"(?:tva|ust[\s\-]*id(?:[\s\-]*nr)?|vat[\s\-]*(?:id|no|number|reg)|umsatzsteuer[\s\-]*id(?:ent)?(?:[\s\-]*nr)?)\.?[\s:]*([A-Z]{2}\s?\d{3}\s?\d{3}\s?\d{3}(?!\d)|[A-Z]{2}\s?[A-Z0-9]{8,12})",
    re.IGNORECASE,
)
_CC = r"USD|CHF|GBP|SEK|DKK|PLN|CZK|JPY"
_CURRENCY_AMOUNT = re.compile(rf"\b({_CC})\s?\d|\b\d[\d.,]{{0,30}}\s?({_CC})\b|([$£])\s?\d|\d\s?([$£])")
_EURO_AMOUNT = re.compile(r"€\s?\d|\d\s?€|\d\s?EUR\b|\bEUR\s?\d")
_QTY = re.compile(r"(?<![\d.,])(\d{1,12}(?:[.,]\d{1,6})?)\s*[x×*]\s*$", re.IGNORECASE)
_LEAD_QTY = re.compile(r"^\s*(\d{1,12}(?:[.,]\d{1,6})?)\s*[x×*]\s+", re.IGNORECASE)
_CODE_PAREN = re.compile(r"\(([A-Da-d]|[1-4])\)\s*:?\s*$")
_BASE_WORD = re.compile(r"\bauf\b", re.IGNORECASE)
_TABLE_HEAD = re.compile(r"bezeichnung|artikel-?\s?nr|beschreibung|description|\bmenge\b|\bpos\b|anzahl", re.IGNORECASE)
_NUMBERS_ONLY = re.compile(r"^[\d\s]+$")
_CODE_TAIL = re.compile(r"^\s*([A-Da-d]|[1-4])\s*$")
_ADDRESS = re.compile(
    r"\b\d{5}\b|\+\d|\(\d|@|www\.|http|\btel\b|\bfax\b|telefon|\bstr\.|\brue\b|\bL-\d{4}\b|\btva\b|quittance|straße|strasse|\bweg\b|\bplatz\b|\ballee\b|\bgasse\b"
    r"|\bstreet\b|\bust\b|steuer|\bhrb\b|amtsgericht|geschäftsführer|\biban\b|\bbic\b|\bvat\b|rechnung|kassenbon|\bbeleg\b"
    r"|quittung|invoice|receipt|datum|\bdate\b|\bbon\b|willkommen|vielen dank",
    re.IGNORECASE,
)


@dataclass
class _Line:
    page: int
    words: list[Word]
    text: str = ""
    low: str = ""
    spans: list[tuple[int, int]] = field(default_factory=list)

    def build(self) -> None:
        parts, pos = [], 0
        for w in self.words:
            t = w.text.replace("−", "-")
            self.spans.append((pos, pos + len(t)))
            parts.append(t)
            pos += len(t) + 1
        self.text = " ".join(parts)
        self.low = self.text.lower()

    def words_in(self, start: int, end: int) -> list[Word]:
        return [w for w, (a, b) in zip(self.words, self.spans) if a < end and b > start]


def clean_text(value: str | None, limit: int = SHORT_MAX) -> str | None:
    """Outside text: control and bidi characters removed, white space collapsed, cut to the ``v.Short`` bound."""
    if value is None:
        return None
    out = []
    for ch in value:
        cat = unicodedata.category(ch)
        if ch in "\t\n\r\x0b\x0c" or cat in ("Zs", "Zl", "Zp"):
            out.append(" ")
        elif cat in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        else:
            out.append(ch)
    text = " ".join("".join(out).split())
    return text[:limit].strip() or None


def _skew(words: list[Word]) -> float:
    """The slope (rise of a line per unit of x) that puts the most word pairs on one line, or 0 for an upright page."""
    if len(words) < 12:
        return 0.0
    heights = sorted(w.h for w in words)
    window = max(heights[len(heights) // 2], 0.004) * 0.5
    pts = [(w.x + w.w / 2, w.y + w.h / 2) for w in words[:1500]]

    def score(slope: float) -> int:
        # pairs of words far apart in x that share a line: only these tell the slope from the upright page
        ys = sorted((y - slope * x, x) for x, y in pts)
        total = j = 0
        for i, (v, xv) in enumerate(ys):
            while ys[j][0] < v - window:
                j += 1
            total += sum(1 for k in range(max(j, i - 60), i) if abs(ys[k][1] - xv) > 0.25)  # 60: bounded cost
        return total

    scores = {step: score(step * 0.0005) for step in range(-20, 21)}  # slopes of -0.01 .. 0.01
    top = max(scores.values())
    # the smallest turn that is nearly as good as the best one: a steep slope can also line up neighbouring lines
    best = min((st for st, sc in scores.items() if sc >= top * 0.97), key=abs)
    return best * 0.0005 if scores[best] > scores[0] * 1.1 + 2 else 0.0


def group_lines(words: list[Word]) -> list[_Line]:
    """Words to lines: per page, from the top; a word joins the line whose centre is within half its height.

    A slightly turned page moves the right-hand columns onto the neighbouring line, so the centres are first
    corrected by the slope of the page (``_skew``)."""
    lines: list[_Line] = []
    pages: dict[int, list[Word]] = {}
    for w in words:
        pages.setdefault(w.page, []).append(w)
    for page in sorted(pages):
        slope = _skew(pages[page])

        def centre_of(w: Word, slope: float = slope) -> float:
            return w.y + w.h / 2 - slope * (w.x + w.w / 2)

        ordered = sorted(pages[page], key=lambda w: (centre_of(w), w.x))
        current: list[Word] = []
        centre = height = sum_c = sum_h = 0.0
        for w in ordered:
            c = centre_of(w)
            if current and len(current) < MAX_LINE_WORDS and abs(c - centre) <= 0.5 * max(height, w.h, 0.004):
                current.append(w)
                sum_c += c
                sum_h += w.h
                centre = sum_c / len(current)
                height = sum_h / len(current)
            else:
                if current:
                    lines.append(_Line(page, sorted(current, key=lambda v: v.x)))
                current, centre, height = [w], c, w.h
                sum_c, sum_h = c, w.h
        if current:
            lines.append(_Line(page, sorted(current, key=lambda v: v.x)))
    for ln in lines:
        ln.build()
    return lines


def drop_overlap(words: list[Word]) -> list[Word]:
    """Photos overlap: lines at the start of a page that repeat the end of the previous page (compared without
    spaces) are dropped, so that an item in the overlap is not counted twice."""
    lines = group_lines(words)
    by_page: dict[int, list[_Line]] = {}
    for ln in lines:
        by_page.setdefault(ln.page, []).append(ln)
    dropped: set[int] = set()
    pages = sorted(by_page)
    for before, after in zip(pages, pages[1:]):
        tail = [re.sub(r"\s", "", ln.low) for ln in by_page[before]]
        head = [re.sub(r"\s", "", ln.low) for ln in by_page[after]]
        for size in range(min(len(tail), len(head), 12), 0, -1):
            if tail[-size:] == head[:size] and any(len(h) > 2 for h in head[:size]):
                for ln in by_page[after][:size]:
                    dropped.update(id(w) for w in ln.words)
                break
    return [w for w in words if id(w) not in dropped]


def parse_amount(text: str) -> Decimal | None:
    """One amount token (``1.234,56``, ``1,234.56``, ``12,50``, ``12.50``, ``-1,00``, ``1,00-``) to a ``Decimal``."""
    m = _AMOUNT.fullmatch(
        re.sub(r"(?i)(?<=\d)\s?(?:euro|eur)$", "", text.strip())
        .replace("−", "-")
        .strip("€ ")
        .removeprefix("EUR")
        .strip()
    )
    return _amount_of(m) if m else None


def _amount_of(m: re.Match) -> Decimal:
    if m.group(2).endswith(",-"):  # "2.500,-" is 2500.00
        value = Decimal(re.sub(r"\D", "", m.group(2)) + ".00")
    else:
        value = Decimal(re.sub(r"\D", "", m.group(2)[:-2]) + "." + m.group(2)[-2:])
    return -value if (m.group(1) or m.group(3)) else value


def _amounts(ln: _Line) -> list[tuple[Decimal, int, int]]:
    masked = _DATE_NUM.sub(lambda m: " " * len(m.group(0)), ln.text)
    masked = _PERCENT.sub(lambda m: " " * len(m.group(0)), masked)
    return [(_amount_of(m), m.start(), m.end()) for m in _AMOUNT.finditer(masked)]


def _rate(text: str) -> Decimal:
    value = Decimal(text.replace(",", "."))
    return value.quantize(Decimal(1)) if value == value.to_integral_value() else value.normalize()


def _dates(ln: _Line) -> list[tuple[str, int, int, str]]:
    """(ISO date, start, end, text between the previous date and this one) for every date of the line."""
    found: list[tuple[int, int, date]] = []
    for m in _DATE_NUM.finditer(ln.text):
        year = int(m.group(3))
        year = year + 2000 if len(m.group(3)) == 2 else year
        found.append((m.start(), m.end(), (year, int(m.group(2)), int(m.group(1)))))  # type: ignore[arg-type]
    for m in _DATE_ISO.finditer(ln.text):
        found.append((m.start(), m.end(), (int(m.group(1)), int(m.group(2)), int(m.group(3)))))  # type: ignore[arg-type]
    for m in _DATE_TEXT.finditer(ln.text):
        found.append((m.start(), m.end(), (int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))))  # type: ignore[arg-type]
    for m in _DATE_EN.finditer(ln.text):
        found.append((m.start(), m.end(), (int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))))  # type: ignore[arg-type]
    out = []
    prev = 0
    for start, end, ymd in sorted(found, key=lambda f: f[0]):
        try:
            iso = date(*ymd).isoformat()  # type: ignore[misc]
        except ValueError:
            continue
        out.append((iso, start, end, ln.low[prev:end]))
        prev = end
    return out


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


def _extend(x: Extraction, key: str, words: list[Word]) -> None:
    have = x.sources.setdefault(key, [])
    seen = set(have)
    for w in words:
        if w not in seen:
            seen.add(w)
            have.append(w)


def parse(words: list[Word], exclude: tuple[str, ...] = ()) -> Extraction:
    x = Extraction()
    lines = group_lines(limit_words(words)[0])  # defensive: the worker already bounds the words
    low_all = "\n".join(ln.low for ln in lines)
    prepaid_lines = {i for i, ln in enumerate(lines) if _prepaid_line(ln)}
    has_prepaid = bool(prepaid_lines)

    for sig, pattern in SIGNAL_TEXT.items():
        if sig == "credit_note":
            # a heading, not a word in the cancellation terms of a footer
            if any(_search(CREDIT_NOTE, ln.low) and len(ln.words) <= 6 and not _amounts(ln) for ln in lines):
                x.text_signals.append(sig)
        elif _search(pattern, low_all):
            x.text_signals.append(sig)
    for sig, pattern in (("cash", SIGNAL_CASH), ("open", SIGNAL_OPEN), ("paid", SIGNAL_PAID)):
        if _search(pattern, low_all):
            x.payment_signals.append(sig)
    if _search(r"\b(ec|girocard|ec-karte|kartenzahlung|visa|mastercard|maestro|karte|card)\b", low_all):
        x.payment_signals.append("card")
    all_text = "\n".join(ln.text for ln in lines)
    foreign = list(_CURRENCY_AMOUNT.finditer(all_text))
    if len(_EURO_AMOUNT.findall(all_text)) >= len(foreign) and _EURO_AMOUNT.search(all_text):
        x.currency = "EUR"  # euro amounts on the bill: a stray "$" in OCR noise raises nothing
    elif foreign:
        code = next(c for c in foreign[0].groups() if c)
        x.currency = {"$": "USD", "£": "GBP"}.get(code, code)
    elif re.search(r"€|\beur\b", low_all):
        x.currency = "EUR"

    totals: list[tuple[int, Decimal, list[Word]]] = []
    labelled: list[tuple[int, str, list[Word]]] = []  # (tier, ISO date, words); tier 0 is the best label
    unlabelled: list[tuple[str, list[Word]]] = []
    handled: set[int] = set()
    numbers: list[tuple[int, str, list[Word]]] = []
    item_lines: list[int] = []
    tech_from = next((i for i, ln in enumerate(lines) if _search(TECHNICAL, ln.low)), len(lines))

    for idx, ln in enumerate(lines):
        if idx >= tech_from:
            break
        low = ln.low
        amounts = _amounts(ln)
        dates = _dates(ln)

        # dates first: due, paid and bill dates
        for n_date, (iso, start, end, before) in enumerate(dates):
            w = ln.words_in(start, end)
            if n_date == 0 and before.lstrip().startswith("/") and idx:  # label split over two lines
                before = lines[idx - 1].low + " " + before
            if _search(DATE_DUE, before) and not _search(DATE_STRONG, before):
                if x.due_date is None:
                    x.due_date = iso
                    _extend(x, "due_date", w)
            elif _search(DATE_PAID, before) and not _search(DATE_STRONG, before):
                if x.paid_date is None:
                    x.paid_date = iso
                    _extend(x, "paid_date", w)
            elif _search(DATE_STRONG, before):
                labelled.append((0, iso, w))
            elif _search(DATE_OTHER, before):
                pass
            elif _search(DATE_LABEL, before):
                labelled.append((1, iso, w))
            elif _search("|".join(INVOICE_LABELS + RECEIPT_LABELS), ln.low):
                labelled.append((2, iso, w))  # a date on the invoice number line
            else:
                unlabelled.append((iso, w))
        if dates:
            handled.add(idx)

        # invoice number, VAT id
        _read_ids(x, ln, numbers)

        if _search(CARRY, low):
            handled.add(idx)
            continue

        # VAT table
        if (
            "%" in ln.text
            and (
                _search(TAX_WORDS, low)
                or _search(GROSS_ROW, low)
                or re.match(r"\s*[A-Da-d1-4]\b", ln.text)
                or (len(amounts) >= 2 and re.match(r"\s*\d{1,2}(?:[.,]\d+)?\s?%", ln.text))
            )
            and not _search(DISCOUNT, low)
        ):
            if _vat_line(x, ln, amounts):
                handled.add(idx)
                continue

        if idx in handled:
            continue
        if not amounts:
            continue
        value, a_start, a_end = amounts[-1]
        w = ln.words_in(a_start, a_end)

        if _search(NET_TOTAL, low):
            x.net_total = value
            _extend(x, "net_total", w)
        elif _search(VAT_TOTAL, low):
            x.vat_total = value
            _extend(x, "vat_total", w)
        elif has_prepaid and _search(AMOUNT_DUE, low):
            x.amount_due = value
            _extend(x, "amount_due", w)
        elif idx in prepaid_lines:
            x.prepaid = abs(value)
            _extend(x, "prepaid", w)
        elif _search(SUBTOTAL, low):
            x.subtotal = value
            _extend(x, "subtotal", w)
        elif _search(CHANGE, low):
            x.change = value
            _extend(x, "change", w)
        elif _search(TENDERED, low):
            x.tendered = value
            _extend(x, "tendered", w)
        elif _search(TIP, low):
            if not any(a.kind == "tip" for a in x.adjustments):  # printed twice: once among the items, once below
                x.adjustments.append(Adjustment("tip", value))
        elif _search(ROUNDING, low):
            x.adjustments.append(Adjustment("rounding", value))
        elif _search(CARD, low) and not _search(r"gesamt|summe|total", low):
            x.card_amount = value
            _extend(x, "card", w)
        elif _search(DUE_ONLY, low):
            x.amount_due = value
            _extend(x, "amount_due", w)
        elif _is_total_line(low):
            tier = 0 if _search(TOTAL_STRONG, low) else 1
            totals.append((tier, value, w))
        elif _search(NET_WORDS, low) or _search(TAX_WORDS, low):
            continue
        elif _search(DISCOUNT, low):
            as_item = _item_of(ln, amounts)
            if as_item is not None and as_item.vat_code is not None:
                item_lines.append(idx)
            else:
                x.adjustments.append(Adjustment("discount", value))
        elif not _search(NOT_ITEM, low):
            item_lines.append(idx)

    if numbers:
        tier, x.invoice_no, w = min(numbers, key=lambda n: n[0])
        x.number_weak = (
            tier >= 1
        )  # a receipt or transaction label: restarts per till, proves less than an invoice number
        _extend(x, "invoice_no", w)
    _choose_total(x, totals)
    total_from_payment(x)
    _choose_date(x, labelled, unlabelled)
    item_set = set(item_lines)
    for idx in item_lines:
        ln = lines[idx]
        item = _item_of(ln, _amounts(ln), _lines_above(lines, idx, item_set))
        if item is not None:
            x.sources[f"item:{len(x.items)}"] = list(ln.words)
            x.items.append(item)
    _vendor(x, lines, exclude)
    if x.prepaid is not None:
        x.payment_signals.append("prepaid")
    if x.doc_type is None:
        if "credit_note" in x.text_signals:
            x.doc_type = "credit_note"
        elif "cash" in x.payment_signals and "open" not in x.payment_signals and "rechnung" not in low_all:
            x.doc_type = "receipt"
        else:
            x.doc_type = "invoice"
    return x


def _prepaid_line(ln: _Line) -> bool:
    """A labelled prepayment: the word is among the first words, the line has no rate, the amount is above 0."""
    amounts = _amounts(ln)
    if not amounts or "%" in ln.text or not _search(PREPAID, ln.low):
        return False
    before = ln.text[: amounts[0][1]]
    return len(before.split()) <= 4 and _search(PREPAID, before.lower()) and abs(amounts[-1][0]) > 0


def _is_total_line(low: str) -> bool:
    if _search(SUBTOTAL, low) or _search(NEVER_TOTAL, low) or _search(NET_WORDS, low):
        return False
    if _search(TAX_WORDS, low) and not _search(TAX_INCLUDED, low):
        return False
    return _search(TOTAL_STRONG, low) or _search(TOTAL_WEAK, low)


def _choose_total(x: Extraction, totals: list[tuple[int, Decimal, list[Word]]]) -> None:
    if not totals:
        return
    best = min(t[0] for t in totals)
    top = [t for t in totals if t[0] == best]
    distinct: list[Decimal] = []
    for _, value, _w in top:
        if value not in distinct:
            distinct.append(value)
    if len(distinct) > 1:
        x.competing_totals = distinct
        return
    x.total = distinct[0]
    for _, _value, w in top:
        _extend(x, "total", w)


def _choose_date(
    x: Extraction, labelled: list[tuple[int, str, list[Word]]], unlabelled: list[tuple[str, list[Word]]]
) -> None:
    if labelled:
        best = min(t for t, _d, _w in labelled)
        chosen = [(d, w) for t, d, w in labelled if t == best]
    else:
        chosen = unlabelled
    if not chosen and x.paid_date:  # a card receipt that only says when it was paid: that is the bill date
        chosen = [(x.paid_date, x.sources.get("paid_date", []))]
    distinct = list(dict.fromkeys(d for d, _ in chosen))
    if len(distinct) > 1:
        x.competing_dates = distinct
    elif distinct:
        x.bill_date = distinct[0]
        _extend(x, "date", [word for d, w in chosen if d == distinct[0] for word in w])


def _read_ids(x: Extraction, ln: _Line, numbers: list[tuple[int, str, list[Word]]]) -> None:
    if x.vat_id is None:
        m = _VAT_ID_LABEL.search(ln.text) or _VAT_ID.search(ln.text)
        if m:
            x.vat_id = re.sub(r"\s", "", m.group(1)).upper()
            _extend(x, "vat_id", ln.words_in(m.start(1), m.end(1)))
    if x.tax_no is None:
        m = _TAX_NO.search(ln.text)
        if m:
            x.tax_no = re.sub(r"\s+", "", m.group(1))
    for rank, tier in enumerate((INVOICE_LABELS, RECEIPT_LABELS, TSE_LABELS)):
        for label in tier:
            m = re.search(label + r"[\s:#.]*(\d{1,3}(?: \d{3})+(?!\d)|[A-Za-z0-9][A-Za-z0-9\-/_.]{0,39})", ln.low)
            if m:
                value = ln.text[m.start(1) : m.end(1)].rstrip(".-/_")
                if re.fullmatch(r"\d{1,3}(?: \d{3})+", value):
                    value = value.replace(" ", "")  # a stray space inside the number: "2 345 678"
                if re.search(r"\d", value):
                    numbers.append((rank, value, ln.words_in(m.start(1), m.end(1))))
                    return


def _vat_line(x: Extraction, ln: _Line, amounts: list[tuple[Decimal, int, int]]) -> bool:
    m = _PERCENT.search(ln.text)
    if not m:
        return False
    rate = _rate(m.group(1))
    code_m = re.match(r"\s*([A-Da-d]|[1-4])\b", ln.text)
    code = code_m.group(1).upper() if code_m and code_m.end() <= m.start() else None
    if code is None:  # "Mehrwertsteuer(A): 19 % auf ...": the code in brackets before the rate
        paren = _CODE_PAREN.search(ln.text[: m.start()])
        code = paren.group(1).upper() if paren else None
    if code is None and amounts:  # till tables print the code at the end: "Ums. inkl. 19% MwSt 9,50 EUR A"
        tail = _CODE_TAIL.match(re.sub(r"^\s*(?:€|eur\b)", "", ln.text[amounts[-1][2] :], flags=re.IGNORECASE))
        code = tail.group(1).upper() if tail else None
    values = [a[0] for a in amounts]
    group = VatGroup(rate=rate, code=code)
    if len(values) == 1:
        if _search(GROSS_ROW, ln.low) and not _search(VAT_ROW, ln.low):
            group.gross = values[0]  # "Umsatz 19% inkl. 7,40": the gross of the group
        elif _search(NET_WORDS, ln.low) and not _search(TAX_INCLUDED, ln.low):
            group.net = values[0]  # "Netto MWST. 19% 13,45": the net of the group
        else:
            group.vat = values[0]
    elif len(values) == 2:
        if values[1] < values[0] or _BASE_WORD.search(ln.text[: amounts[0][1]]):
            group.net, group.vat = values
        else:
            group.vat, group.gross = values
    elif len(values) >= 3:
        group.net, group.vat, group.gross = values[-3:]
    for existing_index, g in enumerate(x.vat_groups):
        if g.rate == rate and (code is None or g.code in (None, code)):
            for name in ("code", "net", "vat", "gross"):
                if getattr(g, name) is None and getattr(group, name) is not None:
                    setattr(g, name, getattr(group, name))
            _extend(x, f"vat_group:{existing_index}", list(ln.words))
            return True
    x.sources[f"vat_group:{len(x.vat_groups)}"] = list(ln.words)
    x.vat_groups.append(group)
    return True


def _lines_above(lines: list[_Line], idx: int, item_lines: set[int]) -> str:
    """The description lines directly above a row of numbers (article number, quantity, prices, code): up to three
    text lines, back to the table head, a date, an amount or the previous row."""
    taken: list[str] = []
    for j in range(idx - 1, max(idx - 4, -1), -1):
        above = lines[j]
        if j in item_lines or _amounts(above) or _dates(above) or _TABLE_HEAD.search(above.text):
            break
        if len(re.findall(r"[^\W\d_]", above.text)) < 2 or _search(NOT_ITEM, above.low):
            break
        taken.insert(0, above.text)
    return " ".join(taken)


def _item_of(ln: _Line, amounts: list[tuple[Decimal, int, int]], above: str = "") -> Item | None:
    if not amounts:
        return None
    value, start, end = amounts[-1]
    prefix = ln.text[: amounts[0][1]]
    columns = _NUMBERS_ONLY.match(prefix.strip() or "x") is not None
    if columns and above:
        # a table whose row holds only numbers ("700012345 1 12,34 12,34 A"); the name stands above. The first
        # number is the article number and never a quantity; the last one is, if quantity x price is the amount.
        rest = re.sub(r"^\s*(?:€|eur\b)", "", ln.text[end:], flags=re.IGNORECASE)
        tail = _CODE_TAIL.match(rest)
        if tail is None:
            return None
        tokens = prefix.split()
        quantity = unit = None
        if len(tokens) >= 2 and len(amounts) >= 2 and re.fullmatch(r"\d{1,4}", tokens[-1]):
            q = Decimal(tokens[-1])
            if q2(q * amounts[0][0]) == value:
                quantity, unit = q, amounts[0][0]
        return Item(
            description=clean_text(above) or "",
            quantity=quantity,
            unit_price=unit,
            amount=value,
            vat_code=tail.group(1).upper(),
        )
    quantity = unit = None
    qm = _QTY.search(prefix)
    lead = _LEAD_QTY.match(prefix)
    if lead and not qm:  # "1 x Pizza à 11,20 11,20 B"
        quantity = Decimal(lead.group(1).replace(",", "."))
        prefix = re.sub(r"\s+[àá@]\s*$", "", prefix[lead.end() :])
        if len(amounts) > 1:
            unit = amounts[0][0]
    elif qm:
        quantity, unit = Decimal(qm.group(1).replace(",", ".")), amounts[0][0]
        prefix = prefix[: qm.start()]
        if len(amounts) == 1:  # "2 x 80,00" and no separate amount: the printed number is the unit price
            value = q2(quantity * unit)
    desc = prefix.strip(" .:-")
    if len(re.findall(r"[^\W\d_]", desc)) < 2:
        return None
    rest = re.sub(r"^\s*(?:€|eur\b)", "", ln.text[end:], flags=re.IGNORECASE)
    tail = _CODE_TAIL.match(rest)
    if rest.strip() and not tail:
        return None
    return Item(
        description=clean_text(desc) or "",
        quantity=quantity,
        unit_price=unit,
        amount=value,
        vat_code=tail.group(1).upper() if tail else None,
    )


_STOP_CELL = re.compile(
    r"\b(?:bill to|ship to|rechnungsempf[äa]nger|rechnungsadresse|lieferadresse|kunde|sold to)\b", re.IGNORECASE
)
_LABEL_NOISE = re.compile(r"qr-?code|zahlungsreferenz|\bseite\b|bestell|kundennummer|\bpage\b", re.IGNORECASE)
_AMAZON = re.compile(r"amazon(?:\.de| services| eu\b| europe)", re.IGNORECASE)
_PREFIX_LABEL = re.compile(r"^[^\W\d_][\w\- ]{0,20}:\s+")


def _vendor(x: Extraction, lines: list[_Line], exclude: tuple[str, ...] = ()) -> None:
    """Brand of a marketplace; else a line with a legal form (header first, then anywhere); else the line directly
    above the street or postcode line; else the first line. A name stops at ``Bill to`` and the like."""
    first = [ln for ln in lines if ln.page == (lines[0].page if lines else 0)][:HEADER_LINES]
    skip = tuple(n.casefold() for n in exclude if n and len(n) > 2)

    def usable(text: str | None, ln: _Line) -> str | None:
        text = clean_text(text)
        if not text or len(re.findall(r"[^\W\d_]", text)) < 3:
            return None
        if _ADDRESS.search(text) or _LABEL_NOISE.search(text) or _amounts(ln) or _dates(ln):
            return None
        if any(n in text.casefold() or text.casefold() in n for n in skip):
            return None
        return text

    def take(words: list[Word], text: str) -> None:
        x.vendor = MISREAD_LEGAL.sub("GmbH", text)
        x.sources["vendor"] = list(words)

    def cell(ln: _Line) -> str:
        """The text of the line up to the stop word; for a legal-form line, up to the legal form."""
        text = ln.text
        stop = _STOP_CELL.search(text)
        if stop:
            text = text[: stop.start()]
        return text.strip(" |-")

    def legal_name(ln: _Line) -> tuple[str, list[Word]] | None:
        text = cell(ln)
        m = None
        for pattern in (LEGAL_LONG, LEGAL_SHORT, MISREAD_LEGAL):
            m = pattern.search(text)
            if m:
                break
        if not m:
            return None
        text = text[: m.end()]
        text = text.rsplit("|", 1)[-1]
        text = _PREFIX_LABEL.sub("", text).strip(" |-")
        found = usable(text, ln)
        return (found, ln.words) if found else None

    if _AMAZON.search("\n".join(ln.text for ln in lines)):
        take([], "Amazon")
        return
    for ln in first:
        got = legal_name(ln)
        if got:
            text, words = got
            i = first.index(ln)
            if i and len(text.split()) <= 2 and not (LEGAL_LONG.search(first[i - 1].text)):
                before = usable(cell(first[i - 1]), first[i - 1])
                if before and len(before.split()) <= 4 and not _search(r"merci|danke|willkommen", before.lower()):
                    text, words = before + " " + text, first[i - 1].words + words
            take(words, text)
            return
    for ln in lines:
        got = legal_name(ln)
        if got:
            take(got[1], got[0])
            return
    for i, ln in enumerate(first):
        if i and _STREET.search(ln.text):
            above = usable(cell(first[i - 1]), first[i - 1])
            if above:
                take(first[i - 1].words, above)
                return
    for ln in first:
        plain = usable(cell(ln), ln)
        if plain:
            take(ln.words, plain)
            return
