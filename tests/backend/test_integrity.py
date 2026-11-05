"""Stored data check (`permita38 check-db`, docs/specs/db-integrity-check.md).

Broken rows are planted with `store.save` directly (this bypasses the services, like bad data in the field)."""

import hashlib
import json
import sqlite3

import pytest

from permita38.cli.main import main
from permita38.core.database import head_id
from permita38.core.repository.schema import GUARDS
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository
from permita38.core.service import integrity
from permita38.core.service.integrity import IntegrityService

from .test_allocations_api import END as ALLOC_END
from .test_allocations_api import START as ALLOC_START
from .test_document_scans_service import INVOICE as SCAN_INVOICE
from .test_migrations import legacy_db
from .test_receipts_api import SINGLE
from .test_trips_api import trip_body


@pytest.fixture
def store(tmp_path):
    repo = SQLiteRepository(tmp_path / "data")
    repo.startup()
    repo.save("users", {"id": "boss01", "username": "boss", "name": "Demo Boss", "role": "boss"})
    yield repo
    repo.close()


def found(store, *prefixes):
    """(code, collection, id, field) of the findings; optionally only those starting with a prefix."""
    return [(f.code, f.collection, f.id, f.field) for f in IntegrityService(store).run(prefixes)]


def doc(coll, rid, **kw):
    return {"id": rid, "customer_id": "cust01", "status": "draft", "date": "2026-03-01", "lines": [], **kw}


def drop_guards(store):
    """Remove the database guards, to store the broken rows that check-db must report (grandfathered data)."""
    raw = sqlite3.connect(store.data_dir / DB_FILENAME)
    try:
        for (name,) in raw.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'guard_%'"):
            raw.execute(f'DROP TRIGGER "{name}"')
        raw.commit()
    finally:
        raw.close()


def with_customer(store):
    store.save("customers", {"id": "cust01", "name": "Demo Client"})


# ------------------------------------------------------------------ clean database
def test_clean_database_has_no_findings(store):
    assert found(store) == []


def test_empty_boss_list_is_an_error(tmp_path):
    repo = SQLiteRepository(tmp_path / "d")
    repo.startup()
    repo.save("users", {"id": "emp001", "username": "e", "role": "employee"})
    repo.save("users", {"id": "boss02", "username": "b", "role": "boss", "active": False})
    assert found(repo) == [("state.no_active_boss", "users", None, "role")]
    repo.close()


def test_only_an_inactive_boss_is_reported_but_an_active_one_beside_it_is_not(tmp_path):
    repo = SQLiteRepository(tmp_path / "d")
    repo.startup()
    repo.save("users", {"id": "boss02", "username": "b", "role": "boss", "active": False})
    assert found(repo) == [("state.no_active_boss", "users", None, "role")]
    repo.save("users", {"id": "boss03", "username": "c", "role": "boss"})
    assert found(repo) == []
    repo.close()


def test_legacy_role_member_is_not_reported(store):
    store.save("users", {"id": "mem001", "username": "mia", "role": "member"})
    assert found(store) == []


# ------------------------------------------------------------------ 1. types
def test_type_misplaced_and_unknown_keys(store):
    store.save("expenses", {"id": "exp001", "date": "2026-03-01", "net": "abc", "future_field": "x"})
    assert found(store, "type.") == [("type.misplaced", "expenses", "exp001", "net")]
    # a legitimate non-column key survives untouched
    assert store.get("expenses", "exp001")["future_field"] == "x"


# ------------------------------------------------------------------ 2. models
def test_model_invalid_reports_field_and_never_the_value(store):
    with_customer(store)
    store.save("invoices", doc("invoices", "inv001", status="bogus-state"))
    rows = [f for f in IntegrityService(store).run(["model."])]
    assert [(f.code, f.severity, f.collection, f.id, f.field) for f in rows] == [
        ("model.invalid", "warning", "invoices", "inv001", "status")
    ]
    assert "bogus-state" not in rows[0].message


def test_model_date_format_on_tables_without_crud_model(store):
    drop_guards(store)
    store.save("allocations", {"id": "alloc01", "user_id": "boss01", "start": "2026-02-30", "end": "2026-03-01"})
    store.save("trips", {"id": "trip01", "start": "2026-03-01", "end": "2026-03-02T10:00"})  # start lacks the time
    store.save("trips", {"id": "trip02", "start": "2026-03-01T08:00", "end": "2026-03-02T10:00"})
    store.save("incoming_offers", {"id": "inoff01", "received_on": "yesterday"})
    store.save("users", {"id": "emp001", "username": "emp", "role": "employee", "tracking_start": "01.03.2026"})
    assert sorted(found(store, "model.")) == [
        ("model.date_format", "allocations", "alloc01", "start"),
        ("model.date_format", "incoming_offers", "inoff01", "received_on"),
        ("model.date_format", "trips", "trip01", "start"),
        ("model.date_format", "users", "emp001", "tracking_start"),
    ]


def test_entry_clock_times_are_not_dates(store):
    store.save(
        "entries",
        {"id": "ent001", "user_id": "boss01", "date": "2026-03-01", "minutes": 480, "start": "22:00", "end": "06:00"},
    )
    assert found(store) == []


# ------------------------------------------------------------------ database guards (format.date, range.negative)
def test_format_date_reports_only_filled_non_iso_values(store):
    drop_guards(store)
    store.save("projects", {"id": "proj01", "name": "P", "start": "2026-03-01", "end": "01.03.2026"})
    store.save("projects", {"id": "proj02", "name": "Q", "start": "", "end": None})
    store.save("expenses", {"id": "exp001", "date": "2026-3-1", "paid_on": "2026-03-01"})
    store.save("entries", {"id": "ent001", "user_id": "boss01", "date": "2026-03-01", "minutes": 5})
    assert sorted(found(store, "format.date")) == [
        ("format.date", "expenses", "exp001", "date"),
        ("format.date", "projects", "proj01", "end"),
    ]
    assert {f.severity for f in IntegrityService(store).run(["format.date"])} == {"error"}


def test_range_negative_reports_only_values_below_zero(store):
    drop_guards(store)
    store.save("entries", {"id": "ent001", "user_id": "boss01", "date": "2026-03-01", "minutes": -1, "rate": 0})
    store.save("entries", {"id": "ent002", "user_id": "boss01", "date": "2026-03-01", "minutes": 0, "rate": 10.5})
    store.save("customers", {"id": "cust01", "name": "Demo Client", "rate": -0.5})
    assert sorted(found(store, "range.negative")) == [
        ("range.negative", "customers", "cust01", "rate"),
        ("range.negative", "entries", "ent001", "minutes"),
    ]
    assert {f.severity for f in IntegrityService(store).run(["range.negative"])} == {"error"}


def test_guard_rules_use_exactly_the_guard_columns():
    for kind, derived in (
        ("date", integrity.GUARD_DATE_COLUMNS),
        ("non_negative", integrity.GUARD_NON_NEGATIVE_COLUMNS),
    ):
        expected = {(g.table, c) for g in GUARDS if g.kind == kind for c in g.columns}
        assert {(t, c) for t, cols in derived.items() for c in cols} == expected
        assert expected  # the guard list is not empty
    pairs = {(g.table, g.columns) for g in GUARDS if g.kind == "order"}
    assert {(t, cols) for t, cols, _ in integrity._PERIODS} == pairs


def test_period_reversed_covers_the_offer_service_period(store):
    drop_guards(store)
    with_customer(store)
    store.save("offers", doc("offers", "off001", service_from="2026-02-10", service_to="2026-02-01"))
    assert found(store, "state.period") == [("state.period_reversed", "offers", "off001", "service_to")]


# ------------------------------------------------------------------ 3. references
def test_dangling_errors(store):
    store.save("invoices", doc("invoices", "inv001"))  # customer cust01 does not exist
    store.save("entries", {"id": "ent001", "user_id": "boss01", "date": "2026-03-01", "invoice_id": "inv999"})
    store.save("expenses", {"id": "exp001", "date": "2026-03-01", "trip_id": "trip99"})
    store.save("receipt_scans", {"id": "rcpt01", "status": "flagged", "expense_id": "exp999"})
    store.save("document_scans", {"id": "dscan01", "status": "flagged", "record_coll": "offers", "record_id": "off999"})
    got = [f for f in IntegrityService(store).run(["ref."])]
    assert {(f.code, f.severity, f.collection, f.id, f.field) for f in got} == {
        ("ref.dangling", "error", "invoices", "inv001", "customer_id"),
        ("ref.dangling", "error", "entries", "ent001", "invoice_id"),
        ("ref.dangling", "error", "expenses", "exp001", "trip_id"),
        ("ref.dangling", "error", "receipt_scans", "rcpt01", "expense_id"),
        ("ref.dangling", "error", "document_scans", "dscan01", "record_id"),
    }


def test_dangling_warnings_lists_and_templates(store):
    store.save("tasks", {"id": "task01", "project_id": "proj99", "name": "T"})
    store.save("projects", {"id": "proj01", "name": "P", "member_ids": ["boss01", "ghost01"]})
    store.save("invoices", doc("invoices", "inv001", customer_id=None, template_id="tpl99999"))
    store.save("invoices", doc("invoices", "inv002", customer_id=None, template_id="classic"))  # built-in id
    store.save("offers", doc("offers", "off001", customer_id="", project_id=""))  # empty means not set
    got = {(f.code, f.severity, f.collection, f.id, f.field) for f in IntegrityService(store).run(["ref."])}
    assert got == {
        ("ref.dangling", "warning", "tasks", "task01", "project_id"),
        ("ref.dangling", "warning", "projects", "proj01", "member_ids"),
        ("ref.dangling", "warning", "invoices", "inv001", "template_id"),
    }


def test_dangling_audit_is_info(store):
    store.save("audit_log", {"id": "aud001", "token_id": "tok999", "user_id": "gone01", "at": 1, "status": 200})
    got = [(f.code, f.severity, f.field) for f in IntegrityService(store).run(["ref."])]
    assert sorted(got) == [("ref.dangling_audit", "info", "token_id"), ("ref.dangling_audit", "info", "user_id")]


def test_document_scan_record_points_into_the_named_collection(store):
    with_customer(store)
    store.save("invoices", doc("invoices", "inv001"))
    store.save("document_scans", {"id": "dscan01", "status": "added", "record_coll": "invoices", "record_id": "inv001"})
    store.save("document_scans", {"id": "dscan02", "status": "added", "record_coll": "offers", "record_id": "inv001"})
    assert found(store, "ref.") == [("ref.dangling", "document_scans", "dscan02", "record_id")]


def test_project_customer_mismatch(store):
    store.save("customers", {"id": "cust01", "name": "A"})
    store.save("customers", {"id": "cust02", "name": "B"})
    store.save("projects", {"id": "proj01", "name": "P", "customer_id": "cust02"})
    store.save("projects", {"id": "proj02", "name": "Q"})  # no customer: nothing to compare
    store.save("contracts", {"id": "con001", "start": "2026-01-01", "customer_id": "cust01", "project_id": "proj01"})
    store.save("contracts", {"id": "con002", "start": "2026-01-01", "customer_id": "cust01", "project_id": "proj02"})
    store.save("contracts", {"id": "con003", "start": "2026-01-01", "customer_id": "cust02", "project_id": "proj01"})
    assert found(store, "ref.") == [("ref.self_project_customer", "contracts", "con001", "customer_id")]


def test_project_without_customer_is_never_a_mismatch(store):
    store.save("customers", {"id": "cust01", "name": "A"})
    store.save("projects", {"id": "proj02", "name": "Q"})
    store.save("contracts", {"id": "con002", "start": "2026-01-01", "customer_id": "cust01", "project_id": "proj02"})
    assert found(store, "ref.") == []


# ------------------------------------------------------------------ 4. numbering
def test_duplicate_and_similar_numbers(store):
    drop_guards(store)
    with_customer(store)
    store.save("invoices", doc("invoices", "inv001", status="sent", number="RE-2026-0001"))
    store.save("invoices", doc("invoices", "inv002", status="sent", number="RE-2026-0001"))
    store.save("invoices", doc("invoices", "inv003", status="sent", number="RE-2026-0002"))
    store.save("invoices", doc("invoices", "inv004", status="sent", number="RE-2026-0002 "))
    got = sorted(found(store, "numbering.duplicate"))
    assert got == [
        ("numbering.duplicate", "invoices", "inv001", "number"),
        ("numbering.duplicate", "invoices", "inv002", "number"),
        ("numbering.duplicate_similar", "invoices", "inv003", "number"),
        ("numbering.duplicate_similar", "invoices", "inv004", "number"),
    ]


def test_same_number_in_offers_and_invoices_depends_on_shared_numbering(store):
    with_customer(store)
    store.save("invoices", doc("invoices", "inv001", status="sent", number="RE-2026-0001"))
    store.save("offers", doc("offers", "off001", status="sent", number="RE-2026-0001"))
    assert found(store, "numbering.duplicate") == []
    store.save_settings({**store.settings(), "numbering_shared": True})
    assert sorted(found(store, "numbering.duplicate")) == [
        ("numbering.duplicate", "invoices", "inv001", "number"),
        ("numbering.duplicate", "offers", "off001", "number"),
    ]


def test_draft_with_number_and_final_without_number(store):
    with_customer(store)
    store.save("invoices", doc("invoices", "inv001", status="draft", number="RE-2026-0001"))
    store.save("offers", doc("offers", "off001", status="draft", number="AN-2026-0001"))
    store.save("invoices", doc("invoices", "inv002", status="sent"))
    store.save("invoices", doc("invoices", "inv003", status="paid"))
    store.save("invoices", doc("invoices", "inv004", status="cancelled"))
    store.save("offers", doc("offers", "off002", status="sent"))  # only invoices need a number
    assert sorted(found(store, "numbering.")) == [
        ("numbering.draft_with_number", "invoices", "inv001", "number"),
        ("numbering.draft_with_number", "offers", "off001", "number"),
        ("numbering.final_without_number", "invoices", "inv002", "number"),
        ("numbering.final_without_number", "invoices", "inv003", "number"),
        ("numbering.final_without_number", "invoices", "inv004", "number"),
    ]


def test_cancelled_numbered_invoice_and_credit_note_are_fine(store):
    with_customer(store)
    store.save("invoices", doc("invoices", "inv001", status="cancelled", number="RE-2026-0001"))
    credit = doc("invoices", "inv002", status="sent", number="RE-2026-0002")
    credit["lines"] = [{"description": "Credit", "qty": 1, "price": -10.0, "amount": -10.0}]
    store.save("invoices", credit)
    assert found(store) == []


def test_sequence_behind_and_gap(store):
    with_customer(store)
    for rid, n in (("inv001", 1), ("inv002", 2), ("inv003", 5), ("inv004", 6), ("inv005", 9)):
        store.save("invoices", doc("invoices", rid, status="sent", number=f"RE-2026-{n:04d}"))
    # a number of another format and one of another year are not part of this period's sequence
    store.save("invoices", doc("invoices", "inv006", status="sent", number="OLD/77"))
    store.save("number_sequences", {"id": "seq001", "doc_type": "invoices", "period": "2026", "last_value": 6})
    store.save("number_sequences", {"id": "seq002", "doc_type": "invoices", "period": "2025", "last_value": 0})
    got = IntegrityService(store).run(["numbering."])
    assert [(f.code, f.severity, f.collection, f.id, f.field) for f in got] == [
        ("numbering.sequence_behind", "warning", "number_sequences", "seq001", "last_value"),
        ("numbering.gap", "info", "invoices", "2026", None),
    ]
    assert got[1].message == "sequence numbers missing in period 2026: 3-4, 7-8"


def test_sequence_not_behind_when_counter_is_current(store):
    with_customer(store)
    store.save("invoices", doc("invoices", "inv001", status="sent", number="RE-2026-0001"))
    store.save("number_sequences", {"id": "seq001", "doc_type": "invoices", "period": "2026", "last_value": 1})
    assert found(store) == []


# ------------------------------------------------------------------ 5. money
def test_line_amount_mismatch_and_totals_unverified(store):
    with_customer(store)
    bad = doc(
        "invoices",
        "inv001",
        lines=[{"qty": 2, "price": 10.0, "amount": 20.0}, {"qty": 3, "price": 10.0, "amount": 31.0}],
    )
    store.save("invoices", bad)
    store.save("invoices", doc("invoices", "inv002", imported=True, totals_unverified=True, lines=bad["lines"]))
    assert found(store, "money.") == [("money.line_amount", "invoices", "inv001", "lines.1.amount")]


def test_expense_itemised(store):
    line = {"description": "Pens", "gross": 119.0, "vat_rate": 19.0, "net": 100.0, "vat": 19.0, "deductible": 100}
    ok = {
        "id": "exp001",
        "date": "2026-03-01",
        "lines": [line],
        "net": 100.0,
        "vat": 19.0,
        "vat_rate": 19.0,
        "deductible": 100.0,
    }
    store.save("expenses", ok)
    store.save("expenses", {**ok, "id": "exp002", "net": 90.0, "lines": [{**line, "vat": 18.0}]})
    assert sorted(found(store, "money.")) == [
        ("money.expense_itemised", "expenses", "exp002", "lines.0.vat"),
        ("money.expense_itemised", "expenses", "exp002", "net"),
    ]


def test_expense_with_a_non_dict_line_is_one_finding_and_does_not_crash(store):
    store.save("expenses", {"id": "exp003", "date": "2026-03-01", "lines": ["oops", 5], "net": 1.0})
    assert found(store, "money.") == [("money.expense_itemised", "expenses", "exp003", "lines")]


def test_money_not_finite_and_too_large(store):
    store.save("expenses", {"id": "exp001", "date": "2026-03-01", "net": float("nan")})
    store.save("expenses", {"id": "exp002", "date": "2026-03-01", "vat": 2e9})
    store.save("expenses", {"id": "exp003", "date": "2026-03-01", "net": 10.0, "vat": 1.9})
    got = [(c, i, f) for c, _, i, f in found(store, "money.")]
    assert sorted(got) == [("money.not_finite", "exp001", "net"), ("money.not_finite", "exp002", "vat")]


# ------------------------------------------------------------------ 6. logical state
def test_period_reversed(store):
    drop_guards(store)
    with_customer(store)
    store.save("projects", {"id": "proj01", "name": "P", "start": "2026-03-10", "end": "2026-03-01"})
    store.save("projects", {"id": "proj02", "name": "Q", "start": "2026-03-01", "end": "2026-03-01"})
    store.save(
        "absences",
        {"id": "abs001", "user_id": "boss01", "kind": "vacation", "start": "2026-05-02", "end": "2026-05-01"},
    )
    store.save("trips", {"id": "trip01", "start": "2026-03-02T08:00", "end": "2026-03-01T08:00"})
    store.save("invoices", doc("invoices", "inv001", service_from="2026-02-10", service_to="2026-02-01"))
    store.save("allocations", {"id": "alloc01", "user_id": "boss01", "start": "2026-04-02", "end": "2026-04-01"})
    store.save("contracts", {"id": "con001", "start": "2026-04-02", "end": "2026-04-01"})
    store.save(
        "entries",
        {"id": "ent001", "user_id": "boss01", "date": "2026-03-01", "minutes": 480, "start": "22:00", "end": "06:00"},
    )
    got = sorted((c, i, f) for code, c, i, f in found(store, "state.period"))
    assert got == [
        ("absences", "abs001", "end"),
        ("allocations", "alloc01", "end"),
        ("contracts", "con001", "end"),
        ("invoices", "inv001", "service_to"),
        ("projects", "proj01", "end"),
        ("trips", "trip01", "end"),
    ]


def test_receipt_and_document_scan_state(store):
    store.save("expenses", {"id": "exp001", "date": "2026-03-01"})
    store.save("receipt_scans", {"id": "rcpt01", "status": "booked"})
    store.save("receipt_scans", {"id": "rcpt02", "status": "booked", "expense_id": "exp001"})
    store.save("receipt_scans", {"id": "rcpt03", "status": "flagged"})
    store.save("document_scans", {"id": "dscan01", "status": "added"})
    store.save(
        "document_scans", {"id": "dscan02", "status": "added", "record_coll": "customers", "record_id": "cust01"}
    )
    store.save("document_scans", {"id": "dscan03", "status": "flagged"})
    assert sorted(found(store, "state.")) == [
        ("state.document_scan_record", "document_scans", "dscan01", "record_id"),
        ("state.document_scan_record", "document_scans", "dscan02", "record_id"),
        ("state.receipt_booked_without_expense", "receipt_scans", "rcpt01", "expense_id"),
    ]


def test_timer_running_without_start(store):
    store.save("timers", {"id": "boss01", "running": True})
    store.save("timers", {"id": "emp001", "running": True, "started": 1700000000})
    store.save("timers", {"id": "emp002", "running": False})
    assert found(store, "state.") == [("state.timer_running_without_start", "timers", "boss01", "started")]


# ------------------------------------------------------------------ 7. files
def test_files(store):
    name = store.save_attachment("bill.pdf", b"%PDF-1.4 fake")
    orphan = store.save_attachment("lonely.pdf", b"%PDF-1.4 other")
    store.save("expenses", {"id": "exp001", "date": "2026-03-01", "attachment": name})
    store.save("expenses", {"id": "exp002", "date": "2026-03-01", "attachment": "missing1.pdf"})
    store.save("invoices", doc("invoices", "inv001", customer_id=None, original_pdf="missing2.pdf"))
    (store.data_dir / "permita38.sqlite3.sync-conflict-20260301-120000-ABCDEFG").write_bytes(b"x")
    got = sorted(found(store, "file."), key=lambda t: (t[0], t[1] or "", t[2] or ""))
    assert got == [
        ("file.missing", "expenses", "exp002", "attachment"),
        ("file.missing", "invoices", "inv001", "original_pdf"),
        ("file.orphan", None, orphan, None),
        ("file.sync_conflict", None, None, None),
    ]


# ------------------------------------------------------------------ service behaviour
def test_a_crashing_rule_is_one_finding_and_the_rest_still_runs(store, monkeypatch):
    def rule_boom(ctx):
        raise RuntimeError("secret detail")
        yield  # pragma: no cover

    store.save("timers", {"id": "boss01", "running": True})
    monkeypatch.setattr(integrity, "RULES", [rule_boom, integrity.rule_state_timer])
    got = IntegrityService(store).run()
    assert [(f.code, f.severity) for f in got] == [
        ("check.crashed", "error"),
        ("state.timer_running_without_start", "warning"),
    ]
    assert got[0].message == "rule rule_boom raised RuntimeError"


def test_findings_are_sorted_and_only_filters_by_prefix(store):
    with_customer(store)
    store.save("invoices", doc("invoices", "inv001", status="sent"))  # error
    store.save("timers", {"id": "boss01", "running": True})  # warning
    store.save("number_sequences", {"id": "seq001", "doc_type": "invoices", "period": "2026", "last_value": 0})
    assert [f.severity for f in IntegrityService(store).run()] == ["error", "warning"]
    assert found(store, "state.") == [("state.timer_running_without_start", "timers", "boss01", "started")]
    assert [c for c, *_ in found(store, "state.", "numbering.")] == [
        "numbering.final_without_number",
        "state.timer_running_without_start",
    ]


# ------------------------------------------------------------------ false-positive guard
def test_data_made_by_the_services_gives_no_errors_and_no_warnings(app, boss_client, scan_stubs):
    c = boss_client
    services = app.state.services
    receipt_reader, doc_reader = scan_stubs
    me = c.get("/api/auth/me").json()
    cust = c.post("/api/customers", json={"name": "Demo Client", "rate": 100}).json()
    proj = c.post("/api/projects", json={"name": "Demo Project", "customer_id": cust["id"]}).json()
    task = c.post("/api/tasks", json={"project_id": proj["id"], "name": "Task"}).json()
    c.post(
        "/api/entries",
        json={
            "user_id": me["id"],
            "date": "2026-03-02",
            "minutes": 150,
            "project_id": proj["id"],
            "task_id": task["id"],
        },
    )
    c.post(
        "/api/entries",
        json={"user_id": me["id"], "date": "2026-03-03", "minutes": 60, "start": "22:00", "end": "06:00"},
    )
    inv = c.post("/api/invoices/from-time", json={"customer_id": cust["id"]}).json()
    assert c.post(f"/api/invoices/{inv['id']}/finalize").status_code == 200
    draft = c.post(
        "/api/invoices", json={"customer_id": cust["id"], "lines": [{"description": "X", "qty": 1, "price": 10}]}
    )
    assert draft.status_code == 200
    off = c.post(
        "/api/offers", json={"customer_id": cust["id"], "lines": [{"description": "Dev", "qty": 4, "price": 90}]}
    ).json()
    assert c.post(f"/api/offers/{off['id']}/finalize").status_code == 200
    r = c.post(
        "/api/expenses",
        json={
            "date": "2026-03-04",
            "category": "Software",
            "lines": [
                {"description": "A", "gross": 119, "vat_rate": 19},
                {"description": "B", "gross": 10.7, "vat_rate": 7, "deductible": 50},
            ],
        },
    )
    assert r.status_code == 200, r.text
    c.post("/api/expenses", json={"date": "2026-03-05", "net": 50, "vat": 9.5, "category": "Software"})
    c.post("/api/timer/start", json={})
    st = services.store.settings()
    company = {"company_name": "Max Muster IT-Beratung", "owner_name": "Max Muster", "vat_id": "DE123456789"}
    services.store.save_settings({**st, **company, "payment_days": 30})
    # a booked receipt scan, a booked old-document scan, a trip, an allocation
    booked = c.post("/api/receipts", files={"file": ("bill.pdf", receipt_reader.add(SINGLE))})
    assert booked.status_code == 201 and booked.json()["outcome"] == "booked", booked.text
    scanned = c.post("/api/document-scans", files={"file": ("old.pdf", doc_reader.add(SCAN_INVOICE))})
    assert scanned.status_code == 201 and scanned.json()["status"] == "added", scanned.text
    assert c.post("/api/trips", json=trip_body()).status_code == 200
    r = c.post(
        "/api/allocations",
        json={
            "user_id": me["id"],
            "project_id": proj["id"],
            "start": ALLOC_START,
            "end": ALLOC_END,
            "hours_per_week": 20,
        },
    )
    assert r.status_code == 201, r.text
    # finalized documents with one shared number sequence
    services.store.save_settings({**services.store.settings(), "numbering_shared": True})
    for coll, extra in (("offers", {}), ("invoices", {}), ("offers", {})):
        d = c.post(
            f"/api/{coll}",
            json={"customer_id": cust["id"], "lines": [{"description": "S", "qty": 1, "price": 5}], **extra},
        )
        assert d.status_code == 200, d.text
        assert c.post(f"/api/{coll}/{d.json()['id']}/finalize").status_code == 200
    store = services.store
    assert [len(store.list(k)) for k in ("receipt_scans", "document_scans", "trips", "allocations")] == [1, 1, 1, 1]
    assert store.list("timers") and store.list("number_sequences")
    findings = IntegrityService(store).run()
    assert [f for f in findings if f.severity in ("error", "warning")] == []


# ------------------------------------------------------------------ CLI
def cli(tmp_path, *args):
    cfg = ["-c", str(tmp_path / "cfg.yaml"), "--data", str(tmp_path / "d")]
    with pytest.raises(SystemExit) as e:
        main([*cfg, "check-db", *args])
    return e.value.code


def make_db(tmp_path, with_boss=True):
    repo = SQLiteRepository(tmp_path / "d")
    repo.startup()
    if with_boss:
        repo.save("users", {"id": "boss01", "username": "boss", "role": "boss"})
    return repo


def test_cli_exit_codes_and_text_output(tmp_path, capsys):
    repo = make_db(tmp_path)
    repo.close()
    assert cli(tmp_path) == 0
    assert capsys.readouterr().out == "0 errors, 0 warnings, 0 info\n"
    repo = SQLiteRepository(tmp_path / "d")
    repo.save("timers", {"id": "boss01", "running": True})
    repo.save("expenses", {"id": "exp001", "date": "2026-03-01", "net": "abc"})
    repo.close()
    assert cli(tmp_path) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == [
        "ERROR",
        "type.misplaced",
        "expenses/exp001",
        "net",
        "value",
        "does",
        "not",
        "fit",
        "the",
        "column",
        "type",
    ]
    assert out[1].split()[:4] == ["WARNING", "model.invalid", "expenses/exp001", "net"]
    assert out[2].startswith("WARNING ") and "state.timer_running_without_start  timers/boss01  started" in out[2]
    assert out[-1] == "1 errors, 2 warnings, 0 info"


def test_cli_warnings_only_exit_zero(tmp_path, capsys):
    repo = make_db(tmp_path)
    repo.save("timers", {"id": "boss01", "running": True})
    repo.close()
    assert cli(tmp_path) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "0 errors, 1 warnings, 0 info"


def test_cli_json_and_only(tmp_path, capsys):
    repo = make_db(tmp_path)
    repo.save("timers", {"id": "boss01", "running": True})
    repo.save("expenses", {"id": "exp001", "date": "2026-03-01", "net": "abc"})
    repo.close()
    assert cli(tmp_path, "--json", "--only", "state.", "numbering.") == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "findings": [
            {
                "code": "state.timer_running_without_start",
                "severity": "warning",
                "collection": "timers",
                "id": "boss01",
                "field": "started",
                "message": "running without a start time",
            }
        ],
        "summary": {"errors": 0, "warnings": 1, "info": 0},
    }


def test_cli_exit_two_with_a_pending_migration_and_nothing_is_migrated(tmp_path, capsys):
    data = tmp_path / "d"
    legacy_db(data, 1).close()
    before = hashlib.sha256((data / DB_FILENAME).read_bytes()).hexdigest()
    assert cli(tmp_path) == 2
    err = capsys.readouterr().err
    assert f"expected {head_id()}. Run `permita38 migrate` first." in err
    assert hashlib.sha256((data / DB_FILENAME).read_bytes()).hexdigest() == before


def test_cli_exit_two_for_a_newer_database(tmp_path, capsys):
    repo = make_db(tmp_path)
    repo.close()
    raw = sqlite3.connect(tmp_path / "d" / DB_FILENAME)
    raw.execute("INSERT INTO schema_migrations (id, name, applied_at) VALUES ('ffffffff', 'future', 'x')")
    raw.commit()
    raw.close()
    assert cli(tmp_path) == 2
    assert capsys.readouterr().err


def test_check_db_leaves_the_database_file_unchanged(tmp_path, capsys):
    repo = make_db(tmp_path)
    repo.save("expenses", {"id": "exp001", "date": "2026-03-01", "net": "abc"})
    repo.close()
    path = tmp_path / "d" / DB_FILENAME
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert cli(tmp_path) == 1
    assert cli(tmp_path, "--json") == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not list(path.parent.glob("*.sqlite3-wal"))
    capsys.readouterr()
