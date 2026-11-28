"""Write-offs, cash discounts and write-off reversals (docs/specs/accounting.md 9.2 and 9.3). Pure, integer cents.

The credit documents of these kinds are ordinary credits for the target rule (they raise ``K_r`` and ``KV_r``); only the
revenue side differs: the net goes to ``cash_discount_granted`` (discount) or ``bad_debts`` (write-off) instead of
``revenue_r``. ``CreditState`` therefore remembers, per rate group, how much of ``credited`` is a discount and how much a
write-off. The amount helpers say what such a credit document has to contain.
"""

from dataclasses import dataclass
from decimal import Decimal

from permita38.core.accounting.types import Cents, InvoiceState, RateGroup

__all__ = [
    "CreditState",
    "credit_split",
]


@dataclass(frozen=True)
class CreditState(InvoiceState):
    """``InvoiceState`` that also tracks the discount and write-off share of ``credited`` (cumulative net and VAT)."""

    discounted: tuple[RateGroup, ...] = ()
    written_off: tuple[RateGroup, ...] = ()


def _zeros(state: InvoiceState) -> tuple[RateGroup, ...]:
    return tuple(RateGroup(g.rate, Cents(0), Cents(0)) for g in state.snapshot.rates)


def credit_split(state: InvoiceState) -> tuple[tuple[RateGroup, ...], tuple[RateGroup, ...]]:
    """``(discounted, written_off)`` per rate group of ``snapshot.rates``; zeros for a state without such credits."""
    if isinstance(state, CreditState):
        return state.discounted or _zeros(state), state.written_off or _zeros(state)
    return _zeros(state), _zeros(state)


def _current(state: InvoiceState) -> tuple[list[Cents], list[Cents]]:
    cur = [Cents(g.gross - k.gross) for g, k in zip(state.snapshot.rates, state.credited, strict=True)]
    cur_vat = [Cents(g.vat - k.vat) for g, k in zip(state.snapshot.rates, state.credited, strict=True)]
    return cur, cur_vat


def _groups(rates: tuple[Decimal, ...], gross: list[Cents], vat: list[Cents], sign: int = 1) -> tuple[RateGroup, ...]:
    return tuple(
        RateGroup(rate, Cents(sign * (g - v)), Cents(sign * v)) for rate, g, v in zip(rates, gross, vat, strict=True)
    )
