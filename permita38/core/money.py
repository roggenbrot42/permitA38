"""Exact money arithmetic.

Rounding rule (used by the API, the PDFs and mirrored by the web editor in ``web/app2.js``):

* every document line amount is ``qty x price`` rounded **half-up to whole cents** (ties away from zero);
* the net total is the exact sum of the line amounts;
* VAT is computed **per VAT rate** on the *sum of the net amounts of that rate* and rounded half-up to cents
  (this is what a §14 UStG document shows: one VAT amount per tax rate, not one per line);
* the gross total is net + the sum of the per-rate VAT amounts.

All of it is done with :class:`decimal.Decimal` on the *decimal representation* of the input numbers
(``Decimal(repr(0.1))`` is ``0.1``), never on binary floats, so ``19 % of 621.50`` is ``118.085`` -> ``118.09``.
"""

import logging
import math
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)

CENT = Decimal("0.01")
ZERO = Decimal(0)
_MAX_ABS = Decimal("1e15")  # sanity bound; real values are validated far below this at the API


def dec(value: Any, default: Decimal | None = ZERO) -> Decimal:
    """Convert a JSON/database value to a finite Decimal.

    ``None``/``""`` -> ``default``. Floats use their shortest decimal representation. Anything that is not a finite
    number (``"abc"``, NaN, infinity, absurdly large values) returns ``default`` and logs a warning instead of
    raising, so one damaged record cannot break an aggregate view. Pass ``default=None`` to get a ``ValueError``.
    """
    if value is None or value == "":
        return default if default is not None else _fail(value)
    if isinstance(value, bool):
        return Decimal(int(value))
    try:
        if isinstance(value, float):
            if not math.isfinite(value):
                return _fail(value, default)
            out = Decimal(repr(value))
        else:
            out = Decimal(str(value).strip())
        if not out.is_finite() or abs(out) > _MAX_ABS:
            return _fail(value, default)
        return out
    except (InvalidOperation, ValueError, TypeError):
        return _fail(value, default)


def _fail(value: Any, default: Decimal | None = None) -> Decimal:
    if default is None:
        raise ValueError(f"not a valid number: {value!r}")
    log.warning("ignoring invalid number %r", value)
    return default


def q2(value: Decimal) -> Decimal:
    """Round half-up (ties away from zero) to whole cents."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def to_float(value: Decimal) -> float:
    """Cents-exact float for JSON output (always 2 decimals worth of precision)."""
    return float(q2(value))


def line_amount(qty: Any, price: Any) -> Decimal:
    return q2(dec(qty) * dec(price))


def vat_amount(net_of_rate_group: Decimal, rate: Any) -> Decimal:
    return q2(net_of_rate_group * dec(rate) / 100)


def hours_from_minutes(minutes: Any) -> Decimal:
    """Minutes -> hours rounded half-up to 2 decimals (used for invoice quantities)."""
    return q2(dec(minutes) / 60)


def split_gross(lines: Sequence[tuple[Decimal, Decimal]]) -> list[tuple[Decimal, Decimal]]:
    """(gross, rate %) per bill line -> (net, vat) per line, as a printed bill does it.

    VAT is computed per rate group: the group VAT is ``q2(sum(gross) * r / (100 + r))``; each line of the group gets
    ``q2(gross * r / (100 + r))`` except the last line, which takes the rest of the group VAT so the lines add up to
    the group exactly. ``net = gross - vat``. Docs: ``docs/specs/itemised-expenses.md``, money rules 1 and 2.
    """
    groups: dict[Decimal, list[int]] = {}
    for i, (_, rate) in enumerate(lines):
        groups.setdefault(rate, []).append(i)
    out: list[tuple[Decimal, Decimal]] = [(ZERO, ZERO)] * len(lines)
    for rate, idx in groups.items():
        total = sum((lines[i][0] for i in idx), ZERO)
        group_vat = q2(total * rate / (100 + rate))
        used = ZERO
        for n, i in enumerate(idx):
            gross = lines[i][0]
            vat = group_vat - used if n == len(idx) - 1 else q2(gross * rate / (100 + rate))
            used += vat
            out[i] = (gross - vat, vat)
    return out
