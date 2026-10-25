"""Parsing of stored values (dates and numbers) that the calculation modules share without depending on each other.

``calc`` re-exports these names, so ``calc.d`` and ``calc.fnum`` keep working.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime

log = logging.getLogger("permita38.core.calc")


def d(s):
    """Parse ``YYYY-MM-DD``; ``None`` for empty or invalid values (logged)."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        log.warning("ignoring invalid date %r", s)
        return None


def fnum(x, default: float = 0.0) -> float:
    """Float from a stored value; damaged values (text, NaN, infinity, overflow) count as ``default``."""
    if x is None or x == "":
        return default
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        log.warning("ignoring invalid number %r", x)
        return default
    return v if math.isfinite(v) else default
