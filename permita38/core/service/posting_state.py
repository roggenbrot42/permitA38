"""Pure views of a stored invoice for the posting services: the engine snapshot and the 9.2 invoice state.

Kept apart from ``postings.py`` so ``opening_items.py`` and ``postings.py`` need not import each other.
"""

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from permita38.core.accounting.postings_credit import CreditState
from permita38.core.accounting.types import Cents, InvoiceState, IssuedSnapshot, RateGroup

__all__ = ["creditable_nets", "invoice_state_of", "stored_snapshot"]


def stored_snapshot(doc: Mapping[str, Any]) -> IssuedSnapshot | None:
    """The engine view of a stored ``issued`` snapshot; ``None`` for a draft or an invoice without one."""
    raw = doc.get("issued")
    if not isinstance(raw, dict) or not isinstance(raw.get("rates"), dict):
        return None
    groups = sorted(
        (RateGroup(Decimal(r), Cents(int(v["net"])), Cents(int(v["vat"]))) for r, v in raw["rates"].items()),
        key=lambda g: g.rate,
        reverse=True,
    )
    return IssuedSnapshot(
        document_id=str(doc["id"]),
        regime=raw["regime"],
        treatment=raw["treatment"],
        rates=tuple(groups),
        perf=date.fromisoformat(str(raw["perf"])),
    )


def invoice_state_of(snap: IssuedSnapshot, credit_notes: Iterable[Mapping[str, Any]], kept_cents: int) -> InvoiceState:
    """The 9.2 state of an invoice: its snapshot, the credited net and VAT per rate group summed from the credit
    documents' own snapshots (``issued.rates``; a write-off reversal is stored negative) and the cash kept.
    Discounts and write-offs are tracked separately (``CreditState``) because they post to their own accounts."""
    index = {g.rate: i for i, g in enumerate(snap.rates)}
    credited = [[0, 0] for _ in snap.rates]
    disc = [[0, 0] for _ in snap.rates]
    wo = [[0, 0] for _ in snap.rates]
    for note in credit_notes:
        raw = note.get("issued")
        rates = raw.get("rates") if isinstance(raw, Mapping) else None
        if not isinstance(rates, Mapping):
            continue
        kind = note.get("credit_kind")
        for key, val in rates.items():
            i = index.get(Decimal(str(key)))
            if i is None:
                continue
            net, vat = int(val["net"]), int(val["vat"])
            targets = [credited[i]]
            if kind == "discount":
                targets.append(disc[i])
            if kind in ("write_off", "write_off_reversal"):
                targets.append(wo[i])
            for t in targets:
                t[0] += net
                t[1] += vat

    def groups(rows: list[list[int]]) -> tuple[RateGroup, ...]:
        return tuple(RateGroup(g.rate, Cents(r[0]), Cents(r[1])) for g, r in zip(snap.rates, rows, strict=True))

    if any(v for row in (*disc, *wo) for v in row):
        return CreditState(snap, groups(credited), Cents(kept_cents), groups(disc), groups(wo))
    return InvoiceState(snap, groups(credited), Cents(kept_cents))


def creditable_nets(snap: IssuedSnapshot, credit_notes: Iterable[Mapping[str, Any]]) -> list[tuple[Decimal, int, int]]:
    """``(rate, net, vat)`` still creditable per rate group: the snapshot minus everything credited by the credit
    documents, write-offs included and their reversals given back (the one rule of the over-credit check)."""
    state = invoice_state_of(snap, credit_notes, 0)
    return [(g.rate, int(g.net) - int(k.net), int(g.vat) - int(k.vat)) for g, k in zip(snap.rates, state.credited)]
