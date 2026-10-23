"""Business meals: 70 % of the cost is deductible, the input VAT is reclaimed in full (§ 15 Abs. 1a UStG).

Every other expense keeps the old rule: the deductible share applies to the VAT as well (private use). The expected
numbers are computed here with Decimal, independently of ``calc.finance``.
"""

from decimal import ROUND_HALF_UP, Decimal

D = Decimal


def cents(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def book(c, **kw):
    body = {"date": "2026-03-10", "net": 84.03, "vat": 15.97, "vat_rate": 19, **kw}
    r = c.post("/api/expenses", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def totals(c):
    return c.get("/api/finance", params={"year": 2026}).json()["totals"]


def test_business_meal_claims_70_percent_of_the_cost_and_all_of_the_input_vat(boss_client):
    book(boss_client, category="Business meals", deductible=70, vat_deductible=100)
    t = totals(boss_client)
    assert D(str(t["expenses"])) == cents(D("84.03") * D("0.70")) == D("58.82")
    assert D(str(t["vat_in"])) == D("15.97")


def test_without_the_vat_share_the_input_vat_follows_the_deductible_share(boss_client):
    book(boss_client, category="Phone & internet", deductible=50)
    t = totals(boss_client)
    assert D(str(t["expenses"])) == cents(D("84.03") * D("0.5")) == D("42.02")
    assert D(str(t["vat_in"])) == cents(D("15.97") * D("0.5")) == D("7.99")


def test_a_vat_share_of_zero_and_mixed_bills_add_up(boss_client):
    book(boss_client, category="Business meals", deductible=70, vat_deductible=100)
    book(boss_client, category="Other", deductible=100)
    book(boss_client, category="Other", deductible=100, vat_deductible=0)  # e.g. VAT that may not be reclaimed
    t = totals(boss_client)
    assert D(str(t["expenses"])) == D("58.82") + D("84.03") + D("84.03")
    assert D(str(t["vat_in"])) == D("15.97") + D("15.97") + D("0")


def test_small_business_owner_has_no_input_vat_and_deducts_the_gross_share(boss_client):
    assert boss_client.put("/api/settings", json={"kleinunternehmer": True}).status_code == 200
    book(boss_client, category="Business meals", deductible=70, vat_deductible=100)
    t = totals(boss_client)
    assert D(str(t["expenses"])) == cents(D("100.00") * D("0.70")) == D("70.00")
    assert D(str(t["vat_in"])) == 0


def test_the_vat_share_is_validated_and_round_trips(boss_client):
    bad = boss_client.post(
        "/api/expenses", json={"date": "2026-03-10", "net": 1, "vat_deductible": 150, "deductible": 70}
    )
    assert bad.status_code == 422
    got = book(boss_client, category="Business meals", deductible=70, vat_deductible=100)
    assert got["vat_deductible"] == 100 and got["deductible"] == 70
    again = boss_client.get(f"/api/expenses/{got['id']}").json()
    assert again["vat_deductible"] == 100
