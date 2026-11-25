"""Reference model of issued invoices, payments, credit notes and period locks for the stateful machine
(docs/specs/credit-notes-payments.md 2-4, docs/specs/accounting.md 9.2, 11; test plan section 20, E20).

Written from the specification text; it imports nothing from ``permita38``. Money is integer cents, never floats.
The model only tracks what the specification lets a client derive from outside:

* ``G`` gross of the snapshot, ``K`` credited gross (credit notes incl. write-offs, minus write-off reversals),
  ``P`` cash kept (payments minus refunds, reversal rows included), ``C = G - K``, ``open = C - P``;
* the receivables role balance of an invoice is ``C - min(P, G)`` (accounting 9.2, "open item" row);
* per rate group the remaining net and VAT while they are exactly known (until a write-off, a discount or a write-off
  reversal splits a credit over the groups by the cumulative allocation, which the model does not recompute).
"""

from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction

from .accounting_model import q2

LABELS = ("sent", "accepted", "rejected")


def fmt(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}"


def vat_of(net: int, rate: int) -> int:
    return q2(Fraction(net * rate, 100))


@dataclass
class PayRow:
    id: str
    day: date
    cents: int  # signed: payment +, refund -, reversal = minus the row it reverses
    kind: str  # payment | refund | reversal
    reverses: str | None = None


@dataclass
class Inv:
    id: str
    day: date
    nets: dict[int, int]  # rate -> net cents (one line per rate)
    customer: str
    draft: bool = True
    label: str = "sent"
    notes: str | None = None
    reference: str | None = None
    due_date: str | None = None
    # --- issued state
    K: int = 0
    P: int = 0
    wo_remaining: int = 0  # written-off gross not yet reversed
    storno: bool = False
    exact: bool = True  # per-group remaining amounts known
    rem: dict[int, list[int]] = field(default_factory=dict)  # rate -> [net, vat] still creditable
    rows: list[PayRow] = field(default_factory=list)
    credits: list[tuple[str, str, int]] = field(default_factory=list)  # (doc id, credit_kind, signed gross)

    # ------------------------------------------------------------ amounts
    @property
    def groups(self) -> dict[int, tuple[int, int]]:
        return {r: (n, vat_of(n, r)) for r, n in self.nets.items()}

    @property
    def G(self) -> int:
        return sum(n + v for n, v in self.groups.values())

    @property
    def C(self) -> int:
        return self.G - self.K

    @property
    def open(self) -> int:
        return self.C - self.P

    @property
    def receivables(self) -> int:
        return self.C - min(self.P, self.G)

    def issue(self) -> None:
        self.draft = False
        self.rem = {r: [n, v] for r, (n, v) in self.groups.items()}

    # ------------------------------------------------------------ derived fields (spec 2.3)
    @property
    def status(self) -> str:
        if self.storno and self.K >= self.G:
            return "cancelled"
        if self.open <= 0 and self.P > 0:
            return "paid"
        return self.label

    @property
    def paid_date(self) -> str | None:
        if self.open <= 0 and self.P > 0:
            reversed_ids = {r.reverses for r in self.rows if r.reverses}
            days = [r.day.isoformat() for r in self.rows if r.kind == "payment" and r.id not in reversed_ids]
            return max(days) if days else None
        return None

    # ------------------------------------------------------------ events
    def pay(self, row_id: str, day: date, cents: int, kind: str = "payment") -> None:
        """A payment or refund that the service accepted. A payment that lifts the cash above the current gross while
        write-offs exist first reverses them (accounting 9.2, bounded by what this payment adds above ``C``)."""
        if kind == "payment" and self.wo_remaining > 0:
            t_before, t_after = min(self.P, self.G), min(self.P + cents, self.G)
            amount = min(t_after - max(self.C, t_before), self.wo_remaining)
            if amount > 0:
                self.K -= amount
                self.wo_remaining -= amount
                self.exact = False
                self.credits.append(("", "write_off_reversal", -amount))
        signed = cents if kind == "payment" else -cents
        self.P += signed
        self.rows.append(PayRow(row_id, day, signed, kind))

    def reverse(self, row_id: str, day: date, target: PayRow) -> None:
        self.P -= target.cents
        self.rows.append(PayRow(row_id, day, -target.cents, "reversal", target.id))

    def credit(self, doc_id: str, kind: str, gross: int) -> None:
        self.K += gross
        self.credits.append((doc_id, kind, gross))
        if kind == "storno":
            self.storno = True
        if kind == "write_off":
            self.wo_remaining += gross
        if kind in ("write_off", "discount"):
            self.exact = False

    def partial_vat(self, rate: int, net: int) -> int:
        left_net, left_vat = self.rem[rate]
        return left_vat if net == left_net else min(vat_of(net, rate), left_vat)

    def apply_partial(self, amounts: dict[int, int]) -> int:
        gross = 0
        for rate, net in amounts.items():
            vat = self.partial_vat(rate, net)
            self.rem[rate][0] -= net
            self.rem[rate][1] -= vat
            gross += net + vat
        return gross

    def storno_gross(self) -> int:
        return self.C

    def apply_storno(self) -> None:
        for rate in self.rem:
            self.rem[rate] = [0, 0]
