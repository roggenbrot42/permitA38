"""Category from words on the bill (docs/specs/receipt-scan.md, "What is booked", step 2).

The table is ordered: the first category with a matching word wins. Every target is a value of ``EXP_CATS`` in
``web/app2.js`` (a test compares the two). A word matches at the start of a word of the text, case-folded, so
``bahn`` finds "Deutsche Bahn" but not "Straßenbahn".
"""

import re

DEFAULT_CATEGORY = "Other"

CATEGORY_WORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Accommodation",
        ("hotel", "pension", "übernachtung", "uebernachtung", "hostel", "gästehaus", "ferienwohnung", "lodging"),
    ),
    (
        "Transportation",
        (
            "tankstelle",
            "tanken",
            "diesel",
            "super e10",
            "bahn",
            "taxi",
            "parkhaus",
            "parkgebühr",
            "parken",
            "parking",
            "fuel",
            "maut",
        ),
    ),
    (
        "Business meals",
        (
            "restaurant",
            "gaststätte",
            "gaststaette",
            "gasthaus",
            "bewirtung",
            "café",
            "cafe",
            "bistro",
            "trinkgeld",
            "pizzeria",
            "trattoria",
            "imbiss",
        ),
    ),
    ("Travel", ("flug", "airline", "reisekosten", "reisebüro", "fahrkarte", "ticket")),
    (
        "Software & subscriptions",
        (
            "software",
            "lizenz",
            "licence",
            "license",
            "abonnement",
            "subscription",
            "hosting",
            "cloud",
            "domain",
            "anthropic",
            "openai",
            "chatgpt",
            "max plan",
            "extra usage",
        ),
    ),
    ("Hardware & equipment", ("computer", "notebook", "laptop", "monitor", "tastatur", "festplatte", "drucker")),
    ("Office & coworking", ("coworking", "büromaterial", "bürobedarf", "papier", "schreibwaren", "toner")),
    ("Phone & internet", ("mobilfunk", "telefon", "internet", "dsl", "handy")),
    ("Training", ("seminar", "schulung", "fortbildung", "workshop", "kurs", "training")),
    ("Insurance", ("versicherung", "insurance")),
    ("Marketing", ("werbung", "marketing", "anzeige", "advertising")),
    ("Fees & bank", ("kontoführung", "kontofuehrung", "bankgebühr", "gebühr", "gebuehr")),
]

CATEGORY_TARGETS = frozenset(c for c, _ in CATEGORY_WORDS) | {DEFAULT_CATEGORY}

WHOLE_WORDS = frozenset(
    {"bahn"}
)  # only as a word of its own or after a hyphen: "Deutsche Bahn", "S-Bahn", not "Bahnhof"
ROOM = re.compile(r"übernachtung|uebernachtung|zimmer|\broom\b", re.IGNORECASE)


def _alternative(word: str) -> str:
    return re.escape(word) + (r"(?!\w)" if word in WHOLE_WORDS else "")


_PATTERNS = [
    (cat, re.compile(r"(?<!\w)(?:" + "|".join(_alternative(w) for w in words) + ")", re.IGNORECASE))
    for cat, words in CATEGORY_WORDS
]


def category_from_text(*texts: str) -> str | None:
    """First category whose word occurs in the first text that has any match (the vendor comes first), else ``None``.

    A meal word in the vendor ("Hotel Restaurant Adler", "Cafe am Bahnhof") beats Transportation and Accommodation;
    only item text that shows a room (Übernachtung, Zimmer, Room) makes it Accommodation."""
    if texts and texts[0]:
        vendor_cats = [cat for cat, pattern in _PATTERNS if pattern.search(texts[0])]
        if "Business meals" in vendor_cats:
            return "Accommodation" if ROOM.search(" ".join(texts)) else "Business meals"
    for text in texts:
        if not text:
            continue
        for cat, pattern in _PATTERNS:
            if pattern.search(text):
                return cat
    return None
