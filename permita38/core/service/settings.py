from permita38.core import documents
from permita38.core.holidays import STATES
from permita38.core.repository import Repository
from permita38.core.settings import effective_settings

from .auth import AuthService
from .roles import can


class SettingsService:
    def __init__(self, store: Repository, auth: AuthService):
        self.store = store
        self.auth = auth

    def get(self) -> dict:
        return effective_settings(self.store.settings())

    def update(self, data: dict) -> dict:
        merged = effective_settings({**self.get(), **data})
        self.store.save_settings(merged)
        return merged

    def templates(self) -> list[dict]:
        return list(documents.builtin_templates().values()) + self.store.list("templates")

    def bootstrap(self, me: dict, timer: dict) -> dict:
        st = self.get()
        if not can(me, "settings.manage"):
            st = {k: st.get(k) for k in ("company_name", "state")}
        return dict(
            me=self.auth.public(me),
            settings=st,
            states=STATES,
            templates=self.templates(),
            timer=timer,
            conflicts=self.store.conflicts(),
        )
