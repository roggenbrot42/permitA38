"""Generic CRUD with role-based permissions."""

from permita38.core import calc
from permita38.core.errors import ServiceError
from permita38.core.repository import COLLECTIONS, ID_RE, Repository
from permita38.core.settings import effective_settings

from .auth import public_user

MEMBER_READ = {"projects", "tasks", "entries", "absences", "users"}
MEMBER_WRITE_OWN = {"entries", "absences"}


class RecordService:
    def __init__(self, store: Repository):
        self.store = store

    def _check(self, coll: str):
        if coll not in COLLECTIONS or coll == "users":
            raise ServiceError(404, "Unknown collection")

    def list_records(self, me: dict, coll: str) -> list[dict]:
        self._check(coll)
        rows = self.store.list(coll)
        if me["role"] == "boss":
            return rows
        if coll not in MEMBER_READ:
            raise ServiceError(403, "Forbidden")
        if coll in ("entries", "absences"):
            rows = [r for r in rows if r.get("user_id") == me["id"]]
        elif coll == "projects":
            rows = [p for p in rows if not p.get("member_ids") or me["id"] in p["member_ids"]]
            keys = ("id", "name", "color", "status", "customer_id", "personal", "member_ids")
            rows = [{k: p[k] for k in keys if k in p} for p in rows]
        return rows

    def get(self, me: dict, coll: str, rid: str) -> dict:
        self._check(coll)
        rec = self.store.get(coll, rid) if _valid_id(rid) else None
        if not rec:
            raise ServiceError(404, "Not found")
        if me["role"] != "boss":
            if coll not in MEMBER_READ:
                raise ServiceError(403, "Forbidden")
            if coll in ("entries", "absences") and rec.get("user_id") != me["id"]:
                raise ServiceError(403, "Forbidden")
        return rec

    def create(self, me: dict, coll: str, data: dict) -> dict:
        self._check(coll)
        data = {k: v for k, v in data.items() if k not in ("id", "created", "updated")}
        return self._write(me, coll, data, None)

    def update(self, me: dict, coll: str, rid: str, data: dict) -> dict:
        self._check(coll)
        old = self.store.get(coll, rid) if _valid_id(rid) else None
        if not old:
            raise ServiceError(404, "Not found")
        return self._write(me, coll, {**data, "id": rid, "created": old.get("created")}, old)

    def _write(self, me: dict, coll: str, data: dict, old: dict | None) -> dict:
        boss = me["role"] == "boss"
        if not boss:
            if coll not in MEMBER_WRITE_OWN:
                raise ServiceError(403, "Forbidden")
            if old and old.get("user_id") != me["id"]:
                raise ServiceError(403, "Forbidden")
            data["user_id"] = me["id"]
        if coll in ("offers", "invoices"):
            data = self.prepare_document(data, old)
        elif coll == "entries":
            data["minutes"] = int(round(float(data.get("minutes") or 0)))
            data.setdefault("billable", True)
            if old and old.get("invoice_id"):
                if not boss:
                    raise ServiceError(403, "Entry already invoiced")
                data.setdefault("invoice_id", old["invoice_id"])
        if data.get("created") is None:
            data.pop("created", None)
        return self.store.save(coll, data)

    def prepare_document(self, doc: dict, old: dict | None) -> dict:
        st = effective_settings(self.store.settings())
        if old:
            doc["number"] = old.get("number")  # numbers are only assigned via finalize
        else:
            doc["status"] = "draft"
            doc.pop("number", None)
            doc["date"] = doc.get("date") or _today()
        if not doc.get("template_id"):
            doc["template_id"] = st["default_template"]
        calc.doc_totals(doc, st)  # fills line amounts
        return {k: v for k, v in doc.items() if v is not None}

    def delete(self, me: dict, coll: str, rid: str):
        self._check(coll)
        old = self.store.get(coll, rid) if _valid_id(rid) else None
        if not old:
            return
        if me["role"] != "boss":
            own = coll in MEMBER_WRITE_OWN and old.get("user_id") == me["id"] and not old.get("invoice_id")
            if not own:
                raise ServiceError(403, "Forbidden")
        with self.store.transaction():
            if coll == "invoices":  # release billed time entries
                for e in self.store.list("entries"):
                    if e.get("invoice_id") == rid:
                        e.pop("invoice_id", None)
                        self.store.save("entries", e)
            self.store.delete(coll, rid)

    def list_users(self, me: dict) -> list[dict]:
        users = self.store.list("users")
        if me["role"] == "boss":
            return [public_user(u) for u in users]
        return [{"id": u["id"], "name": u.get("name"), "role": u.get("role")} for u in users]


def _valid_id(rid: str) -> bool:
    return bool(ID_RE.match(rid))


def _today() -> str:
    from datetime import date

    return date.today().isoformat()
