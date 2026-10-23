"""Itemised expenses (docs/specs/itemised-expenses.md): a bill with several lines, each with its own VAT rate and
deductible share.

Every expected number is computed here with ``Decimal`` / ROUND_HALF_UP by the small oracle below, never with
``permita38.core.money`` or ``calc``. The hand-computed examples of the spec are pinned as literals as well.

Decisions where the spec is silent (the implementation must follow them):
* with ``lines`` present the top-level ``net`` may be omitted in the request (it is ignored anyway); without
  ``lines`` it stays required;
* a validation error for a line has ``loc == ["body", "lines", <index>, <field>]`` (index is an int);
* more than 50 lines, a negative ``gross``, a rate outside 0..100 and a description over 2000 chars give 422;
* ``vat_rate`` of a line is required; ``description`` and ``deductible`` (default 100) are optional;
* ``gross`` 0 is allowed; unknown fields inside a line survive a round trip (extra="allow");
* ``net``/``vat`` sent inside a line are ignored and recomputed;
* an empty ``lines: []`` is a classic expense: the response does not need to carry ``lines`` (None/absent/[]), and
  the top-level fields stay exactly as the client sent them.
"""

from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

D = Decimal


def q2(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def dd(x) -> Decimal:
    return D(str(x))


# ------------------------------------------------------------------ independent oracle
def derive(lines):
    """[(gross, rate)] -> ([(net_i, vat_i)], {rate: (group_vat, group_net)}) following the spec's rules 1 and 2."""
    groups: dict[Decimal, list[int]] = {}
    for i, (g, r) in enumerate(lines):
        groups.setdefault(D(str(r)), []).append(i)
    out: list = [None] * len(lines)
    summary = {}
    for r, idx in groups.items():
        total = sum((D(str(lines[i][0])) for i in idx), D(0))
        group_vat = q2(total * r / (100 + r))
        used = D(0)
        for n, i in enumerate(idx):
            g = D(str(lines[i][0]))
            vat = group_vat - used if n == len(idx) - 1 else q2(g * r / (100 + r))
            used += vat
            out[i] = (g - vat, vat)
        summary[r] = (group_vat, total - group_vat)
    return out, summary


def finance_oracle(expenses, klein=False):
    """expenses: [{'lines': [(gross, rate, deductible, vat_deductible|None, category|None)], 'category': str|None}]
    -> (cost, vat_in, {category: cost})"""
    cost = vat_in = D(0)
    cats: dict[str, Decimal] = {}
    for e in expenses:
        derived, _ = derive([(ln[0], ln[1]) for ln in e["lines"]])
        for (net, vat), ln in zip(derived, e["lines"], strict=True):
            ded = D(str(ln[2])) / 100
            vd = ded if ln[3] is None else D(str(ln[3])) / 100
            c = q2((net + vat) * ded) if klein else q2(net * ded)
            cost += c
            if not klein:
                vat_in += q2(vat * vd)
            cat = ln[4] or e.get("category") or "Other"
            cats[cat] = cats.get(cat, D(0)) + c
    return cost, vat_in, cats


# ------------------------------------------------------------------ API helpers
def L(gross, rate, desc="x", **kw):
    return {"description": desc, "gross": gross, "vat_rate": rate, **kw}


def book(c, lines, status=(200, 201), **kw):
    body = {"date": "2026-03-10", "net": 0, "lines": lines, **kw}  # net is ignored when lines are present
    r = c.post("/api/expenses", json=body)
    assert r.status_code in status, r.text
    return r.json()


def totals(c):
    return c.get("/api/finance", params={"year": 2026}).json()


HOTEL = [L(160.00, 7, "Room 2 nights"), L(30.00, 19, "Breakfast"), L(6.00, 0, "City tax")]


# ------------------------------------------------------------------ 1. the worked example, per rate group
def test_hotel_bill_worked_example_literals(boss_client):
    got = book(boss_client, HOTEL, vendor="Hotel Beispiel")
    by_desc = {x["description"]: x for x in got["lines"]}
    assert (dd(by_desc["Room 2 nights"]["vat"]), dd(by_desc["Room 2 nights"]["net"])) == (D("10.47"), D("149.53"))
    assert (dd(by_desc["Breakfast"]["vat"]), dd(by_desc["Breakfast"]["net"])) == (D("4.79"), D("25.21"))
    assert (dd(by_desc["City tax"]["vat"]), dd(by_desc["City tax"]["net"])) == (D("0.00"), D("6.00"))
    # lines keep their order and their input
    assert [x["description"] for x in got["lines"]] == ["Room 2 nights", "Breakfast", "City tax"]
    assert [dd(x["gross"]) for x in got["lines"]] == [D("160.00"), D("30.00"), D("6.00")]
    # top-level fields are overwritten by the server: gross 196,00 = net 180,74 + VAT 15,26
    assert dd(got["net"]) == D("180.74") and dd(got["vat"]) == D("15.26")
    assert dd(got["net"]) + dd(got["vat"]) == D("196.00")
    assert got["vat_rate"] is None  # mixed rates
    assert dd(got["deductible"]) == 100


def test_hotel_bill_matches_the_oracle(boss_client):
    got = book(boss_client, HOTEL)
    derived, _ = derive([(160.00, 7), (30.00, 19), (6.00, 0)])
    assert [(dd(x["net"]), dd(x["vat"])) for x in got["lines"]] == derived


def test_vat_is_rounded_per_rate_group_not_per_line(boss_client):
    got = book(boss_client, [L(0.50, 7, "a"), L(0.50, 7, "b")])
    # per line would be 0,03 + 0,03 = 0,06; the group VAT is q2(1.00 * 7/107) = 0,07, the last line takes the rest
    assert [dd(x["vat"]) for x in got["lines"]] == [D("0.03"), D("0.04")]
    assert [dd(x["net"]) for x in got["lines"]] == [D("0.47"), D("0.46")]
    assert dd(got["vat"]) == D("0.07") and dd(got["net"]) == D("0.93")
    assert got["vat_rate"] == 7  # all lines share one rate -> the common rate


def test_last_line_of_a_group_takes_the_remainder_even_when_groups_are_interleaved(boss_client):
    lines = [L(0.50, 7, "a"), L(10.00, 19, "b"), L(0.50, 7, "c"), L(0.50, 7, "d")]
    got = book(boss_client, lines)
    derived, summary = derive([(ln["gross"], ln["vat_rate"]) for ln in lines])
    assert [(dd(x["net"]), dd(x["vat"])) for x in got["lines"]] == derived
    # group 7 %: gross 1.50 -> VAT q2(1.5*7/107)=0.10 ; lines 0.03 + 0.03 + remainder 0.04
    assert [dd(x["vat"]) for x in got["lines"] if x["vat_rate"] == 7] == [D("0.03"), D("0.03"), D("0.04")]
    assert dd(got["vat"]) == summary[D(7)][0] + summary[D(19)][0]


def test_single_line_and_single_rate_set_vat_rate(boss_client):
    got = book(boss_client, [L(119.00, 19, "only")])
    assert dd(got["net"]) == D("100.00") and dd(got["vat"]) == D("19.00") and got["vat_rate"] == 19


def test_zero_gross_line_and_zero_rate_line(boss_client):
    got = book(boss_client, [L(0, 19, "free"), L(6.00, 0, "tax")])
    assert dd(got["net"]) == D("6.00") and dd(got["vat"]) == D("0.00")
    assert got["vat_rate"] is None


def test_client_sent_top_level_and_line_values_are_ignored_when_lines_are_present(boss_client):
    lines = [{**ln, "net": 999, "vat": 888} for ln in HOTEL]
    got = book(boss_client, lines, net=1, vat=2, vat_rate=19, deductible=10)
    assert dd(got["net"]) == D("180.74") and dd(got["vat"]) == D("15.26")
    assert got["vat_rate"] is None and dd(got["deductible"]) == 100
    assert [dd(x["vat"]) for x in got["lines"]] == [D("10.47"), D("4.79"), D("0.00")]
    again = boss_client.get(f"/api/expenses/{got['id']}").json()
    assert dd(again["net"]) == D("180.74") and dd(again["vat"]) == D("15.26")


def test_net_may_be_omitted_with_lines_but_not_without(boss_client):
    book(boss_client, HOTEL)  # no top-level net at all
    r = boss_client.post("/api/expenses", json={"date": "2026-03-10", "vat": 1})
    assert r.status_code == 422


def test_effective_top_level_deductible_share(boss_client):
    lines = [L(100.00, 19, "lunch", deductible=70), L(119.00, 19, "other", deductible=100)]
    got = book(boss_client, lines)
    n1, n2 = D("84.03"), D("100.00")
    # group 19 %: gross 219,00 -> VAT q2(219*19/119)=34.97 ; line 1 VAT 15.97, last line takes 19.00
    assert [dd(x["vat"]) for x in got["lines"]] == [D("15.97"), D("19.00")]
    expected = q2(100 * (n1 * 70 + n2 * 100) / (n1 + n2))
    assert dd(got["deductible"]) == expected == D("86.30")


def test_effective_deductible_is_100_when_the_net_sum_is_zero(boss_client):
    got = book(boss_client, [L(0, 19, deductible=0)])
    assert dd(got["deductible"]) == 100


def test_deductible_defaults_to_100_per_line(boss_client):
    got = book(boss_client, [L(10, 19)])
    assert dd(got["lines"][0]["deductible"]) == 100


# ------------------------------------------------------------------ 2. round trip, update, listing
def test_round_trip_get_list_put(boss_client):
    created = book(boss_client, HOTEL, vendor="Hotel Beispiel", category="Travel")
    assert boss_client.get(f"/api/expenses/{created['id']}").json() == created
    assert created in boss_client.get("/api/expenses").json()
    new_lines = [L(100.00, 19, "Lunch", deductible=70, vat_deductible=100, category="Business meals")]
    r = boss_client.put(f"/api/expenses/{created['id']}", json={"date": "2026-03-10", "net": 0, "lines": new_lines})
    assert r.status_code == 200, r.text
    upd = r.json()
    assert len(upd["lines"]) == 1 and dd(upd["net"]) == D("84.03") and dd(upd["vat"]) == D("15.97")
    assert upd["lines"][0]["category"] == "Business meals"
    assert upd["lines"][0]["vat_deductible"] == 100 and upd["lines"][0]["deductible"] == 70
    assert boss_client.get(f"/api/expenses/{created['id']}").json() == upd


def test_put_back_to_a_single_amount_drops_the_lines(boss_client):
    created = book(boss_client, HOTEL)
    r = boss_client.put(
        f"/api/expenses/{created['id']}", json={"date": "2026-03-10", "net": 50, "vat": 9.5, "vat_rate": 19}
    )
    assert r.status_code == 200, r.text
    assert not r.json().get("lines")
    assert dd(r.json()["net"]) == D("50") and dd(r.json()["vat"]) == D("9.5")


def test_unknown_fields_in_lines_and_on_the_expense_survive(boss_client):
    lines = [L(10, 19, "x", future_flag={"a": [1, 2]}, note="keep me")]
    got = book(boss_client, lines, future_top="yes")
    again = boss_client.get(f"/api/expenses/{got['id']}").json()
    assert again["lines"][0]["future_flag"] == {"a": [1, 2]} and again["lines"][0]["note"] == "keep me"
    assert again["future_top"] == "yes"


# ------------------------------------------------------------------ 3. old single-amount expenses are unchanged
def test_classic_expense_is_unchanged_by_the_feature(boss_client):
    r = boss_client.post(
        "/api/expenses", json={"date": "2026-03-10", "net": 84.03, "vat": 15.97, "vat_rate": 19, "deductible": 50}
    )
    assert r.status_code in (200, 201), r.text
    e = r.json()
    assert not e.get("lines")
    assert (e["net"], e["vat"], e["vat_rate"], e["deductible"]) == (84.03, 15.97, 19, 50)
    t = totals(boss_client)["totals"]
    assert dd(t["expenses"]) == q2(D("84.03") * D("0.5")) == D("42.02")
    assert dd(t["vat_in"]) == q2(D("15.97") * D("0.5")) == D("7.99")  # golden value, as before the change


def test_empty_lines_list_is_a_classic_expense(boss_client):
    r = boss_client.post(
        "/api/expenses", json={"date": "2026-03-10", "net": 84.03, "vat": 15.97, "vat_rate": 19, "lines": []}
    )
    assert r.status_code in (200, 201), r.text
    e = r.json()
    assert not e.get("lines")
    assert (e["net"], e["vat"], e["vat_rate"]) == (84.03, 15.97, 19)  # nothing overwritten
    t = totals(boss_client)["totals"]
    assert dd(t["expenses"]) == D("84.03") and dd(t["vat_in"]) == D("15.97")


# ------------------------------------------------------------------ 4. finance totals iterate over the lines
def test_finance_totals_for_the_hotel_bill(boss_client):
    book(boss_client, HOTEL, category="Travel")
    f = totals(boss_client)
    assert dd(f["totals"]["expenses"]) == D("180.74")
    assert dd(f["totals"]["vat_in"]) == D("15.26")
    assert dd(f["categories"]["Travel"]) == D("180.74")


def test_business_meal_line_claims_70_percent_cost_and_full_input_vat(boss_client):
    book(boss_client, [L(100.00, 19, "Lunch", deductible=70, vat_deductible=100)], category="Business meals")
    t = totals(boss_client)["totals"]
    assert dd(t["expenses"]) == D("58.82")  # q2(84.03 * 0.7)
    assert dd(t["vat_in"]) == D("15.97")


def test_line_with_deductible_but_no_vat_deductible_reclaims_the_same_share_of_the_vat(boss_client):
    book(boss_client, [L(100.00, 19, "Phone", deductible=70)])
    t = totals(boss_client)["totals"]
    assert dd(t["expenses"]) == D("58.82")
    assert dd(t["vat_in"]) == D("11.18")  # q2(15.97 * 0.7)


def test_klein_mode_uses_the_gross_share_and_no_input_vat(boss_client):
    assert boss_client.put("/api/settings", json={"kleinunternehmer": True}).status_code == 200
    book(boss_client, [L(100.00, 19, "Lunch", deductible=70, vat_deductible=100)], category="Business meals")
    book(boss_client, HOTEL, category="Travel")
    f = totals(boss_client)
    assert dd(f["totals"]["expenses"]) == D("70.00") + D("196.00")
    assert dd(f["totals"]["vat_in"]) == 0
    assert dd(f["categories"]["Business meals"]) == D("70.00") and dd(f["categories"]["Travel"]) == D("196.00")


def test_category_breakdown_uses_line_category_then_expense_category_then_other(boss_client):
    book(
        boss_client,
        [L(11.90, 19, "a", category="Travel"), L(23.80, 19, "b"), L(5.95, 19, "c", category="Software")],
        category="Office",
    )
    book(boss_client, [L(11.90, 19, "d")])  # no category anywhere -> "Other"
    cats = totals(boss_client)["categories"]
    assert {k: dd(v) for k, v in cats.items()} == {
        "Travel": D("10.00"),
        "Office": D("20.00"),
        "Software": D("5.00"),
        "Other": D("10.00"),
    }


def test_mixed_old_and_itemised_expenses_add_up_against_the_oracle(boss_client):
    spec = [
        {"lines": [(160.00, 7, 100, None, None), (30.00, 19, 100, None, None), (6.00, 0, 100, None, None)]},
        {"lines": [(100.00, 19, 70, 100, None)], "category": "Business meals"},
        {"lines": [(0.50, 7, 100, None, None), (0.50, 7, 50, None, "Gifts")]},
        {"lines": [(200.00, 19, 100, 0, None), (57.80, 7, 33.3, None, None)], "category": "Travel"},
    ]
    for e in spec:
        lines = [L(g, r, f"l{i}", deductible=d, **({"vat_deductible": v} if v is not None else {}),
                   **({"category": c} if c else {})) for i, (g, r, d, v, c) in enumerate(e["lines"])]  # fmt: skip
        book(boss_client, lines, **({"category": e["category"]} if e.get("category") else {}))
    # plus one classic expense: net 84.03 vat 15.97 deductible 50
    boss_client.post(
        "/api/expenses", json={"date": "2026-03-11", "net": 84.03, "vat": 15.97, "vat_rate": 19, "deductible": 50}
    )
    cost, vat_in, cats = finance_oracle(spec)
    cost += q2(D("84.03") * D("0.5"))
    vat_in += q2(D("15.97") * D("0.5"))
    cats["Other"] = cats.get("Other", D(0)) + q2(D("84.03") * D("0.5"))
    f = totals(boss_client)
    assert dd(f["totals"]["expenses"]) == cost
    assert dd(f["totals"]["vat_in"]) == vat_in
    assert {k: dd(v) for k, v in f["categories"].items()} == cats


def test_project_report_keeps_using_the_full_top_level_net(boss_client):
    proj = boss_client.post("/api/customers", json={"name": "Demo Kunde"}).json()
    p = boss_client.post("/api/projects", json={"name": "Demo", "customer_id": proj["id"]}).json()
    book(boss_client, [L(100.00, 19, "Lunch", deductible=70, vat_deductible=100)], project_id=p["id"])
    row = boss_client.get("/api/project-report").json()[p["id"]]  # a dict keyed by project id
    assert dd(row["expenses"]) == D("84.03")  # full net, not the 70 % share


# ------------------------------------------------------------------ 5. validation
def lines_422(r, index, field):
    assert r.status_code == 422, (r.status_code, r.text[:300])
    locs = [e["loc"] for e in r.json()["detail"]]
    assert any("lines" in loc and index in loc and field in loc for loc in locs), locs


def test_at_most_50_lines(boss_client):
    assert book(boss_client, [L(1, 19, f"l{i}") for i in range(50)])["lines"].__len__() == 50
    r = boss_client.post("/api/expenses", json={"date": "2026-03-10", "net": 0, "lines": [L(1, 19) for _ in range(51)]})
    assert r.status_code == 422
    assert any("lines" in e["loc"] for e in r.json()["detail"])


@pytest.mark.parametrize(
    "bad,field",
    [
        ({"gross": -0.01, "vat_rate": 19}, "gross"),
        ({"gross": 1, "vat_rate": 101}, "vat_rate"),
        ({"gross": 1, "vat_rate": -1}, "vat_rate"),
        ({"gross": 1, "vat_rate": 19, "deductible": 101}, "deductible"),
        ({"gross": 1, "vat_rate": 19, "vat_deductible": -5}, "vat_deductible"),
        ({"gross": 1, "vat_rate": 19, "description": "x" * 2001}, "description"),
        ({"gross": 1e300, "vat_rate": 19}, "gross"),
        ({"gross": "abc", "vat_rate": 19}, "gross"),
    ],
)
def test_invalid_line_values_are_rejected_and_the_message_names_the_line(boss_client, bad, field):
    r = boss_client.post("/api/expenses", json={"date": "2026-03-10", "net": 0, "lines": [L(5, 19), bad]})
    lines_422(r, 1, field)


def test_a_line_must_state_gross_and_vat_rate(boss_client):
    lines_422(
        boss_client.post("/api/expenses", json={"date": "2026-03-10", "net": 0, "lines": [{"vat_rate": 19}]}),
        0,
        "gross",
    )
    lines_422(
        boss_client.post("/api/expenses", json={"date": "2026-03-10", "net": 0, "lines": [{"gross": 5}]}), 0, "vat_rate"
    )


def test_lines_must_be_a_list_of_objects(boss_client):
    for bad in ("x", {"gross": 1}, [1, 2], [None]):
        assert boss_client.post("/api/expenses", json={"date": "2026-03-10", "net": 0, "lines": bad}).status_code == 422


def test_rejected_save_stores_nothing(boss_client):
    boss_client.post("/api/expenses", json={"date": "2026-03-10", "net": 0, "lines": [L(5, 19), L(-1, 19)]})
    assert boss_client.get("/api/expenses").json() == []


# ------------------------------------------------------------------ 6. property
rate = st.sampled_from([0, 7, 19])
cents = st.integers(min_value=0, max_value=5_000_000).map(lambda n: n / 100)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(lines=st.lists(st.tuples(cents, rate), min_size=1, max_size=12))
def test_property_lines_add_up_exactly_and_group_vat_adds_up(boss_client, lines):
    got = book(boss_client, [L(g, r, f"l{i}") for i, (g, r) in enumerate(lines)])
    out = got["lines"]
    gross = sum((dd(g) for g, _ in lines), D(0))
    assert sum((dd(x["net"]) + dd(x["vat"]) for x in out), D(0)) == gross
    assert dd(got["net"]) + dd(got["vat"]) == gross
    _, summary = derive(lines)
    for r_, (group_vat, group_net) in summary.items():
        members = [x for x in out if D(str(x["vat_rate"])) == r_]
        assert sum((dd(x["vat"]) for x in members), D(0)) == group_vat
        assert sum((dd(x["net"]) for x in members), D(0)) == group_net
    assert dd(got["vat"]) == sum((v for v, _ in summary.values()), D(0))
    assert all(dd(x["net"]) >= 0 and dd(x["vat"]) >= 0 for x in out)
