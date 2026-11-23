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
from datetime import date, timedelta
from pathlib import Path

import pytest

from .test_rbac import _routes

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
    "next": "audit pagination cursor",
    "status": "audit entry HTTP status code",
    "year": "calendar year of a record reference",
    "loc": "validation error path element (index)",
    # counts
    "count": "number of items",
    "event_count": "number of calendar events",
    "recovery_codes_left": "count of codes",
    "revoked": "number of revoked tokens",
    "pv_children_under_25": "count of children",
    "steuerklasse": "tax class 1..6",
    "ocr_download_size": "bytes",
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
}

# ---------------------------------------------------------------------------------------------------------------------
# Guard (b): keys that may be numbers in the swept GET responses (untyped dict endpoints add hours, counts, ratios).
# ---------------------------------------------------------------------------------------------------------------------
SWEEP_NUMERIC_KEYS = dict(SCHEMA_NUMERIC_ALLOWLIST) | {
    "worked": "dashboard hours",
    "target": "dashboard target hours",
    "balance": "dashboard hours balance",
    "month": "calendar month number",
    "week": "calendar week number",
    "weeks": "number of weeks",
    "day": "day number",
    "total": "count or hours total",
    "capacity": "hours",
    "booked": "hours",
    "free": "hours",
    "percent": "percent",
    "ratio": "ratio",
    "version": "schema version",
    "limit": "page size",
    "size": "bytes",
    # dashboard (hours and days)
    "remaining": "hours left",
    "overtime": "hours",
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
    "assigned": "hours",
    "unassigned": "hours",
    "covered": "hours",
    "promised": "hours",
    "shortfall": "hours",
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
    # lines and numbering
    "quantity": "quantity of an incoming offer line",
    "highest_in_period": "highest document number counter",
    "unlinked_expenses": "count",
}
# exact (route, key) pairs for names that are money elsewhere
SWEEP_NUMERIC_PAIRS = {
    ("/api/finance", "rate"): "tax rate in percent (tax_actual / tax_projected)",
}
# routes whose numbers are all non-money; reason each
SWEEP_ROUTE_ALL_NUMERIC = {
    "/api/data/export/info": "record counts and byte sizes",
}
# REAL money still sent as a JSON number (found by this guard, not allowlisted): (route, key) -> where it comes from.
# test_known_money_numbers_are_fixed is xfail until the fix lands; then remove the entry here.
KNOWN_MONEY_NUMBERS = {
    ("/api/bootstrap", "default_rate"): "settings.default_rate (UnitPrice) in the bootstrap dict",
    ("/api/bootstrap", "planning_min_rate"): "settings.planning_min_rate (UnitPrice) in the bootstrap dict",
    ("/api/incoming-offers", "unit_price"): "lines[].unit_price of the incoming-offer list",
    ("/api/suppliers/vendor-suggestions", "net_total"): "groups[].net_total of the vendor suggestions",
}

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
    for name in _response_schema_names(spec):
        for prop, node in schemas[name].get("properties", {}).items():
            if _is_decimal_string(node) or any(_is_decimal_string(x) for x in node.get("anyOf", [])):
                decimal_fields += 1
                continue
            if _numeric_kind(node):
                numeric_seen.add(prop)
                if prop not in SCHEMA_NUMERIC_ALLOWLIST and (name, prop) not in SCHEMA_NUMERIC_PAIRS:
                    bad.append(f"{name}.{prop}")
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

    def ok(r):
        assert r.status_code in (200, 201), (r.request.method, r.request.url, r.text)
        return r.json()

    me = ok(c.get("/api/auth/me"))
    cust = ok(
        c.post(
            "/api/customers",
            json={"name": "Demo Client GmbH", "street": "Teststr. 1", "zip": "10115", "city": "Berlin", "rate": 100},
        )
    )
    proj = ok(c.post("/api/projects", json={"name": "Demo Website", "customer_id": cust["id"]}))
    ok(
        c.post(
            "/api/entries",
            json={"user_id": me["id"], "date": today.isoformat(), "minutes": 150, "project_id": proj["id"]},
        )
    )
    inv = ok(c.post("/api/invoices/from-time", json={"customer_id": cust["id"]}))
    ok(c.post(f"/api/invoices/{inv['id']}/finalize"))
    ok(c.post("/api/expenses", json={"date": today.isoformat(), "net": 50, "vat": 9.5, "category": "Software"}))
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
    ok(c.post(f"/api/offers/{off['id']}/finalize"))
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
    ok(
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
    ok(
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
    ok(
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


def _sweep_offenders(app, c):
    _seed(c)
    swept, offenders = 0, []
    for method, _route, path in _routes(app):
        if method != "GET" or "{" in path or path in SWEEP_SKIP or path in SWEEP_ROUTE_ALL_NUMERIC:
            continue
        r = c.get(path + SWEEP_QUERY.get(path, ""))
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            continue
        swept += 1
        for kp, key, value in _walk_numbers(r.json()):
            if key in SWEEP_NUMERIC_KEYS or (path, key) in SWEEP_NUMERIC_PAIRS:
                continue
            offenders.append((path, key, f"{path} {'.'.join(kp)} = {value!r}"))
    assert swept > 25, swept
    return offenders


def test_parameterless_get_responses_carry_no_money_numbers(app, boss_client):
    offenders = _sweep_offenders(app, boss_client)
    new = sorted({text for path, key, text in offenders if (path, key) not in KNOWN_MONEY_NUMBERS})
    assert not new, "numbers at keys that are not on the allowlist (money must be a decimal string):\n" + "\n".join(new)


@pytest.mark.xfail(
    strict=True,
    reason="money still sent as a JSON number: GET /api/bootstrap settings.default_rate and planning_min_rate, "
    "GET /api/incoming-offers lines[].unit_price, GET /api/suppliers/vendor-suggestions groups[].net_total",
)
def test_known_money_numbers_are_fixed(app, boss_client):
    offenders = _sweep_offenders(app, boss_client)
    left = sorted({text for path, key, text in offenders if (path, key) in KNOWN_MONEY_NUMBERS})
    assert not left, "\n".join(left)


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
