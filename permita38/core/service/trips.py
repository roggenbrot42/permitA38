"""Business trips (docs/specs/business-trips.md, API contract docs/contracts/trips.md): saving, figures, the checklist,
finalising, reopening and the trip statement.

Rights: the area ``trips`` (``core/rights.py``). A LIMITED scope sees and changes only its own trips; anything else is
404 (a trip of another person is not revealed). Figures are computed on every save and stored with the trip, so the
statement, the finance report and the preview use the same numbers. ``bills`` and ``checklist`` are computed on every
read. A final trip is locked until the boss reopens it.
"""

from decimal import Decimal
from typing import Any

from permita38.core import rights, trip_statement, trips_calc
from permita38.core.errors import ServiceError
from permita38.core.money import ZERO, dec, q2, to_float
from permita38.core.repository import ID_RE, Repository
from permita38.core.rights import Scope
from permita38.core.trip_rates import RateTableMissing, load_checklist, load_rates

#: figures computed by the server: never taken from the request
_COMPUTED = ("allowance_total", "mileage_km", "mileage_total", "rules_version", "days")
#: response-only fields: ignored when a client sends them
_RESPONSE_ONLY = ("bills", "checklist", "mileage_rate")
#: checklist lines that a user may mark "Not needed"; the allowance and the cost centre cannot be skipped
_SKIPPABLE = ("accommodation", "transportation", "food", "material")


def _valid_id(rid: str) -> bool:
    return isinstance(rid, str) and bool(ID_RE.match(rid))


def _cat(bill: dict) -> str:
    return str(bill.get("category") or "").strip().lower()


def _net_sum(bills: list[dict]) -> Decimal:
    return sum((dec(b.get("net")) for b in bills), ZERO)


def bills_summary(bills: list[dict]) -> dict[str, Any]:
    return {
        "count": len(bills),
        "net": to_float(q2(_net_sum(bills))),
        "vat": to_float(q2(sum((dec(b.get("vat")) for b in bills), ZERO))),
    }


def checklist(trip: dict, bills: list[dict]) -> list[dict[str, Any]]:
    """The reminder checklist of a trip (contract, "Checklist"). ``state`` is added, open or not_needed."""
    skipped = set(trip.get("not_needed") or [])
    mileage = trip.get("mileage") or []
    rows: list[dict[str, Any]] = []
    for item in load_checklist():
        iid, label = item["id"], item["label"]
        row = {**item, "state": "open", "count": 0, "sum": 0}
        if iid == "allowance":
            paid = [x for x in trip.get("days") or [] if isinstance(x, dict) and dec(x.get("amount")) > 0]
            if dec(trip.get("allowance_total")) > 0:
                row.update(state="added", count=len(paid), sum=to_float(q2(dec(trip.get("allowance_total")))))
            else:
                row.update(state="not_needed")
        elif iid == "cost_centre":
            centre = trip.get("cost_centre")
            ok = centre == "general" or (centre == "project" and (trip.get("project_id") or trip.get("customer_id")))
            if ok:
                row.update(state="added", count=1)
        elif iid in _SKIPPABLE and iid in skipped:
            row.update(state="not_needed")
        elif iid == "food":
            row.update(state="added")  # informational: the allowance covers meals, never blocks
        else:
            wanted = {"accommodation": "Accommodation", "transportation": "Transportation", "material": "Material"}
            matched = [b for b in bills if _cat(b) == _cat({"category": wanted.get(iid, label)})]
            if iid == "transportation":
                if matched or mileage:
                    row.update(
                        state="added",
                        count=len(matched) + len(mileage),
                        sum=to_float(q2(_net_sum(matched) + dec(trip.get("mileage_total")))),
                    )
            elif matched:
                row.update(state="added", count=len(matched), sum=to_float(q2(_net_sum(matched))))
        rows.append(row)
    return rows


class TripService:
    def __init__(self, store: Repository):
        self.store = store

    # -- visibility -------------------------------------------------------------------------------------------
    @staticmethod
    def _scope(me: dict, op: rights.Op) -> Scope:
        return rights.scope(me.get("role"), "trips", op)

    def _visible(self, me: dict, trip: dict, op: rights.Op = "read") -> bool:
        sc = self._scope(me, op)
        if sc == Scope.ALL:
            return True
        return sc == Scope.LIMITED and trip.get("user_id") == me["id"]

    def _get(self, me: dict, rid: str, op: rights.Op = "read") -> dict:
        trip = self.store.get("trips", rid) if _valid_id(rid) else None
        if not trip or not self._visible(me, trip, op):
            raise ServiceError(404, "Not found")
        return trip

    def _linked(self, rid: str) -> list[dict]:
        return self.store.find("expenses", trip_id=rid)

    def _view(self, trip: dict, bills: list[dict] | None = None) -> dict:
        """The response of a stored trip: ``bills`` and ``checklist`` computed now."""
        bills = self._linked(trip["id"]) if bills is None else bills
        return {**trip, "bills": bills_summary(bills), "checklist": checklist(trip, bills)}

    # -- reads ----------------------------------------------------------------------------------------------------
    def list_trips(self, me: dict) -> list[dict]:
        if self._scope(me, "read") == Scope.NONE:
            raise ServiceError(403, "Forbidden")
        index: dict[str, list[dict]] = {}
        for bill in self.store.list("expenses"):
            if bill.get("trip_id"):
                index.setdefault(bill["trip_id"], []).append(bill)
        rows = [t for t in self.store.list("trips") if self._visible(me, t)]
        rows.sort(key=lambda t: (str(t.get("start") or ""), str(t.get("id") or "")), reverse=True)
        return [self._view(t, index.get(t["id"], [])) for t in rows]

    def get(self, me: dict, rid: str) -> dict:
        if self._scope(me, "read") == Scope.NONE:
            raise ServiceError(403, "Forbidden")
        return self._view(self._get(me, rid))

    # -- figures ------------------------------------------------------------------------------------------------------
    @staticmethod
    def _figures(body: dict) -> dict[str, Any]:
        try:
            return trips_calc.compute(body, load_rates)
        except (RateTableMissing, ValueError) as exc:
            raise ServiceError(422, str(exc)) from None

    def _check_refs(self, body: dict) -> None:
        if body.get("customer_id") and not self.store.get("customers", body["customer_id"]):
            raise ServiceError(422, "Unknown customer")
        if body.get("project_id") and not self.store.get("projects", body["project_id"]):
            raise ServiceError(422, "Unknown project")
        known = {item["id"] for item in load_checklist()}
        for item in body.get("not_needed") or []:
            if item not in known:
                raise ServiceError(422, f"Unknown checklist item: {item}")

    def preview(self, me: dict, body: dict) -> dict:
        """Figures for a trip body; nothing is saved (the same computation a save uses)."""
        if self._scope(me, "write") == Scope.NONE:  # the contract: preview needs write access
            raise ServiceError(403, "Forbidden")
        body = {
            k: v
            for k, v in body.items()
            if k not in ("id", "created", "updated", "status", *_COMPUTED, *_RESPONSE_ONLY)
        }
        self._check_refs(body)
        figures = self._figures(body)
        return figures

    def _prepare(self, body: dict, owner: str, old: dict | None) -> dict:
        data = {k: v for k, v in body.items() if k not in ("id", "created", "updated", *_COMPUTED, *_RESPONSE_ONLY)}
        data["user_id"] = owner
        self._check_refs(data)
        figures = self._figures(data)
        data.update(
            allowance_total=figures["allowance_total"],
            mileage_km=figures["mileage_km"],
            mileage_total=figures["mileage_total"],
            rules_version=figures["rules_version"],
            days=figures["days"],
        )
        data.setdefault("status", "draft")
        if old is not None:
            data["created"] = old.get("created")
        return {k: v for k, v in data.items() if v is not None}

    @staticmethod
    def _guard_final(data: dict, bills: list[dict], force: bool) -> None:
        """Finalising needs every checklist line added or not needed; ``force`` skips only this check."""
        if data.get("status") != "final" or force:
            return
        open_lines = [row["label"] for row in checklist(data, bills) if row["state"] == "open"]
        if open_lines:
            raise ServiceError(409, "Checklist has open items: " + ", ".join(open_lines))

    # -- writes ---------------------------------------------------------------------------------------------------------
    def create(self, me: dict, body: dict, force: bool = False) -> dict:
        if self._scope(me, "write") == Scope.NONE:
            raise ServiceError(403, "Forbidden")
        data = self._prepare(body, me["id"], None)
        self._guard_final(data, [], force)
        return self._view(self.store.save("trips", data), [])

    def update(self, me: dict, rid: str, body: dict, force: bool = False) -> dict:
        old = self._get(me, rid, "write")
        if old.get("status") == "final":
            raise ServiceError(409, "This trip is final; the boss can reopen it")
        data = self._prepare(body, old.get("user_id") or me["id"], old)
        bills = self._linked(rid)
        self._guard_final(data, bills, force)
        return self._view(self.store.save("trips", {**data, "id": rid}), bills)

    def delete(self, me: dict, rid: str) -> None:
        old = self._get(me, rid, "write")
        if old.get("status") == "final":
            raise ServiceError(409, "This trip is final; reopen it before deleting")
        with self.store.transaction():
            for bill in self._linked(rid):  # the bills stay, unlinked from the trip
                bill.pop("trip_id", None)
                self.store.save("expenses", bill)
            self.store.delete("trips", rid)

    def reopen(self, me: dict, rid: str) -> dict:
        """Boss only (the route requires the full write scope): a final trip becomes a draft again."""
        old = self._get(me, rid, "write")
        if old.get("status") != "final":
            raise ServiceError(409, "Only a final trip can be reopened")
        return self._view(self.store.save("trips", {**old, "status": "draft"}))

    # -- statement ------------------------------------------------------------------------------------------------------
    def statement(self, me: dict, rid: str) -> tuple[bytes, str]:
        trip = self._get(me, rid)
        owner = self.store.get("users", trip.get("user_id") or "") or {}
        settings = self.store.settings()
        figures = {
            "days": trip.get("days") or [],
            "allowance_total": trip.get("allowance_total") or 0,
            "mileage_total": trip.get("mileage_total") or 0,
            "mileage_km": trip.get("mileage_km") or 0,
            "rules_version": trip.get("rules_version"),
        }
        data = trip_statement.render(
            company=settings.get("company_name") or "",
            person=owner.get("name") or "",
            trip=trip,
            figures=figures,
            bills=self._linked(rid),
        )
        return data, f"trip-{rid}.pdf"
