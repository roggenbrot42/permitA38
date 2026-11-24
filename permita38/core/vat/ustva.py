"""UStVA box computation from journal lines (docs/specs/ustva.md, "Source of the figures" and "Boxes").

Pure functions, exact integer cents (``Cents``), no float. A box is a sum over journal lines selected by
``tax_key``/``tax_kind`` and ``vat_date`` in ``[period_from, period_to]``; the mapping to boxes and the result formula
come from the :class:`~permita38.core.vat_rules.VatRules`.

Units: ``BoxFigure.base`` is the **form** base in whole euros (an ``int``); every other amount is in cents.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from permita38.core.accounting.types import Cents, Line
from permita38.core.vat_rules import VatRules

__all__ = ["BoxFigure", "UstvaResult", "compute_boxes", "special_prepayment"]

_IGNORED_KEYS = frozenset({"NONE", "KU"})
_REVERSE_CHARGE_KEYS = frozenset({"RC_EU_SVC", "RC_3C_SVC"})


@dataclass(frozen=True)
class BoxFigure:
    """Figures of one box; fields that do not apply to the box kind are ``None``.

    ``base`` = form base in whole euros, ``booked_base`` = booked base in cents; ``tax`` = the figure the form
    carries (cents), ``booked_tax`` = sum of the booked tax lines (cents); ``amount`` = input VAT / credit / result.
    """

    kz: str
    base: int | None = None
    booked_base: Cents | None = None
    tax: Cents | None = None
    booked_tax: Cents | None = None
    amount: Cents | None = None


@dataclass(frozen=True)
class UstvaResult:
    boxes: dict[str, BoxFigure]
    result_cents: Cents  # Kz 83 (the rules' result box)
    rounding_difference: Cents  # booked tax - form tax over the boxes the form computes (81, 86)
    required: bool  # False only for a small business without § 13b tax lines in the period

    def box(self, kz: str) -> BoxFigure | None:
        return self.boxes.get(kz)


@dataclass
class _Acc:
    base: int = 0
    tax: int = 0
    amount: int = 0
    has_base: bool = False
    has_tax: bool = False
    has_amount: bool = False


def _truncate_euros(cents: int) -> int:
    """Whole euros, toward zero (also for negative totals); the mode comes from ``rules.base_rounding``."""
    return -(-cents // 100) if cents < 0 else cents // 100


def _half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def compute_boxes(
    lines: Iterable[Line],
    period_from: date,
    period_to: date,
    rules: VatRules,
    *,
    special_prepayment_credit_cents: int = 0,
    small_business: bool = False,
) -> UstvaResult:
    """Boxes of the period ``[period_from, period_to]`` (inclusive) from journal ``lines``."""
    if rules.base_rounding != "truncate_toward_zero":
        raise ValueError(f"unknown base_rounding mode {rules.base_rounding!r}")
    if special_prepayment_credit_cents < 0:
        raise ValueError("special_prepayment_credit_cents must be >= 0")
    if special_prepayment_credit_cents and small_business:
        raise ValueError("a Sondervorauszahlung credit is not allowed for a small business")
    mapping = {(m.tax_key, m.tax_kind): m for m in rules.map}
    box_by_kz = {b.kz: b for b in rules.boxes}
    acc: dict[str, _Acc] = {}
    reverse_charge_tax = False

    def slot(kz: str) -> _Acc:
        return acc.setdefault(kz, _Acc())

    for ln in lines:
        if ln.tax_key is None or ln.tax_key in _IGNORED_KEYS or ln.tax_kind is None:
            continue
        entry = mapping.get((ln.tax_key, ln.tax_kind))
        if entry is None:
            raise ValueError(f"no VAT rules mapping for (tax_key, tax_kind) = ({ln.tax_key!r}, {ln.tax_kind!r})")
        if ln.vat_date is None or not period_from <= ln.vat_date <= period_to:
            continue
        if ln.tax_kind == "input":
            if small_business or entry.tax_kz is None:
                continue
            s = slot(entry.tax_kz)
            s.amount += ln.amount_cents
            s.has_amount = True
            continue
        base = int(ln.tax_base_cents or 0)
        if entry.base_kz is not None:
            s = slot(entry.base_kz)
            s.base += base
            s.has_base = True
        if ln.tax_kind == "tax":
            booked = -int(ln.amount_cents)
            target = entry.tax_kz if entry.tax_kz is not None else entry.base_kz
            if target is not None:
                t = slot(target)
                t.tax += booked
                t.has_tax = True
            if ln.tax_key in _REVERSE_CHARGE_KEYS:
                reverse_charge_tax = True

    if special_prepayment_credit_cents:
        c = slot("39")
        c.amount += special_prepayment_credit_cents
        c.has_amount = True

    figures: dict[str, BoxFigure] = {}
    rounding_difference = 0
    for box in rules.boxes:
        a = acc.get(box.kz)
        if a is None:
            continue
        if box.kind == "base":
            form_base = _truncate_euros(a.base) if a.has_base else None
            form_tax: Cents | None = None
            booked_tax: Cents | None = None
            if box.computed_tax_rate is not None and form_base is not None:
                form_tax = Cents(_half_up(Decimal(form_base) * box.computed_tax_rate))
                booked_tax = Cents(a.tax)
                rounding_difference += a.tax - form_tax
            figures[box.kz] = BoxFigure(
                kz=box.kz,
                base=form_base,
                booked_base=Cents(a.base) if a.has_base else None,
                tax=form_tax,
                booked_tax=booked_tax,
            )
        elif box.kind == "tax_entered":
            figures[box.kz] = BoxFigure(kz=box.kz, tax=Cents(a.tax), booked_tax=Cents(a.tax))
        else:
            figures[box.kz] = BoxFigure(kz=box.kz, amount=Cents(a.amount))

    def value(kz: str) -> int:
        f = figures.get(kz)
        if f is None:
            return 0
        if box_by_kz[kz].kind == "tax_entered":
            return int(f.tax or 0)
        return int(f.amount or 0)

    def token(tok: str) -> int:
        if tok.endswith("t"):
            f = figures.get(tok[:-1])
            return int(f.tax or 0) if f is not None else 0
        return value(tok)

    result = sum(token(t) for t in rules.result.plus) - sum(token(t) for t in rules.result.minus)
    figures[rules.result.kz] = BoxFigure(kz=rules.result.kz, amount=Cents(result))
    required = (not small_business) or reverse_charge_tax
    return UstvaResult(
        boxes=figures,
        result_cents=Cents(result),
        rounding_difference=Cents(rounding_difference),
        required=required,
    )


def special_prepayment(prev_kz83_sum_cents: int, prev_kz39_cents: int, months_in_business: int = 12) -> Cents:
    """Sondervorauszahlung: ``max(base, 0) / 11`` with ``base = (sum Kz 83 + Kz 39) * 12 / months``, half-up cents."""
    if not 1 <= months_in_business <= 12:
        raise ValueError("months_in_business must be between 1 and 12")
    base = prev_kz83_sum_cents + prev_kz39_cents
    if base <= 0:
        return Cents(0)
    num = base * 12
    den = months_in_business * 11
    return Cents((2 * num + den) // (2 * den))
