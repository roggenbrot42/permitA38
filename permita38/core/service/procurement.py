"""Incoming offers of suppliers, their status machine, the external allocations they create, the commitments and the
subcontractor hints (docs/contracts/subcontractors.md, "Incoming offers", "Finance: commitments", "Warning codes").

Money is computed with ``core/money.py`` only. Planner engine functions are not changed here: the external hours of an
allocation come from ``external_capacity.allocation_days`` (the one spreading function).
"""

from __future__ import annotations

import builtins
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from permita38.core import calc, rights
from permita38.core.errors import ServiceError
from permita38.core.money import ZERO, dec, q2
from permita38.core.repository import ID_RE, Repository
from permita38.core.rights import Scope

from . import external_capacity as ext
from . import planning_warnings as pw
from .suppliers import CAPACITY_KIND, default_vat_mode

NO_VAT_MODES = frozenset({"eu_reverse_charge", "third_country_reverse_charge", "small_business"})
OPEN = ("received", "under_review")
STORED = ("received", "under_review", "accepted", "rejected")
# action -> (allowed stored statuses, new status); accept and unaccept have their own code
TRANSITIONS = {
    "review": (("received",), "under_review"),
    "reject": (("received", "under_review"), "rejected"),
    "reopen": (("rejected",), "under_review"),
}
# fields of an accepted offer that a PUT may not change (the editable ones are notes, attachment, decision_note)
LOCKED_WHEN_ACCEPTED = (
    "supplier_id",
    "reference",
    "title",
    "received_on",
    "valid_until",
    "lines",
    "vat_rate",
    "vat_mode",
    "plan",
    "project_id",
    "contract_id",
)
LOCKED_MESSAGE = "An accepted offer can only change notes, attachment and decision note"
MAX_WARNED_WEEKS = 10
RATE_QUANT = Decimal("0.0001")
LEGAL_HINT = "The customer share is an indicator only; check the bookkeeping rules (verify)."


def _h(x: float) -> str:
    return ("%.2f" % x).rstrip("0").rstrip(".")


def _minus_12_months(today: date) -> date:
    try:
        return today.replace(year=today.year - 1)
    except ValueError:  # 29 February
        return today.replace(year=today.year - 1, day=28)


def _rate_of(net: Decimal, hours: Decimal) -> float:
    """Net of the hour lines per hour, rounded half-up to 4 decimals (0 without hours)."""
    if hours <= 0:
        return 0.0
    return float((net / hours).quantize(RATE_QUANT, rounding=ROUND_HALF_UP))


class ProcurementService:
    def __init__(self, store: Repository):
        self.store = store

    # ------------------------------------------------------------------ helpers
    def _cap(self) -> calc.Capacity:
        return calc.Capacity(self.store, calc.context(self.store))

    def _setting(self, key: str, default: float) -> float:
        value = self.store.settings().get(key)
        return calc.fnum(value, default) if value is not None else default

    def _supplier(self, rid: str | None) -> dict | None:
        return self.store.get("suppliers", rid) if rid and ID_RE.match(rid) else None

    def _offer(self, rid: str) -> dict:
        rec = self.store.get("incoming_offers", rid) if ID_RE.match(rid) else None
        if not rec:
            raise ServiceError(404, "Not found")
        return rec

    @staticmethod
    def effective_status(rec: dict, today: date) -> str:
        valid = calc.d(rec.get("valid_until"))
        if rec.get("status") in OPEN and valid and valid < today:
            return "expired"
        return rec.get("status") or "received"

    def _present(self, rec: dict, today: date) -> dict:
        supplier = self._supplier(rec.get("supplier_id"))
        project = self.store.get("projects", rec["project_id"]) if rec.get("project_id") else None
        out = {k: v for k, v in rec.items() if v is not None}
        out["supplier_name"] = (supplier or {}).get("name")
        out["project_name"] = (project or {}).get("name")
        out["effective_status"] = self.effective_status(rec, today)
        out.setdefault("status", "received")
        out.setdefault("lines", [])
        return out

    # ------------------------------------------------------------------ computed fields
    @staticmethod
    def computed(data: dict, supplier: dict, project: dict | None) -> dict:
        """Server-side figures of an offer: hours, net, VAT, gross, the effective VAT mode and rate, the customer."""
        lines = data.get("lines") or []
        net = sum((q2(dec(ln["quantity"]) * dec(ln["unit_price"])) for ln in lines), ZERO)
        hours = q2(sum((dec(ln["quantity"]) for ln in lines if ln.get("unit") == "h"), ZERO))
        mode = data.get("vat_mode") or supplier.get("vat_mode") or default_vat_mode(supplier.get("country"))
        if mode in NO_VAT_MODES:
            rate, vat = Decimal(0), ZERO
        else:
            rate_value = data.get("vat_rate")
            if rate_value is None:
                rate_value = 19 if mode == "domestic" else 0
            rate = dec(rate_value)
            vat = q2(net * rate / 100)
        return dict(
            hours=float(hours),
            net_total=float(net),
            vat_rate=float(rate),
            vat=float(vat),
            gross_total=float(net + vat),
            vat_mode=mode,
            customer_id=(project or {}).get("customer_id") or None,
        )

    def _checks(self, data: dict) -> tuple[dict, dict | None]:
        supplier = self._supplier(data.get("supplier_id"))
        if not supplier:
            raise ServiceError(422, "Unknown supplier")
        project = None
        if data.get("project_id"):
            project = self.store.get("projects", data["project_id"])
            if not project:
                raise ServiceError(422, "Unknown project")
        if data.get("contract_id"):
            contract = self.store.get("contracts", data["contract_id"])
            if not contract or contract.get("project_id") != data.get("project_id"):
                raise ServiceError(422, "The contract is not a contract of this project")
        return supplier, project

    def _warnings(self, rec: dict, supplier: dict) -> builtins.list[dict]:
        out: builtins.list[dict] = []
        plan = rec.get("plan")
        if plan:
            planned = pw.plan_hours(plan)
            if planned is not None and abs(planned - rec.get("hours", 0.0)) > 0.01:
                out.append(
                    pw.warning(
                        "incoming_offer.hours_mismatch",
                        "warning",
                        "plan",
                        f"The plan of {_h(planned)} h does not match the {_h(rec.get('hours', 0.0))} h of the offer.",
                        {
                            "offer_hours": rec.get("hours", 0.0),
                            "plan_hours": round(planned, 2),
                            "difference": round(abs(planned - rec.get("hours", 0.0)), 2),
                        },
                        confirmable=False,
                    )
                )
        ref = str(rec.get("reference") or "").strip().casefold()
        if ref:
            for o in self.store.find("incoming_offers", supplier_id=rec.get("supplier_id")):
                if o.get("id") != rec.get("id") and str(o.get("reference") or "").strip().casefold() == ref:
                    out.append(
                        pw.warning(
                            "incoming_offer.duplicate_reference",
                            "warning",
                            "reference",
                            f"The reference {rec.get('reference')} is already used by another offer of this supplier.",
                            {"other_id": o.get("id")},
                            confirmable=False,
                        )
                    )
                    break
        return out

    # ------------------------------------------------------------------ offer CRUD
    def list(self, supplier_id: str | None, status: str | None, project_id: str | None, today: date | None = None):
        today = today or date.today()
        rows = []
        for r in self.store.list("incoming_offers"):
            if supplier_id and r.get("supplier_id") != supplier_id:
                continue
            if project_id and r.get("project_id") != project_id:
                continue
            if status:
                if status == "expired":
                    if self.effective_status(r, today) != "expired":
                        continue
                elif r.get("status") != status:
                    continue
            rows.append(r)
        rows.sort(key=lambda r: str(r.get("id") or ""))
        rows.sort(key=lambda r: str(r.get("received_on") or ""), reverse=True)
        return [self._present(r, today) for r in rows]

    def get(self, rid: str, today: date | None = None) -> dict:
        return self._present(self._offer(rid), today or date.today())

    def create(self, data: dict, today: date | None = None) -> dict:
        today = today or date.today()
        supplier, project = self._checks(data)
        if not supplier.get("active", True):
            raise ServiceError(409, "The supplier is not active")
        rec = {**data, **self.computed(data, supplier, project), "status": "received"}
        rec = {k: v for k, v in rec.items() if k not in ("planning_warnings", "effective_status")}
        with self.store.transaction():
            saved = self.store.save("incoming_offers", rec)
        out = self._present(saved, today)
        out["planning_warnings"] = self._warnings(saved, supplier)
        return out

    def update(self, rid: str, data: dict, today: date | None = None) -> dict:
        today = today or date.today()
        old = self._offer(rid)
        supplier, project = self._checks(data)
        new = self.computed(data, supplier, project)
        if old.get("status") == "accepted":
            for k in LOCKED_WHEN_ACCEPTED:
                value = new[k] if k in ("vat_rate", "vat_mode") else data.get(k)  # server figures for VAT
                if (value or None) != (old.get(k) or None):
                    raise ServiceError(409, LOCKED_MESSAGE)
            keep = {k: old.get(k) for k in ("status", "status_changed_at", "decided_by", "allocation_id")}
            rec = {
                **old,
                **{k: data.get(k) for k in ("notes", "attachment", "decision_note")},
                **keep,
                "customer_id": new["customer_id"],
            }
        else:
            keep = {k: old.get(k) for k in ("status", "status_changed_at", "decided_by", "allocation_id")}
            rec = {**data, **new, **keep, "id": rid, "created": old.get("created")}
        rec = {k: v for k, v in rec.items() if k not in ("planning_warnings", "effective_status", "supplier_name")}
        with self.store.transaction():
            saved = self.store.save("incoming_offers", {**rec, "id": rid})
        out = self._present(saved, today)
        out["planning_warnings"] = self._warnings(saved, supplier)
        return out

    def delete(self, rid: str) -> None:
        rec = self._offer(rid)
        if rec.get("status") == "accepted":
            raise ServiceError(409, "An accepted offer cannot be deleted; unaccept it first")
        self.store.delete("incoming_offers", rid)

    # ------------------------------------------------------------------ status actions
    def _stamp(self, rec: dict, status: str, me: dict, today: date, **extra: Any) -> dict:
        return {
            **rec,
            "status": status,
            "status_changed_at": today.isoformat(),
            "decided_by": me.get("id"),
            **extra,
        }

    def transition(self, rid: str, action: str, me: dict, body: dict | None = None, today: date | None = None) -> dict:
        today = today or date.today()
        rec = self._offer(rid)
        allowed, target = TRANSITIONS[action]
        if rec.get("status") not in allowed:
            raise ServiceError(409, f"This action is not possible in status {rec.get('status')}")
        extra = {}
        if action == "reject" and body and body.get("reason"):
            extra["decision_note"] = body["reason"]
        with self.store.transaction():
            saved = self.store.save("incoming_offers", self._stamp(rec, target, me, today, **extra))
        return self._present(saved, today)

    def accept(self, rid: str, body: dict, me: dict, today: date | None = None) -> dict:
        today = today or date.today()
        rec = self._offer(rid)
        if rec.get("status") not in OPEN:
            raise ServiceError(409, f"This action is not possible in status {rec.get('status')}")
        if self.effective_status(rec, today) == "expired" and not body.get("confirm_expired"):
            raise ServiceError(409, f"The offer expired on {rec.get('valid_until')}; confirm to accept it anyway")
        supplier = self._supplier(rec.get("supplier_id"))
        if not supplier:
            raise ServiceError(422, "Unknown supplier")
        if not supplier.get("active", True):
            raise ServiceError(409, "The supplier is not active")
        project_id = body.get("project_id") or rec.get("project_id")
        contract_id = body.get("contract_id") or rec.get("contract_id")
        decision = body.get("decision_note") if body.get("decision_note") is not None else rec.get("decision_note")
        if supplier.get("kind") != CAPACITY_KIND:
            saved = self.store.save(
                "incoming_offers",
                self._stamp(
                    rec, "accepted", me, today, decision_note=decision, project_id=project_id, contract_id=contract_id
                ),
            )
            return {
                "offer": self._present(saved, today),
                "allocation": None,
                "commitment": {"kind": "flat", "planned_net": rec.get("net_total"), "open_net": rec.get("net_total")},
                "planning_warnings": [],
            }
        plan = rec.get("plan")
        if not plan:
            raise ServiceError(422, "A capacity offer needs a plan")
        project = self.store.get("projects", project_id) if project_id else None
        if not project_id:
            raise ServiceError(422, "A capacity offer needs a project")
        if not project:
            raise ServiceError(422, "Unknown project")
        if (project.get("status") or "active") in ("done", "archived"):
            raise ServiceError(409, "The project is closed")
        if not any(ln.get("unit") == "h" for ln in rec.get("lines") or []):
            raise ServiceError(422, "A capacity offer needs an hour line")
        if contract_id:
            contract = self.store.get("contracts", contract_id)
            if not contract or contract.get("project_id") != project_id:
                raise ServiceError(422, "The contract is not a contract of this project")
        row = {
            "supplier_id": supplier["id"],
            "incoming_offer_id": rec["id"],
            "project_id": project_id,
            "contract_id": contract_id or None,
            "customer_id": project.get("customer_id") or None,
            "start": plan["start"],
            "end": plan["end"],
            "status": "active",
        }
        if plan.get("total_hours"):
            row["total_hours"] = float(plan["total_hours"])
        else:
            row["hours_per_week"] = float(plan["hours_per_week"])
        hour_lines = [ln for ln in rec.get("lines") or [] if ln.get("unit") == "h"]
        h_net = sum((q2(dec(ln["quantity"]) * dec(ln["unit_price"])) for ln in hour_lines), ZERO)
        h_qty = sum((dec(ln["quantity"]) for ln in hour_lines), ZERO)
        row["rate"] = _rate_of(h_net, h_qty)
        warnings = self._accept_warnings(supplier, row, rec)
        blocking = list(
            dict.fromkeys(
                w["code"] for w in warnings if w["confirmable"] and w["code"] not in body.get("confirm_warnings", [])
            )
        )
        if blocking:
            raise ServiceError(
                409,
                "Confirm these warnings first: " + ", ".join(blocking),
                body={"codes": blocking},
            )
        with self.store.transaction():
            alloc = self.store.save("allocations", row)
            saved = self.store.save(
                "incoming_offers",
                self._stamp(
                    rec,
                    "accepted",
                    me,
                    today,
                    allocation_id=alloc["id"],
                    decision_note=decision,
                    project_id=project_id,
                    contract_id=contract_id,
                ),
            )
        from .allocations import AllocationService  # local: the allocation views import the planner

        svc = AllocationService(self.store)
        allocation = svc.present_one(alloc)
        nodemand = self._first_week_without_demand(alloc, today)
        return {
            "offer": self._present(saved, today),
            "allocation": allocation,
            "commitment": None,
            "planning_warnings": warnings + nodemand,
        }

    def _first_week_without_demand(self, alloc: dict, today: date) -> builtins.list[dict]:
        """``allocation.no_demand`` for the first week of a new allocation whose project pool has capacity and no
        demand (the planner decides; docs/contracts/subcontractors.md, W1 and W14)."""
        from . import planning as eng  # local import: the planner is the heavy part of the engine

        week = eng.week_start(calc.d(alloc["start"]))
        world = eng.load(self.store, today)
        rows, _ = eng.weeks_detail(world, week, 1, None, alloc["project_id"])
        for p in rows[0]["projects"] if rows else []:
            if (
                p["project_id"] == alloc["project_id"]
                and p["external"]["capacity"] > 1e-9
                and p["external"]["assigned"] <= 1e-9
            ):
                return [
                    pw.warning(
                        "allocation.no_demand",
                        "info",
                        "weeks",
                        f"Week of {week.isoformat()}: {_h(p['external']['capacity'])} h are allocated to project "
                        f"{p['name']} but nothing is planned there.",
                        dict(
                            allocation_id=alloc["id"],
                            project_id=alloc["project_id"],
                            project_name=p["name"],
                            week=week.isoformat(),
                            capacity=p["external"]["capacity"],
                        ),
                        confirmable=False,
                    )
                ]
        return []

    def _accept_warnings(self, supplier: dict, row: dict, rec: dict) -> builtins.list[dict]:
        cap = self._cap()
        candidate = {**row, "id": "candidate", "status": "active"}
        out: builtins.list[dict] = []
        days = ext.allocation_days(supplier, candidate, cap)
        sid = supplier["id"]
        if not days:
            out.append(
                pw.warning(
                    "allocation.no_available_day",
                    "warning",
                    "plan",
                    "No day of the plan is available for this supplier.",
                    {"supplier_id": sid},
                    confirmable=False,
                )
            )
        else:
            out += self._outside(supplier, candidate, cap)
        out += self._weekly(supplier, candidate, cap)
        planned_net = ext.figures(supplier, candidate, cap).planned_net
        net_total = dec(rec.get("net_total"))
        if abs(planned_net - net_total) > Decimal("0.01"):
            out.append(
                pw.warning(
                    "allocation.cost_differs_from_offer",
                    "info",
                    "net_total",
                    f"The planned cost ({float(planned_net):.2f}) differs from the offered net total "
                    f"({float(net_total):.2f}).",
                    {
                        "incoming_offer_id": rec["id"],
                        "planned_net": float(planned_net),
                        "net_total": float(net_total),
                        "difference": float(q2(abs(planned_net - net_total))),
                    },
                    confirmable=False,
                )
            )
        hint = self.dependency_hint(supplier, extra=candidate, cap=cap)
        if hint:
            out.append(hint)
        return out

    def _outside(self, supplier: dict, candidate: dict, cap: calc.Capacity) -> builtins.list[dict]:
        """Unavailable workdays inside the plan (not holidays): the hours that would have been booked there are dropped."""
        start, end = calc.d(candidate["start"]), calc.d(candidate["end"])
        wd = set(ext.workdays(supplier))
        windows = ext.unavailable_windows(supplier)
        if not windows:
            return []
        days: builtins.list[str] = []
        x = start
        while x <= end:
            if x.weekday() in wd and not cap.holiday(x) and any(a <= x <= b for a, b in windows):
                days.append(x.isoformat())
            x += timedelta(days=1)
        if not days:
            return []
        available = len(ext.available_days(supplier, start, end, cap))
        if candidate.get("hours_per_week"):
            per_day = calc.fnum(candidate["hours_per_week"]) / len(wd)
        else:
            per_day = calc.fnum(candidate.get("total_hours")) / available if available else 0.0
        dropped = round(per_day * len(days), 2)
        return [
            pw.warning(
                "allocation.outside_availability",
                "warning",
                "plan",
                f"{len(days)} working days of the plan are not available for this supplier; {_h(dropped)} h are dropped.",
                {"supplier_id": supplier["id"], "dropped_hours": dropped, "days": days[:20]},
                confirmable=False,
            )
        ]

    def _weekly(self, supplier: dict, candidate: dict, cap: calc.Capacity) -> builtins.list[dict]:
        limit = calc.fnum(supplier.get("weekly_hours"))
        rows = [
            r
            for r in self.store.find("allocations", supplier_id=supplier["id"])
            if r.get("status", "active") in ("active", "cancelled")
        ]
        totals: dict[date, float] = defaultdict(float)
        for r in [*rows, candidate]:
            for w, h in ext.weekly_hours(ext.allocation_days(supplier, r, cap)).items():
                totals[w] += h
        own = ext.weekly_hours(ext.allocation_days(supplier, candidate, cap))
        out = []
        for w in sorted(own):
            allocated = round(totals[w], 6)
            over = allocated - limit
            if over > 0.005:
                out.append(
                    pw.warning(
                        "allocation.exceeds_weekly_hours",
                        "warning",
                        "plan",
                        f"{supplier.get('name') or ''} would be allocated {_h(allocated)} h in the week of "
                        f"{w.isoformat()}, {_h(round(over, 2))} h above the {_h(limit)} h per week they can offer.",
                        {
                            "supplier_id": supplier["id"],
                            "week": w.isoformat(),
                            "allocated": round(allocated, 2),
                            "weekly_hours": limit,
                            "over_by": round(over, 2),
                        },
                        confirmable=True,
                    )
                )
        return out[:MAX_WARNED_WEEKS]

    # ------------------------------------------------------------------ dependency hint
    def dependency(self, supplier: dict, extra: dict | None = None, cap: calc.Capacity | None = None) -> dict:
        """Longest run of Monday-Sunday weeks whose allocated hours reach the threshold (docs: "dependency")."""
        cap = cap or self._cap()
        threshold_h = self._setting("subcontractor_hint_hours_per_week", 30.0)
        threshold_w = int(self._setting("subcontractor_hint_weeks", 12))
        rows = [
            r
            for r in self.store.find("allocations", supplier_id=supplier["id"])
            if r.get("status", "active") in ("active", "cancelled")
        ]
        if extra is not None:
            rows.append(extra)
        weeks: dict[date, float] = defaultdict(float)
        for r in rows:
            for w, h in ext.weekly_hours(ext.allocation_days(supplier, r, cap)).items():
                weeks[w] += h
        counting = sorted(w for w, h in weeks.items() if round(h, 6) >= threshold_h)
        best: builtins.list[date] = []
        run: builtins.list[date] = []
        for w in counting:
            run = run + [w] if run and w - run[-1] == timedelta(days=7) else [w]
            if len(run) > len(best):
                best = run
        average = round(sum(weeks[w] for w in best) / len(best), 2) if best else 0.0
        return {
            "supplier_id": supplier["id"],
            "supplier_name": supplier.get("name"),
            "threshold_hours_per_week": float(threshold_h),
            "threshold_weeks": threshold_w,
            "raised": len(best) >= threshold_w and bool(best),
            "longest_run_weeks": len(best),
            "average_hours_per_week": average,
            "from": best[0].isoformat() if best else None,
            "to": (best[-1] + timedelta(days=6)).isoformat() if best else None,
        }

    def dependency_hint(
        self, supplier: dict, extra: dict | None = None, cap: calc.Capacity | None = None
    ) -> dict | None:
        res = self.dependency(supplier, extra, cap)
        if not res["raised"]:
            return None
        return pw.warning(
            "supplier.dependency_hint",
            "info",
            None,
            f"{supplier.get('name') or ''} has been booked for {res['longest_run_weeks']} weeks in a row.",
            {
                "supplier_id": supplier["id"],
                "supplier_name": supplier.get("name"),
                "hours_per_week": res["average_hours_per_week"],
                "weeks": res["longest_run_weeks"],
                "threshold_hours_per_week": res["threshold_hours_per_week"],
                "threshold_weeks": res["threshold_weeks"],
                "from": res["from"],
                "to": res["to"],
            },
            confirmable=False,
        )

    def dependency_route(self, rid: str) -> dict:
        supplier = self._supplier(rid)
        if not supplier:
            raise ServiceError(404, "Not found")
        if supplier.get("kind") != CAPACITY_KIND:
            raise ServiceError(409, "Not a subcontractor")
        res = self.dependency(supplier)
        return {
            k: res[k]
            for k in (
                "supplier_id",
                "threshold_hours_per_week",
                "threshold_weeks",
                "raised",
                "longest_run_weeks",
                "average_hours_per_week",
                "from",
                "to",
            )
        }

    def allocation(self, aid: str, me: dict) -> dict:
        """External rows with their days; employee rows only for callers who may plan (then without days)."""
        from .allocations import AllocationService  # local import, see above

        row = self.store.get("allocations", aid) if ID_RE.match(aid) else None
        if not row:
            raise ServiceError(404, "Not found")
        external = bool(row.get("supplier_id")) or not row.get("user_id")
        if not external and not self.sees_employee_rows(me):
            raise ServiceError(404, "Not found")
        return AllocationService(self.store).present_one(row, days=external)

    # ------------------------------------------------------------------ unaccept, cancel
    def unaccept(self, rid: str, me: dict, today: date | None = None) -> dict:
        today = today or date.today()
        rec = self._offer(rid)
        if rec.get("status") != "accepted":
            raise ServiceError(409, f"This action is not possible in status {rec.get('status')}")
        aid = rec.get("allocation_id")
        with self.store.transaction():
            if aid:
                alloc = self.store.get("allocations", aid)
                linked = self.store.find("expenses", allocation_id=aid)
                start = calc.d(alloc.get("start")) if alloc else None
                started = start is not None and start <= today
                if started or linked:
                    raise ServiceError(409, "The allocation has started or has linked expenses; cancel it instead")
                if alloc:
                    self.store.delete("allocations", aid)
            saved = self.store.save("incoming_offers", self._stamp(rec, "under_review", me, today, allocation_id=None))
        return self._present(saved, today)

    def cancel_allocation(self, aid: str, body: dict, today: date | None = None) -> dict:
        from .allocations import AllocationService  # local: the allocation views import the planner

        today = today or date.today()
        row = self.store.get("allocations", aid) if ID_RE.match(aid) else None
        if not row:
            raise ServiceError(404, "Not found")
        start, end = calc.d(row.get("start")), calc.d(row.get("end"))
        effective = calc.d(body["effective_from"])
        if effective < start:  # type: ignore[operator]
            raise ServiceError(422, "effective_from must not be before the start")
        if effective > end:  # type: ignore[operator]
            raise ServiceError(409, "effective_from is after the end of the allocation")
        if row.get("status", "active") != "active":
            raise ServiceError(409, "The allocation is already cancelled")
        if not row.get("supplier_id") or row.get("user_id"):
            raise ServiceError(409, "This allocation cannot be changed here")
        with self.store.transaction():
            self.store.save(
                "allocations",
                {
                    **row,
                    "status": "cancelled",
                    "cancelled_from": body["effective_from"],
                    "cancel_reason": body.get("reason"),
                },
            )
        saved = self.store.get("allocations", aid) or row
        return AllocationService(self.store).present_one(saved, days=True)

    # ------------------------------------------------------------------ commitments
    def commitments(self, supplier_id: str | None, project_id: str | None, today: date | None = None) -> dict:
        from .allocations import AllocationService  # local import, see above

        today = today or date.today()
        svc = AllocationService(self.store)
        suppliers = {s["id"]: s for s in self.store.list("suppliers")}
        projects = {p["id"]: p for p in self.store.list("projects")}
        offers = {o["id"]: o for o in self.store.list("incoming_offers")}
        items: builtins.list[dict] = []
        for row in self.store.list("allocations"):
            if not row.get("supplier_id") or row.get("user_id"):
                continue
            if row.get("status", "active") not in ("active", "cancelled"):
                continue
            view = svc.present_one(row)
            if row.get("status") == "cancelled" and not (view.get("open_net") or 0) > 0:
                continue
            offer = offers.get(row.get("incoming_offer_id") or "") or {}
            items.append(
                dict(
                    kind="allocation",
                    allocation_id=row["id"],
                    incoming_offer_id=row.get("incoming_offer_id"),
                    offer_reference=offer.get("reference"),
                    supplier_id=row["supplier_id"],
                    supplier_name=(suppliers.get(row["supplier_id"]) or {}).get("name"),
                    project_id=row.get("project_id"),
                    project_name=(projects.get(row.get("project_id") or "") or {}).get("name"),
                    start=row.get("start"),
                    end=row.get("end"),
                    status=row.get("status", "active"),
                    planned_hours=view.get("planned_hours"),
                    planned_net=view.get("planned_net"),
                    invoiced_net=view.get("invoiced_net"),
                    open_net=view.get("open_net"),
                )
            )
        for o in offers.values():
            if o.get("status") != "accepted" or o.get("allocation_id"):
                continue
            supplier = suppliers.get(o.get("supplier_id") or "") or {}
            pid = o.get("project_id")
            net = q2(dec(o.get("net_total")))
            items.append(
                dict(
                    kind="flat",
                    allocation_id=None,
                    incoming_offer_id=o["id"],
                    offer_reference=o.get("reference"),
                    supplier_id=o.get("supplier_id"),
                    supplier_name=supplier.get("name"),
                    project_id=pid,
                    project_name=(projects.get(pid or "") or {}).get("name"),
                    start=None,
                    end=None,
                    status="accepted",
                    planned_hours=None,
                    planned_net=float(net),
                    invoiced_net=None,
                    open_net=float(net),
                )
            )
        if supplier_id:
            items = [i for i in items if i["supplier_id"] == supplier_id]
        if project_id:
            items = [i for i in items if i["project_id"] == project_id]
        items.sort(key=lambda i: str(i.get("allocation_id") or i.get("incoming_offer_id") or ""))
        items.sort(key=lambda i: str(i["start"] or ""), reverse=True)  # newest start first, flat items (no start) last
        return dict(
            as_of=today.isoformat(),
            items=items,
            by_supplier=_group(items, "supplier_id", "supplier_name", "name"),
            by_project=_group(items, "project_id", "project_name", "name", none_name="No project"),
            totals=_totals(items),
            hints=self.hints(today),
        )

    def expense_warnings(self, rec: dict) -> builtins.list[dict]:
        """``allocation.invoiced_exceeds_planned``: the expenses linked to an external allocation (this one included)
        are more than its planned net (docs/contracts/subcontractors.md, W10)."""
        from .allocations import AllocationService  # local import, see above

        aid = rec.get("allocation_id")
        row = self.store.get("allocations", aid) if aid and ID_RE.match(aid) else None
        if not row or not row.get("supplier_id") or row.get("user_id"):
            return []
        planned = dec(AllocationService(self.store).present_one(row).get("planned_net"))
        others = sum(
            (dec(e.get("net")) for e in self.store.find("expenses", allocation_id=aid) if e.get("id") != rec.get("id")),
            ZERO,
        )
        invoiced = q2(others + dec(rec.get("net")))
        excess = q2(invoiced - planned)
        if excess <= Decimal("0.01"):
            return []
        return [
            pw.warning(
                "allocation.invoiced_exceeds_planned",
                "warning",
                "allocation_id",
                f"The linked expenses ({invoiced:.2f}) are {excess:.2f} above the planned cost of the allocation "
                f"({q2(planned):.2f}).",
                {
                    "allocation_id": aid,
                    "planned_net": float(q2(planned)),
                    "invoiced_net": float(invoiced),
                    "excess": float(excess),
                },
                confirmable=False,
            )
        ]

    def committed_by_project(self) -> dict[str, float]:
        """Planned net of the external allocations per project (cancelled ones shortened), for the project report."""
        from .allocations import AllocationService

        svc = AllocationService(self.store)
        out: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for row in self.store.list("allocations"):
            if not row.get("supplier_id") or row.get("user_id") or not row.get("project_id"):
                continue
            if row.get("status", "active") not in ("active", "cancelled"):
                continue
            out[row["project_id"]] += dec(svc.present_one(row).get("planned_net"))
        return {k: float(q2(v)) for k, v in out.items()}

    def hints(self, today: date) -> builtins.list[dict]:
        """``business.customer_dominance`` when one customer's share of the trailing-12-month revenue reaches the
        configured percentage (exact decimals)."""
        out: builtins.list[dict] = []
        pct = Decimal(str(self._setting("customer_dominance_percent", 83.0)))
        settings = self.store.settings()
        accrual = settings.get("accounting", "eur") == "accrual"
        start = _minus_12_months(today) + timedelta(days=1)
        projects = {p["id"]: p for p in self.store.list("projects")}
        totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
        total = ZERO
        for inv in self.store.list("invoices"):
            if inv.get("status") in ("draft", "cancelled"):
                continue
            if not accrual and inv.get("status") != "paid":
                continue
            when = calc.d(inv.get("date")) if accrual else calc.d(inv.get("paid_date"))
            if not when or not (start <= when <= today):
                continue
            net = calc.doc_totals(dict(inv), settings)["net"]
            customer = inv.get("customer_id") or (projects.get(inv.get("project_id") or "") or {}).get("customer_id")
            total += dec(net)
            if customer:
                totals[customer] += dec(net)
        if total > 0 and totals:
            top, amount = max(totals.items(), key=lambda kv: kv[1])
            if 100 * amount >= pct * total:
                name = (self.store.get("customers", top) or {}).get("name")
                out.append(
                    dict(
                        code="business.customer_dominance",
                        severity="info",
                        field=None,
                        confirmable=False,
                        message=f"{name or 'One customer'} accounts for {float(q2(100 * amount / total))} % of the "
                        f"revenue of the last twelve months. {LEGAL_HINT}",
                        details=dict(
                            customer_id=top,
                            customer_name=name,
                            share_percent=float(q2(100 * amount / total)),
                            revenue_customer=float(q2(amount)),
                            revenue_total=float(q2(total)),
                            **{"from": start.isoformat()},
                            to=today.isoformat(),
                        ),
                    )
                )
        return out

    # ------------------------------------------------------------------ scope for the allocation view
    @staticmethod
    def sees_employee_rows(me: dict) -> bool:
        return rights.scope_for(me, "planning") != Scope.NONE


def _group(
    items: builtins.list[dict], key: str, name_key: str, out_name: str, none_name: str | None = None
) -> builtins.list[dict]:
    groups: dict[Any, dict] = {}
    for i in items:
        gid = i.get(key)
        g = groups.setdefault(
            gid,
            {
                key: gid,
                out_name: (i.get(name_key) if gid is not None else none_name),
                "planned": ZERO,
                "invoiced": ZERO,
                "invoiced_seen": False,
                "open": ZERO,
            },
        )
        g["planned"] += dec(i.get("planned_net"))
        if i.get("invoiced_net") is not None:
            g["invoiced"] += dec(i["invoiced_net"])
            g["invoiced_seen"] = True
        g["open"] += dec(i.get("open_net"))
    rows = []
    for g in groups.values():
        rows.append(
            {
                key: g[key],
                out_name: g[out_name],
                "planned_net": float(q2(g["planned"])),
                "invoiced_net": float(q2(g["invoiced"])) if g["invoiced_seen"] else None,
                "open_net": float(q2(g["open"])),
            }
        )
    rows.sort(key=lambda r: (r[key] is None, str(r[out_name] or "").casefold()))
    return rows


def _totals(items: builtins.list[dict]) -> dict:
    planned = sum((dec(i.get("planned_net")) for i in items), ZERO)
    invoiced = sum((dec(i["invoiced_net"]) for i in items if i.get("invoiced_net") is not None), ZERO)
    opened = sum((dec(i.get("open_net")) for i in items), ZERO)
    return dict(planned_net=float(q2(planned)), invoiced_net=float(q2(invoiced)), open_net=float(q2(opened)))
