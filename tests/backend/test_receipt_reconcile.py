"""reconcile(): every check of the spec table, with the oracles of docs/specs/receipt-scan.md as literal numbers."""

from decimal import Decimal as D

import pytest

from permita38.core.money import q2
from permita38.core.receipts.extraction import Adjustment, Extraction, Item, VatGroup
from permita38.core.receipts.reconcile import reconcile, vat_groups


def till(**over) -> Extraction:
    """The till receipt oracle: 2 x 3,40 @7 %, 11,90 @19 %, discount -1,00 @19 %, total 17,70."""
    x = Extraction(
        items=[
            Item("Kaffee", D("2"), D("3.40"), D("6.80"), "A"),
            Item("Brot", None, None, D("11.90"), "B"),
            Item("Rabatt", None, None, D("-1.00"), "B"),
        ],
        vat_groups=[
            VatGroup(D("7"), "A", D("6.36"), D("0.44"), D("6.80")),
            VatGroup(D("19"), "B", D("9.16"), D("1.74"), D("10.90")),
        ],
        total=D("17.70"),
        tendered=D("20.00"),
        change=D("2.30"),
    )
    for k, v in over.items():
        setattr(x, k, v)
    return x


def hotel(**over) -> Extraction:
    x = Extraction(
        vat_groups=[
            VatGroup(D("7"), None, None, D("10.47"), D("160.00")),
            VatGroup(D("19"), None, None, D("4.79"), D("30.00")),
            VatGroup(D("0"), None, None, None, D("6.00")),
        ],
        total=D("196.00"),
    )
    for k, v in over.items():
        setattr(x, k, v)
    return x


def by_name(checks):
    return {(c.name, c.refs): c for c in checks}


def failed(x):
    return {(c.name, c.flag) for c in reconcile(x) if not c.ok}


def test_till_receipt_passes_every_check():
    checks = reconcile(till())
    assert checks and all(c.ok for c in checks)
    names = [c.name for c in checks]
    for n in ("item_line", "items_group", "items_total", "group_sum", "group_vat", "groups_total", "tendered"):
        assert n in names
    assert any(c.ok and c.independent for c in checks)


def test_hotel_oracle_passes_with_group_vat_and_groups_total():
    checks = reconcile(hotel())
    assert all(c.ok for c in checks)
    vat = [c for c in checks if c.name == "group_vat"]
    assert [(c.expected, c.found) for c in vat] == [(D("10.47"), D("10.47")), (D("4.79"), D("4.79"))]
    total = [c for c in checks if c.name == "groups_total"][0]
    assert (total.expected, total.found, total.independent) == (D("196.00"), D("196.00"), True)


@pytest.mark.parametrize(
    "change, expected_failures",
    [
        # the failures of the spec: printed 6,90 for 2 x 3,40
        (
            {
                "items": [
                    Item("Kaffee", D("2"), D("3.40"), D("6.90"), "A"),
                    Item("Brot", None, None, D("11.90"), "B"),
                    Item("Rabatt", None, None, D("-1.00"), "B"),
                ]
            },
            {("item_line", "items_mismatch")},
        ),
        ({"change": D("2.20")}, {("tendered", "payment_mismatch")}),
        ({"total": D("17.80")}, {("total_mismatch_any", "total_mismatch")}),
    ],
)
def test_till_failures(change, expected_failures):
    got = failed(till(**change))
    if ("total_mismatch_any", "total_mismatch") in expected_failures:
        assert ("groups_total", "total_mismatch") in got
        assert ("items_total", "items_mismatch") in got
        assert ("tendered", "payment_mismatch") in got
    else:
        assert expected_failures <= got


def test_printed_vat_043_fails_group_vat():
    x = till()
    x.vat_groups[0].vat = D("0.43")
    x.vat_groups[0].net = D("6.37")
    checks = reconcile(x)
    bad = [c for c in checks if c.name == "group_vat" and not c.ok][0]
    assert (bad.expected, bad.found, bad.flag) == (D("0.44"), D("0.43"), "vat_mismatch")


def test_group_sum_fails_by_one_cent():
    x = till()
    x.vat_groups[1].net = D("9.15")
    assert ("group_sum", "vat_mismatch") in failed(x)


def test_no_tolerance_one_cent_is_a_failure():
    x = hotel(total=D("196.01"))
    assert ("groups_total", "total_mismatch") in failed(x)


def test_deposit_checks():
    ok = hotel(prepaid=D("50.00"), amount_due=D("146.00"))
    due = [c for c in reconcile(ok) if c.name == "due"][0]
    assert (due.ok, due.expected, due.found, due.independent) == (True, D("146.00"), D("146.00"), False)
    bad = hotel(prepaid=D("50.00"), amount_due=D("151.00"))
    assert ("due", "payment_mismatch") in failed(bad)


def test_subtotal_with_adjustments():
    x = Extraction(subtotal=D("50.00"), adjustments=[Adjustment("tip", D("5.00"))], total=D("55.00"))
    c = reconcile(x)
    assert [(k.name, k.ok, k.independent) for k in c] == [("subtotal", True, False)]
    x.total = D("56.00")
    assert failed(x) == {("subtotal", "total_mismatch")}


def test_items_total_uses_subtotal_when_printed():
    x = Extraction(
        items=[Item("a", None, None, D("10.00")), Item("b", None, None, D("5.00"))],
        subtotal=D("15.00"),
        total=D("14.00"),
        adjustments=[Adjustment("discount", D("-1.00"))],
    )
    names = {c.name: c.ok for c in reconcile(x)}
    assert names == {"items_total": True, "subtotal": True}


def test_items_total_with_adjustments_and_no_subtotal():
    x = Extraction(
        items=[Item("a", None, None, D("10.00"))], adjustments=[Adjustment("tip", D("1.00"))], total=D("11.00")
    )
    assert [(c.name, c.ok) for c in reconcile(x)] == [("items_total", True)]


def test_card_check_is_not_independent():
    x = Extraction(total=D("23.80"), card_amount=D("23.80"))
    c = reconcile(x)
    assert [(k.name, k.ok, k.independent) for k in c] == [("card", True, False)]
    x.card_amount = D("23.00")
    assert failed(x) == {("card", "payment_mismatch")}


def test_single_group_groups_total_is_not_independent_and_not_verifiable_oracle():
    """Total 23,80 and "19 %" with nothing else: nothing independent passes."""
    x = Extraction(total=D("23.80"), vat_groups=[VatGroup(rate=D("19"))])
    assert not any(c.ok and c.independent for c in reconcile(x))
    x2 = Extraction(total=D("23.80"), vat_groups=[VatGroup(rate=D("19"), gross=D("23.80"))])
    c = reconcile(x2)
    assert [(k.name, k.independent) for k in c] == [("groups_total", False)]


def test_single_group_gross_taken_from_total_checks_the_printed_vat():
    x = Extraction(total=D("11.90"), vat_groups=[VatGroup(rate=D("19"), vat=D("1.90"))])
    c = reconcile(x)
    assert [(k.name, k.ok, k.independent) for k in c] == [("group_vat", True, True)]  # no vacuous groups_total


def test_checks_run_only_when_both_sides_were_read():
    assert reconcile(Extraction()) == []
    assert reconcile(Extraction(total=D("5.00"))) == []
    assert reconcile(Extraction(tendered=D("20.00"), total=D("5.00"))) == []  # no change read
    assert reconcile(Extraction(prepaid=D("1.00"), total=D("5.00"))) == []  # no amount due read


def test_items_group_skipped_when_an_item_has_no_matching_code():
    x = till()
    x.items[1].vat_code = "Z"
    assert "items_group" not in [c.name for c in reconcile(x)]
    x2 = till()
    x2.items[0].vat_code = None
    assert "items_group" not in [c.name for c in reconcile(x2)]


def test_net_based_invoice_property():
    """Net n, VAT q2(n * r / 100), gross n + VAT: group_vat always passes (0,01 to 20 000 EUR in steps of 7 cents)."""
    for rate in (D("7"), D("19")):
        cents = 1
        while cents <= 2_000_000:
            n = D(cents).scaleb(-2)
            v = q2(n * rate / 100)
            x = Extraction(vat_groups=[VatGroup(rate, None, n, v, None)], total=n + v)
            bad = [c for c in reconcile(x) if not c.ok]
            assert not bad, (n, rate, bad)
            cents += 7


def _en(**sums):
    return Extraction(
        items=[Item("Beratung", D("1"), D("100.00"), D("100.00"))],
        vat_groups=[VatGroup(D("19"), "S", D("100.00"), D("19.00"), None)],
        total=D("119.00"),
        prepaid=D("0.00"),
        einvoice_sums={
            "BT-106": D("100.00"),
            "BT-109": D("100.00"),
            "BT-110": D("19.00"),
            "BT-112": D("119.00"),
            "BT-113": D("0.00"),
            "BT-115": D("119.00"),
            **sums,
        },
    )


def test_einvoice_sums_all_pass():
    c = reconcile(_en())
    assert [k.name for k in c] == [
        "en16931_bt106",
        "en16931_bt109",
        "en16931_bt116_109",
        "en16931_bt116_112",
        "en16931_bt112",
        "en16931_bt115",
        "en16931_bt117",
        "en16931_bt110",
    ]
    assert all(k.ok and k.independent for k in c)


@pytest.mark.parametrize(
    "sums, name, flag",
    [
        ({"BT-106": D("101.00")}, "en16931_bt106", "items_mismatch"),
        ({"BT-109": D("99.00")}, "en16931_bt109", "total_mismatch"),
        ({"BT-112": D("120.00")}, "en16931_bt112", "total_mismatch"),
        ({"BT-115": D("118.00")}, "en16931_bt115", "payment_mismatch"),
        ({"BT-110": D("18.00")}, "en16931_bt110", "vat_mismatch"),
    ],
)
def test_einvoice_one_broken_sum(sums, name, flag):
    assert (name, flag) in failed(_en(**sums))


def test_einvoice_group_vat_broken():
    x = _en()
    x.vat_groups[0].vat = D("18.99")
    assert ("en16931_bt117", "vat_mismatch") in failed(x)


def test_einvoice_does_not_run_gross_checks_on_net_items():
    names = [c.name for c in reconcile(_en())]
    assert "items_total" not in names and "item_line" not in names


def test_einvoice_allowances_and_charges():
    x = _en(**{"BT-107": D("10.00"), "BT-108": D("5.00"), "BT-109": D("95.00")})
    assert [c for c in reconcile(x) if c.name == "en16931_bt109"][0].ok


def test_group_gross_is_not_taken_from_the_items_when_a_discount_applies():
    x = Extraction(
        items=[Item("a", None, None, D("10.00"), "B"), Item("b", None, None, D("5.00"), "B")],
        vat_groups=[VatGroup(D("19"), "B", None, D("1.90"), None)],
        adjustments=[Adjustment("discount", D("-1.00"))],
        total=D("14.00"),
    )
    (g,) = vat_groups(x)
    assert (g.gross, g.from_items, g.from_total) == (D("14.00"), False, True)  # not the items' 15,00
    x.adjustments = []
    x.total = D("15.00")
    (g,) = vat_groups(x)
    assert (g.gross, g.from_items) == (D("15.00"), True)
