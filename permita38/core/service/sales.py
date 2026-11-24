"""Offers and invoices: numbering, finalizing, conversion, billing tracked time, PDF."""

import time
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from permita38.core import calc, documents, rights
from permita38.core.errors import ServiceError
from permita38.core.models import INTERNAL_DOCUMENT_FIELDS, InvoiceIn
from permita38.core.money import ZERO, dec, hours_from_minutes, q2, to_float
from permita38.core.repository import Repository
from permita38.core.settings import accounting_start, effective_settings

from . import invoice_state
from .numbering import NumberingService
from .planner.service import PlannerService
from .postings import IssuePostingService, build_snapshot
from .project_customer import check_billable_project, check_project_customer, project_kind
from .records import _IMPORT_FIELDS, RESPONSE_ONLY, RecordService, _valid_id

FROZEN_MESSAGE = "The invoice is issued and cannot be changed; issue a credit note"
REVERSE_MESSAGE = "The invoice is paid; reverse the payment instead"
CANCEL_MESSAGE = "The invoice is issued; issue a cancellation (credit note) instead"
PAID_NOTE = "recorded as paid"

# docs/specs/credit-notes-payments.md 2.1: the only fields of an issued invoice a PUT may change
_CHANGEABLE = ("due_date", "notes", "reference")
_LABELS = ("sent", "accepted", "rejected")
_SERVER_OWNED = {"id", "created", "updated", "number", *_IMPORT_FIELDS, *RESPONSE_ONLY, *INTERNAL_DOCUMENT_FIELDS}


def _canon(value: Any) -> Any:
    """A value in a comparable shape: empty = ``None``, numbers by value (``100``, ``100.0``, ``"100.00"`` agree)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return format(dec(value).normalize(), "f")
    if isinstance(value, Mapping):
        items = ((k, _canon(v)) for k, v in value.items())
        return {k: v for k, v in items if v is not None}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return value


def _canon_lines(lines: Any) -> list[Any]:
    """Invoice lines without the server-computed ``amount``; the defaults of a line are filled in."""
    out = []
    for ln in lines if isinstance(lines, list) else []:
        row = {"description": "", "qty": 0, "price": 0, **(ln if isinstance(ln, Mapping) else {})}
        row.pop("amount", None)
        out.append(_canon(row))
    return out


def _same_frozen(key: str, new: Any, old: Any) -> bool:
    if key == "lines":
        return _canon_lines(new) == _canon_lines(old)
    if key == "tax_treatment":
        return (new or "standard") == (old or "standard")
    return bool(_canon(new) == _canon(old))


def _user_id(user: Mapping[str, Any] | None) -> str:
    return str((user or {}).get("id") or "system")


def record_payment_row(
    store: Repository,
    invoice_id: str,
    *,
    date: str,
    amount_cents: int,
    kind: str = "payment",
    method: str = "bank",
    note: str | None = None,
    reverses_id: str | None = None,
    user: Mapping[str, Any] | None = None,
) -> dict:
    """Insert one ``payments`` row (``amount_cents`` signed: refunds and reversals negative) and return it.

    TODO(E10): when the accounting is initialised this must also post the payment event (accounting 9.2); the E8
    postings adapter has no payment entry point yet, so before E10 only the row is written (the initialisation posts
    stored payments, accounting 16 step 5).
    """
    row: dict[str, Any] = dict(
        document_id=invoice_id,
        date=date,
        amount_cents=int(amount_cents),
        method=method,
        kind=kind,
        created_at=int(time.time()),
        created_by=_user_id(user),
    )
    if note:
        row["note"] = note
    if reverses_id:
        row["reverses_id"] = reverses_id
    return store.save("payments", row)


def gross_cents_of(invoice: Mapping[str, Any], settings: Mapping[str, Any]) -> int:
    """The gross of an issued invoice in cents: the snapshot's, else the current totals (legacy invoice)."""
    issued = invoice.get("issued")
    if isinstance(issued, Mapping) and issued.get("gross") is not None:
        return int(issued["gross"])
    return int(q2(dec(calc.doc_totals(dict(invoice), dict(settings))["gross"]) * 100))


def materialize_legacy_payment(
    store: Repository, invoice: dict, settings: Mapping[str, Any], user: Mapping[str, Any] | None = None
) -> dict:
    """Before the first write that acts on payments or credits of an issued invoice (spec 2.3): write the missing
    ``issued`` snapshot, and turn a legacy virtual payment (stored ``paid``, no rows) into a real ``payments`` row,
    in the caller's transaction. Idempotent. Returns the invoice as stored afterwards."""
    with store.transaction():
        inv = invoice
        if not inv.get("issued"):
            day = calc.d(inv.get("date")) or date.today()
            inv = store.save("invoices", {**inv, "issued": build_snapshot(store, inv, dict(settings), day)})
        if inv.get("status") == "paid" and not store.find("payments", document_id=inv["id"]):
            gross = int(inv["issued"]["gross"])
            if gross != 0:
                record_payment_row(
                    store,
                    inv["id"],
                    date=invoice_state.legacy_payment_date(inv),
                    amount_cents=gross,
                    note=invoice_state.LEGACY_NOTE,
                    user=user,
                )
        return inv


class SalesService:
    def __init__(
        self,
        store: Repository,
        records: RecordService,
        numbering: NumberingService | None = None,
        postings: IssuePostingService | None = None,
    ):
        self.store = store
        self.postings = postings or IssuePostingService(store)
        self.records = records
        records.invoice_hook = self  # the generic invoice routes go through this service
        self.numbering = numbering or NumberingService(store)

    def settings(self) -> dict:
        return effective_settings(self.store.settings())

    def next_number(self, coll: str) -> str:
        return self.numbering.preview(coll, 1)[0]

    def _get(self, coll: str, rid: str) -> dict:
        doc = self.store.get(coll, rid)
        if not doc:
            raise ServiceError(404, "Not found")
        return doc

    def finalize(self, coll: str, rid: str, user: Mapping[str, Any] | None = None) -> dict:
        # the number is taken in the same transaction that saves the document: a failed finalize gives it back
        with self.store.transaction():
            doc = self._get(coll, rid)
            st = self.settings()
            if not doc.get("customer_id"):
                raise ServiceError(400, "Choose a customer first")
            if not doc.get("lines"):
                raise ServiceError(400, "Add at least one line")
            base = calc.d(doc.get("date")) or date.today()
            if not doc.get("date"):
                doc["date"] = base.isoformat()  # the date it posts with is stored with it
            if not doc.get("number"):
                doc["number"] = self.numbering.allocate(coll, base)
            if doc.get("status", "draft") == "draft":
                doc["status"] = "sent"
            if coll == "invoices" and not doc.get("due_date"):
                doc["due_date"] = (base + timedelta(days=int(st["payment_days"]))).isoformat()
            if coll == "offers" and not doc.get("valid_until"):
                doc["valid_until"] = (base + timedelta(days=int(st["offer_valid_days"]))).isoformat()
            if coll == "invoices":
                start = accounting_start(self.store.settings())
                if start is not None and base >= start and not doc.get("credit_kind"):
                    # year N+1 opens (copied from N) before the snapshot reads its bases
                    self.postings.journal.ensure_year(base.year, str((user or {}).get("id") or "system"))
                if not doc.get("issued"):  # written once, in the transaction of the number, never changed
                    doc["issued"] = build_snapshot(self.store, {**doc, "date": base.isoformat()}, st, base)
                doc = self.store.save(coll, doc)
                self.postings.post_issue({**doc, "date": base.isoformat()}, user)
                return doc
            return self.store.save(coll, doc)

    # -- issued invoices: derived payment fields, freeze (docs/specs/credit-notes-payments.md 2) ---------------------
    def _derived(self, inv: Mapping[str, Any], st: Mapping[str, Any]) -> invoice_state.Derived:
        pays = self.store.find("payments", document_id=inv["id"])
        notes = invoice_state.credit_notes_of(self.store.find("invoices", corrects_id=inv["id"]))
        return invoice_state.derive(inv, gross_cents_of(inv, st), pays, notes)

    @staticmethod
    def _with_fields(inv: dict, d: invoice_state.Derived) -> dict:
        out = {k: v for k, v in inv.items() if k != "paid_date"}
        return {**out, **d.fields}

    @staticmethod
    def _shows_derived(inv: Mapping[str, Any]) -> bool:
        return inv.get("status", "draft") != "draft" and inv.get("kind") != "credit_note"

    def invoice_view(self, inv: dict) -> dict:
        """The invoice as a response shows it: an issued invoice carries the derived payment fields."""
        if not self._shows_derived(inv):
            return inv
        return self._with_fields(inv, self._derived(inv, self.settings()))

    def invoice_views(self, rows: Iterable[dict]) -> list[dict]:
        """:meth:`invoice_view` for many invoices; payments and credit notes are loaded once."""
        rows = list(rows)
        st = self.settings()
        pays = invoice_state.group_by(self.store.list("payments"), "document_id")
        notes = invoice_state.group_by(invoice_state.credit_notes_of(self.store.list("invoices")), "corrects_id")
        out = []
        for inv in rows:
            if not self._shows_derived(inv):
                out.append(inv)
                continue
            d = invoice_state.derive(inv, gross_cents_of(inv, st), pays.get(inv["id"], []), notes.get(inv["id"], []))
            out.append(self._with_fields(inv, d))
        return out

    def get_invoice(self, me: dict, rid: str) -> dict:
        return self.invoice_view(self.records.get_stored(me, "invoices", rid))

    def list_invoices(self, me: dict) -> list[dict]:
        return self.invoice_views(self.records.list_stored(me, "invoices"))

    def delete_invoice(self, me: dict, rid: str) -> None:
        old = self.store.get("invoices", rid) if _valid_id(rid) else None
        if (
            old
            and not old.get("number")
            and (old.get("status", "draft") != "draft" or old.get("kind") == "credit_note")
        ):
            raise ServiceError(409, "An issued invoice or credit note cannot be deleted; issue a credit note instead")
        self.records.delete_stored(me, "invoices", rid)  # a numbered invoice: its own 409; a draft: deleted

    def update_invoice(
        self,
        me: dict,
        rid: str,
        data: dict,
        given: Mapping[str, Any] | None = None,
        nulls: Iterable[str] = (),
    ) -> dict:
        """Update an invoice. A draft goes through the normal write rules (``data`` = the whole body). An issued
        invoice is frozen (spec 2.1): ``given`` holds the keys the client sent (defaults left out), ``nulls`` the keys
        sent as null; only the changeable fields, the legacy ``paid`` translation (2.2) and unknown extra fields apply.
        """
        old = self.store.get("invoices", rid) if _valid_id(rid) else None
        if not old or old.get("status", "draft") == "draft":
            return self.records.update_stored(me, "invoices", rid, data)
        if rights.scope_for(me, "invoices", "write") != rights.Scope.ALL:
            raise ServiceError(403, "Forbidden")
        with self.store.transaction():
            return self.invoice_view(
                self._update_issued(me, rid, data, set(data if given is None else given), set(nulls))
            )

    def _update_issued(self, me: dict, rid: str, data: dict, sent: set[str], nulls: set[str]) -> dict:
        old = self._get("invoices", rid)
        st = self.settings()
        d0 = self._derived(old, st)
        known = set(InvoiceIn.model_fields)
        changes: dict[str, Any] = {}
        for key in sorted((sent | nulls) - _SERVER_OWNED):
            value = None if key in nulls else data.get(key)
            if key == "paid_date" or key == "status":
                continue
            if key in _CHANGEABLE:
                changes[key] = value
            elif key in known:
                if not _same_frozen(key, value, old.get(key)):
                    raise ServiceError(409, FROZEN_MESSAGE)
            elif value is not None:
                changes[key] = value  # an unknown extra field round-trips
        action = self._status_action(old, d0, data.get("status") if "status" in sent else None, data, sent)
        if action in ("pay", "reverse"):
            old = materialize_legacy_payment(self.store, old, st, me)
            d0 = self._derived(old, st)
            if action == "pay" and d0.open_cents > 0:
                paid_on = str(data["paid_date"])[:10]
                record_payment_row(self.store, rid, date=paid_on, amount_cents=d0.open_cents, note=PAID_NOTE, user=me)
            elif action == "reverse":
                for row in self.store.find("payments", document_id=rid):
                    if row["kind"] == "payment":
                        record_payment_row(
                            self.store,
                            rid,
                            date=row["date"],
                            amount_cents=-int(row["amount_cents"]),
                            kind="reversal",
                            reverses_id=row["id"],
                            user=me,
                        )
                changes["status"], changes["paid_date"] = data["status"], None
        elif action == "label":
            changes["status"] = data["status"]
        new = {**old}
        for key, value in changes.items():
            if value is None:
                new.pop(key, None)
            else:
                new[key] = value
        return self.store.save("invoices", new) if new != old else old

    def _status_action(
        self, old: Mapping[str, Any], d0: invoice_state.Derived, req: Any, data: Mapping[str, Any], sent: set[str]
    ) -> str | None:
        """What the requested ``status`` / ``paid_date`` means for an issued invoice: ``pay`` (legacy paid -> a
        payment of the open amount), ``reverse`` (paid back to a label, a legacy virtual payment), ``label``
        (sent/accepted/rejected), or ``None`` (nothing). Refusals raise (spec 2.1, 2.2)."""
        paid_date = data.get("paid_date") if "paid_date" in sent else None
        if req is None or req == d0.status:
            if paid_date and str(paid_date)[:10] != d0.paid_date:
                raise ServiceError(409, "The payment date cannot be changed; reverse the payment instead")
            return None
        if old.get("kind") == "credit_note":
            raise ServiceError(409, FROZEN_MESSAGE)
        if req == "paid":
            if not paid_date:
                raise ServiceError(422, "paid_date is required when the invoice is marked as paid")
            if d0.open_cents <= 0:
                return None
            paid_on = str(paid_date)[:10]
            if paid_on < str(old.get("date") or "")[:10]:
                raise ServiceError(422, "The payment date cannot be before the invoice date")
            if paid_on > date.today().isoformat():
                raise ServiceError(422, "The payment date cannot be in the future")
            return "pay"
        if req in _LABELS:
            if d0.status == "paid":
                if d0.virtual is None:
                    raise ServiceError(409, REVERSE_MESSAGE)
                return "reverse"
            if d0.status == "cancelled":
                raise ServiceError(409, FROZEN_MESSAGE)
            return "label"
        raise ServiceError(409, CANCEL_MESSAGE if req == "cancelled" else FROZEN_MESSAGE)

    def pdf(self, coll: str, rid: str) -> tuple[bytes, str]:
        doc = self._get(coll, rid)
        if doc.get("original_pdf"):  # imported: the original is the legal record, never re-rendered
            path = self.store.attachment_path(doc["original_pdf"])
            if path is None:
                raise ServiceError(404, "The original PDF is missing")
            with open(path, "rb") as fh:
                return fh.read(), (doc.get("number") or "original") + ".pdf"
        data = documents.render(self.store, coll[:-1], calc.with_year_regime(self.store, doc))
        return data, (doc.get("number") or "draft") + ".pdf"

    def accept_offer(self, rid: str) -> dict:
        with self.store.transaction():
            return self._accept_offer(rid)

    def _accept_offer(self, rid: str) -> dict:
        o = self._get("offers", rid)
        o["status"] = "accepted"
        o = self.store.save("offers", o)
        plan = o.get("plan") or {}
        existing = self.store.get("projects", o["project_id"]) if o.get("project_id") else None
        if existing and project_kind(existing) == "internal":
            raise ServiceError(409, "Internal projects cannot have offers, contracts or invoices")
        title = (o.get("title") or "").strip()
        hours: dict = {}
        if plan and plan.get("start") and plan.get("end"):
            if (plan.get("hours_per_week") or 0) > 0:
                hours["hours_per_week"] = plan["hours_per_week"]
            elif (plan.get("total_hours") or 0) > 0:
                hours["total_hours"] = plan["total_hours"]
            else:
                h_lines = [ln for ln in o.get("lines") or [] if ln.get("unit") == "h"]
                if not h_lines:
                    raise ServiceError(422, "The offer has no hours to plan")
                total = sum((dec(ln.get("qty")) for ln in h_lines), ZERO).quantize(Decimal("0.01"), ROUND_HALF_UP)
                if total <= 0 or total > 100000:
                    raise ServiceError(422, "The offer's hour lines must add up to more than 0 and at most 100000 h")
                hours["total_hours"] = to_float(total)
        if existing:  # the offer belongs to a project: widen its period to cover the plan (recorded)
            proj = self.store.save("projects", PlannerService.cover_plan(existing, plan, rid))
        else:
            proj = self.store.save(
                "projects",
                dict(
                    name=title or ("Offer " + (o.get("number") or "")).strip()
                    if o.get("number")
                    else title or "Offer draft",
                    kind="customer",
                    customer_id=o.get("customer_id"),
                    status="active",
                    color="#2563eb",
                    offer_id=rid,
                    start=plan.get("start"),
                    end=plan.get("end"),
                ),
            )
        out = {"offer": o, "project": proj, "contract": None}
        if plan and plan.get("start") and plan.get("end"):
            c = dict(
                title=title or "Contract " + (o.get("number") or ""),
                customer_id=o.get("customer_id"),
                project_id=proj["id"],
                start=plan["start"],
                end=plan["end"],
                offer_id=rid,
                status="confirmed",
                **hours,
            )
            for k in ("rate",):
                if plan.get(k) is not None:
                    c[k] = plan[k]
            check_project_customer(self.store, c, None)
            out["contract"] = self.store.save("contracts", c)
        return out

    def offer_to_invoice(self, rid: str) -> dict:
        o = self._get("offers", rid)
        keys = ("customer_id", "project_id", "lines", "template_id", "vat_rate", "language", "title", "reference")
        if o.get("project_id") and not self.store.get("projects", o["project_id"]):
            raise ServiceError(422, "The offer's project no longer exists; set another project on the offer first.")
        inv = {k: o[k] for k in keys if o.get(k) is not None}
        inv.update(date=date.today().isoformat(), offer_id=rid)
        check_project_customer(self.store, inv, None)
        check_billable_project(self.store, inv, None)  # an old offer whose project was made personal later
        return self.store.save("invoices", self.records.prepare_document(inv, None))

    def invoice_from_time(
        self, customer_id: str | None, project_id: str | None, date_from: str | None, date_to: str | None
    ) -> dict:
        d0, d1 = date_from or "0000-00-00", date_to or "9999-99-99"
        ctx = calc.context(self.store)
        # an invoice goes to exactly one customer: the one asked for, else the customer of the chosen project
        proj = ctx["projects"].get(project_id) if project_id else None
        if project_id and not proj:
            raise ServiceError(404, "Project not found")
        if proj and proj.get("kind") == "internal":
            raise ServiceError(422, "Internal projects cannot be billed")
        customer_id = customer_id or (proj or {}).get("customer_id")
        if not customer_id:
            raise ServiceError(422, "Choose a customer: an invoice goes to one customer only")
        if not self.store.get("customers", customer_id):
            raise ServiceError(404, "Customer not found")
        groups: dict = {}
        picked = []
        for e in self.store.list("entries"):
            if e.get("invoice_id") or not e.get("billable", True) or not (d0 <= e["date"] <= d1):
                continue
            p = ctx["projects"].get(e.get("project_id"))
            if not p or p.get("kind") == "internal":
                continue
            if project_id and p["id"] != project_id:
                continue
            if p.get("customer_id") != customer_id:
                continue
            rate = calc.rate_for(e, ctx)
            t = ctx["tasks"].get(e.get("task_id"))
            g = groups.setdefault((p["id"], (t or {}).get("id"), rate), dict(minutes=0, p=p, t=t, rate=rate))
            g["minutes"] += e["minutes"]
            picked.append(e)
        if not picked:
            raise ServiceError(400, "No unbilled time found for this selection")
        lines = [
            dict(
                description=g["p"]["name"] + (" – " + g["t"]["name"] if g["t"] else ""),
                qty=to_float(hours_from_minutes(g["minutes"])),
                unit="h",
                price=g["rate"],
            )
            for g in groups.values()
        ]
        dates = sorted(e["date"] for e in picked)
        inv = dict(
            customer_id=customer_id,
            lines=lines,
            date=date.today().isoformat(),
            service_from=dates[0],
            service_to=dates[-1],
            vat_rate=self.settings()["vat_rate"],
        )
        if project_id:
            inv["project_id"] = project_id
        inv = {k: v for k, v in inv.items() if v is not None}
        check_project_customer(self.store, inv, None)
        with self.store.transaction():  # the invoice and the "billed" marks on the entries succeed or fail together
            inv = self.store.save("invoices", self.records.prepare_document(inv, None))
            for e in picked:
                e["invoice_id"] = inv["id"]
                self.store.save("entries", e)
        return inv
