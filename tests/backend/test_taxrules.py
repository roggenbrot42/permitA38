"""Tax-rules data files: structure, review clock and the staleness check that forces the quarterly review."""

from datetime import date, timedelta

import pytest

from permita38.core import tax, taxrules

MAX_AGE_DAYS = 200


def test_tax_rules_are_not_older_than_200_days():
    """CI-visible reminder: when this fails, do the tax-rule review (docs/tax-rules-review.md) and bump the dates."""
    today = date.today()
    for rf in taxrules.list_rules():
        age = (today - rf.reviewed_on).days
        assert age <= MAX_AGE_DAYS, (
            f"tax rules '{rf.id}' were last reviewed on {rf.reviewed_on} ({age} days ago, limit {MAX_AGE_DAYS}). "
            "Review them against the official sources and update `reviewed_on` / `next_review_due` "
            "(see docs/tax-rules-review.md)."
        )


def test_every_rules_file_is_well_formed():
    files = taxrules.list_rules()
    assert {f.id for f in files} >= {"vat", "income_tax"}
    for rf in files:
        assert rf.next_review_due > rf.reviewed_on
        assert (rf.next_review_due - rf.reviewed_on).days <= 125, "review interval is about a quarter"
        assert rf.valid_for_year >= 2025
        assert rf.data["sources"], "every rules file must name its sources"
        for src in rf.data["sources"]:
            assert src["url"].startswith("https://") and src["kind"] in ("primary", "secondary")


def test_vat_boxes_are_consistent():
    rules = taxrules.vat_rules()
    boxes = {b["id"]: b for b in rules["boxes"]}
    assert len(boxes) == len(rules["boxes"]), "duplicate box id"
    sources = {s["id"] for s in rules["sources"]}
    for b in boxes.values():
        assert b["verification"] in ("secondary", "unverified")
        assert b["label_de"] and b["label_en"]
        assert set(b.get("sources", [])) <= sources
        if b["verification"] == "secondary":
            assert len(b["sources"]) >= 1
        if "pair" in b:
            assert boxes[b["pair"]]["pair"] == b["id"]
        if b["kind"] == "base_tax":
            assert b["rate"] in (7, 19)
    for needed in "81 86 35 36 41 43 44 45 21 46 47 73 74 84 85 66 61 67 62 63 64 65 83 39".split():
        assert needed in boxes, f"Kennziffer {needed} missing"


@pytest.mark.parametrize(
    "days,level",
    [(-30, "ok"), (0, "ok"), (1, "info"), (90, "info"), (91, "warning"), (180, "warning"), (181, "strong")],
)
def test_banner_levels(days, level):
    assert taxrules.level_for(days) == level


def test_status_reflects_the_review_clock():
    rf = taxrules.load("vat")
    on_time = taxrules.status(rf.next_review_due - timedelta(days=1))
    assert on_time["level"] == "ok"
    late = taxrules.status(rf.next_review_due + timedelta(days=100))
    assert late["level"] == "warning"
    assert {f["id"]: f for f in late["files"]}["vat"]["days_overdue"] == 100
    very_late = taxrules.status(rf.next_review_due + timedelta(days=400))
    assert very_late["level"] == "strong"
    assert on_time["unverified"] > 0  # honest: the first version has unverified entries


def test_count_unverified():
    assert taxrules.count_unverified({"a": {"verification": "unverified"}, "b": [{"verification": "secondary"}]}) == 1


def test_income_tax_tables_are_covered_by_their_review_metadata():
    meta = tax.rules_metadata()
    assert sorted(tax.TARIFFS) == meta["years_in_code"] == sorted(tax.SOLI_FREIGRENZE)
    assert meta["valid_for_year"] == max(tax.TARIFFS)
