"""Plan, run and undo an import of existing documents, customers or expenses.

One code path serves both the dry run and the real run: :meth:`ImportService.plan` validates every row and returns the
report; :meth:`ImportService.run` re-plans from the same input and writes the plan in one transaction, and refuses to
write anything if a single row is an error (all-or-nothing).
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any, Callable

from permita38.core.errors import ServiceError
from permita38.core.money import dec, line_amount, q2, to_float, vat_amount
from permita38.core.repository import Repository, new_id
from permita38.core.repository.schema import COLLECTIONS
from permita38.core.service.numbering import NumberingService
from permita38.core.settings import effective_settings

from . import values as V
from .mapping import auto_map, parse_mapping_file
from .tables import Table, check_size, read_table
from .xlsx_reader import XlsxError, open_guarded_zip

KINDS = {"invoices": "invoices", "offers": "offers", "customers": "customers", "expenses": "expenses"}
DOC_KINDS = ("invoices", "offers")
ALLOWED_STATUS = {"invoices": ("draft", "sent", "paid", "cancelled"),
                  "offers": ("draft", "sent", "accepted", "rejected")}  # fmt: skip
FUZZY_MATCH = 0.9
MAX_PDF_BYTES = 20 * 1024 * 1024
LEGAL_FORMS = {"gmbh", "ag", "kg", "ug", "ohg", "gbr", "mbh", "co", "e", "k", "se", "ev"}
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,20}$")


#: Extension point for the accounting module (not implemented yet): every hook is called inside the import
#: transaction with (collection, saved record) for each imported document or expense, and must raise to abort it.
#: Hooks are registered at start-up: ``importer.service.IMPORT_HOOKS.append(fn)``.
IMPORT_HOOKS: list[Callable[[str, dict], None]] = []


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
    previous: dict | None = None  # the record an overwrite replaces


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
        # planning reads the existing numbers and external ids: do it inside the writing transaction, so nothing
        # can be created between the check and the write
        with self.store.transaction():
            return self._run(data, filename, doc_type, **kwargs)

    def _run(self, data: bytes, filename: str, doc_type: str, **kwargs: Any) -> dict:
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
        overwritten: list[list[Any]] = []
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
                    prev = o.previous or {}
                    for k in ("original_pdf", "original_pdf_sha256"):  # keep the legal original unless replaced
                        if k not in rec and k in prev:
                            rec[k] = prev[k]
                    saved = self.store.save(o.coll, {**rec, "import_batch": batch_id})
                    overwritten.append([o.coll, saved["id"], _digest(saved)])
                else:
                    saved = self.store.save(o.coll, {**rec, "import_batch": batch_id})
                    records.append([o.coll, saved["id"], _digest(saved)])
                for hook in IMPORT_HOOKS:
                    hook(o.coll, saved)
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
                    overwritten=overwritten,
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
        with self.store.transaction():  # checks and deletes in one transaction: nothing can change in between
            batch = self.store.get("import_batches", batch_id)
            if batch is None or batch.get("status") != "imported":
                raise ServiceError(409, "This import was already undone")
            records = batch.get("records") or []
            changed = []
            for coll, rid, updated in records:
                cur = self.store.get(coll, rid)
                if cur is None or _digest(cur) != updated:
                    changed.append(f"{coll}/{rid}")
            if changed:
                raise ServiceError(
                    409, f"{len(changed)} imported record(s) were changed or deleted since the import; undo refused"
                )
            # a customer created by this batch must not be referenced by anything outside the batch
            in_batch = {(coll, rid) for coll, rid, _ in records}
            customer_ids = {rid for coll, rid, _ in records if coll == "customers"}
            for coll in COLLECTIONS:
                for rec in self.store.list(coll):
                    cid = rec.get("customer_id")
                    if cid in customer_ids and (coll, rec.get("id")) not in in_batch:
                        raise ServiceError(
                            409,
                            f"An imported customer is still used by {coll} {rec.get('id')}; undo refused",
                        )
            for coll, rid, _ in records:
                self.store.delete(coll, rid)
            self.store.save("import_batches", {**batch, "status": "undone"})
        return {"batch_id": batch_id, "removed": len(records), "applied": True}

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
        except (ValueError, RecursionError):
            raise ServiceError(400, "The mapping file is not valid JSON") from None
        options = {
            "decimal": "auto",
            "date_order": "dmy",
            "on_duplicate": "skip",
            "accept_unverified": False,
            "merge_similar": False,
        }
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
        if "line_description" in index and doc_type in DOC_KINDS:
            need = [n for n in need if n != ["net", "gross"]]  # line mode computes the totals itself
        for alternatives in need:
            if not any(a in index for a in alternatives):
                names = " or ".join(alternatives)
                raise ServiceError(422, f"No column is mapped for {names}; map it in the mapping or rename the header")

    # ------------------------------------------------------------------ customers
    def _plan_customers(self, rows: list[_Row], options: dict) -> tuple[list[_Outcome], list[dict]]:
        index = _CustomerIndex(self.store.list("customers"), bool(options.get("merge_similar")))
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
                _note_similar(out)
                new.append(rec)
                index.add(rec)
            outcomes.append(out)
        return outcomes, new

    # ------------------------------------------------------------------ documents and expenses
    def _plan_documents(self, doc_type, rows, index, options, pdf_entries):
        settings = effective_settings(self.store.settings())
        customers = _CustomerIndex(self.store.list("customers"), bool(options.get("merge_similar")))
        existing_numbers: dict[str, dict] = {}
        existing_external = set()
        coll = doc_type
        number_colls = (
            ("invoices", "offers") if (doc_type in DOC_KINDS and settings.get("numbering_shared")) else (coll,)
        )
        for c in number_colls:
            for d in self.store.list(c):
                if d.get("number"):
                    existing_numbers[d["number"]] = {**d, "_coll": c}
        for d in self.store.list(coll):
            if d.get("external_id"):
                existing_external.add(d["external_id"])
        planned_numbers: set[str] = set()
        planned_external: set[str] = set()
        batch_digests = self._batch_digests()
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
            pending_customer = record.pop("_new_customer", None)
            out.customer = customer_info
            _note_similar(out)
            doc_day = V.parse_date(record["date"]) if doc_type in DOC_KINDS else None
            if doc_day is not None:
                note = self.numbering.imported_number_note(doc_type, record["number"], doc_day)
                if note:
                    out.messages.append(note)
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
            if (key_value is not None and key_value in planned_numbers) or ext in planned_external:
                out.action = "error"
                what = f"number {key_value}" if key_value is not None and key_value in planned_numbers else "row"
                out.messages.append(
                    f"duplicate {what} in this file (an earlier row has the same "
                    f"{'number' if key_value is not None else 'content or external id'})"
                    + ("" if key_value is not None else "; add an external ID column to tell identical rows apart")
                )
                outcomes.append(out)
                continue
            if ext in existing_external:
                out.action = "skip"
                out.messages.append("already imported (same external id)")
                outcomes.append(out)
                continue
            if key_value is not None and key_value in existing_numbers:
                old = existing_numbers[key_value]
                if old["_coll"] != coll:
                    out.action = "error"
                    out.messages.append(
                        f"{key_value} is already used by a document in {old['_coll']} (shared numbering)"
                    )
                elif options["on_duplicate"] == "overwrite" and old.get("imported"):
                    clean = {k: v for k, v in old.items() if k != "_coll"}
                    if batch_digests.get((old.get("import_batch"), old["id"])) != _digest(clean):
                        out.action = "error"
                        out.messages.append(
                            f"{key_value} was changed since its import; overwrite refused to keep your edits"
                        )
                    else:
                        out.action = "overwrite"
                        out.overwrite_id = old["id"]
                        out.previous = clean
                        out.messages.append("replaces the earlier import with this number")
                elif options["on_duplicate"] == "overwrite":
                    out.action = "error"
                    out.messages.append(f"{key_value} exists and was not imported; overwrite is only for imports")
                else:
                    out.action = "skip"
                    out.messages.append(f"{key_value} already exists; skipped")
                    outcomes.append(out)
                    continue
            if out.action == "error":
                outcomes.append(out)
                continue
            if pending_customer is not None:  # only rows that are imported create their customer
                customers.add(pending_customer)
                new_customers.append(pending_customer)
            if key_value is not None:
                planned_numbers.add(key_value)
            planned_external.add(ext)
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

    def _batch_digests(self) -> dict[tuple[str | None, str], str]:
        """(batch id, record id) -> digest of the record as the importer last wrote it (creates and overwrites)."""
        out: dict[tuple[str | None, str], str] = {}
        for b in self.store.list("import_batches"):
            if b.get("status") != "imported":
                continue
            for _coll, rid, digest in list(b.get("records") or []) + list(b.get("overwritten") or []):
                out[(b["id"], rid)] = digest
        return out

    def _record(self, doc_type, members, options, settings, customers, new_customers):
        values = members[0].values
        if doc_type == "expenses":
            return self._expense(values, options, settings), None
        number = text_or_none(values.get("number"), 50, "number")
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
        for m in members[1:]:  # later lines of one document must agree with its first row
            other_day = V.parse_date(m.values.get("date"), options["date_order"])
            if other_day is not None and other_day != day:
                raise V.CellError(f"row {m.number} has another date than the first row of {number}")
            if any(
                not V.is_blank(m.values.get(k))
                for k in ("customer_name", "vat_id", "tax_number", "email", "customer_no")
            ):
                other = _customer_record(m.values, required=True)
                if _customer_key(other).casefold() != _customer_key(customer_rec).casefold():
                    raise V.CellError(f"row {m.number} has another customer than the first row of {number}")
        found, score = customers.find(customer_rec)
        info: dict
        pending_customer: dict | None = None
        if found is not None:
            info = {"action": "merge", "id": found["id"], "score": score, "name": found.get("name")}
            customer_id = found["id"]
        else:
            customer_id = new_id()
            customer_rec["id"] = customer_id
            pending_customer = customer_rec  # registered by the caller only if the row is really imported
            info = {"action": "create", "id": customer_id, "score": score, "name": customer_rec.get("name")}
        rate_given = V.parse_rate(values.get("vat_rate"))
        net_given = V.parse_amount(values.get("net"), options["decimal"])
        vat_given = V.parse_amount(values.get("vat"), options["decimal"])
        gross_given = V.parse_amount(values.get("gross"), options["decimal"])
        net_hint = net_given
        if net_hint is None and vat_given is None and gross_given is not None and "line_description" not in values:
            # gross-only file: net from gross at the stated (or default) rate; a mismatch is reported below
            r = rate_given if rate_given is not None else dec(settings.get("vat_rate", 19))
            net_hint = q2(gross_given / (1 + r / 100))
        lines, net_total = self._lines(values, members, options, net_hint, gross_given, vat_given)
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
        record["_new_customer"] = pending_customer
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
            if abs(total) > Decimal("1000000000"):
                raise V.CellError("net amount is too large")
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
        allowed = {Decimal(str(a)) for a in V.ALLOWED_VAT_RATES} | {dec(settings.get("vat_rate", 19))}
        if rate_given is not None:
            rate = rate_given
        elif vat_given is not None and net_total:
            default = dec(settings.get("vat_rate", 19))
            ordered = [default] + [Decimal(str(a)) for a in sorted(allowed, reverse=True) if Decimal(str(a)) != default]
            # tiny amounts round the VAT to the same cent for several rates: take the rate that reproduces the cents
            fits = [r for r in ordered if vat_amount(net_total, r) == vat_given]
            rate = fits[0] if fits else Decimal(round(vat_given / net_total * 100))
        else:
            rate = dec(settings.get("vat_rate", 19))
        if rate not in allowed:
            raise V.CellError(f"VAT rate {rate} is not one of 0, 5, 7, 16, 19 or the configured rate")
        return rate

    def _expense(self, values, options, settings) -> dict:
        day = V.parse_date(values.get("date"), options["date_order"])
        if day is None:
            raise V.CellError("date is missing")
        V.parse_currency(values.get("currency"))
        net_given = V.parse_amount(values.get("net"), options["decimal"])
        vat_given = V.parse_amount(values.get("vat"), options["decimal"])
        gross_given = V.parse_amount(values.get("gross"), options["decimal"])
        rate_given = V.parse_rate(values.get("vat_rate"))
        net = net_given
        if net is None and gross_given is not None:
            if vat_given is not None:
                net = gross_given - vat_given
            else:  # gross-only: net from gross at the stated (or default) rate, like invoices
                r = rate_given if rate_given is not None else dec(settings.get("vat_rate", 19))
                net = q2(gross_given / (1 + r / 100))
        if net is None:
            raise V.CellError("net amount is missing")
        if net < 0:
            raise V.CellError("net amount must not be negative (refunds are credit notes)")
        if net > Decimal("1000000000"):
            raise V.CellError("net amount is too large")
        rate = self._rate(rate_given, vat_given, net, settings)  # also rejects rates outside the allowed set
        vat = vat_amount(net, rate)
        computed = {"net": net, "vat": vat, "gross": net + vat}
        mismatches = [
            f"{key}: file {given}, computed {computed[key]}"
            for key, given in (("net", net_given), ("vat", vat_given), ("gross", gross_given))
            if given is not None and given != computed[key]
        ]
        record: dict[str, Any] = {
            "date": day.isoformat(),
            "net": to_float(net),
            "vat": to_float(vat),
            "vat_rate": float(rate),
            "vendor": text_or_none(values.get("vendor"), 200, "vendor"),
            "description": text_or_none(values.get("description"), 2000, "description"),
            "category": text_or_none(values.get("category"), 200, "category"),
            "external_id": text_or_none(values.get("external_id"), 100, "external id"),
        }
        if mismatches:
            if not options["accept_unverified"]:
                raise V.CellError("totals do not match (" + "; ".join(mismatches) + ")")
            record["totals_unverified"] = True
            record["given_totals"] = {
                k: to_float(v)
                for k, v in (("net", net_given), ("vat", vat_given), ("gross", gross_given))
                if v is not None
            }
            record["_messages"] = ["totals unverified, imported as given (" + "; ".join(mismatches) + ")"]
        return record

    # ------------------------------------------------------------------ report
    def _report(self, built: _Plan) -> dict:
        counts = {"create": 0, "skip": 0, "overwrite": 0, "error": 0, "customers_created": 0, "customers_merged": 0}
        rows = []
        for o in built.outcomes:
            counts[o.action] = counts.get(o.action, 0) + 1
            if o.customer and o.customer["action"] == "create" and o.action in ("create", "overwrite"):
                counts["customers_created"] += 1
            if o.customer and o.customer["action"] == "merge" and o.action in ("create", "overwrite"):
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
            "headers": built.table.headers,
            "unmapped_headers": built.unmapped,
            "counts": counts,
            "rows": rows,
            "pdf": {"matched": sum(1 for o in built.outcomes if o.pdf), "unmatched": built.pdf_unmatched},
            "applied": False,
        }


# ---------------------------------------------------------------------- helpers
def _note_similar(out: _Outcome) -> None:
    c = out.customer
    if c and c["action"] == "create" and c["score"] >= FUZZY_MATCH:
        out.messages.append(
            f"new customer {c['name']!r} looks similar to an existing one (score {c['score']}); "
            "created as new, enable merge_similar to merge instead"
        )


def text_or_none(value, limit: int, what: str) -> str | None:
    return V.text(value, limit, what)


def text_or_empty(value) -> str:
    return "" if V.is_blank(value) else str(value).strip()


def _normalized_name(name: str, strip_legal: bool = True) -> str:
    words = re.sub(r"[^a-z0-9äöüß]+", " ", (name or "").casefold()).split()
    while strip_legal and words and words[-1] in LEGAL_FORMS:
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

    def __init__(self, existing: list[dict], merge_similar: bool = False):
        self.merge_similar = merge_similar
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
        exact_name = _normalized_name(rec.get("name", ""), strip_legal=False)
        for c in self.items:
            other = _normalized_name(c.get("name", ""))
            if not other:
                continue
            if _normalized_name(c.get("name", ""), strip_legal=False) == exact_name:
                score = 1.0
            elif other == name:
                score = 0.95  # same name, different legal form (GmbH vs AG): similar, never exact
            elif re.findall(r"\d+", name) != re.findall(r"\d+", other):
                continue  # "Schmidt Bau 1" and "Schmidt Bau 2" are different customers
            else:
                score = SequenceMatcher(None, name, other).ratio()
            if score > best_score:
                best, best_score = c, score
        if best is not None and (best_score == 1.0 or (best_score >= FUZZY_MATCH and self.merge_similar)):
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
    try:
        zf = open_guarded_zip(blob)
    except XlsxError as e:
        raise ServiceError(400, f"The PDF archive cannot be used: {e}") from None
    out: dict[str, bytes] = {}
    for info in zf.infolist():
        if info.is_dir() or not info.filename.lower().endswith(".pdf"):
            continue
        if info.file_size > MAX_PDF_BYTES:
            raise ServiceError(422, f"{info.filename.split('/')[-1]} is larger than 20 MB")
        try:
            data = zf.read(info)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError):
            raise ServiceError(
                400, f"{info.filename.split('/')[-1]} could not be unpacked (damaged or encrypted)"
            ) from None
        if not data.startswith(b"%PDF-"):
            raise ServiceError(422, f"{info.filename.split('/')[-1]} is not a PDF file")
        out[info.filename.split("/")[-1]] = data
    return out


def _match_pdf(number: str, entries: dict[str, bytes]) -> str | None:
    want = re.sub(r"[^a-z0-9]", "", number.casefold())
    hits = [n for n in entries if re.sub(r"[^a-z0-9]", "", n.rsplit(".", 1)[0].casefold()) == want]
    return hits[0] if len(hits) == 1 else None


__all__ = ["ImportService", "KINDS"]
