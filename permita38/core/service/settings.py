from permita38.core import documents
from permita38.core.errors import ServiceError
from permita38.core.holidays import STATES
from permita38.core.repository import Repository
from permita38.core.settings import effective_settings

from .auth import public_user

#: numbering settings: changed only through the guarded numbering service
NUMBERING_KEYS = ("invoice_pattern", "offer_pattern", "invoice_reset", "offer_reset", "numbering_shared")


class SettingsService:
    def __init__(self, store: Repository):
        self.store = store

    def get(self) -> dict:
        return effective_settings(self.store.settings())

    def update(self, data: dict) -> dict:
        current = self.get()
        changed = [k for k in NUMBERING_KEYS if k in data and data[k] != current.get(k)]
        if changed:
            raise ServiceError(
                400,
                "Numbering is changed with PUT /api/numbering (preview, warnings and confirmation), not with "
                "PUT /api/settings. Remove: " + ", ".join(changed),
            )
        merged = effective_settings({**current, **data})
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
