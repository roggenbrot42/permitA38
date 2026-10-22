"""Generic CRUD with role-based permissions."""

from decimal import Decimal

from permita38.core import calc
from permita38.core.errors import ServiceError
from permita38.core.money import dec
from permita38.core.repository import COLLECTIONS, ID_RE, Repository
from permita38.core.settings import effective_settings
from permita38.core.validation import ALLOWED_VAT_RATES, MAX_MONEY

from .auth import public_user

MEMBER_READ = {"projects", "tasks", "entries", "absences", "users"}
MEMBER_WRITE_OWN = {"entries", "absences"}


_IMPORT_FIELDS = (
    "imported",
    "original_number",
    "external_id",
    "import_batch",
    "totals_unverified",
    "original_pdf",
    "original_pdf_sha256",
    "given_totals",
)


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
            rows = [_member_project_view(p) for p in rows if _project_visible(p, me)]
        elif coll == "tasks":
            visible = {p["id"] for p in self.store.list("projects") if _project_visible(p, me)}
            rows = [_member_task_view(t) for t in rows if t.get("project_id") in visible]
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
            if coll == "projects":
                if not _project_visible(rec, me):
                    raise ServiceError(404, "Not found")  # do not reveal that it exists
                return _member_project_view(rec)
            if coll == "tasks":
                pid = rec.get("project_id")
                project = self.store.get("projects", pid) if isinstance(pid, str) and _valid_id(pid) else None
                if not project or not _project_visible(project, me):
                    raise ServiceError(404, "Not found")
                return _member_task_view(rec)
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
            if coll == "entries":  # billing fields are boss/system-only: a member must not set rates or hide hours
                for k in _ENTRY_BILLING_FIELDS:
                    data.pop(k, None)
                    if old and k in old:
                        data[k] = old[k]
        if coll in ("offers", "invoices"):
            self._check_vat_rates(data.get("vat_rate"), *(ln.get("vat") for ln in data.get("lines") or []))
            data = self.prepare_document(data, old)
            self._check_document_amounts(data)
        elif coll == "expenses":
            self._check_vat_rates(data.get("vat_rate"))
        elif coll == "entries":
            data["minutes"] = int(round(float(data.get("minutes") or 0)))
            data.setdefault("billable", True)
            self._check_day_total(data, old)
            if old and old.get("invoice_id"):
                if not boss:
                    raise ServiceError(403, "Entry already invoiced")
                data.setdefault("invoice_id", old["invoice_id"])
        if data.get("created") is None:
            data.pop("created", None)
        return self.store.save(coll, data)

    # -- sanity checks that need stored data or settings ---------------------------------------------
    def _check_vat_rates(self, *rates) -> None:
        allowed = {float(r) for r in ALLOWED_VAT_RATES}
        allowed.add(float(effective_settings(self.store.settings())["vat_rate"]))
        for rate in rates:
            if rate is not None and float(rate) not in allowed:
                shown = ", ".join(f"{r:g}" for r in sorted(allowed))
                raise ServiceError(422, f"VAT rate {float(rate):g} % is not allowed (allowed: {shown} %)")

    def _check_day_total(self, data: dict, old: dict | None) -> None:
        """One person cannot work more than 24 hours in a day."""
        total = data["minutes"]
        for e in self.store.list("entries"):
            same_day = e.get("user_id") == data.get("user_id") and e.get("date") == data.get("date")
            if same_day and e.get("id") != (old or {}).get("id"):
                total += int(e.get("minutes") or 0)
        if total > 1440:
            raise ServiceError(422, f"That would be {total / 60:.1f} hours on {data.get('date')}; a day has only 24")

    @staticmethod
    def _check_document_amounts(doc: dict) -> None:
        net = Decimal(0)
        for i, ln in enumerate(doc.get("lines") or [], 1):
            amount = dec(ln.get("amount"))
            if abs(amount) > Decimal(str(MAX_MONEY)):
                raise ServiceError(422, f"Line {i}: the amount is unrealistically large")
            net += amount
        if abs(net) > Decimal(str(MAX_MONEY)):
            raise ServiceError(422, "The document total is unrealistically large")

    def prepare_document(self, doc: dict, old: dict | None) -> dict:
        st = effective_settings(self.store.settings())
        if old:
            doc["number"] = old.get("number")  # numbers are only assigned via finalize
            for k in _IMPORT_FIELDS:  # owned by the importer: an edit must not lose the legal original
                if k in old:
                    doc[k] = old[k]
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
        if coll == "invoices" and old.get("number"):  # gapless numbering: a numbered invoice is cancelled, not deleted
            raise ServiceError(
                409, "A numbered invoice cannot be deleted; cancel it instead (keeps the numbers gapless)"
            )
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


_ENTRY_BILLING_FIELDS = ("rate", "invoice_id")
_MEMBER_PROJECT_KEYS = ("id", "name", "color", "status", "customer_id", "personal", "member_ids")


def _project_visible(project: dict, me: dict) -> bool:
    return not project.get("member_ids") or me["id"] in project["member_ids"]


def _member_project_view(project: dict) -> dict:
    """Members see which projects exist for them, never rates, budgets or internal notes."""
    return {k: project[k] for k in _MEMBER_PROJECT_KEYS if k in project}


def _member_task_view(task: dict) -> dict:
    return {k: v for k, v in task.items() if k != "rate"}


def _valid_id(rid: str) -> bool:
    return bool(ID_RE.match(rid))


def _today() -> str:
    from datetime import date

    return date.today().isoformat()
