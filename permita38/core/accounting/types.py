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
    "CreditEvent",
    "CreditKind",
    "Entry",
    "Event",
    "GroupTargets",
    "IncomeBasis",
    "InvoiceState",
    "IssueEvent",
    "IssuedSnapshot",
    "Line",
    "PaymentEvent",
    "Part",
    "RateGroup",
    "Regime",
    "Role",
    "TargetLine",
    "Targets",
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
CreditKind = Literal["storno", "partial", "discount", "write_off", "write_off_reversal"]
Part = Literal["", "cash", "declaration", "stat"]


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


@dataclass(frozen=True)
class InvoiceState:
    """State of an issued invoice (spec 9.2): the snapshot, the credited amounts and the cash kept ``P``.

    ``credited`` holds, in the order of ``snapshot.rates``, the cumulative net and VAT of the issued credit notes per
    rate group; ``kept`` is the sum of payments minus refunds (never negative).
    """

    snapshot: IssuedSnapshot
    credited: tuple[RateGroup, ...]
    kept: Cents

    @classmethod
    def issued(cls, snapshot: IssuedSnapshot) -> "InvoiceState":
        zero = tuple(RateGroup(g.rate, Cents(0), Cents(0)) for g in snapshot.rates)
        return cls(snapshot, zero, Cents(0))


@dataclass(frozen=True)
class IssueEvent:
    date: date


@dataclass(frozen=True)
class PaymentEvent:
    """A payment (``amount`` > 0) or a refund / reversal (``amount`` < 0)."""

    payment_id: str
    date: date
    amount: Cents
    cash_role: Role = Role("bank")


@dataclass(frozen=True)
class CreditEvent:
    """A credit note; ``rates`` carries the positive credited net and VAT per rate group."""

    credit_id: str
    date: date
    kind: CreditKind
    rates: tuple[RateGroup, ...]


Event = IssueEvent | PaymentEvent | CreditEvent


@dataclass(frozen=True)
class TargetLine:
    """The target of one journal slot ``(role, part)``: the amount the lines of the invoice should add up to.

    ``base_cents`` is the VAT base target in the sign of the VAT effect (only for lines with ``tax_kind``).
    The flags say which dates the delta line carries: ``income`` = the event date as ``income_date``;
    ``perf_at_issue`` = the performance date as ``vat_date`` for the issue event (else the event date).
    """

    role: Role
    part: Part
    amount_cents: Cents
    tax_key: TaxKey | None = None
    tax_kind: TaxKind | None = None
    tax_rate: str | None = None
    base_cents: Cents = Cents(0)
    open_item: bool = False
    income: bool = False
    perf_at_issue: bool = False


@dataclass(frozen=True)
class GroupTargets:
    """Cash allocation and due VAT of one rate group: ``T_r``, ``TV_r``, ``TN_r``, ``D_r`` and ``B_r``."""

    rate: Decimal
    cash: Cents
    cash_vat: Cents
    cash_net: Cents
    due_vat: Cents
    base: Cents


@dataclass(frozen=True)
class Targets:
    lines: tuple[TargetLine, ...]
    groups: tuple[GroupTargets, ...]
    kept: Cents  # P
    settled: Cents  # T = min(P, G)
    overpayment: Cents  # P - T
