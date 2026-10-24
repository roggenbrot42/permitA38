from permita38.core import documents, rights
from permita38.core.errors import ServiceError
from permita38.core.holidays import STATES
from permita38.core.repository import Repository
from permita38.core.settings import effective_settings

from .auth import public_user
from .records import RESPONSE_ONLY
from .team_timeline import SETTING as TEAM_TIMELINE_KEY
from .team_timeline import visibility as team_timeline_visibility

#: numbering settings: changed only through the guarded numbering service
NUMBERING_KEYS = ("invoice_pattern", "offer_pattern", "invoice_reset", "offer_reset", "numbering_shared")


class SettingsService:
    def __init__(self, store: Repository):
        self.store = store

    def get(self, *, team: bool = False) -> dict:
        """The settings. The team timeline visibility is left out unless ``team`` (the boss sees it, with defaults)."""
        raw = effective_settings(self.store.settings())
        out = {k: v for k, v in raw.items() if k != TEAM_TIMELINE_KEY}
        if team:
            out[TEAM_TIMELINE_KEY] = team_timeline_visibility(raw)
        return out

    def update(self, data: dict) -> dict:
        data = {k: v for k, v in data.items() if k not in RESPONSE_ONLY}  # response-only warnings are never stored
        team = data.pop(TEAM_TIMELINE_KEY, None)
        stored = effective_settings(self.store.settings())  # keeps the stored visibility map, which save replaces
        current = {k: v for k, v in stored.items() if k not in RESPONSE_ONLY}
        changed = [k for k in NUMBERING_KEYS if k in data and data[k] != current.get(k)]
        if changed:
            raise ServiceError(
                400,
                "Numbering is changed with PUT /api/numbering (preview, warnings and confirmation), not with "
                "PUT /api/settings. Remove: " + ", ".join(changed),
            )
        merged = effective_settings({**current, **data})
        if team is not None:  # a partial map: the roles not given keep their level
            merged[TEAM_TIMELINE_KEY] = {**team_timeline_visibility(stored), **team}
        self.store.save_settings(merged)
        return self.get(team=True)

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
