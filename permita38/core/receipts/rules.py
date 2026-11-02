"""From a reading to a draft, the checks and the flags (docs/specs/receipt-scan.md). Pure functions, no I/O.

``evaluate`` turns what a reader found into ``Evaluation``: the flags (a closed set, ``flags.py``), the draft expense in
``ExpenseIn`` shape with ``gross``, the ``doc`` kept with the scan (checks, boxes, items, payment) and the key used to
detect a second file of the same bill. A money value is only ever compared with printed figures: the draft carries the
printed gross per rate, and net and VAT are left to ``money.split_gross`` and ``calc.itemise_expense``.
"""

import copy
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from ..money import q2, to_float
from .categories import DEFAULT_CATEGORY, category_from_text
from .extraction import Extraction, ReadResult, Word
from .flags import ordered
from .reconcile import Check, reconcile, vat_groups
from .textrules import clean_text

NO_NUMBER_LIMIT = Decimal("250.00")  # gross; above it a bill without an invoice number is flagged
USUAL_RATES = (Decimal(0), Decimal(7), Decimal(19))
MAX_ITEMS = 200
MAX_BOXES = 300
MEAL = "Business meals"

BOX_FIELDS = {
    "vendor": "vendor", "date": "date", "bill_date": "date", "invoice_no": "invoice_no", "vat_id": "vat_id",
    "subtotal": "subtotal", "total": "total", "due_date": "due_date", "paid_date": "paid_date",
    "tendered": "tendered", "change": "change", "net_total": "net_total", "vat_total": "vat_total", "qr": "qr",
}  # fmt: skip


@dataclass
class Context:
    today: date
    category_of_vendor: Callable[[str], str | None]
    possible_duplicate: Callable[[dict], bool]
    # "hard", "soft" or None: the same invoice number as a known bill (service ``_clash``); "soft" only raises a flag
    number_clash: Callable[[dict], str | None] = field(default=lambda _doc: None)


@dataclass
class Evaluation:
    flags: list[dict] = field(default_factory=list)
    draft: dict = field(default_factory=dict)
    doc: dict = field(default_factory=dict)
    invoice_key: str | None = None
    clean: bool = False


def payment_kind(x: Extraction) -> str:
    """``conflict``, ``stated``, ``cash``, ``invoice`` or ``unknown`` (docs/specs/receipt-scan.md, "Payment kind")."""
    due_later = bool(x.due_date and x.bill_date and x.due_date > x.bill_date)
    paid_in_full = (
        bool(x.einvoice_sums) and x.prepaid is not None and x.prepaid > 0 and x.prepaid == x.total
    )  # BT-113 = BT-112
    real_prepaid = x.prepaid is not None and x.prepaid > 0 and not paid_in_full  # a prepaid amount of 0 is none
    open_ = (
        "open" in x.payment_signals or due_later or x.amount_due is not None and not real_prepaid
    ) and not paid_in_full
    meal = "meal" in x.text_signals and not open_
    cash = "cash" in x.payment_signals or "card" in x.payment_signals or meal
    prepaid = "prepaid" in x.payment_signals or real_prepaid
    if (cash and open_) or prepaid:
        return "conflict"
    if x.paid_date is not None or "paid" in x.payment_signals:
        return "stated"
    if cash:
        return "cash"
    if open_:
        return "invoice"
    return "unknown"


def _clean(x: Extraction) -> Extraction:
    out = copy.deepcopy(x)
    out.vendor = clean_text(out.vendor)
    out.invoice_no = clean_text(out.invoice_no)
    out.vat_id = clean_text(out.vat_id)
    out.currency = clean_text(out.currency)
    for item in out.items:
        item.description = clean_text(item.description) or ""
        item.vat_code = clean_text(item.vat_code)
    return out


def _money(value: Decimal | None) -> float | None:
    return None if value is None else to_float(value)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(q2(value))


def _rate_float(rate: Decimal) -> float:
    return float(rate)


def _rate_label(rate: Decimal) -> str:
    return format(rate.normalize(), "f") if rate != rate.to_integral_value() else str(int(rate))


def _category(x: Extraction, ctx: Context) -> str:
    if x.vendor:
        history = ctx.category_of_vendor(x.vendor)
        if history:
            return history
    items = " ".join(i.description for i in x.items)
    if any(a.kind == "tip" for a in x.adjustments):
        items += " Trinkgeld"
    if "parking" in x.text_signals:
        return "Transportation"
    found = category_from_text(x.vendor or "", items)
    if found is None and "software" in x.text_signals:
        return "Software & subscriptions"
    if found:
        return found
    if "transport" in x.text_signals:
        return "Transportation"
    return MEAL if "meal" in x.text_signals else DEFAULT_CATEGORY


def _box_value(x: Extraction, key: str) -> str | None:
    """The value read for a source key, as fixed-format text: ISO dates, amounts with two decimals."""
    simple: dict[str, Any] = {
        "vendor": x.vendor, "date": x.bill_date, "invoice_no": x.invoice_no, "vat_id": x.vat_id,
        "due_date": x.due_date, "paid_date": x.paid_date, "subtotal": x.subtotal, "total": x.total,
        "tendered": x.tendered, "change": x.change, "net_total": x.net_total, "vat_total": x.vat_total,
    }  # fmt: skip
    value: Any = simple.get(key)
    base, _, index = key.partition(":")
    try:
        if base == "item":
            value = x.items[int(index)].amount
        elif base == "vat_group":
            g = vat_groups(x)[int(index)]
            value = g.gross if g.gross is not None else g.group.vat
    except (IndexError, ValueError):
        value = None
    if value is None:
        return None
    return _text(value) if isinstance(value, Decimal) else clean_text(str(value))


def _boxes(x: Extraction, checks: list[Check]) -> list[dict]:
    status: dict[str, str] = {}
    for c in checks:
        for ref in c.refs:
            if not c.ok:
                status[ref] = "failed"
            elif status.get(ref) != "failed":
                status[ref] = "ok"
    out: list[dict] = []
    for key, words in x.sources.items():
        base = key.split(":")[0]
        field_name = BOX_FIELDS.get(key) or (base if base in ("item", "vat_group") and ":" in key else None)
        if field_name is None or not words:
            continue
        if base == "item" and int(key.split(":")[1]) >= MAX_ITEMS:
            continue
        by_page: dict[int, list[Word]] = {}
        for w in words:
            by_page.setdefault(w.page, []).append(w)
        for page, ws in sorted(by_page.items()):
            x0 = max(0.0, min(1.0, min(w.x for w in ws)))
            y0 = max(0.0, min(1.0, min(w.y for w in ws)))
            x1 = max(0.0, min(1.0, max(w.x + w.w for w in ws)))
            y1 = max(0.0, min(1.0, max(w.y + w.h for w in ws)))
            out.append(
                {
                    "field": field_name,
                    "page": page,
                    "x": round(x0, 4),
                    "y": round(y0, 4),
                    "w": round(x1 - x0, 4),
                    "h": round(y1 - y0, 4),
                    "check": status.get(key, "none"),
                    "value": _box_value(x, key),
                }
            )
    return out[:MAX_BOXES]


def _draft(x: Extraction, ctx: Context, kind: str) -> dict:
    groups = vat_groups(x)
    meal = False
    category = _category(x, ctx) if x.vendor or x.items or x.total is not None else None
    meal = category == MEAL
    paid_on: str | None = None
    if kind == "stated":
        paid_on = x.paid_date or x.bill_date
    elif kind == "cash":
        paid_on = x.bill_date
    draft: dict[str, Any] = {
        "date": x.bill_date,
        "paid_on": paid_on,
        "invoice_no": x.invoice_no,
        "vendor": x.vendor,
        "gross": _money(x.total),
        "vat_rate": None,
        "category": category,
        "deductible": 70 if meal else 100,
        "vat_deductible": 100 if meal else None,
    }
    rated = [g for g in groups if g.group.rate is not None]
    tip = sum((a.amount for a in x.adjustments if a.kind == "tip"), Decimal(0))
    if tip > 0 and groups and all(g.gross is not None and g.group.rate is not None for g in groups):
        # a tip was paid: the booked gross must equal the total, so it is one more line at 0 % ("Tips are booked")
        draft["lines"] = [_line(g, draft) for g in groups] + [
            {
                "description": "Tip",
                "gross": _money(tip),
                "vat_rate": 0.0,
                "deductible": draft["deductible"],
                "vat_deductible": draft["vat_deductible"],
            }
        ]
        if len(groups) == 1:
            draft["vat_rate"] = _rate_float(rated[0].group.rate)  # type: ignore[arg-type]
    elif len(groups) == 1 and rated:
        draft["vat_rate"] = _rate_float(rated[0].group.rate)  # type: ignore[arg-type]
    elif len(groups) > 1 and all(g.gross is not None and g.group.rate is not None for g in groups):
        draft["lines"] = [_line(g, draft) for g in groups]
    return draft


def _line(g: Any, draft: dict) -> dict:
    return {
        "description": f"VAT {_rate_label(g.group.rate)} %",
        "gross": _money(g.gross),
        "vat_rate": _rate_float(g.group.rate),
        "deductible": draft["deductible"],
        "vat_deductible": draft["vat_deductible"],
    }


def norm_name(text: Any) -> str:
    """Case, spaces and punctuation removed: "Vi Vadi GmbH." and "vi  vadi gmbh" are one name."""
    return "".join(ch for ch in str(text or "").casefold() if ch.isalnum())


def _hash_key(ident: str, number: str) -> str | None:
    return hashlib.sha256(f"{ident}\n{number}".encode()).hexdigest() if ident and number else None


def _invoice_key(x: Extraction) -> str | None:
    """The key in the indexed column: VAT id, else vendor, plus the invoice number."""
    return _hash_key((x.vat_id or x.vendor or "").strip().casefold(), (x.invoice_no or "").strip().casefold())


def invoice_keys(vat_id: Any, vendor: Any, invoice_no: Any) -> list[str]:
    """Every key under which this bill is found again: by VAT id and by vendor (as read and normalised), so that
    a reading with and one without the VAT id still meet. First is the key of the indexed column."""
    number = str(invoice_no or "").strip().casefold()
    idents = [
        str(vat_id or vendor or "").strip().casefold(),
        str(vat_id or "").strip().casefold(),
        norm_name(vat_id),
        str(vendor or "").strip().casefold(),
        norm_name(vendor),
    ]
    out: list[str] = []
    for ident in idents:
        key = _hash_key(ident, number)
        if key and key not in out:
            out.append(key)
    return out


def vat_keys(vat_id: Any, invoice_no: Any) -> list[str]:
    """The keys that rest on the VAT id alone (the only ones that identify a supplier beyond doubt)."""
    number = str(invoice_no or "").strip().casefold()
    out: list[str] = []
    for ident in (str(vat_id or "").strip().casefold(), norm_name(vat_id)):
        key = _hash_key(ident, number)
        if key and key not in out:
            out.append(key)
    return out


def evaluate(read: ReadResult, x: Extraction, ctx: Context) -> Evaluation:
    x = _clean(x)
    codes: set[str] = set()
    empty = not (x.vendor or x.total is not None or x.bill_date or x.items or x.vat_groups or x.competing_totals)
    if read.engine == "none" or read.error or empty:
        return Evaluation(flags=ordered({"unreadable"}), draft={}, doc={"checks": [], "boxes": [], "items": []})

    if read.pages_total > read.pages_read:
        codes.add("pages_skipped")
    if x.total is None:
        codes.add("ambiguous_total" if x.competing_totals else "no_total")
    if x.bill_date is None:
        codes.add("ambiguous_date" if x.competing_dates else "no_date")
    elif x.bill_date > ctx.today.isoformat() or x.bill_date < date(ctx.today.year - 1, 1, 1).isoformat():
        codes.add("date_implausible")
    if x.paid_date and (x.paid_date > ctx.today.isoformat() or (x.bill_date and x.paid_date < x.bill_date)):
        codes.add("date_implausible")
    if not x.vendor:
        codes.add("no_vendor")
    if x.total is not None and x.total > NO_NUMBER_LIMIT and not x.vat_id and not x.tax_no:
        codes.add("no_supplier_tax_id")  # § 14 UStG, verify: the supplier must be identifiable above 250 EUR
    if not x.invoice_no and (x.total is None or x.total > NO_NUMBER_LIMIT):
        codes.add("no_invoice_number")
    if not any(g.rate is not None for g in x.vat_groups) and "small_business" not in x.text_signals:
        codes.add("no_vat")
    if any(g.rate is not None and g.rate not in USUAL_RATES for g in x.vat_groups):
        codes.add("vat_rate_unusual")

    checks = reconcile(x)
    for c in checks:
        if not c.ok and c.flag:
            codes.add(c.flag)
    if x.total is not None and not any(c.ok and c.independent and c.covers for c in checks):
        codes.add("not_verifiable")

    kind = payment_kind(x)
    if kind == "conflict":
        codes.add("payment_unclear")
    if x.currency and x.currency.upper() != "EUR":
        codes.add("foreign_currency")
    if "credit_note" in x.text_signals or x.doc_type == "credit_note" or (x.total is not None and x.total < 0):
        codes.add("credit_note")
    if "reverse_charge" in x.text_signals:
        codes.add("reverse_charge")
    if read.engine == "vision":
        codes.add("model_read")

    draft = _draft(x, ctx, kind)
    if x.vat_groups and not draft.get("lines") and draft.get("vat_rate") is None:
        # a gross would be booked as VAT-free although the bill shows VAT groups: only a shown 0 % or a § 19 note allows it
        shown_zero = all(g.rate == 0 for g in x.vat_groups)  # one group above 0 % or without a rate is not VAT-free
        if not shown_zero and "small_business" not in x.text_signals:
            codes.add("no_vat" if any(g.rate is None for g in x.vat_groups) else "not_verifiable")
    if x.total is not None and draft.get("lines"):
        booked = sum((Decimal(str(ln["gross"])) for ln in draft["lines"]), Decimal(0))
        if q2(booked) != q2(x.total):
            codes.add("total_mismatch")  # the booked gross must equal the printed total
    tse_id = x.qr.tse_id if x.qr is not None and x.qr.kind == "tse" else None
    # a number from OCR cannot prove that two bills differ; one made of the TSE id is exact
    number_ocr = bool(x.invoice_no) and read.engine in ("ocr", "vision") and not (x.qr and x.invoice_no == x.qr.number)
    if x.vendor and x.bill_date and x.total is not None:
        probe = {
            "vendor": x.vendor,
            "date": x.bill_date,
            "gross": _money(x.total),
            "invoice_no": x.invoice_no,
            "invoice_ocr": number_ocr,
        }
        if ctx.possible_duplicate(probe):
            codes.add("possible_duplicate")

    if x.invoice_no and ctx.number_clash(
        {
            "invoice_keys": invoice_keys(x.vat_id, x.vendor, x.invoice_no),
            "vat_keys": vat_keys(x.vat_id, x.invoice_no),
            "tse_id": tse_id,
            "number_ocr": number_ocr,
            "number_weak": x.number_weak,
            "credit_note": "credit_note" in codes,
            "bill_date": x.bill_date,
        }
    ):
        codes.add("possible_duplicate")  # the same number as a bill we cannot call identical: the user decides

    printed_vat = [g.vat for g in x.vat_groups if g.vat is not None]
    doc = {
        "vat_id": x.vat_id,
        "tse_id": tse_id,
        "invoice_keys": invoice_keys(x.vat_id, x.vendor, x.invoice_no),
        "vat_keys": vat_keys(x.vat_id, x.invoice_no),
        "credit_note": "credit_note" in codes,
        "bill_date": x.bill_date,
        "number_ocr": number_ocr,
        "number_weak": x.number_weak,
        "due_date": x.due_date,
        "currency": x.currency,
        "printed_total": _text(x.total),
        "printed_vat": _text(sum(printed_vat, Decimal(0)))
        if printed_vat and len(printed_vat) == len(x.vat_groups)
        else None,
        "prepaid": _text(x.prepaid),
        "amount_due": _text(x.amount_due),
        "card_amount": _text(x.card_amount),
        "payment": {"kind": kind},
        "items": [
            {
                "description": i.description,
                "quantity": None if i.quantity is None else str(i.quantity),
                "unit_price": _text(i.unit_price),
                "amount": _text(i.amount),
                "vat_code": i.vat_code,
            }
            for i in x.items[:MAX_ITEMS]
        ],
        "checks": [
            {
                "name": c.name,
                "expected": str(c.expected),
                "found": str(c.found),
                "ok": c.ok,
                "independent": c.independent,
            }
            for c in checks
        ],
        "boxes": _boxes(x, checks),
        "pages": read.pages_total or read.pages_read,
    }
    flags = ordered(codes)
    return Evaluation(flags=flags, draft=draft, doc=doc, invoice_key=_invoice_key(x), clean=not flags)
