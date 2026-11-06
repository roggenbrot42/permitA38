"""Generic CRUD with role-based permissions (the scopes come from `core/rights.py`)."""

from decimal import Decimal

from permita38.core import calc, rights
from permita38.core.errors import ServiceError
from permita38.core.money import dec
from permita38.core.repository import COLLECTIONS, ID_RE, Repository
from permita38.core.rights import Scope
from permita38.core.settings import effective_settings
from permita38.core.validation import ALLOWED_VAT_RATES, MAX_MONEY

from .auth import public_user
from .project_customer import (
    PROJECT_CUSTOMER_COLLECTIONS,
    check_billable_project,
    check_project_customer,
    project_kind,
)

# collections where a LIMITED scope means "own records only" (write) / "own records or assigned projects" (read)
OWN_COLLECTIONS = {"entries", "absences"}
VIEW_COLLECTIONS = {"projects", "tasks"}

#: fields that only exist in responses
RESPONSE_ONLY = ("sanity_warnings", "planning_warnings")


SCAN_PREFIX = "scan:"

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
        sc = _scope(me, coll, "read")
        rows = self.store.list(coll)
        if coll == "expenses":
            rows = [_expense_view(r) for r in rows]
        if sc == Scope.ALL:
            return rows
        if sc == Scope.NONE or coll not in OWN_COLLECTIONS | VIEW_COLLECTIONS:
            raise ServiceError(403, "Forbidden")
        if coll in OWN_COLLECTIONS:
            rows = [r for r in rows if r.get("user_id") == me["id"]]
        elif coll == "projects":
            rows = [_member_project_view(p) for p in rows]
        elif coll == "tasks":
            rows = [_member_task_view(t) for t in rows]
        return rows

    def get(self, me: dict, coll: str, rid: str) -> dict:
        self._check(coll)
        rec = self.store.get(coll, rid) if _valid_id(rid) else None
        if not rec:
            raise ServiceError(404, "Not found")
        if coll == "expenses":
            rec = _expense_view(rec)
        sc = _scope(me, coll, "read")
        if sc != Scope.ALL:
            if sc == Scope.NONE or coll not in OWN_COLLECTIONS | VIEW_COLLECTIONS:
                raise ServiceError(403, "Forbidden")
            if coll in OWN_COLLECTIONS and rec.get("user_id") != me["id"]:
                raise ServiceError(403, "Forbidden")
            if coll == "projects":
                return _member_project_view(rec)
            if coll == "tasks":
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

    def adopt_expense(self, me: dict, expense: dict, supplier_id: str) -> dict:
        """adopt-vendors: the expense gets the supplier through the normal write rules; its vendor text stays as typed."""
        data = {k: v for k, v in expense.items() if k not in RESPONSE_ONLY}
        data["supplier_id"] = supplier_id
        return self._write(me, "expenses", data, expense, keep_vendor=True)

    def create_scanned(self, me: dict, data: dict, scan_id: str) -> dict:
        """Book a scanned receipt (docs/specs/receipt-scan.md): the normal expense write rules, and the only way to set
        ``external_id = "scan:<scan id>"``."""
        data = {k: v for k, v in data.items() if k not in ("id", "created", "updated")}
        return self._write(me, "expenses", data, None, scan_id=scan_id)

    def _write(
        self, me: dict, coll: str, data: dict, old: dict | None, keep_vendor: bool = False, scan_id: str | None = None
    ) -> dict:
        for k in RESPONSE_ONLY:  # advisory warnings are computed per response, never stored
            data.pop(k, None)
        sc = _scope(me, coll, "write")
        full = sc == Scope.ALL
        if not full:
            if sc == Scope.NONE or coll not in OWN_COLLECTIONS:
                raise ServiceError(403, "Forbidden")
            if old and old.get("user_id") != me["id"]:
                raise ServiceError(403, "Forbidden")
            data["user_id"] = me["id"]
            if (
                coll == "entries"
            ):  # billing fields are for full access/system only: a limited user must not set rates or hide hours
                for k in _ENTRY_BILLING_FIELDS:
                    data.pop(k, None)
                    if old and k in old:
                        data[k] = old[k]
        elif coll == "entries" and not data.get("user_id"):
            # time always belongs to a person: without one it would count for the project and as unbilled work but
            # appear in nobody's timesheet, Today list or weekly hours (an update without user_id keeps the owner)
            data["user_id"] = (old or {}).get("user_id") or me["id"]
        if coll in PROJECT_CUSTOMER_COLLECTIONS:
            self._check_not_internal(data, old)
            check_project_customer(self.store, data, old)
        if coll in ("offers", "invoices"):
            check_billable_project(self.store, data, old)
            self._check_vat_rates(data.get("vat_rate"), *(ln.get("vat") for ln in data.get("lines") or []))
            data = self.prepare_document(data, old)
            self._check_document_amounts(data)
        elif coll == "expenses":
            # `scan:<id>` marks a booked receipt scan: a client can neither set nor drop it; other values are untouched
            if str(data.get("external_id") or "").startswith(SCAN_PREFIX):
                data.pop("external_id")
            if old and str(old.get("external_id") or "").startswith(SCAN_PREFIX):
                data["external_id"] = old["external_id"]
            if scan_id:
                data["external_id"] = SCAN_PREFIX + scan_id
            if data.get("trip_id"):  # a bill belongs to a trip the user may see (rights of the trips area)
                trip = self.store.get("trips", data["trip_id"])
                tsc = rights.scope_for(me, "trips", "read")
                visible = tsc == Scope.ALL or (
                    tsc == Scope.LIMITED and trip is not None and trip.get("user_id") == me["id"]
                )
                if trip is None or not visible:
                    raise ServiceError(422, "Unknown trip")
                if data.get("trip_item_id") and not any(
                    item.get("id") == data["trip_item_id"] for item in trip.get("items") or []
                ):
                    raise ServiceError(422, "Unknown trip item")
            elif data.get("trip_item_id"):  # an item belongs to a trip: it is only valid together with trip_id
                raise ServiceError(422, "Unknown trip item")
            self._link_supplier(data, keep_vendor)
            if data.get("lines"):
                self._check_vat_rates(*(ln.get("vat_rate") for ln in data["lines"]))
                data = calc.itemise_expense(data)  # server derives net/vat per line and overwrites the top level
            else:
                data.pop("lines", None)
                self._check_vat_rates(data.get("vat_rate"))
        elif coll == "contracts":
            data.pop("user_ids", None)  # contracts are not assigned to people; the project carries the team
        elif coll == "projects":
            data.pop("personal", None)  # replaced by ``kind``
            data.pop("member_ids", None)  # replaced by allocations; old clients may still send it
            self._resolve_project_kind(data, old)
            # the extension history is server-managed: never taken from the request
            data.pop("extensions", None)
            if old and old.get("extensions") is not None:
                data["extensions"] = old["extensions"]
        elif coll == "entries":
            data["minutes"] = int(round(float(data.get("minutes") or 0)))
            data.setdefault("billable", True)
            self._check_day_total(data, old)
            if old and old.get("invoice_id"):
                if not full:
                    raise ServiceError(403, "Entry already invoiced")
                data.setdefault("invoice_id", old["invoice_id"])
        if data.get("created") is None:
            data.pop("created", None)
        saved = self.store.save(coll, data)
        return _expense_view(saved) if coll == "expenses" else saved

    def _resolve_project_kind(self, data: dict, old: dict | None) -> None:
        """``kind`` is derived from the customer only on create; an update that omits it keeps the stored kind."""
        given = data.get("kind")
        customer = data.get("customer_id")
        if given is None:
            data.pop("kind", None)
            if old is None:
                data["kind"] = "customer" if customer else "internal"
                return
            data["kind"] = project_kind(old)
            if data["kind"] == "customer" and not customer and not old.get("customer_id"):
                return  # a legacy customer project without a customer stays editable
        if data["kind"] == "customer" and not customer:
            raise ServiceError(422, "a customer project needs a customer_id")
        if data["kind"] == "internal" and customer:
            raise ServiceError(422, "an internal project cannot have a customer_id")
        if old is not None and data["kind"] == "internal" and project_kind(old) != "internal":
            pid = old["id"]
            if any(r.get("project_id") == pid for coll in PROJECT_CUSTOMER_COLLECTIONS for r in self.store.list(coll)):
                raise ServiceError(409, "This project has offers, contracts or invoices; it stays a customer project.")

    def _check_not_internal(self, data: dict, old: dict | None) -> None:
        """Offers, contracts and invoices never sit on an internal project (an unchanged legacy link stays editable)."""
        pid = data["project_id"] if "project_id" in data else (old or {}).get("project_id")
        if not pid or (old is not None and old.get("project_id") == pid):
            return
        project = self.store.get("projects", pid)
        if project and project_kind(project) == "internal":
            raise ServiceError(409, "Internal projects cannot have offers, contracts or invoices")

    def _link_supplier(self, data: dict, keep_vendor: bool = False) -> None:
        """Expense links (docs/contracts/subcontractors.md): the supplier must exist and gives ``vendor`` (a snapshot
        of its name); an allocation needs a supplier and must be an external allocation of that supplier."""
        if not data.get("reverse_charge"):
            data.pop("reverse_charge", None)  # false is the default: not stored
        sid, aid = data.get("supplier_id"), data.get("allocation_id")
        if sid:
            supplier = self.store.get("suppliers", sid)
            if not supplier:
                raise ServiceError(422, "Unknown supplier")
            if not keep_vendor:
                data["vendor"] = supplier.get("name") or data.get("vendor")
        if aid:
            if not sid:
                raise ServiceError(422, "allocation_id needs a supplier")
            alloc = self.store.get("allocations", aid)
            if not alloc:
                raise ServiceError(422, "Unknown allocation")
            if alloc.get("supplier_id") != sid:
                raise ServiceError(422, "The allocation belongs to another supplier")

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
        for k in _IMPORT_FIELDS:  # owned by the importer: a client can neither forge nor drop them
            doc.pop(k, None)
        if old:
            doc["number"] = old.get("number")  # numbers are only assigned via finalize
            for k in _IMPORT_FIELDS:
                if k in old:
                    doc[k] = old[k]
            doc["date"] = doc.get("date") or old.get("date") or _today()  # a document never loses its date
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
        if coll == "projects" and any(a.get("project_id") == rid for a in self.store.list("allocations")):
            raise ServiceError(409, "This project has allocations; remove them first")
        sc = _scope(me, coll, "write")
        if sc != Scope.ALL:
            own = (
                sc == Scope.LIMITED
                and coll in OWN_COLLECTIONS
                and old.get("user_id") == me["id"]
                and not old.get("invoice_id")
            )
            if not own:
                raise ServiceError(403, "Forbidden")
        with self.store.transaction():
            if coll == "invoices":  # release billed time entries
                for e in self.store.list("entries"):
                    if e.get("invoice_id") == rid:
                        e.pop("invoice_id", None)
                        self.store.save("entries", e)
            if coll == "expenses":  # the scan row of a booked receipt goes with its expense (a re-upload works again)
                self.store.delete_where("receipt_scans", expense_id=rid)
            self.store.delete(coll, rid)

    def list_users(self, me: dict) -> list[dict]:
        users = self.store.list("users")
        if rights.scope_for(me, "team") == Scope.ALL:
            return [public_user(u) for u in users]
        return [{"id": u["id"], "name": u.get("name"), "role": rights.normalize_role(u.get("role"))} for u in users]


_ENTRY_BILLING_FIELDS = ("rate", "invoice_id")
_MEMBER_PROJECT_KEYS = ("id", "name", "color", "status", "customer_id", "kind")


def _scope(me: dict, coll: str, op: rights.Op) -> Scope:
    return rights.scope_for(me, rights.COLLECTION_AREA[coll], op)


def _expense_view(expense: dict) -> dict:
    """An itemised bill with mixed VAT rates has ``vat_rate`` null. Storage drops NULLs, so the key is restored here
    (the API always states the field for itemised bills)."""
    if expense.get("lines") and "vat_rate" not in expense:
        return {**expense, "vat_rate": None}
    return expense


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
