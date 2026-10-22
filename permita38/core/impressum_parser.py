"""Deterministic extraction of business data from a German/English legal notice (Impressum / Imprint).

Pure functions, standard library only, no network and no AI. Everything returned is plain text that went through
:func:`clean` (control / format characters removed, ``<``/``>`` dropped, length limited) and every value carries a
confidence and the evidence it was derived from. Values that fail validation are reported as warnings, never returned.
"""

import codecs
import json
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

MAX_TEXT_LINES = 4000
MAX_EVIDENCE = 240

# ---------------------------------------------------------------------------------------------------------
# sanitising
# ---------------------------------------------------------------------------------------------------------


def clean(value: object, max_len: int = 200) -> str:
    """Plain, single-line, length-limited text without control/format characters or angle brackets."""
    s = unicodedata.normalize("NFC", str(value))
    s = "".join(ch for ch in s if ch in "\n\t " or not unicodedata.category(ch).startswith("C"))
    s = s.replace("<", "").replace(">", "")
    return re.sub(r"\s+", " ", s).strip()[:max_len]


def evidence_of(text: str) -> str:
    return clean(text, MAX_EVIDENCE)


# ---------------------------------------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------------------------------------


def vat_id_check_digit(first_eight: str) -> str:
    """Check digit of a German USt-IdNr for its first eight digits (BZSt procedure, ISO 7064 style)."""
    product = 10
    for ch in first_eight:
        total = (int(ch) + product) % 10
        if total == 0:
            total = 10
        product = (2 * total) % 11
    check = 11 - product
    return str(0 if check == 10 else check)


def validate_vat_id_de(value: str) -> str | None:
    """Return the normalised ``DE123456789`` or None if format or check digit is wrong."""
    m = re.fullmatch(r"DE\s*(\d{3})[\s.]?(\d{3})[\s.]?(\d{3})", value.strip().upper())
    if not m:
        return None
    digits = "".join(m.groups())
    if digits[0] == "0" or vat_id_check_digit(digits[:8]) != digits[8]:
        return None
    return "DE" + digits


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63})+")
_FILE_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "woff", "woff2", "ttf", "pdf"}


def validate_email(value: str) -> str | None:
    v = value.strip().lstrip("<([\"'").rstrip(".,;:>)]\"'").lower()  # tolerate punctuation of running text
    if len(v) > 254 or not _EMAIL_RE.fullmatch(v):
        return None
    local, _, domain = v.partition("@")
    tld = domain.rsplit(".", 1)[-1]
    if not (2 <= len(tld) <= 24) or not tld.isalpha() or tld in _FILE_TLDS:
        return None
    if local.startswith(".") or local.endswith(".") or ".." in local or domain.startswith("-") or "-." in domain:
        return None
    return v


def normalize_phone(raw: str) -> str | None:
    """+49 numbers become ``+49…`` (``(0)`` dropped), others digits only. None unless 6–15 digits."""
    s = re.sub(r"\(\s*0\s*\)", "", raw.strip())
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not plus and digits.startswith("00"):
        plus, digits = True, digits[2:]
    if not 6 <= len(digits) <= 15:
        return None
    if len(set(digits)) == 1:  # 0000000
        return None
    return ("+" if plus else "") + digits


def validate_zip(value: str, country: str | None = None) -> str | None:
    if country in ("Austria", "Switzerland"):
        return value if re.fullmatch(r"\d{4}", value) else None
    return value if re.fullmatch(r"\d{5}", value) and value != "00000" else None


# ---------------------------------------------------------------------------------------------------------
# HTML -> text, links, structured data
# ---------------------------------------------------------------------------------------------------------

_SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "object", "canvas", "select", "option"}
_BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "address", "section",
    "article", "footer", "header", "main", "aside", "nav", "dt", "dd", "dl", "blockquote", "pre", "hr", "form",
    "thead", "tbody", "tfoot", "figure", "figcaption", "details", "summary", "title", "body", "td", "th",
}  # fmt: skip
_VOID = {"br", "img", "hr", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr", "param"}


@dataclass
class Page:
    title: str = ""
    lines: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)  # (href, text)
    mailtos: list[str] = field(default_factory=list)
    tels: list[str] = field(default_factory=list)
    jsonld: list[dict] = field(default_factory=list)
    microdata: list[dict] = field(default_factory=list)
    script_only: bool = False  # a JavaScript app with (almost) no server-rendered text


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = Page()
        self._cur: list[str] = []
        self._skip = 0
        self._ld: list[str] | None = None
        self._in_title = False
        self._title: list[str] = []
        self._link: list | None = None  # [href, [text parts]]
        self._stack: list[list] = []  # [tag, scope|None, capture|None]
        self._scopes: list[dict] = []
        self._scripts = 0

    # -- helpers
    def _flush(self) -> None:
        text = clean("".join(self._cur), 400)
        self._cur = []
        if text and len(self.page.lines) < MAX_TEXT_LINES:
            self.page.lines.append(text)

    # -- parser callbacks
    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "script":
            self._scripts += 1
            if a.get("type", "").lower().startswith("application/ld+json"):
                self._ld = []
        if tag in _SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK:
            self._flush()
        if tag == "a":
            href = a.get("href", "").strip()
            self._link = [href, []]
            low = href.lower()
            if low.startswith("mailto:"):
                self.page.mailtos.append(unquote(href[7:].split("?")[0]))
            elif low.startswith("tel:"):
                self.page.tels.append(unquote(href[4:]))
        self._microdata_start(tag, a)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            if tag == "script" and self._ld is not None:
                self._parse_ld("".join(self._ld))
                self._ld = None
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = False
            self.page.title = clean(" ".join(self._title), 200)
        if tag in _BLOCK:
            self._flush()
        if tag == "a" and self._link is not None:
            self.page.links.append((self._link[0], clean("".join(self._link[1]), 120)))
            self._link = None
        self._microdata_end(tag)

    def handle_data(self, data: str) -> None:
        if self._ld is not None:
            self._ld.append(data)
            return
        if self._skip:
            return
        if self._in_title:
            self._title.append(data)
        self._cur.append(data)
        if self._link is not None:
            self._link[1].append(data)
        for entry in self._stack:
            if entry[2] is not None:
                entry[2][1].append(data)

    # -- JSON-LD
    def _parse_ld(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except ValueError:
            return
        todo = [data]
        seen = 0
        while todo and seen < 500:
            node = todo.pop()
            seen += 1
            if isinstance(node, list):
                todo.extend(node)
            elif isinstance(node, dict):
                if "@type" in node:
                    self.page.jsonld.append(node)
                for v in node.values():
                    if isinstance(v, (list, dict)):
                        todo.append(v)

    # -- microdata (itemscope / itemtype / itemprop)
    def _microdata_start(self, tag: str, a: dict) -> None:
        if tag in _VOID and "itemprop" not in a and "itemscope" not in a:
            return
        scope = None
        capture: tuple[str, list[str]] | None = None
        if "itemscope" in a:
            itemtype = a.get("itemtype", "").rstrip("/").rsplit("/", 1)[-1]
            scope = {"type": itemtype, "props": {}, "prop": a.get("itemprop")}
            self._scopes.append(scope)
        prop = a.get("itemprop")
        if prop and scope is None and self._scopes:
            value = a.get("content") or (a.get("href") if tag in ("a", "link") else None) or a.get("datetime")
            if value is not None or tag in _VOID:
                if value is not None:
                    self._scopes[-1]["props"].setdefault(prop, []).append(clean(value, 300))
            else:
                capture = (prop, [])
        if tag not in _VOID:
            self._stack.append([tag, scope, capture])

    def _microdata_end(self, tag: str) -> None:
        idx = next((i for i in range(len(self._stack) - 1, -1, -1) if self._stack[i][0] == tag), None)
        if idx is None:
            return
        closed = self._stack[idx:]
        del self._stack[idx:]
        for _tag, scope, capture in reversed(closed):
            if capture is not None and self._scopes:
                value = clean("".join(capture[1]), 300)
                if value:
                    self._scopes[-1]["props"].setdefault(capture[0], []).append(value)
            if scope is not None:
                self._scopes.remove(scope)
                if scope["prop"] and self._scopes:
                    self._scopes[-1]["props"].setdefault(scope["prop"], []).append(scope)
                else:
                    self.page.microdata.append(scope)

    def finish(self) -> Page:
        self._flush()
        exploded: list[str] = []
        for line in self.page.lines:
            parts = [p.strip() for p in re.split(r"\s+[|•·●¦]\s+|\s{3,}", line) if p.strip()]
            exploded.extend(parts or [line])
        self.page.lines = exploded[:MAX_TEXT_LINES]
        text_chars = sum(len(x) for x in self.page.lines)
        self.page.script_only = self._scripts > 0 and text_chars < 200
        return self.page


def parse_html(html: str) -> Page:
    """Never raises; malformed markup yields whatever could be read."""
    ex = _Extractor()
    try:
        ex.feed(html[:3_000_000])
        ex.close()
    except Exception:  # noqa: BLE001 - html.parser can raise on pathological input
        pass
    return ex.finish()


def decode_html(body: bytes, header_charset: str | None = None) -> str:
    """BOM > HTTP charset > <meta charset> > UTF-8 > Windows-1252 (browser behaviour)."""
    candidates: list[str] = []
    for bom, name in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if body.startswith(bom):
            candidates.append(name)
    if header_charset:
        candidates.append(header_charset)
    m = re.search(rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9_\-:.]{2,40})", body[:4096], re.I)
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "cp1252"]
    for name in candidates:
        key = name.strip().lower()
        if key in ("iso-8859-1", "latin1", "latin-1", "us-ascii", "ascii", "iso8859-1"):
            key = "cp1252"  # what browsers do
        try:
            codecs.lookup(key)
            return body.decode(key)
        except (LookupError, UnicodeError):
            continue
    return body.decode("utf-8", "replace")


# ---------------------------------------------------------------------------------------------------------
# finding the Impressum
# ---------------------------------------------------------------------------------------------------------

_IMPRESSUM_TITLE = re.compile(r"impressum|imprint|legal\s+notice|anbieterkennzeichnung|rechtliche\s+hinweise", re.I)
_IMPRESSUM_BODY = re.compile(
    r"angaben\s+gem(?:ä|ae)ß\s*§\s*5|§\s*5\s*(?:tmg|ddg)|§\s*18\s*(?:abs\.?\s*2\s*)?mstv|information\s+(?:according|pursuant)\s+to",
    re.I,
)


def is_impressum_page(page: Page, url: str = "") -> bool:
    if _IMPRESSUM_TITLE.search(page.title) or re.search(r"impressum|imprint|legal-?notice", urlsplit(url).path, re.I):
        return True
    link_texts = {text for _, text in page.links}  # a navigation link called "Impressum" is not the page itself
    if any(
        _IMPRESSUM_TITLE.fullmatch(line.strip(" :.-"))
        for line in page.lines[:200]
        if len(line) < 40 and line not in link_texts
    ):
        return True
    return bool(_IMPRESSUM_BODY.search("\n".join(page.lines[:400])))


_LINK_PATTERNS = (
    (re.compile(r"(impressum|imprint|legal\s+notice|anbieterkennzeichnung)", re.I), 100, True),
    (re.compile(r"impressum|imprint|legal[-_ ]?notice|anbieterkennzeichnung", re.I), 80, False),
    (re.compile(r"rechtliches|rechtliche[-_ ]hinweise|legal", re.I), 50, True),
    (re.compile(r"kontakt|contact", re.I), 30, True),
)


def find_impressum_links(page: Page, base_url: str) -> list[tuple[str, int]]:
    """Candidate follow-up URLs (absolute http/https, no fragments) with a score, best first."""
    best: dict[str, int] = {}
    for href, text in page.links:
        if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        try:
            url = urljoin(base_url, href)
            parts = urlsplit(url)
        except ValueError:
            continue
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        url = url.split("#", 1)[0]
        score = 0
        for rx, points, on_text in _LINK_PATTERNS:
            if (on_text and rx.search(text)) or (not on_text and rx.search(parts.path)):
                score = max(score, points)
        if rx_path_hint(parts.path):
            score = max(score, 80)
        if score:
            best[url] = max(best.get(url, 0), score)
    return sorted(best.items(), key=lambda kv: -kv[1])


def rx_path_hint(path: str) -> bool:
    return bool(re.search(r"/(impressum|imprint|legal-?notice|anbieterkennzeichnung)(/|\.html?|\.php)?$", path, re.I))


# ---------------------------------------------------------------------------------------------------------
# field extraction
# ---------------------------------------------------------------------------------------------------------


@dataclass
class Candidate:
    value: "str | list[str]"
    confidence: float
    evidence: str
    source: str  # json-ld | microdata | text


@dataclass
class Extracted:
    candidates: dict[str, list[Candidate]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add(self, name: str, value: "str | list[str]", confidence: float, evidence: str, source: str) -> None:
        if not value:
            return
        lst = self.candidates.setdefault(name, [])
        for c in lst:
            if c.value == value:
                if confidence > c.confidence:
                    c.confidence, c.evidence, c.source = confidence, evidence_of(evidence), source
                return
        lst.append(Candidate(value, round(confidence, 2), evidence_of(evidence), source))

    def warn(self, text: str) -> None:
        text = clean(text, 200)
        if text and text not in self.warnings:
            self.warnings.append(text)


_LEGAL_FORM = (
    r"(?:GmbH\s*&\s*Co\.?\s*KGaA|GmbH\s*&\s*Co\.?\s*KG|UG\s*\(haftungsbeschränkt\)\s*&\s*Co\.?\s*KG|"
    r"UG\s*\(haftungsbeschränkt\)|gGmbH|GmbH|mbH|KGaA|AG\s*&\s*Co\.?\s*KG|AG|SE|OHG|KG|GbR|"
    r"e\.\s?Kfm\.|e\.\s?Kfr\.|e\.\s?K\.|eK|e\.\s?V\.|eG|PartG\s*mbB|PartG|Ltd\.?|Limited|LLC|Inc\.?|B\.V\.|"
    r"S\.\s?à\s?r\.\s?l\.|S\.A\.|UG)"
)
_LEGAL_FORM_RE = re.compile(rf"(?<![\w.]){_LEGAL_FORM}(?!\w)")
_HEADING_RE = re.compile(
    r"^(?:angaben\s+gem(?:ä|ae)ß\s*§\s*5.*|anbieter(?:kennzeichnung)?|betreiber|verantwortlich.*|impressum|imprint|"
    r"legal\s+notice|provider|information\s+(?:according|pursuant).*|kontakt|contact|diensteanbieter|"
    r"seitenbetreiber|firmenanschrift|anschrift|address|firma|firmenname|unternehmen|company|name)\s*:?$",
    re.I,
)
_FIELD_HEADING_RE = re.compile(
    r"^(?:firmenanschrift|anschrift|address|firma|firmenname|unternehmen|company|name|adresse)\s*:?$", re.I
)
_ZIP_CITY_RE = re.compile(
    r"^(?:(?P<cc>D|DE|A|AT|CH)\s*[-–]\s*)?(?P<zip>\d{4,5})\s+(?P<city>[A-ZÄÖÜ][\wäöüÄÖÜß.\-/ ()]{1,58}?)\s*[,;]?$"
)
_STREET_RE = re.compile(
    r"^(?:Postfach\s+\d{2,10}|[A-ZÄÖÜ0-9][\wäöüÄÖÜß.\-' ]{1,48}?\s+\d{1,4}\s?[a-zA-Z]?(?:\s?[-–/]\s?\d{1,4}\s?[a-zA-Z]?)?)$"
)
_COUNTRY_RE = re.compile(r"^(Deutschland|Germany|Österreich|Austria|Schweiz|Switzerland)$", re.I)
_COUNTRY_NAMES = {
    "deutschland": "Germany", "germany": "Germany", "österreich": "Austria", "austria": "Austria",
    "schweiz": "Switzerland", "switzerland": "Switzerland",
}  # fmt: skip
_LABEL_WORDS = re.compile(
    r"^(?:tel|telefon|phone|fon|fax|telefax|e-?mail|mail|web|internet|www|ust|umsatzsteuer|steuer|vat|register|handelsregister|"
    r"amtsgericht|registergericht|hrb|hra|gesch(?:ä|ae)ftsf|vertret|verantwortlich|inhaber|vorstand|sitz|kontakt|contact|"
    r"managing|director|represented|stand|datenschutz|haftung|firma|anschrift|adresse|address|mobil)",
    re.I,
)


def _split_segments(line: str) -> list[str]:
    return [s.strip() for s in re.split(r"\s*[|•·●–—]\s*|\s{3,}", line) if s.strip()]


def find_address(lines: list[str]) -> tuple[dict[str, str], str, bool] | None:
    """Locate name / street / zip / city. Returns (parts, evidence, after_heading) or None."""
    n = len(lines)
    heading_idx = [i for i, ln in enumerate(lines[:300]) if len(ln) < 90 and _HEADING_RE.match(ln.strip())]
    zip_idx: list[tuple[int, str, re.Match]] = []
    for i, ln in enumerate(lines[:600]):
        for seg in [ln, *_comma_parts(ln)]:
            m = _ZIP_CITY_RE.match(seg.strip())
            if m and (len(m.group("zip")) == 5 or m.group("cc") or _near_country(lines, i)):
                zip_idx.append((i, seg, m))
                break
    if not zip_idx:
        return None
    chosen = None
    after_heading = False
    for h in heading_idx:
        for i, seg, m in zip_idx:
            if h < i <= h + 9:
                chosen, after_heading = (i, seg, m), True
                break
        if chosen:
            break
    if chosen is None:
        chosen = zip_idx[0]
    i, seg, m = chosen
    parts: dict[str, str] = {"zip": m.group("zip"), "city": clean(m.group("city").strip(" ,;"), 80)}
    cc = (m.group("cc") or "").upper()
    if cc in ("A", "AT"):
        parts["country"] = "Austria"
    elif cc == "CH":
        parts["country"] = "Switzerland"
    start = i
    # single line "Name, Street 1, 12345 City"
    if seg is not lines[i] or lines[i] != seg:
        pieces = _comma_parts(lines[i])
        k = pieces.index(seg) if seg in pieces else len(pieces)
        before = pieces[:k]
        if before and _STREET_RE.match(before[-1]):
            parts["street"] = clean(before[-1], 80)
            if len(before) > 1:
                parts["_name"] = clean(", ".join(before[:-1]), 120)
    if "street" not in parts:
        for j in range(i - 1, max(-1, i - 4), -1):
            cand = lines[j].strip(" ,;")
            if _STREET_RE.match(cand) and not _LABEL_WORDS.match(cand):
                parts["street"] = clean(cand, 80)
                start = j
                break
    # name = the best line above the street (within the block after the heading)
    if "_name" not in parts:
        top = max((h for h in heading_idx if h < start and not _FIELD_HEADING_RE.match(lines[h].strip())), default=-1)
        block = [ln for ln in lines[max(top + 1, start - 6) : start] if ln.strip()]
        block = [ln for ln in block if not _HEADING_RE.match(ln.strip()) and not _LABEL_WORDS.match(ln)]
        named = [ln for ln in block if _LEGAL_FORM_RE.search(ln)]
        if named:
            parts["_name"] = clean(named[0], 120)
        elif block and after_heading:
            parts["_name"] = clean(block[0], 120)
            parts["_weak_name"] = "1"
    for j in range(i + 1, min(n, i + 3)):
        if _COUNTRY_RE.match(lines[j].strip()):
            parts["country"] = _COUNTRY_NAMES[lines[j].strip().lower()]
            break
    ev_start = max(0, start - 3)
    evidence = " / ".join(lines[ev_start : i + 1])
    return parts, evidence, after_heading


def _near_country(lines: list[str], i: int) -> bool:
    return any(_COUNTRY_RE.match(x.strip()) for x in lines[i + 1 : i + 3]) or bool(
        re.search(r"österreich|austria|schweiz|switzerland", " ".join(lines[max(0, i - 4) : i + 3]), re.I)
    )


def _comma_parts(line: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s*,\s*", line) if p.strip()]


def _legal_form_of(name: str) -> str | None:
    forms = list(_LEGAL_FORM_RE.finditer(name))
    if not forms:
        return None
    form = forms[-1].group(0)
    return re.sub(r"\s+", " ", form).replace("e. K.", "e.K.").replace("e.K", "e.K.").replace("e.K..", "e.K.")


_NAME_TOKEN = r"[A-ZÄÖÜ][\wäöüÄÖÜß'’\-]+"
_PERSON_RE = re.compile(
    rf"^(?:(?:Dr|Prof|Dipl|Ing|Mag|MBA|M\.\s?Sc|B\.\s?Sc|Ph\.\s?D)\.?[\-\s]*)*{_NAME_TOKEN}"
    rf"(?:\s+(?:(?:von|van|de|zu|zur|der|den|da|di|le|la|bin|ben|vom)\s+)?{_NAME_TOKEN}){{1,3}}$"
)
_DIRECTOR_LABEL = re.compile(
    r"^(?:gesch(?:ä|ae)ftsf(?:ü|ue)hr(?:er(?:in|innen)?|ung|er/in|er\(in\))|vertretungsberechtigt\w*(?:\s+gesch(?:ä|ae)ftsf(?:ü|ue)hrer\w*)?|"
    r"vorstand|vorsitzende[rn]?|inhaber(?:in)?|managing\s+directors?|directors?|ceo|owner|vertreten\s+durch|"
    r"represented\s+by|authori[sz]ed\s+(?:representative|signatory))\s*(?:\([^)]*\))?\s*[:\-]?\s*(?P<rest>.*)$",
    re.I,
)
_CONTACT_LABEL = re.compile(
    r"^(?:verantwortlich\s+(?:f(?:ü|ue)r\s+den\s+inhalt\s+)?(?:nach|gem(?:ä|ae)ß)[^:]*|verantwortlich(?:\s+f(?:ü|ue)r\s+den\s+inhalt)?|"
    r"ansprechpartner(?:in)?|contact\s+person|responsible\s+for\s+(?:the\s+)?content[^:]*)\s*:?\s*(?P<rest>.*)$",
    re.I,
)


def _names_from(text: str) -> list[str]:
    out = []
    for token in re.split(r"\s*(?:,|;|/|&|\bund\b|\band\b|\|)\s*", text):
        token = _LEGAL_FORM_RE.sub("", token).strip(" .,:;-")
        token = re.sub(r"\s+", " ", token)
        if token and len(token) <= 60 and not any(ch.isdigit() for ch in token) and _PERSON_RE.match(token):
            if not _LABEL_WORDS.match(token) and token.lower() not in ("geschäftsführer", "inhaber"):
                out.append(clean(token, 60))
    return out


def _labelled_people(lines: list[str], label: re.Pattern, limit: int = 5) -> tuple[list[str], str] | None:
    for i, ln in enumerate(lines[:700]):
        m = label.match(ln.strip())
        if not m:
            continue
        names = _names_from(m.group("rest"))
        used = [ln]
        for nxt in lines[i + 1 : i + 6]:
            if len(names) >= limit:
                break
            if _LABEL_WORDS.match(nxt) or label.match(nxt.strip()) or _HEADING_RE.match(nxt.strip()):
                break
            more = _names_from(nxt)
            if not more:
                break
            names += more
            used.append(nxt)
        if names:
            seen: list[str] = []
            for nm in names:
                if nm not in seen:
                    seen.append(nm)
            return seen[:limit], " / ".join(used)
    return None


def _label_value(lines: list[str], label: str, flags: int = re.I, include_next: bool = False) -> list[tuple[str, str]]:
    """Text after ``label`` on the same line (or on the next non-empty line) with the evidence line.

    With ``include_next`` the following line is always appended (values that may sit below a long label)."""
    rx = re.compile(rf"(?:^|[\s,;|(])(?:{label})(?![\wäöüÄÖÜß])\s*(?:\.|\(.*?\))?\s*[:\-–]?\s*(?P<rest>.*)$", flags)
    out = []
    for i, ln in enumerate(lines[:700]):
        m = rx.search(ln)
        if not m:
            continue
        rest = m.group("rest").strip()
        evidence = ln
        if (not rest or include_next) and i + 1 < len(lines):
            rest = (rest + " " + lines[i + 1].strip()).strip()
            evidence = ln + " / " + lines[i + 1]
        if rest:
            out.append((rest, evidence))
    return out


_PHONE_LABEL = r"Tel(?:efon)?|Phone|Telephone|Fon|Mobil(?:e)?|Rufnummer|Tel\.-Nr"
_PHONE_RE = re.compile(r"\+?\(?\d[\d\s()./\-–]{4,26}\d")
_AT = re.compile(r"\s*[\(\[\{<]\s*(?:at|ät|@)\s*[\)\]\}>]\s*", re.I)
_DOT = re.compile(r"\s*[\(\[\{<]\s*(?:dot|punkt|\.)\s*[\)\]\}>]\s*", re.I)
_SPACED_AT = re.compile(r"([A-Za-z0-9._%+\-]+)\s+@\s+([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)")


def deobfuscate(text: str) -> str:
    t = _AT.sub("@", text)
    t = _DOT.sub(".", t)
    return _SPACED_AT.sub(r"\1@\2", t)


def extract_fields(page: Page) -> Extracted:
    ex = Extracted()
    _from_structured(page, ex)
    lines = page.lines
    _text_company(lines, ex)
    _text_vat_tax_register(lines, ex)
    _text_contacts(page, ex)
    _text_people(lines, ex)
    return ex


def _from_structured(page: Page, ex: Extracted) -> None:
    for node in page.jsonld:
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if not any(
            isinstance(t, str) and re.search(r"Organization|Business|Corporation|Store|Service|Person|Company", t)
            for t in types
        ):
            continue
        ev = "json-ld: " + json.dumps({k: v for k, v in node.items() if isinstance(v, (str, int))}, ensure_ascii=False)
        _structured_fields(
            ex,
            "json-ld",
            ev,
            name=_s(node.get("legalName")) or _s(node.get("name")),
            legal=bool(node.get("legalName")),
            email=_s(node.get("email")),
            phone=_s(node.get("telephone")),
            vat=_s(node.get("vatID")),
            tax=_s(node.get("taxID")),
            address=node.get("address"),
        )
    for scope in page.microdata:
        if not re.search(r"Organization|Business|Corporation|Store|Service|Person|Company", scope.get("type", "")):
            continue
        props = scope["props"]
        first = lambda k: next((v for v in props.get(k, []) if isinstance(v, str)), None)  # noqa: E731
        addr = next((v for v in props.get("address", []) if isinstance(v, dict)), None)
        address = (
            {k: next((x for x in v if isinstance(x, str)), None) for k, v in addr["props"].items()} if addr else None
        )
        ev = "microdata: " + json.dumps({k: v for k, v in props.items() if k != "address"}, ensure_ascii=False)
        _structured_fields(
            ex,
            "microdata",
            ev,
            name=first("legalName") or first("name"),
            legal=bool(first("legalName")),
            email=first("email"),
            phone=first("telephone"),
            vat=first("vatID"),
            tax=first("taxID"),
            address=address,
        )


def _s(v: object) -> str | None:
    if isinstance(v, str):
        return v
    if isinstance(v, dict) and isinstance(v.get("name"), str):
        return v["name"]
    return None


def _structured_fields(ex: Extracted, source: str, ev: str, *, name, legal, email, phone, vat, tax, address) -> None:
    if name:
        name = clean(name, 120)
        ex.add("name", name, 0.9 if legal else 0.8, ev, source)
        lf = _legal_form_of(name)
        if lf:
            ex.add("legal_form", lf, 0.85, ev, source)
    if email and (e := validate_email(email)):
        ex.add("email", e, 0.9, ev, source)
    if phone:
        p = normalize_phone(phone)
        if p:
            ex.add("phone", p, 0.85, ev, source)
        else:
            ex.warn(f"Ignored an invalid phone number: {clean(phone, 40)}")
    if vat:
        v = validate_vat_id_de(vat)
        if v:
            ex.add("vat_id", v, 0.95, ev, source)
        else:
            ex.warn(f"Ignored an invalid VAT ID: {clean(vat, 30)}")
    if tax:
        ex.add("tax_number", clean(tax, 30), 0.7, ev, source)
    if isinstance(address, dict):
        country = _s(address.get("addressCountry"))
        country_name = _COUNTRY_NAMES.get((country or "").lower(), None)
        if not country_name and country and country.upper() in ("DE", "AT", "CH"):
            country_name = {"DE": "Germany", "AT": "Austria", "CH": "Switzerland"}[country.upper()]
        zip_ = validate_zip(clean(address.get("postalCode") or "", 10), country_name)
        if address.get("postalCode") and not zip_:
            ex.warn(f"Ignored an invalid postal code: {clean(address.get('postalCode'), 12)}")
        if street := address.get("streetAddress"):
            ex.add("street", clean(street, 80), 0.9, ev, source)
        if zip_:
            ex.add("zip", zip_, 0.9, ev, source)
        if city := address.get("addressLocality"):
            ex.add("city", clean(city, 80), 0.9, ev, source)
        if country_name:
            ex.add("country", country_name, 0.9, ev, source)


def _text_company(lines: list[str], ex: Extracted) -> None:
    found = find_address(lines)
    if not found:
        return
    parts, evidence, after_heading = found
    base = 0.9 if after_heading else 0.7
    if "zip" in parts:
        z = validate_zip(parts["zip"], parts.get("country"))
        if z:
            ex.add("zip", z, base, evidence, "text")
            ex.add("city", parts["city"], base, evidence, "text")
        else:
            ex.warn(f"Ignored an invalid postal code: {parts['zip']}")
    if "street" in parts:
        ex.add("street", parts["street"], base - 0.05, evidence, "text")
    if "country" in parts:
        ex.add("country", parts["country"], 0.85, evidence, "text")
    name = parts.get("_name")
    if name:
        lf = _legal_form_of(name)
        if lf:
            ex.add("name", name.strip(" ,;"), base, evidence, "text")
            ex.add("legal_form", lf, base, evidence, "text")
        else:
            ex.add("name", name.strip(" ,;"), 0.55 if parts.get("_weak_name") else 0.6, evidence, "text")


def _text_vat_tax_register(lines: list[str], ex: Extracted) -> None:
    vat_label = r"USt\.?-?\s?Id\.?-?\s?(?:Nr|Nummer)?\.?|Umsatzsteuer(?:-|\s)?Identifikationsnummer|Umsatzsteuer-?ID|VAT\s*(?:ID|number|no\.?|reg\w*)?|UID"
    for rest, ev in _label_value(lines, vat_label, include_next=True):
        for vm in re.finditer(r"\b(ATU\d{8}|[A-Z]{2}\s?\d[\d\s.]{6,12}\d)\b", rest.upper()):
            raw = vm.group(1).strip()
            if raw.startswith("DE"):
                v = validate_vat_id_de(raw)
                if v:
                    ex.add("vat_id", v, 0.95, ev, "text")
                else:
                    ex.warn(f"Ignored a VAT ID that failed the check-digit test: {clean(raw, 20)}")
            else:
                ex.warn(f"VAT ID {clean(raw, 20)} is not German and was not checked")
    # unlabelled DE numbers that pass the checksum
    for ln in lines[:700]:
        for um in re.finditer(r"\bDE\s?\d{3}[\s.]?\d{3}[\s.]?\d{3}\b", ln):
            v = validate_vat_id_de(um.group(0))
            if v:
                ex.add("vat_id", v, 0.75, ln, "text")
    for rest, ev in _label_value(lines, r"Steuernummer|Steuer-Nr|St\.-?Nr|Tax\s+(?:number|no\.?|id)"):
        m = re.search(r"\b(\d{2,3}\s?/\s?\d{3,4}\s?/\s?\d{4,5}|\d{10,13})\b", rest)
        if m:
            ex.add("tax_number", re.sub(r"\s", "", m.group(1)), 0.8, ev, "text")
    reg_number = None
    for ln in lines[:700]:
        m = re.search(r"\b(HR[AB]|GnR|VR|PR)\s*[:\-]?\s*(\d{1,7}(?:\s?[A-Z]{1,2})?)\b", ln)
        if m:
            reg_number = f"{m.group(1)} {m.group(2).strip()}"
            ex.add("register_number", reg_number, 0.85, ln, "text")
            break
    stop = r"(?=\s*(?:HR[AB]\b|GnR\b|VR\b|PR\b|Registernummer|Handelsregister|Register\b|USt|Umsatz|Steuer|Gesch|Tel|E-?Mail|Fax|[,;|(]|$))"
    for ln in lines[:700]:
        m = re.search(
            rf"Amtsgericht\s+([A-ZÄÖÜ][\wäöüÄÖÜß\-]+(?:\s+(?:am|an\s+der|im|i\.)\s+[A-ZÄÖÜ][\wäöüÄÖÜß\-]+|\s+[A-ZÄÖÜ][\wäöüÄÖÜß\-]+)?){stop}",
            ln,
        )
        if m:
            ex.add("register_court", "Amtsgericht " + clean(m.group(1), 60), 0.85, ln, "text")
            break
    else:
        for ln in lines[:700]:
            m = re.search(
                r"\b([A-ZÄÖÜ][\wäöüÄÖÜß\-]+)\s+(?:local|district)\s+court\b|\b(?:local|district)\s+court\s+(?:of\s+)?([A-ZÄÖÜ][\wäöüÄÖÜß\-]+)",
                ln,
                re.I,
            )
            if m:
                ex.add("register_court", "Amtsgericht " + clean(m.group(1) or m.group(2), 60), 0.7, ln, "text")
                break
        for rest, ev in _label_value(lines, r"Registergericht|Register\s+court|Court\s+of\s+registration"):
            city = re.match(r"([A-ZÄÖÜ][\wäöüÄÖÜß\-]+(?:\s+[A-ZÄÖÜ][\wäöüÄÖÜß\-]+)?)", rest)
            if city:
                court = city.group(1)
                ex.add(
                    "register_court",
                    court if court.startswith("Amtsgericht") else "Amtsgericht " + court,
                    0.75,
                    ev,
                    "text",
                )
                break


def _text_contacts(page: Page, ex: Extracted) -> None:
    lines = page.lines
    seen: set[str] = set()
    for raw in page.mailtos:
        e = validate_email(deobfuscate(raw))
        if e and e not in seen:
            seen.add(e)
            ex.add("email", e, 0.85, f"mailto:{e}", "text")
    label = re.compile(r"e-?mail|mail|kontakt|contact", re.I)
    for ln in lines[:700]:
        text = deobfuscate(ln)
        for em in _EMAIL_RE.finditer(text):
            e = validate_email(em.group(0))
            if e and e not in seen:
                seen.add(e)
                ex.add("email", e, 0.8 if label.search(text[: em.start()]) else 0.6, ln, "text")
    for tel in page.tels:
        p = normalize_phone(tel)
        if p:
            ex.add("phone", p, 0.8, f"tel:{tel}", "text")
    for rest, ev in _label_value(lines, _PHONE_LABEL):
        if re.match(r"(?:fax|telefax)", rest, re.I):
            continue
        m = _PHONE_RE.search(rest)
        if not m:
            continue
        p = normalize_phone(m.group(0))
        if p:
            ex.add("phone", p, 0.85, ev, "text")
        else:
            ex.warn(f"Ignored an invalid phone number: {clean(m.group(0), 40)}")


def _text_people(lines: list[str], ex: Extracted) -> None:
    directors = _labelled_people(lines, _DIRECTOR_LABEL)
    if directors:
        ex.add("managing_directors", directors[0], 0.7, directors[1], "text")
    contact = _labelled_people(lines, _CONTACT_LABEL, limit=1)
    if contact:
        ex.add("contact", contact[0][0], 0.7, contact[1], "text")
    elif directors:
        ex.add("contact", directors[0][0], 0.45, directors[1], "text")
