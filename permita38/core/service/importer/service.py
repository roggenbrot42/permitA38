"""Plan, run and undo an import of existing documents, customers or expenses.

One code path serves both the dry run and the real run: :meth:`ImportService.plan` validates every row and returns the
report; :meth:`ImportService.run` re-plans from the same input and writes the plan in one transaction, and refuses to
write anything if a single row is an error (all-or-nothing).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from permita38.core.errors import ServiceError
from permita38.core.money import dec, line_amount, q2, to_float, vat_amount
from permita38.core.repository import Repository, new_id
from permita38.core.service.numbering import NumberingService
from permita38.core.settings import effective_settings

from . import values as V
from .mapping import auto_map, parse_mapping_file
from .tables import Table, check_size, read_table
from .xlsx_reader import open_guarded_zip

KINDS = {"invoices": "invoices", "offers": "offers", "customers": "customers", "expenses": "expenses"}
DOC_KINDS = ("invoices", "offers")
ALLOWED_STATUS = {"invoices": ("draft", "sent", "paid", "cancelled"),
                  "offers": ("draft", "sent", "accepted", "rejected")}  # fmt: skip
FUZZY_MATCH = 0.9
MAX_PDF_BYTES = 20 * 1024 * 1024
LEGAL_FORMS = {"gmbh", "ag", "kg", "ug", "ohg", "gbr", "mbh", "co", "e", "k", "se", "ev"}
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,20}$")


@dataclass
class _Row:
    number: int  # 1-based row number in the file (header is row 1, so the first data row is 2)
    values: dict[str, Any]


@dataclass
class _Outcome:
    row: int
    key: str
    action: str = "create"
    messages: list[str] = field(default_factory=list)
    record: dict | None = None  # the record to write (create / overwrite)
    coll: str = ""
    customer: dict | None = None  # {"action", "id", "score", "name"}
    totals: dict | None = None
    pdf: str | None = None  # matched PDF entry name
    overwrite_id: str | None = None
    new_customer: dict | None = None  # record of a customer to create (run)


@dataclass
class _Plan:
    doc_type: str
    table: Table
    mapping: dict[str, str]
    options: dict
    outcomes: list[_Outcome]
    new_customers: list[dict]
    pdf_unmatched: list[str]
    pdf_entries: dict[str, bytes]
    unmapped: list[str]


class ImportService:
    def __init__(self, store: Repository, numbering: NumberingService | None = None):
        self.store = store
        self.numbering = numbering or NumberingService(store)

    # ------------------------------------------------------------------ public
    def plan(
        self,
        data: bytes,
        filename: str,
        doc_type: str,
        *,
        mapping: str | dict | None = None,
        options: dict | None = None,
        pdfs: bytes | None = None,
        sheet: str | None = None,
        delimiter: str | None = None,
    ) -> dict:
        return self._report(self._build(data, filename, doc_type, mapping, options, pdfs, sheet, delimiter))

    def run(self, data: bytes, filename: str, doc_type: str, **kwargs: Any) -> dict:
        built = self._build(
            data,
            filename,
            doc_type,
            kwargs.get("mapping"),
            kwargs.get("options"),
            kwargs.get("pdfs"),
            kwargs.get("sheet"),
            kwargs.get("delimiter"),
        )
        report = self._report(built)
        if report["counts"]["error"]:
            return {**report, "applied": False}
        batch_id = new_id()
        records: list[list[Any]] = []
        overwrites = 0
        next_before = self.numbering.preview(built.doc_type, 1)[0] if built.doc_type in DOC_KINDS else None
        with self.store.transaction():
            for c in built.new_customers:
                saved = self.store.save("customers", {**c, "import_batch": batch_id})
                records.append(["customers", saved["id"], _digest(saved)])
            for o in built.outcomes:
                if o.action not in ("create", "overwrite") or o.record is None:
                    continue
                rec = dict(o.record)
                if o.pdf is not None and built.pdf_entries.get(o.pdf) is not None:
                    blob = built.pdf_entries[o.pdf]
                    name = self.store.save_attachment(f"{rec['number']}.pdf", blob)
                    rec["original_pdf"] = name
                    rec["original_pdf_sha256"] = hashlib.sha256(blob).hexdigest()
                if o.action == "overwrite":
                    overwrites += 1
                    rec["id"] = o.overwrite_id
                    saved = self.store.save(o.coll, {**rec, "import_batch": batch_id})
                else:
                    saved = self.store.save(o.coll, {**rec, "import_batch": batch_id})
                    records.append([o.coll, saved["id"], _digest(saved)])
                if o.coll in DOC_KINDS and rec.get("number"):
                    day = V.parse_date(rec.get("date"))
                    if day:
                        self.numbering.observe_imported(o.coll, rec["number"], day)
            batch = self.store.save(
                "import_batches",
                dict(
                    id=batch_id,
                    kind=built.doc_type,
                    filename=filename[:200],
                    status="imported",
                    counts=report["counts"],
                    mapping={"columns": built.mapping, "options": built.options},
                    finished=0,
                    records=records,
                    overwrites=overwrites,
                ),
            )
        next_after = self.numbering.preview(built.doc_type, 1)[0] if built.doc_type in DOC_KINDS else None
        return {
            **report,
            "applied": True,
            "batch_id": batch["id"],
            "next_number": next_after,
            "next_number_before": next_before,
        }

    def batches(self) -> list[dict]:
        out = []
        for b in self.store.list("import_batches"):
            out.append({k: v for k, v in b.items() if k != "records"} | {"records": len(b.get("records") or [])})
        return out

    def undo(self, batch_id: str) -> dict:
        batch = self.store.get("import_batches", batch_id)
        if batch is None:
            raise ServiceError(404, "Not found")
        if batch.get("status") != "imported":
            raise ServiceError(409, "This import was already undone")
        if batch.get("overwrites"):
            raise ServiceError(409, "This import overwrote existing documents and cannot be undone automatically")
        changed = []
        for coll, rid, updated in batch.get("records") or []:
            cur = self.store.get(coll, rid)
            if cur is None or _digest(cur) != updated:
                changed.append(f"{coll}/{rid}")
        if changed:
            raise ServiceError(
                409, f"{len(changed)} imported record(s) were changed or deleted since the import; undo refused"
            )
        with self.store.transaction():
            for coll, rid, _ in batch.get("records") or []:
                self.store.delete(coll, rid)
            self.store.save("import_batches", {**batch, "status": "undone"})
        return {"batch_id": batch_id, "removed": len(batch.get("records") or []), "applied": True}

    # ------------------------------------------------------------------ building the plan
    def _build(self, data, filename, doc_type, mapping_raw, options_raw, pdfs, sheet, delimiter) -> _Plan:
        if doc_type not in KINDS:
            raise ServiceError(400, "The import type must be invoices, offers, customers or expenses")
        check_size(data)
        if pdfs is not None:
            check_size(pdfs)
        table = read_table(data, filename, sheet=sheet, delimiter=delimiter)
        try:
            mapping, file_options = parse_mapping_file(mapping_raw)
        except ValueError:
            raise ServiceError(400, "The mapping file is not valid JSON") from None
        options = {"decimal": "auto", "date_order": "dmy", "on_duplicate": "skip", "accept_unverified": False}
        options.update(file_options)
        options.update({k: v for k, v in (options_raw or {}).items() if v is not None})
        if not mapping:
            mapping, _ = auto_map(table.headers, doc_type)
        unmapped = [h for h in table.headers if h and h not in mapping.values()]
        index = {}
        for field_name, header in mapping.items():
            if header not in table.headers:
                raise ServiceError(422, f"The mapping refers to the column {header!r}, which the file does not have")
            index[field_name] = table.headers.index(header)
        self._check_required(doc_type, index)
        rows = [
            _Row(i + 2, {f: (row[idx] if idx < len(row) else None) for f, idx in index.items()})
            for i, row in enumerate(table.rows)
        ]
        pdf_entries = _read_pdfs(pdfs) if pdfs else {}
        if doc_type == "customers":
            outcomes, new_customers = self._plan_customers(rows, options)
            pdf_unmatched = list(pdf_entries)
        else:
            outcomes, new_customers, pdf_unmatched = self._plan_documents(doc_type, rows, index, options, pdf_entries)
        return _Plan(doc_type, table, mapping, options, outcomes, new_customers, pdf_unmatched, pdf_entries, unmapped)

    def _check_required(self, doc_type: str, index: dict[str, int]) -> None:
        if doc_type == "customers":
            need = [["name"]]
        elif doc_type == "expenses":
            need = [["date"], ["net", "gross"]]
        else:
            need = [
                ["number"],
                ["date"],
                ["customer_name", "vat_id", "tax_number", "email", "customer_no"],
                ["net", "gross"],
            ]
        for alternatives in need:
            if not any(a in index for a in alternatives):
                names = " or ".join(alternatives)
                raise ServiceError(422, f"No column is mapped for {names}; map it in the mapping or rename the header")

    # ------------------------------------------------------------------ customers
    def _plan_customers(self, rows: list[_Row], options: dict) -> tuple[list[_Outcome], list[dict]]:
        index = _CustomerIndex(self.store.list("customers"))
        outcomes = []
        new: list[dict] = []
        for row in rows:
            out = _Outcome(row.number, f"row {row.number}", coll="customers")
            try:
                rec = _customer_record(row.values)
            except V.CellError as e:
                out.action = "error"
                out.messages.append(str(e))
                outcomes.append(out)
                continue
            found, score = index.find(rec)
            if found is not None:
                out.action = "skip"
                out.customer = {"action": "merge", "id": found["id"], "score": score, "name": found.get("name")}
                out.messages.append(f"already exists as {found.get('name')!r}; merged, not created")
            else:
                rec["id"] = new_id()
                out.action = "create"
                out.record = rec
                out.customer = {"action": "create", "id": rec["id"], "score": score, "name": rec.get("name")}
                new.append(rec)
                index.add(rec)
            outcomes.append(out)
        return outcomes, new

    # ------------------------------------------------------------------ documents and expenses
    def _plan_documents(self, doc_type, rows, index, options, pdf_entries):
        settings = effective_settings(self.store.settings())
        customers = _CustomerIndex(self.store.list("customers"))
        existing_numbers = {}
        existing_external = set()
        coll = doc_type
        for d in self.store.list(coll):
            if d.get("number"):
                existing_numbers[d["number"]] = d
            if d.get("external_id"):
                existing_external.add(d["external_id"])
        line_mode = "line_description" in index
        groups: list[tuple[int, list[_Row]]] = []
        if doc_type in DOC_KINDS and line_mode:
            by_number: dict[str, tuple[int, list[_Row]]] = {}
            for row in rows:
                key = text_or_empty(row.values.get("number"))
                if key not in by_number:
                    by_number[key] = (row.number, [])
                    groups.append((row.number, by_number[key][1]))
                by_number[key][1].append(row)
        else:
            groups = [(r.number, [r]) for r in rows]
        outcomes: list[_Outcome] = []
        new_customers: list[dict] = []
        pdf_used: set[str] = set()
        for first_row, members in groups:
            out = _Outcome(first_row, f"row {first_row}", coll=coll)
            try:
                record, customer_info = self._record(doc_type, members, options, settings, customers, new_customers)
            except V.CellError as e:
                out.action = "error"
                out.messages.append(str(e))
                outcomes.append(out)
                continue
            out.customer = customer_info
            out.totals = record.pop("_totals", None)
            out.messages.extend(record.pop("_messages", []))
            if doc_type == "expenses":
                ext = record.get("external_id") or _hash(
                    doc_type, record.get("date"), record.get("vendor"), record.get("net"), record.get("description")
                )
                record["external_id"] = ext
                key_value = None
            else:
                number = record["number"]
                key_value = number
                ext = record.get("external_id") or _hash(
                    doc_type,
                    number,
                    record.get("date"),
                    record.get("customer_key"),
                    out.totals and out.totals.get("gross"),
                )
                record["external_id"] = ext
            record.pop("customer_key", None)
            if ext in existing_external:
                out.action = "skip"
                out.messages.append("already imported (same external id)")
                outcomes.append(out)
                continue
            if key_value is not None and key_value in existing_numbers:
                old = existing_numbers[key_value]
                if options["on_duplicate"] == "overwrite" and old.get("imported"):
                    out.action = "overwrite"
                    out.overwrite_id = old["id"]
                    out.messages.append("replaces the earlier import with this number")
                elif options["on_duplicate"] == "overwrite":
                    out.action = "error"
                    out.messages.append(f"{key_value} exists and was not imported; overwrite is only for imports")
                else:
                    out.action = "skip"
                    out.messages.append(f"{key_value} already exists; skipped")
                    outcomes.append(out)
                    continue
            out.record = record
            out.coll = coll
            if pdf_entries and key_value:
                match = _match_pdf(key_value, pdf_entries)
                if match and match not in pdf_used:
                    out.pdf = match
                    pdf_used.add(match)
                    out.messages.append(f"original PDF {match} attached")
            outcomes.append(out)
        pdf_unmatched = [n for n in pdf_entries if n not in pdf_used]
        return outcomes, new_customers, pdf_unmatched

    def _record(self, doc_type, members, options, settings, customers, new_customers):
        values = members[0].values
        if doc_type == "expenses":
            return self._expense(values, options, settings), None
        number = text_or_none(values.get("number"), 60, "number")
        if number is None:
            raise V.CellError("number is missing")
        day = V.parse_date(values.get("date"), options["date_order"])
        if day is None:
            raise V.CellError("date is missing")
        due = V.parse_date(values.get("due_date"), options["date_order"])
        paid = V.parse_date(values.get("paid_date"), options["date_order"])
        default_status = "paid" if paid else "sent"
        status = V.parse_status(values.get("status"), default_status)
        if status not in ALLOWED_STATUS[doc_type]:
            raise V.CellError(f"status {status!r} is not allowed for {doc_type}")
        V.parse_currency(values.get("currency"))
        customer_rec = _customer_record(values, required=True)
        found, score = customers.find(customer_rec)
        info: dict
        if found is not None:
            info = {"action": "merge", "id": found["id"], "score": score, "name": found.get("name")}
            customer_id = found["id"]
        else:
            customer_id = new_id()
            customer_rec["id"] = customer_id
            customers.add(customer_rec)
            new_customers.append(customer_rec)
            info = {"action": "create", "id": customer_id, "score": score, "name": customer_rec.get("name")}
        rate_given = V.parse_rate(values.get("vat_rate"))
        net_given = V.parse_amount(values.get("net"), options["decimal"])
        vat_given = V.parse_amount(values.get("vat"), options["decimal"])
        gross_given = V.parse_amount(values.get("gross"), options["decimal"])
        lines, net_total = self._lines(values, members, options, net_given, gross_given, vat_given)
        rate = self._rate(rate_given, vat_given, net_total, settings)
        vat_total = vat_amount(net_total, rate)
        gross_total = net_total + vat_total
        computed = {"net": net_total, "vat": vat_total, "gross": gross_total}
        mismatches = []
        for key, given in (("net", net_given), ("vat", vat_given), ("gross", gross_given)):
            if given is not None and given != computed[key]:
                mismatches.append(f"{key}: file {given}, computed {computed[key]}")
        messages: list[str] = []
        totals_info = {"net": to_float(net_total), "vat": to_float(vat_total), "gross": to_float(gross_total)}
        record: dict[str, Any] = {
            "number": number,
            "original_number": number,
            "imported": True,
            "customer_id": customer_id,
            "customer_key": _customer_key(customer_rec),
            "status": status,
            "date": day.isoformat(),
            "vat_rate": float(rate),
            "lines": lines,
        }
        if doc_type == "offers":
            if due:
                record["valid_until"] = due.isoformat()
        else:
            if due:
                record["due_date"] = due.isoformat()
        if paid:
            record["paid_date"] = paid.isoformat()
        for key, field_name in (("title", "title"), ("reference", "reference")):
            t = text_or_none(values.get(field_name), 200, field_name)
            if t:
                record[key] = t
        ext = text_or_none(values.get("external_id"), 100, "external id")
        if ext:
            record["external_id"] = ext
        if mismatches:
            if options["accept_unverified"]:
                record["totals_unverified"] = True
                record["given_totals"] = {
                    k: to_float(v)
                    for k, v in (("net", net_given), ("vat", vat_given), ("gross", gross_given))
                    if v is not None
                }
                messages.append("totals unverified, imported as given (" + "; ".join(mismatches) + ")")
            else:
                raise V.CellError("totals do not match the lines (" + "; ".join(mismatches) + ")")
        record["_totals"] = totals_info
        record["_messages"] = messages
        return record, info

    def _lines(self, values, members, options, net_given, gross_given, vat_given):
        if "line_description" in values:  # line mode: values of every member row are lines
            lines = []
            total = Decimal(0)
            for m in members:
                v = m.values
                desc = text_or_none(v.get("line_description"), 2000, "line description") or ""
                qty = V.parse_amount(v.get("line_qty"), options["decimal"])
                price = V.parse_amount(v.get("line_price"), options["decimal"])
                if price is None:
                    raise V.CellError("line price is missing")
                qty = qty if qty is not None else Decimal(1)
                if qty > Decimal("10000000"):
                    raise V.CellError("line quantity is too large")
                amount = line_amount(qty, price)
                total += amount
                lines.append({"description": desc, "qty": float(qty), "unit": None, "price": to_float(price)})
            if not lines:
                raise V.CellError("no line")
            return lines, q2(total)
        net = net_given
        if net is None and gross_given is not None and vat_given is not None:
            net = gross_given - vat_given
        if net is None:
            raise V.CellError("net amount is missing")
        if net > Decimal("1000000000") or net < Decimal("-1000000000"):
            raise V.CellError("net amount is too large")
        return [{"description": "Imported total", "qty": 1, "unit": None, "price": to_float(net)}], q2(net)

    def _rate(self, rate_given, vat_given, net_total, settings) -> Decimal:
        allowed = set(V.ALLOWED_VAT_RATES) | {dec(settings.get("vat_rate", 19))}
        if rate_given is not None:
            rate = rate_given
        elif vat_given is not None and net_total:
            rate = Decimal(round(vat_given / net_total * 100))
        else:
            rate = dec(settings.get("vat_rate", 19))
        if rate not in {Decimal(str(a)) for a in allowed}:
            raise V.CellError(f"VAT rate {rate} is not one of 0, 5, 7, 16, 19 or the configured rate")
        return rate

    def _expense(self, values, options, settings) -> dict:
        day = V.parse_date(values.get("date"), options["date_order"])
        if day is None:
            raise V.CellError("date is missing")
        V.parse_currency(values.get("currency"))
        net = V.parse_amount(values.get("net"), options["decimal"])
        vat = V.parse_amount(values.get("vat"), options["decimal"])
        gross = V.parse_amount(values.get("gross"), options["decimal"])
        rate = V.parse_rate(values.get("vat_rate"))
        if net is None and gross is not None and vat is not None:
            net = gross - vat
        if net is None:
            raise V.CellError("net amount is missing")
        if net < 0:
            raise V.CellError("net amount must not be negative (refunds are credit notes)")
        if vat is None:
            vat = vat_amount(net, rate if rate is not None else dec(settings.get("vat_rate", 19)))
        if rate is None:
            rate = Decimal(round(vat / net * 100)) if net else dec(settings.get("vat_rate", 19))
        return {
            "date": day.isoformat(),
            "net": to_float(net),
            "vat": to_float(vat),
            "vat_rate": float(rate),
            "vendor": text_or_none(values.get("vendor"), 200, "vendor"),
            "description": text_or_none(values.get("description"), 2000, "description"),
            "category": text_or_none(values.get("category"), 200, "category"),
            "external_id": text_or_none(values.get("external_id"), 100, "external id"),
        }

    # ------------------------------------------------------------------ report
    def _report(self, built: _Plan) -> dict:
        counts = {"create": 0, "skip": 0, "overwrite": 0, "error": 0, "customers_created": 0, "customers_merged": 0}
        rows = []
        for o in built.outcomes:
            counts[o.action] = counts.get(o.action, 0) + 1
            if o.customer and o.customer["action"] == "create" and o.action != "error":
                counts["customers_created"] += 1
            if o.customer and o.customer["action"] == "merge" and o.action != "error":
                counts["customers_merged"] += 1
            rows.append(
                {
                    "row": o.row,
                    "action": o.action,
                    "messages": o.messages,
                    "number": (o.record or {}).get("number") if o.record else None,
                    "customer": o.customer,
                    "totals": o.totals,
                    "pdf": o.pdf,
                }
            )
        if built.doc_type == "customers":
            counts["customers_created"] = sum(1 for o in built.outcomes if o.action == "create")
            counts["customers_merged"] = sum(1 for o in built.outcomes if o.action == "skip")
        return {
            "type": built.doc_type,
            "source": built.table.source,
            "encoding": built.table.info.get("encoding"),
            "delimiter": built.table.info.get("delimiter"),
            "rows_total": len(built.table.rows),
            "mapping": built.mapping,
            "options": built.options,
            "unmapped_headers": built.unmapped,
            "counts": counts,
            "rows": rows,
            "pdf": {"matched": sum(1 for o in built.outcomes if o.pdf), "unmatched": built.pdf_unmatched},
            "applied": False,
        }


# ---------------------------------------------------------------------- helpers
def text_or_none(value, limit: int, what: str) -> str | None:
    return V.text(value, limit, what)


def text_or_empty(value) -> str:
    return "" if V.is_blank(value) else str(value).strip()


def _normalized_name(name: str) -> str:
    words = re.sub(r"[^a-z0-9äöüß]+", " ", (name or "").casefold()).split()
    while words and words[-1] in LEGAL_FORMS:
        words.pop()
    return " ".join(words)


def _customer_key(rec: dict) -> str:
    return (
        rec.get("vat_id") or rec.get("tax_number") or rec.get("email") or rec.get("customer_no") or rec.get("name", "")
    )


def _customer_record(values: dict, required: bool = False) -> dict:
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


class _CustomerIndex:
    """Matches customers by VAT ID, tax number, email, customer number, then normalised name (exact or >= 0.9)."""

    def __init__(self, existing: list[dict]):
        self.items: list[dict] = []
        for c in existing:
            self.add(c)

    def add(self, rec: dict) -> None:
        self.items.append(rec)

    def find(self, rec: dict) -> tuple[dict | None, float]:
        for key in ("vat_id", "tax_number", "email", "customer_no"):
            if rec.get(key):
                for c in self.items:
                    if c.get(key) and str(c[key]).lower() == str(rec[key]).lower():
                        return c, 1.0
        name = _normalized_name(rec.get("name", ""))
        if not name:
            return None, 0.0
        best, best_score = None, 0.0
        for c in self.items:
            other = _normalized_name(c.get("name", ""))
            if not other:
                continue
            score = 1.0 if other == name else SequenceMatcher(None, name, other).ratio()
            if score > best_score:
                best, best_score = c, score
        if best is not None and best_score >= FUZZY_MATCH:
            return best, round(best_score, 3)
        return None, round(best_score, 3)


def _digest(record: dict) -> str:
    """Fingerprint of a whole record: detects any change after the import (timestamps have 1 s resolution)."""
    return hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _hash(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _read_pdfs(blob: bytes) -> dict[str, bytes]:
    """PDF files from a zip, keyed by file name. Names are only used for matching, never as paths."""
    zf = open_guarded_zip(blob)
    out: dict[str, bytes] = {}
    for info in zf.infolist():
        if info.is_dir() or not info.filename.lower().endswith(".pdf"):
            continue
        if info.file_size > MAX_PDF_BYTES:
            raise ServiceError(422, f"{info.filename.split('/')[-1]} is larger than 20 MB")
        data = zf.read(info)
        if not data.startswith(b"%PDF-"):
            raise ServiceError(422, f"{info.filename.split('/')[-1]} is not a PDF file")
        out[info.filename.split("/")[-1]] = data
    return out


def _match_pdf(number: str, entries: dict[str, bytes]) -> str | None:
    want = re.sub(r"[^a-z0-9]", "", number.casefold())
    hits = [n for n in entries if re.sub(r"[^a-z0-9]", "", n.rsplit(".", 1)[0].casefold()) == want]
    return hits[0] if len(hits) == 1 else None


__all__ = ["ImportService", "KINDS"]
