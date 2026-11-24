"""ZM (EC sales list) computation from journal lines (docs/specs/zm.md "Computation"; accounting 6, 7a). Pure, no I/O.

Money is integer cents; the whole-euro amounts are rounded by the rules data ``base_rounding`` (only
``truncate_toward_zero`` is defined; an unknown mode raises ``ValueError``).
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from permita38.core.accounting.types import Line
from permita38.core.vat_rules import vat_rules_for

__all__ = ["ZmLine", "kz21_by_month", "normalize_vat_id", "zm_lines"]

_EU_SVC = "EU_SVC"


@dataclass(frozen=True)
class ZmLine:
    """One ZM row: a customer VAT id with the whole-euro sum of its services in the quarter."""

    vat_id: str
    country: str
    amount: int
    kind: Literal["S"]
    source: tuple[Line, ...]


def normalize_vat_id(vat_id: str) -> str:
    """Canonical VAT id: spaces, dots and dashes removed, ASCII letters upper-cased, a ``GR`` prefix becomes ``EL``."""
    vid = re.sub(r"[ .\-]", "", vat_id)
    vid = "".join(c.upper() if "a" <= c <= "z" else c for c in vid)
    return "EL" + vid[2:] if vid.startswith("GR") else vid


def _euros(cents: int, year: int) -> int:
    """Whole euros, rounded by the ``base_rounding`` mode of the VAT rules of ``year``."""
    mode = vat_rules_for(year).base_rounding
    if mode != "truncate_toward_zero":
        raise ValueError(f"unknown base_rounding mode {mode!r}")
    return -(-cents // 100) if cents < 0 else cents // 100


def zm_lines(
    lines: Iterable[Line],
    year: int,
    quarter: int,
    *,
    vat_id_of: Callable[[Line], str],
    corrected: bool = False,
) -> list[ZmLine]:
    """Group the ``EU_SVC`` base lines whose ``vat_date`` lies in the quarter by normalised customer VAT id.

    Amounts are whole euros per the rules' ``base_rounding`` (``truncate_toward_zero``; others raise ``ValueError``).
    Cents are summed per normalised id first and rounded once. A total that rounds to 0 is omitted unless ``corrected``. An empty VAT id raises ``ValueError``.
    Result sorted by VAT id.
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
        vid = normalize_vat_id(vat_id_of(ln))
        if not vid:
            raise ValueError("missing customer VAT id for an EU_SVC line")
        sums[vid] = sums.get(vid, 0) + int(base)
        sources.setdefault(vid, []).append(ln)
    rounded = {vid: _euros(total, year) for vid, total in sums.items()}
    return [
        ZmLine(vid, vid[:2], rounded[vid], "S", tuple(sources[vid]))
        for vid in sorted(sums)
        if corrected or rounded[vid] != 0
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
    return {m: _euros(t, year) for m, t in sorted(sums.items())}
