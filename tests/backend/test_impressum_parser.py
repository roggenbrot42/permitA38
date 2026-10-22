"""Deterministic Impressum parsing: realistic fake layouts, validators and hostile input."""

import random
import time
import unicodedata

import pytest

from permita38.core.impressum_parser import (
    clean,
    decode_html,
    extract_fields,
    find_impressum_links,
    is_impressum_page,
    normalize_phone,
    parse_html,
    validate_email,
    validate_vat_id_de,
    validate_zip,
    vat_id_check_digit,
)
from permita38.core.service.impressum import ImpressumService

from .impressum_helpers import fixture_bytes

VAT = "DE123456788"  # the widely used dummy number; its check digit is valid


def fields_of(name: str) -> dict:
    page = parse_html(decode_html(fixture_bytes(name)))
    return ImpressumService._merge("https://x.example/", None, [extract_fields(page)], [])


EXPECTED = {
    "gmbh_register": {
        "name": "Beispiel Software GmbH", "legal_form": "GmbH", "street": "Musterstraße 12", "zip": "80331",
        "city": "München", "email": "info@example.com", "phone": "+49891234567", "vat_id": VAT,
        "register_court": "Amtsgericht München", "register_number": "HRB 123456",
        "managing_directors": ["Erika Mustermann", "Max Beispiel"], "contact": "Erika Mustermann",
    },
    "ek_inhaber": {
        "name": "Max Mustermann e.K.", "legal_form": "e.K.", "street": "Beispielgasse 7a", "zip": "50667",
        "city": "Köln", "email": "kontakt@example.org", "phone": "02219876543", "vat_id": VAT,
        "tax_number": "143/123/45678", "register_court": "Amtsgericht Köln", "register_number": "HRA 7654",
        "managing_directors": ["Max Mustermann"],
    },
    "freelancer": {
        "name": "Erika Musterfrau", "street": "Am Beispielplatz 3", "zip": "01067", "city": "Dresden",
        "email": "erika@example.net", "phone": "+493517654321", "vat_id": VAT, "contact": "Erika Musterfrau",
    },
    "english_imprint": {
        "name": "Example Consulting Ltd.", "legal_form": "Ltd.", "street": "Sample Street 21", "zip": "20095",
        "city": "Hamburg", "country": "Germany", "email": "hello@example.com", "phone": "+49405550123",
        "vat_id": VAT, "register_court": "Amtsgericht Hamburg", "register_number": "HRB 987654",
        "managing_directors": ["Jane Sample", "John Example"],
    },
    "jsonld_only": {
        "name": "Beispiel Digital UG (haftungsbeschränkt)", "legal_form": "UG (haftungsbeschränkt)",
        "street": "Probeweg 4", "zip": "70173", "city": "Stuttgart", "country": "Germany",
        "email": "team@example.com", "phone": "+497115550000", "vat_id": VAT,
    },
    "microdata": {
        "name": "Probe Handels GmbH", "legal_form": "GmbH", "street": "Teststraße 8", "zip": "90402",
        "city": "Nürnberg", "country": "Germany", "email": "info@example.com", "phone": "+49911123456",
    },
    "obfuscated_email": {
        "name": "Muster & Söhne GmbH", "legal_form": "GmbH", "street": "Testallee 5", "zip": "04109",
        "city": "Leipzig", "email": "kontakt@example.org", "phone": "+493411234567",
    },
    "latin1_koeln": {
        "name": "Müller & Söhne GmbH & Co. KG", "legal_form": "GmbH & Co. KG", "street": "Bäckerstraße 9",
        "zip": "50667", "city": "Köln", "email": "info@example.com", "phone": "02215550199",
        "managing_directors": ["Jörg Müller"],
    },
    "single_line": {
        "name": "Probe & Muster GmbH", "legal_form": "GmbH", "street": "Hauptstr. 15", "zip": "60311",
        "city": "Frankfurt am Main", "email": "info@example.com", "phone": "0695550100",
        "register_court": "Amtsgericht Frankfurt am Main", "register_number": "HRB 55555",
    },
    "table_layout": {
        "name": "Tabellen Technik GmbH & Co. KG", "legal_form": "GmbH & Co. KG", "street": "Rastergasse 2",
        "zip": "28195", "city": "Bremen", "email": "service@example.com", "phone": "04213330000",
        "register_court": "Amtsgericht Bremen", "register_number": "HRA 4711", "managing_directors": ["Anna Probe"],
    },
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_realistic_layouts(name):
    result = fields_of(name)
    got = result["fields"]
    for key, expected in EXPECTED[name].items():
        assert got.get(key) == expected, f"{name}: {key}"
    # every returned value has a plausible confidence and an evidence snippet
    for key in got:
        assert 0.4 <= result["confidence"][key] <= 1.0, key
        assert result["evidence"][key], key
    assert result["warnings"] == []


def test_fax_numbers_are_not_phone_numbers():
    result = fields_of("gmbh_register")  # has "Telefon: +49 (0) 89 1234567" and "Telefax: +49 89 1234568"
    assert result["fields"]["phone"] == "+49891234567"
    assert "phone" not in result["candidates"]
    html = "<h1>Impressum</h1><p>Telefax: 089 555 12 34<br>Fax 089 555 12 35</p>"
    assert "phone" not in extract_fields(parse_html(html)).candidates


def test_ambiguous_values_are_returned_as_candidates():
    result = fields_of("obfuscated_email")
    assert result["fields"]["email"] == "kontakt@example.org"
    assert [c["value"] for c in result["candidates"]["email"]] == ["buero@example.org"]


def test_confidence_ranking_prefers_structured_and_validated_data():
    result = fields_of("jsonld_only")
    assert result["confidence"]["vat_id"] >= 0.9 and result["confidence"]["street"] >= 0.85
    assert "logo@2x.png" not in str(fields_of("obfuscated_email"))  # file names are not e-mail addresses


def test_no_impressum_and_javascript_app_yield_nothing():
    for name in ("no_impressum", "js_app", "split_home"):
        page = parse_html(decode_html(fixture_bytes(name)))
        assert extract_fields(page).candidates == {}
    assert parse_html(decode_html(fixture_bytes("js_app"))).script_only


def test_invalid_values_become_warnings_not_fields():
    html = """<h1>Impressum</h1><p>Angaben gemäß § 5 DDG</p><p>Fehler GmbH<br>Weg 1<br>00000 Ort</p>
    <p>USt-IdNr.: DE123456789</p><p>Tel.: 0000000</p><p>E-Mail: kaputt@@example.com</p>"""
    result = ImpressumService._merge("x", None, [extract_fields(parse_html(html))], [])
    assert "vat_id" not in result["fields"] and "zip" not in result["fields"] and "email" not in result["fields"]
    assert "phone" not in result["fields"]
    text = " ".join(result["warnings"])
    assert "check-digit" in text and "postal code" in text


def test_non_german_vat_id_is_not_adopted_but_reported():
    html = "<h1>Impressum</h1><p>USt-IdNr.: ATU12345678</p><p>Muster GmbH<br>Ring 1<br>12345 Stadt</p>"
    result = ImpressumService._merge("x", None, [extract_fields(parse_html(html))], [])
    assert "vat_id" not in result["fields"] and any("not German" in w for w in result["warnings"])


@pytest.mark.parametrize(
    "line,form",
    [
        ("Alpha UG (haftungsbeschränkt)", "UG (haftungsbeschränkt)"), ("Beta GmbH & Co. KG", "GmbH & Co. KG"),
        ("Gamma AG", "AG"), ("Delta OHG", "OHG"), ("Epsilon KG", "KG"), ("Zeta GbR", "GbR"),
        ("Eta e.K.", "e.K."), ("Theta e.V.", "e.V."), ("Iota SE", "SE"), ("Kappa gGmbH", "gGmbH"),
        ("Lambda Ltd.", "Ltd."), ("My Partner PartG mbB", "PartG mbB"),
    ],
)  # fmt: skip
def test_legal_forms(line, form):
    html = f"<h1>Impressum</h1><p>Angaben gemäß § 5 DDG</p><p>{line}<br>Beispielweg 3<br>12345 Beispielstadt</p>"
    result = ImpressumService._merge("x", None, [extract_fields(parse_html(html))], [])
    assert result["fields"]["name"] == line and result["fields"]["legal_form"] == form


def test_austrian_and_swiss_addresses():
    for cc, country in (("A", "Austria"), ("CH", "Switzerland")):
        html = f"<h1>Impressum</h1><p>Angaben gemäß § 5 DDG</p><p>Muster GmbH<br>Ring 4<br>{cc}-1010 Stadt</p>"
        result = ImpressumService._merge("x", None, [extract_fields(parse_html(html))], [])
        assert result["fields"]["zip"] == "1010" and result["fields"]["country"] == country


# ---- validators -----------------------------------------------------------------------------------------


def _independent_vat_check(nine: str) -> bool:
    """Second formulation of the procedure: run all nine digits, a valid number ends with product == 2."""
    product = 10
    for ch in nine:
        total = (int(ch) + product) % 10 or 10
        product = (2 * total) % 11
    return product == 2


def test_vat_id_numbers_derived_with_the_algorithm_validate():
    rnd = random.Random(38)
    for _ in range(400):
        first = str(rnd.randint(1, 9)) + "".join(rnd.choice("0123456789") for _ in range(7))
        number = first + vat_id_check_digit(first)
        assert _independent_vat_check(number)
        assert validate_vat_id_de("DE" + number) == "DE" + number
        wrong = number[:8] + str((int(number[8]) + rnd.randint(1, 9)) % 10)
        assert not _independent_vat_check(wrong) and validate_vat_id_de("DE" + wrong) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("DE123456788", VAT), ("de123456788", VAT), ("DE 123 456 788", VAT), ("DE123.456.788", VAT),
        (" DE123456788 ", VAT), ("DE123456789", None), ("DE12345678", None), ("DE1234567888", None),
        ("AT123456788", None), ("DE023456781", None), ("DEABCDEFGHI", None), ("", None),
    ],
)  # fmt: skip
def test_vat_id_formats(value, expected):
    assert validate_vat_id_de(value) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+49 (0) 89 1234567", "+49891234567"), ("0049 89 1234567", "+49891234567"), ("089 / 123 45 67", "0891234567"),
        ("+49-89-1234567", "+49891234567"), ("0000000", None), ("12345", None), ("+4912345678901234567", None),
        ("tel", None), ("", None),
    ],
)  # fmt: skip
def test_phone_normalisation(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "value,ok",
    [
        ("info@example.com", True), ("a.b+c@sub.example.de", True), ("A@EXAMPLE.COM", True), ("x@y", False),
        ("a@@b.com", False), ("a b@example.com", False), ("@example.com", False), ("a@example.", False),
        ("logo@2x.png", False), ("a@b.c", False), (".a@example.com", False), ("a..b@example.com", False),
        ("a@-example.com", False), ("info@example.com.", True), ("<info@example.com>", True), ("a" * 70 + "@example.com", False),
    ],
)  # fmt: skip
def test_email_validation(value, ok):
    assert (validate_email(value) is not None) is ok


def test_zip_validation():
    assert validate_zip("80331") == "80331" and validate_zip("8033") is None and validate_zip("00000") is None
    assert validate_zip("1010", "Austria") == "1010" and validate_zip("80331", "Austria") is None


# ---- decoding, links, page detection --------------------------------------------------------------------


def test_decoding():
    assert "Bäckerstraße" in decode_html(fixture_bytes("latin1_koeln"))  # <meta charset=iso-8859-1>
    assert decode_html("Müller".encode("utf-8-sig")) == "Müller"
    assert decode_html("Müller".encode("utf-16")) == "Müller"
    assert decode_html("Müller".encode("cp1252"), None) == "Müller"  # not valid UTF-8 -> Windows-1252
    assert decode_html("Müller".encode("latin-1"), "iso-8859-1") == "Müller"  # header charset wins
    assert decode_html(b"abc", "no-such-charset") == "abc"
    assert isinstance(decode_html(bytes(range(256))), str)


def test_link_discovery_scores_and_resolves():
    page = parse_html(decode_html(fixture_bytes("split_home")))
    links = find_impressum_links(page, "https://www.teilbar.example/start/")
    assert links[0] == ("https://www.teilbar.example/rechtliches/impressum.html", 100)
    assert all(u.startswith("https://") for u, _ in links)
    html = (
        '<a href="#top">Impressum</a><a href="javascript:void(0)">Impressum</a><a href="mailto:a@b.de">Kontakt</a>'
        '<a href="tel:123">Impressum</a><a href="/imprint">Home</a><a href="/blog">Blog</a><a href="/kontakt">Kontakt</a>'
    )
    found = dict(find_impressum_links(parse_html(html), "https://example.com/"))
    assert found == {"https://example.com/imprint": 80, "https://example.com/kontakt": 30}


def test_impressum_page_detection():
    assert is_impressum_page(parse_html(decode_html(fixture_bytes("gmbh_register"))))
    assert is_impressum_page(parse_html("<p>x</p>"), "https://example.com/de/impressum")
    assert is_impressum_page(parse_html("<p>Angaben gemäß § 5 DDG</p>"))
    assert not is_impressum_page(parse_html(decode_html(fixture_bytes("no_impressum"))), "https://example.com/")


# ---- hygiene --------------------------------------------------------------------------------------------


def test_clean_strips_control_format_and_markup_characters():
    dirty = "A\x00B\u202eC\u200bD<script>E</script>\u2028F\x1b[31mG\ufeffH   I"
    out = clean(dirty)
    assert out == "ABCDscriptE/script F[31mGH I"
    assert "<" not in out and ">" not in out
    assert all(not unicodedata.category(c).startswith("C") for c in out)
    assert len(clean("x" * 1000, 50)) == 50


FRAGMENTS = [
    "<p>", "</p>", "<div>", "</div>", "<br>", "<td>", "<tr", "<a href=\"mailto:x@example.com\">", "</a>",
    "<a href='javascript:alert(1)'>", "<script>", "</script>", "<style>", "<!--", "-->", "<![CDATA[", "&amp;",
    "&#0;", "&#x202E;", "&#xD800;", "&nbsp;", "&unknown;", "\x00", "\x07", "\x1b[2J", "\u202e", "\u2066", "\u200b",
    "\ufeff", "😀", "Impressum", "Angaben gemäß § 5 DDG", "Geschäftsführer:", "USt-IdNr.: DE123456788",
    "DE123456789", "HRB 1234", "Amtsgericht Köln", "80331 München", "Musterstraße 12", "Tel.: +49 89 1234567",
    "info (at) example (dot) com", "<script type=\"application/ld+json\">{\"@type\":\"Organization\",\"name\":\"<b>X</b>\"",
    "<script type=\"application/ld+json\">[1,2,{\"@type\":\"Organization\",\"vatID\":\"\\u202eDE1\"}]</script>",
    "<div itemscope itemtype=\"https://schema.org/Organization\"><span itemprop=\"name\">", "</span></div>",
    "<img src=x onerror=alert(1)>", "<<>>", "<a", "\"'", "\\", "A" * 500, " ",
]  # fmt: skip


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _strings(v)


@pytest.mark.parametrize("seed", range(40))
def test_random_garbage_never_raises_and_never_leaks_control_characters(seed):
    rnd = random.Random(seed)
    html = "".join(rnd.choice(FRAGMENTS) for _ in range(rnd.randint(1, 400)))
    page = parse_html(html)
    result = ImpressumService._merge("https://x.example/", None, [extract_fields(page)], [])
    for text in _strings(result):
        assert "<" not in text and ">" not in text
        assert all(not unicodedata.category(c).startswith("C") for c in text), repr(text)
        assert len(text) <= 500
    for value in result["fields"].values():
        assert isinstance(value, (str, list))


def test_pathological_inputs_are_fast_and_bounded():
    cases = {
        "deep nesting": "<div>" * 20000 + "Impressum" + "</div>" * 20000,
        "many unclosed tags": "<a href=x><td><tr><p>" * 20000,
        "huge line": "A" * 2_000_000,
        "many lines": "<br>".join("Musterstraße 12" for _ in range(100000)),
        "many links": "".join(f'<a href="/impressum{i}">Impressum</a>' for i in range(20000)),
        "quadratic regex bait": "1" * 50000 + " " + "a." * 20000,
    }
    for label, html in cases.items():
        started = time.monotonic()
        page = parse_html(html)
        extract_fields(page)
        find_impressum_links(page, "https://example.com/")
        assert time.monotonic() - started < 5, label
        assert len(page.lines) <= 4000
