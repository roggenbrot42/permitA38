"""ZM (EC sales list) computation from journal lines (docs/specs/zm.md "Computation"; accounting 6, 7a). Pure, no I/O.

Money is integer cents; the whole-euro amounts are truncated toward zero (rules data ``base_rounding``).
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from permita38.core.accounting.types import Line

__all__ = ["ZmLine", "kz21_by_month", "zm_lines"]

_EU_SVC = "EU_SVC"


@dataclass(frozen=True)
class ZmLine:
    """One ZM row: a customer VAT id with the whole-euro sum of its services in the quarter."""

    vat_id: str
    country: str
    amount: int
    kind: Literal["S"]
    source: tuple[Line, ...]


def _trunc_euros(cents: int) -> int:
    """Whole euros, truncated toward zero."""
    return -(-cents // 100) if cents < 0 else cents // 100


def zm_lines(
    lines: Iterable[Line],
    year: int,
    quarter: int,
    *,
    vat_id_of: Callable[[Line], str],
    corrected: bool = False,
) -> list[ZmLine]:
    """Group the ``EU_SVC`` base lines whose ``vat_date`` lies in the quarter by customer VAT id.

    A total of 0 is omitted unless ``corrected``. Result sorted by VAT id.
    """
    if not 1 <= quarter <= 4:
        raise ValueError(f"invalid quarter {quarter}")
    first_month = 3 * quarter - 2
    sums: dict[str, int] = {}
    sources: dict[str, list[Line]] = {}
    for ln in lines:
        d = ln.vat_date
        base = ln.tax_base_cents
        if ln.tax_key != _EU_SVC or ln.tax_kind != "base_only" or d is None or base is None:
            continue
        if d.year != year or not first_month <= d.month <= first_month + 2:
            continue
        vid = vat_id_of(ln)
        sums[vid] = sums.get(vid, 0) + int(base)
        sources.setdefault(vid, []).append(ln)
    return [
        ZmLine(vid, vid[:2], _trunc_euros(total), "S", tuple(sources[vid]))
        for vid, total in sorted(sums.items())
        if corrected or _trunc_euros(total) != 0
    ]


def kz21_by_month(lines: Iterable[Line], year: int) -> dict[int, int]:
    """UStVA Kz 21 per month (1..12) in whole euros, for the months with ``EU_SVC`` lines (cross-check of the ZM)."""
    sums: dict[int, int] = {}
    for ln in lines:
        d = ln.vat_date
        base = ln.tax_base_cents
        if ln.tax_key != _EU_SVC or ln.tax_kind != "base_only" or d is None or base is None or d.year != year:
            continue
        sums[d.month] = sums.get(d.month, 0) + int(base)
    return {m: _trunc_euros(t) for m, t in sorted(sums.items())}
