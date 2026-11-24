"""Period locks (docs/specs/accounting.md 7.8, 8.1, 11, 17-18).

``period_locks`` rows lock whole months **in order** from the accounting start (settings key ``accounting.start``; unset: the
January of the earliest fiscal year); there is no unlock. :class:`LockService` reads and writes them; :func:`vat_date_for` is the pure rule that moves
a VAT date out of a locked month (or allows the drift cases). No SQL here.
"""

import time
from collections.abc import Callable, Iterable, Mapping
from datetime import date, timedelta

from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.settings import accounting_start

COLLECTION = "period_locks"
REASON_MANUAL = "manual"
REASON_YEAR_CLOSE = "year_close"
REASON_VAT_PREFIX = "vat_return:"

# section 11, the four allowed drift cases (automatic postings only)
DRIFT_KINDS: frozenset[str] = frozenset({"soll_issue", "cash_event", "rc_tax_date", "eu_service"})
MOVING_KINDS: frozenset[str] = frozenset({"input_vat", "credit_note", "write_off", "other"})


def first_day_after(year: int, month: int) -> date:
    return date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)


def first_unlocked_from(rows: Iterable[Mapping[str, object]]) -> date | None:
    """The first day of the month after the latest locked month; ``None`` without locks."""
    keys = [(int(str(r["year"])), int(str(r["month"]))) for r in rows]
    if not keys:
        return None
    return first_day_after(*max(keys))


def vat_date_for(kind: str, day: date, first_unlocked: date) -> tuple[date, bool]:
    """``(vat_date, drift)`` of a VAT line dated ``day`` (section 11). Drift kinds keep a locked date and report
    ``drift=True``; non-drift kinds move to ``max(day, first_unlocked)``; a manual entry may not use a locked month
    (422)."""
    locked = day < first_unlocked
    if kind in DRIFT_KINDS:
        return day, locked
    if kind in MOVING_KINDS:
        return max(day, first_unlocked), False
    if kind == "manual":
        if locked:
            raise ServiceError(422, f"vat_date {day.isoformat()} lies in a locked month")
        return day, False
    raise ServiceError(422, f"Unknown VAT date kind '{kind}'")


def _months(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = start
    while (y, m) <= end:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


class LockService:
    def __init__(
        self, store: Repository, today: Callable[[], date] = date.today, clock: Callable[[], float] = time.time
    ):
        self.store = store
        self.today = today
        self.clock = clock

    # ---------- reads
    def all(self) -> list[Record]:
        return sorted(self.store.list(COLLECTION), key=lambda r: (int(r["year"]), int(r["month"])))

    def locked_months(self, year: int) -> list[int]:
        return sorted(int(r["month"]) for r in self.store.list(COLLECTION) if int(r["year"]) == year)

    def first_unlocked_day(self) -> date:
        """The first day of the first unlocked month; ``date.min`` while nothing is locked."""
        return first_unlocked_from(self.store.list(COLLECTION)) or date.min

    def _start(self) -> tuple[int, int]:
        """The first lockable month: ``accounting.start`` from the settings (accounting.md 16 step 1). While that is
        unset, the fallback is 1 January of the earliest fiscal year (409 when there is none)."""
        start = accounting_start(self.store.settings())
        if start is not None:
            return start.year, start.month
        years = [int(r["year"]) for r in self.store.list("fiscal_years")]
        if not years:
            raise ServiceError(409, "No fiscal year exists yet")
        return min(years), 1

    def _validate(self, year: int, month: int) -> None:
        if not 1 <= month <= 12:
            raise ServiceError(422, "month must be 1..12")
        last_day = first_day_after(year, month) - timedelta(days=1)
        if last_day >= self.today():
            raise ServiceError(422, f"{year}-{month:02d} has not ended yet and cannot be locked")
        if (year, month) < self._start():
            raise ServiceError(422, f"{year}-{month:02d} lies before the accounting start")

    def preview_lock_up_to(self, year: int, month: int) -> list[tuple[int, int]]:
        """The months ``lock_up_to`` would lock now, in order (empty when everything up to it is locked)."""
        self._validate(year, month)
        done = {(int(r["year"]), int(r["month"])) for r in self.store.list(COLLECTION)}
        return [ym for ym in _months(self._start(), (year, month)) if ym not in done]

    # ---------- write
    def lock_up_to(self, year: int, month: int, reason: str, by: str) -> list[Record]:
        """Lock every unlocked month from the accounting start up to ``year``-``month``, in order, in one
        transaction. Already locked months are skipped (idempotent). Returns the new rows."""
        vat_id = reason[len(REASON_VAT_PREFIX) :] if reason.startswith(REASON_VAT_PREFIX) else ""
        if reason not in (REASON_MANUAL, REASON_YEAR_CLOSE) and not vat_id.strip():
            raise ServiceError(422, f"Unknown lock reason '{reason}'")
        with self.store.transaction():
            todo = self.preview_lock_up_to(year, month)
            now = int(self.clock())
            return [
                self.store.save(
                    COLLECTION, {"year": y, "month": m, "locked_at": now, "locked_by": by, "reason": reason}
                )
                for y, m in todo
            ]
