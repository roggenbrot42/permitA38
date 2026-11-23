"""Later events on an invoice (docs/specs/accounting.md 9.2): one target rule for payments, refunds and credit notes.

Pure functions, integer cents, no floats. ``targets(state, bases)`` says what the journal should hold for the invoice;
``event_entry(before, after, bases, event)`` posts ``targets(after) - targets(before)``. The issue entry is the same
rule evaluated from the empty state (``before = None``). Discounts, write-offs and write-off reversals (F7) are credits
whose net goes to ``cash_discount_granted`` / ``bad_debts``; see ``postings_credit``.
"""

import dataclasses
from collections.abc import Sequence
from decimal import Decimal

from permita38.core.accounting.postings_credit import CreditState, credit_split
from permita38.core.accounting.postings_issue import TAX_EU_SVC, TAX_KU, TAX_NONE, rate_str, role_for, tax_key_for
from permita38.core.accounting.types import (
    Bases,
    Cents,
    Entry,
    Event,
    GroupTargets,
    InvoiceState,
    IssueEvent,
    Line,
    PaymentEvent,
    RateGroup,
    Role,
    TargetLine,
    Targets,
)

__all__ = ["allocate", "apply_event", "div_half_up", "event_entry", "event_key", "targets"]

_Slot = tuple[Role, str]


def div_half_up(num: int, den: int) -> int:
    """``num / den`` rounded half away from zero (the cent rounding of ``money.q2``), exact integer arithmetic."""
    if den == 0:
        raise ZeroDivisionError("div_half_up by zero")
    if den < 0:
        num, den = -num, -den
    if num >= 0:
        return (2 * num + den) // (2 * den)
    return -((-2 * num + den) // (2 * den))


def allocate(total: Cents, weights: Sequence[tuple[Decimal, Cents]]) -> tuple[Cents, ...]:
    """Split ``total`` over ``weights`` (rate, weight) by largest remainder in cents; ties go to the larger rate."""
    if total == 0:
        return tuple(Cents(0) for _ in weights)
    whole = sum(w for _, w in weights)
    if whole <= 0:
        raise ValueError("cannot allocate over weights that do not sum to a positive amount")
    floors = [(total * w) // whole for _, w in weights]
    remainders = [total * w - f * whole for (_, w), f in zip(weights, floors, strict=True)]
    leftover = total - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: (-remainders[i], -weights[i][0], i))
    for i in order[:leftover]:
        floors[i] += 1
    return tuple(Cents(f) for f in floors)


def event_key(event: Event, document_id: str) -> str:
    if isinstance(event, IssueEvent):
        return f"issue:{document_id}"
    if isinstance(event, PaymentEvent):
        return f"payment:{event.payment_id}"
    return f"credit:{event.credit_id}"


def _plus(grp: RateGroup, extra: RateGroup) -> RateGroup:
    return RateGroup(grp.rate, Cents(grp.net + extra.net), Cents(grp.vat + extra.vat))


def apply_event(state: InvoiceState, event: Event) -> InvoiceState:
    """The state after ``event``; an issue leaves it unchanged. Credits above the current gross and ``P < 0`` raise.

    A ``write_off_reversal`` carries negative amounts and may not exceed what was written off per rate group.
    """
    if isinstance(event, IssueEvent):
        return state
    if isinstance(event, PaymentEvent):
        kept = state.kept + event.amount
        if kept < 0:
            raise ValueError("cash kept would become negative")
        return dataclasses.replace(state, kept=Cents(kept))
    by_rate = {g.rate: g for g in event.rates}
    if len(by_rate) != len(event.rates):
        raise ValueError("credit note names a rate group twice")
    unknown = set(by_rate) - {g.rate for g in state.snapshot.rates}
    if unknown:
        raise ValueError("credit note names a rate group the invoice does not have")
    reversal = event.kind == "write_off_reversal"
    disc, wo = credit_split(state)
    credited: list[RateGroup] = []
    new_disc: list[RateGroup] = []
    new_wo: list[RateGroup] = []
    for grp, done, d, w in zip(state.snapshot.rates, state.credited, disc, wo, strict=True):
        extra = by_rate.get(grp.rate, RateGroup(grp.rate, Cents(0), Cents(0)))
        if reversal:
            if extra.net > 0 or extra.vat > 0 or -extra.net > w.net or -extra.vat > w.vat:
                raise ValueError("write-off reversal exceeds the written-off amount of a rate group")
        elif extra.net < 0 or extra.vat < 0:
            raise ValueError("credit note exceeds the current gross of a rate group")
        total = _plus(done, extra)
        if total.net > grp.net or total.vat > grp.vat:
            raise ValueError("credit note exceeds the current gross of a rate group")
        credited.append(total)
        new_disc.append(_plus(d, extra) if event.kind == "discount" else d)
        new_wo.append(_plus(w, extra) if event.kind in ("write_off", "write_off_reversal") else w)
    if event.kind in ("storno", "partial") and not isinstance(state, CreditState):
        return dataclasses.replace(state, credited=tuple(credited))
    return CreditState(state.snapshot, tuple(credited), state.kept, tuple(new_disc), tuple(new_wo))


def _group_targets(state: InvoiceState, bases: Bases) -> tuple[list[GroupTargets], list[RateGroup], Cents, Cents]:
    """Cash allocation, due VAT and base per rate group; also the current (C_r, CV_r) and ``T``."""
    snap = state.snapshot
    rates = [g.rate for g in snap.rates]
    gross = [g.gross for g in snap.rates]
    cur = [Cents(g.gross - k.gross) for g, k in zip(snap.rates, state.credited, strict=True)]
    cur_vat = [Cents(g.vat - k.vat) for g, k in zip(snap.rates, state.credited, strict=True)]
    credited = [k.gross for k in state.credited]
    credited_vat = [k.vat for k in state.credited]
    if state.kept < 0:
        raise ValueError("cash kept must not be negative")
    total_gross = sum(gross)
    t_total = min(state.kept, total_gross) if total_gross > 0 else 0
    c_total = sum(cur)
    over = t_total > 0 and t_total > c_total
    if over:
        extra = allocate(Cents(t_total - c_total), list(zip(rates, credited, strict=True)))
        cash = [Cents(c + e) for c, e in zip(cur, extra, strict=True)]
        cash_vat = [
            Cents(cv + (div_half_up(kv * e, k) if k != 0 else 0))
            for cv, kv, e, k in zip(cur_vat, credited_vat, extra, credited, strict=True)
        ]
    else:
        cash = list(allocate(Cents(t_total), list(zip(rates, cur, strict=True))))
        cash_vat = [Cents(div_half_up(cv * t, c) if c != 0 else 0) for cv, t, c in zip(cur_vat, cash, cur, strict=True)]
    out: list[GroupTargets] = []
    current: list[RateGroup] = []
    for i, rate in enumerate(rates):
        tn = Cents(cash[i] - cash_vat[i])
        cn = Cents(cur[i] - cur_vat[i])
        if rate == 0:
            due, base = Cents(0), Cents(0)
        elif bases.vat == "ist":
            due, base = cash_vat[i], tn
        elif over:
            due = Cents(max(cur_vat[i], cash_vat[i]))
            base = cn if cur_vat[i] >= cash_vat[i] else tn
        else:
            due, base = cur_vat[i], cn
        out.append(GroupTargets(rate, cash[i], cash_vat[i], tn, due, base))
        current.append(RateGroup(rate, cn, cur_vat[i]))
    return out, current, Cents(t_total), Cents(c_total)


def _merge(acc: dict[_Slot, TargetLine], line: TargetLine) -> None:
    key = (line.role, line.part)
    old = acc.get(key)
    if old is None:
        acc[key] = line
        return
    acc[key] = TargetLine(
        old.role,
        old.part,
        Cents(old.amount_cents + line.amount_cents),
        old.tax_key,
        old.tax_kind,
        old.tax_rate,
        Cents(old.base_cents + line.base_cents),
        old.open_item,
        old.income,
        old.perf_at_issue,
    )


def targets(state: InvoiceState, bases: Bases) -> Targets:
    """The targets of spec 9.2 for ``state``: what the journal should hold for the invoice after the last event."""
    snap = state.snapshot
    groups, current, t_total, c_total = _group_targets(state, bases)
    accrual = bases.income == "accrual"
    standard = snap.treatment == "standard"
    overpayment = Cents(state.kept - t_total)
    disc, wo = credit_split(state)
    discount_net = Cents(sum(d.net for d in disc))
    write_off_net = Cents(sum(w.net for w in wo))
    acc: dict[_Slot, TargetLine] = {}
    _merge(acc, TargetLine(Role("bank"), "", state.kept))
    if accrual:
        _merge(acc, TargetLine(Role("receivables"), "", Cents(c_total - t_total), open_item=True))
    _merge(acc, TargetLine(Role("customer_credit"), "", Cents(-overpayment)))
    if accrual:  # the net of a discount / write-off is a credit on its own account (every treatment), not on revenue
        if discount_net != 0:
            _merge(acc, TargetLine(Role("cash_discount_granted"), "", discount_net, income=True))
        if write_off_net != 0:
            _merge(acc, TargetLine(Role("bad_debts"), "", write_off_net, income=True))
    for grp, cur, d, w in zip(groups, current, disc, wo, strict=True):
        net_cur = cur.net
        if standard:
            rate = grp.rate
            rev_key = TAX_NONE if rate == 0 else None
            revenue = Cents(-(net_cur + d.net + w.net)) if accrual else Cents(-grp.cash_net)
            _merge(acc, TargetLine(role_for("revenue", rate), "", revenue, tax_key=rev_key, income=True))
            if rate == 0:
                continue
            soll = bases.vat == "soll"
            if not accrual and soll:
                _merge(acc, TargetLine(role_for("vat_soll_clearing", rate), "cash", Cents(-grp.cash_vat), income=True))
                _merge(acc, TargetLine(role_for("vat_soll_clearing", rate), "declaration", grp.due_vat))
            if accrual:
                balancing = Cents(-(cur.vat - grp.due_vat))
                _merge(acc, TargetLine(role_for("vat_not_due", rate), "", balancing))
            _merge(
                acc,
                TargetLine(
                    role_for("vat_out", rate),
                    "",
                    Cents(-grp.due_vat),
                    tax_key=tax_key_for(rate),
                    tax_kind="tax",
                    tax_rate=rate_str(rate),
                    base_cents=grp.base,
                    income=not accrual and bases.vat == "ist",
                    perf_at_issue=True,
                ),
            )
        elif snap.treatment == "eu_b2b_service":
            role = Role("revenue_eu_svc")
            if (
                accrual
            ):  # a write-off / discount keeps the amount (bad_debts carries it) but lowers the base: a stat. line
                line = TargetLine(
                    role,
                    "",
                    Cents(-(net_cur + d.net + w.net)),
                    TAX_EU_SVC,
                    "base_only",
                    base_cents=net_cur,
                    income=True,
                    perf_at_issue=True,
                )
                _merge(acc, line)
            else:  # EÜR: no cash moves with a write-off / discount, so the statistic does not change either
                _merge(acc, TargetLine(role, "cash", Cents(-grp.cash_net), income=True))
                base = Cents(net_cur + d.net + w.net)
                stat = TargetLine(role, "stat", Cents(0), TAX_EU_SVC, "base_only", base_cents=base, perf_at_issue=True)
                _merge(acc, stat)
        else:
            role = Role("revenue_ku")
            if accrual:
                amount = Cents(-(net_cur + d.net + w.net))
                _merge(acc, TargetLine(role, "", amount, TAX_KU, "base_only", base_cents=net_cur, income=True))
            else:
                _merge(
                    acc,
                    TargetLine(
                        role, "", Cents(-grp.cash_net), TAX_KU, "base_only", base_cents=grp.cash_net, income=True
                    ),
                )
    return Targets(tuple(acc.values()), tuple(groups), state.kept, t_total, overpayment)


def event_entry(before: InvoiceState | None, after: InvoiceState, bases: Bases, event: Event) -> Entry | None:
    """The entry that posts ``targets(after) - targets(before)``; ``before = None`` is the empty state (issue)."""
    new = {(t.role, t.part): t for t in targets(after, bases).lines}
    old = {(t.role, t.part): t for t in targets(before, bases).lines} if before is not None else {}
    snap = after.snapshot
    is_issue = isinstance(event, IssueEvent)
    lines: list[Line] = []
    for key in [*new, *(k for k in old if k not in new)]:
        tgt = new.get(key) or old[key]
        d_amount = (new[key].amount_cents if key in new else 0) - (old[key].amount_cents if key in old else 0)
        d_base = (new[key].base_cents if key in new else 0) - (old[key].base_cents if key in old else 0)
        has_tax = tgt.tax_kind is not None
        if d_amount == 0 and (d_base == 0 or not has_tax):
            continue
        role = tgt.role
        if role == "bank" and isinstance(event, PaymentEvent):
            role = event.cash_role
        lines.append(
            Line(
                role,
                Cents(d_amount),
                tax_key=tgt.tax_key,
                tax_kind=tgt.tax_kind,
                tax_rate=tgt.tax_rate,
                tax_base_cents=Cents(d_base) if has_tax else None,
                vat_date=(snap.perf if is_issue and tgt.perf_at_issue else event.date) if has_tax else None,
                income_date=event.date if tgt.income and not (d_amount == 0 and tgt.tax_kind == "base_only") else None,
                open_item_id=snap.document_id if tgt.open_item else None,
            )
        )
    if not lines:
        return None
    return Entry(tuple(lines), event.date, event.date, event_key(event, snap.document_id))
