"""Amount plausibility: outliers, VAT consistency, duplicates, zero/negative documents."""

import hashlib
from datetime import timedelta
from statistics import median

from permita38.core import calc
from permita38.core.money import dec, line_amount, q2, vat_amount

from .base import Ctx, Finding, num, parse_date, rule


def expense_gross(e: dict) -> float:
    return (num(e.get("net"), 0.0) or 0.0) + (num(e.get("vat"), 0.0) or 0.0)


def _cents(x: float) -> int:
    return int(q2(dec(x)) * 100)


@rule(
    "expense.amount_outlier",
    "Expense far above your usual amount",
    "A typo with an extra zero (€4,500 instead of €45) would distort your profit and your VAT claim. "
    "Compared with your own earlier expenses in the same category.",
    "warning",
    "expenses",
    thresholds={"factor": 100, "min_samples": 5, "min_amount": 100},
    threshold_help={
        "factor": "warn when the amount is this many times your median",
        "min_samples": "needed earlier expenses in the category",
        "min_amount": "ignore amounts below this (EUR)",
    },
)
def expense_outlier(ctx: Ctx):
    r = ctx.record
    gross = expense_gross(r)
    cat = r.get("category")
    if not cat or gross < ctx.threshold("min_amount"):
        return
    if not any(ctx.changed(k) for k in ("net", "vat", "category")):
        return
    hist = [g for e in ctx.others("expenses") if e.get("category") == cat and (g := expense_gross(e)) > 0]
    if len(hist) < ctx.threshold("min_samples"):
        return
    med = median(hist)
    if med > 0 and gross > ctx.threshold("factor") * med:
        yield Finding(
            f"{gross:,.2f} EUR is {gross / med:,.0f}× your usual {med:,.2f} EUR for '{cat}'.",
            "net",
            {"amount": gross, "median": med, "samples": len(hist)},
        )


@rule(
    "expense.amount_round",
    "Suspiciously round expense",
    "Large exact thousands are often placeholders or estimates rather than the amount on the receipt.",
    "info",
    "expenses",
    thresholds={"min_amount": 5000},
    threshold_help={"min_amount": "only check amounts from this value (EUR)"},
)
def expense_round(ctx: Ctx):
    gross = expense_gross(ctx.record)
    if gross >= ctx.threshold("min_amount") and _cents(gross) % 100_000 == 0 and ctx.changed("net"):
        yield Finding(f"{gross:,.0f} EUR is an exact multiple of 1,000. Is that the amount on the receipt?", "net")


@rule(
    "expense.vat_inconsistent",
    "VAT does not match net × rate",
    "The VAT you reclaim must equal the net amount times the VAT rate (rounded to the cent). A difference usually "
    "means a mistyped amount or a wrong rate.",
    "warning",
    "expenses",
)
def expense_vat(ctx: Ctx):
    r = ctx.record
    rate = num(r.get("vat_rate"))
    net = num(r.get("net"), 0.0) or 0.0
    vat = num(r.get("vat"), 0.0) or 0.0
    if rate is None or net <= 0 or not any(ctx.changed(k) for k in ("net", "vat", "vat_rate")):
        return
    expected = float(vat_amount(dec(net), rate))
    if abs(vat - expected) > 0.011:
        yield Finding(
            f"{rate:g} % of {net:,.2f} EUR is {expected:,.2f} EUR, but the VAT is {vat:,.2f} EUR.",
            "vat",
            {"expected": expected, "entered": vat},
        )


@rule(
    "expense.gross_mismatch",
    "Net + VAT does not equal the gross amount",
    "The amount on the receipt (gross) must equal net plus VAT.",
    "warning",
    "expenses",
)
def expense_gross_mismatch(ctx: Ctx):
    r = ctx.record
    gross = num(r.get("gross"))
    if gross is None:
        return
    total = expense_gross(r)
    if abs(total - gross) > 0.011:
        yield Finding(f"Net + VAT is {total:,.2f} EUR but the gross amount is {gross:,.2f} EUR.", "gross")


@rule(
    "expense.future_date",
    "Expense dated in the future",
    "You cannot have paid a bill that is dated after today; the date is probably wrong.",
    "warning",
    "expenses",
)
def expense_future(ctx: Ctx):
    d = parse_date(ctx.record.get("date"))
    if d and d > ctx.today and ctx.changed("date"):
        yield Finding(f"{d.isoformat()} is in the future.", "date")


def _file_hash(ctx: Ctx, name: str) -> str | None:
    key = ("filehash", name)
    if key not in ctx._cache:
        path = ctx.store.attachment_path(name)
        digest = None
        if path:
            try:
                with open(path, "rb") as f:
                    digest = hashlib.sha256(f.read(20_000_000)).hexdigest()
            except OSError:
                digest = None
        ctx._cache[key] = digest
    return ctx._cache[key]


@rule(
    "expense.duplicate",
    "Possible duplicate expense",
    "The same bill entered twice halves your profit by mistake and claims the input VAT twice. Same vendor, same "
    "amount within a few days, or the same receipt file.",
    "warning",
    "expenses",
    thresholds={"days": 3, "max_files": 300},
    threshold_help={"days": "date window for 'same vendor and amount'", "max_files": "receipt files hashed per check"},
)
def expense_duplicate(ctx: Ctx):
    r = ctx.record
    vendor = (r.get("vendor") or "").strip().casefold()
    cents = _cents(expense_gross(r))
    day = parse_date(r.get("date"))
    window = timedelta(days=ctx.threshold("days"))
    my_hash = _file_hash(ctx, r["attachment"]) if r.get("attachment") else None
    hashed = 0
    for e in ctx.others("expenses"):
        if vendor and cents and (e.get("vendor") or "").strip().casefold() == vendor and _cents(expense_gross(e)) == cents:
            other = parse_date(e.get("date"))
            if day and other and abs(day - other) <= window:
                yield Finding(
                    f"'{r.get('vendor')}' {cents / 100:,.2f} EUR on {e.get('date')} is already recorded.",
                    "vendor",
                    {"duplicate_of": e.get("id")},
                )
                return
        if my_hash and e.get("attachment") and hashed < ctx.threshold("max_files"):
            hashed += 1
            if e["attachment"] == r["attachment"] or _file_hash(ctx, e["attachment"]) == my_hash:
                yield Finding(
                    "This receipt file is already attached to another expense.", "attachment", {"duplicate_of": e.get("id")}
                )
                return


# ---------------------------------------------------------------------------------------------- documents
def doc_net(ctx: Ctx, doc: dict) -> float:
    key = ("net", doc.get("id"), doc.get("updated"), id(doc) if not doc.get("id") else 0)
    if key not in ctx._cache:
        copy = {**doc, "lines": [dict(x) for x in doc.get("lines") or [] if isinstance(x, dict)]}
        ctx._cache[key] = calc.doc_totals(copy, ctx.settings)["net"]
    return ctx._cache[key]


def _same_customer_docs(ctx: Ctx) -> list[dict]:
    cid = ctx.record.get("customer_id")
    return [d for d in ctx.others(ctx.coll) if cid and d.get("customer_id") == cid and d.get("lines")]


@rule(
    "document.line_outlier",
    "Line far above your usual for this customer",
    "A line amount 100× larger than your earlier lines for this customer is most often a decimal or quantity typo.",
    "warning",
    ("invoices", "offers"),
    thresholds={"factor": 100, "min_samples": 5, "min_amount": 100},
    threshold_help={
        "factor": "warn when a line is this many times the median line",
        "min_samples": "needed earlier lines for this customer",
        "min_amount": "ignore lines below this (EUR)",
    },
)
def line_outlier(ctx: Ctx):
    if not ctx.changed("lines"):
        return
    hist = []
    for d in _same_customer_docs(ctx):
        for ln in d.get("lines") or []:
            if isinstance(ln, dict):
                a = abs(float(line_amount(ln.get("qty"), ln.get("price"))))
                if a > 0:
                    hist.append(a)
    if len(hist) < ctx.threshold("min_samples"):
        return
    med = median(hist)
    for i, ln in enumerate(ctx.record.get("lines") or []):
        if not isinstance(ln, dict):
            continue
        a = abs(float(line_amount(ln.get("qty"), ln.get("price"))))
        if a >= ctx.threshold("min_amount") and med > 0 and a > ctx.threshold("factor") * med:
            yield Finding(f"Line {i + 1}: {a:,.2f} EUR is {a / med:,.0f}× the usual {med:,.2f} EUR.", f"lines[{i}]")


@rule(
    "document.total_outlier",
    "Total far above your usual for this customer",
    "A document more than 10× your usual size for the customer deserves a second look before it is sent.",
    "warning",
    ("invoices", "offers"),
    thresholds={"factor": 10, "min_samples": 3, "min_amount": 500},
    threshold_help={
        "factor": "warn when the net total is this many times the median",
        "min_samples": "needed earlier documents for this customer",
        "min_amount": "ignore totals below this (EUR)",
    },
)
def total_outlier(ctx: Ctx):
    if not ctx.changed("lines"):
        return
    hist = [n for d in _same_customer_docs(ctx) if (n := doc_net(ctx, d)) > 0]
    if len(hist) < ctx.threshold("min_samples"):
        return
    med = median(hist)
    total = doc_net(ctx, ctx.record)
    if total >= ctx.threshold("min_amount") and med > 0 and total > ctx.threshold("factor") * med:
        yield Finding(f"Net total {total:,.2f} EUR is {total / med:,.1f}× the usual {med:,.2f} EUR.", "lines")


@rule(
    "document.negative_total",
    "Negative total",
    "A document with a negative total is a credit note. Make sure that is intended and that it refers to the original invoice.",
    "warning",
    ("invoices", "offers"),
)
def negative_total(ctx: Ctx):
    if ctx.record.get("lines") and doc_net(ctx, ctx.record) < 0 and ctx.changed("lines"):
        yield Finding("The net total is negative (credit note).", "lines")


@rule(
    "document.zero_price_line",
    "Line without a price",
    "A described line with a quantity but a price of 0 is usually a forgotten price.",
    "warning",
    ("invoices", "offers"),
)
def zero_price(ctx: Ctx):
    if not ctx.changed("lines"):
        return
    for i, ln in enumerate(ctx.record.get("lines") or []):
        if isinstance(ln, dict) and (num(ln.get("price"), 0.0) or 0.0) == 0 and (
            (num(ln.get("qty"), 0.0) or 0.0) != 0 and (ln.get("description") or "").strip()
        ):
            yield Finding(f"Line {i + 1} has a price of 0.", f"lines[{i}]")


@rule(
    "document.duplicate_number",
    "Duplicate document number",
    "Invoice numbers must be unique and gap-free (§14 UStG). Two documents with the same number cannot both be right.",
    "critical",
    ("invoices", "offers"),
    confirmable=False,
)
def duplicate_number(ctx: Ctx):
    number = ctx.record.get("number")
    if number and any(d.get("number") == number for d in ctx.others(ctx.coll)):
        yield Finding(f"Number {number} is used more than once.", "number")
