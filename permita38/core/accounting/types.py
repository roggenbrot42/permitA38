"""Vocabulary of the accounting engine (docs/specs/accounting.md sections 3, 5, 6, 7a). Money is integer cents."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, NewType

from permita38.core.vat_rules import TaxKey, TaxKind

__all__ = [
    "AccountNo",
    "Bases",
    "Cents",
    "Entry",
    "IncomeBasis",
    "IssuedSnapshot",
    "Line",
    "RateGroup",
    "Regime",
    "Role",
    "TaxKey",
    "TaxKind",
    "Treatment",
    "VatBasis",
]

Cents = NewType("Cents", int)
Role = NewType("Role", str)
AccountNo = NewType("AccountNo", str)

IncomeBasis = Literal["eur", "accrual"]
VatBasis = Literal["soll", "ist"]
Treatment = Literal["standard", "eu_b2b_service", "small_business"]
Regime = Literal["regular", "small_business"]


@dataclass(frozen=True)
class Bases:
    income: IncomeBasis
    vat: VatBasis


@dataclass(frozen=True)
class RateGroup:
    """Net and VAT of one VAT rate on an issued document, in cents."""

    rate: Decimal
    net: Cents
    vat: Cents

    @property
    def gross(self) -> Cents:
        return Cents(self.net + self.vat)


@dataclass(frozen=True)
class IssuedSnapshot:
    """The amounts frozen when an invoice is issued (spec 9.1); rate groups sorted by descending rate."""

    document_id: str
    regime: Regime
    treatment: Treatment
    rates: tuple[RateGroup, ...]
    perf: date

    @property
    def gross(self) -> Cents:
        return Cents(sum(g.gross for g in self.rates))

    @property
    def net(self) -> Cents:
        return Cents(sum(g.net for g in self.rates))


@dataclass(frozen=True)
class Line:
    """One journal line; ``amount_cents`` positive = debit, negative = credit."""

    role: Role
    amount_cents: Cents
    tax_key: TaxKey | None = None
    tax_kind: TaxKind | None = None
    tax_rate: str | None = None
    tax_base_cents: Cents | None = None
    vat_date: date | None = None
    income_date: date | None = None
    open_item_id: str | None = None


@dataclass(frozen=True)
class Entry:
    lines: tuple[Line, ...]
    booking_date: date
    document_date: date
    event_key: str

    @property
    def balance(self) -> Cents:
        return Cents(sum(ln.amount_cents for ln in self.lines))
