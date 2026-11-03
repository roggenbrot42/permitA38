"""The whole bill must add up (docs/specs/receipt-scan.md, "The whole bill must add up").

A pure function from ``Extraction`` to a list of checks, in exact ``Decimal`` with no tolerance. A check runs only if
both of its sides were read. ``independent`` says whether a pass is real evidence: a bill needs one independent pass.
"""

from dataclasses import dataclass
from decimal import Decimal

from ..money import q2
from .extraction import Extraction, VatGroup

_ZERO = Decimal(0)


@dataclass
class Check:
    name: str
    expected: Decimal  # what the arithmetic demands, from the other figures
    found: Decimal  # the figure printed on the bill
    ok: bool
    independent: bool
    flag: str  # the flag code raised when the check fails
    refs: tuple[str, ...] = ()  # source keys of the values involved (``Extraction.sources``), for the overlay
    covers: bool = False  # a pass refers to the booked gross itself (the total, or all group grosses)


@dataclass
class Group:
    """A VAT group with the gross amount the checks use."""

    index: int
    group: VatGroup
    gross: Decimal | None
    gross_printed: bool  # False: derived from net + VAT, or from the total
    from_total: bool  # gross derived from the total and the other groups: not evidence for a check against the total
    from_items: bool = False  # gross derived from the items mapped to the group


def _check(
    name: str,
    expected: Decimal,
    found: Decimal,
    independent: bool,
    flag: str,
    refs: tuple[str, ...],
    covers: bool = False,
) -> Check:
    expected, found = q2(expected), q2(found)
    return Check(name, expected, found, expected == found, independent, flag, refs, covers)


def _tips(x: Extraction) -> Decimal:
    """Tips are paid on top of the VAT groups: the total is the groups plus the tip."""
    return sum((a.amount for a in x.adjustments if a.kind == "tip"), _ZERO)


def vat_groups(x: Extraction) -> list[Group]:
    """The gross of each group: as printed, else net + VAT, else (one group only) the total less the other groups."""
    out: list[Group] = []
    for i, g in enumerate(x.vat_groups):
        if g.gross is not None:
            out.append(Group(i, g, g.gross, True, False))
        elif g.net is not None and g.vat is not None:
            out.append(Group(i, g, g.net + g.vat, False, False))
        else:
            out.append(Group(i, g, None, False, False))
    unknown = [o for o in out if o.gross is None]
    mapping = _item_groups(x) if unknown and x.items else None
    if mapping is not None and x.total is not None and all(i.amount is not None for i in x.items):
        items_sum = sum((i.amount for i in x.items if i.amount is not None), _ZERO)
        # with a discount, charge or tip the items alone do not say what each group paid: the gross stays unknown
        if x.adjustments or items_sum != x.total:
            mapping = None  # the items are net prices (or misread): they are not the gross of the groups
    else:
        mapping = None
    if mapping is not None:
        # a till that prints only the VAT of each group: the gross of a group is the sum of its items
        if all(mapping.get(o.index) for o in unknown):
            for o in unknown:
                o.gross = sum((x.items[n].amount or _ZERO for n in mapping[o.index]), _ZERO)
                o.from_items = True
            unknown = []
    if len(unknown) == 1 and x.total is not None:
        only = unknown[0]
        only.gross = x.total - _tips(x) - sum((o.gross for o in out if o.gross is not None), _ZERO)
        only.from_total = True
    return out


def _item_groups(x: Extraction) -> dict[int, list[int]] | None:
    """Item indexes per group index, or ``None`` if some item cannot be mapped to a group."""
    by_code = {g.code: i for i, g in enumerate(x.vat_groups) if g.code is not None}
    # a till table whose code letter was lost in one row ("Ums. inkl. 7% MwSt 8,60"): one group without a code and
    # one item code that no group has belong together; the sums of the check still have to hold
    nameless = [i for i, g in enumerate(x.vat_groups) if g.code is None]
    unmapped = {it.vat_code for it in x.items if it.vat_code is not None and it.vat_code not in by_code}
    if len(nameless) == 1 and len(unmapped) == 1:
        by_code[next(iter(unmapped))] = nameless[0]
    result: dict[int, list[int]] = {}
    for n, item in enumerate(x.items):
        if item.vat_code is not None and item.vat_code in by_code:
            result.setdefault(by_code[item.vat_code], []).append(n)
        elif item.vat_code is None and len(x.vat_groups) == 1:
            result.setdefault(0, []).append(n)
        else:
            return None
    return result


def _vat_per_item(x: Extraction, mapping: dict[int, list[int]] | None, o: Group) -> Decimal | None:
    """The VAT of a group as the sum of the VAT of each net row, each rounded to cents. Only if the rows of the group
    are net prices that add up to the printed net of the group."""
    g = o.group
    if mapping is None or g.net is None or g.rate is None or not mapping.get(o.index):
        return None
    amounts = [x.items[n].amount for n in mapping[o.index]]
    if any(a is None for a in amounts) or sum((a for a in amounts if a is not None), _ZERO) != g.net:
        return None
    return sum((q2(a * g.rate / 100) for a in amounts if a is not None), _ZERO)


def _einvoice(x: Extraction) -> list[Check]:
    s = x.einvoice_sums
    out: list[Check] = []

    def get(key: str) -> Decimal | None:
        return s.get(key)

    if x.items and get("BT-106") is not None and all(i.amount is not None for i in x.items):
        total = sum((i.amount for i in x.items if i.amount is not None), _ZERO)
        out.append(_check("en16931_bt106", total, s["BT-106"], True, "items_mismatch", ("total",), True))
    if get("BT-106") is not None and get("BT-109") is not None:
        calc = s["BT-106"] - s.get("BT-107", _ZERO) + s.get("BT-108", _ZERO)
        out.append(_check("en16931_bt109", calc, s["BT-109"], True, "total_mismatch", ("total",), True))
    nets = [g.net for g in x.vat_groups]
    if get("BT-109") is not None and x.vat_groups and all(n is not None for n in nets):
        net_sum = sum((n for n in nets if n is not None), _ZERO)
        out.append(_check("en16931_bt116_109", net_sum, s["BT-109"], True, "total_mismatch", ("total",), True))
    if (
        get("BT-112") is not None
        and x.vat_groups
        and all(g.net is not None and g.vat is not None for g in x.vat_groups)
    ):
        both = sum(((g.net or _ZERO) + (g.vat or _ZERO) for g in x.vat_groups), _ZERO)
        out.append(_check("en16931_bt116_112", both, s["BT-112"], True, "total_mismatch", ("total",), True))
    if get("BT-109") is not None and get("BT-110") is not None and get("BT-112") is not None:
        out.append(
            _check("en16931_bt112", s["BT-109"] + s["BT-110"], s["BT-112"], True, "total_mismatch", ("total",), True)
        )
    if get("BT-112") is not None and get("BT-115") is not None:
        calc = s["BT-112"] - s.get("BT-113", _ZERO) + s.get("BT-114", _ZERO)
        out.append(_check("en16931_bt115", calc, s["BT-115"], True, "payment_mismatch", ("total",), True))
    for i, g in enumerate(x.vat_groups):
        if g.net is not None and g.rate is not None and g.vat is not None:
            out.append(_check("en16931_bt117", g.net * g.rate / 100, g.vat, True, "vat_mismatch", (f"vat_group:{i}",)))
    if get("BT-110") is not None and x.vat_groups and all(g.vat is not None for g in x.vat_groups):
        out.append(
            _check(
                "en16931_bt110",
                sum((g.vat for g in x.vat_groups if g.vat is not None), _ZERO),
                s["BT-110"],
                True,
                "vat_mismatch",
                (),
            )
        )
    return out


def total_from_payment(x: Extraction) -> None:
    """No total was read, but the rate groups add up and a card or cash amount equals that sum: that is the total."""
    if x.total is not None or x.competing_totals or not x.vat_groups or x.einvoice_sums:
        return
    paid = x.card_amount if x.card_amount is not None else x.tendered if x.change is None else None
    groups = vat_groups(x)
    if paid is None or not all(o.gross is not None and not o.from_total for o in groups):
        return
    if sum((o.gross for o in groups if o.gross is not None), _ZERO) + _tips(x) == paid:
        x.total = paid
        x.sources.setdefault("total", x.sources.get("card", []))


def _qr_checks(x: Extraction) -> list[Check]:
    qr = x.qr
    if qr is None:
        return []
    out: list[Check] = []
    paid = True
    if qr.kind == "tse" and qr.payments:
        gross = sum((g for _r, g in qr.groups), _ZERO)
        pay = _check(
            "qr_payment", gross, sum((a for a, _k in qr.payments), _ZERO), True, "total_mismatch", ("qr",), True
        )
        out.append(pay)
        paid = pay.ok
    if qr.kind == "tse" and qr.printed_total is not None and x.total is not None:
        # the code wins; OCR digits are the weaker source, so a differing OCR total alone raises no flag
        weak = qr.printed_from_ocr and paid
        out.append(
            _check("qr_total", x.total, qr.printed_total, True, "" if weak else "total_mismatch", ("qr", "total"), True)
        )
    if qr.kind == "epc" and qr.epc_amount is not None and x.total is not None:
        out.append(_check("qr_total", qr.epc_amount, x.total, True, "total_mismatch", ("qr", "total"), True))
    return out


def reconcile(x: Extraction) -> list[Check]:
    if x.einvoice_sums:
        return _einvoice(x)
    return [*_reconcile_text(x), *_qr_checks(x)]


def _reconcile_text(x: Extraction) -> list[Check]:
    checks: list[Check] = []
    item_keys = tuple(f"item:{n}" for n in range(len(x.items)))

    for n, item in enumerate(x.items):
        if item.quantity is not None and item.unit_price is not None and item.amount is not None:
            checks.append(
                _check(
                    "item_line", item.quantity * item.unit_price, item.amount, False, "items_mismatch", (f"item:{n}",)
                )
            )

    groups = vat_groups(x)
    mapping = _item_groups(x) if x.items else None
    if mapping is not None:
        for gi, members in sorted(mapping.items()):
            gross = groups[gi].gross
            if gross is not None and groups[gi].gross_printed:
                got = sum((x.items[n].amount or _ZERO for n in members), _ZERO)
                if all(x.items[n].amount is not None for n in members):
                    refs = (f"vat_group:{gi}", *(f"item:{n}" for n in members))
                    checks.append(_check("items_group", got, gross, True, "items_mismatch", refs))

    if x.items and all(i.amount is not None for i in x.items):
        items_sum = sum((i.amount for i in x.items if i.amount is not None), _ZERO)
        if x.subtotal is not None:
            checks.append(
                _check(
                    "items_total",
                    items_sum,
                    x.subtotal,
                    len(x.items) > 1,
                    "items_mismatch",
                    (*item_keys, "subtotal"),
                    True,
                )  # fmt: skip
            )
        elif x.total is not None:
            adjust = sum((a.amount for a in x.adjustments), _ZERO)
            # a single item may just repeat the total: it proves nothing
            many = len(x.items) > 1
            check = _check(
                "items_total", items_sum + adjust, x.total, many, "items_mismatch", (*item_keys, "total"), True
            )
            group_nets = [
                g.net if g.net is not None else (g.gross - g.vat if g.gross and g.vat else None) for g in x.vat_groups
            ]
            if not check.ok and x.net_total is not None:
                net_sum: Decimal | None = x.net_total
            elif group_nets and all(n is not None for n in group_nets):
                net_sum = sum((n for n in group_nets if n is not None), _ZERO)
            else:
                net_sum = None
            if not check.ok and net_sum is not None:
                # an invoice often lists net prices: the items then add up to the net of the groups
                net_check = _check(
                    "items_total", items_sum + adjust, net_sum, many,
                    "items_mismatch", (*item_keys, "total"),
                )  # fmt: skip
                check = net_check if net_check.ok else check
            checks.append(check)

    if x.subtotal is not None and x.total is not None:
        adjust = sum((a.amount for a in x.adjustments), _ZERO)
        gross_check = _check("subtotal", x.subtotal + adjust, x.total, False, "total_mismatch", ("subtotal", "total"))
        group_vats = [g.vat for g in x.vat_groups if g.vat is not None]
        if not gross_check.ok and x.vat_groups and len(group_vats) == len(x.vat_groups):
            # on an invoice the subtotal is often the net: net + VAT = total
            net_check = _check(
                "subtotal",
                x.subtotal + sum(group_vats, _ZERO) + adjust,
                x.total,
                False,
                "total_mismatch",
                ("subtotal", "total"),
            )
            gross_check = net_check if net_check.ok else gross_check
        checks.append(gross_check)

    for o in groups:
        g = o.group
        ref = (f"vat_group:{o.index}",)
        if g.net is not None and g.vat is not None and g.gross is not None:
            checks.append(_check("group_sum", g.net + g.vat, g.gross, True, "vat_mismatch", ref, len(groups) == 1))
        if g.vat is not None and g.rate is not None and o.gross is not None:
            # 0 % proves nothing (0 = 0); a gross derived from the total has a window of about 7 cents
            # (a single group keeps its spec oracle: its gross is the total, and the printed VAT is the one check)
            proof = g.rate != 0 and not (o.from_total and len(groups) > 1)
            check = _check(
                "group_vat", o.gross * g.rate / (100 + g.rate), g.vat, proof, "vat_mismatch", ref,
                proof and len(groups) == 1,
            )  # fmt: skip
            if not check.ok:
                per_item = _vat_per_item(x, mapping, o)
                if per_item is not None and q2(per_item) == q2(g.vat):
                    # a shop that rounds the VAT of each net row: exact, from the printed rows, not a tolerance
                    check = _check("group_vat", per_item, g.vat, proof, "vat_mismatch", ref, proof and len(groups) == 1)
            checks.append(check)

    if x.total is not None and groups and all(o.gross is not None and not o.from_total for o in groups):
        got = sum((o.gross for o in groups if o.gross is not None), _ZERO) + _tips(x)
        refs = (*(f"vat_group:{o.index}" for o in groups), "total")
        checks.append(_check("groups_total", got, x.total, len(groups) > 1, "total_mismatch", refs, True))

    nets: list[Decimal] = []
    vats: list[Decimal] = []
    for o in groups:
        g = o.group
        if g.net is not None:
            nets.append(g.net)
        elif g.gross is not None and g.vat is not None:
            nets.append(g.gross - g.vat)
        if g.vat is not None:
            vats.append(g.vat)
    if x.net_total is not None and groups and len(nets) == len(groups):
        refs = (*(f"vat_group:{o.index}" for o in groups), "net_total")
        checks.append(_check("net_total", sum(nets, _ZERO), x.net_total, True, "vat_mismatch", refs))
    if x.vat_total is not None and groups and len(vats) == len(groups):
        refs = (*(f"vat_group:{o.index}" for o in groups), "vat_total")
        checks.append(_check("vat_total", sum(vats, _ZERO), x.vat_total, True, "vat_mismatch", refs))

    if x.total is not None and x.prepaid is not None and x.amount_due is not None:
        checks.append(_check("due", x.total - x.prepaid, x.amount_due, False, "payment_mismatch", ("total",)))
    if x.total is not None and x.tendered is not None and x.change is not None:
        checks.append(
            _check(
                "tendered",
                x.tendered - x.change,
                x.total,
                True,
                "payment_mismatch",
                ("tendered", "change", "total"),
                True,
            )
        )
    if x.total is not None and x.card_amount is not None:
        checks.append(_check("card", x.card_amount, x.total, False, "payment_mismatch", ("total",)))
    return checks


__all__ = ["Check", "Group", "reconcile", "vat_groups"]
