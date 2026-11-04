"""Helpers shared by the document import and the scan of old documents (docs/specs/document-scan.md)."""

import hashlib
import json
import re
from difflib import SequenceMatcher
from typing import Any, Callable

from permita38.core.repository import Repository
from permita38.core.service.numbering import NumberingService

from . import values as V

FUZZY_MATCH = 0.9
#: Extension point for the accounting module (not implemented yet): every hook is called inside the import
#: transaction with (collection, saved record) for each imported document or expense, and must raise to abort it.
#: Hooks are registered at start-up: ``importer.service.IMPORT_HOOKS.append(fn)``.
IMPORT_HOOKS: list[Callable[[str, dict], None]] = []


LEGAL_FORMS = {"gmbh", "ag", "kg", "ug", "ohg", "gbr", "mbh", "co", "e", "k", "se", "ev"}
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,20}$")


def text_or_none(value, limit: int, what: str) -> str | None:
    return V.text(value, limit, what)


def normalized_name(name: str, strip_legal: bool = True) -> str:
    words = re.sub(r"[^a-z0-9äöüß]+", " ", (name or "").casefold()).split()
    while strip_legal and words and words[-1] in LEGAL_FORMS:
        words.pop()
    return " ".join(words)


def customer_key(rec: dict) -> str:
    return (
        rec.get("vat_id") or rec.get("tax_number") or rec.get("email") or rec.get("customer_no") or rec.get("name", "")
    )


def customer_record(values: dict, required: bool = False) -> dict:
    name = text_or_none(values.get("name", values.get("customer_name")), 200, "customer name")
    rec: dict[str, Any] = {
        "name": name,
        "customer_no": text_or_none(values.get("customer_no"), 60, "customer number"),
        "vat_id": (text_or_none(values.get("vat_id"), 30, "VAT ID") or "").replace(" ", "").upper() or None,
        "tax_number": text_or_none(values.get("tax_number"), 30, "tax number"),
        "email": (text_or_none(values.get("email"), 254, "email") or "").lower() or None,
        "phone": text_or_none(values.get("phone"), 60, "phone"),
        "street": text_or_none(values.get("street"), 200, "street"),
        "zip": text_or_none(values.get("zip"), 20, "postal code"),
        "city": text_or_none(values.get("city"), 200, "city"),
        "country": text_or_none(values.get("country"), 100, "country"),
        "contact": text_or_none(values.get("contact"), 200, "contact"),
        "notes": text_or_none(values.get("notes"), 5000, "notes"),
    }
    if rec["email"] and not _EMAIL.match(rec["email"]):
        raise V.CellError("email is not a valid address")
    rec = {k: v for k, v in rec.items() if v is not None}
    if required and not any(rec.get(k) for k in ("name", "customer_no", "vat_id", "tax_number", "email")):
        raise V.CellError("customer is missing")
    if required and not rec.get("name"):
        rec.setdefault("name", rec.get("customer_no") or rec.get("vat_id") or rec.get("email") or "Imported customer")
    if not required and not rec.get("name"):
        raise V.CellError("customer name is missing")
    return rec


class CustomerIndex:
    """Matches customers by VAT ID, tax number, email, customer number, then normalised name (exact or >= 0.9)."""

    def __init__(self, existing: list[dict], merge_similar: bool = False):
        self.merge_similar = merge_similar
        self.items: list[dict] = []
        for c in existing:
            self.add(c)

    def add(self, rec: dict) -> None:
        self.items.append(rec)

    def _key_match(self, rec: dict) -> dict | None:
        for key in ("vat_id", "tax_number", "email", "customer_no"):
            if rec.get(key):
                for c in self.items:
                    if c.get(key) and str(c[key]).lower() == str(rec[key]).lower():
                        return c
        return None

    def _name_scores(self, rec: dict) -> list[tuple[float, dict]]:
        """(score, customer) for every customer with a comparable name."""
        name = normalized_name(rec.get("name", ""))
        if not name:
            return []
        out: list[tuple[float, dict]] = []
        exact_name = normalized_name(rec.get("name", ""), strip_legal=False)
        for c in self.items:
            other = normalized_name(c.get("name", ""))
            if not other:
                continue
            if normalized_name(c.get("name", ""), strip_legal=False) == exact_name:
                score = 1.0
            elif other == name:
                score = 0.95  # same name, different legal form (GmbH vs AG): similar, never exact
            elif re.findall(r"\d+", name) != re.findall(r"\d+", other):
                continue  # "Schmidt Bau 1" and "Schmidt Bau 2" are different customers
            else:
                score = SequenceMatcher(None, name, other).ratio()
            out.append((score, c))
        return out

    def find(self, rec: dict) -> tuple[dict | None, float]:
        hit = self._key_match(rec)
        if hit is not None:
            return hit, 1.0
        best, best_score = None, 0.0
        for score, c in self._name_scores(rec):
            if score > best_score:
                best, best_score = c, score
        if best is not None and (best_score == 1.0 or (best_score >= FUZZY_MATCH and self.merge_similar)):
            return best, round(best_score, 3)
        return None, round(best_score, 3)

    def similar(self, rec: dict) -> list[dict]:
        """Customers a scanned recipient may be: the one found by an identifier, else every customer whose name scores
        >= FUZZY_MATCH. Two or more of them are ambiguous."""
        hit = self._key_match(rec)
        if hit is not None:
            return [hit]
        return [c for score, c in self._name_scores(rec) if score >= FUZZY_MATCH]

    def match(self, rec: dict) -> tuple[list[dict], bool]:
        """``(candidates, certain)`` for a scanned recipient. Certain (safe to link without asking) is an identifier match
        or exactly one customer with the same normalised name *including* the legal form. Otherwise the candidates are
        the customers with a similar name (same name with another legal form, or >= FUZZY_MATCH), possibly none."""
        hit = self._key_match(rec)
        if hit is not None:
            return [hit], True
        scores = self._name_scores(rec)
        exact = [c for score, c in scores if score == 1.0]
        if len(exact) == 1:
            return exact, True
        return exact or [c for score, c in scores if score >= FUZZY_MATCH], False


def digest(record: dict) -> str:
    """Fingerprint of a whole record: detects any change after the import (timestamps have 1 s resolution)."""
    return hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def save_document(
    store: Repository, numbering: NumberingService, coll: str, record: dict, batch_id: str, hooks: list | None = None
) -> dict:
    """Write one imported offer or invoice inside the caller's transaction: the batch id, the import hooks, and the
    counter that follows the original number. Returns the saved record."""
    saved = store.save(coll, {**record, "import_batch": batch_id})
    for hook in IMPORT_HOOKS if hooks is None else hooks:
        hook(coll, saved)
    day = V.parse_date(record.get("date"))
    if record.get("number") and day:
        numbering.observe_imported(coll, record["number"], day)
    return saved


def save_batch(
    store: Repository, batch_id: str, kind: str, filename: str, counts: dict, records: list, **extra: Any
) -> dict:
    """The ``import_batches`` row of one run; ``records`` are ``[collection, id, digest]`` so the undo can check them."""
    return store.save(
        "import_batches",
        dict(
            id=batch_id,
            kind=kind,
            filename=filename[:200],
            status="imported",
            counts=counts,
            finished=0,
            records=records,
            overwrites=0,
            overwritten=[],
            **extra,
        ),
    )
