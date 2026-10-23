"""Rule framework of the soft-warning layer: warnings, rule registry and the context handed to every rule."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from permita38.core.holidays import holidays as de_holidays
from permita38.core.repository import Repository

SEVERITIES = ("info", "warning", "critical")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
BLOCKING = ("warning", "critical")  # info never blocks a save


@dataclass(frozen=True)
class SanityWarning:
    code: str
    severity: str
    field: str | None
    message: str
    details: dict = field(default_factory=dict)
    confirmable: bool = True

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "field": self.field,
            "message": self.message,
            "details": self.details,
            "confirmable": self.confirmable,
        }

    @property
    def blocking(self) -> bool:
        return self.severity in BLOCKING


@dataclass
class Finding:
    """What a rule function yields; the engine adds code, default severity and confirmability."""

    message: str
    field: str | None = None
    details: dict = field(default_factory=dict)
    severity: str | None = None  # overrides the rule's default


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    why: str  # plain-language explanation shown in the UI and in docs/sanity-checks.md
    severity: str
    collections: tuple[str, ...]
    fn: Callable[["Ctx"], Iterable[Finding]]
    confirmable: bool = True
    thresholds: dict[str, float] = field(default_factory=dict)
    threshold_help: dict[str, str] = field(default_factory=dict)
    finalize_only: bool = False
    stored_ok: bool = True  # may run over already stored records (the data-health report)


RULES: dict[str, Rule] = {}


def rule(
    id: str,
    title: str,
    why: str,
    severity: str,
    collections: str | tuple[str, ...],
    *,
    confirmable: bool = True,
    thresholds: dict[str, float] | None = None,
    threshold_help: dict[str, str] | None = None,
    finalize_only: bool = False,
    stored_ok: bool = True,
):
    """Register a rule function ``fn(ctx) -> iterable of Finding``."""
    assert severity in SEVERITIES and id not in RULES

    def deco(fn: Callable[["Ctx"], Iterable[Finding]]):
        RULES[id] = Rule(
            id=id,
            title=title,
            why=why,
            severity=severity,
            collections=(collections,) if isinstance(collections, str) else tuple(collections),
            fn=fn,
            confirmable=confirmable,
            thresholds=dict(thresholds or {}),
            threshold_help=dict(threshold_help or {}),
            finalize_only=finalize_only,
            stored_ok=stored_ok,
        )
        return fn

    return deco


class SanityConfig:
    """The user's tuning: ``settings["sanity"] = {"disabled": [...], "thresholds": {"rule.name": number}}``."""

    def __init__(self, raw: Any = None):
        raw = raw if isinstance(raw, dict) else {}
        disabled = raw.get("disabled")
        self.disabled: frozenset[str] = frozenset(x for x in disabled if isinstance(x, str)) if isinstance(disabled, list) else frozenset()
        th = raw.get("thresholds")
        self.thresholds: dict[str, float] = {
            k: float(v) for k, v in (th.items() if isinstance(th, dict) else []) if isinstance(v, (int, float)) and not isinstance(v, bool)
        }

    def enabled(self, rule_id: str) -> bool:
        return rule_id not in self.disabled


def parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def parse_clock(value: Any) -> int | None:
    """``HH:MM`` -> minutes since midnight."""
    if isinstance(value, str) and len(value) == 5 and value[2] == ":":
        try:
            h, m = int(value[:2]), int(value[3:])
        except ValueError:
            return None
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    return None


def num(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


class Ctx:
    """Everything a rule may look at. Collection lists are loaded lazily and cached for the life of one check
    (or one report), so a rule never costs more than a single list call per collection."""

    def __init__(
        self,
        store: Repository,
        settings: dict,
        coll: str,
        record: dict,
        *,
        old: dict | None = None,
        me: dict | None = None,
        today: date | None = None,
        finalize: bool = False,
        cache: dict | None = None,
    ):
        self.store = store
        self.settings = settings
        self.coll = coll
        self.record = record
        self.old = old
        self.me = me
        self.today = today or date.today()
        self.finalize = finalize
        self.cfg = SanityConfig(settings.get("sanity"))
        self._cache: dict = cache if cache is not None else {}
        self.rule_id = ""  # set by the engine while a rule runs

    # -- thresholds ------------------------------------------------------------------------------------
    def threshold(self, name: str) -> float:
        key = f"{self.rule_id}.{name}"
        if key in self.cfg.thresholds:
            return self.cfg.thresholds[key]
        return RULES[self.rule_id].thresholds[name]

    # -- data access (cached) --------------------------------------------------------------------------
    def rows(self, coll: str) -> list[dict]:
        key = ("rows", coll)
        if key not in self._cache:
            self._cache[key] = self.store.list(coll)
        return self._cache[key]

    def others(self, coll: str) -> list[dict]:
        """All stored records of a collection except the one being checked."""
        me_id = self.record.get("id") or (self.old or {}).get("id")
        return [r for r in self.rows(coll) if not me_id or r.get("id") != me_id]

    def get(self, coll: str, rid: Any) -> dict | None:
        if not isinstance(rid, str):
            return None
        return next((r for r in self.rows(coll) if r.get("id") == rid), None)

    def entries_of(self, user_id: Any, day: str) -> list[dict]:
        """Stored time entries of one person on one day (indexed once per check/report)."""
        key = ("entries_by_user_day",)
        if key not in self._cache:
            index: dict[tuple, list[dict]] = {}
            for e in self.rows("entries"):
                index.setdefault((e.get("user_id"), e.get("date")), []).append(e)
            self._cache[key] = index
        me_id = self.record.get("id") or (self.old or {}).get("id")
        return [e for e in self._cache[key].get((user_id, day), []) if not me_id or e.get("id") != me_id]

    def user_of(self, record: dict) -> Any:
        """Who a time entry belongs to: the stored user, or the person who is typing it."""
        return record.get("user_id") or (self.me or {}).get("id")

    def is_employee(self, user_id: Any) -> bool:
        """ArbZG applies to employees. Today: everybody who is not a boss (the roles module will refine this)."""
        if not user_id:
            return False
        u = self.get("users", user_id)
        return bool(u) and u.get("role") != "boss"

    def holiday(self, day: date) -> str | None:
        key = ("hol", day.year)
        if key not in self._cache:
            self._cache[key] = de_holidays(day.year, self.settings.get("state", "") or "")
        return self._cache[key].get(day)

    @property
    def is_member(self) -> bool:
        return bool(self.me) and self.me.get("role") != "boss"

    def changed(self, name: str) -> bool:
        """True for a new record or when ``name`` differs from the stored one (avoids nagging about untouched data)."""
        return self.old is None or self.old.get(name) != self.record.get(name)
