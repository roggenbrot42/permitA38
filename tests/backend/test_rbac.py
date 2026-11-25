"""Roles and rights: boss / employee / accountant, enforced on the server for every route."""

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from permita38.api.deps import declared, is_session_only
from permita38.api.server import all_routers
from permita38.core import rights
from permita38.core.database import list_migrations
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository
from permita38.core.service import team_timeline

from .conftest import PASSWORD, _login

DUMMY_ID = "abc123def456"

# routes that need a signed-in user but no area: the user's own account, public status, static info
OWN_ACCOUNT_OR_PUBLIC = {
    ("GET", "/api/health"),
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/setup"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/change-password"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/login/totp"),
    ("GET", "/api/auth/totp/status"),
    ("POST", "/api/auth/totp/setup"),
    ("POST", "/api/auth/totp/enable"),
    ("POST", "/api/auth/totp/disable"),
    ("POST", "/api/auth/totp/recovery-codes/regenerate"),
    ("GET", "/api/bootstrap"),
    ("GET", "/api/holidays"),
    # API tokens are the signed-in user's own credentials (session-only, see SESSION_ONLY below)
    ("GET", "/api/tokens"),
    ("POST", "/api/tokens"),
    ("POST", "/api/tokens/revoke-all"),
    ("POST", "/api/tokens/{token_id}/revoke"),
    ("GET", "/api/tokens/{token_id}/audit"),
    ("PATCH", "/api/tokens/{token_id}"),  # answers 405: tokens are immutable
}


def _routes(app=None):
    for router in all_routers():
        for r in router.routes:
            assert isinstance(r, APIRoute)
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield m, r, "/api" + r.path


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", DUMMY_ID, path)


@pytest.fixture
def clients(app, boss_client):
    out = {"boss": boss_client}
    for role in ("employee", "accountant"):
        r = boss_client.post(
            "/api/users", json={"username": role[:3] + "x", "name": role.title(), "role": role, "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        out[role] = _login(app, role[:3] + "x")
        out[role].user_id = r.json()["id"]
    return out


# ------------------------------------------------------------------ the table itself
def test_matrix_is_complete_and_boss_can_do_everything():
    assert set(rights.MATRIX) == set(rights.ROLES)
    for role, table in rights.MATRIX.items():
        assert set(table) == set(rights.AREAS), role
    boss = {a: sc for a, sc in rights.MATRIX["boss"].items() if a != "team_timeline"}
    assert all(sc == (rights.Scope.ALL,) * 2 for sc in boss.values())
    # the timeline is read-only for everybody, the boss included (its level is always "full")
    assert rights.MATRIX["boss"]["team_timeline"] == (rights.Scope.ALL, rights.Scope.NONE)
    assert all(rights.scope(r, "team_timeline", "write") == rights.Scope.NONE for r in rights.ROLES)
    for role in rights.ROLES:  # you cannot write what you cannot read
        for area, (rd, wr) in rights.MATRIX[role].items():
            assert not (rd == rights.Scope.NONE and wr != rights.Scope.NONE), (role, area)


def test_legacy_and_unknown_roles():
    assert rights.normalize_role("member") == "employee"
    assert rights.scope("member", "time", "write") == rights.Scope.LIMITED
    for bogus in ("root", "", None, 7, "BOSS"):
        assert not any(rights.allowed(bogus, a, op) for a in rights.AREAS for op in ("read", "write")), bogus


def test_docs_list_every_area_and_role():
    text = (Path(__file__).parents[2] / "docs" / "roles.md").read_text(encoding="utf-8")
    for area in rights.AREAS:
        assert f"`{area}`" in text, area
    for role in rights.ROLES:
        assert role in text
    # the table in the docs is generated from MATRIX: every cell must be present and correct
    for area in rights.AREAS:
        row = next(line for line in text.splitlines() if line.startswith(f"| `{area}`"))
        cells = [c.strip() for c in row.strip("|").split("|")][2:]
        expected = [
            f"{rights.MATRIX[r][area][0].value} / {rights.MATRIX[r][area][1].value}"
            for r in ("boss", "employee", "accountant")
        ]
        assert cells == expected, area


# ------------------------------------------------------------------ every route is guarded
def test_every_route_declares_its_area_or_is_explicitly_account_level(app):
    unguarded = {(m, path) for m, r, path in _routes(app) if not declared(r)}
    assert unguarded == OWN_ACCOUNT_OR_PUBLIC


def test_declared_areas_exist(app):
    for _, r, _path in _routes(app):
        for area, op, _full in declared(r):
            assert area in rights.AREAS and op in ("read", "write")


# Hand-written, independent of the router declarations: (method, path) -> (area, operation, needs full scope).
# Reviewed line by line against docs/roles.md. A new guarded route must be added here.
# Notes: read-only planning calls are POSTs but need only `planning` read; the Impressum lookup is part of
# `customers` (write, it creates data); converting an offer into an invoice creates an invoice (`invoices` write);
# GET /settings needs full scope because employees get their limited view through /bootstrap.
EXPECTED: dict[tuple[str, str], tuple[str, str, bool]] = {
    ("POST", "/api/users/{user_id}/totp/reset"): ("team", "write", False),
    ("GET", "/api/users"): ("team", "read", False),
    ("POST", "/api/users"): ("team", "write", False),
    ("PUT", "/api/users/{user_id}"): ("team", "write", False),
    ("DELETE", "/api/users/{user_id}"): ("team", "write", False),
    # calendars are boss-only (planning write, full scope); each boss manages only their own sources (service check)
    ("GET", "/api/calendars"): ("planning", "write", True),
    ("POST", "/api/calendars"): ("planning", "write", True),
    ("POST", "/api/calendars/upload"): ("planning", "write", True),
    ("PUT", "/api/calendars/{source_id}"): ("planning", "write", True),
    ("DELETE", "/api/calendars/{source_id}"): ("planning", "write", True),
    ("POST", "/api/calendars/{source_id}/refresh"): ("planning", "write", True),
    ("GET", "/api/calendars/busy"): ("planning", "write", True),
    ("GET", "/api/calendars/suggestions"): ("planning", "write", True),
    ("GET", "/api/calendars/{source_id}/preview"): ("planning", "write", True),
    ("GET", "/api/timer"): ("timer", "read", False),
    ("PATCH", "/api/timer"): ("timer", "write", False),
    ("POST", "/api/timer/start"): ("timer", "write", False),
    ("POST", "/api/timer/stop"): ("timer", "write", False),
    ("GET", "/api/dashboard"): ("timer", "read", False),
    ("POST", "/api/plan/simulate"): ("planning", "read", False),
    ("POST", "/api/plan/check-offer"): ("planning", "read", False),
    ("POST", "/api/plan/what-must-move"): ("planning", "read", False),
    ("POST", "/api/plan/suggest-vacation"): ("planning", "read", False),
    ("POST", "/api/plan/suggest-start"): ("planning", "read", False),
    ("GET", "/api/plan"): ("planning", "read", False),
    ("GET", "/api/plan/vacation"): ("planning", "read", False),
    ("POST", "/api/plan/apply"): ("planning", "write", False),
    ("GET", "/api/plan/scenarios"): ("planning", "read", False),
    ("POST", "/api/plan/scenarios"): ("planning", "write", False),
    ("PUT", "/api/plan/scenarios/{rid}"): ("planning", "write", False),
    ("DELETE", "/api/plan/scenarios/{rid}"): ("planning", "write", False),
    ("POST", "/api/plan/scenarios/{rid}/apply"): ("planning", "write", False),
    ("POST", "/api/projects/{rid}/extend"): ("planning", "write", False),
    ("GET", "/api/projects/{rid}/planning"): ("planning", "read", False),
    # planner per project (docs/contracts/planner-per-project.md): boss only through the `planning` area
    ("GET", "/api/allocations"): ("planning", "read", False),
    ("POST", "/api/allocations"): ("planning", "write", False),
    ("PUT", "/api/allocations/{allocation_id}"): ("planning", "write", False),
    ("DELETE", "/api/allocations/{allocation_id}"): ("planning", "write", False),
    ("GET", "/api/finance"): ("finance", "read", False),
    ("GET", "/api/project-report"): ("finance", "read", False),
    ("GET", "/api/settings"): ("settings", "read", True),
    # the team timeline: the matrix only decides access; the setting decides the level (service, 403 at `off`)
    ("GET", "/api/team/timeline"): ("team_timeline", "read", False),
    ("PUT", "/api/settings"): ("settings", "write", False),
    ("GET", "/api/settings/personal-tax"): ("settings", "write", True),
    ("PUT", "/api/settings/personal-tax"): ("settings", "write", True),
    ("GET", "/api/settings/work-limits"): ("settings", "write", True),
    ("PUT", "/api/settings/work-limits"): ("settings", "write", True),
    # numbering and import management is boss-only: settings write at full scope
    ("GET", "/api/numbering/preview"): ("settings", "write", True),
    ("GET", "/api/numbering/status"): ("settings", "write", True),
    ("POST", "/api/numbering/check"): ("settings", "write", True),
    ("PUT", "/api/numbering"): ("settings", "write", True),
    ("GET", "/api/numbering/log"): ("settings", "write", True),
    ("POST", "/api/import/preview"): ("settings", "write", True),
    ("POST", "/api/import/run"): ("settings", "write", True),
    ("GET", "/api/import/batches"): ("settings", "write", True),
    ("POST", "/api/import/batches/{rid}/undo"): ("settings", "write", True),
    # full data export/import: boss only (session only once API tokens exist, see test_data_transfer_security.py)
    ("GET", "/api/data/export/info"): ("settings", "write", True),
    ("POST", "/api/data/export"): ("settings", "write", True),
    ("POST", "/api/data/import/preview"): ("settings", "write", True),
    ("POST", "/api/data/import/run"): ("settings", "write", True),
    ("GET", "/api/data/transfers"): ("settings", "write", True),
    ("POST", "/api/attachments"): ("attachments", "write", False),
    ("GET", "/api/attachments/{name}"): ("attachments", "read", False),
    ("POST", "/api/invoices/from-time"): ("invoices", "write", False),
    ("POST", "/api/invoices/{rid}/finalize"): ("invoices", "write", False),
    ("GET", "/api/invoices/{rid}/pdf"): ("invoices", "read", False),
    ("POST", "/api/offers/{rid}/finalize"): ("offers", "write", False),
    ("GET", "/api/offers/{rid}/pdf"): ("offers", "read", False),
    ("POST", "/api/offers/{rid}/accept"): ("offers", "write", False),
    ("POST", "/api/offers/{rid}/to-invoice"): ("invoices", "write", False),
    ("POST", "/api/customers/lookup-impressum"): ("customers", "write", False),
    ("GET", "/api/customers"): ("customers", "read", False),
    ("POST", "/api/customers"): ("customers", "write", False),
    ("GET", "/api/customers/{rid}"): ("customers", "read", False),
    ("PUT", "/api/customers/{rid}"): ("customers", "write", False),
    ("DELETE", "/api/customers/{rid}"): ("customers", "write", False),
    ("GET", "/api/projects"): ("projects", "read", False),
    ("POST", "/api/projects"): ("projects", "write", False),
    ("GET", "/api/projects/{rid}"): ("projects", "read", False),
    ("PUT", "/api/projects/{rid}"): ("projects", "write", False),
    ("DELETE", "/api/projects/{rid}"): ("projects", "write", False),
    ("GET", "/api/tasks"): ("projects", "read", False),
    ("POST", "/api/tasks"): ("projects", "write", False),
    ("GET", "/api/tasks/{rid}"): ("projects", "read", False),
    ("PUT", "/api/tasks/{rid}"): ("projects", "write", False),
    ("DELETE", "/api/tasks/{rid}"): ("projects", "write", False),
    ("GET", "/api/entries"): ("time", "read", False),
    ("GET", "/api/projects/{rid}/timesheet"): ("time", "read", False),
    ("POST", "/api/entries"): ("time", "write", False),
    ("GET", "/api/entries/{rid}"): ("time", "read", False),
    ("PUT", "/api/entries/{rid}"): ("time", "write", False),
    ("DELETE", "/api/entries/{rid}"): ("time", "write", False),
    ("GET", "/api/contracts"): ("contracts", "read", False),
    ("POST", "/api/contracts"): ("contracts", "write", False),
    ("GET", "/api/contracts/{rid}"): ("contracts", "read", False),
    ("PUT", "/api/contracts/{rid}"): ("contracts", "write", False),
    ("DELETE", "/api/contracts/{rid}"): ("contracts", "write", False),
    ("GET", "/api/absences"): ("absences", "read", False),
    ("POST", "/api/absences"): ("absences", "write", False),
    ("GET", "/api/absences/{rid}"): ("absences", "read", False),
    ("PUT", "/api/absences/{rid}"): ("absences", "write", False),
    ("DELETE", "/api/absences/{rid}"): ("absences", "write", False),
    ("GET", "/api/offers"): ("offers", "read", False),
    ("POST", "/api/offers"): ("offers", "write", False),
    ("GET", "/api/offers/{rid}"): ("offers", "read", False),
    ("PUT", "/api/offers/{rid}"): ("offers", "write", False),
    ("DELETE", "/api/offers/{rid}"): ("offers", "write", False),
    ("GET", "/api/invoices"): ("invoices", "read", False),
    ("POST", "/api/invoices"): ("invoices", "write", False),
    ("GET", "/api/invoices/{rid}"): ("invoices", "read", False),
    ("PUT", "/api/invoices/{rid}"): ("invoices", "write", False),
    ("DELETE", "/api/invoices/{rid}"): ("invoices", "write", False),
    ("GET", "/api/expenses"): ("expenses", "read", False),
    ("POST", "/api/expenses"): ("expenses", "write", False),
    ("GET", "/api/expenses/{rid}"): ("expenses", "read", False),
    ("PUT", "/api/expenses/{rid}"): ("expenses", "write", False),
    ("DELETE", "/api/expenses/{rid}"): ("expenses", "write", False),
    # business trips: employees see and change their own trips only (service check); accountants read all, change none;
    # reopening a final trip is boss only (full scope); preview needs write access (contract), nothing is saved
    ("GET", "/api/suppliers"): ("suppliers", "read", False),
    ("POST", "/api/suppliers"): ("suppliers", "write", False),
    ("GET", "/api/suppliers/vendor-suggestions"): ("suppliers", "write", False),
    ("POST", "/api/suppliers/adopt-vendors"): ("suppliers", "write", False),
    ("GET", "/api/suppliers/{supplier_id}"): ("suppliers", "read", False),
    ("PUT", "/api/suppliers/{supplier_id}"): ("suppliers", "write", False),
    ("DELETE", "/api/suppliers/{supplier_id}"): ("suppliers", "write", False),
    ("GET", "/api/trips"): ("trips", "read", False),
    ("POST", "/api/trips"): ("trips", "write", False),
    ("POST", "/api/trips/preview"): ("trips", "write", False),
    ("GET", "/api/trips/{rid}"): ("trips", "read", False),
    ("PUT", "/api/trips/{rid}"): ("trips", "write", False),
    ("DELETE", "/api/trips/{rid}"): ("trips", "write", False),
    ("POST", "/api/trips/{rid}/reopen"): ("trips", "write", True),
    ("GET", "/api/trips/{rid}/statement.pdf"): ("trips", "read", False),
    ("GET", "/api/templates"): ("templates", "read", False),
    ("POST", "/api/templates"): ("templates", "write", False),
    ("GET", "/api/templates/{rid}"): ("templates", "read", False),
    ("PUT", "/api/templates/{rid}"): ("templates", "write", False),
    ("DELETE", "/api/templates/{rid}"): ("templates", "write", False),
    ("GET", "/api/suppliers/{supplier_id}/dependency"): ("procurement", "read", False),
    ("GET", "/api/incoming-offers"): ("procurement", "read", False),
    ("POST", "/api/incoming-offers"): ("procurement", "write", False),
    ("GET", "/api/incoming-offers/{incoming_offer_id}"): ("procurement", "read", False),
    ("PUT", "/api/incoming-offers/{incoming_offer_id}"): ("procurement", "write", False),
    ("DELETE", "/api/incoming-offers/{incoming_offer_id}"): ("procurement", "write", False),
    ("POST", "/api/incoming-offers/{incoming_offer_id}/review"): ("procurement", "write", False),
    ("POST", "/api/incoming-offers/{incoming_offer_id}/accept"): ("procurement", "write", False),
    ("POST", "/api/incoming-offers/{incoming_offer_id}/reject"): ("procurement", "write", False),
    ("POST", "/api/incoming-offers/{incoming_offer_id}/reopen"): ("procurement", "write", False),
    ("POST", "/api/incoming-offers/{incoming_offer_id}/unaccept"): ("procurement", "write", False),
    ("GET", "/api/allocations/{allocation_id}"): ("procurement", "read", False),
    ("POST", "/api/allocations/{allocation_id}/cancel"): ("procurement", "write", False),
    ("GET", "/api/finance/commitments"): ("procurement", "read", False),
    # receipts: expenses AND attachments (checked as a pair in the test below)
    ("POST", "/api/receipts"): ("expenses", "write", False),
    ("GET", "/api/receipts"): ("expenses", "read", False),
    ("GET", "/api/receipts/capabilities"): ("expenses", "read", False),
    ("GET", "/api/receipts/{scan_id}"): ("expenses", "read", False),
    ("GET", "/api/receipts/{scan_id}/pages/{page}"): ("expenses", "read", False),
    ("POST", "/api/receipts/{scan_id}/confirm"): ("expenses", "write", False),
    ("POST", "/api/receipts/{scan_id}/parts"): ("expenses", "write", False),
    ("DELETE", "/api/receipts/{scan_id}"): ("expenses", "write", False),
    ("POST", "/api/receipts/ocr/install"): ("settings", "write", True),
    # document scan: invoices, offers, attachments and customers (discard: without customers); see the test below
    ("POST", "/api/document-scans"): ("invoices", "write", False),
    ("GET", "/api/document-scans"): ("invoices", "read", False),
    ("GET", "/api/document-scans/{scan_id}"): ("invoices", "read", False),
    ("GET", "/api/document-scans/{scan_id}/pages/{page}"): ("invoices", "read", False),
    ("POST", "/api/document-scans/{scan_id}/confirm"): ("invoices", "write", False),
    ("DELETE", "/api/document-scans/{scan_id}"): ("invoices", "write", False),
}


# ------------------------------------------------------------------ accounting and VAT skeleton routes (E4)
# Hand-written: (method, route template, area, operation, boss only, valid request body, query string). Payments and
# credit notes need write on `invoices` OR `accounting`; with the current matrix that is the set of roles with
# `accounting` write, so the table (and the declared requirement) names that area. Every non-GET route is session only.
_PAY = {"date": "2026-01-05", "amount": "10.00"}
_ENTRY = {
    "date": "2026-01-05",
    "text": "Demo",
    "lines": [{"account": "1200", "amount": "10.00"}, {"account": "8400", "amount": "-10.00"}],
}
_FY = {"year": 2026, "chart": "skr03", "income_basis": "eur"}
_DISCOUNT = {"date": "2026-01-05", "amount_paid": "10.00"}
_CREDIT = {"credit_kind": "storno", "date": "2026-01-05", "reason": "Demo"}
_VAT_PAY = {"date": "2026-02-10", "amount": "5.00", "kind": "payment"}
NEW_ROUTES: list[tuple[str, str, str, str, bool, dict | None, str]] = [
    ("GET", "/api/accounting/years", "accounting", "read", False, None, ""),
    ("POST", "/api/accounting/years", "accounting", "write", False, _FY, ""),
    ("GET", "/api/accounting/years/{year}", "accounting", "read", False, None, ""),
    ("POST", "/api/accounting/years/{year}/close", "accounting", "write", True, None, ""),
    ("POST", "/api/accounting/init", "accounting", "write", True, {"chart": "skr03", "income_basis": "eur"}, ""),
    ("GET", "/api/accounting/init/review", "accounting", "read", False, None, ""),
    ("GET", "/api/accounting/journal", "accounting", "read", False, None, ""),
    ("POST", "/api/accounting/entries", "accounting", "write", False, _ENTRY, ""),
    ("POST", "/api/accounting/entries/{entry_id}/reverse", "accounting", "write", False, {}, ""),
    ("GET", "/api/accounting/entry-templates", "accounting", "read", False, None, ""),
    ("GET", "/api/accounting/accounts", "accounting", "read", False, None, ""),
    ("GET", "/api/accounting/reports/trial-balance", "accounting", "read", False, None, ""),
    ("GET", "/api/accounting/reports/eur", "accounting", "read", False, None, ""),
    ("GET", "/api/accounting/reports/pnl", "accounting", "read", False, None, ""),
    ("GET", "/api/accounting/reports/balance-sheet", "accounting", "read", False, None, ""),
    ("GET", "/api/accounting/reports/open-items", "accounting", "read", False, None, ""),
    ("GET", "/api/accounting/reports/ledger", "accounting", "read", False, None, ""),
    ("GET", "/api/accounting/locks", "accounting", "read", False, None, ""),
    ("POST", "/api/accounting/locks", "accounting", "write", False, {"year": 2026, "month": 1}, ""),
    ("GET", "/api/accounting/datev", "accounting", "read", False, None, ""),
    ("GET", "/api/accounting/integrity", "accounting", "read", False, None, ""),
    ("GET", "/api/invoices/{rid}/payments", "invoices", "read", False, None, ""),
    ("POST", "/api/invoices/{rid}/payments", "accounting", "write", False, _PAY, ""),
    ("POST", "/api/payments/{payment_id}/reverse", "accounting", "write", False, {}, ""),
    ("POST", "/api/invoices/{rid}/payments/legacy/reverse", "accounting", "write", False, {}, ""),
    ("POST", "/api/invoices/{rid}/settle-discount", "accounting", "write", False, _DISCOUNT, ""),
    ("GET", "/api/invoices/{rid}/credit-notes", "invoices", "read", False, None, ""),
    ("POST", "/api/invoices/{rid}/credit-notes", "accounting", "write", False, _CREDIT, ""),
    ("GET", "/api/vat-returns", "vat", "read", False, None, "?year=2026"),
    ("GET", "/api/vat-returns/preview", "vat", "read", False, None, "?year=2026&kind=month&no=1"),
    ("POST", "/api/vat-returns", "vat", "write", False, {"year": 2026, "kind": "month", "no": 1}, ""),
    ("GET", "/api/vat-returns/{rid}", "vat", "read", False, None, ""),
    ("POST", "/api/vat-returns/{rid}/finalize", "vat", "write", False, None, ""),
    ("POST", "/api/vat-returns/{rid}/reopen", "vat", "write", False, None, ""),
    ("POST", "/api/vat-returns/{rid}/filed", "vat", "write", False, {"filed_on": "2026-02-10"}, ""),
    ("DELETE", "/api/vat-returns/{rid}", "vat", "write", False, None, ""),
    ("GET", "/api/vat-returns/{rid}/boxes/{kz}", "vat", "read", False, None, ""),
    ("POST", "/api/vat-returns/{rid}/payment", "vat", "write", False, _VAT_PAY, ""),
    ("GET", "/api/vat-returns/{rid}/export.pdf", "vat", "read", False, None, ""),
    ("GET", "/api/vat-returns/{rid}/export.csv", "vat", "read", False, None, ""),
    ("GET", "/api/vat-returns/{rid}/export.json", "vat", "read", False, None, ""),
    ("GET", "/api/vat/special-prepayment", "vat", "read", False, None, "?year=2026"),
    ("PUT", "/api/vat/special-prepayment", "vat", "write", False, {"year": 2026, "amount": "100.00"}, ""),
    ("GET", "/api/zm", "vat", "read", False, None, "?year=2026"),
    ("GET", "/api/zm/preview", "vat", "read", False, None, "?year=2026&quarter=1"),
    ("POST", "/api/zm", "vat", "write", False, {"year": 2026, "quarter": 1}, ""),
    ("GET", "/api/zm/{rid}", "vat", "read", False, None, ""),
    ("POST", "/api/zm/{rid}/finalize", "vat", "write", False, None, ""),
    ("POST", "/api/zm/{rid}/reopen", "vat", "write", False, None, ""),
    ("POST", "/api/zm/{rid}/filed", "vat", "write", False, {"filed_on": "2026-04-20"}, ""),
    ("DELETE", "/api/zm/{rid}", "vat", "write", False, None, ""),
    ("GET", "/api/zm/{rid}/export.csv", "vat", "read", False, None, ""),
    ("GET", "/api/zm/{rid}/export.pdf", "vat", "read", False, None, ""),
]
# the declared requirement of a route: (area, operation, full scope); boss-only routes are 'full' (checked separately)
NEW_EXPECTED = {(m, p): (a, o, boss) for m, p, a, o, boss, _b, _q in NEW_ROUTES}
NEW_SESSION_ONLY = {(m, p) for m, p, *_ in NEW_ROUTES if m != "GET"}


def _concrete(path: str) -> str:
    out = path.replace("{year}", "2026").replace("{kz}", "81")
    return re.sub(r"\{[^}]+\}", DUMMY_ID, out)


def _is_boss_only(route) -> bool:
    def walk(dep) -> bool:
        return bool(getattr(dep.call, "boss_only", False)) or any(walk(sub) for sub in dep.dependencies)

    return walk(route.dependant)


def test_declared_rights_equal_the_hand_written_table(app):
    actual = {}
    for method, route, path in _routes(app):
        reqs = declared(route)
        if reqs:
            if (
                path.startswith("/api/receipts") and path != "/api/receipts/ocr/install"
            ):  # a receipt needs the expenses AND the attachments right
                op = "read" if method == "GET" else "write"
                assert sorted(reqs) == [("attachments", op, False), ("expenses", op, False)], (method, path, reqs)
                reqs = [("expenses", op, False)]
            if path.startswith("/api/document-scans"):
                op = "read" if method == "GET" else "write"
                areas = ["attachments", "invoices", "offers"]
                if method != "DELETE":
                    areas = sorted(areas + ["customers"])
                assert sorted(reqs) == [(a, op, False) for a in areas], (method, path, reqs)
                reqs = [("invoices", op, False)]
            assert len(reqs) == 1, (method, path, reqs)
            actual[(method, path)] = reqs[0]
    assert actual == EXPECTED | NEW_EXPECTED


# routes whose 403 also depends on data (the team timeline is 403 at level `off`): a valid query so that the
# validation of the parameters does not answer first
VALID_QUERY = {"/api/team/timeline": "?start=2026-11-02&end=2026-11-03"}


def test_every_route_is_denied_exactly_where_the_matrix_says(app, clients):
    """All routes x all roles: 403 if and only if the role lacks the declared access; otherwise the request gets
    past the permission check (whatever the handler then says about the dummy ids). The timeline also answers 403
    for a role whose level is `off` (docs/contracts/team-timeline.md)."""
    checked = 0
    for method, route, path in _routes(app):
        reqs = declared(route)
        if not reqs:
            continue
        for role, c in clients.items():
            should_pass = all(
                (s := rights.scope(role, a, op)) != rights.Scope.NONE and (not full or s == rights.Scope.ALL)
                for a, op, full in reqs
            )
            if path == "/api/team/timeline":
                should_pass = (
                    should_pass and team_timeline.level_for(role, app.state.services.store.settings()) != "off"
                )
            r = c.request(
                method, _url(path) + VALID_QUERY.get(path, ""), json={} if method in ("POST", "PUT", "PATCH") else None
            )
            if method == "POST" and "attachments" in path:
                r = c.post(_url(path), files={"file": ("a.txt", b"x")})
            if _is_boss_only(route):  # init and close year: the role itself must be the boss
                should_pass = should_pass and role == "boss"
            assert (r.status_code != 403) == should_pass, (method, path, role, r.status_code)
            checked += 1
    assert checked > 150


NEW_PREFIXES = ("/api/accounting", "/api/vat", "/api/zm", "/api/payments")
NEW_INVOICE_SUFFIXES = ("/payments", "/credit-notes", "/settle-discount", "/payments/legacy/reverse")


def test_new_routes_are_exactly_the_documented_ones(app):
    found = {
        (m, path)
        for m, r, path in _routes(app)
        if path.startswith(NEW_PREFIXES) or (path.startswith("/api/invoices/{") and path.endswith(NEW_INVOICE_SUFFIXES))
    }
    assert found == {(m, p) for m, p, *_ in NEW_ROUTES}
    assert len(NEW_ROUTES) == 53


def test_boss_only_routes_are_exactly_init_and_close_year(app):
    boss_only = {(m, path) for m, r, path in _routes(app) if _is_boss_only(r)}
    assert boss_only == {("POST", "/api/accounting/init"), ("POST", "/api/accounting/years/{year}/close")}
    assert boss_only == {(m, p) for m, p, _a, _o, boss, _b, _q in NEW_ROUTES if boss}


def _call(client, method, path, body, query):
    kw = {} if body is None else {"json": body}
    return client.request(method, _concrete(path) + query, **kw)


IMPLEMENTED = {
    ("GET", "/api/accounting/years"),
    ("POST", "/api/accounting/years"),
    ("GET", "/api/accounting/years/{year}"),
    ("GET", "/api/accounting/locks"),
    ("POST", "/api/accounting/locks"),
    ("POST", "/api/accounting/years/{year}/close"),  # boss only, 422 for the dummy id
    ("GET", "/api/accounting/entry-templates"),
    ("POST", "/api/accounting/entries"),
    ("POST", "/api/accounting/entries/{entry_id}/reverse"),
    ("GET", "/api/invoices/{rid}/payments"),
    ("POST", "/api/invoices/{rid}/payments"),
    ("POST", "/api/payments/{payment_id}/reverse"),
    ("POST", "/api/invoices/{rid}/payments/legacy/reverse"),
    ("POST", "/api/invoices/{rid}/settle-discount"),
    ("GET", "/api/invoices/{rid}/credit-notes"),
    ("POST", "/api/invoices/{rid}/credit-notes"),
    ("GET", "/api/accounting/journal"),
    ("POST", "/api/vat-returns/{rid}/payment"),
    ("GET", "/api/vat/special-prepayment"),
    ("PUT", "/api/vat/special-prepayment"),
    ("GET", "/api/accounting/accounts"),
    ("GET", "/api/accounting/reports/trial-balance"),
    ("GET", "/api/accounting/reports/eur"),
    ("GET", "/api/accounting/reports/pnl"),
    ("GET", "/api/accounting/reports/balance-sheet"),
    ("GET", "/api/accounting/reports/open-items"),
    ("GET", "/api/accounting/reports/ledger"),
    ("GET", "/api/accounting/datev"),
    ("GET", "/api/vat-returns"),
    ("GET", "/api/vat-returns/preview"),
    ("POST", "/api/vat-returns"),
    ("GET", "/api/vat-returns/{rid}"),
    ("POST", "/api/vat-returns/{rid}/finalize"),
    ("POST", "/api/vat-returns/{rid}/reopen"),
    ("POST", "/api/vat-returns/{rid}/filed"),
    ("DELETE", "/api/vat-returns/{rid}"),
    ("GET", "/api/vat-returns/{rid}/boxes/{kz}"),
    ("GET", "/api/vat-returns/{rid}/export.pdf"),
    ("GET", "/api/vat-returns/{rid}/export.csv"),
    ("GET", "/api/vat-returns/{rid}/export.json"),
    ("GET", "/api/zm"),
    ("GET", "/api/zm/preview"),
    ("POST", "/api/zm"),
    ("GET", "/api/zm/{rid}"),
    ("POST", "/api/zm/{rid}/finalize"),
    ("POST", "/api/zm/{rid}/reopen"),
    ("POST", "/api/zm/{rid}/filed"),
    ("DELETE", "/api/zm/{rid}"),
    ("GET", "/api/zm/{rid}/export.csv"),
    ("GET", "/api/zm/{rid}/export.pdf"),
}


def test_new_routes_answer_501_where_allowed_and_403_where_denied(app, clients):
    """Every route x every role with a valid request: 501 "not implemented yet" for boss and accountant, 403 for the
    employee; init and close year are 403 for the accountant too."""
    for method, path, _area, _op, boss_only, body, query in NEW_ROUTES:
        for role, c in clients.items():
            r = _call(c, method, path, body, query)
            if (method, path) in IMPLEMENTED:  # answered by a service now (tests/backend/test_fiscal_years.py)
                refused = role == "employee" or (boss_only and role != "boss")
                assert (r.status_code == 403) == refused, (method, path, role, r.status_code)
            elif role == "employee" or (boss_only and role != "boss"):
                assert r.status_code == 403, (method, path, role, r.status_code)
            else:
                assert r.status_code == 501 and r.json() == {"detail": "not implemented yet"}, (
                    method, path, role, r.status_code, r.text,
                )  # fmt: skip


def test_api_tokens_read_accounting_and_vat_but_never_write_them(app, clients):
    """A token reads with its owner's rights, read-only or not; every changing route is session only: 403 for a
    read-only and a read-write token alike (docs/specs/accounting.md 17: no write through tokens in v1)."""
    for role, session in clients.items():
        for read_only in (True, False):
            r = session.post("/api/tokens", json={"label": "t", "read_only": read_only})
            assert r.status_code == 201, r.text
            tok = TestClient(app)
            tok.headers["Authorization"] = f"Bearer {r.json()['token']}"
            for method, path, _area, _op, _boss, body, query in NEW_ROUTES:
                got = _call(tok, method, path, body, query)
                if method != "GET":
                    assert got.status_code == 403, (role, read_only, method, path, got.status_code)
                    assert got.json() == {"detail": "This route cannot be used with an API token"}
                else:
                    if role == "employee":
                        assert got.status_code == 403, (role, read_only, method, path)
                    elif (method, path) in IMPLEMENTED:  # 404: the dummy year does not exist
                        assert got.status_code in (200, 404, 422), (role, read_only, method, path, got.status_code)
                    else:
                        assert got.status_code == 501, (role, read_only, method, path)


PAYMENT_DEP_PATHS = {
    "/api/invoices/{rid}/payments",
    "/api/invoices/{rid}/credit-notes",
    "/api/invoices/{rid}/payments/legacy/reverse",
}


def test_payment_dependency_is_the_either_or_rule(app):
    """`invoices` write OR `accounting` write: the roles that pass are the union of both."""
    deps = {}
    for m, route, path in _routes(app):
        if m == "POST" and path in PAYMENT_DEP_PATHS:
            for d in route.dependant.dependencies:
                if hasattr(d.call, "rbac_any"):
                    deps[path] = d.call
    assert set(deps) == PAYMENT_DEP_PATHS
    for fn in deps.values():
        assert fn.rbac_any == (("invoices", "write"), ("accounting", "write"))
        assert rights.PAYMENT_WRITE_AREAS == ("invoices", "accounting")
        for role in rights.ROLES:
            union = rights.allowed(role, "invoices", "write") or rights.allowed(role, "accounting", "write")
            assert union == rights.allowed(role, *fn.rbac[:2]), role  # the declared single area is exact today
            if union:
                assert fn({"role": role}) == {"role": role}
            else:
                with pytest.raises(HTTPException) as err:
                    fn({"role": role})
                assert err.value.status_code == 403


_PAYMENT_IMPLEMENTED = {
    "/api/invoices/{rid}/payments",
    "/api/invoices/{rid}/credit-notes",
    "/api/invoices/{rid}/settle-discount",
    "/api/payments/{payment_id}/reverse",
    "/api/invoices/{rid}/payments/legacy/reverse",
}  # 404: dummy id
PAYMENT_WRITE_ROUTES = [
    ("POST", "/api/invoices/{rid}/payments", _PAY),
    ("POST", "/api/payments/{payment_id}/reverse", {}),
    ("POST", "/api/invoices/{rid}/payments/legacy/reverse", {}),
    ("POST", "/api/invoices/{rid}/settle-discount", _DISCOUNT),
    ("POST", "/api/invoices/{rid}/credit-notes", _CREDIT),
]


def test_payment_routes_follow_invoices_or_accounting_write(app, clients, monkeypatch):
    """With a patched matrix one role has ONLY `invoices` write, another ONLY `accounting` write: both pass the
    payment and credit-note routes; a role with neither is refused (fails if the dependency checked one area)."""
    none = (rights.Scope.NONE, rights.Scope.NONE)
    full = (rights.Scope.ALL, rights.Scope.ALL)
    base = rights.MATRIX["employee"]
    rows = {
        "employee": {**base, "invoices": full, "accounting": none},  # only invoices write
        "accountant": {**rights.MATRIX["accountant"], "invoices": none, "accounting": full},  # only accounting write
    }
    monkeypatch.setattr(rights, "MATRIX", {**rights.MATRIX, **rows})
    for method, path, body in PAYMENT_WRITE_ROUTES:
        for who in ("employee", "accountant"):
            r = _call(clients[who], method, path, body, "")
            want = 404 if path in _PAYMENT_IMPLEMENTED else 501
            assert r.status_code == want, (who, method, path, r.status_code, r.text)
    # neither right: the employee row without invoices and accounting write is refused, the accountant still passes
    monkeypatch.setitem(rights.MATRIX, "employee", {**base, "invoices": none, "accounting": none})
    for method, path, body in PAYMENT_WRITE_ROUTES:
        r = _call(clients["employee"], method, path, body, "")
        assert r.status_code == 403, (method, path, r.status_code, r.text)
        want = 404 if path in _PAYMENT_IMPLEMENTED else 501
        assert _call(clients["accountant"], method, path, body, "").status_code == want


def test_accounting_and_vat_matrix_cells():
    for area in ("accounting", "vat"):
        assert rights.MATRIX["boss"][area] == (rights.Scope.ALL, rights.Scope.ALL)
        assert rights.MATRIX["accountant"][area] == (rights.Scope.ALL, rights.Scope.ALL)
        assert rights.MATRIX["employee"][area] == (rights.Scope.NONE, rights.Scope.NONE)


# Hand-written: the routes an API token can never use (credentials, two-factor, user administration, token management,
# settings writes, numbering/import/calendar writes). Everything else under /api/auth is either public or `me`.
SESSION_ONLY = {
    ("GET", "/api/settings/personal-tax"),
    ("PUT", "/api/settings/personal-tax"),
    ("GET", "/api/settings/work-limits"),
    ("PUT", "/api/settings/work-limits"),
    ("POST", "/api/auth/change-password"),
    ("GET", "/api/auth/totp/status"),
    ("POST", "/api/auth/totp/setup"),
    ("POST", "/api/auth/totp/enable"),
    ("POST", "/api/auth/totp/disable"),
    ("POST", "/api/auth/totp/recovery-codes/regenerate"),
    ("POST", "/api/users/{user_id}/totp/reset"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PUT", "/api/users/{user_id}"),
    ("DELETE", "/api/users/{user_id}"),
    ("GET", "/api/tokens"),
    ("POST", "/api/tokens"),
    ("POST", "/api/tokens/revoke-all"),
    ("POST", "/api/tokens/{token_id}/revoke"),
    ("GET", "/api/tokens/{token_id}/audit"),
    ("PATCH", "/api/tokens/{token_id}"),
    ("PUT", "/api/settings"),
    ("POST", "/api/receipts/ocr/install"),
    # full data export and import (boss only, never with a token)
    ("GET", "/api/data/export/info"),
    ("POST", "/api/data/export"),
    ("POST", "/api/data/import/preview"),
    ("POST", "/api/data/import/run"),
    ("GET", "/api/data/transfers"),
    # boss-only high-impact writes (contract decision 20)
    ("PUT", "/api/numbering"),
    ("POST", "/api/import/run"),
    ("POST", "/api/import/batches/{rid}/undo"),
    ("POST", "/api/calendars"),
    ("POST", "/api/calendars/upload"),
    ("PUT", "/api/calendars/{source_id}"),
    ("DELETE", "/api/calendars/{source_id}"),
    ("POST", "/api/calendars/{source_id}/refresh"),
}
# reachable without a credential (a bearer token is ignored there) or open to a token with the owner's rights
PUBLIC_OR_TOKEN_OK = {
    ("GET", "/api/health"),
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/setup"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/login/totp"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/bootstrap"),
    ("GET", "/api/holidays"),
}


def test_session_only_routes_equal_the_hand_written_table(app):
    actual = {(m, path) for m, r, path in _routes(app) if is_session_only(r)}
    assert actual == SESSION_ONLY | NEW_SESSION_ONLY


def test_every_route_is_guarded_session_only_or_explicitly_public(app):
    """A new route without `Needs(...)` and `SessionOnly` fails here until it is classified on purpose."""
    open_routes = {(m, path) for m, r, path in _routes(app) if not declared(r) and not is_session_only(r)}
    assert open_routes == PUBLIC_OR_TOKEN_OK


def test_settings_writes_need_both_the_right_and_session_only(app):
    found = 0
    for m, r, path in _routes(app):
        if path.startswith("/api/settings") and m in ("PUT", "POST", "DELETE", "PATCH"):
            assert declared(r) and is_session_only(r), (m, path)
            found += 1
    assert found >= 1


def test_anonymous_gets_401_not_403_everywhere(app, client):
    for method, route, path in _routes(app):
        if declared(route):
            r = client.request(method, _url(path), json={} if method in ("POST", "PUT") else None)
            assert r.status_code == 401, (method, path)


# ------------------------------------------------------------------ roles in practice
def test_accountant_reads_money_and_books_expenses_but_changes_nothing_else(boss_client, clients):
    acc = clients["accountant"]
    cust = boss_client.post("/api/customers", json={"name": "Demo GmbH"}).json()
    proj = boss_client.post("/api/projects", json={"name": "Site", "customer_id": cust["id"], "rate": 90}).json()
    boss_client.post("/api/entries", json={"date": "2026-01-05", "minutes": 60, "project_id": proj["id"]})
    inv = boss_client.post(
        "/api/invoices", json={"customer_id": cust["id"], "lines": [{"description": "Work", "qty": 1, "price": 100}]}
    )
    assert inv.status_code == 200, inv.text
    for path in ("/api/customers", "/api/invoices", "/api/offers", "/api/finance", "/api/project-report",
                 "/api/settings", "/api/templates", "/api/entries", "/api/contracts", "/api/expenses"):  # fmt: skip
        assert acc.get(path).status_code == 200, path
    # full project records for the accountant (rates), not the restricted employee view
    assert acc.get(f"/api/projects/{proj['id']}").json()["rate"] == "90.00"
    # expenses and bills: yes
    reply = acc.post("/api/expenses", json={"date": "2026-01-06", "description": "Paper", "net": 10})
    assert reply.status_code == 200
    reply = acc.post("/api/attachments", files={"file": ("bill.txt", b"x")})
    assert reply.status_code == 200
    # everything else: no
    reply = acc.post("/api/customers", json={"name": "X"})
    assert reply.status_code == 403
    reply = acc.put(f"/api/invoices/{inv.json()['id']}", json={"customer_id": cust["id"]})
    assert reply.status_code == 403
    reply = acc.post(f"/api/invoices/{inv.json()['id']}/finalize")
    assert reply.status_code == 403
    reply = acc.delete(f"/api/invoices/{inv.json()['id']}")
    assert reply.status_code == 403
    reply = acc.post("/api/entries", json={"date": "2026-01-05", "minutes": 5})
    assert reply.status_code == 403
    reply = acc.post("/api/timer/start", json={})
    assert reply.status_code == 403
    assert acc.get("/api/dashboard").status_code == 403
    assert acc.get("/api/absences").status_code == 403
    assert acc.get("/api/plan").status_code == 403
    reply = acc.put("/api/settings", json={"company_name": "Hacked"})
    assert reply.status_code == 403
    assert (
        acc.post("/api/users", json={"username": "ev", "name": "E", "role": "boss", "password": PASSWORD}).status_code
        == 403
    )
    # pdf download is a read
    assert acc.get(f"/api/invoices/{inv.json()['id']}/pdf").status_code != 403


def test_accountant_sees_names_only_in_the_team_list_and_no_server_path(clients):
    acc = clients["accountant"]
    users = acc.get("/api/users").json()
    assert users and all(set(u) == {"id", "name", "role"} for u in users)
    b = acc.get("/api/bootstrap").json()
    assert "data_dir" not in b and "vat_rate" in b["settings"]  # full settings, no server folder
    emp = clients["employee"].get("/api/bootstrap").json()
    assert set(emp["settings"]) == {"company_name", "state"}


def test_employee_is_unchanged(boss_client, clients):
    emp = clients["employee"]
    for path in ("/api/invoices", "/api/finance", "/api/customers", "/api/settings", "/api/plan", "/api/templates"):
        assert emp.get(path).status_code == 403, path
    assert emp.get("/api/projects").status_code == 200
    assert emp.get("/api/dashboard").status_code == 200
    reply = emp.post("/api/timer/start", json={})
    assert reply.status_code == 200
    assert (
        emp.post("/api/absences", json={"kind": "vacation", "start": "2026-02-02", "end": "2026-02-03"}).status_code
        == 200
    )
    reply = emp.post("/api/projects", json={"name": "P"})
    assert reply.status_code == 403
    reply = emp.post("/api/attachments", files={"file": ("a.txt", b"x")})
    assert reply.status_code == 403
    assert emp.get(f"/api/dashboard?user_id={clients['boss'].get('/api/auth/me').json()['id']}").status_code == 403


def test_me_and_login_carry_the_permissions(clients, app):
    for role, c in clients.items():
        me = c.get("/api/auth/me").json()
        assert me["role"] == role
        assert me["permissions"] == rights.permissions(role)
    r = TestClient(app).post("/api/auth/login", json={"username": "accx", "password": PASSWORD}).json()
    assert r["user"]["permissions"]["expenses"] == {"read": "all", "write": "all"}


def test_only_the_boss_manages_users_and_roles_are_validated(boss_client, clients):
    ok = boss_client.post(
        "/api/users", json={"username": "zed", "name": "Zed", "role": "accountant", "password": PASSWORD}
    )
    assert ok.status_code == 200 and ok.json()["role"] == "accountant"
    bad = boss_client.post("/api/users", json={"username": "zee", "name": "Z", "role": "root", "password": PASSWORD})
    assert bad.status_code == 422
    for role in ("employee", "accountant"):
        assert (
            clients[role]
            .put(f"/api/users/{clients[role].user_id}", json={"username": "x" + role[:3], "name": "N", "role": "boss"})
            .status_code
            == 403
        )  # no self-promotion


def test_legacy_member_role_is_accepted_and_means_employee(boss_client):
    r = boss_client.post("/api/users", json={"username": "old", "name": "Old", "role": "member", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "employee"


def test_a_user_stored_as_member_still_works(app, boss_client):
    services = app.state.services
    """Another device on an older version may still write `role: member` into the synced database."""
    services.store.save(
        "users",
        {"username": "legacy", "name": "Legacy", "role": "member", "active": True, "workdays": [0, 1, 2, 3, 4],
         "password_hash": __import__("permita38.core.service.auth", fromlist=["x"]).hash_password(PASSWORD),
         "pw_changed": 1},
    )  # fmt: skip
    c = _login(app, "legacy")
    assert c.get("/api/auth/me").json()["role"] == "employee"
    assert c.get("/api/entries").status_code == 200
    assert c.get("/api/invoices").status_code == 403
    listed = boss_client.get("/api/users").json()
    assert next(u for u in listed if u["username"] == "legacy")["role"] == "employee"


def test_an_unknown_stored_role_has_no_rights(app, boss_client):
    services = app.state.services
    from permita38.core.service.auth import hash_password

    services.store.save(
        "users",
        {"username": "odd", "name": "Odd", "role": "wizard", "active": True, "workdays": [0],
         "password_hash": hash_password(PASSWORD), "pw_changed": 1},
    )  # fmt: skip
    c = _login(app, "odd")
    for path in ("/api/projects", "/api/entries", "/api/timer", "/api/users", "/api/customers"):
        assert c.get(path).status_code == 403, path


def test_demoting_to_accountant_and_last_boss_rule(boss_client, clients):
    me = boss_client.get("/api/auth/me").json()
    body = {"username": "boss", "name": "Test Boss", "role": "accountant"}
    reply = boss_client.put(f"/api/users/{me['id']}", json=body)
    assert reply.status_code == 400  # last boss stays boss


def test_role_change_takes_effect_on_the_next_request(boss_client, clients):
    emp = clients["employee"]
    assert emp.get("/api/finance").status_code == 403
    uid = emp.user_id
    boss_client.put(f"/api/users/{uid}", json={"username": "empx", "name": "Employee", "role": "accountant"})
    assert emp.get("/api/finance").status_code == 200
    assert emp.get("/api/timer").status_code == 403


# ------------------------------------------------------------------ migration roles
def test_migration_renames_member_to_employee_and_keeps_boss(tmp_path):
    # Build the database as it was before the roles migration: apply only the earlier migrations of the chain
    # (their SQL, as the runner does), so later migrations never need to be undone by hand.
    data = tmp_path / "data"
    data.mkdir()
    chain = list_migrations("sqlite")
    roles_index = next(i for i, m in enumerate(chain) if m.name == "roles")
    raw = sqlite3.connect(data / DB_FILENAME)
    raw.executescript(
        "CREATE TABLE schema_migrations (id TEXT PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL);"
    )
    for i, m in enumerate(chain[:roles_index], start=1):
        raw.executescript(f"BEGIN IMMEDIATE;\n{m.sql()}\nPRAGMA user_version = {i};\nCOMMIT;")
        raw.execute("INSERT INTO schema_migrations VALUES (?, ?, 'now')", (m.id, m.name))
    raw.commit()
    rows = [
        ("user00000001", "b", "B", "boss", "{}"),
        ("user00000002", "m", "M", "member", '{"future": 1}'),
        ("user00000003", "a", "A", "accountant", "{}"),
    ]
    raw.executemany('INSERT INTO "users" ("id", "username", "name", "role", "extra") VALUES (?, ?, ?, ?, ?)', rows)
    raw.commit()
    raw.close()
    new = SQLiteRepository(data)
    new.startup()
    roles = {u["username"]: u["role"] for u in new.list("users")}
    assert roles == {"b": "boss", "m": "employee", "a": "accountant"}
    assert new.get("users", "user00000002")["future"] == 1  # unknown fields survive
    new.close()


# ------------------------------------------------------------------ review follow-ups
def test_bootstrap_only_contains_what_the_role_may_see(app, boss_client, clients):
    store = app.state.services.store
    store.save("templates", {"name": "Mine", "kind": "offer", "body": "x"})
    (store.data_dir / "users.sync-conflict-20260101.json").write_text("{}")
    boot = {role: c.get("/api/bootstrap").json() for role, c in clients.items()}
    assert boot["boss"]["conflicts"] and any(t.get("name") == "Mine" for t in boot["boss"]["templates"])
    assert boot["employee"]["templates"] == [] and boot["employee"]["conflicts"] == []
    assert boot["accountant"]["templates"] and boot["accountant"]["conflicts"] == []


def test_boss_count_normalises_legacy_and_ignores_unknown_roles(app, boss_client):
    auth = app.state.services.auth
    store = app.state.services.store
    boss_id = boss_client.get("/api/auth/me").json()["id"]
    assert auth._boss_count() == 1
    store.save("users", {"username": "old", "name": "Old", "role": "member", "active": True})
    store.save("users", {"username": "odd", "name": "Odd", "role": "BOSS", "active": True})  # no rights at all
    store.save("users", {"username": "gone", "name": "Gone", "role": "boss", "active": False})
    assert auth._boss_count() == 1
    assert auth._boss_count(excluding=boss_id) == 0
    r = boss_client.put(
        f"/api/users/{boss_id}", json={"username": "boss", "name": "Test Boss", "role": "employee", "active": True}
    )
    assert r.status_code == 400  # the odd-cased and the inactive "boss" do not count


def test_last_boss_check_and_save_share_one_transaction(app, boss_client, monkeypatch):
    store = app.state.services.store
    boss_id = boss_client.get("/api/auth/me").json()["id"]
    depth = {"now": 0, "at_count": None, "at_save": None}
    real_tx, real_count, real_save = store.transaction, app.state.services.auth._boss_count, store.save
    from contextlib import contextmanager

    @contextmanager
    def tx():
        with real_tx():
            depth["now"] += 1
            try:
                yield
            finally:
                depth["now"] -= 1

    def count(*a, **k):
        depth["at_count"] = depth["now"]
        return real_count(*a, **k)

    def save(*a, **k):
        depth["at_save"] = depth["now"]
        return real_save(*a, **k)

    monkeypatch.setattr(store, "transaction", tx)
    monkeypatch.setattr(app.state.services.auth, "_boss_count", count)
    monkeypatch.setattr(store, "save", save)
    boss_client.post("/api/users", json={"username": "b2", "name": "B2", "role": "boss", "password": PASSWORD})
    r = boss_client.put(
        f"/api/users/{boss_id}", json={"username": "boss", "name": "Test Boss", "role": "employee", "active": True}
    )
    assert r.status_code == 200, r.text
    assert depth["at_count"] == 1 and depth["at_save"] == 1


def test_planning_warnings_only_for_roles_with_planning_access():
    from permita38.api.routers.records import _with_warnings

    class Stub:
        class planner:  # noqa: N801
            @staticmethod
            def warnings_for(coll, rec):
                return [{"kind": "x"}]

    rec = {"id": "p1"}
    for role in ("employee", "accountant"):
        assert "planning_warnings" not in _with_warnings(Stub, "projects", rec, {"role": role})
    assert _with_warnings(Stub, "projects", rec, {"role": "boss"})["planning_warnings"] == [{"kind": "x"}]


def test_employee_without_assignment_sees_every_project_and_its_tasks(boss_client, clients):
    emp = clients["employee"]
    mine = boss_client.post("/api/projects", json={"name": "Mine", "member_ids": [emp.user_id]}).json()
    other = boss_client.post("/api/projects", json={"name": "Other"}).json()
    assert "member_ids" not in mine and "member_ids" not in boss_client.get(f"/api/projects/{mine['id']}").json()
    t_mine = boss_client.post("/api/tasks", json={"name": "T1", "project_id": mine["id"]}).json()
    t_other = boss_client.post("/api/tasks", json={"name": "T2", "project_id": other["id"]}).json()
    assert emp.get(f"/api/projects/{other['id']}").status_code == 200
    assert emp.get(f"/api/projects/{mine['id']}").status_code == 200
    listed = emp.get("/api/projects").json()
    assert {mine["id"], other["id"]} <= {p["id"] for p in listed}
    assert all("member_ids" not in p for p in listed)
    assert emp.get(f"/api/tasks/{t_other['id']}").status_code == 200
    assert {t_mine["id"], t_other["id"]} <= {t["id"] for t in emp.get("/api/tasks").json()}
