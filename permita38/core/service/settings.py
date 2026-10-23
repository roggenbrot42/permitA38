from permita38.core import documents
from permita38.core.holidays import STATES
from permita38.core.repository import Repository
from permita38.core.settings import effective_settings

from .auth import public_user


class SettingsService:
    def __init__(self, store: Repository):
        self.store = store

    def get(self) -> dict:
        return effective_settings(self.store.settings())

    def update(self, data: dict) -> dict:
        data = {k: v for k, v in data.items() if k not in ("sanity_warnings", "planning_warnings")}  # response-only
        stored = {k: v for k, v in self.get().items() if k not in ("sanity_warnings", "planning_warnings")}
        merged = effective_settings({**stored, **data})
        self.store.save_settings(merged)
        return merged

    def templates(self) -> list[dict]:
        return list(documents.builtin_templates().values()) + self.store.list("templates")

    def bootstrap(self, me: dict, timer: dict) -> dict:
        st = self.get()
        if me["role"] != "boss":
            st = {k: st.get(k) for k in ("company_name", "state")}
        return dict(
            me=public_user(me),
            settings=st,
            states=STATES,
            templates=self.templates(),
            timer=timer,
            conflicts=self.store.conflicts(),
        )
