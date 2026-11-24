"""calc.doc_totals: live totals for drafts, the frozen snapshot for issued documents (accounting.md 9.1)."""

from permita38.core import calc


def test_live_totals_per_rate_group():
    doc = {"lines": [{"qty": 1, "price": "100.00", "vat": 19}, {"qty": 2, "price": "25.00", "vat": 7}]}
    t = calc.doc_totals(doc, {"vat_rate": 19})
    assert (t["net"], t["vat_total"], t["gross"]) == (150.0, 22.5, 172.5)
    assert t["vat"] == {19.0: 19.0, 7.0: 3.5} and t["by_rate"] == {19.0: 100.0, 7.0: 50.0}
    assert doc["lines"][1]["amount"] == 50.0


def test_live_totals_follow_small_business_and_eu_treatment():
    line = {"qty": 1, "price": "100.00", "vat": 19}
    assert calc.doc_totals({"lines": [dict(line)]}, {"kleinunternehmer": True})["gross"] == 100.0
    for treatment in ("small_business", "eu_b2b_service"):
        assert calc.doc_totals({"lines": [dict(line)], "tax_treatment": treatment}, {})["gross"] == 100.0


def test_issued_document_returns_the_snapshot_figures():
    issued = {
        "rates": {"19": {"net": 10000, "vat": 1901, "gross": 11901}, "7": {"net": 5000, "vat": 350, "gross": 5350}}
    }
    doc = {"lines": [{"qty": 1, "price": "1.00", "vat": 19}], "issued": issued}
    t = calc.doc_totals(doc, {"kleinunternehmer": True, "vat_rate": 0})
    assert (t["net"], t["vat_total"], t["gross"]) == (150.0, 22.51, 172.51)
    assert t["vat"] == {19.0: 19.01, 7.0: 3.5} and t["by_rate"] == {19.0: 100.0, 7.0: 50.0}
    assert doc["lines"][0]["amount"] == 1.0  # line amounts are still filled
