"""Stored data check (``permita38 check-db``): reads every record and reports what breaks the rules. Never writes.

Each rule is a small function ``(ctx) -> Iterable[Finding]`` registered in :data:`RULES`. Messages contain ids, codes
and field names only (never names, amounts, addresses or document text). Spec: docs/specs/db-integrity-check.md.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from permita38.core import calc, documents, money
from permita38.core import validation as v
from permita38.core.models import RECORD_MODELS
from permita38.core.numbering import Pattern, PatternError, normalize_text, parse_pattern, period_key
from permita38.core.repository import Repository
from permita38.core.repository import schema as _schema
from permita38.core.repository.dialect import ROW_LOCAL_KINDS
from permita38.core.repository.schema import GUARDS, TABLES, fits
from permita38.core.settings import effective_settings

ERROR, WARNING, INFO = "error", "warning", "info"
_RANK = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    collection: str | None
    id: str | None
    field: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "collection": self.collection,
            "id": self.id,
            "field": self.field,
            "message": self.message,
        }


@dataclass
class Ctx:
    store: Repository
    settings: dict
    data: dict[str, list[dict]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.ids = {coll: {r["id"] for r in rows if isinstance(r.get("id"), str)} for coll, rows in self.data.items()}

    def rows(self, coll: str) -> list[dict]:
        return self.data.get(coll, [])


def _load(store: Repository) -> Ctx:
    data = {name: store.list(name) for name in TABLES if name != "settings"}
    return Ctx(store, effective_settings(store.settings()), data)


def _filled(value: Any) -> bool:
    return value is not None and value != ""


# ---------------------------------------------------------------------------------------------- 1. storage types
def rule_type_misplaced(ctx: Ctx) -> Iterable[Finding]:
    for coll, rows in ctx.data.items():
        table = TABLES[coll]
        for rec in rows:
            for key, value in rec.items():
                col = table.column(key)
                if col is not None and value is not None and not fits(col.type, value):
                    yield Finding(
                        "type.misplaced", ERROR, coll, rec.get("id"), key, "value does not fit the column type"
                    )


# ---------------------------------------------------------------------------------------------- 2. strict model
# Stored keys removed before the strict model pass, per collection, besides the server-owned id/created/updated.
# Empty: every other stored key is either a model field or an extra (``extra="allow"``) that the models accept.
IGNORED_KEYS: dict[str, tuple[str, ...]] = {}
_SERVER_KEYS = ("id", "created", "updated")

# date columns of the tables without a CRUD model: (kind, columns); kind "date" = ISO date, "datetime" = local datetime
_DATE_COLUMNS: dict[str, tuple[str, tuple[str, ...]]] = {
    "allocations": ("date", ("start", "end", "cancelled_from")),
    "trips": ("datetime", ("start", "end")),  # business trips carry YYYY-MM-DDTHH:MM, not a plain date
    "incoming_offers": ("date", ("received_on", "valid_until")),
    "users": ("date", ("tracking_start",)),
}


def rule_model_invalid(ctx: Ctx) -> Iterable[Finding]:
    for coll, (_, in_model) in RECORD_MODELS.items():
        drop = _SERVER_KEYS + IGNORED_KEYS.get(coll, ())
        for rec in ctx.rows(coll):
            body = {k: val for k, val in rec.items() if k not in drop}
            try:
                in_model.model_validate(body)
            except ValidationError as err:
                for e in err.errors():
                    loc = ".".join(str(p) for p in e["loc"]) or None
                    yield Finding("model.invalid", WARNING, coll, rec.get("id"), loc, e["msg"])


def rule_model_date_format(ctx: Ctx) -> Iterable[Finding]:
    for coll, (kind, columns) in _DATE_COLUMNS.items():
        check = v.iso_date if kind == "date" else v.local_datetime
        for rec in ctx.rows(coll):
            for col in columns:
                value = rec.get(col)
                if not isinstance(value, str) or value == "":
                    continue
                try:
                    check(value)
                except ValueError:
                    yield Finding("model.date_format", WARNING, coll, rec.get("id"), col, "not a valid date")


# ---------------------------------------------------------------------------------------------- 3. references
REF_TARGETS = {
    "customer_id": "customers",
    "project_id": "projects",
    "task_id": "tasks",
    "invoice_id": "invoices",
    "offer_id": "offers",
    "contract_id": "contracts",
    "supplier_id": "suppliers",
    "trip_id": "trips",
    "allocation_id": "allocations",
    "incoming_offer_id": "incoming_offers",
    "expense_id": "expenses",
    "source_id": "calendar_sources",
    "token_id": "api_tokens",
    "user_id": "users",
    "login_user_id": "users",
    "decided_by": "users",
    "created_by": "users",
    "import_batch": "import_batches",
    "batch_id": "import_batches",
    "template_id": "templates",
}
REF_LISTS = {"member_ids": "users", "user_ids": "users"}
_DANGLING_ERRORS = {
    ("entries", "invoice_id"),
    ("invoices", "customer_id"),
    ("expenses", "trip_id"),
    ("receipt_scans", "expense_id"),
    ("document_scans", "record_id"),
}
_AUDIT_COLUMNS = ("token_id", "user_id")


def _dangling(coll: str, col: str) -> tuple[str, str]:
    if coll == "audit_log" and col in _AUDIT_COLUMNS:
        return "ref.dangling_audit", INFO
    return "ref.dangling", ERROR if (coll, col) in _DANGLING_ERRORS else WARNING


def rule_ref_dangling(ctx: Ctx) -> Iterable[Finding]:
    builtin = set(documents.builtin_templates())
    for coll, rows in ctx.data.items():
        table = TABLES[coll]
        pairs: list[tuple[str, str | None]] = [(c, t) for c, t in REF_TARGETS.items() if table.column(c)]
        if coll == "document_scans":
            pairs.append(("record_id", None))  # the target collection is named by record_coll
        for rec in rows:
            for col, target in pairs:
                value = rec.get(col)
                if not _filled(value) or not isinstance(value, str):
                    continue
                if col == "record_id":
                    target = rec.get("record_coll")
                    if target not in ctx.ids:
                        continue  # state.document_scan_record reports a bad record_coll
                if col == "template_id" and value in builtin:
                    continue
                if value not in ctx.ids.get(target or "", set()):
                    code, severity = _dangling(coll, col)
                    yield Finding(code, severity, coll, rec.get("id"), col, f"points to a missing {target} record")
            for col, target in REF_LISTS.items():
                values = rec.get(col)
                if not isinstance(values, list) or not table.column(col):
                    continue
                for value in values:
                    if isinstance(value, str) and value and value not in ctx.ids[target]:
                        yield Finding(
                            "ref.dangling", WARNING, coll, rec.get("id"), col, f"points to a missing {target} record"
                        )


def rule_ref_project_customer(ctx: Ctx) -> Iterable[Finding]:
    projects = {p["id"]: p for p in ctx.rows("projects") if isinstance(p.get("id"), str)}
    for coll, rows in ctx.data.items():
        table = TABLES[coll]
        if not (table.column("project_id") and table.column("customer_id")):
            continue
        for rec in rows:
            project = projects.get(rec.get("project_id"))
            pc, rc = (project or {}).get("customer_id"), rec.get("customer_id")
            if project and _filled(pc) and _filled(rc) and pc != rc:
                yield Finding(
                    "ref.self_project_customer",
                    WARNING,
                    coll,
                    rec.get("id"),
                    "customer_id",
                    "differs from the project's customer",
                )


# ---------------------------------------------------------------------------------------------- 4. numbering
_DOC_COLLS = ("invoices", "offers")
_FINAL_INVOICE = ("sent", "paid", "cancelled")


def _day(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _scopes(ctx: Ctx) -> dict[str, tuple[str, ...]]:
    return {"shared": _DOC_COLLS} if ctx.settings.get("numbering_shared") else {c: (c,) for c in _DOC_COLLS}


def _numbered(ctx: Ctx, colls: Sequence[str]) -> list[tuple[str, dict]]:
    return [(c, r) for c in colls for r in ctx.rows(c) if isinstance(r.get("number"), str) and r["number"]]


def rule_numbering_duplicates(ctx: Ctx) -> Iterable[Finding]:
    for colls in _scopes(ctx).values():
        docs = _numbered(ctx, colls)
        exact: dict[str, int] = {}
        norm: dict[str, set[str]] = {}
        for _, d in docs:
            exact[d["number"]] = exact.get(d["number"], 0) + 1
            norm.setdefault(normalize_text(d["number"]), set()).add(d["number"])
        for coll, d in docs:
            n = d["number"]
            if exact[n] > 1:
                yield Finding(
                    "numbering.duplicate", ERROR, coll, d.get("id"), "number", f"number {n} is used more than once"
                )
            if len(norm[normalize_text(n)]) > 1:
                yield Finding(
                    "numbering.duplicate_similar",
                    WARNING,
                    coll,
                    d.get("id"),
                    "number",
                    f"number {n} equals another number apart from spaces",
                )


def rule_numbering_state(ctx: Ctx) -> Iterable[Finding]:
    for coll in _DOC_COLLS:
        for d in ctx.rows(coll):
            has_number = _filled(d.get("number"))
            if d.get("status") == "draft" and has_number:
                yield Finding(
                    "numbering.draft_with_number", ERROR, coll, d.get("id"), "number", "a draft must not have a number"
                )
            if coll == "invoices" and d.get("status") in _FINAL_INVOICE and not has_number:
                yield Finding(
                    "numbering.final_without_number",
                    ERROR,
                    coll,
                    d.get("id"),
                    "number",
                    "a finalized invoice has no number",
                )


def _patterns(ctx: Ctx) -> dict[str, tuple[Pattern, str]] | None:
    out = {}
    for coll, key in (("invoices", "invoice"), ("offers", "offer")):
        try:
            out[coll] = (parse_pattern(ctx.settings[f"{key}_pattern"], strict=False), ctx.settings[f"{key}_reset"])
        except (PatternError, KeyError):
            return None
    return out


def _sequence_numbers(ctx: Ctx) -> dict[tuple[str, str], list[int]] | None:
    """(counter group, period) -> sequence numbers that the current pattern produced for the document's own date."""
    patterns = _patterns(ctx)
    if patterns is None:
        return None
    out: dict[tuple[str, str], list[int]] = {}
    for group, colls in _scopes(ctx).items():
        for coll, d in _numbered(ctx, colls):
            day = _day(d.get("date"))
            pattern, reset = patterns[coll]
            n = pattern.match(d["number"])
            if day is not None and n and pattern.date_consistent(d["number"], day):
                out.setdefault((group, period_key(reset, day)), []).append(n)
    return out


def rule_numbering_sequence_behind(ctx: Ctx) -> Iterable[Finding]:
    seq = _sequence_numbers(ctx)
    if seq is None:
        return
    for row in ctx.rows("number_sequences"):
        highest = max(seq.get((str(row.get("doc_type")), str(row.get("period"))), []), default=0)
        last = row.get("last_value")
        if highest and (not isinstance(last, int) or last < highest):
            yield Finding(
                "numbering.sequence_behind",
                WARNING,
                "number_sequences",
                row.get("id"),
                "last_value",
                f"the counter is behind the highest number {highest}",
            )


def _ranges(missing: list[int]) -> str:
    out, start, prev = [], missing[0], missing[0]
    for n in [*missing[1:], None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(out)


def rule_numbering_gap(ctx: Ctx) -> Iterable[Finding]:
    seq = _sequence_numbers(ctx)
    for (group, period), numbers in sorted((seq or {}).items()):
        have = set(numbers)
        missing = [n for n in range(min(have), max(have) + 1) if n not in have]
        if missing:
            yield Finding(
                "numbering.gap",
                INFO,
                group,
                period,
                None,
                f"sequence numbers missing in period {period}: {_ranges(missing)}",
            )


# ---------------------------------------------------------------------------------------------- 5. money
_MONEY_COLUMNS = (
    "net", "vat", "rate", "deductible", "vat_deductible", "net_total", "gross_total", "allowance_total",
    "mileage_total", "default_rate", "cost_rate",
)  # fmt: skip


def rule_money_line_amount(ctx: Ctx) -> Iterable[Finding]:
    for coll in _DOC_COLLS:
        for d in ctx.rows(coll):
            lines = d.get("lines")
            if d.get("totals_unverified") or not isinstance(lines, list):
                continue
            for i, ln in enumerate(lines):
                if not isinstance(ln, dict) or not _filled(ln.get("amount")):
                    continue
                want = money.line_amount(ln.get("qty"), ln.get("price"))
                try:
                    same = money.dec(ln["amount"], default=None) == want
                except ValueError:
                    same = False
                if not same:
                    yield Finding(
                        "money.line_amount",
                        ERROR,
                        coll,
                        d.get("id"),
                        f"lines.{i}.amount",
                        "amount differs from qty * price",
                    )


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return money.dec(a, default=None) == money.dec(b, default=None)
    except ValueError:
        return False


def rule_money_expense_itemised(ctx: Ctx) -> Iterable[Finding]:
    for rec in ctx.rows("expenses"):
        lines = rec.get("lines")
        if not lines or not isinstance(lines, list):
            continue
        rid = rec.get("id")
        if not all(isinstance(x, dict) for x in lines):
            yield Finding("money.expense_itemised", ERROR, "expenses", rid, "lines", "lines cannot be recomputed")
            continue
        try:
            want = calc.itemise_expense(copy.deepcopy(rec))
        except (AttributeError, TypeError, ValueError):
            yield Finding("money.expense_itemised", ERROR, "expenses", rid, "lines", "lines cannot be recomputed")
            continue
        for key in ("net", "vat", "vat_rate", "deductible"):
            if not _same(rec.get(key), want.get(key)):
                yield Finding("money.expense_itemised", ERROR, "expenses", rid, key, "differs from the lines")
        for i, (have_line, want_line) in enumerate(zip(lines, want["lines"])):
            for key in ("net", "vat"):
                if not _same(have_line.get(key), want_line.get(key)):
                    yield Finding("money.expense_itemised", ERROR, "expenses", rid, f"lines.{i}.{key}", "differs")


def _not_finite(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return False
    if not isinstance(value, (int, float)):
        return False
    return not math.isfinite(value) or abs(value) > v.MAX_MONEY


def rule_money_not_finite(ctx: Ctx) -> Iterable[Finding]:
    for coll, rows in ctx.data.items():
        for rec in rows:
            for col in _MONEY_COLUMNS:
                if TABLES[coll].column(col) and _not_finite(rec.get(col)):
                    yield Finding(
                        "money.not_finite", ERROR, coll, rec.get("id"), col, "not finite or beyond the allowed amount"
                    )


# ---------------------------------------------------------------------------------------------- 6. logical state
# derived from the "order" guards, so the check and the database guard cannot drift; trips carry local datetimes
_PERIODS: tuple[tuple[str, tuple[str, str], str], ...] = tuple(
    (g.table, (g.columns[0], g.columns[1]), "datetime" if g.table == "trips" else "date")
    for g in GUARDS
    if g.kind == "order"
)


def _valid(check: Callable[[str], str], value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        check(value)
    except ValueError:
        return False
    return True


def rule_state_period_reversed(ctx: Ctx) -> Iterable[Finding]:
    for coll, (a, b), kind in _PERIODS:
        check = v.iso_date if kind == "date" else v.local_datetime
        for rec in ctx.rows(coll):
            if _valid(check, rec.get(a)) and _valid(check, rec.get(b)) and rec[b] < rec[a]:
                yield Finding("state.period_reversed", WARNING, coll, rec.get("id"), b, f"{b} is before {a}")


# ---------------------------------------------------------------------------------------------- 7. database guards
# The tables and columns come from schema.GUARDS (the triggers), so check and guard cannot drift.
def _guard_columns(kind: str) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {}
    for g in GUARDS:
        if g.kind == kind:
            out.setdefault(g.table, []).extend(g.columns)
    return {t: tuple(c) for t, c in out.items()}


GUARD_DATE_COLUMNS = _guard_columns("date")
GUARD_NON_NEGATIVE_COLUMNS = _guard_columns("non_negative")
_GUARD_DATE = re.compile(r"[0-9]{4}-[01][0-9]-[0-3][0-9]")


def rule_format_date(ctx: Ctx) -> Iterable[Finding]:
    for coll, columns in GUARD_DATE_COLUMNS.items():
        for rec in ctx.rows(coll):
            for col in columns:
                value = rec.get(col)
                if _filled(value) and not (isinstance(value, str) and _GUARD_DATE.fullmatch(value)):
                    yield Finding("format.date", ERROR, coll, rec.get("id"), col, "not an ISO date (YYYY-MM-DD)")


def rule_range_negative(ctx: Ctx) -> Iterable[Finding]:
    for coll, columns in GUARD_NON_NEGATIVE_COLUMNS.items():
        for rec in ctx.rows(coll):
            for col in columns:
                value = rec.get(col)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
                    yield Finding("range.negative", ERROR, coll, rec.get("id"), col, "must not be negative")


# ---- the row-local guard kinds (docs/specs/db-guards-v2.md): one generic rule over schema.GUARDS, read at run time
_set = _schema._is_set  # the one Python evaluator of IsSet (schema.SQL_BLANK whitespace, like the dialect's trim)


def _julian_day(value: Any) -> int | None:
    """Day number of a `YYYY-MM-DD` text as SQLite's `julianday` computes it (day 1-31 is normalised, so 2026-02-30 is
    valid); None when the value is not such a text."""
    if not isinstance(value, str) or not _GUARD_DATE.fullmatch(value):
        return None
    y, m, d = int(value[:4]), int(value[5:7]), int(value[8:])
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    y -= m <= 2  # days from civil (proleptic Gregorian, any year)
    era, yoe = divmod(y, 400)
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy


def _outside_range(g: Any, value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, str):  # SQLite orders text after every number
        return g.hi is not None
    if not isinstance(value, (int, float)):
        return False
    return (g.lo is not None and (value <= g.lo if g.lo_exclusive else value < g.lo)) or (
        g.hi is not None and value > g.hi
    )


def _holds(cond: Any, row: dict) -> bool:
    return cond.holds(row)


def guard_violated(g: Any, row: dict) -> bool:
    """Whether a stored row breaks a row-local guard: the Python twin of `Dialect.guard_bad_sql` (same NULL, '' and
    whitespace handling), built from the guard's own parameters and conditions."""
    cols = g.columns
    if g.kind == "one_of":
        return _set(row.get(cols[0])) and row.get(cols[0]) not in g.values
    if g.kind == "exactly_one":
        return _set(row.get(cols[0])) == _set(row.get(cols[1]))
    if g.kind == "required":
        return not _set(row.get(cols[0]))
    if g.kind == "max_days":
        start, end = _julian_day(row.get(cols[0])), _julian_day(row.get(cols[1]))
        return start is not None and end is not None and end - start > g.days
    if g.kind == "range":
        return _outside_range(g, row.get(cols[0]))
    if g.kind == "implies":
        return _holds(g.if_, row) and not _holds(g.then, row)
    raise ValueError(f"unknown guard kind {g.kind!r}")


def rule_guard_row_local(ctx: Ctx) -> Iterable[Finding]:
    for g in _schema.GUARDS:  # read at run time: guards added later (or patched in tests) are covered
        if g.kind not in ROW_LOCAL_KINDS:
            continue
        field_name = ",".join(g.read_columns)
        for rec in ctx.rows(g.table):
            if guard_violated(g, rec):
                yield Finding(g.code, ERROR, g.table, rec.get("id"), field_name, f"violates the {g.kind} rule")


def rule_state_no_active_boss(ctx: Ctx) -> Iterable[Finding]:
    if not any(u.get("role") == "boss" and u.get("active") is not False for u in ctx.rows("users")):
        yield Finding("state.no_active_boss", ERROR, "users", None, "role", "no active user with the role boss")


def rule_state_receipt_booked(ctx: Ctx) -> Iterable[Finding]:
    for rec in ctx.rows("receipt_scans"):
        if rec.get("status") == "booked" and not _filled(rec.get("expense_id")):
            yield Finding(
                "state.receipt_booked_without_expense",
                ERROR,
                "receipt_scans",
                rec.get("id"),
                "expense_id",
                "booked without an expense",
            )


def rule_state_document_scan(ctx: Ctx) -> Iterable[Finding]:
    for rec in ctx.rows("document_scans"):
        if rec.get("status") != "added":
            continue
        if not (_filled(rec.get("record_id")) and rec.get("record_coll") in _DOC_COLLS):
            yield Finding(
                "state.document_scan_record",
                ERROR,
                "document_scans",
                rec.get("id"),
                "record_id",
                "added without a valid offer or invoice",
            )


def rule_state_timer(ctx: Ctx) -> Iterable[Finding]:
    for rec in ctx.rows("timers"):
        if rec.get("running") is True and not _filled(rec.get("started")):
            yield Finding(
                "state.timer_running_without_start",
                WARNING,
                "timers",
                rec.get("id"),
                "started",
                "running without a start time",
            )


# ---------------------------------------------------------------------------------------------- 7. files
_ATTACHMENT_FIELDS = {
    "expenses": "attachment",
    "incoming_offers": "attachment",
    "receipt_scans": "attachment",
    "document_scans": "attachment",
    "offers": "original_pdf",
    "invoices": "original_pdf",
}


def rule_file_missing(ctx: Ctx) -> Iterable[Finding]:
    for coll, col in _ATTACHMENT_FIELDS.items():
        for rec in ctx.rows(coll):
            name = rec.get(col)
            if _filled(name) and isinstance(name, str) and ctx.store.attachment_path(name) is None:
                yield Finding("file.missing", ERROR, coll, rec.get("id"), col, "the file does not exist")


def rule_file_orphan(ctx: Ctx) -> Iterable[Finding]:
    used = {r.get(col) for coll, col in _ATTACHMENT_FIELDS.items() for r in ctx.rows(coll)}
    used.add(ctx.settings.get("logo"))  # the letterhead logo is an attachment named by the settings
    for name in ctx.store.list_attachments():
        if name not in used:
            yield Finding("file.orphan", INFO, None, name, None, "no record refers to this file")


def rule_file_sync_conflict(ctx: Ctx) -> Iterable[Finding]:
    for name in ctx.store.conflicts():
        yield Finding("file.sync_conflict", WARNING, None, None, None, f"sync conflict copy: {name}")


RULES: list[Callable[[Ctx], Iterable[Finding]]] = [
    rule_type_misplaced,
    rule_model_invalid,
    rule_model_date_format,
    rule_ref_dangling,
    rule_ref_project_customer,
    rule_numbering_duplicates,
    rule_numbering_state,
    rule_numbering_sequence_behind,
    rule_numbering_gap,
    rule_money_line_amount,
    rule_money_expense_itemised,
    rule_money_not_finite,
    rule_state_period_reversed,
    rule_format_date,
    rule_range_negative,
    rule_guard_row_local,
    rule_state_no_active_boss,
    rule_state_receipt_booked,
    rule_state_document_scan,
    rule_state_timer,
    rule_file_missing,
    rule_file_orphan,
    rule_file_sync_conflict,
]


class IntegrityService:
    def __init__(self, store: Repository):
        self.store = store

    def run(self, only: Sequence[str] = ()) -> list[Finding]:
        """All findings (or those whose code starts with one of ``only``), most severe first. Reads one snapshot."""
        found: list[Finding] = []
        with self.store.transaction():  # one consistent snapshot; nothing is written
            ctx = _load(self.store)
            for rule in RULES:
                try:
                    found.extend(rule(ctx))
                except Exception as e:  # a bug in one rule must not hide the other findings
                    msg = f"rule {rule.__name__} raised {type(e).__name__}"
                    found.append(Finding("check.crashed", ERROR, None, None, None, msg))
        if only:
            found = [f for f in found if f.code.startswith(tuple(only))]
        return sorted(found, key=lambda f: (_RANK[f.severity], f.code, f.collection or "", f.id or ""))


def summary(findings: Iterable[Finding]) -> dict[str, int]:
    counts = {ERROR: 0, WARNING: 0, INFO: 0}
    for f in findings:
        counts[f.severity] += 1
    return {"errors": counts[ERROR], "warnings": counts[WARNING], "info": counts[INFO]}
