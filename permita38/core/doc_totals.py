"""Document totals and the plain figure helpers behind ``calc.finance`` (shared with ``service/finance_journal.py``).

Split out of ``calc.py`` so the journal figures need not import ``calc``, which imports them.
"""

from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from permita38.core.money import ZERO, dec, line_amount, to_float, vat_amount
from permita38.core.service import invoice_state


def snapshot_totals(issued: dict) -> dict:
    """Totals in the shape of :func:`doc_totals` from an ``issued`` snapshot (integer cents, accounting.md 9.1)."""

    def eur(cents) -> float:
        return to_float(dec(int(cents)) / 100)

    rates = issued["rates"]
    net = sum(int(v["net"]) for v in rates.values())
    vat = sum(int(v["vat"]) for v in rates.values())
    return dict(
        net=eur(net),
        vat={float(r): eur(v["vat"]) for r, v in rates.items()},
        vat_total=eur(vat),
        gross=eur(net + vat),
        by_rate={float(r): eur(v["net"]) for r, v in rates.items()},
    )


def doc_totals(doc: dict[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    """Net, VAT per rate and gross of an offer/invoice, in exact cents (rule: see :mod:`permita38.core.money`).

    Also fills ``amount`` on every line. Returned amounts are floats with at most 2 decimals; ``vat`` and
    ``by_rate`` are keyed by the VAT rate.
    """
    klein = bool(settings.get("kleinunternehmer")) or doc.get("tax_treatment") in ("small_business", "eu_b2b_service")
    if klein:
        default_vat = ZERO
    else:
        raw = doc.get("vat_rate")
        default_vat = dec(raw if raw not in (None, "") else settings.get("vat_rate", 19))
    net = ZERO
    by_rate: dict[Decimal, Decimal] = defaultdict(lambda: ZERO)
    lines = doc.get("lines")
    for ln in lines if isinstance(lines, list) else []:
        if not isinstance(ln, dict):
            continue
        amt = line_amount(ln.get("qty"), ln.get("price"))
        ln["amount"] = to_float(amt)
        net += amt
        raw_vat = ln.get("vat")
        rate = ZERO if klein else (dec(raw_vat) if raw_vat not in (None, "") else default_vat)
        by_rate[rate] += amt
    issued = doc.get("issued")
    if isinstance(issued, dict) and isinstance(issued.get("rates"), dict):
        return snapshot_totals(issued)  # an issued document keeps its frozen figures (no retroactive repricing)
    vat = {r: vat_amount(a, r) for r, a in by_rate.items()}
    vat_total = sum(vat.values(), ZERO)
    return dict(
        net=to_float(net),
        vat={float(r): to_float(v) for r, v in vat.items()},
        vat_total=to_float(vat_total),
        gross=to_float(net + vat_total),
        by_rate={float(r): to_float(a) for r, a in by_rate.items()},
    )


_MONEY_KEYS = ("revenue", "vat_out", "expenses", "vat_in")


def empty_months() -> list[dict[str, Any]]:
    return [dict(month=m, **{k: ZERO for k in _MONEY_KEYS}) for m in range(1, 13)]


def open_receivables_of_documents(store) -> Decimal:
    """What customers still owe, from the invoice documents (gross less credit notes and payments)."""
    s = store.settings()
    pay_rows = invoice_state.group_by(store.list("payments"), "document_id")
    invoices = store.list("invoices")
    notes = invoice_state.group_by(invoice_state.credit_notes_of(invoices), "corrects_id")
    total = ZERO
    for inv in invoices:
        if inv.get("status") == "draft" or inv.get("kind") == "credit_note":
            continue
        t = doc_totals(dict(inv), s)
        st = invoice_state.derive(
            inv, int(dec(t["gross"]) * 100), pay_rows.get(inv["id"], []), notes.get(inv["id"], [])
        )
        if st.status == "cancelled" or inv.get("status") == "cancelled":
            continue
        if st.open_cents > 0:  # only what is still owed; an overpaid invoice is no receivable (as the old code)
            total += Decimal(st.open_cents) / 100  # gross less credit notes and payments
    return total
