"""Generic CRUD with role-based permissions."""

from decimal import Decimal

from permita38.core import calc
from permita38.core.errors import ServiceError
from permita38.core.money import dec
from permita38.core.repository import COLLECTIONS, ID_RE, Repository
from permita38.core.settings import effective_settings
from permita38.core.validation import ALLOWED_VAT_RATES, MAX_MONEY

from .roles import can, can_any, require

# Which rights open which collection (any one of them). The API routers use the same table for their dependencies,
# the service checks again, so that a route that forgets its dependency still cannot leak data.
READ_RIGHTS: dict[str, tuple[str, ...]] = {
    "customers": ("customers.view",),
    "projects": ("projects.view_assigned",),
    "tasks": ("projects.view_assigned",),
    "entries": ("time.track_own", "time.view_all"),
    "contracts": ("planning.view",),
    "absences": ("absences.manage_own", "absences.view_all"),
    "offers": ("offers.view",),
    "invoices": ("invoices.view",),
    "expenses": ("expenses.view",),
    "templates": ("templates.manage", "offers.view", "invoices.view"),
}
WRITE_RIGHTS: dict[str, tuple[str, ...]] = {
    "customers": ("customers.manage",),
    "projects": ("projects.manage",),
    "tasks": ("tasks.manage",),
    "entries": ("time.track_own",),
    "contracts": ("planning.manage",),
    "absences": ("absences.manage_own",),
    "offers": ("offers.manage",),
    "invoices": ("invoices.manage",),
    "expenses": ("expenses.manage",),
    "templates": ("templates.manage",),
}
# collections that are managed through their own endpoints, not the generic record CRUD
SPECIAL = ("users", "roles")


class RecordService:
    def __init__(self, store: Repository):
        self.store = store

    def _check(self, coll: str):
        if coll not in COLLECTIONS or coll in SPECIAL:
            raise ServiceError(404, "Unknown collection")

    # -- what a user may see ----------------------------------------------------------------------------
    @staticmethod
    def _sees_all_of(me: dict, coll: str) -> bool:
        return can(
            me, {"entries": "time.view_all", "absences": "absences.view_all", "projects": "projects.view_all"}[coll]
        )

    def _visible_projects(self, me: dict) -> list[dict]:
        projects = self.store.list("projects")
        if can(me, "projects.view_all"):
            return projects
        return [p for p in projects if _project_assigned(p, me)]

    def _project_view(self, me: dict, project: dict) -> dict:
        return project if can(me, "projects.manage") else _limited_project(project)

    def _task_view(self, me: dict, task: dict) -> dict:
        return task if can_any(me, ("projects.manage", "tasks.manage")) else _limited_task(task)

    def list_records(self, me: dict, coll: str) -> list[dict]:
        self._check(coll)
        require(me, *READ_RIGHTS[coll])
        rows = self.store.list(coll)
        if coll in ("entries", "absences") and not self._sees_all_of(me, coll):
            rows = [r for r in rows if r.get("user_id") == me["id"]]
        elif coll == "projects":
            rows = [self._project_view(me, p) for p in self._visible_projects(me)]
        elif coll == "tasks":
            visible = {p["id"] for p in self._visible_projects(me)}
            rows = [self._task_view(me, t) for t in rows if t.get("project_id") in visible]
        return rows

    def get(self, me: dict, coll: str, rid: str) -> dict:
        self._check(coll)
        require(me, *READ_RIGHTS[coll])
        rec = self.store.get(coll, rid) if _valid_id(rid) else None
        if not rec:
            raise ServiceError(404, "Not found")
        if coll in ("entries", "absences") and rec.get("user_id") != me["id"] and not self._sees_all_of(me, coll):
            raise ServiceError(403, "Forbidden")
        if coll == "projects":
            if not (can(me, "projects.view_all") or _project_assigned(rec, me)):
                raise ServiceError(404, "Not found")  # do not reveal that it exists
            return self._project_view(me, rec)
        if coll == "tasks":
            pid = rec.get("project_id")
            project = self.store.get("projects", pid) if isinstance(pid, str) and _valid_id(pid) else None
            if not project or not (can(me, "projects.view_all") or _project_assigned(project, me)):
                raise ServiceError(404, "Not found")
            return self._task_view(me, rec)
        return rec

    def create(self, me: dict, coll: str, data: dict) -> dict:
        self._check(coll)
        data = {k: v for k, v in data.items() if k not in ("id", "created", "updated")}
        return self._write(me, coll, data, None)

    def update(self, me: dict, coll: str, rid: str, data: dict) -> dict:
        self._check(coll)
        require(me, *WRITE_RIGHTS[coll])
        old = self.store.get(coll, rid) if _valid_id(rid) else None
        if not old:
            raise ServiceError(404, "Not found")
        return self._write(me, coll, {**data, "id": rid, "created": old.get("created")}, old)

    def _write(self, me: dict, coll: str, data: dict, old: dict | None) -> dict:
        require(me, *WRITE_RIGHTS[coll])
        everyone = (coll == "entries" and can(me, "time.edit_all")) or (
            coll == "absences" and can(me, "absences.manage_all")
        )
        if coll in ("entries", "absences"):
            if not everyone:
                if old and old.get("user_id") != me["id"]:
                    raise ServiceError(403, "Forbidden")
                data["user_id"] = me["id"]
            else:
                data.setdefault("user_id", (old or {}).get("user_id") or me["id"])
            if coll == "entries" and not everyone:
                # billing fields are not for the person who did the work: a rate overrides every other rate when
                # the time is billed and `invoice_id` hides hours from invoicing
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
                if not everyone:
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
        require(me, *WRITE_RIGHTS[coll])
        old = self.store.get(coll, rid) if _valid_id(rid) else None
        if not old:
            return
        if coll in ("entries", "absences"):
            everyone = can(me, "time.edit_all" if coll == "entries" else "absences.manage_all")
            if not everyone and (old.get("user_id") != me["id"] or old.get("invoice_id")):
                raise ServiceError(403, "Forbidden")
        with self.store.transaction():
            if coll == "invoices":  # release billed time entries
                for e in self.store.list("entries"):
                    if e.get("invoice_id") == rid:
                        e.pop("invoice_id", None)
                        self.store.save("entries", e)
            self.store.delete(coll, rid)

    def list_users(self, me: dict, public) -> list[dict]:
        """People with their working hours and roles for those who may see the team; id and name for everybody else."""
        users = self.store.list("users")
        if can(me, "team.view"):
            return [public(u) for u in users]
        return [{"id": u["id"], "name": u.get("name")} for u in users]


_ENTRY_BILLING_FIELDS = ("rate", "invoice_id")
_LIMITED_PROJECT_KEYS = ("id", "name", "color", "status", "customer_id", "personal", "member_ids")


def _project_assigned(project: dict, me: dict) -> bool:
    """Open to everybody (no team set) or the user is on the team."""
    return not project.get("member_ids") or me["id"] in project["member_ids"]


def _limited_project(project: dict) -> dict:
    """Without 'Manage projects' people see which projects exist for them, never rates, budgets or internal notes."""
    return {k: project[k] for k in _LIMITED_PROJECT_KEYS if k in project}


def _limited_task(task: dict) -> dict:
    return {k: v for k, v in task.items() if k != "rate"}


def _valid_id(rid: str) -> bool:
    return bool(ID_RE.match(rid))


def _today() -> str:
    from datetime import date

    return date.today().isoformat()
