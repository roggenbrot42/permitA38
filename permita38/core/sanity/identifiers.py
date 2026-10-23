"""Format and checksum validators for identifiers (IBAN, BIC, VAT ids, tax numbers).

Every validator returns ``None`` when the value looks right and a short, human-readable reason otherwise.
Empty values are *not* validated here: callers skip blank fields. They are plausibility checks, not an
authoritative lookup (VIES / the BZSt confirmation service stay the only way to confirm that a number is registered).
"""

import re

from permita38.core.impressum_parser import normalize_phone, validate_email, validate_vat_id_de

# ISO 13616 registry: country -> total IBAN length
IBAN_LENGTHS = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BR": 29, "BY": 28,
    "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24, "FI": 18,
    "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22,
    "IL": 23, "IQ": 23, "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20,
    "LU": 20, "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15,
    "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24, "SC": 31, "SE": 24, "SI": 19,
    "SK": 24, "SM": 27, "ST": 25, "SV": 28, "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}  # fmt: skip


def normalize_iban(value: str) -> str:
    return re.sub(r"[\s-]", "", value).upper()


def iban_error(value: str) -> str | None:
    iban = normalize_iban(value)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", iban):
        return "an IBAN starts with two letters and two digits, followed by letters and digits"
    expected = IBAN_LENGTHS.get(iban[:2])
    if expected is None:
        return f"{iban[:2]} is not a known IBAN country code"
    if len(iban) != expected:
        return f"an IBAN from {iban[:2]} has {expected} characters, this one has {len(iban)}"
    rearranged = iban[4:] + iban[:4]
    number = "".join(str(int(c, 36)) for c in rearranged)  # A=10 … Z=35
    if int(number) % 97 != 1:
        return "the check digits do not match (probably a typo)"
    return None


_BIC_RE = re.compile(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?")


def bic_error(value: str) -> str | None:
    bic = re.sub(r"\s", "", value).upper()
    if not _BIC_RE.fullmatch(bic):
        return "a BIC has 8 or 11 characters: 4 letters (bank), 2 letters (country), 2 characters (place), optional 3 (branch)"
    return None


# EU VAT identification numbers: prefix -> regex of the part after the prefix (format only, no check digits)
EU_VAT_FORMATS: dict[str, str] = {
    "AT": r"U\d{8}", "BE": r"[01]\d{9}", "BG": r"\d{9,10}", "CY": r"\d{8}[A-Z]", "CZ": r"\d{8,10}",
    "DK": r"\d{8}", "EE": r"\d{9}", "EL": r"\d{9}", "ES": r"[A-Z0-9]\d{7}[A-Z0-9]", "FI": r"\d{8}",
    "FR": r"[A-HJ-NP-Z0-9]{2}\d{9}", "HR": r"\d{11}", "HU": r"\d{8}", "IE": r"\d[A-Z0-9+*]\d{5}[A-Z]{1,2}",
    "IT": r"\d{11}", "LT": r"(?:\d{9}|\d{12})", "LU": r"\d{8}", "LV": r"\d{11}", "MT": r"\d{8}",
    "NL": r"\d{9}B\d{2}", "PL": r"\d{10}", "PT": r"\d{9}", "RO": r"\d{2,10}", "SE": r"\d{12}", "SI": r"\d{8}",
    "SK": r"\d{10}", "XI": r"(?:\d{9}|\d{12}|GD\d{3}|HA\d{3})",
}  # fmt: skip
# not in the EU VAT system (no VIES), but common on invoices
OTHER_VAT_FORMATS: dict[str, str] = {"GB": r"(?:\d{9}|\d{12}|GD\d{3}|HA\d{3})", "CH": r"E\d{9}(?:MWST|TVA|IVA)"}


def vat_id_error(value: str) -> str | None:
    raw = re.sub(r"[\s.\-]", "", value).upper()
    if not re.match(r"[A-Z]{2}", raw):
        return "a VAT ID starts with the two-letter country code, e.g. DE123456789"
    prefix, rest = raw[:2], raw[2:]
    if prefix == "DE":
        if not re.fullmatch(r"\d{9}", rest):
            return "a German USt-IdNr is DE followed by 9 digits"
        if validate_vat_id_de(raw) is None:
            return "the check digit of this German USt-IdNr is wrong (probably a typo)"
        return None
    pattern = EU_VAT_FORMATS.get(prefix) or OTHER_VAT_FORMATS.get(prefix)
    if pattern is None:
        return f"{prefix} is not a known VAT country prefix"
    if not re.fullmatch(pattern, rest):
        return f"this does not look like a valid {prefix} VAT ID (format check)"
    return None


def tax_number_error(value: str) -> str | None:
    """German Steuernummer, format only: 10, 11 or 13 digits, usually written with ``/`` or spaces."""
    if re.search(r"[^\d/\s\-.]", value):
        return "a tax number consists of digits and the separators / or space"
    digits = re.sub(r"\D", "", value)
    if len(digits) not in (10, 11, 13):
        return f"a German tax number has 10, 11 or 13 digits (this one has {len(digits)})"
    if len(set(digits)) == 1:
        return "that looks like a placeholder"
    return None


def zip_error(value: str, country: str | None = None) -> str | None:
    c = (country or "").strip().lower()
    if c in ("", "germany", "deutschland", "de"):
        return None if re.fullmatch(r"\d{5}", value.strip()) and value.strip() != "00000" else "a German ZIP code has 5 digits"
    return None  # other countries: not validated


def email_error(value: str) -> str | None:
    return None if validate_email(value) else "this does not look like an e-mail address"


def phone_error(value: str) -> str | None:
    return None if normalize_phone(value) else "a phone number has 6-15 digits"
