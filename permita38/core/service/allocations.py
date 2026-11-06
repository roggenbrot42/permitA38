"""Assignments (table ``allocations``): hours of one person or subcontractor given to one project for a period
(docs/specs/project-centric-planning.md, P5). Exactly one of ``user_id`` / ``supplier_id`` and exactly one of
``hours_per_week`` / ``total_hours``. Figures come from ``planner/spread.py``; warnings on save from
``planner/supply.py``. Over-allocation is never an error, it is a warning that comes back with the saved row.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from permita38.core import calc
from permita38.core.errors import ServiceError
from permita38.core.money import ZERO, dec, q2
from permita38.core.repository import Repository
from permita38.core.service.planner import load as planner_load
from permita38.core.service.planner import supply
from permita38.core.service.planner.model import Assignment, Holiday, Owner
from permita38.core.service.planner.spread import planned_hours as spread_hours
from permita38.core.service.planner.spread import planned_net as spread_net
from permita38.core.service.planner.spread import spread
from permita38.core.settings import effective_settings

from . import external_capacity as ext
from . import planning_warnings as pw

CLOSED_PROJECT = planner_load.CLOSED_PROJECT
MAX_PERIOD_DAYS = planner_load.MAX_ALLOCATION_DAYS
MAX_HOURS = 80.0
SAVE_WARNING_WEEKS = 10
CAPACITY_KIND = "person-capacity"
Rows = list[dict]  # alias: a method named `list` shadows the builtin inside the class body


def _is_external(row: dict) -> bool:
    return bool(row.get("supplier_id")) or not row.get("user_id")


def _active(row: dict) -> bool:
    return row.get("status", "active") == "active"


def _h(x: float) -> str:
    return pw._fmt(x)


def _eur(x: Decimal) -> str:
    return f"{x:,.2f}"


class _Spreader:
    """Owner and assignment of a stored row for ``planner/spread.py``, built with the rules of ``planner/load.py``."""

    def __init__(self, store: Repository, users: dict, suppliers: dict):
        self.store = store
        self.users = users
        self.suppliers = suppliers
        self.holiday: Holiday = planner_load._holiday_fn(effective_settings(store.settings()).get("state") or "")
        self._absences: dict[str, dict] | None = None
        self._owners: dict[str, Owner | None] = {}

    def _days_off(self, uid: str) -> dict:
        if self._absences is None:
            self._absences = {}
            for a in self.store.list("absences"):
                s, e = calc.d(a.get("start")), calc.d(a.get("end") or a.get("start"))
                if not a.get("user_id") or not s or not e or e < s:
                    continue
                kind = planner_load._day_off_kind(a.get("kind"))
                days = self._absences.setdefault(a["user_id"], {})
                for day in calc.daterange(s, min(e, s + timedelta(days=planner_load.MAX_ABSENCE_DAYS))):
                    days[day] = kind
        return self._absences.get(uid, {})

    def owner(self, row: dict) -> Owner | None:
        oid = row.get("supplier_id") if _is_external(row) else row.get("user_id")
        if not oid:
            return None
        if oid not in self._owners:
            self._owners[oid] = self._build(oid, bool(row.get("supplier_id")))
        return self._owners[oid]

    def _build(self, oid: str, supplier: bool) -> Owner | None:
        if supplier:
            s = self.suppliers.get(oid)
            if not s:
                return None
            off: dict = {}
            for w in s.get("unavailable") or []:
                if not isinstance(w, dict):
                    continue
                a, b = calc.d(w.get("from")), calc.d(w.get("to"))
                if a and b and b >= a:
                    for day in calc.daterange(a, min(b, a + timedelta(days=planner_load.MAX_ABSENCE_DAYS))):
                        off[day] = "unavailable"
            return Owner(oid, s.get("name") or oid, "subcontractor", 0.0, planner_load._workdays(s), days_off=off)
        u = self.users.get(oid)
        if not u:
            return None
        return Owner(
            oid,
            u.get("name") or u.get("username") or oid,
            "boss" if u.get("role") == "boss" else "employee",
            calc.fnum(u.get("weekly_hours")),
            planner_load._workdays(u),
            days_off=dict(self._days_off(oid)),
        )

    def figures(self, row: dict) -> tuple[float, Decimal | None]:
        """(planned hours over the whole period, planned net for a subcontractor row or None)."""
        owner = self.owner(row)
        hours = planner_load._hours(row)
        s, e = calc.d(row.get("start")), calc.d(row.get("end"))
        external = _is_external(row)
        if owner is None or hours is None or not s or not e:
            return 0.0, ZERO if external else None
        asg = Assignment(
            id=row.get("id") or "",
            owner_id=owner.id,
            project_id=row.get("project_id") or "",
            project_kind="customer",
            start=s,
            end=e,
            hours_per_week=hours[0],
            total_hours=hours[1],
        )
        supplier = self.suppliers.get(owner.id) or {}
        rate = planner_load._rate(row.get("rate"), supplier.get("rate"))
        cut = None if _active(row) else calc.d(row.get("cancelled_from"))
        if cut is not None:
            # cancelled: the whole period is spread, the days before the cut keep their hours (and cost)
            exact = sum(h for day, h in spread(owner, asg, self.holiday, None).items() if day < cut)
            return round(exact, 2), (q2(dec(round(exact, 6)) * rate) if external else None)
        planned = spread_hours(owner, asg, self.holiday)
        return planned, (spread_net(owner, asg, self.holiday, rate) if external else None)


class AllocationService:
    def __init__(self, store: Repository):
        self.store = store

    def _spreader(self, users: dict | None = None, suppliers: dict | None = None) -> _Spreader:
        if users is None:
            users = {u["id"]: u for u in self.store.list("users")}
        if suppliers is None:
            suppliers = {s["id"]: s for s in self.store.list("suppliers")}
        return _Spreader(self.store, users, suppliers)

    # ------------------------------------------------------------------ presentation
    def invoiced(self) -> dict[str, Decimal]:
        """Net of the expenses linked to each allocation."""
        out: dict[str, Decimal] = {}
        for e in self.store.list("expenses"):
            aid = e.get("allocation_id")
            if aid:
                out[aid] = out.get(aid, ZERO) + dec(e.get("net"))
        return out

    def external_figures(
        self, row: dict, supplier: dict | None, cap: calc.Capacity | None, invoiced: Decimal, spreader=None
    ) -> dict:
        sp = spreader or self._spreader(suppliers={supplier["id"]: supplier} if supplier else {})
        hours, net = sp.figures(row)
        planned = net if net is not None else ZERO
        return dict(
            planned_hours=hours,
            planned_net=float(planned),
            invoiced_net=float(q2(invoiced)),
            open_net=float(max(ZERO, planned - q2(invoiced))),
        )

    def present_external(
        self,
        row: dict,
        cap: calc.Capacity | None,
        suppliers: dict,
        projects: dict,
        offers: dict,
        invoiced: dict,
        days=False,
        spreader=None,
    ) -> dict:
        supplier = suppliers.get(row.get("supplier_id") or "")
        project = projects.get(row.get("project_id") or "")
        out = {k: v for k, v in row.items() if v is not None}
        out["kind"] = "external"
        out["status"] = row.get("status") or "active"
        out["project_name"] = (project or {}).get("name")
        out["supplier_name"] = (supplier or {}).get("name")
        offer = offers.get(row.get("incoming_offer_id") or "")
        if offer and offer.get("reference"):
            out["offer_reference"] = offer["reference"]
        out.update(self.external_figures(row, supplier, cap, invoiced.get(row["id"], ZERO), spreader))
        if days:
            out["days"] = [
                {"date": x.isoformat(), "hours": round(h, 6)}
                for x, h in sorted(ext.allocation_days(supplier or {}, row, cap or self._context()[0]).items())
            ]
        return out

    def _external_context(self) -> tuple[dict, dict, dict]:
        suppliers = {s["id"]: s for s in self.store.list("suppliers")}
        offers = {o["id"]: o for o in self.store.list("incoming_offers")}
        return suppliers, offers, self.invoiced()

    def present_one(self, row: dict, days: bool = False) -> dict:
        """One stored row as the API shows it (employee or external)."""
        cap, users, projects = self._context()
        if _is_external(row):
            suppliers, offers, invoiced = self._external_context()
            return self.present_external(row, cap, suppliers, projects, offers, invoiced, days)
        return self._present(row, users, projects)

    def _present(self, row: dict, users: dict, projects: dict, spreader: _Spreader | None = None) -> dict:
        user = users.get(row.get("user_id") or "")
        project = projects.get(row.get("project_id") or "")
        out = {k: v for k, v in row.items() if v is not None}
        out["kind"] = "employee"
        out["status"] = row.get("status") or "active"
        out["project_name"] = (project or {}).get("name")  # null when the project is gone
        out["user_name"] = (user.get("name") or user.get("username") or "") if user else None
        out["planned_hours"] = (spreader or self._spreader(users)).figures(row)[0]
        return out

    def _context(self) -> tuple[calc.Capacity, dict, dict]:
        ctx = calc.context(self.store)
        return calc.Capacity(self.store, ctx), ctx["users"], ctx["projects"]

    # ------------------------------------------------------------------ reads
    def list(
        self,
        project_id: str | None = None,
        user_id: str | None = None,
        supplier_id: str | None = None,
        status: str | None = None,
        include_external: bool = False,
    ) -> Rows:
        rows = []
        for r in self.store.list("allocations"):
            if _is_external(r) and not include_external:
                continue
            if project_id and r.get("project_id") != project_id:
                continue
            if user_id and r.get("user_id") != user_id:
                continue
            if supplier_id and r.get("supplier_id") != supplier_id:
                continue
            if status and (r.get("status") or "active") != status:
                continue
            rows.append(r)
        rows.sort(key=lambda r: str(r.get("id") or ""))
        rows.sort(key=lambda r: str(r.get("start") or ""), reverse=True)
        cap, users, projects = self._context()
        suppliers, offers, invoiced = self._external_context() if any(_is_external(r) for r in rows) else ({}, {}, {})
        sp = self._spreader(users, suppliers or None)
        return [
            self.present_external(r, cap, suppliers, projects, offers, invoiced, spreader=sp)
            if _is_external(r)
            else self._present(r, users, projects, sp)
            for r in rows
        ]

    def kind_of(self, rid: str) -> str:
        """``employee`` or ``subcontractor`` for the stored row (404 when unknown); decides the right needed."""
        old = self.store.get("allocations", rid)
        if not old:
            raise ServiceError(404, "Not found")
        return "subcontractor" if _is_external(old) else "employee"

    # ------------------------------------------------------------------ writes
    def _check(self, body: dict) -> None:
        supplier: dict = {}
        if body.get("supplier_id"):
            supplier = self.store.get("suppliers", body["supplier_id"]) or {}
            if not supplier:
                raise ServiceError(404, "Supplier not found")
            owner = supplier
        else:
            owner = self.store.get("users", body["user_id"]) or {}
            if not owner:
                raise ServiceError(404, "User not found")
        project = self.store.get("projects", body["project_id"])
        if not project:
            raise ServiceError(404, "Project not found")
        if body["end"] < body["start"]:
            raise ServiceError(409, "The end date is before the start date")
        if (date.fromisoformat(body["end"]) - date.fromisoformat(body["start"])).days > MAX_PERIOD_DAYS:
            raise ServiceError(422, f"The period may be at most {MAX_PERIOD_DAYS} days")
        if (project.get("status") or "active") in CLOSED_PROJECT:
            raise ServiceError(409, "The project is closed")
        if not owner.get("active", True):
            raise ServiceError(409, "The supplier is not active" if supplier else "The user is not active")
        if supplier and supplier.get("kind") != CAPACITY_KIND:
            raise ServiceError(409, "The supplier does not provide person capacity")

    @staticmethod
    def _fields(body: dict) -> dict:
        keys = ("project_id", "start", "end", "user_id", "supplier_id", "hours_per_week", "total_hours", "rate")
        return {k: body[k] for k in keys if body.get(k) is not None}

    def _answer(self, saved: dict) -> dict:
        out = self.present_one(saved)
        out["planning_warnings"] = self._save_warnings(saved, out)
        return out

    def create(self, body: dict) -> dict:
        self._check(body)
        with self.store.transaction():
            saved = self.store.save("allocations", {**self._fields(body), "status": "active"})
        return self._answer(saved)

    def update(self, rid: str, body: dict) -> dict:
        old = self.store.get("allocations", rid)
        if not old:
            raise ServiceError(404, "Not found")
        if not _active(old):
            raise ServiceError(409, "This allocation cannot be changed here")
        if _is_external(old) != bool(body.get("supplier_id")):
            raise ServiceError(409, "An allocation cannot change between employee and subcontractor")
        self._check(body)
        with self.store.transaction():
            drop = ("user_id", "supplier_id", "hours_per_week", "total_hours", "rate")
            merged = {k: v for k, v in old.items() if k not in drop}
            saved = self.store.save("allocations", {**merged, **self._fields(body)})
        return self._answer(saved)

    def delete(self, rid: str) -> None:
        old = self.store.get("allocations", rid)
        if not old:
            raise ServiceError(404, "Not found")
        if not _active(old):
            raise ServiceError(409, "This allocation cannot be changed here")
        if old.get("incoming_offer_id"):
            raise ServiceError(409, "An allocation from an incoming offer cannot be deleted; cancel it instead")
        self.store.delete("allocations", rid)

    # ------------------------------------------------------------------ warnings on save
    def _save_warnings(self, saved: dict, shown: dict) -> Rows:
        out: Rows = []
        oid = saved.get("user_id") or saved.get("supplier_id")
        start, end = calc.d(saved.get("start")), calc.d(saved.get("end"))
        if not oid or not start or not end:
            return out
        today = date.today()
        snap = planner_load.load(self.store, today)
        owners = [o for o in snap.owners if o.id == oid]
        if owners and owners[0].kind != "subcontractor":
            mine = [a for a in snap.assignments if a.owner_id == oid]
            # the whole saved period counts, also the days up to today
            first = min(today, start - timedelta(days=1))
            found = supply.warnings(owners, mine, snap.holiday, first, start, end, snap.names)
            out += [w for w in found if w["code"] == "assignment.over_capacity"][:SAVE_WARNING_WEEKS]
        out += self._overlap(saved, oid, snap.names.get(saved.get("project_id") or "") or "", owners)
        out += self._differs_from_offer(saved, shown)
        return out

    def _overlap(self, saved: dict, oid: str, project_name: str, owners: Sequence[Owner]) -> Rows:
        key = "supplier_id" if saved.get("supplier_id") else "user_id"
        mine = [
            r
            for r in self.store.list("allocations")
            if _active(r) and r.get(key) == oid and r.get("project_id") == saved["project_id"]
        ]
        who = owners[0].name if owners else ""
        out: Rows = []
        for ws in pw.week_starts(date.fromisoformat(saved["start"]), date.fromisoformat(saved["end"])):
            ids = sorted(r["id"] for r in mine if pw.overlaps_week(r, ws))
            if len(ids) >= 2:
                out.append(
                    pw.warning(
                        "assignment.overlap",
                        "info",
                        None,
                        f"Week of {ws.isoformat()}: {len(ids)} assignments of {who or 'the same person'} to "
                        f"{project_name or 'the same project'} overlap; their hours add up.",
                        dict(owner_id=oid, project_id=saved["project_id"], week=ws.isoformat(), allocation_ids=ids),
                        confirmable=False,
                    )
                )
                if len(out) >= SAVE_WARNING_WEEKS:
                    break
        return out

    def _differs_from_offer(self, saved: dict, shown: dict) -> Rows:
        """E6: a subcontractor row linked to an incoming offer whose planned net is not the offer's net."""
        offer_id = saved.get("incoming_offer_id")
        offer = self.store.get("incoming_offers", offer_id) if offer_id and saved.get("supplier_id") else None
        if not offer:
            return []
        planned_h = dec(shown.get("planned_hours"))
        planned_n = q2(dec(shown.get("planned_net")))
        offer_h = dec(offer.get("hours"))
        offer_n = q2(dec(offer.get("net_total")))
        if planned_n == offer_n:
            return []
        dh, dn = planned_h - offer_h, planned_n - offer_n
        more = "more" if dn > 0 else "less"
        dh_text = f"{_h(float(abs(dh)))} h"
        dn_text = f"{_eur(abs(dn))} €"
        if dh == 0 or (dh > 0) == (dn > 0):
            diff = f"{dh_text} / {dn_text} {more}"
        else:
            diff = f"{dh_text} {'more' if dh > 0 else 'less'} / {dn_text} {more}"
        message = (
            f"Planned {_h(float(planned_h))} h / {_eur(planned_n)} € differ from the offer's "
            f"{_h(float(offer_h))} h / {_eur(offer_n)} € ({diff})."
        )
        details = dict(
            allocation_id=saved["id"],
            incoming_offer_id=offer_id,
            planned_hours=float(planned_h),
            planned_net=float(planned_n),
            offer_hours=float(offer_h),
            offer_net=float(offer_n),
        )
        return [pw.warning("assignment.differs_from_offer", "info", None, message, details, confirmable=False)]

    # ------------------------------------------------------------------ what-if rows written by plan/apply
    def apply_change(self, ch: dict) -> Rows:
        """Write one ``AllocationChange`` (create, update or delete); returns the warnings of the row written."""
        rid = ch.get("id")
        keys = ("user_id", "project_id", "start", "end")
        if rid:
            old = self.store.get("allocations", rid)
            if not old:
                raise ServiceError(404, "Allocation not found")
            if _is_external(old) or not _active(old):
                raise ServiceError(409, "This allocation cannot be changed here")
            if ch.get("delete"):
                self.store.delete("allocations", rid)
                return []
            body = {k: ch[k] if ch.get(k) is not None else old.get(k) for k in keys}
            hours_from = ch if ch.get("hours_per_week") is not None or ch.get("total_hours") is not None else old
        else:
            body = {k: ch[k] for k in keys}
            hours_from = ch
        body["hours_per_week"] = hours_from.get("hours_per_week")
        body["total_hours"] = hours_from.get("total_hours")
        if body["hours_per_week"] is not None:
            body["total_hours"] = None
        return (self.update(rid, body) if rid else self.create(body))["planning_warnings"]
