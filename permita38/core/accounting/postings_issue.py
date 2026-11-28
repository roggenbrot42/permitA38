"""Postings of an issued invoice (docs/specs/accounting.md 9.1). Pure functions, integer cents, no floats."""

from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from permita38.core.accounting.types import (
    Bases,
    Cents,
    Entry,
    IssuedSnapshot,
    Line,
    RateGroup,
    Role,
    TaxKey,
    Treatment,
)
from permita38.core.money import ZERO, dec, line_amount, q2, vat_amount

__all__ = [
    "TAX_EU_SVC",
    "TAX_KU",
    "TAX_NONE",
    "issue_entry",
    "performance_date",
    "rate_str",
    "role_for",
    "snapshot_from_document",
    "tax_key_for",
]

TAX_NONE = TaxKey("NONE")
TAX_KU = TaxKey("KU")
TAX_EU_SVC = TaxKey("EU_SVC")
_S_KEYS: dict[str, TaxKey] = {"19": TaxKey("S19"), "7": TaxKey("S7")}


def _cents(value: Decimal) -> Cents:
    return Cents(int(q2(value) * 100))


def performance_date(doc: Mapping[str, object], invoice_date: date) -> date:
    """Performance date: ``service_to``, else ``service_from``, else the invoice date."""
    for key in ("service_to", "service_from"):
        raw = doc.get(key)
        if isinstance(raw, str) and raw:
            return date.fromisoformat(raw[:10])
    return invoice_date


def snapshot_from_document(
    doc: Mapping[str, object],
    settings: Mapping[str, object],
    *,
    document_id: str,
    treatment: Treatment,
    invoice_date: date,
) -> IssuedSnapshot:
    """Per-rate-group net/VAT in cents, rounded exactly as ``calc.doc_totals`` (VAT per rate group, half-up)."""
    klein = bool(settings.get("kleinunternehmer")) or treatment == "small_business"
    raw_default = doc.get("vat_rate")
    default_vat = dec(raw_default if raw_default not in (None, "") else settings.get("vat_rate", 19))
    no_vat = klein or treatment == "eu_b2b_service"
    by_rate: dict[Decimal, Decimal] = defaultdict(lambda: ZERO)
    lines = doc.get("lines")
    for ln in lines if isinstance(lines, list) else []:
        if not isinstance(ln, dict):
            continue
        raw_vat = ln.get("vat")
        rate = ZERO if no_vat else (dec(raw_vat) if raw_vat not in (None, "") else default_vat)
        by_rate[rate] += line_amount(ln.get("qty"), ln.get("price"))
    groups = tuple(
        RateGroup(rate=r, net=_cents(net), vat=_cents(vat_amount(net, r)))
        for r, net in sorted(by_rate.items(), key=lambda kv: kv[0], reverse=True)
    )
    if any(g.gross > 0 for g in groups) and any(g.gross < 0 for g in groups):
        raise ValueError(
            "an invoice whose rate groups have mixed signs (one positive, one negative gross) cannot be issued; "
            "split it into separate documents"
        )
    return IssuedSnapshot(
        document_id=document_id,
        regime="small_business" if klein else "regular",
        treatment="small_business" if klein else treatment,
        rates=groups,
        perf=performance_date(doc, invoice_date),
    )


def rate_str(rate: Decimal) -> str:
    return format(rate.normalize(), "f")


def _suffix(rate: Decimal) -> str:
    text = rate_str(rate)
    return text if text in ("19", "7", "0") else "x"


def role_for(prefix: str, rate: Decimal) -> Role:
    return Role(f"{prefix}_s{_suffix(rate)}")


def tax_key_for(rate: Decimal) -> TaxKey:
    return _S_KEYS.get(rate_str(rate), TaxKey("SX"))


def _revenue_line(grp: RateGroup, invoice_date: date) -> Line:
    return Line(
        role_for("revenue", grp.rate),
        Cents(-grp.net),
        tax_key=TAX_NONE if grp.rate == 0 else None,
        income_date=invoice_date,
    )


def _vat_out_line(grp: RateGroup, vat_date: date) -> Line:
    return Line(
        role_for("vat_out", grp.rate),
        Cents(-grp.vat),
        tax_key=tax_key_for(grp.rate),
        tax_kind="tax",
        tax_rate=rate_str(grp.rate),
        tax_base_cents=grp.net,
        vat_date=vat_date,
    )


def _statistical_line(grp: RateGroup, vat_date: date) -> Line:
    """The base of a group whose VAT rounds to 0: an amount-0 ``base_only`` line (spec 9.1, zero amounts)."""
    return Line(
        role_for("vat_out", grp.rate),
        Cents(0),
        tax_key=tax_key_for(grp.rate),
        tax_kind="base_only",
        tax_rate=rate_str(grp.rate),
        tax_base_cents=grp.net,
        vat_date=vat_date,
    )


def issue_entry(snapshot: IssuedSnapshot, bases: Bases, invoice_date: date) -> Entry | None:
    """The entry of ``finalize`` for the basis combinations of spec 9.1; ``None`` when nothing is posted.

    Treatment and performance date come from the snapshot only; an invoice whose every amount is 0 posts nothing.
    """
    if all(g.net == 0 and g.vat == 0 for g in snapshot.rates):
        return None
    item = snapshot.document_id
    treatment = snapshot.treatment
    perf_date = snapshot.perf
    lines: list[Line] = []
    accrual = bases.income == "accrual"

    if treatment == "small_business":
        if not accrual:
            return None
        g = snapshot.gross
        lines.append(Line(Role("receivables"), g, open_item_id=item))
        lines.append(
            Line(
                Role("revenue_ku"),
                Cents(-g),
                tax_key=TAX_KU,
                tax_kind="base_only",
                tax_base_cents=g,
                vat_date=invoice_date,
                income_date=invoice_date,
            )
        )
    elif treatment == "eu_b2b_service":
        n = snapshot.net
        if accrual:
            lines.append(Line(Role("receivables"), snapshot.gross, open_item_id=item))
        lines.append(
            Line(
                Role("revenue_eu_svc"),
                Cents(-n) if accrual else Cents(0),
                tax_key=TAX_EU_SVC,
                tax_kind="base_only",
                tax_base_cents=n,
                vat_date=perf_date,
                income_date=invoice_date if accrual else None,
            )
        )
    elif accrual:
        lines.append(Line(Role("receivables"), snapshot.gross, open_item_id=item))
        for grp in snapshot.rates:
            if grp.net == 0:
                continue
            lines.append(_revenue_line(grp, invoice_date))
            if grp.rate == 0:
                continue
            if grp.vat == 0:
                # Soll: the base still reaches Kz 81/86 through a statistical line. Ist: the base arrives with the
                # payment (targets), so nothing is posted for the VAT at issue.
                if bases.vat == "soll":
                    lines.append(_statistical_line(grp, perf_date))
            elif bases.vat == "soll":
                lines.append(_vat_out_line(grp, perf_date))
            else:
                lines.append(Line(role_for("vat_not_due", grp.rate), Cents(-grp.vat)))
    elif bases.vat == "soll":
        for grp in snapshot.rates:
            if grp.rate == 0 or grp.net == 0:
                continue
            if grp.vat == 0:
                lines.append(_statistical_line(grp, perf_date))
                continue
            lines.append(Line(role_for("vat_soll_clearing", grp.rate), grp.vat))
            lines.append(_vat_out_line(grp, perf_date))
    else:
        return None

    if not lines:
        return None
    return Entry(
        lines=tuple(lines),
        booking_date=invoice_date,
        document_date=invoice_date,
        event_key=f"issue:{item}",
    )
