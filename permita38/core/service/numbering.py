"""Document numbering: sequences per document type and period, previews, guarded changes and the audit log.

Storage: one row per (counter group, period) in ``number_sequences``. The row id is derived from the key, so two
concurrent first allocations for the same key hit the same row. Allocation (:meth:`NumberingService.allocate`) must
run inside ``store.transaction()``: the SQLite backend holds its lock for the whole transaction, so two finalizes
can never read the same counter, and a failed finalize rolls the counter back with the document (gapless).

A counter that does not exist yet is *seeded* from the existing documents on first use (the highest number that the
current pattern produces in that period, or the number of documents in that period if none matches). Patterns live
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

    def _seed(self, cfg: NumberConfig, pattern: Pattern, reset: str, period: str) -> int:
        docs = [d for d in self._docs_in_scope(cfg, period, reset) if d.get("number")]
        found = [n for n in (pattern.match(d["number"]) for d in docs) if n]
        return max(found) if found else len(docs)

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
        pattern = parse_pattern(cfg.pattern)
        return [number for _, number in self._candidates(cfg, pattern, self.clock(), count)]

    def status(self, doc_type: str) -> dict:
        cfg = self.config(doc_type)
        pattern = parse_pattern(cfg.pattern)
        day = self.clock()
        period = period_key(cfg.reset, day)
        found = [
            n for n in (pattern.match(d.get("number") or "") for d in self._docs_in_scope(cfg, period, cfg.reset)) if n
        ]
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
        pattern = parse_pattern(cfg.pattern)
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

    def observe_imported(self, coll: str, number: str, day: date) -> bool:
        """An imported document carries its own number: move the counter so the next number follows it.

        Returns True if the counter moved. (No previews here: this runs once per imported row.)
        """
        cfg = self.config(coll)
        pattern = parse_pattern(cfg.pattern)
        n = pattern.match(number)
        if n is None:
            return False
        current = self._last(cfg, pattern, day)
        if n <= current:
            return False
        self.store.save(
            "number_sequences",
            dict(
                id=self._row_id(self._group(cfg), period_key(cfg.reset, day)),
                doc_type=self._group(cfg),
                period=period_key(cfg.reset, day),
                last_value=n,
            ),
        )
        return True

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
        try:
            new_pattern = parse_pattern(new_pattern_text)
        except PatternError as e:
            raise ServiceError(400, str(e)) from None
        if next_number is not None and not 1 <= next_number <= MAX_NUMBER:
            raise ServiceError(400, "The next number must be between 1 and 1000000000")
        day = self.clock()
        new_cfg = NumberConfig(cfg.coll, new_pattern_text, new_reset, cfg.shared)
        period = period_key(new_reset, day)
        docs = self._docs_in_scope(new_cfg, period, new_reset)
        found = [n for n in (new_pattern.match(d.get("number") or "") for d in docs) if n]
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
        preview = [new_pattern.format(day, target + i) for i in range(PREVIEW_COUNT)]
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
        result = self.check(doc_type, pattern=pattern, reset=reset, next_number=next_number)
        if result["warnings"] and not confirm:
            return {**result, "applied": False}
        cfg = self.config(doc_type)
        day = self.clock()
        before = {"pattern": cfg.pattern, "reset": cfg.reset, "next": self.preview(doc_type, 1)[0]}
        changed = pattern is not None or reset is not None or next_number is not None
        with self.store.transaction():
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
