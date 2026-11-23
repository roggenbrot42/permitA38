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

_NONE = TaxKey("NONE")
_KU = TaxKey("KU")
_EU_SVC = TaxKey("EU_SVC")
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
    return IssuedSnapshot(
        document_id=document_id,
        regime="small_business" if klein else "regular",
        treatment="small_business" if klein else treatment,
        rates=groups,
        perf=performance_date(doc, invoice_date),
    )


def _rate_str(rate: Decimal) -> str:
    return format(rate.normalize(), "f")


def _suffix(rate: Decimal) -> str:
    text = _rate_str(rate)
    return text if text in ("19", "7", "0") else "x"


def _role(prefix: str, rate: Decimal) -> Role:
    return Role(f"{prefix}_s{_suffix(rate)}")


def _tax_key(rate: Decimal) -> TaxKey:
    return _S_KEYS.get(_rate_str(rate), TaxKey("SX"))


def _revenue_line(grp: RateGroup, invoice_date: date) -> Line:
    return Line(
        _role("revenue", grp.rate),
        Cents(-grp.net),
        tax_key=_NONE if grp.rate == 0 else None,
        income_date=invoice_date,
    )


def _vat_out_line(grp: RateGroup, vat_date: date) -> Line:
    return Line(
        _role("vat_out", grp.rate),
        Cents(-grp.vat),
        tax_key=_tax_key(grp.rate),
        tax_kind="tax",
        tax_rate=_rate_str(grp.rate),
        tax_base_cents=grp.net,
        vat_date=vat_date,
    )


def issue_entry(
    snapshot: IssuedSnapshot,
    bases: Bases,
    treatment: Treatment,
    perf_date: date,
    invoice_date: date,
) -> Entry | None:
    """The entry of ``finalize`` for the basis combinations of spec 9.1; ``None`` when nothing is posted."""
    item = snapshot.document_id
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
                tax_key=_KU,
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
                tax_key=_EU_SVC,
                tax_kind="base_only",
                tax_base_cents=n,
                vat_date=perf_date,
                income_date=invoice_date if accrual else None,
            )
        )
    elif accrual:
        lines.append(Line(Role("receivables"), snapshot.gross, open_item_id=item))
        for grp in snapshot.rates:
            lines.append(_revenue_line(grp, invoice_date))
            if grp.rate == 0:
                continue
            if bases.vat == "soll":
                lines.append(_vat_out_line(grp, perf_date))
            else:
                lines.append(Line(_role("vat_not_due", grp.rate), Cents(-grp.vat)))
    elif bases.vat == "soll":
        for grp in snapshot.rates:
            if grp.rate == 0:
                continue
            lines.append(Line(_role("vat_soll_clearing", grp.rate), grp.vat))
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
