"""Scan of old offers and invoices of the user (docs/specs/document-scan.md).

A clean document is added at once with its original number; any flag keeps it as a ``document_scans`` row (status
``flagged``) that becomes a document only when the user confirms it. Nothing read from a file is logged.
"""

import hashlib
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from permita38.core import calc
from permita38.core.errors import ServiceError
from permita38.core.models import DocumentIn, DocumentScanConfirmIn
from permita38.core.money import dec, line_amount, to_float, vat_amount
from permita38.core.receipts import docrules, photopdf, runner
from permita38.core.receipts.extraction import Word
from permita38.core.repository import ID_RE, Repository
from permita38.core.repository.base import new_id
from permita38.core.settings import effective_settings
from permita38.core.uploads import check_upload

from .importer.shared import CustomerIndex, customer_record, digest, save_batch, save_document
from .numbering import NumberingService

ACCEPTED = [".pdf", ".jpg", ".jpeg"]  # the original must be a PDF; only JPEG photos are turned into one
INTERNAL_KEYS = ("sha256", "doc_key")  # never sent to a client
COLLECTION = {"invoice": "invoices", "offer": "offers"}
DOC_COLLECTIONS = ("invoices", "offers")
NEW_STATUS = "sent"  # an old document is open until the user marks it paid (docs/specs/document-scan.md)

SERVICE_FLAGS = {
    "customer_ambiguous": "Several customers could be the recipient; choose one.",
    "small_business_conflict": "The Settings (small business) give other totals than the printed ones.",
    "invalid_values": "Some values read from the document are not valid; please check them.",
}
NOTES = {
    **docrules.NOTES,
    "customer_new": "No customer matches; a new customer will be created.",
    "added_open": "Added as open, not paid.",
}

_BOX_FIELD = {
    "number": "number", "date": "date", "due_date": "due_date", "valid_until": "valid_until",
    "service_from": "service", "service_to": "service", "recipient": "customer", "kind": "kind",
    "net": "net", "vat": "vat", "gross": "gross",
}  # fmt: skip
_TOTAL_BOXES = ("net", "vat", "gross")
MAX_BOXES = 60


def public_scan(scan: dict) -> dict:
    """The scan as sent to a client: without the hash and the duplicate key."""
    return {k: v for k, v in scan.items() if k not in INTERNAL_KEYS}


def doc_key(space: str, number: str | None) -> str:
    """``"<number space>:<number>"``, the number case-folded with runs of whitespace collapsed (dashes and other
    separators are kept: ``2024-1-23`` and ``2024-12-3`` differ); empty without a number."""
    flat = flat_number(number)
    return f"{space}:{flat}" if flat else ""


def flat_number(number: Any) -> str:
    return " ".join(str(number or "").casefold().split())


def _items(codes: list[str], table: dict[str, str]) -> list[dict]:
    return [{"code": c, "message": table.get(c, c)} for c in codes]


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class DocumentScanService:
    def __init__(
        self, store: Repository, config: Any, numbering: NumberingService | None = None, *, today: Any = date.today
    ):
        self.store = store
        self.config = config
        self.numbering = numbering or NumberingService(store)
        self._today = today

    # ------------------------------------------------------------------ reading
    def list_flagged(self) -> list[dict]:
        rows = list(self.store.find("document_scans", status="flagged"))
        rows.sort(key=lambda s: s.get("created") or 0, reverse=True)
        return [public_scan(s) for s in rows]

    def get(self, scan_id: str) -> dict:
        return public_scan(self._scan(scan_id))

    def _scan(self, scan_id: str) -> dict:
        scan = self.store.get("document_scans", scan_id) if ID_RE.match(scan_id) else None
        if not scan:
            raise ServiceError(404, "Not found")
        return scan

    def page_png(self, scan_id: str, page: int) -> bytes:
        scan = self._scan(scan_id)
        name = scan.get("attachment") or ""
        path = self.store.attachment_path(name) if name.lower().endswith(".pdf") else None
        if not path or page < 0:
            raise ServiceError(404, "Not found")
        png = runner.render_page(path, page, self.config, None)
        if png is None:
            raise ServiceError(404, "Not found")
        return png

    # ------------------------------------------------------------------ upload
    def upload(self, me: dict, filename: str | None, blob: bytes) -> dict:
        if not self.config.scan.enabled:
            raise ServiceError(503, "Scanning is switched off on this device")
        filename, _ext = check_upload(filename, blob, ACCEPTED)
        digest_ = hashlib.sha256(blob).hexdigest()
        self._refuse_known_file(digest_, mark=False)  # (not in a transaction: stale rows are cleaned up later)
        if filename.lower().endswith((".jpg", ".jpeg")):  # a broken photo is refused before the slow read
            try:
                photopdf.jpeg_size(blob)
            except photopdf.PhotoError:
                raise ServiceError(422, "The photo could not be converted to a PDF") from None
        name = self.store.save_attachment(filename, blob)
        try:
            return self._process(me, filename, name, digest_)
        except Exception as exc:
            self.store.delete_attachment(name)  # a duplicate or a failure leaves no file behind
            if isinstance(exc, ServiceError):
                raise
            existing = self._known_scan(digest_, mark=False)  # a parallel upload won the unique index on sha256
            if existing is not None:
                raise self._duplicate_file(existing) from None
            raise

    def _own(self) -> docrules.OwnIdentity:
        st = effective_settings(self.store.settings() or {})
        return docrules.OwnIdentity(
            str(st.get("company_name") or ""),
            str(st.get("owner_name") or ""),
            str(st.get("vat_id") or ""),
            str(st.get("tax_number") or ""),
        )

    def _process(self, me: dict, filename: str, name: str, digest_: str) -> dict:
        path = self.store.attachment_path(name) or ""
        read = runner.read_file(path, self.config)  # the slow part runs outside the transaction
        current = name
        try:
            if name.lower().endswith((".jpg", ".jpeg")):  # the original must be a PDF
                current = self._photo_to_pdf(name, read.rotation or 0)
            x = docrules.parse(read, self._own())
            codes, note_codes = docrules.evaluate(x, self._today())
            scan_id = new_id()
            draft = self._draft(x)
            if self._implausible(draft) and "date_implausible" not in codes:
                codes.append("date_implausible")
                codes.sort(key=list(docrules.FLAGS).index)
            doc = self._doc(x, read, codes)
            row: dict[str, Any] = {
                "id": scan_id,
                "sha256": digest_,
                "attachment": current,
                "filename": (docrules.clean_text(re.split(r"[\\/]", filename)[-1], 200) or "document"),
                "engine": read.engine,
                "kind": x.kind if x.kind in ("invoice", "offer") else None,
                "draft": draft,
                "doc": doc,
                "created_by": me.get("id"),
            }
            with self.store.transaction():  # one writer at a time: the re-checks and the inserts cannot interleave
                self._refuse_known_file(digest_)
                space = self._space(row["kind"])
                key = doc_key(space, x.number) if row["kind"] and x.number else ""
                self._refuse_known_number(key, x.number, None)
                row["doc_key"] = key
                flags = list(codes)
                notes = list(note_codes)
                self._match_customer(draft, flags, notes)
                row["notes"] = _items(notes, NOTES)
                body = None
                if not flags:
                    try:
                        body = DocumentScanConfirmIn.model_validate(self._body_from_draft(draft))
                    except (ValidationError, KeyError, ValueError, ArithmeticError):
                        flags.append("invalid_values")
                saved_scan = None
                record = None
                if body is not None:
                    try:
                        record, saved_scan = self._add(me, row, body.model_dump(), accept_mismatch=False, auto=True)
                    except _Mismatch as err:
                        flags.append(err.code)
                    except ServiceError as err:  # values the document rules refuse: the user fixes them
                        if err.status != 422:
                            raise
                        flags.append("invalid_values")
                if record is None:
                    row.update(status="flagged", flags=_items(flags, {**docrules.FLAGS, **SERVICE_FLAGS}))
                    saved_scan = self.store.save("document_scans", row)
        except BaseException:
            if current != name:
                self.store.delete_attachment(current)  # the converted photo; upload() removes the original
            raise
        if record is not None:
            return {"status": "added", "scan": public_scan(saved_scan or {}), "record": record}
        return {"status": "flagged", "scan": public_scan(saved_scan or {})}

    @staticmethod
    def _implausible(draft: dict) -> bool:
        for later, earlier in (("due_date", "date"), ("valid_until", "date"), ("service_to", "service_from")):
            a, b = draft.get(later), draft.get(earlier)
            if a and b and str(a) < str(b):
                return True
        return False

    def _photo_to_pdf(self, name: str, rotation: int) -> str:
        """A photo becomes a one page PDF, so that it can be the document's original (same as receipts of photos)."""
        path = self.store.attachment_path(name) or ""
        try:
            with open(path, "rb") as handle:
                pdf = photopdf.build([(handle.read(), rotation)])
        except (photopdf.PhotoError, OSError):
            raise ServiceError(422, "The photo could not be converted to a PDF") from None
        new_name = self.store.save_attachment("document.pdf", pdf)
        self.store.delete_attachment(name)
        return new_name

    # ------------------------------------------------------------------ draft and doc
    def _draft(self, x: docrules.DocExtraction) -> dict:
        rec = x.recipient
        rate = x.vat_groups[0].rate if len(x.vat_groups) == 1 else None
        due = x.due_date if x.kind == "invoice" else None
        valid = x.valid_until if x.kind == "offer" else None
        if x.kind == "invoice" and due is None and x.date:
            due = self._default_due(x.date)
        return {
            "kind": x.kind if x.kind in ("invoice", "offer") else None,
            "number": x.number,
            "date": x.date,
            "due_date": due,
            "valid_until": valid,
            "service_from": x.service_from,
            "service_to": x.service_to,
            "customer_id": None,
            "customer": {
                "name": rec.name if rec else None,
                "street": rec.street if rec else None,
                "zip": rec.zip if rec else None,
                "city": rec.city if rec else None,
                "country": rec.country if rec else None,
                "vat_id": rec.vat_id if rec else None,
                "email": rec.email if rec else None,
            },
            "vat_rate": _text(rate),
            "lines": [
                {
                    "description": ln.description,
                    "quantity": _text(ln.quantity),
                    "unit": ln.unit,
                    "price": _text(ln.price),
                }
                for ln in x.lines
            ],
        }

    def _doc(self, x: docrules.DocExtraction, read: Any, codes: list[str]) -> dict:
        totals_bad = "totals_mismatch" in codes or "ambiguous_total" in codes
        boxes: list[dict] = []
        for key, words in x.sources.items():
            base = key.split(":")[0]
            field = _BOX_FIELD.get(key) or ("item" if base == "line" else None)
            if field is None or not words:
                continue
            check = ("failed" if totals_bad else "ok") if field in _TOTAL_BOXES else "none"
            by_page: dict[int, list[Word]] = {}
            for w in words:
                by_page.setdefault(w.page, []).append(w)
            for page, ws in sorted(by_page.items()):
                x0 = max(0.0, min(1.0, min(w.x for w in ws)))
                y0 = max(0.0, min(1.0, min(w.y for w in ws)))
                x1 = max(0.0, min(1.0, max(w.x + w.w for w in ws)))
                y1 = max(0.0, min(1.0, max(w.y + w.h for w in ws)))
                boxes.append(
                    {
                        "field": field, "page": page, "x": round(x0, 4), "y": round(y0, 4),
                        "w": round(x1 - x0, 4), "h": round(y1 - y0, 4), "check": check, "value": None,
                    }
                )  # fmt: skip
        vat = x.vat_total if x.vat_total is not None else (x.vat_groups[0].vat if len(x.vat_groups) == 1 else None)
        doc: dict[str, Any] = {
            "pages": read.pages_total or read.pages_read,
            "boxes": boxes[:MAX_BOXES],
            "checks": [],
            "printed_net": _text(x.net),
            "printed_vat": _text(vat),
            "printed_total": _text(x.gross),
        }
        if read.engine == "ocr" and read.rotation:
            doc["rotation"] = read.rotation
        return doc

    def _default_due(self, day: str) -> str | None:
        try:
            days = int(effective_settings(self.store.settings() or {}).get("payment_days") or 0)
            return (date.fromisoformat(day) + timedelta(days=days)).isoformat()
        except (ValueError, OverflowError):
            return None

    # ------------------------------------------------------------------ customers
    def _match_customer(self, draft: dict, flags: list[str], notes: list[str]) -> None:
        cust = {k: v for k, v in (draft.get("customer") or {}).items() if v}
        if not cust.get("name"):
            return
        try:
            rec = customer_record(cust)
        except Exception:  # a value the importer rules refuse (control characters, a bad e-mail): the user fixes it
            if "no_customer" not in flags:
                flags.append("no_customer")
            return
        found, certain = CustomerIndex(self.store.list("customers")).match(rec)
        if certain:
            draft["customer_id"] = found[0]["id"]
            draft["customer_match"] = {"action": "existing", "ids": [found[0]["id"]]}
        elif found:  # a similar name or several candidates: the user decides; the candidates are only a suggestion
            flags.append("customer_ambiguous")
            draft["customer_match"] = {"action": "ambiguous", "ids": [c["id"] for c in found]}
        else:
            notes.append("customer_new")
            draft["customer_match"] = {"action": "new", "ids": []}

    @staticmethod
    def _body_from_draft(draft: dict) -> dict:
        """The draft as the input of ``DocumentScanConfirmIn`` (what a confirm sends)."""
        customer = None if draft.get("customer_id") else {k: v for k, v in draft["customer"].items() if v}
        return {
            "kind": draft["kind"],
            "number": draft["number"],
            "date": draft["date"],
            "due_date": draft.get("due_date"),
            "valid_until": draft.get("valid_until"),
            "service_from": draft.get("service_from"),
            "service_to": draft.get("service_to"),
            "customer_id": draft.get("customer_id"),
            "customer": customer,
            "vat_rate": draft["vat_rate"],
            "lines": [
                {
                    "description": ln["description"],
                    "quantity": ln["quantity"],
                    "unit": ln.get("unit"),
                    "price": ln["price"],
                }
                for ln in draft["lines"]
            ],
        }

    # ------------------------------------------------------------------ duplicates
    def _space(self, kind: str | None) -> str:
        if effective_settings(self.store.settings() or {}).get("numbering_shared"):
            return "shared"
        return COLLECTION.get(kind or "", "invoices")

    def _live(self, scan: dict, *, mark: bool) -> bool:
        """A waiting scan, or an added one whose document still exists. Any other row (batch undo or deletion removed
        the document, discarded) is gone: with ``mark`` (inside a write transaction) it becomes ``undone`` and loses
        its hash and number key."""
        status = scan.get("status")
        if status == "flagged":
            return True
        if status == "added" and scan.get("record_id") and self.store.get(scan["record_coll"], scan["record_id"]):
            return True
        if mark and (scan.get("sha256") or scan.get("doc_key")):
            self.store.save(
                "document_scans",
                {**scan, "status": "undone" if status == "added" else status, "sha256": None, "doc_key": ""},
            )
        return False

    def _known_scan(self, digest_: str, *, mark: bool = False) -> dict | None:
        for scan in self.store.find("document_scans", sha256=digest_):
            if self._live(scan, mark=mark):
                return scan
        return None

    def _refuse_known_file(self, digest_: str, *, mark: bool = True) -> None:
        scan = self._known_scan(digest_, mark=mark)
        if scan is not None:
            raise self._duplicate_file(scan)

    def _refuse_known_number(self, key: str, number: str | None, own_id: str | None) -> None:
        """409 if a document or a waiting scan already has this number in the same number space."""
        if not key:
            return
        space, _, flat = key.partition(":")
        colls = DOC_COLLECTIONS if space == "shared" else (space,)
        for coll in colls:
            for d in self.store.list(coll):
                for n in (d.get("original_number"), d.get("number")):
                    if n and flat_number(n) == flat:
                        raise self._duplicate_number(
                            {"collection": coll, "id": d["id"], "number": d.get("number") or number}, number
                        )
        for scan in self.store.find("document_scans", doc_key=key):
            if scan["id"] == own_id or not self._live(scan, mark=True):
                continue
            if scan.get("status") == "flagged":
                raise self._duplicate_number({"scan_id": scan["id"]}, number)
            raise self._duplicate_number(
                {"collection": scan.get("record_coll"), "id": scan.get("record_id"), "number": number}, number
            )

    def _duplicate_file(self, scan: dict) -> ServiceError:
        record = self.store.get(scan["record_coll"], scan["record_id"]) if scan.get("record_id") else None
        if record is not None:
            existing: dict[str, Any] = {
                "collection": scan["record_coll"],
                "id": record["id"],
                "number": record.get("number"),
            }
            message = f"This file was already added as {record.get('number')}."
        else:
            existing = {"scan_id": scan["id"]}
            message = "This file was already uploaded."
        return ServiceError(409, message, body={"code": "duplicate_file", "existing": existing})

    @staticmethod
    def _duplicate_number(existing: dict, number: str | None) -> ServiceError:
        return ServiceError(
            409,
            f"A document with the number {number} already exists.",
            body={"code": "duplicate_number", "existing": existing},
        )

    # ------------------------------------------------------------------ adding
    def _add(self, me: dict, scan: dict, data: dict, *, accept_mismatch: bool, auto: bool = False) -> tuple[dict, dict]:
        """Inside the caller's transaction: customer, document, counter, batch row and the scan row. Returns the
        ``record`` answer and the saved scan."""
        coll = COLLECTION[data["kind"]]
        number = str(data["number"]).strip()
        key = doc_key(self._space(data["kind"]), number)
        self._refuse_known_number(key, number, scan["id"])
        settings = effective_settings(self.store.settings() or {})
        batch_id = new_id()
        records: list[list[Any]] = []
        customer_id = data.get("customer_id")
        new_customer: dict | None = None
        counts = {"create": 1, "skip": 0, "overwrite": 0, "error": 0, "customers_created": 0, "customers_merged": 0}
        if customer_id:
            if self.store.get("customers", customer_id) is None:
                raise ServiceError(422, "The customer does not exist")
            counts["customers_merged"] = 1
        else:
            try:
                new_customer = customer_record(
                    {k: (str(v) if v is not None else None) for k, v in (data.get("customer") or {}).items()}
                )
            except Exception as err:
                raise ServiceError(422, f"The customer data is not valid: {err}") from None
            customer_id = new_customer["id"] = new_id()
            counts["customers_created"] = 1
        record: dict[str, Any] = {
            "number": number,
            "original_number": number,
            "imported": True,
            "customer_id": customer_id,
            "status": NEW_STATUS,
            "date": data["date"],
            "vat_rate": float(data["vat_rate"]),
            "lines": [
                {
                    "description": ln["description"],
                    "qty": float(ln["quantity"]),
                    "unit": ln.get("unit"),
                    "price": float(ln["price"]),
                }
                for ln in data["lines"]
            ],
            "external_id": f"scan:{scan['id']}",
        }
        if data["kind"] == "invoice":
            due = data.get("due_date") or self._default_due(data["date"])
            if due:
                record["due_date"] = due
        elif data.get("valid_until"):
            record["valid_until"] = data["valid_until"]
        for k in ("service_from", "service_to"):
            if data.get(k):
                record[k] = data[k]
        if scan.get("attachment"):
            record["original_pdf"] = scan["attachment"]
            path = self.store.attachment_path(scan["attachment"])
            if path:
                with open(path, "rb") as handle:
                    record["original_pdf_sha256"] = hashlib.sha256(handle.read()).hexdigest()
        plain_net = sum((line_amount(ln["quantity"], ln["price"]) for ln in data["lines"]), Decimal(0))
        plain_vat = vat_amount(plain_net, data["vat_rate"])
        plain = {"net": plain_net, "vat": plain_vat, "total": plain_net + plain_vat}
        shown = calc.doc_totals({**record, "lines": [dict(ln) for ln in record["lines"]]}, settings)
        computed = {
            "net": dec(str(shown["net"])),
            "vat": dec(str(shown["vat_total"])),
            "total": dec(str(shown["gross"])),
        }
        printed = {k: dec(v, None) if v is not None else None for k, v in self._printed(scan).items()}
        differs = [k for k, p in printed.items() if p is not None and p != computed[k]]
        if differs and not accept_mismatch:
            if auto:  # the lines give the printed totals, but the Settings (small business) change the result
                raise _Mismatch(
                    "small_business_conflict"
                    if all(p in (None, plain[k]) for k, p in printed.items())
                    else "totals_mismatch"
                )
            raise ServiceError(
                409,
                "Please confirm before saving: the lines do not give the printed totals",
                body={"code": "needs_acceptance", "missing": ["totals_mismatch"]},
            )
        if differs:
            record["totals_unverified"] = True
            record["given_totals"] = {
                k: to_float(p)
                for k, p in (("net", printed["net"]), ("vat", printed["vat"]), ("gross", printed["total"]))
                if p is not None
            }
        try:
            DocumentIn.model_validate(record)
        except ValidationError:
            raise ServiceError(422, "The document values are not valid") from None
        if new_customer is not None:  # everything is validated: from here on the writes cannot be refused
            saved_c = self.store.save("customers", {**new_customer, "import_batch": batch_id})
            records.append(["customers", saved_c["id"], digest(saved_c)])
        saved = save_document(self.store, self.numbering, coll, record, batch_id)
        records.append([coll, saved["id"], digest(saved)])
        save_batch(
            self.store, batch_id, "scan", scan.get("filename") or "document", counts, records,
            mapping={"columns": {}, "options": {}},
        )  # fmt: skip
        row = {
            **scan,
            "status": "added",
            "kind": data["kind"],
            "doc_key": key,
            "record_coll": coll,
            "record_id": saved["id"],
            "batch_id": batch_id,
            "flags": [],
            "notes": [*(scan.get("notes") or []), {"code": "added_open", "message": NOTES["added_open"]}],
        }
        saved_scan = self.store.save("document_scans", row)
        answer = {"collection": coll, "id": saved["id"], "number": number, "year": int(str(data["date"])[:4])}
        return answer, saved_scan

    @staticmethod
    def _printed(scan: dict) -> dict:
        doc = scan.get("doc") or {}
        return {"net": doc.get("printed_net"), "vat": doc.get("printed_vat"), "total": doc.get("printed_total")}

    # ------------------------------------------------------------------ confirm and discard
    def confirm(self, me: dict, scan_id: str, body: DocumentScanConfirmIn) -> dict:
        self._scan(scan_id)
        with self.store.transaction():  # a second click waits here and then finds the scan added
            scan = self._scan(scan_id)
            if scan.get("status") != "flagged":
                raise ServiceError(409, "This document has already been handled", body={"code": "not_flagged"})
            data = body.model_dump()
            record, saved = self._add(me, scan, data, accept_mismatch=body.accept_totals_mismatch)
        return {"record": record, "scan": public_scan(saved)}

    def discard(self, scan_id: str) -> None:
        with self.store.transaction():
            scan = self._scan(scan_id)
            if scan.get("status") != "flagged":
                raise ServiceError(
                    409,
                    "Only a document that still needs a check can be discarded; use the document or the batch undo",
                    body={"code": "not_flagged"},
                )
            self.store.save(
                "document_scans", {**scan, "status": "discarded", "sha256": None, "doc_key": "", "attachment": None}
            )
        if scan.get("attachment"):
            self.store.delete_attachment(scan["attachment"])


class _Mismatch(Exception):
    """The lines of an automatically added document do not give the printed totals: it is flagged instead."""

    def __init__(self, code: str = "totals_mismatch"):
        super().__init__(code)
        self.code = code
