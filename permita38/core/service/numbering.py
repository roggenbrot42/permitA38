"""Document numbering: sequences per document type and period, previews, guarded changes and the audit log.

Storage: one row per (counter group, period) in ``number_sequences``. The row id is derived from the key, so two
concurrent first allocations for the same key hit the same row. Allocation (:meth:`NumberingService.allocate`) must
run inside ``store.transaction()``: the SQLite backend holds its lock for the whole transaction, so two finalizes
can never read the same counter, and a failed finalize rolls the counter back with the document (gapless).

A counter that does not exist yet is *seeded* from the existing documents on first use (the highest number that the
current pattern produces in that period; 0 if none matches). Patterns live
in the settings, which a SQL migration cannot read, so the seeding happens lazily instead of in the migration.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Callable

from permita38.core.errors import ServiceError
from permita38.core.numbering import MAX_NUMBER, Pattern, PatternError, parse_pattern, period_key, sorting_warnings
from permita38.core.repository import Repository
from permita38.core.settings import effective_settings

TYPES = {"invoice": "invoices", "offer": "offers"}
PREVIEW_COUNT = 5
_NUMBER_COLLECTIONS = ("invoices", "offers")


@dataclass(frozen=True)
class NumberConfig:
    coll: str
    pattern: str
    reset: str
    shared: bool


def coll_for(doc_type: str) -> str:
    """'invoice' / 'offer' (also 'invoices' / 'offers') -> collection name."""
    if doc_type in TYPES.values():
        return doc_type
    if doc_type in TYPES:
        return TYPES[doc_type]
    raise ServiceError(400, "Unknown document type; use invoice or offer")


def _parse_date(value) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class NumberingService:
    def __init__(self, store: Repository, clock: Callable[[], date] | None = None):
        self.store = store
        self.clock = clock or date.today

    # ------------------------------------------------------------------ configuration
    def config(self, doc_type: str) -> NumberConfig:
        coll = coll_for(doc_type)
        st = effective_settings(self.store.settings())
        key = "invoice" if coll == "invoices" else "offer"
        return NumberConfig(coll, st[f"{key}_pattern"], st[f"{key}_reset"], bool(st.get("numbering_shared")))

    def _group(self, cfg: NumberConfig) -> str:
        return "shared" if cfg.shared else cfg.coll

    def _row_id(self, group: str, period: str) -> str:
        return "n" + hashlib.sha256(f"{group}|{period}".encode()).hexdigest()[:20]

    # ------------------------------------------------------------------ documents
    def _docs_in_scope(self, cfg: NumberConfig, period: str, reset: str) -> list[dict]:
        colls = _NUMBER_COLLECTIONS if cfg.shared else (cfg.coll,)
        out = []
        for c in colls:
            for doc in self.store.list(c):
                day = _parse_date(doc.get("date"))
                if day is not None and period_key(reset, day) == period:
                    out.append(doc)
        return out

    def _numbers_in_use(self) -> set[str]:
        return {d["number"] for c in _NUMBER_COLLECTIONS for d in self.store.list(c) if d.get("number")}

    def _pattern(self, cfg: NumberConfig) -> Pattern:
        """The stored pattern. Lenient: a pattern from an older version must not break finalize and previews."""
        try:
            return parse_pattern(cfg.pattern, strict=False)
        except PatternError as e:
            raise ServiceError(409, f"The stored numbering pattern is invalid ({e}); fix it under Numbering") from None

    def _seed(self, cfg: NumberConfig, pattern: Pattern, reset: str, period: str) -> int:
        """The highest number the pattern produced in this period (0 if none). Numbers of other formats are ignored;
        numbers that are already taken are skipped when allocating, so they cannot be handed out twice."""
        return max(self._sequence_numbers(pattern, self._docs_in_scope(cfg, period, reset)), default=0)

    @staticmethod
    def _sequence_numbers(pattern: Pattern, docs: list[dict]) -> list[int]:
        """Sequence numbers of the documents whose number the pattern produced for the document's own date."""
        out = []
        for d in docs:
            number, day = d.get("number"), _parse_date(d.get("date"))
            if not number or day is None:
                continue
            n = pattern.match(number)
            if n and pattern.date_consistent(number, day):
                out.append(n)
        return out

    def _last(self, cfg: NumberConfig, pattern: Pattern, day: date) -> int:
        period = period_key(cfg.reset, day)
        row = self.store.get("number_sequences", self._row_id(self._group(cfg), period))
        if row is not None:
            return int(row.get("last_value") or 0)
        return self._seed(cfg, pattern, cfg.reset, period)

    def _candidates(self, cfg: NumberConfig, pattern: Pattern, day: date, count: int) -> list[tuple[int, str]]:
        """The next ``count`` (sequence, number) pairs that are not in use yet."""
        taken = self._numbers_in_use()
        out: list[tuple[int, str]] = []
        n = self._last(cfg, pattern, day)
        while len(out) < count:
            n += 1
            if n > MAX_NUMBER:
                raise ServiceError(409, "The number sequence is exhausted; change the start number first")
            number = pattern.format(day, n)
            if number not in taken:
                out.append((n, number))
        return out

    # ------------------------------------------------------------------ public reads
    def preview(self, doc_type: str, count: int = PREVIEW_COUNT) -> list[str]:
        cfg = self.config(doc_type)
        pattern = self._pattern(cfg)
        return [number for _, number in self._candidates(cfg, pattern, self.clock(), count)]

    def status(self, doc_type: str) -> dict:
        cfg = self.config(doc_type)
        pattern = self._pattern(cfg)
        day = self.clock()
        period = period_key(cfg.reset, day)
        found = self._sequence_numbers(pattern, self._docs_in_scope(cfg, period, cfg.reset))
        return {
            "type": doc_type,
            "pattern": cfg.pattern,
            "reset": cfg.reset,
            "shared": cfg.shared,
            "period": period,
            "next": self.preview(doc_type),
            "highest_in_period": max(found, default=0),
            "warnings": sorting_warnings(pattern, cfg.reset),
        }

    def log(self, doc_type: str | None = None) -> list[dict]:
        rows = self.store.list("numbering_log")
        if doc_type:
            rows = [r for r in rows if r.get("doc_type") == coll_for(doc_type)]
        return rows

    # ------------------------------------------------------------------ allocation (inside a transaction)
    def allocate(self, coll: str, day: date) -> str:
        """Take the next number for a document of ``coll`` dated ``day``. Call inside ``store.transaction()``."""
        cfg = self.config(coll)
        pattern = self._pattern(cfg)
        ((n, number),) = self._candidates(cfg, pattern, day, 1)
        self.store.save(
            "number_sequences",
            dict(
                id=self._row_id(self._group(cfg), period_key(cfg.reset, day)),
                doc_type=self._group(cfg),
                period=period_key(cfg.reset, day),
                last_value=n,
            ),
        )
        return number

    def imported_number_note(self, coll: str, number: str, day: date) -> str | None:
        """A hint for the import report when an imported number does not move the counter of its document date."""
        cfg = self.config(coll)
        pattern = self._pattern(cfg)
        if pattern.match(number) is not None and not pattern.date_consistent(number, day):
            return "the date part of the number differs from the document date; the number counter is not moved"
        return None

    def observe_imported(self, coll: str, number: str, day: date) -> bool:
        """An imported document carries its own number: move the counter so the next number follows it.

        Returns True if the counter moved. (No previews here: this runs once per imported row.)
        """
        cfg = self.config(coll)
        pattern = self._pattern(cfg)
        n = pattern.match(number)
        if n is None or not pattern.date_consistent(number, day):
            return False  # not our format, or a number of another year/month: the counter of this period stays
        period = period_key(cfg.reset, day)
        row_id = self._row_id(self._group(cfg), period)
        row = self.store.get("number_sequences", row_id)
        current = int(row.get("last_value") or 0) if row is not None else self._seed(cfg, pattern, cfg.reset, period)
        if row is not None and n <= current:
            return False
        # persist the counter even when the seed already covers n: the seed is computed from the documents, and
        # would shrink again if an import is undone; a stored counter never moves back
        self.store.save(
            "number_sequences",
            dict(id=row_id, doc_type=self._group(cfg), period=period, last_value=max(n, current)),
        )
        return n > current

    # ------------------------------------------------------------------ guarded changes
    def check(
        self,
        doc_type: str,
        *,
        pattern: str | None = None,
        reset: str | None = None,
        next_number: int | None = None,
    ) -> dict:
        """Dry run of :meth:`update`: the warnings and the resulting preview. Writes nothing."""
        cfg = self.config(doc_type)
        new_pattern_text = pattern if pattern is not None else cfg.pattern
        new_reset = reset if reset is not None else cfg.reset
        try:  # an unchanged stored pattern is parsed leniently, so the start number of a legacy pattern can be changed
            new_pattern = parse_pattern(new_pattern_text, strict=new_pattern_text != cfg.pattern)
        except PatternError as e:
            raise ServiceError(400, str(e)) from None
        if next_number is not None and not 1 <= next_number <= MAX_NUMBER:
            raise ServiceError(400, "The next number must be between 1 and 1000000000")
        day = self.clock()
        new_cfg = NumberConfig(cfg.coll, new_pattern_text, new_reset, cfg.shared)
        period = period_key(new_reset, day)
        docs = self._docs_in_scope(new_cfg, period, new_reset)
        found = self._sequence_numbers(new_pattern, docs)
        highest = max(found, default=0)
        target = next_number if next_number is not None else self._last(new_cfg, new_pattern, day) + 1

        warnings: list[dict] = []
        for msg in sorting_warnings(new_pattern, new_reset):
            warnings.append({"code": "sorting", "message": msg})
        if new_pattern_text != cfg.pattern and self._docs_in_scope(cfg, period_key(cfg.reset, day), cfg.reset):
            warnings.append({"code": "pattern_change", "message": "Existing numbers keep their old format."})
        if new_reset != cfg.reset:
            warnings.append({"code": "reset_change", "message": "The sequence restarts on a different schedule."})
        if highest and target <= highest:
            warnings.append(
                {
                    "code": "backwards",
                    "message": f"The next number would be {target}, but {highest} is already used in this period.",
                }
            )
        elif highest and target > highest + 1:
            warnings.append(
                {"code": "gap", "message": f"Numbers {highest + 1} to {target - 1} would be skipped (a gap)."}
            )
        candidate = new_pattern.format(day, target)
        if candidate in self._numbers_in_use():
            warnings.append({"code": "duplicate", "message": f"{candidate} is already used by another document."})
        preview: list[str] = []
        taken = self._numbers_in_use()
        n = target
        while len(preview) < PREVIEW_COUNT and n <= MAX_NUMBER:  # skip numbers in use, like the real allocation
            number = new_pattern.format(day, n)
            if number not in taken:
                preview.append(number)
            n += 1
        return {
            "type": doc_type,
            "pattern": new_pattern_text,
            "reset": new_reset,
            "next": target,
            "preview": preview,
            "highest_in_period": highest,
            "warnings": warnings,
        }

    def update(
        self,
        doc_type: str,
        *,
        pattern: str | None = None,
        reset: str | None = None,
        next_number: int | None = None,
        reason: str = "",
        confirm: bool = False,
    ) -> dict:
        """Apply a change to the pattern, reset scope or next number. Risky changes need ``confirm``.

        The result has ``applied``; when it is False the caller must show the warnings and ask again with
        ``confirm=True``.
        """
        changed = pattern is not None or reset is not None or next_number is not None
        with self.store.transaction():  # check and write in one transaction: no finalize can slip in between
            result = self.check(doc_type, pattern=pattern, reset=reset, next_number=next_number)
            if result["warnings"] and not confirm:
                return {**result, "applied": False}
            cfg = self.config(doc_type)
            day = self.clock()
            try:
                before_next = self.preview(doc_type, 1)[0]
            except ServiceError:  # an invalid stored pattern is exactly what this change may be fixing
                before_next = None
            before = {"pattern": cfg.pattern, "reset": cfg.reset, "next": before_next}
            if pattern is not None or reset is not None:
                st = self.store.settings()
                key = "invoice" if cfg.coll == "invoices" else "offer"
                st[f"{key}_pattern"] = result["pattern"]
                st[f"{key}_reset"] = result["reset"]
                self.store.save_settings(effective_settings(st))
            if next_number is not None:
                new_cfg = NumberConfig(cfg.coll, result["pattern"], result["reset"], cfg.shared)
                period = period_key(new_cfg.reset, day)
                self.store.save(
                    "number_sequences",
                    dict(
                        id=self._row_id(self._group(new_cfg), period),
                        doc_type=self._group(new_cfg),
                        period=period,
                        last_value=next_number - 1,
                    ),
                )
            if changed:
                after = {"pattern": result["pattern"], "reset": result["reset"], "next": result["next"]}
                self.store.save(
                    "numbering_log",
                    dict(
                        doc_type=cfg.coll,
                        action="change" if confirm or result["warnings"] else "update",
                        before=before,
                        after=after,
                        warnings=[w["code"] for w in result["warnings"]],
                        reason=(reason or "")[:500],
                    ),
                )
        return {**result, "applied": True, "previous": before}
