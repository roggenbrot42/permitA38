from permita38.core import documents, rights
from permita38.core.errors import ServiceError
from permita38.core.holidays import STATES
from permita38.core.models import WorkLimits
from permita38.core.repository import Repository
from permita38.core.settings import LEGACY_TAX_KEYS, effective_settings

from .auth import public_user
from .records import RESPONSE_ONLY
from .team_timeline import SETTING as TEAM_TIMELINE_KEY
from .team_timeline import visibility as team_timeline_visibility

#: the personal tax profile is stored as a setting but changed only through its own endpoint
PERSONAL_TAX_KEY = "personal_tax"
HIDDEN_KEYS = (PERSONAL_TAX_KEY, *LEGACY_TAX_KEYS)

#: per-boss planning limits are stored as ``work_limits.<user_id>`` and changed only through their own endpoint
WORK_LIMITS_PREFIX = "work_limits."
WORK_LIMIT_FIELDS = ("info_day", "warn_day", "max_day", "info_week", "info_average")

#: numbering settings: changed only through the guarded numbering service
NUMBERING_KEYS = ("invoice_pattern", "offer_pattern", "invoice_reset", "offer_reset", "numbering_shared")


class SettingsService:
    def __init__(self, store: Repository):
        self.store = store

    def get(self, *, team: bool = False) -> dict:
        """The settings. The team timeline visibility is left out unless ``team`` (the boss sees it, with defaults)."""
        raw = effective_settings(self.store.settings())
        out = {
            k: v
            for k, v in raw.items()
            if k != TEAM_TIMELINE_KEY and k not in HIDDEN_KEYS and not k.startswith(WORK_LIMITS_PREFIX)
        }
        if team:
            out[TEAM_TIMELINE_KEY] = team_timeline_visibility(raw)
        return out

    def update(self, data: dict) -> dict:
        if any(k.startswith(WORK_LIMITS_PREFIX) for k in data):
            raise ServiceError(400, "Work limits are changed with PUT /api/settings/work-limits")
        if PERSONAL_TAX_KEY in data:
            raise ServiceError(400, "The personal tax profile is changed with PUT /api/settings/personal-tax")
        data = {k: v for k, v in data.items() if k not in RESPONSE_ONLY}  # response-only warnings are never stored
        team = data.pop(TEAM_TIMELINE_KEY, None)
        stored = effective_settings(self.store.settings())  # keeps the stored visibility map, which save replaces
        current = {k: v for k, v in stored.items() if k not in RESPONSE_ONLY}  # includes a stored personal_tax
        changed = [k for k in NUMBERING_KEYS if k in data and data[k] != current.get(k)]
        if changed:
            raise ServiceError(
                400,
                "Numbering is changed with PUT /api/numbering (preview, warnings and confirmation), not with "
                "PUT /api/settings. Remove: " + ", ".join(changed),
            )
        merged = effective_settings({**current, **data})
        for key in LEGACY_TAX_KEYS:  # stored legacy values are dropped lazily
            merged.pop(key, None)
        if team is not None:  # a partial map: the roles not given keep their level
            merged[TEAM_TIMELINE_KEY] = {**team_timeline_visibility(stored), **team}
        self.store.save_settings(merged)
        return self.get(team=True)

    def get_work_limits(self, user_id: str) -> dict:
        """The user's stored work limits, or the defaults with ``stored: false``."""
        stored = self.store.settings().get(WORK_LIMITS_PREFIX + user_id)
        if isinstance(stored, dict):
            return WorkLimits(**{k: stored[k] for k in WORK_LIMIT_FIELDS if k in stored}, stored=True).model_dump()
        return WorkLimits().model_dump()

    def put_work_limits(self, user_id: str, data: dict) -> dict:
        raw = self.store.settings()
        raw[WORK_LIMITS_PREFIX + user_id] = {k: data[k] for k in WORK_LIMIT_FIELDS}
        self.store.save_settings(raw)
        return self.get_work_limits(user_id)

    def templates(self) -> list[dict]:
        return list(documents.builtin_templates().values()) + self.store.list("templates")

    def bootstrap(self, me: dict, timer: dict) -> dict:
        st = self.get()
        if rights.scope_for(me, "settings") != rights.Scope.ALL:
            st = {k: st.get(k) for k in ("company_name", "state")}
        sees_conflicts = rights.allowed_for(me, "settings", "write") or rights.allowed_for(me, "team", "write")
        return dict(
            me=public_user(me),
            settings=st,
            states=STATES,
            templates=self.templates() if rights.allowed_for(me, "templates") else [],
            timer=timer,
            conflicts=self.store.conflicts() if sees_conflicts else [],
        )
