"""D6a: guards that fail closed against money leaking out of the API as a JSON number
(docs/specs/decimal-money-api.md, rule 5). Three independent guards:

(a) an OpenAPI walker over every RESPONSE schema: a number/integer property must be on an explicit non-money
    allowlist; every other one fails. Money/UnitPrice fields must carry the decimal-string schema.
(b) a response sweep: seeded fake data, every parameterless GET called as the boss, the JSON walked; a number at a
    key that is not allowlisted for its path fails.
(c) a source check: ``to_float`` or a bare ``float(`` must not appear in the response-builder modules.

A new numeric field that is not money is added to the allowlist WITH a reason; a new money field must be a
decimal string. Fake data only."""

import ast
import re
from datetime import date, timedelta
from pathlib import Path

from .test_rbac import _routes
from .test_vat_returns import clients  # noqa: F401  (fixture)
from .test_zm_reports import post_eu, synthetic_charts  # noqa: F401  (fixture)

DECIMAL_SCHEMA = {"type": "string", "format": "decimal", "pattern": r"^-?[0-9]+\.[0-9]{2,6}$"}

# ---------------------------------------------------------------------------------------------------------------------
# Guard (a): property names of response schemas that may be numbers. Anything else must be a decimal string.
# ---------------------------------------------------------------------------------------------------------------------
SCHEMA_NUMERIC_ALLOWLIST = {
    # time stamps and counters (epoch seconds, ordinals)
    "created": "epoch seconds",
    "updated": "epoch seconds",
    "at": "audit entry epoch seconds",
    "expires_at": "token expiry epoch seconds",
    "last_used_at": "token last use epoch seconds",
    "revoked_at": "token revocation epoch seconds",
    "last_fetched": "calendar fetch epoch seconds",
    "opened_at": "fiscal year opening epoch seconds",
    "closed_at": "fiscal year closing epoch seconds",
    "created_at": "journal entry creation epoch seconds",
    "fiscal_year": "calendar year of a journal entry",
    "entry_no": "gapless journal entry number",
    "closing_entry_no": "number of journal entries of a closed fiscal year",
    "line_no": "ordinal of a journal line",
    "bill_rev": "posting revision counter of an expense bill",
    "pay_rev": "posting revision counter of an expense payment",
    "post_rev": "posting revision counter of a trip",
    "locked_at": "period lock epoch seconds",
    "month": "calendar month number of a period lock",
    "next": "audit pagination cursor",
    "status": "audit entry HTTP status code",
    "year": "calendar year of a record reference",
    "age_days": "age of an open item in days",
    "loc": "validation error path element (index)",
    "quarter": "calendar quarter 1..4 (ZM)",
    "months": "months with a business in the previous year (Sondervorauszahlung), 1..12",
    "rows": "number of ZM list rows",
    "drift": "number of ZM entries with late lines",
    "entries": "number of ZM drift entries",
    "finalised_at": "finalising time, epoch seconds",
    # counts
    "count": "number of items",
    "event_count": "number of calendar events",
    "recovery_codes_left": "count of codes",
    "revoked": "number of revoked tokens",
    "pv_children_under_25": "count of children",
    "steuerklasse": "tax class 1..6",
    "ocr_download_size": "bytes",
    "datev_consultant_no": "DATEV consultant number (identifier)",
    "datev_client_no": "DATEV client number (identifier)",
    "datev_account_length": "DATEV account number length 4..8",
    # durations and quantities
    "minutes": "minutes of a time entry",
    "absence_hours": "trip absence hours",
    "hours": "hours",
    "billable_hours": "hours",
    "unbilled_hours": "hours",
    "budget_hours": "hours",
    "planned_hours": "hours",
    "total_hours": "hours",
    "hours_per_week": "hours",
    "weekly_hours": "hours",
    "by_period": "hours per period",
    "info_day": "work limit in hours",
    "info_week": "work limit in hours",
    "info_average": "work limit in hours",
    "warn_day": "work limit in hours",
    "max_day": "work limit in hours",
    "km": "kilometres",
    "mileage_km": "kilometres",
    "qty": "quantity of an invoice/offer line",
    "workdays": "weekday numbers or workday count",
    "refresh_minutes": "minutes",
    # percentages, rates of VAT, ratios, probabilities
    "vat_rate": "VAT rate in percent",
    "deductible": "deductible share in percent",
    "vat_deductible": "deductible share of input VAT in percent",
    "church_tax_percent": "percent",
    "zusatzbeitrag_percent": "percent",
    "customer_dominance_percent": "percent",
    "hebesatz": "municipal trade tax multiplier in percent",
    "win_probability": "probability",
    "planning_default_win_probability": "probability",
    "confidence": "match confidence 0..1",
    # settings in days / weeks / hours
    "offer_valid_days": "days",
    "payment_days": "days",
    "payment_terms_days": "days",
    "subcontractor_hint_weeks": "weeks",
    "subcontractor_hint_hours_per_week": "hours",
}
# ambiguous names: a number only on the named schema (elsewhere the same name is money)
SCHEMA_NUMERIC_PAIRS = {
    ("Line", "vat"): "VAT rate in percent of a line (elsewhere `vat` is an amount)",
    ("VatBox", "flag"): "UStVA box flag 0/1",
    ("VatReturn", "period_no"): "month or quarter number of a VAT return",
    ("VatReturnListItem", "no"): "ordinal of a VAT return in its period",
    ("VatReturnListItem", "lines"): "number of lines of a VAT return",
}

# response properties typed as an untyped dict / Any: (schema, property) -> why no money can hide in it
# (the generated ``...records___result___locals___Result__N`` names are normalised to ``Result``)
SCHEMA_UNTYPED_ALLOWLIST: dict[tuple[str, str], str] = {
    ("Result", "planning_warnings"): "warning details: money converted by money_out.warning_list / convert_warnings",
    ("Result", "sanity_warnings"): "warning details: money converted by money_out.warning_list / convert_warnings",
    ("Result", "extensions"): "project extensions: dates and a reason, no money",
    ("Allocation", "planning_warnings"): "warning details, see Result.planning_warnings",
    ("Allocation", "days"): "per-day hours of an allocation, no money",
    ("ConfirmExpense", "sanity_warnings"): "warning details, see Result.sanity_warnings",
    ("DocumentResult", "planning_warnings"): "warning details, see Result.planning_warnings",
    ("DocumentResult", "sanity_warnings"): "warning details, see Result.sanity_warnings",
    ("IncomingOffer", "lines"): "unit_price converted by money_out.incoming_offer_lines",
    ("IncomingOffer", "plan"): "start, end and hours per week, no money",
    ("IncomingOffer", "planning_warnings"): "warning details, see Result.planning_warnings",
    ("Project", "extensions"): "project extensions: dates and a reason, no money",
    ("ScanOut", "flags"): "scan flags: texts and ids, no money",
    ("ScanOut", "notes"): "scan notes: texts",
    ("ScanOut", "draft"): "scan draft: money converted by money_strings (test_money_scan_drafts)",
    ("ScanOut", "doc"): "scanned document: money converted by money_strings (test_money_scan_drafts)",
    ("ScanResult", "draft"): "scan draft: money converted by money_strings (test_money_scan_drafts)",
    ("ScanResult", "doc"): "scanned document: money converted by money_strings (test_money_scan_drafts)",
    ("ScanResult", "flags"): "scan flags: texts and ids, no money",
    ("SettingsResult", "sanity_warnings"): "warning details, see Result.sanity_warnings",
    ("Supplier", "unavailable"): "date ranges, no money",
    ("SupplierResult", "unavailable"): "date ranges, no money",
    ("SupplierResult", "sanity_warnings"): "warning details, see Result.sanity_warnings",
    ("UserOut", "planning_warnings"): "warning details, see Result.planning_warnings",
    ("ValidationError", "input"): "echo of the rejected request input (422), not a computed amount",
    ("ValidationError", "ctx"): "validation error context (limits), not money",
}

# ---------------------------------------------------------------------------------------------------------------------
# Guard (b): keys that may be numbers in the swept GET responses (untyped dict endpoints add hours, counts, ratios).
# ---------------------------------------------------------------------------------------------------------------------
SWEEP_NUMERIC_KEYS = dict(SCHEMA_NUMERIC_ALLOWLIST) | {
    "worked": "dashboard hours",
    "month": "calendar month number",
    "capacity": "hours",
    # dashboard (hours and days)
    "per_day_needed": "hours per day",
    "remaining_days": "days",
    "today_worked": "hours",
    "booked_today": "hours",
    "booked_week": "hours",
    "left_week": "hours",
    "planned_today": "hours",
    "planned_week": "hours",
    # finance (percent and counts; the amounts there are decimal strings)
    "effective_rate": "effective tax rate in percent",
    "months_left": "months of reserve",
    "kfb_used": "child allowances used (count)",
    # plan (hours and shares)
    "unassigned": "hours",
    "promised": "hours",
    "shortfall_expected": "hours",
    "shortfall_worst_case": "hours",
    "shortfall_contracts": "hours",
    "remaining_contracts": "hours",
    "remaining_offers": "hours",
    "employees": "hours of supply",
    "subcontractors": "hours of supply",
    "assigned_customer": "hours",
    "assigned_internal": "hours",
    "billable_share": "share in percent",
    "busy_hours": "hours",
    "holiday_hours": "hours",
    "over_assigned": "hours",
    "subcontractor_hours": "hours",
    "vacation_hours": "hours",
    # supply and demand of the planner, supplier dependency, offer plan warnings (all hours, weeks, counts)
    "demand": "hours of demand per week",
    "supply": "hours of supply per week",
    "line": "line number in a warning",
    "line_hours": "hours of an offer line",
    "plan_hours": "hours of an offer plan",
    "average_hours_per_week": "hours",
    "longest_run_weeks": "weeks",
    "threshold_hours_per_week": "hours",
    "threshold_weeks": "weeks",
    # lines and numbering
    "quantity": "quantity of an incoming offer line",
    "highest_in_period": "highest document number counter",
    "unlinked_expenses": "count",
}
# exact (route, key) pairs for names that are money elsewhere
SWEEP_NUMERIC_PAIRS = {
    ("/api/finance", "rate"): "tax rate in percent (tax_actual / tax_projected)",
    ("/api/plan", "total"): "projects[].supply.total: hours of supply",
    ("/api/dashboard", "target"): "target hours of the week and of a day",
    ("/api/dashboard", "overtime"): "overtime hours",
    ("/api/dashboard", "remaining"): "hours left to the weekly target",
    ("/api/plan", "assigned"): "people[].assigned: hours",
    ("/api/plan", "booked"): "hours booked on a project",
    ("/api/plan", "covered"): "hours covered by supply",
    ("/api/plan", "remaining"): "hours still to staff (also in warning details)",
    ("/api/plan", "shortfall"): "hours missing (also in warning details and weekly_shortfalls)",
    ("POST /api/contracts", "shortfall"): "capacity warning details: hours missing",
    ("POST /api/offers/{id}/accept", "shortfall"): "capacity warning details: hours missing",
}
# ambiguous names (money in some places, not in others) may NEVER be allowlisted by bare key: only as a (route, key)
# pair above, with a reason, so that a new money field with such a name is caught.
AMBIGUOUS_KEYS = frozenset(
    {
        "rate",
        "total",
        "value",
        "amount",
        "net",
        "vat",
        "gross",
        "price",
        "cost",
        "margin",
        "balance",
        "remaining",
        "free",
        "target",
        "booked",
        "covered",
        "assigned",
        "shortfall",
        "overtime",
        "limit",
    }
)


def test_ambiguous_key_names_are_not_allowlisted_by_bare_key():
    bare = AMBIGUOUS_KEYS & (set(SCHEMA_NUMERIC_ALLOWLIST) | set(SWEEP_NUMERIC_KEYS))
    assert not bare, f"allowlist these only as (route, key) / (schema, key) pairs with a reason: {sorted(bare)}"


# routes whose numbers are all non-money; reason each
SWEEP_ROUTE_ALL_NUMERIC = {
    "/api/data/export/info": "record counts and byte sizes",
}
# REAL money still sent as a JSON number (found by this guard, not allowlisted): (route, key) -> where it comes from.
# Empty: all four findings are fixed. An entry here is a bug to fix, never a permanent allowance.
KNOWN_MONEY_NUMBERS: dict[tuple[str, str], str] = {}

# routes not swept; reason each
SWEEP_SKIP: dict[str, str] = {}

# query strings for routes that reject a bare call
SWEEP_QUERY = {
    "/api/team/timeline": "?start=2026-11-02&end=2026-11-30",
    "/api/plan": "?weeks=8",
}

# ---------------------------------------------------------------------------------------------------------------------
# Guard (c): files inside permita38/api that may use to_float or float( (relative path -> reason). None today.
# ---------------------------------------------------------------------------------------------------------------------
SOURCE_FLOAT_ALLOWLIST: dict[str, str] = {}

API_DIR = Path(__file__).resolve().parents[2] / "permita38" / "api"


# ======================================================================================================================
# (a) OpenAPI walker
# ======================================================================================================================
def _is_decimal_string(node) -> bool:
    return isinstance(node, dict) and all(node.get(k) == v for k, v in DECIMAL_SCHEMA.items())


def _numeric_kind(node) -> bool:
    """True if the (sub)schema can be a JSON number, also inside anyOf/oneOf/allOf, arrays and maps. A $ref points
    at an object schema, whose own properties the walker visits."""
    if not isinstance(node, dict):
        return False
    if node.get("type") in ("number", "integer"):
        return True
    for key in ("anyOf", "oneOf", "allOf"):
        if any(_numeric_kind(x) for x in node.get(key, [])):
            return True
    if node.get("type") == "array":
        return _numeric_kind(node.get("items", {}))
    ap = node.get("additionalProperties")
    return isinstance(ap, dict) and _numeric_kind(ap)


def _untyped(node) -> bool:
    """True if the (sub)schema is an untyped dict / ``Any``: an object without declared properties and without a typed
    value schema, or no type at all. Arrays are looked into, a $ref points at a schema whose properties are visited."""
    if not isinstance(node, dict) or "$ref" in node or "enum" in node or "const" in node:
        return False
    for key in ("anyOf", "oneOf", "allOf"):
        if key in node:
            return any(_untyped(x) for x in node[key])
    t = node.get("type")
    if t == "array":
        return _untyped(node.get("items", {}))
    if t == "object":
        ap = node.get("additionalProperties", True)
        return not node.get("properties") and (ap is True or ap == {})
    return t is None


def _response_schema_names(spec) -> list[str]:
    found: set[str] = set()

    def refs(o):
        if isinstance(o, dict):
            if "$ref" in o:
                found.add(o["$ref"].rsplit("/", 1)[-1])
            for v in o.values():
                refs(v)
        elif isinstance(o, list):
            for v in o:
                refs(v)

    for ops in spec["paths"].values():
        for op in ops.values():
            if isinstance(op, dict):
                for response in op.get("responses", {}).values():
                    refs(response)
    todo, seen = list(found), set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        found.clear()
        refs(spec["components"]["schemas"][name])
        todo.extend(found)
    return sorted(seen)


def test_response_schema_numbers_are_allowlisted_and_money_is_decimal(app):
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    bad, numeric_seen, decimal_fields = [], set(), 0
    untyped, untyped_seen = [], set()
    for name in _response_schema_names(spec):
        short = re.sub(r"^permita38__.*___Result__\d+$", "Result", name)
        for prop, node in schemas[name].get("properties", {}).items():
            if _untyped(node):
                untyped_seen.add((short, prop))
                if (short, prop) not in SCHEMA_UNTYPED_ALLOWLIST:
                    untyped.append(f"{name}.{prop}")
            if _is_decimal_string(node) or any(_is_decimal_string(x) for x in node.get("anyOf", [])):
                decimal_fields += 1
                continue
            if _numeric_kind(node):
                numeric_seen.add(prop)
                if prop not in SCHEMA_NUMERIC_ALLOWLIST and (name, prop) not in SCHEMA_NUMERIC_PAIRS:
                    bad.append(f"{name}.{prop}")
    assert not untyped, (
        "response properties typed as an untyped dict / Any can hide money (type them, or allowlist them in "
        f"SCHEMA_UNTYPED_ALLOWLIST with a reason): {untyped}"
    )
    stale_untyped = set(SCHEMA_UNTYPED_ALLOWLIST) - untyped_seen
    assert not stale_untyped, (
        f"SCHEMA_UNTYPED_ALLOWLIST entries that are typed now (remove them): {sorted(stale_untyped)}"
    )
    assert not bad, f"numeric response properties that are not on the allowlist (money must be a decimal string): {bad}"
    assert decimal_fields > 40, decimal_fields  # the walker really sees the Money/UnitPrice fields
    stale = set(SCHEMA_NUMERIC_ALLOWLIST) - numeric_seen
    assert not stale, f"allowlist entries no response schema uses any more (remove them): {sorted(stale)}"


def test_money_and_unit_price_carry_the_decimal_schema(app):
    schemas = app.openapi()["components"]["schemas"]
    for model, fields in {
        "Expense": ("net", "vat"),
        "Line": ("price", "amount"),
        "Customer": ("rate",),
        "Allocation": ("rate", "invoiced_net", "open_net", "planned_net"),
        "Trip": ("allowance_total", "mileage_total"),
        "PersonalTaxEmployment": ("gross_year", "withheld_year"),
    }.items():
        for f in fields:
            node = schemas[model]["properties"][f]
            assert _is_decimal_string(node) or any(_is_decimal_string(x) for x in node.get("anyOf", [])), (
                model,
                f,
                node,
            )


# ======================================================================================================================
# (b) response sweep
# ======================================================================================================================
def _seed(c):
    """Fake data touching every money-carrying area; each call must succeed."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    ids: dict[str, str] = {}
    responses: list[tuple[str, object]] = []

    def ok(r, keep=True):
        assert r.status_code in (200, 201), (r.request.method, r.request.url, r.text)
        body = r.json()
        if keep and r.request.method != "GET":
            responses.append((_label(r.request.method, r.request.url.path, ids), body))
        return body

    me = ok(c.get("/api/auth/me"))
    cust = ok(
        c.post(
            "/api/customers",
            json={"name": "Demo Client GmbH", "street": "Teststr. 1", "zip": "10115", "city": "Berlin", "rate": 100},
        )
    )
    ids["customer"] = cust["id"]
    proj = ok(c.post("/api/projects", json={"name": "Demo Website", "customer_id": cust["id"]}))
    ids["project"] = proj["id"]
    ids["entry"] = ok(
        c.post(
            "/api/entries",
            json={"user_id": me["id"], "date": today.isoformat(), "minutes": 150, "project_id": proj["id"]},
        )
    )["id"]
    inv = ok(c.post("/api/invoices/from-time", json={"customer_id": cust["id"]}))
    ids["invoice"] = inv["id"]
    ok(c.post(f"/api/invoices/{inv['id']}/finalize"))
    exp = ok(c.post("/api/expenses", json={"date": today.isoformat(), "net": 50, "vat": 9.5, "category": "Software"}))
    ids["expense"] = exp["id"]
    ok(
        c.put(
            f"/api/expenses/{exp['id']}",
            json={"date": today.isoformat(), "net": 52, "vat": 9.88, "category": "Software"},
        )
    )
    ok(c.put(f"/api/customers/{cust['id']}", json={"name": "Demo Client GmbH", "rate": 105}))
    ok(
        c.post(
            "/api/expenses",
            json={
                "date": today.isoformat(),
                "net": 0,
                "vendor": "Hotel Beispiel",
                "lines": [
                    {"description": "Room", "gross": 160.0, "vat_rate": 7},
                    {"description": "City tax", "gross": 6.0, "vat_rate": 0},
                ],
            },
        )
    )
    off = ok(
        c.post(
            "/api/offers",
            json={
                "customer_id": cust["id"],
                "lines": [{"description": "Dev", "qty": 40, "unit": "h", "price": 90}],
                "plan": {
                    "start": today.isoformat(),
                    "end": (today + timedelta(days=60)).isoformat(),
                    "hours_per_week": 10,
                },
            },
        )
    )
    ids["offer"] = off["id"]
    ok(c.post(f"/api/offers/{off['id']}/finalize"))
    ok(c.post(f"/api/offers/{off['id']}/accept"))
    ids["contract"] = ok(
        c.post(
            "/api/contracts",
            json={
                "title": "Support",
                "customer_id": cust["id"],
                "project_id": proj["id"],
                "start": today.isoformat(),
                "end": (today + timedelta(days=60)).isoformat(),
                "total_hours": 40,
                "rate": 90,
            },
        )
    )["id"]
    sup = ok(
        c.post(
            "/api/suppliers",
            json={
                "name": "Example Freelance Studio (Demo)",
                "kind": "person-capacity",
                "workdays": [0, 1, 2, 3],
                "weekly_hours": 24,
                "rate": 80.0,
                "country": "DE",
            },
        )
    )
    ids["supplier"] = sup["id"]
    io = ok(
        c.post(
            "/api/incoming-offers",
            json={
                "supplier_id": sup["id"],
                "reference": "ANG-2026-117",
                "title": "Frontend capacity",
                "received_on": today.isoformat(),
                "valid_until": (today + timedelta(days=30)).isoformat(),
                "lines": [{"description": "Frontend", "quantity": 80, "unit": "h", "unit_price": 80.0}],
                "plan": {
                    "start": monday.isoformat(),
                    "end": (monday + timedelta(days=25)).isoformat(),
                    "hours_per_week": 20.0,
                },
                "project_id": proj["id"],
            },
        )
    )
    ids["incoming offer"] = io["id"]
    ok(c.post(f"/api/incoming-offers/{io['id']}/review"))
    ok(c.post(f"/api/incoming-offers/{io['id']}/accept", json={"confirm_warnings": ["*"], "confirm_expired": True}))
    alloc = ok(
        c.post(
            "/api/allocations",
            json={
                "user_id": me["id"],
                "project_id": proj["id"],
                "start": monday.isoformat(),
                "end": (monday + timedelta(days=25)).isoformat(),
                "hours_per_week": 25.0,
            },
        )
    )
    ids["allocation"] = alloc["id"]
    ok(
        c.put(
            f"/api/allocations/{alloc['id']}",
            json={
                "user_id": me["id"],
                "project_id": proj["id"],
                "start": monday.isoformat(),
                "end": (monday + timedelta(days=25)).isoformat(),
                "hours_per_week": 20.0,
            },
        )
    )
    trip = ok(
        c.post(
            "/api/trips",
            json={
                "purpose": "Customer visit Hamburg",
                "start": "2026-11-09T08:00",
                "end": "2026-11-11T18:00",
                "places": [{"from_date": "2026-11-09", "country": "DE", "city": "Hamburg"}],
            },
        )
    )
    ids["trip"] = trip["id"]
    return ids, responses


def _label(method, path, ids):
    """``POST /api/offers/{id}/accept``: the request with every seeded id replaced by ``{id}``."""
    for value in ids.values():
        path = path.replace(value, "{id}")
    return f"{method} {path}"


def _walk_numbers(obj, path=()):
    """Yield (key path, key, value) for every number leaf; list levels are transparent."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_numbers(v, (*path, k))
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_numbers(v, path)
    elif isinstance(obj, int | float) and not isinstance(obj, bool):
        yield path, (path[-1] if path else ""), obj


# GET routes with an id that are not swept; reason each
SWEEP_ID_SKIP = {
    "/api/attachments/{name}": "binary file",
    "/api/invoices/{rid}/pdf": "PDF",
    "/api/offers/{rid}/pdf": "PDF",
    "/api/trips/{rid}/statement.pdf": "PDF",
    "/api/vat-returns/{rid}": "skeleton (501) until the VAT service lands",
    "/api/vat-returns/{rid}/boxes/{kz}": "skeleton (501) until the VAT service lands",
    "/api/vat-returns/{rid}/export.pdf": "skeleton (501), PDF",
    "/api/vat-returns/{rid}/export.csv": "skeleton (501) until the VAT service lands",
    "/api/vat-returns/{rid}/export.json": "skeleton (501) until the VAT service lands",
    "/api/zm/{rid}": "skeleton (501) until the VAT service lands",
    "/api/zm/{rid}/export.csv": "skeleton (501) until the VAT service lands",
    "/api/zm/{rid}/export.pdf": "skeleton (501) until the VAT service lands",
    "/api/tasks/{rid}": "no money fields (task record without a rate in the seed)",
    "/api/absences/{rid}": "no money fields",
    "/api/templates/{rid}": "no money fields",
    "/api/calendars/{source_id}/preview": "needs a calendar source; no money fields",
    "/api/tokens/{token_id}/audit": "audit entries, no money fields",
    "/api/receipts/{scan_id}": "needs an OCR reader; covered by test_money_scan_drafts",
    "/api/receipts/{scan_id}/pages/{page}": "image",
    "/api/document-scans/{scan_id}": "needs an OCR reader; covered by test_money_scan_drafts",
    "/api/document-scans/{scan_id}/pages/{page}": "image",
}
# the seeded record behind the id parameter of a GET route, by path prefix
SWEEP_ID_RECORD = {
    "/api/projects/": "project",
    "/api/allocations/": "allocation",
    "/api/suppliers/": "supplier",
    "/api/incoming-offers/": "incoming offer",
    "/api/customers/": "customer",
    "/api/entries/": "entry",
    "/api/contracts/": "contract",
    "/api/offers/": "offer",
    "/api/invoices/": "invoice",
    "/api/expenses/": "expense",
    "/api/trips/": "trip",
}


# GET routes parametrised by a calendar year, not by a record id: swept with the current year
SWEEP_YEAR_ROUTES = {"/api/accounting/years/{year}"}


def _id_url(path, ids):
    if path in SWEEP_YEAR_ROUTES:
        return path.replace("{year}", str(date.today().year))
    for prefix, kind in SWEEP_ID_RECORD.items():
        if path.startswith(prefix):
            return re.sub(r"\{[^}]+\}", ids[kind], path, count=1)
    raise AssertionError(f"GET route {path} has an id but is neither swept nor in SWEEP_ID_SKIP")


def _sweep(app, c):
    """(number of GET routes swept, number of write responses checked, offenders, numbers seen).
    Offenders are (route, key, text); seen is the set of (route, key) that were numbers."""
    ids, responses = _seed(c)
    swept, offenders, seen = 0, [], set()

    def check(route, body):
        for kp, key, value in _walk_numbers(body):
            seen.add((route, key))
            if key in SWEEP_NUMERIC_KEYS or (route, key) in SWEEP_NUMERIC_PAIRS:
                continue
            offenders.append((route, key, f"{route} {'.'.join(kp)} = {value!r}"))

    for method, _route, path in _routes(app):
        if method != "GET" or path in SWEEP_SKIP or path in SWEEP_ID_SKIP or path in SWEEP_ROUTE_ALL_NUMERIC:
            continue
        url = _id_url(path, ids) if "{" in path else path
        r = c.get(url + SWEEP_QUERY.get(path, ""))
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            continue
        swept += 1
        check(path, r.json())
    for label, body in responses:
        check(label, body)
    assert swept > 25, swept
    return swept, len(responses), offenders, seen


def _sweep_offenders(app, c):
    return _sweep(app, c)[2]


def test_get_responses_and_write_responses_carry_no_money_numbers(app, boss_client):
    offenders = _sweep_offenders(app, boss_client)
    new = sorted({text for path, key, text in offenders if (path, key) not in KNOWN_MONEY_NUMBERS})
    assert not new, "numbers at keys that are not on the allowlist (money must be a decimal string):\n" + "\n".join(new)


def test_sweep_covers_the_id_routes_and_the_write_responses(app, boss_client):
    swept, written, _, _ = _sweep(app, boss_client)
    id_routes = [p for m, _r, p in _routes(app) if m == "GET" and "{" in p and p not in SWEEP_ID_SKIP]
    assert len(id_routes) >= 10, id_routes
    assert swept >= 40, swept
    assert written >= 15, written


def test_sweep_allowlists_have_no_stale_entries(app, boss_client):
    _, _, _, seen = _sweep(app, boss_client)
    seen_keys = {key for _route, key in seen}
    routes = {p for _m, _r, p in _routes(app)}
    stale = [k for k in SWEEP_NUMERIC_KEYS if k not in SCHEMA_NUMERIC_ALLOWLIST and k not in seen_keys]
    stale_pairs = [pair for pair in SWEEP_NUMERIC_PAIRS if pair not in seen]
    stale_routes = [p for p in SWEEP_ROUTE_ALL_NUMERIC if p not in routes]
    stale_known = [pair for pair in KNOWN_MONEY_NUMBERS if pair not in seen]
    assert not (stale or stale_pairs or stale_routes or stale_known), (
        "allowlist entries no swept response uses any more (remove them):\n"
        f"keys {sorted(stale)}\npairs {sorted(stale_pairs)}\nroutes {stale_routes}\nknown {sorted(stale_known)}"
    )
    assert not (set(SWEEP_ID_SKIP) - routes), sorted(set(SWEEP_ID_SKIP) - routes)


def test_known_money_numbers_is_empty():
    assert not KNOWN_MONEY_NUMBERS, "fix the money leak instead of listing it"


# ======================================================================================================================
# (c) source check
# ======================================================================================================================
def test_no_to_float_or_bare_float_in_the_api_layer():
    hits = []
    for file in sorted(API_DIR.rglob("*.py")):
        rel = file.relative_to(API_DIR.parent.parent).as_posix()
        if rel in SOURCE_FLOAT_ALLOWLIST:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if (isinstance(f, ast.Name) and f.id in ("float", "to_float")) or (
                    isinstance(f, ast.Attribute) and f.attr == "to_float"
                ):
                    hits.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and any(a.name == "to_float" for a in node.names):
                hits.append(f"{rel}:{node.lineno} (import)")
    assert not hits, f"money.to_float / float( in response builders (use money.to_decimal_str): {hits}"
    assert (API_DIR / "money_out.py").is_file()


# ======================================================================================================================
# ZM: no integer money in any /api/zm response (the whole-euro amounts and totals are decimal strings)
# ======================================================================================================================
# keys that may be integers in ZM responses; every other number (also a key named ``total`` or ending in ``_cents``)
# is money in the wrong shape
ZM_INT_ALLOWLIST = {
    "year": "calendar year",
    "quarter": "quarter 1..4",
    "rows": "number of list rows",
    "drift": "number of entries with late lines (list item)",
    "entries": "number of entries with late lines (drift)",
    "entry_no": "journal entry number",
    "created": "epoch seconds",
    "updated": "epoch seconds",
    "finalised_at": "epoch seconds",
}


def _zm_number_offenders(label, body):
    return [
        f"{label} {'.'.join(kp)} = {value!r}"
        for kp, key, value in _walk_numbers(body)
        if key not in ZM_INT_ALLOWLIST or key == "total" or key.endswith("_cents")
    ]


def test_zm_sweep_detects_the_old_integer_money_shape():
    old = {
        "total": 7001,
        "lines": [{"amount": "3001.00", "cents": 300110}],
        "drift": {"entries": 1, "lines": [{"base_cents": 50000}]},
    }
    assert len(_zm_number_offenders("old", old)) == 3
    assert (
        _zm_number_offenders("new", {"total": "7001.00", "drift": {"entries": 1, "lines": [{"base": "500.00"}]}}) == []
    )


def test_zm_responses_carry_no_integer_money(app, clients, synthetic_charts):  # noqa: F811
    from permita38.core.service.journal import JournalService

    boss = clients["boss"]
    journal = JournalService(app.state.services.store, clock=lambda: 1_800_000_000)
    post_eu(journal, "ATU00000001", 300110, date(2024, 2, 5))
    post_eu(journal, "FR00000000001", 400000, date(2024, 3, 5))
    seen = []

    def got(r, status):
        assert r.status_code == status, (r.request.method, r.request.url, r.text)
        seen.append((f"{r.request.method} {r.request.url.path}", r.json()))
        return r.json()

    got(boss.get("/api/zm?year=2024"), 200)
    got(boss.get("/api/zm/preview?year=2024&quarter=1"), 200)
    rid = got(boss.post("/api/zm", json={"year": 2024, "quarter": 1}), 201)["id"]
    got(boss.post(f"/api/zm/{rid}/finalize"), 200)
    got(boss.post(f"/api/zm/{rid}/filed", json={"filed_on": "2024-04-20"}), 200)
    post_eu(journal, "ATU00000001", 50000, date(2024, 3, 6))  # late line: drift
    final = got(boss.get(f"/api/zm/{rid}"), 200)
    assert final["drift"]["entries"] == 1  # the drift lines are part of what is swept
    got(boss.get("/api/zm?year=2024"), 200)
    corr = got(boss.post("/api/zm", json={"year": 2024, "quarter": 1, "amends_id": rid}), 201)
    got(boss.get(f"/api/zm/{corr['id']}"), 200)
    got(boss.post(f"/api/zm/{corr['id']}/finalize"), 200)
    offenders = [o for label, body in seen for o in _zm_number_offenders(label, body)]
    assert not offenders, "money in a ZM response must be a decimal string:\n" + "\n".join(offenders)
    assert any("total" in str(b) for _l, b in seen)  # the list was swept


# ======================================================================================================================
# accounting journal and report routes: every amount is a decimal string, no allowlist entry for a money field
# ======================================================================================================================
def test_accounting_report_routes_send_money_only_as_decimal_strings(app, scenario):
    seen_strings = 0
    paths = [
        "journal", "accounts", "reports/trial-balance", "reports/pnl", "reports/balance-sheet",
        "reports/open-items", "reports/ledger?role=bank",
    ]  # fmt: skip
    for path in paths:
        r = scenario.get(f"/api/accounting/{path}")
        assert r.status_code == 200, (path, r.text)
        for kp, key, value in _walk_numbers(r.json()):
            assert key in SWEEP_NUMERIC_KEYS and key not in AMBIGUOUS_KEYS, (path, ".".join(kp), value)
        seen_strings += len(re.findall(r'"-?[0-9]+\.[0-9]{2}"', r.text))
    assert seen_strings > 60, seen_strings


def test_accounting_eur_route_sends_money_only_as_decimal_strings(eur_scenario):
    r = eur_scenario.get("/api/accounting/reports/eur", params={"year": 2025})
    assert r.status_code == 200, r.text
    for kp, key, value in _walk_numbers(r.json()):
        assert key in SWEEP_NUMERIC_KEYS and key not in AMBIGUOUS_KEYS, (".".join(kp), value)
    assert len(re.findall(r'"-?[0-9]+\.[0-9]{2}"', r.text)) >= 8
