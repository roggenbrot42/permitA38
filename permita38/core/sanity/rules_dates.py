"""Date plausibility for documents, entries, expenses, contracts and absences."""

from datetime import timedelta

from .base import Ctx, Finding, parse_date, rule


@rule(
    "document.date_far_future",
    "Document dated far in the future",
    "An invoice date in the future makes the document invalid for the period you want to book it in and breaks the "
    "numbering order.",
    "warning",
    ("invoices", "offers"),
    thresholds={"days": 31},
    threshold_help={"days": "days after today that are still accepted"},
)
def date_far_future(ctx: Ctx):
    d = parse_date(ctx.record.get("date"))
    if d and ctx.changed("date") and d > ctx.today + timedelta(days=ctx.threshold("days")):
        yield Finding(f"{d.isoformat()} is {(d - ctx.today).days} days in the future.", "date")


@rule(
    "document.date_far_past",
    "Document dated far in the past",
    "Back-dating a document by more than a year is a red flag in an audit and may fall into an already filed VAT period.",
    "warning",
    ("invoices", "offers"),
    thresholds={"days": 400},
    threshold_help={"days": "days before today that are still accepted"},
)
def date_far_past(ctx: Ctx):
    d = parse_date(ctx.record.get("date"))
    if d and ctx.changed("date") and d < ctx.today - timedelta(days=ctx.threshold("days")):
        yield Finding(f"{d.isoformat()} is {(ctx.today - d).days} days ago.", "date")


@rule(
    "invoice.due_before_date",
    "Due date before the invoice date",
    "The customer cannot be asked to pay before the invoice exists.",
    "warning",
    "invoices",
)
def due_before(ctx: Ctx):
    r = ctx.record
    d, due = parse_date(r.get("date")), parse_date(r.get("due_date"))
    if d and due and due < d:
        yield Finding(f"Due date {due.isoformat()} is before the invoice date {d.isoformat()}.", "due_date")


@rule(
    "invoice.paid_before_date",
    "Paid before the invoice date",
    "A payment cannot arrive before the invoice was written. The date decides in which month the revenue counts "
    "(cash-basis accounting).",
    "warning",
    "invoices",
)
def paid_before(ctx: Ctx):
    r = ctx.record
    d, paid = parse_date(r.get("date")), parse_date(r.get("paid_date"))
    if d and paid and paid < d:
        yield Finding(f"Paid on {paid.isoformat()}, but the invoice is dated {d.isoformat()}.", "paid_date")


@rule(
    "offer.valid_before_date",
    "Offer expires before its own date",
    "An offer that is already expired when it is written is a mistake.",
    "warning",
    "offers",
)
def valid_before(ctx: Ctx):
    r = ctx.record
    d, valid = parse_date(r.get("date")), parse_date(r.get("valid_until"))
    if d and valid and valid < d:
        yield Finding(f"Valid until {valid.isoformat()} is before the offer date {d.isoformat()}.", "valid_until")


@rule(
    "invoice.service_after_invoice_date",
    "Service period starts after the invoice date",
    "Billing a service that has not started yet is an advance invoice (Anzahlung/Vorauszahlung); make sure it is "
    "labelled as such.",
    "info",
    "invoices",
)
def service_after(ctx: Ctx):
    r = ctx.record
    d, s = parse_date(r.get("date")), parse_date(r.get("service_from"))
    if d and s and s > d:
        yield Finding(f"The service starts on {s.isoformat()}, after the invoice date {d.isoformat()}.", "service_from")


@rule(
    "entry.old_period",
    "Time entry for an old period",
    "Entries older than about three months are often already invoiced or reported. A late entry changes numbers "
    "you may have sent to a customer.",
    "warning",
    "entries",
    thresholds={"days": 90},
    threshold_help={"days": "age in days that counts as 'old'"},
    stored_ok=False,
)
def entry_old(ctx: Ctx):
    d = parse_date(ctx.record.get("date"))
    if d and ctx.changed("date") and d < ctx.today - timedelta(days=ctx.threshold("days")):
        yield Finding(f"{d.isoformat()} is {(ctx.today - d).days} days ago.", "date")


@rule(
    "contract.already_ended",
    "Contract already ended",
    "A confirmed contract whose end date has passed no longer counts for your forecast. Mark it done or fix the date.",
    "warning",
    "contracts",
)
def contract_ended(ctx: Ctx):
    r = ctx.record
    end = parse_date(r.get("end"))
    if end and end < ctx.today and r.get("status", "confirmed") == "confirmed" and (ctx.changed("end") or ctx.changed("status")):
        yield Finding(f"The contract ended on {end.isoformat()}.", "end")


@rule(
    "absence.overlap",
    "Overlapping time off",
    "Two absences for the same person on the same days usually mean a duplicate.",
    "info",
    "absences",
)
def absence_overlap(ctx: Ctx):
    r = ctx.record
    start, end = parse_date(r.get("start")), parse_date(r.get("end") or r.get("start"))
    uid = r.get("user_id") or (ctx.me or {}).get("id")
    if not start or not end:
        return
    for a in ctx.others("absences"):
        if a.get("user_id") != uid:
            continue
        s2, e2 = parse_date(a.get("start")), parse_date(a.get("end") or a.get("start"))
        if s2 and e2 and s2 <= end and start <= e2:
            yield Finding(f"Overlaps another absence ({s2.isoformat()} – {e2.isoformat()}).", "start", {"overlaps": a.get("id")})
            return
