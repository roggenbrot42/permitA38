"""D4: the dict endpoints send money as decimal strings (docs/specs/decimal-money-api.md rule 4).

Fake data only. For each endpoint every money key must be a decimal string and the named non-money keys (ratios,
counts, hours, percentages, probabilities) must stay numbers.
"""

import re
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from permita38.api import money_out

DECIMAL = re.compile(r"^-?[0-9]+\.[0-9]{2,6}$")
TODAY = date.today()
NEXT_MONDAY = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)
END = NEXT_MONDAY + timedelta(days=25)


def _is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def numbers(obj: Any, path: tuple = ()) -> list[tuple[tuple, Any]]:
    """Every JSON number in ``obj`` with its key path (list levels are transparent)."""
    out: list[tuple[tuple, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += numbers(v, (*path, k))
    elif isinstance(obj, list):
        for v in obj:
            out += numbers(v, path)
    elif _is_num(obj):
        out.append((path, obj))
    return out


def values_at(obj: Any, key: str) -> list[Any]:
    """Every value stored under dict key ``key`` anywhere in ``obj``."""
    out: list[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            out += values_at(v, key)
    elif isinstance(obj, list):
        for v in obj:
            out += values_at(v, key)
    return out


def assert_money(obj: Any, key: str, minimum: int = 1) -> None:
    values = [v for v in values_at(obj, key) if v is not None]
    assert len(values) >= minimum, (key, "not present")
    for v in values:
        assert isinstance(v, str) and DECIMAL.match(v), (key, v)


def assert_only_these_numbers(obj: Any, allowed: set[str]) -> None:
    left = {(p, v) for p, v in numbers(obj) if p[-1] not in allowed}
    assert not left, sorted(left, key=str)[:10]


@pytest.fixture
def seeded(boss_client):
    c = boss_client
    me = c.get("/api/auth/me").json()
    cust = c.post("/api/customers", json={"name": "Demo Client GmbH (Demo)", "rate": 100}).json()
    proj = c.post("/api/projects", json={"name": "Demo project", "customer_id": cust["id"]}).json()
    for minutes in (150, 60):
        c.post(
            "/api/entries",
            json={"user_id": me["id"], "date": TODAY.isoformat(), "minutes": minutes, "project_id": proj["id"]},
        )
    inv = c.post("/api/invoices/from-time", json={"customer_id": cust["id"]}).json()
    fin = c.post(f"/api/invoices/{inv['id']}/finalize").json()
    fin.update(status="paid", paid_date=TODAY.isoformat())
    assert c.put(f"/api/invoices/{inv['id']}", json=fin).status_code == 200
    c.post(
        "/api/expenses",
        json={"date": TODAY.isoformat(), "net": 50, "vat": 9.5, "category": "Software", "project_id": proj["id"]},
    )
    # a contract and an offer with a plan, so the planner has items with rates, values and a win probability
    c.post(
        "/api/contracts",
        json={
            "title": "Demo contract",
            "project_id": proj["id"],
            "start": NEXT_MONDAY.isoformat(),
            "end": END.isoformat(),
            "hours_per_week": 10,
            "rate": 70.5,
        },
    )
    off = c.post(
        "/api/offers",
        json={
            "customer_id": cust["id"],
            "project_id": proj["id"],
            "lines": [{"description": "Dev", "qty": 40, "unit": "h", "price": 90}],
            "plan": {"start": NEXT_MONDAY.isoformat(), "end": END.isoformat(), "hours_per_week": 8},
        },
    ).json()
    assert c.post(f"/api/offers/{off['id']}/finalize").status_code == 200
    # an external allocation of a subcontractor with an accepted incoming offer (commitments)
    sup = c.post(
        "/api/suppliers",
        json={
            "name": "Example Freelance Studio (Demo)",
            "kind": "person-capacity",
            "workdays": [0, 1, 2, 3],
            "weekly_hours": 24,
            "rate": 80.0,
            "country": "DE",
        },
    ).json()
    io = c.post(
        "/api/incoming-offers",
        json={
            "supplier_id": sup["id"],
            "reference": "ANG-1",
            "title": "Frontend capacity",
            "received_on": TODAY.isoformat(),
            "valid_until": (TODAY + timedelta(days=60)).isoformat(),
            "lines": [{"description": "Frontend", "quantity": 80, "unit": "h", "unit_price": 80.0}],
            "plan": {"start": NEXT_MONDAY.isoformat(), "end": END.isoformat(), "hours_per_week": 20.0},
            "project_id": proj["id"],
        },
    )
    assert io.status_code == 201, io.text
    acc = c.post(f"/api/incoming-offers/{io.json()['id']}/accept", json={})
    assert acc.status_code == 200, acc.text
    return c


FINANCE_NUMBERS = {"month", "year", "rate", "effective_rate", "months_left", "kfb_used"}


def test_finance_boss_money_keys_are_strings(seeded):
    f = seeded.get("/api/finance").json()
    for k in ("revenue", "vat_out", "expenses", "vat_in", "profit", "vat_due", "tax_set_aside", "vat_set_aside", "social"):
        assert_money(f["months"], k, 12)
    assert_money(f["months"], "take_home", 1)  # None for the months not begun yet
    for k in ("revenue", "vat_out", "expenses", "vat_in", "profit", "vat_due"):
        assert DECIMAL.match(f["totals"][k]), k
    for k in ("projected_profit", "social_year", "take_home", "open_receivables"):
        assert DECIMAL.match(f[k]), k
    assert f["categories"]["Software"] == "50.00"
    for block in ("tax_actual", "tax_projected"):
        for k in ("profit", "social", "est", "soli", "church", "gewst", "kst", "tax_due", "total", "zve", "withheld"):
            assert DECIMAL.match(f[block][k]), (block, k)
        assert DECIMAL.match(f[block]["insurance"]["kv"]) and DECIMAL.match(f[block]["vorsorge"]["A"])
    for k in ("profit_ytd", "tax_due", "withheld", "owed"):
        assert DECIMAL.match(f["tax_owed"][k]), k
    for k in ("due_year", "withheld", "owed_now", "remaining", "monthly"):
        assert DECIMAL.match(f["reserve"][k]), k
    # ratios and counts stay numbers
    assert isinstance(f["effective_rate"], float) and isinstance(f["reserve"]["months_left"], int)
    assert _is_num(f["tax_actual"]["rate"]) and _is_num(f["tax_projected"]["rate"])
    assert _is_num(f["tax_actual"]["kfb_used"])
    assert _is_num(f["months"][0]["month"]) and _is_num(f["year"])
    # nothing else in the whole response is a number (a new money key cannot slip through as one)
    assert_only_these_numbers(f, FINANCE_NUMBERS)
    assert f["totals"]["revenue"] == "350.00"  # 3.5 h x 100 EUR/h of the paid invoice


def test_finance_redacted_money_keys_are_strings(app, seeded):
    made = seeded.post("/api/tokens", json={"label": "t", "read_only": True})
    assert made.status_code == 201, made.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {made.json()['token']}"
    f = c.get("/api/finance").json()
    assert f["redacted"] is True and f["reserve"] == {"redacted": True}
    assert "church" not in f["tax_actual"]
    for k in ("revenue", "vat_out", "expenses", "vat_in", "profit", "vat_due", "vat_set_aside"):
        assert_money(f["months"], k, 12)
    assert DECIMAL.match(f["totals"]["profit"]) and DECIMAL.match(f["projected_profit"])
    assert DECIMAL.match(f["open_receivables"]) and f["categories"]["Software"] == "50.00"
    assert DECIMAL.match(f["tax_actual"]["gewst"]) and DECIMAL.match(f["tax_projected"]["kst_soli"])
    assert f["tax_actual"]["redacted"] is True
    assert_only_these_numbers(f, {"month", "year"})


def test_project_report_money_keys_are_strings(seeded):
    rows = seeded.get("/api/project-report").json()
    row = next(r for r in rows.values() if r["revenue"] != "0.00")
    for k in ("revenue", "cost", "expenses", "margin", "unbilled_revenue", "committed_external_cost"):
        assert DECIMAL.match(row[k]), k
    assert row["expenses"] == "50.00"
    assert _is_num(row["hours"]) and _is_num(row["unbilled_hours"])  # hours stay numbers
    assert_only_these_numbers(rows, {"hours", "unbilled_hours"})


def test_commitments_money_keys_are_strings(seeded):
    com = seeded.get("/api/finance/commitments").json()
    assert com["items"]
    for k in ("planned_net", "invoiced_net", "open_net"):
        assert_money(com["items"], k)
        assert_money(com["by_supplier"], k)
        assert_money(com["by_project"], k)
        assert DECIMAL.match(com["totals"][k]), k
    assert _is_num(com["items"][0]["planned_hours"])
    assert_only_these_numbers(com, {"planned_hours", "share_percent"})


def test_commitment_hint_money_keys_are_strings(seeded):
    com = seeded.get("/api/finance/commitments").json()
    assert com["hints"], "one customer carries all the revenue"
    assert_money(com["hints"], "revenue_customer")
    assert_money(com["hints"], "revenue_total")
    assert all(_is_num(h["details"]["share_percent"]) for h in com["hints"])


def test_plan_money_keys_are_strings(seeded):
    plan = seeded.get("/api/plan", params={"weeks": 6}).json()
    items = [i for p in plan["projects"] for i in p.get("items", [])]
    assert {i["kind"] for i in items} == {"contract", "offer"}
    for i in items:
        for k in ("rate", "value", "invoiced", "open"):
            assert DECIMAL.match(i[k]), (i["kind"], k, i[k])
    contract = next(i for i in items if i["kind"] == "contract")
    assert contract["rate"] == "70.50" and contract["expected_value"] is None
    offer = next(i for i in items if i["kind"] == "offer")
    assert DECIMAL.match(offer["expected_value"]) and _is_num(offer["win_probability"])
    proj = next(p for p in plan["projects"] if p.get("items"))
    for k in ("planned_subcontractor_cost", "contract_value", "margin"):
        assert DECIMAL.match(proj[k]), k
    # hours, shortfalls and probabilities stay numbers
    for k in ("promised", "booked", "remaining", "covered", "shortfall"):
        assert _is_num(contract[k]), k
    money_names = set(money_out.PLAN_KEYS)
    assert not [p for p, _ in numbers(plan) if p[-1] in money_names], "a money key is still a number"


def test_helper_leaves_none_bool_text_and_other_keys_alone():
    data = {"a": 1.5, "b": None, "c": True, "d": "x", "n": [{"a": 2}], "e": {"a": 3, "rate": 0.25}, "other": 4.5}
    out = money_out.money_strings(data, {"a", "b", "c", "d"}, exclude_paths={("e", "a")})
    assert out == {
        "a": "1.50",
        "b": None,
        "c": True,
        "d": "x",
        "n": [{"a": "2.00"}],
        "e": {"a": 3, "rate": 0.25},
        "other": 4.5,
    }
    out = money_out.all_money_strings(
        {"x": 1, "rate": 0.5, "w": ["t"], "nested": {"y": None, "z": 2.5}}, except_keys={"rate"}
    )
    assert out == {"x": "1.00", "rate": 0.5, "w": ["t"], "nested": {"y": None, "z": "2.50"}}
